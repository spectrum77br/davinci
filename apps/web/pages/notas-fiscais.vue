<script setup lang="ts">
// Pós vendas: cada pedido ENVIADO com as duas notas fiscais do envio —
// NF EMBALAGEM (conta Bling da empresa dona da loja) e NF PRODUTO (nota
// cheia da conta avulsa). O backend casa pedido ↔ nota pelo espelho local
// `bling_notas_emitidas` (cron), então a página não consulta o Bling ao
// vivo; só o botão XML busca o arquivo na hora.
import {
  AlertCircle,
  ChevronLeft,
  ChevronRight,
  FileDown,
  Loader2,
  Search,
  X,
} from 'lucide-vue-next'
import { isoDaysAgo, isoToday } from '~/lib/date'

definePageMeta({ middleware: ['permission'], permission: { resource: 'notas_fiscais', action: 'view' } })

type PosVendaNf = {
  nota_id: string
  emitente: string | null
  cnpj: string | null
  numero: string | null
  valor: number | null
  data_emissao: string | null
  via: 'pedido' | 'cpf' | null
}

type PosVendaRow = {
  pedido_bling: string
  pedido_marketplace: string | null
  data_envio: string | null
  envio_com_hora: boolean
  loja: string | null
  plataforma: string | null
  sku: string | null
  produto: string | null
  valor: number | null
  nf_embalagem: PosVendaNf | null
  nf_produto: PosVendaNf | null
}

type PosVendasPage = { items: PosVendaRow[]; total: number }

const { api } = useApi()

// ---- carga (server: range de datas do ENVIO; máx. 62 dias) ----
const dateFrom = ref(isoDaysAgo(7))
const dateTo = ref(isoToday())
const rows = ref<PosVendaRow[]>([])
const loading = ref(false)
const error = ref<string | null>(null)

function apiError(e: any) {
  const detail = e?.data?.detail
  if (detail && typeof detail === 'object')
    return detail.message || detail.code || e?.message || 'erro'
  return detail || e?.message || 'erro'
}

async function carregar() {
  if (!dateFrom.value || !dateTo.value) return
  loading.value = true
  error.value = null
  try {
    const params = new URLSearchParams()
    params.set('date_from', dateFrom.value)
    params.set('date_to', dateTo.value)
    const res = await api<PosVendasPage>(`/api/notas-fiscais/pos-vendas?${params.toString()}`)
    rows.value = res.items
  } catch (e: any) {
    error.value = apiError(e)
    rows.value = []
  } finally {
    loading.value = false
  }
}

watch([dateFrom, dateTo], carregar)

// ---- filtros client-side (padrão das outras telas) ----
const search = ref('')
const lojaFilter = ref('all')
const nfFilter = ref('all')

const lojas = computed(() =>
  Array.from(new Set(rows.value.map((r) => r.loja).filter(Boolean) as string[])).sort(),
)

const filteredRows = computed(() => {
  const q = search.value.trim().toLowerCase()
  return rows.value.filter((r) => {
    if (lojaFilter.value !== 'all' && r.loja !== lojaFilter.value) return false
    if (nfFilter.value === 'sem_embalagem' && r.nf_embalagem) return false
    if (nfFilter.value === 'sem_produto' && r.nf_produto) return false
    if (nfFilter.value === 'sem_nf' && (r.nf_embalagem || r.nf_produto)) return false
    if (nfFilter.value === 'completas' && (!r.nf_embalagem || !r.nf_produto)) return false
    if (!q) return true
    const alvo = [
      r.pedido_bling,
      r.pedido_marketplace,
      r.loja,
      r.plataforma,
      r.sku,
      r.produto,
      r.nf_embalagem?.numero,
      r.nf_embalagem?.emitente,
      r.nf_produto?.numero,
      r.nf_produto?.emitente,
    ]
    return alvo.some((v) => (v || '').toLowerCase().includes(q))
  })
})

const filtrosAtivos = computed(
  () => !!search.value.trim() || lojaFilter.value !== 'all' || nfFilter.value !== 'all',
)

function limparFiltros() {
  search.value = ''
  lojaFilter.value = 'all'
  nfFilter.value = 'all'
}

// ---- paginação (client-side; muitas linhas travam o DOM) ----
const PAGE_SIZE = 50
const page = ref(1)
const totalPages = computed(() => Math.max(1, Math.ceil(filteredRows.value.length / PAGE_SIZE)))
const pagedRows = computed(() =>
  filteredRows.value.slice((page.value - 1) * PAGE_SIZE, page.value * PAGE_SIZE),
)
const pageStart = computed(() =>
  filteredRows.value.length ? (page.value - 1) * PAGE_SIZE + 1 : 0,
)
const pageEnd = computed(() => Math.min(page.value * PAGE_SIZE, filteredRows.value.length))
function goToPage(p: number) {
  page.value = Math.min(Math.max(1, p), totalPages.value)
}
watch([search, lojaFilter, nfFilter, dateFrom, dateTo], () => {
  page.value = 1
})
watch(totalPages, (tp) => {
  if (page.value > tp) page.value = tp
})

