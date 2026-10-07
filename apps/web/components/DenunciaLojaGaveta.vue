<script setup lang="ts">
// Denúncia (01/10/2026): ficha da loja. Vinicius: "tem que clicar na loja depois no anúncio para
// abrir — como fazer para ficar mais fácil". Clicar na loja abre aqui direto: o resumo (anúncios,
// vendas, na loja, na Anatel, caso), os anúncios dela com o status de cada um e os processos do
// SEI. Clicar num anúncio mostra a ficha dele aqui mesmo, com "voltar à loja".
import { computed, ref, watch } from 'vue'
import { ArrowLeft, ExternalLink, Image as ImageIcon } from 'lucide-vue-next'
import {
  type InfoAnuncio, type PainelAnuncio, type PainelLoja, ETIQ_ANATEL, ETIQ_LOJA, ativoSimNao, dataBr,
  etiquetas, nomeGrupo, numero, pillAtivo, pillGrupo, pillTom,
} from '~/lib/denuncia'

const props = defineProps<{ loja: PainelLoja | null; propria: string }>()
const emit = defineEmits<{ (e: 'fechar'): void; (e: 'caso', id: number): void }>()

const { api } = useApi()
const anuncios = ref<PainelAnuncio[]>([])
const carregando = ref(false)
const erro = ref<string | null>(null)
const anuncioAberto = ref<string | null>(null)
const info = ref<InfoAnuncio | null>(null)
// 07/10/2026: "prints" do processo do SEI = o que foi anexado (DenunciaPrintsAnatel); só nº de processo do SEI
const printsAberto = ref<string | null>(null)
const RE_PROCESSO_SEI = /^\d{5}\.\d{6}\/\d{4}-\d{2}$/

const aberta = computed({
  get: () => !!props.loja,
  set: (v: boolean) => {
    if (!v) emit('fechar')
  },
})

watch(
  () => props.loja,
  async (l) => {
    anuncios.value = []
    anuncioAberto.value = null
    erro.value = null
    if (!l) return
    carregando.value = true
    try {
      const qs = new URLSearchParams({ visao: 'anuncios', loja: l.chave, propria: props.propria, ordem: 'vendas' })
      if (l.marketplace) qs.set('marketplace', l.marketplace)
      const r = await api<{ itens: PainelAnuncio[] }>(`/api/denuncia/painel?${qs}`)
      anuncios.value = r.itens
    } catch (e: any) {
      erro.value = e?.data?.detail?.code || e?.message || 'erro'
    } finally {
      carregando.value = false
    }
  },
  { immediate: true },
)

// processos do SEI desta loja: um por número, com a data e quantos anúncios
const processos = computed(() => {
  const m = new Map<string, { protocolo: string; data: string | null; n: number }>()
  for (const a of anuncios.value) {
    const p = a.anatel_st.protocolo
    if (!p) continue
    const x = m.get(p) || { protocolo: p, data: a.anatel_st.data || null, n: 0 }
    x.n += 1
    m.set(p, x)
  }
  return [...m.values()].sort((x, y) => (y.data || '').localeCompare(x.data || ''))
})

const titulo = computed(() =>
  anuncioAberto.value ? info.value?.titulo || `Anúncio ${anuncioAberto.value}` : props.loja?.loja || props.loja?.shop_id || 'Loja',
)
const subtitulo = computed(() =>
  anuncioAberto.value
    ? info.value?.subtitulo
    : [props.loja?.marketplace, props.loja?.shop_id].filter(Boolean).join(' · '),
)
</script>

