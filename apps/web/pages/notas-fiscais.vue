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
  etiqueta_emissao: string | null
  etiqueta_impressao: string | null
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

// Data+hora das colunas de etiqueta (timestamps ISO vindos do banco).
function fmtDataHora(iso: string | null): string {
  if (!iso) return '—'
  const dt = new Date(iso)
  if (Number.isNaN(dt.getTime())) return iso
  return dt.toLocaleString('pt-BR', {
    day: '2-digit',
    month: '2-digit',
    year: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

// Os dois grupos de NF da tabela (colunas idênticas, cor do grupo diferente —
// mesmo esquema visual da tela de Margem: Frete âmbar, Saldo verde).
// `head` pinta o cabeçalho do grupo; `tint` pinta as células do corpo (mais suave).
const LADOS = [
  {
    campo: 'nf_embalagem',
    titulo: 'NF embalagem',
    head: 'bg-amber-50 dark:bg-amber-900/20',
    tint: 'bg-amber-50/40 dark:bg-amber-900/10',
  },
  {
    campo: 'nf_produto',
    titulo: 'NF produto',
    head: 'bg-emerald-50 dark:bg-emerald-900/20',
    tint: 'bg-emerald-50/40 dark:bg-emerald-900/10',
  },
] as const

// Borda grossa que separa os grupos de colunas (igual à Margem).
const SEP = 'border-l-[3px] border-gray-400 dark:border-gray-600'

// Grupo Etiqueta: azul (pra não confundir com o âmbar/verde das NFs) e uma
// linha fina interna separando Emissão de Impressão.
const ETQ_HEAD = 'bg-sky-50 dark:bg-sky-900/20'
const ETQ_TINT = 'bg-sky-50/40 dark:bg-sky-900/10'
const SEP_FINA = 'border-l border-gray-300 dark:border-gray-600'

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

    <!-- Tabela (cabeçalho agrupado em 2 níveis, estilo da tela de Margem) -->
    <div class="overflow-auto rounded border max-h-[75vh]">
      <table class="w-full text-xs border-collapse">
        <thead class="bg-background sticky top-0 z-20">
          <!-- Nível 1: grupos -->
          <tr>
            <th class="px-2 py-1 text-left text-[11px] font-semibold border-b" colspan="3">Identificação</th>
            <th :class="['px-2 py-1 text-left text-[11px] font-semibold border-b', SEP]" colspan="4">Anúncio</th>
            <th
              v-for="lado in LADOS"
              :key="lado.campo"
              :class="['px-2 py-1 text-center text-[11px] font-semibold border-b', SEP, lado.head]"
              colspan="4"
            >{{ lado.titulo }}</th>
            <th :class="['px-2 py-1 text-center text-[11px] font-semibold border-b', SEP, ETQ_HEAD]" colspan="2">Etiqueta</th>
          </tr>
          <!-- Nível 2: colunas -->
          <tr class="border-b">
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[90px]">Data envio</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[80px]">Pedido Bling</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[130px]">Pedido Marketplace</th>
            <th :class="['px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px]', SEP]">Loja</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[90px]">SKU</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[200px]">Produto</th>
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[80px]">Valor</th>
            <template v-for="lado in LADOS" :key="lado.campo">
              <th :class="['px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[170px]', SEP, lado.head]">Nome</th>
              <th :class="['px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[130px]', lado.head]">CNPJ</th>
              <th :class="['px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[85px]', lado.head]">Valor</th>
              <th :class="['px-2 py-1 text-center font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[60px]', lado.head]">XML</th>
            </template>
            <th
              :class="['px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[105px]', SEP, ETQ_HEAD]"
              title="Quando a etiqueta ficou pronta e o envio foi liberado pro despacho"
            >Emissão</th>
            <th
              :class="['px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[105px]', SEP_FINA, ETQ_HEAD]"
              title="Quando a etiqueta foi impressa pela primeira vez"
            >Impressão</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading">
            <td colspan="17" class="py-10 text-center text-muted-foreground">
              <Loader2 class="size-4 inline animate-spin mr-1.5" />
              carregando envios…
            </td>
          </tr>
          <tr v-else-if="!pagedRows.length">
            <td colspan="17" class="py-10 text-center text-muted-foreground">
              {{ rows.length === 0 ? 'nenhum pedido enviado no período' : 'nenhum pedido com esses filtros' }}
            </td>
          </tr>
          <tr
            v-for="r in pagedRows"
            :key="r.pedido_bling"
            class="border-t align-top hover:brightness-95 dark:hover:brightness-110"
          >
            <td class="px-2 py-1 whitespace-nowrap tabular-nums">{{ fmtEnvio(r) }}</td>
            <td class="px-2 py-1 whitespace-nowrap font-medium">{{ r.pedido_bling }}</td>
            <td class="px-2 py-1 whitespace-nowrap font-mono text-[11px]">{{ r.pedido_marketplace || '—' }}</td>
            <td :class="['px-2 py-1 whitespace-nowrap', SEP]">
              {{ r.loja || '—' }}
              <span v-if="r.plataforma" class="text-[10px] text-muted-foreground">· {{ r.plataforma }}</span>
            </td>
            <td class="px-2 py-1 font-mono text-[11px] max-w-[160px] break-words">{{ r.sku || '—' }}</td>
            <td class="px-2 py-1 max-w-[260px]">
              <span class="line-clamp-2" :title="r.produto || undefined">{{ r.produto || '—' }}</span>
            </td>
            <td class="px-2 py-1 whitespace-nowrap text-right tabular-nums">{{ brl(r.valor) }}</td>
            <template v-for="lado in LADOS" :key="lado.campo">
              <!-- Nome (emitente) + nº da nota -->
              <td :class="['px-2 py-1', SEP, lado.tint]">
                <template v-if="r[lado.campo]">
                  <div class="font-medium leading-snug">{{ r[lado.campo]!.emitente || 'conta sem nome' }}</div>
                  <div class="text-[10px] text-muted-foreground whitespace-nowrap">
                    NF {{ r[lado.campo]!.numero || '—' }}
                    <span
                      v-if="r[lado.campo]!.via === 'cpf'"
                      class="text-amber-600 dark:text-amber-400"
                      title="Nota casada pelo CPF do destinatário + data (o pedido do marketplace não estava na nota)"
                    >· ≈ por CPF</span>
                  </div>
                </template>
                <span v-else class="text-muted-foreground">—</span>
              </td>
              <!-- CNPJ -->
              <td :class="['px-2 py-1 whitespace-nowrap tabular-nums', lado.tint]">
                <span v-if="r[lado.campo]">{{ fmtCnpj(r[lado.campo]!.cnpj) }}</span>
                <span v-else class="text-muted-foreground">—</span>
              </td>
              <!-- Valor da nota -->
              <td :class="['px-2 py-1 whitespace-nowrap text-right tabular-nums', lado.tint]">
                <span v-if="r[lado.campo]">{{ brl(r[lado.campo]!.valor) }}</span>
                <span v-else class="text-muted-foreground">—</span>
              </td>
              <!-- XML -->
              <td :class="['px-2 py-1 text-center whitespace-nowrap', lado.tint]">
                <button
                  v-if="r[lado.campo]"
                  class="inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] text-muted-foreground hover:text-foreground hover:bg-muted disabled:opacity-50"
                  :disabled="baixandoXml.has(r[lado.campo]!.nota_id)"
                  title="Baixar o XML da nota"
                  @click="baixarXml(r[lado.campo]!)"
                >
                  <Loader2 v-if="baixandoXml.has(r[lado.campo]!.nota_id)" class="size-3 animate-spin" />
                  <FileDown v-else class="size-3" />
                  XML
                </button>
                <span v-else class="text-muted-foreground">—</span>
              </td>
            </template>
            <!-- Etiqueta: emissão (liberou pro despacho) e 1ª impressão -->
            <td :class="['px-2 py-1 whitespace-nowrap tabular-nums', SEP, ETQ_TINT]">{{ fmtDataHora(r.etiqueta_emissao) }}</td>
            <td :class="['px-2 py-1 whitespace-nowrap tabular-nums', SEP_FINA, ETQ_TINT]">{{ fmtDataHora(r.etiqueta_impressao) }}</td>
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
