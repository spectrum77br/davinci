<script setup lang="ts">
/**
 * "Onde vale investir" — os vídeos agrupados por produto (aparelho), formato,
 * agência, roteiro e horário: quantas views cada grupo trouxe na semana e no
 * mês, e quantos vídeos publicou nesses dias.
 *
 * Até 05/10 cada grupo vinha "contra o normal da conta" (1,7×, "indício",
 * "pouco dado — não conclua ainda"), e isso confundia mais do que ajudava
 * (Eduardo, 06/10/2026: trazer os produtos que mais trouxeram views, e
 * quantos vídeos e views cada agência fez na semana e no mês). Agora são
 * números planos: views somadas, quantos vídeos, views por vídeo e o vídeo
 * campeão com o link.
 *
 * As views da semana/mês são as GANHAS nesses dias por todos os vídeos do
 * grupo — o vídeo de três semanas que continua rendendo conta, e a soma fecha
 * com o Resumo de 7/30 dias. Até 06/10 eram só as views dos vídeos publicados
 * na janela, e a agência aparecia com menos views do que de fato atraiu.
 * "Views por vídeo" é a média do que os vídeos publicados no mês têm até hoje.
 *
 * O grupo conta VÍDEOS (criativos), não posts: o mesmo vídeo em três redes é
 * um vídeo só, com as views das três somadas. Só o Horário conta postagens.
 */
import { computed, ref } from 'vue'
import { ExternalLink } from 'lucide-vue-next'
import {
  ORDEM_REDES, ROTULO_REDE,
  ddmm, fmtViews, ordenarGrupos, qtd,
  type Dimensao, type GrupoDesempenho, type JanelaGrupo, type Janelas,
} from '~/utils/desempenho'

const props = defineProps<{
  grupos: Record<Dimensao, GrupoDesempenho[]>
  janelas: Janelas
}>()

const DIMENSOES: { chave: Dimensao; rotulo: string }[] = [
  { chave: 'produto', rotulo: 'Produto' },
  { chave: 'formato', rotulo: 'Formato' },
  { chave: 'agencia', rotulo: 'Agência' },
  { chave: 'roteiro', rotulo: 'Roteiro' },
  { chave: 'horario', rotulo: 'Horário' },
]
// 'mes' é a ordem do servidor (o sort é estável: o desempate dele fica).
const ORDENS = [
  { chave: 'mes', rotulo: 'views no mês' },
  { chave: 'semana', rotulo: 'views na semana' },
  { chave: 'por_video', rotulo: 'views por vídeo' },
]
const COLUNAS = 'lg:grid-cols-[minmax(0,1.5fr)_7.5rem_7.5rem_6.5rem_minmax(0,1fr)]'

const dim = ref<Dimensao>('produto')
const ordem = ref('mes')
const rotuloDim = computed(() => DIMENSOES.find((d) => d.chave === dim.value)?.rotulo ?? '')
// Horário compara POSTAGENS (o mesmo vídeo sai às 12h numa rede e às 19h noutra).
const porPostagem = computed(() => dim.value === 'horario')
// Postagem é feminino: "todas as postagens", "postagem antiga", "quantas".
const unidade = computed(() => (porPostagem.value
  ? { um: 'postagem', varios: 'postagens', publicado: 'publicada', publicados: 'publicadas', todos: 'todas as', antigo: 'antiga', quantos: 'quantas' }
  : { um: 'vídeo', varios: 'vídeos', publicado: 'publicado', publicados: 'publicados', todos: 'todos os', antigo: 'antigo', quantos: 'quantos' }))
const diasSemana = computed(() => props.janelas?.semana?.dias ?? 7)
const diasMes = computed(() => props.janelas?.mes?.dias ?? 30)

const lista = computed(() => ordenarGrupos(props.grupos?.[dim.value] ?? [], ordem.value))
// "(sem roteiro)" junta a maior parte dos vídeos: não entra na escala da barra,
// senão achata todos os outros.
const maxMes = computed(() => Math.max(0, ...lista.value.filter((g) => g.chave !== 'nenhum').map((g) => g.mes?.views ?? 0)))

// Embaixo das views da janela: quantos vídeos o grupo PUBLICOU nela.
function contagem(j: JanelaGrupo | undefined): string {
  if (!j) return ''
  const u = unidade.value
  return qtd(j.videos, `${u.um} ${u.publicado}`, `${u.varios} ${u.publicados}`)
}

