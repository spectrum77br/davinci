<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { ArrowLeft, Save, Trash2, Link as LinkIcon, Unlink } from 'lucide-vue-next'
import {
  MARKETPLACES,
  MARKETPLACE_LABELS,
  type Marketplace,
  type StoreStatus,
} from '~/composables/useMarketplaces'

definePageMeta({ middleware: ['auth', 'permission'], permission: { resource: 'empresa', action: 'view' } })

type StoreOut = {
  id: string
  company_id: string
  marketplace: string
  apelido_override: string | null
  status: StoreStatus
  integration_id: string | null
  bling_store_id: number | null
  notes: string | null
  created_at: string
  updated_at: string
}

type CompanyDetail = {
  id: string
  razao_social: string
  apelido: string
  responsavel_id: string | null
  uf: string | null
  cnpj: string | null
  inscricao_estadual: string | null
  site_url: string | null
  obs: string | null
  created_at: string
  updated_at: string
  stores: StoreOut[]
}

const route = useRoute()
const router = useRouter()
const { api } = useApi()
const company = ref<CompanyDetail | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)
const saveMsg = ref<string | null>(null)

const canEdit = useCan('empresa', 'edit')
const canDelete = useCan('empresa', 'delete')

const STORE_STATUSES: StoreStatus[] = ['active', 'inactive', 'closing', 'banned', 'pending', 'under_review']

// Marketplace → OAuth provider name. Null = no OAuth yet.
const OAUTH_PROVIDER: Record<string, string | null> = {
  ml: 'mercadolivre',
  shopee: 'shopee',
  amazon: 'amazon',
  // Bling is multi-channel and lives at the company level — we surface it via "Bling integration".
  site: null, aliexpress: null, temu: null, tiktok: null, shein: null, magalu: null,
}

type IntegrationRef = { id: string; platform: string; name: string; store_id: string | null }
const integrations = ref<IntegrationRef[]>([])
const blingStoresByIntegration = ref<Record<string, { id: number; nome: string | null }[]>>({})

async function loadIntegrations() {
  try {
    integrations.value = await api<IntegrationRef[]>('/api/integrations')
  } catch { /* ignore */ }
}

const blingIntegrationForCompany = computed(() => {
  if (!company.value) return null
  const myStoreIds = new Set(company.value.stores.map(s => s.id))
  return integrations.value.find(i => i.platform === 'bling' && i.store_id && myStoreIds.has(i.store_id)) || null
})

async function loadBlingStores(integrationId: string) {
  if (blingStoresByIntegration.value[integrationId]) return
  try {
    const r = await api<{ items: { id: number; nome: string | null }[] }>(
      `/api/integrations/${integrationId}/bling-stores`
    )
    blingStoresByIntegration.value[integrationId] = r.items
  } catch { /* ignore */ }
}

watch(blingIntegrationForCompany, (v) => { if (v) loadBlingStores(v.id) }, { immediate: false })

async function load() {
  loading.value = true
  error.value = null
  try {
    company.value = await api<CompanyDetail>(`/api/companies/${route.params.id}`)
    await loadIntegrations()
    if (blingIntegrationForCompany.value) await loadBlingStores(blingIntegrationForCompany.value.id)
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    loading.value = false
  }
}
await load()

async function startOAuth(provider: string, storeId: string) {
  try {
    const r = await api<{ url: string }>(`/api/oauth/${provider}/start?store_id=${storeId}`)
    window.location.href = r.url
  } catch (e: any) {
    error.value = e?.data?.detail?.code || 'erro'
  }
}

async function unlinkStoreIntegration(s: StoreOut) {
  try {
    await api(`/api/stores/${s.id}/unlink-integration`, { method: 'POST' })
    await load()
  } catch (e: any) {
    error.value = e?.data?.detail?.code || 'erro'
  }
}

function storeFor(mk: Marketplace): StoreOut | null {
  return company.value?.stores.find(s => s.marketplace === mk) ?? null
}

async function saveCompany() {
  if (!company.value) return
  saveMsg.value = null
  try {
    const c = company.value
    await api(`/api/companies/${c.id}`, {
      method: 'PATCH',
      body: {
        razao_social: c.razao_social,
        apelido: c.apelido,
        uf: c.uf,
        cnpj: c.cnpj,
        inscricao_estadual: c.inscricao_estadual,
        site_url: c.site_url,
        obs: c.obs,
      },
    })
    saveMsg.value = 'salvo'
    setTimeout(() => (saveMsg.value = null), 2000)
  } catch (e: any) {
    error.value = e?.data?.detail?.code || 'erro'
  }
}

