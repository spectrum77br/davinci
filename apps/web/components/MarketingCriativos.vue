<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import {
  Check, X, Trash2, Upload, Loader2, Plus, Download, CloudUpload, Search,
  Film, Image as ImageIcon, File as FileIcon,
  Send, CalendarClock, ExternalLink, AlertTriangle,
  MessageSquare, NotebookPen,
} from 'lucide-vue-next'
import { apiErrMsg, MARCAS_ERROS } from '~/lib/apiError'
import { isoToday } from '~/lib/date'
import { PLATAFORMA_LABELS, type Plataforma } from '~/lib/redesSociais'

type CreativeFile = {
  id: string
  file_name: string
  file_mime: string | null
  file_size: number | null
}

type Creative = {
  id: string
  modelo: string
  marca: string | null
  // migration 0279: a coluna `marca` (texto) continua, mas quem diz QUAIS
  // contas de rede social o robô pode usar é o `marca_id`. Opcional aqui
  // porque a listagem antiga (sem o campo) tem que continuar abrindo.
  marca_id?: string | null
  sku: string | null
  equipe: string | null
  files: CreativeFile[]
  // O briefing virou entidade própria (migration 0299) e mora na aba
  // Roteiros. Aqui fica só o ponteiro — `roteiro_titulo` vem junto pra
  // célula ter o que mostrar sem uma segunda chamada. Opcionais porque uma
  // listagem de backend antigo tem que continuar abrindo a tela.
  roteiro_id?: string | null
  roteiro_titulo?: string | null
  aprovado: boolean | null
  // Recado da recusa/aprovação — o mesmo texto que a agência lê no portal.
  // Fica na ENTREGA ("por que este vídeo voltou"), não no briefing.
  feedback?: string | null
  feedback_em?: string | null
  pushed_at: string | null
  pushed_dest: string | null
  created_at: string | null
}

type Field = 'modelo' | 'marca' | 'sku' | 'equipe'

// A célula do Roteiro leva pra aba Roteiros — quem troca de aba é a página
// (`pages/marketing.vue`), dona do estado `platform`.
const emit = defineEmits<{ (e: 'abrir-roteiro', id: string): void }>()

const { api } = useApi()
const toasts = useToasts()
const auth = useAuthStore()
const isAdmin = computed(() => auth.user?.role === 'admin')
const canEdit = useCan('marketing_criativos', 'edit')

// Opções fixas — modelo e marca são escolhidos, não digitados.
const MODELO_OPTIONS = ['imagem', 'carrossel', 'video 15s', 'video 30s', 'video 60s']
// Dica mostrada ao passar o mouse por cima (atributo title do navegador).
const MODELO_HINTS: Record<string, string> = { carrossel: 'carrossel com 6 imagens' }

// Marcas de Cadastros › Marcas. Antes era a lista fixa
// `['uranyx', 'charlots']` aqui dentro; com o robô de postagem (Eduardo,
// 15/09/2026) a marca deixou de ser enfeite — é ela que decide as contas de
// rede social oferecidas no modal —, então vem do cadastro.
type Marca = { id: string; nome: string; slug: string; ativo: boolean }

// Se /api/marcas falhar (ou o usuário não tiver `marcas:view`), o select não
// pode ficar vazio e travar a edição das linhas que já existem.
const MARCA_FALLBACK = ['uranyx', 'charlots']

const marcas = ref<Marca[]>([])

// O valor gravado na coluna sempre foi o SLUG ('uranyx') — continua sendo,
// pra não reescrever o histórico das linhas antigas.
const marcaOptions = computed<{ value: string; label: string }[]>(() =>
  marcas.value.length
    ? marcas.value.map((m) => ({ value: m.slug, label: m.nome || m.slug }))
    : MARCA_FALLBACK.map((v) => ({ value: v, label: v })),
)
const marcaValues = computed(() => marcaOptions.value.map((o) => o.value))

async function loadMarcas() {
  try {
    marcas.value = await api<Marca[]>('/api/marcas?ativo=true')
  } catch {
    marcas.value = []
  }
}

// Equipes de marketing conhecidas (cadastradas nos usuários) — opções do
// seletor da coluna Equipe.
const equipeOptions = ref<string[]>([])
async function loadEquipes() {
  try {
    equipeOptions.value = await api<string[]>('/api/marketing/creatives/equipes')
  } catch {
    equipeOptions.value = []
  }
}

const rows = ref<Creative[]>([])
const loading = ref(false)
const uploadingId = ref<string | null>(null)
const approvingId = ref<string | null>(null)

const ERR_PT: Record<string, string> = {
  ja_enviado_pro_mega: 'Essa linha já foi pro MEGA — não dá mais pra mexer nos arquivos.',
  sem_arquivo: 'Falta anexar pelo menos um arquivo antes de aprovar.',
  sem_sku: 'Preencha o SKU antes de aprovar.',
  produto_nao_encontrado: 'Nenhum produto na tabela de preços tem esse SKU.',
  produto_sem_pasta: 'O produto desse SKU ainda não tem pasta no MEGA.',
  modelo_obrigatorio: 'O campo modelo é obrigatório.',
  arquivo_sumiu: 'O arquivo não foi encontrado no servidor — anexe de novo.',
  fora_da_sua_equipe: 'Essa linha é de outra equipe de marketing.',
  muitos_arquivos: 'Limite de 20 arquivos por linha.',
  nome_invalido: 'Nome de arquivo inválido.',
  forbidden: 'Você não tem permissão pra isso.',
  roteiro_nao_encontrado: 'Esse roteiro não existe mais.',
  titulo_obrigatorio: 'O roteiro precisa de um título.',
  roteiro_mudou_de_lugar: 'O roteiro agora é escrito na aba Roteiros.',
}

function errMsg(e: any): string {
  const code = e?.data?.detail?.code
  if (code && ERR_PT[code]) return ERR_PT[code]
  if (code === 'mega_error') {
    return `Erro no MEGA: ${e?.data?.detail?.message ?? 'falha no envio'}`
  }
  return e?.data?.detail?.message ?? e?.message ?? 'Erro inesperado'
}

async function load() {
  loading.value = true
  try {
    rows.value = await api<Creative[]>('/api/marketing/creatives')
  } catch (e: any) {
    toasts.error('Erro ao carregar criativos', errMsg(e))
  } finally {
    loading.value = false
  }
}
onMounted(() => { void load(); void loadEquipes(); void loadMarcas() })

// ---- filtro ---------------------------------------------------------------
const q = ref('')
const statusFilter = ref<'todos' | 'pendente' | 'aprovado' | 'reprovado'>('todos')

const filteredRows = computed(() => {
  const term = q.value.trim().toLowerCase()
  return rows.value.filter((r) => {
    if (statusFilter.value === 'pendente' && r.aprovado !== null) return false
    if (statusFilter.value === 'aprovado' && r.aprovado !== true) return false
    if (statusFilter.value === 'reprovado' && r.aprovado !== false) return false
    if (!term) return true
    const hay = [r.modelo, r.marca, r.sku, r.equipe, r.roteiro_titulo, ...r.files.map((f) => f.file_name)]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()
    return hay.includes(term)
  })
})

// ---- edição estilo planilha (clica na célula → edita; Enter/blur salva,
// ---- Esc cancela; flash verde ao salvar — mesmo padrão da Tabela de Preços)
const editing = ref<{ id: string; field: Field } | null>(null)
const editValue = ref('')
const flashed = ref<{ id: string; field: Field } | null>(null)

function isEditing(id: string, field: Field) {
  return editing.value?.id === id && editing.value?.field === field
}
function isFlashed(id: string, field: Field) {
  return flashed.value?.id === id && flashed.value?.field === field
}

function setEditInputRef(el: any) {
  if (el && typeof el.focus === 'function') {
    el.focus()
    if (el.tagName === 'INPUT' && typeof el.select === 'function') el.select()
  }
}

function startEdit(r: Creative, field: Field) {
  if (!canEdit.value) return
  editing.value = { id: r.id, field }
  editValue.value = (r[field] ?? '') as string
  void nextTick()
}

function cancelEdit() {
  editing.value = null
}

async function commitEdit() {
  const cur = editing.value
  if (!cur) return
  const r = rows.value.find((x) => x.id === cur.id)
  editing.value = null
  if (!r) return
  const before = (r[cur.field] ?? '') as string
  if (editValue.value === before) return
  try {
    const updated = await api<Creative>(`/api/marketing/creatives/${r.id}`, {
      method: 'PATCH',
      body: { [cur.field]: editValue.value },
    })
    Object.assign(r, updated)
    flashed.value = { id: r.id, field: cur.field }
    setTimeout(() => {
      if (flashed.value?.id === r.id && flashed.value?.field === cur.field) {
        flashed.value = null
      }
    }, 1200)
  } catch (e: any) {
    toasts.error('Erro ao salvar', errMsg(e))
  }
}

// ---- adicionar linha ------------------------------------------------------
const newRow = reactive({ modelo: '', marca: '', sku: '', equipe: '' })
const adding = ref(false)

async function addRow() {
  if (!newRow.modelo.trim()) {
    toasts.warning('Escolha o modelo (imagem, video 15s, video 30s ou video 60s)')
    return
  }
  adding.value = true
  try {
    const created = await api<Creative>('/api/marketing/creatives', {
      method: 'POST',
      body: { modelo: newRow.modelo, marca: newRow.marca || null, sku: newRow.sku || null, equipe: newRow.equipe || null },
    })
    rows.value = [...rows.value, created]
    newRow.modelo = ''
    newRow.marca = ''
    newRow.sku = ''
    newRow.equipe = ''
  } catch (e: any) {
    toasts.error('Erro ao adicionar', errMsg(e))
  } finally {
    adding.value = false
  }
}

// ---- arquivos (vários por linha) ------------------------------------------
const fileInput = ref<HTMLInputElement | null>(null)
const fileTarget = ref<Creative | null>(null)

function pickFile(r: Creative) {
  if (r.pushed_at) {
    toasts.warning(ERR_PT.ja_enviado_pro_mega)
    return
  }
  // Arquivo novo zera `aprovado` (routers/marketing_creatives.py) e o robô só
  // publica criativo aprovado — quem já tem post na agenda precisa saber
  // disso ANTES de subir, senão a postagem fica esperando aprovação calada.
  if (temPostagemEmVoo(r) && !window.confirm(
    'Essa linha tem postagem agendada. Subir arquivo novo volta a linha pra pendente '
    + 'de aprovação, e o robô não publica sem aprovação. Continuar?',
  )) return
  fileTarget.value = r
  fileInput.value?.click()
}

