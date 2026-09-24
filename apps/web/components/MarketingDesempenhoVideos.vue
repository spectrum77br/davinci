<script setup lang="ts">
/**
 * "Qual vídeo rendeu mais" — um criativo (o vídeo produzido) por linha, e o
 * post dele em cada rede lado a lado (Eduardo, 24/09/2026).
 *
 * A linha é o CRIATIVO, não o post, porque é nele que se investe: o mesmo
 * vídeo sai no Instagram, no YouTube e no TikTok, e a pergunta é se ELE
 * funcionou. O número de cada célula é a view NA MESMA IDADE (views com N
 * dias), nunca o total — senão o vídeo mais velho ganha sempre. E quando não
 * tem número, a célula diz o porquê (aguardando, cedo, sem views, falhou):
 * "—" sozinho é o que fazia o Eduardo perguntar "será que demora?".
 */
import { computed, nextTick, ref } from 'vue'
import { AlertTriangle, ChevronRight, Clock, ExternalLink, EyeOff, Info, X } from 'lucide-vue-next'
import { apiErrMsg } from '~/lib/apiError'
import {
  ERROS_DESEMPENHO, ROTULO_REDE, SIGLA_REDE,
  celula, corRede, ddmm, fmtIndice, frescor, geomCurva, hhmm, num, pct, qtd, redesDaTabela, sinal, tomIndice,
  type Celula, type CriativoDesempenho, type Minimos, type PostagemDesempenho,
} from '~/utils/desempenho'

const props = defineProps<{
  postagens: PostagemDesempenho[]
  criativos: CriativoDesempenho[]
  /** Posts apagados das redes — só pra célula dizer "apagado" e não "não postado". */
  foraDoAr?: { creative_id?: string; plataforma: string }[]
  marco: number
  minimos: Minimos
  canEdit: boolean
}>()
// A tela-mãe recarrega tudo: tirar um vídeo muda as somas, as medianas e o
// índice dos OUTROS vídeos da conta, não só esta linha.
const emit = defineEmits<{ (e: 'mudou'): void }>()

const { api } = useApi()
const toasts = useToasts()

// Tom do índice → classe. Vídeo fraco é informação, não erro: nada de vermelho.
const CHIP: Record<string, string> = {
  alto: 'pill-success tabular-nums',
  normal: 'pill-muted tabular-nums',
  baixo: 'text-[11px] text-muted-foreground tabular-nums',
}
const METRICAS = ['views', 'curtidas', 'comentarios', 'compartilhamentos', 'salvamentos'] as const

const porId = computed(() => new Map(props.postagens.map((p) => [p.postagem_id, p])))
const redes = computed(() => redesDaTabela(props.postagens.map((p) => p.plataforma)))

// A barra lateral fixa do DaVinci come até 248 px, então a grade só entra no
// lg (colunas mais estreitas) e ganha a largura cheia no xl; abaixo disso cada
// vídeo vira um cartão. Com o Facebook entra uma quarta coluna, que só cabe no
// xl. Classes por extenso pro Tailwind enxergar.
const GRADE = {
  3: {
    cab: 'hidden lg:grid lg:grid-cols-[minmax(0,1fr)_repeat(3,7.5rem)_4.5rem] xl:grid-cols-[minmax(0,1fr)_repeat(3,8.5rem)_5rem]',
    linha: 'lg:grid lg:grid-cols-[minmax(0,1fr)_repeat(3,7.5rem)_4.5rem] xl:grid-cols-[minmax(0,1fr)_repeat(3,8.5rem)_5rem] lg:gap-x-3',
    celular: 'lg:hidden',
    tela: 'hidden lg:block',
  },
  4: {
    cab: 'hidden xl:grid xl:grid-cols-[minmax(0,1fr)_repeat(4,8.5rem)_5rem]',
    linha: 'xl:grid xl:grid-cols-[minmax(0,1fr)_repeat(4,8.5rem)_5rem] xl:gap-x-3',
    celular: 'xl:hidden',
    tela: 'hidden xl:block',
  },
}
const grade = computed(() => GRADE[redes.value.length > 3 ? 4 : 3])

type Coluna = { rede: string; post?: PostagemDesempenho; extra: number; cel: Celula }
type Linha = {
  c: CriativoDesempenho
  colunas: Coluna[]
  posts: PostagemDesempenho[]
  // Só post com pelo menos uma leitura além da âncora (0,0) tem curva.
  curvas: { p: PostagemDesempenho; g: ReturnType<typeof geomCurva> }[]
  meta: string
  ultimoPost: number
  semIndice: string
}

