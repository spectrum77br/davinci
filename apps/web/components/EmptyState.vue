<script setup lang="ts">
defineProps<{
  icon?: any
  title: string
  description?: string
}>()
</script>

<template>
  <div class="rounded-xl border border-dashed py-14 px-6 text-center bg-muted/20">
    <div v-if="icon" class="mx-auto size-12 rounded-xl bg-background border grid place-items-center text-muted-foreground mb-3">
      <component :is="icon" class="size-5" />
    </div>
    <h3 class="text-sm font-semibold">{{ title }}</h3>
    <p v-if="description" class="text-sm text-muted-foreground mt-1 max-w-md mx-auto">{{ description }}</p>
    <div class="mt-4">
      <slot />
    </div>
  </div>
</template>
