<script setup lang="ts">
import { Receipt, Filter, FileDown, CheckCircle2, Clock } from 'lucide-vue-next'

definePageMeta({ middleware: ['permission'], permission: { resource: 'reembolso', action: 'view' } })

type Reemb = {
  id: string
  pedido: string
  canal: string
  conta: string
  tipo: 'cliente' | 'marketplace'
  valor: number
  forma: string
  data: string
  status: 'pendente' | 'pago' | 'rejeitado'
}

const linhas: Reemb[] = [
  { id: 'R-2089', pedido: 'ML-298311', canal: 'ML',     conta: 'Aguiar',  tipo: 'cliente',     valor: 5499, forma: 'estorno cartão', data: '02/05', status: 'pendente' },
  { id: 'R-2088', pedido: 'SH-882190', canal: 'Shopee', conta: 'Jlas',    tipo: 'cliente',     valor: 1899, forma: 'saldo Shopee',   data: '01/05', status: 'pago' },
  { id: 'R-2087', pedido: 'AMZ-77321', canal: 'Amazon', conta: 'Eron',    tipo: 'marketplace', valor: 4899, forma: 'crédito conta',  data: '30/04', status: 'pago' },
  { id: 'R-2086', pedido: 'ML-298099', canal: 'ML',     conta: 'Luno',    tipo: 'cliente',     valor: 8290, forma: 'PIX',            data: '29/04', status: 'rejeitado' },
  { id: 'R-2085', pedido: 'MGL-12388', canal: 'Magalu', conta: 'Barbosa', tipo: 'cliente',     valor: 489,  forma: 'estorno cartão', data: '28/04', status: 'pago' },
  { id: 'R-2084', pedido: 'ML-297844', canal: 'ML',     conta: 'Aguiar',  tipo: 'cliente',     valor: 3990, forma: 'PIX',            data: '27/04', status: 'pendente' },
]

function brl(v: number) {
  return v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
}
function pill(s: Reemb['status']) {
  if (s === 'pago') return 'pill-success'
  if (s === 'pendente') return 'pill-warning'
  return 'pill-danger'
}

const tot = computed(() => ({
  pendente: linhas.filter(l => l.status === 'pendente').reduce((a,l) => a+l.valor, 0),
  pago: linhas.filter(l => l.status === 'pago').reduce((a,l) => a+l.valor, 0),
}))
</script>

<template>
  <div class="space-y-5">
    <PageHeader title="Reembolso" description="Reembolsos a clientes e estornos do marketplace.">
      <template #actions>
        <Button size="sm" variant="outline">
          <FileDown class="size-4 mr-1.5" /> exportar mês
        </Button>
        <Button size="sm">
          <Receipt class="size-4 mr-1.5" /> processar lote
        </Button>
      </template>
    </PageHeader>

    <div class="grid grid-cols-2 lg:grid-cols-4 gap-3">
      <StatCard label="Pendentes" :value="linhas.filter(l => l.status === 'pendente').length" :icon="Clock" tone="warning" />
      <StatCard label="Pagos (mês)" :value="linhas.filter(l => l.status === 'pago').length" :icon="CheckCircle2" tone="success" />
      <StatCard label="A pagar" :value="brl(tot.pendente)" />
      <StatCard label="Pago no mês" :value="brl(tot.pago)" />
    </div>

    <div class="flex items-center gap-2">
      <Input placeholder="buscar pedido…" class="w-72" />
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
            <th>Tipo</th><th>Forma</th>
            <th class="text-right">Valor</th><th>Data</th><th>Status</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="l in linhas" :key="l.id">
            <td class="font-mono text-xs">{{ l.id }}</td>
            <td class="font-mono text-xs text-muted-foreground">{{ l.pedido }}</td>
            <td><span class="pill pill-muted">{{ l.canal }}</span></td>
            <td>{{ l.conta }}</td>
            <td class="text-muted-foreground capitalize">{{ l.tipo }}</td>
            <td class="text-muted-foreground">{{ l.forma }}</td>
            <td class="text-right tabular-nums font-medium">{{ brl(l.valor) }}</td>
            <td class="text-muted-foreground">{{ l.data }}</td>
            <td><span :class="pill(l.status)">{{ l.status }}</span></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
