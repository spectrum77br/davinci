<script setup lang="ts">
// Pós-venda › Chamados. A tela inteira mora em components/ChamadosPainel.vue
// (30/09/2026) — a aba Jurídico dela passou pra Ouvidoria › Denúncia.
definePageMeta({ middleware: ['permission'], permission: { resource: 'chamados', action: 'view' } })
</script>

<template>
  <ChamadosPainel />
</template>
