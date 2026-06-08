<script setup lang="ts">
import { Mail, ShieldCheck, Loader2, Lock } from 'lucide-vue-next'

definePageMeta({ layout: false })

const auth = useAuthStore()
const route = useRoute()
const config = useRuntimeConfig()
const siteKey = (config.public as any)?.turnstile?.siteKey as string | undefined
const turnstileEnabled = computed(() => Boolean(siteKey))

// 'password' = login padrão (e-mail + senha).
// 'otp-email' / 'otp-code' = recuperação por código no e-mail ("Esqueci minha senha").
const step = ref<'password' | 'otp-email' | 'otp-code'>('password')
const email = ref('')
const password = ref('')
const code = ref('')
const turnstileToken = ref('')
const prefix = ref('')
const expiresAt = ref<Date | null>(null)
const error = ref<string | null>(null)
const loading = ref(false)
const resendCooldown = ref(0)

let cooldownTimer: ReturnType<typeof setInterval> | null = null

function startCooldown(s = 30) {
  resendCooldown.value = s
  if (cooldownTimer) clearInterval(cooldownTimer)
  cooldownTimer = setInterval(() => {
    resendCooldown.value -= 1
    if (resendCooldown.value <= 0 && cooldownTimer) {
      clearInterval(cooldownTimer)
      cooldownTimer = null
    }
  }, 1000)
}

function goToNext(requiresApproval: boolean) {
  const next = (route.query.next as string) || (requiresApproval ? '/pending-approval' : '/')
  return navigateTo(next)
}

async function submitPassword() {
  error.value = null
  loading.value = true
  try {
    const r = await auth.login(email.value.trim().toLowerCase(), password.value)
    await goToNext(r.requires_approval)
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    loading.value = false
  }
}

async function submitEmail() {
  error.value = null
  if (turnstileEnabled.value && !turnstileToken.value) {
    error.value = 'turnstile_required'
    return
  }
  loading.value = true
  try {
    const r = await auth.requestOtp(email.value.trim().toLowerCase(), turnstileToken.value || undefined)
    prefix.value = r.prefix
    expiresAt.value = new Date(r.expires_at)
    step.value = 'otp-code'
    startCooldown(30)
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    loading.value = false
  }
}

async function submitCode() {
  error.value = null
  loading.value = true
  try {
    const r = await auth.verifyOtp(email.value.trim().toLowerCase(), code.value.trim().toUpperCase())
    await goToNext(r.requires_approval)
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    loading.value = false
  }
}

async function resend() {
  if (resendCooldown.value > 0) return
  error.value = null
  try {
    const r = await auth.resendOtp(email.value.trim().toLowerCase())
    prefix.value = r.prefix
    expiresAt.value = new Date(r.expires_at)
    startCooldown(30)
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  }
}

function useOtp() {
  error.value = null
  step.value = 'otp-email'
}

function backToPassword() {
  error.value = null
  code.value = ''
  step.value = 'password'
}

const now = useNow({ interval: 1000 })
const remaining = computed(() => {
  if (!expiresAt.value) return ''
  const ms = expiresAt.value.getTime() - now.value.getTime()
  if (ms <= 0) return 'expirado'
  const m = Math.floor(ms / 60000)
  const s = Math.floor((ms % 60000) / 1000)
  return `${m}m${s.toString().padStart(2, '0')}`
})

const errorLabel = computed(() => {
  const map: Record<string, string> = {
    invalid_credentials: 'E-mail ou senha incorretos.',
    turnstile_required: 'Confirme que você não é um robô.',
    turnstile_failed: 'Verificação anti-bot falhou. Recarregue.',
    invalid_email: 'E-mail inválido.',
    rate_limited: 'Muitas tentativas. Aguarde antes de tentar novamente.',
    code_invalid: 'Código incorreto.',
    code_not_found: 'Código não encontrado ou expirado. Peça um novo.',
    nonce_mismatch: 'Sessão divergente. Reabra o login no mesmo navegador.',
    too_many_attempts: 'Tentativas excedidas. Peça um novo código.',
    suspended: 'Conta suspensa. Procure o administrador.',
  }
  return error.value ? map[error.value] || error.value : null
})
</script>

