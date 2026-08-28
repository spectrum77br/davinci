<script setup lang="ts">
// Controle de Estoque — operator-facing planilha.
//
// Filter model: SINGLE-DAY everywhere. The backend still accepts a
// date range (data_inicio / data_fim) so admin tooling can probe wider
// windows; the UI sends `data` for both to keep the contract one knob.
// Default = today.
//
// Tabs are isolated GETs:
//   * Estoque  → entradas + saídas + saldos for the chosen day.
//   * Pedidos  → "enviado etiqueta" orders shipped on the chosen day.
//   * Envios   → per-day shipment counts (the only tab that benefits
//                from a wider window, so it auto-widens to last 7 days
//                on first activation if the user hasn't picked a date).
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  Boxes, Truck, ClipboardList, Loader2, RefreshCw,
  AlertTriangle, FileUp, Upload, Download, Trash2, Printer, FileText,
  ArrowUp, ArrowDown, Megaphone,
} from 'lucide-vue-next'
import { isoDateBrt, isoDaysAgo, isoToday } from '~/lib/date'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'controle_estoque', action: 'view' },
})

const { api } = useApi()
const auth = useAuthStore()

// ── Types ─────────────────────────────────────────────────────────────
type EntradaMov = { movement_id: string; qty: number; obs: string }
type SaidaMov = { movement_id: string; qty: number; origem: string }
type ProdutoRow = {
  sku: string
  nome: string
  entradas: EntradaMov[]
  saidas: SaidaMov[]
  saida_qty_total: number
  saida_origens: string
  saldo_fisico: number
  saldo_virtual: number
  reserva: number
  conferido: boolean
  // Client-side state for the "no-entrada" obs row — survives until the
  // user types something, at which point saveSkuObs() upserts a real
  // placeholder movement and we cache the resulting id so blur/Enter
  // edits route back through patchMovementObs.
  _skuObsValue?: string
  _skuObsMovementId?: string
}
type PedidoRow = {
  id: string
  // Data de CRIAÇÃO do pedido no Bling (o.data.date()). NÃO confundir
  // com data_envio (= em_andamento_data, ship date) — a coluna
  // "DATA ENVIO" da aba Pedidos usa data_envio, não este campo.
  data: string | null
  data_pedido: string | null
  data_envio: string | null
  pedido_bling: string | null
  pedido_marketplace: string | null
  loja: string | null         // already pretty-formatted by backend
  // Nome de quem comprou (nome_destinatario do Bling). Null em pedidos antigos.
  cliente: string | null
  sku: string | null
  produto: string | null
  quantidade: number
  // 'previsao' = "Em aberto" no Bling (situação 6): NF/etiqueta ainda não
  // geradas — vai virar envio do dia (badge amarelo p/ separar de manhã).
  status: 'enviado' | 'nao_enviado' | 'previsao'
  conferido: boolean
  observacao: string | null
  bling_id: number | null
  etiqueta_disponivel: boolean
  // Quando a etiqueta chegou (ISO com fuso). Null quando não há etiqueta.
  etiqueta_em: string | null
  // Quando a etiqueta foi impressa pela 1ª vez. Null = nunca impressa.
  etiqueta_impressa_em: string | null
  // Instante em que o envio confirmou (entrada na situação 15, ledger
  // bling_envio_evento). Null = não enviado ou pedido anterior ao ledger —
  // nesse caso a coluna "Envio" mostra o rótulo "Enviado" de sempre.
  enviado_em: string | null
  // "Despachar até" prometido ao marketplace (horário de corte do pedido),
  // ISO tz-aware vindo da API de cada plataforma. Null = não capturado.
  ship_deadline: string | null
  // Quando o PAPEL DE PREVISÃO deste pedido saiu na impressora (🖨 do
  // relatório 10×15). Null = nunca. A tela mostra "🖨 HH:MM" sob o selo
  // amarelo pra ninguém separar o mesmo pedido duas vezes.
  previsao_impressa_em: string | null
}
type EnvioRow = {
  data: string
  // Contagem oficial: ledger por evento (shipping_day, corte 10:00 —
  // migrations 0156/0158), imune ao recarimbo de em_andamento_data.
  envios: number
  conferido: boolean
  // Status da conferência da aba Estoque para aquele dia. Vem do
  // backend — comparação count(StockCheck conferido) vs count(produtos).
  conferencia_estoque: 'total' | 'parcial' | 'nenhuma'
}

// ── State ─────────────────────────────────────────────────────────────
type Tab = 'estoque' | 'pedidos' | 'envios' | 'estoque-negativo' | 'upload-nf'
const tab = ref<Tab>('estoque')

// Single-day filter for Estoque + Pedidos. Envios uses a 7-day window
// that auto-resets on first activation (see watch below) — operators
// still want per-day counts but with enough rows on screen to compare.
const dia = ref(isoToday())
const enviosInicio = ref(isoDaysAgo(6))
const enviosFim = ref(isoToday())

// Admin-only tag override.
const isAdmin = computed(() => auth.user?.role === 'admin')

// Modal do botão INFORMAR (admin-only): relatório via Threema dos pedidos
// movidos pra Aguardando Cancelamento por falta de estoque.
const informarEstoqueOpen = ref(false)

// "Atualizar Bling" (job refresh-bling-stock) libera pra quem pode editar o
// Controle de Estoque — mesma permissão do botão "Recarregar" (sync-stocks).
// Antes era admin-only; operadores de conferência precisam puxar o Bling.
// Backend valida o mesmo controle_estoque:edit, então segurança não muda.
const canAtualizarBling = computed(
  () => isAdmin.value || auth.user?.permissions?.controle_estoque?.edit === true,
)

// Operador específico (churchill) tem acesso ao filtro de tag, mesmo não
// sendo admin (stock_tags dele cobre todas as 9 tags). Hardcoded por nome
// — se aparecer outro caso, vira permissão. Backend continua validando a
// tag contra user.stock_tags, então segurança não muda.
const canUseTagFilter = computed(() => {
  if (isAdmin.value) return true
  return (auth.user?.name || '').toLowerCase() === 'churchill'
})
// Gerente de etiquetas (cairo SA): na aba PEDIDOS enxerga todas as tags —
// é ele quem imprime e despacha as etiquetas do time inteiro. Estoque e
// Envios continuam cercados pelas stock_tags. A fonte da verdade é a flag
// permissions.controle_estoque_pedidos_todas_tags (backend valida; aqui
// ela só decide se mostramos o dropdown de tag na aba Pedidos).
const isGerenteEtiquetas = computed(
  () =>
    (auth.user?.permissions as Record<string, unknown> | undefined)
      ?.controle_estoque_pedidos_todas_tags === true,
)
const tagOverride = ref<string>('')

// Aba "Estoque Negativo" só aparece para admin, churchill (gerente) e
// cairo SA (sa.geral@tutamail.com). Operadores das demais tags não veem.
const canSeeEstoqueNegativo = computed(() => {
  if (isAdmin.value) return true
  const name = (auth.user?.name || '').toLowerCase()
  if (name === 'churchill') return true
  const email = (auth.user?.email || '').toLowerCase()
  return email === 'sa.geral@tutamail.com'
})
const visibleTabs = computed<readonly Tab[]>(() => {
  const base: Tab[] = ['estoque', 'pedidos', 'envios']
  if (canSeeEstoqueNegativo.value) base.push('estoque-negativo')
  base.push('upload-nf')
  return base
})

// Filter products by presence of stock. 'all' (default) = no filter,
// 'com' = Product.stock > 0, 'sem' = stock == 0 OR NULL. Applied to
// the /produtos list AND the conferência counter so denominators
// match what the operator sees on screen.
const estoqueFilter = ref<'all' | 'com' | 'sem'>('all')

// Single source of truth for tag labels — keep in sync with backend
// STOCK_TAGS list. The admin dropdown uses these; operadores never
// see this UI (they have a fixed set from user.stock_tags).
const TAG_OPTIONS: { slug: string; label: string }[] = [
  { slug: 'ci', label: 'CI' },
  { slug: 'pi', label: 'PI' },
  { slug: 'ra', label: 'RA' },
  { slug: 'sa', label: 'SA' },
  { slug: 'sp', label: 'SP' },
  { slug: 'us', label: 'Usados' },
  { slug: 'cd', label: 'Centro de Distribuição' },
  { slug: 'fake', label: 'Fake' },
  { slug: 'mala', label: 'Mala' },
  { slug: 'eletro', label: 'Eletro' },
  { slug: 'insumos', label: 'Insumos' },
]

// Manual reload — calls POST /api/estoque/sync-stocks which fans out
// GET /estoques/saldos on Bling for the visible product set. Used when
// the webhook missed a virtual-balance update (rare but happens for
// reservation-driven changes).
const syncing = ref(false)
const syncToast = ref<string | null>(null)
async function syncFromBling() {
  if (syncing.value) return
  syncing.value = true
  syncToast.value = null
  try {
    const params = new URLSearchParams()
    if (canUseTagFilter.value && tagOverride.value) params.set('tag', tagOverride.value)
    const r = await api<{ updated: number; total_products: number; missing_bling_data: number }>(
      `/api/estoque/sync-stocks${params.toString() ? `?${params.toString()}` : ''}`,
      { method: 'POST' },
    )
    syncToast.value = `Sincronizado: ${r.updated}/${r.total_products} produtos`
    void loadCurrentTab()
  } catch (e: any) {
    syncToast.value = `Falha: ${e?.data?.detail?.code || e?.message || 'erro'}`
  } finally {
    syncing.value = false
    setTimeout(() => { syncToast.value = null }, 4000)
  }
}

// ── Atualizar Bling (job assíncrono /api/jobs/refresh-bling-stock) ─────
// Pagina o /produtos do Bling, regrava estoque e — no fim — marca como
// excluído (situacao='E') os produtos que sumiram da listagem do Bling
// (foram apagados lá). Diferente do "Recarregar" (sync-stocks), que só
// atualiza o saldo dos produtos já cadastrados e NÃO remove os excluídos.
// Roda no worker e leva alguns minutos; acompanhamos por polling do job.
const blingJobRunning = ref(false)
const blingJobToast = ref<string | null>(null)
let blingPollHandle: number | null = null

function stopBlingPoll() {
  if (blingPollHandle) { clearInterval(blingPollHandle); blingPollHandle = null }
}

async function atualizarBling() {
  if (blingJobRunning.value) return
  blingJobRunning.value = true
  blingJobToast.value = 'Iniciando atualização do Bling…'
  try {
    const r = await api<{ job_id: string }>('/api/jobs/refresh-bling-stock', {
      method: 'POST',
    })
    pollBlingJob(r.job_id)
  } catch (e: any) {
    blingJobRunning.value = false
    blingJobToast.value = `Falha: ${e?.data?.detail?.code || e?.message || 'erro'}`
    setTimeout(() => { blingJobToast.value = null }, 6000)
  }
}

function pollBlingJob(jobId: string) {
  stopBlingPoll()
  const tick = async () => {
    try {
      const j = await api<{
        status: string; processed?: number; result?: Record<string, any>
      }>(`/api/jobs/${jobId}`)
      if (j.status === 'running' || j.status === 'pending') {
        blingJobToast.value = `Atualizando do Bling… ${j.processed ?? 0} produtos`
        return
      }
      stopBlingPoll()
      blingJobRunning.value = false
      if (j.status === 'succeeded') {
        const excl = Number(j.result?.reconciled_excluido ?? 0)
        blingJobToast.value = excl > 0
          ? `Pronto — estoque atualizado e ${excl} produto(s) excluído(s) no Bling removido(s).`
          : 'Pronto — estoque atualizado (nenhum excluído encontrado).'
        // O sweep do /produtos refresca só o saldo VIRTUAL (stock). O
        // reserved_stock fica com o último valor do webhook e pode estar
        // preso (reserva de um pedido que já saiu no Bling), inflando o
        // "saldo atual" da grade. Encadeia o reconcile de reserva
        // (/sync-stocks: puxa saldoFisico/Virtual do Bling e recalcula
        // reserved_stock = max(0, físico - virtual)); ele recarrega a
        // grade já corrigida ao terminar.
        void syncFromBling()
      } else {
        blingJobToast.value = 'Falha ao atualizar o estoque do Bling.'
      }
      setTimeout(() => { blingJobToast.value = null }, 10000)
    } catch {
      // erro transitório no polling — tenta de novo no próximo tick
    }
  }
  void tick()
  blingPollHandle = window.setInterval(tick, 2000)
}

onBeforeUnmount(stopBlingPoll)

const statusFilter = ref<'all' | 'enviado' | 'nao_enviado' | 'previsao'>('all')
// Filtro por estado da etiqueta (aba Pedidos) — 100% client-side: as
// linhas já carregam etiqueta_em / etiqueta_impressa_em. "não impressa" =
// etiqueta JÁ chegou e ninguém imprimiu (a fila de impressão do gerente
// de etiquetas); "sem etiqueta" = ainda nem chegou.
const etiquetaFilter = ref<'all' | 'impressa' | 'nao_impressa' | 'sem'>('all')
// Loja e plataforma (aba Pedidos) — client-side, as opções nascem do que
// veio no dia. Plataforma = 1ª palavra do nome da loja, que o backend monta
// como "{PLATAFORMA} {apelido}" (ex.: "SHOPEE Jlas" → SHOPEE).
const lojaFilter = ref('')
const plataformaFilter = ref('')
const conferidoFilter = ref<'all' | 'conferidos' | 'nao_conferidos'>('all')
const search = ref('')

// Data
const produtos = ref<ProdutoRow[]>([])
const pedidos = ref<PedidoRow[]>([])
// Atrasados vêm do backend (independente do filtro): pedidos com etiqueta
// gerada em dia passado e ainda não confirmados. O chip do topo os exibe
// só quando o operador está no filtro de hoje.
const pedidosAtrasadosRaw = ref<{ date: string; count: number }[]>([])
const envios = ref<{
  items: EnvioRow[]
  total: number                 // sum of conferido envios (footer "Total")
  total_envios: number          // sum across the window (footer "Total geral")
}>({ items: [], total: 0, total_envios: 0 })

