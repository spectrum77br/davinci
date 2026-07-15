<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { Plus, RefreshCw, X, Trash2, Search, Send } from 'lucide-vue-next'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'logistica', action: 'view' },
})

const { api } = useApi()
const canEdit = useCan('logistica', 'edit')

const tab = ref<'logistica' | 'status'>('logistica')

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
  status_bling: string | null
  chamado: string | null
  observacao: string | null
  created_by: string | null
  created_at: string
  updated_at: string
}

type Opcoes = {
  field_order: string[]
  field_labels: Record<string, string>
  field_options: Record<string, string[]>
}

type Candidato = { status_bling: string; matches: number }

const rows = ref<Logistica[]>([])
const loading = ref(false)
const error = ref<string | null>(null)

const opcoes = ref<Opcoes>({ field_order: [], field_labels: {}, field_options: {} })

async function refresh() {
  loading.value = true
  error.value = null
  try {
    // Por enquanto a aba mostra só Mercado Livre; futuras abas Shopee/Amazon
    // vão trocar a plataforma. O backend filtra server-side.
    rows.value = await api<Logistica[]>('/api/logistica?plataforma=ml')
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    loading.value = false
  }
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

function isMl(c: Logistica): boolean {
  return ['mercado livre', 'mercadolivre', 'ml'].includes(
    (c.plataforma || '').trim().toLowerCase(),
  )
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

// ================= Aba Status =================
type LogisticaStatus = {
  id: string
  plataforma: string | null
  status_plataforma: string | null
  alterar_status_bling: string | null
  monitoramento: boolean
  abrir_chamado: boolean
  mensagem_chamado: string | null
  created_by: string | null
  created_at: string
  updated_at: string
}

// Campos de texto editáveis inline (mensagem_chamado usa textarea).
type StatusTextField =
  | 'plataforma'
  | 'status_plataforma'
  | 'alterar_status_bling'
  | 'mensagem_chamado'

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
  if (t === 'status' && !statusLoaded) refreshStatus()
})

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

async function toggleStatusBool(s: LogisticaStatus, field: 'monitoramento' | 'abrir_chamado') {
  if (!canEdit.value) return
  await patchStatusField(s.id, { [field]: !s[field] })
}