async function onFilePicked(ev: Event) {
  const input = ev.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  const target = fileTarget.value
  input.value = ''
  if (!files.length || !target) return
  uploadingId.value = target.id
  const tinhaAgendada = temPostagemEmVoo(target)
  try {
    const fd = new FormData()
    for (const f of files) fd.append('files', f)
    const updated = await api<Creative>(
      `/api/marketing/creatives/${target.id}/arquivo`,
      { method: 'POST', body: fd },
    )
    Object.assign(target, updated)
    toasts.success(
      files.length === 1 ? 'Arquivo anexado' : `${files.length} arquivos anexados`,
      files.map((f) => f.name).join(', '),
    )
    if (tinhaAgendada) {
      toasts.warning(
        'Aprovação zerada — a postagem agendada não sai assim',
        'Aprove a linha de novo pro robô publicar, ou cancele o agendamento na coluna Publicação.',
      )
      await loadPostagens()
    }
  } catch (e: any) {
    toasts.error('Erro no upload', errMsg(e))
  } finally {
    uploadingId.value = null
  }
}

async function removeFile(r: Creative, f: CreativeFile) {
  // Apagar o arquivo de uma postagem em voo deixa o robô sem o que publicar
  // (o backend responde `arquivo_sumiu`) — avisa antes de deixar apagar.
  const presas = postagensDe(r).filter((p) => p.file_id === f.id && STATUS_EM_VOO.includes(p.status))
  if (presas.length && !window.confirm(
    `Esse arquivo tem ${presas.length} postagem(ns) esperando pra sair. `
    + 'Apagar agora faz o robô falhar por arquivo sumido. Apagar mesmo assim?',
  )) return
  if (!window.confirm(`Apagar o arquivo "${f.file_name}"?`)) return
  try {
    const updated = await api<Creative>(
      `/api/marketing/creatives/${r.id}/arquivo/${f.id}`,
      { method: 'DELETE' },
    )
    Object.assign(r, updated)
    toasts.info('Arquivo apagado', f.file_name)
  } catch (e: any) {
    toasts.error('Erro ao apagar arquivo', errMsg(e))
  }
}

function fmtSize(n: number | null): string {
  if (!n) return ''
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

// ---- preview (imagem/vídeo no modal, com botão de baixar) ------------------
const preview = ref<{ row: Creative; file: CreativeFile } | null>(null)

function fileUrl(r: Creative, f: CreativeFile, download = false) {
  return `/api/marketing/creatives/${r.id}/arquivo/${f.id}${download ? '?download=1' : ''}`
}

const previewIsImage = computed(() => preview.value?.file.file_mime?.startsWith('image/') ?? false)
const previewIsVideo = computed(() => preview.value?.file.file_mime?.startsWith('video/') ?? false)

function openPreview(r: Creative, f: CreativeFile) {
  preview.value = { row: r, file: f }
}
function closePreview() {
  preview.value = null
}

function onKeydown(e: KeyboardEvent) {
  // Preview primeiro: ele abre POR CIMA do modal de publicação (o operador
  // confere o vídeo antes de agendar), então Esc fecha o de cima.
  if (e.key !== 'Escape') return
  if (preview.value) closePreview()
  else if (recusa.value) closeRecusa()
  else if (pub.value) closePublicar()
}
onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))

// ---- roteiro da linha -----------------------------------------------------
//
// O briefing saiu daqui (migration 0299) e virou a aba Roteiros. O que ficou
// nesta tela é o ponteiro — MAIS o atalho de criar: o pedido era desacoplar o
// armazenamento, não tirar a porta de entrada. Sem este botão, escrever
// roteiro pra um item passaria de um clique para "troca de aba, cria, redigita
// marca e SKU, volta, vincula".

const criandoRoteiro = ref<string | null>(null)

async function criarRoteiroDaLinha(r: Creative) {
  criandoRoteiro.value = r.id
  try {
    // Nasce já com o que a linha sabe: título, marca e SKU. O backend
    // resolve marca_id/product_id a partir desses textos, igual ao criativo.
    const rot = await api<{ id: string }>('/api/marketing/roteiros', {
      method: 'POST',
      body: { titulo: r.modelo, marca: r.marca, sku: r.sku },
    })
    Object.assign(r, await api<Creative>(`/api/marketing/creatives/${r.id}`, {
      method: 'PATCH',
      body: { roteiro_id: rot.id },
    }))
    emit('abrir-roteiro', rot.id)
  } catch (e: any) {
    toasts.error('Erro ao criar o roteiro', errMsg(e))
  } finally {
    criandoRoteiro.value = null
  }
}

// ---- recusa com recado ----------------------------------------------------
//
// O X sozinho deixava a agência no escuro: ela vê "recusado" no portal e
// regrava adivinhando. Aqui o motivo entra junto da decisão, num POST só —
// não dá pra reprovar e "escrever depois" e esquecer.

const recusa = ref<Creative | null>(null)
const recusaTexto = ref('')

function openRecusa(r: Creative) {
  recusa.value = r
  recusaTexto.value = r.feedback ?? ''
}
function closeRecusa() {
  recusa.value = null
}
async function confirmarRecusa() {
  const r = recusa.value
  if (!r) return
  closeRecusa()
  await aprovar(r, false, recusaTexto.value)
}

// ---- aprovação (admin) ----------------------------------------------------
async function aprovar(r: Creative, ok: boolean, feedback?: string) {
  approvingId.value = r.id
  const t = ok && !r.pushed_at
    ? toasts.push({ kind: 'info', title: 'Aprovando…', lines: 'Enviando os arquivos pra pasta do produto no MEGA.' }, 0)
    : null
  try {
    const updated = await api<Creative & { enviados?: number; fotos_count?: number | null }>(
      `/api/marketing/creatives/${r.id}/aprovar`,
      // `feedback` ausente mantém o recado anterior no backend; string vazia
      // apaga. Por isso só entra no corpo quando o operador abriu a caixa.
      { method: 'POST', body: feedback === undefined ? { aprovado: ok } : { aprovado: ok, feedback } },
    )
    Object.assign(r, updated)
    if (ok && updated.pushed_dest) {
      const n = updated.enviados ?? r.files.length
      toasts.success(
        n === 1 ? 'Aprovado — arquivo no MEGA' : `Aprovado — ${n} arquivos no MEGA`,
        `Pasta: ${updated.pushed_dest}`,
      )
    } else if (!ok) {
      toasts.info(
        'Marcado como não aprovado',
        updated.feedback ? 'O recado já aparece no portal do time de criação.' : undefined,
      )
    }
  } catch (e: any) {
    toasts.error('Erro na aprovação', errMsg(e))
  } finally {
    if (t !== null) toasts.dismiss(t)
    approvingId.value = null
  }
}

// ---- excluir --------------------------------------------------------------
async function remove(r: Creative) {
  if (!window.confirm(`Excluir a linha "${r.modelo}"?`)) return
  try {
    await api(`/api/marketing/creatives/${r.id}`, { method: 'DELETE' })
    rows.value = rows.value.filter((x) => x.id !== r.id)
  } catch (e: any) {
    toasts.error('Erro ao excluir', errMsg(e))
  }
}

// ---- robô de postagem (Marketing › Criativos × Cadastros › Redes Sociais) --
//
// Spec de 15/09/2026: o criativo APROVADO vira post nas contas da marca. Quem
// publica é o SERVIDOR (Graph API da Meta, worker + modo seco) — esta tela só
// agenda e acompanha. Por isso a coluna Publicação mostra uma pill por conta
// e o botão abre um modal em vez de disparar qualquer coisa direto.

// Espelha PostagemOut de app/routers/marketing_postagens.py.
type Postagem = {
  id: string
  creative_id: string
  file_id: string | null
  rede_social_id: string | null
  plataforma: string
  conta: string | null
  status: string
  agendado_para: string | null
  publicado_em: string | null
  post_url: string | null
  result: string | null
  // O vídeo chegou a subir pra Meta — existe algo lá pra conferir antes de
  // tentar de novo. É booleano de propósito: o id do container é da Meta.
  tem_container?: boolean
  created_at: string | null
  // Só da tela: quantas tentativas nesta conta falharam ANTES deste sucesso.
  // Não vem da API — é montado por escondeFalhasSuperadas pro tooltip.
  falhas_antes?: number
}

// Uma conta da marca no modal. `pode_postar=false` vem com `motivo` — o
// backend manda o CÓDIGO (conta_sem_token…), traduzido aqui embaixo.
type ContaPostagem = {
  id: string
  plataforma: string
  conta: string | null
  pode_postar: boolean
  motivo: string | null
}

// ---------- helpers puros (sem estado — travados em tests/marketing-criativos-sfc.cjs)

// Limite de legenda do Instagram (2200). O Facebook aceita mais, mas o menor
// dos dois é o que vale pra um texto que vai pros dois.
const LEGENDA_MAX = 2200

// De onde saiu o texto que está no textarea (cascata do backend:
// postagem → criativo → produto → marca → nada). Traduzido aqui porque o
// endpoint manda o código; origem que a tela não conhecer passa crua, igual
// ao statusLabel — backend novo não pode virar rótulo mentiroso.
const LEGENDA_ORIGEM_LABEL: Record<string, string> = {
  manual: 'escrita agora',
  criativo: 'legenda deste vídeo',
  produto: 'legenda do produto',
  marca: 'padrão da marca',
  nenhuma: 'sem legenda',
}

// Resposta de GET /api/marketing/legendas/resolvida, já limpa.
type LegendaResolvida = { texto: string; origem: string; total: number; indice: number }