async function createStore(mk: Marketplace) {
  if (!company.value || !canEdit.value) return
  try {
    await api('/api/stores', {
      method: 'POST',
      body: { company_id: company.value.id, marketplace: mk, status: 'pending' },
    })
    await load()
  } catch (e: any) {
    error.value = e?.data?.detail?.code || 'erro'
  }
}

async function patchStore(s: StoreOut, patch: Partial<StoreOut>) {
  try {
    await api(`/api/stores/${s.id}`, { method: 'PATCH', body: patch })
    await load()
  } catch (e: any) {
    error.value = e?.data?.detail?.code || 'erro'
  }
}

async function deleteStore(s: StoreOut) {
  if (!confirm(`Excluir loja ${s.marketplace}? (cascade em cadastros vinculados)`)) return
  try {
    await api(`/api/stores/${s.id}`, { method: 'DELETE' })
    await load()
  } catch (e: any) {
    error.value = e?.data?.detail?.code || 'erro'
  }
}

async function deleteCompany() {
  if (!company.value) return
  if (!confirm('Excluir empresa? Lojas e vínculos serão removidos em cascade.')) return
  if (!confirm('Confirmar novamente — ação irreversível.')) return
  try {
    await api(`/api/companies/${company.value.id}`, { method: 'DELETE' })
    router.push('/companies')
  } catch (e: any) {
    error.value = e?.data?.detail?.code || 'erro'
  }
}
</script>

