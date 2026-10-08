<script setup lang="ts">
import { Download, Mail, Plus, RefreshCw, Send, ShieldCheck } from 'lucide-vue-next'

type Mailbox = { id: string; label: string; address: string; aliases: string[]; state: string; send_enabled: boolean; can_send: boolean; last_sync_at: string | null }
type Summary = { id: string; subject: string; from_address: string; from_name: string; received_at: string; direction: string; folder: string; has_attachments: boolean }
type Outbox = { id: string; status: string; text: string; to: string; from_address: string; created_at: string; error_code: string | null }
type Detail = Summary & { text: string; to: string[]; cc: string[]; reply_to: string | null; attachments: { id: string; filename: string; size: number }[]; reply: { to: string; from_address: string; can_reply: boolean; send_ready: boolean }; outbox: Outbox[] }

const props = defineProps<{ isAdmin: boolean }>()
const { api, url } = useApi()
const mailboxes = ref<Mailbox[]>([])
const mailboxId = ref('')
const mailbox = computed(() => mailboxes.value.find((item) => item.id === mailboxId.value))
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
let timer: ReturnType<typeof setInterval> | undefined

const pending = computed(() => detail.value?.outbox.some((job) => ['queued', 'leased', 'uncertain'].includes(job.status)) ?? false)
const canReply = computed(() => detail.value?.reply.can_reply && detail.value.reply.send_ready && !pending.value)
const states: Record<string, string> = { online: 'Conectada', offline: 'Mac desconectado', login_required: 'Faça login no Mac', error: 'Conexão precisa de atenção' }
const statuses: Record<string, string> = { queued: 'Na fila do Mac', leased: 'Envio em andamento', sent: 'Aceita pelo serviço de e-mail', failed: 'Não enviada', uncertain: 'Precisa conferir no Tuta — não reenviada' }

function errorText(reason: any) {
  const code = reason?.data?.detail?.code
  const known: Record<string, string> = {
    sending_unavailable: 'O envio não está disponível. Confira a conexão do Mac e a permissão de envio.',
    reply_already_pending: 'Já existe uma resposta pendente para esta mensagem.',
    request_id_conflict: 'Esta tentativa já foi registrada com outro conteúdo. Atualize e confira a resposta anterior.',
    sender_not_authorized: 'Este remetente não pertence à caixa selecionada.',
    invalid_body: 'Revise os campos e tente novamente.',
  }
  return known[code] || 'Não foi possível concluir. Atualize e tente novamente.'
}

function date(value: string | null) {
  return value ? new Date(value).toLocaleString('pt-BR') : 'Ainda não sincronizada'
}

async function loadMailboxes() {
  try {
    const result = await api<{ items: Mailbox[] }>('/api/mail/mailboxes')
    mailboxes.value = result.items
    if (!result.items.some((item) => item.id === mailboxId.value)) mailboxId.value = result.items[0]?.id || ''
  } catch (reason) { error.value = errorText(reason) }
}

async function loadMessages(append = false) {
  if (!mailboxId.value) { messages.value = []; return }
  const generation = ++listGeneration
  const id = mailboxId.value
  loading.value = true
  try {
    const offset = append ? messages.value.length : 0
    const result = await api<{ items: Summary[]; more: boolean }>(`/api/mail/mailboxes/${id}/messages?limit=50&offset=${offset}`)
    if (generation !== listGeneration || id !== mailboxId.value) return
    messages.value = append ? [...new Map([...messages.value, ...result.items].map((item) => [item.id, item])).values()] : result.items
    more.value = result.more
  } catch (reason) { if (generation === listGeneration) error.value = errorText(reason) }
  finally { if (generation === listGeneration) loading.value = false }
}

async function openMessage(id: string, preserveDraft = false) {
  if (!preserveDraft && draft.value.trim() && detail.value?.id !== id && !window.confirm('Descartar o rascunho desta resposta e abrir outra mensagem?')) return
  const generation = ++detailGeneration
  const selectedMailbox = mailboxId.value
  if (!preserveDraft) {
    detail.value = null
    draft.value = ''
    draftRequestId.value = ''
  }
  error.value = ''
  try {
    const result = await api<Detail>(`/api/mail/messages/${id}`)
    if (generation !== detailGeneration || mailboxId.value !== selectedMailbox) return
    detail.value = result
    if (!preserveDraft) fromAddress.value = result.reply.from_address
  } catch (reason) { if (generation === detailGeneration) error.value = errorText(reason) }
}

async function refresh() {
  error.value = ''
  await loadMailboxes()
  await loadMessages()
  if (detail.value) await openMessage(detail.value.id, true)
}

