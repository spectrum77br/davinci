<script setup lang="ts">
import {
  DollarSign, ShoppingCart, Package, AlertTriangle, RefreshCw,
  ArrowUpRight, Activity, Truck,
} from 'lucide-vue-next'

const config = useRuntimeConfig()
const { data: health } = await useFetch<{ status: string; postgres: string; redis: string }>(
  `${config.public.apiUrl}/api/health`,
  { server: false, default: () => ({ status: 'unknown', postgres: '?', redis: '?' }) },
)

const kpis = [
  { label: 'Faturamento (30d)', value: 'R$ 482.910', delta: 12.4, hint: 'vs. 30d anteriores', icon: DollarSign },
  { label: 'Pedidos', value: '3.241', delta: 8.1, hint: '127 hoje', icon: ShoppingCart },
  { label: 'SKUs ativos', value: '1.873', delta: -1.2, hint: '14 sem estoque', icon: Package },
  { label: 'Margem média', value: '18,6%', delta: 2.3, hint: 'após taxas + frete', icon: Activity },
]

const channels = [
  { name: 'Mercado Livre', share: 42, sales: 'R$ 202.823', orders: 1364, color: 'bg-amber-400' },
  { name: 'Shopee',        share: 28, sales: 'R$ 135.214', orders: 891,  color: 'bg-orange-500' },
  { name: 'Amazon',        share: 14, sales: 'R$ 67.607',  orders: 412,  color: 'bg-yellow-600' },
  { name: 'Magalu',        share: 9,  sales: 'R$ 43.461',  orders: 281,  color: 'bg-blue-500' },
  { name: 'TikTok Shop',   share: 7,  sales: 'R$ 33.804',  orders: 293,  color: 'bg-pink-500' },
]

const recentSyncs = [
  { id: 1, scope: 'Bling — Aguiar',    kind: 'pedidos',  ok: 87,  err: 0, at: '2 min', dur: '12s' },
  { id: 2, scope: 'ML — Luno',         kind: 'anúncios', ok: 142, err: 3, at: '14 min', dur: '38s' },
  { id: 3, scope: 'Shopee — Jlas',     kind: 'pedidos',  ok: 56,  err: 0, at: '22 min', dur: '9s' },
  { id: 4, scope: 'Bling — custos',    kind: 'produtos', ok: 1873, err: 0, at: '1 h',   dur: '2m 14s' },
  { id: 5, scope: 'Amazon — Eron',     kind: 'pedidos',  ok: 0,   err: 12, at: '3 h',   dur: 'falhou' },
]

const alerts = [
  { tone: 'danger',  title: 'Token Bling expirou', body: 'Conta Aguiar — reconectar OAuth.' },
  { tone: 'warning', title: '14 SKUs sem estoque',  body: 'celular categoria — checar reposição.' },
  { tone: 'info',    title: 'Nova devolução',       body: 'pedido #ML-298311 — solicitação cliente.' },
]

function alertClass(t: string) {
  if (t === 'danger') return 'pill-danger'
  if (t === 'warning') return 'pill-warning'
  return 'pill-info'
}
</script>

