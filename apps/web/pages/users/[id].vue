<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { ArrowLeft, Save, Trash2 } from 'lucide-vue-next'
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
  stock_tag: string | null
  permissions: Partial<Record<Resource, Partial<ResourcePerm>>>
  disabled_at: string | null
}

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
  stock_tag: '',
  status: 'pending' as 'pending' | 'active' | 'suspended',
})

function resetForm() {
  if (!user.value) return
  form.name = user.value.name || ''
  form.email = user.value.email
  form.tuta = user.value.tuta || ''
  form.upseller = user.value.upseller || ''
  form.bling_login = user.value.bling_login || ''
  form.adspower = user.value.adspower || ''
  form.duoke = user.value.duoke || ''
  form.stock_tag = user.value.stock_tag || ''
  form.status = user.value.status
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

async function saveCadastral() {
  saving.value = true
  error.value = null
  try {
    const body: Record<string, any> = {}
    for (const k of ['name', 'tuta', 'upseller', 'bling_login', 'adspower', 'duoke'] as const) {
      body[k] = form[k] || null
    }
    body.email = form.email
    body.status = form.status
    // Operator-of-stock tag — empty string clears it. Backend treats
    // "" / null identically (no tag).
    body.stock_tag = form.stock_tag || null
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
            <Label>Tag Estoque</Label>
            <select v-model="form.stock_tag" class="w-full h-9 rounded-md border bg-background px-3 text-sm">
              <option value="">(nenhuma)</option>
              <option value="ci">ci</option>
              <option value="pi">pi</option>
              <option value="ra">ra</option>
              <option value="sa">sa</option>
              <option value="sp">sp</option>
            </select>
            <p class="text-[11px] text-muted-foreground mt-1">
              Quando preenchida e o usuário não é admin, o sistema bloqueia em /controle-estoque
              e filtra produtos por SKU terminando em <code>.{{ form.stock_tag || 'tag' }}</code>.
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
