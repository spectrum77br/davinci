<script setup lang="ts">
// Suprimentos — certificações (Anatel/Inmetro/isento) com alerta visual
// de validade: linha âmbar quando faltam < 30 dias, vermelha quando já
// venceu. Auto-save inline, mesmo padrão da página Consórcio.
import { computed, reactive, ref } from 'vue'
import { Plus, RefreshCw, Trash2 } from 'lucide-vue-next'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'financeiro_suprimentos', action: 'view' },
})

const { api } = useApi()
const auth = useAuthStore()
const canEdit = computed(() => {
  if (auth.isAdmin) return true
  const p = auth.user?.permissions?.financeiro_suprimentos
  return Boolean(p?.edit || p?.delete)
})
const canDelete = computed(() => {
  if (auth.isAdmin) return true
  return Boolean(auth.user?.permissions?.financeiro_suprimentos?.delete)
})

type Row = {
  id: string
  produto: string | null
  modelo: string | null
  nome_comercial: string | null
  certificado: string | null
  numero: string | null
  valor: number | null
  inicio: string | null  // YYYY-MM-DD
  fim: string | null
}

const rows = ref<Row[]>([])
const loading = ref(false)
const errorText = ref<string | null>(null)
const saveTimers = reactive<Record<string, ReturnType<typeof setTimeout>>>({})

const CERT_OPTIONS = ['', 'anatel', 'inmetro', 'isento']

async function load() {
  loading.value = true
  errorText.value = null
  try {
    rows.value = await api<Row[]>('/api/financeiro/suprimentos')
  } catch (e: any) {
    errorText.value = e?.data?.detail?.code || e?.message || 'erro'
    rows.value = []
  } finally {
    loading.value = false
  }
}
await load()

function scheduleSave(row: Row, field: keyof Row, value: any) {
  ;(row as any)[field] = value
  if (saveTimers[row.id]) clearTimeout(saveTimers[row.id])
  saveTimers[row.id] = setTimeout(() => {
    void persist(row, field)
  }, 500)
}
async function persist(row: Row, field: keyof Row) {
  delete saveTimers[row.id]
  try {
    await api(`/api/financeiro/suprimentos/${row.id}`, {
      method: 'PATCH',
      body: { [field]: row[field] },
    })
  } catch (e: any) {
    errorText.value = `Falha ao salvar ${String(field)}: ${e?.data?.detail?.code || 'erro'}`
  }
}

async function addRow() {
  try {
    const r = await api<Row>('/api/financeiro/suprimentos', {
      method: 'POST',
      body: { certificado: '' },
    })
    rows.value = [r, ...rows.value]
  } catch (e: any) {
    errorText.value = e?.data?.detail?.code || 'erro_create'
  }
}
async function removeRow(row: Row) {
  if (!confirm('Excluir esta certificação?')) return
  try {
    await api(`/api/financeiro/suprimentos/${row.id}`, { method: 'DELETE' })
    rows.value = rows.value.filter((r) => r.id !== row.id)
  } catch (e: any) {
    errorText.value = e?.data?.detail?.code || 'erro_delete'
  }
}

// ── Status de validade ────────────────────────────────────────────────
function daysUntil(dateStr: string | null): number | null {
  if (!dateStr) return null
  const [y, m, d] = dateStr.split('-').map(Number)
  if (!y || !m || !d) return null
  const target = new Date(y, m - 1, d).getTime()
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  return Math.floor((target - today.getTime()) / (24 * 60 * 60 * 1000))
}

function rowStatusClass(row: Row): string {
  const d = daysUntil(row.fim)
  if (d == null) return ''
  if (d < 0) return 'bg-red-100/60 dark:bg-red-900/30'
  if (d < 30) return 'bg-amber-100/60 dark:bg-amber-900/30'
  return ''
}

function fmtMoney(n: number | null): string {
  if (n == null) return ''
  return n.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
}

const totalRows = computed(() => rows.value.length)
</script>

