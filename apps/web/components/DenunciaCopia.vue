<script setup lang="ts">
// Denúncia (30/09/2026): selo "cópia do Mac mini · há 3 min" no topo das três
// telas. Os dados não nascem no DaVinci — o mini da Makisa manda a cada 5 min.
// Passou de 30 min sem cópia: fica âmbar (mini desligado ou sem internet).
import { onMounted, ref } from 'vue'
import { Monitor } from 'lucide-vue-next'
import { type Resumo, copiaAtrasada, haQuanto } from '~/lib/denuncia'

const { api } = useApi()
const resumo = ref<Resumo | null>(null)

async function carregar() {
  try {
    resumo.value = await api<Resumo>('/api/denuncia/resumo')
  } catch {
    resumo.value = null
  }
}
onMounted(carregar)
defineExpose({ carregar, resumo })
</script>

<template>
  <span
    v-if="resumo"
    class="inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs"
    :class="copiaAtrasada(resumo.ultimo_envio_em) ? 'border-amber-400 bg-amber-50 text-amber-800 dark:bg-amber-950/30 dark:text-amber-300' : 'text-muted-foreground'"
    :title="copiaAtrasada(resumo.ultimo_envio_em)
      ? 'O Mac mini da Makisa não manda a cópia há mais de 30 min — pode estar desligado ou sem internet.'
      : 'Os dados vêm do sistema de Fiscalização do Mac mini da Makisa, que manda o que mudou a cada 5 min.'"
  >
    <Monitor class="size-3.5" />
    cópia do Mac mini · {{ haQuanto(resumo.ultimo_envio_em) }}
    <template v-if="resumo.provas_sem_arquivo"> · {{ resumo.provas_sem_arquivo.toLocaleString('pt-BR') }} provas chegando</template>
  </span>
</template>
