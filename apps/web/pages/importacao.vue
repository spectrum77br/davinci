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
type Tab = 'mala' | 'resumo' | 'reposicao'
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

// ── State ─────────────────────────────────────────────────────────
const products = ref<Product[]>([])
const lotes = ref<Lote[]>([])
const resumo = ref<{ items: ResumoRow[]; total: string | number }>({ items: [], total: 0 })
const config = ref<Config>({ tempo_reposicao: 150, tempo_estoque: 60 })

const loading = ref(false)
const errorText = ref<string | null>(null)
const saveTimers = reactive<Record<string, ReturnType<typeof setTimeout>>>({})
const showClosedLotes = ref(false)
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
    const [cfg, ps, ls, rs] = await Promise.all([
      api<Config>('/api/importacao/config'),
      api<Product[]>('/api/importacao/products'),
      api<Lote[]>('/api/importacao/lotes'),
      api<{ items: ResumoRow[]; total: string | number }>('/api/importacao/resumo'),
    ])
    config.value = cfg
    products.value = ps
    lotes.value = ls
    resumo.value = rs
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

async function addProduct() {
  const sku = prompt('SKU do novo produto:')?.trim()
  if (!sku) return
  try {
    const row = await api<Product>('/api/importacao/products', {
      method: 'POST',
      body: { sku },
    })
    products.value = [...products.value, row]
  } catch (e: any) {
    errorText.value = `Falha ao adicionar: ${e?.data?.detail?.code || 'erro'}`
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

// ── Reposição: config ─────────────────────────────────────────────
const savingConfig = ref(false)
async function saveConfig() {
  savingConfig.value = true
  try {
    config.value = await api<Config>('/api/importacao/config', {
      method: 'PATCH',
      body: { ...config.value },
    })
    // Affects every product's reposicao_estoque.
    void loadProductsOnly()
  } catch (e: any) {
    errorText.value = `Falha ao salvar config: ${e?.data?.detail?.code || 'erro'}`
  } finally {
    savingConfig.value = false
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
</script>

<template>
  <div class="space-y-3 p-4">
    <!-- Header + tab nav -->
    <div class="flex flex-wrap items-center gap-3">
      <h1 class="text-xl font-semibold">Importação</h1>
      <div class="flex gap-1 rounded-md bg-muted/40 p-1 w-fit">
        <button
          v-for="t in (['mala','resumo','reposicao'] as const)"
          :key="t"
          class="px-3 py-1.5 rounded text-sm transition-colors"
          :class="tab === t ? 'bg-background shadow-sm font-medium' : 'hover:bg-background/60 text-muted-foreground'"
          @click="tab = t"
        >
          {{ t === 'mala' ? 'Mala' : t === 'resumo' ? 'Resumo' : 'Reposição' }}
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
          @click="addProduct"
        >
          <Plus class="size-3" /> Adicionar produto
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
              <td :colspan="15 + visibleLotes.length * 2 + (canDelete ? 1 : 0)" class="py-6 text-center text-muted-foreground">
                Nenhum produto. Clique em "Adicionar produto" para começar.
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

    <!-- ─── TAB REPOSIÇÃO ────────────────────────────────────────── -->
    <div v-if="tab === 'reposicao'" class="space-y-4 max-w-3xl">
      <div class="border rounded-md p-4 space-y-3">
        <h2 class="font-semibold">Parâmetros da fórmula</h2>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
          <label class="flex flex-col gap-1">
            <span class="text-xs font-medium text-muted-foreground">Tempo de reposição (dias)</span>
            <input v-model.number="config.tempo_reposicao" type="number" :disabled="!canEdit"
              class="h-8 border rounded px-2 bg-background" />
          </label>
          <label class="flex flex-col gap-1">
            <span class="text-xs font-medium text-muted-foreground">Tempo de estoque seguro (dias)</span>
            <input v-model.number="config.tempo_estoque" type="number" :disabled="!canEdit"
              class="h-8 border rounded px-2 bg-background" />
          </label>
        </div>
        <button v-if="canEdit" class="inline-flex items-center gap-1 rounded-md bg-primary text-primary-foreground px-3 py-1.5 text-sm hover:opacity-90 disabled:opacity-50"
          :disabled="savingConfig" @click="saveConfig">
          <Save class="size-3.5" /> Salvar
        </button>
      </div>

      <div class="border rounded-md p-4 bg-muted/20 space-y-2 text-sm">
        <h2 class="font-semibold">Fórmula completa</h2>
        <code class="block bg-background border rounded p-2 text-xs whitespace-pre-wrap">
[(estoque_bling / memoria_consumo) - (tempo_reposicao + tempo_estoque)] × consumo_diario
        </code>
        <div class="text-xs space-y-1 mt-2">
          <p><strong>E</strong>: estoque / memória = quantos dias o estoque atual dura</p>
          <p><strong>F</strong>: tempo_reposicao + tempo_estoque = dias que preciso para a próxima carga chegar + segurança</p>
          <p><strong>G</strong>: F − E = saldo de dias (positivo = falta, negativo = excedente)</p>
          <p><strong>H</strong>: G × consumo_diario = total de unidades a repor (ou excedente)</p>
        </div>
      </div>

      <div class="border rounded-md p-4 space-y-2 text-xs">
        <h2 class="font-semibold text-sm">Regra da "memória de consumo"</h2>
        <p>memória = MAX(consumo_diario_atual, maior_media_30_dias). Quando estoque = 0, usar a maior média (não o consumo atual, que seria 0).</p>
        <p class="text-muted-foreground">Nesta v1, consumo_diario e maior_media_30d são preenchidos manualmente na aba Mala. Integração com Bling para puxar essas métricas automaticamente está prevista.</p>
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
</style>
