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
// Garantia automática (28/10/2026): a vista "Conversas" (o padrão) é a do
// Atendimento — a lista à esquerda (a garantia com mensagem mais nova em cima)
// e o histórico das conversas ligadas à direita, com fotos e vídeos. A vista
// "Tabela" é a de antes. O robô cria a garantia pela NF de produto; o selo
// ✨ marca as criadas sozinhas. Pedido de garantia (28/10/2026): o Painel
// mostra só quem PEDIU (mensagem, SAC, devolução por defeito, atendimento).
// 29/10/2026 (Eduardo: "esse filtro de conversas precisa ter? só vamos
// precisar quando eu clicar no produto"): a tela abre na garantia por produto;
// a lista de Conversas só abre pelo produto clicado, pela busca ou pelo "ver
// todos os pedidos" — sempre com o "voltar para a garantia por produto".
import { AlertCircle, ArrowLeft, Info, Loader2, Plus, RefreshCw, Search, ShieldCheck, Sparkles } from 'lucide-vue-next'
import {
  CARTOES,
  FILTROS_PADRAO,
  FILTROS_STATUS,
  LIMITE_PAGINA,
  REGRAS_PADRAO,
  cartaoAtivo,
  dataBR,
  dataCurta,
  dataHoraBR,
  errosDaApi,
  filtroDoCartao,
  nomePlataforma,
  corpoDaLista,
  numeroBR,
  pct,
  rotuloDoPedido,
  rotuloDoPedidoComTrecho,
  textoNf,
  type Cartao,
  type FiltrosLista,
  type GarantiaDetalhe,
  type GarantiaLinha,
  type GarantiaLista,
  type Indicadores,
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
  // Robô desligado: ninguém marca o pedido de garantia sozinho — "pedidos"
  // esconderia as garantias. Abre em "todas" (se a pessoa não escolheu).
  if (!regras.value.vinculo_automatico && !mostrarEscolhido.value) mostrar.value = 'todas'
}

// ─── vista: Garantia por produto (padrão) ou Tabela ─────────────────────────
// Resumo (28/10/2026): a garantia por produto, no visual da Tabela de Preços
// (GarantiaResumo). A vista Conversas não está no seletor (29/10/2026): abre
// pelo produto clicado, pela busca ou pelo "ver todos os pedidos" (abertoPor).
type Vista = 'conversas' | 'tabela' | 'resumo'
const CHAVE_VISTA = 'garantias.vista'
function vistaGuardada(): Vista {
  try {
    return localStorage.getItem(CHAVE_VISTA) === 'tabela' ? 'tabela' : 'resumo'
  } catch {
    return 'resumo'
  }
}
const vista = ref<Vista>('resumo')
// Por que a lista de Conversas está aberta: o produto clicado, a busca ou
// todos os pedidos de garantia.
const abertoPor = ref<'produto' | 'busca' | 'todos' | null>(null)
// Pedido de garantia (28/10/2026): o Painel é de quem PEDIU a garantia
// ("a garantia é só para o pessoal que solicita", Eduardo); "todas" mostra
// também a venda sem pedido. A busca acha todas.
const mostrar = ref<'pedidos' | 'todas'>('pedidos')
const mostrarEscolhido = ref(false)

