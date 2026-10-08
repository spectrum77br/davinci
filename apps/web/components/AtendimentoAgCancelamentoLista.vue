<script lang="ts">
// Lista "Ag. cancelamento" da Caixa (Atendimento, item 4, fase 4b,
// 05/10/2026): os pedidos em "Aguardando Cancelamento" (83955) no Bling dos
// últimos 60 dias, COM ou SEM conversa — só 27 dos 78 pedidos sem estoque
// tinham conversa no DaVinci, então a troca é pelo PEDIDO. Abre num diálogo
// pelo botão do cabeçalho da lista (AtendimentoLista).
//
// GET /api/atendimento/ag-cancelamento (routers/atendimento_troca.py, quem
// vê a caixa lê; SEM Bling): para cada pedido o porquê (o mesmo bloco do
// painel, `AgCancelamentoOut`, desenhado pela mesma `leituraAgCancelamento`
// do cartão — para quem não vê a Margem, o motivo dela vem "em análise"), os
// itens, o prazo de envio na plataforma, a conversa (botão "Abrir conversa")
// e, na falta de estoque com a chave ligada, as sugestões de troca
// (AtendimentoTrocaSugestoes). Para quem mexe (`useAcessoDaTroca`), o
// "Trocar" e o "enviar oferta" das sugestões (fases 4c e 4d, 08/10/2026) e
// o "Retomar" da troca aberta; o diálogo da troca (AtendimentoTroca) abre
// por cima desta lista, que se relê quando ele fecha depois de uma escrita.
// TROCAS PARADAS NO MEIO (fase 4c, 08/10/2026): a troca que parou depois de
// tirar o pedido de 83955 (o PATCH 6 que levou 429 deixa o pedido em
// Atendido; o espelho vira 9) some da lista acima — ela é só dos 83955. Por
// isso a lista lê também GET /trocas?abertas=true e mostra, numa seção
// própria no topo, as abertas cujo pedido não está entre os de cima, com o
// Retomar (quem só lê vê o estado).
//
// Este bloco (não o setup) é o módulo dos ajudantes puros, testados em
// tests/atendimento-troca-sugestoes.cjs — por isso só importa TIPO.
import type { AgCancelamento, SugestoesTroca } from '~/components/AtendimentoPedido.vue'
import type { Troca } from '~/components/AtendimentoTroca.vue'

export const URL_AG_CANCELAMENTO = '/api/atendimento/ag-cancelamento'

// `ItemPedidoAgOut`.
export type ItemPedidoAg = {
  sku: string
  descricao: string | null
  quantidade: number | null
}
// `PedidoAgCancelamentoOut`.
export type PedidoAgCancelamento = {
  numero: string
  numeroloja: string | null
  bling_id: number | null
  loja: string | null
  plataforma: string | null
  conta: string | null
  data: string | null
  // `bling_orders.marketplace_ship_deadline`.
  prazo_envio: string | null
  motivo: AgCancelamento
  itens: ItemPedidoAg[]
  // A conversa principal (null = o pedido não tem conversa no DaVinci).
  conversa_id: string | null
  sugestoes_troca: SugestoesTroca | null
}
// `ListaAgCancelamentoOut`.
export type ListaAgCancelamento = {
  pedidos: PedidoAgCancelamento[]
  total: number
  // Quantos por motivo, antes do filtro — os contadores dos filtros.
  por_codigo: Record<string, number>
  desde: string
  sugestoes_ativas: boolean
  ve_custo: boolean
  gerado_em: string
}

// Os filtros por motivo, na ordem: o que pede ação da equipe primeiro, a
// trava interna da Margem (não é cancelamento) no fim — e, para quem não vê
// a Margem, o motivo dela mascarado ("Em análise", `painel.EM_ANALISE`).
// Rótulos curtos dos títulos do painel (`painel.TITULO_AG_CANCELAMENTO`).
export const FILTROS_AG: { codigo: string; rotulo: string }[] = [
  { codigo: 'sem_estoque', rotulo: 'Falta de estoque' },
  { codigo: 'restricao_envio', rotulo: 'Restrição de envio' },
  { codigo: 'pedido_cliente', rotulo: 'Comprador pediu' },
  { codigo: 'cancelado_plataforma', rotulo: 'Cancelado na plataforma' },
  { codigo: 'margem_reprovada', rotulo: 'Reprovado na Margem' },
  { codigo: 'pos_nf_manual', rotulo: 'Movido à mão (com NF)' },
  { codigo: 'manual', rotulo: 'Movido à mão' },
  { codigo: 'margem_trava', rotulo: 'Trava da Margem' },
  { codigo: 'em_analise', rotulo: 'Em análise' },
  { codigo: 'desconhecido', rotulo: 'Não conferido' },
]

