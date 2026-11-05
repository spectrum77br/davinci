<script setup lang="ts">
// Barra fixa de "Compras feitas" (04/11/2026, Eduardo: "igual emissão de serviço,
// tem que dar para imprimir a nota etc."): quantas NFs de compra estão marcadas e
// quanto somam, e os botões de lote — imprimir (as DANFEs num PDF só), baixar os
// PDFs ou os XMLs (.zip, uma pasta por empresa que comprou). Mesmo visual da barra
// de Notas emitidas (NfseHistoricoBarra). Só aparece com NF marcada; quem marca é
// a lista (CompraLista).
import { computed } from 'vue'
import { FileCode2, FileDown, Loader2, Printer } from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import { fmtBrl, MAX_LOTE_ARQUIVOS, plural } from '~/lib/nfse'
import type { Compra } from '~/lib/compra'

type Acao = 'imprimir' | 'pdf' | 'xml'

const props = withDefaults(
  defineProps<{
    marcadas: Compra[]
    // A ação em andamento (as outras esperam; o botão dela gira).
    acao?: Acao | null
  }>(),
  { acao: null },
)

const emit = defineEmits<{
  (e: 'imprimir'): void
  (e: 'baixar', tipo: 'pdf' | 'xml'): void
  (e: 'limpar'): void
}>()

const n = computed(() => props.marcadas.length)
const soma = computed(() => props.marcadas.reduce((s, c) => s + (Number(c.total) || 0), 0))

// O servidor junta no máximo 100 por vez (igual à NFS-e).
const demais = computed(() => n.value > MAX_LOTE_ARQUIVOS)
const bloqueio = computed(() => (demais.value ? `No máximo ${MAX_LOTE_ARQUIVOS} por vez: filtre por mês.` : null))
const ocupado = computed(() => !!props.acao)

const BOTOES = [
  { id: 'imprimir' as const, rotulo: 'Imprimir', icone: Printer, dica: 'Junta as DANFEs num PDF só e abre em outra aba para imprimir.' },
  { id: 'pdf' as const, rotulo: 'Baixar PDFs (.zip)', icone: FileDown, dica: 'Um .zip com as DANFEs, uma pasta por empresa que comprou.' },
  { id: 'xml' as const, rotulo: 'Baixar XMLs (.zip)', icone: FileCode2, dica: 'Um .zip com os XMLs, uma pasta por empresa que comprou.' },
]

function clicar(id: Acao) {
  if (ocupado.value || demais.value) return
  if (id === 'imprimir') emit('imprimir')
  else emit('baixar', id)
}
</script>

<template>
  <Transition
    enter-active-class="transition duration-200 ease-out motion-reduce:transition-none"
    enter-from-class="translate-y-3 opacity-0"
    leave-active-class="transition duration-150 ease-in motion-reduce:transition-none"
    leave-to-class="translate-y-3 opacity-0"
  >
    <div v-if="n > 0" class="sticky bottom-4 z-30 mx-auto w-fit max-w-full">
      <div
        class="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border bg-background/95 px-4 py-2.5 shadow-xl backdrop-blur supports-[backdrop-filter]:bg-background/80"
        role="region"
        aria-label="imprimir ou baixar as NFs de compra marcadas"
      >
        <span class="text-sm tabular-nums" aria-live="polite">
          <strong class="font-semibold">{{ plural(n, 'NF marcada', 'NFs marcadas') }}</strong>
          · <span title="soma das compras marcadas">{{ fmtBrl(soma) }}</span>
        </span>
        <span v-if="demais" class="text-xs text-amber-700 dark:text-amber-400" role="status">
          no máximo {{ MAX_LOTE_ARQUIVOS }} por vez: filtre por mês
        </span>
        <Button size="sm" variant="ghost" :disabled="ocupado" @click="emit('limpar')">limpar</Button>
        <div class="h-5 w-px bg-border" aria-hidden="true" />
        <NfseDica v-for="b in BOTOES" :key="b.id" :texto="bloqueio ?? b.dica">
          <span class="inline-flex" :tabindex="bloqueio ? 0 : undefined">
            <Button
              size="sm"
              :variant="b.id === 'imprimir' ? 'default' : 'outline'"
              :disabled="ocupado || demais"
              :aria-busy="acao === b.id ? 'true' : undefined"
              @click="clicar(b.id)"
            >
              <Loader2
                v-if="acao === b.id"
                class="mr-1.5 size-4 animate-spin motion-reduce:animate-none"
                aria-hidden="true"
              />
              <component :is="b.icone" v-else class="mr-1.5 size-4" aria-hidden="true" />
              {{ b.rotulo }}
            </Button>
          </span>
        </NfseDica>
      </div>
    </div>
  </Transition>
</template>
