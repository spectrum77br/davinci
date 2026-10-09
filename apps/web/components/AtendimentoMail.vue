<script setup lang="ts">
import { Download, Folder, Mail, Paperclip, Plus, RefreshCw, Send, Settings2, ShieldAlert, ShieldCheck, X } from 'lucide-vue-next'
import type { ResumoLoja } from '~/components/AtendimentoPlataforma.vue'
import type { MailAttachment } from '~/lib/mailHtml'

type Mailbox = { id: string; label: string; address: string; aliases: string[]; state: string; send_enabled: boolean; can_send: boolean; last_sync_at: string | null; so_leitura?: boolean }
type Summary = { id: string; subject: string; from_address: string; from_name: string; received_at: string; direction: string; folder: string; has_attachments: boolean; store?: StoreBadge; folder_id?: string; subject_hidden?: boolean }
// Folders and store badges ("Caixas", 09/10/2026 — the owner: "bring the store
// name here too; keep it separate and looking like Tuta"). routers/mail_caixa.py:
// GET …/pastas = the folders (Tuta order, Portuguese names) and the stores of
// the mailbox, with counts; GET …/lista = a page of summaries with the folder
// and the store badge (never the body; a security email comes without its
// subject), filtered by folder and store. Both read a light index of the
// encrypted content. Without those routes (older API: 404/405) and with no
// filter chosen, the list falls back to …/messages. A server error does NOT
// fall back: the plain list shows a security email's subject, which the
// folder/store view never does — the error shows instead.
type StoreBadge = { tipo: string; rotulo: string; plataforma: string | null; loja?: string | null; chave?: string; provavel?: boolean }
type FolderItem = { id: string | null; nome: string; caminho: string | null; tipo: string; quantidade: number; total: number }
type StoreItem = StoreBadge & { chave: string; quantidade: number; total: number }
type IndexItem = { id: string; recebido_em: string; direcao: string; tem_anexos: boolean; ponte: string | null; de: string | null; de_nome: string | null; assunto: string | null; assunto_oculto: boolean; pasta: { id: string; nome: string; tipo: string }; selo: StoreBadge }
type Outbox = { id: string; status: string; text: string; to: string; from_address: string; created_at: string; error_code: string | null }
type Detail = Summary & { text: string; html?: string | null; to: string[]; cc: string[]; reply_to: string | null; attachments: MailAttachment[]; reply: { to: string; from_address: string; can_reply: boolean; send_ready: boolean }; outbox: Outbox[] }

// `canOperate` = the person operates /atendimento (`atendimento_mexe`): sees the
// Atendimento queues ("Filas", AtendimentoMailFilas). `stores` = the stores of
// the /atendimento summary, to pick one for an email without store.
const props = defineProps<{ isAdmin: boolean; canOperate?: boolean; stores?: ResumoLoja[] }>()
const emit = defineEmits<{ (e: 'openConversation', id: string): void }>()
const { api, url } = useApi()
const mailboxes = ref<Mailbox[]>([])
const mailboxId = ref('')
const mailbox = computed(() => mailboxes.value.find((item) => item.id === mailboxId.value))
// "Quem mais vê" (09/10/2026): the API marks a mailbox the person only READS
// (`so_leitura`): no reply, no resolve, no settings, no token.
const readOnly = computed(() => mailbox.value?.so_leitura === true)
const messages = ref<Summary[]>([])
const detail = ref<Detail | null>(null)
const loading = ref(false)
const sending = ref(false)
const error = ref('')
const more = ref(false)
const draft = ref('')
const fromAddress = ref('')
const draftRequestId = ref('')
const showSetup = ref(false)
const setupBusy = ref(false)
const setup = reactive({ label: '', address: '', aliases: '' })
const newAgentToken = ref('')
const newMailboxId = ref('')
let listGeneration = 0
let detailGeneration = 0
// Folders/stores of the selected mailbox (empty = routes unavailable: no column).
const folders = ref<FolderItem[]>([])
const mailStores = ref<StoreItem[]>([])
const folderId = ref<string | null>(null)
const storeKey = ref<string | null>(null)
const nextCursor = ref<string | null>(null)
const listSource = ref<'index' | 'plain'>('plain')
// Emails the index still has to sort (the worker job catches up in ~1 min).
const organizing = ref(0)
// The list entry of the open message: its store badge and folder for the header.
const openedSummary = ref<Summary | null>(null)
let foldersGeneration = 0
const POLL_MS = 5_000
const METADATA_MS = 30_000
const READ_OPTIONS = { timeout: 15_000, retry: 0 }
let timer: ReturnType<typeof setTimeout> | undefined
let disposed = false
let mounted = false
let lastMailboxesAt = 0
let lastFoldersAt = 0
let lastDetailAt = 0
let paginated = false
let selectionEpoch = 0
let mailboxRequest: Promise<void> | null = null
let folderRequest: { key: string; promise: Promise<void> } | null = null
let listRequest: { key: string; append: boolean; promise: Promise<void> } | null = null
let refreshRequest: Promise<void> | null = null
let manualRefreshRequested = false
const currentFolder = computed(() => folders.value.find((item) => item.id === folderId.value) || null)
const currentStore = computed(() => (storeKey.value ? mailStores.value.find((item) => item.chave === storeKey.value) || null : null))
// Sections (08/10/2026): "Caixas" is this screen, unchanged; "Filas" is the
// Atendimento layer on top of it (emails without store/link, suspicious,
// summaries, sends to review). Whoever is neither a mailbox owner nor an admin
// lands on "Filas" when they can see it.
const chosenSection = ref<'' | 'mailboxes' | 'queues'>('')
const section = computed(() => {
  if (chosenSection.value === 'queues' && props.canOperate) return 'queues'
  if (chosenSection.value === 'mailboxes') return 'mailboxes'
  return props.canOperate && !props.isAdmin && !mailboxes.value.length ? 'queues' : 'mailboxes'
})
// "Configurar": aliases and the Atendimento settings of the selected mailbox
// (AtendimentoMailConfigurar) — admins who operate Atendimento only.
const configuring = ref(false)
const resolving = ref('')

