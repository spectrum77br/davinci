<script setup lang="ts">
// App Uranyx: gaveta à direita das telas do módulo (produto, receita, app,
// categoria). Mesmo desenho da gaveta da Denúncia e da Emissão de Serviço;
// o rodapé (Salvar, Apagar) fica preso embaixo.
import {
  DialogContent, DialogDescription, DialogOverlay, DialogPortal, DialogRoot, DialogTitle,
} from 'reka-ui'
import { X } from 'lucide-vue-next'
import { Button } from '~/components/ui/button'

withDefaults(defineProps<{ open: boolean; titulo: string; subtitulo?: string; largura?: 'md' | 'lg' }>(), {
  largura: 'lg',
})
const emit = defineEmits<{ (e: 'update:open', v: boolean): void }>()
</script>

<template>
  <DialogRoot :open="open" @update:open="(v) => emit('update:open', v)">
    <DialogPortal>
      <DialogOverlay
        class="fixed inset-0 z-50 bg-black/40 duration-200 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=open]:fade-in-0 data-[state=closed]:fade-out-0 motion-reduce:animate-none"
      />
      <DialogContent
        class="fixed inset-y-0 right-0 z-50 flex h-full w-full flex-col border-l bg-background shadow-xl duration-200 focus:outline-none data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=open]:fade-in-0 data-[state=closed]:fade-out-0 data-[state=open]:slide-in-from-right data-[state=closed]:slide-out-to-right motion-reduce:animate-none"
        :class="largura === 'md' ? 'sm:max-w-[480px]' : 'sm:max-w-[640px]'"
      >
        <div class="flex items-start gap-3 border-b px-5 py-4">
          <div class="min-w-0 flex-1">
            <div class="flex flex-wrap items-center gap-2">
              <DialogTitle class="text-base font-semibold leading-tight line-clamp-2" :title="titulo">{{ titulo }}</DialogTitle>
              <slot name="cabecalho-extra" />
            </div>
            <DialogDescription v-if="subtitulo" class="mt-0.5 text-xs text-muted-foreground">
              {{ subtitulo }}
            </DialogDescription>
            <DialogDescription v-else class="sr-only">{{ titulo }}</DialogDescription>
          </div>
          <Button variant="ghost" size="icon" class="-mr-2 -mt-1 size-8 shrink-0" aria-label="fechar" @click="emit('update:open', false)">
            <X class="size-4" />
          </Button>
        </div>
        <div class="flex-1 overflow-y-auto px-5 py-5 space-y-6">
          <slot />
        </div>
        <div v-if="$slots.rodape" class="flex flex-wrap items-center gap-2 border-t bg-muted/30 px-5 py-3">
          <slot name="rodape" />
        </div>
      </DialogContent>
    </DialogPortal>
  </DialogRoot>
</template>
