<script setup lang="ts">
// Denúncia (01/10/2026): o conteúdo da ficha do anúncio, usado na gaveta do anúncio e dentro
// da ficha da loja. Vinicius: "essa tela auxiliar está meio bagunçada" → em cima, 4 quadrinhos
// (Na loja · Na Anatel · Ativo · Caso); embaixo, abas (Resumo · Na loja · Na Anatel · Provas ·
// Histórico). Cada tentativa na loja é uma linha com UMA etiqueta; resposta, texto e provas
// ficam fechados até clicar. focoDenuncia = abre a aba da denúncia clicada e rola até ela.
import { computed, nextTick, ref, watch } from 'vue'
import { ExternalLink } from 'lucide-vue-next'
import {
  type InfoAnuncio, type PainelStatus, type Prova, ativoSimNao, dataBr, dinheiro, numero, pillAtivo,
  pillResultado, pillSituacaoDenuncia, pillStatusCaso, pillStatusCompra, pillTom, prazoVencido,
} from '~/lib/denuncia'

type Ficha = {
  anuncio: Record<string, any>
  loja: Record<string, any> | null
  denuncias: Record<string, any>[]
  provas: Prova[]
  verificacoes: Record<string, any>[]
  casos: Record<string, any>[]
  compras: Record<string, any>[]
  status?: { na_loja: PainelStatus; na_anatel: PainelStatus }
  junto?: Record<string, { id: string; loja: string | null; titulo: string | null; situacao: string | null }[]>
}

const props = defineProps<{ anuncioId: string | null; focoDenuncia?: number | null }>()
const emit = defineEmits<{ (e: 'info', v: InfoAnuncio): void; (e: 'caso', id: number): void }>()

const { api } = useApi()
const ficha = ref<Ficha | null>(null)
const carregando = ref(false)
const erro = ref<string | null>(null)
type Aba = 'resumo' | 'loja' | 'anatel' | 'provas' | 'historico'
const aba = ref<Aba>('resumo')

const CANAIS_ANATEL = ['Anatel SEI', 'Anatel']

watch(
  () => props.anuncioId,
  async (id) => {
    ficha.value = null
    erro.value = null
    aba.value = 'resumo'
    if (!id) return
    carregando.value = true
    try {
      const f = await api<Ficha>(`/api/denuncia/anuncios/${encodeURIComponent(id)}`)
      ficha.value = f
      const an = f.anuncio || {}
      emit('info', {
        titulo: an.titulo || `Anúncio ${id}`,
        subtitulo: [an.marketplace, an.loja || f.loja?.nome, id].filter(Boolean).join(' · '),
        grupo: an.grupo || null,
      })
      if (props.focoDenuncia) {
        const d = f.denuncias.find((x) => x.id === props.focoDenuncia)
        if (d) {
          aba.value = CANAIS_ANATEL.includes(d.canal) ? 'anatel' : 'loja'
          await nextTick()
          document.getElementById(`den-${d.id}`)?.scrollIntoView({ block: 'start', behavior: 'smooth' })
        }
      }
    } catch (e: any) {
      erro.value = e?.data?.detail?.code || e?.message || 'erro'
    } finally {
      carregando.value = false
    }
  },
  { immediate: true },
)

const a = computed(() => ficha.value?.anuncio || {})

const CAMPOS: [string, string][] = [
  ['hom', 'Nº Anatel declarado'],
  ['inmetro', 'Inmetro declarado'],
  ['certificacao', 'Certificação nossa usada'],
  ['marca', 'Marca'],
  ['modelo', 'Modelo'],
  ['fabricante', 'Fabricante'],
  ['escopo', 'Escopo'],
  ['categoria', 'Categoria'],
]
const campos = computed(() => CAMPOS.filter(([k]) => a.value[k] !== null && a.value[k] !== undefined && a.value[k] !== ''))

