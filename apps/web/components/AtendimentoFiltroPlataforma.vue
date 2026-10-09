<script lang="ts">
// Escolher a PLATAFORMA no topo da lista da Caixa (08/10/2026). O Eduardo:
// "escolher por plataforma deveria ser mais visível — se a pessoa quiser ver
// só Mercado Livre, só Shopee, coisas do tipo". Antes a escolha só existia no
// cabeçalho do grupo da barra de lojas — que some quando a barra está
// recolhida (o padrão abaixo de 1536 px) e em tela estreita.
// Uma fileira de chips logo acima da Caixa (a largura toda: numa linha só no
// computador; na tela estreita ela rola de lado): Todas · Mercado Livre ·
// Shopee · TikTok · Amazon ·
// Magalu · Temu · AliExpress · Sites · Redes — só as que existem para a
// pessoa (há loja dela no /resumo, ou conversa esperando), cada um com o
// "falta responder" da plataforma. Funciona junto com o resto (Todas/Falta
// responder, Filtrar, busca) e fica lembrado no navegador (a página guarda
// `filtros.plataforma`). "Redes" é um GRUPO: `instagram,facebook` — a API da
// lista aceita várias plataformas separadas por vírgula.
// MENU, NÃO FILEIRA (08/10/2026, Eduardo: "ficou muita informação desse
// jeito, prefiro igual ao Duoke: clica e escolhe qual queremos — mas com o
// número de mensagens e a logo igual no nosso"): um botão só ("Plataforma:
// Todas 99+ ▾", ou a plataforma escolhida com a logo e o número) que abre a
// lista — Todas e cada plataforma com a logo e o "falta responder". Mesmo
// jeito do menu "Filtrar" da lista (clique fora ou Esc fecha). Escolher no
// menu a plataforma que já está inteira só fecha (é escolha, não liga/desliga);
// com uma loja dela escolhida na barra, mostra todas as lojas dela.
// A BARRA CORTA (09/10/2026, Eduardo: "quando selecionar a plataforma lá em
// cima, corta as outras ali da listagem: deixa só as lojas da plataforma
// mesmo naquele navegável na esquerda"): escolher aqui grava também
// `filtros.plataforma_topo`, e a barra de lojas (AtendimentoLojas) mostra só
// as lojas dela, com "Todas · Shopee" no topo. "Todas" aqui volta a barra
// inteira. Escolher a plataforma pelo NOME do grupo na barra também corta (é
// escolher a plataforma); clicar numa LOJA na barra não corta.
// O BOTÃO MOSTRA O CORTE (`chipDoTopo`): a plataforma só aparece aqui quando
// a barra está cortada nela — com uma loja Shopee clicada na barra inteira, o
// botão continua "Todas". Assim botão e barra nunca dizem coisas diferentes
// (antes o botão dizia "Shopee" com TikTok e ML na barra).
// NA CAIXA HUMANO (09/10/2026) o número de cada plataforma é o da Caixa
// Humano (`numero="humano"`), não o "falta responder"; as plataformas que
// aparecem são as mesmas.
// Funções puras aqui em cima (testadas em tests/atendimento-filtro-plataforma.cjs
// e tests/atendimento-caixa-humano.cjs).
import type { FiltrosLista } from '~/components/AtendimentoLista.vue'
import type { Resumo } from '~/components/AtendimentoPlataforma.vue'

export type GrupoCaixa = { valor: string; plataformas: string[]; nome: string }
// A ordem do Eduardo (07/10/2026).
export const GRUPOS_CAIXA: GrupoCaixa[] = [
  { valor: 'ml', plataformas: ['ml'], nome: 'Mercado Livre' },
  { valor: 'shopee', plataformas: ['shopee'], nome: 'Shopee' },
  { valor: 'tiktok', plataformas: ['tiktok'], nome: 'TikTok' },
  { valor: 'amazon', plataformas: ['amazon'], nome: 'Amazon' },
  { valor: 'magalu', plataformas: ['magalu'], nome: 'Magalu' },
  { valor: 'temu', plataformas: ['temu'], nome: 'Temu' },
  { valor: 'aliexpress', plataformas: ['aliexpress'], nome: 'AliExpress' },
  { valor: 'site', plataformas: ['site'], nome: 'Sites' },
  { valor: 'instagram,facebook', plataformas: ['instagram', 'facebook'], nome: 'Redes' },
]

