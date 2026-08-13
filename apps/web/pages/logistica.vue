<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { Plus, RefreshCw, X, Trash2, Search, Send, ImagePlus, ChevronLeft, ChevronRight, Copy, NotebookPen, ArrowLeftRight, UserRound, MessageCircle } from 'lucide-vue-next'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'logistica', action: 'view' },
})

const { api } = useApi()
const canEdit = useCan('logistica', 'edit')
const toasts = useToasts()

// Abas por marketplace + a aba Status (playbook único, compartilhado). A chave
// (Status Plataforma) é a mesma pra todas — só o ML enriquece a assinatura hoje.
const PLATAFORMA_TABS = [
  { key: 'ml', label: 'Mercado Livre' },
  { key: 'shopee', label: 'Shopee' },
  { key: 'amazon', label: 'Amazon' },
  { key: 'tiktok', label: 'TikTok' },
] as const
type PlataformaTab = (typeof PLATAFORMA_TABS)[number]['key']
const tab = ref<PlataformaTab | 'status'>('ml')

type MeliStatus = Record<string, string>

type Logistica = {
  id: string
  data: string | null
  pedido_bling: string | null
  pedido_marketplace: string | null
  plataforma: string | null
  conta: string | null
  meli_status: MeliStatus
  status_plataforma: string
  rastreio: string | null
  localizacao: string | null
  divergencia: string | null
  status_bling: string | null
  chamado: string | null
  observacao: string | null
  // Casador da aba Status (backend): regra que casa com a chave deste pedido.
  acao_match: boolean
  acao_status_id: string | null
  acao_resumo: string[]
  // acao_monitorar=alguma regra casada pede monitoramento; acao_resolvido=chegou
  // ao fim da cadeia de status. Ocultamos a linha quando resolvido E sem monitorar.
  acao_monitorar: boolean
  acao_resolvido: boolean
  created_by: string | null
  created_at: string
  updated_at: string
}

type Opcoes = {
  field_order: string[]
  field_labels: Record<string, string>
  field_options: Record<string, string[]>
  status_bling_options: string[]
}

type Candidato = { status_bling: string; matches: number }

const rows = ref<Logistica[]>([])
const loading = ref(false)
const error = ref<string | null>(null)

const opcoes = ref<Opcoes>({ field_order: [], field_labels: {}, field_options: {}, status_bling_options: [] })

async function refresh() {
  loading.value = true
  error.value = null
  try {
    // Cada aba de marketplace filtra server-side pela plataforma. A aba Status
    // usa outro carregador (refreshStatus); aqui caímos em ML só por garantia.
    const plat = tab.value === 'status' ? 'ml' : tab.value
    rows.value = await api<Logistica[]>(`/api/logistica?plataforma=${plat}`)
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    loading.value = false
  }
}

// Recarrega os pedidos E as chaves da aba Status ao mesmo tempo. Como o casador
// (acao_resumo/acao_match) e o destaque vermelho são derivados das regras da aba
// Status, ao cadastrar uma chave nova lá basta recarregar aqui: as linhas já
// casadas somem do vermelho e ganham as ações, sem esperar o status do ML mudar.
//
// Com permissão de edição, o botão também DISPARA o motor em segundo plano:
// re-enriquece o Status Plataforma dos pedidos PENDENTES do painel (as 4 abas
// de marketplace) e aplica no Bling a mudança de situação dos que já têm regra.
// Roda em background (pode passar do timeout do Cloudflare) e a lista se
// atualiza sozinha no poll.
const recarregando = ref(false)
async function recarregar() {
  // O motor (enriquece Status Plataforma + aplica status no Bling) roda pras 4
  // abas de marketplace (ML, Shopee, TikTok, Amazon). Na aba Status o botão só
  // repuxa os pedidos e as chaves da aba Status.
  if (!canEdit.value || tab.value === 'status') {
    await Promise.all([refresh(), refreshStatus()])
    return
  }
  if (
    !confirm(
      'Recarregar vai atualizar o Status Plataforma dos pedidos pendentes do painel (todas as abas) e aplicar no Bling a mudança de situação dos que já têm regra definida. Continuar?',
    )
  )
    return
  recarregando.value = true
  try {
    await api('/api/logistica/recarregar', { method: 'POST' })
    toasts.info('Recarregando em segundo plano', 'Leva uns 2-3 minutos; a lista atualiza sozinha.')
  } catch (e: any) {
    toasts.error('Não foi possível recarregar', e?.data?.detail?.code || e?.message || 'erro')
    recarregando.value = false
    return
  }
  // Repuxa periodicamente até o job terminar (~2-3 min com o escopo pendente;
  // o poll antigo de 30s parava antes e o botão parecia "não fazer nada").
  for (let i = 0; i < 24; i++) {
    await new Promise((r) => setTimeout(r, 10000))
    await Promise.all([refresh(), refreshStatus()])
  }
  recarregando.value = false
}

// ---- Busca + filtros (client-side sobre as linhas já carregadas) ----
const search = ref('')
const contaFilter = ref('all')
const statusBlingFilter = ref('all')
const dataInicioFilter = ref('')
const dataFimFilter = ref('')

const contas = computed(() =>
  [...new Set(rows.value.map((c) => c.conta).filter((v): v is string => !!v))].sort((a, b) =>
    a.localeCompare(b, 'pt-BR', { sensitivity: 'base' }),
  ),
)
const statusBlings = computed(() =>
  [...new Set(rows.value.map((c) => c.status_bling).filter((v): v is string => !!v))].sort((a, b) =>
    a.localeCompare(b, 'pt-BR', { sensitivity: 'base' }),
  ),
)

