<script setup lang="ts">
import { TrendingUp, TrendingDown, Calendar, Download } from 'lucide-vue-next'

definePageMeta({ middleware: ['permission'], permission: { resource: 'margem', action: 'view' } })

type Linha = {
  canal: string
  conta: string
  pedidos: number
  receita: number
  custo: number
  taxas: number
  frete: number
  margem: number
  pct: number
}

const linhas: Linha[] = [
  { canal: 'Mercado Livre', conta: 'Aguiar',  pedidos: 482, receita: 184290, custo: 132100, taxas: 25800, frete: 6500, margem: 19890, pct: 10.8 },
  { canal: 'Mercado Livre', conta: 'Luno',    pedidos: 318, receita: 121430, custo: 87200,  taxas: 17000, frete: 4300, margem: 12930, pct: 10.6 },
  { canal: 'Shopee',        conta: 'Jlas',    pedidos: 412, receita: 98230,  custo: 71800,  taxas: 12700, frete: 3900, margem: 9830,  pct: 10.0 },
  { canal: 'Shopee',        conta: 'Mini',    pedidos: 289, receita: 71920,  custo: 53000,  taxas: 9100,  frete: 2800, margem: 7020,  pct: 9.8  },
  { canal: 'Amazon',        conta: 'Eron',    pedidos: 167, receita: 89320,  custo: 67400,  taxas: 12300, frete: 4100, margem: 5520,  pct: 6.2  },
  { canal: 'Amazon',        conta: 'Atv',     pedidos: 98,  receita: 42810,  custo: 32100,  taxas: 5900,  frete: 2000, margem: 2810,  pct: 6.6  },
  { canal: 'Magalu',        conta: 'Barbosa', pedidos: 134, receita: 38920,  custo: 28200,  taxas: 4900,  frete: 1900, margem: 3920,  pct: 10.1 },
  { canal: 'TikTok Shop',   conta: 'Atv',     pedidos: 87,  receita: 24150,  custo: 17900,  taxas: 3100,  frete: 1100, margem: 2050,  pct: 8.5  },
]

function brl(v: number) {
  return v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 })
}

const tot = computed(() =>
  linhas.reduce((a, l) => ({
    pedidos: a.pedidos + l.pedidos,
    receita: a.receita + l.receita,
    custo: a.custo + l.custo,
    taxas: a.taxas + l.taxas,
    frete: a.frete + l.frete,
    margem: a.margem + l.margem,
  }), { pedidos: 0, receita: 0, custo: 0, taxas: 0, frete: 0, margem: 0 }),
)
const pctTotal = computed(() => ((tot.value.margem / tot.value.receita) * 100).toFixed(1))
</script>

<template>
  <div class="space-y-5">
    <PageHeader title="Margem" description="Lucratividade real por canal e conta — receita menos custo, taxas e frete.">
      <template #actions>
        <Button size="sm" variant="outline">
          <Calendar class="size-4 mr-1.5" /> últimos 30 dias
        </Button>
        <Button size="sm" variant="outline">
          <Download class="size-4 mr-1.5" /> exportar
        </Button>
      </template>
    </PageHeader>

    <div class="grid grid-cols-2 lg:grid-cols-4 gap-3">
      <StatCard label="Receita" :value="brl(tot.receita)" :delta="12.4" :icon="TrendingUp" />
      <StatCard label="Custo" :value="brl(tot.custo)" hint="produtos ao custo Bling" />
      <StatCard label="Taxas + frete" :value="brl(tot.taxas + tot.frete)" hint="comissões e logística" />
      <StatCard label="Margem líquida" :value="brl(tot.margem)" :delta="2.3" :hint="`${pctTotal}% sobre receita`" :icon="TrendingUp" />
    </div>

    <div class="grid lg:grid-cols-3 gap-4">
      <div class="lg:col-span-2 rounded-xl border bg-card p-5">
        <h2 class="font-semibold mb-4">Margem por canal × conta</h2>
        <div class="space-y-3">
          <div v-for="l in linhas" :key="l.canal + l.conta">
            <div class="flex items-center text-sm mb-1">
              <span class="font-medium">{{ l.canal }}</span>
              <span class="text-muted-foreground mx-1">·</span>
              <span class="text-muted-foreground">{{ l.conta }}</span>
              <span class="ml-auto tabular-nums font-semibold" :class="l.pct >= 10 ? 'text-emerald-600' : 'text-amber-600'">
                {{ l.pct.toFixed(1) }}%
              </span>
            </div>
            <div class="h-1.5 rounded-full bg-muted overflow-hidden">
              <div
                class="h-full rounded-full transition-all"
                :class="l.pct >= 10 ? 'bg-emerald-500' : 'bg-amber-500'"
                :style="{ width: `${(l.pct / 15) * 100}%` }"
              />
            </div>
          </div>
        </div>
      </div>

      <div class="rounded-xl border bg-card p-5 space-y-4">
        <h2 class="font-semibold">Decomposição</h2>
        <div class="space-y-2.5">
          <div class="flex justify-between text-sm">
            <span class="text-muted-foreground">Receita bruta</span>
            <span class="tabular-nums font-medium">{{ brl(tot.receita) }}</span>
          </div>
          <div class="flex justify-between text-sm">
            <span class="text-muted-foreground">(-) Custo produtos</span>
            <span class="tabular-nums text-red-600">{{ brl(-tot.custo) }}</span>
          </div>
          <div class="flex justify-between text-sm">
            <span class="text-muted-foreground">(-) Taxas marketplace</span>
            <span class="tabular-nums text-red-600">{{ brl(-tot.taxas) }}</span>
          </div>
          <div class="flex justify-between text-sm">
            <span class="text-muted-foreground">(-) Frete</span>
            <span class="tabular-nums text-red-600">{{ brl(-tot.frete) }}</span>
          </div>
          <div class="border-t pt-2.5 flex justify-between font-semibold">
            <span>Margem</span>
            <span class="tabular-nums text-emerald-600">{{ brl(tot.margem) }}</span>
          </div>
        </div>
      </div>
    </div>

    <div class="table-card">
      <table class="w-full">
        <thead>
          <tr>
            <th>Canal</th>
            <th>Conta</th>
            <th class="text-right">Pedidos</th>
            <th class="text-right">Receita</th>
            <th class="text-right">Custo</th>
            <th class="text-right">Taxas</th>
            <th class="text-right">Frete</th>
            <th class="text-right">Margem</th>
            <th class="text-right">%</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="l in linhas" :key="l.canal + l.conta">
            <td class="font-medium">{{ l.canal }}</td>
            <td class="text-muted-foreground">{{ l.conta }}</td>
            <td class="text-right tabular-nums">{{ l.pedidos }}</td>
            <td class="text-right tabular-nums">{{ brl(l.receita) }}</td>
            <td class="text-right tabular-nums text-muted-foreground">{{ brl(l.custo) }}</td>
            <td class="text-right tabular-nums text-muted-foreground">{{ brl(l.taxas) }}</td>
            <td class="text-right tabular-nums text-muted-foreground">{{ brl(l.frete) }}</td>
            <td class="text-right tabular-nums font-medium">{{ brl(l.margem) }}</td>
            <td class="text-right tabular-nums">
              <span class="pill" :class="l.pct >= 10 ? 'pill-success' : 'pill-warning'">
                {{ l.pct.toFixed(1) }}%
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
