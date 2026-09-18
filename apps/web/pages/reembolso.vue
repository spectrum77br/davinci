<script setup lang="ts">
import {
  AlertCircle,
  Check,
  ChevronLeft,
  ChevronRight,
  Download,
  Loader2,
  Plus,
  RotateCcw,
  Search,
  X,
} from 'lucide-vue-next'
import { isoToday } from '~/lib/date'

definePageMeta({ middleware: ['permission'], permission: { resource: 'reembolso', action: 'view' } })

// Sucata (10/09): reembolso automático da devolução em condição Sucata — mesma
// regra do Extraviado (prejuízo = custo do produto).
type RefundTipo = 'Logistica' | 'Cliente' | 'Manutenção' | 'Extraviado' | 'Sucata' | 'Frete'
const TIPO_OPTIONS: RefundTipo[] = ['Logistica', 'Cliente', 'Manutenção', 'Extraviado', 'Sucata', 'Frete']

type RefundRow = {
  id: string
  data: string | null
  pedido_bling: string | null
  pedido_marketplace: string | null
  plataforma: string | null
  conta: string
  tipo: RefundTipo | null
  prejuizo: number | null
  reembolso: number | null
  // Quando o valor do Reembolso foi lançado no DaVinci (carimbo do backend).
  reembolso_at: string | null
  chamado: string | null
  operacao: string | null
  conferido: boolean
  observacao: string | null
  situacao_bling: string | null
  created_at: string
  updated_at: string
}

type RefundPage = {
  items: RefundRow[]
  total: number
  limit: number
  offset: number
  platforms: string[]
  total_prejuizo: number
  total_reembolso: number
  total_a_conferir: number
}

type LookupRow = {
  data: string | null
  pedido_bling: string | null
  pedido_marketplace: string | null
  plataforma: string | null
  conta: string
  custo_produto: number | null
  custo_manutencao: number | null
}

type RefundExistente = {
  data: string | null
  conta: string | null
  tipo: string | null
  reembolso: number | null
  conferido: boolean
  criado_por: string | null
}

type LookupPage = {
  items: LookupRow[]
  historico_disponivel: boolean
  reembolsos_existentes?: number
  reembolsos_do_pedido?: RefundExistente[]
}

type RefundDraft = LookupRow & {
  tipo: RefundTipo | ''
  prejuizo: number | null
  reembolso: number | null
  chamado: string
  operacao: string
  observacao: string
}

type ConferidoFilter = 'all' | 'true' | 'false'

const PAGE_SIZE = 100

const { api } = useApi()
const canEdit = useCan('reembolso', 'edit')

// Coluna "Situação Bling" restrita a estes usuários.
const _auth = useAuthStore()
const SITUACAO_BLING_USERS = [
  'spectrum77@tuta.com',
  'joffer4@tuta.com',
  'maconer06@tuta.com',
]
const canSeeSituacaoBling = computed(() => {
  const email = _auth.user?.email?.toLowerCase()
  return !!email && SITUACAO_BLING_USERS.includes(email)
})

// Botão "exportar xlsx" restrito a estes usuários.
const EXPORT_XLSX_USERS = [
  'spectrum77@tuta.com',
  'maconer06@tuta.com',
]
const canExportXlsx = computed(() => {
  const email = _auth.user?.email?.toLowerCase()
  return !!email && EXPORT_XLSX_USERS.includes(email)
})

const items = ref<RefundRow[]>([])
const total = ref(0)
const platforms = ref<string[]>([])
const page = ref(1)
const loading = ref(false)
const error = ref<string | null>(null)

const search = ref('')
const platform = ref<'all' | string>('all')
const tipoFilter = ref<'all' | RefundTipo>('all')
const conferidoFilter = ref<ConferidoFilter>('false')
// Filtro por data do conferido (Refund.conferido_at), só para admins.
const isAdmin = useIsAdmin()
const dataInicio = ref('')
const dataFim = ref('')

const addOpen = ref(false)
const lookupPedido = ref('')
const lookupLoading = ref(false)
const lookupResults = ref<LookupRow[]>([])
const lookupError = ref<string | null>(null)
const historicoDisponivel = ref(false)
// O que já existe para o pedido consultado. Serve de freio ao relançamento: a
// lista da tela é filtrada por equipe E por "a finalizar", então um lançamento
// que já existe pode não estar à vista de quem está prestes a repeti-lo.
const reembolsosExistentes = ref(0)
const reembolsosDoPedido = ref<RefundExistente[]>([])
// Confirmação do que acabou de ser salvo. A lista é ordenada pela DATA DO
// PEDIDO, não pela de criação, então um lançamento de pedido antigo nasce fora
// da primeira página: sem essa confirmação a pessoa continua achando que não
// salvou, que é a origem de todo o problema.
const salvoAviso = ref<{ pedido: string | null; conta: string | null } | null>(null)
const historicoLoading = ref(false)
const historicoElapsedMs = ref(0)
const creating = ref(false)
const draft = ref<RefundDraft | null>(null)

