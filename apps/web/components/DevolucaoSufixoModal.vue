<script setup lang="ts">
import { Loader2, X } from 'lucide-vue-next'

type Variant = { suffix: string; sku: string; name: string | null; exists: boolean }
type SuffixesResponse = { base: string; allowed_suffixes: string[]; variants: Variant[] }

const props = defineProps<{
  open: boolean
  // SKU efetivo que vai voltar ao estoque, terminado em `.sp`.
  sku: string
}>()

const emit = defineEmits<{
  (e: 'confirm', payload: { suffix: string }): void
  (e: 'cancel'): void
}>()

const { api } = useApi()

const loading = ref(false)
const errorMsg = ref<string | null>(null)
const data = ref<SuffixesResponse | null>(null)
const selectedSuffix = ref<string | null>(null)

watch(() => props.open, (open) => {
  if (open) {
    data.value = null
    selectedSuffix.value = null
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
    errorMsg.value = e?.data?.detail?.message || e?.message || 'erro ao carregar sufixos'
  } finally {
    loading.value = false
  }
}

// Sufixos disponíveis para escolher (exclui o `.sp` de origem).
const choices = computed(() => (data.value?.variants ?? []).filter((v) => v.suffix !== 'sp'))

function confirm() {
  if (!selectedSuffix.value) return
  emit('confirm', { suffix: selectedSuffix.value })
}
</script>

<template>
  <div v-if="open" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" @click.self="emit('cancel')">
    <div class="bg-background border rounded-lg w-full max-w-xl p-5 space-y-4 max-h-[90vh] overflow-y-auto">
      <div class="flex items-start">
        <div>
          <h2 class="text-lg font-semibold">SKU <span class="font-mono">.sp</span> — trocar sufixo</h2>
          <p class="text-sm text-muted-foreground">
            O SKU <span class="font-mono">{{ sku }}</span> termina em <span class="font-mono">.sp</span>.
            Escolha o sufixo do bin onde a unidade vai entrar. Se a variante não existir, o produto
            será criado com esse sufixo.
          </p>
        </div>
        <Button class="ml-auto" size="sm" variant="ghost" @click="emit('cancel')">
          <X class="size-4" />
        </Button>
      </div>

      <div v-if="loading" class="py-6 text-center text-sm text-muted-foreground">
        <Loader2 class="size-4 inline animate-spin mr-1.5" /> carregando variantes…
      </div>
      <div v-else-if="errorMsg" class="py-6 text-center text-sm text-red-400">{{ errorMsg }}</div>
      <template v-else-if="data">
        <p class="text-xs text-muted-foreground">Base: <span class="font-mono">{{ data.base }}</span></p>
        <div class="grid grid-cols-2 sm:grid-cols-3 gap-2">
          <button
            v-for="v in choices"
            :key="v.suffix"
            type="button"
            class="flex flex-col items-start rounded-md border px-3 py-2 text-left transition-colors"
            :class="selectedSuffix === v.suffix ? 'border-primary bg-primary/10' : 'hover:border-primary/50'"
            @click="selectedSuffix = v.suffix"
          >
            <span class="font-mono text-sm">{{ v.sku }}</span>
            <span
              class="mt-0.5 text-[11px]"
              :class="v.exists ? 'text-emerald-600 dark:text-emerald-400' : 'text-amber-600 dark:text-amber-400'"
            >{{ v.exists ? 'existe' : 'será criado' }}</span>
          </button>
        </div>
      </template>

      <div class="flex justify-end gap-2 pt-1">
        <Button size="sm" variant="ghost" @click="emit('cancel')">cancelar</Button>
        <Button size="sm" :disabled="!selectedSuffix" @click="confirm">confirmar</Button>
      </div>
    </div>
  </div>
</template>
