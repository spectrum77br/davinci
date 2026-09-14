<script setup lang="ts">
// Suprimentos — certificações (Anatel/Inmetro/isento) com alerta visual
// de validade: linha âmbar quando faltam < 30 dias, vermelha quando já
// venceu. Auto-save inline, mesmo padrão da página Consórcio.
import { computed, onBeforeUnmount, reactive, ref } from 'vue'
import { Download, ExternalLink, FileDown, Paperclip, Plus, RefreshCw, Trash2, X } from 'lucide-vue-next'
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
  return Boolean(p?.edit)
})
const canDelete = computed(() => {
  if (auth.isAdmin) return true
  return Boolean(auth.user?.permissions?.financeiro_suprimentos?.delete)
})

type AnatelRecord = {
  numero: string
  cnpj: string
  nome_empresa: string
  produto: string | null
  modelos: string[]
  nomes_comerciais: string[]
  tipos_produto: string[]
  fabricantes: string[]
  certificados: string[]
  inicio: string | null
  fim: string | null
  situacao_certificado: string | null
  situacao_requerimento: string | null
  alertas: string[]
}
type InmetroRecord = {
  chave: string
  cnpj: string
  nome_empresa: string
  certificador: string | null
  numero: string
  modelo: string
  marca: string | null
  descricao: string | null
  produto: string | null
  inicio: string | null
  fim: string | null
  situacao_certificado: string | null
  alertas: string[]
  campos_confirmados: string[]
}
type AnatelStatus = {
  cnpj: string
  nome_empresa: string
  automatico: boolean
  periodicidade: string
  ultimo_sucesso_em: string | null
  ultima_tentativa_em: string | null
  proxima_tentativa_em: string | null
  erro: string | null
  source_updated_at: string | null
  fonte_url: string
}
type SyncResult = {
  status: 'ok' | 'busy' | 'error'
  criados: number
  atualizados: number
  nao_localizados: number
  conflitos: number
  erro?: string | null
}
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
  anatel_numero: string | null
  anatel_dados: AnatelRecord | null
  anatel_consultado_em: string | null
  anatel_encontrado: boolean | null
  inmetro_chave: string | null
  inmetro_dados: InmetroRecord | null
  inmetro_consultado_em: string | null
  inmetro_encontrado: boolean | null
}

const rows = ref<Row[]>([])
const loading = ref(false)
const errorText = ref<string | null>(null)
const exporting = ref(false)
const adding = ref(false)
const synchronizing = ref(false)
const anatelStatus = ref<AnatelStatus | null>(null)
const statusError = ref<string | null>(null)
const syncMessage = ref<string | null>(null)
const synchronizingInmetro = ref(false)
const inmetroStatus = ref<AnatelStatus | null>(null)
const inmetroStatusError = ref<string | null>(null)
const inmetroSyncMessage = ref<string | null>(null)
const inmetroSyncError = ref<string | null>(null)
const selectedRow = ref<Row | null>(null)
const detailsDialog = ref<HTMLDialogElement | null>(null)
const rowBusy = reactive<Record<string, boolean>>({})
const busy = computed(() => loading.value || exporting.value || adding.value || synchronizing.value || synchronizingInmetro.value)
const hasBusyRow = computed(() => Object.values(rowBusy).some(Boolean))
const canAttach = computed(() => auth.isAdmin || Boolean(auth.user?.permissions?.financeiro_suprimentos?.edit))
const autosave = createCertificacoesAutosave(
  (id, patch) => api(`/api/financeiro/suprimentos/${id}`, { method: 'PATCH', body: patch }),
  () => { errorText.value = 'Não foi possível salvar as alterações. Tente novamente antes de atualizar ou baixar o PDF.' },
)
onBeforeUnmount(() => { void autosave.flush().catch(() => {}) })

