<script setup lang="ts">
import {
  AlertCircle,
  Bot,
  Check,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  History,
  ImagePlus,
  Loader2,
  Mail,
  Paperclip,
  Plus,
  RotateCcw,
  Scale,
  Search,
  Send,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  Undo2,
  X,
} from 'lucide-vue-next'
import { PopoverContent, PopoverPortal, PopoverRoot, PopoverTrigger } from 'reka-ui'
import { REVISAO_DOT, REVISAO_FILTROS, REVISAO_ORDEM, REVISAO_PRECISA_VER, diaMes, passaNoFiltroRevisao, rotuloRevisao } from '~/lib/chamadosRevisao'
import type { RevisaoCampos, RevisaoDia, RevisaoDias, RevisaoEstado, RevisaoMarca } from '~/lib/chamadosRevisao'
import { hojeISO } from '~/lib/conciliacao'

// 30/09/2026: a tela virou componente pra aba Jurídico morar também em
// Ouvidoria › Denúncia (Vinicius: "tira o jurídico do chamado e coloca nesse
// painel"). A página /chamados só embrulha isto; `modo="juridico"` (usado em
// /denuncia) mostra só a lista do jurídico, sem cabeçalho nem abas.
const props = defineProps<{ modo?: 'juridico' }>()

// Aba "Chamados" (Pós-venda): centraliza os chamados abertos nas plataformas,
// no formato da planilha — Data | pedido bling | pedido marketplace |
// plataforma | produto | sku | conta | status bling | origem | chamado |
// status (+ quem falou por último) | observação | alterar status bling | valor.
// O sim/não "monitoramento" saiu em 15/09 (Eduardo): o robô acompanha TODO
// chamado de API do ML e fecha sozinho quando o claim encerra — nada a marcar.
// 17/09 (Vinicius): o grupo "Réplica" (botão responder + réplica automática)
// saiu da tabela — a réplica automática nunca foi ligada e a resposta manual
// continua dentro do histórico. No lugar: Status (o que a plataforma diz do
// chamado, e desde quando) e Última ação (quando e quem fez a última coisa no caso).
// 19/09 (Vinicius, Chamados v2): os 13 status da coluna viraram 5, por QUEM
// está com a bola — Análise Humano, Análise Robô, Aguard. Plataforma,
// Encerrado (a plataforma fechou, falta uma pessoa dar lucro/prejuízo) e
// Concluído (fechado por pessoa). Nada mais fecha sozinho: "Encerrado" é
// estado, não fechamento. O porquê do status saiu da célula ("painel bem
// limpo") e ficou só no tooltip do chip. No histórico entrou a "instrução pro
// robô": recado que não vai pra plataforma — o robô lê na próxima passada.

type Origem = 'margem' | 'logistica' | 'devolucao' | 'vendas'
type Canal = 'api' | 'robo' | 'manual'

const ORIGENS: { value: Origem; label: string }[] = [
  { value: 'margem', label: 'Margem' },
  { value: 'logistica', label: 'Logística' },
  { value: 'devolucao', label: 'Devolução' },
  { value: 'vendas', label: 'Vendas' },
]
const CANAIS: { value: Canal; label: string; hint: string }[] = [
  { value: 'manual', label: 'manual', hint: 'só registra no histórico' },
  // 17/09: era "API ML" — aparecia assim em linha de TikTok/Shopee e confundia.
  { value: 'api', label: 'API', hint: 'mediação/disputa pela API da plataforma (ML, Shopee, TikTok)' },
  { value: 'robo', label: 'robô', hint: 'formulário/protocolo — fila do robô' },
]
// Ao fechar: Logística → Resolvido ou Perdimento (célula M2 da planilha).
const FECHAMENTO: Partial<Record<Origem, string[]>> = { logistica: ['Resolvido', 'Perdimento'] }
// Coluna Status (19/09): os 5 códigos que a API manda em `status_aba` → rótulo e
// cor. A ordem é a do filtro. Regra do Vinicius: primeiro o robô; gente só quando
// o robô desiste. O motivo (o porquê exato) vem em `status_aba_motivo` e só
// aparece no tooltip do chip.
const STATUS_ABA: { value: string; label: string; cls: string; hint: string }[] = [
  { value: 'analise_humano', label: 'Análise Humano', cls: 'bg-red-500/15 text-red-700 dark:text-red-300', hint: 'o robô não conseguiu — precisa de gente' },
  { value: 'analise_robo', label: 'Análise Robô', cls: 'bg-indigo-500/15 text-indigo-700 dark:text-indigo-300', hint: 'a IA de Chamado ou o robô têm trabalho aqui: decidir, responder, reenviar, achar outro caminho, ou uma instrução sua' },
  { value: 'aguard_plataforma', label: 'Aguard. Plataforma', cls: 'bg-sky-500/15 text-sky-700 dark:text-sky-300', hint: 'a bola está com a plataforma' },
  { value: 'encerrado', label: 'Encerrado', cls: 'bg-amber-500/15 text-amber-700 dark:text-amber-300', hint: 'a plataforma encerrou o caso (ou tiraram o motivo na Devoluções) — falta fechar com lucro/prejuízo' },
  { value: 'concluido', label: 'Concluído', cls: 'bg-muted text-muted-foreground', hint: 'fechado por uma pessoa' },
]
const STATUS_POR_CODIGO = Object.fromEntries(STATUS_ABA.map((s) => [s.value, s]))
// A data ao lado do chip só vale quando é uma decisão: a plataforma encerrou ou uma
// pessoa concluiu (19/09). Nos outros três, a hora do robô desistir ou da fala não
// ajuda (Vinicius 17/09) — a 2ª linha já diz quando foi a última resposta.
const STATUS_COM_DATA = new Set(['encerrado', 'concluido'])
function mostraDataStatus(row: ChamadoRow): boolean {
  return !!row.status_aba_at && STATUS_COM_DATA.has(row.status_aba || '') && row.status_aba_at !== row.ultima_resposta_at
}
function statusInfo(row: ChamadoRow) {
  return STATUS_POR_CODIGO[row.status_aba || ''] || { value: row.status_aba || '', label: row.status_aba || '—', cls: 'bg-muted text-muted-foreground', hint: '' }
}
// Tooltip do chip (19/09: o motivo saiu da célula — "painel bem limpo"): rótulo + porquê.
function statusTitle(row: ChamadoRow): string {
  const info = statusInfo(row)
  return row.status_aba_motivo ? `${info.label} — ${row.status_aba_motivo}` : `${info.label} — ${info.hint}`
}
// 28/09 (Vinicius): "ml" e "mercado livre" são a mesma plataforma — a coluna
// guarda como cada caminho gravou; a tela mostra um nome só (o filtro também).
const APELIDOS_PLATAFORMA: Record<string, string> = {
  ml: 'mercado livre', mercadolivre: 'mercado livre', meli: 'mercado livre',
  'tik tok': 'tiktok', 'tiktok shop': 'tiktok',
}
function nomePlataforma(p: string | null | undefined): string {
  const k = (p || '').trim().toLowerCase()
  return APELIDOS_PLATAFORMA[k] || k
}
// Quem falou por último, pra coluna Última ação: "nós · robô" / "plataforma · Shopee".
function quemRespondeu(row: ChamadoRow): string {
  return quemFalou(row, row.ultima_resposta_direcao, row.ultima_resposta_autor)
}
function quemFalou(row: ChamadoRow, direcao: string | null | undefined, autorNome: string | null | undefined): string {
  const autor = (autorNome || '').trim()
  if (direcao === 'recebida') {
    const plat = autor && autor !== 'monitor' ? autor : (nomePlataforma(row.plataforma) || 'plataforma').toUpperCase()
    return `plataforma · ${plat}`
  }
  // 24/09: o cérebro virou "IA de Chamado" (a resposta que ela decide sai assinada "cérebro")
  const nos = autor === 'cérebro' || autor === 'IA de Chamado' ? 'IA de Chamado' : autor.startsWith('robô') ? 'robô' : autor === 'sistema' ? 'automático' : autor || 'nós'
  return `nós · ${nos}`
}

type Anexo = { id: string; mensagem_id: string | null; filename: string; content_type: string; size_bytes: number; created_at: string }
type Mensagem = {
  id: string
  chamado_id: string
  direcao: 'enviada' | 'recebida' | 'sistema'
  tipo: string
  texto: string
  canal: string
  status: 'registrada' | 'pendente' | 'enviada' | 'falhou'
  erro: string | null
  autor_nome: string | null
  enviada_at: string | null
  created_at: string
  anexos: Anexo[]
  // 24/09: decisão da IA de Chamado — recebe ✓/✗ no próprio histórico
  da_ia?: boolean
  avaliacao_ia?: AvaliacaoIa | null
}
type AvaliacaoIa = { certo: boolean; correcao: string | null; autor: string | null; quando: string }
type ChamadoRow = {
  id: string
  data: string | null
  pedido_bling: string | null
  pedido_marketplace: string | null
  plataforma: string | null
  conta: string | null
  produto: string | null
  sku: string | null
  status_bling: string | null
  status_bling_atual: string | null
  origem: Origem
  origem_ref: string | null
  chamado: string | null
  chamado_url: string | null
  // 25/09 (294571): consulta aberta à mão no Portal de Atendimento da Shopee — o
  // executor de leitura lê lá também (além da devolução em `chamado`).
  consulta_portal?: string | null
  canal: Canal
  // 22/09: o protocolo veio da TELA (o robô abriu no Seller Center) — nenhuma API
  // responde por ele; quem lê é o robô de leitura.
  chamado_de_tela: boolean
  leitura_robo_at: string | null
  alterar_status_bling: string | null
  // Resultado do chamado em R$ — coluna "Valor" do Controle: positivo = lucro,
  // negativo = prejuízo (Eduardo 15/09). Obrigatório ao resolver.
  valor_recuperado: number | null
  auto_ligada: boolean
  auto_dias: number | null
  auto_mensagem: string | null
  auto_ultimo_envio_at: string | null
  auto_proximo_envio_at: string | null
  resolvido: boolean
  resolvido_at: string | null
  observacao: string | null
  // 09/11: equipes de vendas que veem o chamado (Londres / Israel / Marrocos) — coluna Equipe.
  equipes?: string[]
  juridico_enviado_at?: string | null
  juridico_enviado_por_nome?: string | null
  juridico_obs?: string | null
  juridico_link?: string | null
  juridico_enviados?: string[]
  created_at: string
  updated_at: string
  mensagens_total: number
  ultima_mensagem_at: string | null
  // Coluna Status: código (STATUS_ABA) + desde quando; oficial da API ou derivado do histórico.
  status_plataforma: string | null
  status_plataforma_at: string | null
  status_aba: string | null
  status_aba_at: string | null
  status_aba_motivo?: string | null
  // 19/09 (Chamados v2): custo do produto (soma preco_custo × qtd do pedido no
  // Bling) só pra ver na janela Resolver; sugestão de resultado do robô/plataforma
  // (o humano confirma ao fechar); instrução pendente pro robô (texto).
  custo_produto?: number | string | null
  custo_detalhe?: string | null
  valor_sugerido?: number | string | null
  instrucao_pendente?: string | null
  // Última FALA real (nossa ou da plataforma) — a Revisão usa.
  ultima_resposta_at: string | null
  ultima_resposta_direcao: 'enviada' | 'recebida' | null
  ultima_resposta_autor: string | null
  // 09/11/2026: coluna Última ação — a fala, a instrução de pessoa ou o que a IA de
  // Chamado FEZ na tela (abrir SAFE-T, responder…); a conferência sem novidade não conta.
  ultima_acao_at?: string | null
  ultima_acao_tipo?: 'plataforma' | 'nos' | 'ia' | 'instrucao' | null
  ultima_acao_autor?: string | null
  ultima_acao_resumo?: string | null
  anexos_auto: Anexo[]
} & RevisaoCampos
type Page = { items: ChamadoRow[]; total: number; limit: number; offset: number; plataformas: string[]; contas: string[]; plataforma_totais?: Record<string, number>; pode_encerrar?: boolean }
type Lookup = {
  data: string | null
  pedido_bling: string | null
  pedido_marketplace: string | null
  plataforma: string | null
  conta: string | null
  produto: string | null
  sku: string | null
  status_bling: string | null
}
type Draft = Lookup & { origem: Origem | ''; canal: Canal; chamado: string; chamado_url: string; observacao: string }

// 21/09 (Vinicius): janela "Excluir chamado" — a prévia do que vai junto
// (GET /api/chamados/{id}/exclusao) e a resposta do POST /excluir.
type ExclusaoLancamento = {
  id: string
  sku: string | null
  produtos: string | null
  condicao_produto: string | null
  motivo_devolucao: string | null
  data_devolvido_estoque: string | null
  // Resultado da entrada no Bling (15/10): ok | falhou | conferindo | sem_confirmacao | null.
  estoque_status?: string | null
  // estoque_mov_sku preenchido e não revertido → ao excluir o back dá a saída no Bling sozinho.
  estoque_estornavel: boolean
  estoque_mov_sku: string | null
  estoque_mov_qty: number | null
  // motivo dos que abrem chamado (Não recebido, Extraviado…) → já vem marcado.
  marcado_padrao: boolean
  // passou por Manutenção (e saiu): a exclusão leva junto o débito do conserto.
  manutencao?: boolean
}
type ExclusaoPreview = {
  chamado_id: string
  pedido_bling: string | null
  plataforma: string | null
  status_bling_atual: string | null
  exige_situacao: boolean
  // a disputa/revisão JÁ foi aberta na plataforma — excluir aqui não cancela lá.
  abertura_enviada: boolean
  pode_excluir_lancamentos: boolean
  lancamentos: ExclusaoLancamento[]
}
type ExcluirOut = {
  ok: boolean
  lancamentos_excluidos: number
  estornos: { sku: string | null; qty: number | null; mensagem: string | null }[]
  situacao: string | null
  // 26/10 (caso 300978): prejuízos automáticos das linhas excluídas — só vêm
  // quando há o que dizer.
  prejuizos_apagados?: { qtd: number; total: number }
  prejuizos_mantidos?: { tipo: string | null; prejuizo: number | null; reembolso: number | null; valor?: number | null; motivo: string | null }[]
}

const PAGE_SIZE = 100

const { api } = useApi()
const toasts = useToasts()
// 19/09: `isAdmin` era usado no template sem existir no script — o botão
// "destinatários" do jurídico nunca aparecia (nem pro admin) e o typecheck acusava.
const auth = useAuthStore()
const isAdmin = computed(() => auth.user?.role === 'admin')
const canEdit = useCan('chamados', 'edit')
// 30/09: aviso da IA de Chamado parada em "não sou robô"/login vai pro Cairo —
// ele também escolhe quem recebe. Espelho do backend: _EMAILS_EXTRAS["chamados_ia"]
// em routers/informar.py.
const AVISO_IA_USERS = ['sa.geral@tutamail.com']
const podeAvisoIa = computed(() => {
  const email = (auth.user?.email || '').trim().toLowerCase()
  return isAdmin.value || (!!email && AVISO_IA_USERS.includes(email))
})
const avisoIaCfgOpen = ref(false)
const canDelete = useCan('chamados', 'delete')
// 11/11/2026 (Vinicius): "somente eu consigo encerrar". Vem da lista (`pode_encerrar`:
// edita e vê todas as lojas); a equipe marca Encerrar na Revisão e não vê o resolver.
const podeEncerrar = ref(false)
const canConciliacao = useCan('conciliacao', 'view')

const items = ref<ChamadoRow[]>([])
const total = ref(0)
const plataformas = ref<string[]>([])
const contas = ref<string[]>([])
const situacoes = ref<string[]>([])
const page = ref(1)
const loading = ref(false)
const error = ref<string | null>(null)

const search = ref('')
const origemFilter = ref<'all' | Origem>('all')
const plataformaFilter = ref<'all' | string>('all')
// Filtro por conta (Eduardo 15/09: "ex. ML Aguiar 2") — a lista segue a plataforma.
const contaFilter = ref<'all' | string>('all')
const mostrar = ref<'abertos' | 'resolvidos' | 'todos'>('abertos')
// Filtro pela coluna Status — na página carregada (o status é calculado na listagem).
const statusFilter = ref<'all' | string>('all')
// 05/11/2026: coluna Revisão. Filtro também na página carregada; `ordemRevisao` é a
// posição de cada linha calculada no carregamento — marcar não faz a linha pular.
const revisaoFilter = ref<'all' | string>('all')
const ordemRevisao = ref(new Map<string, number>())
const plataformaTotais = ref<Record<string, number>>({})
// Raio-X: o chamado aberto no painel da direita (por id, sobrevive ao replaceRow).
const raioId = ref<string | null>(null)
// Painel Dias: os números e o dia clicado, que filtra a tabela nos que faltaram.
const diasRevisao = ref<RevisaoDias | null>(null)
const diaEscolhido = ref<RevisaoDia | null>(null)
// Aba Jurídico (Eduardo 04/09): tudo que foi encaminhado ao jurídico, aberto ou resolvido.
// 24/09 (Vinicius): aba IA de Chamado — o cérebro ganha nome e lugar (liga/desliga,
// manual "quando acontecer isso, faça isso", o que ela decidiu). `?tab=ia` abre nela.
const tab = ref<'chamados' | 'juridico' | 'ia'>(props.modo === 'juridico' ? 'juridico' : useRoute().query.tab === 'ia' ? 'ia' : 'chamados')

// Chegando com ?search=... (link da coluna Chamado em Devoluções): abre já
// filtrado pelo pedido e mostrando abertos E resolvidos — sem isso um chamado
// resolvido ficaria escondido pelo filtro padrão "abertos".
const _searchQuery = useRoute().query.search
if (typeof _searchQuery === 'string' && _searchQuery.trim()) {
  search.value = _searchQuery.trim()
  mostrar.value = 'todos'
}

const addOpen = ref(false)
const lookupPedido = ref('')
const lookupLoading = ref(false)
const lookupError = ref<string | null>(null)
const draft = ref<Draft | null>(null)
const creating = ref(false)

const busy = ref<Set<string>>(new Set())
const rowSaveQueue = new Map<string, Promise<void>>()

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))
// Resumo no topo (Vinicius 18/09: "quantidade de chamados igual tem nos outros
// painéis, a soma deles"). O total vem do servidor (todos os filtros, todas as
// páginas); os grupos juntam os status da coluna Status por quem está com a
// bola e são contados na página carregada — como o filtro de status, porque o
// status é calculado na listagem. Clicar num card filtra por ele.
type ResumoGrupo = { key: string; label: string; cor: string; hint: string }
// 19/09: um card por status (a chave É o código de `status_aba`). "Concluído" só
// aparece quando o select "mostrar" traz os concluídos — em "abertos" seria sempre 0.
// 05/11: o número leva a cor do chip do Status.
const RESUMO_GRUPOS: ResumoGrupo[] = [
  { key: 'analise_humano', label: 'Análise Humano', cor: 'text-red-600 dark:text-red-400', hint: 'o robô não conseguiu — precisa de gente' },
  { key: 'analise_robo', label: 'Análise Robô', cor: 'text-indigo-700 dark:text-indigo-300', hint: 'a IA de Chamado ou o robô têm trabalho aqui' },
  { key: 'aguard_plataforma', label: 'Aguard. Plataforma', cor: 'text-sky-700 dark:text-sky-300', hint: 'a bola está com a plataforma' },
  { key: 'encerrado', label: 'Encerrado', cor: 'text-amber-700 dark:text-amber-300', hint: 'a plataforma encerrou — falta fechar com lucro/prejuízo' },
]
const RESUMO_CONCLUIDO: ResumoGrupo = { key: 'concluido', label: 'Concluído', cor: '', hint: 'fechado por uma pessoa' }
// Mais de uma página: os grupos e a divisão por origem só enxergam a página carregada.
const resumoParcial = computed(() => items.value.length < total.value)
const resumoTotalLabel = computed(() => mostrar.value === 'abertos' ? 'Chamados abertos' : mostrar.value === 'resolvidos' ? 'Chamados concluídos' : 'Chamados')
const resumoPorOrigem = computed(() => {
  const partes = ORIGENS.map((o) => ({ label: o.label, n: items.value.filter((r) => r.origem === o.value).length })).filter((x) => x.n > 0)
  if (!partes.length) return ''
  const texto = partes.map((x) => `${x.label} ${x.n}`).join(' · ')
  return resumoParcial.value ? `nesta página: ${texto}` : texto
})
const resumoGrupos = computed(() => {
  const grupos = mostrar.value === 'abertos' ? RESUMO_GRUPOS : [...RESUMO_GRUPOS, RESUMO_CONCLUIDO]
  return grupos.map((g) => ({
    ...g,
    n: items.value.filter((r) => r.status_aba === g.key).length,
    hint: resumoParcial.value ? `${g.hint} (nesta página)` : g.hint,
  }))
})
function filtrarGrupo(key: string) {
  statusFilter.value = statusFilter.value === key ? 'all' : key
}
// Filtro da coluna Status (um código; o card do resumo usa o mesmo filtro).
const visiveis = computed(() => {
  const f = statusFilter.value
  const faltaram = diaEscolhido.value ? new Set(diaEscolhido.value.faltaram) : null
  const linhas = items.value.filter((r) => (f === 'all' || r.status_aba === f) && passaNoFiltroRevisao(r.revisao_estado, revisaoFilter.value) && (!faltaram || faltaram.has(r.id)))
  if (mostrar.value !== 'abertos') return linhas
  const ordem = ordemRevisao.value
  return [...linhas].sort((a, b) => (ordem.get(a.id) ?? 1) - (ordem.get(b.id) ?? 1))
})

// ----------------------------------------------------------------- abas de plataforma
const NOME_ABA: Record<string, string> = { 'mercado livre': 'Mercado Livre', shopee: 'Shopee', tiktok: 'TikTok', amazon: 'Amazon' }
const ORDEM_ABA = ['mercado livre', 'shopee', 'tiktok', 'amazon']
const plataformaAbas = computed(() => {
  const totais = plataformaTotais.value
  const posicao = (p: string) => { const i = ORDEM_ABA.indexOf(p); return i < 0 ? ORDEM_ABA.length : i }
  return plataformas.value
    .filter((p) => (totais[p] || 0) > 0 || p === plataformaFilter.value)
    .map((p) => ({ value: p, label: NOME_ABA[p] || p, n: totais[p] || 0 }))
    .sort((a, b) => posicao(a.value) - posicao(b.value))
})
const plataformaTotal = computed(() => Object.values(plataformaTotais.value).reduce((s, n) => s + n, 0))

