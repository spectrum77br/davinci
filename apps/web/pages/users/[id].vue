<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { ArrowLeft, Save, Trash2, X } from 'lucide-vue-next'
import { ACTIONS, RESOURCES, RESOURCE_GROUPS, RESOURCE_LABELS, type Action, type Resource } from '~/composables/useCan'

definePageMeta({ middleware: ['admin'] })

type ResourcePerm = { view: boolean; edit: boolean; delete: boolean }
type UserDetail = {
  id: string
  email: string
  name: string | null
  role: 'admin' | 'user'
  status: 'pending' | 'active' | 'suspended'
  tuta: string | null
  upseller: string | null
  bling_login: string | null
  adspower: string | null
  duoke: string | null
  threema: string | null
  stock_tags: string[] | null
  sales_teams: number[] | null
  marketing_teams: string[] | null
  permissions: Partial<Record<Resource, Partial<ResourcePerm>>>
  has_password: boolean
  disabled_at: string | null
}

// Single source of truth for the operator-of-stock tag whitelist. Keep
// in sync with backend STOCK_TAGS (apps/api/app/schemas/users.py).
const STOCK_TAG_OPTIONS: { slug: string; label: string }[] = [
  { slug: 'ci', label: 'CI' },
  { slug: 'pi', label: 'PI' },
  { slug: 'ra', label: 'RA' },
  { slug: 'sa', label: 'SA' },
  { slug: 'sp', label: 'SP' },
  { slug: 'us', label: 'Usados' },
  { slug: 'cd', label: 'Centro de Distribuição' },
  { slug: 'fake', label: 'Fake' },
  { slug: 'mala', label: 'Mala' },
  { slug: 'eletro', label: 'Eletro' },
  { slug: 'insumos', label: 'Insumos' },
]

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const { api } = useApi()

const userId = route.params.id as string
const user = ref<UserDetail | null>(null)
const error = ref<string | null>(null)
const saving = ref(false)
const savingPerms = ref(false)
const deleting = ref(false)

async function load() {
  user.value = await api<UserDetail>(`/api/users/${userId}`)
  resetForm()
  resetPerms()
}

const form = reactive({
  name: '',
  email: '',
  tuta: '',
  upseller: '',
  bling_login: '',
  adspower: '',
  duoke: '',
  threema: '',
  stock_tags: [] as string[],
  sales_teams: [] as number[],
  marketing_teams: [] as string[],
  status: 'pending' as 'pending' | 'active' | 'suspended',
})

// Opções de equipe = união { equipes já presentes nas lojas } ∪ { equipes de
// QUALQUER usuário } ∪ { equipes do próprio user }. Carregado uma vez ao
// montar a página. Assim, uma equipe nova criada num usuário (ex.: 2.1)
// aparece na lista de todos os outros mesmo antes de existir loja com ela
// (pedido Eduardo 26/08 — opção 2).
const salesTeamOptions = ref<number[]>([])

async function loadSalesTeamOptions() {
  const set = new Set<number>()
  // Cada fonte é best-effort: se uma falhar, as outras seguem valendo.
  const [lojas, usuarios] = await Promise.all([
    api<Array<{ sales_team: number | null }>>('/api/pricing/store-info')
      .catch(() => [] as Array<{ sales_team: number | null }>),
    api<{ items: Array<{ sales_teams: number[] | null }> }>(
      '/api/users?per_page=200',
    ).catch(() => ({ items: [] as Array<{ sales_teams: number[] | null }> })),
  ])
  for (const r of lojas) {
    if (typeof r.sales_team === 'number' && r.sales_team > 0) set.add(r.sales_team)
  }
  for (const u of usuarios.items || []) {
    for (const t of u.sales_teams || []) {
      if (typeof t === 'number' && t > 0) set.add(t)
    }
  }
  // Inclui equipes que o user já tem (caso as buscas acima falhem).
  for (const t of user.value?.sales_teams || []) set.add(t)
  salesTeamOptions.value = [...set].sort((a, b) => a - b)
}