const pending = computed(() => detail.value?.outbox.some((job) => ['queued', 'leased', 'uncertain'].includes(job.status)) ?? false)
const canReply = computed(() => !readOnly.value && detail.value?.reply.can_reply && detail.value.reply.send_ready && !pending.value)
const states: Record<string, string> = { online: 'Conectada', offline: 'Mac desconectado', login_required: 'Faça login no Mac', error: 'Conexão precisa de atenção' }
const statuses: Record<string, string> = { queued: 'Na fila do Mac', leased: 'Envio em andamento', sent: 'Aceita pelo serviço de e-mail', failed: 'Não enviada', uncertain: 'Precisa conferir no Tuta — não reenviada' }

// The refusal (HTTP detail.code) or a failed job's error_code, in plain Portuguese.
const knownErrors: Record<string, string> = {
  sending_unavailable: 'O envio não está disponível. Confira a conexão do Mac e a permissão de envio.',
  reply_already_pending: 'Já existe uma resposta pendente para esta mensagem.',
  request_id_conflict: 'Esta tentativa já foi registrada com outro conteúdo. Atualize e confira a resposta anterior.',
  sender_not_authorized: 'Este remetente não pertence à caixa selecionada.',
  invalid_body: 'Revise os campos e tente novamente.',
  no_receiving_alias: 'Nenhum endereço desta caixa recebeu a mensagem: com o remetente estrito, não há de onde responder.',
  sender_not_receiving_alias: 'Com o remetente estrito, a resposta sai pelo endereço que recebeu a mensagem.',
  main_address_not_allowed: 'Caixa da empresa: o endereço principal (o login da conta do Tuta) nunca responde ao cliente. Responda por um alias que recebeu a mensagem.',
  visibilidade_mudou: 'A caixa passou de empresa para privada antes do envio: nada foi enviado. Responda de novo se ainda fizer sentido.',
  atendimento_permission_required: 'Caixa da empresa: só quem cuida do Atendimento pode fazer isso.',
  test_mode_recipient: 'Modo de teste: a resposta só sai para os endereços de teste desta caixa.',
  sending_paused: 'O envio desta caixa está pausado.',
  sending_disabled: 'O envio pelo DaVinci está desligado (ATENDIMENTO_ENVIO_ATIVO): nada sai desta caixa da empresa.',
  hourly_limit: 'Teto de respostas da hora desta caixa atingido.',
  daily_limit: 'Teto de respostas do dia desta caixa atingido.',
  account_hourly_limit: 'A conta do Tuta chegou ao teto de envios da hora.',
  job_not_uncertain: 'Esta resposta não está mais esperando conferência. Atualize.',
  job_not_found: 'Resposta não encontrada. Atualize.',
  queued_timeout: 'A resposta ficou mais de 2 h na fila sem sair: nada foi enviado. Responda de novo se ainda fizer sentido.',
  author_access_revoked: 'Quem respondeu perdeu o acesso à caixa antes do envio: nada foi enviado.',
}

function errorText(reason: any) {
  const code = reason?.data?.detail?.code
  return knownErrors[code] || 'Não foi possível concluir. Atualize e tente novamente.'
}

// Why a queued reply did not go out (the lease marked it failed): e.g. the
// main address of a company mailbox, or the mailbox went back to private.
function jobFailure(code: string | null) {
  return (code && knownErrors[code]) || ''
}

function date(value: string | null) {
  return value ? new Date(value).toLocaleString('pt-BR') : 'Ainda não sincronizada'
}

// The list date, like Tuta: the time today, the day otherwise.
function shortDate(value: string) {
  const when = new Date(value)
  const now = new Date()
  if (when.toDateString() === now.toDateString()) return when.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })
  return when.toLocaleDateString('pt-BR', when.getFullYear() === now.getFullYear() ? { day: '2-digit', month: '2-digit' } : { day: '2-digit', month: '2-digit', year: '2-digit' })
}

function fromIndex(item: IndexItem): Summary {
  return {
    id: item.id, subject: item.assunto || '', from_address: item.de || '', from_name: item.de_nome || '', received_at: item.recebido_em,
    direction: item.direcao, folder: item.pasta.nome, has_attachments: item.tem_anexos, store: item.selo, folder_id: item.pasta.id, subject_hidden: item.assunto_oculto,
  }
}

