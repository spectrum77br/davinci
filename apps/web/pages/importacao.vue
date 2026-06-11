<script setup lang="ts">
// Importação — controle de pedidos de importação de malas (China).
//
// Três abas:
//   * Mala      — planilha principal de SKUs × lotes. Cada lote vira
//                 um par de colunas (qtd + total computado).
//   * Resumo    — lançamentos financeiros (lotes fechados + ajustes
//                 manuais). Linha de total no rodapé.
//   * Reposição — parâmetros da fórmula de reposição + card explicando
//                 o cálculo passo a passo.
//
// V1 caveats:
//   * estoque_bling / consumo_diario / maior_media_30d são colunas
//     manuais nesta planilha. Bling sync é uma segunda PR.
//   * Tabela vazia por padrão — operador adiciona produtos via UI.
import { computed, onScopeDispose, reactive, ref, watch } from 'vue'
import {
  Pencil, Plus, RefreshCw, Trash2, Save, Search, Download, X, AlertCircle,
  Send, CheckCircle2, Clock, Briefcase, Zap, Smartphone,
} from 'lucide-vue-next'
import { isoToday } from '~/lib/date'
import { parseBRNumber, formatBRNumber, formatPercent } from '~/lib/number'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'importacao', action: 'view' },
})

const { api } = useApi()
const auth = useAuthStore()
const canEdit = computed(() => {
  if (auth.isAdmin) return true
  const p = auth.user?.permissions?.importacao
  return Boolean(p?.edit || p?.delete)
})
const canDelete = computed(() => {
  if (auth.isAdmin) return true
  return Boolean(auth.user?.permissions?.importacao?.delete)
})

// ── Types ─────────────────────────────────────────────────────────
// Aba "reposicao" foi removida — os parâmetros (tempo_reposicao/
// tempo_estoque) continuam editáveis pela barra âmbar no topo da
// aba Mala, que é onde realmente importam.
// Sub-tab interno: 'mala' é a aba principal "Importação" (mantido o key
// histórico pra não renomear toda a página; o label mostra "Importação").
type Tab = 'mala' | 'resumo' | 'cotacao' | 'kit' | 'frete'
const tab = ref<Tab>('mala')

// ── Categoria (selector top-level, espelha /pricing/tabela) ───────────
type Categoria = 'mala' | 'eletro' | 'celular'
const CATEGORIAS = [
  { key: 'mala' as const, label: 'Mala', icon: Briefcase },
  { key: 'eletro' as const, label: 'Eletro', icon: Zap },
  { key: 'celular' as const, label: 'Celular', icon: Smartphone },
]
// Eletro não tem aba Kit (produtos não viram composto). Aba Frete é
// específica do Celular (etapa 4) — operacional de transportadora,
// saldo a pagar, ajustes manuais.
const SUBTABS_BY_CATEGORIA: Record<Categoria, readonly Tab[]> = {
  mala: ['mala', 'resumo', 'cotacao', 'kit'],
  eletro: ['mala', 'resumo', 'cotacao'],
  celular: ['mala', 'resumo', 'cotacao', 'kit', 'frete'],
}
const route = useRoute()
const router = useRouter()
const categoria = ref<Categoria>(
  (typeof route.query.cat === 'string'
    && (['mala', 'eletro', 'celular'] as const).includes(route.query.cat as Categoria))
    ? (route.query.cat as Categoria)
    : 'mala',
)
const isEletro = computed(() => categoria.value === 'eletro')
const isCelular = computed(() => categoria.value === 'celular')
// Colunas que só existem em Mala (Eletro e Celular não usam). Mala
// continua mostrando exatamente o que mostrava — só ocultamos quando
// muda pra Eletro ou Celular. Mantém isEletro como flag separada pra
// preservar o invariante de zero regressão.
const showMalaCols = computed(() => !isEletro.value && !isCelular.value)

// Colunas fixas com sticky horizontal (Celular). Mesmo padrão da aba
// Tabela de Preços (pricing/[tab].vue): position: sticky + left: Xpx
// + bg opaco + z-index alto. Largura de cada coluna = offset acumulado
// pra próxima. Mantém o offset visualmente alinhado com o min-width
// declarado em cada <th>/<td> sticky abaixo. Ordem é a ordem de
// renderização no DOM — qualquer reordenação aqui precisa bater com
// o template.
const STICKY_WIDTHS_CELULAR: Record<string, number> = {
  modelo_bling: 300,
  sku: 130,
  custo_bling: 60,
  estoque_bling: 66,
  consumo_diario: 66,
  memoria_consumo: 70,
  reposicao_estoque: 76,
  saldo_reposicao: 76,
  custo_realizado: 90,
}
function stickyLeftCelular(key: string): string {
  let left = 0
  for (const k of Object.keys(STICKY_WIDTHS_CELULAR)) {
    if (k === key) return `${left}px`
    left += STICKY_WIDTHS_CELULAR[k]
  }
  return '0px'
}
const availableSubtabs = computed(() => SUBTABS_BY_CATEGORIA[categoria.value])
const countByCategoria = ref<Record<string, number>>({})
function subtabLabel(t: Tab): string {
  return t === 'mala' ? 'Importação'
    : t === 'resumo' ? 'Resumo'
    : t === 'cotacao' ? 'Cotação'
    : t === 'frete' ? 'Frete' : 'Kit'
}
function catQs(): string {
  return `categoria=${categoria.value}`
}

type Config = { categoria: string; tempo_reposicao: number; tempo_estoque: number }
type Product = {
  id: string
  fornecedor: string | null
  modelo_china: string | null
  cor_china: string | null
  fechamento: string | null
  tsa: number | null
  modelo_bling: string | null
  sku: string
  cor: string | null
  custo_bling: string | number
  estoque_bling: number | null
  consumo_diario: string | number | null
  maior_media_30d: string | number | null
  obs: string | null
  memoria_consumo: string | number | null
  reposicao_estoque: number | null
  saldo_reposicao: number | null
  nome_gerado: string
  bling_sync_status: string | null
  bling_sync_marked_at: string | null
  bling_product_id: number | null
  bling_sync_error: string | null
  bling_sync_attempted_at: string | null
  bling_sync_done_at: string | null
  lote_quantidades: Record<string, number>
  // Cotação (aba Cotação do Celular, etapa 3).
  valor_usd: string | number | null
  valor_brl_realizado: string | number | null
  frete_type: 'regular' | 'swap' | 'acessorios' | null
  // Aba Importação Celular (etapa atual).
  custo_realizado: string | number | null
  // Paralelo a lote_quantidades — valor_usd por lote (Celular).
  lote_valores_usd: Record<string, string | number | null>
  // Paralelo — custo BRL manual por lote (Celular, lotes sem
  // taxa/frete). Migration 0128. Quando preenchido, substitui a
  // fórmula no realizado do lote.
  lote_custos_manuais: Record<string, string | number | null>
  // Migration 0138 (Celular). Override do SKU destino da entrada de
  // estoque no Bling, por lote. NULL = vai pro SKU do próprio produto.
  // `lote_item_ids` pareado pra o PATCH no dropdown.
  lote_target_skus?: Record<string, string | null>
  lote_item_ids?: Record<string, string>
}
type CotacaoParams = {
  categoria: string
  taxa_cambio: string | number
  frete_regular_pct: string | number
  frete_swap_pct: string | number
  frete_acessorios_pct: string | number
  adicional: string | number
}

// ── Frete (etapa 4) ─────────────────────────────────────────────
type FreteRow = {
  kind: 'item' | 'ajuste'
  id: string
  transportadora: string | null
  lote_id: string | null
  lote_nome: string | null
  abertura: string | null  // YYYY-MM-DD
  fechamento: string | null
  modelo_bling: string | null
  sku: string | null
  quantidade: number | null
  valor_unit: string | number | null
  total: string | number | null
  frete_pct: string | number | null
  saldo: string | number | null
  pago: boolean
  obs: string | null
}
type FreteList = {
  rows: FreteRow[]
  transportadoras: string[]
  total_a_entregar: string | number
  saldo_a_pagar: string | number
}
type Lote = {
  id: string
  nome: string
  abertura: string         // YYYY-MM-DD
  fechamento: string | null
  realizado: string | number
  previsto: string | number
  // Override do previsto computed. NULL = usando computed.
  previsto_manual: string | number | null
  saldo: string | number
  prazo: number | null
  is_aberto: boolean
  // Params do custo BRL por lote (Celular — migration 0122). NULL em
  // Mala. Quando NULL em Celular (lotes antigos), o frontend usa
  // cotacaoParams como fallback.
  transportadora?: string | null
  taxa?: string | number | null
  frete_pct?: string | number | null
  adicional?: string | number | null
  // Migration 0138 (Celular). Agregado da entrada de estoque no Bling
  // ao fechar o lote. Frontend usa pra renderizar o badge no header.
  bling_stock_total?: number
  bling_stock_sent?: number
  bling_stock_skipped?: number
  bling_stock_errors?: number
}
type ResumoRow = {
  id: string
  data: string
  lote_id: string | null
  lote_nome: string | null
  saldo: string | number
  obs: string | null
}

type CotFabricante = {
  id: string
  nome: string
  obs1: string | null
  obs2: string | null
  obs3: string | null
  obs4: string | null
  ordem: number
}
type CotProduto = { id: string; nome: string; ordem: number }
type CotValor = {
  id: string
  fabricante_id: string
  produto_id: string
  capacidade: string | null
  valor_real: string | number | null
  valor_usd: string | number | null
}
type CotacaoGrid = {
  fabricantes: CotFabricante[]
  produtos: CotProduto[]
  valores: CotValor[]
}

type KitVariation = {
  id: string
  code: string
  label: string
  ordem: number
  highlight: boolean
  obs: string | null
}
type KitBase = {
  id: string
  modelo_bling: string | null
  sku_base: string
  cor: string | null
  ordem: number
}
type KitMark = {
  id: string
  base_id: string
  variation_id: string
  bling_product_id: number | null
  bling_sync_status: 'pending' | 'sent' | 'error' | null
  bling_sync_error: string | null
  bling_sync_attempted_at: string | null
  bling_sync_done_at: string | null
  pricing_product_id: string | null
  pricing_sync_status: 'pending' | 'sent' | 'error' | null
  pricing_sync_error: string | null
  pricing_sync_done_at: string | null
}
type KitGrid = { variations: KitVariation[]; bases: KitBase[]; marks: KitMark[] }

// ── State ─────────────────────────────────────────────────────────
const products = ref<Product[]>([])
const lotes = ref<Lote[]>([])
const resumo = ref<{ items: ResumoRow[]; total: string | number }>({ items: [], total: 0 })
const config = ref<Config>({ categoria: 'mala', tempo_reposicao: 150, tempo_estoque: 60 })
const cotacao = ref<CotacaoGrid>({ fabricantes: [], produtos: [], valores: [] })

// Cotação Celular (etapa 3) — params globais + recálculo reativo do
// previsto BRL por produto. params é singleton-por-categoria, mas
// guardamos só o da categoria atual aqui (recarrega ao trocar).
const cotacaoParams = ref<CotacaoParams>({
  categoria: 'celular',
  taxa_cambio: 5.10,
  frete_regular_pct: 0.16,
  frete_swap_pct: 0.06,
  frete_acessorios_pct: 0.20,
  adicional: 12.00,
})

// Frete (aba Frete do Celular, etapa 4).
const frete = ref<FreteList>({
  rows: [],
  transportadoras: [],
  total_a_entregar: 0,
  saldo_a_pagar: 0,
})
const freteFiltroTransp = ref<string>('')
const freteOcultaPagos = ref<boolean>(true)
const freteAjusteModalOpen = ref(false)
// Quando setado, o modal entra em modo EDIT (PATCH /resumo/{id}).
// null = modo CREATE (POST /lote_ajuste).
const ajusteEditId = ref<string | null>(null)
const freteAjusteForm = reactive<{
  transportadora: string; abertura: string; saldo: string; obs: string;
  lote_nome: string
}>({
  transportadora: '', abertura: isoToday(),
  saldo: '', obs: '', lote_nome: '',
})

function resetAjusteForm() {
  ajusteEditId.value = null
  freteAjusteForm.transportadora = ''
  freteAjusteForm.abertura = isoToday()
  freteAjusteForm.saldo = ''
  freteAjusteForm.obs = ''
  freteAjusteForm.lote_nome = ''
}

function openCreateAjuste() {
  resetAjusteForm()
  freteAjusteModalOpen.value = true
}

function openEditAjuste(r: FreteRow) {
  if (r.kind !== 'ajuste') return
  ajusteEditId.value = r.id
  freteAjusteForm.transportadora = r.transportadora ?? ''
  freteAjusteForm.abertura = r.abertura ?? isoToday()
  freteAjusteForm.saldo = r.saldo == null ? '' : String(r.saldo)
  freteAjusteForm.obs = r.obs ?? ''
  freteAjusteForm.lote_nome = r.lote_nome ?? ''
  freteAjusteModalOpen.value = true
}

function closeAjusteModal() {
  freteAjusteModalOpen.value = false
  resetAjusteForm()
}

async function deleteAjuste(r: FreteRow) {
  if (r.kind !== 'ajuste') return
  const saldoStr = r.saldo == null ? '' : `US$ ${Number(r.saldo).toFixed(2)}`
  const msg = `Excluir esse ajuste?\n\nTransportadora: ${r.transportadora ?? '—'}\n`
    + `Data: ${r.abertura ?? '—'}\nSaldo: ${saldoStr}\n\nNão dá pra desfazer.`
  if (!window.confirm(msg)) return
  try {
    await api(`/api/importacao/resumo/${r.id}`, { method: 'DELETE' })
    await reloadFrete()
  } catch (e: any) {
    errorText.value = e?.data?.detail?.code || 'erro_delete_ajuste'
  }
}

async function reloadFrete() {
  if (categoria.value !== 'celular') return
  const qs = new URLSearchParams({ categoria: categoria.value })
  if (freteFiltroTransp.value) qs.set('transportadora', freteFiltroTransp.value)
  if (freteOcultaPagos.value) qs.set('pago', 'false')
  try {
    frete.value = await api<FreteList>(`/api/importacao/frete?${qs.toString()}`)
  } catch (e: any) {
    errorText.value = e?.data?.detail?.code || 'erro_frete'
  }
}

async function toggleFretePago(r: FreteRow) {
  if (r.kind !== 'item' || !canEdit.value) return
  const novo = !r.pago
  r.pago = novo  // otimista
  try {
    await api(`/api/importacao/lote_item/${r.id}`, {
      method: 'PATCH', body: { pago: novo },
    })
    await reloadFrete()
  } catch (e: any) {
    r.pago = !novo  // revert
    errorText.value = e?.data?.detail?.code || 'erro_pago'
  }
}

async function salvarFreteAjuste() {
  const saldoNum = Number(String(freteAjusteForm.saldo).replace(',', '.'))
  if (!freteAjusteForm.transportadora.trim() || !freteAjusteForm.abertura
      || !Number.isFinite(saldoNum)) {
    errorText.value = 'transportadora, abertura e saldo são obrigatórios'
    return
  }
  try {
    if (ajusteEditId.value) {
      // Edit: PATCH /resumo/{id}. `data` em vez de `abertura` (schema
      // do Resumo). `lote_nome` é opcional e patchable.
      await api(`/api/importacao/resumo/${ajusteEditId.value}`, {
        method: 'PATCH',
        body: {
          transportadora: freteAjusteForm.transportadora.trim(),
          data: freteAjusteForm.abertura,
          saldo: saldoNum,
          lote_nome: freteAjusteForm.lote_nome.trim() || null,
          obs: freteAjusteForm.obs.trim() || null,
        },
      })
    } else {
      await api('/api/importacao/lote_ajuste', {
        method: 'POST',
        body: {
          transportadora: freteAjusteForm.transportadora.trim(),
          abertura: freteAjusteForm.abertura,
          saldo: saldoNum,
          lote_nome: freteAjusteForm.lote_nome.trim() || null,
          obs: freteAjusteForm.obs.trim() || null,
          categoria: categoria.value,
        },
      })
    }
    closeAjusteModal()
    await reloadFrete()
  } catch (e: any) {
    errorText.value = e?.data?.detail?.code || 'erro_ajuste'
  }
}

// Kit grid state. `kitMarkMap` é o lookup canônico (key → mark) —
// usado pra render e pra resync. Re-construído a cada loadKit. As
// mudanças otimistas no toggle entram aqui também.
const kit = ref<KitGrid>({ variations: [], bases: [], marks: [] })
const kitMarkMap = reactive<Record<string, KitMark>>({})

// "Criar Kit": 2 modais separados (Celular/Mala) com regras próprias.
// Cada categoria abre o seu — não é unificado pra cada um evoluir
// independente. Spec dos modais em components/CreateKitVariationXModal.
const createKitCelularOpen = ref(false)
const createKitMalaOpen = ref(false)

