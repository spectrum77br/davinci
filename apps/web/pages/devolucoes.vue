<script setup lang="ts">
// Painel de Devoluções (refeito em 16/11/2026, Vinicius).
//
// Uma lista só: entra quem cair em Aguardando Devolução no Bling e só sai no
// botão Resolver. Uma linha por produto; clicar abre a janela grande, onde
// tudo acontece: observação, rastreio, vídeo, Fraude, LANÇAMENTO
// (components/DevolucaoLancar.vue — estoque no Bling, fotos, chamado
// automático, lixeira), situação do Bling e Resolver.
// Padrão visual da Logística / Chamados (faixa azul, cartões, Onde agir hoje).
//
// A tela de antes (abas Acompanhamento / Fraude / Lançamentos) está guardada
// em pages/devolucoes-antigo.vue, fora do menu: é o caminho de volta.
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { Check, Copy, Download, ExternalLink, Megaphone, Plus, RefreshCw, Search, X } from 'lucide-vue-next'
import { isoDateBrt, isoToday } from '~/lib/date'

definePageMeta({ middleware: ['permission'], permission: { resource: 'devolucoes', action: 'view' } })

const { api } = useApi()
const canEdit = useCan('devolucoes', 'edit')
const canDelete = useCan('devolucoes', 'delete')
const isAdmin = useIsAdmin()
// Botão Informar (Threema): só admin, como na tela de antes.
const informarOpen = ref(false)
const toasts = useToasts()

type Etapa = 'aguardando' | 'chegou' | 'manutencao' | 'fraude'
type Lancamento = {
  id: string
  sku: string | null
  produtos: string | null
  quantidade: number
  condicao: string | null
  condicao_tela: string | null
  motivo: string | null
  sku_lancado: string | null
  estoque_status: string | null
  data_devolvido_estoque: string | null
  manutencao_pendente: boolean
  tecnico: string | null
  prazo_manutencao: string | null
  link_envio: string | null
  link_abertura: string | null
  observacao: string | null
  anexos: { id: string; content_type: string | null; filename: string | null }[]
  created_at: string | null
  custo_produto: number | null
  custo_manutencao: number | null
}
type Item = {
  pedido_bling: string | null
  pedido_marketplace: string | null
  data: string | null
  aguardando_devolucao_data: string | null
  aguardando_devolucao_data_estimada: boolean
  dias_em_devolucao: number | null
  devolucao_chegou_em: string | null
  plataforma: string | null
  loja: string | null
  cliente: string | null
  cidade: string | null
  uf: string | null
  sku: string | null
  produto: string | null
  quantidade: number | null
  rastreio: string | null
  localizacao: string | null
  localizacao_data: string | null
  entrega_localizacao: string | null
  // A ida (envio original) chegou ao cliente? (16/11: Fraude com a ida a caminho)
  ida_situacao: 'entregue' | 'a_caminho' | 'falhou' | null
  lancada: boolean
  observacao: string | null
  prazo_resposta: string | null
  acao_resposta: string | null
  reembolso: boolean | null
  reembolso_valor: number | null
  reembolso_em: string | null
  reembolso_detalhe: string | null
  fila: 'acompanhamento' | 'fraude'
  fila_manual: boolean
  prazo_texto: string | null
  prazo_ate: string | null
  amazon_cancelar: boolean
  amazon_cancelar_texto: string | null
  video_status: 'nao_solicitado' | 'pendente' | 'enviado' | 'sem_video'
  video_link: string | null
  video_solicitado_em: string | null
  video_solicitado_por: string | null
  video_enviado_em: string | null
  video_enviado_por: string | null
  video_sem_motivo: string | null
  video_refazer_motivo: string | null
  situacao_bling_id: string | null
  situacao_bling_nome: string | null
  etapa: Etapa
  falta_lancar: boolean
  prazo_em: string | null
  prazo_tipo: string | null
  prazo_estado: 'vencido' | 'urgente' | null
  prazo_dica: string | null
  lancamentos: Lancamento[]
  condicao_tela: string | null
  sku_lancado: string | null
  lancamentos_a_mais: number
  chamado: { id: string; numero: string | null; resolvido: boolean; resolvido_at: string | null; abertura_status?: string | null } | null
  abrir_chamado?: boolean | null
  tem_chamado_aberto: boolean
  conta: { recebido: number; custo_produto: number; custo_manutencao: number; resultado: number } | null
  pronto_resolver: string | null
  resolvido: boolean
  resolvido_at: string | null
  resolvido_como: string | null
  resolvido_por_nome: string | null
  primeira_do_pedido: boolean
  linhas_do_pedido: number
}
type Painel = {
  items: Item[]
  total_pedidos: number
  como_terminou: string[]
  pode_mover_painel: boolean
  pode_resolver: boolean
  ve_custo: boolean
  com_cerca_de_equipe: boolean
}
type ResumoDia = {
  dia: string
  abertas: number
  novas: number
  resolvidas: number
  estoque_novo: number
  estoque_usado: number
  perdimento: number
  manutencao: number
  recebido: number | null
  custos: number | null
  resultado: number | null
}

// ── Estado ──────────────────────────────────────────────────────────────
const items = ref<Item[]>([])
const comoTerminou = ref<string[]>([])
const podeMover = ref(false)
// Resolver / reabrir: só adm e o Cairo (Vinicius, 16/11/2026).
const podeResolver = ref(false)
const veCusto = ref(false)
const comCerca = ref(false)
const loading = ref(false)
const error = ref<string | null>(null)
const lidoEm = ref<Date | null>(null)
const mostrar = ref<'abertas' | 'resolvidas' | 'todas'>('abertas')

const PLATAFORMAS = [
  { key: 'todas', label: 'Todas' },
  { key: 'ml', label: 'Mercado Livre' },
  { key: 'shopee', label: 'Shopee' },
  { key: 'tiktok', label: 'TikTok' },
  { key: 'amazon', label: 'Amazon' },
] as const
type PlataformaKey = (typeof PLATAFORMAS)[number]['key'] | 'outras'
const plataformaTab = ref<PlataformaKey>('todas')
const contaFilter = ref('all')
const etapaFilter = ref<'all' | Etapa>('all')
const dataIni = ref('')
const dataFim = ref('')
const search = ref('')
// Cartão / etiqueta / "Onde agir" clicado (um por vez; clicar de novo solta).
const recorte = ref<string>('')

const FILTROS_KEY = 'devolucoes-novo:filtros'

function plataformaKey(p: string | null): PlataformaKey {
  const s = (p || '').toLowerCase()
  if (s.includes('mercado') || s === 'ml' || s.includes('meli')) return 'ml'
  if (s.includes('shopee')) return 'shopee'
  if (s.includes('tiktok') || s.includes('tik tok')) return 'tiktok'
  if (s.includes('amazon')) return 'amazon'
  return 'outras'
}
const PLATAFORMA_NOME: Record<PlataformaKey, string> = {
  todas: '', ml: 'Mercado Livre', shopee: 'Shopee', tiktok: 'TikTok', amazon: 'Amazon', outras: '',
}
// A coluna é só a conta: "ML Jlas 2" → "Jlas 2" (a plataforma já é a aba).
function contaDe(i: Item): string {
  return (i.loja || '—').replace(/^(mercado\s*livre|ml|meli|shopee|tiktok(\s*shop)?|amazon)\s+/i, '').trim() || '—'
}

let cargaAtual = 0
async function load(silencioso = false) {
  if (!silencioso) loading.value = true
  // Só a carga MAIS NOVA vale: trocar "Mostrar" rápido (ou o relógio no meio)
  // não pode deixar a lista de um modo com o seletor em outro.
  const minha = ++cargaAtual
  try {
    // Resolvidas/Todas: o início do Período vai pro servidor (o histórico antigo
    // vem desde maio/2026; sem período, os últimos 60 dias).
    const desde = mostrar.value !== 'abertas' && dataIni.value ? `&desde=${dataIni.value}` : ''
    const res = await api<Painel>(`/api/devolucoes-painel?mostrar=${mostrar.value}${desde}`)
    if (minha !== cargaAtual) return
    items.value = res.items
    comoTerminou.value = res.como_terminou
    podeMover.value = res.pode_mover_painel
    podeResolver.value = !!res.pode_resolver
    veCusto.value = res.ve_custo
    comCerca.value = res.com_cerca_de_equipe
    lidoEm.value = new Date()
    error.value = null
  } catch (e: any) {
    if (minha === cargaAtual) error.value = apiError(e)
  } finally {
    if (minha === cargaAtual) loading.value = false
  }
}

function apiError(e: any): string {
  const d = e?.data?.detail
  if (d && typeof d === 'object') return d.message || d.code || 'erro'
  if (typeof d === 'string') return d
  return e?.message || 'erro'
}

