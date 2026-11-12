<script setup lang="ts">
// Logística › Flex › "Anúncios Flex" (projeto Flex, etapa 4 — 02/10/2026).
//
// A tela do motor do Flex por anúncio (backend: routers/flex.py,
// services/flex_motor). O motor decide sozinho; aqui o dono:
//   • vê em que MODO o Flex automático está e quais contas ele pode mexer
//     (aviso fixo — o modo e as contas ficam na configuração do servidor);
//   • vê cada anúncio: o que o sistema quer, o que a plataforma mostra e o
//     porquê em português claro;
//   • APROVA o ligar (orientação do ML: ativar o Flex é decisão do vendedor —
//     desligar o sistema faz sozinho);
//   • pede uma conferência agora ("Sincronizar Flex") e desliga os anúncios
//     marcados ("Desligar selecionados", 12/11/2026 — no lugar do "Desligar
//     tudo", que desligava também o Flex do ML que sai de Piracicaba);
//   • vê os pedidos Flex que o robô não conseguiu passar para o .sp.
// Nada aqui chama o ML/Shopee direto: tudo passa pelo motor no servidor, que
// respeita o modo (em "observar" nada é escrito nas plataformas).
// Revisão de 02/10/2026 (fatos das contas): cada conta mostra se PODE ter
// Flex (assinatura do ML / Entrega Direta ligada na loja Shopee) e quantos
// anúncios dela o DaVinci não conhecia; o anúncio pausado aparece marcado;
// a emergência vira um job com andamento na tela.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  RefreshCw,
  Search,
  X,
  PowerOff,
  Check,
  ExternalLink,
  TriangleAlert,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
} from 'lucide-vue-next'

const props = defineProps<{ canEdit: boolean }>()
const emit = defineEmits<{
  // Pede à página para mostrar o pedido na lista "Pedidos Flex".
  (e: 'buscar-pedido', numero: string): void
  // Algo mudou (aprovação, emergência): a página recarrega os contadores.
  (e: 'mudou'): void
}>()

const { api } = useApi()
const toasts = useToasts()

type FlexConta = {
  id: string
  nome: string | null
  plataforma: string | null
  existe: boolean
  // Shopee: o shop_id da loja (o link do anúncio: shopee.com.br/product/<loja>/<anúncio>).
  loja_id?: string | null
  // O Flex DA CONTA (o motor confere de hora em hora): true = pode ter Flex;
  // false = não pode (motivo em flex_motivo); null = ainda não conferida.
  flex_ativo: boolean | null
  flex_status: string | null
  flex_motivo: string | null
  // De onde o motoboy do Flex sai (ML, lido da assinatura): o sistema só mexe
  // na conta com a saída em São Bernardo (Eduardo, 08/10/2026). ok: true =
  // São Bernardo; false = outra cidade ou ainda não lida; null = sem trava.
  flex_origem_cep: string | null
  flex_origem_cidade: string | null
  flex_origem_ok: boolean | null
  flex_lido_em: string | null
  flex_erro: string | null
  // Descoberta (ML): todos os anúncios da conta, e os que o DaVinci não conhecia.
  descoberta_em: string | null
  descoberta_ok: boolean | null
  descoberta_total: number | null
  descoberta_novos: number | null
  descoberta_erro: string | null
}
type FlexConfig = {
  modo: 'desligado' | 'observar' | 'piloto' | 'ativo' | string
  pode_escrever: boolean
  contas: FlexConta[]
  n_liga: number
  n_desliga: number
  kits: boolean
  max_anuncios_por_familia: number
  teto_escritas_por_rodada: number
  shopee_canais: string[]
  shopee_escrita: boolean
  intervalo_min: number
  pedido_no_sp: boolean
  // De onde o motoboy do Flex sai (cidade como o ML mostra + lote do estoque
  // de lá) — editável por admin; o robô só mexe na conta que sai dessa cidade.
  local: { cidade: string; lote: string; atualizado_em: string | null; atualizado_por: string | null } | null
  pode_alterar_local: boolean
  cidades_vistas: string[]
  lotes: string[]
  // 11/11/2026: modo, escrita da Shopee e pedido no .sp trocados na tela
  // ("tela") ou ainda do .env do servidor ("servidor").
  ajustes?: {
    modo_origem: string
    shopee_escrita_origem: string
    pedido_no_sp_origem: string
    kits_origem?: string
    contas_origem?: string
    atualizado_em: string | null
    atualizado_por: string | null
  } | null
  pode_ajustar?: boolean
  // 12/11/2026: as contas ML/Shopee ativas para marcar nas Chaves do Flex (só
  // para quem pode trocar). `no_flex`: está na lista em que o robô mexe.
  contas_opcoes?: FlexContaOpcao[]
}
type FlexContaOpcao = { id: string; nome: string; plataforma: 'ml' | 'shopee' | string; no_flex: boolean }
type FlexAnuncio = {
  integration_id: string
  conta: string | null
  external_id: string
  plataforma: 'ml' | 'shopee' | string
  titulo: string | null
  desejado: 'ligado' | 'desligado' | 'inelegivel' | string
  motivo: string | null
  motivo_claro: string | null
  observado: 'ligado' | 'desligado' | null
  observado_em: string | null
  aguardando_aprovacao: boolean
  aprovado_em: string | null
  aplicado_em: string | null
  tentativas: number
  proxima_tentativa: string | null
  ultimo_erro: string | null
  recusa: string | null
  saldo_sp: number | null
  familias: string | null
  leitura_falhas: number
  proxima_leitura: string | null
  // Status do anúncio na plataforma (active, paused, under_review, inactive, closed).
  status_anuncio: string | null
  atualizado_em: string
}
type FlexResumo = {
  avaliados: number
  ligados: number
  aguardando: number
  desligar: number
  nao_lidos: number
  // Flex ligado em contas que o sistema não controla (sem Flex conferido ou
  // saída fora da cidade do local): só a emergência ou uma pessoa desliga.
  sem_controle?: number
  // 11/11/2026: com peça livre no local de saída, com peça para ter Flex e,
  // dos com peça, os de conta que ainda não está pronta.
  com_peca?: number
  da_para_ter?: number
  conta_nao_pronta?: number
}
type FlexPedido = {
  bling_id: number
  numero: string | null
  lote?: string
  plataforma: string
  conta: string | null
  numeroloja: string | null
  prazo: string | null
  detectado_em: string
  alerta: string | null
  situacao: string | null
  skus: string[]
  // Saiu sem passar pelo .sp: o saldo Flex segue descontando até alguém
  // marcar que acertou o estoque no Bling.
  acerto_pendente: boolean
}

const config = ref<FlexConfig | null>(null)
const itens = ref<FlexAnuncio[]>([])
const total = ref(0)
const resumo = ref<FlexResumo>({ avaliados: 0, ligados: 0, aguardando: 0, desligar: 0, nao_lidos: 0 })
const pedidosSemSp = ref<FlexPedido[]>([])
const loading = ref(false)
const erro = ref<string | null>(null)

// ---- Filtros (dropdowns rotulados, preferência do dono) ----
const filtroPlataforma = ref<'' | 'ml' | 'shopee'>('')
const filtroConta = ref('')
// 12/11/2026 (revisão): o menu Conta mostra só as contas da plataforma
// escolhida — antes dava para escolher "Mercado Livre" + uma loja da Shopee e
// a lista ficava vazia. Trocar a plataforma solta a conta de outra plataforma.
const contasDoFiltro = computed(() =>
  (config.value?.contas || []).filter((c) => c.existe && (!filtroPlataforma.value || c.plataforma === filtroPlataforma.value)),
)
watch(filtroPlataforma, () => {
  if (filtroConta.value && !contasDoFiltro.value.some((c) => c.id === filtroConta.value)) filtroConta.value = ''
})
// 12/11/2026 (Vinicius: "situação e ação meio que tá duplicando"): saiu o
// menu "Situação". Ficam dois, cada um IGUAL a uma coluna da tabela — o nome
// do menu é o da coluna e as opções são os textos que aparecem nela.
// "Sistema quer" (servidor: `quer=`) segue o selo da coluna (ver querInfo).
const QUERES = [
  { key: '', label: 'Todos' },
  { key: 'pode', label: 'Pode ter Flex' },
  { key: 'desligado', label: 'Flex desligado' },
  { key: 'nao_pode', label: 'Não pode ter Flex' },
] as const
type QuerFiltro = (typeof QUERES)[number]['key']
const filtroQuer = ref<QuerFiltro>('')
// "Ação" (servidor: `acao=`) segue a coluna Ação (ver acaoDe). "Tentar de
// novo" e "Tentar agora" viraram "Tentar novamente".
const ACOES = [
  { key: '', label: 'Todas' },
  { key: 'aprovar', label: 'Aprovar' },
  { key: 'tentar_novamente', label: 'Tentar novamente' },
  { key: 'ativado', label: 'Ativado' },
  { key: 'desativado', label: 'Desativado' },
  { key: 'desliga', label: 'O sistema desliga' },
  { key: 'confirmando', label: 'Shopee confirmando' },
  { key: 'conta_nao_pronta', label: 'Conta não pronta' },
  { key: 'nao_conferido', label: 'Ainda não conferido' },
] as const
type AcaoFiltro = (typeof ACOES)[number]['key']
const filtroAcao = ref<AcaoFiltro>('')
// 11/11/2026 (Vinicius: "preciso ver só os anúncios que são elegíveis ao
// Flex"): de fábrica a lista mostra só quem tem peça em São Bernardo.
type FiltroPecas = 'com' | 'flex' | ''
const filtroPecas = ref<FiltroPecas>('com')
// "O sistema desliga" é o anúncio SEM peça: com o "Mostrar: com peça" de
// fábrica a lista viria sempre vazia — solta o filtro de peças.
watch(filtroAcao, (a) => {
  if (a === 'desliga' && filtroPecas.value) filtroPecas.value = ''
})
const busca = ref('')
const buscaAplicada = ref('')
let buscaTimer: ReturnType<typeof setTimeout> | null = null
watch(busca, (v) => {
  if (buscaTimer) clearTimeout(buscaTimer)
  buscaTimer = setTimeout(() => {
    buscaAplicada.value = v.trim()
  }, 350)
})

const POR_PAGINA = 100
const pagina = ref(1)
const totalPaginas = computed(() => Math.max(1, Math.ceil(total.value / POR_PAGINA)))

const filtrosAtivos = computed(
  () =>
    !!filtroPlataforma.value ||
    !!filtroConta.value ||
    !!filtroQuer.value ||
    !!filtroAcao.value ||
    !!busca.value.trim() ||
    filtroPecas.value !== 'com',
)
function limparFiltros() {
  filtroPlataforma.value = ''
  filtroConta.value = ''
  filtroQuer.value = ''
  filtroAcao.value = ''
  filtroPecas.value = 'com'
  busca.value = ''
  buscaAplicada.value = ''
}

function queryAnuncios(): string {
  const qs = new URLSearchParams()
  qs.set('limit', String(POR_PAGINA))
  qs.set('offset', String((pagina.value - 1) * POR_PAGINA))
  if (filtroPlataforma.value) qs.set('plataforma', filtroPlataforma.value)
  if (filtroConta.value) qs.set('integration_id', filtroConta.value)
  if (buscaAplicada.value) qs.set('busca', buscaAplicada.value)
  if (filtroPecas.value) qs.set('pecas', filtroPecas.value)
  if (filtroQuer.value) qs.set('quer', filtroQuer.value)
  if (filtroAcao.value) qs.set('acao', filtroAcao.value)
  return qs.toString()
}

async function carregarConfig() {
  try {
    config.value = await api<FlexConfig>('/api/flex/config')
  } catch (e: any) {
    erro.value = e?.data?.detail?.code || e?.message || 'erro'
  }
}

// Filtro trocado rápido: só a resposta do ÚLTIMO pedido vale (uma resposta
// velha chegando depois não pode pintar a lista com o filtro anterior).
let pedidoSeq = 0
async function carregarAnuncios() {
  const meu = ++pedidoSeq
  loading.value = true
  try {
    const r = await api<{ total: number; itens: FlexAnuncio[]; resumo: FlexResumo }>(
      `/api/flex/anuncios?${queryAnuncios()}`,
    )
    if (meu !== pedidoSeq) return
    itens.value = r.itens || []
    total.value = r.total || 0
    if (r.resumo) resumo.value = r.resumo
    erro.value = null
  } catch (e: any) {
    if (meu === pedidoSeq) erro.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    if (meu === pedidoSeq) loading.value = false
  }
}

async function carregarPedidos() {
  try {
    pedidosSemSp.value = await api<FlexPedido[]>('/api/flex/pedidos?so_alerta=true&abertos=true')
  } catch {
    // Lista informativa: falhar aqui não pode esconder a tabela de anúncios.
  }
}

async function carregarTudo() {
  await Promise.all([carregarConfig(), carregarAnuncios(), carregarPedidos()])
}

watch([filtroPlataforma, filtroConta, filtroQuer, filtroAcao, buscaAplicada, filtroPecas], () => {
  if (pagina.value !== 1) pagina.value = 1
  else carregarAnuncios()
})
watch(pagina, () => carregarAnuncios())

// ---- Atualização automática (60s) ----
// O motor roda no servidor de N em N minutos; a tela relê o banco sozinha
// para quem deixou a aba aberta. Não roda com ação em andamento.
const aprovando = ref<Set<string>>(new Set())
const sincronizando = ref(false)
let timer: ReturnType<typeof setInterval> | null = null
const recargas: ReturnType<typeof setTimeout>[] = []
function tick() {
  if (typeof document !== 'undefined' && document.visibilityState !== 'visible') return
  if (loading.value || aprovando.value.size || emLote.value || sincronizando.value) return
  carregarAnuncios()
  carregarPedidos()
}
onMounted(() => {
  carregarTudo()
  retomarEmergencia()
  timer = setInterval(tick, 60000)
})
onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
  if (buscaTimer) clearTimeout(buscaTimer)
  if (emergenciaTimer) clearTimeout(emergenciaTimer)
  if (destaqueTimer) clearTimeout(destaqueTimer)
  recargas.forEach((t) => clearTimeout(t))
})

// ---- Textos ----
// (12/11/2026: a frase do modo saiu da barra de cima — o modo e as regras
// ficam em "Como o sistema decide", à direita.)
// Local de saída do Flex (cidade + lote). Até a configuração chegar, o de
// fábrica — os textos nunca ficam sem cidade.
const localCidade = computed(() => config.value?.local?.cidade || 'São Bernardo do Campo')
const localLote = computed(() => config.value?.local?.lote || 'sp')
const modoDesligado = computed(() => !config.value || config.value.modo === 'desligado')

