<script setup lang="ts">
import { TABS_USUARIOS } from '~/lib/navGroups'
import { computed, ref } from 'vue'
import { Plus, RefreshCw, X, Trash2 } from 'lucide-vue-next'
import { isoToday } from '~/lib/date'

const { api } = useApi()
const auth = useAuthStore()
const isAdmin = computed(() => auth.isAdmin)

type Tarefa = {
  id: string
  responsavel_id: string
  responsavel_name: string | null
  responsavel_email: string | null
  data_inicio: string
  data_conclusao: string | null
  tarefa: string
  observacao: string | null
  created_by: string | null
  created_at: string
  updated_at: string
}

type UserOption = {
  id: string
  email: string
  name: string | null
}

const rows = ref<Tarefa[]>([])
const loading = ref(false)
const error = ref<string | null>(null)

const users = ref<UserOption[]>([])
const filterResponsavel = ref<string>('all')

const filteredRows = computed(() => {
  if (!isAdmin.value || filterResponsavel.value === 'all') return rows.value
  return rows.value.filter((t) => t.responsavel_id === filterResponsavel.value)
})

async function refresh() {
  loading.value = true
  error.value = null
  try {
    rows.value = await api<Tarefa[]>('/api/tarefas')
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    loading.value = false
  }
}

async function loadUsers() {
  if (!isAdmin.value || users.value.length > 0) return
  try {
    type UserList = { items: { id: string; email: string; name: string | null }[] }
    const r = await api<UserList>('/api/users?per_page=200')
    users.value = r.items.map((u) => ({ id: u.id, email: u.email, name: u.name }))
  } catch (e: any) {
    // Non-fatal: the picker will just be empty.
    error.value = e?.data?.detail?.code || e?.message || 'erro ao carregar usuários'
  }
}

await refresh()
if (isAdmin.value) await loadUsers()

// ---- Create modal (admin only) ----
const showNew = ref(false)
const draft = ref<{ responsavel_id: string; data_inicio: string; tarefa: string }>({
  responsavel_id: '',
  data_inicio: isoToday(),
  tarefa: '',
})
const creating = ref(false)
const createErr = ref<string | null>(null)

function openNew() {
  draft.value = {
    responsavel_id: '',
    data_inicio: isoToday(),
    tarefa: '',
  }
  createErr.value = null
  showNew.value = true
}

async function createTarefa() {
  if (!draft.value.responsavel_id || !draft.value.data_inicio || !draft.value.tarefa.trim()) {
    createErr.value = 'preencha responsável, data início e descrição'
    return
  }
  creating.value = true
  createErr.value = null
  try {
    await api('/api/tarefas', {
      method: 'POST',
      body: {
        responsavel_id: draft.value.responsavel_id,
        data_inicio: draft.value.data_inicio,
        tarefa: draft.value.tarefa.trim(),
      },
    })
    showNew.value = false
    await refresh()
  } catch (e: any) {
    createErr.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    creating.value = false
  }
}

// ---- Edit modal (admin: all fields; user: only observacao) ----
const editing = ref<Tarefa | null>(null)
const editDraft = ref<{
  responsavel_id: string
  data_inicio: string
  data_conclusao: string
  tarefa: string
  observacao: string
}>({
  responsavel_id: '',
  data_inicio: '',
  data_conclusao: '',
  tarefa: '',
  observacao: '',
})
const saving = ref(false)
const editErr = ref<string | null>(null)

function openEdit(t: Tarefa) {
  editing.value = t
  editDraft.value = {
    responsavel_id: t.responsavel_id,
    data_inicio: t.data_inicio,
    data_conclusao: t.data_conclusao || '',
    tarefa: t.tarefa,
    observacao: t.observacao || '',
  }
  editErr.value = null
}

async function saveEdit() {
  if (!editing.value) return
  saving.value = true
  editErr.value = null
  try {
    const body: Record<string, any> = {}
    if (isAdmin.value) {
      body.responsavel_id = editDraft.value.responsavel_id
      body.data_inicio = editDraft.value.data_inicio
      body.data_conclusao = editDraft.value.data_conclusao || null
      body.tarefa = editDraft.value.tarefa.trim()
      body.observacao = editDraft.value.observacao || null
    } else {
      body.observacao = editDraft.value.observacao || null
    }
    await api(`/api/tarefas/${editing.value.id}`, { method: 'PATCH', body })
    editing.value = null
    await refresh()
  } catch (e: any) {
    editErr.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    saving.value = false
  }
}

async function removeTarefa() {
  if (!editing.value) return
  if (!confirm('Deletar esta tarefa?')) return
  saving.value = true
  editErr.value = null
  try {
    await api(`/api/tarefas/${editing.value.id}`, { method: 'DELETE' })
    editing.value = null
    await refresh()
  } catch (e: any) {
    editErr.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    saving.value = false
  }
}

