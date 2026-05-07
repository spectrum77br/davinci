<script setup lang="ts">
import {
  Upload, FileSpreadsheet, Play, RefreshCw, Wand2, Trash2, AlertTriangle,
  Loader2, CheckCircle2, XCircle, ArrowRight,
} from 'lucide-vue-next'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'auditoria', action: 'view' },
})

const { api } = useApi()
const canEdit = useCan('auditoria', 'edit')
const canDelete = useCan('auditoria', 'delete')

type Upload = {
  id: string
  filename: string
  size_bytes: number
  sheets: string[]
  created_at: string
}

type Account = { id: string; name: string; platform: string; department: string }

type Preview = {
  sheet_name: string
  headers: string[]
  sku_column: number | null
  rows: (string | null)[][]
  total_rows: number
  suggested_account_map: Record<string, string>
}

type RunSummary = {
  total_cells?: number
  ok?: number
  price_mismatch?: number
  missing?: number
  paused?: number
  rows?: number
}

type Run = {
  id: string
  upload_id: string
  job_id: string | null
  sheet_name: string
  account_map: Record<string, string>
  status: 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled'
  total: number
  processed: number
  summary: RunSummary
  error: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string
}

type Finding = {
  id: string
  run_id: string
  sku: string
  pricing_product_id: string | null
  pricing_account_id: string | null
  column_header: string | null
  expected_price: string | null
  actual_price: string | null
  status: 'ok' | 'price_mismatch' | 'missing' | 'paused' | 'extra'
  detail: string | null
  fixed: boolean
  fixed_at: string | null
  created_at: string
}

type FindingsPage = { items: Finding[]; total: number; limit: number; offset: number }

// ---------------------------------------------------------------- state
const uploads = ref<Upload[]>([])
const accounts = ref<Account[]>([])
const runs = ref<Run[]>([])

const selectedUpload = ref<Upload | null>(null)
const selectedSheet = ref<string>('')
const preview = ref<Preview | null>(null)
const accountMap = ref<Record<string, string>>({})

const uploading = ref(false)
const parsing = ref(false)
const creatingRun = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

const activeRunId = ref<string | null>(null)
const activeRun = ref<Run | null>(null)
const findings = ref<Finding[]>([])
const findingsTotal = ref(0)
const findingsLimit = ref(100)
const findingsOffset = ref(0)
const filterStatuses = ref<string[]>(['price_mismatch', 'missing', 'paused'])
const filterSku = ref('')
const filterFixed = ref<'' | 'true' | 'false'>('')
const findingsLoading = ref(false)

const fixingId = ref<string | null>(null)
const bulkFixing = ref(false)
const lastFixResult = ref<{ fixed: number; failed: number; skipped: number } | null>(null)

let pollHandle: ReturnType<typeof setInterval> | null = null

