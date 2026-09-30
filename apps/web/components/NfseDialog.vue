<script setup lang="ts">
// Casca de todo diálogo central da Emissão de Serviço (confirmação, senha,
// emissão em lote, cancelamento). reka Dialog: foco preso, Esc e clique fora
// fecham (exceto com `fechavel=false`) e o foco volta para quem abriu.
import { computed, type Component } from 'vue'
import {
  DialogContent, DialogDescription, DialogOverlay, DialogPortal, DialogRoot, DialogTitle,
} from 'reka-ui'
import { X } from 'lucide-vue-next'
import { Button } from '~/components/ui/button'

const props = withDefaults(
  defineProps<{
    open: boolean
    titulo: string
    descricao?: string
    tamanho?: 'sm' | 'md' | 'lg'
    tom?: 'padrao' | 'perigo'
    icone?: Component
    fechavel?: boolean
    camada?: 'normal' | 'topo'
    // Seletor do elemento que recebe o foco ao abrir (padrão: o 1º focável).
    focoInicial?: string
  }>(),
  { tamanho: 'md', tom: 'padrao', fechavel: true, camada: 'normal' },
)

const emit = defineEmits<{ (e: 'update:open', v: boolean): void }>()

const largura = computed(() => ({ sm: 'max-w-md', md: 'max-w-lg', lg: 'max-w-2xl' })[props.tamanho])
const z = computed(() => (props.camada === 'topo' ? 'z-[70]' : 'z-50'))

function mudar(v: boolean) {
  if (!v && !props.fechavel) return
  emit('update:open', v)
}

function segurar(ev: Event) {
  if (!props.fechavel) ev.preventDefault()
}

function aoAbrir(ev: Event) {
  if (!props.focoInicial) return
  const el = (ev.target as HTMLElement | null)?.querySelector<HTMLElement>(props.focoInicial)
  if (!el) return
  ev.preventDefault()
  el.focus()
}
</script>

<template>
  <DialogRoot :open="open" @update:open="mudar">
    <DialogPortal>
      <DialogOverlay
        class="fixed inset-0 bg-black/50 duration-200 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=open]:fade-in-0 data-[state=closed]:fade-out-0 motion-reduce:animate-none"
        :class="z"
      />
      <DialogContent
        class="fixed left-1/2 top-1/2 flex max-h-[90vh] w-[calc(100%-2rem)] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-lg border bg-background shadow-xl duration-200 focus:outline-none data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=open]:fade-in-0 data-[state=closed]:fade-out-0 data-[state=open]:zoom-in-95 data-[state=closed]:zoom-out-95 data-[state=open]:slide-in-from-left-1/2 data-[state=closed]:slide-out-to-left-1/2 data-[state=open]:slide-in-from-top-[48%] data-[state=closed]:slide-out-to-top-[48%] motion-reduce:animate-none"
        :class="[largura, z]"
        @escape-key-down="segurar"
        @pointer-down-outside="segurar"
        @interact-outside="segurar"
        @open-auto-focus="aoAbrir"
      >
        <div class="flex items-start gap-3 border-b px-5 py-4">
          <div
            v-if="icone"
            class="grid size-9 shrink-0 place-items-center rounded-lg"
            :class="tom === 'perigo' ? 'bg-red-500/10 text-red-600 dark:text-red-400' : 'bg-primary/10 text-primary'"
          >
            <component :is="icone" class="size-[18px]" aria-hidden="true" />
          </div>
          <div class="min-w-0 flex-1">
            <DialogTitle class="text-base font-semibold leading-tight">{{ titulo }}</DialogTitle>
            <DialogDescription
              v-if="descricao || $slots.descricao"
              class="mt-0.5 text-sm text-muted-foreground"
              as="div"
            >
              <slot name="descricao">{{ descricao }}</slot>
            </DialogDescription>
            <DialogDescription v-else class="sr-only">{{ titulo }}</DialogDescription>
          </div>
          <slot name="cabecalho-extra" />
          <Button
            v-if="fechavel"
            variant="ghost"
            size="icon"
            class="-mr-2 -mt-1 ml-auto size-8 shrink-0"
            aria-label="fechar"
            @click="mudar(false)"
          >
            <X class="size-4" />
          </Button>
        </div>

        <slot name="faixa" />

        <div class="flex-1 space-y-4 overflow-y-auto px-5 py-4">
          <slot />
        </div>

        <div
          v-if="$slots.rodape"
          class="flex flex-wrap items-center justify-end gap-2 border-t bg-muted/30 px-5 py-3"
        >
          <slot name="rodape" />
        </div>
      </DialogContent>
    </DialogPortal>
  </DialogRoot>
</template>
