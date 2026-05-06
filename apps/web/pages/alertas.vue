<script setup lang="ts">
import { Bell, AlertTriangle, AlertCircle, Info, CheckCircle2 } from 'lucide-vue-next'

type Severity = 'info' | 'warning' | 'error' | 'success'

const { items, unread, total, loading, refresh, markRead, markAllRead } = useAlerts()

const filtroTone = ref<string>('')
const onlyUnread = ref(false)

await refresh({ limit: 100 })

const filtered = computed(() =>
  items.value.filter(a => {
    if (filtroTone.value && a.severity !== filtroTone.value) return false
    if (onlyUnread.value && a.read_at) return false
    return true
  }),
)

function icon(t: Severity) {
  if (t === 'error') return AlertCircle
  if (t === 'warning') return AlertTriangle
  if (t === 'success') return CheckCircle2
  return Info
}
function tone(t: Severity) {
  if (t === 'error') return 'text-red-600 bg-red-500/10'
  if (t === 'warning') return 'text-amber-600 bg-amber-500/10'
  if (t === 'success') return 'text-emerald-600 bg-emerald-500/10'
  return 'text-primary bg-primary/10'
}

function timeAgo(iso: string) {
  const ms = Date.now() - new Date(iso).getTime()
  const m = Math.floor(ms / 60000)
  if (m < 1) return 'agora'
  if (m < 60) return `${m} min`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h} h`
  return `${Math.floor(h / 24)} d`
}

async function onMarkRead(id: string) {
  await markRead(id)
}

async function onMarkAllRead() {
  await markAllRead()
}
</script>

<template>
  <div class="space-y-5">
    <PageHeader title="Alertas" description="Eventos que pedem atenção — estoque baixo, anúncio banido, sync falhou.">
      <template #actions>
        <Button size="sm" variant="outline" :disabled="unread === 0 || loading" @click="onMarkAllRead">
          marcar todos lidos
        </Button>
      </template>
    </PageHeader>

    <div class="flex items-center gap-2">
      <select v-model="filtroTone" class="h-9 rounded-md border bg-background px-2 text-sm">
        <option value="">todas severidades</option>
        <option value="error">erros</option>
        <option value="warning">avisos</option>
        <option value="info">informativos</option>
        <option value="success">sucesso</option>
      </select>
      <label class="inline-flex items-center gap-2 text-sm cursor-pointer">
        <input v-model="onlyUnread" type="checkbox" class="size-4 accent-primary">
        somente não lidos
      </label>
      <span class="ml-auto text-xs text-muted-foreground">
        {{ unread }} não lidos · {{ total }} total
      </span>
    </div>

    <ul class="space-y-2">
      <li
        v-for="a in filtered" :key="a.id"
        class="rounded-xl border bg-card p-4 flex gap-4 items-start"
        :class="!a.read_at && 'ring-1 ring-primary/20'"
      >
        <div class="size-9 rounded-lg grid place-items-center shrink-0" :class="tone(a.severity)">
          <component :is="icon(a.severity)" class="size-4" />
        </div>
        <div class="min-w-0 flex-1">
          <div class="flex items-center gap-2 mb-0.5">
            <h3 class="font-medium text-sm leading-tight">{{ a.title }}</h3>
            <span v-if="!a.read_at" class="size-1.5 rounded-full bg-primary" />
          </div>
          <p v-if="a.message" class="text-sm text-muted-foreground">{{ a.message }}</p>
          <div class="flex items-center gap-2 mt-2 text-[11px] text-muted-foreground">
            <span class="pill pill-muted">{{ a.type }}</span>
            <span>·</span>
            <span>{{ timeAgo(a.created_at) }} atrás</span>
          </div>
        </div>
        <Button v-if="!a.read_at" size="sm" variant="ghost" @click="onMarkRead(a.id)">marcar lido</Button>
      </li>

      <EmptyState
        v-if="!loading && filtered.length === 0"
        :icon="Bell"
        title="Nada por aqui"
        description="Nenhum alerta corresponde aos filtros."
      />
    </ul>
  </div>
</template>
