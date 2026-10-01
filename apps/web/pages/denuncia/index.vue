<script setup lang="ts">
// ── Ouvidoria › Denúncia (30/09/2026) ───────────────────────────────────────
// A fiscalização da marca (Uranyx e homologações Fossibot/Oukitel/Hotwav) que
// o robô do Mac mini da Makisa faz nos marketplaces. Vinicius: "igual nesse
// Chamados" — um item só no menu, abas no topo: Anúncios · Denúncias · Casos ·
// Jurídico. As três primeiras são a cópia (só leitura) que o mini manda a cada
// 5 min; o Jurídico é a lista de chamados encaminhados ao jurídico, que saiu da
// tela de Chamados e mora aqui (precisa também da permissão de Chamados).
// Robô (01/10): em que passo a rodada do robô do mini está e o que precisa de alguém.
// 01/10 (Vinicius: "juntar aba anúncios e denúncias… são quase as mesmas informações"):
// Anúncios + Denúncias viraram uma aba só, "Anúncios e denúncias" (DenunciaPainel). Link
// antigo ?aba=denuncias cai nela.
import { computed, ref, watch } from 'vue'
import { ScanSearch, Briefcase, Scale, RefreshCw, Bot } from 'lucide-vue-next'

definePageMeta({ middleware: ['permission'], permission: { resource: 'denuncia', action: 'view' } })

type Aba = 'anuncios' | 'casos' | 'juridico' | 'robo'

const route = useRoute()
const router = useRouter()
const podeJuridico = useCan('chamados', 'view')

const ABAS = computed(() => [
  { key: 'anuncios' as Aba, label: 'Anúncios e denúncias', icon: ScanSearch },
  { key: 'casos' as Aba, label: 'Casos', icon: Briefcase },
  ...(podeJuridico.value ? [{ key: 'juridico' as Aba, label: 'Jurídico', icon: Scale }] : []),
  { key: 'robo' as Aba, label: 'Robô', icon: Bot },
])

function abaDaUrl(): Aba {
  const q = String(route.query.aba || '')
  return (ABAS.value.some((a) => a.key === q) ? q : 'anuncios') as Aba
}
const aba = ref<Aba>(abaDaUrl())
watch(aba, (a) => {
  // ?aba= na URL: dá pra mandar o link já na aba certa
  void router.replace({ query: { ...route.query, aba: a === 'anuncios' ? undefined : a } })
})

const copia = ref<{ carregar: () => Promise<void> } | null>(null)
const atual = ref<{ carregar: () => Promise<void> } | null>(null)
function recarregar() {
  void copia.value?.carregar()
  void atual.value?.carregar()
}
</script>

<template>
  <div class="space-y-5">
    <PageHeader
      title="Denúncia"
      description="Fiscalização da marca: anúncios que usam a nossa homologação ou a marca Uranyx, as denúncias feitas, os casos e o jurídico."
    >
      <template #actions>
        <DenunciaCopia v-if="aba !== 'juridico'" ref="copia" />
        <Button v-if="aba !== 'juridico'" size="sm" variant="outline" @click="recarregar">
          <RefreshCw class="size-4 mr-1.5" /> recarregar
        </Button>
      </template>
    </PageHeader>

    <div class="flex gap-1 border-b">
      <button
        v-for="a in ABAS"
        :key="a.key"
        type="button"
        class="px-3 py-2 text-sm border-b-2 -mb-px inline-flex items-center gap-1.5"
        :class="aba === a.key ? 'border-primary font-medium' : 'border-transparent text-muted-foreground hover:text-foreground'"
        @click="aba = a.key"
      >
        <component :is="a.icon" class="size-4" /> {{ a.label }}
      </button>
    </div>

    <DenunciaPainel v-if="aba === 'anuncios'" ref="atual" />
    <DenunciaCasos v-else-if="aba === 'casos'" ref="atual" />
    <DenunciaRobo v-else-if="aba === 'robo'" ref="atual" />
    <ChamadosPainel v-else-if="aba === 'juridico'" modo="juridico" />
  </div>
</template>