// O número do chip: o "falta responder" (`aguardando`, na Caixa) ou o da
// Caixa Humano (`humano`) — o mesmo campo do /resumo em cada nível.
export type NumeroDaCaixa = 'aguardando' | 'humano'
// `aguardando` = o número que o chip mostra (o do `NumeroDaCaixa` escolhido).
export type ChipPlataforma = GrupoCaixa & { aguardando: number }

// `filtros.plataforma` → as plataformas ("instagram,facebook" → duas).
export function plataformasDoValor(valor: string | null | undefined): string[] {
  const saida: string[] = []
  for (const parte of (valor || '').split(',')) {
    const p = parte.trim().toLowerCase()
    if (p && !saida.includes(p)) saida.push(p)
  }
  return saida
}

// O grupo do menu que contém estas plataformas ('' = nenhum): "shopee" →
// "shopee"; "instagram" → "instagram,facebook" (Redes). É o corte de quem
// escolhe a plataforma pela barra, e o do filtro salvo antes do corte.
export function topoDoValor(valor: string | null | undefined): string {
  const ps = plataformasDoValor(valor)
  if (!ps.length) return ''
  return GRUPOS_CAIXA.find((g) => ps.every((p) => g.plataformas.includes(p)))?.valor ?? ''
}

// O CORTE da barra de lojas ([] = a barra inteira): a plataforma do topo,
// enquanto o filtro da lista está DENTRO dela (a plataforma inteira ou uma
// loja dela). Se o filtro saiu dela por outro caminho, a barra volta inteira
// — e o botão, para "Todas".
export function corteDaBarra(f: Pick<FiltrosLista, 'plataforma' | 'plataforma_topo'>): string[] {
  const topo = plataformasDoValor(f.plataforma_topo)
  const atual = plataformasDoValor(f.plataforma)
  if (!topo.length || !atual.length || !atual.every((p) => topo.includes(p))) return []
  return topo
}

// O que o botão do topo mostra e o menu marca: a plataforma do CORTE (null =
// "Todas", a barra inteira).
export function chipDoTopo(chip: GrupoCaixa | null, f: Pick<FiltrosLista, 'plataforma' | 'plataforma_topo'>): boolean {
  const corte = corteDaBarra(f)
  if (!chip) return corte.length === 0
  return corte.length > 0 && topoDoValor(corte.join(',')) === chip.valor
}

// O "falta responder" de um conjunto de plataformas (do /resumo) — ou, com
// `numero = 'humano'`, quantas delas estão na Caixa Humano.
export function aguardandoDe(resumo: Resumo | null | undefined, plataformas: string[], numero: NumeroDaCaixa = 'aguardando'): number {
  return (resumo?.plataformas || [])
    .filter((p) => plataformas.includes(p.plataforma))
    .reduce((s, p) => s + (Number(p[numero]) || 0), 0)
}

// Como o número se lê no title ("3 conversa(s) falta responder").
export function textoDoNumero(numero: NumeroDaCaixa = 'aguardando'): string {
  return numero === 'humano' ? 'conversa(s) esperando uma pessoa (a IA não pode responder)' : 'conversa(s) falta responder'
}

// Os chips da pessoa: a plataforma EXISTE para ela quando o /resumo traz uma
// loja dela (o /resumo já vem no escopo da equipe), ou conversa esperando —
// e a que está escolhida fica sempre, para ela ver o que está filtrando.
// Na Caixa Humano o número muda, as plataformas não (quem existe é pelo
// "falta responder").
export function chipsDoResumo(resumo: Resumo | null | undefined, atual = '', numero: NumeroDaCaixa = 'aguardando'): ChipPlataforma[] {
  if (!resumo) return []
  const escolhidas = plataformasDoValor(atual)
  const comLoja = new Set((resumo.lojas || []).map((l) => l.plataforma))
  return GRUPOS_CAIXA
    .map((g) => ({ ...g, aguardando: aguardandoDe(resumo, g.plataformas) }))
    .filter((g) => g.plataformas.some((p) => comLoja.has(p)) || g.aguardando > 0 || (escolhidas.length > 0 && escolhidas.every((p) => g.plataformas.includes(p))))
    .map((g) => (numero === 'aguardando' ? g : { ...g, aguardando: aguardandoDe(resumo, g.plataformas, numero) }))
}

