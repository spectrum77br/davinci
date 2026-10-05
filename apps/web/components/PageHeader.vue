<script setup lang="ts">
defineProps<{
  title: string
  description?: string
  resource?: string
}>()
</script>

<template>
  <div class="page-header flex flex-wrap items-end gap-3 mb-4 sm:mb-5">
    <div class="min-w-0">
      <h1 class="page-header-title text-xl sm:text-[22px] font-semibold tracking-tight leading-tight">{{ title }}</h1>
      <p v-if="description" class="page-header-description text-sm text-muted-foreground mt-0.5">{{ description }}</p>
    </div>
    <div v-if="$slots.actions" class="w-full sm:w-auto sm:ml-auto flex flex-wrap items-center gap-2 min-w-0">
      <slot name="actions" />
    </div>
  </div>
</template>

<style scoped>
@media (max-width: 1023px) and (max-height: 500px) and (orientation: landscape) {
  .page-header { gap: 0.5rem; margin-bottom: 0.75rem; }
  .page-header-title { font-size: 1.125rem; }
  .page-header-description { font-size: 0.75rem; line-height: 1.4; }
}
</style>
