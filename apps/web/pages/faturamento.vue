<script setup lang="ts">
// 13/11/2026 (Organização do DaVinci, item 6): o Faturamento saiu do menu e
// virou o botão "Faturamento" da Tabela de Preços, ao lado de Celular · Mala ·
// Eletro. Esta rota só redireciona para /pricing/faturamento, para os
// favoritos antigos continuarem valendo (a mesma permissão de antes). A tela
// em si está em components/FaturamentoPainel.vue.
definePageMeta({
  middleware: [
    'permission',
    (to) => navigateTo({ path: '/pricing/faturamento', query: to.query, hash: to.hash }, { replace: true }),
  ],
  permission: { resource: 'faturamento', action: 'view' },
})
</script>

<template>
  <div class="p-4 text-sm text-muted-foreground">abrindo o Faturamento dentro da Tabela de Preços…</div>
</template>
