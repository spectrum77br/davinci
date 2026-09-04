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
  MessageSquareReply,
  Plus,
  RotateCcw,
  Scale,
  Search,
  Send,
  Settings2,
  Trash2,
  Undo2,
  X,
} from 'lucide-vue-next'

definePageMeta({ middleware: ['permission'], permission: { resource: 'chamados', action: 'view' } })

// Aba "Chamados" (Pós-venda): centraliza os chamados abertos nas plataformas,
// no formato da planilha — Data | pedido bling | pedido marketplace |
// plataforma | produto | sku | conta | status bling | origem | chamado |
// réplica | réplica automática | alterar status bling | monitoramento.

type Origem = 'margem' | 'logistica' | 'devolucao'
type Canal = 'api' | 'robo' | 'manual'

const ORIGENS: { value: Origem; label: string }[] = [
  { value: 'margem', label: 'Margem' },
  { value: 'logistica', label: 'Logística' },
  { value: 'devolucao', label: 'Devolução' },
]
const CANAIS: { value: Canal; label: string; hint: string }[] = [
  { value: 'manual', label: 'manual', hint: 'só registra no histórico' },
  { value: 'api', label: 'API ML', hint: 'mediação do Mercado Livre via API' },
  { value: 'robo', label: 'robô', hint: 'formulário/protocolo — fila do robô' },
]
// Ao fechar: Logística → Resolvido ou Perdimento (célula M2 da planilha).
const FECHAMENTO: Partial<Record<Origem, string[]>> = { logistica: ['Resolvido', 'Perdimento'] }

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
  monitoramento: boolean
  // Valor recuperado com o chamado (R$) — coluna "Valor" do Controle.
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
  anexos_auto: Anexo[]
}
type Page = { items: ChamadoRow[]; total: number; limit: number; offset: number; plataformas: string[] }
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
const situacoes = ref<string[]>([])
const page = ref(1)
const loading = ref(false)
const error = ref<string | null>(null)

const search = ref('')
const origemFilter = ref<'all' | Origem>('all')
const plataformaFilter = ref<'all' | string>('all')
const mostrar = ref<'abertos' | 'resolvidos' | 'todos'>('abertos')
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
  sem_destinatarios: 'cadastre os destinatários do jurídico (botão destinatários)',
  threema_nao_configurado: 'Threema não configurado no servidor',
  threema_envio_falhou: 'o Threema não entregou pra nenhum destinatário',
  // abertura automática de devolução no ML (services/chamados_devolucao)
  devolucao_sem_foto: 'aguardando foto na tela Devoluções',
  return_review_indisponivel: 'ML ainda não liberou a revisão da devolução (tenta a cada hora)',
  devolucao_sem_claim: 'sem devolução aberta no ML pra esse pedido (tenta a cada hora)',
  devolucao_sem_return: 'sem devolução aberta no ML pra esse pedido (tenta a cada hora)',
  devolucao_sem_pedido_marketplace: 'devolução sem nº do pedido do ML',
  devolucao_prazo_esgotado: 'ficou 45 dias pendente — abrir na mão',
  devolucao_nao_encontrada: 'lançamento da devolução não encontrado',
  tiktok_aguardando_pacote: 'TikTok ainda não liberou a recusa do pacote (tenta a cada hora)',
  tiktok_arbitragem: 'em arbitragem na TikTok',
  tiktok_quick_refund: 'TikTok já reembolsou (quick refund) — só apelação no Seller Center',
  tiktok_ja_recusada: 'já recusada na TikTok',
  tiktok_motivo_indisponivel: 'TikTok não aceita esse motivo nesse estado',
  shopee_aguardando_pacote: 'Shopee ainda não liberou a disputa (tenta a cada hora)',
  shopee_ja_contestada: 'já contestada na Shopee',
  shopee_devolucao_encerrada: 'devolução já encerrada na Shopee',
  shopee_motivo_indisponivel: 'Shopee não oferece esse motivo pra essa devolução',
  shopee_sem_email: 'sem e-mail do operador pra Shopee (DEVOLUCAO_DISPUTE_EMAIL)',
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

