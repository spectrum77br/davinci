<script setup lang="ts">
// Simulação — calculadora de custo de importação (1 mercadoria por
// cotação). Form vertical em seções (Cabeçalho, Mercadoria, Logística,
// Custos). Linhas calculadas (CIF, II, IPI, …, TOTAL DO PROCESSO, valor
// unidade, fator de multiplicação) recalculam em tempo real conforme
// o operador edita inputs.
//
// NCM: ao desfocar o campo, bate em /api/financeiro/ncm/{codigo} —
//      brasilapi devolve a descrição; alíquotas saem do cache local
//      (preenchidas pelo operador na 1ª consulta de cada NCM). Um
//      botão "Salvar alíquotas no cache" persiste o que estiver no
//      form atual, para reaproveitar nas próximas cotações.
import { computed, reactive, ref, watch } from 'vue'
import { Plus, RefreshCw, Trash2, Save } from 'lucide-vue-next'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'financeiro', action: 'view' },
})

const { api } = useApi()
const auth = useAuthStore()
const canEdit = computed(() => {
  if (auth.isAdmin) return true
  const p = auth.user?.permissions?.financeiro
  return Boolean(p?.edit || p?.delete)
})
const canDelete = computed(() => {
  if (auth.isAdmin) return true
  return Boolean(auth.user?.permissions?.financeiro?.delete)
})

type Cotacao = {
  id: string
  numero_cotacao: string | null
  cliente: string | null
  data: string | null
  processo: string | null
  exportador: string | null
  pais_origem: string | null
  descricao: string | null
  fornecedor: string | null
  quantidade: number | null
  ncm: string | null
  descricao_ncm: string | null
  invoice_numero: string | null
  porto_origem: string | null
  porto_destino: string | null
  etd: string | null
  eta: string | null
  taxa_cambio: number | null
  frete_seguro_usd: number | null
  valor_unitario_usd: number | null
  aliquota_ii: number | null
  aliquota_ipi: number | null
  aliquota_pis: number | null
  aliquota_cofins: number | null
  taxa_siscomex_usd: number | null
  armazenagem_usd: number | null
  despachante_sda_usd: number | null
  despachante_honorarios_usd: number | null
  corretagem_cambio_usd: number | null
  inspecao_usd: number | null
  outras_taxas_usd: number | null
  aliquota_taxas_gerais: number | null
  aliquota_impostos_fed: number | null
  aliquota_icms: number | null
  frete_nacional_usd: number | null
  aliquota_intermediacao: number | null
  created_at: string
}

const cotacoes = ref<Cotacao[]>([])
const selectedId = ref<string | null>(null)
const current = ref<Cotacao | null>(null)
const loading = ref(false)
const errorText = ref<string | null>(null)
const ncmStatus = ref<string | null>(null)
const saveTimers = reactive<Record<string, ReturnType<typeof setTimeout>>>({})

async function loadList() {
  loading.value = true
  errorText.value = null
  try {
    cotacoes.value = await api<Cotacao[]>('/api/financeiro/simulacao')
    if (!selectedId.value && cotacoes.value[0]) {
      selectedId.value = cotacoes.value[0].id
      current.value = { ...cotacoes.value[0] }
    } else if (selectedId.value) {
      const found = cotacoes.value.find((c) => c.id === selectedId.value)
      if (found) current.value = { ...found }
    }
  } catch (e: any) {
    errorText.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    loading.value = false
  }
}
await loadList()

watch(selectedId, (id) => {
  if (!id) {
    current.value = null
    return
  }
  const found = cotacoes.value.find((c) => c.id === id)
  current.value = found ? { ...found } : null
})

function scheduleSave(field: keyof Cotacao, value: any) {
  if (!current.value) return
  ;(current.value as any)[field] = value
  const id = current.value.id
  if (saveTimers[id]) clearTimeout(saveTimers[id])
  saveTimers[id] = setTimeout(() => {
    void persist(field)
  }, 500)
}

async function persist(field: keyof Cotacao) {
  if (!current.value) return
  const id = current.value.id
  delete saveTimers[id]
  try {
    await api(`/api/financeiro/simulacao/${id}`, {
      method: 'PATCH',
      body: { [field]: current.value[field] },
    })
    // Atualiza a lista paralela pra refletir mudança no header
    const idx = cotacoes.value.findIndex((c) => c.id === id)
    if (idx >= 0) cotacoes.value[idx] = { ...current.value }
  } catch (e: any) {
    errorText.value = `Falha ao salvar ${String(field)}: ${e?.data?.detail?.code || 'erro'}`
  }
}

