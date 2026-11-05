<script setup lang="ts">
// Garantia por produto (28/10/2026) — no visual da aba Produtos da Tabela de
// Preços ("pode usar a nossa Tabela de Preços, aba Produtos, como referência…
// é tudo relacionado à garantia", Eduardo): uma linha por produto da Tabela de
// Preços (o modelo, com as cores juntas), só com colunas de garantia. O
// produto vendido que não está na Tabela de Preços entra pelo SKU. POST
// /api/garantias/resumo com os filtros da barra.
// 29/10/2026 ("sem emojis, igual aos nossos painéis do DaVinci"): as mesmas
// classes da aba Produtos — cabeçalho simples com a explicação no mouse, SKU
// numa linha cortada, números em texto pequeno — e a ordem pelo título da
// coluna.
// 04/11/2026 (as 3 partes do painel): é a aba Resumo, só do admin. As pílulas
// de departamento ficam na página (a pílula escolhida chega em
// `departamento`). Colunas: Vendas (Vendidas, Saldo e a % = gasto ÷ saldo ×
// 100), Atendimento (como pediu: Mensagem, SAC, Devolução) e Manutenção (só o
// Gasto da tela Reembolso — 05/11/2026: "em manutenção deixe somente o
// gasto"; Consertos e Perdidos saíram). Desde 05/11/2026 também: só os
// produtos da Tabela de Preços › Produtos (o vendido fora dela fica só na aba
// Garantia), o Saldo é a conta da rentabilidade (o que sobrou: base −
// comissão − frete − custo) e, com o período pela data da venda, o título diz
// de quais vendas são os números. Clicar na linha (ou em
// Vendidas, Saldo, %) abre todas as garantias do produto (emit 'abrir' com a
// `chave`); o número das outras colunas abre só aqueles pedidos (os `ids`).
import { AlertCircle, ArrowDown, ArrowUp, Loader2 } from 'lucide-vue-next'
import {
  DEPARTAMENTOS,
  corpoDoResumo,
  dataBR,
  errosDaApi,
  numeroBR,
  pctDoGasto,
  reaisBR,
  type ColunaDePedidos,
  type Departamento,
  type FiltrosLista,
  type Resumo,
  type ResumoProduto,
} from '~/lib/garantias'

const props = defineProps<{ filtros: FiltrosLista; departamento: Departamento; versao?: number }>()
const emit = defineEmits<{
  // A linha do produto (`chave`): todas as garantias dele; o número de uma
  // coluna, só os pedidos daquela coluna (`ids`; `coluna` = o nome na tela).
  (e: 'abrir', produto: { nome: string; chave?: string; ids?: number[]; coluna?: string }): void
  // Todos os pedidos de garantia do departamento (sem escolher produto).
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
    erro.value = errosDaApi(e).geral || 'Não consegui carregar o resumo por produto.'
  } finally {
    if (g === geracao) carregando.value = false
  }
}
watch(
  [() => corpoDoResumo(props.filtros), () => props.versao],
  () => { void carregar() },
  { immediate: true, deep: true },
)

// A busca e a ordem ficam na página (v-model): voltam iguais depois de abrir
// as garantias de um produto.
const busca = defineModel<string>('busca', { default: '' })
const ordem = defineModel<{ coluna: string; desc: boolean }>('ordem', { default: () => ({ coluna: 'vendidas', desc: true }) })
const nomeDoDepartamento = computed(() =>
  resumo.value?.departamentos.find((d) => d.chave === props.departamento)?.nome
  || DEPARTAMENTOS.find((d) => d.chave === props.departamento)?.nome
  || props.departamento)
function plano(t: string) {
  return t.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase()
}
// Só os produtos da Tabela de Preços › Produtos (05/11/2026); o rodapé soma
// só eles.
const produtos = computed<ResumoProduto[]>(() => {
  const termo = plano(busca.value.trim())
  return (resumo.value?.produtos || []).filter((p) =>
    p.na_tabela && p.departamento === props.departamento && (!termo || plano(`${p.nome} ${p.skus.join(' ')}`).includes(termo)))
})
// O período pela data da venda (05/11/2026): de quais vendas são os números.
const periodoDasVendas = computed(() => {
  const { periodo, de, ate } = props.filtros
  if (periodo !== 'venda' || (!de && !ate)) return ''
  if (de && ate) return `vendas de ${dataBR(de)} a ${dataBR(ate)}`
  return de ? `vendas a partir de ${dataBR(de)}` : `vendas até ${dataBR(ate)}`
})

