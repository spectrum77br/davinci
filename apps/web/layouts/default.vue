<script setup lang="ts">
import { LogOut } from 'lucide-vue-next'

const auth = useAuthStore()
async function logout() {
  await auth.logout()
  await navigateTo('/login')
}
</script>

<template>
  <div class="min-h-screen bg-background text-foreground">
    <header class="border-b">
      <div class="flex items-center gap-6 px-6 h-14">
        <strong class="text-base font-semibold">DaVinci</strong>
        <nav class="flex gap-4 text-sm text-muted-foreground">
          <NuxtLink to="/" class="hover:text-foreground transition-colors">Dashboard</NuxtLink>
          <NuxtLink v-if="auth.isAdmin" to="/users" class="hover:text-foreground transition-colors">Usuários</NuxtLink>
        </nav>
        <div class="ml-auto flex items-center gap-3 text-sm">
          <span v-if="auth.user" class="text-muted-foreground">{{ auth.user.email }}</span>
          <span v-if="auth.user" class="text-xs px-2 py-0.5 rounded border">
            {{ auth.user.role }}
          </span>
          <Button v-if="auth.user" size="sm" variant="ghost" @click="logout">
            <LogOut class="size-4 mr-1" /> sair
          </Button>
        </div>
      </div>
    </header>
    <main class="p-6">
      <slot />
    </main>
  </div>
</template>
