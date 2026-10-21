<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { AlertTriangle, Camera, CameraOff, ExternalLink, Loader2, Power, RefreshCw, ShieldCheck, Smartphone } from 'lucide-vue-next'

definePageMeta({ middleware: ['admin'] })

type Phone = {
  name: string
  read_only?: boolean
  camera_test?: boolean
  running: boolean
  boot_completed: boolean
  desired: 'running' | 'stopped' | 'removed' | null
  sidecar_running: boolean | null
  network: {
    egress: 'proxy' | 'blocked' | 'direct' | 'unknown'
    proxy_configured: boolean
    proxy_tcp_reachable: boolean | null
    internet_confirmed: null
  }
  isolation: {
    celguard: boolean | null
    bridge_ipv6_off: boolean | null
    dns_proprio: boolean | null
  }
  memory_mb: number | null
  cpu_percent: number | null
  has_error: boolean
}
type PhoneList = { enabled: boolean; stream_enabled?: boolean; phones: Phone[] }
type Action = 'start' | 'stop' | 'restart'
type Session = { url: string; ticket: string }

const { api } = useApi()
const config = useRuntimeConfig()
const isAdmin = useIsAdmin()
const data = ref<PhoneList | null>(null)
const loading = ref(false)
const stale = ref(false)
const error = ref('')
const notice = ref('')
const lastUpdated = ref<Date | null>(null)
const activeAction = ref<{ name: string; action: Action | 'open' } | null>(null)
let poll: ReturnType<typeof setInterval> | undefined
let disposed = false
let sessionWindow: Window | null = null

