<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { X } from 'lucide-vue-next'

type Platform = 'bling' | 'ml' | 'shopee' | 'amazon' | 'tiktok' | 'temu'

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
    fields: [
      { key: 'client_id', label: 'Client ID (App ID)', placeholder: 'ID do seu aplicativo ML', required: true },
      { key: 'client_secret', label: 'Client Secret', type: 'password', required: true },
      { key: 'refresh_token', label: 'Refresh Token', type: 'password', placeholder: 'Token de atualização OAuth', required: true },
      { key: 'access_token', label: 'Access Token', type: 'password', placeholder: 'Token de acesso atual' },
      { key: 'user_id', label: 'User ID (opcional)', placeholder: 'Seu ID de usuário ML' },
    ],
  },
  shopee: {
    label: 'Shopee',
    marketplace: 'shopee',
    fields: [
      { key: 'partner_id', label: 'Partner ID', placeholder: 'Ex: 2012455', required: true },
      { key: 'partner_key', label: 'Partner Key', type: 'password', required: true },
      { key: 'shop_id', label: 'Shop ID', placeholder: 'ID da sua loja Shopee', required: true },
      { key: 'access_token', label: 'Access Token', type: 'password', placeholder: 'Token de acesso Shopee' },
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
  const escaped = s.replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c] as string
  ))
  return escaped.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
}

type Company = { id: string; apelido: string; razao_social: string }
type Store = {
  id: string; company_id: string; marketplace: string; status: string;
  apelido_override: string | null; integration_id: string | null
}

const props = defineProps<{
  open: boolean
  prefillStoreId?: string | null
  prefillPlatform?: Platform | null
  lockStore?: boolean
  stores?: Store[]
  companies?: Company[]
}>()

const emit = defineEmits<{
  (e: 'update:open', v: boolean): void
  (e: 'created', integrationId: string): void
}>()

const { api } = useApi()
const auth = useAuthStore()
const defaultName = computed(() => auth.user?.email || '')

const localStores = ref<Store[]>([])
const localCompanies = ref<Company[]>([])

const stores = computed<Store[]>(() => props.stores ?? localStores.value)
const companies = computed<Company[]>(() => props.companies ?? localCompanies.value)
const companyById = computed(() => Object.fromEntries(companies.value.map(c => [c.id, c])))

async function loadIfNeeded() {
  const tasks: Promise<unknown>[] = []
  if (!props.stores) tasks.push(api<Store[]>('/api/stores').then(r => { localStores.value = r }))
  if (!props.companies) tasks.push(api<Company[]>('/api/companies').then(r => { localCompanies.value = r }))
  if (tasks.length) await Promise.all(tasks)
}

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

watch(() => props.open, async (open) => {
  if (!open) return
  await loadIfNeeded()
  const platform: Platform = props.prefillPlatform || 'bling'
  draft.value = {
    store_id: props.prefillStoreId || '',
    platform,
    name: defaultName.value,
    creds: {},
  }
  resetDraftCreds()
  createErr.value = null
}, { immediate: true })

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
    const r = await api<{ id: string }>('/api/integrations', {
      method: 'POST',
      body: {
        store_id: draft.value.store_id,
        platform: draft.value.platform,
        name: draft.value.name || defaultName.value || `${draft.value.platform}-manual`,
        credentials: creds,
      },
    })
    emit('created', r.id)
    emit('update:open', false)
  } catch (e: any) {
    createErr.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    creating.value = false
  }
}

function close() {
  if (!creating.value) emit('update:open', false)
}
</script>

<template>
  <div v-if="open" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" @click.self="close">
    <div class="bg-background border rounded-lg w-full max-w-lg p-5 space-y-4 max-h-[90vh] overflow-y-auto">
      <div class="flex items-start">
        <div>
          <h2 class="text-lg font-semibold">Nova Integração</h2>
          <p class="text-sm text-muted-foreground">Configure as credenciais de acesso à API do marketplace.</p>
        </div>
        <Button class="ml-auto" size="sm" variant="ghost" @click="close">
          <X class="size-4" />
        </Button>
      </div>

      <div class="space-y-3">
        <div>
          <Label>Plataforma</Label>
          <select
            v-model="draft.platform"
            :disabled="!!prefillPlatform"
            class="w-full border rounded px-2 py-1 bg-background"
          >
            <option v-for="p in PLATFORMS" :key="p" :value="p">{{ PLATFORM_LABELS[p] }}</option>
          </select>
        </div>

        <div v-if="!lockStore">
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
        <Button variant="outline" :disabled="creating" @click="close">Cancelar</Button>
        <Button :disabled="creating || !requiredOk" @click="createIntegration">
          {{ creating ? 'salvando…' : 'Salvar Integração' }}
        </Button>
      </div>
    </div>
  </div>
</template>