// ── Datas e dinheiro ────────────────────────────────────────────────────
function ddmm(iso: string | null | undefined): string {
  if (!iso) return '—'
  const dia = iso.length <= 10 ? iso : isoDateBrt(new Date(iso))
  return `${dia.slice(8, 10)}/${dia.slice(5, 7)}`
}
function ddmmaaaa(iso: string | null | undefined): string {
  if (!iso) return '—'
  const dia = iso.length <= 10 ? iso : isoDateBrt(new Date(iso))
  return `${dia.slice(8, 10)}/${dia.slice(5, 7)}/${dia.slice(0, 4)}`
}
const _HORA = new Intl.DateTimeFormat('pt-BR', { timeZone: 'America/Sao_Paulo', hour: '2-digit', minute: '2-digit' })
function hhmm(iso: string): string {
  return _HORA.format(new Date(iso))
}
function brl(n: number | null | undefined): string {
  if (n == null) return '—'
  return n.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
}
function valor(n: number | null | undefined): string {
  if (n == null || n === 0) return '—'
  return n.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function sinal(n: number): string {
  return `${n < 0 ? '−' : n > 0 ? '+' : ''} ${brl(Math.abs(n))}`.trim()
}
function diasEntre(aIso: string, bIso: string): number {
  return Math.round((new Date(`${aIso}T12:00:00`).getTime() - new Date(`${bIso}T12:00:00`).getTime()) / 86400000)
}

// Coluna Prazo: a data do prazo da etapa + a palavra dizendo do que é.
function prazoTexto(i: Item): string {
  if (!i.prazo_em) return i.prazo_tipo ? 'agora' : '—'
  const dia = isoDateBrt(new Date(i.prazo_em))
  const falta = diasEntre(dia, isoToday())
  const comHora = i.prazo_tipo === 'disputa' || i.prazo_tipo === 'responder' || i.prazo_tipo === 'contestar'
  if (falta < 0) return `venceu ${ddmm(dia)}`
  if (falta === 0) return comHora ? `hoje ${hhmm(i.prazo_em)}` : (i.prazo_estado === 'vencido' ? 'venceu hoje' : 'hoje')
  if (falta === 1) return comHora ? `amanhã ${hhmm(i.prazo_em)}` : 'amanhã'
  return ddmm(dia)
}
function prazoCor(i: Item): string {
  if (i.prazo_estado === 'vencido') return 'font-bold text-red-600 dark:text-red-400'
  if (i.prazo_estado === 'urgente') return 'font-bold text-amber-700 dark:text-amber-400'
  return ''
}

const ETAPA_LABEL: Record<Etapa, string> = {
  aguardando: 'Aguardando', chegou: 'Chegou', manutencao: 'Manutenção', fraude: 'Fraude',
}
const ETAPA_COR: Record<Etapa, string> = {
  aguardando: 'bg-sky-100 text-sky-800 dark:bg-sky-950/50 dark:text-sky-300',
  chegou: 'bg-amber-100 text-amber-800 dark:bg-amber-950/50 dark:text-amber-300',
  manutencao: 'bg-indigo-100 text-indigo-800 dark:bg-indigo-950/50 dark:text-indigo-300',
  fraude: 'bg-red-100 text-red-800 dark:bg-red-950/50 dark:text-red-300',
}
// Resolvida é o fim da linha (16/11, Vinicius: "a etapa tem que ir para Resolvida
// e não ficar chegou lançada ou chegou resolvida"). Só a tela: a etapa de onde ele
// veio continua valendo pros filtros.
const COR_RESOLVIDA = 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/50 dark:text-emerald-300'
function etapaRotulo(i: Item): string {
  return i.resolvido ? 'Resolvida' : ETAPA_LABEL[i.etapa]
}
function etapaCor(i: Item): string {
  return i.resolvido ? COR_RESOLVIDA : ETAPA_COR[i.etapa]
}
const ESTOQUE_TEXTO: Record<string, string> = {
  ok: 'entrou no Bling', falhou: 'NÃO entrou no Bling', conferindo: 'conferindo no Bling',
  sem_confirmacao: 'entrada não confirmada',
}
function estoquePendente(i: Item): boolean {
  return i.lancamentos.some((l) => ['falhou', 'conferindo', 'sem_confirmacao'].includes(l.estoque_status || ''))
}
function temAbertura(i: Item): boolean {
  return i.lancamentos.some((l) => !!(l.link_abertura || '').trim())
}
function ehFoto(a: { content_type: string | null }): boolean {
  return (a.content_type || '').startsWith('image/')
}
function temFoto(i: Item): boolean {
  return i.lancamentos.some((l) => l.anexos.some(ehFoto))
}
function urgente(i: Item): boolean {
  return !!i.prazo_estado && ['disputa', 'responder', 'contestar'].includes(i.prazo_tipo || '')
}
// "A finalizar": terminou (Perdimento, Extraviado, não devolveu, o Bling já saiu
// da devolução…) e não volta inteiro pro estoque — o que volta, o sistema resolve
// sozinho. Fica pro adm ou o Cairo analisar e clicar Resolver.
function aFinalizar(i: Item): boolean {
  return !!i.pronto_resolver
}
// Fraude que o robô deixou pra pessoa (services/devolucao_fraude_auto: NAO_RESPONDE)
// ou em que a equipe respondeu "não tenho o vídeo".
function idaNaoEntregue(i: Item): boolean {
  return i.etapa === 'fraude' && !i.lancamentos.length && (i.ida_situacao === 'a_caminho' || i.ida_situacao === 'falhou')
}
function fraudeAMao(i: Item): boolean {
  if (i.etapa !== 'fraude' || i.lancamentos.length || idaNaoEntregue(i)) return false
  // O recado do robô termina em "(responder à mão)" — CARTAO_A_MAO em services/devolucao_fraude_auto.py.
  return (i.observacao || '').includes('responder à mão') || i.video_status === 'sem_video'
}

// ── Filtros ─────────────────────────────────────────────────────────────
const naAba = computed(() =>
  plataformaTab.value === 'todas' ? items.value : items.value.filter((i) => plataformaKey(i.plataforma) === plataformaTab.value),
)
const contas = computed(() => [...new Set(naAba.value.map(contaDe))].sort((a, b) => a.localeCompare(b)))
// Base dos cartões e do "Onde agir": aba + conta + período + busca.
// Busca sem diferença de acento nem de maiúscula ("oferecido" acha "Oferecido").
function semAcento(v: string): string {
  return v.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase()
}
const base = computed(() => {
  const q = semAcento(search.value.trim())
  return naAba.value.filter((i) => {
    if (contaFilter.value !== 'all' && contaDe(i) !== contaFilter.value) return false
    const dia = i.data ? isoDateBrt(new Date(i.data)) : ''
    if (dataIni.value && (!dia || dia < dataIni.value)) return false
    if (dataFim.value && (!dia || dia > dataFim.value)) return false
    if (!q) return true
    // 16/11 (Vinicius): também a coluna Obs — o recado do pedido e a observação
    // de cada lançamento —, o motivo e a condição lançados.
    return [
      i.pedido_bling, i.pedido_marketplace, i.cliente, i.sku, i.produto, i.rastreio, i.localizacao,
      i.cidade, i.observacao, i.loja, i.sku_lancado, obsDaLinha(i), i.resolvido_como,
      ...i.lancamentos.flatMap((l) => [l.observacao, l.motivo, l.condicao_tela, l.sku_lancado]),
    ].some((v) => semAcento(String(v || '')).includes(q))
  })
})

type Agir = { chave: string; titulo: string; detalhe: string; dot: string; grave: boolean; teste: (i: Item) => boolean }
const AGIR: Agir[] = [
  { chave: 'segurado', titulo: 'Chamado segurado pela IA', detalhe: 'o motivo não bate — decidir antes de mandar', dot: 'bg-red-600', grave: true, teste: (i) => i.chamado?.abertura_status === 'segurada' },
  // 16/11 (Vinicius): a Fraude automática responde sozinha o só reembolso; o que ela
  // não responde (danificado, alegação estranha, equipe sem vídeo) vem pra cá.
  { chave: 'fraude_mao', titulo: 'Fraude: responder à mão', detalhe: 'o sistema não responde esse caso sozinho', dot: 'bg-red-600', grave: true, teste: fraudeAMao },
  // 16/11: reembolso pedido SEM o cliente ter recebido (a ida ainda a caminho ou
  // falhou) — não é golpe; acompanhar a entrega (se falhar, o pacote volta pela ida).
  { chave: 'ida', titulo: 'Fraude: ida não entregue', detalhe: 'o cliente pediu reembolso sem ter recebido — acompanhar a entrega', dot: 'bg-amber-500', grave: false, teste: idaNaoEntregue },
  { chave: 'responder', titulo: 'Responder em 24 h', detalhe: 'prazo da plataforma vencendo', dot: 'bg-red-600', grave: true, teste: urgente },
  { chave: 'estoque', titulo: 'Estoque não entrou no Bling', detalhe: 'conferir e tentar de novo no lançamento', dot: 'bg-red-600', grave: true, teste: estoquePendente },
  { chave: 'falta_lancar', titulo: 'Chegou e falta lançar', detalhe: 'lançar condição e estoque', dot: 'bg-red-600', grave: false, teste: (i) => i.falta_lancar },
  // 16/11 (Vinicius): o que volta inteiro pro estoque resolve sozinho; perdimento,
  // extraviado, não devolveu… "o Cairo ou adm analisa e finaliza".
  { chave: 'pronto', titulo: 'A finalizar', detalhe: 'perdimento, extraviado, Bling já fora: adm ou Cairo finaliza', dot: 'bg-blue-600', grave: false, teste: aFinalizar },
  { chave: 'prazo45', titulo: 'Passou de 45 dias', detalhe: 'pacote não chegou (ou vence em até 5 dias)', dot: 'bg-amber-500', grave: false, teste: (i) => i.prazo_tipo === 'pacote chegar' && !!i.prazo_estado },
  { chave: 'amazon', titulo: 'Amazon: cancelar devolução', detalhe: 'cliente não postou em 14 dias', dot: 'bg-amber-500', grave: false, teste: (i) => i.amazon_cancelar },
  { chave: 'tecnico', titulo: 'Manutenção vencendo', detalhe: 'prazo de 30 dias do técnico', dot: 'bg-amber-500', grave: false, teste: (i) => i.etapa === 'manutencao' && i.prazo_tipo === 'técnico' && (!!i.prazo_estado || (!!i.prazo_em && diasEntre(isoDateBrt(new Date(i.prazo_em)), isoToday()) <= 5)) },
  { chave: 'sem_rastreio', titulo: 'Sem rastreio', detalhe: 'pacote de volta sem código', dot: 'bg-slate-400', grave: false, teste: (i) => i.etapa === 'aguardando' && !(i.rastreio || '').trim() },
]
function pedidos(rows: Item[]): number {
  return new Set(rows.map((i) => i.pedido_bling)).size
}
// Com o filtro Etapa aplicado: é o que a lista mostra antes de qualquer
// recorte. Etiquetas e "Onde agir" contam daqui — o número do botão é o
// número de linhas que o clique lista (quem cuida só da Fraude vê só a Fraude).
const comEtapa = computed(() =>
  etapaFilter.value === 'all' ? base.value : base.value.filter((i) => i.etapa === etapaFilter.value),
)
const agirItens = computed(() =>
  AGIR.map((a) => ({ ...a, n: pedidos(comEtapa.value.filter((i) => !i.resolvido && a.teste(i))) })).filter((a) => a.n > 0),
)
// Os cartões de etapa SÃO o filtro Etapa (clicar troca o filtro, não soma) e
// contam sempre o total da etapa, com ou sem filtro ligado.
const cartoes = computed(() => {
  const r = base.value
  const titulo = mostrar.value === 'resolvidas' ? 'Resolvidas' : mostrar.value === 'todas' ? 'Abertas + resolvidas' : 'Em aberto'
  return [
    { k: 'all', label: titulo, n: pedidos(r), cor: '', sub: 'pedidos' },
    { k: 'aguardando', label: 'Aguardando', n: pedidos(r.filter((i) => i.etapa === 'aguardando')), cor: 'text-sky-700 dark:text-sky-400', sub: 'pacote a caminho' },
    { k: 'falta_lancar', label: 'Chegou (falta lançar)', n: pedidos(r.filter((i) => !i.resolvido && i.falta_lancar)), cor: 'text-amber-600 dark:text-amber-400', sub: 'lançar condição' },
    { k: 'manutencao', label: 'Manutenção', n: pedidos(r.filter((i) => i.etapa === 'manutencao')), cor: 'text-indigo-700 dark:text-indigo-400', sub: 'com o técnico' },
    { k: 'fraude', label: 'Fraude', n: pedidos(r.filter((i) => i.etapa === 'fraude')), cor: 'text-red-600 dark:text-red-400', sub: 'não volta pacote' },
    { k: 'a_finalizar', label: 'A finalizar', n: pedidos(r.filter((i) => !i.resolvido && aFinalizar(i))), cor: 'text-blue-700 dark:text-blue-400', sub: 'adm ou Cairo finaliza' },
  ]
})
// Os dois cartões que são recorte (não etapa): o cartão liga o recorte.
const CARTAO_RECORTE: Record<string, string> = { falta_lancar: 'falta_lancar', a_finalizar: 'agir:pronto' }
function cartaoLigado(k: string): boolean {
  if (k in CARTAO_RECORTE) return recorte.value === CARTAO_RECORTE[k]
  if (k === 'all') return false
  return etapaFilter.value === k && !recorte.value
}
function clicarCartao(k: string) {
  if (k in CARTAO_RECORTE) {
    etapaFilter.value = 'all'
    recorte.value = recorte.value === CARTAO_RECORTE[k] ? '' : CARTAO_RECORTE[k]
    return
  }
  recorte.value = ''
  etapaFilter.value = k === 'all' || etapaFilter.value === k ? 'all' : (k as Etapa)
}
const SITUACAO_DOT: Record<string, string> = {
  'aguardando devolução': 'bg-amber-500', manutenção: 'bg-indigo-600', problemas: 'bg-red-600',
  resolvido: 'bg-emerald-600', extraviado: 'bg-red-600', perdimento: 'bg-slate-500',
}
const statusPills = computed(() => {
  const m = new Map<string, Set<string | null>>()
  for (const i of comEtapa.value) {
    const nome = i.situacao_bling_nome || 'sem situação'
    if (!m.has(nome)) m.set(nome, new Set())
    m.get(nome)!.add(i.pedido_bling)
  }
  return [...m.entries()]
    .map(([nome, s]) => ({ nome, n: s.size, dot: SITUACAO_DOT[nome.toLowerCase()] || 'bg-slate-400' }))
    .sort((a, b) => b.n - a.n)
})

const filtrados = computed(() => {
  let rows = comEtapa.value
  const r = recorte.value
  if (r === 'falta_lancar') rows = rows.filter((i) => !i.resolvido && i.falta_lancar)
  else if (r.startsWith('bling:')) rows = rows.filter((i) => (i.situacao_bling_nome || 'sem situação') === r.slice(6))
  else if (r.startsWith('agir:')) {
    const a = AGIR.find((x) => x.chave === r.slice(5))
    if (a) rows = rows.filter((i) => !i.resolvido && a.teste(i))
  }
  return ordenar(rows)
})
// Ordem da lista: primeiro quem tem prazo da plataforma estourando, depois os
// pedidos mais recentes; os produtos do mesmo pedido ficam juntos.
function ordenar(rows: Item[]): Item[] {
  const chave = new Map<string, [number, string]>()
  for (const i of rows) {
    const p = i.pedido_bling || ''
    const u = !i.resolvido && urgente(i) ? 0 : 1
    const d = i.data || ''
    const atual = chave.get(p)
    chave.set(p, atual ? [Math.min(atual[0], u), atual[1] > d ? atual[1] : d] : [u, d])
  }
  return [...rows].sort((a, b) => {
    const ka = chave.get(a.pedido_bling || '')!
    const kb = chave.get(b.pedido_bling || '')!
    if (ka[0] !== kb[0]) return ka[0] - kb[0]
    if (ka[1] !== kb[1]) return ka[1] < kb[1] ? 1 : -1
    if (a.pedido_bling !== b.pedido_bling) return (a.pedido_bling || '') < (b.pedido_bling || '') ? 1 : -1
    return (a.sku || '').localeCompare(b.sku || '')
  })
}
const filtrosAtivos = computed(
  () => contaFilter.value !== 'all' || etapaFilter.value !== 'all' || !!dataIni.value || !!dataFim.value || !!search.value.trim() || !!recorte.value,
)
function limparFiltros() {
  contaFilter.value = 'all'
  etapaFilter.value = 'all'
  dataIni.value = ''
  dataFim.value = ''
  search.value = ''
  recorte.value = ''
}
// Conta e aviso de estoque são do PEDIDO: aparecem na 1ª linha dele que está
// na tela (a busca por SKU pode esconder a 1ª linha de verdade).
const contaPorPedido = computed(() => {
  const m = new Map<string, NonNullable<Item['conta']>>()
  for (const i of [...avulsas.value, ...items.value]) if (i.conta && i.pedido_bling) m.set(i.pedido_bling, i.conta)
  return m
})
function primeiraVisivel(n: number): boolean {
  return n === 0 || filtrados.value[n - 1]?.pedido_bling !== filtrados.value[n]?.pedido_bling
}
function contaDaLinha(i: Item, n: number): Item['conta'] {
  return primeiraVisivel(n) && i.pedido_bling ? contaPorPedido.value.get(i.pedido_bling) || null : null
}
// Coluna Condição: uma linha por PEÇA lançada. O pedido "celular + brinde"
// vira duas peças no lançamento (o brinde volta Novo, o celular vai pro
// técnico): mostrar só a primeira com "+1" não dizia o que era de quem.
function partesDoSku(sku: string | null): string[] {
  return (sku || '').toLowerCase().split('+').map((x) => x.trim()).filter(Boolean)
}
function lancamentosDaLinha(i: Item, n: number): Lancamento[] {
  if (i.linhas_do_pedido <= 1) return i.lancamentos
  const meus = i.lancamentos.filter((l) => partesDoSku(i.sku).includes((l.sku || '').toLowerCase()))
  if (meus.length || !primeiraVisivel(n)) return meus
  // Peça que não casa com produto nenhum do pedido (troca de SKU): na 1ª linha.
  const todos = new Set(items.value.filter((x) => x.pedido_bling === i.pedido_bling).flatMap((x) => partesDoSku(x.sku)))
  return i.lancamentos.filter((l) => !todos.has((l.sku || '').toLowerCase()))
}
function rotuloLanc(l: Lancamento): string {
  return l.manutencao_pendente ? 'no técnico' : (l.condicao_tela || 'sem condição')
}
// Etiqueta da Condição na lista (opção C, escolhida pelo Vinicius em 16/11): a
// cor diz o destino da peça. Verde só no que terminou bem (Novo no estoque);
// com a entrada de estoque pendurada a etiqueta fica cinza — "verde parece que
// resolveu".
const COR_LANC: Record<string, string> = {
  Novo: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/50 dark:text-emerald-300',
  Usado: 'bg-amber-100 text-amber-800 dark:bg-amber-950/50 dark:text-amber-300',
  Perdimento: 'bg-red-100 text-red-800 dark:bg-red-950/50 dark:text-red-300',
  tecnico: 'bg-indigo-100 text-indigo-800 dark:bg-indigo-950/50 dark:text-indigo-300',
  outro: 'bg-slate-200 text-slate-700 dark:bg-slate-800 dark:text-slate-300',
  pendente: 'bg-muted text-muted-foreground',
}
function etiquetaLanc(l: Lancamento): { texto: string; cor: string } {
  if (l.manutencao_pendente) return { texto: 'Técnico', cor: COR_LANC.tecnico }
  const texto = l.condicao_tela || 'sem condição'
  if (!l.condicao_tela || ['falhou', 'conferindo', 'sem_confirmacao'].includes(l.estoque_status || '')) return { texto, cor: COR_LANC.pendente }
  return { texto, cor: COR_LANC[texto] || COR_LANC.outro }
}
// Marca da largada (o histórico de antes do painel novo): "feito antes de mudar
// o painel" (Vinicius, 16/11/2026).
// As duas marcas do sistema: a da virada (0483/0484) e a do histórico (0485).
const LARGADA = 'antes do painel novo'
function ehLargada(como: string | null): boolean {
  return !!como && como.startsWith(LARGADA)
}
// Resolvido pelo sistema quando todas as peças voltaram pro estoque (16/11/2026).
const SOZINHO = 'Voltou pro estoque (resolvido sozinho)'
// Resolvido pelo sistema quando a plataforma cancelou o pedido (16/11/2026, 308473).
const CANCELADO_SOZINHO = 'Cliente cancelou a devolução (resolvido sozinho)'
function comoTerminou_(como: string | null): string {
  if (como === SOZINHO) return 'voltou pro estoque — o sistema resolveu sozinho'
  if (como === CANCELADO_SOZINHO) return 'o cliente cancelou na plataforma — o sistema resolveu sozinho'
  return ehLargada(como) ? 'feito antes de mudar o painel' : (como || '')
}
function obsDaLinha(i: Item): string {
  const marca = i.resolvido && ehLargada(i.resolvido_como) ? 'feito antes de mudar o painel' : ''
  return [marca, i.observacao || ''].filter(Boolean).join(' · ')
}
function tituloLanc(l: Lancamento): string {
  const estoque = l.sku_lancado ? ` · entrou no estoque em ${l.sku_lancado}` : ''
  return `${l.sku || ''}: ${rotuloLanc(l)}${estoque}${l.motivo ? ` · ${l.motivo}` : ''}`
}
// "Aguardando Devolução" não cabe numa linha: 1ª palavra em cima, o resto embaixo.
function duasLinhas(nome: string): [string, string] {
  const k = nome.indexOf(' ')
  return k < 0 ? [nome, ''] : [nome.slice(0, k), nome.slice(k + 1)]
}
function alternarRecorte(k: string) {
  recorte.value = recorte.value === k ? '' : k
}
function contagemDaAba(k: PlataformaKey): number {
  const rows = k === 'todas' ? items.value : items.value.filter((i) => plataformaKey(i.plataforma) === k)
  return pedidos(rows)
}

// O filtro de cada pessoa fica guardado no navegador dela (a pessoa da Fraude
// abre já na etapa Fraude; a do Acompanhamento, na dela).
watch([plataformaTab, contaFilter, etapaFilter], () => {
  try {
    localStorage.setItem(FILTROS_KEY, JSON.stringify({ p: plataformaTab.value, c: contaFilter.value, e: etapaFilter.value }))
  } catch { /* navegador sem armazenamento */ }
})
watch(plataformaTab, () => {
  // Com a lista ainda vazia (abertura da página) não dá pra dizer que a conta
  // salva "não existe nesta aba" — a checagem roda depois da 1ª carga.
  if (!items.value.length) return
  if (contaFilter.value !== 'all' && !contas.value.includes(contaFilter.value)) contaFilter.value = 'all'
})
watch(mostrar, () => { recorte.value = ''; void load() })
watch(dataIni, () => { if (mostrar.value !== 'abertas') void load() })

// ── Excel (csv que o Excel abre) ────────────────────────────────────────
function exportar() {
  const cab = [
    'Data', 'Bling', 'Nº na plataforma', 'Plataforma', 'Conta', 'Cliente', 'SKU', 'Produto', 'Etapa', 'Dias', 'Prazo',
    'Prazo de', 'Reemb. cliente', 'Valor reemb. cliente', 'Localização', 'Rastreio', 'Vídeo envio',
    'Vídeo abertura', 'Foto', 'Condição', 'SKU lançado', 'Chamado', 'Status Bling', 'Observação',
  ]
  const linhas = filtrados.value.map((i) => [
    ddmmaaaa(i.data), i.pedido_bling, i.pedido_marketplace, PLATAFORMA_NOME[plataformaKey(i.plataforma)] || i.plataforma,
    contaDe(i), i.cliente, i.sku, i.produto, etapaRotulo(i), i.dias_em_devolucao ?? '', i.prazo_em ? ddmmaaaa(i.prazo_em) : '',
    i.prazo_tipo, i.reembolso == null ? '' : i.reembolso ? 'Sim' : 'Não',
    i.reembolso_valor != null ? i.reembolso_valor.toFixed(2).replace('.', ',') : '', i.localizacao, i.rastreio,
    i.video_link ? 'Sim' : i.video_status === 'pendente' ? 'pedido' : '', temAbertura(i) ? 'Sim' : '',
    temFoto(i) ? 'Sim' : '', i.lancamentos.map(rotuloLanc).join(' / '), i.lancamentos.map((l) => l.sku_lancado || l.sku || '').join(' / '),
    i.chamado ? (i.tem_chamado_aberto ? 'Aberto' : 'Concluído') : '', i.situacao_bling_nome, obsDaLinha(i),
  ])
  const csv = [cab, ...linhas]
    .map((l) => l.map((c) => `"${String(c ?? '').replace(/"/g, '""')}"`).join(';'))
    .join('\r\n')
  const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `devolucoes-${isoToday()}.csv`
  a.click()
  URL.revokeObjectURL(a.href)
}

// ── Janela ──────────────────────────────────────────────────────────────
const janela = ref<{ pedido: string; sku: string | null } | null>(null)
// Pedido que não está na lista carregada (resolvido, antigo, ou que ainda não
// caiu em Aguardando Devolução): as linhas dele vêm da busca por pedido e
// ficam aqui só enquanto a janela dele está aberta.
const avulsas = ref<Item[]>([])
const linhasDaJanela = computed(() => {
  if (!janela.value) return []
  const daLista = items.value.filter((i) => i.pedido_bling === janela.value!.pedido)
  return daLista.length ? daLista : avulsas.value.filter((i) => i.pedido_bling === janela.value!.pedido)
})
// Linha (produto) em foco e a 1ª do pedido (que carrega chamado e conta).
const foco = computed<Item | null>(
  () => linhasDaJanela.value.find((i) => i.sku === janela.value?.sku) || linhasDaJanela.value[0] || null,
)
const pedidoItem = computed<Item | null>(() => linhasDaJanela.value[0] || null)
const contaDaJanela = computed(() => (janela.value ? contaPorPedido.value.get(janela.value.pedido) || null : null))
const anexosDoPedido = computed(() => (pedidoItem.value?.lancamentos || []).flatMap((l) => l.anexos))
const fotosDoPedido = computed(() => anexosDoPedido.value.filter(ehFoto))
const outrosAnexos = computed(() => anexosDoPedido.value.filter((a) => !ehFoto(a)))
// ── Provas: links e fotos valem pro PEDIDO (16/11/2026, Vinicius: "fica
// duplicado… deixa só em provas; daí no chamado ele leva vídeos e fotos") ──
// No banco o link e a foto moram no lançamento; a janela cola uma vez só e o
// bloco do lançamento grava em todos. Sem lançamento ainda, o link colado e as
// fotos escolhidas ficam esperando aqui e entram junto com o 1º lançamento.
const linkPendente = reactive({ envio: '', abertura: '' })
const linkDigitado = reactive({ envio: '', abertura: '' })
const editandoLink = reactive({ envio: false, abertura: false })
const gravandoLink = ref(false)
const fotosPendentes = ref<File[]>([])
const enviandoFotos = ref(false)
// Link do envio colado à mão: o do lançamento, ou o que espera o 1º lançamento.
const envioColado = computed(
  () => (pedidoItem.value?.lancamentos || []).map((l) => (l.link_envio || '').trim()).find(Boolean) || linkPendente.envio || null,
)
// O que vai pro lançamento: o colado; senão o vídeo respondido pela equipe.
const envioDaJanela = computed(() => envioColado.value || pedidoItem.value?.video_link || '')
const aberturaDaJanela = computed(() => aberturaDoPedido.value || linkPendente.abertura || '')
function limparProvas() {
  obsPendente.value = ''
  linkPendente.envio = linkPendente.abertura = ''
  linkDigitado.envio = linkDigitado.abertura = ''
  editandoLink.envio = editandoLink.abertura = false
  fotosPendentes.value = []
}
function editarLink(qual: 'envio' | 'abertura') {
  linkDigitado[qual] = (qual === 'envio' ? envioColado.value : aberturaDaJanela.value) || ''
  editandoLink[qual] = true
}
function cancelarLink(qual: 'envio' | 'abertura') {
  linkDigitado[qual] = ''
  editandoLink[qual] = false
}
async function salvarLinkProvas(qual: 'envio' | 'abertura') {
  if (gravandoLink.value) return
  const v = linkDigitado[qual].trim()
  const atual = ((qual === 'envio' ? envioColado.value : aberturaDaJanela.value) || '').trim()
  // Campo vazio sem estar trocando um link que existe: a pessoa só passou pelo campo.
  if (v === atual || (!v && !editandoLink[qual])) {
    if (v === atual) cancelarLink(qual)
    return
  }
  gravandoLink.value = true
  try {
    const erro = lancarRef.value
      ? await lancarRef.value.gravarLink(qual === 'envio' ? 'link_envio' : 'link_abertura', v)
      : 'A janela ainda está carregando o lançamento. Tente de novo.'
    if (erro) {
      toasts.push({ kind: 'error', title: 'Link não salvo', lines: [erro] }, 9000)
      return
    }
    // Sem lançamento, o link espera aqui; com lançamento, já foi gravado neles.
    linkPendente[qual] = pedidoItem.value?.lancamentos.length ? '' : v
    cancelarLink(qual)
    toasts.push({ kind: 'success', title: v ? 'Link salvo' : 'Link apagado', lines: pedidoItem.value?.lancamentos.length ? [] : ['Entra no lançamento quando você lançar.'] }, 3000)
  } finally {
    gravandoLink.value = false
  }
}
async function escolherFotosProvas(ev: Event) {
  const input = ev.target as HTMLInputElement
  const files = Array.from(input.files || [])
  input.value = ''
  if (!files.length || !canEdit.value) return
  enviandoFotos.value = true
  try {
    // Com lançamento, sobe na hora; sem, espera o 1º lançamento.
    const subiu = lancarRef.value ? await lancarRef.value.anexarFotos(files) : false
    if (!subiu) fotosPendentes.value = [...fotosPendentes.value, ...files]
  } finally {
    enviandoFotos.value = false
  }
}
function removerFotoProvas(id: string) {
  if (!window.confirm('Remover este arquivo das provas do pedido?')) return
  void lancarRef.value?.removerFoto(id)
}
// Link ou foto em Provas esperando um lançamento que ainda não foi feito.
function provasEsperando(): boolean {
  return !!(linkPendente.envio || linkPendente.abertura || fotosPendentes.value.length || obsPendente.value)
}
const aberturaDoPedido = computed(
  () => (pedidoItem.value?.lancamentos || []).map((l) => (l.link_abertura || '').trim()).find(Boolean) || null,
)
// O link que esperava em Provas já está gravado num lançamento: sai da espera.
watch(
  () => [aberturaDoPedido.value, (pedidoItem.value?.lancamentos || []).some((l) => (l.link_envio || '').trim())] as const,
  ([abertura, envio]) => {
    if (abertura) linkPendente.abertura = ''
    if (envio) linkPendente.envio = ''
  },
)
const rascunho = reactive({ observacao: '', rastreio: '', localizacao: '', desde: '' })
// O que estava nos campos quando a janela abriu. Só vai pro servidor o campo
// que a PESSOA mudou: rastreio e localização mostram o valor AUTOMÁTICO, e
// gravar esse valor sem ninguém ter mexido o transformaria em "digitado à
// mão" (que passa a valer mais que o automático dali em diante).
const original = reactive({ observacao: '', rastreio: '', localizacao: '', desde: '' })
const salvando = ref('')
const videoMotivo = ref('')
const videoApagando = ref(false)
const resolverForm = reactive<{ como: string; situacao: string }>({ como: '', situacao: '' })
const situacoesBling = ref<{ id: number; nome: string }[]>([])

function abrirJanela(i: Item) {
  if (!i.pedido_bling) return
  janela.value = { pedido: i.pedido_bling, sku: i.sku }
  carregarRascunho()
  limparProvas()
  videoMotivo.value = ''
  videoApagando.value = false
  resolverForm.como = ''
  resolverForm.situacao = ''
  if (!situacoesBling.value.length) void carregarSituacoes()
}
const procurando = ref(false)
async function buscarPedido(numero: string): Promise<Item[]> {
  return (await api<Painel>(`/api/devolucoes-painel?pedido=${encodeURIComponent(numero)}`)).items
}
async function abrirPedido(numero: string) {
  const n = numero.trim()
  if (!n || procurando.value) return
  const naLista = items.value.find((i) => i.pedido_bling === n || i.pedido_marketplace === n)
  if (naLista) return abrirJanela(naLista)
  procurando.value = true
  try {
    const linhas = await buscarPedido(n)
    if (!linhas.length) {
      toasts.push({ kind: 'warning', title: `Pedido ${n} não encontrado`, lines: ['Confira o número (do Bling ou da plataforma).'] })
      return
    }
    avulsas.value = linhas
    abrirJanela(linhas[0])
  } catch (e: any) {
    toasts.push({ kind: 'error', title: 'Não deu pra procurar o pedido', lines: [apiError(e)] })
  } finally {
    procurando.value = false
  }
}
function novaDevolucao() {
  const n = window.prompt('Número do pedido (do Bling ou da plataforma):')
  if (n && n.trim()) void abrirPedido(n)
}
// Relê as linhas do pedido aberto quando ele veio da busca (não está na lista).
// `sempre`: relê mesmo com o pedido ainda na lista — é o que se faz ANTES de
// trocar a lista depois de uma ação (resolver, reabrir, mudar o Bling). Se o
// pedido sair dela, a janela já tem as linhas e não desmonta nem remonta (o
// bloco do lançamento perderia o que está digitado).
async function relerAvulsas(sempre = false) {
  const p = janela.value?.pedido
  if (!p || (!sempre && items.value.some((i) => i.pedido_bling === p))) return
  try { avulsas.value = await buscarPedido(p) } catch { /* fica o que estava */ }
}
// O bloco do lançamento criou, alterou ou apagou algo: relê a lista (etapa,
// condição, chamado e conta mudam) sem mexer no que está digitado nos campos
// do pedido.
async function aoMudarLancamento() {
  await load(true)
  await relerAvulsas()
  void loadResumo()
  void chamadoRef.value?.recarregar()
  void lancarRef.value?.recarregar()
}
// ── Bloco Chamado (2ª etapa, 16/11/2026) ──
const chamadoRef = ref<{ recarregar: () => Promise<void> } | null>(null)
// As peças como estão na tela agora (lançadas e por lançar) — a prévia do texto.
const linhasPrevia = computed(() => lancarRef.value?.linhasParaPrevia() ?? [])
// A observação que vai no texto do chamado: UMA pro pedido ("não tá duplicado o
// campo observação?"). No banco mora em cada lançamento; sem lançamento ainda,
// espera aqui e entra junto com o 1º.
const obsPendente = ref('')
const obsMisturadas = computed(
  () => new Set((pedidoItem.value?.lancamentos || []).map((l) => (l.observacao || '').trim()).filter(Boolean)).size > 1,
)
const obsDaJanela = computed(() => {
  const textos = [...new Set((pedidoItem.value?.lancamentos || []).map((l) => (l.observacao || '').trim()).filter(Boolean))]
  return textos.length ? textos.join(' · ') : obsPendente.value
})
async function salvarObsDaJanela(texto: string) {
  if (!pedidoItem.value?.lancamentos.length || !lancarRef.value) {
    obsPendente.value = texto
    if (texto) toasts.push({ kind: 'success', title: 'Observação guardada', lines: ['Entra no lançamento quando você lançar.'] }, 3000)
    return
  }
  const erro = await lancarRef.value.gravarLink('observacao', texto)
  if (erro) toasts.push({ kind: 'error', title: 'Observação não salva', lines: [erro] }, 8000)
  else toasts.push({ kind: 'success', title: 'Observação salva', lines: [] }, 2500)
}
function mudarSituacao(valor: string, seletor: HTMLSelectElement) {
  const p = pedidoItem.value
  if (!p?.pedido_bling || !valor || valor === p.situacao_bling_id) return
  const nome = situacoesBling.value.find((x) => String(x.id) === valor)?.nome || valor
  if (
    lancamentoEmAndamento() || travaResolvidoComEstoquePendente(valor)
    || !window.confirm(`Mudar o pedido ${p.pedido_bling} no Bling para “${nome}”?`)
  ) {
    seletor.value = p.situacao_bling_id || ''
    return
  }
  const pedido = p.pedido_bling
  void acao('situacao', 'O Bling não mudou', async () => {
    const res = await api<{ situacao_nome: string | null; via_desvio: boolean }>(
      `/api/devolutions/pedido/${encodeURIComponent(pedido)}/situacao-bling`,
      { method: 'POST', body: { situacao_id: Number(valor) } },
    )
    toasts.push({
      kind: 'success', title: `Pedido ${pedido} → ${res.situacao_nome || nome}`,
      lines: res.via_desvio ? ['O Bling só aceitou passando por Aguardando Devolução antes.'] : [],
    })
  }).finally(() => {
    seletor.value = pedidoItem.value?.situacao_bling_id || ''
    // As peças que dá pra lançar dependem da situação do Bling (e a lixeira
    // mostra "hoje: …"): o bloco do lançamento relê, sem perder o digitado.
    void lancarRef.value?.recarregar()
  })
}
// A busca de peças do lançamento só enxerga pedido nestas situações do Bling
// (a view vw_devolucoes): Aguardando Devolução, Extraviado, Problemas, 83966,
// Manutenção e Resolvido. Fora delas a janela avisa em vez de mostrar vazio.
const SITUACOES_QUE_LANCAM = new Set(['83957', '83960', '83961', '83966', '84677', '545902'])
// Pedido aberto pela busca que NUNCA entrou em devolução (não está na lista):
// a janela serve pra mudar o Bling e lançar; não há o que resolver ainda.
const foraDaLista = computed(() => {
  const p = pedidoItem.value
  return !!p && !p.resolvido && !p.aguardando_devolucao_data
    && !['83957', '84677'].includes(String(p.situacao_bling_id || ''))
})
// Aparece quando ainda falta peça por lançar: nenhuma lançada, ou menos
// lançamentos do que unidades no pedido (kit "a+b" conta cada peça). Caso
// típico: a 1ª peça foi Perdimento, o Bling foi pra Perdimento e as outras
// peças sumiram da busca.
const foraDeLancar = computed(() => {
  const p = pedidoItem.value
  if (!p || SITUACOES_QUE_LANCAM.has(String(p.situacao_bling_id || ''))) return false
  const unidades = linhasDaJanela.value.reduce((n, i) => n + Math.max(1, partesDoSku(i.sku).length) * Math.max(1, i.quantidade || 1), 0)
  const lancadas = (p.lancamentos || []).reduce((n, l) => n + Math.max(1, l.quantidade || 1), 0)
  return lancadas < unidades
})
function carregarRascunho() {
  const p = pedidoItem.value
  rascunho.observacao = original.observacao = p?.observacao || ''
  rascunho.rastreio = original.rastreio = p?.rastreio || ''
  rascunho.localizacao = original.localizacao = p?.localizacao || ''
  rascunho.desde = original.desde = p?.aguardando_devolucao_data || ''
}
// Fechar (X, Esc, clique fora) salva o que ficou digitado: tirar o campo da
// tela com Esc não dispara o "saiu do campo", e o texto se perderia.
// O lançamento é diferente: lá o salvar é explícito (mexe em estoque e em
// chamado), então fechar com cartão preenchido e não salvo pergunta antes.
const lancarRef = ref<{
  temMudancaNaoSalva: () => boolean
  ocupado: () => boolean
  recarregar: () => Promise<void> | void
  gravarLink: (campo: 'link_envio' | 'link_abertura' | 'observacao', valor: string) => Promise<string | null>
  anexarFotos: (files: File[]) => Promise<boolean>
  removerFoto: (anexoId: string) => Promise<void>
  linhasParaPrevia: () => { sku: string | null; produtos: string | null; quantidade: number; motivo_devolucao: string | null; observacao: string | null }[]
  prazoContestacao: () => string | null
} | null>(null)
// Lançamento em andamento (o Bling pode levar 10–15 s): a janela espera. Fechar
// no meio deixaria peça sendo criada sem ninguém ver o resultado.
function lancamentoEmAndamento(): boolean {
  if (!lancarRef.value?.ocupado()) return false
  toasts.push({ kind: 'warning', title: 'Espere o lançamento terminar', lines: ['Tem lançamento sendo gravado (o Bling pode demorar alguns segundos).'] })
  return true
}
function fecharJanela() {
  if (lancamentoEmAndamento()) return
  if (
    (lancarRef.value?.temMudancaNaoSalva() || provasEsperando())
    && !window.confirm('Tem lançamento preenchido que ainda não foi salvo (ou link/foto em Provas esperando o lançamento).\n\nFechar mesmo assim? O que não foi salvo se perde.')
  ) return
  salvarPendentes()
  janela.value = null
}
// Peça com a entrada de estoque pendurada (não entrou / conferindo / não
// confirmada): mandar o pedido pra Resolvido assim deixava a peça fora do
// estoque com o pedido dado como encerrado (a tela de antes recusava).
const SITUACAO_RESOLVIDO = '545902'
function travaResolvidoComEstoquePendente(alvo: string): boolean {
  if (alvo !== SITUACAO_RESOLVIDO || !linhasDaJanela.value.some(estoquePendente)) return false
  toasts.push({
    kind: 'warning', title: 'Estoque ainda não entrou',
    lines: ['Tem peça deste pedido com a entrada de estoque pendente. Resolva no cartão dela (“Tentar de novo” / “Já entrou”) antes de mandar o pedido pra Resolvido.'],
  }, 9000)
  return true
}
function salvarPendentes() {
  const p = pedidoItem.value
  if (!p?.pedido_bling || !canEdit.value) return
  const body: Record<string, string> = {}
  for (const campo of ['observacao', 'rastreio', 'localizacao'] as const) {
    if (rascunho[campo] === original[campo]) continue
    const novo = rascunho[campo].trim()
    if (novo !== original[campo].trim()) body[campo] = novo
  }
  if (!Object.keys(body).length) return
  const pedido = p.pedido_bling
  void acao('campos', 'Não salvou', () =>
    api(`/api/devolutions/acompanhamento/${encodeURIComponent(pedido)}`, { method: 'PATCH', body }),
  )
}
async function carregarSituacoes() {
  try {
    situacoesBling.value = (await api<{ items: { id: number; nome: string }[] }>('/api/devolutions/situacoes-bling')).items
  } catch { /* o Resolver continua valendo sem mexer no Bling */ }
}

// Uma ação por vez, em FILA: digitar a observação e clicar direto em
// "resolver" dispara o salvar do campo e, em seguida, o Resolver — o clique
// não se perde esperando o salvar terminar. `chave` repetida na fila é
// ignorada (clique duplo).
let fila: Promise<void> = Promise.resolve()
const naFila = new Set<string>()
function acao(chave: string, titulo: string, fn: () => Promise<unknown>, ok?: string): Promise<void> {
  if (naFila.has(chave)) return fila
  naFila.add(chave)
  fila = fila.then(async () => {
    salvando.value = chave
    try {
      await fn()
      await relerAvulsas(true)
      await load(true)
      if (janela.value && !linhasDaJanela.value.length) janela.value = null
      else if (!naFila.size || (naFila.size === 1 && naFila.has(chave))) carregarRascunho()
      if (ok) toasts.push({ kind: 'success', title: ok })
    } catch (e: any) {
      toasts.push({ kind: 'error', title: titulo, lines: [apiError(e)] })
    } finally {
      naFila.delete(chave)
      salvando.value = naFila.size ? salvando.value : ''
    }
  })
  return fila
}
function salvarCampo(campo: 'observacao' | 'rastreio' | 'localizacao' | 'desde') {
  const p = pedidoItem.value
  if (!p?.pedido_bling || !canEdit.value) return
  if (rascunho[campo] === original[campo]) return
  const novo = rascunho[campo].trim()
  if (novo === original[campo].trim()) return
  const body = campo === 'desde' ? { em_devolucao_desde: novo || null } : { [campo]: novo }
  void acao(campo, 'Não salvou', () =>
    api(`/api/devolutions/acompanhamento/${encodeURIComponent(p.pedido_bling!)}`, { method: 'PATCH', body }),
  )
}
function moverFila(destino: 'acompanhamento' | 'fraude' | 'regra') {
  const p = pedidoItem.value
  if (!p?.pedido_bling) return
  void acao('fila', 'Não moveu', () =>
    api(`/api/devolutions/acompanhamento/${encodeURIComponent(p.pedido_bling!)}`, { method: 'PATCH', body: { fila: destino } }),
    destino === 'fraude' ? 'Marcado como Fraude' : destino === 'regra' ? 'Solto pra regra da plataforma' : 'Tirado da Fraude',
  )
}
function solicitarVideo(motivo?: string) {
  const p = pedidoItem.value
  if (!p?.pedido_bling) return
  void acao('video', 'Erro ao solicitar o vídeo', async () => {
    await api(`/api/devolutions/acompanhamento/${encodeURIComponent(p.pedido_bling!)}/video/solicitar`, {
      method: 'POST', body: { motivo: motivo?.trim() || null },
    })
    videoApagando.value = false
    videoMotivo.value = ''
  }, motivo ? 'Link apagado: a equipe vai refazer' : 'Vídeo solicitado à equipe do SKU')
}
function cancelarVideo() {
  const p = pedidoItem.value
  if (!p?.pedido_bling) return
  void acao('video', 'Erro ao cancelar', () =>
    api(`/api/devolutions/acompanhamento/${encodeURIComponent(p.pedido_bling!)}/video`, { method: 'DELETE' }),
    'Solicitação de vídeo cancelada',
  )
}
function resolver() {
  const p = pedidoItem.value
  if (!p?.pedido_bling || !resolverForm.como) return
  if (lancamentoEmAndamento() || travaResolvidoComEstoquePendente(resolverForm.situacao)) return
  // O lançamento só grava no "salvar"/"lançar": resolver com cartão preenchido e
  // não salvo tiraria o pedido da lista sem o estoque entrar nem o chamado abrir.
  if (
    (lancarRef.value?.temMudancaNaoSalva() || provasEsperando())
    && !window.confirm('Tem lançamento preenchido que ainda NÃO foi salvo (ou link/foto em Provas esperando o lançamento): o estoque não entrou e o chamado não abriu.\n\nResolver mesmo assim? O que não foi salvo se perde.')
  ) return
  const pedido = p.pedido_bling
  void acao('resolver', 'Não resolveu', async () => {
    const res = await api<{ situacao_bling_nome: string | null; via_desvio: boolean }>(
      `/api/devolucoes-painel/${encodeURIComponent(pedido)}/resolver`,
      { method: 'POST', body: { como_terminou: resolverForm.como, situacao_id: resolverForm.situacao ? Number(resolverForm.situacao) : null } },
    )
    toasts.push({
      kind: 'success', title: `Pedido ${pedido} resolvido`,
      lines: [`Bling: ${res.situacao_bling_nome || '—'}${res.via_desvio ? ' (o Bling só aceitou passando por Aguardando Devolução)' : ''}`],
    })
    if (mostrar.value === 'abertas') janela.value = null
    void loadResumo()
  })
}
function reabrir() {
  const p = pedidoItem.value
  if (!p?.pedido_bling || lancamentoEmAndamento()) return
  if (
    lancarRef.value?.temMudancaNaoSalva()
    && !window.confirm('Tem lançamento preenchido que ainda não foi salvo.\n\nReabrir mesmo assim? O que não foi salvo pode se perder.')
  ) return
  void acao('resolver', 'Não reabriu', async () => {
    await api(`/api/devolucoes-painel/${encodeURIComponent(p.pedido_bling!)}/reabrir`, { method: 'POST' })
    void loadResumo()
  }, `Pedido ${p.pedido_bling} voltou pra lista de abertas`)
}
async function copiar(texto: string | null) {
  if (!texto) return
  try {
    await navigator.clipboard.writeText(texto)
    toasts.push({ kind: 'success', title: 'Copiado' }, 1500)
  } catch { /* sem permissão de área de transferência */ }
}
function onTecla(e: KeyboardEvent) {
  if (e.key === 'Escape' && janela.value) fecharJanela()
}

// ── Resumo por mês ──────────────────────────────────────────────────────
const resumo = ref<{ desde: string | null; dias: ResumoDia[]; ve_custo: boolean }>({ desde: null, dias: [], ve_custo: false })
const mesesAbertos = ref<Record<string, boolean>>({})
const MESES = ['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho', 'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro']
const SEMANA = ['dom', 'seg', 'ter', 'qua', 'qui', 'sex', 'sáb']
async function loadResumo() {
  try {
    resumo.value = await api('/api/devolucoes-painel/resumo')
    const atual = isoToday().slice(0, 7)
    if (!(atual in mesesAbertos.value)) mesesAbertos.value = { ...mesesAbertos.value, [atual]: true }
  } catch { /* o resumo é extra: a lista continua valendo */ }
}
const meses = computed(() => {
  const m = new Map<string, ResumoDia[]>()
  for (const d of resumo.value.dias) {
    const k = d.dia.slice(0, 7)
    if (!m.has(k)) m.set(k, [])
    m.get(k)!.push(d)
  }
  return [...m.entries()].map(([mes, dias]) => ({ mes, dias }))
})
function tituloMes(mes: string): string {
  return `${MESES[Number(mes.slice(5, 7)) - 1]}/${mes.slice(0, 4)}`
}
function soma(dias: ResumoDia[], f: (d: ResumoDia) => number | null): number {
  return dias.reduce((s, d) => s + (f(d) || 0), 0)
}
function semana(dia: string): string {
  return SEMANA[new Date(`${dia}T12:00:00`).getDay()] || ''
}

// ── Vida da página ──────────────────────────────────────────────────────
let relogio: ReturnType<typeof setInterval> | null = null
onMounted(() => {
  const q = useRoute().query.search
  const atalho = typeof q === 'string' ? q.trim() : ''
  if (atalho) search.value = atalho
  // Vindo de um atalho, o filtro guardado da última visita não entra: ele
  // podia esconder justamente o pedido procurado.
  if (!atalho) {
    try {
      const salvo = JSON.parse(localStorage.getItem(FILTROS_KEY) || '{}')
      if (salvo.p === 'outras' || PLATAFORMAS.some((p) => p.key === salvo.p)) plataformaTab.value = salvo.p
      if (['all', 'aguardando', 'chegou', 'manutencao', 'fraude'].includes(salvo.e)) etapaFilter.value = salvo.e
      if (typeof salvo.c === 'string') contaFilter.value = salvo.c
    } catch { /* filtro salvo inválido: começa limpo */ }
  }
  void load().then(() => {
    if (contaFilter.value !== 'all' && !contas.value.includes(contaFilter.value)) contaFilter.value = 'all'
    // Atalho de outra tela (Chamados → ?search=306120): abre a janela do pedido.
    // Se ele já saiu da lista, vem do histórico, em vez de uma lista vazia.
    if (!atalho) return
    const exato = items.value.some((i) => i.pedido_bling === atalho || i.pedido_marketplace === atalho)
    if (exato || !filtrados.value.length) void abrirPedido(atalho)
  })
  void loadResumo()
  window.addEventListener('keydown', onTecla)
  // Relê sozinho de 2 em 2 min, só com a janela fechada (não atropela quem digita).
  relogio = setInterval(() => {
    if (!janela.value && !loading.value && document.visibilityState === 'visible') void load(true)
  }, 120000)
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onTecla)
  if (relogio) clearInterval(relogio)
})
const lidoHa = computed(() => {
  if (!lidoEm.value) return ''
  return `lido às ${_HORA.format(lidoEm.value)}`
})
</script>