// Only a missing route (older API) permits the plain list. Network failures
// must not bypass the index, which hides sensitive security-email subjects.
function indexRouteMissing(reason: any) {
  const status = Number(reason?.status ?? reason?.statusCode ?? reason?.response?.status ?? 0)
  return status === 404 || status === 405
}

async function loadIndexed(id: string, append: boolean) {
  const query = new URLSearchParams({ limite: '50' })
  if (folderId.value) query.set('pasta', folderId.value)
  if (storeKey.value) query.set('loja', storeKey.value)
  if (append && nextCursor.value) query.set('antes', nextCursor.value)
  const result = await api<{ itens: IndexItem[]; proximo: string | null; faltam: number }>(`/api/mail/mailboxes/${id}/lista?${query}`, READ_OPTIONS)
  return { items: result.itens.map(fromIndex), next: result.proximo, missing: result.faltam }
}

function listKey() {
  return JSON.stringify([mailboxId.value, folderId.value, storeKey.value, selectionEpoch])
}

async function loadFolders(): Promise<void> {
  if (disposed) return
  const key = listKey()
  if (folderRequest) {
    const previous = folderRequest
    await previous.promise
    if (disposed || key !== listKey() || previous.key === key) return
    return loadFolders()
  }
  const promise = readFolders(key)
  folderRequest = { key, promise }
  try { await promise } finally { if (folderRequest?.promise === promise) folderRequest = null }
}

async function readFolders(key: string) {
  if (!mailboxId.value) { folders.value = []; mailStores.value = []; return }
  const generation = ++foldersGeneration
  const id = mailboxId.value
  const query = new URLSearchParams()
  if (folderId.value) query.set('pasta', folderId.value)
  if (storeKey.value) query.set('loja', storeKey.value)
  const search = query.toString()
  try {
    const result = await api<{ pastas: FolderItem[]; lojas: StoreItem[]; faltam: number }>(`/api/mail/mailboxes/${id}/pastas${search ? `?${search}` : ''}`, READ_OPTIONS)
    if (disposed || generation !== foldersGeneration || key !== listKey()) return
    folders.value = result.pastas
    mailStores.value = result.lojas
    organizing.value = result.faltam
    lastFoldersAt = Date.now()
    // The chosen folder/store is gone (emails moved away): back to all.
    const lostFolder = !!folderId.value && !result.pastas.some((item) => item.id === folderId.value)
    const lostStore = !!storeKey.value && !result.lojas.some((item) => item.chave === storeKey.value)
    if (lostFolder) folderId.value = null
    if (lostStore) storeKey.value = null
    if (lostFolder || lostStore) {
      resetList()
      void loadList()
    }
  } catch {
    // Keeps the column it had; without the routes there is no column at all.
  }
}

// The list first (its route indexes what just arrived), then the counts, so
// both read the same index.
async function loadList() {
  const key = listKey()
  await loadMessages()
  if (!disposed && key === listKey()) await loadFolders()
}

function resetList() {
  ++selectionEpoch
  loading.value = false
  ++listGeneration
  ++foldersGeneration
  messages.value = []
  more.value = false
  nextCursor.value = null
  paginated = false
}

function chooseFilter(kind: 'folder' | 'store', value: string | null) {
  if (sending.value || disposed) return
  if (kind === 'folder') folderId.value = value
  else storeKey.value = value
  resetList()
  void loadList()
}

async function loadMailboxes() {
  if (disposed) return
  if (mailboxRequest) return mailboxRequest
  const promise = (async () => {
    try {
      const result = await api<{ items: Mailbox[] }>('/api/mail/mailboxes', READ_OPTIONS)
      if (disposed) return
      mailboxes.value = result.items
      lastMailboxesAt = Date.now()
      if (!result.items.some((item) => item.id === mailboxId.value)) mailboxId.value = result.items[0]?.id || ''
    } catch (reason) { if (!disposed) error.value = errorText(reason) }
  })()
  mailboxRequest = promise
  try { await promise } finally { if (mailboxRequest === promise) mailboxRequest = null }
}

// Share the current read instead of invalidating a page that is still loading.
// A different box/filter waits for that read, then fetches its own selection.
async function loadMessages(append = false, preservePages = false): Promise<void> {
  if (disposed) return
  const key = listKey()
  if (listRequest) {
    const previous = listRequest
    await previous.promise
    if (disposed || key !== listKey()) return
    if (previous.key === key && previous.append === append) return
    return loadMessages(append, preservePages)
  }
  const promise = readMessages(append, preservePages, key)
  listRequest = { key, append, promise }
  try { await promise } finally { if (listRequest?.promise === promise) listRequest = null }
}