function nomePlataforma(p: string | null | undefined): string {
  if (p === 'ml') return 'Mercado Livre'
  if (p === 'shopee') return 'Shopee'
  return p || '—'
}
const contasPermitidas = computed(() => new Set((config.value?.contas || []).filter((c) => c.existe).map((c) => c.id)))
// Contas que a plataforma diz que NÃO podem ter Flex, ou com a saída do Flex
// fora da cidade do local de saída: ninguém aprova lá (o sistema não mexe nelas).
const contasSemFlex = computed(
  () =>
    new Set(
      (config.value?.contas || [])
        .filter((c) => c.flex_ativo === false || (c.flex_ativo === true && c.flex_origem_ok === false))
        .map((c) => c.id),
    ),
)
// "13400123,13421000" → "13400-123, 13421-000"
function cepTexto(ceps: string | null): string {
  return (ceps || '')
    .split(',')
    .filter(Boolean)
    .map((c) => (c.length === 8 ? `${c.slice(0, 5)}-${c.slice(5)}` : c))
    .join(', ')
}
function flexDaConta(c: FlexConta): { texto: string; cls: string; title: string } {
  const loja = c.plataforma === 'shopee'
  if (c.flex_ativo === true && c.flex_origem_ok === false)
    return {
      texto: c.flex_origem_cidade
        ? `Flex ativo, mas a saída é em ${c.flex_origem_cidade || '?'}${c.flex_origem_cep ? ` (CEP ${cepTexto(c.flex_origem_cep)})` : ''} — fora de ${localCidade.value}`
        : 'Flex ativo, mas ainda não deu para ler de onde ele sai',
      cls: 'text-rose-700 dark:text-rose-400 font-medium',
      title:
        (c.flex_origem_cidade
          ? `O motoboy do Flex sai de ${localCidade.value}: troque o endereço do Flex desta conta no painel do Mercado Livre (ou o local de saída, acima).`
          : 'O sistema lê de novo na próxima rodada.') +
        ` Até lá o sistema não mexe nos anúncios dela. Conferido ${fmtDesde(c.flex_lido_em)}.`,
    }
  if (c.flex_ativo === true)
    return {
      texto: loja
        ? 'Entrega Direta ligada na loja'
        : c.flex_origem_ok === true
          ? `Flex ativo na conta · saída em ${c.flex_origem_cidade || localCidade.value}`
          : 'Flex ativo na conta',
      cls: 'text-emerald-700 dark:text-emerald-400',
      title: `Conferido ${fmtDesde(c.flex_lido_em)}.`,
    }
  if (c.flex_ativo === false)
    return {
      texto: loja ? 'Entrega Direta desligada na loja' : `sem Flex no ML (${c.flex_status || '?'})`,
      cls: 'text-rose-700 dark:text-rose-400 font-medium',
      title:
        (c.flex_motivo || '') +
        (loja ? ' Ligue no Seller Center primeiro.' : ' Enquanto isso o sistema não mexe nos anúncios dela.'),
    }
  return {
    texto: c.flex_erro ? 'Flex da conta não conferido (erro)' : 'Flex da conta ainda não conferido',
    cls: 'text-amber-700 dark:text-amber-400',
    title: c.flex_erro || 'O sistema confere na próxima rodada. Até lá não mexe nos anúncios dela.',
  }
}
function descobertaTexto(c: FlexConta): string {
  if (!c.descoberta_em || c.descoberta_total === null) return ''
  const novos = c.descoberta_novos ? `, ${c.descoberta_novos} fora do DaVinci (o sistema desliga)` : ''
  return `${c.descoberta_total} anúncio(s) na conta${novos} — lidos ${fmtDesde(c.descoberta_em)}${c.descoberta_ok === false ? ' (leitura incompleta)' : ''}`
}
// ---- Quadro do topo: as contas agrupadas pelo que falta para ficarem prontas ----
// (Eduardo, 08/10/2026: "arrume esse flex automático e deixe ele mais
// organizado"). Cada conta aparece UMA vez, no grupo da situação dela; o
// grupo diz o que fazer. Os nomes abrem com um clique.
type GrupoConta = {
  chave: string
  titulo: string
  acao: string
  cor: string // a bolinha do grupo
  contas: { id: string; nome: string; detalhe: string; title: string }[]
}
const STATUS_SEM_FLEX: Record<string, string> = {
  out: 'saiu do Flex',
  pending: 'aguardando o ML',
  sem_assinatura: 'nunca teve Flex',
  http_404: 'nunca teve Flex',
  http_403: 'sem acesso',
}
const gruposContas = computed<GrupoConta[]>(() => {
  const contas = config.value?.contas || []
  const cidade = localCidade.value
  const g: Record<string, GrupoConta> = {
    // 12/11/2026: a loja Shopee com a Entrega Direta ligada também é pronta
    // (antes caía em "Lojas da Shopee" e Prontas mostrava 0 com 8 prontas).
    prontas: {
      chave: 'prontas',
      titulo: 'Prontas',
      acao: `O sistema controla o Flex dos anúncios destas contas: Mercado Livre com a saída do Flex em ${cidade} e lojas da Shopee com a Entrega Direta ligada.`,
      cor: 'bg-emerald-500',
      contas: [],
    },
    fora: {
      chave: 'fora',
      titulo: 'Saída do Flex em outra cidade',
      acao: `Troque o endereço do Flex para ${cidade} no painel do Mercado Livre e clique em Sincronizar Flex.`,
      cor: 'bg-rose-500',
      contas: [],
    },
    lendo: {
      chave: 'lendo',
      titulo: 'Ainda lendo de onde o Flex sai',
      acao: 'O sistema confere de novo na próxima rodada (até 15 min).',
      cor: 'bg-amber-400',
      contas: [],
    },
    sem_flex: {
      chave: 'sem_flex',
      titulo: 'Sem Flex no Mercado Livre',
      acao: 'Se quiser Flex nelas: ative o Envios Flex no painel do Mercado Livre.',
      cor: 'bg-zinc-400',
      contas: [],
    },
    erro: {
      chave: 'erro',
      titulo: 'Não deu para conferir',
      acao: 'Reconecte a conta em Integrações.',
      cor: 'bg-amber-500',
      contas: [],
    },
    shopee: {
      chave: 'shopee',
      titulo: 'Lojas da Shopee sem Entrega Direta',
      acao: 'Ligue a Entrega Direta na loja, no Seller Center, e clique em Sincronizar Flex.',
      cor: 'bg-orange-400',
      contas: [],
    },
  }
  for (const c of contas) {
    if (!c.existe) continue
    const nome = c.nome || c.id.slice(0, 8)
    const info = flexDaConta(c)
    const title = [info.title, descobertaTexto(c)].filter(Boolean).join(' · ')
    const item = (detalhe: string) => ({ id: c.id, nome, detalhe, title })
    if (c.plataforma === 'shopee') {
      if (c.flex_ativo === true) g.prontas!.contas.push(item('Shopee · Entrega Direta ligada'))
      else g.shopee!.contas.push(item(c.flex_ativo === false ? 'desligada' : 'não conferida'))
    } else if (c.flex_ativo === null) {
      g.erro!.contas.push(item(c.flex_erro ? 'erro na conferência' : 'ainda não conferida'))
    } else if (c.flex_ativo === false) {
      g.sem_flex!.contas.push(item(STATUS_SEM_FLEX[c.flex_status || ''] || c.flex_status || ''))
    } else if (c.flex_origem_ok === false && c.flex_origem_cidade) {
      g.fora!.contas.push(item(`sai de ${c.flex_origem_cidade}`))
    } else if (c.flex_origem_ok === false) {
      g.lendo!.contas.push(item(''))
    } else {
      g.prontas!.contas.push(item(`Mercado Livre · saída em ${c.flex_origem_cidade || cidade}`))
    }
  }
  // Prontas sempre aparece (mesmo com 0: é a meta); os outros, só com conta.
  return Object.values(g).filter((x) => x.chave === 'prontas' || x.contas.length)
})
// Fechado de fábrica; abrir/fechar fica lembrado neste navegador.
const QUADRO_CHAVE = 'flex:quadro-aberto'
const quadroAberto = ref(false)
onMounted(() => {
  try {
    quadroAberto.value = localStorage.getItem(QUADRO_CHAVE) === '1'
  } catch {
    // sem localStorage (aba anônima): fica fechado
  }
})
watch(quadroAberto, (v) => {
  try {
    localStorage.setItem(QUADRO_CHAVE, v ? '1' : '0')
  } catch {
    // idem
  }
})
const contasFantasma = computed(() => (config.value?.contas || []).filter((c) => !c.existe).length)
const gruposAbertos = ref<Set<string>>(new Set())
function alternarGrupo(chave: string) {
  const n = new Set(gruposAbertos.value)
  if (n.has(chave)) n.delete(chave)
  else n.add(chave)
  gruposAbertos.value = n
}
// As regras em tópicos (12/11/2026: "escreve melhor, deixa por tópico tipo
// regras, 1 tal tal, 2 tal tal") — o forte em negrito, o resto normal.
const regras = computed<{ forte: string; resto: string; antes?: string }[]>(() => {
  const c = config.value
  if (!c) return []
  const pecas = (n: number) => (n === 1 ? '1 peça' : `${n} peças`)
  const meio = Array.from({ length: Math.max(0, c.n_liga - c.n_desliga) }, (_, i) => c.n_desliga + i)
  const meioTxt = meio.length === 1 ? pecas(meio[0]!) : meio.length === 2 ? `${meio[0]} ou ${meio[1]} peças` : `${meio[0]} a ${meio[meio.length - 1]} peças`
  const out: { forte: string; resto: string; antes?: string }[] = [
    {
      forte: 'Liga',
      resto: ` com ${pecas(c.n_liga)} ou mais livres em ${localCidade.value} (estoque .${localLote.value}) — depois que você aprova.`,
    },
    {
      forte: 'Desliga sozinho',
      resto: ` quando fica com menos de ${pecas(c.n_desliga)}${c.n_desliga === 1 ? ' (acabou a peça)' : ''}${c.pode_escrever ? '' : ' — no Piloto ou Ativo'}.`,
    },
  ]
  if (meio.length && meio[0]! > 0) out.push({ antes: 'Com ', forte: meioTxt, resto: ': fica como está (não liga nem desliga).' })
  if (c.pode_escrever)
    out.push({
      antes: 'Para desligar antes: marque e clique em ',
      forte: 'Desligar selecionados',
      resto: ' — o anúncio só volta a ligar se alguém aprovar de novo.',
    })
  out.push({
    antes: 'Só nas ',
    forte: 'contas prontas',
    resto: `: Shopee com a Entrega Direta ligada na loja; Mercado Livre com a saída do Flex em ${localCidade.value}.`,
  })
  out.push(
    c.shopee_escrita
      ? { forte: 'Shopee em promoção', resto: ' não deixa mudar o envio: tire o anúncio da promoção e aprove de novo.' }
      : { antes: 'Na ', forte: 'Shopee', resto: ' o sistema só confere: ligar e desligar é à mão no Seller Center.' },
  )
  out.push({
    forte: c.kits ? 'Kits entram' : 'Kits não entram',
    resto:
      (c.kits ? ` só com tudo no .${localLote.value} (vale o kit .${localLote.value} do Bling, com o estoque da peça mais escassa)` : '') +
      (c.max_anuncios_por_familia > 0
        ? `. No máximo ${c.max_anuncios_por_familia} anúncio(s) com Flex por família.`
        : '. Sem limite de anúncios por família.'),
  })
  out.push({
    antes: 'Confere sozinho a cada ',
    forte: `${c.intervalo_min} min`,
    resto: ` (ou no Sincronizar Flex)${c.pode_escrever ? `, até ${c.teto_escritas_por_rodada} anúncios por vez` : ''}.`,
  })
  return out
})

const DESEJADO: Record<string, { texto: string; cls: string }> = {
  ligado: { texto: 'Pode ter Flex', cls: 'border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300' },
  desligado: { texto: 'Flex desligado', cls: 'border-zinc-300 bg-zinc-50 text-zinc-700 dark:border-zinc-700 dark:bg-zinc-900/40 dark:text-zinc-300' },
  inelegivel: { texto: 'Não pode ter Flex', cls: 'border-rose-300 bg-rose-50 text-rose-800 dark:border-rose-700 dark:bg-rose-950/40 dark:text-rose-300' },
}
function desejadoInfo(a: FlexAnuncio) {
  return DESEJADO[a.desejado] || { texto: a.desejado, cls: 'border-zinc-300' }
}
function observadoTexto(a: FlexAnuncio): string {
  if (a.observado === 'ligado') return 'Flex ligado'
  if (a.observado === 'desligado') return 'Flex desligado'
  return 'Ainda não conferido'
}
// Plataforma diferente do que a regra quer — é o que o motor vai corrigir
// (desligar sozinho; ligar só com aprovação).
function divergente(a: FlexAnuncio): boolean {
  if (!a.observado) return false
  return (a.observado === 'ligado') !== (a.desejado === 'ligado')
}
function saldoCls(a: FlexAnuncio): string {
  if (a.saldo_sp === null || a.saldo_sp === undefined) return 'text-muted-foreground'
  const c = config.value
  if (a.saldo_sp <= 0 || (c && a.saldo_sp < c.n_desliga)) return 'text-rose-700 dark:text-rose-400 font-medium'
  if (c && a.saldo_sp < c.n_liga) return 'text-amber-700 dark:text-amber-400'
  return 'text-emerald-700 dark:text-emerald-400'
}
function familiasTexto(a: FlexAnuncio): string {
  return (a.familias || '').split(',').map((f) => f.trim()).filter(Boolean).join(', ') || '—'
}
// Link do anúncio: só o do Mercado Livre é montável com o que o DaVinci tem
// (MLB123 → produto.mercadolivre.com.br/MLB-123); o da Shopee pede o id da loja.
function linkAnuncio(a: FlexAnuncio): string | null {
  if (a.plataforma === 'shopee') {
    // 12/11/2026: a Shopee também — o shop_id da loja vem da configuração.
    const loja = (config.value?.contas || []).find((c) => c.id === a.integration_id)?.loja_id
    const item = a.external_id.trim()
    return loja && /^\d+$/.test(item) ? `https://shopee.com.br/product/${loja}/${item}` : null
  }
  if (a.plataforma !== 'ml') return null
  const m = /^MLB(\d+)$/i.exec(a.external_id.trim())
  return m ? `https://produto.mercadolivre.com.br/MLB-${m[1]}` : null
}
// Aprovado, mas ainda não ligou (o Bling não confirmou o saldo, a rodada
// estava ocupada…): o botão vira "Tentar novamente" — antes ficava só o rótulo,
// sem como pedir de novo.
function aprovadoSemLigar(a: FlexAnuncio): boolean {
  return !!a.aprovado_em && a.desejado === 'ligado' && a.observado !== 'ligado'
}
// A Shopee aceitou e ainda não mostra a mudança (leva minutos): não é erro —
// a próxima leitura confirma (12/11/2026; o texto vem do motor,
// flex_motor.SHOPEE_CONFIRMANDO). Sem "Tentar novamente" enquanto isso.
function shopeeConfirmando(a: FlexAnuncio): boolean {
  return (a.ultimo_erro || '').includes('esperando ela mostrar')
}
const CONFIRMANDO_TXT = 'A Shopee aceitou — esperando ela mostrar a mudança (o sistema confere na próxima leitura, até 15 min).'
function podeAprovar(a: FlexAnuncio): boolean {
  return (
    props.canEdit &&
    !modoDesligado.value &&
    contasPermitidas.value.has(a.integration_id) &&
    !contasSemFlex.value.has(a.integration_id) &&
    // Na Shopee só se confere (sem flex_shopee_escrita): aprovar não ligaria nada.
    !(a.plataforma === 'shopee' && !config.value?.shopee_escrita) &&
    (a.aguardando_aprovacao ||
      !!a.recusa ||
      (aprovadoSemLigar(a) && !!config.value?.pode_escrever && !shopeeConfirmando(a)))
  )
}
// 12/11/2026: "Tentar de novo" (a plataforma recusou) e "Tentar agora"
// (aprovado e ainda não ligou) viraram um nome só, em AMARELO — "não está
// correto para estar em verde": verde é só o Aprovar.
function tentarNovamente(a: FlexAnuncio): boolean {
  return !!a.recusa || aprovadoSemLigar(a)
}
function rotuloAprovar(a: FlexAnuncio): string {
  return tentarNovamente(a) ? 'Tentar novamente' : 'Aprovar'
}
const BOTAO_APROVAR =
  'border-emerald-400 text-emerald-800 hover:bg-emerald-50 dark:border-emerald-700 dark:text-emerald-300 dark:hover:bg-emerald-950/40'
const BOTAO_TENTAR =
  'border-amber-400 bg-amber-50 text-amber-800 hover:bg-amber-100 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-300 dark:hover:bg-amber-950/60'
