<script setup lang="ts">
import { computed, ref } from 'vue'
import { ChevronLeft, ChevronRight, Loader2, RefreshCw, SlidersHorizontal } from 'lucide-vue-next'
import { isoToday, isoDaysAgo } from '~/lib/date'
import { MARKETPLACE_LABELS, type Marketplace } from '~/composables/useMarketplaces'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'faturamento', action: 'view' },
})

const { api } = useApi()
const auth = useAuthStore()
const isAdmin = computed(() => auth.user?.role === 'admin')

type FaturamentoLinha = {
  store_id: string
  loja: string | null
  tipo: string | null
  departments: string[]
  pedidos: number
  faturamento: number
  ticket_medio: number
}
type FaturamentoOut = {
  itens: FaturamentoLinha[]
  total_pedidos: number
  total_faturamento: number
  start: string
  end: string
  teams: number[]
  team: number | null
}

// Período: por mês (default = mês atual). Modo "Personalizado" mantém
// o range arbitrário antigo (dois date inputs).
type Mode = 'month' | 'custom'
const mode = ref<Mode>('month')
const month = ref<string>(isoToday().slice(0, 7)) // YYYY-MM
const maxMonth = isoToday().slice(0, 7) // trava navegação no futuro
const customStart = ref<string>(isoDaysAgo(90))
const customEnd = ref<string>(isoToday())

function monthLabel(ym: string): string {
  const [y, m] = ym.split('-').map(Number)
  return new Date(y, m - 1, 1).toLocaleDateString('pt-BR', { month: 'long', year: 'numeric' })
}
function shiftMonth(delta: number) {
  const [y, m] = month.value.split('-').map(Number)
  const d = new Date(y, m - 1 + delta, 1)
  const ym = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
  if (ym > maxMonth) return
  month.value = ym
  void load()
}

const loading = ref(false)
const error = ref<string | null>(null)
const data = ref<FaturamentoOut | null>(null)

// Filtro de equipe: null = todas as lojas do escopo do usuário. O <select> só
// aparece quando o backend devolve mais de uma equipe (admin ou multi-equipe).
const team = ref<number | null>(null)
const teams = computed(() => data.value?.teams ?? [])
const department = ref('')
const mobileFiltersOpen = ref(false)
// "Catálogo" saiu do filtro em 06/10/2026 (o catálogo do ML virou grupo
// dentro de cada tipo, na Tabela de Preços). O rótulo fica só para loja
// antiga que ainda tenha o tipo.
const departmentOptions = [
  { value: 'celular', label: 'Celular' },
  { value: 'eletro', label: 'Eletro' },
  { value: 'mala', label: 'Mala' },
  { value: 'shein', label: 'Shein' },
]
const ROTULOS_TIPO_ANTIGO: Record<string, string> = { catalogo: 'Catálogo' }
function departmentLabel(slug: string): string {
  return departmentOptions.find((option) => option.value === slug)?.label
    ?? ROTULOS_TIPO_ANTIGO[slug]
    ?? slug
}
function platformLabel(value: string | null): string {
  if (!value) return '—'
  const platform = value === 'mercadolivre' ? 'ml' : value
  return MARKETPLACE_LABELS[platform as Marketplace] ?? value
}

function periodoParams(): { start: string; end: string } {
  if (mode.value === 'custom') {
    return { start: `${customStart.value}T00:00:00`, end: `${customEnd.value}T23:59:59` }
  }
  // Mês: [dia 1 00:00, dia 1 do mês seguinte 00:00). Backend filtra data < end.
  const [y, m] = month.value.split('-').map(Number)
  const ny = m === 12 ? y + 1 : y
  const nm = m === 12 ? 1 : m + 1
  return {
    start: `${month.value}-01T00:00:00`,
    end: `${ny}-${String(nm).padStart(2, '0')}-01T00:00:00`,
  }
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const { start, end } = periodoParams()
    const qs = new URLSearchParams({ start, end })
    if (team.value != null) qs.set('team', String(team.value))
    if (isAdmin.value && department.value) qs.set('department', department.value)
    data.value = await api<FaturamentoOut>(`/api/faturamento?${qs.toString()}`)
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    loading.value = false
  }
}

function setMode(m: Mode) {
  mode.value = m
  if (m === 'month') void load()
}

await load()

function fmtBRL(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '—'
  return n.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
}
function fmtInt(n: number | null | undefined): string {
  if (n == null) return '—'
  return Number(n).toLocaleString('pt-BR')
}
</script>

