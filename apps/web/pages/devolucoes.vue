<script setup lang="ts">
import {
  AlertCircle,
  ChevronLeft,
  ChevronRight,
  Clock,
  Download,
  ExternalLink,
  Loader2,
  Plus,
  RotateCcw,
  Search,
  Undo2,
  X,
} from 'lucide-vue-next'

definePageMeta({ middleware: ['permission'], permission: { resource: 'devolucoes', action: 'view' } })

type BlingStockResult = {
  ok: boolean
  action: string
  sku: string | null
  bling_product_id: number | null
  message: string
}

type DevolutionRow = {
  id: string
  data: string | null
  pedido_bling: string | null
  pedido_marketplace: string | null
  conta: string
  sku: string | null
  produtos: string | null
  custo_produto: number | null
  condicao_produto: string | null
  link_abertura: string | null
  reembolso: boolean
  motivo_devolucao: string | null
  custo_manutencao: number | null
  tecnico: string | null
  devolver_estoque: boolean
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
  created_at: string
  updated_at: string
  bling_stock_result?: BlingStockResult | null
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
}

type DevolutionDraft = LookupRow & {
  condicao_produto: string
  link_abertura: string
  reembolso: boolean
  motivo_devolucao: string
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

const MOTIVOS_DEVOLUCAO = [
  'Mudou de ideia',
  'Golpe',
  'Tamanho',
  'Item faltando',
  'Dano funcional / Não funciona',
  'Item Incorreto',
  'Não recebido',
  'Danificado (Outros)',
  'Pacote Suspeito',
  'Embalagem Externa Danificada',
] as const

const CONDICOES_PRODUTO = [
  'Novo',
  'Usado',
  'Manutenção',
  'Extraviado',
  'Trocado',
  'Entregue',
] as const

const TECNICOS = [
  'SmarPlay',
  'Bogota',
  'Shark',
  'Cybercell',
] as const

const { api } = useApi()
const canEdit = useCan('devolucoes', 'edit')
const isAdmin = useIsAdmin()

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
const dataDevolucaoFilter = ref('')
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

// Modal de destino de estoque: bin existente OU tag p/ criar produto novo.
type EstoqueResult = { destino_sku?: string; nova_tag?: string }
const estoqueModal = ref<{ open: boolean; sku: string; condicao: string; resolve: ((v: EstoqueResult | null) => void) | null }>(
  { open: false, sku: '', condicao: '', resolve: null },
)
function askEstoque(sku: string, condicao: string): Promise<EstoqueResult | null> {
  return new Promise((resolve) => { estoqueModal.value = { open: true, sku, condicao, resolve } })
}
function onEstoqueConfirm(payload: EstoqueResult) {
  estoqueModal.value.resolve?.(payload)
  estoqueModal.value = { open: false, sku: '', condicao: '', resolve: null }
}
function onEstoqueCancel() {
  estoqueModal.value.resolve?.(null)
  estoqueModal.value = { open: false, sku: '', condicao: '', resolve: null }
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
  // Usado segue a lógica de usados (modal → cria z000N.<tag>).
  if (effCondicao === 'Novo' && isMalaOrEletro(effSku)) {
    out.estoque_destino_sku = effSku
    return out
  }

  // Destino de estoque: bin existente ou criação de produto novo (z000N.<tag>).
  const dest = await askEstoque(effSku, effCondicao)
  if (!dest) return null
  out.estoque_destino_sku = dest.destino_sku ?? null
  out.estoque_nova_tag = dest.nova_tag ?? null
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
  return alreadyAddedKeys.value.has(`${row.pedido_bling}|${row.sku}`)
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

function apiError(e: any) {
  const detail = e?.data?.detail
  if (detail && typeof detail === 'object') return detail.message || detail.code || e?.message || 'erro'
  return detail || e?.message || 'erro'
}

function brl(v: number | null | undefined) {
  if (v == null) return '—'
  return v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
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
  field: 'condicao_produto' | 'link_abertura' | 'motivo_devolucao' | 'tecnico' | 'observacao',
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
    if (dataDevolucaoFilter.value) params.set('data_devolucao', dataDevolucaoFilter.value)
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
    if (dataDevolucaoFilter.value) params.set('data_devolucao', dataDevolucaoFilter.value)
    const blob = await api<Blob>(`/api/devolutions/export.xlsx?${params.toString()}`, { responseType: 'blob' as any })
    const href = URL.createObjectURL(blob as any)
    const a = document.createElement('a')
    a.href = href
    a.download = `devolucoes_${new Date().toISOString().slice(0, 10)}.xlsx`
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

await load()

let searchTimer: ReturnType<typeof setTimeout> | null = null
watch(search, () => {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    page.value = 1
    load()
  }, 300)
})
watch([reembolsoFilter, tagFilter, condicaoFilter, dataDevolucaoFilter], () => { page.value = 1; load() })
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
  }
  creating.value = true
  lookupError.value = null
  const remaining: DevolutionDraft[] = []
  let added = 0
  try {
    for (const d of [...drafts.value]) {
      // No ADD: só Novo/Usado/Trocado processam estoque. Manutenção e Extraviado
      // não mexem no estoque no add (Manutenção volta ao estoque depois, pelo
      // toggle na linha já inserida).
      const processAtAdd = ['Novo', 'Usado', 'Trocado'].includes(d.condicao_produto)
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
        const created = await api<DevolutionRow>('/api/devolutions', { method: 'POST', body })
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
  if (value === 'Extraviado' || value === 'Manutenção') setRowReembolso(row, true)
  if (row.devolver_estoque && isStockTrigger(value)) {
    const extra = await resolveStockModals(value, row.sku, true)
    if (extra === null) {
      setRowText(row, 'condicao_produto', prev ?? '')
      return
    }
    applyStockModalFields(row, extra)
  }
  await saveRow(row)
}

async function saveRow(row: DevolutionRow) {
  if (!canEdit.value || !hasDirty(row.id) || isSaving(row.id)) return
  if (linkRequired(row.condicao_produto) && !row.link_abertura) {
    error.value = 'Link de abertura obrigatório para Extraviado / Manutenção'
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
    if (idx >= 0) items.value[idx] = updated
    clearDirty(row.id)
    if (updated.bling_stock_result) showStockToast(updated.bling_stock_result)
  } catch (e: any) {
    error.value = apiError(e)
  } finally {
    setSaving(row.id, false)
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
    <PageHeader title="Devoluções" description="Controle manual de devoluções por pedido.">
      <template #actions>
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
      </template>
    </PageHeader>

    <div v-if="error" class="flex items-center gap-2 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-400">
      <AlertCircle class="size-4" />
      {{ error }}
    </div>

    <div class="grid grid-cols-2 lg:grid-cols-3 gap-3">
      <StatCard label="Total devoluções" :value="total" :icon="Undo2" />
      <StatCard label="Reembolsadas" :value="totalReembolsadas" :icon="Clock" tone="warning" />
      <StatCard label="Custo manutenção (pág.)" :value="brl(totalCustoManutencao)" tone="danger" />
    </div>

    <div v-if="addOpen" class="rounded-md border bg-background">
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
              <th class="px-2 py-1 text-center text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-amber-50 dark:bg-amber-900/20" :colspan="isAdmin ? 8 : 7">Devolução</th>
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
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[180px] bg-amber-50 dark:bg-amber-900/20">Link abertura</th>
              <th class="px-2 py-1 text-center font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[80px] bg-amber-50 dark:bg-amber-900/20">Reembolso</th>
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
                  v-model="d.link_abertura"
                  :class="linkRequired(d.condicao_produto) && !d.link_abertura ? sheetInputRequiredClass : sheetInputClass"
                  :placeholder="linkRequired(d.condicao_produto) ? 'obrigatório' : ''"
                />
              </td>
              <td class="px-2 py-1 text-center bg-amber-50/40 dark:bg-amber-900/10">
                <input v-model="d.reembolso" type="checkbox" class="size-4 rounded border accent-primary" />
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

    <div class="flex flex-wrap items-center gap-2">
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
        <option value="true">reembolsadas</option>
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
      <input
        v-model="dataDevolucaoFilter"
        type="date"
        title="Data devolução"
        class="h-9 rounded-md border bg-background px-2 text-sm"
      />
      <Button size="sm" variant="outline" :disabled="exporting" @click="exportXlsx">
        <Download class="size-4 mr-1.5" :class="{ 'animate-pulse': exporting }" />
        exportar xlsx
      </Button>
      <span class="ml-auto text-xs text-muted-foreground">
        {{ rangeStart }}–{{ rangeEnd }} de {{ total }} · reembolsadas {{ totalReembolsadas }} · manutenção {{ brl(totalCustoManutencao) }}
      </span>
    </div>

    <div class="overflow-auto rounded border max-h-[75vh] focus:outline-none" tabindex="0">
      <table class="min-w-[2075px] text-xs border-collapse">
        <thead class="sticky top-0 z-20 bg-background">
          <tr>
            <th class="px-2 py-1 text-left text-[11px] font-semibold border-b" colspan="8">Identificação</th>
            <th class="px-2 py-1 text-center text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-amber-50 dark:bg-amber-900/20" :colspan="isAdmin ? 9 : 8">Devolução</th>
            <th class="px-2 py-1 text-left text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-emerald-50 dark:bg-emerald-900/20" colspan="1">Observação</th>
          </tr>
          <tr class="border-b">
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[115px]">Data</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[120px]">Data Devolução</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[120px]">Pedido Bling</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[155px]">Pedido Marketplace</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[150px]">Conta</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[130px]">SKU</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[90px]">Tags</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[200px]">Produtos</th>
            <th v-if="isAdmin" class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px] bg-amber-50 dark:bg-amber-900/20 border-l-[3px] border-gray-400 dark:border-gray-600">Custo produto</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[140px] bg-amber-50 dark:bg-amber-900/20">Condição</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[180px] bg-amber-50 dark:bg-amber-900/20">Link abertura</th>
            <th class="px-2 py-1 text-center font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[80px] bg-amber-50 dark:bg-amber-900/20">Reembolso</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[180px] bg-amber-50 dark:bg-amber-900/20">Motivo</th>
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[120px] bg-amber-50 dark:bg-amber-900/20">Custo manutenção</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[120px] bg-amber-50 dark:bg-amber-900/20">Técnico</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[120px] bg-amber-50 dark:bg-amber-900/20">Devolver estoque</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[140px] bg-amber-50 dark:bg-amber-900/20">Data devolvido estoque</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[240px] bg-emerald-50 dark:bg-emerald-900/20 border-l-[3px] border-gray-400 dark:border-gray-600">Observação</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading && !items.length">
            <td :colspan="isAdmin ? 18 : 17" class="py-8 text-center text-muted-foreground">
              <Loader2 class="size-4 inline animate-spin mr-1.5" />
              carregando…
            </td>
          </tr>
          <tr v-else-if="!items.length">
            <td :colspan="isAdmin ? 18 : 17" class="py-8 text-center text-muted-foreground">sem registros</td>
          </tr>
          <tr v-for="row in items" :key="row.id" class="border-t hover:brightness-95 dark:hover:brightness-110">
            <td class="px-2 py-1 whitespace-nowrap text-muted-foreground">{{ fmtDateTime(row.data) }}</td>
            <td class="px-2 py-1 whitespace-nowrap text-muted-foreground">{{ fmtDateTime(row.created_at) }}</td>
            <td class="px-2 py-1 font-mono whitespace-nowrap">{{ row.pedido_bling || '—' }}</td>
            <td class="px-2 py-1 font-mono text-muted-foreground whitespace-nowrap">{{ row.pedido_marketplace || '—' }}</td>
            <td class="px-2 py-1 whitespace-nowrap">{{ row.conta }}</td>
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
            <td class="px-2 py-1 text-center bg-amber-50/40 dark:bg-amber-900/10">
              <input
                :checked="row.reembolso"
                :disabled="!canEdit"
                type="checkbox"
                class="size-4 rounded border accent-primary disabled:cursor-default disabled:opacity-70"
                @change="(e) => { setRowReembolso(row, (e.target as HTMLInputElement).checked); saveRow(row) }"
              />
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
            </td>
            <td class="px-2 py-1 whitespace-nowrap text-muted-foreground bg-amber-50/40 dark:bg-amber-900/10">{{ fmtDateTime(row.data_devolvido_estoque) }}</td>
            <td class="px-1 py-0.5 bg-emerald-50/40 dark:bg-emerald-900/10 border-l-[3px] border-gray-400 dark:border-gray-600">
              <input
                :value="row.observacao || ''"
                :disabled="!canEdit"
                :class="sheetInputClass"
                @input="(e) => setRowText(row, 'observacao', (e.target as HTMLInputElement).value)"
                @blur="saveRow(row)"
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
      @confirm="onEstoqueConfirm"
      @cancel="onEstoqueCancel"
    />
  </div>
</template>
