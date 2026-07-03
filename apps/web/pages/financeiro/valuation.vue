<script setup lang="ts">
// Valuation — duas abas:
//   1) Resumo: relatório de faturamento dos últimos 3 meses (porta web do
//      antigo PDF diário). Consulta ao vivo davinci.bling_orders / valuation
//      / stores (atualizadas pela rotina das 5h).
//   2) Estoque Bling: último snapshot diário do estoque por local
//      (PI/SA/SP/RA/CD/CI/US/Eletro/Mala/Outros). Gravado pelo cron arq
//      `valuation_estoque_snapshot` (~08h BRT) em valuation_estoque_bling_diario.
import { computed, onMounted, ref } from 'vue'
import { Info, Loader2, Lock, RefreshCw } from 'lucide-vue-next'

definePageMeta({
  middleware: ['admin'],
})

const { api } = useApi()

type ValuationMes = {
  mes: string
  caixa: number | null
  receber: number | null
  estoque: number | null
  total: number | null
  rentabilidade: number | null
  data_snapshot: string | null
}
type OperacionalLinha = {
  chave: string
  label: string
  formato: 'brl' | 'pct'
  descricao: string
  valores: (number | null)[]
}
type OperacionalSecao = {
  meses: string[]
  linhas: OperacionalLinha[]
}
type Report = {
  gerado_em: string
  situacoes_label: string
  valuation_meses: ValuationMes[]
  operacional: OperacionalSecao
}
type EstoqueLocal = { local: string; qtd: number; valor: number }
type EstoqueSnapshot = {
  data: string
  updated_at: string
  total_qtd: number
  total_valor: number
  locais: EstoqueLocal[]
}
type SaldoCelula = { disponivel: number | null; a_receber: number | null; nota?: string | null }
type SaldoLoja = {
  loja: string
  saldos: Record<string, SaldoCelula>
  total_a_receber: number
}
type SaldoSnapshot = {
  data: string
  updated_at: string
  marketplaces: string[]
  lojas: SaldoLoja[]
  total_a_receber: number
  total_disponivel: number
}

type Tab = 'resumo' | 'estoque' | 'saldo'
const tab = ref<Tab>('resumo')

// ── Senha extra (camada acima do require_permission) ─────────────────────
// Token vem do POST /unlock e é guardado em sessionStorage (some ao fechar
// a aba). Backend valida HMAC + age <= 15min. Enviado em X-Valuation-Token
// em todas as chamadas dos endpoints /api/financeiro/valuation*.
const UNLOCK_KEY = 'davinci.valuation.token'
const UNLOCK_EXP_KEY = 'davinci.valuation.token_exp'
const unlockToken = ref<string | null>(null)
const passwordInput = ref('')
const unlockError = ref<string | null>(null)
const unlocking = ref(false)
const unlockInputRef = ref<HTMLInputElement | null>(null)

function readStoredToken(): string | null {
  if (import.meta.server) return null
  const tok = sessionStorage.getItem(UNLOCK_KEY)
  const exp = Number(sessionStorage.getItem(UNLOCK_EXP_KEY) || 0)
  if (!tok || !exp || Date.now() / 1000 > exp) {
    sessionStorage.removeItem(UNLOCK_KEY)
    sessionStorage.removeItem(UNLOCK_EXP_KEY)
    return null
  }
  return tok
}

function valHeaders(): Record<string, string> {
  return unlockToken.value ? { 'X-Valuation-Token': unlockToken.value } : {}
}

async function submitUnlock() {
  if (!passwordInput.value) return
  unlocking.value = true
  unlockError.value = null
  try {
    const r = await api<{ token: string; expires_in: number }>(
      '/api/financeiro/valuation/unlock',
      { method: 'POST', body: { password: passwordInput.value } },
    )
    unlockToken.value = r.token
    sessionStorage.setItem(UNLOCK_KEY, r.token)
    sessionStorage.setItem(
      UNLOCK_EXP_KEY,
      String(Math.floor(Date.now() / 1000) + r.expires_in - 30),
    )
    passwordInput.value = ''
    void loadResumo() // já carrega a aba atual
  } catch (e: any) {
    unlockError.value = e?.data?.detail?.code === 'wrong_password'
      ? 'Senha incorreta.'
      : (e?.data?.detail?.code || e?.message || 'erro')
  } finally {
    unlocking.value = false
  }
}