<template>
  <DenunciaGaveta v-model:open="aberta" :titulo="titulo" :subtitulo="subtitulo">
    <template v-if="!anuncioAberto && loja" #cabecalho-extra>
      <span v-if="loja.nosso" class="pill-danger">Nosso {{ loja.nosso }}</span>
      <span v-if="loja.diversos" class="pill-warning">Diversos {{ loja.diversos }}</span>
    </template>

    <!-- um anúncio da loja -->
    <template v-if="anuncioAberto">
      <button type="button" class="inline-flex items-center gap-1 text-sm text-primary hover:underline" @click="anuncioAberto = null; info = null">
        <ArrowLeft class="size-4" /> voltar à loja {{ loja?.loja || '' }}
      </button>
      <DenunciaAnuncioFicha :anuncio-id="anuncioAberto" @info="(v) => (info = v)" @caso="(id) => emit('caso', id)" />
    </template>

    <!-- a loja -->
    <template v-else-if="loja">
      <div class="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <div class="rounded-lg border px-3 py-2 space-y-0.5">
          <div class="text-[10px] uppercase tracking-wider text-muted-foreground">Anúncios</div>
          <div class="text-lg font-semibold tabular-nums">{{ numero(loja.anuncios) }}</div>
          <div class="text-[11px] text-muted-foreground">{{ numero(loja.no_ar) }} ativos · {{ numero(loja.vendas) }} vendas</div>
        </div>
        <div class="rounded-lg border px-3 py-2 space-y-1 min-w-0">
          <div class="text-[10px] uppercase tracking-wider text-muted-foreground">Na loja</div>
          <div class="flex flex-wrap gap-1">
            <span v-for="e in etiquetas(loja.na_loja, ETIQ_LOJA)" :key="e.k" :class="e.cls">{{ e.texto }}</span>
          </div>
        </div>
        <div class="rounded-lg border px-3 py-2 space-y-1 min-w-0">
          <div class="text-[10px] uppercase tracking-wider text-muted-foreground">Na Anatel</div>
          <div class="flex flex-wrap gap-1">
            <span v-for="e in etiquetas(loja.na_anatel, ETIQ_ANATEL)" :key="e.k" :class="e.cls">{{ e.texto }}</span>
            <span v-if="!etiquetas(loja.na_anatel, ETIQ_ANATEL).length" class="text-sm text-muted-foreground">—</span>
          </div>
        </div>
        <div class="rounded-lg border px-3 py-2 space-y-1 min-w-0">
          <div class="text-[10px] uppercase tracking-wider text-muted-foreground">Caso</div>
          <div v-if="loja.casos.length" class="flex flex-wrap gap-1">
            <button v-for="c in loja.casos" :key="c.id" type="button" class="pill-info hover:underline" :title="`abrir o ${c.codigo} (${c.status})`" @click="emit('caso', c.id)">{{ c.codigo }}</button>
          </div>
          <span v-else class="text-sm text-muted-foreground">—</span>
        </div>
      </div>

      <section>
        <h3 class="text-sm font-semibold mb-2">Anúncios ({{ anuncios.length }})</h3>
        <div v-if="carregando" class="text-sm text-muted-foreground">carregando…</div>
        <div v-else-if="erro" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">{{ erro }}</div>
        <ul v-else class="space-y-1.5">
          <li v-for="a in anuncios" :key="a.id">
            <button
              type="button"
              class="w-full text-left rounded-lg border px-3 py-2 hover:border-primary/50 hover:bg-muted/30 transition-colors"
              @click="anuncioAberto = a.id"
            >
              <div class="flex items-center gap-2 min-w-0">
                <span class="truncate text-sm flex-1" :title="a.titulo || ''">{{ a.titulo || '—' }}</span>
                <a v-if="a.url" :href="a.url" target="_blank" rel="noopener noreferrer" class="shrink-0 text-muted-foreground hover:text-primary" title="abrir no marketplace" @click.stop>
                  <ExternalLink class="size-3.5" />
                </a>
              </div>
              <div class="mt-1 flex flex-wrap items-center gap-1.5">
                <span class="font-mono text-[11px] text-muted-foreground mr-1">{{ a.id }} · {{ a.hom || 'sem nº' }} · {{ numero(a.vendas) }} vendas</span>
                <span v-if="a.grupo" :class="pillGrupo(a.grupo)">{{ nomeGrupo(a.grupo) }}</span>
                <span v-if="a.loja_st.chave !== 'vazio'" :class="pillTom(a.loja_st.tom)" title="na loja">loja: {{ a.loja_st.rotulo }}</span>
                <span :class="pillTom(a.anatel_st.tom)" title="na Anatel">Anatel: {{ a.anatel_st.rotulo === '—' ? 'não vai' : a.anatel_st.rotulo }}</span>
                <span :class="pillAtivo(a.situacao)">ativo: {{ ativoSimNao(a.situacao) }}</span>
              </div>
            </button>
          </li>
        </ul>
      </section>

      <section v-if="processos.length">
        <h3 class="text-sm font-semibold mb-2">Processos na Anatel ({{ processos.length }})</h3>
        <ul class="space-y-1 text-sm">
          <li v-for="p in processos" :key="p.protocolo" class="rounded-lg border">
            <div class="flex items-center gap-3 px-3 py-2">
              <span class="font-mono text-xs">{{ p.protocolo }}</span>
              <span class="text-xs text-muted-foreground tabular-nums">{{ dataBr(p.data, false) }}</span>
              <span class="flex-1" />
              <span class="text-xs text-muted-foreground">{{ p.n }} anúncio{{ p.n > 1 ? 's' : '' }}</span>
              <button
                v-if="RE_PROCESSO_SEI.test(p.protocolo)"
                type="button"
                class="inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] text-primary hover:border-primary"
                :class="printsAberto === p.protocolo ? 'border-primary bg-primary/5' : ''"
                title="ver os prints que foram anexados neste processo"
                @click="printsAberto = printsAberto === p.protocolo ? null : p.protocolo"
              >
                <ImageIcon class="size-3" /> prints
              </button>
            </div>
            <div v-if="printsAberto === p.protocolo" class="border-t p-2">
              <DenunciaPrintsAnatel :protocolo="p.protocolo" />
            </div>
          </li>
        </ul>
      </section>
    </template>
  </DenunciaGaveta>
</template>
