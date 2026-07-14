<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { Plus, RefreshCw, X, Trash2 } from 'lucide-vue-next'

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
    rows.value = await api<Logistica[]>('/api/logistica')
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    loading.value = false
  }
}

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

// "STATUS PLATAFORMA" = assinatura dos status do Meli preenchidos, join " | ".
function assinatura(c: Logistica): string {
  const order = opcoes.value.field_order.length
    ? opcoes.value.field_order
    : Object.keys(c.meli_status || {})
  const parts = order.map((f) => c.meli_status?.[f]).filter(Boolean)
  return parts.join(' | ')
}

// ================= Aba Status =================
type LogisticaStatus = {
  id: string
  status_plataforma: string
  alterar_status_bling: string | null
  abrir_chamado: boolean
  mensagem_chamado: string | null
  created_by: string | null
  created_at: string
  updated_at: string
}

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

const statusModalOpen = ref(false)
const statusEditingId = ref<string | null>(null)
const statusSaving = ref(false)
const statusFormErr = ref<string | null>(null)

const statusForm = reactive({
  status_plataforma: '',
  alterar_status_bling: '',
  abrir_chamado: false,
  mensagem_chamado: '',
})

function resetStatusForm(src?: LogisticaStatus) {
  statusForm.status_plataforma = src?.status_plataforma || ''
  statusForm.alterar_status_bling = src?.alterar_status_bling || ''
  statusForm.abrir_chamado = src?.abrir_chamado || false
  statusForm.mensagem_chamado = src?.mensagem_chamado || ''
}

function openNewStatus() {
  statusEditingId.value = null
  resetStatusForm()
  statusFormErr.value = null
  statusModalOpen.value = true
}

function openEditStatus(s: LogisticaStatus) {
  statusEditingId.value = s.id
  resetStatusForm(s)
  statusFormErr.value = null
  statusModalOpen.value = true
}

function closeStatusModal() {
  statusModalOpen.value = false
  statusEditingId.value = null
}

function statusPayload() {
  return {
    status_plataforma: statusForm.status_plataforma.trim(),
    alterar_status_bling: statusForm.alterar_status_bling.trim() || null,
    abrir_chamado: statusForm.abrir_chamado,
    mensagem_chamado: statusForm.mensagem_chamado.trim() || null,
  }
}

async function saveStatus() {
  statusSaving.value = true
  statusFormErr.value = null
  try {
    if (statusEditingId.value) {
      await api(`/api/logistica/status/${statusEditingId.value}`, { method: 'PATCH', body: statusPayload() })
    } else {
      await api('/api/logistica/status', { method: 'POST', body: statusPayload() })
    }
    closeStatusModal()
    await refreshStatus()
  } catch (e: any) {
    statusFormErr.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    statusSaving.value = false
  }
}