// O filtro da lista está DENTRO do chip (a plataforma inteira, ou uma loja
// dela escolhida na barra). "Todas" = nenhuma plataforma escolhida. (O que o
// botão mostra e o menu marca é o corte: `chipDoTopo`.)
export function chipAtivo(chip: GrupoCaixa | null, filtros: Pick<FiltrosLista, 'plataforma'>): boolean {
  const escolhidas = plataformasDoValor(filtros.plataforma)
  if (!chip) return escolhidas.length === 0
  return escolhidas.length > 0 && escolhidas.every((p) => chip.plataformas.includes(p))
}

// Só a plataforma inteira (sem loja, site ou conta escolhida na barra).
export function soAPlataforma(filtros: FiltrosLista): boolean {
  return !filtros.integration_id && !filtros.externo_ref && !filtros.rede_social_id
}

// O clique: o chip (ou "Todas" com `null`) vira o filtro de plataforma; a
// loja/site/conta escolhidos na barra saem, a caixa (Pergunta/Pós-venda do
// ML) só fica se a plataforma não mudou. Aba (Todas/Falta responder), menu
// Filtrar e busca continuam. Clicar no chip que já mostra a plataforma
// inteira volta para Todas.
export function filtrosDoChip(filtros: FiltrosLista, chip: GrupoCaixa | null): FiltrosLista {
  const inteira = chip && chipAtivo(chip, filtros) && soAPlataforma(filtros)
    && plataformasDoValor(filtros.plataforma).join(',') === chip.plataformas.join(',')
  const valor = !chip || inteira ? '' : chip.valor
  return {
    ...filtros,
    plataforma: valor,
    integration_id: '',
    externo_ref: '',
    rede_social_id: '',
    canal: valor && valor === filtros.plataforma ? filtros.canal : '',
  }
}

// A escolha NO MENU (09/10/2026): a plataforma (ou "Todas" com `null`) vira o
// filtro E o corte da barra de lojas (`plataforma_topo`). Escolher a que já
// está inteira não desfaz (é um menu) — só passa a cortar a barra, se ela
// tinha sido escolhida pela barra. Mesmo objeto = nada mudou.
export function filtrosDoMenu(filtros: FiltrosLista, chip: GrupoCaixa | null): FiltrosLista {
  const inteira = chip && chipAtivo(chip, filtros) && soAPlataforma(filtros)
    && plataformasDoValor(filtros.plataforma).join(',') === chip.plataformas.join(',')
  if (chip && inteira) {
    return (filtros.plataforma_topo || '') === chip.valor ? filtros : { ...filtros, plataforma_topo: chip.valor }
  }
  const novo = filtrosDoChip(filtros, chip)
  novo.plataforma_topo = novo.plataforma
  return novo
}

export function contadorChip(n: number): string {
  return n > 99 ? '99+' : String(n)
}
</script>

<script setup lang="ts">
import { Check, ChevronDown } from 'lucide-vue-next'
import { onClickOutside } from '@vueuse/core'

const props = defineProps<{ resumo: Resumo | null; numero?: NumeroDaCaixa }>()
const filtros = defineModel<FiltrosLista>('filtros', { required: true })

const campo = computed<NumeroDaCaixa>(() => (props.numero === 'humano' ? 'humano' : 'aguardando'))
const chips = computed(() => chipsDoResumo(props.resumo, filtros.value.plataforma, campo.value))
const total = computed(() => (props.resumo?.plataformas || []).reduce((s, p) => s + (Number(p[campo.value]) || 0), 0))
// O que o botão mostra: a plataforma do corte da barra (inteira ou com uma
// loja dela escolhida) ou "Todas" — a barra inteira, mesmo com uma loja
// clicada nela.
const atual = computed(() => chips.value.find((c) => chipDoTopo(c, filtros.value)) ?? null)

const aberto = ref(false)
const caixaRef = ref<HTMLElement | null>(null)
onClickOutside(caixaRef, () => { aberto.value = false })

