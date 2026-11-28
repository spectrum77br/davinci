<script setup lang="ts">
// 27/11/2026 (M-118): a tela "Primeiros passos" saiu. Quem guardou o endereço
// /onboarding cai na tela Tarefas, como a página inicial (/). Sem permissão
// própria; o operador de estoque, o domínio gestaoestoque.com e a conta
// pendente continuam desviados antes, pelos middlewares globais.
await navigateTo('/tarefas', { redirectCode: 302 })
</script>

<template><div /></template>
