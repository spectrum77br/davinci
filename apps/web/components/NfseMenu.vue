<script setup lang="ts">
// Menu ⋯ das linhas (reka DropdownMenu). Item com `href` vira link (PDF/XML);
// os outros emitem `escolher(id)`. `perigo` pinta de vermelho (cancelar,
// excluir) e `separar` põe um traço antes.
import {
  DropdownMenuContent, DropdownMenuItem, DropdownMenuPortal, DropdownMenuRoot,
  DropdownMenuSeparator, DropdownMenuTrigger,
} from 'reka-ui'
import { MoreHorizontal } from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import type { MenuItem } from '~/lib/nfse'

withDefaults(
  defineProps<{
    itens: MenuItem[]
    rotulo?: string
    disabled?: boolean
  }>(),
  { rotulo: 'mais ações', disabled: false },
)

const emit = defineEmits<{ (e: 'escolher', id: string): void }>()

const ITEM =
  'flex cursor-pointer select-none items-center gap-2 rounded px-2 py-1.5 text-sm outline-none data-[disabled]:pointer-events-none data-[highlighted]:bg-muted data-[disabled]:opacity-50'
</script>

<template>
  <DropdownMenuRoot :modal="false">
    <DropdownMenuTrigger as-child :disabled="disabled">
      <Button variant="ghost" size="icon" class="size-8" :aria-label="rotulo" :disabled="disabled" @click.stop>
        <MoreHorizontal class="size-4" />
      </Button>
    </DropdownMenuTrigger>
    <DropdownMenuPortal>
      <DropdownMenuContent
        align="end"
        :side-offset="4"
        :collision-padding="8"
        class="z-[80] min-w-[220px] rounded-md border bg-background p-1 shadow-lg duration-150 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=open]:fade-in-0 data-[state=closed]:fade-out-0 data-[state=open]:zoom-in-95 data-[state=closed]:zoom-out-95 motion-reduce:animate-none"
        @click.stop
      >
        <template v-for="it in itens" :key="it.id">
          <DropdownMenuSeparator v-if="it.separar" class="my-1 h-px bg-border" />
          <DropdownMenuItem
            v-if="it.href"
            as-child
            :disabled="it.disabled"
            :class="[ITEM, it.perigo && 'text-red-600 dark:text-red-400']"
          >
            <a
              :href="it.href"
              :target="it.externo || !it.download ? '_blank' : undefined"
              :rel="it.externo || !it.download ? 'noopener' : undefined"
              :download="it.download ? '' : undefined"
            >
              <component :is="it.icone" v-if="it.icone" class="size-4 shrink-0 opacity-80" aria-hidden="true" />
              {{ it.rotulo }}
            </a>
          </DropdownMenuItem>
          <DropdownMenuItem
            v-else
            :disabled="it.disabled"
            :class="[ITEM, it.perigo && 'text-red-600 dark:text-red-400']"
            @select="emit('escolher', it.id)"
          >
            <component :is="it.icone" v-if="it.icone" class="size-4 shrink-0 opacity-80" aria-hidden="true" />
            {{ it.rotulo }}
          </DropdownMenuItem>
        </template>
      </DropdownMenuContent>
    </DropdownMenuPortal>
  </DropdownMenuRoot>
</template>
