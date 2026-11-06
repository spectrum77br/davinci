<script setup lang="ts">
// Pós-venda › Garantias (07/10/2026) — Painel de Garantia Uranyx.
// Documento "Painel de Garantia — Uranyx (DaVinci)", tela do §4.1:
// [+ Nova garantia], busca (Nome / CPF / NF / Pedido), Status, Período, os 4
// indicadores (06/11/2026: Ativas · Vence em 30 dias · Sem prazo cadastrado ·
// Atendimentos no mês — clicar filtra) e a tabela Nome | Pedido | Plataforma |
// Loja | Produto | Entrega | Garantia | Prazo de garantia | Status do pedido |
// Atendimento. Clicar na linha abre o detalhe (§4.2).
// Permissões (§6): a página pede "Consultar" (garantias.view); "Cadastrar"
// (garantias.edit) mostra o + Nova garantia e o Corrigir; o CPF completo só no
// detalhe, para quem tem "Ver o CPF completo". As regras (prazos, status,
// pontos do §8) vêm do servidor (GET /regras) e aparecem em "Como funciona".
// Links: ?garantia=<id> abre o detalhe; ?novo=<pedido> abre o cadastro já
// buscando o pedido (é o que o /atendimento usa).
// A lista é POST /api/garantias/lista com os filtros no CORPO: a busca leva
// nome e CPF, que não podem ir para o log de acesso (ele grava a query).
// Garantia automática (28/10/2026): o robô cria a garantia pela NF de
// produto (o selo marca as criadas sozinhas); a lista de Conversas é a do
// Atendimento — a lista à esquerda e o histórico das conversas ligadas à
// direita, com fotos e vídeos. Pedido de garantia (28/10/2026): quem PEDIU
// (mensagem, SAC, devolução por defeito, atendimento).
// As 3 partes (04/11/2026, Eduardo: "vamos dividir o painel garantia em 3
// partes… no mesmo estilo da tabela de preços: categorias em cima: celular,
// mala e eletro; resumo, garantia e atendimento embaixo"): as pílulas de
// departamento (?dept=) e as abas (?aba=) — Resumo (só admin: a visão por
// produto), Garantia (todas as garantias do departamento, com a barra de
// filtros, os cartões e a Tabela; aberta pelo Resumo, vira a lista de
// Conversas daquele produto) e Atendimento (só quem está em atendimento
// agora, na lista de Conversas, o mais urgente em cima).
// Garantia × SAC (06/11/2026, "Painel de Garantia: alterações" v3): o prazo é
// ÚNICO por produto (Tabela de Preços › Produtos) — sem hardware/software no
// painel; a Tabela tem as 10 colunas da especificação (Nome sem apelido,
// Pedido, Plataforma, Loja, Produto, Entrega, Garantia, Prazo de garantia, Status
// do pedido no Bling, Atendimento); o atendimento abre SÓ pelo formulário do
// SAC do site: a linha mostra o protocolo e o botão Confirmar, e o confirmado
// vira "Garantia acionada" e vai para a aba Atendimento até alguém encerrar
// (ou o caso terminar sozinho). O SAC que o robô não ligou a nenhuma venda
// fica em "SAC sem pedido" (o contador no topo da aba Garantia), para ligar à
// mão. O Resumo perdeu o Status e o "Ver todos os pedidos de garantia".
import type { Component } from 'vue'
import type { LocationQueryRaw } from 'vue-router'
import { AlertCircle, ArrowLeft, BarChart3, Briefcase, ChevronLeft, ChevronRight, Headset, Info, Loader2, Plus, RefreshCw, Search, ShieldCheck, Smartphone, Sparkles, Zap } from 'lucide-vue-next'
import {
  CARTOES,
  DEPARTAMENTOS,
  FILTROS_PADRAO,
  FILTROS_STATUS,
  LIMITE_PAGINA,
  REGRAS_PADRAO,
  abaDaUrl,
  cartaoAtivo,
  corpoDaLista,
  dataBR,
  dataCurta,
  dataHoraBR,
  departamentoDaUrl,
  errosDaApi,
  filtroDoCartao,
  nomeDaLinha,
  nomePlataforma,
  numeroBR,
  sacDaLinha,
  seloDoAtendimento,
  situacaoDoSac,
  tempoEmAberto,
  textoMeses,
  diasDaGarantia,
  textoPedidoSemGarantia,
  vendidoSemGarantia,
  type Aba,
  type Cartao,
  type ContagemDaAba,
  type CorpoLista,
  type Departamento,
  type FiltrosLista,
  type GarantiaDetalhe,
  type GarantiaLinha,
  type GarantiaLista,
  type Indicadores,
  type PedidoSemGarantia,
  type Regras,
  type SacDaLinha,
  type SacOut,
} from '~/lib/garantias'

definePageMeta({ middleware: ['permission'], permission: { resource: 'garantias', action: 'view' } })

const { api } = useApi()
const route = useRoute()
const router = useRouter()
const toasts = useToasts()
const acesso = useGarantiaAcesso()

// ─── regras (prazos e pontos do §8) ─────────────────────────────────────────
const regras = ref<Regras>(REGRAS_PADRAO)
const regrasAbertas = ref(false)
async function carregarRegras() {
  try {
    regras.value = { ...REGRAS_PADRAO, ...(await api<Regras>('/api/garantias/regras')) }
  } catch {
    // fica o padrão (o mesmo do servidor hoje)
  }
}

// ─── as 3 partes: o departamento (pílulas) e a aba ──────────────────────────
// O Resumo é só do admin (o servidor também recusa): quem não é admin abre na
// Garantia e o ?aba=resumo dele cai na Garantia.
const ABAS: { chave: Aba; rotulo: string; icone: Component }[] = [
  { chave: 'resumo', rotulo: 'Resumo', icone: BarChart3 },
  { chave: 'garantia', rotulo: 'Garantia', icone: ShieldCheck },
  { chave: 'atendimento', rotulo: 'Atendimento', icone: Headset },
]
const ICONE_DEPARTAMENTO: Record<Departamento, Component> = { celular: Smartphone, mala: Briefcase, eletro: Zap }
const abasVisiveis = computed(() => ABAS.filter((t) => t.chave !== 'resumo' || acesso.value.resumo))
const aba = ref<Aba>(abaDaUrl(route.query.aba, acesso.value.resumo))
const departamento = ref<Departamento>(departamentoDaUrl(route.query.dept))
// O que a pessoa enxerga em cada departamento (sem os filtros da barra): os
// números das pílulas e das abas. Vem com a lista.
const abas = ref<Record<Departamento, ContagemDaAba> | null>(null)
const contagem = computed(() => abas.value?.[departamento.value] ?? null)
// A coluna Garantia em dias: vencida em vermelho, 30 dias ou menos em âmbar,
// "aguarda entrega" apagado (as cores ficam aqui: o Tailwind não lê lib/).
const TOM_DIAS = {
  normal: '',
  perto: 'text-amber-700 dark:text-amber-300',
  vencida: 'text-destructive',
  espera: 'text-muted-foreground',
} as const
const NOTA_MALA = 'A garantia do DaVinci hoje é só da linha Uranyx (celular e eletro).'

// A lista de Conversas na aba Garantia: aberta pela linha do Resumo (o
// produto: todas as garantias dele) ou pelo número de uma coluna (os pedidos
// dela). O "ver todos os pedidos de garantia" saiu em 06/11/2026 (dependia da
// detecção pelas conversas). A aba Atendimento é sempre a lista de Conversas.
const abertoPor = ref<'produto' | null>(null)
const produtoAberto = ref('')
const emConversas = computed(() => aba.value === 'atendimento' || (aba.value === 'garantia' && abertoPor.value !== null))
// O "Mostrar" da Tabela: todas as vendas com nota (o padrão da aba Garantia)
// ou só as de garantia acionada (o SAC confirmado). A busca acha todas.
const mostrar = ref<'todas' | 'acionada'>('todas')