function applyPage(items: Summary[], append: boolean, preservePages: boolean, hasMore: boolean, cursor: string | null, source: 'index' | 'plain') {
  if (append) {
    messages.value = [...new Map([...messages.value, ...items].map((item) => [item.id, item])).values()]
    paginated = true
  } else {
    // Keep the older pages only when the refreshed page overlaps them. If more
    // than a whole page arrived, restart at the new cursor rather than skip a gap.
    const ids = new Set(items.map((item) => item.id))
    let boundary = -1
    if (preservePages && paginated && hasMore && source === listSource.value) {
      messages.value.forEach((item, index) => { if (ids.has(item.id)) boundary = index })
    }
    if (boundary >= 0) {
      messages.value = [...items, ...messages.value.slice(boundary + 1).filter((item) => !ids.has(item.id))]
      return // The cursor and `more` still describe the last loaded page.
    }
    messages.value = items
    paginated = false
  }
  more.value = hasMore
  nextCursor.value = cursor
  listSource.value = source
}

async function readMessages(append: boolean, preservePages: boolean, key: string) {
  if (!mailboxId.value) { messages.value = []; return }
  const generation = ++listGeneration
  const id = mailboxId.value
  loading.value = true
  const current = () => !disposed && generation === listGeneration && key === listKey()
  try {
    if (!append || listSource.value === 'index') {
      const indexed = await loadIndexed(id, append).catch((reason) => {
        // Filters require the index; only a missing unfiltered route falls back.
        if (append || folderId.value || storeKey.value || !indexRouteMissing(reason)) throw reason
        return null
      })
      if (!current()) return
      if (indexed) {
        applyPage(indexed.items, append, preservePages, !!indexed.next, indexed.next, 'index')
        organizing.value = indexed.missing
        return
      }
    }
    const offset = append ? messages.value.length : 0
    const result = await api<{ items: Summary[]; more: boolean }>(`/api/mail/mailboxes/${id}/messages?limit=50&offset=${offset}`, READ_OPTIONS)
    if (!current()) return
    applyPage(result.items, append, preservePages, result.more, null, 'plain')
    organizing.value = 0
  } catch (reason) { if (current()) error.value = errorText(reason) }
  finally { if (!disposed && generation === listGeneration) loading.value = false }
}

async function openMessage(id: string, preserveDraft = false) {
  if (disposed) return
  if (!preserveDraft && draft.value.trim() && detail.value?.id !== id && !window.confirm('Descartar o rascunho desta resposta e abrir outra mensagem?')) return
  const listed = messages.value.find((item) => item.id === id)
  if (listed || !preserveDraft) openedSummary.value = listed?.store ? listed : null
  const generation = ++detailGeneration
  const selectedMailbox = mailboxId.value
  if (!preserveDraft) {
    detail.value = null
    draft.value = ''
    draftRequestId.value = ''
  }
  error.value = ''
  try {
    const result = await api<Detail>(`/api/mail/messages/${id}`, READ_OPTIONS)
    if (disposed || generation !== detailGeneration || mailboxId.value !== selectedMailbox) return
    detail.value = result
    lastDetailAt = Date.now()
    if (!preserveDraft) fromAddress.value = result.reply.from_address
  } catch (reason) { if (!disposed && generation === detailGeneration) error.value = errorText(reason) }
}

// One refresh at a time, including manual clicks and the two resume events.
// A manual request during polling is fulfilled as a full refresh afterwards.
async function refresh(background = false): Promise<void> {
  if (disposed) return
  if (!background) manualRefreshRequested = true
  if (refreshRequest) return refreshRequest
  const promise = (async () => {
    do {
      const full = manualRefreshRequested
      manualRefreshRequested = false
      if (full) error.value = ''
      if (full || Date.now() - lastMailboxesAt >= METADATA_MS) await loadMailboxes()
      if (disposed || (background && document.hidden)) return
      const key = listKey()
      await loadMessages(false, !full)
      if (disposed || key !== listKey() || (background && document.hidden)) return
      if (manualRefreshRequested && !full) continue
      if (full || organizing.value || Date.now() - lastFoldersAt >= METADATA_MS) await loadFolders()
      if (disposed || key !== listKey() || (background && document.hidden)) return
      if (detail.value && (full || pending.value || Date.now() - lastDetailAt >= METADATA_MS)) await openMessage(detail.value.id, true)
    } while (manualRefreshRequested && !disposed)
  })()
  refreshRequest = promise
  try { await promise } finally { if (refreshRequest === promise) refreshRequest = null }
}

function schedulePoll() {
  clearTimeout(timer)
  timer = undefined
  if (!mounted || disposed || document.hidden) return
  timer = setTimeout(() => { timer = undefined; void poll() }, POLL_MS)
}

async function poll() {
  if (!mounted || disposed || document.hidden) return
  try {
    if (!sending.value && !resolving.value) await refresh(true)
  } finally { schedulePoll() }
}

function resumePolling() {
  clearTimeout(timer)
  timer = undefined
  if (!document.hidden) void poll()
}

watch(mailboxId, () => {
  if (disposed) return
  ++detailGeneration
  resetList()
  detail.value = null
  messages.value = []
  draft.value = ''
  draftRequestId.value = ''
  folders.value = []
  mailStores.value = []
  folderId.value = null
  storeKey.value = null
  nextCursor.value = null
  organizing.value = 0
  openedSummary.value = null
  lastFoldersAt = 0
  lastDetailAt = 0
  void loadList()
})

