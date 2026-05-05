<script setup lang="ts">
import { Hourglass, LogOut } from 'lucide-vue-next'

definePageMeta({ layout: false })

const auth = useAuthStore()
async function logout() {
  await auth.logout()
  await navigateTo('/login')
}
</script>

<template>
  <div class="min-h-screen flex items-center justify-center p-6 bg-background">
    <Card class="w-full max-w-md text-center">
      <CardHeader class="space-y-3 items-center">
        <div class="size-12 rounded-full bg-secondary text-secondary-foreground flex items-center justify-center">
          <Hourglass class="size-6" />
        </div>
        <CardTitle>Aguardando aprovação</CardTitle>
        <CardDescription>
          Sua conta foi criada e está pendente. Um administrador precisa liberar acesso.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <p class="text-xs text-muted-foreground">{{ auth.user?.email }}</p>
      </CardContent>
      <CardFooter class="justify-center">
        <Button variant="outline" size="sm" @click="logout">
          <LogOut class="size-4 mr-2" /> Sair
        </Button>
      </CardFooter>
    </Card>
  </div>
</template>
