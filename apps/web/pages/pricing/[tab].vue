<script setup lang="ts">
import {
  Plus, Trash2, RefreshCw, Save, X, AlertCircle, Loader2, Eye, EyeOff,
  Star, Send, Lock, Ban, Check, Link2, Copy,
  Smartphone, Briefcase, Zap, BarChart3, DollarSign, Settings2, Upload,
} from 'lucide-vue-next'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'tabela_precos', action: 'view' },
})

type Tab = 'tabela' | 'contas' | 'produtos' | 'auditoria' | 'concorrencia'

const TABS: { key: Tab; label: string; icon: any }[] = [
  { key: 'tabela', label: 'Tabela de Preços', icon: DollarSign },
  { key: 'contas', label: 'Contas', icon: Settings2 },
  { key: 'produtos', label: 'Produtos', icon: Upload },
  { key: 'auditoria', label: 'Auditoria', icon: AlertCircle },
  { key: 'concorrencia', label: 'Concorrência', icon: BarChart3 },
]

const route = useRoute()
const router = useRouter()
const { api } = useApi()

const tab = computed<Tab>(() => {
  const t = route.params.tab as string
  return (TABS.find((x) => x.key === t)?.key ?? 'contas') as Tab
})

function setTab(t: Tab) {
  router.push(`/pricing/${t}`)
}

const canEditContas = useCan('tabela_precos_contas', 'edit')
const canDeleteContas = useCan('tabela_precos_contas', 'delete')
const canEditProdutos = useCan('tabela_precos_produtos', 'edit')
const canDeleteProdutos = useCan('tabela_precos_produtos', 'delete')

const DEPARTMENTS = [
  { value: 'celular', label: 'Celular', icon: Smartphone },
  { value: 'mala', label: 'Mala', icon: Briefcase },
  { value: 'eletro', label: 'Eletro', icon: Zap },
  { value: 'catalogo', label: 'Catálogo ML', icon: BarChart3 },
] as const

type DeptKey = typeof DEPARTMENTS[number]['value']

const TYPE_HEADERS: Record<DeptKey, string[]> = {
  celular: ['Acessórios', 'Reg.Diversos', 'Reg.Uranyx', 'Robusto', 'Apple'],
  catalogo: ['Acessórios', 'Reg.Diversos', 'Reg.Uranyx', 'Robusto', 'Apple'],
  eletro: ['Acessórios', 'Reg.Diversos', 'Reg.Uranyx', 'Robusto', 'Apple'],
  mala: ['8"/Acess.', '12"', '18"/20"', '24"+', 'Queima'],
}

const PLATFORMS = [
  { value: 'mercadolivre', label: 'ML' },
  { value: 'shopee', label: 'Shopee' },
  { value: 'amazon', label: 'Amazon' },
  { value: 'temu', label: 'Temu' },
  { value: 'aliexpress', label: 'AliExpress' },
  { value: 'tiktok', label: 'TikTok' },
  { value: 'magalu', label: 'Magalu' },
] as const

function platformLabel(p: string) {
  return PLATFORMS.find((x) => x.value === p)?.label ?? p
}

const department = ref<DeptKey>('celular')

// =========================================================== accounts state

type Account = {
  id: string
  user_id: string
  name: string
  platform: string
  listing_type: string | null
  department: string
  kit_number: number
  commission: string | number | null
  margin1: string | number | null
  shipping1: string | number | null
  margin2: string | number | null
  shipping2: string | number | null
  margin3: string | number | null
  shipping3: string | number | null
  margin4: string | number | null
  shipping4: string | number | null
  margin5: string | number | null
  shipping5: string | number | null
  email: string | null
  phone: string | null
  observation: string | null
  observation2: string | null
  observation3: string | null
  has_password: boolean
  integration_id: string | null
  sort_order: number
  created_at: string
  updated_at: string
}

const accounts = ref<Account[]>([])
const accountsLoading = ref(false)
const accountsErr = ref<string | null>(null)

async function loadAccounts() {
  accountsLoading.value = true
  accountsErr.value = null
  try {
    accounts.value = await api<Account[]>('/api/pricing/accounts')
  } catch (e: any) {
    accountsErr.value = e?.data?.detail?.code ?? 'load_failed'
  } finally {
    accountsLoading.value = false
  }
}

const accountsByDept = computed(() => {
  const m: Record<DeptKey, Account[]> = { celular: [], mala: [], eletro: [], catalogo: [] }
  for (const a of accounts.value) {
    const k = a.department as DeptKey
    if (m[k]) m[k].push(a)
  }
  for (const k of Object.keys(m) as DeptKey[]) {
    m[k].sort((a, b) => a.sort_order - b.sort_order || a.name.localeCompare(b.name))
  }
  return m
})

const accountsCurrent = computed(() => accountsByDept.value[department.value] ?? [])

// =========================================================== products state

type PricingProduct = {
  id: string
  user_id: string
  product_id: string | null
  sku: string
  name: string
  department: string
  product_type: number
  bling_cost_price: string | number | null
  cost_kit1: string | number
  cost_kit2: string | number | null
  cost_kit3: string | number | null
  cost_kit4: string | number | null
  description: string | null
  model: string | null
  ean: string | null
  is_active: boolean
  in_catalog: boolean
  created_at: string
  updated_at: string
}

const products = ref<PricingProduct[]>([])
const productsLoading = ref(false)
const productsErr = ref<string | null>(null)
const searchProdutos = ref('')

async function loadProducts() {
  productsLoading.value = true
  productsErr.value = null
  try {
    products.value = await api<PricingProduct[]>('/api/pricing/products')
  } catch (e: any) {
    productsErr.value = e?.data?.detail?.code ?? 'load_failed'
  } finally {
    productsLoading.value = false
  }
}

const productsByDept = computed(() => {
  const m: Record<DeptKey, PricingProduct[]> = { celular: [], mala: [], eletro: [], catalogo: [] }
  for (const p of products.value) {
    const k = p.department as DeptKey
    if (m[k]) m[k].push(p)
  }
  for (const k of Object.keys(m) as DeptKey[]) {
    m[k].sort((a, b) => a.sku.localeCompare(b.sku))
  }
  return m
})

const productsCurrent = computed(() => {
  const list = productsByDept.value[department.value] ?? []
  const q = searchProdutos.value.trim().toLowerCase()
  if (!q) return list
  return list.filter(
    (p) =>
      p.sku.toLowerCase().includes(q) ||
      p.name.toLowerCase().includes(q) ||
      (p.ean ?? '').toLowerCase().includes(q),
  )
})

// =========================================================== inline-edit infra

type EditKey = { id: string; field: string }
const editing = ref<EditKey | null>(null)
const editValue = ref<string>('')
const editInputRef = ref<HTMLInputElement | HTMLSelectElement | null>(null)
function setEditInputRef(el: any) {
  if (el) editInputRef.value = el
}
const savedFlash = ref<Set<string>>(new Set())

function flash(id: string, field: string) {
  const k = `${id}::${field}`
  savedFlash.value.add(k)
  setTimeout(() => savedFlash.value.delete(k), 1200)
}

function isEditing(id: string, field: string) {
  return editing.value?.id === id && editing.value?.field === field
}

function isFlashed(id: string, field: string) {
  return savedFlash.value.has(`${id}::${field}`)
}

function cancelEdit() {
  editing.value = null
  editValue.value = ''
}

async function focusEditInput() {
  await nextTick()
  const el = editInputRef.value
  if (!el) return
  el.focus()
  if ('select' in el) (el as HTMLInputElement).select?.()
}

// =========================================================== account inline edit

function startEditAccount(acc: Account, field: string) {
  if (!canEditContas.value) return
  editing.value = { id: acc.id, field }
  const raw = (acc as any)[field]
  if (field === 'commission' || field.startsWith('margin')) {
    const n = raw == null || raw === '' ? null : Number(raw)
    editValue.value = n == null || Number.isNaN(n) ? '' : (n * 100).toFixed(2).replace(/\.?0+$/, '')
  } else if (field.startsWith('shipping')) {
    editValue.value = raw == null || raw === '' ? '' : String(raw)
  } else if (field === 'kit_number') {
    editValue.value = String(raw ?? 1)
  } else {
    editValue.value = raw == null ? '' : String(raw)
  }
  focusEditInput()
}