async function addStatusRow() {
  statusError.value = null
  try {
    const created = await api<LogisticaStatus>('/api/logistica/status', {
      method: 'POST',
      body: {},
    })
    statusRows.value.unshift(created)
  } catch (e: any) {
    statusError.value = e?.data?.detail?.code || e?.message || 'erro'
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
</script>

<template>
  <div class="space-y-4">
    <div class="flex flex-wrap items-center gap-3">
      <h1 class="text-2xl font-semibold">Logística</h1>
    </div>

    <!-- Tabs -->
    <div class="flex gap-1 border-b">
      <button
        type="button"
        class="px-4 py-2 text-sm font-medium border-b-2 -mb-px"
        :class="tab === 'logistica' ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'"
        @click="tab = 'logistica'"
      >
        Mercado Livre
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

    <!-- ============ ABA LOGÍSTICA ============ -->
    <template v-if="tab === 'logistica'">
      <div class="flex flex-wrap items-center gap-3">
        <Button size="sm" variant="ghost" :disabled="loading" @click="refresh">
          <RefreshCw class="size-4 mr-1" /> recarregar
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
        <table class="w-full text-sm min-w-[1050px] border-collapse [&_th]:border [&_td]:border [&_th]:border-border [&_td]:border-border">
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
              <th class="px-3 py-2">Status Bling</th>
              <th class="px-3 py-2">Chamado</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="c in filteredRows"
              :key="c.id"
              class="border-t hover:bg-muted/20 cursor-pointer"
              @click="openEdit(c)"
            >
              <td class="px-3 py-2 whitespace-nowrap">{{ fmtDate(c.data) }}</td>
              <td class="px-3 py-2 whitespace-nowrap font-medium">{{ c.pedido_bling || '—' }}</td>
              <td class="px-3 py-2 whitespace-nowrap">{{ c.pedido_marketplace || '—' }}</td>
              <td class="px-3 py-2 whitespace-nowrap">{{ c.plataforma || '—' }}</td>
              <td class="px-3 py-2 whitespace-nowrap">{{ c.conta || '—' }}</td>
              <td class="px-3 py-2 text-muted-foreground text-xs max-w-[280px] break-words">
                <div class="flex items-start gap-1.5">
                  <span class="flex-1 break-words">{{ assinatura(c) || '—' }}</span>
                  <button
                    v-if="canEdit && isMl(c) && c.pedido_marketplace"
                    class="shrink-0 text-muted-foreground hover:text-foreground disabled:opacity-50"
                    title="Atualizar status do Meli"
                    :disabled="refreshingMeli.has(c.id)"
                    @click.stop="atualizarMeli(c)"
                  >
                    <RefreshCw class="size-3.5" :class="refreshingMeli.has(c.id) ? 'animate-spin' : ''" />
                  </button>
                </div>
              </td>
              <td class="px-3 py-2 whitespace-nowrap">{{ c.rastreio || '—' }}</td>
              <td class="px-3 py-2 whitespace-nowrap">{{ c.localizacao || '—' }}</td>
              <td class="px-3 py-2 whitespace-nowrap">
                <span v-if="c.status_bling" class="text-xs px-2 py-0.5 rounded border border-primary/50">{{ c.status_bling }}</span>
                <span v-else class="text-muted-foreground">—</span>
              </td>
              <td class="px-3 py-2 whitespace-nowrap">
                <div class="flex items-center gap-1.5">
                  <span class="flex-1">{{ c.chamado || '—' }}</span>
                  <button
                    v-if="canEdit && isMl(c) && c.pedido_marketplace"
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
              <td colspan="10" class="px-3 py-6 text-center text-muted-foreground">
                {{ rows.length === 0 ? 'nenhum caso' : 'nenhum caso com esses filtros' }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Mobile cards -->
      <div class="md:hidden space-y-2">
        <div
          v-for="c in filteredRows"
          :key="c.id"
          class="border rounded-md p-3 space-y-2 hover:bg-muted/20 cursor-pointer"
          @click="openEdit(c)"
        >
          <div class="flex items-start gap-2">
            <div class="flex-1 min-w-0">
              <div class="font-medium truncate">{{ c.pedido_bling || '—' }}</div>
              <div class="text-xs text-muted-foreground truncate">{{ c.plataforma || '—' }} · {{ c.conta || '—' }}</div>
            </div>
            <span v-if="c.status_bling" class="text-[10px] px-1.5 py-0.5 rounded border border-primary/50 shrink-0">{{ c.status_bling }}</span>
          </div>
          <div v-if="assinatura(c)" class="text-xs text-muted-foreground break-words">{{ assinatura(c) }}</div>
          <div class="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
            <div><span class="text-muted-foreground">Data:</span> {{ fmtDate(c.data) }}</div>
            <div><span class="text-muted-foreground">Rastreio:</span> {{ c.rastreio || '—' }}</div>
            <div><span class="text-muted-foreground">Localização:</span> {{ c.localizacao || '—' }}</div>
            <div><span class="text-muted-foreground">Chamado:</span> {{ c.chamado || '—' }}</div>
          </div>
          <button
            v-if="canEdit && isMl(c) && c.pedido_marketplace"
            class="inline-flex items-center gap-1 text-xs px-2 py-1 rounded border hover:bg-muted/40 disabled:opacity-50"
            :disabled="sendingChamado.has(c.id)"
            @click.stop="enviarChamado(c)"
          >
            <Send class="size-3" :class="sendingChamado.has(c.id) ? 'animate-pulse' : ''" />
            enviar chamado
          </button>
        </div>
        <div v-if="!loading && filteredRows.length === 0" class="text-center text-sm text-muted-foreground py-6 border rounded-md">
          {{ rows.length === 0 ? 'nenhum caso' : 'nenhum caso com esses filtros' }}
        </div>
      </div>
    </template>

    <!-- ============ ABA STATUS ============ -->
    <template v-else>
      <div class="flex flex-wrap items-center gap-3">
        <Button size="sm" variant="ghost" :disabled="statusLoading" @click="refreshStatus">
          <RefreshCw class="size-4 mr-1" /> recarregar
        </Button>
        <Button v-if="canEdit" size="sm" class="ml-auto" @click="addStatusRow">
          <Plus class="size-4 mr-1" /> Novo status
        </Button>
      </div>

      <p class="text-sm text-muted-foreground">
        Clique numa célula pra editar só aquele campo. Os campos ficam vazios pra o operador
        preencher à mão. A "Mensagem do Chamado" pode conter texto, link ou foto (cole a URL).
      </p>

      <div v-if="statusError" class="text-sm text-red-500">erro: {{ statusError }}</div>

      <!-- Desktop table (edição inline por célula) -->
      <div class="hidden md:block border rounded-md overflow-x-auto">
        <table class="w-full text-sm min-w-[760px] border-collapse [&_th]:border [&_td]:border [&_th]:border-border [&_td]:border-border">
          <thead class="bg-muted/40 text-left">
            <tr class="whitespace-nowrap">
              <th class="px-3 py-2">Plataforma</th>
              <th class="px-3 py-2">Status Plataforma</th>
              <th class="px-3 py-2">Alterar Status Bling</th>
              <th class="px-3 py-2">Monitoramento</th>
              <th class="px-3 py-2">Abrir Chamado</th>
              <th class="px-3 py-2">Mensagem do Chamado</th>
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
              <!-- Alterar Status Bling -->
              <td class="px-2 py-1 whitespace-nowrap align-top" @click="startEdit(s, 'alterar_status_bling')">
                <input
                  v-if="isEditing(s, 'alterar_status_bling')"
                  v-model="editValue"
                  autofocus
                  placeholder="vazio = não altera"
                  class="w-44 rounded border bg-background px-1.5 py-1 text-sm"
                  @blur="commitEdit(s)"
                  @keydown.enter.prevent="commitEdit(s)"
                  @keydown.esc="cancelEdit"
                />
                <template v-else>
                  <span v-if="s.alterar_status_bling" class="text-xs px-2 py-0.5 rounded border border-primary/50" :class="canEdit ? 'cursor-text' : ''">{{ s.alterar_status_bling }}</span>
                  <span v-else class="text-muted-foreground" :class="canEdit ? 'cursor-text' : ''">—</span>
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
              <td :colspan="canEdit ? 7 : 6" class="px-3 py-6 text-center text-muted-foreground">nenhum status</td>
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
            <label class="text-xs text-muted-foreground">Alterar Status Bling</label>
            <input
              :value="s.alterar_status_bling || ''"
              :disabled="!canEdit || statusBusy.has(s.id)"
              placeholder="vazio = não altera"
              class="w-full rounded border bg-background px-2 py-1 text-sm"
              @change="patchStatusField(s.id, { alterar_status_bling: ($event.target as HTMLInputElement).value.trim() || null })"
            />
          </div>
          <div class="flex items-center gap-4">
            <label class="flex items-center gap-2 text-sm">
              <input type="checkbox" :checked="s.monitoramento" :disabled="!canEdit || statusBusy.has(s.id)" class="size-4" @change="toggleStatusBool(s, 'monitoramento')" />
              Monitoramento
            </label>
            <label class="flex items-center gap-2 text-sm">
              <input type="checkbox" :checked="s.abrir_chamado" :disabled="!canEdit || statusBusy.has(s.id)" class="size-4" @change="toggleStatusBool(s, 'abrir_chamado')" />
              Abrir chamado
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
          </div>
        </div>
        <div v-if="!statusLoading && statusRows.length === 0" class="text-center text-sm text-muted-foreground py-6 border rounded-md">
          nenhum status
        </div>
      </div>
    </template>

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