// Os filtros que têm pedido, na ordem; código novo (que a tela não conhece) no fim.
export function filtrosDaLista(porCodigo: Record<string, number> | null | undefined): { codigo: string; rotulo: string; n: number }[] {
  const conta = porCodigo || {}
  const conhecidos = FILTROS_AG.filter((f) => (conta[f.codigo] || 0) > 0).map((f) => ({ ...f, n: conta[f.codigo] }))
  const novos = Object.keys(conta)
    .filter((c) => (conta[c] || 0) > 0 && !FILTROS_AG.some((f) => f.codigo === c))
    .sort()
    .map((c) => ({ codigo: c, rotulo: c, n: conta[c] }))
  return [...conhecidos, ...novos]
}

// FILTRO POR PLATAFORMA (08/10/2026, Eduardo: "coloque um filtro para mim
// escolher por plataforma"; e no mesmo dia, como o da Caixa, um MENU igual ao
// Duoke — um botão que abre a lista com a logo e quantos tem): acima dos motivos, só com as
// plataformas que têm pedido na lista, na MESMA ordem dos chips da Caixa
// (GRUPOS_CAIXA de AtendimentoFiltroPlataforma — o teste confere que não
// divergem), cada uma com quantos pedidos tem. Plataforma que a tela não
// conhece vai no fim, com o código cru.
export const PLATAFORMAS_AG: { valor: string; nome: string }[] = [
  { valor: 'ml', nome: 'Mercado Livre' },
  { valor: 'shopee', nome: 'Shopee' },
  { valor: 'tiktok', nome: 'TikTok' },
  { valor: 'amazon', nome: 'Amazon' },
  { valor: 'magalu', nome: 'Magalu' },
  { valor: 'temu', nome: 'Temu' },
  { valor: 'aliexpress', nome: 'AliExpress' },
  { valor: 'site', nome: 'Sites' },
]
export function plataformasDaLista(pedidos: { plataforma: string | null }[] | null | undefined): { valor: string; nome: string; n: number }[] {
  const conta: Record<string, number> = {}
  for (const p of pedidos || []) {
    const k = (p.plataforma || '').trim().toLowerCase()
    if (k) conta[k] = (conta[k] || 0) + 1
  }
  const conhecidas = PLATAFORMAS_AG.filter((x) => conta[x.valor]).map((x) => ({ ...x, n: conta[x.valor] }))
  const outras = Object.keys(conta)
    .filter((k) => !PLATAFORMAS_AG.some((x) => x.valor === k))
    .sort()
    .map((k) => ({ valor: k, nome: k, n: conta[k] }))
  return [...conhecidas, ...outras]
}
// Quantos por motivo num pedaço da lista (com uma plataforma escolhida, os
// contadores dos motivos passam a ser os dela).
export function contarPorCodigo(pedidos: { motivo: { codigo: string } }[] | null | undefined): Record<string, number> {
  const conta: Record<string, number> = {}
  for (const p of pedidos || []) conta[p.motivo.codigo] = (conta[p.motivo.codigo] || 0) + 1
  return conta
}

// O prazo de envio na plataforma: vencido, vencendo (< 24 h: urgente) ou a data.
export function prazoDeEnvio(iso: string | null | undefined, agora: number): { texto: string; urgente: boolean } | null {
  if (!iso) return null
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return null
  const min = Math.round((t - agora) / 60_000)
  if (min <= 0) return { texto: 'prazo de envio vencido', urgente: true }
  if (min < 60) return { texto: `envio vence em ${min} min`, urgente: true }
  if (min < 24 * 60) return { texto: `envio vence em ${Math.floor(min / 60)} h`, urgente: true }
  const d = new Date(t)
  const data = d.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit', timeZone: 'America/Sao_Paulo' })
  return { texto: `envio até ${data}`, urgente: false }
}

