<script setup lang="ts">
import { Plus, Trash2, RefreshCw, Save, X, AlertCircle, Loader2, Eye, EyeOff, Star } from 'lucide-vue-next'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'tabela_precos', action: 'view' },
})

type Tab = 'contas' | 'produtos' | 'overrides' | 'auditoria' | 'concorrencia'

const TABS: { key: Tab; label: string }[] = [
  { key: 'contas', label: 'Contas' },
  { key: 'produtos', label: 'Produtos' },
  { key: 'overrides', label: 'Overrides' },
  { key: 'auditoria', label: 'Auditoria' },
  { key: 'concorrencia', label: 'Concorrência' },
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
  { value: 'celular', label: 'Celular' },
  { value: 'mala', label: 'Mala' },
  { value: 'eletro', label: 'Eletro' },
  { value: 'catalogo', label: 'Catálogo' },
] as const

const PLATFORMS = [
  { value: 'mercadolivre', label: 'Mercado Livre' },
  { value: 'shopee', label: 'Shopee' },
  { value: 'amazon', label: 'Amazon' },
  { value: 'temu', label: 'Temu' },
  { value: 'aliexpress', label: 'AliExpress' },
  { value: 'tiktok', label: 'TikTok' },
  { value: 'magalu', label: 'Magalu' },
] as const

// ============================================================ contas state

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
  has_password: boolean
  integration_id: string | null
  sort_order: number
  created_at: string
  updated_at: string
}

const accounts = ref<Account[]>([])
const accountsLoading = ref(false)
const accountsErr = ref<string | null>(null)
const filterDeptContas = ref<string>('')

async function loadAccounts() {
  accountsLoading.value = true
  accountsErr.value = null
  try {
    const qs = filterDeptContas.value ? `?department=${filterDeptContas.value}` : ''
    accounts.value = await api<Account[]>(`/api/pricing/accounts${qs}`)
  } catch (e: any) {
    accountsErr.value = e?.data?.detail?.code ?? 'load_failed'
  } finally {
    accountsLoading.value = false
  }
}

const showAccountForm = ref(false)
const editingAccount = ref<Account | null>(null)
const accountForm = reactive({
  name: '',
  platform: 'mercadolivre',
  department: 'celular',
  kit_number: 1,
  listing_type: '',
  commission: '',
  margin1: '',
  shipping1: '',
  email: '',
  password: '',
})

function resetAccountForm() {
  accountForm.name = ''
  accountForm.platform = 'mercadolivre'
  accountForm.department = 'celular'
  accountForm.kit_number = 1
  accountForm.listing_type = ''
  accountForm.commission = ''
  accountForm.margin1 = ''
  accountForm.shipping1 = ''
  accountForm.email = ''
  accountForm.password = ''
  editingAccount.value = null
}

function openCreateAccount() {
  resetAccountForm()
  showAccountForm.value = true
}

function openEditAccount(a: Account) {
  editingAccount.value = a
  accountForm.name = a.name
  accountForm.platform = a.platform
  accountForm.department = a.department
  accountForm.kit_number = a.kit_number
  accountForm.listing_type = a.listing_type ?? ''
  accountForm.commission = a.commission != null ? String(a.commission) : ''
  accountForm.margin1 = a.margin1 != null ? String(a.margin1) : ''
  accountForm.shipping1 = a.shipping1 != null ? String(a.shipping1) : ''
  accountForm.email = a.email ?? ''
  accountForm.password = ''
  showAccountForm.value = true
}

const submitting = ref(false)

async function submitAccount() {
  submitting.value = true
  try {
    const payload: Record<string, unknown> = {
      name: accountForm.name.trim(),
      platform: accountForm.platform,
      department: accountForm.department,
      kit_number: accountForm.kit_number,
      listing_type: accountForm.listing_type || null,
      commission: accountForm.commission === '' ? null : Number(accountForm.commission),
      margin1: accountForm.margin1 === '' ? null : Number(accountForm.margin1),
      shipping1: accountForm.shipping1 === '' ? null : Number(accountForm.shipping1),
      email: accountForm.email || null,
    }
    if (accountForm.password) payload.password = accountForm.password
    if (editingAccount.value) {
      await api<Account>(`/api/pricing/accounts/${editingAccount.value.id}`, {
        method: 'PATCH',
        body: payload,
      })
    } else {
      await api<Account>(`/api/pricing/accounts`, { method: 'POST', body: payload })
    }
    showAccountForm.value = false
    resetAccountForm()
    await loadAccounts()
  } catch (e: any) {
    accountsErr.value = e?.data?.detail?.code ?? 'save_failed'
  } finally {
    submitting.value = false
  }
}