function resetForm() {
  if (!user.value) return
  form.name = user.value.name || ''
  form.email = user.value.email
  form.tuta = user.value.tuta || ''
  form.upseller = user.value.upseller || ''
  form.bling_login = user.value.bling_login || ''
  form.adspower = user.value.adspower || ''
  form.duoke = user.value.duoke || ''
  form.threema = user.value.threema || ''
  form.stock_tags = [...(user.value.stock_tags || [])]
  form.sales_teams = [...(user.value.sales_teams || [])]
  form.marketing_teams = [...(user.value.marketing_teams || [])]
  form.status = user.value.status
}

function toggleStockTag(slug: string) {
  const i = form.stock_tags.indexOf(slug)
  if (i >= 0) form.stock_tags.splice(i, 1)
  else form.stock_tags.push(slug)
}

function toggleSalesTeam(n: number) {
  const i = form.sales_teams.indexOf(n)
  if (i >= 0) form.sales_teams.splice(i, 1)
  else form.sales_teams.push(n)
}

// Converte o texto "empresa.membro" (ex.: "2.1") no int codificado
// E*100+M (2.1 → 201). Retorna null se o formato for inválido.
function parseTeam(raw: unknown): number | null {
  const s = String(raw ?? '').trim()
  const m = s.match(/^(\d+)\.(\d+)$/)
  if (!m) return null
  const e = parseInt(m[1], 10)
  const mem = parseInt(m[2], 10)
  if (e < 1 || mem < 1 || mem > 99) return null
  return e * 100 + mem
}

// Input do "+ adicionar equipe" — formato "empresa.membro" (ex.: 2.1).
// Guarda o int codificado (E*100+M) em form.sales_teams (dedup) e nas opções.
const newSalesTeam = ref<string>('')
function addSalesTeam() {
  const n = parseTeam(newSalesTeam.value)
  if (n == null) return
  if (!form.sales_teams.includes(n)) form.sales_teams.push(n)
  if (!salesTeamOptions.value.includes(n)) {
    salesTeamOptions.value = [...salesTeamOptions.value, n].sort((a, b) => a - b)
  }
  newSalesTeam.value = ''
}

// Rótulo estilo Valuation: o int codificado E*100+M vira "empresa.membro"
// (201 → "2.1"). O número cru segue sendo a chave gravada no banco
// (sales_team) — só o display muda.
function teamLabel(n: number) {
  return `${Math.floor(n / 100)}.${n % 100}`
}

// Remove a equipe das opções visíveis E da seleção do user. Some da lista
// (o número não é apagado de outros usuários/lojas — só sai daqui).
function removeSalesTeamOption(n: number) {
  salesTeamOptions.value = salesTeamOptions.value.filter((x) => x !== n)
  const i = form.sales_teams.indexOf(n)
  if (i >= 0) form.sales_teams.splice(i, 1)
}

// ---- Equipe de Marketing — espelho das Equipes de Vendas, mas com
// ---- nomes livres (strings). Opções = equipes já usadas (endpoint
// ---- /equipes dos Criativos) ∪ equipes do próprio user.
const marketingTeamOptions = ref<string[]>([])

async function loadMarketingTeamOptions() {
  const own = user.value?.marketing_teams || []
  try {
    const opts = await api<string[]>('/api/marketing/creatives/equipes')
    const map = new Map<string, string>()
    for (const t of [...opts, ...own]) {
      const k = t.trim().toLowerCase()
      if (k && !map.has(k)) map.set(k, t.trim())
    }
    marketingTeamOptions.value = [...map.values()].sort((a, b) => a.localeCompare(b))
  } catch {
    marketingTeamOptions.value = [...own].sort((a, b) => a.localeCompare(b))
  }
}

function toggleMarketingTeam(t: string) {
  const i = form.marketing_teams.indexOf(t)
  if (i >= 0) form.marketing_teams.splice(i, 1)
  else form.marketing_teams.push(t)
}

const newMarketingTeam = ref<string>('')
function addMarketingTeam() {
  const t = newMarketingTeam.value.trim().slice(0, 64)
  if (!t) return
  const k = t.toLowerCase()
  if (!form.marketing_teams.some((x) => x.toLowerCase() === k)) form.marketing_teams.push(t)
  if (!marketingTeamOptions.value.some((x) => x.toLowerCase() === k)) {
    marketingTeamOptions.value = [...marketingTeamOptions.value, t].sort((a, b) => a.localeCompare(b))
  }
  newMarketingTeam.value = ''
}

