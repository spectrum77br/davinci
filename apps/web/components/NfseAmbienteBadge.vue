<script setup lang="ts">
// Selo do ambiente de uma empresa na NFE.io: Teste (nota simulada, sem valor
// fiscal), Produção (nota de verdade) ou não ligada. Desde 29/09 o ambiente é
// de cada empresa (o cadastro dela na NFE.io), não do servidor.
import { computed } from 'vue'
import { FlaskConical, HelpCircle, ShieldAlert, Unplug } from 'lucide-vue-next'

const props = withDefaults(
  defineProps<{
    ambiente: string | null | undefined // 'Production' | 'Development' | 'Staging'
    ligada?: boolean
    tamanho?: 'sm' | 'md'
  }>(),
  { ligada: true, tamanho: 'md' },
)

const selo = computed(() => {
  const sm = props.tamanho === 'sm'
  if (!props.ligada) {
    return {
      classe: 'pill-muted',
      icone: Unplug,
      texto: sm ? 'NÃO INTEGRADA' : 'Não integrada',
      dica: 'A empresa ainda não está integrada na NFE.io: não emite.',
    }
  }
  if (props.ambiente === 'Production') {
    return {
      classe: 'pill-danger',
      icone: ShieldAlert,
      texto: sm ? 'PRODUÇÃO' : 'Produção',
      dica: 'Na NFE.io esta empresa está em Produção: as notas são de verdade e valem para a prefeitura e a Receita.',
    }
  }
  if (props.ambiente === 'Development' || props.ambiente === 'Staging') {
    return {
      classe: 'pill-warning',
      icone: FlaskConical,
      texto: sm ? 'TESTE' : 'Teste',
      dica: 'Na NFE.io esta empresa está em Teste: as notas saem simuladas e não valem como nota fiscal.',
    }
  }
  return {
    classe: 'pill-muted',
    icone: HelpCircle,
    texto: sm ? 'AMBIENTE?' : 'Ambiente não informado',
    dica: 'A NFE.io não informou se esta empresa está em Teste ou Produção. Clique em Atualizar no cartão da NFE.io.',
  }
})
</script>

<template>
  <NfseDica :texto="selo.dica" lado="bottom">
    <span
      tabindex="0"
      class="cursor-default whitespace-nowrap focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      :class="[selo.classe, tamanho === 'md' && 'h-7 px-2.5 text-xs', tamanho === 'sm' && 'tracking-wide']"
      :aria-label="`${selo.texto}. ${selo.dica}`"
    >
      <component :is="selo.icone" class="size-3.5 shrink-0" aria-hidden="true" />
      {{ selo.texto }}
    </span>
  </NfseDica>
</template>
