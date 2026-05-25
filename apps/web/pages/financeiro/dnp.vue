<script setup lang="ts">
// DNP — Desenvolvimento de Produtos. Planilha de avaliação de novos
// produtos: operador preenche dados do fornecedor + parâmetros de
// venda, sistema calcula custo de importação rateado (incluindo
// certificado) e margem estimada em tempo real.
//
// Dólar do dia: bate na awesomeapi do lado do cliente ao carregar a
// página. Operador pode sobrescrever; o valor persistido em
// davinci.dnp_config é o que vale pra próxima carga.
//
// Layout: descricao livre substitui as 5 colunas antigas
// (voltagem/cor/material/tamanho/potencia). Foto opcional por linha
// com lightbox no click. Compra/Venda em destaque dourado por serem
// os campos-chave da decisão de compra.
import { computed, reactive, ref } from 'vue'
import { Plus, RefreshCw, Trash2, ExternalLink, Camera, X } from 'lucide-vue-next'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'financeiro_dnp', action: 'view' },
})

const { api } = useApi()
const auth = useAuthStore()
const canEdit = computed(() => {
  if (auth.isAdmin) return true
  const p = auth.user?.permissions?.financeiro_dnp
  return Boolean(p?.edit || p?.delete)
})
const canDelete = computed(() => {
  if (auth.isAdmin) return true
  return Boolean(auth.user?.permissions?.financeiro_dnp?.delete)
})

type Produto = {
  id: string
  produto: string | null
  link: string | null
  fabrica: string | null
  modelo: string | null
  moq: number | null
  descricao: string | null
  foto_url: string | null
  valor_usd: number | null
  projecao_compra: number | null
  fator: number | null
  venda_estimada: number | null
  frete: number | null
  comissao: number | null
  inmetro: string | null
  obs: string | null
}

const rows = ref<Produto[]>([])
const loading = ref(false)
const errorText = ref<string | null>(null)
const saveTimers = reactive<Record<string, ReturnType<typeof setTimeout>>>({})

// Singleton config
const dolar = ref<number>(5.0)
const certificado = ref<number>(10000)
const dolarApiStatus = ref<string | null>(null)
const configSaveTimer = ref<ReturnType<typeof setTimeout> | null>(null)

// Seleção pra exclusão em batch
const selected = ref<Set<string>>(new Set())

// Cache-busting token for the foto endpoint — gets bumped on upload so
// the thumbnail refreshes without a full reload.
const fotoBust = reactive<Record<string, number>>({})

// Lightbox state
const lightboxRow = ref<Produto | null>(null)

async function loadConfig() {
  try {
    const c = await api<{ dolar_dia: number | null; certificado: number | null }>('/api/financeiro/dnp/config')
    if (c.dolar_dia != null) dolar.value = Number(c.dolar_dia)
    if (c.certificado != null) certificado.value = Number(c.certificado)
  } catch {
    // mantém defaults
  }
}

async function loadProdutos() {
  loading.value = true
  errorText.value = null
  try {
    rows.value = await api<Produto[]>('/api/financeiro/dnp/produtos')
  } catch (e: any) {
    errorText.value = e?.data?.detail?.code || e?.message || 'erro'
    rows.value = []
  } finally {
    loading.value = false
  }
}

async function refreshDolar() {
  dolarApiStatus.value = 'Buscando…'
  try {
    const r = await fetch('https://economia.awesomeapi.com.br/json/last/USD-BRL', { cache: 'no-store' })
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    const j = await r.json()
    const bid = Number(j?.USDBRL?.bid)
    if (Number.isFinite(bid) && bid > 0) {
      dolar.value = bid
      dolarApiStatus.value = `Atualizado: ${new Date().toLocaleTimeString('pt-BR')}`
      void persistConfig()
    } else {
      dolarApiStatus.value = 'Resposta inválida'
    }
  } catch (e: any) {
    dolarApiStatus.value = `Falha: ${e?.message || 'rede'}`
  }
}