const apagados = computed(() => new Set((props.foraDoAr ?? []).map((f) => `${f.creative_id}|${f.plataforma}`)))

function postsDe(c: CriativoDesempenho, rede: string): PostagemDesempenho[] {
  return (c.postagens?.[rede] ?? [])
    .map((id) => porId.value.get(id))
    .filter((p): p is PostagemDesempenho => !!p)
}

const linhas = computed<Linha[]>(() => {
  const agora = new Date()
  return props.criativos.map((c) => {
    const colunas = redes.value.map((rede) => {
      const ps = postsDe(c, rede)
      // Dois posts do mesmo vídeo na mesma rede: mostra o mais novo e avisa.
      const apagado = !ps.length && apagados.value.has(`${c.creative_id}|${rede}`)
      return { rede, post: ps[0], extra: Math.max(ps.length - 1, 0), cel: celula(ps[0], props.marco, agora, apagado) }
    })
    const posts = redes.value.flatMap((rede) => postsDe(c, rede))
    const primeiro = [...posts].sort((a, b) => Date.parse(a.publicado_em) - Date.parse(b.publicado_em))[0]
    const pub = c.primeira_publicacao_em || primeiro?.publicado_em || null
    const hora = primeiro ? (primeiro.horario === 'outro' ? hhmm(primeiro.publicado_em) : primeiro.horario) : ''
    // "(sem produto)", "(sem agência)"… não ajudam a reconhecer o vídeo; somem da linha.
    const meta = [c.produto, c.formato, c.agencia]
      .filter((x) => x && x.chave !== 'nenhum')
      .map((x) => x.rotulo)
    if (pub) meta.push(`${ddmm(pub)} ${hora}`.trim())
    // Índice nulo precisa dizer POR QUÊ, senão parece vídeo ruim.
    let semIndice = ''
    if (c.indice_views === null || c.indice_views === undefined) {
      const estados = posts.map((p) => celula(p, props.marco, agora).estado)
      if (estados.length && estados.every((e) => e === 'cedo' || e === 'aguardando')) semIndice = 'cedo demais'
      else if (posts.some((p) => p.indice_motivo === 'base_pequena')) semIndice = 'conta com poucos vídeos'
    }
    return {
      c,
      colunas,
      posts,
      curvas: posts.filter((p) => (p.curva?.length ?? 0) > 1).map((p) => ({ p, g: geomCurva(p.curva) })),
      meta: meta.join(' · '),
      ultimoPost: Math.max(0, ...posts.map((p) => Date.parse(p.publicado_em) || 0)),
      semIndice,
    }
  })
})

// ---------- ordem

const ordem = ref<string | null>(null)
// Com menos de 3 índices, ordenar por índice é ordenar quase tudo por "—":
// aí o padrão é "mais novos", que é o que o Eduardo procura logo depois de postar.
const ordemEfetiva = computed(() => {
  if (ordem.value) return ordem.value
  const comIndice = props.criativos.filter((c) => c.indice_views !== null && c.indice_views !== undefined).length
  return comIndice >= 3 ? 'indice' : 'novos'
})
const opcoesOrdem = computed(() => [
  { chave: 'indice', rotulo: 'índice' },
  { chave: 'novos', rotulo: 'mais novos' },
  ...redes.value.map((r) => ({ chave: r, rotulo: ROTULO_REDE[r] || r })),
])

function nulosNoFim(a: number | null | undefined, b: number | null | undefined): number {
  const na = a === null || a === undefined
  const nb = b === null || b === undefined
  if (na && nb) return 0
  if (na) return 1
  if (nb) return -1
  return (b as number) - (a as number)
}

const ordenadas = computed(() => {
  const arr = [...linhas.value]
  const porNovo = (a: Linha, b: Linha) => b.ultimoPost - a.ultimoPost
  const porIndice = (a: Linha, b: Linha) => nulosNoFim(a.c.indice_views, b.c.indice_views) || porNovo(a, b)
  const o = ordemEfetiva.value
  if (o === 'novos') arr.sort(porNovo)
  else if (o === 'indice') arr.sort(porIndice)
  else {
    // Por rede: views na idade comparada daquela rede (mesma rede, então o
    // número cru é honesto). Sem número vai pro fim.
    const vm = (l: Linha) => l.colunas.find((x) => x.rede === o)?.post?.views_marco
    arr.sort((a, b) => nulosNoFim(vm(a), vm(b)) || porIndice(a, b))
  }
  return arr
})

