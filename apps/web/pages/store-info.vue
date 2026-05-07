<script setup lang="ts">
import { ref, computed } from 'vue'
import { Plus, RefreshCw, Trash2, X, Pencil } from 'lucide-vue-next'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'tabela_precos', action: 'view' },
})

type StoreInfo = {
  id: string
  user_id: string
  platform: string
  segment: string | null
  freight: string | null
  cpf_name: string | null
  account_name: string | null
  server: string | null
  cnpj: string | null
  email: string | null
  observation: string | null
  shipping_address: string | null
  return_address: string | null
  phone: string | null
  link: string | null
  sort_order: number
  has_password: boolean
  created_at: string
  updated_at: string
}

type Draft = {
  platform: string
  segment: string
  freight: string
  cpf_name: string
  account_name: string
  server: string
  cnpj: string
  email: string
  phone: string
  link: string
  shipping_address: string
  return_address: string
  observation: string
  password: string
  sort_order: number
}

const PLATFORMS = [
  { value: 'mercadolivre', label: 'Mercado Livre' },
  { value: 'shopee', label: 'Shopee' },
  { value: 'amazon', label: 'Amazon' },
  { value: 'aliexpress', label: 'AliExpress' },
  { value: 'temu', label: 'Temu' },
  { value: 'tiktok', label: 'TikTok' },
  { value: 'magalu', label: 'Magalu' },
]

const DEPARTMENTS = [
  { value: 'celular', label: 'Celular' },
  { value: 'mala', label: 'Mala' },
  { value: 'eletro', label: 'Eletro' },
  { value: 'catalogo', label: 'Catálogo' },
]

const { api } = useApi()
const canEdit = useCan('tabela_precos', 'edit')
const canDelete = useCan('tabela_precos', 'delete')

const items = ref<StoreInfo[]>([])
const loading = ref(false)
const error = ref<string | null>(null)

const editing = ref<StoreInfo | null>(null)
const showForm = ref(false)
const saving = ref(false)
const formError = ref<string | null>(null)

function emptyDraft(): Draft {
  return {
    platform: 'mercadolivre',
    segment: '', freight: '', cpf_name: '', account_name: '',
    server: '', cnpj: '', email: '', phone: '', link: '',
    shipping_address: '', return_address: '', observation: '',
    password: '', sort_order: 0,
  }
}
const draft = ref<Draft>(emptyDraft())

async function load() {
  loading.value = true
  error.value = null
  try {
    items.value = await api<StoreInfo[]>('/api/pricing/store-info')
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    loading.value = false
  }
}

await load()

function openNew() {
  editing.value = null
  draft.value = emptyDraft()
  formError.value = null
  showForm.value = true
}

function openEdit(row: StoreInfo) {
  editing.value = row
  draft.value = {
    platform: row.platform,
    segment: row.segment || '',
    freight: row.freight || '',
    cpf_name: row.cpf_name || '',
    account_name: row.account_name || '',
    server: row.server || '',
    cnpj: row.cnpj || '',
    email: row.email || '',
    phone: row.phone || '',
    link: row.link || '',
    shipping_address: row.shipping_address || '',
    return_address: row.return_address || '',
    observation: row.observation || '',
    password: '',
    sort_order: row.sort_order,
  }
  formError.value = null
  showForm.value = true
}

function payloadFromDraft() {
  const body: Record<string, unknown> = { platform: draft.value.platform, sort_order: draft.value.sort_order }
  for (const k of [
    'segment','freight','cpf_name','account_name','server','cnpj','email',
    'phone','link','shipping_address','return_address','observation',
  ] as const) {
    const v = (draft.value[k] || '').trim()
    if (v) body[k] = v
  }
  if (draft.value.password) body.password = draft.value.password
  return body
}

async function save() {
  saving.value = true
  formError.value = null
  try {
    if (editing.value) {
      await api(`/api/pricing/store-info/${editing.value.id}`, {
        method: 'PATCH', body: payloadFromDraft(),
      })
    } else {
      await api('/api/pricing/store-info', { method: 'POST', body: payloadFromDraft() })
    }
    showForm.value = false
    await load()
  } catch (e: any) {
    formError.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    saving.value = false
  }
}

async function remove(row: StoreInfo) {
  if (!confirm(`Excluir loja ${row.account_name || row.platform}?`)) return
  try {
    await api(`/api/pricing/store-info/${row.id}`, { method: 'DELETE' })
    await load()
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  }
}

async function setDepartment(row: StoreInfo, department: string) {
  if (!department) return
  try {
    await api(`/api/pricing/store-info/${row.id}/department`, {
      method: 'POST', body: { department },
    })
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  }
}

const sorted = computed(() =>
  [...items.value].sort((a, b) =>
    (a.sort_order - b.sort_order) || a.platform.localeCompare(b.platform),
  ),
)
</script>

