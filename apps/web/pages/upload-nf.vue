<script setup lang="ts">
// Upload de NF-e XML → envio para o Mercado Livre. Migrado do xml-up.
//
// O operador:
//   1. Seleciona um ou mais .xml de NF-e
//   2. Marca quais lojas ML tentar (default = todas)
//   3. Clica "Enviar". O backend extrai o order_id do infCpl (16+
//      dígitos após "Fonte IBPT.") e percorre as lojas até uma aceitar
//      o POST /shipments/{id}/invoice_data.
import { computed, onMounted, ref } from 'vue'
import {
  AlertCircle, CheckCircle2, FileText, Loader2, Trash2, Upload, XCircle,
} from 'lucide-vue-next'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'controle_estoque', action: 'edit' },
})

const { api } = useApi()

type Attempt = {
  store: string
  success: boolean
  error: string | null
  shipping_id: string | null
}
type Result = {
  filename: string
  success: boolean
  order_id?: string | null
  store_name?: string | null
  shipping_id?: string | null
  error?: string | null
  attempts_details?: Attempt[]
}

const files = ref<File[]>([])
const stores = ref<string[]>([])
const selectedStores = ref<Set<string>>(new Set())
const processing = ref(false)
const currentFile = ref<string | null>(null)
const results = ref<Result[]>([])

const totalFiles = computed(() => files.value.length)
const successCount = computed(() => results.value.filter((r) => r.success).length)
const failCount = computed(() => results.value.filter((r) => !r.success).length)

async function loadStores() {
  try {
    const r = await api<{ stores: string[] }>('/api/nf/stores')
    stores.value = r.stores || []
    // Default: marca todas — operador desmarca o que não quer testar.
    selectedStores.value = new Set(stores.value)
  } catch (e) {
    console.error('failed to load ML stores', e)
  }
}

function onFiles(ev: Event) {
  const inp = ev.target as HTMLInputElement
  if (!inp.files) return
  // Append em vez de substituir — operador pode arrastar mais lotes.
  const next = Array.from(inp.files)
  files.value = [...files.value, ...next.filter((nf) =>
    !files.value.some((f) => f.name === nf.name && f.size === nf.size),
  )]
  inp.value = ''
}
function removeFile(idx: number) {
  files.value.splice(idx, 1)
}
function clearAll() {
  files.value = []
  results.value = []
  currentFile.value = null
}
function toggleStore(name: string) {
  const s = new Set(selectedStores.value)
  if (s.has(name)) s.delete(name)
  else s.add(name)
  selectedStores.value = s
}

async function processAll() {
  if (!files.value.length || !selectedStores.value.size) return
  processing.value = true
  results.value = []
  // Sequencial pra mostrar progresso por arquivo (a multipart call do
  // backend processa tudo de uma vez também, mas perder o progresso
  // visual em troca de uma round-trip não vale a pena pro operador).
  for (const file of files.value) {
    currentFile.value = file.name
    const fd = new FormData()
    fd.append('file', file)
    for (const s of selectedStores.value) fd.append('selected_stores', s)
    try {
      const r = await api<Result>('/api/nf/upload', { method: 'POST', body: fd })
      results.value.push({ filename: file.name, ...r })
    } catch (e: any) {
      results.value.push({
        filename: file.name,
        success: false,
        error: e?.data?.detail?.code || e?.message || 'erro',
        attempts_details: [],
      })
    }
  }
  currentFile.value = null
  processing.value = false
}

onMounted(loadStores)
</script>

