<script setup lang="ts">
import { ref, computed } from 'vue'
import { RefreshCw, AlertTriangle } from 'lucide-vue-next'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'anuncios', action: 'view' },
})

type Discrepancy = {
  listing_id: string
  integration_id: string
  platform: string
  external_id: string
  title: string
  sku: string | null
  product_id: string
  expected_stock: number
  actual_stock: number
  diff: number
  last_imported_at: string
}

type Page = { items: Discrepancy[]; total: number }

type Integration = { id: string; platform: string; name: string }

const { api } = useApi()
const items = ref<Discrepancy[]>([])
const total = ref(0)
const integrations = ref<Integration[]>([])
const filtroPlatform = ref('')
const filtroIntegration = ref('')
const minDiff = ref(1)
const page = ref(1)
const pageSize = 50
const loading = ref(false)
const error = ref<string | null>(null)

const PLATFORM_LABEL: Record<string, string> = {
  bling: 'Bling', ml: 'Mercado Livre', shopee: 'Shopee', amazon: 'Amazon',
  aliexpress: 'AliExpress', temu: 'Temu', tiktok: 'TikTok', magalu: 'Magalu',
}

async function loadIntegrations() {
  try {
    integrations.value = await api<Integration[]>('/api/integrations')
  } catch {
    integrations.value = []
  }
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const params = new URLSearchParams()
    if (filtroPlatform.value) params.set('platform', filtroPlatform.value)
    if (filtroIntegration.value) params.set('integration_id', filtroIntegration.value)
    params.set('min_diff', String(minDiff.value))
    params.set('page', String(page.value))
    params.set('page_size', String(pageSize))
    const res = await api<Page>(`/api/discrepancies?${params}`)
    items.value = res.items
    total.value = res.total
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    loading.value = false
  }
}

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))

await loadIntegrations()
await load()

function fmtDate(iso: string) {
  return new Date(iso).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' })
}
</script>

<template>
  <div class="space-y-4">
    <PageHeader
      title="Divergências"
      description="Anúncios cujo estoque na plataforma não bate com o estoque do Bling."
    >
      <template #actions>
        <Button size="sm" variant="ghost" :disabled="loading" @click="load">
          <RefreshCw class="size-4 mr-1" /> recarregar
        </Button>
      </template>
    </PageHeader>

    <div class="flex items-center gap-2 flex-wrap">
      <select
        v-model="filtroPlatform"
        class="border rounded px-2 py-1 text-sm bg-background"
        @change="page = 1; load()"
      >
        <option value="">todas plataformas</option>
        <option v-for="(label, key) in PLATFORM_LABEL" :key="key" :value="key">
          {{ label }}
        </option>
      </select>
      <select
        v-model="filtroIntegration"
        class="border rounded px-2 py-1 text-sm bg-background"
        @change="page = 1; load()"
      >
        <option value="">todas integrações</option>
        <option v-for="i in integrations" :key="i.id" :value="i.id">
          {{ i.name }}
        </option>
      </select>
      <label class="text-xs text-muted-foreground flex items-center gap-1">
        diferença mínima
        <Input
          v-model.number="minDiff"
          type="number"
          min="1"
          class="w-20 h-8"
          @change="page = 1; load()"
        />
      </label>
      <span class="ml-auto text-xs text-muted-foreground">
        {{ total }} divergências
      </span>
    </div>

    <div v-if="error" class="text-sm text-red-500">erro: {{ error }}</div>

    <div class="border rounded-md overflow-x-auto">
      <table class="w-full text-sm">
        <thead class="bg-muted/40 text-left">
          <tr>
            <th class="px-3 py-2">plataforma</th>
            <th class="px-3 py-2">SKU</th>
            <th class="px-3 py-2">anúncio</th>
            <th class="px-3 py-2 text-right">esperado</th>
            <th class="px-3 py-2 text-right">atual</th>
            <th class="px-3 py-2 text-right">diff</th>
            <th class="px-3 py-2">importado</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="d in items" :key="d.listing_id" class="border-t hover:bg-muted/20">
            <td class="px-3 py-2">{{ PLATFORM_LABEL[d.platform] || d.platform }}</td>
            <td class="px-3 py-2 font-mono text-xs">{{ d.sku || '—' }}</td>
            <td class="px-3 py-2 truncate max-w-[480px]" :title="d.title">
              {{ d.title }}
              <div class="text-[11px] text-muted-foreground font-mono">{{ d.external_id }}</div>
            </td>
            <td class="px-3 py-2 text-right">{{ d.expected_stock }}</td>
            <td class="px-3 py-2 text-right">{{ d.actual_stock }}</td>
            <td class="px-3 py-2 text-right">
              <span class="inline-flex items-center gap-1 text-amber-500 font-semibold">
                <AlertTriangle class="size-3" /> {{ d.diff }}
              </span>
            </td>
            <td class="px-3 py-2 text-xs text-muted-foreground">{{ fmtDate(d.last_imported_at) }}</td>
          </tr>
          <tr v-if="!loading && items.length === 0">
            <td colspan="7" class="px-3 py-8 text-center text-muted-foreground">
              nenhuma divergência
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="totalPages > 1" class="flex items-center gap-2 justify-end text-sm">
      <Button size="sm" variant="ghost" :disabled="page <= 1 || loading" @click="page--; load()">
        anterior
      </Button>
      <span class="text-xs text-muted-foreground">
        página {{ page }} de {{ totalPages }}
      </span>
      <Button size="sm" variant="ghost" :disabled="page >= totalPages || loading" @click="page++; load()">
        próxima
      </Button>
    </div>
  </div>
</template>
