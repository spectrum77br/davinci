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
  reaisBR,
  type FiltrosLista,
  type Resumo,
  type ResumoProduto,
} from '~/lib/garantias'

const props = defineProps<{ filtros: FiltrosLista; versao?: number }>()
const emit = defineEmits<{
  // A linha do produto: os pedidos dela na lista; o número de uma coluna, só
  // os pedidos daquela coluna (`coluna` = o nome dela na tela).
  (e: 'abrir', produto: { nome: string; ids: number[]; coluna?: string }): void
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
// `grupo` (29/10/2026, "uma corzinha só para enxergar melhor — não nos
// números, atrás"): cada grupo de colunas com o fundo de uma cor clarinha e o
// título do grupo em cima, como Entrada/Saída/Saldo no Controle de Estoque.
// `perdas` (30/10/2026, "o que realmente foi perdido e o que está na
// manutenção — o que foi gasto de dinheiro"): o aparelho de quem pediu na
// tela Devoluções; o Gasto em reais só para admin (`soValores`).
type Grupo = 'venda' | 'como' | 'situacao' | 'atend' | 'perdas'
type Coluna = { chave: string; grupo: Grupo; rotulo: string; dica: string; valor: (p: ResumoProduto) => number; soValores?: true }
// A % é dos que SEGUIRAM (30/10/2026: "no chat tem muitos que pediram a
// garantia, mas não foi para frente"; reclamação não conta); a de quem pediu
// fica na dica.
const taxa = (p: ResumoProduto) => (p.vendidas ? p.seguiram / p.vendidas : 0)
const COLUNAS: Coluna[] = [
  { chave: 'vendidas', grupo: 'venda', rotulo: 'Vendidas', dica: 'Vendas com nota fiscal de produto: cada venda é uma garantia.', valor: (p) => p.vendidas },
  { chave: 'pediram', grupo: 'venda', rotulo: 'Pediram', dica: 'Clientes que pediram garantia: por mensagem, SAC, devolução por defeito, atendimento registrado ou cadastro à mão. Inclui quem só falou no chat e não mandou o aparelho.', valor: (p) => p.pediram },
  { chave: 'seguiram', grupo: 'venda', rotulo: 'Seguiram', dica: 'Dos que pediram: a garantia foi adiante — o aparelho voltou (tela Devoluções), está voltando (devolução aceita na plataforma) ou teve reembolso ao cliente. Só pedir no chat, reclamação, disputa sem decisão e devolução cancelada não contam. É a base da %.', valor: (p) => p.seguiram || 0 },
  { chave: 'pct', grupo: 'venda', rotulo: '%', dica: 'De cada 100 vendidas, quantas seguiram pela garantia (Seguiram ÷ Vendidas). Quem só pediu pelo chat não entra; a % de quem pediu aparece com o mouse no número.', valor: taxa },
  { chave: 'mensagem', grupo: 'como', rotulo: 'Mensagem', dica: 'O cliente escreveu pedindo a garantia no chat da plataforma ou por e-mail.', valor: (p) => p.origens.mensagem || 0 },
  { chave: 'sac', grupo: 'como', rotulo: 'SAC', dica: 'O cliente preencheu o formulário do SAC no site.', valor: (p) => p.origens.sac || 0 },
  { chave: 'devolucao', grupo: 'como', rotulo: 'Devolução', dica: 'Devolução por defeito (dano funcional, danificado ou manutenção).', valor: (p) => p.origens.devolucao || 0 },
  { chave: 'outros', grupo: 'como', rotulo: 'Outros', dica: 'Atendimento registrado, garantia cadastrada à mão ou marcada pela equipe.', valor: (p) => (p.origens.atendimento || 0) + (p.origens.manual || 0) + (p.origens.pessoa || 0) },
  { chave: 'ativa', grupo: 'situacao', rotulo: 'Ativa', dica: 'Dos que pediram: garantia de hardware e de software dentro do prazo.', valor: (p) => p.status.ativa || 0 },
  { chave: 'somente_software', grupo: 'situacao', rotulo: 'Só software', dica: 'Dos que pediram: o hardware já venceu; só o software ainda está coberto.', valor: (p) => p.status.somente_software || 0 },
  { chave: 'aguardando', grupo: 'situacao', rotulo: 'Aguardando', dica: 'Dos que pediram: a garantia ainda não começou a contar — falta a data de entrega no DaVinci (inclui os já entregues no Bling).', valor: (p) => (p.status.aguardando_entrega || 0) + (p.status.entregue_sem_data || 0) },
  { chave: 'expirada', grupo: 'situacao', rotulo: 'Expirada', dica: 'Dos que pediram: a garantia já venceu.', valor: (p) => p.status.expirada || 0 },
  { chave: 'atendimentos', grupo: 'atend', rotulo: 'Atendidos', dica: 'Pedidos com atendimento registrado na garantia (entre parênteses: de hardware / de software).', valor: (p) => p.com_atendimento },
  { chave: 'no_tecnico', grupo: 'perdas', rotulo: 'No técnico', dica: 'Dos que pediram: o aparelho está na manutenção agora (tela Devoluções, condição Manutenção; o prazo é de 30 dias).', valor: (p) => p.no_tecnico || 0 },
  { chave: 'consertados', grupo: 'perdas', rotulo: 'Consertos', dica: 'Dos que pediram: o aparelho passou pela manutenção e voltou ao estoque (Novo ou Usado).', valor: (p) => p.consertados || 0 },
  { chave: 'perdidos', grupo: 'perdas', rotulo: 'Perdidos', dica: 'Dos que pediram: o aparelho não volta a ser vendido — Extraviado (sumiu no caminho) ou Sucata.', valor: (p) => p.perdidos || 0 },
  { chave: 'gasto', grupo: 'perdas', rotulo: 'Gasto R$', dica: 'Dinheiro gasto com esses aparelhos: o conserto pago ao técnico (Custo manutenção da tela Devoluções — o R$ 1 lançado só para tirar da Manutenção não conta) mais o custo dos aparelhos perdidos. Sem os centavos; o valor exato aparece com o mouse.', valor: (p) => p.gasto || 0, soValores: true },
]
// O Gasto em reais só para admin (o servidor nem manda para os outros).
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
    return d * dir || b.pediram - a.pediram || b.vendidas - a.vendidas || a.nome.localeCompare(b.nome, 'pt-BR')
  })
})
const sentido = (chave: string) => (ordem.value.coluna === chave ? (ordem.value.desc ? 'descending' : 'ascending') : undefined)
// As colunas no mesmo tamanho (29/10/2026: "umas estão maiores, outras
// menores"): a tabela de largura fixa por coluna — todas as de número com a
// mesma largura, o SKU fixo (cortado com "…", a lista inteira no mouse) e o
// Produto com o que sobrar. Abaixo da largura mínima, ela rola para o lado.
const LARGURA_NUMERO = 'w-[77px]'
// A dica da %: quantas vezes a média do departamento (os mesmos filtros).
const taxaDoDepartamento = computed(() => {
  const d = departamentoAtual.value
  return d && d.vendidas ? d.seguiram / d.vendidas : 0
})
// Os grupos: o título e o fundo de cada um (as classes do Controle de
// Estoque: o título no tom cheio, o nome da coluna em /60 e as células em /40).
const GRUPOS: { chave: Grupo; nome: string; topo: string; titulo: string; celula: string }[] = [
  { chave: 'venda', nome: 'Vendas', topo: 'bg-sky-50 dark:bg-sky-900/20', titulo: 'bg-sky-50/60 dark:bg-sky-900/10', celula: 'bg-sky-50/40 dark:bg-sky-900/5' },
  { chave: 'como', nome: 'Como pediu', topo: 'bg-amber-50 dark:bg-amber-900/20', titulo: 'bg-amber-50/60 dark:bg-amber-900/10', celula: 'bg-amber-50/40 dark:bg-amber-900/5' },
  { chave: 'situacao', nome: 'Situação de quem pediu', topo: 'bg-emerald-50 dark:bg-emerald-900/20', titulo: 'bg-emerald-50/60 dark:bg-emerald-900/10', celula: 'bg-emerald-50/40 dark:bg-emerald-900/5' },
  { chave: 'atend', nome: 'Atendimento', topo: 'bg-gray-100 dark:bg-gray-800/40', titulo: 'bg-gray-50 dark:bg-gray-800/20', celula: 'bg-gray-50/60 dark:bg-gray-800/10' },
  { chave: 'perdas', nome: 'Manutenção e perdas', topo: 'bg-rose-50 dark:bg-rose-900/20', titulo: 'bg-rose-50/60 dark:bg-rose-900/10', celula: 'bg-rose-50/40 dark:bg-rose-900/5' },
]
const DO_GRUPO = Object.fromEntries(GRUPOS.map((g) => [g.chave, g])) as Record<Grupo, (typeof GRUPOS)[number]>
const colunasDo = (g: Grupo) => colunas.value.filter((c) => c.grupo === g).length
// A largura mínima: SKU + Produto + as de número (o Gasto só para admin);
// as de número em 77px para a tabela caber inteira numa tela de 1920.
const larguraMinima = computed(() => (colunas.value.length > 16 ? 'min-w-[1607px]' : 'min-w-[1530px]'))
function dicaDaPct(p: ResumoProduto) {
  const pediram = `pediram: ${pct(p.pediram, p.vendidas)} (${numeroBR(p.pediram)} de ${numeroBR(p.vendidas)})`
  if (p.vendidas < 10) return `poucas vendas (menos de 10): a % ainda diz pouco · ${pediram}`
  const media = taxaDoDepartamento.value
  const dep = departamentoAtual.value
  if (!media || !dep) return pediram
  const vezes = taxa(p) / media
  const quanto = vezes >= 1 ? `${vezes.toFixed(1).replace('.', ',')}x a média` : 'abaixo da média'
  return `${quanto} do ${dep.nome} (${pct(dep.seguiram, dep.vendidas)}) · ${pediram}`
}
function soma(campo: (p: ResumoProduto) => number) {
  return produtos.value.reduce((n, p) => n + campo(p), 0)
}
function abrir(p: ResumoProduto) {
  if (p.pedidos.length) emit('abrir', { nome: p.nome, ids: p.pedidos })
}
// O número abre só os pedidos dele (30/10/2026: "se eu clicar em devolução
// aparecem as 4 da devolução, já vem filtrado"). Vendidas e % não são pedidos:
// o clique nelas é o da linha (todos os pedidos do produto). O zero não abre.
function idsDaCelula(p: ResumoProduto, c: Coluna): number[] | null {
  if (c.chave === 'vendidas' || c.chave === 'pct') return null
  if (c.chave === 'pediram') return p.pedidos
  return p.pedidos_por_coluna?.[c.chave] || []
}
function clicarCelula(ev: MouseEvent, p: ResumoProduto, c: Coluna) {
  const ids = idsDaCelula(p, c)
  if (ids === null) return
  ev.stopPropagation()
  if (ids.length) emit('abrir', { nome: p.nome, ids, coluna: c.chave === 'pediram' ? undefined : c.rotulo })
}
function dicaDaCelula(p: ResumoProduto, c: Coluna) {
  if (c.chave === 'pct') return dicaDaPct(p)
  const ids = idsDaCelula(p, c)
  if (!ids) return undefined
  // O zero tem a dica dele (senão herdaria o "ver os N pedidos" da linha).
  const n = ids.length
  if (!n) return 'nenhum pedido nesta coluna'
  const ver = `ver ${n === 1 ? 'o pedido' : `os ${n} pedidos`} de garantia — ${c.chave === 'pediram' ? 'todos do produto' : c.rotulo}`
  return [ver, ...detalheDaCelula(p, c)].join(' · ')
}
// O que a célula de Manutenção e perdas diz a mais com o mouse.
function detalheDaCelula(p: ResumoProduto, c: Coluna): string[] {
  const ve = !!resumo.value?.ve_valores
  if (c.chave === 'no_tecnico') {
    return [
      p.no_tecnico_vencidos ? `${p.no_tecnico_vencidos} com o prazo de 30 dias vencido` : '',
      ve && p.valor_no_tecnico ? `${reaisBR(p.valor_no_tecnico)} em aparelhos no técnico` : '',
    ].filter(Boolean)
  }
  if (c.chave === 'consertados' && p.consertados_sem_custo) return [`${p.consertados_sem_custo} sem o custo do conserto lançado`]
  if (c.chave === 'perdidos' && ve && p.gasto_perdidos) return [`${reaisBR(p.gasto_perdidos)} em aparelhos perdidos`]
  if (c.chave === 'gasto') return [`${reaisBR(p.gasto)}: conserto ${reaisBR(p.gasto_conserto)} + aparelhos perdidos ${reaisBR(p.gasto_perdidos)}`]
  return []
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
          :title="`seguiram: ${numeroBR(d.seguiram)} de ${numeroBR(d.vendidas)} vendidas · pediram garantia: ${numeroBR(d.pediram)} (${pct(d.pediram, d.vendidas)})`"
          :data-departamento="d.chave"
          @click="departamento = d.chave"
        >
          {{ d.nome }} <span class="tabular-nums" data-pct>· {{ pct(d.seguiram, d.vendidas) }}</span>
          <span class="text-xs tabular-nums opacity-80">({{ numeroBR(d.seguiram) }} de {{ numeroBR(d.vendidas) }})</span>
        </button>
        <!-- A conta da %, discreta ("bem sutil, só para mim não esquecer", 30/10/2026). -->
        <span class="self-center text-xs text-muted-foreground" data-calculo>% = seguiram ÷ vendidas · seguiram = o aparelho voltou, está voltando ou teve reembolso (só pedir no chat ou reclamar não conta)</span>
        <Loader2 v-if="carregando" class="size-4 animate-spin self-center text-muted-foreground" />
      </div>

      <div class="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h3 class="text-base font-semibold">Garantia por produto — {{ departamentoAtual?.nome || '—' }}</h3>
          <p class="text-xs text-muted-foreground">
            {{ produtos.length }} produto(s) — clique no número para ver só aqueles pedidos · clique na linha para ver todos os pedidos do produto · clique no título da coluna para ordenar
          </p>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <button type="button" class="btn btn-sm" data-ver-todos @click="emit('todos')">Ver todos os pedidos de garantia</button>
          <input v-model="busca" placeholder="buscar SKU ou produto…" class="w-56 rounded border bg-background px-2 py-1 text-sm" data-busca-produto>
        </div>
      </div>

      <div class="max-h-[max(16rem,calc(100vh-320px))] overflow-auto rounded-lg border">
        <table class="w-full table-fixed border-collapse text-sm" :class="larguraMinima" data-produtos>
          <colgroup>
            <col class="w-[112px]">
            <col>
            <col v-for="c in colunas" :key="c.chave" :class="LARGURA_NUMERO">
          </colgroup>
          <thead class="sticky top-0 z-10 bg-muted">
            <tr data-grupos>
              <th colspan="2" class="border-b border-border bg-background"></th>
              <th
                v-for="g in GRUPOS"
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
              <th class="whitespace-nowrap border-b border-border px-2 py-2 text-left text-xs font-medium" title="O modelo na Tabela de Preços (o vendido que não está nela entra pelo SKU)." :aria-sort="sentido('nome')" data-coluna="nome">
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
              <td :colspan="2 + colunas.length" class="py-6 text-center text-muted-foreground">{{ busca.trim() ? 'Nenhum produto com essa busca.' : 'Nenhuma venda neste departamento com esses filtros.' }}</td>
            </tr>
            <tr
              v-for="p in linhas"
              :key="p.chave"
              class="group hover:bg-accent/30"
              :class="p.pedidos.length ? 'cursor-pointer' : ''"
              :title="p.pedidos.length ? `ver os ${p.pedidos.length} pedidos de garantia de ${p.nome}` : 'nenhum pedido de garantia'"
              :data-produto="p.chave"
              @click="abrir(p)"
            >
              <td class="border border-border px-2 py-1.5 font-mono text-xs group-hover:bg-accent/40" :title="p.skus.join(',')">
                <span class="block truncate">{{ p.skus.join(',') || '—' }}</span>
              </td>
              <td class="border border-border px-2 py-1.5 text-xs group-hover:bg-accent/40">
                {{ p.nome }}
                <span v-if="!p.na_tabela" class="ml-1 whitespace-nowrap rounded bg-muted px-1 text-[10px] text-muted-foreground" title="vendido, mas sem linha na Tabela de Preços">fora da Tabela de Preços</span>
              </td>
              <td
                v-for="c in colunas"
                :key="c.chave"
                class="border border-border px-1.5 py-1.5 text-right text-xs tabular-nums group-hover:bg-accent/40"
                :class="[
                  DO_GRUPO[c.grupo].celula,
                  c.chave === 'pediram' ? 'font-semibold' : '',
                  c.valor(p) || c.chave === 'vendidas' ? '' : 'text-muted-foreground',
                  c.chave === 'pct' && p.vendidas < 10 ? 'text-muted-foreground' : '',
                  idsDaCelula(p, c)?.length ? 'hover:text-primary hover:underline' : idsDaCelula(p, c) ? 'cursor-default' : '',
                ]"
                :title="dicaDaCelula(p, c)"
                :data-celula="c.chave"
                @click="clicarCelula($event, p, c)"
              >
                <template v-if="c.chave === 'pct'">{{ pct(p.seguiram, p.vendidas) }}</template>
                <template v-else-if="c.chave === 'atendimentos'">{{ p.com_atendimento }}<span v-if="p.com_atendimento" class="text-muted-foreground"> ({{ p.atendimentos_hardware }} / {{ p.atendimentos_software }})</span></template>
                <template v-else-if="c.chave === 'gasto'">{{ numeroBR(Math.round(c.valor(p))) }}</template>
                <template v-else>{{ numeroBR(c.valor(p)) }}</template>
              </td>
            </tr>
          </tbody>
          <tfoot v-if="linhas.length" class="sticky bottom-0 bg-muted font-medium">
            <tr data-total-departamento>
              <td class="border border-border px-2 py-1.5 text-xs" colspan="2">{{ busca.trim() ? `Total da busca (${produtos.length} ${produtos.length === 1 ? 'produto' : 'produtos'})` : `Total — ${departamentoAtual?.nome}` }}</td>
              <td v-for="c in colunas" :key="c.chave" class="border border-border px-1.5 py-1.5 text-right text-xs tabular-nums">
                <template v-if="c.chave === 'pct'">{{ pct(soma((p) => p.seguiram), soma((p) => p.vendidas)) }}</template>
                <span v-else-if="c.chave === 'gasto'" :title="reaisBR(soma(c.valor))">{{ numeroBR(Math.round(soma(c.valor))) }}</span>
                <template v-else>{{ numeroBR(soma(c.valor)) }}</template>
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
    </template>
  </section>
</template>
