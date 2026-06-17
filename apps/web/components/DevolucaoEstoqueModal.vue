<script setup lang="ts">
import { Loader2, Search, X } from 'lucide-vue-next'

type Variant = { suffix: string; sku: string; name: string | null; exists: boolean }
type SuffixesResponse = { base: string; allowed_suffixes: string[]; variants: Variant[] }
type ProductHit = { sku: string; name: string; cost_price: number | null; saldo_virtual_total: number | null }

const props = defineProps<{
  open: boolean
  // SKU efetivo que vai voltar ao estoque (Novo/Usado, ou o troca_sku/Manutenção).
  sku: string
  // Condição efetiva — só pro rótulo (Novo/Usado).
  condicao?: string
}>()

const emit = defineEmits<{
  // Um dos três é preenchido: bin existente escolhido, tag pra criar produto
  // novo (z000N.<tag>), OU `suffix` pra manter o SKU base.<sufixo> (ex.: .us).
  (e: 'confirm', payload: { destino_sku?: string; nova_tag?: string; suffix?: string }): void
  (e: 'cancel'): void
}>()

const { api } = useApi()

// Sentinel: criar z000N sem sufixo (alinhado ao backend NOVA_TAG_SEM).
const SEM_TAG = '-'

const loading = ref(false)
const errorMsg = ref<string | null>(null)
const data = ref<SuffixesResponse | null>(null)
const selectedExisting = ref<string | null>(null)
const selectedTag = ref<string | null>(null)
const selectedSuffix = ref<string | null>(null)

// Busca por nome/SKU: achar um produto ATIVO já existente (ex.: outro avulso
// "Poco c65" = z0001) e somar a unidade nele em vez de criar um novo z.
const q = ref('')
const searching = ref(false)
const searchResults = ref<ProductHit[]>([])
const searchErr = ref<string | null>(null)
let searchTimer: ReturnType<typeof setTimeout> | null = null

watch(() => props.open, (open) => {
  if (open) {
    data.value = null
    selectedExisting.value = null
    selectedTag.value = null
    selectedSuffix.value = null
    errorMsg.value = null
    q.value = ''
    searchResults.value = []
    searchErr.value = null
    fetchVariants()
  }
})

watch(q, () => {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(runSearch, 300)
})

async function runSearch() {
  const term = q.value.trim()
  if (!term) { searchResults.value = []; searchErr.value = null; return }
  searching.value = true
  searchErr.value = null
  try {
    searchResults.value = await api<ProductHit[]>(`/api/devolutions/product-search?q=${encodeURIComponent(term)}`)
    if (!searchResults.value.length) searchErr.value = 'nenhum produto ativo encontrado'
  } catch (e: any) {
    searchErr.value = e?.data?.detail?.message || e?.message || 'erro na busca'
  } finally {
    searching.value = false
  }
}

function saldo(v: number | null) {
  return v == null ? '—' : `${v} un.`
}

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
// Usado sempre vira produto "z" (z000N.<tag>): só pode ir pra mala ou eletro;
// os sufixos regionais não se aplicam a usado.
const isUsado = computed(() => (props.condicao ?? '').trim().toLowerCase() === 'usado')

// Tags candidatas pra criação do z000N.<tag>: sufixos regionais (exclui `sp`,
// a origem "a redirecionar") + mala/eletro (usados que viram avulso).
// Para Usado, restringe a mala/eletro.
const tagChoices = computed(() => {
  if (isUsado.value) return ['mala', 'eletro']
  return [
    ...(data.value?.allowed_suffixes ?? []).filter((s) => s !== 'sp'),
    'mala',
    'eletro',
  ]
})

// SKU usado a manter: base.us (ex.: dg020.us). Só pra Usado.
const usadoSku = computed(() => (data.value ? `${data.value.base}.us` : ''))

// Bin existente, "criar novo z" e "manter .us" são mutuamente exclusivos.
const canConfirm = computed(
  () => !!selectedExisting.value || !!selectedTag.value || !!selectedSuffix.value,
)

function pickExisting(sku: string) {
  selectedExisting.value = sku
  selectedTag.value = null
  selectedSuffix.value = null
}
function pickTag(tag: string) {
  selectedTag.value = tag
  selectedExisting.value = null
  selectedSuffix.value = null
}
function pickSuffix(suffix: string) {
  selectedSuffix.value = suffix
  selectedExisting.value = null
  selectedTag.value = null
}

