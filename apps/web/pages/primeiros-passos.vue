<script setup lang="ts">
import { Building2, Plug, Package, DollarSign, Users, CheckCircle2, Circle } from 'lucide-vue-next'

const steps = [
  {
    icon: Building2,
    title: 'Cadastrar empresas e lojas',
    body: 'Crie a razão social e abra as lojas por marketplace.',
    cta: 'Ir para Empresas',
    to: '/companies',
    done: true,
  },
  {
    icon: Plug,
    title: 'Conectar integrações (OAuth)',
    body: 'Bling, Mercado Livre, Shopee, Amazon — autorize cada conta.',
    cta: 'Ir para Integrações',
    to: '/integrations',
    done: true,
  },
  {
    icon: Package,
    title: 'Sincronizar produtos do Bling',
    body: 'Importe SKUs, custos e estoque do ERP.',
    cta: 'Ir para Produtos',
    to: '/produtos',
    done: false,
  },
  {
    icon: DollarSign,
    title: 'Configurar tabela de preços',
    body: 'Defina margens, kits e fórmula por canal.',
    cta: 'Ir para Tabela',
    to: '/tabela-precos',
    done: false,
  },
  {
    icon: Users,
    title: 'Convidar equipe',
    body: 'Crie usuários e defina a matriz de permissões.',
    cta: 'Ir para Usuários',
    to: '/users',
    done: false,
  },
]

const completed = steps.filter((s) => s.done).length
const total = steps.length
const pct = Math.round((completed / total) * 100)
</script>

<template>
  <div class="max-w-3xl">
    <PageHeader
      title="Primeiros passos"
      description="Configure o DaVinci em 5 etapas. Você pode pular e voltar quando quiser."
    />

    <div class="rounded-xl border bg-card p-5 mb-6">
      <div class="flex items-center mb-3">
        <div class="text-sm font-medium">Progresso</div>
        <span class="ml-auto text-xs text-muted-foreground">{{ completed }} de {{ total }}</span>
      </div>
      <div class="h-2 rounded-full bg-muted overflow-hidden">
        <div class="h-full bg-primary rounded-full transition-all duration-500" :style="{ width: pct + '%' }" />
      </div>
    </div>

    <ol class="space-y-3">
      <li
        v-for="(s, i) in steps"
        :key="i"
        class="rounded-xl border bg-card p-4 flex gap-4 items-center"
      >
        <div class="size-10 rounded-lg grid place-items-center shrink-0"
             :class="s.done ? 'bg-emerald-500/10 text-emerald-600' : 'bg-muted text-muted-foreground'">
          <component :is="s.icon" class="size-5" />
        </div>
        <div class="min-w-0 flex-1">
          <div class="flex items-center gap-2">
            <span class="text-xs text-muted-foreground tabular-nums">0{{ i + 1 }}</span>
            <h3 class="font-medium leading-tight">{{ s.title }}</h3>
            <CheckCircle2 v-if="s.done" class="size-4 text-emerald-600" />
            <Circle v-else class="size-4 text-muted-foreground" />
          </div>
          <p class="text-sm text-muted-foreground mt-0.5">{{ s.body }}</p>
        </div>
        <NuxtLink :to="s.to">
          <Button size="sm" :variant="s.done ? 'outline' : 'default'">{{ s.cta }}</Button>
        </NuxtLink>
      </li>
    </ol>
  </div>
</template>
