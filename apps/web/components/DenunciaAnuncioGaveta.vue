<script setup lang="ts">
// Denúncia (30/09/2026): ficha do anúncio — dados que a varredura leu, as
// denúncias feitas contra ele, caso/compra de prova, provas e o histórico de
// "ainda está no ar?". Usada nas telas (clicar num anúncio abre aqui).
// 01/10 (Vinicius: "hoje apareceria tudo junto, certo?"): a ficha da denúncia virou parte
// desta — "Na loja" (cada denúncia no marketplace: texto, resposta, provas) e "Na Anatel"
// (o processo do SEI, a petição, os outros anúncios da mesma petição) ou, sem processo,
// onde o anúncio está no caminho. focoDenuncia = rola até a denúncia clicada.
import { computed, nextTick, ref, watch } from 'vue'
import { ExternalLink } from 'lucide-vue-next'
import {
  type Prova, dataBr, dinheiro, nomeGrupo, numero, pillGrupo, pillResultado, pillSituacaoAnuncio,
  pillSituacaoDenuncia, pillStatusCaso, pillStatusCompra, prazoVencido,
} from '~/lib/denuncia'

type Status = { chave: string; rotulo: string; tom: string; protocolo?: string | null; consumidor?: string | null }

type Ficha = {
  anuncio: Record<string, any>
  loja: Record<string, any> | null
  denuncias: Record<string, any>[]
  provas: Prova[]
  verificacoes: Record<string, any>[]
  casos: Record<string, any>[]
  compras: Record<string, any>[]
  status?: { na_loja: Status; na_anatel: Status }
  junto?: Record<string, { id: string; loja: string | null; titulo: string | null; situacao: string | null }[]>
}

const props = defineProps<{ anuncioId: string | null; focoDenuncia?: number | null }>()
const emit = defineEmits<{ (e: 'fechar'): void }>()

const { api } = useApi()
const ficha = ref<Ficha | null>(null)
const carregando = ref(false)
const erro = ref<string | null>(null)

const aberta = computed({
  get: () => !!props.anuncioId,
  set: (v: boolean) => {
    if (!v) emit('fechar')
  },
})

