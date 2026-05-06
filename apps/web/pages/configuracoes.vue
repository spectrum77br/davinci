<script setup lang="ts">
import { Settings, Bell, Lock, Building2, KeyRound, Webhook, Save } from 'lucide-vue-next'

const sections = [
  { key: 'geral',     label: 'Geral',          icon: Settings },
  { key: 'conta',     label: 'Conta',          icon: Building2 },
  { key: 'notif',     label: 'Notificações',   icon: Bell },
  { key: 'seguranca', label: 'Segurança',      icon: Lock },
  { key: 'api',       label: 'API e webhooks', icon: Webhook },
  { key: 'tokens',    label: 'Tokens pessoais',icon: KeyRound },
] as const

const active = ref<typeof sections[number]['key']>('geral')

const auth = useAuthStore()
const form = reactive({
  nome: auth.user?.name || '',
  email: auth.user?.email || '',
  fuso: 'America/Sao_Paulo',
  idioma: 'pt-BR',
  notifEmail: true,
  notifThreema: true,
  notifPush: false,
  twoFactor: false,
})
</script>

<template>
  <div class="space-y-5">
    <PageHeader title="Configurações" description="Preferências da conta, notificações e integrações." />

    <div class="grid lg:grid-cols-[220px_1fr] gap-6">
      <nav class="space-y-1">
        <button
          v-for="s in sections" :key="s.key"
          class="w-full flex items-center gap-2.5 rounded-lg px-3 h-9 text-sm font-medium transition-colors"
          :class="active === s.key ? 'bg-primary/10 text-primary' : 'text-muted-foreground hover:bg-muted hover:text-foreground'"
          @click="active = s.key"
        >
          <component :is="s.icon" class="size-4" />
          {{ s.label }}
        </button>
      </nav>

      <div class="space-y-6 max-w-2xl">
        <section v-if="active === 'geral'" class="rounded-xl border bg-card p-5 space-y-4">
          <h2 class="font-semibold">Geral</h2>
          <div class="grid grid-cols-2 gap-3">
            <div>
              <Label>Nome</Label>
              <Input v-model="form.nome" />
            </div>
            <div>
              <Label>E-mail</Label>
              <Input v-model="form.email" disabled />
            </div>
            <div>
              <Label>Fuso horário</Label>
              <select v-model="form.fuso" class="w-full h-9 rounded-md border bg-background px-3 text-sm">
                <option>America/Sao_Paulo</option>
                <option>UTC</option>
              </select>
            </div>
            <div>
              <Label>Idioma</Label>
              <select v-model="form.idioma" class="w-full h-9 rounded-md border bg-background px-3 text-sm">
                <option>pt-BR</option>
                <option>en-US</option>
              </select>
            </div>
          </div>
          <div class="flex justify-end">
            <Button size="sm"><Save class="size-4 mr-1.5" /> salvar</Button>
          </div>
        </section>

        <section v-if="active === 'notif'" class="rounded-xl border bg-card p-5 space-y-4">
          <h2 class="font-semibold">Notificações</h2>
          <p class="text-sm text-muted-foreground">Escolha quais alertas e relatórios receber.</p>
          <ul class="divide-y">
            <li class="flex items-center py-3">
              <div>
                <div class="text-sm font-medium">E-mail</div>
                <div class="text-xs text-muted-foreground">resumos diários e alertas críticos</div>
              </div>
              <input v-model="form.notifEmail" type="checkbox" class="ml-auto size-4 accent-primary">
            </li>
            <li class="flex items-center py-3">
              <div>
                <div class="text-sm font-medium">Threema</div>
                <div class="text-xs text-muted-foreground">relatórios diários (faturamento, saldo Bling)</div>
              </div>
              <input v-model="form.notifThreema" type="checkbox" class="ml-auto size-4 accent-primary">
            </li>
            <li class="flex items-center py-3">
              <div>
                <div class="text-sm font-medium">Push (browser)</div>
                <div class="text-xs text-muted-foreground">alertas em tempo real durante navegação</div>
              </div>
              <input v-model="form.notifPush" type="checkbox" class="ml-auto size-4 accent-primary">
            </li>
          </ul>
        </section>

        <section v-if="active === 'seguranca'" class="rounded-xl border bg-card p-5 space-y-4">
          <h2 class="font-semibold">Segurança</h2>
          <ul class="divide-y">
            <li class="flex items-center py-3">
              <div>
                <div class="text-sm font-medium">Autenticação em 2 fatores</div>
                <div class="text-xs text-muted-foreground">TOTP via app autenticador</div>
              </div>
              <input v-model="form.twoFactor" type="checkbox" class="ml-auto size-4 accent-primary">
            </li>
            <li class="flex items-center py-3">
              <div>
                <div class="text-sm font-medium">Sessões ativas</div>
                <div class="text-xs text-muted-foreground">2 dispositivos conectados</div>
              </div>
              <Button size="sm" variant="outline" class="ml-auto">gerenciar</Button>
            </li>
            <li class="flex items-center py-3">
              <div>
                <div class="text-sm font-medium">Encerrar todas sessões</div>
                <div class="text-xs text-muted-foreground">desconecta de todos dispositivos</div>
              </div>
              <Button size="sm" variant="outline" class="ml-auto text-red-600 hover:text-red-700">encerrar</Button>
            </li>
          </ul>
        </section>

        <section v-if="active === 'api'" class="rounded-xl border bg-card p-5 space-y-3">
          <h2 class="font-semibold">API e webhooks</h2>
          <p class="text-sm text-muted-foreground">Endpoints expostos para automações externas.</p>
          <code class="block rounded-md border bg-muted/40 p-3 font-mono text-xs">
            GET https://api.davinci.app/v1/orders<br>
            Authorization: Bearer &lt;TOKEN&gt;
          </code>
          <Button size="sm" variant="outline">configurar webhook</Button>
        </section>

        <section v-if="active === 'tokens'" class="rounded-xl border bg-card p-5 space-y-3">
          <h2 class="font-semibold">Tokens pessoais</h2>
          <EmptyState
            :icon="KeyRound"
            title="Nenhum token criado"
            description="Crie um token para integrar scripts ou ferramentas externas com o DaVinci."
          >
            <Button size="sm">gerar token</Button>
          </EmptyState>
        </section>

        <section v-if="active === 'conta'" class="rounded-xl border bg-card p-5 space-y-3">
          <h2 class="font-semibold">Conta</h2>
          <p class="text-sm text-muted-foreground">Dados da organização e plano.</p>
          <div class="rounded-lg border p-4 flex items-center">
            <div>
              <div class="text-sm font-medium">DaVinci — plano Pro</div>
              <div class="text-xs text-muted-foreground">renovação em 30/05/2026</div>
            </div>
            <Button size="sm" variant="outline" class="ml-auto">gerenciar plano</Button>
          </div>
        </section>
      </div>
    </div>
  </div>
</template>