<template>
  <div class="faturamento-page min-w-0 space-y-4 p-4">
    <div class="faturamento-header flex flex-wrap items-center gap-2">
      <h1 class="text-xl font-semibold">Faturamento</h1>
      <span class="faturamento-description text-xs text-muted-foreground ml-2">
        Pedidos faturáveis (em aberto, em andamento e entregues), por loja.
      </span>
      <button
        class="ml-auto inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
        :disabled="loading"
        aria-label="Recarregar"
        @click="load"
      >
        <RefreshCw class="size-3.5" :class="{ 'animate-spin': loading }" /><span class="hidden lg:inline">Recarregar</span>
      </button>
    </div>

    <div class="faturamento-quick flex items-center gap-1">
      <template v-if="mode === 'month'">
        <button class="grid size-10 shrink-0 place-items-center rounded-lg hover:bg-muted" aria-label="Mês anterior" @click="shiftMonth(-1)"><ChevronLeft class="size-4" /></button>
        <span class="min-w-0 flex-1 text-center text-sm font-medium capitalize">{{ monthLabel(month) }}</span>
        <button class="grid size-10 shrink-0 place-items-center rounded-lg hover:bg-muted disabled:opacity-30" aria-label="Próximo mês" :disabled="month >= maxMonth" @click="shiftMonth(1)"><ChevronRight class="size-4" /></button>
      </template>
      <span v-else class="flex-1 text-sm font-medium">Período personalizado</span>
      <button class="ml-1 inline-flex min-h-10 items-center gap-1.5 rounded-lg border px-2.5 text-xs" :aria-expanded="mobileFiltersOpen" aria-controls="faturamento-filters" @click="mobileFiltersOpen = !mobileFiltersOpen">
        <SlidersHorizontal class="size-3.5" /> Filtros<span v-if="team || (isAdmin && department)" class="size-1.5 rounded-full bg-primary" aria-label="Filtros ativos" />
      </button>
    </div>
    <!-- Filtro de período -->
    <div id="faturamento-filters" :class="{ 'is-open': mobileFiltersOpen }" class="faturamento-filters flex flex-wrap items-center gap-2 bg-muted/30 border rounded-md px-3 py-2 text-sm">
      <div class="faturamento-mode">
        <span class="text-xs text-muted-foreground">Período:</span>
        <div class="inline-flex rounded-md border overflow-hidden">
          <button
            class="px-3 py-1 text-xs hover:bg-muted"
            :class="mode === 'month' ? 'bg-primary text-primary-foreground' : ''"
            @click="setMode('month')"
          >
            Mês
          </button>
          <button
            class="px-3 py-1 text-xs hover:bg-muted border-l"
            :class="mode === 'custom' ? 'bg-primary text-primary-foreground' : ''"
            @click="mode = 'custom'"
          >
            Personalizado
          </button>
        </div>
      </div>
      <template v-if="mode === 'month'">
        <div class="faturamento-month inline-flex items-center gap-1">
          <button
            class="rounded-md border px-1.5 py-1 hover:bg-muted"
            title="Mês anterior"
            aria-label="Mês anterior"
            @click="shiftMonth(-1)"
          >
            <ChevronLeft class="size-4" />
          </button>
          <input
            v-model="month"
            type="month"
            aria-label="Mês do faturamento"
            :max="maxMonth"
            class="h-7 border rounded px-2 bg-background text-xs"
            @change="load"
          />
          <button
            class="rounded-md border px-1.5 py-1 hover:bg-muted disabled:opacity-40"
            title="Próximo mês"
            aria-label="Próximo mês"
            :disabled="month >= maxMonth"
            @click="shiftMonth(1)"
          >
            <ChevronRight class="size-4" />
          </button>
        </div>
        <span class="faturamento-month-label text-xs text-muted-foreground capitalize">{{ monthLabel(month) }}</span>
      </template>
      <template v-else>
        <label class="faturamento-filter-group">
          <span class="faturamento-mobile-label text-xs text-muted-foreground">Início</span>
          <input v-model="customStart" type="date" aria-label="Data inicial do faturamento" class="h-7 border rounded px-2 bg-background text-xs" />
        </label>
        <span class="faturamento-date-separator text-xs text-muted-foreground">a</span>
        <label class="faturamento-filter-group">
          <span class="faturamento-mobile-label text-xs text-muted-foreground">Fim</span>
          <input v-model="customEnd" type="date" aria-label="Data final do faturamento" class="h-7 border rounded px-2 bg-background text-xs" />
        </label>
        <button
          class="faturamento-apply ml-1 rounded-md bg-primary text-primary-foreground px-2.5 py-1 text-xs hover:opacity-90"
          @click="load"
        >Aplicar</button>
      </template>

      <!-- Filtro de equipe: só aparece quando há mais de uma equipe disponível
           (admin ou usuário multi-equipe). null = todas as lojas do escopo. -->
      <div v-if="teams.length > 1" class="faturamento-filter-group">
        <label for="faturamento-equipe" class="ml-2 text-xs text-muted-foreground">Equipe:</label>
        <select
          id="faturamento-equipe"
          v-model.number="team"
          aria-label="Equipe"
          class="h-7 border rounded px-2 bg-background text-xs"
          @change="load"
        >
          <option :value="null">Todas as equipes</option>
          <option v-for="t in teams" :key="t" :value="t">Equipe {{ t }}</option>
        </select>
      </div>

      <div v-if="isAdmin" class="faturamento-filter-group">
        <label for="faturamento-tipo" class="ml-2 text-xs text-muted-foreground">Tipo:</label>
        <select
          id="faturamento-tipo"
          v-model="department"
          class="h-7 border rounded px-2 bg-background text-xs"
          @change="load"
        >
          <option value="">Todos os tipos</option>
          <option v-for="option in departmentOptions" :key="option.value" :value="option.value">
            {{ option.label }}
          </option>
        </select>
      </div>
    </div>

    <p v-if="isAdmin && department" class="text-xs text-muted-foreground">
      Mostrando o faturamento total das lojas classificadas como {{ departmentLabel(department) }}.
    </p>

    <div v-if="error" class="text-sm text-destructive">{{ error }}</div>

    <!-- No celular, cada loja mantém todos os valores da tabela em um cartão. -->
    <section class="faturamento-mobile space-y-3" aria-label="Faturamento por loja" :aria-busy="loading">
      <div v-if="data?.itens.length" class="grid grid-cols-2 gap-2 rounded-lg border bg-muted/30 p-3">
        <div>
          <p class="text-xs text-muted-foreground">Pedidos no período</p>
          <p class="mt-1 text-lg font-semibold tabular-nums">{{ fmtInt(data.total_pedidos) }}</p>
        </div>
        <div class="min-w-0 text-right">
          <p class="text-xs text-muted-foreground">Faturamento total</p>
          <p class="mt-1 break-words text-base font-semibold tabular-nums">{{ fmtBRL(data.total_faturamento) }}</p>
        </div>
      </div>
      <p v-if="loading && !data" class="rounded-lg border px-3 py-6 text-center text-sm text-muted-foreground" role="status">
        <Loader2 class="mr-1 inline size-4 animate-spin" /> carregando…
      </p>
      <p v-else-if="!data?.itens.length" class="rounded-lg border px-3 py-6 text-center text-sm text-muted-foreground">
        Nenhum faturamento no período.
        <span v-if="!isAdmin" class="mt-1 block">Você vê apenas lojas das suas equipes de vendas.</span>
      </p>
      <div class="faturamento-cards grid gap-3 sm:grid-cols-2">
      <article v-for="r in data?.itens || []" :key="r.store_id" class="min-w-0 rounded-lg border bg-background p-3">
        <div class="flex flex-wrap items-start justify-between gap-2">
          <div class="min-w-0">
            <h2 class="break-words text-sm font-semibold">{{ r.loja || '—' }}</h2>
            <p class="mt-0.5 text-xs text-muted-foreground">{{ platformLabel(r.tipo) }}</p>
          </div>
          <div v-if="r.departments?.length" class="flex flex-wrap gap-1" aria-label="Tipos da loja">
            <span v-for="slug in r.departments" :key="slug" class="rounded border border-border bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">{{ departmentLabel(slug) }}</span>
          </div>
          <span v-else class="text-xs text-muted-foreground">Sem tipo</span>
        </div>
        <dl class="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 border-t pt-3 text-xs">
          <div class="col-span-2 flex flex-wrap items-baseline justify-between gap-1">
            <dt class="text-muted-foreground">Faturamento</dt>
            <dd class="text-base font-semibold tabular-nums">{{ fmtBRL(r.faturamento) }}</dd>
          </div>
          <div>
            <dt class="text-muted-foreground">Pedidos</dt>
            <dd class="mt-0.5 font-medium tabular-nums">{{ fmtInt(r.pedidos) }}</dd>
          </div>
          <div class="text-right">
            <dt class="text-muted-foreground">Ticket médio</dt>
            <dd class="mt-0.5 font-medium tabular-nums">{{ fmtBRL(r.ticket_medio) }}</dd>
          </div>
        </dl>
      </article>
      </div>
    </section>

    <div class="faturamento-desktop border rounded-md overflow-auto">
      <table class="w-full text-sm border-collapse">
        <thead class="bg-muted/50 text-xs uppercase tracking-wide">
          <tr>
            <th class="text-left px-3 py-2 font-medium">Loja</th>
            <th class="text-left px-3 py-2 font-medium">Plataforma</th>
            <th class="text-left px-3 py-2 font-medium">Tipo</th>
            <th class="text-right px-3 py-2 font-medium">Pedidos</th>
            <th class="text-right px-3 py-2 font-medium">Faturamento</th>
            <th class="text-right px-3 py-2 font-medium">Ticket médio</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading && !data">
            <td colspan="6" class="text-center py-6 text-muted-foreground">
              <Loader2 class="inline h-4 w-4 animate-spin" /> carregando…
            </td>
          </tr>
          <tr v-else-if="!data?.itens.length">
            <td colspan="6" class="text-center py-8 text-muted-foreground">
              Nenhum faturamento no período.
              <span v-if="!isAdmin">
                <br />Você vê apenas lojas das suas equipes de vendas.
              </span>
            </td>
          </tr>
          <tr
            v-for="r in data?.itens || []"
            :key="r.store_id"
            class="border-t hover:bg-muted/20"
          >
            <td class="px-3 py-1.5">{{ r.loja || '—' }}</td>
            <td class="px-3 py-1.5">{{ platformLabel(r.tipo) }}</td>
            <td class="px-3 py-1.5">
              <div v-if="r.departments?.length" class="flex flex-wrap gap-1">
                <span
                  v-for="slug in r.departments"
                  :key="slug"
                  class="inline-block rounded border border-border bg-muted px-1.5 py-0.5 text-xs text-muted-foreground"
                >{{ departmentLabel(slug) }}</span>
              </div>
              <span v-else class="text-muted-foreground">—</span>
            </td>
            <td class="px-3 py-1.5 text-right tabular-nums">{{ fmtInt(r.pedidos) }}</td>
            <td class="px-3 py-1.5 text-right tabular-nums font-medium">{{ fmtBRL(r.faturamento) }}</td>
            <td class="px-3 py-1.5 text-right tabular-nums">{{ fmtBRL(r.ticket_medio) }}</td>
          </tr>
        </tbody>
        <tfoot v-if="data?.itens.length" class="bg-muted/30 font-semibold">
          <tr class="border-t">
            <td class="px-3 py-2" colspan="3">Total</td>
            <td class="px-3 py-2 text-right tabular-nums">{{ fmtInt(data?.total_pedidos) }}</td>
            <td class="px-3 py-2 text-right tabular-nums">{{ fmtBRL(data?.total_faturamento) }}</td>
            <td class="px-3 py-2"></td>
          </tr>
        </tfoot>
      </table>
    </div>
  </div>
