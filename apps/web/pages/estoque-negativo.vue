<script setup lang="ts">
// Estoque negativo + sufixos — migrado do xml-up. Operador usa antes
// de gerar etiquetas pra identificar SKUs com saldo virtual < 0
// (precisam sair da fila de envio) ou pra exportar listas filtradas
// por sufixo de tag.
//
// Backend lê de products.saldo_fisico + products.saldo_virtual_total,
// que são populados por /atualizar-bling (refresh direto da API Bling
// — distinto de stock/reserved_stock, que vêm de webhooks).
import { computed, onMounted, ref } from 'vue'
import { AlertTriangle, Download, Loader2, RefreshCw, Search } from 'lucide-vue-next'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'controle_estoque', action: 'view' },
})

const { api } = useApi()
const auth = useAuthStore()
const canEdit = computed(() => {
  if (auth.isAdmin) return true
  const p = auth.user?.permissions?.controle_estoque
  return Boolean(p?.edit || p?.delete)
})

type Row = { codigo: string; saldo_fisico: number; saldo_virtual_total: number }

const negativos = ref<Row[]>([])
const sufixos = ref<Row[]>([])
const loading = ref(false)
const refreshing = ref(false)
const refreshError = ref<string | null>(null)
const search = ref('')
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
  try {
    const q = search.value.trim()
    const r = await api<{ items: Row[] }>(
      `/api/estoque/negativos${q ? `?search=${encodeURIComponent(q)}` : ''}`,
    )
    negativos.value = r.items || []
  } catch (e: any) {
    refreshError.value = `Falha negativos: ${e?.data?.detail?.code || e?.message || 'erro'}`
  }
}

async function loadSufixos() {
  const sufs = effectiveSuffixes.value
  if (!sufs) {
    sufixos.value = []
    return
  }
  try {
    const r = await api<{ items: Row[] }>(
      `/api/estoque/sufixos?suffixes=${encodeURIComponent(sufs)}`,
    )
    sufixos.value = r.items || []
  } catch (e: any) {
    refreshError.value = `Falha sufixos: ${e?.data?.detail?.code || e?.message || 'erro'}`
  }
}

async function reloadAll() {
  loading.value = true
  refreshError.value = null
  await Promise.all([loadNegativos(), loadSufixos()])
  loading.value = false
}

