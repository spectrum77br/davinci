<script setup lang="ts">
import { ref, reactive, computed, watch } from 'vue'
import { ArrowLeft, Save, Trash2, Plus, Unlink } from 'lucide-vue-next'
import {
  MARKETPLACES,
  MARKETPLACE_LABELS,
  type Marketplace,
  type StoreStatus,
} from '~/composables/useMarketplaces'

definePageMeta({ middleware: ['permission'], permission: { resource: 'empresa', action: 'view' } })

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
  operacao: string | null
  contabilidade: string | null
  obs: string | null
  created_at: string
  updated_at: string
  stores: StoreOut[]
}

type CompanyCertificate = {
  id: string
  company_id: string
  filename: string
  content_type: string | null
  size_bytes: number
  label: string | null
  expires_at: string | null
  notes: string | null
  has_password: boolean
  uploaded_by: string | null
  uploaded_by_name: string | null
  created_at: string
  updated_at: string
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

// Marketplace → integration platform (for the manual creds modal).
const MK_TO_INTEGRATION_PLATFORM: Record<string, 'bling' | 'ml' | 'shopee' | 'amazon' | 'tiktok' | 'temu' | 'magalu' | null> = {
  ml: 'ml',
  shopee: 'shopee',
  amazon: 'amazon',
  tiktok: 'tiktok',
  temu: 'temu',
  magalu: 'magalu',
  // Bling is multi-channel and lives at the company level — we surface it separately.
  site: null, aliexpress: null, shein: null,
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

// ---------- certificados digitais (admin only) ----------
const isAdmin = useIsAdmin()
const certificates = ref<CompanyCertificate[]>([])
const certLoading = ref(false)
const certError = ref<string | null>(null)
const certForm = reactive({ password: '', label: '', expires_at: '', notes: '' })
const certFileEl = ref<HTMLInputElement | null>(null)
const certUploading = ref(false)
const revealedPw = ref<Record<string, string>>({})

async function loadCertificates() {
  if (!isAdmin.value || !company.value) return
  certLoading.value = true
  certError.value = null
  try {
    certificates.value = await api<CompanyCertificate[]>(
      `/api/companies/${company.value.id}/certificates`,
    )
  } catch (e: any) {
    certError.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    certLoading.value = false
  }
}

async function uploadCertificate() {
  if (!company.value) return
  const input = certFileEl.value
  const f = input?.files?.[0]
  if (!f) { certError.value = 'Selecione um arquivo .p12 ou .pfx'; return }
  const lower = f.name.toLowerCase()
  if (!lower.endsWith('.p12') && !lower.endsWith('.pfx')) {
    certError.value = 'O arquivo precisa ser .p12 ou .pfx'; return
  }
  certUploading.value = true
  certError.value = null
  try {
    const fd = new FormData()
    fd.append('file', f)
    if (certForm.password) fd.append('password', certForm.password)
    if (certForm.label) fd.append('label', certForm.label)
    if (certForm.expires_at) fd.append('expires_at', certForm.expires_at)
    if (certForm.notes) fd.append('notes', certForm.notes)
    await api(`/api/companies/${company.value.id}/certificates`, { method: 'POST', body: fd })
    certForm.password = ''; certForm.label = ''; certForm.expires_at = ''; certForm.notes = ''
    if (input) input.value = ''
    await loadCertificates()
  } catch (e: any) {
    certError.value = e?.data?.detail?.code || e?.message || 'erro ao enviar'
  } finally {
    certUploading.value = false
  }
}

async function downloadCertificate(cert: CompanyCertificate) {
  if (!company.value) return
  try {
    const blob = await api<Blob>(
      `/api/companies/${company.value.id}/certificates/${cert.id}/download`,
      { responseType: 'blob' as any },
    )
    const href = URL.createObjectURL(blob as any)
    const a = document.createElement('a')
    a.href = href
    a.download = cert.filename
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(href)
  } catch (e: any) {
    certError.value = e?.data?.detail?.code || e?.message || 'erro ao baixar'
  }
}

async function revealPassword(cert: CompanyCertificate) {
  if (!company.value) return
  if (revealedPw.value[cert.id] != null) {
    const cp = { ...revealedPw.value }; delete cp[cert.id]; revealedPw.value = cp
    return
  }
  try {
    const r = await api<{ password: string | null }>(
      `/api/companies/${company.value.id}/certificates/${cert.id}/password`,
    )
    revealedPw.value = { ...revealedPw.value, [cert.id]: r.password || '(sem senha)' }
  } catch (e: any) {
    certError.value = e?.data?.detail?.code || e?.message || 'erro'
  }
}

async function deleteCertificate(cert: CompanyCertificate) {
  if (!company.value) return
  if (!confirm(`Excluir o certificado "${cert.label || cert.filename}"? Não dá pra desfazer.`)) return
  try {
    await api(`/api/companies/${company.value.id}/certificates/${cert.id}`, { method: 'DELETE' })
    await loadCertificates()
  } catch (e: any) {
    certError.value = e?.data?.detail?.code || e?.message || 'erro ao excluir'
  }
}

function fmtBytes(n: number) {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

function isExpired(cert: CompanyCertificate) {
  if (!cert.expires_at) return false
  return new Date(cert.expires_at) < new Date(new Date().toDateString())
}

await loadCertificates()

const showNewIntegration = ref(false)
const newIntegrationStoreId = ref<string | null>(null)
const newIntegrationPlatform = ref<'bling' | 'ml' | 'shopee' | 'amazon' | 'tiktok' | 'temu' | 'magalu' | null>(null)

function openNewIntegration(s: StoreOut) {
  const platform = MK_TO_INTEGRATION_PLATFORM[s.marketplace]
  if (!platform) return
  newIntegrationStoreId.value = s.id
  newIntegrationPlatform.value = platform
  showNewIntegration.value = true
}

async function unlinkStoreIntegration(s: StoreOut) {
  try {
    await api(`/api/stores/${s.id}/unlink-integration`, { method: 'POST' })
    await load()
  } catch (e: any) {
    error.value = e?.data?.detail?.code || 'erro'
  }
}

const MK_TO_PLATFORM: Record<string, string> = {
  ml: 'ml',
  shopee: 'shopee',
  amazon: 'amazon',
}

function availableIntegrationsFor(s: StoreOut): IntegrationRef[] {
  const platform = MK_TO_PLATFORM[s.marketplace]
  if (!platform) return []
  return integrations.value.filter(i =>
    i.platform === platform && (i.store_id == null || i.store_id === s.id)
  )
}

async function attachIntegration(s: StoreOut, integrationId: string) {
  if (!integrationId || integrationId === s.integration_id) return
  try {
    await api(`/api/stores/${s.id}`, {
      method: 'PATCH',
      body: { integration_id: integrationId },
    })
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
        operacao: c.operacao,
        contabilidade: c.contabilidade,
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
        <div>
          <Label>Operação</Label>
          <Input v-model="company.operacao" :disabled="!canEdit" />
        </div>
        <div>
          <Label>Contabilidade</Label>
          <Input v-model="company.contabilidade" :disabled="!canEdit" />
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
                  <div v-if="storeFor(mk)!.integration_id" class="flex items-center gap-1">
                    <span class="text-green-400">
                      {{ integrations.find(i => i.id === storeFor(mk)!.integration_id)?.name || 'conectada' }}
                    </span>
                    <Button
                      v-if="canEdit"
                      size="sm"
                      variant="ghost"
                      class="h-7"
                      title="desvincular"
                      @click="unlinkStoreIntegration(storeFor(mk)!)"
                    >
                      <Unlink class="size-3" />
                    </Button>
                  </div>
                  <div v-else class="flex items-center gap-1 flex-wrap">
                    <select
                      v-if="canEdit && availableIntegrationsFor(storeFor(mk)!).length"
                      class="border rounded px-1 text-xs bg-background h-7 max-w-44"
                      @change="(e: any) => attachIntegration(storeFor(mk)!, e.target.value)"
                    >
                      <option value="">— vincular existente —</option>
                      <option v-for="i in availableIntegrationsFor(storeFor(mk)!)" :key="i.id" :value="i.id">
                        {{ i.name }}
                      </option>
                    </select>
                    <Button
                      v-if="canEdit && MK_TO_INTEGRATION_PLATFORM[mk]"
                      size="sm"
                      variant="outline"
                      class="h-7"
                      @click="openNewIntegration(storeFor(mk)!)"
                    >
                      <Plus class="size-3 mr-1" /> Nova integração
                    </Button>
                    <span v-if="!canEdit || (!MK_TO_INTEGRATION_PLATFORM[mk] && !availableIntegrationsFor(storeFor(mk)!).length)" class="text-muted-foreground">
                      sem integração
                    </span>
                  </div>
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

    <section v-if="isAdmin" class="border rounded-md p-4 space-y-4">
      <div class="flex items-center gap-2 flex-wrap">
        <h2 class="font-semibold">Certificados digitais</h2>
        <span class="text-xs text-muted-foreground">.p12 / .pfx — guardado criptografado, visível só para admin</span>
      </div>

      <div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 items-end border-b border-border pb-4">
        <div class="sm:col-span-2 lg:col-span-1">
          <Label>Arquivo (.p12/.pfx)</Label>
          <input
            ref="certFileEl"
            type="file"
            accept=".p12,.pfx,application/x-pkcs12"
            class="block w-full text-sm text-muted-foreground file:mr-3 file:rounded file:border file:border-input file:bg-background file:px-3 file:py-1 file:text-sm hover:file:bg-accent"
          />
        </div>
        <div>
          <Label>Senha do certificado</Label>
          <Input v-model="certForm.password" type="password" placeholder="opcional" autocomplete="off" />
        </div>
        <div>
          <Label>Rótulo</Label>
          <Input v-model="certForm.label" placeholder="ex.: A1 2026" />
        </div>
        <div>
          <Label>Validade</Label>
          <Input v-model="certForm.expires_at" type="date" />
        </div>
        <div>
          <Label>Notas</Label>
          <Input v-model="certForm.notes" />
        </div>
        <div>
          <Button :disabled="certUploading" @click="uploadCertificate">
            {{ certUploading ? 'enviando…' : 'Enviar certificado' }}
          </Button>
        </div>
      </div>

      <div v-if="certError" class="text-sm text-red-500">erro: {{ certError }}</div>

      <div class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead class="bg-muted/40 text-left">
            <tr>
              <th class="px-3 py-2">Arquivo</th>
              <th class="px-3 py-2">Rótulo</th>
              <th class="px-3 py-2">Validade</th>
              <th class="px-3 py-2">Senha</th>
              <th class="px-3 py-2">Tamanho</th>
              <th class="px-3 py-2">Enviado por</th>
              <th class="px-3 py-2 text-right">Ações</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="cert in certificates" :key="cert.id" class="border-t align-top">
              <td class="px-3 py-2 font-mono text-xs" :title="cert.notes || ''">{{ cert.filename }}</td>
              <td class="px-3 py-2">{{ cert.label || '—' }}</td>
              <td class="px-3 py-2">
                <span v-if="cert.expires_at" :class="isExpired(cert) ? 'text-red-500 font-semibold' : ''">
                  {{ cert.expires_at }}{{ isExpired(cert) ? ' (vencido)' : '' }}
                </span>
                <span v-else class="text-muted-foreground">—</span>
              </td>
              <td class="px-3 py-2 text-xs">
                <template v-if="cert.has_password">
                  <button class="text-blue-500 hover:underline" @click="revealPassword(cert)">
                    {{ revealedPw[cert.id] != null ? 'ocultar' : 'ver' }}
                  </button>
                  <span v-if="revealedPw[cert.id] != null" class="ml-2 font-mono break-all">{{ revealedPw[cert.id] }}</span>
                </template>
                <span v-else class="text-muted-foreground">—</span>
              </td>
              <td class="px-3 py-2 text-xs whitespace-nowrap">{{ fmtBytes(cert.size_bytes) }}</td>
              <td class="px-3 py-2 text-xs">{{ cert.uploaded_by_name || '—' }}</td>
              <td class="px-3 py-2 text-right whitespace-nowrap">
                <Button size="sm" variant="ghost" @click="downloadCertificate(cert)">baixar</Button>
                <Button size="sm" variant="ghost" class="text-destructive" title="excluir" @click="deleteCertificate(cert)">
                  <Trash2 class="size-4" />
                </Button>
              </td>
            </tr>
            <tr v-if="!certLoading && certificates.length === 0">
              <td colspan="7" class="px-3 py-4 text-center text-muted-foreground">nenhum certificado</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <IntegrationFormModal
      v-model:open="showNewIntegration"
      :prefill-store-id="newIntegrationStoreId"
      :prefill-platform="newIntegrationPlatform"
      lock-store
      @created="load"
    />
  </div>
  <div v-else-if="loading" class="text-muted-foreground">carregando…</div>
  <div v-else class="text-red-500">erro: {{ error || 'não encontrado' }}</div>
</template>