const limite = ref(20)
const visiveis = computed(() => ordenadas.value.slice(0, limite.value))
const restantes = computed(() => Math.max(ordenadas.value.length - limite.value, 0))

const abertos = ref<Set<string>>(new Set())
function alternar(id: string) {
  const s = new Set(abertos.value)
  s.has(id) ? s.delete(id) : s.add(id)
  abertos.value = s
}

function dicaIndicePost(p: PostagemDesempenho): string {
  const med = p.base?.mediana
  if (med === null || med === undefined) return ''
  return `normal desta conta: ${med.toLocaleString('pt-BR')} views com ${qtd(props.marco, 'dia', 'dias')} (mediana de ${qtd(p.base.n, 'outro vídeo', 'outros vídeos')})`
}
function repetida(l: Linha, p: PostagemDesempenho): boolean {
  return l.posts.filter((x) => x.plataforma === p.plataforma).length > 1
}

// ---------- tirar do desempenho

const tirando = ref<PostagemDesempenho | null>(null)
const motivo = ref('')
const motivoErro = ref('')
const salvando = ref(false)
const motivoEl = ref<HTMLInputElement | null>(null)

function abrirTirar(p: PostagemDesempenho) {
  tirando.value = p
  motivo.value = ''
  motivoErro.value = ''
  nextTick(() => motivoEl.value?.focus())
}
function fecharTirar() {
  if (salvando.value) return
  tirando.value = null
}
async function confirmarTirar() {
  const m = motivo.value.trim()
  // O motivo é o que explica, meses depois, por que o vídeo não conta.
  if (m.length < 3) {
    motivoErro.value = ERROS_DESEMPENHO.motivo_obrigatorio
    return
  }
  if (!tirando.value || salvando.value) return
  salvando.value = true
  try {
    await api(`/api/marketing/metricas/postagens/${tirando.value.postagem_id}/desempenho`, {
      method: 'PATCH',
      body: { contar: false, motivo: m },
    })
    salvando.value = false
    tirando.value = null
    toasts.success('Vídeo tirado do desempenho.')
    emit('mudou')
  } catch (e: any) {
    if (e?.data?.detail?.code === 'motivo_obrigatorio') motivoErro.value = ERROS_DESEMPENHO.motivo_obrigatorio
    else toasts.error('Não consegui tirar do desempenho', apiErrMsg(e, ERROS_DESEMPENHO))
  } finally {
    salvando.value = false
  }
}
</script>

