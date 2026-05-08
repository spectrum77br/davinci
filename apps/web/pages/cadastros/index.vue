<script setup lang="ts">
import { ref, computed } from 'vue'
import { Pencil, Plus, RefreshCw, Trash2, X } from 'lucide-vue-next'
import {
  MARKETPLACES,
  MARKETPLACE_SHORT,
  type Marketplace,
} from '~/composables/useMarketplaces'

definePageMeta({ middleware: ['permission'], permission: { resource: 'cadastro', action: 'view' } })

type CadastroOut = {
  id: string
  tipo: string
  provedor: string | null
  responsavel_id: string | null
  codigo: string
  label: string | null
  status: string
  obs: string | null
  raw_links: Record<string, string>
}

type Cell = {
  store_id: string
  alias: string | null
  company_apelido: string
  store_status: string
}

type Row = { cadastro: CadastroOut; cells: Record<string, Cell[]> }
type Grid = { marketplaces: string[]; rows: Row[] }

type StoreOut = {
  id: string
  company_id: string
  marketplace: string
  apelido_override: string | null
  status: string
}
type Company = { id: string; apelido: string; razao_social: string }

const { api } = useApi()
const grid = ref<Grid | null>(null)
const stores = ref<StoreOut[]>([])
const companies = ref<Company[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const filterTipo = ref('')
const filterProvedor = ref('')
const search = ref('')
const showNew = ref(false)

const canEdit = useCan('cadastro', 'edit')

const TIPOS = ['fone', 'email', 'dominio', 'servidor']

async function refresh() {
  loading.value = true
  error.value = null
  try {
    const [g, s, c] = await Promise.all([
      api<Grid>('/api/cadastros/grid'),
      api<StoreOut[]>('/api/stores'),
      api<Company[]>('/api/companies'),
    ])
    grid.value = g
    stores.value = s
    companies.value = c
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    loading.value = false
  }
}
await refresh()

const filteredRows = computed(() => {
  if (!grid.value) return []
  let rows = grid.value.rows
  if (filterTipo.value) rows = rows.filter(r => r.cadastro.tipo === filterTipo.value)
  if (filterProvedor.value) rows = rows.filter(r => (r.cadastro.provedor || '').toLowerCase().includes(filterProvedor.value.toLowerCase()))
  if (search.value) {
    const q = search.value.toLowerCase()
    rows = rows.filter(r =>
      r.cadastro.codigo.toLowerCase().includes(q) ||
      (r.cadastro.label || '').toLowerCase().includes(q)
    )
  }
  return rows
})

const companyById = computed(() => {
  const m: Record<string, Company> = {}
  for (const c of companies.value) m[c.id] = c
  return m
})

const draft = ref({ tipo: 'fone', provedor: '', codigo: '', label: '', obs: '', store_ids: [] as string[] })
const creating = ref(false)
const createErr = ref<string | null>(null)

function toggleStore(id: string) {
  const i = draft.value.store_ids.indexOf(id)
  if (i >= 0) draft.value.store_ids.splice(i, 1)
  else draft.value.store_ids.push(id)
}

async function createCadastro() {
  creating.value = true
  createErr.value = null
  try {
    const body: Record<string, any> = {
      tipo: draft.value.tipo,
      codigo: draft.value.codigo,
    }
    if (draft.value.provedor) body.provedor = draft.value.provedor
    if (draft.value.label) body.label = draft.value.label
    if (draft.value.obs) body.obs = draft.value.obs
    if (draft.value.store_ids.length) body.store_ids = draft.value.store_ids
    await api('/api/cadastros', { method: 'POST', body })
    showNew.value = false
    draft.value = { tipo: 'fone', provedor: '', codigo: '', label: '', obs: '', store_ids: [] }
    await refresh()
  } catch (e: any) {
    createErr.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    creating.value = false
  }
}

async function updateAlias(cadastroId: string, storeId: string, currentAlias: string | null, newAlias: string) {
  if ((newAlias || null) === currentAlias) return
  // need full set of links — refetch detail
  try {
    const detail = await api<{ stores: { store_id: string; alias: string | null }[] }>(`/api/cadastros/${cadastroId}`)
    const links = detail.stores.map(l =>
      l.store_id === storeId ? { store_id: l.store_id, alias: newAlias || null } : l
    )
    await api(`/api/cadastros/${cadastroId}/stores`, { method: 'PUT', body: { links } })
    await refresh()
  } catch (e: any) {
    error.value = e?.data?.detail?.code || 'erro'
  }
}

async function unlinkStore(cadastroId: string, storeId: string, label: string) {
  if (!confirm(`desvincular "${label}" deste cadastro?`)) return
  try {
    const detail = await api<{ stores: { store_id: string; alias: string | null }[] }>(`/api/cadastros/${cadastroId}`)
    const links = detail.stores
      .filter(l => l.store_id !== storeId)
      .map(l => ({ store_id: l.store_id, alias: l.alias }))
    await api(`/api/cadastros/${cadastroId}/stores`, { method: 'PUT', body: { links } })
    await refresh()
  } catch (e: any) {
    error.value = e?.data?.detail?.code || 'erro'
  }
}

const editing = ref<CadastroOut | null>(null)
const editDraft = ref({ tipo: 'fone', provedor: '', codigo: '', label: '', obs: '' })
const editLinks = ref<Record<string, { selected: boolean; alias: string }>>({})
const editSubmitting = ref(false)
const editErr = ref<string | null>(null)

async function openEdit(cad: CadastroOut) {
  editing.value = cad
  editDraft.value = {
    tipo: cad.tipo,
    provedor: cad.provedor || '',
    codigo: cad.codigo,
    label: cad.label || '',
    obs: cad.obs || '',
  }
  editErr.value = null
  // load current links
  const links: Record<string, { selected: boolean; alias: string }> = {}
  for (const s of stores.value) links[s.id] = { selected: false, alias: '' }
  try {
    const detail = await api<{ stores: { store_id: string; alias: string | null }[] }>(`/api/cadastros/${cad.id}`)
    for (const l of detail.stores) {
      if (!links[l.store_id]) links[l.store_id] = { selected: false, alias: '' }
      links[l.store_id].selected = true
      links[l.store_id].alias = l.alias || ''
    }
  } catch (e: any) {
    editErr.value = e?.data?.detail?.code || e?.message || 'erro'
  }
  editLinks.value = links
}

function toggleEditStore(id: string) {
  const l = editLinks.value[id]
  if (l) l.selected = !l.selected
}

async function submitEdit() {
  if (!editing.value) return
  editSubmitting.value = true
  editErr.value = null
  try {
    const body: Record<string, any> = {
      tipo: editDraft.value.tipo,
      codigo: editDraft.value.codigo,
      provedor: editDraft.value.provedor || null,
      label: editDraft.value.label || null,
      obs: editDraft.value.obs || null,
    }
    await api(`/api/cadastros/${editing.value.id}`, { method: 'PATCH', body })
    const links = Object.entries(editLinks.value)
      .filter(([, v]) => v.selected)
      .map(([store_id, v]) => ({ store_id, alias: v.alias || null }))
    await api(`/api/cadastros/${editing.value.id}/stores`, { method: 'PUT', body: { links } })
    editing.value = null
    await refresh()
  } catch (e: any) {
    editErr.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    editSubmitting.value = false
  }
}

async function deleteCadastro(cad: CadastroOut) {
  if (!confirm(`apagar cadastro ${cad.codigo}? esta ação é irreversível.`)) return
  try {
    await api(`/api/cadastros/${cad.id}`, { method: 'DELETE' })
    await refresh()
  } catch (e: any) {
    error.value = e?.data?.detail?.code || 'erro'
  }
}

const storesByCompanyMk = computed(() => {
  const out: Record<string, StoreOut[]> = {}
  for (const s of stores.value) {
    const key = companyById.value[s.company_id]?.apelido || '?'
    out[key] ??= []
    out[key].push(s)
  }
  return out
})

function cellLabel(cell: Cell): string {
  return cell.alias || cell.company_apelido
}

function cap(s: string | null | undefined): string {
  if (!s) return ''
  return s.charAt(0).toUpperCase() + s.slice(1)
}

const editingCell = ref<{ rowId: string; field: 'provedor' | 'codigo' | 'label' } | null>(null)
const cellDraft = ref('')

function startCellEdit(cad: CadastroOut, field: 'provedor' | 'codigo' | 'label') {
  if (!canEdit.value) return
  editingCell.value = { rowId: cad.id, field }
  cellDraft.value = (cad as any)[field] || ''
}

function isEditingCell(cad: CadastroOut, field: 'provedor' | 'codigo' | 'label'): boolean {
  return editingCell.value?.rowId === cad.id && editingCell.value?.field === field
}

async function saveCellEdit(cad: CadastroOut) {
  if (!editingCell.value) return
  const field = editingCell.value.field
  const trimmed = cellDraft.value.trim()
  const newVal: string | null = field === 'codigo' ? trimmed : (trimmed || null)
  const current = (cad as any)[field] ?? null
  if (newVal === current) {
    editingCell.value = null
    return
  }
  if (field === 'codigo' && !newVal) {
    editingCell.value = null
    return
  }
  try {
    await api(`/api/cadastros/${cad.id}`, { method: 'PATCH', body: { [field]: newVal } })
    editingCell.value = null
    await refresh()
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
    editingCell.value = null
  }
}

function cancelCellEdit() {
  editingCell.value = null
}

const vFocus = {
  mounted: (el: HTMLInputElement) => {
    el.focus()
    el.select()
  },
}

function cellClass(cell: Cell): string {
  if (cell.store_status === 'inactive') return 'line-through text-muted-foreground'
  if (cell.store_status === 'closing') return 'bg-amber-500/10 text-amber-400'
  if (cell.store_status === 'banned') return 'text-red-400'
  return ''
}

const resolving = ref<{ cadastroId: string; marketplace: string; rawValue: string } | null>(null)
const resolveStoreId = ref<string>('')
const resolveAlias = ref<string>('')
const resolveSubmitting = ref(false)
const resolveErr = ref<string | null>(null)

const storesForResolveMk = computed<StoreOut[]>(() => {
  if (!resolving.value) return []
  return stores.value.filter(s => s.marketplace === resolving.value!.marketplace)
})

function openResolve(cadastroId: string, marketplace: string, rawValue: string) {
  resolving.value = { cadastroId, marketplace, rawValue }
  resolveStoreId.value = ''
  resolveAlias.value = rawValue
  resolveErr.value = null
}

async function submitResolve() {
  if (!resolving.value || !resolveStoreId.value) return
  resolveSubmitting.value = true
  resolveErr.value = null
  try {
    const { cadastroId, marketplace } = resolving.value
    await api(`/api/cadastros/${cadastroId}/raw-links/${marketplace}/resolve`, {
      method: 'POST',
      body: { store_id: resolveStoreId.value, alias: resolveAlias.value || null },
    })
    resolving.value = null
    await refresh()
  } catch (e: any) {
    resolveErr.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    resolveSubmitting.value = false
  }
}
</script>

<template>
  <div class="space-y-4">
    <div class="flex items-center gap-3 flex-wrap">
      <h1 class="text-2xl font-semibold">Cadastros</h1>
      <Button size="sm" variant="ghost" :disabled="loading" @click="refresh">
        <RefreshCw class="size-4 mr-1" /> recarregar
      </Button>
      <div class="ml-auto flex gap-2 flex-wrap">
        <Input v-model="search" placeholder="código / label" class="w-56" />
        <Input v-model="filterProvedor" placeholder="provedor" class="w-32" />
        <select v-model="filterTipo" class="border rounded px-2 text-sm bg-background">
          <option value="">todos tipos</option>
          <option v-for="t in TIPOS" :key="t" :value="t">{{ t }}</option>
        </select>
        <Button v-if="canEdit" size="sm" @click="showNew = true">
          <Plus class="size-4 mr-1" /> Novo cadastro
        </Button>
      </div>
    </div>

    <div v-if="error" class="text-sm text-red-500">erro: {{ error }}</div>

    <div class="border rounded-md overflow-x-auto">
      <table class="w-full text-sm">
        <thead class="bg-muted/40 text-left">
          <tr>
            <th class="px-3 py-2">Tipo</th>
            <th class="px-3 py-2">Provedor</th>
            <th class="px-3 py-2">Código</th>
            <th class="px-3 py-2">Label</th>
            <th v-for="mk in MARKETPLACES" :key="mk" class="px-2 py-2 text-center">
              {{ MARKETPLACE_SHORT[mk] }}
            </th>
            <th v-if="canEdit" class="px-2 py-2 text-right">Ações</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in filteredRows" :key="row.cadastro.id" class="border-t hover:bg-muted/20 group">
            <td class="px-3 py-2">{{ cap(row.cadastro.tipo) }}</td>
            <td class="px-3 py-2">
              <input
                v-if="isEditingCell(row.cadastro, 'provedor')"
                v-model="cellDraft"
                v-focus
                class="w-full border rounded px-1 py-0.5 bg-background text-sm"
                @blur="saveCellEdit(row.cadastro)"
                @keydown.enter.prevent="saveCellEdit(row.cadastro)"
                @keydown.esc.prevent="cancelCellEdit"
              />
              <span
                v-else
                :title="canEdit ? 'duplo-clique para editar' : ''"
                @dblclick="startCellEdit(row.cadastro, 'provedor')"
              >{{ row.cadastro.provedor || '—' }}</span>
            </td>
            <td class="px-3 py-2 font-mono">
              <input
                v-if="isEditingCell(row.cadastro, 'codigo')"
                v-model="cellDraft"
                v-focus
                class="w-full border rounded px-1 py-0.5 bg-background text-sm font-mono"
                @blur="saveCellEdit(row.cadastro)"
                @keydown.enter.prevent="saveCellEdit(row.cadastro)"
                @keydown.esc.prevent="cancelCellEdit"
              />
              <span
                v-else
                :title="canEdit ? 'duplo-clique para editar' : ''"
                @dblclick="startCellEdit(row.cadastro, 'codigo')"
              >{{ row.cadastro.codigo }}</span>
            </td>
            <td class="px-3 py-2 text-xs text-muted-foreground">
              <input
                v-if="isEditingCell(row.cadastro, 'label')"
                v-model="cellDraft"
                v-focus
                class="w-full border rounded px-1 py-0.5 bg-background text-xs"
                @blur="saveCellEdit(row.cadastro)"
                @keydown.enter.prevent="saveCellEdit(row.cadastro)"
                @keydown.esc.prevent="cancelCellEdit"
              />
              <span
                v-else
                :title="canEdit ? 'duplo-clique para editar' : ''"
                @dblclick="startCellEdit(row.cadastro, 'label')"
              >{{ row.cadastro.label || '—' }}</span>
            </td>
            <td v-for="mk in MARKETPLACES" :key="mk" class="px-2 py-2 text-xs">
              <div v-if="row.cells[mk]?.length" class="space-y-0.5">
                <div
                  v-for="cell in row.cells[mk]"
                  :key="cell.store_id"
                  class="flex items-center gap-1 group/cell"
                  :class="cellClass(cell)"
                >
                  <span
                    class="flex-1"
                    :title="canEdit ? 'duplo-clique para editar alias' : ''"
                    @dblclick="canEdit && (() => {
                      const v = prompt('alias', cell.alias || '')
                      if (v !== null) updateAlias(row.cadastro.id, cell.store_id, cell.alias, v)
                    })()"
                  >
                    {{ cellLabel(cell) }}
                  </span>
                  <button
                    v-if="canEdit"
                    type="button"
                    class="opacity-0 group-hover/cell:opacity-100 text-muted-foreground hover:text-red-400 transition"
                    title="desvincular"
                    @click="unlinkStore(row.cadastro.id, cell.store_id, cellLabel(cell))"
                  >
                    <X class="size-3" />
                  </button>
                </div>
              </div>
              <button
                v-else-if="row.cadastro.raw_links?.[mk]"
                type="button"
                class="text-amber-400/80 italic underline-offset-2 hover:underline disabled:no-underline disabled:cursor-default"
                :disabled="!canEdit"
                :title="canEdit ? 'clique para vincular a uma loja' : 'sem permissão para vincular'"
                @click="canEdit && openResolve(row.cadastro.id, mk, row.cadastro.raw_links[mk])"
              >
                {{ row.cadastro.raw_links[mk] }}
              </button>
            </td>
            <td v-if="canEdit" class="px-2 py-2 text-right whitespace-nowrap">
              <button
                type="button"
                class="text-muted-foreground hover:text-foreground p-1"
                title="editar campos"
                @click="openEdit(row.cadastro)"
              >
                <Pencil class="size-4" />
              </button>
              <button
                type="button"
                class="text-muted-foreground hover:text-red-400 p-1"
                title="apagar cadastro"
                @click="deleteCadastro(row.cadastro)"
              >
                <Trash2 class="size-4" />
              </button>
            </td>
          </tr>
          <tr v-if="!loading && filteredRows.length === 0">
            <td :colspan="canEdit ? 14 : 13" class="px-3 py-6 text-center text-muted-foreground">nenhum cadastro</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="showNew" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" @click.self="showNew = false">
      <div class="bg-background border rounded-lg w-full max-w-2xl p-5 space-y-4 max-h-[90vh] overflow-auto">
        <div class="flex items-center">
          <h2 class="text-lg font-semibold">Novo cadastro</h2>
          <Button class="ml-auto" size="sm" variant="ghost" @click="showNew = false">
            <X class="size-4" />
          </Button>
        </div>
        <div class="grid grid-cols-2 gap-3">
          <div>
            <Label>Tipo *</Label>
            <select v-model="draft.tipo" class="w-full border rounded px-2 py-1 bg-background">
              <option v-for="t in TIPOS" :key="t" :value="t">{{ cap(t) }}</option>
            </select>
          </div>
          <div>
            <Label>Provedor</Label>
            <Input v-model="draft.provedor" placeholder="tim, vivo, …" />
          </div>
          <div>
            <Label>Código *</Label>
            <Input v-model="draft.codigo" required />
          </div>
          <div>
            <Label>Label</Label>
            <Input v-model="draft.label" />
          </div>
        </div>
        <div>
          <Label>Observação</Label>
          <Input v-model="draft.obs" />
        </div>

        <div>
          <Label>Vincular a lojas</Label>
          <div class="border rounded p-2 max-h-64 overflow-auto space-y-2 mt-1">
            <div v-for="(group, apelido) in storesByCompanyMk" :key="apelido">
              <div class="text-xs font-semibold text-muted-foreground">{{ apelido }}</div>
              <label v-for="s in group" :key="s.id" class="flex items-center gap-2 text-xs px-2 py-0.5">
                <input
                  type="checkbox"
                  :checked="draft.store_ids.includes(s.id)"
                  @change="toggleStore(s.id)"
                />
                <span>{{ MARKETPLACE_SHORT[s.marketplace as Marketplace] }} — {{ s.apelido_override || apelido }}</span>
              </label>
            </div>
            <div v-if="!stores.length" class="text-xs text-muted-foreground">nenhuma loja cadastrada</div>
          </div>
        </div>

        <div v-if="createErr" class="text-sm text-red-500">erro: {{ createErr }}</div>
        <div class="flex justify-end gap-2">
          <Button variant="ghost" :disabled="creating" @click="showNew = false">cancelar</Button>
          <Button :disabled="creating || !draft.codigo" @click="createCadastro">
            {{ creating ? 'criando…' : 'Criar' }}
          </Button>
        </div>
      </div>
    </div>

    <div v-if="editing" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" @click.self="editing = null">
      <div class="bg-background border rounded-lg w-full max-w-2xl p-5 space-y-4 max-h-[90vh] overflow-auto">
        <div class="flex items-center">
          <h2 class="text-lg font-semibold">Editar cadastro</h2>
          <Button class="ml-auto" size="sm" variant="ghost" @click="editing = null">
            <X class="size-4" />
          </Button>
        </div>
        <div class="grid grid-cols-2 gap-3">
          <div>
            <Label>Tipo *</Label>
            <select v-model="editDraft.tipo" class="w-full border rounded px-2 py-1 bg-background">
              <option v-for="t in TIPOS" :key="t" :value="t">{{ cap(t) }}</option>
            </select>
          </div>
          <div>
            <Label>Provedor</Label>
            <Input v-model="editDraft.provedor" placeholder="tim, vivo, …" />
          </div>
          <div>
            <Label>Código *</Label>
            <Input v-model="editDraft.codigo" required />
          </div>
          <div>
            <Label>Label</Label>
            <Input v-model="editDraft.label" />
          </div>
        </div>
        <div>
          <Label>Observação</Label>
          <Input v-model="editDraft.obs" />
        </div>

        <div>
          <Label>Vincular a lojas</Label>
          <div class="border rounded p-2 max-h-64 overflow-auto space-y-2 mt-1">
            <div v-for="(group, apelido) in storesByCompanyMk" :key="apelido">
              <div class="text-xs font-semibold text-muted-foreground">{{ apelido }}</div>
              <div v-for="s in group" :key="s.id" class="flex items-center gap-2 text-xs px-2 py-0.5">
                <input
                  type="checkbox"
                  :checked="editLinks[s.id]?.selected"
                  @change="toggleEditStore(s.id)"
                />
                <span class="flex-1">{{ MARKETPLACE_SHORT[s.marketplace as Marketplace] }} — {{ s.apelido_override || apelido }}</span>
                <Input
                  v-if="editLinks[s.id]?.selected"
                  v-model="editLinks[s.id].alias"
                  placeholder="alias"
                  class="h-6 w-32 text-xs"
                />
              </div>
            </div>
            <div v-if="!stores.length" class="text-xs text-muted-foreground">nenhuma loja cadastrada</div>
          </div>
        </div>

        <div v-if="editErr" class="text-sm text-red-500">erro: {{ editErr }}</div>
        <div class="flex justify-end gap-2">
          <Button variant="ghost" :disabled="editSubmitting" @click="editing = null">cancelar</Button>
          <Button :disabled="editSubmitting || !editDraft.codigo" @click="submitEdit">
            {{ editSubmitting ? 'salvando…' : 'Salvar' }}
          </Button>
        </div>
      </div>
    </div>

    <div v-if="resolving" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" @click.self="resolving = null">
      <div class="bg-background border rounded-lg w-full max-w-md p-5 space-y-4">
        <div class="flex items-center">
          <h2 class="text-lg font-semibold">
            Vincular {{ MARKETPLACE_SHORT[resolving.marketplace as Marketplace] }} → loja
          </h2>
          <Button class="ml-auto" size="sm" variant="ghost" @click="resolving = null">
            <X class="size-4" />
          </Button>
        </div>
        <div class="text-xs text-muted-foreground">
          Valor original na planilha: <span class="font-mono">{{ resolving.rawValue }}</span>
        </div>
        <div>
          <Label>Loja</Label>
          <select v-model="resolveStoreId" class="w-full border rounded px-2 py-1 bg-background text-sm">
            <option value="">— selecione —</option>
            <option v-for="s in storesForResolveMk" :key="s.id" :value="s.id">
              {{ companyById[s.company_id]?.apelido || '?' }}
              <template v-if="s.apelido_override"> ({{ s.apelido_override }})</template>
              — {{ s.status }}
            </option>
          </select>
          <div v-if="!storesForResolveMk.length" class="text-xs text-muted-foreground mt-1">
            nenhuma loja cadastrada nesse marketplace
          </div>
        </div>
        <div>
          <Label>Alias (opcional)</Label>
          <Input v-model="resolveAlias" :placeholder="resolving.rawValue" />
        </div>
        <div v-if="resolveErr" class="text-sm text-red-500">erro: {{ resolveErr }}</div>
        <div class="flex justify-end gap-2">
          <Button variant="ghost" :disabled="resolveSubmitting" @click="resolving = null">cancelar</Button>
          <Button :disabled="resolveSubmitting || !resolveStoreId" @click="submitResolve">
            {{ resolveSubmitting ? 'vinculando…' : 'Vincular' }}
          </Button>
        </div>
      </div>
    </div>
  </div>
</template>
