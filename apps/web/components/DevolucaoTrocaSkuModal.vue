<script setup lang="ts">
import { Loader2, Search, X } from 'lucide-vue-next'

type ProductHit = { sku: string; name: string; cost_price: number | null; saldo_virtual_total: number | null }

const props = defineProps<{
  open: boolean
  // SKU vendido no pedido (Bling) — exibido como contexto e usado pra
  // pré-preencher a busca quando o modal abre.
  soldSku?: string | null
}>()

const emit = defineEmits<{
  (e: 'confirm', payload: { sku: string; condicao: 'Novo' | 'Usado' }): void
  (e: 'cancel'): void
}>()

const { api } = useApi()

const q = ref('')
const loading = ref(false)
const results = ref<ProductHit[]>([])
const errorMsg = ref<string | null>(null)
const selectedSku = ref<string | null>(null)
const condicao = ref<'Novo' | 'Usado' | ''>('')

let searchTimer: ReturnType<typeof setTimeout> | null = null

watch(() => props.open, (open) => {
  if (open) {
    q.value = (props.soldSku || '').trim()
    results.value = []
    errorMsg.value = null
    selectedSku.value = null
    condicao.value = ''
    if (q.value) runSearch()
  }
})

watch(q, () => {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(runSearch, 300)
})

async function runSearch() {
  const term = q.value.trim()
  if (!term) { results.value = []; return }
  loading.value = true
  errorMsg.value = null
  try {
    results.value = await api<ProductHit[]>(`/api/devolutions/product-search?q=${encodeURIComponent(term)}`)
    if (!results.value.length) errorMsg.value = 'nenhum produto encontrado'
  } catch (e: any) {
    errorMsg.value = e?.data?.detail?.message || e?.message || 'erro na busca'
  } finally {
    loading.value = false
  }
}

const canConfirm = computed(() => !!selectedSku.value && (condicao.value === 'Novo' || condicao.value === 'Usado'))

function confirm() {
  if (!canConfirm.value) return
  emit('confirm', { sku: selectedSku.value!, condicao: condicao.value as 'Novo' | 'Usado' })
}

function saldo(v: number | null) {
  return v == null ? '—' : `${v} un.`
}
</script>

<template>
  <div v-if="open" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" @click.self="emit('cancel')">
    <div class="bg-background border rounded-lg w-full max-w-2xl p-5 space-y-4 max-h-[90vh] overflow-y-auto">
      <div class="flex items-start">
        <div>
          <h2 class="text-lg font-semibold">Pedido trocado — SKU que voltou ao estoque</h2>
          <p class="text-sm text-muted-foreground">
            O pedido foi enviado errado. Selecione na tabela de produtos o SKU que de fato
            voltou ao estoque<template v-if="soldSku"> (vendido no Bling: <span class="font-mono">{{ soldSku }}</span>)</template>.
          </p>
        </div>
        <Button class="ml-auto" size="sm" variant="ghost" @click="emit('cancel')">
          <X class="size-4" />
        </Button>
      </div>

      <div class="relative">
        <Search class="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
        <input
          v-model="q"
          autofocus
          class="h-9 w-full rounded-md border bg-background pl-8 pr-3 text-sm"
          placeholder="buscar por SKU ou nome do produto"
          @keydown.enter.prevent="runSearch"
        />
      </div>

      <div class="rounded-md border max-h-[40vh] overflow-auto">
        <div v-if="loading" class="py-6 text-center text-sm text-muted-foreground">
          <Loader2 class="size-4 inline animate-spin mr-1.5" /> buscando…
        </div>
        <div v-else-if="errorMsg" class="py-6 text-center text-sm text-muted-foreground">{{ errorMsg }}</div>
        <table v-else-if="results.length" class="w-full text-xs border-collapse">
          <thead class="bg-background sticky top-0">
            <tr>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground w-8"></th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground min-w-[130px]">SKU</th>
              <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground min-w-[220px]">Produto</th>
              <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground min-w-[100px]">Saldo virtual</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="r in results"
              :key="r.sku"
              class="border-t cursor-pointer"
              :class="selectedSku === r.sku ? 'bg-primary/10' : 'hover:brightness-95 dark:hover:brightness-110'"
              @click="selectedSku = r.sku"
            >
              <td class="px-2 py-1 text-center">
                <input type="radio" :checked="selectedSku === r.sku" class="size-3.5 accent-primary" @change="selectedSku = r.sku" />
              </td>
              <td class="px-2 py-1 font-mono">{{ r.sku }}</td>
              <td class="px-2 py-1 text-muted-foreground">{{ r.name }}</td>
              <td class="px-2 py-1 text-right tabular-nums" title="saldo virtual">{{ saldo(r.saldo_virtual_total) }}</td>
            </tr>
          </tbody>
        </table>
        <div v-else class="py-6 text-center text-sm text-muted-foreground">digite para buscar um produto</div>
      </div>

      <div class="flex items-center gap-4">
        <span class="text-sm font-medium">Volta como:</span>
        <label class="flex items-center gap-1.5 text-sm cursor-pointer">
          <input v-model="condicao" type="radio" value="Novo" class="size-4 accent-primary" /> Novo
        </label>
        <label class="flex items-center gap-1.5 text-sm cursor-pointer">
          <input v-model="condicao" type="radio" value="Usado" class="size-4 accent-primary" /> Usado
        </label>
      </div>

      <div class="flex justify-end gap-2 pt-1">
        <Button size="sm" variant="ghost" @click="emit('cancel')">cancelar</Button>
        <Button size="sm" :disabled="!canConfirm" @click="confirm">confirmar</Button>
      </div>
    </div>
  </div>
</template>
