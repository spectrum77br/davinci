<script setup lang="ts">
import {
  AlertCircle,
  ArrowLeftRight,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  History,
  ImagePlus,
  Loader2,
  Plus,
  RotateCcw,
  Scale,
  Search,
  Send,
  Trash2,
  Undo2,
  X,
} from 'lucide-vue-next'

definePageMeta({ middleware: ['permission'], permission: { resource: 'chamados', action: 'view' } })

// Aba "Chamados" (Pós-venda): centraliza os chamados abertos nas plataformas,
// no formato da planilha — Data | pedido bling | pedido marketplace |
// plataforma | produto | sku | conta | status bling | origem | chamado |
// status (+ quem falou por último) | observação | alterar status bling | valor.
// O sim/não "monitoramento" saiu em 15/09 (Eduardo): o robô acompanha TODO
// chamado de API do ML e fecha sozinho quando o claim encerra — nada a marcar.
// 17/09 (Vinicius): o grupo "Réplica" (botão responder + réplica automática)
// saiu da tabela — a réplica automática nunca foi ligada e a resposta manual
// continua dentro do histórico. No lugar: Status (o que a plataforma diz do
// chamado, e desde quando) e Últ. resposta (quando e quem falou por último).

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
  { value: 'api', label: 'API ML', hint: 'mediação do Mercado Livre via API' },
  { value: 'robo', label: 'robô', hint: 'formulário/protocolo — fila do robô' },
]
// Ao fechar: Logística → Resolvido ou Perdimento (célula M2 da planilha).
const FECHAMENTO: Partial<Record<Origem, string[]>> = { logistica: ['Resolvido', 'Perdimento'] }
// Coluna Status (17/09): código que a API manda → rótulo e cor. A ordem é a do filtro.
const STATUS_ABA: { value: string; label: string; cls: string; hint: string }[] = [
  { value: 'respondeu', label: 'Plataforma respondeu', cls: 'bg-orange-500/15 text-orange-700 dark:text-orange-300', hint: 'a plataforma falou por último — estamos devendo resposta (o robô analisa)' },
  { value: 'humano', label: 'Precisa de humano', cls: 'bg-red-500/15 text-red-700 dark:text-red-300', hint: 'o robô não soube o que fazer com a última resposta da plataforma' },
  { value: 'prova', label: 'Pediu prova', cls: 'bg-orange-500/15 text-orange-700 dark:text-orange-300', hint: 'a Shopee pediu evidência extra na disputa' },
  { value: 'aguardando', label: 'Aguardando plataforma', cls: 'bg-sky-500/15 text-sky-700 dark:text-sky-300', hint: 'nós falamos por último — a bola está com a plataforma' },
  { value: 'em_analise', label: 'Em análise', cls: 'bg-violet-500/15 text-violet-700 dark:text-violet-300', hint: 'disputa/mediação em julgamento pela plataforma' },
  { value: 'reembolso_pago', label: 'Reembolso pago', cls: 'bg-amber-500/15 text-amber-700 dark:text-amber-300', hint: 'a Shopee reembolsou o comprador; se a compensação à loja não vier em 24 h, conta como perdido' },
  { value: 'fila', label: 'Na fila do robô', cls: 'bg-muted text-muted-foreground', hint: 'abertura/réplica ainda não saiu' },
  { value: 'falhou', label: 'Envio falhou', cls: 'bg-red-500/15 text-red-700 dark:text-red-300', hint: 'o último envio à plataforma falhou — ver histórico' },
  { value: 'sem_acompanhamento', label: 'Sem acompanhamento', cls: 'bg-muted text-muted-foreground', hint: 'registrado à mão — o DaVinci não consulta essa plataforma' },
  { value: 'ganhamos', label: 'Ganhamos', cls: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300', hint: 'a plataforma decidiu a favor da loja' },
  { value: 'perdemos', label: 'Perdemos', cls: 'bg-red-500/15 text-red-700 dark:text-red-300', hint: 'a plataforma decidiu a favor do comprador' },
  { value: 'encerrado', label: 'Encerrado', cls: 'bg-muted text-muted-foreground', hint: 'chamado resolvido' },
]
const STATUS_POR_CODIGO = Object.fromEntries(STATUS_ABA.map((s) => [s.value, s]))
// A 1ª linha do Status só leva data quando ele é OFICIAL da plataforma e a data não é a
// da última fala (Vinicius 17/09: no "precisa de humano" a hora do robô desistir não ajuda).
const STATUS_COM_DATA = new Set(['em_analise', 'prova', 'reembolso_pago', 'ganhamos', 'perdemos', 'encerrado'])
function mostraDataStatus(row: ChamadoRow): boolean {
  return !!row.status_aba_at && STATUS_COM_DATA.has(row.status_aba || '') && row.status_aba_at !== row.ultima_resposta_at
}
function statusInfo(row: ChamadoRow) {
  return STATUS_POR_CODIGO[row.status_aba || ''] || { value: row.status_aba || '', label: row.status_aba || '—', cls: 'bg-muted text-muted-foreground', hint: '' }
}
// Quem falou por último, pra coluna Últ. resposta: "nós · robô" / "plataforma · Shopee".
function quemRespondeu(row: ChamadoRow): string {
  const autor = (row.ultima_resposta_autor || '').trim()
  if (row.ultima_resposta_direcao === 'recebida') {
    const plat = autor && autor !== 'monitor' ? autor : (row.plataforma || 'plataforma').toUpperCase()
    return `plataforma · ${plat}`
  }
  const nos = autor === 'cérebro' || autor.startsWith('robô') ? 'robô' : autor === 'sistema' ? 'automático' : autor || 'nós'
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
}
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
  canal: Canal
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
  // Última FALA real (nossa ou da plataforma) — coluna Últ. resposta.
  ultima_resposta_at: string | null
  ultima_resposta_direcao: 'enviada' | 'recebida' | null
  ultima_resposta_autor: string | null
  anexos_auto: Anexo[]
}
type Page = { items: ChamadoRow[]; total: number; limit: number; offset: number; plataformas: string[]; contas: string[] }
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

