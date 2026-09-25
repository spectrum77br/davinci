<script setup lang="ts">
// Cartão de senha extra — o mesmo visual do Valuation. Recebe o objeto de
// useSenhaExtra() e avisa quando desbloqueou.
import { ref, onMounted } from 'vue'
import { Loader2, Lock } from 'lucide-vue-next'

const props = defineProps<{ titulo: string; trava: ReturnType<typeof useSenhaExtra> }>()
const emit = defineEmits<{ desbloqueado: [] }>()
const campo = ref<HTMLInputElement | null>(null)

onMounted(() => campo.value?.focus())

async function enviar() {
  if (await props.trava.desbloquear()) emit('desbloqueado')
  else campo.value?.focus()
}
</script>

<template>
  <div class="flex items-center justify-center min-h-[60vh] p-4">
    <form
      class="w-full max-w-sm space-y-4 border rounded-lg p-6 bg-card shadow-sm"
      @submit.prevent="enviar"
    >
      <div class="flex items-center gap-2">
        <Lock class="size-5 text-muted-foreground" />
        <h1 class="text-lg font-semibold">{{ titulo }}</h1>
      </div>
      <p class="text-xs text-muted-foreground">
        Esta página exige uma senha adicional. O acesso fica liberado por 15 minutos nesta aba.
      </p>
      <div class="space-y-1">
        <label :for="`senha-${titulo}`" class="block text-xs font-medium">Senha</label>
        <input
          :id="`senha-${titulo}`"
          ref="campo"
          v-model="trava.senha.value"
          type="password"
          autocomplete="off"
          data-lpignore="true"
          data-1p-ignore
          class="w-full h-9 border rounded px-2 bg-background text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
          :disabled="trava.desbloqueando.value"
        />
      </div>
      <p v-if="trava.erro.value" class="text-xs text-destructive">{{ trava.erro.value }}</p>
      <button
        type="submit"
        class="w-full h-9 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:opacity-90 disabled:opacity-50"
        :disabled="trava.desbloqueando.value || !trava.senha.value"
      >
        <Loader2 v-if="trava.desbloqueando.value" class="inline h-4 w-4 animate-spin mr-1" />
        Desbloquear
      </button>
    </form>
  </div>
</template>