function dicaMedia(j: JanelaGrupo | undefined): string {
  if (!j || j.media === null || j.media === undefined) return ''
  const u = unidade.value
  let t = `média das views até hoje de ${qtd(j.com_numero, `${u.um} ${u.publicado}`, `${u.varios} ${u.publicados}`)} no mês`
  if (j.views_dos_publicados !== null && j.views_dos_publicados !== undefined) {
    t += ` (${fmtViews(j.views_dos_publicados)} ÷ ${j.com_numero})`
  }
  t += `; mediana ${fmtViews(j.mediana)} (metade ficou acima)`
  if (j.com_numero < j.videos) t += ` · ${j.videos - j.com_numero} ainda sem número`
  return t
}

const linhas = computed(() => lista.value.map((g) => ({
  g,
  semana: contagem(g.semana),
  mes: contagem(g.mes),
  // Barra neutra: só o tamanho do mês perto dos outros. Sem verde, sem veredito.
  barra: maxMes.value > 0 && g.chave !== 'nenhum' && g.mes?.views ? Math.round((g.mes.views / maxMes.value) * 1000) / 10 : 0,
  redes: ORDEM_REDES
    .filter((r) => g.por_rede_mes?.[r] !== undefined && g.por_rede_mes?.[r] !== null)
    .map((r) => `${ROTULO_REDE[r] || r} ${fmtViews(g.por_rede_mes[r])}`)
    .join(' · '),
  dicaMedia: dicaMedia(g.mes),
  melhorNome: g.melhor ? (g.melhor.nome || g.melhor.titulo || 'vídeo sem legenda') : '',
})))

const rodape = computed(() => {
  const s = props.janelas?.semana?.desde
  const m = props.janelas?.mes?.desde
  const u = unidade.value
  return `Semana = últimos ${diasSemana.value} dias${s ? ` (desde ${ddmm(s)})` : ''}; `
    + `mês = últimos ${diasMes.value} dias${m ? ` (desde ${ddmm(m)})` : ''}. `
    + `Views = as que ${u.todos} ${u.varios} do grupo ganharam nesses dias, somando as redes `
    + `(${u.um} ${u.antigo} que continua rendendo conta) — a mesma conta do Resumo. `
    + `Embaixo, ${u.quantos} ${u.varios} foram ${u.publicados} nesses dias.`
})
</script>

