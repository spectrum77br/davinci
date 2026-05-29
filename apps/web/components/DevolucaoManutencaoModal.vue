<script setup lang="ts">
import { X } from 'lucide-vue-next'

const props = defineProps<{
  open: boolean
  sku?: string | null
}>()

const emit = defineEmits<{
  (e: 'confirm', payload: { tipo: 'Novo' | 'Usado' | 'Sucata' }): void
  (e: 'cancel'): void
}>()

const selected = ref<'Novo' | 'Usado' | 'Sucata' | ''>('')

watch(() => props.open, (open) => {
  if (open) selected.value = ''
})

const OPTIONS: { value: 'Novo' | 'Usado' | 'Sucata'; hint: string }[] = [
  { value: 'Novo', hint: 'volta ao estoque · pedido resolvido' },
  { value: 'Usado', hint: 'volta ao estoque (salvado) · pedido resolvido' },
  { value: 'Sucata', hint: 'não volta ao estoque · pedido resolvido' },
]

function confirm() {
  if (!selected.value) return
  emit('confirm', { tipo: selected.value })
}
</script>

<template>
  <div v-if="open" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" @click.self="emit('cancel')">
    <div class="bg-background border rounded-lg w-full max-w-md p-5 space-y-4">
      <div class="flex items-start">
        <div>
          <h2 class="text-lg font-semibold">Manutenção — destino</h2>
          <p class="text-sm text-muted-foreground">
            <template v-if="sku">SKU <span class="font-mono">{{ sku }}</span>. </template>
            Como o item voltou da manutenção?
          </p>
        </div>
        <Button class="ml-auto" size="sm" variant="ghost" @click="emit('cancel')">
          <X class="size-4" />
        </Button>
      </div>

      <div class="space-y-2">
        <button
          v-for="o in OPTIONS"
          :key="o.value"
          type="button"
          class="flex w-full flex-col items-start rounded-md border px-3 py-2 text-left transition-colors"
          :class="selected === o.value ? 'border-primary bg-primary/10' : 'hover:border-primary/50'"
          @click="selected = o.value"
        >
          <span class="text-sm font-medium">{{ o.value }}</span>
          <span class="text-[11px] text-muted-foreground">{{ o.hint }}</span>
        </button>
      </div>

      <div class="flex justify-end gap-2 pt-1">
        <Button size="sm" variant="ghost" @click="emit('cancel')">cancelar</Button>
        <Button size="sm" :disabled="!selected" @click="confirm">confirmar</Button>
      </div>
    </div>
  </div>
</template>