// ----------------------------------------------------------------- revisão do dia
const hojeRotulo = computed(() => diaMes(hojeISO()))
const revisaoResumo = computed(() => {
  const n = (estados: RevisaoEstado[]) => items.value.filter((r) => estados.includes(r.revisao_estado)).length
  const revisar = n(['revisar'])
  const conferido = n(['conferido'])
  const fixo = n(['fixo'])
  const encerrar = n(['encerrar'])
  return { prazos: n(REVISAO_PRECISA_VER), falta: n(['falta']), revisar, conferido, fixo, encerrar, feitos: revisar + conferido + fixo + encerrar, total: items.value.length }
})
const revisaoPct = computed(() => (revisaoResumo.value.total ? (revisaoResumo.value.feitos / revisaoResumo.value.total) * 100 : 0))
const revisaoPills = computed(() => {
  const r = revisaoResumo.value
  return [
    { key: 'prazos', n: r.prazos, label: r.prazos === 1 ? 'prazo pra ver' : 'prazos pra ver', dot: REVISAO_DOT.prazo_vencido },
    { key: 'falta', n: r.falta, label: 'falta revisar', dot: REVISAO_DOT.falta },
    { key: 'revisar', n: r.revisar, label: 'revisar', dot: REVISAO_DOT.revisar },
    { key: 'encerrar', n: r.encerrar, label: 'pra encerrar', dot: REVISAO_DOT.encerrar },
    { key: 'conferido', n: r.conferido, label: 'conferido', dot: REVISAO_DOT.conferido },
    { key: 'fixo', n: r.fixo, label: 'fixo com data', dot: REVISAO_DOT.fixo },
  ]
})
function filtrarRevisao(key: string) {
  revisaoFilter.value = revisaoFilter.value === key ? 'all' : key
}

// Painel "Onde agir hoje": os prazos um a um (Abrir), o resto em grupos (Ver).
type AgirItem = { chave: string; titulo: string; detalhe: string; dot: string; acao: string; row?: ChamadoRow; status?: string; revisao?: string }
const TITULO_PRAZO: Partial<Record<RevisaoEstado, string>> = { prazo_hoje: 'Prazo chegou hoje', respondeu: 'Plataforma respondeu' }
const agirItens = computed<AgirItem[]>(() => {
  const prazos = items.value
    .filter((r) => REVISAO_PRECISA_VER.includes(r.revisao_estado))
    .sort((a, b) => REVISAO_PRECISA_VER.indexOf(a.revisao_estado) - REVISAO_PRECISA_VER.indexOf(b.revisao_estado) || (b.revisao_ate || '').localeCompare(a.revisao_ate || ''))
    .map((r) => ({
      chave: r.id,
      titulo: `${TITULO_PRAZO[r.revisao_estado] || `Prazo venceu ${diaMes(r.revisao_ate)}`} · ${r.pedido_bling || r.pedido_marketplace || 'chamado'}`,
      detalhe: [r.conta, r.revisao_motivo].filter(Boolean).join(' · '),
      dot: REVISAO_DOT.prazo_vencido,
      acao: 'Abrir',
      row: r,
    }))
  const faltam = (status: string) => items.value.filter((r) => r.revisao_estado === 'falta' && r.status_aba === status).length
  const grupos: AgirItem[] = [
    { chave: 'analise_humano', titulo: `Análise Humano · ${faltam('analise_humano')} sem revisão hoje`, detalhe: 'o robô não conseguiu — precisa de gente', dot: 'bg-red-600', acao: 'Ver', status: 'analise_humano', revisao: 'falta' },
    { chave: 'encerrado', titulo: `Encerrado · ${faltam('encerrado')} sem revisão hoje`, detalhe: 'falta fechar com lucro/prejuízo', dot: 'bg-amber-600', acao: 'Ver', status: 'encerrado', revisao: 'falta' },
    { chave: 'revisar', titulo: `Revisar · ${revisaoResumo.value.revisar} esperando o robô`, detalhe: 'olhar de novo antes de fechar o dia', dot: 'bg-amber-600', acao: 'Ver', status: 'all', revisao: 'revisar' },
    { chave: 'aguard_plataforma', titulo: `Aguard. Plataforma · ${faltam('aguard_plataforma')} sem revisão hoje`, detalhe: 'ver se a plataforma respondeu', dot: 'bg-sky-600', acao: 'Ver', status: 'aguard_plataforma', revisao: 'falta' },
    { chave: 'analise_robo', titulo: `Análise Robô · ${faltam('analise_robo')} sem revisão hoje`, detalhe: 'ver se o robô agiu', dot: 'bg-indigo-600', acao: 'Ver', status: 'analise_robo', revisao: 'falta' },
  ]
  // 11/11: o que a equipe deu por resolvido vem logo depois dos prazos pra quem conclui.
  if (podeEncerrar.value) {
    grupos.unshift({ chave: 'encerrar', titulo: `Encerrar · ${revisaoResumo.value.encerrar} ${revisaoResumo.value.encerrar === 1 ? 'marcado' : 'marcados'} pela equipe`, detalhe: 'conferir e encerrar com lucro/prejuízo', dot: REVISAO_DOT.encerrar, acao: 'Ver', status: 'all', revisao: 'encerrar' })
  }
  const contagem: Record<string, number> = { revisar: revisaoResumo.value.revisar, encerrar: revisaoResumo.value.encerrar }
  return [...prazos, ...grupos.filter((g) => (g.chave in contagem ? contagem[g.chave] : faltam(g.chave)) > 0)]
})
const fixosItens = computed(() =>
  items.value
    .filter((r) => r.revisao_estado === 'fixo')
    .sort((a, b) => (a.revisao_ate || '').localeCompare(b.revisao_ate || '')),
)
function agir(item: AgirItem) {
  if (item.row) {
    abrirRaio(item.row)
    nextTick(() => document.getElementById(`chamado-${item.row!.id}`)?.scrollIntoView({ block: 'center', behavior: 'smooth' }))
    return
  }
  statusFilter.value = item.status || 'all'
  revisaoFilter.value = item.revisao || 'all'
}

// ----------------------------------------------------------------- Raio-X
const raioRow = computed(() => items.value.find((r) => r.id === raioId.value) || null)
function abrirRaio(row: ChamadoRow) {
  raioId.value = raioId.value === row.id ? null : row.id
}
function limparFiltros() {
  diaEscolhido.value = null
  search.value = ''
  origemFilter.value = 'all'
  plataformaFilter.value = 'all'
  contaFilter.value = 'all'
  statusFilter.value = 'all'
  revisaoFilter.value = 'all'
}
function escolherDia(dia: RevisaoDia) {
  diaEscolhido.value = diaEscolhido.value?.dia === dia.dia ? null : dia
  statusFilter.value = 'all'
  revisaoFilter.value = 'all'
}
const diaResumo = computed(() => {
  const d = diaEscolhido.value
  if (!d) return null
  const abertos = new Set(items.value.map((r) => r.id))
  const naLista = d.faltaram.filter((id) => abertos.has(id)).length
  return { titulo: `Faltaram em ${diaMes(d.dia)}: ${d.faltaram.length} ${d.faltaram.length === 1 ? 'chamado' : 'chamados'}`, concluidos: d.faltaram.length - naLista }
})
function statusLabelDe(codigo: string): string {
  return STATUS_POR_CODIGO[codigo]?.label || codigo
}
async function carregarDias() {
  try {
    diasRevisao.value = await api<RevisaoDias>('/api/chamados/dias')
  } catch {
    diasRevisao.value = null
  }
}
async function fecharDia(dia: RevisaoDia, method: 'POST' | 'DELETE') {
  try {
    await api(`/api/chamados/dias/${dia.dia}/fechar`, { method })
    await carregarDias()
  } catch (e: any) {
    toasts.error(method === 'POST' ? 'Não foi possível fechar o dia' : 'Não foi possível reabrir o dia', apiError(e))
  }
}
// ----------------------------------------------------------------- botão Atualizar
// 09/11/2026 (Vinicius): o Recarregar "não fazia nada" (só relia a lista, sem aviso)
// e virou Atualizar: pede às plataformas, pela API, a leitura de todos os chamados (a
// passada que o robô faz de hora em hora no :25), espera terminar, recarrega a lista
// e diz ao lado o que mudou. Uma leitura a cada 15 min pra empresa inteira: quem clica
// nesse tempo acompanha a que já foi pedida. Casos de TELA ficam com o robô de leitura
// (abrir dezenas de sessões de loja seguidas é risco de bloqueio da conta).
type AtualizarPedido = { enfileirado: boolean; job_id: string | null; pedido_em: string | null; por: string | null; pode_atualizar_em: string | null }
type AtualizarAndamento = { status: string; resumo?: Record<string, number> | null }
const atualizando = ref(false)
const atualizadoTexto = ref('')
let atualizarTimer: ReturnType<typeof setTimeout> | null = null
onBeforeUnmount(() => { if (atualizarTimer) clearTimeout(atualizarTimer) })

function horaSP(d: Date | string | null | undefined): string {
  if (!d) return ''
  return new Date(d).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', timeZone: 'America/Sao_Paulo' })
}
function qtd(n: number, um: string, varios: string): string {
  return `${n} ${n === 1 ? um : varios}`
}
async function recarregarLista() {
  await Promise.all([load(), carregarDias()])
}
async function esperarLeitura(jobId: string): Promise<AtualizarAndamento> {
  const limite = Date.now() + 20 * 60_000
  while (Date.now() < limite) {
    try {
      const st = await api<AtualizarAndamento>(`/api/chamados/atualizar/${encodeURIComponent(jobId)}`)
      if (['complete', 'failed', 'not_found'].includes(st.status)) return st
    } catch {
      // uma consulta que falhou não encerra a espera
    }
    await new Promise<void>((ok) => { atualizarTimer = setTimeout(ok, 3000) })
  }
  return { status: 'demorou' }
}
function textoDaLeitura(p: AtualizarPedido, st: AtualizarAndamento | null): string {
  const agora = `atualizado às ${horaSP(new Date())}`
  const outra = p.enfileirado ? '' : ` · leitura pedida por ${p.por || 'alguém'} às ${horaSP(p.pedido_em)}`
  const proxima = !p.enfileirado && p.pode_atualizar_em ? ` · próxima leitura a partir das ${horaSP(p.pode_atualizar_em)}` : ''
  if (!st || st.status === 'not_found') return `${agora}${outra}${proxima}`
  if (st.status === 'failed') return `${agora} · a leitura das plataformas deu erro (a lista foi recarregada)`
  if (st.status === 'demorou') return `${agora} · a leitura das plataformas ainda não terminou`
  const r = st.resumo || {}
  if (r.pulado_ja_rodando) return `${agora} · o robô do :25 estava lendo agora mesmo`
  const partes = [
    qtd(r.verificados || 0, 'chamado lido na plataforma', 'chamados lidos na plataforma'),
    qtd(r.novos || 0, 'mensagem nova', 'mensagens novas'),
    qtd(r.encerrados || 0, 'encerrado', 'encerrados'),
  ]
  if (r.falhas) partes.push(qtd(r.falhas, 'com erro', 'com erro'))
  return `${agora} · ${partes.join(' · ')}${outra}${proxima}`
}
async function atualizarTudo() {
  if (atualizando.value) return
  atualizando.value = true
  atualizadoTexto.value = ''
  try {
    if (!canEdit.value) {
      await recarregarLista()
      atualizadoTexto.value = `atualizado às ${horaSP(new Date())}`
      return
    }
    let pedido: AtualizarPedido
    try {
      pedido = await api<AtualizarPedido>('/api/chamados/atualizar', { method: 'POST' })
    } catch (e: any) {
      await recarregarLista()
      atualizadoTexto.value = `lista recarregada às ${horaSP(new Date())} · não consegui pedir a leitura às plataformas (${apiError(e)})`
      return
    }
    atualizadoTexto.value = 'lendo as plataformas…'
    const st = pedido.job_id ? await esperarLeitura(pedido.job_id) : null
    await recarregarLista()
    atualizadoTexto.value = textoDaLeitura(pedido, st)
  } finally {
    atualizando.value = false
  }
}

// Marcar várias linhas seguidas: o painel relê uma vez só, quando a pessoa para.
let diasTimer: ReturnType<typeof setTimeout> | null = null
function atualizarDias() {
  if (diasTimer) clearTimeout(diasTimer)
  diasTimer = setTimeout(carregarDias, 800)
}

// Botão Conciliação do histórico (05/11): a Conciliação acha o pedido em todas as lojas
// da aba e vai pra loja dele — o nome da loja no chamado não é o mesmo de lá.
const ABA_CONCILIACAO: Record<string, string> = { 'mercado livre': 'ml', shopee: 'shopee', tiktok: 'tiktok', amazon: 'amazon' }
function linkConciliacao(row: ChamadoRow) {
  const aba = ABA_CONCILIACAO[nomePlataforma(row.plataforma)]
  if (!aba || !row.pedido_bling || !canConciliacao.value) return null
  return { path: '/conciliacao', query: { visao: 'pedidos', tab: aba, loja: 'todas', q: row.pedido_bling } }
}

// Coluna Última ação (09/11/2026, era "Últ. resposta"): a última coisa FEITA no caso —
// a plataforma ou nós falando, a instrução de uma pessoa ou a IA de Chamado agindo na
// tela. Sem nada disso, a data do status (Encerrado/Concluído).
function ultimaTexto(row: ChamadoRow): string {
  const at = row.ultima_acao_at
  if (at) {
    const quando = fmtCurto(at)
    // curto: a coluna tem ~170 px; o que a IA fez vai no balão
    if (row.ultima_acao_tipo === 'ia') return `${quando} · nós · IA`
    if (row.ultima_acao_tipo === 'instrucao') return `${quando} · instrução · ${(row.ultima_acao_autor || '').trim() || 'equipe'}`
    const direcao = row.ultima_acao_tipo === 'plataforma' ? 'recebida' : 'enviada'
    return `${quando} · ${quemFalou(row, direcao, row.ultima_acao_autor).replace(/mercado livre/i, 'ML')}`
  }
  if (row.ultima_resposta_at) return `${fmtCurto(row.ultima_resposta_at)} · ${quemRespondeu(row).replace(/mercado livre/i, 'ML')}`
  return mostraDataStatus(row) ? fmtCurto(row.status_aba_at) : '—'
}
// O balão da coluna: o que foi feito (o começo do texto da IA ou da instrução).
function ultimaTitulo(row: ChamadoRow): string {
  const base = ultimaTexto(row)
  const resumo = (row.ultima_acao_resumo || '').trim()
  return resumo ? `${base}\n${resumo}` : base
}
const rangeStart = computed(() => (total.value === 0 ? 0 : (page.value - 1) * PAGE_SIZE + 1))
const rangeEnd = computed(() => Math.min(page.value * PAGE_SIZE, total.value))

const sheetInputClass = 'h-7 w-full rounded-none border-0 bg-transparent px-1 text-xs focus:bg-background focus:outline-none focus:ring-1 focus:ring-primary disabled:cursor-default disabled:opacity-70'
const sheetSelectClass = `${sheetInputClass} cursor-pointer`

const ERROS: Record<string, string> = {
  estoque_conferindo: 'a entrada no estoque do Bling de um desses lançamentos ainda está sendo conferida — espere até 10 min',
  estoque_estorno_sem_confirmacao: 'o estorno de um desses lançamentos pode ter saído no Bling — confira o extrato e use "Estorno já saiu" em Devoluções antes de excluir',
  chamado_not_found: 'chamado não encontrado',
  revisao_data_invalida: 'escolha uma data depois de hoje',
  revisao_motivo_vazio: 'escreva o motivo',
  revisao_chamado_concluido: 'chamado concluído não entra na revisão do dia',
  chamado_pedido_nao_encontrado: 'pedido não encontrado no DaVinci',
  chamado_mensagem_vazia: 'digite a mensagem',
  chamado_anexo_tipo_invalido: 'anexo precisa ser imagem (png/jpg/webp/gif)',
  chamado_anexo_muito_grande: 'imagem acima de 8 MB',
  chamado_sem_numero: 'informe o nº do chamado antes de enviar pela API',
  chamado_nao_ml: 'fora da devolução, a réplica pela API só existe no Mercado Livre — em Shopee/TikTok responda pelo Seller Center (canal robô ou manual)',
  chamado_sem_integracao_ml: 'conta sem integração ML no DaVinci',
  chamado_encerrado: 'o chamado já está encerrado no Mercado Livre',
  chamado_sem_acao: 'o ML não permite mensagem nesse chamado agora',
  chamado_sem_pedido_bling: 'linha sem pedido Bling',
  chamado_pedido_bling_nao_achado: 'pedido Bling não encontrado no DaVinci',
  chamado_status_bling_desconhecido: 'situação desconhecida no Bling',
  chamado_sem_integracao_bling: 'sem integração Bling',
  chamado_status_bling_erro: 'o Bling recusou a mudança de situação',
  chamado_valor_obrigatorio: 'informe o valor do chamado (lucro ou prejuízo) antes de resolver',
  // 19/09 (Vinicius): com pedido Bling, a nova situação é obrigatória ao resolver.
  chamado_situacao_obrigatoria: 'escolha a nova situação no Bling',
  // 11/11 (Vinicius): a equipe não conclui; marca Encerrar e quem vê todas as lojas conclui.
  chamado_encerrar_so_sem_equipe: 'quem tem equipe não encerra: marque Encerrar na coluna Revisão',
  // 19/09: instrução não entra em chamado Concluído — a pessoa reabre pela aba antes.
  chamado_concluido: 'chamado concluído — reabra pela aba pra instruir o robô',
  chamado_mensagem_abertura: 'a abertura do chamado não pode sair do histórico — sem ela o sistema para de acompanhar',
  chamado_mensagem_na_fila: 'o robô está enviando essa mensagem agora — espere terminar',
  chamado_mensagem_not_found: 'essa mensagem já tinha sido excluída',
  // 21/09 (Vinicius): janela "Excluir chamado" (chamado + lançamentos de devolução).
  devolucoes_delete_forbidden: 'sem permissão pra excluir lançamentos de devolução',
  devolucao_fora_do_pedido: 'lançamento escolhido não é deste pedido — feche e abra a janela de novo',
  sem_destinatarios: 'cadastre os destinatários do jurídico (botão destinatários)',
  threema_nao_configurado: 'Threema não configurado no servidor',
  threema_juridico_desativado: 'Os avisos do Jurídico pelo Threema estão desativados.',
  threema_envio_falhou: 'o Threema não entregou pra nenhum destinatário',
  // abertura automática de devolução no ML (services/chamados_devolucao)
  devolucao_sem_foto: 'aguardando foto na tela Devoluções',
  return_review_indisponivel: 'ML ainda não liberou a revisão da devolução (tenta a cada hora)',
  devolucao_sem_claim: 'sem devolução aberta no ML pra esse pedido (tenta a cada hora)',
  devolucao_sem_return: 'o comprador não abriu devolução na plataforma pra esse pedido (tenta a cada hora)',
  devolucao_sem_pedido_marketplace: 'devolução sem nº do pedido do ML',
  devolucao_prazo_esgotado: 'ficou 45 dias pendente — abrir na mão',
  devolucao_nao_encontrada: 'lançamento da devolução não encontrado',
  tiktok_aguardando_pacote: 'TikTok ainda não liberou a recusa do pacote (tenta a cada hora)',
  tiktok_recusa_bloqueada: 'TikTok ainda não aceita recusar o pacote (em trânsito) — o robô tenta a cada hora; perto do prazo, recusar ou pedir prorrogação na Central do Vendedor',
  tiktok_arbitragem: 'em arbitragem na TikTok',
  tiktok_quick_refund: 'TikTok já reembolsou (quick refund) — só apelação no Seller Center',
  tiktok_ja_recusada: 'já recusada na TikTok (recusa feita no Seller Center — veja o histórico)',
  tiktok_devolucao_encerrada: 'devolução já encerrada na TikTok (reembolsada/decidida) — nada mais a abrir pela API',
  ml_claim_encerrada: 'reclamação já encerrada no ML — nada mais a abrir pela API (veja o histórico)',
  tiktok_motivo_indisponivel: 'TikTok não aceita esse motivo nesse estado',
  tiktok_reembolso_ja_contestado: 'só reembolso já contestado na TikTok — foto/vídeo agora só pela Central do Vendedor (apelação / quando ela pedir prova)',
  tiktok_reembolso_nao_pendente: 'o só reembolso já saiu de pendente na TikTok (veja o desfecho no histórico)',
  tiktok_reembolso_nao_encontrado: 'pedido de só reembolso não encontrado na TikTok',
  shopee_aguardando_pacote: 'Shopee ainda não liberou a disputa (tenta a cada hora)',
  shopee_ja_contestada: 'já contestada na Shopee (disputa feita no Seller Center — veja o histórico)',
  shopee_devolucao_encerrada: 'devolução já encerrada na Shopee',
  shopee_motivo_indisponivel: 'Shopee não oferece esse motivo pra essa devolução',
  shopee_sem_email: 'sem e-mail do operador pra Shopee (DEVOLUCAO_DISPUTE_EMAIL)',
  shopee_prazo_contestacao_esgotado: 'prazo da Shopee pra contestar venceu (validação do vendedor, ~3 dias após receber o pacote)',
  devolucao_motivo_sem_chamado: 'esse motivo de devolução não abre chamado (Item Incorreto saiu da lista em 07/09)',
  plataforma_sem_api_replica: 'nenhum caso esperando a nossa resposta pela API (disputa não tem API) — ficou só no histórico; responda pelo Seller Center',
  plataforma_sem_api: 'plataforma sem API — abrir na mão',
  chamado_sem_integracao_tiktok: 'conta sem integração TikTok no DaVinci',
  chamado_sem_integracao_shopee: 'conta sem integração Shopee no DaVinci',
  // 19/09: a plataforma não liberava pela API e o robô assumiu por outro caminho (Seller Center)
  substituida_pelo_robo: 'o robô assumiu por outro caminho',
  chamado_instrucao_vazia: 'digite a instrução',
}

function apiError(e: any) {
  const detail = e?.data?.detail
  if (detail && typeof detail === 'object') {
    const code = detail.code as string | undefined
    const base = (code && ERROS[code]) || detail.message || code || e?.message || 'erro'
    return detail.erro ? `${base}: ${detail.erro}` : base
  }
  return detail || e?.message || 'erro'
}

function fmtDate(v: string | null) {
  if (!v) return '—'
  const [y, m, d] = v.split('-')
  return y && m && d ? `${d}/${m}/${y.slice(2)}` : v
}

