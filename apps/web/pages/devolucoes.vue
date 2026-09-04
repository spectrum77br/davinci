<script setup lang="ts">
import {
  AlertCircle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Camera,
  ClipboardList,
  Clock,
  Download,
  ExternalLink,
  Loader2,
  Megaphone,
  PackagePlus,
  PackageSearch,
  Paperclip,
  Plus,
  RotateCcw,
  Search,
  Trash2,
  Undo2,
  X,
} from 'lucide-vue-next'
import { isoToday } from '~/lib/date'

definePageMeta({ middleware: ['permission'], permission: { resource: 'devolucoes', action: 'view' } })

type BlingStockResult = {
  ok: boolean
  action: string
  sku: string | null
  bling_product_id: number | null
  message: string
}

type DevolucaoAnexo = {
  id: string
  filename: string
  content_type: string
  size_bytes: number
  ml_file_name: string | null
  created_at: string
}

type DevolutionRow = {
  id: string
  data: string | null
  pedido_bling: string | null
  pedido_marketplace: string | null
  conta: string
  cliente?: string | null
  sku: string | null
  produtos: string | null
  custo_produto: number | null
  condicao_produto: string | null
  link_abertura: string | null
  reembolso: boolean
  motivo_devolucao: string | null
  video_url: string | null
  link_envio: string | null
  custo_manutencao: number | null
  tecnico: string | null
  devolver_estoque: boolean
  manutencao: boolean
  observacao: string | null
  troca_sku: string | null
  troca_condicao: string | null
  estoque_suffix: string | null
  quantidade: number | null
  estoque_destino_sku: string | null
  estoque_nova_tag: string | null
  manutencao_destino: string | null
  tag: string | null
  data_devolvido_estoque: string | null
  prazo: string | null
  estoque_mov_sku: string | null
  estoque_mov_bling_id: number | null
  estoque_mov_action: string | null
  estoque_mov_qty: number | null
  estoque_mov_revertido_at: string | null
  created_at: string
  updated_at: string
  bling_stock_result?: BlingStockResult | null
  // Chamado MAIS RECENTE do pedido (aba Chamados) — preenchido pela listagem.
  tem_chamado?: boolean
  chamado_numero?: string | null
  chamado_resolvido?: boolean | null
  // Abertura automática no Mercado Livre (pendente | enviada | falhou) + motivo.
  chamado_ml_status?: string | null
  chamado_ml_erro?: string | null
  chamado_plataforma?: string | null
  anexos?: DevolucaoAnexo[]
}

// Campos extras coletados pelos modais antes de chamar o estoque/situação Bling.
type StockModalFields = {
  troca_sku: string | null
  troca_condicao: string | null
  estoque_suffix: string | null
  manutencao_destino: string | null
  estoque_destino_sku: string | null
  estoque_nova_tag: string | null
}

type DevolutionPage = {
  items: DevolutionRow[]
  total: number
  limit: number
  offset: number
}

type LookupRow = {
  data: string | null
  pedido_bling: string | null
  pedido_marketplace: string | null
  conta: string
  sku: string | null
  produtos: string | null
  quantidade: number | null
  custo_produto: number | null
  nome_destinatario: string | null
  cep_destino: string | null
  ja_devolvido?: boolean
}

type DevolutionDraft = LookupRow & {
  condicao_produto: string
  link_abertura: string
  reembolso: boolean
  motivo_devolucao: string
  video_url: string
  link_envio: string
  fotos: File[]
  custo_manutencao: number | null
  tecnico: string
  devolver_estoque: boolean
  observacao: string
}

type ReembolsoFilter = 'all' | 'true' | 'false'

const PAGE_SIZE = 100
// Mesmas tags/rótulos do Controle de Estoque (pages/controle-estoque.vue).
// A tag da devolução é derivada do SKU pela mesma regra do backend
// (app/services/sku_tags.py), então o visual aqui espelha aquela tela.
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
const TAG_LABELS: Record<string, string> = Object.fromEntries(
  TAG_OPTIONS.map(o => [o.slug, o.label]),
)
const tagLabel = (tag: string | null): string => {
  if (!tag) return '—'
  return TAG_LABELS[tag.replace(/^\./, '').toLowerCase()] ?? tag
}

// Lista final pedida pelo Eduardo (03/09): saem Tamanho, Pacote Suspeito e
// Embalagem Externa Danificada; "Mudou de ideia" virou "Bloqueado" (03/09 à
// tarde — migration 0239 renomeou as linhas antigas). Linhas antigas com
// motivo removido continuam aparecendo no select via option de fallback
// (mesmo esquema da condição). Bloqueado / Golpe / Item faltando / Não
// recebido / Danificado (Outros) abrem chamado sozinhos (services/chamados.py).
const MOTIVOS_DEVOLUCAO = [
  'Bloqueado',
  'Golpe',
  'Item faltando',
  'Dano funcional / Não funciona',
  'Item Incorreto',
  'Não recebido',
  'Danificado (Outros)',
] as const

// Motivos que abrem chamado automático no Mercado Livre (services/chamados_devolucao):
// Golpe = pacote vazio; Item Incorreto = produto diferente (foto); Danificado (foto);
// Item faltando; Não recebido; Bloqueado (mala travada por senha).

// Abertura automática na plataforma — o que a linha mostra enquanto não está "enviada".
const ML_STATUS_ERROS: Record<string, string> = {
  devolucao_sem_foto: 'aguardando foto — anexe pela câmera',
  return_review_indisponivel: 'ML ainda não liberou a revisão (o pacote precisa constar entregue) — tenta de novo a cada hora',
  devolucao_sem_claim: 'sem devolução aberta na plataforma pra esse pedido — tenta de novo a cada hora',
  devolucao_sem_return: 'sem devolução aberta na plataforma pra esse pedido — tenta de novo a cada hora',
  devolucao_sem_pedido_marketplace: 'linha sem nº do pedido da plataforma',
  devolucao_prazo_esgotado: 'ficou 45 dias pendente — abrir na mão',
  devolucao_nao_encontrada: 'lançamento não encontrado',
  chamado_sem_integracao_ml: 'conta sem integração ML no DaVinci',
  chamado_sem_integracao_tiktok: 'conta sem integração TikTok no DaVinci',
  chamado_sem_integracao_shopee: 'conta sem integração Shopee no DaVinci',
  tiktok_aguardando_pacote: 'TikTok ainda não liberou a recusa (pacote precisa constar enviado/entregue) — tenta a cada hora',
  tiktok_arbitragem: 'em arbitragem na TikTok — aguardando',
  tiktok_quick_refund: 'TikTok já reembolsou (quick refund) — só apelação no Seller Center',
  tiktok_ja_recusada: 'já recusada na TikTok',
  tiktok_motivo_indisponivel: 'TikTok não aceita esse motivo nesse estado',
  shopee_aguardando_pacote: 'Shopee ainda não liberou a disputa — tenta a cada hora',
  shopee_ja_contestada: 'já contestada na Shopee',
  shopee_devolucao_encerrada: 'devolução já encerrada na Shopee',
  shopee_motivo_indisponivel: 'Shopee não oferece esse motivo pra essa devolução',
  shopee_sem_email: 'sem e-mail do operador pra Shopee (DEVOLUCAO_DISPUTE_EMAIL)',
  plataforma_sem_api: 'sem API — abrir na mão na plataforma',
}
const PLAT_NOME: Record<string, string> = { ml: 'ML', tiktok: 'TikTok', shopee: 'Shopee', amazon: 'Amazon' }
function platNome(row: DevolutionRow): string {
  const p = (row.chamado_plataforma || 'ml').toLowerCase()
  return PLAT_NOME[p] || p.toUpperCase()
}
function mlStatusLabel(row: DevolutionRow): string {
  const st = row.chamado_ml_status
  const plat = platNome(row)
  if (st === 'enviada') return `${plat}: aberto`
  if (st === 'pendente') return `${plat}: ${ML_STATUS_ERROS[row.chamado_ml_erro || ''] || 'pendente'}`
  if (st === 'falhou') return `${plat}: falhou — ${ML_STATUS_ERROS[row.chamado_ml_erro || ''] || row.chamado_ml_erro || ''}`
  if (st === 'registrada') return plat === 'Amazon' ? 'Amazon: SAFE-T só no Seller Central (sem API) — abrir na mão' : `${plat}: sem API — abrir na mão`
  return ''
}
function mlStatusClass(st: string | null | undefined): string {
  if (st === 'enviada') return 'text-emerald-700 dark:text-emerald-300'
  if (st === 'falhou') return 'text-red-600 dark:text-red-400'
  if (st === 'registrada') return 'text-sky-700 dark:text-sky-300'
  return 'text-amber-700 dark:text-amber-300'
}

const CONDICOES_PRODUTO = [
  'Novo',
  'Usado',
  'Manutenção',
  'Extraviado',
  'Trocado',
  'Não devolvido',
] as const

const TECNICOS = [
  'SmarPlay',
  'Bogota',
  'Shark',
  'Cybercell',
  'Factor',
] as const

const { api } = useApi()
const canEdit = useCan('devolucoes', 'edit')
const canDelete = useCan('devolucoes', 'delete')
const isAdmin = useIsAdmin()

// Modal do botão Informar (Threema) — só admins veem o botão.
const informarOpen = ref(false)

// Devolução de estoque é AUTOMÁTICA no insert para todas as condições que
// disparam estoque (Novo/Usado/Trocado) — sem toggle. A ÚNICA exceção é
// "Manutenção", que continua manual: aí o toggle aparece e é necessário.
// Mesmo nas Manutenções, só spectrum77 e sthevem7 podem ver/usar o toggle.
const _auth = useAuthStore()
const STOCK_TOGGLE_USERS = [
  'spectrum77@tuta.com',
  'sthevem7@tuta.com',
  'joffer4@tuta.com',
]
function canSeeStockToggle(condicao?: string | null): boolean {
  if (condicao !== 'Manutenção') return false
  const email = _auth.user?.email?.toLowerCase()
  return !!email && STOCK_TOGGLE_USERS.includes(email)
}

// ── Toast system ─────────────────────────────────────────────────────
type Toast = { id: number; kind: 'success' | 'error' | 'warning'; title: string; lines: string[] }
const toasts = ref<Toast[]>([])
let _toastId = 0
function pushToast(t: Omit<Toast, 'id'>, ttl = 6000) {
  const id = ++_toastId
  toasts.value = [...toasts.value, { id, ...t }]
  window.setTimeout(() => { toasts.value = toasts.value.filter((x) => x.id !== id) }, ttl)
}
function dismissToast(id: number) {
  toasts.value = toasts.value.filter((x) => x.id !== id)
}
function showStockToast(sr: BlingStockResult) {
  const lines = sr.message ? [sr.message] : []
  if (sr.ok) {
    const title = sr.action === 'product_created_usado'
      ? 'Bling · produto criado'
      : sr.action === 'stock_reversed'
        ? 'Bling · estoque estornado'
        : 'Bling · estoque atualizado'
    pushToast({ kind: 'success', title, lines })
  } else {
    const titles: Record<string, string> = {
      no_integration: 'Bling não conectado',
      no_sku: 'SKU não informado',
      sku_not_found: 'SKU não encontrado no Bling',
      create_failed: 'Erro ao criar produto no Bling',
      error: 'Erro no Bling',
    }
    pushToast({ kind: sr.action === 'error' ? 'error' : 'warning', title: titles[sr.action] ?? 'Bling · aviso', lines })
  }
}

const items = ref<DevolutionRow[]>([])
const total = ref(0)
const page = ref(1)
const loading = ref(false)
const error = ref<string | null>(null)

const search = ref('')
const reembolsoFilter = ref<ReembolsoFilter>('all')
const tagFilter = ref('all')
const condicaoFilter = ref('all')
// Filtro de período (data de devolução = created_at). Inclusivo nas duas pontas.
const dataInicioFilter = ref('')
const dataFimFilter = ref('')
// Mostrar só pedidos que já passaram em manutenção (manutencao = true).
const manutencaoFilter = ref(false)
// Filtro de prazo de manutenção (admin): 'vencidas' = só as já vencidas;
// 7/15/30 = prazo (created_at + 30d, condição Manutenção) vencendo em até N
// dias (inclui as vencidas).
const prazoDiasFilter = ref<'all' | 'vencidas' | '7' | '15' | '30'>('all')
const exporting = ref(false)

const addOpen = ref(false)
const lookupPedido = ref('')
const lookupLoading = ref(false)
const lookupResults = ref<LookupRow[]>([])
const lookupError = ref<string | null>(null)
const creating = ref(false)
const drafts = ref<DevolutionDraft[]>([])
const addedThisSession = ref<Set<string>>(new Set())

const dirtyRows = ref<Set<string>>(new Set())
const savingRows = ref<Set<string>>(new Set())

// ── Correção de estoque (entrada manual, sem criar devolução) ────────────
// Reaproveita os modais de destino (resolveStockModals) e a lógica Novo/Usado.
type ProductHit = { sku: string; name: string; cost_price: number | null; saldo_virtual_total: number | null }
const CONDICOES_CORRECAO = ['Novo', 'Usado'] as const
const correcaoOpen = ref(false)
const correcaoSku = ref('')
const correcaoCondicao = ref<(typeof CONDICOES_CORRECAO)[number]>('Novo')
const correcaoQtd = ref(1)
const correcaoProduto = ref('')
const correcaoCusto = ref('')
const correcaoObs = ref('')
const correcaoSubmitting = ref(false)
const correcaoError = ref<string | null>(null)
// Busca de produto (mesma lógica do pedido): mostra SKUs existentes e
// pré-preenche nome + custo ao selecionar.
const correcaoResults = ref<ProductHit[]>([])
const correcaoSearching = ref(false)
const correcaoShowResults = ref(false)
let correcaoSearchTimer: ReturnType<typeof setTimeout> | null = null

