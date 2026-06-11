<script setup lang="ts">
import { computed, ref } from 'vue'
import { Loader2, RefreshCw } from 'lucide-vue-next'
import { isoToday, isoDaysAgo } from '~/lib/date'

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
}

// Período: presets + custom (range). Default 90 dias.
type Preset = '7' | '30' | '90' | 'custom'
const preset = ref<Preset>('90')
const customStart = ref<string>(isoDaysAgo(90))
const customEnd = ref<string>(isoToday())

const loading = ref(false)
const error = ref<string | null>(null)
const data = ref<FaturamentoOut | null>(null)

function periodoParams(): { start: string; end: string } {
  if (preset.value === 'custom') {
    return { start: `${customStart.value}T00:00:00`, end: `${customEnd.value}T23:59:59` }
  }
  const days = preset.value === '7' ? 7 : preset.value === '30' ? 30 : 90
  return { start: `${isoDaysAgo(days)}T00:00:00`, end: `${isoToday()}T23:59:59` }
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const { start, end } = periodoParams()
    const qs = new URLSearchParams({ start, end })
    data.value = await api<FaturamentoOut>(`/api/faturamento?${qs.toString()}`)
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    loading.value = false
  }
}

function setPreset(p: Preset) {
  preset.value = p
  if (p !== 'custom') void load()
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
  <div class="space-y-4 p-4">
    <div class="flex flex-wrap items-center gap-2">
      <h1 class="text-xl font-semibold">Faturamento</h1>
      <span class="text-xs text-muted-foreground ml-2">
        Pedidos entregues (situação Bling 83953), por loja.
      </span>
      <button
        class="ml-auto inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
        :disabled="loading"
        @click="load"
      >
        <RefreshCw class="size-3.5" :class="{ 'animate-spin': loading }" /> Recarregar
      </button>
    </div>

    <!-- Filtro de período -->
    <div class="flex flex-wrap items-center gap-2 bg-muted/30 border rounded-md px-3 py-2 text-sm">
      <span class="text-xs text-muted-foreground">Período:</span>
      <div class="inline-flex rounded-md border overflow-hidden">
        <button
          v-for="p in (['7','30','90'] as const)"
          :key="p"
          class="px-3 py-1 text-xs hover:bg-muted"
          :class="preset === p ? 'bg-primary text-primary-foreground' : ''"
          @click="setPreset(p)"
        >
          {{ p }} dias
        </button>
        <button
          class="px-3 py-1 text-xs hover:bg-muted border-l"
          :class="preset === 'custom' ? 'bg-primary text-primary-foreground' : ''"
          @click="preset = 'custom'"
        >
          custom
        </button>
      </div>
      <template v-if="preset === 'custom'">
        <input v-model="customStart" type="date" class="h-7 border rounded px-2 bg-background text-xs" />
        <span class="text-xs text-muted-foreground">a</span>
        <input v-model="customEnd" type="date" class="h-7 border rounded px-2 bg-background text-xs" />
        <button
          class="ml-1 rounded-md bg-primary text-primary-foreground px-2.5 py-1 text-xs hover:opacity-90"
          @click="load"
        >Aplicar</button>
      </template>
    </div>

    <div v-if="error" class="text-sm text-destructive">{{ error }}</div>

    <div class="border rounded-md overflow-auto">
      <table class="w-full text-sm border-collapse">
        <thead class="bg-muted/50 text-xs uppercase tracking-wide">
          <tr>
            <th class="text-left px-3 py-2 font-medium">Loja</th>
            <th class="text-left px-3 py-2 font-medium">Tipo</th>
            <th class="text-right px-3 py-2 font-medium">Pedidos</th>
            <th class="text-right px-3 py-2 font-medium">Faturamento</th>
            <th class="text-right px-3 py-2 font-medium">Ticket médio</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading && !data">
            <td colspan="5" class="text-center py-6 text-muted-foreground">
              <Loader2 class="inline h-4 w-4 animate-spin" /> carregando…
            </td>
          </tr>
          <tr v-else-if="!data?.itens.length">
            <td colspan="5" class="text-center py-8 text-muted-foreground">
              Nenhum faturamento entregue no período.
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
            <td class="px-3 py-1.5">{{ r.tipo || '—' }}</td>
            <td class="px-3 py-1.5 text-right tabular-nums">{{ fmtInt(r.pedidos) }}</td>
            <td class="px-3 py-1.5 text-right tabular-nums font-medium">{{ fmtBRL(r.faturamento) }}</td>
            <td class="px-3 py-1.5 text-right tabular-nums">{{ fmtBRL(r.ticket_medio) }}</td>
          </tr>
        </tbody>
        <tfoot v-if="data?.itens.length" class="bg-muted/30 font-semibold">
          <tr class="border-t">
            <td class="px-3 py-2" colspan="2">Total</td>
            <td class="px-3 py-2 text-right tabular-nums">{{ fmtInt(data?.total_pedidos) }}</td>
            <td class="px-3 py-2 text-right tabular-nums">{{ fmtBRL(data?.total_faturamento) }}</td>
            <td class="px-3 py-2"></td>
          </tr>
        </tfoot>
      </table>
    </div>
  </div>
</template>