<template>
  <div class="min-h-screen flex items-center justify-center p-6 bg-background">
    <Card class="w-full max-w-md">
      <CardHeader class="space-y-1">
        <div class="flex items-center gap-2 mb-2">
          <div class="size-9 rounded-md bg-primary text-primary-foreground flex items-center justify-center">
            <Mail class="size-5" />
          </div>
          <span class="text-lg font-semibold">DaVinci</span>
        </div>
        <CardTitle v-if="step === 'password'" class="text-xl">Entrar</CardTitle>
        <CardTitle v-else-if="step === 'otp-email'" class="text-xl">Entrar por código</CardTitle>
        <CardTitle v-else class="text-xl">Confirme o código</CardTitle>
        <CardDescription>
          <template v-if="step === 'password'">Acesse com seu e-mail e senha.</template>
          <template v-else-if="step === 'otp-email'">Enviamos um código de acesso ao seu e-mail.</template>
          <template v-else>Olhe sua caixa de entrada.</template>
        </CardDescription>
      </CardHeader>

      <CardContent>
        <!-- Login por senha (padrão) -->
        <form v-if="step === 'password'" class="space-y-4" @submit.prevent="submitPassword">
          <div class="space-y-2">
            <Label for="email">E-mail</Label>
            <Input
              id="email"
              v-model="email"
              type="email"
              required
              autofocus
              autocomplete="username"
              placeholder="voce@empresa.com"
            />
          </div>
          <div class="space-y-2">
            <Label for="password">Senha</Label>
            <Input
              id="password"
              v-model="password"
              type="password"
              required
              autocomplete="current-password"
              placeholder="••••••••"
            />
          </div>
          <Button type="submit" class="w-full" :disabled="loading">
            <Loader2 v-if="loading" class="size-4 mr-2 animate-spin" />
            Entrar
          </Button>
          <p class="text-center text-xs text-muted-foreground">
            Esqueceu a senha? Procure um administrador para redefinir.
          </p>
          <div class="text-center">
            <button
              type="button"
              class="text-[11px] text-muted-foreground/60 hover:text-foreground"
              @click="useOtp"
            >
              Acesso por código (administradores)
            </button>
          </div>
        </form>

        <!-- Recuperação: pedir código por e-mail -->
        <form v-else-if="step === 'otp-email'" class="space-y-4" @submit.prevent="submitEmail">
          <div class="space-y-2">
            <Label for="otp-email">E-mail</Label>
            <Input
              id="otp-email"
              v-model="email"
              type="email"
              required
              autofocus
              placeholder="voce@empresa.com"
            />
          </div>
          <ClientOnly v-if="turnstileEnabled">
            <NuxtTurnstile v-model="turnstileToken" />
          </ClientOnly>
          <Button type="submit" class="w-full" :disabled="loading">
            <Loader2 v-if="loading" class="size-4 mr-2 animate-spin" />
            Enviar código
          </Button>
          <div class="text-center">
            <button
              type="button"
              class="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
              @click="backToPassword"
            >
              <Lock class="size-3" /> Voltar ao login com senha
            </button>
          </div>
        </form>

        <!-- Recuperação: confirmar código -->
        <form v-else class="space-y-4" @submit.prevent="submitCode">
          <div class="rounded-md border border-primary/40 bg-primary/5 p-3">
            <div class="flex items-center gap-2 text-xs uppercase tracking-wide text-muted-foreground">
              <ShieldCheck class="size-3.5" /> Anti-phishing — verifique o prefixo
            </div>
            <div class="font-mono text-2xl tracking-[0.3em] mt-1 text-primary">{{ prefix }}</div>
            <p class="text-xs text-muted-foreground mt-2">
              Confirme que o e-mail recebido começa com <strong>{{ prefix }}</strong>.
              Se não bater, NÃO digite o código.
            </p>
          </div>

          <div class="space-y-2">
            <Label for="code">Código</Label>
            <Input
              id="code"
              v-model="code"
              type="text"
              required
              autofocus
              maxlength="8"
              autocomplete="one-time-code"
              class="font-mono uppercase tracking-[0.25em] text-center text-base"
              @input="(e: any) => (code = e.target.value.toUpperCase())"
            />
            <p class="text-xs text-muted-foreground">expira em {{ remaining }}</p>
          </div>

          <Button type="submit" class="w-full" :disabled="loading">
            <Loader2 v-if="loading" class="size-4 mr-2 animate-spin" />
            Entrar
          </Button>

          <div class="flex justify-between text-xs">
            <button
              type="button"
              class="text-muted-foreground hover:text-foreground"
              @click="step = 'otp-email'"
            >
              ← Trocar e-mail
            </button>
            <button
              type="button"
              :disabled="resendCooldown > 0"
              class="text-muted-foreground hover:text-foreground disabled:opacity-50"
              @click="resend"
            >
              {{ resendCooldown > 0 ? `Reenviar em ${resendCooldown}s` : 'Reenviar código' }}
            </button>
          </div>
        </form>

        <p v-if="errorLabel" class="mt-4 text-sm text-destructive">{{ errorLabel }}</p>
      </CardContent>
    </Card>
  </div>
</template>
