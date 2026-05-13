<script setup lang="ts">
import { ref, computed } from 'vue'
import { SquarePen, Plus, RefreshCw, Trash2, Zap, Search, KeyRound } from 'lucide-vue-next'

definePageMeta({ middleware: ['permission'], permission: { resource: 'integracoes', action: 'view' } })

type Platform = 'bling' | 'ml' | 'shopee' | 'amazon' | 'tiktok' | 'temu'

type Integration = {
  id: string
  user_id: string
  store_id: string | null
  company_id: string | null
  platform: Platform
  name: string
  status: string
  token_expires_at: string | null
  last_test_at: string | null
  last_test_ok: boolean | null
  last_error: string | null
  created_at: string
  updated_at: string
}

type Company = { id: string; apelido: string; razao_social: string }
type Store = {
  id: string; company_id: string; marketplace: string; status: string;
  apelido_override: string | null; integration_id: string | null
}

const PLATFORM_LABELS: Record<string, string> = {
  bling: 'Bling (ERP)',
  ml: 'Mercado Livre',
  shopee: 'Shopee',
  amazon: 'Amazon',
  tiktok: 'TikTok Shop',
  temu: 'Temu',
}

const { api } = useApi()
const items = ref<Integration[]>([])
const companies = ref<Company[]>([])
const stores = ref<Store[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const testingId = ref<string | null>(null)
const showNew = ref(false)
const filter = ref('')

const canEdit = useCan('empresa', 'edit')
const canDelete = useCan('empresa', 'delete')

async function refresh() {
  loading.value = true
  error.value = null
  try {
    const [i, c, s] = await Promise.all([
      api<Integration[]>('/api/integrations'),
      api<Company[]>('/api/companies'),
      api<Store[]>('/api/stores'),
    ])
    items.value = i
    companies.value = c
    stores.value = s
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    loading.value = false
  }
}
await refresh()

const companyById = computed(() => Object.fromEntries(companies.value.map(c => [c.id, c])))
const storeById = computed(() => Object.fromEntries(stores.value.map(s => [s.id, s])))

const filtered = computed(() => {
  const q = filter.value.trim().toLowerCase()
  if (!q) return items.value
  return items.value.filter(i => {
    const store = i.store_id ? storeById.value[i.store_id] : null
    const company = i.company_id ? companyById.value[i.company_id] : null
    const hay = [
      i.name,
      i.platform,
      PLATFORM_LABELS[i.platform],
      i.status,
      store?.marketplace,
      store?.apelido_override,
      company?.apelido,
      company?.razao_social,
    ].filter(Boolean).join(' ').toLowerCase()
    return hay.includes(q)
  })
})

const grouped = computed(() => {
  const map: Record<string, Integration[]> = {}
  for (const i of filtered.value) {
    const key = i.company_id || 'sem-empresa'
    map[key] ??= []
    map[key].push(i)
  }
  return map
})

async function testIntegration(i: Integration) {
  testingId.value = i.id
  try {
    const r = await api<{ ok: boolean; detail: string | null }>(
      `/api/integrations/${i.id}/test`,
      { method: 'POST' }
    )
    if (!r.ok) error.value = `teste falhou: ${r.detail || 'erro'}`
    await refresh()
  } catch (e: any) {
    error.value = e?.data?.detail?.code || 'erro'
  } finally {
    testingId.value = null
  }
}

const editing = ref<Integration | null>(null)
function openEdit(i: Integration) {
  editing.value = i
  showNew.value = true
}
function onModalClose(open: boolean) {
  showNew.value = open
  if (!open) editing.value = null
}

const tiktokAuthorizingId = ref<string | null>(null)
async function authorizeTikTok(i: Integration) {
  tiktokAuthorizingId.value = i.id
  try {
    const r = await api<{ url: string; state: string }>(
      `/api/integrations/tiktok/start?integrationId=${i.id}&origin=${encodeURIComponent(window.location.origin)}`,
    )
    if (r?.url) window.location.href = r.url
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'tiktok_authorize_failed'
  } finally {
    tiktokAuthorizingId.value = null
  }
}

async function deleteIntegration(i: Integration) {
  if (!confirm(`Excluir integração ${i.name}?`)) return
  try {
    await api(`/api/integrations/${i.id}`, { method: 'DELETE' })
    await refresh()
  } catch (e: any) {
    error.value = e?.data?.detail?.code || 'erro'
  }
}

function fmtDate(s: string | null) {
  if (!s) return '—'
  return new Date(s).toLocaleString('pt-BR')
}

function statusClass(i: Integration) {
  if (i.last_test_ok === true) return 'border-green-500 text-green-400'
  if (i.last_test_ok === false) return 'border-red-500 text-red-400'
  return 'border-muted text-muted-foreground'
}

const route = useRoute()
const oauthBanner = computed(() => route.query.oauth === 'ok' ? `OAuth concluído (${route.query.platform})` : null)
</script>

<template>
  <div class="space-y-4">
    <div class="flex items-center gap-3 flex-wrap">
      <h1 class="text-2xl font-semibold">Integrações</h1>
      <Button size="sm" variant="ghost" :disabled="loading" @click="refresh">
        <RefreshCw class="size-4 mr-1" /> recarregar
      </Button>
      <div class="relative ml-auto w-full sm:w-72">
        <Search class="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
        <input
          v-model="filter"
          type="search"
          placeholder="filtrar integrações…"
          class="w-full h-9 rounded-lg border bg-muted/40 pl-9 pr-3 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:bg-background"
        />
      </div>
      <Button v-if="canEdit" size="sm" @click="showNew = true">
        <Plus class="size-4 mr-1" /> Nova (manual)
      </Button>
    </div>

    <div v-if="oauthBanner" class="text-sm text-green-400 border border-green-500/40 rounded p-2">
      {{ oauthBanner }}
    </div>
    <div v-if="error" class="text-sm text-red-500">erro: {{ error }}</div>

    <div v-for="(group, cid) in grouped" :key="cid" class="space-y-2">
      <h2 class="font-semibold text-sm text-muted-foreground">
        {{ companyById[cid]?.apelido || (cid === 'sem-empresa' ? 'sem empresa' : cid) }}
      </h2>
      <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
        <div v-for="i in group" :key="i.id" class="border rounded-md p-3 space-y-2">
          <div class="flex items-center gap-2">
            <span class="text-xs uppercase font-mono">{{ PLATFORM_LABELS[i.platform] }}</span>
            <span class="text-xs px-2 py-0.5 rounded border ml-auto" :class="statusClass(i)">
              {{ i.last_test_ok === null ? 'não testado'
                 : i.last_test_ok ? 'ok' : 'falhou' }}
            </span>
          </div>
          <div class="font-medium">{{ i.name }}</div>
          <div class="text-xs text-muted-foreground space-y-0.5">
            <div v-if="i.store_id">
              loja: <code>{{ storeById[i.store_id]?.marketplace }}</code> /
              {{ storeById[i.store_id]?.apelido_override
                  || companyById[storeById[i.store_id]?.company_id || '']?.apelido || '?' }}
            </div>
            <div v-if="i.token_expires_at">token expira: {{ fmtDate(i.token_expires_at) }}</div>
            <div v-if="i.last_test_at">testada: {{ fmtDate(i.last_test_at) }}</div>
            <div v-if="i.last_error" class="text-red-400 truncate" :title="i.last_error">
              erro: {{ i.last_error }}
            </div>
          </div>
          <div class="flex gap-2 pt-1 flex-wrap">
            <Button size="sm" variant="outline" :disabled="testingId === i.id" @click="testIntegration(i)">
              <Zap class="size-3 mr-1" /> {{ testingId === i.id ? 'testando…' : 'testar' }}
            </Button>
            <Button
              v-if="canEdit && i.platform === 'tiktok'"
              size="sm"
              variant="outline"
              :disabled="tiktokAuthorizingId === i.id"
              title="Iniciar OAuth no TikTok Partner Center"
              @click="authorizeTikTok(i)"
            >
              <KeyRound class="size-3 mr-1" />
              {{ tiktokAuthorizingId === i.id ? 'redirecionando…' : 'Autorizar no TikTok' }}
            </Button>
            <Button v-if="canEdit" size="sm" variant="ghost" title="editar" @click="openEdit(i)">
              <SquarePen class="size-4" />
            </Button>
            <Button v-if="canDelete" size="sm" variant="ghost" @click="deleteIntegration(i)">
              <Trash2 class="size-3" />
            </Button>
          </div>
        </div>
      </div>
    </div>

    <div v-if="!loading && items.length === 0" class="text-muted-foreground text-sm">
      nenhuma integração — conecte via OAuth na página da empresa, ou crie manualmente.
    </div>
    <div v-else-if="!loading && filtered.length === 0" class="text-muted-foreground text-sm">
      nenhuma integração corresponde ao filtro “{{ filter }}”.
    </div>

    <IntegrationFormModal
      :open="showNew"
      :stores="stores"
      :companies="companies"
      :editing="editing"
      @update:open="onModalClose"
      @created="refresh"
      @updated="refresh"
    />
  </div>
</template>
