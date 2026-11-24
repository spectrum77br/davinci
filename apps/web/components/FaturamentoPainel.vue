<script setup lang="ts">
// Faturamento — pedidos faturáveis por loja. Desde 13/11/2026 (Organização do
// DaVinci, item 6) não é mais item do menu: é o botão "Faturamento" da Tabela
// de Preços (/pricing/faturamento), depois de Celular · Eletro · Mala. O link
// antigo /faturamento redireciona (pages/faturamento.vue); `embutido` só tira
// o padding de página.
//
// M-22 (24/11/2026, prévia aprovada em 23/11): a tela segue a organização das
// outras abas da Tabela de Preços. Saíram a linha de título, o Recarregar, a
// faixa cinza e o select Tipo (a COLUNA Tipo ficou). Barra igual à da aba
// Tabela de Preços: Buscar | Período ▾ | Plataforma ▾ | Contas ▾ | Equipes ▾ |
// Atualizar, e "N contas" à direita. Tabela no padrão da aba Contas, com o
// cabeçalho e a linha Total fixos. Colunas novas: Pendências (os cartões
// vermelhos da conta na Tabela de Preços — a MESMA regra da grade, em
// lib/cartaoVermelho.ts), Investimento (gasto de Ads guardado por dia; "—" sem
// dado), % Investimento e Margem (só ADM: a API nem manda o campo aos outros).
// As regras de quais pedidos entram e de quem vê quais lojas são as do
// servidor e não mudaram; Plataforma/Contas/Equipes, a busca e o Total filtram
// na tela o que o servidor já mandou.
//
// M-24 (24/11/2026): o "?" da barra do topo (M-18) abre a "Ajuda · Faturamento".
//
// M-36 (24/11/2026, prévia aprovada em 24/11): Investimento = afiliados + Ads,
// do Marketing (Shopee: as semanas fechadas da Conferência; ML: o Ads por dia).
// O servidor manda os dois pedaços; o balão mostra "Afiliados R$ X + Ads R$ Y"
// e, quando é o caso, até quando vão as semanas fechadas. "—" = sem dado.
//
// M-37 (24/11/2026, prévia aprovada em 24/11): a Margem (só ADM) deixou de ser
// a faixa da aba Contas e virou a margem real da Conciliação, (Σ sobrou de
// todos os pedidos − Ads) ÷ Σ custo, com 1 casa e negativa em vermelho. O
// balão mostra a conta e quantos pedidos entraram de cada jeito; o Total faz
// a mesma conta com as somas das contas filtradas. "—" = conta fora da
// Conciliação ou período que começa antes do início dela.
import { computed, onMounted, ref } from 'vue'
import { ChevronDown, ChevronLeft, ChevronRight, Loader2, RefreshCw, Search } from 'lucide-vue-next'
import { PopoverContent, PopoverPortal, PopoverRoot, PopoverTrigger } from 'reka-ui'
import { isoToday } from '~/lib/date'
import { ORDEM_CATEGORIAS, ROTULO_CATEGORIA, ordenarCategorias } from '~/lib/categorias'
import { cartoesPorConta, type ConferenciaCartao, type GradeCartao } from '~/lib/cartaoVermelho'
import { MARKETPLACE_LABELS, type Marketplace } from '~/composables/useMarketplaces'

const props = defineProps<{ embutido?: boolean }>()
const embutido = computed(() => !!props.embutido)

const { api } = useApi()
// O mesmo critério de administrador da Tabela de Preços (role === 'admin').
const isAdmin = useIsAdmin()
// Pendências vêm da grade da Tabela de Preços: quem não a vê fica com "—".
const podeVerPrecos = useCan('tabela_precos', 'view')

// M-37: a margem real da Conciliação (regras no servidor, services/
// faturamento_margem.py). `valor` em fração; null = "—" (sem custo para
// dividir). `antes_do_inicio`: o período começa antes de `inicio` → "—".
type MargemReal = {
  valor: number | null
  sobrou: number
  ads: number
  ads_com_dado?: boolean
  ads_ate?: string | null
  custo: number
  pedidos: number
  pagos: number
  previstos: number
  cancelados: number
  sem_valor: number
  inicio?: string | null
  antes_do_inicio?: boolean
}
type FaturamentoLinha = {
  store_id: string
  loja: string | null
  tipo: string | null
  departments: string[]
  pedidos: number
  faturamento: number
  ticket_medio: number
  // M-22
  equipe?: number | null
  contas_preco?: string[]
  investimento?: number | null
  // M-36: os dois pedaços do investimento (null = aquele pedaço sem dado) e
  // até quando vão as semanas fechadas da Conferência, se param antes do fim.
  investimento_afiliados?: number | null
  investimento_ads?: number | null
  investimento_desde?: string | null
  investimento_parcial?: boolean
  investimento_ate?: string | null
  // Só vem para administrador.
  margem?: MargemReal | null
}
type EquipeOpcao = { numero: number; nome: string }
type FaturamentoOut = {
  itens: FaturamentoLinha[]
  total_pedidos: number
  total_faturamento: number
  start: string
  end: string
  teams: number[]
  equipes?: EquipeOpcao[]
  team: number | null
}

