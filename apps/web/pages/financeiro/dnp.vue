<script setup lang="ts">
// DNP — Desenvolvimento de Produtos. Planilha de avaliação de novos
// produtos: operador preenche dados do fornecedor + parâmetros de
// venda, sistema calcula custo de importação rateado (incluindo
// certificado) e margem estimada em tempo real.
//
// Dólar do dia: bate na awesomeapi do lado do cliente ao carregar a
// página. Operador pode sobrescrever; o valor persistido em
// davinci.dnp_config é o que vale pra próxima carga.
import { computed, reactive, ref } from 'vue'
import { Plus, RefreshCw, Trash2, ExternalLink } from 'lucide-vue-next'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'financeiro', action: 'view' },
})

const { api } = useApi()
const auth = useAuthStore()
const canEdit = computed(() => {
  if (auth.isAdmin) return true
  const p = auth.user?.permissions?.financeiro
  return Boolean(p?.edit || p?.delete)
})
const canDelete = computed(() => {
  if (auth.isAdmin) return true
  return Boolean(auth.user?.permissions?.financeiro?.delete)
})

type Produto = {
  id: string
  produto: string | null
  link: string | null
  fabrica: string | null
  modelo: string | null
  moq: number | null
  voltagem: string | null
  cor: string | null
  material: string | null
  tamanho: string | null
  potencia: string | null
  valor_usd: number | null
  projecao_compra: number | null
  fator: number | null
  venda_estimada: number | null
  frete: number | null
  comissao: number | null
  inmetro: string | null
  obs: string | null
}

const rows = ref<Produto[]>([])
const loading = ref(false)
const errorText = ref<string | null>(null)
const saveTimers = reactive<Record<string, ReturnType<typeof setTimeout>>>({})

// Singleton config
const dolar = ref<number>(5.0)
const certificado = ref<number>(10000)
const dolarApiStatus = ref<string | null>(null)
const configSaveTimer = ref<ReturnType<typeof setTimeout> | null>(null)

// Seleção pra exclusão em batch
const selected = ref<Set<string>>(new Set())

async function loadConfig() {
  try {
    const c = await api<{ dolar_dia: number | null; certificado: number | null }>('/api/financeiro/dnp/config')
    if (c.dolar_dia != null) dolar.value = Number(c.dolar_dia)
    if (c.certificado != null) certificado.value = Number(c.certificado)
  } catch {
    // mantém defaults
  }
}

async function loadProdutos() {
  loading.value = true
  errorText.value = null
  try {
    rows.value = await api<Produto[]>('/api/financeiro/dnp/produtos')
  } catch (e: any) {
    errorText.value = e?.data?.detail?.code || e?.message || 'erro'
    rows.value = []
  } finally {
    loading.value = false
  }
}

// Dólar automático — bate em awesomeapi do CLIENT (a API tem CORS aberto).
// Se falhar (rede / rate-limit), mantém o valor que veio do banco.
async function refreshDolar() {
  dolarApiStatus.value = 'Buscando…'
  try {
    const r = await fetch('https://economia.awesomeapi.com.br/json/last/USD-BRL', { cache: 'no-store' })
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    const j = await r.json()
    const bid = Number(j?.USDBRL?.bid)
    if (Number.isFinite(bid) && bid > 0) {
      dolar.value = bid
      dolarApiStatus.value = `Atualizado: ${new Date().toLocaleTimeString('pt-BR')}`
      void persistConfig()
    } else {
      dolarApiStatus.value = 'Resposta inválida'
    }
  } catch (e: any) {
    dolarApiStatus.value = `Falha: ${e?.message || 'rede'}`
  }
}

await loadConfig()
await loadProdutos()
// Não awaita — operador já vê a tabela enquanto o dólar carrega.
void refreshDolar()

function scheduleConfigSave() {
  if (configSaveTimer.value) clearTimeout(configSaveTimer.value)
  configSaveTimer.value = setTimeout(() => {
    void persistConfig()
  }, 500)
}

async function persistConfig() {
  configSaveTimer.value = null
  try {
    await api('/api/financeiro/dnp/config', {
      method: 'PATCH',
      body: { dolar_dia: dolar.value, certificado: certificado.value },
    })
  } catch (e: any) {
    errorText.value = `Falha ao salvar config: ${e?.data?.detail?.code || 'erro'}`
  }
}