// ─── lista ──────────────────────────────────────────────────────────────────
// Nasce como a vista de entrada (a garantia por produto: ordem 'cadastro') e
// com o `ids` já criado — a 1ª troca não relê a lista a mais (revisão 29/10).
const filtros = reactive<FiltrosLista>({ ...FILTROS_PADRAO, ordem: 'cadastro', com_conversa: false, origem: 'todas', ids: undefined })
function aplicarVista() {
  filtros.ordem = vista.value === 'conversas' ? 'atividade' : 'cadastro'
  filtros.com_conversa = false
  // O "Mostrar" é da Tabela; nas Conversas o produto/os pedidos são sempre
  // os pedidos de garantia (e a busca acha todas).
  filtros.mostrar = vista.value === 'tabela' ? mostrar.value : 'pedidos'
}
// A vista guarda a escolha (a lista de Conversas é um pedaço da garantia por
// produto: não fica guardada). Quem troca de vista relê a lista (trocarDeVista).
watch(vista, () => {
  aplicarVista()
  if (vista.value === 'conversas') return
  try {
    localStorage.setItem(CHAVE_VISTA, vista.value)
  } catch {
    // sem armazenamento local: vale só nesta visita
  }
})
// O "Mostrar" (é da Tabela): relê só quando muda o que a lista traz.
watch(mostrar, () => {
  const antes = filtros.mostrar
  aplicarVista()
  if (filtros.mostrar !== antes) void carregar({ trocouFiltro: true })
})
const itens = ref<GarantiaLinha[]>([])
const total = ref(0)
const indicadores = ref<Indicadores | null>(null)
const carregando = ref(false)
const carregandoMais = ref(false)
const carregado = ref(false)
const erro = ref('')
let geracao = 0

// `trocouFiltro`: a lista é outra (filtro, busca, vista) — a garantia aberta
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
    const r = await api<GarantiaLista>('/api/garantias/lista', { method: 'POST', body: corpoDaLista(filtros, 0, limite) })
    if (g !== geracao) return
    itens.value = r.itens
    total.value = r.total
    indicadores.value = r.indicadores
    carregado.value = true
    const achada = itens.value.find((x) => x.id === selecionadaId.value)
    if (achada) {
      selecionadaLinha.value = achada
    } else if (trocouFiltro || selecionadaId.value === null) {
      // A vista Conversas abre a primeira — na tela larga; no celular a lista
      // vem primeiro e nada é aberto (nem lido) sem a pessoa tocar. Nas outras
      // vistas a lista é só dos números: nada fica escolhido escondido.
      const primeira = vista.value === 'conversas' && telaLarga() ? itens.value[0] ?? null : null
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
    const r = await api<GarantiaLista>('/api/garantias/lista', { method: 'POST', body: corpoDaLista(filtros, itens.value.length) })
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

// As trocas de vista (abrir produto, ver todos, buscar, voltar, seletor, limpar)
// mexem em vários filtros de uma vez: nenhum watcher relê no meio e a lista é
// relida UMA vez, já com tudo trocado (revisão 29/10: a busca mandava 2-3
// pedidos — o CPF colado contava 2-3 vezes no teto e no log).
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
  void carregar({ trocouFiltro: true })
}
// A lista de Conversas que abre é outra: nada da anterior fica na tela nem
// aberto (nem lido) até ela chegar.
function entrarNasConversas(por: 'produto' | 'busca' | 'todos', ids?: number[], nome = '') {
  abertoPor.value = por
  produtoAberto.value = nome
  filtros.ids = ids
  selecionadaId.value = null
  selecionadaLinha.value = null
  historicoNoCelular.value = false
  itens.value = []
  carregado.value = false
  vista.value = 'conversas'
}
// A busca relê depois da pausa de 300 ms. Na garantia por produto, buscar
// (nome/CPF/NF/pedido) abre a lista com o que achou; apagar a busca volta para
// a garantia por produto. Na Tabela a busca filtra a própria tabela.
watch(() => filtros.busca, () => {
  if (atraso) clearTimeout(atraso)
  atraso = null
  if (emTroca) return
  atraso = setTimeout(buscaAssentou, 300)
})
function buscaAssentou() {
  atraso = null
  const termo = filtros.busca.trim()
  if (vista.value !== 'tabela') {
    if (termo && abertoPor.value !== 'busca') return trocarDeVista(() => entrarNasConversas('busca'))
    if (!termo && abertoPor.value === 'busca') return voltarParaProdutos()
  }
  void carregar({ trocouFiltro: true })
}
// Os filtros da barra relêem a lista. Os ids do produto são uma foto do
// Resumo com os filtros daquela hora: mexer num filtro da barra com o produto
// aberto volta para todos os pedidos — na mesma releitura.
watch(() => [filtros.status, filtros.periodo, filtros.de, filtros.ate, filtros.com_atendimento_no_mes, filtros.com_conversa, filtros.origem], () => {
  if (emTroca) return
  if (filtros.ids?.length) {
    if (vista.value === 'conversas') abertoPor.value = 'todos'
    produtoAberto.value = ''
    filtros.ids = undefined
  }
  void carregar({ trocouFiltro: true })
})