async function novaCotacao() {
  try {
    const r = await api<Cotacao>('/api/financeiro/simulacao', {
      method: 'POST',
      body: {
        numero_cotacao: '',
        data: new Date().toISOString().slice(0, 10),
        // Defaults explícitos pra UI já mostrar as alíquotas finais
        aliquota_taxas_gerais: 0.03,
        aliquota_impostos_fed: 0.035,
        aliquota_icms: 0.04,
        aliquota_intermediacao: 0.16,
      },
    })
    cotacoes.value = [r, ...cotacoes.value]
    selectedId.value = r.id
    current.value = { ...r }
  } catch (e: any) {
    errorText.value = e?.data?.detail?.code || 'erro_create'
  }
}

async function excluirCotacao() {
  if (!current.value) return
  if (!confirm(`Excluir a cotação ${current.value.numero_cotacao || current.value.id.slice(0, 8)}?`)) return
  try {
    await api(`/api/financeiro/simulacao/${current.value.id}`, { method: 'DELETE' })
    cotacoes.value = cotacoes.value.filter((c) => c.id !== current.value!.id)
    selectedId.value = cotacoes.value[0]?.id ?? null
    current.value = selectedId.value
      ? { ...cotacoes.value.find((c) => c.id === selectedId.value)! }
      : null
  } catch (e: any) {
    errorText.value = e?.data?.detail?.code || 'erro_delete'
  }
}

// ── NCM lookup ────────────────────────────────────────────────────────
async function lookupNCM() {
  if (!current.value?.ncm) return
  ncmStatus.value = 'Consultando…'
  try {
    type NCMOut = {
      ncm: string
      descricao: string | null
      aliquota_ii: number | null
      aliquota_ipi: number | null
      aliquota_pis: number | null
      aliquota_cofins: number | null
      cached: boolean
    }
    const r = await api<NCMOut>(`/api/financeiro/ncm/${encodeURIComponent(current.value.ncm)}`)
    if (r.descricao) scheduleSave('descricao_ncm', r.descricao)
    if (r.aliquota_ii != null) scheduleSave('aliquota_ii', r.aliquota_ii)
    if (r.aliquota_ipi != null) scheduleSave('aliquota_ipi', r.aliquota_ipi)
    if (r.aliquota_pis != null) scheduleSave('aliquota_pis', r.aliquota_pis)
    if (r.aliquota_cofins != null) scheduleSave('aliquota_cofins', r.aliquota_cofins)
    ncmStatus.value = r.cached
      ? 'NCM em cache — alíquotas preenchidas.'
      : 'Descrição carregada da brasilapi. Preencha as alíquotas e clique em "Salvar no cache".'
  } catch (e: any) {
    ncmStatus.value = `Falha: ${e?.data?.detail?.code || 'erro'}`
  }
}

async function salvarAliquotasNoCache() {
  if (!current.value?.ncm) return
  try {
    await api(`/api/financeiro/ncm/${encodeURIComponent(current.value.ncm)}`, {
      method: 'PATCH',
      body: {
        aliquota_ii: current.value.aliquota_ii,
        aliquota_ipi: current.value.aliquota_ipi,
        aliquota_pis: current.value.aliquota_pis,
        aliquota_cofins: current.value.aliquota_cofins,
      },
    })
    ncmStatus.value = 'Alíquotas salvas no cache.'
  } catch (e: any) {
    ncmStatus.value = `Falha: ${e?.data?.detail?.code || 'erro'}`
  }
}

// ── Cálculos ──────────────────────────────────────────────────────────
function n(v: number | null | undefined): number {
  return v == null || isNaN(Number(v)) ? 0 : Number(v)
}

