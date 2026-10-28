<script setup lang="ts">
// Garantia por produto (28/10/2026) — no visual da aba Produtos da Tabela de
// Preços ("pode usar a nossa Tabela de Preços, aba Produtos, como referência…
// é tudo relacionado à garantia", Eduardo): as pílulas de departamento em cima
// e uma linha por produto da Tabela de Preços (o modelo, com as cores juntas),
// só com colunas de garantia — vendidas com nota, quantos PEDIRAM garantia e a
// porcentagem, de onde veio o pedido, a situação da garantia e os atendimentos.
// O produto vendido que não está na Tabela de Preços entra pelo SKU. Clicar na
// linha abre os pedidos daquele produto (emit 'abrir'). POST
// /api/garantias/resumo com os filtros da barra. Só contagens.
import { AlertCircle, Loader2 } from 'lucide-vue-next'
import {
  ORIGEM_PEDIDO,
  corpoDoResumo,
  errosDaApi,
  numeroBR,
  pct,
  type FiltrosLista,
  type OrigemPedido,
  type Resumo,
  type ResumoDepartamento,
  type ResumoProduto,
} from '~/lib/garantias'

const props = defineProps<{ filtros: FiltrosLista; versao?: number }>()
const emit = defineEmits<{
  // A linha do produto: os pedidos dela na lista.
  (e: 'abrir', produto: { nome: string; ids: number[] }): void
}>()

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
    erro.value = errosDaApi(e).geral || 'Não consegui carregar a garantia por produto.'
  } finally {
    if (g === geracao) carregando.value = false
  }
}
watch(
  [() => corpoDoResumo(props.filtros), () => props.versao],
  () => { void carregar() },
  { immediate: true, deep: true },
)

// O departamento escolhido (a pílula), guardado só nesta visita.
const departamento = ref<ResumoDepartamento['chave'] | ''>('')
const departamentoAtual = computed(() => {
  const deps = resumo.value?.departamentos || []
  return deps.find((d) => d.chave === departamento.value) || deps[0] || null
})
const busca = ref('')
function plano(t: string) {
  return t.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase()
}
const produtos = computed<ResumoProduto[]>(() => {
  const dep = departamentoAtual.value?.chave
  const termo = plano(busca.value.trim())
  return (resumo.value?.produtos || []).filter((p) =>
    p.departamento === dep && (!termo || plano(`${p.nome} ${p.skus.join(' ')}`).includes(termo)))
})
const ORIGENS: OrigemPedido[] = ['mensagem', 'sac', 'devolucao']
// O nome curto da coluna (o completo fica na dica e na legenda embaixo).
const ORIGEM_CURTA: Partial<Record<OrigemPedido, string>> = { mensagem: 'Mensagem', sac: 'SAC', devolucao: 'Devolução' }
function outras(p: ResumoProduto) {
  return (p.origens.atendimento || 0) + (p.origens.manual || 0) + (p.origens.pessoa || 0)
}
function aguardando(p: ResumoProduto) {
  return (p.status.aguardando_entrega || 0) + (p.status.entregue_sem_data || 0)
}
function soma(campo: (p: ResumoProduto) => number) {
  return produtos.value.reduce((n, p) => n + campo(p), 0)
}
function abrir(p: ResumoProduto) {
  if (p.pedidos.length) emit('abrir', { nome: p.nome, ids: p.pedidos })
}
</script>

