<script setup lang="ts">
// Denúncia (30/09/2026): ficha do anúncio — dados que a varredura leu, as
// denúncias feitas contra ele, caso/compra de prova, provas e o histórico de
// "ainda está no ar?". Usada nas três telas (clicar num anúncio abre aqui).
import { computed, ref, watch } from 'vue'
import { ExternalLink } from 'lucide-vue-next'
import {
  type Prova, dataBr, dinheiro, numero, pillGrupo, pillResultado, pillSituacaoAnuncio,
  pillSituacaoDenuncia, pillStatusCaso, pillStatusCompra, prazoVencido,
} from '~/lib/denuncia'

type Ficha = {
  anuncio: Record<string, any>
  loja: Record<string, any> | null
  denuncias: Record<string, any>[]
  provas: Prova[]
  verificacoes: Record<string, any>[]
  casos: Record<string, any>[]
  compras: Record<string, any>[]
}

const props = defineProps<{ anuncioId: string | null }>()
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
</script>

<template>
  <DenunciaGaveta
    v-model:open="aberta"
    :titulo="a.titulo || (anuncioId ? `Anúncio ${anuncioId}` : 'Anúncio')"
    :subtitulo="[a.marketplace, a.loja || ficha?.loja?.nome, anuncioId].filter(Boolean).join(' · ')"
  >
    <template #cabecalho-extra>
      <span v-if="a.grupo" class="pill" :class="pillGrupo(a.grupo)">{{ a.grupo }}</span>
      <span v-if="a.situacao" class="pill" :class="pillSituacaoAnuncio(a.situacao)">{{ a.situacao }}</span>
      <span v-if="a.propria" class="pill pill-info">loja própria</span>
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
        <h3 class="text-sm font-semibold mb-2">Denúncias ({{ ficha.denuncias.length }})</h3>
        <div v-if="ficha.denuncias.length === 0" class="text-sm text-muted-foreground">Nenhuma denúncia contra este anúncio.</div>
        <div v-else class="table-card">
          <table class="w-full">
            <thead>
              <tr><th>Data</th><th>Canal</th><th>Protocolo</th><th>Situação</th><th>Resultado</th><th>Prazo</th></tr>
            </thead>
            <tbody>
              <tr v-for="d in ficha.denuncias" :key="d.id">
                <td class="text-xs tabular-nums whitespace-nowrap">{{ dataBr(d.data, false) }}</td>
                <td class="text-xs">{{ d.canal }}<span v-if="d.tipo && d.tipo !== 'normal'" class="text-muted-foreground"> · {{ d.tipo }}</span></td>
                <td class="font-mono text-xs">{{ d.protocolo || '—' }}</td>
                <td><span class="pill" :class="pillSituacaoDenuncia(d.situacao)">{{ d.situacao }}</span></td>
                <td><span v-if="d.resultado" class="pill" :class="pillResultado(d.resultado)">{{ d.resultado }}</span></td>
                <td class="text-xs tabular-nums whitespace-nowrap" :class="prazoVencido(d.prazo, d.situacao) ? 'text-red-600 font-medium' : ''">{{ dataBr(d.prazo, false) }}</td>
              </tr>
            </tbody>
          </table>
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

      <section>
        <h3 class="text-sm font-semibold mb-2">Provas ({{ ficha.provas.length }})</h3>
        <DenunciaProvas :provas="ficha.provas" />
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