const STATUS_ANUNCIO: Record<string, string> = {
  paused: 'pausado',
  under_review: 'em revisão',
  inactive: 'inativo',
  closed: 'encerrado',
}
// Só o que NÃO está ativo ganha selo (ativo é o normal).
function statusTexto(a: FlexAnuncio): string | null {
  return a.status_anuncio ? STATUS_ANUNCIO[a.status_anuncio] || null : null
}
// A recusa da plataforma em linha própria — menos quando o motivo já é ela
// (a regra marca "não pode ter Flex" pela recusa): não repete a frase.
function recusaAparte(a: FlexAnuncio): boolean {
  return !!a.recusa && !(a.motivo || '').startsWith('a plataforma recusou ligar')
}
// "Último erro" repetindo a recusa que o "Por quê" já explica em português
// (12/11/2026: "ligar: 200 product.error_busi: …" embaixo da frase): esconde.
function erroAparte(a: FlexAnuncio): boolean {
  if (!a.ultimo_erro || faltaGtin(a)) return false
  return !(a.recusa && a.ultimo_erro === `ligar: ${a.recusa}`)
}
// 12/11/2026: a Shopee recusa ligar o anúncio sem código de barras ("The GTIN
// code is required for this category") — o erro vinha cru, em inglês, em
// vermelho. Vira o porquê em português (como a promoção), sem a linha crua.
const GTIN_TXT =
  'A Shopee pede o código de barras (GTIN/EAN) deste anúncio para mudar o envio. Preencha no Seller Center e clique em Tentar novamente.'
function faltaGtin(a: FlexAnuncio | null | undefined): boolean {
  return /\bGTIN\b/i.test(a?.ultimo_erro || '')
}
function porQue(a: FlexAnuncio): string {
  if (faltaGtin(a)) return GTIN_TXT
  return a.motivo_claro || a.motivo || '—'
}
function chave(a: FlexAnuncio): string {
  return `${a.integration_id}/${a.external_id}`
}

function fmtDataHora(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })
}
function fmtCurto(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
}
function fmtDesde(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const seg = Math.floor((Date.now() - d.getTime()) / 1000)
  if (seg < 60) return 'agora há pouco'
  if (seg < 3600) return `há ${Math.floor(seg / 60)} min`
  if (seg < 86400) return `há ${Math.floor(seg / 3600)} h`
  const dias = Math.floor(seg / 86400)
  return dias === 1 ? 'há 1 dia' : `há ${dias} dias`
}
function prazoVencido(p: FlexPedido): boolean {
  if (!p.prazo) return false
  const d = new Date(p.prazo)
  return !Number.isNaN(d.getTime()) && d.getTime() < Date.now()
}

const MOTIVO_SERVIDOR: Record<string, string> = {
  'flex_modo=desligado': 'O Flex automático está desligado — não há o que conferir.',
  'nenhuma conta em flex_contas': 'Nenhuma conta está liberada para o Flex automático.',
  'nenhuma conta permitida ativa (ML/Shopee)': 'Nenhuma das contas liberadas está ativa no DaVinci.',
  'já há uma rodada pedida neste minuto': 'Já foi pedida uma conferência neste minuto — aguarde.',
}
function motivoServidor(m: string | null | undefined): string {
  return (m && MOTIVO_SERVIDOR[m]) || m || ''
}
const ERRO_APROVAR: Record<string, string> = {
  flex_desligado: 'O Flex automático está desligado.',
  conta_nao_permitida: 'Esta conta não está liberada para o Flex automático.',
  nao_avaliado: 'O sistema ainda não conferiu este anúncio.',
  nao_elegivel: 'Este anúncio não pode ligar o Flex agora.',
  conta_sem_flex: 'Esta conta não tem o Flex ativo na plataforma.',
  conta_fora_da_origem: 'A saída do Flex desta conta não é na cidade do local de saída — troque o endereço do Flex no painel do Mercado Livre primeiro.',
  shopee_so_leitura: 'Na Shopee o sistema só confere: ligue a Entrega Direta à mão no Seller Center.',
}

// ---- Local de saída do Flex (cidade + lote) ----
const editandoLocal = ref(false)
const salvandoLocal = ref(false)
const localForm = ref({ cidade: '', lote: 'sp' })
const sugestoesCidades = computed(() => {
  const out: string[] = []
  const chave = (t: string) => t.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim()
  for (const c of [localCidade.value, ...(config.value?.cidades_vistas || []), 'São Bernardo do Campo']) {
    if (c && !out.some((o) => chave(o) === chave(c))) out.push(c)
  }
  return out
})
const ERRO_LOCAL: Record<string, string> = {
  so_admin: 'Só um admin troca o local de saída do Flex.',
  cidade_vazia: 'Informe a cidade de onde o motoboy sai.',
  cidade_longa: 'Nome de cidade comprido demais.',
  cidade_invalida: 'Digite só o nome da cidade, como o Mercado Livre mostra (sem vírgula).',
  lote_invalido: 'Escolha um dos lotes da lista.',
  formulario: 'Confira a cidade e o estoque (lote).',
  local_mudou: 'Outra pessoa trocou o local agora há pouco — a tela foi atualizada, confira e tente de novo.',
}
// "São Bernardo do Campo - SP" ≈ "sao bernardo do campo": a mesma régua do
// servidor (services/flex_local.normalizar_cidade).
function chaveCidade(t: string): string {
  return t
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .trim()
    .replace(/\s*(?:[-/,(]\s*)[a-z]{2}\)?\s*$/, '')
    .replace(/[^0-9a-z]+/g, ' ')
    .trim()
}
// Contas do ML com Flex que já saem desta cidade (pela última leitura).
function contasNaCidade(cidade: string): number {
  const alvo = chaveCidade(cidade)
  return (config.value?.contas || []).filter(
    (c) =>
      c.flex_ativo === true &&
      c.plataforma === 'ml' &&
      !!c.flex_origem_cidade &&
      c.flex_origem_cidade.split(',').every((x) => chaveCidade(x) === alvo),
  ).length
}
function abrirLocal() {
  quadroAberto.value = true
  localForm.value = { cidade: localCidade.value, lote: localLote.value }
  editandoLocal.value = true
}
async function salvarLocal() {
  const cidade = localForm.value.cidade.trim()
  const lote = localForm.value.lote
  if (!cidade) {
    toasts.error('Local de saída', ERRO_LOCAL.cidade_vazia!)
    return
  }
  if (cidade === localCidade.value && lote === localLote.value) {
    editandoLocal.value = false
    return
  }
  const naCidade = contasNaCidade(cidade)
  const aviso =
    naCidade === 0
      ? `\n\n⚠️ Nenhuma conta do Mercado Livre sai de "${cidade}" hoje (pela última leitura): ` +
        'o sistema vai parar de mexer em TODAS até o endereço do Flex delas ser essa cidade. ' +
        'Confira se o nome está igual ao do Mercado Livre (use uma das sugestões da lista).'
      : `\n\n${naCidade} conta(s) do Mercado Livre já saem dessa cidade.`
  const ok = confirm(
    `Trocar o local de saída do Flex?\n\nDe: ${localCidade.value} (estoque .${localLote.value})\n` +
      `Para: ${cidade} (estoque .${lote})\n\n` +
      'O sistema passa a contar as peças desse estoque e só mexe nas contas cuja saída do Flex ' +
      'no Mercado Livre for nessa cidade. Os anúncios das outras contas ficam como estão.' +
      aviso,
  )
  if (!ok) return
  salvandoLocal.value = true
  try {
    const r = await api<{ ligados_fora?: number | null }>('/api/flex/local', {
      method: 'PUT',
      body: { cidade, lote, cidade_antes: localCidade.value, lote_antes: localLote.value },
    })
    toasts.success('Local de saída trocado', `${cidade} · estoque .${lote}`)
    if (r?.ligados_fora)
      toasts.warning('Anúncios com Flex ligado fora do local', [
        `${r.ligados_fora} anúncio(s) continuam com Flex ligado em contas que não saem de ${cidade}.`,
        'O sistema não mexe mais neles. Se precisar, desligue pelo painel da plataforma.',
      ])
    editandoLocal.value = false
    emit('mudou')
    await Promise.all([carregarConfig(), carregarAnuncios()])
  } catch (e: any) {
    const d = e?.data?.detail
    // 422 (validação do formulário) vem como lista: mensagem simples.
    const code = Array.isArray(d) ? 'formulario' : typeof d === 'object' && d ? d.code : null
    toasts.error('Local de saída', ERRO_LOCAL[code] || (typeof d === 'object' && d?.detalhe) || e?.message || 'erro')
    if (code === 'local_mudou') {
      editandoLocal.value = false
      await carregarConfig()
    }
  } finally {
    salvandoLocal.value = false
  }
}

// ---- Ações ----
async function sincronizar() {
  if (!props.canEdit || sincronizando.value) return
  sincronizando.value = true
  try {
    const r = await api<{ enfileirado: boolean; modo: string; motivo: string | null }>('/api/flex/sincronizar', {
      method: 'POST',
    })
    if (r.enfileirado) {
      toasts.info(
        'Conferência pedida',
        r.modo === 'observar'
          ? 'O sistema vai conferir os anúncios agora (só observando: nada muda nas plataformas). A lista atualiza sozinha.'
          : 'O sistema vai conferir os anúncios agora e aplicar o que a regra pede. A lista atualiza sozinha.',
      )
      // A rodada roda no servidor (pode levar minutos): relê algumas vezes.
      for (const ms of [15000, 45000, 120000]) recargas.push(setTimeout(() => carregarTudo(), ms))
    } else {
      toasts.warning('Nada foi pedido', motivoServidor(r.motivo))
    }
  } catch (e: any) {
    toasts.error('Não foi possível pedir a conferência', e?.data?.detail?.code || e?.message || 'erro')
  } finally {
    sincronizando.value = false
  }
}

async function aprovar(a: FlexAnuncio) {
  if (!podeAprovar(a)) return
  const c = config.value
  const conta = a.conta || 'conta'
  const pergunta = a.recusa
    ? `Tentar ligar o Flex de novo no anúncio ${a.external_id} (${conta})?\n\nA plataforma recusou antes: ${a.recusa}`
    : `Ligar o Flex no anúncio ${a.external_id} (${conta})?\n\nPeças livres em ${localCidade.value} (.${localLote.value}): ${a.saldo_sp ?? '—'}.`
  const aviso = c?.pode_escrever
    ? '\n\nO sistema confere o anúncio e o saldo no Bling e liga em seguida. Se ele estiver no meio de uma conferência, a ligação entra na fila e sai em 1–2 minutos.'
    : '\n\nModo "só observando": a aprovação fica registrada, mas nada muda na plataforma.'
  if (!confirm(pergunta + aviso)) return
  const k = chave(a)
  aprovando.value = new Set([...aprovando.value, k])
  try {
    const r = await api<{
      aprovado: boolean
      modo: string
      aplicado: boolean
      na_fila: boolean
      estado: FlexAnuncio | null
    }>(`/api/flex/anuncios/${a.integration_id}/${encodeURIComponent(a.external_id)}/aprovar`, { method: 'POST' })
    if (r.estado) {
      const i = itens.value.findIndex((x) => chave(x) === k)
      if (i >= 0) itens.value.splice(i, 1, r.estado)
    }
    if (r.aplicado) toasts.success('Flex ligado', `Anúncio ${a.external_id}.`)
    else if (r.estado?.observado === 'ligado')
      toasts.success('O Flex já estava ligado', `Anúncio ${a.external_id}: nada a fazer.`)
    else if (r.na_fila) {
      toasts.info(
        'Aprovado — na fila para ligar',
        'O sistema estava no meio de uma conferência: a ligação entrou na fila e sai em 1–2 minutos. A lista atualiza sozinha.',
      )
      for (const ms of [45000, 120000]) recargas.push(setTimeout(() => carregarAnuncios(), ms))
    } else if (r.modo === 'piloto' || r.modo === 'ativo')
      toasts.warning(
        'Aprovado, mas ainda não ligou',
        (faltaGtin(r.estado) ? GTIN_TXT : r.estado?.ultimo_erro) ||
          r.estado?.motivo_claro ||
          'Veja o motivo na linha do anúncio. A aprovação continua valendo: o sistema tenta de novo na próxima conferência, ou clique em "Tentar novamente".',
      )
    else toasts.info('Aprovação registrada', 'Só observando: nada mudou na plataforma.')
    emit('mudou')
    carregarAnuncios()
  } catch (e: any) {
    const d = e?.data?.detail
    const code = typeof d === 'object' && d ? d.code : null
    const detalhe = typeof d === 'object' && d ? d.detalhe : null
    toasts.error(
      'Não foi possível aprovar',
      [ERRO_APROVAR[code] || code || e?.message || 'erro', detalhe || ''].filter(Boolean),
    )
    carregarAnuncios()
  } finally {
    const s = new Set(aprovando.value)
    s.delete(k)
    aprovando.value = s
  }
}

// ---- Ligar vários de uma vez (12/11/2026) ----
// Vinicius: "um quadradinho para ticar, selecionar os que quero aprovar tudo
// de uma vez" + "ligar todos selecionados" ao lado do Desligar tudo. Marca só
// quem tem o botão Aprovar; o botão aprova um por um (o mesmo caminho do
// Aprovar — confere o saldo no Bling e liga) e no fim mostra o resumo.
const marcados = ref<Set<string>>(new Set())
// 12/11/2026 ("Desligar selecionados"): o mesmo quadradinho marca quem dá para
// ligar (tem Aprovar) e quem dá para desligar (Flex ligado em conta pronta);
// cada botão usa só os seus.
const marcaveis = computed(() => itens.value.filter((a) => podeAprovar(a) || podeDesligar(a)))
const marcadosValidos = computed(() => marcaveis.value.filter((a) => marcados.value.has(chave(a))))
const paraLigar = computed(() => marcadosValidos.value.filter((a) => podeAprovar(a)))
const paraDesligar = computed(() => marcadosValidos.value.filter((a) => podeDesligar(a)))
const todosMarcados = computed(
  () => marcaveis.value.length > 0 && marcadosValidos.value.length === marcaveis.value.length,
)
const algunsMarcados = computed(() => marcadosValidos.value.length > 0 && !todosMarcados.value)
function marcar(a: FlexAnuncio, sim: boolean) {
  const s = new Set(marcados.value)
  if (sim) s.add(chave(a))
  else s.delete(chave(a))
  marcados.value = s
}
function marcarTodos(sim: boolean) {
  marcados.value = sim ? new Set(marcaveis.value.map(chave)) : new Set()
}
function emPromocao(a: FlexAnuncio | null | undefined): boolean {
  const r = a?.recusa || ''
  return r.includes('em promoção') || r.includes('has promotion')
}
// Um lote por vez (ligar OU desligar): a barra mostra o andamento.
const loteAcao = ref<'ligar' | 'desligar' | null>(null)
const emLote = computed(() => loteAcao.value !== null)
const loteFeitos = ref(0)
const loteTotal = ref(0)
function soltarMarcados(feitos: FlexAnuncio[]) {
  const s = new Set(marcados.value)
  for (const a of feitos) s.delete(chave(a))
  marcados.value = s
}
function trocarLinha(k: string, estado: FlexAnuncio | null | undefined) {
  if (!estado) return
  const i = itens.value.findIndex((x) => chave(x) === k)
  if (i >= 0) itens.value.splice(i, 1, estado)
}
async function ligarSelecionados() {
  const lista = [...paraLigar.value]
  if (!lista.length || emLote.value) return
  const aviso = config.value?.pode_escrever
    ? `O sistema confere cada anúncio e o saldo no Bling e liga um por um. Anúncio em promoção a Shopee não deixa: ele fica marcado com o nome da promoção.`
    : 'Modo "só observando": as aprovações ficam registradas, mas nada muda na plataforma.'
  if (!confirm(`Ligar o Flex em ${lista.length} anúncio(s)?\n\n${aviso}`)) return
  loteAcao.value = 'ligar'
  loteTotal.value = lista.length
  loteFeitos.value = 0
  const r = { ligados: 0, ja: 0, fila: 0, confirmando: 0, promocao: 0, outros: 0, erros: 0 }
  for (const a of lista) {
    const k = chave(a)
    aprovando.value = new Set([...aprovando.value, k])
    try {
      const resp = await api<{ aplicado: boolean; na_fila: boolean; modo: string; estado: FlexAnuncio | null }>(
        `/api/flex/anuncios/${a.integration_id}/${encodeURIComponent(a.external_id)}/aprovar`,
        { method: 'POST' },
      )
      trocarLinha(k, resp.estado)
      if (resp.aplicado) r.ligados++
      else if (resp.estado?.observado === 'ligado') r.ja++
      else if (resp.na_fila) r.fila++
      else if (resp.estado && shopeeConfirmando(resp.estado)) r.confirmando++
      else if (emPromocao(resp.estado)) r.promocao++
      else r.outros++
    } catch {
      r.erros++
    } finally {
      const s = new Set(aprovando.value)
      s.delete(k)
      aprovando.value = s
      loteFeitos.value++
    }
  }
  loteAcao.value = null
  soltarMarcados(lista)
  const partes = [
    r.ligados && `${r.ligados} ligado(s)`,
    r.ja && `${r.ja} já estava(m) ligado(s)`,
    r.confirmando && `${r.confirmando} com a Shopee confirmando (até 15 min)`,
    r.fila && `${r.fila} na fila (1–2 min)`,
    r.promocao && `${r.promocao} em promoção — veja o nome na linha`,
    r.outros && `${r.outros} ainda não ligou — veja o motivo na linha`,
    r.erros && `${r.erros} não deu para aprovar`,
  ].filter(Boolean) as string[]
  const tudoCerto = !r.promocao && !r.outros && !r.erros
  ;(tudoCerto ? toasts.success : toasts.warning)(`Ligar selecionados: ${lista.length}`, partes)
  if (r.fila) for (const ms of [45000, 120000]) recargas.push(setTimeout(() => carregarAnuncios(), ms))
  emit('mudou')
  carregarAnuncios()
}

