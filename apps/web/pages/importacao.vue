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
import { computed, reactive, ref } from 'vue'
import {
  Plus, RefreshCw, Trash2, Save, Search, Download, X, AlertCircle,
  Send, CheckCircle2, Clock,
} from 'lucide-vue-next'

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
type Tab = 'mala' | 'resumo' | 'cotacao'
const tab = ref<Tab>('mala')

type Config = { tempo_reposicao: number; tempo_estoque: number }
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
  lote_quantidades: Record<string, number>
}
type Lote = {
  id: string
  nome: string
  abertura: string         // YYYY-MM-DD
  fechamento: string | null
  realizado: string | number
  previsto: string | number
  saldo: string | number
  prazo: number | null
  is_aberto: boolean
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

// ── State ─────────────────────────────────────────────────────────
const products = ref<Product[]>([])
const lotes = ref<Lote[]>([])
const resumo = ref<{ items: ResumoRow[]; total: string | number }>({ items: [], total: 0 })
const config = ref<Config>({ tempo_reposicao: 150, tempo_estoque: 60 })
const cotacao = ref<CotacaoGrid>({ fabricantes: [], produtos: [], valores: [] })
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

const visibleLotes = computed(() => lotes.value.filter((l) => showClosedLotes.value || l.is_aberto))

const filteredProducts = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return products.value
  return products.value.filter(
    (p) =>
      (p.sku || '').toLowerCase().includes(q)
      || (p.modelo_bling || '').toLowerCase().includes(q)
      || (p.cor || '').toLowerCase().includes(q)
      || (p.fornecedor || '').toLowerCase().includes(q),
  )
})

// ── Loaders ───────────────────────────────────────────────────────
async function loadAll() {
  loading.value = true
  errorText.value = null
  try {
    const [cfg, ps, ls, rs, ct] = await Promise.all([
      api<Config>('/api/importacao/config'),
      api<Product[]>('/api/importacao/products'),
      api<Lote[]>('/api/importacao/lotes'),
      api<{ items: ResumoRow[]; total: string | number }>('/api/importacao/resumo'),
      api<CotacaoGrid>('/api/importacao/cotacao'),
    ])
    config.value = cfg
    products.value = ps
    lotes.value = ls
    resumo.value = rs
    cotacao.value = ct
    rebuildCotCells(ct.valores)
  } catch (e: any) {
    errorText.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    loading.value = false
  }
}
await loadAll()

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
    products.value = await api<Product[]>('/api/importacao/products')
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
const newProductPreviewName = computed(() =>
  generateMalaName(newProduct.modelo_bling, newProduct.sku, newProduct.cor),
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
  const abertura = new Date().toISOString().slice(0, 10)
  try {
    const lote = await api<Lote>('/api/importacao/lotes', {
      method: 'POST',
      body: { nome, abertura },
    })
    lotes.value = [lote, ...lotes.value]
  } catch (e: any) {
    errorText.value = `Falha ao criar lote: ${e?.data?.detail?.code || 'erro'}`
  }
}