function fmtBRL(v: number | string | null | undefined) {
  if (v === null || v === undefined || v === '') return '—'
  const n = Number(v)
  if (Number.isNaN(n)) return '—'
  return n.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
}

// Coluna "Valor": positivo = lucro, negativo = prejuízo (Eduardo 15/09).
function resultadoTexto(v: number | string | null | undefined) {
  if (v === null || v === undefined || v === '') return ''
  const n = Number(v)
  if (Number.isNaN(n)) return ''
  if (n > 0) return `lucro de ${fmtBRL(n)}`
  if (n < 0) return `prejuízo de ${fmtBRL(Math.abs(n))}`
  return 'sem lucro nem prejuízo (R$ 0,00)'
}

// ─────────────────────────────────────────────────────────── conversa (chat)
// Eduardo 10/09: "coloque organizado, para ficar de fácil entendimento como se
// fosse um whatsapp". O texto que o monitor grava traz a THREAD INTEIRA colada
// (o ML repete todo o histórico a cada e-mail), então uma única mensagem virava
// um paredão ilegível. Aqui a thread é quebrada de volta em falas e cada uma
// vira um balão; falas repetidas em mensagens seguintes aparecem uma vez só.
type Bolha = {
  chave: string
  // 19/09: 'instrucao' = recado de uma pessoa pro robô (não foi pra plataforma).
  // 24/09: 'ia' = decisão da IA de Chamado (com ✓/✗).
  lado: 'nos' | 'eles' | 'sistema' | 'instrucao' | 'ia'
  autor: string
  quando: string
  texto: string
  meta: string
  status: Mensagem['status'] | null
  erro: string | null
  anexos: Anexo[]
  mensagemId?: string
  avaliacao?: AvaliacaoIa | null
  // 25/09 (lixeirinha): a mensagem de onde o balão saiu — uma mensagem com a
  // conversa colada vira vários balões, e excluir leva todos.
  origem?: { id: string; tipo: string; status: Mensagem['status']; direcao: Mensagem['direcao'] }
  // 07/10: evento que pôs o chamado em Encerrado — destacado, com a data à vista.
  encerramento?: boolean
}

// 07/10 (Vinicius, 296512: "consegue colocar no histórico 'encerrado, thatcher tirou
// o motivo'?"): os eventos de Encerrado saíam em cinza miúdo e passavam batido.
// Textos novos começam com "Encerrado: "; os outros são os já gravados.
const RE_ENCERRAMENTO = /^(Encerrado: |Motivo da devolução retirado |Plataforma encerrou o caso|Monitor: plataforma encerrou)|— chamado encerrado;/

// "Mercado Livre" / "Você" seguido da data, do jeito que o ML escreve na página
// do caso e no corpo do e-mail.
const RE_FALA = /^[ \t]*(Você|Mercado Livre|Mercado Pago)[ \t]*\n[ \t]*(\d{1,2} de [a-zç]+(?: de \d{4})?)[ \t]*$/gim

function partirThread(texto: string): { quem: string; quando: string; corpo: string }[] {
  const t = (texto || '').replace(/\r/g, '')
  const marcas = [...t.matchAll(RE_FALA)]
  if (marcas.length < 2) return []
  const out: { quem: string; quando: string; corpo: string }[] = []
  marcas.forEach((m, i) => {
    const ini = (m.index ?? 0) + m[0].length
    const fim = i + 1 < marcas.length ? (marcas[i + 1].index ?? t.length) : t.length
    const corpo = t.slice(ini, fim).trim()
    if (corpo) out.push({ quem: m[1], quando: m[2], corpo })
  })
  return out
}

function rotuloTipo(m: Mensagem): string {
  if (m.direcao === 'sistema') return 'sistema'
  if (m.direcao === 'recebida') return m.tipo === 'analise' ? 'análise do robô' : 'plataforma'
  if (m.tipo === 'replica_auto') return 'réplica automática'
  // 17/09 (Amazon "não tem nem nexo"): nota de plataforma sem API não foi enviada a
  // ninguém — é tarefa pra equipe, não abertura.
  if (m.tipo === 'abertura' && m.erro === 'plataforma_sem_api') return 'tarefa da equipe (sem API)'
  if (m.tipo === 'abertura') return 'abertura na plataforma'
  return 'réplica'
}


function fmtDateTime(v: string | null) {
  if (!v) return '—'
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return v
  return d.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' })
}

// dd/mm hh:mm — só pra célula Status, que precisa ser estreita.
function fmtCurto(v: string | null) {
  if (!v) return ''
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return v
  const dd = String(d.getDate()).padStart(2, '0')
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const hh = String(d.getHours()).padStart(2, '0')
  const mi = String(d.getMinutes()).padStart(2, '0')
  return `${dd}/${mm} ${hh}:${mi}`
}

function origemLabel(o: Origem) {
  return ORIGENS.find((x) => x.value === o)?.label || o
}

function anexoUrl(id: string) {
  // Relativo → o cookie de sessão vai junto no <img src>.
  return `/api/chamados/anexos/${id}`
}

function setBusy(id: string, on: boolean) {
  const s = new Set(busy.value)
  if (on) s.add(id)
  else s.delete(id)
  busy.value = s
}

function replaceRow(updated: ChamadoRow) {
  const i = items.value.findIndex((r) => r.id === updated.id)
  if (i >= 0) items.value[i] = updated
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const params = new URLSearchParams()
    params.set('limit', String(PAGE_SIZE))
    params.set('offset', String((page.value - 1) * PAGE_SIZE))
    if (tab.value === 'juridico') { params.set('juridico', 'true'); params.set('mostrar', 'todos') }
    else params.set('mostrar', mostrar.value)
    if (search.value.trim()) params.set('search', search.value.trim())
    if (origemFilter.value !== 'all') params.set('origem', origemFilter.value)
    if (plataformaFilter.value !== 'all') params.set('plataforma', plataformaFilter.value)
    if (contaFilter.value !== 'all') params.set('conta', contaFilter.value)
    const res = await api<Page>(`/api/chamados?${params.toString()}`)
    items.value = res.items
    ordemRevisao.value = new Map(res.items.map((r) => [r.id, REVISAO_ORDEM[r.revisao_estado] ?? 1]))
    total.value = res.total
    plataformas.value = res.plataformas
    plataformaTotais.value = res.plataforma_totais || {}
    contas.value = res.contas || []
    podeEncerrar.value = !!res.pode_encerrar
  } catch (e: any) {
    error.value = apiError(e)
  } finally {
    loading.value = false
  }
}

async function loadSituacoes() {
  try {
    const res = await api<{ nomes: string[] }>('/api/chamados/situacoes')
    situacoes.value = res.nomes
  } catch {
    situacoes.value = []
  }
}

await Promise.all([load(), loadSituacoes(), carregarDias()])

let searchTimer: ReturnType<typeof setTimeout> | null = null
watch(search, () => {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    page.value = 1
    load()
  }, 300)
})
// Trocou a plataforma → a lista de contas muda; volta pra "todas contas".
// Registrado ANTES do watch geral pra rodar primeiro no mesmo flush (um load só).
watch(plataformaFilter, () => { contaFilter.value = 'all' })
watch([origemFilter, plataformaFilter, contaFilter, mostrar], () => {
  page.value = 1
  load()
})
watch(page, () => load())

watch(tab, (t) => { if (t === 'ia') return; page.value = 1; load() })

// ----------------------------------------------------------------- jurídico
const juridico = reactive({ open: false, row: null as ChamadoRow | null, obs: '', saving: false, erro: null as string | null, destinatarios: [] as string[], semAcesso: false })
const juridicoCfgOpen = ref(false)
async function openJuridico(row: ChamadoRow) {
  juridico.open = true
  juridico.row = row
  juridico.obs = ''
  juridico.erro = null
  juridico.destinatarios = []
  juridico.semAcesso = false
  try {
    const cfg = await api<{ recipients: string[]; destinatarios: { id: string; nome: string }[] }>('/api/informar/juridico')
    juridico.destinatarios = cfg.recipients.map((id) => cfg.destinatarios.find((d) => d.id === id)?.nome || id)
  } catch {
    juridico.semAcesso = true // não-admin não vê o cadastro; o envio usa o cadastro do servidor
  }
}
function closeJuridico() {
  juridico.open = false
  juridico.row = null
}
async function enviarJuridico() {
  const row = juridico.row
  if (!row || !canEdit.value) return
  juridico.saving = true
  juridico.erro = null
  try {
    const r = await api<{ chamado: ChamadoRow; sent: string[]; failed: string[]; link: string }>(`/api/chamados/${row.id}/juridico`, {
      method: 'POST',
      body: { observacao: juridico.obs || null },
    })
    const idx = items.value.findIndex((i) => i.id === row.id)
    if (idx >= 0) items.value[idx] = r.chamado
    if (hist.row?.id === row.id) hist.row = r.chamado
    toasts.success('Encaminhado ao jurídico', `${r.sent.length} destinatário(s) no Threema${r.failed.length ? ` · ${r.failed.length} falhou` : ''}`)
    closeJuridico()
    // Histórico aberto atrás do modal: recarrega pra mostrar o evento do envio.
    if (hist.open && hist.row) hist.mensagens = await api<Mensagem[]>(`/api/chamados/${hist.row.id}/mensagens`)
  } catch (e: any) {
    juridico.erro = apiError(e)
  } finally {
    juridico.saving = false
  }
}
async function copiarLink(link: string) {
  try { await navigator.clipboard.writeText(link); toasts.success('Link do dossiê copiado') } catch { window.prompt('Link do dossiê', link) }
}

// ----------------------------------------------------------------- novo chamado

function openAdd() {
  addOpen.value = true
  lookupPedido.value = ''
  lookupError.value = null
  draft.value = null
}

function closeAdd() {
  addOpen.value = false
  lookupPedido.value = ''
  lookupError.value = null
  draft.value = null
}

async function lookupOrder() {
  const pedido = lookupPedido.value.trim()
  if (!pedido) return
  lookupLoading.value = true
  lookupError.value = null
  draft.value = null
  try {
    const res = await api<Lookup>(`/api/chamados/pedido-lookup?${new URLSearchParams({ pedido })}`)
    draft.value = { ...res, origem: '', canal: 'manual', chamado: '', chamado_url: '', observacao: '' }
  } catch (e: any) {
    if (e?.status === 404 || e?.statusCode === 404) {
      // Não está no espelho: deixa criar na mão com o número digitado.
      draft.value = {
        data: null, pedido_bling: pedido, pedido_marketplace: null, plataforma: null, conta: null,
        produto: null, sku: null, status_bling: null, origem: '', canal: 'manual', chamado: '', chamado_url: '', observacao: '',
      }
      lookupError.value = 'pedido não encontrado no DaVinci — preencha os dados à mão'
    } else {
      lookupError.value = apiError(e)
    }
  } finally {
    lookupLoading.value = false
  }
}

async function createChamado() {
  const d = draft.value
  if (!d || !canEdit.value) return
  if (!d.origem) {
    lookupError.value = 'Selecione a origem (Margem / Logística / Devolução / Vendas).'
    return
  }
  creating.value = true
  lookupError.value = null
  try {
    const created = await api<ChamadoRow>('/api/chamados', {
      method: 'POST',
      body: {
        origem: d.origem,
        data: d.data,
        pedido_bling: d.pedido_bling,
        pedido_marketplace: d.pedido_marketplace,
        plataforma: d.plataforma,
        conta: d.conta,
        produto: d.produto,
        sku: d.sku,
        status_bling: d.status_bling,
        canal: d.canal,
        chamado: d.chamado || null,
        chamado_url: d.chamado_url || null,
        observacao: d.observacao || null,
      },
    })
    if (page.value === 1 && mostrar.value !== 'resolvidos') items.value = [created, ...items.value].slice(0, PAGE_SIZE)
    total.value += 1
    closeAdd()
    toasts.success('Chamado registrado', `Pedido ${created.pedido_bling || created.pedido_marketplace}`)
  } catch (e: any) {
    lookupError.value = apiError(e)
  } finally {
    creating.value = false
  }
}

// ----------------------------------------------------------------- edição inline

function setRowText(row: ChamadoRow, field: 'chamado' | 'observacao', value: string) {
  row[field] = value || null
}

async function saveRow(row: ChamadoRow, extra: Record<string, unknown> = {}): Promise<void> {
  if (!canEdit.value) return
  const id = row.id
  const prev = rowSaveQueue.get(id) ?? Promise.resolve()
  const next = prev
    .catch(() => undefined)
    .then(async () => {
      try {
        const updated = await api<ChamadoRow>(`/api/chamados/${encodeURIComponent(id)}`, {
          method: 'PATCH',
          body: {
            chamado: row.chamado || null,
            canal: row.canal,
            alterar_status_bling: row.alterar_status_bling || null,
            observacao: row.observacao || null,
            valor_recuperado: row.valor_recuperado ?? null,
            ...extra,
          },
        })
        replaceRow(updated)
        error.value = null
      } catch (e: any) {
        error.value = apiError(e)
      }
    })
    .finally(() => {
      if (rowSaveQueue.get(id) === next) rowSaveQueue.delete(id)
    })
  rowSaveQueue.set(id, next)
  await next
}

// 05/11/2026: a marca vale pro caso — a API devolve todas as linhas que mudaram.
async function marcarRevisao(row: ChamadoRow, marca: RevisaoMarca) {
  try {
    const r = await api<{ itens: ChamadoRow[] }>(`/api/chamados/${row.id}/revisao`, {
      method: 'POST',
      body: { tipo: marca.tipo, ate: marca.ate ?? null, motivo: marca.motivo ?? null },
    })
    r.itens.forEach((linha) => {
      replaceRow(linha)
      if (hist.row?.id === linha.id) hist.row = linha
    })
    atualizarDias()
  } catch (e: any) {
    toasts.error('Não foi possível marcar', apiError(e))
  }
}

function salvarRaio(row: ChamadoRow, campos: Record<string, unknown>) {
  Object.assign(row, campos)
  saveRow(row)
}

// Instrução pelo Raio-X: registra e já marca Revisar (é o "escrevi pro robô, volto
// pra ver"). Devolve o erro pro campo do painel.
async function instruirERevisar(row: ChamadoRow, texto: string): Promise<string | null> {
  try {
    const updated = await api<ChamadoRow>(`/api/chamados/${row.id}/instrucao`, { method: 'POST', body: { texto } })
    replaceRow(updated)
    await marcarRevisao(updated, { tipo: 'revisar' })
    toasts.success('Instrução registrada', 'O robô lê na próxima passada. Chamado marcado como Revisar.')
    return null
  } catch (e: any) {
    return apiError(e)
  }
}

async function aplicarStatusBling(row: ChamadoRow) {
  const alvo = (row.alterar_status_bling || '').trim()
  if (!alvo) {
    toasts.info('Escolha a situação', 'Selecione a situação do Bling antes de aplicar.')
    return
  }
  const atual = row.status_bling_atual || row.status_bling || '(desconhecida)'
  if (!confirm(`Mudar a situação do pedido ${row.pedido_bling} no Bling?\n\n${atual} → ${alvo}`)) return
  setBusy(row.id, true)
  try {
    await api(`/api/chamados/${row.id}/alterar-status-bling`, { method: 'POST', body: { situacao: alvo } })
    row.status_bling = alvo
    row.status_bling_atual = alvo
    row.mensagens_total += 1
    toasts.success('Bling atualizado', `Pedido ${row.pedido_bling} → ${alvo}`)
  } catch (e: any) {
    toasts.error('Não foi possível alterar no Bling', apiError(e))
  } finally {
    setBusy(row.id, false)
  }
}

// ----------------------------------------------------------------- histórico + réplica

const hist = reactive({
  open: false,
  row: null as ChamadoRow | null,
  loading: false,
  mensagens: [] as Mensagem[],
  texto: '',
  files: [] as File[],
  sending: false,
  erro: null as string | null,
  // 19/09: instrução pro robô — separada da réplica porque NÃO vai pra plataforma.
  instrucao: '',
  instrucaoSending: false,
  instrucaoErro: null as string | null,
})

// 15/09 (Eduardo: "preciso de todo o contexto da conversa"): a conversa COMPLETA
// da página do caso (desde a abertura na plataforma) chega numa mensagem
// `historico`. Cada fala entra na linha do tempo pela data dela, junto das notas
// e análises do DaVinci; a mensagem solta igual a uma fala (última resposta do ML,
// réplica já enviada) não aparece de novo.
const normTxt = (t: string) => (t || '').replace(/\s+/g, ' ').trim().toLowerCase()
const MESES: Record<string, number> = {
  janeiro: 0, fevereiro: 1, 'março': 2, marco: 2, abril: 3, maio: 4, junho: 5,
  julho: 6, agosto: 7, setembro: 8, outubro: 9, novembro: 10, dezembro: 11,
}
function dataDaFala(quando: string, ref: Date): number {
  const m = /(\d{1,2}) de ([a-zç]+)(?: de (\d{4}))?/i.exec(quando || '')
  const mes = m ? MESES[m[2].toLowerCase()] : undefined
  if (!m || mes === undefined) return ref.getTime()
  let ano = m[3] ? Number(m[3]) : ref.getFullYear()
  if (!m[3] && mes > ref.getMonth()) ano -= 1
  return new Date(ano, mes, Number(m[1])).getTime()
}
const RE_CAB_FALA = /^\s*(Mercado Livre|Mercado Pago|Você)\s+\d{1,2} de [a-zç]+(?: de \d{4})?\s*/i

const origemDe = (m: Mensagem): Bolha['origem'] => ({ id: m.id, tipo: m.tipo, status: m.status, direcao: m.direcao })

const bolhas = computed<Bolha[]>(() => {
  const itens: { t: number; ord: number; b: Bolha }[] = []
  const vistas = new Set<string>()
  const corposHist = new Set<string>()
  const hoje = new Date()
  let ord = 0
  const pushFala = (m: Mensagem, f: { quem: string; quando: string; corpo: string }, i: number, ultima: boolean) => {
    const chave = `${f.quem}|${f.quando}|${normTxt(f.corpo).slice(0, 120)}`
    if (vistas.has(chave)) return
    vistas.add(chave)
    itens.push({
      t: dataDaFala(f.quando, hoje),
      ord: ord++,
      b: {
        chave: `${m.id}-${i}`,
        lado: f.quem === 'Você' ? 'nos' : 'eles',
        autor: f.quem === 'Você' ? 'nós' : f.quem,
        quando: f.quando,
        texto: f.corpo,
        meta: m.tipo === 'historico' ? 'conversa na plataforma' : '',
        status: ultima ? m.status : null,
        erro: ultima ? m.erro : null,
        anexos: ultima ? m.anexos : [],
        origem: origemDe(m),
      },
    })
  }
  // 1º o histórico completo: as falas dele viram a referência de "já mostrado"
  const falasHistInteiras: string[] = []
  for (const m of hist.mensagens) {
    if (m.tipo !== 'historico') continue
    const falas = partirThread(m.texto)
    falas.forEach((f, i) => {
      corposHist.add(normTxt(f.corpo).slice(0, 100))
      falasHistInteiras.push(normTxt(f.corpo))
      pushFala(m, f, i, false)
    })
  }
  for (const m of hist.mensagens) {
    if (m.tipo === 'historico') continue
    const t = new Date(m.enviada_at || m.created_at).getTime()
    if (m.tipo === 'instrucao') {
      itens.push({
        t, ord: ord++,
        b: {
          chave: m.id, lado: 'instrucao', autor: m.autor_nome || 'nós',
          quando: fmtDateTime(m.created_at), texto: m.texto, meta: 'instrução pra IA de Chamado',
          status: null, erro: null, anexos: m.anexos, origem: origemDe(m),
        },
      })
      continue
    }
    if (m.da_ia) {
      itens.push({
        t, ord: ord++,
        b: {
          chave: m.id, lado: 'ia', autor: m.autor_nome || 'IA de Chamado',
          quando: fmtDateTime(m.created_at), texto: m.texto, meta: 'decisão',
          status: null, erro: null, anexos: m.anexos,
          mensagemId: m.id, avaliacao: m.avaliacao_ia ?? null, origem: origemDe(m),
        },
      })
      continue
    }
    if (m.direcao === 'sistema') {
      itens.push({
        t, ord: ord++,
        b: {
          chave: m.id, lado: 'sistema', autor: m.autor_nome || 'sistema',
          quando: fmtDateTime(m.created_at), texto: m.texto, meta: 'sistema',
          status: null, erro: null, anexos: m.anexos, origem: origemDe(m),
          encerramento: RE_ENCERRAMENTO.test(m.texto),
        },
      })
      continue
    }
    const falas = partirThread(m.texto)
    if (!falas.length) {
      // 15/09: resposta do ML gravada pelo e-mail vem sem o cumprimento (começa no
      // meio da fala: "Sobre o pedido de protocolo…") — conta como já mostrada se
      // o começo dela está DENTRO de alguma fala da conversa completa.
      const inicio = normTxt(m.texto.replace(RE_CAB_FALA, ''))
      const trecho = inicio.slice(0, 60)
      const jaNaConversa = corposHist.size > 0
        && m.tipo !== 'analise'
        && (m.direcao === 'recebida' || m.status === 'enviada')
        && (corposHist.has(inicio.slice(0, 100))
          || (trecho.length >= 30 && falasHistInteiras.some(c => c.includes(trecho))))
      if (jaNaConversa) continue
      itens.push({
        t, ord: ord++,
        b: {
          chave: m.id,
          lado: m.direcao === 'recebida' ? 'eles' : 'nos',
          autor: m.autor_nome || (m.direcao === 'recebida' ? 'plataforma' : 'nós'),
          quando: fmtDateTime(m.enviada_at || m.created_at),
          texto: m.texto, meta: rotuloTipo(m), status: m.status, erro: m.erro, anexos: m.anexos,
          origem: origemDe(m),
        },
      })
      continue
    }
    falas.forEach((f, i) => pushFala(m, f, i, i === falas.length - 1))
  }
  itens.sort((a, b) => a.t - b.t || a.ord - b.ord)
  return itens.map(x => x.b)
})

