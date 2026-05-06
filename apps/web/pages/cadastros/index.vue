<script setup lang="ts">
import { ref, computed } from 'vue'
import { Plus, RefreshCw, X } from 'lucide-vue-next'
import {
  MARKETPLACES,
  MARKETPLACE_SHORT,
  type Marketplace,
} from '~/composables/useMarketplaces'

definePageMeta({ middleware: ['auth', 'permission'], permission: { resource: 'cadastro', action: 'view' } })

type CadastroOut = {
  id: string
  tipo: string
  provedor: string | null
  responsavel_id: string | null
  codigo: string
  label: string | null
  status: string
  obs: string | null
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

const TIPOS = ['fone', 'email', 'dominio']

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

function cellClass(cell: Cell): string {
  if (cell.store_status === 'inactive') return 'line-through text-muted-foreground'
  if (cell.store_status === 'closing') return 'bg-amber-500/10 text-amber-400'
  if (cell.store_status === 'banned') return 'text-red-400'
  return ''
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
            <th class="px-3 py-2">tipo</th>
            <th class="px-3 py-2">provedor</th>
            <th class="px-3 py-2">código</th>
            <th class="px-3 py-2">label</th>
            <th v-for="mk in MARKETPLACES" :key="mk" class="px-2 py-2 text-center">
              {{ MARKETPLACE_SHORT[mk] }}
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in filteredRows" :key="row.cadastro.id" class="border-t hover:bg-muted/20">
            <td class="px-3 py-2">{{ row.cadastro.tipo }}</td>
            <td class="px-3 py-2">{{ row.cadastro.provedor || '—' }}</td>
            <td class="px-3 py-2 font-mono">{{ row.cadastro.codigo }}</td>
            <td class="px-3 py-2 text-xs text-muted-foreground">{{ row.cadastro.label || '—' }}</td>
            <td v-for="mk in MARKETPLACES" :key="mk" class="px-2 py-2 text-xs">
              <div v-if="row.cells[mk]?.length" class="space-y-0.5">
                <div
                  v-for="cell in row.cells[mk]"
                  :key="cell.store_id"
                  :class="cellClass(cell)"
                  :title="canEdit ? 'duplo-clique para editar alias' : ''"
                  @dblclick="canEdit && (() => {
                    const v = prompt('alias', cell.alias || '')
                    if (v !== null) updateAlias(row.cadastro.id, cell.store_id, cell.alias, v)
                  })()"
                >
                  {{ cellLabel(cell) }}
                </div>
              </div>
            </td>
          </tr>
          <tr v-if="!loading && filteredRows.length === 0">
            <td :colspan="13" class="px-3 py-6 text-center text-muted-foreground">nenhum cadastro</td>
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
              <option v-for="t in TIPOS" :key="t" :value="t">{{ t }}</option>
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
  </div>
</template>
