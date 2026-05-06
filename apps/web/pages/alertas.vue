<script setup lang="ts">
import { Bell, AlertTriangle, AlertCircle, Info, CheckCircle2, Filter } from 'lucide-vue-next'

type Alerta = {
  id: string
  tone: 'danger' | 'warning' | 'info' | 'success'
  titulo: string
  body: string
  origem: string
  quando: string
  lido: boolean
}

const alertas = ref<Alerta[]>([
  { id: 'A-1042', tone: 'danger',  titulo: 'Token Bling expirou',           body: 'Conta Aguiar — reconectar OAuth para retomar sincronização.', origem: 'integrations', quando: '2 min',  lido: false },
  { id: 'A-1041', tone: 'warning', titulo: '14 SKUs sem estoque',           body: 'Categoria celular — checar reposição com fornecedor.',         origem: 'produtos',     quando: '14 min', lido: false },
  { id: 'A-1040', tone: 'info',    titulo: 'Nova devolução aberta',         body: 'pedido #ML-298311 — IPH15-128-PRT — solicitação cliente.',     origem: 'devolucoes',   quando: '22 min', lido: false },
  { id: 'A-1039', tone: 'danger',  titulo: 'Frete divergente acumulado',    body: 'R$ 198,40 em 8 pedidos — abrir contestação ML.',                 origem: 'frete',        quando: '1 h',    lido: true  },
  { id: 'A-1038', tone: 'warning', titulo: 'Margem TikTok < 8%',            body: 'Conta Atv — kit 1 vendendo abaixo da meta de 10%.',              origem: 'margem',       quando: '3 h',    lido: true  },
  { id: 'A-1037', tone: 'success', titulo: 'Sync de custos concluída',      body: '1873 SKUs atualizados em 2m 14s.',                               origem: 'sincronizacoes', quando: '4 h',  lido: true  },
])

const filtroTone = ref<string>('')
const onlyUnread = ref(false)

const filtered = computed(() =>
  alertas.value.filter(a => {
    if (filtroTone.value && a.tone !== filtroTone.value) return false
    if (onlyUnread.value && a.lido) return false
    return true
  }),
)

function icon(t: Alerta['tone']) {
  if (t === 'danger') return AlertCircle
  if (t === 'warning') return AlertTriangle
  if (t === 'success') return CheckCircle2
  return Info
}
function tone(t: Alerta['tone']) {
  if (t === 'danger') return 'text-red-600 bg-red-500/10'
  if (t === 'warning') return 'text-amber-600 bg-amber-500/10'
  if (t === 'success') return 'text-emerald-600 bg-emerald-500/10'
  return 'text-primary bg-primary/10'
}
const unreadCount = computed(() => alertas.value.filter(a => !a.lido).length)

function markAllRead() {
  alertas.value.forEach(a => (a.lido = true))
}
</script>

<template>
  <div class="space-y-5">
    <PageHeader title="Alertas" description="Eventos que pedem atenção — token expirado, frete divergente, estoque crítico.">
      <template #actions>
        <Button size="sm" variant="outline" :disabled="unreadCount === 0" @click="markAllRead">
          marcar todos lidos
        </Button>
      </template>
    </PageHeader>

    <div class="flex items-center gap-2">
      <select v-model="filtroTone" class="h-9 rounded-md border bg-background px-2 text-sm">
        <option value="">todos</option>
        <option value="danger">críticos</option>
        <option value="warning">avisos</option>
        <option value="info">informativos</option>
      </select>
      <label class="inline-flex items-center gap-2 text-sm cursor-pointer">
        <input v-model="onlyUnread" type="checkbox" class="size-4 accent-primary">
        somente não lidos
      </label>
      <Button size="sm" variant="ghost"><Filter class="size-4 mr-1.5" /> filtros</Button>
      <span class="ml-auto text-xs text-muted-foreground">
        {{ unreadCount }} não lidos · {{ alertas.length }} total
      </span>
    </div>

    <ul class="space-y-2">
      <li
        v-for="a in filtered" :key="a.id"
        class="rounded-xl border bg-card p-4 flex gap-4 items-start"
        :class="!a.lido && 'ring-1 ring-primary/20'"
      >
        <div class="size-9 rounded-lg grid place-items-center shrink-0" :class="tone(a.tone)">
          <component :is="icon(a.tone)" class="size-4" />
        </div>
        <div class="min-w-0 flex-1">
          <div class="flex items-center gap-2 mb-0.5">
            <h3 class="font-medium text-sm leading-tight">{{ a.titulo }}</h3>
            <span v-if="!a.lido" class="size-1.5 rounded-full bg-primary" />
          </div>
          <p class="text-sm text-muted-foreground">{{ a.body }}</p>
          <div class="flex items-center gap-2 mt-2 text-[11px] text-muted-foreground">
            <span class="pill pill-muted">{{ a.origem }}</span>
            <span>·</span>
            <span>{{ a.quando }} atrás</span>
          </div>
        </div>
        <Button size="sm" variant="ghost">ver</Button>
      </li>

      <EmptyState
        v-if="filtered.length === 0"
        :icon="Bell"
        title="Nada por aqui"
        description="Nenhum alerta corresponde aos filtros."
      />
    </ul>
  </div>
</template>
