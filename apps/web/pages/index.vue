<script setup lang="ts">
const config = useRuntimeConfig()
const { data: health } = await useFetch<{ status: string; postgres: string; redis: string }>(
  `${config.public.apiUrl}/api/health`,
  { server: false, default: () => ({ status: 'unknown', postgres: '?', redis: '?' }) },
)
</script>

<template>
  <section class="space-y-6">
    <div>
      <h1 class="text-2xl font-semibold">Dashboard</h1>
      <p class="text-sm text-muted-foreground">Visão geral do sistema.</p>
    </div>

    <Card class="max-w-md">
      <CardHeader>
        <CardTitle class="text-base">API health</CardTitle>
      </CardHeader>
      <CardContent class="font-mono text-sm space-y-1">
        <div>
          status:
          <span :class="health?.status === 'ok' ? 'text-green-500' : 'text-destructive'">
            {{ health?.status }}
          </span>
        </div>
        <div>postgres: {{ health?.postgres }}</div>
        <div>redis: {{ health?.redis }}</div>
      </CardContent>
    </Card>
  </section>
</template>
