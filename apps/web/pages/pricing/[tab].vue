<script setup lang="ts">
import {
  Plus, Trash2, RefreshCw, Save, X, AlertCircle, Loader2, Eye, EyeOff,
  Star, Send, Ban, Check, Link2, Copy, Minus,
  Smartphone, Briefcase, Zap, BarChart3, DollarSign, Settings2, Upload,
  ChevronDown, Download, Undo2, Redo2, Search, Tags, Camera, Pencil, FolderPlus,
  Image as ImageIcon, Film,
} from 'lucide-vue-next'
import { isoToday } from '~/lib/date'

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
  return (TABS.find((x) => x.key === t)?.key ?? 'tabela') as Tab
})

function setTab(t: Tab) {
  // Preserve the active department (?dept=) when switching sub-tabs.
  router.push({ path: `/pricing/${t}`, query: { ...route.query } })
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
  { value: 'shein', label: 'Shein' },
  { value: 'amazon', label: 'Amazon' },
  { value: 'temu', label: 'Temu' },
  { value: 'aliexpress', label: 'AliExpress' },
  { value: 'tiktok', label: 'TikTok' },
  { value: 'magalu', label: 'Magalu' },
] as const

function platformLabel(p: string) {
  return PLATFORMS.find((x) => x.value === p)?.label ?? p
}

// Department lives in the URL (?dept=mala) so it survives the [tab].vue
// re-mount that happens when the user clicks a different sub-tab. Default
// is 'celular' when the query is absent.
const department = computed<DeptKey>({
  get() {
    const q = route.query.dept
    return typeof q === 'string' && q ? q : 'celular'
  },
  set(v) {
    router.replace({ path: route.path, query: { ...route.query, dept: v } })
  },
})

// Quantas colunas de kit cada departamento mostra na aba Produtos
// (celular vai até kit 8; mala e eletro usam só o kit 1).
const KIT_COUNT_BY_DEPT: Record<string, number> = { celular: 8, mala: 1, eletro: 1 }
const kitCount = computed(() => KIT_COUNT_BY_DEPT[department.value] ?? 4)

// Composição de cada kit do Celular (anotação do Eduardo, 2026-08-21).
// Só informativo: aparece embaixo do "Kit N" no cabeçalho da aba Produtos
// e no hover das colunas "kit N" da calculadora. Mala e eletro têm um kit
// só, sem nome — ficam sem legenda.
const KIT_NOMES_CELULAR: Record<number, string> = {
  1: 'Celular + Fone c/ fio',
  2: 'Celular + Fone',
  3: 'Celular + Relógio',
  4: 'Celular + Fone + Relógio',
  5: 'Celular + Carregador',
  6: 'Celular + Fone + Airtag',
  7: 'Celular + Óculos',
  8: 'Celular + Airtag',
}
// Versão curta pro cabeçalho (coluna estreita): "Celular" vira "Cel".
function kitNome(k: number): string {
  const nome = department.value === 'celular' ? KIT_NOMES_CELULAR[k] : undefined
  return nome ? nome.replace('Celular', 'Cel') : ''
}
function kitTitulo(k: number): string {
  const nome = department.value === 'celular' ? KIT_NOMES_CELULAR[k] : undefined
  return nome ? `Kit ${k} = ${nome}` : `Kit ${k}`
}

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
  discount: string | null
  affiliate: string | null
  ads: string | null
  coupon: string | null
  offer: string | null
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
  magalu: 'magalu',
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
  const order = ['amazon', 'magalu', 'mercadolivre', 'shopee', 'shein', 'temu', 'aliexpress', 'tiktok']
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
  cost_kit5: string | number | null
  cost_kit6: string | number | null
  cost_kit7: string | number | null
  cost_kit8: string | number | null
  description: string | null
  model: string | null
  ean: string | null
  is_active: boolean
  in_catalog: boolean
  fotos_url: string | null
  fotos_count: number | null
  videos_count: number | null
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
  divergent_departments: string[] | null
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

// Botão "Atualizar estoque do Bling" no painel de pendências: dispara o job
// refresh-bling-stock (puxa saldo do Bling página a página e regrava em
// products), aguarda terminar e recarrega a auditoria. Corrige números
// defasados — ex. kit com componente zerado que ficou com estoque em cache.
// O endpoint exige produtos:edit, então gateamos o botão pela mesma permissão.
const canRefreshStock = useCan('produtos', 'edit')
const stockRefreshing = ref(false)