// tentativas na loja, da mais nova para a mais velha, com o nº da tentativa
const naLoja = computed(() => {
  const ds = (ficha.value?.denuncias || []).filter((d) => !CANAIS_ANATEL.includes(d.canal))
  const total = ds.length
  return ds.map((d, i): Record<string, any> => ({ ...d, n: d.tentativa || total - i }))
})
const naAnatel = computed(() => (ficha.value?.denuncias || []).filter((d) => CANAIS_ANATEL.includes(d.canal)))
const idsDenuncias = computed(() => new Set((ficha.value?.denuncias || []).map((d) => d.id)))
function provasDa(id: number): Prova[] {
  return (ficha.value?.provas || []).filter((p) => p.denuncia_id === id)
}
const outrasProvas = computed(() =>
  (ficha.value?.provas || []).filter((p) => !p.denuncia_id || !idsDenuncias.value.has(p.denuncia_id)),
)
function junto(d: Record<string, any>) {
  return (ficha.value?.junto || {})[`${d.canal}|${d.protocolo}`] || []
}
// uma etiqueta só: o resultado quando há (Improcedente, Anúncio removido…), senão a situação
function etiquetaDen(d: Record<string, any>): { texto: string; cls: string } {
  if (d.resultado && d.resultado !== 'Aguardando') return { texto: d.resultado, cls: pillResultado(d.resultado) }
  return { texto: d.situacao || '—', cls: pillSituacaoDenuncia(d.situacao) }
}
// sem processo na Anatel: o que falta para ir (mesmas regras de 01/10 do robô)
const EXPLICA_ANATEL: Record<string, string> = {
  fila: 'Está na fila: vai no próximo passo 4 (Denúncias Anatel).',
  falta_print: 'Falta o print da página do anúncio — o robô tira nos passos 3 e 7; depois entra na fila.',
  esperando_recusa: 'Nosso: só vai à Anatel depois que a loja recusar a nossa denúncia.',
  falta_loja: 'Nosso: primeiro denunciamos na loja; se ela recusar, vai à Anatel.',
  nada: 'Não vai à Anatel: fora do ar, já resolvido ou fora das regras (sem nº declarado).',
}

const abas = computed(() => [
  { k: 'resumo' as Aba, t: 'Resumo' },
  { k: 'loja' as Aba, t: `Na loja (${naLoja.value.length})` },
  { k: 'anatel' as Aba, t: `Na Anatel (${naAnatel.value.length})` },
  { k: 'provas' as Aba, t: `Provas (${ficha.value?.provas.length || 0})` },
  { k: 'historico' as Aba, t: `Histórico (${ficha.value?.verificacoes.length || 0})` },
])
</script>