// ---- download do XML de UMA nota ----
const baixandoXml = ref<Set<string>>(new Set())

async function baixarXml(nf: PosVendaNf) {
  if (baixandoXml.value.has(nf.nota_id)) return
  baixandoXml.value = new Set(baixandoXml.value).add(nf.nota_id)
  error.value = null
  try {
    const blob = await api<Blob>(
      `/api/notas-fiscais/pos-vendas/nota/${nf.nota_id}/xml`,
      { responseType: 'blob' as any },
    )
    const href = URL.createObjectURL(blob as any)
    const a = document.createElement('a')
    a.href = href
    a.download = `NF_${nf.numero || nf.nota_id}.xml`
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(href)
  } catch (e: any) {
    // responseType blob → o corpo do erro chega como Blob; ler pra achar o detail.
    let msg = e?.message || 'erro ao baixar o XML'
    const data = e?.data
    if (data instanceof Blob) {
      try {
        const detail = JSON.parse(await data.text())?.detail
        msg = detail?.message || detail?.code || msg
      } catch { /* mantém msg */ }
    } else if (data?.detail) {
      msg = data.detail.message || data.detail.code || msg
    }
    error.value = `XML da nota ${nf.numero || ''}: ${msg}`
  } finally {
    const next = new Set(baixandoXml.value)
    next.delete(nf.nota_id)
    baixandoXml.value = next
  }
}