// ---- Desligar vários de uma vez (12/11/2026) ----
// Vinicius: "e se colocar desligar selecionados… dai selecionamos antes" — no
// lugar do "Desligar tudo" (que desligava também o Flex do ML que sai de
// Piracicaba, onde o sistema não religa). Só o Flex que o sistema controla:
// ligado na plataforma, conta pronta, piloto/ativo. Desligado assim, só volta
// a ligar com outra aprovação (com peça, ele vai para "Esperando aprovação").
function podeDesligar(a: FlexAnuncio): boolean {
  return (
    props.canEdit &&
    !!config.value?.pode_escrever &&
    contasPermitidas.value.has(a.integration_id) &&
    contaPronta(a) &&
    !(a.plataforma === 'shopee' && !config.value?.shopee_escrita) &&
    a.observado === 'ligado'
  )
}
const ERRO_DESLIGAR: Record<string, string> = {
  nao_escreve: 'O sistema está só observando: não mexe nas plataformas.',
  conta_nao_permitida: 'Conta fora das liberadas para o Flex.',
  conta_nao_pronta: 'A conta não está pronta (o sistema não mexe nela): desligue pelo painel da plataforma.',
  nao_avaliado: 'O sistema ainda não conferiu o anúncio.',
  shopee_so_leitura: 'Na Shopee o sistema só confere: desligue à mão no Seller Center.',
  sem_acesso: 'Não deu para acessar a conta na plataforma (reconecte em Integrações).',
  fora_do_escopo: 'A conta não é da sua equipe.',
}
async function desligarSelecionados() {
  const lista = [...paraDesligar.value]
  if (!lista.length || emLote.value) return
  const ok = confirm(
    `Desligar o Flex em ${lista.length} anúncio(s)?\n\n` +
      'O sistema desliga um por um na plataforma. Eles NÃO voltam a ligar sozinhos: ' +
      `com peça em ${localCidade.value}, ficam em "Esperando aprovação" até alguém aprovar de novo.`,
  )
  if (!ok) return
  loteAcao.value = 'desligar'
  loteTotal.value = lista.length
  loteFeitos.value = 0
  const r = { desligados: 0, ja: 0, confirmando: 0, ocupados: 0, promocao: 0, outros: 0, erros: 0 }
  const motivosErro = new Set<string>()
  for (const a of lista) {
    const k = chave(a)
    aprovando.value = new Set([...aprovando.value, k])
    try {
      const resp = await api<{ modo: string; resultado: string; desligado: boolean; estado: FlexAnuncio | null }>(
        `/api/flex/anuncios/${a.integration_id}/${encodeURIComponent(a.external_id)}/desligar`,
        { method: 'POST' },
      )
      trocarLinha(k, resp.estado)
      if (resp.resultado === 'ok') r.desligados++
      else if (resp.resultado === 'ja_desligado') r.ja++
      else if (resp.resultado === 'ocupado') r.ocupados++
      else if (resp.estado && shopeeConfirmando(resp.estado)) r.confirmando++
      else if ((resp.estado?.ultimo_erro || '').includes('em promoção')) r.promocao++
      else r.outros++
    } catch (e: any) {
      r.erros++
      const d = e?.data?.detail
      const code = typeof d === 'object' && d && !Array.isArray(d) ? d.code : null
      motivosErro.add(ERRO_DESLIGAR[code] || (typeof d === 'object' && d?.detalhe) || e?.message || 'erro')
    } finally {
      const s = new Set(aprovando.value)
      s.delete(k)
      aprovando.value = s
      loteFeitos.value++
    }
  }
  loteAcao.value = null
  soltarMarcados(lista)
  const partes = [
    r.desligados && `${r.desligados} desligado(s)`,
    r.ja && `${r.ja} já estava(m) desligado(s)`,
    r.confirmando && `${r.confirmando} com a Shopee confirmando (até 15 min)`,
    r.ocupados && `${r.ocupados} o sistema estava mexendo agora — tente de novo em 1 minuto`,
    r.promocao && `${r.promocao} em promoção: a Shopee não deixa mudar o envio — desligue no Seller Center`,
    r.outros && `${r.outros} não desligou — veja o erro na linha`,
    r.erros && `${r.erros} não deu para desligar: ${[...motivosErro].join(' ')}`,
  ].filter(Boolean) as string[]
  const tudoCerto = !r.ocupados && !r.promocao && !r.outros && !r.erros
  ;(tudoCerto ? toasts.success : toasts.warning)(`Desligar selecionados: ${lista.length}`, partes)
  emit('mudou')
  carregarAnuncios()
}

// ---- Emergência (job no servidor, com andamento) ----
// O botão só tira as aprovações e põe o job na fila; o servidor desliga em
// paralelo e grava o andamento, que a tela consulta a cada 2 s (também
// depois de recarregar a página: a última emergência em andamento continua
// aparecendo aqui).
type FlexEmergencia = {
  id: number | null
  status: 'na_fila' | 'rodando' | 'concluida' | 'falhou' | null
  modo: string
  escreve: boolean
  erro: string | null
  ja_em_andamento: boolean
  resumo: Record<string, any>
}
const emergenciaAtual = ref<FlexEmergencia | null>(null)
let emergenciaTimer: ReturnType<typeof setTimeout> | null = null
const emergenciaAndando = computed(
  () => !!emergenciaAtual.value && ['na_fila', 'rodando'].includes(emergenciaAtual.value.status || ''),
)

function avisoEmergenciaFinal(e: FlexEmergencia) {
  const r = e.resumo || {}
  if (e.status === 'falhou') {
    toasts.error('A emergência parou no meio', [e.erro || 'erro', 'Aperte de novo para continuar.'])
    return
  }
  if (!e.escreve) {
    const naoLidos = Math.max(0, (r.alvos || 0) - (r.ligados_conhecidos || 0))
    toasts.info('Simulação da emergência (nada mudou)', [
      `${r.ligados_conhecidos || 0} anúncio(s) com Flex ligado seriam desligados.`,
      naoLidos ? `${naoLidos} anúncio(s) ainda não conferidos também seriam desligados por segurança.` : '',
      r.contas_sem_flex ? `${r.contas_sem_flex} conta(s) sem Flex na plataforma ficariam de fora.` : '',
      r.aprovacoes ? `${r.aprovacoes} aprovação(ões) seriam retiradas — continuam valendo, nada foi retirado.` : '',
    ].filter(Boolean))
    return
  }
  const linhas = [
    `${r.desligados || 0} anúncio(s) desligados.`,
    r.ja_desligados ? `${r.ja_desligados} já estavam desligados.` : '',
    r.aprovacoes ? `${r.aprovacoes} aprovação(ões) retiradas.` : '',
    r.falhas ? `${r.falhas} falharam — veja o erro na linha do anúncio.` : '',
    r.ocupados ? `${r.ocupados} estavam sendo mexidos pelo sistema naquele instante e NÃO foram desligados.` : '',
    r.sem_cliente ? `${r.sem_cliente} são de conta sem acesso à plataforma: desligue à mão no painel da plataforma.` : '',
    r.shopee_so_leitura
      ? `${r.shopee_so_leitura} da Shopee NÃO foram mexidos (o sistema só lê a Shopee): desligue à mão no Seller Center.`
      : '',
    r.contas_sem_flex ? `${r.contas_sem_flex} conta(s) sem Flex na plataforma ficaram de fora (não há o que desligar).` : '',
    r.restantes ? `Ainda faltam ${r.restantes}: aperte de novo para continuar.` : '',
  ].filter(Boolean)
  if (r.falhas || r.restantes || r.ocupados || r.sem_cliente || r.shopee_so_leitura)
    toasts.warning('Emergência: desligamento parcial', linhas)
  else toasts.success('Emergência: Flex desligado', linhas)
}

async function acompanharEmergencia(id: number, avisar = true) {
  if (emergenciaTimer) clearTimeout(emergenciaTimer)
  try {
    const e = await api<FlexEmergencia>(`/api/flex/emergencia/${id}`)
    emergenciaAtual.value = e
    if (e.status === 'na_fila' || e.status === 'rodando') {
      emergenciaTimer = setTimeout(() => acompanharEmergencia(id, avisar), 2000)
      return
    }
    if (avisar) avisoEmergenciaFinal(e)
    emit('mudou')
    await carregarTudo()
  } catch {
    // Falha de rede passageira: tenta de novo um pouco depois.
    emergenciaTimer = setTimeout(() => acompanharEmergencia(id, avisar), 5000)
  }
}

async function retomarEmergencia() {
  try {
    const e = await api<FlexEmergencia | null>('/api/flex/emergencia/ultima')
    if (e && e.id && (e.status === 'na_fila' || e.status === 'rodando')) {
      emergenciaAtual.value = e
      acompanharEmergencia(e.id)
    }
  } catch {
    // Informativo: sem isso a tela continua funcionando.
  }
}

// 12/11/2026: o botão "Desligar tudo" saiu da tela (no lugar dele, "Desligar
// selecionados"). O andamento continua aparecendo se uma emergência pedida
// pela API estiver rodando.
function andamentoTexto(e: FlexEmergencia): string {
  const r = e.resumo || {}
  if (e.status === 'na_fila') return 'Na fila do servidor — começa em instantes.'
  const total = Math.min(r.alvos || 0, 3000)
  return (
    `${e.escreve ? 'Desligando' : 'Simulando'}: ${r.processados || 0} de ${total || '…'} anúncio(s)` +
    (e.escreve ? ` · ${r.desligados || 0} desligados` : '') +
    (r.falhas ? ` · ${r.falhas} falhas` : '') +
    (r.ocupados ? ` · ${r.ocupados} ocupados` : '')
  )
}

// Pedido Flex que saiu sem passar pelo .sp: a pessoa diz que acertou o
// estoque no Bling (transferência para o .sp) — o saldo Flex para de descontar.
const acertando = ref<Set<number>>(new Set())
async function marcarAcertado(p: FlexPedido) {
  if (!props.canEdit || acertando.value.has(p.bling_id)) return
  const ok = confirm(
    `Pedido ${p.numero || p.bling_id}: você já acertou o estoque no Bling?\n\n` +
      `Ele saiu pelo Flex (estoque .${p.lote || localLote.value}), mas o Bling baixou outro lote. Confirme só depois ` +
      `de transferir a peça para o .${p.lote || localLote.value} no Bling — até lá o sistema desconta o pedido das peças livres de lá.`,
  )
  if (!ok) return
  acertando.value = new Set([...acertando.value, p.bling_id])
  try {
    await api(`/api/flex/pedidos/${p.bling_id}/acertado`, { method: 'POST' })
    toasts.success('Estoque acertado', `Pedido ${p.numero || p.bling_id}.`)
    emit('mudou')
    await carregarPedidos()
  } catch (e: any) {
    const d = e?.data?.detail
    toasts.error(
      'Não foi possível marcar',
      (typeof d === 'object' && d ? d.detalhe || d.code : null) || e?.message || 'erro',
    )
    await carregarPedidos()
  } finally {
    const s = new Set(acertando.value)
    s.delete(p.bling_id)
    acertando.value = s
  }
}

// Conferidos de verdade na plataforma (o resto a regra avaliou, mas o
// Mercado Livre / a Shopee ainda não foi lido).
const conferidos = computed(() => Math.max(0, resumo.value.avaliados - resumo.value.nao_lidos))

// Lista vazia: dizer POR QUÊ (12/11/2026 — com "Mercado Livre" a lista ficava
// vazia sem explicação: as contas do ML no Flex não têm anúncio de produto com
// peça no .sp). Os cartões (resumo) já seguem a plataforma e a conta.
function listaNomes(nomes: string[]): string {
  if (nomes.length <= 1) return nomes[0] || ''
  return `${nomes.slice(0, -1).join(', ')} e ${nomes[nomes.length - 1]}`
}
const vazioVerTodos = computed(() => !!filtroPecas.value && resumo.value.avaliados > 0 && !(resumo.value.com_peca ?? 0))
const vazioTexto = computed(() => {
  const conta = filtroConta.value ? contasDoFiltro.value.find((c) => c.id === filtroConta.value) : null
  const escopo = conta
    ? `da conta ${(conta.nome || '').trim()}`
    : filtroPlataforma.value === 'ml' ? 'do Mercado Livre' : filtroPlataforma.value === 'shopee' ? 'da Shopee' : ''
  if (resumo.value.avaliados === 0) return escopo ? `Nenhum anúncio ${escopo} no Flex.` : 'Nenhum anúncio avaliado ainda.'
  if (vazioVerTodos.value) {
    if (!escopo)
      return `Nenhum anúncio com peça em ${localCidade.value} ainda — o sistema calcula as peças na próxima conferência (até ${config.value?.intervalo_min ?? 15} min).`
    const nomes = conta ? [(conta.nome || '').trim()] : contasDoFiltro.value.map((c) => (c.nome || c.id.slice(0, 8)).trim())
    const quem = nomes.length === 1 ? `a conta ${nomes[0]} não tem` : `as contas ${listaNomes(nomes)} não têm`
    return `Nenhum anúncio ${escopo} com peça em ${localCidade.value}: ${quem} anúncio de produto com peça no .${localLote.value}.`
  }
  return 'Nenhum anúncio com esses filtros.'
})


