<script setup lang="ts">
// Suprimentos — certificações (Anatel/Inmetro/isento) com alerta visual
// de validade: linha âmbar quando faltam < 30 dias, vermelha quando já
// venceu. Auto-save inline, mesmo padrão da página Consórcio.
import { computed, onBeforeUnmount, reactive, ref } from 'vue'
import { Download, FileDown, Paperclip, Plus, RefreshCw, Trash2 } from 'lucide-vue-next'
import { createCertificacoesAutosave } from '~/utils/certificacoesAutosave'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'financeiro_suprimentos', action: 'view' },
})

const { api, url } = useApi()
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
  tem_pdf: boolean
  pdf_nome: string | null
}

const rows = ref<Row[]>([])
const loading = ref(false)
const errorText = ref<string | null>(null)
const exporting = ref(false)
const adding = ref(false)
const rowBusy = reactive<Record<string, boolean>>({})
const busy = computed(() => loading.value || exporting.value || adding.value)
const hasBusyRow = computed(() => Object.values(rowBusy).some(Boolean))
const canAttach = computed(() => auth.isAdmin || Boolean(auth.user?.permissions?.financeiro_suprimentos?.edit))
const autosave = createCertificacoesAutosave(
  (id, patch) => api(`/api/financeiro/suprimentos/${id}`, { method: 'PATCH', body: patch }),
  () => { errorText.value = 'Não foi possível salvar as alterações. Tente novamente antes de baixar o PDF.' },
)
onBeforeUnmount(() => { void autosave.flush().catch(() => {}) })

const CERT_OPTIONS = ['', 'anatel', 'inmetro', 'isento']

async function load() {
  loading.value = true
  errorText.value = null
  try {
    await autosave.flush()
    rows.value = await api<Row[]>('/api/financeiro/suprimentos')
  } catch (e: any) {
    errorText.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    loading.value = false
  }
}
await load()

function scheduleSave(row: Row, field: keyof Row, value: any) {
  ;(row as any)[field] = value
  autosave.schedule(row.id, field, value)
}

async function addRow() {
  if (busy.value) return
  adding.value = true
  try {
    const r = await api<Row>('/api/financeiro/suprimentos', {
      method: 'POST',
      body: { certificado: '' },
    })
    rows.value = [r, ...rows.value]
  } catch (e: any) {
    errorText.value = e?.data?.detail?.code || 'erro_create'
  } finally {
    adding.value = false
  }
}
async function removeRow(row: Row) {
  if (busy.value || rowBusy[row.id]) return
  if (!confirm('Excluir esta certificação?')) return
  rowBusy[row.id] = true
  try {
    await autosave.flushRow(row.id)
    await api(`/api/financeiro/suprimentos/${row.id}`, { method: 'DELETE' })
    rows.value = rows.value.filter((r) => r.id !== row.id)
  } catch (e: any) {
    errorText.value = e?.data?.detail?.code || 'erro_delete'
  } finally {
    delete rowBusy[row.id]
  }
}

async function downloadPdf(path: string, filename: string) {
  const response = await fetch(url(path), { credentials: 'include' })
  if (!response.ok) throw new Error('Não foi possível baixar o PDF. Tente novamente.')
  const blob = await response.blob()
  const objectUrl = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = objectUrl
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000)
}

async function exportTable() {
  if (busy.value || hasBusyRow.value) return
  exporting.value = true
  errorText.value = null
  try {
    try {
      await autosave.flush()
    } catch {
      throw new Error('As alterações não foram salvas. Tente baixar o PDF novamente.')
    }
    await downloadPdf('/api/financeiro/suprimentos/pdf', 'certificacoes.pdf')
  } catch (e: any) {
    errorText.value = e?.message || 'Não foi possível gerar o PDF.'
  } finally {
    exporting.value = false
  }
}

