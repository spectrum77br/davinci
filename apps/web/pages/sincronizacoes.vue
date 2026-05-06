<script setup lang="ts">
import { RefreshCw, Play, Pause, Activity, AlertCircle, CheckCircle2, Clock, Filter } from 'lucide-vue-next'

definePageMeta({ middleware: ['permission'], permission: { resource: 'sincronizacoes', action: 'view' } })

type Job = {
  id: string
  scope: string
  kind: 'pedidos' | 'anuncios' | 'produtos' | 'estoque' | 'precos'
  schedule: string
  ultima: string
  duracao: string
  ok: number
  err: number
  status: 'idle' | 'rodando' | 'falha' | 'pausado'
  proxima: string
}

const jobs: Job[] = [
  { id: '1', scope: 'Bling — Aguiar',     kind: 'pedidos',  schedule: '*/5 min',  ultima: '2 min',  duracao: '12s',     ok: 87,   err: 0,  status: 'idle',     proxima: '3 min' },
  { id: '2', scope: 'ML — Luno',          kind: 'anuncios', schedule: '*/15 min', ultima: '14 min', duracao: '38s',     ok: 142,  err: 3,  status: 'idle',     proxima: '1 min' },
  { id: '3', scope: 'Shopee — Jlas',      kind: 'pedidos',  schedule: '*/5 min',  ultima: '22 min', duracao: '9s',      ok: 56,   err: 0,  status: 'rodando',  proxima: '—' },
  { id: '4', scope: 'Bling — custos',     kind: 'produtos', schedule: '0 * * * *',ultima: '1 h',    duracao: '2m 14s',  ok: 1873, err: 0,  status: 'idle',     proxima: '12 min' },
  { id: '5', scope: 'Amazon — Eron',      kind: 'pedidos',  schedule: '*/10 min', ultima: '3 h',    duracao: 'falhou',  ok: 0,    err: 12, status: 'falha',    proxima: '—' },
  { id: '6', scope: 'ML — todas contas',  kind: 'precos',   schedule: 'manual',   ultima: 'ontem',  duracao: '4m 02s',  ok: 412,  err: 0,  status: 'idle',     proxima: '—' },
  { id: '7', scope: 'Bling — estoque',    kind: 'estoque',  schedule: '*/30 min', ultima: '8 min',  duracao: '52s',     ok: 1873, err: 0,  status: 'idle',     proxima: '22 min' },
  { id: '8', scope: 'Magalu — Barbosa',   kind: 'pedidos',  schedule: '*/15 min', ultima: '11 min', duracao: '6s',      ok: 14,   err: 0,  status: 'pausado',  proxima: '—' },
]

function pill(s: Job['status']) {
  if (s === 'rodando') return 'pill-info'
  if (s === 'falha') return 'pill-danger'
  if (s === 'pausado') return 'pill-warning'
  return 'pill-success'
}
function statusLabel(s: Job['status']) {
  return s === 'idle' ? 'OK' : s
}

const counts = computed(() => ({
  ok: jobs.filter(j => j.status === 'idle').length,
  rodando: jobs.filter(j => j.status === 'rodando').length,
  falha: jobs.filter(j => j.status === 'falha').length,
  pausado: jobs.filter(j => j.status === 'pausado').length,
}))
</script>

<template>
  <div class="space-y-5">
    <PageHeader title="Sincronizações" description="Jobs agendados que importam pedidos, anúncios, produtos, estoque e preços.">
      <template #actions>
        <Button size="sm" variant="outline"><Filter class="size-4 mr-1.5" /> filtros</Button>
        <Button size="sm">
          <RefreshCw class="size-4 mr-1.5" /> rodar todos
        </Button>
      </template>
    </PageHeader>

    <div class="grid grid-cols-2 lg:grid-cols-4 gap-3">
      <StatCard label="OK" :value="counts.ok" :icon="CheckCircle2" tone="success" />
      <StatCard label="Em execução" :value="counts.rodando" :icon="Activity" />
      <StatCard label="Com falha" :value="counts.falha" :icon="AlertCircle" tone="danger" />
      <StatCard label="Pausados" :value="counts.pausado" :icon="Clock" />
    </div>

    <div class="table-card">
      <table class="w-full">
        <thead>
          <tr>
            <th>Escopo</th>
            <th>Tipo</th>
            <th>Cron</th>
            <th>Última</th>
            <th>Duração</th>
            <th class="text-right">OK</th>
            <th class="text-right">Erros</th>
            <th>Próxima</th>
            <th>Status</th>
            <th class="text-right">Ações</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="j in jobs" :key="j.id">
            <td class="font-medium">{{ j.scope }}</td>
            <td><span class="pill pill-muted">{{ j.kind }}</span></td>
            <td class="font-mono text-xs text-muted-foreground">{{ j.schedule }}</td>
            <td class="text-muted-foreground">{{ j.ultima }}</td>
            <td class="text-muted-foreground">{{ j.duracao }}</td>
            <td class="text-right tabular-nums">{{ j.ok }}</td>
            <td class="text-right tabular-nums">
              <span :class="j.err > 0 ? 'text-red-600 font-medium' : 'text-muted-foreground'">{{ j.err }}</span>
            </td>
            <td class="text-muted-foreground">{{ j.proxima }}</td>
            <td><span :class="pill(j.status)">{{ statusLabel(j.status) }}</span></td>
            <td class="text-right">
              <Button size="sm" variant="ghost" class="h-7 w-7 p-0" :title="j.status === 'pausado' ? 'retomar' : 'pausar'">
                <Play v-if="j.status === 'pausado'" class="size-3.5" />
                <Pause v-else class="size-3.5" />
              </Button>
              <Button size="sm" variant="ghost" class="h-7 w-7 p-0" title="rodar agora">
                <RefreshCw class="size-3.5" />
              </Button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
