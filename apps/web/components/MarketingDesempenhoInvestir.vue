<script setup lang="ts">
/**
 * "Onde vale investir" — os vídeos agrupados por produto, formato, agência,
 * roteiro e horário, cada grupo contra o normal das contas (Eduardo,
 * 24/09/2026: "trackear o que cada vídeo deu de retorno pra saber o que
 * investir").
 *
 * O grupo conta CRIATIVOS, não posts: o mesmo vídeo em três redes é uma
 * decisão de produção, não três. E todo grupo diz com quantos vídeos a
 * conclusão foi tirada ("pouco dado", "indício", "dá pra comparar") — com
 * dois vídeos, 2,0× pode ser sorte, e a barra verde sozinha faria o Eduardo
 * gravar mais do mesmo por causa de um acaso. Por isso pouco dado fica cinza.
 */
import { computed, ref } from 'vue'
import { Info } from 'lucide-vue-next'
import {
  ORDEM_REDES, ROTULO_REDE,
  barra, fmtIndice, qtd, rotuloLeitura,
  type Dimensao, type GrupoDesempenho, type Minimos,
} from '~/utils/desempenho'

const props = defineProps<{
  grupos: Record<Dimensao, GrupoDesempenho[]>
  marco: number
  minimos: Minimos
}>()

const DIMENSOES: { chave: Dimensao; rotulo: string }[] = [
  { chave: 'produto', rotulo: 'Produto' },
  { chave: 'formato', rotulo: 'Formato' },
  { chave: 'agencia', rotulo: 'Agência' },
  { chave: 'roteiro', rotulo: 'Roteiro' },
  { chave: 'horario', rotulo: 'Horário' },
]
// Emerald é "acima do normal"; abaixo NÃO é vermelho — vídeo fraco é
// informação, não erro. Cinza = pouco dado, não conclua.
const TOM_BARRA: Record<string, string> = {
  acima: 'bg-emerald-500 dark:bg-emerald-400',
  normal: 'bg-foreground/40',
  abaixo: 'bg-foreground/40',
  cinza: 'bg-muted-foreground/30',
}
const TOM_LEITURA: Record<string, string> = {
  comparavel: 'text-foreground',
  indicio: 'text-foreground',
  pouco_dado: 'text-muted-foreground',
}
const COLUNAS = 'lg:grid-cols-[minmax(0,1fr)_5.5rem_minmax(12rem,1.3fr)_10rem]'

const dim = ref<Dimensao>('produto')
const rotuloDim = computed(() => DIMENSOES.find((d) => d.chave === dim.value)?.rotulo ?? '')
const lista = computed(() => props.grupos?.[dim.value] ?? [])
// Horário compara POSTAGENS (o mesmo vídeo sai às 12h numa rede e às 19h noutra).
const porPostagem = computed(() => dim.value === 'horario')
const comIndice = computed(() => lista.value.reduce((a, g) => a + (g.n || 0), 0))

const linhas = computed(() => lista.value.map((g) => ({
  g,
  b: barra(g.indice_views, g.leitura),
  redes: ORDEM_REDES
    .filter((r) => g.por_rede?.[r])
    .map((r) => {
      const x = g.por_rede[r]
      const med = x.mediana === null || x.mediana === undefined ? '—' : x.mediana.toLocaleString('pt-BR')
      return `${ROTULO_REDE[r] || r} ${med} (${x.n})`
    })
    .join(' · '),
})))
</script>

