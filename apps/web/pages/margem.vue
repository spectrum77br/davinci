<script setup lang="ts">
// A tela mora em components/MargemPainel.vue (16/10): também é a aba "Margem" da Conciliação.
definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'margem', action: 'view' },
})
</script>

<template>
  <MargemPainel />
</template>
