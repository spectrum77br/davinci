<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { Plus, RefreshCw, Trash2, Zap, X } from 'lucide-vue-next'

definePageMeta({ middleware: ['permission'], permission: { resource: 'empresa', action: 'view' } })

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

type Field = {
  key: string
  label: string
  placeholder?: string
  type?: 'text' | 'password'
  required?: boolean
}
type PlatformSpec = {
  label: string
  marketplace: string
  hint?: { tone: 'info' | 'warn' | 'oauth-ml' | 'oauth-shopee' | 'tiktok' | 'temu'; lines: string[] }
  fields: Field[]
}

const PLATFORM_SPECS: Record<Platform, PlatformSpec> = {
  bling: {
    label: 'Bling (ERP)',
    marketplace: 'site',
    fields: [
      { key: 'api_key', label: 'API Key (Bearer Token)', type: 'password', required: true },
    ],
  },
  ml: {
    label: 'Mercado Livre',
    marketplace: 'ml',
    hint: {
      tone: 'oauth-ml',
      lines: [
        '**Passo 1:** Preencha o Client ID e Client Secret abaixo e salve.',
        '**Passo 2:** Após salvar, clique em "Autorizar ML" no card da integração para conectar via OAuth.',
      ],
    },
    fields: [
      { key: 'client_id', label: 'Client ID (App ID)', placeholder: 'ID do seu aplicativo ML', required: true },
      { key: 'client_secret', label: 'Client Secret', type: 'password', required: true },
      { key: 'refresh_token', label: 'Refresh Token', placeholder: 'Token de atualização OAuth' },
      { key: 'user_id', label: 'User ID (opcional)', placeholder: 'Seu ID de usuário ML' },
    ],
  },
  shopee: {
    label: 'Shopee',
    marketplace: 'shopee',
    hint: {
      tone: 'oauth-shopee',
      lines: [
        '**Passo 1:** Preencha o Partner ID, Partner Key e Shop ID abaixo e salve.',
        '**Passo 2:** Após salvar, clique em "Autorizar Shopee" no card da integração para obter o Access Token automaticamente via OAuth.',
      ],
    },
    fields: [
      { key: 'partner_id', label: 'Partner ID', placeholder: 'Ex: 2012455', required: true },
      { key: 'partner_key', label: 'Partner Key', type: 'password', required: true },
      { key: 'shop_id', label: 'Shop ID', placeholder: 'ID da sua loja Shopee', required: true },
    ],
  },
  amazon: {
    label: 'Amazon',
    marketplace: 'amazon',
    fields: [
      { key: 'seller_id', label: 'Seller ID', placeholder: 'Seu ID de vendedor Amazon', required: true },
      { key: 'marketplace_id', label: 'Marketplace ID', placeholder: 'Ex: A2Q3Y263D00KWC (Brasil)', required: true },
      { key: 'lwa_app_id', label: 'LWA Client ID', placeholder: 'Client ID do app LWA', required: true },
      { key: 'lwa_client_secret', label: 'LWA Client Secret', type: 'password', required: true },
      { key: 'refresh_token', label: 'Refresh Token', placeholder: 'Token de atualização OAuth', required: true },
      { key: 'region', label: 'Região', placeholder: 'Ex: us-east-1' },
    ],
  },
  tiktok: {
    label: 'TikTok Shop',
    marketplace: 'tiktok',
    hint: {
      tone: 'tiktok',
      lines: [
        '**TikTok Shop:** Obtenha as credenciais no TikTok Shop Partner Center.',
        'O App Key, App Secret e Shop Cipher são obtidos ao criar um app. O Access Token é gerado via autorização OAuth.',
      ],
    },
    fields: [
      { key: 'app_key', label: 'App Key', placeholder: 'App Key do TikTok Shop Partner Center', required: true },
      { key: 'app_secret', label: 'App Secret', type: 'password', required: true },
      { key: 'access_token', label: 'Access Token', placeholder: 'Token de acesso da API', required: true },
      { key: 'shop_cipher', label: 'Shop Cipher', placeholder: 'Cipher da loja (obtido via Auth)', required: true },
    ],
  },
  temu: {
    label: 'Temu',
    marketplace: 'temu',
    hint: {
      tone: 'temu',
      lines: [
        '**Temu:** Obtenha as credenciais na Temu Open Platform (partner.temu.com).',
        'Crie um app para obter App Key e App Secret. O Access Token é gerado via autorização. Região padrão: global.',
      ],
    },
    fields: [
      { key: 'app_key', label: 'App Key', placeholder: 'App Key da Temu Open Platform', required: true },
      { key: 'app_secret', label: 'App Secret', type: 'password', required: true },
      { key: 'access_token', label: 'Access Token', placeholder: 'Token de acesso da API', required: true },
      { key: 'region', label: 'Região', placeholder: 'global, us ou eu (padrão: global)' },
    ],
  },
}

