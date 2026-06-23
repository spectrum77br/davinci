<script setup lang="ts">
// Valuation — duas abas:
//   1) Resumo: relatório de faturamento dos últimos 3 meses (porta web do
//      antigo PDF diário). Consulta ao vivo davinci.bling_orders / valuation
//      / stores (atualizadas pela rotina das 5h).
//   2) Estoque Bling: último snapshot diário do estoque por local
//      (PI/SA/SP/RA/CD/CI/US/Eletro/Mala/Outros). Gravado pelo cron arq
//      `valuation_estoque_snapshot` (~08h BRT) em valuation_estoque_bling_diario.
import { computed, onMounted, ref } from 'vue'
import { Loader2, Lock, RefreshCw } from 'lucide-vue-next'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'financeiro_valuation', action: 'view' },
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
type SituacaoLinha = { situacao_nome: string; pedidos: number; faturamento: number }
type SituacaoSecao = {
  data: string | null
  linhas: SituacaoLinha[]
  total_pedidos: number
  total_faturamento: number
}
type FatLinha = {
  grp: string
  faturamento: number
  custo: number
  rentabilidade: number
  margem: number | null
}
type FatMesSecao = {
  mes: string
  linhas: FatLinha[]
  total_faturamento: number
  total_custo: number
  total_rentabilidade: number
  total_margem: number | null
}
type Report = {
  gerado_em: string
  situacoes_label: string
  valuation_meses: ValuationMes[]
  resumo_ontem: SituacaoSecao
  eficacia: SituacaoSecao
  por_marketplace: FatMesSecao[]
  por_categoria: FatMesSecao[]
}
type EstoqueLocal = { local: string; qtd: number; valor: number }
type EstoqueSnapshot = {
  data: string
  updated_at: string
  total_qtd: number
  total_valor: number
  locais: EstoqueLocal[]
}

type Tab = 'resumo' | 'estoque'
const tab = ref<Tab>('resumo')

// ── Senha extra (camada acima do require_permission) ─────────────────────
// Token vem do POST /unlock e é guardado em sessionStorage (some ao fechar
// a aba). Backend valida HMAC + age <= 8h. Enviado em X-Valuation-Token
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
}

// Loading/erro por aba — cada uma carrega na primeira ativação (lazy).
const loadingResumo = ref(false)
const loadingEstoque = ref(false)
const errorResumo = ref<string | null>(null)
const errorEstoque = ref<string | null>(null)
const resumo = ref<Report | null>(null)
const estoque = ref<EstoqueSnapshot | null>(null)

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

function setTab(t: Tab) {
  tab.value = t
  if (!unlockToken.value) return
  if (t === 'resumo' && !resumo.value && !loadingResumo.value) void loadResumo()
  if (t === 'estoque' && !estoque.value && !loadingEstoque.value) void loadEstoque()
}

