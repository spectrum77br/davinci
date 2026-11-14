<script setup lang="ts">
definePageMeta({
  middleware: ['permission'],
  // Igual a /pricing/[tab] (13/11/2026): quem só tem Faturamento também
  // passa, e o redirecionamento abaixo o leva ao Faturamento pelo middleware
  // de [tab].vue.
  permissionAny: [
    { resource: 'tabela_precos', action: 'view' },
    { resource: 'faturamento', action: 'view' },
  ],
})

await navigateTo('/pricing/tabela', { redirectCode: 302 })
</script>

<template><div /></template>
