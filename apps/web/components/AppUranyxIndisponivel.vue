<script setup lang="ts">
// App Uranyx: no lugar da tela quando a API do DaVinci responde 503
// app_uranyx_indisponivel numa leitura (API do app fora do ar, sem resposta a
// tempo, ou o DaVinci sem APP_URANYX_API_URL / APP_URANYX_ADMIN_TOKEN). Nada
// de tela quebrada. Alteração que falha não chega aqui: o erro fica na ação.
import { PlugZap, RefreshCw } from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import { semRespostaATempo, TEXTO_INDISPONIVEL } from '~/lib/appUranyx'

const props = defineProps<{ mensagem?: string | null; carregando?: boolean }>()
const emit = defineEmits<{ (e: 'tentar'): void }>()

// A frase da API só entra quando diz algo além do título.
const detalhe = computed(() => {
  const m = (props.mensagem || '').trim()
  return m && m !== TEXTO_INDISPONIVEL ? m : ''
})

// Sem resposta a tempo, um pedido que estava saindo pode ter sido feito: aí a
// tela não afirma "nada foi alterado".
const demorou = computed(() => semRespostaATempo(props.mensagem))
</script>

<template>
  <EmptyState :icon="PlugZap" :title="TEXTO_INDISPONIVEL">
    <p v-if="detalhe" class="mx-auto -mt-2 mb-3 max-w-md text-sm text-muted-foreground">{{ detalhe }}</p>
    <p class="mx-auto mb-4 max-w-md text-xs text-muted-foreground">
      {{ demorou ? 'A API do app demorou demais para responder ao DaVinci.' : 'O DaVinci não conseguiu falar com a API do app.' }}
      Confira se ela está no ar e se o DaVinci tem o endereço e o token (APP_URANYX_API_URL e APP_URANYX_ADMIN_TOKEN).
      <template v-if="!demorou">Nada foi alterado.</template>
    </p>
    <Button size="sm" variant="outline" :disabled="carregando" @click="emit('tentar')">
      <RefreshCw class="mr-1 size-4" :class="{ 'animate-spin': carregando }" /> Tentar de novo
    </Button>
  </EmptyState>
</template>
