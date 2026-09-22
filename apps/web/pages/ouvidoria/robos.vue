<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  AlertCircle,
  AlertTriangle,
  ArchiveRestore,
  Bot,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  Loader2,
  Play,
  Radar,
  RotateCcw,
  Search,
  ShieldOff,
  Sparkles,
  Trash2,
  UserRound,
  Wrench,
} from 'lucide-vue-next'
import { isoDateBrt, isoDaysAgo, isoToday } from '~/lib/date'
import { PLATAFORMAS, plataformaInfo } from '~/components/OuvidoriaPlataforma.vue'
import type { ModoRobo } from '~/components/OuvidoriaModoToggle.vue'

definePageMeta({ middleware: ['permission'], permission: { resource: 'ouvidoria', action: 'view' } })

// ── Ouvidoria › Robôs (21/09/2026) ─────────────────────────────────────────
// Duas abas. "Robôs" é o catálogo: cada robô com modo (ligado / silencioso /
// desligado), o que faz, cadência, última rodada, saúde — e uma linha
// expansível com a configuração, as contas com problema e as últimas rodadas.
// "Ocorrências" é a lista única do que os robôs encontraram: o robô abre, re-vê
// a cada rodada e fecha sozinho quando o problema some; a pessoa marca como
// tratada ou ignora. A tela só LÊ o que o worker gravou — o único jeito de
// fazer um robô trabalhar daqui é o "Rodar agora", que agenda uma rodada no
// backend. Contrato da API: routers/ouvidoria.py.

type Saude = 'ok' | 'falhando' | 'parado' | 'desligado'
type Severidade = 'urgente' | 'pessoa' | 'baixa' | 'robo_segurou' | 'info'
type Fechamento = 'sumiu' | 'tratada' | 'ignorada'
type StatusFiltro = 'abertas' | 'fechadas' | 'todas'
type Aba = 'robos' | 'ocorrencias'

// GET /api/ouvidoria/robos — colunas de ouvidoria_robos + agregados.
type Robo = {
  chave: string
  nome: string
  descricao: string | null
  area: string | null
  cadencia_texto: string | null
  plataformas: string[]
  modo: ModoRobo
  modo_alterado_por: string | null
  modo_alterado_em: string | null
  // Lixeira do painel: com data, a linha some da lista (o robô continua
  // rodando e avisando igual — arquivar é só sobre a vista).
  arquivado_em: string | null
  arquivado_por: string | null
  // Override dos destinatários do Threema (texto cru salvo na tela); vazio =
  // padrão do .env. Os IDs efetivos, já com nome, vêm resolvidos em
  // `threema_destinatarios`, e `threema_origem` diz de onde saíram
  // (robo = override | env = variável do robô | geral = fallback | null).
  threema_recipients: string | null
  threema_destinatarios: { id: string; nome: string }[]
  threema_origem: 'robo' | 'env' | 'geral' | null
  reaviso_horas: number
  // Parâmetros efetivos do robô (padrão do catálogo por baixo do que está
  // salvo) — mostrados na linha expandida.
  config: Record<string, unknown>
  // chave da config → rótulo com a unidade ("Cadência esperada (min)"), do
  // catálogo do backend. Chave que não vier aqui é mostrada crua.
  config_rotulos: Record<string, string>
  ultima_rodada_em: string | null
  ultima_rodada_ok: boolean | null
  ultima_rodada_resumo: string | null
  ultima_rodada_duracao_ms: number | null
  ultima_falha_em: string | null
  ultima_falha_erro: string | null
  created_at?: string
  updated_at?: string
  // Agregados
  abertas: number
  abertas_pessoa: number
  rodadas_hoje: number
  rodadas_hoje_ok: number
  // 'parado' = ligado e sem rodada há mais de 3× a cadência esperada.
  saude: Saude
}

// ouvidoria_rodadas (RodadaOut)
type Rodada = {
  id: string
  iniciada_em: string
  terminada_em: string | null
  duracao_ms: number | null
  ok: boolean
  resumo: string | null
  contadores: Record<string, unknown>
  erro: string | null
}

// Conta que o robô não conseguiu olhar: a ocorrência `conta:<id>` aberta
// (ContaOut). O erro da API fica em `dados.erro`.
type ContaProblema = {
  conta: string | null
  plataforma: string | null
  titulo: string
  aberta_em: string
  dados: Record<string, unknown>
}

// GET /api/ouvidoria/robos/{chave}
type RoboDetalhe = Robo & {
  rodadas: Rodada[]
  contas: ContaProblema[]
}

// ouvidoria_ocorrencias (OcorrenciaOut)
type Ocorrencia = {
  id: string
  robo_chave: string
  robo_nome: string | null
  // Chave de idempotência dentro do robô: "tiktok:<pedido>", "conta:<id>"…
  chave: string
  plataforma: string | null
  conta: string | null
  pedido: string | null
  titulo: string
  detalhe: string | null
  acao: string | null
  link: string | null
  severidade: Severidade
  precisa_pessoa: boolean
  dados: Record<string, unknown>
  aberta_em: string
  ultima_vista_em: string
  avisada_em: string | null
  reavisada_em: string | null
  fechada_em: string | null
  fechamento: Fechamento | null
  fechada_por: string | null
  created_at?: string
  updated_at?: string
}

type Resumo = {
  abertas: number
  abertas_pessoa: number
  novas_hoje: number
  sumiram_7d: number
  tratadas_7d: number
  contas_sem_vigilancia: number
}

type PorRobo = { chave: string; nome: string; abertas: number }

// GET /api/ouvidoria/ocorrencias — `resumo` e `por_robo` são globais (não
// seguem o filtro); `total` é quantas casam com o filtro (a lista corta em
// `limit`).
type OcorrenciasResp = {
  itens: Ocorrencia[]
  total: number
  resumo: Resumo
  por_robo: PorRobo[]
}

const RESUMO_VAZIO: Resumo = {
  abertas: 0, abertas_pessoa: 0, novas_hoje: 0, sumiram_7d: 0, tratadas_7d: 0, contas_sem_vigilancia: 0,
}

const { api } = useApi()
const toasts = useToasts()
const canEdit = useCan('ouvidoria', 'edit')
const route = useRoute()
const router = useRouter()

// Códigos que o routers/ouvidoria.py devolve em `detail.code`, em linguagem
// de operação.
const ERRO_CODIGO: Record<string, string> = {
  robo_nao_encontrado: 'Robô não encontrado — a lista pode estar velha, atualize a página',
  robo_sem_runner: 'Este robô ainda não tem rotina pra rodar daqui',
  rodada_em_andamento: 'O robô já está no meio de uma rodada — o resultado aparece aqui em instantes',
  rodada_recente: 'A última rodada terminou há menos de 1 min — espere um pouco antes de rodar de novo',
  ocorrencia_nao_encontrada: 'Ocorrência não encontrada — a lista pode estar velha',
  ocorrencia_duplicada: 'O robô já abriu outra linha desse caso — não precisa reabrir',
}
function apiError(e: any) {
  const detail = e?.data?.detail
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((d: any) => String(d?.msg || d?.message || '').replace(/^Value error, /, ''))
      .filter(Boolean)
    if (msgs.length) return msgs.join('; ')
  }
  if (detail && typeof detail === 'object') {
    return detail.message || ERRO_CODIGO[detail.code] || detail.code || e?.message || 'erro'
  }
  return detail || e?.message || 'erro'
}

// ── Abas + querystring ──────────────────────────────────────────────────────
// ?aba=ocorrencias abre direto na lista (é o link que vai no aviso do
// Threema); ?robo=<chave> já deixa o chip daquele robô marcado.
const tab = ref<Aba>(route.query.aba === 'ocorrencias' ? 'ocorrencias' : 'robos')
const filtroRobo = ref<string>(typeof route.query.robo === 'string' ? route.query.robo : '')

watch([tab, filtroRobo], ([t, r]) => {
  const query: Record<string, string> = {}
  for (const [k, v] of Object.entries(route.query)) {
    if (k !== 'aba' && k !== 'robo' && typeof v === 'string') query[k] = v
  }
  query.aba = t
  if (r) query.robo = r
  void router.replace({ query })
})

// ── Datas em pt-BR ──────────────────────────────────────────────────────────
// "hoje 16:09", "ontem 22:30", "16/09 12:09" (com ano só quando não é o
// atual). Dia decidido em BRT (lib/date) pra não virar "amanhã" às 22h.
const BRT = 'America/Sao_Paulo'
function fmtRel(v: string | null | undefined): string {
  if (!v) return '—'
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return v
  const dia = isoDateBrt(d)
  const hora = d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', timeZone: BRT })
  if (dia === isoToday()) return `hoje ${hora}`
  if (dia === isoDaysAgo(1)) return `ontem ${hora}`
  const mesmoAno = dia.slice(0, 4) === isoToday().slice(0, 4)
  const data = d.toLocaleDateString('pt-BR', {
    day: '2-digit',
    month: '2-digit',
    ...(mesmoAno ? {} : { year: '2-digit' }),
    timeZone: BRT,
  })
  return `${data} ${hora}`
}
// Só a hora (lista de rodadas do mesmo dia): "16:09"; de outro dia, "16/09 16:09".
function fmtHora(v: string | null | undefined): string {
  if (!v) return '—'
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return v
  const hora = d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', timeZone: BRT })
  if (isoDateBrt(d) === isoToday()) return hora
  return `${d.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit', timeZone: BRT })} ${hora}`
}
// "há 18 h" / "há 3 min" / "há 2 dias" — pro "parado há…" da Saúde.
function fmtHa(v: string | null | undefined): string {
  if (!v) return ''
  const ms = Date.now() - new Date(v).getTime()
  if (Number.isNaN(ms) || ms < 0) return ''
  const min = Math.floor(ms / 60_000)
  if (min < 1) return 'agora'
  if (min < 60) return `há ${min} min`
  const h = Math.floor(min / 60)
  if (h < 48) return `há ${h} h`
  return `há ${Math.floor(h / 24)} dias`
}
// "5,0 s" / "1 min 12 s".
function fmtDuracao(ms: number | null | undefined): string {
  if (ms == null) return ''
  if (ms < 60_000) {
    return `${(ms / 1000).toLocaleString('pt-BR', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} s`
  }
  const min = Math.floor(ms / 60_000)
  const s = Math.round((ms % 60_000) / 1000)
  return `${min} min${s ? ` ${s} s` : ''}`
}
function duracaoRodada(r: Rodada): string {
  if (r.duracao_ms != null) return fmtDuracao(r.duracao_ms)
  if (!r.terminada_em) return 'em andamento'
  const ms = new Date(r.terminada_em).getTime() - new Date(r.iniciada_em).getTime()
  return Number.isNaN(ms) ? '' : fmtDuracao(Math.max(0, ms))
}
function fmtNum(n: number): string {
  return n.toLocaleString('pt-BR')
}