// As colunas de número. A explicação (`dica`) aparece ao passar o mouse no
// título ("deixe sem a subinformação e, com o mouse por cima, aparece
// explicando", 29/10/2026).
// `grupo` (29/10/2026, "uma corzinha só para enxergar melhor — não nos
// números, atrás"): cada grupo de colunas com o fundo de uma cor clarinha e o
// título do grupo em cima, como Entrada/Saída/Saldo no Controle de Estoque.
// Os reais (Saldo, % e Gasto) só com `ve_valores` (o servidor só manda para
// admin — e o Resumo é só do admin).
type Grupo = 'venda' | 'atendimento' | 'manutencao'
type Coluna = { chave: string; grupo: Grupo; rotulo: string; dica: string; valor: (p: ResumoProduto) => number; soValores?: true }
// A % (04/11/2026): o gasto sobre o saldo — para ordenar; a tela mostra com
// pctDoGasto (sem saldo, "—").
const taxa = (p: ResumoProduto) => (p.saldo ? (p.gasto || 0) / p.saldo : 0)
const COLUNAS: Coluna[] = [
  { chave: 'vendidas', grupo: 'venda', rotulo: 'Vendidas', dica: 'Vendas com nota fiscal de produto: cada venda é uma garantia.', valor: (p) => p.vendidas },
  { chave: 'saldo', grupo: 'venda', rotulo: 'Saldo', dica: 'O que sobrou das vendas do produto, a conta da planilha de rentabilidade: base − comissão − frete − custo do produto (do Bling), em reais e sem os centavos; o valor exato aparece com o mouse.', valor: (p) => p.saldo || 0, soValores: true },
  { chave: 'pct', grupo: 'venda', rotulo: '%', dica: 'Gasto ÷ Saldo × 100', valor: taxa, soValores: true },
  { chave: 'mensagem', grupo: 'atendimento', rotulo: 'Mensagem', dica: 'O cliente escreveu pedindo a garantia no chat da plataforma ou por e-mail.', valor: (p) => p.origens.mensagem || 0 },
  { chave: 'sac', grupo: 'atendimento', rotulo: 'SAC', dica: 'O cliente preencheu o formulário do SAC no site.', valor: (p) => p.origens.sac || 0 },
  { chave: 'devolucao', grupo: 'atendimento', rotulo: 'Devolução', dica: 'Devolução por defeito (dano funcional, danificado ou manutenção).', valor: (p) => p.origens.devolucao || 0 },
  // Manutenção só com o Gasto (05/11/2026): o conserto e os perdidos continuam
  // dentro dele (as três partes aparecem com o mouse).
  { chave: 'gasto', grupo: 'manutencao', rotulo: 'Gasto R$', dica: 'Dinheiro gasto com quem pediu garantia, da tela Reembolso: conserto (Manutenção) + aparelhos perdidos (Extraviado/Sucata) + reembolso ao cliente. Sem os centavos; o valor exato aparece com o mouse.', valor: (p) => p.gasto || 0, soValores: true },
]
const colunas = computed(() => COLUNAS.filter((c) => !c.soValores || resumo.value?.ve_valores))