// ─── lista ──────────────────────────────────────────────────────────────────
// Nasce com o `ids` e o `produto` já criados — a 1ª troca não relê a lista a
// mais (revisão 29/10).
const filtros = reactive<FiltrosLista>({ ...FILTROS_PADRAO, ordem: 'cadastro', com_conversa: false, origem: 'todas', mostrar: 'todas', ids: undefined, produto: null, departamento: departamento.value })
function aplicarVista() {
  filtros.departamento = departamento.value
  filtros.ordem = emConversas.value ? 'atividade' : 'cadastro'
  filtros.com_conversa = false
  // O "Mostrar" é da Tabela. Nas Conversas: o produto do Resumo (a linha ou a
  // coluna) traz todas as que vieram.
  filtros.mostrar = abertoPor.value === 'produto' ? 'todas' : mostrar.value
}
// O corpo do POST /lista. O Atendimento é só o que está em atendimento agora
// no departamento: os filtros da barra não valem lá. No Resumo a lista só traz
// os números das pílulas e das abas: nada do que ficou para trás na aba
// Garantia vai junto (revisão 04/11: o CPF da busca, escondida no Resumo,
// contava de novo no teto e no log a cada "atualizar"; e o cartão ligado).
function corpo(offset = 0, limite = LIMITE_PAGINA): CorpoLista {
  if (aba.value === 'atendimento') {
    return corpoDaLista({ ...FILTROS_PADRAO, departamento: departamento.value, em_atendimento: true }, offset, limite)
  }
  if (aba.value === 'resumo') return corpoDaLista({ ...FILTROS_PADRAO, departamento: departamento.value }, offset, limite)
  return corpoDaLista(filtros, offset, limite)
}
const itens = ref<GarantiaLinha[]>([])
const total = ref(0)
const indicadores = ref<Indicadores | null>(null)
// O dia do servidor (o "há N dias" do selo do Atendimento).
const hoje = ref<string | undefined>(undefined)
// A busca não achou garantia, mas achou o pedido: por que ele não tem.
const pedidoSemGarantia = ref<PedidoSemGarantia | null>(null)
// Os SAC do site que o robô não ligou a nenhuma venda (o contador no topo da
// aba Garantia; só vem para quem enxerga todas as lojas).
const sacSemPedido = ref(0)
const sacSemPedidoAberto = ref(false)
// "abrir o caso" da coluna Atendimento: a garantia que a aba Atendimento abre
// quando a lista chegar (mesmo se o caso já tiver saído dela).
let casoParaAbrir: GarantiaLinha | null = null
const carregando = ref(false)
const carregandoMais = ref(false)
// A Tabela da aba Garantia em PÁGINAS (06/11/2026, Eduardo: "deixe paginado
// também a parte garantias, não mais 100 mais 100"). A lista de conversas
// (aba Atendimento, produto aberto) continua com o "mais N".
const pagina = ref(1)
const paginado = computed(() => aba.value === 'garantia' && !emConversas.value)
const totalPaginas = computed(() => Math.max(1, Math.ceil(total.value / LIMITE_PAGINA)))
const tabelaTopo = ref<HTMLElement | null>(null)
function irParaPagina(p: number) {
  const alvo = Math.min(Math.max(1, p), totalPaginas.value)
  if (alvo === pagina.value || carregando.value) return
  pagina.value = alvo
  void carregar().then(() => tabelaTopo.value?.scrollIntoView({ block: 'start', behavior: 'smooth' }))
}
const carregado = ref(false)
const erro = ref('')
let geracao = 0

// `trocouFiltro`: a lista é outra (filtro, busca, aba) — a garantia aberta
// que não está mais nela dá lugar à primeira. Sem isso (atualizar, registrou
// atendimento, corrigiu), a aberta continua aberta mesmo fora da 1ª página, e
// a releitura traz de novo tudo o que já estava carregado.
async function carregar(opcoes?: { trocouFiltro?: boolean }) {
  const trocouFiltro = opcoes?.trocouFiltro === true
  const g = ++geracao
  carregando.value = true
  erro.value = ''
  try {
    if (trocouFiltro) pagina.value = 1
    const porPagina = paginado.value
    const limite = porPagina || trocouFiltro ? LIMITE_PAGINA : Math.min(500, Math.max(LIMITE_PAGINA, itens.value.length))
    const offset = porPagina ? (pagina.value - 1) * LIMITE_PAGINA : 0
    const r = await api<GarantiaLista>('/api/garantias/lista', { method: 'POST', body: corpo(offset, limite) })
    if (g !== geracao) return
    // A lista encolheu (outra pessoa mexeu, a garantia saiu do filtro) e a
    // página passou do fim: volta para a última que existe.
    if (porPagina && pagina.value > 1 && !r.itens.length && r.total) {
      pagina.value = Math.max(1, Math.ceil(r.total / LIMITE_PAGINA))
      return void carregar()
    }
    itens.value = r.itens
    total.value = r.total
    indicadores.value = r.indicadores
    hoje.value = r.hoje || undefined
    if (r.abas) abas.value = r.abas
    pedidoSemGarantia.value = r.pedido_sem_garantia ?? null
    sacSemPedido.value = r.sac_sem_pedido ?? 0
    carregado.value = true
    const caso = casoParaAbrir
    casoParaAbrir = null
    if (caso && aba.value === 'atendimento') {
      selecionadaId.value = caso.id
      selecionadaLinha.value = itens.value.find((x) => x.id === caso.id) || caso
      historicoNoCelular.value = true
      return
    }
    const achada = itens.value.find((x) => x.id === selecionadaId.value)
    if (achada) {
      selecionadaLinha.value = achada
    } else if (trocouFiltro || selecionadaId.value === null) {
      // A lista de Conversas abre a primeira — na tela larga; no celular a
      // lista vem primeiro e nada é aberto (nem lido) sem a pessoa tocar. Na
      // Tabela nada fica escolhido escondido.
      const primeira = emConversas.value && telaLarga() ? itens.value[0] ?? null : null
      selecionadaId.value = primeira?.id ?? null
      selecionadaLinha.value = primeira
      if (!primeira) historicoNoCelular.value = false
    }
  } catch (e) {
    if (g !== geracao) return
    erro.value = errosDaApi(e).geral || 'Não consegui carregar as garantias.'
  } finally {
    if (g === geracao) carregando.value = false
  }
}

async function carregarMais() {
  if (carregandoMais.value || itens.value.length >= total.value) return
  const g = geracao
  carregandoMais.value = true
  try {
    const r = await api<GarantiaLista>('/api/garantias/lista', { method: 'POST', body: corpo(itens.value.length) })
    if (g !== geracao) return
    const vistos = new Set(itens.value.map((x) => x.id))
    itens.value = [...itens.value, ...r.itens.filter((x) => !vistos.has(x.id))]
    total.value = r.total
  } catch (e) {
    toasts.error('Não consegui carregar mais', errosDaApi(e).geral)
  } finally {
    carregandoMais.value = false
  }
}

// As trocas (aba, departamento, abrir produto, ver todos, voltar, limpar)
// mexem em vários filtros de uma vez: nenhum watcher relê no meio e a lista é
// relida UMA vez, já com tudo trocado (revisão 29/10: a busca mandava 2-3
// pedidos — o CPF colado contava 2-3 vezes no teto e no log). O Resumo lê o
// dele (GarantiaResumo): nele a lista não é relida.
let emTroca = false
let atraso: ReturnType<typeof setTimeout> | null = null
function trocarDeVista(mudar: () => void) {
  emTroca = true
  try {
    mudar()
    aplicarVista()
  } finally {
    void nextTick(() => { emTroca = false })
  }
  if (atraso) clearTimeout(atraso)
  atraso = null
  if (aba.value !== 'resumo') void carregar({ trocouFiltro: true })
}
// A lista que abre é outra: nada da anterior fica na tela nem aberto (nem
// lido) até ela chegar.
function limparLista() {
  selecionadaId.value = null
  selecionadaLinha.value = null
  historicoNoCelular.value = false
  itens.value = []
  carregado.value = false
  pedidoSemGarantia.value = null
}
function fecharConversas() {
  abertoPor.value = null
  produtoAberto.value = ''
  filtros.ids = undefined
  filtros.produto = null
}
function entrarNasConversas(por: 'produto', alvo: { produto?: string | null; ids?: number[]; nome?: string } = {}) {
  abertoPor.value = por
  produtoAberto.value = alvo.nome || ''
  filtros.produto = alvo.produto ?? null
  filtros.ids = alvo.ids
  limparLista()
}
function trocarAba(nova: Aba) {
  const destino: Aba = nova === 'resumo' && !acesso.value.resumo ? 'garantia' : nova
  // A aba de agora só fecha a lista de Conversas aberta pelo Resumo.
  if (destino === aba.value && !abertoPor.value) return
  trocarDeVista(() => {
    fecharConversas()
    limparLista()
    aba.value = destino
  })
}
function trocarDepartamento(novo: Departamento) {
  if (novo === departamento.value) return
  trocarDeVista(() => {
    // O produto aberto é do outro departamento: fecha (a Tabela do novo).
    if (abertoPor.value) fecharConversas()
    limparLista()
    departamento.value = novo
  })
}
// A busca relê depois da pausa de 300 ms e filtra a Tabela da aba Garantia (a
// lista de Conversas aberta pelo Resumo fecha).
watch(() => filtros.busca, () => {
  if (atraso) clearTimeout(atraso)
  atraso = null
  if (emTroca) return
  atraso = setTimeout(buscaAssentou, 300)
})
function buscaAssentou() {
  atraso = null
  if (abertoPor.value && filtros.busca.trim()) {
    return trocarDeVista(() => {
      fecharConversas()
      limparLista()
    })
  }
  void carregar({ trocouFiltro: true })
}
// Os filtros da barra relêem a lista. Os ids de uma coluna do Resumo são uma
// foto com os filtros daquela hora: mexer num filtro da barra com eles
// abertos volta para a Tabela (sem o "todos os pedidos de garantia", que saiu
// em 06/11/2026) — na mesma releitura. O produto (a linha) é um filtro de
// verdade: fica.
function soltarIds() {
  if (!filtros.ids?.length) return
  abertoPor.value = null
  produtoAberto.value = ''
  filtros.ids = undefined
}
watch(() => [filtros.status, filtros.periodo, filtros.de, filtros.ate, filtros.com_atendimento_no_mes, filtros.com_conversa, filtros.origem], () => {
  if (emTroca) return
  if (filtros.ids?.length) {
    soltarIds()
    aplicarVista()
  }
  // O Resumo relê sozinho (o GarantiaResumo acompanha os filtros).
  if (aba.value === 'resumo') return
  void carregar({ trocouFiltro: true })
})
// O "Mostrar" (é da Tabela): relê só quando muda o que a lista traz.
watch(mostrar, () => {
  if (emTroca) return
  const antes = filtros.mostrar
  aplicarVista()
  if (filtros.mostrar !== antes) void carregar({ trocouFiltro: true })
})

