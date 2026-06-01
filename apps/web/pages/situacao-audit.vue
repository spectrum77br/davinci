<script setup lang="ts">
import { ref, onMounted } from 'vue'
import {
  Filter,
  RefreshCw,
  ChevronLeft,
  ChevronRight,
  ArrowRight,
} from 'lucide-vue-next'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'sincronizacoes', action: 'view' },
})

type SituacaoAudit = {
  id: string
  created_at: string
  pedido_bling: string
  bling_id: string | null
  sku: string | null
  situacao_antiga: string | null
  situacao_nova: string
  origem: string
  mudado_por: string | null
  mudado_por_email: string | null
  mudado_por_nome: string | null
}

type Page = { items: SituacaoAudit[]; total: number; limit: number; offset: number }

const { api } = useApi()

const items = ref<SituacaoAudit[]>([])
const total = ref(0)
const limit = ref(50)
const offset = ref(0)
const loading = ref(false)
const error = ref<string | null>(null)

const filtroPedido = ref('')
const filtroOrigem = ref('')

const ORIGEM_LABEL: Record<string, string> = {
  margens: 'Margem (aprovar/reprovar)',
  devolucao: 'Devolução',
  job_envio: 'Job de envio',
}

function origemLabel(o: string) {
  return ORIGEM_LABEL[o] || o
}

function origemPill(o: string) {
  if (o === 'margens') return 'pill pill-success'
  if (o === 'devolucao') return 'pill pill-warning'
  return 'pill pill-muted'
}

function quem(row: SituacaoAudit) {
  if (!row.mudado_por) return 'sistema'
  return row.mudado_por_nome || row.mudado_por_email || row.mudado_por.slice(0, 8)
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const qs = new URLSearchParams()
    qs.set('limit', String(limit.value))
    qs.set('offset', String(offset.value))
    if (filtroPedido.value.trim()) qs.set('pedido_bling', filtroPedido.value.trim())
    if (filtroOrigem.value) qs.set('origem', filtroOrigem.value)
    const r = await api<Page>(`/api/situacao-audit?${qs}`)
    items.value = r.items
    total.value = r.total
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    loading.value = false
  }
}

async function refreshAll() {
  offset.value = 0
  await load()
}

function fmtDate(iso: string) {
  const d = new Date(iso)
  return d.toLocaleString('pt-BR', { hour12: false })
}

function pageNext() {
  if (offset.value + limit.value < total.value) {
    offset.value += limit.value
    void load()
  }
}
function pagePrev() {
  if (offset.value > 0) {
    offset.value = Math.max(0, offset.value - limit.value)
    void load()
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="space-y-5">
    <PageHeader
      title="Auditoria de situações"
      description="Quando e quem mudou a situação de pedidos no Bling pelo app (margem, devolução, job de envio)."
    >
      <template #actions>
        <Button size="sm" variant="outline" @click="refreshAll">
          <RefreshCw class="size-4 mr-1.5" /> recarregar
        </Button>
      </template>
    </PageHeader>

    <div v-if="error" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">
      {{ error }}
    </div>

    <div class="flex flex-wrap gap-2 items-center">
      <Input v-model="filtroPedido" placeholder="nº do pedido…" class="w-48" @keyup.enter="refreshAll" />
      <select v-model="filtroOrigem" class="h-9 rounded-md border bg-background px-2 text-sm" @change="refreshAll">
        <option value="">todas origens</option>
        <option value="margens">Margem (aprovar/reprovar)</option>
        <option value="devolucao">Devolução</option>
        <option value="job_envio">Job de envio</option>
      </select>
      <Button size="sm" variant="outline" @click="refreshAll">
        <Filter class="size-4 mr-1.5" /> aplicar
      </Button>
    </div>

    <div class="table-card">
      <table class="w-full">
        <thead>
          <tr>
            <th>Quando</th>
            <th>Pedido</th>
            <th>SKU</th>
            <th>Situação</th>
            <th>Origem</th>
            <th>Quem</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading && items.length === 0">
            <td colspan="6" class="text-center text-muted-foreground py-6">carregando…</td>
          </tr>
          <tr v-else-if="items.length === 0">
            <td colspan="6" class="text-center text-muted-foreground py-6">sem registros</td>
          </tr>
          <tr v-for="row in items" :key="row.id" class="hover:bg-muted/40">
            <td class="text-xs text-muted-foreground tabular-nums">{{ fmtDate(row.created_at) }}</td>
            <td class="font-mono text-xs">{{ row.pedido_bling }}</td>
            <td class="text-xs">{{ row.sku || '—' }}</td>
            <td class="text-xs tabular-nums">
              <span class="text-muted-foreground">{{ row.situacao_antiga ?? '—' }}</span>
              <ArrowRight class="inline size-3 mx-1 text-muted-foreground" />
              <span class="font-medium">{{ row.situacao_nova }}</span>
            </td>
            <td><span :class="origemPill(row.origem)">{{ origemLabel(row.origem) }}</span></td>
            <td class="text-xs">{{ quem(row) }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="flex items-center justify-between text-sm text-muted-foreground">
      <div>{{ total }} registros</div>
      <div class="flex items-center gap-1">
        <Button size="sm" variant="ghost" :disabled="offset === 0" @click="pagePrev">
          <ChevronLeft class="size-4" />
        </Button>
        <span class="tabular-nums">
          {{ total === 0 ? 0 : offset + 1 }}–{{ Math.min(offset + limit, total) }}
        </span>
        <Button size="sm" variant="ghost" :disabled="offset + limit >= total" @click="pageNext">
          <ChevronRight class="size-4" />
        </Button>
      </div>
    </div>
  </div>
</template>