function ordenar(chave: string) {
  ordem.value = ordem.value.coluna === chave
    ? { coluna: chave, desc: !ordem.value.desc }
    : { coluna: chave, desc: chave !== 'sku' && chave !== 'nome' }
}
const linhas = computed<ResumoProduto[]>(() => {
  const { coluna, desc } = ordem.value
  const col = colunas.value.find((c) => c.chave === coluna)
  const dir = desc ? -1 : 1
  return [...produtos.value].sort((a, b) => {
    let d = 0
    if (coluna === 'nome') d = a.nome.localeCompare(b.nome, 'pt-BR')
    else if (coluna === 'sku') d = (a.skus[0] || '').localeCompare(b.skus[0] || '', 'pt-BR', { numeric: true })
    else if (col) d = col.valor(a) - col.valor(b)
    return d * dir || b.vendidas - a.vendidas || a.nome.localeCompare(b.nome, 'pt-BR')
  })
})
const sentido = (chave: string) => (ordem.value.coluna === chave ? (ordem.value.desc ? 'descending' : 'ascending') : undefined)
// As colunas no mesmo tamanho (29/10/2026: "umas estão maiores, outras
// menores"): a tabela de largura fixa por coluna — todas as de número com a
// mesma largura, o SKU fixo (cortado com "…", a lista inteira no mouse) e o
// Produto com o que sobrar. A largura mínima é a conta das colunas que estão
// na tela (04/11/2026: nada de número fixo que deixa de caber); abaixo dela, a
// tabela rola para o lado.
const LARGURA_SKU = 112
const LARGURA_PRODUTO = 186
const LARGURA_NUMERO = 77
const larguraMinima = computed(() => `${LARGURA_SKU + LARGURA_PRODUTO + colunas.value.length * LARGURA_NUMERO}px`)
// Os grupos: o título e o fundo de cada um (as classes do Controle de
// Estoque: o título no tom cheio, o nome da coluna em /60 e as células em /40).
const GRUPOS: { chave: Grupo; nome: string; topo: string; titulo: string; celula: string }[] = [
  { chave: 'venda', nome: 'Vendas', topo: 'bg-sky-50 dark:bg-sky-900/20', titulo: 'bg-sky-50/60 dark:bg-sky-900/10', celula: 'bg-sky-50/40 dark:bg-sky-900/5' },
  { chave: 'atendimento', nome: 'Atendimento', topo: 'bg-amber-50 dark:bg-amber-900/20', titulo: 'bg-amber-50/60 dark:bg-amber-900/10', celula: 'bg-amber-50/40 dark:bg-amber-900/5' },
  { chave: 'manutencao', nome: 'Manutenção', topo: 'bg-rose-50 dark:bg-rose-900/20', titulo: 'bg-rose-50/60 dark:bg-rose-900/10', celula: 'bg-rose-50/40 dark:bg-rose-900/5' },
]
const DO_GRUPO = Object.fromEntries(GRUPOS.map((g) => [g.chave, g])) as Record<Grupo, (typeof GRUPOS)[number]>
const colunasDo = (g: Grupo) => colunas.value.filter((c) => c.grupo === g).length
// Grupo sem coluna na tela não tem título (sem os reais, a Manutenção some).
const gruposNaTela = computed(() => GRUPOS.filter((g) => colunasDo(g.chave)))
function soma(campo: (p: ResumoProduto) => number) {
  return produtos.value.reduce((n, p) => n + campo(p), 0)
}
// O Gasto aberto: as três partes dele, da tela Reembolso.
function partesDoGasto(gasto: number, conserto: number, perdidos: number, cliente: number) {
  return `${reaisBR(gasto)}: conserto ${reaisBR(conserto)} + perdidos ${reaisBR(perdidos)} + reembolso ao cliente ${reaisBR(cliente)} — da tela Reembolso`
}
const dicaDoGastoTotal = computed(() => partesDoGasto(
  soma((p) => p.gasto || 0), soma((p) => p.gasto_conserto || 0), soma((p) => p.gasto_perdidos || 0), soma((p) => p.gasto_cliente || 0)))
