<script setup lang="ts">
definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'tabela_precos', action: 'view' },
})

await navigateTo('/pricing/contas', { redirectCode: 302 })
</script>

<template><div /></template>