<template>
  <div class="min-w-0 space-y-2">
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

    <p v-if="porPostagem" class="text-[11px] text-muted-foreground">
      Horário compara postagens, não criativos: o mesmo vídeo pode ter saído às 12h numa rede e às 19h noutra.
    </p>

    <p v-if="!comIndice" class="rounded-md border p-6 text-center text-sm text-muted-foreground">
      Ainda não há vídeos com {{ qtd(marco, 'dia', 'dias') }} de vida e base de comparação.
    </p>

    <template v-else>
      <div
        v-if="comIndice < 10"
        class="flex items-start gap-2 rounded-md border bg-muted/30 px-3 py-2 text-xs text-muted-foreground"
      >
        <Info class="mt-0.5 size-3.5 shrink-0" />
        <span>
          Com {{ porPostagem ? qtd(comIndice, 'postagem comparável', 'postagens comparáveis') : qtd(comIndice, 'criativo comparável', 'criativos comparáveis') }},
          a diferença ainda pode ser sorte. Use como pista, não como veredito.
        </span>
      </div>

      <div class="overflow-hidden rounded-xl border bg-card">
        <div class="hidden gap-x-4 border-b bg-muted/40 px-3 py-2 text-[11px] text-muted-foreground lg:grid" :class="COLUNAS">
          <span>{{ rotuloDim }}</span>
          <span class="text-right">vídeos</span>
          <span>
            vs. o normal da conta
            <span class="block text-[10px] opacity-80">← abaixo do normal · normal · acima do normal →</span>
          </span>
          <span>leitura</span>
        </div>
        <p class="border-b px-3 py-1.5 text-[10px] text-muted-foreground lg:hidden">
          vs. o normal da conta: ← abaixo do normal · normal · acima do normal →
        </p>

        <div v-for="l in linhas" :key="l.g.chave" class="space-y-1 border-b px-3 py-2.5 last:border-0">
          <div class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1.5" :class="COLUNAS">
            <span class="min-w-0 truncate text-sm font-medium" :title="l.g.rotulo">{{ l.g.rotulo }}</span>
            <span
              class="text-right text-xs tabular-nums text-muted-foreground"
              :title="`${l.g.n} com índice, de ${l.g.total} publicados`"
            >
              {{ l.g.n }} de {{ l.g.total }}
            </span>
            <!-- Barra divergente em log2: 2× e 0,5× têm o mesmo tamanho. -->
            <div class="col-span-2 flex items-center gap-2 lg:col-span-1">
              <div class="relative h-2.5 min-w-0 flex-1 rounded-sm bg-muted/50">
                <span class="absolute inset-y-0 left-1/2 w-px bg-foreground/30" />
                <span
                  v-if="l.b && l.b.lado !== 'centro'"
                  class="absolute inset-y-0"
                  :class="[TOM_BARRA[l.b.tom], l.b.lado === 'direita' ? 'rounded-r-sm' : 'rounded-l-sm']"
                  :style="l.b.lado === 'direita' ? { left: '50%', width: `${l.b.pct}%` } : { right: '50%', width: `${l.b.pct}%` }"
                />
              </div>
              <span
                class="w-10 shrink-0 text-right text-xs tabular-nums"
                :class="l.g.leitura === 'pouco_dado' ? 'text-muted-foreground' : 'text-foreground'"
              >
                {{ fmtIndice(l.g.indice_views) }}
              </span>
            </div>
            <span class="col-span-2 text-[11px] lg:col-span-1" :class="TOM_LEITURA[l.g.leitura] || 'text-muted-foreground'">
              {{ rotuloLeitura(l.g.leitura) }}
            </span>
          </div>
          <!-- A mediana esconde um acerto isolado: o melhor vai do lado. As
               views cruas por rede só comparam dentro da mesma rede. -->
          <p v-if="l.g.melhor || l.redes" class="text-[11px] text-muted-foreground">
            <template v-if="l.g.melhor">
              melhor: “<span class="break-words">{{ l.g.melhor.titulo || 'vídeo sem legenda' }}</span>”
              {{ fmtIndice(l.g.melhor.indice_views) }}
            </template>
            <template v-if="l.g.melhor && l.redes"> · </template>
            <span
              v-if="l.redes"
              :title="`mediana das views com ${qtd(marco, 'dia', 'dias')}, por rede (entre parênteses, quantos vídeos)`"
            >{{ l.redes }}</span>
          </p>
        </div>
      </div>

      <p class="text-[11px] text-muted-foreground">
        Leitura pelo tamanho da amostra: pouco dado (menos de {{ minimos.indicio }}), indício
        ({{ minimos.indicio }} a {{ minimos.comparavel - 1 }}), dá pra comparar ({{ minimos.comparavel }} ou mais).
      </p>
    </template>
  </div>
</template>
