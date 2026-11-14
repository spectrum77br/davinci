<script setup lang="ts">
// 13/11/2026 (Organização do DaVinci, item 2): Anúncios saiu do menu e virou a
// aba "Anúncios" de Produtos, dividida por departamento como a Tabela de
// Preços. Esta rota só redireciona para /produtos?aba=anuncios (mantendo a
// query), para os favoritos antigos continuarem valendo — com a mesma
// permissão de antes. A tabela está em components/ProdutosAnunciosPainel.vue;
// "Solicitações de anúncio" saiu da tela (a tabela e a API continuam).
definePageMeta({
  middleware: [
    'permission',
    (to) => navigateTo(
      { path: '/produtos', query: { ...to.query, aba: 'anuncios' }, hash: to.hash },
      { replace: true },
    ),
  ],
  permission: { resource: 'anuncios', action: 'view' },
})
</script>

<template>
  <div class="p-4 text-sm text-muted-foreground">abrindo os Anúncios dentro de Produtos…</div>
</template>