watch(
  () => props.anuncioId,
  async (id) => {
    ficha.value = null
    erro.value = null
    if (!id) return
    carregando.value = true
    try {
      ficha.value = await api<Ficha>(`/api/denuncia/anuncios/${encodeURIComponent(id)}`)
      if (props.focoDenuncia) {
        await nextTick()
        document.getElementById(`den-${props.focoDenuncia}`)?.scrollIntoView({ block: 'start', behavior: 'smooth' })
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

const CANAIS_ANATEL = ['Anatel SEI', 'Anatel']
const naLoja = computed(() => (ficha.value?.denuncias || []).filter((d) => !CANAIS_ANATEL.includes(d.canal)))
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
const TOM: Record<string, string> = {
  success: 'pill-success', danger: 'pill-danger', warning: 'pill-warning', info: 'pill-info', muted: 'pill-muted',
}
// sem processo na Anatel: o que falta para ir (mesmas regras de 01/10 do robô)
const EXPLICA_ANATEL: Record<string, string> = {
  fila: 'Está na fila: vai no próximo passo 7 (Anatel / SEI).',
  falta_print: 'Falta o print da página do anúncio — o robô tira no passo 6; depois entra na fila.',
  esperando_recusa: 'Nosso: só vai à Anatel depois que a loja recusar a nossa denúncia.',
  falta_loja: 'Nosso: primeiro denunciamos na loja; se ela recusar, vai à Anatel.',
  nada: 'Não vai à Anatel: fora do ar, já resolvido ou fora das regras (sem nº declarado).',
}
</script>

<template>
  <DenunciaGaveta
    v-model:open="aberta"
    :titulo="a.titulo || (anuncioId ? `Anúncio ${anuncioId}` : 'Anúncio')"
    :subtitulo="[a.marketplace, a.loja || ficha?.loja?.nome, anuncioId].filter(Boolean).join(' · ')"
  >
    <template #cabecalho-extra>
      <span v-if="a.grupo" class="pill" :class="pillGrupo(a.grupo)" :title="a.grupo">certificado: {{ nomeGrupo(a.grupo).toLowerCase() }}</span>
      <span v-if="a.situacao" class="pill" :class="pillSituacaoAnuncio(a.situacao)">{{ a.situacao }}</span>
      <span v-if="a.propria" class="pill pill-info">loja própria</span>
      <span v-if="ficha?.status" :class="TOM[ficha.status.na_loja.tom]" title="na loja">loja: {{ ficha.status.na_loja.rotulo }}</span>
      <span v-if="ficha?.status && ficha.status.na_anatel.chave !== 'nada'" :class="TOM[ficha.status.na_anatel.tom]" title="na Anatel">Anatel: {{ ficha.status.na_anatel.rotulo }}</span>
    </template>

    <div v-if="carregando" class="text-sm text-muted-foreground">carregando…</div>
    <div v-else-if="erro" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">{{ erro }}</div>
    <template v-else-if="ficha">
      <section class="space-y-3">
        <div class="flex flex-wrap items-center gap-2">
          <a v-if="a.url" :href="a.url" target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1 text-sm text-primary hover:underline">
            abrir no marketplace <ExternalLink class="size-3.5" />
          </a>
          <span class="text-xs text-muted-foreground">
            {{ numero(a.vendas) }} vendas · visto de {{ dataBr(a.visto_primeiro, false) }} a {{ dataBr(a.visto_ultimo) }}
            <template v-if="a.saiu_em"> · saiu do ar {{ dataBr(a.saiu_em) }}</template>
          </span>
        </div>
        <dl class="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
          <div v-for="[k, rotulo] in campos" :key="k" class="min-w-0">
            <dt class="text-[11px] uppercase tracking-wider text-muted-foreground">{{ rotulo }}</dt>
            <dd class="truncate" :title="String(a[k])">{{ a[k] }}</dd>
          </div>
        </dl>
        <div v-if="a.a_conferir" class="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-950/30 dark:text-amber-300">
          A conferir: {{ a.a_conferir_motivo || 'título não carregou na varredura' }}
        </div>
        <p v-if="a.obs" class="text-xs text-muted-foreground whitespace-pre-wrap">{{ a.obs }}</p>
      </section>

      <section>
        <h3 class="text-sm font-semibold mb-2">Na loja ({{ naLoja.length }})</h3>
        <div v-if="naLoja.length === 0" class="text-sm text-muted-foreground">
          Não denunciado na loja.<template v-if="a.grupo === 'GRUPO 2'"> Desde 01/10 o Diversos não é mais denunciado nas lojas — vai direto à Anatel.</template>
        </div>
        <div class="space-y-2">
          <div
            v-for="d in naLoja"
            :id="`den-${d.id}`"
            :key="d.id"
            class="rounded-lg border px-3 py-2.5 space-y-2 scroll-mt-4"
            :class="focoDenuncia === d.id ? 'ring-2 ring-primary' : ''"
          >
            <div class="flex flex-wrap items-center gap-2 text-sm">
              <span class="font-medium">{{ d.canal }}</span>
              <span class="text-xs text-muted-foreground tabular-nums">{{ dataBr(d.data, false) }} {{ d.hora || '' }}</span>
              <span v-if="(d.tentativa || 1) > 1 || (d.tipo && d.tipo !== 'normal')" class="text-xs text-muted-foreground">
                · {{ d.tipo && d.tipo !== 'normal' ? d.tipo : `${d.tentativa}ª tentativa` }}
              </span>
              <span class="flex-1" />
              <span :class="pillSituacaoDenuncia(d.situacao)">{{ d.situacao }}</span>
              <span v-if="d.resultado" :class="pillResultado(d.resultado)">{{ d.resultado }}</span>
            </div>
            <div v-if="d.protocolo || d.motivo || d.prazo" class="text-xs text-muted-foreground flex flex-wrap gap-x-3">
              <span v-if="d.protocolo">protocolo <span class="font-mono">{{ d.protocolo }}</span></span>
              <span v-if="d.motivo">motivo: {{ d.motivo }}</span>
              <span v-if="d.prazo" :class="prazoVencido(d.prazo, d.situacao) ? 'text-red-600 font-medium' : ''">prazo {{ dataBr(d.prazo, false) }}</span>
            </div>
            <p v-if="d.resposta" class="text-sm whitespace-pre-wrap rounded-md border bg-muted/30 px-3 py-2"><span class="text-[11px] uppercase tracking-wider text-muted-foreground block mb-0.5">Resposta da loja</span>{{ d.resposta }}</p>
            <p v-if="d.resultado_nota" class="text-xs text-muted-foreground whitespace-pre-wrap">{{ d.resultado_nota }}</p>
            <details v-if="d.texto" class="text-xs">
              <summary class="cursor-pointer text-muted-foreground">texto enviado</summary>
              <p class="mt-1 whitespace-pre-wrap rounded-md border bg-muted/30 px-3 py-2 max-h-72 overflow-y-auto">{{ d.texto }}</p>
            </details>
            <DenunciaProvas v-if="provasDa(d.id).length" :provas="provasDa(d.id)" />
          </div>
        </div>
      </section>

      <section>
        <h3 class="text-sm font-semibold mb-2">Na Anatel</h3>
        <div v-if="naAnatel.length === 0 && ficha.status" class="rounded-lg border px-3 py-2.5 text-sm flex flex-wrap items-center gap-2">
          <span :class="TOM[ficha.status.na_anatel.tom]">{{ ficha.status.na_anatel.rotulo === '—' ? 'sem processo' : ficha.status.na_anatel.rotulo }}</span>
          <span class="text-xs text-muted-foreground">{{ EXPLICA_ANATEL[ficha.status.na_anatel.chave] }}</span>
        </div>
        <div class="space-y-2">
          <div
            v-for="d in naAnatel"
            :id="`den-${d.id}`"
            :key="d.id"
            class="rounded-lg border px-3 py-2.5 space-y-2 scroll-mt-4"
            :class="focoDenuncia === d.id ? 'ring-2 ring-primary' : ''"
          >
            <div class="flex flex-wrap items-center gap-2 text-sm">
              <span class="font-medium">{{ d.canal === 'Anatel SEI' ? 'SEI' : 'Anatel Consumidor' }}</span>
              <span v-if="d.protocolo" class="font-mono text-xs">{{ d.protocolo }}</span>
              <span class="text-xs text-muted-foreground tabular-nums">{{ dataBr(d.data, false) }} {{ d.hora || '' }}</span>
              <span class="flex-1" />
              <span :class="pillSituacaoDenuncia(d.situacao)">{{ d.situacao }}</span>
              <span v-if="d.resultado" :class="pillResultado(d.resultado)">{{ d.resultado }}</span>
            </div>
            <div v-if="d.status_anatel" class="text-xs">
              <span class="text-muted-foreground">andamento na Anatel:</span> {{ d.status_anatel }}
              <span class="text-muted-foreground">{{ dataBr(d.status_anatel_em) }}</span>
            </div>
            <p v-if="d.canal === 'Anatel'" class="text-xs text-muted-foreground">Anatel Consumidor (antigo): desde 25/09 só acompanhamos o andamento; a denúncia nova sai pelo SEI.</p>
            <p v-if="d.resposta || d.resposta_anatel" class="text-sm whitespace-pre-wrap rounded-md border bg-muted/30 px-3 py-2"><span class="text-[11px] uppercase tracking-wider text-muted-foreground block mb-0.5">Resposta da Anatel</span>{{ d.resposta || d.resposta_anatel }}</p>
            <div v-if="junto(d).length" class="text-xs">
              <span class="text-muted-foreground">Esta petição incluiu também {{ junto(d).length }} anúncio{{ junto(d).length > 1 ? 's' : '' }} da loja:</span>
              <ul class="mt-1 space-y-0.5">
                <li v-for="x in junto(d)" :key="x.id" class="flex items-center gap-2">
                  <span class="font-mono">{{ x.id }}</span>
                  <span class="truncate text-muted-foreground">{{ x.titulo }}</span>
                  <span :class="pillSituacaoAnuncio(x.situacao)">{{ x.situacao }}</span>
                </li>
              </ul>
            </div>
            <p v-if="d.obs" class="text-xs text-muted-foreground whitespace-pre-wrap">{{ d.obs }}</p>
            <details v-if="d.texto" class="text-xs">
              <summary class="cursor-pointer text-muted-foreground">texto da denúncia / petição</summary>
              <p class="mt-1 whitespace-pre-wrap rounded-md border bg-muted/30 px-3 py-2 max-h-72 overflow-y-auto">{{ d.texto }}</p>
            </details>
            <DenunciaProvas v-if="provasDa(d.id).length" :provas="provasDa(d.id)" />
          </div>
        </div>
      </section>

      <section v-if="ficha.casos.length || ficha.compras.length">
        <h3 class="text-sm font-semibold mb-2">Caso e compra de prova</h3>
        <div class="space-y-2">
          <div v-for="c in ficha.casos" :key="`c${c.id}`" class="rounded-lg border px-3 py-2 text-sm flex flex-wrap items-center gap-2">
            <span class="font-medium">{{ c.codigo }}</span>
            <span class="pill" :class="pillStatusCaso(c.status)">{{ c.status }}</span>
            <span class="text-xs text-muted-foreground">aberto {{ dataBr(c.aberto_em, false) }}</span>
            <span v-if="c.juridico_enviado_em" class="text-xs text-muted-foreground">· enviado ao jurídico {{ dataBr(c.juridico_enviado_em, false) }}</span>
          </div>
          <div v-for="c in ficha.compras" :key="`k${c.id}`" class="rounded-lg border px-3 py-2 text-sm flex flex-wrap items-center gap-2">
            <span class="font-medium">Compra {{ c.pedido || `#${c.id}` }}</span>
            <span class="pill" :class="pillStatusCompra(c.status)">{{ c.status }}</span>
            <span class="text-xs text-muted-foreground">{{ dataBr(c.data, false) }} · {{ dinheiro(c.valor_pago) }}</span>
          </div>
        </div>
      </section>

      <section v-if="outrasProvas.length">
        <h3 class="text-sm font-semibold mb-2">Outras provas ({{ outrasProvas.length }})</h3>
        <DenunciaProvas :provas="outrasProvas" />
      </section>

      <section v-if="ficha.verificacoes.length">
        <h3 class="text-sm font-semibold mb-2">Ainda está no ar? (últimas checagens)</h3>
        <ul class="text-xs space-y-1">
          <li v-for="v in ficha.verificacoes.slice(0, 15)" :key="v.id" class="flex gap-2 tabular-nums">
            <span class="text-muted-foreground w-28 shrink-0">{{ dataBr(v.ts) }}</span>
            <span class="pill" :class="pillSituacaoAnuncio(v.situacao)">{{ v.situacao }}</span>
            <span v-if="v.vendas !== null && v.vendas !== undefined" class="text-muted-foreground">{{ numero(v.vendas) }} vendas</span>
            <span class="text-muted-foreground truncate">{{ v.fonte }}</span>
          </li>
        </ul>
      </section>
    </template>
  </DenunciaGaveta>
</template>
