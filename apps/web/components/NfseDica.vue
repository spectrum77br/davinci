<script setup lang="ts">
// Tooltip da Emissão de Serviço (reka Tooltip). Sem texto, só renderiza o
// conteúdo. Botão desabilitado não recebe o mouse: envolva num
// <span class="inline-flex" tabindex="0">. Precisa do TooltipProvider da página.
import { TooltipContent, TooltipPortal, TooltipRoot, TooltipTrigger } from 'reka-ui'

withDefaults(
  defineProps<{
    texto?: string | null
    lado?: 'top' | 'bottom' | 'left' | 'right'
    desativado?: boolean
  }>(),
  { lado: 'top', desativado: false },
)
</script>

<template>
  <slot v-if="!texto || desativado" />
  <TooltipRoot v-else>
    <TooltipTrigger as-child>
      <slot />
    </TooltipTrigger>
    <TooltipPortal>
      <TooltipContent
        :side="lado"
        :side-offset="4"
        :collision-padding="8"
        class="z-[80] max-w-xs whitespace-pre-line rounded-md bg-foreground px-2 py-1 text-xs leading-snug text-background shadow duration-150 data-[state=closed]:animate-out data-[state=delayed-open]:animate-in data-[state=closed]:fade-out-0 data-[state=delayed-open]:fade-in-0 motion-reduce:animate-none"
      >
        {{ texto }}
      </TooltipContent>
    </TooltipPortal>
  </TooltipRoot>
</template>
