<script setup lang="ts">
// Produtos 2 (23/11/2026, Marco): a tela de produtos do próprio DaVinci ERP, igual à de lá: lista, abrir o produto, abas e editar (em modo consulta, só leitura enquanto o Bling manda).
// O ERP mostra só a tela (?embed=1), sem os menus dele, no mesmo site e atrás do mesmo portão do
// /erp: só quem entra no ERP (middleware erp; a API do ERP recusa os outros no Caddy).
definePageMeta({ middleware: ['admin', 'erp'] })
</script>

<template>
  <iframe src="/erp?embed=1#products" title="Produtos do DaVinci ERP" class="erp-tela" />
</template>

<style scoped>
.erp-tela {
  display: block;
  width: 100%;
  height: calc(100vh - 7rem);
  min-height: 600px;
  border: 0;
  border-radius: 0.5rem;
  background: #f7f9fb;
}
</style>