// ── Modais de estoque (abrem ANTES das chamadas Bling) ───────────────
// Condições que disparam estoque/situação ao adicionar ou ligar o toggle.
const STOCK_TRIGGER_CONDICOES = ['Novo', 'Usado', 'Trocado', 'Manutenção']
function isStockTrigger(condicao: string | null | undefined) {
  return !!condicao && STOCK_TRIGGER_CONDICOES.includes(condicao)
}
// Mala (b+dígito) e Eletro (u…) voltam direto no próprio SKU — sem modal de tag.
function isMalaOrEletro(sku: string | null | undefined) {
  return /^(b[0-9]|u)/i.test((sku || '').trim())
}

type TrocaResult = { sku: string; condicao: 'Novo' | 'Usado' }
const trocaModal = ref<{ open: boolean; soldSku: string | null; resolve: ((v: TrocaResult | null) => void) | null }>(
  { open: false, soldSku: null, resolve: null },
)
function askTrocaSku(soldSku: string | null): Promise<TrocaResult | null> {
  return new Promise((resolve) => { trocaModal.value = { open: true, soldSku, resolve } })
}
function onTrocaConfirm(payload: TrocaResult) {
  trocaModal.value.resolve?.(payload)
  trocaModal.value = { open: false, soldSku: null, resolve: null }
}
function onTrocaCancel() {
  trocaModal.value.resolve?.(null)
  trocaModal.value = { open: false, soldSku: null, resolve: null }
}

// Modal de Manutenção: Novo / Usado / Sucata.
type ManutencaoResult = { tipo: 'Novo' | 'Usado' | 'Sucata' }
const manutencaoModal = ref<{ open: boolean; sku: string | null; resolve: ((v: ManutencaoResult | null) => void) | null }>(
  { open: false, sku: null, resolve: null },
)
function askManutencao(sku: string | null): Promise<ManutencaoResult | null> {
  return new Promise((resolve) => { manutencaoModal.value = { open: true, sku, resolve } })
}
function onManutencaoConfirm(payload: ManutencaoResult) {
  manutencaoModal.value.resolve?.(payload)
  manutencaoModal.value = { open: false, sku: null, resolve: null }
}
function onManutencaoCancel() {
  manutencaoModal.value.resolve?.(null)
  manutencaoModal.value = { open: false, sku: null, resolve: null }
}

// Modal de destino de estoque: bin existente, tag p/ criar produto novo
// (z000N.<tag>) OU `suffix` p/ manter o SKU base.<sufixo> (ex.: .us).
type EstoqueResult = { destino_sku?: string; nova_tag?: string; suffix?: string }
const estoqueModal = ref<{ open: boolean; sku: string; condicao: string; fullDestino: boolean; resolve: ((v: EstoqueResult | null) => void) | null }>(
  { open: false, sku: '', condicao: '', fullDestino: false, resolve: null },
)
// fullDestino: modo "destino completo" (correção de mala) — mostra os bins
// regionais .mala/.pi/.sp pra escolher em Novo E Usado, criando base.<suffix>.
function askEstoque(sku: string, condicao: string, fullDestino = false): Promise<EstoqueResult | null> {
  return new Promise((resolve) => { estoqueModal.value = { open: true, sku, condicao, fullDestino, resolve } })
}
function onEstoqueConfirm(payload: EstoqueResult) {
  estoqueModal.value.resolve?.(payload)
  estoqueModal.value = { open: false, sku: '', condicao: '', fullDestino: false, resolve: null }
}
function onEstoqueCancel() {
  estoqueModal.value.resolve?.(null)
  estoqueModal.value = { open: false, sku: '', condicao: '', fullDestino: false, resolve: null }
}

/**
 * Abre, em sequência, os modais necessários antes de devolver ao estoque.
 * Retorna os campos extras a enviar no body, ou `null` se o usuário
 * cancelar qualquer modal (operação deve ser abortada — sem chamada Bling).
 */
async function resolveStockModals(
  condicao: string | null,
  sku: string | null,
  devolverEstoque: boolean,
  // Na correção de estoque a mala Novo NÃO volta direto no avulso: força abrir o
  // modal de destino pra o operador escolher o bin regional (.mala/.pi/.sp),
  // igual na devolução de pedido (lá o SKU já vem com o sufixo do pedido).
  forcarDestinoMala = false,
): Promise<StockModalFields | null> {
  const empty: StockModalFields = {
    troca_sku: null, troca_condicao: null, estoque_suffix: null,
    manutencao_destino: null, estoque_destino_sku: null, estoque_nova_tag: null,
  }
  if (!devolverEstoque || !isStockTrigger(condicao)) return empty

  const out: StockModalFields = { ...empty }
  let effSku = (sku || '').trim()
  let effCondicao = condicao as string

  // Manutenção — escolher Novo / Usado / Sucata. Sucata não mexe no estoque.
  if (condicao === 'Manutenção') {
    const m = await askManutencao(sku)
    if (!m) return null
    out.manutencao_destino = m.tipo
    if (m.tipo === 'Sucata') return out
    effCondicao = m.tipo
  }

  // Trocado — escolher o SKU que de fato voltou + condição (Novo/Usado).
  if (condicao === 'Trocado') {
    const r = await askTrocaSku(sku)
    if (!r) return null
    out.troca_sku = r.sku
    out.troca_condicao = r.condicao
    effSku = r.sku.trim()
    effCondicao = r.condicao
  }

  // Mala/Eletro NOVO: volta direto no próprio SKU, sem modal de tag/bin.
  // Usado segue a lógica de usados (modal → cria z000N.<tag>). Na correção de
  // estoque (forcarDestinoMala) NÃO pega o atalho: abre o modal pra escolher o
  // bin regional (.mala/.pi/.sp), já que o avulso base não traz o sufixo.
  if (effCondicao === 'Novo' && isMalaOrEletro(effSku) && !forcarDestinoMala) {
    out.estoque_destino_sku = effSku
    return out
  }

  // Destino de estoque: bin existente ou criação de produto novo (z000N.<tag>).
  // Na correção de mala/eletro (forcarDestinoMala) abre em modo "destino completo":
  // lista os bins regionais .mala/.pi/.sp pra escolher, tanto Novo quanto Usado.
  const fullDestino = forcarDestinoMala && isMalaOrEletro(effSku)
  const dest = await askEstoque(effSku, effCondicao, fullDestino)
  if (!dest) return null
  out.estoque_destino_sku = dest.destino_sku ?? null
  out.estoque_nova_tag = dest.nova_tag ?? null
  out.estoque_suffix = dest.suffix ?? null
  return out
}

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))

const expandedLookupResults = computed<LookupRow[]>(() => {
  const rows: LookupRow[] = []
  for (const row of lookupResults.value) {
    if (row.sku && row.sku.includes('+')) {
      const skus = row.sku.split('+').map((s) => s.trim()).filter(Boolean)
      const splitCost = row.custo_produto != null ? row.custo_produto / skus.length : null
      for (const s of skus) {
        rows.push({ ...row, sku: s, custo_produto: splitCost })
      }
    } else {
      rows.push(row)
    }
  }
  return rows
})

const alreadyAddedKeys = computed(() => {
  const keys = new Set<string>()
  for (const row of items.value) {
    keys.add(`${row.pedido_bling}|${row.sku}`)
  }
  for (const k of addedThisSession.value) {
    keys.add(k)
  }
  return keys
})

function isAlreadyAdded(row: LookupRow) {
  // O backend já marca itens com devolução lançada (não depende da página
  // carregada da tabela); mantemos os keys locais p/ o que foi criado agora.
  return row.ja_devolvido === true
    || alreadyAddedKeys.value.has(`${row.pedido_bling}|${row.sku}`)
}
// Produtos do pedido ainda não adicionados — todos entram de uma vez no rascunho.
const selectableLookupCount = computed(
  () => expandedLookupResults.value.filter((r) => !isAlreadyAdded(r)).length,
)
const rangeStart = computed(() => total.value === 0 ? 0 : (page.value - 1) * PAGE_SIZE + 1)
const rangeEnd = computed(() => Math.min(page.value * PAGE_SIZE, total.value))
const totalCustoManutencao = computed(() => items.value.reduce((a, r) => a + (r.custo_manutencao ?? 0), 0))
const totalReembolsadas = computed(() => items.value.filter((r) => r.reembolso).length)

const sheetInputClass = 'h-7 w-full rounded-none border-0 bg-transparent px-1 text-xs focus:bg-background focus:outline-none focus:ring-1 focus:ring-primary disabled:cursor-default disabled:opacity-70'
const sheetSelectClass = `${sheetInputClass} cursor-pointer`
const sheetMoneyInputClass = `${sheetInputClass} text-right tabular-nums`
const sheetInputRequiredClass = `${sheetInputClass} ring-1 ring-red-400`
const sheetSelectRequiredClass = `${sheetSelectClass} ring-1 ring-red-400`

function linkRequired(condicao: string | null | undefined) {
  return condicao === 'Extraviado' || condicao === 'Manutenção'
}

// Motivos que abrem chamado automático (espelho de services/chamados.MOTIVOS_ABREM_CHAMADO).
const MOTIVOS_ABREM_CHAMADO = ['bloqueado', 'mudou de ideia', 'golpe', 'item incorreto', 'item faltando', 'não recebido', 'danificado (outros)']
// Mala (b<dígito>, bp*, acessórios a006/a015/a073-a076) ou eletro (celular dg*, airfryer/eletro u*) —
// espelho de services/chamados_devolucao.produto_mala_ou_eletro; o backend é quem trava (422).
function isMalaOuEletro(sku: string | null | undefined) {
  const partes = (sku || '').toLowerCase().replace(/,/g, '+').split('+')
  return partes.some((p) => {
    const base = p.trim().split('.')[0]
    if (!base) return false
    return /^b\d/.test(base) || /^bp\d/.test(base) || ['a006', 'a015', 'a073', 'a074', 'a075', 'a076'].includes(base) || /^(dg|u)/.test(base)
  })
}
// Trava (Eduardo 04/09): "mala e eletro é obrigatória, desde que esteja nos motivos que abrem chamado".
function linkEnvioRequired(sku: string | null | undefined, motivo: string | null | undefined) {
  return MOTIVOS_ABREM_CHAMADO.includes((motivo || '').trim().toLowerCase()) && isMalaOuEletro(sku)
}

function apiError(e: any) {
  const detail = e?.data?.detail
  if (detail && typeof detail === 'object') return detail.message || detail.code || e?.message || 'erro'
  return detail || e?.message || 'erro'
}

function brl(v: number | null | undefined) {
  if (v == null) return '—'
  return v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
}

function saldoUn(v: number | null | undefined) {
  return v == null ? '—' : `${v} un.`
}