watch(mailboxId, () => {
  ++detailGeneration
  detail.value = null
  messages.value = []
  draft.value = ''
  draftRequestId.value = ''
  void loadMessages()
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

onMounted(async () => {
  await loadMailboxes()
  timer = setInterval(() => { if (!document.hidden && !sending.value) void refresh() }, 30_000)
})
onBeforeUnmount(() => { clearInterval(timer); ++listGeneration; ++detailGeneration })
</script>

<template>
  <section class="space-y-4" aria-label="Central de e-mail">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h2 class="flex items-center gap-2 text-lg font-semibold"><Mail class="h-5 w-5" /> E-mail</h2>
        <p class="text-sm text-muted-foreground">Caixas privadas, acessíveis ao responsável e aos administradores.</p>
      </div>
      <div class="flex gap-2">
        <button v-if="isAdmin" class="inline-flex items-center gap-2 rounded border px-3 py-2 text-sm" @click="showSetup = !showSetup"><Plus class="h-4 w-4" /> Cadastrar caixa</button>
        <button class="inline-flex items-center gap-2 rounded border px-3 py-2 text-sm" :disabled="loading || sending" @click="refresh"><RefreshCw class="h-4 w-4" /> Atualizar</button>
      </div>
    </div>

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
        <button v-if="isAdmin" class="ml-auto rounded border px-3 py-2 text-xs" @click="changeSending">{{ mailbox.send_enabled ? 'Desativar respostas' : 'Permitir respostas humanas' }}</button>
      </div>

      <div class="grid min-h-[520px] overflow-hidden rounded-lg border lg:grid-cols-[340px_1fr]">
        <aside class="max-h-[760px] overflow-y-auto border-b lg:border-b-0 lg:border-r" aria-label="Mensagens">
          <p v-if="loading && !messages.length" class="p-5 text-sm text-muted-foreground">Carregando mensagens…</p>
          <p v-else-if="!messages.length" class="p-5 text-sm text-muted-foreground">Nenhuma mensagem recebida pelo conector ainda. Os originais continuam no Tuta.</p>
          <button v-for="item in messages" :key="item.id" :disabled="sending" class="block w-full space-y-1 border-b p-4 text-left hover:bg-muted/40" :class="detail?.id === item.id ? 'bg-muted' : ''" @click="openMessage(item.id)">
            <div class="truncate text-sm font-medium">{{ item.from_name || item.from_address }}</div>
            <div class="truncate text-sm">{{ item.subject || '(Sem assunto)' }}</div>
            <div class="flex justify-between gap-2 text-xs text-muted-foreground"><span>{{ date(item.received_at) }}</span><span>{{ item.direction === 'sent' ? 'Enviado' : item.has_attachments ? 'Com anexo' : '' }}</span></div>
          </button>
          <button v-if="more" class="w-full p-3 text-sm text-primary" :disabled="loading" @click="loadMessages(true)">Carregar mais</button>
        </aside>

        <article v-if="detail" class="min-w-0 space-y-5 p-5">
          <header class="space-y-2 border-b pb-4">
            <h3 class="break-words text-lg font-semibold">{{ detail.subject || '(Sem assunto)' }}</h3>
            <p class="break-all text-sm"><span class="text-muted-foreground">De:</span> {{ detail.from_name }} &lt;{{ detail.from_address }}&gt;</p>
            <p class="break-all text-sm"><span class="text-muted-foreground">Para:</span> {{ detail.to.join(', ') }}</p>
            <p v-if="detail.cc.length" class="break-all text-sm"><span class="text-muted-foreground">Cc:</span> {{ detail.cc.join(', ') }}</p>
            <p class="text-xs text-muted-foreground">{{ date(detail.received_at) }} · {{ detail.folder }}</p>
          </header>
          <pre class="max-h-[480px] whitespace-pre-wrap break-words overflow-y-auto font-sans text-sm leading-relaxed">{{ detail.text || '(Mensagem sem texto)' }}</pre>
          <div v-if="detail.attachments.length" class="flex flex-wrap gap-2">
            <a v-for="attachment in detail.attachments" :key="attachment.id" :href="url(`/api/mail/attachments/${attachment.id}`)" class="inline-flex max-w-full items-center gap-2 rounded border px-3 py-2 text-sm"><Download class="h-4 w-4 shrink-0" /><span class="truncate">{{ attachment.filename }}</span><span class="shrink-0 text-xs text-muted-foreground">{{ Math.ceil(attachment.size / 1024) }} KB</span></a>
          </div>
          <section v-if="detail.outbox.length" class="space-y-3 border-t pt-4" aria-label="Respostas desta mensagem">
            <div v-for="job in detail.outbox" :key="job.id" class="space-y-2 rounded bg-muted/40 p-3">
              <p class="text-xs font-medium">{{ statuses[job.status] || job.status }} · {{ date(job.created_at) }}</p>
              <p class="break-all text-xs text-muted-foreground">{{ job.from_address }} → {{ job.to }}</p>
              <p class="whitespace-pre-wrap break-words text-sm">{{ job.text }}</p>
            </div>
          </section>
          <form v-if="detail.reply.can_reply" class="space-y-3 border-t pt-4" @submit.prevent="sendReply">
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
  </section>
</template>