// ---------------------------------------------------------------- Ajuda (M-24)
// O texto do pedido (prévia aprovada em 23/11/2026). "Margem (só ADM)" só
// aparece para administrador.
function ajudaFaturamento(admin: boolean) {
  return {
    titulo: 'Faturamento',
    conteudo: [
      {
        titulo: 'Para que serve',
        itens: [
          'Mostra quanto cada conta vendeu no período: pedidos, faturamento e ticket médio, junto com as pendências da Tabela de Preços e o investimento (afiliados + Ads, do Marketing).',
        ],
      },
      {
        titulo: 'Como ler as colunas',
        itens: [
          { termo: 'Conta, Plataforma, Equipe e Tipo', texto: 'a loja, onde ela vende, a equipe e as categorias que ela vende.' },
          { termo: 'Pedidos', texto: 'pedidos Em aberto, Em andamento e Entregues. Cancelados e devolvidos não entram. Cada pedido conta uma vez.' },
          { termo: 'Faturamento', texto: 'soma do total dos pedidos, como está no Bling. É o valor bruto: não desconta comissão nem reembolso.' },
          { termo: 'Ticket médio', texto: 'faturamento ÷ pedidos.' },
          { termo: 'Pendências', cartao: true, texto: 'quantos cartões vermelhos a conta tem na Tabela de Preços (Celular, Eletro e Mala).' },
          { termo: 'Investimento', texto: 'afiliados + Ads, do Marketing, na conta no período. Shopee: as semanas fechadas da Conferência (a semana que cruza o período entra pelos dias; a semana atual entra quando fechar). Mercado Livre: o Ads de cada dia. Passe o mouse para ver as duas partes. "—" quer dizer sem dado.' },
          { termo: '% Investimento', texto: 'investimento ÷ faturamento da conta. Na linha Total, a soma dos investimentos ÷ o faturamento total.' },
          ...(admin
            ? [{ termo: 'Margem (só ADM)', texto: 'resultado das vendas no período, da Conciliação: (sobrou de todos os pedidos − Ads) ÷ custo dos produtos. Pedido ainda não pago entra pelo valor previsto; pedido sem valor de venda fica fora até a plataforma calcular a venda. Só o Ads é descontado: os afiliados já saem do valor de cada pedido. Negativa em vermelho. "—" quer dizer conta fora da Conciliação, sem pedido no período (fica fora do Total, com o Ads dela) ou período que começa antes do início dela (24/10/2026). Passe o mouse para ver a conta.' }]
            : []),
        ],
      },
      {
        titulo: 'Barra',
        itens: [
          { termo: 'Buscar', texto: 'pelo nome da conta.' },
          { termo: 'Período', texto: 'por mês ou personalizado (de/até). Vale a data da venda.' },
          { termo: 'Plataforma, Contas e Equipes', texto: 'marque o que quer ver. Quem tem equipe só vê as contas dela.' },
          { termo: 'Atualizar', texto: 'relê os números. Os pedidos chegam sozinhos do Bling; o botão não busca pedido novo.' },
        ],
      },
      {
        titulo: 'Bom saber',
        itens: [
          'Pedido com etiqueta emitida sai da conta e volta quando fica Em andamento ou Entregue. O total do mês pode cair e subir de novo.',
          'A linha Total soma só o que está filtrado.',
        ],
      },
    ],
  }
}
const ajuda = computed(() => ajudaFaturamento(isAdmin.value))
useAjuda(ajuda)

// ---------------------------------------------------------------- Período ▾
// Uma caixa por vez: "Por mês" (padrão = mês atual) ou "Personalizado"
// (de / até). Mudou → relê.
type Mode = 'month' | 'custom'
const mode = ref<Mode>('month')
const month = ref<string>(isoToday().slice(0, 7)) // YYYY-MM
const maxMonth = isoToday().slice(0, 7) // trava a navegação no futuro
const customStart = ref<string>(`${isoToday().slice(0, 7)}-01`)
const customEnd = ref<string>(isoToday())
const MESES = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez']

function mesCurto(ym: string): string {
  const [y, m] = ym.split('-').map(Number)
  return `${MESES[m - 1]}/${y}`
}
function dataCurta(iso: string): string {
  const [y, m, d] = iso.split('-')
  return `${d}/${m}/${y}`
}
const periodoInvalido = computed(() => mode.value === 'custom'
  && (!customStart.value || !customEnd.value || customStart.value > customEnd.value))
// O período escolhido agora na caixa…
const rotuloEscolhido = computed(() => mode.value === 'month'
  ? `Período: ${mesCurto(month.value)}`
  : `Período: ${customStart.value ? dataCurta(customStart.value) : '…'} – ${customEnd.value ? dataCurta(customEnd.value) : '…'}`)
// …e o botão mostra o da última leitura (o que está na tabela): com um
// personalizado inválido a tela não relê, e o botão não pode mostrar um
// período que a tabela não tem (revisão de 24/11/2026).
const rotuloCarregado = ref<string | null>(null)
const rotuloPeriodo = computed(() => rotuloCarregado.value ?? rotuloEscolhido.value)

function shiftMonth(delta: number) {
  const [y, m] = month.value.split('-').map(Number)
  const d = new Date(y, m - 1 + delta, 1)
  const ym = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
  if (ym > maxMonth) return
  month.value = ym
  mode.value = 'month'
  void load()
}
// A caixa marcada não desmarca sozinha: sempre há um modo escolhido.
function escolherModo(m: Mode, ev?: Event) {
  if (ev?.target) (ev.target as HTMLInputElement).checked = true
  if (mode.value === m) return
  mode.value = m
  if (m === 'month' || !periodoInvalido.value) void load()
}
function aplicarPersonalizado() {
  mode.value = 'custom'
  if (!periodoInvalido.value) void load()
}

function periodoParams(): { start: string; end: string } {
  if (mode.value === 'custom') {
    return { start: `${customStart.value}T00:00:00`, end: `${customEnd.value}T23:59:59` }
  }
  // Mês: [dia 1 00:00, dia 1 do mês seguinte 00:00). Backend filtra data < end.
  const [y, m] = month.value.split('-').map(Number)
  const ny = m === 12 ? y + 1 : y
  const nm = m === 12 ? 1 : m + 1
  return {
    start: `${month.value}-01T00:00:00`,
    end: `${ny}-${String(nm).padStart(2, '0')}-01T00:00:00`,
  }
}

// ---------------------------------------------------------------- dados
const loading = ref(false)
const error = ref<string | null>(null)
const data = ref<FaturamentoOut | null>(null)
// A última resposta boa (17/11/2026): um erro limpa a tabela (data = null),
// mas os filtros continuam com as opções dela.
const ultimaBoa = ref<FaturamentoOut | null>(null)
let seqLoad = 0

async function load() {
  const seq = ++seqLoad
  loading.value = true
  error.value = null
  rotuloCarregado.value = rotuloEscolhido.value
  try {
    const { start, end } = periodoParams()
    const qs = new URLSearchParams({ start, end })
    const res = await api<FaturamentoOut>(`/api/faturamento?${qs.toString()}`)
    if (seq !== seqLoad) return
    data.value = res
    ultimaBoa.value = res
  } catch (e: any) {
    if (seq !== seqLoad) return
    // Sem isto a tabela do filtro anterior ficava embaixo do erro, como se
    // fosse a resposta do filtro novo.
    data.value = null
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    if (seq === seqLoad) loading.value = false
  }
}