const ativo = computed(() => cartaoAtivo(filtros))
function clicarCartao(c: Cartao) {
  Object.assign(filtros, filtroDoCartao(filtros, c))
}
const temFiltro = computed(() => !!(filtros.busca.trim() || filtros.status !== 'todos' || filtros.de || filtros.ate || filtros.com_atendimento_no_mes || (filtros.origem && filtros.origem !== 'todas') || filtros.ids?.length))
function limparFiltros() {
  trocarDeVista(() => {
    Object.assign(filtros, { ...FILTROS_PADRAO, origem: 'todas', ids: undefined })
    produtoAberto.value = ''
    if (vista.value === 'conversas') {
      abertoPor.value = null
      vista.value = 'resumo'
    }
  })
}
// A garantia por produto: a linha abre os pedidos daquele produto na lista. O
// departamento, a busca e a ordem da tabela ficam aqui (voltam iguais).
const produtoAberto = ref('')
const resumoDepartamento = ref('')
const resumoBusca = ref('')
const resumoOrdem = ref({ coluna: 'pediram', desc: true })
function abrirProduto(p: { nome: string; ids: number[] }) {
  trocarDeVista(() => {
    filtros.busca = ''
    entrarNasConversas('produto', p.ids, p.nome)
  })
}
function verTodosOsPedidos() {
  trocarDeVista(() => {
    filtros.busca = ''
    entrarNasConversas('todos')
  })
}
function voltarParaProdutos() {
  trocarDeVista(() => {
    abertoPor.value = null
    produtoAberto.value = ''
    filtros.ids = undefined
    filtros.busca = ''
    selecionadaId.value = null
    selecionadaLinha.value = null
    historicoNoCelular.value = false
    vista.value = 'resumo'
  })
}
// O seletor de Visualização: a lista de Conversas é um pedaço da garantia por
// produto (o seletor continua nela); trocar para a Tabela fecha a lista.
const vistaDoSeletor = computed<'resumo' | 'tabela'>({
  get: () => (vista.value === 'tabela' ? 'tabela' : 'resumo'),
  set: (v) => {
    if (v === 'resumo') return voltarParaProdutos()
    trocarDeVista(() => {
      abertoPor.value = null
      produtoAberto.value = ''
      filtros.ids = undefined
      vista.value = 'tabela'
    })
  },
})
// O "ver todos os pedidos" do aviso do produto.
function fecharProduto() {
  trocarDeVista(() => entrarNasConversas('todos'))
}
const buscaParcialDeCpf = computed(() => {
  const d = filtros.busca.replace(/\D/g, '')
  return /^[\d.\-\s]+$/.test(filtros.busca.trim()) && d.length > 9 && d.length < 11
})

// ─── vista Conversas: a garantia aberta no histórico ───────────────────────
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
// "Pedidos de garantia" não vale para a busca (acha a venda sem pedido).
const soPedidos = computed(() => filtros.mostrar !== 'todas' && !filtros.busca.trim())
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

// A URL acompanha a garantia aberta (dá para mandar o link).
watch(abertaId, (id) => {
  const query = { ...route.query }
  if (id) query.garantia = String(id)
  else delete query.garantia
  delete query.novo
  void router.replace({ query })
})

