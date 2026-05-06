<script setup lang="ts">
import { Truck, Upload, AlertTriangle, CheckCircle2, Filter } from 'lucide-vue-next'

definePageMeta({ middleware: ['permission'], permission: { resource: 'conciliacao_frete', action: 'view' } })

type Linha = {
  pedido: string
  canal: string
  conta: string
  data: string
  esperado: number
  cobrado: number
  diff: number
  status: 'ok' | 'divergente' | 'pendente'
}

const linhas: Linha[] = [
  { pedido: 'ML-298311',  canal: 'ML',     conta: 'Aguiar',  data: '02/05', esperado: 24.90, cobrado: 24.90,  diff: 0,      status: 'ok' },
  { pedido: 'ML-298287',  canal: 'ML',     conta: 'Aguiar',  data: '02/05', esperado: 19.50, cobrado: 32.40,  diff: 12.90,  status: 'divergente' },
  { pedido: 'SH-882190',  canal: 'Shopee', conta: 'Jlas',    data: '02/05', esperado: 9.99,  cobrado: 9.99,   diff: 0,      status: 'ok' },
  { pedido: 'SH-882203',  canal: 'Shopee', conta: 'Mini',    data: '01/05', esperado: 9.99,  cobrado: 18.40,  diff: 8.41,   status: 'divergente' },
  { pedido: 'AMZ-77321',  canal: 'Amazon', conta: 'Eron',    data: '01/05', esperado: 0,     cobrado: 0,      diff: 0,      status: 'ok' },
  { pedido: 'ML-298120',  canal: 'ML',     conta: 'Luno',    data: '30/04', esperado: 28.40, cobrado: 0,      diff: 0,      status: 'pendente' },
  { pedido: 'MGL-12388',  canal: 'Magalu', conta: 'Barbosa', data: '30/04', esperado: 14.90, cobrado: 14.90,  diff: 0,      status: 'ok' },
  { pedido: 'ML-298099',  canal: 'ML',     conta: 'Aguiar',  data: '29/04', esperado: 19.50, cobrado: 41.20,  diff: 21.70,  status: 'divergente' },
]

function brl(v: number) {
  return v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
}

const totDiff = computed(() => linhas.filter(l => l.status === 'divergente').reduce((a, l) => a + l.diff, 0))
const counts = computed(() => ({
  ok: linhas.filter(l => l.status === 'ok').length,
  div: linhas.filter(l => l.status === 'divergente').length,
  pend: linhas.filter(l => l.status === 'pendente').length,
}))

function statusPill(s: Linha['status']) {
  if (s === 'ok') return 'pill-success'
  if (s === 'divergente') return 'pill-danger'
  return 'pill-warning'
}
</script>

<template>
  <div class="space-y-5">
    <PageHeader title="Conciliação de frete" description="Compare frete esperado pelo Bling com o cobrado pelo marketplace.">
      <template #actions>
        <Button size="sm" variant="outline"><Upload class="size-4 mr-1.5" /> importar planilha</Button>
        <Button size="sm">contestar divergências</Button>
      </template>
    </PageHeader>

    <div class="grid grid-cols-2 lg:grid-cols-4 gap-3">
      <StatCard label="Pedidos OK" :value="counts.ok" :icon="CheckCircle2" tone="success" />
      <StatCard label="Divergentes" :value="counts.div" :icon="AlertTriangle" tone="danger" />
      <StatCard label="Aguardando cobrança" :value="counts.pend" />
      <StatCard label="Prejuízo acumulado" :value="brl(totDiff)" hint="valor a contestar" />
    </div>

    <div class="flex items-center gap-2">
      <Input placeholder="buscar pedido…" class="w-72" />
      <select class="h-9 rounded-md border bg-background px-2 text-sm">
        <option>todos canais</option>
        <option>ML</option><option>Shopee</option><option>Amazon</option><option>Magalu</option>
      </select>
      <select class="h-9 rounded-md border bg-background px-2 text-sm">
        <option>todos status</option>
        <option>ok</option><option>divergente</option><option>pendente</option>
      </select>
      <Button size="sm" variant="ghost"><Filter class="size-4 mr-1.5" /> filtros</Button>
    </div>

    <div class="table-card">
      <table class="w-full">
        <thead>
          <tr>
            <th>Pedido</th><th>Canal</th><th>Conta</th><th>Data</th>
            <th class="text-right">Esperado</th><th class="text-right">Cobrado</th>
            <th class="text-right">Diferença</th><th>Status</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="l in linhas" :key="l.pedido">
            <td class="font-mono text-xs">{{ l.pedido }}</td>
            <td><span class="pill pill-muted">{{ l.canal }}</span></td>
            <td>{{ l.conta }}</td>
            <td class="text-muted-foreground">{{ l.data }}</td>
            <td class="text-right tabular-nums">{{ brl(l.esperado) }}</td>
            <td class="text-right tabular-nums">{{ brl(l.cobrado) }}</td>
            <td class="text-right tabular-nums">
              <span :class="l.diff > 0 ? 'text-red-600 font-medium' : 'text-muted-foreground'">
                {{ l.diff > 0 ? `+${brl(l.diff)}` : '—' }}
              </span>
            </td>
            <td><span :class="statusPill(l.status)">{{ l.status }}</span></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