await loadConfig()
await loadProdutos()
void refreshDolar()

function scheduleConfigSave() {
  if (configSaveTimer.value) clearTimeout(configSaveTimer.value)
  configSaveTimer.value = setTimeout(() => {
    void persistConfig()
  }, 500)
}

async function persistConfig() {
  configSaveTimer.value = null
  try {
    await api('/api/financeiro/dnp/config', {
      method: 'PATCH',
      body: { dolar_dia: dolar.value, certificado: certificado.value },
    })
  } catch (e: any) {
    errorText.value = `Falha ao salvar config: ${e?.data?.detail?.code || 'erro'}`
  }
}

function scheduleSave(row: Produto, field: keyof Produto, value: any) {
  ;(row as any)[field] = value
  if (saveTimers[row.id]) clearTimeout(saveTimers[row.id])
  saveTimers[row.id] = setTimeout(() => {
    void persistRow(row, field)
  }, 500)
}
async function persistRow(row: Produto, field: keyof Produto) {
  delete saveTimers[row.id]
  try {
    await api(`/api/financeiro/dnp/produtos/${row.id}`, {
      method: 'PATCH',
      body: { [field]: row[field] },
    })
  } catch (e: any) {
    errorText.value = `Falha ao salvar ${String(field)}: ${e?.data?.detail?.code || 'erro'}`
  }
}

async function adicionar() {
  try {
    const r = await api<Produto>('/api/financeiro/dnp/produtos', {
      method: 'POST',
      body: { produto: '', link: '' },
    })
    rows.value = [...rows.value, r]
  } catch (e: any) {
    errorText.value = e?.data?.detail?.code || 'erro_create'
  }
}

async function excluirSelecionados() {
  if (selected.value.size === 0) return
  if (!confirm(`Excluir ${selected.value.size} produto(s)?`)) return
  const ids = [...selected.value]
  let ok = 0
  for (const id of ids) {
    try {
      await api(`/api/financeiro/dnp/produtos/${id}`, { method: 'DELETE' })
      ok++
    } catch {
      /* segue a fila — relata no fim */
    }
  }
  selected.value = new Set()
  if (ok < ids.length) errorText.value = `${ids.length - ok} falha(s) ao excluir`
  await loadProdutos()
}

// ── Foto upload ────────────────────────────────────────────────────
function fotoSrc(row: Produto): string | null {
  if (!row.foto_url) return null
  const bust = fotoBust[row.id] ?? 0
  return `/api/financeiro/dnp/produtos/${row.id}/foto${bust ? `?v=${bust}` : ''}`
}

async function uploadFoto(row: Produto, ev: Event) {
  const input = ev.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  const fd = new FormData()
  fd.append('file', file)
  try {
    const updated = await api<Produto>(
      `/api/financeiro/dnp/produtos/${row.id}/foto`,
      { method: 'POST', body: fd },
    )
    row.foto_url = updated.foto_url
    fotoBust[row.id] = Date.now()
  } catch (e: any) {
    errorText.value = `Falha ao subir foto: ${e?.data?.detail?.code || 'erro'}`
  } finally {
    // Reset input so the same file can be picked again later.
    input.value = ''
  }
}

async function removerFoto(row: Produto) {
  if (!row.foto_url) return
  if (!confirm('Remover foto deste produto?')) return
  try {
    await api(`/api/financeiro/dnp/produtos/${row.id}/foto`, { method: 'DELETE' })
    row.foto_url = null
    fotoBust[row.id] = Date.now()
  } catch (e: any) {
    errorText.value = `Falha ao remover foto: ${e?.data?.detail?.code || 'erro'}`
  }
}

// ── Cálculos ──────────────────────────────────────────────────────────
function num(v: number | null | undefined): number {
  return v == null || isNaN(Number(v)) ? 0 : Number(v)
}

