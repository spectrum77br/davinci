<script setup lang="ts">
// Bloco de formulário (gavetas da Emissão de Serviço): título, ícone,
// descrição e âncora `id` (para o "corrigir" rolar até aqui).
import type { Component } from 'vue'

defineProps<{
  titulo: string
  descricao?: string
  icone?: Component
  id?: string
}>()
</script>

<template>
  <section :id="id" class="scroll-mt-4 space-y-3 border-t pt-5 transition-shadow first:border-t-0 first:pt-0">
    <div class="flex items-start gap-2">
      <component :is="icone" v-if="icone" class="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
      <div class="min-w-0 flex-1">
        <h3 class="text-sm font-semibold">{{ titulo }}</h3>
        <p v-if="descricao" class="text-xs text-muted-foreground">{{ descricao }}</p>
      </div>
      <div v-if="$slots.acoes" class="ml-auto flex shrink-0 items-center gap-2">
        <slot name="acoes" />
      </div>
    </div>
    <slot />
  </section>
</template>
