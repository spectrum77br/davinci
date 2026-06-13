<script setup lang="ts">
import {
  AlertCircle,
  CheckCircle2,
  Download,
  FileArchive,
  Loader2,
  Search,
  XCircle,
} from 'lucide-vue-next'
import { isoToday } from '~/lib/date'

definePageMeta({ middleware: ['permission'], permission: { resource: 'notas_fiscais', action: 'view' } })

type Conta = { id: string; nome: string }

type NotaRow = {
  conta: string
  bling_id: number
  numero: string | null
  data_emissao: string | null
  data_operacao: string | null
  tipo: string | null
  situacao: string | null
  cliente: string | null
  documento: string | null
  valor: number | null
}

type NotasPage = {
  items: NotaRow[]
  total: number
  erros: string[]
}

const { api } = useApi()

const contas = ref<Conta[]>([])
const selected = ref<Set<string>>(new Set())
const contasLoading = ref(true)

function firstOfMonth(): string {
  return `${isoToday().slice(0, 8)}01`
}

const dateFrom = ref(firstOfMonth())
const dateTo = ref(isoToday())

const items = ref<NotaRow[]>([])
const total = ref(0)
const erros = ref<string[]>([])
const searched = ref(false)
const loading = ref(false)
const exportingXml = ref(false)
const exportingXlsx = ref(false)
const error = ref<string | null>(null)

const allSelected = computed(
  () => contas.value.length > 0 && selected.value.size === contas.value.length,
)
const canQuery = computed(
  () => selected.value.size > 0 && !!dateFrom.value && !!dateTo.value,
)
const busy = computed(() => loading.value || exportingXml.value || exportingXlsx.value)

function detailParts(detail: any, e: any): { code: string | null; message: string } {
  if (detail && typeof detail === 'object')
    return { code: detail.code ?? null, message: detail.message || detail.code || e?.message || 'erro' }
  return { code: null, message: detail || e?.message || 'erro' }
}

function apiError(e: any) {
  return detailParts(e?.data?.detail, e).message
}

// Os exports usam responseType 'blob', então o ofetch entrega o corpo do
// erro como Blob em e.data — precisamos ler/parsear pra achar o detail
// (e o code, pra distinguir o caso "muitas_notas" → export em background).
async function parseBlobError(e: any): Promise<{ code: string | null; message: string }> {
  const data = e?.data
  if (data instanceof Blob) {
    try {
      return detailParts(JSON.parse(await data.text())?.detail, e)
    } catch {
      return { code: null, message: e?.message || 'erro' }
    }
  }
  return detailParts(e?.data?.detail, e)
}