const PAGE_SIZE = 100

const { api } = useApi()
const toasts = useToasts()
const canEdit = useCan('chamados', 'edit')
const canDelete = useCan('chamados', 'delete')

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
// Aba Jurídico (Eduardo 04/09): tudo que foi encaminhado ao jurídico, aberto ou resolvido.
const tab = ref<'chamados' | 'juridico'>('chamados')

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
const visiveis = computed(() => statusFilter.value === 'all' ? items.value : items.value.filter((r) => r.status_aba === statusFilter.value))
const rangeStart = computed(() => (total.value === 0 ? 0 : (page.value - 1) * PAGE_SIZE + 1))
const rangeEnd = computed(() => Math.min(page.value * PAGE_SIZE, total.value))

const sheetInputClass = 'h-7 w-full rounded-none border-0 bg-transparent px-1 text-xs focus:bg-background focus:outline-none focus:ring-1 focus:ring-primary disabled:cursor-default disabled:opacity-70'
const sheetSelectClass = `${sheetInputClass} cursor-pointer`

const ERROS: Record<string, string> = {
  chamado_not_found: 'chamado não encontrado',
  chamado_pedido_nao_encontrado: 'pedido não encontrado no DaVinci',
  chamado_mensagem_vazia: 'digite a mensagem',
  chamado_anexo_tipo_invalido: 'anexo precisa ser imagem (png/jpg/webp/gif)',
  chamado_anexo_muito_grande: 'imagem acima de 8 MB',
  chamado_sem_numero: 'informe o nº do chamado antes de enviar pela API',
  chamado_nao_ml: 'canal API só vale pra pedidos do Mercado Livre',
  chamado_sem_integracao_ml: 'conta sem integração ML no DaVinci',
  chamado_encerrado: 'o chamado já está encerrado no Mercado Livre',
  chamado_sem_acao: 'o ML não permite mensagem nesse chamado agora',
  chamado_sem_pedido_bling: 'linha sem pedido Bling',
  chamado_pedido_bling_nao_achado: 'pedido Bling não encontrado no DaVinci',
  chamado_status_bling_desconhecido: 'situação desconhecida no Bling',
  chamado_sem_integracao_bling: 'sem integração Bling',
  chamado_status_bling_erro: 'o Bling recusou a mudança de situação',
  chamado_valor_obrigatorio: 'informe o valor do chamado (lucro ou prejuízo) antes de resolver',
  sem_destinatarios: 'cadastre os destinatários do jurídico (botão destinatários)',
  threema_nao_configurado: 'Threema não configurado no servidor',
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
  plataforma_sem_api_replica: 'Shopee/TikTok não têm API de resposta na disputa — ficou só no histórico; responda pelo Seller Center',
  plataforma_sem_api: 'plataforma sem API — abrir na mão',
  chamado_sem_integracao_tiktok: 'conta sem integração TikTok no DaVinci',
  chamado_sem_integracao_shopee: 'conta sem integração Shopee no DaVinci',
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
  lado: 'nos' | 'eles' | 'sistema'
  autor: string
  quando: string
  texto: string
  meta: string
  status: string | null
  erro: string | null
  anexos: Anexo[]
}

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
    total.value = res.total
    plataformas.value = res.plataformas
    contas.value = res.contas || []
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

await Promise.all([load(), loadSituacoes()])

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

watch(tab, () => { page.value = 1; load() })

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