// ── Carga ───────────────────────────────────────────────────────────────────
const robos = ref<Robo[]>([])
const robosLoading = ref(false)
const robosError = ref<string | null>(null)
const robosCarregou = ref(false)

const ocorrencias = ref<Ocorrencia[]>([])
const ocTotal = ref(0)
const resumo = ref<Resumo>({ ...RESUMO_VAZIO })
const porRobo = ref<PorRobo[]>([])
const ocLoading = ref(false)
const ocError = ref<string | null>(null)
const ocCarregou = ref(false)
// Contador de pedidos: resposta velha (filtro trocado no meio) não sobrescreve
// a nova.
let ocSeq = 0

// Filtros da aba Ocorrências — todos vão pro servidor (a lista tem limite).
const LIMITE = 200
const busca = ref('')
const filtroPlataforma = ref('')
const filtroConta = ref('')
const filtroStatus = ref<StatusFiltro>('abertas')
const filtroPessoa = ref(false)
const filtroDias = ref(7)
// Contas já vistas na sessão: as opções do <select> não podem encolher pra
// só a conta escolhida depois que o filtro aplica.
const contasConhecidas = ref<Set<string>>(new Set())

// Filtros da aba Robôs — são poucos robôs, filtra no navegador.
const robosBusca = ref('')
const robosArea = ref('')
const robosEstado = ref<'' | ModoRobo>('')
const robosSoProblema = ref(false)
// Arquivados ficam fora da lista até alguém pedir pra ver (atalho "N arquivados").
const mostrarArquivados = ref(false)

async function loadRobos() {
  robosLoading.value = true
  robosError.value = null
  try {
    robos.value = await api<Robo[]>('/api/ouvidoria/robos')
    robosCarregou.value = true
  } catch (e: any) {
    robosError.value = apiError(e)
  } finally {
    robosLoading.value = false
  }
}

async function loadOcorrencias() {
  const seq = ++ocSeq
  ocLoading.value = true
  ocError.value = null
  const query: Record<string, string | number | boolean> = {
    status: filtroStatus.value,
    dias: filtroDias.value,
    limit: LIMITE,
  }
  if (filtroRobo.value) query.robo = filtroRobo.value
  if (filtroPlataforma.value) query.plataforma = filtroPlataforma.value
  if (filtroConta.value) query.conta = filtroConta.value
  if (filtroPessoa.value) query.precisa_pessoa = true
  if (busca.value.trim()) query.q = busca.value.trim()
  try {
    const r = await api<OcorrenciasResp>('/api/ouvidoria/ocorrencias', { query })
    if (seq !== ocSeq) return
    ocorrencias.value = r.itens ?? []
    ocTotal.value = r.total ?? ocorrencias.value.length
    resumo.value = { ...RESUMO_VAZIO, ...(r.resumo ?? {}) }
    porRobo.value = r.por_robo ?? []
    const contas = new Set(contasConhecidas.value)
    for (const o of ocorrencias.value) if (o.conta) contas.add(o.conta)
    contasConhecidas.value = contas
    ocCarregou.value = true
  } catch (e: any) {
    if (seq !== ocSeq) return
    ocError.value = apiError(e)
  } finally {
    if (seq === ocSeq) ocLoading.value = false
  }
}

// Recarrega as duas abas (o contador da aba Ocorrências fica no cabeçalho
// mesmo quando a pessoa está em Robôs) + o detalhe dos robôs expandidos.
async function refreshTudo() {
  await Promise.all([
    loadRobos(),
    loadOcorrencias(),
    ...[...expandidos.value].map((chave) => loadDetalhe(chave)),
  ])
}

// Busca digitada espera 300 ms; os selects aplicam na hora.
let buscaTimer: ReturnType<typeof setTimeout> | null = null
watch(busca, () => {
  if (buscaTimer) clearTimeout(buscaTimer)
  buscaTimer = setTimeout(() => void loadOcorrencias(), 300)
})
watch([filtroRobo, filtroPlataforma, filtroConta, filtroStatus, filtroPessoa, filtroDias], () => {
  void loadOcorrencias()
})

// ── Atualização automática (60 s) ───────────────────────────────────────────
// O worker grava rodadas e ocorrências o dia inteiro; sem isso cada navegador
// ficava com a foto de quando abriu. Pula com a aba do navegador escondida e
// enquanto há ação em voo ou edição aberta, pra não atropelar a pessoa.
const AUTO_REFRESH_MS = 60_000
let autoRefreshTimer: ReturnType<typeof setInterval> | null = null
function autoRefreshTick() {
  if (typeof document !== 'undefined' && document.visibilityState !== 'visible') return
  if (editando.value) return
  if (acoesEmVoo.value.size || mudandoModo.value.size || rodando.value.size) return
  void refreshTudo()
}
onMounted(() => {
  void refreshTudo()
  autoRefreshTimer = setInterval(autoRefreshTick, AUTO_REFRESH_MS)
})
onBeforeUnmount(() => {
  if (autoRefreshTimer) clearInterval(autoRefreshTimer)
  autoRefreshTimer = null
  if (buscaTimer) clearTimeout(buscaTimer)
})

// ── Aba Robôs ───────────────────────────────────────────────────────────────
const robosPorChave = computed<Record<string, Robo>>(() =>
  Object.fromEntries(robos.value.map((r) => [r.chave, r])),
)
const areas = computed(() =>
  [...new Set(robos.value.map((r) => (r.area || '').trim()).filter(Boolean))].sort((a, b) => a.localeCompare(b, 'pt-BR')),
)
const temProblema = (r: Robo) => r.saude === 'falhando' || r.saude === 'parado'

const arquivado = (r: Robo) => !!r.arquivado_em

const robosFiltrados = computed(() => {
  const q = robosBusca.value.trim().toLowerCase()
  return robos.value.filter((r) => {
    if (arquivado(r) && !mostrarArquivados.value) return false
    if (q && !`${r.nome} ${r.descricao || ''} ${r.chave}`.toLowerCase().includes(q)) return false
    if (robosArea.value && (r.area || '') !== robosArea.value) return false
    if (robosEstado.value && r.modo !== robosEstado.value) return false
    if (robosSoProblema.value && !temProblema(r)) return false
    return true
  })
})

const stRobos = computed(() => {
  // Os cartões contam o painel que está à vista; arquivado sai da conta (mas
  // se um deles estiver falhando o atalho avisa — some da vista, não do
  // trabalho).
  const lista = robos.value.filter((r) => !arquivado(r))
  const fora = robos.value.filter(arquivado)
  return {
    arquivados: fora.length,
    arquivadosComProblema: fora.filter(temProblema).length,
    total: lista.length,
    ligados: lista.filter((r) => r.modo === 'ligado').length,
    silenciosos: lista.filter((r) => r.modo === 'silencioso').length,
    desligados: lista.filter((r) => r.modo === 'desligado').length,
    comProblema: lista.filter(temProblema).length,
    rodadasHoje: lista.reduce((acc, r) => acc + (r.rodadas_hoje || 0), 0),
    rodadasHojeOk: lista.reduce((acc, r) => acc + (r.rodadas_hoje_ok || 0), 0),
    abertas: lista.reduce((acc, r) => acc + (r.abertas || 0), 0),
  }
})
// "99,3% ok" embaixo de Rodadas hoje; sem rodada ainda, só o texto.
const rodadasHojeHint = computed(() => {
  const { rodadasHoje, rodadasHojeOk } = stRobos.value
  if (!rodadasHoje) return 'somando todos os robôs'
  const pct = (rodadasHojeOk / rodadasHoje) * 100
  return `${pct.toLocaleString('pt-BR', { maximumFractionDigits: 1 })}% ok`
})

// Saúde: bolinha + rótulo. 'parado' ganha o "há 18 h" pra pessoa saber o
// tamanho do buraco sem abrir a linha.
const SAUDE: Record<Saude, { label: string; dot: string; cls: string }> = {
  ok: { label: 'ok', dot: 'bg-emerald-500', cls: '' },
  falhando: { label: 'falhando', dot: 'bg-red-500', cls: 'text-red-600 dark:text-red-400 font-medium' },
  parado: { label: 'parado', dot: 'bg-red-500', cls: 'text-red-600 dark:text-red-400 font-medium' },
  desligado: { label: 'desligado', dot: 'bg-gray-300 dark:bg-gray-600', cls: 'text-muted-foreground' },
}
function saudeDe(r: Robo): { label: string; dot: string; cls: string; title: string } {
  const base = SAUDE[r.saude] ?? SAUDE.ok
  if (r.saude === 'parado') {
    const ha = fmtHa(r.ultima_rodada_em)
    return {
      ...base,
      label: ha ? `parado ${ha}` : 'parado',
      title: r.ultima_rodada_em
        ? `Ligado, mas sem rodada desde ${fmtRel(r.ultima_rodada_em)} — mais de 3× a cadência esperada. O worker está de pé?`
        : 'Ligado, mas nunca rodou aqui. O worker está de pé?',
    }
  }
  if (r.saude === 'falhando') {
    return { ...base, title: r.ultima_falha_erro ? `Última falha ${fmtRel(r.ultima_falha_em)}: ${r.ultima_falha_erro}` : 'A última rodada falhou' }
  }
  if (r.saude === 'desligado') return { ...base, title: 'Desligado — não roda sozinho (só pelo "Rodar agora")' }
  return { ...base, title: 'Rodando na cadência esperada e a última rodada terminou bem' }
}

