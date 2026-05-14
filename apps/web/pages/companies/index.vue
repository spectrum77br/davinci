<script setup lang="ts">
import { ref, computed, reactive } from 'vue'
import { Plus, RefreshCw, X, ExternalLink } from 'lucide-vue-next'
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
  id: string | null
  status: StoreStatus
  label: string
  integration_id: string | null
  bling_store_id: number | null
  from_store_info?: boolean
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
  phone: string | null
  email: string | null
  server: string | null
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

// ---------- generic inline edit for company text fields ----------
type EditableField = 'razao_social' | 'apelido' | 'uf' | 'cnpj' | 'inscricao_estadual' | 'site_url'

const editingCell = ref<{ id: string; field: EditableField } | null>(null)
const editCellValue = ref('')
const editCellSaving = ref(false)

function startEditCell(row: GridRow, field: EditableField) {
  if (!canEdit.value) return
  editingCell.value = { id: row.company.id, field }
  editCellValue.value = (row.company[field] || '') as string
}
function cancelEditCell() {
  editingCell.value = null
  editCellValue.value = ''
}
function isEditingCell(row: GridRow, field: EditableField) {
  return editingCell.value?.id === row.company.id && editingCell.value?.field === field
}
async function commitEditCell(row: GridRow, field: EditableField) {
  if (!isEditingCell(row, field)) return
  const next = editCellValue.value.trim()
  const prev = (row.company[field] || '') as string
  if (next === prev.trim()) return cancelEditCell()
  // razao_social and apelido are non-null on the server; refuse to blank them.
  if ((field === 'razao_social' || field === 'apelido') && !next) {
    error.value = `${field === 'razao_social' ? 'Razão social' : 'Apelido'} não pode ficar vazio.`
    return cancelEditCell()
  }
  editCellSaving.value = true
  try {
    const updated = await api<CompanyOut>(`/api/companies/${row.company.id}`, {
      method: 'PATCH',
      body: { [field]: next || null },
    })
    row.company[field] = updated[field] as any
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    editCellSaving.value = false
    cancelEditCell()
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
type CadastroLite = { id: string; codigo: string; label: string | null }

const newAccountFor = ref<{ company: CompanyOut; mk: Marketplace } | null>(null)
const newAccountForm = reactive({ phoneId: '', emailId: '', server: '' })
const availablePhones = ref<CadastroLite[]>([])
const availableEmails = ref<CadastroLite[]>([])
const newAccountSaving = ref(false)
const newAccountErr = ref<string | null>(null)
const newAccountResult = ref<string | null>(null)

async function loadAvailableCadastros(mk: Marketplace) {
  try {
    const [phones, emails] = await Promise.all([
      api<CadastroLite[]>(`/api/cadastros/available?tipo=fone&marketplace=${mk}`),
      api<CadastroLite[]>(`/api/cadastros/available?tipo=email&marketplace=${mk}`),
    ])
    availablePhones.value = phones
    availableEmails.value = emails
  } catch (e: any) {
    newAccountErr.value = e?.data?.detail?.code || e?.message || 'erro ao carregar cadastros'
    availablePhones.value = []
    availableEmails.value = []
  }
}

async function openNewAccount(row: GridRow, mk: Marketplace) {
  if (!canEdit.value) return
  newAccountFor.value = { company: row.company, mk }
  newAccountForm.phoneId = ''
  newAccountForm.emailId = ''
  newAccountForm.server = ''
  newAccountErr.value = null
  newAccountResult.value = null
  await loadAvailableCadastros(mk)
}

function closeNewAccount() {
  if (newAccountSaving.value) return
  newAccountFor.value = null
}

async function submitNewAccount() {
  if (!newAccountFor.value) return
  const { company, mk } = newAccountFor.value
  const phoneCad = availablePhones.value.find((c) => c.id === newAccountForm.phoneId)
  const emailCad = availableEmails.value.find((c) => c.id === newAccountForm.emailId)
  const server = newAccountForm.server.trim()
  if (!phoneCad || !emailCad || !server) {
    newAccountErr.value = 'Fone, e-mail e servidor são obrigatórios.'
    return
  }
  newAccountSaving.value = true
  newAccountErr.value = null
  try {
    // 1. Create the Store cell (gated by enabled_marketplaces server-side).
    const store = await api<{ id: string }>('/api/stores', {
      method: 'POST',
      body: { company_id: company.id, marketplace: mk, status: 'active' },
    })

    // 2. Mirror to store_info so the data shows up on the Lojas page.
    try {
      await api('/api/pricing/store-info', {
        method: 'POST',
        body: {
          platform: mk,
          account_name: company.apelido,
          phone: phoneCad.codigo,
          email: emailCad.codigo,
          server,
        },
      })
    } catch (e: any) {
      error.value = `Loja criada, mas store_info falhou: ${e?.data?.detail?.code || e?.message || 'erro'}`
    }

    // 3. Link the chosen fone/email Cadastros to the new Store so they show
    //    up as "in use" on this marketplace and disappear from future
    //    dropdowns. Each is non-fatal — if the link fails the loja still
    //    exists and the operator can wire it up manually in /cadastros.
    for (const cad of [phoneCad, emailCad]) {
      try {
        await api(`/api/cadastros/${cad.id}/stores`, {
          method: 'POST',
          body: { store_id: store.id, alias: company.apelido },
        })
      } catch (e: any) {
        error.value = `Loja criada, mas link Cadastros falhou para ${cad.codigo}: ${e?.data?.detail?.code || e?.message || 'erro'}`
      }
    }

    newAccountResult.value =
      `Conta criada: ${company.apelido} · ${MARKETPLACE_SHORT[mk]} — ` +
      `Fone ${phoneCad.codigo} · Email ${emailCad.codigo} · Servidor ${server}`
    await refresh()
    // Keep modal open briefly so user sees the result toast, then close.
    setTimeout(() => closeNewAccount(), 1500)
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

// ---------- store cell popover (Ver detalhes / Remover conta) ----------
const cellPopoverFor = ref<string | null>(null) // `${companyId}:${mk}`
function openCellPopover(companyId: string, mk: Marketplace) {
  if (!canEdit.value) return
  const key = `${companyId}:${mk}`
  cellPopoverFor.value = cellPopoverFor.value === key ? null : key
}
function closeCellPopover() { cellPopoverFor.value = null }

const onDocClickCell = () => { cellPopoverFor.value = null }
onMounted(() => document.addEventListener('click', onDocClickCell))
onBeforeUnmount(() => document.removeEventListener('click', onDocClickCell))

function storeInfoFor(row: GridRow, mk: Marketplace): StoreInfoLite | undefined {
  const apelido = (row.company.apelido || '').trim().toLowerCase()
  return storeInfos.value.find(
    (s) => s.platform === mk && (s.account_name || '').trim().toLowerCase() === apelido,
  )
}

async function removeStoreCell(row: GridRow, mk: Marketplace) {
  const cell = row.stores[mk]
  if (!cell || !cell.id) return
  const apelido = row.company.apelido
  if (!confirm(`Remover conta de ${apelido} no ${MARKETPLACE_SHORT[mk]}? Vai apagar Loja, dados em store_info e vínculos em Cadastros.`)) return
  try {
    await api(`/api/stores/${cell.id}`, { method: 'DELETE' })
    closeCellPopover()
    await refresh()
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro ao remover'
  }
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
            <td
              class="px-3 py-2 sticky left-0 bg-background"
              :class="{ 'cursor-pointer hover:bg-accent/30': canEdit && !isEditingCell(row, 'razao_social') }"
              @click="canEdit && !isEditingCell(row, 'razao_social') && startEditCell(row, 'razao_social')"
            >
              <input
                v-if="isEditingCell(row, 'razao_social')"
                v-model="editCellValue"
                type="text"
                class="w-full text-sm bg-transparent outline-none border-b border-blue-500 font-medium"
                :disabled="editCellSaving"
                autofocus
                @blur="commitEditCell(row, 'razao_social')"
                @keydown.enter.prevent="commitEditCell(row, 'razao_social')"
                @keydown.escape.prevent="cancelEditCell"
              />
              <div v-else class="flex items-center gap-1 group">
                <span class="font-medium flex-1 truncate" :title="row.company.razao_social">
                  {{ row.company.razao_social }}
                </span>
                <NuxtLink
                  :to="`/companies/${row.company.id}`"
                  class="opacity-0 group-hover:opacity-100 shrink-0 p-0.5 hover:bg-muted rounded"
                  title="Abrir empresa"
                  @click.stop
                >
                  <ExternalLink class="size-3 text-muted-foreground" />
                </NuxtLink>
              </div>
            </td>
            <td
              class="px-3 py-2"
              :class="{ 'cursor-pointer hover:bg-accent/30': canEdit && !isEditingCell(row, 'uf') }"
              @click="canEdit && !isEditingCell(row, 'uf') && startEditCell(row, 'uf')"
            >
              <input
                v-if="isEditingCell(row, 'uf')"
                v-model="editCellValue"
                type="text"
                maxlength="2"
                class="w-12 text-sm bg-transparent outline-none border-b border-blue-500 uppercase"
                :disabled="editCellSaving"
                autofocus
                @blur="commitEditCell(row, 'uf')"
                @keydown.enter.prevent="commitEditCell(row, 'uf')"
                @keydown.escape.prevent="cancelEditCell"
              />
              <span v-else :class="{ 'text-muted-foreground': !row.company.uf }">{{ row.company.uf || '—' }}</span>
            </td>
            <td
              class="px-3 py-2 font-mono text-xs"
              :class="{ 'cursor-pointer hover:bg-accent/30': canEdit && !isEditingCell(row, 'cnpj') }"
              @click="canEdit && !isEditingCell(row, 'cnpj') && startEditCell(row, 'cnpj')"
            >
              <input
                v-if="isEditingCell(row, 'cnpj')"
                v-model="editCellValue"
                type="text"
                class="w-full text-xs font-mono bg-transparent outline-none border-b border-blue-500"
                :disabled="editCellSaving"
                autofocus
                @blur="commitEditCell(row, 'cnpj')"
                @keydown.enter.prevent="commitEditCell(row, 'cnpj')"
                @keydown.escape.prevent="cancelEditCell"
              />
              <span v-else :class="{ 'text-muted-foreground': !row.company.cnpj }">{{ row.company.cnpj || '—' }}</span>
            </td>
            <td
              class="px-3 py-2"
              :class="{ 'cursor-pointer hover:bg-accent/30': canEdit && !isEditingCell(row, 'inscricao_estadual') }"
              @click="canEdit && !isEditingCell(row, 'inscricao_estadual') && startEditCell(row, 'inscricao_estadual')"
            >
              <input
                v-if="isEditingCell(row, 'inscricao_estadual')"
                v-model="editCellValue"
                type="text"
                class="w-full text-sm bg-transparent outline-none border-b border-blue-500"
                :disabled="editCellSaving"
                autofocus
                @blur="commitEditCell(row, 'inscricao_estadual')"
                @keydown.enter.prevent="commitEditCell(row, 'inscricao_estadual')"
                @keydown.escape.prevent="cancelEditCell"
              />
              <span v-else :class="{ 'text-muted-foreground': !row.company.inscricao_estadual }">
                {{ row.company.inscricao_estadual || '—' }}
              </span>
            </td>
            <td
              class="px-3 py-2"
              :class="{ 'cursor-pointer hover:bg-accent/30': canEdit && !isEditingCell(row, 'apelido') }"
              @click="canEdit && !isEditingCell(row, 'apelido') && startEditCell(row, 'apelido')"
            >
              <input
                v-if="isEditingCell(row, 'apelido')"
                v-model="editCellValue"
                type="text"
                class="w-full text-sm bg-transparent outline-none border-b border-blue-500"
                :disabled="editCellSaving"
                autofocus
                @blur="commitEditCell(row, 'apelido')"
                @keydown.enter.prevent="commitEditCell(row, 'apelido')"
                @keydown.escape.prevent="cancelEditCell"
              />
              <span v-else>{{ row.company.apelido }}</span>
            </td>
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
            <td v-for="mk in MARKETPLACES" :key="mk" class="px-2 py-2 text-center relative">
              <template v-if="row.stores[mk]">
                <button
                  v-if="canEdit"
                  :class="[STORE_STATUS_CLASSES[row.stores[mk]!.status], 'cursor-pointer hover:bg-accent/40 px-1.5 rounded']"
                  :title="`${MARKETPLACE_SHORT[mk]} — clique para opções`"
                  @click.stop="openCellPopover(row.company.id, mk)"
                >{{ STORE_STATUS_LABELS[row.stores[mk]!.status] }}</button>
                <span v-else :class="STORE_STATUS_CLASSES[row.stores[mk]!.status]">
                  {{ STORE_STATUS_LABELS[row.stores[mk]!.status] }}
                </span>
                <div
                  v-if="cellPopoverFor === `${row.company.id}:${mk}`"
                  class="absolute z-30 mt-1 left-1/2 -translate-x-1/2 w-56 rounded-md border bg-popover p-2 shadow-lg text-left text-xs"
                  @click.stop
                >
                  <div class="px-1 pb-2 border-b border-border space-y-0.5">
                    <div class="font-semibold uppercase text-[10px] text-muted-foreground">
                      {{ row.company.apelido }} · {{ MARKETPLACE_SHORT[mk] }}
                    </div>
                    <template v-if="storeInfoFor(row, mk)">
                      <div><span class="text-muted-foreground">Fone:</span> {{ storeInfoFor(row, mk)!.phone || '—' }}</div>
                      <div><span class="text-muted-foreground">Email:</span> {{ storeInfoFor(row, mk)!.email || '—' }}</div>
                      <div><span class="text-muted-foreground">Servidor:</span> {{ storeInfoFor(row, mk)!.server || '—' }}</div>
                    </template>
                    <div v-else class="text-muted-foreground">Sem store_info vinculada.</div>
                  </div>
                  <button
                    v-if="row.stores[mk]?.id"
                    class="w-full text-left mt-1 px-2 py-1 rounded hover:bg-destructive/10 text-destructive"
                    @click="removeStoreCell(row, mk)"
                  >
                    Remover conta
                  </button>
                  <p v-else class="mt-1 text-[10px] text-muted-foreground italic">
                    Detectado via store_info — sem registro em stores. Remova pela aba Lojas.
                  </p>
                </div>
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
            <td
              class="px-3 py-2 max-w-32"
              :class="{ 'cursor-pointer hover:bg-accent/30': canEdit && !isEditingCell(row, 'site_url') }"
              :title="row.company.site_url || ''"
              @click="canEdit && !isEditingCell(row, 'site_url') && startEditCell(row, 'site_url')"
            >
              <input
                v-if="isEditingCell(row, 'site_url')"
                v-model="editCellValue"
                type="text"
                class="w-full text-xs bg-transparent outline-none border-b border-blue-500"
                :disabled="editCellSaving"
                autofocus
                @blur="commitEditCell(row, 'site_url')"
                @keydown.enter.prevent="commitEditCell(row, 'site_url')"
                @keydown.escape.prevent="cancelEditCell"
              />
              <div v-else class="flex items-center gap-1 group">
                <span :class="{ 'text-muted-foreground': !row.company.site_url }" class="flex-1 truncate text-xs">
                  {{ row.company.site_url || '—' }}
                </span>
                <a
                  v-if="row.company.site_url"
                  :href="row.company.site_url"
                  target="_blank"
                  class="opacity-0 group-hover:opacity-100 shrink-0 p-0.5 hover:bg-muted rounded"
                  title="Abrir site"
                  @click.stop
                >
                  <ExternalLink class="size-3 text-muted-foreground" />
                </a>
              </div>
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
            <select
              v-model="newAccountForm.phoneId"
              :disabled="newAccountSaving"
              class="w-full border rounded px-2 py-1 bg-background text-sm"
            >
              <option value="">— selecione um fone disponível —</option>
              <option v-for="c in availablePhones" :key="c.id" :value="c.id">
                {{ c.codigo }}{{ c.label ? ` · ${c.label}` : '' }}
              </option>
            </select>
            <p v-if="!availablePhones.length" class="text-xs text-amber-600 mt-1">
              Sem fones disponíveis para esta plataforma. Cadastre um em /cadastros.
            </p>
          </div>
          <div>
            <Label>E-mail <span class="text-red-500">*</span></Label>
            <select
              v-model="newAccountForm.emailId"
              :disabled="newAccountSaving"
              class="w-full border rounded px-2 py-1 bg-background text-sm"
            >
              <option value="">— selecione um e-mail disponível —</option>
              <option v-for="c in availableEmails" :key="c.id" :value="c.id">
                {{ c.codigo }}{{ c.label ? ` · ${c.label}` : '' }}
              </option>
            </select>
            <p v-if="!availableEmails.length" class="text-xs text-amber-600 mt-1">
              Sem e-mails disponíveis para esta plataforma. Cadastre um em /cadastros.
            </p>
          </div>
          <div>
            <Label>Servidor <span class="text-red-500">*</span></Label>
            <Input v-model="newAccountForm.server" :disabled="newAccountSaving" placeholder="ex: 76" />
          </div>
          <p class="text-xs text-muted-foreground">
            Apenas códigos sem vínculo nesta plataforma aparecem nos selects. Ao criar, o fone e o e-mail
            ficam marcados como "em uso" na tabela Cadastros.
          </p>
        </div>
        <div v-if="newAccountErr" class="text-sm text-red-500">erro: {{ newAccountErr }}</div>
        <div v-if="newAccountResult" class="text-sm rounded border border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 px-3 py-2">
          ✓ {{ newAccountResult }}
        </div>
        <div class="flex justify-end gap-2">
          <Button variant="ghost" :disabled="newAccountSaving" @click="closeNewAccount">cancelar</Button>
          <Button
            :disabled="newAccountSaving || !newAccountForm.phoneId || !newAccountForm.emailId || !newAccountForm.server.trim()"
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
