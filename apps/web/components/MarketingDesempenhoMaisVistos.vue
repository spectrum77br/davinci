<script setup lang="ts">
/**
 * "Vídeos mais vistos" (Eduardo, 06/10/2026: "também trazer os vídeos mais
 * vistos"). Um ranking simples: os vídeos publicados na semana ou no mês,
 * do que mais trouxe views pro que menos, com as views de cada rede e o link
 * pro post.
 *
 * Um vídeo = o mesmo criativo postado em todas as redes, e as views dele são
 * a SOMA da última leitura de cada post (views até hoje). A janela é pela 1ª
 * publicação, em dias de Brasília (a API manda `idade_dias`), e a tela diz
 * isso: "publicados nos últimos 7 dias · views até hoje" — não são as views
 * ganhas na semana (essas estão no Resumo e em "Onde vale investir"). Vídeo
 * ainda sem número fica de fora — no ranking ele não é "0 views".
 */
import { computed, ref, watch } from 'vue'
import { ExternalLink } from 'lucide-vue-next'
import {
  ROTULO_REDE, SIGLA_REDE,
  corRede, ddmm, fmtViews, hhmm, maisVistos,
  type CriativoDesempenho, type Janela, type Janelas, type PostagemDesempenho,
} from '~/utils/desempenho'

const props = defineProps<{
  criativos: CriativoDesempenho[]
  postagens: PostagemDesempenho[]
  janelas: Janelas
  /** Sem marca escolhida no filtro, a linha diz de que marca é o vídeo. */
  mostrarMarca: boolean
}>()

const PASSO = 10
const JANELAS: { chave: Janela; rotulo: string }[] = [
  { chave: 'semana', rotulo: 'Semana' },
  { chave: 'mes', rotulo: 'Mês' },
]

const janela = ref<Janela>('mes')
const limite = ref(PASSO)
watch(janela, () => { limite.value = PASSO })

const infoJanela = computed(() => props.janelas?.[janela.value]
  ?? { dias: janela.value === 'semana' ? 7 : 30, desde: '' })
const subtitulo = computed(() => {
  const j = infoJanela.value
  return `publicados nos últimos ${j.dias} dias${j.desde ? ` (desde ${ddmm(j.desde)})` : ''} · views até hoje`
})

const porId = computed(() => new Map((props.postagens ?? []).map((p) => [p.postagem_id, p])))
function postsDe(c: CriativoDesempenho, rede: string): PostagemDesempenho[] {
  return (c.postagens?.[rede] ?? [])
    .map((id) => porId.value.get(id))
    .filter((p): p is PostagemDesempenho => !!p)
}

type Chip = { rede: string; views: number; url: string | null }
type Linha = { c: CriativoDesempenho; pos: number; sub: string; chips: Chip[] }

// Um chip por rede que deu número: a soma dela, com o link do post mais
// visto (dois posts na mesma rede: o mais visto; empate, o mais novo).
function chipsDe(c: CriativoDesempenho): Chip[] {
  return Object.entries(c.views?.por_rede ?? {}).map(([rede, views]) => {
    const posts = postsDe(c, rede)
    let melhor: PostagemDesempenho | undefined
    for (const p of posts) {
      const v = p.acumulado?.views
      if (v === null || v === undefined) continue
      if (!melhor || v > (melhor.acumulado.views as number)) melhor = p
    }
    return { rede, views, url: (melhor ?? posts[0])?.post_url ?? null }
  })
}

function subDe(c: CriativoDesempenho): string {
  const partes: string[] = []
  // A legenda se repete entre vídeos: ela só ajuda quando o nome é outro.
  if (c.titulo && c.titulo !== c.nome) partes.push(`“${c.titulo}”`)
  if (props.mostrarMarca && c.marca) partes.push(c.marca)
  // "(sem produto)", "(sem agência)" não ajudam a reconhecer o vídeo.
  for (const x of [c.produto, c.agencia]) if (x && x.chave !== 'nenhum') partes.push(x.rotulo)
  const posts = Object.keys(c.postagens ?? {}).flatMap((r) => postsDe(c, r))
  const primeiro = [...posts].sort((a, b) => Date.parse(a.publicado_em) - Date.parse(b.publicado_em))[0]
  const pub = c.primeira_publicacao_em || primeiro?.publicado_em || null
  if (pub) {
    const hora = primeiro ? (primeiro.horario === 'outro' ? hhmm(primeiro.publicado_em) : primeiro.horario) : hhmm(pub)
    partes.push(`${ddmm(pub)} ${hora}`.trim())
  }
  return partes.join(' · ')
}

