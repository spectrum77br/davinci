<script setup lang="ts">
// Lista do que está feito (verde), faltando (vermelho) ou ainda não sabemos
// (âmbar). Clicar no item emite `ir(chave)`; o botão da direita, `acao(chave)`.
import { CheckCircle2, HelpCircle, XCircle } from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import { TOM_PILL, TOM_TEXTO, type ChecklistItem } from '~/lib/nfse'

withDefaults(
  defineProps<{
    itens: ChecklistItem[]
    titulo?: string
    variante?: 'lista' | 'chips'
    disabled?: boolean
  }>(),
  { variante: 'lista', disabled: false },
)

const emit = defineEmits<{ (e: 'ir', chave: string): void; (e: 'acao', chave: string): void }>()

function icone(ok: boolean | null) {
  return ok === true ? CheckCircle2 : ok === false ? XCircle : HelpCircle
}
function cor(ok: boolean | null) {
  return ok === true ? TOM_TEXTO.sucesso : ok === false ? TOM_TEXTO.perigo : TOM_TEXTO.atencao
}
function pill(ok: boolean | null) {
  return ok === true ? TOM_PILL.sucesso : ok === false ? TOM_PILL.perigo : TOM_PILL.atencao
}
function estadoLido(ok: boolean | null) {
  return ok === true ? 'feito' : ok === false ? 'falta' : 'não verificado'
}
</script>

<template>
  <div class="space-y-1.5">
    <p v-if="titulo" class="text-xs font-medium text-muted-foreground">{{ titulo }}</p>

    <ul v-if="variante === 'lista'" class="space-y-0.5">
      <li v-for="it in itens" :key="it.chave" class="flex items-start gap-1">
        <button
          type="button"
          class="flex min-w-0 flex-1 items-start gap-2 rounded px-2 py-1.5 text-left text-sm hover:bg-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-default disabled:hover:bg-transparent"
          :disabled="disabled"
          @click="emit('ir', it.chave)"
        >
          <component :is="icone(it.ok)" class="mt-0.5 size-4 shrink-0" :class="cor(it.ok)" aria-hidden="true" />
          <span class="sr-only">{{ estadoLido(it.ok) }}:</span>
          <!-- 01/10/2026: o título quebra linha (nome de certificado e pendência
               compridos vazavam do diálogo e do popover "O que falta"); o
               detalhe vai na linha de baixo. -->
          <span class="min-w-0 flex-1 break-words">
            <span :class="it.ok === true && 'text-muted-foreground'">{{ it.titulo }}</span>
            <span v-if="it.naoSalvo" class="ml-1 text-xs text-amber-600 dark:text-amber-400">(não salvo)</span>
            <span v-if="it.detalhe" class="block text-xs text-muted-foreground">{{ it.detalhe }}</span>
          </span>
        </button>
        <Button
          v-if="it.acao && !disabled"
          variant="link"
          class="ml-auto mt-1.5 h-auto shrink-0 px-2 py-0 text-xs"
          @click="emit('acao', it.chave)"
        >{{ it.acao }}</Button>
      </li>
    </ul>

    <div v-else class="flex flex-wrap gap-1.5">
      <button
        v-for="it in itens"
        :key="it.chave"
        type="button"
        :class="pill(it.ok)"
        class="hover:opacity-80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-default disabled:hover:opacity-100"
        :disabled="disabled"
        :title="it.detalhe"
        @click="emit('ir', it.chave)"
      >
        <component :is="icone(it.ok)" class="size-3 shrink-0" aria-hidden="true" />
        <span class="sr-only">{{ estadoLido(it.ok) }}:</span>
        {{ it.titulo }}
      </button>
    </div>
  </div>
</template>
