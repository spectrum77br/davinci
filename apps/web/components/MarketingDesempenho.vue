<script setup lang="ts">
/**
 * Desempenho dos vídeos publicados (Eduardo, 23/09/2026).
 *
 * Ele pediu: ver as views e interações POR MARCA, com o total somado e o
 * detalhe de cada rede — e por vídeo publicado pelo automático do DaVinci, não
 * o número geral do canal.
 *
 * Duas contas convivem aqui, e a tela precisa deixá-las distintas:
 *   ACUMULADO   quanto os vídeos têm hoje;
 *   NO PERÍODO  quanto ganharam na janela escolhida — é o que responde
 *               "esta semana rendeu mais que a passada".
 *
 * Três decisões de tela que nasceram de limitações reais, não de estética:
 *
 * 1. O total somado vem com ressalva VISÍVEL, não em rodapé. "View" não conta
 *    a mesma coisa nas três redes (o limiar de segundos difere), então o total
 *    serve pra sentir tendência e a comparação honesta é dentro da mesma rede.
 *
 * 2. Métrica que ninguém reportou aparece como "—", nunca como 0. O YouTube
 *    não mede salvamento; o Instagram só dá views com uma permissão que o
 *    token ainda não tem. Zerar afirmaria algo que não sabemos.
 *
 * 3. Cada rede mostra QUANDO foi lida. Coleta falha baixo — a tela mostraria
 *    número velho parecendo novo, e ninguém notaria. Data velha fica em âmbar.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { AlertTriangle, RefreshCw } from 'lucide-vue-next'

type Numeros = Partial<Record<
  'views' | 'curtidas' | 'comentarios' | 'compartilhamentos' | 'salvamentos' | 'alcance', number
>>
type Plataforma = {
  plataforma: string
  acumulado: Numeros
  no_periodo: Numeros
  posts: number
  coletado_em: string | null
  erro: string | null
}
type LinhaMarca = {
  marca: string
  acumulado: Numeros
  no_periodo: Numeros
  plataformas: Plataforma[]
  posts: number
}

const PLATAFORMA_LABEL: Record<string, string> = {
  instagram: 'Instagram', facebook: 'Facebook', youtube: 'YouTube', tiktok: 'TikTok',
}
const COLUNAS = [
  { chave: 'views', rotulo: 'views' },
  { chave: 'curtidas', rotulo: 'curtidas' },
  { chave: 'comentarios', rotulo: 'comentários' },
  { chave: 'compartilhamentos', rotulo: 'compart.' },
  { chave: 'salvamentos', rotulo: 'salvos' },
] as const

const { api } = useApi()

const dias = ref(30)
const marcas = ref<LinhaMarca[]>([])
const carregando = ref(false)
const erro = ref<string | null>(null)
const abertas = ref<Set<string>>(new Set())

async function carregar() {
  carregando.value = true
  erro.value = null
  try {
    const r = await api<{ marcas: LinhaMarca[] }>(`/api/marketing/metricas?dias=${dias.value}`)
    marcas.value = r.marcas ?? []
  } catch (e: any) {
    // Endpoint fora do ar (servidor antigo) não pode quebrar a aba inteira.
    erro.value = e?.data?.detail?.code || e?.message || 'não consegui carregar'
    marcas.value = []
  } finally {
    carregando.value = false
  }
}
onMounted(carregar)
watch(dias, carregar)

// ---------- helpers puros (travados em tests/marketing-desempenho-sfc.cjs)

/** Número da tela: ausente vira "—", nunca 0 (ver decisão 2 no topo). */
function num(n: Numeros, chave: string): string {
  const v = (n as any)[chave]
  return v === undefined || v === null ? '—' : v.toLocaleString('pt-BR')
}
function ganho(n: Numeros, chave: string): string {
  const v = (n as any)[chave]
  if (v === undefined || v === null || v === 0) return ''
  return v > 0 ? `+${v.toLocaleString('pt-BR')}` : v.toLocaleString('pt-BR')
}

/** Quantas horas faz que esta rede foi lida. Passou de 2 dias, fica em âmbar. */
function coleta(p: Plataforma): { texto: string; velha: boolean } {
  if (!p.coletado_em) return { texto: 'nunca coletado', velha: true }
  const d = new Date(p.coletado_em)
  const horas = (Date.now() - d.getTime()) / 36e5
  const texto = horas < 36
    ? 'coletado hoje'
    : `coletado ${d.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' })}`
  return { texto, velha: horas > 48 }
}