// Foto da conferência do estoque HOJE — independente do filtro de dia.
// Alimenta o bloqueio da aba Envios pro operador (admin nunca bloqueia).
// Recarregado em onMounted, ao trocar a tab e após conferirTodos/toggleProduto
// quando o operador está vendo o dia de hoje.
const conferenciaHoje = ref<{ total: number; conferido: number; percent: number }>({
  total: 0, conferido: 0, percent: 0,
})
async function refreshConferenciaHoje() {
  try {
    const params = new URLSearchParams()
    if (canUseTagFilter.value && tagOverride.value) params.set('tag', tagOverride.value)
    if (estoqueFilter.value !== 'all') params.set('estoque_filter', estoqueFilter.value)
    const r = await api<{ total: number; conferido: number; percent: number }>(
      `/api/estoque/conferencia-hoje${params.toString() ? `?${params.toString()}` : ''}`,
    )
    conferenciaHoje.value = { total: r.total, conferido: r.conferido, percent: r.percent }
  } catch {
    // não-fatal: mantém o valor anterior; o bloqueio cai pro lado seguro
    // (operador não acessa Envios sem conferência confirmada).
  }
}

const loading = ref(false)
const errorText = ref<string | null>(null)

// ── Fetchers ──────────────────────────────────────────────────────────
function singleDayDates(): string {
  // Estoque + Pedidos send the same value for both endpoints — backend
  // tolerates either treating the window as a single point or a range.
  // Input de data limpo → string vazia quebra o parse de date no backend
  // (422); cai pra hoje.
  const d = dia.value || isoToday()
  const parts = [`data_inicio=${d}`, `data_fim=${d}`]
  if (canUseTagFilter.value && tagOverride.value) parts.push(`tag=${tagOverride.value}`)
  // estoque_filter applies only to the Estoque tab (the /produtos call
  // below). Pedidos and Envios ignore the param.
  if (estoqueFilter.value !== 'all') parts.push(`estoque_filter=${estoqueFilter.value}`)
  return parts.join('&')
}
function rangeDates(): string {
  const parts = [
    `data_inicio=${enviosInicio.value || isoDaysAgo(6)}`,
    `data_fim=${enviosFim.value || isoToday()}`,
  ]
  if (canUseTagFilter.value && tagOverride.value) parts.push(`tag=${tagOverride.value}`)
  return parts.join('&')
}

async function loadEstoque() {
  loading.value = true
  errorText.value = null
  try {
    const r = await api<{ data: ProdutoRow[] }>(`/api/estoque/produtos?${singleDayDates()}`)
    produtos.value = r.data || []
  } catch (e: any) {
    errorText.value = e?.data?.detail?.code || e?.message || 'load_failed'
    produtos.value = []
  } finally {
    loading.value = false
  }
}

async function loadPedidos() {
  loading.value = true
  errorText.value = null
  try {
    const qs = [singleDayDates()]
    // Gerente de etiquetas: o tag= do dropdown vale na aba Pedidos mesmo
    // sem canUseTagFilter (que também alimenta o /produtos — lá o cairo
    // continua cercado nas stock_tags dele, então não entra no
    // singleDayDates compartilhado).
    if (!canUseTagFilter.value && isGerenteEtiquetas.value && tagOverride.value)
      qs.push(`tag=${tagOverride.value}`)
    if (statusFilter.value !== 'all') qs.push(`status=${statusFilter.value}`)
    const r = await api<{ data: PedidoRow[]; atrasados?: { date: string; count: number }[] }>(
      `/api/estoque/pedidos?${qs.join('&')}`,
    )
    pedidos.value = r.data || []
    pedidosAtrasadosRaw.value = r.atrasados || []
  } catch (e: any) {
    errorText.value = e?.data?.detail?.code || e?.message || 'load_failed'
    pedidos.value = []
    pedidosAtrasadosRaw.value = []
  } finally {
    loading.value = false
  }
}

async function loadEnvios() {
  loading.value = true
  errorText.value = null
  try {
    const qs = [rangeDates()]
    if (conferidoFilter.value !== 'all') qs.push(`conferido=${conferidoFilter.value}`)
    const r = await api<{
      data: EnvioRow[]
      total: number
      total_envios: number
      total_conferido: number
    }>(`/api/estoque/envios?${qs.join('&')}`)
    envios.value = {
      items: r.data || [],
      total: r.total ?? 0,
      total_envios: r.total_envios ?? 0,
    }
  } catch (e: any) {
    errorText.value = e?.data?.detail?.code || e?.message || 'load_failed'
    envios.value = { items: [], total: 0, total_envios: 0 }
  } finally {
    loading.value = false
  }
}

function loadCurrentTab() {
  if (tab.value === 'estoque') return loadEstoque()
  if (tab.value === 'pedidos') return loadPedidos()
  return loadEnvios()
}

watch(tab, () => {
  void loadCurrentTab()
  // O bloqueio da aba Envios depende da conferência de hoje — refetch
  // sempre que a tab muda pra refletir alterações feitas em outra aba.
  void refreshConferenciaHoje()
})
watch([dia, tagOverride, statusFilter], () => {
  if (tab.value !== 'envios') void loadCurrentTab()
})
watch(estoqueFilter, () => {
  // Refetch Estoque list (the only tab that uses the filter) AND the
  // conferência counter so the percentage matches the visible set.
  if (tab.value === 'estoque') void loadCurrentTab()
  void refreshConferenciaHoje()
})
watch([enviosInicio, enviosFim, conferidoFilter], () => {
  if (tab.value === 'envios') void loadCurrentTab()
})
watch(tagOverride, () => {
  if (tab.value === 'envios') void loadCurrentTab()
})

// ── Aba: Estoque Negativo (refresh-from-Bling + sufixos) ──────────────
type SaldoRow = { codigo: string; saldo_fisico: number; saldo_virtual_total: number }
const negativos = ref<SaldoRow[]>([])
const sufixos = ref<SaldoRow[]>([])
const negativosLoading = ref(false)
const refreshing = ref(false)
const refreshMsg = ref<string | null>(null)
const suffixChoice = ref<string>('.us,.sa')
const customSuffix = ref<string>('')
const SUFFIX_PRESETS: { value: string; label: string }[] = [
  { value: '.us,.sa', label: '.us + .sa (default)' },
  { value: '.ci', label: '.ci' },
  { value: '.ra', label: '.ra' },
  { value: '.sp', label: '.sp' },
  { value: '.cd', label: '.cd' },
  { value: '.pi', label: '.pi' },
  { value: '__custom__', label: 'personalizado…' },
]
const effectiveSuffixes = computed(() =>
  suffixChoice.value === '__custom__' ? customSuffix.value.trim() : suffixChoice.value,
)

async function loadNegativos() {
  negativosLoading.value = true
  try {
    const q = search.value.trim()
    const r = await api<{ items: SaldoRow[] }>(
      `/api/estoque/negativos${q ? `?search=${encodeURIComponent(q)}` : ''}`,
    )
    negativos.value = r.items || []
  } catch (e: any) {
    refreshMsg.value = `Falha negativos: ${e?.data?.detail?.code || e?.message || 'erro'}`
  } finally {
    negativosLoading.value = false
  }
}
async function loadSufixos() {
  const sufs = effectiveSuffixes.value
  if (!sufs) { sufixos.value = []; return }
  try {
    const r = await api<{ items: SaldoRow[] }>(
      `/api/estoque/sufixos?suffixes=${encodeURIComponent(sufs)}`,
    )
    sufixos.value = r.items || []
  } catch (e: any) {
    refreshMsg.value = `Falha sufixos: ${e?.data?.detail?.code || e?.message || 'erro'}`
  }
}
async function refreshFromBling() {
  refreshing.value = true
  refreshMsg.value = null
  try {
    const r = await api<{
      updated: number; total_products: number; missing_bling_data?: number
    }>('/api/estoque/atualizar-bling', { method: 'POST' })
    refreshMsg.value = `Atualizou ${r.updated}/${r.total_products} produtos` +
      (r.missing_bling_data ? ` (${r.missing_bling_data} sem dado Bling)` : '')
    await Promise.all([loadNegativos(), loadSufixos()])
  } catch (e: any) {
    const code = e?.data?.detail?.code
    if (code === 'refresh_already_running') {
      refreshMsg.value = `Refresh já em andamento (${e?.data?.detail?.started_at})`
    } else if (code === 'bling_not_connected') {
      refreshMsg.value = 'Bling não conectado'
    } else {
      refreshMsg.value = e?.message || 'erro'
    }
  } finally {
    refreshing.value = false
  }
}
function downloadCsv() {
  const sufs = effectiveSuffixes.value
  if (!sufs) return
  window.open(`/api/estoque/sufixos.csv?suffixes=${encodeURIComponent(sufs)}`, '_blank')
}

// ── Aba: Upload NF (XML → ML) ─────────────────────────────────────────
type NfAttempt = { store: string; success: boolean; error: string | null; shipping_id: string | null }
type NfResult = {
  filename: string
  success: boolean
  order_id?: string | null
  store_name?: string | null
  shipping_id?: string | null
  error?: string | null
  attempts_details?: NfAttempt[]
}
const nfFiles = ref<File[]>([])
const nfStores = ref<string[]>([])
const nfSelectedStores = ref<Set<string>>(new Set())
const nfProcessing = ref(false)
const nfCurrentFile = ref<string | null>(null)
const nfResults = ref<NfResult[]>([])
const nfFileInputRef = ref<HTMLInputElement | null>(null)

const nfSuccessCount = computed(() => nfResults.value.filter((r) => r.success).length)
const nfFailCount = computed(() => nfResults.value.filter((r) => !r.success).length)

async function loadNfStores() {
  try {
    const r = await api<{ stores: string[] }>('/api/nf/stores')
    nfStores.value = r.stores || []
    nfSelectedStores.value = new Set(nfStores.value)
  } catch (e: any) {
    console.error('Falha lojas NF:', e)
  }
}
function onNfFileChange(ev: Event) {
  const inp = ev.target as HTMLInputElement
  if (!inp.files) return
  const next = Array.from(inp.files)
  nfFiles.value = [
    ...nfFiles.value,
    ...next.filter((nf) =>
      !nfFiles.value.some((f) => f.name === nf.name && f.size === nf.size),
    ),
  ]
  inp.value = ''
}
function removeNfFile(idx: number) {
  nfFiles.value.splice(idx, 1)
}
function clearNfAll() {
  nfFiles.value = []
  nfResults.value = []
  nfCurrentFile.value = null
}
function toggleNfStore(name: string) {
  const s = new Set(nfSelectedStores.value)
  if (s.has(name)) s.delete(name); else s.add(name)
  nfSelectedStores.value = s
}
async function processNfFiles() {
  if (!nfFiles.value.length || !nfSelectedStores.value.size) return
  nfProcessing.value = true
  nfResults.value = []
  for (const file of nfFiles.value) {
    nfCurrentFile.value = file.name
    const fd = new FormData()
    fd.append('file', file)
    for (const s of nfSelectedStores.value) fd.append('selected_stores', s)
    try {
      const r = await api<NfResult>('/api/nf/upload', { method: 'POST', body: fd })
      nfResults.value.push({ filename: file.name, ...r })
    } catch (e: any) {
      nfResults.value.push({
        filename: file.name,
        success: false,
        error: e?.data?.detail?.code || e?.message || 'erro',
        attempts_details: [],
      })
    }
  }
  nfCurrentFile.value = null
  nfProcessing.value = false
}

// Lazy-load dos dados das novas abas + re-load no toggle de aba.
watch(tab, async (newTab) => {
  if (newTab === 'estoque-negativo') {
    await Promise.all([loadNegativos(), loadSufixos()])
  } else if (newTab === 'upload-nf' && !nfStores.value.length) {
    await loadNfStores()
  }
})
watch(effectiveSuffixes, () => {
  if (tab.value === 'estoque-negativo') void loadSufixos()
})
watch(search, () => {
  if (tab.value === 'estoque-negativo') void loadNegativos()
})

onMounted(() => {
  void loadCurrentTab()
  void refreshConferenciaHoje()
})

// ── Conferido toggle (per section) ────────────────────────────────────
async function toggleCheck(
  section: 'estoque' | 'pedido' | 'envio',
  referenceId: string,
  referenceDate: string,
  next: boolean,
  observacao?: string | null,
) {
  const params = new URLSearchParams({
    section,
    reference_id: referenceId,
    reference_date: referenceDate,
    conferido: String(next),
  })
  if (observacao != null) params.set('observacao', observacao)
  await api(`/api/estoque/check?${params.toString()}`, { method: 'POST' })
}

