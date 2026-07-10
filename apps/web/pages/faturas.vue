<script setup lang="ts">
import { computed, ref } from 'vue'
import { Plus, RefreshCw, X, Trash2 } from 'lucide-vue-next'

definePageMeta({
  middleware: ['admin'],
})

const { api } = useApi()

type Fatura = {
  id: string
  servico: string
  plano: string | null
  valor: string | number | null
  data_vencimento: string
  created_by: string | null
  created_at: string
  updated_at: string
}

const rows = ref<Fatura[]>([])
const loading = ref(false)
const error = ref<string | null>(null)

async function refresh() {
  loading.value = true
  error.value = null
  try {
    rows.value = await api<Fatura[]>('/api/faturas')
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    loading.value = false
  }
}

await refresh()

// ---- Create modal ----
const showNew = ref(false)
const draft = ref<{ servico: string; plano: string; valor: string; data_vencimento: string }>({
  servico: '',
  plano: '',
  valor: '',
  data_vencimento: '',
})
const creating = ref(false)
const createErr = ref<string | null>(null)

function openNew() {
  draft.value = { servico: '', plano: '', valor: '', data_vencimento: '' }
  createErr.value = null
  showNew.value = true
}

async function createFatura() {
  if (!draft.value.servico.trim() || !draft.value.data_vencimento) {
    createErr.value = 'preencha serviço e data de vencimento'
    return
  }
  creating.value = true
  createErr.value = null
  try {
    await api('/api/faturas', {
      method: 'POST',
      body: {
        servico: draft.value.servico.trim(),
        plano: draft.value.plano.trim() || null,
        valor: draft.value.valor.trim() ? Number(draft.value.valor.replace(',', '.')) : null,
        data_vencimento: draft.value.data_vencimento,
      },
    })
    showNew.value = false
    await refresh()
  } catch (e: any) {
    createErr.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    creating.value = false
  }
}

// ---- Edit modal ----
const editing = ref<Fatura | null>(null)
const editDraft = ref<{ servico: string; plano: string; valor: string; data_vencimento: string }>({
  servico: '',
  plano: '',
  valor: '',
  data_vencimento: '',
})
const saving = ref(false)
const editErr = ref<string | null>(null)

function openEdit(f: Fatura) {
  editing.value = f
  editDraft.value = {
    servico: f.servico,
    plano: f.plano || '',
    valor: f.valor != null ? String(f.valor) : '',
    data_vencimento: f.data_vencimento,
  }
  editErr.value = null
}

async function saveEdit() {
  if (!editing.value) return
  saving.value = true
  editErr.value = null
  try {
    await api(`/api/faturas/${editing.value.id}`, {
      method: 'PATCH',
      body: {
        servico: editDraft.value.servico.trim(),
        plano: editDraft.value.plano.trim() || null,
        valor: editDraft.value.valor.trim() ? Number(editDraft.value.valor.replace(',', '.')) : null,
        data_vencimento: editDraft.value.data_vencimento,
      },
    })
    editing.value = null
    await refresh()
  } catch (e: any) {
    editErr.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    saving.value = false
  }
}

async function removeFatura() {
  if (!editing.value) return
  if (!confirm('Remover esta fatura?')) return
  saving.value = true
  editErr.value = null
  try {
    await api(`/api/faturas/${editing.value.id}`, { method: 'DELETE' })
    editing.value = null
    await refresh()
  } catch (e: any) {
    editErr.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    saving.value = false
  }
}

function fmtDate(s: string | null) {
  if (!s) return '—'
  const [y, m, d] = s.split('-').map((n) => Number(n))
  if (!y || !m || !d) return s
  return new Date(y, m - 1, d).toLocaleDateString('pt-BR')
}