const calc = computed(() => {
  const c = current.value
  if (!c) return null
  const cambio = n(c.taxa_cambio) || 1
  const frete = n(c.frete_seguro_usd)
  const valorUnit = n(c.valor_unitario_usd)
  const qtd = n(c.quantidade)
  const totalInvoice = valorUnit * qtd
  const cif = totalInvoice + frete
  const ii = cif * n(c.aliquota_ii)
  const ipi = cif * n(c.aliquota_ipi)
  const pis = cif * n(c.aliquota_pis)
  const cofins = cif * n(c.aliquota_cofins)
  const afrmm = cif * 0.02
  const siscomex = n(c.taxa_siscomex_usd)
  const taxaBL = cif * 0.09
  const armaz = n(c.armazenagem_usd)
  const desp_sda = n(c.despachante_sda_usd)
  const desp_hon = n(c.despachante_honorarios_usd)
  const corret = n(c.corretagem_cambio_usd)
  const inspec = n(c.inspecao_usd)
  const outras = n(c.outras_taxas_usd)
  // SUBTOTAL: CIF + impostos federais + AFRMM + Siscomex + BL + armaz +
  // desp + corret + inspec + outras (linhas 28→41 do spec)
  const subtotal = cif + ii + ipi + pis + cofins + afrmm + siscomex + taxaBL + armaz + desp_sda + desp_hon + corret + inspec + outras
  const taxas_gerais = subtotal * n(c.aliquota_taxas_gerais)
  const imp_fed = subtotal * n(c.aliquota_impostos_fed)
  const icms = subtotal * n(c.aliquota_icms)
  const frete_nac = n(c.frete_nacional_usd)
  const intermed = cif * n(c.aliquota_intermediacao)
  const totalProcesso = subtotal + taxas_gerais + imp_fed + icms + frete_nac + intermed
  const valorUnidade = qtd > 0 ? totalProcesso / qtd : 0
  const fator = valorUnit > 0 ? valorUnidade / valorUnit : 0
  const lance_imposto = false
  return {
    cambio, frete, valorUnit, qtd, totalInvoice, cif,
    ii, ipi, pis, cofins, afrmm, siscomex, taxaBL,
    armaz, desp_sda, desp_hon, corret, inspec, outras,
    subtotal, taxas_gerais, imp_fed, icms, frete_nac, intermed,
    totalProcesso, valorUnidade, fator, lance_imposto,
  }
})

function brl(usd: number): number {
  return usd * (calc.value?.cambio || 1)
}
function fmtUSD(v: number | null | undefined): string {
  return v == null ? '' : `US$ ${n(v).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 4 })}`
}
function fmtBRL(v: number | null | undefined): string {
  return v == null ? '' : n(v).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
}
function fmtPct(v: number | null | undefined): string {
  return v == null ? '' : `${(n(v) * 100).toFixed(2)}%`
}