const rowSaveQueue = new Map<string, Promise<void>>()

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))
const rangeStart = computed(() => total.value === 0 ? 0 : (page.value - 1) * PAGE_SIZE + 1)
const rangeEnd = computed(() => Math.min(page.value * PAGE_SIZE, total.value))
// Totais do conjunto filtrado INTEIRO, vindos do servidor (não só a página
// carregada) — batem com a linha Reembolso do quadro Operacional.
const totalPrejuizo = ref(0)
const totalReembolso = ref(0)
const totalAConferir = ref(0)

const sheetInputClass = 'h-7 w-full rounded-none border-0 bg-transparent px-1 text-xs focus:bg-background focus:outline-none focus:ring-1 focus:ring-primary disabled:cursor-default disabled:opacity-70'
const sheetSelectClass = `${sheetInputClass} cursor-pointer`
const sheetMoneyInputClass = `${sheetInputClass} text-right tabular-nums [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:m-0 [&::-webkit-inner-spin-button]:appearance-none`

function apiError(e: any) {
  const detail = e?.data?.detail
  if (detail && typeof detail === 'object') return detail.message || detail.code || e?.message || 'erro'
  return detail || e?.message || 'erro'
}

function brl(v: number | null | undefined) {
  if (v == null) return '—'
  return v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
}