function fmtValor(v: string | number | null) {
  if (v == null || v === '') return '—'
  const n = Number(v)
  if (Number.isNaN(n)) return '—'
  return n.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

// Dias até o vencimento (negativo = já venceu). Compara datas locais.
function daysUntil(s: string): number {
  const [y, m, d] = s.split('-').map((n) => Number(n))
  const venc = new Date(y, m - 1, d)
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  venc.setHours(0, 0, 0, 0)
  return Math.round((venc.getTime() - today.getTime()) / 86_400_000)
}

// Ordem alfabética por serviço (case-insensitive, pt-BR, numérica) —
// mantém a lista organizada independente da ordem que o backend devolve.
const sortedRows = computed(() =>
  [...rows.value].sort((a, b) =>
    (a.servico || '').localeCompare(b.servico || '', 'pt-BR', { sensitivity: 'base', numeric: true }),
  ),
)

type Status = { label: string; cls: string }
function statusOf(f: Fatura): Status {
  const dd = daysUntil(f.data_vencimento)
  if (dd < 0) return { label: `vencida há ${-dd}d`, cls: 'border-red-500 text-red-500' }
  if (dd === 0) return { label: 'vence hoje', cls: 'border-red-500 text-red-500' }
  if (dd === 1) return { label: 'vence amanhã', cls: 'border-amber-500 text-amber-500' }
  if (dd <= 7) return { label: `${dd} dias`, cls: 'border-amber-500 text-amber-500' }
  return { label: `${dd} dias`, cls: 'border-emerald-500 text-emerald-500' }
}
</script>

<template>
  <div class="space-y-4">
    <div class="flex flex-wrap items-center gap-3">
      <h1 class="text-2xl font-semibold">Faturas</h1>
      <Button size="sm" variant="ghost" :disabled="loading" @click="refresh">
        <RefreshCw class="size-4 mr-1" /> recarregar
      </Button>
      <Button size="sm" class="ml-auto" @click="openNew">
        <Plus class="size-4 mr-1" /> Nova fatura
      </Button>
    </div>

    <p class="text-sm text-muted-foreground">
      Assinaturas e planos recorrentes. Você recebe um alerta na tela 1 dia antes do vencimento.
      Ao renovar, edite a data de vencimento pro próximo ciclo.
    </p>

    <div v-if="error" class="text-sm text-red-500">erro: {{ error }}</div>

    <!-- Desktop table -->
    <div class="hidden md:block border rounded-md overflow-x-auto">
      <table class="w-full text-sm min-w-[700px]">
        <thead class="bg-muted/40 text-left">
          <tr class="whitespace-nowrap">
            <th class="px-3 py-2">Serviço</th>
            <th class="px-3 py-2">Plano</th>
            <th class="px-3 py-2 text-right">Valor</th>
            <th class="px-3 py-2">Vencimento</th>
            <th class="px-3 py-2">Status</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="f in sortedRows"
            :key="f.id"
            class="border-t hover:bg-muted/20 cursor-pointer"
            @click="openEdit(f)"
          >
            <td class="px-3 py-2 whitespace-nowrap font-medium">{{ f.servico }}</td>
            <td class="px-3 py-2 text-muted-foreground">{{ f.plano || '—' }}</td>
            <td class="px-3 py-2 whitespace-nowrap text-right font-mono">{{ fmtValor(f.valor) }}</td>
            <td class="px-3 py-2 whitespace-nowrap">{{ fmtDate(f.data_vencimento) }}</td>
            <td class="px-3 py-2 whitespace-nowrap">
              <span class="text-xs px-2 py-0.5 rounded border" :class="statusOf(f).cls">{{ statusOf(f).label }}</span>
            </td>
          </tr>
          <tr v-if="!loading && rows.length === 0">
            <td colspan="5" class="px-3 py-6 text-center text-muted-foreground">nenhuma fatura</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Mobile cards -->
    <div class="md:hidden space-y-2">
      <div
        v-for="f in sortedRows"
        :key="f.id"
        class="border rounded-md p-3 space-y-2 hover:bg-muted/20 cursor-pointer"
        @click="openEdit(f)"
      >
        <div class="flex items-start gap-2">
          <div class="flex-1 min-w-0">
            <div class="font-medium truncate">{{ f.servico }}</div>
            <div class="text-xs text-muted-foreground truncate">{{ f.plano || '—' }}</div>
          </div>
          <span class="text-[10px] px-1.5 py-0.5 rounded border shrink-0" :class="statusOf(f).cls">{{ statusOf(f).label }}</span>
        </div>
        <div class="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
          <div><span class="text-muted-foreground">Vencimento:</span> {{ fmtDate(f.data_vencimento) }}</div>
          <div><span class="text-muted-foreground">Valor:</span> {{ fmtValor(f.valor) }}</div>
        </div>
      </div>
      <div v-if="!loading && rows.length === 0" class="text-center text-sm text-muted-foreground py-6 border rounded-md">
        nenhuma fatura
      </div>
    </div>

    <!-- Create modal -->
    <div v-if="showNew" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" @click.self="showNew = false">
      <div class="bg-background border rounded-lg w-full max-w-md p-5 space-y-4">
        <div class="flex items-center">
          <h2 class="text-lg font-semibold">Nova fatura</h2>
          <Button class="ml-auto" size="sm" variant="ghost" @click="showNew = false">
            <X class="size-4" />
          </Button>
        </div>
        <div class="space-y-3">
          <div>
            <Label>Serviço *</Label>
            <Input v-model="draft.servico" placeholder="ex: Higgsfield" />
          </div>
          <div>
            <Label>Plano</Label>
            <Input v-model="draft.plano" placeholder="ex: Plano 12 meses" />
          </div>
          <div>
            <Label>Valor</Label>
            <Input v-model="draft.valor" inputmode="decimal" placeholder="ex: 300,00" />
          </div>
          <div>
            <Label>Data de vencimento *</Label>
            <Input v-model="draft.data_vencimento" type="date" />
          </div>
        </div>
        <div v-if="createErr" class="text-sm text-red-500">erro: {{ createErr }}</div>
        <div class="flex justify-end gap-2">
          <Button variant="ghost" :disabled="creating" @click="showNew = false">cancelar</Button>
          <Button :disabled="creating" @click="createFatura">
            {{ creating ? 'criando…' : 'Criar' }}
          </Button>
        </div>
      </div>
    </div>

    <!-- Edit modal -->
    <div v-if="editing" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" @click.self="editing = null">
      <div class="bg-background border rounded-lg w-full max-w-md p-5 space-y-4">
        <div class="flex items-center">
          <h2 class="text-lg font-semibold">Editar fatura</h2>
          <Button class="ml-auto" size="sm" variant="ghost" @click="editing = null">
            <X class="size-4" />
          </Button>
        </div>
        <div class="space-y-3">
          <div>
            <Label>Serviço</Label>
            <Input v-model="editDraft.servico" />
          </div>
          <div>
            <Label>Plano</Label>
            <Input v-model="editDraft.plano" />
          </div>
          <div>
            <Label>Valor</Label>
            <Input v-model="editDraft.valor" inputmode="decimal" />
          </div>
          <div>
            <Label>Data de vencimento</Label>
            <Input v-model="editDraft.data_vencimento" type="date" />
          </div>
        </div>
        <div v-if="editErr" class="text-sm text-red-500">erro: {{ editErr }}</div>
        <div class="flex justify-end gap-2">
          <Button variant="ghost" :disabled="saving" class="text-red-500" @click="removeFatura">
            <Trash2 class="size-4 mr-1" /> remover
          </Button>
          <div class="ml-auto flex gap-2">
            <Button variant="ghost" :disabled="saving" @click="editing = null">cancelar</Button>
            <Button :disabled="saving" @click="saveEdit">
              {{ saving ? 'salvando…' : 'Salvar' }}
            </Button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
