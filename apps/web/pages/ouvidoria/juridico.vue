<script setup lang="ts">
// ── Ouvidoria › Jurídico (11/11/2026) ───────────────────────────────────────
// Os processos judiciais num lugar só — os casos da Denúncia que foram ao advogado, os
// chamados encaminhados ao jurídico e os incluídos à mão. Tela: components/JuridicoPainel.vue.
// Permissão: a mesma da Denúncia (quem já acompanhava o jurídico dos casos).
definePageMeta({ middleware: ['permission'], permission: { resource: 'denuncia', action: 'view' } })
</script>

<template>
  <JuridicoPainel />
</template>