const CERT_OPTIONS = ['', 'anatel', 'inmetro', 'isento']
const ANATEL_SOURCE_URL = 'https://www.anatel.gov.br/dadosabertos/paineis_de_dados/certificacao_de_produtos/produtos_certificados.zip'
const INMETRO_SOURCE_URL = 'http://www.inmetro.gov.br/prodcert/'
const OFFICIAL_FIELDS = new Set<keyof Row>(['modelo', 'nome_comercial', 'certificado', 'numero', 'inicio', 'fim'])
const INMETRO_IDENTITY_FIELDS = new Set<keyof Row>(['modelo', 'certificado', 'numero'])

async function loadAnatelStatus() {
  try {
    anatelStatus.value = await api<AnatelStatus>('/api/financeiro/suprimentos/anatel/status')
    statusError.value = null
  } catch {
    statusError.value = 'Não foi possível consultar a atualização automática da Anatel. Tente recarregar em instantes.'
  }
}

async function loadInmetroStatus() {
  try {
    inmetroStatus.value = await api<AnatelStatus>('/api/financeiro/suprimentos/inmetro/status')
    inmetroStatusError.value = null
  } catch {
    inmetroStatusError.value = 'Não foi possível consultar a atualização automática do Inmetro. Tente recarregar em instantes.'
  }
}

async function reloadData(source?: 'anatel' | 'inmetro') {
  const [result] = await Promise.allSettled([
    api<Row[]>('/api/financeiro/suprimentos'),
    ...(!source || source === 'anatel' ? [loadAnatelStatus()] : []),
    ...(!source || source === 'inmetro' ? [loadInmetroStatus()] : []),
  ])
  if (result.status === 'fulfilled') {
    rows.value = result.value
  } else {
    errorText.value = 'Não foi possível recarregar as certificações. Os dados exibidos foram preservados. Tente novamente.'
  }
}

async function load() {
  if (busy.value || hasBusyRow.value) return
  loading.value = true
  errorText.value = null
  try {
    await autosave.flush()
    await reloadData()
  } catch {
    errorText.value = 'Não foi possível salvar as alterações. Tente recarregar novamente.'
  } finally {
    loading.value = false
  }
}
await load()

function scheduleSave(row: Row, field: keyof Row, value: any) {
  if (!canEdit.value || busy.value || rowBusy[row.id] || isOfficialField(row, field)) return
  ;(row as any)[field] = value
  autosave.schedule(row.id, field, value)
}

async function syncAnatel() {
  if (!canAttach.value || busy.value || hasBusyRow.value) return
  synchronizing.value = true
  errorText.value = null
  syncMessage.value = null
  try {
    try {
      await autosave.flush()
    } catch {
      errorText.value = 'As alterações não foram salvas. Tente atualizar novamente para preservar suas edições.'
      return
    }
    const result = await api<SyncResult>('/api/financeiro/suprimentos/anatel/sincronizar', { method: 'POST' })
    if (result.status === 'ok') {
      syncMessage.value = `Consulta concluída: ${result.criados} novos registros e ${result.atualizados} atualizados.`
      if (result.nao_localizados) syncMessage.value += ` ${result.nao_localizados} não localizados na última consulta; dados anteriores preservados.`
      if (result.conflitos) syncMessage.value += ` ${result.conflitos} registros precisam de conferência antes do vínculo automático.`
      await reloadData('anatel')
    } else if (result.status === 'busy') {
      syncMessage.value = 'Uma consulta à Anatel já está em andamento. Recarregue em instantes para ver o resultado.'
      await loadAnatelStatus()
    } else {
      errorText.value = 'Não foi possível concluir a consulta à Anatel. Os dados anteriores foram preservados; o sistema tentará novamente automaticamente.'
      await loadAnatelStatus()
    }
  } catch {
    errorText.value = 'Não foi possível concluir a consulta à Anatel. Os dados anteriores foram preservados. Tente atualizar novamente em instantes.'
    await loadAnatelStatus()
  } finally {
    synchronizing.value = false
  }
}

