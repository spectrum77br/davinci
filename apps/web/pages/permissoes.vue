<script setup lang="ts">
import { ShieldCheck, Users as UsersIcon, ArrowRight } from 'lucide-vue-next'
import { RESOURCES, RESOURCE_LABELS } from '~/composables/useCan'

definePageMeta({ middleware: ['admin'] })

type RoleSummary = { role: string; count: number; tone: string }

const roles: RoleSummary[] = [
  { role: 'admin', count: 2,  tone: 'pill-danger' },
  { role: 'user',  count: 14, tone: 'pill-info' },
]
</script>

<template>
  <div class="space-y-5">
    <PageHeader title="Permissões" description="Visão geral da matriz por recurso. Edite por usuário em Usuários.">
      <template #actions>
        <NuxtLink to="/users">
          <Button size="sm">
            <UsersIcon class="size-4 mr-1.5" /> ir para usuários
          </Button>
        </NuxtLink>
      </template>
    </PageHeader>

    <div class="grid lg:grid-cols-3 gap-4">
      <div v-for="r in roles" :key="r.role" class="rounded-xl border bg-card p-5">
        <div class="flex items-center gap-2 mb-2">
          <ShieldCheck class="size-4 text-muted-foreground" />
          <span class="text-xs uppercase tracking-wider font-medium text-muted-foreground">role</span>
          <span :class="r.tone" class="ml-auto capitalize">{{ r.role }}</span>
        </div>
        <div class="text-3xl font-semibold tabular-nums">{{ r.count }}</div>
        <div class="text-xs text-muted-foreground mt-1">usuários ativos</div>
      </div>

      <div class="rounded-xl border bg-card p-5">
        <div class="text-xs uppercase tracking-wider font-medium text-muted-foreground mb-2">cascade</div>
        <p class="text-sm">
          <code class="kbd">delete</code> liga <code class="kbd">edit</code> e <code class="kbd">view</code>.
          <code class="kbd">edit</code> liga <code class="kbd">view</code>. Admin tem bypass total.
        </p>
      </div>
    </div>

    <div class="table-card">
      <table class="w-full">
        <thead>
          <tr>
            <th>Recurso</th>
            <th class="text-center">view</th>
            <th class="text-center">edit</th>
            <th class="text-center">delete</th>
            <th class="text-right">Ações</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in RESOURCES" :key="r">
            <td class="font-medium">{{ RESOURCE_LABELS[r] }}</td>
            <td class="text-center text-muted-foreground tabular-nums">14 / 16</td>
            <td class="text-center text-muted-foreground tabular-nums">8 / 16</td>
            <td class="text-center text-muted-foreground tabular-nums">2 / 16</td>
            <td class="text-right">
              <NuxtLink to="/users" class="text-xs text-primary inline-flex items-center hover:underline">
                editar usuários <ArrowRight class="size-3 ml-1" />
              </NuxtLink>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
