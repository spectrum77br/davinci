<script setup lang="ts">
// Pós-venda › Garantias (07/10/2026) — Painel de Garantia Uranyx.
// Documento "Painel de Garantia — Uranyx (DaVinci)", tela do §4.1:
// [+ Nova garantia], busca (Nome / CPF / NF / Pedido), Status, Período, os 4
// indicadores (Ativas · Só software · HW vence em 30d · Atendimentos no mês —
// clicar filtra) e a tabela Nome | CPF (mascarado) | NF | Entrega | Fim HW |
// Fim SW | Status | Atend. Clicar na linha abre o detalhe (§4.2).
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
import type { Component } from 'vue'
import type { LocationQueryRaw } from 'vue-router'
import { AlertCircle, ArrowLeft, BarChart3, Briefcase, Headset, Info, Loader2, Plus, RefreshCw, Search, ShieldCheck, Smartphone, Sparkles, Zap } from 'lucide-vue-next'
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
  lojaDaGarantia,
  nomePlataforma,
  numeroBR,
  rotuloDoPedido,
  rotuloDoPedidoComTrecho,
  seloDoAtendimento,
  textoNf,
  textoPedidoSemGarantia,
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
const NOTA_MALA = 'A garantia do DaVinci hoje é só da linha Uranyx (celular e eletro).'

// A lista de Conversas na aba Garantia: aberta pela linha do Resumo (o
// produto: todas as garantias dele), pelo número de uma coluna (os pedidos
// dela) ou pelo "ver todos os pedidos de garantia". A aba Atendimento é
// sempre a lista de Conversas.
const abertoPor = ref<'produto' | 'todos' | null>(null)
const produtoAberto = ref('')
const emConversas = computed(() => aba.value === 'atendimento' || (aba.value === 'garantia' && abertoPor.value !== null))
// O "Mostrar" da Tabela: todas as vendas com nota (o padrão da aba Garantia)
// ou só os pedidos de garantia. A busca acha todas.
const mostrar = ref<'pedidos' | 'todas'>('todas')