// As trocas ABERTAS cujo pedido não está na lista de 83955 (paradas no meio
// com o pedido em 9, ou já em 6 sem a NF liberada), a mais velha primeiro.
export function trocasForaDaLista(trocas: Troca[] | null | undefined, numeros: string[]): Troca[] {
  const naLista = new Set(numeros)
  return (trocas || [])
    .filter((t) => t.aberta !== false && !naLista.has(t.pedido_bling))
    .sort((a, b) => (a.created_at || '').localeCompare(b.created_at || ''))
}

// "dg053.sp ×2, a001.sp" — os itens numa linha.
export function itensEmLinha(itens: ItemPedidoAg[] | null | undefined): string {
  return (itens || []).map((i) => (i.quantidade && i.quantidade > 1 ? `${i.sku} ×${i.quantidade}` : i.sku)).join(', ')
}
</script>

<script setup lang="ts">
// O diálogo: a lista, os filtros por motivo, "Abrir conversa" e a troca.
import { Check, ChevronDown, Loader2, MessagesSquare, PackageX, RotateCcw, Shuffle, X } from 'lucide-vue-next'
import { onClickOutside } from '@vueuse/core'
import { CLS_AG_CANCELAMENTO, leituraAgCancelamento } from '~/components/AtendimentoAgCancelamento.vue'
import { erroDaApi } from '~/components/AtendimentoPlataforma.vue'
import { URL_TROCAS, quemTroca, rotuloEstado, useAcessoDaTroca, type EscolhaTroca, type TrocaAberta } from '~/components/AtendimentoTroca.vue'

const aberto = defineModel<boolean>('aberto', { required: true })
const emit = defineEmits<{
  (e: 'abrirConversa', id: string): void
}>()

const { api } = useApi()
const lista = ref<ListaAgCancelamento | null>(null)
// As trocas abertas (GET /trocas?abertas=true): as paradas fora de 83955.
const trocas = ref<Troca[]>([])
const carregando = ref(false)
const erro = ref<string | null>(null)
const filtro = ref('')
// A plataforma escolhida no menu de cima ('' = todas).
const plataforma = ref('')
const menuPlataforma = ref(false)
const menuPlataformaRef = ref<HTMLElement | null>(null)
onClickOutside(menuPlataformaRef, () => { menuPlataforma.value = false })
// Esc com o menu aberto fecha só o menu (sem ele, o Esc fecha a lista).
function fecharMenuPlataforma(e: KeyboardEvent) {
  if (!menuPlataforma.value) return
  e.stopPropagation()
  menuPlataforma.value = false
}
function escolherPlataforma(valor: string) {
  menuPlataforma.value = false
  plataforma.value = valor
}
const agora = ref(Date.now())

let geracao = 0
async function carregar() {
  const g = ++geracao
  carregando.value = true
  erro.value = null
  try {
    // As trocas abertas à parte: a falha delas nunca derruba a lista.
    const [r, t] = await Promise.all([
      api<ListaAgCancelamento>(URL_AG_CANCELAMENTO),
      api<{ itens: Troca[] }>(URL_TROCAS, { query: { abertas: true } }).catch(() => null),
    ])
    if (g !== geracao) return
    lista.value = r
    trocas.value = t?.itens ?? []
    agora.value = Date.now()
  } catch (e: any) {
    if (g !== geracao) return
    erro.value = erroDaApi(e, 'Não consegui ler os pedidos em Aguardando Cancelamento agora').texto
  } finally {
    if (g === geracao) carregando.value = false
  }
}
const plataformas = computed(() => plataformasDaLista(lista.value?.pedidos))
// O que o botão do menu mostra: a plataforma escolhida ou "Todas".
const plataformaAtual = computed(() => plataformas.value.find((c) => c.valor === plataforma.value) ?? null)
// Os pedidos da plataforma escolhida (todos, sem escolha).
const daPlataforma = computed(() => {
  const todos = lista.value?.pedidos ?? []
  return plataforma.value ? todos.filter((p) => (p.plataforma || '').trim().toLowerCase() === plataforma.value) : todos
})
// Sem plataforma, os contadores do backend; com ela, os da plataforma.
const filtros = computed(() => filtrosDaLista(plataforma.value ? contarPorCodigo(daPlataforma.value) : lista.value?.por_codigo))
// Trocou de plataforma e o motivo escolhido não existe nela: volta para "Todos".
watch(plataforma, () => {
  if (filtro.value && !filtros.value.some((f) => f.codigo === filtro.value)) filtro.value = ''
})
const trocasParadas = computed(() => trocasForaDaLista(trocas.value, (lista.value?.pedidos ?? []).map((p) => p.numero)))
// Cada pedido com a leitura do motivo (a mesma do cartão) e o prazo já prontos.
const pedidos = computed(() => {
  const todos = daPlataforma.value
  return (filtro.value ? todos.filter((p) => p.motivo.codigo === filtro.value) : todos).map((p) => {
    const leitura = leituraAgCancelamento(p.motivo)
    return { p, leitura, cls: CLS_AG_CANCELAMENTO[leitura.tom], prazo: prazoDeEnvio(p.prazo_envio, agora.value) }
  })
})