async function commitEditAccount() {
  if (!editing.value) return
  const { id, field } = editing.value
  const acc = accounts.value.find((x) => x.id === id)
  if (!acc) return cancelEdit()

  const raw = editValue.value.trim()
  const payload: Record<string, unknown> = {}

  if (field === 'name') {
    if (!raw) return cancelEdit()
    payload.name = raw
  } else if (field === 'platform') {
    payload.platform = raw
  } else if (field === 'kit_number') {
    const n = parseInt(raw)
    if (!Number.isFinite(n) || n < 1 || n > 5) return cancelEdit()
    payload.kit_number = n
  } else if (field === 'commission') {
    if (!raw) {
      payload.commission = null
    } else {
      const pct = Number(raw)
      if (Number.isNaN(pct)) return cancelEdit()
      payload.commission = (pct / 100).toFixed(4)
    }
  } else if (field.startsWith('margin')) {
    if (!raw || raw === '-' || raw === '—') {
      payload[field] = null
    } else {
      const pct = Number(raw)
      if (Number.isNaN(pct)) return cancelEdit()
      payload[field] = (pct / 100).toFixed(4)
    }
  } else if (field.startsWith('shipping')) {
    if (!raw || raw === '-' || raw === '—') {
      payload[field] = null
    } else {
      const n = Number(raw)
      if (Number.isNaN(n)) return cancelEdit()
      payload[field] = n
    }
  } else if (field.startsWith('observation') || field === 'email' || field === 'phone' || field === 'listing_type') {
    payload[field] = raw || null
  } else {
    return cancelEdit()
  }

  try {
    const updated = await api<Account>(`/api/pricing/accounts/${id}`, {
      method: 'PATCH',
      body: payload,
    })
    Object.assign(acc, updated)
    flash(id, field)
  } catch (e: any) {
    accountsErr.value = e?.data?.detail?.code ?? 'save_failed'
  } finally {
    cancelEdit()
  }
}

async function deleteAccount(a: Account) {
  if (!confirm(`Excluir conta "${a.name}"?`)) return
  try {
    await api(`/api/pricing/accounts/${a.id}`, { method: 'DELETE' })
    accounts.value = accounts.value.filter((x) => x.id !== a.id)
  } catch (e: any) {
    accountsErr.value = e?.data?.detail?.code ?? 'delete_failed'
  }
}

async function autoMatchAccounts() {
  try {
    await api('/api/pricing/accounts/auto-match', { method: 'POST' })
    await loadAccounts()
  } catch (e: any) {
    accountsErr.value = e?.data?.detail?.code ?? 'auto_match_failed'
  }
}

// =========================================================== add account row

const showAddAcc = ref(false)
const newAcc = reactive({ name: '', platform: 'mercadolivre', kit_number: 1, commission: '' })
const addingAcc = ref(false)

function openAddAcc() {
  newAcc.name = ''
  newAcc.platform = 'mercadolivre'
  newAcc.kit_number = 1
  newAcc.commission = ''
  showAddAcc.value = true
  nextTick(() => {
    const el = document.getElementById('new-acc-name') as HTMLInputElement | null
    el?.focus()
  })
}

async function submitNewAcc() {
  if (!newAcc.name.trim()) return
  addingAcc.value = true
  try {
    const body: Record<string, unknown> = {
      name: newAcc.name.trim(),
      platform: newAcc.platform,
      department: department.value,
      kit_number: newAcc.kit_number || 1,
    }
    if (newAcc.commission) body.commission = (Number(newAcc.commission) / 100).toFixed(4)
    const created = await api<Account>('/api/pricing/accounts', { method: 'POST', body })
    accounts.value.push(created)
    showAddAcc.value = false
  } catch (e: any) {
    accountsErr.value = e?.data?.detail?.code ?? 'create_failed'
  } finally {
    addingAcc.value = false
  }
}

// =========================================================== product inline edit

function startEditProduct(p: PricingProduct, field: string) {
  if (!canEditProdutos.value) return
  editing.value = { id: p.id, field }
  const raw = (p as any)[field]
  editValue.value = raw == null ? '' : String(raw)
  focusEditInput()
}

async function commitEditProduct() {
  if (!editing.value) return
  const { id, field } = editing.value
  const p = products.value.find((x) => x.id === id)
  if (!p) return cancelEdit()

  const raw = editValue.value.trim()
  const payload: Record<string, unknown> = {}

  if (field === 'sku' || field === 'name') {
    if (!raw) return cancelEdit()
    payload[field] = raw
  } else if (field === 'department') {
    payload.department = raw
  } else if (field === 'product_type') {
    const n = parseInt(raw)
    if (!Number.isFinite(n)) return cancelEdit()
    payload.product_type = n
  } else if (field.startsWith('cost_kit')) {
    if (!raw || raw === '-' || raw === '—') {
      if (field === 'cost_kit1') payload[field] = '0'
      else payload[field] = null
    } else {
      const n = Number(raw)
      if (Number.isNaN(n)) return cancelEdit()
      payload[field] = n.toFixed(2)
    }
  } else if (field === 'description' || field === 'model' || field === 'ean') {
    payload[field] = raw || null
  } else {
    return cancelEdit()
  }

  try {
    const updated = await api<PricingProduct>(`/api/pricing/products/${id}`, {
      method: 'PATCH',
      body: payload,
    })
    Object.assign(p, updated)
    flash(id, field)
  } catch (e: any) {
    productsErr.value = e?.data?.detail?.code ?? 'save_failed'
  } finally {
    cancelEdit()
  }
}

async function toggleCatalog(p: PricingProduct) {
  try {
    const updated = await api<PricingProduct>(
      `/api/pricing/products/${p.id}/catalog`,
      { method: 'POST' },
    )
    Object.assign(p, updated)
  } catch (e: any) {
    productsErr.value = e?.data?.detail?.code ?? 'toggle_failed'
  }
}

async function toggleActive(p: PricingProduct) {
  try {
    const updated = await api<PricingProduct>(`/api/pricing/products/${p.id}`, {
      method: 'PATCH',
      body: { is_active: !p.is_active },
    })
    Object.assign(p, updated)
  } catch (e: any) {
    productsErr.value = e?.data?.detail?.code ?? 'toggle_failed'
  }
}

async function deleteProduct(p: PricingProduct) {
  if (!confirm(`Excluir produto "${p.sku}"?`)) return
  try {
    await api(`/api/pricing/products/${p.id}`, { method: 'DELETE' })
    products.value = products.value.filter((x) => x.id !== p.id)
  } catch (e: any) {
    productsErr.value = e?.data?.detail?.code ?? 'delete_failed'
  }
}

// =========================================================== add product row

const showAddProd = ref(false)
const newProd = reactive({
  sku: '',
  name: '',
  product_type: 2,
  cost_kit1: '0',
  cost_kit2: '',
  cost_kit3: '',
  cost_kit4: '',
  ean: '',
  description: '',
  model: '',
})
const addingProd = ref(false)

function openAddProd() {
  newProd.sku = ''
  newProd.name = ''
  newProd.product_type = 2
  newProd.cost_kit1 = '0'
  newProd.cost_kit2 = ''
  newProd.cost_kit3 = ''
  newProd.cost_kit4 = ''
  newProd.ean = ''
  newProd.description = ''
  newProd.model = ''
  showAddProd.value = true
  nextTick(() => {
    const el = document.getElementById('new-prod-sku') as HTMLInputElement | null
    el?.focus()
  })
}

async function submitNewProd() {
  if (!newProd.sku.trim() || !newProd.name.trim()) return
  addingProd.value = true
  try {
    const body: Record<string, unknown> = {
      sku: newProd.sku.trim(),
      name: newProd.name.trim(),
      department: department.value,
      product_type: newProd.product_type || 2,
      cost_kit1: Number(newProd.cost_kit1 || 0).toFixed(2),
    }
    if (newProd.cost_kit2) body.cost_kit2 = Number(newProd.cost_kit2).toFixed(2)
    if (newProd.cost_kit3) body.cost_kit3 = Number(newProd.cost_kit3).toFixed(2)
    if (newProd.cost_kit4) body.cost_kit4 = Number(newProd.cost_kit4).toFixed(2)
    if (newProd.ean) body.ean = newProd.ean
    if (newProd.description) body.description = newProd.description
    if (newProd.model) body.model = newProd.model
    const created = await api<PricingProduct>('/api/pricing/products', { method: 'POST', body })
    products.value.push(created)
    showAddProd.value = false
  } catch (e: any) {
    productsErr.value = e?.data?.detail?.code ?? 'create_failed'
  } finally {
    addingProd.value = false
  }
}

// =========================================================== overrides / grid

type GridCell = {
  pricing_account_id: string
  pricing_product_id: string
  price: string | number | null
  source: string
  cell_status: 'auto' | 'manual' | 'locked' | 'disabled'
  has_override: boolean
}
type GridResponse = {
  accounts: Account[]
  products: PricingProduct[]
  cells: GridCell[]
}
type PushResult = {
  pricing_account_id: string
  pricing_product_id: string
  ok: boolean
  code: string
  detail: string | null
  price: string | null
  item_id: string | null
  variation_id: string | null
  cached: boolean
}