const ativo = computed(() => cartaoAtivo(filtros))
function clicarCartao(c: Cartao) {
  Object.assign(filtros, filtroDoCartao(filtros, c))
}
// Os filtros que a aba mostra: o Resumo só tem o período (o Status saiu em
// 06/11/2026); a Garantia, também o status, a busca e os cartões.
const temFiltro = computed(() => {
  const periodo = !!filtros.de || !!filtros.ate || (!!filtros.origem && filtros.origem !== 'todas')
  if (aba.value === 'resumo') return periodo
  return periodo || filtros.status !== 'todos' || !!filtros.busca.trim() || filtros.com_atendimento_no_mes
})
function limparFiltros() {
  trocarDeVista(() => {
    Object.assign(filtros, { busca: '', status: 'todos', periodo: FILTROS_PADRAO.periodo, de: '', ate: '', com_atendimento_no_mes: false, origem: 'todas' })
    soltarIds()
  })
}
// O Resumo: a busca e a ordem da tabela ficam aqui (voltam iguais).
const resumoBusca = ref('')
const resumoOrdem = ref({ coluna: 'vendidas', desc: true })
// O Resumo abre a aba Garantia na lista de Conversas: a linha (ou Vendidas,
// Saldo, %) traz todas as garantias do produto (a `chave`); o número de uma
// coluna, só aqueles pedidos (os `ids` — o aviso diz qual: "A17 · Devolução").
// A busca, o Status e o cartão "Atendimentos no mês" (os da aba Garantia, que
// o Resumo não usa — o Status saiu dele em 06/11/2026) saem: a lista aberta tem
// o que o número do Resumo contou.
function abrirProduto(p: { nome: string; chave?: string; ids?: number[]; coluna?: string }) {
  trocarDeVista(() => {
    filtros.busca = ''
    filtros.status = 'todos'
    filtros.com_atendimento_no_mes = false
    aba.value = 'garantia'
    entrarNasConversas('produto', p.ids
      ? { ids: p.ids, nome: p.coluna ? `${p.nome} · ${p.coluna}` : p.nome }
      : { produto: p.chave ?? null, nome: p.nome })
  })
}
function voltarParaTabela() {
  trocarDeVista(() => {
    fecharConversas()
    limparLista()
  })
}
function voltarParaResumo() {
  trocarDeVista(() => {
    fecharConversas()
    limparLista()
    filtros.busca = ''
    aba.value = acesso.value.resumo ? 'resumo' : 'garantia'
  })
}
const buscaParcialDeCpf = computed(() => {
  const d = filtros.busca.replace(/\D/g, '')
  return /^[\d.\-\s]+$/.test(filtros.busca.trim()) && d.length > 9 && d.length < 11
})
// O cartão do pedido sem garantia: a busca não achou nenhuma garantia, mas o
// servidor achou o pedido (dentro da equipe).
const cartaoSemGarantia = computed(() =>
  aba.value === 'garantia' && carregado.value && !itens.value.length && filtros.busca.trim() ? pedidoSemGarantia.value : null)
// O vazio de cada aba (a Mala ganha o aviso de que a garantia é da Uranyx).
// A busca procura só no departamento da pílula: o vazio dela diz isso e
// oferece procurar nos outros dois (revisão 04/11: a garantia de Eletro
// buscada no Celular parecia não existir).
const outrosDepartamentos = computed(() => DEPARTAMENTOS.filter((d) => d.chave !== departamento.value))
const vazio = computed(() => {
  const mala = departamento.value === 'mala' ? NOTA_MALA : ''
  if (aba.value === 'atendimento') {
    return {
      titulo: 'Nenhum caso em atendimento neste departamento.',
      descricao: mala || 'Aparece aqui o SAC do site depois que alguém clica em Confirmar na coluna Atendimento da aba Garantia; sai quando alguém encerra ou quando o caso termina sozinho.',
      mostrarTodas: false,
      buscaNosOutros: false,
    }
  }
  const busca = !!filtros.busca.trim()
  const nome = DEPARTAMENTOS.find((d) => d.chave === departamento.value)?.nome || departamento.value
  const soAcionadas = filtros.mostrar === 'acionada' && !busca
  const mostrarTodas = soAcionadas && !abertoPor.value
  const titulo = soAcionadas
    ? (temFiltro.value ? 'Nenhuma garantia acionada com esses filtros' : 'Nenhuma garantia acionada ainda')
    : busca
      ? `Nenhuma garantia com esses filtros em ${nome}`
      : temFiltro.value ? 'Nenhuma garantia com esses filtros' : 'Nenhuma garantia neste departamento ainda'
  const descricao = mostrarTodas
    ? 'Em "Mostrar" está "garantia acionada": só as vendas com o SAC do site confirmado. As outras ficam escondidas.'
    : busca
      ? `A busca procura só em ${nome}: se a garantia for de ${outrosDepartamentos.value.map((d) => d.nome).join(' ou ')}, procure na pílula dela. Ou mude a busca, o status ou o período.`
      : temFiltro.value
        ? 'Mude a busca, o status ou o período.'
        : soAcionadas
          ? 'Nenhum SAC do site foi confirmado ainda neste departamento.'
          : mala
            ? ''
            : 'Cadastre a primeira pelo + Nova garantia: informe o pedido e a data de entrega vira o início da garantia.'
  return { titulo, descricao: [descricao, mala].filter(Boolean).join(' '), mostrarTodas, buscaNosOutros: busca }
})

// ─── lista de Conversas: a garantia aberta no histórico ────────────────────
const selecionadaId = ref<number | null>(null)
// A última linha conhecida da aberta: ela continua aberta mesmo quando a
// releitura não a traz (fora da 1ª página).
const selecionadaLinha = ref<GarantiaLinha | null>(null)
const selecionada = computed(() =>
  itens.value.find((x) => x.id === selecionadaId.value)
  || (selecionadaLinha.value?.id === selecionadaId.value ? selecionadaLinha.value : null),
)
// No celular a lista e o histórico se revezam.
const historicoNoCelular = ref(false)
function telaLarga(): boolean {
  try {
    return typeof window === 'undefined' || window.matchMedia('(min-width: 768px)').matches
  } catch {
    return true
  }
}
function selecionar(id: number) {
  selecionadaId.value = id
  selecionadaLinha.value = itens.value.find((x) => x.id === id) || null
  historicoNoCelular.value = true
}
// O selo do aparelho repete o do Atendimento quando o motivo é ele (no
// técnico, chegou sem condição, voltando): fica só o do Atendimento.
const MOTIVOS_DO_APARELHO = ['no_tecnico', 'sem_condicao', 'voltando']
function aparelhoRepetido(g: GarantiaLinha): boolean {
  return !!g.atendimento && MOTIVOS_DO_APARELHO.includes(g.atendimento.motivo)
}
const versaoHistorico = ref(0)
// "atualizar", cadastrar e corrigir também releem o resumo.
const versaoResumo = ref(0)

// ─── detalhe e cadastro ─────────────────────────────────────────────────────
const abertaId = ref<number | null>(null)
const avisosDaAberta = ref<string[]>([])
const versaoDetalhe = ref(0)
const formAberto = ref(false)
const corrigindo = ref<GarantiaDetalhe | null>(null)
const pedidoInicial = ref<string | null>(null)

