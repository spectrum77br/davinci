<script setup lang="ts">
import { Plus, RefreshCw, Trash2, X, Check, Loader2, Eye, EyeOff, Copy, ExternalLink, AlertCircle, Unlink, Link2 } from 'lucide-vue-next'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'lojas_info', action: 'view' },
})

type StoreInfo = {
  id: string
  user_id: string
  platform: string
  segment: string | null
  freight: string | null
  cpf_name: string | null
  account_name: string | null
  server: string | null
  cnpj: string | null
  email: string | null
  observation: string | null
  shipping_address: string | null
  return_address: string | null
  phone: string | null
  link: string | null
  integration_id: string | null
  sort_order: number
  has_password: boolean
  departments: string[]
  has_pricing: boolean
  has_integration: boolean
  created_at: string
  updated_at: string
}

const DEPT_BADGE: Record<string, { label: string; cls: string }> = {
  celular:  { label: 'Cel',      cls: 'bg-blue-500/15 text-blue-400 border-blue-500/40' },
  mala:     { label: 'Mala',     cls: 'bg-amber-500/15 text-amber-400 border-amber-500/40' },
  eletro:   { label: 'Eletro',   cls: 'bg-purple-500/15 text-purple-400 border-purple-500/40' },
  catalogo: { label: 'Catálogo', cls: 'bg-muted text-muted-foreground border-border' },
}

type IntegrationRef = {
  id: string
  platform: string
  name: string
  store_id: string | null
}

const STORE_INFO_TO_INTEGRATION_PLATFORM: Record<string, string> = {
  mercadolivre: 'ml',
  ml: 'ml',
  shopee: 'shopee',
  amazon: 'amazon',
  tiktok: 'tiktok',
  temu: 'temu',
}

const PLATFORM_ALIASES: Record<string, string> = {
  ml: 'mercadolivre',
  mercadolivre: 'mercadolivre',
}

function normPlatform(p: string | null | undefined): string {
  if (!p) return ''
  const lower = p.toLowerCase()
  return PLATFORM_ALIASES[lower] ?? lower
}

const PLATFORMS = [
  { value: 'mercadolivre', label: 'ML' },
  { value: 'shopee', label: 'Shopee' },
  { value: 'amazon', label: 'Amazon' },
  { value: 'aliexpress', label: 'AliExpress' },
  { value: 'temu', label: 'Temu' },
  { value: 'tiktok', label: 'TikTok' },
  { value: 'magalu', label: 'Magalu' },
  { value: 'shein', label: 'Shein' },
]

function platformLabel(p: string) {
  return PLATFORMS.find((x) => x.value === normPlatform(p))?.label ?? p
}

const DEPARTMENTS = [
  { value: 'celular', label: 'Celular' },
  { value: 'mala', label: 'Mala' },
  { value: 'eletro', label: 'Eletro' },
  { value: 'catalogo', label: 'Catálogo' },
]

const { api } = useApi()
const canEdit = useCan('tabela_precos', 'edit')
const canDelete = useCan('tabela_precos', 'delete')