const grid = ref<GridResponse | null>(null)
const gridLoading = ref(false)
const gridErr = ref<string | null>(null)
const editingCell = ref<{ accId: string; prodId: string } | null>(null)
const cellEditValue = ref<string>('')
const pushing = ref(false)
const lastPushResults = ref<PushResult[]>([])

async function loadGrid() {
  gridLoading.value = true
  gridErr.value = null
  try {
    const qs = department.value ? `?department=${department.value}` : ''
    grid.value = await api<GridResponse>(`/api/pricing/grid${qs}`)
  } catch (e: any) {
    gridErr.value = e?.data?.detail?.code ?? 'load_failed'
  } finally {
    gridLoading.value = false
  }
}

const cellMap = computed(() => {
  const m = new Map<string, GridCell>()
  for (const c of grid.value?.cells ?? []) {
    m.set(`${c.pricing_product_id}::${c.pricing_account_id}`, c)
  }
  return m
})

function cellOf(prodId: string, accId: string): GridCell | undefined {
  return cellMap.value.get(`${prodId}::${accId}`)
}

function cellLabel(c: GridCell | undefined): string {
  if (!c) return '—'
  if (c.source === 'disabled') return '∅'
  if (c.price == null) return '—'
  return Number(c.price).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
}

function cellTone(c: GridCell | undefined): string {
  if (!c) return ''
  if (c.source === 'disabled') return 'bg-muted/50 text-muted-foreground'
  if (c.source === 'locked') return 'bg-amber-50 text-amber-900 dark:bg-amber-900/20 dark:text-amber-100'
  if (c.source === 'override') return 'bg-blue-50 text-blue-900 dark:bg-blue-900/20 dark:text-blue-100'
  if (c.source === 'missing_inputs') return 'bg-red-50 text-red-900 dark:bg-red-900/20 dark:text-red-100'
  return ''
}

function startCellEdit(c: GridCell) {
  editingCell.value = { accId: c.pricing_account_id, prodId: c.pricing_product_id }
  cellEditValue.value = c.price != null ? String(c.price) : ''
}

function cancelCellEdit() {
  editingCell.value = null
  cellEditValue.value = ''
}

async function saveOverride() {
  if (!editingCell.value) return
  const { accId, prodId } = editingCell.value
  const val = cellEditValue.value.trim()
  try {
    if (val === '') {
      await api(
        `/api/pricing/overrides?pricing_product_id=${prodId}&pricing_account_id=${accId}`,
        { method: 'DELETE' },
      ).catch((e) => {
        if (e?.response?.status !== 404) throw e
      })
    } else {
      await api('/api/pricing/overrides', {
        method: 'PUT',
        body: {
          pricing_product_id: prodId,
          pricing_account_id: accId,
          price_override: Number(val),
          cell_status: 'manual',
        },
      })
    }
    cancelCellEdit()
    await loadGrid()
  } catch (e: any) {
    gridErr.value = e?.data?.detail?.code ?? 'save_failed'
  }
}

async function setCellStatus(c: GridCell, status: 'auto' | 'locked' | 'disabled') {
  try {
    await api('/api/pricing/overrides/cell-status', {
      method: 'PUT',
      body: {
        pricing_product_id: c.pricing_product_id,
        pricing_account_id: c.pricing_account_id,
        cell_status: status,
      },
    })
    await loadGrid()
  } catch (e: any) {
    gridErr.value = e?.data?.detail?.code ?? 'status_failed'
  }
}

async function pushCell(c: GridCell) {
  pushing.value = true
  lastPushResults.value = []
  try {
    const r = await api<{ results: PushResult[] }>('/api/pricing/push', {
      method: 'POST',
      headers: { 'Idempotency-Key': `cell:${c.pricing_product_id}:${c.pricing_account_id}:${Date.now()}` },
      body: {
        items: [{ pricing_account_id: c.pricing_account_id, pricing_product_id: c.pricing_product_id }],
      },
    })
    lastPushResults.value = r.results
  } catch (e: any) {
    gridErr.value = e?.data?.detail?.code ?? 'push_failed'
  } finally {
    pushing.value = false
  }
}

type BatchJob = {
  id: string
  type: string
  status: 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled'
  total: number
  processed: number
  result?: { summary?: { total: number; ok: number; failed: number; cached: number } }
  error?: string | null
}
const activeJob = ref<BatchJob | null>(null)
const BATCH_THRESHOLD = 5

async function pushItemsBatch(
  items: { pricing_account_id: string; pricing_product_id: string }[],
  keyHint: string,
) {
  if (!items.length) {
    gridErr.value = 'no_eligible_cells'
    return
  }
  if (items.length <= BATCH_THRESHOLD) {
    pushing.value = true
    lastPushResults.value = []
    try {
      const r = await api<{ results: PushResult[] }>('/api/pricing/push', {
        method: 'POST',
        headers: { 'Idempotency-Key': `${keyHint}:${Date.now()}` },
        body: { items },
      })
      lastPushResults.value = r.results
    } catch (e: any) {
      gridErr.value = e?.data?.detail?.code ?? 'push_failed'
    } finally {
      pushing.value = false
    }
    return
  }
  pushing.value = true
  activeJob.value = null
  try {
    const created = await api<{ job_id: string }>('/api/pricing/push-batch', {
      method: 'POST',
      headers: { 'Idempotency-Key': `${keyHint}:${Date.now()}` },
      body: { items },
    })
    await pollJob(created.job_id)
  } catch (e: any) {
    gridErr.value = e?.data?.detail?.code ?? 'push_failed'
  } finally {
    pushing.value = false
  }
}

async function pollJob(jobId: string, attempts = 0): Promise<void> {
  try {
    const job = await api<BatchJob>(`/api/jobs/${jobId}`)
    activeJob.value = job
    if (job.status === 'succeeded' || job.status === 'failed' || job.status === 'cancelled') {
      await loadGrid()
      return
    }
  } catch (e: any) {
    if (attempts > 60) {
      gridErr.value = 'job_poll_timeout'
      return
    }
  }
  await new Promise((r) => setTimeout(r, 1500))
  return pollJob(jobId, attempts + 1)
}

async function pushAccountColumn(accId: string) {
  if (!grid.value) return
  if (!confirm('Disparar push para todos os produtos desta conta?')) return
  const items = grid.value.products
    .map((p) => ({ pricing_account_id: accId, pricing_product_id: p.id }))
    .filter((it) => {
      const c = cellOf(it.pricing_product_id, it.pricing_account_id)
      return c && c.price != null && c.source !== 'locked' && c.source !== 'disabled'
    })
  await pushItemsBatch(items, `col:${accId}`)
}

async function pushAllVisible() {
  if (!grid.value) return
  const items: { pricing_account_id: string; pricing_product_id: string }[] = []
  for (const p of grid.value.products) {
    for (const a of grid.value.accounts) {
      const c = cellOf(p.id, a.id)
      if (c && c.price != null && c.source !== 'locked' && c.source !== 'disabled') {
        items.push({ pricing_account_id: a.id, pricing_product_id: p.id })
      }
    }
  }
  if (!confirm(`Push ${items.length} célula(s)?`)) return
  await pushItemsBatch(items, 'all-visible')
}

async function sendManualReport() {
  const text = prompt(
    'Texto do relatório (HTML do Telegram):',
    activeJob.value
      ? `Push job ${activeJob.value.id}\nstatus: ${activeJob.value.status}\nok: ${activeJob.value.result?.summary?.ok ?? 0} / ${activeJob.value.total}`
      : 'Sem dados',
  )
  if (!text) return
  try {
    await api('/api/pricing/push-report', { method: 'POST', body: { summary: text } })
    alert('Enviado.')
  } catch (e: any) {
    gridErr.value = e?.data?.detail?.code ?? 'report_failed'
  }
}

// =========================================================== audit

type AuditRow = {
  sku: string
  listing_count: number
  platforms: string[]
  integration_ids: string[]
  sample_titles: string[]
  dismissed: boolean
}

const auditRows = ref<AuditRow[]>([])
const auditLoading = ref(false)
const auditErr = ref<string | null>(null)
const auditIncludeDismissed = ref(false)

async function loadAudit() {
  auditLoading.value = true
  auditErr.value = null
  try {
    const qs = auditIncludeDismissed.value ? '?include_dismissed=true' : ''
    auditRows.value = await api<AuditRow[]>(`/api/pricing/sku-audit${qs}`)
  } catch (e: any) {
    auditErr.value = e?.data?.detail?.code ?? 'load_failed'
  } finally {
    auditLoading.value = false
  }
}

async function dismissSku(sku: string) {
  try {
    await api(`/api/pricing/sku-audit/${encodeURIComponent(sku)}/dismiss`, { method: 'POST' })
    await loadAudit()
  } catch (e: any) {
    auditErr.value = e?.data?.detail?.code ?? 'dismiss_failed'
  }
}