async function toggleProduto(row: ProdutoRow) {
  const next = !row.conferido
  row.conferido = next
  try {
    await toggleCheck('estoque', row.sku, dia.value, next)
    // Só conta pra liberar a aba Envios se o operador está marcando
    // o dia de hoje. Marcar dias passados não destrava nada.
    if (dia.value === isoToday()) void refreshConferenciaHoje()
  } catch {
    row.conferido = !next
  }
}
async function togglePedido(row: PedidoRow) {
  const next = !row.conferido
  row.conferido = next
  // reference_date é informacional pra section=pedido (backend filtra
  // só por reference_id); usar data_envio mantém alinhado com a coluna
  // "DATA ENVIO" exibida ao operador.
  const refDate = (row.data_envio || dia.value).slice(0, 10)
  try {
    await toggleCheck('pedido', row.id, refDate, next, row.observacao)
  } catch {
    row.conferido = !next
  }
}
async function patchPedidoObs(row: PedidoRow, newObs: string) {
  row.observacao = newObs
  const refDate = (row.data_envio || dia.value).slice(0, 10)
  try {
    await toggleCheck('pedido', row.id, refDate, row.conferido, newObs)
  } catch { /* next reload reverts */ }
}
// Etiqueta transformada (landing zone da NF automática). URL relativa → o
// cookie de sessão vai junto quando o <a> abre numa aba nova.
function etiquetaUrl(row: PedidoRow) {
  const base = `/api/estoque/pedidos/${encodeURIComponent(row.pedido_bling || '')}/etiqueta`
  // O armazém do dropdown recorta a declaração dos pedidos divididos (quem
  // despacha só vê o item que está com ele).
  return tagImpressao() ? `${base}?tag=${tagImpressao()}` : base
}
// Armazém a mandar na impressão: só admin/gerente escolhem pelo dropdown —
// operador comum já é cercado pelas stock_tags dele no backend.
function tagImpressao() {
  return (canUseTagFilter.value || isGerenteEtiquetas.value) ? tagOverride.value : ''
}
// Hora BRT em que a etiqueta chegou (dd/mm quando não é hoje).
const _HORA_BRT = new Intl.DateTimeFormat('pt-BR', {
  timeZone: 'America/Sao_Paulo', hour: '2-digit', minute: '2-digit',
})
function etiquetaHora(row: PedidoRow) {
  if (!row.etiqueta_em) return ''
  const d = new Date(row.etiqueta_em)
  const hora = _HORA_BRT.format(d)
  const dia = isoDateBrt(d)
  return dia === isoToday() ? hora : `${dia.slice(8)}/${dia.slice(5, 7)} ${hora}`
}
function impressaHora(row: PedidoRow) {
  if (!row.etiqueta_impressa_em) return ''
  const d = new Date(row.etiqueta_impressa_em)
  const hora = _HORA_BRT.format(d)
  const dia = isoDateBrt(d)
  return dia === isoToday() ? hora : `${dia.slice(8)}/${dia.slice(5, 7)} ${hora}`
}
// Hora em que o papel de PREVISÃO saiu na impressora (mesma convenção das
// horas de etiqueta: só hora se foi hoje, dd/mm antes quando é de outro dia).
function previsaoImpressaHora(row: PedidoRow) {
  if (!row.previsao_impressa_em) return ''
  const d = new Date(row.previsao_impressa_em)
  const hora = _HORA_BRT.format(d)
  const dia = isoDateBrt(d)
  return dia === isoToday() ? hora : `${dia.slice(8)}/${dia.slice(5, 7)} ${hora}`
}
function envioHora(row: PedidoRow) {
  if (!row.enviado_em) return ''
  const d = new Date(row.enviado_em)
  // SEMPRE com o dia (pedido do Eduardo): o badge verde é o comprovante de
  // quando saiu — "07/08 09:07" —, sem o "sem dia = hoje" implícito que as
  // colunas Etiqueta/Impressão usam.
  const dia = isoDateBrt(d)
  return `${dia.slice(8)}/${dia.slice(5, 7)} ${_HORA_BRT.format(d)}`
}

// ── Horário de corte ("despachar até" do marketplace) ─────────────────
// Mostrado embaixo do nome da loja, só em pedido ainda NÃO enviado.
// Relógio de 60s mantém "falta Xmin"/"estourou" vivos sem recarregar.
const corteAgoraMs = ref(Date.now())
let corteClock: number | null = null
onMounted(() => {
  corteClock = window.setInterval(() => { corteAgoraMs.value = Date.now() }, 60_000)
})
onBeforeUnmount(() => {
  if (corteClock !== null) window.clearInterval(corteClock)
})
function corteInfo(row: PedidoRow): { label: string; cls: string } | null {
  if (!row.ship_deadline || row.status === 'enviado') return null
  const dl = new Date(row.ship_deadline)
  const hora = _HORA_BRT.format(dl)
  const dia = isoDateBrt(dl)
  const hoje = isoToday()
  const ddmm = `${dia.slice(8)}/${dia.slice(5, 7)}`
  if (dia > hoje) {
    // Corte só amanhã ou depois: discreto, sem urgência.
    return { label: `corte ${ddmm} ${hora}`, cls: 'text-muted-foreground' }
  }
  const faltaMin = Math.floor((dl.getTime() - corteAgoraMs.value) / 60_000)
  if (faltaMin < 0) {
    return {
      label: `corte ${dia === hoje ? hora : `${ddmm} ${hora}`} — estourou`,
      cls: 'text-red-600 dark:text-red-400 font-semibold',
    }
  }
  if (faltaMin < 60) {
    return {
      label: `corte ${hora} — falta ${faltaMin}min`,
      cls: 'text-amber-700 dark:text-amber-400 font-semibold',
    }
  }
  return { label: `corte ${hora}`, cls: 'text-amber-700 dark:text-amber-400' }
}

// Dia da previsão pelo corte: 'hoje' = corte hoje ou atrasado (sai JÁ);
// 'amanha' = corte amanhã (dá pra ir adiantando). Eduardo, 2026-08-26:
// "os de hoje... e os de amanha pra ja ir adiantando". O corte é por
// pedido (replicado nos itens). Sem deadline não rola em previsão (o
// backend exige corte na janela), mas na dúvida conta como hoje.
function previsaoDia(row: PedidoRow): 'hoje' | 'amanha' {
  if (!row.ship_deadline) return 'hoje'
  const dl = new Date(row.ship_deadline)
  const diaCorte = isoDateBrt(dl)
  if (diaCorte <= isoToday()) return 'hoje'
  // ML de AGÊNCIA: o SLA do ML vem 23:59 e o backend soma +1 dia no corte
  // salvo (folga de "posta na manhã seguinte" — vale pro card de atrasados,
  // ver _ml_corte_agencia). Pra SEPARAÇÃO isso invertia o dia: o ML manda
  // enviar hoje/24h e a tela dizia "amanhã" (Eduardo, 2026-08-27: "tem dois
  // com a previsao amanha e esta para enviar hoje, em 24 horas"). Agência
  // (corte amanhã às 23:59) conta como HOJE; coleta de amanhã (hora real,
  // ex. 13:30) segue como adiantamento.
  const ehML = (row.loja || '').trim().toUpperCase().startsWith('ML')
  if (ehML && diaCorte === isoDaysAgo(-1) && _HORA_BRT.format(dl) === '23:59') return 'hoje'
  return 'amanha'
}

async function toggleEnvio(row: EnvioRow) {
  if (!isAdmin.value) return
  const next = !row.conferido
  row.conferido = next
  try {
    await toggleCheck('envio', row.data, row.data, next)
    if (next) envios.value.total += row.envios
    else envios.value.total = Math.max(0, envios.value.total - row.envios)
  } catch {
    row.conferido = !next
  }
}

async function patchMovementObs(movementId: string, newObs: string, row: ProdutoRow, idx: number) {
  row.entradas[idx].obs = newObs
  try {
    const params = new URLSearchParams()
    if (newObs) params.set('observacao', newObs)
    await api(`/api/estoque/movement/${movementId}/obs?${params.toString()}`, { method: 'PATCH' })
  } catch {
    void loadEstoque()
  }
}

// Obs input on a SKU with NO entrada today — upserts a placeholder
// `manual-note` movement via the dedicated endpoint. Once created, the
// next loadEstoque() surfaces it as a regular entrada (with qty=0) and
// subsequent edits go through patchMovementObs.
async function saveSkuObs(row: ProdutoRow, newObs: string) {
  // No-op on empty → don't create an empty placeholder movement.
  if (!newObs.trim() && (!row._skuObsMovementId)) return
  try {
    const params = new URLSearchParams({
      sku: row.sku,
      reference_date: dia.value,
    })
    if (newObs) params.set('observacao', newObs)
    const r = await api<{ movement_id: string }>(
      `/api/estoque/sku-obs?${params.toString()}`,
      { method: 'POST' },
    )
    row._skuObsMovementId = r.movement_id
    row._skuObsValue = newObs
  } catch {
    void loadEstoque()
  }
}

// ── Search filter (client-side) ───────────────────────────────────────
const produtosFiltered = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return produtos.value
  return produtos.value.filter(
    (p) =>
      (p.sku || '').toLowerCase().includes(q)
      || (p.nome || '').toLowerCase().includes(q),
  )
})
function plataformaDe(loja: string | null): string {
  const primeira = (loja || '').trim().split(/\s+/)[0] || ''
  // Loja sem cadastro cai no ID cru do Bling — não vira plataforma.
  return /^[A-Za-z]/.test(primeira) ? primeira.toUpperCase() : ''
}
const plataformaOptions = computed(() =>
  [...new Set(pedidos.value.map((p) => plataformaDe(p.loja)).filter(Boolean))].sort(),
)
// As lojas listadas respeitam a plataforma escolhida (dropdown menor).
const lojaOptions = computed(() => {
  const plat = plataformaFilter.value
  const nomes = pedidos.value
    .filter((p) => !plat || plataformaDe(p.loja) === plat)
    .map((p) => (p.loja || '').trim())
    .filter(Boolean)
  return [...new Set(nomes)].sort((a, b) => a.localeCompare(b, 'pt-BR'))
})
// Trocar de plataforma zera a loja: a anterior provavelmente não existe
// mais na lista e o operador ficaria com a tabela vazia sem entender.
watch(plataformaFilter, () => { lojaFilter.value = '' })

const pedidosFiltered = computed(() => {
  let rows = pedidos.value
  if (plataformaFilter.value) {
    rows = rows.filter((p) => plataformaDe(p.loja) === plataformaFilter.value)
  }
  if (lojaFilter.value) {
    rows = rows.filter((p) => (p.loja || '').trim() === lojaFilter.value)
  }
  if (etiquetaFilter.value === 'impressa') {
    rows = rows.filter((p) => p.etiqueta_impressa_em)
  } else if (etiquetaFilter.value === 'nao_impressa') {
    rows = rows.filter((p) => p.etiqueta_em && !p.etiqueta_impressa_em)
  } else if (etiquetaFilter.value === 'sem') {
    rows = rows.filter((p) => !p.etiqueta_em)
  }
  const q = search.value.trim().toLowerCase()
  if (!q) return rows
  return rows.filter(
    (p) =>
      (p.sku || '').toLowerCase().includes(q)
      || (p.produto || '').toLowerCase().includes(q)
      || (p.pedido_bling || '').toLowerCase().includes(q)
      || (p.pedido_marketplace || '').toLowerCase().includes(q)
      || (p.cliente || '').toLowerCase().includes(q),
  )
})

// Pedidos distintos (não linhas) — `bling_orders` é multi-row por pedido
// (uma linha por item), então `pedidosFiltered.length` conta itens.
// O operador quer ver número de pedidos.
const totalPedidos = computed(() =>
  new Set(
    pedidosFiltered.value.map((p) => p.pedido_bling).filter(Boolean),
  ).size,
)

// Breakdown enviado/não-enviado — distinct pedido_bling pra bater com
// totalPedidos (em_andamento_data é consistente entre itens do mesmo
// pedido, então cada pedido cai inteiro num dos dois lados).
const pedidosEnviadosCount = computed(() =>
  new Set(
    pedidosFiltered.value
      .filter((p) => p.status === 'enviado')
      .map((p) => p.pedido_bling)
      .filter(Boolean),
  ).size,
)
const pedidosNaoEnviadosCount = computed(() =>
  new Set(
    pedidosFiltered.value
      .filter((p) => p.status === 'nao_enviado')
      .map((p) => p.pedido_bling)
      .filter(Boolean),
  ).size,
)
// Previsão = "Em aberto" no Bling (vai emitir NF/etiqueta no dia) — o
// pessoal do envio separa o produto de manhã e cola a etiqueta quando ela
// liberar (ML solta ~meio-dia). Pedido do Eduardo, 2026-08-24.
const pedidosPrevisaoCount = computed(() =>
  new Set(
    pedidosFiltered.value
      .filter((p) => p.status === 'previsao')
      .map((p) => p.pedido_bling)
      .filter(Boolean),
  ).size,
)

