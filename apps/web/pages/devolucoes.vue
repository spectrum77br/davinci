<script setup lang="ts">
import { Undo2, Filter, MessageSquare, Package, Clock } from 'lucide-vue-next'

definePageMeta({ middleware: ['permission'], permission: { resource: 'devolucoes', action: 'view' } })

type Dev = {
  id: string
  pedido: string
  canal: string
  conta: string
  motivo: string
  sku: string
  valor: number
  abertaEm: string
  status: 'aberta' | 'em_analise' | 'aprovada' | 'recusada' | 'concluida'
  prazo: string
}

const devs: Dev[] = [
  { id: 'D-1029', pedido: 'ML-298311', canal: 'ML',     conta: 'Aguiar',  motivo: 'produto não conforme',     sku: 'IPH15-128-PRT', valor: 5499, abertaEm: '02/05', status: 'aberta',     prazo: '5 dias' },
  { id: 'D-1028', pedido: 'SH-882190', canal: 'Shopee', conta: 'Jlas',    motivo: 'arrependimento',           sku: 'AIRPODS-PRO2',  valor: 1899, abertaEm: '01/05', status: 'em_analise', prazo: '3 dias' },
  { id: 'D-1027', pedido: 'AMZ-77321', canal: 'Amazon', conta: 'Eron',    motivo: 'avaria no transporte',     sku: 'TV-LG-55',      valor: 4899, abertaEm: '30/04', status: 'aprovada',   prazo: '—' },
  { id: 'D-1026', pedido: 'ML-298099', canal: 'ML',     conta: 'Luno',    motivo: 'item incorreto',           sku: 'GAL-S24-512',   valor: 8290, abertaEm: '29/04', status: 'recusada',   prazo: '—' },
  { id: 'D-1025', pedido: 'MGL-12388', canal: 'Magalu', conta: 'Barbosa', motivo: 'desistência',              sku: 'MALA-ABS-28',   valor: 489,  abertaEm: '28/04', status: 'concluida',  prazo: '—' },
  { id: 'D-1024', pedido: 'ML-297844', canal: 'ML',     conta: 'Aguiar',  motivo: 'defeito de fabricação',    sku: 'WATCH-S9-GPS',  valor: 3990, abertaEm: '27/04', status: 'em_analise', prazo: '1 dia' },
]

function brl(v: number) {
  return v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
}
function statusPill(s: Dev['status']) {
  if (s === 'aberta') return 'pill-warning'
  if (s === 'em_analise') return 'pill-info'
  if (s === 'aprovada') return 'pill-success'
  if (s === 'concluida') return 'pill-muted'
  return 'pill-danger'
}
function statusLabel(s: Dev['status']) {
  return s === 'em_analise' ? 'em análise' : s
}

const totAberto = computed(() => devs.filter(d => ['aberta','em_analise','aprovada'].includes(d.status)).reduce((a,d) => a+d.valor, 0))
</script>

<template>
  <div class="space-y-5">
    <PageHeader title="Devoluções" description="Solicitações de devolução abertas pelos compradores.">
      <template #actions>
        <Button size="sm" variant="outline">
          <MessageSquare class="size-4 mr-1.5" /> respostas padrão
        </Button>
      </template>
    </PageHeader>

    <div class="grid grid-cols-2 lg:grid-cols-4 gap-3">
      <StatCard label="Em aberto" :value="devs.filter(d => d.status === 'aberta').length" :icon="Undo2" tone="warning" />
      <StatCard label="Em análise" :value="devs.filter(d => d.status === 'em_analise').length" :icon="Clock" />
      <StatCard label="Concluídas (mês)" :value="devs.filter(d => d.status === 'concluida').length" :icon="Package" />
      <StatCard label="Valor em risco" :value="brl(totAberto)" hint="abertas + em análise" tone="danger" />
    </div>

    <div class="flex items-center gap-2">
      <Input placeholder="buscar pedido ou SKU…" class="w-72" />
      <select class="h-9 rounded-md border bg-background px-2 text-sm">
        <option>todos canais</option>
      </select>
      <select class="h-9 rounded-md border bg-background px-2 text-sm">
        <option>todos status</option>
      </select>
      <Button size="sm" variant="ghost"><Filter class="size-4 mr-1.5" /> filtros</Button>
    </div>

    <div class="table-card">
      <table class="w-full">
        <thead>
          <tr>
            <th>ID</th><th>Pedido</th><th>Canal</th><th>Conta</th>
            <th>SKU</th><th>Motivo</th><th class="text-right">Valor</th>
            <th>Aberta</th><th>Prazo</th><th>Status</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="d in devs" :key="d.id">
            <td class="font-mono text-xs">{{ d.id }}</td>
            <td class="font-mono text-xs text-muted-foreground">{{ d.pedido }}</td>
            <td><span class="pill pill-muted">{{ d.canal }}</span></td>
            <td>{{ d.conta }}</td>
            <td class="font-mono text-xs">{{ d.sku }}</td>
            <td class="text-muted-foreground">{{ d.motivo }}</td>
            <td class="text-right tabular-nums">{{ brl(d.valor) }}</td>
            <td class="text-muted-foreground">{{ d.abertaEm }}</td>
            <td class="text-muted-foreground">{{ d.prazo }}</td>
            <td><span :class="statusPill(d.status)">{{ statusLabel(d.status) }}</span></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