async function undismissSku(sku: string) {
  try {
    await api(`/api/pricing/sku-audit/${encodeURIComponent(sku)}/undismiss`, { method: 'POST' })
    await loadAudit()
  } catch (e: any) {
    auditErr.value = e?.data?.detail?.code ?? 'undismiss_failed'
  }
}

async function syncBlingCosts() {
  if (!confirm('Disparar sync de custos Bling para todos os produtos?')) return
  try {
    const r = await api<{ job_id: string }>('/api/pricing/jobs/sync-bling-costs', { method: 'POST' })
    activeJob.value = null
    await pollJob(r.job_id)
    alert('Sync concluído.')
  } catch (e: any) {
    auditErr.value = e?.data?.detail?.code ?? 'sync_failed'
  }
}

// =========================================================== competitor

type CompetitorRow = {
  item_id: string
  title: string
  price: number
  currency: string
  permalink: string
  seller_id: number | null
  condition: string | null
  sold_quantity: number | null
  available_quantity: number | null
  thumbnail: string | null
}

const competitorQuery = ref('')
const competitorRows = ref<CompetitorRow[]>([])
const competitorLoading = ref(false)
const competitorErr = ref<string | null>(null)

async function searchCompetitor() {
  const q = competitorQuery.value.trim()
  if (!q) {
    competitorRows.value = []
    return
  }
  competitorLoading.value = true
  competitorErr.value = null
  try {
    const params = new URLSearchParams({ q, limit: '20' })
    competitorRows.value = await api<CompetitorRow[]>(`/api/pricing/competitor-prices?${params}`)
  } catch (e: any) {
    competitorErr.value = e?.data?.detail?.code ?? 'search_failed'
  } finally {
    competitorLoading.value = false
  }
}

// =========================================================== display helpers

function fmtCommission(v: string | number | null) {
  if (v == null || v === '') return '—'
  const n = Number(v)
  if (Number.isNaN(n)) return '—'
  return `${(n * 100).toFixed(0)}%`
}

function fmtMargin(v: string | number | null) {
  if (v == null || v === '') return '—'
  const n = Number(v)
  if (Number.isNaN(n)) return '—'
  return `${(n * 100).toFixed(1)}%`
}

function fmtShipping(v: string | number | null) {
  if (v == null || v === '') return '—'
  const n = Number(v)
  if (Number.isNaN(n)) return '—'
  return n.toFixed(0)
}

function fmtMoney(v: string | number | null) {
  if (v == null || v === '') return '—'
  const n = Number(v)
  if (Number.isNaN(n)) return '—'
  return n.toFixed(2)
}

// =========================================================== boot

await loadAccounts()
await loadProducts()

watch(
  tab,
  async (t) => {
    if (t === 'tabela') await loadGrid()
    else if (t === 'auditoria') await loadAudit()
  },
  { immediate: true },
)

watch(department, async () => {
  if (tab.value === 'tabela') await loadGrid()
})
</script>