function abrir(id: number, avisos: string[] = []) {
  avisosDaAberta.value = avisos
  abertaId.value = id
}
function fecharDetalhe() {
  abertaId.value = null
  avisosDaAberta.value = []
}
function novaGarantia(pedido: string | null = null) {
  corrigindo.value = null
  pedidoInicial.value = pedido
  formAberto.value = true
}
function corrigir(g: GarantiaDetalhe) {
  corrigindo.value = g
  pedidoInicial.value = null
  formAberto.value = true
}
function aoSalvar(g: GarantiaDetalhe) {
  const era = corrigindo.value
  formAberto.value = false
  corrigindo.value = null
  toasts.success(era ? `Garantia #${g.id} corrigida` : `Garantia #${g.id} cadastrada`, vendidoSemGarantia(g)
    ? 'O produto foi vendido sem garantia (prazo 0 na Tabela de Preços › Produtos).'
    : g.data_inicio
    ? (g.fim_garantia ? `Garantia até ${dataBR(g.fim_garantia)}.` : 'O produto não tem prazo da garantia na Tabela de Preços › Produtos: a garantia fica sem data final até ele ser cadastrado.')
    : g.entregue_sem_data
      ? 'O pedido já consta como entregue no Bling, mas o DaVinci não tem a data de entrega: a garantia fica sem prazo.'
      : 'Aguardando a data de entrega do pedido.')
  if (era && abertaId.value === g.id) {
    avisosDaAberta.value = g.avisos || []
    versaoDetalhe.value++
  } else {
    abrir(g.id, g.avisos || [])
  }
  void carregar()
  versaoResumo.value++
}
function aoMudarDetalhe(r?: GarantiaDetalhe) {
  // A linha aberta no histórico acompanha o pedido de garantia (ela continua
  // aberta mesmo quando sai da lista dos pedidos).
  const linha = selecionadaLinha.value
  if (r && linha && linha.id === r.id) {
    selecionadaLinha.value = { ...linha, pedido_em: r.pedido_em, pedido_origem: r.pedido_origem, pedido_origem_rotulo: r.pedido_origem_rotulo, nao_e_pedido: r.nao_e_pedido }
  }
  void carregar()
  versaoHistorico.value++
  versaoResumo.value++
}
function abrirDoForm(id: number) {
  formAberto.value = false
  corrigindo.value = null
  abrir(id)
}

// A URL acompanha a aba, o departamento e a garantia aberta (dá para mandar
// o link). Monta pelo estado: dois replace seguidos não se desfazem.
function atualizarUrl() {
  const query: LocationQueryRaw = { ...route.query, aba: aba.value, dept: departamento.value }
  if (abertaId.value) query.garantia = String(abertaId.value)
  else delete query.garantia
  delete query.novo
  void router.replace({ query })
}
watch([aba, departamento, abertaId], atualizarUrl)

onMounted(() => {
  aplicarVista()
  void carregarRegras()
  // A lista também no Resumo: os números das pílulas e das abas vêm com ela.
  void carregar()
  const id = Number(route.query.garantia)
  if (Number.isInteger(id) && id > 0) abrir(id)
  const novo = typeof route.query.novo === 'string' ? route.query.novo.trim() : ''
  if (novo && acesso.value.cadastra) novaGarantia(novo)
  // O ?novo= já usado, a aba que a pessoa não vê e o departamento que não
  // existe saem da URL.
  const urlDiferente = (k: 'aba' | 'dept', v: string) => route.query[k] !== undefined && route.query[k] !== v
  if (novo || urlDiferente('aba', aba.value) || urlDiferente('dept', departamento.value)) atualizarUrl()
})

const STATUS_CARTAO: Record<Cartao, keyof Indicadores> = {
  ativas: 'ativas',
  vence_30d: 'vence_30d',
  sem_prazo: 'sem_prazo',
  atendimentos_no_mes: 'atendimentos_no_mes',
}
// O número em âmbar: o que pede ação (vence logo; produto sem prazo).
const CARTAO_ALERTA: Cartao[] = ['vence_30d', 'sem_prazo']

// ─── o SAC do site na coluna Atendimento (06/11/2026) ───────────────────────
// Confirmar: a garantia fica "acionada" e o caso vai para a aba Atendimento.
// Nenhum e-mail sai nesse clique (a resposta é pela aba Atendimento). Com a
// garantia vencida (409 garantia_vencida) ou o produto vendido sem garantia
// (409 produto_sem_garantia, o prazo 0) o servidor recusa e a pessoa confirma
// de novo sabendo disso.
const confirmando = ref<number | null>(null)
async function confirmar(g: GarantiaLinha) {
  const sac = g.sac
  if (!sac || confirmando.value !== null) return
  confirmando.value = sac.id
  try {
    const url = `/api/garantias/sac/${sac.id}/confirmar`
    let r: SacOut
    try {
      r = await api<SacOut>(url, { method: 'POST', body: { ciente_vencida: false } })
    } catch (e: any) {
      const det = e?.data?.detail
      if (det?.code === 'produto_sem_garantia') {
        if (!window.confirm('Este produto foi vendido sem garantia. Confirmar mesmo assim?')) return
      } else if (det?.code === 'garantia_vencida') {
        const fim = typeof det.fim_garantia === 'string' ? det.fim_garantia : g.fim_garantia
        if (!window.confirm(`A garantia venceu em ${dataBR(fim)}. Confirmar mesmo assim?`)) return
      } else {
        throw e
      }
      r = await api<SacOut>(url, { method: 'POST', body: { ciente_vencida: true } })
    }
    // A linha já fica "Garantia acionada" com a resposta (a lista relê atrás).
    atualizarSac(r)
    toasts.success(`Garantia acionada — ${sac.protocolo}`, 'O caso foi para a aba Atendimento. Nenhum e-mail foi enviado: responda o cliente por lá.')
    void carregar()
    versaoResumo.value++
  } catch (e) {
    toasts.error('Não deu para confirmar', errosDaApi(e).geral)
  } finally {
    confirmando.value = null
  }
}
// "abrir o caso": a aba Atendimento, já com esta garantia aberta.
function abrirCaso(g: GarantiaLinha) {
  trocarDeVista(() => {
    fecharConversas()
    limparLista()
    filtros.busca = ''
    aba.value = 'atendimento'
    casoParaAbrir = g
  })
}
// O caso como o servidor devolveu (Confirmar/Encerrar) vai para a linha
// aberta e para a da lista, na hora (revisão 06/11/2026: depois do Encerrar o
// painel continuava com o caso aberto — o Encerrar ativo e a caixa de e-mail —,
// porque a releitura da aba Atendimento já não traz o caso e a linha aberta
// ficava a de antes).
function atualizarSac(s: SacDaLinha | null | undefined) {
  if (!s || typeof s !== 'object' || typeof s.id !== 'number') return
  const novo = sacDaLinha(s)
  const trocar = (g: GarantiaLinha): GarantiaLinha => (g.sac?.id === novo.id ? { ...g, sac: novo } : g)
  if (itens.value.some((g) => g.sac?.id === novo.id)) itens.value = itens.value.map(trocar)
  const aberta = selecionadaLinha.value
  if (aberta?.sac?.id === novo.id) selecionadaLinha.value = trocar(aberta)
}
// O histórico pediu (Encerrou no cabeçalho do caso, com o caso como ficou; ou
// registrou atendimento): a lista relê. O histórico NÃO se relê por aqui — o
// do Encerrar não muda, e o do Registrar o próprio GarantiaHistorico relê
// (cada leitura grava "consultou" no log).
function aoMudarCaso(sac?: SacDaLinha) {
  atualizarSac(sac)
  void carregar()
}
// O caso aberto pelo "ver o caso (encerrado)" não é da lista da aba (ela só
// tem os abertos): ele entra em cima, para dar para abrir e Reabrir
// (06/11/2026, "queria ver ele em atendimentos de volta").
const listaDeConversas = computed(() => {
  const s = selecionadaLinha.value
  return s && !itens.value.some((g) => g.id === s.id) ? [s, ...itens.value] : itens.value
})
function aoVincularSac() {
  void carregar()
}
</script>