const PLATFORMS = Object.keys(PLATFORM_SPECS) as Platform[]
const PLATFORM_LABELS: Record<string, string> = Object.fromEntries(
  (Object.entries(PLATFORM_SPECS) as [Platform, PlatformSpec][]).map(([k, v]) => [k, v.label]),
)
const PLATFORM_MK: Record<string, string> = Object.fromEntries(
  (Object.entries(PLATFORM_SPECS) as [Platform, PlatformSpec][]).map(([k, v]) => [k, v.marketplace]),
)

const HINT_CLASSES: Record<NonNullable<PlatformSpec['hint']>['tone'], string> = {
  info: 'border-blue-500/40 bg-blue-500/5 text-blue-300',
  warn: 'border-amber-500/40 bg-amber-500/5 text-amber-300',
  'oauth-ml': 'border-yellow-500/40 bg-yellow-500/5 text-yellow-300',
  'oauth-shopee': 'border-orange-500/40 bg-orange-500/5 text-orange-300',
  tiktok: 'border-pink-500/40 bg-pink-500/5 text-pink-300',
  temu: 'border-purple-500/40 bg-purple-500/5 text-purple-300',
}

function renderHintLine(s: string): string {
  // bold **xxx** segments only — no other HTML allowed
  const escaped = s.replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c] as string
  ))
  return escaped.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
}

