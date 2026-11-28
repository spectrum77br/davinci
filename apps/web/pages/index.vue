<script setup lang="ts">
// 27/11/2026 (M-118): o Dashboard saiu — a página inicial (/) agora abre a
// tela Tarefas. Sem permissão própria de propósito: o "voltar ao início" do
// 403 manda para cá, e Tarefas abre para qualquer pessoa logada. O operador
// de estoque, o domínio gestaoestoque.com e a conta pendente continuam
// desviados antes, pelos middlewares globais (auth.global.ts e
// operator-domain.global.ts), que rodam antes desta página.
await navigateTo('/tarefas', { redirectCode: 302 })
</script>

<template><div /></template>