function toggleConta(id: string) {
  const next = new Set(selected.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  selected.value = next
}

function toggleAll() {
  selected.value = allSelected.value
    ? new Set()
    : new Set(contas.value.map((c) => c.id))
}

async function loadContas() {
  contasLoading.value = true
  try {
    const res = await api<{ items: Conta[] }>('/api/notas-fiscais/contas')
    contas.value = res.items
    selected.value = new Set(res.items.map((c) => c.id))
  } catch (e: any) {
    error.value = apiError(e)
  } finally {
    contasLoading.value = false
  }
}

function buildParams() {
  const params = new URLSearchParams()
  params.set('date_from', dateFrom.value)
  params.set('date_to', dateTo.value)
  for (const id of selected.value) params.append('conta', id)
  return params
}

async function buscar() {
  if (!canQuery.value || busy.value) return
  loading.value = true
  error.value = null
  try {
    const res = await api<NotasPage>(`/api/notas-fiscais?${buildParams().toString()}`)
    items.value = res.items
    total.value = res.total
    erros.value = res.erros
    searched.value = true
  } catch (e: any) {
    error.value = apiError(e)
  } finally {
    loading.value = false
  }
}

async function downloadBlob(path: string, filename: string) {
  const blob = await api<Blob>(path, { responseType: 'blob' as any })
  const href = URL.createObjectURL(blob as any)
  const a = document.createElement('a')
  a.href = href
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(href)
}

async function exportXml() {
  if (!canQuery.value || busy.value) return
  exportingXml.value = true
  error.value = null
  try {
    await downloadBlob(
      `/api/notas-fiscais/export.xml?${buildParams().toString()}`,
      `notas_fiscais_xml_${dateFrom.value}_${dateTo.value}.zip`,
    )
  } catch (e: any) {
    const { code, message } = await parseBlobError(e)
    // >500 notas estouram o download direto — cai pro export em background.
    if (code === 'muitas_notas') await startExportJob('xml')
    else error.value = message
  } finally {
    exportingXml.value = false
  }
}

async function exportXlsx() {
  if (!canQuery.value || busy.value) return
  exportingXlsx.value = true
  error.value = null
  try {
    const from = dateFrom.value.replaceAll('-', '')
    const to = dateTo.value.replaceAll('-', '')
    await downloadBlob(
      `/api/notas-fiscais/export.xlsx?${buildParams().toString()}`,
      `NF-e_Report_excel_${from}_ate_${to}.xlsx`,
    )
  } catch (e: any) {
    const { code, message } = await parseBlobError(e)
    if (code === 'muitas_notas') await startExportJob('xlsx')
    else error.value = message
  } finally {
    exportingXlsx.value = false
  }
}

// ─── export em background (lotes >500 notas, gerado pelo worker) ───────

type ExportJob = {
  id: string
  status: 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled'
  total: number
  processed: number
  result: { filename?: string; notas?: number; avisos?: number; fmt?: string }
  error: string | null
}

const exportJob = ref<ExportJob | null>(null)
const exportJobNote = ref<string | null>(null)
const downloadingJob = ref(false)
let exportPoll: ReturnType<typeof setInterval> | null = null

function stopExportPoll() {
  if (exportPoll) {
    clearInterval(exportPoll)
    exportPoll = null
  }
}

function pollExportJob(jobId: string) {
  stopExportPoll()
  const tick = async () => {
    try {
      const j = await api<ExportJob>(`/api/jobs/${jobId}`)
      exportJob.value = j
      if (j.status === 'succeeded' || j.status === 'failed' || j.status === 'cancelled') {
        stopExportPoll()
        exportJobNote.value = null
      }
    } catch {
      /* mantém o polling — erro transitório */
    }
  }
  void tick()
  exportPoll = setInterval(tick, 2000)
}

async function startExportJob(fmt: 'xlsx' | 'xml') {
  stopExportPoll()
  exportJob.value = null
  error.value = null
  exportJobNote.value =
    'O período tem mais de 500 notas — gerando o arquivo em segundo plano. '
    + 'Pode levar alguns minutos; você pode continuar usando o sistema.'
  try {
    const r = await api<{ job_id: string }>('/api/notas-fiscais/export-job', {
      method: 'POST',
      body: {
        fmt,
        date_from: dateFrom.value,
        date_to: dateTo.value,
        conta: Array.from(selected.value),
      },
    })
    pollExportJob(r.job_id)
  } catch (e: any) {
    exportJobNote.value = null
    error.value = apiError(e)
  }
}

async function downloadExportJob() {
  const j = exportJob.value
  if (!j || j.status !== 'succeeded' || downloadingJob.value) return
  downloadingJob.value = true
  try {
    await downloadBlob(
      `/api/notas-fiscais/export-job/${j.id}/download`,
      j.result?.filename || 'notas_fiscais_export',
    )
  } catch (e: any) {
    error.value = (await parseBlobError(e)).message
  } finally {
    downloadingJob.value = false
  }
}

onUnmounted(stopExportPoll)

function fmtDateTime(v: string | null) {
  if (!v) return '—'
  const d = new Date(v.replace(' ', 'T'))
  if (Number.isNaN(d.getTime())) return v
  return d.toLocaleString('pt-BR', {
    day: '2-digit',
    month: '2-digit',
    year: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function brl(v: number | null) {
  if (v == null) return '—'
  return v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
}

await loadContas()
</script>

<template>
  <div class="space-y-5">
    <PageHeader
      title="Notas Fiscais"
      description="Consulta e export de NF-e das contas Bling de emissão (XML e XLSX)."
    >
      <template #actions>
        <Button size="sm" variant="outline" :disabled="!canQuery || busy" @click="exportXml">
          <FileArchive class="size-4 mr-1.5" :class="{ 'animate-pulse': exportingXml }" />
          {{ exportingXml ? 'exportando xml…' : 'exportar xml' }}
        </Button>
        <Button size="sm" variant="outline" :disabled="!canQuery || busy" @click="exportXlsx">
          <Download class="size-4 mr-1.5" :class="{ 'animate-pulse': exportingXlsx }" />
          {{ exportingXlsx ? 'exportando…' : 'exportar xlsx' }}
        </Button>
      </template>
    </PageHeader>

    <div v-if="error" class="flex items-center gap-2 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-400">
      <AlertCircle class="size-4" />
      {{ error }}
    </div>

    <div
      v-if="exportJob || exportJobNote"
      class="rounded-md border px-3 py-2.5 text-sm"
      :class="exportJob?.status === 'failed'
        ? 'border-red-500/40 bg-red-500/10 text-red-400'
        : exportJob?.status === 'succeeded'
          ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
          : 'border-blue-500/40 bg-blue-500/10 text-blue-600 dark:text-blue-400'"
    >
      <div class="flex items-center gap-2">
        <Loader2 v-if="!exportJob || exportJob.status === 'pending' || exportJob.status === 'running'" class="size-4 animate-spin shrink-0" />
        <CheckCircle2 v-else-if="exportJob.status === 'succeeded'" class="size-4 shrink-0" />
        <XCircle v-else class="size-4 shrink-0" />

        <span v-if="!exportJob || exportJob.status === 'pending'">
          {{ exportJobNote || 'preparando export em segundo plano…' }}
        </span>
        <span v-else-if="exportJob.status === 'running'">
          gerando export… {{ exportJob.processed }}<span v-if="exportJob.total">/{{ exportJob.total }}</span> notas processadas
        </span>
        <span v-else-if="exportJob.status === 'succeeded'" class="flex flex-wrap items-center gap-x-2 gap-y-1">
          export pronto — {{ exportJob.result?.notas ?? 0 }} nota{{ exportJob.result?.notas === 1 ? '' : 's' }}<template v-if="exportJob.result?.avisos">, {{ exportJob.result.avisos }} aviso{{ exportJob.result.avisos === 1 ? '' : 's' }}</template>
          <Button size="sm" variant="outline" :disabled="downloadingJob" @click="downloadExportJob">
            <Download class="size-4 mr-1.5" :class="{ 'animate-pulse': downloadingJob }" />
            baixar
          </Button>
        </span>
        <span v-else>
          falha ao gerar o export: {{ exportJob.error || 'erro desconhecido' }}
        </span>
      </div>
    </div>

    <div class="rounded-md border bg-background px-3 py-3 space-y-3">
      <div class="flex items-center gap-2">
        <span class="text-[11px] font-medium text-muted-foreground uppercase tracking-wide">Lojas</span>
        <button
          class="text-xs text-primary hover:underline disabled:opacity-50"
          :disabled="contasLoading || !contas.length"
          @click="toggleAll"
        >
          {{ allSelected ? 'desmarcar todas' : 'marcar todas' }}
        </button>
        <Loader2 v-if="contasLoading" class="size-4 animate-spin text-muted-foreground" />
      </div>
      <div v-if="!contasLoading && !contas.length" class="text-sm text-muted-foreground">
        nenhuma conta ativa em bling_notas
      </div>
      <div class="flex flex-wrap gap-2">
        <label
          v-for="c in contas"
          :key="c.id"
          class="flex cursor-pointer items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-sm select-none"
          :class="selected.has(c.id) ? 'border-primary bg-primary/10' : 'hover:bg-muted'"
        >
          <input
            type="checkbox"
            class="size-3.5 rounded border accent-primary"
            :checked="selected.has(c.id)"
            @change="toggleConta(c.id)"
          />
          {{ c.nome }}
        </label>
      </div>

      <div class="flex flex-wrap items-end gap-3 pt-1">
        <label class="space-y-1">
          <span class="block text-[11px] font-medium text-muted-foreground">De</span>
          <input v-model="dateFrom" type="date" class="h-9 rounded-md border bg-background px-2 text-sm" />
        </label>
        <label class="space-y-1">
          <span class="block text-[11px] font-medium text-muted-foreground">Até</span>
          <input v-model="dateTo" type="date" class="h-9 rounded-md border bg-background px-2 text-sm" />
        </label>
        <Button size="sm" :disabled="!canQuery || busy" @click="buscar">
          <Loader2 v-if="loading" class="size-4 mr-1.5 animate-spin" />
          <Search v-else class="size-4 mr-1.5" />
          buscar
        </Button>
        <span v-if="searched" class="text-xs text-muted-foreground">
          {{ total }} nota{{ total === 1 ? '' : 's' }} no período
        </span>
      </div>
    </div>

    <div
      v-if="erros.length"
      class="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-600 dark:text-amber-400 space-y-0.5"
    >
      <div class="flex items-center gap-2 font-medium">
        <AlertCircle class="size-4" />
        contas com falha na consulta
      </div>
      <div v-for="(e, i) in erros" :key="i" class="pl-6 text-xs">{{ e }}</div>
    </div>

    <div class="overflow-auto rounded border max-h-[70vh]">
      <table class="w-full min-w-[980px] text-xs border-collapse">
        <thead class="sticky top-0 z-10 bg-background">
          <tr class="border-b">
            <th class="px-2 py-1.5 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap">Conta</th>
            <th class="px-2 py-1.5 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap">Número</th>
            <th class="px-2 py-1.5 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap">Data emissão</th>
            <th class="px-2 py-1.5 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap">Tipo</th>
            <th class="px-2 py-1.5 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap">Situação</th>
            <th class="px-2 py-1.5 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap">Cliente</th>
            <th class="px-2 py-1.5 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap">Documento</th>
            <th class="px-2 py-1.5 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap">Valor</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading">
            <td colspan="8" class="py-8 text-center text-muted-foreground">
              <Loader2 class="size-4 inline animate-spin mr-1.5" />
              consultando o Bling…
            </td>
          </tr>
          <tr v-else-if="!items.length">
            <td colspan="8" class="py-8 text-center text-muted-foreground">
              {{ searched ? 'nenhuma nota no período' : 'selecione as lojas e o período e clique em buscar' }}
            </td>
          </tr>
          <tr v-for="row in items" :key="`${row.conta}-${row.bling_id}`" class="border-t hover:brightness-95 dark:hover:brightness-110">
            <td class="px-2 py-1 whitespace-nowrap">{{ row.conta }}</td>
            <td class="px-2 py-1 font-mono whitespace-nowrap">{{ row.numero || '—' }}</td>
            <td class="px-2 py-1 whitespace-nowrap text-muted-foreground">{{ fmtDateTime(row.data_emissao) }}</td>
            <td class="px-2 py-1 whitespace-nowrap">{{ row.tipo || '—' }}</td>
            <td class="px-2 py-1 whitespace-nowrap">{{ row.situacao || '—' }}</td>
            <td class="px-2 py-1 whitespace-nowrap max-w-[280px] truncate">{{ row.cliente || '—' }}</td>
            <td class="px-2 py-1 font-mono whitespace-nowrap text-muted-foreground">{{ row.documento || '—' }}</td>
            <td class="px-2 py-1 text-right tabular-nums whitespace-nowrap">{{ brl(row.valor) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
