<script setup lang="ts">
// Resumo do Painel de Garantia (28/10/2026) — as garantias por DEPARTAMENTO
// (Celular, Eletro, Acessórios…), com a porcentagem de cada um no total, a
// situação (ativa, só software, aguardando entrega, expirada), quantos
// clientes escreveram, os atendimentos registrados e os produtos (modelos)
// de cada departamento. POST /api/garantias/resumo com os filtros da barra
// (período, origem, status). Departamento = a categoria do produto no Bling,
// a mesma do Faturamento; sem ela, a família do SKU (aparece como "sem
// categoria no Bling"). Só contagens: nada de nome nem CPF.
// Barras: um tom só (a cor da marca) sobre o trilho cinza — a parte do total;
// o número e a porcentagem sempre escritos ao lado (a cor nunca sozinha).
import { AlertCircle, Loader2, PieChart } from 'lucide-vue-next'
import {
  corpoDoResumo,
  errosDaApi,
  largura,
  numeroBR,
  pct,
  statusInfo,
  type FiltrosLista,
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

// "Entregue — sem data no DaVinci" à parte do "Aguardando entrega" (a regra
// do painel: nunca dizer "aguardando" de quem já recebeu).
type ChaveStatus = StatusGarantia | 'entregue_sem_data'
const STATUS_ORDEM: ChaveStatus[] = ['ativa', 'somente_software', 'aguardando_entrega', 'entregue_sem_data', 'expirada']
function rotuloStatus(s: ChaveStatus) {
  return s === 'entregue_sem_data' ? statusInfo('aguardando_entrega', true) : statusInfo(s)
}
// Os produtos de cada departamento: os 10 maiores; "ver todos" abre o resto.
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
        <span><strong class="text-foreground">{{ numeroBR(resumo.total) }}</strong> {{ resumo.total === 1 ? 'garantia' : 'garantias' }} por departamento — a categoria do produto no Bling (a mesma do Faturamento).</span>
        <Loader2 v-if="carregando" class="size-3 animate-spin" />
      </p>
      <p v-if="!resumo.departamentos.length" class="rounded-lg border py-10 text-center text-sm text-muted-foreground">Nenhuma garantia com esses filtros.</p>

      <!-- a parte de cada departamento no total -->
      <div v-if="resumo.departamentos.length" class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" data-departamentos>
        <a
          v-for="d in resumo.departamentos"
          :key="d.chave"
          :href="`#dep-${d.chave}`"
          class="rounded-xl border bg-card p-3 transition-colors hover:bg-muted/50 sm:p-4"
          :data-departamento="d.chave"
        >
          <div class="flex items-baseline justify-between gap-2">
            <span class="text-xs font-medium uppercase tracking-wider text-muted-foreground">{{ d.nome }}</span>
            <span class="text-sm font-semibold tabular-nums" data-pct>{{ pct(d.total, resumo.total) }}</span>
          </div>
          <div class="mt-1 text-2xl font-semibold tabular-nums">{{ numeroBR(d.total) }}</div>
          <div class="mt-2 h-2 rounded-full bg-muted" :title="`${d.nome}: ${numeroBR(d.total)} de ${numeroBR(resumo.total)} (${pct(d.total, resumo.total)})`">
            <div class="h-2 rounded-full bg-primary" :style="{ width: `${largura(d.total, resumo.total)}%` }" />
          </div>
          <div class="mt-2 flex flex-wrap gap-x-1.5 text-xs text-muted-foreground">
            <template v-for="(c, i) in d.categorias" :key="c.nome">
              <span v-if="i" aria-hidden="true">·</span>
              <span :class="c.sem_categoria ? 'italic' : ''" :title="c.sem_categoria ? 'sem categoria no pedido nem no cadastro do produto: entrou pela família do SKU' : ''">{{ c.nome }} {{ pct(c.total, d.total) }}</span>
            </template>
          </div>
        </a>
      </div>

      <!-- cada departamento em detalhe -->
      <article v-for="d in resumo.departamentos" :id="`dep-${d.chave}`" :key="`det-${d.chave}`" class="scroll-mt-16 space-y-3 rounded-lg border bg-card p-4" :data-detalhe="d.chave">
        <header class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h2 class="text-base font-semibold">{{ d.nome }}</h2>
          <span class="text-sm tabular-nums text-muted-foreground">{{ numeroBR(d.total) }} {{ d.total === 1 ? 'garantia' : 'garantias' }} · {{ pct(d.total, resumo.total) }} do total</span>
        </header>

        <!-- situação -->
        <div class="grid grid-cols-2 gap-2 md:grid-cols-5">
          <div v-for="s in STATUS_ORDEM" :key="s" class="rounded-md border px-3 py-2" :data-status="s" :title="rotuloStatus(s).dica || ''">
            <div class="truncate text-xs text-muted-foreground">{{ rotuloStatus(s).emoji }} {{ rotuloStatus(s).rotulo }}</div>
            <div class="flex items-baseline justify-between gap-2">
              <span class="text-lg font-semibold tabular-nums">{{ numeroBR(d.status[s] || 0) }}</span>
              <span class="text-xs tabular-nums text-muted-foreground">{{ pct(d.status[s] || 0, d.total) }}</span>
            </div>
          </div>
        </div>

        <!-- quem procurou -->
        <dl class="grid gap-2 text-sm md:grid-cols-2">
          <div class="rounded-md bg-muted/40 px-3 py-2">
            <dt class="text-xs text-muted-foreground" title="alguma conversa ligada à garantia tem mensagem do cliente desde a compra — a mensagem automática da loja não conta">Clientes que escreveram depois da compra</dt>
            <dd class="tabular-nums"><strong>{{ numeroBR(d.cliente_escreveu) }}</strong> · {{ pct(d.cliente_escreveu, d.total) }} das garantias</dd>
          </div>
          <div class="rounded-md bg-muted/40 px-3 py-2">
            <dt class="text-xs text-muted-foreground">Garantias com atendimento registrado</dt>
            <dd class="tabular-nums"><strong>{{ numeroBR(d.com_atendimento) }}</strong> · {{ pct(d.com_atendimento, d.total) }} das garantias<template v-if="atendimentos(d)"> — hardware {{ numeroBR(d.atendimentos_hardware) }} · software {{ numeroBR(d.atendimentos_software) }}</template></dd>
          </div>
        </dl>

        <!-- produtos (modelos) -->
        <div class="overflow-x-auto rounded-md border">
          <table class="w-full text-sm" :data-produtos="d.chave">
            <thead class="bg-muted text-left">
              <tr>
                <th class="px-3 py-2 font-medium">Produto</th>
                <th class="px-3 py-2 text-right font-medium">Garantias</th>
                <th class="w-[30%] px-3 py-2 font-medium">% do departamento</th>
                <th class="whitespace-nowrap px-3 py-2 text-right font-medium" title="clientes que escreveram depois da compra">Escreveram</th>
                <th class="whitespace-nowrap px-3 py-2 text-right font-medium" title="garantias deste produto com pelo menos um atendimento registrado">Com atendimento</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="p in produtosVisiveis(d)" :key="p.base" class="border-t">
                <td class="px-3 py-2">
                  <div>{{ p.nome }}</div>
                  <div class="font-mono text-xs text-muted-foreground">{{ p.base }}</div>
                </td>
                <td class="px-3 py-2 text-right tabular-nums">{{ numeroBR(p.total) }}</td>
                <td class="px-3 py-2">
                  <div class="flex items-center gap-2" :title="`${p.nome}: ${numeroBR(p.total)} de ${numeroBR(d.total)} (${pct(p.total, d.total)})`">
                    <div class="h-2 flex-1 rounded-full bg-muted"><div class="h-2 rounded-full bg-primary" :style="{ width: `${largura(p.total, d.total)}%` }" /></div>
                    <span class="w-12 text-right text-xs tabular-nums">{{ pct(p.total, d.total) }}</span>
                  </div>
                </td>
                <td class="px-3 py-2 text-right tabular-nums">{{ numeroBR(p.cliente_escreveu) }} <span class="text-xs text-muted-foreground">({{ pct(p.cliente_escreveu, p.total) }})</span></td>
                <td class="px-3 py-2 text-right tabular-nums">{{ numeroBR(p.com_atendimento) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <button v-if="d.produtos.length > 10" type="button" class="text-xs text-primary underline-offset-2 hover:underline" @click="alternar(d.chave)">
          {{ abertos.has(d.chave) ? 'mostrar só os 10 maiores' : `ver todos os ${d.produtos.length} produtos` }}
        </button>
      </article>
    </template>
  </section>
</template>