async function refreshBlingStock() {
  if (stockRefreshing.value) return
  stockRefreshing.value = true
  try {
    const { job_id } = await api<{ job_id: string }>('/api/jobs/refresh-bling-stock', {
      method: 'POST',
    })
    for (let i = 0; i < 120; i++) {
      await new Promise((r) => setTimeout(r, 1500))
      const job = await api<BatchJob>(`/api/jobs/${job_id}`)
      if (job.status === 'succeeded' || job.status === 'failed' || job.status === 'cancelled') {
        if (job.status === 'succeeded') {
          toast.success('Estoque atualizado', 'Puxado do Bling')
          await loadAudit()
        } else {
          toast.error('Falha ao atualizar estoque', job.error || undefined)
        }
        return
      }
    }
    toast.warning('Atualização ainda rodando', 'Clique em Atualizar daqui a pouco')
  } catch (e: any) {
    toast.error('Erro ao atualizar estoque', e?.data?.detail?.code || e?.message || undefined)
  } finally {
    stockRefreshing.value = false
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
  if (el) {
    el.focus()
    if ('select' in el) (el as HTMLInputElement).select?.()
    return
  }
  // Fallback: setEditInputRef sometimes hasn't been called yet on the
  // first tick when the v-if just remounted the input — retry once on
  // the next macrotask before giving up.
  setTimeout(() => {
    const el2 = editInputRef.value
    if (!el2) return
    el2.focus()
    if ('select' in el2) (el2 as HTMLInputElement).select?.()
  }, 0)
}

// =========================================================== account inline edit

// 5 campos livres exibidos embaixo do nome da loja (grade de preço + aba
// Contas). Substituem os antigos obs1/obs2/obs3. `observation` é reusado como
// "obs"; desconto/afiliado/ads/cupom são colunas próprias (migration 0173).
const STORE_NOTE_FIELDS: { key: string; label: string }[] = [
  { key: 'discount', label: 'desconto' },
  { key: 'affiliate', label: 'afiliado' },
  { key: 'ads', label: 'ads' },
  { key: 'coupon', label: 'cupom' },
  { key: 'offer', label: 'oferta' },
  { key: 'observation', label: 'obs' },
]

function startEditAccount(acc: Account, field: string) {
  if (!canEditContas.value) return
  // Flush any pending edit BEFORE overwriting editing.value. commitEditAccount
  // is now synchronous — it captures + clears in the same tick and dispatches
  // the PATCH fire-and-forget — so calling it here cannot race with the new
  // edit we're about to start.
  if (
    editing.value &&
    (editing.value.id !== acc.id || editing.value.field !== field)
  ) {
    commitEditAccount()
  }
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

// Aceita decimal BRASILEIRO nos campos numéricos digitados: "674,50" →
// 674.5 e "1.234,56" → 1234.56 (com vírgula, pontos são milhar). Number()
// puro devolvia NaN pra vírgula: o cadastro de produto mandava "NaN" pro
// backend (422 → banner create_failed, Eduardo 2026-08-24) e as edições
// inline simplesmente não salvavam.
function parseDec(raw: unknown): number {
  const s = String(raw ?? '').trim()
  if (!s) return NaN
  const norm = s.includes(',') ? s.replace(/\./g, '').replace(',', '.') : s
  return Number(norm)
}

// SSH parity: commit is SYNCHRONOUS. Reads editing.value + editValue.value
// the moment blur/enter fires, clears them immediately, then dispatches
// the PATCH fire-and-forget. Keeping this sync (not async) guarantees the
// snapshot can't be reassigned by another scheduled microtask between
// capture and clear — we're already running synchronously inside the
// blur callback.
function commitEditAccount(): void {
  const snap = editing.value
  if (!snap) return
  const { id, field } = snap
  const raw = editValue.value.trim()
  editing.value = null
  editValue.value = ''
  void _patchAccount(id, field, raw)
}

async function _patchAccount(id: string, field: string, raw: string) {
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
    if (!Number.isFinite(n) || n < 1 || n > 8) return
    payload.kit_number = n
  } else if (field === 'commission') {
    if (!raw) {
      payload.commission = null
    } else {
      const pct = parseDec(raw)
      if (Number.isNaN(pct)) return
      payload.commission = (pct / 100).toFixed(4)
    }
  } else if (field.startsWith('margin')) {
    if (!raw || raw === '-' || raw === '—') {
      payload[field] = null
    } else {
      const pct = parseDec(raw)
      if (Number.isNaN(pct)) return
      payload[field] = (pct / 100).toFixed(4)
    }
  } else if (field.startsWith('shipping')) {
    if (!raw || raw === '-' || raw === '—') {
      payload[field] = null
    } else {
      const n = parseDec(raw)
      if (Number.isNaN(n)) return
      payload[field] = n
    }
  } else if (field.startsWith('observation') || field === 'discount' || field === 'affiliate' || field === 'ads' || field === 'coupon' || field === 'offer' || field === 'email' || field === 'phone' || field === 'listing_type') {
    payload[field] = raw || null
  } else {
    return
  }

  // Optimistic update: paint the new value before the round-trip.
  Object.assign(acc, payload)

  try {
    const updated = await api<Account>(`/api/pricing/accounts/${id}`, {
      method: 'PATCH',
      body: payload,
    })
    Object.assign(acc, updated)
    flash(id, field)
  } catch (e: any) {
    accountsErr.value = e?.data?.detail?.code ?? 'save_failed'
    // Reload to revert the optimistic update on failure.
    await loadAccounts()
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
    if (newAcc.commission) body.commission = (parseDec(newAcc.commission) / 100).toFixed(4)
    const created = await api<Account>('/api/pricing/accounts', { method: 'POST', body })
    accounts.value.push(created)
    showAddAcc.value = false
  } catch (e: any) {
    accountsErr.value = e?.data?.detail?.code ?? 'create_failed'
  } finally {
    addingAcc.value = false
  }
}

// ---- Fotos via MEGA (sidecar megacmd) --------------------------------
const megaBusy = ref(false)
const showMegaLogin = ref(false)
const megaLoginForm = reactive({ email: '', password: '', code: '' })
const megaLoginBusy = ref(false)
const megaLoginErr = ref('')
const showMegaPreview = ref(false)
const megaPreview = ref<any | null>(null)
const megaApplyBusy = ref(false)
const uploadingFotosId = ref<string | null>(null)
const fotosFileInput = ref<HTMLInputElement | null>(null)
let fotosUploadTarget: PricingProduct | null = null

function megaErrMsg(e: any): string {
  const d = e?.data?.detail
  const msg = d?.message || d?.code || e?.message || 'erro'
  if (/not logged/i.test(String(msg)))
    return 'Conta MEGA não conectada — clique em "Fotos (MEGA)" pra fazer login'
  return String(msg)
}

async function megaSyncClick() {
  if (megaBusy.value) return
  megaBusy.value = true
  try {
    const st = await api<any>('/api/pricing/mega/status')
    if (!st.available) {
      toast.error('MEGA indisponível', [st.error || 'serviço não está no ar'])
      return
    }
    if (!st.logged_in) {
      megaLoginErr.value = ''
      showMegaLogin.value = true
      return
    }
    await megaDryRun()
  } catch (e: any) {
    toast.error('MEGA', [megaErrMsg(e)])
  } finally {
    megaBusy.value = false
  }
}

async function megaDryRun() {
  const rep = await api<any>('/api/pricing/mega/sync', {
    method: 'POST',
    body: { dry_run: true, only_missing: true },
  })
  megaPreview.value = rep
  showMegaPreview.value = true
}

async function submitMegaLogin() {
  if (megaLoginBusy.value) return
  megaLoginBusy.value = true
  megaLoginErr.value = ''
  try {
    const res = await api<any>('/api/pricing/mega/login', {
      method: 'POST',
      body: {
        email: megaLoginForm.email.trim(),
        password: megaLoginForm.password,
        code: megaLoginForm.code.trim() || null,
      },
    })
    if (!res.ok) {
      megaLoginErr.value = res.message || 'login falhou — confira email e senha'
      return
    }
    showMegaLogin.value = false
    megaLoginForm.password = ''
    megaLoginForm.code = ''
    toast.success('MEGA conectado', ['Buscando pastas de fotos…'])
    megaBusy.value = true
    try {
      await megaDryRun()
    } finally {
      megaBusy.value = false
    }
  } catch (e: any) {
    megaLoginErr.value = megaErrMsg(e)
  } finally {
    megaLoginBusy.value = false
  }
}

async function megaApply() {
  if (megaApplyBusy.value) return
  megaApplyBusy.value = true
  try {
    const rep = await api<any>('/api/pricing/mega/sync', {
      method: 'POST',
      body: { dry_run: false, only_missing: true },
    })
    showMegaPreview.value = false
    const lines = [`${rep.applied} produto(s) receberam link de fotos`]
    if (rep.errors?.length) {
      lines.push(`${rep.errors.length} erro(s) ao gerar link`)
      toast.warning('Fotos do MEGA sincronizadas', lines)
    } else {
      toast.success('Fotos do MEGA sincronizadas', lines)
    }
    await loadProducts()
    // Reconta fotos/vídeos em segundo plano (não segura o fechamento do modal).
    void megaCountsRefresh(true)
  } catch (e: any) {
    toast.error('Sincronizar MEGA', [megaErrMsg(e)])
  } finally {
    megaApplyBusy.value = false
  }
}

// -- contagem de fotos/vídeos por pasta (mega-find via sidecar) --------
const megaCountsBusy = ref(false)
async function megaCountsRefresh(silent = false) {
  if (megaCountsBusy.value) return
  megaCountsBusy.value = true
  try {
    const rep = await api<any>('/api/pricing/mega/counts/refresh', {
      method: 'POST',
      body: {},
    })
    await loadProducts()
    if (!silent)
      toast.success('Contagem de mídias atualizada', [
        `${rep.folders_counted} pasta(s) contadas`,
        `${rep.products_updated} produto(s) atualizados`,
      ])
  } catch (e: any) {
    if (!silent) toast.error('Contar fotos/vídeos', [megaErrMsg(e)])
  } finally {
    megaCountsBusy.value = false
  }
}

// -- criação da estrutura de pastas marca/modelo no MEGA ---------------
// Pasta principal sugerida = departamento ativo (espelha /Celular, /Malas,
// /Eletro no MEGA — mesma organização das abas do DaVinci).
const MEGA_PASTA_POR_DEPT: Record<string, string> = {
  celular: 'Celular',
  mala: 'Malas',
  eletro: 'Eletro',
}
const showMegaScaffold = ref(false)
const megaScaffoldBrand = ref('')
const megaScaffoldText = ref('')
const megaScaffoldBusy = ref(false)
const megaScaffoldCount = computed(
  () => megaScaffoldText.value.split('\n').map(s => s.trim()).filter(Boolean).length,
)

// Sugere uma pasta por MODELO a partir dos produtos sem link: corta
// tamanho/kit de mala ("M2 listrada mala 12+18" → "M2 listrada") e
// memória/cor de eletrônico ("uranyx Fossibot S7 8.128 - …" → "Fossibot S7"),
// então várias linhas da tabela apontam pra mesma pasta de fotos.
function suggestMegaFolders(): string[] {
  const out: string[] = []
  const seen = new Set<string>()
  for (const p of products.value) {
    if (p.fotos_url) continue
    let n = (p.name || '').trim()
    if (!n) continue
    if (/^uranyx\s/i.test(n)) {
      n = n.split(/\s+-\s+/)[0].replace(/^uranyx\s+/i, '')
      n = n.replace(/\s+\d+\.\d+.*$/, '').replace(/\s*\(.*$/, '')
    } else if (/\b(mala|maleta)\b/i.test(n)) {
      n = n.replace(/\s+(mala|maleta)\b.*$/i, '')
    } else {
      n = n.split(/\s+-\s+/)[0].replace(/\s+\d+\.\d+.*$/, '')
    }
    n = n.replace(/\s*\+\s*acess\w*.*$/i, '').trim()
    if (!n) continue
    const key = n.toLowerCase()
    if (!seen.has(key)) {
      seen.add(key)
      out.push(n)
    }
  }
  const norm = (s: string) =>
    s.toLowerCase().normalize('NFKD').replace(/[\u0300-\u036f]/g, '')
      .replace(/[^a-z0-9]+/g, ' ').trim()
  // descarta nome que é prefixo de outro ("M2" × "M2 listrada") pra não
  // criar pastas ambíguas no matching
  return out.filter(a => !out.some(b => b !== a && norm(b).startsWith(norm(a) + ' ')))
}

function openMegaScaffold() {
  if (!megaScaffoldBrand.value.trim())
    megaScaffoldBrand.value = MEGA_PASTA_POR_DEPT[department.value] ?? ''
  if (!megaScaffoldText.value.trim())
    megaScaffoldText.value = suggestMegaFolders().join('\n')
  showMegaScaffold.value = true
}

async function megaScaffoldCreate() {
  const brand = megaScaffoldBrand.value.trim()
  const names = megaScaffoldText.value.split('\n').map(s => s.trim()).filter(Boolean)
  if (!brand || !names.length || megaScaffoldBusy.value) return
  megaScaffoldBusy.value = true
  try {
    const rep = await api<any>('/api/pricing/mega/scaffold', {
      method: 'POST',
      body: { brand, names },
    })
    const lines = [
      `${rep.folders_created} pasta(s) em ${rep.brand_path}`,
      `${rep.applied} produto(s) receberam o link`,
    ]
    if (rep.unmatched_products?.length)
      lines.push(`${rep.unmatched_products.length} produto(s) continuam sem pasta`)
    if (rep.errors?.length) {
      toast.warning('Estrutura criada com avisos', [...lines, ...rep.errors.slice(0, 3)])
    } else {
      toast.success('Estrutura criada no MEGA', lines)
    }
    showMegaScaffold.value = false
    await loadProducts()
    await megaDryRun()
  } catch (e: any) {
    toast.error('Criar pastas no MEGA', [megaErrMsg(e)])
  } finally {
    megaScaffoldBusy.value = false
  }
}

function pickFotosUpload(p: PricingProduct) {
  fotosUploadTarget = p
  fotosFileInput.value?.click()
}

async function onFotosPicked(ev: Event) {
  const input = ev.target as HTMLInputElement
  const files = input.files
  const p = fotosUploadTarget
  if (!files?.length || !p) {
    if (input) input.value = ''
    return
  }
  uploadingFotosId.value = p.id
  try {
    const fd = new FormData()
    for (const f of Array.from(files)) fd.append('files', f)
    const res = await api<any>(
      `/api/pricing/mega/products/${p.id}/fotos/upload`,
      { method: 'POST', body: fd },
    )
    p.fotos_url = res.fotos_url
    const lines = [`${res.uploaded} arquivo(s) → ${p.name}`]
    if (typeof res.fotos_count === 'number') {
      p.fotos_count = res.fotos_count
      p.videos_count = res.videos_count ?? 0
      lines.push(
        `pasta agora tem ${res.fotos_count} foto(s) e ${res.videos_count ?? 0} vídeo(s)`,
      )
    }
    toast.success('Enviado pro MEGA', lines)
    await loadProducts()
  } catch (e: any) {
    toast.error('Upload de fotos/vídeos', [megaErrMsg(e)])
  } finally {
    uploadingFotosId.value = null
    fotosUploadTarget = null
    input.value = ''
  }
}

// =========================================================== product inline edit

function startEditProduct(p: PricingProduct, field: string) {
  if (!canEditProdutos.value) return
  // Flush any pending edit BEFORE overwriting editing.value. commitEditProduct
  // is now synchronous (PATCH dispatched fire-and-forget inside) so this
  // call cannot race with the new edit we're about to start below.
  if (
    editing.value &&
    (editing.value.id !== p.id || editing.value.field !== field)
  ) {
    commitEditProduct()
  }
  editing.value = { id: p.id, field }
  const raw = (p as any)[field]
  editValue.value = raw == null ? '' : String(raw)
  focusEditInput()
}

// SSH parity: commit is SYNCHRONOUS. Captures editing.value + editValue.value
// the moment blur/enter fires and dispatches the PATCH fire-and-forget. See
// `commitEditAccount` for the rationale (no async = no window for other
// microtasks to rewrite the refs between read and clear).
function commitEditProduct(): void {
  const snap = editing.value
  if (!snap) return
  const { id, field } = snap
  const raw = editValue.value.trim()
  editing.value = null
  editValue.value = ''
  void _patchProduct(id, field, raw)
}

async function _patchProduct(id: string, field: string, raw: string) {
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
      const n = parseDec(raw)
      if (Number.isNaN(n)) return
      payload[field] = n.toFixed(2)
    }
  } else if (field === 'description' || field === 'model' || field === 'ean') {
    payload[field] = raw || null
  } else if (field === 'fotos_url') {
    // Normaliza links colados sem protocolo (ex.: "mega.nz/folder/...").
    payload[field] = raw ? (/^https?:\/\//i.test(raw) ? raw : `https://${raw}`) : null
  } else {
    return
  }

  // Optimistic update: paint the new value immediately, both on the
  // products list and the grid mirror, so the user sees the change without
  // waiting on the network. Failures revert via loadGrid below.
  Object.assign(p, payload)
  const gp0 = grid.value?.products.find((x) => x.id === id)
  if (gp0 && gp0 !== p) Object.assign(gp0, payload)
  if (field.startsWith('cost_kit')) recomputeCellsForProduct(id)

  try {
    const updated = await api<PricingProduct>(`/api/pricing/products/${id}`, {
      method: 'PATCH',
      body: payload,
    })
    Object.assign(p, updated)
    const gp = grid.value?.products.find((x) => x.id === id)
    if (gp && gp !== p) Object.assign(gp, updated)
    flash(id, field)
    // SSH parity: do NOT reload the whole grid after a cost edit. The
    // server response already carries the canonical row, and recomputing
    // cells in-memory from the new cost is enough to repaint dependent
    // prices. A full loadGrid() here used to occasionally overwrite the
    // freshly-saved value with stale data, producing the "I typed 20 and
    // it snapped back to 4000" bug.
    if (field.startsWith('cost_kit')) recomputeCellsForProduct(id)
  } catch (e: any) {
    productsErr.value = e?.data?.detail?.code ?? 'save_failed'
    // Revert the optimistic update on failure by re-fetching.
    if (grid.value) await loadGrid()
    else await loadProducts()
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
  cost_kit5: '',
  cost_kit6: '',
  cost_kit7: '',
  cost_kit8: '',
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
  newProd.cost_kit5 = ''
  newProd.cost_kit6 = ''
  newProd.cost_kit7 = ''
  newProd.cost_kit8 = ''
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
    // parseDec: aceita vírgula ("674,50"). Valor ilegível aborta com
    // mensagem clara em vez de mandar "NaN" pro backend (422).
    const k1 = parseDec(newProd.cost_kit1 || 0)
    if (Number.isNaN(k1)) {
      productsErr.value = `Kit 1: preço inválido ("${newProd.cost_kit1}") — use só números, ex.: 674,50`
      return
    }
    const body: Record<string, unknown> = {
      sku: newProd.sku.trim(),
      name: newProd.name.trim(),
      department: department.value,
      product_type: newProd.product_type || 2,
      cost_kit1: k1.toFixed(2),
    }
    for (let k = 2; k <= 8; k++) {
      const v = (newProd as any)[`cost_kit${k}`]
      if (!v) continue
      const n = parseDec(v)
      if (Number.isNaN(n)) {
        productsErr.value = `Kit ${k}: preço inválido ("${v}") — use só números, ex.: 674,50`
        return
      }
      body[`cost_kit${k}`] = n.toFixed(2)
    }
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

type CellColor = 'red' | 'orange' | 'yellow' | 'green' | 'blue' | 'purple' | 'pink' | 'gray'
type GridCell = {
  pricing_account_id: string
  pricing_product_id: string
  price: string | number | null
  source: string
  cell_status: 'auto' | 'manual' | 'locked' | 'disabled' | 'NA' | 'SV' | 'error' | 'no_link'
  has_override: boolean
  cell_color: CellColor | null
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
// SSH-style per-cell feedback while pushItemsBatch walks the queue.
// `pushStates` is keyed by `${productId}:${accountId}` and ticks through
// pushing → success/error/no_link. `bulkPushProgress` shows "3/173" while
// the loop runs. Success states fade out 5s after the run finishes; errors
// and no_link stick around so the user can review them.
type PushCellState = 'pushing' | 'success' | 'error' | 'no_link'
const pushStates = ref<Map<string, PushCellState>>(new Map())
const bulkPushProgress = ref<string>('')

function pushCellKey(productId: string, accountId: string): string {
  return `${productId}:${accountId}`
}

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

// ---------- Feature 7b: grid platform filter
// Empty string = show all marketplaces. Selecting one hides the other
// platforms' columns (and their grouped headers). Reset to '' whenever
// the user switches department — see the watch(department) below.
const gridPlatformFilter = ref<string>('')

// ---------- Feature 8: push dropdown
const showPushMenu = ref(false)
const pushLabel = ref('')

// ---------- Feature 11: keyboard nav
const selectedCell = ref<{ row: number; col: number } | null>(null)

// ---------- Excel-style per-cell highlight color
// 8 swatches + a clear button. Backend whitelist mirrors this in
// app/routers/pricing.py:_ALLOWED_CELL_COLORS — keep in sync. Tailwind
// classes hardcoded so the JIT compiler picks them up; can't be built
// dynamically from the color value.
const CELL_COLORS: ReadonlyArray<{ value: CellColor; label: string; swatch: string; ring: string }> = [
  { value: 'red',    label: 'Vermelho', swatch: 'bg-red-200',    ring: 'ring-red-500' },
  { value: 'orange', label: 'Laranja',  swatch: 'bg-orange-200', ring: 'ring-orange-500' },
  { value: 'yellow', label: 'Amarelo',  swatch: 'bg-yellow-200', ring: 'ring-yellow-500' },
  { value: 'green',  label: 'Verde',    swatch: 'bg-green-200',  ring: 'ring-green-500' },
  { value: 'blue',   label: 'Azul',     swatch: 'bg-blue-200',   ring: 'ring-blue-500' },
  { value: 'purple', label: 'Roxo',     swatch: 'bg-purple-200', ring: 'ring-purple-500' },
  { value: 'pink',   label: 'Rosa',     swatch: 'bg-pink-200',   ring: 'ring-pink-500' },
  { value: 'gray',   label: 'Cinza',    swatch: 'bg-gray-300',   ring: 'ring-gray-500' },
] as const

const selectedCellColor = computed<CellColor | null>(() => {
  if (!selectedCell.value) return null
  const { row, col } = selectedCell.value
  const prod = filteredGridProducts.value[row]
  const acc = gridAccounts.value[col]
  if (!prod || !acc) return null
  return cellOf(prod.id, acc.id)?.cell_color ?? null
})

async function setCellColor(color: CellColor | null) {
  if (!selectedCell.value) return
  const { row, col } = selectedCell.value
  const prod = filteredGridProducts.value[row]
  const acc = gridAccounts.value[col]
  if (!prod || !acc || !grid.value) return

  // Optimistic local update — find or insert the cell row, then PUT.
  let cell = cellOf(prod.id, acc.id)
  const prevColor = cell?.cell_color ?? null
  if (cell) {
    cell.cell_color = color
  } else {
    cell = {
      pricing_account_id: acc.id,
      pricing_product_id: prod.id,
      price: null,
      source: 'computed',
      cell_status: 'auto',
      has_override: false,
      cell_color: color,
    }
    grid.value.cells.push(cell)
  }

  try {
    await api('/api/pricing/overrides/cell-color', {
      method: 'PUT',
      body: {
        pricing_product_id: prod.id,
        pricing_account_id: acc.id,
        cell_color: color,
      },
    })
  } catch (e) {
    // Revert on failure.
    cell.cell_color = prevColor
    console.error('cell_color save failed', e)
  }
}

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
    const typed = parseDec(editValue.value)
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
  // Critical states keep priority over any manual highlight — NA/SV/error
  // /no_link are signals the operator actually needs to see, not paint over.
  if (c.cell_status === 'NA') return 'bg-gray-200 text-gray-500 font-semibold'
  if (c.cell_status === 'SV') return 'bg-amber-100 text-amber-700 font-semibold'
  // Transient post-push states. SSH paints these so the user can decide whether
  // to mark NA/SV permanently or fix the underlying issue and retry.
  if (c.cell_status === 'error') return 'bg-red-50 text-red-700'
  if (c.cell_status === 'no_link') return 'bg-amber-50 text-amber-700'
  // Operator-picked highlight beats the automatic palette. Hardcoded
  // Tailwind class names so the JIT bundles them; keep in sync with
  // CELL_COLORS above.
  if (c.cell_color) {
    switch (c.cell_color) {
      case 'red':    return 'bg-red-200 text-red-900'
      case 'orange': return 'bg-orange-200 text-orange-900'
      case 'yellow': return 'bg-yellow-200 text-yellow-900'
      case 'green':  return 'bg-green-200 text-green-900'
      case 'blue':   return 'bg-blue-200 text-blue-900'
      case 'purple': return 'bg-purple-200 text-purple-900'
      case 'pink':   return 'bg-pink-200 text-pink-900'
      case 'gray':   return 'bg-gray-300 text-gray-800'
    }
  }
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
          price_override: parseDec(val),
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

// SSH-style cell-by-cell push: walk the items sequentially from the browser,
// one POST /api/pricing/push per cell, and update per-cell visual state in
// `pushStates` so the user sees each spinner → ✓/✗ as it happens. Callers
// must build `items` in the visual order they want (e.g. produto×conta in
// gridAccounts order) — pushItemsBatch groups by product only to compute
// the "N/total" label.
async function pushItemsBatch(
  items: { pricing_account_id: string; pricing_product_id: string }[],
  keyHint: string,
) {
  if (!items.length) {
    gridErr.value = 'no_eligible_cells'
    return
  }
  pushing.value = true
  lastPushResults.value = []
  pushStates.value = new Map()
  bulkPushProgress.value = ''
  const baseKey = `${keyHint}:${Date.now()}`

  const productIds = [...new Set(items.map((it) => it.pricing_product_id))]
  const results: PushResult[] = []
  let sent = 0
  let errors = 0
  let noLinks = 0
  let skipped = 0
  const errorDetails: string[] = []

  function setCell(ck: string, st: PushCellState) {
    pushStates.value = new Map(pushStates.value).set(ck, st)
  }

  try {
    for (let pIdx = 0; pIdx < productIds.length; pIdx++) {
      const prodId = productIds[pIdx]
      bulkPushProgress.value = `${pIdx + 1}/${productIds.length}`
      const prodItems = items.filter((it) => it.pricing_product_id === prodId)

      for (const item of prodItems) {
        const ck = pushCellKey(item.pricing_product_id, item.pricing_account_id)
        setCell(ck, 'pushing')

        try {
          const r = await api<{ results: PushResult[] }>('/api/pricing/push', {
            method: 'POST',
            headers: { 'Idempotency-Key': `${baseKey}:${ck}` },
            body: { items: [item] },
          })
          const result = r.results[0]
          if (!result) {
            setCell(ck, 'error')
            errors++
            continue
          }
          results.push(result)
          lastPushResults.value = results.slice()

          if (result.ok) {
            setCell(ck, 'success')
            sent++
          } else if (result.code === 'no_link' || result.code === 'account_not_linked') {
            setCell(ck, 'no_link')
            noLinks++
          } else if (result.code === 'all_skipped') {
            setCell(ck, 'success')
            skipped++
          } else {
            setCell(ck, 'error')
            errors++
            errorDetails.push(`${result.code}: ${result.detail || ''}`)
          }
        } catch (e: any) {
          setCell(ck, 'error')
          errors++
          errorDetails.push(e?.message || 'request_failed')
        }
      }
    }

    if (errors > 0) {
      toast.error(
        `Envio: ${sent} ok, ${errors} erro(s)${noLinks > 0 ? `, ${noLinks} sem vínculo` : ''}`,
        errorDetails.slice(0, 5),
      )
    } else if (sent === 0 && noLinks > 0) {
      toast.warning(`Nenhum preço enviado. ${noLinks} célula(s) sem vínculo.`)
    } else {
      toast.success(
        'Push concluído!',
        `${sent} preço(s) enviado(s) com sucesso${skipped > 0 ? ` (${skipped} pulado(s))` : ''}`,
      )
    }

    await loadGrid()
  } catch (e: any) {
    gridErr.value = e?.data?.detail?.code ?? 'push_failed'
    toast.error('Erro no push', gridErr.value || undefined)
  } finally {
    pushing.value = false
    bulkPushProgress.value = ''
    // Let success indicators fade after 5s; keep error/no_link so the user
    // can scan the grid for cells that still need attention.
    setTimeout(() => {
      pushStates.value = new Map(
        [...pushStates.value.entries()].filter(([, v]) => v !== 'success'),
      )
    }, 5000)
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
  const items = filteredGridProducts.value
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
  // gridAccounts.value carries the visual left-to-right order; iterating
  // products × gridAccounts means the queue walks the table the same way
  // the user reads it, so the per-cell spinners march in a predictable path.
  for (const p of filteredGridProducts.value) {
    for (const a of gridAccounts.value) {
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
// Platform order chosen to match the SSH UI (Shein sits next to Shopee):
//   amazon → magalu → mercadolivre → shopee → shein → temu → aliexpress → tiktok
const PLATFORM_ORDER: Record<string, number> = {
  amazon: 0, magalu: 1, mercadolivre: 2, shopee: 3,
  shein: 4, temu: 5, aliexpress: 6, tiktok: 7,
}

// Same SSH order is reused by the body iteration so each data column lines
// up with its grouped header — see `gridAccounts` below.
//
// Platform filter: when gridPlatformFilter is set, drop accounts whose
// platform doesn't match. The downstream consumers (accountGroups,
// pushItemsBatch's "send all visible" iteration, the cellOf lookups
// in keyboard nav) all read from this computed so they inherit the
// filter automatically.
const gridAccounts = computed<Account[]>(() => {
  let accs = (grid.value?.accounts ?? []).slice()
  if (gridPlatformFilter.value) {
    accs = accs.filter((a) => a.platform === gridPlatformFilter.value)
  }
  return accs.sort((a, b) => {
    const pa = PLATFORM_ORDER[a.platform] ?? 99
    const pb = PLATFORM_ORDER[b.platform] ?? 99
    if (pa !== pb) return pa - pb
    if (a.kit_number !== b.kit_number) return (a.kit_number || 0) - (b.kit_number || 0)
    return (a.name || '').localeCompare(b.name || '', 'pt-BR', { sensitivity: 'base' })
  })
})

// Dropdown options derived from the unfiltered grid response — only
// platforms actually present in the current department show up so the
// operator doesn't see dead-end options like "TikTok" when there are
// zero TikTok accounts in the loaded department.
const gridPlatformOptions = computed<string[]>(() => {
  const set = new Set<string>()
  for (const a of (grid.value?.accounts ?? [])) set.add(a.platform)
  return Array.from(set).sort(
    (a, b) => (PLATFORM_ORDER[a] ?? 99) - (PLATFORM_ORDER[b] ?? 99),
  )
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
  const key = `cost_kit${Math.max(1, Math.min(8, kitNumber || 1))}` as keyof PricingProduct
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
    case 'shein': return 'bg-teal-50/50'
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
    case 'shein':        return 'bg-teal-50 dark:bg-teal-900/30'
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
          price_override: parseDec(val),
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
  if (department.value === 'celular') {
    for (let k = 2; k <= 8; k++) headers.push(`Kit${k}`)
  }
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
      for (let k = 2; k <= 8; k++) {
        row.push(String(Number((p as any)[`cost_kit${k}`] || 0).toFixed(0)))
      }
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
  a.download = `tabela-precos-${department.value}-${isoToday()}.csv`
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
  // Switching department invalidates the platform filter — the new
  // dept may not have the same platforms, and stale filters silently
  // hide all columns until the operator notices.
  gridPlatformFilter.value = ''
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
              <th
                v-for="f in STORE_NOTE_FIELDS"
                :key="`h-${f.key}`"
                class="text-left px-2 py-2 font-medium border-b border-border min-w-[120px] capitalize"
              >{{ f.label }}</th>
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
                  type="text" inputmode="numeric"
                  class="w-full text-xs border rounded px-1 py-1 bg-background text-center"
                />
              </td>
              <td class="border border-border px-1 py-1">
                <input
                  v-model="newAcc.commission"
                  type="text" inputmode="decimal"
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
                  type="text" inputmode="numeric"
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
                  type="text" inputmode="decimal"
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
                    type="text" inputmode="decimal"
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
                    type="text" inputmode="numeric"
                    class="w-full text-xs bg-transparent outline-none text-center"
                    @blur="commitEditAccount"
                    @keydown.enter.prevent="commitEditAccount"
                    @keydown.escape.prevent="cancelEdit"
                  />
                  <span v-else>{{ fmtShipping((acc as any)[`shipping${t}`]) }}</span>
                </td>
              </template>
              <!-- desconto / afiliado / ads / cupom / obs -->
              <template v-for="f in STORE_NOTE_FIELDS" :key="`c-${f.key}`">
                <td
                  class="border border-border px-2 py-1.5 text-xs cursor-pointer text-left max-w-[260px]"
                  :class="{
                    'ring-2 ring-blue-500 ring-inset bg-background': isEditing(acc.id, f.key),
                    'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(acc.id, f.key),
                  }"
                  @click="!isEditing(acc.id, f.key) && startEditAccount(acc, f.key)"
                >
                  <input
                    v-if="isEditing(acc.id, f.key)"
                    :ref="setEditInputRef"
                    v-model="editValue"
                    type="text"
                    class="w-full text-xs bg-transparent outline-none"
                    @blur="commitEditAccount"
                    @keydown.enter.prevent="commitEditAccount"
                    @keydown.escape.prevent="cancelEdit"
                  />
                  <span v-else :class="{ 'text-muted-foreground': !((acc as any)[f.key]) }">
                    {{ (acc as any)[f.key] || '—' }}
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
            <button
              v-if="canRefreshStock"
              class="btn btn-sm"
              :disabled="stockRefreshing"
              title="Puxa o estoque atual do Bling e regrava — corrige números defasados (ex. kit com componente zerado)"
              @click="refreshBlingStock"
            >
              <RefreshCw class="h-3.5 w-3.5 mr-1" :class="{ 'animate-spin': stockRefreshing }" /> Atualizar estoque do Bling
            </button>
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
                      <span v-if="row.divergent_departments?.length" class="text-orange-500">
                        ({{ row.divergent_departments.join(', ') }})
                      </span>
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
          <button
            v-if="canEditProdutos"
            class="btn btn-sm"
            :disabled="megaBusy"
            title="Conectar a conta MEGA e preencher os links de fotos automaticamente pelo nome"
            @click="megaSyncClick"
          >
            <Loader2 v-if="megaBusy" class="h-4 w-4 mr-1 animate-spin" />
            <Camera v-else class="h-4 w-4 mr-1" /> Fotos (MEGA)
          </button>
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
              <th v-for="k in kitCount" :key="`kith-${k}`" class="text-right px-2 py-2 font-medium border-b border-border w-24" :title="kitTitulo(k)">
                Kit {{ k }}
                <span v-if="kitNome(k)" class="block text-[9px] font-normal text-muted-foreground leading-tight whitespace-normal">{{ kitNome(k) }}</span>
              </th>
              <th class="text-center px-2 py-2 font-medium border-b border-border w-14">Fotos</th>
              <th class="text-center px-2 py-2 font-medium border-b border-border w-24">Tabela</th>
              <th class="text-center px-2 py-2 font-medium border-b border-border w-16">Catálogo</th>
              <th class="text-center px-2 py-2 font-medium border-b border-border w-12"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="productsLoading && !products.length">
              <td colSpan="16" class="text-center py-6 text-muted-foreground">
                <Loader2 class="inline h-4 w-4 animate-spin" /> carregando…
              </td>
            </tr>
            <tr v-else-if="!productsCurrent.length && !showAddProd">
              <td colSpan="16" class="text-center py-6 text-muted-foreground">
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
              <td v-for="k in kitCount" :key="`newkit-${k}`" class="border border-border px-1 py-1">
                <input v-model="(newProd as any)[`cost_kit${k}`]" type="text" inputmode="decimal" :placeholder="k === 1 ? '0.00' : ''" class="w-full text-xs border rounded px-1.5 py-1 bg-background text-right" />
              </td>
              <!-- Fotos: link é adicionado depois, editando a linha criada. -->
              <td class="border border-border px-1 py-1 text-center text-xs text-muted-foreground">—</td>
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
                class="border border-border px-2 py-1.5 text-xs font-mono cursor-pointer max-w-[220px]"
                :class="{
                  'ring-2 ring-blue-500 ring-inset bg-background': isEditing(p.id, 'sku'),
                  'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(p.id, 'sku'),
                }"
                :title="p.sku"
                @click="!isEditing(p.id, 'sku') && startEditProduct(p, 'sku')"
              >
                <input
                  v-if="isEditing(p.id, 'sku')"
                  :ref="setEditInputRef"
                  v-model="editValue" type="text"
                  class="w-full text-xs bg-transparent outline-none font-mono"
                  @blur="commitEditProduct" @keydown.enter.prevent="commitEditProduct" @keydown.escape.prevent="cancelEdit"
                />
                <span v-else class="block truncate">{{ p.sku.length > 20 ? p.sku.slice(0, 20) + '…' : p.sku }}</span>
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
                v-for="k in kitCount" :key="`kit-${k}`"
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
                  v-model="editValue" type="text" inputmode="decimal"
                  class="w-full text-xs bg-transparent outline-none text-right"
                  @blur="commitEditProduct" @keydown.enter.prevent="commitEditProduct" @keydown.escape.prevent="cancelEdit"
                />
                <span v-else>{{ fmtBRL((p as any)[`cost_kit${k}`]) }}</span>
              </td>
              <!-- Fotos: link da pasta (MEGA) com as fotos de todas as cores.
                   Câmera azul abre o link; cinza = sem link (clique pra colar). -->
              <td
                class="border border-border px-1 py-1.5 text-center"
                :class="{
                  'ring-2 ring-blue-500 ring-inset bg-background': isEditing(p.id, 'fotos_url'),
                  'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(p.id, 'fotos_url'),
                }"
              >
                <Loader2
                  v-if="uploadingFotosId === p.id"
                  class="h-3.5 w-3.5 animate-spin text-blue-600 inline"
                />
                <input
                  v-else-if="isEditing(p.id, 'fotos_url')"
                  :ref="setEditInputRef"
                  v-model="editValue" type="text"
                  placeholder="cole o link das fotos (MEGA)…"
                  class="w-56 text-xs bg-transparent outline-none"
                  @blur="commitEditProduct" @keydown.enter.prevent="commitEditProduct" @keydown.escape.prevent="cancelEdit"
                />
                <span v-else-if="p.fotos_url" class="inline-flex items-center gap-0.5">
                  <a
                    :href="p.fotos_url" target="_blank" rel="noopener"
                    class="p-1 text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded"
                    :title="`Abrir fotos — ${p.fotos_url}`"
                  ><Camera class="h-3.5 w-3.5" /></a>
                  <button
                    v-if="canEditProdutos"
                    class="p-0.5 text-muted-foreground hover:text-foreground rounded"
                    title="Editar link das fotos"
                    @click="startEditProduct(p, 'fotos_url')"
                  ><Pencil class="h-2.5 w-2.5" /></button>
                  <button
                    v-if="canEditProdutos"
                    class="p-0.5 text-muted-foreground hover:text-foreground rounded"
                    title="Enviar mais fotos/vídeos pra pasta deste produto no MEGA"
                    @click="pickFotosUpload(p)"
                  ><Upload class="h-2.5 w-2.5" /></button>
                  <span
                    v-if="typeof p.fotos_count === 'number'"
                    class="ml-1 inline-flex items-center gap-0.5 text-[10px] tabular-nums text-muted-foreground whitespace-nowrap"
                    :title="`${p.fotos_count} foto(s) e ${p.videos_count ?? 0} vídeo(s) na pasta — recontagem pelo botão Fotos (MEGA)`"
                  >
                    <ImageIcon class="h-2.5 w-2.5" />{{ p.fotos_count }}
                    <Film class="h-2.5 w-2.5 ml-0.5" />{{ p.videos_count ?? 0 }}
                  </span>
                </span>
                <span v-else-if="canEditProdutos" class="inline-flex items-center gap-0.5">
                  <button
                    class="p-1 text-muted-foreground/50 hover:text-blue-600 rounded"
                    title="Colar link das fotos (pasta do MEGA com todas as cores)"
                    @click="startEditProduct(p, 'fotos_url')"
                  ><Camera class="h-3.5 w-3.5" /></button>
                  <button
                    class="p-0.5 text-muted-foreground/50 hover:text-foreground rounded"
                    title="Enviar fotos pro MEGA (cria a pasta do produto e salva o link)"
                    @click="pickFotosUpload(p)"
                  ><Upload class="h-2.5 w-2.5" /></button>
                </span>
                <span v-else class="text-xs text-muted-foreground">—</span>
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

      <!-- MEGA: input de upload compartilhado (alvo = fotosUploadTarget) -->
      <input
        ref="fotosFileInput"
        type="file" multiple accept="image/*,video/*"
        class="hidden"
        @change="onFotosPicked"
      />

      <!-- MEGA: modal de login -->
      <div
        v-if="showMegaLogin"
        class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
        @click.self="showMegaLogin = false"
      >
        <div class="bg-background border rounded-lg shadow-xl w-full max-w-sm p-4 space-y-3">
          <h3 class="text-sm font-semibold flex items-center gap-2">
            <Camera class="h-4 w-4" /> Conectar conta MEGA
          </h3>
          <p class="text-xs text-muted-foreground">
            Login feito uma única vez — a sessão fica salva no servidor.
            A senha não é armazenada.
          </p>
          <input
            v-model="megaLoginForm.email"
            type="email" placeholder="email do MEGA"
            class="w-full border rounded px-2 py-1.5 text-sm bg-background"
          />
          <input
            v-model="megaLoginForm.password"
            type="password" placeholder="senha do MEGA"
            class="w-full border rounded px-2 py-1.5 text-sm bg-background"
            @keydown.enter="submitMegaLogin"
          />
          <input
            v-model="megaLoginForm.code"
            type="text" placeholder="código 2FA (só se a conta tiver)"
            class="w-full border rounded px-2 py-1.5 text-sm bg-background"
            @keydown.enter="submitMegaLogin"
          />
          <p v-if="megaLoginErr" class="text-xs text-destructive">{{ megaLoginErr }}</p>
          <div class="flex justify-end gap-2">
            <button class="btn btn-sm" @click="showMegaLogin = false">Cancelar</button>
            <button
              class="btn btn-sm btn-primary"
              :disabled="megaLoginBusy || !megaLoginForm.email || !megaLoginForm.password"
              @click="submitMegaLogin"
            >
              <Loader2 v-if="megaLoginBusy" class="h-4 w-4 mr-1 animate-spin" /> Conectar
            </button>
          </div>
        </div>
      </div>

      <!-- MEGA: modal de prévia da sincronização -->
      <div
        v-if="showMegaPreview && megaPreview"
        class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
        @click.self="showMegaPreview = false"
      >
        <div class="bg-background border rounded-lg shadow-xl w-full max-w-2xl p-4 space-y-3 max-h-[85vh] overflow-auto">
          <h3 class="text-sm font-semibold flex items-center gap-2">
            <Camera class="h-4 w-4" /> Fotos do MEGA — prévia da sincronização
          </h3>
          <p class="text-xs text-muted-foreground">
            {{ megaPreview.folders_total }} pasta(s) no MEGA ·
            {{ megaPreview.matched_total }} produto(s) casaram pelo nome ·
            <b class="text-foreground">{{ megaPreview.to_apply }}</b> vão receber link agora
            (quem já tem link não é alterado)
          </p>
          <p v-if="!megaPreview.folders_total && canEditProdutos" class="text-xs text-amber-700">
            A conta MEGA ainda está sem pastas — use "Criar pastas no MEGA" pra montar a
            estrutura (marca → uma pasta por modelo) a partir dos seus produtos.
          </p>
          <div v-if="megaPreview.to_apply" class="border rounded max-h-56 overflow-auto">
            <table class="w-full text-xs">
              <tbody>
                <tr
                  v-for="m in megaPreview.matched.filter(x => !x.has_url)"
                  :key="m.sku"
                  class="border-b border-border/50"
                >
                  <td class="px-2 py-1 font-mono whitespace-nowrap">{{ m.sku }}</td>
                  <td class="px-2 py-1">{{ m.name }}</td>
                  <td class="px-2 py-1 text-muted-foreground">📁 {{ m.folder }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <details v-if="megaPreview.ambiguous?.length" class="text-xs">
            <summary class="cursor-pointer text-amber-700">
              {{ megaPreview.ambiguous.length }} produto(s) com mais de uma pasta possível
              (resolver colando o link à mão)
            </summary>
            <ul class="mt-1 pl-4 list-disc space-y-0.5">
              <li v-for="a in megaPreview.ambiguous" :key="a.sku">
                {{ a.name }} → {{ a.candidates.join(' | ') }}
              </li>
            </ul>
          </details>
          <details v-if="megaPreview.unmatched_products?.length" class="text-xs">
            <summary class="cursor-pointer text-muted-foreground">
              {{ megaPreview.unmatched_products.length }} produto(s) sem pasta no MEGA
            </summary>
            <ul class="mt-1 pl-4 list-disc space-y-0.5">
              <li v-for="u in megaPreview.unmatched_products" :key="u.sku">{{ u.name }}</li>
            </ul>
          </details>
          <details v-if="megaPreview.unmatched_folders?.length" class="text-xs">
            <summary class="cursor-pointer text-muted-foreground">
              {{ megaPreview.unmatched_folders.length }} pasta(s) do MEGA sem produto
            </summary>
            <ul class="mt-1 pl-4 list-disc space-y-0.5">
              <li v-for="(f, i) in megaPreview.unmatched_folders" :key="i">{{ f }}</li>
            </ul>
          </details>
          <div class="flex items-center justify-between gap-2 pt-1">
            <div v-if="canEditProdutos" class="flex gap-2">
              <button class="btn btn-sm" @click="openMegaScaffold">
                <FolderPlus class="h-4 w-4 mr-1" /> Criar pastas no MEGA…
              </button>
              <button
                class="btn btn-sm"
                :disabled="megaCountsBusy"
                title="Reconta as fotos e vídeos de cada pasta do MEGA e mostra os números na coluna Fotos"
                @click="megaCountsRefresh()"
              >
                <Loader2 v-if="megaCountsBusy" class="h-4 w-4 mr-1 animate-spin" />
                <RefreshCw v-else class="h-4 w-4 mr-1" />
                Contar fotos/vídeos
              </button>
            </div>
            <span v-else></span>
            <div class="flex gap-2">
              <button class="btn btn-sm" @click="showMegaPreview = false">Fechar</button>
              <button
                class="btn btn-sm btn-primary"
                :disabled="megaApplyBusy || !megaPreview.to_apply"
                @click="megaApply"
              >
                <Loader2 v-if="megaApplyBusy" class="h-4 w-4 mr-1 animate-spin" />
                Aplicar {{ megaPreview.to_apply }} link(s)
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- MEGA: modal de criação da estrutura de pastas (marca/modelo) -->
      <div
        v-if="showMegaScaffold"
        class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
        @click.self="showMegaScaffold = false"
      >
        <div class="bg-background border rounded-lg shadow-xl w-full max-w-lg p-4 space-y-3 max-h-[85vh] overflow-auto">
          <h3 class="text-sm font-semibold flex items-center gap-2">
            <FolderPlus class="h-4 w-4" /> Criar pastas no MEGA
          </h3>
          <p class="text-xs text-muted-foreground">
            Cria a pasta do departamento com uma subpasta por modelo
            (ex.: /Malas/M2 listrada, /Celular/Fossibot S7) e já preenche o
            link de fotos de todos os produtos daquele modelo. As fotos e
            vídeos podem ser adicionados depois — o link continua o mesmo.
          </p>
          <label class="block text-xs">
            <span class="text-muted-foreground">Pasta principal (departamento)</span>
            <input
              v-model="megaScaffoldBrand"
              placeholder="Celular"
              class="mt-1 w-full border rounded px-2 py-1.5 text-sm bg-background"
            />
          </label>
          <label class="block text-xs">
            <span class="text-muted-foreground">
              Modelos — uma pasta por linha ({{ megaScaffoldCount }}); edite à vontade
            </span>
            <textarea
              v-model="megaScaffoldText"
              rows="12"
              class="mt-1 w-full border rounded px-2 py-1.5 text-xs font-mono bg-background"
            ></textarea>
          </label>
          <div class="flex justify-end gap-2 pt-1">
            <button class="btn btn-sm" @click="showMegaScaffold = false">Cancelar</button>
            <button
              class="btn btn-sm btn-primary"
              :disabled="megaScaffoldBusy || !megaScaffoldCount || !megaScaffoldBrand.trim()"
              @click="megaScaffoldCreate"
            >
              <Loader2 v-if="megaScaffoldBusy" class="h-4 w-4 mr-1 animate-spin" />
              Criar {{ megaScaffoldCount }} pasta(s)
            </button>
          </div>
        </div>
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
        <!-- Marketplace filter: hides every column except the selected
             platform's. Empty string = todos. Options list reflects
             only platforms actually present in the loaded department. -->
        <select
          v-model="gridPlatformFilter"
          class="border rounded px-2 py-1 text-sm bg-background min-w-[140px]"
          title="Filtrar colunas por marketplace"
        >
          <option value="">Todos marketplaces</option>
          <option v-for="p in gridPlatformOptions" :key="p" :value="p">
            {{ platformLabel(p) }}
          </option>
        </select>
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
          <Loader2 class="h-3 w-3 animate-spin" />
          {{ bulkPushProgress ? `enviando ${bulkPushProgress}…` : 'enviando…' }}
        </span>
        <!-- Excel-style cell color picker — only shows once a cell is selected.
             Selected swatch gets a ring matching its color. Trailing X clears. -->
        <div v-if="selectedCell" class="flex items-center gap-1 border-l pl-2 ml-1">
          <span class="text-xs text-muted-foreground mr-1">Cor:</span>
          <button
            v-for="color in CELL_COLORS"
            :key="color.value"
            type="button"
            class="w-5 h-5 rounded-sm border border-gray-300 transition-transform hover:scale-110"
            :class="[
              color.swatch,
              selectedCellColor === color.value ? `ring-2 ${color.ring} scale-110` : '',
            ]"
            :title="color.label"
            @click="setCellColor(color.value)"
          />
          <button
            type="button"
            class="w-5 h-5 rounded-sm border border-gray-300 flex items-center justify-center hover:bg-muted transition-colors"
            :class="selectedCellColor === null ? 'ring-2 ring-gray-400' : ''"
            title="Remover cor"
            @click="setCellColor(null)"
          >
            <X class="h-3 w-3 text-muted-foreground" />
          </button>
        </div>
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
                :colspan="department === 'celular' ? 4 + kitCount : 5"
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
                :colspan="department === 'celular' ? 4 + kitCount : 5"
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
                <template v-for="f in STORE_NOTE_FIELDS" :key="f.key">
                  <div v-if="editingObsId === `${acc.id}-${f.key}`">
                    <input
                      v-model="obsValue"
                      class="w-full text-[9px] border rounded px-1 py-0.5 bg-background"
                      @blur="commitObs(acc.id, f.key)"
                      @keydown.enter="commitObs(acc.id, f.key)"
                      @keydown.escape="editingObsId = null"
                    />
                  </div>
                  <div
                    v-else
                    class="text-[9px] cursor-pointer truncate leading-tight"
                    :class="(acc as any)[f.key] ? 'text-amber-700 font-medium' : 'text-muted-foreground/60 italic'"
                    :title="(acc as any)[f.key] ? `${f.label}: ${(acc as any)[f.key]}` : f.label"
                    @click="startEditObs(acc.id, f.key, (acc as any)[f.key])"
                  >
                    {{ (acc as any)[f.key] ? `${f.label}: ${(acc as any)[f.key]}` : f.label }}
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
              <!-- Celular mostra os 8 kits (pedido do Eduardo 2026-08-24;
                   antes parava no kit 4). Offsets sticky calculados. -->
              <template v-if="department === 'celular'">
                <th
                  v-for="k in kitCount" :key="`gridkith-${k}`"
                  class="sticky bg-background px-1 py-1 text-center text-[10px] text-muted-foreground z-30 min-w-[56px]"
                  :style="{ left: `${368 + (k - 1) * 56}px` }"
                  :title="kitTitulo(k)"
                >
                  kit {{ k }}
                </th>
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
                  v-for="k in kitCount" :key="`gridkit-${k}`"
                  class="sticky bg-background px-1 py-1 text-center text-xs z-10 min-w-[56px] cursor-pointer"
                  :class="[
                    'text-muted-foreground',
                    isEditing(prod.id, `cost_kit${k}`) ? 'ring-2 ring-blue-500 ring-inset' : '',
                    isFlashed(prod.id, `cost_kit${k}`) ? 'bg-emerald-50 dark:bg-emerald-900/20' : '',
                  ]"
                  :style="{ left: `${368 + (k - 1) * 56}px` }"
                  @click="canEditProdutos && !isEditing(prod.id, `cost_kit${k}`) && startEditProduct(prod as any, `cost_kit${k}`)"
                >
                  <input
                    v-if="isEditing(prod.id, `cost_kit${k}`)"
                    :ref="setEditInputRef"
                    v-model="editValue" type="text" inputmode="decimal"
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
                    v-model="editValue" type="text" inputmode="decimal"
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
                class="px-1 py-1 relative"
                :class="[
                  cellTone(cellOf(prod.id, acc.id)),
                  platformBg(acc.platform),
                  'border-l',
                  selectedCell?.row === rowIdx && selectedCell?.col === accIdx ? 'ring-2 ring-blue-500 ring-inset' : '',
                ]"
                @click="selectedCell = { row: rowIdx, col: accIdx }"
              >
                <!-- Per-cell push status (SSH-style overlay) -->
                <div
                  v-if="pushStates.get(pushCellKey(prod.id, acc.id))"
                  class="absolute inset-0 flex items-center justify-center z-10 pointer-events-none"
                  :class="{
                    'bg-blue-100/80': pushStates.get(pushCellKey(prod.id, acc.id)) === 'pushing',
                    'bg-emerald-100/80': pushStates.get(pushCellKey(prod.id, acc.id)) === 'success',
                    'bg-red-100/80': pushStates.get(pushCellKey(prod.id, acc.id)) === 'error',
                    'bg-gray-200/80': pushStates.get(pushCellKey(prod.id, acc.id)) === 'no_link',
                  }"
                >
                  <Loader2 v-if="pushStates.get(pushCellKey(prod.id, acc.id)) === 'pushing'" class="h-3 w-3 animate-spin text-blue-600" />
                  <Check v-else-if="pushStates.get(pushCellKey(prod.id, acc.id)) === 'success'" class="h-3 w-3 text-emerald-600" />
                  <X v-else-if="pushStates.get(pushCellKey(prod.id, acc.id)) === 'error'" class="h-3 w-3 text-red-600" />
                  <Minus v-else class="h-3 w-3 text-gray-500" />
                </div>
                <template v-if="editingCell && editingCell.accId === acc.id && editingCell.prodId === prod.id">
                  <div class="flex items-center gap-1">
                    <input
                      v-model="cellEditValue"
                      type="text" inputmode="decimal"
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