<template>
  <div class="space-y-4">
    <PageHeader
      title="Tabela de Preços"
      description="Gerencie custos, margens e preços de venda por conta"
    />

    <!-- ============================================ Department pills -->
    <div class="flex flex-wrap gap-2">
      <button
        v-for="d in DEPARTMENTS"
        :key="d.value"
        type="button"
        class="inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm transition-colors"
        :class="department === d.value
          ? 'bg-primary text-primary-foreground border-primary'
          : 'border-border hover:bg-muted'"
        @click="department = d.value"
      >
        <component :is="d.icon" class="h-4 w-4" />
        {{ d.label }} ({{ accountsByDept[d.value].length }} contas)
      </button>
    </div>

    <!-- ============================================ Sub-tabs -->
    <div class="flex flex-wrap gap-1 rounded-md bg-muted/40 p-1 w-fit">
      <button
        v-for="t in TABS"
        :key="t.key"
        type="button"
        class="inline-flex items-center gap-1.5 rounded px-3 py-1.5 text-sm transition-colors"
        :class="tab === t.key
          ? 'bg-background shadow-sm'
          : 'text-muted-foreground hover:text-foreground'"
        @click="setTab(t.key)"
      >
        <component :is="t.icon" class="h-4 w-4" />
        {{ t.label }}
        <span v-if="t.key === 'contas'" class="text-xs text-muted-foreground">
          ({{ accountsCurrent.length }})
        </span>
        <span v-else-if="t.key === 'produtos'" class="text-xs text-muted-foreground">
          ({{ productsByDept[department].length }})
        </span>
      </button>
    </div>

    <!-- ============================================ CONTAS -->
    <section v-if="tab === 'contas'" class="space-y-3">
      <div class="flex items-center justify-between">
        <div>
          <h3 class="text-base font-semibold">
            Contas de Venda — {{ DEPARTMENTS.find(d => d.value === department)?.label }}
          </h3>
          <p class="text-xs text-muted-foreground">
            {{ accountsCurrent.length }} conta(s) com 5 pares margem/frete por tipo — clique para editar
          </p>
        </div>
        <div class="flex gap-2">
          <button v-if="canEditContas" class="btn btn-sm" @click="autoMatchAccounts">
            <Link2 class="h-4 w-4 mr-1" /> Vincular Integrações
          </button>
          <button v-if="canEditContas" class="btn btn-sm btn-primary" :disabled="showAddAcc" @click="openAddAcc">
            <Plus class="h-4 w-4 mr-1" /> Adicionar Conta
          </button>
        </div>
      </div>

      <div v-if="accountsErr" class="rounded border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive flex items-center gap-2">
        <AlertCircle class="h-4 w-4" /> {{ accountsErr }}
      </div>

      <div class="border rounded-lg overflow-auto max-h-[calc(100vh-320px)]">
        <table class="w-full text-sm border-collapse">
          <thead class="sticky top-0 bg-muted z-10">
            <tr>
              <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[120px]">Nome</th>
              <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[100px]">Plataforma</th>
              <th class="text-center px-2 py-2 font-medium border-b border-border w-12">Kit</th>
              <th class="text-center px-2 py-2 font-medium border-b border-border w-20">Comissão</th>
              <th
                v-for="(label, i) in TYPE_HEADERS[department]"
                :key="i"
                colSpan="2"
                class="text-center px-1 py-2 font-medium border-b border-border"
              >
                <div class="text-[11px]">{{ label }}</div>
                <div class="text-[10px] text-muted-foreground flex justify-center gap-3">
                  <span>marg</span><span>frete</span>
                </div>
              </th>
              <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[140px]">Obs 1</th>
              <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[140px]">Obs 2</th>
              <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[140px]">Obs 3</th>
              <th class="text-center px-2 py-2 font-medium border-b border-border w-16"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="accountsLoading && !accounts.length">
              <td colSpan="14" class="text-center py-6 text-muted-foreground">
                <Loader2 class="inline h-4 w-4 animate-spin" /> carregando…
              </td>
            </tr>
            <tr v-else-if="!accountsCurrent.length && !showAddAcc">
              <td colSpan="14" class="text-center py-6 text-muted-foreground">
                Nenhuma conta neste departamento.
              </td>
            </tr>

            <!-- add row -->
            <tr v-if="showAddAcc" class="bg-blue-50/40 dark:bg-blue-900/10">
              <td class="border border-border px-1 py-1">
                <input
                  id="new-acc-name"
                  v-model="newAcc.name"
                  type="text"
                  placeholder="Nome"
                  class="w-full text-xs border rounded px-1.5 py-1 bg-background"
                  @keydown.enter="submitNewAcc"
                  @keydown.escape="showAddAcc = false"
                />
              </td>
              <td class="border border-border px-1 py-1">
                <select v-model="newAcc.platform" class="w-full text-xs border rounded px-1 py-1 bg-background">
                  <option v-for="p in PLATFORMS" :key="p.value" :value="p.value">{{ p.label }}</option>
                </select>
              </td>
              <td class="border border-border px-1 py-1">
                <input
                  v-model.number="newAcc.kit_number"
                  type="number" min="1" max="5"
                  class="w-full text-xs border rounded px-1 py-1 bg-background text-center"
                />
              </td>
              <td class="border border-border px-1 py-1">
                <input
                  v-model="newAcc.commission"
                  type="number" step="0.1"
                  placeholder="11"
                  class="w-full text-xs border rounded px-1 py-1 bg-background text-center"
                  @keydown.enter="submitNewAcc"
                />
              </td>
              <td v-for="i in 10" :key="i" class="border border-border text-center text-xs text-muted-foreground">—</td>
              <td v-for="i in 3" :key="`obs-${i}`" class="border border-border text-center text-xs text-muted-foreground">—</td>
              <td class="border border-border px-1 py-1 text-center">
                <div class="flex gap-0.5 justify-center">
                  <button class="p-1 text-emerald-600 hover:bg-emerald-50 rounded" :disabled="addingAcc" @click="submitNewAcc">
                    <Loader2 v-if="addingAcc" class="h-3 w-3 animate-spin" />
                    <Check v-else class="h-3 w-3" />
                  </button>
                  <button class="p-1 text-destructive hover:bg-destructive/10 rounded" @click="showAddAcc = false">
                    <X class="h-3 w-3" />
                  </button>
                </div>
              </td>
            </tr>

            <!-- data rows -->
            <tr v-for="acc in accountsCurrent" :key="acc.id" class="hover:bg-accent/30">
              <!-- name -->
              <td
                class="border border-border px-2 py-1.5 text-xs cursor-pointer text-left"
                :class="{
                  'ring-2 ring-blue-500 ring-inset bg-background': isEditing(acc.id, 'name'),
                  'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(acc.id, 'name'),
                }"
                @click="!isEditing(acc.id, 'name') && startEditAccount(acc, 'name')"
              >
                <input
                  v-if="isEditing(acc.id, 'name')"
                  :ref="setEditInputRef"
                  v-model="editValue"
                  type="text"
                  class="w-full text-xs bg-transparent outline-none"
                  @blur="commitEditAccount"
                  @keydown.enter.prevent="commitEditAccount"
                  @keydown.escape.prevent="cancelEdit"
                />
                <span v-else class="font-medium">{{ acc.name }}<Check v-if="isFlashed(acc.id, 'name')" class="inline h-3 w-3 ml-1 text-emerald-600" /></span>
              </td>
              <!-- platform -->
              <td
                class="border border-border px-2 py-1.5 text-xs cursor-pointer text-center"
                :class="{
                  'ring-2 ring-blue-500 ring-inset bg-background': isEditing(acc.id, 'platform'),
                  'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(acc.id, 'platform'),
                }"
                @click="!isEditing(acc.id, 'platform') && startEditAccount(acc, 'platform')"
              >
                <select
                  v-if="isEditing(acc.id, 'platform')"
                  :ref="setEditInputRef"
                  v-model="editValue"
                  class="w-full text-xs bg-transparent outline-none"
                  @blur="commitEditAccount"
                  @change="commitEditAccount"
                  @keydown.escape.prevent="cancelEdit"
                >
                  <option v-for="p in PLATFORMS" :key="p.value" :value="p.value">{{ p.label }}</option>
                </select>
                <span v-else>{{ platformLabel(acc.platform) }}</span>
              </td>
              <!-- kit -->
              <td
                class="border border-border px-2 py-1.5 text-xs cursor-pointer text-center"
                :class="{ 'ring-2 ring-blue-500 ring-inset bg-background': isEditing(acc.id, 'kit_number') }"
                @click="!isEditing(acc.id, 'kit_number') && startEditAccount(acc, 'kit_number')"
              >
                <input
                  v-if="isEditing(acc.id, 'kit_number')"
                  :ref="setEditInputRef"
                  v-model="editValue"
                  type="number" min="1" max="5"
                  class="w-full text-xs bg-transparent outline-none text-center"
                  @blur="commitEditAccount"
                  @keydown.enter.prevent="commitEditAccount"
                  @keydown.escape.prevent="cancelEdit"
                />
                <span v-else>{{ acc.kit_number }}</span>
              </td>
              <!-- commission -->
              <td
                class="border border-border px-2 py-1.5 text-xs cursor-pointer text-center"
                :class="{
                  'ring-2 ring-blue-500 ring-inset bg-background': isEditing(acc.id, 'commission'),
                  'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(acc.id, 'commission'),
                }"
                @click="!isEditing(acc.id, 'commission') && startEditAccount(acc, 'commission')"
              >
                <input
                  v-if="isEditing(acc.id, 'commission')"
                  :ref="setEditInputRef"
                  v-model="editValue"
                  type="number" step="0.1"
                  class="w-full text-xs bg-transparent outline-none text-center"
                  @blur="commitEditAccount"
                  @keydown.enter.prevent="commitEditAccount"
                  @keydown.escape.prevent="cancelEdit"
                />
                <span v-else>{{ fmtCommission(acc.commission) }}</span>
              </td>
              <!-- 5 pairs marg/frete -->
              <template v-for="t in 5" :key="t">
                <td
                  class="border border-border px-2 py-1.5 text-xs cursor-pointer text-center"
                  :class="{
                    'ring-2 ring-blue-500 ring-inset bg-background': isEditing(acc.id, `margin${t}`),
                    'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(acc.id, `margin${t}`),
                  }"
                  @click="!isEditing(acc.id, `margin${t}`) && startEditAccount(acc, `margin${t}`)"
                >
                  <input
                    v-if="isEditing(acc.id, `margin${t}`)"
                    :ref="setEditInputRef"
                    v-model="editValue"
                    type="number" step="0.1"
                    class="w-full text-xs bg-transparent outline-none text-center"
                    @blur="commitEditAccount"
                    @keydown.enter.prevent="commitEditAccount"
                    @keydown.escape.prevent="cancelEdit"
                  />
                  <span v-else>{{ fmtMargin((acc as any)[`margin${t}`]) }}</span>
                </td>
                <td
                  class="border border-border px-2 py-1.5 text-xs cursor-pointer text-center"
                  :class="{
                    'ring-2 ring-blue-500 ring-inset bg-background': isEditing(acc.id, `shipping${t}`),
                    'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(acc.id, `shipping${t}`),
                  }"
                  @click="!isEditing(acc.id, `shipping${t}`) && startEditAccount(acc, `shipping${t}`)"
                >
                  <input
                    v-if="isEditing(acc.id, `shipping${t}`)"
                    :ref="setEditInputRef"
                    v-model="editValue"
                    type="number" step="1"
                    class="w-full text-xs bg-transparent outline-none text-center"
                    @blur="commitEditAccount"
                    @keydown.enter.prevent="commitEditAccount"
                    @keydown.escape.prevent="cancelEdit"
                  />
                  <span v-else>{{ fmtShipping((acc as any)[`shipping${t}`]) }}</span>
                </td>
              </template>
              <!-- obs 1/2/3 -->
              <template v-for="i in 3" :key="`obs${i}`">
                <td
                  class="border border-border px-2 py-1.5 text-xs cursor-pointer text-left max-w-[260px]"
                  :class="{
                    'ring-2 ring-blue-500 ring-inset bg-background': isEditing(acc.id, i === 1 ? 'observation' : `observation${i}`),
                    'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(acc.id, i === 1 ? 'observation' : `observation${i}`),
                  }"
                  @click="!isEditing(acc.id, i === 1 ? 'observation' : `observation${i}`) && startEditAccount(acc, i === 1 ? 'observation' : `observation${i}`)"
                >
                  <input
                    v-if="isEditing(acc.id, i === 1 ? 'observation' : `observation${i}`)"
                    :ref="setEditInputRef"
                    v-model="editValue"
                    type="text"
                    class="w-full text-xs bg-transparent outline-none"
                    @blur="commitEditAccount"
                    @keydown.enter.prevent="commitEditAccount"
                    @keydown.escape.prevent="cancelEdit"
                  />
                  <span v-else :class="{ 'text-muted-foreground': !((acc as any)[i === 1 ? 'observation' : `observation${i}`]) }">
                    {{ (acc as any)[i === 1 ? 'observation' : `observation${i}`] || '—' }}
                  </span>
                </td>
              </template>
              <!-- actions -->
              <td class="border border-border px-1 py-1 text-center">
                <button
                  v-if="canDeleteContas"
                  class="p-1 text-destructive hover:bg-destructive/10 rounded"
                  :title="`Excluir ${acc.name}`"
                  @click="deleteAccount(acc)"
                >
                  <Trash2 class="h-3 w-3" />
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- ============================================ PRODUTOS -->
    <section v-else-if="tab === 'produtos'" class="space-y-3">
      <div class="flex items-center justify-between">
        <div>
          <h3 class="text-base font-semibold">
            Produtos — {{ DEPARTMENTS.find(d => d.value === department)?.label }}
          </h3>
          <p class="text-xs text-muted-foreground">
            {{ productsCurrent.length }} produto(s) — clique para editar
          </p>
        </div>
        <div class="flex gap-2">
          <input
            v-model="searchProdutos"
            placeholder="buscar SKU, nome, EAN…"
            class="border rounded px-2 py-1 text-sm bg-background w-56"
          />
          <button v-if="canEditProdutos" class="btn btn-sm btn-primary" :disabled="showAddProd" @click="openAddProd">
            <Plus class="h-4 w-4 mr-1" /> Adicionar Produto
          </button>
        </div>
      </div>

      <div v-if="productsErr" class="rounded border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive flex items-center gap-2">
        <AlertCircle class="h-4 w-4" /> {{ productsErr }}
      </div>

      <div class="border rounded-lg overflow-auto max-h-[calc(100vh-320px)]">
        <table class="w-full text-sm border-collapse">
          <thead class="sticky top-0 bg-muted z-10">
            <tr>
              <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[120px]">SKU</th>
              <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[200px]">Nome</th>
              <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[100px]">EAN</th>
              <template v-if="department === 'mala'">
                <th class="text-left px-2 py-2 font-medium border-b border-border">Descrição</th>
                <th class="text-left px-2 py-2 font-medium border-b border-border">Modelo</th>
              </template>
              <th class="text-right px-2 py-2 font-medium border-b border-border w-20">Custo Bling</th>
              <th class="text-right px-2 py-2 font-medium border-b border-border w-20">Kit 1</th>
              <th class="text-right px-2 py-2 font-medium border-b border-border w-20">Kit 2</th>
              <th class="text-right px-2 py-2 font-medium border-b border-border w-20">Kit 3</th>
              <th class="text-right px-2 py-2 font-medium border-b border-border w-20">Kit 4</th>
              <th class="text-center px-2 py-2 font-medium border-b border-border w-16">Tipo</th>
              <th class="text-center px-2 py-2 font-medium border-b border-border w-16">Catálogo</th>
              <th class="text-center px-2 py-2 font-medium border-b border-border w-12">Ativo</th>
              <th class="text-center px-2 py-2 font-medium border-b border-border w-12"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="productsLoading && !products.length">
              <td colSpan="14" class="text-center py-6 text-muted-foreground">
                <Loader2 class="inline h-4 w-4 animate-spin" /> carregando…
              </td>
            </tr>
            <tr v-else-if="!productsCurrent.length && !showAddProd">
              <td colSpan="14" class="text-center py-6 text-muted-foreground">
                Nenhum produto neste departamento.
              </td>
            </tr>

            <!-- add row -->
            <tr v-if="showAddProd" class="bg-blue-50/40 dark:bg-blue-900/10">
              <td class="border border-border px-1 py-1">
                <input
                  id="new-prod-sku"
                  v-model="newProd.sku"
                  type="text" placeholder="SKU"
                  class="w-full text-xs border rounded px-1.5 py-1 bg-background font-mono"
                  @keydown.enter="submitNewProd"
                  @keydown.escape="showAddProd = false"
                />
              </td>
              <td class="border border-border px-1 py-1">
                <input
                  v-model="newProd.name"
                  type="text" placeholder="Nome"
                  class="w-full text-xs border rounded px-1.5 py-1 bg-background"
                  @keydown.enter="submitNewProd"
                />
              </td>
              <td class="border border-border px-1 py-1">
                <input
                  v-model="newProd.ean"
                  type="text" placeholder="EAN"
                  class="w-full text-xs border rounded px-1.5 py-1 bg-background font-mono"
                />
              </td>
              <template v-if="department === 'mala'">
                <td class="border border-border px-1 py-1">
                  <input v-model="newProd.description" type="text" placeholder="Descrição" class="w-full text-xs border rounded px-1.5 py-1 bg-background" />
                </td>
                <td class="border border-border px-1 py-1">
                  <input v-model="newProd.model" type="text" placeholder="Modelo" class="w-full text-xs border rounded px-1.5 py-1 bg-background" />
                </td>
              </template>
              <td class="border border-border px-1 py-1 text-center text-xs text-muted-foreground">—</td>
              <td class="border border-border px-1 py-1">
                <input v-model="newProd.cost_kit1" type="number" step="0.01" placeholder="0.00" class="w-full text-xs border rounded px-1.5 py-1 bg-background text-right" />
              </td>
              <td class="border border-border px-1 py-1">
                <input v-model="newProd.cost_kit2" type="number" step="0.01" class="w-full text-xs border rounded px-1.5 py-1 bg-background text-right" />
              </td>
              <td class="border border-border px-1 py-1">
                <input v-model="newProd.cost_kit3" type="number" step="0.01" class="w-full text-xs border rounded px-1.5 py-1 bg-background text-right" />
              </td>
              <td class="border border-border px-1 py-1">
                <input v-model="newProd.cost_kit4" type="number" step="0.01" class="w-full text-xs border rounded px-1.5 py-1 bg-background text-right" />
              </td>
              <td class="border border-border px-1 py-1">
                <input v-model.number="newProd.product_type" type="number" min="1" max="9" class="w-full text-xs border rounded px-1.5 py-1 bg-background text-center" />
              </td>
              <td class="border border-border px-1 py-1 text-center text-xs text-muted-foreground">—</td>
              <td class="border border-border px-1 py-1 text-center text-xs text-muted-foreground">—</td>
              <td class="border border-border px-1 py-1 text-center">
                <div class="flex gap-0.5 justify-center">
                  <button class="p-1 text-emerald-600 hover:bg-emerald-50 rounded" :disabled="addingProd" @click="submitNewProd">
                    <Loader2 v-if="addingProd" class="h-3 w-3 animate-spin" />
                    <Check v-else class="h-3 w-3" />
                  </button>
                  <button class="p-1 text-destructive hover:bg-destructive/10 rounded" @click="showAddProd = false">
                    <X class="h-3 w-3" />
                  </button>
                </div>
              </td>
            </tr>

            <!-- data rows -->
            <tr v-for="p in productsCurrent" :key="p.id" class="hover:bg-accent/30" :class="{ 'opacity-50': !p.is_active }">
              <td
                class="border border-border px-2 py-1.5 text-xs font-mono cursor-pointer"
                :class="{
                  'ring-2 ring-blue-500 ring-inset bg-background': isEditing(p.id, 'sku'),
                  'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(p.id, 'sku'),
                }"
                @click="!isEditing(p.id, 'sku') && startEditProduct(p, 'sku')"
              >
                <input
                  v-if="isEditing(p.id, 'sku')"
                  :ref="setEditInputRef"
                  v-model="editValue" type="text"
                  class="w-full text-xs bg-transparent outline-none font-mono"
                  @blur="commitEditProduct" @keydown.enter.prevent="commitEditProduct" @keydown.escape.prevent="cancelEdit"
                />
                <span v-else>{{ p.sku }}</span>
              </td>
              <td
                class="border border-border px-2 py-1.5 text-xs cursor-pointer"
                :class="{ 'ring-2 ring-blue-500 ring-inset bg-background': isEditing(p.id, 'name'), 'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(p.id, 'name') }"
                @click="!isEditing(p.id, 'name') && startEditProduct(p, 'name')"
              >
                <input
                  v-if="isEditing(p.id, 'name')"
                  :ref="setEditInputRef"
                  v-model="editValue" type="text"
                  class="w-full text-xs bg-transparent outline-none"
                  @blur="commitEditProduct" @keydown.enter.prevent="commitEditProduct" @keydown.escape.prevent="cancelEdit"
                />
                <span v-else>{{ p.name }}</span>
              </td>
              <td
                class="border border-border px-2 py-1.5 text-xs font-mono cursor-pointer"
                :class="{ 'ring-2 ring-blue-500 ring-inset bg-background': isEditing(p.id, 'ean'), 'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(p.id, 'ean') }"
                @click="!isEditing(p.id, 'ean') && startEditProduct(p, 'ean')"
              >
                <input
                  v-if="isEditing(p.id, 'ean')"
                  :ref="setEditInputRef"
                  v-model="editValue" type="text"
                  class="w-full text-xs bg-transparent outline-none font-mono"
                  @blur="commitEditProduct" @keydown.enter.prevent="commitEditProduct" @keydown.escape.prevent="cancelEdit"
                />
                <span v-else>{{ p.ean || '—' }}</span>
              </td>
              <template v-if="department === 'mala'">
                <td
                  class="border border-border px-2 py-1.5 text-xs cursor-pointer"
                  :class="{ 'ring-2 ring-blue-500 ring-inset bg-background': isEditing(p.id, 'description'), 'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(p.id, 'description') }"
                  @click="!isEditing(p.id, 'description') && startEditProduct(p, 'description')"
                >
                  <input
                    v-if="isEditing(p.id, 'description')"
                    :ref="setEditInputRef"
                    v-model="editValue" type="text"
                    class="w-full text-xs bg-transparent outline-none"
                    @blur="commitEditProduct" @keydown.enter.prevent="commitEditProduct" @keydown.escape.prevent="cancelEdit"
                  />
                  <span v-else>{{ p.description || '—' }}</span>
                </td>
                <td
                  class="border border-border px-2 py-1.5 text-xs cursor-pointer"
                  :class="{ 'ring-2 ring-blue-500 ring-inset bg-background': isEditing(p.id, 'model'), 'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(p.id, 'model') }"
                  @click="!isEditing(p.id, 'model') && startEditProduct(p, 'model')"
                >
                  <input
                    v-if="isEditing(p.id, 'model')"
                    :ref="setEditInputRef"
                    v-model="editValue" type="text"
                    class="w-full text-xs bg-transparent outline-none"
                    @blur="commitEditProduct" @keydown.enter.prevent="commitEditProduct" @keydown.escape.prevent="cancelEdit"
                  />
                  <span v-else>{{ p.model || '—' }}</span>
                </td>
              </template>
              <td class="border border-border px-2 py-1.5 text-xs text-right text-muted-foreground">
                {{ fmtMoney(p.bling_cost_price) }}
              </td>
              <td
                v-for="k in 4" :key="`kit-${k}`"
                class="border border-border px-2 py-1.5 text-xs text-right cursor-pointer"
                :class="{
                  'ring-2 ring-blue-500 ring-inset bg-background': isEditing(p.id, `cost_kit${k}`),
                  'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(p.id, `cost_kit${k}`),
                }"
                @click="!isEditing(p.id, `cost_kit${k}`) && startEditProduct(p, `cost_kit${k}`)"
              >
                <input
                  v-if="isEditing(p.id, `cost_kit${k}`)"
                  :ref="setEditInputRef"
                  v-model="editValue" type="number" step="0.01"
                  class="w-full text-xs bg-transparent outline-none text-right"
                  @blur="commitEditProduct" @keydown.enter.prevent="commitEditProduct" @keydown.escape.prevent="cancelEdit"
                />
                <span v-else>{{ fmtMoney((p as any)[`cost_kit${k}`]) }}</span>
              </td>
              <td
                class="border border-border px-2 py-1.5 text-xs text-center cursor-pointer"
                :class="{ 'ring-2 ring-blue-500 ring-inset bg-background': isEditing(p.id, 'product_type'), 'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(p.id, 'product_type') }"
                @click="!isEditing(p.id, 'product_type') && startEditProduct(p, 'product_type')"
              >
                <input
                  v-if="isEditing(p.id, 'product_type')"
                  :ref="setEditInputRef"
                  v-model="editValue" type="number" min="1" max="9"
                  class="w-full text-xs bg-transparent outline-none text-center"
                  @blur="commitEditProduct" @keydown.enter.prevent="commitEditProduct" @keydown.escape.prevent="cancelEdit"
                />
                <span v-else>{{ p.product_type }}</span>
              </td>
              <td class="border border-border px-1 py-1 text-center">
                <button
                  v-if="canEditProdutos"
                  class="p-1 rounded"
                  :class="p.in_catalog ? 'text-amber-600' : 'text-muted-foreground hover:text-foreground'"
                  :title="p.in_catalog ? 'Catálogo ON' : 'Catálogo OFF'"
                  @click="toggleCatalog(p)"
                >
                  <Star class="h-3.5 w-3.5" :fill="p.in_catalog ? 'currentColor' : 'none'" />
                </button>
                <span v-else>{{ p.in_catalog ? 'sim' : '—' }}</span>
              </td>
              <td class="border border-border px-1 py-1 text-center">
                <button
                  v-if="canEditProdutos"
                  class="p-1 rounded"
                  :title="p.is_active ? 'Ativo' : 'Inativo'"
                  @click="toggleActive(p)"
                >
                  <Eye v-if="p.is_active" class="h-3.5 w-3.5 text-emerald-600" />
                  <EyeOff v-else class="h-3.5 w-3.5 text-muted-foreground" />
                </button>
              </td>
              <td class="border border-border px-1 py-1 text-center">
                <button
                  v-if="canDeleteProdutos"
                  class="p-1 text-destructive hover:bg-destructive/10 rounded"
                  :title="`Excluir ${p.sku}`"
                  @click="deleteProduct(p)"
                >
                  <Trash2 class="h-3 w-3" />
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- ============================================ TABELA DE PREÇOS (grid) -->
    <section v-else-if="tab === 'tabela'" class="space-y-3">
      <div class="flex flex-wrap items-center gap-2">
        <button class="btn btn-sm" :disabled="gridLoading" @click="loadGrid">
          <RefreshCw class="h-4 w-4 mr-1" :class="{ 'animate-spin': gridLoading }" />
          Recarregar
        </button>
        <button class="btn btn-sm" :disabled="pushing || !grid?.cells.length" @click="pushAllVisible">
          <Send class="h-4 w-4 mr-1" /> Push tudo
        </button>
        <button class="btn btn-sm" :disabled="pushing" @click="sendManualReport">
          Enviar relatório
        </button>
        <span v-if="pushing" class="text-sm text-muted-foreground flex items-center gap-1">
          <Loader2 class="h-3 w-3 animate-spin" /> enviando…
        </span>
      </div>

      <div v-if="activeJob" class="rounded border bg-muted/40 px-3 py-2 text-sm">
        <div class="flex items-center justify-between">
          <span class="font-medium">Job #{{ activeJob.id.slice(0, 8) }}</span>
          <span :class="{
            'text-amber-700': activeJob.status === 'running' || activeJob.status === 'pending',
            'text-emerald-700': activeJob.status === 'succeeded',
            'text-red-700': activeJob.status === 'failed' || activeJob.status === 'cancelled',
          }">{{ activeJob.status }}</span>
        </div>
        <div class="mt-1 h-1.5 bg-muted rounded overflow-hidden">
          <div class="h-full bg-primary transition-all" :style="{
            width: activeJob.total ? `${Math.round((activeJob.processed / activeJob.total) * 100)}%` : '0%',
          }" />
        </div>
        <div class="text-xs text-muted-foreground mt-1">
          {{ activeJob.processed }} / {{ activeJob.total }}
          <span v-if="activeJob.result?.summary">
            · ok {{ activeJob.result.summary.ok }} · falhas {{ activeJob.result.summary.failed }}
            · cached {{ activeJob.result.summary.cached }}
          </span>
          <span v-if="activeJob.error" class="text-red-600 ml-1">{{ activeJob.error }}</span>
        </div>
      </div>

      <div v-if="gridErr" class="rounded border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive flex items-center gap-2">
        <AlertCircle class="h-4 w-4" /> {{ gridErr }}
      </div>

      <div v-if="lastPushResults.length" class="rounded border bg-muted/30 px-3 py-2 text-sm">
        <div class="font-medium mb-1">Resultado do push:</div>
        <ul class="space-y-0.5 max-h-40 overflow-y-auto">
          <li
            v-for="(r, idx) in lastPushResults"
            :key="idx"
            class="flex items-center gap-2"
            :class="r.ok ? 'text-emerald-700' : 'text-red-700'"
          >
            <span class="font-mono text-xs">{{ r.code }}</span>
            <span v-if="r.cached" class="text-xs px-1 rounded bg-amber-100 text-amber-900">cached</span>
            <span v-if="r.price">{{ Number(r.price).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' }) }}</span>
            <span v-if="r.detail" class="text-xs text-muted-foreground">{{ r.detail }}</span>
          </li>
        </ul>
      </div>

      <div class="overflow-auto rounded border max-h-[70vh]">
        <table class="text-xs">
          <thead class="bg-muted/50 sticky top-0 z-10">
            <tr>
              <th class="sticky left-0 bg-muted/50 px-2 py-2 text-left min-w-[160px]">Produto</th>
              <th
                v-for="acc in grid?.accounts ?? []"
                :key="acc.id"
                class="px-2 py-2 text-left min-w-[110px]"
              >
                <div class="flex items-center gap-1">
                  <span class="truncate">{{ acc.name }}</span>
                  <button class="btn btn-xs" title="Push para todos produtos desta conta" @click="pushAccountColumn(acc.id)">
                    <Send class="h-3 w-3" />
                  </button>
                </div>
                <div class="text-[10px] text-muted-foreground">{{ platformLabel(acc.platform) }} · kit {{ acc.kit_number }}</div>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="gridLoading && !grid">
              <td colSpan="999" class="p-6 text-center text-muted-foreground">
                <Loader2 class="inline h-4 w-4 animate-spin" /> carregando…
              </td>
            </tr>
            <tr v-else-if="!grid?.products.length">
              <td colSpan="999" class="p-6 text-center text-muted-foreground">
                Nenhum produto. Crie/importe produtos na aba Produtos.
              </td>
            </tr>
            <tr v-for="prod in grid?.products ?? []" :key="prod.id" class="border-t hover:bg-muted/20">
              <td class="sticky left-0 bg-background px-2 py-1 text-xs whitespace-nowrap">
                <div class="font-mono truncate max-w-[160px]" :title="prod.sku">{{ prod.sku }}</div>
                <div class="text-[10px] text-muted-foreground truncate max-w-[160px]" :title="prod.name">{{ prod.name }}</div>
              </td>
              <td
                v-for="acc in grid?.accounts ?? []"
                :key="acc.id"
                class="px-1 py-1 border-l"
                :class="cellTone(cellOf(prod.id, acc.id))"
              >
                <template v-if="editingCell && editingCell.accId === acc.id && editingCell.prodId === prod.id">
                  <div class="flex items-center gap-1">
                    <input
                      v-model="cellEditValue"
                      type="number" step="0.01"
                      class="border rounded px-1 py-0.5 w-20 text-xs bg-background"
                      @keydown.enter="saveOverride"
                      @keydown.escape="cancelCellEdit"
                    />
                    <button class="p-0.5 text-emerald-600 hover:bg-muted rounded" @click="saveOverride">
                      <Save class="h-3 w-3" />
                    </button>
                    <button class="p-0.5 hover:bg-muted rounded" @click="cancelCellEdit">
                      <X class="h-3 w-3" />
                    </button>
                  </div>
                </template>
                <template v-else>
                  <div class="flex items-center justify-between gap-1">
                    <span>{{ cellLabel(cellOf(prod.id, acc.id)) }}</span>
                    <div class="flex items-center gap-0.5 opacity-60 hover:opacity-100">
                      <button v-if="cellOf(prod.id, acc.id)" class="p-0.5 hover:bg-muted rounded" title="Editar override" @click="startCellEdit(cellOf(prod.id, acc.id)!)">
                        <Save class="h-3 w-3" />
                      </button>
                      <button
                        v-if="cellOf(prod.id, acc.id)"
                        class="p-0.5 hover:bg-muted rounded"
                        :title="cellOf(prod.id, acc.id)?.cell_status === 'locked' ? 'Destravar' : 'Travar'"
                        @click="setCellStatus(cellOf(prod.id, acc.id)!, cellOf(prod.id, acc.id)?.cell_status === 'locked' ? 'auto' : 'locked')"
                      >
                        <Lock class="h-3 w-3" />
                      </button>
                      <button
                        v-if="cellOf(prod.id, acc.id)"
                        class="p-0.5 hover:bg-muted rounded"
                        :title="cellOf(prod.id, acc.id)?.cell_status === 'disabled' ? 'Habilitar' : 'Desabilitar'"
                        @click="setCellStatus(cellOf(prod.id, acc.id)!, cellOf(prod.id, acc.id)?.cell_status === 'disabled' ? 'auto' : 'disabled')"
                      >
                        <Ban class="h-3 w-3" />
                      </button>
                      <button
                        v-if="cellOf(prod.id, acc.id) && cellOf(prod.id, acc.id)?.price"
                        class="p-0.5 hover:bg-emerald-100 rounded text-emerald-700"
                        title="Push esta célula"
                        :disabled="pushing"
                        @click="pushCell(cellOf(prod.id, acc.id)!)"
                      >
                        <Send class="h-3 w-3" />
                      </button>
                    </div>
                  </div>
                </template>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- ============================================ AUDITORIA -->
    <section v-else-if="tab === 'auditoria'" class="space-y-3">
      <div class="flex flex-wrap items-center gap-2">
        <button class="btn btn-sm" :disabled="auditLoading" @click="loadAudit">
          <RefreshCw class="h-4 w-4 mr-1" :class="{ 'animate-spin': auditLoading }" />
          Recarregar
        </button>
        <label class="flex items-center gap-1 text-sm">
          <input v-model="auditIncludeDismissed" type="checkbox" />
          Incluir dispensados
        </label>
        <button class="btn btn-sm" @click="syncBlingCosts">
          Sync custos Bling
        </button>
      </div>

      <p class="text-sm text-muted-foreground">
        SKUs presentes em <code>listings</code> mas ausentes em <code>pricing_products</code>. Importe ou dispense.
      </p>

      <div v-if="auditErr" class="rounded border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive flex items-center gap-2">
        <AlertCircle class="h-4 w-4" /> {{ auditErr }}
      </div>

      <div v-if="activeJob && activeJob.type === 'sync_bling_costs'" class="rounded border bg-muted/40 px-3 py-2 text-sm">
        Sync custos: {{ activeJob.processed }} / {{ activeJob.total }} ({{ activeJob.status }})
      </div>

      <div class="overflow-x-auto rounded border">
        <table class="w-full text-sm">
          <thead class="bg-muted/50 text-left">
            <tr>
              <th class="px-3 py-2">SKU</th>
              <th class="px-3 py-2 text-right">Listings</th>
              <th class="px-3 py-2">Plataformas</th>
              <th class="px-3 py-2">Amostra de títulos</th>
              <th class="px-3 py-2">Status</th>
              <th class="px-3 py-2 w-28"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="auditLoading && !auditRows.length">
              <td class="px-3 py-6 text-center text-muted-foreground" colSpan="6">
                <Loader2 class="inline h-4 w-4 animate-spin" /> carregando…
              </td>
            </tr>
            <tr v-else-if="!auditRows.length">
              <td class="px-3 py-6 text-center text-muted-foreground" colSpan="6">
                Nenhum SKU pendente.
              </td>
            </tr>
            <tr v-for="r in auditRows" :key="r.sku" class="border-t hover:bg-muted/20">
              <td class="px-3 py-2 font-mono text-xs">{{ r.sku }}</td>
              <td class="px-3 py-2 text-right">{{ r.listing_count }}</td>
              <td class="px-3 py-2 text-xs">{{ r.platforms.join(', ') }}</td>
              <td class="px-3 py-2 text-xs text-muted-foreground truncate max-w-md">
                <div v-for="(t, i) in r.sample_titles" :key="i" class="truncate">{{ t }}</div>
              </td>
              <td class="px-3 py-2 text-xs">
                <span v-if="r.dismissed" class="text-amber-700">dispensado</span>
                <span v-else class="text-red-700">pendente</span>
              </td>
              <td class="px-3 py-2 text-right">
                <button v-if="!r.dismissed" class="btn btn-xs" @click="dismissSku(r.sku)">
                  <Ban class="h-3 w-3" /> Dispensar
                </button>
                <button v-else class="btn btn-xs" @click="undismissSku(r.sku)">
                  Reverter
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- ============================================ CONCORRENCIA -->
    <section v-else-if="tab === 'concorrencia'" class="space-y-3">
      <div class="flex flex-wrap items-center gap-2">
        <input
          v-model="competitorQuery"
          placeholder="busca ML (modelo, marca…)"
          class="border rounded px-2 py-1 text-sm bg-background w-80"
          @keydown.enter="searchCompetitor"
        />
        <button class="btn btn-sm btn-primary" :disabled="competitorLoading" @click="searchCompetitor">
          <Loader2 v-if="competitorLoading" class="h-4 w-4 animate-spin mr-1" />
          <RefreshCw v-else class="h-4 w-4 mr-1" />
          Buscar
        </button>
        <span class="text-xs text-muted-foreground">cache 5 min · API pública ML</span>
      </div>

      <div v-if="competitorErr" class="rounded border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive flex items-center gap-2">
        <AlertCircle class="h-4 w-4" /> {{ competitorErr }}
      </div>

      <div class="overflow-x-auto rounded border">
        <table class="w-full text-sm">
          <thead class="bg-muted/50 text-left">
            <tr>
              <th class="px-3 py-2"></th>
              <th class="px-3 py-2">Título</th>
              <th class="px-3 py-2 text-right">Preço</th>
              <th class="px-3 py-2 text-right">Vendas</th>
              <th class="px-3 py-2 text-right">Estoque</th>
              <th class="px-3 py-2">Condição</th>
              <th class="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="competitorLoading && !competitorRows.length">
              <td class="px-3 py-6 text-center text-muted-foreground" colSpan="7">
                <Loader2 class="inline h-4 w-4 animate-spin" /> buscando…
              </td>
            </tr>
            <tr v-else-if="!competitorRows.length">
              <td class="px-3 py-6 text-center text-muted-foreground" colSpan="7">
                Sem resultados.
              </td>
            </tr>
            <tr v-for="r in competitorRows" :key="r.item_id" class="border-t hover:bg-muted/20">
              <td class="px-3 py-1">
                <img v-if="r.thumbnail" :src="r.thumbnail" alt="" class="h-10 w-10 object-cover rounded" />
              </td>
              <td class="px-3 py-2 truncate max-w-md">{{ r.title }}</td>
              <td class="px-3 py-2 text-right font-medium">
                {{ r.price.toLocaleString('pt-BR', { style: 'currency', currency: r.currency || 'BRL' }) }}
              </td>
              <td class="px-3 py-2 text-right text-muted-foreground">{{ r.sold_quantity ?? '—' }}</td>
              <td class="px-3 py-2 text-right text-muted-foreground">{{ r.available_quantity ?? '—' }}</td>
              <td class="px-3 py-2 text-xs">{{ r.condition ?? '—' }}</td>
              <td class="px-3 py-2 text-right">
                <a v-if="r.permalink" :href="r.permalink" target="_blank" rel="noopener" class="btn btn-xs">
                  Abrir
                </a>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>