</template>

<style scoped>
.faturamento-quick,
.faturamento-mobile,
.faturamento-mobile-label {
  display: none;
}
.faturamento-mode,
.faturamento-filter-group {
  display: contents;
}

@media (max-width: 1023px) {
  .faturamento-page {
    padding: 0;
  }
  .faturamento-header {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
  }
  .faturamento-header h1 {
    font-size: 1.125rem;
  }
  .faturamento-description { display: none; }
  .faturamento-quick { display: flex; }
  .faturamento-filters:not(.is-open) { display: none; }
  .faturamento-mode {
    display: flex;
    flex: 1 1 100%;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
  }
  .faturamento-filter-group {
    display: flex;
    flex: 1 1 8.5rem;
    min-width: 0;
    flex-direction: column;
    gap: 0.25rem;
  }
  .faturamento-filter-group label {
    margin-left: 0;
  }
  .faturamento-filter-group input,
  .faturamento-filter-group select {
    width: 100%;
  }
  .faturamento-mobile-label {
    display: block;
  }
  .faturamento-date-separator,
  .faturamento-month-label {
    display: none;
  }
  .faturamento-apply {
    width: 100%;
    margin-left: 0;
  }
  .faturamento-header button,
  .faturamento-filters button,
  .faturamento-filters input,
  .faturamento-filters select {
    min-height: 2.75rem;
  }
  .faturamento-filters {
    gap: 0.5rem;
    padding: 0.625rem;
  }
  .faturamento-filters input,
  .faturamento-filters select {
    min-width: 0;
    max-width: 100%;
    font-size: 1rem;
  }
  .faturamento-month {
    flex: 1 1 100%;
  }
  .faturamento-month input {
    flex: 1;
    width: 0;
  }
  .faturamento-month button {
    display: inline-flex;
    min-width: 2.75rem;
    align-items: center;
    justify-content: center;
  }
  .faturamento-mobile {
    display: block;
  }
  .faturamento-desktop {
    display: none;
  }
}
</style>