// Helper pra montar as linhas da tabela de custos
const custoRows = computed(() => {
  const c = calc.value
  if (!c) return []
  return [
    { label: 'Frete + Seguro', usd: c.frete, aliq: null, kind: 'input', field: 'frete_seguro_usd' },
    { label: 'Valor Unitário Origem', usd: c.valorUnit, aliq: null, kind: 'input', field: 'valor_unitario_usd', step: '0.0001' },
    { label: 'Total Invoice (Valor × Qtd)', usd: c.totalInvoice, aliq: null, kind: 'calc' },
    { label: 'TOTAL CIF', usd: c.cif, aliq: null, kind: 'calc', bold: true },
    { label: 'II', usd: c.ii, aliq: 'aliquota_ii', kind: 'aliquota' },
    { label: 'IPI', usd: c.ipi, aliq: 'aliquota_ipi', kind: 'aliquota' },
    { label: 'PIS', usd: c.pis, aliq: 'aliquota_pis', kind: 'aliquota' },
    { label: 'COFINS', usd: c.cofins, aliq: 'aliquota_cofins', kind: 'aliquota' },
    { label: 'AFRMM (2% CIF)', usd: c.afrmm, aliq: 0.02, kind: 'calc' },
    { label: 'Taxa SISCOMEX', usd: c.siscomex, aliq: null, kind: 'input', field: 'taxa_siscomex_usd' },
    { label: 'Taxa BL (9% CIF)', usd: c.taxaBL, aliq: 0.09, kind: 'calc' },
    { label: 'Armazenagem Terminal', usd: c.armaz, aliq: null, kind: 'input', field: 'armazenagem_usd' },
    { label: 'Despachante S.D.A', usd: c.desp_sda, aliq: null, kind: 'input', field: 'despachante_sda_usd' },
    { label: 'Despachante Honorários', usd: c.desp_hon, aliq: null, kind: 'input', field: 'despachante_honorarios_usd' },
    { label: 'Corretagem Câmbio', usd: c.corret, aliq: null, kind: 'input', field: 'corretagem_cambio_usd' },
    { label: 'Inspeção (pré-embarque)', usd: c.inspec, aliq: null, kind: 'input', field: 'inspecao_usd' },
    { label: 'Outras Taxas', usd: c.outras, aliq: null, kind: 'input', field: 'outras_taxas_usd' },
    { label: 'SUBTOTAL', usd: c.subtotal, aliq: null, kind: 'calc', bold: true },
    { label: 'Taxas Gerais Importação', usd: c.taxas_gerais, aliq: 'aliquota_taxas_gerais', kind: 'aliquota' },
    { label: 'Impostos Federais Saída NF', usd: c.imp_fed, aliq: 'aliquota_impostos_fed', kind: 'aliquota' },
    { label: 'ICMS', usd: c.icms, aliq: 'aliquota_icms', kind: 'aliquota' },
    { label: 'Frete Nacional', usd: c.frete_nac, aliq: null, kind: 'input', field: 'frete_nacional_usd' },
    { label: 'Intermediação (% CIF)', usd: c.intermed, aliq: 'aliquota_intermediacao', kind: 'aliquota' },
    { label: 'TOTAL DO PROCESSO', usd: c.totalProcesso, aliq: null, kind: 'calc', bold: true },
    { label: 'VALOR POR UNIDADE / KIT', usd: c.valorUnidade, aliq: null, kind: 'calc', bold: true },
    { label: 'FATOR DE MULTIPLICAÇÃO', usd: c.fator, aliq: null, kind: 'calc', bold: true, raw: true },
  ] as Array<{
    label: string
    usd: number
    aliq: string | number | null
    kind: 'input' | 'calc' | 'aliquota'
    field?: keyof Cotacao
    step?: string
    bold?: boolean
    raw?: boolean
  }>
})
</script>

