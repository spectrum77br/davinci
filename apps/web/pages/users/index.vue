<script setup lang="ts">
import { ref } from 'vue'
import { Plus, RefreshCw, X } from 'lucide-vue-next'

definePageMeta({ middleware: ['admin'] })

type UserRow = {
  id: string
  email: string
  name: string | null
  role: 'admin' | 'user'
  status: 'pending' | 'active' | 'suspended'
  tuta: string | null
  upseller: string | null
  bling_login: string | null
  adspower: string | null
  last_login_at: string | null
  disabled_at: string | null
}

type UserList = { items: UserRow[]; total: number; page: number; per_page: number }

const { api } = useApi()
const list = ref<UserList | null>(null)
const loading = ref(false)
const q = ref('')
const showNew = ref(false)
const error = ref<string | null>(null)

async function refresh() {
  loading.value = true
  error.value = null
  try {
    const params = new URLSearchParams()
    if (q.value) params.set('q', q.value)
    params.set('per_page', '100')
    list.value = await api<UserList>(`/api/users?${params.toString()}`)
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    loading.value = false
  }
}

await refresh()

const draft = ref({
  email: '',
  name: '',
  tuta: '',
  upseller: '',
  bling_login: '',
  adspower: '',
})
const creating = ref(false)
const createErr = ref<string | null>(null)

async function createUser() {
  creating.value = true
  createErr.value = null
  try {
    const body: Record<string, any> = { email: draft.value.email }
    for (const k of ['name', 'tuta', 'upseller', 'bling_login', 'adspower'] as const) {
      if (draft.value[k]) body[k] = draft.value[k]
    }
    await api('/api/users', { method: 'POST', body })
    showNew.value = false
    draft.value = { email: '', name: '', tuta: '', upseller: '', bling_login: '', adspower: '' }
    await refresh()
  } catch (e: any) {
    createErr.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    creating.value = false
  }
}

async function approve(u: UserRow) {
  try {
    await api(`/api/users/${u.id}`, { method: 'PATCH', body: { status: 'active' } })
    await refresh()
  } catch (e: any) {
    error.value = e?.data?.detail?.code || 'erro'
  }
}

function fmtDate(s: string | null) {
  if (!s) return '—'
  return new Date(s).toLocaleString('pt-BR')
}
</script>

<template>
  <div class="space-y-4">
    <div class="flex items-center gap-3">
      <h1 class="text-2xl font-semibold">Usuários</h1>
      <Button size="sm" variant="ghost" :disabled="loading" @click="refresh">
        <RefreshCw class="size-4 mr-1" /> recarregar
      </Button>
      <div class="ml-auto flex gap-2">
        <Input v-model="q" placeholder="buscar e-mail/nome" class="w-64" @keydown.enter="refresh" />
        <Button size="sm" @click="showNew = true">
          <Plus class="size-4 mr-1" /> Novo usuário
        </Button>
      </div>
    </div>

    <div v-if="error" class="text-sm text-red-500">erro: {{ error }}</div>

    <div class="border rounded-md overflow-hidden">
      <table class="w-full text-sm">
        <thead class="bg-muted/40 text-left">
          <tr>
            <th class="px-3 py-2">Nome</th>
            <th class="px-3 py-2">E-mail</th>
            <th class="px-3 py-2">Tuta</th>
            <th class="px-3 py-2">Upseller</th>
            <th class="px-3 py-2">Bling</th>
            <th class="px-3 py-2">AdsPower</th>
            <th class="px-3 py-2">Role</th>
            <th class="px-3 py-2">Status</th>
            <th class="px-3 py-2">Último login</th>
            <th class="px-3 py-2 text-right">Ações</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="u in list?.items || []" :key="u.id" class="border-t hover:bg-muted/20">
            <td class="px-3 py-2">
              <NuxtLink :to="`/users/${u.id}`" class="hover:underline">
                {{ u.name || '—' }}
              </NuxtLink>
            </td>
            <td class="px-3 py-2">{{ u.email }}</td>
            <td class="px-3 py-2">{{ u.tuta || '—' }}</td>
            <td class="px-3 py-2">{{ u.upseller || '—' }}</td>
            <td class="px-3 py-2">{{ u.bling_login || '—' }}</td>
            <td class="px-3 py-2">{{ u.adspower || '—' }}</td>
            <td class="px-3 py-2">
              <span class="text-xs px-2 py-0.5 rounded border" :class="u.role === 'admin' ? 'border-amber-400 text-amber-300' : ''">
                {{ u.role }}
              </span>
            </td>
            <td class="px-3 py-2">
              <span class="text-xs px-2 py-0.5 rounded border" :class="{
                'border-green-500 text-green-400': u.status === 'active',
                'border-amber-500 text-amber-400': u.status === 'pending',
                'border-red-500 text-red-400': u.status === 'suspended',
              }">{{ u.status }}</span>
            </td>
            <td class="px-3 py-2 text-muted-foreground">{{ fmtDate(u.last_login_at) }}</td>
            <td class="px-3 py-2 text-right space-x-2">
              <Button v-if="u.status === 'pending'" size="sm" variant="outline" @click="approve(u)">
                Aprovar
              </Button>
              <NuxtLink :to="`/users/${u.id}`" class="text-xs underline text-muted-foreground">permissões</NuxtLink>
            </td>
          </tr>
          <tr v-if="!loading && (list?.items?.length || 0) === 0">
            <td colspan="10" class="px-3 py-6 text-center text-muted-foreground">nenhum usuário</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- New user modal -->
    <div v-if="showNew" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" @click.self="showNew = false">
      <div class="bg-background border rounded-lg w-full max-w-md p-5 space-y-4">
        <div class="flex items-center">
          <h2 class="text-lg font-semibold">Novo usuário</h2>
          <Button class="ml-auto" size="sm" variant="ghost" @click="showNew = false">
            <X class="size-4" />
          </Button>
        </div>
        <div class="space-y-3">
          <div>
            <Label>E-mail *</Label>
            <Input v-model="draft.email" type="email" required />
          </div>
          <div>
            <Label>Nome</Label>
            <Input v-model="draft.name" />
          </div>
          <div class="grid grid-cols-2 gap-3">
            <div>
              <Label>Tuta</Label>
              <Input v-model="draft.tuta" />
            </div>
            <div>
              <Label>Upseller</Label>
              <Input v-model="draft.upseller" />
            </div>
            <div>
              <Label>Bling login</Label>
              <Input v-model="draft.bling_login" />
            </div>
            <div>
              <Label>AdsPower</Label>
              <Input v-model="draft.adspower" />
            </div>
          </div>
        </div>
        <div v-if="createErr" class="text-sm text-red-500">erro: {{ createErr }}</div>
        <div class="text-xs text-muted-foreground">
          Usuário nasce <code>user/pending</code>. Defina permissões depois em "permissões".
        </div>
        <div class="flex justify-end gap-2">
          <Button variant="ghost" :disabled="creating" @click="showNew = false">cancelar</Button>
          <Button :disabled="creating || !draft.email" @click="createUser">
            {{ creating ? 'criando…' : 'Criar' }}
          </Button>
        </div>
      </div>
    </div>
  </div>
</template>