function normalizeUrl(v: string | null | undefined) {
  if (!v) return ''
  const trimmed = v.trim()
  if (!trimmed) return ''
  if (/^https?:\/\//i.test(trimmed)) return trimmed
  return `https://${trimmed}`
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

// Prazo: só a data (dd/mm/aa). Vencido = data no passado (ignora a hora).
function fmtDate(v: string | null) {
  if (!v) return '—'
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return v
  return d.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit', year: '2-digit' })
}
function prazoOverdue(v: string | null): boolean {
  if (!v) return false
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return false
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  return d.getTime() < today.getTime()
}
// Dias restantes até o prazo (negativo = vencido). Compara só a data.
function prazoDiasLabel(v: string | null): string {
  if (!v) return ''
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return ''
  d.setHours(0, 0, 0, 0)
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const dias = Math.round((d.getTime() - today.getTime()) / 86_400_000)
  if (dias > 0) return `faltam ${dias}d`
  if (dias === 0) return 'vence hoje'
  return `vencido há ${-dias}d`
}

function markDirty(id: string) {
  const next = new Set(dirtyRows.value)
  next.add(id)
  dirtyRows.value = next
}
function clearDirty(id: string) {
  const next = new Set(dirtyRows.value)
  next.delete(id)
  dirtyRows.value = next
}
function hasDirty(id: string) { return dirtyRows.value.has(id) }
function setSaving(id: string, v: boolean) {
  const next = new Set(savingRows.value)
  if (v) next.add(id); else next.delete(id)
  savingRows.value = next
}
function isSaving(id: string) { return savingRows.value.has(id) }
function numberOrNull(value: string) {
  if (value === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function setRowNumber(row: DevolutionRow, field: 'custo_produto' | 'custo_manutencao', value: string) {
  row[field] = numberOrNull(value)
  markDirty(row.id)
}

function setRowText(
  row: DevolutionRow,
  field: 'condicao_produto' | 'link_abertura' | 'link_envio' | 'video_url' | 'motivo_devolucao' | 'tecnico' | 'observacao',
  value: string,
) {
  row[field] = value || null
  markDirty(row.id)
}

function setRowDevolverEstoque(row: DevolutionRow, value: boolean) {
  row.devolver_estoque = value
  markDirty(row.id)
}

function setRowReembolso(row: DevolutionRow, value: boolean) {
  row.reembolso = value
  markDirty(row.id)
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const params = new URLSearchParams()
    params.set('limit', String(PAGE_SIZE))
    params.set('offset', String((page.value - 1) * PAGE_SIZE))
    if (search.value.trim()) params.set('search', search.value.trim())
    if (reembolsoFilter.value !== 'all') params.set('reembolso', reembolsoFilter.value)
    if (tagFilter.value !== 'all') params.set('tag', tagFilter.value)
    if (condicaoFilter.value !== 'all') params.set('condicao', condicaoFilter.value)
    if (dataInicioFilter.value) params.set('data_inicio', dataInicioFilter.value)
    if (dataFimFilter.value) params.set('data_fim', dataFimFilter.value)
    if (manutencaoFilter.value) params.set('manutencao', 'true')
    if (isAdmin.value && prazoDiasFilter.value === 'vencidas') params.set('prazo_vencido', 'true')
    else if (isAdmin.value && prazoDiasFilter.value !== 'all') params.set('prazo_dias', prazoDiasFilter.value)
    const res = await api<DevolutionPage>(`/api/devolutions?${params.toString()}`)
    items.value = res.items
    total.value = res.total
    dirtyRows.value = new Set()
  } catch (e: any) {
    error.value = apiError(e)
  } finally {
    loading.value = false
  }
}

async function exportXlsx() {
  if (exporting.value) return
  exporting.value = true
  try {
    const params = new URLSearchParams()
    if (search.value.trim()) params.set('search', search.value.trim())
    if (reembolsoFilter.value !== 'all') params.set('reembolso', reembolsoFilter.value)
    if (tagFilter.value !== 'all') params.set('tag', tagFilter.value)
    if (condicaoFilter.value !== 'all') params.set('condicao', condicaoFilter.value)
    if (dataInicioFilter.value) params.set('data_inicio', dataInicioFilter.value)
    if (dataFimFilter.value) params.set('data_fim', dataFimFilter.value)
    if (manutencaoFilter.value) params.set('manutencao', 'true')
    if (isAdmin.value && prazoDiasFilter.value === 'vencidas') params.set('prazo_vencido', 'true')
    else if (isAdmin.value && prazoDiasFilter.value !== 'all') params.set('prazo_dias', prazoDiasFilter.value)
    const blob = await api<Blob>(`/api/devolutions/export.xlsx?${params.toString()}`, { responseType: 'blob' as any })
    const href = URL.createObjectURL(blob as any)
    const a = document.createElement('a')
    a.href = href
    a.download = `devolucoes_${isoToday()}.xlsx`
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(href)
  } catch (e: any) {
    pushToast({ kind: 'error', title: 'Erro ao exportar', lines: [apiError(e)] })
  } finally {
    exporting.value = false
  }
}

// ── Aba Acompanhamento: pedidos hoje em "Aguardando Devolução" no Bling ──
// Linha por ITEM do pedido (grão da vw_devolucoes). Rastreio/localização são
// por PEDIDO e preenchidos à mão: salvar numa linha reflete nas linhas irmãs
// do mesmo pedido. A data da última movimentação é carimbada pelo backend
// sozinha quando a localização muda.
type AcompanhamentoRow = {
  pedido_bling: string | null
  pedido_marketplace: string | null
  data: string | null
  aguardando_devolucao_data: string | null
  dias_em_devolucao: number | null
  // true = data estimada (sinal da Logística / backfill) — mostra "≈".
  aguardando_devolucao_data_estimada: boolean
  plataforma: string | null
  loja: string | null
  cliente: string | null
  cidade: string | null
  uf: string | null
  sku: string | null
  produto: string | null
  quantidade: number | null
  rastreio: string | null
  localizacao: string | null
  localizacao_data: string | null
  // Entrega original quando `localizacao` é o status de uma devolução viva.
  entrega_localizacao: string | null
  lancada: boolean
}
type AcompanhamentoPage = { items: AcompanhamentoRow[]; total_pedidos: number }
type RastreioSaved = {
  pedido_bling: string
  rastreio: string | null
  localizacao: string | null
  localizacao_data: string | null
  // Entrega original quando `localizacao` é o status de uma devolução viva.
  entrega_localizacao: string | null
  // "Em devolução desde" efetivo + dias, pra espelhar na linha após editar.
  aguardando_devolucao_data: string | null
  dias_em_devolucao: number | null
  // true = data estimada (sinal da Logística / backfill) — mostra "≈".
  aguardando_devolucao_data_estimada: boolean
}

type Tab = 'acompanhamento' | 'lancamentos'
// Acompanhamento é a aba inicial (folha do Eduardo, 2026-09-02); a aba
// Lançamentos guarda TODO o conteúdo antigo da página, intacto.
const tab = ref<Tab>('acompanhamento')

const acompRows = ref<AcompanhamentoRow[]>([])
const acompLoading = ref(false)
const acompError = ref<string | null>(null)
const acompSearch = ref('')
const acompPlataformaFilter = ref('all')
const acompLojaFilter = ref('all')
const acompParadoFilter = ref<'all' | '7' | '15' | '30'>('all')
// Chaves "pedido|campo" com PATCH em voo — trava o input e evita corrida.
const acompSaving = ref<Set<string>>(new Set())

async function loadAcompanhamento() {
  acompLoading.value = true
  acompError.value = null
  try {
    const res = await api<AcompanhamentoPage>('/api/devolutions/acompanhamento')
    acompRows.value = res.items
  } catch (e: any) {
    acompError.value = apiError(e)
  } finally {
    acompLoading.value = false
  }
}

// "Atualizar clientes": reaproveita o backfill de endereços da aba Lançamentos
// (busca no Bling nome/cidade dos pedidos em devolução sem endereço) e
// recarrega a tabela pra mostrar o resultado.
async function backfillAcompanhamento() {
  await backfillAddresses()
  await loadAcompanhamento()
}

const acompPlataformas = computed(() => {
  const s = new Set<string>()
  for (const r of acompRows.value) if (r.plataforma) s.add(r.plataforma)
  return [...s].sort()
})
const acompLojas = computed(() => {
  const s = new Set<string>()
  for (const r of acompRows.value) if (r.loja) s.add(r.loja)
  return [...s].sort()
})

const acompFiltered = computed(() => {
  const term = acompSearch.value.trim().toLowerCase()
  const minDias = acompParadoFilter.value === 'all' ? null : Number(acompParadoFilter.value)
  return acompRows.value.filter((r) => {
    if (acompPlataformaFilter.value !== 'all' && r.plataforma !== acompPlataformaFilter.value) return false
    if (acompLojaFilter.value !== 'all' && r.loja !== acompLojaFilter.value) return false
    if (minDias != null && (r.dias_em_devolucao ?? -1) < minDias) return false
    if (term) {
      const hay = [
        r.pedido_bling, r.pedido_marketplace, r.cliente, r.sku, r.produto,
        r.rastreio, r.localizacao, r.cidade,
      ].filter(Boolean).join(' ').toLowerCase()
      if (!hay.includes(term)) return false
    }
    return true
  })
})

// Chips por PEDIDO (a tabela tem uma linha por item do pedido).
function pedidosDe(rows: AcompanhamentoRow[]): Set<string> {
  const s = new Set<string>()
  for (const r of rows) if (r.pedido_bling) s.add(r.pedido_bling)
  return s
}
const acompTotalPedidos = computed(() => pedidosDe(acompRows.value).size)
const acompSemRastreio = computed(() => pedidosDe(acompRows.value.filter((r) => !r.rastreio)).size)
const acompSemLocalizacao = computed(() => pedidosDe(acompRows.value.filter((r) => !r.localizacao)).size)
const acompParados15 = computed(() => pedidosDe(acompRows.value.filter((r) => (r.dias_em_devolucao ?? 0) >= 15)).size)

// Data pura (YYYY-MM-DD) SEM passar por new Date() — evita o clássico
// "-1 dia" do fuso (Date interpreta como meia-noite UTC).
function fmtDateOnly(v: string | null): string {
  if (!v) return '—'
  const [y, m, d] = v.split('-')
  if (!y || !m || !d) return v
  return `${d}/${m}/${y.slice(2)}`
}
function diasBadgeClass(dias: number | null): string {
  if (dias == null) return 'bg-muted text-muted-foreground'
  if (dias >= 15) return 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300'
  if (dias >= 7) return 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300'
  return 'bg-muted text-muted-foreground'
}

function isSavingRastreio(pedido: string | null, field: string): boolean {
  return !!pedido && acompSaving.value.has(`${pedido}|${field}`)
}

// Salva no blur (Enter também: só tira o foco do campo). Campo por PEDIDO:
// espelha a resposta do backend em todas as linhas do mesmo pedido. Só chama
// a API se o valor realmente mudou; string vazia limpa o campo.
async function saveRastreio(
  row: AcompanhamentoRow,
  field: 'rastreio' | 'localizacao' | 'em_devolucao_desde',
  raw: string,
) {
  if (!canEdit.value || !row.pedido_bling) return
  const value = raw.trim()
  // "Em devolução desde" na mão (03/09, caso 287144): data vale mais que o
  // automático; vazio = null = volta ao automático.
  const atual = field === 'em_devolucao_desde' ? (row.aguardando_devolucao_data ?? '') : (row[field] ?? '')
  if (atual === value) return
  const key = `${row.pedido_bling}|${field}`
  if (acompSaving.value.has(key)) return
  acompSaving.value = new Set([...acompSaving.value, key])
  try {
    const body = field === 'em_devolucao_desde' ? { em_devolucao_desde: value || null } : { [field]: value }
    const res = await api<RastreioSaved>(
      `/api/devolutions/acompanhamento/${encodeURIComponent(row.pedido_bling)}`,
      { method: 'PATCH', body },
    )
    for (const r of acompRows.value) {
      if (r.pedido_bling === res.pedido_bling) {
        r.rastreio = res.rastreio
        r.localizacao = res.localizacao
        r.localizacao_data = res.localizacao_data
        r.entrega_localizacao = res.entrega_localizacao
        r.aguardando_devolucao_data = res.aguardando_devolucao_data
        r.dias_em_devolucao = res.dias_em_devolucao
      }
    }
  } catch (e: any) {
    pushToast({ kind: 'error', title: 'Erro ao salvar rastreio', lines: [apiError(e)] })
  } finally {
    const next = new Set(acompSaving.value)
    next.delete(key)
    acompSaving.value = next
  }
}

// "Lançar": pula pra aba Lançamentos com o pedido já buscado — mesmo fluxo
// do botão "adicionar pedido".
function goLancar(row: AcompanhamentoRow) {
  if (!row.pedido_bling) return
  tab.value = 'lancamentos'
  openAdd()
  lookupPedido.value = row.pedido_bling
  void lookupOrder()
}

// As duas abas carregam juntas (paralelo): trocar de aba é instantâneo.
await Promise.all([load(), loadAcompanhamento()])

let searchTimer: ReturnType<typeof setTimeout> | null = null
watch(search, () => {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    page.value = 1
    load()
  }, 300)
})
watch([reembolsoFilter, tagFilter, condicaoFilter, dataInicioFilter, dataFimFilter, manutencaoFilter, prazoDiasFilter], () => { page.value = 1; load() })
watch(page, () => load())

function openAdd() {
  addOpen.value = true
  lookupPedido.value = ''
  lookupResults.value = []
  lookupError.value = null
  drafts.value = []
  addedThisSession.value = new Set()
}
function closeAdd() {
  addOpen.value = false
  lookupPedido.value = ''
  lookupResults.value = []
  lookupError.value = null
  drafts.value = []
  addedThisSession.value = new Set()
}

function openCorrecao() {
  correcaoSku.value = ''
  correcaoCondicao.value = 'Novo'
  correcaoQtd.value = 1
  correcaoProduto.value = ''
  correcaoCusto.value = ''
  correcaoObs.value = ''
  correcaoError.value = null
  correcaoResults.value = []
  correcaoShowResults.value = false
  correcaoOpen.value = true
}
function closeCorrecao() {
  correcaoOpen.value = false
}

// Busca incremental de produtos (debounced) — mesma fonte do modal de troca.
function onCorrecaoSkuInput() {
  correcaoShowResults.value = true
  if (correcaoSearchTimer) clearTimeout(correcaoSearchTimer)
  correcaoSearchTimer = setTimeout(runCorrecaoSearch, 300)
}
async function runCorrecaoSearch() {
  const term = correcaoSku.value.trim()
  if (!term) { correcaoResults.value = []; return }
  correcaoSearching.value = true
  try {
    correcaoResults.value = await api<ProductHit[]>(`/api/devolutions/product-search?q=${encodeURIComponent(term)}`)
  } catch {
    correcaoResults.value = []
  } finally {
    correcaoSearching.value = false
  }
}
// Selecionar um produto preenche SKU + nome + custo (igual ao fluxo do pedido).
function pickCorrecaoProduct(r: ProductHit) {
  correcaoSku.value = r.sku
  correcaoProduto.value = r.name
  correcaoCusto.value = r.cost_price != null ? String(r.cost_price) : ''
  correcaoResults.value = []
  correcaoShowResults.value = false
}
function onCorrecaoSkuBlur() {
  // Atraso pra permitir que o clique numa opção registre antes de esconder.
  window.setTimeout(() => { correcaoShowResults.value = false }, 150)
}

// Correção de estoque: abre os MESMOS modais de destino da página
// (bin existente / criação de z000N.<tag>) e lança a entrada no Bling via API,
// sem criar registro de devolução nem mexer na situação de pedido.
async function submitCorrecao() {
  if (!canEdit.value || correcaoSubmitting.value) return
  const sku = correcaoSku.value.trim()
  if (!sku) { correcaoError.value = 'Informe o SKU'; return }
  const qtd = Math.max(1, Math.floor(Number(correcaoQtd.value) || 1))
  correcaoError.value = null
  // Resolve o destino pelos modais (Novo/Usado seguem a lógica já existente).
  // forcarDestinoMala=true: na correção a mala Novo NÃO volta direto no avulso —
  // abre o modal pra o operador escolher o bin regional (.mala/.pi/.sp).
  const extra = await resolveStockModals(correcaoCondicao.value, sku, true, true)
  if (extra === null) return // modal cancelado → aborta sem chamar a API
  correcaoSubmitting.value = true
  try {
    const sr = await api<BlingStockResult>('/api/devolutions/stock-correction', {
      method: 'POST',
      body: {
        sku,
        condicao_produto: correcaoCondicao.value,
        quantidade: qtd,
        produtos: correcaoProduto.value.trim() || null,
        custo_produto: numberOrNull(correcaoCusto.value),
        observacao: correcaoObs.value.trim() || null,
        troca_sku: extra.troca_sku,
        troca_condicao: extra.troca_condicao,
        estoque_suffix: extra.estoque_suffix,
        estoque_destino_sku: extra.estoque_destino_sku,
        estoque_nova_tag: extra.estoque_nova_tag,
        manutencao_destino: extra.manutencao_destino,
      },
    })
    showStockToast(sr)
    if (sr.ok) closeCorrecao()
    else correcaoError.value = sr.message || 'falha ao atualizar estoque'
  } catch (e: any) {
    correcaoError.value = apiError(e)
  } finally {
    correcaoSubmitting.value = false
  }
}

// Um único "usar" para o pedido: traz TODOS os produtos (que ainda não foram
// adicionados) como linhas de rascunho, cada uma com sua própria condição.
function selectAllProducts() {
  drafts.value = expandedLookupResults.value
    .filter((row) => !isAlreadyAdded(row))
    .map((row) => ({
      ...row,
      condicao_produto: '',
      link_abertura: '',
      reembolso: false,
      motivo_devolucao: '',
      video_url: '',
      link_envio: '',
      fotos: [],
      custo_manutencao: null,
      tecnico: '',
      devolver_estoque: false,
      observacao: '',
    }))
}

async function lookupOrder() {
  const pedido = lookupPedido.value.trim()
  if (!pedido) return
  lookupLoading.value = true
  lookupError.value = null
  lookupResults.value = []
  drafts.value = []
  try {
    const res = await api<LookupRow[]>(`/api/devolutions/order-lookup?pedido=${encodeURIComponent(pedido)}`)
    lookupResults.value = res
    if (!res.length) lookupError.value = 'nenhum resultado encontrado'
  } catch (e: any) {
    lookupError.value = apiError(e)
  } finally {
    lookupLoading.value = false
  }
}

function buildPayload(d: DevolutionDraft) {
  return {
    data: d.data,
    pedido_bling: d.pedido_bling,
    pedido_marketplace: d.pedido_marketplace,
    conta: d.conta,
    sku: d.sku,
    produtos: d.produtos,
    custo_produto: d.custo_produto,
    condicao_produto: d.condicao_produto || null,
    link_abertura: d.link_abertura || null,
    reembolso: d.reembolso,
    motivo_devolucao: d.motivo_devolucao || null,
    video_url: d.video_url || null,
    link_envio: d.link_envio || null,
    custo_manutencao: d.custo_manutencao,
    tecnico: d.tecnico || null,
    devolver_estoque: d.devolver_estoque,
    observacao: d.observacao || null,
    quantidade: d.quantidade ?? 1,
    troca_sku: null as string | null,
    troca_condicao: null as string | null,
    estoque_suffix: null as string | null,
    manutencao_destino: null as string | null,
    estoque_destino_sku: null as string | null,
    estoque_nova_tag: null as string | null,
  }
}

// Adiciona TODOS os produtos do rascunho de uma vez. Cada produto pode disparar
// os modais (manutenção / troca / destino de estoque), resolvidos em sequência.
// Produtos que falharem ou cujo modal for cancelado continuam no rascunho.
async function createAllDevolutions() {
  if (!canEdit.value || !drafts.value.length) return
  for (const d of drafts.value) {
    if (!d.condicao_produto) {
      lookupError.value = 'Escolha a condição de todos os produtos'
      return
    }
    if (linkRequired(d.condicao_produto) && !d.link_abertura) {
      lookupError.value = 'Link de abertura obrigatório para Extraviado / Manutenção'
      return
    }
    if (linkEnvioRequired(d.sku, d.motivo_devolucao) && !d.link_envio) {
      lookupError.value = 'Link de envio obrigatório: mala/eletro com motivo que abre chamado'
      return
    }
  }
  creating.value = true
  lookupError.value = null
  const remaining: DevolutionDraft[] = []
  let added = 0
  try {
    for (const d of [...drafts.value]) {
      // No ADD: Novo/Usado/Trocado SEMPRE processam estoque (automático, sem
      // toggle). Manutenção só processa se o operador ligou o toggle (continua
      // manual); senão pode ser devolvida ao estoque depois, pela linha salva.
      // Extraviado e demais condições não mexem no estoque.
      const processAtAdd =
        ['Novo', 'Usado', 'Trocado'].includes(d.condicao_produto) ||
        (d.condicao_produto === 'Manutenção' && d.devolver_estoque)
      let extra: StockModalFields | null = null
      if (processAtAdd) {
        // Abre os modais (manutenção / troca / destino) antes da chamada Bling.
        extra = await resolveStockModals(d.condicao_produto, d.sku, true)
        if (extra === null) { remaining.push(d); continue } // modal cancelado → mantém
        d.devolver_estoque = true // processou o estoque → liga o toggle
      }
      const body = buildPayload(d)
      if (extra) Object.assign(body, extra)
      try {
        let created = await api<DevolutionRow>('/api/devolutions', { method: 'POST', body })
        // Fotos escolhidas no rascunho sobem agora (a linha precisa existir) —
        // cada upload já re-dispara o chamado no ML se ele estiver esperando foto.
        for (const f of d.fotos || []) {
          const fd = new FormData()
          fd.append('file', f)
          try {
            created = await api<DevolutionRow>(`/api/devolutions/${encodeURIComponent(created.id)}/anexos`, { method: 'POST', body: fd })
          } catch (e: any) {
            lookupError.value = `foto ${f.name}: ${apiError(e)}`
          }
        }
        if (page.value === 1) items.value = [created, ...items.value].slice(0, PAGE_SIZE)
        total.value += 1
        addedThisSession.value = new Set([...addedThisSession.value, `${created.pedido_bling}|${created.sku}`])
        added += 1
        if (created.bling_stock_result) showStockToast(created.bling_stock_result)
      } catch (e: any) {
        lookupError.value = apiError(e)
        remaining.push(d)
      }
    }
    drafts.value = remaining
    if (added > 0) {
      pushToast({ kind: 'success', title: 'Devoluções adicionadas', lines: [`${added} produto${added === 1 ? '' : 's'}`] })
    }
  } finally {
    creating.value = false
  }
}

function rowPatchPayload(row: DevolutionRow) {
  return {
    custo_produto: row.custo_produto,
    condicao_produto: row.condicao_produto || null,
    link_abertura: row.link_abertura || null,
    reembolso: row.reembolso,
    motivo_devolucao: row.motivo_devolucao || null,
    video_url: row.video_url || null,
    link_envio: row.link_envio || null,
    custo_manutencao: row.custo_manutencao,
    tecnico: row.tecnico || null,
    devolver_estoque: row.devolver_estoque,
    observacao: row.observacao || null,
    quantidade: row.quantidade ?? 1,
    troca_sku: row.troca_sku,
    troca_condicao: row.troca_condicao,
    estoque_suffix: row.estoque_suffix,
    manutencao_destino: row.manutencao_destino,
    estoque_destino_sku: row.estoque_destino_sku,
    estoque_nova_tag: row.estoque_nova_tag,
  }
}

function applyStockModalFields(row: DevolutionRow, extra: StockModalFields) {
  row.troca_sku = extra.troca_sku
  row.troca_condicao = extra.troca_condicao
  row.estoque_suffix = extra.estoque_suffix
  row.manutencao_destino = extra.manutencao_destino
  row.estoque_destino_sku = extra.estoque_destino_sku
  row.estoque_nova_tag = extra.estoque_nova_tag
}

// Toggle "devolver estoque" inline: abre os modais antes de salvar/chamar Bling.
async function toggleRowDevolverEstoque(row: DevolutionRow) {
  if (!canEdit.value) return
  const next = !row.devolver_estoque
  if (next && isStockTrigger(row.condicao_produto)) {
    const extra = await resolveStockModals(row.condicao_produto, row.sku, true)
    if (extra === null) return // cancelado → não liga o toggle
    applyStockModalFields(row, extra)
  }
  setRowDevolverEstoque(row, next)
  await saveRow(row)
}

// Mudança de condição inline: se devolver_estoque on e condição dispara
// estoque, abre os modais antes de salvar. Cancelar reverte a condição.
async function changeRowCondicao(row: DevolutionRow, value: string) {
  if (!canEdit.value) return
  const prev = row.condicao_produto
  setRowText(row, 'condicao_produto', value)
  // Ao SAIR de Manutenção, o custo de manutenção é obrigatório (registra o
  // reparo antes de marcar que o pedido passou em manutenção).
  if (prev === 'Manutenção' && value !== 'Manutenção' && (row.custo_manutencao == null || (row.custo_manutencao as unknown) === '')) {
    pushToast({
      kind: 'warning',
      title: 'Custo de manutenção obrigatório',
      lines: ['Preencha o custo de manutenção antes de mudar a condição.'],
    })
    setRowText(row, 'condicao_produto', prev ?? '')
    return
  }
  if (value === 'Extraviado' || value === 'Manutenção') setRowReembolso(row, true)
  // Novo/Usado/Trocado: estoque é automático — ao mudar a condição já abre o
  // modal (quando precisa) e devolve ao estoque, sem depender do toggle.
  // Manutenção: continua manual (só processa se o toggle já estiver ligado).
  const autoStock = ['Novo', 'Usado', 'Trocado'].includes(value)
  const manualStock = value === 'Manutenção' && row.devolver_estoque
  if (autoStock || manualStock) {
    const extra = await resolveStockModals(value, row.sku, true)
    if (extra === null) {
      setRowText(row, 'condicao_produto', prev ?? '')
      return
    }
    applyStockModalFields(row, extra)
    if (autoStock) setRowDevolverEstoque(row, true)
  }
  await saveRow(row)
}

async function saveRow(row: DevolutionRow) {
  if (!canEdit.value || !hasDirty(row.id) || isSaving(row.id)) return
  if (linkRequired(row.condicao_produto) && !row.link_abertura) {
    error.value = 'Link de abertura obrigatório para Extraviado / Manutenção'
    return
  }
  if (linkEnvioRequired(row.sku, row.motivo_devolucao) && !row.link_envio) {
    error.value = 'Link de envio obrigatório: mala/eletro com motivo que abre chamado'
    return
  }
  setSaving(row.id, true)
  error.value = null
  try {
    const updated = await api<DevolutionRow>(`/api/devolutions/${encodeURIComponent(row.id)}`, {
      method: 'PATCH',
      body: rowPatchPayload(row),
    })
    const idx = items.value.findIndex((i) => i.id === row.id)
    // PATCH não devolve `cliente` (só a listagem preenche) — preserva o da linha.
    if (idx >= 0) items.value[idx] = { ...updated, cliente: updated.cliente ?? row.cliente }
    clearDirty(row.id)
    if (updated.bling_stock_result) showStockToast(updated.bling_stock_result)
  } catch (e: any) {
    error.value = apiError(e)
  } finally {
    setSaving(row.id, false)
  }
}

// Fotos escolhidas no RASCUNHO (antes de existir a linha): ficam no draft e
// sobem logo depois do POST que cria a devolução.
function pickDraftFotos(d: DevolutionDraft, ev: Event) {
  const input = ev.target as HTMLInputElement
  const files = Array.from(input.files || [])
  input.value = ''
  if (files.length) d.fotos = [...(d.fotos || []), ...files]
}

// ---- anexos (fotos/vídeos) da devolução — modal por linha
const anexosRow = ref<DevolutionRow | null>(null)
const anexoUploading = ref(false)
function openAnexos(row: DevolutionRow) {
  anexosRow.value = row
}
function closeAnexos() {
  anexosRow.value = null
}
function anexoUrl(id: string) {
  return `/api/devolutions/anexos/${id}`
}
function isVideo(a: DevolucaoAnexo) {
  return a.content_type.startsWith('video/')
}
function replaceRow(row: DevolutionRow, updated: DevolutionRow) {
  const merged = { ...updated, cliente: updated.cliente ?? row.cliente }
  const idx = items.value.findIndex((i) => i.id === row.id)
  if (idx >= 0) items.value[idx] = merged
  if (anexosRow.value?.id === row.id) anexosRow.value = merged
}
async function uploadAnexo(ev: Event) {
  const input = ev.target as HTMLInputElement
  const files = Array.from(input.files || [])
  input.value = ''
  const row = anexosRow.value
  if (!row || !files.length) return
  anexoUploading.value = true
  error.value = null
  try {
    for (const f of files) {
      const fd = new FormData()
      fd.append('file', f)
      const updated = await api<DevolutionRow>(`/api/devolutions/${encodeURIComponent(row.id)}/anexos`, { method: 'POST', body: fd })
      replaceRow(row, updated)
    }
  } catch (e: any) {
    error.value = apiError(e)
  } finally {
    anexoUploading.value = false
  }
}
async function removeAnexo(a: DevolucaoAnexo) {
  const row = anexosRow.value
  if (!row) return
  try {
    await api(`/api/devolutions/anexos/${a.id}`, { method: 'DELETE' })
    const rest = (row.anexos || []).filter((x) => x.id !== a.id)
    replaceRow(row, { ...row, anexos: rest })
  } catch (e: any) {
    error.value = apiError(e)
  }
}

const deleting = ref<Set<string>>(new Set())
function isDeleting(id: string): boolean {
  return deleting.value.has(id)
}
async function removeRow(row: DevolutionRow) {
  if (!canDelete.value || isDeleting(row.id)) return
  const label = row.sku || row.pedido_bling || 'esta devolução'
  // Estoque já devolvido ao Bling: com o movimento registrado, o back dá BAIXA
  // da mesma quantidade antes de excluir (03/09, Eduardo). Lançamento antigo
  // sem registro não tem como ser estornado daqui.
  const estornavel = !!row.estoque_mov_sku && !row.estoque_mov_revertido_at
  const stockNote = estornavel
    ? ` O estoque devolvido no Bling (${row.estoque_mov_qty ?? 1} un. de ${row.estoque_mov_sku}) será ESTORNADO automaticamente (saída).`
    : (row.data_devolvido_estoque
      ? ' O estoque já devolvido no Bling NÃO será estornado (lançamento antigo, sem registro do movimento) — ajuste-o direto no Bling.'
      : '')
  if (!confirm(`Remover o lançamento de "${label}"? Esta ação não pode ser desfeita.${stockNote}`)) return
  const next = new Set(deleting.value)
  next.add(row.id)
  deleting.value = next
  error.value = null
  try {
    const res = await api<{ ok: boolean; estoque_estornado: boolean; mensagem: string | null }>(
      `/api/devolutions/${encodeURIComponent(row.id)}`,
      { method: 'DELETE' },
    )
    items.value = items.value.filter((i) => i.id !== row.id)
    total.value = Math.max(0, total.value - 1)
    if (res?.estoque_estornado) {
      pushToast({ kind: 'success', title: 'Lançamento excluído', lines: [res.mensagem || 'Estoque estornado no Bling.'] })
    }
  } catch (e: any) {
    error.value = apiError(e)
  } finally {
    const done = new Set(deleting.value)
    done.delete(row.id)
    deleting.value = done
  }
}

const backfilling = ref(false)
async function backfillAddresses() {
  if (!canEdit.value || backfilling.value) return
  backfilling.value = true
  try {
    const res = await api<{ processed: number; updated: number; failed: number; message: string }>(
      '/api/devolutions/backfill-addresses',
      { method: 'POST' },
    )
    pushToast({
      kind: res.updated > 0 ? 'success' : 'warning',
      title: 'Backfill de endereços',
      lines: [res.message, ...(res.failed > 0 ? [`${res.failed} falhou`] : [])],
    })
  } catch (e: any) {
    pushToast({ kind: 'error', title: 'Erro no backfill', lines: [apiError(e)] })
  } finally {
    backfilling.value = false
  }
}

</script>

<template>
  <div class="space-y-5">
    <PageHeader
      title="Devoluções"
      :description="tab === 'acompanhamento'
        ? 'Pedidos aguardando devolução no Bling — rastreio e última localização do pacote.'
        : 'Controle manual de devoluções por pedido.'"
    >
      <template #actions>
        <template v-if="tab === 'acompanhamento'">
          <Button
            v-if="isAdmin"
            size="sm"
            variant="outline"
            title="Enviar no Threema a lista de pedidos aguardando devolução"
            @click="informarOpen = true"
          >
            <Megaphone class="size-4 mr-1.5" />
            informar
          </Button>
          <Button size="sm" variant="outline" :disabled="acompLoading" @click="loadAcompanhamento">
            <RotateCcw class="size-4 mr-1.5" :class="{ 'animate-spin': acompLoading }" />
            atualizar
          </Button>
          <Button
            size="sm"
            variant="outline"
            :disabled="!canEdit || backfilling"
            title="Busca no Bling o nome/cidade dos pedidos em devolução que estão sem esses dados"
            @click="backfillAcompanhamento"
          >
            <RotateCcw class="size-4 mr-1.5" :class="{ 'animate-spin': backfilling }" />
            atualizar clientes
          </Button>
        </template>
        <template v-else>
          <Button size="sm" variant="outline" :disabled="loading" @click="load">
            <RotateCcw class="size-4 mr-1.5" :class="{ 'animate-spin': loading }" />
            atualizar
          </Button>
          <Button size="sm" variant="outline" :disabled="!canEdit || backfilling" @click="backfillAddresses">
            <RotateCcw class="size-4 mr-1.5" :class="{ 'animate-spin': backfilling }" />
            atualizar endereços
          </Button>
          <Button size="sm" :disabled="!canEdit" @click="openAdd">
            <Plus class="size-4 mr-1.5" />
            adicionar pedido
          </Button>
          <Button size="sm" variant="outline" :disabled="!canEdit" @click="openCorrecao">
            <PackagePlus class="size-4 mr-1.5" />
            correção de estoque
          </Button>
        </template>
      </template>
    </PageHeader>

    <!-- Abas (mesmo estilo do Controle de Estoque) -->
    <div class="flex gap-1 rounded-md bg-muted/40 p-1 w-fit flex-wrap">
      <button
        class="px-3 py-1.5 rounded text-sm transition-colors inline-flex items-center gap-1.5"
        :class="tab === 'acompanhamento' ? 'bg-background shadow-sm font-medium' : 'hover:bg-background/60 text-muted-foreground'"
        @click="tab = 'acompanhamento'"
      >
        <PackageSearch class="size-4" />
        Acompanhamento
        <span class="rounded bg-muted px-1.5 text-[11px] tabular-nums">{{ acompTotalPedidos }}</span>
      </button>
      <button
        class="px-3 py-1.5 rounded text-sm transition-colors inline-flex items-center gap-1.5"
        :class="tab === 'lancamentos' ? 'bg-background shadow-sm font-medium' : 'hover:bg-background/60 text-muted-foreground'"
        @click="tab = 'lancamentos'"
      >
        <ClipboardList class="size-4" />
        Lançamentos
      </button>
    </div>

    <!-- ══ Aba Acompanhamento — pedidos em Aguardando Devolução no Bling ══ -->
    <template v-if="tab === 'acompanhamento'">
      <div v-if="acompError" class="flex items-center gap-2 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-400">
        <AlertCircle class="size-4" />
        {{ acompError }}
      </div>

      <div class="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatCard label="Pedidos em devolução" :value="acompTotalPedidos" :icon="PackageSearch" />
        <StatCard label="Sem rastreio" :value="acompSemRastreio" :icon="AlertCircle" tone="warning" />
        <StatCard label="Sem localização" :value="acompSemLocalizacao" :icon="Clock" tone="warning" />
        <StatCard label="Parados 15+ dias" :value="acompParados15" :icon="Clock" tone="danger" />
      </div>

      <div class="flex flex-wrap items-center gap-2">
        <div class="relative">
          <Search class="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
          <input
            v-model="acompSearch"
            class="h-9 w-72 rounded-md border bg-background pl-8 pr-3 text-sm"
            placeholder="buscar pedido, cliente, sku, rastreio…"
          />
        </div>
        <select v-model="acompPlataformaFilter" class="h-9 rounded-md border bg-background px-2 text-sm" title="Filtrar por plataforma">
          <option value="all">todas plataformas</option>
          <option v-for="p in acompPlataformas" :key="p" :value="p">{{ p }}</option>
        </select>
        <select v-model="acompLojaFilter" class="h-9 rounded-md border bg-background px-2 text-sm" title="Filtrar por loja">
          <option value="all">todas lojas</option>
          <option v-for="l in acompLojas" :key="l" :value="l">{{ l }}</option>
        </select>
        <select v-model="acompParadoFilter" class="h-9 rounded-md border bg-background px-2 text-sm" title="Só pedidos que estão em devolução há N dias ou mais">
          <option value="all">qualquer tempo</option>
          <option value="7">parados 7+ dias</option>
          <option value="15">parados 15+ dias</option>
          <option value="30">parados 30+ dias</option>
        </select>
        <span class="ml-auto text-xs text-muted-foreground">
          {{ acompFiltered.length }} de {{ acompRows.length }} itens · rastreio e localização salvam ao sair do campo
        </span>
      </div>

      <div class="overflow-auto rounded border max-h-[75vh] focus:outline-none" tabindex="0">
        <table class="min-w-[2000px] text-xs border-collapse">
          <thead class="sticky top-0 z-20 bg-background">
            <tr>
              <th class="px-2 py-1 text-left text-[11px] font-semibold border-b" colspan="12">Pedido aguardando devolução (Bling)</th>
              <th class="px-2 py-1 text-center text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-amber-50 dark:bg-amber-900/20" colspan="3" title="Preenchido sozinho: código e status do PACOTE QUE VOLTA (devolução na Shopee/TikTok/ML, atualizado a cada 30 min) — senão o rastreio da entrega original (Logística). O que você digitar aqui vale mais que o automático">Rastreio</th>
              <th class="px-2 py-1 text-center text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-emerald-50 dark:bg-emerald-900/20" colspan="1">Devolução</th>
            </tr>
            <tr class="border-b">
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[115px]">Data pedido</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px]">Pedido Bling</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[150px]">Pedido Marketplace</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[100px]">Plataforma</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[140px]">Loja</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[180px]">Cliente</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[140px]">Cidade/UF</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[130px]">SKU</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[210px]">Produto</th>
              <th class="px-2 py-1 text-center font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[45px]">Qtd</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[125px]" title="Dia em que o pedido entrou em Aguardando Devolução">Em devolução desde</th>
              <th class="px-2 py-1 text-center font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[60px]" title="Há quantos dias o pedido está aguardando devolução">Dias</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[160px] bg-amber-50 dark:bg-amber-900/20 border-l-[3px] border-gray-400 dark:border-gray-600">Rastreio</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[220px] bg-amber-50 dark:bg-amber-900/20">Última localização</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[135px] bg-amber-50 dark:bg-amber-900/20" title="Preenchida sozinha quando a localização muda">Data últ. movimentação</th>
              <th class="px-2 py-1 text-center font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[95px] bg-emerald-50 dark:bg-emerald-900/20 border-l-[3px] border-gray-400 dark:border-gray-600">Lançada</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="acompLoading && !acompRows.length">
              <td colspan="16" class="py-8 text-center text-muted-foreground">
                <Loader2 class="size-4 inline animate-spin mr-1.5" />
                carregando…
              </td>
            </tr>
            <tr v-else-if="!acompFiltered.length">
              <td colspan="16" class="py-8 text-center text-muted-foreground">nenhum pedido aguardando devolução</td>
            </tr>
            <tr
              v-for="row in acompFiltered"
              :key="`${row.pedido_bling}-${row.sku}`"
              class="border-t hover:brightness-95 dark:hover:brightness-110"
            >
              <td class="px-2 py-1 whitespace-nowrap text-muted-foreground">{{ fmtDateTime(row.data) }}</td>
              <td class="px-2 py-1 font-mono whitespace-nowrap">{{ row.pedido_bling || '—' }}</td>
              <td class="px-2 py-1 font-mono text-muted-foreground whitespace-nowrap">{{ row.pedido_marketplace || '—' }}</td>
              <td class="px-2 py-1 whitespace-nowrap">{{ row.plataforma || '—' }}</td>
              <td class="px-2 py-1 whitespace-nowrap">{{ row.loja || '—' }}</td>
              <td class="px-2 py-1">{{ row.cliente || '—' }}</td>
              <td class="px-2 py-1 whitespace-nowrap text-muted-foreground">{{ row.cidade ? `${row.cidade}${row.uf ? ' — ' + row.uf : ''}` : (row.uf || '—') }}</td>
              <td class="px-2 py-1 font-mono text-xs">{{ row.sku || '—' }}</td>
              <td class="px-2 py-1 text-muted-foreground">{{ row.produto || '—' }}</td>
              <td class="px-2 py-1 text-center tabular-nums">{{ row.quantidade ?? '—' }}</td>
              <td class="px-1 py-0.5 whitespace-nowrap">
                <!-- "Em devolução desde": automático (devolução aberta no marketplace /
                     sinal da Logística / entrada no Bling); editável na mão quando o
                     automático não bate (03/09, caso 287144). Vazio = volta ao automático. -->
                <div v-if="canEdit" class="flex items-center gap-1">
                  <span
                    v-if="row.aguardando_devolucao_data_estimada"
                    class="text-amber-600 dark:text-amber-400 text-xs font-semibold"
                    title="Data ESTIMADA (pelo último movimento do pacote no marketplace) — o Bling não informa o histórico. Se souber a data certa, corrija ao lado."
                  >≈</span>
                  <input
                    type="date"
                    :value="row.aguardando_devolucao_data || ''"
                    :disabled="isSavingRastreio(row.pedido_bling, 'em_devolucao_desde')"
                    class="w-[128px] text-xs border rounded px-1.5 py-1 bg-background text-foreground"
                    :class="row.aguardando_devolucao_data_estimada ? 'border-amber-300 dark:border-amber-700' : ''"
                    title="Dia em que o pedido entrou em devolução (automático). Se estiver errado, escolha a data certa; apagar volta ao automático."
                    @change="(e) => saveRastreio(row, 'em_devolucao_desde', (e.target as HTMLInputElement).value)"
                  />
                </div>
                <span v-else class="text-muted-foreground">{{ row.aguardando_devolucao_data_estimada ? '≈ ' : '' }}{{ fmtDateOnly(row.aguardando_devolucao_data) }}</span>
              </td>
              <td class="px-2 py-1 text-center">
                <span
                  v-if="row.dias_em_devolucao != null"
                  class="inline-flex rounded px-1.5 py-0.5 text-[11px] font-medium tabular-nums"
                  :class="diasBadgeClass(row.dias_em_devolucao)"
                >{{ row.dias_em_devolucao }}d</span>
                <span v-else class="text-muted-foreground">—</span>
              </td>
              <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10 border-l-[3px] border-gray-400 dark:border-gray-600">
                <input
                  :value="row.rastreio || ''"
                  :disabled="!canEdit || isSavingRastreio(row.pedido_bling, 'rastreio')"
                  :class="sheetInputClass"
                  placeholder="código de rastreio"
                  @keydown.enter="(e) => (e.target as HTMLInputElement).blur()"
                  @blur="(e) => saveRastreio(row, 'rastreio', (e.target as HTMLInputElement).value)"
                />
              </td>
              <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
                <input
                  :value="row.localizacao || ''"
                  :title="row.entrega_localizacao ? `Status da devolução (automático). Entrega original: ${row.entrega_localizacao}` : undefined"
                  :disabled="!canEdit || isSavingRastreio(row.pedido_bling, 'localizacao')"
                  :class="sheetInputClass"
                  placeholder="onde o pacote está"
                  @keydown.enter="(e) => (e.target as HTMLInputElement).blur()"
                  @blur="(e) => saveRastreio(row, 'localizacao', (e.target as HTMLInputElement).value)"
                />
              </td>
              <td class="px-2 py-1 whitespace-nowrap text-muted-foreground bg-amber-50/40 dark:bg-amber-900/10" title="Preenchida sozinha quando a localização muda">
                {{ fmtDateTime(row.localizacao_data) }}
              </td>
              <td class="px-2 py-1 text-center bg-emerald-50/40 dark:bg-emerald-900/10 border-l-[3px] border-gray-400 dark:border-gray-600">
                <span
                  v-if="row.lancada"
                  class="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300"
                  title="Este pedido já tem devolução lançada na aba Lançamentos"
                >
                  <CheckCircle2 class="size-3" />
                  lançada
                </span>
                <Button
                  v-else
                  size="sm"
                  variant="outline"
                  class="h-6 px-2 text-[11px]"
                  :disabled="!canEdit"
                  title="Abrir a aba Lançamentos com este pedido já buscado"
                  @click="goLancar(row)"
                >
                  lançar
                </Button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <!-- ══ Aba Lançamentos — conteúdo original da página, intacto ══ -->
    <div v-if="tab === 'lancamentos' && error" class="flex items-center gap-2 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-400">
      <AlertCircle class="size-4" />
      {{ error }}
    </div>

    <div v-show="tab === 'lancamentos'" class="grid grid-cols-2 lg:grid-cols-3 gap-3">
      <StatCard label="Total devoluções" :value="total" :icon="Undo2" />
      <StatCard label="Enviada para Reembolso" :value="totalReembolsadas" :icon="Clock" tone="warning" />
      <StatCard label="Custo manutenção (pág.)" :value="brl(totalCustoManutencao)" tone="danger" />
    </div>

    <div v-if="tab === 'lancamentos' && addOpen" class="rounded-md border bg-background">
      <div class="flex flex-wrap items-end gap-3 border-b px-3 py-3">
        <label class="space-y-1">
          <span class="text-[11px] font-medium text-muted-foreground">Buscar pedido</span>
          <div class="relative">
            <Search class="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
            <input
              v-model="lookupPedido"
              class="h-9 w-80 rounded-md border bg-background pl-8 pr-3 text-sm"
              placeholder="Pedido, nome do cliente ou CEP"
              @keydown.enter.prevent="lookupOrder"
            />
          </div>
        </label>
        <Button size="sm" :disabled="lookupLoading || !lookupPedido.trim()" @click="lookupOrder">
          <Loader2 v-if="lookupLoading" class="size-4 mr-1.5 animate-spin" />
          <Search v-else class="size-4 mr-1.5" />
          buscar
        </Button>
        <Button size="sm" variant="ghost" @click="closeAdd">
          <X class="size-4 mr-1.5" />
          fechar
        </Button>
        <span v-if="lookupError" class="text-sm text-red-400">{{ lookupError }}</span>
      </div>

      <div v-if="expandedLookupResults.length > 0" class="overflow-auto border-b">
        <table class="w-full text-xs border-collapse">
          <thead class="bg-background">
            <tr>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px]">Data</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[120px]">Pedido Bling</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[155px]">Pedido Marketplace</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[155px]">Conta</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[180px]">Cliente</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[90px]">CEP</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[120px]">SKU</th>
              <th class="px-2 py-1 text-center font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[50px]">Qtd</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[200px]">Produto</th>
              <th v-if="isAdmin" class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px]">Custo</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="row in expandedLookupResults"
              :key="`${row.pedido_bling}-${row.sku}-${row.conta}`"
              class="border-t"
              :class="isAlreadyAdded(row) ? 'opacity-50' : ''"
            >
              <td class="px-2 py-1 whitespace-nowrap text-muted-foreground">{{ fmtDateTime(row.data) }}</td>
              <td class="px-2 py-1 font-mono">{{ row.pedido_bling || '—' }}</td>
              <td class="px-2 py-1 font-mono text-muted-foreground">{{ row.pedido_marketplace || '—' }}</td>
              <td class="px-2 py-1">{{ row.conta }}</td>
              <td class="px-2 py-1">{{ row.nome_destinatario || '—' }}</td>
              <td class="px-2 py-1 font-mono text-muted-foreground">{{ row.cep_destino || '—' }}</td>
              <td class="px-2 py-1 font-mono text-xs">{{ row.sku || '—' }}</td>
              <td class="px-2 py-1 text-center tabular-nums">{{ row.quantidade ?? '—' }}</td>
              <td class="px-2 py-1 text-muted-foreground">{{ row.produtos || '—' }}</td>
              <td v-if="isAdmin" class="px-2 py-1 text-right tabular-nums">{{ brl(row.custo_produto) }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Um único "usar" para o pedido inteiro — traz todos os produtos como rascunho. -->
      <div
        v-if="expandedLookupResults.length > 0 && !drafts.length"
        class="flex flex-wrap items-center gap-3 border-b px-3 py-2"
      >
        <Button size="sm" :disabled="!canEdit || selectableLookupCount === 0" @click="selectAllProducts">
          <Plus class="size-4 mr-1.5" />
          usar {{ selectableLookupCount }} produto{{ selectableLookupCount === 1 ? '' : 's' }}
        </Button>
        <span v-if="selectableLookupCount === 0" class="text-xs text-muted-foreground">
          todos os produtos deste pedido já foram adicionados
        </span>
      </div>

      <div v-if="drafts.length" class="overflow-auto">
        <table class="min-w-[1600px] w-full text-xs border-collapse">
          <thead class="bg-background">
            <tr>
              <th class="px-2 py-1 text-left text-[11px] font-semibold border-b" colspan="6">Identificação</th>
              <th class="px-2 py-1 text-center text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-amber-50 dark:bg-amber-900/20" :colspan="isAdmin ? 10 : 9">Devolução</th>
            </tr>
            <tr>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px]">Data</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[120px]">Pedido Bling</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[155px]">Pedido Marketplace</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[150px]">Conta</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[130px]">SKU</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[200px]">Produtos</th>
              <th v-if="isAdmin" class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px] bg-amber-50 dark:bg-amber-900/20 border-l-[3px] border-gray-400 dark:border-gray-600">Custo produto</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[140px] bg-amber-50 dark:bg-amber-900/20">Condição</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[170px] bg-amber-50 dark:bg-amber-900/20" title="Link das fotos/vídeo feitos na expedição do pedido — prova pra contestar pacote vazio/item errado. Obrigatório pra mala e eletro quando o motivo abre chamado">Link envio</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[180px] bg-amber-50 dark:bg-amber-900/20">Link abertura</th>
              <th class="px-2 py-1 text-center font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[60px] bg-amber-50 dark:bg-amber-900/20" title="Fotos da devolução — vão como evidência do chamado automático no Mercado Livre">Foto</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[170px] bg-amber-50 dark:bg-amber-900/20" title="Link do vídeo — entra no texto do chamado (a API do ML não aceita vídeo anexo)">Link vídeo</th>
              <!-- Reembolso saiu da tela (03/09): liga sozinho por custo+técnico
                   / Extraviado / Manutenção; segue no filtro, no card e no export. -->
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[180px] bg-amber-50 dark:bg-amber-900/20">Motivo</th>
              <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[120px] bg-amber-50 dark:bg-amber-900/20">Custo manutenção</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[120px] bg-amber-50 dark:bg-amber-900/20">Técnico</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[120px] bg-amber-50 dark:bg-amber-900/20">Devolver estoque</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(d, i) in drafts" :key="i" class="border-t">
              <td class="px-2 py-1 whitespace-nowrap text-muted-foreground">{{ fmtDateTime(d.data) }}</td>
              <td class="px-2 py-1 font-mono">{{ d.pedido_bling || '—' }}</td>
              <td class="px-2 py-1 font-mono text-muted-foreground">{{ d.pedido_marketplace || '—' }}</td>
              <td class="px-2 py-1">{{ d.conta }}</td>
              <td class="px-2 py-1 font-mono">{{ d.sku || '—' }}</td>
              <td class="px-2 py-1 text-muted-foreground">{{ d.produtos || '—' }}</td>
              <td v-if="isAdmin" class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10 border-l-[3px] border-gray-400 dark:border-gray-600">
                <input v-model.number="d.custo_produto" type="text" inputmode="decimal" :class="sheetMoneyInputClass" />
              </td>
              <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
                <select v-model="d.condicao_produto" :class="d.condicao_produto ? sheetSelectClass : sheetSelectRequiredClass" @change="(e) => { if ((e.target as HTMLSelectElement).value === 'Extraviado' || (e.target as HTMLSelectElement).value === 'Manutenção') d.reembolso = true }">
                  <option value="">—</option>
                  <option v-for="c in CONDICOES_PRODUTO" :key="c" :value="c">{{ c }}</option>
                </select>
              </td>
              <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
                <input
                  v-model="d.link_envio"
                  :class="linkEnvioRequired(d.sku, d.motivo_devolucao) && !d.link_envio ? sheetInputRequiredClass : sheetInputClass"
                  :placeholder="linkEnvioRequired(d.sku, d.motivo_devolucao) ? 'obrigatório (mala/eletro)' : 'link do envio'"
                />
              </td>
              <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
                <input
                  v-model="d.link_abertura"
                  :class="linkRequired(d.condicao_produto) && !d.link_abertura ? sheetInputRequiredClass : sheetInputClass"
                  :placeholder="linkRequired(d.condicao_produto) ? 'obrigatório' : ''"
                />
              </td>
              <td class="px-1 py-0.5 text-center bg-amber-50/40 dark:bg-amber-900/10">
                <label
                  class="inline-flex cursor-pointer items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] hover:bg-muted"
                  :class="d.fotos?.length ? 'text-emerald-700 dark:text-emerald-300' : 'text-muted-foreground'"
                  :title="d.fotos?.length ? d.fotos.map((f) => f.name).join(', ') : 'anexar foto (JPG/PNG/PDF) — sobe junto com o lançamento'"
                >
                  <Camera class="size-3.5" />
                  {{ d.fotos?.length || '' }}
                  <input type="file" multiple accept="image/jpeg,image/png,application/pdf" class="hidden" @change="(e) => pickDraftFotos(d, e)" />
                </label>
              </td>
              <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
                <input v-model="d.video_url" :class="sheetInputClass" placeholder="link do vídeo" />
              </td>
              <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
                <select v-model="d.motivo_devolucao" :class="sheetSelectClass">
                  <option value="">—</option>
                  <option v-for="m in MOTIVOS_DEVOLUCAO" :key="m" :value="m">{{ m }}</option>
                </select>
              </td>
              <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
                <input v-model.number="d.custo_manutencao" type="text" inputmode="decimal" :class="sheetMoneyInputClass" />
              </td>
              <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
                <select v-model="d.tecnico" :class="sheetSelectClass">
                  <option value="">—</option>
                  <option v-for="t in TECNICOS" :key="t" :value="t">{{ t }}</option>
                </select>
              </td>
              <td class="px-2 py-1 text-center bg-amber-50/40 dark:bg-amber-900/10">
                <button
                  v-if="canSeeStockToggle(d.condicao_produto)"
                  type="button"
                  role="switch"
                  :aria-checked="d.devolver_estoque"
                  :class="[
                    'relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors',
                    d.devolver_estoque ? 'bg-primary' : 'bg-gray-300 dark:bg-gray-600',
                  ]"
                  @click="d.devolver_estoque = !d.devolver_estoque"
                >
                  <span
                    :class="[
                      'inline-block size-4 transform rounded-full bg-white shadow transition-transform',
                      d.devolver_estoque ? 'translate-x-4' : 'translate-x-0.5',
                    ]"
                  />
                </button>
                <span v-else class="text-muted-foreground">—</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Um único "adicionar" cria todos os produtos do rascunho de uma vez. -->
      <div v-if="drafts.length" class="flex flex-wrap items-center justify-end gap-3 border-t px-3 py-2">
        <span v-if="lookupError" class="mr-auto text-sm text-red-400">{{ lookupError }}</span>
        <Button size="sm" :disabled="creating || !canEdit" @click="createAllDevolutions">
          <Loader2 v-if="creating" class="size-4 mr-1.5 animate-spin" />
          <Plus v-else class="size-4 mr-1.5" />
          adicionar {{ drafts.length }} produto{{ drafts.length === 1 ? '' : 's' }}
        </Button>
      </div>
    </div>

    <div v-show="tab === 'lancamentos'" class="flex flex-wrap items-center gap-2">
      <div class="relative">
        <Search class="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
        <input
          v-model="search"
          class="h-9 w-72 rounded-md border bg-background pl-8 pr-3 text-sm"
          placeholder="buscar pedido, conta, sku, motivo…"
        />
      </div>
      <select v-model="reembolsoFilter" class="h-9 rounded-md border bg-background px-2 text-sm">
        <option value="all">todas</option>
        <option value="true">enviada para reembolso</option>
        <option value="false">sem reembolso</option>
      </select>
      <select v-model="tagFilter" class="h-9 rounded-md border bg-background px-2 text-sm">
        <option value="all">todas tags</option>
        <option v-for="opt in TAG_OPTIONS" :key="opt.slug" :value="opt.slug">{{ opt.label }}</option>
      </select>
      <select v-model="condicaoFilter" class="h-9 rounded-md border bg-background px-2 text-sm">
        <option value="all">todas condições</option>
        <option v-for="c in CONDICOES_PRODUTO" :key="c" :value="c">{{ c }}</option>
      </select>
      <div class="flex items-center gap-1.5 h-9 rounded-md border bg-background px-2" title="Período da data de devolução">
        <span class="text-xs text-muted-foreground">de</span>
        <input
          v-model="dataInicioFilter"
          type="date"
          :max="dataFimFilter || undefined"
          title="Data inicial"
          class="bg-transparent text-sm focus:outline-none"
        />
        <span class="text-xs text-muted-foreground">até</span>
        <input
          v-model="dataFimFilter"
          type="date"
          :min="dataInicioFilter || undefined"
          title="Data final"
          class="bg-transparent text-sm focus:outline-none"
        />
        <button
          v-if="dataInicioFilter || dataFimFilter"
          type="button"
          title="Limpar período"
          class="text-muted-foreground hover:text-foreground"
          @click="dataInicioFilter = ''; dataFimFilter = ''"
        >
          <X class="size-3.5" />
        </button>
      </div>
      <label class="flex items-center gap-1.5 h-9 rounded-md border bg-background px-2.5 text-sm cursor-pointer select-none" title="Mostrar só pedidos que já passaram em manutenção">
        <input v-model="manutencaoFilter" type="checkbox" class="size-4 rounded border accent-primary" />
        <span class="whitespace-nowrap">manutenções realizadas</span>
      </label>
      <select
        v-if="isAdmin"
        v-model="prazoDiasFilter"
        class="h-9 rounded-md border bg-background px-2 text-sm"
        title="Prazo de manutenção vencendo em até N dias (inclui vencidos)"
      >
        <option value="all">todos prazos</option>
        <option value="vencidas">vencidas</option>
        <option value="7">prazo em 7 dias</option>
        <option value="15">prazo em 15 dias</option>
        <option value="30">prazo em 30 dias</option>
      </select>
      <Button size="sm" variant="outline" :disabled="exporting" @click="exportXlsx">
        <Download class="size-4 mr-1.5" :class="{ 'animate-pulse': exporting }" />
        exportar xlsx
      </Button>
      <span class="ml-auto text-xs text-muted-foreground">
        {{ rangeStart }}–{{ rangeEnd }} de {{ total }} · enviada p/ reembolso {{ totalReembolsadas }} · manutenção {{ brl(totalCustoManutencao) }}
      </span>
    </div>

    <div v-show="tab === 'lancamentos'" class="overflow-auto rounded border max-h-[75vh] focus:outline-none" tabindex="0">
      <table class="min-w-[2360px] text-xs border-collapse">
        <thead class="sticky top-0 z-20 bg-background">
          <tr>
            <th class="px-2 py-1 text-left text-[11px] font-semibold border-b" colspan="9">Identificação</th>
            <th class="px-2 py-1 text-center text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-amber-50 dark:bg-amber-900/20" :colspan="isAdmin ? 13 : 12">Devolução</th>
            <th class="px-2 py-1 text-left text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-emerald-50 dark:bg-emerald-900/20" colspan="1">Observação</th>
            <th v-if="isAdmin" class="px-2 py-1 text-left text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-slate-50 dark:bg-slate-800/40" colspan="1">Atualização</th>
          </tr>
          <tr class="border-b">
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[115px]">Data</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[120px]">Data Devolução</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[120px]">Pedido Bling</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[155px]">Pedido Marketplace</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[150px]">Conta</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[170px]">Cliente</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[130px]">SKU</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[90px]">Tags</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[200px]">Produtos</th>
            <th v-if="isAdmin" class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px] bg-amber-50 dark:bg-amber-900/20 border-l-[3px] border-gray-400 dark:border-gray-600">Custo produto</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[140px] bg-amber-50 dark:bg-amber-900/20">Condição</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[170px] bg-amber-50 dark:bg-amber-900/20" title="Link das fotos/vídeo feitos na expedição do pedido — prova pra contestar pacote vazio/item errado. Obrigatório pra mala e eletro quando o motivo abre chamado">Link envio</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[180px] bg-amber-50 dark:bg-amber-900/20">Link abertura</th>
            <th class="px-2 py-1 text-center font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[60px] bg-amber-50 dark:bg-amber-900/20" title="Fotos e vídeos da devolução — as fotos vão como evidência do chamado automático na plataforma (ML, TikTok, Shopee)">Foto</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[170px] bg-amber-50 dark:bg-amber-900/20" title="Link do vídeo — entra no texto do chamado (a API do ML não aceita vídeo anexo)">Link vídeo</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[180px] bg-amber-50 dark:bg-amber-900/20">Motivo</th>
            <th class="px-2 py-1 text-center font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[115px] bg-amber-50 dark:bg-amber-900/20" title="Chamado mais recente deste pedido na aba Chamados — clique pra abrir">Chamado</th>
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[120px] bg-amber-50 dark:bg-amber-900/20">Custo manutenção</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[120px] bg-amber-50 dark:bg-amber-900/20">Técnico</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[120px] bg-amber-50 dark:bg-amber-900/20">Devolver estoque</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[140px] bg-amber-50 dark:bg-amber-900/20">Data devolvido estoque</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px] bg-amber-50 dark:bg-amber-900/20">Prazo</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[240px] bg-emerald-50 dark:bg-emerald-900/20 border-l-[3px] border-gray-400 dark:border-gray-600">Observação</th>
            <th v-if="isAdmin" class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[120px] bg-slate-50 dark:bg-slate-800/40 border-l-[3px] border-gray-400 dark:border-gray-600">Atualizado</th>
            <th v-if="canDelete" class="px-2 py-1 text-center font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[50px]"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading && !items.length">
            <td :colspan="(isAdmin ? 25 : 23) + (canDelete ? 1 : 0)" class="py-8 text-center text-muted-foreground">
              <Loader2 class="size-4 inline animate-spin mr-1.5" />
              carregando…
            </td>
          </tr>
          <tr v-else-if="!items.length">
            <td :colspan="(isAdmin ? 25 : 23) + (canDelete ? 1 : 0)" class="py-8 text-center text-muted-foreground">sem registros</td>
          </tr>
          <tr v-for="row in items" :key="row.id" class="border-t hover:brightness-95 dark:hover:brightness-110">
            <td class="px-2 py-1 whitespace-nowrap text-muted-foreground">{{ fmtDateTime(row.data) }}</td>
            <td class="px-2 py-1 whitespace-nowrap text-muted-foreground">{{ fmtDateTime(row.created_at) }}</td>
            <td class="px-2 py-1 font-mono whitespace-nowrap">{{ row.pedido_bling || '—' }}</td>
            <td class="px-2 py-1 font-mono text-muted-foreground whitespace-nowrap">{{ row.pedido_marketplace || '—' }}</td>
            <td class="px-2 py-1 whitespace-nowrap">{{ row.conta }}</td>
            <td class="px-2 py-1 whitespace-nowrap">{{ row.cliente || '—' }}</td>
            <td class="px-2 py-1 font-mono text-xs">{{ row.sku || '—' }}</td>
            <td class="px-2 py-1 text-xs text-muted-foreground whitespace-nowrap">{{ tagLabel(row.tag) }}</td>
            <td class="px-2 py-1 text-muted-foreground">{{ row.produtos || '—' }}</td>
            <td v-if="isAdmin" class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10 border-l-[3px] border-gray-400 dark:border-gray-600">
              <input
                :value="row.custo_produto ?? ''"
                :disabled="!canEdit"
                type="text"
                inputmode="decimal"
                :class="sheetMoneyInputClass"
                @input="(e) => setRowNumber(row, 'custo_produto', (e.target as HTMLInputElement).value)"
                @blur="saveRow(row)"
              />
            </td>
            <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
              <select
                :value="row.condicao_produto || ''"
                :disabled="!canEdit"
                :class="sheetSelectClass"
                @change="(e) => changeRowCondicao(row, (e.target as HTMLSelectElement).value)"
              >
                <option value="">—</option>
                <option v-for="c in CONDICOES_PRODUTO" :key="c" :value="c">{{ c }}</option>
                <option
                  v-if="row.condicao_produto && !(CONDICOES_PRODUTO as readonly string[]).includes(row.condicao_produto)"
                  :value="row.condicao_produto"
                >{{ row.condicao_produto }}</option>
              </select>
            </td>
            <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
              <div class="flex items-center gap-1">
                <input
                  :value="row.link_envio || ''"
                  :disabled="!canEdit"
                  :class="linkEnvioRequired(row.sku, row.motivo_devolucao) && !row.link_envio ? sheetInputRequiredClass : sheetInputClass"
                  :placeholder="linkEnvioRequired(row.sku, row.motivo_devolucao) ? 'obrigatório (mala/eletro)' : 'link do envio'"
                  @input="(e) => setRowText(row, 'link_envio', (e.target as HTMLInputElement).value)"
                  @blur="saveRow(row)"
                />
                <a
                  v-if="row.link_envio"
                  :href="normalizeUrl(row.link_envio)"
                  target="_blank"
                  rel="noopener noreferrer"
                  title="Abrir em nova guia"
                  class="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
                >
                  <ExternalLink class="size-3.5" />
                </a>
              </div>
            </td>
            <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
              <div class="flex items-center gap-1">
                <input
                  :value="row.link_abertura || ''"
                  :disabled="!canEdit"
                  :class="linkRequired(row.condicao_produto) && !row.link_abertura ? sheetInputRequiredClass : sheetInputClass"
                  :placeholder="linkRequired(row.condicao_produto) ? 'obrigatório' : ''"
                  @input="(e) => setRowText(row, 'link_abertura', (e.target as HTMLInputElement).value)"
                  @blur="saveRow(row)"
                />
                <a
                  v-if="row.link_abertura"
                  :href="normalizeUrl(row.link_abertura)"
                  target="_blank"
                  rel="noopener noreferrer"
                  title="Abrir em nova guia"
                  class="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
                >
                  <ExternalLink class="size-3.5" />
                </a>
              </div>
            </td>
            <td class="px-1 py-0.5 text-center bg-amber-50/40 dark:bg-amber-900/10">
              <button
                type="button"
                class="inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] hover:bg-muted"
                :class="(row.anexos || []).length ? 'text-emerald-700 dark:text-emerald-300' : 'text-muted-foreground'"
                title="Fotos e vídeos da devolução — as fotos vão como evidência do chamado no Mercado Livre"
                @click="openAnexos(row)"
              >
                <Camera class="size-3.5" />
                {{ (row.anexos || []).length || '' }}
              </button>
            </td>
            <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
              <div class="flex items-center gap-1">
                <input
                  :value="row.video_url || ''"
                  :disabled="!canEdit"
                  :class="sheetInputClass"
                  placeholder="link do vídeo"
                  @input="(e) => setRowText(row, 'video_url', (e.target as HTMLInputElement).value)"
                  @blur="saveRow(row)"
                />
                <a
                  v-if="row.video_url"
                  :href="normalizeUrl(row.video_url)"
                  target="_blank"
                  rel="noopener noreferrer"
                  title="Abrir vídeo em nova guia"
                  class="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
                >
                  <ExternalLink class="size-3.5" />
                </a>
              </div>
            </td>
            <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
              <select
                :value="row.motivo_devolucao || ''"
                :disabled="!canEdit"
                :class="sheetSelectClass"
                @change="(e) => { setRowText(row, 'motivo_devolucao', (e.target as HTMLSelectElement).value); saveRow(row) }"
              >
                <option value="">—</option>
                <option v-for="m in MOTIVOS_DEVOLUCAO" :key="m" :value="m">{{ m }}</option>
                <option
                  v-if="row.motivo_devolucao && !(MOTIVOS_DEVOLUCAO as readonly string[]).includes(row.motivo_devolucao)"
                  :value="row.motivo_devolucao"
                >{{ row.motivo_devolucao }}</option>
              </select>
            </td>
            <td class="px-2 py-1 text-center bg-amber-50/40 dark:bg-amber-900/10">
              <NuxtLink
                v-if="row.tem_chamado"
                :to="{ path: '/chamados', query: row.pedido_bling ? { search: row.pedido_bling } : {} }"
                class="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium hover:brightness-95 dark:hover:brightness-110"
                :class="row.chamado_resolvido
                  ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300'
                  : 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300'"
                :title="row.chamado_resolvido ? 'Chamado resolvido — abrir na aba Chamados' : 'Chamado em aberto — abrir na aba Chamados'"
              >
                <CheckCircle2 v-if="row.chamado_resolvido" class="size-3" />
                <Clock v-else class="size-3" />
                {{ row.chamado_numero || 'sim' }}
              </NuxtLink>
              <span v-else class="text-muted-foreground">—</span>
              <div
                v-if="row.chamado_ml_status"
                class="mt-0.5 max-w-[140px] whitespace-normal text-[10px] leading-tight"
                :class="mlStatusClass(row.chamado_ml_status)"
                :title="mlStatusLabel(row)"
              >{{ mlStatusLabel(row) }}</div>
            </td>
            <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
              <input
                :value="row.custo_manutencao ?? ''"
                :disabled="!canEdit"
                type="text"
                inputmode="decimal"
                :class="sheetMoneyInputClass"
                @input="(e) => setRowNumber(row, 'custo_manutencao', (e.target as HTMLInputElement).value)"
                @blur="saveRow(row)"
              />
            </td>
            <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
              <select
                :value="row.tecnico || ''"
                :disabled="!canEdit"
                :class="sheetSelectClass"
                @change="(e) => { setRowText(row, 'tecnico', (e.target as HTMLSelectElement).value); saveRow(row) }"
              >
                <option value="">—</option>
                <option v-for="t in TECNICOS" :key="t" :value="t">{{ t }}</option>
                <option
                  v-if="row.tecnico && !(TECNICOS as readonly string[]).includes(row.tecnico)"
                  :value="row.tecnico"
                >{{ row.tecnico }}</option>
              </select>
            </td>
            <td class="px-2 py-1 text-center bg-amber-50/40 dark:bg-amber-900/10">
              <button
                v-if="canSeeStockToggle(row.condicao_produto)"
                type="button"
                role="switch"
                :aria-checked="row.devolver_estoque"
                :disabled="!canEdit"
                :class="[
                  'relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors disabled:cursor-default disabled:opacity-70',
                  row.devolver_estoque ? 'bg-primary' : 'bg-gray-300 dark:bg-gray-600',
                ]"
                @click="toggleRowDevolverEstoque(row)"
              >
                <span
                  :class="[
                    'inline-block size-4 transform rounded-full bg-white shadow transition-transform',
                    row.devolver_estoque ? 'translate-x-4' : 'translate-x-0.5',
                  ]"
                />
              </button>
              <span v-else class="text-muted-foreground">—</span>
            </td>
            <td class="px-2 py-1 whitespace-nowrap text-muted-foreground bg-amber-50/40 dark:bg-amber-900/10">
              <div class="flex flex-col items-start gap-0.5">
                <span>{{ fmtDateTime(row.data_devolvido_estoque) }}</span>
                <span
                  v-if="row.estoque_mov_sku && !row.estoque_mov_revertido_at"
                  :title="`Estoque devolvido no Bling: +${row.estoque_mov_qty ?? 1} un. em ${row.estoque_mov_sku}`"
                  class="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-mono bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300"
                >{{ row.estoque_mov_sku }}</span>
                <span
                  v-else-if="row.estoque_mov_sku && row.estoque_mov_revertido_at"
                  :title="`Estoque estornado em ${fmtDateTime(row.estoque_mov_revertido_at)} · baixa de ${row.estoque_mov_qty ?? 1} un. em ${row.estoque_mov_sku}`"
                  class="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-mono bg-rose-100 text-rose-700 dark:bg-rose-900/30 dark:text-rose-300"
                >
                  <Undo2 class="size-2.5 shrink-0" />
                  <span class="line-through">{{ row.estoque_mov_sku }}</span>
                </span>
              </div>
            </td>
            <td class="px-2 py-1 whitespace-nowrap bg-amber-50/40 dark:bg-amber-900/10">
              <div
                v-if="row.condicao_produto === 'Manutenção' && row.prazo"
                class="flex flex-col gap-0.5"
                :title="'Prazo: 30 dias da inserção'"
              >
                <span :class="prazoOverdue(row.prazo) ? 'font-medium text-red-600 dark:text-red-400' : 'text-muted-foreground'">{{ fmtDate(row.prazo) }}</span>
                <span class="text-[10px]" :class="prazoOverdue(row.prazo) ? 'text-red-600 dark:text-red-400' : 'text-muted-foreground'">{{ prazoDiasLabel(row.prazo) }}</span>
              </div>
              <span v-else class="text-muted-foreground">—</span>
            </td>
            <td class="px-1 py-0.5 bg-emerald-50/40 dark:bg-emerald-900/10 border-l-[3px] border-gray-400 dark:border-gray-600">
              <input
                :value="row.observacao || ''"
                :disabled="!canEdit"
                :class="sheetInputClass"
                @input="(e) => setRowText(row, 'observacao', (e.target as HTMLInputElement).value)"
                @blur="saveRow(row)"
              />
            </td>
            <td v-if="isAdmin" class="px-2 py-1 whitespace-nowrap text-muted-foreground bg-slate-50/40 dark:bg-slate-800/20 border-l-[3px] border-gray-400 dark:border-gray-600" title="Última alteração feita neste registro">{{ fmtDateTime(row.updated_at) }}</td>
            <td v-if="canDelete" class="px-2 py-1 text-center">
              <button
                type="button"
                :disabled="isDeleting(row.id)"
                title="Remover lançamento (não estorna o estoque no Bling)"
                class="inline-flex h-6 w-6 items-center justify-center rounded text-muted-foreground hover:bg-red-100 hover:text-red-600 disabled:cursor-default disabled:opacity-50 dark:hover:bg-red-900/30 dark:hover:text-red-400"
                @click="removeRow(row)"
              >
                <Loader2 v-if="isDeleting(row.id)" class="size-3.5 animate-spin" />
                <Trash2 v-else class="size-3.5" />
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="tab === 'lancamentos' && total > PAGE_SIZE" class="flex items-center justify-between gap-2">
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

    <!-- Bling stock result toasts — top-right, auto-dismiss after 6s -->
    <div class="fixed top-4 right-4 z-[60] flex flex-col gap-2 w-[min(380px,calc(100vw-2rem))]">
      <TransitionGroup
        enter-active-class="transition duration-150 ease-out"
        enter-from-class="opacity-0 translate-x-4"
        enter-to-class="opacity-100 translate-x-0"
        leave-active-class="transition duration-100 ease-in"
        leave-from-class="opacity-100"
        leave-to-class="opacity-0"
      >
        <div
          v-for="t in toasts"
          :key="t.id"
          class="rounded-lg border-2 shadow-lg px-3 py-2 text-sm"
          :class="t.kind === 'success' ? 'border-emerald-400 bg-emerald-50' : t.kind === 'error' ? 'border-red-400 bg-red-50' : 'border-amber-400 bg-amber-50'"
        >
          <div class="flex items-start justify-between gap-2">
            <div class="flex-1 min-w-0">
              <div
                class="font-semibold"
                :class="t.kind === 'success' ? 'text-emerald-800' : t.kind === 'error' ? 'text-red-800' : 'text-amber-800'"
              >{{ t.title }}</div>
              <ul v-if="t.lines.length" class="mt-0.5 space-y-0.5 font-mono text-xs">
                <li
                  v-for="(ln, i) in t.lines"
                  :key="i"
                  :class="t.kind === 'success' ? 'text-emerald-700' : t.kind === 'error' ? 'text-red-700' : 'text-amber-700'"
                >{{ ln }}</li>
              </ul>
            </div>
            <button
              class="shrink-0 opacity-60 hover:opacity-100"
              :class="t.kind === 'success' ? 'text-emerald-800' : t.kind === 'error' ? 'text-red-800' : 'text-amber-800'"
              @click="dismissToast(t.id)"
            >
              <X class="size-4" />
            </button>
          </div>
        </div>
      </TransitionGroup>
    </div>

    <!-- Anexos da devolução: fotos (evidência do chamado no ML) e vídeos (só guardados) -->
    <div
      v-if="anexosRow"
      class="fixed inset-0 bg-black/60 flex items-center justify-center z-40 p-4"
      @click.self="closeAnexos"
    >
      <div class="bg-background border rounded-lg w-full max-w-lg p-5 space-y-4">
        <div class="flex items-start">
          <div>
            <h2 class="text-lg font-semibold">Fotos e vídeos da devolução</h2>
            <p class="text-sm text-muted-foreground">
              Pedido {{ anexosRow.pedido_bling || '—' }} · {{ anexosRow.conta }} · motivo {{ anexosRow.motivo_devolucao || '—' }}.
              As fotos (JPG/PNG) vão como evidência do chamado automático na plataforma (ML, TikTok, Shopee; Amazon é manual).
              Vídeo: use o campo "Link vídeo" da linha (o link entra no texto do chamado); arquivo de vídeo fica só guardado aqui — a API do ML não aceita vídeo anexo.
            </p>
          </div>
          <Button class="ml-auto" size="sm" variant="ghost" @click="closeAnexos">
            <X class="size-4" />
          </Button>
        </div>
        <div
          v-if="anexosRow.chamado_ml_status"
          class="rounded border px-2 py-1 text-xs"
          :class="mlStatusClass(anexosRow.chamado_ml_status)"
        >{{ mlStatusLabel(anexosRow) }}</div>
        <div class="flex flex-wrap gap-3">
          <div v-for="a in anexosRow.anexos || []" :key="a.id" class="relative">
            <a :href="anexoUrl(a.id)" target="_blank" rel="noopener" :title="a.filename">
              <video v-if="isVideo(a)" :src="anexoUrl(a.id)" class="h-24 w-24 rounded border object-cover" muted />
              <div v-else-if="a.content_type === 'application/pdf'" class="flex h-24 w-24 items-center justify-center rounded border text-xs">PDF</div>
              <img v-else :src="anexoUrl(a.id)" :alt="a.filename" class="h-24 w-24 rounded border object-cover" />
            </a>
            <div class="mt-0.5 w-24 truncate text-[10px] text-muted-foreground" :title="a.filename">
              <span v-if="a.ml_file_name" class="text-emerald-600" title="já enviada ao ML">✓ </span>{{ a.filename }}
            </div>
            <button
              v-if="canEdit"
              type="button"
              class="absolute -right-1.5 -top-1.5 rounded-full border bg-background p-0.5 text-red-500 hover:bg-red-500/10"
              title="remover"
              @click="removeAnexo(a)"
            >
              <X class="size-3" />
            </button>
          </div>
          <div v-if="!(anexosRow.anexos || []).length" class="text-sm text-muted-foreground">nenhum anexo ainda</div>
        </div>
        <label v-if="canEdit" class="inline-flex cursor-pointer items-center gap-1.5 rounded border px-2 py-1 text-xs hover:bg-muted">
          <Loader2 v-if="anexoUploading" class="size-3.5 animate-spin" />
          <Paperclip v-else class="size-3.5" />
          anexar foto / vídeo
          <input
            type="file"
            multiple
            accept="image/jpeg,image/png,application/pdf,video/mp4,video/quicktime,video/webm"
            class="hidden"
            :disabled="anexoUploading"
            @change="uploadAnexo"
          />
        </label>
      </div>
    </div>

    <!-- Correção de estoque: entrada manual via API, sem criar devolução -->
    <div
      v-if="correcaoOpen"
      class="fixed inset-0 bg-black/60 flex items-center justify-center z-40 p-4"
      @click.self="closeCorrecao"
    >
      <div class="bg-background border rounded-lg w-full max-w-md p-5 space-y-4">
        <div class="flex items-start">
          <div>
            <h2 class="text-lg font-semibold">Correção de estoque</h2>
            <p class="text-sm text-muted-foreground">
              Adiciona unidades de um SKU direto ao estoque Bling — sem criar devolução.
            </p>
          </div>
          <Button class="ml-auto" size="sm" variant="ghost" @click="closeCorrecao">
            <X class="size-4" />
          </Button>
        </div>

        <div class="space-y-1">
          <span class="text-[11px] font-medium text-muted-foreground">SKU</span>
          <div class="relative">
            <Search class="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
            <input
              v-model="correcaoSku"
              autocomplete="off"
              class="h-9 w-full rounded-md border bg-background pl-8 pr-3 text-sm font-mono"
              placeholder="buscar SKU ou nome do produto"
              @input="onCorrecaoSkuInput"
              @focus="correcaoShowResults = true"
              @blur="onCorrecaoSkuBlur"
              @keydown.enter.prevent
            />
            <div
              v-if="correcaoShowResults && (correcaoSearching || correcaoResults.length)"
              class="absolute z-10 mt-1 w-full rounded-md border bg-background shadow-lg max-h-56 overflow-auto"
            >
              <div v-if="correcaoSearching" class="py-3 text-center text-xs text-muted-foreground">
                <Loader2 class="size-3.5 inline animate-spin mr-1.5" /> buscando…
              </div>
              <button
                v-for="r in correcaoResults"
                :key="r.sku"
                type="button"
                class="flex w-full items-center gap-2 border-t px-2 py-1.5 text-left text-xs first:border-t-0 hover:bg-primary/10"
                @mousedown.prevent="pickCorrecaoProduct(r)"
              >
                <span class="shrink-0 font-mono">{{ r.sku }}</span>
                <span class="flex-1 truncate text-muted-foreground">{{ r.name }}</span>
                <span class="shrink-0 tabular-nums text-muted-foreground" title="saldo virtual">{{ saldoUn(r.saldo_virtual_total) }}</span>
              </button>
            </div>
          </div>
        </div>

        <div class="space-y-1">
          <span class="text-[11px] font-medium text-muted-foreground">Condição</span>
          <div class="flex gap-2">
            <button
              v-for="c in CONDICOES_CORRECAO"
              :key="c"
              type="button"
              class="flex-1 rounded-md border px-3 py-2 text-sm transition-colors"
              :class="correcaoCondicao === c ? 'border-primary bg-primary/10' : 'hover:border-primary/50'"
              @click="correcaoCondicao = c"
            >{{ c }}</button>
          </div>
        </div>

        <div class="grid grid-cols-2 gap-3">
          <label class="space-y-1">
            <span class="text-[11px] font-medium text-muted-foreground">Quantidade</span>
            <input
              v-model.number="correcaoQtd"
              type="number"
              min="1"
              class="h-9 w-full rounded-md border bg-background px-3 text-sm tabular-nums"
            />
          </label>
          <label v-if="isAdmin" class="space-y-1">
            <span class="text-[11px] font-medium text-muted-foreground">Custo (opcional)</span>
            <input
              v-model="correcaoCusto"
              type="text"
              inputmode="decimal"
              class="h-9 w-full rounded-md border bg-background px-3 text-sm text-right tabular-nums"
            />
          </label>
        </div>

        <label class="block space-y-1">
          <span class="text-[11px] font-medium text-muted-foreground">Produto (opcional, p/ criar avulso)</span>
          <input
            v-model="correcaoProduto"
            class="h-9 w-full rounded-md border bg-background px-3 text-sm"
            placeholder="nome do produto"
          />
        </label>

        <label class="block space-y-1">
          <span class="text-[11px] font-medium text-muted-foreground">Observação</span>
          <input
            v-model="correcaoObs"
            class="h-9 w-full rounded-md border bg-background px-3 text-sm"
            placeholder="observação do movimento no Bling"
            @keydown.enter.prevent="submitCorrecao"
          />
        </label>

        <p v-if="correcaoError" class="text-sm text-red-400">{{ correcaoError }}</p>

        <div class="flex justify-end gap-2 pt-1">
          <Button size="sm" variant="ghost" @click="closeCorrecao">cancelar</Button>
          <Button size="sm" :disabled="correcaoSubmitting || !canEdit || !correcaoSku.trim()" @click="submitCorrecao">
            <Loader2 v-if="correcaoSubmitting" class="size-4 mr-1.5 animate-spin" />
            <PackagePlus v-else class="size-4 mr-1.5" />
            seguir
          </Button>
        </div>
      </div>
    </div>

    <!-- Modais que abrem ANTES das chamadas de estoque/produtos Bling -->
    <DevolucaoManutencaoModal
      :open="manutencaoModal.open"
      :sku="manutencaoModal.sku"
      @confirm="onManutencaoConfirm"
      @cancel="onManutencaoCancel"
    />
    <DevolucaoTrocaSkuModal
      :open="trocaModal.open"
      :sold-sku="trocaModal.soldSku"
      @confirm="onTrocaConfirm"
      @cancel="onTrocaCancel"
    />
    <DevolucaoEstoqueModal
      :open="estoqueModal.open"
      :sku="estoqueModal.sku"
      :condicao="estoqueModal.condicao"
      :full-destino="estoqueModal.fullDestino"
      @confirm="onEstoqueConfirm"
      @cancel="onEstoqueCancel"
    />

    <!-- Modal do botão INFORMAR (só admins) — lista do Acompanhamento no Threema -->
    <InformarThreemaModal
      :open="informarOpen"
      contexto="devolucoes"
      descricao="Manda no Threema a lista de pedidos aguardando devolução (a mesma da aba Acompanhamento), com dias parados e última localização de cada um."
      @close="informarOpen = false"
    />
  </div>
</template>
