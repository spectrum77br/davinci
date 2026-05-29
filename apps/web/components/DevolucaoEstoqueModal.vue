<script setup lang="ts">
import { Loader2, X } from 'lucide-vue-next'

type Variant = { suffix: string; sku: string; name: string | null; exists: boolean }
type SuffixesResponse = { base: string; allowed_suffixes: string[]; variants: Variant[] }

const props = defineProps<{
  open: boolean
  // SKU efetivo que vai voltar ao estoque (Novo/Usado, ou o troca_sku/Manutenção).
  sku: string
  // Condição efetiva — só pro rótulo (Novo/Usado).
  condicao?: string
}>()

const emit = defineEmits<{
  // Um dos dois é preenchido: bin existente escolhido, OU tag pra criar produto novo.
  (e: 'confirm', payload: { destino_sku?: string; nova_tag?: string }): void
  (e: 'cancel'): void
}>()

const { api } = useApi()

const loading = ref(false)
const errorMsg = ref<string | null>(null)
const data = ref<SuffixesResponse | null>(null)
const selectedExisting = ref<string | null>(null)
const selectedTag = ref<string | null>(null)

watch(() => props.open, (open) => {
  if (open) {
    data.value = null
    selectedExisting.value = null
    selectedTag.value = null
    errorMsg.value = null
    fetchVariants()
  }
})

async function fetchVariants() {
  loading.value = true
  errorMsg.value = null
  try {
    data.value = await api<SuffixesResponse>(`/api/devolutions/sku-suffixes?sku=${encodeURIComponent(props.sku)}`)
  } catch (e: any) {
    errorMsg.value = e?.data?.detail?.message || e?.message || 'erro ao carregar estoques'
  } finally {
    loading.value = false
  }
}

// Variantes (bins) já existentes na tabela products.
const existing = computed(() => (data.value?.variants ?? []).filter((v) => v.exists))
// Quando NÃO há nenhuma variante, o operador escolhe uma tag pra criar z000N.<tag>.
const hasExisting = computed(() => existing.value.length > 0)
// Tags candidatas pra criação (exclui `sp`, a origem "a redirecionar").
const tagChoices = computed(() => (data.value?.allowed_suffixes ?? []).filter((s) => s !== 'sp'))

const canConfirm = computed(() =>
  hasExisting.value ? !!selectedExisting.value : !!selectedTag.value,
)

function confirm() {
  if (!canConfirm.value) return
  if (hasExisting.value) emit('confirm', { destino_sku: selectedExisting.value! })
  else emit('confirm', { nova_tag: selectedTag.value! })
}
</script>

<template>
  <div v-if="open" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" @click.self="emit('cancel')">
    <div class="bg-background border rounded-lg w-full max-w-xl p-5 space-y-4 max-h-[90vh] overflow-y-auto">
      <div class="flex items-start">
        <div>
          <h2 class="text-lg font-semibold">Devolver ao estoque</h2>
          <p class="text-sm text-muted-foreground">
            SKU <span class="font-mono">{{ sku }}</span>
            <template v-if="condicao"> · {{ condicao }}</template>.
            Escolha o estoque (bin) que vai receber a unidade.
          </p>
        </div>
        <Button class="ml-auto" size="sm" variant="ghost" @click="emit('cancel')">
          <X class="size-4" />
        </Button>
      </div>

      <div v-if="loading" class="py-6 text-center text-sm text-muted-foreground">
        <Loader2 class="size-4 inline animate-spin mr-1.5" /> carregando estoques…
      </div>
      <div v-else-if="errorMsg" class="py-6 text-center text-sm text-red-400">{{ errorMsg }}</div>

      <template v-else-if="data">
        <p class="text-xs text-muted-foreground">Base: <span class="font-mono">{{ data.base }}</span></p>

        <!-- Há bins existentes: o operador só pode escolher entre eles. -->
        <div v-if="hasExisting" class="grid grid-cols-2 sm:grid-cols-3 gap-2">
          <button
            v-for="v in existing"
            :key="v.sku"
            type="button"
            class="flex flex-col items-start rounded-md border px-3 py-2 text-left transition-colors"
            :class="selectedExisting === v.sku ? 'border-primary bg-primary/10' : 'hover:border-primary/50'"
            @click="selectedExisting = v.sku"
          >
            <span class="font-mono text-sm">{{ v.sku }}</span>
            <span class="mt-0.5 text-[11px] text-muted-foreground truncate w-full">{{ v.name || '—' }}</span>
          </button>
        </div>

        <!-- Nenhum bin existe: criar produto novo z000N.<tag>. -->
        <template v-else>
          <p class="text-xs text-amber-600 dark:text-amber-400">
            Nenhum estoque encontrado para esse SKU. Um produto novo será criado
            (<span class="font-mono">z000N.&lt;tag&gt;</span>) com a tag escolhida.
          </p>
          <div class="grid grid-cols-3 sm:grid-cols-4 gap-2">
            <button
              v-for="t in tagChoices"
              :key="t"
              type="button"
              class="rounded-md border px-3 py-2 text-center font-mono text-sm uppercase transition-colors"
              :class="selectedTag === t ? 'border-primary bg-primary/10' : 'hover:border-primary/50'"
              @click="selectedTag = t"
            >.{{ t }}</button>
          </div>
        </template>
      </template>

      <div class="flex justify-end gap-2 pt-1">
        <Button size="sm" variant="ghost" @click="emit('cancel')">cancelar</Button>
        <Button size="sm" :disabled="!canConfirm" @click="confirm">confirmar</Button>
      </div>
    </div>
  </div>
</template>
