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
// Funções puras aqui em cima (testadas em tests/atendimento-filtro-plataforma.cjs).
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

// O "falta responder" de um conjunto de plataformas (do /resumo).
export function aguardandoDe(resumo: Resumo | null | undefined, plataformas: string[]): number {
  return (resumo?.plataformas || [])
    .filter((p) => plataformas.includes(p.plataforma))
    .reduce((s, p) => s + (Number(p.aguardando) || 0), 0)
}

// Os chips da pessoa: a plataforma EXISTE para ela quando o /resumo traz uma
// loja dela (o /resumo já vem no escopo da equipe), ou conversa esperando —
// e a que está escolhida fica sempre, para ela ver o que está filtrando.
export function chipsDoResumo(resumo: Resumo | null | undefined, atual = ''): ChipPlataforma[] {
  if (!resumo) return []
  const escolhidas = plataformasDoValor(atual)
  const comLoja = new Set((resumo.lojas || []).map((l) => l.plataforma))
  return GRUPOS_CAIXA
    .map((g) => ({ ...g, aguardando: aguardandoDe(resumo, g.plataformas) }))
    .filter((g) => g.plataformas.some((p) => comLoja.has(p)) || g.aguardando > 0 || (escolhidas.length > 0 && escolhidas.every((p) => g.plataformas.includes(p))))
}

// O chip aceso: o filtro está DENTRO dele (a plataforma inteira, ou uma loja
// dela escolhida na barra). "Todas" = nenhuma plataforma escolhida.
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

export function contadorChip(n: number): string {
  return n > 99 ? '99+' : String(n)
}
</script>

<script setup lang="ts">
const props = defineProps<{ resumo: Resumo | null }>()
const filtros = defineModel<FiltrosLista>('filtros', { required: true })

const chips = computed(() => chipsDoResumo(props.resumo, filtros.value.plataforma))
const total = computed(() => (props.resumo?.plataformas || []).reduce((s, p) => s + (Number(p.aguardando) || 0), 0))

function escolher(chip: GrupoCaixa | null) {
  filtros.value = filtrosDoChip(filtros.value, chip)
}
function titulo(chip: ChipPlataforma): string {
  const partes = [`Só ${chip.nome}: ${chip.aguardando} conversa(s) falta responder`]
  if (chipAtivo(chip, filtros.value) && !soAPlataforma(filtros.value)) partes.push(`Uma loja escolhida na barra — clique para ver todas as lojas ${chip.nome}`)
  else if (chipAtivo(chip, filtros.value)) partes.push('Clique de novo para ver todas as plataformas')
  return partes.join('\n')
}

// Fileira que rola de lado: a roda do mouse (vertical) anda na fileira, e o
// chip escolhido aparece sempre.
const fileira = ref<HTMLElement | null>(null)
function rolar(e: WheelEvent) {
  const el = fileira.value
  if (!el || el.scrollWidth <= el.clientWidth || Math.abs(e.deltaX) > Math.abs(e.deltaY)) return
  el.scrollLeft += e.deltaY
  e.preventDefault()
}
watch(() => filtros.value.plataforma, () => {
  void nextTick(() => fileira.value?.querySelector<HTMLElement>('[aria-pressed="true"]')?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' }))
})
</script>

<template>
  <div
    v-if="chips.length"
    ref="fileira"
    class="flex items-center gap-1.5 overflow-x-auto pb-0.5 [scrollbar-width:thin] lg:flex-wrap lg:overflow-visible"
    role="group"
    aria-label="plataforma"
    data-filtro-plataforma
    @wheel="rolar"
  >
    <span class="shrink-0 pr-0.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground" aria-hidden="true">Plataforma</span>
    <button
      type="button"
      class="inline-flex h-7 shrink-0 items-center gap-1 rounded-full border px-2.5 text-xs font-medium transition-colors"
      :class="chipAtivo(null, filtros) ? 'border-primary bg-primary text-primary-foreground' : 'bg-background text-muted-foreground hover:bg-muted hover:text-foreground'"
      :aria-pressed="chipAtivo(null, filtros)"
      :title="`Todas as plataformas: ${total} conversa(s) falta responder`"
      data-chip="todas"
      @click="escolher(null)"
    >
      Todas
      <span
        v-if="total"
        class="min-w-[18px] rounded-full px-1 text-center text-[10px] font-semibold tabular-nums"
        :class="chipAtivo(null, filtros) ? 'bg-white/25 text-current' : 'bg-red-500 text-white'"
      >{{ contadorChip(total) }}</span>
    </button>
    <button
      v-for="c in chips"
      :key="c.valor"
      type="button"
      class="inline-flex h-7 shrink-0 items-center gap-1.5 rounded-full border px-2.5 text-xs font-medium transition-colors"
      :class="chipAtivo(c, filtros)
        ? (soAPlataforma(filtros) ? 'border-primary bg-primary text-primary-foreground' : 'border-primary bg-primary/10 text-primary')
        : 'bg-background text-muted-foreground hover:bg-muted hover:text-foreground'"
      :aria-pressed="chipAtivo(c, filtros)"
      :aria-label="`Só ${c.nome}${c.aguardando ? ` — ${c.aguardando} falta responder` : ''}`"
      :title="titulo(c)"
      :data-chip="c.valor"
      @click="escolher(c)"
    >
      <span class="inline-flex shrink-0 items-center -space-x-1" aria-hidden="true">
        <AtendimentoIconePlataforma v-for="p in c.plataformas" :key="p" :plataforma="p" :tamanho="14" decorativo />
      </span>
      <span>{{ c.nome }}</span>
      <span
        v-if="c.aguardando"
        class="min-w-[18px] rounded-full px-1 text-center text-[10px] font-semibold tabular-nums"
        :class="chipAtivo(c, filtros) && soAPlataforma(filtros) ? 'bg-white/25 text-current' : 'bg-red-500 text-white'"
      >{{ contadorChip(c.aguardando) }}</span>
    </button>
  </div>
</template>
