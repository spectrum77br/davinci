<script setup lang="ts">
// Gaveta à direita da Emissão de Serviço (nota fixa, tomador, nota avulsa,
// dados fiscais, detalhe da nota). Com `sujo`, fechar pergunta antes
// ("Sair sem salvar?") pelo diálogo do próprio app. `rolarPara(id)` leva até
// uma seção do corpo e pisca um contorno nela por 2 s.
import { inject, ref } from 'vue'
import {
  DialogContent, DialogDescription, DialogOverlay, DialogPortal, DialogRoot, DialogTitle,
} from 'reka-ui'
import { X } from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import { NFSE_TELA } from '~/lib/nfse'

const props = withDefaults(
  defineProps<{
    open: boolean
    titulo: string
    subtitulo?: string
    largura?: 'md' | 'lg'
    sujo?: boolean
    fechavel?: boolean
    textoSujo?: string
  }>(),
  { largura: 'lg', sujo: false, fechavel: true, textoSujo: 'O que você mudou vai se perder.' },
)

const emit = defineEmits<{ (e: 'update:open', v: boolean): void }>()

const tela = inject(NFSE_TELA, null)
const corpo = ref<HTMLElement | null>(null)
let perguntando = false

async function mudar(v: boolean) {
  if (v) return emit('update:open', true)
  if (!props.fechavel || perguntando) return
  if (props.sujo && tela) {
    perguntando = true
    try {
      const ok = await tela.confirmar({
        titulo: 'Sair sem salvar?',
        texto: props.textoSujo,
        tom: 'perigo',
        botao: 'Sair sem salvar',
        voltar: 'Continuar editando',
      })
      if (!ok) return
    } finally {
      perguntando = false
    }
  }
  emit('update:open', false)
}

function segurar(ev: Event) {
  if (!props.fechavel) ev.preventDefault()
}

const DESTAQUE = ['ring-2', 'ring-amber-400', 'rounded-lg']

function rolarPara(id: string) {
  const alvo = corpo.value?.querySelector<HTMLElement>(`#${CSS.escape(id)}`)
  if (!alvo) return
  if (alvo instanceof HTMLDetailsElement) alvo.open = true
  alvo.scrollIntoView({ behavior: 'smooth', block: 'start' })
  alvo.classList.add(...DESTAQUE)
  setTimeout(() => alvo.classList.remove(...DESTAQUE), 2000)
}

defineExpose({ rolarPara })
</script>

<template>
  <DialogRoot :open="open" @update:open="mudar">
    <DialogPortal>
      <DialogOverlay
        class="fixed inset-0 z-50 bg-black/40 duration-200 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=open]:fade-in-0 data-[state=closed]:fade-out-0 motion-reduce:animate-none"
      />
      <DialogContent
        class="fixed inset-y-0 right-0 z-50 flex h-full w-full flex-col border-l bg-background shadow-xl duration-200 focus:outline-none data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=open]:fade-in-0 data-[state=closed]:fade-out-0 data-[state=open]:slide-in-from-right data-[state=closed]:slide-out-to-right motion-reduce:animate-none"
        :class="largura === 'md' ? 'sm:max-w-[480px]' : 'sm:max-w-[600px]'"
        @escape-key-down="segurar"
        @pointer-down-outside="segurar"
        @interact-outside="segurar"
      >
        <div class="flex items-start gap-3 border-b px-5 py-4">
          <div class="min-w-0 flex-1">
            <div class="flex flex-wrap items-center gap-2">
              <DialogTitle class="text-base font-semibold leading-tight">{{ titulo }}</DialogTitle>
              <slot name="cabecalho-extra" />
            </div>
            <DialogDescription v-if="subtitulo" class="mt-0.5 text-xs text-muted-foreground">
              {{ subtitulo }}
            </DialogDescription>
            <DialogDescription v-else class="sr-only">{{ titulo }}</DialogDescription>
          </div>
          <Button
            v-if="fechavel"
            variant="ghost"
            size="icon"
            class="-mr-2 -mt-1 size-8 shrink-0"
            aria-label="fechar"
            @click="mudar(false)"
          >
            <X class="size-4" />
          </Button>
        </div>

        <div ref="corpo" class="flex-1 overflow-y-auto px-5 py-5">
          <slot />
        </div>

        <div
          v-if="$slots.rodape"
          class="flex items-center justify-between gap-2 border-t bg-muted/30 px-5 py-3"
        >
          <slot name="rodape" />
        </div>
      </DialogContent>
    </DialogPortal>
  </DialogRoot>
</template>
