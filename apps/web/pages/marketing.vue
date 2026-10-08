<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'
import { BarChart3, Clapperboard, ClipboardCheck, NotebookPen, TrendingUp } from 'lucide-vue-next'

// Abas: Conferência | Criativos | Roteiros | Desempenho.
//
// 08/10/2026: as abas Mercado Livre e Shopee (os painéis de Ads: tabelas de
// 7/30 dias, gráfico Evolução, agenda automática, Oferta Relâmpago, alertas de
// crédito, executor local e "rodar ciclo agora") saíram a pedido do dono. Só o
// heatmap de horários ficou, dentro da Conferência, embaixo do relatório do
// mesmo marketplace (components/MarketingHorarios.vue).
//
// Permissões: "marketing" (view) = Conferência; "marketing_criativos" = abas
// Criativos, Roteiros e Desempenho. A aba Roteiros pendura no MESMO recurso de
// Criativos (migration 0299): recurso novo nasceria False pra todo mundo menos
// admin, e não existe migration de backfill de permissão neste repositório.
// Pelo mesmo motivo a Conferência pendura em "marketing".
const canCriativos = useCan('marketing_criativos', 'view')
const canConferencia = useCan('marketing', 'view')
const route = useRoute()
const router = useRouter()

type Aba = 'conferencia' | 'criativos' | 'roteiros' | 'desempenho'
// ?aba= abre a aba direto (o aviso do Threema manda pra ?aba=conferencia&execucao=<id>).
// Aba que a pessoa não pode ver cai no padrão. Os links antigos ?aba=ml e
// ?aba=shopee caem na Conferência (o middleware abaixo já trocou pra
// ?aba=conferencia, com ?conf=ml no Mercado Livre).
function abaDaUrl(): Aba | null {
  const q = String(route.query.aba || '')
  if ((q === 'conferencia' || q === 'ml' || q === 'shopee') && canConferencia.value) return 'conferencia'
  if ((q === 'criativos' || q === 'roteiros' || q === 'desempenho') && canCriativos.value) return q
  return null
}
// Quem vê o Marketing abre na Conferência; quem só tem Criativos, em Criativos.
function abaPadrao(): Aba {
  return canConferencia.value ? 'conferencia' : 'criativos'
}
const aba = ref<Aba>(abaDaUrl() ?? abaPadrao())

const focoRoteiro = ref<string | null>(null)
const roteirosEl = ref<{ abrir: (id: string) => void } | null>(null)

function abrirRoteiro(id: string) {
  focoRoteiro.value = id
  aba.value = 'roteiros'
  // Se a aba já estiver montada, o componente não remonta: fala com ele.
  void nextTick(() => roteirosEl.value?.abrir(id))
}

onMounted(async () => {
  if (!canCriativos.value && !canConferencia.value) await navigateTo('/403')
})

// A aba vai pra URL (?aba=), pra dar F5 e mandar link. O id da execução, o
// marketplace da Conferência (?conf=ml) e a conta dos horários (?horario=) só
// valem lá dentro: saem junto quando a aba muda.
watch(aba, (a) => {
  const query: Record<string, any> = { ...route.query, aba: a }
  if (a !== 'conferencia') {
    delete query.execucao
    delete query.conf
    delete query.horario
  }
  void router.replace({ query })
})

definePageMeta({
  // Links antigos das abas removidas: ?aba=ml → Conferência do Mercado Livre;
  // ?aba=shopee → Conferência da Shopee. Troca a URL antes da tela montar,
  // porque a Conferência lê o ?conf= uma vez só, quando nasce.
  middleware: [
    (to) => {
      const velha = to.query.aba
      if (velha !== 'ml' && velha !== 'shopee') return
      const query: Record<string, any> = { ...to.query, aba: 'conferencia' }
      if (velha === 'ml') query.conf = 'ml'
      else delete query.conf
      return navigateTo({ path: to.path, query, hash: to.hash }, { replace: true })
    },
  ],
})
</script>

<template>
  <div class="space-y-5">
    <div class="flex items-center gap-2">
      <BarChart3 class="h-6 w-6 text-primary" />
      <h1 class="text-2xl font-semibold">Marketing</h1>
    </div>

    <!-- Abas: cada uma aparece conforme a permissão (marketing / marketing_criativos). -->
    <div v-if="canConferencia || canCriativos" class="flex max-w-full">
      <div class="flex w-fit max-w-full gap-1 overflow-x-auto rounded-md bg-muted/40 p-1">
        <!-- Conferência: o relatório semanal Mala · Celular · Eletro de terça e
             quinta, um por marketplace (Shopee | Mercado Livre | Amazon lá dentro),
             com os horários dos anúncios da Shopee e do Mercado Livre embaixo. -->
        <button v-if="canConferencia"
          class="px-3 py-1.5 rounded text-sm whitespace-nowrap transition-colors inline-flex items-center gap-1.5"
          :class="aba === 'conferencia' ? 'bg-background shadow-sm font-medium' : 'hover:bg-background/60 text-muted-foreground'"
          @click="aba = 'conferencia'">
          <ClipboardCheck class="size-3.5" />
          Conferência
        </button>
        <button v-if="canCriativos"
          class="px-3 py-1.5 rounded text-sm whitespace-nowrap transition-colors inline-flex items-center gap-1.5"
          :class="aba === 'criativos' ? 'bg-background shadow-sm font-medium' : 'hover:bg-background/60 text-muted-foreground'"
          @click="aba = 'criativos'">
          <Clapperboard class="size-3.5" />
          Criativos
        </button>
        <button v-if="canCriativos"
          class="px-3 py-1.5 rounded text-sm whitespace-nowrap transition-colors inline-flex items-center gap-1.5"
          :class="aba === 'roteiros' ? 'bg-background shadow-sm font-medium' : 'hover:bg-background/60 text-muted-foreground'"
          @click="aba = 'roteiros'">
          <NotebookPen class="size-3.5" />
          Roteiros
        </button>
        <!-- Desempenho: o que os vídeos JÁ PUBLICADOS renderam. Pendura na
             mesma permissão de Criativos — quem vê o que foi publicado vê o
             que rendeu. -->
        <button v-if="canCriativos"
          class="px-3 py-1.5 rounded text-sm whitespace-nowrap transition-colors inline-flex items-center gap-1.5"
          :class="aba === 'desempenho' ? 'bg-background shadow-sm font-medium' : 'hover:bg-background/60 text-muted-foreground'"
          @click="aba = 'desempenho'">
          <TrendingUp class="size-3.5" />
          Desempenho
        </button>
      </div>
    </div>

    <MarketingConferencia v-if="aba === 'conferencia' && canConferencia" />
    <MarketingCriativos
      v-else-if="aba === 'criativos' && canCriativos"
      @abrir-roteiro="abrirRoteiro"
    />
    <MarketingRoteiros
      v-else-if="aba === 'roteiros' && canCriativos"
      ref="roteirosEl"
      :foco="focoRoteiro"
    />
    <MarketingDesempenho v-else-if="aba === 'desempenho' && canCriativos" />
  </div>
</template>