<template>
  <div class="min-w-0 space-y-2.5">
    <!-- Topo -->
    <div class="flex flex-wrap items-center gap-3 rounded-md border bg-card px-3 py-1.5">
      <h1 class="text-[15px] font-bold">Devoluções</h1>
      <div class="flex flex-wrap gap-0.5 text-xs font-semibold">
        <button
          v-for="t in PLATAFORMAS"
          :key="t.key"
          type="button"
          class="border-b-2 px-2.5 py-1"
          :class="plataformaTab === t.key ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'"
          @click="plataformaTab = t.key"
        >
          {{ t.label }} <span class="font-medium text-muted-foreground">{{ contagemDaAba(t.key) }}</span>
        </button>
        <button
          v-if="contagemDaAba('outras') || plataformaTab === 'outras'"
          type="button"
          class="border-b-2 px-2.5 py-1"
          :class="plataformaTab === 'outras' ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'"
          title="Plataformas fora das quatro (Temu, AliExpress, Shein, Magalu…)"
          @click="plataformaTab = 'outras'"
        >
          Outras <span class="font-medium text-muted-foreground">{{ contagemDaAba('outras') }}</span>
        </button>
      </div>
      <div class="ml-auto flex flex-wrap items-center justify-end gap-1.5 text-xs font-semibold">
        <span class="text-[11px] font-normal text-muted-foreground">{{ lidoHa }}</span>
        <button type="button" class="inline-flex items-center gap-1 rounded-md border px-2.5 py-1 hover:bg-muted disabled:opacity-50" :disabled="loading" title="Recarrega a lista (ela também se atualiza sozinha a cada 2 minutos)" @click="load(); loadResumo()">
          <RefreshCw class="size-3.5" :class="loading ? 'animate-spin' : ''" /> Atualizar
        </button>
        <button type="button" class="inline-flex items-center gap-1 rounded-md border px-2.5 py-1 hover:bg-muted disabled:opacity-50" :disabled="!filtrados.length" :title="`Baixa as ${filtrados.length} linhas que estão aparecendo (arquivo que o Excel abre)`" @click="exportar">
          <Download class="size-3.5" /> Excel
        </button>
        <button v-if="isAdmin" type="button" class="inline-flex items-center gap-1 rounded-md border px-2.5 py-1 hover:bg-muted" title="Enviar no Threema a lista de pedidos aguardando devolução e escolher quem recebe os avisos de prazo" @click="informarOpen = true">
          <Megaphone class="size-3.5" /> Informar
        </button>
        <a v-if="canEdit" href="/devolucoes-antigo?tab=lancamentos" target="_blank" rel="noopener" class="inline-flex items-center gap-1 rounded-md border px-2.5 py-1 font-normal text-muted-foreground hover:bg-muted" title="O que ainda mora na tela de antes: correção de estoque (entrada sem devolução), histórico completo dos lançamentos com os filtros de tag e a planilha deles">ferramentas antigas</a>
        <button v-if="canEdit" type="button" class="inline-flex items-center gap-1 whitespace-nowrap rounded-md bg-primary px-2.5 py-1 text-primary-foreground" title="Lançar a devolução de um pedido que não está na lista (digite o número do Bling ou o da plataforma)" @click="novaDevolucao">
          <Plus class="size-3.5" /> nova devolução
        </button>
      </div>
    </div>

    <!-- Filtros -->
    <div class="flex flex-wrap items-center gap-1.5 text-xs">
      <label class="inline-flex h-7 items-center gap-1 rounded-md border bg-card px-2">
        <span class="font-semibold text-muted-foreground">Conta</span>
        <select v-model="contaFilter" class="max-w-[150px] bg-transparent outline-none">
          <option value="all">Todas</option>
          <option v-for="c in contas" :key="c" :value="c">{{ c }}</option>
        </select>
      </label>
      <label class="inline-flex h-7 items-center gap-1 rounded-md border bg-card px-2" title="Abertas: o que ainda não foi resolvido. Resolvidas: o que saiu no botão Resolver nos últimos 60 dias.">
        <span class="font-semibold text-muted-foreground">Mostrar</span>
        <select v-model="mostrar" class="bg-transparent outline-none">
          <option value="abertas">Abertas</option>
          <option value="resolvidas">Resolvidas</option>
          <option value="todas">Todas</option>
        </select>
      </label>
      <label class="inline-flex h-7 items-center gap-1 rounded-md border bg-card px-2" title="Fica guardado neste navegador: quem cuida da Fraude abre sempre na Fraude">
        <span class="font-semibold text-muted-foreground">Etapa</span>
        <select v-model="etapaFilter" class="bg-transparent outline-none">
          <option value="all">Todas</option>
          <option value="aguardando">Aguardando</option>
          <option value="chegou">Chegou</option>
          <option value="manutencao">Manutenção</option>
          <option value="fraude">Fraude</option>
        </select>
      </label>
      <label class="inline-flex h-7 items-center gap-1 rounded-md border bg-card px-2" title="Período da data do pedido">
        <span class="font-semibold text-muted-foreground">Período</span>
        <input v-model="dataIni" type="date" :max="dataFim || undefined" class="bg-transparent outline-none" />
        <span class="text-muted-foreground">até</span>
        <input v-model="dataFim" type="date" :min="dataIni || undefined" class="bg-transparent outline-none" />
      </label>
      <label class="inline-flex h-7 min-w-0 items-center gap-1 rounded-md border bg-card px-2">
        <Search class="size-3.5 text-muted-foreground" />
        <input v-model="search" class="w-72 bg-transparent outline-none" placeholder="buscar pedido, produto, SKU, cliente, rastreio, obs" />
      </label>
      <button v-if="filtrosAtivos" type="button" class="text-muted-foreground underline" @click="limparFiltros">limpar</button>
      <span class="text-muted-foreground">{{ pedidos(filtrados) }} pedidos · {{ filtrados.length }} de {{ naAba.length }} produtos</span>
    </div>

    <!-- Cartões -->
    <div class="grid grid-cols-2 gap-2 lg:grid-cols-6">
      <button
        v-for="k in cartoes"
        :key="k.label"
        type="button"
        class="rounded-md border bg-card px-3 py-1.5 text-center hover:bg-muted/40 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
        :class="cartaoLigado(k.k) ? 'ring-2 ring-primary' : ''"
        @click="clicarCartao(k.k)"
      >
        <span class="block text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">{{ k.label }}</span>
        <span class="block text-xl font-bold leading-tight" :class="k.cor">{{ k.n }}</span>
        <span class="block text-[10px] leading-tight text-muted-foreground">{{ k.sub }}</span>
      </button>
    </div>

    <!-- Status Bling -->
    <div v-if="statusPills.length" class="flex flex-wrap items-center gap-3 rounded-md border bg-card px-3 py-1.5 text-xs">
      <span class="text-[13px]">Status Bling dos pedidos</span>
      <button
        v-for="sp in statusPills"
        :key="sp.nome"
        type="button"
        class="inline-flex items-center gap-1.5 rounded bg-muted px-2 py-0.5 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
        :class="recorte === `bling:${sp.nome}` ? 'ring-2 ring-primary' : ''"
        @click="alternarRecorte(`bling:${sp.nome}`)"
      >
        <span class="size-[7px] rounded-full" :class="sp.dot" />
        {{ sp.n }} {{ sp.nome }}
      </button>
      <span class="ml-auto hidden text-[11px] text-muted-foreground lg:inline">clique numa etiqueta ou num cartão para filtrar</span>
    </div>

    <div v-if="error" class="text-sm text-red-500">erro: {{ error }}</div>

    <!-- "Onde agir hoje" do lado da lista a partir de xl, igual Logística e Chamados
         (Vinicius 16/11: "ficou em cima e não do lado igual é padrão dos outros
         painéis"); a lista rola de lado dentro dela. Em tela estreita vai pra cima. -->
    <div class="flex flex-col gap-3 xl:grid xl:grid-cols-[minmax(0,1fr)_340px] xl:items-start">
      <div class="flex min-w-0 flex-col gap-2.5">
        <!-- Tabela -->
        <div class="flex min-w-0 flex-col rounded-md border bg-card">
          <div class="relative flex items-center justify-center rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-xs font-semibold text-white">
            <span>Devoluções — uma linha por produto</span>
            <span class="absolute right-3 hidden text-[11px] font-normal text-slate-300 2xl:block">entra sozinho quando cai em Aguardando Devolução × sai só no Resolver</span>
          </div>
          <!-- 16/11/2026 (Vinicius, "igual no painel chamado"): a lista tem altura
               própria e rola aqui dentro — o resumo dos meses fica à vista logo abaixo. -->
          <div class="max-h-[560px] overflow-auto focus:outline-none" tabindex="0" data-lista-rolagem>
            <!-- Larguras fixas em tudo, menos na Localização: "o que sobrar deixa a
                 localização" (Vinicius, mesma regra da Logística). Com o menu lateral
                 aberto em tela de 1920 sobra uma rolagem curta pro lado; com ele
                 recolhido cabe tudo. O mínimo = soma das larguras fixas + ~120 px pra
                 Localização (16/11: a coluna Cliente entrou com 130 px e a Localização,
                 sem esse acréscimo, ficou com zero e os títulos encavalaram). -->
            <table class="w-full table-fixed border-collapse text-[11.5px]" :class="veCusto ? 'min-w-[1872px]' : 'min-w-[1788px]'">
              <colgroup>
                <col class="w-[42px]"><col class="w-[58px]"><col class="w-[148px]"><col class="w-[88px]"><col class="w-[130px]"><col class="w-[112px]"><col class="w-[168px]">
                <col class="w-[90px]"><col class="w-[36px]"><col class="w-[90px]"><col class="w-[70px]"><col>
                <col class="w-[40px]"><col class="w-[60px]"><col class="w-[36px]">
                <col class="w-[150px]"><col class="w-[96px]"><col class="w-[92px]"><col v-if="veCusto" class="w-[84px]"><col class="w-[160px]">
              </colgroup>
              <thead class="sticky top-0 z-10 bg-muted text-[10px] uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th class="border-b px-1.5 py-1 text-center font-semibold" colspan="7">Pedido</th>
                  <th class="border-b border-l border-l-slate-300 px-1.5 py-1 text-center font-semibold dark:border-l-slate-600" colspan="5">Devolução</th>
                  <th class="border-b border-l border-l-slate-300 px-1.5 py-1 text-center font-semibold dark:border-l-slate-600" colspan="3">Provas</th>
                  <th class="border-b border-l border-l-slate-300 px-1.5 py-1 text-center font-semibold dark:border-l-slate-600" :colspan="veCusto ? 5 : 4">Lançamento</th>
                </tr>
                <tr>
                  <th class="border-b px-1.5 py-1 text-center font-semibold" title="Data do pedido">Data</th>
                  <th class="border-b px-1.5 py-1 text-center font-semibold" title="Número do pedido no Bling">Bling</th>
                  <th class="border-b px-1.5 py-1 text-center font-semibold" title="Número do pedido na plataforma (um clique seleciona para copiar)">Nº na plataforma</th>
                  <th class="border-b px-1.5 py-1 text-center font-semibold">Conta</th>
                  <th class="border-b px-1.5 py-1 text-left font-semibold" title="Nome do cliente (cidade/UF no passar do mouse)">Cliente</th>
                  <th class="border-b px-1.5 py-1 text-left font-semibold" title="SKU da venda">SKU</th>
                  <th class="border-b px-1.5 py-1 text-left font-semibold">Produto</th>
                  <th class="border-b border-l border-l-slate-300 px-1.5 py-1 text-center font-semibold dark:border-l-slate-600" title="Aguardando = o pacote não chegou · Chegou = chegou (falta lançar ou já lançada) · Manutenção = com o técnico · Fraude = não volta pacote, só dinheiro">Etapa</th>
                  <th class="border-b px-1.5 py-1 text-center font-semibold" title="Há quantos dias o pedido está em devolução (desde que caiu em Aguardando Devolução). Não recomeça quando a etapa muda.">Dias</th>
                  <th class="border-b px-1.5 py-1 text-center font-semibold" title="O prazo que está correndo nesta etapa, o mais próximo de vencer. A palavra de baixo diz do que é: disputa, responder, contestar, pacote chegar (45 dias) ou técnico (30 dias).">Prazo</th>
                  <th class="border-b px-1.5 py-1 text-center font-semibold leading-tight" title="O dinheiro já voltou pro cliente? Sim = a plataforma devolveu e desconta da loja. Não = ainda está com a gente.">Reemb. cliente</th>
                  <th class="border-b px-1.5 py-1 text-center font-semibold">Localização</th>
                  <th class="border-b border-l border-l-slate-300 px-1.5 py-1 text-center font-semibold dark:border-l-slate-600" title="Vídeo da expedição do pedido">Envio</th>
                  <th class="border-b px-1.5 py-1 text-center font-semibold" title="Vídeo da abertura do pacote que voltou">Abertura</th>
                  <th class="border-b px-1.5 py-1 text-center font-semibold" title="Fotos da devolução">Foto</th>
                  <th class="border-b border-l border-l-slate-300 px-1.5 py-1 text-center font-semibold dark:border-l-slate-600" title="Uma linha por peça lançada: como ficou (Novo, Usado, Perdimento, no técnico) e o SKU em que entrou no estoque">Condição</th>
                  <th class="border-b px-1.5 py-1 text-center font-semibold" title="Chamado mais recente do pedido. O botãozinho abre na aba Chamados.">Chamado</th>
                  <th class="border-b px-1.5 py-1 text-center font-semibold" title="Situação do pedido no Bling">Status Bling</th>
                  <th v-if="veCusto" class="border-b px-1.5 py-1 text-center font-semibold" title="A conta da devolução, por pedido: o que o chamado ganhou menos o custo (produto perdido ou conserto)">Reembolso</th>
                  <th class="border-b px-1.5 py-1 text-left font-semibold" title="Observação do pedido">Obs.</th>
                </tr>
              </thead>
              <tbody>
                <tr v-if="loading && !items.length">
                  <td :colspan="veCusto ? 20 : 19" class="py-8 text-center text-muted-foreground"><RefreshCw class="mr-1.5 inline size-4 animate-spin" /> carregando…</td>
                </tr>
                <tr
                  v-for="(i, n) in filtrados"
                  :key="`${i.pedido_bling}|${i.sku}|${n}`"
                  class="cursor-pointer border-b align-middle hover:bg-muted/40"
                  :class="[
                    janela && janela.pedido === i.pedido_bling ? 'bg-blue-50 shadow-[inset_3px_0_0_hsl(var(--primary))] dark:bg-blue-950/30' : '',
                    !(janela && janela.pedido === i.pedido_bling) && !i.resolvido && urgente(i) ? 'bg-red-50/70 shadow-[inset_3px_0_0_#dc2626] dark:bg-red-950/30' : '',
                    i.resolvido ? 'opacity-70' : '',
                  ]"
                  @click="abrirJanela(i)"
                >
                  <td class="px-1.5 py-1.5 text-center text-muted-foreground" :title="ddmmaaaa(i.data)">{{ ddmm(i.data) }}</td>
                  <td class="px-1.5 py-1.5 text-center font-mono font-bold" :class="primeiraVisivel(n) ? '' : 'text-muted-foreground'">{{ i.pedido_bling || '—' }}</td>
                  <!-- 16/11: sem cortar ("o número do pedido na plataforma tá cortando") — cabe
                       numa linha até 19 caracteres (TikTok, Amazon); mais que isso quebra. -->
                  <td class="break-all px-1.5 py-1.5 text-center font-mono text-[10.5px] leading-tight" :title="i.pedido_marketplace || undefined">
                    <span v-if="i.pedido_marketplace" class="cursor-text select-all" @click.stop>{{ i.pedido_marketplace }}</span><span v-else class="text-muted-foreground">—</span>
                  </td>
                  <td class="px-1.5 py-1.5 text-center leading-tight" :title="i.loja || ''">
                    <div class="line-clamp-2 break-words">{{ contaDe(i) }}</div>
                    <div v-if="plataformaTab === 'todas'" class="whitespace-nowrap text-[10px] text-muted-foreground">{{ PLATAFORMA_NOME[plataformaKey(i.plataforma)] || i.plataforma }}</div>
                  </td>
                  <!-- 16/11/2026 (Vinicius): "depois da conta eu preciso do nome do cliente, isso é muito importante". -->
                  <td class="px-1.5 py-1.5 leading-tight" :title="[i.cliente, [i.cidade, i.uf].filter(Boolean).join('/')].filter(Boolean).join(' · ')" data-cliente>
                    <div class="line-clamp-2 break-words">{{ i.cliente || '—' }}</div>
                  </td>
                  <!-- SKU de kit ("a+b+c") quebra depois de cada "+" em vez de cortar. -->
                  <td class="px-1.5 py-1.5 font-mono text-[10.5px] leading-tight [overflow-wrap:anywhere]" :title="i.sku || ''">{{ (i.sku || '—').replace(/\+/g, '+\u200b') }}</td>
                  <td class="px-1.5 py-1.5" :title="i.produto || ''">
                    <div class="line-clamp-3 break-words leading-tight">{{ i.produto || '—' }}<b v-if="i.quantidade && i.quantidade > 1"> ×{{ i.quantidade }}</b></div>
                  </td>
                  <td class="border-l border-l-slate-300 px-1.5 py-1.5 text-center leading-tight dark:border-l-slate-600">
                    <span class="inline-block w-[84px] rounded py-0.5 text-[11px] font-semibold" :class="etapaCor(i)" data-etapa>{{ etapaRotulo(i) }}</span>
                    <div v-if="!i.resolvido && estoquePendente(i)" class="text-[10px] font-semibold text-red-700 dark:text-red-400">estoque a conferir</div>
                    <div v-else-if="!i.resolvido && aFinalizar(i)" class="text-[10px] text-blue-700 dark:text-blue-400">a finalizar</div>
                    <div v-else-if="!i.resolvido && i.etapa === 'chegou' && !i.falta_lancar" class="text-[10px] text-muted-foreground">lançada</div>
                  </td>
                  <td class="px-1.5 py-1.5 text-center tabular-nums" :title="i.aguardando_devolucao_data ? `em devolução desde ${ddmmaaaa(i.aguardando_devolucao_data)}${i.aguardando_devolucao_data_estimada ? ' (data estimada)' : ''}` : ''">{{ i.dias_em_devolucao ?? '—' }}</td>
                  <td class="whitespace-nowrap px-1.5 py-1.5 text-center leading-tight" :title="i.prazo_dica || i.prazo_texto || i.amazon_cancelar_texto || ''">
                    <template v-if="i.prazo_tipo">
                      <div :class="prazoCor(i)">{{ prazoTexto(i) }}</div>
                      <div class="text-[10px] text-muted-foreground">{{ i.prazo_tipo }}</div>
                    </template>
                    <span v-else class="text-muted-foreground">—</span>
                  </td>
                  <td class="whitespace-nowrap px-1.5 py-1.5 text-center leading-tight" :title="i.reembolso_detalhe || ''">
                    <template v-if="i.reembolso != null">
                      <div>{{ i.reembolso ? 'Sim' : 'Não' }}</div>
                      <div v-if="i.reembolso && i.reembolso_valor != null" class="text-[10px] text-muted-foreground">{{ brl(i.reembolso_valor) }}</div>
                    </template>
                    <span v-else class="text-muted-foreground">—</span>
                  </td>
                  <td class="px-1.5 py-1.5 text-center leading-tight" :title="[i.localizacao, i.entrega_localizacao].filter(Boolean).join(' · ')">
                    <div v-if="i.etapa === 'fraude' && !i.localizacao" class="text-muted-foreground">não volta pacote</div>
                    <div v-else class="line-clamp-2 break-words">{{ i.localizacao || '—' }}</div>
                    <div v-if="i.etapa === 'fraude' && i.ida_situacao && i.ida_situacao !== 'entregue'" class="text-[10px] font-semibold text-amber-700 dark:text-amber-400" data-ida-nao-entregue :title="i.entrega_localizacao || ''">{{ i.ida_situacao === 'falhou' ? 'a entrega da ida falhou' : 'a ida ainda não chegou no cliente' }}</div>
                    <div v-if="i.devolucao_chegou_em" class="text-[10px] text-muted-foreground">chegou {{ ddmm(i.devolucao_chegou_em) }}</div>
                    <div v-else-if="i.localizacao_data" class="text-[10px] text-muted-foreground">últ. mov. {{ ddmm(i.localizacao_data) }}</div>
                  </td>
                  <td class="border-l border-l-slate-300 px-1.5 py-1.5 text-center dark:border-l-slate-600" :title="i.video_status === 'pendente' ? `pedido à equipe em ${ddmm(i.video_solicitado_em)}` : i.video_sem_motivo ? `sem vídeo: ${i.video_sem_motivo}` : ''">
                    <Check v-if="i.video_link" class="inline size-3.5 text-emerald-700 dark:text-emerald-400" />
                    <span v-else-if="i.video_status === 'pendente'" class="text-[10.5px] font-semibold text-amber-700 dark:text-amber-400">pedido</span>
                    <span v-else-if="i.video_status === 'sem_video'" class="text-[10.5px] text-red-600 dark:text-red-400">sem</span>
                    <span v-else class="text-muted-foreground">—</span>
                  </td>
                  <td class="px-1.5 py-1.5 text-center">
                    <Check v-if="temAbertura(i)" class="inline size-3.5 text-emerald-700 dark:text-emerald-400" /><span v-else class="text-muted-foreground">—</span>
                  </td>
                  <td class="px-1.5 py-1.5 text-center">
                    <Check v-if="temFoto(i)" class="inline size-3.5 text-emerald-700 dark:text-emerald-400" /><span v-else class="text-muted-foreground">—</span>
                  </td>
                  <td class="border-l border-l-slate-300 px-1.5 py-1.5 leading-snug dark:border-l-slate-600">
                    <template v-if="lancamentosDaLinha(i, n).length">
                      <div v-for="l in lancamentosDaLinha(i, n).slice(0, 3)" :key="l.id" class="flex items-center gap-1.5 py-[1px]" :title="tituloLanc(l)">
                        <span class="inline-block min-w-[72px] shrink-0 whitespace-nowrap rounded px-1 py-[1px] text-center text-[10.5px] font-semibold" :class="etiquetaLanc(l).cor">{{ etiquetaLanc(l).texto }}</span>
                        <span class="truncate font-mono text-[10.5px]" :class="l.sku_lancado ? '' : 'text-muted-foreground'">{{ l.sku_lancado || l.sku }}</span>
                      </div>
                      <div v-if="lancamentosDaLinha(i, n).length > 3" class="text-[10px] text-muted-foreground">+{{ lancamentosDaLinha(i, n).length - 3 }} peça(s)</div>
                    </template>
                    <div v-else class="text-center text-muted-foreground">—</div>
                    <div v-if="primeiraVisivel(n) && estoquePendente(i)" class="text-[10px] font-bold text-red-600 dark:text-red-400">estoque não entrou</div>
                  </td>
                  <td class="px-1.5 py-1.5 text-center">
                    <span v-if="i.chamado" class="inline-flex items-center gap-1">
                      <span v-if="i.chamado.abertura_status === 'segurada'" class="inline-block rounded bg-amber-100 px-2 py-0.5 text-[11px] font-semibold text-amber-800 dark:bg-amber-950/50 dark:text-amber-300" title="A IA segurou a contestação: o motivo não bate com o que foi lançado. Decidir na janela.">Segurado</span>
                      <span v-else class="inline-block rounded px-2 py-0.5 text-[11px] font-semibold" :class="i.tem_chamado_aberto ? 'bg-blue-100 text-blue-800 dark:bg-blue-950/50 dark:text-blue-300' : 'bg-muted text-muted-foreground'">{{ i.tem_chamado_aberto ? 'Aberto' : 'Concluído' }}</span>
                      <a :href="`/chamados?search=${encodeURIComponent(i.pedido_bling || '')}`" target="_blank" rel="noopener" class="inline-flex size-5 items-center justify-center rounded border bg-background text-muted-foreground hover:text-foreground" title="Abrir o chamado na aba Chamados" @click.stop><ExternalLink class="size-3" /></a>
                    </span>
                    <span v-else class="text-muted-foreground">—</span>
                  </td>
                  <td class="px-1.5 py-1.5 text-center text-[11px] leading-tight" :title="i.situacao_bling_nome || ''">
                    <template v-if="i.situacao_bling_nome">
                      <div>{{ duasLinhas(i.situacao_bling_nome)[0] }}</div>
                      <div v-if="duasLinhas(i.situacao_bling_nome)[1]" class="text-[10px] text-muted-foreground">{{ duasLinhas(i.situacao_bling_nome)[1] }}</div>
                    </template>
                    <span v-else class="text-muted-foreground">—</span>
                  </td>
                  <td v-if="veCusto" class="overflow-hidden whitespace-nowrap px-1.5 py-1.5 text-center leading-tight" :title="contaDaLinha(i, n) ? `recebido ${brl(contaDaLinha(i, n)!.recebido)} − custo ${brl(contaDaLinha(i, n)!.custo_produto + contaDaLinha(i, n)!.custo_manutencao)}` : ''">
                    <template v-if="contaDaLinha(i, n)">
                      <div class="font-semibold" :class="contaDaLinha(i, n)!.resultado < 0 ? 'text-red-700 dark:text-red-400' : 'text-emerald-700 dark:text-emerald-400'">{{ sinal(contaDaLinha(i, n)!.resultado) }}</div>
                      <div class="text-[10px] text-muted-foreground">{{ contaDaLinha(i, n)!.recebido ? 'líquido' : 'só custo' }}</div>
                    </template>
                    <span v-else class="text-muted-foreground">—</span>
                  </td>
                  <td class="px-1.5 py-1.5 text-muted-foreground" :title="obsDaLinha(i)"><div class="line-clamp-2 break-words leading-tight">{{ obsDaLinha(i) }}</div></td>
                </tr>
                <tr v-if="!loading && !filtrados.length">
                  <td :colspan="veCusto ? 20 : 19" class="px-3 py-6 text-center text-muted-foreground">
                    {{ items.length ? 'nenhuma devolução com esses filtros' : 'nenhuma devolução aqui' }}
                    <template v-if="search.trim()">
                      · <button type="button" class="font-semibold text-primary underline disabled:opacity-50" :disabled="procurando" title="Procura o pedido no histórico inteiro: resolvidas, antigas e pedidos que ainda não caíram em Aguardando Devolução" @click="abrirPedido(search)">{{ procurando ? 'procurando…' : `procurar “${search.trim()}” no histórico` }}</button>
                    </template>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div class="border-t px-3 py-1.5 text-center text-[11px] text-muted-foreground">
            Clique na linha abre a janela da devolução. Reemb. cliente = o dinheiro já voltou pro cliente<template v-if="veCusto"> · Reembolso = a conta do pedido: o que o chamado ganhou menos o custo</template>. Vermelho = prazo da plataforma em menos de 24 h.
          </div>
        </div>

        <!-- Meses -->
        <div v-if="resumo.desde" class="flex min-w-0 flex-col rounded-md border bg-card">
          <div class="flex items-center justify-between rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-xs font-semibold text-white">
            <span>Meses — como ficou cada mês</span>
            <span class="text-[11px] font-normal text-slate-300">mostrar abre os dias · conta a partir de {{ ddmmaaaa(resumo.desde) }}</span>
          </div>
          <div class="overflow-x-auto">
            <table class="w-full min-w-[900px] border-collapse text-xs">
              <thead class="bg-muted text-[10px] uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th class="border-b px-2.5 py-1" />
                  <th class="border-b border-l border-l-slate-300 px-2.5 py-1 text-center font-semibold dark:border-l-slate-600" colspan="3">Devoluções</th>
                  <th class="border-b border-l border-l-slate-300 px-2.5 py-1 text-center font-semibold dark:border-l-slate-600" colspan="4">Lançado (unidades)</th>
                  <th v-if="resumo.ve_custo" class="border-b border-l border-l-slate-300 px-2.5 py-1 text-center font-semibold dark:border-l-slate-600" colspan="3">Reembolso (R$)</th>
                </tr>
                <tr>
                  <th class="border-b px-2.5 py-1 text-left font-semibold">Mês / dia</th>
                  <th class="border-b border-l border-l-slate-300 px-2.5 py-1 text-right font-semibold dark:border-l-slate-600" title="Como o mês ou o dia terminou">Abertas</th>
                  <th class="border-b px-2.5 py-1 text-right font-semibold" title="Caíram em Aguardando Devolução">Novas</th>
                  <th class="border-b px-2.5 py-1 text-right font-semibold" title="Saíram no botão Resolver">Resolvidas</th>
                  <th class="border-b border-l border-l-slate-300 px-2.5 py-1 text-right font-semibold dark:border-l-slate-600">Estoque novo</th>
                  <th class="border-b px-2.5 py-1 text-right font-semibold">Estoque usado</th>
                  <th class="border-b px-2.5 py-1 text-right font-semibold">Perdimento</th>
                  <th class="border-b px-2.5 py-1 text-right font-semibold" title="Foram pro técnico">Manutenção</th>
                  <template v-if="resumo.ve_custo">
                    <th class="border-b border-l border-l-slate-300 px-2.5 py-1 text-right font-semibold dark:border-l-slate-600" title="O que os chamados concluídos ganharam">Recebido</th>
                    <th class="border-b px-2.5 py-1 text-right font-semibold" title="Produto perdido + conserto">Custos</th>
                    <th class="border-b px-2.5 py-1 text-right font-semibold" title="Recebido menos custos">Resultado</th>
                  </template>
                </tr>
              </thead>
              <tbody>
                <template v-for="m in meses" :key="m.mes">
                  <tr class="h-[30px] border-b bg-muted/60 font-bold tabular-nums">
                    <td class="whitespace-nowrap px-2.5">{{ tituloMes(m.mes) }} <button type="button" class="ml-1 font-semibold text-primary underline" @click="mesesAbertos = { ...mesesAbertos, [m.mes]: !mesesAbertos[m.mes] }">{{ mesesAbertos[m.mes] ? 'esconder' : 'mostrar' }}</button></td>
                    <td class="border-l border-l-slate-300 px-2.5 text-right dark:border-l-slate-600">{{ m.dias[0]?.abertas }}</td>
                    <td class="px-2.5 text-right text-blue-700 dark:text-blue-300">+{{ soma(m.dias, (d) => d.novas) }}</td>
                    <td class="px-2.5 text-right text-emerald-700 dark:text-emerald-300">−{{ soma(m.dias, (d) => d.resolvidas) }}</td>
                    <td class="border-l border-l-slate-300 px-2.5 text-right dark:border-l-slate-600">{{ soma(m.dias, (d) => d.estoque_novo) }}</td>
                    <td class="px-2.5 text-right">{{ soma(m.dias, (d) => d.estoque_usado) }}</td>
                    <td class="px-2.5 text-right text-red-700 dark:text-red-300">{{ soma(m.dias, (d) => d.perdimento) }}</td>
                    <td class="px-2.5 text-right text-indigo-700 dark:text-indigo-300">{{ soma(m.dias, (d) => d.manutencao) }}</td>
                    <template v-if="resumo.ve_custo">
                      <td class="border-l border-l-slate-300 px-2.5 text-right text-emerald-700 dark:border-l-slate-600 dark:text-emerald-300">{{ valor(soma(m.dias, (d) => d.recebido)) }}</td>
                      <td class="px-2.5 text-right text-red-700 dark:text-red-300">{{ valor(soma(m.dias, (d) => d.custos)) }}</td>
                      <td class="px-2.5 text-right" :class="soma(m.dias, (d) => d.resultado) < 0 ? 'text-red-700 dark:text-red-300' : 'text-emerald-700 dark:text-emerald-300'">{{ valor(soma(m.dias, (d) => d.resultado)) }}</td>
                    </template>
                  </tr>
                  <tr
                    v-for="d in m.dias"
                    v-show="mesesAbertos[m.mes]"
                    :key="d.dia"
                    class="h-[30px] border-b tabular-nums"
                    :class="d.dia === isoToday() ? 'bg-amber-50 dark:bg-amber-950/20' : ''"
                  >
                    <td class="whitespace-nowrap px-2.5 pl-5 font-bold">{{ ddmm(d.dia) }} <span class="font-normal text-muted-foreground">{{ semana(d.dia) }}<template v-if="d.dia === isoToday()"> (hoje)</template></span></td>
                    <td class="border-l border-l-slate-300 px-2.5 text-right font-bold dark:border-l-slate-600">{{ d.abertas }}</td>
                    <td class="px-2.5 text-right" :class="d.novas ? 'font-semibold text-blue-700 dark:text-blue-300' : 'text-muted-foreground'">{{ d.novas ? `+${d.novas}` : 0 }}</td>
                    <td class="px-2.5 text-right" :class="d.resolvidas ? 'font-semibold text-emerald-700 dark:text-emerald-300' : 'text-muted-foreground'">{{ d.resolvidas ? `−${d.resolvidas}` : 0 }}</td>
                    <td class="border-l border-l-slate-300 px-2.5 text-right dark:border-l-slate-600" :class="d.estoque_novo ? '' : 'text-muted-foreground'">{{ d.estoque_novo }}</td>
                    <td class="px-2.5 text-right" :class="d.estoque_usado ? '' : 'text-muted-foreground'">{{ d.estoque_usado }}</td>
                    <td class="px-2.5 text-right" :class="d.perdimento ? 'text-red-700 dark:text-red-300' : 'text-muted-foreground'">{{ d.perdimento }}</td>
                    <td class="px-2.5 text-right" :class="d.manutencao ? 'text-indigo-700 dark:text-indigo-300' : 'text-muted-foreground'">{{ d.manutencao }}</td>
                    <template v-if="resumo.ve_custo">
                      <td class="border-l border-l-slate-300 px-2.5 text-right dark:border-l-slate-600" :class="d.recebido ? 'text-emerald-700 dark:text-emerald-300' : 'text-muted-foreground'">{{ valor(d.recebido) }}</td>
                      <td class="px-2.5 text-right" :class="d.custos ? 'text-red-700 dark:text-red-300' : 'text-muted-foreground'">{{ valor(d.custos) }}</td>
                      <td class="px-2.5 text-right font-bold" :class="(d.resultado || 0) < 0 ? 'text-red-700 dark:text-red-300' : d.resultado ? 'text-emerald-700 dark:text-emerald-300' : 'text-muted-foreground'">{{ valor(d.resultado) }}</td>
                    </template>
                  </tr>
                </template>
              </tbody>
            </table>
          </div>
          <div class="border-t px-3 py-1.5 text-[11px] text-muted-foreground">
            Abertas = como o mês ou o dia terminou. Novas = caíram em Aguardando Devolução. Resolvidas = saíram no Resolver. Lançado = unidades lançadas no dia, pela condição.<template v-if="resumo.ve_custo"> Recebido = o que os chamados ganharam. Custos = produto perdido e conserto. Resultado = recebido menos custos.</template>
          </div>
        </div>
      </div>

      <!-- Onde agir hoje -->
      <div class="order-first flex min-w-0 flex-col rounded-md border bg-card xl:order-none">
        <div class="rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-center text-xs font-semibold text-white">Onde agir hoje</div>
        <div class="grid gap-1.5 p-2 sm:grid-cols-2 xl:grid-cols-1">
          <div
            v-for="a in agirItens"
            :key="a.chave"
            class="grid grid-cols-[12px_minmax(0,1fr)_58px] items-center gap-2 rounded-md border px-2.5 py-1.5 text-xs"
            :class="a.grave ? 'border-red-300 bg-red-50 dark:border-red-800 dark:bg-red-950/40' : ''"
          >
            <span class="size-[7px] rounded-full" :class="a.dot" />
            <div class="min-w-0">
              <div class="truncate font-bold" :class="a.grave ? 'text-red-700 dark:text-red-300' : ''" :title="a.titulo">{{ a.titulo }} · {{ a.n }}</div>
              <div class="truncate text-[11px] text-muted-foreground" :title="a.detalhe">{{ a.detalhe }}</div>
            </div>
            <button type="button" class="h-6 rounded-md border bg-background text-[11px] font-semibold hover:bg-muted" :class="recorte === `agir:${a.chave}` ? 'ring-2 ring-primary' : ''" @click="alternarRecorte(`agir:${a.chave}`)">Ver</button>
          </div>
          <div v-if="!agirItens.length" class="rounded-md border px-2.5 py-3 text-center text-xs text-muted-foreground sm:col-span-2 xl:col-span-1">nada pra agir agora</div>
        </div>
      </div>
    </div>

    <!-- Janela grande -->
    <div v-if="janela && foco && pedidoItem" class="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/50 p-4" @mousedown.self="fecharJanela">
      <div class="w-full max-w-[1760px] rounded-md border bg-card shadow-2xl">
        <div class="relative flex items-center justify-center rounded-t-md bg-[#1f3a5f] px-3 py-2 text-[13px] font-semibold text-white">
          <span>Devolução · {{ pedidoItem.pedido_bling }} · {{ foco.produto || '' }}</span>
          <button type="button" class="absolute right-2 inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-normal text-slate-300 hover:text-white" @click="fecharJanela">fechar <X class="size-3.5" /></button>
        </div>
        <div class="flex flex-wrap items-center gap-2 border-b px-4 py-2 text-xs">
          <span v-if="foraDaLista" class="inline-block rounded bg-muted px-2 py-0.5 text-center text-[11px] font-semibold text-muted-foreground" title="Este pedido ainda não caiu em Aguardando Devolução no Bling">fora da lista</span>
          <span v-else class="inline-block w-[84px] rounded py-0.5 text-center text-[11px] font-semibold" :class="etapaCor(pedidoItem)">{{ etapaRotulo(pedidoItem) }}</span>
          <span class="font-semibold">{{ contaDe(pedidoItem) }} · {{ PLATAFORMA_NOME[plataformaKey(pedidoItem.plataforma)] || pedidoItem.plataforma || '—' }}</span>
          <span class="text-muted-foreground">pedido de {{ ddmmaaaa(pedidoItem.data) }}<template v-if="pedidoItem.dias_em_devolucao != null"> · em devolução há {{ pedidoItem.dias_em_devolucao }} dia(s)</template></span>
          <label v-if="canEdit && situacoesBling.length" class="inline-flex items-center gap-1 rounded bg-muted px-2 py-0.5 text-[11px]" title="Muda a situação do pedido no Bling na hora (vale pro pedido inteiro). Não resolve a devolução: pra tirar da lista é o Resolver.">
            Bling:
            <select class="max-w-[190px] bg-transparent outline-none" :value="pedidoItem.situacao_bling_id || ''" :disabled="!!salvando" @change="mudarSituacao(($event.target as HTMLSelectElement).value, $event.target as HTMLSelectElement)">
              <option v-if="!situacoesBling.some((x) => String(x.id) === pedidoItem!.situacao_bling_id)" :value="pedidoItem.situacao_bling_id || ''">{{ pedidoItem.situacao_bling_nome || '—' }}</option>
              <option v-for="x in situacoesBling" :key="x.id" :value="String(x.id)">{{ x.nome }}</option>
            </select>
          </label>
          <span v-else class="rounded bg-muted px-2 py-0.5 text-[11px]">Bling: {{ pedidoItem.situacao_bling_nome || '—' }}</span>
          <span v-if="pedidoItem.prazo_tipo" class="rounded px-2 py-0.5 text-[11px] font-semibold" :class="pedidoItem.prazo_estado ? 'bg-red-100 text-red-800 dark:bg-red-950/50 dark:text-red-300' : 'bg-muted'" :title="pedidoItem.prazo_dica || pedidoItem.prazo_texto || ''">prazo: {{ prazoTexto(pedidoItem) }} · {{ pedidoItem.prazo_tipo }}</span>
          <span v-if="pedidoItem.resolvido" class="rounded bg-emerald-100 px-2 py-0.5 text-[11px] font-semibold text-emerald-800 dark:bg-emerald-950/50 dark:text-emerald-300">resolvida</span>
          <template v-if="podeMover && canEdit && !pedidoItem.resolvido && !foraDaLista">
            <button v-if="pedidoItem.fila !== 'fraude'" type="button" class="rounded-md border px-2 py-0.5 text-[11px] font-semibold hover:bg-muted disabled:opacity-50" :disabled="!!salvando" title="Não volta pacote: só dinheiro (chegou vazio, reembolso sem devolução)" @click="moverFila('fraude')">marcar Fraude</button>
            <button v-else type="button" class="rounded-md border px-2 py-0.5 text-[11px] font-semibold hover:bg-muted disabled:opacity-50" :disabled="!!salvando" title="Volta pacote: sai da Fraude" @click="moverFila('acompanhamento')">tirar da Fraude</button>
            <button v-if="pedidoItem.fila_manual" type="button" class="text-[11px] text-muted-foreground underline" :disabled="!!salvando" title="Fixado na mão. Soltar devolve a decisão pra regra da plataforma." @click="moverFila('regra')">soltar pra regra</button>
          </template>
          <span v-else-if="pedidoItem.fila_manual" class="text-[11px] text-muted-foreground">painel fixado na mão</span>
          <div v-if="linhasDaJanela.length > 1" class="ml-auto flex flex-wrap items-center gap-1.5">
            <span class="text-muted-foreground">produtos deste pedido:</span>
            <button
              v-for="l in linhasDaJanela"
              :key="l.sku || ''"
              type="button"
              class="max-w-[220px] truncate rounded-md px-2 py-0.5 font-semibold"
              :class="l.sku === foco.sku ? 'bg-primary text-primary-foreground' : 'border hover:bg-muted'"
              :title="l.produto || ''"
              @click="janela = { pedido: janela.pedido, sku: l.sku }"
            >{{ l.produto || l.sku }}</button>
          </div>
        </div>

        <div class="grid gap-3 p-4 lg:grid-cols-[360px_minmax(0,1fr)_minmax(0,660px)]">
          <!-- O que o sistema já sabe -->
          <div class="space-y-3">
            <div class="text-center text-[12px] font-bold text-[#1f3a5f] dark:text-sky-300">O que o sistema já sabe</div>
            <div class="rounded-md border p-3">
              <div class="mb-2 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Pedido</div>
              <dl class="grid grid-cols-[120px_minmax(0,1fr)] gap-x-2 gap-y-1 text-xs">
                <dt class="text-muted-foreground">Produto</dt><dd class="font-semibold">{{ foco.produto || '—' }}</dd>
                <dt class="text-muted-foreground">SKU da venda</dt><dd class="font-mono font-semibold">{{ foco.sku || '—' }}</dd>
                <dt class="text-muted-foreground">Quantidade</dt><dd class="font-semibold">{{ foco.quantidade ?? '—' }}</dd>
                <dt class="text-muted-foreground">Pedido Bling</dt><dd class="font-mono font-semibold">{{ pedidoItem.pedido_bling }}</dd>
                <dt class="text-muted-foreground">Nº na plataforma</dt>
                <dd class="font-mono font-semibold">{{ pedidoItem.pedido_marketplace || '—' }} <button v-if="pedidoItem.pedido_marketplace" type="button" class="align-[-2px] text-muted-foreground hover:text-foreground" title="Copiar" @click="copiar(pedidoItem.pedido_marketplace)"><Copy class="size-3" /></button></dd>
                <dt class="text-muted-foreground">Cliente</dt><dd class="font-semibold">{{ pedidoItem.cliente || '—' }}<template v-if="pedidoItem.cidade"> · {{ pedidoItem.cidade }}/{{ pedidoItem.uf }}</template></dd>
              </dl>
            </div>
            <div class="rounded-md border p-3">
              <div class="mb-2 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Caso na plataforma</div>
              <dl class="grid grid-cols-[120px_minmax(0,1fr)] gap-x-2 gap-y-1 text-xs">
                <dt class="text-muted-foreground">Prazo</dt>
                <dd class="font-semibold"><template v-if="pedidoItem.prazo_tipo">{{ prazoTexto(pedidoItem) }} · {{ pedidoItem.prazo_tipo }}<div v-if="pedidoItem.prazo_dica || pedidoItem.prazo_texto" class="font-normal text-muted-foreground">{{ pedidoItem.prazo_dica || pedidoItem.prazo_texto }}</div></template><template v-else>—</template></dd>
                <dt class="text-muted-foreground">Reemb. cliente</dt>
                <dd class="font-semibold">
                  <template v-if="pedidoItem.reembolso === true">Sim<template v-if="pedidoItem.reembolso_valor != null"> · {{ brl(pedidoItem.reembolso_valor) }}</template><template v-if="pedidoItem.reembolso_em"> · {{ ddmm(pedidoItem.reembolso_em) }}</template></template>
                  <template v-else-if="pedidoItem.reembolso === false">Não · o dinheiro ainda está com a gente</template>
                  <template v-else>a plataforma ainda não informou</template>
                </dd>
                <dt class="text-muted-foreground">Em devolução desde</dt>
                <dd class="flex items-center gap-1.5 font-semibold">
                  <input v-model="rascunho.desde" type="date" class="h-6 rounded border bg-background px-1 text-xs" :disabled="!canEdit || !!salvando" title="A data digitada vale mais que a automática; apagar volta ao automático" @change="salvarCampo('desde')" />
                  <span v-if="pedidoItem.aguardando_devolucao_data_estimada" class="font-normal text-muted-foreground" title="Data estimada: corrija se souber a certa">≈ estimada</span>
                </dd>
              </dl>
            </div>
            <div class="rounded-md border p-3">
              <div class="mb-2 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Pacote</div>
              <dl class="grid grid-cols-[120px_minmax(0,1fr)] items-center gap-x-2 gap-y-1 text-xs">
                <dt class="text-muted-foreground">Rastreio</dt>
                <dd class="flex items-center gap-1">
                  <input v-model="rascunho.rastreio" class="h-6 w-full rounded border bg-background px-1.5 font-mono text-xs" placeholder="código de rastreio" :disabled="!canEdit || !!salvando" title="O que você digitar vale mais que o automático" @blur="salvarCampo('rastreio')" @keydown.enter.prevent="($event.target as HTMLInputElement).blur()" />
                  <button v-if="pedidoItem.rastreio" type="button" class="text-muted-foreground hover:text-foreground" title="Copiar rastreio" @click="copiar(pedidoItem.rastreio)"><Copy class="size-3.5" /></button>
                </dd>
                <dt class="text-muted-foreground">Localização</dt>
                <dd><input v-model="rascunho.localizacao" class="h-6 w-full rounded border bg-background px-1.5 text-xs" placeholder="onde o pacote está" :disabled="!canEdit || !!salvando" @blur="salvarCampo('localizacao')" @keydown.enter.prevent="($event.target as HTMLInputElement).blur()" /></dd>
                <template v-if="pedidoItem.entrega_localizacao"><dt class="text-muted-foreground">Entrega da ida</dt><dd class="font-semibold">{{ pedidoItem.entrega_localizacao }}</dd></template>
                <dt class="text-muted-foreground">Últ. movimentação</dt><dd class="font-semibold">{{ ddmmaaaa(pedidoItem.localizacao_data) }}</dd>
                <dt class="text-muted-foreground">Chegou em</dt><dd class="font-semibold">{{ pedidoItem.devolucao_chegou_em ? ddmmaaaa(pedidoItem.devolucao_chegou_em) : 'sem confirmação ainda' }}</dd>
              </dl>
            </div>
          </div>

          <!-- Provas -->
          <div class="space-y-3">
            <div class="text-center text-[12px] font-bold text-[#1f3a5f] dark:text-sky-300">Provas</div>
            <div class="rounded-md border p-3">
              <div class="grid gap-3 md:grid-cols-2">
                <div>
                  <div class="mb-1 text-[11px] font-semibold">Vídeo do envio
                    <Check v-if="pedidoItem.video_link || envioColado" class="inline size-3.5 text-emerald-700 dark:text-emerald-400" />
                    <span v-else-if="pedidoItem.video_status === 'pendente'" class="text-amber-700 dark:text-amber-400">pedido</span>
                    <span v-else class="text-red-600 dark:text-red-400">falta</span>
                  </div>
                  <template v-if="pedidoItem.video_link">
                    <a :href="pedidoItem.video_link" target="_blank" rel="noopener" class="flex h-[110px] items-center justify-center rounded-md border bg-muted text-[13px] text-muted-foreground hover:text-foreground">▶ abrir vídeo</a>
                    <div class="mt-1 text-[11px] text-muted-foreground">enviado por {{ pedidoItem.video_enviado_por || 'equipe' }} em {{ ddmm(pedidoItem.video_enviado_em) }}</div>
                    <div v-if="canEdit && !videoApagando" class="mt-1.5"><button type="button" class="rounded-md border px-2 py-1 text-[11px] font-semibold hover:bg-muted" title="Apaga o link e devolve pra equipe refazer (pede o motivo)" @click="videoApagando = true">apagar e pedir de novo</button></div>
                    <div v-if="videoApagando" class="mt-1.5 flex gap-1.5">
                      <input v-model="videoMotivo" class="h-7 w-full rounded border bg-background px-1.5 text-xs" placeholder="motivo pra refazer (ex.: não mostra o lacre)" @keydown.enter.prevent="videoMotivo.trim().length >= 3 && solicitarVideo(videoMotivo)" />
                      <button type="button" class="rounded-md border px-2 text-[11px] font-semibold hover:bg-muted disabled:opacity-50" :disabled="videoMotivo.trim().length < 3 || !!salvando" @click="solicitarVideo(videoMotivo)">pedir</button>
                      <button type="button" class="text-[11px] text-muted-foreground underline" @click="videoApagando = false">cancelar</button>
                    </div>
                  </template>
                  <template v-else-if="envioColado && !editandoLink.envio">
                    <a :href="envioColado" target="_blank" rel="noopener" class="flex h-[110px] items-center justify-center rounded-md border bg-muted text-[13px] text-muted-foreground hover:text-foreground">▶ abrir vídeo</a>
                    <div class="mt-1 flex items-center gap-2 text-[11px] text-muted-foreground">
                      <span>{{ linkPendente.envio ? 'link colado — entra no lançamento' : 'link colado no lançamento' }}</span>
                      <button v-if="canEdit" type="button" class="font-semibold text-primary underline" @click="editarLink('envio')">trocar link</button>
                    </div>
                  </template>
                  <template v-else>
                    <div class="flex h-[110px] items-center justify-center rounded-md border border-dashed px-3 text-center text-xs text-muted-foreground">
                      <template v-if="pedidoItem.video_status === 'pendente'">pedido à equipe do SKU em {{ ddmm(pedidoItem.video_solicitado_em) }}<template v-if="pedidoItem.video_solicitado_por"> por {{ pedidoItem.video_solicitado_por }}</template><template v-if="pedidoItem.video_refazer_motivo"> · refazer: {{ pedidoItem.video_refazer_motivo }}</template></template>
                      <template v-else-if="pedidoItem.video_status === 'sem_video'">a equipe respondeu: sem vídeo — {{ pedidoItem.video_sem_motivo }}</template>
                      <template v-else>sem vídeo ainda</template>
                    </div>
                    <div v-if="canEdit" class="mt-1.5 flex gap-1.5">
                      <button v-if="pedidoItem.video_status !== 'pendente'" type="button" class="rounded-md border px-2 py-1 text-[11px] font-semibold hover:bg-muted disabled:opacity-50" :disabled="!!salvando" title="A equipe do SKU vê o pedido na aba Pedidos do Controle de Estoque" @click="solicitarVideo()">{{ pedidoItem.video_status === 'sem_video' ? 'pedir de novo' : 'solicitar vídeo' }}</button>
                      <button v-else type="button" class="rounded-md border px-2 py-1 text-[11px] font-semibold hover:bg-muted disabled:opacity-50" :disabled="!!salvando" @click="cancelarVideo">cancelar pedido</button>
                    </div>
                    <!-- Quem já tem o link (MEGA) cola aqui, uma vez só: vale pro pedido inteiro. -->
                    <div v-if="canEdit" class="mt-1.5 flex items-center gap-1.5">
                      <input v-model="linkDigitado.envio" class="h-7 w-full rounded border bg-background px-2 text-xs" placeholder="ou cole aqui o link do vídeo do envio (MEGA)" :disabled="gravandoLink" data-provas-envio @keydown.enter.prevent="salvarLinkProvas('envio')" @blur="salvarLinkProvas('envio')" />
                      <button v-if="editandoLink.envio" type="button" class="whitespace-nowrap text-[11px] text-muted-foreground underline" @mousedown.prevent="cancelarLink('envio')">cancelar</button>
                    </div>
                  </template>
                </div>
                <div>
                  <div class="mb-1 text-[11px] font-semibold">Vídeo da abertura
                    <Check v-if="aberturaDaJanela" class="inline size-3.5 text-emerald-700 dark:text-emerald-400" /><span v-else class="text-red-600 dark:text-red-400">falta</span>
                  </div>
                  <template v-if="aberturaDaJanela && !editandoLink.abertura">
                    <a :href="aberturaDaJanela" target="_blank" rel="noopener" class="flex h-[110px] items-center justify-center rounded-md border bg-muted text-[13px] text-muted-foreground hover:text-foreground">▶ abrir vídeo</a>
                    <div class="mt-1 flex items-center gap-2 text-[11px] text-muted-foreground">
                      <span>{{ linkPendente.abertura ? 'link colado — entra no lançamento' : 'vale pro pedido inteiro' }}</span>
                      <button v-if="canEdit" type="button" class="font-semibold text-primary underline" @click="editarLink('abertura')">trocar link</button>
                    </div>
                  </template>
                  <template v-else>
                    <div class="flex h-[110px] items-center justify-center rounded-md border border-dashed px-3 text-center text-xs text-muted-foreground">sem vídeo da abertura ainda</div>
                    <div v-if="canEdit" class="mt-1.5 flex items-center gap-1.5">
                      <input v-model="linkDigitado.abertura" class="h-7 w-full rounded border bg-background px-2 text-xs" placeholder="cole aqui o link do vídeo da abertura" :disabled="gravandoLink" data-provas-abertura @keydown.enter.prevent="salvarLinkProvas('abertura')" @blur="salvarLinkProvas('abertura')" />
                      <button v-if="editandoLink.abertura" type="button" class="whitespace-nowrap text-[11px] text-muted-foreground underline" @mousedown.prevent="cancelarLink('abertura')">cancelar</button>
                    </div>
                  </template>
                </div>
              </div>
            </div>
            <div class="rounded-md border p-3">
              <div class="mb-1 flex items-center gap-2 text-[11px] font-semibold">
                <span>Fotos da devolução <span class="font-normal text-muted-foreground">({{ fotosDoPedido.length + fotosPendentes.length }}) · vão como evidência do chamado, junto com os vídeos</span></span>
                <label v-if="canEdit" class="ml-auto inline-flex cursor-pointer items-center gap-1 whitespace-nowrap rounded-md border px-2 py-1 text-[11px] font-semibold hover:bg-muted" :class="enviandoFotos ? 'pointer-events-none opacity-50' : ''" title="Foto (JPG/PNG), PDF ou vídeo. Vale pro pedido inteiro: não precisa repetir por produto.">
                  <Plus class="size-3" /> {{ enviandoFotos ? 'enviando…' : 'adicionar fotos' }}
                  <input type="file" class="hidden" multiple accept="image/jpeg,image/png,application/pdf,video/mp4,video/quicktime,video/webm" data-provas-fotos @change="escolherFotosProvas" />
                </label>
              </div>
              <div v-if="fotosDoPedido.length" class="grid grid-cols-4 gap-2">
                <div v-for="f in fotosDoPedido" :key="f.id" class="group relative h-[92px] overflow-hidden rounded-md border bg-muted">
                  <a :href="`/api/devolutions/anexos/${f.id}`" target="_blank" rel="noopener" class="block size-full" :title="f.filename || ''">
                    <img :src="`/api/devolutions/anexos/${f.id}`" class="size-full object-cover" alt="foto da devolução" loading="lazy" />
                  </a>
                  <button v-if="canEdit" type="button" class="absolute right-1 top-1 hidden size-5 items-center justify-center rounded bg-background/90 text-muted-foreground hover:text-red-600 group-hover:inline-flex" title="Remover esta foto" @click="removerFotoProvas(f.id)"><X class="size-3.5" /></button>
                </div>
              </div>
              <div v-else-if="!fotosPendentes.length" class="rounded-md border border-dashed px-3 py-4 text-center text-xs text-muted-foreground">nenhuma foto ainda</div>
              <!-- Escolhidas antes de existir lançamento: sobem sozinhas junto com o 1º lançamento. -->
              <div v-if="fotosPendentes.length" class="mt-2 rounded-md border border-amber-300 bg-amber-50/60 px-2 py-1.5 text-[11px] dark:border-amber-800 dark:bg-amber-950/20">
                <div class="mb-1 font-semibold text-amber-900 dark:text-amber-200">{{ fotosPendentes.length }} arquivo(s) esperando o lançamento — sobem sozinhos quando você lançar</div>
                <div class="flex flex-wrap gap-1.5">
                  <span v-for="(f, k) in fotosPendentes" :key="k" class="inline-flex max-w-[200px] items-center gap-1 rounded border bg-background px-1.5 py-0.5">
                    <span class="truncate">{{ f.name }}</span>
                    <button type="button" class="text-muted-foreground hover:text-red-600" title="Tirar da fila" @click="fotosPendentes.splice(k, 1)"><X class="size-3" /></button>
                  </span>
                </div>
              </div>
              <!-- Vídeo e PDF anexados não são foto: viram link (não baixa o arquivo pra mostrar). -->
              <div v-if="outrosAnexos.length" class="mt-2 flex flex-wrap gap-1.5">
                <span v-for="a in outrosAnexos" :key="a.id" class="inline-flex max-w-[240px] items-center gap-1 rounded-md border px-2 py-1 text-[11px] font-semibold">
                  <a :href="`/api/devolutions/anexos/${a.id}`" target="_blank" rel="noopener" class="inline-flex min-w-0 items-center gap-1 hover:underline" :title="a.filename || ''"><ExternalLink class="size-3 shrink-0" /> <span class="truncate">{{ (a.content_type || '').startsWith('video/') ? 'vídeo' : 'arquivo' }} · {{ a.filename || 'anexo' }}</span></a>
                  <button v-if="canEdit" type="button" class="text-muted-foreground hover:text-red-600" title="Remover este arquivo" @click="removerFotoProvas(a.id)"><X class="size-3" /></button>
                </span>
              </div>
            </div>
            <div class="rounded-md border p-3">
              <div class="mb-2 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Observação do pedido</div>
              <textarea v-model="rascunho.observacao" rows="3" class="w-full resize-y rounded-md border bg-background px-2 py-1.5 text-xs" placeholder="recado pra quem acompanha (escrever “aguarde 60 dias” ou “até 30/11” segura o prazo de 45 dias)" :disabled="!canEdit || !!salvando" @blur="salvarCampo('observacao')" />
            </div>
          </div>

          <!-- O que a pessoa faz -->
          <div class="space-y-3">
            <div class="text-center text-[12px] font-bold text-[#1f3a5f] dark:text-sky-300">O que a pessoa faz</div>

            <div class="rounded-md border p-3">
              <div class="mb-2 flex items-center text-[10px] font-semibold uppercase tracking-wide text-muted-foreground"><span class="mr-1.5 inline-flex size-[18px] items-center justify-center rounded-full bg-[#1f3a5f] text-[10.5px] font-bold text-white">1</span>Lançamento <span class="ml-1 font-normal normal-case tracking-normal">· uma peça por cartão · condição, estoque e motivo</span></div>
              <div v-if="foraDeLancar" class="mb-2 rounded-md border border-amber-300 bg-amber-50 px-2.5 py-1.5 text-[11.5px] text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
                Este pedido está em <b>{{ pedidoItem.situacao_bling_nome || 'outra situação' }}</b> no Bling<template v-if="pedidoItem.lancamentos.length"> e ainda tem produto sem lançamento</template>. Pra lançar, mude ali em cima, em <b>Bling:</b>, para <b>Aguardando Devolução</b> — as peças que faltam aparecem aqui em seguida.
              </div>
              <!-- :key = pedido: trocar de pedido recomeça o bloco do zero (rascunho de um não vaza pro outro). -->
              <DevolucaoLancar
                ref="lancarRef"
                :key="pedidoItem.pedido_bling || ''"
                :pedido="pedidoItem.pedido_bling || ''"
                :fila="pedidoItem.fila"
                :video-link="pedidoItem.video_link"
                :link-envio="envioDaJanela"
                :link-abertura="aberturaDaJanela"
                :fotos="fotosPendentes"
                :observacao="obsDaJanela"
                @fotos-enviadas="fotosPendentes = []"
                :can-edit="canEdit"
                :can-delete="canDelete"
                :is-admin="isAdmin"
                @mudou="aoMudarLancamento"
              />
            </div>

            <div class="rounded-md border p-3">
              <!-- :key = pedido: trocar de pedido recomeça o bloco (prévia e instrução de um não vazam pro outro). -->
              <DevolucaoChamado
                ref="chamadoRef"
                :key="pedidoItem.pedido_bling || ''"
                :pedido="pedidoItem.pedido_bling || ''"
                :plataforma="PLATAFORMA_NOME[plataformaKey(pedidoItem.plataforma)] || pedidoItem.plataforma || ''"
                :can-edit="canEdit"
                :linhas="linhasPrevia"
                :link-envio="envioDaJanela"
                :link-abertura="aberturaDaJanela"
                :fotos="fotosDoPedido.length + fotosPendentes.length"
                :observacao="obsDaJanela"
                :obs-misturadas="obsMisturadas"
                :prazo="pedidoItem.prazo_tipo ? `${prazoTexto(pedidoItem)} · ${pedidoItem.prazo_tipo}` : ''"
                :prazo-contestacao="lancarRef?.prazoContestacao() ?? null"
                @observacao="salvarObsDaJanela"
                @mudou="aoMudarLancamento"
              />
            </div>

            <div v-if="veCusto" class="rounded-md border p-3">
              <div class="mb-1.5 flex items-center text-[10px] font-semibold uppercase tracking-wide text-muted-foreground"><span class="mr-1.5 inline-flex size-[18px] items-center justify-center rounded-full bg-[#1f3a5f] text-[10.5px] font-bold text-white">3</span>Reembolso <span class="ml-1 font-normal normal-case tracking-normal">· a conta deste pedido, preenchida sozinha</span></div>
              <table v-if="contaDaJanela" class="w-full border-collapse text-xs tabular-nums">
                <tbody>
                  <tr><td class="w-[112px] whitespace-nowrap py-1 text-right font-semibold" :class="contaDaJanela.recebido ? 'text-emerald-700 dark:text-emerald-400' : 'text-muted-foreground'">+ {{ brl(contaDaJanela.recebido) }}</td><td class="py-1 pl-3">recebido pelo chamado <span class="text-muted-foreground">{{ pedidoItem.chamado ? (pedidoItem.tem_chamado_aberto ? '· ainda aberto' : '· concluído') : '· sem chamado' }}</span></td></tr>
                  <tr v-if="contaDaJanela.custo_produto"><td class="whitespace-nowrap py-1 text-right font-semibold text-red-700 dark:text-red-400">− {{ brl(contaDaJanela.custo_produto) }}</td><td class="py-1 pl-3">custo do produto <span class="text-muted-foreground">· perdimento ou extraviado</span></td></tr>
                  <tr v-if="contaDaJanela.custo_manutencao"><td class="whitespace-nowrap py-1 text-right font-semibold text-red-700 dark:text-red-400">− {{ brl(contaDaJanela.custo_manutencao) }}</td><td class="py-1 pl-3">custo de manutenção</td></tr>
                  <tr class="border-t"><td class="whitespace-nowrap py-1.5 text-right text-[13px] font-bold" :class="contaDaJanela.resultado < 0 ? 'text-red-700 dark:text-red-400' : 'text-emerald-700 dark:text-emerald-400'">{{ sinal(contaDaJanela.resultado) }}</td><td class="py-1.5 pl-3 font-bold">resultado</td></tr>
                </tbody>
              </table>
              <div v-else class="text-xs text-muted-foreground">nada a contar ainda: sem custo lançado e sem chamado concluído com valor.</div>
              <div class="mt-1 text-[11px] text-muted-foreground">Recebido = o valor informado ao concluir o chamado, quando é lucro. Chamado concluído com prejuízo conta R$ 0,00 aqui, pra não descontar o mesmo produto duas vezes.</div>
            </div>

            <div class="rounded-md border border-blue-300 bg-blue-50/60 p-3 dark:border-blue-800 dark:bg-blue-950/20">
              <div class="mb-2 flex items-center text-[10px] font-semibold uppercase tracking-wide text-blue-900 dark:text-blue-200"><span class="mr-1.5 inline-flex size-[18px] items-center justify-center rounded-full bg-[#1f3a5f] text-[10.5px] font-bold text-white">{{ veCusto ? 4 : 3 }}</span>Resolver <span class="ml-1 font-normal normal-case tracking-normal">· tira o pedido da lista de abertas</span></div>
              <template v-if="pedidoItem.resolvido">
                <div class="text-xs">Resolvida em <b>{{ ddmmaaaa(pedidoItem.resolvido_at) }}</b><template v-if="pedidoItem.resolvido_por_nome"> por <b>{{ pedidoItem.resolvido_por_nome }}</b></template> · {{ comoTerminou_(pedidoItem.resolvido_como) }}</div>
                <button v-if="canEdit && podeResolver" type="button" class="mt-2 rounded-md border bg-background px-2.5 py-1.5 text-xs font-semibold hover:bg-muted disabled:opacity-50" :disabled="!!salvando" title="Volta pra lista de abertas. Não mexe no Bling." @click="reabrir">reabrir</button>
              </template>
              <div v-else-if="foraDaLista" class="text-xs text-muted-foreground">
                Este pedido ainda não entrou em devolução, então não há o que resolver. Ele entra na lista sozinho quando o Bling for para <b>Aguardando Devolução</b> (ali em cima, em <b>Bling:</b>).
              </div>
              <template v-else>
                <div v-if="pedidoItem.pronto_resolver" class="mb-2 text-xs"><b class="text-blue-800 dark:text-blue-200">A finalizar:</b> {{ pedidoItem.pronto_resolver }}.</div>
                <div class="mb-2 text-[11px] text-muted-foreground" data-resolve-sozinho>Quando todas as peças voltarem pro estoque, o sistema resolve sozinho.</div>
                <!-- 16/11 (Vinicius): "se já voltou estoque tá resolvido… o chamado em aberto é outra coisa". -->
                <div v-if="pedidoItem.tem_chamado_aberto" class="mb-2 text-[11px] text-muted-foreground">O chamado continua na aba Chamados: dá pra resolver a devolução agora — o valor que o chamado ganhar entra no Reembolso quando ele terminar.</div>
                <div v-if="linhasDaJanela.length > 1" class="mb-2 text-[11px] text-muted-foreground">Vale pro pedido inteiro ({{ linhasDaJanela.length }} produtos): a situação do Bling é do pedido.</div>
                <div v-if="!podeResolver" class="rounded border border-dashed bg-background/60 px-2.5 py-2 text-xs text-muted-foreground" data-so-adm-cairo>
                  Só o <b class="text-foreground">adm</b> e o <b class="text-foreground">Cairo</b> resolvem uma devolução. Situação atual no Bling: <b class="text-foreground">{{ pedidoItem.situacao_bling_nome || '—' }}</b>.
                </div>
                <div v-else class="grid items-end gap-2 sm:grid-cols-[1fr_1fr_auto]">
                  <label class="block">
                    <span class="mb-1 block text-[11px] font-semibold">Como terminou <span class="text-red-500">*</span></span>
                    <select v-model="resolverForm.como" class="h-8 w-full rounded-md border bg-background px-2 text-xs" :disabled="!canEdit">
                      <option value="" disabled>escolha…</option>
                      <option v-for="c in comoTerminou" :key="c" :value="c">{{ c }}</option>
                    </select>
                  </label>
                  <label class="block">
                    <span class="mb-1 block text-[11px] font-semibold">Nova situação no Bling</span>
                    <select v-model="resolverForm.situacao" class="h-8 w-full rounded-md border bg-background px-2 text-xs" :disabled="!canEdit">
                      <option value="">não mexer ({{ pedidoItem.situacao_bling_nome || '—' }})</option>
                      <option v-for="s in situacoesBling" :key="s.id" :value="String(s.id)">{{ s.nome }}</option>
                    </select>
                  </label>
                  <button type="button" class="inline-flex h-8 items-center gap-1 whitespace-nowrap rounded-md bg-primary px-3 text-xs font-semibold text-primary-foreground disabled:opacity-50" :disabled="!canEdit || !resolverForm.como || !!salvando" @click="resolver"><Check class="size-3.5" /> resolver</button>
                </div>
                <div v-if="podeResolver" class="mt-1.5 text-[11px] text-muted-foreground">Situação atual no Bling: <b class="text-foreground">{{ pedidoItem.situacao_bling_nome || '—' }}</b>. Dá pra reabrir depois.</div>
              </template>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Informar (só admin): mesma lista e mesmo cadastro de avisos da tela de antes. -->
    <InformarThreemaModal
      :open="informarOpen"
      contexto="devolucoes"
      contexto-auto="devolucoes_auto"
      enviar-com-auto
      descricao="Quem está marcado recebe no Threema o aviso automático de prazo: quando falta menos de 24 h pra loja responder na plataforma (TikTok: confirmar ou recusar o pacote recebido; Shopee: conferir o pacote recebido, enviar evidências, responder à proposta; Mercado Livre: revisar a devolução recebida em 3 dias), e um último aviso faltando 4 h — por caso e por prazo. O pedido de só reembolso tem aviso próprio pela aba Chamados. 'Enviar agora' manda a lista dos pedidos aguardando devolução, com dias parados, última localização e prazo. A seleção fica salva."
      @close="informarOpen = false"
    />
  </div>
</template>