// ---------------------------------------------------------------- helpers
function formatSize(b: number): string {
  if (b < 1024) return `${b} B`
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`
  return `${(b / (1024 * 1024)).toFixed(1)} MB`
}

function formatDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('pt-BR')
}

function statusLabel(s: string): string {
  return {
    ok: 'OK',
    price_mismatch: 'Preço diverge',
    missing: 'SKU ausente',
    paused: 'Pausado',
    extra: 'Extra',
    pending: 'Pendente',
    running: 'Rodando',
    succeeded: 'Concluído',
    failed: 'Falhou',
    cancelled: 'Cancelado',
  }[s] ?? s
}

function statusClass(s: string): string {
  return {
    ok: 'bg-green-500/15 text-green-400',
    price_mismatch: 'bg-amber-500/20 text-amber-300',
    missing: 'bg-red-500/15 text-red-400',
    paused: 'bg-zinc-500/20 text-zinc-300',
    extra: 'bg-blue-500/15 text-blue-400',
    pending: 'bg-zinc-500/20 text-zinc-300',
    running: 'bg-blue-500/15 text-blue-400',
    succeeded: 'bg-green-500/15 text-green-400',
    failed: 'bg-red-500/15 text-red-400',
    cancelled: 'bg-zinc-500/20 text-zinc-300',
  }[s] ?? 'bg-zinc-500/20 text-zinc-300'
}

// ---------------------------------------------------------------- loaders
async function loadUploads() {
  uploads.value = await api<Upload[]>('/api/audit/uploads')
}

async function loadAccounts() {
  accounts.value = await api<Account[]>('/api/pricing/accounts')
}

async function loadRuns() {
  runs.value = await api<Run[]>('/api/audit/runs')
}

async function refreshAll() {
  await Promise.all([loadUploads(), loadAccounts(), loadRuns()])
}

onMounted(refreshAll)
onBeforeUnmount(() => {
  if (pollHandle) clearInterval(pollHandle)
})

// ---------------------------------------------------------------- upload
async function onPickFile() {
  fileInput.value?.click()
}

async function onFileChange(e: Event) {
  const target = e.target as HTMLInputElement
  const file = target.files?.[0]
  if (!file) return
  uploading.value = true
  try {
    const fd = new FormData()
    fd.append('file', file)
    const created = await api<Upload>('/api/audit/uploads', {
      method: 'POST',
      body: fd,
    })
    uploads.value.unshift(created)
    selectUpload(created)
  } catch (e: any) {
    alert(`Falha no upload: ${e?.data?.detail?.code ?? e?.message ?? 'erro'}`)
  } finally {
    uploading.value = false
    if (target) target.value = ''
  }
}

async function selectUpload(u: Upload) {
  selectedUpload.value = u
  selectedSheet.value = u.sheets[0] ?? ''
  preview.value = null
  accountMap.value = {}
  if (selectedSheet.value) await parseSheet()
}

async function parseSheet() {
  if (!selectedUpload.value || !selectedSheet.value) return
  parsing.value = true
  try {
    preview.value = await api<Preview>('/api/audit/parse', {
      method: 'POST',
      body: { upload_id: selectedUpload.value.id, sheet: selectedSheet.value, max_rows: 10 },
    })
    accountMap.value = { ...preview.value.suggested_account_map }
  } catch (e: any) {
    alert(`Falha ao ler aba: ${e?.data?.detail?.code ?? 'erro'}`)
  } finally {
    parsing.value = false
  }
}

async function deleteUpload(u: Upload) {
  if (!confirm(`Apagar planilha "${u.filename}"?`)) return
  await api(`/api/audit/uploads/${u.id}`, { method: 'DELETE' })
  if (selectedUpload.value?.id === u.id) {
    selectedUpload.value = null
    preview.value = null
  }
  await loadUploads()
}

// ---------------------------------------------------------------- account map
const headerMappingRows = computed(() => {
  if (!preview.value) return []
  const skuIdx = preview.value.sku_column
  return preview.value.headers.map((h, i) => ({
    index: i,
    header: h,
    isSku: i === skuIdx,
  }))
})

function setHeaderAccount(header: string, accountId: string) {
  if (!accountId) {
    delete accountMap.value[header]
    accountMap.value = { ...accountMap.value }
  } else {
    accountMap.value = { ...accountMap.value, [header]: accountId }
  }
}

const mappedColumnCount = computed(() => Object.keys(accountMap.value).length)

// ---------------------------------------------------------------- run
async function startRun() {
  if (!selectedUpload.value || !selectedSheet.value) return
  if (mappedColumnCount.value === 0) {
    alert('Mapeie ao menos uma coluna para uma conta antes de rodar.')
    return
  }
  creatingRun.value = true
  try {
    const created = await api<{ job_id: string }>('/api/audit/runs', {
      method: 'POST',
      body: {
        upload_id: selectedUpload.value.id,
        sheet: selectedSheet.value,
        account_map: accountMap.value,
      },
    })
    await loadRuns()
    const matched = runs.value.find((r) => r.job_id === created.job_id)
    if (matched) await openRun(matched.id)
  } catch (e: any) {
    alert(`Falha ao criar run: ${e?.data?.detail?.code ?? 'erro'}`)
  } finally {
    creatingRun.value = false
  }
}

async function openRun(id: string) {
  activeRunId.value = id
  findingsOffset.value = 0
  await Promise.all([loadActiveRun(), loadFindings()])
  if (pollHandle) clearInterval(pollHandle)
  if (activeRun.value && (activeRun.value.status === 'running' || activeRun.value.status === 'pending')) {
    pollHandle = setInterval(async () => {
      await loadActiveRun()
      if (activeRun.value && (activeRun.value.status === 'succeeded' || activeRun.value.status === 'failed')) {
        if (pollHandle) clearInterval(pollHandle)
        await loadFindings()
        await loadRuns()
      }
    }, 2500)
  }
}

async function loadActiveRun() {
  if (!activeRunId.value) return
  activeRun.value = await api<Run>(`/api/audit/runs/${activeRunId.value}`)
}

async function loadFindings() {
  if (!activeRunId.value) return
  findingsLoading.value = true
  try {
    const params = new URLSearchParams()
    for (const s of filterStatuses.value) params.append('status_in', s)
    if (filterSku.value) params.set('sku', filterSku.value)
    if (filterFixed.value) params.set('fixed', filterFixed.value)
    params.set('limit', String(findingsLimit.value))
    params.set('offset', String(findingsOffset.value))
    const r = await api<FindingsPage>(
      `/api/audit/runs/${activeRunId.value}/findings?${params.toString()}`,
    )
    findings.value = r.items
    findingsTotal.value = r.total
  } finally {
    findingsLoading.value = false
  }
}

function toggleStatusFilter(s: string) {
  const i = filterStatuses.value.indexOf(s)
  if (i >= 0) filterStatuses.value.splice(i, 1)
  else filterStatuses.value.push(s)
}

watch([filterStatuses, filterSku, filterFixed], () => {
  findingsOffset.value = 0
  if (activeRunId.value) loadFindings()
})

// ---------------------------------------------------------------- fix
async function fixOne(f: Finding) {
  fixingId.value = f.id
  try {
    const r = await api<{ fixed: number; failed: number; skipped: number }>(
      `/api/audit/findings/${f.id}/fix-price`,
      { method: 'POST' },
    )
    lastFixResult.value = r
    await loadFindings()
  } catch (e: any) {
    alert(`Falha: ${e?.data?.detail?.code ?? 'erro'}`)
  } finally {
    fixingId.value = null
  }
}

async function fixBulk() {
  if (!activeRunId.value) return
  if (!confirm(`Corrigir todos os preços em divergência da run atual? (${findingsTotal.value} itens filtrados)`)) return
  bulkFixing.value = true
  try {
    const r = await api<{ fixed: number; failed: number; skipped: number }>(
      `/api/audit/runs/${activeRunId.value}/fix-prices`,
      { method: 'POST', body: { status_in: ['price_mismatch'] } },
    )
    lastFixResult.value = r
    await loadFindings()
  } catch (e: any) {
    alert(`Falha: ${e?.data?.detail?.code ?? 'erro'}`)
  } finally {
    bulkFixing.value = false
  }
}

const accountById = computed(() => {
  const m: Record<string, Account> = {}
  for (const a of accounts.value) m[a.id] = a
  return m
})
</script>

<template>
  <div class="space-y-6">
    <PageHeader
      title="Auditoria por planilha"
      description="Compare preços de uma planilha externa com a tabela de preços calculada"
    >
      <template #actions>
        <button
          class="inline-flex items-center gap-2 rounded-md border border-input bg-background px-3 h-9 text-sm hover:bg-muted"
          @click="refreshAll"
        >
          <RefreshCw class="size-4" /> Atualizar
        </button>
      </template>
    </PageHeader>

    <!-- ============================================================= 1) Upload -->
    <section class="rounded-lg border bg-card">
      <div class="px-4 py-3 border-b flex items-center justify-between">
        <div class="font-medium text-sm flex items-center gap-2">
          <Upload class="size-4" /> 1. Upload da planilha
        </div>
        <div class="flex items-center gap-2">
          <input ref="fileInput" type="file" accept=".xlsx,.xlsm" class="hidden" @change="onFileChange" />
          <button
            v-if="canEdit"
            class="inline-flex items-center gap-2 rounded-md bg-primary text-primary-foreground px-3 h-9 text-sm hover:bg-primary/90 disabled:opacity-50"
            :disabled="uploading"
            @click="onPickFile"
          >
            <Loader2 v-if="uploading" class="size-4 animate-spin" />
            <Upload v-else class="size-4" />
            {{ uploading ? 'Enviando...' : 'Enviar .xlsx' }}
          </button>
        </div>
      </div>
      <div class="p-4 space-y-3">
        <div v-if="!uploads.length" class="text-sm text-muted-foreground">
          Nenhuma planilha enviada ainda.
        </div>
        <ul v-else class="divide-y">
          <li
            v-for="u in uploads"
            :key="u.id"
            class="flex items-center gap-3 py-2 cursor-pointer"
            :class="selectedUpload?.id === u.id ? 'bg-muted/30 -mx-2 px-2 rounded' : ''"
            @click="selectUpload(u)"
          >
            <FileSpreadsheet class="size-5 text-muted-foreground shrink-0" />
            <div class="min-w-0 flex-1">
              <div class="text-sm font-medium truncate">{{ u.filename }}</div>
              <div class="text-xs text-muted-foreground">
                {{ formatSize(u.size_bytes) }} · {{ u.sheets.length }} aba(s) · {{ formatDate(u.created_at) }}
              </div>
            </div>
            <button
              v-if="canDelete"
              class="text-muted-foreground hover:text-destructive p-1"
              :title="'Apagar'"
              @click.stop="deleteUpload(u)"
            >
              <Trash2 class="size-4" />
            </button>
          </li>
        </ul>
      </div>
    </section>

    <!-- ============================================================= 2) Mapeamento -->
    <section v-if="selectedUpload" class="rounded-lg border bg-card">
      <div class="px-4 py-3 border-b flex items-center justify-between flex-wrap gap-2">
        <div class="font-medium text-sm flex items-center gap-2">
          <Wand2 class="size-4" /> 2. Mapear colunas para contas
        </div>
        <div class="flex items-center gap-2">
          <label class="text-xs text-muted-foreground">Aba:</label>
          <select
            v-model="selectedSheet"
            class="h-8 rounded-md border border-input bg-background px-2 text-sm"
            @change="parseSheet"
          >
            <option v-for="s in selectedUpload.sheets" :key="s" :value="s">{{ s }}</option>
          </select>
          <button
            class="inline-flex items-center gap-1 rounded-md border border-input bg-background px-2 h-8 text-xs hover:bg-muted"
            :disabled="parsing"
            @click="parseSheet"
          >
            <Loader2 v-if="parsing" class="size-3 animate-spin" />
            <RefreshCw v-else class="size-3" />
            Reparse
          </button>
        </div>
      </div>

      <div v-if="preview" class="p-4 space-y-4">
        <div class="text-xs text-muted-foreground">
          {{ preview.total_rows }} linhas detectadas. Mapeie cada coluna para uma conta de pricing
          ou deixe sem mapear (será ignorada).
        </div>
        <div class="overflow-x-auto">
          <table class="w-full text-sm border-collapse">
            <thead>
              <tr class="bg-muted/40">
                <th class="text-left px-3 py-2 font-medium border-b">Coluna</th>
                <th class="text-left px-3 py-2 font-medium border-b">Conta de pricing</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in headerMappingRows" :key="row.index" class="border-b">
                <td class="px-3 py-1.5">
                  <span :class="row.isSku ? 'font-semibold text-amber-300' : ''">{{ row.header || `(coluna ${row.index + 1})` }}</span>
                  <span v-if="row.isSku" class="text-xs text-muted-foreground ml-2">SKU</span>
                </td>
                <td class="px-3 py-1">
                  <select
                    v-if="!row.isSku"
                    :value="accountMap[row.header] ?? ''"
                    class="h-8 rounded-md border border-input bg-background px-2 text-sm w-full max-w-md"
                    @change="(e) => setHeaderAccount(row.header, (e.target as HTMLSelectElement).value)"
                  >
                    <option value="">— ignorar —</option>
                    <option v-for="a in accounts" :key="a.id" :value="a.id">
                      {{ a.name }} · {{ a.platform }} · {{ a.department }}
                    </option>
                  </select>
                  <span v-else class="text-xs text-muted-foreground">—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div v-if="preview.rows.length" class="space-y-1">
          <div class="text-xs text-muted-foreground">Pré-visualização (10 primeiras linhas)</div>
          <div class="overflow-x-auto rounded border">
            <table class="text-xs">
              <thead><tr class="bg-muted/40">
                <th v-for="h in preview.headers" :key="h" class="px-2 py-1 text-left">{{ h }}</th>
              </tr></thead>
              <tbody>
                <tr v-for="(r, i) in preview.rows" :key="i" class="border-t">
                  <td v-for="(c, j) in r" :key="j" class="px-2 py-1 whitespace-nowrap">{{ c ?? '' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div class="flex items-center gap-3 pt-2">
          <span class="text-xs text-muted-foreground">{{ mappedColumnCount }} coluna(s) mapeada(s)</span>
          <button
            v-if="canEdit"
            class="inline-flex items-center gap-2 rounded-md bg-primary text-primary-foreground px-3 h-9 text-sm hover:bg-primary/90 disabled:opacity-50"
            :disabled="creatingRun || mappedColumnCount === 0 || !preview.sku_column && preview.sku_column !== 0"
            @click="startRun"
          >
            <Loader2 v-if="creatingRun" class="size-4 animate-spin" />
            <Play v-else class="size-4" />
            Rodar auditoria
          </button>
          <span v-if="preview.sku_column === null" class="text-xs text-amber-300 inline-flex items-center gap-1">
            <AlertTriangle class="size-3" /> coluna "sku" não detectada — adicione um cabeçalho "SKU"
          </span>
        </div>
      </div>
    </section>

    <!-- ============================================================= 3) Runs -->
    <section v-if="runs.length" class="rounded-lg border bg-card">
      <div class="px-4 py-3 border-b font-medium text-sm">3. Auditorias recentes</div>
      <ul class="divide-y">
        <li
          v-for="r in runs"
          :key="r.id"
          class="flex items-center gap-3 px-4 py-2 cursor-pointer"
          :class="activeRunId === r.id ? 'bg-muted/30' : ''"
          @click="openRun(r.id)"
        >
          <span :class="`text-xs px-2 py-0.5 rounded ${statusClass(r.status)}`">{{ statusLabel(r.status) }}</span>
          <span class="text-sm truncate">{{ r.sheet_name }}</span>
          <span class="text-xs text-muted-foreground">{{ r.processed }}/{{ r.total || '?' }} SKUs</span>
          <span v-if="r.summary?.price_mismatch" class="text-xs text-amber-300">
            {{ r.summary.price_mismatch }} divergências
          </span>
          <span class="ml-auto text-xs text-muted-foreground">{{ formatDate(r.finished_at ?? r.created_at) }}</span>
          <ArrowRight class="size-4 text-muted-foreground" />
        </li>
      </ul>
    </section>

    <!-- ============================================================= 4) Resultados -->
    <section v-if="activeRun" class="rounded-lg border bg-card">
      <div class="px-4 py-3 border-b flex items-center justify-between flex-wrap gap-2">
        <div class="font-medium text-sm flex items-center gap-2">
          <span :class="`text-xs px-2 py-0.5 rounded ${statusClass(activeRun.status)}`">{{ statusLabel(activeRun.status) }}</span>
          <span>Run {{ activeRun.id.slice(0, 8) }} · {{ activeRun.sheet_name }}</span>
        </div>
        <div class="flex items-center gap-2">
          <button
            class="inline-flex items-center gap-1 rounded-md border border-input bg-background px-2 h-8 text-xs hover:bg-muted"
            :disabled="findingsLoading"
            @click="loadFindings"
          >
            <RefreshCw class="size-3" /> Atualizar
          </button>
          <button
            v-if="canEdit && activeRun.status === 'succeeded'"
            class="inline-flex items-center gap-2 rounded-md bg-amber-500 text-black px-3 h-8 text-xs hover:bg-amber-400 disabled:opacity-50"
            :disabled="bulkFixing"
            @click="fixBulk"
          >
            <Loader2 v-if="bulkFixing" class="size-3 animate-spin" />
            <Wand2 v-else class="size-3" />
            Corrigir todas as divergências
          </button>
        </div>
      </div>

      <div class="p-4 space-y-3">
        <div class="grid grid-cols-2 sm:grid-cols-5 gap-3 text-sm">
          <div class="rounded border p-2">
            <div class="text-xs text-muted-foreground">Linhas</div>
            <div class="text-base font-semibold">{{ activeRun.summary?.rows ?? 0 }}</div>
          </div>
          <div class="rounded border p-2">
            <div class="text-xs text-muted-foreground">OK</div>
            <div class="text-base font-semibold text-green-400">{{ activeRun.summary?.ok ?? 0 }}</div>
          </div>
          <div class="rounded border p-2">
            <div class="text-xs text-muted-foreground">Divergências</div>
            <div class="text-base font-semibold text-amber-300">{{ activeRun.summary?.price_mismatch ?? 0 }}</div>
          </div>
          <div class="rounded border p-2">
            <div class="text-xs text-muted-foreground">Ausentes</div>
            <div class="text-base font-semibold text-red-400">{{ activeRun.summary?.missing ?? 0 }}</div>
          </div>
          <div class="rounded border p-2">
            <div class="text-xs text-muted-foreground">Pausados</div>
            <div class="text-base font-semibold text-zinc-300">{{ activeRun.summary?.paused ?? 0 }}</div>
          </div>
        </div>

        <div v-if="lastFixResult" class="text-xs rounded border p-2 bg-muted/30">
          Último ajuste: {{ lastFixResult.fixed }} corrigidos · {{ lastFixResult.failed }} falhas · {{ lastFixResult.skipped }} ignorados
        </div>

        <div class="flex items-center gap-2 flex-wrap pt-2">
          <span class="text-xs text-muted-foreground">Status:</span>
          <button
            v-for="s in ['ok','price_mismatch','missing','paused']"
            :key="s"
            class="text-xs px-2 py-0.5 rounded border"
            :class="filterStatuses.includes(s) ? statusClass(s) + ' border-transparent' : 'text-muted-foreground'"
            @click="toggleStatusFilter(s)"
          >
            {{ statusLabel(s) }}
          </button>
          <input
            v-model="filterSku"
            placeholder="SKU"
            class="h-8 rounded-md border border-input bg-background px-2 text-xs ml-2"
          />
          <select v-model="filterFixed" class="h-8 rounded-md border border-input bg-background px-2 text-xs">
            <option value="">corrigidos: todos</option>
            <option value="false">não corrigidos</option>
            <option value="true">corrigidos</option>
          </select>
          <span class="text-xs text-muted-foreground ml-auto">{{ findingsTotal }} resultado(s)</span>
        </div>

        <div class="overflow-x-auto rounded border">
          <table class="w-full text-sm">
            <thead>
              <tr class="bg-muted/40 text-xs">
                <th class="text-left px-2 py-1.5">Status</th>
                <th class="text-left px-2 py-1.5">SKU</th>
                <th class="text-left px-2 py-1.5">Conta</th>
                <th class="text-right px-2 py-1.5">Esperado</th>
                <th class="text-right px-2 py-1.5">Planilha</th>
                <th class="text-left px-2 py-1.5">Detalhe</th>
                <th class="text-right px-2 py-1.5">Ação</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="findingsLoading">
                <td colspan="7" class="px-2 py-6 text-center text-muted-foreground text-xs">Carregando...</td>
              </tr>
              <tr v-else-if="!findings.length">
                <td colspan="7" class="px-2 py-6 text-center text-muted-foreground text-xs">Nenhum resultado para os filtros.</td>
              </tr>
              <tr v-for="f in findings" :key="f.id" class="border-t">
                <td class="px-2 py-1">
                  <span :class="`text-xs px-2 py-0.5 rounded ${statusClass(f.status)}`">{{ statusLabel(f.status) }}</span>
                  <CheckCircle2 v-if="f.fixed" class="size-3 text-green-400 inline-block ml-1" />
                </td>
                <td class="px-2 py-1 font-mono text-xs">{{ f.sku }}</td>
                <td class="px-2 py-1 text-xs">{{ f.pricing_account_id ? accountById[f.pricing_account_id]?.name ?? f.column_header : f.column_header }}</td>
                <td class="px-2 py-1 text-right">{{ f.expected_price ?? '—' }}</td>
                <td class="px-2 py-1 text-right">{{ f.actual_price ?? '—' }}</td>
                <td class="px-2 py-1 text-xs text-muted-foreground">{{ f.detail ?? '' }}</td>
                <td class="px-2 py-1 text-right">
                  <button
                    v-if="canEdit && f.status === 'price_mismatch' && !f.fixed"
                    class="inline-flex items-center gap-1 rounded border border-input bg-background px-2 h-7 text-xs hover:bg-muted disabled:opacity-50"
                    :disabled="fixingId === f.id"
                    @click="fixOne(f)"
                  >
                    <Loader2 v-if="fixingId === f.id" class="size-3 animate-spin" />
                    <Wand2 v-else class="size-3" />
                    Corrigir
                  </button>
                  <XCircle v-else-if="f.status === 'missing'" class="size-3 text-red-400 inline-block" />
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div v-if="findingsTotal > findingsLimit" class="flex items-center justify-between text-xs text-muted-foreground pt-1">
          <button
            class="rounded border border-input bg-background px-2 h-7 hover:bg-muted disabled:opacity-50"
            :disabled="findingsOffset === 0"
            @click="findingsOffset = Math.max(0, findingsOffset - findingsLimit); loadFindings()"
          >
            Anterior
          </button>
          <span>{{ findingsOffset + 1 }} – {{ Math.min(findingsOffset + findingsLimit, findingsTotal) }} de {{ findingsTotal }}</span>
          <button
            class="rounded border border-input bg-background px-2 h-7 hover:bg-muted disabled:opacity-50"
            :disabled="findingsOffset + findingsLimit >= findingsTotal"
            @click="findingsOffset = findingsOffset + findingsLimit; loadFindings()"
          >
            Próxima
          </button>
        </div>
      </div>
    </section>
  </div>
</template>