<template>
  <div v-if="company" class="space-y-6">
    <div class="flex items-center gap-3">
      <NuxtLink to="/companies" class="text-muted-foreground hover:text-foreground">
        <ArrowLeft class="size-5" />
      </NuxtLink>
      <h1 class="text-2xl font-semibold">{{ company.apelido }}</h1>
      <span class="text-sm text-muted-foreground">{{ company.razao_social }}</span>
      <Button v-if="canDelete" class="ml-auto" size="sm" variant="ghost" @click="deleteCompany">
        <Trash2 class="size-4 mr-1" /> excluir
      </Button>
    </div>

    <div v-if="error" class="text-sm text-red-500">erro: {{ error }}</div>

    <section class="border rounded-md p-4 space-y-3">
      <h2 class="font-semibold">Dados cadastrais</h2>
      <div class="grid grid-cols-2 gap-3">
        <div>
          <Label>Razão social</Label>
          <Input v-model="company.razao_social" :disabled="!canEdit" />
        </div>
        <div>
          <Label>Apelido (conta)</Label>
          <Input v-model="company.apelido" :disabled="!canEdit" />
        </div>
        <div>
          <Label>CNPJ</Label>
          <Input v-model="company.cnpj" :disabled="!canEdit" />
        </div>
        <div>
          <Label>UF</Label>
          <Input v-model="company.uf" maxlength="2" :disabled="!canEdit" />
        </div>
        <div>
          <Label>Inscrição estadual</Label>
          <Input v-model="company.inscricao_estadual" :disabled="!canEdit" />
        </div>
        <div>
          <Label>Site</Label>
          <Input v-model="company.site_url" :disabled="!canEdit" />
        </div>
      </div>
      <div>
        <Label>Observação</Label>
        <Input v-model="company.obs" :disabled="!canEdit" />
      </div>
      <div class="flex gap-2 items-center">
        <Button v-if="canEdit" size="sm" @click="saveCompany">
          <Save class="size-4 mr-1" /> salvar
        </Button>
        <span v-if="saveMsg" class="text-xs text-green-400">{{ saveMsg }}</span>
      </div>
    </section>

    <section class="border rounded-md p-4 space-y-3">
      <h2 class="font-semibold">Lojas desta empresa</h2>
      <div class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead class="bg-muted/40 text-left">
            <tr>
              <th class="px-3 py-2">Marketplace</th>
              <th class="px-3 py-2">Status</th>
              <th class="px-3 py-2">Apelido override</th>
              <th class="px-3 py-2">Loja no Bling</th>
              <th class="px-3 py-2">Integração</th>
              <th class="px-3 py-2">Notas</th>
              <th class="px-3 py-2 text-right"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="mk in MARKETPLACES" :key="mk" class="border-t">
              <td class="px-3 py-2 font-medium">{{ MARKETPLACE_LABELS[mk] }}</td>
              <template v-if="storeFor(mk)">
                <td class="px-3 py-2">
                  <select
                    :value="storeFor(mk)!.status"
                    :disabled="!canEdit"
                    class="border rounded px-1 text-xs bg-background"
                    @change="(e: any) => patchStore(storeFor(mk)!, { status: e.target.value })"
                  >
                    <option v-for="st in STORE_STATUSES" :key="st" :value="st">{{ st }}</option>
                  </select>
                </td>
                <td class="px-3 py-2">
                  <Input
                    :model-value="storeFor(mk)!.apelido_override || ''"
                    :disabled="!canEdit"
                    class="h-8"
                    @blur="(e: any) => {
                      const v = e.target.value || null
                      if (v !== storeFor(mk)!.apelido_override) patchStore(storeFor(mk)!, { apelido_override: v })
                    }"
                  />
                </td>
                <td class="px-3 py-2">
                  <select
                    v-if="blingIntegrationForCompany && blingStoresByIntegration[blingIntegrationForCompany.id]"
                    :value="storeFor(mk)!.bling_store_id ?? ''"
                    :disabled="!canEdit"
                    class="border rounded px-1 text-xs bg-background h-8 max-w-44"
                    @change="(e: any) => {
                      const v = e.target.value ? Number(e.target.value) : null
                      if (v !== storeFor(mk)!.bling_store_id) patchStore(storeFor(mk)!, { bling_store_id: v as any })
                    }"
                  >
                    <option value="">— nenhum —</option>
                    <option
                      v-for="bs in blingStoresByIntegration[blingIntegrationForCompany.id]"
                      :key="bs.id"
                      :value="bs.id"
                    >
                      {{ bs.nome || `loja` }} (id: {{ bs.id }})
                    </option>
                  </select>
                  <Input
                    v-else
                    type="number"
                    :model-value="storeFor(mk)!.bling_store_id ?? ''"
                    :disabled="!canEdit"
                    class="h-8 w-32"
                    @blur="(e: any) => {
                      const v = e.target.value ? Number(e.target.value) : null
                      if (v !== storeFor(mk)!.bling_store_id) patchStore(storeFor(mk)!, { bling_store_id: v as any })
                    }"
                  />
                </td>
                <td class="px-3 py-2 text-xs">
                  <span v-if="storeFor(mk)!.integration_id" class="text-green-400">conectada</span>
                  <Button
                    v-else-if="canEdit && OAUTH_PROVIDER[mk]"
                    size="sm"
                    variant="outline"
                    class="h-7"
                    @click="startOAuth(OAUTH_PROVIDER[mk]!, storeFor(mk)!.id)"
                  >
                    <LinkIcon class="size-3 mr-1" /> OAuth
                  </Button>
                  <span v-else class="text-muted-foreground">sem integração</span>
                  <Button
                    v-if="canEdit && storeFor(mk)!.integration_id"
                    size="sm"
                    variant="ghost"
                    class="h-7 ml-1"
                    @click="unlinkStoreIntegration(storeFor(mk)!)"
                  >
                    <Unlink class="size-3" />
                  </Button>
                </td>
                <td class="px-3 py-2">
                  <Input
                    :model-value="storeFor(mk)!.notes || ''"
                    :disabled="!canEdit"
                    class="h-8"
                    @blur="(e: any) => {
                      const v = e.target.value || null
                      if (v !== storeFor(mk)!.notes) patchStore(storeFor(mk)!, { notes: v })
                    }"
                  />
                </td>
                <td class="px-3 py-2 text-right">
                  <Button v-if="canDelete" size="sm" variant="ghost" @click="deleteStore(storeFor(mk)!)">
                    <Trash2 class="size-4" />
                  </Button>
                </td>
              </template>
              <template v-else>
                <td colspan="6" class="px-3 py-2 text-xs text-muted-foreground">— sem loja —</td>
                <td class="px-3 py-2 text-right">
                  <Button v-if="canEdit" size="sm" variant="outline" @click="createStore(mk)">criar</Button>
                </td>
              </template>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="!blingIntegrationForCompany" class="text-xs text-muted-foreground">
        Conecte uma integração Bling em qualquer loja desta empresa para habilitar o select "Loja no Bling".
      </p>
    </section>
  </div>
  <div v-else-if="loading" class="text-muted-foreground">carregando…</div>
  <div v-else class="text-red-500">erro: {{ error || 'não encontrado' }}</div>
</template>