function lockOut() {
  sessionStorage.removeItem(UNLOCK_KEY)
  sessionStorage.removeItem(UNLOCK_EXP_KEY)
  unlockToken.value = null
  resumo.value = null
  estoque.value = null
  saldo.value = null
}

// Loading/erro por aba — cada uma carrega na primeira ativação (lazy).
const loadingResumo = ref(false)
const loadingEstoque = ref(false)
const loadingSaldo = ref(false)
const errorResumo = ref<string | null>(null)
const errorEstoque = ref<string | null>(null)
const errorSaldo = ref<string | null>(null)
const resumo = ref<Report | null>(null)
const estoque = ref<EstoqueSnapshot | null>(null)
const saldo = ref<SaldoSnapshot | null>(null)

async function loadResumo() {
  if (!unlockToken.value) return
  loadingResumo.value = true
  errorResumo.value = null
  try {
    resumo.value = await api<Report>('/api/financeiro/valuation', {
      headers: valHeaders(),
    })
  } catch (e: any) {
    const code = e?.data?.detail?.code
    if (code === 'valuation_locked') {
      lockOut()
    } else {
      errorResumo.value = code || e?.message || 'erro'
    }
  } finally {
    loadingResumo.value = false
  }
}

async function loadEstoque() {
  if (!unlockToken.value) return
  loadingEstoque.value = true
  errorEstoque.value = null
  try {
    estoque.value = await api<EstoqueSnapshot>(
      '/api/financeiro/valuation/estoque-bling',
      { headers: valHeaders() },
    )
  } catch (e: any) {
    const code = e?.data?.detail?.code
    if (code === 'valuation_locked') {
      lockOut()
    } else {
      errorEstoque.value = code || e?.message || 'erro'
    }
  } finally {
    loadingEstoque.value = false
  }
}

async function loadSaldo() {
  if (!unlockToken.value) return
  loadingSaldo.value = true
  errorSaldo.value = null
  try {
    saldo.value = await api<SaldoSnapshot>(
      '/api/financeiro/valuation/saldo-marketplace',
      { headers: valHeaders() },
    )
  } catch (e: any) {
    const code = e?.data?.detail?.code
    if (code === 'valuation_locked') {
      lockOut()
    } else {
      errorSaldo.value = code || e?.message || 'erro'
    }
  } finally {
    loadingSaldo.value = false
  }
}

function setTab(t: Tab) {
  tab.value = t
  if (!unlockToken.value) return
  if (t === 'resumo' && !resumo.value && !loadingResumo.value) void loadResumo()
  if (t === 'estoque' && !estoque.value && !loadingEstoque.value) void loadEstoque()
  if (t === 'saldo' && !saldo.value && !loadingSaldo.value) void loadSaldo()
}

function reload() {
  if (!unlockToken.value) return
  if (tab.value === 'resumo') void loadResumo()
  else if (tab.value === 'estoque') void loadEstoque()
  else void loadSaldo()
}

// Carga inicial só roda client-side, após restaurar token (sessionStorage).
onMounted(() => {
  unlockToken.value = readStoredToken()
  if (unlockToken.value) {
    void loadResumo()
  } else {
    // Foca o campo de senha pra começar digitando direto.
    setTimeout(() => unlockInputRef.value?.focus(), 60)
  }
})

function fmtBRL(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '—'
  return n.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
}
function fmtInt(n: number | null | undefined): string {
  if (n == null) return '—'
  return Number(n).toLocaleString('pt-BR')
}
function fmtPct(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '—'
  return `${n.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%`
}
function mesLabel(iso: string): string {
  const [y, m] = iso.split('-').map(Number)
  return new Date(y, m - 1, 1).toLocaleDateString('pt-BR', { month: 'short', year: 'numeric' })
}
function diaCurto(iso: string | null): string {
  if (!iso) return ''
  const [y, m, d] = iso.split('-').map(Number)
  return `${String(d).padStart(2, '0')}/${String(m).padStart(2, '0')}`
}
function diaLabel(iso: string | null): string {
  if (!iso) return ''
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(y, m - 1, d).toLocaleDateString('pt-BR')
}