async function createMailbox() {
  if (setupBusy.value) return
  setupBusy.value = true
  error.value = ''
  try {
    const created = await api<Mailbox & { agent_token: string }>('/api/mail/mailboxes', {
      method: 'POST', body: { label: setup.label.trim(), address: setup.address.trim(), aliases: setup.aliases.split(/[\n,;]/).map((item) => item.trim()).filter(Boolean) },
    })
    newAgentToken.value = created.agent_token
    newMailboxId.value = created.id
    showSetup.value = false
    setup.label = ''; setup.address = ''; setup.aliases = ''
    await loadMailboxes()
    mailboxId.value = created.id
  } catch (reason) { error.value = errorText(reason) }
  finally { setupBusy.value = false }
}

async function changeSending() {
  if (!mailbox.value || !props.isAdmin) return
  const enabled = !mailbox.value.send_enabled
  if (enabled && !window.confirm('Permitir respostas escritas por pessoas nesta caixa? O Mac também precisa estar conectado e com o envio liberado.')) return
  try {
    await api(`/api/mail/mailboxes/${mailboxId.value}`, { method: 'PATCH', body: { send_enabled: enabled } })
    await refresh()
  } catch (reason) { error.value = errorText(reason) }
}

async function sendReply() {
  if (!detail.value || !canReply.value || !draft.value.trim() || sending.value) return
  if (!window.confirm(`Enviar esta resposta de ${fromAddress.value} para ${detail.value.reply.to}?`)) return
  const id = detail.value.id
  draftRequestId.value ||= crypto.randomUUID()
  sending.value = true
  error.value = ''
  try {
    await api(`/api/mail/messages/${id}/reply`, { method: 'POST', body: { request_id: draftRequestId.value, text: draft.value, from_address: fromAddress.value } })
    draft.value = ''
    draftRequestId.value = ''
    await openMessage(id, true)
  } catch (reason) {
    const message = errorText(reason)
    await openMessage(id, true)
    error.value = message
  } finally { sending.value = false }
}

// A person checked Tuta: did the uncertain reply go out? Never resends.
async function resolveJob(job: Outbox, sent: boolean) {
  if (!detail.value || resolving.value) return
  const question = sent
    ? 'Confirmar que esta resposta aparece nos Enviados do Tuta?'
    : 'Marcar que esta resposta NÃO saiu?\n\nConfira nos Enviados do Tuta antes: se ela saiu e alguém responder de novo, o cliente recebe duas vezes.'
  if (!window.confirm(question)) return
  const id = detail.value.id
  resolving.value = job.id
  error.value = ''
  try {
    await api(`/api/mail/outbox/${job.id}/resolve`, { method: 'POST', body: { saiu: sent } })
    await openMessage(id, true)
  } catch (reason) { error.value = errorText(reason) }
  finally { resolving.value = '' }
}

// A mailbox was just registered: open its settings (aliases, Atendimento).
watch(newMailboxId, (id) => { if (id && props.isAdmin) configuring.value = true })

onMounted(async () => {
  mounted = true
  document.addEventListener('visibilitychange', resumePolling)
  window.addEventListener('focus', resumePolling)
  await loadMailboxes()
  if (!disposed) {
    await loadList()
    schedulePoll()
  }
})
onBeforeUnmount(() => {
  disposed = true
  mounted = false
  clearTimeout(timer)
  document.removeEventListener('visibilitychange', resumePolling)
  window.removeEventListener('focus', resumePolling)
  ++listGeneration
  ++foldersGeneration
  ++detailGeneration
})
</script>