// 09/11/2026 (dono, pelo Vinicius): o histórico em duas colunas — à esquerda a conversa
// com a plataforma (o problema, ela, nós e os avisos), à direita tudo da IA de Chamado
// (decisões, o que ela fez, instruções da equipe e os avisos dela, tipo "parou no
// captcha"). As duas na ordem de data, a mais nova embaixo, e abrem lá embaixo.
function daColunaIa(b: Bolha): boolean {
  if (b.lado === 'ia' || b.lado === 'instrucao') return true
  if (b.lado !== 'sistema') return false
  return b.origem?.tipo === 'analise' || /^IA de Chamado\b/i.test((b.texto || '').trim())
}
const colunasHist = computed(() => {
  const conversa: Bolha[] = []
  const ia: Bolha[] = []
  for (const b of bolhas.value) (daColunaIa(b) ? ia : conversa).push(b)
  return [
    { chave: 'conversa', titulo: 'Conversa com a plataforma', vazio: 'sem mensagens ainda', bolhas: conversa },
    { chave: 'ia', titulo: 'IA de Chamado', vazio: 'a IA ainda não analisou este caso', bolhas: ia },
  ]
})
const colunaHistEls: Record<string, HTMLElement | null> = {}
function rolarHistoricoProFim() {
  nextTick(() => {
    for (const el of Object.values(colunaHistEls)) if (el) el.scrollTop = el.scrollHeight
  })
}
// Abriu, carregou ou chegou mensagem nova (réplica, instrução, Atualizar): os dois lados
// vão pra mais nova.
watch(() => [hist.open, hist.loading, hist.row?.id, bolhas.value.length], () => {
  if (hist.open && !hist.loading) rolarHistoricoProFim()
})

async function openHistorico(row: ChamadoRow, focoReplica = false) {
  hist.open = true
  hist.row = row
  hist.texto = ''
  hist.files = []
  hist.erro = null
  hist.instrucao = ''
  hist.instrucaoErro = null
  hist.mensagens = []
  hist.loading = true
  try {
    hist.mensagens = await api<Mensagem[]>(`/api/chamados/${row.id}/mensagens`)
  } catch (e: any) {
    hist.erro = apiError(e)
  } finally {
    hist.loading = false
  }
  if (focoReplica) {
    // 19/09: a réplica agora vive num balão — "focar" é abrir o balão.
    await nextTick()
    replicaBalao.value = true
  }
}

// 21/10/2026 (Pós-venda × e-mail, decisão 3 do Eduardo): bloco NOVO, só leitura, com os
// e-mails ligados ao chamado — de uma pasta com a categoria "Chamado (Pós-venda)" ou
// ligados à mão. Não mexe no histórico nem em nada da aba (regra do dono: só acrescentar).
// O texto protegido (links de acesso fora, código mascarado; texto puro, nenhum link
// clicável) vem de GET /api/chamados/{id}/emails, só pra quem lê aquele e-mail no
// Atendimento (loja no escopo da equipe); os outros veem a data, a pasta e a loja.
type EmailAnexo = { id: string; filename?: string; content_type?: string; tamanho: number }
type EmailLigado = {
  id: string
  chamado_id: string | null
  recebido_em: string | null
  pasta: string | null
  loja: string | null
  plataforma: string | null
  pedido: string | null
  pode_ler: boolean
  // o remetente que imita a plataforma (o mesmo aviso do cartão do Atendimento)
  suspeito?: boolean
  suspeito_motivos?: string[]
  de?: string
  de_nome?: string
  assunto?: string
  texto?: string
  links_removidos?: number
  codigo_mascarado?: boolean
  anexos?: EmailAnexo[]
}
const emailsLigados = reactive({ chamadoId: null as string | null, itens: [] as EmailLigado[], ocultos: 0 })

async function carregarEmailsLigados(chamadoId: string | null) {
  emailsLigados.chamadoId = chamadoId
  emailsLigados.itens = []
  emailsLigados.ocultos = 0
  if (!chamadoId) return
  try {
    const r = await api<{ itens: EmailLigado[]; ocultos: number }>(`/api/chamados/${chamadoId}/emails`)
    // o modal pode ter trocado de chamado enquanto carregava
    if (emailsLigados.chamadoId !== chamadoId) return
    emailsLigados.itens = r.itens || []
    emailsLigados.ocultos = r.ocultos || 0
  } catch {
    // sem os e-mails o histórico continua igual: o bloco só não aparece
  }
}

// Abriu (ou trocou) o histórico de um chamado → carrega os e-mails dele; fechou → limpa.
watch(
  () => (hist.open && hist.row ? hist.row.id : null),
  (id) => { void carregarEmailsLigados(id) },
)

function emailAnexoUrl(id: string) {
  return `/api/chamados/emails/anexos/${id}`
}

function protecaoDoEmail(e: EmailLigado): string {
  const partes: string[] = []
  if (e.codigo_mascarado) partes.push('código mascarado')
  if (e.links_removidos) partes.push(`${e.links_removidos} link(s) de acesso removido(s)`)
  return partes.join(' · ')
}

// 23/09 (Vinicius): a Observação da coluna também dentro do histórico — mesmo
// campo, mesmo balão (ObservacaoPopover) e mesmo PATCH da linha. Depois de salvar,
// o histórico passa a apontar pra linha nova da tabela (o replaceRow troca o
// objeto), senão a próxima edição sairia de uma cópia velha.
async function salvarObservacaoHist() {
  const row = hist.row
  if (!row) return
  await saveRow(row)
  const atual = items.value.find(r => r.id === row.id)
  if (atual && hist.row?.id === row.id) hist.row = atual
}

function closeHistorico() {
  hist.open = false
  hist.row = null
  replicaBalao.value = false
  instrucaoBalao.value = false
}

function onReplicaFiles(ev: Event) {
  const input = ev.target as HTMLInputElement
  const list = Array.from(input.files || [])
  input.value = ''
  hist.files = [...hist.files, ...list]
}

function removeReplicaFile(i: number) {
  hist.files = hist.files.filter((_, idx) => idx !== i)
}

async function enviarReplica() {
  const row = hist.row
  if (!row || !canEdit.value) return
  const texto = hist.texto.trim()
  if (!texto) {
    hist.erro = 'digite a mensagem'
    return
  }
  hist.sending = true
  hist.erro = null
  try {
    const fd = new FormData()
    fd.append('texto', texto)
    for (const f of hist.files) fd.append('files', f)
    const m = await api<Mensagem>(`/api/chamados/${row.id}/mensagens`, { method: 'POST', body: fd })
    hist.mensagens = [...hist.mensagens, m]
    hist.texto = ''
    hist.files = []
    row.mensagens_total += 1
    row.ultima_mensagem_at = m.created_at
    if (m.status === 'enviada' || m.status === 'registrada') {
      row.ultima_resposta_at = m.enviada_at || m.created_at
      row.ultima_resposta_direcao = 'enviada'
      row.ultima_resposta_autor = m.autor_nome
    }
    // Atualiza a coluna Status sem recarregar (19/09, códigos novos): na fila do robô →
    // Análise Robô; falhou na hora → Análise Humano; saiu/registrada → Aguard. Plataforma.
    // Encerrado/Concluído não mudam por uma réplica — a decisão da plataforma manda.
    if (!row.resolvido && (row.status_aba === 'analise_humano' || row.status_aba === 'analise_robo' || row.status_aba === 'aguard_plataforma')) {
      row.status_aba = m.status === 'pendente' ? 'analise_robo' : m.status === 'falhou' ? 'analise_humano' : 'aguard_plataforma'
      row.status_aba_motivo = m.status === 'pendente' ? 'na fila do robô' : m.status === 'falhou' ? `envio falhou: ${ERROS[m.erro || ''] || m.erro || ''}` : null
      row.status_aba_at = m.enviada_at || m.created_at
    }
    if (m.status === 'falhou') toasts.warning('Réplica registrada, mas o envio falhou', ERROS[m.erro || ''] || m.erro || '')
    else if (m.status === 'pendente') toasts.info('Réplica na fila do robô', 'Será enviada na próxima passada.')
    else if (m.status === 'enviada') toasts.success('Réplica enviada', 'Mensagem entregue na plataforma.')
    else toasts.success('Réplica registrada', 'Canal manual: só no histórico.')
  } catch (e: any) {
    hist.erro = apiError(e)
  } finally {
    hist.sending = false
  }
}

// 19/09: instrução pro robô. Não vai pra plataforma — vira mensagem tipo `instrucao`
// no histórico e o chamado cai em Análise Robô até o robô passar e responder aqui.
const relendo = ref(false)
// "Reler agora" (22/09): o cron relê a plataforma de hora em hora, no minuto 25.
// Quando a TikTok está com prazo correndo ("sem resposta, aprova o reembolso
// sozinha"), esperar a próxima janela é caro — este botão lê na hora.
// ----------------------------------------------- consulta do Portal (25/09)

const consultaDraft = ref('')
const consultaErro = ref<string | null>(null)
const salvandoConsulta = ref(false)
watch(
  () => [hist.row?.id, hist.row?.consulta_portal],
  () => {
    consultaDraft.value = hist.row?.consulta_portal || ''
    consultaErro.value = null
  },
)

function ehShopee(row: ChamadoRow): boolean {
  return /shopee/i.test(row.plataforma || '')
}

async function salvarConsulta() {
  const row = hist.row
  if (!row || !canEdit.value || salvandoConsulta.value) return
  const valor = consultaDraft.value.trim()
  if (valor === (row.consulta_portal || '')) return
  salvandoConsulta.value = true
  consultaErro.value = null
  try {
    const updated = await api<ChamadoRow>(`/api/chamados/${row.id}`, {
      method: 'PATCH',
      body: { consulta_portal: valor },
    })
    replaceRow(updated)
    hist.row = updated
    if (updated.consulta_portal) {
      toasts.success('Consulta ligada ao chamado', 'O robô de leitura passa a ler essa consulta no Portal e traz a resposta pra cá.')
    }
  } catch (e: any) {
    consultaErro.value = 'cole o link ou o número da consulta'
  } finally {
    salvandoConsulta.value = false
  }
}

// Caso do suporte da Amazon: mesma régua de `chamados_leitura.e_caso_amazon`.
function casoAmazon(row: ChamadoRow): boolean {
  return (row.plataforma ?? '').trim().toLowerCase() === 'amazon'
    && /^\d{9,14}$/.test((row.chamado ?? '').trim())
}

async function relerAgora() {
  const row = hist.row
  if (!row || !canEdit.value || relendo.value) return
  relendo.value = true
  try {
    const updated = await api<ChamadoRow>(`/api/chamados/${row.id}/reler`, { method: 'POST' })
    replaceRow(updated)
    hist.row = updated
    hist.mensagens = await api<Mensagem[]>(`/api/chamados/${updated.id}/mensagens`)
    // Caso aberto na tela não tem API pra consultar: aqui o botão só fura a fila
    // do robô de leitura. Dizer "relido" seria mentira — nada foi lido ainda.
    if (updated.chamado_de_tela) {
      toasts.success('Pus este caso na frente da fila', 'O robô lê a tela da plataforma na próxima passada e o que ele achar aparece aqui.')
    } else if (updated.consulta_portal) {
      toasts.success('Caso relido na plataforma', 'E a consulta do Portal foi pra frente da fila do robô de leitura — o que ele achar aparece aqui.')
    } else {
      toasts.success('Caso relido na plataforma', 'O que chegou de novo está no histórico abaixo.')
    }
  } catch (e: any) {
    toasts.error('Não consegui reler o caso', apiError(e))
  } finally {
    relendo.value = false
  }
}

async function enviarInstrucao() {
  const row = hist.row
  if (!row || !canEdit.value) return
  const texto = hist.instrucao.trim()
  if (!texto) {
    hist.instrucaoErro = ERROS.chamado_instrucao_vazia
    return
  }
  hist.instrucaoSending = true
  hist.instrucaoErro = null
  try {
    const updated = await api<ChamadoRow>(`/api/chamados/${row.id}/instrucao`, { method: 'POST', body: { texto } })
    replaceRow(updated)
    hist.row = updated
    hist.instrucao = ''
    // A instrução entra na linha do tempo pelo próprio histórico (tipo `instrucao`).
    hist.mensagens = await api<Mensagem[]>(`/api/chamados/${updated.id}/mensagens`)
    toasts.success('Instrução registrada', 'O robô lê na próxima passada e responde aqui no histórico.')
  } catch (e: any) {
    hist.instrucaoErro = apiError(e)
  } finally {
    hist.instrucaoSending = false
  }
}

// 19/09 (Vinicius): "réplica manual muito grande; campo pequeno que abre o balão,
// igual às observações; histórico aproveita o espaço". O rodapé do histórico virou
// uma faixa em duas colunas (réplica | instrução), cada uma com um campo de UMA
// linha que só mostra o começo do rascunho; clicar abre um balão (mesmo padrão do
// ObservacaoPopover) com a textarea grande. O rascunho é o mesmo hist.texto /
// hist.instrucao — fechar o balão não apaga nada. Enter não envia: só os botões.
const replicaBalao = ref(false)
const instrucaoBalao = ref(false)
const replicaCaixa = ref<HTMLTextAreaElement | null>(null)
const instrucaoCaixa = ref<HTMLTextAreaElement | null>(null)
// Foco na caixa com o cursor no fim (o padrão deixava no começo).
function focarCaixa(e: Event, el: HTMLTextAreaElement | null) {
  e.preventDefault()
  if (!el) return
  el.focus()
  const fim = el.value.length
  el.setSelectionRange(fim, fim)
}
// Enviar de dentro do balão: deu certo (rascunho limpo, sem erro) → o balão fecha.
async function enviarReplicaDoBalao() {
  await enviarReplica()
  if (!hist.erro) replicaBalao.value = false
}
async function enviarInstrucaoDoBalao() {
  await enviarInstrucao()
  if (!hist.instrucaoErro) instrucaoBalao.value = false
}

// Links dentro da fala viram clicáveis (22/09): a TikTok manda o vídeo e as
// fotos do comprador como URL no meio do texto, e copiar e colar à mão numa
// tela de atendimento é pedir pra ninguém abrir. Nada de v-html — o texto vem
// da plataforma, então ele é sempre renderizado como texto; só a URL vira <a>.
const RE_URL = /(https?:\/\/[^\s|]+)/g
function partesComLink(texto: string): { t: 'txt' | 'url'; v: string }[] {
  const out: { t: 'txt' | 'url'; v: string }[] = []
  let ultimo = 0
  for (const m of (texto || '').matchAll(RE_URL)) {
    const i = m.index ?? 0
    if (i > ultimo) out.push({ t: 'txt', v: texto.slice(ultimo, i) })
    out.push({ t: 'url', v: m[0] })
    ultimo = i + m[0].length
  }
  if (ultimo < (texto || '').length) out.push({ t: 'txt', v: texto.slice(ultimo) })
  return out
}

function statusMensagemClass(s: Mensagem['status']) {
  return {
    registrada: 'bg-muted text-muted-foreground',
    pendente: 'bg-amber-500/15 text-amber-700 dark:text-amber-300',
    enviada: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300',
    falhou: 'bg-red-500/15 text-red-600 dark:text-red-300',
  }[s]
}

// ----------------------------------------------------------------- resolver

const resolver = reactive({
  open: false,
  row: null as ChamadoRow | null,
  situacao: '' as string,
  // Valor OBRIGATÓRIO ao resolver (Eduardo 15/09): lucro ou prejuízo + quanto.
  tipo: 'lucro' as 'lucro' | 'prejuizo',
  valor: '' as string,
  // 23/09 (Vinicius): a MESMA observação da coluna — vem preenchida e grava de volta;
  // o texto também fica no evento "resolvido" do histórico, pra analisar depois.
  observacao: '' as string,
  // 19/09: veio da sugestão do robô/plataforma (valor_sugerido), não da coluna Valor.
  sugerido: false,
  saving: false,
  erro: null as string | null,
  // 24/09 (Vinicius: "vou fechar o chamado e quero colocar se a IA acertou ou errou"):
  // a última decisão da IA neste chamado e a avaliação escolhida na janela.
  ia: null as Mensagem | null,
  iaAval: '' as '' | 'certo' | 'errado',
  iaCorrecao: '',
})

// Valor assinado que vai pra API (negativo = prejuízo); null = inválido/vazio.
const resolverValor = computed<number | null>(() => {
  const raw = String(resolver.valor ?? '').trim().replace(',', '.')
  if (!raw) return null
  const n = Number(raw)
  if (Number.isNaN(n) || n < 0) return null
  return resolver.tipo === 'prejuizo' ? -n : n
})
const resolverValorOk = computed(() => resolverValor.value !== null)
// 19/09 (Vinicius: "status atual do Bling e o que vai trocar, obrigatório"): com
// pedido Bling a nova situação é obrigatória — sem "não alterar". Sem pedido Bling
// não há o que trocar e o select nem aparece.
const resolverExigeSituacao = computed(() => !!resolver.row?.pedido_bling)
const resolverSituacaoOk = computed(() => !resolverExigeSituacao.value || !!resolver.situacao)
// Situação atual no Bling pra pessoa ver de onde está saindo: a viva (lookup) ou a
// gravada no chamado.
const resolverSituacaoAtual = computed(() => resolver.row?.status_bling_atual || resolver.row?.status_bling || '—')

function opcoesFechamento(row: ChamadoRow): string[] {
  return FECHAMENTO[row.origem] || []
}

function openResolver(row: ChamadoRow) {
  resolver.open = true
  resolver.row = row
  resolver.situacao = ''
  resolver.observacao = row.observacao || ''
  resolver.erro = null
  // Pré-preenche com o que já está na coluna Valor (negativo = prejuízo). Coluna vazia
  // e o robô/plataforma sugeriu um resultado (19/09) → entra a sugestão; a pessoa confirma.
  let atual = row.valor_recuperado === null || row.valor_recuperado === undefined ? NaN : Number(row.valor_recuperado)
  resolver.sugerido = false
  if (Number.isNaN(atual) && row.valor_sugerido !== null && row.valor_sugerido !== undefined && row.valor_sugerido !== '') {
    const sug = Number(row.valor_sugerido)
    if (!Number.isNaN(sug)) { atual = sug; resolver.sugerido = true }
  }
  resolver.tipo = !Number.isNaN(atual) && atual < 0 ? 'prejuizo' : 'lucro'
  resolver.valor = Number.isNaN(atual) ? '' : String(Math.abs(atual))
  resolver.ia = null
  resolver.iaAval = ''
  resolver.iaCorrecao = ''
  carregarDecisaoDaIa(row)
  nextTick(() => (document.getElementById('resolver-valor') as HTMLInputElement | null)?.focus())
}

// 24/09: a última decisão da IA neste chamado (do histórico já aberto, ou busca).
async function carregarDecisaoDaIa(row: ChamadoRow) {
  try {
    const msgs = hist.open && hist.row?.id === row.id
      ? hist.mensagens
      : await api<Mensagem[]>(`/api/chamados/${row.id}/mensagens`)
    if (resolver.row?.id !== row.id) return
    const ultima = [...msgs].reverse().find(m => m.da_ia) || null
    resolver.ia = ultima
    if (ultima?.avaliacao_ia) {
      resolver.iaAval = ultima.avaliacao_ia.certo ? 'certo' : 'errado'
      resolver.iaCorrecao = ultima.avaliacao_ia.correcao || ''
    }
  } catch { /* sem a decisão da IA a janela funciona igual */ }
}

// Texto curto da decisão: sem o "Análise da IA de Chamado [classe]:" do começo.
function resumoDaIa(texto: string) {
  return texto.replace(/^Análise d[aoe] [^[]*\[[^\]]*\]:\s*/, '')
}

// ✓/✗ numa decisão da IA. `refazer`: no histórico (chamado aberto) o ✗ manda a IA
// refazer; ao fechar pela janela Resolver, fica só o aprendizado.
async function avaliarIa(mensagemId: string, certo: boolean, correcao: string, refazer: boolean) {
  await api(`/api/chamados/ia/decisoes/${mensagemId}/avaliacao`, {
    method: 'PUT',
    body: certo ? { certo: true } : { certo: false, correcao, refazer },
  })
}
const histIa = reactive({ corrigindo: null as string | null, correcao: '', salvando: null as string | null, erro: null as string | null })
async function avaliarNoHistorico(b: Bolha, certo: boolean) {
  if (!b.mensagemId || !hist.row) return
  if (!certo && !histIa.correcao.trim()) return
  histIa.salvando = b.mensagemId
  histIa.erro = null
  try {
    await avaliarIa(b.mensagemId, certo, histIa.correcao.trim(), true)
    histIa.corrigindo = null
    histIa.correcao = ''
    hist.mensagens = await api<Mensagem[]>(`/api/chamados/${hist.row.id}/mensagens`)
  } catch (e: any) {
    histIa.erro = apiError(e)
  } finally {
    histIa.salvando = null
  }
}
// 25/09 (Vinicius: "quero excluir uma mensagem do histórico"): a lixeirinha
// ESCONDE — some da tela e da IA de Chamado; a linha fica no banco pra fala da
// plataforma não voltar na próxima leitura e o robô não repetir resposta. A
// abertura e mensagem nossa que o robô está postando agora (enviando) não têm
// lixeira (a API recusa também). 30/09 (298394): a réplica ainda PENDENTE tem — a
// lixeira a cancela (o robô não manda), pra trocar o texto antes de sair.
const histExcluindo = ref<string | null>(null)
function podeLixeira(b: Bolha): boolean {
  const o = b.origem
  if (!canDelete.value || !o || o.tipo === 'abertura') return false
  return !(o.direcao === 'enviada' && (o.status as string) === 'enviando')
}
async function excluirMensagem(b: Bolha) {
  const o = b.origem
  if (!o || !hist.row || histExcluindo.value) return
  const juntas = bolhas.value.filter(x => x.origem?.id === o.id).length
  const naFila = o.direcao === 'enviada' && o.status === 'pendente'
  const aviso = naFila
    ? [
        'Cancelar esta réplica?',
        'Ela ainda não saiu: o robô não vai mandar. Para trocar o texto, escreva a nova réplica depois de cancelar.',
      ].join('\n\n')
    : [
        'Excluir esta mensagem do histórico?',
        juntas > 1 ? `Ela chegou junto com outras ${juntas - 1} fala(s) na mesma leitura — todas somem juntas.` : '',
        'Some da tela e a IA de Chamado não lê mais. O que já foi pra plataforma não é desfeito.',
      ].filter(Boolean).join('\n\n')
  if (!confirm(aviso)) return
  histExcluindo.value = o.id
  try {
    await api(`/api/chamados/mensagens/${o.id}`, { method: 'DELETE' })
    hist.mensagens = await api<Mensagem[]>(`/api/chamados/${hist.row.id}/mensagens`)
    toasts.success(naFila ? 'Réplica cancelada — não vai sair' : 'Mensagem excluída do histórico')
  } catch (e: any) {
    toasts.error('Não consegui excluir a mensagem', apiError(e))
  } finally {
    histExcluindo.value = null
  }
}
async function desfazerNoHistorico(b: Bolha) {
  if (!b.mensagemId || !hist.row) return
  histIa.salvando = b.mensagemId
  try {
    await api(`/api/chamados/ia/decisoes/${b.mensagemId}/avaliacao`, { method: 'DELETE' })
    hist.mensagens = await api<Mensagem[]>(`/api/chamados/${hist.row.id}/mensagens`)
  } catch (e: any) {
    histIa.erro = apiError(e)
  } finally {
    histIa.salvando = null
  }
}