function fmtDate(s: string | null) {
  if (!s) return '—'
  // s is YYYY-MM-DD; parse as local date to avoid TZ shift.
  const [y, m, d] = s.split('-').map((n) => Number(n))
  if (!y || !m || !d) return s
  return new Date(y, m - 1, d).toLocaleDateString('pt-BR')
}

function userLabel(u: UserOption) {
  return u.name ? `${u.name} (${u.email})` : u.email
}
</script>

<template>
  <div class="space-y-4">
    <RouteTabs :tabs="TABS_USUARIOS" />
    <div class="flex flex-wrap items-center gap-3">
      <h1 class="text-2xl font-semibold">Tarefas</h1>
      <Button size="sm" variant="ghost" :disabled="loading" @click="refresh">
        <RefreshCw class="size-4 mr-1" /> recarregar
      </Button>
      <select
        v-if="isAdmin"
        v-model="filterResponsavel"
        class="ml-auto border rounded-md px-2 py-1 text-sm bg-background"
      >
        <option value="all">Todos responsáveis</option>
        <option v-for="u in users" :key="u.id" :value="u.id">{{ u.name || u.email }}</option>
      </select>
      <Button v-if="isAdmin" size="sm" @click="openNew">
        <Plus class="size-4 mr-1" /> Nova tarefa
      </Button>
    </div>

    <div v-if="error" class="text-sm text-red-500">erro: {{ error }}</div>

    <!-- Desktop table -->
    <div class="hidden md:block border rounded-md overflow-x-auto">
      <table class="w-full text-sm min-w-[900px]">
        <thead class="bg-muted/40 text-left">
          <tr class="whitespace-nowrap">
            <th class="px-3 py-2">Responsável</th>
            <th class="px-3 py-2">Data início</th>
            <th class="px-3 py-2">Data conclusão</th>
            <th class="px-3 py-2">Tarefa</th>
            <th class="px-3 py-2">Observação</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="t in filteredRows"
            :key="t.id"
            class="border-t hover:bg-muted/20 cursor-pointer"
            :class="{ 'opacity-60': t.data_conclusao }"
            @click="openEdit(t)"
          >
            <td class="px-3 py-2 whitespace-nowrap">{{ t.responsavel_name || t.responsavel_email || '—' }}</td>
            <td class="px-3 py-2 whitespace-nowrap">{{ fmtDate(t.data_inicio) }}</td>
            <td class="px-3 py-2 whitespace-nowrap">
              <span v-if="t.data_conclusao">{{ fmtDate(t.data_conclusao) }}</span>
              <span v-else class="text-xs px-2 py-0.5 rounded border border-amber-500 text-amber-400">pendente</span>
            </td>
            <td class="px-3 py-2">{{ t.tarefa }}</td>
            <td class="px-3 py-2 text-muted-foreground">{{ t.observacao || '—' }}</td>
          </tr>
          <tr v-if="!loading && filteredRows.length === 0">
            <td colspan="5" class="px-3 py-6 text-center text-muted-foreground">nenhuma tarefa</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Mobile cards -->
    <div class="md:hidden space-y-2">
      <div
        v-for="t in filteredRows"
        :key="t.id"
        class="border rounded-md p-3 space-y-2 hover:bg-muted/20 cursor-pointer"
        :class="{ 'opacity-60': t.data_conclusao }"
        @click="openEdit(t)"
      >
        <div class="flex items-start gap-2">
          <div class="flex-1 min-w-0">
            <div class="font-medium truncate">{{ t.tarefa }}</div>
            <div class="text-xs text-muted-foreground truncate">{{ t.responsavel_name || t.responsavel_email || '—' }}</div>
          </div>
          <span
            v-if="!t.data_conclusao"
            class="text-[10px] px-1.5 py-0.5 rounded border border-amber-500 text-amber-400 shrink-0"
          >pendente</span>
        </div>
        <div class="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
          <div><span class="text-muted-foreground">Início:</span> {{ fmtDate(t.data_inicio) }}</div>
          <div><span class="text-muted-foreground">Concl.:</span> {{ fmtDate(t.data_conclusao) }}</div>
        </div>
        <div v-if="t.observacao" class="text-xs">
          <span class="text-muted-foreground">Obs.:</span> {{ t.observacao }}
        </div>
      </div>
      <div v-if="!loading && filteredRows.length === 0" class="text-center text-sm text-muted-foreground py-6 border rounded-md">
        nenhuma tarefa
      </div>
    </div>

    <!-- Create modal (admin) -->
    <div v-if="showNew" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" @click.self="showNew = false">
      <div class="bg-background border rounded-lg w-full max-w-md p-5 space-y-4">
        <div class="flex items-center">
          <h2 class="text-lg font-semibold">Nova tarefa</h2>
          <Button class="ml-auto" size="sm" variant="ghost" @click="showNew = false">
            <X class="size-4" />
          </Button>
        </div>
        <div class="space-y-3">
          <div>
            <Label>Responsável *</Label>
            <select
              v-model="draft.responsavel_id"
              class="w-full h-9 rounded-md border bg-background px-2 text-sm"
            >
              <option value="">— selecione —</option>
              <option v-for="u in users" :key="u.id" :value="u.id">{{ userLabel(u) }}</option>
            </select>
          </div>
          <div>
            <Label>Data de início *</Label>
            <Input v-model="draft.data_inicio" type="date" />
          </div>
          <div>
            <Label>Descrição *</Label>
            <textarea
              v-model="draft.tarefa"
              rows="3"
              class="w-full rounded-md border bg-background px-2 py-1.5 text-sm resize-y"
            />
          </div>
        </div>
        <div v-if="createErr" class="text-sm text-red-500">erro: {{ createErr }}</div>
        <div class="flex justify-end gap-2">
          <Button variant="ghost" :disabled="creating" @click="showNew = false">cancelar</Button>
          <Button :disabled="creating" @click="createTarefa">
            {{ creating ? 'criando…' : 'Criar' }}
          </Button>
        </div>
      </div>
    </div>

    <!-- Edit modal -->
    <div v-if="editing" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" @click.self="editing = null">
      <div class="bg-background border rounded-lg w-full max-w-md p-5 space-y-4">
        <div class="flex items-center">
          <h2 class="text-lg font-semibold">{{ isAdmin ? 'Editar tarefa' : 'Detalhes da tarefa' }}</h2>
          <Button class="ml-auto" size="sm" variant="ghost" @click="editing = null">
            <X class="size-4" />
          </Button>
        </div>

        <!-- Admin: all fields editable -->
        <template v-if="isAdmin">
          <div class="space-y-3">
            <div>
              <Label>Responsável</Label>
              <select
                v-model="editDraft.responsavel_id"
                class="w-full h-9 rounded-md border bg-background px-2 text-sm"
              >
                <option v-for="u in users" :key="u.id" :value="u.id">{{ userLabel(u) }}</option>
              </select>
            </div>
            <div class="grid grid-cols-2 gap-3">
              <div>
                <Label>Data início</Label>
                <Input v-model="editDraft.data_inicio" type="date" />
              </div>
              <div>
                <Label>Data conclusão</Label>
                <Input v-model="editDraft.data_conclusao" type="date" />
              </div>
            </div>
            <div>
              <Label>Tarefa</Label>
              <textarea
                v-model="editDraft.tarefa"
                rows="3"
                class="w-full rounded-md border bg-background px-2 py-1.5 text-sm resize-y"
              />
            </div>
            <div>
              <Label>Observação</Label>
              <textarea
                v-model="editDraft.observacao"
                rows="3"
                class="w-full rounded-md border bg-background px-2 py-1.5 text-sm resize-y"
              />
            </div>
          </div>
        </template>

        <!-- Non-admin: read-only fields, only observacao editable -->
        <template v-else>
          <div class="space-y-3 text-sm">
            <div>
              <div class="text-xs text-muted-foreground">Tarefa</div>
              <div class="font-medium whitespace-pre-wrap">{{ editing?.tarefa }}</div>
            </div>
            <div class="grid grid-cols-2 gap-3 text-xs">
              <div>
                <div class="text-muted-foreground">Data início</div>
                <div>{{ fmtDate(editing?.data_inicio ?? null) }}</div>
              </div>
              <div>
                <div class="text-muted-foreground">Data conclusão</div>
                <div>
                  <span v-if="editing?.data_conclusao">{{ fmtDate(editing.data_conclusao) }}</span>
                  <span v-else class="text-amber-400">pendente</span>
                </div>
              </div>
            </div>
            <div>
              <Label>Observação</Label>
              <textarea
                v-model="editDraft.observacao"
                rows="4"
                class="w-full rounded-md border bg-background px-2 py-1.5 text-sm resize-y"
              />
            </div>
          </div>
        </template>

        <div v-if="editErr" class="text-sm text-red-500">erro: {{ editErr }}</div>
        <div class="flex justify-end gap-2">
          <Button v-if="isAdmin" variant="ghost" :disabled="saving" class="text-red-500" @click="removeTarefa">
            <Trash2 class="size-4 mr-1" /> deletar
          </Button>
          <div class="ml-auto flex gap-2">
            <Button variant="ghost" :disabled="saving" @click="editing = null">cancelar</Button>
            <Button :disabled="saving" @click="saveEdit">
              {{ saving ? 'salvando…' : 'Salvar' }}
            </Button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