// O backend pode mandar lixo (ou nada) sem derrubar o modal. Duas decisões
// moram aqui: o texto é cortado no limite do Instagram — não adianta mostrar o
// que não caberia no post — e texto VAZIO é sempre `nenhuma`, venha a origem
// que vier, porque é o texto que vai (ou não vai) pro Instagram e é ele que o
// aviso âmbar descreve.
function normalizaLegenda(resp: any): LegendaResolvida {
  const texto = typeof resp?.texto === 'string' ? resp.texto.slice(0, LEGENDA_MAX) : ''
  const total = Number.isFinite(resp?.total_variacoes) ? Math.trunc(resp.total_variacoes) : 0
  const bruto = Number.isFinite(resp?.indice) ? Math.trunc(resp.indice) : 0
  // `indice` é 1-based (variação 2 de 4); fora da faixa vira 0 = não mostra.
  const indice = bruto >= 1 && bruto <= total ? bruto : 0
  const origem = texto.trim() ? String(resp?.origem ?? '').trim() : 'nenhuma'
  return { texto, origem, total: total > 0 ? total : 0, indice }
}

// Rótulo discreto acima do textarea: "padrão da marca · variação 2 de 4 ·
// 412/2200". É o único contador da caixa — o operador precisa saber de onde
// veio aquele texto ANTES de decidir editar.
function legendaRotulo(o: { origem: string; total: number; indice: number; tamanho: number }): string {
  const partes: string[] = []
  const org = (o.origem || '').trim()
  if (org) partes.push(LEGENDA_ORIGEM_LABEL[org] || org)
  // "variação 1 de 1" é ruído: o rodízio só interessa quando há mais de um
  // texto disputando a vez naquela conta.
  if (o.total > 1 && o.indice >= 1) partes.push(`variação ${o.indice} de ${o.total}`)
  partes.push(`${o.tamanho}/${LEGENDA_MAX}`)
  return partes.join(' · ')
}

// Espelha STATUS_EM_VOO de app/models/marketing_postagem.py: enquanto está
// num desses, a postagem ainda vai acontecer.
const STATUS_EM_VOO = ['agendado', 'pendente', 'containering', 'publicando']

// Cancelar só faz sentido antes de a Meta receber qualquer coisa:
// `containering`/`publicando` já estão lá e publicar não tem desfazer.
const STATUS_CANCELAVEIS = ['agendado', 'pendente', 'revisar']

const STATUS_LABEL: Record<string, string> = {
  agendado: 'agendado',
  pendente: 'na fila',
  containering: 'processando',
  publicando: 'publicando',
  publicado: 'publicado',
  falhou: 'falhou',
  cancelado: 'cancelado',
  revisar: 'revisar',
}

const STATUS_PILL: Record<string, string> = {
  agendado: 'pill-warning',
  pendente: 'pill-warning',
  containering: 'pill-warning',
  publicando: 'pill-warning',
  publicado: 'pill-success',
  falhou: 'pill-danger',
  cancelado: 'pill-muted',
  // `revisar` = o horário passou faz tempo e ninguém publicou (worker parado).
  // Não sai sozinho desse estado, então pede atenção como uma falha.
  revisar: 'pill-danger',
}

function statusLabel(st: string): string {
  return STATUS_LABEL[st] || st
}
function statusPill(st: string): string {
  return STATUS_PILL[st] || 'pill-muted'
}
function emVoo(st: string): boolean {
  return STATUS_EM_VOO.includes(st)
}
function podeCancelar(p: Postagem): boolean {
  return STATUS_CANCELAVEIS.includes(p.status)
}

// BRT é UTC-3 FIXO (o Brasil não tem horário de verão desde 2019), então dá
// pra deslocar o instante e ler os campos UTC — sem depender do fuso da
// máquina de quem abriu a tela. É o mesmo motivo de ~/lib/date existir.
const BRT_OFFSET_MIN = -180
const BRT_OFFSET = '-03:00'

function brtPartes(iso: string | null | undefined): { data: string; hora: string } | null {
  if (!iso) return null
  const t = new Date(iso)
  if (Number.isNaN(t.getTime())) return null
  const s = new Date(t.getTime() + BRT_OFFSET_MIN * 60_000).toISOString()
  return { data: s.slice(0, 10), hora: s.slice(11, 16) }
}

// Pill: "14:30" quando é hoje, "16/09 14:30" quando não é — mesma ideia do
// fmtDate/daysUntil de pages/faturas.vue (data curta, que cabe na célula).
// `hojeBrt` sai de isoToday() (~/lib/date), nunca de toISOString() cru.
function fmtBrtCurto(iso: string | null | undefined, hojeBrt: string = isoToday()): string {
  const p = brtPartes(iso)
  if (!p) return ''
  if (p.data === hojeBrt) return p.hora
  return `${p.data.slice(8, 10)}/${p.data.slice(5, 7)} ${p.hora}`
}

// Completa, pro title: "16/09/2026 14:30".
function fmtBrtLongo(iso: string | null | undefined): string {
  const p = brtPartes(iso)
  if (!p) return ''
  return `${p.data.slice(8, 10)}/${p.data.slice(5, 7)}/${p.data.slice(0, 4)} ${p.hora}`
}

// Valor inicial do <input type="datetime-local">: próxima hora cheia, sempre
// pelo menos 1h à frente. O agendamento nativo do Facebook exige 10 min de
// antecedência — "daqui a dois minutos" seria recusado pela Meta.
function proximaHoraBrt(agora: Date = new Date()): string {
  const h = 3_600_000
  const cheia = Math.ceil((agora.getTime() + h) / h) * h
  return new Date(cheia + BRT_OFFSET_MIN * 60_000).toISOString().slice(0, 16)
}

// 'YYYY-MM-DDTHH:mm' (o que o datetime-local devolve, no relógio de quem
// digitou) → ISO com offset EXPLÍCITO. Sem offset o backend teria que
// adivinhar o fuso; com o offset da MÁQUINA, um notebook viajando agendaria
// na hora errada. O operador sempre pensa em BRT, então é BRT que vai.
function brtParaIso(local: string | null | undefined): string | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::\d{2})?$/.exec((local || '').trim())
  if (!m) return null
  const [, ano, mes, dia, hora, min] = m
  if (+mes < 1 || +mes > 12 || +dia < 1 || +dia > 31 || +hora > 23 || +min > 59) return null
  return `${ano}-${mes}-${dia}T${hora}:${min}:00${BRT_OFFSET}`
}

// O backend grava a conta sem "@" (schemas/marcas._handle); aqui a gente põe
// de volta pro operador reconhecer o perfil.
function contaLabel(x: { conta: string | null }): string {
  const c = (x.conta || '').trim().replace(/^@+/, '')
  return c ? `@${c}` : '(conta removida)'
}

function plataformaLabel(p: string): string {
  return PLATAFORMA_LABELS[p as Plataforma] || p
}

// Motivo de uma conta não poder receber post. O backend manda código; se um
// dia mandar frase pronta, ela passa direto em vez de virar "indisponível".
const CONTA_MOTIVO_PT: Record<string, string> = {
  conta_sem_token: 'conta não conectada — conecte em Cadastros › Redes Sociais',
  sem_token: 'conta não conectada — conecte em Cadastros › Redes Sociais',
  token_expirado: 'acesso expirado — reconecte a conta',
  token_revogado: 'acesso revogado — reconecte a conta',
  conta_inativa: 'conta inativa no cadastro',
  inativa: 'conta inativa no cadastro',
  plataforma_nao_suportada: 'o robô ainda não publica nessa rede',
  sem_conta: 'linha sem @ cadastrado',
  limite_diario: 'limite de posts do dia já batido',
  intervalo_curto: 'postou faz pouco — espere o intervalo',
}

function motivoConta(motivo: string | null | undefined): string {
  const m = (motivo || '').trim()
  if (!m) return 'indisponível'
  return CONTA_MOTIVO_PT[m] || m
}

// Por que o botão "publicar" está desabilitado (null = pode publicar). A
// trava de verdade é do backend; aqui é só pra não deixar o operador clicar
// num caminho que já se sabe que vai dar 422.
function motivoPublicar(r: Creative, podeEditar: boolean): string | null {
  if (!podeEditar) return 'Você não tem permissão de edição em Criativos.'
  if (!r.files.length) return 'Anexe o arquivo (vídeo ou imagem) antes de publicar.'
  if (r.aprovado !== true) return 'Só criativo aprovado vai pro ar — aprove a linha primeiro.'
  return null
}

// Data que importa em cada estado: publicado mostra quando saiu; o resto,
// quando vai (ou ia) sair.
function postagemQuando(p: Postagem, hojeBrt: string = isoToday()): string {
  const iso = p.status === 'publicado'
    ? (p.publicado_em || p.agendado_para)
    : (p.agendado_para || p.publicado_em || p.created_at)
  return fmtBrtCurto(iso, hojeBrt)
}

// Tooltip da pill: conta, estado, horário completo e — quando falhou — o erro
// que o worker gravou em `result`.
function postagemTitle(p: Postagem): string {
  const partes: string[] = [`${plataformaLabel(p.plataforma)} ${contaLabel(p)}`, statusLabel(p.status)]
  if (p.status === 'publicado' && p.publicado_em) {
    partes.push(`publicado em ${fmtBrtLongo(p.publicado_em)}`)
  } else if (p.agendado_para) {
    partes.push(`${emVoo(p.status) ? 'agendado para' : 'era para'} ${fmtBrtLongo(p.agendado_para)} (BRT)`)
  } else if (emVoo(p.status)) {
    partes.push('sai no próximo ciclo do robô')
  }
  if (p.status === 'revisar') {
    // Duas histórias bem diferentes moram no mesmo estado, e confundi-las
    // custa um Reel repetido no perfil da marca.
    partes.push(p.tem_container
      ? 'o vídeo subiu e a Meta não confirmou — CONFIRA A CONTA antes de tentar de novo'
      : 'o horário passou faz tempo e não publicou — decida se ainda vale')
  }
  if (p.result && p.status !== 'publicado') partes.push(p.result)
  // As tentativas que falharam antes deste sucesso saem da linha (senão viram
  // tarja vermelha permanente ao lado do post que deu certo), mas não podem
  // sumir de vez: quem for entender por que o post demorou precisa saber.
  if (p.falhas_antes) {
    partes.push(p.falhas_antes === 1
      ? '1 tentativa falhou antes desta'
      : `${p.falhas_antes} tentativas falharam antes desta`)
  }
  if (p.post_url) partes.push(p.post_url)
  return partes.join(' · ')
}

