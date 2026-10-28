<script setup lang="ts">
// Resumo do Painel de Garantia (28/10/2026) — por DEPARTAMENTO (Celular,
// Eletro, Acessórios…): quantos PEDIRAM garantia sobre quantos foram vendidos
// com nota ("a garantia é só para o pessoal que solicita", Eduardo, 28/10). E,
// dos pedidos: a situação da garantia, de onde veio o pedido (mensagem do
// cliente, SAC do site, devolução por defeito…), os atendimentos registrados
// e os produtos. POST /api/garantias/resumo com os filtros da barra (período,
// origem, status). Departamento = a categoria do produto no Bling, a mesma do
// Faturamento; sem ela, a família do SKU. Só contagens: nada de nome nem CPF.
// Barras: um tom só (a cor da marca) sobre o trilho cinza; o comprimento é a
// porcentagem de quem pediu, na escala da MAIOR do quadro (o número e a
// porcentagem sempre escritos ao lado — a cor nunca sozinha).
import { AlertCircle, Loader2, PieChart } from 'lucide-vue-next'
import {
  ORIGEM_PEDIDO,
  corpoDoResumo,
  errosDaApi,
  numeroBR,
  pct,
  statusInfo,
  type FiltrosLista,
  type OrigemPedido,
  type Resumo,
  type ResumoDepartamento,
  type StatusGarantia,
} from '~/lib/garantias'

const props = defineProps<{ filtros: FiltrosLista; versao?: number }>()

const { api } = useApi()
const resumo = ref<Resumo | null>(null)
const carregando = ref(false)
const erro = ref('')
let geracao = 0

async function carregar() {
  const g = ++geracao
  carregando.value = true
  erro.value = ''
  try {
    const r = await api<Resumo>('/api/garantias/resumo', { method: 'POST', body: corpoDoResumo(props.filtros) })
    if (g !== geracao) return
    resumo.value = r
  } catch (e) {
    if (g !== geracao) return
    erro.value = errosDaApi(e).geral || 'Não consegui carregar o resumo.'
  } finally {
    if (g === geracao) carregando.value = false
  }
}
watch(
  [() => corpoDoResumo(props.filtros), () => props.versao],
  () => { void carregar() },
  { immediate: true, deep: true },
)

// A taxa (0–1) e a barra na escala da maior taxa do grupo.
function taxa(pediram: number, vendidas: number) {
  return vendidas ? pediram / vendidas : 0
}
function barra(t: number, maior: number) {
  if (!t || !maior) return 0
  return Math.max(2, Math.min(100, (t / maior) * 100))
}
const maiorDepartamento = computed(() => Math.max(0, ...(resumo.value?.departamentos || []).map((d) => taxa(d.pediram, d.vendidas))))
// A escala dos produtos: a maior taxa entre os que venderam 10 ou mais (1
// pedido em 1 venda daria 100% e encolheria todas as barras).
function maiorProduto(d: ResumoDepartamento) {
  const base = d.produtos.filter((p) => p.vendidas >= 10)
  return Math.max(0, ...(base.length ? base : d.produtos).map((p) => taxa(p.pediram, p.vendidas)))
}

// "Entregue — sem data no DaVinci" à parte do "Aguardando entrega" (a regra
// do painel: nunca dizer "aguardando" de quem já recebeu).
type ChaveStatus = StatusGarantia | 'entregue_sem_data'
const STATUS_ORDEM: ChaveStatus[] = ['ativa', 'somente_software', 'aguardando_entrega', 'entregue_sem_data', 'expirada']
function rotuloStatus(s: ChaveStatus) {
  return s === 'entregue_sem_data' ? statusInfo('aguardando_entrega', true) : statusInfo(s)
}
const ORIGEM_ORDEM = Object.keys(ORIGEM_PEDIDO) as OrigemPedido[]
function origens(d: ResumoDepartamento) {
  return ORIGEM_ORDEM.filter((o) => (d.origens[o] || 0) > 0)
}
// Os produtos de cada departamento: os 10 com mais pedidos; "ver todos" abre o resto.
const abertos = ref<Set<string>>(new Set())
function produtosVisiveis(d: ResumoDepartamento) {
  return abertos.value.has(d.chave) ? d.produtos : d.produtos.slice(0, 10)
}
function alternar(chave: string) {
  const s = new Set(abertos.value)
  if (s.has(chave)) s.delete(chave)
  else s.add(chave)
  abertos.value = s
}
function atendimentos(d: ResumoDepartamento) {
  return d.atendimentos_hardware + d.atendimentos_software
}
</script>

