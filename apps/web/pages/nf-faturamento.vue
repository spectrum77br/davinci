<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ChevronLeft, ChevronRight, Loader2, RefreshCw, X } from 'lucide-vue-next'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'nf_faturamento', action: 'view' },
})

const { api } = useApi()

type Row = {
  data: string | null
  pedido_bling: string
  pedido_marketplace: string | null
  plataforma: string | null
  conta: string | null
  status_bling: string | null
  status_faturamento: string
  erro_faturamento: string | null
  status_etiqueta: string
  erro_etiqueta: string | null
  status_impressao: string
  erro_impressao: string | null
}

const rows = ref<Row[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const dias = ref(7)

async function load() {
  loading.value = true
  error.value = null
  try {
    rows.value = await api<Row[]>(`/api/nf-cadastro/faturamento?dias=${dias.value}&limit=2000`)
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'Erro ao carregar'
  } finally {
    loading.value = false
  }
}
load()
watch(dias, () => load())

// -- Filtros client-side ----------------------------------------------------
const search = ref('')
const plataformaFilter = ref('')
const contaFilter = ref('')

const plataformas = computed(() =>
  Array.from(new Set(rows.value.map((r) => r.plataforma).filter((x): x is string => !!x))).sort(),
)
const contas = computed(() =>
  Array.from(new Set(rows.value.map((r) => r.conta).filter((x): x is string => !!x))).sort(),
)

const filteredRows = computed(() => {
  const q = search.value.trim().toLowerCase()
  return rows.value.filter((r) => {
    if (plataformaFilter.value && r.plataforma !== plataformaFilter.value) return false
    if (contaFilter.value && r.conta !== contaFilter.value) return false
    if (q) {
      const hay = [
        r.pedido_bling,
        r.pedido_marketplace,
        r.plataforma,
        r.conta,
        r.status_bling,
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
      if (!hay.includes(q)) return false
    }
    return true
  })
})

function limparFiltros() {
  search.value = ''
  plataformaFilter.value = ''
  contaFilter.value = ''
}

// -- Paginação --------------------------------------------------------------
const PAGE_SIZE = 50
const page = ref(1)
const totalPages = computed(() => Math.max(1, Math.ceil(filteredRows.value.length / PAGE_SIZE)))
const pagedRows = computed(() =>
  filteredRows.value.slice((page.value - 1) * PAGE_SIZE, page.value * PAGE_SIZE),
)
function goToPage(p: number) {
  page.value = Math.min(Math.max(1, p), totalPages.value)
}
watch([search, plataformaFilter, contaFilter, dias], () => {
  page.value = 1
})

// -- Helpers ----------------------------------------------------------------
function fmtData(d: string | null): string {
  if (!d) return '—'
  const [y, m, dd] = d.slice(0, 10).split('-')
  return `${dd}/${m}/${y}`
}
function badgeClass(status: string): string {
  const s = (status || '').toLowerCase()
  if (s === 'ok') return 'bg-emerald-100 text-emerald-700'
  if (s === 'erro') return 'bg-red-100 text-red-700'
  return 'bg-gray-100 text-gray-600'
}
function badgeLabel(status: string): string {
  const s = (status || '').toLowerCase()
  if (s === 'ok') return 'OK'
  if (s === 'erro') return 'Erro'
  if (s === 'pendente') return 'Pendente'
  return status
}
</script>

<template>
  <div class="space-y-4 p-4">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <div>
        <h1 class="text-xl font-semibold">Painel de Faturamento (NF)</h1>
        <p class="text-sm text-muted-foreground">
          Status por etapa (faturamento → etiqueta → impressão) dos pedidos das lojas com cadastro de NF.
        </p>
      </div>
      <div class="flex items-center gap-2">
        <label class="text-sm text-muted-foreground">Janela</label>
        <select v-model.number="dias" class="rounded-md border bg-background px-2 py-1.5 text-sm">
          <option :value="1">Hoje</option>
          <option :value="3">3 dias</option>
          <option :value="7">7 dias</option>
          <option :value="15">15 dias</option>
          <option :value="30">30 dias</option>
          <option :value="60">60 dias</option>
          <option :value="90">90 dias</option>
        </select>
        <button
          class="inline-flex items-center gap-1 rounded-md border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
          :disabled="loading"
          @click="load"
        >
          <RefreshCw class="h-4 w-4" :class="loading ? 'animate-spin' : ''" />
          Recarregar
        </button>
      </div>
    </div>

    <!-- Filtros -->
    <div class="flex flex-wrap items-center gap-2">
      <input
        v-model="search"
        type="text"
        placeholder="Buscar pedido, conta, status…"
        class="w-64 rounded-md border bg-background px-2 py-1.5 text-sm"
      />
      <select v-model="plataformaFilter" class="rounded-md border bg-background px-2 py-1.5 text-sm">
        <option value="">Todas as plataformas</option>
        <option v-for="p in plataformas" :key="p" :value="p">{{ p }}</option>
      </select>
      <select v-model="contaFilter" class="rounded-md border bg-background px-2 py-1.5 text-sm">
        <option value="">Todas as contas</option>
        <option v-for="c in contas" :key="c" :value="c">{{ c }}</option>
      </select>
      <button
        v-if="search || plataformaFilter || contaFilter"
        class="inline-flex items-center gap-1 rounded-md border px-2 py-1.5 text-sm hover:bg-muted"
        @click="limparFiltros"
      >
        <X class="h-4 w-4" /> Limpar
      </button>
      <span class="text-sm text-muted-foreground">
        {{ filteredRows.length }} de {{ rows.length }}
      </span>
    </div>

    <div v-if="error" class="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
      {{ error }}
    </div>

    <!-- Tabela -->
    <div class="overflow-x-auto rounded-md border">
      <table class="min-w-[1000px] w-full text-sm">
        <thead class="bg-muted/50 text-left">
          <tr>
            <th class="px-3 py-2 font-medium">Data</th>
            <th class="px-3 py-2 font-medium">Pedido Bling</th>
            <th class="px-3 py-2 font-medium">Pedido Marketplace</th>
            <th class="px-3 py-2 font-medium">Plataforma</th>
            <th class="px-3 py-2 font-medium">Conta</th>
            <th class="px-3 py-2 font-medium">Status Bling</th>
            <th class="px-3 py-2 font-medium">Faturamento</th>
            <th class="px-3 py-2 font-medium">Etiqueta</th>
            <th class="px-3 py-2 font-medium">Impressão</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading">
            <td colspan="9" class="px-3 py-8 text-center text-muted-foreground">
              <Loader2 class="mx-auto h-5 w-5 animate-spin" />
            </td>
          </tr>
          <tr v-else-if="!filteredRows.length">
            <td colspan="9" class="px-3 py-8 text-center text-muted-foreground">
              Nenhum pedido. As lojas precisam ter um cadastro de NF (Faturador/Etiqueta/Impressão) atribuído na tela Lojas.
            </td>
          </tr>
          <tr v-for="r in pagedRows" :key="r.pedido_bling" class="border-t hover:bg-muted/30">
            <td class="whitespace-nowrap px-3 py-2">{{ fmtData(r.data) }}</td>
            <td class="whitespace-nowrap px-3 py-2 font-medium">{{ r.pedido_bling }}</td>
            <td class="whitespace-nowrap px-3 py-2">{{ r.pedido_marketplace || '—' }}</td>
            <td class="whitespace-nowrap px-3 py-2">{{ r.plataforma || '—' }}</td>
            <td class="whitespace-nowrap px-3 py-2">{{ r.conta || '—' }}</td>
            <td class="whitespace-nowrap px-3 py-2">{{ r.status_bling || '—' }}</td>
            <td class="whitespace-nowrap px-3 py-2">
              <span
                class="rounded px-2 py-0.5 text-xs font-medium"
                :class="badgeClass(r.status_faturamento)"
                :title="r.erro_faturamento || ''"
              >{{ badgeLabel(r.status_faturamento) }}</span>
            </td>
            <td class="whitespace-nowrap px-3 py-2">
              <span
                class="rounded px-2 py-0.5 text-xs font-medium"
                :class="badgeClass(r.status_etiqueta)"
                :title="r.erro_etiqueta || ''"
              >{{ badgeLabel(r.status_etiqueta) }}</span>
            </td>
            <td class="whitespace-nowrap px-3 py-2">
              <span
                class="rounded px-2 py-0.5 text-xs font-medium"
                :class="badgeClass(r.status_impressao)"
                :title="r.erro_impressao || ''"
              >{{ badgeLabel(r.status_impressao) }}</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Paginação -->
    <div v-if="filteredRows.length > PAGE_SIZE" class="flex items-center justify-center gap-2">
      <button
        class="inline-flex items-center rounded-md border px-2 py-1 text-sm hover:bg-muted disabled:opacity-50"
        :disabled="page <= 1"
        @click="goToPage(page - 1)"
      >
        <ChevronLeft class="h-4 w-4" />
      </button>
      <span class="text-sm text-muted-foreground">Página {{ page }} de {{ totalPages }}</span>
      <button
        class="inline-flex items-center rounded-md border px-2 py-1 text-sm hover:bg-muted disabled:opacity-50"
        :disabled="page >= totalPages"
        @click="goToPage(page + 1)"
      >
        <ChevronRight class="h-4 w-4" />
      </button>
    </div>
  </div>
</template>
