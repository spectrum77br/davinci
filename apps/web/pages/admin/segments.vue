<script setup lang="ts">
import { TABS_CADASTROS } from '~/lib/navGroups'
import { AlertCircle, Check, ChevronDown, ChevronRight, Loader2, Plus, RefreshCw, Trash2, X } from 'lucide-vue-next'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'segmentos', action: 'view' },
})

// Data Especial: janela (datas BRT, inclusivas) em que pedidos do segmento —
// e de TODOS os subsegmentos — não são travados por margem baixa na aba
// Margem (nem pelo robô de auto-hold). min_margin em fração (-0.15 = -15%);
// null = aprova qualquer margem no período.
type SpecialDate = {
  id: string
  segment_id: string
  date_start: string
  date_end: string
  min_margin: string | null
}

type Segment = {
  id: string
  user_id: string | null
  parent_id: string | null
  name: string
  slug: string
  sort_order: number
  active: boolean
  min_margin: string | null
  altura: string | null
  largura: string | null
  comprimento: string | null
  peso: string | null
  special_dates: SpecialDate[]
  created_at: string
  updated_at: string
}

type TreeNode = Segment & { children: TreeNode[] }
type DimField = 'altura' | 'largura' | 'comprimento' | 'peso'
type EditField = 'name' | 'min_margin' | DimField
const dimFields: DimField[] = ['altura', 'largura', 'comprimento', 'peso']

const { api } = useApi()
const canEdit = useCan('segmentos', 'edit')
const canDelete = useCan('segmentos', 'delete')

