<script setup lang="ts">
// Consórcio — planilha editável inline com auto-save (debounce 500ms).
// Toda célula vira input ao clicar; campos calculados (IMPOSTO 7%,
// DEVEDOR, FINALIZADO, CRÉDITO líquido) ficam read-only.
import { computed, reactive, ref } from 'vue'
import { Plus, RefreshCw, Trash2 } from 'lucide-vue-next'

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

type Row = {
  id: string
  credito: number | null
  emp: string | null
  grupo: number | null
  cota: number | null
  alienacao: string | null
  nf: string | null
  parc_a_pagar: number | null
  lance: number | null
  valor_parc: number | null
  atualizado: string | null  // YYYY-MM-DD
  fundo_reserva: string | null
  obs: string | null
}

const rows = ref<Row[]>([])
const loading = ref(false)
const errorText = ref<string | null>(null)
// id -> timer pendente do auto-save. Permite cancelar quando o user
// digita rápido na mesma célula.
const saveTimers = reactive<Record<string, ReturnType<typeof setTimeout>>>({})

const ALIENACAO_OPTIONS = ['', 'pago', 'ag. repasse', 'a contemplar']

async function load() {
  loading.value = true
  errorText.value = null
  try {
    rows.value = await api<Row[]>('/api/financeiro/consorcio')
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
    const body: Record<string, any> = { [field]: row[field] }
    await api(`/api/financeiro/consorcio/${row.id}`, { method: 'PATCH', body })
  } catch (e: any) {
    errorText.value = `Falha ao salvar ${String(field)}: ${e?.data?.detail?.code || 'erro'}`
  }
}

async function addRow() {
  try {
    const r = await api<Row>('/api/financeiro/consorcio', {
      method: 'POST',
      body: { emp: '', alienacao: '' },
    })
    rows.value = [r, ...rows.value]
  } catch (e: any) {
    errorText.value = e?.data?.detail?.code || 'erro_create'
  }
}

async function removeRow(row: Row) {
  if (!confirm('Excluir esta linha?')) return
  try {
    await api(`/api/financeiro/consorcio/${row.id}`, { method: 'DELETE' })
    rows.value = rows.value.filter((r) => r.id !== row.id)
  } catch (e: any) {
    errorText.value = e?.data?.detail?.code || 'erro_delete'
  }
}

// ── Calculadas ────────────────────────────────────────────────────────
function numOrZero(v: number | null | undefined): number {
  return v == null || isNaN(Number(v)) ? 0 : Number(v)
}