const phones = computed(() => [...(data.value?.phones || [])].sort((a, b) => a.name.localeCompare(b.name, 'pt-BR', { numeric: true })))
const busy = computed(() => loading.value || !!activeAction.value)
const enabled = computed(() => isAdmin.value && data.value?.enabled === true)
const updatedLabel = computed(() => lastUpdated.value?.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', second: '2-digit' }))
const sessionEndpoint = computed(() => {
  const origin = String((config.public as Record<string, unknown>).celularesGatewayOrigin || '').trim()
  if (!origin) return null
  try {
    const parsed = new URL(origin)
    if (parsed.protocol !== 'https:' || parsed.username || parsed.password || parsed.pathname !== '/' || parsed.search || parsed.hash) return null
    return new URL('/session', parsed.origin).href
  } catch {
    return null
  }
})

function userError(cause: unknown, fallback: string) {
  const status = (cause as { statusCode?: number; status?: number })?.statusCode || (cause as { status?: number })?.status
  if (status === 401 || status === 403) return 'Seu acesso não foi autorizado. Entre novamente com uma conta de administrador.'
  if (status === 404) return 'Este celular não está disponível para gerenciamento pelo painel.'
  if (status === 429) return 'Há muitas solicitações. Aguarde um momento antes de tentar novamente.'
  if (status === 502 || status === 503 || status === 504) return 'Não foi possível consultar o serviço dos celulares. Tente atualizar em instantes.'
  return fallback
}

async function refresh(silent = false) {
  if (!isAdmin.value || loading.value || disposed) return
  loading.value = true
  if (!silent) error.value = ''
  try {
    const result = await api<PhoneList>('/api/celulares', { timeout: 15000, retry: 0 })
    if (disposed) return
    data.value = result
    lastUpdated.value = new Date()
    stale.value = false
    error.value = ''
  } catch (cause) {
    if (disposed) return
    stale.value = true
    error.value = userError(cause, 'Não foi possível atualizar os celulares. Os dados exibidos podem estar desatualizados.')
  } finally {
    loading.value = false
  }
}

function canAct() {
  return enabled.value && !stale.value && !busy.value
}

function phoneStatus(phone: Phone) {
  if (phone.has_error) return 'Requer verificação'
  if (phone.running) return phone.boot_completed ? 'Ligado' : 'Android iniciando'
  return 'Desligado'
}

function isolationReady(phone: Phone) {
  return phone.isolation?.celguard === true
    && phone.isolation?.bridge_ipv6_off === true
    && phone.isolation?.dns_proprio === true
    && phone.network.egress !== 'direct'
}

function isolationState(phone: Phone) {
  if (isCameraTest(phone)) return 'Ambiente de teste'
  const flags = [phone.isolation?.celguard, phone.isolation?.bridge_ipv6_off, phone.isolation?.dns_proprio]
  if (flags.some(value => value === false) || phone.network.egress === 'direct') return 'Requer verificação'
  if (isolationReady(phone)) return 'Proteções ativas'
  return 'Não verificado'
}

function networkLabel(phone: Phone) {
  if (isCameraTest(phone)) return 'Sem acesso à internet'
  return { proxy: 'Saída por proxy', blocked: 'Saída bloqueada', direct: 'Saída direta', unknown: 'Não verificada' }[phone.network.egress]
}

function proxyLabel(phone: Phone) {
  if (!phone.network.proxy_configured) return 'Não configurado'
  if (phone.network.proxy_tcp_reachable === true) return 'Responde à conexão TCP'
  if (phone.network.proxy_tcp_reachable === false) return 'Não respondeu à conexão TCP'
  return 'Conexão não verificada'
}

function isCameraTest(phone: Phone) {
  return phone.name === 'vrcamera' && phone.read_only === true && phone.camera_test === true && phone.network.egress === 'blocked'
}

function canOpen(phone: Phone) {
  const connectionReady = isCameraTest(phone) || (phone.name !== 'vrcamera' && !phone.read_only && isolationReady(phone) && phone.network.egress === 'proxy')
  return canAct() && data.value?.stream_enabled === true && !!sessionEndpoint.value && phone.running && phone.boot_completed && !phone.has_error && connectionReady
}

function openHint(phone: Phone) {
  if (data.value?.stream_enabled !== true || !sessionEndpoint.value) return 'Acesso remoto em preparação'
  if (stale.value) return 'Atualize o status para abrir o celular'
  if (phone.has_error) return 'O celular precisa de verificação'
  if (!phone.running) return phone.read_only ? 'Ambiente de teste indisponível' : 'Ligue o celular para abrir a tela'
  if (!phone.boot_completed) return 'Aguarde o Android terminar de iniciar'
  if (isCameraTest(phone)) return 'Abra, toque em Câmera e depois em Iniciar câmera'
  if (!isolationReady(phone)) return 'O isolamento precisa ser confirmado antes de abrir'
  if (phone.network.egress === 'blocked') return 'A saída de rede está bloqueada. Verifique a conexão pelo proxy'
  if (phone.network.egress !== 'proxy') return 'Confirme a saída de rede pelo proxy antes de abrir'
  return 'Abre em uma nova aba'
}

async function runAction(phone: Phone, action: Action) {
  if (phone.read_only || phone.name === 'vrcamera' || !canAct() || (action === 'start' ? phone.running : !phone.running)) return
  if (action !== 'start') {
    const verb = action === 'stop' ? 'Desligar' : 'Reiniciar'
    if (!window.confirm(`${verb} ${phone.name}?\n\nIsso interrompe os aplicativos e qualquer acesso remoto em andamento neste celular.`)) return
  }
  activeAction.value = { name: phone.name, action }
  error.value = ''
  notice.value = ''
  try {
    const result = await api<{ action: Action; phone: Phone }>(`/api/celulares/${encodeURIComponent(phone.name)}/${action}`, {
      method: 'POST', timeout: 30000, retry: 0,
    })
    if (disposed) return
    if (data.value) data.value.phones = data.value.phones.map(item => item.name === phone.name ? result.phone : item)
    notice.value = {
      start: `Solicitação para ligar ${phone.name} recebida. Aguarde o Android ficar pronto.`,
      stop: `Solicitação para desligar ${phone.name} recebida.`,
      restart: `Solicitação para reiniciar ${phone.name} recebida. Aguarde o Android ficar pronto.`,
    }[action]
    await refresh(true)
  } catch (cause) {
    if (!disposed) {
      stale.value = true
      error.value = userError(cause, 'Não foi possível confirmar a ação. Atualize o status antes de tentar novamente.')
    }
  } finally {
    activeAction.value = null
  }
}

async function openPhone(phone: Phone) {
  if (!canOpen(phone)) return
  // Open synchronously from the click; the one-use ticket is sent only in a POST body.
  const popup = window.open('about:blank', '_blank')
  if (!popup) {
    error.value = 'Permita abrir uma nova aba neste site para acessar a tela do celular.'
    return
  }
  sessionWindow = popup
  popup.opener = null
  const referrer = popup.document.createElement('meta')
  referrer.name = 'referrer'
  // Preserve the Origin used by the gateway to validate the POST, without sending a path.
  referrer.content = 'strict-origin'
  popup.document.head.appendChild(referrer)
  popup.document.title = 'Abrindo celular'
  popup.document.body.textContent = 'Preparando acesso ao celular…'
  activeAction.value = { name: phone.name, action: 'open' }
  error.value = ''
  notice.value = ''
  try {
    const result = await api<Session>(`/api/celulares/${encodeURIComponent(phone.name)}/session`, {
      method: 'POST', timeout: 15000, retry: 0,
    })
    if (disposed || popup.closed) {
      popup.close()
      return
    }
    // The trusted origin comes from deployment configuration, never from this response.
    if (!sessionEndpoint.value || result.url !== sessionEndpoint.value || typeof result.ticket !== 'string' || !result.ticket || result.ticket.length > 8192) {
      throw new Error('invalid_remote_session')
    }
    const form = popup.document.createElement('form')
    form.method = 'POST'
    form.action = sessionEndpoint.value
    form.target = '_self'
    const ticket = popup.document.createElement('input')
    ticket.type = 'hidden'
    ticket.name = 'ticket'
    ticket.value = result.ticket
    form.appendChild(ticket)
    popup.document.body.appendChild(form)
    form.submit()
    form.remove()
    sessionWindow = null
  } catch (cause) {
    popup.close()
    if (!disposed) error.value = userError(cause, 'Não foi possível abrir uma sessão segura. Atualize e tente novamente.')
  } finally {
    sessionWindow = null
    activeAction.value = null
  }
}

onMounted(() => {
  void refresh()
  poll = setInterval(() => {
    if (!document.hidden && enabled.value && !busy.value) void refresh(true)
  }, 15000)
})

onUnmounted(() => {
  disposed = true
  if (poll) clearInterval(poll)
  sessionWindow?.close()
})
</script>

<template>
  <div v-if="isAdmin" class="mx-auto w-full max-w-6xl space-y-5">
    <PageHeader title="Celulares" description="Acompanhe os celulares na nuvem e abra uma sessão quando precisar.">
      <template #actions>
        <Button variant="outline" size="sm" :disabled="busy" @click="refresh()">
          <RefreshCw class="mr-1.5 size-4" :class="{ 'animate-spin': loading }" />
          Atualizar
        </Button>
      </template>
    </PageHeader>

    <div class="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted-foreground">
      <span class="inline-flex items-center gap-1.5"><ShieldCheck class="size-3.5" /> Acesso de administradores</span>
      <span v-if="updatedLabel">Última consulta: {{ updatedLabel }}</span>
      <span v-if="stale" class="font-medium text-amber-700 dark:text-amber-400">Status desatualizado</span>
    </div>

    <div v-if="error" role="alert" class="flex items-start gap-2 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300">
      <AlertTriangle class="mt-0.5 size-4 shrink-0" />
      <span>{{ error }}</span>
    </div>
    <p v-if="notice" role="status" class="rounded-xl border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-800 dark:border-blue-900 dark:bg-blue-950/30 dark:text-blue-200">{{ notice }}</p>

    <div v-if="!data && loading" role="status" class="flex min-h-48 items-center justify-center gap-2 rounded-xl border bg-card text-sm text-muted-foreground">
      <Loader2 class="size-4 animate-spin" /> Consultando celulares…
    </div>

    <div v-else-if="data && !data.enabled" class="rounded-2xl border bg-card p-6 sm:p-8">
      <Smartphone class="mb-4 size-7 text-muted-foreground" />
      <h2 class="text-base font-semibold">Painel em preparação</h2>
      <p class="mt-2 max-w-xl text-sm text-muted-foreground">O serviço dos celulares ainda não foi habilitado neste ambiente. Os controles aparecerão quando a conexão estiver configurada.</p>
    </div>

    <div v-else-if="data && !phones.length" class="rounded-2xl border bg-card p-6 text-sm text-muted-foreground">Nenhum celular está liberado para este painel.</div>

    <div v-else-if="phones.length" class="grid min-w-0 gap-4 md:grid-cols-2 xl:grid-cols-3" :aria-busy="loading">
      <article v-for="phone in phones" :key="phone.name" class="flex min-w-0 flex-col overflow-hidden rounded-2xl border bg-card" :class="{ 'opacity-75': stale }">
        <div class="flex items-start gap-3 p-5 pb-4">
          <div class="flex size-10 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary"><Smartphone class="size-5" /></div>
          <div class="min-w-0 flex-1">
            <h2 class="break-words text-base font-semibold">{{ phone.name === 'vrcamera' ? 'Teste de câmera' : phone.name }}</h2>
            <span class="mt-1 inline-flex items-center gap-1.5 text-xs" :class="phone.has_error ? 'text-amber-700 dark:text-amber-400' : phone.running && phone.boot_completed ? 'text-emerald-700 dark:text-emerald-400' : 'text-muted-foreground'">
              <span class="size-1.5 rounded-full bg-current" />{{ phoneStatus(phone) }}
            </span>
          </div>
        </div>

        <dl class="space-y-3 px-5 text-sm">
          <div class="flex justify-between gap-3"><dt class="text-muted-foreground">Android</dt><dd class="text-right">{{ phone.running ? phone.boot_completed ? 'Pronto' : 'Inicializando' : 'Desligado' }}</dd></div>
          <div class="flex justify-between gap-3"><dt class="text-muted-foreground">Rede</dt><dd class="text-right" :class="{ 'text-amber-700 dark:text-amber-400': phone.network.egress === 'direct' }">{{ networkLabel(phone) }}</dd></div>
          <div class="flex justify-between gap-3"><dt class="text-muted-foreground">Isolamento</dt><dd class="text-right" :class="{ 'text-amber-700 dark:text-amber-400': isolationState(phone) === 'Requer verificação' }">{{ isolationState(phone) }}</dd></div>
        </dl>

        <p v-if="isCameraTest(phone)" class="mx-5 mt-4 rounded-lg bg-muted/40 px-3 py-3 text-sm text-muted-foreground">Confira a câmera do seu celular neste aparelho de teste, sem contas de loja.</p>
        <details v-else class="mx-5 mt-4 rounded-lg bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
          <summary class="cursor-pointer py-1 font-medium text-foreground">Detalhes da conexão</summary>
          <div class="space-y-2 pb-1 pt-3">
            <p>Proxy: {{ proxyLabel(phone) }}.</p>
            <p>Uma resposta TCP confirma apenas a conexão com o proxy. O acesso à internet dentro do Android ainda não foi confirmado por este painel.</p>
            <p v-if="phone.memory_mb !== null || phone.cpu_percent !== null" class="flex flex-wrap gap-x-3 gap-y-1">
              <span v-if="phone.memory_mb !== null">Memória: {{ Math.round(phone.memory_mb).toLocaleString('pt-BR') }} MB</span>
              <span v-if="phone.cpu_percent !== null">CPU: {{ phone.cpu_percent.toLocaleString('pt-BR', { maximumFractionDigits: 1 }) }}%</span>
            </p>
          </div>
        </details>

        <div class="mt-auto space-y-3 p-5">
          <Button class="w-full" :disabled="!canOpen(phone)" :title="openHint(phone)" @click="openPhone(phone)">
            <Loader2 v-if="activeAction?.name === phone.name && activeAction?.action === 'open'" class="mr-2 size-4 animate-spin" />
            <ExternalLink v-else class="mr-2 size-4" />
            {{ isCameraTest(phone) ? 'Abrir teste' : 'Abrir celular' }}
          </Button>
          <p class="text-center text-xs text-muted-foreground">{{ openHint(phone) }}</p>
          <div v-if="!phone.read_only && phone.name !== 'vrcamera'" class="grid grid-cols-2 gap-2">
            <Button variant="outline" size="sm" :disabled="!canAct()" @click="runAction(phone, phone.running ? 'stop' : 'start')">
              <Loader2 v-if="activeAction?.name === phone.name && ['start', 'stop'].includes(activeAction?.action || '')" class="mr-1.5 size-3.5 animate-spin" />
              <Power v-else class="mr-1.5 size-3.5" />
              {{ phone.running ? 'Desligar' : 'Ligar' }}
            </Button>
            <Button variant="outline" size="sm" :disabled="!canAct() || !phone.running" @click="runAction(phone, 'restart')">
              <RefreshCw class="mr-1.5 size-3.5" :class="{ 'animate-spin': activeAction?.name === phone.name && activeAction?.action === 'restart' }" />
              Reiniciar
            </Button>
          </div>
        </div>

        <div class="flex items-center gap-2 border-t bg-muted/20 px-5 py-3 text-xs text-muted-foreground"><Camera v-if="isCameraTest(phone)" class="size-3.5 shrink-0" /><CameraOff v-else class="size-3.5 shrink-0" /><span>{{ isCameraTest(phone) ? 'Câmera disponível para teste' : 'Câmera: ainda não disponível' }}</span></div>
      </article>
    </div>
  </div>
</template>
