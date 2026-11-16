<script setup lang="ts">
// Painel NOVO de Devoluções (Vinicius, 16/11/2026) — em teste, ao lado do
// antigo (pages/devolucoes.vue, que continua intacto e é o caminho de volta).
//
// Uma lista só: entra quem cair em Aguardando Devolução no Bling e só sai no
// botão Resolver. Uma linha por produto; clicar abre a janela grande.
// Padrão visual da Logística / Chamados (faixa azul, cartões, Onde agir hoje).
//
// O que esta tela FAZ: ler tudo, filtrar, observação, rastreio, localização,
// "em devolução desde", pedir/cancelar vídeo, mover Fraude, Resolver/reabrir.
// O que ela MANDA pro painel antigo: lançar (estoque no Bling, chamado
// automático, fotos, lixeira) — regras delicadas que não foram reescritas.
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { Check, Copy, Download, ExternalLink, Plus, RefreshCw, Search, X } from 'lucide-vue-next'
import { isoDateBrt, isoToday } from '~/lib/date'

definePageMeta({ middleware: ['permission'], permission: { resource: 'devolucoes', action: 'view' } })

const { api } = useApi()
const canEdit = useCan('devolucoes', 'edit')
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
  chamado: { id: string; numero: string | null; resolvido: boolean; resolvido_at: string | null } | null
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
    const res = await api<Painel>(`/api/devolucoes-painel?mostrar=${mostrar.value}`)
    if (minha !== cargaAtual) return
    items.value = res.items
    comoTerminou.value = res.como_terminou
    podeMover.value = res.pode_mover_painel
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

// ── Filtros ─────────────────────────────────────────────────────────────
const naAba = computed(() =>
  plataformaTab.value === 'todas' ? items.value : items.value.filter((i) => plataformaKey(i.plataforma) === plataformaTab.value),
)
const contas = computed(() => [...new Set(naAba.value.map(contaDe))].sort((a, b) => a.localeCompare(b)))
// Base dos cartões e do "Onde agir": aba + conta + período + busca.
const base = computed(() => {
  const q = search.value.trim().toLowerCase()
  return naAba.value.filter((i) => {
    if (contaFilter.value !== 'all' && contaDe(i) !== contaFilter.value) return false
    const dia = i.data ? isoDateBrt(new Date(i.data)) : ''
    if (dataIni.value && (!dia || dia < dataIni.value)) return false
    if (dataFim.value && (!dia || dia > dataFim.value)) return false
    if (!q) return true
    return [
      i.pedido_bling, i.pedido_marketplace, i.cliente, i.sku, i.produto, i.rastreio, i.localizacao,
      i.cidade, i.observacao, i.loja, i.sku_lancado,
    ].some((v) => (v || '').toLowerCase().includes(q))
  })
})