async function onKitVariationCreated(_v: { id: string; code: string; label: string; ordem: number }) {
  // Refetch da aba pra trazer a coluna nova. Marks ficam vazias até o
  // operador clicar na célula do produto correspondente.
  try {
    const kt = await api<KitGrid>(`/api/importacao/kit?${catQs()}`)
    kit.value = kt
    rebuildKitMarkMap(kt.marks)
  } catch (e: any) {
    errorText.value = e?.data?.detail?.code || 'falha_refetch_kit'
  }
}
function kitKey(baseId: string, varId: string): string {
  return `${baseId}::${varId}`
}
function getKitMark(baseId: string, varId: string): KitMark | undefined {
  return kitMarkMap[kitKey(baseId, varId)]
}
function isKitMarked(baseId: string, varId: string): boolean {
  return kitKey(baseId, varId) in kitMarkMap
}
function rebuildKitMarkMap(marks: KitMark[]) {
  for (const k of Object.keys(kitMarkMap)) delete kitMarkMap[k]
  for (const m of marks) kitMarkMap[kitKey(m.base_id, m.variation_id)] = m
}
// Cells map: cellKey(produto_id, fabricante_id) → { capacidade, valor_real, valor_usd }
// as strings (inputs bind to strings; we coerce numbers on persist). Rebuilt
// from cotacao.valores on every load and topped-up on the fly when the user
// types into an empty cell (the row only gets POSTed if at least one field
// has a value — empty cells stay client-side until they get content).
const cotCells = reactive<Record<string, { capacidade: string; valor_real: string; valor_usd: string }>>({})
function cotCellKey(prodId: string, fabId: string): string {
  return `${prodId}::${fabId}`
}
function getCotCell(prodId: string, fabId: string) {
  const k = cotCellKey(prodId, fabId)
  if (!cotCells[k]) cotCells[k] = { capacidade: '', valor_real: '', valor_usd: '' }
  return cotCells[k]
}
function rebuildCotCells(valores: CotValor[]) {
  for (const k of Object.keys(cotCells)) delete cotCells[k]
  for (const v of valores) {
    cotCells[cotCellKey(v.produto_id, v.fabricante_id)] = {
      capacidade: v.capacidade ?? '',
      valor_real: v.valor_real == null ? '' : String(v.valor_real),
      valor_usd: v.valor_usd == null ? '' : String(v.valor_usd),
    }
  }
}

const loading = ref(false)
const errorText = ref<string | null>(null)
const saveTimers = reactive<Record<string, ReturnType<typeof setTimeout>>>({})
// Default true — fechar um lote NÃO deve sumir com ele da tabela
// (operador quer ver os totais previstos/realizados depois de fechado).
// O checkbox continua na UI pra quem quiser limpar a visão das
// "rodadas antigas".
const showClosedLotes = ref(true)
const search = ref('')
// Filtro extra da aba Importação por coluna "saldo reposição":
//   - 'todos'    → sem filtro
//   - 'positivo' → saldo_reposicao > 0  (precisa repor)
//   - 'negativo' → saldo_reposicao < 0  (já tem sobra)
//   - 'nenhum'   → saldo_reposicao null OU 0 (sem dados / neutro)
// Aplicado client-side, depois do filtro de texto.
type SaldoFilter = 'todos' | 'positivo' | 'negativo' | 'nenhum'
const saldoFilter = ref<SaldoFilter>('todos')
// Filtro análogo pra coluna "reposição estoque" (decidir o que
// comprar). Operam em AND com o saldoFilter.
type ReposicaoFilter = 'todos' | 'positivo' | 'negativo' | 'nenhum'
const reposicaoFilter = ref<ReposicaoFilter>('todos')

const visibleLotes = computed(() => lotes.value.filter((l) => showClosedLotes.value || l.is_aberto))

const filteredProducts = computed(() => {
  const q = search.value.trim().toLowerCase()
  let list = products.value
  if (q) {
    list = list.filter(
      (p) =>
        (p.sku || '').toLowerCase().includes(q)
        || (p.modelo_bling || '').toLowerCase().includes(q)
        || (p.cor || '').toLowerCase().includes(q)
        || (p.fornecedor || '').toLowerCase().includes(q),
    )
  }
  const mode = saldoFilter.value
  if (mode !== 'todos') {
    list = list.filter((p) => {
      const s = p.saldo_reposicao
      if (s == null) return mode === 'nenhum'
      const n = Number(s)
      if (mode === 'positivo') return n > 0
      if (mode === 'negativo') return n < 0
      return n === 0  // 'nenhum' inclui 0
    })
  }
  const repMode = reposicaoFilter.value
  if (repMode !== 'todos') {
    list = list.filter((p) => {
      const r = p.reposicao_estoque
      if (r == null) return repMode === 'nenhum'
      const n = Number(r)
      if (repMode === 'positivo') return n > 0
      if (repMode === 'negativo') return n < 0
      return n === 0  // 'nenhum' inclui 0
    })
  }
  return list
})

// ── Cotação Celular: cálculo do previsto + autosave ──────────────
// Fórmula validada pelo operador 2026-06-02:
//   previsto = valor_usd * (1 + frete_pct) * taxa_cambio + adicional
// Sempre calculado em tempo real — NÃO persistir o previsto.
function calcularPrevisto(prod: Product): number | null {
  const usdRaw = prod.valor_usd
  const usd = usdRaw == null || usdRaw === '' ? null : Number(usdRaw)
  if (usd == null || !Number.isFinite(usd) || usd <= 0) return null
  const frete = (prod.frete_type ?? 'regular') as 'regular' | 'swap' | 'acessorios'
  const pctRaw = cotacaoParams.value[`frete_${frete}_pct` as const]
  const pct = Number(pctRaw)
  const cambio = Number(cotacaoParams.value.taxa_cambio)
  const adic = Number(cotacaoParams.value.adicional)
  if (!Number.isFinite(pct) || !Number.isFinite(cambio) || !Number.isFinite(adic)) return null
  return usd * (1 + pct) * cambio + adic
}

// Custo BRL pras sub-cells dinâmicas por lote da aba Importação Celular.
// Diferença vs calcularPrevisto: usa params do PRÓPRIO LOTE (taxa,
// frete_pct, adicional), não os globais da Cotação. Cada remessa pode
// ter câmbio/frete diferentes. Fallback pra cotacaoParams quando os
// campos do lote ainda estão null (lote criado antes da migration 0122).
//
// Custo só existe quando o item DESSE lote tem qty E valor_usd
// preenchidos. Sem fallback pro prod.valor_usd geral — era o bug que
// mostrava custo "hipotético" mesmo sem compra real naquele lote.
function custoBRL(prod: Product, lote: Lote): number | null {
  const qty = Number(prod.lote_quantidades?.[lote.id] ?? 0)
  if (!(qty > 0)) return null
  const perLote = prod.lote_valores_usd?.[lote.id]
  if (perLote == null || perLote === '') return null
  const usd = Number(perLote)
  if (!Number.isFinite(usd) || usd <= 0) return null
  const cambio = Number(lote.taxa ?? cotacaoParams.value.taxa_cambio)
  const frete = Number(lote.frete_pct ?? cotacaoParams.value.frete_regular_pct)
  const adic = Number(lote.adicional ?? cotacaoParams.value.adicional)
  if (!Number.isFinite(cambio) || !Number.isFinite(frete) || !Number.isFinite(adic)) return null
  return usd * cambio * (1 + frete) + adic
}

const _cotacaoSaveTimers: Record<string, ReturnType<typeof setTimeout>> = {}

function scheduleSaveCotacaoParam<K extends keyof CotacaoParams>(field: K, value: string) {
  // Aceita inputs em formato BR (vírgula) ou decimal puro.
  const normalized = String(value).replace(',', '.').trim()
  const n = normalized === '' ? null : Number(normalized)
  if (n == null || !Number.isFinite(n)) return
  ;(cotacaoParams.value as any)[field] = n
  const key = `param_${String(field)}`
  if (_cotacaoSaveTimers[key]) clearTimeout(_cotacaoSaveTimers[key])
  _cotacaoSaveTimers[key] = setTimeout(async () => {
    try {
      const out = await api<CotacaoParams>(
        `/api/importacao/cotacao/params?${catQs()}`,
        { method: 'PATCH', body: { [field]: n } },
      )
      cotacaoParams.value = out
    } catch (e: any) {
      errorText.value = `Falha ao salvar ${String(field)}: ${e?.data?.detail?.code || 'erro'}`
    }
  }, 600)
}

function scheduleSaveCotacaoProduto(
  prod: Product,
  field: 'valor_usd' | 'valor_brl_realizado' | 'frete_type',
  value: any,
) {
  let normalized: any = value
  if (field !== 'frete_type') {
    const s = String(value).replace(',', '.').trim()
    normalized = s === '' ? null : Number(s)
    if (normalized != null && !Number.isFinite(normalized)) return
  }
  ;(prod as any)[field] = normalized
  const key = `prod_${prod.id}_${field}`
  if (_cotacaoSaveTimers[key]) clearTimeout(_cotacaoSaveTimers[key])
  _cotacaoSaveTimers[key] = setTimeout(async () => {
    try {
      await api(`/api/importacao/cotacao/produto/${prod.id}`, {
        method: 'PATCH', body: { [field]: normalized },
      })
    } catch (e: any) {
      errorText.value = `Falha ao salvar ${String(field)}: ${e?.data?.detail?.code || 'erro'}`
    }
  }, 600)
}

// ── Loaders ───────────────────────────────────────────────────────
async function loadAll() {
  loading.value = true
  errorText.value = null
  const qs = catQs()
  const hasKit = availableSubtabs.value.includes('kit')
  try {
    const [cfg, ps, ls, rs, ct, counts] = await Promise.all([
      api<Config>(`/api/importacao/config?${qs}`),
      api<Product[]>(`/api/importacao/products?${qs}`),
      api<Lote[]>(`/api/importacao/lotes?${qs}`),
      api<{ items: ResumoRow[]; total: string | number }>(`/api/importacao/resumo?${qs}`),
      api<CotacaoGrid>(`/api/importacao/cotacao?${qs}`),
      api<Record<string, number>>('/api/importacao/categoria-counts'),
    ])
    config.value = cfg
    products.value = ps
    lotes.value = ls
    resumo.value = rs
    cotacao.value = ct
    rebuildCotCells(ct.valores)
    countByCategoria.value = counts
    // Kit só pra mala/celular — eletro não tem; reseta o grid.
    if (hasKit) {
      const kt = await api<KitGrid>(`/api/importacao/kit?${qs}`)
      kit.value = kt
      rebuildKitMarkMap(kt.marks)
    } else {
      kit.value = { variations: [], bases: [], marks: [] }
      rebuildKitMarkMap([])
    }
    // Cotação params — só relevante pra celular nesta etapa. Endpoint
    // auto-cria a row na 1ª chamada com defaults, então é seguro chamar
    // sempre (operador acaba inicializando ao abrir a aba pela 1ª vez).
    if (categoria.value === 'celular') {
      try {
        cotacaoParams.value = await api<CotacaoParams>(`/api/importacao/cotacao/params?${qs}`)
      } catch { /* deixa defaults — UI mostra valores razoáveis */ }
      // Frete (etapa 4): carrega ao montar; futuras edições no
      // filtro/toggle disparam reloadFrete() direto.
      await reloadFrete()
    }
  } catch (e: any) {
    errorText.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    loading.value = false
  }
}
await loadAll()

// Troca de categoria: reseta a sub-tab se não existir na nova, atualiza
// a URL (?cat=) e recarrega tudo.
function setCategoria(c: Categoria) {
  if (c === categoria.value) return
  if (!SUBTABS_BY_CATEGORIA[c].includes(tab.value)) tab.value = 'mala'
  categoria.value = c
}
watch(categoria, (c) => {
  void router.replace({ query: { ...route.query, cat: c } })
  void loadAll()
})

// ── Mala: cell editing ────────────────────────────────────────────
function scheduleSave(row: Product, field: keyof Product, value: any) {
  ;(row as any)[field] = value
  const key = `prod_${row.id}_${String(field)}`
  if (saveTimers[key]) clearTimeout(saveTimers[key])
  saveTimers[key] = setTimeout(() => {
    void persistProduct(row, field)
  }, 500)
}
async function persistProduct(row: Product, field: keyof Product) {
  const key = `prod_${row.id}_${String(field)}`
  delete saveTimers[key]
  try {
    await api(`/api/importacao/products/${row.id}`, {
      method: 'PATCH',
      body: { [field]: row[field] },
    })
    // Reload to get refreshed computed fields (memoria/reposicao/saldo).
    void loadProductsOnly()
  } catch (e: any) {
    errorText.value = `Falha ao salvar ${String(field)}: ${e?.data?.detail?.code || 'erro'}`
  }
}

async function loadProductsOnly() {
  try {
    products.value = await api<Product[]>(`/api/importacao/products?${catQs()}`)
  } catch { /* ignore */ }
}

// ── Criar produto: form modal ─────────────────────────────────────
// Mirrors backend's generate_mala_name (services/importacao_naming.py).
// Keep the two in sync — both rules + edge cases must produce the same
// output so the live preview matches what gets stored on save.
function generateMalaName(
  modeloBling: string | null,
  sku: string | null,
  cor: string | null,
): string {
  const parts: string[] = ['Mala']
  const m = (modeloBling ?? '').trim()
  if (m) parts.push(m)

  const sk = (sku ?? '').trim()
  if (sk.includes('.')) {
    const suffix = sk.split('.', 2)[1]?.trim() ?? ''
    if (suffix && /^\d+$/.test(suffix)) parts.push(`tamanho ${suffix}`)
  }

  const base = parts.join(' ')
  const c = (cor ?? '').trim()
  return c ? `${base} - ${c}` : base
}

const showCreateModal = ref(false)
const creatingProduct = ref(false)
const newProduct = reactive({
  fornecedor: '',
  modelo_china: '',
  cor_china: '',
  modelo_bling: '',
  sku: '',
  cor: '',
  custo_bling: '',
  tsa: '' as string | number,
  obs: '',
})
// Dispatch por categoria — espelha generate_product_name do backend.
// Celular usa só modelo_bling (cor já embutida no nome). Spec do
// Excel operacional G1: "nome seguir → modelo bling".
function generateProductName(
  cat: Categoria, modeloBling: string | null, sku: string | null, cor: string | null,
): string {
  if (cat === 'eletro') return (modeloBling ?? '').trim() || 'Produto eletro'
  if (cat === 'celular') return (modeloBling ?? '').trim() || 'Produto celular'
  return generateMalaName(modeloBling, sku, cor)
}
const newProductPreviewName = computed(() =>
  generateProductName(categoria.value, newProduct.modelo_bling, newProduct.sku, newProduct.cor),
)

function openCreateModal() {
  newProduct.fornecedor = ''
  newProduct.modelo_china = ''
  newProduct.cor_china = ''
  newProduct.modelo_bling = ''
  newProduct.sku = ''
  newProduct.cor = ''
  newProduct.custo_bling = ''
  newProduct.tsa = ''
  newProduct.obs = ''
  showCreateModal.value = true
}

async function saveNewProduct() {
  if (!newProduct.sku.trim()) {
    errorText.value = 'SKU obrigatório'
    return
  }
  creatingProduct.value = true
  try {
    const tsaNum = newProduct.tsa === '' ? null : Number(newProduct.tsa)
    const row = await api<Product>('/api/importacao/products', {
      method: 'POST',
      body: {
        categoria: categoria.value,
        sku: newProduct.sku.trim(),
        fornecedor: newProduct.fornecedor.trim() || null,
        modelo_china: newProduct.modelo_china.trim() || null,
        cor_china: newProduct.cor_china.trim() || null,
        modelo_bling: newProduct.modelo_bling.trim() || null,
        cor: newProduct.cor.trim() || null,
        custo_bling: Number(newProduct.custo_bling) || 0,
        tsa: tsaNum && tsaNum >= 1 && tsaNum <= 3 ? tsaNum : null,
        obs: newProduct.obs.trim() || null,
      },
    })
    products.value = [...products.value, row]
    showCreateModal.value = false
  } catch (e: any) {
    errorText.value = `Falha ao adicionar: ${e?.data?.detail?.code || 'erro'}`
  } finally {
    creatingProduct.value = false
  }
}