function escolher(chip: GrupoCaixa | null) {
  aberto.value = false
  // Já está na plataforma inteira (e cortando a barra): escolher de novo não
  // desfaz (é um menu) — `filtrosDoMenu` devolve o mesmo objeto.
  const novo = filtrosDoMenu(filtros.value, chip)
  if (novo === filtros.value) return
  // O tipo do chamado (os chips do grupo Site, na lista) é da plataforma de antes.
  if (novo.plataforma !== filtros.value.plataforma) novo.tipo_chamado = ''
  filtros.value = novo
}
function titulo(chip: ChipPlataforma): string {
  const partes = [`Só ${chip.nome}: ${chip.aguardando} ${textoDoNumero(campo.value)}`]
  if (chipAtivo(chip, filtros.value) && !soAPlataforma(filtros.value)) partes.push(`Uma loja escolhida na barra — clique para ver todas as lojas ${chip.nome}`)
  return partes.join('\n')
}
</script>

<template>
  <div
    v-if="chips.length"
    ref="caixaRef"
    class="relative flex items-center gap-1.5"
    data-filtro-plataforma
    @keydown.esc="aberto = false"
  >
    <span class="shrink-0 pr-0.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground" aria-hidden="true">Plataforma</span>
    <button
      type="button"
      class="inline-flex h-7 shrink-0 items-center gap-1.5 rounded-md border px-2.5 text-xs font-medium transition-colors"
      :class="atual ? 'border-primary bg-primary/10 text-primary' : 'bg-background hover:bg-muted'"
      aria-haspopup="menu"
      :aria-expanded="aberto"
      aria-label="plataforma"
      :title="atual ? titulo(atual) : `Todas as plataformas: ${total} ${textoDoNumero(campo)}`"
      data-plataforma-botao
      @click="aberto = !aberto"
    >
      <span v-if="atual" class="inline-flex shrink-0 items-center -space-x-1" aria-hidden="true">
        <AtendimentoIconePlataforma v-for="p in atual.plataformas" :key="p" :plataforma="p" :tamanho="14" decorativo />
      </span>
      <span>{{ atual ? atual.nome : 'Todas' }}</span>
      <span
        v-if="atual ? atual.aguardando : total"
        class="min-w-[18px] rounded-full bg-red-500 px-1 text-center text-[10px] font-semibold tabular-nums text-white"
      >{{ contadorChip(atual ? atual.aguardando : total) }}</span>
      <ChevronDown class="size-3.5 shrink-0 opacity-70" aria-hidden="true" />
    </button>
    <div
      v-show="aberto"
      role="menu"
      aria-label="escolher a plataforma"
      class="absolute left-0 top-full z-30 mt-1 w-60 rounded-md border bg-background p-1 shadow-lg"
    >
      <button
        type="button"
        role="menuitemradio"
        class="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs hover:bg-muted"
        :class="chipDoTopo(null, filtros) ? 'font-medium text-primary' : ''"
        :aria-checked="chipDoTopo(null, filtros)"
        :title="`Todas as plataformas: ${total} ${textoDoNumero(campo)}`"
        data-chip="todas"
        @click="escolher(null)"
      >
        <span class="min-w-0 flex-1 truncate">Todas</span>
        <span
          v-if="total"
          class="min-w-[18px] rounded-full bg-red-500 px-1 text-center text-[10px] font-semibold tabular-nums text-white"
        >{{ contadorChip(total) }}</span>
        <Check v-if="chipDoTopo(null, filtros)" class="size-3.5 shrink-0" />
      </button>
      <button
        v-for="c in chips"
        :key="c.valor"
        type="button"
        role="menuitemradio"
        class="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs hover:bg-muted"
        :class="chipDoTopo(c, filtros) ? 'font-medium text-primary' : ''"
        :aria-checked="chipDoTopo(c, filtros)"
        :aria-label="`Só ${c.nome}${c.aguardando ? ` — ${c.aguardando} ${campo === 'humano' ? 'esperando uma pessoa' : 'falta responder'}` : ''}`"
        :title="titulo(c)"
        :data-chip="c.valor"
        @click="escolher(c)"
      >
        <span class="inline-flex w-5 shrink-0 items-center -space-x-1" aria-hidden="true">
          <AtendimentoIconePlataforma v-for="p in c.plataformas" :key="p" :plataforma="p" :tamanho="14" decorativo />
        </span>
        <span class="min-w-0 flex-1 truncate">{{ c.nome }}</span>
        <span
          v-if="c.aguardando"
          class="min-w-[18px] rounded-full bg-red-500 px-1 text-center text-[10px] font-semibold tabular-nums text-white"
        >{{ contadorChip(c.aguardando) }}</span>
        <Check v-if="chipDoTopo(c, filtros)" class="size-3.5 shrink-0" />
      </button>
    </div>
  </div>
</template>