const todos = computed(() => maisVistos(props.criativos ?? [], janela.value, Infinity))
const linhas = computed<Linha[]>(() => todos.value.slice(0, limite.value).map((c, i) => ({
  c, pos: i + 1, sub: subDe(c), chips: chipsDe(c),
})))
const restantes = computed(() => Math.max(todos.value.length - limite.value, 0))
function mostrarMais() {
  limite.value += PASSO
}
</script>

<template>
  <div class="min-w-0 space-y-2">
    <div class="flex flex-wrap items-center gap-2 text-xs">
      <div class="flex w-fit gap-1 rounded-md bg-muted/40 p-1">
        <button
          v-for="j in JANELAS" :key="j.chave"
          class="rounded px-3 py-1 text-sm transition-colors"
          :class="janela === j.chave ? 'bg-background shadow-sm font-medium' : 'hover:bg-background/60 text-muted-foreground'"
          :aria-pressed="janela === j.chave"
          @click="janela = j.chave"
        >
          {{ j.rotulo }}
        </button>
      </div>
      <span class="text-muted-foreground">{{ subtitulo }}</span>
    </div>

    <p v-if="!linhas.length" class="rounded-md border p-6 text-center text-sm text-muted-foreground">
      Nenhum vídeo publicado nos últimos {{ infoJanela.dias }} dias tem views ainda.
    </p>

    <div v-else class="overflow-hidden rounded-xl border bg-card">
      <ol>
        <li
          v-for="l in linhas" :key="l.c.creative_id"
          class="flex flex-col gap-1.5 border-b px-3 py-2.5 last:border-0 sm:flex-row sm:items-center sm:gap-3"
        >
          <div class="flex min-w-0 flex-1 items-start gap-2.5">
            <span class="w-5 shrink-0 pt-0.5 text-right text-xs font-medium tabular-nums text-muted-foreground">
              {{ l.pos }}
            </span>
            <div class="min-w-0 flex-1 space-y-1">
              <p class="truncate text-sm font-medium" :title="l.c.nome || l.c.titulo">
                {{ l.c.nome || l.c.titulo || 'vídeo sem legenda' }}
              </p>
              <p v-if="l.sub" class="truncate text-[11px] text-muted-foreground" :title="l.sub">{{ l.sub }}</p>
              <div v-if="l.chips.length" class="flex flex-wrap gap-1">
                <template v-for="ch in l.chips" :key="ch.rede">
                  <a
                    v-if="ch.url" :href="ch.url" target="_blank" rel="noopener"
                    class="inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] text-muted-foreground transition-colors hover:bg-muted/40 hover:text-foreground"
                    :title="`${ROTULO_REDE[ch.rede] || ch.rede}: ${fmtViews(ch.views)} views — abrir o post`"
                  >
                    <span class="inline-block size-1.5 shrink-0 rounded-full" :style="{ background: corRede(ch.rede) }" />
                    {{ SIGLA_REDE[ch.rede] || ch.rede }}
                    <span class="tabular-nums text-foreground">{{ fmtViews(ch.views) }}</span>
                  </a>
                  <span
                    v-else
                    class="inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] text-muted-foreground"
                    :title="`${ROTULO_REDE[ch.rede] || ch.rede}: ${fmtViews(ch.views)} views`"
                  >
                    <span class="inline-block size-1.5 shrink-0 rounded-full" :style="{ background: corRede(ch.rede) }" />
                    {{ SIGLA_REDE[ch.rede] || ch.rede }}
                    <span class="tabular-nums text-foreground">{{ fmtViews(ch.views) }}</span>
                  </span>
                </template>
              </div>
            </div>
          </div>
          <div class="shrink-0 pl-[1.875rem] sm:pl-0 sm:text-right">
            <span class="text-base font-semibold tabular-nums">{{ fmtViews(l.c.views.total) }}</span>
            <span class="text-xs text-muted-foreground"> views</span>
          </div>
        </li>
      </ol>
      <button
        v-if="restantes > 0"
        class="w-full border-t px-3 py-2 text-xs text-muted-foreground transition-colors hover:bg-muted/40 hover:text-foreground"
        @click="mostrarMais"
      >
        mostrar mais {{ Math.min(restantes, PASSO) }}
      </button>
    </div>
  </div>
</template>
