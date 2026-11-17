<script setup lang="ts">
// 17/11/2026 (Novos procedimentos 7): Notas fiscais saiu do menu e virou a aba
// "Notas fiscais" da Logística (/logistica?tab=notas). Esta rota só
// redireciona para lá, para os favoritos e os links antigos (Ouvidoria: Vigia
// de Nota Cheia com ?de=&ate=&nf=) continuarem valendo — mantém a query e a
// mesma permissão de antes. A tela em si está em components/NotasFiscaisPainel.vue.
definePageMeta({
  middleware: [
    'permission',
    (to) => navigateTo({ path: '/logistica', query: { ...to.query, tab: 'notas' }, hash: to.hash }, { replace: true }),
  ],
  permission: { resource: 'notas_fiscais', action: 'view' },
})
</script>

<template>
  <div class="p-4 text-sm text-muted-foreground">abrindo as Notas fiscais dentro da Logística…</div>
</template>