// ---------------------------------------------------------------- Pendências
// Os cartões vermelhos de cada conta da Tabela de Preços, nas três categorias.
// Carregadas UMA vez (ao abrir e no Atualizar), não por conta: a grade e a
// conferência de Celular, Eletro e Mala; a contagem é a da grade
// (cartoesPorConta, lib/cartaoVermelho.ts). Só a contagem fica guardada.
type EstadoPendencias = 'carregando' | 'ok' | 'erro' | 'sem_acesso'
const pendenciasEstado = ref<EstadoPendencias>('carregando')
const cartoesDaConta = ref<Map<string, number>>(new Map())
let seqPendencias = 0

async function carregarPendencias() {
  const seq = ++seqPendencias
  if (!podeVerPrecos.value) {
    pendenciasEstado.value = 'sem_acesso'
    cartoesDaConta.value = new Map()
    return
  }
  pendenciasEstado.value = 'carregando'
  try {
    const porCategoria = await Promise.all(ORDEM_CATEGORIAS.map(async (dep) => {
      const q = encodeURIComponent(dep)
      const [grade, conferencia] = await Promise.all([
        api<GradeCartao>(`/api/pricing/grid?department=${q}`),
        api<ConferenciaCartao>(`/api/pricing/conferencia?department=${q}`),
      ])
      return cartoesPorConta(grade, conferencia, dep)
    }))
    if (seq !== seqPendencias) return
    const todas = new Map<string, number>()
    for (const mapa of porCategoria) {
      for (const [conta, n] of mapa) todas.set(conta, (todas.get(conta) ?? 0) + n)
    }
    cartoesDaConta.value = todas
    pendenciasEstado.value = 'ok'
  } catch {
    if (seq !== seqPendencias) return
    cartoesDaConta.value = new Map()
    pendenciasEstado.value = 'erro'
  }
}

// null = a loja não tem conta na Tabela de Preços (nenhuma das contas dela
// aparece na grade de Celular, Eletro ou Mala) → "—".
function pendenciasDe(r: FaturamentoLinha): number | null {
  const contas = (r.contas_preco ?? []).filter((id) => cartoesDaConta.value.has(id))
  if (!contas.length) return null
  return contas.reduce((soma, id) => soma + (cartoesDaConta.value.get(id) ?? 0), 0)
}

async function atualizar() {
  await Promise.all([load(), carregarPendencias()])
}

await load()
onMounted(() => { void carregarPendencias() })

// ---------------------------------------------------------------- rótulos
// Tipo da loja (chips): Celular, Eletro, Mala (M-23, lib/categorias.ts) e o
// que não é categoria depois. Os rótulos antigos ficam só para a etiqueta.
const ROTULOS_TIPO_ANTIGO: Record<string, string> = { catalogo: 'Catálogo', shein: 'Shein', sem: 'Sem categoria' }
function departmentLabel(slug: string): string {
  return (ROTULO_CATEGORIA as Record<string, string>)[slug] ?? ROTULOS_TIPO_ANTIGO[slug] ?? slug
}
function tiposDaLoja(r: FaturamentoLinha): string[] {
  return ordenarCategorias(r.departments ?? [])
}
// A chave do filtro Plataforma: "mercadolivre" e "ml" são a mesma; loja sem
// cadastro (sem plataforma) = 'sem'.
const SEM_PLATAFORMA = 'sem'
function chavePlataforma(tipo: string | null): string {
  if (!tipo) return SEM_PLATAFORMA
  return tipo === 'mercadolivre' ? 'ml' : tipo
}
function platformLabel(value: string | null): string {
  if (!value) return '—'
  const platform = value === 'mercadolivre' ? 'ml' : value
  return MARKETPLACE_LABELS[platform as Marketplace] ?? value
}
// Só o número da equipe, como na aba Contas: 101/102/103 → 1/2/3.
const NUMERO_DA_EQUIPE: Record<number, string> = { 101: '1', 102: '2', 103: '3' }
function numeroEquipe(n: number | null | undefined): string {
  if (n == null) return '—'
  return NUMERO_DA_EQUIPE[n] ?? String(n)
}
function chaveConta(r: FaturamentoLinha): string {
  return (r.loja ?? '').trim().toLocaleLowerCase('pt-BR')
}

// ---------------------------------------------------------------- filtros
// Como na aba Tabela de Preços (M-8/M-26): guardamos o que está DESMARCADO —
// vazio = tudo marcado, e opção nova já aparece marcada. Recarregar a página
// volta tudo marcado.
const busca = ref('')
const buscaContas = ref('')
const plataformasDesmarcadas = ref<Set<string>>(new Set())
const contasDesmarcadas = ref<Set<string>>(new Set())
const equipesDesmarcadas = ref<Set<string>>(new Set())

const linhasDaResposta = computed<FaturamentoLinha[]>(() => data.value?.itens ?? [])
const respostaDasOpcoes = computed(() => data.value ?? ultimaBoa.value)

const plataformaOptions = computed(() => {
  const vistas = new Map<string, string>()
  for (const r of respostaDasOpcoes.value?.itens ?? []) {
    const k = chavePlataforma(r.tipo)
    if (!vistas.has(k)) vistas.set(k, r.tipo ? platformLabel(r.tipo) : 'Sem plataforma')
  }
  // Em ordem alfabética; "Sem plataforma" (as lojas sem cadastro) no fim.
  const ultima = (v: string) => (v === SEM_PLATAFORMA ? 1 : 0)
  return Array.from(vistas, ([value, label]) => ({ value, label }))
    .sort((a, b) => ultima(a.value) - ultima(b.value) || a.label.localeCompare(b.label, 'pt-BR'))
})
const contaOptions = computed(() => {
  const porChave = new Map<string, string>()
  for (const r of respostaDasOpcoes.value?.itens ?? []) {
    const k = chaveConta(r)
    if (!porChave.has(k)) porChave.set(k, (r.loja ?? '—').trim())
  }
  return Array.from(porChave, ([value, label]) => ({ value, label }))
    .sort((a, b) => a.label.localeCompare(b.label, 'pt-BR', { sensitivity: 'base' }))
})
const contaOptionsBuscadas = computed(() => {
  const q = buscaContas.value.trim().toLocaleLowerCase('pt-BR')
  return q ? contaOptions.value.filter((o) => o.value.includes(q)) : contaOptions.value
})
// Só as equipes que o servidor deixa esta pessoa ver (a regra de sempre).
const equipeOptions = computed(() => [...(respostaDasOpcoes.value?.teams ?? [])]
  .sort((a, b) => a - b)
  .map((n) => ({ value: String(n), label: numeroEquipe(n) })))