<template>
  <section class="space-y-4" data-garantia-resumo>
    <p v-if="erro" role="alert" class="flex items-center gap-2 text-sm text-destructive">
      <AlertCircle class="size-4" /> {{ erro }} <button type="button" class="underline" @click="carregar">tentar de novo</button>
    </p>
    <div v-else-if="!resumo" class="flex items-center justify-center gap-2 rounded-lg border py-14 text-sm text-muted-foreground"><Loader2 class="size-4 animate-spin" /> montando o resumo…</div>
    <template v-else>
      <p class="flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
        <PieChart class="size-3.5" />
        <span><strong class="text-foreground">{{ numeroBR(resumo.pediram) }}</strong> {{ resumo.pediram === 1 ? 'pedido de garantia' : 'pedidos de garantia' }} em <strong class="text-foreground">{{ numeroBR(resumo.vendidas) }}</strong> vendas com nota (<strong class="text-foreground">{{ pct(resumo.pediram, resumo.vendidas) }}</strong>) — por departamento, a categoria do produto no Bling (a mesma do Faturamento).</span>
        <Loader2 v-if="carregando" class="size-3 animate-spin" />
      </p>
      <p v-if="!resumo.departamentos.length" class="rounded-lg border py-10 text-center text-sm text-muted-foreground">Nenhuma venda com esses filtros.</p>

      <!-- quanto pediu garantia em cada departamento -->
      <div v-if="resumo.departamentos.length" class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" data-departamentos>
        <a
          v-for="d in resumo.departamentos"
          :key="d.chave"
          :href="`#dep-${d.chave}`"
          class="rounded-xl border bg-card p-3 transition-colors hover:bg-muted/50 sm:p-4"
          :data-departamento="d.chave"
        >
          <div class="text-xs font-medium uppercase tracking-wider text-muted-foreground">{{ d.nome }}</div>
          <div class="mt-1 flex items-baseline gap-2">
            <span class="text-2xl font-semibold tabular-nums" data-pct>{{ pct(d.pediram, d.vendidas) }}</span>
            <span class="text-xs text-muted-foreground">pediram garantia</span>
          </div>
          <div class="text-xs tabular-nums text-muted-foreground">{{ numeroBR(d.pediram) }} {{ d.pediram === 1 ? 'pedido' : 'pedidos' }} em {{ numeroBR(d.vendidas) }} {{ d.vendidas === 1 ? 'venda' : 'vendas' }}</div>
          <div class="mt-2 h-2 rounded-full bg-muted" :title="`${d.nome}: ${numeroBR(d.pediram)} pedidos em ${numeroBR(d.vendidas)} vendas (${pct(d.pediram, d.vendidas)}) — a barra cheia é o departamento com a maior porcentagem`">
            <div class="h-2 rounded-full bg-primary" :style="{ width: `${barra(taxa(d.pediram, d.vendidas), maiorDepartamento)}%` }" />
          </div>
          <div class="mt-2 flex flex-wrap gap-x-1.5 text-xs text-muted-foreground">
            <template v-for="(c, i) in d.categorias" :key="c.nome">
              <span v-if="i" aria-hidden="true">·</span>
              <span :class="c.sem_categoria ? 'italic' : ''" :title="`${c.nome}: ${numeroBR(c.pediram)} pedidos em ${numeroBR(c.vendidas)} vendas${c.sem_categoria ? ' (sem categoria no pedido nem no cadastro do produto: entrou pela família do SKU)' : ''}`">{{ c.nome }} {{ pct(c.pediram, c.vendidas) }}</span>
            </template>
          </div>
        </a>
      </div>

      <!-- cada departamento em detalhe -->
      <article v-for="d in resumo.departamentos" :id="`dep-${d.chave}`" :key="`det-${d.chave}`" class="scroll-mt-16 space-y-3 rounded-lg border bg-card p-4" :data-detalhe="d.chave">
        <header class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h2 class="text-base font-semibold">{{ d.nome }}</h2>
          <span class="text-sm tabular-nums text-muted-foreground">{{ numeroBR(d.pediram) }} {{ d.pediram === 1 ? 'pedido de garantia' : 'pedidos de garantia' }} em {{ numeroBR(d.vendidas) }} vendas · {{ pct(d.pediram, d.vendidas) }}</span>
        </header>

        <template v-if="d.pediram">
          <!-- de onde vieram os pedidos -->
          <div class="flex flex-wrap items-center gap-1.5 text-xs" data-origens>
            <span class="text-muted-foreground">De onde vieram:</span>
            <span v-for="o in origens(d)" :key="o" class="rounded-full border px-2 py-0.5 tabular-nums" :data-origem="o">{{ ORIGEM_PEDIDO[o].emoji }} {{ ORIGEM_PEDIDO[o].rotulo }} <strong>{{ numeroBR(d.origens[o] || 0) }}</strong></span>
          </div>

          <!-- situação da garantia de quem pediu -->
          <div class="grid grid-cols-2 gap-2 md:grid-cols-5">
            <div v-for="s in STATUS_ORDEM" :key="s" class="rounded-md border px-3 py-2" :data-status="s" :title="rotuloStatus(s).dica || ''">
              <div class="truncate text-xs text-muted-foreground">{{ rotuloStatus(s).emoji }} {{ rotuloStatus(s).rotulo }}</div>
              <div class="flex items-baseline justify-between gap-2">
                <span class="text-lg font-semibold tabular-nums">{{ numeroBR(d.status[s] || 0) }}</span>
                <span class="text-xs tabular-nums text-muted-foreground">{{ pct(d.status[s] || 0, d.pediram) }}</span>
              </div>
            </div>
          </div>
          <p class="text-xs text-muted-foreground">
            Com atendimento registrado: <strong class="text-foreground tabular-nums">{{ numeroBR(d.com_atendimento) }}</strong> de {{ numeroBR(d.pediram) }} pedidos ({{ pct(d.com_atendimento, d.pediram) }})<template v-if="atendimentos(d)"> — hardware {{ numeroBR(d.atendimentos_hardware) }} · software {{ numeroBR(d.atendimentos_software) }}</template>.
          </p>
        </template>
        <p v-else class="text-xs text-muted-foreground">Nenhum pedido de garantia neste departamento.</p>

        <!-- produtos (modelos) -->
        <div class="overflow-x-auto rounded-md border">
          <table class="w-full text-sm" :data-produtos="d.chave">
            <thead class="bg-muted text-left">
              <tr>
                <th class="px-3 py-2 font-medium">Produto</th>
                <th class="px-3 py-2 text-right font-medium">Vendidas</th>
                <th class="whitespace-nowrap px-3 py-2 text-right font-medium">Pediram garantia</th>
                <th class="w-[28%] px-3 py-2 font-medium">% que pediu</th>
                <th class="whitespace-nowrap px-3 py-2 text-right font-medium" title="pedidos deste produto com pelo menos um atendimento registrado">Com atendimento</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="p in produtosVisiveis(d)" :key="p.base" class="border-t">
                <td class="px-3 py-2">
                  <div>{{ p.nome }}</div>
                  <div class="font-mono text-xs text-muted-foreground">{{ p.base }}</div>
                </td>
                <td class="px-3 py-2 text-right tabular-nums">{{ numeroBR(p.vendidas) }}</td>
                <td class="px-3 py-2 text-right tabular-nums">{{ numeroBR(p.pediram) }}</td>
                <td class="px-3 py-2">
                  <div class="flex items-center gap-2" :title="`${p.nome}: ${numeroBR(p.pediram)} pedidos em ${numeroBR(p.vendidas)} vendas (${pct(p.pediram, p.vendidas)})`">
                    <div class="h-2 flex-1 rounded-full bg-muted"><div class="h-2 rounded-full bg-primary" :style="{ width: `${barra(taxa(p.pediram, p.vendidas), maiorProduto(d))}%` }" /></div>
                    <span class="w-12 text-right text-xs tabular-nums">{{ pct(p.pediram, p.vendidas) }}</span>
                  </div>
                </td>
                <td class="px-3 py-2 text-right tabular-nums">{{ numeroBR(p.com_atendimento) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <button v-if="d.produtos.length > 10" type="button" class="text-xs text-primary underline-offset-2 hover:underline" @click="alternar(d.chave)">
          {{ abertos.has(d.chave) ? 'mostrar só os 10 com mais pedidos' : `ver todos os ${d.produtos.length} produtos` }}
        </button>
      </article>
    </template>
  </section>
</template>
