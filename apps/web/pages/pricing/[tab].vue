<script setup lang="ts">
import {
  Plus, Trash2, RefreshCw, Save, X, AlertCircle, Loader2, Eye, EyeOff,
  Star, Send, Ban, Check, Link2, Copy,
  Smartphone, Briefcase, Zap, BarChart3, DollarSign, Settings2, Upload,
  ChevronDown, Download, Undo2, Redo2, Search, Tags,
} from 'lucide-vue-next'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'tabela_precos', action: 'view' },
})

type Tab = 'tabela' | 'contas' | 'produtos' | 'concorrencia'

const TABS: { key: Tab; label: string; icon: any }[] = [
  { key: 'tabela', label: 'Tabela de Preços', icon: DollarSign },
  { key: 'contas', label: 'Contas', icon: Settings2 },
  { key: 'produtos', label: 'Produtos', icon: Upload },
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

// Departments + their subtypes are loaded from /api/segments at boot.
// Icon mapping is by slug — unknown slugs fall back to a generic Tags icon.
type DeptKey = string

const DEPT_ICONS: Record<string, any> = {
  celular: Smartphone,
  mala: Briefcase,
  eletro: Zap,
  catalogo: BarChart3,
}

const DEPARTMENTS_FALLBACK = [
  { value: 'celular', label: 'Celular', icon: Smartphone },
  { value: 'mala', label: 'Mala', icon: Briefcase },
  { value: 'eletro', label: 'Eletro', icon: Zap },
  { value: 'catalogo', label: 'Catálogo ML', icon: BarChart3 },
]

const DEPARTMENTS = ref<{ value: string; label: string; icon: any }[]>([...DEPARTMENTS_FALLBACK])

const TYPE_HEADERS_FALLBACK: Record<string, string[]> = {
  celular: ['Acessórios', 'Diversos', 'Regular', 'Robusto', 'Apple'],
  catalogo: ['Acessórios', 'Diversos', 'Regular', 'Robusto', 'Apple'],
  eletro: ['1', '2', '3', '4', '5'],
  mala: ['Acessórios', '12"', '18" e 20"', '24" acima', 'Queima de estoque'],
}

const TYPE_HEADERS = ref<Record<string, string[]>>({ ...TYPE_HEADERS_FALLBACK })

type SegmentRow = {
  id: string
  parent_id: string | null
  name: string
  slug: string
  sort_order: number
  active: boolean
}

// Raw segments list — kept alongside TYPE_HEADERS because the slot editor
// popover needs to resolve `account.segment_id` (department root) to its
// ordered children for the per-slot dropdowns.
const allSegments = ref<SegmentRow[]>([])

async function loadSegments() {
  try {
    const rows = await api<SegmentRow[]>('/api/segments')
    allSegments.value = rows
    const roots = rows
      .filter((r) => r.parent_id === null && r.active)
      .sort((a, b) => a.sort_order - b.sort_order)
    if (roots.length === 0) return

    DEPARTMENTS.value = roots.map((r) => ({
      value: r.slug,
      label: r.name,
      icon: DEPT_ICONS[r.slug] ?? Tags,
    }))

    const next: Record<string, string[]> = {}
    for (const root of roots) {
      const children = rows
        .filter((r) => r.parent_id === root.id && r.active)
        .sort((a, b) => a.sort_order - b.sort_order)
        .map((r) => r.name)
      next[root.slug] = children.length
        ? children
        : (TYPE_HEADERS_FALLBACK[root.slug] ?? [])
    }
    TYPE_HEADERS.value = next
  } catch {
    /* keep fallback */
  }
}

// ============================================================ slot editor
// Compact per-account popover that lets the user pick which segment
// slot{1..5}_segment_id points to. Default mapping mirrors the department
// children's sort_order; this popover only matters when the user wants to
// remap (e.g. swap Acessórios/Diversos for one specific account).
const slotPopoverAccountId = ref<string | null>(null)
const slotPopoverValues = ref<Array<string | null>>([null, null, null, null, null])
const slotPopoverSaving = ref(false)
const slotPopoverError = ref<string | null>(null)

function openSlotPopover(acc: Account) {
  slotPopoverAccountId.value = acc.id
  slotPopoverValues.value = [
    acc.slot1_segment_id,
    acc.slot2_segment_id,
    acc.slot3_segment_id,
    acc.slot4_segment_id,
    acc.slot5_segment_id,
  ]
  slotPopoverError.value = null
}
function closeSlotPopover() {
  slotPopoverAccountId.value = null
  slotPopoverError.value = null
}
function segmentChildrenFor(acc: Account): SegmentRow[] {
  if (!acc.segment_id) return []
  return allSegments.value
    .filter((s) => s.parent_id === acc.segment_id && s.active)
    .sort((a, b) => a.sort_order - b.sort_order || a.name.localeCompare(b.name))
}
async function saveSlotPopover(accId: string) {
  slotPopoverSaving.value = true
  slotPopoverError.value = null
  try {
    const body: Record<string, string | null> = {}
    for (let i = 0; i < 5; i++) body[`slot${i + 1}_segment_id`] = slotPopoverValues.value[i]
    const updated = await api<Account>(`/api/pricing/accounts/${accId}`, {
      method: 'PATCH',
      body,
    })
    const idx = accounts.value.findIndex((a) => a.id === accId)
    if (idx >= 0) accounts.value[idx] = updated
    closeSlotPopover()
  } catch (e: any) {
    slotPopoverError.value = e?.data?.detail?.code ?? 'save_failed'
  } finally {
    slotPopoverSaving.value = false
  }
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
  segment_id: string | null
  sort_order: number
  created_at: string
  updated_at: string
  // Slot ↔ segment binding. Each slot{N}_segment_id pins which segment the
  // margin{N}/shipping{N} pair applies to. The router resolves and returns
  // the segment name alongside for read-only display.
  slot1_segment_id: string | null
  slot2_segment_id: string | null
  slot3_segment_id: string | null
  slot4_segment_id: string | null
  slot5_segment_id: string | null
  slot1_segment_name: string | null
  slot2_segment_name: string | null
  slot3_segment_name: string | null
  slot4_segment_name: string | null
  slot5_segment_name: string | null
}

// Global toast feedback for push / auto-match outcomes. Rendered by
// <AppToastStack /> in app.vue, so we just call success/error/warning here.
const toast = useToasts()

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

// =========================================================== integrations
type Integration = { id: string; name: string; platform: string }
const integrations = ref<Integration[]>([])

async function loadIntegrations() {
  try {
    integrations.value = await api<Integration[]>('/api/integrations')
  } catch {
    integrations.value = []
  }
}

const PRICING_TO_INTEG_PLATFORM: Record<string, string> = {
  mercadolivre: 'ml',
  shopee: 'shopee',
  amazon: 'amazon',
  tiktok: 'tiktok',
  temu: 'temu',
}

function integrationsForPricingPlatform(p: string): Integration[] {
  const target = PRICING_TO_INTEG_PLATFORM[p]
  if (!target) return []
  return integrations.value
    .filter((i) => i.platform === target)
    .slice()
    .sort((a, b) => a.name.localeCompare(b.name))
}

function integrationName(id: string | null): string {
  if (!id) return '—'
  return integrations.value.find((i) => i.id === id)?.name ?? '—'
}

async function setAccountIntegration(acc: Account, integration_id: string | null) {
  try {
    const updated = await api<Account>(`/api/pricing/accounts/${acc.id}`, {
      method: 'PATCH',
      body: { integration_id },
    })
    Object.assign(acc, updated)
    flash(acc.id, 'integration_id')
  } catch (e: any) {
    accountsErr.value = e?.data?.detail?.code ?? 'save_failed'
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

// SSH-style contas filters
const contasSearch = ref('')
const contasPlatformFilter = ref<string>('')

const accountsFiltered = computed(() => {
  const q = contasSearch.value.trim().toLowerCase()
  const pf = contasPlatformFilter.value
  return accountsCurrent.value.filter((a) => {
    if (pf && a.platform !== pf) return false
    if (!q) return true
    return (
      a.name.toLowerCase().includes(q) ||
      (a.platform || '').toLowerCase().includes(q) ||
      (a.email || '').toLowerCase().includes(q)
    )
  })
})

const accountsGrouped = computed<{ platform: string; label: string; rows: Account[] }[]>(() => {
  const groups = new Map<string, Account[]>()
  for (const a of accountsFiltered.value) {
    const key = a.platform || 'outros'
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key)!.push(a)
  }
  const order = ['amazon', 'magalu', 'mercadolivre', 'shopee', 'temu', 'aliexpress', 'tiktok']
  return Array.from(groups.entries())
    .sort(([a], [b]) => {
      const ia = order.indexOf(a)
      const ib = order.indexOf(b)
      if (ia === -1 && ib === -1) return a.localeCompare(b)
      if (ia === -1) return 1
      if (ib === -1) return -1
      return ia - ib
    })
    .map(([platform, rows]) => ({
      platform,
      label: platformLabel(platform).toUpperCase(),
      rows: rows.slice().sort((a, b) =>
        (a.name || '').localeCompare(b.name || '', 'pt-BR', { sensitivity: 'base' }),
      ),
    }))
})

const platformsPresentInDept = computed(() => {
  const set = new Set<string>()
  for (const a of accountsCurrent.value) set.add(a.platform)
  return Array.from(set)
})

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

// ----- Pendências (sku-audit) for the Produtos tab -----
type AuditPendingRow = {
  sku: string
  title: string | null
  stock: number | null
  accounts: string[]
  account_count: number
  issues: string[]
  bling_cost: string | null
  pricing_cost: string | null
  dismissed: boolean
}

const auditRows = ref<AuditPendingRow[]>([])
const auditLoading = ref(false)
const auditLoaded = ref(false)
const auditError = ref<string | null>(null)
const auditShowDismissed = ref(false)
const auditHidden = ref(false)

async function loadAudit() {
  auditLoading.value = true
  auditError.value = null
  try {
    const includeDismissed = '?include_dismissed=true'
    auditRows.value = await api<AuditPendingRow[]>(`/api/pricing/sku-audit${includeDismissed}`)
    auditLoaded.value = true
  } catch (e: any) {
    auditError.value = e?.data?.detail?.code || 'audit_failed'
    auditLoaded.value = true
  } finally {
    auditLoading.value = false
  }
}

const auditPending = computed(() => auditRows.value.filter((r) => !r.dismissed))
const auditDismissed = computed(() => auditRows.value.filter((r) => r.dismissed))

function issueBadgeClass(issue: string): string {
  const base = 'inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold'
  if (issue === 'Sem anúncio') return `${base} bg-red-100 text-red-700`
  if (issue === 'Custo divergente') return `${base} bg-orange-100 text-orange-700`
  return `${base} bg-amber-100 text-amber-700`
}

async function dismissAuditSku(sku: string) {
  try {
    await api(`/api/pricing/sku-audit/${encodeURIComponent(sku)}/dismiss`, { method: 'POST' })
    const row = auditRows.value.find((r) => r.sku === sku)
    if (row) row.dismissed = true
  } catch (e: any) {
    auditError.value = e?.data?.detail?.code || 'dismiss_failed'
  }
}

async function undismissAuditSku(sku: string) {
  try {
    await api(`/api/pricing/sku-audit/${encodeURIComponent(sku)}/undismiss`, { method: 'POST' })
    const row = auditRows.value.find((r) => r.sku === sku)
    if (row) row.dismissed = false
  } catch (e: any) {
    auditError.value = e?.data?.detail?.code || 'undismiss_failed'
  }
}

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
    m[k].sort((a, b) => (a.name || '').localeCompare(b.name || '', 'pt-BR', { sensitivity: 'base' }))
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
  // ── Snapshot editing state synchronously BEFORE any async work ──────
  // @blur fires immediately when the user moves focus; the next cell's
  // @click then runs startEditAccount and reassigns editing.value /
  // editValue.value while this function is still pending. Capturing into
  // locals + clearing the refs now means the PATCH that follows always
  // targets the row the user just edited, not the one they clicked next.
  const snapshot = editing.value
  if (!snapshot) return
  const { id, field } = snapshot
  const raw = editValue.value.trim()
  editing.value = null
  editValue.value = ''

  const acc = accounts.value.find((x) => x.id === id)
  if (!acc) return

  const payload: Record<string, unknown> = {}

  if (field === 'name') {
    if (!raw) return
    payload.name = raw
  } else if (field === 'platform') {
    payload.platform = raw
  } else if (field === 'kit_number') {
    const n = parseInt(raw)
    if (!Number.isFinite(n) || n < 1 || n > 5) return
    payload.kit_number = n
  } else if (field === 'commission') {
    if (!raw) {
      payload.commission = null
    } else {
      const pct = Number(raw)
      if (Number.isNaN(pct)) return
      payload.commission = (pct / 100).toFixed(4)
    }
  } else if (field.startsWith('margin')) {
    if (!raw || raw === '-' || raw === '—') {
      payload[field] = null
    } else {
      const pct = Number(raw)
      if (Number.isNaN(pct)) return
      payload[field] = (pct / 100).toFixed(4)
    }
  } else if (field.startsWith('shipping')) {
    if (!raw || raw === '-' || raw === '—') {
      payload[field] = null
    } else {
      const n = Number(raw)
      if (Number.isNaN(n)) return
      payload[field] = n
    }
  } else if (field.startsWith('observation') || field === 'email' || field === 'phone' || field === 'listing_type') {
    payload[field] = raw || null
  } else {
    return
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
    const r = await api<{ matched?: number; skipped?: number; updated?: number }>(
      '/api/pricing/accounts/auto-match',
      { method: 'POST' },
    )
    await loadAccounts()
    // Backend may report either `matched` (new field) or `updated` (legacy).
    const matched = r.matched ?? r.updated ?? 0
    const skipped = r.skipped ?? 0
    if (matched > 0) {
      toast.success(
        `${matched} conta(s) vinculada(s) com sucesso!`,
        skipped > 0 ? `${skipped} sem match` : undefined,
      )
    } else {
      toast.info('Todas as contas já estão vinculadas.', skipped > 0 ? `${skipped} sem match` : undefined)
    }
  } catch (e: any) {
    accountsErr.value = e?.data?.detail?.code ?? 'auto_match_failed'
    toast.error('Erro ao vincular integrações', accountsErr.value || undefined)
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
  // ── Snapshot editing state synchronously BEFORE any async work ──────
  // Same race fix as commitEditAccount: blur on cell A → click on cell B
  // rewrites editing.value before the PATCH for A actually fires. Capture
  // {id, field, raw} now and clear the refs so startEditProduct(B) doesn't
  // bleed cell B's empty value into the cell A request.
  const snapshot = editing.value
  if (!snapshot) return
  const { id, field } = snapshot
  const raw = editValue.value.trim()
  editing.value = null
  editValue.value = ''

  const p =
    products.value.find((x) => x.id === id) ??
    grid.value?.products.find((x) => x.id === id)
  if (!p) return

  const payload: Record<string, unknown> = {}

  if (field === 'sku' || field === 'name') {
    if (!raw) return
    payload[field] = raw
  } else if (field === 'department') {
    payload.department = raw
  } else if (field === 'product_type') {
    const n = parseInt(raw)
    if (!Number.isFinite(n)) return
    payload.product_type = n
  } else if (field.startsWith('cost_kit')) {
    if (!raw || raw === '-' || raw === '—') {
      if (field === 'cost_kit1') payload[field] = '0'
      else payload[field] = null
    } else {
      const n = Number(raw)
      if (Number.isNaN(n)) return
      payload[field] = n.toFixed(2)
    }
  } else if (field === 'description' || field === 'model' || field === 'ean') {
    payload[field] = raw || null
  } else {
    return
  }

  try {
    const updated = await api<PricingProduct>(`/api/pricing/products/${id}`, {
      method: 'PATCH',
      body: payload,
    })
    Object.assign(p, updated)
    // Mirror cost edits to the grid copy so the tabela view repaints without
    // a full reload. Recompute affected cells.
    const gp = grid.value?.products.find((x) => x.id === id)
    if (gp) Object.assign(gp, updated)
    flash(id, field)
    if (field.startsWith('cost_kit')) {
      // In-memory recompute first for instant repaint; then loadGrid so the
      // backend formula (with stored overrides, product_type, etc) is the
      // source of truth. Falls back gracefully if grid isn't loaded.
      recomputeCellsForProduct(id)
      if (grid.value) await loadGrid()
    }
  } catch (e: any) {
    productsErr.value = e?.data?.detail?.code ?? 'save_failed'
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
  cell_status: 'auto' | 'manual' | 'locked' | 'disabled' | 'NA' | 'SV' | 'error' | 'no_link'
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

// ---------- Feature 3: obs editing in header
const editingObsId = ref<string | null>(null)
const obsValue = ref('')

// ---------- Feature 4: undo/redo
type UndoEntry = { prodId: string; accId: string; oldValue: string; newValue: string }
const undoStack = ref<UndoEntry[]>([])
const redoStack = ref<UndoEntry[]>([])

// ---------- Feature 6: bling sync from grid
const syncingBlingCosts = ref(false)

// ---------- Feature 7: grid search
const gridSearch = ref('')

// ---------- Feature 8: push dropdown
const showPushMenu = ref(false)
const pushLabel = ref('')

// ---------- Feature 11: keyboard nav
const selectedCell = ref<{ row: number; col: number } | null>(null)

// ---------- Feature 12: compare mode
const compareProductIds = ref<Set<string>>(new Set())
const actualPriceMap = ref<Record<string, Record<string, number | null>>>({})
const competitorLoadingId = ref<string | null>(null)
const compareToast = ref<string | null>(null)

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
  // Stock + sales maps load in the background; failures don't break the grid.
  loadStockMap()
  loadSalesMaps()
}

// Maps: pricing_product_id -> integer (stock or units sold)
const stockMap = ref<Record<string, number>>({})
const salesMap7d = ref<Record<string, number>>({})
const salesMap30d = ref<Record<string, number>>({})

async function loadStockMap() {
  try {
    const qs = department.value ? `?department=${department.value}` : ''
    stockMap.value = await api<Record<string, number>>(`/api/pricing/stock-map${qs}`)
  } catch {
    stockMap.value = {}
  }
}

async function loadSalesMaps() {
  const dep = department.value
  const [m7, m30] = await Promise.all([
    api<Record<string, number>>(`/api/pricing/sales-map?days=7${dep ? `&department=${dep}` : ''}`).catch(() => ({})),
    api<Record<string, number>>(`/api/pricing/sales-map?days=30${dep ? `&department=${dep}` : ''}`).catch(() => ({})),
  ])
  salesMap7d.value = m7
  salesMap30d.value = m30
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
  // SSH semantics: NA/SV are explicit user flags. Missing/uncomputable cells
  // show "—" so the user can tell apart "sem anúncio" from "sem preço".
  if (!c) return '—'
  if (c.cell_status === 'NA') return 'NA'
  if (c.cell_status === 'SV') return 'SV'
  if (c.source === 'disabled') return '∅'
  if (c.price == null) return '—'
  return Number(c.price).toFixed(0)
}

// SSH: "Ao editar, TODOS os preços da linha recalculam automaticamente no
// frontend (pois dependem do custo)". While the user is mid-edit on a
// cost_kit cell, every non-override cell in that product's row paints with
// the formula evaluated against the typed value.
function liveCellLabel(prod: any, acc: Account): string {
  const c = cellOf(prod.id, acc.id)
  // Explicit user states + override (price fixed) always win.
  if (c) {
    if (c.cell_status === 'NA') return 'NA'
    if (c.cell_status === 'SV') return 'SV'
    if (c.source === 'disabled') return '∅'
    if (c.source === 'override') return c.price != null ? Number(c.price).toFixed(0) : '—'
  }
  if (
    editing.value
    && editing.value.id === prod.id
    && editing.value.field.startsWith('cost_kit')
  ) {
    const typed = Number(editValue.value)
    if (Number.isFinite(typed)) {
      const fakeProd = { ...prod, [editing.value.field]: typed }
      const p = computePrice(acc, fakeProd as any)
      if (p != null) return String(Math.round(p))
    }
  }
  return cellLabel(c)
}

function cellTone(c: GridCell | undefined): string {
  if (!c) return 'text-muted-foreground'
  if (c.cell_status === 'NA') return 'bg-gray-200 text-gray-500 font-semibold'
  if (c.cell_status === 'SV') return 'bg-amber-100 text-amber-700 font-semibold'
  // Transient post-push states. SSH paints these so the user can decide whether
  // to mark NA/SV permanently or fix the underlying issue and retry.
  if (c.cell_status === 'error') return 'bg-red-50 text-red-700'
  if (c.cell_status === 'no_link') return 'bg-amber-50 text-amber-700'
  if (c.source === 'disabled') return 'bg-muted/50 text-muted-foreground'
  if (c.source === 'locked') return 'bg-amber-50 text-amber-900 dark:bg-amber-900/20 dark:text-amber-100'
  // SSH: override (preço fixo manual) → fundo laranja.
  if (c.source === 'override') return 'bg-orange-100 text-orange-900 dark:bg-orange-900/30 dark:text-orange-100'
  if (c.source === 'missing_inputs') return 'bg-gray-100 text-gray-500 font-semibold'
  if (c.price != null) {
    const prod = grid.value?.products.find(p => p.id === c.pricing_product_id)
    const acc = grid.value?.accounts.find(a => a.id === c.pricing_account_id)
    if (prod && acc) {
      const price = Number(c.price)
      const cost = getKitCost(prod, acc.kit_number)
      const ms = getMarginShipping(acc, prod.product_type)
      const shipping = ms ? Number(ms.shipping) : 0
      if (price < cost + shipping) return 'bg-red-100 text-red-700 font-bold'
    }
  }
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
  const oldCell = cellOf(prodId, accId)
  const oldValue = oldCell?.price != null ? String(oldCell.price) : ''
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
    if (oldValue !== val) {
      undoStack.value.push({ prodId, accId, oldValue, newValue: val })
      redoStack.value = []
    }
    cancelCellEdit()
    await loadGrid()
  } catch (e: any) {
    gridErr.value = e?.data?.detail?.code ?? 'save_failed'
  }
}

async function setCellStatus(c: GridCell, status: 'auto' | 'locked' | 'disabled' | 'NA' | 'SV') {
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
    const okItems = r.results.filter((x) => x.ok)
    const failItems = r.results.filter((x) => !x.ok)
    if (failItems.length === 0) {
      const priceTxt = okItems[0]?.price ? ` — R$ ${Number(okItems[0].price).toFixed(0)}` : ''
      toast.success(`Preço enviado${priceTxt}`, `${okItems.length} variação(ões) ok`)
    } else if (okItems.length === 0) {
      toast.error(
        'Erro no push',
        failItems.map((f) => f.detail || f.code).slice(0, 5),
      )
    } else {
      toast.warning(
        `Push parcial: ${okItems.length} ok, ${failItems.length} erro(s)`,
        failItems.map((f) => f.detail || f.code).slice(0, 5),
      )
    }
  } catch (e: any) {
    gridErr.value = e?.data?.detail?.code ?? 'push_failed'
    toast.error('Erro no push', gridErr.value || undefined)
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
      const ok = r.results.filter((x) => x.ok).length
      const fail = r.results.filter((x) => !x.ok).length
      if (fail === 0) {
        toast.success('Push concluído!', `${ok} preço(s) enviado(s) com sucesso`)
      } else if (ok === 0) {
        toast.error(
          `Push falhou: ${fail} erro(s)`,
          r.results
            .filter((x) => !x.ok)
            .map((f) => f.detail || f.code)
            .slice(0, 5),
        )
      } else {
        toast.warning(
          `Envio: ${ok} ok, ${fail} erro(s)`,
          r.results
            .filter((x) => !x.ok)
            .map((f) => f.detail || f.code)
            .slice(0, 5),
        )
      }
    } catch (e: any) {
      gridErr.value = e?.data?.detail?.code ?? 'push_failed'
      toast.error('Erro no push', gridErr.value || undefined)
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
    toast.info(`Push em background iniciado`, `${items.length} item(ns) na fila`)
    await pollJob(created.job_id)
  } catch (e: any) {
    gridErr.value = e?.data?.detail?.code ?? 'push_failed'
    toast.error('Erro ao iniciar push em batch', gridErr.value || undefined)
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
      // Surface the terminal outcome via toast so the user doesn't have to
      // scroll back to the (also-rendered) result box.
      if (job.status === 'succeeded') {
        const s = job.result?.summary
        if (s) {
          if (s.failed === 0) {
            toast.success('Push concluído!', `${s.ok} preço(s) enviado(s)`)
          } else {
            toast.error(
              `Envio: ${s.ok} ok, ${s.failed} falha(s)`,
              s.cached ? `${s.cached} cached` : undefined,
            )
          }
        } else {
          toast.success('Push concluído!')
        }
      } else if (job.status === 'failed') {
        toast.error('Push falhou', job.error || 'Erro desconhecido')
      } else {
        toast.warning('Push cancelado')
      }
      return
    }
  } catch (e: any) {
    if (attempts > 60) {
      gridErr.value = 'job_poll_timeout'
      toast.error('Timeout aguardando o job', 'Recarregue a página pra ver o status')
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

// =========================================================== grid extras (features)

// Feature 1: account groups by platform + kit (SSH order)
// Platform order chosen to match the SSH UI:
//   amazon → magalu → mercadolivre → shopee → temu → aliexpress → tiktok
const PLATFORM_ORDER: Record<string, number> = {
  amazon: 0, magalu: 1, mercadolivre: 2, shopee: 3,
  temu: 4, aliexpress: 5, tiktok: 6,
}

// Same SSH order is reused by the body iteration so each data column lines
// up with its grouped header — see `gridAccounts` below.
const gridAccounts = computed<Account[]>(() => {
  return (grid.value?.accounts ?? []).slice().sort((a, b) => {
    const pa = PLATFORM_ORDER[a.platform] ?? 99
    const pb = PLATFORM_ORDER[b.platform] ?? 99
    if (pa !== pb) return pa - pb
    if (a.kit_number !== b.kit_number) return (a.kit_number || 0) - (b.kit_number || 0)
    return (a.name || '').localeCompare(b.name || '', 'pt-BR', { sensitivity: 'base' })
  })
})

const accountGroups = computed<
  { label: string; platform: string; accounts: Account[] }[]
>(() => {
  const accs = gridAccounts.value
  const groups: { label: string; platform: string; accounts: Account[] }[] = []
  let currentKey = ''
  for (const acc of accs) {
    const key = `${acc.platform}-kit${acc.kit_number}`
    const label = `${platformLabel(acc.platform)} kit ${acc.kit_number}`
    if (key !== currentKey) {
      groups.push({ label, platform: acc.platform, accounts: [acc] })
      currentKey = key
    } else {
      groups[groups.length - 1].accounts.push(acc)
    }
  }
  return groups
})

const firstAccountIdInGroup = computed<Set<string>>(() => {
  const s = new Set<string>()
  for (const g of accountGroups.value) {
    if (g.accounts[0]) s.add(g.accounts[0].id)
  }
  return s
})

// Feature 7: grid search
const filteredGridProducts = computed(() => {
  const prods = grid.value?.products ?? []
  const q = gridSearch.value.trim().toLowerCase()
  const filtered = q
    ? prods.filter((p) => p.sku.toLowerCase().includes(q) || p.name.toLowerCase().includes(q))
    : prods.slice()
  return filtered.sort((a, b) =>
    (a.name || '').localeCompare(b.name || '', 'pt-BR', { sensitivity: 'base' }),
  )
})

// Feature 9: negative margin helpers + counter
function getKitCost(prod: PricingProduct, kitNumber: number): number {
  const key = `cost_kit${Math.max(1, Math.min(4, kitNumber || 1))}` as keyof PricingProduct
  const v = prod[key] ?? prod.cost_kit1
  return Number(v ?? 0)
}

function getMarginShipping(acc: Account, productType: number): { margin: number; shipping: number } | null {
  const t = Math.max(1, Math.min(5, productType || 1))
  const margin = Number((acc as any)[`margin${t}`] || 0)
  const shipping = Number((acc as any)[`shipping${t}`] || 0)
  if (!margin && !shipping) return null
  return { margin, shipping }
}

// Mirrors backend calc.py: (cost * (1 + margin) + shipping) / (1 - commission)
// Returns null if any required input is missing (margin/commission/cost).
function computePrice(acc: Account, prod: PricingProduct): number | null {
  const cost = getKitCost(prod, acc.kit_number)
  if (!cost) return null
  const ms = getMarginShipping(acc, (prod as any).product_type ?? 2)
  if (!ms) return null
  const commission = Number(acc.commission || 0)
  const denom = 1 - commission
  if (denom <= 0) return null
  // SSH rounds the computed price to integer reais (Math.round). Cents
  // only show up on manual overrides.
  const price = (cost * (1 + ms.margin) + ms.shipping) / denom
  return Math.round(price)
}

// Re-compute every non-overridden cell for `prodId` against the current
// account roster. Skips cells the user explicitly set (manual/locked/NA/SV/
// disabled status, or a real price_override producing source='override').
// Used after inline kit edits to reflect the new cost without a full grid
// reload. has_override alone is NOT a skip signal — overrides with
// cell_status='auto' and no price_override are just leftover rows from the
// SSH import and should still recompute.
function recomputeCellsForProduct(prodId: string) {
  if (!grid.value) return
  const prod = grid.value.products.find((x) => x.id === prodId)
  if (!prod) return
  for (const acc of grid.value.accounts) {
    const c = cellOf(prodId, acc.id)
    if (!c) continue
    if (c.cell_status === 'manual' || c.cell_status === 'locked'
        || c.cell_status === 'NA' || c.cell_status === 'SV'
        || c.source === 'disabled' || c.source === 'override') continue
    const newPrice = computePrice(acc, prod)
    c.price = newPrice == null ? null : (newPrice.toFixed(2) as any)
    c.source = newPrice == null ? 'missing_inputs' : 'computed'
  }
}

const negativeMarginCount = computed(() => {
  if (!grid.value) return 0
  let count = 0
  for (const prod of grid.value.products) {
    for (const acc of grid.value.accounts) {
      const c = cellOf(prod.id, acc.id)
      if (!c || c.price == null || c.source === 'disabled' || c.source === 'locked') continue
      if (c.cell_status === 'NA' || c.cell_status === 'SV') continue
      const price = Number(c.price)
      const cost = getKitCost(prod, acc.kit_number)
      const ms = getMarginShipping(acc, prod.product_type)
      const shipping = ms ? Number(ms.shipping) : 0
      if (price < cost + shipping) count++
    }
  }
  return count
})

// Feature 13: platform color coding
function platformBg(platform: string): string {
  switch (platform) {
    case 'shopee': return 'bg-orange-50/50'
    case 'amazon': return 'bg-yellow-50/50'
    case 'temu': return 'bg-purple-50/50'
    case 'aliexpress': return 'bg-red-50/50'
    case 'tiktok': return 'bg-pink-50/50'
    case 'mercadolivre': return 'bg-blue-50/50'
    default: return ''
  }
}
function platformHeaderBg(platform: string): string {
  switch (platform) {
    case 'amazon':       return 'bg-yellow-50 dark:bg-yellow-900/30'
    case 'magalu':       return 'bg-blue-50 dark:bg-blue-900/30'
    case 'mercadolivre': return 'bg-muted'
    case 'shopee':       return 'bg-orange-50 dark:bg-orange-900/30'
    case 'temu':         return 'bg-purple-50 dark:bg-purple-900/30'
    case 'aliexpress':   return 'bg-red-50 dark:bg-red-900/30'
    case 'tiktok':       return 'bg-pink-50 dark:bg-pink-900/30'
    default:             return 'bg-muted/50'
  }
}

// Feature 3: header obs editing
function startEditObs(accId: string, field: string, currentVal: string | null) {
  editingObsId.value = `${accId}-${field}`
  obsValue.value = currentVal || ''
}

async function commitObs(accId: string, field: string) {
  // Snapshot obsValue synchronously: blur on obs1 → click on obs2 would
  // reassign obsValue before the PATCH fires (same race as commitEditProduct).
  const raw = obsValue.value.trim()
  editingObsId.value = null
  obsValue.value = ''
  const acc = (grid.value?.accounts ?? []).find(a => a.id === accId)
  if (!acc) return
  try {
    const updated = await api<Account>(`/api/pricing/accounts/${accId}`, {
      method: 'PATCH',
      body: { [field]: raw || null },
    })
    Object.assign(acc, updated)
    const local = accounts.value.find(a => a.id === accId)
    if (local) Object.assign(local, updated)
  } catch (e: any) {
    gridErr.value = e?.data?.detail?.code ?? 'save_failed'
  }
}

// Feature 4: undo/redo
async function saveOverrideValue(prodId: string, accId: string, val: string) {
  try {
    if (!val) {
      await api(
        `/api/pricing/overrides?pricing_product_id=${prodId}&pricing_account_id=${accId}`,
        { method: 'DELETE' },
      ).catch((e: any) => {
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
    await loadGrid()
  } catch (e: any) {
    gridErr.value = e?.data?.detail?.code ?? 'save_failed'
  }
}

function handleUndo() {
  if (!undoStack.value.length) return
  const entry = undoStack.value.pop()!
  redoStack.value.push(entry)
  saveOverrideValue(entry.prodId, entry.accId, entry.oldValue)
}

function handleRedo() {
  if (!redoStack.value.length) return
  const entry = redoStack.value.pop()!
  undoStack.value.push(entry)
  saveOverrideValue(entry.prodId, entry.accId, entry.newValue)
}

// Feature 5: Excel/CSV export
function handleExportExcel() {
  if (!grid.value) return
  const accs = grid.value.accounts
  const prods = filteredGridProducts.value
  const headers = ['SKU', 'Produto', 'Bling', '7d', '30d', department.value === 'celular' ? 'Kit1' : 'Custo']
  if (department.value === 'celular') headers.push('Kit2', 'Kit3', 'Kit4')
  for (const acc of accs) headers.push(acc.name)
  const rows = prods.map(p => {
    const row: string[] = [
      p.sku,
      p.name,
      String(stockMap.value[p.id] ?? 0),
      String(salesMap7d.value[p.id] ?? 0),
      String(salesMap30d.value[p.id] ?? 0),
      String(Number(p.cost_kit1 || 0).toFixed(0)),
    ]
    if (department.value === 'celular') {
      row.push(
        String(Number(p.cost_kit2 || 0).toFixed(0)),
        String(Number(p.cost_kit3 || 0).toFixed(0)),
        String(Number(p.cost_kit4 || 0).toFixed(0)),
      )
    }
    for (const acc of accs) {
      const c = cellOf(p.id, acc.id)
      if (c?.cell_status === 'NA') row.push('NA')
      else if (c?.cell_status === 'SV') row.push('SV')
      else row.push(c?.price != null ? String(Number(c.price).toFixed(0)) : '')
    }
    return row
  })
  const csv = [headers, ...rows].map(r => r.map(v => `"${String(v).replace(/"/g, '""')}"`).join(',')).join('\n')
  const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `tabela-precos-${department.value}-${new Date().toISOString().slice(0, 10)}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

// Feature 6: Bling sync from grid
async function syncBlingCostsFromGrid() {
  syncingBlingCosts.value = true
  try {
    const r = await api<{ job_id: string }>('/api/pricing/jobs/sync-bling-costs', { method: 'POST' })
    activeJob.value = null
    await pollJob(r.job_id)
    await loadGrid()
  } catch (e: any) {
    gridErr.value = e?.data?.detail?.code ?? 'sync_failed'
  } finally {
    syncingBlingCosts.value = false
  }
}

// Feature 8: push dropdown helpers
async function pushAllAndClose() {
  showPushMenu.value = false
  pushLabel.value = 'Todas'
  try {
    await pushAllVisible()
  } finally {
    pushLabel.value = ''
  }
}

async function pushAccountAndClose(accId: string, accName: string) {
  showPushMenu.value = false
  pushLabel.value = accName
  try {
    await pushAccountColumn(accId)
  } finally {
    pushLabel.value = ''
  }
}

// Feature 11: keyboard navigation in grid
function handleGridKeyDown(e: KeyboardEvent) {
  if (e.ctrlKey || e.metaKey) {
    if (e.key.toLowerCase() === 'z') { e.preventDefault(); handleUndo(); return }
    if (e.key.toLowerCase() === 'y') { e.preventDefault(); handleRedo(); return }
  }
  if (!selectedCell.value) return
  if (editingCell.value) return
  const { row, col } = selectedCell.value
  const maxRow = filteredGridProducts.value.length - 1
  const maxCol = (grid.value?.accounts.length ?? 0) - 1
  switch (e.key) {
    case 'ArrowUp': e.preventDefault(); if (row > 0) selectedCell.value = { row: row - 1, col }; break
    case 'ArrowDown': e.preventDefault(); if (row < maxRow) selectedCell.value = { row: row + 1, col }; break
    case 'ArrowLeft': e.preventDefault(); if (col > 0) selectedCell.value = { row, col: col - 1 }; break
    case 'ArrowRight': e.preventDefault(); if (col < maxCol) selectedCell.value = { row, col: col + 1 }; break
    case 'Enter': {
      e.preventDefault()
      const prod = filteredGridProducts.value[row]
      const acc = (grid.value?.accounts ?? [])[col]
      if (prod && acc) {
        const c = cellOf(prod.id, acc.id)
        if (c) startCellEdit(c)
      }
      break
    }
    case 'Escape': e.preventDefault(); selectedCell.value = null; break
  }
}

// Feature 12: compare mode (placeholder until /api/pricing/actual-prices exists)
async function toggleCompare(prod: PricingProduct) {
  if (compareProductIds.value.has(prod.id)) {
    compareProductIds.value.delete(prod.id)
    compareProductIds.value = new Set(compareProductIds.value)
    return
  }
  competitorLoadingId.value = prod.id
  compareProductIds.value.add(prod.id)
  compareProductIds.value = new Set(compareProductIds.value)
  try {
    const results = await api<Record<string, number | null>>(
      `/api/pricing/actual-prices/${prod.id}?department=${department.value}`,
    )
    actualPriceMap.value[prod.id] = results
  } catch {
    compareToast.value = 'Comparação real ainda não disponível neste backend.'
    setTimeout(() => { compareToast.value = null }, 2500)
  } finally {
    competitorLoadingId.value = null
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

function fmtBRL(v: string | number | null) {
  if (v == null || v === '') return '—'
  const n = Number(v)
  if (Number.isNaN(n)) return '—'
  return `R$ ${n.toFixed(0)}`
}

function tabelaName(p: PricingProduct): string {
  const headers = TYPE_HEADERS.value
  const list = headers[(p.department as DeptKey)] ?? headers.celular
  const t = Math.max(1, Math.min(5, p.product_type || 1))
  return list[t - 1] ?? '—'
}

function tabelaBadgeClass(p: PricingProduct): string {
  const t = Math.max(1, Math.min(5, p.product_type || 1))
  switch (t) {
    case 1: return 'bg-slate-100 text-slate-700'
    case 2: return 'bg-blue-100 text-blue-700'
    case 3: return 'bg-emerald-100 text-emerald-700'
    case 4: return 'bg-orange-100 text-orange-700'
    case 5: return 'bg-purple-100 text-purple-700'
    default: return 'bg-gray-100 text-gray-700'
  }
}

// =========================================================== boot

await loadSegments()
await loadAccounts()
await loadProducts()
await loadIntegrations()

watch(
  tab,
  async (t) => {
    if (t === 'tabela') await loadGrid()
    if (t === 'produtos') loadAudit()
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

      <!-- Filters: busca + plataforma + contador (SSH-style) -->
      <div class="flex flex-wrap gap-2 items-center">
        <input
          v-model="contasSearch"
          placeholder="Buscar conta..."
          class="border rounded px-2 py-1 text-sm bg-background w-56"
        />
        <select
          v-model="contasPlatformFilter"
          class="border rounded px-2 py-1 text-sm bg-background h-8"
        >
          <option value="">Todas plataformas</option>
          <option v-for="p in platformsPresentInDept" :key="p" :value="p">{{ platformLabel(p) }}</option>
        </select>
        <span class="ml-auto text-xs text-muted-foreground">
          {{ accountsFiltered.length }} conta(s)
        </span>
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
                v-for="(label, i) in TYPE_HEADERS[department] ?? []"
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
              <td colSpan="15" class="text-center py-6 text-muted-foreground">
                <Loader2 class="inline h-4 w-4 animate-spin" /> carregando…
              </td>
            </tr>
            <tr v-else-if="!accountsFiltered.length && !showAddAcc">
              <td colSpan="20" class="text-center py-6 text-muted-foreground">
                {{ accountsCurrent.length ? 'Nenhuma conta corresponde aos filtros.' : 'Nenhuma conta neste departamento.' }}
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

            <!-- data rows grouped by platform (SSH-style) -->
            <template v-for="group in accountsGrouped" :key="group.platform">
              <tr class="bg-muted/40">
                <td colSpan="20" class="px-3 py-1.5 text-[11px] font-bold tracking-wider text-muted-foreground uppercase">
                  {{ group.label }} · {{ group.rows.length }} CONTA(S)
                </td>
              </tr>
              <tr v-for="acc in group.rows" :key="acc.id" class="hover:bg-accent/30">
                <!-- name -->
              <td
                class="border border-border px-2 py-1.5 text-xs text-left relative"
                :class="{
                  'ring-2 ring-blue-500 ring-inset bg-background': isEditing(acc.id, 'name'),
                  'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(acc.id, 'name'),
                }"
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
                <div v-else class="flex items-center gap-1 group/acc">
                  <span
                    class="font-medium cursor-pointer flex-1 truncate"
                    @click="startEditAccount(acc, 'name')"
                  >{{ acc.name }}<Check v-if="isFlashed(acc.id, 'name')" class="inline h-3 w-3 ml-1 text-emerald-600" /></span>
                  <button
                    v-if="canEditContas"
                    class="opacity-0 group-hover/acc:opacity-100 text-muted-foreground hover:text-foreground p-0.5 rounded"
                    title="Vincular segmento aos slots de margem/frete"
                    @click.stop="openSlotPopover(acc)"
                  >
                    <Tags class="h-3 w-3" />
                  </button>
                </div>
                <!-- slot↔segment editor popover (per-account) -->
                <div
                  v-if="slotPopoverAccountId === acc.id"
                  class="absolute left-0 top-full mt-1 z-30 w-72 rounded-md border bg-background shadow-lg p-3 text-left"
                  @click.stop
                >
                  <div class="text-xs font-semibold mb-2">
                    Slots desta conta · {{ DEPARTMENTS.find(d => d.value === acc.department)?.label }}
                  </div>
                  <div class="space-y-1.5">
                    <div v-for="i in 5" :key="i" class="flex items-center gap-2">
                      <span class="text-[10px] text-muted-foreground w-12 shrink-0">
                        slot{{ i }}<br/>
                        <span class="opacity-70">m{{ i }}/f{{ i }}</span>
                      </span>
                      <select
                        v-model="slotPopoverValues[i - 1]"
                        class="flex-1 text-xs border rounded px-1.5 py-1 bg-background"
                      >
                        <option :value="null">— sem vínculo —</option>
                        <option
                          v-for="seg in segmentChildrenFor(acc)"
                          :key="seg.id"
                          :value="seg.id"
                        >{{ seg.name }}</option>
                      </select>
                    </div>
                  </div>
                  <p v-if="slotPopoverError" class="text-[10px] text-red-600 mt-1.5">{{ slotPopoverError }}</p>
                  <div class="flex justify-end gap-1 pt-2 mt-2 border-t">
                    <button class="text-xs px-2 py-1 rounded hover:bg-muted" @click="closeSlotPopover">Cancelar</button>
                    <button
                      class="text-xs px-2 py-1 rounded bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50"
                      :disabled="slotPopoverSaving"
                      @click="saveSlotPopover(acc.id)"
                    >
                      {{ slotPopoverSaving ? 'salvando…' : 'salvar' }}
                    </button>
                  </div>
                </div>
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
            </template>
          </tbody>
        </table>
      </div>
    </section>

    <!-- ============================================ PRODUTOS -->
    <section v-else-if="tab === 'produtos'" class="space-y-3">
      <!-- Pendências box (Custo divergente / Sem anúncio / Fora da tabela) -->
      <div
        v-if="!auditHidden && (auditLoading || auditLoaded || auditError)"
        class="rounded border border-amber-300 bg-amber-50 p-3"
      >
        <div class="flex items-center justify-between">
          <div class="text-sm font-semibold text-amber-800 flex items-center gap-2">
            <AlertCircle class="h-4 w-4" />
            <span v-if="auditLoading">Verificando pendências…</span>
            <span v-else-if="auditPending.length === 0 && auditDismissed.length === 0">
              Nenhuma pendência encontrada.
            </span>
            <span v-else>
              {{ auditPending.length }} produto(s) do Bling com pendências
              <span v-if="auditDismissed.length" class="font-normal text-amber-700">({{ auditDismissed.length }} dispensado(s))</span>
            </span>
          </div>
          <div class="flex gap-2">
            <button class="btn btn-sm" :disabled="auditLoading" @click="loadAudit">
              <RefreshCw class="h-3.5 w-3.5 mr-1" :class="{ 'animate-spin': auditLoading }" /> Atualizar
            </button>
            <button class="btn btn-sm" @click="auditHidden = true">Ocultar</button>
          </div>
        </div>

        <div v-if="auditError" class="mt-2 text-xs text-red-700">{{ auditError }}</div>

        <div v-if="auditPending.length" class="mt-3 overflow-auto rounded border border-amber-200 bg-background max-h-[420px]">
          <table class="w-full text-xs">
            <thead class="bg-amber-100/60 sticky top-0">
              <tr class="text-left">
                <th class="px-2 py-1.5 font-semibold">SKU</th>
                <th class="px-2 py-1.5 font-semibold">Produto</th>
                <th class="px-2 py-1.5 font-semibold text-right w-20">Estoque</th>
                <th class="px-2 py-1.5 font-semibold w-44">Contas</th>
                <th class="px-2 py-1.5 font-semibold">Pendências</th>
                <th class="px-2 py-1.5 font-semibold w-24"></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in auditPending" :key="row.sku" class="border-t border-amber-100 align-top">
                <td class="px-2 py-1.5 font-mono">{{ row.sku }}</td>
                <td class="px-2 py-1.5">{{ row.title || '—' }}</td>
                <td class="px-2 py-1.5 text-right tabular-nums">{{ row.stock ?? 0 }}</td>
                <td class="px-2 py-1.5">
                  <span v-if="row.account_count === 0" class="text-red-600 font-medium">Nenhuma</span>
                  <span v-else>
                    {{ row.account_count }} ({{ row.accounts.slice(0, 3).join(', ') }}{{ row.accounts.length > 3 ? ', ...' : '' }})
                  </span>
                </td>
                <td class="px-2 py-1.5">
                  <div class="flex flex-col gap-1">
                    <div class="flex flex-wrap gap-1">
                      <span v-for="iss in row.issues" :key="iss" :class="issueBadgeClass(iss)">{{ iss }}</span>
                    </div>
                    <div v-if="row.issues.includes('Custo divergente')" class="text-[10px] text-orange-700">
                      Bling: R$ {{ row.bling_cost ?? '—' }} · Tabela: R$ {{ row.pricing_cost ?? '—' }}
                    </div>
                  </div>
                </td>
                <td class="px-2 py-1.5 text-right">
                  <button class="btn btn-xs" @click="dismissAuditSku(row.sku)">Dispensar</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div v-if="auditDismissed.length" class="mt-3">
          <button class="text-xs text-amber-800 underline" @click="auditShowDismissed = !auditShowDismissed">
            {{ auditShowDismissed ? 'Ocultar' : 'Dispensados' }} ({{ auditDismissed.length }})
          </button>
          <div v-if="auditShowDismissed" class="mt-2 overflow-auto rounded border border-amber-200 bg-background max-h-[280px]">
            <table class="w-full text-xs">
              <thead class="bg-amber-100/40 sticky top-0">
                <tr class="text-left">
                  <th class="px-2 py-1.5 font-semibold">SKU</th>
                  <th class="px-2 py-1.5 font-semibold">Produto</th>
                  <th class="px-2 py-1.5 font-semibold">Pendências</th>
                  <th class="px-2 py-1.5 font-semibold w-24"></th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in auditDismissed" :key="row.sku" class="border-t border-amber-100">
                  <td class="px-2 py-1.5 font-mono">{{ row.sku }}</td>
                  <td class="px-2 py-1.5">{{ row.title || '—' }}</td>
                  <td class="px-2 py-1.5">
                    <span v-for="iss in row.issues" :key="iss" :class="issueBadgeClass(iss)" class="mr-1">{{ iss }}</span>
                  </td>
                  <td class="px-2 py-1.5 text-right">
                    <button class="btn btn-xs" @click="undismissAuditSku(row.sku)">Restaurar</button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>

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
              <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[200px]">Produto</th>
              <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[100px]">EAN</th>
              <template v-if="department === 'mala'">
                <th class="text-left px-2 py-2 font-medium border-b border-border">Descrição</th>
                <th class="text-left px-2 py-2 font-medium border-b border-border">Modelo</th>
              </template>
              <th class="text-right px-2 py-2 font-medium border-b border-border w-24">Kit 1</th>
              <th class="text-right px-2 py-2 font-medium border-b border-border w-24">Kit 2</th>
              <th class="text-right px-2 py-2 font-medium border-b border-border w-24">Kit 3</th>
              <th class="text-right px-2 py-2 font-medium border-b border-border w-24">Kit 4</th>
              <th class="text-center px-2 py-2 font-medium border-b border-border w-24">Tabela</th>
              <th class="text-center px-2 py-2 font-medium border-b border-border w-16">Catálogo</th>
              <th class="text-center px-2 py-2 font-medium border-b border-border w-12"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="productsLoading && !products.length">
              <td colSpan="15" class="text-center py-6 text-muted-foreground">
                <Loader2 class="inline h-4 w-4 animate-spin" /> carregando…
              </td>
            </tr>
            <tr v-else-if="!productsCurrent.length && !showAddProd">
              <td colSpan="15" class="text-center py-6 text-muted-foreground">
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
                <select v-model.number="newProd.product_type" class="w-full text-xs border rounded px-1.5 py-1 bg-background text-center">
                  <option
                    v-for="(label, i) in (TYPE_HEADERS[department] ?? [])"
                    :key="i"
                    :value="i + 1"
                  >
                    {{ label }}
                  </option>
                </select>
              </td>
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
                <span v-else>{{ fmtBRL((p as any)[`cost_kit${k}`]) }}</span>
              </td>
              <td
                class="border border-border px-2 py-1.5 text-xs text-center cursor-pointer"
                :class="{ 'ring-2 ring-blue-500 ring-inset bg-background': isEditing(p.id, 'product_type'), 'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(p.id, 'product_type') }"
                @click="!isEditing(p.id, 'product_type') && startEditProduct(p, 'product_type')"
              >
                <select
                  v-if="isEditing(p.id, 'product_type')"
                  :ref="setEditInputRef"
                  v-model="editValue"
                  class="w-full text-xs bg-background outline-none text-center"
                  @blur="commitEditProduct" @change="commitEditProduct" @keydown.escape.prevent="cancelEdit"
                >
                  <option
                    v-for="(label, i) in (TYPE_HEADERS[p.department as DeptKey] ?? [])"
                    :key="i"
                    :value="String(i + 1)"
                  >
                    {{ label }}
                  </option>
                </select>
                <span v-else class="inline-block px-2 py-0.5 rounded text-[10px] font-medium" :class="tabelaBadgeClass(p)">
                  {{ tabelaName(p) }}
                </span>
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
        <div class="relative flex-1 min-w-[200px] max-w-sm">
          <Search class="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
          <input
            v-model="gridSearch"
            placeholder="Buscar por SKU ou nome..."
            class="border rounded pl-7 pr-2 py-1 text-sm bg-background w-full"
          />
        </div>
        <button class="btn btn-sm" :disabled="!undoStack.length" title="Desfazer (Ctrl+Z)" @click="handleUndo">
          <Undo2 class="h-4 w-4" />
        </button>
        <button class="btn btn-sm" :disabled="!redoStack.length" title="Refazer (Ctrl+Y)" @click="handleRedo">
          <Redo2 class="h-4 w-4" />
        </button>
        <button class="btn btn-sm" :disabled="!grid?.products.length" @click="handleExportExcel">
          <Download class="h-4 w-4 mr-1" /> Excel
        </button>
        <button
          class="btn btn-sm text-green-700 border-green-300 hover:bg-green-50"
          :disabled="syncingBlingCosts"
          @click="syncBlingCostsFromGrid"
        >
          <Loader2 v-if="syncingBlingCosts" class="h-4 w-4 animate-spin mr-1" />
          <RefreshCw v-else class="h-4 w-4 mr-1" />
          Custo Bling
        </button>
        <div class="relative inline-block">
          <button
            class="btn btn-sm bg-blue-600 text-white hover:bg-blue-700"
            :disabled="pushing || !grid?.cells.length"
            @click="showPushMenu = !showPushMenu"
          >
            <Send class="h-4 w-4 mr-1" />
            {{ pushLabel || 'Enviar' }}
            <ChevronDown class="h-3 w-3 ml-1" />
          </button>
          <div
            v-if="showPushMenu"
            class="absolute right-0 mt-1 w-64 bg-background border rounded-md shadow-lg z-50 max-h-80 overflow-y-auto"
          >
            <button class="w-full text-left px-3 py-2 text-sm hover:bg-muted font-medium" @click="pushAllAndClose">
              Enviar todas as contas
            </button>
            <div class="border-t" />
            <template v-for="group in accountGroups" :key="group.label">
              <div class="px-3 py-1 text-xs text-muted-foreground font-semibold bg-muted/30">
                {{ group.label }}
              </div>
              <button
                v-for="acc in group.accounts"
                :key="acc.id"
                class="w-full text-left px-3 py-1.5 text-sm hover:bg-muted flex justify-between items-center gap-2"
                @click="pushAccountAndClose(acc.id, acc.name)"
              >
                <span class="truncate">{{ acc.name }}</span>
                <span class="text-[10px] text-muted-foreground shrink-0">{{ acc.listing_type || acc.platform }}</span>
              </button>
            </template>
          </div>
        </div>
        <button class="btn btn-sm" :disabled="pushing" @click="sendManualReport">
          Enviar relatório
        </button>
        <button class="btn btn-sm" :disabled="gridLoading" @click="loadGrid">
          <RefreshCw class="h-4 w-4 mr-1" :class="{ 'animate-spin': gridLoading }" />
          Recarregar
        </button>
        <span v-if="pushing" class="text-sm text-muted-foreground flex items-center gap-1">
          <Loader2 class="h-3 w-3 animate-spin" /> enviando…
        </span>
      </div>

      <div
        v-if="negativeMarginCount > 0"
        class="rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800 flex items-center gap-2"
      >
        <AlertCircle class="h-4 w-4" />
        <strong>{{ negativeMarginCount }}</strong>
        combinação(ões) com margem negativa (preço &lt; custo + frete)
      </div>

      <div v-if="compareToast" class="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800">
        {{ compareToast }}
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

      <div
        class="overflow-auto rounded border max-h-[70vh] focus:outline-none"
        tabindex="0"
        @keydown="handleGridKeyDown"
      >
        <table class="text-xs border-collapse">
          <thead class="bg-background sticky top-0 z-20">
            <!-- Row 1: Platform groups -->
            <tr>
              <th
                class="sticky left-0 bg-background px-2 py-1 text-left z-30"
                :colspan="department === 'celular' ? 8 : 5"
              />
              <th
                v-for="group in accountGroups"
                :key="`g-${group.label}`"
                :colspan="group.accounts.length"
                class="px-2 py-1 text-center text-[11px] font-semibold border-l-[3px] border-gray-500"
                :class="platformHeaderBg(group.platform)"
              >
                {{ group.label }}
              </th>
            </tr>
            <!-- Row 2: Account names + obs (left spacer is intentionally
                 empty — "Produto" lives in the row 3 column header to avoid
                 the duplicate label readers were seeing) -->
            <tr>
              <th
                class="sticky left-0 bg-background px-2 py-1 text-left z-30 align-bottom"
                :colspan="department === 'celular' ? 8 : 5"
              />
              <th
                v-for="acc in gridAccounts"
                :key="`n-${acc.id}`"
                class="px-1 py-1 text-left min-w-[110px] align-top border-l"
                :class="platformHeaderBg(acc.platform)"
              >
                <div class="flex items-center gap-1">
                  <span class="text-xs font-semibold truncate" :title="acc.name">{{ acc.name }}</span>
                  <button class="p-0.5 hover:bg-muted rounded shrink-0" title="Push para esta conta" @click="pushAccountColumn(acc.id)">
                    <Send class="h-3 w-3" />
                  </button>
                </div>
                <template v-for="field in ['observation', 'observation2', 'observation3']" :key="field">
                  <div v-if="editingObsId === `${acc.id}-${field}`">
                    <input
                      v-model="obsValue"
                      class="w-full text-[9px] border rounded px-1 py-0.5 bg-background"
                      @blur="commitObs(acc.id, field)"
                      @keydown.enter="commitObs(acc.id, field)"
                      @keydown.escape="editingObsId = null"
                    />
                  </div>
                  <div
                    v-else
                    class="text-[9px] cursor-pointer truncate leading-tight"
                    :class="(acc as any)[field] ? 'text-amber-700 font-medium' : 'text-muted-foreground/60 italic'"
                    :title="(acc as any)[field] || field"
                    @click="startEditObs(acc.id, field, (acc as any)[field])"
                  >
                    {{ (acc as any)[field] || (field === 'observation' ? 'obs1' : field === 'observation2' ? 'obs2' : 'obs3') }}
                  </div>
                </template>
              </th>
            </tr>
            <!-- Row 3: Listing type + cost column headers -->
            <tr>
              <th class="sticky left-0 bg-background px-2 py-1 text-left z-30 min-w-[200px]">Produto</th>
              <th class="sticky bg-background px-1 py-1 text-center text-[10px] text-green-700 font-bold z-30 min-w-[56px]" :style="{ left: '200px' }">bling</th>
              <th class="sticky bg-blue-50 px-1 py-1 text-center text-[10px] text-blue-700 font-bold z-30 min-w-[56px]" :style="{ left: '256px' }">7d</th>
              <th class="sticky bg-blue-50 px-1 py-1 text-center text-[10px] text-blue-700 font-bold z-30 min-w-[56px]" :style="{ left: '312px' }">30d</th>
              <template v-if="department === 'celular'">
                <th class="sticky bg-background px-1 py-1 text-center text-[10px] font-bold text-blue-700 z-30 min-w-[56px]" :style="{ left: '368px' }">kit 1</th>
                <th class="sticky bg-background px-1 py-1 text-center text-[10px] text-muted-foreground z-30 min-w-[56px]" :style="{ left: '424px' }">kit 2</th>
                <th class="sticky bg-background px-1 py-1 text-center text-[10px] text-muted-foreground z-30 min-w-[56px]" :style="{ left: '480px' }">kit 3</th>
                <th class="sticky bg-background px-1 py-1 text-center text-[10px] text-muted-foreground z-30 min-w-[56px]" :style="{ left: '536px' }">kit 4</th>
              </template>
              <template v-else>
                <th class="sticky bg-background px-1 py-1 text-center text-[10px] font-bold text-blue-700 z-30 min-w-[56px]" :style="{ left: '368px' }">custo</th>
              </template>
              <th
                v-for="acc in gridAccounts"
                :key="`lt-${acc.id}`"
                class="px-1 py-1 text-center text-[10px] text-muted-foreground border-l"
                :class="platformHeaderBg(acc.platform)"
              >
                {{ acc.listing_type || platformLabel(acc.platform) }}
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="gridLoading && !grid">
              <td colSpan="999" class="p-6 text-center text-muted-foreground">
                <Loader2 class="inline h-4 w-4 animate-spin" /> carregando…
              </td>
            </tr>
            <tr v-else-if="!filteredGridProducts.length">
              <td colSpan="999" class="p-6 text-center text-muted-foreground">
                {{ grid?.products.length ? 'Nenhum produto corresponde à busca.' : 'Nenhum produto. Crie/importe produtos na aba Produtos.' }}
              </td>
            </tr>
            <tr
              v-for="(prod, rowIdx) in filteredGridProducts"
              :key="prod.id"
              class="border-t hover:bg-muted/20 group"
            >
              <!-- Sticky: SKU + nome + compare toggle -->
              <td class="sticky left-0 bg-background px-2 py-1 text-xs whitespace-nowrap z-10 min-w-[200px]">
                <div class="flex items-center gap-1">
                  <button
                    class="p-0.5 rounded shrink-0 transition-opacity"
                    :class="compareProductIds.has(prod.id) ? 'text-purple-600 bg-purple-100 opacity-100' : 'text-gray-400 hover:text-purple-600 opacity-0 group-hover:opacity-100'"
                    title="Comparar com preço real"
                    @click.stop="toggleCompare(prod)"
                  >
                    <Loader2 v-if="competitorLoadingId === prod.id" class="h-3 w-3 animate-spin" />
                    <Eye v-else class="h-3 w-3" />
                  </button>
                  <div class="min-w-0">
                    <div class="text-xs truncate max-w-[180px]" :title="`${prod.sku} — ${prod.name}`">{{ prod.name }}</div>
                  </div>
                </div>
              </td>
              <!-- Sticky: Bling stock (was cost; now stock from products table) -->
              <td
                class="sticky bg-green-50 px-1 py-1 text-center text-xs font-bold z-10 min-w-[56px]"
                :class="(stockMap[prod.id] ?? 0) === 0 ? 'text-red-600' : 'text-green-700'"
                :style="{ left: '200px' }"
              >
                {{ stockMap[prod.id] ?? 0 }}
              </td>
              <!-- Sticky: 7d sales -->
              <td
                class="sticky bg-blue-50 px-1 py-1 text-center text-xs font-bold z-10 min-w-[56px]"
                :class="(salesMap7d[prod.id] ?? 0) === 0 ? 'text-muted-foreground/60' : 'text-blue-700'"
                :style="{ left: '256px' }"
              >
                {{ (salesMap7d[prod.id] ?? 0) === 0 ? '—' : salesMap7d[prod.id] }}
              </td>
              <!-- Sticky: 30d sales -->
              <td
                class="sticky bg-blue-50 px-1 py-1 text-center text-xs font-bold z-10 min-w-[56px]"
                :class="(salesMap30d[prod.id] ?? 0) === 0 ? 'text-muted-foreground/60' : 'text-blue-700'"
                :style="{ left: '312px' }"
              >
                {{ (salesMap30d[prod.id] ?? 0) === 0 ? '—' : salesMap30d[prod.id] }}
              </td>
              <!-- Sticky cost: kits or custo (positions shifted by +112px) -->
              <template v-if="department === 'celular'">
                <td
                  v-for="k in 4" :key="`gridkit-${k}`"
                  class="sticky bg-background px-1 py-1 text-center text-xs z-10 min-w-[56px] cursor-pointer"
                  :class="[
                    k === 1 ? 'text-blue-700 font-bold' : 'text-muted-foreground',
                    isEditing(prod.id, `cost_kit${k}`) ? 'ring-2 ring-blue-500 ring-inset' : '',
                    isFlashed(prod.id, `cost_kit${k}`) ? 'bg-emerald-50 dark:bg-emerald-900/20' : '',
                  ]"
                  :style="{ left: `${368 + (k - 1) * 56}px` }"
                  @click="canEditProdutos && !isEditing(prod.id, `cost_kit${k}`) && startEditProduct(prod as any, `cost_kit${k}`)"
                >
                  <input
                    v-if="isEditing(prod.id, `cost_kit${k}`)"
                    :ref="setEditInputRef"
                    v-model="editValue" type="number" step="0.01"
                    class="w-full text-xs bg-transparent outline-none text-center"
                    @blur="commitEditProduct" @keydown.enter.prevent="commitEditProduct" @keydown.escape.prevent="cancelEdit"
                  />
                  <span v-else>{{ (prod as any)[`cost_kit${k}`] != null ? Number((prod as any)[`cost_kit${k}`]).toFixed(0) : '—' }}</span>
                </td>
              </template>
              <template v-else>
                <td
                  class="sticky bg-background text-blue-700 font-bold px-1 py-1 text-center text-xs z-10 min-w-[56px] cursor-pointer"
                  :class="[
                    isEditing(prod.id, 'cost_kit1') ? 'ring-2 ring-blue-500 ring-inset' : '',
                    isFlashed(prod.id, 'cost_kit1') ? 'bg-emerald-50 dark:bg-emerald-900/20' : '',
                  ]"
                  :style="{ left: '368px' }"
                  @click="canEditProdutos && !isEditing(prod.id, 'cost_kit1') && startEditProduct(prod as any, 'cost_kit1')"
                >
                  <input
                    v-if="isEditing(prod.id, 'cost_kit1')"
                    :ref="setEditInputRef"
                    v-model="editValue" type="number" step="0.01"
                    class="w-full text-xs bg-transparent outline-none text-center"
                    @blur="commitEditProduct" @keydown.enter.prevent="commitEditProduct" @keydown.escape.prevent="cancelEdit"
                  />
                  <span v-else>{{ Number(prod.cost_kit1 || 0).toFixed(0) }}</span>
                </td>
              </template>
              <!-- Account cells -->
              <td
                v-for="(acc, accIdx) in gridAccounts"
                :key="acc.id"
                class="px-1 py-1"
                :class="[
                  cellTone(cellOf(prod.id, acc.id)),
                  platformBg(acc.platform),
                  'border-l',
                  selectedCell?.row === rowIdx && selectedCell?.col === accIdx ? 'ring-2 ring-blue-500 ring-inset' : '',
                ]"
                @click="selectedCell = { row: rowIdx, col: accIdx }"
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
                  <div class="flex flex-col gap-0.5">
                    <div class="flex items-center justify-between gap-1">
                      <span>{{ liveCellLabel(prod, acc) }}</span>
                      <div class="flex items-center gap-0.5 opacity-60 hover:opacity-100">
                        <button v-if="cellOf(prod.id, acc.id)" class="p-0.5 hover:bg-muted rounded" title="Editar override" @click.stop="startCellEdit(cellOf(prod.id, acc.id)!)">
                          <Save class="h-3 w-3" />
                        </button>
                        <button
                          v-if="cellOf(prod.id, acc.id)"
                          class="p-0.5 hover:bg-muted rounded"
                          :title="cellOf(prod.id, acc.id)?.cell_status === 'disabled' ? 'Habilitar' : 'Desabilitar'"
                          @click.stop="setCellStatus(cellOf(prod.id, acc.id)!, cellOf(prod.id, acc.id)?.cell_status === 'disabled' ? 'auto' : 'disabled')"
                        >
                          <Ban class="h-3 w-3" />
                        </button>
                        <button
                          v-if="cellOf(prod.id, acc.id)?.cell_status === 'NA' || cellOf(prod.id, acc.id)?.cell_status === 'error'"
                          class="px-1 py-0.5 hover:bg-muted rounded text-[9px] font-bold"
                          :class="cellOf(prod.id, acc.id)?.cell_status === 'NA' ? 'text-gray-700 bg-gray-200' : 'text-gray-500'"
                          :title="cellOf(prod.id, acc.id)?.cell_status === 'NA' ? 'Limpar NA' : 'Marcar como NA (não anunciar)'"
                          @click.stop="setCellStatus(cellOf(prod.id, acc.id)!, cellOf(prod.id, acc.id)?.cell_status === 'NA' ? 'auto' : 'NA')"
                        >NA</button>
                        <button
                          v-if="cellOf(prod.id, acc.id)?.cell_status === 'SV' || cellOf(prod.id, acc.id)?.cell_status === 'no_link'"
                          class="px-1 py-0.5 hover:bg-muted rounded text-[9px] font-bold"
                          :class="cellOf(prod.id, acc.id)?.cell_status === 'SV' ? 'text-amber-700 bg-amber-100' : 'text-amber-600'"
                          :title="cellOf(prod.id, acc.id)?.cell_status === 'SV' ? 'Limpar SV' : 'Marcar como SV (sem vínculo)'"
                          @click.stop="setCellStatus(cellOf(prod.id, acc.id)!, cellOf(prod.id, acc.id)?.cell_status === 'SV' ? 'auto' : 'SV')"
                        >SV</button>
                        <button
                          v-if="cellOf(prod.id, acc.id) && cellOf(prod.id, acc.id)?.price"
                          class="p-0.5 hover:bg-emerald-100 rounded text-emerald-700"
                          title="Push esta célula"
                          :disabled="pushing"
                          @click.stop="pushCell(cellOf(prod.id, acc.id)!)"
                        >
                          <Send class="h-3 w-3" />
                        </button>
                      </div>
                    </div>
                    <div
                      v-if="compareProductIds.has(prod.id) && actualPriceMap[prod.id]?.[acc.id] != null"
                      class="text-[9px]"
                      :class="actualPriceMap[prod.id][acc.id]! > Number(cellOf(prod.id, acc.id)?.price || 0) ? 'text-cyan-600' : 'text-purple-600'"
                    >
                      real: {{ actualPriceMap[prod.id][acc.id] }}
                    </div>
                  </div>
                </template>
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
