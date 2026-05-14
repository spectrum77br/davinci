<script setup lang="ts">
import { RefreshCw, Search, Loader2, AlertCircle, Pencil, Check, X } from 'lucide-vue-next'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'margem', action: 'view' },
})

type MargensStatus = 'Pendente' | 'Reprovado' | 'Aprovado'

type Margem = {
  id: string
  created_at: string
  data: string | null
  pedido_bling: number | null
  pedido_plataforma: string | null
  plataforma: string | null
  conta: string | null
  sku: string | null
  produtos: string | null
  custo: number | null
  lucro: number | null
  margem: number | null
  margem_min: number | null
  status: MargensStatus
  observacao: string | null
}

const STATUS_OPTIONS: MargensStatus[] = ['Pendente', 'Reprovado', 'Aprovado']
const STATUS_CLS: Record<MargensStatus, string> = {
  Pendente:  'bg-amber-500/15 text-amber-400 border-amber-500/40',
  Reprovado: 'bg-red-500/15 text-red-400 border-red-500/40',
  Aprovado:  'bg-emerald-500/15 text-emerald-400 border-emerald-500/40',
}

const { api } = useApi()
const canEdit = useCan('margem', 'edit')

const items = ref<Margem[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const search = ref('')
const filterStatus = ref<'all' | MargensStatus>('Pendente')

function apiError(e: any) {
  const detail = e?.data?.detail
  if (detail && typeof detail === 'object') return detail.message || detail.code || e?.message || 'erro'
  return detail || e?.message || 'erro'
}

async function load() {
  loading.value = true
  error.value = null
  try {
    items.value = await api<Margem[]>('/api/margens')
  } catch (e: any) {
    error.value = apiError(e)
  } finally {
    loading.value = false
  }
}
await load()

function brl(v: number | null | undefined) {
  if (v == null) return '—'
  return v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
}

function pct(v: number | null | undefined) {
  if (v == null) return '—'
  return `${(v * 100).toFixed(1)}%`
}

function fmtDate(v: string | null) {
  if (!v) return '—'
  return new Date(v).toLocaleDateString('pt-BR')
}

const filtered = computed(() => {
  let list = items.value
  if (filterStatus.value !== 'all') {
    list = list.filter((r) => r.status === filterStatus.value)
  }
  const q = search.value.trim().toLowerCase()
  if (q) {
    list = list.filter(
      (r) =>
        String(r.pedido_bling ?? '').includes(q) ||
        (r.pedido_plataforma || '').toLowerCase().includes(q) ||
        (r.plataforma || '').toLowerCase().includes(q) ||
        (r.conta || '').toLowerCase().includes(q) ||
        (r.sku || '').toLowerCase().includes(q) ||
        (r.produtos || '').toLowerCase().includes(q),
    )
  }
  return list
})

const counts = computed(() => {
  const c = { Pendente: 0, Reprovado: 0, Aprovado: 0 } as Record<MargensStatus, number>
  for (const r of items.value) c[r.status]++
  return c
})

async function setStatus(row: Margem, value: MargensStatus) {
  if (!canEdit.value || value === row.status) return
  const prev = row.status
  row.status = value
  try {
    const updated = await api<Margem>(`/api/margens/${row.id}`, {
      method: 'PATCH',
      body: { status: value },
    })
    Object.assign(row, updated)
  } catch (e: any) {
    row.status = prev
    error.value = apiError(e)
  }
}

const editingObs = ref<string | null>(null)
const obsDraft = ref('')

function startEditObs(row: Margem) {
  if (!canEdit.value) return
  editingObs.value = row.id
  obsDraft.value = row.observacao ?? ''
}

function cancelEditObs() {
  editingObs.value = null
  obsDraft.value = ''
}

async function saveObs(row: Margem) {
  const next = obsDraft.value.trim() || null
  if (next === (row.observacao ?? null)) {
    cancelEditObs()
    return
  }
  try {
    const updated = await api<Margem>(`/api/margens/${row.id}`, {
      method: 'PATCH',
      body: { observacao: next },
    })
    Object.assign(row, updated)
  } catch (e: any) {
    error.value = apiError(e)
  } finally {
    cancelEditObs()
  }
}
</script>

<template>
  <div class="space-y-5">
    <PageHeader title="Margem" description="Margem por pedido — aprovar, reprovar ou deixar pendente.">
      <template #actions>
        <Button size="sm" variant="outline" :disabled="loading" @click="load">
          <RefreshCw class="size-4 mr-1.5" :class="{ 'animate-spin': loading }" />
          atualizar
        </Button>
      </template>
    </PageHeader>

    <div v-if="error" class="flex items-center gap-2 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-400">
      <AlertCircle class="size-4" />
      {{ error }}
    </div>

    <div class="grid grid-cols-2 lg:grid-cols-4 gap-3">
      <StatCard label="Total" :value="items.length" />
      <StatCard label="Pendente" :value="counts.Pendente" />
      <StatCard label="Aprovado" :value="counts.Aprovado" />
      <StatCard label="Reprovado" :value="counts.Reprovado" />
    </div>

    <div class="flex flex-wrap items-center gap-2">
      <div class="relative">
        <Search class="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
        <input
          v-model="search"
          class="pl-8 pr-3 py-1.5 text-sm rounded-md border bg-background w-64"
          placeholder="buscar pedido, sku, conta…"
        />
      </div>
      <select
        v-model="filterStatus"
        class="text-sm rounded-md border bg-background px-2 py-1.5"
      >
        <option value="all">todos status</option>
        <option v-for="s in STATUS_OPTIONS" :key="s" :value="s">{{ s }}</option>
      </select>
      <span class="ml-auto text-xs text-muted-foreground">
        {{ filtered.length }} de {{ items.length }}
      </span>
    </div>

    <div class="table-card overflow-x-auto">
      <table class="w-full text-sm min-w-[1240px]">
        <thead>
          <tr>
            <th>Data</th>
            <th>Pedido</th>
            <th>Plataforma</th>
            <th>Conta</th>
            <th>SKU</th>
            <th>Produto</th>
            <th class="text-right">Custo</th>
            <th class="text-right">Saldo</th>
            <th class="text-right">Margem</th>
            <th class="text-right">Margem mín.</th>
            <th>Status</th>
            <th>Observação</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading && !items.length">
            <td colspan="12" class="text-center py-8 text-muted-foreground">
              <Loader2 class="size-4 inline animate-spin mr-1.5" /> carregando…
            </td>
          </tr>
          <tr v-else-if="!filtered.length">
            <td colspan="12" class="text-center py-8 text-muted-foreground">
              sem registros
            </td>
          </tr>
          <tr v-for="r in filtered" :key="r.id">
            <td class="whitespace-nowrap text-muted-foreground">{{ fmtDate(r.data) }}</td>
            <td class="tabular-nums">
              <div class="font-medium">{{ r.pedido_bling ?? '—' }}</div>
              <div v-if="r.pedido_plataforma" class="text-xs text-muted-foreground">{{ r.pedido_plataforma }}</div>
            </td>
            <td class="uppercase text-xs text-muted-foreground">{{ r.plataforma || '—' }}</td>
            <td>{{ r.conta || '—' }}</td>
            <td class="font-mono text-xs">{{ r.sku || '—' }}</td>
            <td class="max-w-[280px] truncate" :title="r.produtos || ''">{{ r.produtos || '—' }}</td>
            <td class="text-right tabular-nums text-muted-foreground">{{ brl(r.custo) }}</td>
            <td class="text-right tabular-nums font-medium" :class="r.lucro != null && r.lucro >= 0 ? 'text-emerald-500' : 'text-red-500'">{{ brl(r.lucro) }}</td>
            <td class="text-right tabular-nums font-medium" :class="r.margem != null && r.margem_min != null ? (r.margem >= r.margem_min ? 'text-emerald-500' : 'text-red-500') : (r.margem != null && r.margem >= 0 ? 'text-emerald-500' : 'text-red-500')">
              {{ pct(r.margem) }}
            </td>
            <td class="text-right tabular-nums text-muted-foreground">{{ pct(r.margem_min) }}</td>
            <td>
              <select
                :value="r.status"
                :disabled="!canEdit"
                class="pill border text-xs font-medium px-2 py-1 rounded-md cursor-pointer disabled:cursor-default disabled:opacity-70"
                :class="STATUS_CLS[r.status]"
                @change="(e) => setStatus(r, (e.target as HTMLSelectElement).value as MargensStatus)"
              >
                <option v-for="s in STATUS_OPTIONS" :key="s" :value="s">{{ s }}</option>
              </select>
            </td>
            <td class="max-w-[260px]">
              <div v-if="editingObs === r.id" class="flex items-center gap-1">
                <input
                  v-model="obsDraft"
                  class="flex-1 px-2 py-1 text-xs rounded-md border bg-background"
                  autofocus
                  @keydown.enter="saveObs(r)"
                  @keydown.esc="cancelEditObs"
                />
                <button class="p-1 text-emerald-500 hover:opacity-80" @click="saveObs(r)">
                  <Check class="size-3.5" />
                </button>
                <button class="p-1 text-muted-foreground hover:opacity-80" @click="cancelEditObs">
                  <X class="size-3.5" />
                </button>
              </div>
              <button
                v-else
                class="flex items-center gap-1.5 text-left text-xs text-muted-foreground hover:text-foreground w-full truncate disabled:cursor-default"
                :disabled="!canEdit"
                :title="r.observacao || ''"
                @click="startEditObs(r)"
              >
                <span class="truncate">{{ r.observacao || (canEdit ? 'adicionar…' : '—') }}</span>
                <Pencil v-if="canEdit" class="size-3 shrink-0 opacity-50" />
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