// ── Impressão das previsões (padrão etiqueta térmica, 10×15 cm) ──────
// Relatório SÓ INFORMATIVO pro pessoal do envio: imprime de manhã a lista
// do que está em previsão (pedidos "Em aberto" no Bling), separa o produto
// e, quando a etiqueta liberar (~meio-dia no ML), já está tudo separadinho.
// Sai na MESMA impressora térmica das etiquetas: etiqueta 10×15 em
// PAISAGEM (150 mm de largura — retrato espremia as 7 colunas).
// 1ª etiqueta = "Separar" (total por produto — a lista de pegar no estoque);
// depois, a conferência pedido a pedido. No Bling não muda NADA; no banco
// só carimba previsao_impressa (hora que o papel saiu) pra tela mostrar o
// 🖨 e ninguém separar duas vezes. Pedido do Eduardo, 2026-08-26.
function _esc(s: unknown): string {
  return String(s ?? '').replace(/[&<>"']/g, (ch) =>
    ch === '&' ? '&amp;' : ch === '<' ? '&lt;' : ch === '>' ? '&gt;' : ch === '"' ? '&quot;' : '&#39;',
  )
}
// Carimba "papel de previsão impresso": otimista na tela (o pessoal segue
// trabalhando sem esperar rede) + POST pro banco (é o que os outros
// computadores e o F5 vão ver). Se o POST falhar, o papel já saiu mesmo —
// o carimbo local fica e o próximo reload mostra a verdade do banco.
async function marcarPrevisoesImpressas(nums: string[], rows: PedidoRow[]) {
  if (!nums.length) return
  const agora = new Date().toISOString()
  for (const r of rows) r.previsao_impressa_em = agora
  try {
    await api('/api/estoque/pedidos/previsoes/impressas', {
      method: 'POST',
      body: { pedidos: nums },
    })
  } catch {
    /* sem toast: carimbo é apoio, não trava o fluxo de impressão */
  }
}
// ── Seleção das previsões pra imprimir ───────────────────────────────
// Checkbox amarelo nas linhas de previsão (Eduardo, 2026-08-27: "precisa
// tem um botao para mim selecionar as previsoes que quero imprimir").
// Seleção por PEDIDO, igual às etiquetas. Com previsões marcadas, o botão
// "imprimir" sai SÓ com elas; sem nenhuma marcada, imprime todas (como era).
const previsoesSel = ref<Set<string>>(new Set())
// Só conta o que está marcado E ainda é previsão na tela (se o pedido já
// virou etiqueta, a marca dele deixa de valer — o botão não pode mentir).
const previsoesSelCount = computed(() => {
  let n = 0
  const vistos = new Set<string>()
  for (const p of pedidosFiltered.value) {
    if (p.status !== 'previsao' || !p.pedido_bling || vistos.has(p.pedido_bling)) continue
    vistos.add(p.pedido_bling)
    if (previsoesSel.value.has(p.pedido_bling)) n += 1
  }
  return n
})
function togglePrevisaoSel(pedido: string | null) {
  if (!pedido) return
  const next = new Set(previsoesSel.value)
  if (next.has(pedido)) next.delete(pedido)
  else next.add(pedido)
  previsoesSel.value = next
}
function imprimirPrevisoes() {
  const todas = pedidosFiltered.value.filter((p) => p.status === 'previsao')
  const marcadas = todas.filter((p) => p.pedido_bling && previsoesSel.value.has(p.pedido_bling))
  // Marcou → sai só o marcado; não marcou nada → sai tudo (como sempre foi).
  const linhas = marcadas.length ? marcadas : todas
  if (!linhas.length) return
  // Itens do mesmo pedido juntos, na ordem em que estão na tabela.
  const porPedido = new Map<string, PedidoRow[]>()
  for (const r of linhas) {
    const k = r.pedido_bling || r.id
    const arr = porPedido.get(k)
    if (arr) arr.push(r)
    else porPedido.set(k, [r])
  }
  const [y, m, d] = dia.value.split('-')
  const dataBR = `${d}/${m}/${y}`
  const hora = _HORA_BRT.format(new Date())
  // HOJE × AMANHÃ (Eduardo, 2026-08-26): corte hoje/atrasado sai JÁ; corte
  // amanhã é adiantamento. Cada grupo ganha sua tabela de separação; na
  // conferência o dia vai carimbado embaixo do nº do pedido.
  const hojeItens = linhas.filter((r) => previsaoDia(r) === 'hoje')
  const amanhaItens = linhas.filter((r) => previsaoDia(r) === 'amanha')
  // Tabelas com borda e cabeçalho, no MESMO estilo do "Relatório de
  // pedidos" (imprimirRelatorio) que a equipe já conhece — só que
  // estreitas, cabendo nos 100 mm da térmica (Eduardo, 2026-08-26:
  // "no estilo relatorio... só precisa estar organizado").
  const tabelaSeparar = (itens: PedidoRow[]) => {
    const tot = new Map<string, { produto: string; qtd: number }>()
    for (const r of itens) {
      const k = r.sku || r.produto || '?'
      const t = tot.get(k)
      if (t) t.qtd += r.quantidade || 0
      else tot.set(k, { produto: r.produto || '', qtd: r.quantidade || 0 })
    }
    const rows = [...tot.entries()]
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(
        ([sku, t]) =>
          `<tr><td class="qtd">${_esc(t.qtd)}</td><td class="sku">${_esc(sku)}</td><td class="nome">${_esc(t.produto)}</td></tr>`,
      )
      .join('')
    return `<table>
      <colgroup><col class="c-qtd"><col class="c-sku"><col></colgroup>
      <thead><tr><th>Qtd</th><th>Código (SKU)</th><th>Produto</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`
  }
  // Conferência: UM pedido por folha 10×15 (Eduardo, 2026-08-26: "ta saindo
  // dois pedido por etiqueta, tem como deixar só um?" — antes eram 2), cada
  // informação na sua COLUNA (Pedido | Marketplace | Loja | Cliente | Qtd |
  // SKU | Produto). Cada pedido vira uma página própria (break-before) com
  // cabeçalho repetido; rowspan junta os itens do mesmo pedido; a tabela
  // estica até o rodapé, então o pedido sozinho ocupa a folha inteira.
  const peds = [...porPedido.entries()]
  const paginasConf: string[] = []
  for (let i = 0; i < peds.length; i += 1) {
    const corpo = peds
      .slice(i, i + 1)
      .map(([num, itens]) => {
        const first = itens[0]
        const diaTxt = first && previsaoDia(first) === 'amanha' ? 'AMANHÃ' : 'HOJE'
        const rows = itens
          .map((r, j) => {
            const cab =
              j === 0
                ? `<td class="ped" rowspan="${itens.length}">${_esc(num)}<div class="dia">${diaTxt}</div></td>`
                  + `<td class="mkt" rowspan="${itens.length}">${_esc(first?.pedido_marketplace || '—')}</td>`
                  + `<td class="loja" rowspan="${itens.length}">${_esc(first?.loja || '—')}</td>`
                  + `<td class="cli" rowspan="${itens.length}">${_esc(first?.cliente || '—')}</td>`
                : ''
            return `<tr>${cab}<td class="qtd">${_esc(r.quantidade)}</td><td class="sku">${_esc(r.sku || '—')}</td><td class="nome">${_esc(r.produto || '')}</td></tr>`
          })
          .join('')
        return `<tbody>${rows}</tbody>`
      })
      .join('')
    paginasConf.push(`<div class="pagped">
      <div class="sec">Conferência por pedido</div>
      <table>
        <colgroup><col class="c-ped"><col class="c-mkt"><col class="c-loja"><col class="c-cli"><col class="c-qtd2"><col class="c-sku2"><col></colgroup>
        <thead><tr><th>Pedido</th><th>Marketplace</th><th>Loja</th><th>Cliente</th><th>Qtd</th><th>SKU</th><th>Produto</th></tr></thead>
        ${corpo}
      </table>
    </div>`)
  }
  const html = `<!doctype html><html><head><meta charset="utf-8"><title>Previsão ${_esc(dataBR)}</title><style>
    /* PAISAGEM na etiqueta 10×15 (Eduardo, 2026-08-27 "fica muito
       expremido... deixar ela em paisagem"): 150 mm de largura útil —
       as 7 colunas respiram e nome/SKU saem inteiros. O driver da
       térmica gira sozinho pelo tamanho declarado. */
    @page { size: 150mm 100mm; margin: 4mm; }
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { font-family: Arial, Helvetica, sans-serif; color: #000; font-size: 10pt; }
    .cab { text-align: center; border-bottom: 2px solid #000; padding-bottom: 1.5mm; margin-bottom: 1.5mm; }
    .cab h1 { font-size: 13pt; letter-spacing: .5px; }
    .cab .sub { font-size: 8.5pt; margin-top: .5mm; }
    .sec { font-size: 10pt; font-weight: 700; text-transform: uppercase; border-bottom: 1px solid #000; margin: 1mm 0; padding-bottom: .5mm; }
    table { border-collapse: collapse; width: 100%; table-layout: fixed; margin-top: .5mm; }
    th, td { border: 1px solid #000; padding: 1mm 1.2mm; vertical-align: top; overflow-wrap: break-word; }
    th { font-size: 8pt; font-weight: 700; text-transform: uppercase; text-align: center; padding: .6mm 1mm; }
    td.qtd { font-size: 13pt; font-weight: 700; text-align: center; vertical-align: middle; }
    td.sku { font-family: 'Courier New', monospace; font-weight: 700; font-size: 10pt; word-break: break-all; }
    td.nome { font-size: 10pt; }
    td.ped { font-weight: 700; font-size: 11pt; text-align: center; vertical-align: middle; }
    td.ped .dia { font-size: 8.5pt; margin-top: 1mm; }
    td.mkt { font-family: 'Courier New', monospace; font-size: 8.5pt; word-break: break-all; vertical-align: middle; }
    td.loja { font-size: 9pt; vertical-align: middle; }
    td.cli { font-size: 9pt; vertical-align: middle; }
    col.c-qtd { width: 11mm; }
    col.c-sku { width: 32mm; }
    /* 142 mm úteis em paisagem: marketplace (16 dígitos) e SKU cabem
       inteiros numa linha; o resto vai pro nome do produto. */
    col.c-ped { width: 16mm; }
    col.c-mkt { width: 28mm; }
    col.c-loja { width: 15mm; }
    col.c-cli { width: 20mm; }
    col.c-qtd2 { width: 9mm; }
    col.c-sku2 { width: 23mm; }
    tr, tbody { break-inside: avoid; }
    /* Cada dupla de pedidos da conferência = uma folha própria. */
    .pagped { break-before: page; page-break-before: always; }
    /* A folha inteira é dos 2 pedidos: a tabela estica até o rodapé
       (92 mm úteis − título) e a sobra é distribuída entre as linhas —
       sem faixa branca embaixo (Eduardo, 2026-08-27 "tem que ocupar o
       espaço em branco certinho"). Texto centralizado na vertical. */
    .pagped table { height: 84mm; }
    .pagped td { vertical-align: middle; }
  </style></head><body>
    <div class="cab">
      <h1>PREVISÃO — ${_esc(dataBR)}</h1>
      <div class="sub">${porPedido.size} pedido(s) · ${linhas.length} item(ns) — ${hojeItens.length} hoje · ${amanhaItens.length} amanhã · impresso ${_esc(hora)}</div>
      <div class="sub">só informação: separar agora — a etiqueta libera ao longo do dia</div>
    </div>
    ${hojeItens.length ? `<div class="sec">Separar — para HOJE</div>${tabelaSeparar(hojeItens)}` : ''}
    ${amanhaItens.length ? `<div class="sec">Separar — para AMANHÃ (já adiantar)</div>${tabelaSeparar(amanhaItens)}` : ''}
    ${paginasConf.join('')}
  </body></html>`
  // Iframe invisível (não sofre bloqueio de popup): carrega o relatório e
  // chama a impressão. Só some 1 min depois pra não matar o diálogo aberto.
  const iframe = document.createElement('iframe')
  iframe.style.position = 'fixed'
  iframe.style.right = '0'
  iframe.style.bottom = '0'
  iframe.style.width = '0'
  iframe.style.height = '0'
  iframe.style.border = '0'
  iframe.srcdoc = html
  iframe.onload = () => {
    try {
      iframe.contentWindow?.focus()
      iframe.contentWindow?.print()
      // Diálogo de impressão aberto = papel saindo: carimba "🖨 impressa"
      // nas linhas (tela + banco). Eduardo, 2026-08-26: "quando a gente
      // imprimir, ja aparecer no davinci que ja foram impressas".
      const nums = [...new Set(
        linhas.map((r) => r.pedido_bling).filter((n): n is string => !!n),
      )]
      void marcarPrevisoesImpressas(nums, linhas)
      // Papel saiu: desmarca a seleção pra não sair repetido no próximo clique.
      previsoesSel.value = new Set()
    } finally {
      setTimeout(() => iframe.remove(), 60000)
    }
  }
  document.body.appendChild(iframe)
}

// "Atrasado" = pedido com ETIQUETA gerada em dia passado e ainda não
// confirmado pela agência (situacao=83965 + em_andamento_data < hoje).
// Esse dado vem do backend (`atrasados`), porque o effective_date desses
// pedidos é a data da etiqueta (passada) — eles NÃO aparecem no filtro de
// hoje, então o frontend não conseguiria derivá-los do que está carregado.
// O chip só aparece no filtro de HOJE (data local/BRT) — é um alerta do
// que sobrou de dias anteriores.
function _localToday(): string {
  const n = new Date()
  return `${n.getFullYear()}-${String(n.getMonth() + 1).padStart(2, '0')}-${String(n.getDate()).padStart(2, '0')}`
}
const pendentesAntigosByDay = computed(() => {
  if (dia.value !== _localToday()) return []
  return pedidosAtrasadosRaw.value
})
const totalPendentesAntigos = computed(() =>
  pendentesAntigosByDay.value.reduce((s, g) => s + g.count, 0),
)
function formatDateBR(iso: string): string {
  const [, m, d] = iso.split('-')
  return `${d}/${m}`
}

// ── Ordenação clicável das colunas (estilo Excel) ────────────────────
// A ordenação é por PEDIDO, não por linha: os itens de um mesmo pedido
// ficam sempre juntos (a etiqueta é do pedido, e o checkbox/separador
// dependem disso). O critério vem da 1ª linha do grupo.
type PedidoSortKey =
  | 'data' | 'loja' | 'pedido_bling' | 'pedido_marketplace' | 'cliente'
  | 'sku' | 'produto' | 'quantidade' | 'etiqueta' | 'impressao' | 'envio'
const sortKey = ref<PedidoSortKey>('data')
const sortDir = ref<'asc' | 'desc'>('desc')
// Datas/horas abrem no mais recente; texto e número abrem no crescente.
const _SORT_DESC_PRIMEIRO: PedidoSortKey[] = ['data', 'etiqueta', 'impressao', 'envio']
function ordenarPor(key: PedidoSortKey) {
  if (sortKey.value === key) {
    sortDir.value = sortDir.value === 'asc' ? 'desc' : 'asc'
    return
  }
  sortKey.value = key
  sortDir.value = _SORT_DESC_PRIMEIRO.includes(key) ? 'desc' : 'asc'
}
// Ordem das colunas ordenáveis da aba Pedidos (a tabela renderiza os <th>
// a partir daqui — as células continuam escritas à mão, na mesma ordem).
const PEDIDO_COLS: { key: PedidoSortKey; label: string; cls: string }[] = [
  { key: 'data', label: 'Data Envio', cls: 'text-left' },
  { key: 'pedido_bling', label: 'Pedido Bling', cls: 'text-left' },
  { key: 'pedido_marketplace', label: 'Marketplace', cls: 'text-left' },
  { key: 'loja', label: 'Loja', cls: 'text-left' },
  { key: 'cliente', label: 'Cliente', cls: 'text-left' },
  { key: 'sku', label: 'SKU', cls: 'text-left' },
  { key: 'produto', label: 'Produto', cls: 'text-left' },
  { key: 'quantidade', label: 'Qtd', cls: 'text-right' },
  { key: 'etiqueta', label: 'Etiqueta', cls: 'text-center' },
  { key: 'impressao', label: 'Impressão', cls: 'text-center' },
  { key: 'envio', label: 'Envio', cls: 'text-center' },
]
function sortValue(r: PedidoRow): string | number {
  switch (sortKey.value) {
    case 'data': return r.data_envio || r.data || ''
    case 'loja': return r.loja || ''
    case 'pedido_bling': return Number(r.pedido_bling) || 0
    case 'pedido_marketplace': return r.pedido_marketplace || ''
    case 'cliente': return r.cliente || ''
    case 'sku': return r.sku || ''
    case 'produto': return r.produto || ''
    case 'quantidade': return r.quantidade
    case 'etiqueta': return r.etiqueta_em || ''
    case 'impressao': return r.etiqueta_impressa_em || ''
    // Sem instante no ledger, "enviado" ainda ordena antes de "não enviado".
    case 'envio': return r.enviado_em || (r.status === 'enviado' ? '0' : '')
  }
}

type PedidoRowWithGroup = PedidoRow & { _isFirstOfGroup: boolean; _groupSize: number }
const pedidosFilteredGrouped = computed<PedidoRowWithGroup[]>(() => {
  const grupos = new Map<string, PedidoRow[]>()
  for (const r of pedidosFiltered.value) {
    const key = r.pedido_bling || ''
    const arr = grupos.get(key)
    if (arr) arr.push(r)
    else grupos.set(key, [r])
  }
  const vazio = (v: string | number) =>
    v === '' || (v === 0 && sortKey.value === 'pedido_bling')
  const ordenados = [...grupos.entries()].sort(([ka, ga], [kb, gb]) => {
    const va = sortValue(ga[0]!)
    const vb = sortValue(gb[0]!)
    // Célula em branco vai pro fim nas DUAS direções (igual planilha).
    if (vazio(va) !== vazio(vb)) return vazio(va) ? 1 : -1
    const cmp = typeof va === 'number' && typeof vb === 'number'
      ? va - vb
      : String(va).localeCompare(String(vb), 'pt-BR', { numeric: true, sensitivity: 'base' })
    if (cmp !== 0) return sortDir.value === 'asc' ? cmp : -cmp
    return ka.localeCompare(kb)
  })
  const out: PedidoRowWithGroup[] = []
  for (const [, grupo] of ordenados) {
    grupo.forEach((r, i) => out.push({
      ...r,
      _isFirstOfGroup: i === 0,
      _groupSize: grupo.length,
    }))
  }
  return out
})

// ── Impressão de etiquetas em LOTE ───────────────────────────────────
// A seleção é por PEDIDO (não por linha): um pedido com N itens rende N
// linhas na tabela, mas UMA etiqueta só.
const etiquetasSel = ref<Set<string>>(new Set())
// Qual dos dois botões está gerando (só um roda por vez).
const imprimindoLote = ref<'etiquetas' | 'relatorio' | null>(null)
// Pedidos com etiqueta pronta, na ordem em que aparecem na tela — é essa
// ordem que vai pro PDF do lote.
const pedidosComEtiqueta = computed(() => {
  const out: string[] = []
  for (const r of pedidosFilteredGrouped.value) {
    if (r._isFirstOfGroup && r.etiqueta_disponivel && r.pedido_bling) out.push(r.pedido_bling)
  }
  return out
})
const selecionadosCount = computed(() => etiquetasSel.value.size)
const todasSelecionadas = computed(() =>
  pedidosComEtiqueta.value.length > 0
  && pedidosComEtiqueta.value.every(p => etiquetasSel.value.has(p)),
)
function toggleEtiquetaSel(pedido: string | null) {
  if (!pedido) return
  const next = new Set(etiquetasSel.value)
  if (next.has(pedido)) next.delete(pedido)
  else next.add(pedido)
  etiquetasSel.value = next
}
function toggleTodasEtiquetas() {
  etiquetasSel.value = todasSelecionadas.value
    ? new Set()
    : new Set(pedidosComEtiqueta.value)
}
// `comRelatorio`: o backend anexa o relatório de conferência como últimas
// páginas do mesmo PDF (etiquetas em cima, relatório embaixo).
async function imprimirLote(comRelatorio = false) {
  const pedidos = pedidosComEtiqueta.value.filter(p => etiquetasSel.value.has(p))
  if (!pedidos.length || imprimindoLote.value) return
  // Reimprimir é permitido (etiqueta pode rasgar), mas avisa — o pedido
  // do usuário é justamente não duplicar sem querer.
  const jaImpressos = pedidos.filter(p =>
    pedidosFilteredGrouped.value.some(r => r.pedido_bling === p && r.etiqueta_impressa_em),
  )
  if (jaImpressos.length && !confirm(
    `${jaImpressos.length} etiqueta(s) já foram impressas (${jaImpressos.join(', ')}). Imprimir de novo?`,
  )) return

  imprimindoLote.value = comRelatorio ? 'relatorio' : 'etiquetas'
  try {
    // fetch cru (não useApi): a resposta é um PDF binário, não JSON.
    const resp = await fetch('/api/estoque/pedidos/etiquetas', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        pedidos, incluir_relatorio: comRelatorio, tag: tagImpressao() || null,
      }),
    })
    if (!resp.ok) throw new Error(String(resp.status))
    const blob = await resp.blob()
    const objUrl = URL.createObjectURL(blob)
    // <a download> em vez de window.open: depois do await o navegador já perdeu
    // o "user activation" do clique e bloqueia o popup SEM avisar. Quanto mais
    // etiquetas, mais demora a resposta e mais certo é o bloqueio — por isso 5
    // funcionava e 8 não. Download não depende de gesto, então não tem limite.
    const a = document.createElement('a')
    a.href = objUrl
    a.download = comRelatorio
      ? `etiquetas_relatorio_${pedidos.length}.pdf`
      : `etiquetas_lote_${pedidos.length}.pdf`
    document.body.appendChild(a)
    a.click()
    a.remove()
    // Só revoga depois que o navegador pegou o blob.
    setTimeout(() => URL.revokeObjectURL(objUrl), 60_000)
    etiquetasSel.value = new Set()
    await loadPedidos()  // repuxa pra marcar "Impressa"
  } catch {
    alert('Não foi possível gerar o lote de etiquetas.')
  } finally {
    imprimindoLote.value = null
  }
}