async function sendToBling(row: Product) {
  // The backend just records intent — no real Bling call yet (BlingClient
  // has no create_product method). Once the write integration ships, this
  // same button will trigger the real sync.
  if (row.bling_sync_status === 'pending') {
    if (!confirm(`Produto ${row.sku} já está marcado como pendente. Marcar de novo?`)) return
  } else if (row.bling_sync_status === 'sent') {
    if (!confirm(`Produto ${row.sku} já foi enviado pro Bling. Reenviar?`)) return
  }
  try {
    const updated = await api<Product>(`/api/importacao/products/${row.id}/sync-bling`, {
      method: 'POST',
    })
    const idx = products.value.findIndex((p) => p.id === row.id)
    if (idx >= 0) products.value[idx] = { ...products.value[idx], ...updated }
  } catch (e: any) {
    errorText.value = `Falha ao enviar pro Bling: ${e?.data?.detail?.code || 'erro'}`
  }
}
async function removeProduct(row: Product) {
  if (!confirm(`Excluir SKU ${row.sku}?`)) return
  try {
    await api(`/api/importacao/products/${row.id}`, { method: 'DELETE' })
    products.value = products.value.filter((p) => p.id !== row.id)
  } catch (e: any) {
    errorText.value = `Falha ao excluir: ${e?.data?.detail?.code || 'erro'}`
  }
}

// ── Mala: lote actions ────────────────────────────────────────────
async function addLote() {
  const nome = prompt('Nome do novo lote (ex: ml27):')?.trim()
  if (!nome) return
  const abertura = isoToday()
  try {
    const lote = await api<Lote>('/api/importacao/lotes', {
      method: 'POST',
      body: { nome, abertura, categoria: categoria.value },
    })
    lotes.value = [lote, ...lotes.value]
  } catch (e: any) {
    errorText.value = `Falha ao criar lote: ${e?.data?.detail?.code || 'erro'}`
  }
}

async function fecharLote(lote: Lote) {
  if (lote.fechamento) return
  if (!confirm(`Fechar o lote ${lote.nome} hoje? Isso cria um lançamento no Resumo.`)) return
  const fechamento = isoToday()
  try {
    const updated = await api<Lote>(`/api/importacao/lotes/${lote.id}`, {
      method: 'PATCH',
      body: { fechamento },
    })
    const idx = lotes.value.findIndex((l) => l.id === lote.id)
    if (idx >= 0) lotes.value[idx] = updated
    // Resumo got a new row server-side — refresh.
    void loadResumoOnly()
  } catch (e: any) {
    errorText.value = `Falha ao fechar lote: ${e?.data?.detail?.code || 'erro'}`
  }
}

async function removeLote(lote: Lote) {
  if (!confirm(`Excluir lote ${lote.nome}? Isso apaga TODAS as quantidades pedidas dele.`)) return
  try {
    await api(`/api/importacao/lotes/${lote.id}`, { method: 'DELETE' })
    lotes.value = lotes.value.filter((l) => l.id !== lote.id)
    void loadProductsOnly()
  } catch (e: any) {
    errorText.value = `Falha ao excluir lote: ${e?.data?.detail?.code || 'erro'}`
  }
}

function schedulePatchLote(lote: Lote, field: keyof Lote, value: any) {
  ;(lote as any)[field] = value
  const key = `lote_${lote.id}_${String(field)}`
  if (saveTimers[key]) clearTimeout(saveTimers[key])
  saveTimers[key] = setTimeout(() => { void persistLote(lote, field) }, 500)
}

// Frete %: operador pensa em "14" (percentual), banco guarda "0.14"
// (decimal NUMERIC(6,4)). Converte na entrada; saída já é via
// formatPercent (multiplica por 100 pra exibir).
function onFretePctChange(lote: Lote, raw: string) {
  const n = parseBRNumber(raw)
  schedulePatchLote(lote, 'frete_pct', n == null ? null : n / 100)
}
async function persistLote(lote: Lote, field: keyof Lote) {
  const key = `lote_${lote.id}_${String(field)}`
  delete saveTimers[key]
  try {
    const updated = await api<Lote>(`/api/importacao/lotes/${lote.id}`, {
      method: 'PATCH',
      body: { [field]: lote[field] },
    })
    const idx = lotes.value.findIndex((l) => l.id === lote.id)
    if (idx >= 0) lotes.value[idx] = updated
    if (field === 'fechamento') void loadResumoOnly()
  } catch (e: any) {
    errorText.value = `Falha ao salvar lote: ${e?.data?.detail?.code || 'erro'}`
  }
}

// ── Mala: lote item (qty cell) ────────────────────────────────────
function scheduleLoteItem(prod: Product, loteId: string, qty: number) {
  // Optimistic local update of the dict the FE renders from.
  prod.lote_quantidades = { ...prod.lote_quantidades, [loteId]: qty }
  const key = `item_${prod.id}_${loteId}`
  if (saveTimers[key]) clearTimeout(saveTimers[key])
  saveTimers[key] = setTimeout(() => { void persistLoteItem(prod, loteId, qty) }, 400)
}
async function persistLoteItem(prod: Product, loteId: string, qty: number) {
  const key = `item_${prod.id}_${loteId}`
  delete saveTimers[key]
  try {
    await api(`/api/importacao/lotes/${loteId}/items`, {
      method: 'PUT',
      body: { product_id: prod.id, quantidade: qty },
    })
    // Lote previsto changed → refresh lotes (+ products for saldo_reposicao).
    void loadLotesOnly()
    void loadProductsOnly()
  } catch (e: any) {
    errorText.value = `Falha ao salvar quantidade: ${e?.data?.detail?.code || 'erro'}`
  }
}

// Aba Importação Celular: valor USD por (produto, lote). Reusa o
// endpoint PUT /lotes/{id}/items mantendo `quantidade` atual e
// adicionando `valor_usd`. Body vazia → null no DB (limpa o valor).
function scheduleLoteItemValor(prod: Product, loteId: string, raw: string) {
  const s = String(raw).replace(',', '.').trim()
  const novo = s === '' ? null : Number(s)
  if (novo != null && !Number.isFinite(novo)) return
  // Optimistic update.
  prod.lote_valores_usd = { ...(prod.lote_valores_usd || {}), [loteId]: novo }
  const key = `item_valor_${prod.id}_${loteId}`
  if (saveTimers[key]) clearTimeout(saveTimers[key])
  saveTimers[key] = setTimeout(async () => {
    delete saveTimers[key]
    const qty = prod.lote_quantidades?.[loteId] || 0
    try {
      await api(`/api/importacao/lotes/${loteId}/items`, {
        method: 'PUT',
        body: { product_id: prod.id, quantidade: qty, valor_usd: novo },
      })
      // Realizado/saldo do lote depende disso → recarrega.
      void loadLotesOnly()
    } catch (e: any) {
      errorText.value = `Falha ao salvar valor USD: ${e?.data?.detail?.code || 'erro'}`
    }
  }, 400)
}
// Aba Importação Celular: custo BRL manual por (produto, lote) — usado
// só em lotes sem taxa/frete (ex: i48, acessórios em massa). Persiste
// via PATCH /lote_item/{id} (já existia pra `pago`, migration 0128 abriu
// pro custo_manual também). raw vazio → null (limpa).
function scheduleLoteItemCustoManual(prod: Product, loteId: string, raw: string) {
  const novo = parseBRNumber(raw)
  prod.lote_custos_manuais = { ...(prod.lote_custos_manuais || {}), [loteId]: novo }
  const key = `item_custo_${prod.id}_${loteId}`
  if (saveTimers[key]) clearTimeout(saveTimers[key])
  saveTimers[key] = setTimeout(async () => {
    delete saveTimers[key]
    try {
      // O PATCH endpoint é por item_id, não por (lote, product). Precisamos
      // primeiro garantir que o item existe — usa o upsert PUT com a
      // quantidade atual (mantém) e depois o PATCH com custo_manual.
      const qty = prod.lote_quantidades?.[loteId] || 0
      const upserted = await api<{ item_id?: string }>(
        `/api/importacao/lotes/${loteId}/items`,
        { method: 'PUT', body: { product_id: prod.id, quantidade: qty } },
      )
      const itemId = upserted?.item_id
      if (itemId) {
        await api(`/api/importacao/lote_item/${itemId}`, {
          method: 'PATCH', body: { custo_manual: novo },
        })
      } else {
        // Fallback: o upsert antigo não retornava item_id. Refetch da
        // lista de lotes/produtos pra obter o id e tentar de novo via
        // próxima edição. Por hora avisa o operador.
        errorText.value = 'Não foi possível salvar o custo manual — recarregue a página.'
      }
      void loadLotesOnly()
    } catch (e: any) {
      errorText.value = `Falha ao salvar custo manual: ${e?.data?.detail?.code || 'erro'}`
    }
  }, 400)
}
// Aba Importação Celular: cache de variantes de SKU por item_id. Lazy
// load no focus do dropdown (POR ITEM, porque o "prefixo base" muda
// entre produtos). Migration 0138.
const variantsByItem = ref<Record<string, Array<{ sku: string; name: string | null }>>>({})

async function loadSkuVariants(itemId: string) {
  if (!itemId || variantsByItem.value[itemId]) return
  try {
    const r = await api<{ variants: Array<{ sku: string; name: string | null }> }>(
      `/api/importacao/lote_item/${itemId}/sku-variants`,
    )
    variantsByItem.value = { ...variantsByItem.value, [itemId]: r.variants || [] }
  } catch {
    variantsByItem.value = { ...variantsByItem.value, [itemId]: [] }
  }
}

// Opções do dropdown de destino. Combina: SKU do produto + SKU
// atualmente selecionado (se diferente) + variantes carregadas (lazy).
// Set garante dedupe. Cobre o caso "variantes ainda não carregadas mas
// já existe target_sku setado" — a opção atual aparece de qualquer jeito.
function loteItemSkuOptions(prod: Product, loteId: string): string[] {
  const itemId = prod.lote_item_ids?.[loteId] || ''
  const current = prod.lote_target_skus?.[loteId] || prod.sku
  const all = new Set<string>()
  if (prod.sku) all.add(prod.sku)
  if (current) all.add(current)
  for (const v of variantsByItem.value[itemId] || []) {
    if (v.sku) all.add(v.sku)
  }
  return Array.from(all).sort()
}

// Persiste a escolha do SKU destino no item. raw vazio → null (limpa
// override, volta a usar o SKU do próprio ImportProduct).
async function setLoteItemTargetSku(
  prod: Product, loteId: string, itemId: string, raw: string,
) {
  const novo = raw && raw.trim() && raw.trim() !== prod.sku ? raw.trim() : null
  prod.lote_target_skus = { ...(prod.lote_target_skus || {}), [loteId]: novo }
  try {
    await api(`/api/importacao/lote_item/${itemId}`, {
      method: 'PATCH', body: { bling_stock_target_sku: novo },
    })
  } catch (e: any) {
    errorText.value = `Falha ao salvar SKU destino: ${e?.data?.detail?.code || 'erro'}`
  }
}

async function loadLotesOnly() {
  try { lotes.value = await api<Lote[]>(`/api/importacao/lotes?${catQs()}`) } catch { /* ignore */ }
}
async function loadResumoOnly() {
  try {
    resumo.value = await api<{ items: ResumoRow[]; total: string | number }>(
      `/api/importacao/resumo?${catQs()}`,
    )
  } catch { /* ignore */ }
}

// ── Resumo: add manual entry ──────────────────────────────────────
const newResumo = reactive({
  data: isoToday(),
  lote_nome: '',
  saldo: '',
  obs: '',
})
const addingResumo = ref(false)
async function addResumo() {
  if (!newResumo.saldo || isNaN(Number(newResumo.saldo))) {
    errorText.value = 'saldo inválido'
    return
  }
  try {
    await api('/api/importacao/resumo', {
      method: 'POST',
      body: {
        categoria: categoria.value,
        data: newResumo.data,
        lote_nome: newResumo.lote_nome.trim() || null,
        saldo: Number(newResumo.saldo),
        obs: newResumo.obs.trim() || null,
      },
    })
    newResumo.lote_nome = ''
    newResumo.saldo = ''
    newResumo.obs = ''
    addingResumo.value = false
    await loadResumoOnly()
  } catch (e: any) {
    errorText.value = `Falha ao incluir lançamento: ${e?.data?.detail?.code || 'erro'}`
  }
}
async function removeResumo(row: ResumoRow) {
  if (!confirm('Excluir esse lançamento?')) return
  try {
    await api(`/api/importacao/resumo/${row.id}`, { method: 'DELETE' })
    await loadResumoOnly()
  } catch (e: any) {
    errorText.value = `Falha ao excluir: ${e?.data?.detail?.code || 'erro'}`
  }
}

// ── Config (parâmetros da fórmula de reposição) ───────────────────
// Editado pela barra âmbar no topo da aba Mala (a aba Reposição foi
// removida). Cada @change dispara este PATCH + reload dos produtos
// pra recalcular reposicao_estoque/saldo_reposicao.
async function saveConfig() {
  try {
    // Config é por categoria (migration 0132). O body do PATCH só leva
    // tempo_reposicao/tempo_estoque — a categoria vai por query string,
    // pra qual row alterar.
    config.value = await api<Config>(`/api/importacao/config?${catQs()}`, {
      method: 'PATCH',
      body: {
        tempo_reposicao: config.value.tempo_reposicao,
        tempo_estoque: config.value.tempo_estoque,
      },
    })
    void loadProductsOnly()
  } catch (e: any) {
    errorText.value = `Falha ao salvar config: ${e?.data?.detail?.code || 'erro'}`
  }
}