function imposto7(row: Row): number | null {
  if (!row.lance || numOrZero(row.lance) <= 0) return null
  return numOrZero(row.credito) * 0.07
}
function devedor(row: Row): number {
  return numOrZero(row.parc_a_pagar) * numOrZero(row.valor_parc)
}
function finalizado(row: Row): string | null {
  if (!row.atualizado || !row.parc_a_pagar) return null
  const [y, m, d] = row.atualizado.split('-').map(Number)
  if (!y || !m || !d) return null
  const dt = new Date(y, m - 1, d)
  dt.setDate(dt.getDate() + row.parc_a_pagar * 30)
  return dt.toLocaleDateString('pt-BR')
}
function creditoLiquido(row: Row): number | null {
  if (!row.lance || numOrZero(row.lance) <= 0) return null
  return numOrZero(row.credito) - (numOrZero(row.lance) + (imposto7(row) ?? 0))
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
        <h1 class="text-xl font-semibold">Consórcio</h1>
        <span class="text-xs text-muted-foreground">{{ totalRows }} {{ totalRows === 1 ? 'linha' : 'linhas' }}</span>
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
        <Plus class="size-3.5" /> Nova linha
      </button>
    </div>

    <div v-if="errorText" class="text-sm text-destructive">erro: {{ errorText }}</div>

    <div class="border rounded-md overflow-x-auto">
      <table class="grid-table w-full text-xs border-collapse">
        <thead>
          <tr class="bg-muted/40 text-[10px] uppercase tracking-wide">
            <th class="text-right">Crédito</th>
            <th class="text-left">EMP</th>
            <th class="text-right">Grupo</th>
            <th class="text-right">Cota</th>
            <th class="text-left">Alienação</th>
            <th class="text-left">NF</th>
            <th class="text-right">Parc a pagar</th>
            <th class="text-right">Lance</th>
            <th class="text-right calc">Imposto 7%</th>
            <th class="text-right">Valor parc.</th>
            <th class="text-right calc">Devedor</th>
            <th class="text-left">Atualizado</th>
            <th class="text-left calc">Finalizado</th>
            <th class="text-right calc">Crédito líq.</th>
            <th class="text-left">Fundo reserva</th>
            <th class="text-left">Obs</th>
            <th v-if="canDelete" class="w-8"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!loading && rows.length === 0">
            <td :colspan="canDelete ? 17 : 16" class="py-6 text-center text-muted-foreground">
              Nenhuma linha. Clique em "Nova linha" para começar.
            </td>
          </tr>
          <tr v-for="row in rows" :key="row.id" class="hover:bg-muted/20">
            <td>
              <input type="number" step="0.01" class="cell-input text-right"
                :value="row.credito ?? ''" :disabled="!canEdit"
                @input="(e) => scheduleSave(row, 'credito', Number((e.target as HTMLInputElement).value) || null)" />
            </td>
            <td>
              <input class="cell-input" :value="row.emp ?? ''" :disabled="!canEdit"
                @input="(e) => scheduleSave(row, 'emp', (e.target as HTMLInputElement).value)" />
            </td>
            <td>
              <input type="number" class="cell-input text-right"
                :value="row.grupo ?? ''" :disabled="!canEdit"
                @input="(e) => scheduleSave(row, 'grupo', Number((e.target as HTMLInputElement).value) || null)" />
            </td>
            <td>
              <input type="number" class="cell-input text-right"
                :value="row.cota ?? ''" :disabled="!canEdit"
                @input="(e) => scheduleSave(row, 'cota', Number((e.target as HTMLInputElement).value) || null)" />
            </td>
            <td>
              <select class="cell-input" :value="row.alienacao ?? ''" :disabled="!canEdit"
                @change="(e) => scheduleSave(row, 'alienacao', (e.target as HTMLSelectElement).value)">
                <option v-for="o in ALIENACAO_OPTIONS" :key="o" :value="o">{{ o || '—' }}</option>
              </select>
            </td>
            <td>
              <input class="cell-input" :value="row.nf ?? ''" :disabled="!canEdit"
                @input="(e) => scheduleSave(row, 'nf', (e.target as HTMLInputElement).value)" />
            </td>
            <td>
              <input type="number" class="cell-input text-right"
                :value="row.parc_a_pagar ?? ''" :disabled="!canEdit"
                @input="(e) => scheduleSave(row, 'parc_a_pagar', Number((e.target as HTMLInputElement).value) || null)" />
            </td>
            <td>
              <input type="number" step="0.01" class="cell-input text-right"
                :value="row.lance ?? ''" :disabled="!canEdit"
                @input="(e) => scheduleSave(row, 'lance', Number((e.target as HTMLInputElement).value) || null)" />
            </td>
            <td class="calc text-right">{{ fmtMoney(imposto7(row)) }}</td>
            <td>
              <input type="number" step="0.01" class="cell-input text-right"
                :value="row.valor_parc ?? ''" :disabled="!canEdit"
                @input="(e) => scheduleSave(row, 'valor_parc', Number((e.target as HTMLInputElement).value) || null)" />
            </td>
            <td class="calc text-right">{{ fmtMoney(devedor(row)) }}</td>
            <td>
              <input type="date" class="cell-input" :value="row.atualizado ?? ''" :disabled="!canEdit"
                @input="(e) => scheduleSave(row, 'atualizado', (e.target as HTMLInputElement).value || null)" />
            </td>
            <td class="calc">{{ finalizado(row) ?? '—' }}</td>
            <td class="calc text-right">{{ fmtMoney(creditoLiquido(row)) }}</td>
            <td>
              <input class="cell-input" :value="row.fundo_reserva ?? ''" :disabled="!canEdit"
                placeholder="ex: 2.5%"
                @input="(e) => scheduleSave(row, 'fundo_reserva', (e.target as HTMLInputElement).value)" />
            </td>
            <td>
              <input class="cell-input" :value="row.obs ?? ''" :disabled="!canEdit"
                @input="(e) => scheduleSave(row, 'obs', (e.target as HTMLInputElement).value)" />
            </td>
            <td v-if="canDelete" class="text-center">
              <button class="text-muted-foreground hover:text-destructive" :title="'Excluir linha'" @click="removeRow(row)">
                <Trash2 class="size-3.5" />
              </button>
            </td>
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
}
.grid-table th.calc,
.grid-table td.calc {
  background-color: hsl(var(--muted) / 0.4);
  color: hsl(var(--muted-foreground));
  font-style: italic;
}
.cell-input {
  width: 100%;
  border: 0;
  background: transparent;
  padding: 2px 4px;
  font-size: 11px;
  color: inherit;
}
.cell-input:focus {
  outline: 1px solid hsl(var(--primary));
  background: hsl(var(--background));
}
.cell-input:disabled {
  cursor: not-allowed;
  opacity: 0.7;
}
</style>
