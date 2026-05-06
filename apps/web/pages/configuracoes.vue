<script setup lang="ts">
import { Settings, Bell, Lock, Building2, KeyRound, Webhook, Save, Copy } from 'lucide-vue-next'

definePageMeta({ middleware: ['auth'] })

type SettingsOut = {
  daily_sync_enabled: boolean
  daily_sync_time: string | null
  sync_interval_minutes: number | null
  low_stock_threshold: number | null
  notify_email: boolean
  notify_telegram: boolean
  notify_daily_sync: boolean
  telegram_chat_id: string | null
}

type WebhookUrl = { url: string; secret_hint: string; events: string[] }

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
const { api } = useApi()

const form = reactive<SettingsOut>({
  daily_sync_enabled: false,
  daily_sync_time: null,
  sync_interval_minutes: null,
  low_stock_threshold: null,
  notify_email: true,
  notify_telegram: false,
  notify_daily_sync: true,
  telegram_chat_id: null,
})
const saving = ref(false)
const message = ref<string | null>(null)
const error = ref<string | null>(null)
const webhook = ref<WebhookUrl | null>(null)

async function load() {
  try {
    const data = await api<SettingsOut>('/api/settings')
    Object.assign(form, data, {
      daily_sync_time: data.daily_sync_time ? data.daily_sync_time.slice(0, 5) : null,
    })
  } catch (e: any) {
    error.value = e?.data?.detail?.code || 'load_failed'
  }
}

async function loadWebhook() {
  try {
    webhook.value = await api<WebhookUrl>('/api/settings/webhook-url')
  } catch {
    webhook.value = null
  }
}

async function save() {
  saving.value = true
  error.value = null
  message.value = null
  try {
    const payload: Partial<SettingsOut> = { ...form }
    if (payload.daily_sync_time && payload.daily_sync_time.length === 5) {
      payload.daily_sync_time = `${payload.daily_sync_time}:00`
    }
    const data = await api<SettingsOut>('/api/settings', {
      method: 'PATCH',
      body: payload,
    })
    Object.assign(form, data, {
      daily_sync_time: data.daily_sync_time ? data.daily_sync_time.slice(0, 5) : null,
    })
    message.value = 'Salvo.'
    setTimeout(() => (message.value = null), 2500)
  } catch (e: any) {
    error.value = e?.data?.detail?.code || 'save_failed'
  } finally {
    saving.value = false
  }
}

function copyWebhook() {
  if (!webhook.value) return
  navigator.clipboard?.writeText(webhook.value.url)
  message.value = 'URL copiada.'
  setTimeout(() => (message.value = null), 2000)
}