<template>
  <div class="space-y-3 p-4">
    <div class="flex flex-wrap items-center gap-3">
      <div class="flex items-center gap-2">
        <h1 class="text-xl font-semibold">Suprimentos — Certificações</h1>
        <span class="text-xs text-muted-foreground">{{ totalRows }} {{ totalRows === 1 ? 'item' : 'itens' }}</span>
      </div>
      <div class="text-[10px] text-muted-foreground inline-flex items-center gap-3 ml-2">
        <span class="inline-flex items-center gap-1"><span class="size-2 rounded bg-amber-400"></span> &lt; 30 dias</span>
        <span class="inline-flex items-center gap-1"><span class="size-2 rounded bg-red-500"></span> vencido</span>
      </div>
      <button
        class="ml-auto inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
        :disabled="loading"
        @click="load"
      >
        <RefreshCw class="size-3.5" :class="{ 'animate-spin': loading }" />
        Recarregar
      </button>
      <button
        v-if="canEdit"
        class="inline-flex items-center gap-1.5 rounded-md bg-primary text-primary-foreground px-3 py-1.5 text-sm hover:opacity-90"
        @click="addRow"
      >
        <Plus class="size-3.5" /> Nova certificação
      </button>
    </div>

    <div v-if="errorText" class="text-sm text-destructive">erro: {{ errorText }}</div>

    <div class="border overflow-x-auto">
      <table class="grid-table w-full text-xs border-collapse">
        <thead>
          <tr class="bg-emerald-800 text-white text-[10px] uppercase tracking-wide">
            <th class="text-left">Produto</th>
            <th class="text-left">Modelo</th>
            <th class="text-left">Nome comercial</th>
            <th class="text-left">Certificado</th>
            <th class="text-left">Número</th>
            <th class="text-right">Valor</th>
            <th class="text-left">Início</th>
            <th class="text-left">Fim</th>
            <th v-if="canDelete" class="w-8"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!loading && rows.length === 0">
            <td :colspan="canDelete ? 9 : 8" class="py-6 text-center text-muted-foreground">
              Nenhuma certificação. Clique em "Nova certificação" para começar.
            </td>
          </tr>
          <tr v-for="row in rows" :key="row.id"
            class="even:bg-muted/10 hover:bg-amber-50/40 dark:hover:bg-amber-900/10"
            :class="rowStatusClass(row)">
            <td>
              <input class="cell-input" :value="row.produto ?? ''" :disabled="!canEdit"
                @input="(e) => scheduleSave(row, 'produto', (e.target as HTMLInputElement).value)" />
            </td>
            <td>
              <input class="cell-input" :value="row.modelo ?? ''" :disabled="!canEdit"
                @input="(e) => scheduleSave(row, 'modelo', (e.target as HTMLInputElement).value)" />
            </td>
            <td>
              <input class="cell-input" :value="row.nome_comercial ?? ''" :disabled="!canEdit"
                @input="(e) => scheduleSave(row, 'nome_comercial', (e.target as HTMLInputElement).value)" />
            </td>
            <td>
              <select class="cell-input" :value="row.certificado ?? ''" :disabled="!canEdit"
                @change="(e) => scheduleSave(row, 'certificado', (e.target as HTMLSelectElement).value)">
                <option v-for="o in CERT_OPTIONS" :key="o" :value="o">{{ o || '—' }}</option>
              </select>
            </td>
            <td>
              <input class="cell-input" :value="row.numero ?? ''" :disabled="!canEdit"
                @input="(e) => scheduleSave(row, 'numero', (e.target as HTMLInputElement).value)" />
            </td>
            <td>
              <input type="number" step="0.01" class="cell-input text-right"
                :value="row.valor ?? ''" :disabled="!canEdit"
                @input="(e) => scheduleSave(row, 'valor', Number((e.target as HTMLInputElement).value) || null)" />
            </td>
            <td>
              <input type="date" class="cell-input" :value="row.inicio ?? ''" :disabled="!canEdit"
                @input="(e) => scheduleSave(row, 'inicio', (e.target as HTMLInputElement).value || null)" />
            </td>
            <td>
              <input type="date" class="cell-input" :value="row.fim ?? ''" :disabled="!canEdit"
                @input="(e) => scheduleSave(row, 'fim', (e.target as HTMLInputElement).value || null)" />
            </td>
            <td v-if="canDelete" class="text-center">
              <button class="text-muted-foreground hover:text-destructive" @click="removeRow(row)">
                <Trash2 class="size-3.5" />
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p class="text-[10px] text-muted-foreground">Valor: {{ fmtMoney(rows.reduce((a, r) => a + (r.valor || 0), 0)) }} total</p>
  </div>
</template>

<style scoped>
/* Mesmo visual do Consórcio: header verde, células compactas, inputs
   editáveis com fundo amarelo claro. */
.grid-table th,
.grid-table td {
  border: 1px solid hsl(var(--border));
  padding: 2px 5px;
  white-space: nowrap;
}
.grid-table thead th {
  border-color: rgba(255, 255, 255, 0.15);
  font-weight: 600;
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
</style>