function rotuloParcial(nome: string, opcoes: { value: string }[], desmarcadas: Set<string>): string {
  const marcadas = opcoes.filter((o) => !desmarcadas.has(o.value)).length
  return marcadas < opcoes.length ? `${nome} (${marcadas})` : nome
}
const rotuloPlataforma = computed(() => rotuloParcial('Plataforma', plataformaOptions.value, plataformasDesmarcadas.value))
const rotuloContas = computed(() => rotuloParcial('Contas', contaOptions.value, contasDesmarcadas.value))
const rotuloEquipes = computed(() => rotuloParcial('Equipes', equipeOptions.value, equipesDesmarcadas.value))

type Filtro = 'plataforma' | 'conta' | 'equipe'
function conjunto(f: Filtro) {
  return f === 'plataforma' ? plataformasDesmarcadas : f === 'conta' ? contasDesmarcadas : equipesDesmarcadas
}
function alternarFiltro(f: Filtro, valor: string, marcado: boolean) {
  const alvo = conjunto(f)
  const novo = new Set(alvo.value)
  if (marcado) novo.delete(valor)
  else novo.add(valor)
  alvo.value = novo
}
function marcarTodas(f: Filtro, marcar: boolean) {
  if (f === 'conta') {
    // Como o marcarContas da Tabela: valem para a lista que está à vista (com
    // busca, só as encontradas); as outras ficam como estavam.
    const novo = new Set(contasDesmarcadas.value)
    for (const o of contaOptionsBuscadas.value) {
      if (marcar) novo.delete(o.value)
      else novo.add(o.value)
    }
    contasDesmarcadas.value = novo
    return
  }
  const opcoes = f === 'plataforma' ? plataformaOptions.value : equipeOptions.value
  conjunto(f).value = marcar ? new Set() : new Set(opcoes.map((o) => o.value))
}

// A linha aparece passando na busca e nos três filtros. Equipes com tudo
// marcado não filtra (aparece até loja sem equipe); com alguma desmarcada, a
// loja precisa ser de uma equipe marcada.
const linhasFiltradas = computed(() => {
  const q = busca.value.trim().toLocaleLowerCase('pt-BR')
  const plats = plataformasDesmarcadas.value
  const contas = contasDesmarcadas.value
  const equipes = equipesDesmarcadas.value
  return linhasDaResposta.value.filter((r) => {
    if (q && !chaveConta(r).includes(q)) return false
    if (plats.has(chavePlataforma(r.tipo))) return false
    if (contas.has(chaveConta(r))) return false
    if (equipes.size && (r.equipe == null || equipes.has(String(r.equipe)))) return false
    return true
  })
})

// ---------------------------------------------------------------- Total
const total = computed(() => {
  const linhas = linhasFiltradas.value
  let pedidos = 0
  let faturamento = 0
  let investimento: number | null = null
  let afiliados: number | null = null
  let ads: number | null = null
  let parcial = false
  let ate = false
  let pendencias: number | null = null
  // Margem (M-37): (Σ sobrou − Σ Ads) ÷ Σ custo das contas com Conciliação.
  // A conta com o período antes do início dela fica fora (e o balão conta).
  // A conta sem pedido no período também fica fora, com o Ads dela: é o que
  // dá os 24,0% de nov/2026 do exemplo aprovado (Ads R$ 35.011,50).
  let margem: MargemReal | null = null
  let margemAntes = 0
  let margemSemPedido = 0
  let margemInicio: string | null = null
  for (const r of linhas) {
    pedidos += r.pedidos
    faturamento += r.faturamento
    if (r.investimento != null) {
      investimento = (investimento ?? 0) + r.investimento
      if (r.investimento_afiliados != null) afiliados = (afiliados ?? 0) + r.investimento_afiliados
      if (r.investimento_ads != null) ads = (ads ?? 0) + r.investimento_ads
      parcial ||= !!r.investimento_parcial
      ate ||= !!r.investimento_ate
    }
    const p = pendenciasDe(r)
    if (p != null) pendencias = (pendencias ?? 0) + p
    const m = r.margem
    if (m?.antes_do_inicio) {
      margemAntes += 1
      if (m.inicio && (!margemInicio || m.inicio > margemInicio)) margemInicio = m.inicio
    } else if (m && m.pedidos === 0) {
      margemSemPedido += 1
    } else if (m) {
      margem ??= { valor: null, sobrou: 0, ads: 0, ads_com_dado: true, ads_ate: null, custo: 0, pedidos: 0, pagos: 0, previstos: 0, cancelados: 0, sem_valor: 0 }
      margem.sobrou += m.sobrou
      margem.ads += m.ads
      margem.custo += m.custo
      margem.pedidos += m.pedidos
      margem.pagos += m.pagos
      margem.previstos += m.previstos
      margem.cancelados += m.cancelados
      margem.sem_valor += m.sem_valor
      if (!m.ads_com_dado) margem.ads_com_dado = false
      if (m.ads_ate && (!margem.ads_ate || m.ads_ate < margem.ads_ate)) margem.ads_ate = m.ads_ate
    }
  }
  if (margem) {
    margem.sobrou = Math.round(margem.sobrou * 100) / 100
    margem.ads = Math.round(margem.ads * 100) / 100
    margem.custo = Math.round(margem.custo * 100) / 100
    margem.valor = margem.custo > 0 ? (margem.sobrou - margem.ads) / margem.custo : null
  }
  return {
    pedidos,
    faturamento: Math.round(faturamento * 100) / 100,
    ticket: pedidos ? faturamento / pedidos : null,
    investimento: investimento == null ? null : Math.round(investimento * 100) / 100,
    afiliados: afiliados == null ? null : Math.round(afiliados * 100) / 100,
    ads: ads == null ? null : Math.round(ads * 100) / 100,
    parcial,
    ate,
    pendencias,
    margem,
    margemAntes,
    margemSemPedido,
    margemInicio,
  }
})