const filteredRows = computed(() => {
  const q = search.value.trim().toLowerCase()
  const di = dataInicioFilter.value
  const df = dataFimFilter.value
  return rows.value.filter((c) => {
    // Painel de pendências: some quando já chegou ao fim da cadeia de status no
    // Bling (resolvido) E a regra casada não pede monitoramento — nada a fazer.
    if (c.acao_resolvido && !c.acao_monitorar) return false
    if (contaFilter.value !== 'all' && (c.conta || '') !== contaFilter.value) return false
    if (statusBlingFilter.value !== 'all' && (c.status_bling || '') !== statusBlingFilter.value) return false
    if (di && (!c.data || c.data < di)) return false
    if (df && (!c.data || c.data > df)) return false
    if (q) {
      const hay = [
        c.pedido_bling,
        c.pedido_marketplace,
        c.conta,
        c.rastreio,
        c.chamado,
        c.localizacao,
        c.divergencia,
        c.status_bling,
        c.status_plataforma,
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
      if (!hay.includes(q)) return false
    }
    return true
  })
})

function limparFiltros() {
  search.value = ''
  contaFilter.value = 'all'
  statusBlingFilter.value = 'all'
  dataInicioFilter.value = ''
  dataFimFilter.value = ''
}

// ---- Paginação (client-side, 50 por página; muitas linhas travam o DOM) ----
const PAGE_SIZE = 50
const page = ref(1)
const totalPages = computed(() => Math.max(1, Math.ceil(filteredRows.value.length / PAGE_SIZE)))
const pagedRows = computed(() =>
  filteredRows.value.slice((page.value - 1) * PAGE_SIZE, page.value * PAGE_SIZE),
)
const pageStart = computed(() =>
  filteredRows.value.length ? (page.value - 1) * PAGE_SIZE + 1 : 0,
)
const pageEnd = computed(() => Math.min(page.value * PAGE_SIZE, filteredRows.value.length))
function goToPage(p: number) {
  page.value = Math.min(Math.max(1, p), totalPages.value)
}
// Filtros mudaram → volta pra 1ª página.
watch([search, contaFilter, statusBlingFilter, dataInicioFilter, dataFimFilter], () => {
  page.value = 1
})
// Recarregou dados / página ficou fora do intervalo → corrige.
watch(totalPages, (tp) => {
  if (page.value > tp) page.value = tp
})

const filtrosAtivos = computed(
  () =>
    !!search.value.trim() ||
    contaFilter.value !== 'all' ||
    statusBlingFilter.value !== 'all' ||
    !!dataInicioFilter.value ||
    !!dataFimFilter.value,
)

async function loadOpcoes() {
  try {
    opcoes.value = await api<Opcoes>('/api/logistica/opcoes')
  } catch {
    // sem opções o formulário ainda funciona (selects vazios)
  }
}

await Promise.all([refresh(), loadOpcoes()])

// ---- Modal (create + edit compartilham o mesmo formulário) ----
const modalOpen = ref(false)
const editingId = ref<string | null>(null)
const saving = ref(false)
const formErr = ref<string | null>(null)

function emptyForm() {
  const meli: MeliStatus = {}
  for (const f of opcoes.value.field_order) meli[f] = ''
  return {
    data: '',
    pedido_bling: '',
    pedido_marketplace: '',
    plataforma: '',
    conta: '',
    meli_status: meli,
    rastreio: '',
    localizacao: '',
    status_bling: '',
    chamado: '',
    observacao: '',
  }
}

const form = reactive(emptyForm())

function resetForm(src?: Logistica) {
  const base = emptyForm()
  form.data = src?.data || ''
  form.pedido_bling = src?.pedido_bling || ''
  form.pedido_marketplace = src?.pedido_marketplace || ''
  form.plataforma = src?.plataforma || ''
  form.conta = src?.conta || ''
  form.rastreio = src?.rastreio || ''
  form.localizacao = src?.localizacao || ''
  form.status_bling = src?.status_bling || ''
  form.chamado = src?.chamado || ''
  form.observacao = src?.observacao || ''
  const meli = { ...base.meli_status }
  if (src?.meli_status) for (const k of Object.keys(meli)) meli[k] = src.meli_status[k] || ''
  form.meli_status = meli
}

function openNew() {
  editingId.value = null
  resetForm()
  formErr.value = null
  candidatos.value = []
  modalOpen.value = true
}

function openEdit(c: Logistica) {
  editingId.value = c.id
  resetForm(c)
  formErr.value = null
  modalOpen.value = true
  suggest()
}

function closeModal() {
  modalOpen.value = false
  editingId.value = null
}

function payload() {
  const meli: MeliStatus = {}
  for (const f of opcoes.value.field_order) {
    const v = (form.meli_status[f] || '').trim()
    if (v) meli[f] = v
  }
  return {
    data: form.data || null,
    pedido_bling: form.pedido_bling.trim() || null,
    pedido_marketplace: form.pedido_marketplace.trim() || null,
    plataforma: form.plataforma.trim() || null,
    conta: form.conta.trim() || null,
    meli_status: meli,
    rastreio: form.rastreio.trim() || null,
    localizacao: form.localizacao.trim() || null,
    status_bling: form.status_bling.trim() || null,
    chamado: form.chamado.trim() || null,
    observacao: form.observacao.trim() || null,
  }
}

async function save() {
  saving.value = true
  formErr.value = null
  try {
    if (editingId.value) {
      await api(`/api/logistica/${editingId.value}`, { method: 'PATCH', body: payload() })
    } else {
      await api('/api/logistica', { method: 'POST', body: payload() })
    }
    closeModal()
    await refresh()
  } catch (e: any) {
    formErr.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    saving.value = false
  }
}

async function remove() {
  if (!editingId.value) return
  if (!confirm('Remover este caso?')) return
  saving.value = true
  formErr.value = null
  try {
    await api(`/api/logistica/${editingId.value}`, { method: 'DELETE' })
    closeModal()
    await refresh()
  } catch (e: any) {
    formErr.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    saving.value = false
  }
}

// ---- Sugestão de Status Bling (ao vivo, conforme os status do Meli) ----
const candidatos = ref<Candidato[]>([])
let suggestTimer: any = null

async function suggest() {
  const meli: MeliStatus = {}
  for (const f of opcoes.value.field_order) {
    const v = (form.meli_status[f] || '').trim()
    if (v) meli[f] = v
  }
  if (Object.keys(meli).length === 0) {
    candidatos.value = []
    return
  }
  try {
    const r = await api<{ candidatos: Candidato[] }>('/api/logistica/sugestao', {
      method: 'POST',
      body: { meli_status: meli },
    })
    candidatos.value = r.candidatos
  } catch {
    candidatos.value = []
  }
}

watch(
  () => ({ ...form.meli_status }),
  () => {
    if (!modalOpen.value) return
    clearTimeout(suggestTimer)
    suggestTimer = setTimeout(suggest, 250)
  },
  { deep: true },
)

// ---- Helpers de exibição ----
function fmtDate(s: string | null) {
  if (!s) return '—'
  const [y, m, d] = s.split('-').map((n) => Number(n))
  if (!y || !m || !d) return s
  return new Date(y, m - 1, d).toLocaleDateString('pt-BR')
}

// "STATUS PLATAFORMA" = assinatura em PT vinda do backend (traduz os status do
// Meli). Fallback pro join local dos tokens crus se o backend não mandar.
function assinatura(c: Logistica): string {
  if (c.status_plataforma) return c.status_plataforma
  const order = opcoes.value.field_order.length
    ? opcoes.value.field_order
    : Object.keys(c.meli_status || {})
  const parts = order.map((f) => c.meli_status?.[f]).filter(Boolean)
  return parts.join(' | ')
}

// Tooltip da coluna "Status Plataforma": mostra qual campo do Meli é cada
// pedaço da assinatura (order_status, ship_status, ...) pra dar pra identificar.
// Pareia os campos NÃO-vazios (na ordem fixa) com os valores PT já traduzidos
// (a assinatura junta esses mesmos valores por " | ").
function statusTooltip(c: Logistica): string {
  const fo = opcoes.value.field_order
  const labels = opcoes.value.field_labels
  const ms = c.meli_status || {}
  const naoVazios = fo.filter((f) => String(ms[f] ?? '').trim())
  if (!naoVazios.length) return ''
  const partesPt = (c.status_plataforma || '').split(' | ')
  return naoVazios
    .map((f, i) => `${labels[f] || f}: ${partesPt[i] ?? String(ms[f] ?? '')}`)
    .join('\n')
}

function isMl(c: Logistica): boolean {
  return ['mercado livre', 'mercadolivre', 'ml'].includes(
    (c.plataforma || '').trim().toLowerCase(),
  )
}

function isShopee(c: Logistica): boolean {
  return (c.plataforma || '').trim().toLowerCase() === 'shopee'
}

function isTiktok(c: Logistica): boolean {
  return ['tiktok', 'tik tok', 'tiktok shop'].includes(
    (c.plataforma || '').trim().toLowerCase(),
  )
}

function isAmazon(c: Logistica): boolean {
  return (c.plataforma || '').trim().toLowerCase() === 'amazon'
}

// Copia a "chave" (assinatura do Status Plataforma, ex. "Pago | Pendente | Programado").
async function copiarChave(c: Logistica) {
  const chave = assinatura(c)
  if (!chave) return
  try {
    await navigator.clipboard.writeText(chave)
    toasts.success('Chave copiada', chave)
  } catch {
    toasts.error('Não foi possível copiar')
  }
}

// Puxa a assinatura do Meli (8 campos) da API do ML pra uma linha.
const refreshingMeli = ref<Set<string>>(new Set())
async function atualizarMeli(c: Logistica) {
  refreshingMeli.value = new Set(refreshingMeli.value).add(c.id)
  try {
    const updated = await api<Logistica>(`/api/logistica/${c.id}/atualizar-meli`, {
      method: 'POST',
    })
    const i = rows.value.findIndex((r) => r.id === c.id)
    if (i >= 0) rows.value[i] = updated
  } catch (e: any) {
    const code = e?.data?.detail?.code || e?.message || 'erro'
    error.value = code
  } finally {
    const s = new Set(refreshingMeli.value)
    s.delete(c.id)
    refreshingMeli.value = s
  }
}

// Puxa o order_status da Shopee (API v2) pra uma linha.
async function atualizarShopee(c: Logistica) {
  refreshingMeli.value = new Set(refreshingMeli.value).add(c.id)
  try {
    const updated = await api<Logistica>(`/api/logistica/${c.id}/atualizar-shopee`, {
      method: 'POST',
    })
    const i = rows.value.findIndex((r) => r.id === c.id)
    if (i >= 0) rows.value[i] = updated
  } catch (e: any) {
    const code = e?.data?.detail?.code || e?.message || 'erro'
    error.value = code
  } finally {
    const s = new Set(refreshingMeli.value)
    s.delete(c.id)
    refreshingMeli.value = s
  }
}

// Puxa o status do pedido TikTok (Order API 202309) + rastreio pra uma linha.
async function atualizarTiktok(c: Logistica) {
  refreshingMeli.value = new Set(refreshingMeli.value).add(c.id)
  try {
    const updated = await api<Logistica>(`/api/logistica/${c.id}/atualizar-tiktok`, {
      method: 'POST',
    })
    const i = rows.value.findIndex((r) => r.id === c.id)
    if (i >= 0) rows.value[i] = updated
  } catch (e: any) {
    const code = e?.data?.detail?.code || e?.message || 'erro'
    error.value = code
  } finally {
    const s = new Set(refreshingMeli.value)
    s.delete(c.id)
    refreshingMeli.value = s
  }
}

// Puxa o status do pedido Amazon (OrderStatus + EasyShip) pra uma linha.
async function atualizarAmazon(c: Logistica) {
  refreshingMeli.value = new Set(refreshingMeli.value).add(c.id)
  try {
    const updated = await api<Logistica>(`/api/logistica/${c.id}/atualizar-amazon`, {
      method: 'POST',
    })
    const i = rows.value.findIndex((r) => r.id === c.id)
    if (i >= 0) rows.value[i] = updated
  } catch (e: any) {
    const code = e?.data?.detail?.code || e?.message || 'erro'
    error.value = code
  } finally {
    const s = new Set(refreshingMeli.value)
    s.delete(c.id)
    refreshingMeli.value = s
  }
}

// ================= Aba Status =================
type LogisticaStatus = {
  id: string
  plataforma: string | null
  status_plataforma: string | null
  status_atual: string | null
  alterar_status_bling: string | null
  monitoramento: boolean
  abrir_chamado: boolean
  abrir_reembolso: boolean
  mensagem_chamado: string | null
  mensagem_bling: string | null
  mensagem_threema: string | null
  threema_recipients: string | null
  anexos: Anexo[]
  created_by: string | null
  created_at: string
  updated_at: string
}

type Anexo = {
  id: string
  filename: string
  content_type: string
  size_bytes: number
  created_at: string
}

// Campos de texto editáveis inline (mensagem_chamado usa textarea).
type StatusTextField =
  | 'plataforma'
  | 'status_plataforma'
  | 'status_atual'
  | 'alterar_status_bling'
  | 'mensagem_chamado'
  | 'mensagem_bling'
  | 'mensagem_threema'

const statusRows = ref<LogisticaStatus[]>([])
const statusLoading = ref(false)
const statusError = ref<string | null>(null)
let statusLoaded = false

async function refreshStatus() {
  statusLoading.value = true
  statusError.value = null
  try {
    statusRows.value = await api<LogisticaStatus[]>('/api/logistica/status')
    statusLoaded = true
  } catch (e: any) {
    statusError.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    statusLoading.value = false
  }
}

watch(tab, (t) => {
  if (t === 'status') {
    if (!statusLoaded) refreshStatus()
  } else {
    // Troca de aba de marketplace → recarrega os pedidos daquela plataforma.
    page.value = 1
    refresh()
  }
})

// Carrega as chaves cadastradas na aba Status já no início, pra a aba
// Mercado Livre poder pintar de vermelho os pedidos cuja chave ainda não
// foi cadastrada (= status que a gente ainda não decidiu o que fazer).
onMounted(() => {
  if (!statusLoaded) refreshStatus()
})

function normKey(s: string | null | undefined): string {
  return (s || '').trim().toLowerCase()
}
const registeredKeys = computed(() => {
  const set = new Set<string>()
  for (const s of statusRows.value) {
    const k = normKey(s.status_plataforma)
    if (k) set.add(k)
  }
  return set
})
// Vermelho: tem chave (Status Plataforma) e ela NÃO está na aba Status.
function precisaAtencao(c: Logistica): boolean {
  const k = normKey(assinatura(c))
  return !!k && !registeredKeys.value.has(k)
}

// ---- Edição inline (uma célula por vez) ----
const editing = ref<{ id: string; field: StatusTextField } | null>(null)
const editValue = ref('')
const statusBusy = ref<Set<string>>(new Set())

function isEditing(s: LogisticaStatus, field: StatusTextField) {
  return editing.value?.id === s.id && editing.value?.field === field
}

function startEdit(s: LogisticaStatus, field: StatusTextField) {
  if (!canEdit.value) return
  editing.value = { id: s.id, field }
  editValue.value = (s[field] as string | null) || ''
}

function cancelEdit() {
  editing.value = null
}

function setStatusBusy(id: string, on: boolean) {
  const next = new Set(statusBusy.value)
  if (on) next.add(id)
  else next.delete(id)
  statusBusy.value = next
}

async function patchStatusField(id: string, body: Record<string, unknown>) {
  setStatusBusy(id, true)
  statusError.value = null
  try {
    const updated = await api<LogisticaStatus>(`/api/logistica/status/${id}`, {
      method: 'PATCH',
      body,
    })
    const i = statusRows.value.findIndex((r) => r.id === id)
    if (i >= 0) statusRows.value[i] = updated
  } catch (e: any) {
    statusError.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    setStatusBusy(id, false)
  }
}

async function commitEdit(s: LogisticaStatus) {
  if (!editing.value) return
  const field = editing.value.field
  const val = editValue.value.trim()
  editing.value = null
  const cur = (s[field] as string | null) || ''
  if (val === cur) return
  await patchStatusField(s.id, { [field]: val || null })
}

async function toggleStatusBool(
  s: LogisticaStatus,
  field: 'monitoramento' | 'abrir_chamado' | 'abrir_reembolso',
) {
  if (!canEdit.value) return
  await patchStatusField(s.id, { [field]: !s[field] })
}

// ---- Modal "Novo status" ----
const showStatusForm = ref(false)
const statusSaving = ref(false)
const statusForm = ref({
  plataforma: '',
  status_plataforma: '',
  status_atual: '',
  alterar_status_bling: '',
  monitoramento: false,
  abrir_chamado: false,
  abrir_reembolso: false,
  mensagem_chamado: '',
  mensagem_bling: '',
  mensagem_threema: '',
})

function openStatusForm() {
  statusForm.value = {
    plataforma: '',
    status_plataforma: '',
    status_atual: '',
    alterar_status_bling: '',
    monitoramento: false,
    abrir_chamado: false,
    abrir_reembolso: false,
    mensagem_chamado: '',
    mensagem_bling: '',
    mensagem_threema: '',
  }
  showStatusForm.value = true
}

async function saveStatusForm() {
  statusSaving.value = true
  statusError.value = null
  try {
    const f = statusForm.value
    const created = await api<LogisticaStatus>('/api/logistica/status', {
      method: 'POST',
      body: {
        plataforma: f.plataforma.trim() || null,
        status_plataforma: f.status_plataforma.trim() || null,
        status_atual: f.status_atual.trim() || null,
        alterar_status_bling: f.alterar_status_bling.trim() || null,
        monitoramento: f.monitoramento,
        abrir_chamado: f.abrir_chamado,
        abrir_reembolso: f.abrir_reembolso,
        mensagem_chamado: f.mensagem_chamado.trim() || null,
        mensagem_bling: f.mensagem_bling.trim() || null,
        mensagem_threema: f.mensagem_threema.trim() || null,
      },
    })
    statusRows.value.unshift(created)
    showStatusForm.value = false
  } catch (e: any) {
    statusError.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    statusSaving.value = false
  }
}

async function removeStatusRow(s: LogisticaStatus) {
  if (!confirm('Remover este status?')) return
  setStatusBusy(s.id, true)
  statusError.value = null
  try {
    await api(`/api/logistica/status/${s.id}`, { method: 'DELETE' })
    statusRows.value = statusRows.value.filter((r) => r.id !== s.id)
  } catch (e: any) {
    statusError.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    setStatusBusy(s.id, false)
  }
}

// ---- Mensagem Threema (notifica as pessoas do problema) ----
// O modal serve pra dois modos: 'salvar' (ícone 👤 guarda QUEM recebe na regra,
// porque vai virar automático) e 'enviar' (dispara agora pros escolhidos).
type ThreemaDestinatario = { id: string; nome: string }
const threemaDestinatarios = ref<ThreemaDestinatario[]>([])
const threemaModal = reactive({
  open: false,
  mode: 'enviar' as 'enviar' | 'salvar',
  status: null as LogisticaStatus | null,
  selecionados: new Set<string>(),
  sending: false,
})

async function loadThreemaDestinatarios() {
  if (threemaDestinatarios.value.length) return
  try {
    threemaDestinatarios.value = await api<ThreemaDestinatario[]>(
      '/api/logistica/threema/destinatarios',
    )
  } catch {
    threemaDestinatarios.value = []
  }
}

// IDs salvos na regra (string "A,B" → Set). Se vazio, cai em "todos".
function parseThreemaIds(raw: string | null): Set<string> {
  if (!raw) return new Set()
  return new Set(
    raw
      .split(/[,;\s]+/)
      .map((x) => x.trim().toUpperCase())
      .filter(Boolean),
  )
}

function preselecionarThreema(s: LogisticaStatus): Set<string> {
  const salvos = parseThreemaIds(s.threema_recipients)
  if (salvos.size) return salvos
  return new Set(threemaDestinatarios.value.map((d) => d.id))
}

// 👤 — escolher e SALVAR na regra quem recebe (base do envio automático futuro).
async function escolherDestinatarios(s: LogisticaStatus) {
  await loadThreemaDestinatarios()
  threemaModal.mode = 'salvar'
  threemaModal.status = s
  threemaModal.selecionados = preselecionarThreema(s)
  threemaModal.open = true
}

async function enviarThreema(s: LogisticaStatus) {
  if (!s.mensagem_threema) return
  await loadThreemaDestinatarios()
  threemaModal.mode = 'enviar'
  threemaModal.status = s
  threemaModal.selecionados = preselecionarThreema(s)
  threemaModal.open = true
}

function toggleThreemaDest(id: string) {
  if (threemaModal.selecionados.has(id)) threemaModal.selecionados.delete(id)
  else threemaModal.selecionados.add(id)
}

async function confirmarThreemaModal() {
  const s = threemaModal.status
  if (!s) return
  const recipients = [...threemaModal.selecionados]
  if (!recipients.length) {
    toasts.error('Escolha ao menos um destinatário')
    return
  }
  threemaModal.sending = true
  setStatusBusy(s.id, true)
  statusError.value = null
  try {
    if (threemaModal.mode === 'salvar') {
      await patchStatusField(s.id, { threema_recipients: recipients.join(',') })
      if (statusError.value) {
        toasts.error('Não foi possível salvar', statusError.value)
        return
      }
      toasts.success('Destinatários salvos', `${recipients.length} contato(s)`)
      threemaModal.open = false
      return
    }
    const r = await api<{ sent: string[]; failed: string[] }>(
      `/api/logistica/status/${s.id}/enviar-threema`,
      { method: 'POST', body: { recipients } },
    )
    if (r.failed.length) {
      toasts.error(`Enviado a ${r.sent.length}, falhou ${r.failed.length}`, r.failed.join(', '))
    } else {
      toasts.success('Mensagem Threema enviada', `${r.sent.length} destinatário(s)`)
    }
    threemaModal.open = false
  } catch (e: any) {
    const code = e?.data?.detail?.code || e?.message || 'erro'
    statusError.value = code
    toasts.error(threemaModal.mode === 'salvar' ? 'Não foi possível salvar' : 'Não foi possível enviar', code)
  } finally {
    threemaModal.sending = false
    setStatusBusy(s.id, false)
  }
}

// Enviar Threema a partir de uma LINHA do marketplace (aba Amazon/Shopee/etc):
// o servidor acha a regra casada (coligação), injeta Pedido/Loja da própria
// linha e usa os destinatários salvos na regra.
async function enviarThreemaPedido(c: Logistica) {
  if (!confirm(`Enviar aviso Threema do pedido ${c.pedido_marketplace || c.pedido_bling || ''} (loja ${c.plataforma || '—'})?`)) return
  refreshingMeli.value = new Set(refreshingMeli.value).add(c.id)
  try {
    const r = await api<{ sent: string[]; failed: string[] }>(
      `/api/logistica/${c.id}/enviar-threema`,
      { method: 'POST' },
    )
    if (r.failed.length) {
      toasts.error(`Enviado a ${r.sent.length}, falhou ${r.failed.length}`, r.failed.join(', '))
    } else {
      toasts.success('Mensagem Threema enviada', `${r.sent.length} destinatário(s)`)
    }
    // Enviou → o aviso foi feito: o pedido resolve e some do painel.
    if (r.sent.length) await refresh()
  } catch (e: any) {
    const code = e?.data?.detail?.code || e?.message || 'erro'
    toasts.error('Não foi possível enviar', code)
  } finally {
    const s = new Set(refreshingMeli.value)
    s.delete(c.id)
    refreshingMeli.value = s
  }
}

// ---- Anexos de imagem na "Mensagem do Chamado" ----
function anexoUrl(id: string) {
  // Relativo → o cookie de sessão vai junto no <img src>/<a href>.
  return `/api/logistica/anexos/${id}`
}

async function uploadAnexo(s: LogisticaStatus, ev: Event) {
  const input = ev.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = '' // permite re-selecionar o mesmo arquivo
  if (!file) return
  setStatusBusy(s.id, true)
  statusError.value = null
  try {
    const fd = new FormData()
    fd.append('file', file)
    const anexo = await api<Anexo>(`/api/logistica/status/${s.id}/anexos`, {
      method: 'POST',
      body: fd,
    })
    if (!Array.isArray(s.anexos)) s.anexos = []
    s.anexos.push(anexo)
  } catch (e: any) {
    statusError.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    setStatusBusy(s.id, false)
  }
}

async function removeAnexo(s: LogisticaStatus, anexoId: string) {
  if (!confirm('Remover esta imagem?')) return
  setStatusBusy(s.id, true)
  statusError.value = null
  try {
    await api(`/api/logistica/anexos/${anexoId}`, { method: 'DELETE' })
    s.anexos = (s.anexos || []).filter((a) => a.id !== anexoId)
  } catch (e: any) {
    statusError.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    setStatusBusy(s.id, false)
  }
}

// ---- Enviar chamado direto pro ML (aba Mercado Livre) ----
const CHAMADO_ERROS: Record<string, string> = {
  logistica_sem_mensagem_chamado:
    'Sem regra na aba Status que case com o Status Plataforma deste pedido — cadastre a mensagem do chamado.',
  logistica_nao_ml: 'Só pedidos do Mercado Livre.',
  logistica_sem_pedido: 'Pedido sem número de marketplace.',
  logistica_sem_integracao: 'Conta sem integração ML configurada.',
  logistica_sem_reclamacao:
    'Pedido sem reclamação aberta pelo comprador — só dá pra abrir chamado no painel do Mercado Livre.',
  logistica_reclamacao_encerrada: 'Reclamação já encerrada no Mercado Livre.',
  logistica_reclamacao_sem_acao:
    'O Mercado Livre não liberou falar com o mediador neste chamado.',
}
const sendingChamado = ref<Set<string>>(new Set())
async function enviarChamado(c: Logistica) {
  if (
    !confirm(
      'Isto abre a mediação do Mercado Livre nesta reclamação (o ML entra como mediador — ação irreversível) e manda a mensagem da regra pro ML. Continuar?',
    )
  )
    return
  sendingChamado.value = new Set(sendingChamado.value).add(c.id)
  error.value = null
  try {
    const updated = await api<Logistica>(`/api/logistica/${c.id}/enviar-chamado`, {
      method: 'POST',
    })
    const i = rows.value.findIndex((r) => r.id === c.id)
    if (i >= 0) rows.value[i] = updated
  } catch (e: any) {
    const code = e?.data?.detail?.code
    error.value =
      (code && CHAMADO_ERROS[code]) ||
      e?.data?.detail?.erro ||
      code ||
      e?.message ||
      'erro'
  } finally {
    const s = new Set(sendingChamado.value)
    s.delete(c.id)
    sendingChamado.value = s
  }
}

const MENSAGEM_BLING_ERROS: Record<string, string> = {
  logistica_sem_mensagem_bling:
    'Sem regra na aba Status com Mensagem Bling que case com o Status Plataforma deste pedido.',
  logistica_sem_pedido_bling: 'Linha sem número de pedido Bling.',
  logistica_pedido_bling_nao_achado: 'Pedido não encontrado no Bling.',
  logistica_sem_integracao_bling: 'Integração Bling não configurada.',
}
const aplicandoBling = ref<Set<string>>(new Set())
async function aplicarMensagemBling(c: Logistica) {
  aplicandoBling.value = new Set(aplicandoBling.value).add(c.id)
  error.value = null
  try {
    // 1) dry-run: mostra o que SERIA escrito nas Observações antes de confirmar.
    const prev = await api<{ observacoes_novo: string }>(
      `/api/logistica/${c.id}/mensagem-bling/preview`,
      { method: 'POST' },
    )
    if (
      !confirm(
        `Vai anexar nas Observações do pedido no Bling (linha nova no topo, nada é sobrescrito):\n\n${prev.observacoes_novo}\n\nConfirmar?`,
      )
    )
      return
    // 2) aplica de verdade (PUT do pedido inteiro sanitizado).
    await api(`/api/logistica/${c.id}/mensagem-bling`, { method: 'POST' })
    toasts.success('Mensagem Bling aplicada', c.pedido_bling || '')
  } catch (e: any) {
    const code = e?.data?.detail?.code
    const msg =
      (code && MENSAGEM_BLING_ERROS[code]) || e?.data?.detail?.erro || code || e?.message || 'erro'
    error.value = msg
    toasts.error('Não foi possível aplicar', msg)
  } finally {
    const s = new Set(aplicandoBling.value)
    s.delete(c.id)
    aplicandoBling.value = s
  }
}

// ---- Executor: Alterar Status Bling (muda a situação do pedido no Bling) ----
const STATUS_BLING_ERROS: Record<string, string> = {
  logistica_sem_status_bling:
    'Sem regra na aba Status com "Alterar Status Bling" que case com o Status Plataforma deste pedido.',
  logistica_status_bling_desconhecido: 'A situação alvo não existe no catálogo do Bling.',
  logistica_status_atual_divergente:
    'O pedido não está no "Status Atual" que a regra exige — a mudança não foi aplicada pra não regredir.',
  logistica_sem_pedido_bling: 'Linha sem número de pedido Bling.',
  logistica_pedido_bling_nao_achado: 'Pedido não encontrado no Bling.',
  logistica_sem_integracao_bling: 'Integração Bling não configurada.',
}
// A regra casada pede mudança de status quando o resumo tem "Status Bling → X".
function temStatusBlingAcao(c: Logistica): boolean {
  return c.acao_resumo.some((a) => a.startsWith('Status Bling'))
}
const aplicandoStatus = ref<Set<string>>(new Set())
async function aplicarStatusBling(c: Logistica) {
  aplicandoStatus.value = new Set(aplicandoStatus.value).add(c.id)
  error.value = null
  try {
    // 1) dry-run: mostra a transição da regra (Status Atual -> Alvo) antes de confirmar.
    const prev = await api<{
      situacao_de: string | null
      situacao_alvo: string
      situacao_atual_nome: string | null
      ja_no_alvo: boolean
      aplicavel: boolean
    }>(`/api/logistica/${c.id}/alterar-status-bling/preview`, { method: 'POST' })
    // O preview lê a situação viva do Bling e o backend já sincronizou o
    // status_bling da linha; reflete no painel em qualquer ramo (nada a fazer/fora
    // do fluxo/confirmar) pra a coluna se auto-corrigir sem esperar recarregar.
    if (prev.situacao_atual_nome) c.status_bling = prev.situacao_atual_nome
    if (prev.ja_no_alvo) {
      toasts.info('Nada a fazer', `Pedido já está em "${prev.situacao_alvo}".`)
      return
    }
    // A regra tem "Status Atual" (o "de") mas o pedido não está nele → não muda
    // pra não regredir (ex.: já Entregue, regra "Em andamento → Entregue").
    if (!prev.aplicavel) {
      const atualNome = prev.situacao_atual_nome || '(desconhecida)'
      toasts.info(
        'Fora do fluxo',
        `A regra é "${prev.situacao_de} → ${prev.situacao_alvo}", mas o pedido está em "${atualNome}". Nada a fazer.`,
      )
      return
    }
    // "De" = o Status Atual da regra (o fluxo esperado); cai no atual do Bling se
    // a regra não exige um "de" específico.
    const de = prev.situacao_de || prev.situacao_atual_nome || '(desconhecida)'
    if (
      !confirm(
        `Vai mudar a situação do pedido no Bling:\n\n${de}  →  ${prev.situacao_alvo}\n\nConfirmar?`,
      )
    )
      return
    // 2) aplica de verdade (PATCH da situação, não reenvia o pedido).
    await api(`/api/logistica/${c.id}/alterar-status-bling`, { method: 'POST' })
    c.status_bling = prev.situacao_alvo
    toasts.success('Status Bling alterado', `${c.pedido_bling || ''} → ${prev.situacao_alvo}`)
  } catch (e: any) {
    const code = e?.data?.detail?.code
    const msg =
      (code && STATUS_BLING_ERROS[code]) || e?.data?.detail?.erro || code || e?.message || 'erro'
    error.value = msg
    toasts.error('Não foi possível alterar', msg)
  } finally {
    const s = new Set(aplicandoStatus.value)
    s.delete(c.id)
    aplicandoStatus.value = s
  }
}
</script>

<template>
  <div class="space-y-4">
    <div class="flex flex-wrap items-center gap-3">
      <h1 class="text-2xl font-semibold">Logística</h1>
    </div>

    <!-- Tabs -->
    <div class="flex flex-wrap gap-1 border-b">
      <button
        v-for="t in PLATAFORMA_TABS"
        :key="t.key"
        type="button"
        class="px-4 py-2 text-sm font-medium border-b-2 -mb-px"
        :class="tab === t.key ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'"
        @click="tab = t.key"
      >
        {{ t.label }}
      </button>
      <button
        type="button"
        class="px-4 py-2 text-sm font-medium border-b-2 -mb-px"
        :class="tab === 'status' ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'"
        @click="tab = 'status'"
      >
        Status
      </button>
    </div>

    <!-- ============ ABAS DE MARKETPLACE (ML/Shopee/Amazon/TikTok) ============ -->
    <template v-if="tab !== 'status'">
      <div class="flex flex-wrap items-center gap-3">
        <Button size="sm" variant="ghost" :disabled="loading || statusLoading || recarregando" @click="recarregar">
          <RefreshCw class="size-4 mr-1" :class="loading || statusLoading || recarregando ? 'animate-spin' : ''" /> recarregar
        </Button>
        <Button v-if="canEdit" size="sm" class="ml-auto" @click="openNew">
          <Plus class="size-4 mr-1" /> Novo caso
        </Button>
      </div>

      <p class="text-sm text-muted-foreground">
        Casos de pós-venda a acompanhar. Preencha os status do Meli no caso e o sistema
        sugere os Status Bling que a planilha já viu pra aquela combinação — a decisão final é sua.
      </p>

      <!-- Busca + filtros -->
      <div class="flex flex-wrap items-center gap-2">
        <div class="relative">
          <Search class="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
          <input
            v-model="search"
            class="h-9 w-72 rounded-md border bg-background pl-8 pr-3 text-sm"
            placeholder="buscar pedido, conta, rastreio, chamado…"
          />
        </div>
        <select v-model="contaFilter" class="h-9 rounded-md border bg-background px-2 text-sm">
          <option value="all">todas contas</option>
          <option v-for="c in contas" :key="c" :value="c">{{ c }}</option>
        </select>
        <select v-model="statusBlingFilter" class="h-9 rounded-md border bg-background px-2 text-sm">
          <option value="all">todos status bling</option>
          <option v-for="s in statusBlings" :key="s" :value="s">{{ s }}</option>
        </select>
        <div class="flex items-center gap-1.5 h-9 rounded-md border bg-background px-2" title="Período da data">
          <span class="text-xs text-muted-foreground">de</span>
          <input
            v-model="dataInicioFilter"
            type="date"
            :max="dataFimFilter || undefined"
            title="Data inicial"
            class="bg-transparent text-sm focus:outline-none"
          />
          <span class="text-xs text-muted-foreground">até</span>
          <input
            v-model="dataFimFilter"
            type="date"
            :min="dataInicioFilter || undefined"
            title="Data final"
            class="bg-transparent text-sm focus:outline-none"
          />
        </div>
        <Button v-if="filtrosAtivos" size="sm" variant="ghost" @click="limparFiltros">
          <X class="size-4 mr-1" /> limpar
        </Button>
        <span class="text-xs text-muted-foreground ml-auto">
          {{ filteredRows.length }} de {{ rows.length }}
        </span>
      </div>

      <div v-if="error" class="text-sm text-red-500">erro: {{ error }}</div>

      <!-- Desktop table -->
      <div class="hidden md:block border rounded-md overflow-x-auto">
        <table class="w-full text-sm min-w-[1200px] border-collapse [&_th]:border [&_td]:border [&_th]:border-border [&_td]:border-border">
          <thead class="bg-muted/40 text-left">
            <tr class="whitespace-nowrap">
              <th class="px-3 py-2">Data</th>
              <th class="px-3 py-2">Pedido Bling</th>
              <th class="px-3 py-2">Pedido Marketplace</th>
              <th class="px-3 py-2">Plataforma</th>
              <th class="px-3 py-2">Conta</th>
              <th class="px-3 py-2">Status Plataforma</th>
              <th class="px-3 py-2">Rastreio</th>
              <th class="px-3 py-2">Localização</th>
              <th class="px-3 py-2">Divergência</th>
              <th class="px-3 py-2">Status Bling</th>
              <th class="px-3 py-2">Chamado</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="c in pagedRows"
              :key="c.id"
              class="border-t cursor-pointer"
              :class="precisaAtencao(c) ? 'bg-red-50 hover:bg-red-100 dark:bg-red-950/40' : 'hover:bg-muted/20'"
              @click="openEdit(c)"
            >
              <td class="px-3 py-2 whitespace-nowrap">{{ fmtDate(c.data) }}</td>
              <td class="px-3 py-2 whitespace-nowrap font-medium">{{ c.pedido_bling || '—' }}</td>
              <td class="px-3 py-2 whitespace-nowrap">{{ c.pedido_marketplace || '—' }}</td>
              <td class="px-3 py-2 whitespace-nowrap">{{ c.plataforma || '—' }}</td>
              <td class="px-3 py-2 whitespace-nowrap">{{ c.conta || '—' }}</td>
              <td class="px-3 py-2 text-muted-foreground text-xs max-w-[280px] break-words">
                <div class="flex items-start gap-1.5">
                  <span
                    class="flex-1 break-words"
                    :class="statusTooltip(c) ? 'cursor-help' : ''"
                    :title="statusTooltip(c)"
                  >{{ assinatura(c) || '—' }}</span>
                  <button
                    v-if="assinatura(c)"
                    class="shrink-0 text-muted-foreground hover:text-foreground"
                    title="Copiar chave"
                    @click.stop="copiarChave(c)"
                  >
                    <Copy class="size-3.5" />
                  </button>
                  <button
                    v-if="canEdit && isMl(c) && c.pedido_marketplace"
                    class="shrink-0 text-muted-foreground hover:text-foreground disabled:opacity-50"
                    title="Atualizar status do Meli"
                    :disabled="refreshingMeli.has(c.id)"
                    @click.stop="atualizarMeli(c)"
                  >
                    <RefreshCw class="size-3.5" :class="refreshingMeli.has(c.id) ? 'animate-spin' : ''" />
                  </button>
                  <button
                    v-if="canEdit && isShopee(c) && c.pedido_marketplace"
                    class="shrink-0 text-muted-foreground hover:text-foreground disabled:opacity-50"
                    title="Atualizar status da Shopee"
                    :disabled="refreshingMeli.has(c.id)"
                    @click.stop="atualizarShopee(c)"
                  >
                    <RefreshCw class="size-3.5" :class="refreshingMeli.has(c.id) ? 'animate-spin' : ''" />
                  </button>
                  <button
                    v-if="canEdit && isTiktok(c) && c.pedido_marketplace"
                    class="shrink-0 text-muted-foreground hover:text-foreground disabled:opacity-50"
                    title="Atualizar status do TikTok"
                    :disabled="refreshingMeli.has(c.id)"
                    @click.stop="atualizarTiktok(c)"
                  >
                    <RefreshCw class="size-3.5" :class="refreshingMeli.has(c.id) ? 'animate-spin' : ''" />
                  </button>
                  <button
                    v-if="canEdit && isAmazon(c) && c.pedido_marketplace"
                    class="shrink-0 text-muted-foreground hover:text-foreground disabled:opacity-50"
                    title="Atualizar status da Amazon"
                    :disabled="refreshingMeli.has(c.id)"
                    @click.stop="atualizarAmazon(c)"
                  >
                    <RefreshCw class="size-3.5" :class="refreshingMeli.has(c.id) ? 'animate-spin' : ''" />
                  </button>
                </div>
              </td>
              <td class="px-3 py-2 whitespace-nowrap">{{ c.rastreio || '—' }}</td>
              <td class="px-3 py-2 whitespace-nowrap">{{ c.localizacao || '—' }}</td>
              <td class="px-3 py-2 text-xs max-w-[280px] break-words">
                <span v-if="c.divergencia" class="text-amber-700 dark:text-amber-400" :title="c.divergencia">{{ c.divergencia }}</span>
                <span v-else class="text-muted-foreground">—</span>
              </td>
              <td class="px-3 py-2 whitespace-nowrap">
                <div class="flex items-center gap-1.5">
                  <span v-if="c.status_bling" class="text-xs px-2 py-0.5 rounded border border-primary/50">{{ c.status_bling }}</span>
                  <span v-else class="text-muted-foreground">—</span>
                  <button
                    v-if="canEdit && c.pedido_bling && temStatusBlingAcao(c)"
                    class="shrink-0 text-muted-foreground hover:text-foreground disabled:opacity-50"
                    title="Alterar a situação do pedido no Bling conforme a regra da aba Status"
                    :disabled="aplicandoStatus.has(c.id)"
                    @click.stop="aplicarStatusBling(c)"
                  >
                    <ArrowLeftRight class="size-3.5" :class="aplicandoStatus.has(c.id) ? 'animate-pulse' : ''" />
                  </button>
                  <button
                    v-if="canEdit && c.pedido_bling && c.acao_resumo.includes('Mensagem Bling')"
                    class="shrink-0 text-muted-foreground hover:text-foreground disabled:opacity-50"
                    title="Aplicar Mensagem Bling nas Observações do pedido"
                    :disabled="aplicandoBling.has(c.id)"
                    @click.stop="aplicarMensagemBling(c)"
                  >
                    <NotebookPen class="size-3.5" :class="aplicandoBling.has(c.id) ? 'animate-pulse' : ''" />
                  </button>
                  <button
                    v-if="canEdit && c.pedido_marketplace && c.acao_resumo.includes('Mensagem Threema')"
                    class="shrink-0 text-muted-foreground hover:text-foreground disabled:opacity-50"
                    title="Enviar aviso Threema deste pedido (com pedido e loja)"
                    :disabled="refreshingMeli.has(c.id)"
                    @click.stop="enviarThreemaPedido(c)"
                  >
                    <MessageCircle class="size-3.5" :class="refreshingMeli.has(c.id) ? 'animate-pulse' : ''" />
                  </button>
                </div>
              </td>
              <td class="px-3 py-2 whitespace-nowrap">
                <div class="flex items-center gap-1.5">
                  <span class="flex-1">{{ c.chamado || '—' }}</span>
                  <button
                    v-if="canEdit && isMl(c) && c.pedido_marketplace && c.acao_resumo.includes('Abrir chamado')"
                    class="shrink-0 inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded border hover:bg-muted/40 disabled:opacity-50"
                    title="Enviar chamado direto pro Mercado Livre"
                    :disabled="sendingChamado.has(c.id)"
                    @click.stop="enviarChamado(c)"
                  >
                    <Send class="size-3" :class="sendingChamado.has(c.id) ? 'animate-pulse' : ''" />
                    enviar
                  </button>
                </div>
              </td>
            </tr>
            <tr v-if="!loading && filteredRows.length === 0">
              <td colspan="11" class="px-3 py-6 text-center text-muted-foreground">
                {{ rows.length === 0 ? 'nenhum caso' : 'nenhum caso com esses filtros' }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Mobile cards -->
      <div class="md:hidden space-y-2">
        <div
          v-for="c in pagedRows"
          :key="c.id"
          class="border rounded-md p-3 space-y-2 cursor-pointer"
          :class="precisaAtencao(c) ? 'border-red-300 bg-red-50 hover:bg-red-100 dark:bg-red-950/40' : 'hover:bg-muted/20'"
          @click="openEdit(c)"
        >
          <div class="flex items-start gap-2">
            <div class="flex-1 min-w-0">
              <div class="font-medium truncate">{{ c.pedido_bling || '—' }}</div>
              <div class="text-xs text-muted-foreground truncate">{{ c.plataforma || '—' }} · {{ c.conta || '—' }}</div>
            </div>
            <span v-if="c.status_bling" class="text-[10px] px-1.5 py-0.5 rounded border border-primary/50 shrink-0">{{ c.status_bling }}</span>
          </div>
          <div v-if="assinatura(c)" class="flex items-start gap-1.5 text-xs text-muted-foreground break-words">
            <span class="flex-1 break-words" :title="statusTooltip(c)">{{ assinatura(c) }}</span>
            <button class="shrink-0 hover:text-foreground" title="Copiar chave" @click.stop="copiarChave(c)">
              <Copy class="size-3.5" />
            </button>
          </div>
          <div class="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
            <div><span class="text-muted-foreground">Data:</span> {{ fmtDate(c.data) }}</div>
            <div><span class="text-muted-foreground">Rastreio:</span> {{ c.rastreio || '—' }}</div>
            <div><span class="text-muted-foreground">Localização:</span> {{ c.localizacao || '—' }}</div>
            <div><span class="text-muted-foreground">Chamado:</span> {{ c.chamado || '—' }}</div>
          </div>
          <div
            v-if="c.divergencia"
            class="text-xs text-amber-700 dark:text-amber-400 border border-amber-300 dark:border-amber-700 rounded px-2 py-1"
          >
            <span class="font-medium">Divergência:</span> {{ c.divergencia }}
          </div>
          <button
            v-if="canEdit && isMl(c) && c.pedido_marketplace && c.acao_resumo.includes('Abrir chamado')"
            class="inline-flex items-center gap-1 text-xs px-2 py-1 rounded border hover:bg-muted/40 disabled:opacity-50"
            :disabled="sendingChamado.has(c.id)"
            @click.stop="enviarChamado(c)"
          >
            <Send class="size-3" :class="sendingChamado.has(c.id) ? 'animate-pulse' : ''" />
            enviar chamado
          </button>
          <button
            v-if="canEdit && c.pedido_bling && temStatusBlingAcao(c)"
            class="inline-flex items-center gap-1 text-xs px-2 py-1 rounded border hover:bg-muted/40 disabled:opacity-50"
            :disabled="aplicandoStatus.has(c.id)"
            @click.stop="aplicarStatusBling(c)"
          >
            <ArrowLeftRight class="size-3" :class="aplicandoStatus.has(c.id) ? 'animate-pulse' : ''" />
            Status Bling
          </button>
          <button
            v-if="canEdit && c.pedido_bling && c.acao_resumo.includes('Mensagem Bling')"
            class="inline-flex items-center gap-1 text-xs px-2 py-1 rounded border hover:bg-muted/40 disabled:opacity-50"
            :disabled="aplicandoBling.has(c.id)"
            @click.stop="aplicarMensagemBling(c)"
          >
            <NotebookPen class="size-3" :class="aplicandoBling.has(c.id) ? 'animate-pulse' : ''" />
            Mensagem Bling
          </button>
          <button
            v-if="canEdit && c.pedido_marketplace && c.acao_resumo.includes('Mensagem Threema')"
            class="inline-flex items-center gap-1 text-xs px-2 py-1 rounded border hover:bg-muted/40 disabled:opacity-50"
            :disabled="refreshingMeli.has(c.id)"
            @click.stop="enviarThreemaPedido(c)"
          >
            <MessageCircle class="size-3" :class="refreshingMeli.has(c.id) ? 'animate-pulse' : ''" />
            Enviar Threema
          </button>
        </div>
        <div v-if="!loading && filteredRows.length === 0" class="text-center text-sm text-muted-foreground py-6 border rounded-md">
          {{ rows.length === 0 ? 'nenhum caso' : 'nenhum caso com esses filtros' }}
        </div>
      </div>

      <!-- Paginação -->
      <div v-if="filteredRows.length > PAGE_SIZE" class="flex items-center justify-between gap-3 pt-1">
        <span class="text-xs text-muted-foreground">
          {{ pageStart }}–{{ pageEnd }} de {{ filteredRows.length }}
        </span>
        <div class="flex items-center gap-1">
          <Button size="sm" variant="outline" :disabled="page <= 1" @click="goToPage(page - 1)">
            <ChevronLeft class="size-4" />
          </Button>
          <span class="text-xs text-muted-foreground px-2 whitespace-nowrap">
            página {{ page }} de {{ totalPages }}
          </span>
          <Button size="sm" variant="outline" :disabled="page >= totalPages" @click="goToPage(page + 1)">
            <ChevronRight class="size-4" />
          </Button>
        </div>
      </div>
    </template>

    <!-- ============ ABA STATUS ============ -->
    <template v-else>
      <div class="flex flex-wrap items-center gap-3">
        <Button size="sm" variant="ghost" :disabled="statusLoading" @click="refreshStatus">
          <RefreshCw class="size-4 mr-1" /> recarregar
        </Button>
        <Button v-if="canEdit" size="sm" class="ml-auto" @click="openStatusForm">
          <Plus class="size-4 mr-1" /> Novo status
        </Button>
      </div>

      <p class="text-sm text-muted-foreground">
        Clique numa célula pra editar só aquele campo. Os campos ficam vazios pra o operador
        preencher à mão. Na "Mensagem do Chamado" dá pra anexar imagens no botão de foto (ou colar uma URL no texto).
      </p>

      <div v-if="statusError" class="text-sm text-red-500">erro: {{ statusError }}</div>

      <!-- Desktop table (edição inline por célula) -->
      <div class="hidden md:block border rounded-md overflow-x-auto">
        <table class="w-full text-sm min-w-[760px] border-collapse [&_th]:border [&_td]:border [&_th]:border-border [&_td]:border-border">
          <thead class="bg-muted/40 text-left">
            <tr class="whitespace-nowrap">
              <th class="px-3 py-2">Plataforma</th>
              <th class="px-3 py-2">Status Plataforma</th>
              <th class="px-3 py-2">Status Atual</th>
              <th class="px-3 py-2">Alterar Status Bling</th>
              <th class="px-3 py-2">Monitoramento</th>
              <th class="px-3 py-2">Abrir Chamado</th>
              <th class="px-3 py-2">Abrir Reembolso</th>
              <th class="px-3 py-2">Mensagem do Chamado</th>
              <th class="px-3 py-2">Mensagem Bling</th>
              <th class="px-3 py-2">Mensagem Threema</th>
              <th v-if="canEdit" class="px-3 py-2 w-10"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in statusRows" :key="s.id" class="border-t hover:bg-muted/20">
              <!-- Plataforma -->
              <td class="px-2 py-1 whitespace-nowrap align-top" @click="startEdit(s, 'plataforma')">
                <input
                  v-if="isEditing(s, 'plataforma')"
                  v-model="editValue"
                  autofocus
                  placeholder="vazio = geral"
                  class="w-40 rounded border bg-background px-1.5 py-1 text-sm"
                  @blur="commitEdit(s)"
                  @keydown.enter.prevent="commitEdit(s)"
                  @keydown.esc="cancelEdit"
                />
                <span v-else :class="[canEdit ? 'cursor-text' : '', s.plataforma ? '' : 'text-muted-foreground']">{{ s.plataforma || '—' }}</span>
              </td>
              <!-- Status Plataforma -->
              <td class="px-2 py-1 align-top" @click="startEdit(s, 'status_plataforma')">
                <input
                  v-if="isEditing(s, 'status_plataforma')"
                  v-model="editValue"
                  autofocus
                  class="w-64 rounded border bg-background px-1.5 py-1 text-sm"
                  @blur="commitEdit(s)"
                  @keydown.enter.prevent="commitEdit(s)"
                  @keydown.esc="cancelEdit"
                />
                <span v-else :class="[canEdit ? 'cursor-text' : '', s.status_plataforma ? 'font-medium' : 'text-muted-foreground']">{{ s.status_plataforma || '—' }}</span>
              </td>
              <!-- Status Atual (dropdown com os status conhecidos do Bling) -->
              <td class="px-2 py-1 whitespace-nowrap align-top" @click="startEdit(s, 'status_atual')">
                <select
                  v-if="isEditing(s, 'status_atual')"
                  v-model="editValue"
                  autofocus
                  class="w-44 rounded border bg-background px-1.5 py-1 text-sm"
                  @change="commitEdit(s)"
                  @blur="commitEdit(s)"
                  @keydown.esc="cancelEdit"
                >
                  <option value="">— vazio —</option>
                  <option v-for="opt in opcoes.status_bling_options" :key="opt" :value="opt">{{ opt }}</option>
                </select>
                <template v-else>
                  <span v-if="s.status_atual" class="text-xs px-2 py-0.5 rounded border border-border" :class="canEdit ? 'cursor-pointer' : ''">{{ s.status_atual }}</span>
                  <span v-else class="text-muted-foreground" :class="canEdit ? 'cursor-pointer' : ''">—</span>
                </template>
              </td>
              <!-- Alterar Status Bling (dropdown com os status conhecidos do Bling) -->
              <td class="px-2 py-1 whitespace-nowrap align-top" @click="startEdit(s, 'alterar_status_bling')">
                <select
                  v-if="isEditing(s, 'alterar_status_bling')"
                  v-model="editValue"
                  autofocus
                  class="w-44 rounded border bg-background px-1.5 py-1 text-sm"
                  @change="commitEdit(s)"
                  @blur="commitEdit(s)"
                  @keydown.esc="cancelEdit"
                >
                  <option value="">— não altera —</option>
                  <option v-for="opt in opcoes.status_bling_options" :key="opt" :value="opt">{{ opt }}</option>
                </select>
                <template v-else>
                  <span v-if="s.alterar_status_bling" class="text-xs px-2 py-0.5 rounded border border-primary/50" :class="canEdit ? 'cursor-pointer' : ''">{{ s.alterar_status_bling }}</span>
                  <span v-else class="text-muted-foreground" :class="canEdit ? 'cursor-pointer' : ''">—</span>
                </template>
              </td>
              <!-- Monitoramento (toggle direto) -->
              <td class="px-3 py-1 whitespace-nowrap align-top">
                <label class="inline-flex items-center gap-1.5" :class="canEdit ? 'cursor-pointer' : ''">
                  <input
                    type="checkbox"
                    :checked="s.monitoramento"
                    :disabled="!canEdit || statusBusy.has(s.id)"
                    class="size-4"
                    @change="toggleStatusBool(s, 'monitoramento')"
                  />
                  <span :class="s.monitoramento ? 'text-emerald-500' : 'text-muted-foreground'">{{ s.monitoramento ? 'Sim' : 'Não' }}</span>
                </label>
              </td>
              <!-- Abrir Chamado (toggle direto) -->
              <td class="px-3 py-1 whitespace-nowrap align-top">
                <label class="inline-flex items-center gap-1.5" :class="canEdit ? 'cursor-pointer' : ''">
                  <input
                    type="checkbox"
                    :checked="s.abrir_chamado"
                    :disabled="!canEdit || statusBusy.has(s.id)"
                    class="size-4"
                    @change="toggleStatusBool(s, 'abrir_chamado')"
                  />
                  <span :class="s.abrir_chamado ? 'text-emerald-500' : 'text-muted-foreground'">{{ s.abrir_chamado ? 'Sim' : 'Não' }}</span>
                </label>
              </td>
              <!-- Abrir Reembolso (toggle direto) -->
              <td class="px-3 py-1 whitespace-nowrap align-top">
                <label class="inline-flex items-center gap-1.5" :class="canEdit ? 'cursor-pointer' : ''">
                  <input
                    type="checkbox"
                    :checked="s.abrir_reembolso"
                    :disabled="!canEdit || statusBusy.has(s.id)"
                    class="size-4"
                    @change="toggleStatusBool(s, 'abrir_reembolso')"
                  />
                  <span :class="s.abrir_reembolso ? 'text-emerald-500' : 'text-muted-foreground'">{{ s.abrir_reembolso ? 'Sim' : 'Não' }}</span>
                </label>
              </td>
              <!-- Mensagem do Chamado (textarea inline) -->
              <td class="px-2 py-1 text-xs max-w-[340px] align-top" @click="startEdit(s, 'mensagem_chamado')">
                <textarea
                  v-if="isEditing(s, 'mensagem_chamado')"
                  v-model="editValue"
                  autofocus
                  rows="3"
                  placeholder="texto, link ou foto (cole a URL)"
                  class="w-80 rounded border bg-background px-1.5 py-1 text-sm resize-y"
                  @blur="commitEdit(s)"
                  @keydown.esc="cancelEdit"
                />
                <span v-else class="break-words whitespace-pre-line" :class="[canEdit ? 'cursor-text' : '', s.mensagem_chamado ? 'text-muted-foreground' : 'text-muted-foreground/60']">{{ s.mensagem_chamado || '—' }}</span>
                <!-- Anexos de imagem -->
                <div class="mt-1.5 flex flex-wrap items-center gap-1.5" @click.stop>
                  <div v-for="a in s.anexos || []" :key="a.id" class="relative group">
                    <a :href="anexoUrl(a.id)" target="_blank" rel="noopener" :title="a.filename">
                      <img :src="anexoUrl(a.id)" :alt="a.filename" class="size-10 rounded border object-cover" />
                    </a>
                    <button
                      v-if="canEdit"
                      class="absolute -right-1 -top-1 hidden rounded-full bg-red-500 p-0.5 text-white group-hover:block disabled:opacity-50"
                      title="Remover imagem"
                      :disabled="statusBusy.has(s.id)"
                      @click="removeAnexo(s, a.id)"
                    >
                      <X class="size-3" />
                    </button>
                  </div>
                  <label
                    v-if="canEdit"
                    class="inline-flex size-10 cursor-pointer items-center justify-center rounded border border-dashed text-muted-foreground hover:text-foreground"
                    :class="statusBusy.has(s.id) ? 'pointer-events-none opacity-50' : ''"
                    title="Anexar imagem"
                  >
                    <ImagePlus class="size-4" />
                    <input type="file" accept="image/*" class="hidden" :disabled="statusBusy.has(s.id)" @change="uploadAnexo(s, $event)" />
                  </label>
                </div>
              </td>
              <!-- Mensagem Bling (textarea inline) -->
              <td class="px-2 py-1 text-xs max-w-[300px] align-top" @click="startEdit(s, 'mensagem_bling')">
                <textarea
                  v-if="isEditing(s, 'mensagem_bling')"
                  v-model="editValue"
                  autofocus
                  rows="3"
                  placeholder="texto a colar no Bling"
                  class="w-72 rounded border bg-background px-1.5 py-1 text-sm resize-y"
                  @blur="commitEdit(s)"
                  @keydown.esc="cancelEdit"
                />
                <span v-else class="break-words whitespace-pre-line" :class="[canEdit ? 'cursor-text' : '', s.mensagem_bling ? 'text-muted-foreground' : 'text-muted-foreground/60']">{{ s.mensagem_bling || '—' }}</span>
              </td>
              <!-- Mensagem Threema (textarea inline) -->
              <td class="px-2 py-1 text-xs max-w-[300px] align-top" @click="startEdit(s, 'mensagem_threema')">
                <textarea
                  v-if="isEditing(s, 'mensagem_threema')"
                  v-model="editValue"
                  autofocus
                  rows="3"
                  placeholder="mensagem p/ notificar as pessoas"
                  class="w-72 rounded border bg-background px-1.5 py-1 text-sm resize-y"
                  @blur="commitEdit(s)"
                  @keydown.esc="cancelEdit"
                />
                <span v-else class="break-words whitespace-pre-line" :class="[canEdit ? 'cursor-text' : '', s.mensagem_threema ? 'text-muted-foreground' : 'text-muted-foreground/60']">{{ s.mensagem_threema || '—' }}</span>
                <div v-if="canEdit && s.mensagem_threema" class="mt-1 flex items-center gap-1">
                  <button
                    class="inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] text-primary hover:bg-primary/10 disabled:opacity-50"
                    title="Enviar Mensagem Threema aos destinatários"
                    :disabled="statusBusy.has(s.id)"
                    @click.stop="enviarThreema(s)"
                  >
                    <Send class="size-3" /> Enviar
                  </button>
                  <button
                    class="inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] text-muted-foreground hover:bg-muted disabled:opacity-50"
                    :title="s.threema_recipients ? `Destinatários salvos: ${s.threema_recipients}` : 'Escolher quem recebe (salvar na regra)'"
                    :disabled="statusBusy.has(s.id)"
                    @click.stop="escolherDestinatarios(s)"
                  >
                    <UserRound class="size-3" />
                  </button>
                </div>
              </td>
              <!-- Ação -->
              <td v-if="canEdit" class="px-2 py-1 align-top text-center">
                <button
                  class="text-muted-foreground hover:text-red-500 disabled:opacity-50"
                  title="Remover"
                  :disabled="statusBusy.has(s.id)"
                  @click="removeStatusRow(s)"
                >
                  <Trash2 class="size-4" />
                </button>
              </td>
            </tr>
            <tr v-if="!statusLoading && statusRows.length === 0">
              <td :colspan="canEdit ? 11 : 10" class="px-3 py-6 text-center text-muted-foreground">nenhum status</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Mobile cards (campos editáveis diretos) -->
      <div class="md:hidden space-y-2">
        <div v-for="s in statusRows" :key="s.id" class="border rounded-md p-3 space-y-2">
          <div class="flex items-start gap-2">
            <div class="flex-1 min-w-0 text-xs text-muted-foreground">Status</div>
            <button
              v-if="canEdit"
              class="text-muted-foreground hover:text-red-500 disabled:opacity-50 shrink-0"
              :disabled="statusBusy.has(s.id)"
              @click="removeStatusRow(s)"
            >
              <Trash2 class="size-4" />
            </button>
          </div>
          <div>
            <label class="text-xs text-muted-foreground">Plataforma</label>
            <input
              :value="s.plataforma || ''"
              :disabled="!canEdit || statusBusy.has(s.id)"
              placeholder="vazio = geral"
              class="w-full rounded border bg-background px-2 py-1 text-sm"
              @change="patchStatusField(s.id, { plataforma: ($event.target as HTMLInputElement).value.trim() || null })"
            />
          </div>
          <div>
            <label class="text-xs text-muted-foreground">Status Plataforma</label>
            <input
              :value="s.status_plataforma || ''"
              :disabled="!canEdit || statusBusy.has(s.id)"
              class="w-full rounded border bg-background px-2 py-1 text-sm"
              @change="patchStatusField(s.id, { status_plataforma: ($event.target as HTMLInputElement).value.trim() || null })"
            />
          </div>
          <div>
            <label class="text-xs text-muted-foreground">Status Atual</label>
            <select
              :value="s.status_atual || ''"
              :disabled="!canEdit || statusBusy.has(s.id)"
              class="w-full rounded border bg-background px-2 py-1 text-sm"
              @change="patchStatusField(s.id, { status_atual: ($event.target as HTMLSelectElement).value || null })"
            >
              <option value="">— vazio —</option>
              <option v-for="opt in opcoes.status_bling_options" :key="opt" :value="opt">{{ opt }}</option>
            </select>
          </div>
          <div>
            <label class="text-xs text-muted-foreground">Alterar Status Bling</label>
            <select
              :value="s.alterar_status_bling || ''"
              :disabled="!canEdit || statusBusy.has(s.id)"
              class="w-full rounded border bg-background px-2 py-1 text-sm"
              @change="patchStatusField(s.id, { alterar_status_bling: ($event.target as HTMLSelectElement).value || null })"
            >
              <option value="">— não altera —</option>
              <option v-for="opt in opcoes.status_bling_options" :key="opt" :value="opt">{{ opt }}</option>
            </select>
          </div>
          <div class="flex flex-wrap items-center gap-4">
            <label class="flex items-center gap-2 text-sm">
              <input type="checkbox" :checked="s.monitoramento" :disabled="!canEdit || statusBusy.has(s.id)" class="size-4" @change="toggleStatusBool(s, 'monitoramento')" />
              Monitoramento
            </label>
            <label class="flex items-center gap-2 text-sm">
              <input type="checkbox" :checked="s.abrir_chamado" :disabled="!canEdit || statusBusy.has(s.id)" class="size-4" @change="toggleStatusBool(s, 'abrir_chamado')" />
              Abrir chamado
            </label>
            <label class="flex items-center gap-2 text-sm">
              <input type="checkbox" :checked="s.abrir_reembolso" :disabled="!canEdit || statusBusy.has(s.id)" class="size-4" @change="toggleStatusBool(s, 'abrir_reembolso')" />
              Abrir reembolso
            </label>
          </div>
          <div>
            <label class="text-xs text-muted-foreground">Mensagem do Chamado</label>
            <textarea
              :value="s.mensagem_chamado || ''"
              :disabled="!canEdit || statusBusy.has(s.id)"
              rows="2"
              placeholder="texto, link ou foto (cole a URL)"
              class="w-full rounded border bg-background px-2 py-1 text-sm resize-y"
              @change="patchStatusField(s.id, { mensagem_chamado: ($event.target as HTMLTextAreaElement).value.trim() || null })"
            />
            <div class="mt-1.5 flex flex-wrap items-center gap-1.5">
              <div v-for="a in s.anexos || []" :key="a.id" class="relative">
                <a :href="anexoUrl(a.id)" target="_blank" rel="noopener" :title="a.filename">
                  <img :src="anexoUrl(a.id)" :alt="a.filename" class="size-12 rounded border object-cover" />
                </a>
                <button
                  v-if="canEdit"
                  class="absolute -right-1 -top-1 rounded-full bg-red-500 p-0.5 text-white disabled:opacity-50"
                  title="Remover imagem"
                  :disabled="statusBusy.has(s.id)"
                  @click="removeAnexo(s, a.id)"
                >
                  <X class="size-3" />
                </button>
              </div>
              <label
                v-if="canEdit"
                class="inline-flex size-12 cursor-pointer items-center justify-center rounded border border-dashed text-muted-foreground"
                :class="statusBusy.has(s.id) ? 'pointer-events-none opacity-50' : ''"
                title="Anexar imagem"
              >
                <ImagePlus class="size-5" />
                <input type="file" accept="image/*" class="hidden" :disabled="statusBusy.has(s.id)" @change="uploadAnexo(s, $event)" />
              </label>
            </div>
          </div>
          <div>
            <label class="text-xs text-muted-foreground">Mensagem Bling</label>
            <textarea
              :value="s.mensagem_bling || ''"
              :disabled="!canEdit || statusBusy.has(s.id)"
              rows="2"
              placeholder="texto a colar no Bling"
              class="w-full rounded border bg-background px-2 py-1 text-sm resize-y"
              @change="patchStatusField(s.id, { mensagem_bling: ($event.target as HTMLTextAreaElement).value.trim() || null })"
            />
          </div>
          <div>
            <label class="text-xs text-muted-foreground">Mensagem Threema</label>
            <textarea
              :value="s.mensagem_threema || ''"
              :disabled="!canEdit || statusBusy.has(s.id)"
              rows="2"
              placeholder="mensagem p/ notificar as pessoas"
              class="w-full rounded border bg-background px-2 py-1 text-sm resize-y"
              @change="patchStatusField(s.id, { mensagem_threema: ($event.target as HTMLTextAreaElement).value.trim() || null })"
            />
            <div v-if="canEdit && s.mensagem_threema" class="mt-1 flex items-center gap-1">
              <button
                class="inline-flex items-center gap-1 rounded border px-2 py-1 text-xs text-primary hover:bg-primary/10 disabled:opacity-50"
                :disabled="statusBusy.has(s.id)"
                @click="enviarThreema(s)"
              >
                <Send class="size-3.5" /> Enviar Threema
              </button>
              <button
                class="inline-flex items-center gap-1 rounded border px-2 py-1 text-xs text-muted-foreground hover:bg-muted disabled:opacity-50"
                :title="s.threema_recipients ? `Destinatários salvos: ${s.threema_recipients}` : 'Escolher quem recebe (salvar na regra)'"
                :disabled="statusBusy.has(s.id)"
                @click="escolherDestinatarios(s)"
              >
                <UserRound class="size-3.5" />
              </button>
            </div>
          </div>
        </div>
        <div v-if="!statusLoading && statusRows.length === 0" class="text-center text-sm text-muted-foreground py-6 border rounded-md">
          nenhum status
        </div>
      </div>
    </template>

    <!-- Modal "Novo status" (aba Status) -->
    <div v-if="showStatusForm" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" @click.self="showStatusForm = false">
      <div class="bg-background border rounded-lg w-full max-w-lg p-5 space-y-4 max-h-[90vh] overflow-y-auto">
        <div class="flex items-center">
          <h2 class="text-lg font-semibold">Novo status</h2>
          <Button class="ml-auto" size="sm" variant="ghost" @click="showStatusForm = false">
            <X class="size-4" />
          </Button>
        </div>

        <div>
          <Label>Plataforma</Label>
          <Input v-model="statusForm.plataforma" placeholder="vazio = geral" />
        </div>
        <div>
          <Label>Status Plataforma</Label>
          <Input v-model="statusForm.status_plataforma" />
        </div>
        <div>
          <Label>Status Atual</Label>
          <select
            v-model="statusForm.status_atual"
            class="w-full rounded-md border bg-background px-2 py-1.5 text-sm"
          >
            <option value="">— vazio —</option>
            <option v-for="opt in opcoes.status_bling_options" :key="opt" :value="opt">{{ opt }}</option>
          </select>
        </div>
        <div>
          <Label>Alterar Status Bling</Label>
          <select
            v-model="statusForm.alterar_status_bling"
            class="w-full rounded-md border bg-background px-2 py-1.5 text-sm"
          >
            <option value="">— não altera —</option>
            <option v-for="opt in opcoes.status_bling_options" :key="opt" :value="opt">{{ opt }}</option>
          </select>
        </div>
        <div class="flex flex-wrap items-center gap-4">
          <label class="flex items-center gap-2 text-sm">
            <input v-model="statusForm.monitoramento" type="checkbox" class="size-4" />
            Monitoramento
          </label>
          <label class="flex items-center gap-2 text-sm">
            <input v-model="statusForm.abrir_chamado" type="checkbox" class="size-4" />
            Abrir chamado
          </label>
          <label class="flex items-center gap-2 text-sm">
            <input v-model="statusForm.abrir_reembolso" type="checkbox" class="size-4" />
            Abrir reembolso
          </label>
        </div>
        <div>
          <Label>Mensagem do Chamado</Label>
          <textarea
            v-model="statusForm.mensagem_chamado"
            rows="3"
            placeholder="texto ou link"
            class="w-full rounded-md border bg-background px-2 py-1.5 text-sm resize-y"
          />
          <p class="mt-1 text-xs text-muted-foreground">Depois de salvar, dá pra anexar imagens direto na linha.</p>
        </div>
        <div>
          <Label>Mensagem Bling</Label>
          <textarea
            v-model="statusForm.mensagem_bling"
            rows="3"
            placeholder="texto a colar no Bling"
            class="w-full rounded-md border bg-background px-2 py-1.5 text-sm resize-y"
          />
        </div>
        <div>
          <Label>Mensagem Threema</Label>
          <textarea
            v-model="statusForm.mensagem_threema"
            rows="3"
            placeholder="mensagem p/ notificar as pessoas"
            class="w-full rounded-md border bg-background px-2 py-1.5 text-sm resize-y"
          />
        </div>

        <div class="flex justify-end gap-2">
          <Button variant="ghost" @click="showStatusForm = false">Cancelar</Button>
          <Button :disabled="statusSaving" @click="saveStatusForm">
            {{ statusSaving ? 'Salvando…' : 'Salvar' }}
          </Button>
        </div>
      </div>
    </div>

    <!-- Modal: escolher destinatários da Mensagem Threema -->
    <div v-if="threemaModal.open" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" @click.self="threemaModal.open = false">
      <div class="bg-background border rounded-lg w-full max-w-sm p-5 space-y-4">
        <div class="flex items-center">
          <h2 class="text-lg font-semibold">{{ threemaModal.mode === 'salvar' ? 'Destinatários da regra' : 'Enviar Threema' }}</h2>
          <Button class="ml-auto" size="sm" variant="ghost" @click="threemaModal.open = false">
            <X class="size-4" />
          </Button>
        </div>
        <p class="text-sm text-muted-foreground">{{ threemaModal.mode === 'salvar' ? 'Escolha quem recebe o aviso desta regra (usado no envio e no automático).' : 'Escolha quem recebe a mensagem.' }}</p>
        <div v-if="!threemaDestinatarios.length" class="text-sm text-muted-foreground">
          Nenhum destinatário configurado.
        </div>
        <div v-else class="space-y-2">
          <label
            v-for="d in threemaDestinatarios"
            :key="d.id"
            class="flex items-center gap-2 text-sm rounded-md border px-3 py-2 cursor-pointer hover:bg-muted/50"
          >
            <input
              type="checkbox"
              class="size-4"
              :checked="threemaModal.selecionados.has(d.id)"
              @change="toggleThreemaDest(d.id)"
            />
            {{ d.nome }}
          </label>
        </div>
        <div class="flex justify-end gap-2">
          <Button variant="ghost" @click="threemaModal.open = false">Cancelar</Button>
          <Button
            :disabled="threemaModal.sending || !threemaModal.selecionados.size"
            @click="confirmarThreemaModal"
          >
            <component :is="threemaModal.mode === 'salvar' ? UserRound : Send" class="size-4 mr-1" />
            {{ threemaModal.sending
              ? (threemaModal.mode === 'salvar' ? 'Salvando…' : 'Enviando…')
              : (threemaModal.mode === 'salvar' ? 'Salvar' : 'Enviar') }}
          </Button>
        </div>
      </div>
    </div>

    <!-- Modal create/edit caso -->
    <div v-if="modalOpen" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" @click.self="closeModal">
      <div class="bg-background border rounded-lg w-full max-w-2xl p-5 space-y-4 max-h-[90vh] overflow-y-auto">
        <div class="flex items-center">
          <h2 class="text-lg font-semibold">{{ editingId ? 'Editar caso' : 'Novo caso' }}</h2>
          <Button class="ml-auto" size="sm" variant="ghost" @click="closeModal">
            <X class="size-4" />
          </Button>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div>
            <Label>Data</Label>
            <Input v-model="form.data" type="date" :disabled="!canEdit" />
          </div>
          <div>
            <Label>Pedido Bling</Label>
            <Input v-model="form.pedido_bling" :disabled="!canEdit" />
          </div>
          <div>
            <Label>Pedido Marketplace</Label>
            <Input v-model="form.pedido_marketplace" :disabled="!canEdit" />
          </div>
          <div>
            <Label>Plataforma</Label>
            <Input v-model="form.plataforma" placeholder="ex: Mercado Livre" :disabled="!canEdit" />
          </div>
          <div>
            <Label>Conta</Label>
            <Input v-model="form.conta" :disabled="!canEdit" />
          </div>
          <div>
            <Label>Rastreio</Label>
            <Input v-model="form.rastreio" placeholder="número de rastreio" :disabled="!canEdit" />
          </div>
          <div>
            <Label>Localização</Label>
            <Input v-model="form.localizacao" placeholder="ex: em trânsito / Correios" :disabled="!canEdit" />
          </div>
          <div>
            <Label>Chamado</Label>
            <Input v-model="form.chamado" placeholder="nº / referência do chamado" :disabled="!canEdit" />
          </div>
        </div>

        <!-- Status do Meli -->
        <div class="border rounded-md p-3 space-y-3">
          <div class="text-sm font-medium">Status do Meli</div>
          <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div v-for="f in opcoes.field_order" :key="f">
              <Label class="text-xs">{{ opcoes.field_labels[f] || f }}</Label>
              <select
                v-model="form.meli_status[f]"
                :disabled="!canEdit"
                class="w-full rounded-md border bg-background px-2 py-1.5 text-sm"
              >
                <option value="">—</option>
                <option v-for="opt in opcoes.field_options[f] || []" :key="opt" :value="opt">{{ opt }}</option>
              </select>
            </div>
          </div>
        </div>

        <!-- Sugestão -->
        <div class="border rounded-md p-3 space-y-2">
          <div class="text-sm font-medium">Sugestão de Status Bling</div>
          <div v-if="candidatos.length === 0" class="text-xs text-muted-foreground">
            preencha os status do Meli acima pra ver os candidatos
          </div>
          <div v-else class="flex flex-wrap gap-2">
            <button
              v-for="c in candidatos"
              :key="c.status_bling"
              type="button"
              :disabled="!canEdit"
              class="text-xs px-2 py-1 rounded border hover:bg-muted/40"
              :class="form.status_bling === c.status_bling ? 'border-primary bg-primary/10' : 'border-border'"
              @click="form.status_bling = c.status_bling"
            >
              {{ c.status_bling }} <span class="text-muted-foreground">({{ c.matches }})</span>
            </button>
          </div>
          <div>
            <Label class="text-xs">Status Bling (final)</Label>
            <Input v-model="form.status_bling" placeholder="clique num candidato ou digite" :disabled="!canEdit" />
          </div>
        </div>

        <div>
          <Label>Observação</Label>
          <textarea
            v-model="form.observacao"
            rows="2"
            :disabled="!canEdit"
            class="w-full rounded-md border bg-background px-2 py-1.5 text-sm resize-y"
          />
        </div>

        <div v-if="formErr" class="text-sm text-red-500">erro: {{ formErr }}</div>
        <div v-if="canEdit" class="flex justify-end gap-2">
          <Button v-if="editingId" variant="ghost" :disabled="saving" class="text-red-500 mr-auto" @click="remove">
            <Trash2 class="size-4 mr-1" /> remover
          </Button>
          <Button variant="ghost" :disabled="saving" @click="closeModal">cancelar</Button>
          <Button :disabled="saving" @click="save">
            {{ saving ? 'salvando…' : 'Salvar' }}
          </Button>
        </div>
      </div>
    </div>

  </div>
</template>
