<script setup lang="ts">
import { CalendarDays, Check, ChevronDown, ChevronRight, Loader2, Plus, Trash2, X } from 'lucide-vue-next'

// Data Especial: janela em que a margem baixa não trava pedidos do segmento
// (nem dos subsegmentos). min_margin em fração (-0.15 = -15%); null = aprova
// qualquer margem no período.
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
type Editing = { id: string; field: EditField } | null

const dimFields: DimField[] = ['altura', 'largura', 'comprimento', 'peso']

const props = defineProps<{
  node: TreeNode
  depth: number
  expanded: Set<string>
  editing: Editing
  editValue: string
  flashed: Set<string>
  canEdit: boolean
  canDelete: boolean
  addingUnder: string | null | undefined
  newName: string
  adding: boolean
  setEditInputRef: (el: any) => void
}>()

const emit = defineEmits<{
  (e: 'toggle', id: string): void
  (e: 'start-edit', node: Segment, field: EditField): void
  (e: 'commit-edit'): void
  (e: 'cancel-edit'): void
  (e: 'update:edit-value', v: string): void
  (e: 'toggle-active', node: Segment): void
  (e: 'open-add', parentId: string | null): void
  (e: 'close-add'): void
  (e: 'submit-add'): void
  (e: 'update:new-name', v: string): void
  (e: 'remove', node: Segment, depth: number): void
  (e: 'open-special', node: Segment): void
}>()

function isOpen(id: string) { return props.expanded.has(id) }
function isEditing(id: string, f: EditField) { return props.editing?.id === id && props.editing?.field === f }
function isFlashed(id: string, f: string) { return props.flashed.has(`${id}::${f}`) }
const hasChildren = computed(() => props.node.children.length > 0)
const open = computed(() => isOpen(props.node.id))

// "01/09–15/09" (ano só quando difere do atual: "28/12/25–05/01/26").
function fmtRange(sd: SpecialDate): string {
  const cur = String(new Date().getFullYear())
  const f = (iso: string) => {
    const [y, m, d] = iso.split('-')
    return y === cur ? `${d}/${m}` : `${d}/${m}/${y!.slice(2)}`
  }
  return `${f(sd.date_start)}–${f(sd.date_end)}`
}
function fmtRegra(sd: SpecialDate): string {
  if (sd.min_margin === null) return 'aprova tudo'
  const pct = (Number(sd.min_margin) * 100).toFixed(2).replace(/\.?0+$/, '')
  return `≥ ${pct}%`
}
</script>

