<script setup lang="ts">
defineProps<{
  title: string
  description?: string
  resource?: string
}>()
</script>

<template>
  <div class="flex flex-wrap items-end gap-3 mb-5">
    <div class="min-w-0">
      <h1 class="text-[22px] font-semibold tracking-tight leading-tight">{{ title }}</h1>
      <p v-if="description" class="text-sm text-muted-foreground mt-0.5">{{ description }}</p>
    </div>
    <div class="ml-auto flex flex-wrap items-center gap-2">
      <slot name="actions" />
    </div>
  </div>
</template>