<template>
  <div class="space-y-4">
    <PageHeader title="Painel de Garantia — Uranyx" description="Garantias dos produtos Uranyx: o prazo de cada produto (Tabela de Preços › Produtos) a partir da entrega e os atendimentos que chegam pelo SAC do site.">
      <template #actions>
        <Button size="sm" variant="ghost" :disabled="carregando" @click="carregar(); versaoResumo++">
          <RefreshCw class="mr-1 size-4" :class="{ 'animate-spin': carregando }" /> atualizar
        </Button>
        <Button size="sm" variant="outline" :aria-expanded="regrasAbertas" @click="regrasAbertas = !regrasAbertas">
          <Info class="mr-1 size-4" /> Como funciona
        </Button>
        <Button v-if="acesso.cadastra" size="sm" data-nova-garantia @click="novaGarantia()"><Plus class="mr-1 size-4" /> Nova garantia</Button>
      </template>
    </PageHeader>

    <!-- regras (§2 e os pontos do §8, como o servidor diz) -->
    <section v-if="regrasAbertas" class="space-y-1.5 rounded-lg border bg-muted/30 p-4 text-sm" data-regras>
      <h2 class="font-semibold">Como a garantia é calculada</h2>
      <ul class="list-disc space-y-1 pl-5 text-muted-foreground">
        <li>O painel tem <strong class="text-foreground">3 partes</strong>, por departamento (Celular, Mala, Eletro — as pílulas em cima): <strong class="text-foreground">Resumo</strong> (só para administradores) — a visão por produto; <strong class="text-foreground">Garantia</strong> — todas as garantias (as vendas com nota de todas as plataformas), com a busca, os filtros e os cartões; <strong class="text-foreground">Atendimento</strong> — só o que está em atendimento agora.</li>
        <li>A <strong class="text-foreground">data inicial</strong> é a data de entrega do pedido (não se edita). O fim da garantia = entrega + o <strong class="text-foreground">prazo do produto na Tabela de Preços › Produtos</strong> (coluna Garantia, em meses). Se o dia não existe no mês final, vale o último dia (30/11 + 3 meses = 28/02).</li>
        <li>{{ regras.ultimo_dia_coberto ? 'A garantia vale até a data final, inclusive.' : 'A garantia termina no dia anterior à data final.' }}</li>
        <li>Produto sem prazo na Tabela de Preços: a garantia fica sem data final, com o aviso "prazo não cadastrado". Prazo <strong class="text-foreground">0</strong> = o produto foi vendido <strong class="text-foreground">sem garantia</strong> (como o avulso): a venda fica "Sem garantia", sem data final. Se o prazo do produto aumentar, vale para todas as vendas dele; se diminuir, só para as vendas a partir da mudança (as já vendidas não encurtam); se for corrigido no mesmo dia, o valor anterior não conta (como se não tivesse existido). A mudança fica no log.</li>
        <li>{{ regras.cadastro_antes_da_entrega ? 'Pode cadastrar antes da entrega: fica Aguardando entrega e o prazo entra sozinho quando a data aparecer.' : 'Só dá para cadastrar depois da entrega.' }}</li>
        <li>{{ regras.garantia_por === 'nf' ? 'Uma garantia por NF (o mesmo CPF não se repete na mesma NF); os itens da NF aparecem no detalhe.' : 'Uma garantia por produto.' }}</li>
        <li v-if="regras.vinculo_automatico">A garantia <strong class="text-foreground">cai sozinha</strong>: todo pedido com produto Uranyx que tem a nota fiscal de PRODUTO no DaVinci vira garantia (com o selo “criada sozinha pela NF”), com o nome e o CPF da nota. A nota de embalagem nunca vira garantia. Se faltar algo (CPF que é CNPJ, nota repetida), o pedido fica para o cadastro manual. As conversas do pedido (Shopee, TikTok, Mercado Livre, Amazon) entram sozinhas no histórico, com fotos e vídeos.</li>
        <li v-else>O vínculo com o Atendimento é manual: o atendente clica em "Vincular à garantia" e busca por CPF, NF ou pedido (a busca já vem com o pedido da conversa).</li>
        <li>{{ regras.recalcular_quando_entrega_mudar ? 'Se a data de entrega for corrigida, a garantia é recalculada sozinha (fica no log).' : 'A data de entrega corrigida depois não muda a garantia.' }}</li>
        <li>Status: Ativa · Expirada · Aguardando entrega · Prazo não cadastrado · Sem garantia (prazo 0) — ou "Entregue — sem data no DaVinci", quando o Bling já dá o pedido como entregue mas nenhuma fonte tem a data (a Logística guarda a data só desde 15/07/2026). Na lista fica o Status do pedido no Bling; o da garantia fica no filtro, nos cartões e nos Detalhes. O CPF e a NF ficam nos Detalhes e na busca.</li>
        <li><strong class="text-foreground">SAC do site</strong>: o atendimento abre só pelo formulário uranyx.com.br/sac (o e-mail com o protocolo, US-26-NNNN, que chega no sac@). O DaVinci liga o SAC à venda pelo nº do pedido na plataforma, depois pela nota fiscal, depois pelo CPF + produto; sem achar, ele fica em "SAC sem pedido" para ligar à mão. Na coluna Atendimento aparecem o protocolo, a data e o botão <strong class="text-foreground">Confirmar</strong> — confirmado, a venda fica "Garantia acionada" (o log guarda quem confirmou). Confirmar não manda e-mail. Os outros e-mails do sac@ continuam na fila de E-mail do Atendimento.</li>
        <li><strong class="text-foreground">Resumo</strong>: só os produtos da Tabela de Preços › Produtos. A % é gasto ÷ saldo × 100 — saldo = base − comissão − frete − custo do produto, a conta da planilha de rentabilidade (o que sobrou), das vendas do produto; gasto = tela Reembolso (conserto + perdidos + reembolso ao cliente) de quem pediu garantia. Com o período pela data da venda, tudo é só das vendas daqueles dias. O que cada coluna conta aparece com o mouse no título dela.</li>
        <li><strong class="text-foreground">Atendimento</strong> = só os casos com Garantia acionada (o SAC confirmado), o mais antigo em cima, com o tempo em aberto desde o formulário. A resposta ao cliente é só por e-mail, pelo sac@ com a assinatura de Cadastros › E-mails; o chat da plataforma aparece só para leitura. O caso sai quando alguém clica em "Encerrar atendimento" ou quando termina sozinho (técnico, devolução, decisão da plataforma).</li>
      </ul>
    </section>

    <!-- departamentos em cima (as pílulas da Tabela de Preços) -->
    <div class="flex flex-wrap gap-2" data-departamentos>
      <button
        v-for="d in DEPARTAMENTOS"
        :key="d.chave"
        type="button"
        class="inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm transition-colors"
        :class="departamento === d.chave ? 'bg-primary text-primary-foreground border-primary' : 'border-border hover:bg-muted'"
        :aria-pressed="departamento === d.chave"
        :data-departamento="d.chave"
        @click="trocarDepartamento(d.chave)"
      >
        <component :is="ICONE_DEPARTAMENTO[d.chave]" class="h-4 w-4" />
        <!-- Só o nome (06/11/2026, Eduardo: "tire a quantidade de garantias da categoria"). -->
        {{ d.nome }}
      </button>
    </div>

    <!-- as abas embaixo (como as da Tabela de Preços) -->
    <!-- As abas e, ao lado delas, o período (05/11/2026: as setas no print — "se
         eu colocar uma data, filtra o saldo daqueles dias"). Sempre pela data da
         venda: o "Período por" saiu ("exclua isso também"). O Atendimento não
         tem filtro. -->
    <div class="flex flex-wrap items-end gap-x-4 gap-y-2" data-linha-abas>
    <div class="flex flex-wrap gap-1 rounded-md bg-muted/40 p-1 w-fit" role="tablist" data-abas>
      <button
        v-for="t in abasVisiveis"
        :key="t.chave"
        type="button"
        role="tab"
        class="inline-flex items-center gap-1.5 rounded px-3 py-1.5 text-sm transition-colors"
        :class="aba === t.chave ? 'bg-background shadow-sm' : 'text-muted-foreground hover:text-foreground'"
        :aria-selected="aba === t.chave"
        :data-aba="t.chave"
        @click="trocarAba(t.chave)"
      >
        <component :is="t.icone" class="h-4 w-4" />
        {{ t.rotulo }}
        <span v-if="t.chave !== 'resumo' && contagem" class="text-xs text-muted-foreground">({{ numeroBR(t.chave === 'garantia' ? contagem.garantias : contagem.atendimento) }})</span>
      </button>
    </div>
    <div v-if="aba !== 'atendimento'" class="flex flex-wrap items-end gap-2" data-periodo>
      <div class="flex w-full items-end gap-2 sm:w-auto">
        <div class="flex-1 sm:w-36 sm:flex-none">
          <label for="gar-de" class="mb-1 block text-xs font-medium text-muted-foreground">Vendas de</label>
          <Input id="gar-de" v-model="filtros.de" type="date" class="h-9" :max="filtros.ate || undefined" />
        </div>
        <div class="flex-1 sm:w-36 sm:flex-none">
          <label for="gar-ate" class="mb-1 block text-xs font-medium text-muted-foreground">Até</label>
          <Input id="gar-ate" v-model="filtros.ate" type="date" class="h-9" :min="filtros.de || undefined" />
        </div>
      </div>
      <Button v-if="aba === 'resumo' && temFiltro" size="sm" variant="ghost" class="h-9" data-limpar-datas @click="limparFiltros">limpar datas</Button>
    </div>
    </div>

    <!-- a barra é da aba Garantia: a busca, o Status (saiu do Resumo em
         06/11/2026) e o "Mostrar" (o período fica ao lado das abas; a Origem
         saiu em 05/11/2026, "excluir origem também"). O Atendimento não tem
         filtro. -->
    <div v-if="aba === 'garantia'" class="flex flex-wrap items-end gap-2" data-barra>
      <div class="w-full sm:w-80">
        <label for="gar-busca" class="mb-1 block text-xs font-medium text-muted-foreground">Buscar</label>
        <div class="relative">
          <Search class="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input id="gar-busca" v-model="filtros.busca" class="h-9 pl-8" placeholder="Nome / CPF / NF / Pedido" autocomplete="off" />
        </div>
      </div>
      <div class="w-[calc(50%-0.25rem)] sm:w-48">
        <label for="gar-status" class="mb-1 block text-xs font-medium text-muted-foreground">Status</label>
        <select id="gar-status" v-model="filtros.status" class="h-9 w-full rounded-md border bg-background px-2 text-sm">
          <option v-for="s in FILTROS_STATUS" :key="s.value" :value="s.value">{{ s.label }}</option>
        </select>
      </div>
      <div v-if="aba === 'garantia' && !abertoPor" class="w-[calc(50%-0.25rem)] sm:w-52">
        <label for="gar-mostrar" class="mb-1 block text-xs font-medium text-muted-foreground">Mostrar</label>
        <select id="gar-mostrar" v-model="mostrar" class="h-9 w-full rounded-md border bg-background px-2 text-sm" data-mostrar>
          <option value="todas">todas as vendas com nota</option>
          <option value="acionada">garantia acionada</option>
        </select>
      </div>
      <Button v-if="temFiltro" size="sm" variant="ghost" class="h-9" @click="limparFiltros">limpar filtros</Button>
    </div>
    <p v-if="aba === 'garantia' && buscaParcialDeCpf" class="-mt-2 text-xs text-muted-foreground">Para buscar por CPF, digite os 11 dígitos (pedaço de CPF não busca).</p>

    <!-- Resumo (só admin): a visão por produto do departamento da pílula -->
    <GarantiaResumo
      v-if="aba === 'resumo' && acesso.resumo"
      v-model:busca="resumoBusca"
      v-model:ordem="resumoOrdem"
      :filtros="filtros"
      :departamento="departamento"
      :versao="versaoResumo"
      @abrir="abrirProduto"
    />

    <template v-else>
      <template v-if="aba === 'garantia'">
        <!-- SAC do site que o robô não ligou a nenhuma venda: ligar à mão -->
        <div v-if="sacSemPedido > 0 && !abertoPor" class="flex flex-wrap items-center gap-2 rounded-lg border border-amber-300/70 bg-amber-50/60 px-3 py-2 text-sm dark:border-amber-800/60 dark:bg-amber-900/20" data-sac-sem-pedido>
          <AlertCircle class="size-4 shrink-0 text-amber-700 dark:text-amber-300" />
          <span>O SAC do site tem formulários que não acharam a venda.</span>
          <Button size="sm" variant="outline" class="h-7" :aria-expanded="sacSemPedidoAberto" data-sac-sem-pedido-botao @click="sacSemPedidoAberto = !sacSemPedidoAberto">SAC sem pedido ({{ numeroBR(sacSemPedido) }})</Button>
        </div>
        <GarantiaSacSemPedido v-if="sacSemPedidoAberto && sacSemPedido > 0 && !abertoPor" @fechar="sacSemPedidoAberto = false" @vinculado="aoVincularSac" />
        <!-- indicadores (clicar filtra) -->
        <div class="grid grid-cols-2 gap-2 lg:grid-cols-4" data-indicadores>
          <button
            v-for="c in CARTOES"
            :key="c.chave"
            type="button"
            class="rounded-xl border bg-card p-3 text-left transition-colors hover:bg-muted/50 sm:p-4"
            :class="ativo === c.chave ? 'border-primary ring-1 ring-primary' : ''"
            :aria-pressed="ativo === c.chave"
            :title="`${c.dica} — clique para ${ativo === c.chave ? 'tirar o filtro' : 'filtrar'}`"
            :data-cartao="c.chave"
            @click="clicarCartao(c.chave)"
          >
            <div class="truncate text-xs font-medium uppercase tracking-wider text-muted-foreground">{{ c.rotulo }}</div>
            <div class="mt-1 text-2xl font-semibold tabular-nums" :class="CARTAO_ALERTA.includes(c.chave) && (indicadores?.[STATUS_CARTAO[c.chave]] || 0) > 0 ? 'text-amber-600 dark:text-amber-400' : ''">
              {{ indicadores ? indicadores[STATUS_CARTAO[c.chave]] : '—' }}
            </div>
          </button>
        </div>
        <p v-if="indicadores" class="-mt-1 text-xs text-muted-foreground" data-resumo-linha>
          <button type="button" class="underline-offset-2 hover:underline" data-total-acionadas @click="mostrar = 'acionada'">{{ numeroBR(indicadores.acionadas) }} {{ indicadores.acionadas === 1 ? 'garantia acionada' : 'garantias acionadas' }}</button> em {{ numeroBR(indicadores.total) }} {{ indicadores.total === 1 ? 'venda com nota' : 'vendas com nota' }} ·
          <button type="button" class="underline-offset-2 hover:underline" @click="filtros.status = 'aguardando_entrega'; filtros.com_atendimento_no_mes = false">{{ indicadores.aguardando_entrega }} sem data de entrega</button>
          <template v-if="indicadores.entregue_sem_data">
            (<button type="button" class="underline-offset-2 hover:underline" data-entregue-sem-data @click="filtros.status = 'entregue_sem_data'; filtros.com_atendimento_no_mes = false">{{ indicadores.entregue_sem_data }} já {{ indicadores.entregue_sem_data === 1 ? 'entregue' : 'entregues' }} no Bling</button>)
          </template> ·
          <button type="button" class="underline-offset-2 hover:underline" @click="filtros.status = 'expirada'; filtros.com_atendimento_no_mes = false">{{ indicadores.expiradas }} expiradas</button>
          <template v-if="indicadores.sem_garantia"> ·
            <button type="button" class="underline-offset-2 hover:underline" title="produto vendido sem garantia (prazo 0 na Tabela de Preços › Produtos)" data-sem-garantia @click="filtros.status = 'sem_garantia'; filtros.com_atendimento_no_mes = false">{{ numeroBR(indicadores.sem_garantia) }} sem garantia</button>
          </template>
        </p>
      </template>
      <p v-else class="-mt-1 text-xs text-muted-foreground" data-atendimento-explica>
        Só os casos do SAC do site com a garantia acionada (o Confirmar da aba Garantia) — o mais antigo em cima; a resposta é por e-mail. Sai quando alguém encerra ou quando o caso termina sozinho.
      </p>

      <p v-if="erro" role="alert" class="flex items-center gap-2 text-sm text-destructive">
        <AlertCircle class="size-4" /> {{ erro }} <button type="button" class="underline" @click="carregar()">tentar de novo</button>
      </p>

      <!-- a lista de Conversas aberta na aba Garantia: de onde ela veio e o caminho de volta -->
      <p v-if="aba === 'garantia' && abertoPor" class="-mt-1 flex flex-wrap items-center gap-2 text-xs" :data-produto-aberto="abertoPor === 'produto' ? '' : undefined" data-lista-aberta>
        <span class="rounded-full border border-primary/40 bg-primary/5 px-2 py-0.5">
          Produto: <strong>{{ produtoAberto || 'escolhido' }}</strong><template v-if="carregado"> · {{ total }} {{ filtros.ids?.length ? (total === 1 ? 'pedido de garantia' : 'pedidos de garantia') : (total === 1 ? 'garantia' : 'garantias') }}</template>
        </span>
        <button type="button" class="text-primary underline-offset-2 hover:underline" data-voltar-tabela @click="voltarParaTabela">voltar para a tabela</button>
        <button v-if="acesso.resumo" type="button" class="text-primary underline-offset-2 hover:underline" data-voltar-resumo @click="voltarParaResumo">voltar para o resumo</button>
      </p>

      <!-- carregando / pedido sem garantia / vazio -->
      <div v-if="!carregado && !erro" class="flex items-center justify-center gap-2 rounded-lg border py-14 text-sm text-muted-foreground" data-carregando>
        <Loader2 class="size-4 animate-spin" /> carregando garantias…
      </div>
      <div v-else-if="cartaoSemGarantia" class="space-y-3 rounded-lg border border-amber-300/70 bg-amber-50/60 p-4 text-sm dark:border-amber-800/60 dark:bg-amber-900/20" data-pedido-sem-garantia>
        <p data-pedido-sem-garantia-texto>{{ textoPedidoSemGarantia(cartaoSemGarantia) }}</p>
        <Button v-if="cartaoSemGarantia.pode_cadastrar" size="sm" data-cadastrar-garantia @click="novaGarantia(cartaoSemGarantia.pedido_bling)">
          <Plus class="mr-1 size-4" /> Cadastrar garantia
        </Button>
      </div>
      <EmptyState
        v-else-if="carregado && !itens.length && !(emConversas && selecionadaLinha)"
        :icon="aba === 'atendimento' ? Headset : ShieldCheck"
        :title="vazio.titulo"
        :description="vazio.descricao"
        data-vazio
      >
        <div class="flex flex-wrap justify-center gap-2">
          <Button v-if="vazio.mostrarTodas" size="sm" variant="outline" data-mostrar-todas @click="mostrar = 'todas'">mostrar todas as vendas com nota</Button>
          <template v-if="aba === 'garantia'">
            <!-- a mesma busca na outra pílula (uma leitura, como trocar a pílula em cima) -->
            <template v-if="vazio.buscaNosOutros">
              <Button v-for="d in outrosDepartamentos" :key="d.chave" size="sm" variant="outline" :data-buscar-em="d.chave" @click="trocarDepartamento(d.chave)">procurar em {{ d.nome }}</Button>
            </template>
            <Button v-if="temFiltro" size="sm" variant="outline" @click="limparFiltros">limpar filtros</Button>
            <Button v-else-if="acesso.cadastra && departamento !== 'mala'" size="sm" @click="novaGarantia()"><Plus class="mr-1 size-4" /> Nova garantia</Button>
          </template>
        </div>
      </EmptyState>

      <!-- lista de Conversas: lista à esquerda, histórico à direita (como o Atendimento) -->
      <div v-else-if="carregado && emConversas" class="grid gap-3 md:h-[calc(100vh-15rem)] md:min-h-[520px] md:grid-cols-[minmax(280px,360px)_1fr]" data-vista-conversas>
        <div class="flex min-h-0 flex-col overflow-hidden rounded-lg border bg-card" :class="historicoNoCelular ? 'hidden md:flex' : ''">
          <ul class="min-h-0 flex-1 divide-y overflow-y-auto" data-lista-conversas>
            <li v-for="g in listaDeConversas" :key="g.id">
              <button
                type="button"
                class="flex w-full gap-2.5 px-3 py-2.5 text-left hover:bg-muted/50"
                :class="selecionadaId === g.id ? 'bg-primary/5 ring-1 ring-inset ring-primary/40' : ''"
                :aria-current="selecionadaId === g.id"
                :data-garantia-id="g.id"
                @click="selecionar(g.id)"
              >
                <AtendimentoIconePlataforma :plataforma="g.ultima_plataforma || g.plataforma || ''" :tamanho="28" decorativo class="mt-0.5 shrink-0" />
                <div class="min-w-0 flex-1">
                  <!-- o nome inteiro (06/11/2026: "sem cortar com reticências"): a data quebra a linha -->
                  <div class="flex flex-wrap items-baseline gap-x-1.5">
                    <span class="break-words text-sm font-medium" data-nome>{{ nomeDaLinha(g) }}</span>
                    <Sparkles v-if="g.origem === 'robo'" class="size-3 shrink-0 text-violet-500" aria-label="criada sozinha pela NF" />
                    <span class="ml-auto shrink-0 text-[10px] tabular-nums text-muted-foreground">{{ g.ultima_mensagem_em ? dataHoraBR(g.ultima_mensagem_em) : dataCurta(g.criado_em) }}</span>
                  </div>
                  <div class="truncate text-xs text-muted-foreground" data-pedido-bling>pedido {{ g.pedido_bling }}<template v-if="g.plataforma"> · {{ nomePlataforma(g.plataforma) }}</template><template v-if="g.conta"> · {{ g.conta }}</template></div>
                  <div v-if="g.sac" class="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-[11px]" data-sac-da-lista>
                    <span class="font-medium">{{ tempoEmAberto(g.sac, hoje) }}</span>
                    <span class="text-muted-foreground">· SAC {{ g.sac.protocolo }} · {{ dataBR(g.sac.recebido_em) }}</span>
                  </div>
                  <div v-if="g.ultima_previa" class="mt-0.5 truncate text-xs" :class="g.ultima_autor === 'cliente' ? 'text-foreground' : 'text-muted-foreground'">{{ g.ultima_autor === 'loja' ? 'Loja: ' : g.ultima_autor === 'mediador' ? 'Mediador: ' : '' }}{{ g.ultima_previa }}</div>
                  <div class="mt-1 flex flex-wrap items-center gap-1.5">
                    <!-- em atendimento: o motivo e há quanto tempo (o vencido no tom de erro) -->
                    <span
                      v-if="g.atendimento"
                      class="inline-block max-w-full truncate rounded-full border px-2 py-0.5 text-[10px] font-medium"
                      :class="g.atendimento.vencido ? 'border-destructive/40 bg-destructive/10 text-destructive' : 'border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-500/40 dark:bg-amber-500/15 dark:text-amber-300'"
                      :title="`em atendimento: ${seloDoAtendimento(g.atendimento, hoje)}`"
                      :data-atendimento="g.atendimento.motivo"
                    >{{ seloDoAtendimento(g.atendimento, hoje) }}</span>
                    <GarantiaStatus :status="g.status" :entregue-sem-data="g.entregue_sem_data" />
                    <GarantiaAparelho v-if="!aparelhoRepetido(g)" :situacao="g.devolucao_situacao" :rotulo="g.devolucao_rotulo" :vencido="g.devolucao_vencido" />
                    <span v-if="g.conversas" class="text-[10px] text-muted-foreground">{{ g.conversas }} {{ g.conversas === 1 ? 'conversa' : 'conversas' }}</span>
                    <span v-if="g.atendimentos" class="text-[10px] text-muted-foreground">· {{ g.atendimentos }} atend.</span>
                  </div>
                </div>
              </button>
            </li>
          </ul>
          <div class="flex items-center justify-between gap-2 border-t px-3 py-2 text-xs text-muted-foreground">
            <span>{{ itens.length }} de {{ total }}</span>
            <Button v-if="itens.length < total" size="sm" variant="outline" class="h-7" :disabled="carregandoMais" @click="carregarMais">
              <Loader2 v-if="carregandoMais" class="mr-1 size-3.5 animate-spin" /> mais {{ Math.min(LIMITE_PAGINA, total - itens.length) }}
            </Button>
          </div>
        </div>
        <div class="min-h-[60vh] md:min-h-0" :class="historicoNoCelular ? '' : 'hidden md:block'">
          <Button size="sm" variant="ghost" class="mb-2 md:hidden" @click="historicoNoCelular = false"><ArrowLeft class="mr-1 size-4" /> garantias</Button>
          <GarantiaHistorico
            v-if="selecionada"
            :garantia="selecionada"
            :versao="versaoHistorico"
            :automatico="regras.vinculo_automatico"
            :hoje="hoje"
            class="h-[70vh] md:h-full"
            @detalhes="abrir"
            @mudou="aoMudarCaso"
          />
        </div>
      </div>

      <template v-else-if="carregado">
        <!-- tabela (tela larga): as 10 colunas de 06/11/2026, na ordem da especificação -->
        <div class="hidden overflow-x-auto rounded-lg border md:block">
          <table ref="tabelaTopo" class="w-full text-sm" data-tabela-garantias>
            <thead class="bg-muted">
              <tr class="text-left">
                <th class="px-3 py-2.5 font-medium" title="o nome do comprador na venda, sem o apelido">Nome</th>
                <th class="px-3 py-2.5 font-medium" title="o número do pedido no Bling">Pedido</th>
                <th class="px-3 py-2.5 font-medium">Plataforma</th>
                <th class="px-3 py-2.5 font-medium" title="a conta que vendeu">Loja</th>
                <th class="px-3 py-2.5 font-medium" title="o nome do produto na Tabela de Preços › Produtos, pelo SKU">Produto</th>
                <th class="px-3 py-2.5 font-medium" title="a data de entrega (o início da garantia)">Entrega</th>
                <th class="px-3 py-2.5 font-medium" title="o prazo da garantia do produto (Tabela de Preços › Produtos)">Garantia</th>
                <th class="whitespace-nowrap px-3 py-2.5 font-medium" title="os dias que faltam da garantia, contados da entrega pelo prazo do produto (a data final fica no balão)">Prazo de garantia</th>
                <th class="whitespace-nowrap px-3 py-2.5 font-medium" title="a situação do pedido no Bling">Status do pedido</th>
                <th class="px-3 py-2.5 font-medium" title="o SAC do site: protocolo e Confirmar; confirmado, Garantia acionada">Atendimento</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="g in itens"
                :key="g.id"
                tabindex="0"
                class="cursor-pointer border-t align-top hover:bg-muted/40 focus:outline-none focus-visible:bg-muted/60"
                :class="abertaId === g.id ? 'bg-muted/50' : ''"
                :aria-label="`abrir a garantia de ${nomeDaLinha(g)}`"
                :data-linha-garantia="g.id"
                @click="abrir(g.id)"
                @keydown.enter="abrir(g.id)"
              >
                <td class="px-3 py-2.5 font-medium" data-nome>{{ nomeDaLinha(g) }}</td>
                <td class="whitespace-nowrap px-3 py-2.5 font-mono text-xs" data-pedido-bling>{{ g.pedido_bling }}</td>
                <td class="whitespace-nowrap px-3 py-2.5 text-xs" data-plataforma>{{ nomePlataforma(g.plataforma) || '—' }}</td>
                <td class="max-w-[180px] truncate px-3 py-2.5 text-xs" :title="g.conta || ''" data-loja>{{ g.conta || '—' }}</td>
                <td class="max-w-[220px] px-3 py-2.5 text-xs" data-produto>
                  <span class="break-words">{{ g.produto_nome || '—' }}</span>
                  <span v-if="g.produto_nome && g.produto_na_tabela === false" class="ml-1 inline-flex items-center gap-0.5 rounded-full border border-amber-300 bg-amber-50 px-1.5 py-px text-[10px] text-amber-800 dark:border-amber-500/40 dark:bg-amber-500/15 dark:text-amber-300" title="o SKU não está na Tabela de Preços › Produtos: o nome é o da nota fiscal" data-produto-fora>fora da Tabela</span>
                </td>
                <td class="whitespace-nowrap px-3 py-2.5 tabular-nums" data-entrega>{{ g.data_inicio ? dataCurta(g.data_inicio) : g.entregue_sem_data ? 'sem data' : '—' }}</td>
                <!-- 06/11/2026 (Eduardo: "aqui vai garantia e prazo de garantia, baseado nela"):
                     Garantia = o prazo do produto; Prazo de garantia = os dias que faltam por ele. -->
                <td class="whitespace-nowrap px-3 py-2.5 tabular-nums" data-garantia>
                  <span v-if="vendidoSemGarantia(g)" class="text-muted-foreground" title="o produto foi vendido sem garantia (prazo 0 na Tabela de Preços › Produtos)" data-sem-garantia>sem garantia</span>
                  <template v-else-if="g.garantia_meses">{{ textoMeses(g.garantia_meses) }}</template>
                  <span v-else class="text-amber-700 dark:text-amber-300" title="prazo não cadastrado na Tabela de Preços › Produtos" data-sem-prazo>— <AlertCircle class="inline size-3.5" aria-label="prazo não cadastrado" /></span>
                </td>
                <td class="whitespace-nowrap px-3 py-2.5 tabular-nums" data-prazo-garantia>
                  <span v-if="g.garantia_meses" :class="TOM_DIAS[diasDaGarantia(g, hoje).tom]" :title="diasDaGarantia(g, hoje).titulo" data-garantia-dias>{{ diasDaGarantia(g, hoje).texto }}</span>
                  <template v-else>—</template>
                </td>
                <td class="whitespace-nowrap px-3 py-2.5 text-xs" data-status-pedido>{{ g.status_pedido || '—' }}</td>
                <td class="px-3 py-2.5 text-xs" data-atendimento-sac>
                  <template v-if="situacaoDoSac(g.sac) === 'recebido' && g.sac">
                    <div class="whitespace-nowrap font-mono">{{ g.sac.protocolo }}</div>
                    <div class="whitespace-nowrap text-muted-foreground">{{ dataBR(g.sac.recebido_em) }}</div>
                    <Button v-if="acesso.registra" size="sm" class="mt-1 h-7" :disabled="confirmando === g.sac.id" data-confirmar-sac @click.stop="confirmar(g)" @keydown.enter.stop>
                      <Loader2 v-if="confirmando === g.sac.id" class="mr-1 size-3.5 animate-spin" /> Confirmar
                    </Button>
                  </template>
                  <template v-else-if="situacaoDoSac(g.sac) === 'acionada' && g.sac">
                    <span class="inline-block whitespace-nowrap rounded-full border border-emerald-400/60 bg-emerald-50 px-2 py-0.5 text-[10px] font-medium text-emerald-800 dark:border-emerald-500/40 dark:bg-emerald-500/15 dark:text-emerald-300" :title="`${g.sac.protocolo} · confirmado em ${dataHoraBR(g.sac.confirmado_em)}${g.sac.confirmado_por ? ` por ${g.sac.confirmado_por.nome}` : ''}`" data-garantia-acionada>Garantia acionada</span>
                    <div>
                      <button type="button" class="whitespace-nowrap text-primary underline-offset-2 hover:underline" data-abrir-caso @click.stop="abrirCaso(g)" @keydown.enter.stop>{{ g.sac.encerrado_em ? 'ver o caso (encerrado)' : 'abrir o caso' }}</button>
                    </div>
                  </template>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- cartões (celular): os mesmos dados, o Atendimento fora do botão da linha -->
        <ul class="space-y-2 md:hidden" data-cartoes-garantias>
          <li v-for="g in itens" :key="g.id" class="rounded-lg border bg-card">
            <button type="button" class="w-full space-y-1.5 p-3 text-left hover:bg-muted/40" @click="abrir(g.id)">
              <div class="flex items-start gap-2">
                <div class="min-w-0 flex-1">
                  <div class="break-words font-medium" data-nome>{{ nomeDaLinha(g) }}</div>
                  <div class="text-xs text-muted-foreground" data-loja>pedido {{ g.pedido_bling }} · {{ [nomePlataforma(g.plataforma), g.conta].filter(Boolean).join(' · ') || '—' }}</div>
                </div>
                <span class="shrink-0 text-xs text-muted-foreground" data-status-pedido>{{ g.status_pedido || '—' }}</span>
              </div>
              <div class="text-xs" data-produto>
                {{ g.produto_nome || '—' }}<span v-if="g.produto_nome && g.produto_na_tabela === false" class="ml-1 text-[10px] text-amber-700 dark:text-amber-300" data-produto-fora>(fora da Tabela)</span>
              </div>
              <div class="grid grid-cols-3 gap-1 text-xs">
                <div><div class="text-muted-foreground">Entrega</div><div class="tabular-nums">{{ dataCurta(g.data_inicio) }}</div></div>
                <div><div class="text-muted-foreground">Garantia</div><div class="tabular-nums" :class="vendidoSemGarantia(g) ? 'text-muted-foreground' : g.garantia_meses ? '' : 'text-amber-700 dark:text-amber-300'" :data-sem-garantia="vendidoSemGarantia(g) ? '' : undefined">{{ vendidoSemGarantia(g) ? 'sem garantia' : g.garantia_meses ? textoMeses(g.garantia_meses) : '— prazo não cadastrado' }}</div></div>
                <div><div class="text-muted-foreground">Prazo de garantia</div><div class="tabular-nums" :class="g.garantia_meses ? TOM_DIAS[diasDaGarantia(g, hoje).tom] : ''">{{ g.garantia_meses ? diasDaGarantia(g, hoje).texto : '—' }}</div></div>
              </div>
            </button>
            <div v-if="g.sac" class="flex flex-wrap items-center gap-2 border-t px-3 py-2 text-xs" data-atendimento-sac>
              <template v-if="situacaoDoSac(g.sac) === 'recebido'">
                <span class="font-mono">{{ g.sac.protocolo }}</span>
                <span class="text-muted-foreground">{{ dataBR(g.sac.recebido_em) }}</span>
                <Button v-if="acesso.registra" size="sm" class="ml-auto h-7" :disabled="confirmando === g.sac.id" data-confirmar-sac @click.stop="confirmar(g)">Confirmar</Button>
              </template>
              <template v-else>
                <span class="rounded-full border border-emerald-400/60 bg-emerald-50 px-2 py-0.5 text-[10px] font-medium text-emerald-800 dark:border-emerald-500/40 dark:bg-emerald-500/15 dark:text-emerald-300" data-garantia-acionada>Garantia acionada</span>
                <button type="button" class="ml-auto text-primary underline-offset-2 hover:underline" data-abrir-caso @click="abrirCaso(g)">{{ g.sac.encerrado_em ? 'ver o caso (encerrado)' : 'abrir o caso' }}</button>
              </template>
            </div>
          </li>
        </ul>

        <div v-if="paginado" class="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground" data-paginacao>
          <span>{{ numeroBR(total ? (pagina - 1) * LIMITE_PAGINA + 1 : 0) }}–{{ numeroBR((pagina - 1) * LIMITE_PAGINA + itens.length) }} de {{ numeroBR(total) }}</span>
          <div v-if="totalPaginas > 1" class="flex items-center gap-2">
            <button type="button" class="inline-flex items-center rounded-md border px-2 py-1 text-sm hover:bg-muted disabled:opacity-50" :disabled="pagina <= 1 || carregando" aria-label="página anterior" data-pagina-anterior @click="irParaPagina(pagina - 1)">
              <ChevronLeft class="h-4 w-4" />
            </button>
            <span class="text-sm" data-pagina>Página {{ numeroBR(pagina) }} de {{ numeroBR(totalPaginas) }}</span>
            <button type="button" class="inline-flex items-center rounded-md border px-2 py-1 text-sm hover:bg-muted disabled:opacity-50" :disabled="pagina >= totalPaginas || carregando" aria-label="próxima página" data-pagina-proxima @click="irParaPagina(pagina + 1)">
              <ChevronRight class="h-4 w-4" />
            </button>
          </div>
        </div>
        <div v-else class="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
          <span>{{ itens.length }} de {{ total }}</span>
          <Button v-if="itens.length < total" size="sm" variant="outline" :disabled="carregandoMais" @click="carregarMais">
            <Loader2 v-if="carregandoMais" class="mr-1 size-4 animate-spin" /> carregar mais {{ Math.min(LIMITE_PAGINA, total - itens.length) }}
          </Button>
        </div>
      </template>
    </template>

    <GarantiaDetalhe
      v-if="abertaId !== null && !formAberto"
      :garantia-id="abertaId"
      :regras="regras"
      :avisos="avisosDaAberta"
      :versao="versaoDetalhe"
      @fechar="fecharDetalhe"
      @corrigir="corrigir"
      @mudou="aoMudarDetalhe"
    />
    <GarantiaForm
      v-if="formAberto"
      :garantia="corrigindo"
      :pedido-inicial="pedidoInicial"
      :regras="regras"
      @fechar="formAberto = false; corrigindo = null"
      @salva="aoSalvar"
      @abrir="abrirDoForm"
    />
  </div>
</template>