function closeResolver() {
  resolver.open = false
  resolver.row = null
}

async function confirmarResolver() {
  const row = resolver.row
  if (!row || !canEdit.value) return
  const valor = resolverValor.value
  if (valor === null) {
    resolver.erro = ERROS.chamado_valor_obrigatorio
    return
  }
  if (!resolverSituacaoOk.value) {
    resolver.erro = ERROS.chamado_situacao_obrigatoria
    return
  }
  if (resolver.iaAval === 'errado' && !resolver.iaCorrecao.trim()) {
    resolver.erro = 'Diga o que era o certo pra IA aprender (ou deixe sem avaliar).'
    return
  }
  resolver.saving = true
  resolver.erro = null
  const ia = resolver.ia
  const iaAval = resolver.iaAval
  const iaCorrecao = resolver.iaCorrecao.trim()
  try {
    const updated = await api<ChamadoRow>(`/api/chamados/${row.id}/resolver`, {
      method: 'POST',
      body: {
        resolvido: true,
        situacao: resolver.situacao || null,
        valor_recuperado: valor,
        observacao: resolver.observacao.trim() || null,
      },
    })
    if (mostrar.value === 'abertos') {
      items.value = items.value.filter((r) => r.id !== row.id)
      total.value = Math.max(0, total.value - 1)
    } else {
      replaceRow(updated)
    }
    toasts.success(
      'Chamado resolvido',
      [resultadoTexto(valor), resolver.situacao ? `Bling → ${resolver.situacao}` : ''].filter(Boolean).join(' · '),
    )
    // 24/09: avaliação da IA dada ao fechar — só aprendizado (o chamado acabou de fechar)
    const jaIgual = ia?.avaliacao_ia
      && (ia.avaliacao_ia.certo ? 'certo' : 'errado') === iaAval
      && (iaAval === 'certo' || (ia.avaliacao_ia.correcao || '') === iaCorrecao)
    if (ia && iaAval && !jaIgual) {
      avaliarIa(ia.id, iaAval === 'certo', iaCorrecao, false)
        .catch(() => toasts.error('Avaliação da IA não foi salva', 'O chamado foi resolvido; marque acertou/errou pela aba IA de Chamado.'))
    }
    closeResolver()
    // 19/09: resolvido de dentro do histórico → o histórico fecha junto (a linha já
    // saiu da lista de abertos; não faz sentido continuar olhando um chamado concluído).
    if (hist.open && hist.row?.id === row.id) closeHistorico()
  } catch (e: any) {
    resolver.erro = apiError(e)
  } finally {
    resolver.saving = false
  }
}

async function reabrir(row: ChamadoRow) {
  if (!confirm(`Reabrir o chamado do pedido ${row.pedido_bling || row.pedido_marketplace}?`)) return
  setBusy(row.id, true)
  try {
    const updated = await api<ChamadoRow>(`/api/chamados/${row.id}/resolver`, { method: 'POST', body: { resolvido: false } })
    if (mostrar.value === 'resolvidos') {
      items.value = items.value.filter((r) => r.id !== row.id)
      total.value = Math.max(0, total.value - 1)
    } else {
      replaceRow(updated)
    }
  } catch (e: any) {
    toasts.error('Erro ao reabrir', apiError(e))
  } finally {
    setBusy(row.id, false)
  }
}

// ----------------------------------------------------------------- excluir

// 21/09 (Vinicius): a lixeira (lista e histórico) abre esta janela no lugar do
// confirm(): junto com o chamado vão os lançamentos de devolução do mesmo pedido
// que a pessoa marcar, e a nova situação no Bling é obrigatória igual ao resolver.
// O back troca o Bling ANTES de apagar — se o Bling recusar, nada some. O que
// pode ou não (permissão de devoluções, estoque estornável, o que já vem marcado)
// é a prévia do back que diz, não a tela.
const excluir = reactive({
  open: false,
  row: null as ChamadoRow | null,
  loading: false,
  preview: null as ExclusaoPreview | null,
  situacao: '' as string,
  selecionados: new Set<string>(),
  saving: false,
  erro: null as string | null,
})

// Antes da prévia chegar vale a regra do resolver (tem pedido Bling → exige).
const excluirExigeSituacao = computed(() => (excluir.preview ? excluir.preview.exige_situacao : !!excluir.row?.pedido_bling))
const excluirSituacaoOk = computed(() => !excluirExigeSituacao.value || !!excluir.situacao)
const excluirSituacaoAtual = computed(
  () => excluir.preview?.status_bling_atual || excluir.row?.status_bling_atual || excluir.row?.status_bling || '—',
)
const excluirPodeLancamentos = computed(() => excluir.preview?.pode_excluir_lancamentos === true)

// Não limpa `erro`: depois de um estorno recusado a prévia recarrega e o aviso fica.
async function carregarExclusao(row: ChamadoRow) {
  excluir.loading = true
  try {
    const p = await api<ExclusaoPreview>(`/api/chamados/${row.id}/exclusao`)
    // fechou ou trocou de chamado no meio → resposta velha não entra
    if (excluir.row?.id !== row.id) return
    excluir.preview = p
    excluir.selecionados = new Set(
      p.pode_excluir_lancamentos ? p.lancamentos.filter((l) => l.marcado_padrao).map((l) => l.id) : [],
    )
  } catch (e: any) {
    if (excluir.row?.id === row.id) excluir.erro = apiError(e)
  } finally {
    if (excluir.row?.id === row.id) excluir.loading = false
  }
}

function openExcluir(row: ChamadoRow) {
  excluir.open = true
  excluir.row = row
  excluir.preview = null
  excluir.situacao = ''
  excluir.selecionados = new Set()
  excluir.erro = null
  void carregarExclusao(row)
}

function closeExcluir() {
  // No meio do POST não fecha: no 502 de estorno a situação no Bling já mudou e
  // parte dos lançamentos já saiu — a pessoa precisa ver isso na própria janela.
  if (excluir.saving) return
  excluir.open = false
  excluir.row = null
  excluir.preview = null
}

// Nome da plataforma pro aviso ("na Shopee", não "na SHOPEE").
const PLATAFORMA_NA: Record<string, string> = {
  'mercado livre': 'no Mercado Livre',
  shopee: 'na Shopee',
  tiktok: 'na TikTok',
  amazon: 'na Amazon',
}
function plataformaNa(p: string | null | undefined): string {
  return PLATAFORMA_NA[nomePlataforma(p)] || 'na plataforma'
}

function toggleLancamento(id: string) {
  if (excluir.selecionados.has(id)) excluir.selecionados.delete(id)
  else excluir.selecionados.add(id)
}

function lancamentoTexto(l: ExclusaoLancamento) {
  const produto = [l.sku, l.produtos].filter(Boolean).join(' · ')
  const estado = [l.condicao_produto, l.motivo_devolucao].filter(Boolean).join(' / ')
  return [produto, estado].filter(Boolean).join(' · ') || '(lançamento sem produto)'
}

// 26/10 (caso 300978): linhas com prejuízo automático na tela Reembolso — ao
// excluir, o DaVinci apaga o que ele lançou (se ninguém mexeu).
const CONDICOES_COM_PREJUIZO = ['Extraviado', 'Sucata', 'Manutenção']

// Linha embaixo da caixinha: o que acontece com o estoque desse lançamento ao excluir.
function estoqueTexto(l: ExclusaoLancamento): { texto: string; alerta: boolean } {
  if (!l.data_devolvido_estoque) return { texto: 'estoque não devolvido', alerta: false }
  const quando = fmtDateTime(l.data_devolvido_estoque)
  // A data é do pedido; o resultado da entrada no Bling vem em estoque_status (15/10).
  // Estorno anterior que pode ter saído: não dá para excluir daqui ainda.
  if (l.estoque_status === 'sem_confirmacao' && l.estoque_estornavel) {
    return { texto: 'o estorno anterior pode ter saído no Bling — confira o extrato e use "Estorno já saiu" em Devoluções; não dá para excluir daqui ainda', alerta: true }
  }
  // Entrada anterior ainda não estornada vale primeiro (o estorno sai ao excluir).
  if (l.estoque_status && l.estoque_status !== 'ok' && l.estoque_estornavel) {
    return {
      texto: `há uma entrada anterior de ${l.estoque_mov_qty ?? '?'} un. de ${l.estoque_mov_sku || l.sku || '?'} — ao excluir, o Bling recebe a saída automaticamente`,
      alerta: true,
    }
  }
  if (l.estoque_status === 'falhou') return { texto: `a entrada no Bling falhou (pedida em ${quando}) — nada a estornar`, alerta: false }
  if (l.estoque_status === 'conferindo') return { texto: 'a entrada no Bling ainda está sendo conferida — espere até 10 min para excluir', alerta: true }
  if (l.estoque_status === 'sem_confirmacao') return { texto: `a entrada no Bling não foi confirmada (pedida em ${quando}) — confira o extrato; se entrou, ajuste direto no Bling`, alerta: true }
  if (l.estoque_estornavel) {
    const qty = l.estoque_mov_qty ?? '?'
    const sku = l.estoque_mov_sku || l.sku || '?'
    return {
      texto: `estoque devolvido em ${quando} — ao excluir, o Bling recebe a saída de ${qty} un. de ${sku} automaticamente`,
      alerta: true,
    }
  }
  if (l.estoque_mov_sku) {
    // Movimento registrado e já estornado (a pessoa desligou "devolver estoque" antes).
    return { texto: `estoque devolvido em ${quando} e já estornado no Bling — nada a fazer`, alerta: false }
  }
  return { texto: `estoque devolvido em ${quando}, sem registro do movimento — não dá pra estornar daqui`, alerta: true }
}

async function confirmarExcluir() {
  const row = excluir.row
  if (!row || !canDelete.value) return
  if (!excluirSituacaoOk.value) {
    excluir.erro = ERROS.chamado_situacao_obrigatoria
    return
  }
  excluir.saving = true
  excluir.erro = null
  try {
    const res = await api<ExcluirOut>(`/api/chamados/${row.id}/excluir`, {
      method: 'POST',
      body: { devolucoes: [...excluir.selecionados], situacao: excluir.situacao || null },
    })
    items.value = items.value.filter((r) => r.id !== row.id)
    total.value = Math.max(0, total.value - 1)
    const n = res.lancamentos_excluidos
    const estornos = res.estornos.map((x) => `${x.sku || '?'} ×${x.qty ?? '?'}`).join(', ')
    const np = res.prejuizos_apagados?.qtd || 0
    toasts.success(
      'Chamado excluído',
      [
        n ? `${n} lançamento${n > 1 ? 's' : ''} removido${n > 1 ? 's' : ''}` : '',
        estornos ? `estoque estornado: ${estornos}` : '',
        np ? `${np} prejuízo${np > 1 ? 's' : ''} apagado${np > 1 ? 's' : ''} (${fmtBRL(res.prejuizos_apagados?.total)})` : '',
        res.situacao ? `Bling → ${res.situacao}` : '',
      ].filter(Boolean).join(' · '),
    )
    // Prejuízo que alguém já tinha mexido (valor lançado, conferido, protocolo) fica — aviso até fechar.
    const mantidos = res.prejuizos_mantidos || []
    if (mantidos.length) {
      toasts.push({
        kind: 'warning',
        title: 'Prejuízos que ficaram na tela Reembolso',
        lines: mantidos.map((m) => {
          const valor = m.valor ?? m.prejuizo
          return `${m.tipo || ''}${valor != null ? ` de ${fmtBRL(valor)}` : ''}: ${m.motivo || 'alguém mexeu nele'}`
        }),
      }, 0)
    }
    closeExcluir()
    // Excluído de dentro do histórico → o histórico fecha junto (o chamado não existe mais).
    if (hist.open && hist.row?.id === row.id) closeHistorico()
  } catch (e: any) {
    const detail = e?.data?.detail
    if (detail?.code === 'estoque_estorno_falhou') {
      // O Bling recusou o estorno de um lançamento: os anteriores já saíram, o chamado
      // ficou e a situação no Bling JÁ foi trocada (vem antes das exclusões). A prévia
      // recarrega pra mostrar só o que sobrou.
      const n = Number(detail.lancamentos_excluidos || 0)
      const ja = `${n} lançamento${n === 1 ? '' : 's'} já excluído${n === 1 ? '' : 's'}`
      excluir.erro = `${detail.message || 'o Bling recusou o estorno do estoque'} · ${ja}; o chamado ficou`
      if (excluir.situacao) {
        row.status_bling = excluir.situacao
        row.status_bling_atual = excluir.situacao
      }
      excluir.saving = false
      await carregarExclusao(row)
      return
    }
    excluir.erro = apiError(e)
  } finally {
    excluir.saving = false
  }
}
</script>

