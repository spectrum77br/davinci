<script setup lang="ts">
// Último minuto da senha extra (Emissão de Serviço, 30/09/2026): digitar a
// senha de novo aqui troca a chave SEM passar pelo cadeado, então o que está
// digitado na tela (valores do Emitir do mês, uma nota aberta) não se perde.
// Ignorar o aviso: a página tranca no fim dos 15 minutos, como sempre.
import { ref } from 'vue'
import { Clock, Loader2 } from 'lucide-vue-next'

const props = defineProps<{ trava: ReturnType<typeof useSenhaExtra> }>()
const campo = ref<HTMLInputElement | null>(null)

async function enviar() {
  if (!(await props.trava.desbloquear())) campo.value?.focus()
}
</script>

<template>
  <div
    v-if="trava.perto.value"
    role="alert"
    class="sticky top-2 z-40 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 shadow-sm dark:border-amber-700 dark:bg-amber-950 dark:text-amber-100"
  >
    <Clock class="size-4 shrink-0" aria-hidden="true" />
    <span class="min-w-0 flex-1">
      A senha desta página vence em menos de 1 minuto. Para continuar sem perder o que está na tela, digite a senha de novo.
    </span>
    <form class="flex items-center gap-2" @submit.prevent="enviar">
      <label for="renovar-senha" class="sr-only">Senha</label>
      <input
        id="renovar-senha"
        ref="campo"
        v-model="trava.senha.value"
        type="password"
        autocomplete="off"
        data-lpignore="true"
        data-1p-ignore
        placeholder="senha"
        class="h-8 w-36 rounded border bg-background px-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-primary/30"
        :disabled="trava.desbloqueando.value"
      />
      <button
        type="submit"
        class="h-8 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50"
        :disabled="trava.desbloqueando.value || !trava.senha.value"
      >
        <Loader2 v-if="trava.desbloqueando.value" class="mr-1 inline size-4 animate-spin" aria-hidden="true" />
        continuar
      </button>
    </form>
    <p v-if="trava.erro.value" class="w-full text-xs text-destructive">{{ trava.erro.value }}</p>
  </div>
</template>