function removeMarketingTeamOption(t: string) {
  marketingTeamOptions.value = marketingTeamOptions.value.filter((x) => x !== t)
  const i = form.marketing_teams.indexOf(t)
  if (i >= 0) form.marketing_teams.splice(i, 1)
}

const perms = reactive<Record<Resource, ResourcePerm>>(
  Object.fromEntries(RESOURCES.map((r) => [r, { view: false, edit: false, delete: false }])) as any,
)

function resetPerms() {
  if (!user.value) return
  for (const r of RESOURCES) {
    const p = user.value.permissions?.[r] || {}
    perms[r] = {
      view: !!p.view,
      edit: !!p.edit,
      delete: !!p.delete,
    }
  }
}

await load()
await loadSalesTeamOptions()
await loadMarketingTeamOptions()

// Cascade rules: delete → edit → view
function onChange(r: Resource, action: Action) {
  const p = perms[r]
  if (action === 'delete' && p.delete) {
    p.edit = true
    p.view = true
  } else if (action === 'edit' && p.edit) {
    p.view = true
  } else if (action === 'view' && !p.view) {
    p.edit = false
    p.delete = false
  } else if (action === 'edit' && !p.edit) {
    p.delete = false
  }
}

function setRow(r: Resource, on: boolean) {
  perms[r] = on
    ? { view: true, edit: true, delete: true }
    : { view: false, edit: false, delete: false }
}

function setColumn(action: Action, on: boolean) {
  for (const r of RESOURCES) {
    perms[r][action] = on
    onChange(r, action)
  }
}

const isAdminUser = computed(() => user.value?.role === 'admin')
const isSelf = computed(() => user.value?.id === auth.user?.id)

// Definir/resetar senha. Backend bloqueia mexer na senha de OUTRO admin
// (cannot_set_admin_password) — espelhamos com o disabled abaixo.
const PASSWORD_MIN = 8
const newPassword = ref('')
const settingPassword = ref(false)
const passwordMsg = ref<string | null>(null)
const canSetPassword = computed(() => !isAdminUser.value || isSelf.value)

async function setPassword() {
  passwordMsg.value = null
  if (newPassword.value.length < PASSWORD_MIN) {
    passwordMsg.value = `A senha precisa de pelo menos ${PASSWORD_MIN} caracteres.`
    return
  }
  settingPassword.value = true
  try {
    await api(`/api/users/${userId}/password`, {
      method: 'POST',
      body: { password: newPassword.value },
    })
    newPassword.value = ''
    passwordMsg.value = 'Senha definida.'
    if (user.value) user.value.has_password = true
  } catch (e: any) {
    passwordMsg.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    settingPassword.value = false
  }
}

async function saveCadastral() {
  saving.value = true
  error.value = null
  try {
    const body: Record<string, any> = {}
    for (const k of ['name', 'tuta', 'upseller', 'bling_login', 'adspower', 'duoke', 'threema'] as const) {
      body[k] = form[k] || null
    }
    body.email = form.email
    body.status = form.status
    // Operator-of-stock tags — empty array clears (backend treats []
    // and null identically).
    body.stock_tags = [...form.stock_tags]
    // Equipes de Vendas (mesma semântica de stock_tags).
    body.sales_teams = [...form.sales_teams]
    // Equipe de Marketing (nomes livres — mesma semântica).
    body.marketing_teams = [...form.marketing_teams]
    user.value = await api<UserDetail>(`/api/users/${userId}`, { method: 'PATCH', body })
    resetPerms()
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    saving.value = false
  }
}

async function savePerms() {
  savingPerms.value = true
  error.value = null
  try {
    user.value = await api<UserDetail>(`/api/users/${userId}/permissions`, {
      method: 'PATCH',
      body: { permissions: perms },
    })
    resetPerms()
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    savingPerms.value = false
  }
}