function fmtDateTime(v: string | null) {
  if (!v) return '—'
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return v
  return d.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' })
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
    const res = await api<Page>(`/api/chamados?${params.toString()}`)
    items.value = res.items
    total.value = res.total
    plataformas.value = res.plataformas
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
watch([origemFilter, plataformaFilter, mostrar], () => {
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
    if (hist.open) await loadMensagens()
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
    lookupError.value = 'Selecione a origem (Margem / Logística / Devolução).'
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
            monitoramento: row.monitoramento,
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

// ----------------------------------------------------------------- réplica automática

const auto = reactive({
  open: false,
  row: null as ChamadoRow | null,
  ligada: false,
  dias: 2 as number | null,
  mensagem: '',
  saving: false,
  uploading: false,
  erro: null as string | null,
})

function openAuto(row: ChamadoRow) {
  auto.open = true
  auto.row = row
  auto.ligada = row.auto_ligada
  auto.dias = row.auto_dias ?? 2
  auto.mensagem = row.auto_mensagem || ''
  auto.erro = null
}

function closeAuto() {
  auto.open = false
  auto.row = null
}

async function salvarAuto() {
  const row = auto.row
  if (!row || !canEdit.value) return
  if (auto.ligada && (!auto.dias || auto.dias < 1)) {
    auto.erro = 'informe a frequência em dias (mínimo 1)'
    return
  }
  if (auto.ligada && !auto.mensagem.trim()) {
    auto.erro = 'digite a mensagem que será reenviada'
    return
  }
  auto.saving = true
  auto.erro = null
  try {
    const updated = await api<ChamadoRow>(`/api/chamados/${row.id}`, {
      method: 'PATCH',
      body: {
        auto_ligada: auto.ligada,
        auto_dias: auto.dias || null,
        auto_mensagem: auto.mensagem.trim() || null,
      },
    })
    replaceRow(updated)
    auto.row = updated
    toasts.success(auto.ligada ? 'Réplica automática ligada' : 'Réplica automática desligada', auto.ligada ? `A cada ${auto.dias} dia(s).` : '')
    closeAuto()
  } catch (e: any) {
    auto.erro = apiError(e)
  } finally {
    auto.saving = false
  }
}

async function uploadAutoAnexo(ev: Event) {
  const row = auto.row
  const input = ev.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!row || !file) return
  auto.uploading = true
  auto.erro = null
  try {
    const fd = new FormData()
    fd.append('file', file)
    const a = await api<Anexo>(`/api/chamados/${row.id}/anexos-auto`, { method: 'POST', body: fd })
    row.anexos_auto = [...(row.anexos_auto || []), a]
  } catch (e: any) {
    auto.erro = apiError(e)
  } finally {
    auto.uploading = false
  }
}

async function removeAutoAnexo(a: Anexo) {
  const row = auto.row
  if (!row || !confirm('Remover esta imagem da réplica automática?')) return
  try {
    await api(`/api/chamados/anexos/${a.id}`, { method: 'DELETE' })
    row.anexos_auto = row.anexos_auto.filter((x) => x.id !== a.id)
  } catch (e: any) {
    auto.erro = apiError(e)
  }
}

// ----------------------------------------------------------------- resolver

const resolver = reactive({
  open: false,
  row: null as ChamadoRow | null,
  situacao: '' as string,
  saving: false,
  erro: null as string | null,
})

function opcoesFechamento(row: ChamadoRow): string[] {
  return FECHAMENTO[row.origem] || []
}

function openResolver(row: ChamadoRow) {
  resolver.open = true
  resolver.row = row
  resolver.situacao = ''
  resolver.erro = null
}

function closeResolver() {
  resolver.open = false
  resolver.row = null
}

async function confirmarResolver() {
  const row = resolver.row
  if (!row || !canEdit.value) return
  resolver.saving = true
  resolver.erro = null
  try {
    const updated = await api<ChamadoRow>(`/api/chamados/${row.id}/resolver`, {
      method: 'POST',
      body: { resolvido: true, situacao: resolver.situacao || null },
    })
    if (mostrar.value === 'abertos') {
      items.value = items.value.filter((r) => r.id !== row.id)
      total.value = Math.max(0, total.value - 1)
    } else {
      replaceRow(updated)
    }
    toasts.success('Chamado resolvido', resolver.situacao ? `Bling → ${resolver.situacao}` : '')
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
    <PageHeader title="Chamados" description="Todos os chamados abertos nas plataformas — Margem, Logística e Devolução — num lugar só.">
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
      <select v-model="mostrar" class="h-9 rounded-md border bg-background px-2 text-sm">
        <option value="abertos">abertos</option>
        <option value="resolvidos">resolvidos</option>
        <option value="todos">todos</option>
      </select>
      <span class="ml-auto text-xs text-muted-foreground">{{ rangeStart }}–{{ rangeEnd }} de {{ total }}</span>
    </div>

    <!-- planilha -->
    <div class="overflow-auto rounded border max-h-[75vh] focus:outline-none" tabindex="0">
      <table class="min-w-[2100px] text-xs border-collapse">
        <thead class="sticky top-0 z-20 bg-background">
          <tr>
            <th class="px-2 py-1 text-left text-[11px] font-semibold border-b" colspan="8">Identificação</th>
            <th class="px-2 py-1 text-center text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-amber-50 dark:bg-amber-900/20" colspan="3">Chamado</th>
            <th class="px-2 py-1 text-center text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-sky-50 dark:bg-sky-900/20" colspan="2">Réplica</th>
            <th class="px-2 py-1 text-left text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-violet-50 dark:bg-violet-900/20" colspan="1">Jurídico</th>
            <th class="px-2 py-1 text-center text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-emerald-50 dark:bg-emerald-900/20" colspan="2">Bling</th>
            <th class="px-2 py-1 text-center text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600" colspan="3">Controle</th>
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
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[190px] bg-sky-50 dark:bg-sky-900/20 border-l-[3px] border-gray-400 dark:border-gray-600">Réplica</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[170px] bg-violet-50 dark:bg-violet-900/20 border-l-[3px] border-gray-400 dark:border-gray-600" title="Encaminhado ao jurídico: quando, por quem, observação e link do dossiê">Jurídico</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[220px] bg-sky-50 dark:bg-sky-900/20">Réplica automática</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[210px] bg-emerald-50 dark:bg-emerald-900/20 border-l-[3px] border-gray-400 dark:border-gray-600">Alterar status Bling</th>
            <th class="px-2 py-1 text-center font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px] bg-emerald-50 dark:bg-emerald-900/20">Monitoramento</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[220px] border-l-[3px] border-gray-400 dark:border-gray-600">Observação</th>
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px]" title="Valor recuperado com o chamado (R$)">Valor</th>
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
          <tr v-else-if="!items.length">
            <td colspan="17" class="py-8 text-center text-muted-foreground">sem chamados</td>
          </tr>
          <tr v-for="row in items" :key="row.id" class="border-t hover:brightness-95 dark:hover:brightness-110" :class="{ 'opacity-60': row.resolvido }">
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
                  title="histórico do chamado (data, hora e quem enviou)"
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
            <td class="px-2 py-1 bg-sky-50/40 dark:bg-sky-900/10 border-l-[3px] border-gray-400 dark:border-gray-600">
              <div class="flex items-center gap-2">
                <Button size="sm" variant="outline" class="h-7 px-2" :disabled="!canEdit || row.resolvido" @click="openHistorico(row, true)">
                  <MessageSquareReply class="size-3.5 mr-1" />
                  responder
                </Button>
                <span v-if="row.ultima_mensagem_at" class="text-[11px] text-muted-foreground whitespace-nowrap">últ. {{ fmtDateTime(row.ultima_mensagem_at) }}</span>
              </div>
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
            <td class="px-2 py-1 bg-sky-50/40 dark:bg-sky-900/10">
              <div class="flex items-center gap-2">
                <button
                  type="button"
                  class="inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] hover:bg-muted"
                  :disabled="!canEdit && !row.auto_ligada"
                  title="configurar réplica automática"
                  @click="openAuto(row)"
                >
                  <Settings2 class="size-3.5" />
                  <span v-if="row.auto_ligada" class="font-medium text-emerald-700 dark:text-emerald-300">ligada · a cada {{ row.auto_dias }}d</span>
                  <span v-else class="text-muted-foreground">desligada</span>
                </button>
                <span v-if="row.auto_ligada && row.auto_proximo_envio_at" class="text-[11px] text-muted-foreground whitespace-nowrap" :title="`última: ${fmtDateTime(row.auto_ultimo_envio_at)}`">
                  próx. {{ fmtDateTime(row.auto_proximo_envio_at) }}
                </span>
              </div>
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
            <td class="px-2 py-1 text-center bg-emerald-50/40 dark:bg-emerald-900/10">
              <input
                :checked="row.monitoramento"
                :disabled="!canEdit"
                type="checkbox"
                class="size-4 rounded border accent-primary disabled:cursor-default disabled:opacity-70"
                title="sim/não — acompanhar até resolver (canal API fecha sozinho quando o ML encerra)"
                @change="(e) => { row.monitoramento = (e.target as HTMLInputElement).checked; saveRow(row) }"
              />
            </td>
            <td class="px-1 py-0.5 border-l-[3px] border-gray-400 dark:border-gray-600">
              <input
                :value="row.observacao || ''"
                :disabled="!canEdit"
                :class="sheetInputClass"
                @input="(e) => setRowText(row, 'observacao', (e.target as HTMLInputElement).value)"
                @change="saveRow(row)"
              />
            </td>
            <!-- Valor recuperado com o chamado (R$) — Eduardo 03/09 -->
            <td class="px-1 py-0.5">
              <input
                :value="row.valor_recuperado ?? ''"
                :disabled="!canEdit"
                type="number"
                step="0.01"
                min="0"
                placeholder="R$"
                :class="[sheetInputClass, 'text-right tabular-nums']"
                title="Valor recuperado com o chamado (R$)"
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
          <div
            v-for="m in hist.mensagens"
            :key="m.id"
            class="rounded-md border px-3 py-2"
            :class="{
              'bg-muted/40': m.direcao === 'sistema',
              'border-sky-500/30': m.direcao === 'enviada',
              'border-orange-500/30 bg-orange-500/5': m.direcao === 'recebida',
            }"
          >
            <div class="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-muted-foreground">
              <span class="font-mono">{{ fmtDateTime(m.created_at) }}</span>
              <span class="font-medium text-foreground">{{ m.autor_nome || (m.direcao === 'recebida' ? 'plataforma' : '—') }}</span>
              <span>· {{ m.direcao === 'enviada' ? (m.tipo === 'replica_auto' ? 'réplica automática' : m.tipo === 'abertura' ? 'abertura na plataforma (devolução)' : 'réplica') : m.direcao }}</span>
              <span v-if="m.direcao !== 'sistema'" class="rounded px-1.5 py-0.5" :class="statusMensagemClass(m.status)">{{ m.status }}<template v-if="m.erro"> — {{ ERROS[m.erro] || m.erro }}</template></span>
              <span v-if="m.enviada_at" class="font-mono">enviada {{ fmtDateTime(m.enviada_at) }}</span>
            </div>
            <div class="mt-1 whitespace-pre-wrap text-sm" :class="{ 'text-muted-foreground italic': m.direcao === 'sistema' }">{{ m.texto }}</div>
            <div v-if="m.anexos.length" class="mt-2 flex flex-wrap gap-2">
              <a v-for="a in m.anexos" :key="a.id" :href="anexoUrl(a.id)" target="_blank" rel="noopener" :title="a.filename">
                <img :src="anexoUrl(a.id)" :alt="a.filename" class="h-20 w-20 rounded border object-cover" />
              </a>
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

    <!-- modal: réplica automática -->
    <div v-if="auto.open && auto.row" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" @click.self="closeAuto">
      <div class="w-full max-w-xl rounded-lg border bg-background shadow-xl">
        <div class="flex items-start justify-between gap-3 border-b px-4 py-3">
          <div>
            <div class="text-sm font-semibold">Réplica automática · pedido {{ auto.row.pedido_bling || auto.row.pedido_marketplace }}</div>
            <div class="text-xs text-muted-foreground">Reenvia a mensagem abaixo (com as fotos) a cada N dias enquanto ligada e o chamado estiver aberto. Canal: {{ auto.row.canal }}.</div>
          </div>
          <button type="button" class="rounded p-1 hover:bg-muted" @click="closeAuto"><X class="size-4" /></button>
        </div>
        <div class="space-y-3 px-4 py-3">
          <div class="flex flex-wrap items-center gap-4">
            <label class="inline-flex items-center gap-2 text-sm">
              <input v-model="auto.ligada" type="checkbox" class="size-4 rounded border accent-primary" :disabled="!canEdit" />
              <span :class="auto.ligada ? 'font-medium text-emerald-700 dark:text-emerald-300' : ''">{{ auto.ligada ? 'ligada' : 'desligada' }}</span>
            </label>
            <label class="inline-flex items-center gap-2 text-sm">
              enviar a cada
              <input v-model.number="auto.dias" type="number" min="1" max="365" class="h-8 w-16 rounded-md border bg-background px-2 text-sm text-center" :disabled="!canEdit" />
              dia(s)
            </label>
            <span v-if="auto.row.auto_ligada && auto.row.auto_proximo_envio_at" class="text-xs text-muted-foreground">próximo envio {{ fmtDateTime(auto.row.auto_proximo_envio_at) }}</span>
          </div>
          <textarea v-model="auto.mensagem" rows="5" class="w-full rounded-md border bg-background px-3 py-2 text-sm" placeholder="mensagem que será reenviada…" :disabled="!canEdit" />
          <div class="space-y-1">
            <div class="flex flex-wrap items-center gap-2">
              <label v-if="canEdit" class="inline-flex cursor-pointer items-center gap-1 rounded border px-2 py-1 text-xs hover:bg-muted">
                <Loader2 v-if="auto.uploading" class="size-3.5 animate-spin" />
                <ImagePlus v-else class="size-3.5" />
                anexar foto
                <input type="file" accept="image/png,image/jpeg,image/webp,image/gif" class="hidden" :disabled="auto.uploading" @change="uploadAutoAnexo" />
              </label>
              <span class="text-[11px] text-muted-foreground">{{ auto.row.anexos_auto.length }} foto(s)</span>
            </div>
            <div v-if="auto.row.anexos_auto.length" class="flex flex-wrap gap-2">
              <div v-for="a in auto.row.anexos_auto" :key="a.id" class="relative">
                <a :href="anexoUrl(a.id)" target="_blank" rel="noopener" :title="a.filename">
                  <img :src="anexoUrl(a.id)" :alt="a.filename" class="h-20 w-20 rounded border object-cover" />
                </a>
                <button v-if="canEdit" type="button" class="absolute -right-1.5 -top-1.5 rounded-full border bg-background p-0.5 text-red-500 hover:bg-red-500/10" title="remover" @click="removeAutoAnexo(a)">
                  <X class="size-3" />
                </button>
              </div>
            </div>
          </div>
          <div v-if="auto.erro" class="text-xs text-red-500">{{ auto.erro }}</div>
        </div>
        <div class="flex items-center justify-end gap-2 border-t px-4 py-3">
          <Button size="sm" variant="ghost" @click="closeAuto">cancelar</Button>
          <Button size="sm" :disabled="!canEdit || auto.saving" @click="salvarAuto">
            <Loader2 v-if="auto.saving" class="size-4 mr-1.5 animate-spin" />
            salvar
          </Button>
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
          <p class="text-muted-foreground">O chamado sai da lista de abertos e a réplica automática é desligada.</p>
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
          <div v-if="resolver.erro" class="text-xs text-red-500">{{ resolver.erro }}</div>
        </div>
        <div class="flex items-center justify-end gap-2 border-t px-4 py-3">
          <Button size="sm" variant="ghost" @click="closeResolver">cancelar</Button>
          <Button size="sm" :disabled="!canEdit || resolver.saving" @click="confirmarResolver">
            <Loader2 v-if="resolver.saving" class="size-4 mr-1.5 animate-spin" />
            <CheckCircle2 v-else class="size-4 mr-1.5" />
            resolver
          </Button>
        </div>
      </div>
    </div>
  </div>
</template>