// ---------- fim helpers puros

function alternar(marca: string) {
  const s = new Set(abertas.value)
  s.has(marca) ? s.delete(marca) : s.add(marca)
  abertas.value = s
}

const totalPosts = computed(() => marcas.value.reduce((a, m) => a + m.posts, 0))
</script>

<template>
  <div class="space-y-3">
    <div class="flex flex-wrap items-center gap-3">
      <div class="flex gap-1 rounded-md bg-muted/40 p-1 w-fit">
        <button
          v-for="d in [7, 30, 90]" :key="d"
          class="px-3 py-1 rounded text-sm transition-colors"
          :class="dias === d ? 'bg-background shadow-sm font-medium' : 'hover:bg-background/60 text-muted-foreground'"
          @click="dias = d"
        >
          {{ d }} dias
        </button>
      </div>
      <button
        class="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
        :disabled="carregando" @click="carregar"
      >
        <RefreshCw class="size-3.5" :class="carregando && 'animate-spin'" /> recarregar
      </button>
      <span v-if="totalPosts" class="text-xs text-muted-foreground">
        {{ totalPosts }} vídeo(s) publicado(s) pelo DaVinci
      </span>
    </div>

    <p v-if="erro" class="text-sm text-amber-600 dark:text-amber-400">
      não consegui carregar o desempenho ({{ erro }})
    </p>

    <!-- A ressalva fica NA TELA, não em rodapé: somar views de redes
         diferentes dá um número, mas não um número comparável. -->
    <p class="text-[11px] text-muted-foreground">
      o total soma as três redes para dar a tendência; “view” não conta a mesma coisa em cada uma,
      então a comparação honesta é dentro da mesma plataforma — por isso o detalhe fica ao lado.
    </p>

    <div v-if="!carregando && !marcas.length && !erro" class="rounded-md border p-6 text-center text-sm text-muted-foreground">
      ainda não há números. A coleta roda uma vez por dia, de madrugada, e só enxerga vídeos
      publicados pelo DaVinci — o primeiro retrato aparece no dia seguinte à publicação.
    </div>

    <div v-for="m in marcas" :key="m.marca" class="rounded-md border overflow-hidden">
      <button
        class="w-full flex flex-wrap items-center gap-x-4 gap-y-1 px-3 py-2 text-left hover:bg-muted/40 transition-colors"
        @click="alternar(m.marca)"
      >
        <span class="font-medium">{{ m.marca }}</span>
        <span class="text-xs text-muted-foreground">{{ m.posts }} vídeo(s)</span>
        <span class="ml-auto flex items-center gap-4 text-sm tabular-nums">
          <span v-for="c in COLUNAS" :key="c.chave" class="text-right">
            <span class="block text-[10px] uppercase tracking-wide text-muted-foreground">{{ c.rotulo }}</span>
            {{ num(m.acumulado, c.chave) }}
            <span v-if="ganho(m.no_periodo, c.chave)" class="text-[10px] text-emerald-600 dark:text-emerald-400">
              {{ ganho(m.no_periodo, c.chave) }}
            </span>
          </span>
        </span>
      </button>

      <div v-if="abertas.has(m.marca)" class="border-t bg-muted/20 px-3 py-2 space-y-1">
        <div
          v-for="p in m.plataformas" :key="p.plataforma"
          class="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm"
        >
          <span class="w-24 shrink-0 text-muted-foreground">
            {{ PLATAFORMA_LABEL[p.plataforma] || p.plataforma }}
          </span>
          <span class="text-xs text-muted-foreground">{{ p.posts }} vídeo(s)</span>
          <span
            class="text-[11px]"
            :class="coleta(p).velha ? 'text-amber-600 dark:text-amber-400' : 'text-muted-foreground'"
          >
            {{ coleta(p).texto }}
          </span>
          <span v-if="p.erro" :title="p.erro" class="inline-flex items-center gap-1 text-[11px] text-amber-600 dark:text-amber-400">
            <AlertTriangle class="size-3" /> a última leitura falhou
          </span>
          <span class="ml-auto flex items-center gap-4 tabular-nums">
            <span v-for="c in COLUNAS" :key="c.chave" class="w-16 text-right">
              {{ num(p.acumulado, c.chave) }}
              <span v-if="ganho(p.no_periodo, c.chave)" class="text-[10px] text-emerald-600 dark:text-emerald-400">
                {{ ganho(p.no_periodo, c.chave) }}
              </span>
            </span>
          </span>
        </div>
      </div>
    </div>
  </div>
</template>