<template>
  <section class="space-y-4" aria-label="Central de e-mail">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h2 class="flex items-center gap-2 text-lg font-semibold"><Mail class="h-5 w-5" /> E-mail</h2>
        <p v-if="section === 'queues'" class="text-sm text-muted-foreground">Os e-mails das lojas que precisam de alguém: sem loja, sem vínculo, suspeitos, resumos e respostas a conferir.</p>
        <p v-else class="text-sm text-muted-foreground">Caixas privadas, acessíveis ao responsável, aos administradores e a quem um admin liberar (só leitura).</p>
      </div>
      <div v-if="section === 'mailboxes'" class="flex gap-2">
        <button v-if="isAdmin" class="inline-flex items-center gap-2 rounded border px-3 py-2 text-sm" @click="showSetup = !showSetup"><Plus class="h-4 w-4" /> Cadastrar caixa</button>
        <button class="inline-flex items-center gap-2 rounded border px-3 py-2 text-sm" :disabled="loading || sending" @click="refresh()"><RefreshCw class="h-4 w-4" /> Atualizar</button>
      </div>
    </div>

    <!-- Sections: the mailboxes (this screen) and the Atendimento queues (who operates Atendimento) -->
    <div v-if="canOperate" class="flex gap-1 border-b" role="tablist" aria-label="Seções do e-mail" data-mail-sections>
      <button
        type="button"
        role="tab"
        :aria-selected="section === 'mailboxes'"
        class="-mb-px border-b-2 px-3 py-1.5 text-sm"
        :class="section === 'mailboxes' ? 'border-primary font-medium' : 'border-transparent text-muted-foreground hover:text-foreground'"
        @click="chosenSection = 'mailboxes'"
      >Caixas</button>
      <button
        type="button"
        role="tab"
        :aria-selected="section === 'queues'"
        class="-mb-px border-b-2 px-3 py-1.5 text-sm"
        :class="section === 'queues' ? 'border-primary font-medium' : 'border-transparent text-muted-foreground hover:text-foreground'"
        @click="chosenSection = 'queues'"
      >Filas</button>
    </div>

    <AtendimentoMailFilas
      v-if="canOperate && section === 'queues'"
      :is-admin="isAdmin"
      :lojas="stores || []"
      @abrir-conversa="(id: string) => emit('openConversation', id)"
    />

    <template v-if="section === 'mailboxes'">
      <p v-if="error" role="alert" class="rounded border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">{{ error }}</p>

      <form v-if="showSetup && isAdmin" class="grid gap-3 rounded-lg border p-4 md:grid-cols-2" @submit.prevent="createMailbox">
        <label class="text-sm">Nome da caixa<input v-model="setup.label" required maxlength="120" class="mt-1 w-full rounded border bg-background p-2" placeholder="Ex.: Caixa principal" /></label>
        <label class="text-sm">Endereço principal<input v-model="setup.address" required type="email" class="mt-1 w-full rounded border bg-background p-2" autocomplete="off" /></label>
        <label class="text-sm md:col-span-2">Outros remetentes autorizados da mesma conta<textarea v-model="setup.aliases" class="mt-1 w-full rounded border bg-background p-2" rows="2" placeholder="Opcional: um endereço por linha" /></label>
        <p class="text-xs text-muted-foreground md:col-span-2">O login e a senha Tuta ficam no Mac. Esta etapa cadastra a caixa para seu usuário e não conecta nem envia e-mails.</p>
        <button class="w-fit rounded bg-primary px-4 py-2 text-sm text-primary-foreground" :disabled="setupBusy">{{ setupBusy ? 'Cadastrando…' : 'Cadastrar caixa privada' }}</button>
      </form>

      <div v-if="newAgentToken" class="space-y-2 rounded border p-4">
        <p class="font-medium">Chave de conexão do Mac</p>
        <p class="text-sm text-muted-foreground">Salve esta chave no arquivo privado de configuração do conector local. Ela aparece uma vez e dá acesso somente a esta caixa.</p>
        <label class="block text-xs">Identificador da caixa<input :value="newMailboxId" readonly class="mt-1 w-full rounded border bg-background p-2 font-mono" /></label>
        <label class="block text-xs">Chave privada<input :value="newAgentToken" readonly type="password" autocomplete="off" class="mt-1 w-full rounded border bg-background p-2 font-mono" /></label>
        <button class="rounded border px-3 py-2 text-sm" @click="newAgentToken = ''; newMailboxId = ''">Já salvei — ocultar</button>
      </div>

      <div v-if="!mailboxes.length && !loading" class="rounded-lg border border-dashed p-10 text-center text-muted-foreground">Nenhuma caixa disponível para seu usuário. Um administrador pode cadastrar uma caixa e conectá-la pelo Mac.</div>

      <template v-if="mailbox">
        <div class="flex flex-wrap items-center gap-3 rounded-lg border p-3">
          <select v-model="mailboxId" aria-label="Caixa de e-mail" :disabled="sending" class="max-w-full rounded border bg-background p-2 text-sm"><option v-for="item in mailboxes" :key="item.id" :value="item.id">{{ item.label }} · {{ item.address }}</option></select>
          <span class="text-sm">{{ states[mailbox.state] || 'Conexão precisa de atenção' }}</span>
          <span class="text-xs text-muted-foreground">Última leitura: {{ date(mailbox.last_sync_at) }}</span>
          <span v-if="readOnly" class="ml-auto text-xs text-muted-foreground" data-mail-read-only>Você só vê esta caixa (sem responder nem configurar).</span>
          <button v-if="isAdmin && !readOnly" class="ml-auto rounded border px-3 py-2 text-xs" @click="changeSending">{{ mailbox.send_enabled ? 'Desativar respostas' : 'Permitir respostas humanas' }}</button>
          <button v-if="isAdmin && !readOnly" class="inline-flex items-center gap-1 rounded border px-3 py-2 text-xs" :aria-expanded="configuring" data-mail-configure @click="configuring = !configuring"><Settings2 class="h-3.5 w-3.5" /> Configurar</button>
        </div>

        <AtendimentoMailConfigurar
          v-if="isAdmin && !readOnly && configuring"
          :key="mailbox.id"
          :mailbox="mailbox"
          @mudou="refresh"
          @fechar="configuring = false"
        />

        <!-- Like Tuta: folders (and stores) | list | reading. Below xl the folder and store selectors sit on top of the list. -->
        <div class="grid min-h-[520px] overflow-hidden rounded-lg border" :class="folders.length ? 'lg:grid-cols-[320px_minmax(0,1fr)] xl:grid-cols-[230px_340px_minmax(0,1fr)]' : 'lg:grid-cols-[340px_1fr]'">
          <AtendimentoMailPastasDaCaixa
            v-if="folders.length"
            modo="coluna"
            class="hidden max-h-[760px] border-r xl:flex"
            :pastas="folders"
            :lojas="mailStores"
            :pasta="folderId"
            :loja="storeKey"
            :desligado="sending"
            @pasta="(value: string | null) => chooseFilter('folder', value)"
            @loja="(value: string | null) => chooseFilter('store', value)"
          />
          <aside class="max-h-[760px] overflow-y-auto border-b lg:border-b-0 lg:border-r" aria-label="Mensagens">
            <div v-if="folders.length" class="sticky top-0 z-10 space-y-2 border-b bg-background/95 p-3 backdrop-blur" data-mail-list-header>
              <AtendimentoMailPastasDaCaixa
                modo="seletor"
                class="xl:hidden"
                :pastas="folders"
                :lojas="mailStores"
                :pasta="folderId"
                :loja="storeKey"
                :desligado="sending"
                @pasta="(value: string | null) => chooseFilter('folder', value)"
                @loja="(value: string | null) => chooseFilter('store', value)"
              />
              <div class="flex min-w-0 items-center gap-2">
                <h3 class="min-w-0 truncate text-sm font-semibold">{{ currentFolder?.nome || 'Todas' }}</h3>
                <span class="shrink-0 text-xs tabular-nums text-muted-foreground">{{ (currentFolder?.quantidade ?? 0).toLocaleString('pt-BR') }}</span>
                <button
                  v-if="currentStore"
                  type="button"
                  class="ml-auto inline-flex min-w-0 items-center gap-1 rounded text-xs text-muted-foreground hover:text-foreground"
                  :disabled="sending"
                  :aria-label="`Mostrar todas as lojas (agora: ${currentStore.rotulo})`"
                  data-mail-store-filter
                  @click="chooseFilter('store', null)"
                ><AtendimentoMailSelo :selo="currentStore" /><X class="h-3.5 w-3.5 shrink-0" /></button>
              </div>
              <p v-if="organizing" class="text-xs text-muted-foreground" data-mail-organizing>Organizando {{ organizing.toLocaleString('pt-BR') }} e-mail(s) por pasta e loja — as contagens se completam em instantes.</p>
            </div>
            <p v-if="loading && !messages.length" class="p-5 text-sm text-muted-foreground">Carregando mensagens…</p>
            <p v-else-if="!messages.length && (folderId || storeKey)" class="p-5 text-sm text-muted-foreground">{{ folderId ? (storeKey ? 'Nenhum e-mail desta loja nesta pasta.' : 'Nenhum e-mail nesta pasta.') : 'Nenhum e-mail desta loja.' }}</p>
            <p v-else-if="!messages.length" class="p-5 text-sm text-muted-foreground">Nenhuma mensagem recebida pelo conector ainda. Os originais continuam no Tuta.</p>
            <button v-for="item in messages" :key="item.id" :disabled="sending" class="block w-full space-y-1 border-b p-4 text-left hover:bg-muted/40" :class="detail?.id === item.id ? 'bg-muted' : ''" :data-mail-item="item.id" @click="openMessage(item.id)">
              <template v-if="item.store">
                <div class="flex items-baseline gap-2">
                  <span class="min-w-0 flex-1 truncate text-sm font-medium">{{ item.from_name || item.from_address || '(remetente ilegível)' }}</span>
                  <span class="shrink-0 text-xs text-muted-foreground" :title="date(item.received_at)">{{ shortDate(item.received_at) }}</span>
                </div>
                <div v-if="item.subject_hidden" class="flex items-center gap-1 truncate text-sm italic text-muted-foreground"><ShieldAlert class="h-3.5 w-3.5 shrink-0" />{{ item.subject }}</div>
                <div v-else class="truncate text-sm">{{ item.subject || '(Sem assunto)' }}</div>
                <div class="flex min-w-0 items-center gap-2 pt-0.5 text-xs text-muted-foreground">
                  <AtendimentoMailSelo :selo="item.store" />
                  <span v-if="!folderId" class="inline-flex min-w-0 items-center gap-1 truncate" data-mail-item-folder><Folder class="h-3 w-3 shrink-0" />{{ item.folder }}</span>
                  <span class="ml-auto flex shrink-0 items-center gap-1">
                    <Paperclip v-if="item.has_attachments" class="h-3 w-3" aria-label="Com anexo" />
                    <span v-if="item.direction === 'sent'">Enviado</span>
                  </span>
                </div>
              </template>
              <template v-else>
                <div class="truncate text-sm font-medium">{{ item.from_name || item.from_address }}</div>
                <div class="truncate text-sm">{{ item.subject || '(Sem assunto)' }}</div>
                <div class="flex justify-between gap-2 text-xs text-muted-foreground"><span>{{ date(item.received_at) }}</span><span>{{ item.direction === 'sent' ? 'Enviado' : item.has_attachments ? 'Com anexo' : '' }}</span></div>
              </template>
            </button>
            <button v-if="more" class="w-full p-3 text-sm text-primary" :disabled="loading" @click="loadMessages(true)">Carregar mais</button>
          </aside>

          <article v-if="detail" class="min-w-0 space-y-5 p-5">
            <header class="space-y-2 border-b pb-4">
              <h3 class="break-words text-lg font-semibold">{{ detail.subject || '(Sem assunto)' }}</h3>
              <p class="break-all text-sm"><span class="text-muted-foreground">De:</span> {{ detail.from_name }} &lt;{{ detail.from_address }}&gt;</p>
              <p class="break-all text-sm"><span class="text-muted-foreground">Para:</span> {{ detail.to.join(', ') }}</p>
              <p v-if="detail.cc.length" class="break-all text-sm"><span class="text-muted-foreground">Cc:</span> {{ detail.cc.join(', ') }}</p>
              <p class="text-xs text-muted-foreground">{{ date(detail.received_at) }}<template v-if="!openedSummary?.store"> · {{ detail.folder }}</template></p>
              <div v-if="openedSummary?.store" class="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground" data-mail-open-badge>
                <span class="inline-flex min-w-0 items-center gap-1">Loja: <AtendimentoMailSelo :selo="openedSummary.store" /></span>
                <span class="inline-flex min-w-0 items-center gap-1">Pasta: <Folder class="h-3 w-3 shrink-0" /><span class="truncate text-foreground">{{ openedSummary.folder }}</span></span>
              </div>
            </header>
            <AtendimentoMailBody v-if="detail.html" :key="detail.id" :html="detail.html" :text="detail.text" :attachments="detail.attachments" />
            <pre v-else class="max-h-[480px] whitespace-pre-wrap break-words overflow-y-auto font-sans text-sm leading-relaxed">{{ detail.text || '(Mensagem sem texto)' }}</pre>
            <div v-if="detail.attachments.length" class="flex flex-wrap gap-2">
              <a v-for="attachment in detail.attachments" :key="attachment.id" :href="url(`/api/mail/attachments/${attachment.id}`)" class="inline-flex max-w-full items-center gap-2 rounded border px-3 py-2 text-sm"><Download class="h-4 w-4 shrink-0" /><span class="truncate">{{ attachment.filename }}</span><span class="shrink-0 text-xs text-muted-foreground">{{ Math.ceil(attachment.size / 1024) }} KB</span></a>
            </div>
            <section v-if="detail.outbox.length" class="space-y-3 border-t pt-4" aria-label="Respostas desta mensagem">
              <div v-for="job in detail.outbox" :key="job.id" class="space-y-2 rounded bg-muted/40 p-3">
                <p class="text-xs font-medium">{{ statuses[job.status] || job.status }} · {{ date(job.created_at) }}</p>
                <p class="break-all text-xs text-muted-foreground">{{ job.from_address }} → {{ job.to }}</p>
                <p v-if="job.status === 'failed' && jobFailure(job.error_code)" class="text-xs text-red-700">{{ jobFailure(job.error_code) }}</p>
                <p class="whitespace-pre-wrap break-words text-sm">{{ job.text }}</p>
                <div v-if="job.status === 'uncertain' && !readOnly" class="flex flex-wrap items-center gap-2 pt-1" data-mail-resolve>
                  <span class="text-xs text-muted-foreground">Conferiu nos Enviados do Tuta?</span>
                  <button type="button" class="rounded border px-2 py-1 text-xs" :disabled="!!resolving" @click="resolveJob(job, true)">Saiu</button>
                  <button type="button" class="rounded border px-2 py-1 text-xs" :disabled="!!resolving" @click="resolveJob(job, false)">Não saiu</button>
                </div>
              </div>
            </section>
            <form v-if="detail.reply.can_reply && !readOnly" class="space-y-3 border-t pt-4" @submit.prevent="sendReply">
              <h4 class="font-medium">Responder</h4>
              <p class="break-all text-sm">Para: <strong>{{ detail.reply.to }}</strong></p>
              <p v-if="detail.reply_to && detail.reply_to !== detail.from_address" class="text-xs text-amber-700">A mensagem indica este endereço de resposta, diferente do remetente. Confira antes de enviar.</p>
              <label class="block text-sm">Remetente<select v-model="fromAddress" class="ml-2 max-w-full rounded border bg-background p-2" :disabled="sending"><option v-for="address in mailbox.aliases" :key="address" :value="address">{{ address }}</option></select></label>
              <textarea v-model="draft" rows="5" maxlength="100000" class="w-full rounded border bg-background p-3 text-sm" placeholder="Escreva sua resposta…" :disabled="sending || pending" aria-label="Texto da resposta" />
              <p v-if="!detail.reply.send_ready" class="text-xs text-muted-foreground">Envio indisponível. O Mac precisa estar conectado, com respostas habilitadas nesta caixa e no conector.</p>
              <p v-if="pending" class="text-xs text-muted-foreground">Confira a resposta pendente acima antes de preparar outra.</p>
              <button class="inline-flex items-center gap-2 rounded bg-primary px-4 py-2 text-sm text-primary-foreground disabled:opacity-40" :disabled="!canReply || !draft.trim() || sending"><Send class="h-4 w-4" />{{ sending ? 'Registrando…' : 'Conferir e enviar resposta' }}</button>
            </form>
          </article>
          <div v-else class="flex items-center justify-center p-10 text-sm text-muted-foreground"><ShieldCheck class="mr-2 h-5 w-5" /> Escolha uma mensagem para ler.</div>
        </div>
      </template>
    </template>
  </section>
</template>