<template>
  <section class="space-y-3" data-garantia-resumo>
    <p v-if="erro" role="alert" class="flex items-center gap-2 text-sm text-destructive">
      <AlertCircle class="size-4" /> {{ erro }} <button type="button" class="underline" @click="carregar">tentar de novo</button>
    </p>
    <div v-else-if="!resumo" class="flex items-center justify-center gap-2 rounded-lg border py-14 text-sm text-muted-foreground"><Loader2 class="size-4 animate-spin" /> montando a garantia por produto…</div>
    <template v-else>
      <!-- pílulas de departamento (como a Tabela de Preços) -->
      <div class="flex flex-wrap gap-2" data-departamentos>
        <button
          v-for="d in resumo.departamentos"
          :key="d.chave"
          type="button"
          class="inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm transition-colors"
          :class="departamentoAtual?.chave === d.chave ? 'border-primary bg-primary text-primary-foreground' : 'border-border hover:bg-muted'"
          :data-departamento="d.chave"
          @click="departamento = d.chave"
        >
          {{ d.nome }} <span class="tabular-nums" data-pct>· {{ pct(d.pediram, d.vendidas) }}</span>
          <span class="text-xs tabular-nums opacity-80">({{ numeroBR(d.pediram) }} de {{ numeroBR(d.vendidas) }})</span>
        </button>
        <Loader2 v-if="carregando" class="size-4 animate-spin self-center text-muted-foreground" />
      </div>

      <div class="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h3 class="text-base font-semibold">Garantia por produto — {{ departamentoAtual?.nome || '—' }}</h3>
          <p class="text-xs text-muted-foreground">
            {{ produtos.length }} produto(s) — clique na linha para ver os pedidos de garantia daquele produto
          </p>
        </div>
        <input v-model="busca" placeholder="buscar SKU ou produto…" class="w-56 rounded border bg-background px-2 py-1 text-sm" data-busca-produto>
      </div>

      <div class="max-h-[calc(100vh-320px)] overflow-auto rounded-lg border">
        <table class="w-full border-collapse text-sm" data-produtos>
          <thead class="sticky top-0 z-10 bg-muted">
            <tr>
              <th class="min-w-[120px] border-b border-border px-2 py-2 text-left font-medium">SKU</th>
              <th class="min-w-[220px] border-b border-border px-2 py-2 text-left font-medium">Produto</th>
              <th class="w-20 border-b border-border px-2 py-2 text-right font-medium" title="vendas com nota fiscal de produto (cada uma é uma garantia)">Vendidas</th>
              <th class="w-20 border-b border-border px-2 py-2 text-right font-medium" title="clientes que pediram garantia (mensagem, SAC, devolução por defeito, atendimento ou cadastro à mão)">Pediram</th>
              <th class="w-16 border-b border-border px-2 py-2 text-right font-medium">%</th>
              <th v-for="o in ORIGENS" :key="o" class="w-24 whitespace-nowrap border-b border-border px-2 py-2 text-right font-medium" :title="`pedidos que vieram de: ${ORIGEM_PEDIDO[o].rotulo}`">{{ ORIGEM_PEDIDO[o].emoji }} {{ ORIGEM_CURTA[o] }}</th>
              <th class="w-16 border-b border-border px-2 py-2 text-right font-medium" title="pedidos que vieram de atendimento registrado, cadastro à mão ou marcados pela equipe">Outros</th>
              <th class="w-20 whitespace-nowrap border-b border-border px-2 py-2 text-right font-medium" title="dos pedidos: garantia ativa hoje (hardware e software)">🟢 Ativas</th>
              <th class="w-20 whitespace-nowrap border-b border-border px-2 py-2 text-right font-medium" title="dos pedidos: só o software ainda coberto">🔵 Só SW</th>
              <th class="w-24 whitespace-nowrap border-b border-border px-2 py-2 text-right font-medium" title="dos pedidos: aguardando a data de entrega (inclui os já entregues no Bling sem data no DaVinci)">🟡 Aguard.</th>
              <th class="w-20 whitespace-nowrap border-b border-border px-2 py-2 text-right font-medium" title="dos pedidos: garantia vencida">🔴 Expir.</th>
              <th class="w-24 border-b border-border px-2 py-2 text-right font-medium" title="pedidos com atendimento registrado (hardware / software)">Atend.</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!produtos.length">
              <td colspan="14" class="py-6 text-center text-muted-foreground">{{ busca.trim() ? 'Nenhum produto com essa busca.' : 'Nenhuma venda neste departamento com esses filtros.' }}</td>
            </tr>
            <tr
              v-for="p in produtos"
              :key="p.chave"
              class="hover:bg-muted/40"
              :class="p.pedidos.length ? 'cursor-pointer' : ''"
              :title="p.pedidos.length ? `ver os ${p.pedidos.length} pedidos de garantia de ${p.nome}` : 'nenhum pedido de garantia'"
              :data-produto="p.chave"
              @click="abrir(p)"
            >
              <td class="border border-border px-2 py-1.5 font-mono text-xs">{{ p.skus.join(', ') }}</td>
              <td class="border border-border px-2 py-1.5">
                {{ p.nome }}
                <span v-if="!p.na_tabela" class="ml-1 whitespace-nowrap rounded bg-muted px-1 text-[10px] text-muted-foreground" title="vendido, mas sem linha na Tabela de Preços">fora da Tabela de Preços</span>
              </td>
              <td class="border border-border px-2 py-1.5 text-right tabular-nums">{{ numeroBR(p.vendidas) }}</td>
              <td class="border border-border px-2 py-1.5 text-right font-semibold tabular-nums">{{ numeroBR(p.pediram) }}</td>
              <td class="border border-border px-2 py-1.5 text-right tabular-nums">{{ pct(p.pediram, p.vendidas) }}</td>
              <td v-for="o in ORIGENS" :key="o" class="border border-border px-2 py-1.5 text-right tabular-nums" :class="p.origens[o] ? '' : 'text-muted-foreground'">{{ p.origens[o] || 0 }}</td>
              <td class="border border-border px-2 py-1.5 text-right tabular-nums" :class="outras(p) ? '' : 'text-muted-foreground'">{{ outras(p) }}</td>
              <td class="border border-border px-2 py-1.5 text-right tabular-nums" :class="p.status.ativa ? '' : 'text-muted-foreground'">{{ p.status.ativa || 0 }}</td>
              <td class="border border-border px-2 py-1.5 text-right tabular-nums" :class="p.status.somente_software ? '' : 'text-muted-foreground'">{{ p.status.somente_software || 0 }}</td>
              <td class="border border-border px-2 py-1.5 text-right tabular-nums" :class="aguardando(p) ? '' : 'text-muted-foreground'">{{ aguardando(p) }}</td>
              <td class="border border-border px-2 py-1.5 text-right tabular-nums" :class="p.status.expirada ? '' : 'text-muted-foreground'">{{ p.status.expirada || 0 }}</td>
              <td class="border border-border px-2 py-1.5 text-right tabular-nums" :class="p.com_atendimento ? '' : 'text-muted-foreground'">
                {{ p.com_atendimento }}<span v-if="p.com_atendimento" class="text-[10px] text-muted-foreground"> ({{ p.atendimentos_hardware }}/{{ p.atendimentos_software }})</span>
              </td>
            </tr>
          </tbody>
          <tfoot v-if="produtos.length" class="sticky bottom-0 bg-muted font-medium">
            <tr data-total-departamento>
              <td class="border border-border px-2 py-1.5" colspan="2">Total — {{ departamentoAtual?.nome }}</td>
              <td class="border border-border px-2 py-1.5 text-right tabular-nums">{{ numeroBR(soma((p) => p.vendidas)) }}</td>
              <td class="border border-border px-2 py-1.5 text-right tabular-nums">{{ numeroBR(soma((p) => p.pediram)) }}</td>
              <td class="border border-border px-2 py-1.5 text-right tabular-nums">{{ pct(soma((p) => p.pediram), soma((p) => p.vendidas)) }}</td>
              <td v-for="o in ORIGENS" :key="o" class="border border-border px-2 py-1.5 text-right tabular-nums">{{ soma((p) => p.origens[o] || 0) }}</td>
              <td class="border border-border px-2 py-1.5 text-right tabular-nums">{{ soma(outras) }}</td>
              <td class="border border-border px-2 py-1.5 text-right tabular-nums">{{ soma((p) => p.status.ativa || 0) }}</td>
              <td class="border border-border px-2 py-1.5 text-right tabular-nums">{{ soma((p) => p.status.somente_software || 0) }}</td>
              <td class="border border-border px-2 py-1.5 text-right tabular-nums">{{ soma(aguardando) }}</td>
              <td class="border border-border px-2 py-1.5 text-right tabular-nums">{{ soma((p) => p.status.expirada || 0) }}</td>
              <td class="border border-border px-2 py-1.5 text-right tabular-nums">{{ soma((p) => p.com_atendimento) }}</td>
            </tr>
          </tfoot>
        </table>
      </div>
      <p class="text-xs text-muted-foreground">
        Mensagem = o cliente escreveu no chat ou por e-mail · SAC = o formulário do site · Devolução = devolução por defeito · Outros = atendimento registrado, cadastro à mão ou marcado pela equipe. Situação e atendimentos contam só os pedidos.
      </p>
    </template>
  </section>
</template>