<template>
  <div class="chamados-panel min-w-0 space-y-2.5">
    <!-- 05/11/2026: padrão da Conciliação. A aba Jurídico saiu em 30/09 e a coluna em
         05/11 (Vinicius: "não usamos"); os dados e o modal continuam no código. -->
    <div v-if="modo !== 'juridico'" class="flex flex-wrap items-center gap-3 rounded-md border bg-card px-3 py-1.5">
      <h1 class="text-[15px] font-bold">Chamados</h1>
      <div class="flex flex-wrap items-center gap-3.5 text-xs font-semibold">
        <div v-if="tab === 'chamados'" class="flex flex-wrap gap-0.5">
          <button type="button" class="border-b-2 px-2.5 py-1" :class="plataformaFilter === 'all' ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'" @click="plataformaFilter = 'all'">
            Todas <span class="font-medium text-muted-foreground">{{ plataformaTotal }}</span>
          </button>
          <button v-for="p in plataformaAbas" :key="p.value" type="button" class="border-b-2 px-2.5 py-1" :class="plataformaFilter === p.value ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'" @click="plataformaFilter = p.value">
            {{ p.label }} <span class="font-medium text-muted-foreground">{{ p.n }}</span>
          </button>
        </div>
        <div class="inline-flex rounded-md border p-px">
          <button type="button" class="rounded px-2.5 py-1" :class="tab === 'chamados' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground'" @click="tab = 'chamados'">Chamados</button>
          <button type="button" class="rounded px-2.5 py-1" :class="tab === 'ia' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground'" @click="tab = 'ia'">IA de Chamado</button>
        </div>
      </div>
      <div class="ml-auto flex items-center justify-end gap-1.5 text-xs font-semibold">
        <span v-if="atualizadoTexto" class="max-w-[560px] truncate text-[11px] font-normal text-muted-foreground" :title="atualizadoTexto">{{ atualizadoTexto }}</span>
        <button
          type="button"
          class="inline-flex items-center gap-1 rounded-md border px-2.5 py-1 hover:bg-muted disabled:opacity-50"
          :disabled="loading || atualizando"
          :title="canEdit
            ? 'Lê agora, pela API, os chamados da Shopee, TikTok e Mercado Livre (o robô faz isso sozinho de hora em hora, no :25) e recarrega a lista. Os casos lidos pela tela (abertos no Seller Center, Amazon, consulta do Portal) seguem com o robô de leitura: pra um deles, use o Atualizar do histórico.'
            : 'Recarrega a lista'"
          @click="atualizarTudo"
        >
          <RotateCcw class="size-3.5" :class="{ 'animate-spin': loading || atualizando }" /> Atualizar
        </button>
        <button type="button" class="inline-flex items-center gap-1 whitespace-nowrap rounded-md bg-primary px-2.5 py-1 text-primary-foreground disabled:opacity-50" :disabled="!canEdit" @click="openAdd">
          <Plus class="size-3.5" /> novo chamado
        </button>
      </div>
    </div>
    <div v-else class="flex flex-wrap items-center gap-2">
      <span class="text-xs text-muted-foreground">Tudo que foi encaminhado ao jurídico pela tela de Chamados (abertos e resolvidos).</span>
      <div class="ml-auto flex items-center gap-2">
        <Button v-if="isAdmin" size="sm" variant="outline" @click="juridicoCfgOpen = true">destinatários</Button>
        <Button size="sm" variant="outline" :disabled="loading" @click="load">
          <RotateCcw class="size-4 mr-1.5" :class="{ 'animate-spin': loading }" />
          atualizar
        </Button>
      </div>
    </div>

    <div v-if="tab === 'ia' && podeAvisoIa" class="flex flex-wrap items-center justify-end gap-2">
      <span class="text-xs text-muted-foreground">Se a IA parar num "não sou robô" ou login, avisa no Threema.</span>
      <Button size="sm" variant="outline" @click="avisoIaCfgOpen = true">quem recebe o aviso</Button>
    </div>

    <div v-if="error" class="flex items-center gap-2 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-400">
      <AlertCircle class="size-4" />
      {{ error }}
    </div>

    <!-- novo chamado -->
    <div v-if="addOpen" class="rounded-md border bg-background">
      <div class="flex flex-wrap items-end gap-3 border-b px-3 py-3">
        <label class="min-w-0 w-full sm:w-auto space-y-1">
          <span class="text-[11px] font-medium text-muted-foreground">Pedido</span>
          <div class="relative">
            <Search class="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
            <input
              v-model="lookupPedido"
              class="h-9 w-full sm:w-72 rounded-md border bg-background pl-8 pr-3 text-sm"
              placeholder="nº Bling ou marketplace"
              @keydown.enter.prevent="lookupOrder"
            />
          </div>
        </label>
        <Button size="sm" :disabled="lookupLoading || !lookupPedido.trim()" @click="lookupOrder">
          <Loader2 v-if="lookupLoading" class="size-4 mr-1.5 animate-spin" />
          <Search v-else class="size-4 mr-1.5" />
          buscar
        </Button>
        <Button size="sm" variant="ghost" @click="closeAdd">
          <X class="size-4 mr-1.5" />
          fechar
        </Button>
        <span v-if="lookupError" class="text-sm text-amber-600 dark:text-amber-400">{{ lookupError }}</span>
      </div>

      <div v-if="draft" class="overflow-auto">
        <table class="min-w-[1500px] w-full text-xs border-collapse">
          <thead class="bg-background">
            <tr>
              <th class="px-2 py-1 text-left text-[11px] font-semibold border-b" colspan="8">Identificação (do DaVinci)</th>
              <th class="px-2 py-1 text-center text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-amber-50 dark:bg-amber-900/20" colspan="4">Chamado</th>
              <th class="px-2 py-1 text-right text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600" colspan="1">Ação</th>
            </tr>
            <tr>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap">Data</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap">Pedido Bling</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap">Pedido Marketplace</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap">Plataforma</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[220px]">Produto</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap">SKU</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap">Conta</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap">Status Bling</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[130px] bg-amber-50 dark:bg-amber-900/20 border-l-[3px] border-gray-400 dark:border-gray-600">Origem</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[130px] bg-amber-50 dark:bg-amber-900/20">Canal</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[140px] bg-amber-50 dark:bg-amber-900/20">Nº chamado</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[200px] bg-amber-50 dark:bg-amber-900/20">Observação</th>
              <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap border-l-[3px] border-gray-400 dark:border-gray-600"></th>
            </tr>
          </thead>
          <tbody>
            <tr class="border-t">
              <td class="px-2 py-1 whitespace-nowrap text-muted-foreground">{{ fmtDate(draft.data) }}</td>
              <td class="px-2 py-1 font-mono">{{ draft.pedido_bling || '—' }}</td>
              <td class="px-2 py-1 font-mono text-muted-foreground">{{ draft.pedido_marketplace || '—' }}</td>
              <td class="px-2 py-1 uppercase">{{ nomePlataforma(draft.plataforma) || '—' }}</td>
              <td class="px-2 py-1 max-w-[320px] truncate" :title="draft.produto || ''">{{ draft.produto || '—' }}</td>
              <td class="px-2 py-1 font-mono">{{ draft.sku || '—' }}</td>
              <td class="px-2 py-1">{{ draft.conta || '—' }}</td>
              <td class="px-2 py-1 text-muted-foreground">{{ draft.status_bling || '—' }}</td>
              <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10 border-l-[3px] border-gray-400 dark:border-gray-600">
                <select v-model="draft.origem" :class="[sheetSelectClass, !draft.origem ? 'ring-1 ring-red-500/60' : '']">
                  <option value="">— obrigatório</option>
                  <option v-for="o in ORIGENS" :key="o.value" :value="o.value">{{ o.label }}</option>
                </select>
              </td>
              <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
                <select v-model="draft.canal" :class="sheetSelectClass">
                  <option v-for="c in CANAIS" :key="c.value" :value="c.value" :title="c.hint">{{ c.label }}</option>
                </select>
              </td>
              <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
                <input v-model="draft.chamado" :class="sheetInputClass" placeholder="protocolo / claim" />
              </td>
              <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
                <input v-model="draft.observacao" :class="sheetInputClass" />
              </td>
              <td class="px-2 py-1 text-right border-l-[3px] border-gray-400 dark:border-gray-600">
                <Button size="sm" :disabled="creating || !canEdit || !draft.origem" @click="createChamado">
                  <Loader2 v-if="creating" class="size-4 mr-1.5 animate-spin" />
                  <Plus v-else class="size-4 mr-1.5" />
                  adicionar
                </Button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <ChamadosIa v-if="tab === 'ia'" :can-edit="canEdit" />

    <template v-if="tab !== 'ia'">
    <!-- filtros (mesmo formato da Conciliação); a plataforma virou aba lá em cima -->
    <div class="flex flex-wrap items-center gap-1.5 text-xs">
      <label class="inline-flex h-7 items-center gap-1 rounded-md border bg-card px-2">
        <span class="font-semibold text-muted-foreground">Origem</span>
        <select v-model="origemFilter" class="bg-transparent outline-none">
          <option value="all">Todas</option>
          <option v-for="o in ORIGENS" :key="o.value" :value="o.value">{{ o.label }}</option>
        </select>
      </label>
      <label class="inline-flex h-7 items-center gap-1 rounded-md border bg-card px-2" title="filtrar por conta (ex. ML Aguiar 2)">
        <span class="font-semibold text-muted-foreground">Conta</span>
        <select v-model="contaFilter" class="max-w-[150px] bg-transparent outline-none">
          <option value="all">Todas</option>
          <option v-for="c in contas" :key="c" :value="c">{{ c }}</option>
        </select>
      </label>
      <label class="inline-flex h-7 items-center gap-1 rounded-md border bg-card px-2">
        <span class="font-semibold text-muted-foreground">Mostrar</span>
        <select v-model="mostrar" class="bg-transparent outline-none">
          <option value="abertos">Abertos</option>
          <option value="resolvidos">Concluídos</option>
          <option value="todos">Todos</option>
        </select>
      </label>
      <label class="inline-flex h-7 items-center gap-1 rounded-md border bg-card px-2" title="filtrar pela coluna Status">
        <span class="font-semibold text-muted-foreground">Status</span>
        <select v-model="statusFilter" class="bg-transparent outline-none">
          <option value="all">Todos</option>
          <option v-for="s in STATUS_ABA" :key="s.value" :value="s.value">{{ s.label }}</option>
        </select>
      </label>
      <label class="inline-flex h-7 items-center gap-1 rounded-md border bg-card px-2" title="filtrar pela coluna Revisão">
        <span class="font-semibold text-muted-foreground">Revisão</span>
        <select v-model="revisaoFilter" class="bg-transparent outline-none">
          <option value="all">Todas</option>
          <option v-for="f in REVISAO_FILTROS" :key="f.value" :value="f.value">{{ f.label }}</option>
        </select>
      </label>
      <label class="inline-flex h-7 min-w-0 items-center gap-1 rounded-md border bg-card px-2">
        <Search class="size-3.5 text-muted-foreground" />
        <input v-model="search" class="w-64 bg-transparent outline-none" placeholder="buscar pedido, conta, produto, chamado" />
      </label>
      <button type="button" class="text-muted-foreground underline" @click="limparFiltros">limpar</button>
      <span class="text-muted-foreground">{{ visiveis.length }} de {{ total }} chamados</span>
    </div>

    <!-- resumo (18/09): total do servidor + grupos da coluna Status na página carregada; clicar filtra -->
    <div v-if="tab === 'chamados'" class="grid grid-cols-2 gap-2" :class="resumoGrupos.length > 4 ? 'lg:grid-cols-6' : 'lg:grid-cols-5'">
      <button type="button" class="rounded-md border bg-card px-3 py-1.5 text-center hover:bg-muted/40 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary" :title="resumoPorOrigem || 'mostrar todos os status'" @click="statusFilter = 'all'">
        <span class="block text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">{{ resumoTotalLabel }}</span>
        <span class="block text-xl font-bold leading-tight">{{ total }}</span>
      </button>
      <button
        v-for="g in resumoGrupos"
        :key="g.key"
        type="button"
        class="rounded-md border bg-card px-3 py-1.5 text-center hover:bg-muted/40 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
        :class="statusFilter === g.key ? 'ring-2 ring-primary' : ''"
        :title="statusFilter === g.key ? 'tirar o filtro' : `filtrar: ${g.label} — ${g.hint}`"
        @click="filtrarGrupo(g.key)"
      >
        <span class="block text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">{{ g.label }}</span>
        <span class="block text-xl font-bold leading-tight" :class="g.cor">{{ g.n }}</span>
      </button>
    </div>

    <!-- revisão do dia: clicar numa etiqueta filtra a lista -->
    <div v-if="tab === 'chamados' && mostrar === 'abertos'" class="flex flex-wrap items-center gap-3 rounded-md border bg-card px-3 py-1.5 text-xs">
      <span class="text-[13px] font-bold">Revisão de {{ hojeRotulo }}</span>
      <span class="inline-block h-2.5 w-60 overflow-hidden rounded-full bg-muted"><span class="block h-full bg-[#1f3a5f] dark:bg-slate-300" :style="{ width: `${revisaoPct}%` }" /></span>
      <span><b>{{ revisaoResumo.feitos }} de {{ revisaoResumo.total }}</b> revisados</span>
      <button
        v-for="p in revisaoPills"
        :key="p.key"
        type="button"
        class="inline-flex items-center gap-1.5 rounded bg-muted px-2 py-0.5 font-semibold focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
        :class="revisaoFilter === p.key ? 'ring-2 ring-primary' : ''"
        :title="revisaoFilter === p.key ? 'tirar o filtro' : `filtrar: ${p.label}`"
        @click="filtrarRevisao(p.key)"
      >
        <span class="size-[7px] rounded-full" :class="p.dot" />
        {{ p.n }} {{ p.label }}
      </button>
    </div>

    <div class="grid items-stretch gap-3 xl:grid-cols-[minmax(0,1fr)_360px]">
      <div class="flex min-w-0 flex-col gap-3">
      <div class="flex min-w-0 flex-col rounded-md border bg-card">
        <div class="relative flex items-center justify-center rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-xs font-semibold text-white">
          <span>Chamados — uma linha por caso</span>
          <span class="absolute right-3 hidden text-[11px] font-normal text-slate-300 2xl:block">quem está com a bola × o que já foi revisado hoje</span>
        </div>
        <div v-if="diaResumo" class="flex flex-wrap items-center gap-2.5 border-b border-blue-200 bg-blue-50 px-3 py-1.5 text-xs dark:border-blue-900 dark:bg-blue-950/30">
          <b>{{ diaResumo.titulo }}</b>
          <span class="text-muted-foreground">a Revisão mostra como estão hoje<template v-if="diaResumo.concluidos"> · {{ diaResumo.concluidos }} já {{ diaResumo.concluidos === 1 ? 'concluído' : 'concluídos' }}</template></span>
          <button type="button" class="ml-auto text-primary underline" @click="diaEscolhido = null">voltar pra lista de hoje</button>
        </div>
        <!-- 10 linhas de 40px + o cabeçalho: o painel de meses fica à vista; o resto rola aqui dentro. -->
        <div class="max-h-[448px] overflow-auto focus:outline-none" tabindex="0">
          <!-- 09/11 (Vinicius): Equipe logo depois da Data; a Observação saiu da tabela (ficava
               esmagada) e mora só no Raio-X. min-w = colunas fixas ÷ (1 − soma das %). -->
          <table class="w-full min-w-[1393px] table-fixed border-collapse text-[11.5px]">
            <colgroup>
              <col class="w-[48px]"><col class="w-[68px]"><col class="w-[60px]"><col class="w-[146px]"><col class="w-[96px]"><col class="w-[17.5%]">
              <col class="w-[70px]"><col class="w-[104px]"><col class="w-[46px]"><col class="w-[130px]"><col class="w-[12%]">
              <col class="w-[214px]">
            </colgroup>
            <thead class="sticky top-0 z-10 bg-muted text-[10px] uppercase tracking-wide text-muted-foreground">
              <tr>
                <th class="border-b px-1.5 py-1 text-center font-semibold" colspan="6">Identificação</th>
                <th class="border-b border-l border-l-slate-300 px-1.5 py-1 text-center font-semibold dark:border-l-slate-600" colspan="5">Chamado</th>
                <th class="sticky right-0 border-b border-l border-l-slate-300 bg-muted px-1.5 py-1 text-center font-semibold dark:border-l-slate-600">Hoje</th>
              </tr>
              <tr>
                <th class="border-b px-1.5 py-1 text-center font-semibold">Data</th>
                <th class="border-b px-1.5 py-1 text-center font-semibold" title="Equipe de vendas da loja do pedido (Lojas › Equipe)">Equipe</th>
                <th class="border-b px-1.5 py-1 text-center font-semibold">Pedido</th>
                <th class="border-b px-1.5 py-1 text-center font-semibold">Pedido marketplace</th>
                <th class="border-b px-1.5 py-1 text-center font-semibold">Conta</th>
                <th class="border-b px-1.5 py-1 text-left font-semibold">Produto</th>
                <th class="border-b border-l border-l-slate-300 px-1.5 py-1 text-center font-semibold dark:border-l-slate-600">Origem</th>
                <th class="border-b px-1.5 py-1 text-center font-semibold">Protocolo</th>
                <th class="border-b px-1.5 py-1 text-center font-semibold">Msgs</th>
                <th class="border-b px-1.5 py-1 text-center font-semibold" title="Quem está com a bola (o porquê fica no tooltip do chip)">Status</th>
                <th class="border-b px-1.5 py-1 text-center font-semibold" title="A última coisa feita no caso: a plataforma ou nós falando, a instrução de uma pessoa ou a IA de Chamado agindo na tela (a conferência sem novidade não conta)">Última ação</th>
                <th class="sticky right-0 border-b border-l border-l-slate-300 bg-muted px-1.5 py-1 text-center font-semibold dark:border-l-slate-600">Revisão</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="loading && !items.length">
                <td colspan="12" class="py-8 text-center text-muted-foreground">
                  <Loader2 class="size-4 inline animate-spin mr-1.5" />
                  carregando…
                </td>
              </tr>
              <tr v-else-if="!visiveis.length">
                <td colspan="12" class="py-8 text-center text-muted-foreground">{{ items.length ? 'nenhum chamado com esse filtro nesta página' : 'sem chamados' }}</td>
              </tr>
              <tr
                v-for="row in visiveis"
                :id="`chamado-${row.id}`"
                :key="row.id"
                class="h-10 cursor-pointer border-b hover:bg-muted/40"
                :class="[row.id === raioId ? 'bg-blue-50 shadow-[inset_3px_0_0_hsl(var(--primary))] dark:bg-blue-950/30' : '', row.resolvido ? 'opacity-60' : '']"
                @click="abrirRaio(row)"
              >
                <td class="px-1.5 text-center text-muted-foreground" :title="fmtDate(row.data)">{{ fmtDate(row.data).slice(0, 5) }}</td>
                <td class="truncate px-1.5 text-center" :title="row.equipes?.length ? row.equipes.join(' · ') : 'sem equipe: a loja do pedido não tem equipe em Lojas'">{{ row.equipes?.length ? row.equipes.join(' · ') : '—' }}</td>
                <td class="px-1.5 text-center font-mono font-bold">{{ row.pedido_bling || '—' }}</td>
                <!-- Cairo 06/11: usam esse número pra procurar; um clique seleciona pra copiar, sem abrir o Raio-X. -->
                <td class="px-1.5 text-center font-mono"><span class="cursor-text select-all" @click.stop>{{ row.pedido_marketplace || '—' }}</span></td>
                <td class="truncate px-1.5 text-center" :title="row.conta || ''">{{ row.conta || '—' }}</td>
                <td class="truncate px-1.5" :title="row.produto || ''">{{ row.produto || '—' }}</td>
                <td class="border-l border-l-slate-300 px-1.5 text-center dark:border-l-slate-600">{{ origemLabel(row.origem) }}</td>
                <td class="truncate px-1.5 text-center" :title="row.chamado || ''">
                  <a v-if="row.chamado && row.chamado_url" :href="row.chamado_url" target="_blank" rel="noopener" class="hover:underline" title="abrir na plataforma" @click.stop>{{ row.chamado }}</a>
                  <template v-else>{{ row.chamado || '—' }}</template>
                </td>
                <td class="px-1.5 text-center">
                  <button type="button" class="inline-flex w-9 items-center justify-center gap-0.5 rounded border py-px text-[10.5px] text-muted-foreground hover:bg-muted" title="histórico do chamado e resposta à plataforma" @click.stop="openHistorico(row)">
                    <History class="size-2.5" />
                    {{ row.mensagens_total }}
                  </button>
                </td>
                <td class="px-1.5 text-center">
                  <span class="inline-block w-[124px] rounded py-0.5 text-center text-[11px] font-semibold" :class="statusInfo(row).cls" :title="statusTitle(row)">{{ statusInfo(row).label }}</span>
                </td>
                <td class="truncate px-1.5 text-center text-[11px] text-muted-foreground" :title="ultimaTitulo(row)">{{ ultimaTexto(row) }}</td>
                <!-- fica presa à direita: em tela menor a tabela rola, mas a Revisão continua à vista -->
                <td class="sticky right-0 border-l border-l-slate-300 px-1.5 text-center dark:border-l-slate-600" :class="row.id === raioId ? 'bg-blue-50 dark:bg-blue-950' : 'bg-card'" @click.stop>
                  <ChamadoRevisaoSeletor v-if="!row.resolvido" :revisao="row" :disabled="!canEdit" @marcar="(m) => marcarRevisao(row, m)" />
                  <span v-else-if="row.resolvido_at" class="text-[11px] tabular-nums text-muted-foreground" :title="`fechado em ${fmtDateTime(row.resolvido_at)}`">fechado {{ fmtCurto(row.resolvido_at) }}</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="mt-auto border-t px-3 py-1.5 text-center text-[11px] text-muted-foreground">Clique na linha abre o Raio-X do chamado à direita. Conferido e Revisar valem só no dia; Fixo vale até a data. Cada dia a lista recomeça.</div>
      </div>
      <ChamadosDias v-if="tab === 'chamados' && diasRevisao" :dados="diasRevisao" :escolhido="diaEscolhido?.dia || null" :status-label="statusLabelDe" @escolher="escolherDia" @fechar="(d) => fecharDia(d, 'POST')" @reabrir="(d) => fecharDia(d, 'DELETE')" />
      </div>

      <div class="flex min-w-0 flex-col gap-3">
        <ChamadoRaioX
          v-if="raioRow"
          :row="raioRow"
          :can-edit="canEdit"
          :can-delete="canDelete"
          :pode-encerrar="podeEncerrar"
          :busy="busy.has(raioRow.id)"
          :origem="origemLabel(raioRow.origem)"
          :status-label="statusInfo(raioRow).label"
          :status-cls="statusInfo(raioRow).cls"
          :status-titulo="statusTitle(raioRow)"
          :ultima-resposta="ultimaTexto(raioRow)"
          :ultima-resposta-titulo="ultimaTitulo(raioRow)"
          :canais="CANAIS"
          :situacoes="situacoes"
          :instruir="(texto: string) => instruirERevisar(raioRow!, texto)"
          class="flex-1"
          @fechar="raioId = null"
          @marcar="(m) => marcarRevisao(raioRow!, m)"
          @historico="openHistorico(raioRow)"
          @salvar="(c) => salvarRaio(raioRow!, c)"
          @aplicar-bling="aplicarStatusBling(raioRow)"
          @resolver="openResolver(raioRow)"
          @reabrir="reabrir(raioRow)"
          @excluir="openExcluir(raioRow)"
        />
        <template v-else-if="mostrar === 'abertos'">
          <div class="flex min-w-0 flex-col rounded-md border bg-card">
            <div class="rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-center text-xs font-semibold text-white">Onde agir hoje</div>
            <div class="space-y-1.5 p-2">
              <div v-for="a in agirItens" :key="a.chave" class="grid grid-cols-[12px_minmax(0,1fr)_58px] items-center gap-2 rounded-md border px-2.5 py-1.5 text-xs">
                <span class="size-[7px] rounded-full" :class="a.dot" />
                <div class="min-w-0">
                  <div class="truncate font-bold" :title="a.titulo">{{ a.titulo }}</div>
                  <div class="truncate text-[11px] text-muted-foreground" :title="a.detalhe">{{ a.detalhe }}</div>
                </div>
                <button type="button" class="h-6 rounded-md border text-[11px] font-semibold hover:bg-muted" @click="agir(a)">{{ a.acao }}</button>
              </div>
              <div v-if="!agirItens.length" class="rounded-md border px-2.5 py-3 text-center text-xs text-muted-foreground">nada pra agir: todos revisados hoje</div>
            </div>
          </div>
          <div class="flex min-w-0 flex-1 flex-col rounded-md border bg-card">
            <div class="rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-center text-xs font-semibold text-white">Fixos com data</div>
            <button
              v-for="f in fixosItens"
              :key="f.id"
              type="button"
              class="grid w-full grid-cols-[48px_minmax(0,1fr)] items-center gap-2 border-b px-3 py-1.5 text-left text-xs last:border-b-0 hover:bg-muted/40"
              @click="abrirRaio(f)"
            >
              <b class="text-center">{{ diaMes(f.revisao_ate) }}</b>
              <span class="min-w-0">
                <span class="block truncate">{{ f.pedido_bling || f.pedido_marketplace || 'chamado' }} · {{ f.conta }}</span>
                <span class="block truncate text-[11px] text-muted-foreground">{{ f.revisao_motivo }}</span>
              </span>
            </button>
            <div v-if="!fixosItens.length" class="px-3 py-3 text-center text-[11px] text-muted-foreground">nenhum chamado fixado com data</div>
          </div>
        </template>
        <div v-else class="flex min-w-0 flex-1 flex-col rounded-md border bg-card">
          <div class="rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-center text-xs font-semibold text-white">Raio-X</div>
          <div class="px-3 py-6 text-center text-xs text-muted-foreground">clique num chamado pra ver o Raio-X</div>
        </div>
      </div>
    </div>

    <div v-if="total > PAGE_SIZE" class="flex flex-wrap sm:flex-nowrap items-center justify-between gap-2">
      <span class="text-xs text-muted-foreground">página {{ page }} de {{ totalPages }} · {{ PAGE_SIZE }}/página</span>
      <div class="flex items-center gap-1">
        <Button size="sm" variant="outline" :disabled="page <= 1 || loading" @click="page = 1">«</Button>
        <Button size="sm" variant="outline" :disabled="page <= 1 || loading" @click="page = page - 1"><ChevronLeft class="size-4" /></Button>
        <input v-model.number="page" type="number" :min="1" :max="totalPages" class="w-16 rounded-md border bg-background px-2 py-1 text-center text-sm" @change="page = Math.min(Math.max(1, page), totalPages)" />
        <Button size="sm" variant="outline" :disabled="page >= totalPages || loading" @click="page = page + 1"><ChevronRight class="size-4" /></Button>
        <Button size="sm" variant="outline" :disabled="page >= totalPages || loading" @click="page = totalPages">»</Button>
      </div>
    </div>

    </template>

    <!-- modal: encaminhar ao jurídico -->
    <div v-if="juridico.open && juridico.row" class="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-4" @click.self="closeJuridico">
      <div class="chamado-dialog w-full max-w-lg rounded-lg border bg-background p-5 shadow-xl space-y-4">
        <div class="flex items-start justify-between gap-3">
          <div>
            <div class="text-sm font-semibold inline-flex items-center gap-1.5"><Scale class="size-4 text-violet-600" /> Encaminhar ao jurídico</div>
            <div class="text-xs text-muted-foreground">
              Chamado {{ juridico.row.chamado || '(sem nº)' }} · pedido {{ juridico.row.pedido_bling || juridico.row.pedido_marketplace }} · {{ nomePlataforma(juridico.row.plataforma).toUpperCase() }} {{ juridico.row.conta || '' }}
            </div>
          </div>
          <button type="button" class="rounded p-1 hover:bg-muted" @click="closeJuridico"><X class="size-4" /></button>
        </div>
        <p class="text-sm text-muted-foreground">
          Vai pelo Threema (o "Informar") um aviso com os dados do chamado e o <b>link do dossiê</b>: histórico completo e todas as fotos, aberto sem login por link secreto.
        </p>
        <div class="text-xs">
          <span class="text-muted-foreground">Destinatários:</span>
          <span v-if="juridico.semAcesso"> cadastrados pelo admin (você não vê a lista).</span>
          <span v-else-if="juridico.destinatarios.length"> {{ juridico.destinatarios.join(', ') }}</span>
          <span v-else class="text-red-500"> nenhum cadastrado.</span>
          <button v-if="isAdmin" type="button" class="ml-2 underline" @click="juridicoCfgOpen = true">editar destinatários</button>
        </div>
        <div v-if="juridico.row.juridico_enviado_at" class="rounded border border-violet-500/30 bg-violet-500/5 px-2 py-1 text-xs">
          Já encaminhado em {{ fmtDateTime(juridico.row.juridico_enviado_at) }} por {{ juridico.row.juridico_enviado_por_nome || '—' }}. Enviar de novo manda o mesmo link atualizado.
        </div>
        <div>
          <label class="text-xs text-muted-foreground">Observação pro jurídico (opcional)</label>
          <textarea v-model="juridico.obs" rows="3" class="mt-1 w-full rounded-md border bg-background px-2 py-1.5 text-sm" placeholder="ex.: cliente ameaça processar; já enviamos as provas pela plataforma"></textarea>
        </div>
        <p v-if="juridico.erro" class="text-sm text-red-500">{{ juridico.erro }}</p>
        <div class="flex justify-end gap-2">
          <Button variant="ghost" @click="closeJuridico">Cancelar</Button>
          <Button :disabled="juridico.saving || !canEdit" @click="enviarJuridico">
            <Loader2 v-if="juridico.saving" class="size-4 mr-1 animate-spin" />
            <Scale v-else class="size-4 mr-1" />
            {{ juridico.saving ? 'Enviando…' : 'Enviar ao jurídico' }}
          </Button>
        </div>
      </div>
    </div>
    <InformarThreemaModal :open="avisoIaCfgOpen" contexto="chamados_ia" somente-cadastro descricao="Quem está marcado recebe no Threema o aviso de quando a IA de Chamado para na tela da loja num 'não sou robô' (captcha/quebra-cabeça) ou num login: qual pedido, onde parou e o link do chamado. A loja fica aberta nessa tela no Mac mini da Makisa — resolva lá e mande a instrução 'continua' no chamado. A seleção fica salva." @close="avisoIaCfgOpen = false" />
    <InformarThreemaModal :open="juridicoCfgOpen" contexto="juridico" somente-cadastro descricao="Quem está marcado recebe no Threema o aviso do botão 'encaminhar ao jurídico' (dados do chamado + link do dossiê com o histórico e as fotos). A seleção fica salva." @close="juridicoCfgOpen = false; if (juridico.open && juridico.row) openJuridico(juridico.row)" />

    <!-- modal: histórico + réplica -->
    <div v-if="hist.open && hist.row" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" @click.self="closeHistorico">
      <!-- 09/11/2026 (dono, pelo Vinicius): "deixa o histórico maior, quase tela toda; de um
           lado a conversa com o marketplace, do outro a IA". Antes: max-w-3xl (768 px). -->
      <div class="chamado-dialog flex h-[94vh] w-[96vw] max-w-[1800px] flex-col rounded-lg border bg-background shadow-xl">
        <div class="shrink-0 flex items-start justify-between gap-3 border-b px-4 py-3">
          <div>
            <!-- 21/09 (Vinicius): o pedido do marketplace junto do Bling — é o número
                 que a pessoa procura na plataforma. -->
            <div class="text-sm font-semibold">
              Chamado {{ hist.row.chamado || '(sem nº)' }} · pedido {{ hist.row.pedido_bling || hist.row.pedido_marketplace }}<template v-if="hist.row.pedido_bling && hist.row.pedido_marketplace"> · {{ (nomePlataforma(hist.row.plataforma) || 'marketplace').toUpperCase() }} {{ hist.row.pedido_marketplace }}</template>
            </div>
            <div class="text-xs text-muted-foreground">
              {{ origemLabel(hist.row.origem) }} · {{ nomePlataforma(hist.row.plataforma).toUpperCase() }} {{ hist.row.conta || '' }} · canal {{ hist.row.canal }}
              <a v-if="hist.row.chamado_url" :href="hist.row.chamado_url" target="_blank" rel="noopener" class="ml-1 underline">abrir na plataforma</a>
            </div>
            <!-- 25/09 (294571): consulta aberta à mão no Portal de Atendimento — o
                 robô de leitura passa a ler lá também. Aceita o link ou o número. -->
            <div v-if="ehShopee(hist.row) && !hist.row.chamado_de_tela" class="mt-1 flex flex-wrap items-center gap-1.5 text-xs">
              <span class="text-muted-foreground">Consulta no Portal:</span>
              <input
                v-model="consultaDraft"
                :disabled="!canEdit || salvandoConsulta"
                placeholder="cole o link ou o nº da consulta"
                class="h-6 w-60 rounded border bg-background px-1.5 text-xs"
                @keydown.enter.prevent="salvarConsulta"
                @blur="salvarConsulta"
              />
              <a v-if="hist.row.consulta_portal" :href="`https://seller-service.cs.shopee.com.br/detail/${hist.row.consulta_portal}`" target="_blank" rel="noopener" class="underline">abrir</a>
              <span v-if="consultaErro" class="text-red-600">{{ consultaErro }}</span>
            </div>
          </div>
          <div class="flex items-center gap-2">
            <!-- 09/11 (Vinicius): abre o perfil da loja no AdsPower DESTE computador, já na
                 página do chamado (cada um no seu). -->
            <ChamadoServidor :chamado-id="hist.row.id" :destino="hist.row.chamado_url" />
            <!-- 07/10 (Vinicius): atalho pra Devoluções numa aba nova, já na aba
                 Lançamentos e buscando este pedido (é lá que fica o motivo). -->
            <NuxtLink
              v-if="hist.row.origem === 'devolucao' && (hist.row.pedido_bling || hist.row.pedido_marketplace)"
              :to="{ path: '/devolucoes', query: { tab: 'lancamentos', search: hist.row.pedido_bling || hist.row.pedido_marketplace } }"
              target="_blank"
              class="inline-flex h-7 items-center whitespace-nowrap rounded-md border border-input bg-background px-2 text-sm font-medium hover:bg-accent hover:text-accent-foreground"
              title="Abrir a Devoluções numa aba nova, na aba Lançamentos, já buscando este pedido"
            >
              <ExternalLink class="size-3.5 mr-1" />
              Devoluções
            </NuxtLink>
            <!-- 05/11/2026 (Vinicius): conferir se caiu o valor que a loja informou. -->
            <NuxtLink
              v-if="linkConciliacao(hist.row)"
              :to="linkConciliacao(hist.row)!"
              target="_blank"
              class="inline-flex h-7 items-center whitespace-nowrap rounded-md border border-input bg-background px-2 text-sm font-medium hover:bg-accent hover:text-accent-foreground"
              title="Abrir a Conciliação numa aba nova, na loja deste pedido, com o Raio-X aberto"
            >
              <ExternalLink class="size-3.5 mr-1" />
              Conciliação
            </NuxtLink>
            <Button
              v-if="hist.row.canal === 'api' || hist.row.chamado_de_tela || casoAmazon(hist.row)"
              size="sm"
              variant="outline"
              class="h-7 px-2"
              :disabled="!canEdit || relendo"
              :title="hist.row.chamado_de_tela || casoAmazon(hist.row)
                ? 'Pôr este caso na frente da fila do robô de leitura (ele abre a tela da plataforma)'
                : 'Ler o caso na plataforma agora (o robô faz isso de hora em hora, no minuto 25)'"
              @click="relerAgora"
            >
              <Loader2 v-if="relendo" class="size-3.5 mr-1 animate-spin" />
              {{ relendo ? 'Atualizando…' : 'Atualizar' }}
            </Button>
            <button type="button" class="rounded p-1 hover:bg-muted" @click="closeHistorico"><X class="size-4" /></button>
          </div>
        </div>

        <!-- 21/10/2026 (Pós-venda × e-mail): bloco NOVO, só leitura — os e-mails ligados ao
             chamado. Não entra no histórico (que fica como era); recolhe quando são muitos. -->
        <details
          v-if="emailsLigados.itens.length"
          class="shrink-0 border-b px-4 py-2"
          :open="emailsLigados.itens.length <= 3"
        >
          <summary class="cursor-pointer select-none text-xs font-medium">
            <Mail class="mr-1 inline size-3.5 text-teal-600 dark:text-teal-400" />
            E-mails ligados ({{ emailsLigados.itens.length }})
            <span v-if="emailsLigados.ocultos" class="font-normal text-muted-foreground">· {{ emailsLigados.ocultos }} sem acesso ao texto</span>
          </summary>
          <div class="mt-2 max-h-56 space-y-2 overflow-y-auto pr-1">
            <div
              v-for="e in emailsLigados.itens"
              :key="e.id"
              class="rounded-xl border border-teal-200/70 bg-teal-50/60 px-3 py-2 dark:border-teal-800/60 dark:bg-teal-900/15"
            >
              <div class="flex flex-wrap items-center gap-x-2 text-[11px]">
                <span class="inline-flex items-center gap-1 font-medium text-teal-700 dark:text-teal-300"><Mail class="size-3" /> {{ e.pode_ler ? (e.de_nome || e.de || 'e-mail') : 'e-mail' }}</span>
                <span v-if="e.pasta" class="text-muted-foreground">· pasta «{{ e.pasta }}»</span>
                <span v-if="e.loja" class="text-muted-foreground">· {{ e.loja }}</span>
                <span
                  v-if="e.suspeito"
                  class="rounded bg-red-500/15 px-1.5 py-px font-medium text-red-700 dark:text-red-300"
                  :title="(e.suspeito_motivos || []).join(' · ') || 'remetente suspeito: confira antes de confiar no e-mail'"
                  data-email-suspeito
                >remetente suspeito</span>
                <span class="ml-auto text-muted-foreground">{{ fmtDateTime(e.recebido_em) }}</span>
              </div>
              <template v-if="e.pode_ler">
                <div v-if="e.assunto" class="mt-1 break-words text-sm font-medium">{{ e.assunto }}</div>
                <div class="mt-1 whitespace-pre-wrap break-words text-sm leading-relaxed">{{ e.texto }}</div>
                <div v-if="protecaoDoEmail(e)" class="mt-1 text-[10px] text-muted-foreground">{{ protecaoDoEmail(e) }}</div>
                <div v-if="e.anexos?.length" class="mt-2 flex flex-wrap gap-1.5">
                  <a
                    v-for="a in e.anexos"
                    :key="a.id"
                    :href="emailAnexoUrl(a.id)"
                    class="inline-flex max-w-[220px] items-center gap-1 rounded border bg-background px-1.5 py-0.5 text-[11px] hover:bg-muted"
                    :title="`baixar ${a.filename || 'anexo'}`"
                  ><Paperclip class="size-3 shrink-0" /><span class="truncate">{{ a.filename || 'anexo' }}</span></a>
                </div>
              </template>
              <div v-else class="mt-1 text-[11px] italic text-muted-foreground">sem acesso ao texto — só quem vê o Atendimento desta loja</div>
            </div>
          </div>
        </details>

        <!-- 09/11/2026: duas colunas, cada uma rola sozinha e abre na mais nova (embaixo):
             à esquerda a conversa com a plataforma (o problema, ela, nós); à direita tudo da
             IA de Chamado (decisões, o que ela fez, as instruções da equipe). Mesma ordem de
             data nas duas — a última de cada lado é a de baixo. -->
        <div class="grid min-h-0 flex-1 grid-rows-2 md:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)] md:grid-rows-1">
         <div
          v-for="col in colunasHist"
          :key="col.chave"
          class="flex min-h-0 flex-col"
          :class="col.chave === 'ia' ? 'border-t bg-muted/30 md:border-l md:border-t-0' : ''"
         >
          <div class="shrink-0 border-b px-4 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{{ col.titulo }}</div>
        <div :ref="(el) => { colunaHistEls[col.chave] = el as HTMLElement | null }" class="flex-1 min-h-0 overflow-y-auto px-4 py-3 space-y-3">
          <div v-if="hist.loading" class="text-sm text-muted-foreground"><Loader2 class="size-4 inline animate-spin mr-1.5" />carregando histórico…</div>
          <div v-else-if="!col.bolhas.length" class="text-sm text-muted-foreground">{{ col.vazio }}</div>
          <!-- Conversa em balões (Eduardo 10/09: "como se fosse um whatsapp"):
               nossas falas à direita, plataforma à esquerda, sistema no meio. -->
          <div
            v-for="b in col.bolhas"
            :key="b.chave"
            class="group flex"
            :class="{
              'justify-end': b.lado === 'nos' || b.lado === 'instrucao',
              'justify-start': b.lado === 'eles',
              'justify-center': b.lado === 'sistema',
            }"
          >
            <div
              v-if="b.lado === 'sistema' && b.encerramento"
              class="max-w-[85%] whitespace-pre-wrap break-words rounded-2xl border border-amber-300/70 bg-amber-50 px-3 py-1.5 text-center text-xs text-amber-800 dark:border-amber-800/60 dark:bg-amber-900/20 dark:text-amber-200"
            ><span class="font-semibold">{{ b.quando }}</span> · {{ b.texto }}</div>
            <div
              v-else-if="b.lado === 'sistema'"
              class="max-w-[85%] whitespace-pre-wrap break-words rounded-2xl bg-muted px-3 py-1 text-center text-[11px] italic text-muted-foreground"
              :title="b.quando"
            >{{ b.texto }}</div>

            <!-- 24/09: decisão da IA de Chamado, no meio da conversa, com ✓/✗ -->
            <div
              v-else-if="b.lado === 'ia'"
              class="w-full rounded-xl border border-violet-300/60 bg-violet-50/60 px-3 py-2 dark:border-violet-800/60 dark:bg-violet-900/15"
            >
              <div class="flex flex-wrap items-center gap-x-2 text-[11px]">
                <span class="inline-flex items-center gap-1 font-medium text-violet-700 dark:text-violet-300"><Bot class="size-3" /> {{ b.autor }} · decisão</span>
                <span class="text-muted-foreground">{{ b.quando }}</span>
                <span
                  v-if="b.avaliacao"
                  class="ml-auto inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-medium"
                  :class="b.avaliacao.certo ? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300' : 'bg-red-500/15 text-red-700 dark:text-red-300'"
                >
                  <component :is="b.avaliacao.certo ? ThumbsUp : ThumbsDown" class="size-3" />
                  {{ b.avaliacao.certo ? 'acertou' : 'errou' }}<template v-if="b.avaliacao.autor"> · {{ b.avaliacao.autor }}</template>
                  <button v-if="canEdit" type="button" class="ml-1 opacity-70 hover:opacity-100" title="desfazer a avaliação" :disabled="histIa.salvando === b.mensagemId" @click="desfazerNoHistorico(b)"><Undo2 class="size-3" /></button>
                </span>
              </div>
              <div class="mt-1 whitespace-pre-wrap break-words text-sm leading-relaxed">{{ resumoDaIa(b.texto) }}</div>
              <div v-if="b.avaliacao && !b.avaliacao.certo" class="mt-1.5 rounded border border-red-500/30 bg-red-500/5 px-2 py-1 text-xs">
                <span class="font-semibold text-red-700 dark:text-red-300">O certo era:</span> {{ b.avaliacao.correcao }}
              </div>
              <template v-if="!b.avaliacao && canEdit">
                <div v-if="histIa.corrigindo !== b.mensagemId" class="mt-1.5 flex items-center justify-end gap-1">
                  <Loader2 v-if="histIa.salvando === b.mensagemId" class="size-4 animate-spin text-muted-foreground" />
                  <template v-else>
                    <Button size="sm" variant="outline" class="h-7 px-2 text-xs text-emerald-700 dark:text-emerald-300" @click="avaliarNoHistorico(b, true)"><ThumbsUp class="size-3.5 mr-1" /> acertou</Button>
                    <Button size="sm" variant="outline" class="h-7 px-2 text-xs text-red-700 dark:text-red-300" @click="histIa.corrigindo = b.mensagemId || null; histIa.correcao = ''"><ThumbsDown class="size-3.5 mr-1" /> errou</Button>
                  </template>
                </div>
                <div v-else class="mt-1.5 space-y-1">
                  <textarea v-model="histIa.correcao" rows="3" class="w-full rounded-md border bg-background px-2 py-1.5 text-sm" placeholder="o que era o certo? — ela refaz este chamado na próxima passada (1 a 2 min) e guarda como aprendizado" />
                  <div class="flex items-center justify-end gap-1">
                    <span v-if="histIa.erro" class="mr-auto text-[11px] text-red-500">{{ histIa.erro }}</span>
                    <Button size="sm" variant="ghost" class="h-7" @click="histIa.corrigindo = null"><X class="size-3.5" /></Button>
                    <Button size="sm" class="h-7" :disabled="!histIa.correcao.trim() || histIa.salvando === b.mensagemId" @click="avaliarNoHistorico(b, false)"><Check class="size-3.5 mr-1" /> salvar correção</Button>
                  </div>
                </div>
              </template>
            </div>

            <!-- 19/09: instrução pro robô — do nosso lado, mas em índigo pra não confundir
                 com réplica (não foi pra plataforma). -->
            <div
              v-else-if="b.lado === 'instrucao'"
              class="max-w-[78%] rounded-2xl rounded-br-sm px-3 py-2 shadow-sm bg-indigo-50 dark:bg-indigo-900/25 border border-indigo-200/70 dark:border-indigo-800/60"
            >
              <div class="flex flex-wrap items-center gap-x-2 text-[11px]">
                <span class="font-medium text-indigo-700 dark:text-indigo-300 inline-flex items-center gap-1"><Bot class="size-3" /> Instrução pra IA de Chamado · {{ b.autor }}</span>
              </div>
              <div class="mt-1 whitespace-pre-wrap break-words text-sm leading-relaxed">{{ b.texto }}</div>
              <div class="mt-1 text-right text-[10px] text-muted-foreground">{{ b.quando }}</div>
            </div>

            <div
              v-else
              class="max-w-[78%] rounded-2xl px-3 py-2 shadow-sm"
              :class="b.lado === 'nos'
                ? 'rounded-br-sm bg-emerald-50 dark:bg-emerald-900/25 border border-emerald-200/70 dark:border-emerald-800/60'
                : 'rounded-bl-sm bg-background border'"
            >
              <div class="flex flex-wrap items-center gap-x-2 text-[11px]">
                <span class="font-medium" :class="b.lado === 'nos' ? 'text-emerald-700 dark:text-emerald-300' : 'text-orange-600 dark:text-orange-400'">{{ b.autor }}</span>
                <span v-if="b.meta" class="text-muted-foreground">· {{ b.meta }}</span>
                <span v-if="b.status" class="rounded px-1.5 py-0.5" :class="statusMensagemClass(b.status)">{{ b.status }}<template v-if="b.erro"> — {{ ERROS[b.erro] || b.erro }}</template></span>
              </div>
              <div class="mt-1 whitespace-pre-wrap break-words text-sm leading-relaxed"><template v-for="(p, i) in partesComLink(b.texto)" :key="i"><a v-if="p.t === 'url'" :href="p.v" target="_blank" rel="noopener" class="underline break-all">{{ p.v }}</a><template v-else>{{ p.v }}</template></template></div>
              <div v-if="b.anexos.length" class="mt-2 flex flex-wrap gap-2">
                <a v-for="a in b.anexos" :key="a.id" :href="anexoUrl(a.id)" target="_blank" rel="noopener" :title="a.filename">
                  <img :src="anexoUrl(a.id)" :alt="a.filename" class="h-20 w-20 rounded border object-cover" />
                </a>
              </div>
              <div class="mt-1 text-right text-[10px] text-muted-foreground">{{ b.quando }}</div>
            </div>
            <!-- 25/09: lixeirinha — aparece ao passar o mouse (no toque, fica sempre à vista);
                 do lado de dentro do balão: à esquerda dos nossos, à direita dos outros. -->
            <button
              v-if="podeLixeira(b)"
              type="button"
              class="shrink-0 rounded p-1 text-muted-foreground opacity-0 transition-opacity hover:bg-red-500/10 hover:text-red-600 focus:opacity-100 group-hover:opacity-100 disabled:opacity-40 [@media(hover:none)]:opacity-60"
              :class="[
                b.lado === 'nos' || b.lado === 'instrucao' ? 'order-first mr-1' : 'ml-1',
                b.lado === 'sistema' ? 'self-center' : 'self-start mt-1',
              ]"
              :disabled="histExcluindo === b.origem?.id"
              :title="b.origem?.direcao === 'enviada' && b.origem?.status === 'pendente' ? 'cancelar réplica (ainda não saiu)' : 'excluir mensagem do histórico'"
              aria-label="excluir mensagem do histórico"
              @click="excluirMensagem(b)"
            >
              <Loader2 v-if="histExcluindo === b.origem?.id" class="size-3.5 animate-spin" />
              <Trash2 v-else class="size-3.5" />
            </button>
          </div>
        </div>
         </div>
        </div>

        <!-- 19/09 (Vinicius): "réplica manual muito grande; campo pequeno que abre o
             balão, igual às observações; histórico aproveita o espaço". Rodapé em UMA
             faixa de duas colunas: à esquerda a réplica manual (vai pra plataforma pelo
             canal), à direita a instrução pro robô (NÃO vai pra plataforma: o robô lê na
             próxima passada, mesmo em Encerrado, e responde aqui). Cada coluna tem um
             campo de uma linha que mostra o começo do rascunho; clicar abre o balão com
             a textarea grande — o rascunho é o mesmo hist.texto / hist.instrucao, fechar
             o balão não apaga. Os dois botões de enviar são iguais (azul). Em Concluído
             (fechado por pessoa) a API devolve 422 chamado_concluido — a réplica some e
             fica só o aviso pra reabrir. -->
        <div v-if="hist.row.resolvido" class="shrink-0 grid gap-4 border-t px-4 py-3 md:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]">
          <div class="min-w-0">
            <!-- 23/09 (Vinicius): a Observação da coluna, aqui também. -->
            <div class="flex items-center gap-2 min-w-0">
              <span class="shrink-0 text-xs font-medium">Observação</span>
              <div class="min-w-0 flex-1 rounded-md border bg-amber-50/40 dark:bg-amber-900/10">
                <ObservacaoPopover
                  :model-value="hist.row.observacao"
                  :disabled="!canEdit"
                  placeholder="clique pra escrever"
                  :titulo="`Observação · ${hist.row.pedido_bling || hist.row.pedido_marketplace || 'chamado'}`"
                  @update:model-value="(v) => { if (hist.row) setRowText(hist.row, 'observacao', v) }"
                  @save="salvarObservacaoHist"
                />
              </div>
            </div>
          </div>
          <div class="text-xs text-muted-foreground inline-flex items-center gap-1.5">
            <Bot class="size-3.5 text-indigo-600 dark:text-indigo-400" />
            Chamado concluído — reabra o chamado pra instruir o robô.
          </div>
        </div>
        <div v-else class="shrink-0 grid gap-4 border-t px-4 py-3 md:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]">
          <!-- coluna esquerda: réplica manual -->
          <div class="space-y-2 min-w-0">
            <div class="text-xs font-medium truncate">Réplica manual <span class="text-muted-foreground font-normal">— resposta à plataforma pelo canal <b>{{ hist.row.canal }}</b></span></div>
            <PopoverRoot :open="replicaBalao" @update:open="replicaBalao = $event">
              <PopoverTrigger as-child>
                <button
                  id="replica-texto"
                  type="button"
                  :disabled="!canEdit"
                  class="flex h-9 w-full items-center truncate rounded-md border bg-background px-3 text-left text-sm hover:bg-muted/40 focus:outline-none focus:ring-1 focus:ring-primary disabled:cursor-default disabled:opacity-60"
                  :title="hist.texto || 'clique pra escrever a réplica'"
                >
                  <span v-if="hist.texto" class="truncate">{{ hist.texto }}</span>
                  <span v-else class="text-muted-foreground/60">digite aqui…</span>
                </button>
              </PopoverTrigger>
              <PopoverPortal>
                <PopoverContent
                  side="top"
                  align="start"
                  :side-offset="4"
                  :collision-padding="8"
                  class="z-[70] w-[440px] max-w-[calc(100vw-16px)] rounded-md border bg-background p-2 shadow-lg"
                  @open-auto-focus="focarCaixa($event, replicaCaixa)"
                >
                  <div class="mb-1 text-[11px] font-medium text-muted-foreground">Réplica manual — canal {{ hist.row.canal }}</div>
                  <textarea
                    ref="replicaCaixa"
                    v-model="hist.texto"
                    rows="6"
                    :disabled="!canEdit"
                    class="w-full resize-y rounded-md border bg-background px-2 py-1.5 text-sm disabled:opacity-60"
                    placeholder="digite a mensagem…"
                  />
                  <div class="mt-1 flex flex-wrap items-center gap-2">
                    <span v-if="hist.files.length" class="text-[11px] text-muted-foreground">{{ hist.files.length }} foto{{ hist.files.length > 1 ? 's' : '' }} anexada{{ hist.files.length > 1 ? 's' : '' }}</span>
                    <span v-if="hist.erro" class="text-[11px] text-red-500">{{ hist.erro }}</span>
                    <Button size="sm" variant="outline" class="ml-auto" @click="replicaBalao = false">Fechar</Button>
                    <Button size="sm" :disabled="!canEdit || hist.sending || !hist.texto.trim()" @click="enviarReplicaDoBalao">
                      <Loader2 v-if="hist.sending" class="size-4 mr-1.5 animate-spin" />
                      <Send v-else class="size-4 mr-1.5" />
                      {{ hist.row.canal === 'manual' ? 'Registrar' : hist.row.canal === 'robo' ? 'Enfileirar pro robô' : 'Enviar' }}
                    </Button>
                  </div>
                </PopoverContent>
              </PopoverPortal>
            </PopoverRoot>
            <div class="flex flex-wrap items-center gap-2">
              <label class="inline-flex items-center gap-1 rounded border px-2 py-1 text-xs" :class="canEdit ? 'cursor-pointer hover:bg-muted' : 'cursor-default opacity-60'">
                <ImagePlus class="size-3.5" />
                Anexar foto
                <input type="file" accept="image/png,image/jpeg,image/webp,image/gif" multiple class="hidden" :disabled="!canEdit" @change="onReplicaFiles" />
              </label>
              <span v-for="(f, i) in hist.files" :key="`${f.name}-${i}`" class="inline-flex max-w-[160px] items-center gap-1 rounded bg-muted px-2 py-0.5 text-[11px]" :title="f.name">
                <span class="truncate">{{ f.name }}</span>
                <button type="button" class="shrink-0 hover:text-red-500" @click="removeReplicaFile(i)"><X class="size-3" /></button>
              </span>
              <span v-if="hist.erro" class="text-xs text-red-500">{{ hist.erro }}</span>
              <!-- 19/09 (Vinicius): resolver sem sair do histórico — abre a MESMA janela de
                   resolver por cima; ao confirmar, a linha sai dos abertos e este modal fecha. -->
              <span v-if="!podeEncerrar" class="ml-auto" />
              <Button v-else size="sm" variant="outline" class="ml-auto" :disabled="!canEdit || busy.has(hist.row.id)" title="marcar como resolvido (lucro/prejuízo + situação no Bling)" @click="openResolver(hist.row)">
                <CheckCircle2 class="size-4 mr-1.5" />
                Resolver
              </Button>
              <!-- 21/09 (Vinicius): lixeira ao lado do resolver — abre a janela de exclusão
                   (chamado + lançamentos de devolução do pedido + nova situação no Bling). -->
              <Button v-if="canDelete" size="sm" variant="outline" class="px-2 text-red-500 hover:bg-red-500/10 hover:text-red-600" :disabled="busy.has(hist.row.id)" title="excluir chamado e lançamentos" @click="openExcluir(hist.row)">
                <Trash2 class="size-4" />
              </Button>
              <Button size="sm" :disabled="!canEdit || hist.sending || !hist.texto.trim()" @click="enviarReplica">
                <Loader2 v-if="hist.sending" class="size-4 mr-1.5 animate-spin" />
                <Send v-else class="size-4 mr-1.5" />
                {{ hist.row.canal === 'manual' ? 'Registrar' : hist.row.canal === 'robo' ? 'Enfileirar pro robô' : 'Enviar' }}
              </Button>
            </div>
            <!-- 23/09 (Vinicius): a Observação da coluna, aqui também. -->
            <div class="flex items-center gap-2 min-w-0">
              <span class="shrink-0 text-xs font-medium">Observação</span>
              <div class="min-w-0 flex-1 rounded-md border bg-amber-50/40 dark:bg-amber-900/10">
                <ObservacaoPopover
                  :model-value="hist.row.observacao"
                  :disabled="!canEdit"
                  placeholder="clique pra escrever"
                  :titulo="`Observação · ${hist.row.pedido_bling || hist.row.pedido_marketplace || 'chamado'}`"
                  @update:model-value="(v) => { if (hist.row) setRowText(hist.row, 'observacao', v) }"
                  @save="salvarObservacaoHist"
                />
              </div>
            </div>
          </div>

          <!-- coluna direita: instrução pro robô -->
          <div class="space-y-2 min-w-0 rounded-md bg-indigo-50/40 dark:bg-indigo-900/10 px-2 py-2 -mx-2">
            <!-- 21/09 (Vinicius): a explicação cortava com "…" e não dava pra ler —
                 agora quebra linha. -->
            <div class="text-xs font-medium flex items-start gap-1.5 min-w-0">
              <Bot class="size-3.5 shrink-0 mt-0.5 text-indigo-600 dark:text-indigo-400" />
              <span class="min-w-0">Instrução pra IA de Chamado <span class="text-muted-foreground font-normal">— não vai pra plataforma; a IA lê na próxima passada e responde aqui</span></span>
            </div>
            <div v-if="hist.row.instrucao_pendente" class="truncate rounded border border-indigo-500/30 bg-indigo-500/5 px-2 py-1 text-xs" :title="hist.row.instrucao_pendente">
              <span class="font-medium text-indigo-700 dark:text-indigo-300">Instrução pendente:</span> {{ hist.row.instrucao_pendente }}
            </div>
            <PopoverRoot :open="instrucaoBalao" @update:open="instrucaoBalao = $event">
              <PopoverTrigger as-child>
                <button
                  type="button"
                  :disabled="!canEdit"
                  class="flex h-9 w-full items-center truncate rounded-md border bg-background px-3 text-left text-sm hover:bg-muted/40 focus:outline-none focus:ring-1 focus:ring-primary disabled:cursor-default disabled:opacity-60"
                  :title="hist.instrucao || 'clique pra escrever a instrução'"
                >
                  <span v-if="hist.instrucao" class="truncate">{{ hist.instrucao }}</span>
                  <span v-else class="text-muted-foreground/60">digite aqui…</span>
                </button>
              </PopoverTrigger>
              <PopoverPortal>
                <PopoverContent
                  side="top"
                  align="start"
                  :side-offset="4"
                  :collision-padding="8"
                  class="z-[70] w-[440px] max-w-[calc(100vw-16px)] rounded-md border bg-background p-2 shadow-lg"
                  @open-auto-focus="focarCaixa($event, instrucaoCaixa)"
                >
                  <div class="mb-1 text-[11px] font-medium text-muted-foreground inline-flex items-center gap-1"><Bot class="size-3 text-indigo-600 dark:text-indigo-400" /> Instrução pra IA de Chamado — não vai pra plataforma</div>
                  <textarea
                    ref="instrucaoCaixa"
                    v-model="hist.instrucao"
                    rows="6"
                    :disabled="!canEdit"
                    class="w-full resize-y rounded-md border bg-background px-2 py-1.5 text-sm disabled:opacity-60"
                    placeholder="ex.: contesta de novo citando a foto do pacote; se a plataforma negar, pede prorrogação"
                  />
                  <div class="mt-1 flex flex-wrap items-center gap-2">
                    <span v-if="hist.instrucaoErro" class="text-[11px] text-red-500">{{ hist.instrucaoErro }}</span>
                    <Button size="sm" variant="outline" class="ml-auto" @click="instrucaoBalao = false">Fechar</Button>
                    <Button size="sm" :disabled="!canEdit || hist.instrucaoSending || !hist.instrucao.trim()" @click="enviarInstrucaoDoBalao">
                      <Loader2 v-if="hist.instrucaoSending" class="size-4 mr-1.5 animate-spin" />
                      <Bot v-else class="size-4 mr-1.5" />
                      Enviar instrução
                    </Button>
                  </div>
                </PopoverContent>
              </PopoverPortal>
            </PopoverRoot>
            <div class="flex flex-wrap items-center gap-2">
              <span v-if="hist.instrucaoErro" class="text-xs text-red-500">{{ hist.instrucaoErro }}</span>
              <Button size="sm" class="ml-auto" :disabled="!canEdit || hist.instrucaoSending || !hist.instrucao.trim()" @click="enviarInstrucao">
                <Loader2 v-if="hist.instrucaoSending" class="size-4 mr-1.5 animate-spin" />
                <Bot v-else class="size-4 mr-1.5" />
                Enviar instrução
              </Button>
            </div>
            <!-- 05/11/2026 (Vinicius): "abro o histórico, analiso e marco aqui dentro". -->
            <div v-if="!hist.row.resolvido" class="flex items-center justify-end gap-2 min-w-0">
              <span class="shrink-0 text-xs font-medium">Revisão</span>
              <ChamadoRevisaoSeletor :revisao="hist.row" :disabled="!canEdit" @marcar="(m) => { if (hist.row) marcarRevisao(hist.row, m) }" />
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- modal: resolver (z-[60]: 19/09 também abre por cima do histórico, que é z-50) -->
    <div v-if="resolver.open && resolver.row" class="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-4" @click.self="closeResolver">
      <div class="chamado-dialog w-full max-w-md rounded-lg border bg-background shadow-xl">
        <div class="flex items-start justify-between gap-3 border-b px-4 py-3">
          <div>
            <div class="text-sm font-semibold">Resolver chamado · pedido {{ resolver.row.pedido_bling || resolver.row.pedido_marketplace }}</div>
            <!-- 19/09: a plataforma já encerrou (ganhamos/perdemos/sem decisão) — a pessoa só confirma o resultado.
                 07/10: ou alguém tirou o motivo na Devoluções — aí o motivo diz quem. -->
            <div v-if="resolver.row.status_aba === 'encerrado'" class="text-xs text-amber-700 dark:text-amber-300">
              Encerrado: {{ resolver.row.status_aba_motivo || 'plataforma encerrou sem decisão' }}
            </div>
            <!-- 21/09 (Vinicius): a sugestão do robô fica só aqui em cima (o valor já
                 vem preenchido no campo); sem a linha repetida embaixo do Valor. -->
            <div v-else-if="resolver.sugerido" class="text-xs text-indigo-700 dark:text-indigo-300 inline-flex items-center gap-1">
              <Bot class="size-3" /> robô sugere {{ resultadoTexto(resolver.row.valor_sugerido) }}
            </div>
          </div>
          <button type="button" class="rounded p-1 hover:bg-muted" @click="closeResolver"><X class="size-4" /></button>
        </div>
        <div class="space-y-3 px-4 py-3 text-sm">
          <p class="text-muted-foreground">O chamado sai da lista de abertos.</p>
          <!-- 19/09 (Vinicius: "status atual do Bling e o que vai trocar, obrigatório"):
               de onde está saindo e pra onde vai. Sem pedido Bling não há o que trocar. -->
          <template v-if="resolverExigeSituacao">
            <div class="text-xs">
              <span class="text-muted-foreground">Situação atual no Bling:</span>
              <b class="ml-1">{{ resolverSituacaoAtual }}</b>
            </div>
            <label class="block space-y-1">
              <span class="text-xs font-medium">Nova situação no Bling <span class="text-red-500">*</span></span>
              <select v-model="resolver.situacao" class="h-9 w-full rounded-md border bg-background px-2 text-sm" :class="resolverSituacaoOk ? '' : 'ring-1 ring-red-500/60'">
                <option value="" disabled>escolha…</option>
                <template v-if="opcoesFechamento(resolver.row).length">
                  <option v-for="s in opcoesFechamento(resolver.row)" :key="s" :value="s">{{ s }}</option>
                  <option disabled>──────</option>
                </template>
                <option v-for="s in situacoes.filter((x) => !opcoesFechamento(resolver.row!).includes(x))" :key="s" :value="s">{{ s }}</option>
              </select>
            </label>
          </template>
          <div v-else class="text-[11px] text-muted-foreground">Sem pedido Bling — nada a trocar no Bling.</div>
          <!-- Valor obrigatório (Eduardo 15/09): "encerrou com prejuízo ou com lucro? ex. 100 reais ganhamos" -->
          <div class="space-y-1">
            <span class="block text-xs font-medium">
              Valor <span class="text-red-500">*</span>
              <span class="font-normal text-muted-foreground">— o chamado encerrou com lucro ou prejuízo?</span>
            </span>
            <div class="flex items-center gap-2">
              <div class="inline-flex shrink-0 overflow-hidden rounded-md border">
                <button
                  type="button"
                  class="px-3 py-1.5 text-xs"
                  :class="resolver.tipo === 'lucro' ? 'bg-emerald-600 text-white' : 'hover:bg-muted'"
                  @click="resolver.tipo = 'lucro'"
                >lucro</button>
                <button
                  type="button"
                  class="border-l px-3 py-1.5 text-xs"
                  :class="resolver.tipo === 'prejuizo' ? 'bg-red-600 text-white' : 'hover:bg-muted'"
                  @click="resolver.tipo = 'prejuizo'"
                >prejuízo</button>
              </div>
              <div class="relative flex-1">
                <span class="absolute left-2.5 top-1/2 -translate-y-1/2 text-xs text-muted-foreground">R$</span>
                <input
                  id="resolver-valor"
                  v-model="resolver.valor"
                  type="number"
                  min="0"
                  step="0.01"
                  inputmode="decimal"
                  placeholder="0,00"
                  class="h-9 w-full rounded-md border bg-background pl-8 pr-2 text-right text-sm tabular-nums"
                  :class="resolverValorOk ? '' : 'ring-1 ring-red-500/60'"
                  @keydown.enter.prevent="confirmarResolver"
                />
              </div>
            </div>
            <!-- 21/09 (Vinicius): sem os textos de ajuda — obrigatório continua
                 obrigatório (asterisco + borda vermelha + botão travado), só sem a frase. -->
            <!-- 19/09: custo do produto (Bling), só pra ver — ajuda a decidir o tamanho do prejuízo. -->
            <div v-if="resolver.row.custo_produto !== null && resolver.row.custo_produto !== undefined" class="text-[11px] text-muted-foreground" :title="resolver.row.custo_detalhe || ''">
              Custo do produto: <b class="text-foreground">{{ fmtBRL(resolver.row.custo_produto) }}</b><template v-if="resolver.row.custo_detalhe"> · {{ resolver.row.custo_detalhe }}</template>
            </div>
          </div>
          <!-- 23/09 (Vinicius, pedido 294554): o que aconteceu, pra analisar depois. É a
               mesma Observação da coluna (vem preenchida); o texto fica também no
               evento "resolvido" do histórico. -->
          <label class="block space-y-1">
            <span class="block text-xs font-medium">Observação</span>
            <textarea
              v-model="resolver.observacao"
              rows="3"
              placeholder="o que aconteceu? ex.: abrimos 2 disputas e a Shopee recusou as duas"
              class="w-full resize-y rounded-md border bg-background px-2 py-1.5 text-sm"
            />
          </label>
          <!-- 24/09 (Vinicius): ao fechar, dizer se a IA acertou — vira aprendizado dela -->
          <div v-if="resolver.ia" class="space-y-1.5 rounded-md border border-violet-300/60 bg-violet-50/50 px-3 py-2 dark:border-violet-800/60 dark:bg-violet-900/15">
            <div class="flex items-center gap-1.5 text-xs font-medium text-violet-700 dark:text-violet-300"><Bot class="size-3.5" /> A IA de Chamado acertou?</div>
            <div class="line-clamp-3 text-xs text-muted-foreground" :title="resumoDaIa(resolver.ia.texto)">{{ resumoDaIa(resolver.ia.texto) }}</div>
            <div class="flex flex-wrap items-center gap-1.5">
              <Button size="sm" :variant="resolver.iaAval === 'certo' ? 'default' : 'outline'" class="h-7 px-2 text-xs" @click="resolver.iaAval = resolver.iaAval === 'certo' ? '' : 'certo'"><ThumbsUp class="size-3.5 mr-1" /> acertou</Button>
              <Button size="sm" :variant="resolver.iaAval === 'errado' ? 'destructive' : 'outline'" class="h-7 px-2 text-xs" @click="resolver.iaAval = resolver.iaAval === 'errado' ? '' : 'errado'"><ThumbsDown class="size-3.5 mr-1" /> errou</Button>
              <span v-if="!resolver.iaAval" class="text-[11px] text-muted-foreground">(opcional)</span>
            </div>
            <textarea
              v-if="resolver.iaAval === 'errado'"
              v-model="resolver.iaCorrecao"
              rows="2"
              placeholder="o que era o certo? — fica de aprendizado pra IA (o chamado fecha, ela não refaz)"
              class="w-full resize-y rounded-md border bg-background px-2 py-1.5 text-sm"
            />
          </div>
          <div v-if="resolver.erro" class="text-xs text-red-500">{{ resolver.erro }}</div>
        </div>
        <div class="flex items-center justify-end gap-2 border-t px-4 py-3">
          <Button size="sm" variant="ghost" @click="closeResolver">Cancelar</Button>
          <Button size="sm" :disabled="!canEdit || resolver.saving || !resolverValorOk || !resolverSituacaoOk" @click="confirmarResolver">
            <Loader2 v-if="resolver.saving" class="size-4 mr-1.5 animate-spin" />
            <CheckCircle2 v-else class="size-4 mr-1.5" />
            Resolver
          </Button>
        </div>
      </div>
    </div>

    <!-- modal: excluir chamado (21/09, Vinicius): a lixeira da lista e a do histórico
         abrem esta janela (z-[60], por cima do histórico). Mesmo visual e mesma
         validação de situação do resolver; os lançamentos de devolução do pedido
         entram por caixinha, e o texto embaixo diz o que acontece com o estoque. -->
    <div v-if="excluir.open && excluir.row" class="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-4" @click.self="closeExcluir">
      <div class="chamado-dialog w-full max-w-lg rounded-lg border bg-background shadow-xl">
        <div class="flex items-start justify-between gap-3 border-b px-4 py-3">
          <div>
            <div class="text-sm font-semibold inline-flex items-center gap-1.5">
              <Trash2 class="size-4 text-red-500" />
              Excluir chamado · pedido {{ excluir.row.pedido_bling || excluir.row.pedido_marketplace }}
            </div>
            <div class="text-xs text-muted-foreground">
              Chamado {{ excluir.row.chamado || '(sem nº)' }} · {{ nomePlataforma(excluir.row.plataforma).toUpperCase() }} {{ excluir.row.conta || '' }}
            </div>
          </div>
          <button type="button" class="rounded p-1 hover:bg-muted" @click="closeExcluir"><X class="size-4" /></button>
        </div>
        <div class="space-y-3 px-4 py-3 text-sm">
          <p class="text-muted-foreground">O chamado e o histórico dele somem da aba. Esta ação não pode ser desfeita.</p>
          <div v-if="excluir.preview?.abertura_enviada" class="rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-xs text-amber-700 dark:text-amber-300">
            A disputa/revisão já foi aberta {{ plataformaNa(excluir.preview.plataforma || excluir.row.plataforma) }} — excluir aqui não cancela lá; feche na plataforma também.
          </div>
          <template v-if="excluirExigeSituacao">
            <div class="text-xs">
              <span class="text-muted-foreground">Situação atual no Bling:</span>
              <b class="ml-1">{{ excluirSituacaoAtual }}</b>
            </div>
            <label class="block space-y-1">
              <span class="text-xs font-medium">Nova situação no Bling <span class="text-red-500">*</span></span>
              <select v-model="excluir.situacao" class="h-9 w-full rounded-md border bg-background px-2 text-sm" :class="excluirSituacaoOk ? '' : 'ring-1 ring-red-500/60'">
                <option value="" disabled>escolha…</option>
                <template v-if="opcoesFechamento(excluir.row).length">
                  <option v-for="s in opcoesFechamento(excluir.row)" :key="s" :value="s">{{ s }}</option>
                  <option disabled>──────</option>
                </template>
                <option v-for="s in situacoes.filter((x) => !opcoesFechamento(excluir.row!).includes(x))" :key="s" :value="s">{{ s }}</option>
              </select>
            </label>
          </template>
          <div v-else class="text-[11px] text-muted-foreground">Sem pedido Bling — nada a trocar no Bling.</div>
          <div class="space-y-1.5">
            <div class="text-xs font-medium">
              Lançamentos de devolução deste pedido
              <span v-if="excluir.preview && !excluirPodeLancamentos" class="font-normal text-amber-700 dark:text-amber-300">— sem permissão pra excluir lançamentos</span>
            </div>
            <div v-if="excluir.loading" class="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground">
              <Loader2 class="size-3.5 animate-spin" /> carregando…
            </div>
            <div v-else-if="!excluir.preview" class="text-[11px] text-muted-foreground">não deu pra carregar os lançamentos deste pedido</div>
            <div v-else-if="!excluir.preview.lancamentos.length" class="text-[11px] text-muted-foreground">nenhum lançamento de devolução neste pedido</div>
            <div v-else class="max-h-60 space-y-1 overflow-auto">
              <label
                v-for="l in excluir.preview.lancamentos"
                :key="l.id"
                class="flex items-start gap-2 rounded border px-2 py-1.5"
                :class="excluirPodeLancamentos && !excluir.saving ? 'cursor-pointer hover:bg-muted/40' : 'cursor-default opacity-60'"
              >
                <input
                  type="checkbox"
                  class="mt-0.5 size-3.5 shrink-0 accent-primary"
                  :checked="excluir.selecionados.has(l.id)"
                  :disabled="!excluirPodeLancamentos || excluir.saving"
                  @change="toggleLancamento(l.id)"
                />
                <div class="min-w-0 space-y-0.5">
                  <div class="text-xs">{{ lancamentoTexto(l) }}</div>
                  <div class="text-[11px]" :class="estoqueTexto(l).alerta ? 'text-amber-700 dark:text-amber-300' : 'text-muted-foreground'">{{ estoqueTexto(l).texto }}</div>
                  <div v-if="CONDICOES_COM_PREJUIZO.includes(l.condicao_produto || '')" class="text-[11px] text-muted-foreground">
                    leva junto o prejuízo {{ l.condicao_produto }} que o DaVinci lançou na tela Reembolso ({{ l.condicao_produto === 'Manutenção' ? 'se o valor ainda for só o débito do conserto e ninguém conferiu' : 'se ninguém lançou reembolso, conferiu, anotou protocolo ou observação' }})
                  </div>
                  <div v-if="l.manutencao && l.condicao_produto !== 'Manutenção'" class="text-[11px] text-muted-foreground">
                    leva junto o débito do conserto (Manutenção) que o DaVinci lançou para esta linha, se ninguém mexeu nele
                  </div>
                </div>
              </label>
            </div>
          </div>
          <div v-if="excluir.erro" class="text-xs text-red-500">{{ excluir.erro }}</div>
        </div>
        <div class="flex items-center justify-end gap-2 border-t px-4 py-3">
          <Button size="sm" variant="ghost" @click="closeExcluir">Cancelar</Button>
          <Button size="sm" variant="destructive" :disabled="!canDelete || excluir.loading || excluir.saving || !excluirSituacaoOk" @click="confirmarExcluir">
            <Loader2 v-if="excluir.saving" class="size-4 mr-1.5 animate-spin" />
            <Trash2 v-else class="size-4 mr-1.5" />
            Excluir
          </Button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
@media (max-width: 639px) {
  .chamado-dialog { max-height: calc(100dvh - 2rem); overflow-y: auto; }
  .chamado-dialog > .border-t { flex-wrap: wrap; }
  .chamado-dialog > div:first-child > div { min-width: 0; overflow-wrap: anywhere; }
}
</style>
