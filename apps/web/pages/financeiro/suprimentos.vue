<script setup lang="ts">
// Certificações pelo menu (Suprimentos). A tela mora em
// components/CertificacoesPainel.vue, que também é a aba "Certificações"
// da Importação (05/11/2026).
definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'financeiro_suprimentos', action: 'view' },
})
</script>

<template>
  <CertificacoesPainel />
</template>
