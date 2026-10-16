<script setup lang="ts">
// 16/10 (Vinicius): a Margem agora é a aba "Margem" da Conciliação; esta rota só redireciona
// (links antigos ?pedido=N continuam valendo).
definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'margem', action: 'view' },
})
const route = useRoute()
await navigateTo({ path: '/conciliacao', query: { ...route.query, visao: 'margem' } }, { replace: true })
</script>

<template>
  <div class="p-4 text-sm text-muted-foreground">abrindo a Margem dentro da Conciliação…</div>
</template>
