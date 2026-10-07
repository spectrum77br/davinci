<script lang="ts">
import type { PerfilAdsPower } from '~/components/AtendimentoAdsPower.vue'

// Hora do último "atualizar" por conversa, fora do componente: o painel é
// remontado a cada troca de conversa, e a trava de 60 s (a mesma do backend)
// não pode zerar só porque a pessoa foi e voltou.
const ultimaAtualizacao = new Map<string, number>()
const TRAVA_MS = 60_000

// ─── painel do pedido (01/10/2026, item 3 do Comunicador) ───────────────────
// GET /api/atendimento/conversas/{id}/painel (routers/atendimento_painel.py):
// estoque por item, margem (a da aba Margem), Observações do Bling (ao vivo,
// 5 min de memória), links e o perfil do AdsPower. Quem busca é a conversa
// (AtendimentoConversa) — o cabeçalho usa o AdsPower daqui. Cada bloco pode
// vir vazio com o porquê: a tela desenha o que vier.
export type LotePainel = {
  lote: string
  sku: string
  saldo: number | null
  atualizado_em: string | null
  de_venda: boolean
  rotulo: string | null
  proprio: boolean
  ativo: boolean
}
export type ComponentePainel = {
  sku: string | null
  nome: string | null
  existe: boolean
  quantidade_por_kit: number
  necessario: number | null
  saldo: number | null
  atualizado_em: string | null
  cobre: boolean | null
}
export type ItemEstoque = {
  sku: string | null
  descricao: string | null
  nome: string | null
  existe: boolean | null
  ativo: boolean
  quantidade: number | null
  saldo: number | null
  atualizado_em: string | null
  cobre: boolean | null
  lote: string | null
  lotes: LotePainel[]
  saldo_outros_lotes: number
  kit: boolean
  componentes: ComponentePainel[]
  falhou: boolean
}
export type ItemMargem = {
  sku: string | null
  produto: string | null
  quantidade: number | null
  margem: number | null
  margem_minima: number | null
  abaixo_da_minima: boolean | null
  status: string | null
  data_especial: boolean
  aguardando_repasse: boolean
  lucro?: number | null
  custo?: number | null
}
export type MargemPainel = {
  na_margem: boolean
  status: string | null
  margem: number | null
  margem_minima: number | null
  abaixo_da_minima: boolean | null
  lucro?: number | null
  itens: ItemMargem[]
  aviso: string | null
  falhou: boolean
}
export type ObservacoesBling = {
  observacoes: string | null
  observacoes_internas: string | null
  lido_em: string | null
  do_cache: boolean
  erro: string | null
  codigo: string | null
}
export type EnvioFoto = {
  pode: boolean
  motivo: string | null
  codigo: string | null
  tipos: string[]
  max_bytes: number
  legenda_obrigatoria: boolean
  legenda_permitida: boolean
}
export type Painel = {
  pedido: { numero_bling: string; bling_id: number | null; numeroloja: string | null; situacao_id: string | null; situacao: string | null } | null
  estoque: { itens: ItemEstoque[]; fonte: string; falhou: boolean }
  margem: MargemPainel | null
  ve_margem: boolean
  observacoes_bling: ObservacoesBling | null
  links: { bling: string | null; plataforma: { url: string; rotulo: string } | null }
  // O tipo mora no botão (AtendimentoAdsPower.vue).
  adspower: PerfilAdsPower
  envio_foto: EnvioFoto
  gerado_em: string | null
}

// Margem em fração (0.165) → "16,5%" (como a aba Margem).
export function pct(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '—'
  return `${(Number(v) * 100).toFixed(1).replace('.', ',')}%`
}

// O saldo do lote comprado contra a quantidade: verde cobre, vermelho não
// cobre, cinza sem dado. O saldo é o disponível no Bling (já sem o que está
// reservado para pedidos em aberto).
export function situacaoDoSaldo(it: Pick<ItemEstoque, 'existe' | 'saldo' | 'quantidade' | 'cobre'>): { nivel: 'cobre' | 'falta' | 'sem_dado'; texto: string } {
  if (it.existe === false) return { nivel: 'sem_dado', texto: 'SKU fora do catálogo do DaVinci' }
  if (it.saldo === null || it.saldo === undefined) return { nivel: 'sem_dado', texto: 'sem saldo lido' }
  if (it.cobre === true) return { nivel: 'cobre', texto: `cobre ${it.quantidade ?? ''}`.trim() }
  if (it.cobre === false) return { nivel: 'falta', texto: `não cobre ${it.quantidade ?? ''}`.trim() }
  return { nivel: 'sem_dado', texto: '' }
}
</script>

<script setup lang="ts">
// Painel da direita da Caixa (Atendimento, 25/09/2026; cara do Duoke em
// 28/09/2026), com as abas do Duoke: **Pedido | Produto | Cupom**.
// - Pedido: o retrato do pedido NA PLATAFORMA (`pedido_mkt`, lido pela API da
//   loja, só leitura): status, nº com copiar, data "(UTC-03:00)", itens com
//   foto, variação, SKU e preço, valor pago, pagamento e "Informações
//   logísticas" — tudo que o print do Duoke mostra, MENOS endereço (dado
//   pessoal não entra). Embaixo, recolhível, "No DaVinci": o pedido do Bling,
//   NF, entrega da Logística, chamados e devoluções (o contexto que a IA usa).
//   TikTok, Amazon e Magalu ainda não têm retrato: aí o "No DaVinci" já vem
//   aberto. (Magalu, 30/09/2026: o DaVinci ainda não lê pedido pela API dela —
//   o `enriquecer.py` só conhece Shopee e ML —, então o painel é o do Bling.)
// - Produto: o anúncio da pergunta do ML, os produtos que o comprador mandou
//   no chat e os do pedido, com foto e preço; e as outras perguntas do mesmo
//   comprador no mesmo anúncio (ML).
// - Cupom: desenhada, "em breve" (ainda não liga com a plataforma).
// - Em cima das abas (parte 2, P5), o cartão "Cliente" (AtendimentoCliente):
//   quem é este comprador para a loja, em duas linhas, e a linha do tempo.
// - Painel do pedido (01/10/2026, item 3): na aba Pedido, "Abrir no Bling" /
//   "Abrir na plataforma" no topo e, depois do retrato, Estoque (lote
//   comprado, lotes irmãos, kit), Margem (a da aba Margem) e Observações do
//   Bling (só leitura). Vem em `painel` (a conversa busca).
// - Avaliação (RF8, 02/10/2026), na aba Pedido: a nota, a data, as fotos, se
//   foi respondida (ou o prazo interno, se está pendente) e as avaliações
//   anteriores do mesmo cliente. Vem em `avaliacoes` (a mesma resposta do
//   cartão da conversa, AtendimentoAvaliacao); sem ela, o resumo do
//   `contexto.avaliacoes` (nota e estado, sem texto nem foto).
// - Garantia Uranyx (07/10/2026), no topo da aba Pedido: as garantias do
//   CPF/pedido da conversa, os vínculos já feitos, o alerta de CPF sem
//   garantia e o "Vincular à garantia" (AtendimentoGarantia). Vem em
//   `garantia` (a conversa busca); o botão segue a permissão "Registrar
//   atendimento", não o `canEdit` da caixa (só leitura não bloqueia).
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Copy,
  ExternalLink,
  Layers,
  Loader2,
  MessageCircleQuestion,
  MessagesSquare,
  NotebookPen,
  Package,
  Percent,
  Play,
  RotateCcw,
  Star,
  Store,
  Ticket,
  TriangleAlert,
  Truck,
  Undo2,
  X,
} from 'lucide-vue-next'
import {
  corDaNota,
  doContexto,
  estrelasDe,
  midiaSegura,
  prazoAvaliacao,
  situacaoAvaliacao,
  type AvaliacaoLoja,
  type AvaliacoesResposta,
} from '~/components/AtendimentoAvaliacao.vue'
import {
  cartaoProdutoDe,
  copiar,
  erroDaApi,
  fmtData,
  fmtDataHora,
  fmtDataPlataforma,
  fmtDinheiro,
  nivelLogistica,
  plataformaInfo,
  retratoDe,
  statusPedidoCls,
  useRelogio,
  type Anexo,
  type CartaoProduto,
  type Cliente,
  type ConversaDetalhe,
  type Contexto,
  type EventoCliente,
  type PedidoMkt,
} from '~/components/AtendimentoPlataforma.vue'
import type { AcessoGarantia, SituacaoConversa } from '~/lib/garantias'

