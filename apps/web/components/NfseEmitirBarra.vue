<script setup lang="ts">
// Barra fixa de ação do "Emitir do mês": quantas notas estão marcadas, o
// total, de quantas empresas, e o botão de emitir (vermelho quando alguma
// empresa marcada está em Produção na NFE.io).
// Sem nada marcado, oferece "marcar todas as prontas". Quem decide se ela
// aparece (edição + pelo menos 1 nota pronta) é a aba.
import { computed } from 'vue'
import { FlaskConical, Send, ShieldAlert } from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import { fmtBrl, plural, useNfseTela } from '~/lib/nfse'

const props = withDefaults(
  defineProps<{
    prontas: number
    totalProntas: number
    marcadas: number
    totalMarcadas: number
    empresas: number
    reenvios?: number // recusadas antes: vão de novo
    // Das marcadas, quantas são de empresa em Produção na NFE.io (nota de verdade).
    emProducao?: number
    ocupado?: boolean
    // Algum valor novo ainda está sendo conferido: só o botão de emitir espera.
    conferindo?: boolean
  }>(),
  { reenvios: 0, emProducao: 0, ocupado: false, conferindo: false },
)

const emit = defineEmits<{
  (e: 'emitir'): void
  (e: 'limpar'): void
  (e: 'marcar-todas'): void
}>()

const tela = useNfseTela()

const producao = computed(() => props.emProducao > 0)
const bloqueio = computed(() => {
  const st = tela.status.value
  if (tela.erroStatus.value || !st) return 'Não deu para conferir a ligação com a NFE.io.'
  if (!st.chave_configurada) return 'A chave de acesso da NFE.io não está configurada.'
  if (producao.value && !st.producao_liberada) {
    return 'Neste servidor a produção está travada: desmarque as notas de empresas em Produção.'
  }
  if (props.conferindo) return 'Espere terminar a conferência do valor novo.'
  return null
})
const textoBotao = computed(() => {
  const base = `Emitir ${plural(props.marcadas, 'nota', 'notas')}`
  if (!producao.value) return `${base} de teste`
  return props.emProducao === props.marcadas ? `${base} em produção` : `${base} (${props.emProducao} em produção)`
})
</script>

<template>
  <Transition
    enter-active-class="transition duration-200 ease-out motion-reduce:transition-none"
    enter-from-class="translate-y-3 opacity-0"
    leave-active-class="transition duration-150 ease-in motion-reduce:transition-none"
    leave-to-class="translate-y-3 opacity-0"
  >
    <div v-if="prontas > 0" class="sticky bottom-4 z-30 mx-auto w-fit max-w-full">
      <div
        class="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border bg-background/95 px-4 py-2.5 shadow-xl backdrop-blur supports-[backdrop-filter]:bg-background/80"
        role="region"
        aria-label="emitir as notas marcadas"
      >
        <template v-if="marcadas">
          <span v-if="producao" class="pill-danger tracking-wide">
            <ShieldAlert class="size-3.5 shrink-0" aria-hidden="true" /> PRODUÇÃO
          </span>
          <span v-else class="pill-warning tracking-wide">
            <FlaskConical class="size-3.5 shrink-0" aria-hidden="true" /> TESTE
          </span>
          <div class="h-5 w-px bg-border" aria-hidden="true" />
        </template>

        <template v-if="!marcadas">
          <span class="text-sm tabular-nums">
            {{ plural(prontas, 'pronta', 'prontas') }} · {{ fmtBrl(totalProntas) }}
          </span>
          <Button size="sm" variant="outline" :disabled="ocupado" @click="emit('marcar-todas')">
            marcar todas as prontas
          </Button>
        </template>

        <template v-else>
          <span class="text-sm tabular-nums" aria-live="polite">
            <strong class="font-semibold">{{ plural(marcadas, 'marcada', 'marcadas') }}</strong>
            · {{ fmtBrl(totalMarcadas) }} · {{ plural(empresas, 'empresa', 'empresas') }}
            <span v-if="reenvios" class="text-muted-foreground">
              · {{ reenvios }} {{ reenvios === 1 ? 'vai' : 'vão' }} de novo
            </span>
          </span>
          <Button size="sm" variant="ghost" :disabled="ocupado" @click="emit('limpar')">limpar</Button>
          <NfseDica :texto="bloqueio">
            <span class="inline-flex" :tabindex="bloqueio ? 0 : undefined">
              <Button
                size="sm"
                :variant="producao ? 'destructive' : 'default'"
                :disabled="ocupado || !!bloqueio"
                @click="emit('emitir')"
              >
                <Send class="mr-1.5 size-4" aria-hidden="true" />
                {{ textoBotao }}
              </Button>
            </span>
          </NfseDica>
        </template>
      </div>
    </div>
  </Transition>
</template>