async function downloadCertificate(row: Row) {
  if (busy.value || rowBusy[row.id]) return
  rowBusy[row.id] = true
  errorText.value = null
  try {
    await downloadPdf(`/api/financeiro/suprimentos/${row.id}/pdf`, row.pdf_nome || 'certificado.pdf')
  } catch (e: any) {
    errorText.value = e?.message || 'Não foi possível baixar o certificado.'
  } finally {
    delete rowBusy[row.id]
  }
}

async function attachPdf(row: Row, event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file || busy.value || rowBusy[row.id]) return
  if (file.size > 8 * 1024 * 1024) {
    errorText.value = 'O PDF deve ter no máximo 8 MB.'
    return
  }
  rowBusy[row.id] = true
  errorText.value = null
  try {
    const body = new FormData()
    body.append('file', file)
    const updated = await api<Row>(`/api/financeiro/suprimentos/${row.id}/pdf`, { method: 'POST', body })
    // Preserve edits still waiting for inline autosave.
    row.tem_pdf = updated.tem_pdf
    row.pdf_nome = updated.pdf_nome
  } catch (e: any) {
    const code = e?.data?.detail?.code
    errorText.value = code === 'pdf_too_large' ? 'O PDF deve ter no máximo 8 MB.'
      : code === 'invalid_pdf' ? 'Selecione um arquivo PDF válido e não vazio.'
        : code === 'pdf_encrypted' ? 'Selecione um PDF sem senha.'
          : 'Não foi possível anexar o PDF. Tente novamente.'
  } finally {
    delete rowBusy[row.id]
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

const totalRows = computed(() => rows.value.length)
</script>

<template>
  <div class="space-y-3 p-4">
    <div class="flex flex-wrap items-center gap-3">
      <div class="flex items-center gap-2">
        <h1 class="text-xl font-semibold">Certificações</h1>
        <span class="text-xs text-muted-foreground">{{ totalRows }} {{ totalRows === 1 ? 'item' : 'itens' }}</span>
      </div>
      <div class="text-[10px] text-muted-foreground inline-flex items-center gap-3 ml-2">
        <span class="inline-flex items-center gap-1"><span class="size-2 rounded bg-amber-400"></span> &lt; 30 dias</span>
        <span class="inline-flex items-center gap-1"><span class="size-2 rounded bg-red-500"></span> vencido</span>
      </div>
      <button
        class="ml-auto inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
        :disabled="busy || hasBusyRow"
        @click="load"
      >
        <RefreshCw class="size-3.5" :class="{ 'animate-spin': loading }" />
        Recarregar
      </button>
      <button
        class="inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
        :disabled="busy || hasBusyRow"
        @click="exportTable"
      >
        <FileDown class="size-3.5" /> {{ exporting ? 'Gerando PDF...' : 'Baixar tabela em PDF' }}
      </button>
      <button
        v-if="canEdit"
        class="inline-flex items-center gap-1.5 rounded-md bg-primary text-primary-foreground px-3 py-1.5 text-sm hover:opacity-90 disabled:opacity-50"
        :disabled="busy"
        @click="addRow"
      >
        <Plus class="size-3.5" /> Nova certificação
      </button>
    </div>

    <div v-if="errorText" role="alert" class="text-sm text-destructive">{{ errorText }}</div>

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
            <th class="text-left">PDF do certificado</th>
            <th v-if="canDelete" class="w-8"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!loading && rows.length === 0">
            <td :colspan="canDelete ? 10 : 9" class="py-6 text-center text-muted-foreground">
              Nenhuma certificação. Clique em "Nova certificação" para começar.
            </td>
          </tr>
          <tr v-for="row in rows" :key="row.id"
            class="even:bg-muted/10 hover:bg-amber-50/40 dark:hover:bg-amber-900/10"
            :class="rowStatusClass(row)">
            <td>
              <input class="cell-input" :value="row.produto ?? ''" :disabled="!canEdit || busy || rowBusy[row.id]"
                @input="(e) => scheduleSave(row, 'produto', (e.target as HTMLInputElement).value)" />
            </td>
            <td>
              <input class="cell-input" :value="row.modelo ?? ''" :disabled="!canEdit || busy || rowBusy[row.id]"
                @input="(e) => scheduleSave(row, 'modelo', (e.target as HTMLInputElement).value)" />
            </td>
            <td>
              <input class="cell-input" :value="row.nome_comercial ?? ''" :disabled="!canEdit || busy || rowBusy[row.id]"
                @input="(e) => scheduleSave(row, 'nome_comercial', (e.target as HTMLInputElement).value)" />
            </td>
            <td>
              <select class="cell-input" :value="row.certificado ?? ''" :disabled="!canEdit || busy || rowBusy[row.id]"
                @change="(e) => scheduleSave(row, 'certificado', (e.target as HTMLSelectElement).value)">
                <option v-for="o in CERT_OPTIONS" :key="o" :value="o">{{ o || '—' }}</option>
              </select>
            </td>
            <td>
              <input class="cell-input" :value="row.numero ?? ''" :disabled="!canEdit || busy || rowBusy[row.id]"
                @input="(e) => scheduleSave(row, 'numero', (e.target as HTMLInputElement).value)" />
            </td>
            <td>
              <input type="number" step="0.01" class="cell-input text-right"
                :value="row.valor ?? ''" :disabled="!canEdit || busy || rowBusy[row.id]"
                @input="(e) => scheduleSave(row, 'valor', (e.target as HTMLInputElement).value === '' ? null : Number((e.target as HTMLInputElement).value))" />
            </td>
            <td>
              <input type="date" class="cell-input" :value="row.inicio ?? ''" :disabled="!canEdit || busy || rowBusy[row.id]"
                @input="(e) => scheduleSave(row, 'inicio', (e.target as HTMLInputElement).value || null)" />
            </td>
            <td>
              <input type="date" class="cell-input" :value="row.fim ?? ''" :disabled="!canEdit || busy || rowBusy[row.id]"
                @input="(e) => scheduleSave(row, 'fim', (e.target as HTMLInputElement).value || null)" />
            </td>
            <td>
              <div class="flex items-center gap-2">
                <button v-if="row.tem_pdf" class="inline-flex items-center gap-1 text-primary hover:underline disabled:opacity-50"
                  :disabled="busy || rowBusy[row.id]" :title="row.pdf_nome || 'Baixar certificado'"
                  :aria-label="`Baixar PDF de ${row.produto || 'certificado'}`" @click="downloadCertificate(row)">
                  <Download class="size-3.5" /> Baixar
                </button>
                <label v-if="canAttach" class="relative inline-flex items-center gap-1 text-primary cursor-pointer hover:underline rounded focus-within:ring-1 focus-within:ring-primary"
                  :class="{ 'opacity-50 pointer-events-none': busy || rowBusy[row.id] }"
                  :title="row.tem_pdf ? 'Substituir o PDF anexado (até 8 MB)' : 'Anexar PDF (até 8 MB)'">
                  <Paperclip class="size-3.5" />
                  {{ rowBusy[row.id] ? 'Aguarde...' : row.tem_pdf ? 'Substituir' : 'Anexar' }}
                  <input type="file" accept=".pdf,application/pdf" class="absolute inset-0 opacity-0 w-full cursor-pointer"
                    :aria-label="`${row.tem_pdf ? 'Substituir' : 'Anexar'} PDF de ${row.produto || 'certificado'}`"
                    :disabled="busy || rowBusy[row.id]" @change="attachPdf(row, $event)" />
                </label>
                <span v-else-if="!row.tem_pdf" class="text-muted-foreground">Sem PDF</span>
              </div>
            </td>
            <td v-if="canDelete" class="text-center">
              <button class="text-muted-foreground hover:text-destructive disabled:opacity-50" :disabled="busy || rowBusy[row.id]"
                aria-label="Excluir certificação" @click="removeRow(row)">
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