async function removerChamado(row: ChamadoRow) {
  if (!confirm(`Apagar o chamado do pedido ${row.pedido_bling || row.pedido_marketplace}? O histórico vai junto.`)) return
  setBusy(row.id, true)
  try {
    await api(`/api/chamados/${row.id}`, { method: 'DELETE' })
    items.value = items.value.filter((r) => r.id !== row.id)
    total.value = Math.max(0, total.value - 1)
  } catch (e: any) {
    toasts.error('Erro ao apagar', apiError(e))
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
    if (m.direcao === 'sistema') {
      itens.push({
        t, ord: ord++,
        b: {
          chave: m.id, lado: 'sistema', autor: m.autor_nome || 'sistema',
          quando: fmtDateTime(m.created_at), texto: m.texto, meta: 'sistema',
          status: null, erro: null, anexos: m.anexos,
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
        },
      })
      continue
    }
    falas.forEach((f, i) => pushFala(m, f, i, i === falas.length - 1))
  }
  itens.sort((a, b) => a.t - b.t || a.ord - b.ord)
  return itens.map(x => x.b)
})

async function openHistorico(row: ChamadoRow, focoReplica = false) {
  hist.open = true
  hist.row = row
  hist.texto = ''
  hist.files = []
  hist.erro = null
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
    await nextTick()
    ;(document.getElementById('replica-texto') as HTMLTextAreaElement | null)?.focus()
  }
}