// ── Formatters ────────────────────────────────────────────────────
function fmtMoney(v: string | number | null | undefined): string {
  if (v == null || v === '') return '—'
  const n = Number(v)
  if (!Number.isFinite(n)) return '—'
  return n.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
}
// USD com prefixo "US$" — usado na aba Frete (valores em dólar). O
// `style: currency / currency: USD` produz "US$1,234.56"; queremos
// "US$ 1.234,56" no padrão BR. Por isso formatamos o número à mão.
function fmtUsd(v: string | number | null | undefined): string {
  if (v == null || v === '') return '—'
  const n = Number(v)
  if (!Number.isFinite(n)) return '—'
  return `US$ ${n.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}
function fmtNum2(v: string | number | null | undefined): string {
  if (v == null || v === '') return '—'
  const n = Number(v)
  if (!Number.isFinite(n)) return '—'
  return n.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function reposicaoClass(v: number | null): string {
  if (v == null) return 'text-muted-foreground'
  if (v > 0) return 'text-red-700 font-semibold'
  if (v < 0) return 'text-emerald-700 font-semibold'
  return ''
}
function loteTotal(prod: Product, loteId: string): number {
  const q = prod.lote_quantidades[loteId] || 0
  return q * Number(prod.custo_bling || 0)
}

// Cor de fundo alternada por paridade do número do lote (ML25, ML26, …).
// Ímpar = bege amber-100; par = cinza slate-200. Cobre header (8 rows)
// + body (quant/total). `!bg-*` é OBRIGATÓRIO — o CSS scoped da página
// seta `background:` direto em .lote-label/.lote-value/.col-quant/etc,
// e classes Tailwind sem `!important` perdem na cascata pra essas
// regras. Strings inteiras (não interpoladas) pra não cair no purge.
function loteBgClass(loteName: string | null | undefined): string {
  const m = (loteName || '').match(/(\d+)/)
  if (!m) return ''
  const n = parseInt(m[1], 10)
  return n % 2 === 0
    ? '!bg-slate-200 !text-slate-700 dark:!bg-slate-700/60 dark:!text-slate-100'
    : '!bg-amber-100 !text-amber-900 dark:!bg-amber-900/40 dark:!text-amber-100'
}

// ── Cotação: handlers ─────────────────────────────────────────────
// Autosave pattern mirrors the Mala tab: 500ms debounce per (row, field).
// Fabricante and produto edits go to PATCH; cell edits go to PUT (upsert).

function scheduleCotFab(fab: CotFabricante, field: keyof CotFabricante, value: any) {
  ;(fab as any)[field] = value
  const key = `cot_fab_${fab.id}_${String(field)}`
  if (saveTimers[key]) clearTimeout(saveTimers[key])
  saveTimers[key] = setTimeout(() => { void persistCotFab(fab, field) }, 500)
}
async function persistCotFab(fab: CotFabricante, field: keyof CotFabricante) {
  delete saveTimers[`cot_fab_${fab.id}_${String(field)}`]
  try {
    await api(`/api/importacao/cotacao/fabricantes/${fab.id}`, {
      method: 'PATCH',
      body: { [field]: (fab as any)[field] },
    })
  } catch (e: any) {
    errorText.value = `Falha ao salvar fabricante: ${e?.data?.detail?.code || 'erro'}`
  }
}

function scheduleCotProduto(p: CotProduto, value: string) {
  p.nome = value
  const key = `cot_prod_${p.id}`
  if (saveTimers[key]) clearTimeout(saveTimers[key])
  saveTimers[key] = setTimeout(() => { void persistCotProduto(p) }, 500)
}
async function persistCotProduto(p: CotProduto) {
  delete saveTimers[`cot_prod_${p.id}`]
  try {
    await api(`/api/importacao/cotacao/produtos/${p.id}`, {
      method: 'PATCH',
      body: { nome: p.nome },
    })
  } catch (e: any) {
    errorText.value = `Falha ao salvar produto: ${e?.data?.detail?.code || 'erro'}`
  }
}

function scheduleCotCell(
  prodId: string,
  fabId: string,
  field: 'capacidade' | 'valor_real' | 'valor_usd',
  value: string,
) {
  const cell = getCotCell(prodId, fabId)
  cell[field] = value
  const key = `cot_cell_${prodId}_${fabId}`
  if (saveTimers[key]) clearTimeout(saveTimers[key])
  saveTimers[key] = setTimeout(() => { void persistCotCell(prodId, fabId) }, 400)
}
async function persistCotCell(prodId: string, fabId: string) {
  delete saveTimers[`cot_cell_${prodId}_${fabId}`]
  const cell = getCotCell(prodId, fabId)
  try {
    await api('/api/importacao/cotacao/valores', {
      method: 'PUT',
      body: {
        fabricante_id: fabId,
        produto_id: prodId,
        capacidade: cell.capacidade.trim() === '' ? null : cell.capacidade,
        valor_real: cell.valor_real === '' ? null : Number(cell.valor_real),
        valor_usd: cell.valor_usd === '' ? null : Number(cell.valor_usd),
      },
    })
  } catch (e: any) {
    errorText.value = `Falha ao salvar célula: ${e?.data?.detail?.code || 'erro'}`
  }
}

const addingCotFab = ref(false)
async function addCotFabricante() {
  if (addingCotFab.value) return
  addingCotFab.value = true
  try {
    const fab = await api<CotFabricante>(`/api/importacao/cotacao/fabricantes?${catQs()}`, { method: 'POST' })
    cotacao.value.fabricantes = [...cotacao.value.fabricantes, fab]
  } catch (e: any) {
    errorText.value = `Falha ao criar fabricante: ${e?.data?.detail?.code || 'erro'}`
  } finally {
    addingCotFab.value = false
  }
}
async function removeCotFabricante(fab: CotFabricante) {
  if (!confirm(`Excluir fabricante "${fab.nome || '(sem nome)'}"? Isso apaga TODAS as cotações dele.`)) return
  try {
    await api(`/api/importacao/cotacao/fabricantes/${fab.id}`, { method: 'DELETE' })
    cotacao.value.fabricantes = cotacao.value.fabricantes.filter((f) => f.id !== fab.id)
    // Clean stale cell entries for this fabricante.
    for (const k of Object.keys(cotCells)) {
      if (k.endsWith(`::${fab.id}`)) delete cotCells[k]
    }
  } catch (e: any) {
    errorText.value = `Falha ao excluir fabricante: ${e?.data?.detail?.code || 'erro'}`
  }
}

const addingCotProd = ref(false)
async function addCotProduto() {
  if (addingCotProd.value) return
  addingCotProd.value = true
  try {
    const prod = await api<CotProduto>(`/api/importacao/cotacao/produtos?${catQs()}`, { method: 'POST' })
    cotacao.value.produtos = [...cotacao.value.produtos, prod]
  } catch (e: any) {
    errorText.value = `Falha ao incluir produto: ${e?.data?.detail?.code || 'erro'}`
  } finally {
    addingCotProd.value = false
  }
}
async function removeCotProduto(prod: CotProduto) {
  if (!confirm(`Excluir produto "${prod.nome || '(sem nome)'}"?`)) return
  try {
    await api(`/api/importacao/cotacao/produtos/${prod.id}`, { method: 'DELETE' })
    cotacao.value.produtos = cotacao.value.produtos.filter((p) => p.id !== prod.id)
    for (const k of Object.keys(cotCells)) {
      if (k.startsWith(`${prod.id}::`)) delete cotCells[k]
    }
  } catch (e: any) {
    errorText.value = `Falha ao excluir produto: ${e?.data?.detail?.code || 'erro'}`
  }
}

// ── Kit: toggle handler ────────────────────────────────────────────
// Toggle otimista: muda kitMarkMap local, manda PUT, reverte em caso
// de erro. Sem debounce — toggle é discreto. Desmarcar uma row que já
// foi sincronizada com Bling exibe warning (destrutivo).
async function toggleKitMark(baseId: string, varId: string) {
  const key = kitKey(baseId, varId)
  const existing = kitMarkMap[key]
  const wasMarked = existing !== undefined

  // Warning na desmarcação se já sincronizado.
  if (wasMarked && existing.bling_product_id !== null) {
    const ok = confirm(
      `Esse kit foi criado no Bling (id ${existing.bling_product_id}).\n\n`
      + `Desmarcar aqui NÃO remove o produto no Bling — você precisa apagar lá manualmente se quiser.\n\n`
      + `Continuar?`,
    )
    if (!ok) return
  }

  // Otimista: snapshot pra rollback.
  const snapshot = existing ? { ...existing } : null
  if (wasMarked) {
    delete kitMarkMap[key]
  } else {
    // Mark pending até o server confirmar (não temos id ainda).
    kitMarkMap[key] = {
      id: 'pending',
      base_id: baseId,
      variation_id: varId,
      bling_product_id: null,
      bling_sync_status: 'pending',
      bling_sync_error: null,
      bling_sync_attempted_at: null,
      bling_sync_done_at: null,
      pricing_product_id: null,
      pricing_sync_status: null,
      pricing_sync_error: null,
      pricing_sync_done_at: null,
    }
  }
  try {
    // Backend retorna a mark criada (com id real do DB) ou null se
    // desmarcou. Antes era 204 sem body — frontend mantinha placeholder
    // local com id='pending' que nunca casava com a row real, e se o
    // backend de alguma forma não persistisse, o operador ficava com
    // "x laranja" eternamente sem worker pra processar.
    const result = await api<KitMark | null>('/api/importacao/kit/mark', {
      method: 'PUT',
      body: { base_id: baseId, variation_id: varId, marked: !wasMarked },
    })
    if (result != null) {
      // Sobrescreve o placeholder com a mark real (id verdadeiro,
      // status atualizado pelo backend).
      kitMarkMap[key] = result
    }
    // Se result=null, mark foi deletada — kitMarkMap já está sem ela.
  } catch (e: any) {
    // Rollback
    if (wasMarked && snapshot) {
      kitMarkMap[key] = snapshot
    } else {
      delete kitMarkMap[key]
    }
    errorText.value = `Falha ao salvar marca do kit: ${e?.data?.detail?.code || 'erro'}`
  }
}

async function resyncKitMark(mark: KitMark) {
  try {
    const updated = await api<KitMark>(`/api/importacao/kit/mark/${mark.id}/resync`, {
      method: 'POST',
    })
    kitMarkMap[kitKey(mark.base_id, mark.variation_id)] = updated
  } catch (e: any) {
    errorText.value = `Falha ao reenviar: ${e?.data?.detail?.code || 'erro'}`
  }
}

async function resyncKitPricing(mark: KitMark) {
  try {
    const updated = await api<KitMark>(`/api/importacao/kit/mark/${mark.id}/resync-pricing`, {
      method: 'POST',
    })
    kitMarkMap[kitKey(mark.base_id, mark.variation_id)] = updated
  } catch (e: any) {
    errorText.value = `Falha ao reenviar pricing: ${e?.data?.detail?.code || 'erro'}`
  }
}

function kitMarkTitle(m: KitMark | undefined): string {
  if (!m) return 'Clique pra marcar'
  const parts: string[] = []
  if (m.bling_sync_status === 'sent') {
    parts.push(`Bling: id ${m.bling_product_id}`)
  } else if (m.bling_sync_status === 'pending') {
    parts.push('Bling: aguardando…')
  } else if (m.bling_sync_status === 'error') {
    parts.push(`Bling erro: ${m.bling_sync_error ?? '?'}`)
  }
  if (m.pricing_sync_status === 'sent') {
    parts.push('Pricing: criado')
  } else if (m.pricing_sync_status === 'pending') {
    parts.push('Pricing: aguardando…')
  } else if (m.pricing_sync_status === 'error') {
    parts.push(`Pricing erro: ${m.pricing_sync_error ?? '?'}`)
  }
  return parts.length > 0 ? parts.join(' | ') : 'Marcado'
}

// Cor da célula: error em qualquer um vermelho, pending âmbar, sent
// (ou só Bling sent + pricing null) verde.
function kitMarkColorClass(m: KitMark | undefined): string {
  if (!m) return ''
  if (m.bling_sync_status === 'error' || m.pricing_sync_status === 'error') {
    return 'kit-mark-error'
  }
  if (m.bling_sync_status === 'pending' || m.pricing_sync_status === 'pending') {
    return 'kit-mark-pending'
  }
  return 'kit-mark-sent'
}

function kitMarkHasError(m: KitMark | undefined): boolean {
  if (!m) return false
  return m.bling_sync_status === 'error' || m.pricing_sync_status === 'error'
}

// Polling leve: se há marks pending, refresh do grid a cada 10s.
// Para automaticamente quando não há mais pending. Limitado à aba kit.
let kitPollHandle: ReturnType<typeof setInterval> | null = null
async function reloadKitOnly() {
  try {
    const kt = await api<KitGrid>(`/api/importacao/kit?${catQs()}`)
    kit.value = kt
    rebuildKitMarkMap(kt.marks)
  } catch { /* ignore */ }
}
function hasPendingKitMarks(): boolean {
  for (const k in kitMarkMap) {
    const m = kitMarkMap[k]
    if (m.bling_sync_status === 'pending' || m.pricing_sync_status === 'pending') {
      return true
    }
  }
  return false
}
watch([() => tab.value, kitMarkMap], () => {
  const shouldPoll = tab.value === 'kit' && hasPendingKitMarks()
  if (shouldPoll && !kitPollHandle) {
    kitPollHandle = setInterval(() => { void reloadKitOnly() }, 10_000)
  } else if (!shouldPoll && kitPollHandle) {
    clearInterval(kitPollHandle)
    kitPollHandle = null
  }
}, { deep: true })

// Polling análogo na aba Mala: refresh do grid a cada 10s enquanto
// houver produtos com bling_sync_status='pending'. Para automaticamente
// quando todos viram 'sent'/'error'.
let malaPollHandle: ReturnType<typeof setInterval> | null = null
function hasPendingMalaProducts(): boolean {
  return products.value.some((p) => p.bling_sync_status === 'pending')
}
watch([() => tab.value, products], () => {
  const shouldPoll = tab.value === 'mala' && hasPendingMalaProducts()
  if (shouldPoll && !malaPollHandle) {
    malaPollHandle = setInterval(() => { void loadProductsOnly() }, 10_000)
  } else if (!shouldPoll && malaPollHandle) {
    clearInterval(malaPollHandle)
    malaPollHandle = null
  }
}, { deep: true })

onScopeDispose(() => {
  if (kitPollHandle) clearInterval(kitPollHandle)
  if (malaPollHandle) clearInterval(malaPollHandle)
})
</script>

<template>
  <div class="space-y-3 p-4">
    <!-- Selector top-level de categoria (Mala / Eletro / Celular) -->
    <div class="flex flex-wrap gap-2">
      <button
        v-for="cat in CATEGORIAS"
        :key="cat.key"
        class="inline-flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium transition-colors"
        :class="categoria === cat.key
          ? 'bg-primary text-primary-foreground'
          : 'bg-card border hover:bg-muted'"
        @click="setCategoria(cat.key)"
      >
        <component :is="cat.icon" class="size-4" />
        {{ cat.label }} ({{ countByCategoria[cat.key] ?? 0 }})
      </button>
    </div>

    <!-- Header + tab nav -->
    <div class="flex flex-wrap items-center gap-3">
      <h1 class="text-xl font-semibold">Importação</h1>
      <div class="flex gap-1 rounded-md bg-muted/40 p-1 w-fit">
        <button
          v-for="t in availableSubtabs"
          :key="t"
          class="px-3 py-1.5 rounded text-sm transition-colors"
          :class="tab === t ? 'bg-background shadow-sm font-medium' : 'hover:bg-background/60 text-muted-foreground'"
          @click="tab = t"
        >
          {{ subtabLabel(t) }}
        </button>
      </div>
      <button
        class="ml-auto inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
        :disabled="loading"
        @click="loadAll"
      >
        <RefreshCw class="size-3.5" :class="{ 'animate-spin': loading }" /> Recarregar
      </button>
    </div>

    <div v-if="errorText" class="text-sm text-destructive flex items-center gap-2">
      <AlertCircle class="size-3.5" /> {{ errorText }}
    </div>

    <!-- ─── TAB MALA ─────────────────────────────────────────────── -->
    <div v-if="tab === 'mala'" class="space-y-2">
      <!-- Replenishment parameters — same fields as the Reposição tab,
           surfaced here so the operator can tweak them while reading
           the table. Both inputs PATCH the same singleton config row
           used by the backend's _compute_product_fields() — every
           reposição/saldo cell recalculates on the next loadProductsOnly
           triggered by saveConfig(). -->
      <div class="flex flex-wrap items-center gap-3 bg-amber-50 dark:bg-amber-900/20 border rounded-md px-3 py-2 text-xs">
        <label class="inline-flex items-center gap-2">
          <span class="font-semibold uppercase tracking-wide text-[10px]">tempo de reposição</span>
          <input
            type="number"
            min="0"
            class="h-7 w-20 border rounded px-2 text-right text-sm bg-background"
            v-model.number="config.tempo_reposicao"
            :disabled="!canEdit"
            @change="saveConfig"
          />
          <span class="text-[10px] text-muted-foreground">dias</span>
        </label>
        <label class="inline-flex items-center gap-2">
          <span class="font-semibold uppercase tracking-wide text-[10px]">tempo de estoque</span>
          <input
            type="number"
            min="0"
            class="h-7 w-20 border rounded px-2 text-right text-sm bg-background"
            v-model.number="config.tempo_estoque"
            :disabled="!canEdit"
            @change="saveConfig"
          />
          <span class="text-[10px] text-muted-foreground">dias</span>
        </label>
        <span class="ml-auto text-[10px] text-muted-foreground">
          Recalcula reposição/saldo em tempo real
        </span>
      </div>

      <div class="flex flex-wrap items-center gap-2 bg-muted/30 border rounded-md px-3 py-2 text-xs">
        <div class="relative flex-1 min-w-[200px] max-w-sm">
          <Search class="absolute left-2 top-1/2 -translate-y-1/2 size-3 text-muted-foreground" />
          <input
            v-model="search"
            type="search"
            placeholder="Buscar SKU, modelo, cor, fornecedor…"
            class="h-7 w-full border rounded pl-7 pr-2 text-xs bg-background"
          />
        </div>
        <label class="inline-flex items-center gap-1 cursor-pointer">
          <input type="checkbox" v-model="showClosedLotes" /> mostrar lotes fechados
        </label>
        <label class="inline-flex items-center gap-1">
          <span class="text-muted-foreground">saldo reposição:</span>
          <select v-model="saldoFilter" class="h-7 border rounded px-2 bg-background">
            <option value="todos">todos</option>
            <option value="positivo">positivo</option>
            <option value="negativo">negativo</option>
            <option value="nenhum">nenhum</option>
          </select>
        </label>
        <label class="inline-flex items-center gap-1">
          <span class="text-muted-foreground">reposição estoque:</span>
          <select v-model="reposicaoFilter" class="h-7 border rounded px-2 bg-background">
            <option value="todos">todos</option>
            <option value="positivo">positivo</option>
            <option value="negativo">negativo</option>
            <option value="nenhum">nenhum</option>
          </select>
        </label>
        <span class="text-muted-foreground">
          {{ filteredProducts.length }} SKUs · {{ visibleLotes.length }} lote(s)
        </span>
        <button
          v-if="canEdit"
          class="ml-auto inline-flex items-center gap-1 rounded-md bg-primary text-primary-foreground px-2.5 py-1 hover:opacity-90"
          @click="openCreateModal"
        >
          <Plus class="size-3" /> Criar produto
        </button>
        <button
          v-if="canEdit"
          class="inline-flex items-center gap-1 rounded-md border px-2.5 py-1 hover:bg-muted"
          @click="addLote"
        >
          <Plus class="size-3" /> Criar lote
        </button>
      </div>

      <!-- max-height + overflow:auto cria um container de scroll próprio
           pra tabela. Sem isso, position:sticky nas células do cabeçalho
           não tem âncora (overflow-x:auto sozinho não estabelece um
           contexto de scroll vertical funcional). 100vh − 220px deixa
           espaço pro header da página + barra de parâmetros. -->
      <div class="border rounded-md overflow-auto" style="max-height: calc(100vh - 220px)">
        <table class="grid-table text-xs border-collapse">
          <thead class="thead-sticky">
            <!-- 8-row header. Fixed left columns use rowspan=8 so their
                 label sits centered across the full header height.
                 Each lote occupies 2 cols (label + value) and fills
                 rows 1-7 with metadata (lote/abertura/fechamento/
                 previsto/realizado/saldo/prazo) then row 8 with the
                 actual sub-headers (quant | total) that align with
                 the per-cell inputs in tbody. Mirrors the operator's
                 Excel layout 1:1. -->
            <tr>
              <!-- min-widths fixados pra: SKU/cor confortáveis, e as
                   colunas numéricas estreitas o suficiente pra caber
                   as palavras inteiras (sem quebrar "memória" em
                   "memóri/a"). Palavras curtas (até ~7 chars) → 60-66px;
                   "reposição"/"saldo" → 72-76px.
                   fornecedor/modelo china/cor china/fechamento/TSA
                   foram removidos da exibição — `modelo bling` já vem
                   consolidado do Bling. Campos seguem no DB (sync ainda
                   grava) e no modal "Criar produto". -->
              <th
                :rowspan="isCelular ? 12 : 8"
                class="col-head text-left"
                :class="isCelular ? 'sticky bg-background z-30' : ''"
                :style="isCelular ? { left: stickyLeftCelular('modelo_bling'), minWidth: '300px' } : { minWidth: '500px' }"
              >modelo bling</th>
              <th
                :rowspan="isCelular ? 12 : 8"
                class="col-head text-left"
                :class="isCelular ? 'sticky bg-background z-30' : ''"
                :style="isCelular ? { left: stickyLeftCelular('sku'), minWidth: '130px' } : { minWidth: '130px' }"
              >sku</th>
              <th
                :rowspan="isCelular ? 12 : 8"
                class="col-head text-right"
                :class="isCelular ? 'sticky bg-background z-30' : ''"
                :style="isCelular ? { left: stickyLeftCelular('custo_bling'), minWidth: '60px' } : { minWidth: '60px' }"
              >custo bling</th>
              <th
                :rowspan="isCelular ? 12 : 8"
                class="col-head text-right"
                :class="isCelular ? 'sticky bg-background z-30' : ''"
                :style="isCelular ? { left: stickyLeftCelular('estoque_bling'), minWidth: '66px' } : { minWidth: '66px' }"
              >estoque bling</th>
              <th
                :rowspan="isCelular ? 12 : 8"
                class="col-head text-right"
                :class="isCelular ? 'sticky bg-background z-30' : ''"
                :style="isCelular ? { left: stickyLeftCelular('consumo_diario'), minWidth: '66px' } : { minWidth: '66px' }"
              >consumo diário</th>
              <th
                :rowspan="isCelular ? 12 : 8"
                class="col-head text-right"
                :class="isCelular ? 'sticky bg-background z-30' : ''"
                :style="isCelular ? { left: stickyLeftCelular('memoria_consumo'), minWidth: '70px' } : { minWidth: '70px' }"
              >memória consumo</th>
              <th
                :rowspan="isCelular ? 12 : 8"
                class="col-head text-right"
                :class="isCelular ? 'sticky bg-background z-30' : ''"
                :style="isCelular ? { left: stickyLeftCelular('reposicao_estoque'), minWidth: '76px' } : { minWidth: '76px' }"
              >reposição estoque</th>
              <th
                :rowspan="isCelular ? 12 : 8"
                class="col-head text-right"
                :class="isCelular ? 'sticky bg-background z-30' : ''"
                :style="isCelular ? { left: stickyLeftCelular('saldo_reposicao'), minWidth: '76px' } : { minWidth: '76px' }"
              >saldo reposição</th>
              <!-- Mala: `obs` fica nas colunas fixas. Celular não usa
                   obs nesta tabela (operador anotou que é unused no
                   Excel celular); ficar oculto pra não confundir. -->
              <th v-if="!isCelular" :rowspan="isCelular ? 12 : 8" class="col-head text-left" style="min-width: 100px">obs</th>
              <!-- Celular: `custo realizado` (coluna J do Excel — "media
                   do custo", editável manualmente). -->
              <th
                v-if="isCelular"
                :rowspan="isCelular ? 12 : 8"
                class="col-head text-right sticky bg-background z-30"
                :style="{ left: stickyLeftCelular('custo_realizado'), minWidth: '90px' }"
              >custo realizado</th>
              <template v-for="lote in visibleLotes" :key="`lote-r1-${lote.id}`">
                <td class="lote-label border-l" :class="loteBgClass(lote.nome)">lote</td>
                <td class="lote-value" :colspan="isCelular ? 2 : 1" :class="loteBgClass(lote.nome)">
                  <span class="font-semibold uppercase">{{ lote.nome }}</span>
                  <button v-if="canEdit && lote.is_aberto" class="ml-2 text-[10px] underline hover:text-primary" @click="fecharLote(lote)">fechar</button>
                  <button v-if="canDelete" class="ml-1 text-destructive" @click="removeLote(lote)" :title="`Excluir ${lote.nome}`">
                    <Trash2 class="size-3 inline" />
                  </button>
                  <!-- Badge "Bling estoque": só Celular, só lote fechado. Cores:
                       verde = tudo sent; vermelho = qualquer erro;
                       amarelo = só skipped; cinza = sem items. -->
                  <span
                    v-if="isCelular && lote.fechamento && (lote.bling_stock_total ?? 0) > 0"
                    class="ml-2 inline-block rounded px-1.5 py-0.5 text-[9px] font-medium border"
                    :class="{
                      'bg-emerald-50 text-emerald-700 border-emerald-300':
                        (lote.bling_stock_errors ?? 0) === 0
                        && (lote.bling_stock_skipped ?? 0) === 0
                        && (lote.bling_stock_sent ?? 0) > 0,
                      'bg-red-50 text-red-700 border-red-300':
                        (lote.bling_stock_errors ?? 0) > 0,
                      'bg-amber-50 text-amber-700 border-amber-300':
                        (lote.bling_stock_errors ?? 0) === 0
                        && (lote.bling_stock_skipped ?? 0) > 0,
                    }"
                    :title="`Bling: ${lote.bling_stock_sent ?? 0} enviados, ${lote.bling_stock_skipped ?? 0} pulados, ${lote.bling_stock_errors ?? 0} erros`"
                  >
                    Bling
                    <template v-if="(lote.bling_stock_errors ?? 0) > 0">✗ {{ lote.bling_stock_errors }} erro<span v-if="(lote.bling_stock_errors ?? 0) > 1">s</span></template>
                    <template v-else-if="(lote.bling_stock_skipped ?? 0) > 0">⚠ {{ lote.bling_stock_skipped }} pulado<span v-if="(lote.bling_stock_skipped ?? 0) > 1">s</span></template>
                    <template v-else>✓ {{ lote.bling_stock_sent }} enviado<span v-if="(lote.bling_stock_sent ?? 0) > 1">s</span></template>
                  </span>
                </td>
              </template>
              <th :rowspan="isCelular ? 12 : 8" v-if="canEdit" class="col-head text-center">bling</th>
              <th :rowspan="isCelular ? 12 : 8" v-if="canDelete" class="col-head w-8"></th>
            </tr>
            <tr>
              <template v-for="lote in visibleLotes" :key="`lote-r2-${lote.id}`">
                <td class="lote-label border-l" :class="loteBgClass(lote.nome)">abertura</td>
                <td class="lote-value editable" :colspan="isCelular ? 2 : 1" :class="loteBgClass(lote.nome)">
                  <input type="date" :value="lote.abertura" :disabled="!canEdit"
                    class="w-full bg-transparent border-0 p-0 text-[11px]"
                    @input="(e) => schedulePatchLote(lote, 'abertura', (e.target as HTMLInputElement).value)" />
                </td>
              </template>
            </tr>
            <tr>
              <template v-for="lote in visibleLotes" :key="`lote-r3-${lote.id}`">
                <td class="lote-label border-l" :class="loteBgClass(lote.nome)">fechamento</td>
                <td class="lote-value editable" :colspan="isCelular ? 2 : 1" :class="loteBgClass(lote.nome)">
                  <input type="date" :value="lote.fechamento ?? ''" :disabled="!canEdit"
                    class="w-full bg-transparent border-0 p-0 text-[11px]"
                    @input="(e) => schedulePatchLote(lote, 'fechamento', (e.target as HTMLInputElement).value || null)" />
                </td>
              </template>
            </tr>
            <tr>
              <template v-for="lote in visibleLotes" :key="`lote-r4-${lote.id}`">
                <td class="lote-label border-l" :class="loteBgClass(lote.nome)">previsto</td>
                <td class="lote-value calculated" :colspan="isCelular ? 2 : 1" :class="loteBgClass(lote.nome)">{{ fmtMoney(lote.previsto) }}</td>
              </template>
            </tr>
            <tr>
              <template v-for="lote in visibleLotes" :key="`lote-r5-${lote.id}`">
                <td class="lote-label border-l" :class="loteBgClass(lote.nome)">realizado</td>
                <td class="lote-value editable" :colspan="isCelular ? 2 : 1" :class="loteBgClass(lote.nome)">
                  <input type="number" step="0.01" :value="lote.realizado" :disabled="!canEdit"
                    class="w-full bg-transparent border-0 p-0 text-[11px] text-right"
                    @input="(e) => schedulePatchLote(lote, 'realizado', Number((e.target as HTMLInputElement).value) || 0)" />
                </td>
              </template>
            </tr>
            <tr>
              <template v-for="lote in visibleLotes" :key="`lote-r6-${lote.id}`">
                <td class="lote-label border-l" :class="loteBgClass(lote.nome)">saldo</td>
                <td class="lote-value calculated" :colspan="isCelular ? 2 : 1"
                  :class="[loteBgClass(lote.nome), Number(lote.saldo) > 0 ? 'text-red-700' : 'text-emerald-700']">
                  {{ fmtMoney(lote.saldo) }}
                </td>
              </template>
            </tr>
            <tr>
              <template v-for="lote in visibleLotes" :key="`lote-r7-${lote.id}`">
                <td class="lote-label border-l" :class="loteBgClass(lote.nome)">prazo</td>
                <td class="lote-value calculated" :colspan="isCelular ? 2 : 1" :class="loteBgClass(lote.nome)">{{ lote.prazo != null ? lote.prazo + 'd' : '—' }}</td>
              </template>
            </tr>
            <!-- 4 rows extras só pra Celular (transportadora, taxa,
                 frete %, adicional). Mala ignora. -->
            <tr v-if="isCelular">
              <template v-for="lote in visibleLotes" :key="`lote-r8c-${lote.id}`">
                <td class="lote-label border-l" :class="loteBgClass(lote.nome)">transportadora</td>
                <td class="lote-value editable" colspan="2" :class="loteBgClass(lote.nome)">
                  <input type="text" :value="lote.transportadora ?? ''" :disabled="!canEdit"
                    class="w-full bg-transparent border-0 p-0 text-[11px]"
                    @input="(e) => schedulePatchLote(lote, 'transportadora', (e.target as HTMLInputElement).value || null)" />
                </td>
              </template>
            </tr>
            <tr v-if="isCelular">
              <template v-for="lote in visibleLotes" :key="`lote-r9c-${lote.id}`">
                <td class="lote-label border-l" :class="loteBgClass(lote.nome)">taxa</td>
                <td class="lote-value editable" colspan="2" :class="loteBgClass(lote.nome)">
                  <input inputmode="decimal" :value="lote.taxa == null ? '' : formatBRNumber(Number(lote.taxa), 4)" :disabled="!canEdit"
                    class="w-full bg-transparent border-0 p-0 text-[11px] text-right" placeholder="5,08"
                    @change="(e) => schedulePatchLote(lote, 'taxa', parseBRNumber((e.target as HTMLInputElement).value))" />
                </td>
              </template>
            </tr>
            <tr v-if="isCelular">
              <template v-for="lote in visibleLotes" :key="`lote-r10c-${lote.id}`">
                <td class="lote-label border-l" :class="loteBgClass(lote.nome)">frete %</td>
                <td class="lote-value editable" colspan="2" :class="loteBgClass(lote.nome)">
                  <input inputmode="decimal" :value="lote.frete_pct == null ? '' : formatPercent(Number(lote.frete_pct), 2)" :disabled="!canEdit"
                    class="w-full bg-transparent border-0 p-0 text-[11px] text-right" placeholder="14"
                    @change="(e) => onFretePctChange(lote, (e.target as HTMLInputElement).value)" />
                </td>
              </template>
            </tr>
            <tr v-if="isCelular">
              <template v-for="lote in visibleLotes" :key="`lote-r11c-${lote.id}`">
                <td class="lote-label border-l" :class="loteBgClass(lote.nome)">adicional</td>
                <td class="lote-value editable" colspan="2" :class="loteBgClass(lote.nome)">
                  <input inputmode="decimal" :value="lote.adicional == null ? '' : formatBRNumber(Number(lote.adicional), 2)" :disabled="!canEdit"
                    class="w-full bg-transparent border-0 p-0 text-[11px] text-right" placeholder="12,50"
                    @change="(e) => schedulePatchLote(lote, 'adicional', parseBRNumber((e.target as HTMLInputElement).value))" />
                </td>
              </template>
            </tr>
            <tr>
              <!-- Última row do thead = sub-headers que alinham com as
                   cells do body. Mala: quant | total. Celular: quant |
                   valor | custo (3 cols por lote). -->
              <template v-for="lote in visibleLotes" :key="`lote-rh-${lote.id}`">
                <th class="col-quant border-l" :class="loteBgClass(lote.nome)">quant</th>
                <template v-if="isCelular">
                  <th class="col-total" :class="loteBgClass(lote.nome)">valor</th>
                  <th class="col-total" :class="loteBgClass(lote.nome)">custo</th>
                </template>
                <th v-else class="col-total" :class="loteBgClass(lote.nome)">total</th>
              </template>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!loading && filteredProducts.length === 0">
              <td :colspan="(isEletro ? 9 : isCelular ? 10 : 9) + visibleLotes.length * (isCelular ? 3 : 2) + (canEdit ? 1 : 0) + (canDelete ? 1 : 0)" class="py-6 text-center text-muted-foreground">
                {{ categoria === 'celular'
                  ? 'Categoria Celular em construção. Clique em "Criar produto" pra começar.'
                  : 'Nenhum produto. Clique em "Criar produto" para começar.' }}
              </td>
            </tr>
            <tr v-for="row in filteredProducts" :key="row.id" class="even:bg-muted/10 hover:bg-amber-50/40">
              <td
                :class="isCelular ? 'sticky bg-background z-10' : ''"
                :style="isCelular ? { left: stickyLeftCelular('modelo_bling'), minWidth: '300px' } : { minWidth: '500px' }"
              >
                <input class="cell-input" :value="row.modelo_bling ?? ''" :disabled="!canEdit"
                  @input="(e) => scheduleSave(row, 'modelo_bling', (e.target as HTMLInputElement).value)" />
              </td>
              <td
                class="font-mono"
                :class="isCelular ? 'sticky bg-background z-10' : ''"
                :style="isCelular ? { left: stickyLeftCelular('sku'), minWidth: '130px' } : undefined"
              >
                <input class="cell-input" :value="row.sku ?? ''" :disabled="!canEdit"
                  @input="(e) => scheduleSave(row, 'sku', (e.target as HTMLInputElement).value)" />
              </td>
              <td
                :class="isCelular ? 'sticky bg-background z-10' : ''"
                :style="isCelular ? { left: stickyLeftCelular('custo_bling'), minWidth: '60px' } : undefined"
              >
                <input type="number" step="0.01" class="cell-input text-right" :value="row.custo_bling" :disabled="!canEdit"
                  @input="(e) => scheduleSave(row, 'custo_bling', Number((e.target as HTMLInputElement).value) || 0)" />
              </td>
              <!-- estoque_bling auto-pulled from products.stock by SKU in
                   the router; consumo_diario = bling_orders last 30d / 30.
                   Both read-only — the operator can't override the source. -->
              <td
                class="calc text-right"
                :class="isCelular ? 'sticky bg-background z-10' : ''"
                :style="isCelular ? { left: stickyLeftCelular('estoque_bling'), minWidth: '66px' } : undefined"
                :title="'Auto: products.stock por SKU'"
              >
                {{ row.estoque_bling ?? '—' }}
              </td>
              <td
                class="calc text-right"
                :class="isCelular ? 'sticky bg-background z-10' : ''"
                :style="isCelular ? { left: stickyLeftCelular('consumo_diario'), minWidth: '66px' } : undefined"
                :title="'Auto: bling_orders 30d ÷ 30'"
              >
                {{ fmtNum2(row.consumo_diario) }}
              </td>
              <td
                class="calc text-right"
                :class="isCelular ? 'sticky bg-background z-10' : ''"
                :style="isCelular ? { left: stickyLeftCelular('memoria_consumo'), minWidth: '70px' } : undefined"
              >{{ fmtNum2(row.memoria_consumo) }}</td>
              <td
                class="calc text-right"
                :class="[reposicaoClass(row.reposicao_estoque), isCelular ? 'sticky bg-background z-10' : '']"
                :style="isCelular ? { left: stickyLeftCelular('reposicao_estoque'), minWidth: '76px' } : undefined"
              >
                {{ row.reposicao_estoque ?? '—' }}
              </td>
              <td
                class="calc text-right"
                :class="[reposicaoClass(row.saldo_reposicao), isCelular ? 'sticky bg-background z-10' : '']"
                :style="isCelular ? { left: stickyLeftCelular('saldo_reposicao'), minWidth: '76px' } : undefined"
              >
                {{ row.saldo_reposicao ?? '—' }}
              </td>
              <!-- obs só aparece em Mala/Eletro. -->
              <td v-if="!isCelular"><input class="cell-input" :value="row.obs ?? ''" :disabled="!canEdit"
                @input="(e) => scheduleSave(row, 'obs', (e.target as HTMLInputElement).value)" /></td>
              <!-- Celular: custo_realizado (read-only, computed pelo
                   backend como média ponderada por qty do custoBRL
                   dos lotes onde o produto aparece). Atualiza quando
                   operador edita valor_usd ou params do lote → próximo
                   loadProductsOnly traz o novo valor. -->
              <td
                v-if="isCelular"
                class="calc text-right sticky bg-background z-10"
                :style="{ left: stickyLeftCelular('custo_realizado'), minWidth: '90px' }"
              >
                {{ fmtMoney(row.custo_realizado) }}
              </td>
              <!-- Per-lote cells align directly under the last-row
                   sub-headers. Mala: 2 cells (quant + total). Celular:
                   3 cells (quant + valor USD editável + custo BRL
                   computed via custoBRL(prod, lote)). -->
              <template v-for="lote in visibleLotes" :key="`cell-${row.id}-${lote.id}`">
                <td class="border-l" :class="loteBgClass(lote.nome)">
                  <input
                    type="number"
                    class="cell-input text-right"
                    :value="row.lote_quantidades[lote.id] ?? ''"
                    :disabled="!canEdit"
                    @input="(e) => scheduleLoteItem(row, lote.id, Number((e.target as HTMLInputElement).value) || 0)"
                  />
                  <!-- Dropdown destino do estoque no Bling. Em Celular,
                       sempre visível enquanto houver item_id e qty > 0
                       (mesmo com lote fechado — permite o operador
                       corrigir um SKU que foi pulado/errou e
                       reprocessar automaticamente). Default = SKU do
                       produto; operador escolhe se quiser redirecionar
                       (ex.: i203.sa → i203.sp). -->
                  <select
                    v-if="isCelular
                      && row.lote_item_ids?.[lote.id]
                      && (row.lote_quantidades[lote.id] || 0) > 0"
                    class="mt-0.5 w-full text-[10px] border rounded px-1 py-0.5 bg-white"
                    :value="row.lote_target_skus?.[lote.id] || row.sku"
                    :disabled="!canEdit"
                    title="Destino da entrada de estoque no Bling"
                    @focus="() => loadSkuVariants(row.lote_item_ids![lote.id])"
                    @change="(e) => setLoteItemTargetSku(row, lote.id, row.lote_item_ids![lote.id], (e.target as HTMLSelectElement).value)"
                  >
                    <option v-for="sku in loteItemSkuOptions(row, lote.id)" :key="sku" :value="sku">{{ sku }}</option>
                  </select>
                </td>
                <template v-if="isCelular">
                  <td :class="loteBgClass(lote.nome)">
                    <input
                      type="number" step="0.01" class="cell-input text-right"
                      :value="row.lote_valores_usd?.[lote.id] ?? ''"
                      :disabled="!canEdit"
                      @change="(e) => scheduleLoteItemValor(row, lote.id, (e.target as HTMLInputElement).value)"
                    />
                  </td>
                  <!-- Lote sem taxa/frete (ex: I48) → custo BRL é
                       digitado manual. Senão → calculado via fórmula. -->
                  <td
                    v-if="lote.taxa != null && lote.frete_pct != null"
                    class="calc text-right"
                    :class="loteBgClass(lote.nome)"
                  >
                    {{ fmtMoney(custoBRL(row, lote)) }}
                  </td>
                  <td v-else :class="loteBgClass(lote.nome)">
                    <input
                      inputmode="decimal"
                      class="cell-input text-right" placeholder="0,00"
                      :value="row.lote_custos_manuais?.[lote.id] == null ? '' : formatBRNumber(Number(row.lote_custos_manuais?.[lote.id]), 2)"
                      :disabled="!canEdit"
                      @change="(e) => scheduleLoteItemCustoManual(row, lote.id, (e.target as HTMLInputElement).value)"
                    />
                  </td>
                </template>
                <td v-else class="calc text-right" :class="loteBgClass(lote.nome)">{{ fmtMoney(loteTotal(row, lote.id)) }}</td>
              </template>
              <td v-if="canEdit" class="text-center whitespace-nowrap">
                <button
                  class="inline-flex items-center gap-1 rounded border px-2 py-0.5 text-[10px] hover:bg-muted"
                  :class="{
                    'bg-amber-50 text-amber-700 border-amber-300': row.bling_sync_status === 'pending',
                    'bg-emerald-50 text-emerald-700 border-emerald-300': row.bling_sync_status === 'sent',
                    'bg-red-50 text-red-700 border-red-300': row.bling_sync_status === 'error',
                  }"
                  :title="row.bling_sync_status === 'sent'
                    ? `Bling id ${row.bling_product_id ?? '?'} (enviado em ${row.bling_sync_done_at?.slice(0, 16) ?? ''})`
                    : row.bling_sync_status === 'pending'
                      ? 'Aguardando worker criar no Bling…'
                      : row.bling_sync_status === 'error'
                        ? `Erro: ${row.bling_sync_error ?? 'desconhecido'} — clique pra reenviar`
                        : `Criar produto no Bling (categoria ${categoria})`"
                  @click="sendToBling(row)"
                >
                  <Clock v-if="row.bling_sync_status === 'pending'" class="size-3" />
                  <CheckCircle2 v-else-if="row.bling_sync_status === 'sent'" class="size-3" />
                  <Send v-else class="size-3" />
                  <span>{{
                    row.bling_sync_status === 'pending' ? 'Pendente'
                    : row.bling_sync_status === 'sent' ? 'Enviado'
                    : row.bling_sync_status === 'error' ? 'Erro ↻'
                    : 'Enviar'
                  }}</span>
                </button>
              </td>
              <td v-if="canDelete" class="text-center">
                <button class="text-muted-foreground hover:text-destructive" @click="removeProduct(row)" :title="`Excluir ${row.sku}`">
                  <Trash2 class="size-3" />
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- ─── TAB RESUMO ───────────────────────────────────────────── -->
    <div v-if="tab === 'resumo'" class="space-y-2">
      <div class="flex items-center gap-2">
        <button v-if="canEdit" class="inline-flex items-center gap-1 rounded-md bg-primary text-primary-foreground px-3 py-1 text-sm hover:opacity-90"
          @click="addingResumo = !addingResumo">
          <Plus class="size-3" /> Incluir lançamento
        </button>
        <span class="text-xs text-muted-foreground">{{ resumo.items.length }} lançamento(s)</span>
      </div>

      <div v-if="addingResumo" class="border rounded-md p-3 bg-muted/20 space-y-2 max-w-xl">
        <div class="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
          <label>Data <input v-model="newResumo.data" type="date" class="w-full h-7 border rounded px-2 bg-background" /></label>
          <label>Lote (livre) <input v-model="newResumo.lote_nome" type="text" placeholder="ml25" class="w-full h-7 border rounded px-2 bg-background" /></label>
          <label>Saldo (R$) <input v-model="newResumo.saldo" type="number" step="0.01" class="w-full h-7 border rounded px-2 bg-background text-right" /></label>
          <label>Obs <input v-model="newResumo.obs" type="text" class="w-full h-7 border rounded px-2 bg-background" /></label>
        </div>
        <div class="flex gap-2">
          <button class="rounded-md bg-primary text-primary-foreground px-3 py-1 text-sm" @click="addResumo">Salvar</button>
          <button class="rounded-md border px-3 py-1 text-sm" @click="addingResumo = false">Cancelar</button>
        </div>
      </div>

      <div class="border rounded-md overflow-x-auto">
        <table class="grid-table w-full text-xs border-collapse">
          <thead>
            <tr class="bg-emerald-800 text-white text-[10px] uppercase tracking-wide">
              <th class="text-left w-32">Data</th>
              <th class="text-left w-28">Lote</th>
              <th class="text-right w-40">Saldo (R$)</th>
              <th class="text-left">Obs</th>
              <th v-if="canDelete" class="w-8"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in resumo.items" :key="row.id" class="even:bg-muted/10">
              <td>{{ row.data }}</td>
              <td class="font-mono">{{ row.lote_nome || '—' }}</td>
              <td class="text-right" :class="Number(row.saldo) < 0 ? 'text-emerald-700 font-semibold' : ''">
                {{ fmtMoney(row.saldo) }}
              </td>
              <td>{{ row.obs || '' }}</td>
              <td v-if="canDelete" class="text-center">
                <button class="text-muted-foreground hover:text-destructive" @click="removeResumo(row)">
                  <Trash2 class="size-3" />
                </button>
              </td>
            </tr>
          </tbody>
          <tfoot class="bg-muted/40 font-semibold">
            <tr>
              <td colspan="2" class="text-right">TOTAL</td>
              <td class="text-right">{{ fmtMoney(resumo.total) }}</td>
              <td :colspan="canDelete ? 2 : 1"></td>
            </tr>
          </tfoot>
        </table>
      </div>
    </div>

    <!-- ─── TAB COTAÇÃO ──────────────────────────────────────────
         Tabela INDEPENDENTE — não puxa de import_products, sem fórmulas.
         Cabeçalho com 6 linhas por fabricante:
           1) nome do fabricante (colspan=3 + botão excluir)
           2..5) obs1..obs4 (colspan=3 cada, editáveis)
           6) sub-cabeçalho capacidade | R$ | USD
         Coluna fixa de produto à esquerda (rowspan=6). Per-célula upsert
         direto (PUT /cotacao/valores) — célula vazia em todos os 3 campos
         é deletada server-side. -->
    <div v-if="tab === 'cotacao' && categoria !== 'celular'" class="space-y-2">
      <div class="flex flex-wrap items-center gap-2 bg-muted/30 border rounded-md px-3 py-2 text-xs">
        <span class="text-muted-foreground">
          {{ cotacao.produtos.length }} produto(s) · {{ cotacao.fabricantes.length }} fabricante(s)
        </span>
        <button
          v-if="canEdit"
          class="ml-auto inline-flex items-center gap-1 rounded-md bg-primary text-primary-foreground px-2.5 py-1 hover:opacity-90 disabled:opacity-50"
          :disabled="addingCotProd"
          @click="addCotProduto"
        >
          <Plus class="size-3" /> Incluir Produto
        </button>
        <button
          v-if="canEdit"
          class="inline-flex items-center gap-1 rounded-md border px-2.5 py-1 hover:bg-muted disabled:opacity-50"
          :disabled="addingCotFab"
          @click="addCotFabricante"
        >
          <Plus class="size-3" /> Criar Fabricante
        </button>
      </div>

      <div class="border rounded-md overflow-x-auto">
        <table class="cot-table text-xs border-collapse">
          <thead>
            <!-- Row 1: nome do fabricante -->
            <tr>
              <th rowspan="6" class="cot-prod-head text-left">Produto</th>
              <template v-for="fab in cotacao.fabricantes" :key="`fab-head-${fab.id}`">
                <th colspan="3" class="cot-fab-name border-l">
                  <div class="flex items-center gap-1">
                    <input
                      class="cot-input font-semibold uppercase text-center flex-1"
                      :value="fab.nome"
                      :disabled="!canEdit"
                      placeholder="nome do fabricante"
                      @input="(e) => scheduleCotFab(fab, 'nome', (e.target as HTMLInputElement).value)"
                    />
                    <button
                      v-if="canDelete"
                      class="text-muted-foreground hover:text-destructive shrink-0"
                      :title="`Excluir fabricante ${fab.nome}`"
                      @click="removeCotFabricante(fab)"
                    >
                      <Trash2 class="size-3" />
                    </button>
                  </div>
                </th>
              </template>
              <th v-if="canDelete" rowspan="6" class="cot-actions-head w-8"></th>
            </tr>
            <!-- Rows 2..5: obs1..obs4 -->
            <tr v-for="i in 4" :key="`obs-row-${i}`">
              <template v-for="fab in cotacao.fabricantes" :key="`fab-obs${i}-${fab.id}`">
                <td colspan="3" class="cot-fab-obs border-l">
                  <input
                    class="cot-input"
                    :value="(fab as any)[`obs${i}`] ?? ''"
                    :disabled="!canEdit"
                    :placeholder="`obs ${i}`"
                    @input="(e) => scheduleCotFab(fab, (`obs${i}` as keyof CotFabricante), (e.target as HTMLInputElement).value)"
                  />
                </td>
              </template>
            </tr>
            <!-- Row 6: sub-headers -->
            <tr>
              <template v-for="fab in cotacao.fabricantes" :key="`fab-subh-${fab.id}`">
                <th class="cot-sub-head border-l">capacidade</th>
                <th class="cot-sub-head">R$</th>
                <th class="cot-sub-head">USD</th>
              </template>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!loading && cotacao.produtos.length === 0">
              <td :colspan="1 + cotacao.fabricantes.length * 3 + (canDelete ? 1 : 0)" class="py-6 text-center text-muted-foreground">
                Nenhum produto. Clique em "Incluir Produto" para começar.
              </td>
            </tr>
            <tr v-for="prod in cotacao.produtos" :key="prod.id" class="even:bg-muted/10 hover:bg-amber-50/40">
              <td class="cot-prod-cell">
                <input
                  class="cot-input"
                  :value="prod.nome"
                  :disabled="!canEdit"
                  placeholder="nome do produto"
                  @input="(e) => scheduleCotProduto(prod, (e.target as HTMLInputElement).value)"
                />
              </td>
              <template v-for="fab in cotacao.fabricantes" :key="`cell-${prod.id}-${fab.id}`">
                <td class="border-l">
                  <input
                    class="cot-input"
                    :value="getCotCell(prod.id, fab.id).capacidade"
                    :disabled="!canEdit"
                    @input="(e) => scheduleCotCell(prod.id, fab.id, 'capacidade', (e.target as HTMLInputElement).value)"
                  />
                </td>
                <td>
                  <input
                    type="number"
                    step="0.01"
                    class="cot-input text-right"
                    :value="getCotCell(prod.id, fab.id).valor_real"
                    :disabled="!canEdit"
                    @input="(e) => scheduleCotCell(prod.id, fab.id, 'valor_real', (e.target as HTMLInputElement).value)"
                  />
                </td>
                <td>
                  <input
                    type="number"
                    step="0.01"
                    class="cot-input text-right"
                    :value="getCotCell(prod.id, fab.id).valor_usd"
                    :disabled="!canEdit"
                    @input="(e) => scheduleCotCell(prod.id, fab.id, 'valor_usd', (e.target as HTMLInputElement).value)"
                  />
                </td>
              </template>
              <td v-if="canDelete" class="text-center">
                <button class="text-muted-foreground hover:text-destructive" @click="removeCotProduto(prod)" :title="`Excluir ${prod.nome || 'produto'}`">
                  <Trash2 class="size-3" />
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- ─── TAB COTAÇÃO (Celular) ────────────────────────────────
         UI distinta da Cotação de Mala (matriz fabricantes×produtos).
         Pra celular, calcula previsto = usd*(1+frete)*câmbio + adic
         em tempo real, sem persistir. Params globais editáveis no
         topo + tabela 115 produtos com 3 inputs por linha. -->
    <div v-if="tab === 'cotacao' && categoria === 'celular'" class="space-y-3">
      <div class="border rounded-md p-3 bg-muted/30 text-xs">
        <div class="text-[10px] uppercase tracking-wide text-muted-foreground mb-2 font-semibold">
          Parâmetros da fórmula (salvam ao sair do campo)
        </div>
        <div class="grid grid-cols-2 md:grid-cols-5 gap-3">
          <label class="flex flex-col gap-1">
            <span class="text-[10px] text-muted-foreground">Taxa USD→BRL</span>
            <input
              type="number" step="0.0001" min="0"
              class="h-7 border rounded px-2 bg-background text-right"
              :value="cotacaoParams.taxa_cambio" :disabled="!canEdit"
              @change="(e) => scheduleSaveCotacaoParam('taxa_cambio', (e.target as HTMLInputElement).value)"
            />
          </label>
          <label class="flex flex-col gap-1">
            <span class="text-[10px] text-muted-foreground">Adicional (R$)</span>
            <input
              type="number" step="0.01" min="0"
              class="h-7 border rounded px-2 bg-background text-right"
              :value="cotacaoParams.adicional" :disabled="!canEdit"
              @change="(e) => scheduleSaveCotacaoParam('adicional', (e.target as HTMLInputElement).value)"
            />
          </label>
          <label class="flex flex-col gap-1">
            <span class="text-[10px] text-muted-foreground">Frete regular (0–1)</span>
            <input
              type="number" step="0.0001" min="0" max="1"
              class="h-7 border rounded px-2 bg-background text-right"
              :value="cotacaoParams.frete_regular_pct" :disabled="!canEdit"
              @change="(e) => scheduleSaveCotacaoParam('frete_regular_pct', (e.target as HTMLInputElement).value)"
            />
          </label>
          <label class="flex flex-col gap-1">
            <span class="text-[10px] text-muted-foreground">Frete swap (0–1)</span>
            <input
              type="number" step="0.0001" min="0" max="1"
              class="h-7 border rounded px-2 bg-background text-right"
              :value="cotacaoParams.frete_swap_pct" :disabled="!canEdit"
              @change="(e) => scheduleSaveCotacaoParam('frete_swap_pct', (e.target as HTMLInputElement).value)"
            />
          </label>
          <label class="flex flex-col gap-1">
            <span class="text-[10px] text-muted-foreground">Frete acessórios (0–1)</span>
            <input
              type="number" step="0.0001" min="0" max="1"
              class="h-7 border rounded px-2 bg-background text-right"
              :value="cotacaoParams.frete_acessorios_pct" :disabled="!canEdit"
              @change="(e) => scheduleSaveCotacaoParam('frete_acessorios_pct', (e.target as HTMLInputElement).value)"
            />
          </label>
        </div>
      </div>

      <div class="border rounded-md overflow-auto" style="max-height: calc(100vh - 320px)">
        <table class="grid-table w-full text-xs border-collapse">
          <thead class="thead-sticky">
            <tr class="bg-emerald-800 text-white text-[10px] uppercase tracking-wide">
              <th class="text-left">Produto</th>
              <th class="text-left" style="width: 130px">SKU</th>
              <th class="text-right" style="width: 120px">Valor (R$)</th>
              <th class="text-right" style="width: 100px">USD</th>
              <th class="text-left" style="width: 130px">Tipo Frete</th>
              <th class="text-right" style="width: 130px">Previsto (R$)</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!loading && filteredProducts.length === 0">
              <td colspan="6" class="py-6 text-center text-muted-foreground">
                Nenhum produto na categoria celular.
              </td>
            </tr>
            <tr v-for="prod in filteredProducts" :key="prod.id" class="even:bg-muted/10">
              <td class="font-medium">{{ prod.modelo_bling || '—' }}</td>
              <td class="text-[10px] text-muted-foreground font-mono">{{ prod.sku }}</td>
              <td>
                <input
                  type="number" step="0.01" class="cell-input text-right"
                  :value="prod.valor_brl_realizado ?? ''" :disabled="!canEdit"
                  @change="(e) => scheduleSaveCotacaoProduto(prod, 'valor_brl_realizado', (e.target as HTMLInputElement).value)"
                />
              </td>
              <td>
                <input
                  type="number" step="0.01" class="cell-input text-right"
                  :value="prod.valor_usd ?? ''" :disabled="!canEdit"
                  @change="(e) => scheduleSaveCotacaoProduto(prod, 'valor_usd', (e.target as HTMLInputElement).value)"
                />
              </td>
              <td>
                <select
                  class="cell-input"
                  :value="prod.frete_type ?? 'regular'" :disabled="!canEdit"
                  @change="(e) => scheduleSaveCotacaoProduto(prod, 'frete_type', (e.target as HTMLSelectElement).value)"
                >
                  <option value="regular">Regular</option>
                  <option value="swap">Swap</option>
                  <option value="acessorios">Acessórios</option>
                </select>
              </td>
              <td class="text-right font-semibold">
                {{ fmtMoney(calcularPrevisto(prod)) }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- ─── TAB KIT ──────────────────────────────────────────────────
         Matriz produto × variação de kit. Seed fixo via migration
         0099 — operador apenas toggle de "x". Integração com Bling /
         Tabela de Preços fica pras fases 2/3. -->
    <div v-if="tab === 'kit'" class="space-y-2">
      <div class="flex flex-wrap items-center gap-2 bg-muted/30 border rounded-md px-3 py-2 text-xs">
        <span class="text-muted-foreground">
          Matriz <strong>{{ kit.bases.length }}</strong> produtos × <strong>{{ kit.variations.length }}</strong> variações.
          Clique pra marcar/desmarcar "x". Criação automática no Bling
          (<em>categoria {{ categoria }} kit</em>)<template v-if="categoria === 'mala'"> e item na Tabela de Preços ficam pra fase 2</template>.
        </span>
        <!-- "Criar Kit" — botão separado por categoria (cada um abre
             seu próprio modal com regras específicas). Eletro não tem
             aba kit, então só aparece em mala/celular. -->
        <button
          v-if="canEdit && categoria === 'celular'"
          class="ml-auto inline-flex items-center gap-1 rounded-md bg-primary text-primary-foreground px-2.5 py-1 hover:opacity-90"
          @click="createKitCelularOpen = true"
        >
          <Plus class="size-3" /> Criar Kit
        </button>
        <button
          v-if="canEdit && categoria === 'mala'"
          class="ml-auto inline-flex items-center gap-1 rounded-md bg-primary text-primary-foreground px-2.5 py-1 hover:opacity-90"
          @click="createKitMalaOpen = true"
        >
          <Plus class="size-3" /> Criar Kit
        </button>
      </div>

      <CreateKitVariationCelularModal
        :open="createKitCelularOpen"
        @close="createKitCelularOpen = false"
        @created="onKitVariationCreated"
      />
      <CreateKitVariationMalaModal
        :open="createKitMalaOpen"
        @close="createKitMalaOpen = false"
        @created="onKitVariationCreated"
      />

      <div class="border rounded-md overflow-auto" style="max-height: calc(100vh - 220px)">
        <table class="kit-table text-xs border-collapse">
          <thead>
            <tr>
              <th class="kit-h kit-col-modelo">modelo</th>
              <th class="kit-h kit-col-sku">sku</th>
              <!-- Celular: cor já está embutida no modelo_bling
                   ("Apple iPad 11 128 GB - Amarelo"); coluna omitida. -->
              <th v-if="!isCelular" class="kit-h kit-col-cor">cor</th>
              <th
                v-for="v in kit.variations" :key="v.id"
                class="kit-h kit-h-var"
                :class="{ 'kit-h-highlight': v.highlight }"
                :title="v.obs || `Variação #${v.ordem}`"
              >
                {{ v.label }}
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!loading && kit.bases.length === 0">
              <td :colspan="(isCelular ? 2 : 3) + kit.variations.length" class="py-6 text-center text-muted-foreground">
                Sem dados — verifique se a migration de seed (0099) rodou.
              </td>
            </tr>
            <tr v-for="b in kit.bases" :key="b.id" class="even:bg-muted/10 hover:bg-amber-50/40">
              <td class="kit-col-modelo">{{ b.modelo_bling ?? '—' }}</td>
              <td class="kit-col-sku font-mono">{{ b.sku_base }}</td>
              <td v-if="!isCelular" class="kit-col-cor">{{ b.cor ?? '—' }}</td>
              <td
                v-for="v in kit.variations" :key="`${b.id}-${v.id}`"
                class="kit-cell"
                :class="{ 'kit-cell-highlight': v.highlight, 'kit-cell-disabled': !canEdit }"
                :title="kitMarkTitle(getKitMark(b.id, v.id))"
                @click="canEdit && toggleKitMark(b.id, v.id)"
              >
                <template v-if="isKitMarked(b.id, v.id)">
                  <span class="kit-mark" :class="kitMarkColorClass(getKitMark(b.id, v.id))">x</span>
                  <!-- Resync (Bling) — visível se Bling status='error'. -->
                  <button
                    v-if="canEdit && getKitMark(b.id, v.id)?.bling_sync_status === 'error'"
                    class="kit-resync"
                    title="Reenviar tudo (Bling + Pricing)"
                    @click.stop="resyncKitMark(getKitMark(b.id, v.id)!)"
                  >↻</button>
                  <!-- Resync só pricing — Bling ok mas pricing falhou. -->
                  <button
                    v-else-if="canEdit
                      && getKitMark(b.id, v.id)?.bling_sync_status === 'sent'
                      && getKitMark(b.id, v.id)?.pricing_sync_status === 'error'"
                    class="kit-resync"
                    title="Reenviar pricing (Bling já criado)"
                    @click.stop="resyncKitPricing(getKitMark(b.id, v.id)!)"
                  >↻ $</button>
                </template>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- ─── MODAL: Criar Produto ────────────────────────────────────
         Backed by saveNewProduct() — POSTs to /api/importacao/products.
         The "Nome gerado (preview)" line uses generateMalaName() which
         mirrors backend's generate_mala_name(). Both must stay in sync. -->
    <div v-if="showCreateModal" class="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" @click.self="showCreateModal = false">
      <div class="bg-background rounded-lg shadow-xl w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        <div class="flex items-center justify-between border-b px-4 py-3">
          <h3 class="font-semibold">Criar produto</h3>
          <button class="text-muted-foreground hover:text-foreground" @click="showCreateModal = false">
            <X class="size-4" />
          </button>
        </div>
        <div class="p-4 space-y-4 text-sm">
          <!-- Live preview of the canonical mala name. -->
          <div class="rounded-md border border-amber-300 bg-amber-50 dark:bg-amber-900/20 px-3 py-2">
            <div class="text-[10px] font-semibold uppercase tracking-wide text-amber-700 dark:text-amber-300">
              Nome gerado (preview)
            </div>
            <div class="font-mono text-sm">{{ newProductPreviewName }}</div>
            <div class="text-[10px] text-muted-foreground mt-1">
              <template v-if="isEletro">Eletro: nome = <code>{{ '{' }}modelo bling{{ '}' }}</code></template>
              <template v-else>Padrão: <code>Mala {{ '{' }}modelo bling{{ '}' }} tamanho {{ '{' }}n após o ponto no SKU{{ '}' }} - {{ '{' }}cor{{ '}' }}</code></template>
            </div>
          </div>

          <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
            <label class="flex flex-col gap-1">
              <span class="text-xs font-medium text-muted-foreground">SKU <span class="text-red-600">*</span></span>
              <input v-model="newProduct.sku" type="text" placeholder="b042.28" class="h-8 border rounded px-2 bg-background font-mono" />
            </label>
            <label class="flex flex-col gap-1">
              <span class="text-xs font-medium text-muted-foreground">Modelo bling</span>
              <input v-model="newProduct.modelo_bling" type="text" placeholder="Lisa M2" class="h-8 border rounded px-2 bg-background" />
            </label>
            <label v-if="!isEletro" class="flex flex-col gap-1">
              <span class="text-xs font-medium text-muted-foreground">Cor</span>
              <input v-model="newProduct.cor" type="text" placeholder="Roxo Escuro" class="h-8 border rounded px-2 bg-background" />
            </label>
            <label class="flex flex-col gap-1">
              <span class="text-xs font-medium text-muted-foreground">Custo bling (R$)</span>
              <input v-model="newProduct.custo_bling" type="number" step="0.01" min="0" placeholder="0,00" class="h-8 border rounded px-2 bg-background text-right" />
            </label>
            <label class="flex flex-col gap-1">
              <span class="text-xs font-medium text-muted-foreground">Fornecedor</span>
              <input v-model="newProduct.fornecedor" type="text" class="h-8 border rounded px-2 bg-background" />
            </label>
            <label v-if="!isEletro" class="flex flex-col gap-1">
              <span class="text-xs font-medium text-muted-foreground">Modelo china</span>
              <input v-model="newProduct.modelo_china" type="text" class="h-8 border rounded px-2 bg-background" />
            </label>
            <label v-if="!isEletro" class="flex flex-col gap-1">
              <span class="text-xs font-medium text-muted-foreground">Cor china</span>
              <input v-model="newProduct.cor_china" type="text" class="h-8 border rounded px-2 bg-background" />
            </label>
            <label v-if="!isEletro" class="flex flex-col gap-1">
              <span class="text-xs font-medium text-muted-foreground">TSA (1, 2 ou 3)</span>
              <input v-model="newProduct.tsa" type="number" min="1" max="3" step="1" placeholder="—" class="h-8 border rounded px-2 bg-background text-right" />
            </label>
            <label class="flex flex-col gap-1 md:col-span-2">
              <span class="text-xs font-medium text-muted-foreground">Observação</span>
              <textarea v-model="newProduct.obs" rows="2" class="border rounded px-2 py-1 bg-background"></textarea>
            </label>
          </div>

          <!-- Metadados Bling — fixos pela regra de negócio (planilha-mãe). -->
          <div class="rounded-md border bg-muted/30 px-3 py-2 text-xs space-y-1">
            <div class="font-semibold">Metadados Bling (fixos)</div>
            <div>categoria: <code>{{ categoria }}</code> · formato: <code>Simples</code></div>
            <div class="text-muted-foreground">
              Ao clicar "Enviar pro Bling" o produto é criado no Bling
              (formato Simples, categoria <code>{{ categoria }}</code>) e fica
              disponível como componente para kits. Preço de venda continua
              manual no Bling.
            </div>
          </div>
        </div>
        <div class="flex items-center justify-end gap-2 border-t px-4 py-3 bg-muted/20">
          <button class="rounded-md border px-3 py-1.5 text-sm hover:bg-muted" @click="showCreateModal = false">
            Cancelar
          </button>
          <button
            class="inline-flex items-center gap-1.5 rounded-md bg-primary text-primary-foreground px-3 py-1.5 text-sm hover:opacity-90 disabled:opacity-50"
            :disabled="creatingProduct || !newProduct.sku.trim()"
            @click="saveNewProduct"
          >
            <Save class="size-3.5" />
            {{ creatingProduct ? 'Salvando…' : 'Salvar' }}
          </button>
        </div>
      </div>
    </div>

    <!-- ─── TAB FRETE (Celular, etapa 4) ─────────────────────────
         Agrega items dos lotes + ajustes manuais. `valor_unit` e
         `frete_pct` derivam de ImportProduct (aba Cotação) — não há
         input por linha aqui exceto o checkbox `pago`. -->
    <div v-if="tab === 'frete' && categoria === 'celular'" class="space-y-3">
      <!-- Cards de resumo no topo. -->
      <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div class="border rounded-md p-3 bg-amber-50 dark:bg-amber-900/20">
          <div class="text-[10px] uppercase tracking-wide text-amber-700 dark:text-amber-300 font-semibold">
            Total a entregar (US$)
          </div>
          <div class="text-lg font-semibold mt-1">{{ fmtUsd(frete.total_a_entregar) }}</div>
          <div class="text-[10px] text-muted-foreground">lotes ainda sem fechamento</div>
        </div>
        <div class="border rounded-md p-3 bg-red-50 dark:bg-red-900/20">
          <div class="text-[10px] uppercase tracking-wide text-red-700 dark:text-red-300 font-semibold">
            Saldo a pagar (US$)
          </div>
          <div class="text-lg font-semibold mt-1">{{ fmtUsd(frete.saldo_a_pagar) }}</div>
          <div class="text-[10px] text-muted-foreground">lotes fechados + ajustes, ainda não pagos</div>
        </div>
      </div>

      <!-- Filtros + botão de ajuste. -->
      <div class="flex flex-wrap items-center gap-2 bg-muted/30 border rounded-md px-3 py-2 text-xs">
        <label class="inline-flex items-center gap-1">
          <span class="text-muted-foreground">Transportadora:</span>
          <select
            class="h-7 border rounded px-2 bg-background"
            v-model="freteFiltroTransp" @change="reloadFrete"
          >
            <option value="">— todas —</option>
            <option v-for="t in frete.transportadoras" :key="t" :value="t">{{ t }}</option>
          </select>
        </label>
        <label class="inline-flex items-center gap-1 ml-2">
          <input type="checkbox" v-model="freteOcultaPagos" @change="reloadFrete" />
          <span>oculta pagos</span>
        </label>
        <button
          v-if="canEdit"
          class="ml-auto inline-flex items-center gap-1 rounded-md bg-primary text-primary-foreground px-2.5 py-1 hover:opacity-90"
          @click="openCreateAjuste"
        >
          <Plus class="size-3" /> Ajuste manual
        </button>
      </div>

      <div class="border rounded-md overflow-auto" style="max-height: calc(100vh - 360px)">
        <table class="grid-table w-full text-xs border-collapse">
          <thead class="thead-sticky">
            <tr class="bg-emerald-800 text-white text-[10px] uppercase tracking-wide">
              <th class="text-left">Transportadora</th>
              <th class="text-left">Lote</th>
              <th class="text-left">Abertura</th>
              <th class="text-left">Fechamento</th>
              <th class="text-left">Modelo</th>
              <th class="text-right">Qtd</th>
              <th class="text-right">Valor Unit. (US$)</th>
              <th class="text-right">Total (US$)</th>
              <th class="text-right">Frete %</th>
              <th class="text-right">Saldo (US$)</th>
              <th class="text-center">Pago</th>
              <th class="text-center">Ações</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!loading && frete.rows.length === 0">
              <td colspan="12" class="py-6 text-center text-muted-foreground">
                Nenhum item — crie um lote (aba Importação) ou um ajuste manual.
              </td>
            </tr>
            <tr v-for="r in frete.rows" :key="`${r.kind}-${r.id}`"
              :class="r.kind === 'ajuste' ? 'bg-amber-50/40 dark:bg-amber-900/10' : 'even:bg-muted/10'">
              <td>{{ r.transportadora || '—' }}</td>
              <td>{{ r.lote_nome || (r.kind === 'ajuste' ? '(ajuste)' : '—') }}</td>
              <td>{{ r.abertura || '—' }}</td>
              <td>{{ r.fechamento || (r.kind === 'item' ? 'Pendente' : '—') }}</td>
              <td>
                <span v-if="r.modelo_bling">{{ r.modelo_bling }}</span>
                <span v-else class="italic text-muted-foreground">{{ r.obs || '(sem modelo)' }}</span>
              </td>
              <td class="text-right">{{ r.quantidade ?? '—' }}</td>
              <td class="text-right">{{ r.valor_unit == null ? '—' : fmtUsd(r.valor_unit) }}</td>
              <td class="text-right">{{ r.total == null ? '—' : fmtUsd(r.total) }}</td>
              <td class="text-right">
                <span v-if="r.frete_pct != null">{{ (Number(r.frete_pct) * 100).toFixed(1) }}%</span>
                <span v-else>—</span>
              </td>
              <!-- Saldo aparece como projeção (lote aberto) ou débito
                   (fechado + !pago). Vermelho bold SÓ pra dívida real
                   — abertos e pagos ficam em cor neutra pra não dar
                   falsa impressão de débito. -->
              <td
                :class="r.saldo != null && r.fechamento != null && !r.pago && Number(r.saldo) > 0
                  ? 'text-right font-semibold text-red-700'
                  : 'text-right text-muted-foreground'"
              >
                {{ r.saldo == null ? 'Pendente' : fmtUsd(r.saldo) }}
              </td>
              <td class="text-center">
                <input
                  v-if="r.kind === 'item'"
                  type="checkbox" :checked="r.pago" :disabled="!canEdit"
                  @change="toggleFretePago(r)"
                />
                <span v-else class="text-muted-foreground text-[10px]">—</span>
              </td>
              <!-- Ações: só em ajustes manuais (linhas de item já têm
                   o checkbox Pago e são gerenciadas pela aba Importação). -->
              <td class="text-center">
                <div v-if="r.kind === 'ajuste' && canEdit" class="inline-flex items-center gap-1">
                  <button
                    class="rounded p-1 hover:bg-muted text-muted-foreground hover:text-foreground"
                    title="Editar ajuste"
                    @click="openEditAjuste(r)"
                  >
                    <Pencil class="size-3.5" />
                  </button>
                  <button
                    class="rounded p-1 hover:bg-red-50 hover:text-red-700"
                    title="Excluir ajuste"
                    @click="deleteAjuste(r)"
                  >
                    <Trash2 class="size-3.5" />
                  </button>
                </div>
                <span v-else class="text-muted-foreground text-[10px]">—</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Modal: Ajuste manual de frete (create ou edit conforme `ajusteEditId`). -->
      <div v-if="freteAjusteModalOpen"
        class="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4"
        @click.self="closeAjusteModal">
        <div class="bg-background rounded-lg shadow-xl w-full max-w-md">
          <div class="flex items-center justify-between border-b px-4 py-3">
            <h3 class="font-semibold text-sm">
              {{ ajusteEditId ? 'Editar ajuste de frete' : 'Ajuste manual de frete' }}
            </h3>
            <button class="text-muted-foreground hover:text-foreground"
              @click="closeAjusteModal">
              <X class="size-4" />
            </button>
          </div>
          <div class="p-4 space-y-3 text-sm">
            <label class="flex flex-col gap-1">
              <span class="text-[10px] text-muted-foreground">Transportadora *</span>
              <input v-model="freteAjusteForm.transportadora"
                class="h-8 border rounded px-2 bg-background" placeholder="ex: Cargo X" />
            </label>
            <label class="flex flex-col gap-1">
              <span class="text-[10px] text-muted-foreground">Data *</span>
              <input v-model="freteAjusteForm.abertura" type="date"
                class="h-8 border rounded px-2 bg-background" />
            </label>
            <label class="flex flex-col gap-1">
              <span class="text-[10px] text-muted-foreground">
                Saldo (US$) * — negativo se for desconto/crédito
              </span>
              <input v-model="freteAjusteForm.saldo" type="number" step="0.01"
                class="h-8 border rounded px-2 bg-background text-right" />
            </label>
            <label class="flex flex-col gap-1">
              <span class="text-[10px] text-muted-foreground">Identificador do ajuste</span>
              <input v-model="freteAjusteForm.lote_nome"
                class="h-8 border rounded px-2 bg-background"
                placeholder="ex: AJ-001 (opcional)" />
            </label>
            <label class="flex flex-col gap-1">
              <span class="text-[10px] text-muted-foreground">Observação</span>
              <input v-model="freteAjusteForm.obs"
                class="h-8 border rounded px-2 bg-background"
                placeholder="motivo, referência etc." />
            </label>
          </div>
          <div class="flex gap-2 justify-end border-t px-4 py-3">
            <button class="rounded-md border px-3 py-1 text-sm"
              @click="closeAjusteModal">Cancelar</button>
            <button class="rounded-md bg-primary text-primary-foreground px-3 py-1 text-sm"
              @click="salvarFreteAjuste">
              {{ ajusteEditId ? 'Salvar' : 'Criar' }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.grid-table {
  /* width:auto + min-width keeps horizontal scroll natural when there
   * are many columns. Sem isso (w-full sozinho), as colunas dos lotes
   * comprimem pro min-content e o scroll trava antes do último lote.
   * Mesmo padrão de .cot-table / .kit-table. min-width:100% preenche
   * o container quando há poucos lotes (Mala). */
  width: auto;
  min-width: 100%;
}
.grid-table th,
.grid-table td {
  border: 1px solid hsl(var(--border));
  padding: 2px 4px;
  vertical-align: middle;
}
.grid-table td.calc {
  background: hsl(var(--muted) / 0.5);
  color: hsl(var(--muted-foreground));
}
/* Sticky horizontal nas colunas fixas (aba Importação Celular).
 * `.calc` (estoque_bling, consumo_diario, memória, reposição, saldo,
 * custo_realizado) aplica bg semi-transparente — hsl/0.5 — que vence
 * o utility `bg-background` por especificidade. Sem essa regra, cells
 * de lote vazam atrás das colunas fixas durante o scroll horizontal.
 * Specificity (0,2,2) > .grid-table td.calc (0,2,1). Header (.col-head)
 * já é opaco, não precisa override. */
.grid-table tbody td.sticky {
  background: hsl(var(--background));
}
/* Sticky <thead>: cabeçalho inteiro (8 linhas + lotes) fica fixo no topo
 * ao rolar. Substitui o sticky por-célula que existia em .col-head — o
 * thead inteiro viaja junto agora, então as 8 linhas dos lotes
 * (abertura/fechamento/previsto/realizado/saldo/prazo/quant) ficam
 * sempre visíveis. Cada <th>/<td> já tem background-color próprio
 * (col-head, lote-label, lote-value, col-quant/col-total), então não
 * fica transparente sobre o body. */
.thead-sticky {
  position: sticky;
  top: 0;
  /* 20 (não 10): empatava com as colunas sticky do corpo (z-10) e
   * era coberto ao rolar vertical na aba Celular. Bate com o padrão
   * da Tabela de Preços (thead z-20, corpo z-10). */
  z-index: 20;
}
.cell-input {
  width: 100%;
  border: 0;
  background: rgb(254 252 232 / 0.6);
  padding: 2px 4px;
  font-size: 11px;
  color: inherit;
}
:global(.dark) .cell-input {
  background: rgb(120 53 15 / 0.15);
}
.cell-input:focus {
  outline: 1px solid hsl(var(--primary));
  background: hsl(var(--background));
}
.cell-input:disabled {
  cursor: not-allowed;
  opacity: 0.7;
  background: transparent;
}

/* ── 8-row Excel-style header ──────────────────────────────────────
 * Fixed left columns: one <th rowspan=8>, centered both axes.
 * Per-lote metadata: label/value pairs that stack down the same
 * 8 thead rows. Row 8 holds the actual data sub-headers
 * (quant | total) so they line up with the body inputs.
 */
.col-head {
  vertical-align: middle;
  text-align: center;
  font-size: 11px;
  font-weight: 600;
  background: hsl(var(--muted));
  padding: 4px;
  /* Quebra só em espaços (entre palavras), nunca no meio.
   * `word-break: break-word` foi removido — não-padrão, quebrava
   * "memória" em "memóri/a". */
  white-space: normal;
  word-break: normal;
  overflow-wrap: normal;
  line-height: 1.15;
}
.lote-label {
  background: hsl(var(--muted) / 0.5);
  font-size: 11px;
  font-weight: 600;
  text-align: right;
  padding: 2px 4px;
  /* width antigo 80px forçava a sub-col QUANT a ser sempre 80px (pra
   * caber "transportadora" nowrap). Reduzindo pra 50px + permitindo
   * wrap, QUANT vira ~50px e cada lote fica mais estreito (~150px),
   * cabendo mais lotes por tela. "transportadora" quebra em 2 linhas,
   * row da transportadora fica um pouco mais alta — trade aceito. */
  white-space: normal;
  word-break: normal;
  overflow-wrap: anywhere;
  line-height: 1.1;
  width: 50px;
  color: hsl(var(--muted-foreground));
}
.lote-value {
  font-size: 11px;
  text-align: left;
  padding: 2px 6px;
  /* min-width antigo 110px expandia cols sticky do lote (valor+custo =
   * 50+50 = 100) puxando cada lote pra ~170px. 100 bate exato com
   * 2× col-total e mantém cada lote compacto (~130px). */
  min-width: 100px;
  background: hsl(var(--background));
}
.lote-value.calculated {
  font-weight: 600;
}
.lote-value.editable {
  background: rgb(255 253 230 / 0.7);
}
:global(.dark) .lote-value.editable {
  background: rgb(120 53 15 / 0.15);
}
.col-quant,
.col-total {
  text-align: center;
  font-size: 10px;
  font-weight: 700;
  background: hsl(var(--muted));
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: 3px;
}
/* width + min-width juntos: em table-layout:auto o browser pode comprimir
 * colunas sem `width` explícito, deixando a tabela mais curta que a soma
 * dos `min-width` e travando o scroll antes do último lote (Celular tem
 * 15 lotes × 3 sub-cells = 45 colunas). Mesmo padrão da .cot-prod-head.
 *
 * Larguras compactas pra caber MAIS lotes simultaneamente no Celular —
 * operador prefere ver ~7 lotes por tela em vez de ~5. Valores grandes
 * tipo "R$ 256.000,00" no `previsto` (colspan=2 ⇒ 100px) ficam apertados
 * mas legíveis no font 11px. Mala (poucos lotes) não sente diferença. */
.col-quant {
  width: 32px;
  min-width: 32px;
}
.col-total {
  width: 50px;
  min-width: 50px;
}

/* ── Cotação table ─────────────────────────────────────────────── */
.cot-table {
  /* width:auto + min-width keeps horizontal scroll natural when there
   * are many fabricantes; the parent .overflow-x-auto handles the
   * scrollbar. Fixed widths per col so the layout stays predictable. */
  width: auto;
  min-width: 100%;
  border-collapse: collapse;
}
.cot-table th,
.cot-table td {
  border: 1px solid hsl(var(--border));
  padding: 2px 4px;
  vertical-align: middle;
}
.cot-prod-head {
  background: hsl(var(--muted));
  font-weight: 700;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  width: 200px;
  min-width: 200px;
  padding: 6px 8px;
  /* Sticky left so the produto column stays visible when scrolling
   * horizontally through many fabricantes. */
  position: sticky;
  left: 0;
  z-index: 2;
}
.cot-prod-cell {
  background: hsl(var(--background));
  font-weight: 500;
  width: 200px;
  min-width: 200px;
  position: sticky;
  left: 0;
  z-index: 1;
}
.cot-fab-name {
  background: hsl(var(--muted) / 0.7);
  padding: 4px 6px;
  min-width: 240px;
}
.cot-fab-obs {
  background: rgb(255 253 230 / 0.6);
  padding: 2px 4px;
}
:global(.dark) .cot-fab-obs {
  background: rgb(120 53 15 / 0.15);
}
.cot-sub-head {
  background: hsl(var(--muted));
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  text-align: center;
  padding: 3px 4px;
  min-width: 80px;
}
.cot-actions-head {
  background: hsl(var(--muted) / 0.7);
}
.cot-input {
  width: 100%;
  border: 0;
  background: transparent;
  padding: 2px 4px;
  font-size: 11px;
  color: inherit;
}
.cot-input:focus {
  outline: 1px solid hsl(var(--primary));
  background: hsl(var(--background));
}
.cot-input:disabled {
  cursor: not-allowed;
  opacity: 0.7;
}
.cot-input::placeholder {
  color: hsl(var(--muted-foreground));
  font-style: italic;
}

/* ── Kit table ──────────────────────────────────────────────────── */
.kit-table {
  width: auto;
  min-width: 100%;
  border-collapse: collapse;
}
.kit-table th,
.kit-table td {
  border: 1px solid hsl(var(--border));
  padding: 2px 6px;
  vertical-align: middle;
}
.kit-h {
  background: hsl(var(--muted));
  font-weight: 600;
  font-size: 11px;
  text-align: center;
  position: sticky;
  top: 0;
  z-index: 2;
}
.kit-h-var {
  white-space: nowrap;
  min-width: 60px;
  padding: 4px 6px;
}
.kit-h-highlight {
  background: rgb(254 240 138);  /* yellow-200 */
}
:global(.dark) .kit-h-highlight {
  background: rgb(133 77 14 / 0.4);  /* yellow-900 */
}
/* Sticky left cols: modelo (left:0), sku (left:120), cor (left:230) */
.kit-col-modelo {
  position: sticky;
  left: 0;
  background: hsl(var(--background));
  min-width: 120px;
  max-width: 120px;
  z-index: 3;
}
.kit-col-sku {
  position: sticky;
  left: 120px;
  background: hsl(var(--background));
  min-width: 110px;
  max-width: 110px;
  z-index: 3;
}
.kit-col-cor {
  position: sticky;
  left: 230px;
  background: hsl(var(--background));
  min-width: 130px;
  max-width: 130px;
  z-index: 3;
}
/* Header sticky cols need higher z to sit on top of body sticky */
thead .kit-col-modelo,
thead .kit-col-sku,
thead .kit-col-cor {
  z-index: 4;
  background: hsl(var(--muted));
}
.kit-cell {
  text-align: center;
  cursor: pointer;
  min-width: 60px;
  user-select: none;
}
.kit-cell:hover {
  background: hsl(var(--muted) / 0.4);
}
.kit-cell-highlight {
  background: rgb(254 252 232);  /* yellow-50 */
}
:global(.dark) .kit-cell-highlight {
  background: rgb(133 77 14 / 0.15);
}
.kit-cell-highlight:hover {
  background: rgb(254 240 138);  /* yellow-200 */
}
.kit-cell-disabled {
  cursor: not-allowed;
  opacity: 0.7;
}
.kit-mark {
  color: rgb(21 128 61);  /* green-700 — default e 'sent' */
  font-weight: 700;
  font-size: 13px;
}
.kit-mark-sent {
  color: rgb(21 128 61);  /* green-700 */
}
.kit-mark-pending {
  color: rgb(217 119 6);  /* amber-600 */
}
.kit-mark-error {
  color: rgb(220 38 38);  /* red-600 */
}
.kit-resync {
  margin-left: 4px;
  color: rgb(180 83 9);   /* amber-700 */
  font-size: 11px;
  cursor: pointer;
  text-decoration: underline;
  background: transparent;
  border: none;
  padding: 0;
}
.kit-resync:hover {
  color: rgb(146 64 14);  /* amber-800 */
}
</style>