// Quem o robô avisa: a API já resolve os IDs (override da tela, variável do
// robô ou fallback geral) e traz o nome de cada um.
const ORIGEM_THREEMA: Record<string, string> = {
  robo: 'destinatários deste robô',
  env: 'variável do robô no .env',
  geral: 'padrão geral do sistema',
}
function nomesDestinatarios(r: Robo): string {
  return (r.threema_destinatarios || []).map((d) => d.nome || d.id).join(', ')
}
function avisaDe(r: Robo): { linha1: string; linha2: string; title: string } {
  if (r.modo === 'desligado') return { linha1: '—', linha2: '', title: '' }
  if (r.modo === 'silencioso') return { linha1: 'Ninguém (silencioso)', linha2: 'registra, não avisa', title: '' }
  const n = (r.threema_destinatarios || []).length
  if (!n) return { linha1: 'Ninguém', linha2: 'sem destinatário no Threema', title: 'Nenhum destinatário configurado — nem no robô, nem no .env. Abra a linha e clique em Editar.' }
  return {
    linha1: `Threema · ${n} ${n === 1 ? 'pessoa' : 'pessoas'}`,
    linha2: `re-aviso ${r.reaviso_horas} h`,
    title: `${nomesDestinatarios(r)} (${ORIGEM_THREEMA[r.threema_origem || ''] || 'origem desconhecida'})`,
  }
}

// ── Linha expandida: detalhe, contas com problema, últimas rodadas ─────────
const expandidos = ref<Set<string>>(new Set())
const detalhes = ref<Record<string, RoboDetalhe>>({})
const detalheLoading = ref<Set<string>>(new Set())
const rodadasTodas = ref<Set<string>>(new Set())

function setEmVoo(alvo: typeof detalheLoading, chave: string, v: boolean) {
  const next = new Set(alvo.value)
  if (v) next.add(chave); else next.delete(chave)
  alvo.value = next
}

async function loadDetalhe(chave: string) {
  setEmVoo(detalheLoading, chave, true)
  try {
    const det = await api<RoboDetalhe>(`/api/ouvidoria/robos/${encodeURIComponent(chave)}`)
    detalhes.value = { ...detalhes.value, [chave]: det }
  } catch (e: any) {
    toasts.error('Não deu pra abrir o detalhe do robô', apiError(e))
  } finally {
    setEmVoo(detalheLoading, chave, false)
  }
}

function toggleExpandir(chave: string) {
  const next = new Set(expandidos.value)
  if (next.has(chave)) {
    next.delete(chave)
    if (editando.value === chave) editando.value = null
  } else {
    next.add(chave)
    if (!detalhes.value[chave]) void loadDetalhe(chave)
  }
  expandidos.value = next
}

function contasProblema(chave: string): ContaProblema[] {
  return detalhes.value[chave]?.contas ?? []
}
// Erro da API que derrubou a conta (dados.erro), senão o título da ocorrência.
function erroDaConta(c: ContaProblema): string {
  const erro = c.dados?.erro
  const texto = typeof erro === 'string' && erro ? erro : c.titulo
  return `${texto} · aberta ${fmtRel(c.aberta_em)}`
}
// Plataformas cobertas: as do cadastro do robô + qualquer uma que apareça nas
// contas com problema (não deixa problema escondido por cadastro velho).
function plataformasCobertas(r: Robo): string[] {
  const lista = [...(r.plataformas || [])]
  for (const c of contasProblema(r.chave)) {
    const p = (c.plataforma || '').toLowerCase()
    if (p && !lista.includes(p)) lista.push(p)
  }
  return lista
}
function contasProblemaDa(chave: string, plataforma: string): ContaProblema[] {
  return contasProblema(chave).filter((c) => (c.plataforma || '').toLowerCase() === plataforma.toLowerCase())
}
// Quantas contas a última rodada conferiu, se o robô contou (contadores.contas).
function contasNaRodada(chave: string): number | null {
  const r = detalhes.value[chave]?.rodadas?.[0]
  const n = r?.contadores?.contas
  return typeof n === 'number' ? n : null
}
function rodadasVisiveis(chave: string): Rodada[] {
  const lista = detalhes.value[chave]?.rodadas ?? []
  return rodadasTodas.value.has(chave) ? lista : lista.slice(0, 8)
}
function toggleRodadasTodas(chave: string) {
  const next = new Set(rodadasTodas.value)
  if (next.has(chave)) next.delete(chave); else next.add(chave)
  rodadasTodas.value = next
}
// Contadores da rodada em texto miúdo, quando o robô não escreveu resumo.
function contadoresTexto(c: Record<string, unknown> | null | undefined): string {
  if (!c) return ''
  return Object.entries(c)
    .filter(([, v]) => typeof v === 'number' || typeof v === 'string')
    .map(([k, v]) => `${k.replace(/_/g, ' ')} ${v}`)
    .join(' · ')
}

// Configuração em linguagem de operação. O rótulo (com a unidade) vem do
// backend em `config_rotulos` — são os mesmos `Parametro` que já governam os
// limites do PATCH —, então robô novo aparece rotulado sem mexer na tela.
// Chave sem rótulo aparece crua, pra nada ficar escondido.
function configLabel(r: Robo, k: string): string {
  return (r.config_rotulos || {})[k] ?? k.replace(/_/g, ' ')
}
function configLinhas(r: Robo): { chave: string; label: string; valor: string }[] {
  return Object.entries(r.config || {}).map(([k, v]) => ({
    chave: k,
    label: configLabel(r, k),
    valor: v !== null && typeof v === 'object' ? JSON.stringify(v) : String(v ?? '—'),
  }))
}

// ── Editar configuração (PATCH /robos/{chave}) ──────────────────────────────
// Só re-aviso, destinatários e as chaves simples do config. Objetos/listas
// dentro do config ficam como estão (mandados de volta sem mexer).
type EditForm = {
  reaviso_horas: number
  // IDs marcados no seletor de pessoas (a tela mostra o nome; o ID vai por baixo).
  threema_ids: string[]
  config: Record<string, string | number | boolean>
}
const editando = ref<string | null>(null)
const editForm = ref<EditForm>({ reaviso_horas: 24, threema_ids: [], config: {} })
const salvandoEdicao = ref(false)

// Quem pode receber aviso (usuários ativos com Threema em Admin › Usuários +
// apelidos do .env). Carregado ao abrir a edição — é o mesmo seletor do
// botão Informar: caixinha por pessoa, nunca ID digitado à mão (21/09:
// Vinicius digitou "cairo sa" no campo de texto e a API recusou).
type Destinatario = { id: string; nome: string }
const diretorio = ref<Destinatario[]>([])
const diretorioCarregado = ref(false)
const seletorAberto = ref(false)
async function loadDiretorio() {
  try {
    diretorio.value = await api<Destinatario[]>('/api/ouvidoria/threema/destinatarios')
    diretorioCarregado.value = true
  } catch (e: any) {
    toasts.error('Não deu pra carregar as pessoas do Threema', apiError(e))
  }
}
function nomeDe(id: string): string {
  return diretorio.value.find((d) => d.id === id)?.nome || id
}
function pessoaMarcada(id: string): boolean {
  return editForm.value.threema_ids.includes(id)
}
function alternarPessoa(id: string) {
  const ids = editForm.value.threema_ids
  editForm.value = {
    ...editForm.value,
    threema_ids: ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id],
  }
}
// Quem o robô avisa HOJE sem override (variável do robô / padrão geral):
// pré-marcados ao abrir, pra "adicionar o cairo" ser marcar uma caixinha, não
// remontar a lista. Salvar sem mudar nada mantém o fallback (manda "").
function idsPadrao(r: Robo): string[] {
  return r.threema_origem === 'robo' ? [] : (r.threema_destinatarios || []).map((d) => d.id)
}
function mesmaLista(a: string[], b: string[]): boolean {
  return a.length === b.length && [...a].sort().every((v, i) => v === [...b].sort()[i])
}

function abrirEdicao(r: Robo) {
  const config: EditForm['config'] = {}
  for (const [k, v] of Object.entries(r.config || {})) {
    if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') config[k] = v
  }
  editForm.value = {
    reaviso_horas: r.reaviso_horas,
    threema_ids: (r.threema_destinatarios || []).map((d) => d.id),
    config,
  }
  seletorAberto.value = false
  editando.value = r.chave
  if (!diretorioCarregado.value) void loadDiretorio()
}
function tipoCampo(v: string | number | boolean): 'number' | 'checkbox' | 'text' {
  if (typeof v === 'number') return 'number'
  if (typeof v === 'boolean') return 'checkbox'
  return 'text'
}
function setConfig(k: string, v: string | number | boolean) {
  editForm.value = { ...editForm.value, config: { ...editForm.value.config, [k]: v } }
}
async function salvarEdicao(r: Robo) {
  const reaviso = Number(editForm.value.reaviso_horas)
  if (!Number.isFinite(reaviso) || reaviso < 1) {
    toasts.warning('Re-aviso precisa ser um número de horas (mínimo 1)')
    return
  }
  salvandoEdicao.value = true
  try {
    await api(`/api/ouvidoria/robos/${encodeURIComponent(r.chave)}`, {
      method: 'PATCH',
      body: {
        reaviso_horas: reaviso,
        // Lista igual ao fallback (.env) → manda "" e o robô continua seguindo
        // o padrão do sistema; diferente → vira override deste robô.
        threema_recipients: mesmaLista(editForm.value.threema_ids, idsPadrao(r))
          ? ''
          : editForm.value.threema_ids.join(', '),
        // A API substitui a config inteira — manda a efetiva com as
        // alterações por cima (objetos/listas seguem intactos).
        config: { ...(r.config || {}), ...editForm.value.config },
      },
    })
    toasts.success('Configuração salva', r.nome)
    editando.value = null
    await Promise.all([loadRobos(), loadDetalhe(r.chave)])
  } catch (e: any) {
    toasts.error('Não deu pra salvar', apiError(e))
  } finally {
    salvandoEdicao.value = false
  }
}