<template>
  <div class="min-w-0 space-y-2">
    <div class="flex flex-wrap items-center gap-2">
      <div class="flex max-w-full flex-wrap gap-1 rounded-md bg-muted/40 p-1 w-fit">
        <button
          v-for="d in DIMENSOES" :key="d.chave"
          class="rounded px-3 py-1 text-sm transition-colors"
          :class="dim === d.chave ? 'bg-background shadow-sm font-medium' : 'hover:bg-background/60 text-muted-foreground'"
          :aria-pressed="dim === d.chave"
          @click="dim = d.chave"
        >
          {{ d.rotulo }}
        </button>
      </div>
      <div class="flex flex-wrap items-center gap-2 text-xs">
        <span class="text-muted-foreground">Ordenar por:</span>
        <div class="flex max-w-full flex-wrap gap-1 rounded-md bg-muted/40 p-1">
          <button
            v-for="o in ORDENS" :key="o.chave"
            class="rounded px-2.5 py-0.5 transition-colors"
            :class="ordem === o.chave ? 'bg-background shadow-sm font-medium' : 'hover:bg-background/60 text-muted-foreground'"
            :aria-pressed="ordem === o.chave"
            @click="ordem = o.chave"
          >
            {{ o.chave === 'por_video' ? `views por ${unidade.um}` : o.rotulo }}
          </button>
        </div>
      </div>
    </div>

    <p v-if="porPostagem" class="text-[11px] text-muted-foreground">
      Horário compara postagens, não vídeos: o mesmo vídeo pode ter saído às 12h numa rede e às 19h noutra. Cada
      postagem conta as views dela, na rede dela.
    </p>

    <p v-if="!linhas.length" class="rounded-md border p-6 text-center text-sm text-muted-foreground">
      Nenhum vídeo publicado nem view ganha nos últimos {{ diasMes }} dias.
    </p>

    <template v-else>
      <div class="overflow-hidden rounded-xl border bg-card">
        <div class="hidden gap-x-4 border-b bg-muted/40 px-3 py-2 text-[11px] text-muted-foreground lg:grid" :class="COLUNAS">
          <span>{{ rotuloDim }}</span>
          <span class="text-right">Views na semana<span class="block text-[10px] opacity-80">ganhas nos últimos {{ diasSemana }} dias</span></span>
          <span class="text-right">Views no mês<span class="block text-[10px] opacity-80">ganhas nos últimos {{ diasMes }} dias</span></span>
          <span class="text-right">Views por {{ unidade.um }}<span class="block text-[10px] opacity-80">{{ unidade.publicado }} no mês, até hoje</span></span>
          <span>{{ porPostagem ? 'Postagem' : 'Vídeo' }} com mais views<span class="block text-[10px] opacity-80">{{ unidade.publicado }} no mês</span></span>
        </div>

        <div v-for="l in linhas" :key="l.g.chave" class="space-y-1.5 border-b px-3 py-2.5 last:border-0">
          <div class="grid grid-cols-2 gap-x-4 gap-y-2" :class="COLUNAS">
            <!-- o grupo: nome e, no produto, os SKUs que ele juntou -->
            <div class="col-span-2 min-w-0 lg:col-span-1">
              <p class="truncate text-sm font-medium" :title="l.g.rotulo">{{ l.g.rotulo }}</p>
              <p
                v-if="l.g.detalhe" class="truncate text-[11px] text-muted-foreground"
                :title="`Cores, tamanhos e SKUs somados neste aparelho: ${l.g.detalhe}`"
              >
                {{ l.g.detalhe }}
              </p>
            </div>

            <!-- semana -->
            <div class="min-w-0 lg:text-right">
              <p class="text-[10px] text-muted-foreground lg:hidden">Views na semana · ganhas em {{ diasSemana }} dias</p>
              <p class="text-base font-semibold tabular-nums">{{ fmtViews(l.g.semana?.views) }}</p>
              <p class="text-[11px] text-muted-foreground">{{ l.semana }}</p>
            </div>

            <!-- mês -->
            <div class="min-w-0 lg:text-right">
              <p class="text-[10px] text-muted-foreground lg:hidden">Views no mês · ganhas em {{ diasMes }} dias</p>
              <p class="text-base font-semibold tabular-nums">{{ fmtViews(l.g.mes?.views) }}</p>
              <p class="text-[11px] text-muted-foreground">{{ l.mes }}</p>
              <div v-if="l.barra" class="mt-1 h-1 rounded-full bg-muted/50">
                <div class="h-1 rounded-full bg-foreground/30 lg:ml-auto" :style="{ width: `${l.barra}%` }" />
              </div>
            </div>

            <!-- por vídeo -->
            <div class="min-w-0 lg:text-right" :title="l.dicaMedia || undefined">
              <p class="text-[10px] text-muted-foreground lg:hidden">Views por {{ unidade.um }} {{ unidade.publicado }} no mês</p>
              <p class="text-sm font-medium tabular-nums">{{ fmtViews(l.g.mes?.media) }}</p>
              <p v-if="l.g.mes?.mediana !== null && l.g.mes?.mediana !== undefined" class="text-[11px] text-muted-foreground">
                mediana {{ fmtViews(l.g.mes.mediana) }}
              </p>
            </div>

            <!-- o campeão do mês, com o link do post mais visto dele -->
            <div class="min-w-0">
              <p class="text-[10px] text-muted-foreground lg:hidden">
                {{ porPostagem ? 'Postagem' : 'Vídeo' }} com mais views · {{ unidade.publicado }} no mês
              </p>
              <a
                v-if="l.g.melhor && l.g.melhor.post_url"
                :href="l.g.melhor.post_url" target="_blank" rel="noopener"
                class="inline-flex max-w-full items-center gap-1 text-xs hover:underline"
                :title="`${l.melhorNome} — ${fmtViews(l.g.melhor.views)} views${l.g.melhor.plataforma ? ` (abre o post no ${ROTULO_REDE[l.g.melhor.plataforma] || l.g.melhor.plataforma})` : ''}`"
              >
                <span class="truncate">{{ l.melhorNome }}</span>
                <span class="shrink-0 tabular-nums text-muted-foreground">· {{ fmtViews(l.g.melhor.views) }}</span>
                <ExternalLink class="size-3 shrink-0" />
              </a>
              <span v-else-if="l.g.melhor" class="inline-flex max-w-full items-center gap-1 text-xs" :title="l.melhorNome">
                <span class="truncate">{{ l.melhorNome }}</span>
                <span class="shrink-0 tabular-nums text-muted-foreground">· {{ fmtViews(l.g.melhor.views) }}</span>
              </span>
              <span v-else class="text-xs text-muted-foreground">—</span>
            </div>
          </div>
          <p v-if="l.redes" class="text-[11px] text-muted-foreground">ganhas no mês: {{ l.redes }}</p>
        </div>
      </div>

      <p class="text-[11px] text-muted-foreground">
        {{ rodape }} Um vídeo = o mesmo criativo postado em todas as redes. No Produto, cores e tamanhos do mesmo
        aparelho são uma linha só (a linha cinza diz quais).
      </p>
    </template>
  </div>
</template>