function scheduleSave(row: Produto, field: keyof Produto, value: any) {
  ;(row as any)[field] = value
  if (saveTimers[row.id]) clearTimeout(saveTimers[row.id])
  saveTimers[row.id] = setTimeout(() => {
    void persistRow(row, field)
  }, 500)
}
async function persistRow(row: Produto, field: keyof Produto) {
  delete saveTimers[row.id]
  try {
    await api(`/api/financeiro/dnp/produtos/${row.id}`, {
      method: 'PATCH',
      body: { [field]: row[field] },
    })
  } catch (e: any) {
    errorText.value = `Falha ao salvar ${String(field)}: ${e?.data?.detail?.code || 'erro'}`
  }
}

async function adicionar() {
  try {
    const r = await api<Produto>('/api/financeiro/dnp/produtos', {
      method: 'POST',
      body: { produto: '', link: '' },
    })
    rows.value = [...rows.value, r]
  } catch (e: any) {
    errorText.value = e?.data?.detail?.code || 'erro_create'
  }
}

async function excluirSelecionados() {
  if (selected.value.size === 0) return
  if (!confirm(`Excluir ${selected.value.size} produto(s)?`)) return
  const ids = [...selected.value]
  let ok = 0
  for (const id of ids) {
    try {
      await api(`/api/financeiro/dnp/produtos/${id}`, { method: 'DELETE' })
      ok++
    } catch {
      /* segue a fila — relata no fim */
    }
  }
  selected.value = new Set()
  if (ok < ids.length) errorText.value = `${ids.length - ok} falha(s) ao excluir`
  await loadProdutos()
}

// ── Cálculos ──────────────────────────────────────────────────────────
function num(v: number | null | undefined): number {
  return v == null || isNaN(Number(v)) ? 0 : Number(v)
}

// N (certificado rateado) = ((((L*O)*DOLAR)*4) + CERTIFICADO) / M
function calcCertificado(r: Produto): number | null {
  const L = num(r.valor_usd), O = num(r.fator), M = num(r.projecao_compra)
  if (!L || !O || !M) return null
  return ((((L * O) * dolar.value) * 4) + certificado.value) / M
}
// P (compra) = (L*O)*DOLAR + N
function calcCompra(r: Produto): number | null {
  const N = calcCertificado(r)
  const L = num(r.valor_usd), O = num(r.fator)
  if (N == null || !L || !O) return null
  return (L * O) * dolar.value + N
}
// T (%) = ((((Q * (1-S)) - R) / P) - 1)
function calcMargem(r: Produto): number | null {
  const Q = num(r.venda_estimada), S = num(r.comissao), R = num(r.frete)
  const P = calcCompra(r)
  if (P == null || P === 0 || !r.venda_estimada) return null
  return (((Q * (1 - S)) - R) / P) - 1
}