<template>
  <tr class="hover:bg-accent/30">
    <!-- name + indent + chevron -->
    <td
      class="border border-border px-3 py-1.5 text-sm cursor-pointer"
      :class="{
        'ring-2 ring-blue-500 ring-inset bg-background': isEditing(node.id, 'name'),
        'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(node.id, 'name'),
      }"
      :style="{ paddingLeft: `${12 + depth * 20}px` }"
      @click="!isEditing(node.id, 'name') && emit('start-edit', node, 'name')"
    >
      <div class="flex items-center gap-1.5">
        <button
          v-if="hasChildren"
          class="p-0.5 hover:bg-muted rounded shrink-0"
          @click.stop="emit('toggle', node.id)"
        >
          <ChevronDown v-if="open" class="h-3.5 w-3.5" />
          <ChevronRight v-else class="h-3.5 w-3.5" />
        </button>
        <span v-else class="w-4 shrink-0" />

        <input
          v-if="isEditing(node.id, 'name')"
          :ref="setEditInputRef"
          :value="editValue"
          type="text"
          class="flex-1 text-sm bg-transparent outline-none"
          @input="(e: any) => emit('update:edit-value', e.target.value)"
          @blur="emit('commit-edit')"
          @keydown.enter.prevent="emit('commit-edit')"
          @keydown.escape.prevent="emit('cancel-edit')"
        />
        <span v-else class="flex-1" :class="{ 'opacity-50': !node.active, 'font-semibold': depth === 0 }">
          {{ node.name }}
        </span>

        <span v-if="depth === 0" class="text-[10px] uppercase tracking-wider text-muted-foreground shrink-0">raiz</span>
      </div>
    </td>

    <!-- min_margin (subtypes only) -->
    <td
      class="border border-border px-3 py-1.5 text-xs text-right cursor-pointer"
      :class="{
        'ring-2 ring-blue-500 ring-inset bg-background': isEditing(node.id, 'min_margin'),
        'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(node.id, 'min_margin'),
      }"
      @click="depth > 0 && !isEditing(node.id, 'min_margin') && emit('start-edit', node, 'min_margin')"
    >
      <input
        v-if="isEditing(node.id, 'min_margin')"
        :ref="setEditInputRef"
        :value="editValue"
        type="number"
        step="0.1"
        placeholder="%"
        class="w-full text-xs bg-transparent outline-none text-right"
        @input="(e: any) => emit('update:edit-value', e.target.value)"
        @blur="emit('commit-edit')"
        @keydown.enter.prevent="emit('commit-edit')"
        @keydown.escape.prevent="emit('cancel-edit')"
      />
      <span v-else-if="depth === 0" class="text-muted-foreground">—</span>
      <span v-else-if="node.min_margin === null" class="text-muted-foreground italic">—</span>
      <span
        v-else
        :class="Number(node.min_margin) < 0 ? 'text-red-600 font-semibold' : ''"
      >
        {{ (Number(node.min_margin) * 100).toFixed(2).replace(/\.?0+$/, '') }}%
      </span>
    </td>

    <!-- Datas Especiais: janelas de exceção da margem (segmento + subsegmentos).
         Clique abre o modal de gerenciamento na página. -->
    <td
      class="border border-border px-2 py-1.5 text-xs"
      :class="canEdit ? 'cursor-pointer hover:bg-amber-50/60 dark:hover:bg-amber-900/10' : ''"
      :title="canEdit ? 'Gerenciar datas especiais' : undefined"
      @click="canEdit && emit('open-special', node)"
    >
      <div class="flex flex-wrap items-center gap-1">
        <span
          v-for="sd in node.special_dates"
          :key="sd.id"
          class="inline-flex items-center rounded bg-amber-100 dark:bg-amber-900/40 px-1.5 py-0.5 text-[10px] font-medium text-amber-800 dark:text-amber-300 whitespace-nowrap"
        >
          {{ fmtRange(sd) }} · {{ fmtRegra(sd) }}
        </span>
        <span
          v-if="!node.special_dates.length"
          class="inline-flex items-center gap-1 text-muted-foreground"
        >
          <template v-if="canEdit"><CalendarDays class="h-3 w-3" /> adicionar</template>
          <template v-else>—</template>
        </span>
      </div>
    </td>

    <!-- dimensions: altura, largura, comprimento, peso (subtypes only) -->
    <td
      v-for="f in dimFields"
      :key="f"
      class="border border-border px-3 py-1.5 text-xs text-right cursor-pointer"
      :class="{
        'ring-2 ring-blue-500 ring-inset bg-background': isEditing(node.id, f),
        'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(node.id, f),
      }"
      @click="depth > 0 && !isEditing(node.id, f) && emit('start-edit', node, f)"
    >
      <input
        v-if="isEditing(node.id, f)"
        :ref="setEditInputRef"
        :value="editValue"
        type="number"
        step="0.001"
        min="0"
        class="w-full text-xs bg-transparent outline-none text-right"
        @input="(e: any) => emit('update:edit-value', e.target.value)"
        @blur="emit('commit-edit')"
        @keydown.enter.prevent="emit('commit-edit')"
        @keydown.escape.prevent="emit('cancel-edit')"
      />
      <span v-else-if="depth === 0" class="text-muted-foreground">—</span>
      <span v-else-if="node[f] === null" class="text-muted-foreground italic">—</span>
      <span v-else>{{ Number(node[f]).toString() }}</span>
    </td>

    <!-- active -->
    <td class="border border-border px-3 py-1.5 text-center">
      <button
        v-if="canEdit"
        class="px-2 py-0.5 rounded text-[11px] font-medium"
        :class="node.active
          ? 'bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-300'
          : 'bg-muted text-muted-foreground'"
        :disabled="!canEdit"
        @click="emit('toggle-active', node)"
      >
        {{ node.active ? 'ativo' : 'inativo' }}
      </button>
      <span v-else class="text-[11px]" :class="node.active ? 'text-emerald-600' : 'text-muted-foreground'">
        {{ node.active ? 'sim' : 'não' }}
      </span>
    </td>

    <!-- actions -->
    <td class="border border-border px-1 py-1 text-center">
      <div class="flex items-center gap-1 justify-center">
        <button
          v-if="canEdit"
          class="p-1 text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/30 rounded"
          title="Adicionar subsegmento"
          @click="emit('open-add', node.id)"
        >
          <Plus class="h-3.5 w-3.5" />
        </button>
        <button
          v-if="canDelete"
          class="p-1 text-destructive hover:bg-destructive/10 rounded"
          :title="`Excluir ${node.name}`"
          @click="emit('remove', node, depth)"
        >
          <Trash2 class="h-3.5 w-3.5" />
        </button>
      </div>
    </td>
  </tr>

  <!-- add child inline -->
  <tr v-if="addingUnder === node.id" class="bg-blue-50/40 dark:bg-blue-900/10">
    <td class="border border-border px-3 py-1.5" :style="{ paddingLeft: `${32 + depth * 20}px` }">
      <input
        id="seg-new-input"
        :value="newName"
        type="text"
        :placeholder="`Subsegmento de ${node.name}`"
        class="w-full text-sm border rounded px-2 py-1 bg-background"
        @input="(e: any) => emit('update:new-name', e.target.value)"
        @keydown.enter="emit('submit-add')"
        @keydown.escape="emit('close-add')"
      />
    </td>
    <td class="border border-border text-xs text-muted-foreground px-3 text-right">—</td>
    <td class="border border-border text-xs text-muted-foreground px-3">—</td>
    <td v-for="f in dimFields" :key="f" class="border border-border text-xs text-muted-foreground px-3 text-right">—</td>
    <td class="border border-border text-xs text-muted-foreground px-3 text-center">—</td>
    <td class="border border-border px-1 py-1 text-center">
      <div class="flex gap-0.5 justify-center">
        <button class="p-1 text-emerald-600 hover:bg-emerald-50 dark:hover:bg-emerald-900/30 rounded" :disabled="adding" @click="emit('submit-add')">
          <Loader2 v-if="adding" class="h-3.5 w-3.5 animate-spin" />
          <Check v-else class="h-3.5 w-3.5" />
        </button>
        <button class="p-1 text-destructive hover:bg-destructive/10 rounded" @click="emit('close-add')">
          <X class="h-3.5 w-3.5" />
        </button>
      </div>
    </td>
  </tr>

  <!-- recursive children -->
  <template v-if="open">
    <SegmentRow
      v-for="child in node.children"
      :key="child.id"
      :node="child"
      :depth="depth + 1"
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
      @toggle="(id: string) => emit('toggle', id)"
      @start-edit="(n: any, f: any) => emit('start-edit', n, f)"
      @commit-edit="emit('commit-edit')"
      @cancel-edit="emit('cancel-edit')"
      @update:edit-value="(v: string) => emit('update:edit-value', v)"
      @toggle-active="(n: any) => emit('toggle-active', n)"
      @open-add="(pid: any) => emit('open-add', pid)"
      @close-add="emit('close-add')"
      @submit-add="emit('submit-add')"
      @update:new-name="(v: string) => emit('update:new-name', v)"
      @remove="(n: any, d: number) => emit('remove', n, d)"
      @open-special="(n: any) => emit('open-special', n)"
    />
  </template>
</template>