// ---------------------------------------------------------------- formatos
function fmtBRL(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '—'
  return n.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
}
function fmtInt(n: number | null | undefined): string {
  if (n == null) return '—'
  return Number(n).toLocaleString('pt-BR')
}
// % Investimento = investimento ÷ faturamento, 2 casas. Sem investimento (ou
// sem faturamento para dividir) → "—".
function fmtPctInvestimento(inv: number | null | undefined, fat: number): string {
  if (inv == null || !(fat > 0)) return '—'
  return `${((inv / fat) * 100).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%`
}
// Margem (M-37): percentual com 1 casa ("25,2%"); o valor que conta na tela
// já arredondado, para "-0,0%" não aparecer nem ficar vermelho à toa.
function pctMargem(m: MargemReal | null | undefined): number | null {
  if (!m || m.antes_do_inicio || m.valor == null || !Number.isFinite(m.valor)) return null
  const pct = Math.round(m.valor * 1000) / 10
  return pct === 0 ? 0 : pct
}
function fmtMargem(m: MargemReal | null | undefined): string {
  const pct = pctMargem(m)
  if (pct == null) return '—'
  return `${pct.toLocaleString('pt-BR', { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%`
}
function classeMargem(m: MargemReal | null | undefined): string {
  const pct = pctMargem(m)
  if (pct == null) return 'text-muted-foreground'
  return pct < 0 ? 'text-red-600' : ''
}
function dataDM(iso: string | null | undefined): string {
  if (!iso) return ''
  const [, m, d] = iso.split('-')
  return `${d}/${m}`
}
function dataDMA(iso: string | null | undefined): string {
  if (!iso) return ''
  const [a, m, d] = iso.split('-')
  return `${d}/${m}/${a}`
}
// O balão da Margem: a conta do pedido ("Sobrou R$ X − Ads R$ Y ÷ custo R$ Z ·
// N pedidos: A pagos, B com valor previsto, C cancelados, D sem valor de
// venda") e, embaixo, o que explica um "—" ou o Ads.
function contaMargem(m: MargemReal): string[] {
  const linhas = [
    `Sobrou ${fmtBRL(m.sobrou)} − Ads ${fmtBRL(m.ads)} ÷ custo ${fmtBRL(m.custo)}`
      + ` · ${fmtInt(m.pedidos)} pedidos: ${fmtInt(m.pagos)} pagos, ${fmtInt(m.previstos)} com valor previsto,`
      + ` ${fmtInt(m.cancelados)} cancelados, ${fmtInt(m.sem_valor)} sem valor de venda`,
  ]
  if (m.valor == null) linhas.push(m.pedidos ? 'sem custo para dividir' : 'nenhum pedido na Conciliação no período')
  return linhas
}
function tituloMargem(m: MargemReal | null | undefined): string {
  if (!m) return 'Conta fora da Conciliação.'
  if (m.antes_do_inicio) return `A Conciliação começa em ${dataDMA(m.inicio)}. Escolha um período a partir dessa data.`
  const linhas = contaMargem(m)
  if (!m.ads_com_dado) linhas.push('sem dado de Ads no Marketing: conta R$ 0,00')
  if (m.ads_ate) linhas.push(`Ads das semanas fechadas até ${dataDM(m.ads_ate)} (a semana atual entra quando fechar)`)
  return linhas.join('\n')
}
const tituloMargemTotal = computed(() => {
  const t = total.value
  const fora = t.margemAntes
    ? `${t.margemAntes} conta(s) fora: a Conciliação começa em ${dataDMA(t.margemInicio)}.`
    : null
  const semPedido = t.margemSemPedido
    ? `${t.margemSemPedido} conta(s) sem pedido no período ficam fora (o Ads delas também).`
    : null
  if (!t.margem) return [fora, semPedido].filter(Boolean).join('\n') || 'Nenhuma conta filtrada está na Conciliação.'
  const linhas = contaMargem(t.margem)
  if (!t.margem.ads_com_dado) linhas.push('Conta sem dado de Ads no Marketing entra com R$ 0,00.')
  if (t.margem.ads_ate) linhas.push('Alguma conta só tem o Ads das semanas fechadas da Conferência (a semana atual entra quando fechar).')
  if (fora) linhas.push(fora)
  if (semPedido) linhas.push(semPedido)
  return linhas.join('\n')
})
// M-36: o balão do Investimento — "Afiliados R$ X + Ads R$ Y" (pedaço sem
// dado = "—") e, embaixo, desde quando há dado (período começa antes) e até
// quando vão as semanas fechadas da Conferência.
function partesInvestimento(afiliados: number | null | undefined, ads: number | null | undefined): string {
  return `Afiliados ${fmtBRL(afiliados)} + Ads ${fmtBRL(ads)}`
}
function tituloInvestimento(r: FaturamentoLinha): string {
  if (r.investimento == null) return 'Sem dado de afiliados nem de Ads no Marketing para esta conta no período.'
  const linhas = [partesInvestimento(r.investimento_afiliados, r.investimento_ads)]
  if (r.investimento_parcial) linhas.push(`dado guardado desde ${dataDM(r.investimento_desde)}`)
  if (r.investimento_ate) linhas.push(`semanas fechadas até ${dataDM(r.investimento_ate)} (a semana atual entra quando fechar)`)
  return linhas.join('\n')
}
const tituloInvestimentoTotal = computed(() => {
  const t = total.value
  if (t.investimento == null) return 'Sem dado de afiliados nem de Ads no Marketing nas contas filtradas.'
  const linhas = [partesInvestimento(t.afiliados, t.ads)]
  if (t.parcial) linhas.push('Alguma conta só tem dado depois do início do período.')
  if (t.ate) linhas.push('Alguma conta só tem as semanas fechadas da Conferência (a semana atual entra quando fechar).')
  return linhas.join('\n')
})
function tituloPendencias(p: number | null): string {
  if (pendenciasEstado.value === 'sem_acesso') return 'Sem acesso à Tabela de Preços.'
  if (pendenciasEstado.value === 'erro') return 'As pendências não carregaram — clique em Atualizar.'
  if (pendenciasEstado.value === 'carregando') return 'carregando…'
  if (p == null) return 'A loja não tem conta na Tabela de Preços.'
  return `${p} cartão(ões) vermelho(s) na Tabela de Preços (Celular, Eletro e Mala).`
}
function textoPendencias(p: number | null): string {
  if (pendenciasEstado.value === 'carregando') return '…'
  if (pendenciasEstado.value !== 'ok' || p == null) return '—'
  return fmtInt(p)
}

