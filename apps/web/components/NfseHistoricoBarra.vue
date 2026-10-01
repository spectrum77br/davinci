<script setup lang="ts">
// Barra fixa de ação da aba "Notas emitidas" (30/09/2026): quantas notas
// estão marcadas, quanto somam, quantas são canceladas ou de teste, e os
// botões de lote — imprimir (um PDF só, com todas), baixar os PDFs ou os XMLs
// (.zip, uma pasta por empresa). Mesmo visual da barra do "Emitir do mês"
// (NfseEmitirBarra). Só aparece com nota marcada; quem marca é a aba.
import { computed } from 'vue'
import { FileCode2, FileDown, FlaskConical, Loader2, Printer } from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import { fmtBrl, MAX_LOTE_ARQUIVOS, plural, type Emissao } from '~/lib/nfse'

type Acao = 'imprimir' | 'pdf' | 'xml'

const props = withDefaults(
  defineProps<{
    marcadas: Emissao[]
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
// Soma só as emitidas: a cancelada não vale mais (igual ao rodapé da tabela).
const soma = computed(() =>
  props.marcadas.filter((l) => l.status === 'emitida').reduce((s, l) => s + (Number(l.valor_servico) || 0), 0),
)
const canceladas = computed(() => props.marcadas.filter((l) => l.status === 'cancelada').length)
const deTeste = computed(() => props.marcadas.filter((l) => l.teste).length)

// O servidor junta no máximo 100 por vez (a NFE.io entrega um arquivo por nota).
const demais = computed(() => n.value > MAX_LOTE_ARQUIVOS)
const bloqueio = computed(() =>
  demais.value ? `No máximo ${MAX_LOTE_ARQUIVOS} por vez: filtre por mês ou empresa.` : null,
)
const ocupado = computed(() => !!props.acao)

const botoes = computed(() => [
  { id: 'imprimir' as const, rotulo: 'Imprimir', icone: Printer, dica: 'Junta os PDFs num só e abre em outra aba para imprimir.' },
  { id: 'pdf' as const, rotulo: 'Baixar PDFs (.zip)', icone: FileDown, dica: 'Um .zip com os PDFs, uma pasta por empresa.' },
  { id: 'xml' as const, rotulo: 'Baixar XMLs (.zip)', icone: FileCode2, dica: 'Um .zip com os XMLs, uma pasta por empresa.' },
])

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
        aria-label="imprimir ou baixar as notas marcadas"
      >
        <span class="text-sm tabular-nums" aria-live="polite">
          <strong class="font-semibold">{{ plural(n, 'marcada', 'marcadas') }}</strong>
          · <span title="soma das emitidas (a cancelada não conta)">{{ fmtBrl(soma) }}</span>
          <span v-if="canceladas" class="text-muted-foreground">
            · {{ plural(canceladas, 'cancelada', 'canceladas') }}
          </span>
          <span v-if="deTeste" class="inline-flex items-center gap-1 text-amber-700 dark:text-amber-400">
            · <FlaskConical class="size-3.5" aria-hidden="true" /> {{ deTeste }} de teste
          </span>
        </span>
        <span v-if="demais" class="text-xs text-amber-700 dark:text-amber-400" role="status">
          no máximo {{ MAX_LOTE_ARQUIVOS }} por vez: filtre por mês ou empresa
        </span>
        <Button size="sm" variant="ghost" :disabled="ocupado" @click="emit('limpar')">limpar</Button>
        <div class="h-5 w-px bg-border" aria-hidden="true" />
        <NfseDica v-for="b in botoes" :key="b.id" :texto="bloqueio ?? b.dica">
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