function closeHistorico() {
  hist.open = false
  hist.row = null
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
    if (!row.resolvido && (row.status_aba === 'respondeu' || row.status_aba === 'humano' || row.status_aba === 'aguardando' || row.status_aba === 'fila' || row.status_aba === 'falhou' || row.status_aba === 'sem_acompanhamento')) {
      row.status_aba = m.status === 'pendente' ? 'fila' : m.status === 'falhou' ? 'falhou' : 'aguardando'
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
  saving: false,
  erro: null as string | null,
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

function opcoesFechamento(row: ChamadoRow): string[] {
  return FECHAMENTO[row.origem] || []
}

function openResolver(row: ChamadoRow) {
  resolver.open = true
  resolver.row = row
  resolver.situacao = ''
  resolver.erro = null
  // Pré-preenche com o que já está na coluna Valor (negativo = prejuízo).
  const atual = row.valor_recuperado === null || row.valor_recuperado === undefined ? NaN : Number(row.valor_recuperado)
  resolver.tipo = !Number.isNaN(atual) && atual < 0 ? 'prejuizo' : 'lucro'
  resolver.valor = Number.isNaN(atual) ? '' : String(Math.abs(atual))
  nextTick(() => (document.getElementById('resolver-valor') as HTMLInputElement | null)?.focus())
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
  resolver.saving = true
  resolver.erro = null
  try {
    const updated = await api<ChamadoRow>(`/api/chamados/${row.id}/resolver`, {
      method: 'POST',
      body: { resolvido: true, situacao: resolver.situacao || null, valor_recuperado: valor },
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
    closeResolver()
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
</script>

<template>
  <div class="space-y-5">
    <PageHeader title="Chamados" description="Todos os chamados abertos nas plataformas — Margem, Logística, Devolução e Vendas — num lugar só.">
      <template #actions>
        <Button size="sm" variant="outline" :disabled="loading" @click="load">
          <RotateCcw class="size-4 mr-1.5" :class="{ 'animate-spin': loading }" />
          atualizar
        </Button>
        <Button size="sm" :disabled="!canEdit" @click="openAdd">
          <Plus class="size-4 mr-1.5" />
          novo chamado
        </Button>
      </template>
    </PageHeader>

    <div class="flex gap-1 border-b">
      <button type="button" class="px-3 py-2 text-sm border-b-2 -mb-px" :class="tab === 'chamados' ? 'border-primary font-medium' : 'border-transparent text-muted-foreground hover:text-foreground'" @click="tab = 'chamados'">Chamados</button>
      <button type="button" class="px-3 py-2 text-sm border-b-2 -mb-px inline-flex items-center gap-1.5" :class="tab === 'juridico' ? 'border-primary font-medium' : 'border-transparent text-muted-foreground hover:text-foreground'" @click="tab = 'juridico'">
        <Scale class="size-4" /> Jurídico
      </button>
      <div v-if="tab === 'juridico'" class="ml-auto flex items-center gap-2 pb-1">
        <span class="text-xs text-muted-foreground">Tudo que foi encaminhado ao jurídico (abertos e resolvidos).</span>
        <Button v-if="isAdmin" size="sm" variant="outline" @click="juridicoCfgOpen = true">destinatários</Button>
      </div>
    </div>

    <div v-if="error" class="flex items-center gap-2 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-400">
      <AlertCircle class="size-4" />
      {{ error }}
    </div>

    <!-- novo chamado -->
    <div v-if="addOpen" class="rounded-md border bg-background">
      <div class="flex flex-wrap items-end gap-3 border-b px-3 py-3">
        <label class="space-y-1">
          <span class="text-[11px] font-medium text-muted-foreground">Pedido</span>
          <div class="relative">
            <Search class="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
            <input
              v-model="lookupPedido"
              class="h-9 w-72 rounded-md border bg-background pl-8 pr-3 text-sm"
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
              <td class="px-2 py-1 uppercase">{{ draft.plataforma || '—' }}</td>
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

    <!-- filtros -->
    <div class="flex flex-wrap items-center gap-2">
      <div class="relative">
        <Search class="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
        <input v-model="search" class="h-9 w-72 rounded-md border bg-background pl-8 pr-3 text-sm" placeholder="buscar pedido, conta, produto, chamado…" />
      </div>
      <select v-model="origemFilter" class="h-9 rounded-md border bg-background px-2 text-sm">
        <option value="all">todas origens</option>
        <option v-for="o in ORIGENS" :key="o.value" :value="o.value">{{ o.label }}</option>
      </select>
      <select v-model="plataformaFilter" class="h-9 rounded-md border bg-background px-2 text-sm">
        <option value="all">todas plataformas</option>
        <option v-for="p in plataformas" :key="p" :value="p">{{ p }}</option>
      </select>
      <select v-model="contaFilter" class="h-9 rounded-md border bg-background px-2 text-sm" title="filtrar por conta (ex. ML Aguiar 2)">
        <option value="all">todas contas</option>
        <option v-for="c in contas" :key="c" :value="c">{{ c }}</option>
      </select>
      <select v-model="mostrar" class="h-9 rounded-md border bg-background px-2 text-sm">
        <option value="abertos">abertos</option>
        <option value="resolvidos">encerrados</option>
        <option value="todos">todos</option>
      </select>
      <select v-model="statusFilter" class="h-9 rounded-md border bg-background px-2 text-sm" title="filtrar pela coluna Status">
        <option value="all">todos status</option>
        <option v-for="s in STATUS_ABA" :key="s.value" :value="s.value">{{ s.label }}</option>
      </select>
      <span class="ml-auto text-xs text-muted-foreground">{{ rangeStart }}–{{ rangeEnd }} de {{ total }}</span>
    </div>

    <!-- planilha -->
    <div class="overflow-auto rounded border max-h-[75vh] focus:outline-none" tabindex="0">
      <table class="min-w-[2000px] text-xs border-collapse">
        <thead class="sticky top-0 z-20 bg-background">
          <tr>
            <th class="px-2 py-1 text-left text-[11px] font-semibold border-b" colspan="8">Identificação</th>
            <th class="px-2 py-1 text-center text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-amber-50 dark:bg-amber-900/20" colspan="5">Chamado</th>
            <th class="px-2 py-1 text-left text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-violet-50 dark:bg-violet-900/20" colspan="1">Jurídico</th>
            <th class="px-2 py-1 text-center text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-emerald-50 dark:bg-emerald-900/20" colspan="1">Bling</th>
            <th class="px-2 py-1 text-center text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600" colspan="2">Controle</th>
          </tr>
          <tr class="border-b">
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[80px]">Data</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[100px]">Pedido Bling</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[150px]">Pedido Marketplace</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[90px]">Plataforma</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[220px]">Produto</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[120px]">SKU</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px]">Conta</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[150px]">Status Bling</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[100px] bg-amber-50 dark:bg-amber-900/20 border-l-[3px] border-gray-400 dark:border-gray-600">Origem</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[190px] bg-amber-50 dark:bg-amber-900/20">Chamado</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px] bg-amber-50 dark:bg-amber-900/20">Canal</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap w-[1%] bg-amber-50 dark:bg-amber-900/20" title="O que a plataforma diz do chamado (e desde quando) + quem falou por último">Status</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap w-[150px] min-w-[120px] max-w-[150px] bg-amber-50 dark:bg-amber-900/20">Observação</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[170px] bg-violet-50 dark:bg-violet-900/20 border-l-[3px] border-gray-400 dark:border-gray-600" title="Encaminhado ao jurídico: quando, por quem, observação e link do dossiê">Jurídico</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[210px] bg-emerald-50 dark:bg-emerald-900/20 border-l-[3px] border-gray-400 dark:border-gray-600">Alterar status Bling</th>
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px] border-l-[3px] border-gray-400 dark:border-gray-600" title="Resultado do chamado (R$): positivo = lucro, negativo = prejuízo">Valor</th>
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[120px]"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading && !items.length">
            <td colspan="17" class="py-8 text-center text-muted-foreground">
              <Loader2 class="size-4 inline animate-spin mr-1.5" />
              carregando…
            </td>
          </tr>
          <tr v-else-if="!visiveis.length">
            <td colspan="17" class="py-8 text-center text-muted-foreground">{{ items.length ? 'nenhum chamado com esse status nesta página' : 'sem chamados' }}</td>
          </tr>
          <tr v-for="row in visiveis" :key="row.id" class="border-t hover:brightness-95 dark:hover:brightness-110" :class="{ 'opacity-60': row.resolvido }">
            <td class="px-2 py-1 whitespace-nowrap text-muted-foreground">{{ fmtDate(row.data) }}</td>
            <td class="px-2 py-1 font-mono whitespace-nowrap">{{ row.pedido_bling || '—' }}</td>
            <td class="px-2 py-1 font-mono text-muted-foreground whitespace-nowrap">{{ row.pedido_marketplace || '—' }}</td>
            <td class="px-2 py-1 uppercase whitespace-nowrap">{{ row.plataforma || '—' }}</td>
            <td class="px-2 py-1 max-w-[320px] truncate" :title="row.produto || ''">{{ row.produto || '—' }}</td>
            <td class="px-2 py-1 font-mono whitespace-nowrap max-w-[180px] truncate" :title="row.sku || ''">{{ row.sku || '—' }}</td>
            <td class="px-2 py-1 whitespace-nowrap">{{ row.conta || '—' }}</td>
            <td class="px-2 py-1 whitespace-nowrap">
              <span>{{ row.status_bling_atual || row.status_bling || '—' }}</span>
            </td>
            <td class="px-2 py-1 whitespace-nowrap bg-amber-50/40 dark:bg-amber-900/10 border-l-[3px] border-gray-400 dark:border-gray-600">
              <span class="rounded px-1.5 py-0.5 text-[11px] font-medium" :class="{
                'bg-violet-500/15 text-violet-700 dark:text-violet-300': row.origem === 'margem',
                'bg-sky-500/15 text-sky-700 dark:text-sky-300': row.origem === 'logistica',
                'bg-orange-500/15 text-orange-700 dark:text-orange-300': row.origem === 'devolucao',
                'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300': row.origem === 'vendas',
              }">{{ origemLabel(row.origem) }}</span>
            </td>
            <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
              <div class="flex items-center gap-1">
                <input
                  :value="row.chamado || ''"
                  :disabled="!canEdit"
                  :class="sheetInputClass"
                  placeholder="protocolo / claim"
                  @input="(e) => setRowText(row, 'chamado', (e.target as HTMLInputElement).value)"
                  @change="saveRow(row)"
                />
                <button
                  type="button"
                  class="shrink-0 inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] hover:bg-muted"
                  title="histórico do chamado e resposta à plataforma"
                  @click="openHistorico(row)"
                >
                  <History class="size-3.5" />
                  {{ row.mensagens_total }}
                </button>
              </div>
            </td>
            <td class="px-1 py-0.5 bg-amber-50/40 dark:bg-amber-900/10">
              <select :value="row.canal" :disabled="!canEdit" :class="sheetSelectClass" @change="(e) => { row.canal = (e.target as HTMLSelectElement).value as Canal; saveRow(row) }">
                <option v-for="c in CANAIS" :key="c.value" :value="c.value" :title="c.hint">{{ c.label }}</option>
              </select>
            </td>
            <!-- Status (17/09, Vinicius "status e últ. resposta não seria a mesma coisa?"):
                 uma célula só — 1ª linha o status (com a data quando ela não é a da última
                 fala: oficial da API, robô pediu gente); 2ª linha quem falou por último. -->
            <td class="px-2 py-1 w-[1%] max-w-[210px] bg-amber-50/40 dark:bg-amber-900/10">
              <div class="space-y-0.5">
                <div class="flex flex-wrap items-center gap-x-1.5 gap-y-0.5">
                  <span class="inline-block rounded px-1.5 py-0.5 text-[11px] font-medium whitespace-nowrap" :class="statusInfo(row).cls" :title="statusInfo(row).hint">{{ statusInfo(row).label }}</span>
                  <span v-if="mostraDataStatus(row)" class="text-[11px] text-muted-foreground whitespace-nowrap">{{ fmtCurto(row.status_aba_at) }}</span>
                </div>
                <div v-if="row.ultima_resposta_at" class="text-[11px] whitespace-nowrap" :class="row.ultima_resposta_direcao === 'recebida' ? 'text-orange-600 dark:text-orange-400' : 'text-emerald-700 dark:text-emerald-300'" :title="`última resposta: ${fmtDateTime(row.ultima_resposta_at)} · ${quemRespondeu(row)}`">
                  <span v-if="mostraDataStatus(row)" class="text-muted-foreground">últ. </span>{{ fmtCurto(row.ultima_resposta_at) }} · {{ quemRespondeu(row) }}
                </div>
              </div>
            </td>
            <td class="px-1 py-0.5 w-[150px] max-w-[150px] bg-amber-50/40 dark:bg-amber-900/10">
              <input
                :value="row.observacao || ''"
                :title="row.observacao || ''"
                :disabled="!canEdit"
                :class="sheetInputClass"
                @input="(e) => setRowText(row, 'observacao', (e.target as HTMLInputElement).value)"
                @change="saveRow(row)"
              />
            </td>
            <td class="px-2 py-1 bg-violet-50/40 dark:bg-violet-900/10 border-l-[3px] border-gray-400 dark:border-gray-600">
              <div v-if="row.juridico_enviado_at" class="space-y-0.5 text-[11px]">
                <div class="whitespace-nowrap"><Scale class="inline size-3 mr-0.5 text-violet-600" />{{ fmtDateTime(row.juridico_enviado_at) }} · {{ row.juridico_enviado_por_nome || '—' }}</div>
                <div v-if="row.juridico_obs" class="max-w-[220px] truncate text-muted-foreground" :title="row.juridico_obs">{{ row.juridico_obs }}</div>
                <div class="flex items-center gap-2">
                  <a v-if="row.juridico_link" :href="row.juridico_link" target="_blank" rel="noopener" class="underline">dossiê</a>
                  <button v-if="row.juridico_link" type="button" class="underline text-muted-foreground" @click="copiarLink(row.juridico_link!)">copiar link</button>
                  <button v-if="canEdit" type="button" class="underline text-muted-foreground" @click="openJuridico(row)">reenviar</button>
                </div>
              </div>
              <Button v-else size="sm" variant="outline" class="h-7 px-2" :disabled="!canEdit" title="Encaminhar ao jurídico: aviso no Threema com o histórico e as fotos" @click="openJuridico(row)">
                <Scale class="size-3.5 mr-1" />
                jurídico
              </Button>
            </td>
            <td class="px-1 py-0.5 bg-emerald-50/40 dark:bg-emerald-900/10 border-l-[3px] border-gray-400 dark:border-gray-600">
              <div class="flex items-center gap-1">
                <select :value="row.alterar_status_bling || ''" :disabled="!canEdit" :class="sheetSelectClass" @change="(e) => { row.alterar_status_bling = (e.target as HTMLSelectElement).value || null; saveRow(row) }">
                  <option value="">— não altera</option>
                  <option v-for="s in situacoes" :key="s" :value="s">{{ s }}</option>
                </select>
                <button
                  type="button"
                  class="shrink-0 inline-flex items-center rounded border p-1 hover:bg-muted disabled:opacity-40"
                  :disabled="!canEdit || !row.alterar_status_bling || !row.pedido_bling || busy.has(row.id)"
                  title="aplicar no Bling agora"
                  @click="aplicarStatusBling(row)"
                >
                  <Loader2 v-if="busy.has(row.id)" class="size-3.5 animate-spin" />
                  <ArrowLeftRight v-else class="size-3.5" />
                </button>
              </div>
            </td>
            <!-- Resultado do chamado (R$): positivo = lucro, negativo = prejuízo — Eduardo 03/09 e 15/09 -->
            <td class="px-1 py-0.5 border-l-[3px] border-gray-400 dark:border-gray-600">
              <input
                :value="row.valor_recuperado ?? ''"
                :disabled="!canEdit"
                type="number"
                step="0.01"
                placeholder="R$"
                :class="[
                  sheetInputClass,
                  'text-right tabular-nums font-medium',
                  Number(row.valor_recuperado) > 0 ? 'text-emerald-700 dark:text-emerald-300' : Number(row.valor_recuperado) < 0 ? 'text-red-600 dark:text-red-400' : '',
                ]"
                :title="resultadoTexto(row.valor_recuperado) || 'Resultado do chamado (R$): positivo = lucro, negativo = prejuízo'"
                @input="(e) => { const v = (e.target as HTMLInputElement).value; row.valor_recuperado = v === '' ? null : Number(v) }"
                @change="saveRow(row)"
              />
            </td>
            <td class="px-2 py-1 text-right whitespace-nowrap">
              <div class="inline-flex items-center gap-1">
                <button
                  v-if="!row.resolvido"
                  type="button"
                  class="inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] hover:bg-muted disabled:opacity-40"
                  :disabled="!canEdit || busy.has(row.id)"
                  title="marcar como resolvido"
                  @click="openResolver(row)"
                >
                  <CheckCircle2 class="size-3.5" />
                  resolver
                </button>
                <button
                  v-else
                  type="button"
                  class="inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] hover:bg-muted disabled:opacity-40"
                  :disabled="!canEdit || busy.has(row.id)"
                  title="reabrir"
                  @click="reabrir(row)"
                >
                  <Undo2 class="size-3.5" />
                  reabrir
                </button>
                <button
                  v-if="canDelete"
                  type="button"
                  class="inline-flex items-center rounded border p-1 text-red-500 hover:bg-red-500/10 disabled:opacity-40"
                  :disabled="busy.has(row.id)"
                  title="apagar chamado"
                  @click="removerChamado(row)"
                >
                  <Trash2 class="size-3.5" />
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="total > PAGE_SIZE" class="flex items-center justify-between gap-2">
      <span class="text-xs text-muted-foreground">página {{ page }} de {{ totalPages }} · {{ PAGE_SIZE }}/página</span>
      <div class="flex items-center gap-1">
        <Button size="sm" variant="outline" :disabled="page <= 1 || loading" @click="page = 1">«</Button>
        <Button size="sm" variant="outline" :disabled="page <= 1 || loading" @click="page = page - 1"><ChevronLeft class="size-4" /></Button>
        <input v-model.number="page" type="number" :min="1" :max="totalPages" class="w-16 rounded-md border bg-background px-2 py-1 text-center text-sm" @change="page = Math.min(Math.max(1, page), totalPages)" />
        <Button size="sm" variant="outline" :disabled="page >= totalPages || loading" @click="page = page + 1"><ChevronRight class="size-4" /></Button>
        <Button size="sm" variant="outline" :disabled="page >= totalPages || loading" @click="page = totalPages">»</Button>
      </div>
    </div>

    <!-- modal: encaminhar ao jurídico -->
    <div v-if="juridico.open && juridico.row" class="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-4" @click.self="closeJuridico">
      <div class="w-full max-w-lg rounded-lg border bg-background p-5 shadow-xl space-y-4">
        <div class="flex items-start justify-between gap-3">
          <div>
            <div class="text-sm font-semibold inline-flex items-center gap-1.5"><Scale class="size-4 text-violet-600" /> Encaminhar ao jurídico</div>
            <div class="text-xs text-muted-foreground">
              Chamado {{ juridico.row.chamado || '(sem nº)' }} · pedido {{ juridico.row.pedido_bling || juridico.row.pedido_marketplace }} · {{ (juridico.row.plataforma || '').toUpperCase() }} {{ juridico.row.conta || '' }}
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
    <InformarThreemaModal :open="juridicoCfgOpen" contexto="juridico" somente-cadastro @close="juridicoCfgOpen = false; if (juridico.open && juridico.row) openJuridico(juridico.row)" />

    <!-- modal: histórico + réplica -->
    <div v-if="hist.open && hist.row" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" @click.self="closeHistorico">
      <div class="w-full max-w-3xl max-h-[90vh] flex flex-col rounded-lg border bg-background shadow-xl">
        <div class="flex items-start justify-between gap-3 border-b px-4 py-3">
          <div>
            <div class="text-sm font-semibold">
              Chamado {{ hist.row.chamado || '(sem nº)' }} · pedido {{ hist.row.pedido_bling || hist.row.pedido_marketplace }}
            </div>
            <div class="text-xs text-muted-foreground">
              {{ origemLabel(hist.row.origem) }} · {{ (hist.row.plataforma || '').toUpperCase() }} {{ hist.row.conta || '' }} · canal {{ hist.row.canal }}
              <a v-if="hist.row.chamado_url" :href="hist.row.chamado_url" target="_blank" rel="noopener" class="ml-1 underline">abrir na plataforma</a>
            </div>
          </div>
          <div class="flex items-center gap-2">
            <Button size="sm" variant="outline" class="h-7 px-2" :disabled="!canEdit" title="Encaminhar ao jurídico (Threema + dossiê com fotos)" @click="openJuridico(hist.row)">
              <Scale class="size-3.5 mr-1" />
              {{ hist.row.juridico_enviado_at ? 'reenviar ao jurídico' : 'encaminhar ao jurídico' }}
            </Button>
            <button type="button" class="rounded p-1 hover:bg-muted" @click="closeHistorico"><X class="size-4" /></button>
          </div>
        </div>

        <div class="flex-1 overflow-auto px-4 py-3 space-y-3">
          <div v-if="hist.loading" class="text-sm text-muted-foreground"><Loader2 class="size-4 inline animate-spin mr-1.5" />carregando histórico…</div>
          <div v-else-if="!hist.mensagens.length" class="text-sm text-muted-foreground">sem mensagens ainda</div>
          <!-- Conversa em balões (Eduardo 10/09: "como se fosse um whatsapp"):
               nossas falas à direita, plataforma à esquerda, sistema no meio. -->
          <div
            v-for="b in bolhas"
            :key="b.chave"
            class="flex"
            :class="{
              'justify-end': b.lado === 'nos',
              'justify-start': b.lado === 'eles',
              'justify-center': b.lado === 'sistema',
            }"
          >
            <div
              v-if="b.lado === 'sistema'"
              class="rounded-full bg-muted px-3 py-1 text-[11px] italic text-muted-foreground"
              :title="b.quando"
            >{{ b.texto }}</div>

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
              <div class="mt-1 whitespace-pre-wrap break-words text-sm leading-relaxed">{{ b.texto }}</div>
              <div v-if="b.anexos.length" class="mt-2 flex flex-wrap gap-2">
                <a v-for="a in b.anexos" :key="a.id" :href="anexoUrl(a.id)" target="_blank" rel="noopener" :title="a.filename">
                  <img :src="anexoUrl(a.id)" :alt="a.filename" class="h-20 w-20 rounded border object-cover" />
                </a>
              </div>
              <div class="mt-1 text-right text-[10px] text-muted-foreground">{{ b.quando }}</div>
            </div>
          </div>
        </div>

        <div v-if="canEdit && !hist.row.resolvido" class="border-t px-4 py-3 space-y-2">
          <div class="text-xs font-medium">Réplica manual <span class="text-muted-foreground font-normal">— resposta à plataforma pelo canal <b>{{ hist.row.canal }}</b></span></div>
          <textarea id="replica-texto" v-model="hist.texto" rows="4" class="w-full rounded-md border bg-background px-3 py-2 text-sm" placeholder="digite a mensagem…" />
          <div class="flex flex-wrap items-center gap-2">
            <label class="inline-flex cursor-pointer items-center gap-1 rounded border px-2 py-1 text-xs hover:bg-muted">
              <ImagePlus class="size-3.5" />
              anexar foto
              <input type="file" accept="image/png,image/jpeg,image/webp,image/gif" multiple class="hidden" @change="onReplicaFiles" />
            </label>
            <span v-for="(f, i) in hist.files" :key="`${f.name}-${i}`" class="inline-flex items-center gap-1 rounded bg-muted px-2 py-0.5 text-[11px]">
              {{ f.name }}
              <button type="button" class="hover:text-red-500" @click="removeReplicaFile(i)"><X class="size-3" /></button>
            </span>
            <span v-if="hist.erro" class="text-xs text-red-500">{{ hist.erro }}</span>
            <Button size="sm" class="ml-auto" :disabled="hist.sending || !hist.texto.trim()" @click="enviarReplica">
              <Loader2 v-if="hist.sending" class="size-4 mr-1.5 animate-spin" />
              <Send v-else class="size-4 mr-1.5" />
              {{ hist.row.canal === 'manual' ? 'registrar' : hist.row.canal === 'robo' ? 'enfileirar pro robô' : 'enviar' }}
            </Button>
          </div>
        </div>
      </div>
    </div>

    <!-- modal: resolver -->
    <div v-if="resolver.open && resolver.row" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" @click.self="closeResolver">
      <div class="w-full max-w-md rounded-lg border bg-background shadow-xl">
        <div class="flex items-start justify-between gap-3 border-b px-4 py-3">
          <div class="text-sm font-semibold">Resolver chamado · pedido {{ resolver.row.pedido_bling || resolver.row.pedido_marketplace }}</div>
          <button type="button" class="rounded p-1 hover:bg-muted" @click="closeResolver"><X class="size-4" /></button>
        </div>
        <div class="space-y-3 px-4 py-3 text-sm">
          <p class="text-muted-foreground">O chamado sai da lista de abertos.</p>
          <label class="block space-y-1">
            <span class="text-xs font-medium">Situação no Bling ao fechar</span>
            <select v-model="resolver.situacao" class="h-9 w-full rounded-md border bg-background px-2 text-sm">
              <option value="">— não alterar</option>
              <template v-if="opcoesFechamento(resolver.row).length">
                <option v-for="s in opcoesFechamento(resolver.row)" :key="s" :value="s">{{ s }}</option>
                <option disabled>──────</option>
              </template>
              <option v-for="s in situacoes.filter((x) => !opcoesFechamento(resolver.row!).includes(x))" :key="s" :value="s">{{ s }}</option>
            </select>
            <span class="text-[11px] text-muted-foreground">Logística → Resolvido ou Perdimento · Margem → não altera · Devolução → sem padrão</span>
          </label>
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
            <span class="text-[11px] text-muted-foreground">
              Obrigatório. Ganhamos R$ 100 → <b>lucro</b> 100,00 · perdemos R$ 50 → <b>prejuízo</b> 50,00. Vai pra coluna Valor (prejuízo fica negativo).
            </span>
          </div>
          <div v-if="resolver.erro" class="text-xs text-red-500">{{ resolver.erro }}</div>
        </div>
        <div class="flex items-center justify-end gap-2 border-t px-4 py-3">
          <Button size="sm" variant="ghost" @click="closeResolver">cancelar</Button>
          <Button size="sm" :disabled="!canEdit || resolver.saving || !resolverValorOk" @click="confirmarResolver">
            <Loader2 v-if="resolver.saving" class="size-4 mr-1.5 animate-spin" />
            <CheckCircle2 v-else class="size-4 mr-1.5" />
            resolver
          </Button>
        </div>
      </div>
    </div>
  </div>
</template>