async function atualizarBling() {
  if (!canEdit.value) return
  refreshing.value = true
  refreshError.value = null
  try {
    const r = await api<{
      updated: number; total_products: number; missing_bling_data?: number
    }>('/api/estoque/atualizar-bling', { method: 'POST' })
    refreshError.value = `Atualizou ${r.updated}/${r.total_products} produtos` +
      (r.missing_bling_data ? ` (${r.missing_bling_data} sem dado Bling)` : '')
    await reloadAll()
  } catch (e: any) {
    const code = e?.data?.detail?.code
    if (code === 'refresh_already_running') {
      refreshError.value = `Outro refresh em andamento (iniciado ${e?.data?.detail?.started_at})`
    } else if (code === 'bling_not_connected') {
      refreshError.value = 'Bling não conectado'
    } else {
      refreshError.value = e?.message || 'falha desconhecida'
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

onMounted(() => { void reloadAll() })
</script>

<template>
  <div class="space-y-4 p-4">
    <div class="flex flex-wrap items-center gap-3">
      <h1 class="text-xl font-semibold flex items-center gap-2">
        <AlertTriangle class="size-5 text-amber-600" /> Estoque Negativo
      </h1>
      <button
        class="ml-auto inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
        :disabled="loading || refreshing"
        @click="reloadAll"
      >
        <RefreshCw class="size-3.5" :class="{ 'animate-spin': loading }" /> Recarregar
      </button>
      <button
        v-if="canEdit"
        class="inline-flex items-center gap-1.5 rounded-md bg-primary text-primary-foreground px-3 py-1.5 text-sm hover:opacity-90 disabled:opacity-50"
        :disabled="refreshing"
        :title="'Consulta GET /estoques/saldos no Bling e atualiza saldo_fisico/saldo_virtual_total'"
        @click="atualizarBling"
      >
        <Loader2 v-if="refreshing" class="size-3.5 animate-spin" />
        <RefreshCw v-else class="size-3.5" />
        {{ refreshing ? 'Atualizando…' : 'Atualizar do Bling' }}
      </button>
    </div>

    <div v-if="refreshError" class="text-sm rounded border bg-muted/30 px-3 py-2">
      {{ refreshError }}
    </div>

    <!-- Negativos -->
    <section class="space-y-2">
      <div class="flex items-center gap-2">
        <h2 class="font-semibold text-sm">SKUs com saldo virtual &lt; 0</h2>
        <span class="text-xs text-muted-foreground">{{ negativos.length }} SKU(s)</span>
        <div class="relative ml-auto min-w-[200px]">
          <Search class="absolute left-2 top-1/2 -translate-y-1/2 size-3 text-muted-foreground" />
          <input
            v-model="search"
            type="search"
            placeholder="Buscar SKU…"
            class="h-7 w-full border rounded pl-7 pr-2 text-xs bg-background"
            @input="loadNegativos"
          />
        </div>
      </div>
      <div class="border rounded-md overflow-x-auto">
        <table class="grid-table w-full text-xs border-collapse">
          <thead>
            <tr class="bg-emerald-800 text-white text-[10px] uppercase tracking-wide">
              <th class="text-left">SKU</th>
              <th class="text-right">Saldo Físico</th>
              <th class="text-right">Saldo Virtual</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="negativos.length === 0 && !loading">
              <td colspan="3" class="py-6 text-center text-muted-foreground">
                Nenhum SKU com saldo virtual negativo.
              </td>
            </tr>
            <tr v-for="row in negativos" :key="row.codigo" class="even:bg-muted/10">
              <td class="font-mono">{{ row.codigo }}</td>
              <td class="text-right">{{ row.saldo_fisico }}</td>
              <td class="text-right font-semibold text-red-700">{{ row.saldo_virtual_total }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- Sufixos -->
    <section class="space-y-2">
      <div class="flex flex-wrap items-center gap-2">
        <h2 class="font-semibold text-sm">SKUs por sufixo (apenas saldo físico &gt; 0)</h2>
        <span class="text-xs text-muted-foreground">{{ sufixos.length }} SKU(s)</span>
        <label class="inline-flex items-center gap-1 text-xs">
          Sufixo:
          <select v-model="suffixChoice" class="h-7 border rounded px-2 bg-background" @change="loadSufixos">
            <option v-for="opt in SUFFIX_PRESETS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
          </select>
        </label>
        <input
          v-if="suffixChoice === '__custom__'"
          v-model="customSuffix"
          placeholder="ex: .us,.sa,.ra"
          class="h-7 border rounded px-2 text-xs bg-background"
          @blur="loadSufixos"
        />
        <button
          class="ml-auto inline-flex items-center gap-1 rounded-md border px-2.5 py-1 text-xs hover:bg-muted disabled:opacity-50"
          :disabled="sufixos.length === 0"
          @click="downloadCsv"
        >
          <Download class="size-3" /> CSV
        </button>
      </div>
      <div class="border rounded-md overflow-x-auto">
        <table class="grid-table w-full text-xs border-collapse">
          <thead>
            <tr class="bg-emerald-800 text-white text-[10px] uppercase tracking-wide">
              <th class="text-left">SKU</th>
              <th class="text-right">Saldo Físico</th>
              <th class="text-right">Saldo Virtual</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="sufixos.length === 0 && !loading">
              <td colspan="3" class="py-6 text-center text-muted-foreground">
                Nenhum SKU encontrado para os sufixos selecionados.
              </td>
            </tr>
            <tr v-for="row in sufixos" :key="row.codigo" class="even:bg-muted/10">
              <td class="font-mono">{{ row.codigo }}</td>
              <td class="text-right font-semibold text-emerald-700">{{ row.saldo_fisico }}</td>
              <td class="text-right">{{ row.saldo_virtual_total }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<style scoped>
.grid-table th,
.grid-table td {
  border: 1px solid hsl(var(--border));
  padding: 4px 6px;
}
.grid-table thead th {
  border-color: rgba(255, 255, 255, 0.15);
  font-weight: 600;
  white-space: nowrap;
}
</style>