async function removeStatus() {
  if (!statusEditingId.value) return
  if (!confirm('Remover este status?')) return
  statusSaving.value = true
  statusFormErr.value = null
  try {
    await api(`/api/logistica/status/${statusEditingId.value}`, { method: 'DELETE' })
    closeStatusModal()
    await refreshStatus()
  } catch (e: any) {
    statusFormErr.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    statusSaving.value = false
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
        Logística
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
              v-for="c in rows"
              :key="c.id"
              class="border-t hover:bg-muted/20 cursor-pointer"
              @click="openEdit(c)"
            >
              <td class="px-3 py-2 whitespace-nowrap">{{ fmtDate(c.data) }}</td>
              <td class="px-3 py-2 whitespace-nowrap font-medium">{{ c.pedido_bling || '—' }}</td>
              <td class="px-3 py-2 whitespace-nowrap">{{ c.pedido_marketplace || '—' }}</td>
              <td class="px-3 py-2 whitespace-nowrap">{{ c.plataforma || '—' }}</td>
              <td class="px-3 py-2 whitespace-nowrap">{{ c.conta || '—' }}</td>
              <td class="px-3 py-2 text-muted-foreground text-xs max-w-[280px] break-words">{{ assinatura(c) || '—' }}</td>
              <td class="px-3 py-2 whitespace-nowrap">{{ c.rastreio || '—' }}</td>
              <td class="px-3 py-2 whitespace-nowrap">{{ c.localizacao || '—' }}</td>
              <td class="px-3 py-2 whitespace-nowrap">
                <span v-if="c.status_bling" class="text-xs px-2 py-0.5 rounded border border-primary/50">{{ c.status_bling }}</span>
                <span v-else class="text-muted-foreground">—</span>
              </td>
              <td class="px-3 py-2 whitespace-nowrap">{{ c.chamado || '—' }}</td>
            </tr>
            <tr v-if="!loading && rows.length === 0">
              <td colspan="10" class="px-3 py-6 text-center text-muted-foreground">nenhum caso</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Mobile cards -->
      <div class="md:hidden space-y-2">
        <div
          v-for="c in rows"
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
        </div>
        <div v-if="!loading && rows.length === 0" class="text-center text-sm text-muted-foreground py-6 border rounded-md">
          nenhum caso
        </div>
      </div>
    </template>

    <!-- ============ ABA STATUS ============ -->
    <template v-else>
      <div class="flex flex-wrap items-center gap-3">
        <Button size="sm" variant="ghost" :disabled="statusLoading" @click="refreshStatus">
          <RefreshCw class="size-4 mr-1" /> recarregar
        </Button>
        <Button v-if="canEdit" size="sm" class="ml-auto" @click="openNewStatus">
          <Plus class="size-4 mr-1" /> Novo status
        </Button>
      </div>

      <p class="text-sm text-muted-foreground">
        Cadastro do que fazer pra cada Status Plataforma: se altera o status no Bling e se abre chamado.
      </p>

      <div v-if="statusError" class="text-sm text-red-500">erro: {{ statusError }}</div>

      <!-- Desktop table -->
      <div class="hidden md:block border rounded-md overflow-x-auto">
        <table class="w-full text-sm min-w-[700px] border-collapse [&_th]:border [&_td]:border [&_th]:border-border [&_td]:border-border">
          <thead class="bg-muted/40 text-left">
            <tr class="whitespace-nowrap">
              <th class="px-3 py-2">Status Plataforma</th>
              <th class="px-3 py-2">Alterar Status Bling</th>
              <th class="px-3 py-2">Abrir Chamado</th>
              <th class="px-3 py-2">Mensagem do Chamado</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="s in statusRows"
              :key="s.id"
              class="border-t hover:bg-muted/20 cursor-pointer"
              @click="openEditStatus(s)"
            >
              <td class="px-3 py-2 font-medium">{{ s.status_plataforma }}</td>
              <td class="px-3 py-2">
                <span v-if="s.alterar_status_bling" class="text-xs px-2 py-0.5 rounded border border-primary/50">{{ s.alterar_status_bling }}</span>
                <span v-else class="text-muted-foreground">—</span>
              </td>
              <td class="px-3 py-2 whitespace-nowrap">
                <span :class="s.abrir_chamado ? 'text-emerald-500' : 'text-muted-foreground'">
                  {{ s.abrir_chamado ? 'Sim' : 'Não' }}
                </span>
              </td>
              <td class="px-3 py-2 text-muted-foreground text-xs max-w-[320px] break-words">{{ s.mensagem_chamado || '—' }}</td>
            </tr>
            <tr v-if="!statusLoading && statusRows.length === 0">
              <td colspan="4" class="px-3 py-6 text-center text-muted-foreground">nenhum status</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Mobile cards -->
      <div class="md:hidden space-y-2">
        <div
          v-for="s in statusRows"
          :key="s.id"
          class="border rounded-md p-3 space-y-2 hover:bg-muted/20 cursor-pointer"
          @click="openEditStatus(s)"
        >
          <div class="flex items-start gap-2">
            <div class="flex-1 min-w-0 font-medium truncate">{{ s.status_plataforma }}</div>
            <span :class="s.abrir_chamado ? 'text-emerald-500' : 'text-muted-foreground'" class="text-xs shrink-0">
              {{ s.abrir_chamado ? 'Chamado' : 'Sem chamado' }}
            </span>
          </div>
          <div class="text-xs">
            <span class="text-muted-foreground">Alterar Bling:</span> {{ s.alterar_status_bling || '—' }}
          </div>
          <div v-if="s.mensagem_chamado" class="text-xs text-muted-foreground break-words">{{ s.mensagem_chamado }}</div>
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

    <!-- Modal create/edit status -->
    <div v-if="statusModalOpen" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" @click.self="closeStatusModal">
      <div class="bg-background border rounded-lg w-full max-w-lg p-5 space-y-4 max-h-[90vh] overflow-y-auto">
        <div class="flex items-center">
          <h2 class="text-lg font-semibold">{{ statusEditingId ? 'Editar status' : 'Novo status' }}</h2>
          <Button class="ml-auto" size="sm" variant="ghost" @click="closeStatusModal">
            <X class="size-4" />
          </Button>
        </div>

        <div class="space-y-3">
          <div>
            <Label>Status Plataforma</Label>
            <Input v-model="statusForm.status_plataforma" :disabled="!canEdit" />
          </div>
          <div>
            <Label>Alterar Status Bling</Label>
            <Input v-model="statusForm.alterar_status_bling" placeholder="deixe vazio pra não alterar" :disabled="!canEdit" />
          </div>
          <label class="flex items-center gap-2 text-sm">
            <input v-model="statusForm.abrir_chamado" type="checkbox" :disabled="!canEdit" class="size-4" />
            Abrir chamado
          </label>
          <div>
            <Label>Mensagem do Chamado</Label>
            <textarea
              v-model="statusForm.mensagem_chamado"
              rows="2"
              :disabled="!canEdit"
              class="w-full rounded-md border bg-background px-2 py-1.5 text-sm resize-y"
            />
          </div>
        </div>

        <div v-if="statusFormErr" class="text-sm text-red-500">erro: {{ statusFormErr }}</div>
        <div v-if="canEdit" class="flex justify-end gap-2">
          <Button v-if="statusEditingId" variant="ghost" :disabled="statusSaving" class="text-red-500 mr-auto" @click="removeStatus">
            <Trash2 class="size-4 mr-1" /> remover
          </Button>
          <Button variant="ghost" :disabled="statusSaving" @click="closeStatusModal">cancelar</Button>
          <Button :disabled="statusSaving" @click="saveStatus">
            {{ statusSaving ? 'salvando…' : 'Salvar' }}
          </Button>
        </div>
      </div>
    </div>
  </div>
</template>