// ─── lista ──────────────────────────────────────────────────────────────────
// Nasce com o `ids` e o `produto` já criados — a 1ª troca não relê a lista a
// mais (revisão 29/10).
const filtros = reactive<FiltrosLista>({ ...FILTROS_PADRAO, ordem: 'cadastro', com_conversa: false, origem: 'todas', mostrar: 'todas', ids: undefined, produto: null, departamento: departamento.value })
function aplicarVista() {
  filtros.departamento = departamento.value
  filtros.ordem = emConversas.value ? 'atividade' : 'cadastro'
  filtros.com_conversa = false
  // O "Mostrar" é da Tabela. Nas Conversas: o produto do Resumo (a linha ou a
  // coluna) traz todas as que vieram; o "ver todos" traz os pedidos.
  filtros.mostrar = abertoPor.value === 'todos' ? 'pedidos' : abertoPor.value === 'produto' ? 'todas' : mostrar.value
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
const carregando = ref(false)
const carregandoMais = ref(false)
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
    const limite = trocouFiltro ? LIMITE_PAGINA : Math.min(500, Math.max(LIMITE_PAGINA, itens.value.length))
    const r = await api<GarantiaLista>('/api/garantias/lista', { method: 'POST', body: corpo(0, limite) })
    if (g !== geracao) return
    itens.value = r.itens
    total.value = r.total
    indicadores.value = r.indicadores
    hoje.value = r.hoje || undefined
    if (r.abas) abas.value = r.abas
    pedidoSemGarantia.value = r.pedido_sem_garantia ?? null
    carregado.value = true
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
function entrarNasConversas(por: 'produto' | 'todos', alvo: { produto?: string | null; ids?: number[]; nome?: string } = {}) {
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
    // O produto aberto é do outro departamento; "todos os pedidos" continua
    // (os do departamento novo).
    if (abertoPor.value === 'produto') fecharConversas()
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
// abertos volta para todos os pedidos — na mesma releitura. O produto (a
// linha) é um filtro de verdade: fica.
function soltarIds() {
  if (!filtros.ids?.length) return
  abertoPor.value = 'todos'
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
// Os filtros que a aba mostra: o Resumo tem status e período; a Garantia,
// também a busca e os cartões.
const temFiltro = computed(() => {
  const barra = filtros.status !== 'todos' || !!filtros.de || !!filtros.ate || (!!filtros.origem && filtros.origem !== 'todas')
  if (aba.value === 'resumo') return barra
  return barra || !!filtros.busca.trim() || filtros.com_atendimento_no_mes
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
// A busca e o cartão "Atendimentos no mês" (os da aba Garantia, que o Resumo
// não usa) saem: a lista aberta tem o que o número do Resumo contou.
function abrirProduto(p: { nome: string; chave?: string; ids?: number[]; coluna?: string }) {
  trocarDeVista(() => {
    filtros.busca = ''
    filtros.com_atendimento_no_mes = false
    aba.value = 'garantia'
    entrarNasConversas('produto', p.ids
      ? { ids: p.ids, nome: p.coluna ? `${p.nome} · ${p.coluna}` : p.nome }
      : { produto: p.chave ?? null, nome: p.nome })
  })
}
function verTodosOsPedidos() {
  trocarDeVista(() => {
    filtros.busca = ''
    filtros.com_atendimento_no_mes = false
    aba.value = 'garantia'
    entrarNasConversas('todos')
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
      titulo: 'Nada em atendimento agora neste departamento.',
      descricao: mala || 'Aparece aqui quem pediu garantia e ainda tem algo em aberto: no técnico, chegou sem condição, voltando, plataforma decidindo ou pedido de garantia que ainda não respondemos.',
      mostrarTodas: false,
      buscaNosOutros: false,
    }
  }
  const busca = !!filtros.busca.trim()
  const nome = DEPARTAMENTOS.find((d) => d.chave === departamento.value)?.nome || departamento.value
  const soPedidos = filtros.mostrar !== 'todas' && !busca
  const mostrarTodas = soPedidos && !abertoPor.value
  const titulo = soPedidos
    ? (temFiltro.value ? 'Nenhum pedido de garantia com esses filtros' : 'Nenhum pedido de garantia ainda')
    : busca
      ? `Nenhuma garantia com esses filtros em ${nome}`
      : temFiltro.value ? 'Nenhuma garantia com esses filtros' : 'Nenhuma garantia neste departamento ainda'
  const descricao = mostrarTodas
    ? 'Em "Mostrar" está "só pedidos de garantia": as vendas em que o cliente não pediu garantia ficam escondidas.'
    : busca
      ? `A busca procura só em ${nome}: se a garantia for de ${outrosDepartamentos.value.map((d) => d.nome).join(' ou ')}, procure na pílula dela. Ou mude a busca, o status ou o período.`
      : temFiltro.value
        ? 'Mude a busca, o status ou o período.'
        : soPedidos
          ? 'Ninguém pediu garantia ainda neste departamento.'
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
  toasts.success(era ? `Garantia #${g.id} corrigida` : `Garantia #${g.id} cadastrada`, g.data_inicio
    ? `Hardware até ${dataBR(g.fim_hardware)} · software até ${dataBR(g.fim_software)}.`
    : g.entregue_sem_data
      ? 'O pedido já consta como entregue no Bling, mas o DaVinci não tem a data de entrega: a garantia fica sem prazos.'
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
  somente_software: 'somente_software',
  hw_vence_30d: 'hw_vence_30d',
  atendimentos_no_mes: 'atendimentos_no_mes',
}
</script>

<template>
  <div class="space-y-4">
    <PageHeader title="Painel de Garantia — Uranyx" description="Garantias dos produtos Uranyx: prazos de hardware e software a partir da entrega e os atendimentos vinculados no Atendimento.">
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
        <li>A <strong class="text-foreground">data inicial</strong> é a data de entrega do pedido (não se edita). Hardware: +{{ regras.meses_hardware }} meses; software: +{{ regras.meses_software }} meses. Se o dia não existe no mês final, vale o último dia (30/11 → 28/02).</li>
        <li>{{ regras.ultimo_dia_coberto ? 'A garantia vale até a data final, inclusive.' : 'A garantia termina no dia anterior à data final.' }}</li>
        <li>{{ regras.cadastro_antes_da_entrega ? 'Pode cadastrar antes da entrega: fica Aguardando entrega e os prazos entram sozinhos quando a data aparecer.' : 'Só dá para cadastrar depois da entrega.' }}</li>
        <li>{{ regras.garantia_por === 'nf' ? 'Uma garantia por NF (o mesmo CPF não se repete na mesma NF); os itens da NF aparecem no detalhe.' : 'Uma garantia por produto.' }}</li>
        <li v-if="regras.vinculo_automatico">A garantia <strong class="text-foreground">cai sozinha</strong>: todo pedido com produto Uranyx que tem a nota fiscal de PRODUTO no DaVinci vira garantia (com o selo “criada sozinha pela NF”), com o nome e o CPF da nota. A nota de embalagem nunca vira garantia. Se faltar algo (CPF que é CNPJ, nota repetida), o pedido fica para o cadastro manual. As conversas do pedido (Shopee, TikTok, Mercado Livre, Amazon) entram sozinhas no histórico, com fotos e vídeos; o "Registrar atendimento" (tipo e solução) continua sendo da pessoa.</li>
        <li v-else>O vínculo com o Atendimento é manual: o atendente clica em "Vincular à garantia" e busca por CPF, NF ou pedido (a busca já vem com o pedido da conversa).</li>
        <li>{{ regras.recalcular_quando_entrega_mudar ? 'Se a data de entrega for corrigida, a garantia é recalculada sozinha (fica no log).' : 'A data de entrega corrigida depois não muda a garantia.' }}</li>
        <li>Status: Ativa (hardware e software) · Somente software · Expirada · Aguardando entrega — ou "Entregue — sem data no DaVinci", quando o Bling já dá o pedido como entregue mas nenhuma fonte tem a data (a Logística guarda a data só desde 15/07/2026). Na lista o CPF aparece mascarado.</li>
        <li>Sem escolher mensagens, a data do atendimento é a 1ª mensagem do cliente no trecho atual da conversa (uma pausa de até {{ regras.dias_pausa_atendimento }} dias não abre trecho novo).</li>
        <li><strong class="text-foreground">Resumo</strong>: só os produtos da Tabela de Preços › Produtos. A % é gasto ÷ saldo × 100 — saldo = base − comissão − frete − custo do produto, a conta da planilha de rentabilidade (o que sobrou), das vendas do produto; gasto = tela Reembolso (conserto + perdidos + reembolso ao cliente) de quem pediu garantia. Com o período pela data da venda, tudo é só das vendas daqueles dias. O que cada coluna conta aparece com o mouse no título dela.</li>
        <li><strong class="text-foreground">Atendimento</strong> = quem pediu garantia e ainda tem algo em aberto (no técnico, chegou sem condição, voltando, plataforma decidindo, pedido de garantia que ainda não respondemos) — sai sozinho quando termina. Conversa comum esperando a loja (um "obrigado", uma foto) não entra. O mais urgente fica em cima.</li>
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
        {{ d.nome }}<template v-if="abas"> ({{ numeroBR(abas[d.chave]?.garantias ?? 0) }} {{ abas[d.chave]?.garantias === 1 ? 'garantia' : 'garantias' }})</template>
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
    </div>
    </div>

    <!-- a barra: a busca e o "Mostrar" são da aba Garantia; o status vale
         também no Resumo (o período fica ao lado das abas; a Origem saiu em
         05/11/2026, "excluir origem também"). O Atendimento não tem filtro. -->
    <div v-if="aba !== 'atendimento'" class="flex flex-wrap items-end gap-2" data-barra>
      <div v-if="aba === 'garantia'" class="w-full sm:w-80">
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
          <option value="pedidos">só pedidos de garantia</option>
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
      @todos="verTodosOsPedidos"
    />

    <template v-else>
      <template v-if="aba === 'garantia'">
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
            <div class="mt-1 text-2xl font-semibold tabular-nums" :class="c.chave === 'hw_vence_30d' && (indicadores?.hw_vence_30d || 0) > 0 ? 'text-amber-600 dark:text-amber-400' : ''">
              {{ indicadores ? indicadores[STATUS_CARTAO[c.chave]] : '—' }}
            </div>
          </button>
        </div>
        <p v-if="indicadores" class="-mt-1 text-xs text-muted-foreground" data-resumo-linha>
          <span data-total-pedidos>{{ indicadores.total }} {{ indicadores.total === 1 ? 'pedido de garantia' : 'pedidos de garantia' }}<template v-if="indicadores.vendidas"> em {{ numeroBR(indicadores.vendidas) }} vendas com nota</template></span> ·
          <button type="button" class="underline-offset-2 hover:underline" @click="filtros.status = 'aguardando_entrega'; filtros.com_atendimento_no_mes = false">{{ indicadores.aguardando_entrega }} sem data de entrega</button>
          <template v-if="indicadores.entregue_sem_data">
            (<button type="button" class="underline-offset-2 hover:underline" data-entregue-sem-data @click="filtros.status = 'entregue_sem_data'; filtros.com_atendimento_no_mes = false">{{ indicadores.entregue_sem_data }} já {{ indicadores.entregue_sem_data === 1 ? 'entregue' : 'entregues' }} no Bling</button>)
          </template> ·
          <button type="button" class="underline-offset-2 hover:underline" @click="filtros.status = 'expirada'; filtros.com_atendimento_no_mes = false">{{ indicadores.expiradas }} expiradas</button>
          <template v-if="!abertoPor">
            · <button type="button" class="text-primary underline-offset-2 hover:underline" data-ver-todos-pedidos @click="verTodosOsPedidos">ver todos os pedidos de garantia</button>
          </template>
        </p>
      </template>
      <p v-else class="-mt-1 text-xs text-muted-foreground" data-atendimento-explica>
        Quem pediu garantia e ainda tem algo em aberto (no técnico, chegou sem condição, voltando, plataforma decidindo, pedido de garantia que ainda não respondemos) — o mais urgente em cima; sai sozinho quando termina.
      </p>

      <p v-if="erro" role="alert" class="flex items-center gap-2 text-sm text-destructive">
        <AlertCircle class="size-4" /> {{ erro }} <button type="button" class="underline" @click="carregar()">tentar de novo</button>
      </p>

      <!-- a lista de Conversas aberta na aba Garantia: de onde ela veio e o caminho de volta -->
      <p v-if="aba === 'garantia' && abertoPor" class="-mt-1 flex flex-wrap items-center gap-2 text-xs" :data-produto-aberto="abertoPor === 'produto' ? '' : undefined" data-lista-aberta>
        <span class="rounded-full border border-primary/40 bg-primary/5 px-2 py-0.5">
          <template v-if="abertoPor === 'produto'">Produto: <strong>{{ produtoAberto || 'escolhido' }}</strong><template v-if="carregado"> · {{ total }} {{ filtros.ids?.length ? (total === 1 ? 'pedido de garantia' : 'pedidos de garantia') : (total === 1 ? 'garantia' : 'garantias') }}</template></template>
          <template v-else>Todos os pedidos de garantia<template v-if="carregado"> · {{ total }}</template></template>
        </span>
        <button v-if="abertoPor === 'produto'" type="button" class="text-primary underline-offset-2 hover:underline" @click="verTodosOsPedidos">ver todos os pedidos</button>
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
        v-else-if="carregado && !itens.length"
        :icon="aba === 'atendimento' ? Headset : ShieldCheck"
        :title="vazio.titulo"
        :description="vazio.descricao"
        data-vazio
      >
        <div class="flex flex-wrap justify-center gap-2">
          <Button v-if="vazio.mostrarTodas" size="sm" variant="outline" data-mostrar-todas @click="mostrar = 'todas'">mostrar todas as vendas</Button>
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
            <li v-for="g in itens" :key="g.id">
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
                  <div class="flex items-baseline gap-1.5">
                    <span class="truncate text-sm font-medium">{{ g.cliente_nome }}</span>
                    <Sparkles v-if="g.origem === 'robo'" class="size-3 shrink-0 text-violet-500" aria-label="criada sozinha pela NF" />
                    <span class="ml-auto shrink-0 text-[10px] tabular-nums text-muted-foreground">{{ g.ultima_mensagem_em ? dataHoraBR(g.ultima_mensagem_em) : dataCurta(g.criado_em) }}</span>
                  </div>
                  <div class="truncate text-xs text-muted-foreground">pedido {{ g.pedido_bling }}<template v-if="g.plataforma"> · {{ nomePlataforma(g.plataforma) }}</template> · NF {{ textoNf(g.nf_numero, g.nf_serie) }}</div>
                  <div v-if="g.ultima_previa" class="mt-0.5 truncate text-xs" :class="g.ultima_autor === 'cliente' ? 'text-foreground' : 'text-muted-foreground'">{{ g.ultima_autor === 'loja' ? 'Loja: ' : g.ultima_autor === 'mediador' ? 'Mediador: ' : '' }}{{ g.ultima_previa }}</div>
                  <div class="mt-0.5 truncate text-[11px]" :class="g.pedido_em && !g.nao_e_pedido ? 'text-foreground' : 'text-muted-foreground'" :title="g.pedido_trecho || ''" data-pedido>{{ rotuloDoPedidoComTrecho(g) }}</div>
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
            class="h-[70vh] md:h-full"
            @detalhes="abrir"
            @mudou="carregar"
          />
        </div>
      </div>

      <template v-else-if="carregado">
        <!-- tabela (tela larga) -->
        <div class="hidden overflow-x-auto rounded-lg border md:block">
          <table class="w-full text-sm" data-tabela-garantias>
            <thead class="bg-muted">
              <tr class="text-left">
                <th class="px-3 py-2.5 font-medium">Nome</th>
                <th class="whitespace-nowrap px-3 py-2.5 font-medium">Pedido de garantia</th>
                <th class="px-3 py-2.5 font-medium">Loja</th>
                <th class="whitespace-nowrap px-3 py-2.5 font-medium">CPF</th>
                <th class="px-3 py-2.5 font-medium">NF</th>
                <th class="px-3 py-2.5 font-medium">Entrega</th>
                <th class="whitespace-nowrap px-3 py-2.5 font-medium">Fim HW</th>
                <th class="whitespace-nowrap px-3 py-2.5 font-medium">Fim SW</th>
                <th class="px-3 py-2.5 font-medium">Status</th>
                <th class="px-3 py-2.5 text-right font-medium">Atend.</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="g in itens"
                :key="g.id"
                tabindex="0"
                class="cursor-pointer border-t hover:bg-muted/40 focus:outline-none focus-visible:bg-muted/60"
                :class="abertaId === g.id ? 'bg-muted/50' : ''"
                :aria-label="`abrir a garantia de ${g.cliente_nome}`"
                @click="abrir(g.id)"
                @keydown.enter="abrir(g.id)"
              >
                <td class="px-3 py-2.5">
                  <div class="font-medium">{{ g.cliente_nome }}</div>
                  <div class="text-xs text-muted-foreground">pedido {{ g.pedido_bling }}</div>
                </td>
                <td class="whitespace-nowrap px-3 py-2.5 text-xs" :class="g.pedido_em && !g.nao_e_pedido ? '' : 'text-muted-foreground'" :title="g.pedido_trecho || ''" data-pedido>{{ rotuloDoPedido(g) }}</td>
                <td class="max-w-[220px] truncate px-3 py-2.5 text-xs" :title="lojaDaGarantia(g)" data-loja>{{ lojaDaGarantia(g) }}</td>
                <td class="whitespace-nowrap px-3 py-2.5 font-mono text-xs">{{ g.cpf_mascarado || '—' }}</td>
                <td class="whitespace-nowrap px-3 py-2.5 font-mono">{{ textoNf(g.nf_numero, g.nf_serie) }}</td>
                <td class="whitespace-nowrap px-3 py-2.5 tabular-nums">{{ dataCurta(g.data_inicio) }}</td>
                <td class="whitespace-nowrap px-3 py-2.5 tabular-nums">{{ dataCurta(g.fim_hardware) }}</td>
                <td class="whitespace-nowrap px-3 py-2.5 tabular-nums">{{ dataCurta(g.fim_software) }}</td>
                <td class="px-3 py-2.5"><GarantiaStatus :status="g.status" :entregue-sem-data="g.entregue_sem_data" /></td>
                <td class="px-3 py-2.5 text-right tabular-nums">{{ g.atendimentos }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- cartões (celular) -->
        <ul class="space-y-2 md:hidden" data-cartoes-garantias>
          <li v-for="g in itens" :key="g.id">
            <button type="button" class="w-full space-y-1.5 rounded-lg border bg-card p-3 text-left hover:bg-muted/40" @click="abrir(g.id)">
              <div class="flex items-start gap-2">
                <div class="min-w-0 flex-1">
                  <div class="truncate font-medium">{{ g.cliente_nome }}</div>
                  <div class="font-mono text-xs text-muted-foreground">{{ g.cpf_mascarado || '—' }} · NF {{ textoNf(g.nf_numero, g.nf_serie) }}</div>
                </div>
                <GarantiaStatus :status="g.status" :entregue-sem-data="g.entregue_sem_data" />
              </div>
              <div class="grid grid-cols-3 gap-1 text-xs">
                <div><div class="text-muted-foreground">Entrega</div><div class="tabular-nums">{{ dataCurta(g.data_inicio) }}</div></div>
                <div><div class="text-muted-foreground">Fim HW</div><div class="tabular-nums">{{ dataCurta(g.fim_hardware) }}</div></div>
                <div><div class="text-muted-foreground">Fim SW</div><div class="tabular-nums">{{ dataCurta(g.fim_software) }}</div></div>
              </div>
              <div class="text-xs" :class="g.pedido_em && !g.nao_e_pedido ? '' : 'text-muted-foreground'" data-pedido>{{ rotuloDoPedido(g) }}</div>
              <div class="truncate text-xs text-muted-foreground" data-loja>Loja: {{ lojaDaGarantia(g) }}</div>
              <div class="text-xs text-muted-foreground">pedido {{ g.pedido_bling }} · {{ g.atendimentos }} {{ g.atendimentos === 1 ? 'atendimento' : 'atendimentos' }}</div>
            </button>
          </li>
        </ul>

        <div class="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
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