onMounted(() => {
  load()
  loadWebhook()
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
        <div v-if="message" class="rounded-md border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-700 dark:bg-green-950/30 dark:border-green-900 dark:text-green-400">
          {{ message }}
        </div>
        <div v-if="error" class="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950/30 dark:border-red-900 dark:text-red-400">
          {{ error }}
        </div>

        <section v-if="active === 'geral'" class="rounded-xl border bg-card p-5 space-y-5">
          <div>
            <h2 class="font-semibold">Sincronização</h2>
            <p class="text-sm text-muted-foreground">Janelas de execução automática e thresholds globais.</p>
          </div>

          <div class="flex items-center justify-between border-b pb-4">
            <div>
              <div class="text-sm font-medium">Sync diário</div>
              <div class="text-xs text-muted-foreground">Roda <code>sync_all</code> uma vez por dia no horário escolhido.</div>
            </div>
            <input v-model="form.daily_sync_enabled" type="checkbox" class="size-4 accent-primary">
          </div>

          <div class="grid grid-cols-2 gap-3">
            <div>
              <Label>Horário do sync diário (BRT)</Label>
              <Input
                v-model="form.daily_sync_time"
                type="time"
                :disabled="!form.daily_sync_enabled"
              />
              <div class="text-[11px] text-muted-foreground mt-1">America/Sao_Paulo. Janela de tolerância: 5 min.</div>
            </div>
            <div>
              <Label>Intervalo de sync incremental (min)</Label>
              <Input
                v-model.number="form.sync_interval_minutes"
                type="number"
                min="5"
                max="1440"
                placeholder="—"
              />
              <div class="text-[11px] text-muted-foreground mt-1">Opcional. 5–1440.</div>
            </div>
            <div class="col-span-2">
              <Label>Threshold global de estoque baixo</Label>
              <Input
                v-model.number="form.low_stock_threshold"
                type="number"
                min="0"
                placeholder="—"
              />
              <div class="text-[11px] text-muted-foreground mt-1">
                Fallback quando o produto não tem <code>min_stock</code> próprio.
              </div>
            </div>
          </div>

          <div class="flex justify-end">
            <Button size="sm" :disabled="saving" @click="save">
              <Save class="size-4 mr-1.5" /> {{ saving ? 'salvando…' : 'salvar' }}
            </Button>
          </div>
        </section>

        <section v-if="active === 'notif'" class="rounded-xl border bg-card p-5 space-y-4">
          <div>
            <h2 class="font-semibold">Notificações</h2>
            <p class="text-sm text-muted-foreground">Escolha como receber alertas e relatórios.</p>
          </div>
          <ul class="divide-y">
            <li class="flex items-center py-3">
              <div>
                <div class="text-sm font-medium">E-mail</div>
                <div class="text-xs text-muted-foreground">resumos diários e alertas críticos</div>
              </div>
              <input v-model="form.notify_email" type="checkbox" class="ml-auto size-4 accent-primary">
            </li>
            <li class="flex items-center py-3">
              <div>
                <div class="text-sm font-medium">Telegram</div>
                <div class="text-xs text-muted-foreground">mensagem ao terminar o sync diário</div>
              </div>
              <input v-model="form.notify_telegram" type="checkbox" class="ml-auto size-4 accent-primary">
            </li>
            <li class="flex items-center py-3">
              <div>
                <div class="text-sm font-medium">Resumo do sync diário</div>
                <div class="text-xs text-muted-foreground">cria alerta <code>daily_sync_completed</code></div>
              </div>
              <input v-model="form.notify_daily_sync" type="checkbox" class="ml-auto size-4 accent-primary">
            </li>
          </ul>
          <div>
            <Label>Telegram chat_id pessoal (opcional)</Label>
            <Input
              v-model="form.telegram_chat_id"
              placeholder="ex.: -100123456789"
              :disabled="!form.notify_telegram"
            />
            <div class="text-[11px] text-muted-foreground mt-1">
              Vazio = usa <code>TELEGRAM_CHAT_ID</code> global do bot.
            </div>
          </div>
          <div class="flex justify-end">
            <Button size="sm" :disabled="saving" @click="save">
              <Save class="size-4 mr-1.5" /> {{ saving ? 'salvando…' : 'salvar' }}
            </Button>
          </div>
        </section>

        <section v-if="active === 'api'" class="rounded-xl border bg-card p-5 space-y-3">
          <h2 class="font-semibold">API e webhooks</h2>
          <p class="text-sm text-muted-foreground">URL para configurar o webhook do Bling.</p>
          <div v-if="webhook" class="space-y-3">
            <div class="rounded-md border bg-muted/40 p-3 font-mono text-xs break-all">
              {{ webhook.url }}
            </div>
            <div class="text-xs text-muted-foreground">
              Secret: <code>{{ webhook.secret_hint }}</code>
            </div>
            <div class="text-xs text-muted-foreground">
              Eventos: <code>{{ webhook.events.join(', ') }}</code>
            </div>
            <Button size="sm" variant="outline" @click="copyWebhook">
              <Copy class="size-4 mr-1.5" /> copiar URL
            </Button>
          </div>
          <div v-else class="text-sm text-muted-foreground">
            Sem permissão para visualizar webhook ou ainda carregando…
          </div>
        </section>

        <section v-if="active === 'seguranca'" class="rounded-xl border bg-card p-5 space-y-3">
          <h2 class="font-semibold">Segurança</h2>
          <p class="text-sm text-muted-foreground">Login por OTP. 2FA ainda não disponível.</p>
        </section>

        <section v-if="active === 'tokens'" class="rounded-xl border bg-card p-5 space-y-3">
          <h2 class="font-semibold">Tokens pessoais</h2>
          <EmptyState
            :icon="KeyRound"
            title="Nenhum token criado"
            description="Tokens pessoais para automações externas (em breve)."
          />
        </section>

        <section v-if="active === 'conta'" class="rounded-xl border bg-card p-5 space-y-3">
          <h2 class="font-semibold">Conta</h2>
          <div class="grid grid-cols-2 gap-3">
            <div>
              <Label>Nome</Label>
              <Input :value="auth.user?.name || ''" disabled />
            </div>
            <div>
              <Label>E-mail</Label>
              <Input :value="auth.user?.email || ''" disabled />
            </div>
          </div>
        </section>
      </div>
    </div>
  </div>
</template>
