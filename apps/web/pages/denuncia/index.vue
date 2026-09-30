<script setup lang="ts">
// ── Ouvidoria › Denúncia (30/09/2026) ───────────────────────────────────────
// A fiscalização da marca (Uranyx e homologações Fossibot/Oukitel/Hotwav) que
// o robô do Mac mini da Makisa faz nos marketplaces. Vinicius: "igual nesse
// Chamados" — um item só no menu, abas no topo: Anúncios · Denúncias · Casos ·
// Jurídico. As três primeiras são a cópia (só leitura) que o mini manda a cada
// 5 min; o Jurídico é a lista de chamados encaminhados ao jurídico, que saiu da
// tela de Chamados e mora aqui (precisa também da permissão de Chamados).
import { computed, ref, watch } from 'vue'
import { ScanSearch, Flag, Briefcase, Scale, RefreshCw } from 'lucide-vue-next'

definePageMeta({ middleware: ['permission'], permission: { resource: 'denuncia', action: 'view' } })

type Aba = 'anuncios' | 'denuncias' | 'casos' | 'juridico'

const route = useRoute()
const router = useRouter()
const podeJuridico = useCan('chamados', 'view')

const ABAS = computed(() => [
  { key: 'anuncios' as Aba, label: 'Anúncios', icon: ScanSearch },
  { key: 'denuncias' as Aba, label: 'Denúncias', icon: Flag },
  { key: 'casos' as Aba, label: 'Casos', icon: Briefcase },
  ...(podeJuridico.value ? [{ key: 'juridico' as Aba, label: 'Jurídico', icon: Scale }] : []),
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

    <DenunciaAnuncios v-if="aba === 'anuncios'" ref="atual" />
    <DenunciaDenuncias v-else-if="aba === 'denuncias'" ref="atual" />
    <DenunciaCasos v-else-if="aba === 'casos'" ref="atual" />
    <ChamadosPainel v-else-if="aba === 'juridico'" modo="juridico" />
  </div>
</template>
