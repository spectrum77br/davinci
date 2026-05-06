<script setup lang="ts">
definePageMeta({ layout: 'default' })
</script>

<template>
  <div class="max-w-md mx-auto mt-24 text-center space-y-3">
    <h1 class="text-3xl font-semibold">403</h1>
    <p class="text-muted-foreground">Você não tem permissão para acessar esta página.</p>
    <NuxtLink to="/" class="text-sm underline">voltar ao início</NuxtLink>
  </div>
</template>
