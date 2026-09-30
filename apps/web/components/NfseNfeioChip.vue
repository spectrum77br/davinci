<script setup lang="ts">
// Situação do certificado A1 da empresa NA NFE.IO (quem assina a nota é a
// NFE.io): vence em / vence em N dias / venceu / sem certificado. Em Teste o
// certificado é opcional e o vencido não fica vermelho. Substitui o chip do
// certificado guardado no DaVinci e o cartão de teste no gov.br (29/09).
import { computed } from 'vue'
import { AlertTriangle, KeyRound, ShieldCheck, Unplug, XCircle } from 'lucide-vue-next'
import { situacaoCertificado, TOM_PILL, TOM_TEXTO, type Prestador } from '~/lib/nfse'

const props = defineProps<{ prestador: Prestador }>()

const sit = computed(() => situacaoCertificado(props.prestador))

const icone = computed(() => {
  const n = props.prestador.nfeio
  if (!n) return Unplug
  if (!n.cert_status && !n.cert_expira) return KeyRound
  if (sit.value.vencido) return XCircle
  if (sit.value.tom === 'atencao') return AlertTriangle
  return ShieldCheck
})
const corIcone = computed(() =>
  props.prestador.nfeio && !sit.value.vencido && sit.value.tom === 'neutro' && props.prestador.nfeio.cert_expira
    ? TOM_TEXTO.sucesso
    : '',
)
</script>

<template>
  <span :class="TOM_PILL[sit.tom]" class="max-w-full">
    <component :is="icone" class="size-3 shrink-0" :class="corIcone" aria-hidden="true" />
    <span class="truncate">{{ sit.rotulo }}</span>
  </span>
</template>