<template>
  <div class="space-y-3 p-4">
    <div class="flex flex-wrap items-center gap-3">
      <h1 class="text-xl font-semibold">Simulação — Importação</h1>
      <select
        v-model="selectedId"
        class="h-8 border rounded px-2 bg-background text-sm min-w-[260px]"
      >
        <option v-if="cotacoes.length === 0" :value="null" disabled>Nenhuma cotação</option>
        <option v-for="c in cotacoes" :key="c.id" :value="c.id">
          {{ c.numero_cotacao || `#${c.id.slice(0, 8)}` }} — {{ c.cliente || 'sem cliente' }}{{ c.data ? ` (${c.data})` : '' }}
        </option>
      </select>
      <button
        class="inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
        :disabled="loading"
        @click="loadList"
      >
        <RefreshCw class="size-3.5" :class="{ 'animate-spin': loading }" />
        Recarregar
      </button>
      <button
        v-if="canEdit"
        class="ml-auto inline-flex items-center gap-1.5 rounded-md bg-primary text-primary-foreground px-3 py-1.5 text-sm hover:opacity-90"
        @click="novaCotacao"
      >
        <Plus class="size-3.5" /> Nova cotação
      </button>
      <button
        v-if="canDelete && current"
        class="inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm hover:bg-destructive/10 text-destructive"
        @click="excluirCotacao"
      >
        <Trash2 class="size-3.5" /> Excluir
      </button>
    </div>

    <div v-if="errorText" class="text-sm text-destructive">erro: {{ errorText }}</div>

    <div v-if="!current" class="border p-10 text-center text-muted-foreground text-sm">
      Selecione uma cotação ou crie uma nova.
    </div>

    <div v-else class="grid gap-4 lg:grid-cols-2">
      <!-- QUOTATION IMPORT (cabeçalho) — tabela 2 colunas estilo planilha -->
      <table class="grid-table border-collapse text-xs">
        <thead>
          <tr class="bg-black text-white"><th colspan="2" class="text-left tracking-wider">QUOTATION IMPORT</th></tr>
        </thead>
        <tbody>
          <tr>
            <td class="label">Nº Cotação</td>
            <td><input class="cell-input" :value="current.numero_cotacao ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave('numero_cotacao', (e.target as HTMLInputElement).value)" /></td>
          </tr>
          <tr>
            <td class="label">Cliente</td>
            <td><input class="cell-input" :value="current.cliente ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave('cliente', (e.target as HTMLInputElement).value)" /></td>
          </tr>
          <tr>
            <td class="label">Data</td>
            <td><input type="date" class="cell-input" :value="current.data ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave('data', (e.target as HTMLInputElement).value || null)" /></td>
          </tr>
          <tr>
            <td class="label">Processo</td>
            <td><input class="cell-input" :value="current.processo ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave('processo', (e.target as HTMLInputElement).value)" /></td>
          </tr>
          <tr>
            <td class="label">Exportador</td>
            <td><input class="cell-input" :value="current.exportador ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave('exportador', (e.target as HTMLInputElement).value)" /></td>
          </tr>
          <tr>
            <td class="label">País Origem</td>
            <td><input class="cell-input" :value="current.pais_origem ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave('pais_origem', (e.target as HTMLInputElement).value)" /></td>
          </tr>
        </tbody>
      </table>

      <!-- MERCADORIA — campos editáveis (amarelo) + NCM/Qtd em laranja -->
      <table class="grid-table border-collapse text-xs">
        <thead>
          <tr class="bg-black text-white"><th colspan="2" class="text-left tracking-wider">MERCADORIA</th></tr>
        </thead>
        <tbody>
          <tr>
            <td class="label">Descrição</td>
            <td><input class="cell-input" :value="current.descricao ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave('descricao', (e.target as HTMLInputElement).value)" /></td>
          </tr>
          <tr>
            <td class="label">Fornecedor</td>
            <td><input class="cell-input" :value="current.fornecedor ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave('fornecedor', (e.target as HTMLInputElement).value)" /></td>
          </tr>
          <tr>
            <td class="label">Quantidade</td>
            <td><input type="number" class="cell-input cell-orange text-right" :value="current.quantidade ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave('quantidade', Number((e.target as HTMLInputElement).value) || null)" /></td>
          </tr>
          <tr>
            <td class="label">NCM</td>
            <td class="flex items-stretch gap-1 p-0">
              <input class="cell-input cell-orange flex-1" :value="current.ncm ?? ''" :disabled="!canEdit"
                placeholder="ex: 85094010"
                @input="(e) => scheduleSave('ncm', (e.target as HTMLInputElement).value)"
                @blur="lookupNCM" />
              <button v-if="canEdit" class="px-1.5 border-l text-[10px] hover:bg-muted whitespace-nowrap"
                :disabled="!current.ncm" :title="'Salvar as 4 alíquotas atuais no cache do NCM'"
                @click="salvarAliquotasNoCache">
                <Save class="size-3 inline" />
              </button>
            </td>
          </tr>
          <tr>
            <td class="label">Descrição NCM</td>
            <td class="calc">{{ current.descricao_ncm || '—' }}</td>
          </tr>
          <tr>
            <td class="label">Invoice nº</td>
            <td><input class="cell-input" :value="current.invoice_numero ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave('invoice_numero', (e.target as HTMLInputElement).value)" /></td>
          </tr>
          <tr v-if="ncmStatus">
            <td colspan="2" class="text-[10px] text-muted-foreground px-2 py-1">{{ ncmStatus }}</td>
          </tr>
        </tbody>
      </table>

      <!-- LOGÍSTICA — span 2 columns -->
      <table class="grid-table border-collapse text-xs lg:col-span-2">
        <thead>
          <tr class="bg-black text-white"><th colspan="2" class="text-left tracking-wider">LOGÍSTICA</th></tr>
        </thead>
        <tbody>
          <tr>
            <td class="label">Porto Origem</td>
            <td><input class="cell-input" :value="current.porto_origem ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave('porto_origem', (e.target as HTMLInputElement).value)" /></td>
          </tr>
          <tr>
            <td class="label">Porto Destino</td>
            <td><input class="cell-input" :value="current.porto_destino ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave('porto_destino', (e.target as HTMLInputElement).value)" /></td>
          </tr>
          <tr>
            <td class="label">ETD (Saída)</td>
            <td><input type="date" class="cell-input" :value="current.etd ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave('etd', (e.target as HTMLInputElement).value || null)" /></td>
          </tr>
          <tr>
            <td class="label">ETA (Chegada)</td>
            <td><input type="date" class="cell-input" :value="current.eta ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave('eta', (e.target as HTMLInputElement).value || null)" /></td>
          </tr>
          <tr>
            <td class="label">Taxa Câmbio (USD/BRL)</td>
            <td><input type="number" step="0.0001" class="cell-input cell-orange text-right"
              :value="current.taxa_cambio ?? ''" :disabled="!canEdit"
              @input="(e) => scheduleSave('taxa_cambio', Number((e.target as HTMLInputElement).value) || null)" /></td>
          </tr>
        </tbody>
      </table>

      <!-- CUSTOS — 4 colunas estilo planilha completa -->
      <div class="lg:col-span-2 overflow-x-auto">
        <table class="grid-table w-full text-xs border-collapse">
          <thead>
            <tr class="bg-emerald-800 text-white text-[10px] uppercase tracking-wide">
              <th class="text-left">Descrição</th>
              <th class="text-right">USD</th>
              <th class="text-right">BRL</th>
              <th class="text-right w-[110px]">Alíquota</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(r, i) in custoRows" :key="i"
              class="even:bg-muted/10"
              :class="{ 'font-semibold bg-emerald-100/60 dark:bg-emerald-900/30': r.bold }">
              <td class="label">{{ r.label }}</td>
              <td class="text-right" :class="r.kind === 'calc' ? 'calc' : ''">
                <input v-if="r.kind === 'input' && canEdit" type="number" :step="r.step || '0.01'" class="cell-input text-right"
                  :value="r.usd || ''"
                  @input="(e) => scheduleSave(r.field!, Number((e.target as HTMLInputElement).value) || null)" />
                <span v-else>{{ r.raw ? r.usd.toFixed(4) : fmtUSD(r.usd) }}</span>
              </td>
              <td class="text-right calc">{{ r.raw ? '—' : fmtBRL(brl(r.usd)) }}</td>
              <td class="text-right">
                <input v-if="r.kind === 'aliquota' && canEdit" type="number" step="0.0001"
                  class="cell-input cell-orange text-right"
                  :value="(current as any)[r.aliq] ?? ''"
                  @input="(e) => scheduleSave(r.aliq as keyof Cotacao, Number((e.target as HTMLInputElement).value) || null)" />
                <span v-else-if="typeof r.aliq === 'number'" class="calc-inline">{{ fmtPct(r.aliq) }}</span>
                <span v-else-if="r.aliq" class="calc-inline">{{ fmtPct((current as any)[r.aliq]) }}</span>
                <span v-else>—</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* Visual de planilha — 3 minitabelas 2-col (Cabeçalho/Mercadoria/
   Logística) e a tabela grande de Custos. Header preto nas seções,
   verde escuro no thead da tabela de Custos. */