const valMeses = computed(() => resumo.value?.valuation_meses ?? [])
const geradoEm = computed(() =>
  resumo.value ? new Date(resumo.value.gerado_em).toLocaleString('pt-BR') : '',
)
const estoqueUpdatedEm = computed(() =>
  estoque.value ? new Date(estoque.value.updated_at).toLocaleString('pt-BR') : '',
)
const saldoUpdatedEm = computed(() =>
  saldo.value ? new Date(saldo.value.updated_at).toLocaleString('pt-BR') : '',
)
const mktLabel: Record<string, string> = {
  ml: 'Mercado Livre',
  shopee: 'Shopee',
  amazon: 'Amazon',
  magalu: 'Magalu',
  tiktok: 'TikTok',
  shein: 'Shein',
  temu: 'Temu',
  aliexpress: 'AliExpress',
}
function mktTitle(m: string): string {
  return mktLabel[m] || m.charAt(0).toUpperCase() + m.slice(1)
}
// Totais por coluna de marketplace (rodapé da planilha).
function colTotalReceber(m: string): number {
  return (saldo.value?.lojas ?? []).reduce((s, l) => s + (l.saldos[m]?.a_receber ?? 0), 0)
}
function colTotalDisp(m: string): number {
  return (saldo.value?.lojas ?? []).reduce((s, l) => s + (l.saldos[m]?.disponivel ?? 0), 0)
}
const loading = computed(() =>
  tab.value === 'resumo'
    ? loadingResumo.value
    : tab.value === 'estoque'
      ? loadingEstoque.value
      : loadingSaldo.value,
)
</script>

