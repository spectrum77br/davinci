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
//   • pede uma conferência agora ("Sincronizar Flex") e tem o botão de
//     emergência que desliga tudo das contas liberadas;
//   • vê os pedidos Flex que o robô não conseguiu passar para o .sp.
// Nada aqui chama o ML/Shopee direto: tudo passa pelo motor no servidor, que
// respeita o modo (em "observar" nada é escrito nas plataformas).
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RefreshCw, Search, X, PowerOff, Check, ExternalLink, TriangleAlert, ChevronLeft, ChevronRight } from 'lucide-vue-next'

const props = defineProps<{ canEdit: boolean }>()
const emit = defineEmits<{
  // Pede à página para mostrar o pedido na lista "Pedidos Flex".
  (e: 'buscar-pedido', numero: string): void
  // Algo mudou (aprovação, emergência): a página recarrega os contadores.
  (e: 'mudou'): void
}>()

const { api } = useApi()
const toasts = useToasts()

type FlexConta = { id: string; nome: string | null; plataforma: string | null; existe: boolean }
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
}
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
  atualizado_em: string
}
type FlexResumo = { avaliados: number; ligados: number; aguardando: number; desligar: number; nao_lidos: number }
type FlexPedido = {
  bling_id: number
  numero: string | null
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
// Cada opção vira um filtro do backend (desejado / observado / aguardando).
const SITUACOES = [
  { key: '', label: 'Todos' },
  { key: 'aguardando', label: 'Esperando sua aprovação' },
  { key: 'ligado_agora', label: 'Com Flex ligado agora' },
  { key: 'desligar', label: 'Ligado, mas deveria desligar' },
  { key: 'quer_ligado', label: 'Pode ter Flex' },
  { key: 'quer_desligado', label: 'Flex deve ficar desligado' },
  { key: 'inelegivel', label: 'Não pode ter Flex' },
  { key: 'nao_lido', label: 'Ainda não conferido na plataforma' },
] as const
type Situacao = (typeof SITUACOES)[number]['key']
const filtroSituacao = ref<Situacao>('')
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
  () => !!filtroPlataforma.value || !!filtroConta.value || !!filtroSituacao.value || !!busca.value.trim(),
)
function limparFiltros() {
  filtroPlataforma.value = ''
  filtroConta.value = ''
  filtroSituacao.value = ''
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
  const s = filtroSituacao.value
  if (s === 'aguardando') qs.set('aguardando', 'true')
  else if (s === 'desligar') qs.set('desligar', 'true')
  else if (s === 'ligado_agora') qs.set('observado', 'ligado')
  else if (s === 'quer_ligado') qs.set('desejado', 'ligado')
  else if (s === 'quer_desligado') qs.set('desejado', 'desligado')
  else if (s === 'inelegivel') qs.set('desejado', 'inelegivel')
  else if (s === 'nao_lido') qs.set('observado', 'nao_lido')
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

watch([filtroPlataforma, filtroConta, filtroSituacao, buscaAplicada], () => {
  if (pagina.value !== 1) pagina.value = 1
  else carregarAnuncios()
})
watch(pagina, () => carregarAnuncios())

// ---- Atualização automática (60s) ----
// O motor roda no servidor de N em N minutos; a tela relê o banco sozinha
// para quem deixou a aba aberta. Não roda com ação em andamento.
const aprovando = ref<Set<string>>(new Set())
const sincronizando = ref(false)
const emergenciaRodando = ref(false)
let timer: ReturnType<typeof setInterval> | null = null
const recargas: ReturnType<typeof setTimeout>[] = []
function tick() {
  if (typeof document !== 'undefined' && document.visibilityState !== 'visible') return
  if (loading.value || aprovando.value.size || sincronizando.value || emergenciaRodando.value) return
  carregarAnuncios()
  carregarPedidos()
}
onMounted(() => {
  carregarTudo()
  timer = setInterval(tick, 60000)
})
onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
  if (buscaTimer) clearTimeout(buscaTimer)
  recargas.forEach((t) => clearTimeout(t))
})

