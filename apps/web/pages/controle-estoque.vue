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
//   * Pedidos  → "etiqueta enviada" orders (Bling situação 21 "Em digitação";
//                83965 "Enviado Etiqueta" = legado) shipped on the chosen day.
//   * Envios   → per-day shipment counts (the only tab that benefits
//                from a wider window, so it auto-widens to last 7 days
//                on first activation if the user hasn't picked a date).
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import {
  Boxes, Truck, ClipboardList, Loader2, RefreshCw,
  AlertTriangle, Download, Printer, FileText, FileUp, Upload, Trash2,
  ArrowUp, ArrowDown, Megaphone, Check, LifeBuoy, Send, Video,
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
  // Pedido que sai de 2+ armazéns (itens com tags diferentes). A tela pede
  // confirmação "Atenção: estoque compartilhado" antes de imprimir.
  estoque_compartilhado: boolean
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
  // Chamado de ATRASO NA POSTAGEM aberto em lote pela própria aba (Eduardo,
  // 15/09): nº/protocolo, canal e status da abertura — coluna "Chamado".
  chamado_atraso?: ChamadoAtrasoInfo | null
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

// Solicitação de vídeo da expedição (tela Devoluções, 17/09): uma por
// PEDIDO, com os itens dentro. Vem de /api/estoque/videos-pendentes, já
// cercada pela tag do SKU (mesma regra da aba Pedidos).
type VideoPendente = {
  pedido_bling: string
  pedido_marketplace: string
  loja: string
  cliente: string
  itens: { sku: string; produto: string; quantidade: number }[]
  solicitado_em: string | null
  solicitado_por: string | null
  // Quem apagou o link na Devoluções disse por quê — "refazer: ...".
  refazer_motivo: string | null
  // Etiqueta guardada (pedidos desde 03/08) — botão pra imprimir de novo.
  etiqueta_disponivel: boolean
}

// ── State ─────────────────────────────────────────────────────────────
type Tab = 'estoque' | 'pedidos' | 'envios' | 'upload-nf'
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

const visibleTabs: readonly Tab[] = ['estoque', 'pedidos', 'envios', 'upload-nf']

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

// ── Vídeos solicitados pela Devoluções (Vinicius 17/09) ───────────────
// Enquanto houver solicitação pra um pedido da equipe, a aba Pedidos fica
// trancada: "Envie o link do vídeo do pedido abaixo antes de acessar seus
// pedidos". Independe do dia do filtro. Admin e churchill nunca travam
// (mesma regra da aba Envios), mas veem a lista pra poder responder.
const videosPendentes = ref<VideoPendente[]>([])
const videoLinkDraft = reactive<Record<string, string>>({})
const videoSemMotivoDraft = reactive<Record<string, string>>({})
const videoSemAberto = ref<Set<string>>(new Set())
const videoEnviando = ref<Set<string>>(new Set())
const videoErro = ref<string | null>(null)
async function refreshVideosPendentes() {
  try {
    const params = new URLSearchParams()
    if ((canUseTagFilter.value || isGerenteEtiquetas.value) && tagOverride.value)
      params.set('tag', tagOverride.value)
    const r = await api<{ data: VideoPendente[]; total: number }>(
      `/api/estoque/videos-pendentes${params.toString() ? `?${params.toString()}` : ''}`,
    )
    videosPendentes.value = r.data || []
  } catch {
    // não-fatal: mantém a lista anterior (a trava cai pro lado seguro).
  }
}
const canAccessPedidos = computed(() => {
  if (isAdmin.value) return true
  // Mesmo bypass do churchill da aba Envios (ver canAccessEnvios).
  if (auth.user?.email === 'maconer06@tuta.com') return true
  return videosPendentes.value.length === 0
})
// Etiqueta do pedido de novo, pra achar o vídeo. carimbar=false: não conta
// como 1ª impressão (o pedido já saiu faz tempo).
function etiquetaVideoUrl(p: VideoPendente) {
  return `/api/estoque/pedidos/${encodeURIComponent(p.pedido_bling)}/etiqueta?carimbar=false`
}
function fmtVideoQuando(iso: string | null) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('pt-BR', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
    timeZone: 'America/Sao_Paulo',
  })
}
function toggleSemVideo(p: VideoPendente) {
  const next = new Set(videoSemAberto.value)
  if (next.has(p.pedido_bling)) next.delete(p.pedido_bling)
  else next.add(p.pedido_bling)
  videoSemAberto.value = next
}
async function responderVideo(p: VideoPendente, modo: 'link' | 'sem') {
  const link = (videoLinkDraft[p.pedido_bling] || '').trim()
  const motivo = (videoSemMotivoDraft[p.pedido_bling] || '').trim()
  if (modo === 'link' && !link) return
  if (modo === 'sem' && motivo.length < 3) return
  if (videoEnviando.value.has(p.pedido_bling)) return
  videoEnviando.value = new Set([...videoEnviando.value, p.pedido_bling])
  videoErro.value = null
  try {
    await api(`/api/estoque/videos-pendentes/${encodeURIComponent(p.pedido_bling)}`, {
      method: 'POST',
      body: modo === 'link' ? { link } : { sem_video_motivo: motivo },
    })
    videosPendentes.value = videosPendentes.value.filter((v) => v.pedido_bling !== p.pedido_bling)
    delete videoLinkDraft[p.pedido_bling]
    delete videoSemMotivoDraft[p.pedido_bling]
    // A aba destrava sozinha quando a última pendência sai — carrega os pedidos.
    if (tab.value === 'pedidos' && !videosPendentes.value.length) void loadPedidos()
  } catch (e: any) {
    const code = e?.data?.detail?.code
    videoErro.value = code === 'video_link_invalido'
      ? `Pedido ${p.pedido_bling}: o link do vídeo não parece válido.`
      : code === 'video_nao_pendente'
        ? `Pedido ${p.pedido_bling}: essa solicitação já foi respondida ou cancelada.`
        : `Pedido ${p.pedido_bling}: não deu pra enviar (${e?.data?.detail?.message || code || e?.message || 'erro'}).`
    // Pode ter sido cancelada na Devoluções — recarrega a lista.
    void refreshVideosPendentes()
  } finally {
    const next = new Set(videoEnviando.value)
    next.delete(p.pedido_bling)
    videoEnviando.value = next
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
  if (tab.value === 'envios') return loadEnvios()
}

watch(tab, (newTab) => {
  void loadCurrentTab()
  // Lojas ML da aba Upload NF: carrega só na primeira vez que ela abre.
  if (newTab === 'upload-nf' && !nfStores.value.length) void loadNfStores()
  // O bloqueio da aba Envios depende da conferência de hoje — refetch
  // sempre que a tab muda pra refletir alterações feitas em outra aba.
  void refreshConferenciaHoje()
  // Idem pro bloqueio da aba Pedidos (vídeos solicitados pela Devoluções).
  void refreshVideosPendentes()
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
  void refreshVideosPendentes()
})

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

onMounted(() => {
  void loadCurrentTab()
  void refreshConferenciaHoje()
  void refreshVideosPendentes()
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
  return `/api/estoque/pedidos/${encodeURIComponent(row.pedido_bling || '')}/etiqueta`
}
// Pedido que sai de 2+ armazéns: modal de aviso CENTRALIZADO antes de
// imprimir (confirm() nativo era discreto demais). O <a> individual sempre
// leva preventDefault; o clique no botão "Imprimir mesmo assim" do modal é
// uma user activation NOVA → window.open não é bloqueado.
const avisoCompartilhado = ref<{ pedidos: string[], onOk: () => void } | null>(null)
function confirmarCompartilhado(row: PedidoRow, e: Event) {
  if (!row.estoque_compartilhado) return
  e.preventDefault()
  const url = etiquetaUrl(row)
  avisoCompartilhado.value = {
    pedidos: [row.pedido_bling || ''],
    onOk: () => { window.open(url, '_blank', 'noopener') },
  }
}
function okCompartilhado() {
  const aviso = avisoCompartilhado.value
  avisoCompartilhado.value = null
  aviso?.onOk()
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

// ── Atualização automática da lista ───────────────────────────────────
// O robô que confirma o envio no marketplace roda a cada minuto, mas a
// tela só recarregava quando o operador trocava filtro ou imprimia — daí
// a sensação de "não atualizou" mesmo com o pedido já enviado (Eduardo,
// 04/09). Recarrega sozinha a cada minuto, sem atropelar quem está usando:
// pula quando a aba está em segundo plano, quando já há carga em andamento,
// quando um modal está aberto, quando há impressão em lote rodando e
// quando o cursor está dentro de um campo (a observação salva no blur).
const AUTO_REFRESH_MS = 60_000
let autoRefreshTimer: number | null = null

function autoRefreshTick() {
  if (typeof document !== 'undefined' && document.visibilityState !== 'visible') return
  if (loading.value || informarEstoqueOpen.value || imprimindoLote.value) return
  if (typeof document !== 'undefined') {
    const el = document.activeElement
    const tag = el ? el.tagName : ''
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
  }
  void loadCurrentTab()
  // Solicitação nova de vídeo (Devoluções) aparece sem recarregar a página.
  if (tab.value === 'pedidos') void refreshVideosPendentes()
}

onMounted(() => {
  autoRefreshTimer = window.setInterval(autoRefreshTick, AUTO_REFRESH_MS)
})
onBeforeUnmount(() => {
  if (autoRefreshTimer !== null) window.clearInterval(autoRefreshTimer)
  autoRefreshTimer = null
})

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
// confirmado pela agência (situacao IN (21 "Em digitação", 83965 legado)
// + em_andamento_data < hoje).
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

// ── Chamados de ATRASO NA POSTAGEM em lote (Eduardo, 15/09) ─────────────
// "Selecionar todos os pedidos e o sistema abre um chamado em cada loja —
// pode juntar: todos ML Aguiar num único chamado". Mesmas caixinhas da
// impressão → conferência por loja (cada pedido com marcado/desmarcado,
// motivo fila/energia/etiqueta/outro e texto editável) → um chamado por
// loja. ML sai pelo robô do formulário de ajuda; Shopee/TikTok/Amazon ficam
// registrados pra abrir na mão (sem API). Regras e textos: apps/api
// services/chamados_atraso.
type ChamadoAtrasoInfo = {
  chamado_id: string
  chamado: string | null
  canal: 'api' | 'robo' | 'manual'
  resolvido: boolean
  status: string | null
  motivo: string | null
}
type AtrasoMotivo = 'fila' | 'energia' | 'etiqueta' | 'outro'
type AtrasoPedido = {
  pedido_bling: string
  pedido_marketplace: string | null
  corte: string | null
  postagem: string | null
  etiqueta_em: string | null
  etiqueta_gerada: boolean
  atraso_min: number | null
  situacao: string
  incluir: boolean
  motivo: AtrasoMotivo
}
type AtrasoGrupo = {
  chave: string
  loja: string
  plataforma: string | null
  conta: string | null
  canal: 'robo' | 'manual'
  pedidos: AtrasoPedido[]
  motivo_outro: string
  texto: string
  _editado?: boolean
}
type AtrasoExcluido = { pedido_bling: string; pedido_marketplace: string | null; motivo: string; detalhe: string }
type AtrasoAberto = {
  chave: string
  loja?: string
  canal?: string
  chamado_id?: string
  pedidos?: number
  excluido?: AtrasoExcluido
}
const atraso = reactive({
  open: false,
  loading: false,
  enviando: false,
  erro: null as string | null,
  grupos: [] as AtrasoGrupo[],
  excluidos: [] as AtrasoExcluido[],
  abertos: null as AtrasoAberto[] | null,
})
const ATRASO_MOTIVO_LABEL: Record<AtrasoMotivo, string> = {
  fila: 'fila na postagem',
  energia: 'queda de energia',
  etiqueta: 'problema na emissão da etiqueta',
  outro: 'outro (escrever o motivo)',
}
// O que a regra concluiu pro pedido que entra DESMARCADO (a pessoa decide).
const ATRASO_SITUACAO: Record<string, string> = {
  no_prazo: 'postado dentro do prazo',
  dia_seguinte: 'postado em outro dia',
  corte_futuro: 'corte só amanhã ou depois',
  sem_corte: 'sem horário de corte capturado',
  etiqueta_gerada: 'não postado, mas a etiqueta já foi gerada',
}
// Pedido que não entra de jeito nenhum.
const ATRASO_EXCLUSAO: Record<string, string> = {
  sem_numero_marketplace: 'sem nº do pedido na plataforma',
  ja_tem_chamado: 'já tem chamado de atraso aberto',
  fora_da_sua_tag: 'pedido de outra operação',
  pedido_nao_encontrado: 'pedido não encontrado',
  motivo_outro_obrigatorio: 'escreva o motivo do chamado (opção "outro")',
}
const _DATA_HORA_BRT = new Intl.DateTimeFormat('pt-BR', {
  timeZone: 'America/Sao_Paulo', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
})
function fmtDataHoraBrt(iso: string | null) {
  return iso ? _DATA_HORA_BRT.format(new Date(iso)) : '—'
}
function fmtAtraso(min: number | null) {
  if (min === null || min === undefined) return '—'
  if (min <= 0) return 'no prazo'
  return min < 60 ? `${min} min` : `${Math.floor(min / 60)}h${String(min % 60).padStart(2, '0')}`
}
// Espelho de services/chamados_atraso.motivos_permitidos: postado → fila/
// energia/outro; não postado sem etiqueta → etiqueta/outro; com etiqueta
// gerada → só outro (Eduardo: "com etiqueta gerada não deixa, trava").
function motivosPermitidos(p: AtrasoPedido): AtrasoMotivo[] {
  if (p.postagem) return ['fila', 'energia', 'outro']
  if (p.etiqueta_gerada) return ['outro']
  return ['etiqueta', 'outro']
}
function grupoPrecisaMotivoOutro(g: AtrasoGrupo): boolean {
  return g.pedidos.some(p => p.incluir && p.motivo === 'outro')
}
const atrasoGruposComPedidos = computed(() => atraso.grupos.filter(g => g.pedidos.some(p => p.incluir)))
const atrasoFaltaMotivoOutro = computed(() =>
  atraso.grupos.some(g => grupoPrecisaMotivoOutro(g) && !g.motivo_outro.trim()),
)
function chamadoAtrasoErro(e: any): string {
  const detail = e?.data?.detail
  const code = detail?.code as string | undefined
  if (e?.status === 403 || e?.statusCode === 403) return 'Sem permissão: é preciso poder editar chamados.'
  return (code && (ATRASO_EXCLUSAO[code] || CHAMADO_ERROS[code])) || detail?.message || 'Não deu para montar os chamados agora. Tente de novo em instantes.'
}
async function prepararChamadosAtraso() {
  const pedidos = pedidosComEtiqueta.value.filter(p => etiquetasSel.value.has(p))
  if (!pedidos.length || atraso.loading) return
  atraso.open = true
  atraso.loading = true
  atraso.erro = null
  atraso.abertos = null
  atraso.grupos = []
  atraso.excluidos = []
  try {
    const res = await api<{ grupos: AtrasoGrupo[]; excluidos: AtrasoExcluido[] }>(
      '/api/estoque/pedidos/chamados-atraso/preview',
      { method: 'POST', body: { pedidos } },
    )
    atraso.grupos = res.grupos
    atraso.excluidos = res.excluidos
  } catch (e: any) {
    atraso.erro = chamadoAtrasoErro(e)
  } finally {
    atraso.loading = false
  }
}
// Mudou marcado/motivo/texto livre: refaz o texto padrão do grupo — a não
// ser que a pessoa já tenha mexido no texto à mão.
let atrasoRegerarTimer: number | null = null
function regerarTextoAtraso(grupo: AtrasoGrupo) {
  if (grupo._editado) return
  if (atrasoRegerarTimer !== null) window.clearTimeout(atrasoRegerarTimer)
  atrasoRegerarTimer = window.setTimeout(async () => {
    try {
      const res = await api<{ grupos: AtrasoGrupo[] }>('/api/estoque/pedidos/chamados-atraso/preview', {
        method: 'POST',
        body: {
          pedidos: grupo.pedidos.map(p => p.pedido_bling),
          motivos: Object.fromEntries(grupo.pedidos.map(p => [p.pedido_bling, p.motivo])),
          incluir: Object.fromEntries(grupo.pedidos.map(p => [p.pedido_bling, p.incluir])),
          motivo_outro: { [grupo.chave]: grupo.motivo_outro },
        },
      })
      const novo = res.grupos.find(g => g.chave === grupo.chave)
      if (novo && !grupo._editado) grupo.texto = novo.texto
    } catch {
      // mantém o texto atual
    }
  }, 350)
}
function mudarMotivoAtraso(grupo: AtrasoGrupo, pedido: AtrasoPedido, motivo: AtrasoMotivo) {
  pedido.motivo = motivo
  regerarTextoAtraso(grupo)
}
function mudarIncluirAtraso(grupo: AtrasoGrupo, pedido: AtrasoPedido, incluir: boolean) {
  pedido.incluir = incluir
  // Marcou um que a regra deixou de fora sem motivo válido: cai em "outro".
  if (incluir && !motivosPermitidos(pedido).includes(pedido.motivo)) pedido.motivo = motivosPermitidos(pedido)[0]
  regerarTextoAtraso(grupo)
}
async function confirmarChamadosAtraso() {
  if (atraso.enviando || atraso.loading || !atrasoGruposComPedidos.value.length) return
  if (atrasoFaltaMotivoOutro.value) {
    atraso.erro = ATRASO_EXCLUSAO.motivo_outro_obrigatorio
    return
  }
  atraso.enviando = true
  atraso.erro = null
  try {
    const res = await api<{ abertos: AtrasoAberto[] }>('/api/estoque/pedidos/chamados-atraso', {
      method: 'POST',
      body: {
        grupos: atrasoGruposComPedidos.value.map(g => ({
          chave: g.chave,
          texto: g.texto,
          motivo_outro: g.motivo_outro,
          pedidos: g.pedidos.map(p => ({ pedido_bling: p.pedido_bling, motivo: p.motivo, incluir: p.incluir })),
        })),
      },
    })
    atraso.abertos = res.abertos
    etiquetasSel.value = new Set()
    await loadPedidos()
  } catch (e: any) {
    atraso.erro = chamadoAtrasoErro(e)
  } finally {
    atraso.enviando = false
  }
}
function fecharAtraso() {
  atraso.open = false
}
function chamadoAtrasoLabel(info: ChamadoAtrasoInfo): string {
  if (info.resolvido) return 'resolvido'
  if (info.chamado) return `nº ${info.chamado}`
  if (info.canal === 'robo') {
    if (info.status === 'pendente') return 'robô: na fila'
    if (info.status === 'falhou') return 'robô: falhou'
    return 'robô'
  }
  return 'abrir na mão'
}

// ── Abrir chamado do pedido parado (Eduardo, 09/09: "um botão de abrir
// chamado para os pedidos atrasados"; ele escolhe a linha e clica) ────────
// O chamado nasce na aba Chamados e vai pro Mercado Livre pelo robô do
// formulário de ajuda — o mesmo caminho do botão da aba Logística.
const chamadoEnviando = ref<Set<string>>(new Set())
const chamadoAberto = ref<Set<string>>(new Set())
const CHAMADO_ERROS: Record<string, string> = {
  pedido_nao_encontrado: 'Pedido não encontrado no espelho do Bling.',
  pedido_sem_logistica: 'Esse pedido ainda não está na aba Logística (sem etiqueta/rastreio), então não há envio para cobrar.',
  pedido_nao_ml: 'Só dá para abrir chamado automático no Mercado Livre. Nas outras plataformas é preciso abrir na mão.',
  pedido_sem_numero_marketplace: 'O pedido está sem o número da plataforma, que o formulário do Mercado Livre exige.',
  pedido_sem_etiqueta: 'A etiqueta desse pedido ainda não foi gerada, então não há envio parado para cobrar.',
  pedido_no_prazo: 'Esse pedido ainda está dentro do prazo de envio.',
  pedido_ja_enviado: 'Esse pedido já foi enviado.',
  pedido_ja_tem_chamado: 'Já existe chamado aberto para esse pedido. Continue por ele na aba Chamados.',
  pedido_fora_da_sua_tag: 'Esse pedido é de outra operação, fora das suas tags.',
  no_stock_tag: 'Seu usuário não tem tag de estoque definida. Peça para um administrador configurar.',
}
// Prazo de envio já vencido: pinta o botão de vermelho (é o caso que o
// Eduardo quer cobrar). Reaproveita a mesma data do "corte" da tela.
function corteVencido(row: PedidoRow): boolean {
  if (!row.ship_deadline || row.status === 'enviado') return false
  return new Date(row.ship_deadline).getTime() < Date.now()
}
// O chamado AFIRMA ao Mercado Livre que o prazo venceu e que a etiqueta já
// saiu. Só oferece o botão onde isso é verdade — pedido de previsão (amarelo,
// sem etiqueta) aparece na mesma lista e o texto seria mentira.
function podeChamado(row: PedidoRow): boolean {
  return row.status === 'nao_enviado' && corteVencido(row)
}
async function abrirChamado(row: PedidoRow) {
  const pedido = row.pedido_bling
  if (!pedido || chamadoEnviando.value.has(pedido) || chamadoAberto.value.has(pedido)) return
  if (!confirm(
    `Abrir chamado no Mercado Livre para o pedido ${pedido}?\n\n`
    + 'A mensagem vai para o formulário de ajuda do Mercado Livre e não tem como desfazer.',
  )) return
  chamadoEnviando.value = new Set(chamadoEnviando.value).add(pedido)
  try {
    await api(`/api/estoque/pedidos/${encodeURIComponent(pedido)}/abrir-chamado`, { method: 'POST' })
    chamadoAberto.value = new Set(chamadoAberto.value).add(pedido)
  }
  catch (e: any) {
    const code = e?.data?.detail?.code || e?.data?.code || ''
    alert(CHAMADO_ERROS[code] || 'Não deu para abrir o chamado agora. Tente de novo em instantes.')
  }
  finally {
    const next = new Set(chamadoEnviando.value)
    next.delete(pedido)
    chamadoEnviando.value = next
  }
}

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
  // Pedido dividido entre armazéns: quem imprime precisa saber que parte dos
  // itens da declaração está em outro armazém. O modal centralizado segura o
  // lote; confirmar chama a continuação (download via <a> não precisa de
  // user activation, então rodar depois do clique do modal é seguro).
  const compartilhados = pedidos.filter(p =>
    pedidosFilteredGrouped.value.some(r => r.pedido_bling === p && r.estoque_compartilhado),
  )
  if (compartilhados.length) {
    avisoCompartilhado.value = {
      pedidos: compartilhados,
      onOk: () => { void executarLote(pedidos, comRelatorio) },
    }
    return
  }
  await executarLote(pedidos, comRelatorio)
}
async function executarLote(pedidos: string[], comRelatorio: boolean) {
  if (imprimindoLote.value) return
  imprimindoLote.value = comRelatorio ? 'relatorio' : 'etiquetas'
  try {
    // fetch cru (não useApi): a resposta é um PDF binário, não JSON.
    const resp = await fetch('/api/estoque/pedidos/etiquetas', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        pedidos, incluir_relatorio: comRelatorio,
      }),
    })
    if (resp.status === 404) {
      // Nenhum dos selecionados tem etiqueta salva de verdade (ex.: só a NF
      // chegou; a etiqueta ainda não foi capturada). Mensagem específica —
      // o alerta genérico não dizia o que fazer.
      alert(
        'Nenhum dos pedidos selecionados tem etiqueta pronta ainda. '
        + 'A etiqueta chega sozinha em até ~1h; se continuar sem, avise o responsável.',
      )
      return
    }
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
          <FileUp v-else class="size-4" />
          {{
            t === 'estoque' ? 'Estoque' :
            t === 'pedidos' ? 'Pedidos' :
            t === 'envios' ? 'Envios' :
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
    <!-- Vídeos solicitados pela Devoluções (17/09). Pro operador é uma TRAVA:
         enquanto houver pedido de vídeo pra um pedido da equipe dele, a aba só
         mostra esta lista (canAccessPedidos). Admin e churchill não travam,
         mas veem a mesma lista pra poder responder. -->
    <div
      v-if="tab === 'pedidos' && videosPendentes.length"
      class="border rounded-md overflow-x-auto"
      :class="canAccessPedidos ? 'border-amber-400' : ''"
    >
      <div class="px-4 text-center space-y-1" :class="canAccessPedidos ? 'py-3' : 'py-6'">
        <p class="text-sm" :class="canAccessPedidos ? 'text-amber-700 dark:text-amber-400' : 'text-muted-foreground'">
          {{ canAccessPedidos
            ? `📹 ${videosPendentes.length} vídeo${videosPendentes.length !== 1 ? 's' : ''} solicitado${videosPendentes.length !== 1 ? 's' : ''} pela Devoluções`
            : '⚠️ Envie o link do vídeo do pedido abaixo antes de acessar seus pedidos.' }}
        </p>
        <p class="text-[11px] text-muted-foreground">
          Cole o link do vídeo da expedição de cada pedido. Não tem o vídeo? Clique em "não tenho o vídeo" e diga o motivo.
        </p>
      </div>
      <table class="grid-table w-full text-xs border-collapse">
        <thead>
          <tr class="bg-muted/30 text-[10px] uppercase tracking-wide">
            <th class="text-left">Solicitado</th>
            <th class="text-left">Pedido Bling</th>
            <th class="text-left">Marketplace</th>
            <th class="text-left">Loja</th>
            <th class="text-left">Cliente</th>
            <th class="text-left">SKU</th>
            <th class="text-left">Produto</th>
            <th class="text-right">Qtd</th>
            <th class="text-center">Etiqueta</th>
            <th class="text-left min-w-[300px]">Link do vídeo</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="p in videosPendentes" :key="p.pedido_bling">
            <!-- Uma linha por item, igual à aba Pedidos; campo de link só na 1ª. -->
            <tr
              v-for="(it, i) in p.itens"
              :key="`${p.pedido_bling}-${i}`"
              :class="{ 'border-t-2 border-t-muted-foreground/30': i === 0 }"
            >
              <td class="whitespace-nowrap text-[11px] align-top">
                <template v-if="i === 0">
                  {{ fmtVideoQuando(p.solicitado_em) }}
                  <div class="text-[9px] text-muted-foreground">{{ p.solicitado_por || '' }}</div>
                  <div
                    v-if="p.refazer_motivo"
                    class="text-[10px] text-red-600 dark:text-red-400 font-medium whitespace-normal max-w-[180px]"
                    title="O link anterior foi apagado na Devoluções — refazer o vídeo"
                  >
                    refazer: {{ p.refazer_motivo }}
                  </div>
                </template>
              </td>
              <td class="font-mono text-[11px] align-top">{{ i === 0 ? p.pedido_bling : '' }}</td>
              <td class="font-mono text-[11px] align-top">{{ i === 0 ? (p.pedido_marketplace || '—') : '' }}</td>
              <td class="align-top">{{ i === 0 ? (p.loja || '—') : '' }}</td>
              <td class="truncate max-w-[160px] align-top" :title="p.cliente || ''">{{ i === 0 ? (p.cliente || '—') : '' }}</td>
              <td class="font-mono text-[11px] align-top">{{ it.sku || '—' }}</td>
              <td class="truncate max-w-[280px] align-top" :title="it.produto || ''">{{ it.produto || '—' }}</td>
              <td class="text-right align-top">{{ it.quantidade }}</td>
              <td class="text-center align-top">
                <a
                  v-if="i === 0 && p.etiqueta_disponivel"
                  :href="etiquetaVideoUrl(p)"
                  target="_blank"
                  rel="noopener"
                  class="inline-flex items-center gap-1 rounded-md border bg-primary text-primary-foreground px-2 py-1 text-[10px] hover:opacity-90"
                  title="Abrir a etiqueta do pedido de novo (pra achar o vídeo)"
                >
                  <Printer class="size-3" />
                  Imprimir
                </a>
                <span
                  v-else-if="i === 0"
                  class="text-[10px] text-muted-foreground/50"
                  title="Etiqueta não guardada (pedido anterior a 03/08)"
                >—</span>
              </td>
              <td class="align-top">
                <template v-if="i === 0">
                  <div class="flex items-center gap-1">
                    <input
                      v-model="videoLinkDraft[p.pedido_bling]"
                      type="url"
                      placeholder="cole o link do vídeo"
                      class="h-7 flex-1 border rounded px-2 bg-background text-[11px]"
                      :disabled="videoEnviando.has(p.pedido_bling)"
                      @keydown.enter.prevent="responderVideo(p, 'link')"
                    />
                    <button
                      type="button"
                      class="inline-flex items-center gap-1 rounded-md bg-primary text-primary-foreground px-2 py-1 text-[10px] disabled:opacity-50"
                      :disabled="!(videoLinkDraft[p.pedido_bling] || '').trim() || videoEnviando.has(p.pedido_bling)"
                      title="Enviar o link pra Devoluções"
                      @click="responderVideo(p, 'link')"
                    >
                      <Loader2 v-if="videoEnviando.has(p.pedido_bling)" class="size-3 animate-spin" />
                      <Send v-else class="size-3" />
                      enviar
                    </button>
                  </div>
                  <div class="mt-1">
                    <button
                      v-if="!videoSemAberto.has(p.pedido_bling)"
                      type="button"
                      class="text-[10px] text-muted-foreground underline hover:text-foreground"
                      @click="toggleSemVideo(p)"
                    >
                      não tenho o vídeo
                    </button>
                    <div v-else class="flex items-center gap-1">
                      <input
                        v-model="videoSemMotivoDraft[p.pedido_bling]"
                        placeholder="por que não tem o vídeo?"
                        class="h-7 flex-1 border border-red-400 rounded px-2 bg-background text-[11px]"
                        :disabled="videoEnviando.has(p.pedido_bling)"
                        @keydown.enter.prevent="responderVideo(p, 'sem')"
                      />
                      <button
                        type="button"
                        class="inline-flex items-center gap-1 rounded-md border border-red-400 px-2 py-1 text-[10px] text-red-600 disabled:opacity-50 dark:text-red-400"
                        :disabled="(videoSemMotivoDraft[p.pedido_bling] || '').trim().length < 3 || videoEnviando.has(p.pedido_bling)"
                        title="Responder que não tem o vídeo — a Devoluções vê o motivo"
                        @click="responderVideo(p, 'sem')"
                      >
                        enviar sem vídeo
                      </button>
                      <button type="button" class="text-[10px] text-muted-foreground" @click="toggleSemVideo(p)">
                        cancelar
                      </button>
                    </div>
                  </div>
                </template>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
      <p v-if="videoErro" class="px-4 py-2 text-xs text-red-500">{{ videoErro }}</p>
    </div>
    <div v-if="tab === 'pedidos' && canAccessPedidos" class="flex flex-wrap items-center gap-2 text-xs">
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
      <!-- Chamados de ATRASO NA POSTAGEM em lote (Eduardo, 15/09): um chamado
           por loja com os pedidos selecionados; conferência antes de enviar. -->
      <button
        v-if="pedidosComEtiqueta.length > 0"
        type="button"
        class="inline-flex items-center gap-1.5 rounded-md border border-red-400 bg-background px-2.5 py-1 font-semibold text-red-600 disabled:opacity-50 dark:text-red-400"
        :disabled="selecionadosCount === 0 || atraso.loading"
        title="Abre um chamado por loja justificando o atraso da postagem dos pedidos selecionados (fila ou queda de energia). Mercado Livre pelo robô; nas outras plataformas fica o texto pronto pra abrir na mão."
        @click="prepararChamadosAtraso"
      >
        <Loader2 v-if="atraso.loading" class="size-3.5 animate-spin" />
        <LifeBuoy v-else class="size-3.5" />
        Abrir chamados de atraso ({{ selecionadosCount }})
      </button>
    </div>
    <div v-if="tab === 'pedidos' && canAccessPedidos" class="border rounded-md overflow-x-auto">
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
            <th class="text-center" title="Chamado do pedido: o de atraso na postagem (botão em lote, qualquer loja) ou o de pedido parado (só Mercado Livre, pelo formulário de ajuda).">Chamado</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="pedidosFiltered.length === 0">
            <td colspan="15" class="py-6 text-center text-muted-foreground">
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
                @click="confirmarCompartilhado(row, $event)"
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
            <td class="text-center">
              <NuxtLink
                v-if="row._isFirstOfGroup && row.chamado_atraso"
                :to="{ path: '/chamados', query: row.pedido_bling ? { search: row.pedido_bling } : {} }"
                class="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] font-medium"
                :class="row.chamado_atraso.resolvido
                  ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300'
                  : 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300'"
                :title="`Chamado de atraso na postagem (${row.chamado_atraso.motivo === 'energia' ? 'queda de energia' : 'fila na postagem'}) — abrir na aba Chamados`"
              >
                <Check v-if="row.chamado_atraso.resolvido" class="size-3" />
                <LifeBuoy v-else class="size-3" />
                {{ chamadoAtrasoLabel(row.chamado_atraso) }}
              </NuxtLink>
              <button
                v-else-if="row._isFirstOfGroup && row.pedido_bling && podeChamado(row)"
                :disabled="chamadoEnviando.has(row.pedido_bling) || chamadoAberto.has(row.pedido_bling)"
                class="inline-flex items-center gap-1 rounded-md border border-red-400 px-2 py-1 text-[10px] text-red-600 hover:bg-muted disabled:opacity-60 dark:text-red-400"
                :title="chamadoAberto.has(row.pedido_bling) ? 'Chamado já aberto agora' : 'Abrir chamado no Mercado Livre para este pedido'"
                @click="abrirChamado(row)"
              >
                <Loader2 v-if="chamadoEnviando.has(row.pedido_bling)" class="size-3 animate-spin" />
                <Check v-else-if="chamadoAberto.has(row.pedido_bling)" class="size-3" />
                <LifeBuoy v-else class="size-3" />
                {{ chamadoAberto.has(row.pedido_bling) ? 'Aberto' : 'Chamado' }}
              </button>
              <span v-else class="text-[10px] text-muted-foreground/50">—</span>
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

    <!-- Conferência dos chamados de atraso na postagem (Eduardo, 15/09) -->
    <div
      v-if="atraso.open"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      @click.self="fecharAtraso"
    >
      <div class="flex max-h-[92vh] w-full max-w-5xl flex-col overflow-hidden rounded-xl border bg-background shadow-2xl">
        <div class="flex items-start justify-between gap-3 border-b px-5 py-3">
          <div>
            <div class="text-base font-semibold">Chamados de atraso na postagem</div>
            <div class="text-xs text-muted-foreground">
              Um chamado por loja. Postado até 60 min depois do corte = fila na postagem; mais que isso, no mesmo dia = queda de energia; ainda não postado e sem etiqueta = problema na emissão da etiqueta. O que a regra deixou de fora aparece desmarcado: marque e escolha o motivo se quiser incluir. Confira e ajuste o texto antes de enviar.
            </div>
          </div>
          <button type="button" class="rounded-md border px-2 py-1 text-xs hover:bg-muted" @click="fecharAtraso">fechar</button>
        </div>
        <div class="flex-1 space-y-4 overflow-auto px-5 py-4 text-sm">
          <div v-if="atraso.loading" class="text-muted-foreground"><Loader2 class="mr-1.5 inline size-4 animate-spin" />conferindo os pedidos…</div>
          <div v-if="atraso.erro" class="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-red-600">{{ atraso.erro }}</div>

          <template v-if="atraso.abertos">
            <div class="rounded-md border border-emerald-500/40 bg-emerald-500/10 px-3 py-2">
              <div class="font-semibold">Pronto.</div>
              <ul class="mt-1 list-disc pl-5">
                <li v-for="(a, i) in atraso.abertos" :key="i">
                  <template v-if="a.chamado_id">
                    <b>{{ a.loja }}</b>: {{ a.pedidos }} pedido(s) —
                    <span v-if="a.canal === 'robo'">na fila do robô do Mercado Livre; o protocolo aparece na aba Chamados quando ele abrir.</span>
                    <span v-else>registrado na aba Chamados com o texto pronto — <b>abrir na mão no painel da plataforma</b>.</span>
                  </template>
                  <template v-else-if="a.excluido">
                    {{ a.excluido.pedido_marketplace || a.excluido.pedido_bling }}: ficou de fora ({{ ATRASO_EXCLUSAO[a.excluido.motivo] || a.excluido.detalhe }})
                  </template>
                </li>
              </ul>
            </div>
          </template>

          <template v-else-if="!atraso.loading">
            <div v-if="!atraso.grupos.length && !atraso.erro" class="text-muted-foreground">
              Nenhum dos pedidos selecionados pode entrar. Veja abaixo o motivo de cada um.
            </div>
            <div v-for="g in atraso.grupos" :key="g.chave" class="rounded-md border">
              <div class="flex flex-wrap items-center gap-2 border-b bg-muted/30 px-3 py-2">
                <span class="font-semibold">{{ g.loja }}</span>
                <span class="text-xs text-muted-foreground">{{ g.pedidos.filter(p => p.incluir).length }} de {{ g.pedidos.length }} pedido(s) no chamado</span>
                <span
                  class="ml-auto rounded px-1.5 py-0.5 text-[11px]"
                  :class="g.canal === 'robo' ? 'bg-sky-100 text-sky-700 dark:bg-sky-900/30 dark:text-sky-300' : 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300'"
                >
                  {{ g.canal === 'robo' ? 'Mercado Livre: o robô abre o formulário' : 'sem API: fica registrado pra abrir na mão' }}
                </span>
              </div>
              <table class="w-full text-xs">
                <thead>
                  <tr class="text-[10px] uppercase tracking-wide text-muted-foreground">
                    <th class="px-2 py-1 text-center">incluir</th>
                    <th class="px-2 py-1 text-left">Pedido</th>
                    <th class="px-2 py-1 text-left">Corte</th>
                    <th class="px-2 py-1 text-left">Etiqueta</th>
                    <th class="px-2 py-1 text-left">Postagem</th>
                    <th class="px-2 py-1 text-right">Atraso</th>
                    <th class="px-2 py-1 text-left">Motivo</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="p in g.pedidos" :key="p.pedido_bling" class="border-t" :class="{ 'opacity-60': !p.incluir }">
                    <td class="px-2 py-1 text-center">
                      <input type="checkbox" class="cursor-pointer" :checked="p.incluir" @change="(e) => mudarIncluirAtraso(g, p, (e.target as HTMLInputElement).checked)" />
                    </td>
                    <td class="px-2 py-1">
                      <div class="font-mono">{{ p.pedido_marketplace || p.pedido_bling }}</div>
                      <div v-if="ATRASO_SITUACAO[p.situacao]" class="text-[10px] text-muted-foreground">{{ ATRASO_SITUACAO[p.situacao] }}</div>
                    </td>
                    <td class="px-2 py-1">{{ fmtDataHoraBrt(p.corte) }}</td>
                    <td class="px-2 py-1">
                      <span v-if="p.etiqueta_em">{{ fmtDataHoraBrt(p.etiqueta_em) }}</span>
                      <span v-else-if="p.etiqueta_gerada">gerada</span>
                      <span v-else class="text-muted-foreground">sem etiqueta</span>
                    </td>
                    <td class="px-2 py-1">
                      <span v-if="p.postagem">{{ fmtDataHoraBrt(p.postagem) }}</span>
                      <span v-else class="text-red-600 dark:text-red-400">não postado</span>
                    </td>
                    <td class="px-2 py-1 text-right tabular-nums">{{ fmtAtraso(p.atraso_min) }}</td>
                    <td class="px-2 py-1">
                      <select
                        :value="p.motivo"
                        :disabled="!p.incluir"
                        class="h-7 rounded border bg-background px-1 text-xs disabled:opacity-50"
                        @change="(e) => mudarMotivoAtraso(g, p, (e.target as HTMLSelectElement).value as AtrasoMotivo)"
                      >
                        <option v-for="m in motivosPermitidos(p)" :key="m" :value="m">{{ ATRASO_MOTIVO_LABEL[m] }}</option>
                      </select>
                    </td>
                  </tr>
                </tbody>
              </table>
              <div v-if="grupoPrecisaMotivoOutro(g)" class="border-t px-3 py-2">
                <div class="mb-1 text-[11px] font-medium">Descreva o motivo (vai no texto do chamado, no bloco "Outro motivo") <span class="text-red-500">*</span></div>
                <textarea
                  v-model="g.motivo_outro"
                  rows="2"
                  class="w-full rounded-md border bg-background px-2 py-1.5 text-xs"
                  :class="!g.motivo_outro.trim() ? 'ring-1 ring-red-500/60' : ''"
                  placeholder="ex.: a transportadora não fez a coleta hoje; os pacotes já estão embalados e saem na próxima coleta"
                  @input="regerarTextoAtraso(g)"
                />
              </div>
              <div class="border-t px-3 py-2">
                <div class="mb-1 flex items-center justify-between text-[11px] text-muted-foreground">
                  <span>Texto do chamado (pode editar)</span>
                  <span v-if="g._editado">editado à mão — mudar motivo ou marcação não refaz o texto</span>
                </div>
                <textarea v-model="g.texto" rows="9" class="w-full rounded-md border bg-background px-2 py-1.5 font-mono text-xs" @input="g._editado = true" />
              </div>
            </div>
            <div v-if="atraso.excluidos.length" class="rounded-md border border-dashed px-3 py-2 text-xs">
              <div class="mb-1 font-semibold">Não podem entrar ({{ atraso.excluidos.length }})</div>
              <ul class="space-y-0.5">
                <li v-for="e in atraso.excluidos" :key="e.pedido_bling">
                  <span class="font-mono">{{ e.pedido_marketplace || e.pedido_bling }}</span> — {{ ATRASO_EXCLUSAO[e.motivo] || e.detalhe }}
                </li>
              </ul>
            </div>
          </template>
        </div>
        <div class="flex items-center justify-end gap-2 border-t px-5 py-3">
          <button type="button" class="rounded-md border px-3 py-1.5 text-sm hover:bg-muted" @click="fecharAtraso">
            {{ atraso.abertos ? 'fechar' : 'cancelar' }}
          </button>
          <button
            v-if="!atraso.abertos"
            type="button"
            class="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-sm font-semibold text-primary-foreground disabled:opacity-50"
            :disabled="atraso.enviando || atraso.loading || !atrasoGruposComPedidos.length || atrasoFaltaMotivoOutro"
            :title="atrasoFaltaMotivoOutro ? 'Escreva o motivo do chamado (opção outro)' : ''"
            @click="confirmarChamadosAtraso"
          >
            <Loader2 v-if="atraso.enviando" class="size-4 animate-spin" />
            <LifeBuoy v-else class="size-4" />
            Abrir {{ atrasoGruposComPedidos.length }} chamado(s)
          </button>
        </div>
      </div>
    </div>

    <!-- Aviso de estoque compartilhado: pedido que sai de 2+ armazéns -->
    <div
      v-if="avisoCompartilhado"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      @click.self="avisoCompartilhado = null"
    >
      <div class="w-full max-w-md overflow-hidden rounded-xl border-4 border-amber-500 bg-white shadow-2xl">
        <div class="flex items-center gap-3 bg-amber-500 px-5 py-4">
          <AlertTriangle class="size-8 shrink-0 text-white" />
          <div class="text-lg font-bold uppercase tracking-wide text-white">
            Atenção: estoque compartilhado
          </div>
        </div>
        <div class="px-5 py-5 text-sm text-gray-800">
          <template v-if="avisoCompartilhado.pedidos.length === 1">
            O pedido <span class="font-bold">{{ avisoCompartilhado.pedidos[0] }}</span>
            sai de <span class="font-bold">mais de um armazém</span>.
          </template>
          <template v-else>
            Os pedidos <span class="font-bold">{{ avisoCompartilhado.pedidos.join(', ') }}</span>
            saem de <span class="font-bold">mais de um armazém</span>.
          </template>
          <div class="mt-2 text-muted-foreground">
            Parte dos itens da declaração está em outro armazém.
          </div>
          <div class="mt-3 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 font-medium text-amber-800">
            Fale com o responsável do outro estoque antes de despachar, para o pedido sair completo.
          </div>
        </div>
        <div class="flex justify-end gap-2 border-t bg-gray-50 px-5 py-3">
          <button
            class="rounded-md border px-4 py-2 text-sm hover:bg-gray-100"
            @click="avisoCompartilhado = null"
          >
            Cancelar
          </button>
          <button
            class="rounded-md bg-amber-500 px-4 py-2 text-sm font-semibold text-white hover:bg-amber-600"
            @click="okCompartilhado"
          >
            Imprimir mesmo assim
          </button>
        </div>
      </div>
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
