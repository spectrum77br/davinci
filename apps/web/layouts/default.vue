<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

const route = useRoute()
const collapsed = ref(false)
const mobileMenuOpen = ref(false)
const isMobile = ref(false)
let mobileViewport: MediaQueryList | undefined
let restoreScroll: (() => void) | undefined

function toggle() {
  collapsed.value = !collapsed.value
  if (import.meta.client) {
    try {
      localStorage.setItem('sidebar:collapsed', collapsed.value ? '1' : '0')
    } catch {
      // Keep the preference for this visit when storage is unavailable.
    }
  }
}
function closeMobileMenu() {
  mobileMenuOpen.value = false
}
function syncViewport() {
  isMobile.value = mobileViewport?.matches ?? false
  if (!isMobile.value) closeMobileMenu()
}

onMounted(() => {
  // Use the same initial state as SSR; apply browser preferences after hydration.
  try {
    collapsed.value = localStorage.getItem('sidebar:collapsed') === '1'
  } catch {
    // Private browsing can disable storage; navigation still works.
  }
  mobileViewport = window.matchMedia('(max-width: 1023px)')
  syncViewport()
  mobileViewport.addEventListener('change', syncViewport)
})

watch(() => route.fullPath, closeMobileMenu)
watch(mobileMenuOpen, (open) => {
  restoreScroll?.()
  restoreScroll = undefined
  if (!open || !import.meta.client) return

  // Fixing the body also prevents the page behind the drawer from scrolling
  // on iOS. Restore the exact position and existing inline styles on close.
  const body = document.body
  const scrollY = window.scrollY
  const previous = {
    position: body.style.position,
    top: body.style.top,
    left: body.style.left,
    right: body.style.right,
    overflow: body.style.overflow,
  }
  Object.assign(body.style, {
    position: 'fixed', top: `-${scrollY}px`, left: '0', right: '0', overflow: 'hidden',
  })
  restoreScroll = () => {
    Object.assign(body.style, previous)
    window.scrollTo({ top: scrollY, behavior: 'instant' })
  }
})

onBeforeUnmount(() => {
  mobileViewport?.removeEventListener('change', syncViewport)
  restoreScroll?.()
})
</script>

<template>
  <div class="app-shell min-h-screen min-h-dvh flex bg-background text-foreground">
    <div
      v-if="mobileMenuOpen"
      class="fixed inset-0 z-40 bg-black/45 backdrop-blur-[2px] lg:hidden"
      aria-hidden="true"
      @click="closeMobileMenu"
    />
    <AppSidebar
      :collapsed="collapsed"
      :mobile="isMobile"
      :mobile-open="mobileMenuOpen"
      @toggle="toggle"
      @close="closeMobileMenu"
    />
    <div :inert="mobileMenuOpen || undefined" class="flex-1 min-w-0 flex flex-col">
      <AppTopbar :mobile-menu-open="mobileMenuOpen" @toggle-menu="mobileMenuOpen = !mobileMenuOpen" />
      <main class="app-main flex-1 min-w-0 px-3 py-4 sm:px-4 lg:px-6 lg:py-6">
        <slot />
      </main>
    </div>
  </div>
</template>

<style scoped>
.app-main {
  padding-bottom: max(1rem, env(safe-area-inset-bottom));
}
@media (max-width: 1023px) {
  .app-main {
    --content-gutter: 0.75rem;
    padding-left: max(var(--content-gutter), env(safe-area-inset-left));
    padding-right: max(var(--content-gutter), env(safe-area-inset-right));
  }
}
@media (min-width: 640px) and (max-width: 1023px) {
  .app-main { --content-gutter: 1rem; }
}
@media (max-width: 1023px) and (max-height: 500px) and (orientation: landscape) {
  .app-main { padding-top: 0.5rem; }
}
@media (min-width: 1024px) {
  .app-main { padding-bottom: 1.5rem; }
}
</style>