// ── Modo e "Rodar agora" ────────────────────────────────────────────────────
const mudandoModo = ref<Set<string>>(new Set())
const rodando = ref<Set<string>>(new Set())

async function mudarModo(r: Robo, modo: ModoRobo) {
  if (modo === r.modo) return
  setEmVoo(mudandoModo, r.chave, true)
  try {
    await api(`/api/ouvidoria/robos/${encodeURIComponent(r.chave)}`, { method: 'PATCH', body: { modo } })
    const frase = modo === 'ligado' ? 'ligado' : modo === 'silencioso' ? 'em modo silencioso' : 'desligado'
    toasts.success(`${r.nome} ${frase}`)
    await loadRobos()
    if (expandidos.value.has(r.chave)) void loadDetalhe(r.chave)
  } catch (e: any) {
    toasts.error('Não deu pra mudar o modo', apiError(e))
  } finally {
    setEmVoo(mudandoModo, r.chave, false)
  }
}

// Lixeira da linha: tira o robô da LISTA do painel. Vinicius, 22/09/2026:
// "não para excluir o robô definitivamente" — a linha continua no banco, o
// modo não muda e o robô segue rodando e avisando igual. Volta pelo atalho
// "N arquivados" em cima da tabela.
const arquivando = ref<Set<string>>(new Set())

async function arquivarRobo(r: Robo, arquivar: boolean) {
  if (arquivar && !confirm(
    `Tirar "${r.nome}" do painel?\n\n` +
      'Ele NÃO é excluído e continua rodando e avisando igual — só some desta lista. ' +
      'Para trazer de volta, use o atalho "arquivados" em cima da tabela.',
  )) return
  setEmVoo(arquivando, r.chave, true)
  try {
    await api(`/api/ouvidoria/robos/${encodeURIComponent(r.chave)}`, {
      method: 'PATCH',
      body: { arquivado: arquivar },
    })
    toasts.success(
      arquivar ? `${r.nome} saiu do painel` : `${r.nome} voltou pro painel`,
      arquivar ? 'continua rodando igual; volta pelo atalho "arquivados"' : '',
    )
    await loadRobos()
  } catch (e: any) {
    toasts.error(arquivar ? 'Não deu pra tirar do painel' : 'Não deu pra trazer de volta', apiError(e))
  } finally {
    setEmVoo(arquivando, r.chave, false)
  }
}

async function rodarAgora(r: Robo) {
  setEmVoo(rodando, r.chave, true)
  try {
    await api(`/api/ouvidoria/robos/${encodeURIComponent(r.chave)}/rodar`, { method: 'POST' })
    toasts.success('Rodada agendada', `${r.nome} roda em segundo plano — o resultado aparece aqui em instantes`)
    // A rodada leva alguns segundos; repuxa duas vezes pra mostrar o
    // resultado sem esperar o próximo auto-refresh.
    setTimeout(() => void refreshTudo(), 6_000)
    setTimeout(() => void refreshTudo(), 20_000)
  } catch (e: any) {
    toasts.error('Não deu pra agendar a rodada', apiError(e))
  } finally {
    setEmVoo(rodando, r.chave, false)
  }
}

// ── Aba Ocorrências ─────────────────────────────────────────────────────────
// Chips por robô: todos os robôs do catálogo, com a contagem de abertas que
// veio em `por_robo` (0 quando o robô não tem nada aberto). Os com abertas
// primeiro.
const chipsRobo = computed(() => {
  const contagem = new Map(porRobo.value.map((p) => [p.chave, p]))
  const lista = robos.value.map((r) => ({ chave: r.chave, nome: r.nome, abertas: contagem.get(r.chave)?.abertas ?? 0 }))
  for (const p of porRobo.value) if (!robosPorChave.value[p.chave]) lista.push({ chave: p.chave, nome: p.nome, abertas: p.abertas })
  return lista.sort((a, b) => (b.abertas - a.abertas) || a.nome.localeCompare(b.nome, 'pt-BR'))
})
const opcoesPlataforma = computed(() => {
  const vistas = new Set<string>()
  for (const r of robos.value) for (const p of r.plataformas || []) vistas.add(p.toLowerCase())
  for (const o of ocorrencias.value) if (o.plataforma) vistas.add(o.plataforma.toLowerCase())
  if (filtroPlataforma.value) vistas.add(filtroPlataforma.value)
  const ordem = Object.keys(PLATAFORMAS)
  return [...vistas].sort((a, b) => {
    const ia = ordem.indexOf(a); const ib = ordem.indexOf(b)
    return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib) || a.localeCompare(b)
  }).map((cod) => ({ cod, nome: plataformaInfo(cod).nome }))
})
const opcoesConta = computed(() => [...contasConhecidas.value].sort((a, b) => a.localeCompare(b, 'pt-BR')))
const temFiltroOc = computed(() =>
  Boolean(busca.value.trim() || filtroRobo.value || filtroPlataforma.value || filtroConta.value || filtroPessoa.value || filtroStatus.value !== 'abertas'),
)
function limparFiltrosOc() {
  busca.value = ''
  filtroRobo.value = ''
  filtroPlataforma.value = ''
  filtroConta.value = ''
  filtroPessoa.value = false
  filtroStatus.value = 'abertas'
  filtroDias.value = 7
}

function roboNome(o: Ocorrencia): string {
  return o.robo_nome || robosPorChave.value[o.robo_chave]?.nome || o.robo_chave
}
function roboCadencia(chave: string): string {
  return robosPorChave.value[chave]?.cadencia_texto ?? ''
}

// Status da ocorrência: aberta (por severidade) ou o tipo de fechamento. A
// segunda linha ("quem") diz quem fechou / quando avisou.
const SEV_LABEL: Record<Severidade, string> = {
  urgente: 'Aberta · urgente',
  pessoa: 'Aberta · pessoa',
  baixa: 'Aberta · baixa',
  robo_segurou: 'Aberta · robô segurou',
  info: 'Aberta · info',
}
const CLS_RED = 'bg-red-500/15 text-red-700 dark:text-red-300'
const CLS_AMBER = 'bg-amber-500/15 text-amber-700 dark:text-amber-300'
const CLS_BLUE = 'bg-sky-500/15 text-sky-700 dark:text-sky-300'
const CLS_GREEN = 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300'
const CLS_GRAY = 'bg-muted text-muted-foreground'
const SEV_CLS: Record<Severidade, string> = {
  urgente: CLS_RED, pessoa: CLS_RED, baixa: CLS_AMBER, robo_segurou: CLS_BLUE, info: CLS_GRAY,
}
function statusDe(o: Ocorrencia): { label: string; cls: string; quem: string; title: string } {
  if (!o.fechada_em) {
    const partes: string[] = []
    if (o.avisada_em) partes.push(`avisada ${fmtRel(o.avisada_em)}`)
    if (o.reavisada_em) partes.push(`re-aviso ${fmtRel(o.reavisada_em)}`)
    return {
      label: SEV_LABEL[o.severidade] ?? `Aberta · ${o.severidade}`,
      cls: SEV_CLS[o.severidade] ?? CLS_RED,
      quem: partes.join(' · '),
      title: o.precisa_pessoa ? 'Alguém precisa agir' : 'Só registro — ninguém precisa agir',
    }
  }
  if (o.fechamento === 'sumiu') {
    return { label: 'Fechou sozinha', cls: CLS_GREEN, quem: `sumiu ${fmtRel(o.fechada_em)}`, title: 'O robô parou de ver o problema e fechou sozinho' }
  }
  if (o.fechamento === 'ignorada') {
    return { label: 'Ignorada', cls: CLS_GRAY, quem: `${o.fechada_por || '—'} · ${fmtRel(o.fechada_em)}`, title: 'Ignorada por uma pessoa — o robô não reabre' }
  }
  return { label: 'Tratada', cls: CLS_GRAY, quem: `${o.fechada_por || '—'} · ${fmtRel(o.fechada_em)}`, title: 'Marcada como tratada por uma pessoa' }
}

// Botão do link: rótulo pelo destino, pra pessoa saber onde vai cair.
function linkInfo(link: string | null): { label: string; interno: boolean } | null {
  if (!link) return null
  const l = link.toLowerCase()
  if (l.startsWith('/')) {
    if (l.startsWith('/integrations')) return { label: 'Integrações', interno: true }
    if (l.startsWith('/devolucoes')) return { label: 'Devoluções', interno: true }
    if (l.startsWith('/chamados')) return { label: 'Chamados', interno: true }
    if (l.startsWith('/margem')) return { label: 'Margem', interno: true }
    if (l.startsWith('/logistica')) return { label: 'Logística', interno: true }
    return { label: 'Abrir', interno: true }
  }
  if (l.includes('bling.com')) return { label: 'Abrir no Bling', interno: false }
  if (l.includes('mercadolivre') || l.includes('mercadolibre')) return { label: 'Mercado Livre', interno: false }
  if (l.includes('shopee')) return { label: 'Shopee', interno: false }
  if (l.includes('tiktok')) return { label: 'TikTok', interno: false }
  if (l.includes('amazon')) return { label: 'Amazon', interno: false }
  return { label: 'Abrir', interno: false }
}