<template>
  <!-- ─────────────────────────────────── Gate de senha ─────────────────── -->
  <div
    v-if="!unlockToken"
    class="flex items-center justify-center min-h-[60vh] p-4"
  >
    <form
      class="w-full max-w-sm space-y-4 border rounded-lg p-6 bg-card shadow-sm"
      @submit.prevent="submitUnlock"
    >
      <div class="flex items-center gap-2">
        <Lock class="size-5 text-muted-foreground" />
        <h1 class="text-lg font-semibold">Valuation</h1>
      </div>
      <p class="text-xs text-muted-foreground">
        Esta página exige uma senha adicional. O acesso fica liberado por 15 minutos nesta aba.
      </p>
      <div class="space-y-1">
        <label for="valuation-pwd" class="block text-xs font-medium">Senha</label>
        <input
          id="valuation-pwd"
          ref="unlockInputRef"
          v-model="passwordInput"
          type="password"
          autocomplete="off"
          class="w-full h-9 border rounded px-2 bg-background text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
          :disabled="unlocking"
        />
      </div>
      <p v-if="unlockError" class="text-xs text-destructive">{{ unlockError }}</p>
      <button
        type="submit"
        class="w-full h-9 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:opacity-90 disabled:opacity-50"
        :disabled="unlocking || !passwordInput"
      >
        <Loader2 v-if="unlocking" class="inline h-4 w-4 animate-spin mr-1" />
        Desbloquear
      </button>
    </form>
  </div>

  <!-- ─────────────────────────────────── Conteúdo desbloqueado ─────────── -->
  <div v-else class="space-y-4 p-4">
    <div class="flex flex-wrap items-center gap-2">
      <h1 class="text-xl font-semibold">Valuation</h1>
      <span class="text-xs text-muted-foreground ml-2">
        {{ tab === 'resumo'
          ? 'Faturamento, custo, rentabilidade e margem dos últimos 3 meses.'
          : tab === 'estoque'
            ? 'Estoque Bling por local — snapshot diário.'
            : 'Saldo das contas por marketplace — snapshot diário.' }}
      </span>
      <button
        class="ml-auto inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs hover:bg-muted"
        title="Bloquear novamente (esquece o token desta aba)"
        @click="lockOut"
      >
        <Lock class="size-3.5" /> Bloquear
      </button>
      <button
        class="inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
        :disabled="loading"
        @click="reload"
      >
        <RefreshCw class="size-3.5" :class="{ 'animate-spin': loading }" /> Recarregar
      </button>
    </div>

    <!-- Tabs -->
    <div class="inline-flex rounded-md border overflow-hidden text-sm">
      <button
        class="px-4 py-1.5 hover:bg-muted"
        :class="tab === 'resumo' ? 'bg-primary text-primary-foreground' : ''"
        @click="setTab('resumo')"
      >
        Resumo
      </button>
      <button
        class="px-4 py-1.5 hover:bg-muted border-l"
        :class="tab === 'estoque' ? 'bg-primary text-primary-foreground' : ''"
        @click="setTab('estoque')"
      >
        Estoque Bling
      </button>
      <button
        class="px-4 py-1.5 hover:bg-muted border-l"
        :class="tab === 'saldo' ? 'bg-primary text-primary-foreground' : ''"
        @click="setTab('saldo')"
      >
        A Receber
      </button>
    </div>

    <!-- ─────────────────────────────────────────  Aba RESUMO  ───────────────────── -->
    <template v-if="tab === 'resumo'">
      <p v-if="resumo" class="text-xs text-muted-foreground">
        Atualizado às 5h diariamente · gerado {{ geradoEm }}.
        Faturamento considera: {{ resumo.situacoes_label }}.
        Custo / Rentabilidade / Margem: <b>Em aberto, Em andamento e Entregue</b>. Margem = Rentabilidade ÷ Custo.
      </p>

      <div v-if="errorResumo" class="text-sm text-destructive">{{ errorResumo }}</div>
      <div v-if="loadingResumo && !resumo" class="text-sm text-muted-foreground">
        <Loader2 class="inline h-4 w-4 animate-spin" /> carregando…
      </div>

      <template v-if="resumo">
        <!-- 1. Valuation 3 meses -->
        <section class="space-y-2">
          <h2 class="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
            Valuation — 3 meses
          </h2>
          <div class="border rounded-md overflow-auto">
            <table class="w-full text-sm border-collapse">
              <thead class="bg-muted/50 text-xs uppercase tracking-wide">
                <tr>
                  <th class="text-left px-3 py-2 font-medium">Componente</th>
                  <th
                    v-for="m in valMeses"
                    :key="m.mes"
                    class="text-right px-3 py-2 font-medium capitalize"
                  >
                    {{ mesLabel(m.mes) }}
                    <span v-if="m.data_snapshot" class="block text-[10px] normal-case text-muted-foreground">
                      (até {{ diaCurto(m.data_snapshot) }})
                    </span>
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr class="border-t">
                  <td class="px-3 py-1.5">Caixa</td>
                  <td v-for="m in valMeses" :key="m.mes" class="px-3 py-1.5 text-right tabular-nums">
                    {{ fmtBRL(m.caixa) }}
                  </td>
                </tr>
                <tr class="border-t bg-muted/20">
                  <td class="px-3 py-1.5">A Receber</td>
                  <td v-for="m in valMeses" :key="m.mes" class="px-3 py-1.5 text-right tabular-nums">
                    {{ fmtBRL(m.receber) }}
                  </td>
                </tr>
                <tr class="border-t">
                  <td class="px-3 py-1.5">Estoque</td>
                  <td v-for="m in valMeses" :key="m.mes" class="px-3 py-1.5 text-right tabular-nums">
                    {{ fmtBRL(m.estoque) }}
                  </td>
                </tr>
                <tr class="border-t bg-muted/40 font-semibold">
                  <td class="px-3 py-2">TOTAL VALUATION</td>
                  <td v-for="m in valMeses" :key="m.mes" class="px-3 py-2 text-right tabular-nums">
                    {{ fmtBRL(m.total) }}
                  </td>
                </tr>
                <tr class="border-t bg-amber-50 dark:bg-amber-950/30 font-semibold">
                  <td class="px-3 py-2">Rentabilidade (mês)</td>
                  <td v-for="m in valMeses" :key="m.mes" class="px-3 py-2 text-right tabular-nums">
                    {{ fmtBRL(m.rentabilidade) }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <!-- 3. Operacional — 3 meses (situações / reembolso / devoluções por mês) -->
        <section class="space-y-2">
          <h2 class="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
            Operacional — 3 meses
            <span class="normal-case text-xs text-muted-foreground">(situações, reembolso e devoluções por mês)</span>
          </h2>
          <div class="border rounded-md overflow-auto">
            <table class="w-full text-sm border-collapse">
              <thead class="bg-muted/50 text-xs uppercase tracking-wide">
                <tr>
                  <th class="text-left px-3 py-2 font-medium">Métrica</th>
                  <th
                    v-for="m in resumo.operacional.meses"
                    :key="m"
                    class="text-right px-3 py-2 font-medium capitalize"
                  >
                    {{ mesLabel(m) }}
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="linha in resumo.operacional.linhas"
                  :key="linha.chave"
                  class="border-t hover:bg-muted/20"
                >
                  <td class="px-3 py-1.5">
                    <span
                      class="inline-flex items-center gap-1.5 cursor-help"
                      :title="linha.descricao"
                    >
                      {{ linha.label }}
                      <Info class="h-3.5 w-3.5 text-muted-foreground/60 shrink-0" />
                    </span>
                  </td>
                  <td
                    v-for="(v, i) in linha.valores"
                    :key="resumo.operacional.meses[i]"
                    class="px-3 py-1.5 text-right tabular-nums"
                  >
                    {{ linha.formato === 'pct' ? fmtPct(v) : fmtBRL(v) }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      </template>
    </template>

    <!-- ─────────────────────────────────────────  Aba ESTOQUE BLING  ──────────── -->
    <template v-if="tab === 'estoque'">
      <p class="text-xs text-muted-foreground">
        Snapshot diário do estoque por local (PI/SA/SP/RA/CD/CI/US/Eletro/Mala/Outros),
        valor = saldo físico × preço de custo. Roda às 8h BRT.
        <template v-if="estoque">
          · data {{ diaLabel(estoque.data) }} · gravado {{ estoqueUpdatedEm }}.
        </template>
      </p>

      <div v-if="errorEstoque === 'estoque_bling_sem_snapshot'" class="text-sm text-muted-foreground">
        Nenhum snapshot ainda — aguarde a primeira execução do cron diário (≈08h BRT).
      </div>
      <div v-else-if="errorEstoque" class="text-sm text-destructive">{{ errorEstoque }}</div>
      <div v-if="loadingEstoque && !estoque" class="text-sm text-muted-foreground">
        <Loader2 class="inline h-4 w-4 animate-spin" /> carregando…
      </div>

      <template v-if="estoque">
        <div class="border rounded-md overflow-auto">
          <table class="w-full text-sm border-collapse">
            <thead class="bg-muted/50 text-xs uppercase tracking-wide">
              <tr>
                <th class="text-left px-3 py-2 font-medium">Local</th>
                <th class="text-right px-3 py-2 font-medium">Quantidade</th>
                <th class="text-right px-3 py-2 font-medium">Valor</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="!estoque.locais.length">
                <td colspan="3" class="text-center py-6 text-muted-foreground">Sem itens em estoque.</td>
              </tr>
              <tr
                v-for="r in estoque.locais"
                :key="r.local"
                class="border-t hover:bg-muted/20"
              >
                <td class="px-3 py-1.5 font-medium">{{ r.local }}</td>
                <td class="px-3 py-1.5 text-right tabular-nums">{{ fmtInt(r.qtd) }}</td>
                <td class="px-3 py-1.5 text-right tabular-nums">{{ fmtBRL(r.valor) }}</td>
              </tr>
            </tbody>
            <tfoot class="bg-muted/30 font-semibold">
              <tr class="border-t">
                <td class="px-3 py-2">TOTAL</td>
                <td class="px-3 py-2 text-right tabular-nums">{{ fmtInt(estoque.total_qtd) }}</td>
                <td class="px-3 py-2 text-right tabular-nums">{{ fmtBRL(estoque.total_valor) }}</td>
              </tr>
            </tfoot>
          </table>
        </div>
      </template>
    </template>

    <!-- ─────────────────────────────────────────  Aba SALDO MARKETPLACE  ──────── -->
    <template v-if="tab === 'saldo'">
      <p class="text-xs text-muted-foreground">
        Saldo das contas por marketplace. Cada célula mostra
        <span class="text-muted-foreground/70">disponível</span> /
        <span class="font-medium">a receber</span>. Snapshot diário pela rotina AdsPower.
        <template v-if="saldo">
          · data {{ diaLabel(saldo.data) }} · gravado {{ saldoUpdatedEm }}.
        </template>
      </p>

      <div v-if="errorSaldo === 'saldo_marketplace_sem_snapshot'" class="text-sm text-muted-foreground">
        Nenhum snapshot ainda — aguarde a próxima execução da rotina AdsPower (≈08h BRT).
      </div>
      <div v-else-if="errorSaldo" class="text-sm text-destructive">{{ errorSaldo }}</div>
      <div v-if="loadingSaldo && !saldo" class="text-sm text-muted-foreground">
        <Loader2 class="inline h-4 w-4 animate-spin" /> carregando…
      </div>

      <template v-if="saldo">
        <div class="border rounded-md overflow-auto">
          <table class="w-full text-sm border-collapse">
            <thead class="bg-muted/50 text-xs uppercase tracking-wide">
              <tr>
                <th class="text-left px-3 py-2 font-medium sticky left-0 bg-muted/50">Loja</th>
                <th
                  v-for="m in saldo.marketplaces"
                  :key="m"
                  class="text-right px-3 py-2 font-medium"
                >
                  {{ mktTitle(m) }}
                </th>
                <th class="text-right px-3 py-2 font-medium">Total a receber</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="!saldo.lojas.length">
                <td :colspan="saldo.marketplaces.length + 2" class="text-center py-6 text-muted-foreground">
                  Sem lojas no snapshot.
                </td>
              </tr>
              <tr
                v-for="l in saldo.lojas"
                :key="l.loja"
                class="border-t hover:bg-muted/20"
              >
                <td class="px-3 py-1.5 font-medium sticky left-0 bg-background">{{ l.loja }}</td>
                <td
                  v-for="m in saldo.marketplaces"
                  :key="m"
                  class="px-3 py-1.5 text-right tabular-nums"
                >
                  <template v-if="l.saldos[m]?.nota">
                    <span
                      class="inline-block rounded px-1.5 py-0.5 text-[11px] font-medium text-amber-600 bg-amber-500/10"
                      :title="l.saldos[m].nota || ''"
                    >{{ l.saldos[m].nota }}</span>
                  </template>
                  <template v-else-if="l.saldos[m]">
                    <div class="text-[11px] text-muted-foreground">{{ fmtBRL(l.saldos[m].disponivel) }}</div>
                    <div class="font-medium">{{ fmtBRL(l.saldos[m].a_receber) }}</div>
                  </template>
                  <span v-else class="text-muted-foreground">—</span>
                </td>
                <td class="px-3 py-1.5 text-right tabular-nums font-medium">{{ fmtBRL(l.total_a_receber) }}</td>
              </tr>
            </tbody>
            <tfoot class="bg-muted/30 font-semibold">
              <tr class="border-t">
                <td class="px-3 py-2 sticky left-0 bg-muted/30">TOTAL</td>
                <td
                  v-for="m in saldo.marketplaces"
                  :key="m"
                  class="px-3 py-2 text-right tabular-nums"
                >
                  <div class="text-[11px] text-muted-foreground font-normal">{{ fmtBRL(colTotalDisp(m)) }}</div>
                  <div>{{ fmtBRL(colTotalReceber(m)) }}</div>
                </td>
                <td class="px-3 py-2 text-right tabular-nums">{{ fmtBRL(saldo.total_a_receber) }}</td>
              </tr>
            </tfoot>
          </table>
        </div>
        <p class="text-[11px] text-muted-foreground">
          Total disponível (todas as contas): {{ fmtBRL(saldo.total_disponivel) }}.
          O total a receber reconcilia com "A Receber" da aba Resumo.
        </p>
      </template>
    </template>
  </div>
</template>