const colunas = computed(() => (isAdmin.value ? 11 : 10))
</script>

<template>
  <div class="faturamento-page min-w-0 space-y-3" :class="{ 'p-4': !embutido }">
    <!-- Barra (M-22): a mesma da aba Tabela de Preços — campo e botões de 28px (h-7). -->
    <div class="flex flex-wrap items-center gap-2" data-faturamento-barra>
      <div class="relative flex-1 min-w-[200px] max-w-sm">
        <Search class="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
        <input
          v-model="busca"
          placeholder="Buscar por nome..."
          aria-label="Buscar por nome da conta"
          class="h-7 border rounded pl-7 pr-2 py-0 text-sm bg-background w-full"
          data-campo="faturamento-busca"
        />
      </div>
      <!-- Período ▾: "Por mês" (‹ mês ›) ou "Personalizado" (de / até), uma caixa por vez. -->
      <PopoverRoot>
        <PopoverTrigger as-child>
          <button type="button" class="btn btn-sm h-7 py-0" data-filtro="periodo" title="Escolher o período">
            {{ rotuloPeriodo }}
            <ChevronDown class="h-3 w-3 ml-1" />
          </button>
        </PopoverTrigger>
        <PopoverPortal>
          <PopoverContent
            side="bottom"
            align="start"
            :side-offset="4"
            :collision-padding="8"
            data-menu-periodo
            class="z-[70] w-72 max-w-[calc(100vw-16px)] rounded-md border bg-background p-2 shadow-lg"
          >
            <label class="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm hover:bg-muted/50">
              <input type="checkbox" class="size-4" :checked="mode === 'month'" data-campo="periodo-mes" @change="escolherModo('month', $event)" />
              Por mês
            </label>
            <div class="flex items-center gap-1 pb-1 pl-7" :class="{ 'opacity-50': mode !== 'month' }">
              <button type="button" class="grid h-7 w-7 place-items-center rounded border hover:bg-muted" aria-label="Mês anterior" data-acao="mes-anterior" @click="shiftMonth(-1)">
                <ChevronLeft class="h-4 w-4" />
              </button>
              <span class="min-w-[84px] text-center text-sm font-medium" data-mes>{{ mesCurto(month) }}</span>
              <button type="button" class="grid h-7 w-7 place-items-center rounded border hover:bg-muted disabled:opacity-40" aria-label="Próximo mês" data-acao="mes-seguinte" :disabled="month >= maxMonth" @click="shiftMonth(1)">
                <ChevronRight class="h-4 w-4" />
              </button>
            </div>
            <label class="flex cursor-pointer items-center gap-2 rounded border-t px-1 py-1 pt-2 text-sm hover:bg-muted/50">
              <input type="checkbox" class="size-4" :checked="mode === 'custom'" data-campo="periodo-personalizado" @change="escolherModo('custom', $event)" />
              Personalizado
            </label>
            <div class="flex flex-wrap items-center gap-1 pl-7 text-xs" :class="{ 'opacity-50': mode !== 'custom' }">
              <span class="text-muted-foreground">de</span>
              <input v-model="customStart" type="date" aria-label="Data inicial" class="h-7 border rounded px-1 bg-background text-xs" data-campo="periodo-de" @change="aplicarPersonalizado" />
              <span class="text-muted-foreground">até</span>
              <input v-model="customEnd" type="date" aria-label="Data final" class="h-7 border rounded px-1 bg-background text-xs" data-campo="periodo-ate" @change="aplicarPersonalizado" />
            </div>
            <p v-if="periodoInvalido" class="px-1 pt-1 text-[11px] text-destructive">A data inicial precisa ser antes da final.</p>
            <p class="mt-1 border-t px-1 pt-1 text-[11px] text-muted-foreground">Vale a data da venda. Esc ou clicar fora fecha.</p>
          </PopoverContent>
        </PopoverPortal>
      </PopoverRoot>
      <!-- Plataforma ▾: as plataformas presentes. -->
      <PopoverRoot>
        <PopoverTrigger as-child>
          <button type="button" class="btn btn-sm h-7 py-0" data-filtro="plataforma" title="Escolher as plataformas">
            {{ rotuloPlataforma }}
            <ChevronDown class="h-3 w-3 ml-1" />
          </button>
        </PopoverTrigger>
        <PopoverPortal>
          <PopoverContent
            side="bottom"
            align="start"
            :side-offset="4"
            :collision-padding="8"
            data-menu-plataforma
            class="z-[70] w-64 max-w-[calc(100vw-16px)] rounded-md border bg-background p-2 shadow-lg"
          >
            <div class="flex items-center gap-2 px-1 pb-1 text-xs">
              <button type="button" class="text-blue-700 hover:underline dark:text-blue-300" data-acao="plataforma-marcar-todas" @click="marcarTodas('plataforma', true)">Marcar todas</button>
              <span class="text-muted-foreground">·</span>
              <button type="button" class="text-blue-700 hover:underline dark:text-blue-300" data-acao="plataforma-limpar" @click="marcarTodas('plataforma', false)">Limpar</button>
            </div>
            <div class="max-h-72 overflow-y-auto border-t pt-1">
              <label
                v-for="o in plataformaOptions"
                :key="o.value"
                class="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm hover:bg-muted/50"
              >
                <input
                  type="checkbox"
                  class="size-4"
                  :checked="!plataformasDesmarcadas.has(o.value)"
                  :data-plataforma="o.value"
                  @change="alternarFiltro('plataforma', o.value, ($event.target as HTMLInputElement).checked)"
                />
                {{ o.label }}
              </label>
              <p v-if="!plataformaOptions.length" class="px-1 py-1 text-[11px] text-muted-foreground">Nenhuma conta no período.</p>
            </div>
          </PopoverContent>
        </PopoverPortal>
      </PopoverRoot>
      <!-- Contas ▾: por nome da loja, com busca. -->
      <PopoverRoot>
        <PopoverTrigger as-child>
          <button type="button" class="btn btn-sm h-7 py-0" data-filtro="contas" title="Escolher as contas">
            {{ rotuloContas }}
            <ChevronDown class="h-3 w-3 ml-1" />
          </button>
        </PopoverTrigger>
        <PopoverPortal>
          <PopoverContent
            side="bottom"
            align="start"
            :side-offset="4"
            :collision-padding="8"
            data-menu-contas
            class="z-[70] w-64 max-w-[calc(100vw-16px)] rounded-md border bg-background p-2 shadow-lg"
          >
            <div class="relative px-1 pb-1">
              <Search class="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
              <input
                v-model="buscaContas"
                placeholder="Buscar conta…"
                class="h-7 w-full border rounded pl-7 pr-2 text-sm bg-background"
                data-campo="contas-busca"
              />
            </div>
            <div class="flex items-center gap-2 px-1 pb-1 text-xs">
              <button type="button" class="text-blue-700 hover:underline dark:text-blue-300" data-acao="contas-marcar-todas" @click="marcarTodas('conta', true)">Marcar todas</button>
              <span class="text-muted-foreground">·</span>
              <button type="button" class="text-blue-700 hover:underline dark:text-blue-300" data-acao="contas-limpar" @click="marcarTodas('conta', false)">Limpar</button>
            </div>
            <div class="max-h-72 overflow-y-auto border-t pt-1">
              <label
                v-for="o in contaOptionsBuscadas"
                :key="o.value"
                class="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm hover:bg-muted/50"
              >
                <input
                  type="checkbox"
                  class="size-4"
                  :checked="!contasDesmarcadas.has(o.value)"
                  :data-conta="o.value"
                  @change="alternarFiltro('conta', o.value, ($event.target as HTMLInputElement).checked)"
                />
                <span class="truncate">{{ o.label }}</span>
              </label>
              <p v-if="!contaOptionsBuscadas.length" class="px-1 py-1 text-[11px] text-muted-foreground">
                {{ buscaContas.trim() ? 'Nenhuma conta encontrada.' : 'Nenhuma conta no período.' }}
              </p>
            </div>
          </PopoverContent>
        </PopoverPortal>
      </PopoverRoot>
      <!-- Equipes ▾: só o número (1, 2, 3, como na aba Contas) das equipes que o servidor deixa ver. -->
      <PopoverRoot>
        <PopoverTrigger as-child>
          <button type="button" class="btn btn-sm h-7 py-0" data-filtro="equipes" title="Escolher as equipes">
            {{ rotuloEquipes }}
            <ChevronDown class="h-3 w-3 ml-1" />
          </button>
        </PopoverTrigger>
        <PopoverPortal>
          <PopoverContent
            side="bottom"
            align="start"
            :side-offset="4"
            :collision-padding="8"
            data-menu-equipes
            class="z-[70] w-64 max-w-[calc(100vw-16px)] rounded-md border bg-background p-2 shadow-lg"
          >
            <div class="flex items-center gap-2 px-1 pb-1 text-xs">
              <button type="button" class="text-blue-700 hover:underline dark:text-blue-300" data-acao="equipes-marcar-todas" @click="marcarTodas('equipe', true)">Marcar todas</button>
              <span class="text-muted-foreground">·</span>
              <button type="button" class="text-blue-700 hover:underline dark:text-blue-300" data-acao="equipes-limpar" @click="marcarTodas('equipe', false)">Limpar</button>
            </div>
            <div class="max-h-72 overflow-y-auto border-t pt-1">
              <label
                v-for="o in equipeOptions"
                :key="o.value"
                class="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm hover:bg-muted/50"
              >
                <input
                  type="checkbox"
                  class="size-4"
                  :checked="!equipesDesmarcadas.has(o.value)"
                  :data-equipe="o.value"
                  @change="alternarFiltro('equipe', o.value, ($event.target as HTMLInputElement).checked)"
                />
                {{ o.label }}
              </label>
              <p v-if="!equipeOptions.length" class="px-1 py-1 text-[11px] text-muted-foreground">Nenhuma equipe.</p>
            </div>
          </PopoverContent>
        </PopoverPortal>
      </PopoverRoot>
      <!-- Atualizar: relê os dados (o que o Recarregar fazia), no estilo do Atualizar da Tabela. -->
      <button
        type="button"
        class="btn btn-sm h-7 py-0 bg-blue-600 text-white hover:bg-blue-700 hover:text-white"
        data-acao="atualizar"
        :disabled="loading"
        title="Relê os números (os pedidos chegam sozinhos do Bling)"
        @click="atualizar"
      >
        <Loader2 v-if="loading" class="h-4 w-4 mr-1 animate-spin" />
        <RefreshCw v-else class="h-4 w-4 mr-1" />
        Atualizar
      </button>
      <!-- Personalizado inválido: a tabela fica no período anterior; o aviso fica à vista com o popover fechado. -->
      <span v-if="periodoInvalido" class="text-xs text-destructive" data-aviso-periodo>Período não aplicado. A data inicial precisa ser antes da final.</span>
      <span class="ml-auto text-xs text-muted-foreground" data-contador>{{ linhasFiltradas.length }} contas</span>
    </div>

    <div v-if="error" class="rounded border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive">{{ error }}</div>

    <!-- Tabela no padrão da aba Contas (M-22): cabeçalho e Total fixos. -->
    <div class="border rounded-lg overflow-auto max-h-[calc(100vh-240px)]" :aria-busy="loading" data-faturamento-tabela>
      <table class="w-full text-sm border-collapse">
        <thead class="sticky top-0 bg-muted z-10">
          <tr>
            <th class="text-left px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border min-w-[140px]">Conta</th>
            <th class="text-center px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border min-w-[100px]">Plataforma</th>
            <th class="text-center px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border w-16" title="A equipe de vendas da loja (1, 2 ou 3), como na aba Contas.">Equipe</th>
            <th class="text-center px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border min-w-[120px]">Tipo</th>
            <th class="text-center px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border w-20">Pedidos</th>
            <th class="text-center px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border min-w-[120px]">Faturamento</th>
            <th class="text-center px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border min-w-[100px]">Ticket médio</th>
            <th class="text-center px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border w-24" title="Cartões vermelhos da conta na Tabela de Preços (Celular, Eletro e Mala).">Pendências</th>
            <th class="text-center px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border min-w-[110px]" title="Afiliados + Ads, do Marketing, no período. Passe o mouse na conta para ver as duas partes. — = sem dado.">Investimento</th>
            <th class="text-center px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border whitespace-nowrap">% Investimento</th>
            <th v-if="isAdmin" class="text-center px-2 py-2 text-[11px] font-semibold leading-[14px] border-b border-border min-w-[110px]" data-coluna-so-admin="margem" title="Margem real da Conciliação: (sobrou de todos os pedidos − Ads) ÷ custo. Passe o mouse na conta para ver a conta. — = fora da Conciliação.">Margem</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading && !data">
            <td :colspan="colunas" class="text-center py-6 text-xs text-muted-foreground">
              <Loader2 class="inline h-4 w-4 animate-spin" /> carregando…
            </td>
          </tr>
          <tr v-else-if="!error && !linhasFiltradas.length">
            <td :colspan="colunas" class="text-center py-6 text-xs text-muted-foreground">
              <template v-if="linhasDaResposta.length">Nenhuma conta corresponde aos filtros.</template>
              <template v-else>Nenhum faturamento no período.</template>
              <span v-if="!isAdmin" class="block">Você vê apenas lojas das suas equipes de vendas.</span>
            </td>
          </tr>
          <tr v-for="r in linhasFiltradas" :key="r.store_id" class="h-[33px] hover:bg-accent/30" data-linha-faturamento>
            <td class="border border-border px-2 py-1 text-xs text-left font-medium">{{ r.loja || '—' }}</td>
            <td class="border border-border px-2 py-1 text-xs text-center">{{ platformLabel(r.tipo) }}</td>
            <td class="border border-border px-2 py-1 text-xs text-center tabular-nums">{{ numeroEquipe(r.equipe) }}</td>
            <td class="border border-border px-2 py-1 text-xs text-center">
              <div v-if="r.departments?.length" class="flex flex-wrap justify-center gap-1">
                <span
                  v-for="slug in tiposDaLoja(r)"
                  :key="slug"
                  class="inline-block rounded border border-border bg-muted px-1.5 py-0.5 text-[11px] leading-none text-muted-foreground"
                >{{ departmentLabel(slug) }}</span>
              </div>
              <span v-else class="text-muted-foreground">—</span>
            </td>
            <td class="border border-border px-2 py-1 text-xs text-center tabular-nums">{{ fmtInt(r.pedidos) }}</td>
            <td class="border border-border px-2 py-1 text-xs text-right tabular-nums font-medium">{{ fmtBRL(r.faturamento) }}</td>
            <td class="border border-border px-2 py-1 text-xs text-right tabular-nums font-medium">{{ fmtBRL(r.ticket_medio) }}</td>
            <!-- Pendências em grade de 2 colunas (cartão | número): o lugar do
                 cartão fica reservado mesmo sem cartão, para os cartões e os
                 números ficarem um embaixo do outro em todas as linhas. -->
            <td class="border border-border px-2 py-1 text-xs text-center tabular-nums" :title="tituloPendencias(pendenciasDe(r))" data-pendencias>
              <span class="inline-grid grid-cols-[7px_2.4em] items-center gap-x-[6px]">
                <span v-if="pendenciasEstado === 'ok' && (pendenciasDe(r) ?? 0) > 0" class="block h-[10px] w-[7px] rotate-12 rounded-[1px] bg-red-600 shadow-sm" aria-hidden="true" data-cartao />
                <span v-else aria-hidden="true" />
                <span class="text-left" :class="{ 'text-muted-foreground': ['—', '…'].includes(textoPendencias(pendenciasDe(r))) }">{{ textoPendencias(pendenciasDe(r)) }}</span>
              </span>
            </td>
            <td class="border border-border px-2 py-1 text-xs text-right tabular-nums" :title="tituloInvestimento(r)" data-investimento>
              <template v-if="r.investimento != null">{{ fmtBRL(r.investimento) }}<span v-if="r.investimento_parcial" class="text-muted-foreground" data-parcial>*</span></template>
              <span v-else class="text-muted-foreground">—</span>
            </td>
            <td class="border border-border px-2 py-1 text-xs text-center tabular-nums">{{ fmtPctInvestimento(r.investimento, r.faturamento) }}</td>
            <td v-if="isAdmin" class="border border-border px-2 py-1 text-xs text-center tabular-nums" :class="classeMargem(r.margem)" :title="tituloMargem(r.margem)" data-margem>{{ fmtMargem(r.margem) }}</td>
          </tr>
        </tbody>
        <tfoot v-if="linhasFiltradas.length" class="sticky bottom-0 z-10 bg-muted font-semibold" data-total>
          <tr class="h-[33px]">
            <td class="border border-border px-2 py-1 text-xs text-left">Total</td>
            <td class="border border-border px-2 py-1" colspan="3"></td>
            <td class="border border-border px-2 py-1 text-xs text-center tabular-nums">{{ fmtInt(total.pedidos) }}</td>
            <td class="border border-border px-2 py-1 text-xs text-right tabular-nums">{{ fmtBRL(total.faturamento) }}</td>
            <td class="border border-border px-2 py-1 text-xs text-right tabular-nums">{{ fmtBRL(total.ticket) }}</td>
            <td class="border border-border px-2 py-1 text-xs text-center tabular-nums" :title="tituloPendencias(total.pendencias)">
              <span class="inline-grid grid-cols-[7px_2.4em] items-center gap-x-[6px]">
                <span v-if="pendenciasEstado === 'ok' && (total.pendencias ?? 0) > 0" class="block h-[10px] w-[7px] rotate-12 rounded-[1px] bg-red-600 shadow-sm" aria-hidden="true" data-cartao />
                <span v-else aria-hidden="true" />
                <span class="text-left">{{ textoPendencias(total.pendencias) }}</span>
              </span>
            </td>
            <td class="border border-border px-2 py-1 text-xs text-right tabular-nums" :title="tituloInvestimentoTotal" data-investimento-total>
              <template v-if="total.investimento != null">{{ fmtBRL(total.investimento) }}<span v-if="total.parcial" class="text-muted-foreground">*</span></template>
              <template v-else>—</template>
            </td>
            <td class="border border-border px-2 py-1 text-xs text-center tabular-nums">{{ fmtPctInvestimento(total.investimento, total.faturamento) }}</td>
            <td v-if="isAdmin" class="border border-border px-2 py-1 text-xs text-center tabular-nums" :class="classeMargem(total.margem)" :title="tituloMargemTotal" data-margem-total>{{ fmtMargem(total.margem) }}</td>
          </tr>
        </tfoot>
      </table>
    </div>
  </div>
</template>