const tree = ref<TreeNode[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const expanded = ref<Set<string>>(new Set())

async function load() {
  loading.value = true
  error.value = null
  try {
    tree.value = await api<TreeNode[]>('/api/segments/tree')
    // expand roots by default on first load
    if (expanded.value.size === 0) {
      for (const n of tree.value) expanded.value.add(n.id)
    }
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    loading.value = false
  }
}

await load()

function toggle(id: string) {
  if (expanded.value.has(id)) expanded.value.delete(id)
  else expanded.value.add(id)
}

// =========================================================== inline edit

const editing = ref<{ id: string; field: EditField } | null>(null)
const editValue = ref<string>('')
const editOriginal = ref<string>('')
const editInputRef = ref<HTMLInputElement | null>(null)
function setEditInputRef(el: any) { if (el) editInputRef.value = el }
const flashed = ref<Set<string>>(new Set())

function isEditing(id: string, f: string) { return editing.value?.id === id && editing.value?.field === f }
function isFlashed(id: string, f: string) { return flashed.value.has(`${id}::${f}`) }
function flash(id: string, f: string) {
  const k = `${id}::${f}`
  flashed.value.add(k)
  setTimeout(() => flashed.value.delete(k), 1200)
}

async function startEdit(seg: Segment, field: EditField) {
  if (!canEdit.value) return
  editing.value = { id: seg.id, field }
  const raw = (seg as any)[field]
  // min_margin is stored as a fraction (0.15 = 15%); show as percent for editing.
  let initial: string
  if (field === 'min_margin') {
    initial = raw == null ? '' : (Number(raw) * 100).toString()
  } else {
    initial = raw == null ? '' : String(raw)
  }
  editValue.value = initial
  editOriginal.value = initial
  await nextTick()
  editInputRef.value?.focus()
  editInputRef.value?.select?.()
}

function cancelEdit() {
  editing.value = null
  editValue.value = ''
  editOriginal.value = ''
}

async function commitEdit() {
  if (!editing.value) return
  const { id, field } = editing.value
  if (editValue.value === editOriginal.value) return cancelEdit()
  const raw = editValue.value.trim()
  const payload: Record<string, unknown> = {}
  if (field === 'min_margin') {
    if (!raw) {
      payload.min_margin = null
    } else {
      const n = Number(raw)
      if (!Number.isFinite(n)) return cancelEdit()
      // UI accepts percent (15 → 0.15), DB stores fraction.
      payload.min_margin = (n / 100).toFixed(4)
    }
  } else if ((dimFields as string[]).includes(field)) {
    // altura/largura/comprimento (cm) e peso (kg): número plano, nullable.
    if (!raw) {
      payload[field] = null
    } else {
      const n = Number(raw)
      if (!Number.isFinite(n) || n < 0) return cancelEdit()
      payload[field] = n.toString()
    }
  } else {
    if (!raw && field === 'name') return cancelEdit()
    payload[field] = raw || null
  }

  try {
    await api(`/api/segments/${id}`, { method: 'PATCH', body: payload })
    flash(id, field)
    await load()
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    cancelEdit()
  }
}

// =========================================================== toggle active

async function toggleActive(seg: Segment) {
  try {
    await api(`/api/segments/${seg.id}`, {
      method: 'PATCH',
      body: { active: !seg.active },
    })
    flash(seg.id, 'active')
    await load()
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  }
}

// =========================================================== add child / root

const addingUnder = ref<string | null | undefined>(undefined) // undefined = closed, null = adding root
const newName = ref('')
const adding = ref(false)

function openAdd(parentId: string | null) {
  addingUnder.value = parentId
  newName.value = ''
  if (parentId) expanded.value.add(parentId)
  nextTick(() => {
    const el = document.getElementById('seg-new-input') as HTMLInputElement | null
    el?.focus()
  })
}

function closeAdd() {
  addingUnder.value = undefined
  newName.value = ''
}

async function submitAdd() {
  if (!newName.value.trim()) return
  adding.value = true
  try {
    await api('/api/segments', {
      method: 'POST',
      body: { name: newName.value.trim(), parent_id: addingUnder.value },
    })
    closeAdd()
    await load()
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    adding.value = false
  }
}

// =========================================================== delete

async function remove(seg: Segment, depth: number) {
  const msg = depth === 0
    ? `Excluir segmento raiz "${seg.name}"? Todos os filhos serão removidos.`
    : `Excluir "${seg.name}"?`
  if (!confirm(msg)) return
  try {
    await api(`/api/segments/${seg.id}`, { method: 'DELETE' })
    await load()
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  }
}

// =========================================================== datas especiais
// Modal por segmento: lista as janelas + formulário rotulado De/Até/margem.

const specialFor = ref<Segment | null>(null)
const sdStart = ref('')
const sdEnd = ref('')
// percent na UI ("-15" = -15%); vazio = aprova tudo. O input é type="number",
// então o v-model do Vue entrega NUMBER quando preenchido (cast automático) e
// '' quando vazio — daí o tipo união e o String() defensivo no addSpecial.
const sdMargin = ref<string | number>('')
const sdSaving = ref(false)
const sdError = ref<string | null>(null)

function findNode(nodes: TreeNode[], id: string): TreeNode | null {
  for (const n of nodes) {
    if (n.id === id) return n
    const hit = findNode(n.children, id)
    if (hit) return hit
  }
  return null
}

function openSpecial(seg: Segment) {
  specialFor.value = findNode(tree.value, seg.id) ?? seg
  sdStart.value = ''
  sdEnd.value = ''
  sdMargin.value = ''
  sdError.value = null
}

function closeSpecial() {
  specialFor.value = null
  sdError.value = null
}

function fmtBR(iso: string): string {
  const [y, m, d] = iso.split('-')
  return `${d}/${m}/${y}`
}

function sdRegra(sd: SpecialDate): string {
  if (sd.min_margin === null) return 'aprova qualquer margem'
  const pct = (Number(sd.min_margin) * 100).toFixed(2).replace(/\.?0+$/, '')
  return `aprova até margem ${pct}%`
}

// Erro da API → frase pt-BR (422 do FastAPI vem como LISTA em detail).
function sdErrMsg(e: any): string {
  const d = e?.data?.detail
  if (d?.code === 'segment_not_found') return 'Segmento não encontrado — recarregue a página.'
  if (d?.code === 'special_date_not_found') return 'Este período já foi removido — recarregue a página.'
  if (Array.isArray(d)) return 'Dados inválidos — confira as datas e a margem.'
  return d?.code || e?.message || 'Erro ao salvar — tente de novo.'
}

async function addSpecial() {
  if (!specialFor.value) return
  sdError.value = null
  if (!sdStart.value || !sdEnd.value) {
    sdError.value = 'Preencha as duas datas (De e Até).'
    return
  }
  if (sdEnd.value < sdStart.value) {
    sdError.value = 'A data final não pode ser antes da inicial.'
    return
  }
  const body: Record<string, unknown> = {
    date_start: sdStart.value,
    date_end: sdEnd.value,
  }
  // BUG corrigido (01/09, Eduardo: "não está deixando adicionar"): input
  // type="number" faz o v-model entregar NUMBER, e number.trim() explodia
  // ANTES do POST — clique morria sem mensagem. String() cobre os dois casos.
  const raw = String(sdMargin.value ?? '').trim()
  if (raw) {
    const n = Number(raw.replace(',', '.'))
    if (!Number.isFinite(n)) {
      sdError.value = 'Margem inválida — use um número, ex.: -15.'
      return
    }
    // UI em percent (-15 → -0.15), banco guarda fração (igual à Margem Mín).
    body.min_margin = (n / 100).toFixed(4)
  }
  sdSaving.value = true
  try {
    await api(`/api/segments/${specialFor.value.id}/special-dates`, {
      method: 'POST',
      body,
    })
    const id = specialFor.value.id
    await load()
    specialFor.value = findNode(tree.value, id)
    sdStart.value = ''
    sdEnd.value = ''
    sdMargin.value = ''
  } catch (e: any) {
    sdError.value = sdErrMsg(e)
  } finally {
    sdSaving.value = false
  }
}

async function removeSpecial(sd: SpecialDate) {
  if (!specialFor.value) return
  sdError.value = null
  try {
    await api(`/api/segments/${specialFor.value.id}/special-dates/${sd.id}`, {
      method: 'DELETE',
    })
    const id = specialFor.value.id
    await load()
    specialFor.value = findNode(tree.value, id)
  } catch (e: any) {
    sdError.value = sdErrMsg(e)
  }
}
</script>

<template>
  <div class="space-y-4">
    <RouteTabs :tabs="TABS_CADASTROS" />
    <PageHeader title="Segmentos">
      <template #actions>
        <Button size="sm" variant="ghost" :disabled="loading" @click="load">
          <RefreshCw class="size-4 mr-1" :class="{ 'animate-spin': loading }" /> recarregar
        </Button>
        <Button v-if="canEdit" size="sm" @click="openAdd(null)">
          <Plus class="size-4 mr-1" /> Novo segmento raiz
        </Button>
      </template>
    </PageHeader>

    <div v-if="error" class="rounded border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive flex items-center gap-2">
      <AlertCircle class="h-4 w-4" /> {{ error }}
    </div>

    <div class="border rounded-lg overflow-auto max-h-[calc(100vh-220px)]">
      <table class="w-full text-sm border-collapse">
        <thead class="sticky top-0 bg-muted z-10">
          <tr>
            <th class="text-left px-3 py-2 font-medium border-b border-border min-w-[280px]">Nome</th>
            <th class="text-right px-3 py-2 font-medium border-b border-border w-28">Margem Mín</th>
            <th class="text-left px-3 py-2 font-medium border-b border-border w-28">Datas Especiais</th>
            <th class="text-right px-3 py-2 font-medium border-b border-border w-24">Altura <span class="text-muted-foreground font-normal">(cm)</span></th>
            <th class="text-right px-3 py-2 font-medium border-b border-border w-24">Largura <span class="text-muted-foreground font-normal">(cm)</span></th>
            <th class="text-right px-3 py-2 font-medium border-b border-border w-28">Comprim. <span class="text-muted-foreground font-normal">(cm)</span></th>
            <th class="text-right px-3 py-2 font-medium border-b border-border w-24">Peso <span class="text-muted-foreground font-normal">(kg)</span></th>
            <th class="text-center px-3 py-2 font-medium border-b border-border w-20">Ativo</th>
            <th class="text-center px-3 py-2 font-medium border-b border-border w-32">Ações</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading && !tree.length">
            <td colspan="9" class="text-center py-6 text-muted-foreground">
              <Loader2 class="inline h-4 w-4 animate-spin" /> carregando…
            </td>
          </tr>
          <tr v-else-if="!tree.length && addingUnder === undefined">
            <td colspan="9" class="text-center py-8 text-muted-foreground">Nenhum segmento.</td>
          </tr>

          <template v-for="node in tree" :key="node.id">
            <SegmentRow
              :node="node"
              :depth="0"
              :expanded="expanded"
              :editing="editing"
              :edit-value="editValue"
              :flashed="flashed"
              :can-edit="canEdit"
              :can-delete="canDelete"
              :adding-under="addingUnder"
              :new-name="newName"
              :adding="adding"
              :set-edit-input-ref="setEditInputRef"
              @toggle="toggle"
              @start-edit="startEdit"
              @commit-edit="commitEdit"
              @cancel-edit="cancelEdit"
              @update:edit-value="(v: string) => (editValue = v)"
              @toggle-active="toggleActive"
              @open-add="openAdd"
              @close-add="closeAdd"
              @submit-add="submitAdd"
              @update:new-name="(v: string) => (newName = v)"
              @remove="remove"
              @open-special="openSpecial"
            />
          </template>

          <!-- add new root -->
          <tr v-if="addingUnder === null" class="bg-blue-50/40 dark:bg-blue-900/10">
            <td class="border border-border px-3 py-1.5">
              <input
                id="seg-new-input"
                v-model="newName"
                type="text"
                placeholder="Nome do segmento raiz"
                class="w-full text-sm border rounded px-2 py-1 bg-background"
                @keydown.enter="submitAdd"
                @keydown.escape="closeAdd"
              />
            </td>
            <td class="border border-border text-xs text-muted-foreground px-3 text-right">—</td>
            <td class="border border-border text-xs text-muted-foreground px-3">—</td>
            <td v-for="f in dimFields" :key="f" class="border border-border text-xs text-muted-foreground px-3 text-right">—</td>
            <td class="border border-border text-xs text-muted-foreground px-3 text-center">—</td>
            <td class="border border-border px-1 py-1 text-center">
              <div class="flex gap-0.5 justify-center">
                <button class="p-1 text-emerald-600 hover:bg-emerald-50 dark:hover:bg-emerald-900/30 rounded" :disabled="adding" @click="submitAdd">
                  <Loader2 v-if="adding" class="h-3.5 w-3.5 animate-spin" />
                  <Check v-else class="h-3.5 w-3.5" />
                </button>
                <button class="p-1 text-destructive hover:bg-destructive/10 rounded" @click="closeAdd">
                  <X class="h-3.5 w-3.5" />
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Modal Datas Especiais: janelas de exceção da margem do segmento -->
    <div
      v-if="specialFor"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      @click.self="closeSpecial"
    >
      <div class="w-full max-w-md rounded-lg border border-border bg-background p-4 shadow-xl space-y-3">
        <div class="flex items-center justify-between">
          <h3 class="font-semibold text-sm">Datas Especiais — {{ specialFor.name }}</h3>
          <button class="p-1 hover:bg-muted rounded" title="Fechar" @click="closeSpecial">
            <X class="h-4 w-4" />
          </button>
        </div>

        <p class="text-xs text-muted-foreground leading-relaxed">
          Pedidos <strong>feitos</strong> dentro do período (vale a <strong>data do
          pedido</strong>, não o dia de hoje) deste segmento <strong>e de todos os
          subsegmentos</strong> não ficam travados por margem baixa na aba
          Margem (o robô também não segura). Sem margem preenchida, aprova
          qualquer margem — até negativa. Para liberar pedidos antigos que já
          estão pendentes, comece o período na data do pedido mais antigo.
        </p>

        <div v-if="sdError" class="rounded border border-destructive/50 bg-destructive/10 px-3 py-2 text-xs text-destructive flex items-center gap-2">
          <AlertCircle class="h-3.5 w-3.5 shrink-0" /> {{ sdError }}
        </div>

        <div v-if="specialFor.special_dates.length" class="space-y-1.5">
          <div
            v-for="sd in specialFor.special_dates"
            :key="sd.id"
            class="flex items-center justify-between gap-2 rounded border border-amber-200 dark:border-amber-800 bg-amber-50/60 dark:bg-amber-900/15 px-2.5 py-1.5 text-sm"
          >
            <span>
              <span class="font-medium tabular-nums">{{ fmtBR(sd.date_start) }} até {{ fmtBR(sd.date_end) }}</span>
              <span class="text-muted-foreground"> · {{ sdRegra(sd) }}</span>
            </span>
            <button
              v-if="canEdit"
              class="p-1 text-destructive hover:bg-destructive/10 rounded shrink-0"
              title="Remover este período"
              @click="removeSpecial(sd)"
            >
              <Trash2 class="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
        <p v-else class="text-sm text-muted-foreground">Nenhuma data especial cadastrada.</p>

        <div v-if="canEdit" class="rounded border border-border p-3 space-y-2.5">
          <div class="text-xs font-medium">Adicionar período</div>
          <div class="grid grid-cols-2 gap-2">
            <label class="block text-xs text-muted-foreground">
              De (data do pedido)
              <input
                v-model="sdStart"
                type="date"
                class="mt-1 w-full text-sm border rounded px-2 py-1 bg-background text-foreground"
              />
            </label>
            <label class="block text-xs text-muted-foreground">
              Até (data do pedido)
              <input
                v-model="sdEnd"
                type="date"
                class="mt-1 w-full text-sm border rounded px-2 py-1 bg-background text-foreground"
              />
            </label>
          </div>
          <label class="block text-xs text-muted-foreground">
            Margem mínima especial (%) — opcional
            <input
              v-model="sdMargin"
              type="number"
              step="0.1"
              placeholder="ex.: -15"
              class="mt-1 w-full text-sm border rounded px-2 py-1 bg-background text-foreground"
            />
          </label>
          <p class="text-[11px] text-muted-foreground">
            Vazio = aprova qualquer margem no período. Com valor (ex.: -15),
            aprova enquanto a margem for maior ou igual a -15%.
          </p>
          <Button size="sm" class="w-full" :disabled="sdSaving" @click="addSpecial">
            <Loader2 v-if="sdSaving" class="size-4 mr-1 animate-spin" />
            <Plus v-else class="size-4 mr-1" />
            Adicionar período
          </Button>
        </div>
      </div>
    </div>
  </div>
</template>