async function deleteAccount(a: Account) {
  if (!confirm(`Excluir conta "${a.name}"?`)) return
  try {
    await api(`/api/pricing/accounts/${a.id}`, { method: 'DELETE' })
    await loadAccounts()
  } catch (e: any) {
    accountsErr.value = e?.data?.detail?.code ?? 'delete_failed'
  }
}

// ========================================================== produtos state

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
const filterDeptProdutos = ref<string>('')
const filterCatalog = ref<'all' | 'in' | 'out'>('all')
const searchProdutos = ref('')

async function loadProducts() {
  productsLoading.value = true
  productsErr.value = null
  try {
    const params = new URLSearchParams()
    if (filterDeptProdutos.value) params.set('department', filterDeptProdutos.value)
    if (filterCatalog.value === 'in') params.set('in_catalog', 'true')
    if (filterCatalog.value === 'out') params.set('in_catalog', 'false')
    const qs = params.toString() ? `?${params.toString()}` : ''
    products.value = await api<PricingProduct[]>(`/api/pricing/products${qs}`)
  } catch (e: any) {
    productsErr.value = e?.data?.detail?.code ?? 'load_failed'
  } finally {
    productsLoading.value = false
  }
}

const productsFiltered = computed(() => {
  const q = searchProdutos.value.trim().toLowerCase()
  if (!q) return products.value
  return products.value.filter(
    (p) =>
      p.sku.toLowerCase().includes(q) ||
      p.name.toLowerCase().includes(q) ||
      (p.ean ?? '').toLowerCase().includes(q),
  )
})

const showProductForm = ref(false)
const editingProduct = ref<PricingProduct | null>(null)
const productForm = reactive({
  sku: '',
  name: '',
  department: 'celular',
  product_type: 2,
  cost_kit1: '0',
  cost_kit2: '',
  cost_kit3: '',
  cost_kit4: '',
  ean: '',
  is_active: true,
  in_catalog: false,
})

function resetProductForm() {
  productForm.sku = ''
  productForm.name = ''
  productForm.department = 'celular'
  productForm.product_type = 2
  productForm.cost_kit1 = '0'
  productForm.cost_kit2 = ''
  productForm.cost_kit3 = ''
  productForm.cost_kit4 = ''
  productForm.ean = ''
  productForm.is_active = true
  productForm.in_catalog = false
  editingProduct.value = null
}

function openCreateProduct() {
  resetProductForm()
  showProductForm.value = true
}

function openEditProduct(p: PricingProduct) {
  editingProduct.value = p
  productForm.sku = p.sku
  productForm.name = p.name
  productForm.department = p.department
  productForm.product_type = p.product_type
  productForm.cost_kit1 = String(p.cost_kit1 ?? '0')
  productForm.cost_kit2 = p.cost_kit2 != null ? String(p.cost_kit2) : ''
  productForm.cost_kit3 = p.cost_kit3 != null ? String(p.cost_kit3) : ''
  productForm.cost_kit4 = p.cost_kit4 != null ? String(p.cost_kit4) : ''
  productForm.ean = p.ean ?? ''
  productForm.is_active = p.is_active
  productForm.in_catalog = p.in_catalog
  showProductForm.value = true
}