const props = withDefaults(defineProps<{
  conversa: ConversaDetalhe
  contexto: Contexto | null
  pedidoMkt: PedidoMkt | null
  // Cartão do anúncio da pergunta do ML (ou null).
  produto: CartaoProduto | null
  // Produtos que apareceram na conversa (cartões nas mensagens).
  produtosConversa: CartaoProduto[]
  canEdit: boolean
  // Cartão "Cliente" (null = sem dado: o cartão não aparece).
  cliente?: Cliente | null
  // `flags.leitura_ativa` do /resumo. Desligada, o "atualizar" nem aparece:
  // o backend recusaria (409 leitura_desligada) sem ir à loja. Sem a prop,
  // ligada (prop booleana ausente viraria `false` no Vue).
  leituraAtiva?: boolean
  // `pedido_atualizavel` do detalhe: os mesmos portões do POST (loja
  // conectada, canal não desligado, leitura ligada). `false` esconde o
  // "atualizar"; ausente (API antiga), vale o resto das condições.
  atualizavel?: boolean | null
  // Painel do pedido (GET /conversas/{id}/painel): estoque, margem,
  // Observações do Bling, links. null enquanto carrega (ou se falhou).
  painel?: Painel | null
  painelCarregando?: boolean
  painelErro?: string | null
  // As avaliações de venda (GET /conversas/{id}/avaliacoes, que o cartão da
  // conversa busca). null = ainda não veio (ou falhou): vale o contexto.
  avaliacoes?: AvaliacoesResposta | null
  // Garantia Uranyx (GET /api/garantias/conversa/{id}, que a conversa busca)
  // e o que esta pessoa pode fazer com ela. Sem acesso, o bloco não aparece.
  garantia?: SituacaoConversa | null
  garantiaCarregando?: boolean
  garantiaErro?: string | null
  garantiaAcesso?: AcessoGarantia | null
}>(), {
  cliente: null, leituraAtiva: true, atualizavel: null, painel: null, painelCarregando: false, painelErro: null, avaliacoes: null,
  garantia: null, garantiaCarregando: false, garantiaErro: null, garantiaAcesso: null,
})
const emit = defineEmits<{
  (e: 'fechar'): void
  (e: 'atualizado', r: { pedido_mkt: PedidoMkt | null; produto: CartaoProduto | null }): void
  // Clique num item da linha do tempo que não é o pedido desta conversa: a
  // conversa decide (rolar até a mensagem, abrir outra conversa).
  (e: 'irPara', ev: EventoCliente): void
  // Reler o painel; `true` = reler as Observações no Bling na hora.
  (e: 'recarregarPainel', atualizar: boolean): void
  // Foto da avaliação: a conversa abre grande (o mesmo visor das fotos do chat).
  (e: 'abrirImagem', i: { url: string; nome: string }): void
  // Garantia: abrir o "Vincular à garantia" (o modal mora na conversa, que
  // tem as mensagens) e reler o bloco.
  (e: 'vincularGarantia'): void
  (e: 'recarregarGarantia'): void
}>()
const { api } = useApi()
const toasts = useToasts()

type Aba = 'pedido' | 'produto' | 'cupom'
const ABAS: { v: Aba; l: string }[] = [
  { v: 'pedido', l: 'Pedido' },
  { v: 'produto', l: 'Produto' },
  { v: 'cupom', l: 'Cupom' },
]
// Pergunta do ML (pré-venda) não tem pedido: abre no Produto.
const aba = ref<Aba>(
  props.conversa.canal === 'pergunta' || (!props.pedidoMkt && !props.conversa.pedido_marketplace && !props.contexto?.pedido && (props.produto || props.conversa.anuncio_titulo))
    ? 'produto'
    : 'pedido',
)

const pm = computed(() => props.pedidoMkt)
const itensPm = computed(() => pm.value?.itens || [])
const log = computed(() => pm.value?.logistica || null)
const moeda = computed(() => pm.value?.moeda || 'BRL')

// ─── atualizar o retrato (POST, trava de 60 s) ──────────────────────────────
// Só Shopee e ML têm retrato pela API por enquanto.
const temRetratoNaApi = computed(() => ['shopee', 'ml'].includes(props.conversa.plataforma))
// O POST exige a permissão de edição (quem só vê levaria 403): sem ela, o
// botão nem aparece — o retrato ainda se renova sozinho no sync. Com a
// leitura desligada (no servidor ou só nesta loja, que o 409 conta), também
// não: o clique só daria o aviso de recusa.
// O código do 409: `leitura_desligada` = no servidor (todas as lojas);
// `canal_desligado` = só esta loja. A frase do painel sem retrato depende disso.
const recusa = ref<'leitura_desligada' | 'canal_desligado' | null>(null)
const leituraDesligada = computed(() => props.leituraAtiva === false || recusa.value !== null)
const podeAtualizar = computed(() => props.canEdit && temRetratoNaApi.value && !leituraDesligada.value && props.atualizavel !== false && !props.conversa.somente_leitura && !!(props.conversa.pedido_marketplace || pm.value?.pedido))
const atualizando = ref(false)
async function atualizar() {
  if (atualizando.value) return
  const id = props.conversa.id
  const falta = TRAVA_MS - (Date.now() - (ultimaAtualizacao.get(id) || 0))
  if (falta > 0) {
    toasts.info('Pedido atualizado há pouco', `Dá para buscar de novo em ${Math.ceil(falta / 1000)} s.`)
    return
  }
  atualizando.value = true
  ultimaAtualizacao.set(id, Date.now())
  try {
    // Falha da loja não é erro HTTP: volta 200 com o retrato que já havia e
    // o `motivo` (recente = clique dentro do minuto; limite = teto por loja/
    // pessoa no minuto, nem foi à loja; sem_alteracao; falhou).
    const r = await api<{ pedido_mkt?: unknown; produto?: unknown; atualizado?: boolean; motivo?: string | null }>(
      `/api/atendimento/conversas/${encodeURIComponent(id)}/pedido/atualizar`,
      { method: 'POST' },
    )
    if (props.conversa.id !== id) return
    emit('atualizado', { pedido_mkt: retratoDe(r?.pedido_mkt), produto: cartaoProdutoDe((r?.produto ?? null) as Anexo | null) })
    if (r?.atualizado) toasts.success('Pedido atualizado da plataforma')
    else if (r?.motivo === 'recente') toasts.info('Pedido atualizado há pouco', 'Espere um minuto para buscar de novo.')
    // Teto do minuto (por loja e por pessoa): sem isto caía no aviso de que
    // a plataforma não devolveu o pedido — e ela nem foi consultada.
    else if (r?.motivo === 'limite') toasts.info('Muitas atualizações desta loja agora', 'Para não sobrecarregar a API da loja, tente de novo em 1 minuto.')
    else if (r?.motivo === 'falhou') toasts.warning('Não consegui falar com a loja agora', 'O painel continua com o que já tinha — tente de novo em instantes.')
    // `sem_alteracao`: foi à loja e ela não devolveu o pedido (API fora,
    // pedido não encontrado) — quando devolve, mesmo igual, vem `atualizado`.
    else toasts.warning('A plataforma não devolveu o pedido agora', 'O painel continua com o que já tinha — tente de novo em instantes.')
  } catch (e: any) {
    const er = erroDaApi(e, 'Não consegui atualizar o pedido')
    // Recusado antes de ir à loja (conversa ocupada, sem pedido na
    // plataforma): pode tentar de novo sem esperar o minuto.
    ultimaAtualizacao.delete(id)
    // Leitura desligada (geral ou desta loja): não é erro de quem clicou — o
    // botão some e o painel fica com o retrato que já tinha.
    const code = e?.data?.detail?.code
    if (code === 'leitura_desligada' || code === 'canal_desligado') {
      recusa.value = code
      toasts.info(er.texto)
    } else {
      toasts.error(er.texto, er.motivos)
    }
  } finally {
    atualizando.value = false
  }
}

async function copiarTexto(t: string | number | null | undefined, rotulo: string) {
  if (t === null || t === undefined || t === '') return
  const s = String(t)
  if (await copiar(s)) toasts.success(`${rotulo} copiado`)
  else window.prompt(rotulo, s)
}