// Em voo primeiro (é o que ainda dá pra cancelar), depois o que já foi ao ar,
// e o histórico no fim; dentro do grupo, mais recente primeiro.
function ordemStatus(st: string): number {
  if (emVoo(st)) return 0
  if (st === 'publicado') return 1
  return 2
}

// Conta+plataforma: duas tentativas na MESMA conta são a mesma história.
function chaveConta(p: Postagem): string {
  return `${p.plataforma}|${(p.conta || '').trim().toLowerCase()}`
}

// Esconde da linha as falhas que a conta já superou.
//
// Pedido do Eduardo (23/09/2026): cada tentativa virava uma pill vermelha
// permanente, então um criativo que falhou duas vezes e publicou na terceira
// ficava com duas tarjas de erro pra sempre ao lado do sucesso. O que importa
// na linha é que o vídeo está no ar.
//
// Some da TELA, não do banco: o histórico continua inteiro, e o tooltip do
// post que deu certo diz quantas tentativas vieram antes. E some só o que veio
// ANTES do sucesso — falha posterior é notícia nova e continua aparecendo.
function escondeFalhasSuperadas(list: Postagem[]): Postagem[] {
  const publicou = new Map<string, string>()
  for (const p of list) {
    if (p.status !== 'publicado') continue
    const k = chaveConta(p)
    const quando = p.publicado_em || p.created_at || ''
    if (quando > (publicou.get(k) ?? '')) publicou.set(k, quando)
  }
  if (!publicou.size) return list

  const ocultas = new Map<string, number>()
  const visiveis = list.filter((p) => {
    if (p.status !== 'falhou') return true
    const k = chaveConta(p)
    const sucesso = publicou.get(k)
    if (!sucesso || (p.created_at || '') > sucesso) return true
    ocultas.set(k, (ocultas.get(k) ?? 0) + 1)
    return false
  })
  if (!ocultas.size) return list
  // Cópia: não mexo no objeto que veio da API — a mesma lista alimenta outras
  // contas da tela.
  return visiveis.map((p) =>
    p.status === 'publicado' && ocultas.get(chaveConta(p))
      ? { ...p, falhas_antes: ocultas.get(chaveConta(p)) }
      : p,
  )
}

function ordenaPostagens(list: Postagem[]): Postagem[] {
  return [...list].sort((a, b) => {
    const g = ordemStatus(a.status) - ordemStatus(b.status)
    if (g !== 0) return g
    const ka = a.publicado_em || a.agendado_para || a.created_at || ''
    const kb = b.publicado_em || b.agendado_para || b.created_at || ''
    return kb.localeCompare(ka)
  })
}

// Corpo do POST /api/marketing/postagens.
function montaBody(o: {
  creativeId: string
  fileId: string
  redeSocialIds: string[]
  legenda: string
  legendaOrigem: string
  quando: 'agora' | 'agendar'
  dataHora: string
  shareToFeed: boolean
  temInstagram: boolean
}): Record<string, any> {
  // A legenda SÓ viaja quando o operador reescreveu o texto. O que a tela
  // mostra é a legenda resolvida para UMA conta — mandá-la de volta faria o
  // @ daquela conta ir pras outras (`{{ instagram }}`) e congelaria o rodízio
  // em uma variação só. Vindo vazia, o backend resolve por conta, dentro do
  // laço do `agendar()`.
  const legenda = o.legendaOrigem === 'manual' ? (o.legenda || '').trim() : ''
  // share_to_feed só existe no Instagram (o Reel aparecer também no feed);
  // mandar pro Facebook seria opção morta viajando no payload.
  const opcoes: Record<string, any> = o.temInstagram ? { share_to_feed: o.shareToFeed } : {}
  return {
    creative_id: o.creativeId,
    file_id: o.fileId,
    rede_social_ids: [...o.redeSocialIds],
    legenda: legenda || null,
    // null = "publica no próximo tick" (app/models/marketing_postagem.py).
    agendado_para: o.quando === 'agendar' ? brtParaIso(o.dataHora) : null,
    opcoes,
  }
}

// GET /contas pode vir como lista pura ou como envelope {commit, contas}.
// O modo seco (settings.marketing_postagem_commit) é informação do SERVIDOR:
// `commit: null` = não veio, e aí a tela avisa baixinho em vez de prometer
// que o post vai ao ar.
function normalizaContas(resp: any): { contas: ContaPostagem[]; commit: boolean | null } {
  const lista: any[] = Array.isArray(resp) ? resp : Array.isArray(resp?.contas) ? resp.contas : []
  const commit = typeof resp?.commit === 'boolean'
    ? resp.commit
    : typeof resp?.modo_seco === 'boolean'
      ? !resp.modo_seco
      : null
  const contas = lista
    .map((c: any): ContaPostagem => ({
      id: String(c?.id ?? c?.rede_social_id ?? ''),
      plataforma: String(c?.plataforma ?? ''),
      conta: c?.conta ?? null,
      pode_postar: c?.pode_postar === true,
      motivo: c?.motivo ?? null,
    }))
    .filter((c) => !!c.id)
  return { contas, commit }
}

// Códigos novos do robô de postagem, traduzidos AQUI (lib/apiError.ts é de
// outro dono — ver notes do PR pra subir esses códigos pro MARCAS_ERROS).
const POSTAGEM_ERROS: Record<string, string> = {
  criativo_nao_aprovado: 'Esse criativo não está aprovado — aprove a linha antes de publicar.',
  postagem_em_voo: 'Já existe uma postagem desse arquivo nessa conta esperando pra sair.',
  video_ja_usado_em_outra_marca: 'Esse mesmo vídeo já foi usado por outra marca — escolha outro arquivo.',
  conta_sem_token: 'A conta não está conectada — conecte em Cadastros › Redes Sociais.',
  conta_inativa: 'Essa conta está marcada como inativa no cadastro.',
  plataforma_nao_suportada: 'O robô ainda não publica nessa rede.',
  limite_diario: 'Essa conta já bateu o limite de postagens do dia.',
  intervalo_curto: 'Muito perto da postagem anterior dessa conta — espace mais.',
  arquivo_sumiu: 'O arquivo não foi encontrado no servidor — anexe de novo.',
  conta_sem_postagem_auto: 'Essa conta está com a publicação automática desligada — ligue em Cadastros › Redes Sociais ou publique agora.',
  postagem_em_revisao: 'A tentativa anterior desse vídeo nessa conta ficou sem resposta da Meta — confira a conta e resolva aquela linha antes de agendar outra.',
  postagem_precisa_conferir_na_meta: 'Essa postagem chegou a subir o vídeo pra Meta — confira se o post saiu na conta antes de tentar de novo.',
  criativo_sem_marca: 'Escolha a marca do criativo antes de publicar.',
  conta_de_outra_marca: 'Essa conta é de outra marca — só dá pra publicar nas contas da marca do criativo.',
  agendamento_longe_demais: 'Data muito à frente — confira o ano/mês escolhido.',
  agendamento_no_passado: 'Essa data já passou — escolha um horário à frente.',
}
const POSTAGEM_ERR_MAP: Record<string, string> = { ...MARCAS_ERROS, ...POSTAGEM_ERROS }

// ---------- fim helpers puros

function pubErrMsg(e: any): string {
  return apiErrMsg(e, POSTAGEM_ERR_MAP)
}

const postagens = ref<Postagem[]>([])

// Hook próprio (o arquivo já tem mais de um): mantém a carga da agenda junto
// do resto do robô, sem depender da ordem de declaração lá de cima.
onMounted(() => { void loadPostagens() })

async function loadPostagens() {
  try {
    postagens.value = await api<Postagem[]>('/api/marketing/postagens')
  } catch {
    // Endpoint novo: se o backend ainda não subiu (ou o usuário não enxerga),
    // a tela continua inteira — só a coluna Publicação fica vazia.
    postagens.value = []
  }
}

const postagensPorCriativo = computed(() => {
  const m = new Map<string, Postagem[]>()
  for (const p of postagens.value) {
    const atual = m.get(p.creative_id)
    if (atual) atual.push(p)
    else m.set(p.creative_id, [p])
  }
  for (const [k, v] of m) m.set(k, ordenaPostagens(escondeFalhasSuperadas(v)))
  return m
})

function postagensDe(r: Creative): Postagem[] {
  return postagensPorCriativo.value.get(r.id) ?? []
}
function temPostagemEmVoo(r: Creative): boolean {
  return postagensDe(r).some((p) => emVoo(p.status))
}

// ---- modal Publicar / Agendar ---------------------------------------------
const pub = ref<Creative | null>(null)
const pubFileId = ref('')
const pubMarcaId = ref('')
const pubContas = ref<ContaPostagem[]>([])
const pubContasLoading = ref(false)
const pubSel = ref<string[]>([])
const pubLegenda = ref('')
// De onde veio o texto do textarea. Começa em `nenhuma` (e não em `manual`),
// senão o carregador abaixo se recusaria a preencher o campo do próprio modal
// que acabou de abrir.
const pubLegendaOrigem = ref('nenhuma')
const pubLegendaTotal = ref(0)
const pubLegendaIndice = ref(0)
const pubLegendaLoading = ref(false)
// A consulta da legenda falhou (backend antigo ou sem permissão). Não é erro
// de operação — é aviso pra escrever à mão, então não vai pro `pubErr`.
const pubLegendaErro = ref(false)
const pubQuando = ref<'agora' | 'agendar'>('agora')
const pubDataHora = ref('')
const pubShareToFeed = ref(true)
const pubCommit = ref<boolean | null>(null)
const pubSaving = ref(false)
const pubErr = ref('')

const pubFiles = computed<CreativeFile[]>(() => pub.value?.files ?? [])
const pubMarcaNome = computed(() => {
  const m = marcas.value.find((x) => x.id === pubMarcaId.value)
  return m ? (m.nome || m.slug) : ''
})
const pubContasSel = computed(() => pubContas.value.filter((c) => pubSel.value.includes(c.id)))
const pubTemInstagram = computed(() => pubContasSel.value.some((c) => c.plataforma === 'instagram'))

// Quem manda no aviso é o TEXTO, não a origem: apagar à mão a legenda que veio
// do padrão da marca deixa o post tão mudo quanto não ter achado modelo nenhum.
const pubSemLegenda = computed(() => !pubLegenda.value.trim())
const pubLegendaRotulo = computed(() => legendaRotulo({
  origem: pubLegendaOrigem.value,
  total: pubLegendaTotal.value,
  indice: pubLegendaIndice.value,
  tamanho: pubLegenda.value.length,
}))