async function submitProduct() {
  submitting.value = true
  try {
    const payload: Record<string, unknown> = {
      sku: productForm.sku.trim(),
      name: productForm.name.trim(),
      department: productForm.department,
      product_type: productForm.product_type,
      cost_kit1: Number(productForm.cost_kit1 || 0),
      cost_kit2: productForm.cost_kit2 === '' ? null : Number(productForm.cost_kit2),
      cost_kit3: productForm.cost_kit3 === '' ? null : Number(productForm.cost_kit3),
      cost_kit4: productForm.cost_kit4 === '' ? null : Number(productForm.cost_kit4),
      ean: productForm.ean || null,
      is_active: productForm.is_active,
      in_catalog: productForm.in_catalog,
    }
    if (editingProduct.value) {
      await api<PricingProduct>(`/api/pricing/products/${editingProduct.value.id}`, {
        method: 'PATCH',
        body: payload,
      })
    } else {
      await api<PricingProduct>(`/api/pricing/products`, { method: 'POST', body: payload })
    }
    showProductForm.value = false
    resetProductForm()
    await loadProducts()
  } catch (e: any) {
    productsErr.value = e?.data?.detail?.code ?? 'save_failed'
  } finally {
    submitting.value = false
  }
}

async function deleteProduct(p: PricingProduct) {
  if (!confirm(`Excluir produto "${p.sku}"?`)) return
  try {
    await api(`/api/pricing/products/${p.id}`, { method: 'DELETE' })
    await loadProducts()
  } catch (e: any) {
    productsErr.value = e?.data?.detail?.code ?? 'delete_failed'
  }
}

async function toggleCatalog(p: PricingProduct) {
  try {
    await api(`/api/pricing/products/${p.id}/catalog`, { method: 'POST' })
    await loadProducts()
  } catch (e: any) {
    productsErr.value = e?.data?.detail?.code ?? 'toggle_failed'
  }
}

// =============================================================== boot

watch(
  tab,
  async (t) => {
    if (t === 'contas') await loadAccounts()
    else if (t === 'produtos') await loadProducts()
  },
  { immediate: true },
)

watch(filterDeptContas, () => {
  if (tab.value === 'contas') loadAccounts()
})
watch([filterDeptProdutos, filterCatalog], () => {
  if (tab.value === 'produtos') loadProducts()
})
</script>

