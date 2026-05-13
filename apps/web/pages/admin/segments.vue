<script setup lang="ts">
import { AlertCircle, Check, ChevronDown, ChevronRight, Loader2, Plus, RefreshCw, Trash2, X } from 'lucide-vue-next'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'segmentos', action: 'view' },
})

type Segment = {
  id: string
  user_id: string | null
  parent_id: string | null
  name: string
  slug: string
  sort_order: number
  active: boolean
  min_margin: string | null
  created_at: string
  updated_at: string
}

type TreeNode = Segment & { children: TreeNode[] }

const { api } = useApi()
const canEdit = useCan('segmentos', 'edit')
const canDelete = useCan('segmentos', 'delete')

const tree = ref<TreeNode[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const expanded = ref<Set<string>>(new Set())

async function load() {
  loading.value = true
  error.value = null
  try {
    tree.value = await api<TreeNode[]>('/api/segments/tree')
    // expand roots by default on first load
    if (expanded.value.size === 0) {
      for (const n of tree.value) expanded.value.add(n.id)
    }
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    loading.value = false
  }
}

await load()

function toggle(id: string) {
  if (expanded.value.has(id)) expanded.value.delete(id)
  else expanded.value.add(id)
}

// =========================================================== inline edit

const editing = ref<{ id: string; field: 'name' | 'min_margin' } | null>(null)
const editValue = ref<string>('')
const editOriginal = ref<string>('')
const editInputRef = ref<HTMLInputElement | null>(null)
function setEditInputRef(el: any) { if (el) editInputRef.value = el }
const flashed = ref<Set<string>>(new Set())

function isEditing(id: string, f: string) { return editing.value?.id === id && editing.value?.field === f }
function isFlashed(id: string, f: string) { return flashed.value.has(`${id}::${f}`) }
function flash(id: string, f: string) {
  const k = `${id}::${f}`
  flashed.value.add(k)
  setTimeout(() => flashed.value.delete(k), 1200)
}

async function startEdit(seg: Segment, field: 'name' | 'min_margin') {
  if (!canEdit.value) return
  editing.value = { id: seg.id, field }
  const raw = (seg as any)[field]
  // min_margin is stored as a fraction (0.15 = 15%); show as percent for editing.
  let initial: string
  if (field === 'min_margin') {
    initial = raw == null ? '' : (Number(raw) * 100).toString()
  } else {
    initial = raw == null ? '' : String(raw)
  }
  editValue.value = initial
  editOriginal.value = initial
  await nextTick()
  editInputRef.value?.focus()
  editInputRef.value?.select?.()
}

function cancelEdit() {
  editing.value = null
  editValue.value = ''
  editOriginal.value = ''
}

async function commitEdit() {
  if (!editing.value) return
  const { id, field } = editing.value
  if (editValue.value === editOriginal.value) return cancelEdit()
  const raw = editValue.value.trim()
  const payload: Record<string, unknown> = {}
  if (field === 'min_margin') {
    if (!raw) {
      payload.min_margin = null
    } else {
      const n = Number(raw)
      if (!Number.isFinite(n)) return cancelEdit()
      // UI accepts percent (15 → 0.15), DB stores fraction.
      payload.min_margin = (n / 100).toFixed(4)
    }
  } else {
    if (!raw && field === 'name') return cancelEdit()
    payload[field] = raw || null
  }

  try {
    await api(`/api/segments/${id}`, { method: 'PATCH', body: payload })
    flash(id, field)
    await load()
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    cancelEdit()
  }
}

// =========================================================== toggle active

async function toggleActive(seg: Segment) {
  try {
    await api(`/api/segments/${seg.id}`, {
      method: 'PATCH',
      body: { active: !seg.active },
    })
    flash(seg.id, 'active')
    await load()
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  }
}

// =========================================================== add child / root

const addingUnder = ref<string | null | undefined>(undefined) // undefined = closed, null = adding root
const newName = ref('')
const adding = ref(false)

function openAdd(parentId: string | null) {
  addingUnder.value = parentId
  newName.value = ''
  if (parentId) expanded.value.add(parentId)
  nextTick(() => {
    const el = document.getElementById('seg-new-input') as HTMLInputElement | null
    el?.focus()
  })
}

function closeAdd() {
  addingUnder.value = undefined
  newName.value = ''
}

async function submitAdd() {
  if (!newName.value.trim()) return
  adding.value = true
  try {
    await api('/api/segments', {
      method: 'POST',
      body: { name: newName.value.trim(), parent_id: addingUnder.value },
    })
    closeAdd()
    await load()
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    adding.value = false
  }
}

// =========================================================== delete

async function remove(seg: Segment, depth: number) {
  const msg = depth === 0
    ? `Excluir segmento raiz "${seg.name}"? Todos os filhos serão removidos.`
    : `Excluir "${seg.name}"?`
  if (!confirm(msg)) return
  try {
    await api(`/api/segments/${seg.id}`, { method: 'DELETE' })
    await load()
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  }
}
</script>

<template>
  <div class="space-y-4">
    <PageHeader title="Segmentos">
      <template #actions>
        <Button size="sm" variant="ghost" :disabled="loading" @click="load">
          <RefreshCw class="size-4 mr-1" :class="{ 'animate-spin': loading }" /> recarregar
        </Button>
        <Button v-if="canEdit" size="sm" @click="openAdd(null)">
          <Plus class="size-4 mr-1" /> Novo segmento raiz
        </Button>
      </template>
    </PageHeader>

    <div v-if="error" class="rounded border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive flex items-center gap-2">
      <AlertCircle class="h-4 w-4" /> {{ error }}
    </div>

    <div class="border rounded-lg overflow-auto max-h-[calc(100vh-220px)]">
      <table class="w-full text-sm border-collapse">
        <thead class="sticky top-0 bg-muted z-10">
          <tr>
            <th class="text-left px-3 py-2 font-medium border-b border-border min-w-[280px]">Nome</th>
            <th class="text-right px-3 py-2 font-medium border-b border-border w-28">Margem Mín</th>
            <th class="text-center px-3 py-2 font-medium border-b border-border w-20">Ativo</th>
            <th class="text-center px-3 py-2 font-medium border-b border-border w-32">Ações</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading && !tree.length">
            <td colspan="4" class="text-center py-6 text-muted-foreground">
              <Loader2 class="inline h-4 w-4 animate-spin" /> carregando…
            </td>
          </tr>
          <tr v-else-if="!tree.length && addingUnder === undefined">
            <td colspan="4" class="text-center py-8 text-muted-foreground">Nenhum segmento.</td>
          </tr>

          <template v-for="node in tree" :key="node.id">
            <SegmentRow
              :node="node"
              :depth="0"
              :expanded="expanded"
              :editing="editing"
              :edit-value="editValue"
              :flashed="flashed"
              :can-edit="canEdit"
              :can-delete="canDelete"
              :adding-under="addingUnder"
              :new-name="newName"
              :adding="adding"
              :set-edit-input-ref="setEditInputRef"
              @toggle="toggle"
              @start-edit="startEdit"
              @commit-edit="commitEdit"
              @cancel-edit="cancelEdit"
              @update:edit-value="(v: string) => (editValue = v)"
              @toggle-active="toggleActive"
              @open-add="openAdd"
              @close-add="closeAdd"
              @submit-add="submitAdd"
              @update:new-name="(v: string) => (newName = v)"
              @remove="remove"
            />
          </template>

          <!-- add new root -->
          <tr v-if="addingUnder === null" class="bg-blue-50/40 dark:bg-blue-900/10">
            <td class="border border-border px-3 py-1.5">
              <input
                id="seg-new-input"
                v-model="newName"
                type="text"
                placeholder="Nome do segmento raiz"
                class="w-full text-sm border rounded px-2 py-1 bg-background"
                @keydown.enter="submitAdd"
                @keydown.escape="closeAdd"
              />
            </td>
            <td class="border border-border text-xs text-muted-foreground px-3 text-right">—</td>
            <td class="border border-border text-xs text-muted-foreground px-3 text-center">—</td>
            <td class="border border-border px-1 py-1 text-center">
              <div class="flex gap-0.5 justify-center">
                <button class="p-1 text-emerald-600 hover:bg-emerald-50 dark:hover:bg-emerald-900/30 rounded" :disabled="adding" @click="submitAdd">
                  <Loader2 v-if="adding" class="h-3.5 w-3.5 animate-spin" />
                  <Check v-else class="h-3.5 w-3.5" />
                </button>
                <button class="p-1 text-destructive hover:bg-destructive/10 rounded" @click="closeAdd">
                  <X class="h-3.5 w-3.5" />
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
