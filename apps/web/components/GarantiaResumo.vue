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
// 29/10/2026 ("sem emojis, igual aos nossos painéis do DaVinci"): as mesmas
// classes da aba Produtos — cabeçalho simples com a explicação miúda embaixo
// (como os kits), SKU numa linha cortada em 20 letras, números em texto
// pequeno — e a ordem pelo título da coluna.
import { AlertCircle, ArrowDown, ArrowUp, Loader2 } from 'lucide-vue-next'
import {
  corpoDoResumo,
  errosDaApi,
  numeroBR,
  pct,
  type FiltrosLista,
  type Resumo,
  type ResumoProduto,
} from '~/lib/garantias'

const props = defineProps<{ filtros: FiltrosLista; versao?: number }>()
const emit = defineEmits<{
  // A linha do produto: os pedidos dela na lista.
  (e: 'abrir', produto: { nome: string; ids: number[] }): void
  // Todos os pedidos de garantia na lista (sem escolher produto).
  (e: 'todos'): void
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

// O departamento (a pílula), a busca e a ordem ficam na página (v-model):
// voltam iguais depois de abrir os pedidos de um produto.
const departamento = defineModel<string>('departamento', { default: '' })
const busca = defineModel<string>('busca', { default: '' })
const ordem = defineModel<{ coluna: string; desc: boolean }>('ordem', { default: () => ({ coluna: 'pediram', desc: true }) })
const departamentoAtual = computed(() => {
  const deps = resumo.value?.departamentos || []
  return deps.find((d) => d.chave === departamento.value) || deps[0] || null
})
function plano(t: string) {
  return t.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase()
}
const produtos = computed<ResumoProduto[]>(() => {
  const dep = departamentoAtual.value?.chave
  const termo = plano(busca.value.trim())
  return (resumo.value?.produtos || []).filter((p) =>
    p.departamento === dep && (!termo || plano(`${p.nome} ${p.skus.join(' ')}`).includes(termo)))
})

// As colunas de número. A explicação (`dica`) aparece ao passar o mouse no
// título ("deixe sem a subinformação e, com o mouse por cima, aparece
// explicando", 29/10/2026).
type Coluna = { chave: string; rotulo: string; dica: string; valor: (p: ResumoProduto) => number }
const taxa = (p: ResumoProduto) => (p.vendidas ? p.pediram / p.vendidas : 0)
const COLUNAS: Coluna[] = [
  { chave: 'vendidas', rotulo: 'Vendidas', dica: 'Vendas com nota fiscal de produto: cada venda é uma garantia.', valor: (p) => p.vendidas },
  { chave: 'pediram', rotulo: 'Pediram', dica: 'Clientes que pediram garantia: por mensagem, SAC, devolução por defeito, atendimento registrado ou cadastro à mão.', valor: (p) => p.pediram },
  { chave: 'pct', rotulo: '%', dica: 'De cada 100 vendidas, quantas pediram garantia.', valor: taxa },
  { chave: 'mensagem', rotulo: 'Mensagem', dica: 'O cliente escreveu pedindo a garantia no chat da plataforma ou por e-mail.', valor: (p) => p.origens.mensagem || 0 },
  { chave: 'sac', rotulo: 'SAC', dica: 'O cliente preencheu o formulário do SAC no site.', valor: (p) => p.origens.sac || 0 },
  { chave: 'devolucao', rotulo: 'Devolução', dica: 'Devolução por defeito (dano funcional, danificado ou manutenção).', valor: (p) => p.origens.devolucao || 0 },
  { chave: 'outros', rotulo: 'Outros', dica: 'Atendimento registrado, garantia cadastrada à mão ou marcada pela equipe.', valor: (p) => (p.origens.atendimento || 0) + (p.origens.manual || 0) + (p.origens.pessoa || 0) },
  { chave: 'ativa', rotulo: 'Ativa', dica: 'Dos que pediram: garantia de hardware e de software dentro do prazo.', valor: (p) => p.status.ativa || 0 },
  { chave: 'somente_software', rotulo: 'Só software', dica: 'Dos que pediram: o hardware já venceu; só o software ainda está coberto.', valor: (p) => p.status.somente_software || 0 },
  { chave: 'aguardando', rotulo: 'Aguardando', dica: 'Dos que pediram: a garantia ainda não começou a contar — falta a data de entrega no DaVinci (inclui os já entregues no Bling).', valor: (p) => (p.status.aguardando_entrega || 0) + (p.status.entregue_sem_data || 0) },
  { chave: 'expirada', rotulo: 'Expirada', dica: 'Dos que pediram: a garantia já venceu.', valor: (p) => p.status.expirada || 0 },
  { chave: 'atendimentos', rotulo: 'Atendidos', dica: 'Pedidos com atendimento registrado na garantia (entre parênteses: de hardware / de software).', valor: (p) => p.com_atendimento },
]

function ordenar(chave: string) {
  ordem.value = ordem.value.coluna === chave
    ? { coluna: chave, desc: !ordem.value.desc }
    : { coluna: chave, desc: chave !== 'sku' && chave !== 'nome' }
}
const linhas = computed<ResumoProduto[]>(() => {
  const { coluna, desc } = ordem.value
  const col = COLUNAS.find((c) => c.chave === coluna)
  const dir = desc ? -1 : 1
  return [...produtos.value].sort((a, b) => {
    let d = 0
    if (coluna === 'nome') d = a.nome.localeCompare(b.nome, 'pt-BR')
    else if (coluna === 'sku') d = (a.skus[0] || '').localeCompare(b.skus[0] || '', 'pt-BR', { numeric: true })
    else if (col) d = col.valor(a) - col.valor(b)
    return d * dir || b.pediram - a.pediram || b.vendidas - a.vendidas || a.nome.localeCompare(b.nome, 'pt-BR')
  })
})
const sentido = (chave: string) => (ordem.value.coluna === chave ? (ordem.value.desc ? 'descending' : 'ascending') : undefined)
// As colunas no mesmo tamanho (29/10/2026: "umas estão maiores, outras
// menores"): a tabela de largura fixa por coluna — todas as de número com a
// mesma largura, o SKU fixo (cortado com "…", a lista inteira no mouse) e o
// Produto com o que sobrar. Abaixo da largura mínima, ela rola para o lado.
const LARGURA_NUMERO = 'w-[81px]'
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
            {{ produtos.length }} produto(s) — clique na linha para ver os pedidos de garantia daquele produto · clique no título da coluna para ordenar
          </p>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <button type="button" class="btn btn-sm" data-ver-todos @click="emit('todos')">Ver todos os pedidos de garantia</button>
          <input v-model="busca" placeholder="buscar SKU ou produto…" class="w-56 rounded border bg-background px-2 py-1 text-sm" data-busca-produto>
        </div>
      </div>

      <div class="max-h-[max(16rem,calc(100vh-320px))] overflow-auto rounded-lg border">
        <table class="w-full min-w-[1270px] table-fixed border-collapse text-sm" data-produtos>
          <colgroup>
            <col class="w-[112px]">
            <col>
            <col v-for="c in COLUNAS" :key="c.chave" :class="LARGURA_NUMERO">
          </colgroup>
          <thead class="sticky top-0 z-10 bg-muted">
            <tr data-colunas>
              <!-- O cabeçalho num padrão só (29/10/2026: "tudo reto"): só o nome,
                   numa linha; a explicação aparece com o mouse em cima. -->
              <th class="whitespace-nowrap border-b border-border px-2 py-2 text-left text-xs font-medium" title="As cores do modelo, como na Tabela de Preços." :aria-sort="sentido('sku')" data-coluna="sku">
                <button type="button" class="flex items-center gap-1" @click="ordenar('sku')">
                  <span class="truncate">SKU</span><ArrowDown v-if="sentido('sku') === 'descending'" class="size-3 shrink-0" /><ArrowUp v-else-if="sentido('sku')" class="size-3 shrink-0" />
                </button>
              </th>
              <th class="whitespace-nowrap border-b border-border px-2 py-2 text-left text-xs font-medium" title="O modelo na Tabela de Preços (o vendido que não está nela entra pelo SKU)." :aria-sort="sentido('nome')" data-coluna="nome">
                <button type="button" class="flex items-center gap-1" @click="ordenar('nome')">
                  <span class="truncate">Produto</span><ArrowDown v-if="sentido('nome') === 'descending'" class="size-3 shrink-0" /><ArrowUp v-else-if="sentido('nome')" class="size-3 shrink-0" />
                </button>
              </th>
              <th
                v-for="c in COLUNAS"
                :key="c.chave"
                class="overflow-hidden whitespace-nowrap border-b border-border px-1 py-2 text-right text-xs font-medium"
                :title="c.dica"
                :aria-sort="sentido(c.chave)"
                :data-coluna="c.chave"
              >
                <button type="button" class="flex w-full items-center justify-end gap-1" @click="ordenar(c.chave)">
                  <ArrowDown v-if="sentido(c.chave) === 'descending'" class="size-3 shrink-0" /><ArrowUp v-else-if="sentido(c.chave)" class="size-3 shrink-0" /><span class="min-w-0 truncate">{{ c.rotulo }}</span>
                </button>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!linhas.length">
              <td :colspan="2 + COLUNAS.length" class="py-6 text-center text-muted-foreground">{{ busca.trim() ? 'Nenhum produto com essa busca.' : 'Nenhuma venda neste departamento com esses filtros.' }}</td>
            </tr>
            <tr
              v-for="p in linhas"
              :key="p.chave"
              class="hover:bg-accent/30"
              :class="p.pedidos.length ? 'cursor-pointer' : ''"
              :title="p.pedidos.length ? `ver os ${p.pedidos.length} pedidos de garantia de ${p.nome}` : 'nenhum pedido de garantia'"
              :data-produto="p.chave"
              @click="abrir(p)"
            >
              <td class="border border-border px-2 py-1.5 font-mono text-xs" :title="p.skus.join(',')">
                <span class="block truncate">{{ p.skus.join(',') || '—' }}</span>
              </td>
              <td class="border border-border px-2 py-1.5 text-xs">
                {{ p.nome }}
                <span v-if="!p.na_tabela" class="ml-1 whitespace-nowrap rounded bg-muted px-1 text-[10px] text-muted-foreground" title="vendido, mas sem linha na Tabela de Preços">fora da Tabela de Preços</span>
              </td>
              <td
                v-for="c in COLUNAS"
                :key="c.chave"
                class="border border-border px-1.5 py-1.5 text-right text-xs tabular-nums"
                :class="[
                  c.chave === 'pediram' ? 'font-semibold' : '',
                  c.valor(p) || c.chave === 'vendidas' ? '' : 'text-muted-foreground',
                  c.chave === 'pct' && p.vendidas < 10 ? 'text-muted-foreground' : '',
                ]"
                :title="c.chave === 'pct' && p.vendidas < 10 ? 'poucas vendas (menos de 10): a % ainda diz pouco' : undefined"
              >
                <template v-if="c.chave === 'pct'">{{ pct(p.pediram, p.vendidas) }}</template>
                <template v-else-if="c.chave === 'atendimentos'">{{ p.com_atendimento }}<span v-if="p.com_atendimento" class="text-muted-foreground"> ({{ p.atendimentos_hardware }} / {{ p.atendimentos_software }})</span></template>
                <template v-else>{{ numeroBR(c.valor(p)) }}</template>
              </td>
            </tr>
          </tbody>
          <tfoot v-if="linhas.length" class="sticky bottom-0 bg-muted font-medium">
            <tr data-total-departamento>
              <td class="border border-border px-2 py-1.5 text-xs" colspan="2">{{ busca.trim() ? `Total da busca (${produtos.length} ${produtos.length === 1 ? 'produto' : 'produtos'})` : `Total — ${departamentoAtual?.nome}` }}</td>
              <td v-for="c in COLUNAS" :key="c.chave" class="border border-border px-1.5 py-1.5 text-right text-xs tabular-nums">
                <template v-if="c.chave === 'pct'">{{ pct(soma((p) => p.pediram), soma((p) => p.vendidas)) }}</template>
                <template v-else>{{ numeroBR(soma(c.valor)) }}</template>
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
    </template>
  </section>
</template>