// ---- Textos ----
const MODOS: Record<string, { titulo: string; texto: string; cls: string }> = {
  desligado: {
    titulo: 'Flex automático: DESLIGADO',
    texto: 'O sistema não confere nem mexe no Flex de nenhum anúncio. A lista abaixo mostra a última conferência feita, se houver.',
    cls: 'border-zinc-300 bg-zinc-50 text-zinc-800 dark:border-zinc-700 dark:bg-zinc-900/40 dark:text-zinc-200',
  },
  observar: {
    titulo: 'Flex automático: SÓ OBSERVANDO',
    texto: 'O sistema confere os anúncios e mostra o que faria, mas não liga nem desliga nada no Mercado Livre ou na Shopee.',
    cls: 'border-sky-300 bg-sky-50 text-sky-900 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-200',
  },
  piloto: {
    titulo: 'Flex automático: PILOTO',
    texto: 'O sistema DESLIGA sozinho o Flex dos anúncios sem peça suficiente em São Bernardo, só nas contas liberadas abaixo. Para LIGAR, ele espera você aprovar.',
    cls: 'border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-200',
  },
  ativo: {
    titulo: 'Flex automático: ATIVO',
    texto: 'O sistema DESLIGA sozinho o Flex dos anúncios sem peça suficiente em São Bernardo, nas contas liberadas abaixo. Para LIGAR, ele espera você aprovar.',
    cls: 'border-emerald-300 bg-emerald-50 text-emerald-900 dark:border-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-200',
  },
}
// Enquanto a configuração não chega, nada de "DESLIGADO": seria mentira.
const CARREGANDO = {
  titulo: 'Flex automático',
  texto: 'Lendo a configuração…',
  cls: 'border-zinc-300 bg-zinc-50 text-zinc-700 dark:border-zinc-700 dark:bg-zinc-900/40 dark:text-zinc-300',
}
const modoInfo = computed(() =>
  config.value ? MODOS[config.value.modo] || MODOS.desligado! : CARREGANDO,
)
const modoDesligado = computed(() => !config.value || config.value.modo === 'desligado')

