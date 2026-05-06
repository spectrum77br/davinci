<script setup lang="ts">
import { ListChecks, Plus, Filter, Calendar, User } from 'lucide-vue-next'

definePageMeta({ middleware: ['permission'], permission: { resource: 'tarefas', action: 'view' } })

type Tarefa = {
  id: string
  titulo: string
  responsavel: string
  prazo: string
  prioridade: 'baixa' | 'media' | 'alta'
  vinculo?: string
  status: 'a_fazer' | 'em_andamento' | 'concluida'
}

const tarefas: Tarefa[] = [
  { id: 'T-431', titulo: 'Reconectar OAuth Bling Aguiar',     responsavel: 'spectrum',  prazo: 'hoje',     prioridade: 'alta',  vinculo: 'integrations', status: 'a_fazer' },
  { id: 'T-430', titulo: 'Revisar margem TikTok < 8%',        responsavel: 'eron',      prazo: '07/05',    prioridade: 'alta',  vinculo: 'margem',       status: 'em_andamento' },
  { id: 'T-429', titulo: 'Contestar 3 fretes ML divergentes', responsavel: 'aguiar',    prazo: '06/05',    prioridade: 'media', vinculo: 'frete',        status: 'a_fazer' },
  { id: 'T-428', titulo: 'Cadastrar 12 SKUs novos no Bling',  responsavel: 'spectrum',  prazo: '10/05',    prioridade: 'media', vinculo: 'produtos',     status: 'a_fazer' },
  { id: 'T-427', titulo: 'Negociar comissão Shopee Jlas',     responsavel: 'jlas',      prazo: '15/05',    prioridade: 'baixa',                          status: 'em_andamento' },
  { id: 'T-426', titulo: 'Atualizar fotos categoria mala',    responsavel: 'mini',      prazo: 'ontem',    prioridade: 'media', vinculo: 'anuncios',     status: 'concluida' },
  { id: 'T-425', titulo: 'Conferir devolução AMZ-77321',      responsavel: 'eron',      prazo: '04/05',    prioridade: 'alta',  vinculo: 'devolucoes',   status: 'concluida' },
]

const colunas: { key: Tarefa['status']; label: string; pill: string }[] = [
  { key: 'a_fazer',       label: 'A fazer',       pill: 'pill-muted' },
  { key: 'em_andamento',  label: 'Em andamento',  pill: 'pill-info' },
  { key: 'concluida',     label: 'Concluídas',    pill: 'pill-success' },
]

function porStatus(s: Tarefa['status']) {
  return tarefas.filter(t => t.status === s)
}
function prioPill(p: Tarefa['prioridade']) {
  if (p === 'alta') return 'pill-danger'
  if (p === 'media') return 'pill-warning'
  return 'pill-muted'
}
function prazoColor(p: string) {
  if (p === 'hoje' || p === 'ontem') return 'text-red-600 font-medium'
  return 'text-muted-foreground'
}
</script>

<template>
  <div class="space-y-5">
    <PageHeader title="Tarefas" description="Acompanhe pendências do time vinculadas a contas, pedidos e operações.">
      <template #actions>
        <Button size="sm" variant="outline"><Filter class="size-4 mr-1.5" /> filtros</Button>
        <Button size="sm"><Plus class="size-4 mr-1.5" /> nova tarefa</Button>
      </template>
    </PageHeader>

    <div class="grid grid-cols-3 gap-4">
      <div v-for="col in colunas" :key="col.key" class="rounded-xl border bg-muted/30 p-3 space-y-3">
        <div class="flex items-center px-1">
          <h2 class="text-sm font-semibold">{{ col.label }}</h2>
          <span class="pill pill-muted ml-2">{{ porStatus(col.key).length }}</span>
        </div>
        <div class="space-y-2">
          <article
            v-for="t in porStatus(col.key)"
            :key="t.id"
            class="rounded-lg border bg-card p-3 space-y-2 hover:shadow-sm transition-shadow cursor-pointer"
          >
            <div class="flex items-start gap-2">
              <span class="font-mono text-[10px] text-muted-foreground tabular-nums mt-0.5">{{ t.id }}</span>
              <span :class="prioPill(t.prioridade)" class="ml-auto">{{ t.prioridade }}</span>
            </div>
            <h3 class="text-sm font-medium leading-snug">{{ t.titulo }}</h3>
            <div class="flex items-center gap-2 text-xs">
              <User class="size-3 text-muted-foreground" />
              <span>{{ t.responsavel }}</span>
              <span class="ml-auto inline-flex items-center gap-1" :class="prazoColor(t.prazo)">
                <Calendar class="size-3" />
                {{ t.prazo }}
              </span>
            </div>
            <div v-if="t.vinculo" class="text-[11px]">
              <span class="pill pill-muted">{{ t.vinculo }}</span>
            </div>
          </article>

          <button
            class="w-full rounded-lg border border-dashed py-2 text-xs text-muted-foreground hover:bg-card hover:text-foreground transition-colors"
          >
            <Plus class="size-3 inline -mt-0.5 mr-1" /> adicionar
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
