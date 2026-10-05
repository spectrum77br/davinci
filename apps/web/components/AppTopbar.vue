<script setup lang="ts">
import { Bell, LogOut, Sun, Moon, Menu } from 'lucide-vue-next'

defineProps<{ mobileMenuOpen?: boolean }>()
const emit = defineEmits<{ (e: 'toggle-menu'): void }>()

const auth = useAuthStore()
const colorMode = useState<'light' | 'dark'>('color-mode', () => 'light')
const { unread, refreshUnreadCount } = useAlerts()

if (import.meta.client && auth.user) {
  refreshUnreadCount()
  const id = setInterval(refreshUnreadCount, 60_000)
  onScopeDispose(() => clearInterval(id))
}

onMounted(() => {
  // Restore after hydration so the server and initial client render agree.
  try {
    const stored = localStorage.getItem('theme')
    if (stored === 'light' || stored === 'dark') {
      colorMode.value = stored
      document.documentElement.classList.toggle('dark', stored === 'dark')
    }
  } catch {
    // Theme controls remain usable if the browser blocks local storage.
  }
})

function toggleTheme() {
  colorMode.value = colorMode.value === 'dark' ? 'light' : 'dark'
  if (import.meta.client) {
    try { localStorage.setItem('theme', colorMode.value) } catch { /* visit-only preference */ }
    document.documentElement.classList.toggle('dark', colorMode.value === 'dark')
  }
}

async function logout() {
  await auth.logout()
  await navigateTo('/login')
}
</script>

<template>
  <header class="app-topbar min-h-14 sticky top-0 z-30 bg-background/95 lg:bg-background/80 backdrop-blur border-b flex items-center gap-1 px-2 lg:gap-3 lg:px-5">
    <button
      type="button"
      class="size-11 shrink-0 grid place-items-center rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted lg:hidden"
      aria-label="Abrir menu"
      aria-controls="davinci-navigation"
      :aria-expanded="!!mobileMenuOpen"
      @click="emit('toggle-menu')"
    >
      <Menu class="size-5" />
    </button>
    <NuxtLink to="/" class="flex min-w-0 items-center gap-1.5 font-bold text-sm lg:hidden" aria-label="DaVinci — início">
      <LogoMark class="size-6 shrink-0" />
      <span>DaVinci</span>
    </NuxtLink>
    <div class="ml-auto flex items-center gap-0 lg:gap-1">
      <button
        type="button"
        class="rounded-lg h-11 w-11 lg:h-9 lg:w-9 grid place-items-center text-muted-foreground hover:text-foreground hover:bg-muted"
        :aria-label="colorMode === 'dark' ? 'Ativar tema claro' : 'Ativar tema escuro'"
        @click="toggleTheme"
      >
        <Sun v-if="colorMode === 'dark'" class="size-[18px]" />
        <Moon v-else class="size-[18px]" />
      </button>

      <NuxtLink
        to="/alertas"
        class="relative rounded-lg h-11 w-11 lg:h-9 lg:w-9 grid place-items-center text-muted-foreground hover:text-foreground hover:bg-muted"
        :aria-label="unread > 0 ? `Alertas: ${unread} não lidos` : 'Alertas'"
        :title="unread > 0 ? `${unread} não lidos` : 'Alertas'"
      >
        <Bell class="size-[18px]" />
        <span
          v-if="unread > 0"
          class="absolute -top-0.5 -right-0.5 min-w-[16px] h-4 px-1 rounded-full bg-primary text-primary-foreground text-[10px] font-semibold grid place-items-center"
        >{{ unread > 99 ? '99+' : unread }}</span>
      </NuxtLink>

      <div class="hidden lg:block mx-2 h-6 w-px bg-border" />

      <div v-if="auth.user" class="hidden sm:flex items-center gap-2 pr-1">
        <div class="size-8 rounded-full bg-primary/10 text-primary grid place-items-center text-[12px] font-semibold">
          {{ (auth.user.name || auth.user.email)[0]?.toUpperCase() }}
        </div>
        <div class="hidden lg:block leading-tight">
          <div class="text-[13px] font-medium">{{ auth.user.name || auth.user.email.split('@')[0] }}</div>
          <div class="text-[11px] text-muted-foreground">{{ auth.user.role }}</div>
        </div>
      </div>

      <Button v-if="auth.user" size="sm" variant="ghost" class="h-11 w-11 p-0 lg:h-9 lg:w-auto lg:px-3" aria-label="Sair da conta" @click="logout">
        <LogOut class="size-4" />
      </Button>
    </div>
  </header>
</template>

<style scoped>
.app-topbar {
  padding-top: env(safe-area-inset-top);
}
@media (max-width: 1023px) {
  .app-topbar {
    padding-left: max(0.5rem, env(safe-area-inset-left));
    padding-right: max(0.5rem, env(safe-area-inset-right));
  }
}
@media (max-width: 1023px) and (max-height: 500px) and (orientation: landscape) {
  .app-topbar { min-height: calc(3rem + env(safe-area-inset-top)); }
}
</style>