.grid-table {
  width: 100%;
}
.grid-table th,
.grid-table td {
  border: 1px solid hsl(var(--border));
  padding: 2px 5px;
  white-space: nowrap;
  vertical-align: middle;
}
.grid-table thead th {
  font-weight: 600;
  padding: 4px 6px;
  font-size: 11px;
}
.grid-table td.label {
  background: hsl(var(--muted) / 0.6);
  font-weight: 500;
  width: 180px;
}
/* Calc cells: cinza claro, itálico, não-editável */
.grid-table td.calc {
  background: hsl(var(--muted) / 0.4);
  color: hsl(var(--muted-foreground));
  font-style: italic;
  text-overflow: ellipsis;
  overflow: hidden;
}
.calc-inline {
  color: hsl(var(--muted-foreground));
  font-style: italic;
}
/* Inputs editáveis em fundo amarelo (estilo Excel) */
.cell-input {
  width: 100%;
  border: 0;
  background: rgb(254 252 232 / 0.6); /* yellow-50 */
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
/* Inputs "automáticos / impactam cálculo" em laranja (NCM, Quantidade,
   Câmbio, Alíquotas NCM) */
.cell-orange {
  background: rgb(254 215 170 / 0.7) !important; /* orange-200 */
}
:global(.dark) .cell-orange {
  background: rgb(154 52 18 / 0.25) !important; /* orange-800 */
}
</style>
