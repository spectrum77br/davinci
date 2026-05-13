<script setup lang="ts">
import { ref, computed, reactive } from 'vue'
import { Plus, RefreshCw, X } from 'lucide-vue-next'
import {
  MARKETPLACES,
  MARKETPLACE_SHORT,
  STORE_STATUS_LABELS,
  STORE_STATUS_CLASSES,
  type Marketplace,
  type StoreStatus,
} from '~/composables/useMarketplaces'

definePageMeta({ middleware: ['permission'], permission: { resource: 'empresa', action: 'view' } })

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
  enabled_marketplaces: string[]
  created_at: string
  updated_at: string
}

type GridRow = { company: CompanyOut; stores: Record<string, GridStoreCell | null> }
type GridOut = { marketplaces: string[]; rows: GridRow[] }

type StoreInfoLite = {
  id: string
  account_name: string | null
  cpf_name: string | null
  platform: string
}

const { api } = useApi()
const grid = ref<GridOut | null>(null)
const storeInfos = ref<StoreInfoLite[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const filterMk = ref<string>('')
const filterUf = ref<string>('')
const filterResponsavel = ref<string>('')
const search = ref<string>('')
const showNew = ref(false)

const canEdit = useCan('empresa', 'edit')

async function refresh() {
  loading.value = true
  error.value = null
  try {
    const [gridRes, storeRes] = await Promise.all([
      api<GridOut>('/api/companies/grid'),
      api<StoreInfoLite[]>('/api/pricing/store-info').catch(() => [] as StoreInfoLite[]),
    ])
    grid.value = gridRes
    storeInfos.value = storeRes
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    loading.value = false
  }
}
await refresh()

// Distinct cpf_name list (responsáveis), alphabetical, lowered for keys.
const responsaveisOpts = computed(() => {
  const set = new Set<string>()
  for (const s of storeInfos.value) {
    const n = (s.cpf_name || '').trim()
    if (n) set.add(n)
  }
  return Array.from(set).sort((a, b) => a.localeCompare(b, 'pt-BR'))
})

// Aggregate store_info per company.apelido for the Responsável column.
// Returns the first non-empty cpf_name + the list of store_info ids that
// share this account_name (so editing can fan out a PATCH to each).
const respByApelido = computed(() => {
  const m = new Map<string, { cpf: string; ids: string[] }>()
  for (const s of storeInfos.value) {
    const k = (s.account_name || '').trim().toLowerCase()
    if (!k) continue
    const e = m.get(k) || { cpf: '', ids: [] }
    e.ids.push(s.id)
    if (!e.cpf && s.cpf_name) e.cpf = s.cpf_name
    m.set(k, e)
  }
  return m
})

// Companies linked to a given responsável: a company "has" the responsavel
// when at least one of its store_info rows (matched by store_info.platform
// + store.apelido_override or stores+apelido) shares the cpf_name. Pragmatic
// match: company.apelido (lower) equals any store_info.account_name (lower)
// linked to that responsavel. Falls back to true when no filter selected.
const responsavelByCompany = computed(() => {
  const filtered = filterResponsavel.value
    ? storeInfos.value.filter((s) => (s.cpf_name || '').trim() === filterResponsavel.value)
    : storeInfos.value
  const namesByLower = new Set<string>()
  for (const s of filtered) {
    const n = (s.account_name || '').trim().toLowerCase()
    if (n) namesByLower.add(n)
  }
  return namesByLower
})

const filteredRows = computed(() => {
  if (!grid.value) return []
  let rows = grid.value.rows
  if (filterUf.value) rows = rows.filter(r => (r.company.uf || '').toUpperCase() === filterUf.value.toUpperCase())
  if (filterMk.value) rows = rows.filter(r => r.stores[filterMk.value] != null)
  if (filterResponsavel.value) {
    const allowed = responsavelByCompany.value
    rows = rows.filter(r => allowed.has((r.company.apelido || '').trim().toLowerCase()))
  }
  if (search.value) {
    const q = search.value.toLowerCase()
    const respMap = respByApelido.value
    rows = rows.filter(r => {
      const resp = (respMap.get(r.company.apelido.trim().toLowerCase())?.cpf || '').toLowerCase()
      return (
        r.company.razao_social.toLowerCase().includes(q) ||
        r.company.apelido.toLowerCase().includes(q) ||
        (r.company.cnpj || '').includes(q) ||
        resp.includes(q)
      )
    })
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

// ---------- inline Responsável edit ----------
const editingResp = ref<string | null>(null) // apelido_lower being edited
const respValue = ref('')
const respSaving = ref(false)
function startEditResp(apelido: string) {
  if (!canEdit.value) return
  const k = apelido.trim().toLowerCase()
  editingResp.value = k
  respValue.value = respByApelido.value.get(k)?.cpf || ''
}
function cancelEditResp() {
  editingResp.value = null
  respValue.value = ''
}
async function commitEditResp(apelido: string) {
  const k = apelido.trim().toLowerCase()
  if (editingResp.value !== k) return
  const next = respValue.value.trim()
  const entry = respByApelido.value.get(k)
  if (next === (entry?.cpf || '')) {
    cancelEditResp()
    return
  }
  if (!entry || entry.ids.length === 0) {
    // No store_info row exists yet — Responsável lives on store_info.cpf_name,
    // so we can't persist it until at least one loja exists for this company.
    error.value = `Crie uma loja para "${apelido}" antes de atribuir um responsável.`
    cancelEditResp()
    return
  }
  respSaving.value = true
  try {
    // Fan out the patch: every store_info row sharing this account_name
    // gets the same cpf_name. Mirrors how the Responsável filter aggregates.
    await Promise.all(
      entry.ids.map((id) =>
        api(`/api/pricing/store-info/${id}`, {
          method: 'PATCH',
          body: { cpf_name: next || null },
        }),
      ),
    )
    // Patch in-memory so the cell updates without a full refresh.
    for (const s of storeInfos.value) {
      if ((s.account_name || '').trim().toLowerCase() === k) {
        s.cpf_name = next || null
      }
    }
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    respSaving.value = false
    cancelEditResp()
  }
}

// ---------- inline obs edit ----------
const editingObs = ref<string | null>(null)
const obsValue = ref('')
const obsSaving = ref(false)
function startEditObs(row: GridRow) {
  if (!canEdit.value) return
  editingObs.value = row.company.id
  obsValue.value = row.company.obs || ''
}
function cancelEditObs() {
  editingObs.value = null
  obsValue.value = ''
}
async function commitEditObs(row: GridRow) {
  if (editingObs.value !== row.company.id) return
  const next = obsValue.value.trim()
  const prev = row.company.obs || ''
  if (next === prev.trim()) return cancelEditObs()
  obsSaving.value = true
  try {
    await api(`/api/companies/${row.company.id}`, {
      method: 'PATCH',
      body: { obs: next || null },
    })
    row.company.obs = next || null
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    obsSaving.value = false
    cancelEditObs()
  }
}

// ---------- new account modal (Store + store_info) ----------
const newAccountFor = ref<{ company: CompanyOut; mk: Marketplace } | null>(null)
const newAccountForm = reactive({ phone: '', email: '', server: '' })
const newAccountSaving = ref(false)
const newAccountErr = ref<string | null>(null)

function openNewAccount(row: GridRow, mk: Marketplace) {
  if (!canEdit.value) return
  newAccountFor.value = { company: row.company, mk }
  newAccountForm.phone = ''
  newAccountForm.email = ''
  newAccountForm.server = ''
  newAccountErr.value = null
}

function closeNewAccount() {
  if (newAccountSaving.value) return
  newAccountFor.value = null
}

async function submitNewAccount() {
  if (!newAccountFor.value) return
  const { company, mk } = newAccountFor.value
  const phone = newAccountForm.phone.trim()
  const email = newAccountForm.email.trim()
  const server = newAccountForm.server.trim()
  if (!phone || !email || !server) {
    newAccountErr.value = 'Fone, e-mail e servidor são obrigatórios.'
    return
  }
  newAccountSaving.value = true
  newAccountErr.value = null
  try {
    // 1. Create the Store cell (gated by enabled_marketplaces server-side).
    await api('/api/stores', {
      method: 'POST',
      body: { company_id: company.id, marketplace: mk, status: 'active' },
    })
    // 2. Mirror to store_info so the data shows up on the Lojas page.
    //    Failure here is non-fatal — the Store still exists; user can fill
    //    the fields manually later.
    try {
      await api('/api/pricing/store-info', {
        method: 'POST',
        body: {
          platform: mk,
          account_name: company.apelido,
          phone,
          email,
          server,
        },
      })
    } catch (e: any) {
      // Surface as a warning but don't roll back.
      error.value = `Loja criada, mas store_info falhou: ${e?.data?.detail?.code || e?.message || 'erro'}`
    }
    await refresh()
    closeNewAccount()
  } catch (e: any) {
    newAccountErr.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    newAccountSaving.value = false
  }
}

async function createStoreCell(companyId: string, mk: Marketplace) {
  // Kept for backward compat — the "+" button now opens the modal instead.
  const row = grid.value?.rows.find((r) => r.company.id === companyId)
  if (!row) return
  openNewAccount(row, mk)
}

async function toggleMarketplaceEnabled(row: GridRow, mk: Marketplace) {
  if (!canEdit.value) return
  const enabled = new Set(row.company.enabled_marketplaces || [])
  const willEnable = !enabled.has(mk)
  const verb = willEnable ? 'Liberar' : 'Bloquear'
  if (!confirm(`${verb} ${MARKETPLACE_SHORT[mk]} para ${row.company.apelido}?`)) return
  if (willEnable) enabled.add(mk)
  else enabled.delete(mk)
  try {
    const updated = await api<CompanyOut>(`/api/companies/${row.company.id}`, {
      method: 'PATCH',
      body: { enabled_marketplaces: Array.from(enabled) },
    })
    row.company.enabled_marketplaces = updated.enabled_marketplaces
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
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
        <Input v-model="search" placeholder="razão social / apelido / CNPJ / responsável" class="w-64" />
        <Input v-model="filterUf" placeholder="UF" class="w-20" />
        <select v-model="filterMk" class="border rounded px-2 text-sm bg-background">
          <option value="">todos marketplaces</option>
          <option v-for="mk in MARKETPLACES" :key="mk" :value="mk">{{ MARKETPLACE_SHORT[mk] }}</option>
        </select>
        <select v-model="filterResponsavel" class="border rounded px-2 text-sm bg-background">
          <option value="">todos responsáveis</option>
          <option v-for="r in responsaveisOpts" :key="r" :value="r">{{ r }}</option>
        </select>
        <Button v-if="canEdit" size="sm" @click="showNew = true">
          <Plus class="size-4 mr-1" /> Nova empresa
        </Button>
      </div>
    </div>

    <div v-if="error" class="text-sm text-red-500">erro: {{ error }}</div>

    <div class="border rounded-md overflow-auto max-h-[calc(100vh-220px)]">
      <table class="w-full text-sm">
        <thead class="bg-muted text-left sticky top-0 z-10 shadow-[inset_0_-1px_0_var(--border)]">
          <tr>
            <th class="px-3 py-2 sticky left-0 bg-muted z-20">EMPRESA</th>
            <th class="px-3 py-2">UF</th>
            <th class="px-3 py-2">CNPJ</th>
            <th class="px-3 py-2">I.E.</th>
            <th class="px-3 py-2">conta</th>
            <th class="px-3 py-2">Responsável</th>
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
            <td
              class="px-3 py-2 text-xs max-w-40"
              :class="{
                'cursor-pointer hover:bg-accent/30':
                  canEdit && editingResp !== row.company.apelido.trim().toLowerCase(),
              }"
              :title="respByApelido.get(row.company.apelido.trim().toLowerCase())?.cpf || ''"
              @click="
                canEdit
                && editingResp !== row.company.apelido.trim().toLowerCase()
                && startEditResp(row.company.apelido)
              "
            >
              <input
                v-if="editingResp === row.company.apelido.trim().toLowerCase()"
                v-model="respValue"
                type="text"
                class="w-full text-xs bg-transparent outline-none border-b border-blue-500"
                :disabled="respSaving"
                autofocus
                @blur="commitEditResp(row.company.apelido)"
                @keydown.enter.prevent="commitEditResp(row.company.apelido)"
                @keydown.escape.prevent="cancelEditResp"
              />
              <span
                v-else
                :class="{ 'text-muted-foreground': !respByApelido.get(row.company.apelido.trim().toLowerCase())?.cpf }"
                class="block truncate"
              >
                {{ respByApelido.get(row.company.apelido.trim().toLowerCase())?.cpf || '—' }}
              </span>
            </td>
            <td v-for="mk in MARKETPLACES" :key="mk" class="px-2 py-2 text-center">
              <template v-if="row.stores[mk]">
                <span :class="STORE_STATUS_CLASSES[row.stores[mk]!.status]">
                  {{ STORE_STATUS_LABELS[row.stores[mk]!.status] }}
                </span>
              </template>
              <template v-else-if="!(row.company.enabled_marketplaces || []).includes(mk)">
                <button
                  v-if="canEdit"
                  class="text-red-500 font-semibold"
                  :title="`${MARKETPLACE_SHORT[mk]} bloqueado para esta empresa — clique para liberar`"
                  @click="toggleMarketplaceEnabled(row, mk)"
                >×</button>
                <span v-else class="text-red-500" :title="`${MARKETPLACE_SHORT[mk]} bloqueado`">×</span>
              </template>
              <button
                v-else-if="canEdit"
                class="text-muted-foreground hover:text-foreground"
                :title="`Criar loja em ${MARKETPLACE_SHORT[mk]}`"
                @click="createStoreCell(row.company.id, mk)"
              >+</button>
            </td>
            <td class="px-3 py-2 truncate max-w-32">
              <a v-if="row.company.site_url" :href="row.company.site_url" target="_blank" class="hover:underline text-xs">
                {{ row.company.site_url }}
              </a>
              <span v-else class="text-muted-foreground">—</span>
            </td>
            <td
              class="px-3 py-2 text-xs max-w-48"
              :class="{ 'cursor-pointer hover:bg-accent/30': canEdit && editingObs !== row.company.id }"
              :title="row.company.obs || ''"
              @click="canEdit && editingObs !== row.company.id && startEditObs(row)"
            >
              <input
                v-if="editingObs === row.company.id"
                v-model="obsValue"
                type="text"
                class="w-full text-xs bg-transparent outline-none border-b border-blue-500"
                :disabled="obsSaving"
                autofocus
                @blur="commitEditObs(row)"
                @keydown.enter.prevent="commitEditObs(row)"
                @keydown.escape.prevent="cancelEditObs"
              />
              <span v-else :class="{ 'text-muted-foreground': !row.company.obs }" class="block truncate">
                {{ row.company.obs || '—' }}
              </span>
            </td>
          </tr>
          <tr v-if="!loading && filteredRows.length === 0">
            <td :colspan="17" class="px-3 py-6 text-center text-muted-foreground">nenhuma empresa</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Nova conta (Store + store_info) -->
    <div
      v-if="newAccountFor"
      class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4"
      @click.self="closeNewAccount"
    >
      <div class="bg-background border rounded-lg w-full max-w-md p-5 space-y-4">
        <div class="flex items-center">
          <div>
            <h2 class="text-lg font-semibold">Nova conta</h2>
            <p class="text-xs text-muted-foreground">
              {{ newAccountFor.company.apelido }} · {{ MARKETPLACE_SHORT[newAccountFor.mk] }}
            </p>
          </div>
          <Button class="ml-auto" size="sm" variant="ghost" :disabled="newAccountSaving" @click="closeNewAccount">
            <X class="size-4" />
          </Button>
        </div>
        <div class="space-y-3">
          <div>
            <Label>Fone <span class="text-red-500">*</span></Label>
            <Input v-model="newAccountForm.phone" :disabled="newAccountSaving" placeholder="11999999999" />
          </div>
          <div>
            <Label>E-mail <span class="text-red-500">*</span></Label>
            <Input v-model="newAccountForm.email" :disabled="newAccountSaving" placeholder="conta@dominio" />
          </div>
          <div>
            <Label>Servidor <span class="text-red-500">*</span></Label>
            <Input v-model="newAccountForm.server" :disabled="newAccountSaving" placeholder="ex: 76" />
          </div>
          <p class="text-xs text-muted-foreground">
            Cria a loja e o registro correspondente em Lojas (info) — você pode editar os outros campos depois.
          </p>
        </div>
        <div v-if="newAccountErr" class="text-sm text-red-500">erro: {{ newAccountErr }}</div>
        <div class="flex justify-end gap-2">
          <Button variant="ghost" :disabled="newAccountSaving" @click="closeNewAccount">cancelar</Button>
          <Button
            :disabled="newAccountSaving || !newAccountForm.phone.trim() || !newAccountForm.email.trim() || !newAccountForm.server.trim()"
            @click="submitNewAccount"
          >
            {{ newAccountSaving ? 'criando…' : 'Criar conta' }}
          </Button>
        </div>
      </div>
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