// Aberto: lê a lista e põe o foco no diálogo (o Esc fecha).
const caixa = ref<HTMLElement | null>(null)
watch(aberto, (v) => {
  if (!v) return
  void carregar()
  void nextTick(() => caixa.value?.focus())
}, { immediate: true })

// A troca de produto (fase 4c): quem mexe na caixa troca e retoma daqui.
const acesso = useAcessoDaTroca()
const trocaVisivel = ref(false)
const troca = ref<{ numero: string; conversaId: string | null; escolha: EscolhaTroca | null; aberta: TrocaAberta | null } | null>(null)
function abrirTroca(p: PedidoAgCancelamento, escolha: EscolhaTroca | null) {
  const aberta = escolha ? null : p.motivo.troca_aberta
  if (!acesso.value.trocar || (!escolha && !aberta)) return
  troca.value = { numero: p.numero, conversaId: p.conversa_id, escolha, aberta }
  trocaVisivel.value = true
}
// A troca parada fora de 83955: o Retomar abre o diálogo nela.
function retomarParada(t: Troca) {
  if (!acesso.value.trocar || !t.pode_retomar) return
  troca.value = { numero: t.pedido_bling, conversaId: t.conversa_id, escolha: null, aberta: t }
  trocaVisivel.value = true
}

function fechar() {
  // Com o diálogo da troca aberto por cima, o Esc e o clique fora são dele.
  if (trocaVisivel.value) return
  aberto.value = false
}
function abrir(p: PedidoAgCancelamento) {
  if (!p.conversa_id) return
  emit('abrirConversa', p.conversa_id)
  fechar()
}
</script>