async function removeUser() {
  if (!confirm('Excluir (desabilitar) este usuário?')) return
  deleting.value = true
  error.value = null
  try {
    await api(`/api/users/${userId}`, { method: 'DELETE' })
    await router.push('/users')
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
    deleting.value = false
  }
}
</script>

<template>
  <div v-if="user" class="space-y-6 max-w-5xl">
    <div class="flex flex-wrap items-center gap-2 sm:gap-3">
      <Button size="sm" variant="ghost" @click="router.push('/users')">
        <ArrowLeft class="size-4" />
      </Button>
      <h1 class="text-xl sm:text-2xl font-semibold break-all min-w-0">{{ user.name || user.email }}</h1>
      <span class="text-xs px-2 py-0.5 rounded border">{{ user.role }}</span>
      <span class="text-xs px-2 py-0.5 rounded border">{{ user.status }}</span>
      <Button
        class="sm:ml-auto"
        size="sm"
        variant="outline"
        :disabled="isSelf || deleting"
        @click="removeUser"
      >
        <Trash2 class="size-4 mr-1" /> Excluir
      </Button>
    </div>

    <div v-if="error" class="text-sm text-red-500">erro: {{ error }}</div>

    <!-- Cadastral -->
    <Card>
      <CardHeader>
        <CardTitle>Dados cadastrais</CardTitle>
        <CardDescription>Role é read-only. Promoção a admin só via DB ou OWNER_OPEN_ID.</CardDescription>
      </CardHeader>
      <CardContent class="space-y-4">
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <Label>Nome</Label>
            <Input v-model="form.name" />
          </div>
          <div>
            <Label>E-mail</Label>
            <Input v-model="form.email" type="email" />
          </div>
          <div>
            <Label>Tuta</Label>
            <Input v-model="form.tuta" />
          </div>
          <div>
            <Label>Upseller</Label>
            <Input v-model="form.upseller" />
          </div>
          <div>
            <Label>Bling login</Label>
            <Input v-model="form.bling_login" />
          </div>
          <div>
            <Label>AdsPower</Label>
            <Input v-model="form.adspower" />
          </div>
          <div>
            <Label>Duoke</Label>
            <Input v-model="form.duoke" />
          </div>
          <div>
            <Label>Threema</Label>
            <Input v-model="form.threema" placeholder="ex. CDSA84BZ" />
          </div>
          <div class="md:col-span-2">
            <Label>Tags de Estoque</Label>
            <div class="grid grid-cols-2 md:grid-cols-4 gap-1.5 mt-1 border rounded-md p-2 bg-background">
              <label
                v-for="opt in STOCK_TAG_OPTIONS"
                :key="opt.slug"
                class="inline-flex items-center gap-1.5 text-sm cursor-pointer hover:bg-muted/50 rounded px-1.5 py-0.5"
              >
                <input
                  type="checkbox"
                  :checked="form.stock_tags.includes(opt.slug)"
                  @change="toggleStockTag(opt.slug)"
                />
                <span>{{ opt.label }}</span>
              </label>
            </div>
            <p class="text-[11px] text-muted-foreground mt-1">
              Multi-select. Quando ≥1 marcada e o usuário não é admin, o sistema bloqueia em
              /controle-estoque e mostra a união de produtos das tags selecionadas.
              <span v-if="form.stock_tags.length">
                Selecionadas: <code>{{ form.stock_tags.join(', ') }}</code>
              </span>
            </p>
          </div>
          <div class="md:col-span-2">
            <Label>Equipes de Vendas</Label>
            <div
              v-if="salesTeamOptions.length"
              class="grid grid-cols-2 md:grid-cols-6 gap-1.5 mt-1 border rounded-md p-2 bg-background"
            >
              <div
                v-for="n in salesTeamOptions"
                :key="n"
                class="group inline-flex items-center gap-1.5 text-sm rounded px-1.5 py-0.5 hover:bg-muted/50"
              >
                <label class="inline-flex items-center gap-1.5 cursor-pointer flex-1">
                  <input
                    type="checkbox"
                    :checked="form.sales_teams.includes(n)"
                    @change="toggleSalesTeam(n)"
                  />
                  <span>{{ teamLabel(n) }}</span>
                </label>
                <button
                  type="button"
                  title="Excluir equipe"
                  class="text-muted-foreground/50 hover:text-destructive opacity-0 group-hover:opacity-100 transition"
                  @click="removeSalesTeamOption(n)"
                >
                  <X class="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
            <p v-else class="text-[11px] text-muted-foreground mt-1 italic">
              Nenhuma equipe cadastrada ainda. Use o campo abaixo pra adicionar.
            </p>
            <div class="flex items-center gap-2 mt-2">
              <Input
                v-model="newSalesTeam"
                type="text"
                placeholder="+ adicionar equipe (ex: 2.1)"
                class="w-48"
                @keydown.enter.prevent="addSalesTeam"
              />
              <Button type="button" variant="outline" size="sm" @click="addSalesTeam">
                Adicionar
              </Button>
            </div>
            <p class="text-[11px] text-muted-foreground mt-1">
              Multi-select de equipes de vendas no formato empresa.membro (ex.: 1.1, 2.1).
              A loja é vinculada à equipe na página Lojas; aqui você seleciona quais
              equipes esse usuário pertence.
              <span v-if="form.sales_teams.length">
                Selecionadas: <code>{{ form.sales_teams.map(teamLabel).join(', ') }}</code>
              </span>
            </p>
          </div>
          <div class="md:col-span-2">
            <Label>Equipe de Marketing</Label>
            <div
              v-if="marketingTeamOptions.length"
              class="grid grid-cols-2 md:grid-cols-6 gap-1.5 mt-1 border rounded-md p-2 bg-background"
            >
              <div
                v-for="t in marketingTeamOptions"
                :key="t"
                class="group inline-flex items-center gap-1.5 text-sm rounded px-1.5 py-0.5 hover:bg-muted/50"
              >
                <label class="inline-flex items-center gap-1.5 cursor-pointer flex-1">
                  <input
                    type="checkbox"
                    :checked="form.marketing_teams.includes(t)"
                    @change="toggleMarketingTeam(t)"
                  />
                  <span>{{ t }}</span>
                </label>
                <button
                  type="button"
                  title="Excluir equipe"
                  class="text-muted-foreground/50 hover:text-destructive opacity-0 group-hover:opacity-100 transition"
                  @click="removeMarketingTeamOption(t)"
                >
                  <X class="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
            <p v-else class="text-[11px] text-muted-foreground mt-1 italic">
              Nenhuma equipe de marketing cadastrada ainda. Use o campo abaixo pra adicionar.
            </p>
            <div class="flex items-center gap-2 mt-2">
              <Input
                v-model="newMarketingTeam"
                type="text"
                placeholder="+ adicionar equipe (ex: video)"
                class="w-48"
                @keydown.enter.prevent="addMarketingTeam"
              />
              <Button type="button" variant="outline" size="sm" @click="addMarketingTeam">
                Adicionar
              </Button>
            </div>
            <p class="text-[11px] text-muted-foreground mt-1">
              Multi-select de equipes de marketing (nome livre). Usuário não-admin com
              ≥1 equipe marcada só enxerga, na aba Criativos, as linhas com o campo
              Equipe igual a uma das dele.
              <span v-if="form.marketing_teams.length">
                Selecionadas: <code>{{ form.marketing_teams.join(', ') }}</code>
              </span>
            </p>
          </div>
          <div>
            <Label>Status</Label>
            <select v-model="form.status" class="w-full h-9 rounded-md border bg-background px-3 text-sm">
              <option value="pending">pending</option>
              <option value="active">active</option>
              <option value="suspended">suspended</option>
            </select>
          </div>
        </div>
        <div class="flex justify-end">
          <Button :disabled="saving" @click="saveCadastral">
            <Save class="size-4 mr-1" /> {{ saving ? 'salvando…' : 'Salvar dados' }}
          </Button>
        </div>
      </CardContent>
    </Card>

    <!-- Senha -->
    <Card>
      <CardHeader>
        <CardTitle>Senha de acesso</CardTitle>
        <CardDescription>
          O usuário entra com o e-mail acima + esta senha. Quem esquecer a senha
          deve pedir um reset aqui — não há recuperação automática.
        </CardDescription>
      </CardHeader>
      <CardContent class="space-y-4">
        <div class="flex items-center gap-2 text-sm">
          <span class="text-muted-foreground">Status:</span>
          <span v-if="user.has_password" class="px-2 py-0.5 rounded border text-emerald-500">
            senha definida
          </span>
          <span v-else class="px-2 py-0.5 rounded border text-amber-500">
            sem senha — entra só por código
          </span>
        </div>
        <div v-if="canSetPassword" class="flex flex-col sm:flex-row gap-2 sm:items-end">
          <div class="flex-1">
            <Label>{{ user.has_password ? 'Nova senha' : 'Definir senha' }}</Label>
            <Input
              v-model="newPassword"
              type="text"
              autocomplete="new-password"
              :placeholder="`mín. ${PASSWORD_MIN} caracteres`"
            />
          </div>
          <Button :disabled="settingPassword || !newPassword" @click="setPassword">
            <Save class="size-4 mr-1" />
            {{ settingPassword ? 'salvando…' : (user.has_password ? 'Resetar senha' : 'Definir senha') }}
          </Button>
        </div>
        <p v-else class="text-sm text-amber-500">
          Não é possível alterar a senha de outro administrador.
        </p>
        <p v-if="passwordMsg" class="text-sm text-muted-foreground">{{ passwordMsg }}</p>
      </CardContent>
    </Card>

    <!-- Permissions matrix -->
    <Card>
      <CardHeader>
        <CardTitle>Permissões</CardTitle>
        <CardDescription>
          <span v-if="isAdminUser" class="text-amber-400">
            Admins têm bypass total — matriz não se aplica
          </span>
          <span v-else>
            Marcar <code>delete</code> liga <code>edit</code> e <code>view</code>; <code>edit</code> liga <code>view</code>.
          </span>
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div class="border rounded-md overflow-x-auto">
          <table class="w-full text-sm min-w-[480px]">
            <thead class="bg-muted/40">
              <tr>
                <th class="px-3 py-2 text-left">Recurso</th>
                <th v-for="a in ACTIONS" :key="a" class="px-3 py-2 text-center capitalize">
                  <div class="flex flex-col items-center gap-1">
                    <span>{{ a }}</span>
                    <div v-if="!isAdminUser" class="flex gap-1 text-[10px] text-muted-foreground">
                      <button class="underline hover:text-foreground" type="button" @click="setColumn(a, true)">all</button>
                      <button class="underline hover:text-foreground" type="button" @click="setColumn(a, false)">none</button>
                    </div>
                  </div>
                </th>
                <th class="px-3 py-2 text-center w-32">Linha</th>
              </tr>
            </thead>
            <tbody>
              <template v-for="g in RESOURCE_GROUPS" :key="g.label">
                <tr class="bg-muted/40 border-t">
                  <td :colspan="ACTIONS.length + 2" class="px-3 py-1 text-[10px] uppercase tracking-[0.12em] font-semibold text-muted-foreground">
                    {{ g.label }}
                  </td>
                </tr>
                <tr v-for="r in g.resources" :key="r" class="border-t">
                  <td class="px-3 py-2">{{ RESOURCE_LABELS[r] }}</td>
                  <td v-for="a in ACTIONS" :key="a" class="px-3 py-2 text-center">
                    <input
                      v-model="perms[r][a]"
                      type="checkbox"
                      :disabled="isAdminUser"
                      @change="onChange(r, a)"
                    />
                  </td>
                  <td class="px-3 py-2 text-center">
                    <div v-if="!isAdminUser" class="flex justify-center gap-1 text-[10px]">
                      <button class="underline text-muted-foreground hover:text-foreground" type="button" @click="setRow(r, true)">all</button>
                      <button class="underline text-muted-foreground hover:text-foreground" type="button" @click="setRow(r, false)">none</button>
                    </div>
                  </td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
        <div class="flex justify-end mt-4">
          <Button :disabled="savingPerms || isAdminUser" @click="savePerms">
            <Save class="size-4 mr-1" /> {{ savingPerms ? 'salvando…' : 'Salvar permissões' }}
          </Button>
        </div>
      </CardContent>
    </Card>
  </div>
</template>