// ---- formatação ----
function fmtEnvio(r: PosVendaRow): string {
  if (!r.data_envio) return '—'
  if (!r.envio_com_hora) {
    // Só a data (YYYY-MM-DD) — formatar na mão evita o pulo de fuso do new Date().
    const [y, m, d] = r.data_envio.slice(0, 10).split('-')
    return `${d}/${m}/${y.slice(2)}`
  }
  const dt = new Date(r.data_envio)
  if (Number.isNaN(dt.getTime())) return r.data_envio
  return dt.toLocaleString('pt-BR', {
    day: '2-digit',
    month: '2-digit',
    year: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function brl(v: number | null) {
  if (v == null) return '—'
  return v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
}

function fmtCnpj(v: string | null) {
  const d = (v || '').replace(/\D/g, '')
  if (d.length !== 14) return v || '—'
  return `${d.slice(0, 2)}.${d.slice(2, 5)}.${d.slice(5, 8)}/${d.slice(8, 12)}-${d.slice(12)}`
}

await carregar()
</script>

<template>
  <div class="space-y-4">
    <PageHeader
      title="Pós vendas"
      description="Pedidos enviados com as duas notas fiscais de cada envio: NF embalagem (conta da loja) e NF produto (conta avulsa)."
    />

    <div v-if="error" class="flex items-center gap-2 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-400">
      <AlertCircle class="size-4 shrink-0" />
      {{ error }}
    </div>

    <!-- Busca + filtros -->
    <div class="flex flex-wrap items-center gap-2">
      <div class="relative">
        <Search class="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
        <input
          v-model="search"
          class="h-9 w-72 rounded-md border bg-background pl-8 pr-3 text-sm"
          placeholder="buscar pedido, loja, sku, produto, nota…"
        />
      </div>
      <select v-model="lojaFilter" class="h-9 rounded-md border bg-background px-2 text-sm">
        <option value="all">todas lojas</option>
        <option v-for="l in lojas" :key="l" :value="l">{{ l }}</option>
      </select>
      <select v-model="nfFilter" class="h-9 rounded-md border bg-background px-2 text-sm">
        <option value="all">todas NFs</option>
        <option value="completas">com as duas NFs</option>
        <option value="sem_embalagem">sem NF embalagem</option>
        <option value="sem_produto">sem NF produto</option>
        <option value="sem_nf">sem nenhuma NF</option>
      </select>
      <div class="flex items-center gap-1.5 h-9 rounded-md border bg-background px-2" title="Período da data de envio">
        <span class="text-xs text-muted-foreground">de</span>
        <input
          v-model="dateFrom"
          type="date"
          :max="dateTo || undefined"
          title="Data inicial do envio"
          class="bg-transparent text-sm focus:outline-none"
        />
        <span class="text-xs text-muted-foreground">até</span>
        <input
          v-model="dateTo"
          type="date"
          :min="dateFrom || undefined"
          title="Data final do envio"
          class="bg-transparent text-sm focus:outline-none"
        />
      </div>
      <Button v-if="filtrosAtivos" size="sm" variant="ghost" @click="limparFiltros">
        <X class="size-4 mr-1" /> limpar
      </Button>
      <span class="text-xs text-muted-foreground ml-auto">
        {{ filteredRows.length }} de {{ rows.length }}
      </span>
    </div>

    <!-- Tabela -->
    <div class="border rounded-md overflow-x-auto">
      <table class="w-full text-sm min-w-[1400px] border-collapse [&_th]:border [&_td]:border [&_th]:border-border [&_td]:border-border">
        <thead class="bg-muted/40 text-left">
          <tr class="whitespace-nowrap">
            <th class="px-3 py-2">Data envio</th>
            <th class="px-3 py-2">Pedido Bling</th>
            <th class="px-3 py-2">Pedido Marketplace</th>
            <th class="px-3 py-2">Loja</th>
            <th class="px-3 py-2">SKU</th>
            <th class="px-3 py-2">Produto</th>
            <th class="px-3 py-2">Valor</th>
            <th class="px-3 py-2">NF embalagem</th>
            <th class="px-3 py-2">NF produto</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading">
            <td colspan="9" class="py-10 text-center text-muted-foreground">
              <Loader2 class="size-4 inline animate-spin mr-1.5" />
              carregando envios…
            </td>
          </tr>
          <tr v-else-if="!pagedRows.length">
            <td colspan="9" class="py-10 text-center text-muted-foreground">
              {{ rows.length === 0 ? 'nenhum pedido enviado no período' : 'nenhum pedido com esses filtros' }}
            </td>
          </tr>
          <tr v-for="r in pagedRows" :key="r.pedido_bling" class="border-t align-top hover:bg-muted/20">
            <td class="px-3 py-2 whitespace-nowrap tabular-nums">{{ fmtEnvio(r) }}</td>
            <td class="px-3 py-2 whitespace-nowrap font-medium">{{ r.pedido_bling }}</td>
            <td class="px-3 py-2 whitespace-nowrap font-mono text-xs">{{ r.pedido_marketplace || '—' }}</td>
            <td class="px-3 py-2 whitespace-nowrap">
              {{ r.loja || '—' }}
              <span v-if="r.plataforma" class="text-xs text-muted-foreground">· {{ r.plataforma }}</span>
            </td>
            <td class="px-3 py-2 font-mono text-xs max-w-[160px] break-words">{{ r.sku || '—' }}</td>
            <td class="px-3 py-2 max-w-[260px]">
              <span class="line-clamp-2" :title="r.produto || undefined">{{ r.produto || '—' }}</span>
            </td>
            <td class="px-3 py-2 whitespace-nowrap text-right tabular-nums">{{ brl(r.valor) }}</td>
            <td v-for="lado in (['nf_embalagem', 'nf_produto'] as const)" :key="lado" class="px-3 py-2 min-w-[230px]">
              <template v-if="r[lado]">
                <div class="font-medium text-xs leading-snug">{{ r[lado]!.emitente || 'conta sem nome' }}</div>
                <div class="text-xs text-muted-foreground whitespace-nowrap tabular-nums">
                  {{ fmtCnpj(r[lado]!.cnpj) }}
                </div>
                <div class="mt-0.5 flex items-center gap-2 whitespace-nowrap">
                  <span class="text-xs text-muted-foreground">nº {{ r[lado]!.numero || '—' }}</span>
                  <span class="tabular-nums text-xs">{{ brl(r[lado]!.valor) }}</span>
                  <button
                    class="inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] text-muted-foreground hover:text-foreground hover:bg-muted disabled:opacity-50"
                    :disabled="baixandoXml.has(r[lado]!.nota_id)"
                    title="Baixar o XML da nota"
                    @click="baixarXml(r[lado]!)"
                  >
                    <Loader2 v-if="baixandoXml.has(r[lado]!.nota_id)" class="size-3 animate-spin" />
                    <FileDown v-else class="size-3" />
                    XML
                  </button>
                  <span
                    v-if="r[lado]!.via === 'cpf'"
                    class="text-[10px] text-amber-600 dark:text-amber-400"
                    title="Nota casada pelo CPF do destinatário + data (o pedido do marketplace não estava na nota)"
                  >≈ por CPF</span>
                </div>
              </template>
              <span v-else class="text-muted-foreground">—</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Paginação -->
    <div v-if="filteredRows.length > PAGE_SIZE" class="flex items-center justify-between gap-3 pt-1">
      <span class="text-xs text-muted-foreground">
        {{ pageStart }}–{{ pageEnd }} de {{ filteredRows.length }}
      </span>
      <div class="flex items-center gap-1">
        <Button size="sm" variant="outline" :disabled="page <= 1" @click="goToPage(page - 1)">
          <ChevronLeft class="size-4" />
        </Button>
        <span class="text-xs text-muted-foreground px-2 whitespace-nowrap">
          página {{ page }} de {{ totalPages }}
        </span>
        <Button size="sm" variant="outline" :disabled="page >= totalPages" @click="goToPage(page + 1)">
          <ChevronRight class="size-4" />
        </Button>
      </div>
    </div>
  </div>
</template>