<template>
  <div
    v-if="aberto"
    class="fixed inset-0 z-[70] flex items-stretch justify-center bg-black/50 sm:items-center sm:p-4"
    data-lista-ag-cancelamento
    @click.self="fechar"
    @keydown.esc="fechar"
  >
    <div
      ref="caixa"
      role="dialog"
      aria-modal="true"
      aria-label="Pedidos em Aguardando Cancelamento"
      tabindex="-1"
      class="flex max-h-full w-full flex-col bg-background shadow-2xl sm:max-h-[88vh] sm:max-w-3xl sm:rounded-lg sm:border"
    >
      <header class="flex shrink-0 items-start gap-2 border-b px-4 py-3">
        <PackageX class="mt-0.5 size-4 shrink-0 text-red-600 dark:text-red-400" />
        <div class="min-w-0 flex-1">
          <h2 class="text-sm font-semibold">Aguardando Cancelamento no Bling</h2>
          <p class="text-[11px] text-muted-foreground">
            Pedidos dos últimos 60 dias, com ou sem conversa.
            {{ acesso.trocar ? 'O "Trocar" confere ao vivo no Bling e mostra a prévia antes de mudar o pedido.' : 'Só leitura: nada muda no Bling por aqui.' }}
          </p>
          <p v-if="lista && !lista.sugestoes_ativas" class="text-[11px] text-muted-foreground">
            Sugestões de troca desligadas (chave ATENDIMENTO_TROCA_SUGESTOES_ATIVA).
          </p>
        </div>
        <button type="button" class="rounded p-1 hover:bg-muted disabled:opacity-60" title="ler de novo" :disabled="carregando" @click="carregar">
          <Loader2 v-if="carregando" class="size-4 animate-spin" />
          <RotateCcw v-else class="size-4" />
        </button>
        <button type="button" class="rounded p-1 hover:bg-muted" aria-label="fechar" @click="fechar">
          <X class="size-4" />
        </button>
      </header>

      <div v-if="plataformas.length > 1" class="flex shrink-0 items-center gap-1.5 border-b px-4 py-2 text-[11px]">
        <span class="font-semibold uppercase tracking-wider text-muted-foreground" aria-hidden="true">Plataforma</span>
        <div ref="menuPlataformaRef" class="relative" data-menu-plataforma @keydown.esc="fecharMenuPlataforma">
          <button
            type="button"
            class="inline-flex h-7 items-center gap-1.5 rounded-md border px-2.5 text-xs font-medium transition-colors"
            :class="plataformaAtual ? 'border-primary bg-primary/10 text-primary' : 'bg-background hover:bg-muted'"
            aria-haspopup="menu"
            :aria-expanded="menuPlataforma"
            aria-label="filtrar pela plataforma"
            data-plataforma-botao
            @click="menuPlataforma = !menuPlataforma"
          >
            <AtendimentoIconePlataforma v-if="plataformaAtual" :plataforma="plataformaAtual.valor" :tamanho="14" decorativo />
            <span>{{ plataformaAtual ? plataformaAtual.nome : 'Todas' }}</span>
            <span class="min-w-[18px] rounded-full bg-muted px-1 text-center text-[10px] font-semibold tabular-nums">{{ plataformaAtual ? plataformaAtual.n : (lista?.pedidos.length ?? 0) }}</span>
            <ChevronDown class="size-3.5 shrink-0 opacity-70" aria-hidden="true" />
          </button>
          <div
            v-show="menuPlataforma"
            role="menu"
            aria-label="escolher a plataforma"
            class="absolute left-0 top-full z-30 mt-1 w-56 rounded-md border bg-background p-1 shadow-lg"
          >
            <button
              type="button"
              role="menuitemradio"
              class="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs hover:bg-muted"
              :class="!plataforma ? 'font-medium text-primary' : ''"
              :aria-checked="!plataforma"
              data-filtro-plataforma="todas"
              @click="escolherPlataforma('')"
            >
              <span class="min-w-0 flex-1 truncate">Todas</span>
              <span class="min-w-[18px] rounded-full bg-muted px-1 text-center text-[10px] font-semibold tabular-nums">{{ lista?.pedidos.length ?? 0 }}</span>
              <Check v-if="!plataforma" class="size-3.5 shrink-0" />
            </button>
            <button
              v-for="c in plataformas"
              :key="c.valor"
              type="button"
              role="menuitemradio"
              class="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs hover:bg-muted"
              :class="plataforma === c.valor ? 'font-medium text-primary' : ''"
              :aria-checked="plataforma === c.valor"
              :data-filtro-plataforma="c.valor"
              @click="escolherPlataforma(c.valor)"
            >
              <AtendimentoIconePlataforma :plataforma="c.valor" :tamanho="14" decorativo />
              <span class="min-w-0 flex-1 truncate">{{ c.nome }}</span>
              <span class="min-w-[18px] rounded-full bg-muted px-1 text-center text-[10px] font-semibold tabular-nums">{{ c.n }}</span>
              <Check v-if="plataforma === c.valor" class="size-3.5 shrink-0" />
            </button>
          </div>
        </div>
      </div>

      <div v-if="filtros.length" class="flex shrink-0 flex-wrap gap-1.5 border-b px-4 py-2 text-[11px]" role="tablist" aria-label="filtrar pelo motivo">
        <button
          type="button"
          role="tab"
          class="rounded-full border px-2 py-0.5"
          :class="!filtro ? 'border-primary bg-primary/10 text-primary' : 'hover:bg-muted'"
          :aria-selected="!filtro"
          @click="filtro = ''"
        >
          Todos ({{ daPlataforma.length }})
        </button>
        <button
          v-for="f in filtros"
          :key="f.codigo"
          type="button"
          role="tab"
          class="rounded-full border px-2 py-0.5"
          :class="filtro === f.codigo ? 'border-primary bg-primary/10 text-primary' : 'hover:bg-muted'"
          :aria-selected="filtro === f.codigo"
          :data-filtro="f.codigo"
          @click="filtro = f.codigo"
        >
          {{ f.rotulo }} ({{ f.n }})
        </button>
      </div>

      <div class="min-h-0 flex-1 space-y-2 overflow-y-auto px-4 py-3 text-xs">
        <div v-if="erro" class="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-red-600 dark:text-red-400">{{ erro }}</div>
        <div v-else-if="carregando && !lista" class="flex items-center gap-2 text-muted-foreground" aria-busy="true">
          <Loader2 class="size-4 animate-spin" /> lendo os pedidos…
        </div>
        <!-- As trocas paradas no meio com o pedido FORA de 83955 (fase 4c). -->
        <section
          v-if="!erro && trocasParadas.length"
          class="space-y-1.5 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2"
          data-trocas-paradas
        >
          <p class="font-semibold">Trocas paradas no meio ({{ trocasParadas.length }})</p>
          <p class="text-[11px] text-muted-foreground">O pedido já saiu de Aguardando Cancelamento no Bling, mas a troca não terminou (ex.: ficou em Atendido): retome para ele voltar a Em aberto e a NF à fila.</p>
          <div
            v-for="t in trocasParadas"
            :key="t.id"
            class="flex flex-wrap items-start gap-x-2 gap-y-1 rounded border bg-background px-2 py-1 text-[11px]"
            data-troca-parada
            :data-numero="t.pedido_bling"
            :data-estado="t.estado"
          >
            <Shuffle class="mt-px size-3.5 shrink-0 text-amber-700 dark:text-amber-300" />
            <div class="min-w-0 flex-1 break-words">
              <p><span class="font-semibold tabular-nums">Pedido {{ t.pedido_bling }}</span> · {{ rotuloEstado(t.estado) }}</p>
              <p class="text-muted-foreground">
                <span class="font-mono">{{ t.sku_antigo }}</span> → <span class="font-mono">{{ t.sku_novo }}</span> · {{ quemTroca(t) }}
              </p>
              <p v-if="t.erro">{{ t.erro }}</p>
            </div>
            <button
              v-if="acesso.trocar && t.pode_retomar"
              type="button"
              class="inline-flex shrink-0 items-center gap-1 rounded border bg-background px-1.5 py-0.5 text-[10px] hover:bg-muted"
              data-retomar-troca
              @click="retomarParada(t)"
            >
              <RotateCcw class="size-3" /> Retomar
            </button>
            <span v-else-if="!t.pode_retomar" class="shrink-0 text-[10px] text-muted-foreground">alguém está conduzindo agora</span>
          </div>
        </section>

        <p v-if="lista && !erro && !pedidos.length" class="text-muted-foreground">Nenhum pedido em Aguardando Cancelamento{{ plataforma ? ' nesta plataforma' : '' }}{{ filtro ? ' com este motivo' : '' }}.</p>

        <article
          v-for="{ p, leitura, cls, prazo } in pedidos"
          :key="p.numero"
          class="space-y-1.5 rounded-md border px-3 py-2"
          :class="cls.cartao"
          data-pedido-ag
          :data-numero="p.numero"
          :data-codigo="p.motivo.codigo"
        >
          <div class="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span class="font-semibold tabular-nums">Pedido {{ p.numero }}</span>
            <span v-if="p.numeroloja" class="font-mono text-[11px] text-muted-foreground">{{ p.numeroloja }}</span>
            <AtendimentoPlataforma v-if="p.plataforma" :codigo="p.plataforma" />
            <span v-if="p.conta" class="text-[11px] text-muted-foreground">{{ p.conta }}</span>
            <span
              v-if="prazo"
              class="ml-auto rounded px-1.5 py-px text-[10px] font-medium"
              :class="prazo.urgente ? 'bg-red-500/15 text-red-700 dark:text-red-300' : 'bg-muted text-muted-foreground'"
            >{{ prazo.texto }}</span>
          </div>
          <div class="flex items-start gap-1.5">
            <span class="shrink-0 rounded px-1.5 py-px text-[10px] font-medium" :class="cls.selo">{{ leitura.titulo }}</span>
            <p class="min-w-0 flex-1 font-medium" :class="cls.frase" :title="p.motivo.texto">{{ leitura.frase }}</p>
          </div>
          <p v-for="(d, i) in leitura.detalhes" :key="i" class="text-muted-foreground">{{ d }}</p>
          <p v-if="leitura.observacao" class="break-words text-muted-foreground">Observação do Bling: {{ leitura.observacao }}</p>
          <p v-if="p.itens.length" class="break-words text-[11px] text-muted-foreground">Itens: <span class="font-mono">{{ itensEmLinha(p.itens) }}</span></p>
          <p v-if="leitura.fala" class="text-[11px] text-muted-foreground">{{ leitura.fala }}</p>

          <!-- A troca de produto aberta (fase 4c): o estado e, parada no meio, o Retomar. -->
          <div
            v-if="p.motivo.troca_aberta"
            class="flex flex-wrap items-start gap-x-2 gap-y-1 rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-[11px]"
            data-troca-aberta
            :data-estado="p.motivo.troca_aberta.estado"
          >
            <Shuffle class="mt-px size-3.5 shrink-0 text-amber-700 dark:text-amber-300" />
            <div class="min-w-0 flex-1 break-words">
              <p><span class="font-medium">Troca em andamento:</span> {{ rotuloEstado(p.motivo.troca_aberta.estado) }}</p>
              <p class="text-muted-foreground">
                <span class="font-mono">{{ p.motivo.troca_aberta.sku_antigo }}</span> → <span class="font-mono">{{ p.motivo.troca_aberta.sku_novo }}</span> · {{ quemTroca(p.motivo.troca_aberta) }}
              </p>
              <p v-if="p.motivo.troca_aberta.erro">{{ p.motivo.troca_aberta.erro }}</p>
            </div>
            <button
              v-if="acesso.trocar && p.motivo.troca_aberta.pode_retomar"
              type="button"
              class="inline-flex shrink-0 items-center gap-1 rounded border bg-background px-1.5 py-0.5 text-[10px] hover:bg-muted"
              data-retomar-troca
              @click="abrirTroca(p, null)"
            >
              <RotateCcw class="size-3" /> Retomar
            </button>
            <span v-else-if="!p.motivo.troca_aberta.pode_retomar" class="shrink-0 text-[10px] text-muted-foreground">alguém está conduzindo agora</span>
          </div>

          <AtendimentoTrocaSugestoes
            v-if="p.sugestoes_troca"
            :sugestoes="p.sugestoes_troca"
            :numero="p.numero"
            :conversa-id="p.conversa_id"
            :pode-trocar="acesso.trocar"
            :pode-ofertar="acesso.ofertar"
            :troca-aberta="p.motivo.troca_aberta"
            :oferta-envio="p.motivo.oferta_envio ?? null"
            :troca-envio="p.motivo.troca_envio ?? null"
            @trocar="(e: EscolhaTroca) => abrirTroca(p, e)"
            @oferta-enviada="carregar"
          />

          <div class="flex items-center justify-end gap-2 pt-0.5">
            <button
              v-if="p.conversa_id"
              type="button"
              class="inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[11px] hover:bg-muted"
              data-abrir-conversa
              @click="abrir(p)"
            >
              <MessagesSquare class="size-3.5" /> Abrir conversa
            </button>
            <span v-else class="text-[11px] text-muted-foreground">sem conversa no DaVinci</span>
          </div>
        </article>
      </div>
    </div>

    <!-- A troca (fase 4c), por cima da lista; ao fechar depois de uma escrita, a lista se relê. -->
    <AtendimentoTroca
      v-if="trocaVisivel && troca"
      v-model:aberto="trocaVisivel"
      :numero="troca.numero"
      :escolha="troca.escolha"
      :conversa-id="troca.conversaId"
      :troca-aberta="troca.aberta"
      @mudou="carregar"
    />
  </div>
</template>