<template>
  <div v-if="carregando" class="text-sm text-muted-foreground">carregando…</div>
  <div v-else-if="erro" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">{{ erro }}</div>
  <div v-else-if="ficha" class="space-y-5">
    <!-- os 4 quadrinhos -->
    <div class="grid grid-cols-2 sm:grid-cols-4 gap-2">
      <div class="rounded-lg border px-3 py-2 space-y-1 min-w-0">
        <div class="text-[10px] uppercase tracking-wider text-muted-foreground">Na loja</div>
        <span v-if="ficha.status && ficha.status.na_loja.chave !== 'vazio'" :class="pillTom(ficha.status.na_loja.tom)">{{ ficha.status.na_loja.rotulo }}</span>
        <span v-else class="text-sm text-muted-foreground">—</span>
        <div v-if="naLoja.length" class="text-[11px] text-muted-foreground">
          {{ naLoja.length }} tentativa{{ naLoja.length > 1 ? 's' : '' }} · última {{ dataBr(naLoja[0].data, false) }}
        </div>
      </div>
      <div class="rounded-lg border px-3 py-2 space-y-1 min-w-0">
        <div class="text-[10px] uppercase tracking-wider text-muted-foreground">Na Anatel</div>
        <span v-if="ficha.status" :class="pillTom(ficha.status.na_anatel.tom)">{{ ficha.status.na_anatel.rotulo === '—' ? 'não vai' : ficha.status.na_anatel.rotulo }}</span>
        <div v-if="ficha.status?.na_anatel.protocolo" class="font-mono text-[11px] text-muted-foreground truncate" :title="ficha.status.na_anatel.protocolo">{{ ficha.status.na_anatel.protocolo }}</div>
      </div>
      <div class="rounded-lg border px-3 py-2 space-y-1 min-w-0">
        <div class="text-[10px] uppercase tracking-wider text-muted-foreground">Ativo</div>
        <span :class="pillAtivo(a.situacao)">{{ ativoSimNao(a.situacao) }}</span>
        <div class="text-[11px] text-muted-foreground">
          <template v-if="a.saiu_em">saiu {{ dataBr(a.saiu_em, false) }}</template>
          <template v-else>visto {{ dataBr(a.visto_ultimo, false) }}</template>
        </div>
      </div>
      <div class="rounded-lg border px-3 py-2 space-y-1 min-w-0">
        <div class="text-[10px] uppercase tracking-wider text-muted-foreground">Caso</div>
        <div v-if="ficha.casos.length" class="flex flex-wrap gap-1">
          <button
            v-for="c in ficha.casos"
            :key="c.id"
            type="button"
            class="pill-info hover:underline"
            :title="`abrir o ${c.codigo} (${c.status})`"
            @click="emit('caso', c.id)"
          >
            {{ c.codigo }}
          </button>
        </div>
        <span v-else class="text-sm text-muted-foreground">—</span>
      </div>
    </div>

    <!-- abas -->
    <div class="flex items-center gap-1 border-b border-border overflow-x-auto">
      <button
        v-for="x in abas"
        :key="x.k"
        type="button"
        class="-mb-px inline-flex h-8 items-center whitespace-nowrap border-b-2 px-2.5 text-xs font-medium transition-colors"
        :class="aba === x.k ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'"
        @click="aba = x.k"
      >
        {{ x.t }}
      </button>
    </div>

    <!-- Resumo -->
    <section v-if="aba === 'resumo'" class="space-y-4">
      <div class="flex flex-wrap items-center gap-x-3 gap-y-1">
        <a v-if="a.url" :href="a.url" target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1 text-sm text-primary hover:underline">
          abrir no marketplace <ExternalLink class="size-3.5" />
        </a>
        <span class="text-xs text-muted-foreground">
          <template v-if="a.preco">{{ dinheiro(a.preco) }}<template v-if="a.preco_em"> (lido em {{ dataBr(a.preco_em, false) }})</template> · </template>{{ numero(a.vendas) }} vendas · visto de {{ dataBr(a.visto_primeiro, false) }} a {{ dataBr(a.visto_ultimo) }}
        </span>
      </div>
      <dl class="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
        <div v-for="[k, rotulo] in campos" :key="k" class="min-w-0">
          <dt class="text-[10px] uppercase tracking-wider text-muted-foreground">{{ rotulo }}</dt>
          <dd class="truncate" :title="String(a[k])">{{ a[k] }}</dd>
        </div>
      </dl>
      <div v-if="a.a_conferir" class="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-950/30 dark:text-amber-300">
        A conferir: {{ a.a_conferir_motivo || 'título não carregou na varredura' }}
      </div>
      <p v-if="a.obs" class="text-xs text-muted-foreground whitespace-pre-wrap">{{ a.obs }}</p>
      <div v-if="ficha.casos.length || ficha.compras.length" class="space-y-2">
        <div class="text-[10px] uppercase tracking-wider text-muted-foreground">Caso e compra de prova</div>
        <div v-for="c in ficha.casos" :key="`c${c.id}`" class="rounded-lg border px-3 py-2 text-sm flex flex-wrap items-center gap-2">
          <button type="button" class="font-medium text-primary hover:underline" @click="emit('caso', c.id)">{{ c.codigo }}</button>
          <span :class="pillStatusCaso(c.status)">{{ c.status }}</span>
          <span class="text-xs text-muted-foreground">aberto {{ dataBr(c.aberto_em, false) }}</span>
          <span v-if="c.juridico_enviado_em" class="text-xs text-muted-foreground">· enviado ao jurídico {{ dataBr(c.juridico_enviado_em, false) }}</span>
        </div>
        <div v-for="c in ficha.compras" :key="`k${c.id}`" class="rounded-lg border px-3 py-2 text-sm flex flex-wrap items-center gap-2">
          <span class="font-medium">Compra {{ c.pedido || `#${c.id}` }}</span>
          <span :class="pillStatusCompra(c.status)">{{ c.status }}</span>
          <span class="text-xs text-muted-foreground">{{ dataBr(c.data, false) }} · {{ dinheiro(c.valor_pago) }}</span>
        </div>
      </div>
    </section>

    <!-- Na loja -->
    <section v-else-if="aba === 'loja'" class="space-y-2">
      <div v-if="naLoja.length === 0" class="text-sm text-muted-foreground">
        Não denunciado na loja.<template v-if="a.grupo === 'GRUPO 2'"> Desde 01/10 o Diversos não é mais denunciado nas lojas — vai direto à Anatel.</template>
      </div>
      <div
        v-for="d in naLoja"
        :id="`den-${d.id}`"
        :key="d.id"
        class="rounded-lg border px-3 py-2 space-y-1.5 scroll-mt-4"
        :class="focoDenuncia === d.id ? 'ring-2 ring-primary' : ''"
      >
        <div class="flex items-center gap-2 text-sm">
          <span class="font-medium whitespace-nowrap">{{ d.tipo && d.tipo !== 'normal' ? d.tipo : `${d.n}ª tentativa` }}</span>
          <span class="text-xs text-muted-foreground whitespace-nowrap tabular-nums">{{ d.canal }} · {{ dataBr(d.data, false) }} {{ d.hora || '' }}</span>
          <span class="flex-1" />
          <span :class="etiquetaDen(d).cls">{{ etiquetaDen(d).texto }}</span>
        </div>
        <div v-if="d.motivo || d.prazo || d.protocolo" class="text-xs text-muted-foreground">
          <span v-if="d.motivo">{{ d.motivo }}</span>
          <span v-if="d.prazo" :class="prazoVencido(d.prazo, d.situacao) ? 'text-red-600 font-medium' : ''"> · prazo {{ dataBr(d.prazo, false) }}</span>
          <span v-if="d.protocolo"> · protocolo <span class="font-mono">{{ d.protocolo }}</span></span>
        </div>
        <div class="flex flex-wrap gap-x-4 gap-y-1 text-xs">
          <details v-if="d.resposta || d.resultado_nota" class="w-full">
            <summary class="cursor-pointer text-muted-foreground">resposta da loja</summary>
            <p class="mt-1 whitespace-pre-wrap rounded-md border bg-muted/30 px-3 py-2">{{ d.resposta || d.resultado_nota }}</p>
          </details>
          <details v-if="d.texto" class="w-full">
            <summary class="cursor-pointer text-muted-foreground">texto enviado</summary>
            <p class="mt-1 whitespace-pre-wrap rounded-md border bg-muted/30 px-3 py-2 max-h-72 overflow-y-auto">{{ d.texto }}</p>
          </details>
          <details v-if="provasDa(d.id).length" class="w-full">
            <summary class="cursor-pointer text-muted-foreground">provas ({{ provasDa(d.id).length }})</summary>
            <div class="mt-1"><DenunciaProvas :provas="provasDa(d.id)" /></div>
          </details>
        </div>
      </div>
    </section>

    <!-- Na Anatel -->
    <section v-else-if="aba === 'anatel'" class="space-y-2">
      <div v-if="naAnatel.length === 0 && ficha.status" class="rounded-lg border px-3 py-2.5 text-sm flex flex-wrap items-center gap-2">
        <span :class="pillTom(ficha.status.na_anatel.tom)">{{ ficha.status.na_anatel.rotulo === '—' ? 'não vai' : ficha.status.na_anatel.rotulo }}</span>
        <span class="text-xs text-muted-foreground">{{ EXPLICA_ANATEL[ficha.status.na_anatel.chave] }}</span>
      </div>
      <div
        v-for="d in naAnatel"
        :id="`den-${d.id}`"
        :key="d.id"
        class="rounded-lg border px-3 py-2 space-y-1.5 scroll-mt-4"
        :class="focoDenuncia === d.id ? 'ring-2 ring-primary' : ''"
      >
        <div class="flex items-center gap-2 text-sm">
          <span class="font-medium whitespace-nowrap">{{ d.canal === 'Anatel SEI' ? 'SEI' : 'Anatel Consumidor' }}</span>
          <span v-if="d.protocolo" class="font-mono text-xs truncate">{{ d.protocolo }}</span>
          <span class="text-xs text-muted-foreground whitespace-nowrap tabular-nums">{{ dataBr(d.data, false) }} {{ d.hora || '' }}</span>
          <span class="flex-1" />
          <span :class="etiquetaDen(d).cls">{{ etiquetaDen(d).texto }}</span>
        </div>
        <div v-if="d.status_anatel" class="text-xs">
          <span class="text-muted-foreground">andamento:</span> {{ d.status_anatel }}
          <span class="text-muted-foreground">{{ dataBr(d.status_anatel_em) }}</span>
        </div>
        <p v-if="d.canal === 'Anatel'" class="text-xs text-muted-foreground">Anatel Consumidor (antigo): desde 25/09 só acompanhamos o andamento; a denúncia nova sai pelo SEI.</p>
        <div v-if="junto(d).length" class="text-xs">
          <span class="text-muted-foreground">A petição incluiu também {{ junto(d).length }} anúncio{{ junto(d).length > 1 ? 's' : '' }} da loja:</span>
          <ul class="mt-1 space-y-0.5">
            <li v-for="x in junto(d)" :key="x.id" class="flex items-center gap-2 min-w-0">
              <span class="font-mono shrink-0">{{ x.id }}</span>
              <span class="truncate text-muted-foreground">{{ x.titulo }}</span>
              <span class="shrink-0" :class="pillAtivo(x.situacao)">ativo: {{ ativoSimNao(x.situacao) }}</span>
            </li>
          </ul>
        </div>
        <div class="flex flex-wrap gap-x-4 gap-y-1 text-xs">
          <details v-if="d.resposta || d.resposta_anatel" class="w-full">
            <summary class="cursor-pointer text-muted-foreground">resposta da Anatel</summary>
            <p class="mt-1 whitespace-pre-wrap rounded-md border bg-muted/30 px-3 py-2">{{ d.resposta || d.resposta_anatel }}</p>
          </details>
          <details v-if="d.texto" class="w-full">
            <summary class="cursor-pointer text-muted-foreground">texto da denúncia / petição</summary>
            <p class="mt-1 whitespace-pre-wrap rounded-md border bg-muted/30 px-3 py-2 max-h-72 overflow-y-auto">{{ d.texto }}</p>
          </details>
          <details v-if="provasDa(d.id).length" class="w-full">
            <summary class="cursor-pointer text-muted-foreground">provas ({{ provasDa(d.id).length }})</summary>
            <div class="mt-1"><DenunciaProvas :provas="provasDa(d.id)" /></div>
          </details>
        </div>
        <p v-if="d.obs" class="text-xs text-muted-foreground whitespace-pre-wrap">{{ d.obs }}</p>
      </div>
    </section>

    <!-- Provas -->
    <section v-else-if="aba === 'provas'" class="space-y-2">
      <div v-if="!ficha.provas.length" class="text-sm text-muted-foreground">Nenhuma prova guardada.</div>
      <DenunciaProvas v-else :provas="ficha.provas" />
      <p v-if="outrasProvas.length && outrasProvas.length < ficha.provas.length" class="text-[11px] text-muted-foreground">
        {{ outrasProvas.length }} não são de nenhuma denúncia (capturas da varredura, caso, compra).
      </p>
    </section>

    <!-- Histórico: ainda está no ar? -->
    <section v-else class="space-y-1">
      <div v-if="!ficha.verificacoes.length" class="text-sm text-muted-foreground">Nenhuma conferência ainda.</div>
      <ul class="text-xs space-y-1">
        <li v-for="v in ficha.verificacoes.slice(0, 30)" :key="v.id" class="flex items-center gap-2 tabular-nums">
          <span class="text-muted-foreground w-28 shrink-0">{{ dataBr(v.ts) }}</span>
          <span :class="pillAtivo(v.situacao)">ativo: {{ ativoSimNao(v.situacao) }}</span>
          <span v-if="v.vendas !== null && v.vendas !== undefined" class="text-muted-foreground">{{ numero(v.vendas) }} vendas</span>
          <span class="text-muted-foreground truncate">{{ v.fonte }}</span>
        </li>
      </ul>
    </section>
  </div>
</template>
