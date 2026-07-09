<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Plus, RefreshCw, X, Trash2, SquarePen } from 'lucide-vue-next'

const { api } = useApi()
const canEdit = useCan('integracoes', 'edit')

type Automacao = {
  id: string
  nome: string
  descricao: string | null
  frequencia: string | null
  categoria: string | null
  ativa: boolean
  created_by: string | null
  created_at: string
  updated_at: string
}

const rows = ref<Automacao[]>([])
const loading = ref(false)
const error = ref<string | null>(null)

async function refresh() {
  loading.value = true
  error.value = null
  try {
    rows.value = await api<Automacao[]>('/api/automacoes')
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    loading.value = false
  }
}
onMounted(refresh)

// Agrupa por categoria (sem categoria = "Outras"), preservando a ordem
// alfabética que o backend já entrega.
const grouped = computed(() => {
  const map: Record<string, Automacao[]> = {}
  for (const a of rows.value) {
    const key = (a.categoria || '').trim() || 'Outras'
    map[key] ??= []
    map[key].push(a)
  }
  return Object.keys(map)
    .sort((a, b) => a.localeCompare(b, 'pt-BR'))
    .map(k => ({ categoria: k, items: map[k] }))
})

type Draft = { nome: string; descricao: string; frequencia: string; categoria: string; ativa: boolean }
function emptyDraft(): Draft {
  return { nome: '', descricao: '', frequencia: '', categoria: '', ativa: true }
}

// ---- Create modal ----
const showNew = ref(false)
const draft = ref<Draft>(emptyDraft())
const creating = ref(false)
const createErr = ref<string | null>(null)

function openNew() {
  draft.value = emptyDraft()
  createErr.value = null
  showNew.value = true
}