function fmtDateTime(v: string | null) {
  if (!v) return '—'
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return v
  return d.toLocaleString('pt-BR', {
    day: '2-digit',
    month: '2-digit',
    year: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function numberOrNull(value: string) {
  if (value === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function clampReembolsoForCliente(row: RefundRow) {
  if (row.tipo === 'Cliente' && row.reembolso != null && row.reembolso > 0) {
    row.reembolso = -row.reembolso
  }
}

function setRowReembolso(row: RefundRow, value: string) {
  const parsed = numberOrNull(value)
  if (parsed != null && row.tipo === 'Cliente' && parsed > 0) {
    row.reembolso = -parsed
  } else {
    row.reembolso = parsed
  }
}

function setRowText(row: RefundRow, field: 'chamado' | 'operacao' | 'observacao', value: string) {
  row[field] = value || null
}

async function fetchOrderCost(pedido_bling: string | null, conta: string | null): Promise<number | null> {
  if (!pedido_bling || !conta) return null
  try {
    const params = new URLSearchParams({ pedido_bling, conta })
    const res = await api<{ custo_produto: number | null }>(`/api/refunds/order-cost?${params.toString()}`)
    return res.custo_produto ?? null
  } catch (e: any) {
    error.value = apiError(e)
    return null
  }
}

async function setRowTipo(row: RefundRow, value: string) {
  const next = (value || null) as RefundTipo | null
  row.tipo = next
  if (next === 'Cliente') clampReembolsoForCliente(row)
  if (next === 'Extraviado' || next === 'Sucata') {
    const cost = await fetchOrderCost(row.pedido_bling, row.conta)
    if (cost != null && row.tipo === next) {
      row.prejuizo = cost
    }
  }
  await saveRow(row)
}

function onDraftTipoChange() {
  if (!draft.value) return
  if ((draft.value.tipo === 'Extraviado' || draft.value.tipo === 'Sucata') && draft.value.custo_produto != null) {
    draft.value.prejuizo = draft.value.custo_produto
  }
  if (draft.value.tipo === 'Manutenção' && draft.value.custo_manutencao != null) {
    draft.value.prejuizo = draft.value.custo_manutencao
  }
  if (draft.value.tipo === 'Cliente' && draft.value.reembolso != null && draft.value.reembolso > 0) {
    draft.value.reembolso = -draft.value.reembolso
  }
}

function setDraftReembolso(value: string) {
  if (!draft.value) return
  const parsed = numberOrNull(value)
  if (parsed != null && draft.value.tipo === 'Cliente' && parsed > 0) {
    draft.value.reembolso = -parsed
  } else {
    draft.value.reembolso = parsed
  }
}

async function setRowConferido(row: RefundRow, value: boolean) {
  row.conferido = value
  await saveRow(row)
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const params = new URLSearchParams()
    params.set('limit', String(PAGE_SIZE))
    params.set('offset', String((page.value - 1) * PAGE_SIZE))
    if (search.value.trim()) params.set('search', search.value.trim())
    if (platform.value !== 'all') params.set('platform', platform.value)
    if (tipoFilter.value !== 'all') params.set('tipo', tipoFilter.value)
    if (conferidoFilter.value !== 'all') params.set('conferido', conferidoFilter.value)
    if (dataInicio.value) params.set('data_inicio', dataInicio.value)
    if (dataFim.value) params.set('data_fim', dataFim.value)
    const res = await api<RefundPage>(`/api/refunds?${params.toString()}`)
    items.value = res.items
    total.value = res.total
    platforms.value = res.platforms
    totalPrejuizo.value = res.total_prejuizo
    totalReembolso.value = res.total_reembolso
    totalAConferir.value = res.total_a_conferir
  } catch (e: any) {
    error.value = apiError(e)
  } finally {
    loading.value = false
  }
}

const exporting = ref(false)

async function exportXlsx() {
  if (exporting.value) return
  exporting.value = true
  error.value = null
  try {
    const params = new URLSearchParams()
    if (search.value.trim()) params.set('search', search.value.trim())
    if (platform.value !== 'all') params.set('platform', platform.value)
    if (tipoFilter.value !== 'all') params.set('tipo', tipoFilter.value)
    if (conferidoFilter.value !== 'all') params.set('conferido', conferidoFilter.value)
    if (dataInicio.value) params.set('data_inicio', dataInicio.value)
    if (dataFim.value) params.set('data_fim', dataFim.value)
    const blob = await api<Blob>(`/api/refunds/export.xlsx?${params.toString()}`, { responseType: 'blob' as any })
    const href = URL.createObjectURL(blob as any)
    const a = document.createElement('a')
    a.href = href
    a.download = `reembolsos_${isoToday()}.xlsx`
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(href)
  } catch (e: any) {
    error.value = apiError(e)
  } finally {
    exporting.value = false
  }
}

await load()

let searchTimer: ReturnType<typeof setTimeout> | null = null
watch(search, () => {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    page.value = 1
    load()
  }, 300)
})
watch([platform, tipoFilter, conferidoFilter, dataInicio, dataFim], () => {
  page.value = 1
  load()
})
watch(page, () => load())

function verSalvoNaLista() {
  const pedido = salvoAviso.value?.pedido
  if (!pedido) return
  search.value = pedido
  page.value = 1
  salvoAviso.value = null
  load()
}

function openAdd() {
  salvoAviso.value = null
  addOpen.value = true
  lookupPedido.value = ''
  lookupResults.value = []
  lookupError.value = null
  historicoDisponivel.value = false
  reembolsosExistentes.value = 0
  reembolsosDoPedido.value = []
  historicoLoading.value = false
  historicoElapsedMs.value = 0
  draft.value = null
}

function closeAdd() {
  addOpen.value = false
  lookupPedido.value = ''
  lookupResults.value = []
  lookupError.value = null
  historicoDisponivel.value = false
  reembolsosExistentes.value = 0
  reembolsosDoPedido.value = []
  historicoLoading.value = false
  historicoElapsedMs.value = 0
  draft.value = null
}

function selectLookup(row: LookupRow) {
  draft.value = {
    ...row,
    tipo: '',
    prejuizo: null,
    reembolso: null,
    chamado: '',
    operacao: '',
    observacao: '',
  }
}

function fmtElapsed(ms: number) {
  const s = Math.floor(ms / 1000)
  const m = Math.floor(s / 60)
  const rem = s % 60
  return m > 0 ? `${m}m ${String(rem).padStart(2, '0')}s` : `${s}s`
}

async function lookupOrder(forceRefresh = false) {
  const pedido = lookupPedido.value.trim()
  // Os resets vêm ANTES da saída por campo vazio: senão o aviso e o rascunho do
  // pedido anterior ficam na tela com a busca já apagada.
  lookupError.value = null
  lookupResults.value = []
  historicoDisponivel.value = false
  reembolsosExistentes.value = 0
  reembolsosDoPedido.value = []
  draft.value = null
  if (!pedido) return
  let timerHandle: ReturnType<typeof setInterval> | null = null
  if (forceRefresh) {
    historicoLoading.value = true
    historicoElapsedMs.value = 0
    const started = Date.now()
    timerHandle = setInterval(() => {
      historicoElapsedMs.value = Date.now() - started
    }, 250)
  } else {
    lookupLoading.value = true
  }
  try {
    const params = new URLSearchParams({ pedido })
    if (forceRefresh) params.set('force_refresh', 'true')
    const res = await api<LookupPage>(`/api/refunds/order-lookup?${params.toString()}`)
    lookupResults.value = res.items
    historicoDisponivel.value = !!res.historico_disponivel
    reembolsosExistentes.value = res.reembolsos_existentes ?? 0
    reembolsosDoPedido.value = res.reembolsos_do_pedido ?? []
    if (res.items.length === 1) selectLookup(res.items[0])
    if (!res.items.length && !historicoDisponivel.value) {
      lookupError.value = forceRefresh ? 'pedido não encontrado no histórico' : 'pedido não encontrado'
    }
  } catch (e: any) {
    lookupError.value = apiError(e)
  } finally {
    if (timerHandle) clearInterval(timerHandle)
    lookupLoading.value = false
    historicoLoading.value = false
  }
}

function lookupHistorico() {
  lookupOrder(true)
}

function draftPayload() {
  if (!draft.value) return null
  return {
    data: draft.value.data,
    pedido_bling: draft.value.pedido_bling,
    pedido_marketplace: draft.value.pedido_marketplace,
    plataforma: draft.value.plataforma,
    conta: draft.value.conta,
    tipo: draft.value.tipo || null,
    // Coage via numberOrNull: o input de prejuízo usa v-model.number, que guarda
    // '' (string vazia) quando o campo é editado e limpo. '' renderiza como campo
    // vazio mas serializa como prejuizo:'' → o backend rejeita (422). Aqui '' / NaN
    // viram null pros dois campos numéricos no ponto de serialização.
    prejuizo: numberOrNull(String(draft.value.prejuizo ?? '')),
    reembolso: numberOrNull(String(draft.value.reembolso ?? '')),
    chamado: draft.value.chamado || null,
    operacao: draft.value.operacao || null,
    observacao: draft.value.observacao || null,
  }
}

async function createRefund() {
  const body = draftPayload()
  if (!body || !canEdit.value) return
  if (!body.tipo) {
    lookupError.value = 'Selecione o tipo antes de adicionar o pedido.'
    return
  }
  creating.value = true
  lookupError.value = null
  try {
    const created = await api<RefundRow>('/api/refunds', { method: 'POST', body })
    if (page.value === 1) items.value = [created, ...items.value].slice(0, PAGE_SIZE)
    total.value += 1
    // A lista é ordenada pela DATA DO PEDIDO: um lançamento de pedido antigo
    // não fica no topo no próximo carregamento, e some da primeira página. A
    // confirmação abaixo fica na tela com um atalho para achar a linha, em vez
    // de deixar a pessoa concluir que não salvou.
    salvoAviso.value = { pedido: created.pedido_bling, conta: created.conta }
    closeAdd()
  } catch (e: any) {
    lookupError.value = apiError(e)
  } finally {
    creating.value = false
  }
}

function rowPatchPayload(row: RefundRow) {
  return {
    tipo: row.tipo || null,
    prejuizo: row.prejuizo,
    reembolso: row.reembolso,
    chamado: row.chamado || null,
    operacao: row.operacao || null,
    conferido: row.conferido,
    observacao: row.observacao || null,
  }
}

async function saveRow(row: RefundRow): Promise<void> {
  if (!canEdit.value) return
  const id = row.id
  const prev = rowSaveQueue.get(id) ?? Promise.resolve()
  const next = prev
    .catch(() => undefined)
    .then(async () => {
      try {
        const saved = await api<RefundRow>(`/api/refunds/${encodeURIComponent(id)}`, {
          method: 'PATCH',
          body: rowPatchPayload(row),
        })
        // O carimbo "Reembolso em" nasce no backend: reflete sem recarregar.
        row.reembolso_at = saved.reembolso_at
        error.value = null
      } catch (e: any) {
        error.value = apiError(e)
      }
    })
    .finally(() => {
      if (rowSaveQueue.get(id) === next) rowSaveQueue.delete(id)
    })
  rowSaveQueue.set(id, next)
  await next
}
</script>

<template>
  <div class="space-y-5">
    <PageHeader title="Reembolso" description="Controle de reembolsos por pedido da conciliação marketplace.">
      <template #actions>
        <Button v-if="canExportXlsx" size="sm" variant="outline" :disabled="exporting" @click="exportXlsx">
          <Download class="size-4 mr-1.5" :class="{ 'animate-pulse': exporting }" />
          exportar xlsx
        </Button>
        <Button size="sm" variant="outline" :disabled="loading" @click="load">
          <RotateCcw class="size-4 mr-1.5" :class="{ 'animate-spin': loading }" />
          atualizar
        </Button>
        <Button size="sm" :disabled="!canEdit" @click="openAdd">
          <Plus class="size-4 mr-1.5" />
          adicionar pedido
        </Button>
      </template>
    </PageHeader>

    <div v-if="error" class="flex items-center gap-2 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-400">
      <AlertCircle class="size-4" />
      {{ error }}
    </div>

    <div
      v-if="salvoAviso"
      role="status"
      class="flex flex-wrap items-center gap-2 rounded-md border border-emerald-500/40 bg-emerald-500/10 px-4 py-3"
    >
      <Check class="size-5 shrink-0 text-emerald-600 dark:text-emerald-400" />
      <span class="text-sm">
        Reembolso salvo
        <template v-if="salvoAviso.pedido">
          para o pedido <span class="font-mono">{{ salvoAviso.pedido }}</span>
        </template>
        <template v-if="salvoAviso.conta"> ({{ salvoAviso.conta }})</template>.
        A lista é ordenada pela data do pedido, então ele pode não estar nesta página.
      </span>
      <Button v-if="salvoAviso.pedido" size="sm" variant="outline" @click="verSalvoNaLista">
        ver na lista
      </Button>
      <Button size="sm" variant="ghost" @click="salvoAviso = null">fechar</Button>
    </div>

    <div v-if="addOpen" class="rounded-md border bg-background">
      <div class="flex flex-wrap items-end gap-3 border-b px-3 py-3">
        <label class="space-y-1">
          <span class="text-[11px] font-medium text-muted-foreground">Pedido</span>
          <div class="relative">
            <Search class="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
            <input
              v-model="lookupPedido"
              class="h-9 w-72 rounded-md border bg-background pl-8 pr-3 text-sm"
              placeholder="Bling ou marketplace"
              :disabled="historicoLoading"
              @keydown.enter.prevent="lookupOrder(false)"
            />
          </div>
        </label>
        <Button size="sm" :disabled="lookupLoading || historicoLoading || !lookupPedido.trim()" @click="lookupOrder(false)">
          <Loader2 v-if="lookupLoading" class="size-4 mr-1.5 animate-spin" />
          <Search v-else class="size-4 mr-1.5" />
          buscar
        </Button>
        <Button size="sm" variant="ghost" :disabled="historicoLoading" @click="closeAdd">
          <X class="size-4 mr-1.5" />
          fechar
        </Button>
        <span v-if="lookupError" class="text-sm text-red-400">{{ lookupError }}</span>
      </div>

      <div
        v-if="historicoLoading"
        class="border-b border-amber-500/30 bg-amber-500/5 px-4 py-4"
      >
        <div class="flex items-start gap-3">
          <Loader2 class="mt-0.5 size-5 shrink-0 animate-spin text-amber-500" />
          <div class="flex-1">
            <div class="text-sm font-medium text-amber-700 dark:text-amber-300">
              Buscando pedido <span class="font-mono">{{ lookupPedido.trim() }}</span> no historico…
            </div>
            <p class="mt-1 text-xs text-muted-foreground">
              A view de conciliacao precisa materializar todos os pedidos antes
              de filtrar, entao essa busca pode levar varios minutos. Pode deixar
              essa aba aberta; o resultado aparece assim que terminar.
            </p>
            <div class="mt-2 text-xs font-mono tabular-nums text-amber-700 dark:text-amber-300">
              {{ fmtElapsed(historicoElapsedMs) }} decorrido
            </div>
          </div>
        </div>
      </div>

      <div
        v-else-if="historicoDisponivel && !lookupResults.length && !draft"
        class="border-b border-blue-500/30 bg-blue-500/5 px-4 py-4"
      >
        <div class="flex items-start gap-3">
          <AlertCircle class="mt-0.5 size-5 shrink-0 text-blue-500" />
          <div class="flex-1">
            <div class="text-sm font-medium">
              Pedido <span class="font-mono">{{ lookupPedido.trim() }}</span> nao esta no historico recente
            </div>
            <p class="mt-1 text-xs text-muted-foreground">
              Ele existe no Bling, mas esta fora da janela de 20 dias da
              conciliacao rapida. A busca no historico le a view completa e
              pode levar varios minutos.
            </p>
            <Button size="sm" class="mt-3" @click="lookupHistorico">
              <Search class="size-4 mr-1.5" />
              buscar no historico
            </Button>
          </div>
        </div>
      </div>

      <div
        v-if="reembolsosExistentes > 0"
        role="alert"
        class="border-b border-amber-500/40 bg-amber-500/10 px-4 py-3"
      >
        <div class="flex items-start gap-3">
          <AlertCircle class="mt-0.5 size-5 shrink-0 text-amber-500" />
          <div class="flex-1">
            <div class="text-sm font-medium text-amber-700 dark:text-amber-300">
              Este pedido já tem
              {{ reembolsosExistentes }}
              {{ reembolsosExistentes === 1 ? 'reembolso lançado' : 'reembolsos lançados' }}
            </div>
            <ul class="mt-2 space-y-1">
              <li
                v-for="(r, i) in reembolsosDoPedido"
                :key="`ja-${i}`"
                class="text-xs tabular-nums"
              >
                <span class="text-muted-foreground">{{ fmtDateTime(r.data) }}</span>
                <span class="mx-1.5">·</span>
                <span>{{ r.conta || '—' }}</span>
                <span class="mx-1.5">·</span>
                <span>{{ r.tipo || 'sem tipo' }}</span>
                <span class="mx-1.5">·</span>
                <span class="font-medium">{{ brl(r.reembolso) }}</span>
                <span v-if="r.criado_por" class="text-muted-foreground"> · por {{ r.criado_por }}</span>
                <span v-if="r.conferido" class="text-muted-foreground"> · finalizado</span>
              </li>
            </ul>
            <p class="mt-2 text-xs text-muted-foreground">
              Um pedido pode ter mais de um reembolso legítimo, de tipos ou
              contas diferentes. Confira a lista acima antes de adicionar.
            </p>
          </div>
        </div>
      </div>

      <div v-if="lookupResults.length > 1 && !draft" class="overflow-auto border-b">
        <table class="w-full text-xs border-collapse">
          <thead class="bg-background">
            <tr>
              <th class="px-2 py-1 text-left text-[11px] font-semibold border-b" colspan="5">Identificação</th>
              <th class="px-2 py-1 text-right text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600" colspan="1">Ação</th>
            </tr>
            <tr>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px]">Data</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[120px]">Pedido Bling</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[155px]">Pedido Marketplace</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[105px]">Plataforma</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[155px]">Conta</th>
              <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[90px] border-l-[3px] border-gray-400 dark:border-gray-600"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in lookupResults" :key="`${row.pedido_bling}-${row.pedido_marketplace}-${row.conta}`" class="border-t hover:brightness-95 dark:hover:brightness-110">
              <td class="px-2 py-1 whitespace-nowrap text-muted-foreground">{{ fmtDateTime(row.data) }}</td>
              <td class="px-2 py-1 font-mono">{{ row.pedido_bling || '—' }}</td>
              <td class="px-2 py-1 font-mono text-muted-foreground">{{ row.pedido_marketplace || '—' }}</td>
              <td class="px-2 py-1 uppercase">{{ row.plataforma || '—' }}</td>
              <td class="px-2 py-1">{{ row.conta }}</td>
              <td class="px-2 py-1 text-right border-l-[3px] border-gray-400 dark:border-gray-600">
                <Button size="sm" variant="outline" @click="selectLookup(row)">usar</Button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="draft" class="overflow-auto">
        <table class="min-w-[1380px] w-full text-xs border-collapse">
          <thead class="bg-background">
            <tr>
              <th class="px-2 py-1 text-left text-[11px] font-semibold border-b" colspan="5">Identificação</th>
              <th class="px-2 py-1 text-center text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-amber-50 dark:bg-amber-900/20" colspan="5">Reembolso</th>
              <th class="px-2 py-1 text-left text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-emerald-50 dark:bg-emerald-900/20" colspan="1">Observação</th>
              <th class="px-2 py-1 text-right text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600" colspan="1">Ação</th>
            </tr>
            <tr>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px]">Data</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[120px]">Pedido Bling</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[155px]">Pedido Marketplace</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[105px]">Plataforma</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[155px]">Conta</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[145px] bg-amber-50 dark:bg-amber-900/20 border-l-[3px] border-gray-400 dark:border-gray-600">Tipo</th>
              <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px] bg-amber-50 dark:bg-amber-900/20">Prejuízo</th>
              <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px] bg-amber-50 dark:bg-amber-900/20">Reembolso</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[145px] bg-amber-50 dark:bg-amber-900/20">Chamado</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[155px] bg-amber-50 dark:bg-amber-900/20">Operação</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[240px] bg-emerald-50 dark:bg-emerald-900/20 border-l-[3px] border-gray-400 dark:border-gray-600">Observação</th>
              <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[120px] border-l-[3px] border-gray-400 dark:border-gray-600"></th>
            </tr>
          </thead>
          <tbody>
            <tr class="border-t">
              <td class="px-2 py-1 whitespace-nowrap text-muted-foreground">{{ fmtDateTime(draft.data) }}</td>
              <td class="px-2 py-1 font-mono">{{ draft.pedido_bling || '—' }}</td>
              <td class="px-2 py-1 font-mono text-muted-foreground">{{ draft.pedido_marketplace || '—' }}</td>
              <td class="px-2 py-1 uppercase">{{ draft.plataforma || '—' }}</td>
              <td class="px-2 py-1">{{ draft.conta }}</td>
              <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10 border-l-[3px] border-gray-400 dark:border-gray-600">
                <select v-model="draft.tipo" :class="[sheetSelectClass, !draft.tipo ? 'ring-1 ring-red-500/60' : '']" @change="onDraftTipoChange">
                  <option value="">— obrigatório</option>
                  <option v-for="tipo in TIPO_OPTIONS" :key="tipo" :value="tipo">{{ tipo }}</option>
                </select>
              </td>
              <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
                <input v-model.number="draft.prejuizo" type="number" step="0.01" :class="sheetMoneyInputClass" />
              </td>
              <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
                <input
                  :value="draft.reembolso ?? ''"
                  type="number"
                  step="0.01"
                  :max="draft.tipo === 'Cliente' ? 0 : undefined"
                  :class="sheetMoneyInputClass"
                  @input="(e) => setDraftReembolso((e.target as HTMLInputElement).value)"
                />
              </td>
              <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
                <input v-model="draft.chamado" :class="sheetInputClass" />
              </td>
              <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
                <input v-model="draft.operacao" :class="sheetInputClass" />
              </td>
              <td class="px-1 py-0.5 bg-emerald-50/40 dark:bg-emerald-900/10 border-l-[3px] border-gray-400 dark:border-gray-600">
                <input v-model="draft.observacao" :class="sheetInputClass" />
              </td>
              <td class="px-2 py-1 text-right border-l-[3px] border-gray-400 dark:border-gray-600">
                <Button size="sm" :disabled="creating || !canEdit || !draft.tipo" @click="createRefund">
                  <Loader2 v-if="creating" class="size-4 mr-1.5 animate-spin" />
                  <Plus v-else class="size-4 mr-1.5" />
                  adicionar
                </Button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="flex flex-wrap items-center gap-2">
      <div class="relative">
        <Search class="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
        <input
          v-model="search"
          class="h-9 w-72 rounded-md border bg-background pl-8 pr-3 text-sm"
          placeholder="buscar pedido, conta, chamado…"
        />
      </div>
      <select v-model="platform" class="h-9 rounded-md border bg-background px-2 text-sm">
        <option value="all">todas plataformas</option>
        <option v-for="p in platforms" :key="p" :value="p">{{ p }}</option>
      </select>
      <select v-model="tipoFilter" class="h-9 rounded-md border bg-background px-2 text-sm">
        <option value="all">todos tipos</option>
        <option v-for="tipo in TIPO_OPTIONS" :key="tipo" :value="tipo">{{ tipo }}</option>
      </select>
      <select v-model="conferidoFilter" class="h-9 rounded-md border bg-background px-2 text-sm">
        <option value="false">a finalizar</option>
        <option value="true">finalizados</option>
        <option value="all">todos</option>
      </select>
      <div v-if="isAdmin" class="flex items-center gap-1.5">
        <span class="text-xs text-muted-foreground">finalizado em</span>
        <input
          v-model="dataInicio"
          type="date"
          class="h-9 rounded-md border bg-background px-2 text-sm"
          title="Data do finalizado — início"
        />
        <span class="text-xs text-muted-foreground">até</span>
        <input
          v-model="dataFim"
          type="date"
          class="h-9 rounded-md border bg-background px-2 text-sm"
          title="Data do finalizado — fim"
        />
        <Button
          v-if="dataInicio || dataFim"
          size="sm"
          variant="ghost"
          title="limpar datas"
          @click="dataInicio = ''; dataFim = ''"
        >
          <X class="size-4" />
        </Button>
      </div>
      <span class="ml-auto text-xs text-muted-foreground">
        {{ rangeStart }}–{{ rangeEnd }} de {{ total }} · a conferir {{ totalAConferir }} · prejuízo {{ brl(totalPrejuizo) }} · reembolso {{ brl(totalReembolso) }}
      </span>
    </div>

    <div class="overflow-auto rounded border max-h-[75vh] focus:outline-none" tabindex="0">
      <table class="min-w-[1440px] text-xs border-collapse">
        <thead class="sticky top-0 z-20 bg-background">
          <tr>
            <th class="px-2 py-1 text-left text-[11px] font-semibold border-b" :colspan="canSeeSituacaoBling ? 6 : 5">Identificação</th>
            <th class="px-2 py-1 text-center text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-amber-50 dark:bg-amber-900/20" colspan="6">Reembolso</th>
            <th class="px-2 py-1 text-center text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-emerald-50 dark:bg-emerald-900/20" colspan="2">Conferência</th>
          </tr>
          <tr class="border-b">
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[115px]">Data</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[120px]">Pedido Bling</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[155px]">Pedido Marketplace</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[105px]">Plataforma</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[155px]">Conta</th>
            <th v-if="canSeeSituacaoBling" class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[140px]">Situação Bling</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[145px] bg-amber-50 dark:bg-amber-900/20 border-l-[3px] border-gray-400 dark:border-gray-600">Tipo</th>
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px] bg-amber-50 dark:bg-amber-900/20">Prejuízo</th>
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px] bg-amber-50 dark:bg-amber-900/20">Reembolso</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[150px] bg-amber-50 dark:bg-amber-900/20">Chamado</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[155px] bg-amber-50 dark:bg-amber-900/20">Operação</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[115px] bg-amber-50 dark:bg-amber-900/20" title="Quando o valor do Reembolso foi lançado no DaVinci">Data</th>
            <th class="px-2 py-1 text-center font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[90px] bg-emerald-50 dark:bg-emerald-900/20 border-l-[3px] border-gray-400 dark:border-gray-600">Finalizado</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[260px] bg-emerald-50 dark:bg-emerald-900/20">Observação</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading && !items.length">
            <td :colspan="canSeeSituacaoBling ? 14 : 13" class="py-8 text-center text-muted-foreground">
              <Loader2 class="size-4 inline animate-spin mr-1.5" />
              carregando…
            </td>
          </tr>
          <tr v-else-if="!items.length">
            <td :colspan="canSeeSituacaoBling ? 14 : 13" class="py-8 text-center text-muted-foreground">sem registros</td>
          </tr>
          <tr v-for="row in items" :key="row.id" class="border-t hover:brightness-95 dark:hover:brightness-110">
            <td class="px-2 py-1 whitespace-nowrap text-muted-foreground">{{ fmtDateTime(row.data) }}</td>
            <td class="px-2 py-1 font-mono whitespace-nowrap">{{ row.pedido_bling || '—' }}</td>
            <td class="px-2 py-1 font-mono text-muted-foreground whitespace-nowrap">{{ row.pedido_marketplace || '—' }}</td>
            <td class="px-2 py-1 uppercase whitespace-nowrap">{{ row.plataforma || '—' }}</td>
            <td class="px-2 py-1 whitespace-nowrap">{{ row.conta }}</td>
            <td v-if="canSeeSituacaoBling" class="px-2 py-1 whitespace-nowrap text-muted-foreground">{{ row.situacao_bling || '—' }}</td>
            <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10 border-l-[3px] border-gray-400 dark:border-gray-600">
              <select
                :value="row.tipo || ''"
                :disabled="!canEdit"
                :class="sheetSelectClass"
                @change="(e) => setRowTipo(row, (e.target as HTMLSelectElement).value)"
              >
                <option value="">—</option>
                <option v-for="tipo in TIPO_OPTIONS" :key="tipo" :value="tipo">{{ tipo }}</option>
              </select>
            </td>
            <td class="px-2 py-1 text-right tabular-nums bg-amber-50/40 dark:bg-amber-900/10 text-muted-foreground">
              {{ row.prejuizo ?? '—' }}
            </td>
            <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
              <input
                :value="row.reembolso ?? ''"
                :disabled="!canEdit"
                type="number"
                step="0.01"
                :max="row.tipo === 'Cliente' ? 0 : undefined"
                :class="sheetMoneyInputClass"
                @input="(e) => setRowReembolso(row, (e.target as HTMLInputElement).value)"
                @change="saveRow(row)"
              />
            </td>
            <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
              <input
                :value="row.chamado || ''"
                :disabled="!canEdit"
                :class="sheetInputClass"
                @input="(e) => setRowText(row, 'chamado', (e.target as HTMLInputElement).value)"
                @change="saveRow(row)"
              />
            </td>
            <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
              <input
                :value="row.operacao || ''"
                :disabled="!canEdit"
                :class="sheetInputClass"
                @input="(e) => setRowText(row, 'operacao', (e.target as HTMLInputElement).value)"
                @change="saveRow(row)"
              />
            </td>
            <td class="px-2 py-1 whitespace-nowrap bg-amber-50/40 dark:bg-amber-900/10 text-muted-foreground">
              {{ fmtDateTime(row.reembolso_at) }}
            </td>
            <td class="px-2 py-1 text-center bg-emerald-50/40 dark:bg-emerald-900/10 border-l-[3px] border-gray-400 dark:border-gray-600">
              <input
                :checked="row.conferido"
                :disabled="!canEdit"
                type="checkbox"
                class="size-4 rounded border accent-primary disabled:cursor-default disabled:opacity-70"
                @change="(e) => setRowConferido(row, (e.target as HTMLInputElement).checked)"
              />
            </td>
            <td class="px-1 py-0.5 bg-emerald-50/40 dark:bg-emerald-900/10">
              <input
                :value="row.observacao || ''"
                :disabled="!canEdit"
                :class="sheetInputClass"
                @input="(e) => setRowText(row, 'observacao', (e.target as HTMLInputElement).value)"
                @change="saveRow(row)"
              />
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="total > PAGE_SIZE" class="flex items-center justify-between gap-2">
      <span class="text-xs text-muted-foreground">
        página {{ page }} de {{ totalPages }} · {{ PAGE_SIZE }}/página
      </span>
      <div class="flex items-center gap-1">
        <Button size="sm" variant="outline" :disabled="page <= 1 || loading" @click="page = 1">«</Button>
        <Button size="sm" variant="outline" :disabled="page <= 1 || loading" @click="page = page - 1">
          <ChevronLeft class="size-4" />
        </Button>
        <input
          v-model.number="page"
          type="number"
          :min="1"
          :max="totalPages"
          class="w-16 rounded-md border bg-background px-2 py-1 text-center text-sm"
          @change="page = Math.min(Math.max(1, page), totalPages)"
        />
        <Button size="sm" variant="outline" :disabled="page >= totalPages || loading" @click="page = page + 1">
          <ChevronRight class="size-4" />
        </Button>
        <Button size="sm" variant="outline" :disabled="page >= totalPages || loading" @click="page = totalPages">»</Button>
      </div>
    </div>
  </div>
</template>