// N (certificado rateado) = ((((L*O)*DOLAR)*4) + CERTIFICADO) / M
function calcCertificado(r: Produto): number | null {
  const L = num(r.valor_usd), O = num(r.fator), M = num(r.projecao_compra)
  if (!L || !O || !M) return null
  return ((((L * O) * dolar.value) * 4) + certificado.value) / M
}
// P (compra) = (L*O)*DOLAR + N
function calcCompra(r: Produto): number | null {
  const N = calcCertificado(r)
  const L = num(r.valor_usd), O = num(r.fator)
  if (N == null || !L || !O) return null
  return (L * O) * dolar.value + N
}
// MOQ Total = Compra × MOQ
function calcMoqTotal(r: Produto): number | null {
  const P = calcCompra(r)
  const M = num(r.moq)
  if (P == null || !M) return null
  return P * M
}
// Margem = (((Q*(1-S)) - R) / P) - 1
function calcMargem(r: Produto): number | null {
  const Q = num(r.venda_estimada), S = num(r.comissao), R = num(r.frete)
  const P = calcCompra(r)
  if (P == null || P === 0 || !r.venda_estimada) return null
  return (((Q * (1 - S)) - R) / P) - 1
}

function fmt2(v: number | null): string {
  if (v == null) return '—'
  return v.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function fmtMoney(v: number | null): string {
  if (v == null) return '—'
  return v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
}
// Comissão + Margem viram percentual inteiro arredondado pra exibição
// (Comissão é input — armazenamos 0.14, mostramos 14%; Margem é
// computada, mostramos como porcentagem inteira).
function fmtPctInt(v: number | null): string {
  if (v == null) return '—'
  return `${Math.round(v * 100)}%`
}
function margemClass(v: number | null): string {
  if (v == null) return ''
  if (v > 0) return 'text-emerald-700 dark:text-emerald-400 font-semibold'
  return 'text-red-600 dark:text-red-400 font-semibold'
}

// Formata um <input type="number" step="0.01"> pra sempre mostrar
// 2 casas decimais no blur. Mantém type=number durante a edição (setas
// + validação nativas) — só reformata o valor depois que o operador
// sai do campo.
function snapTwoDecimals(row: Produto, field: keyof Produto, ev: Event) {
  const el = ev.target as HTMLInputElement
  const v = Number(el.value)
  if (!Number.isFinite(v)) return
  const fixed = Math.round(v * 100) / 100
  if ((row as any)[field] !== fixed) {
    scheduleSave(row, field, fixed)
  }
}

// Comissão: input mostra "14" (percentual inteiro), armazenamos 0.14.
function comissaoDisplay(v: number | null): number | null {
  if (v == null) return null
  return Math.round(v * 100)
}
function onComissaoInput(row: Produto, value: string) {
  const pct = Number(value)
  if (!Number.isFinite(pct)) {
    scheduleSave(row, 'comissao', null)
    return
  }
  // Round to 4 decimals to dodge floating-point noise (14/100 = 0.14
  // exactly, but 35/100 produces 0.35000000000000003 on some JS engines).
  scheduleSave(row, 'comissao', Math.round(pct) / 100)
}

const totalRows = computed(() => rows.value.length)
</script>

<template>
  <div class="space-y-3 p-4">
    <div class="flex flex-wrap items-center gap-3">
      <div class="flex items-center gap-2">
        <h1 class="text-xl font-semibold">DNP — Desenvolvimento de Produtos</h1>
        <span class="text-xs text-muted-foreground">{{ totalRows }} {{ totalRows === 1 ? 'produto' : 'produtos' }}</span>
      </div>
      <button
        class="ml-auto inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
        :disabled="loading"
        @click="loadProdutos"
      >
        <RefreshCw class="size-3.5" :class="{ 'animate-spin': loading }" />
        Recarregar
      </button>
      <button
        v-if="canEdit"
        class="inline-flex items-center gap-1.5 rounded-md bg-primary text-primary-foreground px-3 py-1.5 text-sm hover:opacity-90"
        @click="adicionar"
      >
        <Plus class="size-3.5" /> Adicionar produto
      </button>
      <button
        v-if="canDelete && selected.size > 0"
        class="inline-flex items-center gap-1.5 rounded-md border border-destructive text-destructive px-3 py-1.5 text-sm hover:bg-destructive/10"
        @click="excluirSelecionados"
      >
        <Trash2 class="size-3.5" /> Excluir ({{ selected.size }})
      </button>
    </div>

    <!-- Config bar — Dólar (laranja) + Certificado (amarelo) -->
    <div class="flex flex-wrap items-center gap-3 border bg-muted/30 p-3 text-xs">
      <label class="inline-flex items-center gap-2">
        <span class="font-semibold uppercase tracking-wide text-[10px]">Dólar do dia</span>
        <input type="number" step="0.0001"
          class="h-7 w-28 border px-2 text-right text-sm dolar-input"
          :value="dolar" :disabled="!canEdit"
          @input="(e) => { dolar = Number((e.target as HTMLInputElement).value) || 0; scheduleConfigSave() }" />
        <button class="h-7 px-2 border rounded text-[10px] hover:bg-muted whitespace-nowrap"
          :disabled="!canEdit" @click="refreshDolar">
          ↻ awesomeapi
        </button>
        <span v-if="dolarApiStatus" class="text-[10px] text-muted-foreground">{{ dolarApiStatus }}</span>
      </label>
      <label class="inline-flex items-center gap-2 ml-4">
        <span class="font-semibold uppercase tracking-wide text-[10px]">Certificado</span>
        <input type="number" step="0.01"
          class="h-7 w-32 border px-2 text-right text-sm cert-input"
          :value="certificado" :disabled="!canEdit"
          @input="(e) => { certificado = Number((e.target as HTMLInputElement).value) || 0; scheduleConfigSave() }" />
      </label>
      <span class="ml-auto text-[10px] text-muted-foreground">
        Recalcula em tempo real ao alterar Dólar ou Certificado.
      </span>
    </div>

    <div v-if="errorText" class="text-sm text-destructive">erro: {{ errorText }}</div>

    <div class="border overflow-x-auto">
      <table class="grid-table w-full text-xs border-collapse">
        <thead>
          <tr class="bg-emerald-800 text-white text-[10px] uppercase tracking-wide">
            <th v-if="canDelete" class="w-8 text-center">
              <input type="checkbox" :checked="selected.size > 0 && selected.size === rows.length"
                @change="(e) => {
                  const c = (e.target as HTMLInputElement).checked
                  selected = new Set(c ? rows.map((r) => r.id) : [])
                }" />
            </th>
            <th class="text-left">Produto</th>
            <th class="text-center">Foto</th>
            <th class="text-left">Link</th>
            <th class="text-left">Fábrica</th>
            <th class="text-left">Modelo</th>
            <th class="text-right">MOQ</th>
            <th class="text-left">Descrição</th>
            <th class="text-right">Valor USD</th>
            <th class="text-right whitespace-normal">Projeção de Compra</th>
            <th class="text-right">Certificado</th>
            <th class="text-right">Fator</th>
            <th class="text-right">MOQ Total</th>
            <th class="text-right hl-cell">Compra</th>
            <th class="text-right hl-cell">Venda</th>
            <th class="text-right">Frete</th>
            <th class="text-right">Comissão</th>
            <th class="text-right">Margem</th>
            <th class="text-left">Inmetro</th>
            <th class="text-left">Obs</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!loading && rows.length === 0">
            <td :colspan="canDelete ? 20 : 19" class="py-6 text-center text-muted-foreground">
              Nenhum produto. Clique em "Adicionar produto" para começar.
            </td>
          </tr>
          <tr v-for="row in rows" :key="row.id"
            class="even:bg-muted/10 hover:bg-amber-50/40 dark:hover:bg-amber-900/10">
            <td v-if="canDelete" class="text-center">
              <input type="checkbox" :checked="selected.has(row.id)"
                @change="(e) => {
                  const c = (e.target as HTMLInputElement).checked
                  const s = new Set(selected)
                  if (c) s.add(row.id); else s.delete(row.id)
                  selected = s
                }" />
            </td>
            <td><input class="cell-input" :value="row.produto ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave(row, 'produto', (e.target as HTMLInputElement).value)" /></td>

            <!-- Foto: thumbnail clicável (abre lightbox) + upload + remover -->
            <td class="text-center foto-cell">
              <div v-if="row.foto_url" class="relative inline-block">
                <img
                  :src="fotoSrc(row) || ''"
                  alt="foto"
                  class="foto-thumb cursor-pointer"
                  @click="lightboxRow = row"
                />
                <button
                  v-if="canEdit"
                  class="absolute -top-1 -right-1 bg-white/95 dark:bg-black/80 border border-border rounded-full p-0.5 text-muted-foreground hover:text-destructive shadow-sm"
                  :title="'Remover foto'"
                  @click="removerFoto(row)"
                >
                  <X class="size-2.5" />
                </button>
              </div>
              <label v-else-if="canEdit"
                class="inline-flex items-center gap-1 cursor-pointer text-muted-foreground hover:text-foreground text-[10px]">
                <Camera class="size-3" />
                <input type="file" accept="image/*" class="hidden"
                  @change="(e) => uploadFoto(row, e)" />
              </label>
              <span v-else class="text-muted-foreground">—</span>
            </td>

            <td class="link-cell">
              <a v-if="row.link" :href="row.link" target="_blank" rel="noopener noreferrer"
                class="inline-flex items-center gap-1 text-blue-600 hover:underline" :title="row.link">
                <ExternalLink class="size-3" /> link
              </a>
              <input class="cell-input mt-0.5" :value="row.link ?? ''" :disabled="!canEdit"
                placeholder="https://..."
                @input="(e) => scheduleSave(row, 'link', (e.target as HTMLInputElement).value)" />
            </td>
            <td><input class="cell-input" :value="row.fabrica ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave(row, 'fabrica', (e.target as HTMLInputElement).value)" /></td>
            <td><input class="cell-input" :value="row.modelo ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave(row, 'modelo', (e.target as HTMLInputElement).value)" /></td>
            <td><input type="number" class="cell-input text-right"
              :value="row.moq ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave(row, 'moq', Number((e.target as HTMLInputElement).value) || null)" /></td>

            <!-- Descrição: substituiu voltagem|cor|material|tamanho|potencia. -->
            <td>
              <input class="cell-input" :value="row.descricao ?? ''" :disabled="!canEdit"
                placeholder="110/220 | preto | 5L | …"
                @input="(e) => scheduleSave(row, 'descricao', (e.target as HTMLInputElement).value)" />
            </td>

            <td><input type="number" step="0.01" class="cell-input text-right"
              :value="row.valor_usd ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave(row, 'valor_usd', Number((e.target as HTMLInputElement).value) || null)"
              @blur="(e) => snapTwoDecimals(row, 'valor_usd', e)" /></td>
            <td><input type="number" class="cell-input text-right"
              :value="row.projecao_compra ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave(row, 'projecao_compra', Number((e.target as HTMLInputElement).value) || null)" /></td>
            <td class="calc text-right">{{ fmt2(calcCertificado(row)) }}</td>
            <td><input type="number" step="0.01" class="cell-input text-right"
              :value="row.fator ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave(row, 'fator', Number((e.target as HTMLInputElement).value) || null)"
              @blur="(e) => snapTwoDecimals(row, 'fator', e)" /></td>
            <td class="calc text-right">{{ fmtMoney(calcMoqTotal(row)) }}</td>
            <td class="calc text-right hl-cell">{{ fmt2(calcCompra(row)) }}</td>
            <td class="hl-cell"><input type="number" step="0.01" class="cell-input text-right"
              :value="row.venda_estimada ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave(row, 'venda_estimada', Number((e.target as HTMLInputElement).value) || null)"
              @blur="(e) => snapTwoDecimals(row, 'venda_estimada', e)" /></td>
            <td><input type="number" step="0.01" class="cell-input text-right"
              :value="row.frete ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave(row, 'frete', Number((e.target as HTMLInputElement).value) || null)"
              @blur="(e) => snapTwoDecimals(row, 'frete', e)" /></td>
            <td>
              <div class="flex items-center justify-end">
                <input type="number" step="1" class="cell-input text-right"
                  :value="comissaoDisplay(row.comissao) ?? ''" :disabled="!canEdit"
                  @input="(e) => onComissaoInput(row, (e.target as HTMLInputElement).value)" />
                <span class="ml-0.5 text-muted-foreground">%</span>
              </div>
            </td>
            <td class="calc text-right" :class="margemClass(calcMargem(row))">{{ fmtPctInt(calcMargem(row)) }}</td>
            <td><input class="cell-input" :value="row.inmetro ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave(row, 'inmetro', (e.target as HTMLInputElement).value)" /></td>
            <td><input class="cell-input" :value="row.obs ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave(row, 'obs', (e.target as HTMLInputElement).value)" /></td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Lightbox modal — overlay com clique-fora pra fechar. -->
    <div
      v-if="lightboxRow"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4"
      @click="lightboxRow = null"
    >
      <button
        class="absolute top-4 right-4 text-white/80 hover:text-white"
        @click.stop="lightboxRow = null"
      >
        <X class="size-6" />
      </button>
      <img
        :src="fotoSrc(lightboxRow) || ''"
        :alt="lightboxRow.produto || 'foto'"
        class="max-h-[90vh] max-w-[90vw] object-contain shadow-2xl"
        @click.stop
      />
    </div>
  </div>