function abrir(p: ResumoProduto) {
  if (p.vendidas) emit('abrir', { nome: p.nome, chave: p.chave })
}
// O número abre só os pedidos dele (30/10/2026: "se eu clicar em devolução
// aparecem as 4 da devolução, já vem filtrado"). Vendidas, Saldo e % não são
// pedidos: o clique nelas é o da linha (todas as garantias do produto). O
// zero não abre.
function idsDaCelula(p: ResumoProduto, c: Coluna): number[] | null {
  if (c.chave === 'vendidas' || c.chave === 'saldo' || c.chave === 'pct') return null
  return p.pedidos_por_coluna?.[c.chave as ColunaDePedidos] || []
}
function clicarCelula(ev: MouseEvent, p: ResumoProduto, c: Coluna) {
  const ids = idsDaCelula(p, c)
  if (ids === null) return
  ev.stopPropagation()
  if (ids.length) emit('abrir', { nome: p.nome, ids, coluna: c.rotulo })
}
function dicaDaCelula(p: ResumoProduto, c: Coluna) {
  if (c.chave === 'pct') return `gasto ${reaisBR(p.gasto)} ÷ saldo ${reaisBR(p.saldo)} × 100`
  if (c.chave === 'saldo') return reaisBR(p.saldo)
  const ids = idsDaCelula(p, c)
  if (!ids) return undefined
  // O zero tem a dica dele (senão herdaria o "ver as N garantias" da linha).
  const n = ids.length
  if (!n) return 'nenhum pedido nesta coluna'
  const ver = `ver ${n === 1 ? 'o pedido' : `os ${n} pedidos`} de garantia — ${c.rotulo}`
  if (c.chave === 'gasto') return `${ver} · ${partesDoGasto(p.gasto || 0, p.gasto_conserto || 0, p.gasto_perdidos || 0, p.gasto_cliente || 0)}`
  return ver
}
function dicaDaLinha(p: ResumoProduto) {
  if (!p.vendidas) return 'nenhuma garantia'
  return `ver ${p.vendidas === 1 ? 'a garantia' : `as ${p.vendidas} garantias`} de ${p.nome}`
}
// O número na célula: os reais sem os centavos (o exato no mouse); a % com 2 casas.
function textoDaCelula(p: ResumoProduto, c: Coluna) {
  if (c.chave === 'pct') return pctDoGasto(p.gasto, p.saldo)
  if (c.chave === 'saldo' || c.chave === 'gasto') return numeroBR(Math.round(c.valor(p)))
  return numeroBR(c.valor(p))
}
</script>

