<script setup lang="ts">
// Suprimentos — certificações (Anatel/Inmetro/isento) com alerta visual
// de validade: linha âmbar quando faltam < 30 dias, vermelha quando já
// venceu. Auto-save inline, mesmo padrão da página Consórcio.
// Desde 05/11/2026 é componente: aba "Certificações" da Importação (no
// lugar da antiga aba Kit) e a rota antiga /financeiro/suprimentos, que
// saiu do menu.
// `embutido` tira o recuo da página quando vem dentro da Importação.
// M-67 (25/11/2026): barra com Layout ▾ (mostra/esconde Modelo, Número, Valor
// e Situação oficial) e Excel (.xlsx), no lugar do "Baixar tabela em PDF".
// M-66 (26/11/2026): topo limpo, no padrão da Tabela de Preços. Saíram a
// contagem de itens, a legenda de cores, os blocos Anatel/Inmetro, a frase de
// edição e os botões Recarregar / Atualizar agora / Atualizar Inmetro: tudo
// virou o botão azul "Atualizar ▾" com as caixas Anatel e Inmetro. A Anvisa
// fica fora do menu: não existe consulta de Anvisa no sistema.
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ChevronDown, Download, ExternalLink, Loader2, Paperclip, Plus, RefreshCw, Trash2, X } from 'lucide-vue-next'
import { PopoverContent, PopoverPortal, PopoverRoot, PopoverTrigger } from 'reka-ui'
import { createCertificacoesAutosave } from '~/utils/certificacoesAutosave'
import { gerarXlsx, TIPO_XLSX, type CelulaPlanilha } from '~/lib/planilhaXlsx'

defineProps<{ embutido?: boolean }>()

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
  valor: number | string | null
  inicio: string | null  // YYYY-MM-DD
  fim: string | null
  tem_pdf: boolean
  pdf_nome: string | null
  campos_manuais: string[] | null
  anatel_numero: string | null
  anatel_dados: AnatelRecord | null
  anatel_consultado_em: string | null
  anatel_encontrado: boolean | null
  inmetro_chave: string | null
  inmetro_dados: InmetroRecord | null
  inmetro_consultado_em: string | null
  inmetro_encontrado: boolean | null
  // Garantia (meses) do certificado (06/11/2026): vale para todos os produtos
  // dele na Tabela de Preços. null = não define; 0 = sem garantia.
  garantia_meses: number | null
}

const rows = ref<Row[]>([])
const mobileTableMode = ref(false)
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
  () => { errorText.value = 'Não foi possível salvar as alterações. Tente novamente antes de atualizar ou baixar o Excel.' },
)
onBeforeUnmount(() => { void autosave.flush().catch(() => {}) })

// Layout ▾ (M-67): caixas para mostrar ou esconder colunas da tabela, como o
// Layout da Tabela de Preços (marcada = aparece; tudo marcado no começo;
// lembrado neste navegador). Produto, Nome comercial, Certificado, Início,
// Fim, Garantia e PDF do certificado ficam sempre à mostra. O menu mostra só a
// caixa e o nome. O Excel leva só as colunas à mostra.
const COLUNAS_LAYOUT = [
  { chave: 'modelo', rotulo: 'Modelo' },
  { chave: 'numero', rotulo: 'Número' },
  { chave: 'valor', rotulo: 'Valor' },
  { chave: 'situacao', rotulo: 'Situação' },
]
const CHAVE_COLUNAS_OCULTAS = 'certificacoes.colunasOcultas'
// Guarda as DESMARCADAS: vazio = tudo à mostra.
const colunasOcultas = ref<string[]>([])

function mostraColuna(chave: string): boolean {
  return !colunasOcultas.value.includes(chave)
}

function alternarColuna(chave: string) {
  colunasOcultas.value = mostraColuna(chave)
    ? [...colunasOcultas.value, chave]
    : colunasOcultas.value.filter((c) => c !== chave)
}

// O que veio do navegador ("modelo,valor"): só as chaves que existem.
function lerColunasOcultas(texto: string | null): string[] {
  const validas = new Set(COLUNAS_LAYOUT.map((c) => c.chave))
  return [...new Set((texto || '').split(',').map((s) => s.trim()).filter((s) => validas.has(s)))]
}