</template>

<style scoped>
.grid-table th,
.grid-table td {
  border: 1px solid hsl(var(--border));
  padding: 2px 4px;
  vertical-align: middle;
}
.grid-table thead th {
  border-color: rgba(255, 255, 255, 0.15);
  font-weight: 600;
  white-space: nowrap;
}
.grid-table td.calc {
  background: hsl(var(--muted) / 0.5);
  color: hsl(var(--muted-foreground));
  font-style: italic;
}
.cell-input {
  width: 100%;
  border: 0;
  background: rgb(254 252 232 / 0.6);
  padding: 2px 4px;
  font-size: 11px;
  color: inherit;
}
:global(.dark) .cell-input {
  background: rgb(120 53 15 / 0.15);
}
.cell-input:focus {
  outline: 1px solid hsl(var(--primary));
  background: hsl(var(--background));
}
.cell-input:disabled {
  cursor: not-allowed;
  opacity: 0.7;
  background: transparent;
}
/* Dólar = destaque laranja; Certificado = amarelo. */
.dolar-input {
  background: rgb(254 215 170 / 0.7);
}
:global(.dark) .dolar-input {
  background: rgb(154 52 18 / 0.25);
}
.cert-input {
  background: rgb(254 252 232 / 0.8);
}
:global(.dark) .cert-input {
  background: rgb(120 53 15 / 0.2);
}
.link-cell {
  min-width: 110px;
}
/* Compra + Venda — destaque dourado, foco visual do operador.
   Aplicado no header E nas células de dados. */
.hl-cell,
.grid-table thead th.hl-cell {
  background: rgb(255 192 0 / 0.85) !important;
  color: #1a1a1a;
  font-weight: 700;
}
.hl-cell .cell-input {
  background: rgb(255 235 180 / 0.95);
  font-weight: 600;
  color: #1a1a1a;
}
:global(.dark) .hl-cell {
  background: rgb(180 130 0 / 0.7) !important;
  color: #ffffff;
}
:global(.dark) .hl-cell .cell-input {
  background: rgb(180 130 0 / 0.4);
  color: #ffffff;
}
.foto-cell {
  width: 56px;
}
.foto-thumb {
  width: 44px;
  height: 44px;
  object-fit: cover;
  border: 1px solid hsl(var(--border));
  border-radius: 3px;
}
</style>
