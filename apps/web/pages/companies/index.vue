<script setup lang="ts">
import { ref, computed } from 'vue'
import { Plus, RefreshCw, X } from 'lucide-vue-next'
import {
  MARKETPLACES,
  MARKETPLACE_SHORT,
  STORE_STATUS_LABELS,
  STORE_STATUS_CLASSES,
  type Marketplace,
  type StoreStatus,
} from '~/composables/useMarketplaces'

definePageMeta({ middleware: ['auth', 'permission'], permission: { resource: 'empresa', action: 'view' } })

type GridStoreCell = {
  id: string
  status: StoreStatus
  label: string
  integration_id: string | null
  bling_store_id: number | null
}

type CompanyOut = {
  id: string
  razao_social: string
  apelido: string
  responsavel_id: string | null
  uf: string | null
  cnpj: string | null
  inscricao_estadual: string | null
  site_url: string | null
  obs: string | null
  created_at: string
  updated_at: string
}

type GridRow = { company: CompanyOut; stores: Record<string, GridStoreCell | null> }
type GridOut = { marketplaces: string[]; rows: GridRow[] }

const { api } = useApi()
const grid = ref<GridOut | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)
const filterMk = ref<string>('')
const filterUf = ref<string>('')
const search = ref<string>('')
const showNew = ref(false)

const canEdit = useCan('empresa', 'edit')

async function refresh() {
  loading.value = true
  error.value = null
  try {
    grid.value = await api<GridOut>('/api/companies/grid')
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
  if (filterUf.value) rows = rows.filter(r => (r.company.uf || '').toUpperCase() === filterUf.value.toUpperCase())
  if (filterMk.value) rows = rows.filter(r => r.stores[filterMk.value] != null)
  if (search.value) {
    const q = search.value.toLowerCase()
    rows = rows.filter(r =>
      r.company.razao_social.toLowerCase().includes(q) ||
      r.company.apelido.toLowerCase().includes(q) ||
      (r.company.cnpj || '').includes(q)
    )
  }
  return rows
})

const draft = ref({ razao_social: '', apelido: '', cnpj: '', uf: '', inscricao_estadual: '', site_url: '', obs: '' })
const creating = ref(false)
const createErr = ref<string | null>(null)

async function createCompany() {
  creating.value = true
  createErr.value = null
  try {
    const body: Record<string, any> = {
      razao_social: draft.value.razao_social,
      apelido: draft.value.apelido,
    }
    for (const k of ['cnpj', 'uf', 'inscricao_estadual', 'site_url', 'obs'] as const) {
      if (draft.value[k]) body[k] = draft.value[k]
    }
    await api('/api/companies', { method: 'POST', body })
    showNew.value = false
    draft.value = { razao_social: '', apelido: '', cnpj: '', uf: '', inscricao_estadual: '', site_url: '', obs: '' }
    await refresh()
  } catch (e: any) {
    createErr.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    creating.value = false
  }
}

async function createStoreCell(companyId: string, mk: Marketplace) {
  if (!canEdit.value) return
  if (!confirm(`Criar loja em ${MARKETPLACE_SHORT[mk]} para esta empresa?`)) return
  try {
    await api('/api/stores', {
      method: 'POST',
      body: { company_id: companyId, marketplace: mk, status: 'active' },
    })
    await refresh()
  } catch (e: any) {
    error.value = e?.data?.detail?.code || 'erro'
  }
}
</script>