async function createAutomacao() {
  if (!draft.value.nome.trim()) {
    createErr.value = 'preencha o nome'
    return
  }
  creating.value = true
  createErr.value = null
  try {
    await api('/api/automacoes', {
      method: 'POST',
      body: {
        nome: draft.value.nome.trim(),
        descricao: draft.value.descricao.trim() || null,
        frequencia: draft.value.frequencia.trim() || null,
        categoria: draft.value.categoria.trim() || null,
        ativa: draft.value.ativa,
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
const editing = ref<Automacao | null>(null)
const editDraft = ref<Draft>(emptyDraft())
const saving = ref(false)
const editErr = ref<string | null>(null)

function openEdit(a: Automacao) {
  editing.value = a
  editDraft.value = {
    nome: a.nome,
    descricao: a.descricao || '',
    frequencia: a.frequencia || '',
    categoria: a.categoria || '',
    ativa: a.ativa,
  }
  editErr.value = null
}

async function saveEdit() {
  if (!editing.value) return
  saving.value = true
  editErr.value = null
  try {
    await api(`/api/automacoes/${editing.value.id}`, {
      method: 'PATCH',
      body: {
        nome: editDraft.value.nome.trim(),
        descricao: editDraft.value.descricao.trim() || null,
        frequencia: editDraft.value.frequencia.trim() || null,
        categoria: editDraft.value.categoria.trim() || null,
        ativa: editDraft.value.ativa,
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

async function removeAutomacao() {
  if (!editing.value) return
  if (!confirm('Remover esta automação do catálogo? (não afeta o sistema — só o registro)')) return
  saving.value = true
  editErr.value = null
  try {
    await api(`/api/automacoes/${editing.value.id}`, { method: 'DELETE' })
    editing.value = null
    await refresh()
  } catch (e: any) {
    editErr.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="space-y-4">
    <div class="flex flex-wrap items-center gap-3">
      <Button size="sm" variant="ghost" :disabled="loading" @click="refresh">
        <RefreshCw class="size-4 mr-1" /> recarregar
      </Button>
      <Button v-if="canEdit" size="sm" class="ml-auto" @click="openNew">
        <Plus class="size-4 mr-1" /> Nova automação
      </Button>
    </div>

    <p class="text-sm text-muted-foreground">
      Catálogo das automações que rodam no sistema. É só um registro pra você
      ter visibilidade do que está funcionando — editar aqui não liga nem
      desliga nada de verdade.
    </p>

    <div v-if="error" class="text-sm text-red-500">erro: {{ error }}</div>

    <div v-for="group in grouped" :key="group.categoria" class="space-y-2">
      <h3 class="font-bold text-xs uppercase tracking-wide text-foreground/80 border-b border-border pb-1">
        {{ group.categoria }}
        <span class="font-normal text-muted-foreground normal-case">
          {{ group.items.length }} automaç{{ group.items.length > 1 ? 'ões' : 'ão' }}
        </span>
      </h3>
      <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
        <div
          v-for="a in group.items"
          :key="a.id"
          class="border rounded-md p-3 space-y-2"
          :class="canEdit ? 'cursor-pointer hover:bg-muted/20' : ''"
          @click="canEdit && openEdit(a)"
        >
          <div class="flex items-center gap-2">
            <span class="font-medium truncate">{{ a.nome }}</span>
            <span
              class="text-xs px-2 py-0.5 rounded border ml-auto shrink-0"
              :class="a.ativa ? 'border-green-500 text-green-400' : 'border-red-500 text-red-400'"
            >
              {{ a.ativa ? 'funcionando' : 'parada' }}
            </span>
          </div>
          <div v-if="a.descricao" class="text-xs text-muted-foreground">{{ a.descricao }}</div>
          <div v-if="a.frequencia" class="text-xs">
            <span class="text-muted-foreground">frequência:</span> {{ a.frequencia }}
          </div>
          <div v-if="canEdit" class="flex justify-end pt-1">
            <Button size="sm" variant="ghost" title="editar" @click.stop="openEdit(a)">
              <SquarePen class="size-4" />
            </Button>
          </div>
        </div>
      </div>
    </div>

    <div v-if="!loading && rows.length === 0" class="text-muted-foreground text-sm">
      nenhuma automação cadastrada ainda{{ canEdit ? ' — clique em “Nova automação”.' : '.' }}
    </div>

    <!-- Create modal -->
    <div v-if="showNew" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" @click.self="showNew = false">
      <div class="bg-background border rounded-lg w-full max-w-md p-5 space-y-4">
        <div class="flex items-center">
          <h2 class="text-lg font-semibold">Nova automação</h2>
          <Button class="ml-auto" size="sm" variant="ghost" @click="showNew = false">
            <X class="size-4" />
          </Button>
        </div>
        <div class="space-y-3">
          <div>
            <Label>Nome *</Label>
            <Input v-model="draft.nome" placeholder="ex: Sincronização de pedidos Bling" />
          </div>
          <div>
            <Label>Descrição</Label>
            <Input v-model="draft.descricao" placeholder="o que ela faz" />
          </div>
          <div>
            <Label>Frequência</Label>
            <Input v-model="draft.frequencia" placeholder="ex: a cada 5 min / diária 08:00" />
          </div>
          <div>
            <Label>Categoria</Label>
            <Input v-model="draft.categoria" placeholder="ex: Sync, Tokens, Financeiro" />
          </div>
          <label class="flex items-center gap-2 text-sm cursor-pointer">
            <input v-model="draft.ativa" type="checkbox" class="size-4" />
            Está funcionando
          </label>
        </div>
        <div v-if="createErr" class="text-sm text-red-500">erro: {{ createErr }}</div>
        <div class="flex justify-end gap-2">
          <Button variant="ghost" :disabled="creating" @click="showNew = false">cancelar</Button>
          <Button :disabled="creating" @click="createAutomacao">
            {{ creating ? 'criando…' : 'Criar' }}
          </Button>
        </div>
      </div>
    </div>

    <!-- Edit modal -->
    <div v-if="editing" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" @click.self="editing = null">
      <div class="bg-background border rounded-lg w-full max-w-md p-5 space-y-4">
        <div class="flex items-center">
          <h2 class="text-lg font-semibold">Editar automação</h2>
          <Button class="ml-auto" size="sm" variant="ghost" @click="editing = null">
            <X class="size-4" />
          </Button>
        </div>
        <div class="space-y-3">
          <div>
            <Label>Nome</Label>
            <Input v-model="editDraft.nome" />
          </div>
          <div>
            <Label>Descrição</Label>
            <Input v-model="editDraft.descricao" />
          </div>
          <div>
            <Label>Frequência</Label>
            <Input v-model="editDraft.frequencia" />
          </div>
          <div>
            <Label>Categoria</Label>
            <Input v-model="editDraft.categoria" />
          </div>
          <label class="flex items-center gap-2 text-sm cursor-pointer">
            <input v-model="editDraft.ativa" type="checkbox" class="size-4" />
            Está funcionando
          </label>
        </div>
        <div v-if="editErr" class="text-sm text-red-500">erro: {{ editErr }}</div>
        <div class="flex justify-end gap-2">
          <Button variant="ghost" :disabled="saving" class="text-red-500" @click="removeAutomacao">
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