onMounted(() => {
  vista.value = vistaGuardada()
  aplicarVista()
  void carregarRegras()
  void carregar()
  const id = Number(route.query.garantia)
  if (Number.isInteger(id) && id > 0) abrir(id)
  const novo = typeof route.query.novo === 'string' ? route.query.novo.trim() : ''
  if (novo) {
    if (acesso.value.cadastra) novaGarantia(novo)
    const query = { ...route.query }
    delete query.novo
    void router.replace({ query })
  }
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
        <li>A <strong class="text-foreground">data inicial</strong> é a data de entrega do pedido (não se edita). Hardware: +{{ regras.meses_hardware }} meses; software: +{{ regras.meses_software }} meses. Se o dia não existe no mês final, vale o último dia (30/11 → 28/02).</li>
        <li>{{ regras.ultimo_dia_coberto ? 'A garantia vale até a data final, inclusive.' : 'A garantia termina no dia anterior à data final.' }}</li>
        <li>{{ regras.cadastro_antes_da_entrega ? 'Pode cadastrar antes da entrega: fica 🟡 Aguardando entrega e os prazos entram sozinhos quando a data aparecer.' : 'Só dá para cadastrar depois da entrega.' }}</li>
        <li>{{ regras.garantia_por === 'nf' ? 'Uma garantia por NF (o mesmo CPF não se repete na mesma NF); os itens da NF aparecem no detalhe.' : 'Uma garantia por produto.' }}</li>
        <li v-if="regras.vinculo_automatico">A garantia <strong class="text-foreground">cai sozinha</strong>: todo pedido com produto Uranyx que tem a nota fiscal de PRODUTO no DaVinci vira garantia (✨), com o nome e o CPF da nota. A nota de embalagem nunca vira garantia. Se faltar algo (CPF que é CNPJ, nota repetida), o pedido fica para o cadastro manual. As conversas do pedido (Shopee, TikTok, Mercado Livre, Amazon) entram sozinhas no histórico, com fotos e vídeos; o "Registrar atendimento" (tipo e solução) continua sendo da pessoa.</li>
        <li v-else>O vínculo com o Atendimento é manual: o atendente clica em "Vincular à garantia" e busca por CPF, NF ou pedido (a busca já vem com o pedido da conversa).</li>
        <li>{{ regras.recalcular_quando_entrega_mudar ? 'Se a data de entrega for corrigida, a garantia é recalculada sozinha (fica no log).' : 'A data de entrega corrigida depois não muda a garantia.' }}</li>
        <li>Status: 🟢 Ativa (hardware e software) · 🔵 Somente software · 🔴 Expirada · 🟡 Aguardando entrega — ou 🟡 "Entregue — sem data no DaVinci", quando o Bling já dá o pedido como entregue mas nenhuma fonte tem a data (a Logística guarda a data só desde 15/07/2026). Na lista o CPF aparece mascarado.</li>
        <li>Sem escolher mensagens, a data do atendimento é a 1ª mensagem do cliente no trecho atual da conversa (uma pausa de até {{ regras.dias_pausa_atendimento }} dias não abre trecho novo).</li>
      </ul>
    </section>

    <!-- busca, status e período -->
    <div class="flex flex-wrap items-end gap-2">
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
      <div class="w-[calc(50%-0.25rem)] sm:w-56">
        <label for="gar-periodo" class="mb-1 block text-xs font-medium text-muted-foreground">Período por</label>
        <select id="gar-periodo" v-model="filtros.periodo" class="h-9 w-full rounded-md border bg-background px-2 text-sm">
          <option value="pedido">data do pedido de garantia</option>
          <option value="entrega">data de entrega</option>
          <option value="cadastro">data de cadastro</option>
        </select>
      </div>
      <div class="flex w-full items-end gap-2 sm:w-auto">
        <div class="flex-1 sm:w-36 sm:flex-none">
          <label for="gar-de" class="mb-1 block text-xs font-medium text-muted-foreground">De</label>
          <Input id="gar-de" v-model="filtros.de" type="date" class="h-9" :max="filtros.ate || undefined" />
        </div>
        <div class="flex-1 sm:w-36 sm:flex-none">
          <label for="gar-ate" class="mb-1 block text-xs font-medium text-muted-foreground">Até</label>
          <Input id="gar-ate" v-model="filtros.ate" type="date" class="h-9" :min="filtros.de || undefined" />
        </div>
      </div>
      <div class="w-[calc(50%-0.25rem)] sm:w-52">
        <label for="gar-vista" class="mb-1 block text-xs font-medium text-muted-foreground">Visualização</label>
        <select id="gar-vista" v-model="vistaDoSeletor" class="h-9 w-full rounded-md border bg-background px-2 text-sm" data-vista>
          <option value="resumo">Garantia por produto</option>
          <option value="tabela">Tabela</option>
        </select>
      </div>
      <div v-if="vista === 'tabela'" class="w-[calc(50%-0.25rem)] sm:w-52">
        <label for="gar-mostrar" class="mb-1 block text-xs font-medium text-muted-foreground">Mostrar</label>
        <select id="gar-mostrar" v-model="mostrar" class="h-9 w-full rounded-md border bg-background px-2 text-sm" data-mostrar @change="mostrarEscolhido = true">
          <option value="pedidos">pedidos de garantia</option>
          <option value="todas">todas as vendas com nota</option>
        </select>
      </div>
      <div class="w-[calc(50%-0.25rem)] sm:w-44">
        <label for="gar-origem" class="mb-1 block text-xs font-medium text-muted-foreground">Origem</label>
        <select id="gar-origem" v-model="filtros.origem" class="h-9 w-full rounded-md border bg-background px-2 text-sm" data-origem>
          <option value="todas">todas</option>
          <option value="robo">criadas sozinhas (NF)</option>
          <option value="manual">cadastradas na tela</option>
        </select>
      </div>
      <Button v-if="temFiltro" size="sm" variant="ghost" class="h-9" @click="limparFiltros">limpar filtros</Button>
    </div>
    <p v-if="buscaParcialDeCpf" class="-mt-2 text-xs text-muted-foreground">Para buscar por CPF, digite os 11 dígitos (pedaço de CPF não busca).</p>

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
    <p v-if="indicadores" class="-mt-1 text-xs text-muted-foreground">
      <span data-total-pedidos>{{ indicadores.total }} {{ indicadores.total === 1 ? 'pedido de garantia' : 'pedidos de garantia' }}<template v-if="indicadores.vendidas"> em {{ numeroBR(indicadores.vendidas) }} vendas com nota ({{ pct(indicadores.total, indicadores.vendidas) }})</template></span> ·
      <button type="button" class="underline-offset-2 hover:underline" @click="filtros.status = 'aguardando_entrega'; filtros.com_atendimento_no_mes = false">🟡 {{ indicadores.aguardando_entrega }} sem data de entrega</button>
      <template v-if="indicadores.entregue_sem_data">
        (<button type="button" class="underline-offset-2 hover:underline" data-entregue-sem-data @click="filtros.status = 'entregue_sem_data'; filtros.com_atendimento_no_mes = false">{{ indicadores.entregue_sem_data }} já {{ indicadores.entregue_sem_data === 1 ? 'entregue' : 'entregues' }} no Bling</button>)
      </template> ·
      <button type="button" class="underline-offset-2 hover:underline" @click="filtros.status = 'expirada'; filtros.com_atendimento_no_mes = false">🔴 {{ indicadores.expiradas }} expiradas</button>
    </p>

    <p v-if="erro" role="alert" class="flex items-center gap-2 text-sm text-destructive">
      <AlertCircle class="size-4" /> {{ erro }} <button type="button" class="underline" @click="carregar">tentar de novo</button>
    </p>

    <!-- a lista de Conversas aberta: de onde ela veio e o caminho de volta -->
    <p v-if="vista === 'conversas'" class="-mt-1 flex flex-wrap items-center gap-2 text-xs" :data-produto-aberto="abertoPor === 'produto' ? '' : undefined" data-lista-aberta>
      <span class="rounded-full border border-primary/40 bg-primary/5 px-2 py-0.5">
        <template v-if="abertoPor === 'produto'">Produto: <strong>{{ produtoAberto || 'escolhido' }}</strong><template v-if="carregado"> · {{ total }} {{ total === 1 ? 'pedido' : 'pedidos' }} de garantia</template></template>
        <template v-else-if="abertoPor === 'busca'">Busca: <strong>“{{ filtros.busca.trim() }}”</strong><template v-if="carregado"> · {{ total }} {{ total === 1 ? 'garantia' : 'garantias' }}</template></template>
        <template v-else>Todos os pedidos de garantia<template v-if="carregado"> · {{ total }}</template></template>
      </span>
      <button v-if="abertoPor === 'produto'" type="button" class="text-primary underline-offset-2 hover:underline" @click="fecharProduto">ver todos os pedidos</button>
      <button type="button" class="text-primary underline-offset-2 hover:underline" data-voltar-produtos @click="voltarParaProdutos">voltar para a garantia por produto</button>
    </p>

    <!-- Garantia por produto: os filtros da barra valem; a busca abre a lista -->
    <template v-if="vista === 'resumo'">
      <GarantiaResumo v-model:departamento="resumoDepartamento" v-model:busca="resumoBusca" v-model:ordem="resumoOrdem" :filtros="filtros" :versao="versaoResumo" @abrir="abrirProduto" @todos="verTodosOsPedidos" />
    </template>

    <!-- carregando / vazio -->
    <div v-else-if="!carregado && !erro" class="flex items-center justify-center gap-2 rounded-lg border py-14 text-sm text-muted-foreground" data-carregando>
      <Loader2 class="size-4 animate-spin" /> carregando garantias…
    </div>
    <EmptyState
      v-else-if="carregado && !itens.length"
      :icon="ShieldCheck"
      :title="soPedidos ? (temFiltro ? 'Nenhum pedido de garantia com esses filtros' : 'Nenhum pedido de garantia ainda') : temFiltro ? 'Nenhuma garantia com esses filtros' : 'Nenhuma garantia cadastrada ainda'"
      :description="soPedidos && vista === 'tabela' ? 'Em &quot;Mostrar&quot; está &quot;pedidos de garantia&quot;: as vendas em que o cliente não pediu garantia ficam escondidas.' : temFiltro ? 'Mude a busca, o status ou o período.' : soPedidos ? 'Ninguém pediu garantia ainda: as vendas em que o cliente não pediu ficam só na Tabela.' : 'Cadastre a primeira pelo + Nova garantia: informe o pedido e a data de entrega vira o início da garantia.'"
      data-vazio
    >
      <Button v-if="soPedidos && vista === 'tabela'" size="sm" variant="outline" data-mostrar-todas @click="mostrar = 'todas'; mostrarEscolhido = true">mostrar todas as vendas</Button>
      <Button v-if="temFiltro" size="sm" variant="outline" @click="limparFiltros">limpar filtros</Button>
      <Button v-else-if="acesso.cadastra" size="sm" @click="novaGarantia()"><Plus class="mr-1 size-4" /> Nova garantia</Button>
    </EmptyState>

    <!-- vista Conversas: lista à esquerda, histórico à direita (como o Atendimento) -->
    <div v-else-if="carregado && vista === 'conversas'" class="grid gap-3 md:h-[calc(100vh-15rem)] md:min-h-[520px] md:grid-cols-[minmax(280px,360px)_1fr]" data-vista-conversas>
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
                <div class="mt-1 flex items-center gap-1.5">
                  <GarantiaStatus :status="g.status" :entregue-sem-data="g.entregue_sem_data" />
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
                <div class="text-xs text-muted-foreground">pedido {{ g.pedido_bling }}<template v-if="g.plataforma"> · {{ nomePlataforma(g.plataforma) }}</template></div>
              </td>
              <td class="whitespace-nowrap px-3 py-2.5 text-xs" :class="g.pedido_em && !g.nao_e_pedido ? '' : 'text-muted-foreground'" :title="g.pedido_trecho || ''" data-pedido>{{ rotuloDoPedido(g) }}</td>
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