// A marca do criativo: `marca_id` quando o backend manda; senão casa o texto
// da coluna (que sempre foi o slug) com o cadastro, pra não obrigar o
// operador a reescolher o que já está escrito na linha.
function marcaDoCriativo(r: Creative): string {
  if (r.marca_id) return r.marca_id
  const alvo = (r.marca || '').trim().toLowerCase()
  if (!alvo) return ''
  const m = marcas.value.find(
    (x) => x.slug.toLowerCase() === alvo || (x.nome || '').toLowerCase() === alvo,
  )
  return m?.id ?? ''
}

function openPublicar(r: Creative) {
  const motivo = motivoPublicar(r, canEdit.value)
  if (motivo) {
    toasts.warning('Ainda não dá pra publicar', motivo)
    return
  }
  pub.value = r
  // Vídeo primeiro: o robô publica Reels; imagem é a exceção.
  const video = r.files.find((f) => f.file_mime?.startsWith('video/'))
  pubFileId.value = (video ?? r.files[0])?.id ?? ''
  // A legenda NÃO sai mais do `roteiro`: roteiro é prompt de geração do vídeo
  // (em inglês, "cena, fala, texto na tela") e os dois únicos preenchidos em
  // produção são assim — não é texto de Instagram. Quem diz a legenda é a
  // cascata do backend (legenda do criativo → produto → padrão da marca),
  // pedida logo abaixo já RENDERIZADA, pra tela e post baterem byte a byte.
  pubLegenda.value = ''
  pubLegendaOrigem.value = 'nenhuma'
  pubLegendaTotal.value = 0
  pubLegendaIndice.value = 0
  pubLegendaErro.value = false
  pubQuando.value = 'agora'
  pubDataHora.value = proximaHoraBrt()
  pubShareToFeed.value = true
  pubCommit.value = null
  pubErr.value = ''
  pubMarcaId.value = marcaDoCriativo(r)
  void loadContas()
}

function closePublicar() {
  pub.value = null
  pubContas.value = []
  pubSel.value = []
  pubLegenda.value = ''
  pubLegendaOrigem.value = 'nenhuma'
  pubLegendaTotal.value = 0
  pubLegendaIndice.value = 0
  pubLegendaLoading.value = false
  pubLegendaErro.value = false
  pubErr.value = ''
}

async function loadContas() {
  pubContas.value = []
  pubSel.value = []
  pubCommit.value = null
  if (!pubMarcaId.value) {
    pubErr.value = 'Escolha a marca pra ver as contas de rede social.'
    // Sem marca ainda pode haver legenda do próprio vídeo ou do produto (quem
    // resolve a cascata é o backend, pelo criativo) — só o @ da conta é que
    // ainda não existe.
    await carregaLegenda()
    return
  }
  pubContasLoading.value = true
  pubErr.value = ''
  try {
    // creative_id + file_id junto de propósito: com os dois o backend responde
    // com o MESMO veredito que o publicador usa (vídeo já usado em outra
    // marca, postagem em voo, teto do dia), então o checkbox desabilitado na
    // tela e a recusa do robô nunca discordam. Por isso trocar o arquivo
    // recarrega esta lista.
    const q = new URLSearchParams({ marca_id: pubMarcaId.value })
    if (pub.value && pubFileId.value) {
      q.set('creative_id', pub.value.id)
      q.set('file_id', pubFileId.value)
    }
    const resp = await api<any>(`/api/marketing/postagens/contas?${q.toString()}`)
    const { contas, commit } = normalizaContas(resp)
    pubContas.value = contas
    pubCommit.value = commit
    // Uma conta liberada só: já marca (o caso comum). Com várias o operador
    // escolhe — publicar em tudo por engano não tem desfazer.
    const liberadas = contas.filter((c) => c.pode_postar)
    const primeira = liberadas[0]
    pubSel.value = liberadas.length === 1 && primeira ? [primeira.id] : []
  } catch (e: any) {
    pubErr.value = pubErrMsg(e)
  } finally {
    pubContasLoading.value = false
  }
  // Depois das contas, nunca antes: o `{{ instagram }}` da legenda é o @ da
  // conta que vai receber o post, então só dá pra renderizar o texto sabendo
  // qual conta ficou marcada. Vale pro arquivo e pra marca também (os dois
  // recarregam esta lista).
  await carregaLegenda()
}

function toggleConta(c: ContaPostagem) {
  if (!c.pode_postar) return
  pubSel.value = pubSel.value.includes(c.id)
    ? pubSel.value.filter((x) => x !== c.id)
    : [...pubSel.value, c.id]
  void carregaLegenda()
}

// Cada consulta leva um número; só a última manda no textarea. Trocar de conta
// depressa dispara duas idas ao servidor e a que volta por último não é
// necessariamente a mais nova — escrever a resposta velha por cima poria no
// campo um texto que não é o da conta marcada, e o que está no campo é o que
// vai pro Instagram.
let legendaSeq = 0

async function carregaLegenda() {
  const r = pub.value
  if (!r) return
  // Texto escrito à mão é do operador: trocar a conta muda o @ do modelo, mas
  // não pode apagar o que ele digitou.
  if (pubLegendaOrigem.value === 'manual') return
  const seq = ++legendaSeq
  pubLegendaLoading.value = true
  pubLegendaErro.value = false
  try {
    const q = new URLSearchParams({ creative_id: r.id })
    if (pubFileId.value) q.set('file_id', pubFileId.value)
    // Uma conta só: o texto mostrado é o da PRIMEIRA marcada (é dela o @ do
    // placeholder). Com várias marcadas o backend resolve de novo pra cada uma
    // ao agendar — inclusive o rodízio, que é por conta.
    const conta = pubSel.value[0]
    if (conta) q.set('rede_social_id', conta)
    const resp = await api<any>(`/api/marketing/legendas/resolvida?${q.toString()}`)
    if (seq !== legendaSeq || pub.value !== r) return
    const l = normalizaLegenda(resp)
    pubLegenda.value = l.texto
    pubLegendaOrigem.value = l.origem
    pubLegendaTotal.value = l.total
    pubLegendaIndice.value = l.indice
  } catch {
    if (seq !== legendaSeq || pub.value !== r) return
    // 404 (backend antigo, sem o endpoint) ou 403 (sem permissão): textarea
    // vazio e a tela inteira. O que NÃO pode voltar a acontecer é cair no
    // roteiro — é prompt de vídeo em inglês, não legenda.
    pubLegenda.value = ''
    pubLegendaOrigem.value = 'nenhuma'
    pubLegendaTotal.value = 0
    pubLegendaIndice.value = 0
    pubLegendaErro.value = true
  } finally {
    if (seq === legendaSeq) pubLegendaLoading.value = false
  }
}

// Sem `v-model` de propósito: a mesma digitação que muda o texto muda a ORIGEM
// pra "manual", e ler o valor do evento não depende da ordem em que os dois
// listeners (o do v-model e este) correriam.
function onLegendaInput(e: Event) {
  const alvo = e.target as HTMLTextAreaElement | null
  pubLegenda.value = alvo && typeof alvo.value === 'string' ? alvo.value : ''
  // Apagar tudo à mão volta a ser "sem legenda" — e volta a pedir confirmação
  // no publicar.
  pubLegendaOrigem.value = pubLegenda.value.trim() ? 'manual' : 'nenhuma'
  // Variação é do modelo que veio do cadastro; texto editado não é mais ela.
  pubLegendaTotal.value = 0
  pubLegendaIndice.value = 0
  pubLegendaErro.value = false
}

async function salvarPostagem() {
  const r = pub.value
  if (!r) return
  pubErr.value = ''
  if (!pubFileId.value) {
    pubErr.value = 'Escolha o arquivo que vai pro ar.'
    return
  }
  if (!pubSel.value.length) {
    pubErr.value = 'Marque pelo menos uma conta.'
    return
  }
  if (pubLegenda.value.length > LEGENDA_MAX) {
    pubErr.value = `A legenda passa de ${LEGENDA_MAX} caracteres.`
    return
  }
  if (pubQuando.value === 'agendar' && !brtParaIso(pubDataHora.value)) {
    pubErr.value = 'Data e hora do agendamento inválidas.'
    return
  }
  // Reel sem legenda é criativo queimado: a legenda é o único texto que a
  // busca do Instagram e o Google leem daquele vídeo — e post não se edita
  // depois. Mesma régua do cancelar: o que não tem desfazer pede confirm.
  if (pubSemLegenda.value && !window.confirm(
    'Esse post vai pro ar SEM legenda nenhuma — e legenda não dá pra acrescentar depois. '
    + 'Publicar assim mesmo?',
  )) return
  pubSaving.value = true
  try {
    await api('/api/marketing/postagens', {
      method: 'POST',
      body: montaBody({
        creativeId: r.id,
        fileId: pubFileId.value,
        redeSocialIds: pubSel.value,
        legenda: pubLegenda.value,
        legendaOrigem: pubLegendaOrigem.value,
        quando: pubQuando.value,
        dataHora: pubDataHora.value,
        shareToFeed: pubShareToFeed.value,
        temInstagram: pubTemInstagram.value,
      }),
    })
    const n = pubSel.value.length
    const agora = pubQuando.value === 'agora'
    toasts.success(
      agora
        ? (n === 1 ? 'Postagem na fila' : `${n} postagens na fila`)
        : (n === 1 ? 'Postagem agendada' : `${n} postagens agendadas`),
      agora
        ? 'O robô publica no próximo ciclo.'
        : `Para ${pubDataHora.value.replace('T', ' ')} (BRT).`,
    )
    closePublicar()
    await loadPostagens()
  } catch (e: any) {
    pubErr.value = pubErrMsg(e)
  } finally {
    pubSaving.value = false
  }
}