// Colunas da tabela (o colspan da linha "Nenhuma certificação"): as 11 menos
// as escondidas, mais a lixeira de quem pode excluir.
const totalColunas = computed(() => 11 - colunasOcultas.value.length + (canDelete.value ? 1 : 0))

// Lidas só depois de montar (no servidor não há localStorage).
function lerPreferencia(chave: string): string | null {
  try {
    return localStorage.getItem(chave)
  } catch {
    return null
  }
}
function gravarPreferencia(chave: string, valor: string) {
  try {
    localStorage.setItem(chave, valor)
  } catch {
    /* navegador sem armazenamento: vale só nesta visita */
  }
}
onMounted(() => {
  colunasOcultas.value = lerColunasOcultas(lerPreferencia(CHAVE_COLUNAS_OCULTAS))
  watch(colunasOcultas, (v) => gravarPreferencia(CHAVE_COLUNAS_OCULTAS, v.join(',')))
})

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
    statusError.value = 'Não foi possível consultar a atualização automática da Anatel. Tente atualizar em instantes.'
  }
}

async function loadInmetroStatus() {
  try {
    inmetroStatus.value = await api<AnatelStatus>('/api/financeiro/suprimentos/inmetro/status')
    inmetroStatusError.value = null
  } catch {
    inmetroStatusError.value = 'Não foi possível consultar a atualização automática do Inmetro. Tente atualizar em instantes.'
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
    errorText.value = 'Não foi possível reler a lista de certificações. Os dados exibidos foram preservados. Tente novamente.'
  }
}

// `limparAviso = false` (M-66): o Atualizar ▾ relê a lista depois das
// consultas sem apagar o aviso de erro que uma consulta deixou.
async function load(limparAviso = true) {
  if (busy.value || hasBusyRow.value) return
  loading.value = true
  if (limparAviso) errorText.value = null
  try {
    await autosave.flush()
    await reloadData()
  } catch {
    errorText.value = 'Não foi possível salvar as alterações. Tente atualizar novamente.'
  } finally {
    loading.value = false
  }
}
await load()

function scheduleSave(row: Row, field: keyof Row, value: any) {
  if (!canEdit.value || busy.value || rowBusy[row.id]) return
  if (row[field] !== value) row.campos_manuais = [...new Set([...(row.campos_manuais || []), field])]
  ;(row as any)[field] = value
  autosave.schedule(row.id, field, value)
}

// Garantia (meses) do certificado (06/11/2026, Eduardo: "em certificações
// adicione o campo garantia, e aí ele passa a mudar de acordo com o
// certificado"). Mudar aqui muda a Garantia (meses) de TODOS os produtos deste
// certificado na Tabela de Preços (o servidor grava a vigência e recalcula as
// garantias). Por isso salva no @change (ao sair do campo), não a cada tecla.
// Vazio = o certificado não define (os produtos ficam com o deles); inteiro
// de 0 a 120 (0 = sem garantia); o resto não salva e avisa. O campo é texto
// (inputmode numérico), como a Garantia da Tabela de Preços: o type="number"
// entrega "" para "12e" ou "doze", e isso apagaria a garantia do certificado.
const GARANTIA_MESES_MAX = 120
function lerGarantiaMeses(raw: string): number | null | undefined {
  const t = raw.trim()
  if (!t) return null
  if (!/^\d+$/.test(t)) return undefined
  const n = parseInt(t, 10)
  return n <= GARANTIA_MESES_MAX ? n : undefined
}
function salvarGarantia(row: Row, raw: string) {
  const n = lerGarantiaMeses(raw)
  if (n === undefined) {
    errorText.value = `Garantia: prazo inválido ("${raw}") — use um número inteiro de meses, de 0 (sem garantia) a ${GARANTIA_MESES_MAX}, ou deixe vazio.`
    return
  }
  if (errorText.value?.startsWith('Garantia:')) errorText.value = null
  if ((row.garantia_meses ?? null) === n) return
  scheduleSave(row, 'garantia_meses', n)
}

