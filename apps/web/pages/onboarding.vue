<script setup lang="ts">
import { computed } from 'vue'
import {
  Plug, Building2, Package, Tag, Link2, RefreshCw,
  CheckCircle2, Circle, ArrowRight,
} from 'lucide-vue-next'

type Step = { key: string; done: boolean }
type DashboardOut = {
  kpis: Record<string, number>
  channels: { platform: string; listings: number; linked: number }[]
  recent_syncs: unknown[]
  onboarding: Step[]
  needs_onboarding: boolean
}

const { api } = useApi()
const { data, refresh, pending } = await useAsyncData('onboarding', () => api<DashboardOut>('/api/dashboard'))

const stepDefs = [
  {
    key: 'company',
    icon: Building2,
    title: 'Cadastrar empresa e lojas',
    body: 'Crie a razão social e abra as lojas por marketplace.',
    cta: 'Empresas',
    to: '/companies',
  },
  {
    key: 'bling',
    icon: Plug,
    title: 'Conectar Bling',
    body: 'Autorize o ERP — origem dos produtos, custos e estoque.',
    cta: 'Integrações',
    to: '/integrations',
  },
  {
    key: 'products',
    icon: Package,
    title: 'Importar produtos do Bling',
    body: 'Sincronize SKUs, custos e estoque do ERP.',
    cta: 'Produtos',
    to: '/produtos',
  },
  {
    key: 'marketplaces',
    icon: Tag,
    title: 'Conectar marketplaces',
    body: 'Mercado Livre, Shopee, Amazon — autorize cada conta.',
    cta: 'Integrações',
    to: '/integrations',
  },
  {
    key: 'links',
    icon: Link2,
    title: 'Importar anúncios e vincular',
    body: 'Auto-vincular anúncios aos SKUs do Bling.',
    cta: 'Anúncios',
    to: '/anuncios',
  },
] as const

const steps = computed(() => {
  const status = new Map((data.value?.onboarding ?? []).map((s) => [s.key, s.done]))
  return stepDefs.map((s) => ({ ...s, done: status.get(s.key) ?? false }))
})

const completed = computed(() => steps.value.filter((s) => s.done).length)
const total = computed(() => steps.value.length)
const pct = computed(() => Math.round((completed.value / total.value) * 100))
const allDone = computed(() => completed.value === total.value)
</script>

<template>
  <div class="max-w-3xl">
    <PageHeader
      title="Primeiros passos"
      description="Configure o DaVinci em 5 etapas. Pule e volte quando quiser."
    >
      <template #actions>
        <Button size="sm" variant="outline" :disabled="pending" @click="refresh()">
          <RefreshCw class="size-4 mr-1.5" :class="pending && 'animate-spin'" />
          Atualizar
        </Button>
      </template>
    </PageHeader>

    <div class="rounded-xl border bg-card p-5 mb-6">
      <div class="flex items-center mb-3">
        <div class="text-sm font-medium">Progresso</div>
        <span class="ml-auto text-xs text-muted-foreground tabular-nums">{{ completed }} de {{ total }}</span>
      </div>
      <div class="h-2 rounded-full bg-muted overflow-hidden">
        <div class="h-full bg-primary rounded-full transition-all duration-500" :style="{ width: pct + '%' }" />
      </div>
      <div v-if="allDone" class="mt-4 flex items-center gap-2 text-sm text-emerald-600">
        <CheckCircle2 class="size-4" />
        Tudo pronto. <NuxtLink to="/" class="underline ml-1">Ir para o dashboard</NuxtLink>.
      </div>
    </div>

    <ol class="space-y-3">
      <li
        v-for="(s, i) in steps"
        :key="s.key"
        class="rounded-xl border bg-card p-4 flex gap-4 items-center"
      >
        <div
          class="size-10 rounded-lg grid place-items-center shrink-0"
          :class="s.done ? 'bg-emerald-500/10 text-emerald-600' : 'bg-muted text-muted-foreground'"
        >
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
          <Button size="sm" :variant="s.done ? 'outline' : 'default'">
            {{ s.cta }}
            <ArrowRight class="size-3.5 ml-1" />
          </Button>
        </NuxtLink>
      </li>
    </ol>
  </div>
</template>