type Agir = { chave: string; titulo: string; detalhe: string; dot: string; grave: boolean; teste: (i: Item) => boolean }
const AGIR: Agir[] = [
  { chave: 'responder', titulo: 'Responder em 24 h', detalhe: 'prazo da plataforma vencendo', dot: 'bg-red-600', grave: true, teste: urgente },
  { chave: 'estoque', titulo: 'Estoque não entrou no Bling', detalhe: 'conferir e tentar de novo no lançamento', dot: 'bg-red-600', grave: true, teste: estoquePendente },
  { chave: 'falta_lancar', titulo: 'Chegou e falta lançar', detalhe: 'lançar condição e estoque', dot: 'bg-red-600', grave: false, teste: (i) => i.falta_lancar },
  { chave: 'pronto', titulo: 'Pronto pra resolver', detalhe: 'estoque lançado ou reembolso ganho: falta o Resolver', dot: 'bg-blue-600', grave: false, teste: (i) => !!i.pronto_resolver },
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
  ]
})
function cartaoLigado(k: string): boolean {
  if (k === 'falta_lancar') return recorte.value === 'falta_lancar'
  if (k === 'all') return false
  return etapaFilter.value === k && !recorte.value
}
function clicarCartao(k: string) {
  if (k === 'falta_lancar') {
    etapaFilter.value = 'all'
    recorte.value = recorte.value === 'falta_lancar' ? '' : 'falta_lancar'
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
  for (const i of items.value) if (i.conta && i.pedido_bling) m.set(i.pedido_bling, i.conta)
  return m
})
function primeiraVisivel(n: number): boolean {
  return n === 0 || filtrados.value[n - 1]?.pedido_bling !== filtrados.value[n]?.pedido_bling
}
function contaDaLinha(i: Item, n: number): Item['conta'] {
  return primeiraVisivel(n) && i.pedido_bling ? contaPorPedido.value.get(i.pedido_bling) || null : null
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

// ── Excel (csv que o Excel abre) ────────────────────────────────────────
function exportar() {
  const cab = [
    'Data', 'Bling', 'Nº na plataforma', 'Plataforma', 'Conta', 'SKU', 'Produto', 'Etapa', 'Dias', 'Prazo',
    'Prazo de', 'Reemb. cliente', 'Valor reemb. cliente', 'Localização', 'Rastreio', 'Vídeo envio',
    'Vídeo abertura', 'Foto', 'Condição', 'SKU lançado', 'Chamado', 'Status Bling', 'Observação',
  ]
  const linhas = filtrados.value.map((i) => [
    ddmmaaaa(i.data), i.pedido_bling, i.pedido_marketplace, PLATAFORMA_NOME[plataformaKey(i.plataforma)] || i.plataforma,
    contaDe(i), i.sku, i.produto, ETAPA_LABEL[i.etapa], i.dias_em_devolucao ?? '', i.prazo_em ? ddmmaaaa(i.prazo_em) : '',
    i.prazo_tipo, i.reembolso == null ? '' : i.reembolso ? 'Sim' : 'Não',
    i.reembolso_valor != null ? i.reembolso_valor.toFixed(2).replace('.', ',') : '', i.localizacao, i.rastreio,
    i.video_link ? 'Sim' : i.video_status === 'pendente' ? 'pedido' : '', temAbertura(i) ? 'Sim' : '',
    temFoto(i) ? 'Sim' : '', i.condicao_tela, i.sku_lancado,
    i.chamado ? (i.tem_chamado_aberto ? 'Aberto' : 'Concluído') : '', i.situacao_bling_nome, i.observacao,
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
const linhasDaJanela = computed(() =>
  janela.value ? items.value.filter((i) => i.pedido_bling === janela.value!.pedido) : [],
)
// Linha (produto) em foco e a 1ª do pedido (que carrega chamado e conta).
const foco = computed<Item | null>(
  () => linhasDaJanela.value.find((i) => i.sku === janela.value?.sku) || linhasDaJanela.value[0] || null,
)
const pedidoItem = computed<Item | null>(() => linhasDaJanela.value[0] || null)
const contaDaJanela = computed(() => (janela.value ? contaPorPedido.value.get(janela.value.pedido) || null : null))
const anexosDoPedido = computed(() => (pedidoItem.value?.lancamentos || []).flatMap((l) => l.anexos))
const fotosDoPedido = computed(() => anexosDoPedido.value.filter(ehFoto))
const outrosAnexos = computed(() => anexosDoPedido.value.filter((a) => !ehFoto(a)))
const aberturaDoPedido = computed(
  () => (pedidoItem.value?.lancamentos || []).map((l) => (l.link_abertura || '').trim()).find(Boolean) || null,
)
// A busca de pedido do painel antigo (onde o lançamento acontece) só acha
// pedido nestas situações do Bling. Fora delas o botão "lançar" não acharia
// nada: a janela avisa em vez de mandar a pessoa pra uma busca vazia.
const SITUACOES_QUE_LANCAM = ['83957', '83960', '83961', '83966', '84677', '545902']
const podeLancar = computed(() => SITUACOES_QUE_LANCAM.includes(pedidoItem.value?.situacao_bling_id || ''))
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
  videoMotivo.value = ''
  videoApagando.value = false
  resolverForm.como = ''
  resolverForm.situacao = ''
  if (!situacoesBling.value.length) void carregarSituacoes()
}
function carregarRascunho() {
  const p = pedidoItem.value
  rascunho.observacao = original.observacao = p?.observacao || ''
  rascunho.rastreio = original.rastreio = p?.rastreio || ''
  rascunho.localizacao = original.localizacao = p?.localizacao || ''
  rascunho.desde = original.desde = p?.aguardando_devolucao_data || ''
}
// Fechar (X, Esc, clique fora) salva o que ficou digitado: tirar o campo da
// tela com Esc não dispara o "saiu do campo", e o texto se perderia.
function fecharJanela() {
  salvarPendentes()
  janela.value = null
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
  if (!p?.pedido_bling) return
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
  try {
    const salvo = JSON.parse(localStorage.getItem(FILTROS_KEY) || '{}')
    if (salvo.p === 'outras' || PLATAFORMAS.some((p) => p.key === salvo.p)) plataformaTab.value = salvo.p
    if (['all', 'aguardando', 'chegou', 'manutencao', 'fraude'].includes(salvo.e)) etapaFilter.value = salvo.e
    if (typeof salvo.c === 'string') contaFilter.value = salvo.c
  } catch { /* filtro salvo inválido: começa limpo */ }
  const q = useRoute().query.search
  if (typeof q === 'string' && q.trim()) search.value = q.trim()
  void load().then(() => {
    if (contaFilter.value !== 'all' && !contas.value.includes(contaFilter.value)) contaFilter.value = 'all'
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
      <span class="rounded bg-amber-100 px-1.5 py-0.5 text-[10.5px] font-semibold text-amber-800 dark:bg-amber-950/50 dark:text-amber-300" title="Painel novo, em teste. O painel de sempre continua no menu Devoluções, com tudo como estava.">painel novo · em teste</span>
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
        <NuxtLink to="/devolucoes?tab=lancamentos" class="inline-flex items-center gap-1 rounded-md border px-2.5 py-1 hover:bg-muted" title="Abre o painel de sempre (Acompanhamento, Fraude e Lançamentos)">painel antigo</NuxtLink>
        <NuxtLink v-if="canEdit" to="/devolucoes?tab=lancamentos&lancar=novo" class="inline-flex items-center gap-1 whitespace-nowrap rounded-md bg-primary px-2.5 py-1 text-primary-foreground" title="Lançar uma devolução: abre a busca de pedido do painel de sempre">
          <Plus class="size-3.5" /> nova devolução
        </NuxtLink>
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
        <input v-model="search" class="w-72 bg-transparent outline-none" placeholder="buscar pedido, produto, SKU, cliente, rastreio" />
      </label>
      <button v-if="filtrosAtivos" type="button" class="text-muted-foreground underline" @click="limparFiltros">limpar</button>
      <span class="text-muted-foreground">{{ pedidos(filtrados) }} pedidos · {{ filtrados.length }} de {{ naAba.length }} produtos</span>
    </div>

    <!-- Cartões -->
    <div class="grid grid-cols-2 gap-2 lg:grid-cols-5">
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
      <span class="text-[13px] font-bold">Status Bling dos pedidos</span>
      <button
        v-for="sp in statusPills"
        :key="sp.nome"
        type="button"
        class="inline-flex items-center gap-1.5 rounded bg-muted px-2 py-0.5 font-semibold focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
        :class="recorte === `bling:${sp.nome}` ? 'ring-2 ring-primary' : ''"
        @click="alternarRecorte(`bling:${sp.nome}`)"
      >
        <span class="size-[7px] rounded-full" :class="sp.dot" />
        {{ sp.n }} {{ sp.nome }}
      </button>
      <span class="ml-auto hidden text-[11px] text-muted-foreground lg:inline">clique numa etiqueta ou num cartão para filtrar</span>
    </div>

    <div v-if="error" class="text-sm text-red-500">erro: {{ error }}</div>

    <div class="grid items-start gap-3 xl:grid-cols-[minmax(0,1fr)_340px]">
      <div class="flex min-w-0 flex-col gap-2.5">
        <!-- Tabela -->
        <div class="flex min-w-0 flex-col rounded-md border bg-card">
          <div class="relative flex items-center justify-center rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-xs font-semibold text-white">
            <span>Devoluções — uma linha por produto</span>
            <span class="absolute right-3 hidden text-[11px] font-normal text-slate-300 2xl:block">entra sozinho quando cai em Aguardando Devolução × sai só no Resolver</span>
          </div>
          <div class="overflow-x-auto">
            <!-- Larguras fixas em tudo, menos na Localização: "o que sobrar deixa a
                 localização" (Vinicius, mesma regra da Logística). Com o menu lateral
                 aberto em tela de 1920 sobra uma rolagem curta pro lado; com ele
                 recolhido cabe tudo. -->
            <table class="w-full min-w-[1410px] table-fixed border-collapse text-[11.5px]">
              <colgroup>
                <col class="w-[42px]"><col class="w-[58px]"><col class="w-[120px]"><col class="w-[76px]"><col class="w-[74px]"><col class="w-[140px]">
                <col class="w-[90px]"><col class="w-[36px]"><col class="w-[90px]"><col class="w-[70px]"><col>
                <col class="w-[40px]"><col class="w-[60px]"><col class="w-[36px]">
                <col class="w-[78px]"><col class="w-[96px]"><col class="w-[82px]"><col class="w-[70px]">
              </colgroup>
              <thead class="bg-muted text-[10px] uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th class="border-b px-1.5 py-1 text-center font-semibold" colspan="6">Pedido</th>
                  <th class="border-b border-l border-l-slate-300 px-1.5 py-1 text-center font-semibold dark:border-l-slate-600" colspan="5">Devolução</th>
                  <th class="border-b border-l border-l-slate-300 px-1.5 py-1 text-center font-semibold dark:border-l-slate-600" colspan="3">Provas</th>
                  <th class="border-b border-l border-l-slate-300 px-1.5 py-1 text-center font-semibold dark:border-l-slate-600" colspan="4">Lançamento</th>
                </tr>
                <tr>
                  <th class="border-b px-1.5 py-1 text-center font-semibold" title="Data do pedido">Data</th>
                  <th class="border-b px-1.5 py-1 text-center font-semibold" title="Número do pedido no Bling">Bling</th>
                  <th class="border-b px-1.5 py-1 text-center font-semibold" title="Número do pedido na plataforma (um clique seleciona para copiar)">Nº na plataforma</th>
                  <th class="border-b px-1.5 py-1 text-center font-semibold">Conta</th>
                  <th class="border-b px-1.5 py-1 text-left font-semibold" title="SKU da venda">SKU</th>
                  <th class="border-b px-1.5 py-1 text-left font-semibold">Produto</th>
                  <th class="border-b border-l border-l-slate-300 px-1.5 py-1 text-center font-semibold dark:border-l-slate-600" title="Aguardando = o pacote não chegou · Chegou = chegou (falta lançar ou já lançada) · Manutenção = com o técnico · Fraude = não volta pacote, só dinheiro">Etapa</th>
                  <th class="border-b px-1.5 py-1 text-center font-semibold" title="Há quantos dias o pedido está em devolução (desde que caiu em Aguardando Devolução). Não recomeça quando a etapa muda.">Dias</th>
                  <th class="border-b px-1.5 py-1 text-center font-semibold" title="O prazo que está correndo nesta etapa, o mais próximo de vencer. A palavra de baixo diz do que é: disputa, responder, contestar, pacote chegar (45 dias) ou técnico (30 dias).">Prazo</th>
                  <th class="border-b px-1.5 py-1 text-center font-semibold leading-tight" title="O dinheiro já voltou pro cliente? Sim = a plataforma devolveu e desconta da loja. Não = ainda está com a gente.">Reemb. cliente</th>
                  <th class="border-b px-1.5 py-1 text-left font-semibold">Localização</th>
                  <th class="border-b border-l border-l-slate-300 px-1.5 py-1 text-center font-semibold dark:border-l-slate-600" title="Vídeo da expedição do pedido">Envio</th>
                  <th class="border-b px-1.5 py-1 text-center font-semibold" title="Vídeo da abertura do pacote que voltou">Abertura</th>
                  <th class="border-b px-1.5 py-1 text-center font-semibold" title="Fotos da devolução">Foto</th>
                  <th class="border-b border-l border-l-slate-300 px-1.5 py-1 text-center font-semibold dark:border-l-slate-600" title="Novo, Usado ou Perdimento — e, embaixo, o SKU em que o produto entrou no estoque">Condição</th>
                  <th class="border-b px-1.5 py-1 text-center font-semibold" title="Chamado mais recente do pedido. O botãozinho abre na aba Chamados.">Chamado</th>
                  <th v-if="veCusto" class="border-b px-1.5 py-1 text-center font-semibold" title="A conta da devolução, por pedido: o que o chamado ganhou menos o custo (produto perdido ou conserto)">Reembolso</th>
                  <th v-else class="border-b px-1.5 py-1 text-center font-semibold" title="Situação do pedido no Bling">Bling</th>
                  <th class="border-b px-1.5 py-1 text-left font-semibold" title="Observação do pedido">Obs.</th>
                </tr>
              </thead>
              <tbody>
                <tr v-if="loading && !items.length">
                  <td colspan="18" class="py-8 text-center text-muted-foreground"><RefreshCw class="mr-1.5 inline size-4 animate-spin" /> carregando…</td>
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
                  <td class="truncate px-1.5 py-1.5 text-center font-mono text-[10.5px]" :title="i.pedido_marketplace || undefined">
                    <span v-if="i.pedido_marketplace" class="cursor-text select-all" @click.stop>{{ i.pedido_marketplace }}</span><span v-else class="text-muted-foreground">—</span>
                  </td>
                  <td class="px-1.5 py-1.5 text-center leading-tight" :title="i.loja || ''">
                    <div class="truncate">{{ contaDe(i) }}</div>
                    <div v-if="plataformaTab === 'todas'" class="truncate text-[10px] text-muted-foreground">{{ PLATAFORMA_NOME[plataformaKey(i.plataforma)] || i.plataforma }}</div>
                  </td>
                  <td class="truncate px-1.5 py-1.5 font-mono text-[10.5px]" :title="i.sku || ''">{{ i.sku || '—' }}</td>
                  <td class="px-1.5 py-1.5" :title="i.produto || ''">
                    <div class="line-clamp-2 break-words leading-tight">{{ i.produto || '—' }}<b v-if="i.quantidade && i.quantidade > 1"> ×{{ i.quantidade }}</b></div>
                  </td>
                  <td class="border-l border-l-slate-300 px-1.5 py-1.5 text-center leading-tight dark:border-l-slate-600">
                    <span class="inline-block w-[84px] rounded py-0.5 text-[11px] font-semibold" :class="ETAPA_COR[i.etapa]">{{ ETAPA_LABEL[i.etapa] }}</span>
                    <div v-if="i.resolvido" class="text-[10px] font-semibold text-emerald-700 dark:text-emerald-400">resolvida</div>
                    <div v-else-if="i.etapa === 'chegou' && !i.falta_lancar" class="text-[10px] text-muted-foreground">lançada</div>
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
                  <td class="px-1.5 py-1.5 leading-tight" :title="[i.localizacao, i.entrega_localizacao].filter(Boolean).join(' · ')">
                    <div v-if="i.etapa === 'fraude' && !i.localizacao" class="text-muted-foreground">não volta pacote</div>
                    <div v-else class="line-clamp-2 break-words">{{ i.localizacao || '—' }}</div>
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
                  <td class="border-l border-l-slate-300 px-1.5 py-1.5 text-center leading-tight dark:border-l-slate-600">
                    <template v-if="i.condicao_tela || i.sku_lancado">
                      <div>{{ i.condicao_tela || '—' }}<span v-if="i.lancamentos_a_mais" class="text-muted-foreground"> +{{ i.lancamentos_a_mais }}</span></div>
                      <div v-if="i.sku_lancado" class="truncate font-mono text-[10.5px] text-muted-foreground" :title="`entrou no estoque em ${i.sku_lancado}`">{{ i.sku_lancado }}</div>
                    </template>
                    <span v-else class="text-muted-foreground">—</span>
                    <div v-if="primeiraVisivel(n) && estoquePendente(i)" class="text-[10px] font-bold text-red-600 dark:text-red-400">estoque não entrou</div>
                  </td>
                  <td class="px-1.5 py-1.5 text-center">
                    <span v-if="i.chamado" class="inline-flex items-center gap-1">
                      <span class="inline-block rounded px-2 py-0.5 text-[11px] font-semibold" :class="i.tem_chamado_aberto ? 'bg-blue-100 text-blue-800 dark:bg-blue-950/50 dark:text-blue-300' : 'bg-muted text-muted-foreground'">{{ i.tem_chamado_aberto ? 'Aberto' : 'Concluído' }}</span>
                      <a :href="`/chamados?search=${encodeURIComponent(i.pedido_bling || '')}`" target="_blank" rel="noopener" class="inline-flex size-5 items-center justify-center rounded border bg-background text-muted-foreground hover:text-foreground" title="Abrir o chamado na aba Chamados" @click.stop><ExternalLink class="size-3" /></a>
                    </span>
                    <span v-else class="text-muted-foreground">—</span>
                  </td>
                  <td v-if="veCusto" class="overflow-hidden whitespace-nowrap px-1.5 py-1.5 text-center leading-tight" :title="contaDaLinha(i, n) ? `recebido ${brl(contaDaLinha(i, n)!.recebido)} − custo ${brl(contaDaLinha(i, n)!.custo_produto + contaDaLinha(i, n)!.custo_manutencao)}` : ''">
                    <template v-if="contaDaLinha(i, n)">
                      <div class="font-semibold" :class="contaDaLinha(i, n)!.resultado < 0 ? 'text-red-700 dark:text-red-400' : 'text-emerald-700 dark:text-emerald-400'">{{ sinal(contaDaLinha(i, n)!.resultado) }}</div>
                      <div class="text-[10px] text-muted-foreground">{{ contaDaLinha(i, n)!.recebido ? 'líquido' : 'só custo' }}</div>
                    </template>
                    <span v-else class="text-muted-foreground">—</span>
                  </td>
                  <td v-else class="truncate px-1.5 py-1.5 text-center text-[11px]" :title="i.situacao_bling_nome || ''">{{ i.situacao_bling_nome || '—' }}</td>
                  <td class="truncate px-1.5 py-1.5 text-muted-foreground" :title="i.observacao || ''">{{ i.observacao || '' }}</td>
                </tr>
                <tr v-if="!loading && !filtrados.length">
                  <td colspan="18" class="px-3 py-6 text-center text-muted-foreground">{{ items.length ? 'nenhuma devolução com esses filtros' : 'nenhuma devolução aqui' }}</td>
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
      <div class="flex min-w-0 flex-col rounded-md border bg-card">
        <div class="rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-center text-xs font-semibold text-white">Onde agir hoje</div>
        <div class="space-y-1.5 p-2">
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
          <div v-if="!agirItens.length" class="rounded-md border px-2.5 py-3 text-center text-xs text-muted-foreground">nada pra agir agora</div>
        </div>
      </div>
    </div>

    <!-- Janela grande -->
    <div v-if="janela && foco && pedidoItem" class="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/50 p-4" @mousedown.self="fecharJanela">
      <div class="w-full max-w-[1560px] rounded-md border bg-card shadow-2xl">
        <div class="relative flex items-center justify-center rounded-t-md bg-[#1f3a5f] px-3 py-2 text-[13px] font-semibold text-white">
          <span>Devolução · {{ pedidoItem.pedido_bling }} · {{ foco.produto || '' }}</span>
          <button type="button" class="absolute right-2 inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-normal text-slate-300 hover:text-white" @click="fecharJanela">fechar <X class="size-3.5" /></button>
        </div>
        <div class="flex flex-wrap items-center gap-2 border-b px-4 py-2 text-xs">
          <span class="inline-block w-[84px] rounded py-0.5 text-center text-[11px] font-semibold" :class="ETAPA_COR[pedidoItem.etapa]">{{ ETAPA_LABEL[pedidoItem.etapa] }}</span>
          <span class="font-semibold">{{ contaDe(pedidoItem) }} · {{ PLATAFORMA_NOME[plataformaKey(pedidoItem.plataforma)] || pedidoItem.plataforma || '—' }}</span>
          <span class="text-muted-foreground">pedido de {{ ddmmaaaa(pedidoItem.data) }}<template v-if="pedidoItem.dias_em_devolucao != null"> · em devolução há {{ pedidoItem.dias_em_devolucao }} dia(s)</template></span>
          <span class="rounded bg-muted px-2 py-0.5 text-[11px] font-semibold">Bling: {{ pedidoItem.situacao_bling_nome || '—' }}</span>
          <span v-if="pedidoItem.prazo_tipo" class="rounded px-2 py-0.5 text-[11px] font-semibold" :class="pedidoItem.prazo_estado ? 'bg-red-100 text-red-800 dark:bg-red-950/50 dark:text-red-300' : 'bg-muted'" :title="pedidoItem.prazo_dica || pedidoItem.prazo_texto || ''">prazo: {{ prazoTexto(pedidoItem) }} · {{ pedidoItem.prazo_tipo }}</span>
          <span v-if="pedidoItem.resolvido" class="rounded bg-emerald-100 px-2 py-0.5 text-[11px] font-semibold text-emerald-800 dark:bg-emerald-950/50 dark:text-emerald-300">resolvida</span>
          <template v-if="podeMover && canEdit && !pedidoItem.resolvido">
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

        <div class="grid gap-3 p-4 lg:grid-cols-[390px_minmax(0,1fr)_minmax(0,520px)]">
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
                    <Check v-if="pedidoItem.video_link" class="inline size-3.5 text-emerald-700 dark:text-emerald-400" />
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
                  </template>
                </div>
                <div>
                  <div class="mb-1 text-[11px] font-semibold">Vídeo da abertura
                    <Check v-if="aberturaDoPedido" class="inline size-3.5 text-emerald-700 dark:text-emerald-400" /><span v-else class="text-red-600 dark:text-red-400">falta</span>
                  </div>
                  <a v-if="aberturaDoPedido" :href="aberturaDoPedido" target="_blank" rel="noopener" class="flex h-[110px] items-center justify-center rounded-md border bg-muted text-[13px] text-muted-foreground hover:text-foreground">▶ abrir vídeo</a>
                  <div v-else class="flex h-[110px] items-center justify-center rounded-md border border-dashed px-3 text-center text-xs text-muted-foreground">entra no lançamento (campo Link abertura)</div>
                </div>
              </div>
            </div>
            <div class="rounded-md border p-3">
              <div class="mb-1 text-[11px] font-semibold">Fotos da devolução <span class="font-normal text-muted-foreground">({{ fotosDoPedido.length }}) · vão como evidência do chamado</span></div>
              <div v-if="fotosDoPedido.length" class="grid grid-cols-4 gap-2">
                <a v-for="f in fotosDoPedido" :key="f.id" :href="`/api/devolutions/anexos/${f.id}`" target="_blank" rel="noopener" class="block h-[92px] overflow-hidden rounded-md border bg-muted" :title="f.filename || ''">
                  <img :src="`/api/devolutions/anexos/${f.id}`" class="size-full object-cover" alt="foto da devolução" loading="lazy" />
                </a>
              </div>
              <div v-else class="rounded-md border border-dashed px-3 py-4 text-center text-xs text-muted-foreground">nenhuma foto ainda — as fotos entram no lançamento</div>
              <!-- Vídeo e PDF anexados não são foto: viram link (não baixa o arquivo pra mostrar). -->
              <div v-if="outrosAnexos.length" class="mt-2 flex flex-wrap gap-1.5">
                <a v-for="a in outrosAnexos" :key="a.id" :href="`/api/devolutions/anexos/${a.id}`" target="_blank" rel="noopener" class="inline-flex max-w-[220px] items-center gap-1 truncate rounded-md border px-2 py-1 text-[11px] font-semibold hover:bg-muted" :title="a.filename || ''"><ExternalLink class="size-3 shrink-0" /> {{ (a.content_type || '').startsWith('video/') ? 'vídeo' : 'arquivo' }} · {{ a.filename || 'anexo' }}</a>
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
              <div class="mb-2 flex items-center text-[10px] font-semibold uppercase tracking-wide text-muted-foreground"><span class="mr-1.5 inline-flex size-[18px] items-center justify-center rounded-full bg-[#1f3a5f] text-[10.5px] font-bold text-white">1</span>Lançamento <span class="ml-1 font-normal normal-case tracking-normal">· quando o pacote chega</span></div>
              <div v-if="pedidoItem.lancamentos.length" class="space-y-1.5">
                <div v-for="l in pedidoItem.lancamentos" :key="l.id" class="rounded-md border px-2.5 py-1.5 text-xs">
                  <div class="flex flex-wrap items-center gap-x-3 gap-y-0.5">
                    <span class="font-mono text-[10.5px] text-muted-foreground">{{ l.sku }}</span>
                    <span class="font-semibold">{{ l.manutencao_pendente ? 'com o técnico' : (l.condicao_tela || 'sem condição') }}</span>
                    <span v-if="l.motivo" class="text-muted-foreground">{{ l.motivo }}</span>
                    <span v-if="l.sku_lancado" class="font-mono">→ {{ l.sku_lancado }}</span>
                    <span v-if="l.estoque_status" class="font-semibold" :class="l.estoque_status === 'ok' ? 'text-emerald-700 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'">{{ ESTOQUE_TEXTO[l.estoque_status] || l.estoque_status }}<template v-if="l.estoque_status === 'ok' && l.data_devolvido_estoque"> em {{ ddmm(l.data_devolvido_estoque) }}</template></span>
                  </div>
                  <div v-if="l.tecnico || l.prazo_manutencao || l.custo_manutencao" class="text-[11px] text-muted-foreground">
                    <template v-if="l.tecnico">técnico {{ l.tecnico }}</template><template v-if="l.prazo_manutencao"> · prazo {{ ddmm(l.prazo_manutencao) }}</template><template v-if="veCusto && l.custo_manutencao"> · conserto {{ brl(l.custo_manutencao) }}</template>
                  </div>
                </div>
              </div>
              <div v-else class="rounded-md border border-dashed px-3 py-3 text-center text-xs text-muted-foreground">ainda não lançada</div>
              <div class="mt-2 flex flex-wrap items-center justify-between gap-2">
                <span class="text-[11px] text-muted-foreground">Condição na tela: Novo · Usado · Perdimento. O lançamento abre no painel de sempre (estoque, fotos, motivo).</span>
                <span class="flex flex-wrap items-center justify-end gap-1.5">
                  <a v-if="pedidoItem.lancamentos.length" :href="`/devolucoes?tab=lancamentos&search=${encodeURIComponent(pedidoItem.pedido_bling || '')}`" target="_blank" rel="noopener" class="inline-flex items-center gap-1 whitespace-nowrap rounded-md border px-2.5 py-1.5 text-xs font-semibold hover:bg-muted">abrir lançamento <ExternalLink class="size-3" /></a>
                  <a v-if="canEdit && podeLancar" :href="`/devolucoes?tab=lancamentos&lancar=${encodeURIComponent(pedidoItem.pedido_bling || '')}`" target="_blank" rel="noopener" class="inline-flex items-center gap-1 whitespace-nowrap rounded-md px-2.5 py-1.5 text-xs font-semibold" :class="pedidoItem.lancamentos.length ? 'border hover:bg-muted' : 'bg-primary text-primary-foreground'">{{ pedidoItem.lancamentos.length ? 'lançar outro produto' : 'lançar' }} <ExternalLink class="size-3" /></a>
                  <span v-else-if="canEdit && !pedidoItem.lancamentos.length" class="text-[11px] text-amber-700 dark:text-amber-400">O Bling está em {{ pedidoItem.situacao_bling_nome || 'outra situação' }}: a busca de lançamento não acha este pedido. Volte a situação pra Aguardando Devolução pra lançar.</span>
                </span>
              </div>
            </div>

            <div class="rounded-md border p-3">
              <div class="mb-2 flex items-center text-[10px] font-semibold uppercase tracking-wide text-muted-foreground"><span class="mr-1.5 inline-flex size-[18px] items-center justify-center rounded-full bg-[#1f3a5f] text-[10.5px] font-bold text-white">2</span>Chamado</div>
              <div class="flex flex-wrap items-center gap-2 text-xs">
                <template v-if="pedidoItem.chamado">
                  <span class="inline-block rounded px-2 py-0.5 text-[11px] font-semibold" :class="pedidoItem.tem_chamado_aberto ? 'bg-blue-100 text-blue-800 dark:bg-blue-950/50 dark:text-blue-300' : 'bg-muted text-muted-foreground'">{{ pedidoItem.tem_chamado_aberto ? 'Aberto' : 'Concluído' }}</span>
                  <span v-if="pedidoItem.chamado.numero" class="font-mono text-muted-foreground">{{ pedidoItem.chamado.numero }}</span>
                  <a :href="`/chamados?search=${encodeURIComponent(pedidoItem.pedido_bling || '')}`" target="_blank" rel="noopener" class="inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[11px] font-semibold hover:bg-muted"><ExternalLink class="size-3" /> abrir na aba Chamados</a>
                </template>
                <span v-else class="text-muted-foreground">sem chamado. Ele abre sozinho no lançamento, pelo motivo (Bloqueado, Golpe, Item faltando, Não recebido, Danificado).</span>
              </div>
              <div class="mt-2 text-[11px] text-muted-foreground">“Abrir chamado: Sim/Não”, a conferência do motivo e a Instrução pra IA entram na próxima etapa: mexem no robô que abre a disputa, que é tiro único na plataforma.</div>
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
                <div class="text-xs">Resolvida em <b>{{ ddmmaaaa(pedidoItem.resolvido_at) }}</b><template v-if="pedidoItem.resolvido_por_nome"> por <b>{{ pedidoItem.resolvido_por_nome }}</b></template> · {{ pedidoItem.resolvido_como }}</div>
                <button v-if="canEdit" type="button" class="mt-2 rounded-md border bg-background px-2.5 py-1.5 text-xs font-semibold hover:bg-muted disabled:opacity-50" :disabled="!!salvando" title="Volta pra lista de abertas. Não mexe no Bling." @click="reabrir">reabrir</button>
              </template>
              <template v-else>
                <div v-if="pedidoItem.pronto_resolver" class="mb-2 text-xs"><b class="text-blue-800 dark:text-blue-200">Pronto pra resolver:</b> {{ pedidoItem.pronto_resolver }}.</div>
                <div v-if="linhasDaJanela.length > 1" class="mb-2 text-[11px] text-muted-foreground">Vale pro pedido inteiro ({{ linhasDaJanela.length }} produtos): a situação do Bling é do pedido.</div>
                <div class="grid items-end gap-2 sm:grid-cols-[1fr_1fr_auto]">
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
                <div class="mt-1.5 text-[11px] text-muted-foreground">Situação atual no Bling: <b class="text-foreground">{{ pedidoItem.situacao_bling_nome || '—' }}</b>. Dá pra reabrir depois.</div>
              </template>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