<template>
  <section class="space-y-3" data-garantia-resumo>
    <p v-if="erro" role="alert" class="flex items-center gap-2 text-sm text-destructive">
      <AlertCircle class="size-4" /> {{ erro }} <button type="button" class="underline" @click="carregar">tentar de novo</button>
    </p>
    <div v-else-if="!resumo" class="flex items-center justify-center gap-2 rounded-lg border py-14 text-sm text-muted-foreground"><Loader2 class="size-4 animate-spin" /> montando o resumo por produto…</div>
    <template v-else>
      <div class="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h3 class="flex items-center gap-2 text-base font-semibold">
            Resumo por produto — {{ nomeDoDepartamento }}
            <span v-if="periodoDasVendas" class="text-sm font-normal text-muted-foreground" data-periodo-vendas>· {{ periodoDasVendas }}</span>
            <Loader2 v-if="carregando" class="size-4 animate-spin text-muted-foreground" />
          </h3>
          <p class="text-xs text-muted-foreground">
            {{ produtos.length }} produto(s) — clique no número para ver só aqueles pedidos · clique na linha para ver todas as garantias do produto · clique no título da coluna para ordenar ·
            <!-- A conta da %, discreta ("bem sutil, só para mim não esquecer", 30/10/2026). -->
            <span title="saldo = base − comissão − frete − custo do produto (a planilha de rentabilidade: o que sobrou) · gasto = tela Reembolso (conserto + perdidos + reembolso ao cliente) de quem pediu garantia" data-calculo>% = gasto ÷ saldo × 100</span>
          </p>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <button type="button" class="btn btn-sm" data-ver-todos @click="emit('todos')">Ver todos os pedidos de garantia</button>
          <input v-model="busca" placeholder="buscar SKU ou produto…" class="w-56 rounded border bg-background px-2 py-1 text-sm" data-busca-produto>
        </div>
      </div>

      <div class="max-h-[max(16rem,calc(100vh-320px))] overflow-auto rounded-lg border">
        <table class="w-full table-fixed border-collapse text-sm" :style="{ minWidth: larguraMinima }" data-produtos>
          <colgroup>
            <col class="w-[112px]">
            <col>
            <col v-for="c in colunas" :key="c.chave" class="w-[77px]">
          </colgroup>
          <thead class="sticky top-0 z-10 bg-muted">
            <tr data-grupos>
              <th colspan="2" class="border-b border-border bg-background"></th>
              <th
                v-for="g in gruposNaTela"
                :key="g.chave"
                :colspan="colunasDo(g.chave)"
                class="whitespace-nowrap border border-border px-1 py-1 text-center text-[11px] font-semibold"
                :class="g.topo"
                :data-grupo="g.chave"
              >{{ g.nome }}</th>
            </tr>
            <tr data-colunas>
              <!-- O cabeçalho num padrão só (29/10/2026: "tudo reto"): só o nome,
                   numa linha; a explicação aparece com o mouse em cima. -->
              <th class="whitespace-nowrap border-b border-border px-2 py-2 text-left text-xs font-medium" title="As cores do modelo, como na Tabela de Preços." :aria-sort="sentido('sku')" data-coluna="sku">
                <button type="button" class="flex items-center gap-1" @click="ordenar('sku')">
                  <span class="truncate">SKU</span><ArrowDown v-if="sentido('sku') === 'descending'" class="size-3 shrink-0" /><ArrowUp v-else-if="sentido('sku')" class="size-3 shrink-0" />
                </button>
              </th>
              <th class="whitespace-nowrap border-b border-border px-2 py-2 text-left text-xs font-medium" title="O modelo na Tabela de Preços › Produtos (o vendido fora dela fica só na aba Garantia)." :aria-sort="sentido('nome')" data-coluna="nome">
                <button type="button" class="flex items-center gap-1" @click="ordenar('nome')">
                  <span class="truncate">Produto</span><ArrowDown v-if="sentido('nome') === 'descending'" class="size-3 shrink-0" /><ArrowUp v-else-if="sentido('nome')" class="size-3 shrink-0" />
                </button>
              </th>
              <th
                v-for="c in colunas"
                :key="c.chave"
                class="overflow-hidden whitespace-nowrap border-b border-border px-0.5 py-2 text-right text-xs font-medium"
                :class="DO_GRUPO[c.grupo].titulo"
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
              <td :colspan="2 + colunas.length" class="py-6 text-center text-muted-foreground" data-sem-produtos>
                {{ busca.trim() ? 'Nenhum produto com essa busca.' : 'Nenhuma venda neste departamento com esses filtros.' }}
                <template v-if="departamento === 'mala'"> A garantia do DaVinci hoje é só da linha Uranyx (celular e eletro).</template>
              </td>
            </tr>
            <tr
              v-for="p in linhas"
              :key="p.chave"
              class="group hover:bg-accent/30"
              :class="p.vendidas ? 'cursor-pointer' : ''"
              :title="dicaDaLinha(p)"
              :data-produto="p.chave"
              @click="abrir(p)"
            >
              <td class="border border-border px-2 py-1.5 font-mono text-xs group-hover:bg-accent/40" :title="p.skus.join(',')">
                <span class="block truncate">{{ p.skus.join(',') || '—' }}</span>
              </td>
              <td class="border border-border px-2 py-1.5 text-xs group-hover:bg-accent/40">
                {{ p.nome }}
              </td>
              <td
                v-for="c in colunas"
                :key="c.chave"
                class="border border-border px-1.5 py-1.5 text-right text-xs tabular-nums group-hover:bg-accent/40"
                :class="[
                  DO_GRUPO[c.grupo].celula,
                  c.valor(p) || c.chave === 'vendidas' ? '' : 'text-muted-foreground',
                  idsDaCelula(p, c)?.length ? 'hover:text-primary hover:underline' : idsDaCelula(p, c) ? 'cursor-default' : '',
                ]"
                :title="dicaDaCelula(p, c)"
                :data-celula="c.chave"
                @click="clicarCelula($event, p, c)"
              >{{ textoDaCelula(p, c) }}</td>
            </tr>
          </tbody>
          <tfoot v-if="linhas.length" class="sticky bottom-0 bg-muted font-medium">
            <tr data-total-departamento>
              <td class="border border-border px-2 py-1.5 text-xs" colspan="2">{{ busca.trim() ? `Total da busca (${produtos.length} ${produtos.length === 1 ? 'produto' : 'produtos'})` : `Total — ${nomeDoDepartamento}` }}</td>
              <td v-for="c in colunas" :key="c.chave" class="border border-border px-1.5 py-1.5 text-right text-xs tabular-nums" :data-total="c.chave">
                <template v-if="c.chave === 'pct'">{{ pctDoGasto(soma((p) => p.gasto || 0), soma((p) => p.saldo || 0)) }}</template>
                <span v-else-if="c.chave === 'saldo'" :title="reaisBR(soma(c.valor))">{{ numeroBR(Math.round(soma(c.valor))) }}</span>
                <span v-else-if="c.chave === 'gasto'" :title="dicaDoGastoTotal">{{ numeroBR(Math.round(soma(c.valor))) }}</span>
                <template v-else>{{ numeroBR(soma(c.valor)) }}</template>
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
    </template>
  </section>
</template>