<template>
  <div class="min-w-0 space-y-2">
    <div class="flex flex-wrap items-center gap-2 text-xs">
      <span class="text-muted-foreground">Ordenar por:</span>
      <div class="flex max-w-full flex-wrap gap-1 rounded-md bg-muted/40 p-1">
        <button
          v-for="o in opcoesOrdem" :key="o.chave"
          class="rounded px-2.5 py-0.5 transition-colors"
          :class="ordemEfetiva === o.chave ? 'bg-background shadow-sm font-medium' : 'hover:bg-background/60 text-muted-foreground'"
          :aria-pressed="ordemEfetiva === o.chave"
          @click="ordem = o.chave"
        >
          {{ o.rotulo }}
        </button>
      </div>
    </div>

    <p v-if="!criativos.length" class="rounded-md border p-6 text-center text-sm text-muted-foreground">
      Nenhum vídeo publicado nos últimos 90 dias.
    </p>

    <div v-else class="overflow-hidden rounded-xl border bg-card">
      <div class="gap-x-3 border-b bg-muted/40 px-3 py-2 text-[11px] text-muted-foreground" :class="grade.cab">
        <span>Vídeo</span>
        <span v-for="r in redes" :key="r" class="inline-flex items-center gap-1">
          <span class="inline-block size-2 shrink-0 rounded-full" :style="{ background: corRede(r) }" />
          {{ ROTULO_REDE[r] || r }} · views com {{ marco }}d
        </span>
        <span class="text-right">Índice</span>
      </div>

      <div v-for="l in visiveis" :key="l.c.creative_id" class="border-b last:border-0">
        <div class="flex flex-col gap-2 px-3 py-2.5" :class="grade.linha">
          <!-- vídeo: título (abre o detalhe) + de onde ele é -->
          <div class="flex min-w-0 items-start gap-2">
            <button
              class="min-w-0 flex-1 text-left"
              :aria-expanded="abertos.has(l.c.creative_id)"
              @click="alternar(l.c.creative_id)"
            >
              <span class="flex min-w-0 items-center gap-1 text-sm font-medium">
                <ChevronRight
                  class="size-3.5 shrink-0 text-muted-foreground transition-transform"
                  :class="abertos.has(l.c.creative_id) && 'rotate-90'"
                />
                <span class="truncate" :title="l.c.titulo">{{ l.c.titulo || 'vídeo sem legenda' }}</span>
              </span>
              <span v-if="l.meta" class="block truncate pl-[18px] text-[11px] text-muted-foreground" :title="l.meta">
                {{ l.meta }}
              </span>
            </button>
            <!-- índice no topo do cartão (celular) -->
            <span class="shrink-0 pt-0.5" :class="grade.celular">
              <span
                v-if="l.c.indice_views !== null && l.c.indice_views !== undefined"
                :class="CHIP[tomIndice(l.c.indice_views) || 'normal']"
                :title="`Mediana dos índices deste vídeo nas redes em que saiu (${l.c.n_indices})`"
              >{{ fmtIndice(l.c.indice_views) }}</span>
            </span>
          </div>

          <!-- uma célula por rede -->
          <div v-for="col in l.colunas" :key="col.rede" class="flex min-w-0 items-start gap-2">
            <span class="inline-flex w-8 shrink-0 items-center gap-1 pt-0.5 text-[11px] text-muted-foreground" :class="grade.celular">
              <span class="inline-block size-1.5 shrink-0 rounded-full" :style="{ background: corRede(col.rede) }" />
              {{ SIGLA_REDE[col.rede] || col.rede }}
            </span>
            <div class="min-w-0 flex-1 space-y-0.5">
              <div
                v-if="col.cel.estado === 'aguardando'"
                class="inline-flex items-center gap-1 text-xs text-muted-foreground"
                :title="col.cel.dica"
              >
                <Clock class="size-3 shrink-0" /> aguardando 1ª leitura
              </div>
              <div
                v-else-if="col.cel.estado === 'falhou'"
                class="inline-flex items-center gap-1 text-xs text-amber-600 dark:text-amber-400"
                :title="col.cel.dica"
              >
                <AlertTriangle class="size-3 shrink-0" /> {{ col.cel.texto }}
              </div>
              <div v-else-if="col.cel.estado === 'ok'" class="flex flex-wrap items-center gap-1.5">
                <span class="text-sm font-medium tabular-nums">{{ col.cel.texto }}</span>
                <span
                  v-if="col.post && col.post.indice_views !== null && col.post.indice_views !== undefined"
                  :class="CHIP[tomIndice(col.post.indice_views) || 'normal']"
                  :title="dicaIndicePost(col.post)"
                >{{ fmtIndice(col.post.indice_views) }}</span>
              </div>
              <div v-else class="text-xs" :class="(col.cel.estado === 'nao_postado' || col.cel.estado === 'apagado') && 'text-muted-foreground'" :title="col.cel.dica || undefined">
                {{ col.cel.texto }}
              </div>
              <div v-if="col.cel.detalhe" class="text-[11px] text-muted-foreground tabular-nums">{{ col.cel.detalhe }}</div>
              <div v-if="col.cel.aviso" class="text-[11px] text-amber-600 dark:text-amber-400" :title="col.post?.erro || ''">
                {{ col.cel.aviso }}
              </div>
              <span v-if="col.extra" class="pill-muted">+{{ qtd(col.extra, 'post', 'posts') }}</span>
            </div>
          </div>

          <!-- índice do criativo (tela larga) -->
          <div class="text-right" :class="grade.tela">
            <span
              v-if="l.c.indice_views !== null && l.c.indice_views !== undefined"
              :class="CHIP[tomIndice(l.c.indice_views) || 'normal']"
              :title="`Mediana dos índices deste vídeo nas redes em que saiu (${l.c.n_indices})`"
            >{{ fmtIndice(l.c.indice_views) }}</span>
            <template v-else>
              <span class="text-sm text-muted-foreground">—</span>
              <span
                v-if="l.semIndice" class="block text-[10px] text-muted-foreground"
                :title="`O índice precisa de ${minimos.base_conta} vídeos da mesma conta com ${qtd(marco, 'dia', 'dias')} de vida.`"
              >{{ l.semIndice }}</span>
            </template>
          </div>
        </div>

        <!-- detalhe do vídeo -->
        <div v-if="abertos.has(l.c.creative_id)" class="space-y-3 border-t bg-muted/20 px-3 py-3">
          <div class="overflow-x-auto">
            <table class="w-full min-w-[34rem] text-xs tabular-nums">
              <thead>
                <tr class="text-left text-[11px] text-muted-foreground">
                  <th class="py-1 pr-3 font-normal">Rede</th>
                  <th class="px-2 py-1 text-right font-normal">views</th>
                  <th class="px-2 py-1 text-right font-normal">curtidas</th>
                  <th class="px-2 py-1 text-right font-normal">comentários</th>
                  <th class="px-2 py-1 text-right font-normal">compart.</th>
                  <th class="px-2 py-1 text-right font-normal">salvos</th>
                  <th class="px-2 py-1 text-right font-normal">interação</th>
                  <th class="py-1 pl-2 font-normal">lido</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="p in l.posts" :key="p.postagem_id" class="border-t border-border/60">
                  <td class="whitespace-nowrap py-1 pr-3">
                    <span class="inline-flex items-center gap-1">
                      <span class="inline-block size-2 shrink-0 rounded-full" :style="{ background: corRede(p.plataforma) }" />
                      {{ ROTULO_REDE[p.plataforma] || p.plataforma }}
                      <span v-if="repetida(l, p)" class="text-muted-foreground">{{ ddmm(p.publicado_em) }}</span>
                    </span>
                  </td>
                  <td v-for="k in METRICAS" :key="k" class="px-2 py-1 text-right">{{ num(p.acumulado ?? {}, k) }}</td>
                  <td class="px-2 py-1 text-right">{{ pct(p.taxa_interacao) }}</td>
                  <td
                    class="whitespace-nowrap py-1 pl-2"
                    :class="frescor(p.lido_em).tom === 'velha' ? 'text-amber-600 dark:text-amber-400' : 'text-muted-foreground'"
                  >
                    {{ frescor(p.lido_em).texto }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <!-- curva: como as views cresceram nos primeiros 14 dias -->
          <div v-if="l.curvas.length" class="space-y-1">
            <p class="text-[11px] text-muted-foreground">Views nos primeiros 14 dias</p>
            <div class="flex flex-wrap gap-4">
              <figure v-for="{ p, g } in l.curvas" :key="p.postagem_id" class="space-y-0.5">
                <svg
                  :viewBox="`0 0 ${g.W} ${g.H}`"
                  class="h-10 w-40"
                  role="img"
                  :aria-label="`views nos primeiros 14 dias no ${ROTULO_REDE[p.plataforma] || p.plataforma}`"
                >
                  <line
                    :x1="g.PAD.l" :x2="g.W - g.PAD.r" :y1="g.H - g.PAD.b" :y2="g.H - g.PAD.b"
                    stroke="currentColor" stroke-opacity="0.08"
                  />
                  <!-- As idades que a tabela compara (1d, 3d, 7d). -->
                  <g v-for="m in g.marcos" :key="m.rotulo">
                    <line
                      :x1="m.x" :x2="m.x" :y1="g.PAD.t" :y2="g.H - g.PAD.b"
                      stroke="currentColor" stroke-opacity="0.2" stroke-dasharray="2 2"
                    />
                    <text :x="m.x" :y="g.H - 1" text-anchor="middle" font-size="7" fill="currentColor" fill-opacity="0.5">
                      {{ m.rotulo }}
                    </text>
                  </g>
                  <path :d="g.d" fill="none" :stroke="corRede(p.plataforma)" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round" />
                </svg>
                <figcaption class="flex items-center gap-1 text-[11px] text-muted-foreground">
                  <span class="inline-block size-1.5 shrink-0 rounded-full" :style="{ background: corRede(p.plataforma) }" />
                  {{ ROTULO_REDE[p.plataforma] || p.plataforma }}<template v-if="repetida(l, p)"> {{ ddmm(p.publicado_em) }}</template>
                  · {{ num(p.acumulado ?? {}, 'views') }}
                </figcaption>
              </figure>
            </div>
          </div>

          <!-- por post: ritmo, link, dica de conta e "tirar do desempenho" -->
          <div
            v-for="p in l.posts" :key="p.postagem_id"
            class="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px]"
          >
            <span class="inline-flex items-center gap-1 text-muted-foreground">
              <span class="inline-block size-1.5 shrink-0 rounded-full" :style="{ background: corRede(p.plataforma) }" />
              {{ ROTULO_REDE[p.plataforma] || p.plataforma }}<template v-if="p.conta"> · @{{ p.conta }}</template>
            </span>
            <span
              v-if="p.ritmo_dia !== null && p.ritmo_dia !== undefined"
              class="tabular-nums"
              :class="p.ritmo_dia > 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-muted-foreground'"
            >
              {{ sinal(Math.round(p.ritmo_dia)) }}/dia agora
            </span>
            <a
              v-if="p.post_url" :href="p.post_url" target="_blank" rel="noopener"
              class="inline-flex items-center gap-0.5 hover:underline"
            >
              abrir <ExternalLink class="size-3" />
            </a>
            <button v-if="canEdit" class="btn btn-xs gap-1" @click="abrirTirar(p)">
              <EyeOff class="size-3" /> Tirar do desempenho
            </button>
            <!-- Só dica: o TikTok mostra sempre o @ atual do dono, então
                 @ diferente quer dizer outra conta — mas quem decide é gente. -->
            <p v-if="p.autor_diferente" class="flex w-full items-start gap-1 text-muted-foreground">
              <Info class="mt-px size-3 shrink-0" />
              <span>
                Este vídeo está em @{{ p.autor_diferente }}, não na conta atual @{{ p.conta }}. Se for de outra
                conta, tire do desempenho.
              </span>
            </p>
          </div>
        </div>
      </div>

      <button
        v-if="restantes > 0"
        class="w-full border-t px-3 py-2 text-xs text-muted-foreground transition-colors hover:bg-muted/40 hover:text-foreground"
        @click="limite += 20"
      >
        mostrar mais {{ Math.min(restantes, 20) }}
      </button>
    </div>

    <!-- tirar do desempenho: sai das somas e da leitura, mas não apaga nada -->
    <div
      v-if="tirando"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      @click.self="fecharTirar"
      @keydown.esc="fecharTirar"
    >
      <div
        role="dialog" aria-modal="true" aria-labelledby="desempenho-tirar-titulo"
        class="w-full max-w-md rounded-lg border border-border bg-card shadow-xl"
      >
        <div class="flex items-center gap-2 border-b px-4 py-3">
          <EyeOff class="size-4 shrink-0 text-muted-foreground" />
          <span id="desempenho-tirar-titulo" class="text-sm font-medium">Tirar este vídeo do desempenho?</span>
          <button class="btn btn-sm btn-ghost ml-auto px-1.5" title="Fechar (Esc)" @click="fecharTirar">
            <X class="size-4" />
          </button>
        </div>
        <div class="space-y-3 px-4 py-4 text-sm">
          <p class="flex min-w-0 items-center gap-1 text-xs text-muted-foreground">
            <span class="inline-block size-2 shrink-0 rounded-full" :style="{ background: corRede(tirando.plataforma) }" />
            <span class="truncate">
              {{ ROTULO_REDE[tirando.plataforma] || tirando.plataforma }}<template v-if="tirando.conta"> · @{{ tirando.conta }}</template>
              · {{ ddmm(tirando.publicado_em) }}<template v-if="tirando.titulo"> · {{ tirando.titulo }}</template>
            </span>
          </p>
          <p>
            Ele sai de todas as somas, médias e comparações e deixa de ser lido. O histórico fica guardado, e dá
            pra voltar a contar quando quiser.
          </p>
          <label class="block space-y-1">
            <span class="text-xs font-medium">Motivo</span>
            <input
              ref="motivoEl"
              v-model="motivo"
              maxlength="200"
              class="w-full rounded-md border bg-background px-2.5 py-1.5 text-sm outline-none focus:ring-2 focus:ring-ring"
              placeholder="ex.: conta antiga, vídeo de teste"
              @input="motivoErro = ''"
              @keydown.enter.prevent="confirmarTirar"
            />
          </label>
          <p v-if="motivoErro" class="text-xs text-red-600 dark:text-red-400">{{ motivoErro }}</p>
        </div>
        <div class="flex items-center justify-end gap-2 border-t px-4 py-3">
          <button class="btn btn-sm btn-ghost" @click="fecharTirar">Cancelar</button>
          <button class="btn btn-sm btn-primary gap-1" :disabled="salvando" @click="confirmarTirar">
            <EyeOff class="size-3.5" /> Tirar do desempenho
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