<template>
  <div class="space-y-4">
    <PageHeader
      title="Lojas (info)"
      description="Cadastros de lojas externas — usados pela tabela de preços e relatórios."
    >
      <template #actions>
        <Button size="sm" variant="ghost" :disabled="loading" @click="load">
          <RefreshCw class="size-4 mr-1" /> recarregar
        </Button>
        <Button v-if="canEdit" size="sm" @click="openNew">
          <Plus class="size-4 mr-1" /> Nova loja
        </Button>
      </template>
    </PageHeader>

    <div v-if="error" class="text-sm text-red-500">erro: {{ error }}</div>

    <div class="border rounded-md overflow-x-auto">
      <table class="w-full text-sm">
        <thead class="bg-muted/40 text-left">
          <tr>
            <th class="px-3 py-2">plataforma</th>
            <th class="px-3 py-2">conta</th>
            <th class="px-3 py-2">segmento</th>
            <th class="px-3 py-2">CNPJ</th>
            <th class="px-3 py-2">e-mail</th>
            <th class="px-3 py-2">criar conta de preço</th>
            <th class="px-3 py-2 text-right">ações</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in sorted" :key="row.id" class="border-t hover:bg-muted/20">
            <td class="px-3 py-2">{{ row.platform }}</td>
            <td class="px-3 py-2">{{ row.account_name || '—' }}</td>
            <td class="px-3 py-2 text-muted-foreground">{{ row.segment || '—' }}</td>
            <td class="px-3 py-2 font-mono text-xs">{{ row.cnpj || '—' }}</td>
            <td class="px-3 py-2 text-xs">{{ row.email || '—' }}</td>
            <td class="px-3 py-2">
              <select
                v-if="canEdit"
                class="border rounded px-2 py-1 text-xs bg-background"
                :disabled="!canEdit"
                @change="(e: Event) => {
                  const v = (e.target as HTMLSelectElement).value
                  if (v) {
                    setDepartment(row, v)
                    ;(e.target as HTMLSelectElement).value = ''
                  }
                }"
              >
                <option value="">selecionar dept…</option>
                <option v-for="d in DEPARTMENTS" :key="d.value" :value="d.value">{{ d.label }}</option>
              </select>
              <span v-else class="text-xs text-muted-foreground">—</span>
            </td>
            <td class="px-3 py-2 text-right">
              <Button v-if="canEdit" size="sm" variant="ghost" @click="openEdit(row)">
                <Pencil class="size-4" />
              </Button>
              <Button v-if="canDelete" size="sm" variant="ghost" class="text-red-500" @click="remove(row)">
                <Trash2 class="size-4" />
              </Button>
            </td>
          </tr>
          <tr v-if="!loading && sorted.length === 0">
            <td colspan="7" class="px-3 py-8 text-center text-muted-foreground">
              nenhuma loja cadastrada
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div
      v-if="showForm"
      class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4"
      @click.self="showForm = false"
    >
      <div class="bg-background border rounded-lg w-full max-w-3xl p-5 space-y-4 max-h-[90vh] overflow-auto">
        <div class="flex items-center">
          <h2 class="text-lg font-semibold">
            {{ editing ? 'Editar loja' : 'Nova loja' }}
          </h2>
          <Button class="ml-auto" size="sm" variant="ghost" @click="showForm = false">
            <X class="size-4" />
          </Button>
        </div>

        <div class="grid grid-cols-2 gap-3">
          <div>
            <Label>Plataforma *</Label>
            <select v-model="draft.platform" class="w-full border rounded px-2 py-1 bg-background">
              <option v-for="p in PLATFORMS" :key="p.value" :value="p.value">{{ p.label }}</option>
            </select>
          </div>
          <div>
            <Label>Nome da conta</Label>
            <Input v-model="draft.account_name" />
          </div>
          <div>
            <Label>Segmento</Label>
            <Input v-model="draft.segment" />
          </div>
          <div>
            <Label>Frete</Label>
            <Input v-model="draft.freight" />
          </div>
          <div>
            <Label>CPF / responsável</Label>
            <Input v-model="draft.cpf_name" />
          </div>
          <div>
            <Label>CNPJ</Label>
            <Input v-model="draft.cnpj" />
          </div>
          <div>
            <Label>Servidor</Label>
            <Input v-model="draft.server" />
          </div>
          <div>
            <Label>E-mail</Label>
            <Input v-model="draft.email" />
          </div>
          <div>
            <Label>Telefone</Label>
            <Input v-model="draft.phone" />
          </div>
          <div>
            <Label>Link</Label>
            <Input v-model="draft.link" />
          </div>
          <div class="col-span-2">
            <Label>Endereço de envio</Label>
            <Input v-model="draft.shipping_address" />
          </div>
          <div class="col-span-2">
            <Label>Endereço de devolução</Label>
            <Input v-model="draft.return_address" />
          </div>
          <div class="col-span-2">
            <Label>Observação</Label>
            <Input v-model="draft.observation" />
          </div>
          <div>
            <Label>Senha {{ editing ? '(deixe em branco p/ manter)' : '' }}</Label>
            <Input v-model="draft.password" type="password" />
          </div>
          <div>
            <Label>Ordem</Label>
            <Input v-model.number="draft.sort_order" type="number" />
          </div>
        </div>

        <div v-if="formError" class="text-sm text-red-500">erro: {{ formError }}</div>
        <div class="flex justify-end gap-2">
          <Button variant="ghost" :disabled="saving" @click="showForm = false">cancelar</Button>
          <Button :disabled="saving || !draft.platform" @click="save">
            {{ saving ? 'salvando…' : editing ? 'Salvar' : 'Criar' }}
          </Button>
        </div>
      </div>
    </div>
  </div>
</template>
