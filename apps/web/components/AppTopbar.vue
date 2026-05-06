<script setup lang="ts">
import { Search, Bell, LogOut, Sun, Moon } from 'lucide-vue-next'

const auth = useAuthStore()
const colorMode = useState<'light' | 'dark'>('color-mode', () => 'light')

if (import.meta.client) {
  const stored = localStorage.getItem('theme') as 'light' | 'dark' | null
  if (stored) {
    colorMode.value = stored
    document.documentElement.classList.toggle('dark', stored === 'dark')
  }
}

function toggleTheme() {
  colorMode.value = colorMode.value === 'dark' ? 'light' : 'dark'
  if (import.meta.client) {
    localStorage.setItem('theme', colorMode.value)
    document.documentElement.classList.toggle('dark', colorMode.value === 'dark')
  }
}

async function logout() {
  await auth.logout()
  await navigateTo('/login')
}
</script>

<template>
  <header class="h-14 sticky top-0 z-30 bg-background/80 backdrop-blur border-b flex items-center gap-3 px-5">
    <div class="relative flex-1 max-w-md">
      <Search class="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
      <input
        type="search"
        placeholder="Buscar SKU, anúncio, conta…"
        class="w-full h-9 rounded-lg border bg-muted/40 pl-9 pr-12 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:bg-background"
      />
      <span class="absolute right-2 top-1/2 -translate-y-1/2 kbd">⌘K</span>
    </div>

    <div class="ml-auto flex items-center gap-1">
      <button
        type="button"
        class="rounded-lg h-9 w-9 grid place-items-center text-muted-foreground hover:text-foreground hover:bg-muted"
        @click="toggleTheme"
      >
        <Sun v-if="colorMode === 'dark'" class="size-[18px]" />
        <Moon v-else class="size-[18px]" />
      </button>

      <button
        type="button"
        class="relative rounded-lg h-9 w-9 grid place-items-center text-muted-foreground hover:text-foreground hover:bg-muted"
      >
        <Bell class="size-[18px]" />
        <span class="absolute top-1.5 right-1.5 size-1.5 rounded-full bg-primary" />
      </button>

      <div class="mx-2 h-6 w-px bg-border" />

      <div v-if="auth.user" class="flex items-center gap-2 pr-1">
        <div class="size-8 rounded-full bg-primary/10 text-primary grid place-items-center text-[12px] font-semibold">
          {{ (auth.user.name || auth.user.email)[0]?.toUpperCase() }}
        </div>
        <div class="hidden md:block leading-tight">
          <div class="text-[13px] font-medium">{{ auth.user.name || auth.user.email.split('@')[0] }}</div>
          <div class="text-[11px] text-muted-foreground">{{ auth.user.role }}</div>
        </div>
      </div>

      <Button v-if="auth.user" size="sm" variant="ghost" class="h-9" @click="logout">
        <LogOut class="size-4" />
      </Button>
    </div>
  </header>
</template>
