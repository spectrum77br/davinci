<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { Plus, RefreshCw, X, Trash2 } from 'lucide-vue-next'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'chamados', action: 'view' },
})

const { api } = useApi()
const canEdit = useCan('chamados', 'edit')

type MeliStatus = Record<string, string>

type Chamado = {
  id: string
  data: string | null
  pedido_bling: string | null
  pedido_marketplace: string | null
  plataforma: string | null
  conta: string | null
  meli_status: MeliStatus
  localizacao: string | null
  status_bling: string | null
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

const rows = ref<Chamado[]>([])
const loading = ref(false)
const error = ref<string | null>(null)

const opcoes = ref<Opcoes>({ field_order: [], field_labels: {}, field_options: {} })

async function refresh() {
  loading.value = true
  error.value = null
  try {
    rows.value = await api<Chamado[]>('/api/chamados')
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    loading.value = false
  }
}

async function loadOpcoes() {
  try {
    opcoes.value = await api<Opcoes>('/api/chamados/opcoes')
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
    localizacao: '',
    status_bling: '',
    observacao: '',
  }
}

const form = reactive(emptyForm())

function resetForm(src?: Chamado) {
  const base = emptyForm()
  form.data = src?.data || ''
  form.pedido_bling = src?.pedido_bling || ''
  form.pedido_marketplace = src?.pedido_marketplace || ''
  form.plataforma = src?.plataforma || ''
  form.conta = src?.conta || ''
  form.localizacao = src?.localizacao || ''
  form.status_bling = src?.status_bling || ''
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

function openEdit(c: Chamado) {
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
    localizacao: form.localizacao.trim() || null,
    status_bling: form.status_bling.trim() || null,
    observacao: form.observacao.trim() || null,
  }
}

async function save() {
  saving.value = true
  formErr.value = null
  try {
    if (editingId.value) {
      await api(`/api/chamados/${editingId.value}`, { method: 'PATCH', body: payload() })
    } else {
      await api('/api/chamados', { method: 'POST', body: payload() })
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
  if (!confirm('Remover este chamado?')) return
  saving.value = true
  formErr.value = null
  try {
    await api(`/api/chamados/${editingId.value}`, { method: 'DELETE' })
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
    const r = await api<{ candidatos: Candidato[] }>('/api/chamados/sugestao', {
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
function assinatura(c: Chamado): string {
  const order = opcoes.value.field_order.length
    ? opcoes.value.field_order
    : Object.keys(c.meli_status || {})
  const parts = order.map((f) => c.meli_status?.[f]).filter(Boolean)
  return parts.join(' | ')
}
</script>

<template>
  <div class="space-y-4">
    <div class="flex flex-wrap items-center gap-3">
      <h1 class="text-2xl font-semibold">Chamados</h1>
      <Button size="sm" variant="ghost" :disabled="loading" @click="refresh">
        <RefreshCw class="size-4 mr-1" /> recarregar
      </Button>
      <Button v-if="canEdit" size="sm" class="ml-auto" @click="openNew">
        <Plus class="size-4 mr-1" /> Novo chamado
      </Button>
    </div>

    <p class="text-sm text-muted-foreground">
      Casos de pós-venda a acompanhar. Preencha os status do Meli no chamado e o sistema
      sugere os Status Bling que a planilha já viu pra aquela combinação — a decisão final é sua.
    </p>

    <div v-if="error" class="text-sm text-red-500">erro: {{ error }}</div>

    <!-- Desktop table -->
    <div class="hidden md:block border rounded-md overflow-x-auto">
      <table class="w-full text-sm min-w-[900px] border-collapse [&_th]:border [&_td]:border [&_th]:border-border [&_td]:border-border">
        <thead class="bg-muted/40 text-left">
          <tr class="whitespace-nowrap">
            <th class="px-3 py-2">Data</th>
            <th class="px-3 py-2">Pedido Bling</th>
            <th class="px-3 py-2">Pedido Marketplace</th>
            <th class="px-3 py-2">Plataforma</th>
            <th class="px-3 py-2">Conta</th>
            <th class="px-3 py-2">Status Plataforma</th>
            <th class="px-3 py-2">Localização</th>
            <th class="px-3 py-2">Status Bling</th>
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
            <td class="px-3 py-2 whitespace-nowrap">{{ c.localizacao || '—' }}</td>
            <td class="px-3 py-2 whitespace-nowrap">
              <span v-if="c.status_bling" class="text-xs px-2 py-0.5 rounded border border-primary/50">{{ c.status_bling }}</span>
              <span v-else class="text-muted-foreground">—</span>
            </td>
          </tr>
          <tr v-if="!loading && rows.length === 0">
            <td colspan="8" class="px-3 py-6 text-center text-muted-foreground">nenhum chamado</td>
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
          <div><span class="text-muted-foreground">Localização:</span> {{ c.localizacao || '—' }}</div>
        </div>
      </div>
      <div v-if="!loading && rows.length === 0" class="text-center text-sm text-muted-foreground py-6 border rounded-md">
        nenhum chamado
      </div>
    </div>

    <!-- Modal create/edit -->
    <div v-if="modalOpen" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" @click.self="closeModal">
      <div class="bg-background border rounded-lg w-full max-w-2xl p-5 space-y-4 max-h-[90vh] overflow-y-auto">
        <div class="flex items-center">
          <h2 class="text-lg font-semibold">{{ editingId ? 'Editar chamado' : 'Novo chamado' }}</h2>
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
            <Label>Localização</Label>
            <Input v-model="form.localizacao" placeholder="ex: em trânsito / Correios" :disabled="!canEdit" />
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