function fmt2(v: number | null): string {
  if (v == null) return '—'
  return v.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function fmtPct(v: number | null): string {
  if (v == null) return '—'
  return `${(v * 100).toFixed(2)}%`
}
function margemClass(v: number | null): string {
  if (v == null) return ''
  if (v > 0) return 'text-emerald-700 dark:text-emerald-400 font-semibold'
  return 'text-red-600 dark:text-red-400 font-semibold'
}

const totalRows = computed(() => rows.value.length)
</script>

<template>
  <div class="space-y-3 p-4">
    <div class="flex flex-wrap items-center gap-3">
      <div class="flex items-center gap-2">
        <h1 class="text-xl font-semibold">DNP — Desenvolvimento de Produtos</h1>
        <span class="text-xs text-muted-foreground">{{ totalRows }} {{ totalRows === 1 ? 'produto' : 'produtos' }}</span>
      </div>
      <button
        class="ml-auto inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
        :disabled="loading"
        @click="loadProdutos"
      >
        <RefreshCw class="size-3.5" :class="{ 'animate-spin': loading }" />
        Recarregar
      </button>
      <button
        v-if="canEdit"
        class="inline-flex items-center gap-1.5 rounded-md bg-primary text-primary-foreground px-3 py-1.5 text-sm hover:opacity-90"
        @click="adicionar"
      >
        <Plus class="size-3.5" /> Adicionar produto
      </button>
      <button
        v-if="canDelete && selected.size > 0"
        class="inline-flex items-center gap-1.5 rounded-md border border-destructive text-destructive px-3 py-1.5 text-sm hover:bg-destructive/10"
        @click="excluirSelecionados"
      >
        <Trash2 class="size-3.5" /> Excluir ({{ selected.size }})
      </button>
    </div>

    <!-- Config bar — Dólar (laranja) + Certificado (amarelo) -->
    <div class="flex flex-wrap items-center gap-3 border bg-muted/30 p-3 text-xs">
      <label class="inline-flex items-center gap-2">
        <span class="font-semibold uppercase tracking-wide text-[10px]">Dólar do dia</span>
        <input type="number" step="0.0001"
          class="h-7 w-28 border px-2 text-right text-sm dolar-input"
          :value="dolar" :disabled="!canEdit"
          @input="(e) => { dolar = Number((e.target as HTMLInputElement).value) || 0; scheduleConfigSave() }" />
        <button class="h-7 px-2 border rounded text-[10px] hover:bg-muted whitespace-nowrap"
          :disabled="!canEdit" @click="refreshDolar">
          ↻ awesomeapi
        </button>
        <span v-if="dolarApiStatus" class="text-[10px] text-muted-foreground">{{ dolarApiStatus }}</span>
      </label>
      <label class="inline-flex items-center gap-2 ml-4">
        <span class="font-semibold uppercase tracking-wide text-[10px]">Certificado</span>
        <input type="number" step="0.01"
          class="h-7 w-32 border px-2 text-right text-sm cert-input"
          :value="certificado" :disabled="!canEdit"
          @input="(e) => { certificado = Number((e.target as HTMLInputElement).value) || 0; scheduleConfigSave() }" />
      </label>
      <span class="ml-auto text-[10px] text-muted-foreground">
        Recalcula em tempo real ao alterar Dólar ou Certificado.
      </span>
    </div>

    <div v-if="errorText" class="text-sm text-destructive">erro: {{ errorText }}</div>

    <div class="border overflow-x-auto">
      <table class="grid-table w-full text-xs border-collapse">
        <thead>
          <tr class="bg-emerald-800 text-white text-[10px] uppercase tracking-wide">
            <th v-if="canDelete" class="w-8 text-center">
              <input type="checkbox" :checked="selected.size > 0 && selected.size === rows.length"
                @change="(e) => {
                  const c = (e.target as HTMLInputElement).checked
                  selected = new Set(c ? rows.map((r) => r.id) : [])
                }" />
            </th>
            <th class="text-left">Produto</th>
            <th class="text-left">Link</th>
            <th class="text-left">Fábrica</th>
            <th class="text-left">Modelo</th>
            <th class="text-right">MOQ</th>
            <th class="text-left">Volt.</th>
            <th class="text-left">Cor</th>
            <th class="text-left">Material</th>
            <th class="text-left">Tam.</th>
            <th class="text-left">Pot.</th>
            <th class="text-right">Valor USD</th>
            <th class="text-right">Proj. compra</th>
            <th class="text-right">Certificado</th>
            <th class="text-right">Fator</th>
            <th class="text-right">Compra</th>
            <th class="text-right">Venda est.</th>
            <th class="text-right">Frete</th>
            <th class="text-right">Comissão</th>
            <th class="text-right">%</th>
            <th class="text-left">Inmetro</th>
            <th class="text-left">Obs</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!loading && rows.length === 0">
            <td :colspan="canDelete ? 22 : 21" class="py-6 text-center text-muted-foreground">
              Nenhum produto. Clique em "Adicionar produto" para começar.
            </td>
          </tr>
          <tr v-for="row in rows" :key="row.id"
            class="even:bg-muted/10 hover:bg-amber-50/40 dark:hover:bg-amber-900/10">
            <td v-if="canDelete" class="text-center">
              <input type="checkbox" :checked="selected.has(row.id)"
                @change="(e) => {
                  const c = (e.target as HTMLInputElement).checked
                  const s = new Set(selected)
                  if (c) s.add(row.id); else s.delete(row.id)
                  selected = s
                }" />
            </td>
            <td><input class="cell-input" :value="row.produto ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave(row, 'produto', (e.target as HTMLInputElement).value)" /></td>
            <td class="link-cell">
              <a v-if="row.link" :href="row.link" target="_blank" rel="noopener noreferrer"
                class="inline-flex items-center gap-1 text-blue-600 hover:underline" :title="row.link">
                <ExternalLink class="size-3" /> link
              </a>
              <input class="cell-input mt-0.5" :value="row.link ?? ''" :disabled="!canEdit"
                placeholder="https://..."
                @input="(e) => scheduleSave(row, 'link', (e.target as HTMLInputElement).value)" />
            </td>
            <td><input class="cell-input" :value="row.fabrica ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave(row, 'fabrica', (e.target as HTMLInputElement).value)" /></td>
            <td><input class="cell-input" :value="row.modelo ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave(row, 'modelo', (e.target as HTMLInputElement).value)" /></td>
            <td><input type="number" class="cell-input text-right"
              :value="row.moq ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave(row, 'moq', Number((e.target as HTMLInputElement).value) || null)" /></td>
            <td><input class="cell-input" :value="row.voltagem ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave(row, 'voltagem', (e.target as HTMLInputElement).value)" /></td>
            <td><input class="cell-input" :value="row.cor ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave(row, 'cor', (e.target as HTMLInputElement).value)" /></td>
            <td><input class="cell-input" :value="row.material ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave(row, 'material', (e.target as HTMLInputElement).value)" /></td>
            <td><input class="cell-input" :value="row.tamanho ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave(row, 'tamanho', (e.target as HTMLInputElement).value)" /></td>
            <td><input class="cell-input" :value="row.potencia ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave(row, 'potencia', (e.target as HTMLInputElement).value)" /></td>
            <td><input type="number" step="0.01" class="cell-input text-right"
              :value="row.valor_usd ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave(row, 'valor_usd', Number((e.target as HTMLInputElement).value) || null)" /></td>
            <td><input type="number" class="cell-input text-right"
              :value="row.projecao_compra ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave(row, 'projecao_compra', Number((e.target as HTMLInputElement).value) || null)" /></td>
            <td class="calc text-right">{{ fmt2(calcCertificado(row)) }}</td>
            <td><input type="number" step="0.01" class="cell-input text-right"
              :value="row.fator ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave(row, 'fator', Number((e.target as HTMLInputElement).value) || null)" /></td>
            <td class="calc text-right">{{ fmt2(calcCompra(row)) }}</td>
            <td><input type="number" step="0.01" class="cell-input text-right"
              :value="row.venda_estimada ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave(row, 'venda_estimada', Number((e.target as HTMLInputElement).value) || null)" /></td>
            <td><input type="number" step="0.01" class="cell-input text-right"
              :value="row.frete ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave(row, 'frete', Number((e.target as HTMLInputElement).value) || null)" /></td>
            <td><input type="number" step="0.0001" class="cell-input text-right"
              :value="row.comissao ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave(row, 'comissao', Number((e.target as HTMLInputElement).value) || null)" /></td>
            <td class="calc text-right" :class="margemClass(calcMargem(row))">{{ fmtPct(calcMargem(row)) }}</td>
            <td><input class="cell-input" :value="row.inmetro ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave(row, 'inmetro', (e.target as HTMLInputElement).value)" /></td>
            <td><input class="cell-input" :value="row.obs ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave(row, 'obs', (e.target as HTMLInputElement).value)" /></td>
          </tr>
        </tbody>
      </table>
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
.grid-table thead th {
  border-color: rgba(255, 255, 255, 0.15);
  font-weight: 600;
  white-space: nowrap;
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
/* Dólar = destaque laranja; Certificado = amarelo. */
.dolar-input {
  background: rgb(254 215 170 / 0.7); /* orange-200 */
}
:global(.dark) .dolar-input {
  background: rgb(154 52 18 / 0.25);
}
.cert-input {
  background: rgb(254 252 232 / 0.8);
}
:global(.dark) .cert-input {
  background: rgb(120 53 15 / 0.2);
}
.link-cell {
  min-width: 110px;
}
</style>