function confirm() {
  if (!canConfirm.value) return
  if (selectedExisting.value) emit('confirm', { destino_sku: selectedExisting.value })
  else if (selectedTag.value) emit('confirm', { nova_tag: selectedTag.value })
  else emit('confirm', { suffix: selectedSuffix.value! })
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

        <!-- Buscar produto ativo já existente por nome/SKU e somar a unidade nele
             (ex.: já existe um "Poco c65" = z0001 → volta no z0001, não cria z novo). -->
        <div class="space-y-1.5">
          <p class="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
            Voltar num produto existente
          </p>
          <p class="text-xs text-muted-foreground">
            Se já existe um produto com esse nome (ex.: outro avulso), busque e some a unidade nele.
          </p>
          <div class="relative">
            <Search class="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
            <input
              v-model="q"
              class="h-9 w-full rounded-md border bg-background pl-8 pr-3 text-sm"
              placeholder="buscar por nome ou SKU do produto"
              @keydown.enter.prevent="runSearch"
            />
          </div>
          <div v-if="searching" class="py-3 text-center text-sm text-muted-foreground">
            <Loader2 class="size-4 inline animate-spin mr-1.5" /> buscando…
          </div>
          <div v-else-if="searchErr" class="py-2 text-center text-xs text-muted-foreground">{{ searchErr }}</div>
          <div v-else-if="searchResults.length" class="rounded-md border max-h-[28vh] overflow-auto divide-y">
            <button
              v-for="r in searchResults"
              :key="r.sku"
              type="button"
              class="flex w-full items-center gap-3 px-3 py-2 text-left transition-colors"
              :class="selectedExisting === r.sku ? 'bg-primary/10' : 'hover:brightness-95 dark:hover:brightness-110'"
              @click="pickExisting(r.sku)"
            >
              <input type="radio" :checked="selectedExisting === r.sku" class="size-3.5 accent-primary shrink-0" />
              <span class="font-mono text-sm shrink-0">{{ r.sku }}</span>
              <span class="text-xs text-muted-foreground truncate flex-1">{{ r.name || '—' }}</span>
              <span class="text-[11px] text-muted-foreground tabular-nums shrink-0" title="saldo virtual">{{ saldo(r.saldo_virtual_total) }}</span>
            </button>
          </div>
        </div>

        <!-- Bins já existentes: entrada direta de N unidades. Oculto para Usado,
             que sempre vira produto z (não entra em bin regional existente). -->
        <div v-if="hasExisting && !isUsado" class="space-y-1.5">
          <p class="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Estoques existentes</p>
          <div class="grid grid-cols-2 sm:grid-cols-3 gap-2">
            <button
              v-for="v in existing"
              :key="v.sku"
              type="button"
              class="flex flex-col items-start rounded-md border px-3 py-2 text-left transition-colors"
              :class="selectedExisting === v.sku ? 'border-primary bg-primary/10' : 'hover:border-primary/50'"
              @click="pickExisting(v.sku)"
            >
              <span class="font-mono text-sm">{{ v.sku }}</span>
              <span class="mt-0.5 text-[11px] text-muted-foreground truncate w-full">{{ v.name || '—' }}</span>
            </button>
          </div>
        </div>

        <!-- Manter o SKU usado base.us (ex.: dg020.us) — só pra Usado. Entra no
             bin se já existir, senão cria base.us (sem virar produto z). -->
        <div v-if="isUsado" class="space-y-1.5">
          <p class="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Manter SKU usado</p>
          <div class="grid grid-cols-2 sm:grid-cols-3 gap-2">
            <button
              type="button"
              class="flex flex-col items-center rounded-md border px-3 py-2 text-center transition-colors"
              :class="selectedSuffix === 'us' ? 'border-primary bg-primary/10' : 'hover:border-primary/50'"
              @click="pickSuffix('us')"
            >
              <span class="font-mono text-sm">{{ usadoSku }}</span>
              <span class="mt-0.5 text-[11px] text-muted-foreground">mantém .us</span>
            </button>
          </div>
        </div>

        <!-- Criar produto novo z000N.<tag> — sempre disponível (mesmo com bins). -->
        <div class="space-y-1.5">
          <p class="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
            Criar novo (<span class="font-mono normal-case">zXXXX.&lt;tag&gt;</span>)
          </p>
          <p v-if="!hasExisting && !isUsado" class="text-xs text-amber-600 dark:text-amber-400">
            Nenhum estoque encontrado para esse SKU — escolha a tag do produto novo.
          </p>
          <p v-else-if="isUsado" class="text-xs text-muted-foreground">
            Usado sempre vira produto z — escolha a tag.
          </p>
          <div class="grid grid-cols-2 sm:grid-cols-3 gap-2">
            <button
              type="button"
              class="flex flex-col items-center rounded-md border px-3 py-2 text-center transition-colors"
              :class="selectedTag === SEM_TAG ? 'border-primary bg-primary/10' : 'hover:border-primary/50'"
              @click="pickTag(SEM_TAG)"
            >
              <span class="font-mono text-sm">zXXXX</span>
              <span class="mt-0.5 text-[11px] text-muted-foreground">sem tag → vira us</span>
            </button>
            <button
              v-for="t in tagChoices"
              :key="t"
              type="button"
              class="flex flex-col items-center rounded-md border px-3 py-2 text-center transition-colors"
              :class="selectedTag === t ? 'border-primary bg-primary/10' : 'hover:border-primary/50'"
              @click="pickTag(t)"
            >
              <span class="font-mono text-sm">zXXXX.{{ t }}</span>
              <span class="mt-0.5 text-[11px] text-muted-foreground">tag {{ t }}</span>
            </button>
          </div>
        </div>
      </template>

      <div class="flex justify-end gap-2 pt-1">
        <Button size="sm" variant="ghost" @click="emit('cancel')">cancelar</Button>
        <Button size="sm" :disabled="!canConfirm" @click="confirm">confirmar</Button>
      </div>
    </div>
  </div>
</template>