<template>
  <div class="space-y-4 p-4 max-w-5xl">
    <div class="flex items-center gap-2">
      <Upload class="size-5 text-primary" />
      <h1 class="text-xl font-semibold">Upload de NF-e → Mercado Livre</h1>
    </div>

    <!-- Stores picker -->
    <section class="border rounded-md p-3 space-y-2">
      <div class="flex items-center justify-between">
        <h2 class="font-semibold text-sm">Lojas ML alvo</h2>
        <div class="text-xs text-muted-foreground">
          {{ selectedStores.size }} de {{ stores.length }} selecionada(s)
        </div>
      </div>
      <div v-if="!stores.length" class="text-xs text-muted-foreground">
        Nenhuma integração ML ativa encontrada.
      </div>
      <div v-else class="flex flex-wrap gap-1.5">
        <button
          v-for="s in stores"
          :key="s"
          type="button"
          class="rounded-full border px-2.5 py-1 text-xs transition-colors"
          :class="selectedStores.has(s) ? 'bg-primary text-primary-foreground border-primary' : 'border-muted-foreground/40 hover:bg-muted'"
          @click="toggleStore(s)"
        >
          {{ s }}
        </button>
      </div>
    </section>

    <!-- Upload area -->
    <section class="border rounded-md p-3 space-y-2">
      <div class="flex items-center gap-2 flex-wrap">
        <label class="inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm cursor-pointer hover:bg-muted">
          <FileText class="size-3.5" /> Selecionar XMLs
          <input type="file" accept=".xml" multiple class="hidden" @change="onFiles" />
        </label>
        <span class="text-xs text-muted-foreground">{{ totalFiles }} arquivo(s)</span>
        <button
          v-if="totalFiles > 0"
          class="ml-auto inline-flex items-center gap-1 rounded-md border border-destructive text-destructive px-2.5 py-1 text-xs hover:bg-destructive/10"
          @click="clearAll"
        >
          <Trash2 class="size-3" /> Limpar
        </button>
        <button
          class="inline-flex items-center gap-1.5 rounded-md bg-primary text-primary-foreground px-3 py-1.5 text-sm hover:opacity-90 disabled:opacity-50"
          :disabled="processing || !totalFiles || !selectedStores.size"
          @click="processAll"
        >
          <Loader2 v-if="processing" class="size-3.5 animate-spin" />
          <Upload v-else class="size-3.5" />
          {{ processing ? 'Enviando…' : `Enviar (${totalFiles})` }}
        </button>
      </div>

      <ul v-if="totalFiles" class="text-xs space-y-1 border-t pt-2">
        <li v-for="(f, idx) in files" :key="idx" class="flex items-center gap-2">
          <FileText class="size-3 text-muted-foreground shrink-0" />
          <span class="truncate flex-1">{{ f.name }}</span>
          <span class="text-muted-foreground">{{ (f.size / 1024).toFixed(0) }} KiB</span>
          <button v-if="!processing" class="text-muted-foreground hover:text-destructive" @click="removeFile(idx)">
            <Trash2 class="size-3" />
          </button>
        </li>
      </ul>
    </section>

    <!-- Live progress -->
    <div
      v-if="processing && currentFile"
      class="rounded border bg-muted/20 px-3 py-2 text-sm flex items-center gap-2"
    >
      <Loader2 class="size-3 animate-spin" />
      Processando: <strong>{{ currentFile }}</strong>
      ({{ results.length + 1 }} de {{ totalFiles }})
    </div>

    <!-- Results -->
    <section v-if="results.length" class="space-y-2">
      <div class="flex items-center gap-3 text-sm">
        <h2 class="font-semibold">Resultados</h2>
        <span class="inline-flex items-center gap-1 text-emerald-700">
          <CheckCircle2 class="size-3.5" /> {{ successCount }}
        </span>
        <span class="inline-flex items-center gap-1 text-red-700">
          <XCircle class="size-3.5" /> {{ failCount }}
        </span>
      </div>

      <div class="border rounded-md divide-y">
        <div
          v-for="(r, idx) in results"
          :key="idx"
          class="p-3 text-xs"
          :class="r.success ? 'bg-emerald-50/40' : 'bg-red-50/40'"
        >
          <div class="flex items-center gap-2 flex-wrap">
            <CheckCircle2 v-if="r.success" class="size-4 text-emerald-700 shrink-0" />
            <XCircle v-else class="size-4 text-red-700 shrink-0" />
            <strong class="truncate">{{ r.filename }}</strong>
            <span v-if="r.order_id" class="text-muted-foreground font-mono">
              pedido {{ r.order_id }}
            </span>
            <span v-if="r.success" class="ml-auto text-emerald-800">
              ✓ {{ r.store_name }}
            </span>
            <span v-else class="ml-auto text-red-700 inline-flex items-center gap-1">
              <AlertCircle class="size-3" /> {{ r.error || 'falha' }}
            </span>
          </div>
          <details v-if="r.attempts_details && r.attempts_details.length" class="mt-1.5">
            <summary class="cursor-pointer text-[10px] text-muted-foreground hover:text-foreground">
              {{ r.attempts_details.length }} tentativa(s)
            </summary>
            <ul class="mt-1 pl-4 space-y-0.5 text-[11px]">
              <li v-for="(a, i) in r.attempts_details" :key="i" class="flex items-center gap-2">
                <CheckCircle2 v-if="a.success" class="size-3 text-emerald-700" />
                <XCircle v-else class="size-3 text-red-700" />
                <span class="font-medium">{{ a.store }}</span>
                <span v-if="a.shipping_id" class="text-muted-foreground">ship={{ a.shipping_id }}</span>
                <span v-if="!a.success" class="text-muted-foreground italic">{{ a.error }}</span>
              </li>
            </ul>
          </details>
        </div>
      </div>
    </section>
  </div>
</template>
