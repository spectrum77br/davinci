<script setup lang="ts">
/**
 * Desempenho dos vídeos publicados (Eduardo, 23/09/2026 — v2 em 24/09/2026).
 *
 * v1: views e interações POR MARCA, com o total somado e o detalhe de cada
 * rede, vídeo a vídeo — o número do post publicado pelo DaVinci, não o geral
 * do canal.
 *
 * v2: ele perguntou "qual vídeo rendeu mais" e "onde vale investir". A tabela
 * por marca não respondia isso: somava vídeo de ontem com vídeo de um mês, e
 * conta grande com conta pequena. Agora todo vídeo é comparado NA MESMA IDADE
 * (views com 1, 3 ou 7 dias de publicado) e contra o normal DA PRÓPRIA CONTA
 * (o índice "2,0×"). A tela abre pelo resumo de cada rede, depois vídeo a
 * vídeo (MarketingDesempenhoVideos), depois os grupos que dizem onde investir
 * (MarketingDesempenhoInvestir). A tabela por marca ficou no fim, como filtro.
 *
 * Três decisões de tela que nasceram de limitações reais, não de estética:
 *
 * 1. Métrica que ninguém reportou aparece como "—", nunca como 0. O YouTube
 *    não mede salvamento; o Instagram só dá views com uma permissão que o
 *    token ainda não tem. Zerar afirmaria algo que não sabemos.
 *
 * 2. Removido não é erro. O Eduardo apaga vídeo de teste de propósito; um
 *    alerta âmbar ali esconderia o alerta de verdade no meio do ruído. Vídeo
 *    apagado sai das contas e vira uma linha no rodapé ("Fora do ar").
 *
 * 3. A leitura sempre diz QUANDO foi feita. Coleta falha baixo — sem a data a
 *    tela mostraria número velho com cara de novo, e ninguém notaria. Leitura
 *    com mais de 30 h (a da noite não rodou) fica em âmbar, e leitura que
 *    falhou hoje mostra a última boa com aviso, em vez de sumir com o número.
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { AlertTriangle, ChevronRight, ExternalLink, Info, RefreshCw, RotateCcw } from 'lucide-vue-next'
import { apiErrMsg } from '~/lib/apiError'
import {
  ERROS_DESEMPENHO, ROTULO_REDE, SIGLA_REDE,
  corRede, ddmm, deltaTxt, diaRelativo, frescor, ganho, geomBarras, hhmm,
  maisCompacto, num, qtd, redesDaTabela, sinal,
  type Dimensao, type GrupoDesempenho, type LinhaMarca, type RespostaDesempenho,
} from '~/utils/desempenho'

const { api } = useApi()
const toasts = useToasts()
const canEdit = useCan('marketing_criativos', 'edit')

const dias = ref(30)
const marco = ref(3)
const marcaId = ref<string | null>(null)
const dados = ref<RespostaDesempenho | null>(null)
// Primeira carga mostra esqueleto; troca de filtro mantém a tela anterior
// esmaecida — piscar esqueleto a cada clique faz a tela parecer quebrada.
const carregando = ref(false)
const recarregando = ref(false)
const erro = ref<string | null>(null)
// Relógio da tela: "lido hoje às…", "faltam 2 dias" e o botão travado de
// "Atualizar agora" dependem da hora, não só dos dados.
const agora = ref(Date.now())

// Servidor com a API v1 (deploy pela metade) não pode derrubar a aba: o que
// faltar vira vazio, e a tela mostra "—" e listas vazias.
function normalizar(r: Partial<RespostaDesempenho> | null): RespostaDesempenho {
  const x = (r ?? {}) as any
  return {
    ...x,
    minimos: { base_conta: 5, views_taxa: 100, indicio: 3, comparavel: 8, tolerancia_h: 36, ...(x.minimos ?? {}) },
    coleta: {
      ultima_leitura_em: null, proxima_leitura_em: null, proxima_noturna_em: null, inicio_da_coleta: null,
      em_andamento: false, pode_atualizar_em: null, ultima_rodada: null, ...(x.coleta ?? {}),
    },
    marcas_disponiveis: x.marcas_disponiveis ?? [],
    resumo: {
      videos: { no_ar: 0, aguardando: 0, com_falha: 0, fora_do_ar: 0, fora_do_desempenho: 0, ...(x.resumo?.videos ?? {}) },
      redes: x.resumo?.redes ?? [],
      tendencia: { views_no_periodo: null, redes_sem_views: [], ...(x.resumo?.tendencia ?? {}) },
    },
    serie_dias: x.serie_dias ?? [],
    postagens: x.postagens ?? [],
    criativos: x.criativos ?? [],
    grupos: { produto: [], formato: [], agencia: [], roteiro: [], horario: [], ...(x.grupos ?? {}) } as Record<Dimensao, GrupoDesempenho[]>,
    marcas: x.marcas ?? [],
    sem_video_no_ar: x.sem_video_no_ar ?? [],
    fora_do_ar: x.fora_do_ar ?? [],
    fora_do_desempenho: x.fora_do_desempenho ?? [],
  }
}

// Filtro trocado duas vezes seguidas: a resposta velha pode chegar depois da
// nova. Só a última chamada escreve na tela.
let geracao = 0
async function carregar(silencioso = false) {
  const minha = ++geracao
  if (!silencioso) {
    if (dados.value) recarregando.value = true
    else carregando.value = true
    erro.value = null
  }
  const q = new URLSearchParams({ dias: String(dias.value), marco: String(marco.value) })
  if (marcaId.value) q.set('marca_id', marcaId.value)
  try {
    const r = await api<RespostaDesempenho>(`/api/marketing/metricas?${q.toString()}`)
    if (minha !== geracao) return
    dados.value = normalizar(r)
    agora.value = Date.now()
  } catch (e: any) {
    // A checagem silenciosa (depois do "Atualizar agora") falhar não apaga
    // número bom: a próxima tentativa resolve.
    if (minha !== geracao || silencioso) return
    erro.value = e?.data?.detail?.code || (e?.statusCode ? `HTTP ${e.statusCode}` : e?.message) || 'erro'
    dados.value = null
  } finally {
    if (minha === geracao) {
      carregando.value = false
      recarregando.value = false
    }
  }
}
watch([dias, marco, marcaId], () => carregar())

let relogio: number | undefined
onMounted(() => {
  carregar()
  relogio = window.setInterval(() => { agora.value = Date.now() }, 30_000)
})

// ---------- Atualizar agora
//
// O servidor lê tudo às 23:47 e os vídeos novos de hora em hora (:47). O botão
// pede uma leitura fora dessa agenda; o servidor segura um pedido a cada 10
// min (429), então o botão fica travado até lá.
const atualizando = ref(false)
const pedidoEm = ref<string | null>(null)
const aindaRodando = ref(false)
const podeAtualizarLocal = ref<string | null>(null)
let timerChecagem: number | undefined
// Aba trocada (Criativos desmonta esta) com uma checagem em voo: o GET
// voltava depois do unmount e armava o timer seguinte, que ninguém mais
// limpava — até ~23 min de GET pesado e o toast "Números atualizados"
// aparecendo em outra aba. Desmontou, para.
let desmontado = false
// 12 checagens de 15 s (3 min) cobrem uma leitura normal. Depois disso a tela
// segue olhando de minuto em minuto — é o que faz valer o "os números
// aparecem sozinhos" — até o cadeado de 30 min do servidor vencer.
const CHECAGENS_RAPIDAS = 12
const CHECAGENS_LENTAS = 20

const podeAtualizarEm = computed<string | null>(() => {
  const t = [dados.value?.coleta.pode_atualizar_em, podeAtualizarLocal.value]
    .map((s) => (s ? Date.parse(s) : NaN))
    .filter((x) => !Number.isNaN(x))
  const max = t.length ? Math.max(...t) : 0
  return max > agora.value ? new Date(max).toISOString() : null
})
const rodadaTerminou = computed(() => {
  const fim = dados.value?.coleta.ultima_rodada?.fim
  return !!(fim && pedidoEm.value && Date.parse(fim) > Date.parse(pedidoEm.value))
})

async function atualizarAgora() {
  if (atualizando.value || podeAtualizarEm.value) return
  atualizando.value = true
  aindaRodando.value = false
  try {
    const r = await api<{ enfileirado: boolean; pedido_em: string; pode_atualizar_em: string }>(
      '/api/marketing/metricas/atualizar',
      { method: 'POST' },
    )
    podeAtualizarLocal.value = r.pode_atualizar_em
    pedidoEm.value = r.pedido_em
    acompanhar(0)
  } catch (e: any) {
    atualizando.value = false
    const det = e?.data?.detail
    if (e?.statusCode === 429 || det?.code === 'atualizacao_recente') {
      if (det?.pode_atualizar_em) podeAtualizarLocal.value = det.pode_atualizar_em
      toasts.warning(det?.pode_atualizar_em
        ? `Já atualizei há pouco — dá pra pedir de novo às ${hhmm(det.pode_atualizar_em)}.`
        : 'Já atualizei há pouco — dá pra pedir de novo em alguns minutos.')
    } else {
      toasts.error(`Não consegui pedir a leitura agora (${det?.code || e?.statusCode || 'erro'}).`)
    }
  }
}

function acompanhar(tentativa: number) {
  if (desmontado) return
  window.clearTimeout(timerChecagem)
  timerChecagem = window.setTimeout(async () => {
    // Carga do usuário (troca de filtro) em andamento: ela já traz a coleta.
    if (!carregando.value && !recarregando.value) await carregar(true)
    if (desmontado) return
    if (rodadaTerminou.value) {
      atualizando.value = false
      aindaRodando.value = false
      toasts.success(`Números atualizados às ${hhmm(dados.value?.coleta.ultima_rodada?.fim)}.`)
      return
    }
    if (tentativa + 1 === CHECAGENS_RAPIDAS) {
      atualizando.value = false
      aindaRodando.value = true
    }
    if (tentativa + 1 < CHECAGENS_RAPIDAS + CHECAGENS_LENTAS) acompanhar(tentativa + 1)
  }, tentativa < CHECAGENS_RAPIDAS ? 15_000 : 60_000)
}

onBeforeUnmount(() => {
  desmontado = true
  window.clearTimeout(timerChecagem)
  window.clearInterval(relogio)
})

// ---------- linha de status

const leitura = computed(() => frescor(dados.value?.coleta.ultima_leitura_em, agora.value))
const proximaNoite = computed(() => {
  const iso = dados.value?.coleta.proxima_noturna_em
  return iso ? `${diaRelativo(iso, agora.value)} às ${hhmm(iso)}` : 'hoje às 23:47'
})
const aguardando = computed(() => dados.value?.resumo.videos.aguardando ?? 0)

// ---------- resumo do período

const cartoes = computed(() => {
  const d = dados.value
  if (!d) return []
  return d.resumo.redes.map((r) => {
    const serie = r.serie ?? []
    const estimados = new Set(r.estimado_dias ?? [])
    const metrica = r.metrica_serie === 'curtidas' ? 'curtidas' : 'views'
    const g = geomBarras(serie, 100, 28)
    return {
      r,
      // Conta sem views (Instagram sem insights) mostra o total de curtidas,
      // o mesmo número do título do cartão — "total — views" não diz nada.
      metrica,
      barras: g.barras.map((b) => {
        const hoje = b.i === serie.length - 1
        const estimado = estimados.has(d.serie_dias[b.i])
        let dica = `${ddmm(d.serie_dias[b.i])}: ${sinal(b.v)} ${metrica}`
        if (hoje) dica += ' — hoje (parcial)'
        if (estimado) dica += ' — estimado: ganho de vários dias dividido igualmente entre eles'
        // Hoje ainda está acontecendo; o estimado é conta, não leitura.
        return { ...b, dica, opacidade: hoje ? 0.4 : estimado ? 0.55 : 1 }
      }),
      passo: serie.length ? 100 / serie.length : 100,
      // A porcentagem compara DIAS FECHADOS, do mesmo tamanho (o servidor
      // manda o par). O título tem hoje pela metade — lido só às 23:47 —, e
      // contra 7 dias inteiros o fluxo parado aparecia como "▼ 14%".
      delta: deltaTxt(r.comparacao?.atual, r.comparacao?.anterior),
      deltaDica: r.comparacao?.ate
        ? `Compara só dias fechados: os ${qtd(d.dias, 'dia', 'dias')} até ${ddmm(r.comparacao.ate)} com os ${d.dias} anteriores a eles.`
        : '',
      lido: frescor(r.lido_em, agora.value),
    }
  })
})
const redesSemViews = computed(() =>
  (dados.value?.resumo.tendencia.redes_sem_views ?? []).map((r) => ROTULO_REDE[r] ?? r).join(' e '),
)

// ---------- comparação

const semIndice = computed(() =>
  !!dados.value?.postagens.length && !dados.value.postagens.some((p) => p.indice_views !== null && p.indice_views !== undefined),
)

// ---------- por marca

const redesMarcas = computed(() =>
  redesDaTabela((dados.value?.marcas ?? []).flatMap((m) => m.plataformas.map((p) => p.plataforma))),
)
// Com o Facebook entra uma quarta coluna e a grade só cabe numa tela maior (a
// barra lateral fixa do DaVinci come até 248 px). Classes escritas por
// extenso pro Tailwind enxergar.
const GRADE_MARCAS = {
  3: {
    cab: 'hidden lg:grid lg:grid-cols-[minmax(0,1fr)_repeat(3,7rem)_6rem]',
    linha: 'grid-cols-3 lg:grid-cols-[minmax(0,1fr)_repeat(3,7rem)_6rem]',
    larga: 'col-span-3 lg:col-span-1',
    celular: 'lg:hidden',
    direita: 'lg:text-right',
  },
  4: {
    cab: 'hidden xl:grid xl:grid-cols-[minmax(0,1fr)_repeat(4,7rem)_6rem]',
    linha: 'grid-cols-4 xl:grid-cols-[minmax(0,1fr)_repeat(4,7rem)_6rem]',
    larga: 'col-span-4 xl:col-span-1',
    celular: 'xl:hidden',
    direita: 'xl:text-right',
  },
}
const gradeMarcas = computed(() => GRADE_MARCAS[redesMarcas.value.length > 3 ? 4 : 3])

function plat(m: LinhaMarca, rede: string) {
  return m.plataformas.find((p) => p.plataforma === rede)
}
function filtrarMarca(id: string | null | undefined) {
  if (!id) return
  marcaId.value = marcaId.value === id ? null : id
}

// ---------- rodapés

const foraAberto = ref(false)
const voltando = ref<string | null>(null)

async function voltarAContar(postagemId: string) {
  if (voltando.value) return
  voltando.value = postagemId
  try {
    await api(`/api/marketing/metricas/postagens/${postagemId}/desempenho`, {
      method: 'PATCH',
      body: { contar: true, motivo: null },
    })
    toasts.success('Vídeo voltou a contar.')
    await carregar()
  } catch (e: any) {
    toasts.error('Não consegui voltar a contar', apiErrMsg(e, ERROS_DESEMPENHO))
  } finally {
    voltando.value = null
  }
}

// Nada publicado de verdade (não é filtro de marca escondendo): a tela diz
// que o vídeo aparece na hora, pra ninguém achar que a aba quebrou.
const nadaPublicado = computed(() => {
  const d = dados.value
  return !!d && !marcaId.value && !d.postagens.length && !d.fora_do_ar.length && !d.fora_do_desempenho.length
})
</script>

<template>
  <div class="desempenho min-w-0 space-y-4">
    <!-- barra de cima: marca, período, recarregar e "Atualizar agora" -->
    <div class="flex flex-wrap items-center gap-2">
      <div
        v-if="dados?.marcas_disponiveis.length"
        class="flex max-w-full gap-1 overflow-x-auto rounded-md bg-muted/40 p-1"
      >
        <button
          class="shrink-0 whitespace-nowrap rounded px-3 py-1 text-sm transition-colors"
          :class="marcaId === null ? 'bg-background shadow-sm font-medium' : 'hover:bg-background/60 text-muted-foreground'"
          :aria-pressed="marcaId === null"
          @click="marcaId = null"
        >
          Todas as marcas
        </button>
        <button
          v-for="m in dados.marcas_disponiveis" :key="m.id"
          class="shrink-0 whitespace-nowrap rounded px-3 py-1 text-sm transition-colors"
          :class="marcaId === m.id ? 'bg-background shadow-sm font-medium' : 'hover:bg-background/60 text-muted-foreground'"
          :aria-pressed="marcaId === m.id"
          @click="marcaId = m.id"
        >
          {{ m.nome }}
        </button>
      </div>
      <div class="flex w-fit gap-1 rounded-md bg-muted/40 p-1">
        <button
          v-for="d in [7, 30, 90]" :key="d"
          class="px-3 py-1 rounded text-sm transition-colors"
          :class="dias === d ? 'bg-background shadow-sm font-medium' : 'hover:bg-background/60 text-muted-foreground'"
          :aria-pressed="dias === d"
          @click="dias = d"
        >
          {{ d }} dias
        </button>
      </div>
      <div class="ml-auto flex items-center gap-2">
        <button
          class="btn btn-sm btn-ghost px-1.5"
          title="recarregar" aria-label="recarregar"
          :disabled="carregando || recarregando" @click="carregar()"
        >
          <RefreshCw class="size-3.5" :class="(carregando || recarregando) && 'animate-spin'" />
        </button>
        <!-- O title fica no span: botão desabilitado não mostra dica em todo navegador. -->
        <span
          :title="podeAtualizarEm && !atualizando
            ? `Atualizado há pouco — dá pra pedir de novo às ${hhmm(podeAtualizarEm)}.`
            : ''"
        >
          <button
            class="btn btn-sm gap-1.5 whitespace-nowrap"
            :disabled="atualizando || !!podeAtualizarEm"
            @click="atualizarAgora"
          >
            <RefreshCw class="size-3.5" :class="atualizando && 'animate-spin'" />
            {{ atualizando ? 'Atualizando…' : 'Atualizar agora' }}
          </button>
        </span>
      </div>
    </div>

    <!-- linha de status: de quando é o número e quando vem o próximo -->
    <div v-if="dados" class="space-y-2">
      <p class="text-xs text-muted-foreground">
        <span v-if="leitura.tom === 'velha'" class="text-amber-600 dark:text-amber-400">
          A última leitura foi {{ leitura.texto }} — a leitura da noite pode não ter rodado.
        </span>
        <span v-else>Última leitura: {{ leitura.texto }}</span>
        · próxima leitura de todos os vídeos: {{ proximaNoite }} · vídeo novo é lido em até 1 hora.
      </p>
      <p v-if="aindaRodando && !rodadaTerminou" class="text-xs text-muted-foreground">
        A leitura ainda está rodando — os números aparecem sozinhos quando terminar.
      </p>
      <div
        v-if="aguardando > 0"
        class="flex items-start gap-2 rounded-md border bg-muted/30 px-3 py-2 text-xs text-muted-foreground"
      >
        <Info class="mt-0.5 size-3.5 shrink-0" />
        <span>
          {{ qtd(aguardando, 'vídeo publicado', 'vídeos publicados') }} ainda sem número. Vídeo novo é lido em
          até 1 hora depois de publicado — ou clique em Atualizar agora.
        </span>
      </div>
    </div>

    <!-- estados da página: primeira carga, erro, nada publicado -->
    <div v-if="carregando && !dados" class="space-y-3" aria-busy="true">
      <div class="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <div v-for="i in 4" :key="i" class="h-24 animate-pulse rounded-xl bg-muted/40" />
      </div>
      <div class="space-y-2">
        <div v-for="i in 4" :key="i" class="h-12 animate-pulse rounded-md bg-muted/40" />
      </div>
    </div>

    <div
      v-else-if="erro && !dados"
      class="space-y-2 rounded-md border border-amber-500/30 bg-amber-500/10 p-4 text-sm text-amber-700 dark:text-amber-400"
    >
      <p class="flex items-center gap-1.5">
        <AlertTriangle class="size-4 shrink-0" /> Não consegui carregar o desempenho agora.
      </p>
      <div class="flex flex-wrap items-center gap-3">
        <button class="btn btn-sm" @click="carregar()">Tentar de novo</button>
        <span class="break-all text-[11px] opacity-80">{{ erro }}</span>
      </div>
    </div>

    <div
      v-else-if="nadaPublicado"
      class="rounded-md border p-6 text-center text-sm text-muted-foreground"
    >
      Nenhum vídeo publicado pelo DaVinci nos últimos 90 dias. Quando você ou o robô publicar, ele aparece
      aqui na hora e ganha o primeiro número em até 1 hora.
    </div>

    <div
      v-else-if="dados"
      class="space-y-6 transition-opacity"
      :class="recarregando && 'opacity-60 pointer-events-none'"
    >
      <!-- resumo do período: um cartão por rede + o de tendência -->
      <section class="space-y-2">
        <h3 class="text-sm font-semibold">
          Resumo do período <span class="font-normal text-muted-foreground">({{ ddmm(dados.desde) }} a {{ ddmm(dados.ate) }})</span>
        </h3>
        <div class="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <div v-for="c in cartoes" :key="c.r.plataforma" class="min-w-0 space-y-2 rounded-xl border bg-card p-4">
            <div class="flex items-center gap-1.5 text-sm font-medium">
              <span class="inline-block size-2 shrink-0 rounded-full" :style="{ background: corRede(c.r.plataforma) }" />
              {{ ROTULO_REDE[c.r.plataforma] || c.r.plataforma }}
            </div>
            <div>
              <p v-if="c.r.sem_views" class="text-2xl font-semibold tabular-nums">
                {{ maisCompacto(c.r.no_periodo?.curtidas) }} <span class="text-sm font-normal text-muted-foreground">curtidas</span>
              </p>
              <p v-else class="text-2xl font-semibold tabular-nums">
                {{ maisCompacto(c.r.no_periodo?.views) }} <span class="text-sm font-normal text-muted-foreground">views</span>
              </p>
              <p v-if="c.r.sem_views" class="text-[11px] text-muted-foreground">
                Views indisponíveis: a conta ainda não liberou insights pro DaVinci.
              </p>
              <!-- Rede só com vídeo novo: ainda não tem número, e não é problema. -->
              <p v-else-if="!c.r.videos && c.r.aguardando" class="text-[11px] text-muted-foreground">
                {{ qtd(c.r.aguardando, 'vídeo', 'vídeos') }} aguardando 1ª leitura
              </p>
              <p
                v-if="c.delta" class="text-xs tabular-nums" :title="c.deltaDica"
                :class="c.delta.sobe ? 'text-emerald-600 dark:text-emerald-400' : 'text-muted-foreground'"
              >
                {{ c.delta.texto }} vs os {{ dados.dias }} dias anteriores
              </p>
              <p v-else-if="c.r.periodo_anterior === null || c.r.periodo_anterior === undefined" class="text-xs text-muted-foreground">
                sem base de comparação ainda<template v-if="dados.coleta.inicio_da_coleta"> (a leitura começou em {{ ddmm(dados.coleta.inicio_da_coleta) }})</template>
              </p>
            </div>
            <!-- Ganho por dia. Barra some em dia sem dado (null); ganho
                 negativo desenha 0 e o sinal fica no title. -->
            <svg viewBox="0 0 100 28" preserveAspectRatio="none" class="h-7 w-full" role="img" :aria-label="`ganho por dia no ${ROTULO_REDE[c.r.plataforma] || c.r.plataforma}`">
              <line x1="0" x2="100" y1="27.5" y2="27.5" stroke="currentColor" stroke-opacity="0.08" vector-effect="non-scaling-stroke" />
              <g v-for="b in c.barras" :key="b.i">
                <title>{{ b.dica }}</title>
                <rect :x="b.i * c.passo" y="0" :width="c.passo" height="28" fill="transparent" />
                <rect :x="b.x" :y="b.y" :width="b.w" :height="b.h" :fill="corRede(c.r.plataforma)" :fill-opacity="b.opacidade" />
              </g>
            </svg>
            <p class="text-xs text-muted-foreground tabular-nums">
              {{ qtd(c.r.videos, 'vídeo', 'vídeos') }}
              · {{ c.r.interacoes_no_periodo === null || c.r.interacoes_no_periodo === undefined ? '—' : sinal(c.r.interacoes_no_periodo) }} interações
              · total {{ num(c.r.acumulado ?? {}, c.metrica) }} {{ c.metrica }}
            </p>
            <p class="text-[11px]" :class="c.lido.tom === 'velha' ? 'text-amber-600 dark:text-amber-400' : 'text-muted-foreground'">
              {{ c.lido.tom === 'nenhuma' ? c.lido.texto : `lido ${c.lido.texto}` }}
            </p>
            <p
              v-if="c.r.com_falha > 0" :title="c.r.erro || ''"
              class="flex items-center gap-1 text-[11px] text-amber-600 dark:text-amber-400"
            >
              <AlertTriangle class="size-3 shrink-0" />
              {{ qtd(c.r.com_falha, 'vídeo', 'vídeos') }} com a leitura falhando
            </p>
          </div>

          <!-- Soma das redes: só tendência. A ressalva fica NO cartão, não em
               rodapé — o número existe, mas não compara nada. -->
          <div v-if="cartoes.length" class="min-w-0 space-y-2 rounded-xl border bg-card p-4">
            <div class="text-sm font-medium">Todas as redes (só tendência)</div>
            <p class="text-2xl font-semibold tabular-nums">
              {{ maisCompacto(dados.resumo.tendencia.views_no_periodo) }} <span class="text-sm font-normal text-muted-foreground">views</span>
            </p>
            <p class="text-[11px] text-muted-foreground">
              “View” não conta igual em cada rede — este total serve só pra ver a direção. Pra comparar vídeos,
              use as tabelas abaixo.
            </p>
            <p v-if="redesSemViews" class="text-[11px] text-muted-foreground">
              {{ redesSemViews }} fora da soma: sem views.
            </p>
          </div>
        </div>
      </section>

      <!-- cabeçalho da comparação (vale pras duas tabelas de baixo) -->
      <section class="space-y-3">
        <div class="flex flex-wrap items-center gap-2">
          <span class="text-sm">Comparar vídeos na idade de:</span>
          <div class="flex w-fit gap-1 rounded-md bg-muted/40 p-1">
            <button
              v-for="m in [1, 3, 7]" :key="m"
              class="px-3 py-1 rounded text-sm transition-colors"
              :class="marco === m ? 'bg-background shadow-sm font-medium' : 'hover:bg-background/60 text-muted-foreground'"
              :aria-pressed="marco === m"
              @click="marco = m"
            >
              {{ qtd(m, 'dia', 'dias') }}
            </button>
          </div>
        </div>
        <div class="flex items-start gap-2 rounded-md border bg-muted/30 px-3 py-2.5 text-xs text-muted-foreground">
          <Info class="mt-0.5 size-3.5 shrink-0" />
          <div class="space-y-1.5">
            <p>
              <strong class="font-medium text-foreground">Retorno, aqui, é atenção (views) e interesse (curtidas,
              comentários, compartilhamentos e salvamentos).</strong> Quanto cada vídeo
              <strong class="font-medium text-foreground">vendeu</strong> não é medido: os posts não levam link
              rastreado nem cupom.
            </p>
            <p>
              Todo vídeo é comparado <strong class="font-medium text-foreground">na mesma idade</strong>
              (views com {{ qtd(marco, 'dia', 'dias') }} de publicado), pra vídeo antigo não ganhar só por ter tido
              mais tempo.
            </p>
            <p>
              “2,0×” = o dobro do <strong class="font-medium text-foreground">normal daquela conta naquela rede</strong>
              (mediana dos outros vídeos dela nos últimos 90 dias). Assim conta pequena não perde só por ser
              pequena, e Instagram nunca é comparado direto com TikTok.
            </p>
          </div>
        </div>
        <p v-if="semIndice" class="text-xs text-muted-foreground">
          O índice aparece quando a conta tiver {{ dados.minimos.base_conta }} vídeos com
          {{ qtd(marco, 'dia', 'dias') }} de vida. Por enquanto, compare as views dentro de cada coluna (mesma rede).
        </p>
      </section>

      <!-- vídeo a vídeo -->
      <section class="space-y-2">
        <h3 class="text-sm font-semibold">Qual vídeo rendeu mais</h3>
        <MarketingDesempenhoVideos
          :postagens="dados.postagens"
          :criativos="dados.criativos"
          :fora-do-ar="dados.fora_do_ar"
          :marco="marco"
          :minimos="dados.minimos"
          :can-edit="canEdit"
          @mudou="carregar()"
        />
      </section>

      <!-- grupos: onde vale investir -->
      <section class="space-y-2">
        <h3 class="text-sm font-semibold">Onde vale investir</h3>
        <MarketingDesempenhoInvestir :grupos="dados.grupos" :marco="marco" :minimos="dados.minimos" />
      </section>

      <!-- por marca — virou filtro: clicar na marca filtra a tela toda -->
      <section v-if="dados.marcas.length" class="space-y-2">
        <div class="flex flex-wrap items-baseline gap-x-2">
          <h3 class="text-sm font-semibold">Por marca</h3>
          <span class="text-[11px] text-muted-foreground">clique numa marca pra filtrar a tela</span>
        </div>
        <div class="overflow-hidden rounded-xl border bg-card">
          <div class="gap-x-3 border-b bg-muted/40 px-3 py-2 text-[11px] text-muted-foreground" :class="gradeMarcas.cab">
            <span>Marca</span>
            <span v-for="r in redesMarcas" :key="r" class="inline-flex items-center gap-1 justify-self-end">
              <span class="inline-block size-2 shrink-0 rounded-full" :style="{ background: corRede(r) }" />
              {{ ROTULO_REDE[r] || r }}
            </span>
            <span class="text-right">Vídeos no ar</span>
          </div>
          <button
            v-for="m in dados.marcas" :key="m.marca_id || m.marca"
            class="grid w-full items-baseline gap-x-3 gap-y-1 border-b px-3 py-2 text-left transition-colors last:border-0 hover:bg-muted/40"
            :class="[gradeMarcas.linha, m.marca_id && marcaId === m.marca_id && 'bg-muted/40']"
            :aria-pressed="!!m.marca_id && marcaId === m.marca_id"
            @click="filtrarMarca(m.marca_id)"
          >
            <span class="min-w-0 truncate text-sm font-medium" :class="gradeMarcas.larga">{{ m.marca }}</span>
            <span v-for="r in redesMarcas" :key="r" class="min-w-0 text-sm tabular-nums" :class="gradeMarcas.direita">
              <span class="flex items-center gap-1 text-[10px] text-muted-foreground" :class="gradeMarcas.celular">
                <span class="inline-block size-1.5 shrink-0 rounded-full" :style="{ background: corRede(r) }" />
                {{ SIGLA_REDE[r] || r }}
              </span>
              {{ num(plat(m, r)?.acumulado ?? {}, 'views') }}
              <!-- Emerald é ganho: o negativo (o YouTube tira view de robô) sai neutro. -->
              <span
                v-if="ganho(plat(m, r)?.no_periodo ?? {}, 'views')" class="text-[10px]"
                :class="(plat(m, r)?.no_periodo?.views ?? 0) > 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-muted-foreground'"
              >
                {{ ganho(plat(m, r)?.no_periodo ?? {}, 'views') }}
              </span>
            </span>
            <span class="text-xs tabular-nums text-muted-foreground" :class="[gradeMarcas.larga, gradeMarcas.direita]">
              <span :class="gradeMarcas.celular">vídeos no ar: </span>{{ m.posts }}<template v-if="m.aguardando"> +{{ m.aguardando }} aguardando</template>
            </span>
          </button>
        </div>
      </section>

      <!-- rodapés -->
      <div class="space-y-1.5 text-[11px] text-muted-foreground">
        <div v-if="dados.fora_do_desempenho.length">
          <button
            class="inline-flex items-center gap-1 hover:text-foreground"
            :aria-expanded="foraAberto"
            @click="foraAberto = !foraAberto"
          >
            <ChevronRight class="size-3 transition-transform" :class="foraAberto && 'rotate-90'" />
            Fora do desempenho ({{ dados.fora_do_desempenho.length }})
          </button>
          — não entram em soma, média nem comparação, e não são mais lidos.
          <ul v-if="foraAberto" class="ml-4 mt-1 space-y-1">
            <li
              v-for="f in dados.fora_do_desempenho" :key="f.postagem_id"
              class="flex flex-wrap items-center gap-x-1.5 gap-y-1"
            >
              <span class="inline-block size-2 shrink-0 rounded-full" :style="{ background: corRede(f.plataforma) }" />
              <span>{{ ROTULO_REDE[f.plataforma] || f.plataforma }}</span>
              <template v-if="f.conta">· <span class="break-all">@{{ f.conta }}</span></template>
              <template v-if="f.publicado_em">· {{ ddmm(f.publicado_em) }}</template>
              <template v-if="f.motivo">· <span class="break-words">{{ f.motivo }}</span></template>
              <template v-if="f.post_url">
                ·
                <a :href="f.post_url" target="_blank" rel="noopener" class="inline-flex items-center gap-0.5 hover:underline">
                  abrir <ExternalLink class="size-3" />
                </a>
              </template>
              <button
                v-if="canEdit"
                class="btn btn-xs ml-1 gap-1"
                :disabled="voltando === f.postagem_id"
                @click="voltarAContar(f.postagem_id)"
              >
                <RotateCcw class="size-3" /> Voltar a contar
              </button>
            </li>
          </ul>
        </div>
        <!-- Apagado não é erro (decisão 2): linha neutra, sem âmbar. -->
        <p v-if="dados.fora_do_ar.length">
          Fora do ar: {{ qtd(dados.fora_do_ar.length, 'vídeo apagado', 'vídeos apagados') }} das redes — não contam mais.
        </p>
        <p v-if="dados.sem_video_no_ar.length">
          Sem vídeo no ar: {{ dados.sem_video_no_ar.join(', ') }} — o histórico continua guardado, e a marca volta a
          aparecer no primeiro vídeo novo.
        </p>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* Cor de cada rede (paleta testada pra daltonismo). A cor marca a bolinha,
   a barra e a curva — texto nunca vai na cor da rede, que não tem contraste
   garantido no fundo. Emerald = ganho/acima do normal; âmbar só pra problema
   de verdade (leitura falhou ou velha). */
.desempenho {
  --rede-instagram: #eb6834;
  --rede-youtube: #2a78d6;
  --rede-tiktok: #1baf7a;
  --rede-facebook: #4a3aa7;
}
.dark .desempenho {
  --rede-instagram: #d95926;
  --rede-youtube: #3987e5;
  --rede-tiktok: #199e70;
  --rede-facebook: #9085e9;
}
</style>