// ─── No DaVinci (contexto: Bling, Logística, Chamados, Devoluções) ──────────
const pedido = computed(() => props.contexto?.pedido ?? null)
const logistica = computed(() => props.contexto?.logistica ?? null)
const chamados = computed(() => props.contexto?.chamados ?? [])
const devolucoes = computed(() => props.contexto?.devolucoes ?? [])
const notaFiscal = computed(() => props.contexto?.nota_fiscal ?? null)
const outrasPerguntas = computed(() => props.contexto?.outras_perguntas ?? [])
// Postagem da logística; sem ela, o "Em andamento" do Bling (despacho).
const enviadoEm = computed(() => logistica.value?.data_envio || pedido.value?.enviado_em || null)
const numeroMarketplace = computed(() => props.conversa.pedido_marketplace || pm.value?.pedido || pedido.value?.numeroloja || null)
const temDavinci = computed(() => !!(pedido.value || logistica.value || notaFiscal.value || chamados.value.length || devolucoes.value.length))
// Sem retrato (TikTok, Amazon, pedido ainda não buscado) o "No DaVinci" é o
// que há: começa aberto. Chamado aberto também abre — é aviso importante.
const davinciAberto = ref(!props.pedidoMkt || chamados.value.length > 0)

// Links para as telas que já existem: Chamados aceita ?search=, Logística
// aceita ?tab=<plataforma>&q=<busca> (é o que a Ouvidoria já usa).
const buscaPedido = computed(() => numeroMarketplace.value || (pedido.value?.numero ? String(pedido.value.numero) : ''))
const linkChamados = computed(() => (buscaPedido.value ? `/chamados?search=${encodeURIComponent(buscaPedido.value)}` : '/chamados'))
// Só as plataformas que têm aba na Logística: nas outras (Magalu, as lojas
// do robô) o link abria a busca na aba do ML, que não acha nada — some.
const ABAS_LOGISTICA = ['ml', 'shopee', 'amazon', 'tiktok']
const temAbaLogistica = computed(() => ABAS_LOGISTICA.includes(props.conversa.plataforma))
const linkLogistica = computed(() => {
  const p = new URLSearchParams()
  if (ABAS_LOGISTICA.includes(props.conversa.plataforma)) p.set('tab', props.conversa.plataforma)
  if (buscaPedido.value) p.set('q', buscaPedido.value)
  const q = p.toString()
  return q ? `/logistica?${q}` : '/logistica'
})

// Status do ML (UNANSWERED/ANSWERED/CLOSED_UNANSWERED/UNDER_REVIEW…) em
// português; `respondida` manda quando o status é desconhecido.
const STATUS_PERGUNTA: Record<string, string> = {
  UNANSWERED: 'sem resposta',
  ANSWERED: 'respondida',
  CLOSED_UNANSWERED: 'fechada sem resposta',
  UNDER_REVIEW: 'em análise no ML',
  BANNED: 'removida pelo ML',
  DELETED: 'apagada',
  DISABLED: 'desativada',
}
function statusPergunta(p: { status: string | null; respondida: boolean }) {
  return STATUS_PERGUNTA[(p.status || '').toUpperCase()] || (p.respondida ? 'respondida' : 'sem resposta')
}

// Por que não há retrato — em linguagem de gente.
const semRetrato = computed(() => {
  // `de` traz o artigo certo ("do TikTok", "da Amazon").
  const de = plataformaInfo(props.conversa.plataforma).de
  if (props.conversa.plataforma === 'instagram') return 'Direct do Instagram: sem pedido de loja por aqui.'
  // Magalu: a pergunta é pré-venda (no anúncio) e o chat pode ter começado
  // pelo produto, antes da compra — sem nº não é a API que falta.
  if (props.conversa.plataforma === 'magalu' && !numeroMarketplace.value) {
    return props.conversa.canal === 'pergunta'
      ? 'Pergunta de pré-venda (no anúncio): ainda não há pedido.'
      : 'Conversa sem pedido ligado — o comprador pode ter escrito pelo anúncio, antes de comprar.'
  }
  if (!temRetratoNaApi.value) return `O pedido ${de} ainda não vem pela API — abaixo, o que o DaVinci já sabe (Bling e Logística).`
  if (!numeroMarketplace.value) return 'Conversa sem pedido ligado (pergunta de pré-venda ou mensagem solta).'
  const nao = 'O pedido ainda não foi buscado na plataforma'
  if (props.leituraAtiva === false || recusa.value === 'leitura_desligada') return `${nao} — a leitura das lojas está desligada no servidor.`
  if (recusa.value === 'canal_desligado') return `${nao} — a leitura desta loja está desligada, então ele não é buscado agora.`
  // `pedido_atualizavel=false` sem o motivo: canal desligado, loja
  // arquivada/desconectada ou (sem o /resumo carregado) a leitura geral
  // desligada. Nesses casos não há botão nem "próxima leitura" que traga.
  if (props.atualizavel === false) return `${nao} — a leitura desta loja está desligada (ou a loja não está conectada), então ele não é buscado agora.`
  // "clique em buscar" só quando o botão está lá.
  return podeAtualizar.value ? `${nao} — clique em buscar.` : `${nao} — ele chega na próxima leitura da loja.`
})

// ─── Produto ────────────────────────────────────────────────────────────────
// Os produtos que o comprador mandou no chat, sem repetir o anúncio da
// pergunta nem o mesmo item duas vezes.
const produtosChat = computed(() => {
  const vistos = new Set<string>()
  if (props.produto?.item_id) vistos.add(props.produto.item_id)
  return props.produtosConversa.filter((p) => {
    const k = p.item_id || p.titulo || ''
    if (!k || vistos.has(k)) return false
    vistos.add(k)
    return true
  })
})

// Ícone e cor do estado da logística (como o Duoke: ✓ verde entregue,
// caminhão azul a caminho).
const nivelLog = computed(() => nivelLogistica(log.value?.status, log.value?.status_texto))
const LOG_ICONE = { entregue: CheckCircle2, caminho: Truck, problema: TriangleAlert, preparando: Package } as const
const LOG_COR = {
  entregue: 'text-emerald-600 dark:text-emerald-400',
  caminho: 'text-sky-600 dark:text-sky-400',
  problema: 'text-red-600 dark:text-red-400',
  preparando: 'text-orange-500 dark:text-orange-400',
} as const

const fotosFalhas = reactive(new Set<number>())

// ─── linha do tempo do cartão "Cliente" ─────────────────────────────────────
// Evento do pedido DESTA conversa (compra, envio, entrega, avaliação dele):
// abre a aba Pedido aqui mesmo. O resto (mensagem, pergunta, outro pedido)
// sobe para a conversa, que sabe rolar até a mensagem ou abrir a outra.
function irPara(ev: EventoCliente) {
  const ref = (ev.ref || '').trim()
  const deste = !!ref && [props.conversa.pedido_marketplace, pm.value?.pedido, pedido.value?.numeroloja].some((n) => !!n && String(n) === ref)
  if (deste) {
    aba.value = 'pedido'
    return
  }
  emit('irPara', ev)
}

// Situação da NF como a Shopee manda (`invoice_data.status`): o código cru
// ("pending") não diz nada a quem atende. Código desconhecido aparece como veio.
const NF_STATUS: Record<string, string> = {
  pending: 'Aguardando emissão',
}
function nfStatus(s: string | null | undefined): string {
  const cod = (s || '').trim()
  return NF_STATUS[cod.toLowerCase()] || cod
}

// ─── painel do pedido: estoque, margem, observações, links ──────────────────
const pnl = computed(() => props.painel)
const linksPedido = computed(() => pnl.value?.links ?? { bling: null, plataforma: null })
const itensEstoque = computed(() => pnl.value?.estoque?.itens ?? [])
const margemPedido = computed(() => pnl.value?.margem ?? null)
const obsBling = computed(() => pnl.value?.observacoes_bling ?? null)
const NIVEL_SALDO_CLS = {
  cobre: 'bg-emerald-500/15 text-emerald-800 dark:text-emerald-300',
  falta: 'bg-red-500/15 text-red-700 dark:text-red-300',
  sem_dado: 'bg-muted text-muted-foreground',
} as const
function lotesIrmaos(it: ItemEstoque) {
  return (it.lotes || []).filter((l) => !l.proprio)
}
const STATUS_MARGEM_CLS: Record<string, string> = {
  Aprovado: 'bg-emerald-500/15 text-emerald-800 dark:text-emerald-300',
  Pendente: 'bg-amber-500/20 text-amber-800 dark:text-amber-300',
  Reprovado: 'bg-red-500/15 text-red-700 dark:text-red-300',
}
function margemCls(m: number | null | undefined, minima: number | null | undefined) {
  if (m === null || m === undefined) return 'text-muted-foreground'
  return m >= (minima ?? 0) ? 'text-emerald-700 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'
}
// A Margem só mostra o lucro a admin; aqui vem só quando a API manda.
const temLucro = computed(() => margemPedido.value?.lucro !== undefined && margemPedido.value?.lucro !== null)
const linkMargem = computed(() => {
  const n = pnl.value?.pedido?.numero_bling
  return n ? `/margem?pedido=${encodeURIComponent(n)}` : '/margem'
})