function nomePlataforma(p: string | null | undefined): string {
  if (p === 'ml') return 'Mercado Livre'
  if (p === 'shopee') return 'Shopee'
  return p || '—'
}
const contasPermitidas = computed(() => new Set((config.value?.contas || []).filter((c) => c.existe).map((c) => c.id)))
const contasTexto = computed(() =>
  (config.value?.contas || []).map((c) =>
    c.existe ? `${c.nome || c.id} (${nomePlataforma(c.plataforma)})` : `${c.id.slice(0, 8)}… (conta não encontrada)`,
  ),
)
const regraTexto = computed(() => {
  const c = config.value
  if (!c) return ''
  const pecas = (n: number) => (n === 1 ? '1 peça livre' : `${n} peças livres`)
  return (
    `Liga com ${pecas(c.n_liga)} ou mais em São Bernardo e desliga com menos de ${c.n_desliga}. ` +
    `No máximo ${c.max_anuncios_por_familia} anúncio(s) com Flex por família. ` +
    `Kits ${c.kits ? 'entram' : 'não entram'}. ` +
    `Confere sozinho a cada ${c.intervalo_min} min` +
    (c.pode_escrever ? `, mudando até ${c.teto_escritas_por_rodada} anúncios por vez.` : '.')
  )
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
  if (a.plataforma !== 'ml') return null
  const m = /^MLB(\d+)$/i.exec(a.external_id.trim())
  return m ? `https://produto.mercadolivre.com.br/MLB-${m[1]}` : null
}
function podeAprovar(a: FlexAnuncio): boolean {
  return (
    props.canEdit &&
    !modoDesligado.value &&
    contasPermitidas.value.has(a.integration_id) &&
    (a.aguardando_aprovacao || !!a.recusa)
  )
}
// A recusa da plataforma em linha própria — menos quando o motivo já é ela
// (a regra marca "não pode ter Flex" pela recusa): não repete a frase.
function recusaAparte(a: FlexAnuncio): boolean {
  return !!a.recusa && !(a.motivo || '').startsWith('a plataforma recusou ligar')
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
    : `Ligar o Flex no anúncio ${a.external_id} (${conta})?\n\nPeças livres em São Bernardo: ${a.saldo_sp ?? '—'}.`
  const aviso = c?.pode_escrever
    ? '\n\nO sistema liga agora na plataforma.'
    : '\n\nModo "só observando": a aprovação fica registrada, mas nada muda na plataforma.'
  if (!confirm(pergunta + aviso)) return
  const k = chave(a)
  aprovando.value = new Set([...aprovando.value, k])
  try {
    const r = await api<{ aprovado: boolean; modo: string; aplicado: boolean; estado: FlexAnuncio | null }>(
      `/api/flex/anuncios/${a.integration_id}/${encodeURIComponent(a.external_id)}/aprovar`,
      { method: 'POST' },
    )
    if (r.estado) {
      const i = itens.value.findIndex((x) => chave(x) === k)
      if (i >= 0) itens.value.splice(i, 1, r.estado)
    }
    if (r.aplicado) toasts.success('Flex ligado', `Anúncio ${a.external_id}.`)
    else if (r.modo === 'piloto' || r.modo === 'ativo')
      toasts.warning(
        'Aprovado, mas ainda não ligou',
        r.estado?.ultimo_erro || r.estado?.motivo_claro || 'Veja o motivo na linha do anúncio; o sistema tenta de novo na próxima rodada.',
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

async function emergencia() {
  if (!props.canEdit || emergenciaRodando.value) return
  const c = config.value
  const simulacao = !c?.pode_escrever
  // Simulação de verdade: nada muda, nem as aprovações (o servidor só conta).
  const ok = confirm(
    simulacao
      ? 'SIMULAR a emergência?\n\nO modo atual não mexe nas plataformas: o sistema só mostra o que faria — ' +
          'quantos anúncios desligaria e quantas aprovações retiraria. Nada muda, nem as aprovações.'
      : 'DESLIGAR O FLEX DE TODOS os anúncios das contas liberadas?\n\n' +
          'Também retira todas as aprovações AGORA: nada volta a ligar sem uma pessoa aprovar de novo.',
  )
  if (!ok) return
  emergenciaRodando.value = true
  try {
    const r = await api<Record<string, any>>('/api/flex/emergencia', { method: 'POST' })
    if (r.motivo) {
      toasts.warning('Nada a desligar', motivoServidor(r.motivo))
    } else if (!r.escreve) {
      const naoLidos = Math.max(0, (r.alvos || 0) - (r.ligados_conhecidos || 0))
      toasts.info('Simulação da emergência (nada mudou)', [
        `${r.ligados_conhecidos || 0} anúncio(s) com Flex ligado seriam desligados.`,
        naoLidos ? `${naoLidos} anúncio(s) ainda não conferidos também seriam desligados por segurança.` : '',
        r.aprovacoes
          ? `${r.aprovacoes} aprovação(ões) seriam retiradas — continuam valendo, nada foi retirado.`
          : '',
      ].filter(Boolean))
    } else {
      const linhas = [
        `${r.desligados || 0} anúncio(s) desligados.`,
        r.ja_desligados ? `${r.ja_desligados} já estavam desligados.` : '',
        r.aprovacoes ? `${r.aprovacoes} aprovação(ões) retiradas.` : '',
        r.falhas ? `${r.falhas} falharam — veja o erro na linha do anúncio.` : '',
        r.ocupados
          ? `${r.ocupados} estavam sendo mexidos pelo sistema naquele instante e NÃO foram desligados.`
          : '',
        r.sem_cliente
          ? `${r.sem_cliente} são de conta sem acesso à plataforma: desligue à mão no painel da plataforma.`
          : '',
        r.shopee_so_leitura
          ? `${r.shopee_so_leitura} da Shopee NÃO foram mexidos (o sistema só lê a Shopee): desligue à mão no Seller Center.`
          : '',
        r.restantes ? `Ainda faltam ${r.restantes}: clique de novo para continuar.` : '',
      ].filter(Boolean)
      if (r.falhas || r.restantes || r.ocupados || r.sem_cliente || r.shopee_so_leitura)
        toasts.warning('Emergência: desligamento parcial', linhas)
      else toasts.success('Emergência: Flex desligado', linhas)
    }
    emit('mudou')
    await carregarTudo()
  } catch (e: any) {
    toasts.error('A emergência falhou', e?.data?.detail?.code || e?.message || 'erro')
  } finally {
    emergenciaRodando.value = false
  }
}

// Pedido Flex que saiu sem passar pelo .sp: a pessoa diz que acertou o
// estoque no Bling (transferência para o .sp) — o saldo Flex para de descontar.
const acertando = ref<Set<number>>(new Set())
async function marcarAcertado(p: FlexPedido) {
  if (!props.canEdit || acertando.value.has(p.bling_id)) return
  const ok = confirm(
    `Pedido ${p.numero || p.bling_id}: você já acertou o estoque no Bling?\n\n` +
      'Ele saiu de São Bernardo, mas o Bling baixou outro lote. Confirme só depois de transferir ' +
      'a peça para o .sp no Bling — até lá o sistema desconta o pedido das peças livres em SP.',
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

// Clique num número do resumo = filtra a lista por aquilo.
function filtrarPor(s: Situacao) {
  filtroSituacao.value = filtroSituacao.value === s ? '' : s
}

defineExpose({ recarregar: carregarTudo })
</script>

<template>
  <div class="space-y-4">
    <!-- Aviso fixo do MODO: é a primeira coisa que o dono precisa saber. -->
    <div class="rounded-md border px-3 py-2 text-sm space-y-1" :class="modoInfo.cls" role="status">
      <div class="font-semibold">{{ modoInfo.titulo }}</div>
      <div>{{ modoInfo.texto }}</div>
      <div v-if="config">
        <span class="font-medium">Contas liberadas:</span>
        {{ contasTexto.length ? contasTexto.join(' · ') : 'nenhuma — o sistema não mexe em nenhuma conta.' }}
      </div>
      <div v-if="config" class="text-xs opacity-90">
        {{ regraTexto }}
        <template v-if="!config.shopee_escrita">
          Na Shopee o sistema só confere: ligar ou desligar lá é feito à mão no Seller Center.
        </template>
        Pedido Flex vai para o estoque .sp:
        <strong>{{ config.pedido_no_sp ? 'sim' : 'NÃO (desligado na configuração)' }}</strong>.
      </div>
      <div class="text-xs opacity-75">
        O modo e as contas liberadas ficam na configuração do servidor — para mudar, peça ao responsável técnico.
      </div>
    </div>

    <!-- Ações -->
    <div class="flex flex-wrap items-center gap-2">
      <Button size="sm" variant="ghost" :disabled="loading" @click="carregarTudo">
        <RefreshCw class="size-4 mr-1" :class="loading ? 'animate-spin' : ''" /> atualizar lista
      </Button>
      <Button
        v-if="canEdit"
        size="sm"
        variant="outline"
        :disabled="sincronizando || modoDesligado"
        :title="modoDesligado
          ? 'O Flex automático está desligado — não há o que conferir.'
          : `Pede ao sistema para conferir todos os anúncios agora (ele já faz isso sozinho a cada ${config?.intervalo_min ?? 15} min).`"
        @click="sincronizar"
      >
        <RefreshCw class="size-4 mr-1" :class="sincronizando ? 'animate-spin' : ''" /> Sincronizar Flex
      </Button>
      <Button
        v-if="canEdit"
        size="sm"
        variant="destructive"
        class="ml-auto"
        :disabled="emergenciaRodando || modoDesligado"
        :title="modoDesligado
          ? 'O Flex automático está desligado — não há o que desligar.'
          : 'Desliga o Flex de TODOS os anúncios das contas liberadas e retira todas as aprovações. Pede confirmação.'"
        @click="emergencia"
      >
        <PowerOff class="size-4 mr-1" :class="emergenciaRodando ? 'animate-pulse' : ''" /> Desligar tudo (emergência)
      </Button>
    </div>

    <!-- Resumo: cada número filtra a lista. -->
    <div class="flex flex-wrap gap-2 text-xs">
      <button
        type="button"
        class="rounded-md border px-2.5 py-1 hover:bg-muted/40"
        :class="filtroSituacao === '' ? 'border-primary' : ''"
        @click="filtroSituacao = ''"
      >
        <strong>{{ resumo.avaliados }}</strong> anúncios avaliados pela regra
        <span class="text-muted-foreground">({{ conferidos }} já conferidos na plataforma)</span>
      </button>
      <button
        type="button"
        class="rounded-md border px-2.5 py-1 hover:bg-muted/40"
        :class="filtroSituacao === 'ligado_agora' ? 'border-primary' : ''"
        @click="filtrarPor('ligado_agora')"
      >
        <strong>{{ resumo.ligados }}</strong> com Flex ligado
      </button>
      <button
        type="button"
        class="rounded-md border px-2.5 py-1 hover:bg-muted/40"
        :class="[
          filtroSituacao === 'aguardando' ? 'border-primary' : '',
          resumo.aguardando ? 'border-amber-400 bg-amber-50 text-amber-900 dark:bg-amber-950/40 dark:text-amber-200' : '',
        ]"
        @click="filtrarPor('aguardando')"
      >
        <strong>{{ resumo.aguardando }}</strong> esperando sua aprovação
      </button>
      <button
        type="button"
        class="rounded-md border px-2.5 py-1 hover:bg-muted/40"
        :class="[
          filtroSituacao === 'desligar' ? 'border-primary' : '',
          resumo.desligar ? 'border-rose-300 text-rose-800 dark:text-rose-300' : '',
        ]"
        title="Ligados na plataforma, mas sem peça suficiente em São Bernardo (ou sem poder ter Flex). O sistema desliga sozinho no modo piloto/ativo."
        @click="filtrarPor('desligar')"
      >
        <strong>{{ resumo.desligar }}</strong> ligados que deveriam desligar
      </button>
      <button
        type="button"
        class="rounded-md border px-2.5 py-1 hover:bg-muted/40"
        :class="filtroSituacao === 'nao_lido' ? 'border-primary' : ''"
        @click="filtrarPor('nao_lido')"
      >
        <strong>{{ resumo.nao_lidos }}</strong> ainda não conferidos na plataforma
      </button>
    </div>

    <!-- Filtros (rotulados) -->
    <div class="flex flex-wrap items-center gap-2">
      <div class="relative">
        <Search class="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
        <input
          v-model="busca"
          class="h-9 w-64 rounded-md border bg-background pl-8 pr-3 text-sm"
          placeholder="buscar anúncio, título ou família…"
        />
      </div>
      <label class="flex items-center gap-1.5 text-sm text-muted-foreground">
        Plataforma:
        <select v-model="filtroPlataforma" class="h-9 rounded-md border bg-background px-2 text-sm text-foreground">
          <option value="">Mercado Livre e Shopee</option>
          <option value="ml">Mercado Livre</option>
          <option value="shopee">Shopee</option>
        </select>
      </label>
      <label class="flex items-center gap-1.5 text-sm text-muted-foreground">
        Conta:
        <select v-model="filtroConta" class="h-9 rounded-md border bg-background px-2 text-sm text-foreground">
          <option value="">todas</option>
          <option v-for="c in config?.contas || []" :key="c.id" :value="c.id">
            {{ c.nome || c.id.slice(0, 8) }}
          </option>
        </select>
      </label>
      <label class="flex items-center gap-1.5 text-sm text-muted-foreground">
        Situação:
        <select v-model="filtroSituacao" class="h-9 rounded-md border bg-background px-2 text-sm text-foreground">
          <option v-for="s in SITUACOES" :key="s.key" :value="s.key">{{ s.label }}</option>
        </select>
      </label>
      <Button v-if="filtrosAtivos" size="sm" variant="ghost" @click="limparFiltros">
        <X class="size-4 mr-1" /> limpar
      </Button>
      <span class="text-xs text-muted-foreground ml-auto">
        {{ total }} anúncio(s)
      </span>
    </div>

    <div v-if="erro" class="text-sm text-red-500">erro: {{ erro }}</div>

    <!-- Tabela (computador) -->
    <div class="hidden md:block border rounded-md overflow-x-auto">
      <table class="w-full text-sm min-w-[1100px] border-collapse [&_th]:border [&_td]:border [&_th]:border-border [&_td]:border-border">
        <thead class="bg-muted/40 text-left">
          <tr class="whitespace-nowrap">
            <th class="px-3 py-2">Conta</th>
            <th class="px-3 py-2">Anúncio</th>
            <th class="px-3 py-2" title="Família do produto (o código sem o lote: dg053.ci e dg053.sp são a família dg053)">Famílias</th>
            <th
              class="px-3 py-2"
              title="Peças do lote .sp (São Bernardo) livres para o Flex: o estoque do .sp menos os pedidos Flex que ainda não saíram dele. Com mais de uma família, vale a menor."
            >Peças em SP</th>
            <th class="px-3 py-2" title="O que a regra do sistema quer para este anúncio">O sistema quer</th>
            <th class="px-3 py-2" title="O que o Mercado Livre / a Shopee mostrou na última conferência">Na plataforma</th>
            <!-- Ação antes do "Por quê": o botão Aprovar não pode ficar escondido
                 no fim de uma tabela que rola para o lado. -->
            <th class="px-3 py-2">Ação</th>
            <th class="px-3 py-2 min-w-[280px]">Por quê</th>
            <th class="px-3 py-2">Atualizado</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="a in itens"
            :key="chave(a)"
            class="border-t align-top"
            :class="a.aguardando_aprovacao ? 'bg-amber-50/60 dark:bg-amber-950/20' : 'hover:bg-muted/20'"
          >
            <td class="px-3 py-2 whitespace-nowrap">
              <div>{{ a.conta || '—' }}</div>
              <div class="text-[11px] text-muted-foreground">{{ nomePlataforma(a.plataforma) }}</div>
              <div
                v-if="config && !contasPermitidas.has(a.integration_id)"
                class="text-[11px] text-amber-700 dark:text-amber-400"
                title="Esta conta saiu da lista de contas liberadas: o sistema não mexe mais nela."
              >fora das contas liberadas</div>
            </td>
            <td class="px-3 py-2 max-w-[260px]">
              <div class="flex items-center gap-1 font-mono text-xs whitespace-nowrap">
                {{ a.external_id }}
                <a
                  v-if="linkAnuncio(a)"
                  :href="linkAnuncio(a)!"
                  target="_blank"
                  rel="noopener"
                  class="text-muted-foreground hover:text-foreground"
                  title="Abrir o anúncio no Mercado Livre (nova guia)"
                ><ExternalLink class="size-3" /></a>
              </div>
              <div v-if="a.titulo" class="text-xs truncate" :title="a.titulo">{{ a.titulo }}</div>
            </td>
            <td class="px-3 py-2 text-xs font-mono">{{ familiasTexto(a) }}</td>
            <td class="px-3 py-2 text-right tabular-nums" :class="saldoCls(a)">{{ a.saldo_sp ?? '—' }}</td>
            <td class="px-3 py-2 whitespace-nowrap">
              <span class="text-xs px-2 py-0.5 rounded border" :class="desejadoInfo(a).cls">{{ desejadoInfo(a).texto }}</span>
            </td>
            <td class="px-3 py-2 whitespace-nowrap text-xs">
              <span :class="divergente(a) ? 'text-amber-700 dark:text-amber-400 font-medium' : a.observado ? '' : 'text-muted-foreground'">
                {{ observadoTexto(a) }}
              </span>
              <div
                v-if="a.observado_em"
                class="text-[11px] text-muted-foreground"
                :title="'Conferido na plataforma em ' + fmtDataHora(a.observado_em)"
              >conferido {{ fmtDesde(a.observado_em) }}</div>
            </td>
            <td class="px-3 py-2 whitespace-nowrap text-xs">
              <button
                v-if="podeAprovar(a)"
                type="button"
                class="inline-flex items-center gap-1 rounded border border-emerald-400 px-2 py-1 text-xs font-medium text-emerald-800 hover:bg-emerald-50 disabled:opacity-50 dark:border-emerald-700 dark:text-emerald-300 dark:hover:bg-emerald-950/40"
                :disabled="aprovando.has(chave(a))"
                :title="a.recusa ? 'Tentar ligar o Flex de novo neste anúncio' : 'Aprovar: ligar o Flex neste anúncio'"
                @click="aprovar(a)"
              >
                <Check class="size-3.5" :class="aprovando.has(chave(a)) ? 'animate-pulse' : ''" />
                {{ a.recusa ? 'Tentar de novo' : 'Aprovar' }}
              </button>
              <span v-else-if="a.aguardando_aprovacao" class="text-amber-700 dark:text-amber-400">esperando aprovação</span>
              <span
                v-else-if="a.aprovado_em && a.desejado === 'ligado' && a.observado !== 'ligado'"
                class="text-muted-foreground"
                :title="'Aprovado em ' + fmtDataHora(a.aprovado_em)"
              >aprovado — falta ligar</span>
              <span v-else class="text-muted-foreground">—</span>
            </td>
            <td class="px-3 py-2 text-xs max-w-[360px]">
              <div :title="a.motivo || ''">{{ a.motivo_claro || a.motivo || '—' }}</div>
              <div v-if="recusaAparte(a)" class="mt-0.5 text-rose-700 dark:text-rose-400">
                A plataforma recusou ligar: {{ a.recusa }}
              </div>
              <div v-if="a.ultimo_erro" class="mt-0.5 text-rose-700 dark:text-rose-400">
                Último erro: {{ a.ultimo_erro }}
                <template v-if="a.proxima_tentativa"> · tenta de novo {{ fmtCurto(a.proxima_tentativa) }}</template>
              </div>
            </td>
            <td class="px-3 py-2 whitespace-nowrap text-xs" :title="fmtDataHora(a.atualizado_em)">
              {{ fmtDesde(a.atualizado_em) }}
            </td>
          </tr>
          <tr v-if="!loading && itens.length === 0">
            <td colspan="9" class="px-3 py-6 text-center text-muted-foreground">
              {{ resumo.avaliados === 0 ? 'Nenhum anúncio avaliado ainda.' : 'Nenhum anúncio com esses filtros.' }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Cartões (celular) -->
    <div class="md:hidden space-y-2">
      <div
        v-for="a in itens"
        :key="chave(a)"
        class="border rounded-md p-3 space-y-1.5 text-xs"
        :class="a.aguardando_aprovacao ? 'border-amber-300 bg-amber-50/60 dark:bg-amber-950/20' : ''"
      >
        <div class="flex items-start gap-2">
          <div class="flex-1 min-w-0">
            <div class="font-mono">{{ a.external_id }}</div>
            <div v-if="a.titulo" class="truncate">{{ a.titulo }}</div>
            <div class="text-muted-foreground">{{ nomePlataforma(a.plataforma) }} · {{ a.conta || '—' }}</div>
          </div>
          <span class="shrink-0 px-1.5 py-0.5 rounded border text-[10px]" :class="desejadoInfo(a).cls">{{ desejadoInfo(a).texto }}</span>
        </div>
        <div class="grid grid-cols-2 gap-x-3 gap-y-1">
          <div><span class="text-muted-foreground">Famílias:</span> {{ familiasTexto(a) }}</div>
          <div><span class="text-muted-foreground">Peças em SP:</span> <span :class="saldoCls(a)">{{ a.saldo_sp ?? '—' }}</span></div>
          <div class="col-span-2">
            <span class="text-muted-foreground">Na plataforma: </span>
            <span :class="divergente(a) ? 'text-amber-700 dark:text-amber-400 font-medium' : ''">{{ observadoTexto(a) }}</span>
            <span v-if="a.observado_em" class="text-muted-foreground"> · conferido {{ fmtDesde(a.observado_em) }}</span>
          </div>
        </div>
        <div>{{ a.motivo_claro || a.motivo || '—' }}</div>
        <div v-if="recusaAparte(a)" class="text-rose-700 dark:text-rose-400">A plataforma recusou ligar: {{ a.recusa }}</div>
        <div v-if="a.ultimo_erro" class="text-rose-700 dark:text-rose-400">Último erro: {{ a.ultimo_erro }}</div>
        <button
          v-if="podeAprovar(a)"
          type="button"
          class="inline-flex items-center gap-1 rounded border border-emerald-400 px-2 py-1 font-medium text-emerald-800 disabled:opacity-50 dark:text-emerald-300"
          :disabled="aprovando.has(chave(a))"
          @click="aprovar(a)"
        >
          <Check class="size-3.5" /> {{ a.recusa ? 'Tentar de novo' : 'Aprovar' }}
        </button>
      </div>
      <div v-if="!loading && itens.length === 0" class="text-center text-sm text-muted-foreground py-6 border rounded-md">
        {{ resumo.avaliados === 0 ? 'Nenhum anúncio avaliado ainda.' : 'Nenhum anúncio com esses filtros.' }}
      </div>
    </div>

    <!-- Paginação (100 por página, no servidor) -->
    <div v-if="total > POR_PAGINA" class="flex items-center justify-end gap-1">
      <Button size="sm" variant="outline" :disabled="pagina <= 1" @click="pagina--">
        <ChevronLeft class="size-4" />
      </Button>
      <span class="text-xs text-muted-foreground px-2 whitespace-nowrap">página {{ pagina }} de {{ totalPaginas }}</span>
      <Button size="sm" variant="outline" :disabled="pagina >= totalPaginas" @click="pagina++">
        <ChevronRight class="size-4" />
      </Button>
    </div>

    <!-- Pedidos Flex sem peça em SP (aviso do robô de prioridade, etapa 2) -->
    <div id="flex-pedidos-sem-sp" class="space-y-2 pt-2">
      <h2 class="text-base font-semibold flex items-center gap-1.5">
        <TriangleAlert class="size-4" :class="pedidosSemSp.length ? 'text-rose-600' : 'text-muted-foreground'" />
        Pedidos Flex sem peça em São Bernardo
        <span class="text-sm font-normal text-muted-foreground">({{ pedidosSemSp.length }})</span>
      </h2>
      <p class="text-sm text-muted-foreground">
        Pedidos Flex saem de São Bernardo. Nestes, o estoque .sp não tinha a peça e o sistema NÃO trocou o lote.
        Decida: separar em São Bernardo, transferir a peça para o .sp ou cancelar o pedido.
        Os que já <strong>saíram</strong> sem passar pelo .sp ficam aqui até alguém acertar o estoque no Bling
        (o Bling baixou outro lote): até lá o sistema desconta o pedido das peças livres em SP.
      </p>
      <div v-if="pedidosSemSp.length" class="border rounded-md overflow-x-auto">
        <table class="w-full text-sm min-w-[900px] border-collapse [&_th]:border [&_td]:border [&_th]:border-border [&_td]:border-border">
          <thead class="bg-muted/40 text-left">
            <tr class="whitespace-nowrap">
              <th class="px-3 py-2">Pedido Bling</th>
              <th class="px-3 py-2">Pedido na plataforma</th>
              <th class="px-3 py-2">Plataforma</th>
              <th class="px-3 py-2">Conta</th>
              <th class="px-3 py-2">SKU</th>
              <th class="px-3 py-2" title="Prazo para despachar dado pela plataforma">Despachar até</th>
              <th class="px-3 py-2">O que falta</th>
              <th v-if="canEdit" class="px-3 py-2">Ação</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in pedidosSemSp" :key="p.bling_id" class="border-t align-top bg-rose-50/50 dark:bg-rose-950/20">
              <td class="px-3 py-2 whitespace-nowrap">
                <button
                  v-if="p.numero"
                  type="button"
                  class="font-medium underline-offset-2 hover:underline"
                  title="Mostrar este pedido na lista Pedidos Flex"
                  @click="emit('buscar-pedido', p.numero)"
                >{{ p.numero }}</button>
                <span v-else class="text-muted-foreground">{{ p.bling_id }}</span>
                <div class="text-[11px] text-muted-foreground" :title="fmtDataHora(p.detectado_em)">visto {{ fmtDesde(p.detectado_em) }}</div>
              </td>
              <td class="px-3 py-2 whitespace-nowrap font-mono text-xs">{{ p.numeroloja || '—' }}</td>
              <td class="px-3 py-2 whitespace-nowrap">{{ nomePlataforma(p.plataforma) }}</td>
              <td class="px-3 py-2 whitespace-nowrap">{{ p.conta || '—' }}</td>
              <td class="px-3 py-2 font-mono text-xs">
                <div v-for="s in p.skus" :key="s">{{ s }}</div>
                <span v-if="!p.skus.length" class="text-muted-foreground">—</span>
              </td>
              <td class="px-3 py-2 whitespace-nowrap text-xs" :class="prazoVencido(p) ? 'text-rose-700 dark:text-rose-400 font-medium' : ''">
                {{ p.prazo ? fmtCurto(p.prazo) : '—' }}
                <div v-if="prazoVencido(p)" class="text-[11px]">prazo vencido</div>
              </td>
              <td class="px-3 py-2 text-xs max-w-[380px]">
                <div v-if="p.acerto_pendente" class="font-medium text-rose-700 dark:text-rose-400">
                  Já saiu de São Bernardo sem passar pelo .sp — acerte o estoque no Bling (transferência para o .sp).
                </div>
                <div v-if="p.alerta">{{ p.alerta }}</div>
                <span v-if="!p.alerta && !p.acerto_pendente">—</span>
              </td>
              <td v-if="canEdit" class="px-3 py-2 whitespace-nowrap text-xs">
                <button
                  v-if="p.acerto_pendente"
                  type="button"
                  class="inline-flex items-center gap-1 rounded border border-emerald-400 px-2 py-1 font-medium text-emerald-800 hover:bg-emerald-50 disabled:opacity-50 dark:border-emerald-700 dark:text-emerald-300 dark:hover:bg-emerald-950/40"
                  :disabled="acertando.has(p.bling_id)"
                  title="Já transferi a peça para o .sp no Bling: parar de descontar este pedido"
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
      <div v-else class="text-sm text-muted-foreground border rounded-md px-3 py-4 text-center">
        Nenhum pedido Flex sem peça em São Bernardo.
      </div>
    </div>
  </div>
</template>
