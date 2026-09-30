<script setup lang="ts">
// Rótulo + controle + dica + erro de um campo. Com erro, a dica some e o
// erro é anunciado (role=alert).
defineProps<{
  rotulo: string
  dica?: string
  erro?: string | null
  obrigatorio?: boolean
  opcional?: boolean
  para?: string
}>()
</script>

<template>
  <div class="space-y-1.5">
    <label :for="para" class="block text-sm font-medium">
      {{ rotulo }}
      <span v-if="obrigatorio" class="text-red-500" aria-hidden="true">*</span>
      <span v-else-if="opcional" class="font-normal text-muted-foreground">(opcional)</span>
    </label>
    <slot />
    <p v-if="erro" class="text-xs text-red-600 dark:text-red-400" role="alert">{{ erro }}</p>
    <p v-else-if="dica" class="text-xs text-muted-foreground">{{ dica }}</p>
  </div>
</template>