// ─── avaliação de venda (RF8) ───────────────────────────────────────────────
const agora = useRelogio()
const avaliacoesPainel = computed<AvaliacaoLoja[]>(() =>
  props.avaliacoes ? props.avaliacoes.itens : (props.contexto?.avaliacoes ?? []).map(doContexto))
const avaliacoesDoPedido = computed(() => avaliacoesPainel.value.filter((a) => a.do_pedido))
const avaliacoesAnteriores = computed(() => avaliacoesPainel.value.filter((a) => !a.do_pedido))
const avaliacoesPendentes = computed(() => avaliacoesDoPedido.value.filter((a) => a.pendente).length)
</script>

<template>
  <div class="flex min-h-0 flex-1 flex-col">
    <!-- quem é o comprador para a loja (some quando não há dado) -->
    <AtendimentoCliente v-if="cliente" :cliente="cliente" :conversa-id="conversa.id" class="shrink-0 border-b" @ir-para="irPara" />

    <!-- abas -->
    <div class="flex shrink-0 items-stretch border-b px-1" role="tablist" aria-label="painel da conversa">
      <button
        v-for="a in ABAS"
        :key="a.v"
        type="button"
        role="tab"
        :aria-selected="aba === a.v"
        class="-mb-px inline-flex items-center gap-1 border-b-2 px-3 py-2.5 text-sm"
        :class="aba === a.v ? 'border-primary font-medium text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'"
        @click="aba = a.v"
      >
        {{ a.l }}
        <span v-if="a.v === 'cupom'" class="rounded bg-muted px-1 text-[9px] font-medium uppercase tracking-wide text-muted-foreground">em breve</span>
      </button>
      <button type="button" class="my-auto ml-auto rounded p-1 hover:bg-muted" title="esconder o painel" aria-label="esconder o painel" @click="emit('fechar')">
        <X class="size-4" />
      </button>
    </div>

    <div class="min-h-0 flex-1 overflow-y-auto overscroll-contain text-sm">
      <!-- ═══ PEDIDO ═══ -->
      <div v-if="aba === 'pedido'" class="space-y-4 px-3 py-3">
        <!-- Garantia Uranyx: garantias do CPF/pedido, alerta e o Vincular -->
        <AtendimentoGarantia
          v-if="garantiaAcesso?.ve"
          :situacao="garantia"
          :carregando="garantiaCarregando"
          :erro="garantiaErro"
          :pode-vincular="garantiaAcesso.registra"
          :pode-abrir="garantiaAcesso.consulta"
          :pode-cadastrar="garantiaAcesso.cadastra"
          @vincular="emit('vincularGarantia')"
          @recarregar="emit('recarregarGarantia')"
        />
        <!-- nº do Bling e os botões "Abrir no Bling" / "Abrir na plataforma" -->
        <div v-if="pnl?.pedido || linksPedido.bling || linksPedido.plataforma" class="flex flex-wrap items-center gap-1.5 text-xs">
          <span v-if="pnl?.pedido" class="inline-flex min-w-0 items-center gap-1" :title="pnl.pedido.situacao ? `situação no Bling: ${pnl.pedido.situacao}` : ''">
            <span class="text-muted-foreground">Bling</span>
            <span class="font-mono">{{ pnl.pedido.numero_bling }}</span>
            <button type="button" class="rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground" title="copiar nº do Bling" aria-label="copiar nº do Bling" @click="copiarTexto(pnl.pedido.numero_bling, 'Nº do Bling')"><Copy class="size-3" /></button>
            <span v-if="pnl.pedido.situacao" class="truncate rounded bg-muted px-1.5 py-px text-[11px]">{{ pnl.pedido.situacao }}</span>
          </span>
          <span class="ml-auto flex flex-wrap items-center gap-1">
            <a
              v-if="linksPedido.bling"
              :href="linksPedido.bling"
              target="_blank"
              rel="noopener noreferrer"
              class="inline-flex items-center gap-0.5 rounded border px-1.5 py-0.5 hover:bg-muted"
              title="abrir o pedido no Bling (outra aba) — aqui é só leitura"
            >Abrir no Bling<ExternalLink class="size-3" /></a>
            <AtendimentoAbrirPlataforma
              v-if="linksPedido.plataforma"
              :href="linksPedido.plataforma.url"
              :perfil="pnl?.adspower ?? null"
              :conversa-id="conversa?.id ?? null"
              titulo="abrir o pedido no painel do vendedor (outra aba)"
              class="inline-flex items-center gap-0.5 rounded border px-1.5 py-0.5 hover:bg-muted"
            >{{ linksPedido.plataforma.rotulo }}<ExternalLink class="size-3" /></AtendimentoAbrirPlataforma>
          </span>
        </div>

        <template v-if="pm">
          <section class="space-y-1">
            <div class="flex items-center gap-2">
              <span v-if="pm.status_texto || pm.status" class="rounded border px-1.5 py-px text-xs" :class="statusPedidoCls(pm.status, pm.status_texto)">{{ pm.status_texto || pm.status }}</span>
              <button
                v-if="podeAtualizar"
                type="button"
                class="ml-auto rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50"
                :disabled="atualizando"
                :title="`atualizar da plataforma${pm.atualizado_em ? ` (retrato de ${fmtDataHora(pm.atualizado_em)})` : ''}`"
                aria-label="atualizar o pedido da plataforma"
                @click="atualizar"
              >
                <RotateCcw class="size-4" :class="{ 'animate-spin': atualizando }" />
              </button>
            </div>
            <div v-if="pm.pedido" class="flex items-center gap-1 font-medium">
              <span class="min-w-0 truncate font-mono text-[13px]" :title="pm.pedido">{{ pm.pedido }}</span>
              <button type="button" class="shrink-0 rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground" title="copiar nº do pedido" aria-label="copiar nº do pedido" @click="copiarTexto(pm.pedido, 'Nº do pedido')"><Copy class="size-3.5" /></button>
            </div>
            <div v-if="pm.criado_em" class="text-xs text-muted-foreground">{{ fmtDataPlataforma(pm.criado_em) }} (UTC-03:00)</div>
          </section>

          <!-- itens -->
          <ul v-if="itensPm.length" class="space-y-3">
            <li v-for="(it, i) in itensPm" :key="i" class="flex gap-2.5">
              <img
                v-if="it.imagem && !fotosFalhas.has(i)"
                :src="it.imagem"
                alt=""
                loading="lazy"
                referrerpolicy="no-referrer"
                class="size-14 shrink-0 rounded border object-cover"
                @error="fotosFalhas.add(i)"
              />
              <span v-else class="flex size-14 shrink-0 items-center justify-center rounded border bg-muted text-muted-foreground"><Package class="size-5" /></span>
              <div class="min-w-0 flex-1 text-xs">
                <div class="flex items-start gap-2">
                  <div class="line-clamp-2 min-w-0 flex-1 text-[13px] font-medium leading-5" :title="it.titulo || ''">
                    <AtendimentoIconePlataforma :plataforma="conversa.plataforma" :tamanho="13" decorativo class="-mt-0.5 mr-0.5" />{{ it.titulo || 'Produto' }}
                  </div>
                  <div v-if="it.preco !== null && it.preco !== undefined" class="shrink-0 text-right tabular-nums">
                    <div class="text-[13px]">{{ fmtDinheiro(it.preco, moeda) }}</div>
                    <s v-if="it.preco_original && it.preco_original > it.preco" class="text-[11px] text-muted-foreground">{{ fmtDinheiro(it.preco_original, moeda) }}</s>
                  </div>
                </div>
                <div v-if="it.variacao" class="mt-0.5 truncate text-muted-foreground" :title="it.variacao">Variação: {{ it.variacao }}</div>
                <div class="mt-0.5 flex items-center gap-2 text-muted-foreground">
                  <span v-if="it.sku" class="min-w-0 truncate">SKU: <span class="font-mono">{{ it.sku }}</span></span>
                  <span v-if="it.quantidade !== null && it.quantidade !== undefined" class="ml-auto shrink-0 tabular-nums">× {{ it.quantidade }}</span>
                </div>
              </div>
            </li>
          </ul>

          <!-- valores e pagamento -->
          <dl class="grid grid-cols-[1fr_auto] gap-x-3 gap-y-1.5 text-xs">
            <template v-if="pm.valor_pago !== null && pm.valor_pago !== undefined">
              <dt class="text-muted-foreground">Valor pago pelo comprador</dt>
              <dd class="text-right font-semibold tabular-nums">{{ fmtDinheiro(pm.valor_pago, moeda) }}</dd>
            </template>
            <template v-if="pm.total !== null && pm.total !== undefined && pm.total !== pm.valor_pago">
              <dt class="text-muted-foreground">Total do pedido</dt>
              <dd class="text-right tabular-nums">{{ fmtDinheiro(pm.total, moeda) }}</dd>
            </template>
            <template v-if="pm.frete !== null && pm.frete !== undefined">
              <dt class="text-muted-foreground">Frete</dt>
              <dd class="text-right tabular-nums">{{ fmtDinheiro(pm.frete, moeda) }}</dd>
            </template>
            <dt class="text-muted-foreground">Método de pagamento</dt>
            <dd class="text-right">{{ pm.pagamento_metodo || '—' }}</dd>
            <dt class="text-muted-foreground">Tempo de pagamento</dt>
            <dd class="text-right tabular-nums">{{ pm.pago_em ? fmtDataPlataforma(pm.pago_em) : '—' }}</dd>
          </dl>

          <!-- nota fiscal -->
          <section v-if="pm.nf && (pm.nf.numero || pm.nf.status)" class="space-y-1">
            <h4 class="text-[13px] font-semibold">Nota fiscal</h4>
            <div class="flex items-center gap-2 rounded-md bg-muted/50 px-2.5 py-1.5 text-xs">
              <span class="text-muted-foreground">{{ pm.nf.numero ? 'Número' : 'Situação' }}</span>
              <span v-if="pm.nf.numero" class="ml-auto flex items-center gap-1 font-mono">
                {{ pm.nf.numero }}
                <button type="button" class="rounded p-0.5 opacity-60 hover:bg-muted hover:opacity-100" title="copiar nº da NF" @click="copiarTexto(pm.nf.numero, 'Nº da NF')"><Copy class="size-3" /></button>
              </span>
              <span v-if="pm.nf.status" :class="pm.nf.numero ? '' : 'ml-auto'">{{ nfStatus(pm.nf.status) }}</span>
            </div>
          </section>

          <!-- logística -->
          <section class="space-y-1.5">
            <h4 class="text-[13px] font-semibold">Informações logísticas</h4>
            <dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-xs">
              <dt class="text-muted-foreground">Provedor de transporte</dt>
              <dd class="truncate text-right" :title="log?.transportadora || ''">{{ log?.transportadora || '—' }}</dd>
              <!-- as duas do Duoke: quando saiu (coleta/postagem) e quando a plataforma deu o pedido por concluído -->
              <dt class="text-muted-foreground">Hora de envio</dt>
              <dd class="text-right tabular-nums">{{ pm.enviado_em ? fmtDataPlataforma(pm.enviado_em) : '—' }}</dd>
              <dt class="text-muted-foreground">Tempo concluído</dt>
              <dd class="text-right tabular-nums" :class="pm.concluido_em ? 'text-emerald-700 dark:text-emerald-300' : ''">{{ pm.concluido_em ? fmtDataPlataforma(pm.concluido_em) : '—' }}</dd>
            </dl>
            <dl v-if="log && (log.rastreio || log.status_texto || log.status || log.descricao || log.atualizado_em)" class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-2 rounded-md bg-muted/50 px-2.5 py-2 text-xs">
              <template v-if="log.rastreio">
                <dt class="text-muted-foreground">Número de rastreio</dt>
                <dd class="flex min-w-0 items-center justify-end gap-1">
                  <span class="truncate font-mono text-primary" :title="log.rastreio">{{ log.rastreio }}</span>
                  <button type="button" class="shrink-0 rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground" title="copiar rastreio" aria-label="copiar rastreio" @click="copiarTexto(log.rastreio, 'Rastreio')"><Copy class="size-3" /></button>
                </dd>
              </template>
              <template v-if="log.status_texto || log.status">
                <dt class="text-muted-foreground">Estado da logística</dt>
                <dd class="flex items-center justify-end gap-1 text-right">
                  <component :is="LOG_ICONE[nivelLog]" class="size-4 shrink-0" :class="LOG_COR[nivelLog]" />
                  <span>{{ log.status_texto || log.status }}</span>
                </dd>
              </template>
              <template v-if="log.descricao">
                <dt class="text-muted-foreground">Descrição mais recente</dt>
                <dd class="text-right" :title="log.descricao"><span class="line-clamp-3">{{ log.descricao }}</span></dd>
              </template>
              <template v-if="log.atualizado_em">
                <dt class="text-muted-foreground">Hora de atualização</dt>
                <dd class="text-right tabular-nums">{{ fmtDataPlataforma(log.atualizado_em) }}</dd>
              </template>
            </dl>
            <div v-else class="text-[11px] text-muted-foreground">Sem rastreio na plataforma ainda.</div>
          </section>

          <div class="flex items-center gap-2 text-[11px] text-muted-foreground">
            <span v-if="pm.atualizado_em">Lido {{ plataformaInfo(conversa.plataforma).de }} em {{ fmtDataHora(pm.atualizado_em) }}</span>
            <button v-if="podeAtualizar" type="button" class="ml-auto inline-flex items-center gap-1 rounded border px-1.5 py-0.5 hover:bg-muted disabled:opacity-50" :disabled="atualizando" @click="atualizar">
              <RotateCcw class="size-3" :class="{ 'animate-spin': atualizando }" /> atualizar
            </button>
          </div>
        </template>

        <!-- sem retrato -->
        <div v-else class="space-y-2 rounded-md border border-dashed px-3 py-4 text-center text-xs text-muted-foreground">
          <Package class="mx-auto size-6 opacity-60" />
          <div>{{ semRetrato }}</div>
          <div v-if="numeroMarketplace" class="flex items-center justify-center gap-1 font-mono text-foreground">
            {{ numeroMarketplace }}
            <button type="button" class="rounded p-0.5 opacity-60 hover:bg-muted hover:opacity-100" title="copiar nº do pedido" @click="copiarTexto(numeroMarketplace, 'Nº do pedido')"><Copy class="size-3" /></button>
          </div>
          <button v-if="podeAtualizar" type="button" class="inline-flex items-center gap-1 rounded border bg-background px-2 py-1 text-foreground hover:bg-muted disabled:opacity-50" :disabled="atualizando" @click="atualizar">
            <RotateCcw class="size-3.5" :class="{ 'animate-spin': atualizando }" /> buscar na plataforma
          </button>
        </div>

        <!-- painel do pedido: carregando / falhou -->
        <div v-if="painelCarregando && !pnl" class="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <Loader2 class="size-3.5 animate-spin" /> lendo estoque, margem e observações…
        </div>
        <div v-else-if="painelErro && !pnl" class="flex items-center gap-1.5 rounded-md border border-dashed px-2.5 py-1.5 text-[11px] text-muted-foreground">
          <TriangleAlert class="size-3.5 shrink-0" />
          <span class="flex-1">{{ painelErro }}</span>
          <button type="button" class="underline hover:text-foreground" @click="emit('recarregarPainel', false)">tentar de novo</button>
        </div>

        <!-- ESTOQUE do que o comprador comprou (products.stock: o disponível no Bling) -->
        <section v-if="itensEstoque.length" class="space-y-1.5">
          <div class="flex items-center gap-1.5 text-[13px] font-semibold">
            <Layers class="size-4 text-muted-foreground" /> Estoque
            <span class="text-[11px] font-normal text-muted-foreground" title="saldo disponível no Bling (já sem o que está reservado para pedidos em aberto). Para confirmar uma troca, vale o saldo ao vivo do Bling.">· disponível no Bling</span>
            <button v-if="pnl" type="button" class="ml-auto rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50" :disabled="painelCarregando" title="ler o estoque de novo" aria-label="ler o estoque de novo" @click="emit('recarregarPainel', false)">
              <RotateCcw class="size-3.5" :class="{ 'animate-spin': painelCarregando }" />
            </button>
          </div>
          <ul class="space-y-2">
            <li v-for="(it, i) in itensEstoque" :key="`${it.sku}-${i}`" class="rounded-md border px-2.5 py-2 text-xs">
              <div class="flex items-start gap-2">
                <div class="min-w-0 flex-1">
                  <div class="line-clamp-2 text-[12px]" :title="it.descricao || it.nome || ''">{{ it.descricao || it.nome || 'Produto' }}</div>
                  <div class="mt-0.5 flex items-center gap-1.5 text-muted-foreground">
                    <span class="font-mono">{{ it.sku }}</span>
                    <span v-if="it.kit" class="rounded bg-muted px-1 text-[10px]">kit</span>
                    <span v-if="it.quantidade" class="tabular-nums">× {{ it.quantidade }}</span>
                  </div>
                </div>
                <div class="shrink-0 text-right">
                  <div class="rounded px-1.5 py-0.5 font-semibold tabular-nums" :class="NIVEL_SALDO_CLS[situacaoDoSaldo(it).nivel]" :title="situacaoDoSaldo(it).texto">
                    {{ it.saldo ?? '—' }}<span class="ml-1 font-normal">em estoque</span>
                  </div>
                  <div v-if="situacaoDoSaldo(it).texto" class="mt-0.5 text-[10px]" :class="situacaoDoSaldo(it).nivel === 'falta' ? 'text-red-700 dark:text-red-300' : 'text-muted-foreground'">{{ situacaoDoSaldo(it).texto }}</div>
                </div>
              </div>
              <div v-if="it.atualizado_em" class="mt-1 text-[10px] text-muted-foreground">atualizado em {{ fmtDataHora(it.atualizado_em) }}</div>
              <div v-if="it.falhou" class="mt-1 text-[10px] text-amber-800 dark:text-amber-300">Não consegui ler o saldo deste item agora.</div>
              <!-- lotes irmãos: o mesmo produto em outro estoque -->
              <div v-if="lotesIrmaos(it).length" class="mt-1.5 flex flex-wrap items-center gap-1 text-[11px]">
                <span class="text-muted-foreground">Outros lotes:</span>
                <span
                  v-for="l in lotesIrmaos(it)"
                  :key="l.sku"
                  class="rounded border px-1 py-px tabular-nums"
                  :class="[!l.de_venda ? 'border-dashed text-muted-foreground' : '', (l.saldo ?? 0) > 0 && l.de_venda ? 'border-emerald-500/40' : '']"
                  :title="`${l.sku}${l.rotulo ? ` — ${l.rotulo}` : ''}${l.ativo ? '' : ' (inativo no Bling)'}${l.atualizado_em ? ` · atualizado em ${fmtDataHora(l.atualizado_em)}` : ''}`"
                >.{{ l.lote }} {{ l.saldo ?? '—' }}</span>
              </div>
              <div v-if="it.cobre === false && it.saldo_outros_lotes > 0" class="mt-1 text-[11px] text-emerald-800 dark:text-emerald-300">
                Há {{ it.saldo_outros_lotes }} em outros lotes de venda.
              </div>
              <!-- kit: cada componente com o que o pedido precisa -->
              <ul v-if="it.componentes?.length" class="mt-1.5 space-y-0.5 border-t pt-1.5 text-[11px]">
                <li v-for="(c, j) in it.componentes" :key="`${c.sku}-${j}`" class="flex items-center gap-1.5">
                  <span class="min-w-0 truncate font-mono" :title="c.nome || ''">{{ c.sku || 'componente fora do catálogo' }}</span>
                  <span v-if="c.necessario" class="text-muted-foreground">precisa {{ c.necessario }}</span>
                  <span class="ml-auto rounded px-1 tabular-nums" :class="c.cobre === true ? NIVEL_SALDO_CLS.cobre : c.cobre === false ? NIVEL_SALDO_CLS.falta : NIVEL_SALDO_CLS.sem_dado">{{ c.saldo ?? '—' }}</span>
                </li>
              </ul>
            </li>
          </ul>
        </section>

        <!-- ENCAIXE (item 4, outro dev): o cartão de Ag. cancelamento e o botão
             "Sugerir troca" entram AQUI, logo abaixo do estoque (contrato:
             davinci-atendimento-nomes-para-o-dev.md §4 "Lugar reservado"). -->

        <!-- MARGEM (a mesma da aba Margem) -->
        <section v-if="pnl && pnl.pedido && !pnl.ve_margem" class="text-[11px] text-muted-foreground">
          <Percent class="mr-1 inline size-3.5" />A margem aparece para quem tem acesso à aba Margem.
        </section>
        <section v-else-if="margemPedido" class="space-y-1.5">
          <div class="flex items-center gap-1.5 text-[13px] font-semibold">
            <Percent class="size-4 text-muted-foreground" /> Margem
            <span v-if="margemPedido.status" class="rounded px-1.5 py-px text-[10px] font-medium" :class="STATUS_MARGEM_CLS[margemPedido.status] || 'bg-muted'">{{ margemPedido.status }}</span>
            <NuxtLink :to="linkMargem" target="_blank" class="ml-auto inline-flex items-center gap-0.5 text-[11px] font-normal text-muted-foreground hover:text-foreground hover:underline" title="abrir na aba Margem (outra aba)">abrir <ExternalLink class="size-3" /></NuxtLink>
          </div>
          <div v-if="margemPedido.falhou" class="text-[11px] text-amber-800 dark:text-amber-300">Não consegui ler a margem agora.</div>
          <template v-else-if="margemPedido.na_margem">
            <dl class="grid grid-cols-[1fr_auto] gap-x-3 gap-y-1 text-xs">
              <dt class="text-muted-foreground">Margem do pedido</dt>
              <dd class="text-right font-semibold tabular-nums" :class="margemCls(margemPedido.margem, margemPedido.margem_minima)">{{ pct(margemPedido.margem) }}</dd>
              <template v-if="margemPedido.margem_minima !== null">
                <dt class="text-muted-foreground">Mínima</dt>
                <dd class="text-right tabular-nums text-muted-foreground">{{ pct(margemPedido.margem_minima) }}</dd>
              </template>
              <template v-if="temLucro">
                <dt class="text-muted-foreground">Lucro</dt>
                <dd class="text-right tabular-nums" :class="margemCls(margemPedido.margem, margemPedido.margem_minima)">{{ fmtDinheiro(margemPedido.lucro ?? null, 'BRL') }}</dd>
              </template>
            </dl>
            <ul v-if="margemPedido.itens.length > 1" class="space-y-0.5 text-[11px]">
              <li v-for="(mi, k) in margemPedido.itens" :key="`${mi.sku}-${k}`" class="flex items-center gap-1.5">
                <span class="min-w-0 truncate font-mono text-muted-foreground">{{ mi.sku }}</span>
                <span class="ml-auto tabular-nums" :class="margemCls(mi.margem, mi.margem_minima)">{{ pct(mi.margem) }}</span>
              </li>
            </ul>
            <p v-if="margemPedido.aviso" class="text-[11px] text-muted-foreground">{{ margemPedido.aviso }}</p>
          </template>
          <p v-else class="text-[11px] text-muted-foreground">{{ margemPedido.aviso }}</p>
        </section>

        <!-- OBSERVAÇÕES DO BLING (GET ao vivo do pedido, só leitura) -->
        <section v-if="obsBling" class="space-y-1.5">
          <div class="flex items-center gap-1.5 text-[13px] font-semibold">
            <NotebookPen class="size-4 text-muted-foreground" /> Observações do Bling
            <span class="text-[11px] font-normal text-muted-foreground">· só leitura</span>
            <button type="button" class="ml-auto rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50" :disabled="painelCarregando" title="ler de novo no Bling agora" aria-label="ler as observações de novo no Bling" @click="emit('recarregarPainel', true)">
              <RotateCcw class="size-3.5" :class="{ 'animate-spin': painelCarregando }" />
            </button>
          </div>
          <div v-if="obsBling.erro" class="rounded-md border border-dashed px-2.5 py-1.5 text-[11px] text-muted-foreground">{{ obsBling.erro }}</div>
          <template v-else>
            <div v-if="obsBling.observacoes" class="max-h-48 overflow-y-auto whitespace-pre-wrap break-words rounded-md bg-muted/50 px-2.5 py-1.5 text-xs">{{ obsBling.observacoes }}</div>
            <div v-else class="text-[11px] text-muted-foreground">Sem observações no pedido.</div>
            <div v-if="obsBling.observacoes_internas" class="space-y-0.5">
              <div class="text-[11px] text-muted-foreground">Observações internas</div>
              <div class="max-h-32 overflow-y-auto whitespace-pre-wrap break-words rounded-md bg-muted/50 px-2.5 py-1.5 text-xs">{{ obsBling.observacoes_internas }}</div>
            </div>
          </template>
          <div v-if="obsBling.lido_em" class="text-[10px] text-muted-foreground">lido no Bling em {{ fmtDataHora(obsBling.lido_em) }}<template v-if="obsBling.do_cache"> (guardado por até 5 min)</template></div>
        </section>

        <!-- AVALIAÇÃO de venda (RF8): nota, data, fotos, respondida ou não, e as
             anteriores do mesmo cliente. Responder fica no cartão da conversa. -->
        <section v-if="avaliacoesPainel.length" class="space-y-1.5" data-painel-avaliacao>
          <div class="flex items-center gap-1.5 text-[13px] font-semibold">
            <Star class="size-4 text-yellow-500" /> Avaliação
            <span
              v-if="avaliacoesPendentes"
              class="ml-auto rounded-full bg-yellow-400/30 px-1.5 text-[10px] font-semibold text-yellow-900 dark:text-yellow-200"
              title="avaliação sem resposta da loja — fica com a etiqueta Avaliação até ser respondida ou tratada"
            >{{ avaliacoesPendentes }} sem resposta</span>
          </div>
          <ul v-if="avaliacoesDoPedido.length" class="space-y-1.5">
            <li
              v-for="a in avaliacoesDoPedido"
              :key="a.id"
              class="rounded-md border px-2.5 py-2 text-xs"
              :class="a.pendente && a.nota_baixa ? 'border-red-500/40' : ''"
            >
              <div class="flex flex-wrap items-center gap-1.5">
                <span class="text-sm leading-4 tracking-tight" :class="corDaNota(a.estrelas)" :aria-label="`nota ${a.estrelas} de 5`">{{ estrelasDe(a.estrelas) }}</span>
                <span class="font-medium tabular-nums">{{ a.estrelas }}/5</span>
                <span class="rounded px-1.5 py-px text-[10px]" :class="situacaoAvaliacao(a).cls" :title="situacaoAvaliacao(a).dica">{{ situacaoAvaliacao(a).rotulo }}</span>
                <span v-if="a.criado_em" class="ml-auto tabular-nums text-muted-foreground" :title="fmtDataHora(a.criado_em)">{{ fmtData(a.criado_em) }}</span>
              </div>
              <div v-if="a.anuncio_titulo" class="mt-0.5 truncate text-muted-foreground" :title="a.anuncio_titulo">{{ a.anuncio_titulo }}</div>
              <div v-if="midiaSegura(a.midia).length" class="mt-1 flex flex-wrap gap-1">
                <template v-for="(m, i) in midiaSegura(a.midia)" :key="m.url">
                  <button
                    v-if="m.tipo === 'imagem'"
                    type="button"
                    class="overflow-hidden rounded border bg-background"
                    :title="`abrir a foto ${i + 1} da avaliação`"
                    @click="emit('abrirImagem', { url: m.url, nome: `foto ${i + 1} da avaliação ${a.estrelas}★` })"
                  >
                    <img :src="m.url" alt="" loading="lazy" referrerpolicy="no-referrer" class="size-10 object-cover" />
                  </button>
                  <a v-else :href="m.url" target="_blank" rel="noopener noreferrer" class="flex size-10 items-center justify-center rounded border bg-muted text-muted-foreground hover:text-foreground" title="abrir o vídeo do comprador (outra aba)">
                    <Play class="size-4" /><span class="sr-only">vídeo</span>
                  </a>
                </template>
              </div>
              <div v-if="a.respondida && a.resposta_em" class="mt-1 text-emerald-700 dark:text-emerald-300">respondida em {{ fmtDataHora(a.resposta_em) }}</div>
              <div
                v-else-if="prazoAvaliacao(a, agora)"
                class="mt-1 inline-block rounded px-1.5 py-px tabular-nums"
                :class="prazoAvaliacao(a, agora)!.cls"
                :title="prazoAvaliacao(a, agora)!.titulo"
              >prazo interno: {{ prazoAvaliacao(a, agora)!.texto }}</div>
            </li>
          </ul>
          <div v-else class="text-[11px] text-muted-foreground">Este pedido ainda não tem avaliação.</div>
          <div v-if="avaliacoesAnteriores.length" class="space-y-0.5">
            <div class="text-[11px] text-muted-foreground">Anteriores deste cliente</div>
            <ul class="space-y-0.5 text-[11px]">
              <li v-for="a in avaliacoesAnteriores" :key="a.id" class="flex items-center gap-1.5">
                <span class="tracking-tight" :class="corDaNota(a.estrelas)" :aria-label="`nota ${a.estrelas} de 5`">{{ estrelasDe(a.estrelas) }}</span>
                <span class="rounded px-1 py-px text-[10px]" :class="situacaoAvaliacao(a).cls">{{ situacaoAvaliacao(a).rotulo }}</span>
                <span v-if="a.pedido" class="min-w-0 truncate font-mono text-muted-foreground" :title="`pedido ${a.pedido}`">{{ a.pedido }}</span>
                <span v-if="a.criado_em" class="ml-auto shrink-0 tabular-nums text-muted-foreground">{{ fmtData(a.criado_em) }}</span>
              </li>
            </ul>
          </div>
        </section>

        <!-- No DaVinci -->
        <section class="rounded-md border">
          <button type="button" class="flex w-full items-center gap-1.5 px-2.5 py-2 text-left text-[13px] font-semibold" :aria-expanded="davinciAberto" @click="davinciAberto = !davinciAberto">
            <ChevronDown v-if="davinciAberto" class="size-4 text-muted-foreground" />
            <ChevronRight v-else class="size-4 text-muted-foreground" />
            <Store class="size-4 text-muted-foreground" /> No DaVinci
            <span v-if="chamados.length" class="ml-auto rounded-full bg-amber-500/20 px-1.5 text-[10px] font-semibold text-amber-800 dark:text-amber-300" :title="`${chamados.length} chamado(s) aberto(s)`">{{ chamados.length }} chamado{{ chamados.length > 1 ? 's' : '' }}</span>
            <span v-else-if="!temDavinci" class="ml-auto text-[11px] font-normal text-muted-foreground">nada ligado</span>
          </button>
          <div v-if="davinciAberto" class="space-y-4 border-t px-2.5 py-2.5">
            <div v-if="!temDavinci" class="text-xs text-muted-foreground">
              {{ numeroMarketplace ? 'Pedido ainda não encontrado no DaVinci (Bling/Logística).' : 'Nada do DaVinci ligado a esta conversa.' }}
            </div>

            <!-- pedido Bling -->
            <section v-if="pedido" class="space-y-1.5">
              <div class="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                <Package class="size-3.5" /> Pedido no Bling
              </div>
              <dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
                <template v-if="pedido.numero">
                  <dt class="text-muted-foreground">Bling</dt>
                  <dd class="flex items-center gap-1 font-mono">
                    {{ pedido.numero }}
                    <button type="button" class="rounded p-0.5 opacity-60 hover:bg-muted hover:opacity-100" title="copiar nº do Bling" @click="copiarTexto(pedido.numero, 'Nº do Bling')"><Copy class="size-3" /></button>
                  </dd>
                </template>
                <template v-if="pedido.data">
                  <dt class="text-muted-foreground">Data</dt>
                  <dd>{{ fmtData(pedido.data) }}</dd>
                </template>
                <template v-if="pedido.situacao">
                  <dt class="text-muted-foreground">Situação</dt>
                  <dd>{{ pedido.situacao }}</dd>
                </template>
                <template v-if="enviadoEm">
                  <dt class="text-muted-foreground">Enviado</dt>
                  <dd>{{ fmtData(enviadoEm) }}</dd>
                </template>
                <template v-if="notaFiscal?.numero">
                  <dt class="text-muted-foreground">NF</dt>
                  <dd class="flex items-center gap-1 font-mono">
                    {{ notaFiscal.numero }}
                    <button type="button" class="rounded p-0.5 opacity-60 hover:bg-muted hover:opacity-100" title="copiar nº da NF" @click="copiarTexto(notaFiscal.numero, 'Nº da NF')"><Copy class="size-3" /></button>
                  </dd>
                </template>
              </dl>
              <ul v-if="pedido.itens?.length" class="divide-y rounded-md border text-xs">
                <li v-for="(it, i) in pedido.itens" :key="i" class="px-2 py-1.5">
                  <div class="line-clamp-2" :title="it.descricao || ''">{{ it.descricao || '—' }}</div>
                  <div class="mt-0.5 flex items-center gap-2 text-[11px] text-muted-foreground">
                    <span v-if="it.sku" class="font-mono">{{ it.sku }}</span>
                    <span v-if="it.quantidade !== null && it.quantidade !== undefined" class="ml-auto tabular-nums">× {{ it.quantidade }}</span>
                  </div>
                </li>
              </ul>
            </section>
            <section v-else-if="notaFiscal?.numero" class="text-xs">
              <span class="text-muted-foreground">NF</span> <span class="font-mono">{{ notaFiscal.numero }}</span>
            </section>

            <!-- entrega na Logística -->
            <section v-if="logistica || numeroMarketplace" class="space-y-1.5">
              <div class="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                <Truck class="size-3.5" /> Entrega (Logística)
                <NuxtLink v-if="temAbaLogistica" :to="linkLogistica" target="_blank" class="ml-auto inline-flex items-center gap-0.5 font-normal normal-case tracking-normal hover:text-foreground hover:underline" title="abrir em Logística (outra aba)">
                  abrir <ExternalLink class="size-3" />
                </NuxtLink>
              </div>
              <dl v-if="logistica" class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
                <template v-if="logistica.status">
                  <dt class="text-muted-foreground">Status</dt>
                  <dd>{{ logistica.status }}</dd>
                </template>
                <template v-if="logistica.rastreio">
                  <dt class="text-muted-foreground">Rastreio</dt>
                  <dd class="flex min-w-0 items-center gap-1 font-mono">
                    <span class="truncate" :title="logistica.rastreio">{{ logistica.rastreio }}</span>
                    <button type="button" class="shrink-0 rounded p-0.5 opacity-60 hover:bg-muted hover:opacity-100" title="copiar rastreio" @click="copiarTexto(logistica.rastreio, 'Rastreio')"><Copy class="size-3" /></button>
                  </dd>
                </template>
                <template v-if="logistica.transportadora">
                  <dt class="text-muted-foreground">Transportadora</dt>
                  <dd>{{ logistica.transportadora }}</dd>
                </template>
                <template v-if="logistica.previsao">
                  <dt class="text-muted-foreground">Previsão</dt>
                  <dd>{{ fmtData(logistica.previsao) }}</dd>
                </template>
                <template v-if="logistica.entregue_em">
                  <dt class="text-muted-foreground">Entregue</dt>
                  <dd class="text-emerald-700 dark:text-emerald-300">{{ fmtDataHora(logistica.entregue_em) }}</dd>
                </template>
              </dl>
              <div v-else class="text-[11px] text-muted-foreground">Sem rastreio no DaVinci ainda.</div>
            </section>

            <!-- chamados -->
            <section v-if="chamados.length" class="space-y-1.5">
              <div class="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                <MessagesSquare class="size-3.5" /> Chamados abertos
              </div>
              <ul class="space-y-1 text-xs">
                <li v-for="ch in chamados" :key="ch.id" class="rounded-md border px-2 py-1.5">
                  <div class="line-clamp-2">{{ ch.titulo || 'Chamado' }}</div>
                  <div class="mt-0.5 flex items-center gap-2 text-[11px] text-muted-foreground">
                    <span>{{ ch.status || '—' }}</span>
                    <NuxtLink :to="linkChamados" target="_blank" class="ml-auto inline-flex items-center gap-0.5 hover:text-foreground hover:underline">abrir <ExternalLink class="size-3" /></NuxtLink>
                  </div>
                </li>
              </ul>
              <p class="text-[11px] text-amber-800 dark:text-amber-300">Com chamado aberto, a IA não responde sozinha — combine a resposta com quem cuida do chamado.</p>
            </section>

            <!-- devoluções -->
            <section v-if="devolucoes.length" class="space-y-1.5">
              <div class="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                <Undo2 class="size-3.5" /> Devoluções
                <NuxtLink to="/devolucoes" target="_blank" class="ml-auto inline-flex items-center gap-0.5 font-normal normal-case tracking-normal hover:text-foreground hover:underline">abrir <ExternalLink class="size-3" /></NuxtLink>
              </div>
              <ul class="space-y-1 text-xs">
                <li v-for="dv in devolucoes" :key="dv.id" class="flex items-center gap-2 rounded-md border px-2 py-1.5">
                  <span class="font-mono text-[11px] text-muted-foreground">{{ String(dv.id).slice(0, 8) }}</span>
                  <span class="ml-auto">{{ dv.status || '—' }}</span>
                </li>
              </ul>
            </section>
          </div>
        </section>
      </div>

      <!-- ═══ PRODUTO ═══ -->
      <div v-else-if="aba === 'produto'" class="space-y-4 px-3 py-3">
        <section v-if="produto || conversa.anuncio_titulo" class="space-y-1.5">
          <h4 class="text-[13px] font-semibold">{{ conversa.canal === 'pergunta' ? 'Anúncio da pergunta' : 'Anúncio' }}</h4>
          <AtendimentoCartaoProduto v-if="produto" :cartao="produto" grande />
          <div v-else class="rounded-md border px-2.5 py-2 text-xs">
            {{ conversa.anuncio_titulo }}
            <span v-if="conversa.anuncio_id" class="font-mono text-muted-foreground">({{ conversa.anuncio_id }})</span>
          </div>
        </section>

        <!-- outras perguntas do mesmo comprador neste anúncio (ML) -->
        <section v-if="outrasPerguntas.length" class="space-y-1.5">
          <div class="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            <MessageCircleQuestion class="size-3.5" /> Outras perguntas dele neste anúncio
          </div>
          <ul class="space-y-1 text-xs">
            <li v-for="(p, i) in outrasPerguntas" :key="i" class="rounded-md border px-2 py-1.5">
              <div class="line-clamp-3 whitespace-pre-wrap break-words" :title="p.texto || ''">{{ p.texto || '—' }}</div>
              <div class="mt-0.5 flex items-center gap-2 text-[11px] text-muted-foreground">
                <span
                  class="rounded px-1 py-px"
                  :class="p.respondida ? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300' : 'bg-amber-500/20 text-amber-800 dark:text-amber-300'"
                >{{ statusPergunta(p) }}</span>
                <span v-if="p.data" class="ml-auto tabular-nums">{{ fmtDataHora(p.data) }}</span>
              </div>
            </li>
          </ul>
          <p class="text-[11px] text-muted-foreground">Cada pergunta é respondida na conversa dela, na fila.</p>
        </section>

        <section v-if="produtosChat.length" class="space-y-1.5">
          <h4 class="text-[13px] font-semibold">Mandados na conversa</h4>
          <AtendimentoCartaoProduto v-for="(p, i) in produtosChat" :key="p.item_id || i" :cartao="p" grande />
        </section>

        <section v-if="itensPm.length" class="space-y-1.5">
          <h4 class="text-[13px] font-semibold">Do pedido<span v-if="pm?.pedido" class="font-mono font-normal text-muted-foreground"> #{{ pm.pedido }}</span></h4>
          <AtendimentoCartaoProduto
            v-for="(it, i) in itensPm"
            :key="i"
            :cartao="{ item_id: it.sku || null, titulo: [it.titulo, it.variacao].filter(Boolean).join(' — ') || null, imagem: it.imagem, preco: it.preco ?? null, preco_original: it.preco_original ?? null, moeda: moeda, link: null }"
            grande
          />
        </section>

        <div v-if="!produto && !conversa.anuncio_titulo && !outrasPerguntas.length && !produtosChat.length && !itensPm.length" class="rounded-md border border-dashed px-3 py-6 text-center text-xs text-muted-foreground">
          <Package class="mx-auto mb-1.5 size-6 opacity-60" />
          Nenhum produto nesta conversa ainda.
        </div>
      </div>

      <!-- ═══ CUPOM (em breve) ═══ -->
      <div v-else class="px-3 py-3">
        <div class="space-y-2 rounded-md border border-dashed px-3 py-8 text-center text-xs text-muted-foreground">
          <Ticket class="mx-auto size-7 opacity-60" />
          <div class="text-sm font-medium text-foreground">Cupons — em breve</div>
          <p>Aqui vão aparecer os cupons ativos da loja (valor, código, consumo mínimo e validade) para mandar ao comprador, como no Duoke.</p>
          <p>Ainda não liga com a plataforma.</p>
        </div>
      </div>
    </div>
  </div>
</template>