const items = ref<StoreInfo[]>([])
const integrations = ref<IntegrationRef[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const filterPlatform = ref<string>('all')
const search = ref('')

async function load() {
  loading.value = true
  error.value = null
  try {
    const [storeInfo, integs] = await Promise.all([
      api<StoreInfo[]>('/api/pricing/store-info'),
      api<IntegrationRef[]>('/api/integrations').catch(() => [] as IntegrationRef[]),
    ])
    items.value = storeInfo
    integrations.value = integs
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    loading.value = false
  }
}

await load()

function integrationFor(row: StoreInfo): IntegrationRef | null {
  if (!row.integration_id) return null
  return integrations.value.find((i) => i.id === row.integration_id) || null
}

function availableIntegrationsFor(row: StoreInfo): IntegrationRef[] {
  const plat = STORE_INFO_TO_INTEGRATION_PLATFORM[normPlatform(row.platform)]
  if (!plat) return []
  return integrations.value.filter((i) => i.platform === plat)
}

async function attachIntegration(row: StoreInfo, integrationId: string) {
  if (!integrationId || integrationId === row.integration_id) return
  try {
    const updated = await api<StoreInfo>(`/api/pricing/store-info/${row.id}`, {
      method: 'PATCH',
      body: { integration_id: integrationId },
    })
    Object.assign(row, updated)
    flash(row.id, 'account_name')
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  }
}

async function unlinkIntegration(row: StoreInfo) {
  try {
    const updated = await api<StoreInfo>(`/api/pricing/store-info/${row.id}`, {
      method: 'PATCH',
      body: { integration_id: null },
    })
    Object.assign(row, updated)
    flash(row.id, 'account_name')
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  }
}

const sorted = computed(() => {
  let list = [...items.value]
  if (filterPlatform.value !== 'all') {
    const want = normPlatform(filterPlatform.value)
    list = list.filter((s) => normPlatform(s.platform) === want)
  }
  const q = search.value.trim().toLowerCase()
  if (q) {
    list = list.filter(
      (s) =>
        (s.account_name || '').toLowerCase().includes(q) ||
        (s.cpf_name || '').toLowerCase().includes(q) ||
        (s.email || '').toLowerCase().includes(q) ||
        (s.cnpj || '').toLowerCase().includes(q) ||
        s.platform.toLowerCase().includes(q),
    )
  }
  return list
})

// Grouped by platform (alphabetical platform order, alphabetical account_name
// within each group). Mirrors the SSH "Lojas" view with platform headers.
const groups = computed(() => {
  const map = new Map<string, StoreInfo[]>()
  for (const r of sorted.value) {
    const key = normPlatform(r.platform)
    if (!map.has(key)) map.set(key, [])
    map.get(key)!.push(r)
  }
  for (const arr of map.values()) {
    arr.sort((a, b) =>
      (a.account_name || '').localeCompare(b.account_name || '', 'pt-BR', { sensitivity: 'base' })
    )
  }
  return Array.from(map.keys())
    .sort()
    .map((platform) => ({
      platform,
      count: map.get(platform)!.length,
      rows: map.get(platform)!,
    }))
})

// =========================================================== inline edit

const editing = ref<{ id: string; field: string } | null>(null)
const editValue = ref<string>('')
const editOriginal = ref<string>('')
const editInputRef = ref<HTMLInputElement | HTMLSelectElement | null>(null)
function setEditInputRef(el: any) {
  if (el) editInputRef.value = el
}
const flashed = ref<Set<string>>(new Set())

function isEditing(id: string, field: string) {
  return editing.value?.id === id && editing.value?.field === field
}

function isFlashed(id: string, field: string) {
  return flashed.value.has(`${id}::${field}`)
}

function flash(id: string, field: string) {
  const k = `${id}::${field}`
  flashed.value.add(k)
  setTimeout(() => flashed.value.delete(k), 1200)
}

async function startEdit(row: StoreInfo, field: string) {
  if (!canEdit.value) return
  editing.value = { id: row.id, field }
  let initial: string
  if (field === 'password') {
    initial = revealedPasswords.value.get(row.id) ?? ''
  } else {
    const raw = (row as any)[field]
    initial = raw == null ? '' : String(raw)
  }
  editValue.value = initial
  editOriginal.value = initial
  await nextTick()
  const el = editInputRef.value
  if (el) {
    el.focus()
    if ('select' in el) (el as HTMLInputElement).select?.()
  }
}

function cancelEdit() {
  editing.value = null
  editValue.value = ''
  editOriginal.value = ''
}

async function commitEdit() {
  if (!editing.value) return
  const { id, field } = editing.value
  const row = items.value.find((x) => x.id === id)
  if (!row) return cancelEdit()

  if (editValue.value === editOriginal.value) {
    return cancelEdit()
  }

  const raw = editValue.value.trim()
  const payload: Record<string, unknown> = {}

  if (field === 'platform') {
    if (!raw) return cancelEdit()
    payload.platform = raw
  } else if (field === 'sort_order') {
    const n = parseInt(raw)
    if (Number.isNaN(n)) return cancelEdit()
    payload.sort_order = n
  } else if (field === 'password') {
    payload.password = raw || null
  } else {
    payload[field] = raw || null
  }

  try {
    const updated = await api<StoreInfo>(`/api/pricing/store-info/${id}`, {
      method: 'PATCH',
      body: payload,
    })
    Object.assign(row, updated)
    flash(id, field)
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    cancelEdit()
  }
}

// =========================================================== add row

const showAdd = ref(false)
const newRow = reactive({ platform: 'mercadolivre', account_name: '' })
const adding = ref(false)

function openAdd() {
  newRow.platform = 'mercadolivre'
  newRow.account_name = ''
  showAdd.value = true
  nextTick(() => {
    const el = document.getElementById('new-store-account') as HTMLInputElement | null
    el?.focus()
  })
}

async function submitNew() {
  if (!newRow.platform) return
  adding.value = true
  try {
    const body: Record<string, unknown> = { platform: newRow.platform }
    if (newRow.account_name) body.account_name = newRow.account_name
    const created = await api<StoreInfo>('/api/pricing/store-info', {
      method: 'POST',
      body,
    })
    items.value.push(created)
    showAdd.value = false
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    adding.value = false
  }
}

// =========================================================== delete

async function remove(row: StoreInfo) {
  if (!confirm(`Excluir loja "${row.account_name || row.platform}"?`)) return
  try {
    await api(`/api/pricing/store-info/${row.id}`, { method: 'DELETE' })
    items.value = items.value.filter((x) => x.id !== row.id)
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  }
}

// =========================================================== bind dept

const TIPO_OPTIONS = [
  { slug: 'celular',  label: 'Celular' },
  { slug: 'mala',     label: 'Mala' },
  { slug: 'eletro',   label: 'Eletro' },
  { slug: 'catalogo', label: 'Catálogo' },
] as const

const tipoPopoverFor = ref<string | null>(null)
const tipoBusy = ref<Set<string>>(new Set())

function openTipoPopover(rowId: string) {
  if (!canEdit.value) return
  tipoPopoverFor.value = rowId === tipoPopoverFor.value ? null : rowId
}

// Close the Tipo popover on any click outside the popover/cell.
const onDocClick = () => { tipoPopoverFor.value = null }
onMounted(() => document.addEventListener('click', onDocClick))
onBeforeUnmount(() => document.removeEventListener('click', onDocClick))

async function toggleDepartment(row: StoreInfo, slug: string, checked: boolean) {
  const key = `${row.id}:${slug}`
  if (tipoBusy.value.has(key)) return
  tipoBusy.value.add(key)
  try {
    if (checked) {
      await api(`/api/pricing/store-info/${row.id}/department`, {
        method: 'POST',
        body: { department: slug },
      })
    } else {
      await api(
        `/api/pricing/store-info/${row.id}/department/${encodeURIComponent(slug)}`,
        { method: 'DELETE' },
      )
    }
    flash(row.id, 'department')
    await load()
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    tipoBusy.value.delete(key)
  }
}

// =========================================================== password reveal

const revealed = ref<Set<string>>(new Set())
const revealedPasswords = ref<Map<string, string>>(new Map())
async function toggleReveal(id: string) {
  if (revealed.value.has(id)) {
    revealed.value.delete(id)
    revealedPasswords.value.delete(id)
    return
  }
  try {
    const r = await api<{ password: string }>(`/api/pricing/store-info/${id}/password`)
    revealedPasswords.value.set(id, r.password)
    revealed.value.add(id)
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  }
}

async function copyText(text: string) {
  await navigator.clipboard.writeText(text)
}
</script>

<template>
  <div class="space-y-4">
    <PageHeader
      title="Lojas"
      description="Cadastros completos das lojas — clique em qualquer célula para editar"
    >
      <template #actions>
        <select v-model="filterPlatform" class="border rounded px-2 py-1 text-sm bg-background">
          <option value="all">Todas plataformas</option>
          <option v-for="p in PLATFORMS" :key="p.value" :value="p.value">{{ p.label }}</option>
        </select>
        <input
          v-model="search"
          placeholder="buscar conta, CPF, e-mail, CNPJ…"
          class="border rounded px-2 py-1 text-sm bg-background w-64"
        />
        <Button size="sm" variant="ghost" :disabled="loading" @click="load">
          <RefreshCw class="size-4 mr-1" :class="{ 'animate-spin': loading }" /> recarregar
        </Button>
        <Button v-if="canEdit" size="sm" :disabled="showAdd" @click="openAdd">
          <Plus class="size-4 mr-1" /> Nova loja
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
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[110px]">Plataforma</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[120px]">Conta</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[80px]">Frete</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[140px]">Responsável</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[80px]">Servidor</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[140px]">CNPJ</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[180px]">E-mail</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[120px]">Fone</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[120px]">Senha</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border w-32">End. Envio</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border w-32">End. Dev.</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[160px]">Obs</th>
            <th class="text-center px-2 py-2 font-medium border-b border-border min-w-[110px]">Tipo</th>
            <th class="text-center px-2 py-2 font-medium border-b border-border w-20">Tab. Preço</th>
            <th class="text-center px-2 py-2 font-medium border-b border-border w-20">Integração</th>
            <th class="text-center px-2 py-2 font-medium border-b border-border w-12"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading && !items.length">
            <td colSpan="16" class="text-center py-6 text-muted-foreground">
              <Loader2 class="inline h-4 w-4 animate-spin" /> carregando…
            </td>
          </tr>
          <tr v-else-if="!sorted.length && !showAdd">
            <td colSpan="16" class="text-center py-8 text-muted-foreground">Nenhuma loja cadastrada.</td>
          </tr>

          <!-- add row -->
          <tr v-if="showAdd" class="bg-blue-50/40 dark:bg-blue-900/10">
            <td class="border border-border px-1 py-1">
              <select v-model="newRow.platform" class="w-full text-xs border rounded px-1 py-1 bg-background">
                <option v-for="p in PLATFORMS" :key="p.value" :value="p.value">{{ p.label }}</option>
              </select>
            </td>
            <td class="border border-border px-1 py-1">
              <input
                id="new-store-account"
                v-model="newRow.account_name"
                type="text" placeholder="Nome da conta"
                class="w-full text-xs border rounded px-1.5 py-1 bg-background"
                @keydown.enter="submitNew"
                @keydown.escape="showAdd = false"
              />
            </td>
            <td v-for="i in 13" :key="i" class="border border-border text-center text-xs text-muted-foreground">—</td>
            <td class="border border-border px-1 py-1 text-center">
              <div class="flex gap-0.5 justify-center">
                <button class="p-1 text-emerald-600 hover:bg-emerald-50 rounded" :disabled="adding" @click="submitNew">
                  <Loader2 v-if="adding" class="h-3 w-3 animate-spin" />
                  <Check v-else class="h-3 w-3" />
                </button>
                <button class="p-1 text-destructive hover:bg-destructive/10 rounded" @click="showAdd = false">
                  <X class="h-3 w-3" />
                </button>
              </div>
            </td>
          </tr>

          <!-- platform groups: header + rows -->
          <template v-for="group in groups" :key="group.platform">
            <tr class="bg-muted/60">
              <td colSpan="16" class="px-3 py-2 text-xs font-bold uppercase tracking-wide text-foreground/80 border-b border-border">
                {{ group.platform }}
                <span class="font-normal text-muted-foreground normal-case">
                  {{ group.count }} conta{{ group.count > 1 ? 's' : '' }}
                </span>
              </td>
            </tr>
          <tr v-for="row in group.rows" :key="row.id" class="hover:bg-accent/30">
            <!-- platform -->
            <td
              class="border border-border px-2 py-1.5 text-xs cursor-pointer"
              :class="{
                'ring-2 ring-blue-500 ring-inset bg-background': isEditing(row.id, 'platform'),
                'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(row.id, 'platform'),
              }"
              @click="!isEditing(row.id, 'platform') && startEdit(row, 'platform')"
            >
              <select
                v-if="isEditing(row.id, 'platform')"
                :ref="setEditInputRef"
                v-model="editValue"
                class="w-full text-xs bg-transparent outline-none"
                @blur="commitEdit" @change="commitEdit" @keydown.escape.prevent="cancelEdit"
              >
                <option v-for="p in PLATFORMS" :key="p.value" :value="p.value">{{ p.label }}</option>
              </select>
              <span v-else>{{ platformLabel(row.platform) }}</span>
            </td>
            <!-- account_name + integration link -->
            <td
              class="border border-border px-2 py-1.5 text-xs"
              :class="{
                'ring-2 ring-blue-500 ring-inset bg-background': isEditing(row.id, 'account_name'),
                'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(row.id, 'account_name'),
              }"
            >
              <input
                v-if="isEditing(row.id, 'account_name')"
                :ref="setEditInputRef"
                v-model="editValue" type="text"
                class="w-full text-xs bg-transparent outline-none"
                @blur="commitEdit" @keydown.enter.prevent="commitEdit" @keydown.escape.prevent="cancelEdit"
              />
              <div v-else class="flex items-center gap-1 group min-w-0">
                <span
                  class="cursor-pointer truncate flex-1"
                  :class="{ 'text-muted-foreground': !row.account_name }"
                  :title="row.account_name || ''"
                  @click="startEdit(row, 'account_name')"
                >
                  {{ row.account_name || '—' }}
                </span>
                <span
                  v-if="integrationFor(row)"
                  class="shrink-0 inline-flex items-center gap-0.5 px-1 py-0.5 rounded border bg-muted text-muted-foreground text-[10px] max-w-[100px]"
                  :title="`integração: ${integrationFor(row)!.name}`"
                >
                  <Link2 class="h-2.5 w-2.5 shrink-0" />
                  <span class="truncate">{{ integrationFor(row)!.name }}</span>
                </span>
                <button
                  v-if="row.integration_id && canEdit"
                  class="opacity-0 group-hover:opacity-100 p-0.5 hover:bg-muted rounded shrink-0"
                  title="Desvincular integração"
                  @click.stop="unlinkIntegration(row)"
                >
                  <Unlink class="h-3 w-3" />
                </button>
              </div>
            </td>
            <!-- text fields -->
            <template
              v-for="f in [
                'freight', 'cpf_name', 'server', 'cnpj', 'email', 'phone',
              ]"
              :key="f"
            >
              <td
                class="border border-border px-2 py-1.5 text-xs cursor-pointer"
                :class="{
                  'ring-2 ring-blue-500 ring-inset bg-background': isEditing(row.id, f),
                  'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(row.id, f),
                  'font-mono': f === 'cnpj' || f === 'phone',
                }"
                @click="!isEditing(row.id, f) && startEdit(row, f)"
              >
                <input
                  v-if="isEditing(row.id, f)"
                  :ref="setEditInputRef"
                  v-model="editValue" type="text"
                  class="w-full text-xs bg-transparent outline-none"
                  :class="{ 'font-mono': f === 'cnpj' || f === 'phone' }"
                  @blur="commitEdit" @keydown.enter.prevent="commitEdit" @keydown.escape.prevent="cancelEdit"
                />
                <span v-else :class="{ 'text-muted-foreground': !((row as any)[f]) }">
                  {{ (row as any)[f] || '—' }}
                </span>
              </td>
            </template>
            <!-- password -->
            <td class="border border-border px-2 py-1.5 text-xs">
              <div class="flex items-center gap-1 group">
                <span
                  class="cursor-pointer truncate flex-1 font-mono"
                  :class="{
                    'ring-2 ring-blue-500 ring-inset bg-background': isEditing(row.id, 'password'),
                    'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(row.id, 'password'),
                    'text-muted-foreground': !row.has_password,
                  }"
                  @click="!isEditing(row.id, 'password') && startEdit(row, 'password')"
                >
                  <input
                    v-if="isEditing(row.id, 'password')"
                    :ref="setEditInputRef"
                    v-model="editValue" type="text"
                    class="w-full text-xs bg-transparent outline-none font-mono"
                    @blur="commitEdit" @keydown.enter.prevent="commitEdit" @keydown.escape.prevent="cancelEdit"
                  />
                  <template v-else>
                    {{ row.has_password ? (revealed.has(row.id) ? (revealedPasswords.get(row.id) || '••••') : '••••••••') : '—' }}
                  </template>
                </span>
                <button
                  v-if="row.has_password && !isEditing(row.id, 'password')"
                  class="opacity-0 group-hover:opacity-100 transition-opacity p-0.5 hover:bg-muted rounded"
                  :title="revealed.has(row.id) ? 'Ocultar' : 'Mostrar senha'"
                  @click="toggleReveal(row.id)"
                >
                  <EyeOff v-if="revealed.has(row.id)" class="h-3 w-3" />
                  <Eye v-else class="h-3 w-3" />
                </button>
              </div>
            </td>
            <!-- shipping_address (compact: truncate + tooltip) -->
            <td
              class="border border-border px-2 py-1.5 text-xs cursor-pointer max-w-[140px]"
              :class="{
                'ring-2 ring-blue-500 ring-inset bg-background': isEditing(row.id, 'shipping_address'),
                'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(row.id, 'shipping_address'),
              }"
              :title="row.shipping_address || ''"
              @click="!isEditing(row.id, 'shipping_address') && startEdit(row, 'shipping_address')"
            >
              <input
                v-if="isEditing(row.id, 'shipping_address')"
                :ref="setEditInputRef"
                v-model="editValue" type="text"
                class="w-full text-xs bg-transparent outline-none"
                @blur="commitEdit" @keydown.enter.prevent="commitEdit" @keydown.escape.prevent="cancelEdit"
              />
              <span v-else class="block truncate" :class="{ 'text-muted-foreground': !row.shipping_address }">
                {{ row.shipping_address || '—' }}
              </span>
            </td>
            <!-- return_address (compact: truncate + tooltip) -->
            <td
              class="border border-border px-2 py-1.5 text-xs cursor-pointer max-w-[140px]"
              :class="{
                'ring-2 ring-blue-500 ring-inset bg-background': isEditing(row.id, 'return_address'),
                'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(row.id, 'return_address'),
              }"
              :title="row.return_address || ''"
              @click="!isEditing(row.id, 'return_address') && startEdit(row, 'return_address')"
            >
              <input
                v-if="isEditing(row.id, 'return_address')"
                :ref="setEditInputRef"
                v-model="editValue" type="text"
                class="w-full text-xs bg-transparent outline-none"
                @blur="commitEdit" @keydown.enter.prevent="commitEdit" @keydown.escape.prevent="cancelEdit"
              />
              <span v-else class="block truncate" :class="{ 'text-muted-foreground': !row.return_address }">
                {{ row.return_address || '—' }}
              </span>
            </td>
            <!-- observation -->
            <td
              class="border border-border px-2 py-1.5 text-xs cursor-pointer"
              :class="{
                'ring-2 ring-blue-500 ring-inset bg-background': isEditing(row.id, 'observation'),
                'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(row.id, 'observation'),
              }"
              @click="!isEditing(row.id, 'observation') && startEdit(row, 'observation')"
            >
              <input
                v-if="isEditing(row.id, 'observation')"
                :ref="setEditInputRef"
                v-model="editValue" type="text"
                class="w-full text-xs bg-transparent outline-none"
                @blur="commitEdit" @keydown.enter.prevent="commitEdit" @keydown.escape.prevent="cancelEdit"
              />
              <span v-else :class="{ 'text-muted-foreground': !row.observation }">
                {{ row.observation || '—' }}
              </span>
            </td>
            <!-- Tipo (badges from linked pricing_accounts; catálogo excluded;
                 click to open the bind/unbind popover) -->
            <td
              class="border border-border px-1 py-1 text-center relative"
              :class="{ 'ring-2 ring-blue-500 ring-inset bg-background': tipoPopoverFor === row.id }"
              :title="canEdit ? 'Vincular departamentos' : ''"
              @click.stop="openTipoPopover(row.id)"
            >
              <div
                v-if="row.departments.some((d) => DEPT_BADGE[d])"
                class="flex flex-wrap gap-0.5 justify-center cursor-pointer"
              >
                <span
                  v-for="d in row.departments.filter((x) => DEPT_BADGE[x])"
                  :key="d"
                  class="px-1.5 py-0.5 rounded border text-[10px] font-semibold"
                  :class="DEPT_BADGE[d].cls"
                >
                  {{ DEPT_BADGE[d].label }}
                </span>
              </div>
              <span v-else class="text-muted-foreground cursor-pointer text-xs">—</span>
              <!-- popover -->
              <div
                v-if="tipoPopoverFor === row.id"
                class="absolute z-20 mt-1 left-1/2 -translate-x-1/2 w-36 rounded-md border bg-popover p-2 shadow-lg text-left"
                @click.stop
              >
                <label
                  v-for="opt in TIPO_OPTIONS"
                  :key="opt.slug"
                  class="flex items-center gap-2 py-1 cursor-pointer text-xs hover:bg-accent/50 px-1 rounded"
                >
                  <input
                    type="checkbox"
                    :checked="row.departments.includes(opt.slug)"
                    :disabled="tipoBusy.has(`${row.id}:${opt.slug}`)"
                    @change="(e) => toggleDepartment(row, opt.slug, (e.target as HTMLInputElement).checked)"
                  />
                  <span>{{ opt.label }}</span>
                </label>
                <button
                  class="mt-1 w-full text-center text-[10px] text-muted-foreground hover:text-foreground py-0.5"
                  @click="tipoPopoverFor = null"
                >
                  fechar
                </button>
              </div>
            </td>
            <!-- Tab. Preço -->
            <td class="border border-border px-1 py-1 text-center">
              <span
                class="inline-block px-1.5 py-0.5 rounded border text-[10px] font-semibold"
                :class="row.has_pricing
                  ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/40'
                  : 'bg-red-500/15 text-red-400 border-red-500/40'"
              >
                {{ row.has_pricing ? 'Sim' : '✕ Não' }}
              </span>
            </td>
            <!-- Integração -->
            <td class="border border-border px-1 py-1 text-center">
              <span
                class="inline-block px-1.5 py-0.5 rounded border text-[10px] font-semibold"
                :class="row.has_integration
                  ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/40'
                  : 'bg-red-500/15 text-red-400 border-red-500/40'"
              >
                {{ row.has_integration ? 'Sim' : '✕ Não' }}
              </span>
            </td>
            <!-- delete -->
            <td class="border border-border px-1 py-1 text-center">
              <button
                v-if="canDelete"
                class="p-1 text-destructive hover:bg-destructive/10 rounded"
                :title="`Excluir ${row.account_name || row.platform}`"
                @click="remove(row)"
              >
                <Trash2 class="h-3 w-3" />
              </button>
            </td>
          </tr>
          </template>
        </tbody>
      </table>
    </div>
  </div>
</template>
