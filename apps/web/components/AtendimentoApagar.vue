<script setup lang="ts">
// O botão "apagar (2:41)" no rodapé do balão da loja (03/11/2026, o "Retirar"
// do Duoke). Componente à parte por causa do relógio de 1 s: só ele se
// redesenha a cada segundo, não a conversa inteira. Quem decide se aparece e
// até quando é o servidor (`pode_apagar_ate`); aqui só a contagem — chegou a
// zero, o botão some (a rota confere o prazo de novo).
import { Loader2, Trash2 } from 'lucide-vue-next'
import { useRelogio } from '~/components/AtendimentoPlataforma.vue'

const props = defineProps<{
  ate: string
  apagando?: boolean
  disabled?: boolean
}>()
const emit = defineEmits<{ (e: 'apagar'): void }>()

const agora = useRelogio(1000)
const faltaS = computed(() => {
  const ate = Date.parse(props.ate)
  return Number.isNaN(ate) ? 0 : Math.max(0, Math.floor((ate - agora.value) / 1000))
})
const contagem = computed(() => `${Math.floor(faltaS.value / 60)}:${String(faltaS.value % 60).padStart(2, '0')}`)
</script>

<template>
  <button
    v-if="faltaS > 0 || apagando"
    type="button"
    class="ml-1 inline-flex items-center gap-0.5 rounded px-1 text-[10px] text-muted-foreground hover:bg-red-500/10 hover:text-red-700 disabled:opacity-50 dark:hover:text-red-300"
    :disabled="disabled || apagando"
    title="apagar na Shopee: o comprador deixa de ver (pede confirmação; não dá para desfazer)"
    data-apagar-mensagem
    @click="emit('apagar')"
  >
    <Loader2 v-if="apagando" class="size-3 animate-spin" /><Trash2 v-else class="size-3" />
    apagar ({{ contagem }})
  </button>
</template>
