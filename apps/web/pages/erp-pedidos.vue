<script setup lang="ts">
// Pedidos 2 (23/11/2026, Marco): a tela de pedidos do próprio DaVinci ERP (Lidos das lojas), igual à de lá: logo da loja, número e situação do Bling, abrir a venda (em modo consulta, só leitura).
// O ERP mostra só a tela (?embed=1), sem os menus dele, no mesmo site e atrás do mesmo portão do
// /erp: só quem entra no ERP (middleware erp; a API do ERP recusa os outros no Caddy).
definePageMeta({ middleware: ['admin', 'erp'] })
</script>

<template>
  <iframe src="/erp?embed=1#orders/store" title="Pedidos do DaVinci ERP" class="erp-tela" />
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