// Extras (`dados`) numa linha miúda do balão: valor, SKU, status na plataforma…
function dadosResumo(o: Ocorrencia): string {
  const d = o.dados || {}
  const partes: string[] = []
  const valor = d.valor
  if (typeof valor === 'number') partes.push(valor.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' }))
  if (typeof d.sku === 'string' && d.sku) partes.push(`SKU ${d.sku}`)
  if (typeof d.status_plataforma === 'string' && d.status_plataforma) partes.push(`status ${d.status_plataforma}`)
  if (typeof d.pago_em === 'string' && d.pago_em) partes.push(`pago ${fmtRel(d.pago_em)}`)
  if (typeof d.pack_id === 'string' || typeof d.pack_id === 'number') partes.push(`pack ${d.pack_id}`)
  return partes.join(' · ')
}

// Tratar / ignorar / reabrir. Ignorar pede confirmação: a ocorrência some e o
// robô NÃO reabre pra essa chave enquanto ela estiver ignorada.
const acoesEmVoo = ref<Set<string>>(new Set())
const MSG_ACAO = {
  tratar: 'Marcada como tratada',
  ignorar: 'Ignorada — o robô não abre de novo pra esse caso',
  reabrir: 'Reaberta',
} as const
async function agir(o: Ocorrencia, tipo: keyof typeof MSG_ACAO) {
  if (
    tipo === 'ignorar'
    && !confirm(`Ignorar "${o.titulo}"?\n\nO robô não vai abrir de novo pra ${o.pedido || o.conta || 'esse caso'} enquanto estiver ignorada. Dá pra reabrir depois pela lista de fechadas.`)
  ) return
  setEmVoo(acoesEmVoo, o.id, true)
  try {
    await api(`/api/ouvidoria/ocorrencias/${encodeURIComponent(o.id)}/${tipo}`, { method: 'POST' })
    toasts.success(MSG_ACAO[tipo], o.pedido ? `Pedido ${o.pedido}` : (o.conta || ''))
    await Promise.all([loadOcorrencias(), loadRobos()])
  } catch (e: any) {
    toasts.error('Não deu', apiError(e))
  } finally {
    setEmVoo(acoesEmVoo, o.id, false)
  }
}

const descricaoAba = computed(() => tab.value === 'robos'
  ? 'Cada robô vigia uma coisa. Aqui você liga/desliga, vê se ele está rodando de verdade, o que a última rodada encontrou e quem ele avisa.'
  : 'O que os robôs encontraram e ainda ninguém tratou. Cada linha é uma ocorrência: o robô abre, re-vê a cada rodada e fecha sozinho quando o problema some — ou você marca como tratada.')
const carregandoAba = computed(() => (tab.value === 'robos' ? robosLoading.value : ocLoading.value))
</script>

<template>
  <div class="space-y-4">
    <PageHeader title="Robôs" :description="descricaoAba">
      <template #actions>
        <Button size="sm" variant="outline" :disabled="carregandoAba" @click="refreshTudo">
          <RotateCcw class="size-4 mr-1.5" :class="{ 'animate-spin': carregandoAba }" />
          atualizar
        </Button>
      </template>
    </PageHeader>

    <!-- Abas -->
    <div class="flex items-center gap-1 border-b border-border">
      <button
        class="px-3 h-9 text-sm font-medium border-b-2 -mb-px transition-colors inline-flex items-center gap-1.5"
        :class="tab === 'robos' ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'"
        @click="tab = 'robos'"
      >
        Robôs
        <span class="text-[11px] font-medium text-muted-foreground tabular-nums">{{ robos.length }}</span>
      </button>
      <button
        class="px-3 h-9 text-sm font-medium border-b-2 -mb-px transition-colors inline-flex items-center gap-1.5"
        :class="tab === 'ocorrencias' ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'"
        @click="tab = 'ocorrencias'"
      >
        Ocorrências
        <span
          v-if="resumo.abertas > 0"
          class="rounded-full px-1.5 text-[11px] font-semibold tabular-nums"
          :class="CLS_RED"
          title="ocorrências abertas"
        >{{ resumo.abertas }}</span>
      </button>
    </div>

    <!-- ══ Aba Robôs ══ -->
    <template v-if="tab === 'robos'">
      <div v-if="robosError" class="flex items-center gap-2 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-400">
        <AlertCircle class="size-4" />
        {{ robosError }}
      </div>

      <div class="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-2">
        <StatCard label="Ligados" :value="stRobos.ligados" :hint="`de ${stRobos.total}`" :icon="Radar" tone="success" compact />
        <StatCard label="Silenciosos" :value="stRobos.silenciosos" hint="registram, não avisam" :icon="Bot" tone="warning" compact />
        <StatCard label="Desligados" :value="stRobos.desligados" hint="só rodam pelo Rodar agora" :icon="ShieldOff" compact />
        <StatCard label="Com problema" :value="stRobos.comProblema" hint="falhando ou parado" :icon="AlertTriangle" tone="danger" compact />
        <StatCard label="Rodadas hoje" :value="fmtNum(stRobos.rodadasHoje)" :hint="rodadasHojeHint" :icon="Play" compact />
        <StatCard label="Ocorrências abertas" :value="stRobos.abertas" hint="ver aba Ocorrências" :icon="AlertCircle" tone="danger" compact />
      </div>

      <div class="flex flex-wrap items-center gap-2">
        <div class="relative">
          <Search class="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
          <input
            v-model="robosBusca"
            class="h-9 w-64 rounded-md border bg-background pl-8 pr-3 text-sm"
            placeholder="nome do robô…"
          />
        </div>
        <select v-model="robosArea" class="h-9 rounded-md border bg-background px-2 text-sm" title="Área que o robô vigia">
          <option value="">Área: todas</option>
          <option v-for="a in areas" :key="a" :value="a">{{ a }}</option>
        </select>
        <select v-model="robosEstado" class="h-9 rounded-md border bg-background px-2 text-sm" title="Modo do robô">
          <option value="">Estado: todos</option>
          <option value="ligado">ligado</option>
          <option value="silencioso">silencioso</option>
          <option value="desligado">desligado</option>
        </select>
        <label class="inline-flex items-center gap-1.5 h-9 rounded-md border bg-background px-2 text-sm cursor-pointer" title="Só robôs falhando ou parados">
          <input v-model="robosSoProblema" type="checkbox" class="size-3.5" />
          Só com problema
        </label>
        <button
          v-if="stRobos.arquivados"
          type="button"
          class="inline-flex items-center gap-1.5 h-9 rounded-md border px-2 text-sm"
          :class="[
            mostrarArquivados ? 'bg-muted' : 'bg-background hover:bg-muted/60',
            stRobos.arquivadosComProblema ? 'border-red-500/50 text-red-500' : 'text-muted-foreground',
          ]"
          :title="stRobos.arquivadosComProblema
            ? `${stRobos.arquivadosComProblema} robô(s) fora do painel estão falhando ou parados — arquivar não desliga`
            : 'Robôs tirados do painel. Eles continuam rodando; clique para ver e trazer de volta'"
          @click="mostrarArquivados = !mostrarArquivados"
        >
          <Trash2 class="size-3.5" />
          {{ stRobos.arquivados }} fora do painel
          <AlertTriangle v-if="stRobos.arquivadosComProblema" class="size-3.5" />
        </button>
        <span class="ml-auto text-xs text-muted-foreground">
          {{ stRobos.total }} {{ stRobos.total === 1 ? 'robô' : 'robôs' }} · {{ robosFiltrados.length }} {{ robosFiltrados.length === 1 ? 'mostrado' : 'mostrados' }}
        </span>
      </div>

      <div class="overflow-auto rounded border max-h-[75vh]">
        <table class="w-full min-w-[1180px] text-xs border-collapse">
          <thead class="sticky top-0 z-20 bg-background">
            <tr class="border-b">
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap w-[84px]">Ligado</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap">Robô</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap w-[170px]">Plataformas</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap w-[120px]">Última rodada</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap w-[230px]">Resultado da última rodada</th>
              <th class="px-2 py-1 text-center font-semibold text-[11px] text-muted-foreground whitespace-nowrap w-[70px]" title="Ocorrências abertas deste robô">Abertas</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap w-[150px]">Avisa</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap w-[130px]">Saúde</th>
              <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap w-[130px]"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="robosLoading && !robos.length">
              <td colspan="9" class="py-8 text-center text-muted-foreground">
                <Loader2 class="size-4 inline animate-spin mr-1.5" />
                carregando…
              </td>
            </tr>
            <tr v-else-if="!robosFiltrados.length">
              <td colspan="9" class="py-8 text-center text-muted-foreground">
                <template v-if="!robos.length && robosCarregou">Nenhum robô cadastrado ainda — o worker cadastra o catálogo ao subir.</template>
                <template v-else-if="robos.length">Nenhum robô com esses filtros.</template>
              </td>
            </tr>
            <template v-for="r in robosFiltrados" :key="r.chave">
              <tr
                class="border-t align-top hover:brightness-95 dark:hover:brightness-110"
                :class="[
                  temProblema(r) ? 'bg-amber-50/40 dark:bg-amber-900/10' : '',
                  r.modo === 'desligado' ? 'text-muted-foreground' : '',
                ]"
              >
                <td class="px-2 py-1.5">
                  <OuvidoriaModoToggle
                    v-if="canEdit"
                    :model-value="r.modo"
                    :nome="r.nome"
                    :busy="mudandoModo.has(r.chave)"
                    @change="(m) => mudarModo(r, m)"
                  />
                  <span v-else class="text-[11px] text-muted-foreground">{{ r.modo }}</span>
                </td>
                <td class="px-2 py-1.5">
                  <div class="font-semibold flex items-center gap-1.5" :class="r.modo === 'desligado' ? 'text-muted-foreground' : ''">
                    {{ r.nome }}
                    <span
                      v-if="arquivado(r)"
                      class="inline-flex items-center gap-1 rounded border px-1 py-px text-[10px] font-normal text-muted-foreground"
                      :title="`Fora do painel desde ${fmtRel(r.arquivado_em)}${r.arquivado_por ? ', por ' + r.arquivado_por : ''} — continua rodando`"
                    >
                      <Trash2 class="size-2.5" />
                      fora do painel
                    </span>
                  </div>
                  <div v-if="r.cadencia_texto" class="text-[11px] text-muted-foreground">{{ r.cadencia_texto }}</div>
                  <div v-if="r.descricao" class="text-[11.5px] leading-snug text-muted-foreground mt-0.5">{{ r.descricao }}</div>
                </td>
                <td class="px-2 py-1.5">
                  <div class="flex flex-wrap gap-1">
                    <OuvidoriaPlataforma v-for="p in r.plataformas || []" :key="p" :codigo="p" curto caixa />
                    <span v-if="!(r.plataformas || []).length" class="text-muted-foreground">—</span>
                  </div>
                </td>
                <td class="px-2 py-1.5 whitespace-nowrap">
                  <div>{{ fmtRel(r.ultima_rodada_em) }}</div>
                  <div class="text-[11px] text-muted-foreground">
                    <template v-if="r.modo === 'desligado' && r.modo_alterado_por">desligado por {{ r.modo_alterado_por }}</template>
                    <template v-else-if="!r.ultima_rodada_em">nunca rodou aqui</template>
                    <template v-else-if="r.ultima_rodada_ok === false">falhou</template>
                    <template v-else>{{ fmtDuracao(r.ultima_rodada_duracao_ms) }}</template>
                  </div>
                </td>
                <td class="px-2 py-1.5 leading-snug">
                  <template v-if="r.modo === 'desligado' && !r.ultima_rodada_resumo">—</template>
                  <span
                    v-else-if="r.ultima_rodada_ok === false"
                    class="text-red-600 dark:text-red-400 line-clamp-2"
                    :title="r.ultima_falha_erro || ''"
                  >{{ r.ultima_falha_erro || 'falhou' }}</span>
                  <span v-else :title="r.ultima_rodada_resumo || ''">{{ r.ultima_rodada_resumo || '—' }}</span>
                </td>
                <td class="px-2 py-1.5 text-center">
                  <span
                    class="inline-flex min-w-[26px] justify-center rounded-full px-1.5 py-0.5 text-[11px] font-semibold tabular-nums"
                    :class="r.abertas > 0 ? (r.abertas_pessoa > 0 ? CLS_RED : CLS_AMBER) : CLS_GRAY"
                    :title="r.abertas_pessoa ? `${r.abertas_pessoa} precisam de pessoa` : ''"
                  >{{ r.abertas }}</span>
                </td>
                <td class="px-2 py-1.5 leading-snug" :class="r.modo === 'silencioso' ? 'text-muted-foreground' : ''" :title="avisaDe(r).title">
                  <div>{{ avisaDe(r).linha1 }}</div>
                  <div v-if="avisaDe(r).linha2" class="text-[11px] text-muted-foreground">{{ avisaDe(r).linha2 }}</div>
                </td>
                <td class="px-2 py-1.5 whitespace-nowrap">
                  <span class="inline-flex items-center gap-1.5" :class="saudeDe(r).cls" :title="saudeDe(r).title">
                    <i class="inline-block size-2 rounded-full" :class="saudeDe(r).dot" />
                    {{ saudeDe(r).label }}
                  </span>
                </td>
                <td class="px-2 py-1.5 text-right whitespace-nowrap">
                  <div class="inline-flex items-center gap-1">
                    <button
                      v-if="canEdit"
                      type="button"
                      class="inline-flex h-7 items-center gap-1 rounded-md border bg-background px-2 text-[11.5px] font-medium hover:bg-muted disabled:opacity-50"
                      :disabled="rodando.has(r.chave)"
                      :title="r.modo === 'desligado' ? 'Roda UMA vez agora, mesmo desligado (o modo só governa a rodada automática)' : 'Agenda uma rodada agora, fora da cadência'"
                      @click="rodarAgora(r)"
                    >
                      <Loader2 v-if="rodando.has(r.chave)" class="size-3 animate-spin" />
                      <Play v-else class="size-3" />
                      Rodar agora
                    </button>
                    <!-- Lixeira: tira da lista do painel. Não exclui o robô e
                         não mexe no modo — ele continua rodando e avisando. -->
                    <button
                      v-if="canEdit"
                      type="button"
                      class="size-7 grid place-items-center rounded-md hover:bg-muted text-muted-foreground disabled:opacity-50"
                      :disabled="arquivando.has(r.chave)"
                      :title="arquivado(r)
                        ? `Trazer de volta pro painel (fora desde ${fmtRel(r.arquivado_em)}${r.arquivado_por ? ', por ' + r.arquivado_por : ''})`
                        : 'Tirar do painel — não exclui o robô nem desliga; ele continua rodando'"
                      @click="arquivarRobo(r, !arquivado(r))"
                    >
                      <Loader2 v-if="arquivando.has(r.chave)" class="size-3.5 animate-spin" />
                      <ArchiveRestore v-else-if="arquivado(r)" class="size-3.5" />
                      <Trash2 v-else class="size-3.5" />
                    </button>
                    <button
                      type="button"
                      class="size-7 grid place-items-center rounded-md hover:bg-muted text-muted-foreground"
                      :title="expandidos.has(r.chave) ? 'fechar detalhe' : 'configuração, contas e últimas rodadas'"
                      @click="toggleExpandir(r.chave)"
                    >
                      <component :is="expandidos.has(r.chave) ? ChevronDown : ChevronRight" class="size-4" />
                    </button>
                  </div>
                </td>
              </tr>

              <!-- Linha expandida -->
              <tr v-if="expandidos.has(r.chave)" class="bg-muted/30">
                <td colspan="9" class="px-3 py-2">
                  <div v-if="detalheLoading.has(r.chave) && !detalhes[r.chave]" class="py-3 text-muted-foreground">
                    <Loader2 class="size-4 inline animate-spin mr-1.5" />
                    carregando…
                  </div>
                  <div v-else class="grid grid-cols-1 lg:grid-cols-[1.1fr_1fr_1fr] gap-x-5 gap-y-3">
                    <!-- Configuração -->
                    <div>
                      <h4 class="mb-1.5 text-[11px] uppercase tracking-wider font-semibold text-muted-foreground">Configuração</h4>
                      <template v-if="editando !== r.chave">
                        <div class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
                          <template v-for="l in configLinhas(r)" :key="l.chave">
                            <span class="text-muted-foreground">{{ l.label }}</span>
                            <span>{{ l.valor }}</span>
                          </template>
                          <span class="text-muted-foreground">Re-aviso</span>
                          <span>a cada {{ r.reaviso_horas }} h enquanto persistir</span>
                          <span class="text-muted-foreground">Avisar</span>
                          <span>
                            <template v-if="r.modo === 'silencioso'">ninguém (silencioso)</template>
                            <template v-else-if="(r.threema_destinatarios || []).length">
                              Threema: {{ nomesDestinatarios(r) }}
                              <span class="text-muted-foreground"> · {{ ORIGEM_THREEMA[r.threema_origem || ''] || 'origem desconhecida' }}</span>
                            </template>
                            <template v-else><span class="text-red-600 dark:text-red-400">ninguém — sem destinatário no Threema</span></template>
                          </span>
                          <span class="text-muted-foreground">Modo</span>
                          <span>
                            {{ r.modo }}
                            <span v-if="r.modo_alterado_por" class="text-muted-foreground"> · {{ r.modo_alterado_por }}, {{ fmtRel(r.modo_alterado_em) }}</span>
                          </span>
                          <template v-if="r.ultima_falha_em">
                            <span class="text-muted-foreground">Última falha</span>
                            <span class="text-red-600 dark:text-red-400 break-words">{{ fmtRel(r.ultima_falha_em) }} — {{ r.ultima_falha_erro || 'sem detalhe' }}</span>
                          </template>
                        </div>
                        <div v-if="canEdit" class="mt-2">
                          <button
                            type="button"
                            class="inline-flex h-7 items-center gap-1 rounded-md border bg-background px-2 text-[11.5px] font-medium hover:bg-muted"
                            @click="abrirEdicao(r)"
                          >
                            <Wrench class="size-3" />
                            Editar
                          </button>
                        </div>
                      </template>
                      <!-- Edição inline (re-aviso, destinatários, chaves simples do config) -->
                      <form v-else class="space-y-2" @submit.prevent="salvarEdicao(r)">
                        <div class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 items-center">
                          <template v-for="(v, k) in editForm.config" :key="k">
                            <label class="text-muted-foreground" :for="`cfg-${r.chave}-${k}`">{{ configLabel(r, String(k)) }}</label>
                            <input
                              v-if="tipoCampo(v) === 'checkbox'"
                              :id="`cfg-${r.chave}-${k}`"
                              type="checkbox"
                              class="size-3.5"
                              :checked="Boolean(v)"
                              @change="setConfig(String(k), ($event.target as HTMLInputElement).checked)"
                            />
                            <input
                              v-else
                              :id="`cfg-${r.chave}-${k}`"
                              :type="tipoCampo(v)"
                              class="h-7 w-full max-w-[240px] rounded border bg-background px-2 text-xs"
                              :value="v"
                              @input="setConfig(String(k), tipoCampo(v) === 'number' ? Number(($event.target as HTMLInputElement).value) : ($event.target as HTMLInputElement).value)"
                            />
                          </template>
                          <label class="text-muted-foreground" :for="`reaviso-${r.chave}`">Re-aviso (h)</label>
                          <input
                            :id="`reaviso-${r.chave}`"
                            v-model.number="editForm.reaviso_horas"
                            type="number"
                            min="1"
                            class="h-7 w-24 rounded border bg-background px-2 text-xs"
                          />
                          <label class="text-muted-foreground">Threema</label>
                          <div class="relative w-full max-w-[420px]">
                            <button
                              type="button"
                              class="flex min-h-7 w-full flex-wrap items-center gap-1 rounded border bg-background px-2 py-1 text-left text-xs hover:bg-muted/50"
                              :title="seletorAberto ? 'Fechar' : 'Escolher quem recebe os avisos'"
                              @click="seletorAberto = !seletorAberto"
                            >
                              <template v-if="editForm.threema_ids.length">
                                <span
                                  v-for="id in editForm.threema_ids"
                                  :key="id"
                                  class="inline-flex items-center rounded-full border bg-card px-2 py-0.5 text-[11px]"
                                  :title="id"
                                >{{ nomeDe(id) }}</span>
                              </template>
                              <span v-else class="text-muted-foreground">ninguém — clique pra escolher</span>
                              <ChevronDown class="ml-auto size-3.5 shrink-0 text-muted-foreground" :class="seletorAberto ? 'rotate-180' : ''" />
                            </button>
                            <div
                              v-if="seletorAberto"
                              class="absolute left-0 top-full z-30 mt-1 w-full rounded-md border bg-card p-1 shadow-lg"
                            >
                              <div v-if="!diretorioCarregado" class="px-2 py-1.5 text-xs text-muted-foreground">carregando…</div>
                              <div v-else-if="!diretorio.length" class="px-2 py-1.5 text-xs text-muted-foreground">
                                Ninguém com Threema cadastrado — preencha o campo Threema em Admin › Usuários.
                              </div>
                              <template v-else>
                                <label
                                  v-for="d in diretorio"
                                  :key="d.id"
                                  class="flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-xs hover:bg-muted"
                                >
                                  <input type="checkbox" class="size-3.5" :checked="pessoaMarcada(d.id)" @change="alternarPessoa(d.id)" />
                                  <span>{{ d.nome }}</span>
                                  <span class="ml-auto font-mono text-[10px] text-muted-foreground">{{ d.id }}</span>
                                </label>
                                <div class="mt-1 flex items-center justify-between border-t px-2 pt-1.5 text-[11px] text-muted-foreground">
                                  <span>{{ editForm.threema_ids.length }} {{ editForm.threema_ids.length === 1 ? 'pessoa' : 'pessoas' }}</span>
                                  <button type="button" class="hover:text-foreground" @click="editForm = { ...editForm, threema_ids: idsPadrao(r) }; seletorAberto = false">
                                    voltar ao padrão do sistema
                                  </button>
                                </div>
                              </template>
                            </div>
                          </div>
                        </div>
                        <div class="flex items-center gap-1.5">
                          <button
                            type="submit"
                            class="inline-flex h-7 items-center gap-1 rounded-md bg-primary px-2.5 text-[11.5px] font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
                            :disabled="salvandoEdicao"
                          >
                            <Loader2 v-if="salvandoEdicao" class="size-3 animate-spin" />
                            Salvar
                          </button>
                          <button
                            type="button"
                            class="inline-flex h-7 items-center rounded-md px-2 text-[11.5px] text-muted-foreground hover:bg-muted"
                            :disabled="salvandoEdicao"
                            @click="editando = null"
                          >
                            cancelar
                          </button>
                        </div>
                      </form>
                    </div>

                    <!-- Contas cobertas -->
                    <div>
                      <h4 class="mb-1.5 text-[11px] uppercase tracking-wider font-semibold text-muted-foreground">Contas cobertas</h4>
                      <div v-if="!plataformasCobertas(r).length" class="text-muted-foreground">Este robô não olha contas de marketplace.</div>
                      <div v-else class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
                        <template v-for="p in plataformasCobertas(r)" :key="p">
                          <span class="text-muted-foreground">{{ plataformaInfo(p).nome }}</span>
                          <span class="flex flex-wrap items-center gap-1">
                            <template v-if="contasProblemaDa(r.chave, p).length">
                              <span
                                v-for="(c, i) in contasProblemaDa(r.chave, p)"
                                :key="`${c.conta}-${i}`"
                                class="inline-flex items-center rounded-full px-1.5 py-px text-[11px] font-medium"
                                :class="CLS_RED"
                                :title="erroDaConta(c)"
                              >{{ c.conta || '?' }} sem acesso</span>
                            </template>
                            <span v-else class="inline-flex items-center gap-1 text-emerald-700 dark:text-emerald-300">
                              <CheckCircle2 class="size-3" />
                              sem conta com problema
                            </span>
                          </span>
                        </template>
                      </div>
                      <div class="mt-2 text-[11px] text-muted-foreground">
                        <template v-if="contasNaRodada(r.chave) != null">{{ contasNaRodada(r.chave) }} contas na última rodada · </template>
                        conta sem acesso = ocorrência "Conta sem acesso à API"; reautorizar em
                        <NuxtLink to="/integrations" class="underline underline-offset-2 hover:text-foreground">Sistema › Integrações</NuxtLink>.
                      </div>
                    </div>

                    <!-- Últimas rodadas -->
                    <div>
                      <h4 class="mb-1.5 text-[11px] uppercase tracking-wider font-semibold text-muted-foreground">Últimas rodadas</h4>
                      <div v-if="!(detalhes[r.chave]?.rodadas?.length)" class="text-muted-foreground">
                        {{ r.modo === 'desligado' ? 'Desligado — não roda sozinho (só pelo "Rodar agora").' : 'Nenhuma rodada registrada ainda.' }}
                      </div>
                      <div v-else class="font-mono text-[11px] leading-relaxed">
                        <div
                          v-for="rd in rodadasVisiveis(r.chave)"
                          :key="rd.id"
                          class="flex items-baseline gap-2 whitespace-nowrap"
                          :title="rd.erro || rd.resumo || contadoresTexto(rd.contadores)"
                        >
                          <span :class="rd.ok === false ? 'text-red-500' : (rd.ok === null ? 'text-muted-foreground' : 'text-emerald-500')">●</span>
                          <span class="w-[86px] shrink-0">{{ fmtHora(rd.iniciada_em) }}</span>
                          <span class="w-[62px] shrink-0 text-muted-foreground">{{ duracaoRodada(rd) }}</span>
                          <span class="truncate" :class="rd.ok === false ? 'text-red-600 dark:text-red-400' : ''">
                            {{ rd.ok === false ? (rd.erro || 'falhou') : (rd.resumo || contadoresTexto(rd.contadores) || 'ok') }}
                          </span>
                        </div>
                        <button
                          v-if="(detalhes[r.chave]?.rodadas?.length ?? 0) > 8"
                          type="button"
                          class="mt-1 font-sans text-[11px] text-muted-foreground hover:text-foreground underline underline-offset-2"
                          @click="toggleRodadasTodas(r.chave)"
                        >
                          {{ rodadasTodas.has(r.chave) ? 'mostrar menos' : `ver as ${detalhes[r.chave]?.rodadas?.length} últimas` }}
                        </button>
                      </div>
                    </div>
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>

      <div class="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted-foreground px-0.5">
        <span class="inline-flex items-center gap-1.5"><i class="inline-block h-3 w-[30px] rounded-full bg-emerald-500" /> ligado: roda, registra e avisa</span>
        <span class="inline-flex items-center gap-1.5"><i class="inline-block h-3 w-[30px] rounded-full bg-amber-500" /> silencioso: roda e registra, não avisa</span>
        <span class="inline-flex items-center gap-1.5"><i class="inline-block h-3 w-[30px] rounded-full bg-gray-300 dark:bg-gray-600" /> desligado: não roda sozinho (só pelo "Rodar agora")</span>
      </div>
    </template>

    <!-- ══ Aba Ocorrências ══ -->
    <template v-else>
      <div v-if="ocError" class="flex items-center gap-2 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-400">
        <AlertCircle class="size-4" />
        {{ ocError }}
      </div>

      <div class="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-2">
        <StatCard label="Abertas" :value="resumo.abertas" :hint="`em ${chipsRobo.filter((c) => c.abertas > 0).length} ${chipsRobo.filter((c) => c.abertas > 0).length === 1 ? 'robô' : 'robôs'}`" :icon="AlertCircle" tone="danger" compact />
        <StatCard label="Precisam de pessoa" :value="resumo.abertas_pessoa" hint="avisadas no Threema" :icon="UserRound" tone="warning" compact />
        <StatCard label="Novas hoje" :value="resumo.novas_hoje" hint="desde 00:00" :icon="Sparkles" compact />
        <StatCard label="Fecharam sozinhas (7 d)" :value="resumo.sumiram_7d" hint="o problema sumiu antes de alguém agir" :icon="CheckCircle2" tone="success" compact />
        <StatCard label="Tratadas (7 d)" :value="resumo.tratadas_7d" hint="marcadas por alguém" :icon="Wrench" compact />
        <StatCard label="Contas sem vigilância" :value="resumo.contas_sem_vigilancia" hint="credencial morta — nenhum robô enxerga" :icon="ShieldOff" tone="danger" compact />
      </div>

      <!-- Chips por robô -->
      <div class="flex flex-wrap gap-1.5">
        <button
          type="button"
          class="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-colors"
          :class="!filtroRobo ? 'border-primary bg-primary/10 text-primary' : 'bg-background hover:bg-muted'"
          @click="filtroRobo = ''"
        >
          <i class="inline-block size-[7px] rounded-full" :class="resumo.abertas > 0 ? 'bg-red-500' : 'bg-gray-300 dark:bg-gray-600'" />
          Todos <b class="font-semibold tabular-nums">{{ resumo.abertas }}</b>
        </button>
        <button
          v-for="c in chipsRobo"
          :key="c.chave"
          type="button"
          class="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-colors"
          :class="filtroRobo === c.chave ? 'border-primary bg-primary/10 text-primary' : 'bg-background hover:bg-muted'"
          @click="filtroRobo = filtroRobo === c.chave ? '' : c.chave"
        >
          <i class="inline-block size-[7px] rounded-full" :class="c.abertas > 0 ? 'bg-red-500' : 'bg-gray-300 dark:bg-gray-600'" />
          {{ c.nome }} <b class="font-semibold tabular-nums">{{ c.abertas }}</b>
        </button>
      </div>

      <div class="flex flex-wrap items-center gap-2">
        <div class="relative">
          <Search class="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
          <input
            v-model="busca"
            class="h-9 w-72 rounded-md border bg-background pl-8 pr-3 text-sm"
            placeholder="pedido, conta, texto…"
          />
        </div>
        <select v-model="filtroPlataforma" class="h-9 rounded-md border bg-background px-2 text-sm" title="Filtrar por plataforma">
          <option value="">Plataforma: todas</option>
          <option v-for="p in opcoesPlataforma" :key="p.cod" :value="p.cod">{{ p.nome }}</option>
        </select>
        <select v-model="filtroConta" class="h-9 rounded-md border bg-background px-2 text-sm" title="Filtrar por conta">
          <option value="">Conta: todas</option>
          <option v-for="c in opcoesConta" :key="c" :value="c">{{ c }}</option>
        </select>
        <select v-model="filtroStatus" class="h-9 rounded-md border bg-background px-2 text-sm" title="Abertas = ainda ninguém tratou; fechadas = sumiram, tratadas ou ignoradas">
          <option value="abertas">Status: abertas</option>
          <option value="fechadas">Status: fechadas</option>
          <option value="todas">Status: todas</option>
        </select>
        <label class="inline-flex items-center gap-1.5 h-9 rounded-md border bg-background px-2 text-sm cursor-pointer" title="Esconde o que é só registro (robô segurou, info)">
          <input v-model="filtroPessoa" type="checkbox" class="size-3.5" />
          Só o que precisa de pessoa
        </label>
        <select v-model.number="filtroDias" class="ml-auto h-9 rounded-md border bg-background px-2 text-sm" title="Janela das fechadas (as abertas aparecem sempre)">
          <option :value="1">Período: hoje</option>
          <option :value="7">Período: 7 dias</option>
          <option :value="30">Período: 30 dias</option>
          <option :value="90">Período: 90 dias</option>
        </select>
      </div>

      <div class="overflow-auto rounded border max-h-[75vh]">
        <table class="w-full min-w-[1180px] text-xs border-collapse">
          <thead class="sticky top-0 z-20 bg-background">
            <tr class="border-b">
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap w-[150px]">Robô</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap w-[118px]">Plataforma</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap w-[110px]">Conta</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap w-[165px]">Pedido</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap">O que encontrou</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap w-[118px]">
                Achado em
                <span class="block font-normal">visto por último</span>
              </th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap w-[170px]">Status</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap w-[240px]"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="ocLoading && !ocorrencias.length">
              <td colspan="8" class="py-8 text-center text-muted-foreground">
                <Loader2 class="size-4 inline animate-spin mr-1.5" />
                carregando…
              </td>
            </tr>
            <tr v-else-if="!ocorrencias.length">
              <td colspan="8" class="py-10 text-center text-muted-foreground">
                <template v-if="!ocCarregou" />
                <template v-else-if="!temFiltroOc">
                  <CheckCircle2 class="size-5 inline text-emerald-500 mr-1.5 -mt-0.5" />
                  Nenhuma ocorrência aberta — os robôs não encontraram nada.
                </template>
                <template v-else>
                  Nenhuma ocorrência com esses filtros.
                  <button type="button" class="ml-1 underline underline-offset-2 hover:text-foreground" @click="limparFiltrosOc">limpar filtros</button>
                </template>
              </td>
            </tr>
            <tr
              v-for="o in ocorrencias"
              :key="o.id"
              class="border-t align-top hover:brightness-95 dark:hover:brightness-110"
              :class="o.fechada_em ? 'text-muted-foreground' : 'bg-amber-50/40 dark:bg-amber-900/10'"
            >
              <td class="px-2 py-1.5">
                <div class="font-semibold" :class="o.fechada_em ? '' : 'text-foreground'">{{ roboNome(o) }}</div>
                <div v-if="roboCadencia(o.robo_chave)" class="text-[11px] text-muted-foreground">{{ roboCadencia(o.robo_chave) }}</div>
              </td>
              <td class="px-2 py-1.5 whitespace-nowrap">
                <OuvidoriaPlataforma v-if="o.plataforma" :codigo="o.plataforma" />
                <span v-else class="text-muted-foreground">—</span>
              </td>
              <td class="px-2 py-1.5 whitespace-nowrap" :title="o.conta || ''">{{ o.conta || '—' }}</td>
              <td class="px-2 py-1.5 font-mono whitespace-nowrap">{{ o.pedido || '—' }}</td>
              <td class="px-2 py-1.5 leading-snug">
                <div :class="o.fechada_em ? '' : 'text-foreground'">{{ o.titulo }}</div>
                <div v-if="o.acao" class="text-muted-foreground">→ {{ o.acao }}</div>
                <!-- Detalhe longo no balão (ObservacaoPopover, só leitura): a
                     célula mostra o começo; clicou, abre inteiro. -->
                <div v-if="o.detalhe" class="-ml-1 mt-0.5 max-w-[520px] text-muted-foreground">
                  <ObservacaoPopover
                    :model-value="o.detalhe"
                    disabled
                    titulo="Detalhe"
                    :dica="[roboNome(o), o.pedido || o.conta || '', dadosResumo(o)].filter(Boolean).join(' · ')"
                  />
                </div>
              </td>
              <td class="px-2 py-1.5 whitespace-nowrap">
                <div>{{ fmtRel(o.aberta_em) }}</div>
                <div class="text-[11px] text-muted-foreground">{{ fmtRel(o.fechada_em || o.ultima_vista_em) }}</div>
              </td>
              <td class="px-2 py-1.5 whitespace-nowrap">
                <span class="inline-block rounded px-1.5 py-0.5 text-[11px] font-medium" :class="statusDe(o).cls" :title="statusDe(o).title">{{ statusDe(o).label }}</span>
                <div v-if="statusDe(o).quem" class="mt-0.5 text-[11px] text-muted-foreground">{{ statusDe(o).quem }}</div>
              </td>
              <td class="px-2 py-1.5">
                <div class="flex flex-wrap items-center gap-1">
                  <template v-if="!o.fechada_em">
                    <button
                      v-if="canEdit"
                      type="button"
                      class="inline-flex h-[26px] items-center gap-1 rounded-md bg-primary px-2 text-[11.5px] font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
                      :disabled="acoesEmVoo.has(o.id)"
                      title="Resolvi — fecha a ocorrência. Se o problema voltar depois de 24 h, o robô abre outra."
                      @click="agir(o, 'tratar')"
                    >
                      <Loader2 v-if="acoesEmVoo.has(o.id)" class="size-3 animate-spin" />
                      Tratado
                    </button>
                    <template v-if="linkInfo(o.link)">
                      <NuxtLink
                        v-if="linkInfo(o.link)!.interno"
                        :to="o.link!"
                        class="inline-flex h-[26px] items-center gap-1 rounded-md border bg-background px-2 text-[11.5px] font-medium hover:bg-muted"
                      >
                        {{ linkInfo(o.link)!.label }}
                        <ExternalLink class="size-3" />
                      </NuxtLink>
                      <a
                        v-else
                        :href="o.link!"
                        target="_blank"
                        rel="noopener"
                        class="inline-flex h-[26px] items-center gap-1 rounded-md border bg-background px-2 text-[11.5px] font-medium hover:bg-muted"
                      >
                        {{ linkInfo(o.link)!.label }}
                        <ExternalLink class="size-3" />
                      </a>
                    </template>
                    <button
                      v-if="canEdit"
                      type="button"
                      class="inline-flex h-[26px] items-center rounded-md px-2 text-[11.5px] text-muted-foreground hover:bg-muted disabled:opacity-50"
                      :disabled="acoesEmVoo.has(o.id)"
                      title="Não é problema — some da lista e o robô não abre de novo pra esse caso"
                      @click="agir(o, 'ignorar')"
                    >
                      Ignorar
                    </button>
                  </template>
                  <template v-else>
                    <template v-if="linkInfo(o.link)">
                      <NuxtLink
                        v-if="linkInfo(o.link)!.interno"
                        :to="o.link!"
                        class="inline-flex h-[26px] items-center gap-1 rounded-md px-2 text-[11.5px] text-muted-foreground hover:bg-muted"
                      >
                        {{ linkInfo(o.link)!.label }}
                        <ExternalLink class="size-3" />
                      </NuxtLink>
                      <a
                        v-else
                        :href="o.link!"
                        target="_blank"
                        rel="noopener"
                        class="inline-flex h-[26px] items-center gap-1 rounded-md px-2 text-[11.5px] text-muted-foreground hover:bg-muted"
                      >
                        {{ linkInfo(o.link)!.label }}
                        <ExternalLink class="size-3" />
                      </a>
                    </template>
                    <button
                      v-if="canEdit && (o.fechamento === 'tratada' || o.fechamento === 'ignorada')"
                      type="button"
                      class="inline-flex h-[26px] items-center rounded-md px-2 text-[11.5px] text-muted-foreground hover:bg-muted disabled:opacity-50"
                      :disabled="acoesEmVoo.has(o.id)"
                      title="Volta pra lista de abertas (e o robô volta a re-ver)"
                      @click="agir(o, 'reabrir')"
                    >
                      <Loader2 v-if="acoesEmVoo.has(o.id)" class="size-3 animate-spin mr-1" />
                      Reabrir
                    </button>
                  </template>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 text-[11px] text-muted-foreground px-0.5">
        <div class="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span><span class="inline-block rounded px-1.5 py-px font-medium" :class="CLS_RED">Aberta · pessoa</span> alguém precisa agir</span>
          <span><span class="inline-block rounded px-1.5 py-px font-medium" :class="CLS_BLUE">Robô segurou</span> o robô já protegeu, falta decidir</span>
          <span><span class="inline-block rounded px-1.5 py-px font-medium" :class="CLS_GREEN">Fechou sozinha</span> o problema sumiu</span>
          <span><span class="inline-block rounded px-1.5 py-px font-medium" :class="CLS_GRAY">Tratada</span> marcada por alguém</span>
        </div>
        <span>
          {{ resumo.abertas }} abertas · mostrando {{ ocorrencias.length }} de {{ ocTotal }}
          <template v-if="filtroStatus !== 'abertas'"> ({{ filtroDias === 1 ? 'hoje' : `${filtroDias} dias` }})</template>
          <template v-if="ocTotal > ocorrencias.length"> — afine os filtros pra ver o resto</template>
        </span>
      </div>
    </template>
  </div>
</template>