// Devolve false quando não deu para salvar as edições pendentes (o Atualizar ▾
// para aí, para não reler a lista por cima delas). `recarregar = false`: quem
// chamou relê a lista inteira no fim (Atualizar ▾).
async function syncAnatel(recarregar = true): Promise<boolean> {
  if (!canAttach.value || busy.value || hasBusyRow.value) return false
  synchronizing.value = true
  errorText.value = null
  syncMessage.value = null
  try {
    try {
      await autosave.flush()
    } catch {
      errorText.value = 'As alterações não foram salvas. Tente atualizar novamente para preservar suas edições.'
      return false
    }
    const result = await api<SyncResult>('/api/financeiro/suprimentos/anatel/sincronizar', { method: 'POST' })
    if (result.status === 'ok') {
      syncMessage.value = `Consulta concluída: ${result.criados} novos registros e ${result.atualizados} atualizados.`
      if (result.nao_localizados) syncMessage.value += ` ${result.nao_localizados} não localizados na última consulta; dados anteriores preservados.`
      if (result.conflitos) syncMessage.value += ` ${result.conflitos} registros precisam de conferência antes do vínculo automático.`
      if (recarregar) await reloadData('anatel')
    } else if (result.status === 'busy') {
      syncMessage.value = 'Uma consulta à Anatel já está em andamento. Atualize em instantes para ver o resultado.'
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
  return true
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

function hasManualOfficialFields(row: Row) {
  return Boolean(row.campos_manuais?.some(field => isOfficialField(row, field as keyof Row)))
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
  if (isOfficialField(row, field)) return `Dados de ${officialSource(row)}; suas edições serão preservadas.`
  if (row.inmetro_chave && (field === 'inicio' || field === 'fim')) return 'Data preenchida manualmente; não informada pelo ProdCert.'
  return ''
}

function officialSituation(row: Row) {
  if (!isLinked(row)) return 'Manual · sem confirmação automática'
  if (hasManualOfficialFields(row)) return 'Cadastro editado · consultar fonte'
  const situation = row.anatel_numero ? row.anatel_dados?.situacao_requerimento : row.inmetro_dados?.situacao_certificado
  return `${officialSource(row)} · ${situation || 'Situação não informada na fonte'}`
}

function officialSituationClass(row: Row) {
  if (!isLinked(row) || isMissingSource(row) || hasManualOfficialFields(row)) return 'text-muted-foreground'
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

// Mesmo contrato do syncAnatel (devolve false se as edições não foram salvas).
async function syncInmetro(recarregar = true): Promise<boolean> {
  if (!canAttach.value || busy.value || hasBusyRow.value) return false
  synchronizingInmetro.value = true
  inmetroSyncError.value = null
  inmetroSyncMessage.value = null
  try {
    try {
      await autosave.flush()
    } catch {
      inmetroSyncError.value = 'As alterações não foram salvas. Tente atualizar o Inmetro novamente para preservar suas edições.'
      return false
    }
    const result = await api<SyncResult>('/api/financeiro/suprimentos/inmetro/sincronizar', { method: 'POST' })
    if (result.status === 'ok') {
      inmetroSyncMessage.value = `Consulta ao Inmetro concluída: ${result.criados} novos registros e ${result.atualizados} atualizados.`
      if (result.nao_localizados) inmetroSyncMessage.value += ` ${result.nao_localizados} não localizados na última consulta; dados anteriores preservados.`
      if (result.conflitos) inmetroSyncMessage.value += ` ${result.conflitos} registros precisam de conferência antes do vínculo automático.`
      if (recarregar) await reloadData('inmetro')
    } else if (result.status === 'busy') {
      inmetroSyncMessage.value = 'Uma consulta ao Inmetro já está em andamento. Atualize em instantes para ver o resultado.'
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
  return true
}

// ── Atualizar ▾ (M-66) ─────────────────────────────────────────────────
// No padrão do Atualizar da Tabela de Preços: o menu tem as caixas Anatel e
// Inmetro (as duas marcadas toda vez que abre) e o botão azul "Atualizar".
// Confirmar faz, para cada órgão marcado, o que faziam os antigos "Atualizar
// agora" (Anatel) e "Atualizar Inmetro" e, no fim, o que fazia o Recarregar:
// salva as edições pendentes e relê a lista e a situação das duas consultas.
// Quem só vê não tem o menu: o Atualizar só relê a lista.
// Anvisa: fica fora até existir uma consulta de Anvisa no sistema.
const menuAtualizarAberto = ref(false)
const atualizarAnatel = ref(true)
const atualizarInmetro = ref(true)
const atualizandoAlgo = computed(() => loading.value || synchronizing.value || synchronizingInmetro.value)
const rotuloAtualizar = computed(() =>
  synchronizing.value ? 'Consultando Anatel...'
    : synchronizingInmetro.value ? 'Consultando Inmetro...'
      : loading.value ? 'Atualizando...' : 'Atualizar')

function abrirMenuAtualizar(aberto: boolean) {
  menuAtualizarAberto.value = aberto
  if (aberto) {
    atualizarAnatel.value = true
    atualizarInmetro.value = true
  }
}

async function executarAtualizar() {
  if (busy.value || hasBusyRow.value) return
  menuAtualizarAberto.value = false
  const anatel = canAttach.value && atualizarAnatel.value
  const inmetro = canAttach.value && atualizarInmetro.value
  // As mensagens da vez anterior saem; as desta vez ficam até a próxima.
  errorText.value = null
  syncMessage.value = null
  inmetroSyncMessage.value = null
  inmetroSyncError.value = null
  if (anatel && !(await syncAnatel(false))) return
  if (inmetro && !(await syncInmetro(false))) return
  await load(false)
}

// Aviso de falha da consulta diária (M-66): uma linha só, abaixo da barra, e
// só quando a última tentativa falhou ou não deu para ler a situação (some
// quando a consulta volta a funcionar). É o único aviso de que o robô parou.
const avisoFalhaConsulta = computed(() => {
  const orgaos: string[] = []
  if (!statusError.value && anatelStatus.value?.erro) orgaos.push('à Anatel')
  if (!inmetroStatusError.value && inmetroStatus.value?.erro) orgaos.push('ao Inmetro')
  const partes: string[] = []
  if (orgaos.length) {
    partes.push(`A última tentativa de consulta ${orgaos.join(' e ')} falhou. Os dados anteriores foram preservados; o sistema tentará novamente automaticamente.`)
  }
  if (statusError.value) partes.push(statusError.value)
  if (inmetroStatusError.value) partes.push(inmetroStatusError.value)
  return partes.join(' ')
})

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

function baixarBlob(blob: Blob, filename: string) {
  const objectUrl = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = objectUrl
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000)
}

async function downloadPdf(path: string, filename: string) {
  const response = await fetch(url(path), { credentials: 'include' })
  if (!response.ok) throw new Error('Não foi possível baixar o PDF. Tente novamente.')
  baixarBlob(await response.blob(), filename)
}

// Excel (M-67, no lugar do "Baixar tabela em PDF"): todas as linhas da lista,
// na ordem da tela, com as colunas à mostra (Layout), na ordem da tabela.
// Início e Fim vão como data; Valor e Garantia (meses) como número; vazio fica
// em branco. "PDF do certificado" leva o nome do arquivo anexado.
function linhasExcel(): CelulaPlanilha[][] {
  const numero = (v: unknown): number | '' => {
    if (v == null || v === '') return ''
    const n = Number(v)
    return Number.isFinite(n) ? n : ''
  }
  const data = (v: string | null): CelulaPlanilha => (v ? { data: v } : '')
  const cab: CelulaPlanilha[] = ['Produto']
  if (mostraColuna('modelo')) cab.push('Modelo')
  cab.push('Nome comercial', 'Certificado')
  if (mostraColuna('numero')) cab.push('Número')
  if (mostraColuna('valor')) cab.push('Valor')
  cab.push('Início', 'Fim', 'Garantia (meses)')
  if (mostraColuna('situacao')) cab.push('Situação oficial')
  cab.push('PDF do certificado')
  const linhas: CelulaPlanilha[][] = [cab]
  for (const row of rows.value) {
    const l: CelulaPlanilha[] = [row.produto ?? '']
    if (mostraColuna('modelo')) l.push(row.modelo ?? '')
    l.push(row.nome_comercial ?? '', row.certificado ?? '')
    if (mostraColuna('numero')) l.push(row.numero ?? '')
    if (mostraColuna('valor')) l.push(numero(row.valor))
    l.push(data(row.inicio), data(row.fim), numero(row.garantia_meses))
    if (mostraColuna('situacao')) l.push(officialSituation(row))
    l.push(row.tem_pdf ? (row.pdf_nome || 'Sim') : '')
    linhas.push(l)
  }
  return linhas
}

function hojeIso(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

// Antes de baixar, salva as edições pendentes (a planilha sai com o que está na tela).
async function exportTable() {
  if (busy.value || hasBusyRow.value) return
  exporting.value = true
  errorText.value = null
  try {
    try {
      await autosave.flush()
    } catch {
      throw new Error('As alterações não foram salvas. Tente baixar o Excel novamente.')
    }
    const bytes = gerarXlsx(linhasExcel(), 'Certificações')
    baixarBlob(new Blob([bytes as BlobPart], { type: TIPO_XLSX }), `certificacoes-${hojeIso()}.xlsx`)
  } catch (e: any) {
    errorText.value = e?.message || 'Não foi possível gerar o Excel.'
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
</script>

<template>
  <div class="space-y-3" :class="{ 'p-4': !embutido }">
    <!-- Barra no padrão da Tabela de Preços (M-66/M-67): título, Layout ▾,
         Excel, Atualizar ▾ e, à direita, o botão de criar linha. -->
    <div class="flex flex-wrap items-center gap-2" data-barra-certificacoes>
      <h1 class="mr-1 text-xl font-semibold">Certificações</h1>
      <!-- Layout ▾: mostra/esconde Modelo, Número, Valor e Situação oficial.
           Só a caixa e o nome; lembrado neste navegador. -->
      <PopoverRoot>
        <PopoverTrigger as-child>
          <button type="button" class="btn btn-sm h-7 py-0" data-acao="layout-certificacoes" title="Escolher as colunas que a tabela mostra">
            Layout
            <ChevronDown class="h-3 w-3 ml-1" />
          </button>
        </PopoverTrigger>
        <PopoverPortal>
          <PopoverContent
            side="bottom"
            align="start"
            :side-offset="4"
            :collision-padding="8"
            data-menu-layout-certificacoes
            class="z-[70] w-64 max-w-[calc(100vw-16px)] rounded-md border bg-background p-2 shadow-lg"
          >
            <label
              v-for="c in COLUNAS_LAYOUT"
              :key="c.chave"
              class="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm hover:bg-muted/50"
            >
              <input
                type="checkbox"
                class="size-4"
                :checked="mostraColuna(c.chave)"
                :data-campo="`layout-certificacoes-${c.chave}`"
                @change="alternarColuna(c.chave)"
              />
              <span>{{ c.rotulo }}</span>
            </label>
          </PopoverContent>
        </PopoverPortal>
      </PopoverRoot>
      <button class="btn btn-sm h-7 py-0" :disabled="busy || hasBusyRow" data-acao="excel-certificacoes" @click="exportTable">
        <Download class="h-4 w-4 mr-1" /> {{ exporting ? 'Gerando...' : 'Excel' }}
      </button>
      <!-- Atualizar ▾ (M-66; no lugar do Recarregar e dos botões das consultas).
           Toda vez que abre: Anatel e Inmetro marcadas. Anvisa não entra: não
           existe consulta de Anvisa. Quem só vê: o botão só relê a lista. -->
      <PopoverRoot v-if="canAttach" :open="menuAtualizarAberto" @update:open="abrirMenuAtualizar">
        <PopoverTrigger as-child>
          <button
            type="button"
            class="btn btn-sm h-7 py-0 bg-blue-600 text-white hover:bg-blue-700 hover:text-white"
            data-acao="atualizar"
            :disabled="busy || hasBusyRow"
            title="Consultar a Anatel e o Inmetro e reler a lista"
          >
            <Loader2 v-if="atualizandoAlgo" class="h-4 w-4 mr-1 animate-spin" />
            <RefreshCw v-else class="h-4 w-4 mr-1" />
            {{ rotuloAtualizar }}
            <ChevronDown class="h-3 w-3 ml-1" />
          </button>
        </PopoverTrigger>
        <PopoverPortal>
          <PopoverContent
            side="bottom"
            align="start"
            :side-offset="4"
            :collision-padding="8"
            data-menu-atualizar
            class="z-[70] w-56 max-w-[calc(100vw-16px)] rounded-md border bg-background py-1 shadow-lg"
          >
            <div class="flex h-[26px] items-center px-3 text-[11px] font-semibold text-muted-foreground">
              O que consultar
            </div>
            <label class="flex h-[26px] cursor-pointer items-center gap-2 px-3 text-sm hover:bg-muted/50">
              <input v-model="atualizarAnatel" type="checkbox" class="size-4 shrink-0" data-campo="atualizar-anatel" />
              Anatel
            </label>
            <label class="flex h-[26px] cursor-pointer items-center gap-2 px-3 text-sm hover:bg-muted/50">
              <input v-model="atualizarInmetro" type="checkbox" class="size-4 shrink-0" data-campo="atualizar-inmetro" />
              Inmetro
            </label>
            <div class="mt-1 border-t px-3 pb-1 pt-2">
              <button
                type="button"
                class="btn btn-sm h-7 w-full py-0 bg-blue-600 text-white hover:bg-blue-700 hover:text-white"
                data-acao="atualizar-executar"
                :disabled="busy || hasBusyRow"
                title="Consulta os órgãos marcados e relê a lista"
                @click="executarAtualizar"
              >
                <RefreshCw class="h-4 w-4 mr-1" /> Atualizar
              </button>
            </div>
          </PopoverContent>
        </PopoverPortal>
      </PopoverRoot>
      <button
        v-else
        type="button"
        class="btn btn-sm h-7 py-0 bg-blue-600 text-white hover:bg-blue-700 hover:text-white"
        data-acao="atualizar"
        :disabled="busy || hasBusyRow"
        title="Reler a lista"
        @click="executarAtualizar"
      >
        <Loader2 v-if="atualizandoAlgo" class="h-4 w-4 mr-1 animate-spin" />
        <RefreshCw v-else class="h-4 w-4 mr-1" />
        {{ rotuloAtualizar }}
      </button>
      <button
        v-if="canEdit"
        type="button"
        class="btn btn-sm h-7 py-0 btn-primary ml-auto"
        data-acao="nova-certificacao"
        :disabled="busy"
        @click="addRow"
      >
        <Plus class="h-4 w-4 mr-1" /> Nova certificação
      </button>
    </div>

    <!-- Uma linha só, e só quando a consulta diária falhou (M-66). -->
    <p v-if="avisoFalhaConsulta" role="status" class="truncate text-xs text-amber-700 dark:text-amber-400" :title="avisoFalhaConsulta" data-aviso-falha-consulta>
      {{ avisoFalhaConsulta }}
    </p>
    <p v-if="syncMessage" role="status" class="text-xs text-muted-foreground">{{ syncMessage }}</p>
    <p v-if="inmetroSyncMessage" role="status" class="text-xs text-muted-foreground">{{ inmetroSyncMessage }}</p>
    <p v-if="inmetroSyncError" role="alert" class="text-sm text-destructive">{{ inmetroSyncError }}</p>
    <div v-if="errorText" role="alert" class="text-sm text-destructive">{{ errorText }}</div>

    <div class="flex justify-end lg:hidden">
      <button class="rounded-lg border px-3 py-2 text-xs" :aria-pressed="mobileTableMode" @click="mobileTableMode = !mobileTableMode">{{ mobileTableMode ? 'Ver cartões' : 'Ver tabela' }}</button>
    </div>
    <section v-if="!mobileTableMode" class="grid gap-3 sm:grid-cols-2 lg:hidden" aria-label="Certificações por produto">
      <article v-for="row in rows" :key="row.id" class="min-w-0 rounded-xl border p-3" :class="rowStatusClass(row)">
        <div class="flex flex-wrap items-start justify-between gap-2">
          <div class="min-w-0"><h2 class="break-words text-sm font-semibold">{{ row.produto || 'Produto não informado' }}</h2><p class="mt-1 break-words text-xs text-muted-foreground">{{ row.modelo || 'Modelo não informado' }}</p></div>
          <span class="rounded-md bg-muted px-2 py-1 text-xs uppercase">{{ row.certificado || 'Não informado' }}</span>
        </div>
        <dl class="mt-3 space-y-2 text-xs">
          <div class="flex justify-between gap-3"><dt class="text-muted-foreground">Validade</dt><dd class="text-right font-medium">{{ formatDate(row.fim) }}</dd></div>
          <div class="flex justify-between gap-3"><dt class="text-muted-foreground">Garantia</dt><dd class="text-right">{{ row.garantia_meses == null ? 'Não definida' : row.garantia_meses === 0 ? 'Sem garantia' : `${row.garantia_meses} ${row.garantia_meses === 1 ? 'mês' : 'meses'}` }}</dd></div>
          <div class="flex justify-between gap-3"><dt class="text-muted-foreground">Situação oficial</dt><dd class="text-right" :class="officialSituationClass(row)">{{ officialSituation(row) }}</dd></div>
        </dl>
        <p v-if="isMissingSource(row)" class="mt-2 text-xs text-amber-700 dark:text-amber-400">Não localizado na última consulta · dados anteriores</p>
        <details class="mt-3 border-t pt-2 text-xs">
          <summary class="cursor-pointer py-2 text-muted-foreground">Mais detalhes</summary>
          <dl class="space-y-2 pt-2">
            <div><dt class="text-muted-foreground">Nome comercial</dt><dd class="break-words">{{ row.nome_comercial || '—' }}</dd></div>
            <div><dt class="text-muted-foreground">Número</dt><dd class="break-all">{{ row.numero || '—' }}</dd></div>
            <div><dt class="text-muted-foreground">Início</dt><dd>{{ formatDate(row.inicio) }}</dd></div>
            <div><dt class="text-muted-foreground">Valor</dt><dd>{{ row.valor == null ? '—' : Number(row.valor).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' }) }}</dd></div>
          </dl>
          <p v-if="officialAlerts(row).length" class="mt-2 text-amber-700 dark:text-amber-400">Confira as observações nos dados oficiais.</p>
        </details>
        <div class="mt-2 flex flex-wrap items-center gap-2">
          <button v-if="isLinked(row)" class="rounded-lg border px-3 py-2 text-xs" @click="openDetails(row)">Dados oficiais</button>
          <button v-if="row.tem_pdf" class="inline-flex items-center gap-1 rounded-lg border px-3 py-2 text-xs" :disabled="busy || rowBusy[row.id]" @click="downloadCertificate(row)"><Download class="size-3.5" /> Baixar PDF</button>
          <span v-else class="text-xs text-muted-foreground">Sem PDF</span>
          <button v-if="canEdit" class="rounded-lg px-3 py-2 text-xs text-primary" @click="mobileTableMode = true">Editar na tabela</button>
        </div>
      </article>
      <p v-if="!loading && !rows.length" class="py-6 text-center text-sm text-muted-foreground">Nenhuma certificação cadastrada.</p>
    </section>
    <div class="border overflow-x-auto" :class="mobileTableMode ? '' : 'hidden lg:block'">
      <table class="grid-table w-full text-xs border-collapse">
        <thead>
          <tr class="bg-emerald-800 text-white text-[10px] uppercase tracking-wide">
            <th class="text-left">Produto</th>
            <th v-if="mostraColuna('modelo')" class="text-left">Modelo</th>
            <th class="text-left">Nome comercial</th>
            <th class="text-left">Certificado</th>
            <th v-if="mostraColuna('numero')" class="text-left">Número</th>
            <th v-if="mostraColuna('valor')" class="text-right">Valor</th>
            <th class="text-left">Início</th>
            <th class="text-left">Fim</th>
            <th class="text-right" data-col-garantia-meses
              title="Prazo da garantia, em meses (0 = sem garantia). Mudar aqui muda a Garantia (meses) de todos os produtos deste certificado na Tabela de Preços. Vazio = o certificado não define (cada produto fica com o seu).">Garantia (meses)</th>
            <th v-if="mostraColuna('situacao')" class="text-left">Situação oficial</th>
            <th class="text-left">PDF do certificado</th>
            <th v-if="canDelete" class="w-8"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!loading && rows.length === 0">
            <td :colspan="totalColunas" class="py-6 text-center text-muted-foreground">
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
            <td v-if="mostraColuna('modelo')">
              <input class="cell-input" :value="row.modelo ?? ''" :disabled="!canEdit || busy || rowBusy[row.id]"
                :title="row.modelo || ''"
                @input="(e) => scheduleSave(row, 'modelo', (e.target as HTMLInputElement).value)" />
            </td>
            <td>
              <input class="cell-input" :value="row.nome_comercial ?? ''" :disabled="!canEdit || busy || rowBusy[row.id]"
                :title="row.nome_comercial || ''"
                @input="(e) => scheduleSave(row, 'nome_comercial', (e.target as HTMLInputElement).value)" />
            </td>
            <td>
              <select class="cell-input" :value="row.certificado ?? ''" :disabled="!canEdit || busy || rowBusy[row.id]"
                @change="(e) => scheduleSave(row, 'certificado', (e.target as HTMLSelectElement).value)">
                <option v-for="o in CERT_OPTIONS" :key="o" :value="o">{{ o || '—' }}</option>
              </select>
            </td>
            <td v-if="mostraColuna('numero')">
              <input class="cell-input" :value="row.numero ?? ''" :disabled="!canEdit || busy || rowBusy[row.id]"
                :title="fieldSourceTitle(row, 'numero')"
                @input="(e) => scheduleSave(row, 'numero', (e.target as HTMLInputElement).value)" />
            </td>
            <td v-if="mostraColuna('valor')">
              <input type="number" step="0.01" class="cell-input text-right"
                :value="row.valor ?? ''" :disabled="!canEdit || busy || rowBusy[row.id]"
                @input="(e) => scheduleSave(row, 'valor', (e.target as HTMLInputElement).value === '' ? null : Number((e.target as HTMLInputElement).value))" />
            </td>
            <td>
              <input type="date" class="cell-input" :value="row.inicio ?? ''" :disabled="!canEdit || busy || rowBusy[row.id]"
                :title="fieldSourceTitle(row, 'inicio')"
                @input="(e) => scheduleSave(row, 'inicio', (e.target as HTMLInputElement).value || null)" />
            </td>
            <td>
              <input type="date" class="cell-input" :value="row.fim ?? ''" :disabled="!canEdit || busy || rowBusy[row.id]"
                :title="fieldSourceTitle(row, 'fim')"
                @input="(e) => scheduleSave(row, 'fim', (e.target as HTMLInputElement).value || null)" />
            </td>
            <td>
              <input type="text" inputmode="numeric" placeholder="—" class="cell-input text-right"
                :value="row.garantia_meses ?? ''" :disabled="!canEdit || busy || rowBusy[row.id]"
                title="Vale para todos os produtos deste certificado na Tabela de Preços (0 = sem garantia; vazio = não define)."
                data-cert-garantia
                @change="(e) => salvarGarantia(row, (e.target as HTMLInputElement).value)" />
            </td>
            <td v-if="mostraColuna('situacao')" class="official-status-cell">
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
            <h2 id="certification-details-title" class="text-lg font-semibold">Dados da fonte original · {{ officialSource(selectedRow) }}</h2>
            <p class="text-sm text-muted-foreground">{{ officialData(selectedRow)?.produto || 'Certificação' }} · {{ officialData(selectedRow)?.numero || selectedRow.anatel_numero || 'Número não informado na fonte' }}</p>
          </div>
          <button class="rounded p-1 hover:bg-muted" aria-label="Fechar dados oficiais" @click="detailsDialog?.close()"><X class="size-4" /></button>
        </div>
        <p v-if="hasManualOfficialFields(selectedRow)" class="text-sm text-muted-foreground">O cadastro contém alterações manuais. A situação abaixo se refere aos dados originais da fonte.</p>
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