<template>
  <div class="p-6 space-y-4">
    <header class="flex items-center justify-between">
      <h1 class="text-2xl font-semibold">Tabela de preços</h1>
    </header>

    <nav class="border-b">
      <ul class="flex gap-1">
        <li v-for="t in TABS" :key="t.key">
          <button
            type="button"
            class="px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors"
            :class="tab === t.key
              ? 'border-primary text-primary'
              : 'border-transparent text-muted-foreground hover:text-foreground'"
            @click="setTab(t.key)"
          >
            {{ t.label }}
          </button>
        </li>
      </ul>
    </nav>

    <!-- ============================================== CONTAS -->
    <section v-if="tab === 'contas'" class="space-y-3">
      <div class="flex flex-wrap items-center gap-2">
        <select v-model="filterDeptContas" class="input input-sm">
          <option value="">Todos departamentos</option>
          <option v-for="d in DEPARTMENTS" :key="d.value" :value="d.value">{{ d.label }}</option>
        </select>
        <button class="btn btn-sm" @click="loadAccounts" :disabled="accountsLoading">
          <RefreshCw class="h-4 w-4" :class="{ 'animate-spin': accountsLoading }" />
          Recarregar
        </button>
        <div class="flex-1" />
        <button v-if="canEditContas" class="btn btn-sm btn-primary" @click="openCreateAccount">
          <Plus class="h-4 w-4" /> Nova conta
        </button>
      </div>

      <div v-if="accountsErr" class="rounded border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive flex items-center gap-2">
        <AlertCircle class="h-4 w-4" /> {{ accountsErr }}
      </div>

      <div class="overflow-x-auto rounded border">
        <table class="w-full text-sm">
          <thead class="bg-muted/50 text-left">
            <tr>
              <th class="px-3 py-2">Nome</th>
              <th class="px-3 py-2">Plataforma</th>
              <th class="px-3 py-2">Depto</th>
              <th class="px-3 py-2">Kit</th>
              <th class="px-3 py-2">Comissão</th>
              <th class="px-3 py-2">Margem 1</th>
              <th class="px-3 py-2">Frete 1</th>
              <th class="px-3 py-2">E-mail</th>
              <th class="px-3 py-2 w-24"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="accountsLoading && !accounts.length">
              <td class="px-3 py-6 text-center text-muted-foreground" colspan="9">
                <Loader2 class="inline h-4 w-4 animate-spin" /> carregando…
              </td>
            </tr>
            <tr v-else-if="!accounts.length">
              <td class="px-3 py-6 text-center text-muted-foreground" colspan="9">
                Nenhuma conta cadastrada.
              </td>
            </tr>
            <tr v-for="a in accounts" :key="a.id" class="border-t hover:bg-muted/30">
              <td class="px-3 py-2 font-medium">{{ a.name }}</td>
              <td class="px-3 py-2">{{ a.platform }}</td>
              <td class="px-3 py-2">{{ a.department }}</td>
              <td class="px-3 py-2">{{ a.kit_number }}</td>
              <td class="px-3 py-2">{{ a.commission ?? '—' }}</td>
              <td class="px-3 py-2">{{ a.margin1 ?? '—' }}</td>
              <td class="px-3 py-2">{{ a.shipping1 ?? '—' }}</td>
              <td class="px-3 py-2 text-muted-foreground">{{ a.email ?? '—' }}</td>
              <td class="px-3 py-2 text-right">
                <button v-if="canEditContas" class="btn btn-xs" @click="openEditAccount(a)">Editar</button>
                <button v-if="canDeleteContas" class="btn btn-xs btn-destructive ml-1" @click="deleteAccount(a)">
                  <Trash2 class="h-3 w-3" />
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <Teleport to="body">
        <div v-if="showAccountForm" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div class="bg-background rounded-lg shadow-xl w-full max-w-2xl p-6 space-y-4">
            <header class="flex items-center justify-between">
              <h2 class="text-lg font-semibold">{{ editingAccount ? 'Editar conta' : 'Nova conta' }}</h2>
              <button class="btn btn-sm btn-ghost" @click="showAccountForm = false">
                <X class="h-4 w-4" />
              </button>
            </header>
            <form class="grid grid-cols-2 gap-3" @submit.prevent="submitAccount">
              <label class="space-y-1 col-span-2">
                <span class="text-xs font-medium">Nome</span>
                <input v-model="accountForm.name" required class="input w-full" />
              </label>
              <label class="space-y-1">
                <span class="text-xs font-medium">Plataforma</span>
                <select v-model="accountForm.platform" class="input w-full">
                  <option v-for="p in PLATFORMS" :key="p.value" :value="p.value">{{ p.label }}</option>
                </select>
              </label>
              <label class="space-y-1">
                <span class="text-xs font-medium">Departamento</span>
                <select v-model="accountForm.department" class="input w-full">
                  <option v-for="d in DEPARTMENTS" :key="d.value" :value="d.value">{{ d.label }}</option>
                </select>
              </label>
              <label class="space-y-1">
                <span class="text-xs font-medium">Kit</span>
                <input v-model.number="accountForm.kit_number" type="number" min="1" max="5" class="input w-full" />
              </label>
              <label class="space-y-1">
                <span class="text-xs font-medium">Tipo de listagem</span>
                <input v-model="accountForm.listing_type" class="input w-full" />
              </label>
              <label class="space-y-1">
                <span class="text-xs font-medium">Comissão (0-1)</span>
                <input v-model="accountForm.commission" type="number" step="0.0001" min="0" max="1" class="input w-full" />
              </label>
              <label class="space-y-1">
                <span class="text-xs font-medium">Margem 1 (0-1)</span>
                <input v-model="accountForm.margin1" type="number" step="0.0001" class="input w-full" />
              </label>
              <label class="space-y-1">
                <span class="text-xs font-medium">Frete 1</span>
                <input v-model="accountForm.shipping1" type="number" step="0.01" class="input w-full" />
              </label>
              <label class="space-y-1 col-span-2">
                <span class="text-xs font-medium">E-mail</span>
                <input v-model="accountForm.email" type="email" class="input w-full" />
              </label>
              <label class="space-y-1 col-span-2">
                <span class="text-xs font-medium">
                  Senha {{ editingAccount ? '(deixe vazio p/ manter)' : '' }}
                </span>
                <input v-model="accountForm.password" type="password" autocomplete="new-password" class="input w-full" />
              </label>
              <div class="col-span-2 flex justify-end gap-2 pt-2">
                <button type="button" class="btn" @click="showAccountForm = false">Cancelar</button>
                <button type="submit" class="btn btn-primary" :disabled="submitting">
                  <Save v-if="!submitting" class="h-4 w-4" />
                  <Loader2 v-else class="h-4 w-4 animate-spin" />
                  Salvar
                </button>
              </div>
            </form>
          </div>
        </div>
      </Teleport>
    </section>

    <!-- ============================================== PRODUTOS -->
    <section v-else-if="tab === 'produtos'" class="space-y-3">
      <div class="flex flex-wrap items-center gap-2">
        <input
          v-model="searchProdutos"
          placeholder="buscar SKU, nome, EAN…"
          class="input input-sm w-64"
        />
        <select v-model="filterDeptProdutos" class="input input-sm">
          <option value="">Todos departamentos</option>
          <option v-for="d in DEPARTMENTS" :key="d.value" :value="d.value">{{ d.label }}</option>
        </select>
        <select v-model="filterCatalog" class="input input-sm">
          <option value="all">Todos</option>
          <option value="in">Catálogo ON</option>
          <option value="out">Catálogo OFF</option>
        </select>
        <button class="btn btn-sm" @click="loadProducts" :disabled="productsLoading">
          <RefreshCw class="h-4 w-4" :class="{ 'animate-spin': productsLoading }" />
          Recarregar
        </button>
        <div class="flex-1" />
        <button v-if="canEditProdutos" class="btn btn-sm btn-primary" @click="openCreateProduct">
          <Plus class="h-4 w-4" /> Novo produto
        </button>
      </div>

      <div v-if="productsErr" class="rounded border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive flex items-center gap-2">
        <AlertCircle class="h-4 w-4" /> {{ productsErr }}
      </div>

      <div class="overflow-x-auto rounded border">
        <table class="w-full text-sm">
          <thead class="bg-muted/50 text-left">
            <tr>
              <th class="px-3 py-2">SKU</th>
              <th class="px-3 py-2">Nome</th>
              <th class="px-3 py-2">Depto</th>
              <th class="px-3 py-2 text-right">Custo Bling</th>
              <th class="px-3 py-2 text-right">Kit 1</th>
              <th class="px-3 py-2 text-right">Kit 2</th>
              <th class="px-3 py-2 text-right">Kit 3</th>
              <th class="px-3 py-2 text-right">Kit 4</th>
              <th class="px-3 py-2 text-center">Catálogo</th>
              <th class="px-3 py-2 text-center">Ativo</th>
              <th class="px-3 py-2 w-24"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="productsLoading && !products.length">
              <td class="px-3 py-6 text-center text-muted-foreground" colspan="11">
                <Loader2 class="inline h-4 w-4 animate-spin" /> carregando…
              </td>
            </tr>
            <tr v-else-if="!productsFiltered.length">
              <td class="px-3 py-6 text-center text-muted-foreground" colspan="11">
                Nenhum produto.
              </td>
            </tr>
            <tr v-for="p in productsFiltered" :key="p.id" class="border-t hover:bg-muted/30">
              <td class="px-3 py-2 font-mono text-xs">{{ p.sku }}</td>
              <td class="px-3 py-2">{{ p.name }}</td>
              <td class="px-3 py-2">{{ p.department }}</td>
              <td class="px-3 py-2 text-right">{{ p.bling_cost_price ?? '—' }}</td>
              <td class="px-3 py-2 text-right">{{ p.cost_kit1 ?? '—' }}</td>
              <td class="px-3 py-2 text-right">{{ p.cost_kit2 ?? '—' }}</td>
              <td class="px-3 py-2 text-right">{{ p.cost_kit3 ?? '—' }}</td>
              <td class="px-3 py-2 text-right">{{ p.cost_kit4 ?? '—' }}</td>
              <td class="px-3 py-2 text-center">
                <button
                  v-if="canEditProdutos"
                  class="btn btn-xs"
                  :class="p.in_catalog ? 'btn-primary' : ''"
                  :title="p.in_catalog ? 'Catálogo ON' : 'Catálogo OFF'"
                  @click="toggleCatalog(p)"
                >
                  <Star class="h-3 w-3" />
                </button>
                <span v-else>{{ p.in_catalog ? 'sim' : 'não' }}</span>
              </td>
              <td class="px-3 py-2 text-center">
                <Eye v-if="p.is_active" class="inline h-4 w-4 text-emerald-600" />
                <EyeOff v-else class="inline h-4 w-4 text-muted-foreground" />
              </td>
              <td class="px-3 py-2 text-right">
                <button v-if="canEditProdutos" class="btn btn-xs" @click="openEditProduct(p)">Editar</button>
                <button v-if="canDeleteProdutos" class="btn btn-xs btn-destructive ml-1" @click="deleteProduct(p)">
                  <Trash2 class="h-3 w-3" />
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <Teleport to="body">
        <div v-if="showProductForm" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div class="bg-background rounded-lg shadow-xl w-full max-w-2xl p-6 space-y-4">
            <header class="flex items-center justify-between">
              <h2 class="text-lg font-semibold">{{ editingProduct ? 'Editar produto' : 'Novo produto' }}</h2>
              <button class="btn btn-sm btn-ghost" @click="showProductForm = false">
                <X class="h-4 w-4" />
              </button>
            </header>
            <form class="grid grid-cols-2 gap-3" @submit.prevent="submitProduct">
              <label class="space-y-1">
                <span class="text-xs font-medium">SKU</span>
                <input v-model="productForm.sku" required class="input w-full" />
              </label>
              <label class="space-y-1">
                <span class="text-xs font-medium">EAN</span>
                <input v-model="productForm.ean" class="input w-full" />
              </label>
              <label class="space-y-1 col-span-2">
                <span class="text-xs font-medium">Nome</span>
                <input v-model="productForm.name" required class="input w-full" />
              </label>
              <label class="space-y-1">
                <span class="text-xs font-medium">Departamento</span>
                <select v-model="productForm.department" class="input w-full">
                  <option v-for="d in DEPARTMENTS" :key="d.value" :value="d.value">{{ d.label }}</option>
                </select>
              </label>
              <label class="space-y-1">
                <span class="text-xs font-medium">Tipo (1=simples, 2=kit)</span>
                <input v-model.number="productForm.product_type" type="number" min="1" max="9" class="input w-full" />
              </label>
              <label class="space-y-1">
                <span class="text-xs font-medium">Custo Kit 1</span>
                <input v-model="productForm.cost_kit1" type="number" step="0.01" required class="input w-full" />
              </label>
              <label class="space-y-1">
                <span class="text-xs font-medium">Custo Kit 2</span>
                <input v-model="productForm.cost_kit2" type="number" step="0.01" class="input w-full" />
              </label>
              <label class="space-y-1">
                <span class="text-xs font-medium">Custo Kit 3</span>
                <input v-model="productForm.cost_kit3" type="number" step="0.01" class="input w-full" />
              </label>
              <label class="space-y-1">
                <span class="text-xs font-medium">Custo Kit 4</span>
                <input v-model="productForm.cost_kit4" type="number" step="0.01" class="input w-full" />
              </label>
              <label class="flex items-center gap-2 col-span-1">
                <input v-model="productForm.is_active" type="checkbox" />
                <span class="text-sm">Ativo</span>
              </label>
              <label class="flex items-center gap-2 col-span-1">
                <input v-model="productForm.in_catalog" type="checkbox" />
                <span class="text-sm">No catálogo ML</span>
              </label>
              <div class="col-span-2 flex justify-end gap-2 pt-2">
                <button type="button" class="btn" @click="showProductForm = false">Cancelar</button>
                <button type="submit" class="btn btn-primary" :disabled="submitting">
                  <Save v-if="!submitting" class="h-4 w-4" />
                  <Loader2 v-else class="h-4 w-4 animate-spin" />
                  Salvar
                </button>
              </div>
            </form>
          </div>
        </div>
      </Teleport>
    </section>

    <!-- ============================================== STUBS 9b/9c/9d -->
    <section v-else class="rounded border border-dashed p-12 text-center text-muted-foreground">
      <p class="text-lg font-medium">Tab "{{ tab }}" — em construção</p>
      <p class="text-sm mt-2">
        Esta aba chega na sub-fase 9{{ tab === 'overrides' ? 'b' : tab === 'auditoria' ? 'd' : 'd' }}.
      </p>
    </section>
  </div>
</template>