const { api } = useApi()
const items = ref<Integration[]>([])
const companies = ref<Company[]>([])
const stores = ref<Store[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const testingId = ref<string | null>(null)
const showNew = ref(false)

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

const grouped = computed(() => {
  const map: Record<string, Integration[]> = {}
  for (const i of items.value) {
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

async function deleteIntegration(i: Integration) {
  if (!confirm(`Excluir integração ${i.name}?`)) return
  try {
    await api(`/api/integrations/${i.id}`, { method: 'DELETE' })
    await refresh()
  } catch (e: any) {
    error.value = e?.data?.detail?.code || 'erro'
  }
}

const oauthStarting = ref<string | null>(null)
async function startOAuth(platform: string, storeId: string) {
  oauthStarting.value = storeId
  try {
    const r = await api<{ url: string }>(
      `/api/oauth/${platform}/start?store_id=${storeId}`
    )
    window.location.href = r.url
  } catch (e: any) {
    error.value = e?.data?.detail?.code || 'erro'
    oauthStarting.value = null
  }
}

// New manual integration modal
const auth = useAuthStore()
const defaultName = computed(() => auth.user?.email || '')

const draft = ref<{ store_id: string; platform: Platform; name: string; creds: Record<string, string> }>({
  store_id: '', platform: 'bling', name: '', creds: {},
})
const creating = ref(false)
const createErr = ref<string | null>(null)

const currentSpec = computed<PlatformSpec>(() => PLATFORM_SPECS[draft.value.platform])

const eligibleStores = computed(() => {
  const mk = PLATFORM_MK[draft.value.platform]
  return stores.value.filter(s =>
    s.integration_id === null && (s.marketplace === mk || draft.value.platform === 'bling')
  )
})

function resetDraftCreds() {
  const next: Record<string, string> = {}
  for (const f of currentSpec.value.fields) next[f.key] = ''
  draft.value.creds = next
}

watch(() => draft.value.platform, resetDraftCreds)

watch(showNew, (open) => {
  if (open) {
    draft.value = {
      store_id: '',
      platform: 'bling',
      name: defaultName.value,
      creds: {},
    }
    resetDraftCreds()
    createErr.value = null
  }
})

const requiredOk = computed(() => {
  if (!draft.value.store_id) return false
  return currentSpec.value.fields.every(f => !f.required || (draft.value.creds[f.key] || '').trim() !== '')
})

async function createIntegration() {
  creating.value = true
  createErr.value = null
  try {
    const creds: Record<string, string> = {}
    for (const f of currentSpec.value.fields) {
      const v = (draft.value.creds[f.key] || '').trim()
      if (v) creds[f.key] = v
    }
    await api('/api/integrations', {
      method: 'POST',
      body: {
        store_id: draft.value.store_id,
        platform: draft.value.platform,
        name: draft.value.name || defaultName.value || `${draft.value.platform}-manual`,
        credentials: creds,
      },
    })
    showNew.value = false
    await refresh()
  } catch (e: any) {
    createErr.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    creating.value = false
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
      <Button v-if="canEdit" class="ml-auto" size="sm" @click="showNew = true">
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
          <div class="flex gap-2 pt-1">
            <Button size="sm" variant="outline" :disabled="testingId === i.id" @click="testIntegration(i)">
              <Zap class="size-3 mr-1" /> {{ testingId === i.id ? 'testando…' : 'testar' }}
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

    <!-- OAuth quick-connect by store (Bling only for now) -->
    <section v-if="canEdit" class="border rounded-md p-4 space-y-2">
      <h2 class="font-semibold">Conectar OAuth (Bling)</h2>
      <p class="text-xs text-muted-foreground">Lojas sem integração:</p>
      <div class="flex flex-wrap gap-2">
        <Button
          v-for="s in stores.filter(s => s.integration_id === null)"
          :key="s.id"
          size="sm"
          variant="outline"
          :disabled="oauthStarting === s.id"
          @click="startOAuth('bling', s.id)"
        >
          {{ companyById[s.company_id]?.apelido || '?' }} / {{ s.marketplace }}
          {{ oauthStarting === s.id ? '…' : '' }}
        </Button>
        <span v-if="!stores.some(s => s.integration_id === null)" class="text-xs text-muted-foreground">
          todas as lojas já estão conectadas
        </span>
      </div>
      <p class="text-xs text-muted-foreground">
        ML / Shopee / Amazon: pendente (Fase 2 ext.)
      </p>
    </section>

    <!-- Manual create modal -->
    <div v-if="showNew" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" @click.self="showNew = false">
      <div class="bg-background border rounded-lg w-full max-w-lg p-5 space-y-4 max-h-[90vh] overflow-y-auto">
        <div class="flex items-start">
          <div>
            <h2 class="text-lg font-semibold">Nova Integração</h2>
            <p class="text-sm text-muted-foreground">Configure as credenciais de acesso à API do marketplace.</p>
          </div>
          <Button class="ml-auto" size="sm" variant="ghost" @click="showNew = false">
            <X class="size-4" />
          </Button>
        </div>

        <div class="space-y-3">
          <div>
            <Label>Plataforma</Label>
            <select v-model="draft.platform" class="w-full border rounded px-2 py-1 bg-background">
              <option v-for="p in PLATFORMS" :key="p" :value="p">{{ PLATFORM_LABELS[p] }}</option>
            </select>
          </div>

          <div>
            <Label>Loja <span class="text-red-500">*</span></Label>
            <select v-model="draft.store_id" class="w-full border rounded px-2 py-1 bg-background">
              <option value="">— selecione —</option>
              <option v-for="s in eligibleStores" :key="s.id" :value="s.id">
                {{ companyById[s.company_id]?.apelido || '?' }} / {{ s.marketplace }}
              </option>
            </select>
          </div>

          <div>
            <Label>Nome da Integração</Label>
            <Input v-model="draft.name" :placeholder="defaultName" />
          </div>

          <div
            v-if="currentSpec.hint"
            class="border rounded-md p-3 text-xs space-y-1"
            :class="HINT_CLASSES[currentSpec.hint.tone]"
          >
            <p
              v-for="(line, idx) in currentSpec.hint.lines"
              :key="idx"
              v-html="renderHintLine(line)"
            />
          </div>

          <div v-for="f in currentSpec.fields" :key="f.key">
            <Label>
              {{ f.label }}
              <span v-if="f.required" class="text-red-500">*</span>
            </Label>
            <Input
              v-model="draft.creds[f.key]"
              :type="f.type === 'password' ? 'password' : 'text'"
              :placeholder="f.placeholder || ''"
            />
          </div>

          <p class="text-xs text-muted-foreground">
            Credenciais armazenadas cifradas (AES-GCM).
          </p>
        </div>

        <div v-if="createErr" class="text-sm text-red-500">erro: {{ createErr }}</div>
        <div class="flex justify-end gap-2">
          <Button variant="outline" :disabled="creating" @click="showNew = false">Cancelar</Button>
          <Button :disabled="creating || !requiredOk" @click="createIntegration">
            {{ creating ? 'salvando…' : 'Salvar Integração' }}
          </Button>
        </div>
      </div>
    </div>
  </div>
</template>