// ---- Padrão de Chamados/Conciliação (11/11/2026) ----
// Conta pronta = o robô mexe nela: liberada, com Flex conferido e (ML) a saída
// do Flex na cidade do local. As outras aparecem em cinza, com o que falta.
const contasProntas = computed(
  () =>
    new Set(
      (config.value?.contas || [])
        .filter((c) => c.existe && c.flex_ativo === true && c.flex_origem_ok !== false)
        .map((c) => c.id),
    ),
)
function contaPronta(a: FlexAnuncio): boolean {
  return contasProntas.value.has(a.integration_id)
}
function motivoConta(a: FlexAnuncio): string {
  const c = (config.value?.contas || []).find((x) => x.id === a.integration_id)
  if (!c || !c.existe) return 'conta fora das liberadas'
  if (c.plataforma === 'shopee') return c.flex_ativo === false ? 'loja sem Entrega Direta' : 'loja ainda não conferida'
  if (c.flex_ativo === false) return 'conta sem Flex no ML'
  if (c.flex_ativo === null) return 'conta ainda não conferida'
  if (c.flex_origem_ok === false) return c.flex_origem_cidade ? `conta sai de ${c.flex_origem_cidade}` : 'lendo de onde sai o Flex'
  return 'conta não pronta'
}
// Na Shopee o motor só confere: com peça, a regra diz "desligado (só leitura)",
// mas quem decide é a pessoa no Seller Center — a tela mostra "Pode ter Flex".
function shopeeSoLeitura(a: FlexAnuncio): boolean {
  return a.plataforma === 'shopee' && (a.motivo || '').startsWith('Shopee só leitura')
}
function querInfo(a: FlexAnuncio): { texto: string; cls: string; title: string } {
  const n = config.value?.n_liga ?? 3
  const temPeca = (a.saldo_sp ?? -1) >= n
  if (temPeca && (!contaPronta(a) || shopeeSoLeitura(a))) {
    return {
      ...DESEJADO.ligado!,
      cls: `${DESEJADO.ligado!.cls}${contaPronta(a) ? '' : ' opacity-60'}`,
      title: contaPronta(a) ? 'Tem peça para ter Flex — ligar é à mão no Seller Center' : `Tem peça para ter Flex quando a conta ficar pronta (${motivoConta(a)})`,
    }
  }
  // 12/11/2026 (Vinicius: "ele pode ter flex, eu só preciso tirar a
  // promoção"): a recusa da Shopee por promoção não é "Não pode ter Flex".
  if (temPeca && emPromocao(a)) {
    return {
      ...DESEJADO.ligado!,
      title: 'Pode ter Flex — a Shopee só recusou por causa da promoção: tire o anúncio da promoção no Seller Center e clique em Tentar novamente.',
    }
  }
  // 12/11/2026 (revisão): com peça de menos, a conta não pronta mostrava "Não
  // pode ter Flex" (vermelho) e a pronta "Flex desligado" (cinza) — o mesmo
  // caso. O que pesa aqui são as peças: cinza, apagado, como os outros dela.
  if (!contaPronta(a) && a.saldo_sp !== null && a.saldo_sp !== undefined && a.saldo_sp < n) {
    return {
      ...DESEJADO.desligado!,
      cls: `${DESEJADO.desligado!.cls} opacity-60`,
      title: `Só ${a.saldo_sp} peça(s) em ${localCidade.value} — precisa de ${n} para ligar (e a conta ainda não está pronta: ${motivoConta(a)})`,
    }
  }
  return { ...desejadoInfo(a), title: a.motivo_claro || a.motivo || '' }
}
type Acao = { tipo: 'aprovar' | 'texto' | 'chip'; texto?: string; cls?: string; dot?: string; title?: string }
function acaoDe(a: FlexAnuncio): Acao {
  if (podeAprovar(a)) return { tipo: 'aprovar' }
  const n = config.value?.n_liga ?? 3
  const nd = config.value?.n_desliga ?? 1
  if (!contaPronta(a)) {
    return { tipo: 'texto', texto: motivoConta(a), cls: 'text-muted-foreground', dot: a.plataforma === 'shopee' ? 'bg-slate-400' : 'bg-amber-500' }
  }
  if (a.aguardando_aprovacao) return { tipo: 'texto', texto: 'esperando aprovação', cls: 'text-amber-700 dark:text-amber-400' }
  if (shopeeConfirmando(a)) return { tipo: 'texto', texto: 'Shopee confirmando', cls: 'text-muted-foreground' }
  if (aprovadoSemLigar(a)) return { tipo: 'texto', texto: 'aprovado — falta ligar', cls: 'text-muted-foreground' }
  if (shopeeSoLeitura(a) || (a.plataforma === 'shopee' && !config.value?.shopee_escrita)) {
    if (a.observado !== 'ligado' && (a.saldo_sp ?? -1) >= n) return { tipo: 'texto', texto: 'ligar no Seller Center', cls: 'font-semibold text-blue-700 dark:text-blue-400' }
    if (a.observado === 'ligado' && (a.saldo_sp ?? 0) < nd) return { tipo: 'texto', texto: 'desligar no Seller Center', cls: 'font-semibold text-rose-700 dark:text-rose-400' }
  }
  if (a.observado === 'ligado' && a.desejado !== 'ligado') {
    return { tipo: 'texto', texto: config.value?.pode_escrever ? 'o sistema desliga' : 'deveria desligar', cls: 'font-semibold text-rose-700 dark:text-rose-400' }
  }
  // 12/11/2026: no lugar do "✓ certo", a etiqueta no modelo da coluna
  // "Sistema quer" — Ativado (verde) / Desativado (cinza).
  if (a.observado && (a.observado === 'ligado') === (a.desejado === 'ligado')) {
    const ligado = a.observado === 'ligado'
    return {
      tipo: 'chip',
      texto: ligado ? 'Ativado' : 'Desativado',
      cls: (ligado ? DESEJADO.ligado : DESEJADO.desligado)!.cls,
      title: ligado ? 'O Flex está ligado na plataforma, como o sistema quer.' : 'O Flex está desligado na plataforma, como o sistema quer.',
    }
  }
  // Conta pronta, plataforma ainda não lida (o filtro Ação "Ainda não conferido").
  if (!a.observado) return { tipo: 'texto', texto: 'ainda não conferido', cls: 'text-muted-foreground' }
  return { tipo: 'texto', texto: '—', cls: 'text-muted-foreground' }
}

// Cartões do topo: ATALHOS dos menus (12/11/2026) — o cartão marca o menu
// certo, então os menus sempre mostram o filtro que está valendo (antes o
// cartão filtrava escondido e os menus diziam "Todos"). Clicar de novo volta.
type CartaoFlex = 'com' | 'flex' | 'aguardando' | 'desligar' | 'nao_pronta'
const cartaoAtivo = computed<CartaoFlex | null>(() => {
  if (filtroQuer.value) return null
  const a = filtroAcao.value
  const p = filtroPecas.value
  if (a === 'aprovar') return 'aguardando'
  if (a === 'desliga') return 'desligar'
  if (a === 'conta_nao_pronta' && p === 'com') return 'nao_pronta'
  if (!a && p === 'flex') return 'flex'
  if (!a && p === 'com') return 'com'
  return null
})
const cartoes = computed(() => {
  const r = resumo.value
  return [
    { k: 'com' as CartaoFlex, label: `Com peça em ${localCidade.value}`, n: r.com_peca ?? 0, cor: '', dica: `anúncios cujo produto tem peça livre no estoque .${localLote.value}` },
    { k: 'flex' as CartaoFlex, label: `Dá para ter Flex (${config.value?.n_liga ?? 3}+ peças)`, n: r.da_para_ter ?? 0, cor: 'text-emerald-600', dica: 'com peça suficiente para ligar o Flex' },
    { k: 'aguardando' as CartaoFlex, label: 'Esperando aprovação', n: r.aguardando, cor: 'text-amber-600', dica: 'o sistema quer ligar e espera você aprovar' },
    { k: 'desligar' as CartaoFlex, label: 'Ligados sem peça', n: r.desligar, cor: 'text-red-600', dica: 'Flex ligado, mas a regra quer desligado' },
    { k: 'nao_pronta' as CartaoFlex, label: 'Conta não pronta', n: r.conta_nao_pronta ?? 0, cor: 'text-amber-600', dica: 'tem peça, mas a conta ainda não está pronta para o Flex' },
  ]
})
function clicarCartao(k: CartaoFlex) {
  const voltar = cartaoAtivo.value === k && k !== 'com'
  filtroQuer.value = ''
  filtroAcao.value = ''
  filtroPecas.value = 'com'
  if (voltar) return
  if (k === 'flex') filtroPecas.value = 'flex'
  else if (k === 'aguardando') filtroAcao.value = 'aprovar'
  else if (k === 'desligar') filtroAcao.value = 'desliga' // o watch solta as peças
  else if (k === 'nao_pronta') filtroAcao.value = 'conta_nao_pronta'
}