// ── Relatório imprimível dos pedidos selecionados ────────────────────
// Espelha as colunas da tela (Data Envio | Loja + corte | Pedido Bling |
// Marketplace | Cliente | SKU | Qtd | Produto), ordenado pelo nome do
// comprador. Um pedido com N itens rende N linhas (cada uma com seu
// SKU/qtd). Abre numa aba nova já com o diálogo de impressão —
// window.open síncrono no clique, então o popup não é bloqueado.
function imprimirRelatorio() {
  const sel = etiquetasSel.value
  if (!sel.size) return
  const rows = pedidosFilteredGrouped.value.filter(
    r => r.pedido_bling && sel.has(r.pedido_bling),
  )
  if (!rows.length) return
  const sorted = [...rows].sort((a, b) =>
    (a.cliente || '').localeCompare(b.cliente || '', 'pt-BR', { sensitivity: 'base' })
    || (a.pedido_bling || '').localeCompare(b.pedido_bling || ''))
  const esc = (s: string | null | undefined) => (s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  // Corte na versão papel: mesmo texto da tela; a cor Tailwind vira uma
  // classe de print (vermelho = estourou, laranja = hoje, cinza = futuro).
  const corteHtml = (r: PedidoRow) => {
    const info = corteInfo(r)
    if (!info) return ''
    const cls = info.cls.includes('red')
      ? 'corte-vermelho'
      : info.cls.includes('amber') ? 'corte-laranja' : 'corte-cinza'
    return `<div class="corte ${cls}">${esc(info.label)}</div>`
  }
  const linhas = sorted.map(r => `<tr>
      <td>${esc(r.data_envio ? r.data_envio.slice(0, 10) : '')}</td>
      <td class="loja">${esc(r.loja)}${corteHtml(r)}</td>
      <td>${esc(r.pedido_bling)}</td>
      <td class="mono">${esc(r.pedido_marketplace)}</td>
      <td class="nome">${esc(r.cliente)}</td>
      <td class="mono">${esc(r.sku)}</td>
      <td>${r.quantidade}</td>
      <td class="desc">${esc(r.produto)}</td>
    </tr>`).join('')
  const diaLabel = dia.value
    ? `${dia.value.slice(8)}/${dia.value.slice(5, 7)}/${dia.value.slice(0, 4)}`
    : ''
  const html = `<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<title>Relatório de pedidos ${esc(diaLabel)}</title>
<style>
  @page { size: A4 landscape; margin: 8mm; }
  body { font-family: Calibri, Arial, sans-serif; margin: 16px; color: #000; }
  h1 { font-size: 13px; font-weight: 600; margin: 0 0 8px; }
  table { border-collapse: collapse; width: 100%; font-size: 11px; }
  th, td { border: 1px solid #000; padding: 3px 5px; text-align: center; }
  th { font-weight: 700; }
  td.loja { color: #7cb342; font-weight: 600; }
  td.nome, td.desc { text-align: left; }
  td.mono { font-family: Consolas, monospace; font-size: 10px; }
  .corte { font-size: 8px; font-weight: 600; margin-top: 1px; }
  .corte-vermelho { color: #c62828; }
  .corte-laranja { color: #b45309; }
  .corte-cinza { color: #666; }
  @media print { body { margin: 0; } h1 { display: none; } }
</style></head><body>
<h1>Relatório de pedidos ${esc(diaLabel)} — ${sel.size} pedido(s), ${sorted.length} linha(s)</h1>
<table>
  <thead><tr>
    <th>Data Envio</th><th>Nome da Loja</th><th>Pedido Bling</th>
    <th>Pedido Marketplace</th><th>Cliente</th><th>Código (SKU)</th>
    <th>Qtd</th><th>Descrição</th>
  </tr></thead>
  <tbody>${linhas}</tbody>
</table>
<script>window.onload = () => { window.focus(); window.print() }<\/script>
</body></html>`
  const w = window.open('', '_blank')
  if (!w) {
    alert('O navegador bloqueou a janela do relatório. Libere popups para o DaVinci.')
    return
  }
  w.document.write(html)
  w.document.close()
}

// Tag extraction from SKU — mirrors the backend rule subset that's
// derivable from the SKU string alone:
//   * fake.* prefix      → 'fake'
//   * .ci/.pi/.ra/.sa/.sp/.us/.cd suffix → that tag
//   * .<numero> suffix   → 'mala' (number = tamanho, padrão das malas)
//   * outros             → 'outros' (kits sem suffix, insumos, eletro, etc)
// Composite SKUs (a+b+c) usam o primeiro pedaço — em produção quase
// sempre todos os pedaços têm a mesma tag. NÃO replica a lógica
// completa do backend (que considera SKUs sem dot etc); é uma
// aproximação suficiente pro contador visual.
const _TAG_SUFFIXES = new Set(['ci', 'pi', 'ra', 'sa', 'sp', 'us', 'cd'])
function extractPedidoTag(sku: string | null): string {
  if (!sku) return 'outros'
  const firstPiece = sku.split('+')[0].toLowerCase().trim()
  if (firstPiece.startsWith('fake.')) return 'fake'
  const parts = firstPiece.split('.')
  if (parts.length < 2) return 'outros'
  const suffix = parts[parts.length - 1]
  if (_TAG_SUFFIXES.has(suffix)) return suffix
  // Suffix numérico (12, 24, 18, etc) = tamanho de mala
  if (/^\d+$/.test(suffix)) return 'mala'
  return 'outros'
}

// Breakdown por tag (com base em pedidosFiltered, então respeita
// busca + filtros). Ordem fixa pra o display ficar estável.
const _TAG_DISPLAY_ORDER = ['sa', 'ci', 'pi', 'ra', 'sp', 'us', 'cd', 'fake', 'mala', 'outros']
const pedidosCountByTag = computed(() => {
  const counts: Record<string, number> = {}
  for (const p of pedidosFiltered.value) {
    const t = extractPedidoTag(p.sku)
    counts[t] = (counts[t] || 0) + 1
  }
  // Retorna só tags com contagem > 0, na ordem de display.
  return _TAG_DISPLAY_ORDER
    .map((t) => ({ tag: t, count: counts[t] || 0 }))
    .filter((x) => x.count > 0)
})

// Percentual conferido na aba Estoque DO DIA VISÍVEL — usado pelo
// header de progresso e pelo botão "Conferir todos". Difere de
// `conferenciaHoje` que é sempre HOJE (fonte da verdade pro bloqueio).
const conferidoPercent = computed(() => {
  const total = produtos.value.length
  if (total === 0) return 0
  const checked = produtos.value.filter((p) => p.conferido).length
  return Math.round((checked / total) * 100)
})

// Bloqueio da aba Envios: admin entra sempre; operador só com 100%
// da aba Estoque conferido HOJE (não do dia que ele estiver visualizando).
const canAccessEnvios = computed(() => {
  if (isAdmin.value) return true
  // Bypass específico pro churchill (sub-gerente): mesmo perfil
  // operacional dos admins em termos de acesso a Envios, mas com
  // role=user pra manter outras restrições do admin. Mesma pessoa
  // que já tem bypass em `canUseTagFilter` acima (lá identificada
  // por name). Se outro sub-gerente entrar no time, refatorar pra
  // permission discreta (User.permissions JSONB).
  if (auth.user?.email === 'maconer06@tuta.com') return true
  return conferenciaHoje.value.percent >= 100
})

async function conferirTodos() {
  const unchecked = produtos.value.filter((p) => !p.conferido)
  if (unchecked.length === 0) return
  if (!window.confirm(`Confirmar que você conferiu os ${unchecked.length} itens restantes?`)) return
  // Optimistic UI: marca local imediatamente; em caso de falha
  // individual reverte só aquele item (loadEstoque pegaria o resto).
  await Promise.all(
    unchecked.map(async (row) => {
      row.conferido = true
      try {
        await toggleCheck('estoque', row.sku, dia.value, true)
      } catch {
        row.conferido = false
      }
    }),
  )
  if (dia.value === isoToday()) void refreshConferenciaHoje()
}
</script>

<template>
  <div class="controle-estoque space-y-3 p-4">
    <!-- Header + tabs -->
    <div class="flex flex-wrap items-center gap-3">
      <div class="flex items-center gap-2">
        <Boxes class="h-5 w-5 text-primary" />
        <h1 class="text-xl font-semibold">Controle de Estoque</h1>
      </div>
      <button
        class="ml-auto inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
        :disabled="syncing"
        :title="'Busca saldos atualizados direto do Bling para os produtos visíveis'"
        @click="syncFromBling"
      >
        <RefreshCw class="size-3.5" :class="{ 'animate-spin': syncing }" />
        {{ syncing ? 'Sincronizando…' : 'Recarregar' }}
      </button>
      <span
        v-if="syncToast"
        class="text-xs text-muted-foreground bg-muted/40 border rounded px-2 py-1"
      >{{ syncToast }}</span>
      <button
        v-if="canAtualizarBling"
        class="inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
        :disabled="blingJobRunning"
        :title="'Puxa os produtos do Bling, atualiza o estoque e remove os que foram excluídos no Bling'"
        @click="atualizarBling"
      >
        <Loader2 v-if="blingJobRunning" class="size-3.5 animate-spin" />
        <Download v-else class="size-3.5" />
        {{ blingJobRunning ? 'Atualizando…' : 'Atualizar Bling' }}
      </button>
      <span
        v-if="blingJobToast"
        class="text-xs text-muted-foreground bg-muted/40 border rounded px-2 py-1"
      >{{ blingJobToast }}</span>
      <button
        v-if="isAdmin"
        class="inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm hover:bg-muted"
        :title="'Manda via Threema os pedidos em Aguardando Cancelamento por falta de estoque (só admins)'"
        @click="informarEstoqueOpen = true"
      >
        <Megaphone class="size-3.5" />
        Informar
      </button>
      <div class="flex gap-1 rounded-md bg-muted/40 p-1 w-fit flex-wrap">
        <button
          v-for="t in visibleTabs"
          :key="t"
          class="px-3 py-1.5 rounded text-sm transition-colors inline-flex items-center gap-1.5"
          :class="tab === t ? 'bg-background shadow-sm font-medium' : 'hover:bg-background/60 text-muted-foreground'"
          @click="tab = t"
        >
          <Boxes v-if="t === 'estoque'" class="size-4" />
          <ClipboardList v-else-if="t === 'pedidos'" class="size-4" />
          <Truck v-else-if="t === 'envios'" class="size-4" />
          <AlertTriangle v-else-if="t === 'estoque-negativo'" class="size-4" />
          <FileUp v-else class="size-4" />
          {{
            t === 'estoque' ? 'Estoque' :
            t === 'pedidos' ? 'Pedidos' :
            t === 'envios' ? 'Envios' :
            t === 'estoque-negativo' ? 'Estoque Negativo' :
            'Upload NF'
          }}
        </button>
      </div>
    </div>

    <!-- Filters bar -->
    <div class="flex flex-wrap items-center gap-2 bg-muted/30 border rounded-md px-3 py-2 text-xs">
      <input
        v-model="search"
        type="search"
        placeholder="Buscar SKU, nome, pedido…"
        class="h-7 border rounded px-2 bg-background min-w-[200px]"
      />
      <template v-if="tab !== 'envios'">
        <label class="inline-flex items-center gap-1">
          Dia:
          <input v-model="dia" type="date" class="h-7 border rounded px-2 bg-background" />
        </label>
      </template>
      <template v-else>
        <label class="inline-flex items-center gap-1">
          De:
          <input v-model="enviosInicio" type="date" class="h-7 border rounded px-2 bg-background" />
        </label>
        <label class="inline-flex items-center gap-1">
          Até:
          <input v-model="enviosFim" type="date" class="h-7 border rounded px-2 bg-background" />
        </label>
        <!-- Filtro em default ("todas/todos") fica esmaecido pra diferenciar
             na hora o que está ativo (pedido do Eduardo, 2026-08-24) — vale
             pra todos os selects da barra. As opções reais forçam
             text-foreground porque o Chrome herda a cor do select pra lista
             aberta; "Ordenar" fica de fora (sempre tem valor real). -->
        <label class="inline-flex items-center gap-1">
          Conferência:
          <select
            v-model="conferidoFilter"
            class="h-7 border rounded px-2 bg-background"
            :class="conferidoFilter === 'all' ? 'text-muted-foreground' : ''"
          >
            <option value="all" class="text-muted-foreground">todos</option>
            <option value="conferidos" class="text-foreground">conferidos</option>
            <option value="nao_conferidos" class="text-foreground">não conferidos</option>
          </select>
        </label>
      </template>
      <label
        v-if="canUseTagFilter || (isGerenteEtiquetas && tab === 'pedidos')"
        class="inline-flex items-center gap-1"
      >
        Tag:
        <select
          v-model="tagOverride"
          class="h-7 border rounded px-2 bg-background"
          :class="tagOverride === '' ? 'text-muted-foreground' : ''"
        >
          <option value="" class="text-muted-foreground">todas</option>
          <option
            v-for="opt in TAG_OPTIONS"
            :key="opt.slug"
            :value="opt.slug"
            class="text-foreground"
          >
            {{ opt.label }}
          </option>
        </select>
      </label>
      <!-- Stock-presence filter — only meaningful on the Estoque tab.
           Backend ignores the param for Pedidos / Envios, so it's safe
           to leave the dropdown visible everywhere, but we keep it
           Estoque-only to avoid implying it affects the other tabs. -->
      <label v-if="tab === 'estoque'" class="inline-flex items-center gap-1">
        Estoque:
        <select
          v-model="estoqueFilter"
          class="h-7 border rounded px-2 bg-background"
          :class="estoqueFilter === 'all' ? 'text-muted-foreground' : ''"
        >
          <option value="all" class="text-muted-foreground">todos</option>
          <option value="com" class="text-foreground">com estoque</option>
          <option value="sem" class="text-foreground">sem estoque</option>
        </select>
      </label>
      <label v-if="tab === 'pedidos'" class="inline-flex items-center gap-1">
        Status:
        <select
          v-model="statusFilter"
          class="h-7 border rounded px-2 bg-background"
          :class="statusFilter === 'all' ? 'text-muted-foreground' : ''"
        >
          <option value="all" class="text-muted-foreground">todos</option>
          <option value="enviado" class="text-foreground">enviado</option>
          <option value="nao_enviado" class="text-foreground">não enviado</option>
          <option value="previsao" class="text-foreground">previsão</option>
        </select>
      </label>
      <label v-if="tab === 'pedidos'" class="inline-flex items-center gap-1">
        Etiqueta:
        <select
          v-model="etiquetaFilter"
          class="h-7 border rounded px-2 bg-background"
          :class="etiquetaFilter === 'all' ? 'text-muted-foreground' : ''"
        >
          <option value="all" class="text-muted-foreground">todas</option>
          <option value="impressa" class="text-foreground">impressa</option>
          <option value="nao_impressa" class="text-foreground">não impressa</option>
          <option value="sem" class="text-foreground">sem etiqueta</option>
        </select>
      </label>
      <label v-if="tab === 'pedidos'" class="inline-flex items-center gap-1">
        Plataforma:
        <select
          v-model="plataformaFilter"
          class="h-7 border rounded px-2 bg-background"
          :class="plataformaFilter === '' ? 'text-muted-foreground' : ''"
        >
          <option value="" class="text-muted-foreground">todas</option>
          <option v-for="p in plataformaOptions" :key="p" :value="p" class="text-foreground">
            {{ p }}
          </option>
        </select>
      </label>
      <label v-if="tab === 'pedidos'" class="inline-flex items-center gap-1">
        Loja:
        <select
          v-model="lojaFilter"
          class="h-7 border rounded px-2 bg-background max-w-[180px]"
          :class="lojaFilter === '' ? 'text-muted-foreground' : ''"
        >
          <option value="" class="text-muted-foreground">todas</option>
          <option v-for="l in lojaOptions" :key="l" :value="l" class="text-foreground">
            {{ l }}
          </option>
        </select>
      </label>
      <!-- Mesma ordenação dos cabeçalhos clicáveis (estado compartilhado):
           aqui fica visível qual coluna manda e pra que lado. -->
      <label v-if="tab === 'pedidos'" class="inline-flex items-center gap-1">
        Ordenar:
        <select
          class="h-7 border rounded px-2 bg-background"
          :value="sortKey"
          @change="ordenarPor(($event.target as HTMLSelectElement).value as PedidoSortKey)"
        >
          <option v-for="col in PEDIDO_COLS" :key="col.key" :value="col.key">
            {{ col.label }}
          </option>
        </select>
        <button
          type="button"
          class="h-7 px-1.5 border rounded bg-background inline-flex items-center"
          :title="sortDir === 'asc' ? 'Crescente (A→Z, menor→maior)' : 'Decrescente (Z→A, maior→menor)'"
          @click="sortDir = sortDir === 'asc' ? 'desc' : 'asc'"
        >
          <ArrowUp v-if="sortDir === 'asc'" class="size-3.5" />
          <ArrowDown v-else class="size-3.5" />
        </button>
      </label>
      <div class="ml-auto inline-flex items-center gap-2 text-muted-foreground">
        <Loader2 v-if="loading" class="size-3 animate-spin" />
        <span v-if="errorText" class="text-destructive">{{ errorText }}</span>
      </div>
    </div>

    <!-- TAB: ESTOQUE ────────────────────────────────────────────────── -->
    <!-- Barra de progresso + "Conferir todos". Botão sempre visível
         (sem mínimo de %); ao 100% troca pro badge verde. -->
    <div v-if="tab === 'estoque' && produtos.length > 0" class="flex items-center gap-3 text-xs">
      <template v-if="conferidoPercent < 100">
        <button
          class="px-3 py-1.5 bg-emerald-600 text-white text-[11px] font-medium rounded hover:bg-emerald-700"
          @click="conferirTodos"
        >
          ✓ Conferir todos
        </button>
        <span class="text-muted-foreground">
          {{ conferidoPercent }}% conferido ({{ produtos.filter((p) => p.conferido).length }}/{{ produtos.length }})
        </span>
      </template>
      <span v-else class="text-emerald-600 font-medium">
        ✓ Estoque 100% conferido
      </span>
    </div>
    <div v-if="tab === 'estoque'" class="border rounded-md overflow-x-auto">
      <table class="grid-table w-full text-xs border-collapse">
        <colgroup>
          <col style="width: 80px" />   <!-- SKU -->
          <col style="width: 160px" />  <!-- Produto -->
          <col style="width: 50px" />   <!-- Entrada Qtd -->
          <col style="width: 50px" />   <!-- Saída Qtd -->
          <col style="width: 60px" />   <!-- Saldo Atual -->
          <col style="width: 55px" />   <!-- Saldo Reserva -->
          <col style="width: 40px" />   <!-- Conf -->
        </colgroup>
        <thead>
          <tr class="bg-muted/50">
            <th class="text-left text-[11px] font-semibold" colspan="2">Identificação</th>
            <th class="text-center text-[11px] font-semibold bg-amber-50 dark:bg-amber-900/20">Entrada</th>
            <th class="text-center text-[11px] font-semibold bg-amber-50 dark:bg-amber-900/20">Saída</th>
            <th class="text-center text-[11px] font-semibold bg-emerald-50 dark:bg-emerald-900/20" colspan="2">Saldo</th>
            <th class="text-center text-[11px] font-semibold bg-gray-100 dark:bg-gray-800/40">Conf.</th>
          </tr>
          <tr class="bg-muted/30 text-[10px] uppercase tracking-wide">
            <th class="text-left">SKU</th>
            <th class="text-left">Produto</th>
            <th class="text-right bg-amber-50/60 dark:bg-amber-900/10">Qtd</th>
            <th class="text-right bg-amber-50/60 dark:bg-amber-900/10">Qtd</th>
            <th class="text-right bg-emerald-50/60 dark:bg-emerald-900/10">Atual</th>
            <th class="text-right bg-emerald-50/60 dark:bg-emerald-900/10">Reserva</th>
            <th class="text-center bg-gray-100/60 dark:bg-gray-800/30">✓</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="produtosFiltered.length === 0">
            <td colspan="7" class="py-6 text-center text-muted-foreground">
              Nenhum produto para esse filtro.
            </td>
          </tr>
          <tr
            v-for="row in produtosFiltered" :key="row.sku"
            class="hover:bg-muted/20"
            :class="row.conferido ? 'bg-emerald-50/40 dark:bg-emerald-900/10' : ''"
          >
            <td class="font-mono text-[11px] truncate" :title="row.sku">{{ row.sku }}</td>
            <td class="truncate" :title="row.nome">{{ row.nome }}</td>
            <!-- Entrada: só Qtd (stack vertical das N entradas). -->
            <td class="bg-amber-50/40 dark:bg-amber-900/5 align-top text-right">
              <div v-if="row.entradas.length === 0" class="text-muted-foreground/60">—</div>
              <div v-else class="space-y-0.5">
                <div
                  v-for="e in row.entradas" :key="e.movement_id"
                  class="font-semibold text-amber-700 dark:text-amber-300 leading-5 h-5"
                >
                  {{ e.qty }}
                </div>
              </div>
            </td>
            <td
              class="text-right bg-amber-50/40 dark:bg-amber-900/5"
              :class="row.saida_qty_total > 0 ? 'font-semibold text-amber-700 dark:text-amber-300' : 'text-muted-foreground/60'"
            >
              {{ row.saida_qty_total || '—' }}
            </td>
            <td
              class="text-right bg-emerald-50/40 dark:bg-emerald-900/5 font-semibold"
              :class="row.saldo_fisico === 0 ? 'text-red-600' : 'text-emerald-700'"
            >
              {{ row.saldo_fisico }}
            </td>
            <td class="text-right bg-emerald-50/40 dark:bg-emerald-900/5 text-muted-foreground">
              {{ row.reserva || '—' }}
            </td>
            <td class="text-center bg-gray-100/40 dark:bg-gray-800/20">
              <input
                type="checkbox"
                :checked="row.conferido"
                class="cursor-pointer"
                @change="toggleProduto(row)"
              />
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- TAB: PEDIDOS ────────────────────────────────────────────────── -->
    <!-- Stats bar: total + breakdown por tag. Reflete pedidosFiltered
         (search + filtros). Tag extraída do SKU no frontend — ver
         extractPedidoTag(). -->
    <div v-if="tab === 'pedidos'" class="flex flex-wrap items-center gap-2 text-xs">
      <span class="inline-flex items-center gap-1.5 rounded-md bg-primary text-primary-foreground px-2.5 py-1 font-semibold">
        Total: {{ totalPedidos }} pedidos
      </span>
      <span class="inline-flex items-center gap-1.5 rounded-md bg-emerald-600 text-white px-2.5 py-1 font-semibold">
        Enviados: {{ pedidosEnviadosCount }}
      </span>
      <span class="inline-flex items-center gap-1.5 rounded-md bg-red-600 text-white px-2.5 py-1 font-semibold">
        Não enviados: {{ pedidosNaoEnviadosCount }}
      </span>
      <!-- Previsão = pedidos "Em aberto" no Bling que vão emitir NF/etiqueta
           no dia — o pessoal do envio separa de manhã (etiqueta ML ~meio-dia). -->
      <span
        class="inline-flex items-center gap-1.5 rounded-md bg-yellow-500 text-white px-2.5 py-1 font-semibold"
        title="Pedidos em aberto no Bling — NF e etiqueta ainda não geradas; devem sair hoje"
      >
        Previsão: {{ pedidosPrevisaoCount }}
      </span>
      <!-- Relatório das previsões no padrão da etiqueta térmica (10×15):
           só informação — separa o produto de manhã e cola a etiqueta
           quando ela liberar (~meio-dia). -->
      <button
        v-if="pedidosPrevisaoCount > 0"
        class="inline-flex items-center gap-1 rounded-md border border-yellow-500 text-yellow-700 dark:text-yellow-400 px-2 py-1 font-semibold hover:bg-yellow-500/10"
        :title="previsoesSelCount > 0
          ? 'Imprime SÓ as previsões marcadas na tabela (checkbox amarelo)'
          : 'Imprime a lista das previsões na impressora térmica (etiqueta 10×15) — pra escolher só algumas, marque os checkboxes amarelos na tabela'"
        @click="imprimirPrevisoes"
      >
        <Printer class="size-3.5" />
        {{ previsoesSelCount > 0 ? `imprimir ${previsoesSelCount} marcada${previsoesSelCount !== 1 ? 's' : ''}` : 'imprimir' }}
      </button>
      <div v-if="totalPendentesAntigos > 0" class="relative inline-block group">
        <span class="inline-flex items-center gap-1.5 rounded-md bg-amber-500 text-white px-2.5 py-1 font-semibold cursor-help">
          ⚠️ {{ totalPendentesAntigos }} atrasado{{ totalPendentesAntigos !== 1 ? 's' : '' }}
        </span>
        <div class="absolute hidden group-hover:block top-full left-0 mt-1 bg-popover border rounded-md shadow-lg p-3 z-20 min-w-[180px]">
          <div class="text-xs font-semibold text-muted-foreground mb-2">
            Pendentes por dia de criação:
          </div>
          <div class="space-y-1">
            <div
              v-for="grupo in pendentesAntigosByDay"
              :key="grupo.date"
              class="flex items-center justify-between gap-3 text-xs"
            >
              <span>{{ formatDateBR(grupo.date) }}</span>
              <span class="font-semibold text-amber-600">
                {{ grupo.count }} {{ grupo.count !== 1 ? 'pedidos' : 'pedido' }}
              </span>
            </div>
          </div>
        </div>
      </div>
      <span
        v-for="bucket in pedidosCountByTag" :key="bucket.tag"
        class="inline-flex items-center gap-1 rounded-md border bg-background px-2 py-0.5"
        :title="`Pedidos com tag ${bucket.tag.toUpperCase()}`"
      >
        <span class="uppercase font-semibold tracking-wide text-[10px]">{{ bucket.tag }}</span>
        <span class="text-foreground font-mono">{{ bucket.count }}</span>
      </span>
      <!-- Impressão em lote: junta as etiquetas selecionadas num PDF só. -->
      <button
        v-if="pedidosComEtiqueta.length > 0"
        type="button"
        class="ml-auto inline-flex items-center gap-1.5 rounded-md border bg-primary text-primary-foreground px-2.5 py-1 font-semibold disabled:opacity-50"
        :disabled="selecionadosCount === 0 || imprimindoLote !== null"
        title="Junta as etiquetas selecionadas num PDF único"
        @click="imprimirLote(false)"
      >
        <Printer class="size-3.5" />
        {{ imprimindoLote === 'etiquetas' ? 'Gerando…' : `Imprimir selecionadas (${selecionadosCount})` }}
      </button>
      <!-- Etiquetas + relatório num PDF só: as etiquetas primeiro (é o que
           cola no volume), o relatório de conferência nas últimas páginas. -->
      <button
        v-if="pedidosComEtiqueta.length > 0"
        type="button"
        class="inline-flex items-center gap-1.5 rounded-md border bg-primary text-primary-foreground px-2.5 py-1 font-semibold disabled:opacity-50"
        :disabled="selecionadosCount === 0 || imprimindoLote !== null"
        title="Um PDF só: etiquetas em cima, relatório de conferência embaixo"
        @click="imprimirLote(true)"
      >
        <Printer class="size-3.5" />
        {{ imprimindoLote === 'relatorio' ? 'Gerando…' : 'Etiquetas + relatório' }}
      </button>
      <!-- Relatório imprimível (papel) dos pedidos selecionados: Loja,
           nº pedido, cliente, SKU, qtd, descrição — ordenado por cliente. -->
      <button
        v-if="pedidosComEtiqueta.length > 0"
        type="button"
        class="inline-flex items-center gap-1.5 rounded-md border bg-background px-2.5 py-1 font-semibold disabled:opacity-50"
        :disabled="selecionadosCount === 0"
        title="Abre uma página pronta pra imprimir com os pedidos selecionados"
        @click="imprimirRelatorio"
      >
        <FileText class="size-3.5" />
        Imprimir relatório
      </button>
    </div>
    <div v-if="tab === 'pedidos'" class="border rounded-md overflow-x-auto">
      <table class="grid-table w-full text-xs border-collapse">
        <thead>
          <tr class="bg-muted/30 text-[10px] uppercase tracking-wide">
            <th class="text-center w-8">
              <input
                type="checkbox"
                class="cursor-pointer"
                :checked="todasSelecionadas"
                :disabled="pedidosComEtiqueta.length === 0"
                title="Selecionar todos os pedidos com etiqueta"
                @change="toggleTodasEtiquetas"
              />
            </th>
            <!-- Cabeçalhos clicáveis: ordenam a tabela como numa planilha
                 (2º clique inverte). Os itens de um pedido andam juntos. -->
            <th v-for="col in PEDIDO_COLS" :key="col.key" :class="col.cls">
              <button
                type="button"
                class="inline-flex items-center gap-0.5 uppercase tracking-wide hover:text-foreground"
                :class="sortKey === col.key ? 'text-foreground font-semibold' : ''"
                :title="`Ordenar por ${col.label}`"
                @click="ordenarPor(col.key)"
              >
                {{ col.label }}
                <ArrowDown v-if="sortKey === col.key && sortDir === 'desc'" class="size-3" />
                <ArrowUp v-else-if="sortKey === col.key" class="size-3" />
              </button>
            </th>
            <th class="text-left bg-emerald-50/40">Obs</th>
            <th class="text-center">Imprimir Etiqueta</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="pedidosFiltered.length === 0">
            <td colspan="14" class="py-6 text-center text-muted-foreground">
              Nenhum pedido para esse dia.
            </td>
          </tr>
          <tr
            v-for="(row, idx) in pedidosFilteredGrouped" :key="row.id"
            class="hover:bg-muted/20"
            :class="{ 'border-t-2 border-t-muted-foreground/30': row._isFirstOfGroup && idx > 0 }"
          >
            <!-- Só a 1ª linha do pedido tem checkbox: a etiqueta é do PEDIDO. -->
            <td class="text-center">
              <input
                v-if="row._isFirstOfGroup && row.etiqueta_disponivel && row.pedido_bling"
                type="checkbox"
                class="cursor-pointer"
                :checked="etiquetasSel.has(row.pedido_bling)"
                @change="toggleEtiquetaSel(row.pedido_bling)"
              />
              <!-- Previsão: checkbox amarelo escolhe QUAIS previsões saem no
                   papel (Eduardo, 2026-08-27). Nada marcado = imprime todas. -->
              <input
                v-else-if="row._isFirstOfGroup && row.status === 'previsao' && row.pedido_bling"
                type="checkbox"
                class="cursor-pointer accent-yellow-500"
                :checked="previsoesSel.has(row.pedido_bling)"
                title="Marcar esta previsão pra imprimir (nada marcado = imprime todas)"
                @change="togglePrevisaoSel(row.pedido_bling)"
              />
            </td>
            <td class="whitespace-nowrap">
              {{ row._isFirstOfGroup ? (row.data_envio ? row.data_envio.slice(0, 10) : '—') : '' }}
            </td>
            <td class="font-mono text-[11px]" :class="{ 'text-muted-foreground/40': !row._isFirstOfGroup }">
              {{ row._isFirstOfGroup ? (row.pedido_bling || '—') : '' }}
            </td>
            <td class="font-mono text-[11px]">{{ row.pedido_marketplace || '—' }}</td>
            <td>
              {{ row.loja || '—' }}
              <!-- Horário de corte ("despachar até" do marketplace). Só em
                   pedido não enviado; some sozinho quando o envio confirma. -->
              <div
                v-if="corteInfo(row)"
                class="text-[9px] mt-0.5 whitespace-nowrap"
                :class="corteInfo(row)!.cls"
                title="Despachar até (prazo do marketplace)"
              >
                {{ corteInfo(row)!.label }}
              </div>
            </td>
            <!-- Nome de quem comprou (nome_destinatario do Bling). -->
            <td class="truncate max-w-[160px]" :title="row.cliente || ''">
              {{ row.cliente || '—' }}
            </td>
            <td class="font-mono text-[11px]">{{ row.sku || '—' }}</td>
            <td class="truncate max-w-[280px]" :title="row.produto || ''">{{ row.produto || '—' }}</td>
            <td class="text-right">{{ row.quantidade }}</td>
            <!-- Hora em que a etiqueta CHEGOU (nf_etiqueta_arquivo.created_at). -->
            <td class="text-center whitespace-nowrap text-[11px]" title="Hora que a etiqueta chegou">
              {{ etiquetaHora(row) || '—' }}
            </td>
            <!-- Hora da 1ª IMPRESSÃO da etiqueta (impressa_em). -->
            <td class="text-center whitespace-nowrap text-[11px]" title="Hora que a etiqueta foi impressa">
              {{ impressaHora(row) || '—' }}
            </td>
            <!-- Ex-coluna "Status". Mesma lógica de sempre (badge por
                 situacao); só o TEXTO do badge verde muda: mostra a hora do
                 envio quando o ledger tem o instante, senão "Enviado". -->
            <td class="text-center">
              <span
                class="inline-block px-1.5 py-0.5 rounded text-[10px] font-medium whitespace-nowrap"
                :class="row.status === 'enviado'
                  ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300'
                  : row.status === 'previsao'
                    ? 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-300'
                    : 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300'"
                :title="row.status === 'previsao'
                  ? (previsaoDia(row) === 'amanha'
                    ? 'Em aberto no Bling — corte amanhã: dá pra já ir adiantando a separação'
                    : 'Em aberto no Bling — NF e etiqueta ainda não geradas; deve sair hoje')
                  : row.status === 'enviado' && envioHora(row) ? 'Hora que o envio confirmou' : undefined"
              >
                {{ row.status === 'enviado'
                  ? (envioHora(row) || 'Enviado')
                  : row.status === 'previsao'
                    ? (previsaoDia(row) === 'amanha' ? 'Previsão · amanhã' : 'Previsão · hoje')
                    : 'Não enviado' }}
              </span>
              <!-- Papel de previsão já saiu na impressora? Carimbo gravado
                   no clique do 🖨 do relatório — evita separar duas vezes. -->
              <div
                v-if="row.status === 'previsao' && row.previsao_impressa_em"
                class="text-[9px] text-muted-foreground whitespace-nowrap mt-0.5"
                :title="'Papel de previsão já impresso (última vez ' + previsaoImpressaHora(row) + ')'"
              >
                🖨 {{ previsaoImpressaHora(row) }}
              </div>
            </td>
            <td class="bg-emerald-50/30">
              <input
                :value="row.observacao || ''"
                placeholder="observação"
                class="w-full h-6 border rounded px-1 bg-background text-[11px]"
                @blur="(e) => patchPedidoObs(row, (e.target as HTMLInputElement).value)"
              />
            </td>
            <td class="text-center">
              <a
                v-if="row.etiqueta_disponivel"
                :href="etiquetaUrl(row)"
                target="_blank"
                rel="noopener"
                class="inline-flex items-center gap-1 rounded-md border bg-primary text-primary-foreground px-2 py-1 text-[10px] hover:opacity-90"
                title="Abrir etiqueta pronta pra impressão"
              >
                <Printer class="size-3" />
                Imprimir
              </a>
              <!-- Horas de chegada/impressão da etiqueta agora moram nas
                   colunas "Etiqueta" e "Impressão" — sem carimbo duplicado
                   aqui. O aviso de reimpressão em lote continua (usa
                   etiqueta_impressa_em direto). -->
              <span v-if="!row.etiqueta_disponivel" class="text-[10px] text-muted-foreground/50">—</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- TAB: ENVIOS ─────────────────────────────────────────────────── -->
    <!-- Bloqueio pro operador: precisa estar 100% conferido HOJE.
         Admin nunca cai aqui (canAccessEnvios returns true). -->
    <div v-if="tab === 'envios' && !canAccessEnvios" class="border rounded-md py-10 px-4 text-center space-y-3">
      <p class="text-sm text-muted-foreground">
        ⚠️ Confira o estoque do dia antes de acessar os envios.
      </p>
      <p class="text-[11px] text-muted-foreground">
        Hoje: {{ conferenciaHoje.conferido }}/{{ conferenciaHoje.total }} ({{ conferenciaHoje.percent }}%)
      </p>
      <button
        class="px-3 py-1.5 bg-primary text-primary-foreground text-xs rounded"
        @click="tab = 'estoque'"
      >
        Ir para Estoque
      </button>
    </div>
    <div v-else-if="tab === 'envios'" class="border rounded-md overflow-x-auto">
      <table class="grid-table w-full text-xs border-collapse">
        <thead>
          <tr class="bg-muted/30 text-[10px] uppercase tracking-wide">
            <th class="text-left">Data</th>
            <th class="text-right">Envios</th>
            <th class="text-center">Conf. Estoque</th>
            <th class="text-center bg-gray-100/40">Conferido</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="envios.items.length === 0">
            <td colspan="4" class="py-6 text-center text-muted-foreground">
              Nenhum envio no período.
            </td>
          </tr>
          <tr
            v-for="row in envios.items" :key="row.data"
            class="hover:bg-muted/20"
            :class="row.conferido ? 'bg-emerald-50/40 dark:bg-emerald-900/10' : ''"
          >
            <td>{{ row.data }}</td>
            <td class="text-right font-semibold">{{ row.envios }}</td>
            <td class="text-center">
              <span
                class="inline-block px-1.5 py-0.5 rounded text-[10px] font-medium"
                :class="{
                  'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300': row.conferencia_estoque === 'total',
                  'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300': row.conferencia_estoque === 'parcial',
                  'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300': row.conferencia_estoque === 'nenhuma',
                }"
              >
                {{
                  row.conferencia_estoque === 'total'
                    ? 'Total'
                    : row.conferencia_estoque === 'parcial'
                      ? 'Parcial'
                      : 'Não conferido'
                }}
              </span>
            </td>
            <td class="text-center bg-gray-100/30">
              <input
                v-if="isAdmin"
                type="checkbox"
                :checked="row.conferido"
                class="cursor-pointer"
                @change="toggleEnvio(row)"
              />
              <span
                v-else
                class="inline-block text-base"
                :class="row.conferido ? 'text-emerald-600' : 'text-muted-foreground/40'"
                :title="row.conferido ? 'Conferido' : 'Não conferido'"
              >{{ row.conferido ? '✓' : '✗' }}</span>
            </td>
          </tr>
        </tbody>
        <tfoot v-if="envios.items.length > 0" class="bg-muted/30 font-semibold">
          <tr>
            <td class="text-right">Total (conferidos)</td>
            <td class="text-right">{{ envios.total }}</td>
            <td></td>
            <td class="text-center text-muted-foreground text-[10px]">
              geral: {{ envios.total_envios }}
            </td>
          </tr>
        </tfoot>
      </table>
    </div>

    <!-- TAB: ESTOQUE NEGATIVO ───────────────────────────────────────── -->
    <div v-if="tab === 'estoque-negativo' && canSeeEstoqueNegativo" class="space-y-4">
      <div class="flex flex-wrap items-center gap-2 bg-muted/30 border rounded-md px-3 py-2 text-xs">
        <span class="text-muted-foreground">
          Lê de <code class="bg-background px-1 rounded">products.saldo_virtual_total</code> /
          <code class="bg-background px-1 rounded">saldo_fisico</code> — atualize pelo Bling antes de gerar etiquetas.
        </span>
        <button
          class="ml-auto inline-flex items-center gap-1 rounded-md bg-primary text-primary-foreground px-2.5 py-1 hover:opacity-90 disabled:opacity-50"
          :disabled="refreshing"
          @click="refreshFromBling"
        >
          <Loader2 v-if="refreshing" class="size-3 animate-spin" />
          <RefreshCw v-else class="size-3" />
          {{ refreshing ? 'Atualizando…' : 'Atualizar via Bling' }}
        </button>
        <span v-if="refreshMsg" class="text-xs text-muted-foreground bg-background border rounded px-2 py-1">
          {{ refreshMsg }}
        </span>
      </div>

      <!-- Saldo virtual negativo -->
      <div class="space-y-2">
        <div class="flex items-center gap-2">
          <h3 class="font-semibold text-sm flex items-center gap-2">
            <AlertTriangle class="size-4 text-amber-600" /> Saldo virtual &lt; 0
          </h3>
          <span class="text-xs text-muted-foreground">{{ negativos.length }} SKU(s)</span>
        </div>
        <div v-if="negativosLoading" class="text-center py-6 text-muted-foreground text-xs">
          <Loader2 class="size-4 animate-spin mx-auto mb-1" /> Carregando…
        </div>
        <div v-else-if="negativos.length === 0" class="text-center py-6 text-muted-foreground text-xs">
          Nenhum SKU com saldo virtual negativo.
        </div>
        <div v-else class="border rounded-md overflow-x-auto">
          <table class="grid-table w-full text-xs border-collapse">
            <thead>
              <tr class="bg-emerald-800 text-white text-[10px] uppercase tracking-wide">
                <th class="text-left">SKU</th>
                <th class="text-right">Saldo Físico</th>
                <th class="text-right">Saldo Virtual</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in negativos" :key="row.codigo" class="even:bg-muted/10 hover:bg-amber-50/40">
                <td class="font-mono">{{ row.codigo }}</td>
                <td class="text-right">{{ row.saldo_fisico }}</td>
                <td class="text-right font-semibold text-red-700">{{ row.saldo_virtual_total }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Por sufixo -->
      <div class="space-y-2">
        <div class="flex flex-wrap items-center gap-2">
          <h3 class="font-semibold text-sm">Por sufixo (saldo físico &gt; 0)</h3>
          <span class="text-xs text-muted-foreground">{{ sufixos.length }} SKU(s)</span>
          <label class="inline-flex items-center gap-1 text-xs">
            Sufixo:
            <select v-model="suffixChoice" class="h-7 border rounded px-2 bg-background">
              <option v-for="opt in SUFFIX_PRESETS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
            </select>
          </label>
          <input
            v-if="suffixChoice === '__custom__'"
            v-model="customSuffix"
            placeholder=".us,.sa,.ra"
            class="h-7 border rounded px-2 text-xs bg-background"
            @blur="loadSufixos"
          />
          <button
            class="ml-auto inline-flex items-center gap-1 rounded-md border px-2.5 py-1 hover:bg-muted disabled:opacity-50"
            :disabled="sufixos.length === 0"
            @click="downloadCsv"
          >
            <Download class="size-3" /> CSV
          </button>
        </div>
        <div v-if="sufixos.length === 0" class="text-center py-6 text-muted-foreground text-xs">
          Nenhum SKU encontrado para os sufixos selecionados.
        </div>
        <div v-else class="border rounded-md overflow-x-auto">
          <table class="grid-table w-full text-xs border-collapse">
            <thead>
              <tr class="bg-emerald-800 text-white text-[10px] uppercase tracking-wide">
                <th class="text-left">SKU</th>
                <th class="text-right">Saldo Físico</th>
                <th class="text-right">Saldo Virtual</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in sufixos" :key="row.codigo" class="even:bg-muted/10 hover:bg-amber-50/40">
                <td class="font-mono">{{ row.codigo }}</td>
                <td class="text-right font-semibold text-emerald-700">{{ row.saldo_fisico }}</td>
                <td class="text-right">{{ row.saldo_virtual_total }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- TAB: UPLOAD NF ─────────────────────────────────────────────── -->
    <div v-if="tab === 'upload-nf'" class="space-y-4">
      <!-- Stores picker -->
      <section class="border rounded-md p-3 space-y-2">
        <div class="flex items-center justify-between">
          <h3 class="font-semibold text-sm">Lojas ML alvo</h3>
          <span class="text-xs text-muted-foreground">
            {{ nfSelectedStores.size }} de {{ nfStores.length }} selecionada(s)
          </span>
        </div>
        <div v-if="!nfStores.length" class="text-xs text-muted-foreground">
          Nenhuma integração ML ativa encontrada.
        </div>
        <div v-else class="flex flex-wrap gap-1.5">
          <button
            v-for="s in nfStores"
            :key="s"
            type="button"
            class="rounded-full border px-2.5 py-1 text-xs transition-colors"
            :class="nfSelectedStores.has(s) ? 'bg-primary text-primary-foreground border-primary' : 'border-muted-foreground/40 hover:bg-muted'"
            @click="toggleNfStore(s)"
          >
            {{ s }}
          </button>
        </div>
      </section>

      <!-- File picker -->
      <section class="border rounded-md p-3 space-y-2">
        <div class="flex flex-wrap items-center gap-2">
          <label class="inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm cursor-pointer hover:bg-muted">
            <FileUp class="size-3.5" /> Selecionar XMLs
            <input ref="nfFileInputRef" type="file" accept=".xml" multiple class="hidden" @change="onNfFileChange" />
          </label>
          <span class="text-xs text-muted-foreground">{{ nfFiles.length }} arquivo(s)</span>
          <button
            v-if="nfFiles.length"
            class="ml-auto inline-flex items-center gap-1 rounded-md border border-destructive text-destructive px-2.5 py-1 text-xs hover:bg-destructive/10"
            @click="clearNfAll"
          >
            <Trash2 class="size-3" /> Limpar
          </button>
          <button
            class="inline-flex items-center gap-1.5 rounded-md bg-primary text-primary-foreground px-3 py-1.5 text-sm hover:opacity-90 disabled:opacity-50"
            :disabled="nfProcessing || !nfFiles.length || !nfSelectedStores.size"
            @click="processNfFiles"
          >
            <Loader2 v-if="nfProcessing" class="size-3.5 animate-spin" />
            <Upload v-else class="size-3.5" />
            {{ nfProcessing ? 'Enviando…' : `Enviar (${nfFiles.length})` }}
          </button>
        </div>
        <ul v-if="nfFiles.length" class="text-xs space-y-1 border-t pt-2">
          <li v-for="(f, idx) in nfFiles" :key="idx" class="flex items-center gap-2">
            <FileUp class="size-3 text-muted-foreground shrink-0" />
            <span class="truncate flex-1">{{ f.name }}</span>
            <span class="text-muted-foreground">{{ (f.size / 1024).toFixed(0) }} KiB</span>
            <button v-if="!nfProcessing" class="text-muted-foreground hover:text-destructive" @click="removeNfFile(idx)">
              <Trash2 class="size-3" />
            </button>
          </li>
        </ul>
      </section>

      <div
        v-if="nfProcessing && nfCurrentFile"
        class="rounded border bg-muted/20 px-3 py-2 text-sm flex items-center gap-2"
      >
        <Loader2 class="size-3 animate-spin" />
        Processando: <strong>{{ nfCurrentFile }}</strong>
        ({{ nfResults.length + 1 }} de {{ nfFiles.length }})
      </div>

      <!-- Results -->
      <section v-if="nfResults.length" class="space-y-2">
        <div class="flex items-center gap-3 text-sm">
          <h3 class="font-semibold">Resultados</h3>
          <span class="text-emerald-700">✓ {{ nfSuccessCount }}</span>
          <span class="text-red-700">✕ {{ nfFailCount }}</span>
        </div>
        <div class="border rounded-md divide-y">
          <div
            v-for="(r, idx) in nfResults"
            :key="idx"
            class="p-3 text-xs"
            :class="r.success ? 'bg-emerald-50/40' : 'bg-red-50/40'"
          >
            <div class="flex items-center gap-2 flex-wrap">
              <span v-if="r.success" class="text-emerald-700 font-bold">✓</span>
              <span v-else class="text-red-700 font-bold">✕</span>
              <strong class="truncate">{{ r.filename }}</strong>
              <span v-if="r.order_id" class="text-muted-foreground font-mono">pedido {{ r.order_id }}</span>
              <span v-if="r.success" class="ml-auto text-emerald-800">→ {{ r.store_name }}</span>
              <span v-else class="ml-auto text-red-700">{{ r.error || 'falha' }}</span>
            </div>
            <details v-if="r.attempts_details && r.attempts_details.length" class="mt-1.5">
              <summary class="cursor-pointer text-[10px] text-muted-foreground hover:text-foreground">
                {{ r.attempts_details.length }} tentativa(s)
              </summary>
              <ul class="mt-1 pl-4 space-y-0.5 text-[11px]">
                <li v-for="(a, i) in r.attempts_details" :key="i" class="flex items-center gap-2">
                  <span v-if="a.success" class="text-emerald-700">✓</span>
                  <span v-else class="text-red-700">✕</span>
                  <span class="font-medium">{{ a.store }}</span>
                  <span v-if="a.shipping_id" class="text-muted-foreground">ship={{ a.shipping_id }}</span>
                  <span v-if="!a.success" class="text-muted-foreground italic">{{ a.error }}</span>
                </li>
              </ul>
            </details>
          </div>
        </div>
      </section>
    </div>

    <!-- Modal do botão INFORMAR (admin-only) -->
    <InformarThreemaModal
      :open="informarEstoqueOpen"
      contexto="controle_estoque"
      descricao="Manda via Threema os pedidos movidos pra Aguardando Cancelamento por falta de estoque (que ainda estão nessa situação). A seleção de destinatários fica salva."
      @close="informarEstoqueOpen = false"
    />
  </div>
</template>

<style scoped>
/* Full-grid borders on every cell — spreadsheet look. Padding kept tight
   so the row count visible on screen stays high. */
.grid-table th,
.grid-table td {
  border: 1px solid hsl(var(--border));
  padding: 4px 6px;
}
.grid-table thead th {
  background-clip: padding-box;
}

/* Inline-editable obs field on Entrada column. Sits flush in the cell
   with a dashed underline so operators can see it's editable without
   a heavy border that competes with the spreadsheet grid. Focused
   state turns into a clear bordered input on the muted background. */
.obs-input {
  display: block;
  width: 100%;
  height: 20px;
  padding: 0 4px;
  font-size: 11px;
  line-height: 20px;
  background: transparent;
  border: 0;
  border-bottom: 1px dashed hsl(var(--border));
  border-radius: 2px;
  color: inherit;
  cursor: text;
}
.obs-input::placeholder {
  color: hsl(var(--muted-foreground) / 0.55);
}
.obs-input:hover {
  background: hsl(var(--background) / 0.6);
  border-bottom-style: solid;
}
.obs-input:focus {
  background: hsl(var(--background));
  border: 1px solid hsl(var(--primary));
  outline: none;
}
</style>