<template>
  <div class="space-y-6">
    <PageHeader title="Dashboard" description="Visão geral das contas, vendas e sincronizações.">
      <template #actions>
        <Button size="sm" variant="outline">
          <RefreshCw class="size-4 mr-1.5" /> recarregar
        </Button>
        <Button size="sm">
          últimos 30 dias
        </Button>
      </template>
    </PageHeader>

    <div class="grid grid-cols-2 lg:grid-cols-4 gap-3">
      <StatCard v-for="k in kpis" :key="k.label" v-bind="k" />
    </div>

    <div class="grid lg:grid-cols-3 gap-4">
      <div class="lg:col-span-2 rounded-xl border bg-card p-5">
        <div class="flex items-center mb-4">
          <h2 class="font-semibold">Vendas por canal</h2>
          <span class="ml-2 text-xs text-muted-foreground">últimos 30 dias</span>
          <NuxtLink to="/anuncios" class="ml-auto text-xs text-primary inline-flex items-center hover:underline">
            ver anúncios <ArrowUpRight class="size-3 ml-0.5" />
          </NuxtLink>
        </div>

        <div class="space-y-4">
          <div v-for="c in channels" :key="c.name" class="space-y-1.5">
            <div class="flex items-center text-sm">
              <span class="font-medium">{{ c.name }}</span>
              <span class="ml-auto tabular-nums text-muted-foreground">{{ c.orders }} pedidos</span>
              <span class="ml-3 tabular-nums font-semibold w-28 text-right">{{ c.sales }}</span>
            </div>
            <div class="h-2 rounded-full bg-muted overflow-hidden">
              <div class="h-full rounded-full" :class="c.color" :style="{ width: c.share + '%' }" />
            </div>
          </div>
        </div>
      </div>

      <div class="rounded-xl border bg-card p-5 space-y-3">
        <div class="flex items-center">
          <AlertTriangle class="size-4 text-amber-500 mr-1.5" />
          <h2 class="font-semibold">Alertas</h2>
          <span class="pill pill-muted ml-auto">{{ alerts.length }}</span>
        </div>
        <ul class="space-y-3">
          <li v-for="(a, i) in alerts" :key="i" class="flex gap-3">
            <span :class="alertClass(a.tone)">{{ a.tone === 'danger' ? '!' : a.tone === 'warning' ? '⚠' : 'i' }}</span>
            <div class="min-w-0">
              <div class="text-sm font-medium leading-tight">{{ a.title }}</div>
              <div class="text-xs text-muted-foreground">{{ a.body }}</div>
            </div>
          </li>
        </ul>
        <div class="pt-2 border-t">
          <div class="flex items-center text-xs text-muted-foreground">
            <span>API</span>
            <span class="ml-auto" :class="health?.status === 'ok' ? 'text-emerald-600' : 'text-red-600'">
              {{ health?.status }}
            </span>
          </div>
          <div class="flex items-center text-xs text-muted-foreground mt-1">
            <span>Postgres / Redis</span>
            <span class="ml-auto font-mono">{{ health?.postgres }} / {{ health?.redis }}</span>
          </div>
        </div>
      </div>
    </div>

    <div class="rounded-xl border bg-card overflow-hidden">
      <div class="flex items-center px-5 py-3 border-b">
        <Truck class="size-4 text-muted-foreground mr-1.5" />
        <h2 class="font-semibold">Sincronizações recentes</h2>
        <NuxtLink to="/sincronizacoes" class="ml-auto text-xs text-primary inline-flex items-center hover:underline">
          ver todas <ArrowUpRight class="size-3 ml-0.5" />
        </NuxtLink>
      </div>
      <table class="w-full text-sm">
        <thead>
          <tr class="bg-muted/40 text-left text-xs text-muted-foreground">
            <th class="px-5 py-2 font-medium">Escopo</th>
            <th class="px-3 py-2 font-medium">Tipo</th>
            <th class="px-3 py-2 font-medium text-right">OK</th>
            <th class="px-3 py-2 font-medium text-right">Erros</th>
            <th class="px-3 py-2 font-medium">Duração</th>
            <th class="px-5 py-2 font-medium text-right">Quando</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in recentSyncs" :key="r.id" class="border-t hover:bg-muted/30">
            <td class="px-5 py-2.5 font-medium">{{ r.scope }}</td>
            <td class="px-3 py-2.5 text-muted-foreground">{{ r.kind }}</td>
            <td class="px-3 py-2.5 text-right tabular-nums">{{ r.ok }}</td>
            <td class="px-3 py-2.5 text-right tabular-nums">
              <span :class="r.err > 0 ? 'text-red-600 font-medium' : 'text-muted-foreground'">{{ r.err }}</span>
            </td>
            <td class="px-3 py-2.5 text-muted-foreground">{{ r.dur }}</td>
            <td class="px-5 py-2.5 text-right text-muted-foreground">{{ r.at }} atrás</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