// "Onde agir hoje" (à direita): o que depende de uma pessoa agora.
type AgirFlex = { chave: string; dot: string; titulo: string; detalhe: string; acao?: string; fazer?: () => void; grave?: boolean }
function irParaPedidos() {
  if (typeof document !== 'undefined') document.getElementById('flex-pedidos-sem-sp')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
const agirItens = computed<AgirFlex[]>(() => {
  const out: AgirFlex[] = []
  const r = resumo.value
  if (pedidosSemSp.value.length) {
    out.push({
      chave: 'pedidos', dot: 'bg-red-600', grave: true,
      titulo: `Pedidos Flex sem peça em ${localCidade.value} · ${pedidosSemSp.value.length}`,
      detalhe: pedidosSemSp.value.slice(0, 3).map((p) => p.numero || p.bling_id).join(' · ') + (pedidosSemSp.value.length > 3 ? ' …' : ''),
      acao: 'Ver', fazer: irParaPedidos,
    })
  }
  if (r.aguardando) out.push({ chave: 'aguardando', dot: 'bg-amber-500', titulo: `Esperando sua aprovação · ${r.aguardando}`, detalhe: 'o sistema quer ligar o Flex', acao: 'Ver', fazer: () => clicarCartao('aguardando') })
  if (r.desligar) out.push({ chave: 'desligar', dot: 'bg-red-600', titulo: `Ligados sem peça · ${r.desligar}`, detalhe: config.value?.pode_escrever ? 'o sistema desliga sozinho' : 'desligar (o sistema está só observando)', acao: 'Ver', fazer: () => clicarCartao('desligar') })
  // 12/11/2026 (revisão): o "Ver" das contas abre o quadro Contas no grupo —
  // antes filtrava a lista por "conta não pronta" e, sem anúncio do ML com
  // peça, mostrava os da Shopee.
  const fora = gruposContas.value.find((g) => g.chave === 'fora')?.contas || []
  if (fora.length) out.push({ chave: 'fora', dot: 'bg-amber-500', titulo: `${fora.length} conta(s) do ML saem de outra cidade`, detalhe: `trocar a saída do Flex para ${localCidade.value} no painel do ML`, acao: 'Ver', fazer: () => abrirGrupoContas('fora') })
  const shopee = gruposContas.value.find((g) => g.chave === 'shopee')?.contas || []
  if (shopee.length) out.push({ chave: 'shopee', dot: 'bg-slate-400', titulo: `${shopee.length} loja(s) Shopee sem Entrega Direta`, detalhe: 'ligar no Seller Center e clicar Sincronizar Flex', acao: 'Ver', fazer: () => abrirGrupoContas('shopee') })
  if (r.sem_controle) {
    // Flex ligado em conta que o sistema não controla — hoje, o ML que sai de
    // Piracicaba ("fora do controle" assustava e não dizia o que era).
    const cidades = [
      ...new Set(
        (config.value?.contas || [])
          .filter((c) => c.existe && c.plataforma === 'ml' && c.flex_ativo === true && c.flex_origem_ok === false && c.flex_origem_cidade)
          .flatMap((c) => c.flex_origem_cidade!.split(',').map((x) => x.trim()).filter(Boolean)),
      ),
    ]
    out.push({
      chave: 'sem_controle',
      dot: 'bg-slate-400',
      titulo: cidades.length
        ? `${r.sem_controle.toLocaleString('pt-BR')} anúncios com Flex saindo de ${listaNomes(cidades)}`
        : `${r.sem_controle.toLocaleString('pt-BR')} anúncios com Flex em conta não pronta`,
      detalhe: 'o sistema não mexe neles — desligar é pelo painel da plataforma',
    })
  }
  return out
})
// O "Ver" das contas: abre o grupo no quadro Contas e rola até ele.
const grupoDestaque = ref<string | null>(null)
let destaqueTimer: ReturnType<typeof setTimeout> | null = null
function abrirGrupoContas(chave: string) {
  gruposAbertos.value = new Set([...gruposAbertos.value, chave])
  grupoDestaque.value = chave
  if (destaqueTimer) clearTimeout(destaqueTimer)
  destaqueTimer = setTimeout(() => (grupoDestaque.value = null), 2500)
  nextTick(() => {
    if (typeof document !== 'undefined')
      document.querySelector(`[data-flex-grupo="${chave}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  })
}
const MODO_CHIP: Record<string, { texto: string; cls: string }> = {
  desligado: { texto: 'Desligado', cls: 'bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300' },
  observar: { texto: 'Só observando', cls: 'bg-amber-100 text-amber-800 dark:bg-amber-950/50 dark:text-amber-300' },
  piloto: { texto: 'Piloto', cls: 'bg-blue-100 text-blue-800 dark:bg-blue-950/50 dark:text-blue-300' },
  ativo: { texto: 'Ativo', cls: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/50 dark:text-emerald-300' },
}
const modoChip = computed(() => (config.value ? MODO_CHIP[config.value.modo] || MODO_CHIP.desligado! : { texto: 'lendo…', cls: 'bg-muted text-muted-foreground' }))
const contasProntasTotal = computed(() => {
  const todas = (config.value?.contas || []).filter((c) => c.existe)
  return { prontas: todas.filter((c) => contasProntas.value.has(c.id)).length, total: todas.length }
})

// ---- Chaves do Flex na tela (11/11/2026) ----
// Antes só no .env do servidor (só o Eduardo mexia). Troca: admin e o gerente
// (cairo SA). Nasce como está no servidor; vale na próxima rodada do robô.
const MODOS_OPCOES = [
  { v: 'desligado', t: 'Desligado — não confere nem mexe' },
  { v: 'observar', t: 'Só observando — confere e mostra, não mexe' },
  { v: 'piloto', t: 'Piloto — desliga sozinho, liga com aprovação' },
  { v: 'ativo', t: 'Ativo — igual ao piloto' },
]
const editandoAjustes = ref(false)
const salvandoAjustes = ref(false)
const ajusteForm = ref({ modo: 'observar', shopee_escrita: false, pedido_no_sp: false, kits: false, contas: [] as string[] })
// As contas que podem entrar no Flex (12/11/2026: antes a lista só mudava no
// servidor). Marcadas = as que estão na lista do robô agora.
const contasOpcoes = computed(() => config.value?.contas_opcoes || [])
const contasNoFlexAgora = computed(() => contasOpcoes.value.filter((o) => o.no_flex).map((o) => o.id))
const contasOpcoesGrupos = computed(() =>
  [
    { chave: 'ml', titulo: 'Mercado Livre', contas: contasOpcoes.value.filter((o) => o.plataforma === 'ml') },
    { chave: 'shopee', titulo: 'Shopee', contas: contasOpcoes.value.filter((o) => o.plataforma === 'shopee') },
  ].filter((g) => g.contas.length),
)
function nomeOpcao(id: string) {
  const o = contasOpcoes.value.find((x) => x.id === id)
  return o ? `${o.nome.trim()} (${o.plataforma === 'ml' ? 'ML' : 'Shopee'})` : id.slice(0, 8)
}
// O que aparece fora do formulário: quantas contas de cada plataforma estão
// na lista do robô (só as que existem).
const contasNoFlexResumo = computed(() => {
  const todas = (config.value?.contas || []).filter((c) => c.existe)
  const ml = todas.filter((c) => c.plataforma === 'ml').map((c) => (c.nome || '').trim())
  const shopee = todas.filter((c) => c.plataforma === 'shopee').map((c) => (c.nome || '').trim())
  return { ml, shopee }
})
function abrirAjustes() {
  const c = config.value
  if (!c) return
  ajusteForm.value = {
    modo: c.modo,
    shopee_escrita: !!c.shopee_escrita,
    pedido_no_sp: !!c.pedido_no_sp,
    kits: !!c.kits,
    contas: [...contasNoFlexAgora.value],
  }
  editandoAjustes.value = true
}
function simNao(v: boolean) {
  return v ? 'SIM' : 'NÃO'
}
async function salvarAjustes() {
  const c = config.value
  if (!c) return
  const f = ajusteForm.value
  const mudancas: string[] = []
  if (f.modo !== c.modo) mudancas.push(`• Modo: ${MODO_CHIP[c.modo]?.texto || c.modo} → ${MODO_CHIP[f.modo]?.texto || f.modo}`)
  if (f.shopee_escrita !== c.shopee_escrita) mudancas.push(`• DaVinci liga/desliga na Shopee: ${simNao(c.shopee_escrita)} → ${simNao(f.shopee_escrita)}`)
  if (f.pedido_no_sp !== c.pedido_no_sp) mudancas.push(`• Pedido Flex vai para o .${localLote.value}: ${simNao(c.pedido_no_sp)} → ${simNao(f.pedido_no_sp)}`)
  if (f.kits !== !!c.kits) mudancas.push(`• Kits entram: ${simNao(!!c.kits)} → ${simNao(f.kits)}`)
  const antesContas = new Set(contasNoFlexAgora.value)
  const depoisContas = new Set(f.contas)
  const entram = f.contas.filter((id) => !antesContas.has(id))
  const saem = contasNoFlexAgora.value.filter((id) => !depoisContas.has(id))
  if (entram.length) mudancas.push(`• Conta(s) que entram no Flex: ${entram.map(nomeOpcao).join(', ')}`)
  if (saem.length) mudancas.push(`• Conta(s) que saem do Flex: ${saem.map(nomeOpcao).join(', ')}`)
  if (!mudancas.length) {
    editandoAjustes.value = false
    return
  }
  const avisos: string[] = []
  if (f.modo === 'piloto' || f.modo === 'ativo')
    avisos.push(`Em ${f.modo}, o sistema DESLIGA sozinho o Flex dos anúncios sem peça em ${localCidade.value} (nas contas prontas). Ligar continua precisando de Aprovar.`)
  if (f.shopee_escrita && (f.modo === 'piloto' || f.modo === 'ativo'))
    avisos.push('Na Shopee o sistema passa a ligar/desligar a Entrega Direta nos anúncios. Aprove UM primeiro e confira no Seller Center se a Shopee Xpress continuou ligada.')
  if (f.pedido_no_sp)
    avisos.push(`O robô de prioridade passa a levar todo pedido Flex para o estoque .${localLote.value}, e a NF automática espera o tipo de envio ser lido.`)
  if (entram.some((id) => contasOpcoes.value.find((o) => o.id === id)?.plataforma === 'ml'))
    avisos.push(`Conta do Mercado Livre só passa a ter Flex de verdade com o Flex ativo no ML e a saída em ${localCidade.value}. Senão ela aparece como "conta não pronta" e o sistema não mexe nela.`)
  if (saem.length)
    avisos.push('Tirar uma conta NÃO desliga o Flex que já está ligado nos anúncios dela — o sistema só para de mexer nela.')
  if (f.kits && !c.kits)
    avisos.push(`Kits entram: o anúncio de kit (celular + fone, relógio…) passa a valer pelo kit .${localLote.value} do Bling, que tem o estoque da peça mais escassa — sem o kit .${localLote.value} cadastrado, não liga. Nada liga sozinho: com peça, ele vai para "Esperando aprovação".`)
  const ok = confirm(
    `Trocar as chaves do Flex?\n\n${mudancas.join('\n')}` +
      (avisos.length ? `\n\n${avisos.join('\n\n')}` : '') +
      `\n\nVale na próxima conferência (até ${c.intervalo_min} min) ou ao clicar em Sincronizar Flex.`,
  )
  if (!ok) return
  salvandoAjustes.value = true
  try {
    await api('/api/flex/ajustes', {
      method: 'PUT',
      body: {
        ...f,
        modo_antes: c.modo,
        shopee_escrita_antes: c.shopee_escrita,
        pedido_no_sp_antes: c.pedido_no_sp,
        kits_antes: !!c.kits,
        // Só manda as contas quando a tela tem a lista (quem pode trocar).
        ...(contasOpcoes.value.length ? { contas: f.contas, contas_antes: contasNoFlexAgora.value } : {}),
      },
    })
    toasts.success('Chaves do Flex trocadas', mudancas.map((m) => m.replace('• ', '')))
    editandoAjustes.value = false
    emit('mudou')
    await carregarTudo()
  } catch (e: any) {
    const d = e?.data?.detail
    const code = typeof d === 'object' && d && !Array.isArray(d) ? d.code : null
    const msg: Record<string, string> = {
      so_admin: 'Só um admin (ou o gerente) troca as chaves do Flex.',
      ajuste_mudou: 'Alguém trocou essas chaves agora há pouco — confira e tente de novo.',
      conta_invalida: 'Só entra no Flex conta do Mercado Livre ou da Shopee que esteja ativa.',
    }
    toasts.error('Chaves do Flex', (code && msg[code]) || (typeof d === 'object' && d?.detalhe) || e?.message || 'erro')
    if (code === 'ajuste_mudou') {
      editandoAjustes.value = false
      await carregarConfig()
    }
  } finally {
    salvandoAjustes.value = false
  }
}

defineExpose({ recarregar: carregarTudo })
</script>

<template>
  <!-- 11/11/2026: padrão de Chamados/Conciliação. O quadro azul virou a barra
       de cima (modo, saída, botões), os cartões que filtram e, à direita, "Onde agir hoje", as contas e "Como o sistema decide". De
       fábrica a lista mostra só os anúncios com peça no local de saída. -->
  <div class="min-w-0 space-y-2.5">
    <!-- 12/11/2026: os botões vão para a linha das abas da Logística (Vinicius:
         "tem que ficar padrão na linha de cima") — Teleport para o
         #flex-acoes-topo de pages/logistica.vue. Só no navegador (ClientOnly):
         o alvo é da página e o SSR não leva teleport para fora do body. -->
    <ClientOnly>
      <Teleport to="#flex-acoes-topo">
      <div class="flex flex-wrap items-center gap-1.5 font-semibold" data-flex-acoes>
        <button
          type="button"
          class="inline-flex items-center gap-1 rounded-md border px-2.5 py-1 hover:bg-muted disabled:opacity-50"
          :disabled="loading"
          title="Relê a lista (a tela também se atualiza sozinha a cada minuto)"
          @click="carregarTudo"
        >
          <RefreshCw class="size-3.5" :class="loading ? 'animate-spin' : ''" /> Atualizar
        </button>
        <button
          v-if="canEdit"
          type="button"
          class="inline-flex items-center gap-1 rounded-md border px-2.5 py-1 hover:bg-muted disabled:opacity-50"
          :disabled="sincronizando || modoDesligado"
          :title="modoDesligado
            ? 'O Flex automático está desligado — não há o que conferir.'
            : `Pede ao sistema para conferir todos os anúncios e as contas agora (ele já faz isso sozinho a cada ${config?.intervalo_min ?? 15} min).`"
          @click="sincronizar"
        >
          <RefreshCw class="size-3.5" :class="sincronizando ? 'animate-spin' : ''" /> Sincronizar Flex
        </button>
        <button
          v-if="canEdit"
          type="button"
          class="inline-flex items-center gap-1 rounded-md border border-emerald-600 bg-emerald-600 px-2.5 py-1 text-white hover:bg-emerald-700 disabled:opacity-50"
          :disabled="!paraLigar.length || emLote || modoDesligado"
          :title="paraLigar.length
            ? `Aprova e liga o Flex nos ${paraLigar.length} anúncio(s) marcados, um por um. Pede confirmação.`
            : 'Marque no quadradinho da primeira coluna os anúncios com Aprovar.'"
          data-flex-ligar-selecionados
          @click="ligarSelecionados"
        >
          <Check class="size-3.5" :class="loteAcao === 'ligar' ? 'animate-pulse' : ''" />
          {{ loteAcao === 'ligar' ? `Ligando ${loteFeitos} de ${loteTotal}…` : `Ligar selecionados (${paraLigar.length})` }}
        </button>
        <!-- 12/11/2026: no lugar do "Desligar tudo" (que desligava também o
             Flex do ML que sai de Piracicaba) — só os marcados. -->
        <button
          v-if="canEdit"
          type="button"
          class="inline-flex items-center gap-1 rounded-md border border-red-300 px-2.5 py-1 text-red-700 hover:bg-red-50 disabled:opacity-50 dark:border-red-800 dark:text-red-400 dark:hover:bg-red-950/40"
          :disabled="!paraDesligar.length || emLote || !config?.pode_escrever"
          :title="!config?.pode_escrever
            ? 'O sistema está só observando: não mexe nas plataformas.'
            : paraDesligar.length
              ? `Desliga o Flex nos ${paraDesligar.length} anúncio(s) marcados, um por um. Pede confirmação. Eles só voltam a ligar com nova aprovação.`
              : 'Marque no quadradinho da primeira coluna os anúncios com Flex ligado (Ativado).'"
          data-flex-desligar-selecionados
          @click="desligarSelecionados"
        >
          <PowerOff class="size-3.5" :class="loteAcao === 'desligar' ? 'animate-pulse' : ''" />
          {{ loteAcao === 'desligar' ? `Desligando ${loteFeitos} de ${loteTotal}…` : `Desligar selecionados (${paraDesligar.length})` }}
        </button>
      </div>
      </Teleport>
    </ClientOnly>

    <!-- Andamento da emergência (o job roda no servidor) -->
    <div
      v-if="emergenciaAtual && emergenciaAndando"
      class="rounded-md border border-rose-300 bg-rose-50 px-3 py-2 text-sm text-rose-900 dark:border-rose-700 dark:bg-rose-950/40 dark:text-rose-200"
      role="status"
    >
      <span class="font-semibold">Emergência em andamento.</span>
      {{ andamentoTexto(emergenciaAtual) }}
    </div>

    <!-- filtros -->
    <div class="flex flex-wrap items-center gap-1.5 text-xs">
      <label class="inline-flex h-7 items-center gap-1 rounded-md border bg-card px-2">
        <span class="font-semibold text-muted-foreground">Plataforma</span>
        <select v-model="filtroPlataforma" class="bg-transparent outline-none">
          <option value="">Mercado Livre e Shopee</option>
          <option value="ml">Mercado Livre</option>
          <option value="shopee">Shopee</option>
        </select>
      </label>
      <label class="inline-flex h-7 items-center gap-1 rounded-md border bg-card px-2">
        <span class="font-semibold text-muted-foreground">Conta</span>
        <select v-model="filtroConta" class="max-w-[160px] bg-transparent outline-none">
          <option value="">Todas</option>
          <option v-for="c in contasDoFiltro" :key="c.id" :value="c.id">
            {{ (c.nome || c.id.slice(0, 8)).trim() }} · {{ nomePlataforma(c.plataforma) }}
          </option>
        </select>
      </label>
      <label class="inline-flex h-7 items-center gap-1 rounded-md border bg-card px-2" :title="`De fábrica, só os anúncios cujo produto tem peça no estoque .${localLote} (${localCidade})`">
        <span class="font-semibold text-muted-foreground">Mostrar</span>
        <select v-model="filtroPecas" class="bg-transparent outline-none">
          <option value="com">Com peça em {{ localCidade }}</option>
          <option value="flex">Com peça para ter Flex</option>
          <option value="">Todos os anúncios</option>
        </select>
      </label>
      <label class="inline-flex h-7 items-center gap-1 rounded-md border bg-card px-2" title="O selo da coluna &quot;Sistema quer&quot;">
        <span class="font-semibold text-muted-foreground">Sistema quer</span>
        <select v-model="filtroQuer" class="bg-transparent outline-none" data-flex-filtro-quer>
          <option v-for="o in QUERES" :key="o.key" :value="o.key">{{ o.label }}</option>
        </select>
      </label>
      <label class="inline-flex h-7 items-center gap-1 rounded-md border bg-card px-2" title="O que aparece na coluna &quot;Ação&quot;">
        <span class="font-semibold text-muted-foreground">Ação</span>
        <select v-model="filtroAcao" class="bg-transparent outline-none" data-flex-filtro-acao>
          <option v-for="o in ACOES" :key="o.key" :value="o.key">{{ o.label }}</option>
        </select>
      </label>
      <label class="inline-flex h-7 min-w-0 items-center gap-1 rounded-md border bg-card px-2">
        <Search class="size-3.5 text-muted-foreground" />
        <input v-model="busca" class="w-56 bg-transparent outline-none" placeholder="buscar anúncio, título ou família" />
      </label>
      <button v-if="filtrosAtivos" type="button" class="text-muted-foreground underline" @click="limparFiltros">limpar</button>
      <span class="text-muted-foreground">{{ total }} de {{ resumo.avaliados }} anúncios</span>
    </div>

    <!-- cartões: clicar filtra a lista -->
    <div class="grid grid-cols-2 gap-2 lg:grid-cols-5">
      <button
        v-for="k in cartoes"
        :key="k.k"
        type="button"
        class="rounded-md border bg-card px-3 py-1.5 text-center hover:bg-muted/40 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
        :class="cartaoAtivo === k.k ? 'ring-2 ring-primary' : ''"
        :title="cartaoAtivo === k.k && k.k !== 'com' ? 'tirar o filtro' : `filtrar: ${k.dica}`"
        @click="clicarCartao(k.k)"
      >
        <span class="block text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">{{ k.label }}</span>
        <span class="block text-xl font-bold leading-tight" :class="k.cor">{{ k.n }}</span>
      </button>
    </div>

    <!-- 12/11/2026: a faixa "Contas prontas" saiu daqui (repetia o quadro
         Contas, à direita). -->

    <div v-if="erro" class="text-sm text-red-500">erro: {{ erro }}</div>

    <div class="grid items-stretch gap-3 xl:grid-cols-[minmax(0,1fr)_340px]">
      <div class="flex min-w-0 flex-col gap-3">
        <!-- Tabela (computador) -->
        <div class="hidden min-w-0 flex-col rounded-md border bg-card md:flex">
          <div class="relative flex items-center justify-center rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-xs font-semibold text-white">
            <span>Anúncios Flex — {{ filtroPecas === 'com' ? `só os que têm peça em ${localCidade}` : filtroPecas === 'flex' ? 'com peça para ter Flex' : 'todos os anúncios' }}</span>
            <span class="absolute right-3 hidden text-[11px] font-normal text-slate-300 2xl:block">o que o sistema quer × o que está na plataforma</span>
          </div>
          <div class="overflow-x-auto">
            <table class="w-full min-w-[1030px] table-fixed border-collapse text-[11.5px]">
              <colgroup>
                <col class="w-[30px]">
                <col class="w-[112px]"><col>
                <col class="w-[70px]"><col class="w-[60px]">
                <col class="w-[104px]"><col class="w-[108px]">
                <col class="w-[150px]"><col class="w-[26%]">
              </colgroup>
              <thead class="bg-muted text-[10px] uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th class="border-b px-1 py-1 text-center align-middle" rowspan="2">
                    <input
                      type="checkbox"
                      class="size-3.5 accent-emerald-600 disabled:opacity-30"
                      :checked="todosMarcados"
                      :indeterminate="algunsMarcados"
                      :disabled="!marcaveis.length || emLote || !canEdit"
                      :title="marcaveis.length ? `Marcar os ${marcaveis.length} anúncio(s) desta página que dá para ligar (Aprovar) ou desligar (Flex ligado)` : 'Nenhum anúncio para ligar ou desligar nesta página'"
                      data-flex-marcar-todos
                      @change="marcarTodos(($event.target as HTMLInputElement).checked)"
                    />
                  </th>
                  <th class="border-b px-1.5 py-1 text-center font-semibold" colspan="2">Anúncio</th>
                  <th class="border-b border-l border-l-slate-300 px-1.5 py-1 text-center font-semibold dark:border-l-slate-600" colspan="2">{{ localCidade }}</th>
                  <th class="border-b border-l border-l-slate-300 px-1.5 py-1 text-center font-semibold dark:border-l-slate-600" colspan="2">Flex</th>
                  <th class="border-b border-l border-l-slate-300 px-1.5 py-1 text-center font-semibold dark:border-l-slate-600" colspan="2">Onde agir</th>
                </tr>
                <tr>
                  <th class="border-b px-1.5 py-1 text-center font-semibold">Conta</th>
                  <th class="border-b px-1.5 py-1 text-left font-semibold">Anúncio</th>
                  <th class="border-b border-l border-l-slate-300 px-1.5 py-1 text-center font-semibold dark:border-l-slate-600" title="Família do produto (o código sem o lote: dg053.ci e dg053.sp são a família dg053)">Família</th>
                  <th
                    class="border-b px-1.5 py-1 text-center font-semibold"
                    :title="`Peças do lote .${localLote} (${localCidade}) livres para o Flex: o estoque do .${localLote} menos os pedidos Flex que ainda não saíram dele. Com mais de uma família, vale a menor.`"
                  >Peças</th>
                  <th class="border-b border-l border-l-slate-300 px-1.5 py-1 text-center font-semibold dark:border-l-slate-600" title="O que o Mercado Livre / a Shopee mostrou na última conferência">Na plataforma</th>
                  <th class="border-b px-1.5 py-1 text-center font-semibold" title="O que a regra do sistema quer para este anúncio">Sistema quer</th>
                  <th class="border-b border-l border-l-slate-300 px-1.5 py-1 text-center font-semibold dark:border-l-slate-600">Ação</th>
                  <th class="border-b px-1.5 py-1 text-left font-semibold">Por quê</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="a in itens"
                  :key="chave(a)"
                  class="border-b align-middle hover:bg-muted/40"
                  :class="[
                    marcados.has(chave(a)) && podeAprovar(a)
                      ? 'bg-emerald-50/80 dark:bg-emerald-950/30'
                      : marcados.has(chave(a)) && podeDesligar(a)
                        ? 'bg-rose-50/80 dark:bg-rose-950/30'
                        : a.aguardando_aprovacao ? 'bg-amber-50/60 dark:bg-amber-950/20' : '',
                    !contaPronta(a) ? 'text-muted-foreground' : '',
                  ]"
                >
                  <td class="px-1 py-1.5 text-center">
                    <input
                      type="checkbox"
                      class="size-3.5 accent-emerald-600 disabled:opacity-25"
                      :checked="(podeAprovar(a) || podeDesligar(a)) && marcados.has(chave(a))"
                      :disabled="!(podeAprovar(a) || podeDesligar(a)) || emLote || aprovando.has(chave(a))"
                      :title="podeAprovar(a)
                        ? 'Marcar para ligar com o botão Ligar selecionados'
                        : podeDesligar(a)
                          ? 'Marcar para desligar com o botão Desligar selecionados'
                          : 'Só dá para marcar anúncio com Aprovar (para ligar) ou com o Flex ligado em conta pronta (para desligar)'"
                      data-flex-marcar
                      @change="marcar(a, ($event.target as HTMLInputElement).checked)"
                    />
                  </td>
                  <td class="px-1.5 py-1.5 text-center">
                    <div class="truncate" :class="contaPronta(a) ? 'text-foreground' : ''" :title="a.conta || ''">{{ a.conta || '—' }}</div>
                    <div class="text-[10px] text-muted-foreground">{{ nomePlataforma(a.plataforma) }}</div>
                  </td>
                  <td class="px-1.5 py-1.5">
                    <div class="flex items-center gap-1 font-mono text-[11px]">
                      {{ a.external_id }}
                      <a
                        v-if="linkAnuncio(a)"
                        :href="linkAnuncio(a)!"
                        target="_blank"
                        rel="noopener"
                        class="text-muted-foreground hover:text-foreground"
                        :title="a.plataforma === 'shopee' ? 'Abrir o anúncio na Shopee (nova guia)' : 'Abrir o anúncio no Mercado Livre (nova guia)'"
                      ><ExternalLink class="size-3" /></a>
                      <span
                        v-if="statusTexto(a)"
                        class="rounded border border-zinc-300 px-1 font-sans text-[10px] text-zinc-700 dark:border-zinc-600 dark:text-zinc-300"
                        title="Status do anúncio na plataforma: anúncio que não está ativo não pede aprovação"
                      >{{ statusTexto(a) }}</span>
                    </div>
                    <div v-if="a.titulo" class="truncate" :class="contaPronta(a) ? 'text-foreground' : ''" :title="a.titulo">{{ a.titulo }}</div>
                  </td>
                  <td class="truncate border-l border-l-slate-300 px-1.5 py-1.5 text-center font-mono text-[11px] dark:border-l-slate-600" :title="familiasTexto(a)">{{ familiasTexto(a) }}</td>
                  <td class="px-1.5 py-1.5 text-center font-bold tabular-nums" :class="saldoCls(a)">{{ a.saldo_sp ?? '—' }}</td>
                  <td class="border-l border-l-slate-300 px-1.5 py-1.5 text-center text-[11px] dark:border-l-slate-600">
                    <div :class="divergente(a) ? 'font-semibold text-amber-700 dark:text-amber-400' : a.observado ? 'text-foreground' : 'text-muted-foreground'">{{ observadoTexto(a) }}</div>
                    <div v-if="a.observado_em" class="text-[10px] text-muted-foreground" :title="'Conferido na plataforma em ' + fmtDataHora(a.observado_em)">{{ fmtDesde(a.observado_em) }}</div>
                  </td>
                  <td class="px-1.5 py-1.5 text-center">
                    <span class="inline-block w-[98px] truncate rounded border py-0.5 text-[10.5px] font-semibold" :class="querInfo(a).cls" :title="querInfo(a).title">{{ querInfo(a).texto }}</span>
                  </td>
                  <td class="border-l border-l-slate-300 px-1.5 py-1.5 text-center text-[11px] dark:border-l-slate-600">
                    <button
                      v-if="acaoDe(a).tipo === 'aprovar'"
                      type="button"
                      class="inline-flex items-center gap-1 rounded-md border px-2 py-0.5 font-semibold disabled:opacity-50"
                      :class="tentarNovamente(a) ? BOTAO_TENTAR : BOTAO_APROVAR"
                      :disabled="aprovando.has(chave(a))"
                      :title="a.recusa
                        ? 'A plataforma recusou ligar (veja o porquê na linha). Arrumou? Tentar novamente'
                        : aprovadoSemLigar(a)
                          ? 'Aprovado em ' + fmtDataHora(a.aprovado_em) + ', mas ainda não ligou: tentar novamente'
                          : 'Aprovar: ligar o Flex neste anúncio'"
                      @click="aprovar(a)"
                    >
                      <Check class="size-3.5" :class="aprovando.has(chave(a)) ? 'animate-pulse' : ''" />
                      {{ rotuloAprovar(a) }}
                    </button>
                    <span
                      v-else-if="acaoDe(a).tipo === 'chip'"
                      class="inline-block w-[98px] truncate rounded border py-0.5 text-[10.5px] font-semibold"
                      :class="acaoDe(a).cls"
                      :title="acaoDe(a).title"
                    >{{ acaoDe(a).texto }}</span>
                    <span v-else class="inline-flex items-center gap-1.5" :class="acaoDe(a).cls">
                      <span v-if="acaoDe(a).dot" class="size-[7px] shrink-0 rounded-full" :class="acaoDe(a).dot" />
                      {{ acaoDe(a).texto }}
                    </span>
                  </td>
                  <td class="px-1.5 py-1.5 text-[11px]">
                    <div class="line-clamp-2 leading-tight" :title="porQue(a)">{{ porQue(a) }}</div>
                    <div v-if="recusaAparte(a)" class="truncate text-rose-700 dark:text-rose-400" :title="a.recusa || ''">A plataforma recusou ligar: {{ a.recusa }}</div>
                    <div v-if="shopeeConfirmando(a)" class="truncate text-muted-foreground" :title="CONFIRMANDO_TXT">{{ CONFIRMANDO_TXT }}</div>
                    <div v-else-if="erroAparte(a)" class="truncate text-rose-700 dark:text-rose-400" :title="a.ultimo_erro || ''">
                      Último erro: {{ a.ultimo_erro }}
                      <template v-if="a.proxima_tentativa"> · tenta de novo {{ fmtCurto(a.proxima_tentativa) }}</template>
                      <template v-else-if="a.proxima_leitura"> · confere de novo {{ fmtCurto(a.proxima_leitura) }}</template>
                    </div>
                  </td>
                </tr>
                <tr v-if="!loading && itens.length === 0">
                  <td colspan="9" class="px-3 py-6 text-center text-muted-foreground">
                    {{ vazioTexto }}
                    <button v-if="vazioVerTodos" type="button" class="ml-1 underline" @click="filtroPecas = ''">ver todos os anúncios</button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div class="mt-auto flex flex-wrap items-center gap-2 border-t px-3 py-1.5 text-[11px] text-muted-foreground">
            <span class="flex-1 text-center">
              Cinza = conta que ainda não está pronta para o Flex (o sistema não mexe nela). Ligar: Aprovar, ou marque vários e clique em Ligar selecionados<template v-if="!config?.shopee_escrita"> (na Shopee, à mão no Seller Center)</template>. Desligar: o sistema faz sozinho quando a peça acaba (piloto/ativo); para desligar antes, marque e clique em Desligar selecionados.
            </span>
            <template v-if="total > POR_PAGINA">
              <button type="button" class="rounded border p-0.5 disabled:opacity-40" :disabled="pagina <= 1" @click="pagina--"><ChevronLeft class="size-3.5" /></button>
              <span class="whitespace-nowrap">página {{ pagina }} de {{ totalPaginas }}</span>
              <button type="button" class="rounded border p-0.5 disabled:opacity-40" :disabled="pagina >= totalPaginas" @click="pagina++"><ChevronRight class="size-3.5" /></button>
            </template>
          </div>
        </div>

        <!-- Cartões (celular) -->
        <div class="space-y-2 md:hidden">
          <div
            v-for="a in itens"
            :key="chave(a)"
            class="space-y-1.5 rounded-md border p-3 text-xs"
            :class="a.aguardando_aprovacao ? 'border-amber-300 bg-amber-50/60 dark:bg-amber-950/20' : ''"
          >
            <div class="flex items-start gap-2">
              <div class="min-w-0 flex-1">
                <div class="font-mono">{{ a.external_id }}</div>
                <div v-if="a.titulo" class="truncate">{{ a.titulo }}</div>
                <div class="text-muted-foreground">
                  {{ nomePlataforma(a.plataforma) }} · {{ a.conta || '—' }}<template v-if="statusTexto(a)"> · {{ statusTexto(a) }}</template>
                </div>
              </div>
              <span class="shrink-0 rounded border px-1.5 py-0.5 text-[10px]" :class="querInfo(a).cls">{{ querInfo(a).texto }}</span>
            </div>
            <div class="grid grid-cols-2 gap-x-3 gap-y-1">
              <div><span class="text-muted-foreground">Família:</span> {{ familiasTexto(a) }}</div>
              <div><span class="text-muted-foreground">Peças em {{ localCidade }}:</span> <span :class="saldoCls(a)">{{ a.saldo_sp ?? '—' }}</span></div>
              <div class="col-span-2">
                <span class="text-muted-foreground">Na plataforma: </span>
                <span :class="divergente(a) ? 'font-medium text-amber-700 dark:text-amber-400' : ''">{{ observadoTexto(a) }}</span>
                <span v-if="a.observado_em" class="text-muted-foreground"> · conferido {{ fmtDesde(a.observado_em) }}</span>
              </div>
            </div>
            <div>{{ porQue(a) }}</div>
            <div v-if="recusaAparte(a)" class="text-rose-700 dark:text-rose-400">A plataforma recusou ligar: {{ a.recusa }}</div>
            <div v-if="shopeeConfirmando(a)" class="text-muted-foreground">{{ CONFIRMANDO_TXT }}</div>
            <div v-else-if="erroAparte(a)" class="text-rose-700 dark:text-rose-400">Último erro: {{ a.ultimo_erro }}</div>
            <div v-if="podeAprovar(a)" class="flex items-center gap-3">
              <button
                type="button"
                class="inline-flex items-center gap-1 rounded border px-2 py-1 font-medium disabled:opacity-50"
                :class="tentarNovamente(a) ? BOTAO_TENTAR : BOTAO_APROVAR"
                :disabled="aprovando.has(chave(a))"
                @click="aprovar(a)"
              >
                <Check class="size-3.5" /> {{ rotuloAprovar(a) }}
              </button>
              <label class="inline-flex items-center gap-1 text-muted-foreground">
                <input
                  type="checkbox"
                  class="size-3.5 accent-emerald-600"
                  :checked="marcados.has(chave(a))"
                  :disabled="emLote"
                  @change="marcar(a, ($event.target as HTMLInputElement).checked)"
                />
                marcar
              </label>
            </div>
            <div v-else class="flex items-center gap-3">
              <span
                v-if="acaoDe(a).tipo === 'chip'"
                class="inline-block rounded border px-1.5 py-0.5 text-[10px] font-semibold"
                :class="acaoDe(a).cls"
              >{{ acaoDe(a).texto }}</span>
              <span v-else-if="acaoDe(a).tipo === 'texto'" :class="acaoDe(a).cls">{{ acaoDe(a).texto }}</span>
              <label v-if="podeDesligar(a)" class="inline-flex items-center gap-1 text-muted-foreground">
                <input
                  type="checkbox"
                  class="size-3.5 accent-rose-600"
                  :checked="marcados.has(chave(a))"
                  :disabled="emLote"
                  @change="marcar(a, ($event.target as HTMLInputElement).checked)"
                />
                marcar para desligar
              </label>
            </div>
          </div>
          <div v-if="!loading && itens.length === 0" class="rounded-md border px-3 py-6 text-center text-sm text-muted-foreground">
            {{ vazioTexto }}
            <button v-if="vazioVerTodos" type="button" class="ml-1 underline" @click="filtroPecas = ''">ver todos os anúncios</button>
          </div>
          <div v-if="total > POR_PAGINA" class="flex items-center justify-end gap-1 text-xs">
            <button type="button" class="rounded border p-1 disabled:opacity-40" :disabled="pagina <= 1" @click="pagina--"><ChevronLeft class="size-4" /></button>
            <span class="px-2 text-muted-foreground">página {{ pagina }} de {{ totalPaginas }}</span>
            <button type="button" class="rounded border p-1 disabled:opacity-40" :disabled="pagina >= totalPaginas" @click="pagina++"><ChevronRight class="size-4" /></button>
          </div>
        </div>

        <!-- Pedidos Flex sem peça no local de saída (aviso do robô de prioridade, etapa 2) -->
        <div id="flex-pedidos-sem-sp" class="flex min-w-0 flex-col rounded-md border bg-card">
          <div class="flex items-center justify-center gap-1.5 rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-xs font-semibold text-white">
            <TriangleAlert class="size-3.5" :class="pedidosSemSp.length ? 'text-rose-300' : 'text-slate-300'" />
            Pedidos Flex sem peça em {{ localCidade }} · {{ pedidosSemSp.length }}
          </div>
          <p class="border-b px-3 py-1.5 text-[11px] text-muted-foreground">
            Pedidos Flex saem de {{ localCidade }}. Nestes, o estoque .{{ localLote }} não tinha a peça e o sistema NÃO trocou o lote.
            Decida: separar em {{ localCidade }}, transferir a peça para o .{{ localLote }} ou cancelar o pedido.
            Os que já <strong>saíram</strong> sem passar pelo .{{ localLote }} ficam aqui até alguém acertar o estoque no Bling
            (o Bling baixou outro lote): até lá o sistema desconta o pedido das peças livres em {{ localCidade }}.
          </p>
          <div v-if="pedidosSemSp.length" class="overflow-x-auto">
            <table class="w-full min-w-[860px] border-collapse text-[11.5px]">
              <thead class="bg-muted text-[10px] uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th class="border-b px-2 py-1 text-left font-semibold">Pedido Bling</th>
                  <th class="border-b px-2 py-1 text-left font-semibold">Pedido na plataforma</th>
                  <th class="border-b px-2 py-1 text-left font-semibold">Conta</th>
                  <th class="border-b px-2 py-1 text-left font-semibold">SKU</th>
                  <th class="border-b px-2 py-1 text-left font-semibold" title="Prazo para despachar dado pela plataforma">Despachar até</th>
                  <th class="border-b px-2 py-1 text-left font-semibold">O que falta</th>
                  <th v-if="canEdit" class="border-b px-2 py-1 text-left font-semibold">Ação</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="p in pedidosSemSp" :key="p.bling_id" class="border-b align-top bg-rose-50/50 dark:bg-rose-950/20">
                  <td class="whitespace-nowrap px-2 py-1.5">
                    <button
                      v-if="p.numero"
                      type="button"
                      class="font-mono font-bold underline-offset-2 hover:underline"
                      title="Mostrar este pedido na lista Pedidos Flex"
                      @click="emit('buscar-pedido', p.numero)"
                    >{{ p.numero }}</button>
                    <span v-else class="text-muted-foreground">{{ p.bling_id }}</span>
                    <div class="text-[10px] text-muted-foreground" :title="fmtDataHora(p.detectado_em)">visto {{ fmtDesde(p.detectado_em) }}</div>
                  </td>
                  <td class="whitespace-nowrap px-2 py-1.5 font-mono">{{ p.numeroloja || '—' }}</td>
                  <td class="whitespace-nowrap px-2 py-1.5">{{ p.conta || '—' }}<div class="text-[10px] text-muted-foreground">{{ nomePlataforma(p.plataforma) }}</div></td>
                  <td class="px-2 py-1.5 font-mono">
                    <div v-for="s in p.skus" :key="s">{{ s }}</div>
                    <span v-if="!p.skus.length" class="text-muted-foreground">—</span>
                  </td>
                  <td class="whitespace-nowrap px-2 py-1.5" :class="prazoVencido(p) ? 'font-medium text-rose-700 dark:text-rose-400' : ''">
                    {{ p.prazo ? fmtCurto(p.prazo) : '—' }}
                    <div v-if="prazoVencido(p)" class="text-[10px]">prazo vencido</div>
                  </td>
                  <td class="max-w-[380px] px-2 py-1.5">
                    <div v-if="p.acerto_pendente" class="font-medium text-rose-700 dark:text-rose-400">
                      Já saiu sem passar pelo .{{ p.lote || localLote }} — acerte o estoque no Bling (transferência para o .{{ p.lote || localLote }}).
                    </div>
                    <div v-if="p.alerta">{{ p.alerta }}</div>
                    <span v-if="!p.alerta && !p.acerto_pendente">—</span>
                  </td>
                  <td v-if="canEdit" class="whitespace-nowrap px-2 py-1.5">
                    <button
                      v-if="p.acerto_pendente"
                      type="button"
                      class="inline-flex items-center gap-1 rounded border border-emerald-400 px-2 py-0.5 font-medium text-emerald-800 hover:bg-emerald-50 disabled:opacity-50 dark:border-emerald-700 dark:text-emerald-300 dark:hover:bg-emerald-950/40"
                      :disabled="acertando.has(p.bling_id)"
                      :title="`Já transferi a peça para o .${p.lote || localLote} no Bling: parar de descontar este pedido`"
                      @click="marcarAcertado(p)"
                    >
                      <Check class="size-3.5" /> Estoque acertado
                    </button>
                    <span v-else class="text-muted-foreground">—</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-else class="px-3 py-3 text-center text-[11px] text-muted-foreground">Nenhum pedido Flex sem peça em {{ localCidade }}.</div>
        </div>
      </div>

      <!-- À direita: Onde agir hoje, Contas e Como o sistema decide -->
      <div class="flex min-w-0 flex-col gap-3">
        <div class="flex min-w-0 flex-col rounded-md border bg-card">
          <div class="rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-center text-xs font-semibold text-white">Onde agir hoje</div>
          <div class="space-y-1.5 p-2">
            <div
              v-for="a in agirItens"
              :key="a.chave"
              class="grid items-center gap-2 rounded-md border px-2.5 py-1.5 text-xs"
              :class="[a.acao ? 'grid-cols-[12px_minmax(0,1fr)_58px]' : 'grid-cols-[12px_minmax(0,1fr)]', a.grave ? 'border-red-300 bg-red-50 dark:border-red-800 dark:bg-red-950/40' : '']"
            >
              <span class="size-[7px] rounded-full" :class="a.dot" />
              <div class="min-w-0">
                <div class="truncate font-bold" :class="a.grave ? 'text-red-700 dark:text-red-300' : ''" :title="a.titulo">{{ a.titulo }}</div>
                <div class="truncate text-[11px] text-muted-foreground" :title="a.detalhe">{{ a.detalhe }}</div>
              </div>
              <button v-if="a.acao" type="button" class="h-6 rounded-md border text-[11px] font-semibold hover:bg-muted" @click="a.fazer && a.fazer()">{{ a.acao }}</button>
            </div>
            <div v-if="!agirItens.length" class="rounded-md border px-2.5 py-3 text-center text-xs text-muted-foreground">nada pra agir agora</div>
          </div>
        </div>

        <div class="flex min-w-0 flex-col rounded-md border bg-card" data-flex-grupos>
          <div class="rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-center text-xs font-semibold text-white">
            Contas — {{ contasProntasTotal.prontas }} de {{ contasProntasTotal.total }} prontas
          </div>
          <!-- Local de saída do Flex (cidade + lote) — editável por admin -->
          <div v-if="config" class="border-b px-3 py-1.5 text-xs" data-flex-local>
            <span class="text-muted-foreground">Saída do Flex:</span>{{ ' ' }}<b>{{ localCidade }}</b> · estoque <b>.{{ localLote }}</b>
            <button
              v-if="config.pode_alterar_local && canEdit && !editandoLocal"
              type="button"
              class="ml-1 text-[11px] font-medium text-primary underline underline-offset-2"
              data-flex-local-alterar
              @click="abrirLocal"
            >alterar</button>
            <div v-if="config.local?.atualizado_por" class="text-[10.5px] text-muted-foreground">
              trocado por {{ config.local.atualizado_por }} {{ fmtDesde(config.local.atualizado_em) }}
            </div>
            <div v-if="editandoLocal" class="mt-1.5 space-y-1.5 rounded-md border bg-muted/30 p-2" data-flex-local-form>
              <label class="block text-[11px]">
                Cidade de onde o motoboy sai
                <input
                  id="flex-local-cidade"
                  v-model="localForm.cidade"
                  list="flex-local-cidades"
                  maxlength="100"
                  class="mt-0.5 block w-full rounded border bg-background px-2 py-1 text-xs"
                />
                <datalist id="flex-local-cidades">
                  <option v-for="cid in sugestoesCidades" :key="cid" :value="cid" />
                </datalist>
              </label>
              <label class="block text-[11px]">
                Estoque (lote) de lá
                <select id="flex-local-lote" v-model="localForm.lote" class="mt-0.5 block rounded border bg-background px-2 py-1 text-xs">
                  <option v-for="l in config.lotes" :key="l" :value="l" :selected="l === localForm.lote">.{{ l }}</option>
                </select>
              </label>
              <div class="flex gap-1.5">
                <button type="button" class="rounded-md bg-primary px-2.5 py-1 text-[11px] font-semibold text-primary-foreground disabled:opacity-50" :disabled="salvandoLocal" @click="salvarLocal">Salvar</button>
                <button type="button" class="rounded-md border px-2.5 py-1 text-[11px] font-semibold disabled:opacity-50" :disabled="salvandoLocal" @click="editandoLocal = false">Cancelar</button>
              </div>
              <div class="text-[10.5px] text-muted-foreground">
                A cidade é a que o Mercado Livre mostra na saída do Flex de cada conta. Ao salvar, o sistema conta as peças do
                estoque escolhido e só mexe nas contas cuja saída do Flex for nessa cidade. Vale na próxima conferência (até
                {{ config.intervalo_min }} min) ou ao clicar em Sincronizar Flex.
              </div>
            </div>
          </div>
          <div v-if="config && !config.contas.length" class="px-3 py-2 text-[11px] text-muted-foreground">Nenhuma conta liberada — o sistema não mexe em nenhuma conta.</div>
          <div
            v-for="g in gruposContas"
            :key="g.chave"
            class="border-b transition-colors last:border-b-0"
            :class="grupoDestaque === g.chave ? 'bg-amber-50 ring-2 ring-inset ring-amber-400 dark:bg-amber-950/30' : ''"
            :data-flex-grupo="g.chave"
          >
            <button
              type="button"
              class="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs hover:bg-muted/40 disabled:cursor-default disabled:hover:bg-transparent"
              :disabled="!g.contas.length"
              :title="g.acao"
              @click="alternarGrupo(g.chave)"
            >
              <span class="size-[7px] shrink-0 rounded-full" :class="g.cor" />
              <span class="min-w-0 flex-1 truncate font-semibold">{{ g.titulo }}</span>
              <span class="shrink-0 font-bold">{{ g.contas.length }}</span>
              <ChevronDown v-if="g.contas.length" class="size-3.5 shrink-0 opacity-60 transition-transform" :class="gruposAbertos.has(g.chave) ? 'rotate-180' : ''" />
            </button>
            <div v-if="gruposAbertos.has(g.chave)" class="space-y-1 px-3 pb-2 text-[11px]">
              <div class="text-muted-foreground">{{ g.acao }}</div>
              <div v-for="c in g.contas" :key="c.id" class="flex gap-2" :title="c.title">
                <span class="min-w-0 flex-1 truncate">{{ c.nome }}</span>
                <span class="shrink-0 text-muted-foreground">{{ c.detalhe }}</span>
              </div>
            </div>
          </div>
        </div>

        <div class="flex min-w-0 flex-1 flex-col rounded-md border bg-card" data-flex-regra>
          <div class="rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-center text-xs font-semibold text-white">Como o sistema decide</div>
          <div v-if="config" class="space-y-2 px-3 py-2 text-[11.5px]">
            <!-- As 3 chaves que mandam o robô escrever (11/11/2026: saíram do .env para cá) -->
            <div class="space-y-0.5 rounded-md border px-2 py-1.5" data-flex-ajustes>
              <div class="flex items-center gap-2">
                <span class="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Chaves do Flex</span>
                <button
                  v-if="config.pode_ajustar && canEdit && !editandoAjustes"
                  type="button"
                  class="ml-auto text-[11px] font-medium text-primary underline underline-offset-2"
                  data-flex-ajustes-alterar
                  @click="abrirAjustes"
                >alterar</button>
              </div>
              <template v-if="!editandoAjustes">
                <div class="flex items-center gap-1.5">
                  <span class="text-muted-foreground">Modo:</span>
                  <span class="rounded px-1.5 text-[10.5px] font-semibold" :class="modoChip.cls">{{ modoChip.texto }}</span>
                </div>
                <div>
                  <span class="text-muted-foreground">DaVinci liga/desliga na Shopee:&nbsp;</span>
                  <b :class="config.shopee_escrita ? 'text-emerald-700 dark:text-emerald-400' : ''">{{ config.shopee_escrita ? 'SIM' : 'NÃO' }}</b>
                </div>
                <div>
                  <span class="text-muted-foreground">Pedido Flex vai para o .{{ localLote }}:&nbsp;</span>
                  <b :class="config.pedido_no_sp ? 'text-emerald-700 dark:text-emerald-400' : ''">{{ config.pedido_no_sp ? 'SIM' : 'NÃO' }}</b>
                </div>
                <div :title="`Kit (celular + fone, relógio…) entra no Flex pelo kit .${localLote} do Bling — o estoque dele é o da peça mais escassa; sem o kit .${localLote} cadastrado, não liga.`">
                  <span class="text-muted-foreground">Kits entram:&nbsp;</span>
                  <b :class="config.kits ? 'text-emerald-700 dark:text-emerald-400' : ''">{{ config.kits ? 'SIM' : 'NÃO' }}</b>
                </div>
                <div data-flex-ajustes-contas :title="[...contasNoFlexResumo.ml.map((n) => `${n} (ML)`), ...contasNoFlexResumo.shopee.map((n) => `${n} (Shopee)`)].join('\n')">
                  <span class="text-muted-foreground">Contas no Flex:&nbsp;</span>
                  <b>{{ contasNoFlexResumo.ml.length }}</b> do ML<template v-if="contasNoFlexResumo.ml.length"> ({{ contasNoFlexResumo.ml.join(', ') }})</template>
                  · <b>{{ contasNoFlexResumo.shopee.length }}</b> da Shopee
                </div>
                <div class="text-[10.5px] text-muted-foreground">
                  <template v-if="config.ajustes?.atualizado_por">
                    trocado por {{ config.ajustes.atualizado_por }} {{ fmtDesde(config.ajustes.atualizado_em) }}
                  </template>
                  <template v-else>como está na configuração do servidor (ninguém trocou na tela ainda)</template>
                </div>
              </template>
              <div v-else class="space-y-1.5" data-flex-ajustes-form>
                <label class="block text-[11px]">
                  Modo
                  <select v-model="ajusteForm.modo" class="mt-0.5 block w-full rounded border bg-background px-1.5 py-1 text-xs">
                    <option v-for="o in MODOS_OPCOES" :key="o.v" :value="o.v">{{ o.t }}</option>
                  </select>
                </label>
                <label class="flex items-center gap-1.5 text-[11px]">
                  <input v-model="ajusteForm.shopee_escrita" type="checkbox" />
                  DaVinci liga/desliga a Entrega Direta nos anúncios da Shopee
                </label>
                <label class="flex items-center gap-1.5 text-[11px]">
                  <input v-model="ajusteForm.pedido_no_sp" type="checkbox" />
                  Pedido Flex vai para o estoque .{{ localLote }} ({{ localCidade }})
                </label>
                <label class="flex items-center gap-1.5 text-[11px]" data-flex-ajustes-kits>
                  <input v-model="ajusteForm.kits" type="checkbox" />
                  Kits entram (só com tudo no .{{ localLote }}: vale o kit .{{ localLote }} do Bling)
                </label>
                <div v-if="contasOpcoesGrupos.length" class="space-y-1 rounded border px-1.5 py-1" data-flex-ajustes-contas-form>
                  <div class="text-[11px] font-medium">Contas em que o sistema mexe</div>
                  <div v-for="g in contasOpcoesGrupos" :key="g.chave" class="space-y-0.5">
                    <div class="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">{{ g.titulo }}</div>
                    <div class="grid grid-cols-2 gap-x-2 gap-y-0.5">
                      <label v-for="o in g.contas" :key="o.id" class="flex min-w-0 items-center gap-1.5 text-[11px]" :data-flex-conta-opcao="o.nome.trim()">
                        <input v-model="ajusteForm.contas" type="checkbox" :value="o.id" />
                        <span class="truncate">{{ o.nome.trim() }}</span>
                      </label>
                    </div>
                  </div>
                  <div class="text-[10.5px] text-muted-foreground">
                    Desmarcar não desliga o Flex que já está ligado na conta — o sistema só para de mexer nela.
                  </div>
                </div>
                <div class="flex gap-1.5">
                  <button type="button" class="rounded-md bg-primary px-2.5 py-1 text-[11px] font-semibold text-primary-foreground disabled:opacity-50" :disabled="salvandoAjustes" @click="salvarAjustes">Salvar</button>
                  <button type="button" class="rounded-md border px-2.5 py-1 text-[11px] font-semibold disabled:opacity-50" :disabled="salvandoAjustes" @click="editandoAjustes = false">Cancelar</button>
                </div>
                <div class="text-[10.5px] text-muted-foreground">
                  Pede confirmação antes. Vale na próxima conferência (até {{ config.intervalo_min }} min) ou ao clicar em Sincronizar Flex.
                </div>
              </div>
            </div>
            <div class="space-y-1" data-flex-regras>
              <div class="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Regras</div>
              <ol class="list-decimal space-y-1 pl-4">
                <li v-for="(r, i) in regras" :key="i">{{ r.antes || '' }}<b>{{ r.forte }}</b>{{ r.resto }}</li>
              </ol>
            </div>
            <div class="text-[10.5px] text-muted-foreground">
              <template v-if="config.ajustes?.contas_origem === 'tela'">A lista de contas é a escolhida nas Chaves do Flex.</template>
              <template v-else>A lista de contas liberadas ainda é a da configuração do servidor (troque em Chaves do Flex › alterar).</template>
              <template v-if="contasFantasma"> {{ contasFantasma }} conta(s) da lista não existem mais no DaVinci.</template>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