function isLinked(row: Row) {
  return Boolean(row.anatel_numero || row.inmetro_chave)
}

function isOfficialField(row: Row, field: keyof Row) {
  if (row.anatel_numero) return OFFICIAL_FIELDS.has(field)
  if (!row.inmetro_chave) return false
  if (INMETRO_IDENTITY_FIELDS.has(field)) return true
  return Boolean((field === 'inicio' || field === 'fim') && row.inmetro_dados?.campos_confirmados?.includes(field) && row.inmetro_dados[field])
}

function officialSource(row: Row) {
  return row.anatel_numero ? 'Anatel' : row.inmetro_chave ? 'Inmetro' : ''
}

function officialData(row: Row) {
  return row.anatel_numero ? row.anatel_dados : row.inmetro_dados
}

function isMissingSource(row: Row) {
  return row.anatel_numero ? row.anatel_encontrado === false : Boolean(row.inmetro_chave && row.inmetro_encontrado === false)
}

function officialAlerts(row: Row) {
  return officialData(row)?.alertas || []
}

function fieldSourceTitle(row: Row, field: keyof Row) {
  if (isOfficialField(row, field)) return `Atualizado automaticamente por ${officialSource(row)}`
  if (row.inmetro_chave && (field === 'inicio' || field === 'fim')) return 'Data preenchida manualmente; não informada pelo ProdCert.'
  return ''
}

function officialSituation(row: Row) {
  if (!isLinked(row)) return 'Manual · sem confirmação automática'
  const situation = row.anatel_numero ? row.anatel_dados?.situacao_requerimento : row.inmetro_dados?.situacao_certificado
  return `${officialSource(row)} · ${situation || 'Situação não informada na fonte'}`
}

function officialSituationClass(row: Row) {
  if (!isLinked(row) || isMissingSource(row)) return 'text-muted-foreground'
  if (row.inmetro_chave && !row.anatel_numero) {
    const situation = row.inmetro_dados?.situacao_certificado?.toLocaleLowerCase('pt-BR') || ''
    if (situation === 'ativo') return 'text-emerald-700 dark:text-emerald-400'
    if (situation === 'suspenso') return 'text-amber-700 dark:text-amber-400'
    return 'text-muted-foreground'
  }
  const situation = row.anatel_dados?.situacao_requerimento?.toLocaleLowerCase('pt-BR') || ''
  if (situation === 'homologação emitida') return 'text-emerald-700 dark:text-emerald-400'
  if (situation.includes('análise')) return 'text-amber-700 dark:text-amber-400'
  return 'text-muted-foreground'
}

async function syncInmetro() {
  if (!canAttach.value || busy.value || hasBusyRow.value) return
  synchronizingInmetro.value = true
  inmetroSyncError.value = null
  inmetroSyncMessage.value = null
  try {
    try {
      await autosave.flush()
    } catch {
      inmetroSyncError.value = 'As alterações não foram salvas. Tente atualizar o Inmetro novamente para preservar suas edições.'
      return
    }
    const result = await api<SyncResult>('/api/financeiro/suprimentos/inmetro/sincronizar', { method: 'POST' })
    if (result.status === 'ok') {
      inmetroSyncMessage.value = `Consulta ao Inmetro concluída: ${result.criados} novos registros e ${result.atualizados} atualizados.`
      if (result.nao_localizados) inmetroSyncMessage.value += ` ${result.nao_localizados} não localizados na última consulta; dados anteriores preservados.`
      if (result.conflitos) inmetroSyncMessage.value += ` ${result.conflitos} registros precisam de conferência antes do vínculo automático.`
      await reloadData('inmetro')
    } else if (result.status === 'busy') {
      inmetroSyncMessage.value = 'Uma consulta ao Inmetro já está em andamento. Recarregue em instantes para ver o resultado.'
      await loadInmetroStatus()
    } else {
      inmetroSyncError.value = 'Não foi possível concluir a consulta ao Inmetro. Os dados anteriores foram preservados; o sistema tentará novamente automaticamente.'
      await loadInmetroStatus()
    }
  } catch {
    inmetroSyncError.value = 'Não foi possível concluir a consulta ao Inmetro. Os dados anteriores foram preservados. Tente atualizar novamente em instantes.'
    await loadInmetroStatus()
  } finally {
    synchronizingInmetro.value = false
  }
}