async function fecharLote(lote: Lote) {
  if (lote.fechamento) return
  if (!confirm(`Fechar o lote ${lote.nome} hoje? Isso cria um lançamento no Resumo.`)) return
  const fechamento = new Date().toISOString().slice(0, 10)
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
async function loadLotesOnly() {
  try { lotes.value = await api<Lote[]>('/api/importacao/lotes') } catch { /* ignore */ }
}
async function loadResumoOnly() {
  try {
    resumo.value = await api<{ items: ResumoRow[]; total: string | number }>(
      '/api/importacao/resumo',
    )
  } catch { /* ignore */ }
}

// ── Resumo: add manual entry ──────────────────────────────────────
const newResumo = reactive({
  data: new Date().toISOString().slice(0, 10),
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
    config.value = await api<Config>('/api/importacao/config', {
      method: 'PATCH',
      body: { ...config.value },
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
    const fab = await api<CotFabricante>('/api/importacao/cotacao/fabricantes', { method: 'POST' })
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
    const prod = await api<CotProduto>('/api/importacao/cotacao/produtos', { method: 'POST' })
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
</script>

<template>
  <div class="space-y-3 p-4">
    <!-- Header + tab nav -->
    <div class="flex flex-wrap items-center gap-3">
      <h1 class="text-xl font-semibold">Importação</h1>
      <div class="flex gap-1 rounded-md bg-muted/40 p-1 w-fit">
        <button
          v-for="t in (['mala','resumo','cotacao'] as const)"
          :key="t"
          class="px-3 py-1.5 rounded text-sm transition-colors"
          :class="tab === t ? 'bg-background shadow-sm font-medium' : 'hover:bg-background/60 text-muted-foreground'"
          @click="tab = t"
        >
          {{ t === 'mala' ? 'Mala' : t === 'resumo' ? 'Resumo' : 'Cotação' }}
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

      <div class="border rounded-md overflow-x-auto">
        <table class="grid-table w-full text-xs border-collapse">
          <thead>
            <!-- 8-row header. Fixed left columns use rowspan=8 so their
                 label sits centered across the full header height.
                 Each lote occupies 2 cols (label + value) and fills
                 rows 1-7 with metadata (lote/abertura/fechamento/
                 previsto/realizado/saldo/prazo) then row 8 with the
                 actual sub-headers (quant | total) that align with
                 the per-cell inputs in tbody. Mirrors the operator's
                 Excel layout 1:1. -->
            <tr>
              <th rowspan="8" class="col-head text-left">fornecedor</th>
              <th rowspan="8" class="col-head text-left">modelo china</th>
              <th rowspan="8" class="col-head text-left">cor china</th>
              <th rowspan="8" class="col-head text-left">fechamento</th>
              <th rowspan="8" class="col-head text-center">TSA</th>
              <th rowspan="8" class="col-head text-left">modelo bling</th>
              <th rowspan="8" class="col-head text-left">sku</th>
              <th rowspan="8" class="col-head text-left">cor</th>
              <th rowspan="8" class="col-head text-right">custo bling</th>
              <th rowspan="8" class="col-head text-right">estoque bling</th>
              <th rowspan="8" class="col-head text-right">consumo diário</th>
              <th rowspan="8" class="col-head text-right">memória consumo</th>
              <th rowspan="8" class="col-head text-right">reposição estoque</th>
              <th rowspan="8" class="col-head text-right">saldo reposição</th>
              <th rowspan="8" class="col-head text-left">nome gerado</th>
              <th rowspan="8" class="col-head text-left">obs</th>
              <template v-for="lote in visibleLotes" :key="`lote-r1-${lote.id}`">
                <td class="lote-label border-l">lote</td>
                <td class="lote-value">
                  <span class="font-semibold uppercase">{{ lote.nome }}</span>
                  <button v-if="canEdit && lote.is_aberto" class="ml-2 text-[10px] underline hover:text-primary" @click="fecharLote(lote)">fechar</button>
                  <button v-if="canDelete" class="ml-1 text-destructive" @click="removeLote(lote)" :title="`Excluir ${lote.nome}`">
                    <Trash2 class="size-3 inline" />
                  </button>
                </td>
              </template>
              <th rowspan="8" v-if="canEdit" class="col-head text-center">bling</th>
              <th rowspan="8" v-if="canDelete" class="col-head w-8"></th>
            </tr>
            <tr>
              <template v-for="lote in visibleLotes" :key="`lote-r2-${lote.id}`">
                <td class="lote-label border-l">abertura</td>
                <td class="lote-value editable">
                  <input type="date" :value="lote.abertura" :disabled="!canEdit"
                    class="w-full bg-transparent border-0 p-0 text-[11px]"
                    @input="(e) => schedulePatchLote(lote, 'abertura', (e.target as HTMLInputElement).value)" />
                </td>
              </template>
            </tr>
            <tr>
              <template v-for="lote in visibleLotes" :key="`lote-r3-${lote.id}`">
                <td class="lote-label border-l">fechamento</td>
                <td class="lote-value editable">
                  <input type="date" :value="lote.fechamento ?? ''" :disabled="!canEdit"
                    class="w-full bg-transparent border-0 p-0 text-[11px]"
                    @input="(e) => schedulePatchLote(lote, 'fechamento', (e.target as HTMLInputElement).value || null)" />
                </td>
              </template>
            </tr>
            <tr>
              <template v-for="lote in visibleLotes" :key="`lote-r4-${lote.id}`">
                <td class="lote-label border-l">previsto</td>
                <td class="lote-value calculated">{{ fmtMoney(lote.previsto) }}</td>
              </template>
            </tr>
            <tr>
              <template v-for="lote in visibleLotes" :key="`lote-r5-${lote.id}`">
                <td class="lote-label border-l">realizado</td>
                <td class="lote-value editable">
                  <input type="number" step="0.01" :value="lote.realizado" :disabled="!canEdit"
                    class="w-full bg-transparent border-0 p-0 text-[11px] text-right"
                    @input="(e) => schedulePatchLote(lote, 'realizado', Number((e.target as HTMLInputElement).value) || 0)" />
                </td>
              </template>
            </tr>
            <tr>
              <template v-for="lote in visibleLotes" :key="`lote-r6-${lote.id}`">
                <td class="lote-label border-l">saldo</td>
                <td class="lote-value calculated" :class="Number(lote.saldo) > 0 ? 'text-red-700' : 'text-emerald-700'">
                  {{ fmtMoney(lote.saldo) }}
                </td>
              </template>
            </tr>
            <tr>
              <template v-for="lote in visibleLotes" :key="`lote-r7-${lote.id}`">
                <td class="lote-label border-l">prazo</td>
                <td class="lote-value calculated">{{ lote.prazo != null ? lote.prazo + 'd' : '—' }}</td>
              </template>
            </tr>
            <tr>
              <!-- Row 8 = the actual sub-headers for the body data cells.
                   These align directly above the quant/total cells in tbody. -->
              <template v-for="lote in visibleLotes" :key="`lote-r8-${lote.id}`">
                <th class="col-quant border-l">quant</th>
                <th class="col-total">total</th>
              </template>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!loading && filteredProducts.length === 0">
              <td :colspan="16 + visibleLotes.length * 2 + (canEdit ? 1 : 0) + (canDelete ? 1 : 0)" class="py-6 text-center text-muted-foreground">
                Nenhum produto. Clique em "Criar produto" para começar.
              </td>
            </tr>
            <tr v-for="row in filteredProducts" :key="row.id" class="even:bg-muted/10 hover:bg-amber-50/40">
              <td><input class="cell-input" :value="row.fornecedor ?? ''" :disabled="!canEdit"
                @input="(e) => scheduleSave(row, 'fornecedor', (e.target as HTMLInputElement).value)" /></td>
              <td><input class="cell-input" :value="row.modelo_china ?? ''" :disabled="!canEdit"
                @input="(e) => scheduleSave(row, 'modelo_china', (e.target as HTMLInputElement).value)" /></td>
              <td><input class="cell-input" :value="row.cor_china ?? ''" :disabled="!canEdit"
                @input="(e) => scheduleSave(row, 'cor_china', (e.target as HTMLInputElement).value)" /></td>
              <td><input class="cell-input" :value="row.fechamento ?? ''" :disabled="!canEdit"
                @input="(e) => scheduleSave(row, 'fechamento', (e.target as HTMLInputElement).value)" /></td>
              <td class="text-center">
                <!-- TSA = count of locks. Blank = no TSA, 1/2/3 = number of cadeados. -->
                <input
                  type="number"
                  min="1"
                  max="3"
                  step="1"
                  class="cell-input text-center"
                  :value="row.tsa ?? ''"
                  :disabled="!canEdit"
                  @input="(e) => {
                    const v = (e.target as HTMLInputElement).value;
                    scheduleSave(row, 'tsa', v === '' ? null : Math.max(1, Math.min(3, Number(v))));
                  }"
                />
              </td>
              <td><input class="cell-input" :value="row.modelo_bling ?? ''" :disabled="!canEdit"
                @input="(e) => scheduleSave(row, 'modelo_bling', (e.target as HTMLInputElement).value)" /></td>
              <td class="font-mono"><input class="cell-input" :value="row.sku ?? ''" :disabled="!canEdit"
                @input="(e) => scheduleSave(row, 'sku', (e.target as HTMLInputElement).value)" /></td>
              <td><input class="cell-input" :value="row.cor ?? ''" :disabled="!canEdit"
                @input="(e) => scheduleSave(row, 'cor', (e.target as HTMLInputElement).value)" /></td>
              <td><input type="number" step="0.01" class="cell-input text-right" :value="row.custo_bling" :disabled="!canEdit"
                @input="(e) => scheduleSave(row, 'custo_bling', Number((e.target as HTMLInputElement).value) || 0)" /></td>
              <!-- estoque_bling auto-pulled from products.stock by SKU in
                   the router; consumo_diario = bling_orders last 30d / 30.
                   Both read-only — the operator can't override the source. -->
              <td class="calc text-right" :title="'Auto: products.stock por SKU'">
                {{ row.estoque_bling ?? '—' }}
              </td>
              <td class="calc text-right" :title="'Auto: bling_orders 30d ÷ 30'">
                {{ fmtNum2(row.consumo_diario) }}
              </td>
              <td class="calc text-right">{{ fmtNum2(row.memoria_consumo) }}</td>
              <td class="calc text-right" :class="reposicaoClass(row.reposicao_estoque)">
                {{ row.reposicao_estoque ?? '—' }}
              </td>
              <td class="calc text-right" :class="reposicaoClass(row.saldo_reposicao)">
                {{ row.saldo_reposicao ?? '—' }}
              </td>
              <td class="text-[11px] text-muted-foreground italic" :title="row.nome_gerado">
                {{ row.nome_gerado || '—' }}
              </td>
              <td><input class="cell-input" :value="row.obs ?? ''" :disabled="!canEdit"
                @input="(e) => scheduleSave(row, 'obs', (e.target as HTMLInputElement).value)" /></td>
              <!-- Per-lote cells align directly under the row-8 quant/total sub-headers. -->
              <template v-for="lote in visibleLotes" :key="`cell-${row.id}-${lote.id}`">
                <td class="border-l">
                  <input
                    type="number"
                    class="cell-input text-right"
                    :value="row.lote_quantidades[lote.id] ?? ''"
                    :disabled="!canEdit"
                    @input="(e) => scheduleLoteItem(row, lote.id, Number((e.target as HTMLInputElement).value) || 0)"
                  />
                </td>
                <td class="calc text-right">{{ fmtMoney(loteTotal(row, lote.id)) }}</td>
              </template>
              <td v-if="canEdit" class="text-center whitespace-nowrap">
                <button
                  class="inline-flex items-center gap-1 rounded border px-2 py-0.5 text-[10px] hover:bg-muted"
                  :class="{
                    'bg-amber-50 text-amber-700 border-amber-300': row.bling_sync_status === 'pending',
                    'bg-emerald-50 text-emerald-700 border-emerald-300': row.bling_sync_status === 'sent',
                    'bg-red-50 text-red-700 border-red-300': row.bling_sync_status === 'error',
                  }"
                  :title="row.bling_sync_status === 'pending'
                    ? `Pendente desde ${row.bling_sync_marked_at ?? ''} — aguardando integração de escrita do Bling`
                    : row.bling_sync_status === 'sent'
                      ? 'Já criado no Bling'
                      : row.bling_sync_status === 'error'
                        ? 'Falha no último envio'
                        : 'Marcar como pronto para enviar ao Bling (a integração de escrita ainda não existe)'"
                  @click="sendToBling(row)"
                >
                  <Clock v-if="row.bling_sync_status === 'pending'" class="size-3" />
                  <CheckCircle2 v-else-if="row.bling_sync_status === 'sent'" class="size-3" />
                  <Send v-else class="size-3" />
                  <span>{{
                    row.bling_sync_status === 'pending' ? 'Pendente'
                    : row.bling_sync_status === 'sent' ? 'Enviado'
                    : row.bling_sync_status === 'error' ? 'Erro'
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
    <div v-if="tab === 'cotacao'" class="space-y-2">
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
              Padrão: <code>Mala {{ '{' }}modelo bling{{ '}' }} tamanho {{ '{' }}n após o ponto no SKU{{ '}' }} - {{ '{' }}cor{{ '}' }}</code>
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
            <label class="flex flex-col gap-1">
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
            <label class="flex flex-col gap-1">
              <span class="text-xs font-medium text-muted-foreground">Modelo china</span>
              <input v-model="newProduct.modelo_china" type="text" class="h-8 border rounded px-2 bg-background" />
            </label>
            <label class="flex flex-col gap-1">
              <span class="text-xs font-medium text-muted-foreground">Cor china</span>
              <input v-model="newProduct.cor_china" type="text" class="h-8 border rounded px-2 bg-background" />
            </label>
            <label class="flex flex-col gap-1">
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
            <div>categoria: <code>mala</code> · tag: <code>mala</code></div>
            <div class="text-muted-foreground">
              Esses valores são gravados automaticamente ao enviar o produto pro Bling.
              A integração de escrita ainda não foi implementada — o botão "Enviar pro Bling"
              apenas marca o produto como <em>pendente</em> de sincronização.
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
  </div>
</template>

<style scoped>
.grid-table th,
.grid-table td {
  border: 1px solid hsl(var(--border));
  padding: 2px 4px;
  vertical-align: middle;
}
.grid-table td.calc {
  background: hsl(var(--muted) / 0.5);
  color: hsl(var(--muted-foreground));
  font-style: italic;
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
  background: hsl(var(--muted) / 0.7);
  padding: 4px;
  white-space: nowrap;
}
.lote-label {
  background: hsl(var(--muted) / 0.5);
  font-size: 11px;
  font-weight: 600;
  text-align: right;
  padding: 2px 6px;
  white-space: nowrap;
  width: 80px;
  color: hsl(var(--muted-foreground));
}
.lote-value {
  font-size: 11px;
  text-align: left;
  padding: 2px 6px;
  min-width: 110px;
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
</style>