<template>
  <div class="space-y-4">
    <div class="flex items-center gap-3 flex-wrap">
      <h1 class="text-2xl font-semibold">Empresas</h1>
      <Button size="sm" variant="ghost" :disabled="loading" @click="refresh">
        <RefreshCw class="size-4 mr-1" /> recarregar
      </Button>
      <div class="ml-auto flex gap-2 flex-wrap">
        <Input v-model="search" placeholder="razão social / apelido / CNPJ" class="w-64" />
        <Input v-model="filterUf" placeholder="UF" class="w-20" />
        <select v-model="filterMk" class="border rounded px-2 text-sm bg-background">
          <option value="">todos marketplaces</option>
          <option v-for="mk in MARKETPLACES" :key="mk" :value="mk">{{ MARKETPLACE_SHORT[mk] }}</option>
        </select>
        <Button v-if="canEdit" size="sm" @click="showNew = true">
          <Plus class="size-4 mr-1" /> Nova empresa
        </Button>
      </div>
    </div>

    <div v-if="error" class="text-sm text-red-500">erro: {{ error }}</div>

    <div class="border rounded-md overflow-x-auto">
      <table class="w-full text-sm">
        <thead class="bg-muted/40 text-left">
          <tr>
            <th class="px-3 py-2 sticky left-0 bg-muted/40">EMPRESA</th>
            <th class="px-3 py-2">UF</th>
            <th class="px-3 py-2">CNPJ</th>
            <th class="px-3 py-2">I.E.</th>
            <th class="px-3 py-2">conta</th>
            <th v-for="mk in MARKETPLACES" :key="mk" class="px-2 py-2 text-center">
              {{ MARKETPLACE_SHORT[mk] }}
            </th>
            <th class="px-3 py-2">site</th>
            <th class="px-3 py-2">obs</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in filteredRows" :key="row.company.id" class="border-t hover:bg-muted/20">
            <td class="px-3 py-2 sticky left-0 bg-background">
              <NuxtLink :to="`/companies/${row.company.id}`" class="hover:underline font-medium">
                {{ row.company.razao_social }}
              </NuxtLink>
            </td>
            <td class="px-3 py-2">{{ row.company.uf || '—' }}</td>
            <td class="px-3 py-2 font-mono text-xs">{{ row.company.cnpj || '—' }}</td>
            <td class="px-3 py-2">{{ row.company.inscricao_estadual || '—' }}</td>
            <td class="px-3 py-2">{{ row.company.apelido }}</td>
            <td v-for="mk in MARKETPLACES" :key="mk" class="px-2 py-2 text-center">
              <template v-if="row.stores[mk]">
                <span :class="STORE_STATUS_CLASSES[row.stores[mk]!.status]">
                  {{ STORE_STATUS_LABELS[row.stores[mk]!.status] }}
                </span>
              </template>
              <button
                v-else-if="canEdit"
                class="text-muted-foreground hover:text-foreground"
                @click="createStoreCell(row.company.id, mk)"
              >+</button>
            </td>
            <td class="px-3 py-2 truncate max-w-32">
              <a v-if="row.company.site_url" :href="row.company.site_url" target="_blank" class="hover:underline text-xs">
                {{ row.company.site_url }}
              </a>
              <span v-else class="text-muted-foreground">—</span>
            </td>
            <td class="px-3 py-2 text-xs text-muted-foreground truncate max-w-48">
              {{ row.company.obs || '—' }}
            </td>
          </tr>
          <tr v-if="!loading && filteredRows.length === 0">
            <td :colspan="14" class="px-3 py-6 text-center text-muted-foreground">nenhuma empresa</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="showNew" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" @click.self="showNew = false">
      <div class="bg-background border rounded-lg w-full max-w-lg p-5 space-y-4">
        <div class="flex items-center">
          <h2 class="text-lg font-semibold">Nova empresa</h2>
          <Button class="ml-auto" size="sm" variant="ghost" @click="showNew = false">
            <X class="size-4" />
          </Button>
        </div>
        <div class="space-y-3">
          <div>
            <Label>Razão social *</Label>
            <Input v-model="draft.razao_social" required />
          </div>
          <div class="grid grid-cols-2 gap-3">
            <div>
              <Label>Apelido *</Label>
              <Input v-model="draft.apelido" required />
            </div>
            <div>
              <Label>UF</Label>
              <Input v-model="draft.uf" maxlength="2" />
            </div>
            <div>
              <Label>CNPJ</Label>
              <Input v-model="draft.cnpj" />
            </div>
            <div>
              <Label>I.E.</Label>
              <Input v-model="draft.inscricao_estadual" />
            </div>
          </div>
          <div>
            <Label>Site</Label>
            <Input v-model="draft.site_url" />
          </div>
          <div>
            <Label>Observação</Label>
            <Input v-model="draft.obs" />
          </div>
        </div>
        <div v-if="createErr" class="text-sm text-red-500">erro: {{ createErr }}</div>
        <div class="flex justify-end gap-2">
          <Button variant="ghost" :disabled="creating" @click="showNew = false">cancelar</Button>
          <Button :disabled="creating || !draft.razao_social || !draft.apelido" @click="createCompany">
            {{ creating ? 'criando…' : 'Criar' }}
          </Button>
        </div>
      </div>
    </div>
  </div>
</template>