async function cancelarPostagem(p: Postagem) {
  // Cancelar libera a conta pra um novo agendamento do mesmo vídeo. Quando o
  // upload já chegou na Meta, isso só é seguro DEPOIS de olhar o perfil —
  // senão o "novo agendamento" vira o segundo post.
  const aviso = p.tem_container
    ? `O vídeo dessa postagem chegou a subir pra Meta e ela não confirmou. `
      + `Abra ${contaLabel(p)} e confira se o post saiu ANTES de cancelar — `
      + `cancelar libera um novo agendamento do mesmo vídeo. Cancelar mesmo assim?`
    : `Cancelar a postagem em ${contaLabel(p)}?`
  if (!window.confirm(aviso)) return
  try {
    await api(`/api/marketing/postagens/${p.id}`, { method: 'DELETE' })
    toasts.info('Agendamento cancelado', contaLabel(p))
    await loadPostagens()
  } catch (e: any) {
    toasts.error('Erro ao cancelar', pubErrMsg(e))
  }
}
</script>

<template>
  <div class="space-y-3">
    <input
      ref="fileInput"
      type="file"
      accept="image/*,video/*"
      multiple
      class="hidden"
      @change="onFilePicked"
    >

    <!-- filtro -->
    <div class="flex flex-wrap items-center gap-2">
      <div class="relative">
        <Search class="absolute left-2 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" />
        <input
          v-model="q"
          type="text"
          placeholder="filtrar por modelo, marca, SKU, arquivo…"
          class="h-8 w-72 max-w-full rounded-md border bg-background pl-7 pr-2 text-xs outline-none focus:ring-2 focus:ring-ring"
        />
      </div>
      <select
        v-model="statusFilter"
        class="h-8 rounded-md border bg-background px-2 text-xs outline-none focus:ring-2 focus:ring-ring"
      >
        <option value="todos">todos</option>
        <option value="pendente">pendentes</option>
        <option value="aprovado">aprovados</option>
        <option value="reprovado">não aprovados</option>
      </select>
      <span class="ml-auto text-xs text-muted-foreground">
        {{ filteredRows.length }} de {{ rows.length }}
      </span>
    </div>

    <div class="border rounded-lg overflow-auto max-h-[calc(100vh-300px)]">
      <table class="w-full text-sm border-collapse">
        <thead class="sticky top-0 bg-muted z-10">
          <tr>
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[120px]">Modelo</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[95px]">Marca</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[90px]">Equipe</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border w-28 min-w-[80px]">SKU</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[150px]">Roteiro</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[200px]">Arquivos</th>
            <th class="text-center px-2 py-2 font-medium border-b border-border w-24">Aprovado</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[190px]">Publicação</th>
            <th class="text-center px-2 py-2 font-medium border-b border-border w-12"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading && !rows.length">
            <td colspan="9" class="text-center py-6 text-muted-foreground">
              <Loader2 class="inline h-4 w-4 animate-spin" /> carregando…
            </td>
          </tr>
          <tr v-else-if="!rows.length">
            <td colspan="9" class="text-center py-6 text-muted-foreground">
              Nenhum criativo ainda — adicione a primeira linha abaixo.
            </td>
          </tr>
          <tr v-else-if="!filteredRows.length">
            <td colspan="9" class="text-center py-6 text-muted-foreground">
              Nenhuma linha bate com o filtro.
            </td>
          </tr>

          <!-- data rows -->
          <tr v-for="r in filteredRows" :key="r.id" class="hover:bg-accent/30 align-top">
            <!-- modelo -->
            <td
              class="border border-border px-2 py-1.5 text-xs"
              :class="{
                'cursor-pointer': canEdit,
                'ring-2 ring-blue-500 ring-inset bg-background': isEditing(r.id, 'modelo'),
                'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(r.id, 'modelo'),
              }"
              @click="!isEditing(r.id, 'modelo') && startEdit(r, 'modelo')"
            >
              <select
                v-if="isEditing(r.id, 'modelo')"
                :ref="setEditInputRef"
                v-model="editValue"
                class="w-full text-xs bg-background outline-none rounded"
                @change="commitEdit" @blur="commitEdit" @keydown.escape.prevent="cancelEdit"
              >
                <option
                  v-if="editValue && !MODELO_OPTIONS.includes(editValue)"
                  :value="editValue"
                >{{ editValue }}</option>
                <option v-for="o in MODELO_OPTIONS" :key="o" :value="o" :title="MODELO_HINTS[o]">{{ o }}</option>
              </select>
              <span v-else class="font-medium" :title="MODELO_HINTS[r.modelo]">{{ r.modelo }}</span>
            </td>
            <!-- marca -->
            <td
              class="border border-border px-2 py-1.5 text-xs"
              :class="{
                'cursor-pointer': canEdit,
                'ring-2 ring-blue-500 ring-inset bg-background': isEditing(r.id, 'marca'),
                'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(r.id, 'marca'),
              }"
              @click="!isEditing(r.id, 'marca') && startEdit(r, 'marca')"
            >
              <select
                v-if="isEditing(r.id, 'marca')"
                :ref="setEditInputRef"
                v-model="editValue"
                class="w-full text-xs bg-background outline-none rounded"
                @change="commitEdit" @blur="commitEdit" @keydown.escape.prevent="cancelEdit"
              >
                <option value="">—</option>
                <option
                  v-if="editValue && !marcaValues.includes(editValue)"
                  :value="editValue"
                >{{ editValue }}</option>
                <option v-for="o in marcaOptions" :key="o.value" :value="o.value">{{ o.label }}</option>
              </select>
              <span v-else>{{ r.marca || '—' }}</span>
            </td>
            <!-- equipe -->
            <td
              class="border border-border px-2 py-1.5 text-xs"
              :class="{
                'cursor-pointer': canEdit,
                'ring-2 ring-blue-500 ring-inset bg-background': isEditing(r.id, 'equipe'),
                'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(r.id, 'equipe'),
              }"
              @click="!isEditing(r.id, 'equipe') && startEdit(r, 'equipe')"
            >
              <select
                v-if="isEditing(r.id, 'equipe')"
                :ref="setEditInputRef"
                v-model="editValue"
                class="w-full text-xs bg-background outline-none rounded"
                @change="commitEdit" @blur="commitEdit" @keydown.escape.prevent="cancelEdit"
              >
                <option value="">—</option>
                <option
                  v-if="editValue && !equipeOptions.includes(editValue)"
                  :value="editValue"
                >{{ editValue }}</option>
                <option v-for="o in equipeOptions" :key="o" :value="o">{{ o }}</option>
              </select>
              <span v-else>{{ r.equipe || '—' }}</span>
            </td>
            <!-- sku -->
            <td
              class="border border-border px-2 py-1.5 text-xs font-mono"
              :class="{
                'cursor-pointer': canEdit,
                'ring-2 ring-blue-500 ring-inset bg-background': isEditing(r.id, 'sku'),
                'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(r.id, 'sku'),
              }"
              @click="!isEditing(r.id, 'sku') && startEdit(r, 'sku')"
            >
              <input
                v-if="isEditing(r.id, 'sku')"
                :ref="setEditInputRef"
                v-model="editValue" type="text"
                class="w-full text-xs bg-transparent outline-none font-mono"
                @blur="commitEdit" @keydown.enter.prevent="commitEdit" @keydown.escape.prevent="cancelEdit"
              />
              <span v-else class="break-all">{{ r.sku || '—' }}</span>
            </td>
            <!-- roteiro: ponteiro pra aba Roteiros (o texto saiu daqui) -->
            <td class="border border-border px-2 py-1.5 text-xs">
              <div class="flex flex-col items-start gap-1">
                <button
                  v-if="r.roteiro_id"
                  type="button"
                  class="inline-flex max-w-full items-center gap-1 text-primary hover:underline"
                  :title="`Abrir o briefing na aba Roteiros: ${r.roteiro_titulo || 'sem título'}`"
                  @click.stop="emit('abrir-roteiro', r.roteiro_id)"
                >
                  <NotebookPen class="size-3.5 shrink-0" />
                  <span class="truncate">{{ r.roteiro_titulo || 'sem título' }}</span>
                </button>
                <button
                  v-else-if="canEdit"
                  type="button"
                  class="inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] bg-background hover:bg-muted disabled:opacity-50"
                  title="Cria o roteiro já com o modelo, a marca e o SKU desta linha, e abre pra escrever"
                  :disabled="criandoRoteiro === r.id"
                  @click.stop="criarRoteiroDaLinha(r)"
                >
                  <Loader2 v-if="criandoRoteiro === r.id" class="size-3 animate-spin" />
                  <Plus v-else class="size-3" />
                  escrever roteiro
                </button>
                <span v-else class="text-muted-foreground italic">sem roteiro</span>
                <span
                  v-if="r.feedback"
                  class="pill-warning"
                  :title="`Recado no portal do time de criação: ${r.feedback}`"
                >
                  <MessageSquare class="size-3" /> recado
                </span>
              </div>
            </td>
            <!-- arquivos -->
            <td class="border border-border px-2 py-1.5 text-xs">
              <div class="space-y-1">
                <div v-for="f in r.files" :key="f.id" class="flex items-center gap-1">
                  <button
                    type="button"
                    class="inline-flex min-w-0 items-center gap-1 text-primary hover:underline"
                    :title="`Visualizar ${f.file_name}`"
                    @click.stop="openPreview(r, f)"
                  >
                    <ImageIcon v-if="f.file_mime?.startsWith('image/')" class="size-3.5 shrink-0" />
                    <Film v-else-if="f.file_mime?.startsWith('video/')" class="size-3.5 shrink-0" />
                    <FileIcon v-else class="size-3.5 shrink-0" />
                    <span class="truncate max-w-[140px]">{{ f.file_name }}</span>
                  </button>
                  <span class="text-muted-foreground shrink-0 text-[10px]">{{ fmtSize(f.file_size) }}</span>
                  <button
                    v-if="canEdit && !r.pushed_at"
                    class="shrink-0 rounded p-0.5 text-muted-foreground hover:text-destructive hover:bg-destructive/10"
                    title="Apagar arquivo"
                    @click.stop="removeFile(r, f)"
                  >
                    <X class="size-3" />
                  </button>
                </div>
                <div class="flex items-center gap-1.5">
                  <button
                    v-if="canEdit && !r.pushed_at"
                    class="inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] bg-background hover:bg-muted disabled:opacity-50"
                    :disabled="uploadingId === r.id"
                    @click.stop="pickFile(r)"
                  >
                    <Loader2 v-if="uploadingId === r.id" class="size-3 animate-spin" />
                    <Upload v-else class="size-3" />
                    {{ r.files.length ? 'adicionar' : 'anexar' }}
                  </button>
                  <span
                    v-if="r.pushed_at"
                    class="pill-success"
                    :title="`Enviado pro MEGA: ${r.pushed_dest}`"
                  >
                    <CloudUpload class="size-3" /> no MEGA
                  </span>
                </div>
              </div>
            </td>
            <!-- aprovado -->
            <td class="border border-border px-2 py-1.5 text-center">
              <div v-if="isAdmin" class="inline-flex items-center gap-1">
                <button
                  class="rounded p-1 border transition-colors disabled:opacity-50"
                  :class="r.aprovado === true
                    ? 'bg-emerald-500 text-white border-emerald-500'
                    : 'text-emerald-600 bg-background hover:bg-emerald-500/10'"
                  :disabled="approvingId === r.id"
                  title="Aprovar (envia os arquivos pro MEGA)"
                  @click.stop="aprovar(r, true)"
                >
                  <Loader2 v-if="approvingId === r.id" class="size-3.5 animate-spin" />
                  <Check v-else class="size-3.5" />
                </button>
                <button
                  class="rounded p-1 border transition-colors disabled:opacity-50"
                  :class="r.aprovado === false
                    ? 'bg-red-500 text-white border-red-500'
                    : 'text-red-600 bg-background hover:bg-red-500/10'"
                  :disabled="approvingId === r.id"
                  title="Não aprovar — abre a caixa do motivo, que a agência lê no portal"
                  @click.stop="openRecusa(r)"
                >
                  <X class="size-3.5" />
                </button>
              </div>
              <span v-else-if="r.aprovado === true" class="pill-success"><Check class="size-3" /> aprovado</span>
              <span v-else-if="r.aprovado === false" class="pill-danger"><X class="size-3" /> não aprovado</span>
              <span v-else class="pill-muted">pendente</span>
            </td>
            <!-- publicação (robô de postagem — Cadastros › Redes Sociais) -->
            <td class="border border-border px-2 py-1.5 text-xs">
              <div class="space-y-1">
                <div v-for="p in postagensDe(r)" :key="p.id" class="flex items-center gap-1">
                  <a
                    v-if="p.post_url"
                    :href="p.post_url"
                    target="_blank"
                    rel="noopener"
                    :class="statusPill(p.status)"
                    :title="postagemTitle(p)"
                  >
                    <span class="truncate max-w-[110px]">{{ contaLabel(p) }}</span>
                    <span class="shrink-0">{{ statusLabel(p.status) }}</span>
                    <span class="shrink-0 opacity-70">{{ postagemQuando(p) }}</span>
                    <ExternalLink class="size-3 shrink-0" />
                  </a>
                  <span v-else :class="statusPill(p.status)" :title="postagemTitle(p)">
                    <Loader2
                      v-if="p.status === 'publicando' || p.status === 'containering'"
                      class="size-3 shrink-0 animate-spin"
                    />
                    <CalendarClock
                      v-else-if="p.status === 'agendado' || p.status === 'pendente'"
                      class="size-3 shrink-0"
                    />
                    <AlertTriangle
                      v-else-if="p.status === 'falhou' || p.status === 'revisar'"
                      class="size-3 shrink-0"
                    />
                    <span class="truncate max-w-[110px]">{{ contaLabel(p) }}</span>
                    <span class="shrink-0">{{ statusLabel(p.status) }}</span>
                    <span class="shrink-0 opacity-70">{{ postagemQuando(p) }}</span>
                  </span>
                  <button
                    v-if="canEdit && podeCancelar(p)"
                    class="shrink-0 rounded p-0.5 text-muted-foreground hover:text-destructive hover:bg-destructive/10"
                    :title="`Cancelar a postagem em ${contaLabel(p)}`"
                    @click.stop="cancelarPostagem(p)"
                  >
                    <X class="size-3" />
                  </button>
                </div>
                <!-- O title fica no SPAN de fora: botão desabilitado não
                     dispara hover no Chrome/Safari, e o motivo é justamente o
                     que o operador precisa ler quando não dá pra publicar. -->
                <span
                  class="inline-block"
                  :title="motivoPublicar(r, canEdit) || 'Publicar agora ou agendar nas contas da marca'"
                >
                  <button
                    class="inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] bg-background hover:bg-muted disabled:opacity-50"
                    :disabled="!!motivoPublicar(r, canEdit)"
                    @click.stop="openPublicar(r)"
                  >
                    <Send class="size-3" /> publicar / agendar
                  </button>
                </span>
              </div>
            </td>
            <!-- ações -->
            <td class="border border-border px-1 py-1.5 text-center">
              <button
                v-if="canEdit && (!r.pushed_at || isAdmin)"
                class="p-1 text-muted-foreground hover:text-destructive hover:bg-destructive/10 rounded"
                title="Excluir linha"
                @click.stop="remove(r)"
              >
                <Trash2 class="size-3.5" />
              </button>
            </td>
          </tr>

          <!-- add row -->
          <tr v-if="canEdit" class="bg-blue-50/40 dark:bg-blue-900/10">
            <td class="border border-border px-1 py-1">
              <select
                v-model="newRow.modelo"
                class="w-full text-xs border rounded px-1.5 py-1 bg-background"
                :class="{ 'text-muted-foreground': !newRow.modelo }"
              >
                <option value="" disabled>modelo…</option>
                <option v-for="o in MODELO_OPTIONS" :key="o" :value="o" :title="MODELO_HINTS[o]">{{ o }}</option>
              </select>
            </td>
            <td class="border border-border px-1 py-1">
              <select
                v-model="newRow.marca"
                class="w-full text-xs border rounded px-1.5 py-1 bg-background"
                :class="{ 'text-muted-foreground': !newRow.marca }"
              >
                <option value="">marca —</option>
                <option v-for="o in marcaOptions" :key="o.value" :value="o.value">{{ o.label }}</option>
              </select>
            </td>
            <td class="border border-border px-1 py-1">
              <select
                v-model="newRow.equipe"
                class="w-full text-xs border rounded px-1.5 py-1 bg-background"
                :class="{ 'text-muted-foreground': !newRow.equipe }"
              >
                <option value="">equipe —</option>
                <option v-for="o in equipeOptions" :key="o" :value="o">{{ o }}</option>
              </select>
            </td>
            <td class="border border-border px-1 py-1">
              <input
                v-model="newRow.sku"
                type="text" placeholder="SKU"
                class="w-full text-xs border rounded px-1.5 py-1 bg-background font-mono"
                @keydown.enter="addRow"
              />
            </td>
            <td colspan="4" class="border border-border px-2 py-1 text-[11px] text-muted-foreground align-middle">
              Arquivos e roteiro você preenche na linha, depois de adicionar.
            </td>
            <td class="border border-border px-1 py-1 text-center">
              <button
                class="inline-flex items-center justify-center rounded bg-primary text-primary-foreground p-1.5 disabled:opacity-50"
                title="Adicionar linha"
                :disabled="adding"
                @click="addRow"
              >
                <Loader2 v-if="adding" class="size-3.5 animate-spin" />
                <Plus v-else class="size-3.5" />
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <p class="text-xs text-muted-foreground">
      Clique numa célula pra editar (Enter salva, Esc cancela). O roteiro é escrito
      na aba Roteiros — "escrever roteiro" cria um já com a marca e o SKU da linha.
      Dá pra anexar vários
      arquivos por linha; clique no nome pra visualizar a imagem ou o vídeo (com opção
      de baixar). Ao aprovar (<Check class="size-3 inline text-emerald-600" />), todos os
      arquivos sobem pra pasta do produto no MEGA — o produto é achado pelo SKU na
      Tabela de Preços (aba Produtos). Depois de aprovado, "publicar / agendar"
      (<Send class="size-3 inline text-muted-foreground" />) manda o arquivo pras
      contas da marca em Cadastros › Redes Sociais — quem publica é o robô no
      servidor, na hora marcada (BRT).
    </p>

    <!-- recusa com motivo -->
    <div
      v-if="recusa"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      @click.self="closeRecusa"
    >
      <div class="w-full max-w-md rounded-lg border border-border bg-card shadow-xl">
        <div class="flex items-center gap-2 border-b px-4 py-3">
          <MessageSquare class="size-4 shrink-0 text-red-600" />
          <span class="text-sm font-medium">Não aprovar</span>
          <button class="btn btn-sm btn-ghost ml-auto px-1.5" title="Fechar (Esc)" @click="closeRecusa">
            <X class="size-4" />
          </button>
        </div>
        <div class="space-y-2 px-4 py-4">
          <p class="text-xs text-muted-foreground">
            <b class="text-foreground">{{ recusa.modelo }}</b>
            <span v-if="recusa.sku" class="font-mono"> · {{ recusa.sku }}</span>
          </p>
          <textarea
            v-model="recusaTexto"
            rows="4"
            autofocus
            class="w-full resize-y rounded-md border bg-background px-2.5 py-2 text-xs leading-relaxed outline-none focus:ring-2 focus:ring-ring"
            placeholder="Por que voltou? Ex.: áudio estourado nos 3s finais; falta o SKU na tela."
          />
          <p class="text-[11px] text-muted-foreground">
            Esse texto aparece no portal do time de criação, junto do criativo. Sem
            ele a agência regrava adivinhando.
          </p>
        </div>
        <div class="flex items-center justify-end gap-2 border-t px-4 py-3">
          <button class="btn btn-sm btn-ghost" @click="closeRecusa">cancelar</button>
          <button class="btn btn-sm btn-destructive gap-1" @click="confirmarRecusa">
            <X class="size-3.5" /> não aprovar
          </button>
        </div>
      </div>
    </div>

    <!-- preview modal -->
    <div
      v-if="preview"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4"
      @click.self="closePreview"
    >
      <div class="flex max-h-full w-full max-w-5xl flex-col overflow-hidden rounded-lg border border-border bg-card shadow-xl">
        <div class="flex items-center gap-2 border-b px-3 py-2">
          <ImageIcon v-if="previewIsImage" class="size-4 shrink-0 text-muted-foreground" />
          <Film v-else-if="previewIsVideo" class="size-4 shrink-0 text-muted-foreground" />
          <FileIcon v-else class="size-4 shrink-0 text-muted-foreground" />
          <span class="truncate text-sm font-medium">{{ preview.file.file_name }}</span>
          <span class="shrink-0 text-xs text-muted-foreground">{{ fmtSize(preview.file.file_size) }}</span>
          <div class="ml-auto flex shrink-0 items-center gap-1.5">
            <a
              :href="fileUrl(preview.row, preview.file, true)"
              class="btn btn-sm gap-1"
            >
              <Download class="size-3.5" /> baixar
            </a>
            <button class="btn btn-sm btn-ghost px-1.5" title="Fechar (Esc)" @click="closePreview">
              <X class="size-4" />
            </button>
          </div>
        </div>
        <div class="flex min-h-[200px] flex-1 items-center justify-center overflow-auto bg-black/40 p-2">
          <img
            v-if="previewIsImage"
            :src="fileUrl(preview.row, preview.file)"
            :alt="preview.file.file_name"
            class="max-h-[75vh] max-w-full object-contain"
          />
          <video
            v-else-if="previewIsVideo"
            :src="fileUrl(preview.row, preview.file)"
            controls
            autoplay
            class="max-h-[75vh] max-w-full"
          />
          <div v-else class="p-8 text-center text-sm text-muted-foreground">
            Não dá pra visualizar esse tipo de arquivo aqui — use o botão baixar.
          </div>
        </div>
      </div>
    </div>

    <!-- modal Publicar / Agendar -->
    <div
      v-if="pub"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      @click.self="closePublicar"
    >
      <div class="flex max-h-full w-full max-w-2xl flex-col overflow-hidden rounded-lg border border-border bg-card shadow-xl">
        <div class="flex items-center gap-2 border-b px-3 py-2">
          <Send class="size-4 shrink-0 text-muted-foreground" />
          <span class="truncate text-sm font-medium">
            Publicar / agendar — {{ pub.modelo }}<span v-if="pub.sku" class="text-muted-foreground"> · {{ pub.sku }}</span>
          </span>
          <button class="btn btn-sm btn-ghost ml-auto px-1.5" title="Fechar (Esc)" @click="closePublicar">
            <X class="size-4" />
          </button>
        </div>

        <div class="flex-1 space-y-3 overflow-auto p-3 text-xs">
          <!-- modo seco: o robô registra tudo e NÃO chama a Meta -->
          <p
            v-if="pubCommit === false"
            class="rounded border border-amber-500/30 bg-amber-500/10 px-2 py-1.5 text-amber-700 dark:text-amber-400"
          >
            <AlertTriangle class="mr-1 inline size-3.5" />
            Modo seco ligado no servidor: o robô vai registrar a postagem e <strong>não publica</strong> nas redes.
          </p>
          <p v-else-if="pubCommit === null" class="text-[11px] text-muted-foreground">
            Se o robô estiver em modo seco no servidor, a postagem fica só registrada e nada vai ao ar.
          </p>

          <!-- arquivo -->
          <div>
            <div class="mb-1 font-medium">Arquivo</div>
            <div v-if="pubFiles.length > 1" class="space-y-1">
              <label v-for="f in pubFiles" :key="f.id" class="flex cursor-pointer items-center gap-1.5">
                <input v-model="pubFileId" type="radio" :value="f.id" @change="loadContas" />
                <Film v-if="f.file_mime?.startsWith('video/')" class="size-3.5 shrink-0 text-muted-foreground" />
                <ImageIcon v-else-if="f.file_mime?.startsWith('image/')" class="size-3.5 shrink-0 text-muted-foreground" />
                <FileIcon v-else class="size-3.5 shrink-0 text-muted-foreground" />
                <span class="truncate">{{ f.file_name }}</span>
                <span class="shrink-0 text-muted-foreground">{{ fmtSize(f.file_size) }}</span>
              </label>
            </div>
            <div v-else-if="pubFiles.length" class="flex items-center gap-1.5">
              <Film v-if="pubFiles[0].file_mime?.startsWith('video/')" class="size-3.5 shrink-0 text-muted-foreground" />
              <ImageIcon v-else-if="pubFiles[0].file_mime?.startsWith('image/')" class="size-3.5 shrink-0 text-muted-foreground" />
              <FileIcon v-else class="size-3.5 shrink-0 text-muted-foreground" />
              <span class="truncate">{{ pubFiles[0].file_name }}</span>
              <span class="shrink-0 text-muted-foreground">{{ fmtSize(pubFiles[0].file_size) }}</span>
            </div>
          </div>

          <!-- marca (manda nas contas oferecidas) -->
          <div>
            <div class="mb-1 font-medium">Marca</div>
            <span v-if="pub.marca_id && pubMarcaNome" class="pill-muted">{{ pubMarcaNome }}</span>
            <select
              v-else
              v-model="pubMarcaId"
              class="h-8 w-full max-w-xs rounded-md border bg-background px-2 text-xs outline-none focus:ring-2 focus:ring-ring"
              @change="loadContas"
            >
              <option value="">escolha a marca…</option>
              <option v-for="m in marcas" :key="m.id" :value="m.id">{{ m.nome || m.slug }}</option>
            </select>
            <p class="mt-1 text-[11px] text-muted-foreground">
              As contas abaixo são as dessa marca em Cadastros › Redes Sociais.
            </p>
          </div>

          <!-- contas -->
          <div>
            <div class="mb-1 font-medium">Contas</div>
            <div v-if="pubContasLoading" class="text-muted-foreground">
              <Loader2 class="inline size-3 animate-spin" /> carregando contas…
            </div>
            <div v-else-if="!pubContas.length" class="text-muted-foreground">
              Nenhuma conta cadastrada pra essa marca.
            </div>
            <div v-else class="space-y-1">
              <label
                v-for="c in pubContas"
                :key="c.id"
                class="flex items-center gap-1.5"
                :class="c.pode_postar ? 'cursor-pointer' : 'opacity-60'"
              >
                <input
                  type="checkbox"
                  :disabled="!c.pode_postar"
                  :checked="pubSel.includes(c.id)"
                  @change="toggleConta(c)"
                />
                <span class="shrink-0 text-muted-foreground">{{ plataformaLabel(c.plataforma) }}</span>
                <span class="truncate">{{ contaLabel(c) }}</span>
                <span v-if="!c.pode_postar" class="pill-muted shrink-0">{{ motivoConta(c.motivo) }}</span>
              </label>
            </div>
          </div>

          <!-- legenda -->
          <div>
            <div class="mb-1 flex items-center gap-2 font-medium">
              Legenda
              <Loader2 v-if="pubLegendaLoading" class="size-3 shrink-0 animate-spin text-muted-foreground" />
              <span
                class="ml-auto truncate font-normal"
                :class="pubLegenda.length > LEGENDA_MAX ? 'text-destructive' : 'text-muted-foreground'"
              >{{ pubLegendaRotulo }}</span>
            </div>
            <textarea
              :value="pubLegenda"
              rows="5"
              :maxlength="LEGENDA_MAX"
              class="w-full rounded-md border bg-background px-2 py-1.5 text-xs leading-snug outline-none focus:ring-2 focus:ring-ring"
              placeholder="Texto que vai junto do post…"
              @input="onLegendaInput"
            />
            <p
              v-if="pubSemLegenda && !pubLegendaLoading"
              class="mt-1 rounded border border-amber-500/30 bg-amber-500/10 px-2 py-1.5 text-amber-700 dark:text-amber-400"
            >
              <AlertTriangle class="mr-1 inline size-3.5" />
              Esse post vai sair <strong>sem legenda</strong> — e legenda não dá pra acrescentar
              depois que o post está no ar. Escreva aqui, ou cadastre o padrão da marca.
            </p>
            <p v-else class="mt-1 text-[11px] text-muted-foreground">
              Texto já pronto: é exatamente isso que vai pro post. Editar aqui vale só pra esta
              postagem — o padrão fica em Cadastros › Marcas.
            </p>
            <!-- Sem o v-else-if de propósito: quando a consulta falha o campo
                 fica vazio, então o aviso âmbar de cima aparece e este aqui é o
                 que explica POR QUE não veio nada. -->
            <p v-if="pubLegendaErro" class="mt-1 text-[11px] text-muted-foreground">
              Não deu pra consultar a legenda padrão (servidor desatualizado ou sem permissão) —
              escreva a legenda à mão.
            </p>
          </div>

          <!-- quando -->
          <div>
            <div class="mb-1 font-medium">Quando</div>
            <label class="flex cursor-pointer items-center gap-1.5">
              <input v-model="pubQuando" type="radio" value="agora" />
              publicar agora (no próximo ciclo do robô)
            </label>
            <label class="mt-1 flex cursor-pointer flex-wrap items-center gap-1.5">
              <input v-model="pubQuando" type="radio" value="agendar" />
              agendar para
              <input
                v-model="pubDataHora"
                type="datetime-local"
                :disabled="pubQuando !== 'agendar'"
                class="h-7 rounded-md border bg-background px-1.5 text-xs outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
              />
              <span class="text-muted-foreground">horário de Brasília</span>
            </label>
          </div>

          <!-- opções da plataforma -->
          <label v-if="pubTemInstagram" class="flex cursor-pointer items-center gap-1.5">
            <input v-model="pubShareToFeed" type="checkbox" />
            mostrar o Reel também no feed do Instagram
          </label>

          <p
            v-if="pubErr"
            class="rounded border border-red-500/30 bg-red-500/10 px-2 py-1.5 text-red-700 dark:text-red-400"
          >{{ pubErr }}</p>
        </div>

        <div class="flex items-center gap-2 border-t px-3 py-2">
          <span class="text-[11px] text-muted-foreground">
            {{ pubSel.length }} de {{ pubContas.length }} conta(s) marcada(s)
          </span>
          <button class="btn btn-sm ml-auto" @click="closePublicar">cancelar</button>
          <button
            class="btn btn-sm btn-primary gap-1"
            :disabled="pubSaving || !pubSel.length"
            @click="salvarPostagem"
          >
            <Loader2 v-if="pubSaving" class="size-3.5 animate-spin" />
            <CalendarClock v-else-if="pubQuando === 'agendar'" class="size-3.5" />
            <Send v-else class="size-3.5" />
            {{ pubQuando === 'agendar' ? 'agendar' : 'publicar' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
