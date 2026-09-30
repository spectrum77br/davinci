<script setup lang="ts">
// Chip de situação de uma nota (emitida, recusada, sem resposta…) — rótulo,
// ícone e cor saem de SITUACOES. Só visual: quem quer clique envolve num
// button ou PopoverTrigger.
import { computed } from 'vue'
import { SELO_TESTE, situacao, statusPill, type EstadoNota } from '~/lib/nfse'

const props = withDefaults(
  defineProps<{
    estado: EstadoNota
    numero?: string | null
    rotulo?: string
    teste?: boolean
    compacto?: boolean
    dica?: boolean
  }>(),
  { numero: null, teste: false, compacto: false, dica: true },
)

const info = computed(() => situacao(props.estado))
const texto = computed(() => (props.rotulo ?? info.value.rotulo) + (props.numero ? ` · nº ${props.numero}` : ''))
</script>

<template>
  <NfseDica :texto="dica ? info.explicacao : null">
    <span class="inline-flex items-center gap-1.5 whitespace-nowrap">
      <span :class="statusPill(estado)">
        <component
          :is="info.icone"
          v-if="!props.compacto"
          class="size-3 shrink-0"
          :class="info.girar && 'animate-spin motion-reduce:animate-none'"
          aria-hidden="true"
        />
        {{ texto }}
      </span>
      <span v-if="teste" :class="SELO_TESTE">teste</span>
    </span>
  </NfseDica>
</template>
