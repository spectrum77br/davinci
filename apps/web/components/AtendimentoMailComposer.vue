<script setup lang="ts">
import { FileText, LoaderCircle, MailPlus, Paperclip, Save, Send, Trash2, X } from 'lucide-vue-next'
import { attachmentProblem, base64Bytes, composeRecipients, forwardedText, prefixedSubject, replyRecipients } from '~/lib/mailCompose'
import type { ComposeAction, ComposeContent, ComposeSource } from '~/lib/mailCompose'

type Context = { allowed_from_addresses: string[]; send_ready: boolean; can_compose: boolean; reply_to?: string[]; reply_all_to?: string[]; reply_all_cc?: string[] }
type DraftSummary = { id: string; action: ComposeAction; subject: string; source_message_id: string | null; updated_at: string }
type SavedDraft = ComposeContent & { id: string; mailbox_id: string; created_at: string; updated_at: string }
type Outgoing = { id: string; status: string; action: ComposeAction; to: string[] | string; cc?: string[]; bcc?: string[]; subject: string; text: string; created_at: string; error_code?: string | null; attachments?: { filename: string; size: number }[] }
const props = withDefaults(defineProps<{ mailboxId: string; mailboxLabel: string; ownAddresses?: string[]; disabled?: boolean; restricted?: boolean }>(), { ownAddresses: () => [] })
const emit = defineEmits<{ (event: 'active', value: boolean): void; (event: 'sent', sourceId: string | null): void }>()
const { api } = useApi()
const visible = ref(false)
const view = ref<'editor' | 'drafts' | 'outbox'>('editor')
const loading = ref(false)
const busy = ref(false)
const fileBusy = ref(false)
const error = ref('')
const notice = ref('')
const context = ref<Context | null>(null)
const composeAllowed = ref(false)
const source = ref<ComposeSource | null>(null)
const drafts = ref<DraftSummary[]>([])
const outgoing = ref<Outgoing[]>([])
const draftId = ref<string | null>(null)
const action = ref<ComposeAction>('new')
const from = ref('')
const to = ref('')
const cc = ref('')
const bcc = ref('')
const subject = ref('')
const text = ref('')
const attachments = ref<ComposeContent['attachments']>([])
const sourceAttachmentIds = ref<string[]>([])
const savedFingerprint = ref('')
const sendRequestId = ref('')
const ambiguous = ref(false)
const expandedCopies = ref(false)
const dialog = ref<HTMLElement | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
let disposed = false
let generation = 0
const titles: Record<ComposeAction, string> = { new: 'Nova mensagem', reply: 'Responder', reply_all: 'Responder a todos', forward: 'Encaminhar' }
const statuses: Record<string, string> = { queued: 'Na fila para envio', leased: 'Envio em andamento', sent: 'Aceita pelo serviço de e-mail', failed: 'Não enviada', uncertain: 'Confira nos Enviados do Tuta antes de reenviar' }
const locked = computed(() => busy.value || loading.value || fileBusy.value)
const pending = computed(() => source.value?.outbox?.some((item) => ['queued', 'leased', 'uncertain'].includes(item.status)) ?? false)
const selectedOriginals = computed(() => (source.value?.attachments ?? []).filter((item) => sourceAttachmentIds.value.includes(item.id)))
const attachmentSizes = computed(() => [...attachments.value.map((item) => base64Bytes(item.data_base64)), ...selectedOriginals.value.map((item) => item.size)])
const attachmentCount = computed(() => attachmentSizes.value.length)
const attachmentSize = computed(() => attachmentSizes.value.reduce((sum, size) => sum + size, 0))
const dirty = computed(() => visible.value && view.value === 'editor' && fingerprint() !== savedFingerprint.value)
const canSend = computed(() => context.value?.can_compose && context.value.send_ready && context.value.allowed_from_addresses.includes(from.value) && !locked.value && !pending.value && !props.disabled)
function fingerprint() { return JSON.stringify([action.value, source.value?.id ?? null, from.value, to.value, cc.value, bcc.value, subject.value, text.value, attachments.value, sourceAttachmentIds.value]) }
function sizeLabel(bytes: number) { return bytes >= 1024 * 1024 ? `${(bytes / (1024 * 1024)).toFixed(1)} MB` : `${Math.ceil(bytes / 1024)} KB` }
function date(value: string) { return new Date(value).toLocaleString('pt-BR') }
function addresses(value: string[] | string) { return Array.isArray(value) ? value.join(', ') : value }
function failure(reason: any) {
  const code = reason?.data?.detail?.code
  const messages: Record<string, string> = {
    sending_unavailable: 'O envio está indisponível. Confira a conexão e as permissões desta caixa.',
    sender_not_authorized: 'Escolha um remetente autorizado para esta mensagem.',
    sender_not_receiving_alias: 'Use o endereço desta caixa que recebeu a mensagem.',
    main_address_not_allowed: 'Escolha o endereço da loja para responder ao cliente.',
    no_receiving_alias: 'Esta mensagem não tem um remetente autorizado nesta caixa.',
    test_mode_recipient: 'A caixa está em modo de teste. Todos os destinatários precisam estar autorizados para testes.',
    sending_paused: 'O envio desta caixa está pausado.', sending_disabled: 'O envio desta caixa está desativado.',
    hourly_limit: 'Limite de envios da hora atingido.', daily_limit: 'Limite de envios do dia atingido.', account_hourly_limit: 'Limite de envios da conta atingido.',
    reply_already_pending: 'Esta mensagem já tem um envio pendente. Confira a saída antes de enviar outra vez.',
    request_id_conflict: 'Esta tentativa já foi registrada. Confira a saída antes de preparar outra mensagem.',
    attachment_too_large: 'Confira os anexos: até 10 MB por arquivo e 15 MB no total.',
    attachments_too_large: 'Os anexos juntos podem ter até 15 MB.', too_many_attachments: 'Use no máximo 10 anexos.',
    attachment_not_found: 'Um anexo original não está disponível. Reabra a mensagem e selecione os anexos novamente.',
    invalid_body: 'Revise os endereços, o assunto e os anexos antes de continuar.',
    invalid_attachment: 'Não foi possível ler um dos anexos. Remova-o e adicione novamente.',
    compose_not_allowed: 'Sua permissão nesta caixa não permite este tipo de envio.',
    source_attachment_not_found: 'Um anexo original não está disponível. Reabra a mensagem e selecione os anexos novamente.',
    author_access_revoked: 'Seu acesso à caixa mudou. Nenhuma nova tentativa foi enviada.',
  }
  return messages[code] || (Number(reason?.status || reason?.statusCode) === 403 ? 'Seu usuário não pode executar esta ação nesta caixa.' : 'Não foi possível concluir. Seu conteúdo foi preservado; tente novamente.')
}
function reveal(next: typeof view.value) { visible.value = true; view.value = next; emit('active', true); nextTick(() => dialog.value?.focus()) }
function discardAllowed() {
  if (ambiguous.value) { error.value = 'Confirme esta tentativa com “Tentar confirmar envio” antes de fechar ou abrir outra mensagem. Assim evitamos enviar o mesmo e-mail duas vezes.'; return false }
  return !dirty.value || window.confirm('Há alterações que ainda não foram salvas. Descartar essas alterações?')
}
function close() {
  if (locked.value || !discardAllowed()) return
  ++generation; visible.value = false; emit('active', false)
}
function reset() {
  context.value = null; source.value = null; draftId.value = null; action.value = 'new'; from.value = ''; to.value = ''; cc.value = ''; bcc.value = ''; subject.value = ''; text.value = ''; attachments.value = []; sourceAttachmentIds.value = []; sendRequestId.value = ''; ambiguous.value = false; expandedCopies.value = false; error.value = ''; notice.value = ''; savedFingerprint.value = fingerprint()
}
async function loadContext(kind: ComposeAction, sourceId?: string | null) {
  const query = new URLSearchParams({ action: kind })
  if (sourceId) query.set('source_message_id', sourceId)
  return api<Context>(`/api/mail/mailboxes/${props.mailboxId}/compose-context?${query}`, { retry: 0 })
}
async function open(kind: ComposeAction = 'new', original: ComposeSource | null = null, initialText = '', initialFrom = '') {
  if (props.disabled || locked.value || !discardAllowed()) return
  reset(); action.value = kind; source.value = original ? JSON.parse(JSON.stringify(original)) : null
  subject.value = original ? prefixedSubject(original.subject, kind) : ''
  text.value = initialText || (kind === 'forward' && original ? forwardedText(original) : '')
  if (kind === 'forward') sourceAttachmentIds.value = original?.attachments.map((item) => item.id) ?? []
  const recipients = original ? replyRecipients(original, kind, props.ownAddresses) : { to: [], cc: [] }
  to.value = recipients.to.join(', '); cc.value = recipients.cc.join(', '); expandedCopies.value = !!cc.value
  loading.value = true; reveal('editor'); const current = ++generation
  try {
    const result = await loadContext(kind, original?.id)
    if (disposed || current !== generation) return
    context.value = result
    from.value = [initialFrom, original?.reply.from_address ?? '', ...result.allowed_from_addresses].find((item) => result.allowed_from_addresses.includes(item)) || ''
    if (kind === 'reply_all' && result.reply_all_to && result.reply_all_cc) { to.value = result.reply_all_to.join(', '); cc.value = result.reply_all_cc.join(', '); expandedCopies.value = !!cc.value }
    if (kind === 'reply' && result.reply_to) to.value = result.reply_to.join(', ')
    savedFingerprint.value = fingerprint()
    if (initialText.trim()) savedFingerprint.value = ''
  } catch (reason) { if (current === generation) error.value = failure(reason) }
  finally { if (current === generation) loading.value = false }
}
async function showList(kind: 'drafts' | 'outbox') {
  if (props.disabled || locked.value || !discardAllowed()) return
  reset(); reveal(kind); loading.value = true; const current = ++generation
  try {
    const result = await api<{ items: any[] }>(`/api/mail/mailboxes/${props.mailboxId}/${kind}`, { retry: 0 })
    if (disposed || current !== generation) return
    if (kind === 'drafts') drafts.value = result.items
    else outgoing.value = result.items
  } catch (reason) { if (current === generation) error.value = failure(reason) }
  finally { if (current === generation) loading.value = false }
}
async function restore(id: string) {
  if (locked.value || !discardAllowed()) return
  loading.value = true; error.value = ''; const current = ++generation
  try {
    const item = await api<SavedDraft>(`/api/mail/drafts/${id}`, { retry: 0 })
    const original = item.source_message_id ? await api<ComposeSource>(`/api/mail/messages/${item.source_message_id}`, { retry: 0 }) : null
    const authorization = await loadContext(item.action, item.source_message_id)
    if (disposed || current !== generation) return
    source.value = original; draftId.value = item.id; action.value = item.action; from.value = item.from_address || ''; to.value = item.to.join(', '); cc.value = item.cc.join(', '); bcc.value = item.bcc.join(', '); subject.value = item.subject; text.value = item.text; attachments.value = item.attachments; sourceAttachmentIds.value = item.source_attachment_ids; context.value = authorization; sendRequestId.value = ''; ambiguous.value = false; expandedCopies.value = !!cc.value || !!bcc.value; reveal('editor'); savedFingerprint.value = fingerprint()
  } catch (reason) { if (current === generation) error.value = failure(reason) }
  finally { if (current === generation) loading.value = false }
}
function payload(): ComposeContent {
  const recipients = composeRecipients(to.value, cc.value, bcc.value)
  const problem = attachmentProblem(attachmentSizes.value)
  if (problem) throw new Error(problem)
  return { action: action.value, source_message_id: source.value?.id || null, from_address: from.value || null, ...recipients, subject: subject.value, text: text.value, attachments: attachments.value.map((item) => ({ ...item })), source_attachment_ids: [...sourceAttachmentIds.value] }
}
async function refreshAuthorization() {
  if (locked.value) return
  loading.value = true; error.value = ''
  try { context.value = await loadContext(action.value, source.value?.id) }
  catch (reason) { error.value = failure(reason) }
  finally { loading.value = false }
}
async function resolveOutgoing(item: Outgoing, sent: boolean) {
  if (locked.value || item.status !== 'uncertain') return
  if (!window.confirm(sent ? 'Você conferiu que esta mensagem está nos Enviados do Tuta?' : 'Você conferiu nos Enviados do Tuta e esta mensagem NÃO saiu? Esta ação apenas registra a conferência; não reenvia.')) return
  busy.value = true; error.value = ''
  try {
    await api(`/api/mail/outbox/${item.id}/resolve`, { method: 'POST', body: { saiu: sent }, retry: 0 })
    const result = await api<{ items: Outgoing[] }>(`/api/mail/mailboxes/${props.mailboxId}/outbox`, { retry: 0 })
    outgoing.value = result.items
  } catch (reason) { error.value = failure(reason) }
  finally { busy.value = false }
}
async function saveDraft() {
  if (locked.value || ambiguous.value || !context.value?.can_compose) return
  let content: ComposeContent
  try { content = payload() } catch (reason) { error.value = (reason as Error).message; return }
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const saved = await api<SavedDraft>(draftId.value ? `/api/mail/drafts/${draftId.value}` : `/api/mail/mailboxes/${props.mailboxId}/drafts`, { method: draftId.value ? 'PATCH' : 'POST', body: content, retry: 0 })
    draftId.value = saved.id; savedFingerprint.value = fingerprint(); notice.value = 'Rascunho salvo no DaVinci, com os anexos.'
  } catch (reason) { error.value = failure(reason) }
  finally { busy.value = false }
}
async function removeDraft(id: string) {
  if (locked.value || !window.confirm('Excluir este rascunho do DaVinci?')) return
  busy.value = true; error.value = ''
  try { await api(`/api/mail/drafts/${id}`, { method: 'DELETE', retry: 0 }); drafts.value = drafts.value.filter((item) => item.id !== id) }
  catch (reason) { error.value = failure(reason) }
  finally { busy.value = false }
}
async function addFiles(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files || [])
  input.value = ''
  if (locked.value || ambiguous.value || !files.length) return
  error.value = ''; notice.value = ''
  const problem = attachmentProblem([...attachmentSizes.value, ...files.map((file) => file.size)])
  if (problem) { error.value = problem; return }
  fileBusy.value = true
  try {
    const added = await Promise.all(files.map(async (file) => {
      if (/[\x00-\x1f\x7f/\\]/.test(file.name) || file.name.length > 255) throw new Error('Um arquivo tem um nome inválido ou longo demais. Renomeie e tente novamente.')
      const data = new Uint8Array(await file.arrayBuffer())
      let binary = ''
      for (let offset = 0; offset < data.length; offset += 8192) binary += String.fromCharCode(...data.subarray(offset, offset + 8192))
      return { filename: file.name, content_type: file.type || 'application/octet-stream', data_base64: btoa(binary) }
    }))
    attachments.value.push(...added)
  } catch (reason) { error.value = (reason as Error).message || 'Não foi possível ler os anexos.' }
  finally { fileBusy.value = false }
}
async function send() {
  if (!canSend.value) return
  let content: ComposeContent
  try {
    content = payload()
    if (!content.to.length && !content.cc.length && !content.bcc.length) throw new Error('Informe pelo menos um destinatário.')
    if (!content.text.trim() && !attachmentCount.value) throw new Error('Escreva a mensagem ou adicione um anexo.')
    if (!content.subject.trim()) throw new Error('Informe o assunto da mensagem.')
  } catch (reason) { error.value = (reason as Error).message; return }
  const destinations = [`De: ${from.value}`, content.to.length ? `Para: ${content.to.join(', ')}` : '', content.cc.length ? `CC: ${content.cc.join(', ')}` : '', content.bcc.length ? `CCO: ${content.bcc.join(', ')}` : '', `Assunto: ${content.subject}`, `Anexos: ${attachmentCount.value} (${sizeLabel(attachmentSize.value)})`].filter(Boolean)
  if (!window.confirm(`Enviar esta mensagem?\n\n${destinations.join('\n')}`)) return
  sendRequestId.value ||= crypto.randomUUID(); busy.value = true; error.value = ''; notice.value = ''
  try {
    const result = await api<{ id: string; status: string }>(`/api/mail/mailboxes/${props.mailboxId}/send`, { method: 'POST', body: { request_id: sendRequestId.value, ...content }, retry: 0 })
    // Acceptance is the only point at which the draft can be cleared.
    const sentSource = content.source_message_id
    const oldDraft = draftId.value
    savedFingerprint.value = fingerprint(); ambiguous.value = false
    if (oldDraft) {
      try { await api(`/api/mail/drafts/${oldDraft}`, { method: 'DELETE', retry: 0 }) }
      catch { notice.value = 'O envio foi registrado, mas não foi possível remover o rascunho. Exclua-o em Rascunhos para não repetir o envio.' }
    }
    emit('sent', sentSource)
    draftId.value = null; text.value = ''; attachments.value = []; sourceAttachmentIds.value = []; sendRequestId.value = ''; savedFingerprint.value = fingerprint()
    const jobs = await api<{ items: Outgoing[] }>(`/api/mail/mailboxes/${props.mailboxId}/outbox`, { retry: 0 }).catch(() => ({ items: [{ id: result.id, status: result.status, action: content.action, subject: content.subject, text: content.text, to: content.to, cc: content.cc, bcc: content.bcc, created_at: new Date().toISOString() }] }))
    outgoing.value = jobs.items; view.value = 'outbox'; notice.value ||= 'Mensagem registrada. Acompanhe a confirmação do envio abaixo.'
  } catch (reason: any) {
    const status = Number(reason?.status || reason?.statusCode || reason?.response?.status)
    ambiguous.value = !status || status >= 500
    error.value = ambiguous.value ? 'A confirmação do envio não chegou. O conteúdo foi mantido. Use “Tentar confirmar envio” para consultar a mesma tentativa, sem criar outra.' : failure(reason)
  } finally { busy.value = false }
}
function beforeUnload(event: BeforeUnloadEvent) { if (dirty.value || busy.value || ambiguous.value) { event.preventDefault(); event.returnValue = '' } }
function keydown(event: KeyboardEvent) {
  if (event.key === 'Escape') { event.preventDefault(); close(); return }
  if (event.key !== 'Tab') return
  const elements = [...(dialog.value?.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex="0"]') ?? [])].filter((element) => element.offsetParent !== null)
  if (!elements.length) { event.preventDefault(); return }
  if (event.shiftKey && (document.activeElement === elements[0] || document.activeElement === dialog.value)) { event.preventDefault(); elements[elements.length - 1].focus() }
  else if (!event.shiftKey && document.activeElement === elements[elements.length - 1]) { event.preventDefault(); elements[0].focus() }
}
onMounted(() => {
  window.addEventListener('beforeunload', beforeUnload)
  if (!props.disabled && !props.restricted) void loadContext('new').then((result) => {
    if (!disposed) composeAllowed.value = result.can_compose
  }).catch(() => { composeAllowed.value = false })
})
onBeforeUnmount(() => { disposed = true; ++generation; window.removeEventListener('beforeunload', beforeUnload); emit('active', false) })
defineExpose({ open })
</script>

<template>
  <div class="flex flex-wrap items-center gap-2" data-mail-composer-toolbar>
    <template v-if="!restricted && composeAllowed">
      <button type="button" :disabled="disabled || locked" class="inline-flex items-center gap-2 rounded-md bg-primary px-3 py-2 text-sm text-primary-foreground disabled:opacity-40" data-mail-compose-new @click="open('new')"><MailPlus class="size-4" /> Nova mensagem</button>
      <button type="button" :disabled="disabled || locked" class="inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm disabled:opacity-40" data-mail-compose-drafts @click="showList('drafts')"><FileText class="size-4" /> Rascunhos do DaVinci</button>
      <button type="button" :disabled="disabled || locked" class="inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm disabled:opacity-40" data-mail-compose-outbox @click="showList('outbox')"><Send class="size-4" /> Meus envios</button>
    </template>
    <Teleport to="body">
      <div v-if="visible" class="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 p-2 sm:p-6" @click.self="close">
        <section ref="dialog" role="dialog" aria-modal="true" aria-labelledby="mail-composer-title" tabindex="-1" class="flex max-h-[94dvh] w-full max-w-3xl flex-col overflow-hidden rounded-xl border bg-background shadow-2xl outline-none" @keydown="keydown">
          <header class="flex items-center gap-3 border-b px-5 py-4">
            <div class="min-w-0 flex-1"><h2 id="mail-composer-title" class="font-semibold">{{ view === 'drafts' ? 'Rascunhos do DaVinci' : view === 'outbox' ? 'Meus envios' : titles[action] }}</h2><p class="truncate text-xs text-muted-foreground">{{ mailboxLabel }}</p></div>
            <button type="button" :disabled="locked" class="rounded p-2 hover:bg-muted disabled:opacity-40" aria-label="Fechar editor de e-mail" @click="close"><X class="size-5" /></button>
          </header>
          <div class="overflow-y-auto p-5">
            <p v-if="error" role="alert" class="mb-4 rounded border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">{{ error }}</p>
            <p v-if="notice" role="status" class="mb-4 rounded border bg-muted/40 p-3 text-sm">{{ notice }}</p>
            <p v-if="loading" class="flex items-center gap-2 py-6 text-sm text-muted-foreground"><LoaderCircle class="size-4 animate-spin" /> Carregando…</p>
            <template v-else-if="view === 'drafts'">
              <p class="mb-4 text-xs text-muted-foreground">Rascunhos pessoais salvos no DaVinci. Eles não aparecem na pasta Rascunhos do Tuta.</p>
              <p v-if="!drafts.length" class="py-6 text-center text-sm text-muted-foreground">Nenhum rascunho salvo nesta caixa.</p>
              <div v-for="item in drafts" :key="item.id" class="flex items-center gap-3 border-b py-3">
                <button type="button" class="min-w-0 flex-1 text-left" :disabled="locked" @click="restore(item.id)"><span class="block truncate text-sm font-medium">{{ item.subject || '(Sem assunto)' }}</span><span class="text-xs text-muted-foreground">{{ titles[item.action] }} · {{ date(item.updated_at) }}</span></button>
                <button type="button" class="rounded p-2 text-muted-foreground hover:text-destructive" :disabled="locked" :aria-label="`Excluir rascunho ${item.subject || 'sem assunto'}`" @click="removeDraft(item.id)"><Trash2 class="size-4" /></button>
              </div>
            </template>
            <template v-else-if="view === 'outbox'">
              <button type="button" class="mb-4 rounded border px-3 py-2 text-sm" :disabled="locked" @click="showList('outbox')">Atualizar envios</button>
              <p v-if="!outgoing.length" class="py-6 text-center text-sm text-muted-foreground">Nenhum envio registrado pelo seu usuário nesta caixa.</p>
              <article v-for="item in outgoing" :key="item.id" class="mb-3 space-y-2 rounded-lg border p-4">
                <p class="text-sm font-medium">{{ item.subject || '(Sem assunto)' }}</p><p class="text-xs text-muted-foreground">{{ statuses[item.status] || item.status }} · {{ date(item.created_at) }}</p>
                <p v-if="item.to.length" class="break-all text-xs">Para: {{ addresses(item.to) }}</p><p v-if="item.cc?.length" class="break-all text-xs">CC: {{ item.cc.join(', ') }}</p><p v-if="item.bcc?.length" class="break-all text-xs">CCO: {{ item.bcc.join(', ') }}</p>
                <p v-if="item.status === 'failed'" class="text-xs text-destructive">{{ failure({ data: { detail: { code: item.error_code } } }) }}</p>
                <p v-if="item.attachments?.length" class="text-xs text-muted-foreground">Anexos: {{ item.attachments.map((file) => file.filename).join(', ') }}</p>
                <div v-if="item.status === 'uncertain'" class="flex flex-wrap items-center gap-2 text-xs"><span>Conferiu nos Enviados do Tuta?</span><button type="button" class="rounded border px-2 py-1" :disabled="locked" @click="resolveOutgoing(item, true)">Saiu</button><button type="button" class="rounded border px-2 py-1" :disabled="locked" @click="resolveOutgoing(item, false)">Não saiu</button></div>
                <details><summary class="cursor-pointer text-xs text-muted-foreground">Ver mensagem</summary><p class="mt-2 whitespace-pre-wrap break-words text-sm">{{ item.text }}</p></details>
              </article>
            </template>
            <form v-else class="space-y-4" @submit.prevent="send">
              <p v-if="!context?.can_compose" class="rounded bg-muted p-3 text-sm">Sua permissão nesta caixa não permite este tipo de envio.</p>
              <fieldset :disabled="locked || ambiguous || !context?.can_compose" class="space-y-3 disabled:opacity-70">
                <label class="block text-sm">De<select v-model="from" class="mt-1 block w-full rounded border bg-background p-2" aria-label="Remetente"><option value="" disabled>Selecione o remetente</option><option v-for="address in context?.allowed_from_addresses || []" :key="address" :value="address">{{ address }}</option></select></label>
                <label class="block text-sm">Para<input v-model="to" type="text" autocomplete="off" maxlength="16000" class="mt-1 block w-full rounded border bg-background p-2" placeholder="nome@exemplo.com" aria-label="Destinatários" /></label>
                <button type="button" class="text-xs text-primary" :aria-expanded="expandedCopies" @click="expandedCopies = !expandedCopies">{{ expandedCopies ? 'Ocultar CC e CCO' : 'Adicionar CC / CCO' }}</button>
                <div v-show="expandedCopies" class="space-y-3"><label class="block text-sm">CC<input v-model="cc" autocomplete="off" maxlength="16000" class="mt-1 block w-full rounded border bg-background p-2" aria-label="Com cópia" /></label><label class="block text-sm">CCO <span class="text-xs text-muted-foreground">(cópia oculta)</span><input v-model="bcc" autocomplete="off" maxlength="16000" class="mt-1 block w-full rounded border bg-background p-2" aria-label="Com cópia oculta" /></label></div>
                <p class="text-xs text-muted-foreground">Separe os endereços com vírgula ou ponto e vírgula. Até 100 destinatários.</p>
                <p v-if="source?.reply_to && source.reply_to !== source.from_address && action !== 'forward'" class="text-xs text-amber-700">O e-mail indica um endereço de resposta diferente do remetente. Confira o campo Para.</p>
                <label class="block text-sm">Assunto<input v-model="subject" maxlength="998" class="mt-1 block w-full rounded border bg-background p-2" aria-label="Assunto da mensagem" /></label>
                <label class="block text-sm">Mensagem<textarea v-model="text" rows="10" maxlength="100000" class="mt-1 block min-h-48 w-full rounded border bg-background p-3 text-sm leading-relaxed" placeholder="Escreva sua mensagem…" aria-label="Texto da mensagem" /></label>
                <div class="space-y-2 rounded-lg border p-3">
                  <div class="flex flex-wrap items-center justify-between gap-2"><button type="button" class="inline-flex items-center gap-2 rounded border px-3 py-2 text-sm" data-mail-add-attachments @click="fileInput?.click()"><Paperclip class="size-4" /> Adicionar anexos</button><span class="text-xs text-muted-foreground">{{ attachmentCount }}/10 arquivos · {{ sizeLabel(attachmentSize) }}</span></div>
                  <input ref="fileInput" type="file" multiple class="hidden" aria-label="Selecionar anexos" @change="addFiles" />
                  <p class="text-xs text-muted-foreground">Até 10 MB por arquivo e 15 MB no total.</p>
                  <div v-for="(file, index) in attachments" :key="`${index}-${file.filename}`" class="flex items-center gap-2 text-sm"><Paperclip class="size-4 shrink-0" /><span class="min-w-0 flex-1 truncate">{{ file.filename }}</span><span class="shrink-0 text-xs text-muted-foreground">{{ sizeLabel(base64Bytes(file.data_base64)) }}</span><button type="button" class="rounded p-1" :aria-label="`Remover anexo ${file.filename}`" @click="attachments.splice(index, 1)"><X class="size-4" /></button></div>
                  <div v-if="action === 'forward' && source?.attachments.length" class="space-y-2 border-t pt-2"><p class="text-xs font-medium">Anexos da mensagem original</p><label v-for="file in source.attachments" :key="file.id" class="flex items-center gap-2 text-sm"><input v-model="sourceAttachmentIds" type="checkbox" :value="file.id" /><span class="min-w-0 flex-1 truncate">{{ file.filename }}</span><span class="shrink-0 text-xs text-muted-foreground">{{ sizeLabel(file.size) }}</span></label></div>
                </div>
              </fieldset>
              <p v-if="pending" class="text-xs text-amber-700">Há um envio pendente ou em conferência para a mensagem original. Confira os envios antes de continuar.</p>
              <div v-else-if="context?.can_compose && !context.send_ready" class="space-y-1 text-xs text-muted-foreground"><p>Envio indisponível no momento. Você pode salvar o rascunho e enviar quando o Mac estiver conectado.</p><button type="button" :disabled="locked" class="text-primary underline" @click="refreshAuthorization">Atualizar conexão</button></div>
              <p v-if="from && context && !context.allowed_from_addresses.includes(from)" class="text-xs text-destructive">O remetente do rascunho não está mais autorizado. Selecione outro.</p>
              <div class="flex flex-wrap items-center gap-2 border-t pt-4">
                <button type="submit" :disabled="!canSend" class="inline-flex items-center gap-2 rounded bg-primary px-4 py-2 text-sm text-primary-foreground disabled:opacity-40" data-mail-compose-send><Send class="size-4" /> {{ busy ? 'Aguarde…' : ambiguous ? 'Tentar confirmar envio' : 'Conferir e enviar' }}</button>
                <button type="button" :disabled="locked || !context?.can_compose || ambiguous" class="inline-flex items-center gap-2 rounded border px-3 py-2 text-sm disabled:opacity-40" data-mail-compose-save @click="saveDraft"><Save class="size-4" /> Salvar rascunho</button>
                <span class="text-xs text-muted-foreground">{{ dirty ? 'Alterações não salvas' : draftId ? 'Rascunho salvo' : '' }}</span>
              </div>
            </form>
          </div>
        </section>
      </div>
    </Teleport>
  </div>
</template>
