<script setup lang="ts">
import { ref } from 'vue'

const collapsed = ref(false)
if (import.meta.client) {
  const stored = localStorage.getItem('sidebar:collapsed')
  if (stored === '1') collapsed.value = true
}
function toggle() {
  collapsed.value = !collapsed.value
  if (import.meta.client) {
    localStorage.setItem('sidebar:collapsed', collapsed.value ? '1' : '0')
  }
}
</script>

<template>
  <div class="min-h-screen flex bg-background text-foreground">
    <AppSidebar :collapsed="collapsed" @toggle="toggle" />
    <div class="flex-1 min-w-0 flex flex-col">
      <AppTopbar />
      <main class="flex-1 px-6 py-6">
        <slot />
      </main>
    </div>
  </div>
</template>
