<script setup lang="ts">
// Denúncia (30/09/2026): gaveta à direita das três telas (ficha do anúncio,
// da denúncia, do caso). Mesmo desenho da gaveta da Emissão de Serviço, sem o
// "sair sem salvar" — aqui ninguém edita, é só consulta.
import {
  DialogContent, DialogDescription, DialogOverlay, DialogPortal, DialogRoot, DialogTitle,
} from 'reka-ui'
import { X } from 'lucide-vue-next'

defineProps<{ open: boolean; titulo: string; subtitulo?: string }>()
const emit = defineEmits<{ (e: 'update:open', v: boolean): void }>()
</script>

<template>
  <DialogRoot :open="open" @update:open="(v) => emit('update:open', v)">
    <DialogPortal>
      <DialogOverlay
        class="fixed inset-0 z-50 bg-black/40 duration-200 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=open]:fade-in-0 data-[state=closed]:fade-out-0 motion-reduce:animate-none"
      />
      <DialogContent
        class="fixed inset-y-0 right-0 z-50 flex h-full w-full flex-col border-l bg-background shadow-xl duration-200 focus:outline-none sm:max-w-[680px] data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=open]:fade-in-0 data-[state=closed]:fade-out-0 data-[state=open]:slide-in-from-right data-[state=closed]:slide-out-to-right motion-reduce:animate-none"
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
      </DialogContent>
    </DialogPortal>
  </DialogRoot>
</template>