function reload() {
  if (!unlockToken.value) return
  if (tab.value === 'resumo') void loadResumo()
  else void loadEstoque()
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
const loading = computed(() =>
  tab.value === 'resumo' ? loadingResumo.value : loadingEstoque.value,
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
        Esta página exige uma senha adicional. O acesso fica liberado por 8h nesta aba.
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
          : 'Estoque Bling por local — snapshot diário.' }}
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
    </div>

    <!-- ─────────────────────────────────────────  Aba RESUMO  ───────────────────── -->
    <template v-if="tab === 'resumo'">
      <p v-if="resumo" class="text-xs text-muted-foreground">
        Atualizado às 5h diariamente · gerado {{ geradoEm }}.
        Faturamento considera: {{ resumo.situacoes_label }}.
        Custo / Rentabilidade / Margem apenas <b>Entregue</b>. Margem = Rentabilidade ÷ Custo.
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

        <!-- 2. Resumo de ontem -->
        <section class="space-y-2">
          <h2 class="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
            Resumo de ontem
            <span v-if="resumo.resumo_ontem.data" class="normal-case text-xs">
              ({{ diaLabel(resumo.resumo_ontem.data) }})
            </span>
          </h2>
          <div class="border rounded-md overflow-auto">
            <table class="w-full text-sm border-collapse">
              <thead class="bg-muted/50 text-xs uppercase tracking-wide">
                <tr>
                  <th class="text-left px-3 py-2 font-medium">Situação</th>
                  <th class="text-right px-3 py-2 font-medium">Pedidos</th>
                  <th class="text-right px-3 py-2 font-medium">Faturamento</th>
                </tr>
              </thead>
              <tbody>
                <tr v-if="!resumo.resumo_ontem.linhas.length">
                  <td colspan="3" class="text-center py-6 text-muted-foreground">Sem pedidos ontem.</td>
                </tr>
                <tr
                  v-for="(r, i) in resumo.resumo_ontem.linhas"
                  :key="r.situacao_nome + i"
                  class="border-t hover:bg-muted/20"
                >
                  <td class="px-3 py-1.5">{{ r.situacao_nome }}</td>
                  <td class="px-3 py-1.5 text-right tabular-nums">{{ fmtInt(r.pedidos) }}</td>
                  <td class="px-3 py-1.5 text-right tabular-nums">{{ fmtBRL(r.faturamento) }}</td>
                </tr>
              </tbody>
              <tfoot v-if="resumo.resumo_ontem.linhas.length" class="bg-muted/30 font-semibold">
                <tr class="border-t">
                  <td class="px-3 py-2">Total</td>
                  <td class="px-3 py-2 text-right tabular-nums">{{ fmtInt(resumo.resumo_ontem.total_pedidos) }}</td>
                  <td class="px-3 py-2 text-right tabular-nums">{{ fmtBRL(resumo.resumo_ontem.total_faturamento) }}</td>
                </tr>
              </tfoot>
            </table>
          </div>
        </section>

        <!-- 3. Eficácia operacional -->
        <section class="space-y-2">
          <h2 class="text-sm font-semibold uppercase tracking-wide text-destructive">
            Eficácia operacional
            <span class="normal-case text-xs text-muted-foreground">(situações problemáticas — total do banco)</span>
          </h2>
          <div class="border rounded-md overflow-auto">
            <table class="w-full text-sm border-collapse">
              <thead class="bg-destructive/10 text-xs uppercase tracking-wide">
                <tr>
                  <th class="text-left px-3 py-2 font-medium">Situação</th>
                  <th class="text-right px-3 py-2 font-medium">Pedidos</th>
                  <th class="text-right px-3 py-2 font-medium">Faturamento</th>
                </tr>
              </thead>
              <tbody>
                <tr v-if="!resumo.eficacia.linhas.length">
                  <td colspan="3" class="text-center py-6 text-muted-foreground">Sem ocorrências.</td>
                </tr>
                <tr
                  v-for="(r, i) in resumo.eficacia.linhas"
                  :key="r.situacao_nome + i"
                  class="border-t hover:bg-muted/20"
                >
                  <td class="px-3 py-1.5">{{ r.situacao_nome }}</td>
                  <td class="px-3 py-1.5 text-right tabular-nums">{{ fmtInt(r.pedidos) }}</td>
                  <td class="px-3 py-1.5 text-right tabular-nums">{{ fmtBRL(r.faturamento) }}</td>
                </tr>
              </tbody>
              <tfoot v-if="resumo.eficacia.linhas.length" class="bg-muted/30 font-semibold">
                <tr class="border-t">
                  <td class="px-3 py-2">Total</td>
                  <td class="px-3 py-2 text-right tabular-nums">{{ fmtInt(resumo.eficacia.total_pedidos) }}</td>
                  <td class="px-3 py-2 text-right tabular-nums">{{ fmtBRL(resumo.eficacia.total_faturamento) }}</td>
                </tr>
              </tfoot>
            </table>
          </div>
        </section>

        <!-- 4 + 5. Por Marketplace / Por Categoria -->
        <section
          v-for="grupo in [
            { titulo: 'Por Marketplace', col: 'Marketplace', meses: resumo.por_marketplace },
            { titulo: 'Por Categoria', col: 'Categoria', meses: resumo.por_categoria },
          ]"
          :key="grupo.titulo"
          class="space-y-3"
        >
          <h2 class="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
            {{ grupo.titulo }}
          </h2>
          <div v-for="sec in grupo.meses" :key="grupo.titulo + sec.mes" class="space-y-1">
            <h3 class="text-xs font-semibold capitalize">{{ mesLabel(sec.mes) }}</h3>
            <div class="border rounded-md overflow-auto">
              <table class="w-full text-sm border-collapse">
                <thead class="bg-muted/50 text-xs uppercase tracking-wide">
                  <tr>
                    <th class="text-left px-3 py-2 font-medium">{{ grupo.col }}</th>
                    <th class="text-right px-3 py-2 font-medium">Faturamento</th>
                    <th class="text-right px-3 py-2 font-medium">Custo (Entregue)</th>
                    <th class="text-right px-3 py-2 font-medium">Rentabilidade</th>
                    <th class="text-right px-3 py-2 font-medium">Margem</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-if="!sec.linhas.length">
                    <td colspan="5" class="text-center py-4 text-muted-foreground">Sem dados no mês.</td>
                  </tr>
                  <tr
                    v-for="(r, i) in sec.linhas"
                    :key="r.grp + i"
                    class="border-t hover:bg-muted/20"
                  >
                    <td class="px-3 py-1.5">{{ r.grp }}</td>
                    <td class="px-3 py-1.5 text-right tabular-nums">{{ fmtBRL(r.faturamento) }}</td>
                    <td class="px-3 py-1.5 text-right tabular-nums">{{ fmtBRL(r.custo) }}</td>
                    <td
                      class="px-3 py-1.5 text-right tabular-nums"
                      :class="r.rentabilidade < 0 ? 'text-destructive' : ''"
                    >
                      {{ fmtBRL(r.rentabilidade) }}
                    </td>
                    <td
                      class="px-3 py-1.5 text-right tabular-nums"
                      :class="r.margem != null && r.margem < 0 ? 'text-destructive' : ''"
                    >
                      {{ fmtPct(r.margem) }}
                    </td>
                  </tr>
                </tbody>
                <tfoot v-if="sec.linhas.length" class="bg-muted/30 font-semibold">
                  <tr class="border-t">
                    <td class="px-3 py-2">Total</td>
                    <td class="px-3 py-2 text-right tabular-nums">{{ fmtBRL(sec.total_faturamento) }}</td>
                    <td class="px-3 py-2 text-right tabular-nums">{{ fmtBRL(sec.total_custo) }}</td>
                    <td class="px-3 py-2 text-right tabular-nums">{{ fmtBRL(sec.total_rentabilidade) }}</td>
                    <td class="px-3 py-2 text-right tabular-nums">{{ fmtPct(sec.total_margem) }}</td>
                  </tr>
                </tfoot>
              </table>
            </div>
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
  </div>
</template>