function formatTimestamp(value: string | null | undefined) {
  if (!value) return 'Ainda não realizada'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'Data não informada'
  return date.toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short', timeZone: 'America/Sao_Paulo' })
}

function formatDate(value: string | null | undefined) {
  if (!value) return 'Não informada'
  const parts = value.split('-')
  return parts.length === 3 ? parts.reverse().join('/') : value
}

function formatCnpj(value: string | null | undefined) {
  const digits = value?.replace(/\D/g, '') || ''
  return digits.length === 14 ? digits.replace(/^(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})$/, '$1.$2.$3/$4-$5') : value || 'Não informado'
}

function openDetails(row: Row) {
  selectedRow.value = row
  detailsDialog.value?.showModal()
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
  if (!canDelete.value || isLinked(row) || busy.value || rowBusy[row.id]) return
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

    <div class="flex flex-wrap items-center gap-x-5 gap-y-2 rounded-md border bg-muted/20 px-3 py-2 text-xs">
      <div class="space-y-1">
        <p class="font-medium">Anatel · Makisa Trading LTDA · Atualização diária</p>
        <p class="text-muted-foreground">
          Última consulta bem-sucedida: {{ formatTimestamp(anatelStatus?.ultimo_sucesso_em) }}
          <span v-if="anatelStatus?.source_updated_at"> · Base publicada em {{ formatTimestamp(anatelStatus.source_updated_at) }}</span>
        </p>
      </div>
      <button v-if="canAttach" class="ml-auto inline-flex items-center gap-1.5 rounded-md border bg-background px-3 py-1.5 text-xs hover:bg-muted disabled:opacity-50"
        :disabled="busy || hasBusyRow" @click="syncAnatel">
        <RefreshCw class="size-3.5" :class="{ 'animate-spin': synchronizing }" />
        {{ synchronizing ? 'Consultando Anatel...' : 'Atualizar agora' }}
      </button>
    </div>

    <p v-if="statusError || anatelStatus?.erro" role="status" class="text-xs text-amber-700 dark:text-amber-400">
      {{ statusError || 'A última tentativa de consulta à Anatel falhou. Os dados anteriores foram preservados; o sistema tentará novamente automaticamente.' }}
    </p>
    <p v-if="syncMessage" role="status" class="text-xs text-muted-foreground">{{ syncMessage }}</p>

    <div class="flex flex-wrap items-center gap-x-5 gap-y-2 rounded-md border bg-muted/20 px-3 py-2 text-xs">
      <div class="space-y-1">
        <p class="font-medium">Inmetro · Makisa Trading LTDA · Atualização diária</p>
        <p class="text-muted-foreground">Última consulta bem-sucedida: {{ formatTimestamp(inmetroStatus?.ultimo_sucesso_em) }}</p>
        <p class="text-muted-foreground">Fonte: ProdCert. Datas não informadas na fonte podem ser preenchidas manualmente.</p>
      </div>
      <button v-if="canAttach" class="ml-auto inline-flex items-center gap-1.5 rounded-md border bg-background px-3 py-1.5 text-xs hover:bg-muted disabled:opacity-50"
        :disabled="busy || hasBusyRow" @click="syncInmetro">
        <RefreshCw class="size-3.5" :class="{ 'animate-spin': synchronizingInmetro }" />
        {{ synchronizingInmetro ? 'Consultando Inmetro...' : 'Atualizar Inmetro' }}
      </button>
    </div>
    <p v-if="inmetroStatusError || inmetroStatus?.erro" role="status" class="text-xs text-amber-700 dark:text-amber-400">
      {{ inmetroStatusError || 'A última tentativa de consulta ao Inmetro falhou. Os dados anteriores foram preservados; o sistema tentará novamente automaticamente.' }}
    </p>
    <p v-if="inmetroSyncMessage" role="status" class="text-xs text-muted-foreground">{{ inmetroSyncMessage }}</p>
    <p v-if="inmetroSyncError" role="alert" class="text-sm text-destructive">{{ inmetroSyncError }}</p>
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
            <th class="text-left">Situação oficial</th>
            <th class="text-left">PDF do certificado</th>
            <th v-if="canDelete" class="w-8"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!loading && rows.length === 0">
            <td :colspan="canDelete ? 11 : 10" class="py-6 text-center text-muted-foreground">
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
                :readonly="isOfficialField(row, 'modelo')" :title="row.modelo || ''"
                @input="(e) => scheduleSave(row, 'modelo', (e.target as HTMLInputElement).value)" />
            </td>
            <td>
              <input class="cell-input" :value="row.nome_comercial ?? ''" :disabled="!canEdit || busy || rowBusy[row.id]"
                :readonly="isOfficialField(row, 'nome_comercial')" :title="row.nome_comercial || ''"
                @input="(e) => scheduleSave(row, 'nome_comercial', (e.target as HTMLInputElement).value)" />
            </td>
            <td>
              <select class="cell-input" :value="row.certificado ?? ''" :disabled="!canEdit || busy || rowBusy[row.id] || isOfficialField(row, 'certificado')"
                @change="(e) => scheduleSave(row, 'certificado', (e.target as HTMLSelectElement).value)">
                <option v-for="o in CERT_OPTIONS" :key="o" :value="o">{{ o || '—' }}</option>
              </select>
            </td>
            <td>
              <input class="cell-input" :value="row.numero ?? ''" :disabled="!canEdit || busy || rowBusy[row.id]"
                :readonly="isOfficialField(row, 'numero')" :title="fieldSourceTitle(row, 'numero')"
                @input="(e) => scheduleSave(row, 'numero', (e.target as HTMLInputElement).value)" />
            </td>
            <td>
              <input type="number" step="0.01" class="cell-input text-right"
                :value="row.valor ?? ''" :disabled="!canEdit || busy || rowBusy[row.id]"
                @input="(e) => scheduleSave(row, 'valor', (e.target as HTMLInputElement).value === '' ? null : Number((e.target as HTMLInputElement).value))" />
            </td>
            <td>
              <input type="date" class="cell-input" :value="row.inicio ?? ''" :disabled="!canEdit || busy || rowBusy[row.id]"
                :readonly="isOfficialField(row, 'inicio')" :title="fieldSourceTitle(row, 'inicio')"
                @input="(e) => scheduleSave(row, 'inicio', (e.target as HTMLInputElement).value || null)" />
            </td>
            <td>
              <input type="date" class="cell-input" :value="row.fim ?? ''" :disabled="!canEdit || busy || rowBusy[row.id]"
                :readonly="isOfficialField(row, 'fim')" :title="fieldSourceTitle(row, 'fim')"
                @input="(e) => scheduleSave(row, 'fim', (e.target as HTMLInputElement).value || null)" />
            </td>
            <td class="official-status-cell">
              <div class="flex items-center gap-2">
                <span :class="officialSituationClass(row)">{{ officialSituation(row) }}</span>
                <button v-if="isLinked(row)" class="shrink-0 text-primary hover:underline" @click="openDetails(row)"
                  :aria-label="`Ver dados de ${officialSource(row)} de ${row.produto || row.numero}`">Ver</button>
              </div>
              <span v-if="isMissingSource(row)" class="block text-[10px] text-amber-700 dark:text-amber-400">
                Não localizado na última consulta · dados anteriores
              </span>
              <span v-else-if="officialAlerts(row).length" class="block text-[10px] text-amber-700 dark:text-amber-400">
                Confira as observações da fonte em “Ver”
              </span>
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
              <button v-if="!isLinked(row)" class="text-muted-foreground hover:text-destructive disabled:opacity-50" :disabled="busy || rowBusy[row.id]"
                aria-label="Excluir certificação" @click="removeRow(row)">
                <Trash2 class="size-3.5" />
              </button>
              <span v-else class="text-muted-foreground" :title="`Dados atualizados automaticamente por ${officialSource(row)}`">—</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <dialog ref="detailsDialog" class="certification-dialog rounded-lg border bg-background p-0 text-foreground shadow-xl"
      aria-labelledby="certification-details-title" @close="selectedRow = null"
      @click="($event.target === $event.currentTarget) && detailsDialog?.close()">
      <div v-if="selectedRow" class="space-y-4 p-5">
        <div class="flex items-start justify-between gap-4">
          <div>
            <h2 id="certification-details-title" class="text-lg font-semibold">Dados oficiais · {{ officialSource(selectedRow) }}</h2>
            <p class="text-sm text-muted-foreground">{{ selectedRow.produto || officialData(selectedRow)?.produto || 'Certificação' }} · {{ selectedRow.numero }}</p>
          </div>
          <button class="rounded p-1 hover:bg-muted" aria-label="Fechar dados oficiais" @click="detailsDialog?.close()"><X class="size-4" /></button>
        </div>
        <p v-if="isMissingSource(selectedRow)" class="rounded bg-amber-50 p-3 text-sm text-amber-800 dark:bg-amber-950/30 dark:text-amber-300">
          Este registro não foi localizado na última consulta. Os dados abaixo são da consulta anterior; isso não confirma cancelamento ou irregularidade.
        </p>
        <dl v-if="selectedRow.anatel_numero" class="details-grid grid grid-cols-1 gap-x-5 gap-y-3 text-sm sm:grid-cols-2">
          <div><dt>Empresa</dt><dd>{{ selectedRow.anatel_dados?.nome_empresa || anatelStatus?.nome_empresa || 'Makisa Trading LTDA' }}</dd></div>
          <div><dt>CNPJ</dt><dd>{{ formatCnpj(selectedRow.anatel_dados?.cnpj || anatelStatus?.cnpj) }}</dd></div>
          <div><dt>Situação do requerimento</dt><dd>{{ selectedRow.anatel_dados?.situacao_requerimento || 'Não informada na fonte' }}</dd></div>
          <div><dt>Situação do certificado</dt><dd>{{ selectedRow.anatel_dados?.situacao_certificado || 'Não informada na fonte' }}</dd></div>
          <div><dt>Início do certificado</dt><dd>{{ formatDate(selectedRow.anatel_dados?.inicio) }}</dd></div>
          <div><dt>Validade do certificado</dt><dd>{{ formatDate(selectedRow.anatel_dados?.fim) }}</dd></div>
          <div class="sm:col-span-2"><dt>Produto na fonte</dt><dd>{{ selectedRow.anatel_dados?.produto || 'Não informado' }}</dd></div>
          <div><dt>Modelos</dt><dd>{{ selectedRow.anatel_dados?.modelos?.join(', ') || 'Não informados' }}</dd></div>
          <div><dt>Nomes comerciais</dt><dd>{{ selectedRow.anatel_dados?.nomes_comerciais?.join(', ') || 'Não informados' }}</dd></div>
          <div><dt>Tipos de produto</dt><dd>{{ selectedRow.anatel_dados?.tipos_produto?.join(', ') || 'Não informados' }}</dd></div>
          <div><dt>Fabricantes</dt><dd>{{ selectedRow.anatel_dados?.fabricantes?.join(', ') || 'Não informados' }}</dd></div>
          <div class="sm:col-span-2"><dt>Certificados</dt><dd>{{ selectedRow.anatel_dados?.certificados?.join(', ') || 'Não informados' }}</dd></div>
          <div><dt>Consulta deste registro</dt><dd>{{ formatTimestamp(selectedRow.anatel_consultado_em) }}</dd></div>
        </dl>
        <dl v-else class="details-grid grid grid-cols-1 gap-x-5 gap-y-3 text-sm sm:grid-cols-2">
          <div><dt>Empresa</dt><dd>{{ selectedRow.inmetro_dados?.nome_empresa || inmetroStatus?.nome_empresa || 'Makisa Trading LTDA' }}</dd></div>
          <div><dt>CNPJ</dt><dd>{{ formatCnpj(selectedRow.inmetro_dados?.cnpj || inmetroStatus?.cnpj) }}</dd></div>
          <div><dt>Organismo certificador</dt><dd>{{ selectedRow.inmetro_dados?.certificador || 'Não informado na fonte' }}</dd></div>
          <div><dt>Situação do certificado</dt><dd>{{ selectedRow.inmetro_dados?.situacao_certificado || 'Não informada na fonte' }}</dd></div>
          <div><dt>Número do certificado</dt><dd>{{ selectedRow.inmetro_dados?.numero || 'Não informado na fonte' }}</dd></div>
          <div><dt>Modelo</dt><dd>{{ selectedRow.inmetro_dados?.modelo || 'Não informado na fonte' }}</dd></div>
          <div><dt>Marca</dt><dd>{{ selectedRow.inmetro_dados?.marca || 'Não informada na fonte' }}</dd></div>
          <div><dt>Produto na fonte</dt><dd>{{ selectedRow.inmetro_dados?.produto || 'Não informado na fonte' }}</dd></div>
          <div><dt>Emissão do certificado</dt><dd>{{ selectedRow.inmetro_dados?.inicio ? formatDate(selectedRow.inmetro_dados.inicio) : 'Não informada pelo ProdCert' }}</dd></div>
          <div><dt>Validade do certificado</dt><dd>{{ selectedRow.inmetro_dados?.fim ? formatDate(selectedRow.inmetro_dados.fim) : 'Não informada pelo ProdCert' }}</dd></div>
          <div class="sm:col-span-2"><dt>Descrição na fonte</dt><dd>{{ selectedRow.inmetro_dados?.descricao || 'Não informada na fonte' }}</dd></div>
          <div><dt>Consulta deste registro</dt><dd>{{ formatTimestamp(selectedRow.inmetro_consultado_em) }}</dd></div>
        </dl>
        <div v-if="officialAlerts(selectedRow).length" class="rounded border border-amber-300/60 p-3 text-sm">
          <p class="mb-1 font-medium">Observações da fonte</p>
          <ul class="list-disc space-y-1 pl-4"><li v-for="(alerta, index) in officialAlerts(selectedRow)" :key="index">{{ alerta }}</li></ul>
        </div>
        <div class="flex flex-wrap items-center justify-between gap-3 border-t pt-3 text-xs text-muted-foreground">
          <span>A validade por data é independente da situação informada pela fonte.</span>
          <a :href="selectedRow.anatel_numero ? ANATEL_SOURCE_URL : INMETRO_SOURCE_URL" target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1 text-primary hover:underline">
            {{ selectedRow.anatel_numero ? 'Base oficial da Anatel' : 'Consulta oficial do Inmetro · ProdCert' }} <ExternalLink class="size-3" />
          </a>
        </div>
      </div>
    </dialog>
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
.cell-input:read-only {
  background: transparent;
}
.grid-table .official-status-cell {
  min-width: 170px;
  max-width: 270px;
  white-space: normal;
}
.certification-dialog {
  width: min(720px, calc(100vw - 32px));
  max-height: calc(100vh - 48px);
}
.certification-dialog::backdrop {
  background: rgb(0 0 0 / 0.45);
}
.details-grid dt {
  color: hsl(var(--muted-foreground));
  font-size: 12px;
}
.details-grid dd {
  overflow-wrap: anywhere;
}
</style>
