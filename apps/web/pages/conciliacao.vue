<script setup lang="ts">
// Conciliação — pedido × financeiro das lojas (passo 2b, 14/10/2026).
// Desenho e decisões em docs/conciliacao-avaliacao.md; maquetes em docs/img/.
// Esta é a aba Pedidos: uma linha por pedido do Bling com as quatro versões do
// dinheiro (Bling, venda, fatura, extrato), o resultado e a situação; o Raio-X
// do pedido selecionado fica à direita. Extrato do dia e Resumo vêm depois.
import { Download, Loader2, RefreshCw, Search, Wand2 } from 'lucide-vue-next'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'conciliacao', action: 'view' },
})

type Loja = {
  conta: string
  integration_id: string
  platform: string
  inicio: string
  ultimo_extrato_em: string | null
  ultimo_fatura_em: string | null
  ultimo_recalculo_em: string | null
  ultimo_erro: string | null
}
type Pedido = {
  id: string
  pedido_bling: string
  numeroloja: string | null
  pack_id: string | null
  conta: string | null
  data_pedido: string | null
  situacao_bling: string | null
  bling_total: number | null
  venda_valor: number | null
  venda_tarifa: number | null
  venda_frete: number | null
  venda_cupom: number | null
  venda_estorno: number | null
  venda_liquido: number | null
  venda_status: string | null
  previsao_liberacao: string | null
  fatura_tarifa: number | null
  fatura_parcelamento: number | null
  fatura_frete: number | null
  fatura_bonificacao: number | null
  liberado_em: string | null
  liberado_valor: number | null
  saidas_depois: number | null
  recebido: number | null
  custo: number | null
  reembolso: number | null
  sobrou: number | null
  margem: number | null
  dif_bling_venda: number | null
  dif_previsto_recebido: number | null
  situacao: string
  situacao_label: string
  motivo: string | null
  etapa: string
  etapa_label: string
  etapa_em: string | null
  etapa_por: string | null
  etapa_motivo: string | null
  conferido: boolean
  conferido_por: string | null
  conferido_em: string | null
  observacao: string | null
}
type Resumo = {
  pedidos: number
  vendido: number
  tarifas_frete: number
  previsto: number
  recebido: number
  diferenca: number
  custo: number
  reembolso: number
  sobrou: number
  margem: number | null
  liberados: number
  por_situacao: Record<string, number>
  por_etapa: Record<string, number>
}
type Movimento = {
  id: string
  origem: string
  data: string | null
  tipo: string
  descricao: string | null
  sub_tipo: string | null
  credito: number | null
  debito: number | null
  bruto: number | null
  tarifa: number | null
  parcelamento: number | null
  frete: number | null
  descontado_da_venda: boolean | null
}
type Versao = Record<string, number | string | null | undefined>
type RaioX = { pedido: Pedido; versoes: Record<'bling' | 'venda' | 'fatura' | 'extrato', Versao>; movimentos: Movimento[] }

const { api } = useApi()
const canEdit = useCan('conciliacao', 'edit')
const toasts = useToasts()

const MARKETPLACES = [
  { key: 'ml', label: 'Mercado Livre', ativo: true },
  { key: 'shopee', label: 'Shopee', ativo: false },
  { key: 'amazon', label: 'Amazon', ativo: false },
  { key: 'tiktok', label: 'TikTok', ativo: false },
]
const VISOES = [
  { key: 'pedidos', label: 'Pedidos', ativo: true },
  { key: 'extrato', label: 'Extrato do dia', ativo: false },
  { key: 'resumo', label: 'Resumo', ativo: false },
]
const SITUACOES: Array<{ key: string; label: string; cls: string }> = [
  { key: 'bateu', label: 'Bateu', cls: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300' },
  { key: 'aguardando', label: 'Aguardando liberação', cls: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300' },
  { key: 'vencido', label: 'Não liberado (vencido)', cls: 'bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300' },
  { key: 'recebido_a_menos', label: 'Recebido a menos', cls: 'bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300' },
  { key: 'recebido_a_mais', label: 'Recebido a mais', cls: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300' },
  { key: 'bling_diferente', label: 'Bling ≠ venda', cls: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300' },
  { key: 'estornado_depois', label: 'Estornado depois', cls: 'bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300' },
  { key: 'cancelado', label: 'Cancelado', cls: 'bg-slate-200 text-slate-700 dark:bg-slate-700 dark:text-slate-200' },
  { key: 'sem_venda', label: 'Sem venda do ML', cls: 'bg-slate-200 text-slate-700 dark:bg-slate-700 dark:text-slate-200' },
]
const ETAPAS: Array<{ key: string; label: string; cls: string }> = [
  { key: 'aguardando', label: 'aguardando', cls: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300' },
  { key: 'conciliado', label: 'conciliado', cls: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300' },
  { key: 'pendencia', label: 'pendência', cls: 'bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300' },
  { key: 'finalizado', label: 'finalizado', cls: 'bg-slate-200 text-slate-800 dark:bg-slate-700 dark:text-slate-100' },
  { key: 'reaberto', label: 'reaberto', cls: 'bg-violet-100 text-violet-800 dark:bg-violet-900/40 dark:text-violet-300' },
]
const TIPOS_MOV: Record<string, string> = {
  venda_liberada: 'Venda liberada',
  mediacao: 'Mediação (reclamação perdida)',
  mediacao_cancelada: 'Mediação cancelada',
  estorno: 'Estorno',
  chargeback: 'Chargeback',
  disputa: 'Disputa',
  reserva_disputa: 'Reserva por reclamação',
  reserva_estorno: 'Reserva pra estorno',
  reserva_frete_devolucao: 'Reserva do frete de devolução',
  reserva_saque: 'Reserva pro saque',
  saque: 'Saque',
  frete: 'Frete',
  frete_cancelado: 'Frete cancelado',
  frete_devolucao: 'Frete de devolução',
  fatura_tarifa_venda: 'Fatura: custo por vender',
  fatura_tarifa_cobranca: 'Fatura: custo por cobrar',
  fatura_parcelamento: 'Fatura: taxa de parcelamento',
  fatura_parcelamento_comprador: 'Fatura: parcelamento pago pelo comprador',
  fatura_frete: 'Fatura: Mercado Envios',
  fatura_publicidade: 'Fatura: publicidade',
  fatura_bonificacao: 'Fatura: bonificação (devolução de tarifa)',
  fatura_outro: 'Fatura: outra cobrança',
}

const tab = ref('ml')
const visao = ref('pedidos')
const lojas = ref<Loja[]>([])
const loja = ref('')
const de = ref('')
const ate = ref('')
const dataFiltro = ref<'pedido' | 'liberacao'>('pedido')
const situacao = ref('')
const etapa = ref('')
const q = ref('')
const page = ref(1)
const SIZE = 100
const itens = ref<Pedido[]>([])
const total = ref(0)
const resumo = ref<Resumo | null>(null)
const loading = ref(false)
const erro = ref('')
const selecionado = ref<Pedido | null>(null)
const raiox = ref<RaioX | null>(null)
const raioxLoading = ref(false)
const sincronizando = ref(false)
const exportando = ref(false)
const salvando = ref(false)
const obsDraft = ref('')
const motivoDraft = ref('')

const lojaAtual = computed(() => lojas.value.find((l) => l.conta === loja.value) || null)
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / SIZE)))
const pctBateu = computed(() => {
  const r = resumo.value
  if (!r || !r.pedidos) return 0
  const ok = (r.por_situacao?.bateu || 0) + (r.por_situacao?.cancelado || 0)
  return Math.round((ok / r.pedidos) * 100)
})

function params() {
  const p: Record<string, string | number> = { loja: loja.value, data: dataFiltro.value, page: page.value, size: SIZE }
  if (de.value) p.de = de.value
  if (ate.value) p.ate = ate.value
  if (situacao.value) p.situacao = situacao.value
  if (etapa.value) p.etapa = etapa.value
  if (q.value.trim()) p.q = q.value.trim()
  return p
}

async function carregarLojas() {
  const r = await api<{ lojas: Loja[] }>('/api/conciliacao/lojas')
  lojas.value = r.lojas
  if (!loja.value && lojas.value.length) {
    loja.value = lojas.value[0].conta
    if (!de.value) de.value = lojas.value[0].inicio
  }
}

async function carregar() {
  if (!loja.value) return
  loading.value = true
  erro.value = ''
  try {
    const r = await api<{ itens: Pedido[]; total: number; resumo: Resumo }>('/api/conciliacao/pedidos', { query: params() })
    itens.value = r.itens
    total.value = r.total
    resumo.value = r.resumo
    if (selecionado.value && !itens.value.some((i) => i.pedido_bling === selecionado.value?.pedido_bling)) {
      selecionado.value = null
      raiox.value = null
    }
  } catch (e: any) {
    erro.value = e?.data?.detail?.code || e?.message || 'erro ao carregar'
  } finally {
    loading.value = false
  }
}

async function abrir(p: Pedido) {
  selecionado.value = p
  raioxLoading.value = true
  try {
    raiox.value = await api<RaioX>(`/api/conciliacao/pedidos/${encodeURIComponent(p.pedido_bling)}`, { query: { loja: loja.value } })
    obsDraft.value = raiox.value.pedido.observacao || ''
    motivoDraft.value = ''
  } catch (e: any) {
    toasts.error?.(e?.data?.detail?.code || 'não consegui abrir o pedido')
    raiox.value = null
  } finally {
    raioxLoading.value = false
  }
}

async function marcar(patch: Record<string, unknown>) {
  if (!selecionado.value || !canEdit.value) return
  salvando.value = true
  try {
    const r = await api<{ pedido: Pedido }>(`/api/conciliacao/pedidos/${encodeURIComponent(selecionado.value.pedido_bling)}`, {
      method: 'PATCH',
      body: { loja: loja.value, ...patch },
    })
    const i = itens.value.findIndex((x) => x.pedido_bling === r.pedido.pedido_bling)
    if (i >= 0) itens.value[i] = r.pedido
    selecionado.value = r.pedido
    if (raiox.value) raiox.value.pedido = r.pedido
    toasts.success?.('salvo')
  } catch (e: any) {
    const d = e?.data?.detail
    toasts.error?.(d?.mensagem || d?.code || 'não consegui salvar')
  } finally {
    salvando.value = false
  }
}

async function finalizar() {
  const p = selecionado.value
  if (!p) return
  const precisaMotivo = !['bateu', 'cancelado'].includes(p.situacao)
  if (precisaMotivo && !motivoDraft.value.trim()) {
    toasts.error?.('pra finalizar com pendência, escreva o motivo')
    return
  }
  await marcar({ etapa: 'finalizado', etapa_motivo: motivoDraft.value.trim() || undefined })
}

async function sincronizar() {
  if (!loja.value) return
  sincronizando.value = true
  try {
    const r = await api<{ enfileirado: boolean }>(`/api/conciliacao/lojas/${encodeURIComponent(loja.value)}/sincronizar`, { method: 'POST' })
    toasts.success?.(r.enfileirado ? 'rodada enfileirada: fatura, extrato e recálculo (leva alguns minutos)' : 'já tem uma rodada na fila')
  } catch (e: any) {
    toasts.error?.(e?.data?.detail?.code || 'não consegui enfileirar')
  } finally {
    sincronizando.value = false
  }
}

async function exportar() {
  exportando.value = true
  try {
    const { page: _p, size: _s, ...rest } = params()
    const blob = await api<Blob>('/api/conciliacao/export.xlsx', { method: 'POST', query: rest, responseType: 'blob' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `conciliacao_${loja.value}_${new Date().toISOString().slice(0, 10)}.xlsx`
    a.click()
    URL.revokeObjectURL(url)
  } catch {
    toasts.error?.('não consegui gerar o Excel')
  } finally {
    exportando.value = false
  }
}

function fmtMoney(v: number | null | undefined, vazio = '—') {
  if (v === null || v === undefined) return vazio
  return v.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function fmtPct(v: number | null | undefined) {
  if (v === null || v === undefined) return '—'
  return `${(v * 100).toLocaleString('pt-BR', { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%`
}
function fmtDia(v: string | null | undefined) {
  if (!v) return '—'
  const d = new Date(v)
  return d.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' })
}
function fmtDiaHora(v: string | null | undefined) {
  if (!v) return '—'
  const d = new Date(v)
  return d.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
}
function sitCls(key: string) {
  return SITUACOES.find((s) => s.key === key)?.cls || 'bg-slate-200 text-slate-700'
}
function etapaCls(key: string) {
  return ETAPAS.find((s) => s.key === key)?.cls || 'bg-slate-200 text-slate-700'
}
function tipoMov(t: string) {
  return TIPOS_MOV[t] || t
}
function sinal(v: number | null | undefined, cls: { pos: string; neg: string }) {
  if (v === null || v === undefined) return ''
  if (v > 0.005) return cls.pos
  if (v < -0.005) return cls.neg
  return ''
}
function limpar() {
  situacao.value = ''
  etapa.value = ''
  q.value = ''
  dataFiltro.value = 'pedido'
  de.value = lojaAtual.value?.inicio || ''
  ate.value = ''
  page.value = 1
  carregar()
}

watch([loja, dataFiltro, situacao, etapa], () => {
  page.value = 1
  carregar()
})
watch(page, () => carregar())

await carregarLojas()
await carregar()
</script>

<template>
  <div class="space-y-3">
    <div class="flex flex-wrap items-center justify-between gap-2 rounded-md border bg-card px-3 py-2">
      <div class="flex flex-wrap items-center gap-3">
        <h1 class="text-base font-bold">Conciliação</h1>
        <div class="flex gap-0.5 border-b">
          <button
            v-for="m in MARKETPLACES"
            :key="m.key"
            type="button"
            class="px-3 py-1.5 text-xs font-semibold border-b-2 -mb-px"
            :class="[
              tab === m.key ? 'border-primary text-primary' : 'border-transparent text-muted-foreground',
              !m.ativo && 'opacity-50 cursor-not-allowed',
            ]"
            :disabled="!m.ativo"
            :title="m.ativo ? '' : 'entra depois do Mercado Livre'"
            @click="tab = m.key"
          >
            {{ m.label }}
          </button>
        </div>
        <div class="inline-flex overflow-hidden rounded-md border text-xs">
          <button
            v-for="v in VISOES"
            :key="v.key"
            type="button"
            class="px-3 py-1 font-semibold"
            :class="[visao === v.key ? 'bg-primary text-primary-foreground' : 'text-muted-foreground', !v.ativo && 'opacity-50 cursor-not-allowed']"
            :disabled="!v.ativo"
            :title="v.ativo ? '' : 'próxima fase'"
            @click="visao = v.key"
          >
            {{ v.label }}
          </button>
        </div>
      </div>
      <div class="flex flex-wrap items-center gap-1.5 text-xs">
        <button type="button" class="inline-flex items-center gap-1 rounded-md border px-2.5 py-1.5 font-semibold hover:bg-muted" :disabled="loading" @click="carregar()">
          <RefreshCw class="size-3.5" :class="loading && 'animate-spin'" /> Recarregar
        </button>
        <button type="button" class="inline-flex items-center gap-1 rounded-md border px-2.5 py-1.5 font-semibold hover:bg-muted" :disabled="exportando" @click="exportar()">
          <Download class="size-3.5" /> Excel
        </button>
        <button
          v-if="canEdit"
          type="button"
          class="inline-flex items-center gap-1 rounded-md bg-primary px-2.5 py-1.5 font-semibold text-primary-foreground"
          :disabled="sincronizando"
          title="Lê a fatura do ML e o extrato do Mercado Pago agora e recalcula os pedidos desta loja"
          @click="sincronizar()"
        >
          <Loader2 v-if="sincronizando" class="size-3.5 animate-spin" /><Wand2 v-else class="size-3.5" /> Sincronizar agora
        </button>
      </div>
    </div>

    <div class="flex flex-wrap items-center gap-2 text-xs">
      <label class="inline-flex items-center gap-1 rounded-md border bg-card px-2 py-1">
        <span class="font-semibold text-muted-foreground">Loja</span>
        <select v-model="loja" class="bg-transparent outline-none">
          <option v-for="l in lojas" :key="l.conta" :value="l.conta">{{ l.conta }}</option>
        </select>
      </label>
      <label class="inline-flex items-center gap-1 rounded-md border bg-card px-2 py-1">
        <span class="font-semibold text-muted-foreground">De</span>
        <input v-model="de" type="date" class="bg-transparent outline-none" @change="page = 1; carregar()" />
        <span class="font-semibold text-muted-foreground">até</span>
        <input v-model="ate" type="date" class="bg-transparent outline-none" @change="page = 1; carregar()" />
      </label>
      <div class="inline-flex items-center gap-2 rounded-md border bg-card px-2 py-1">
        <span class="font-semibold text-muted-foreground">Data</span>
        <label class="inline-flex items-center gap-1"><input v-model="dataFiltro" type="radio" value="pedido" /> do pedido</label>
        <label class="inline-flex items-center gap-1"><input v-model="dataFiltro" type="radio" value="liberacao" /> da liberação</label>
      </div>
      <label class="inline-flex items-center gap-1 rounded-md border bg-card px-2 py-1">
        <span class="font-semibold text-muted-foreground">Situação</span>
        <select v-model="situacao" class="bg-transparent outline-none">
          <option value="">Todas</option>
          <option v-for="s in SITUACOES" :key="s.key" :value="s.key">{{ s.label }}</option>
        </select>
      </label>
      <label class="inline-flex items-center gap-1 rounded-md border bg-card px-2 py-1">
        <span class="font-semibold text-muted-foreground">Etapa</span>
        <select v-model="etapa" class="bg-transparent outline-none">
          <option value="">Todas</option>
          <option v-for="e in ETAPAS" :key="e.key" :value="e.key">{{ e.label }}</option>
        </select>
      </label>
      <label class="inline-flex items-center gap-1 rounded-md border bg-card px-2 py-1">
        <Search class="size-3.5 text-muted-foreground" />
        <input v-model="q" placeholder="pedido Bling ou venda ML" class="w-44 bg-transparent outline-none" @keyup.enter="page = 1; carregar()" />
      </label>
      <button type="button" class="text-muted-foreground underline" @click="limpar()">limpar</button>
      <span class="text-muted-foreground">{{ itens.length }} de {{ total }} pedidos</span>
      <span v-if="lojaAtual" class="ml-auto text-muted-foreground" :title="lojaAtual.ultimo_erro || ''">
        extrato lido {{ fmtDiaHora(lojaAtual.ultimo_extrato_em) }} · fatura {{ fmtDiaHora(lojaAtual.ultimo_fatura_em) }} · recálculo {{ fmtDiaHora(lojaAtual.ultimo_recalculo_em) }}
        <span v-if="lojaAtual.ultimo_erro" class="text-red-600">· erro: {{ lojaAtual.ultimo_erro }}</span>
      </span>
    </div>

    <div v-if="resumo" class="flex flex-wrap items-center gap-3 rounded-md border bg-card px-3 py-2 text-xs">
      <span class="text-sm font-bold">{{ loja }} · {{ resumo.pedidos }} pedidos</span>
      <span class="inline-flex items-center gap-2">
        <span class="inline-block h-2.5 w-48 overflow-hidden rounded-full bg-muted"><span class="block h-full bg-emerald-600" :style="{ width: pctBateu + '%' }" /></span>
        <b>{{ (resumo.por_situacao.bateu || 0) + (resumo.por_situacao.cancelado || 0) }} de {{ resumo.pedidos }}</b> fechados
      </span>
      <template v-for="s in SITUACOES" :key="s.key">
        <button v-if="resumo.por_situacao[s.key]" type="button" class="rounded px-1.5 py-0.5 font-bold" :class="s.cls" @click="situacao = situacao === s.key ? '' : s.key">
          {{ resumo.por_situacao[s.key] }} {{ s.label.toLowerCase() }}
        </button>
      </template>
      <span class="ml-auto inline-flex gap-2 text-muted-foreground">
        <template v-for="e in ETAPAS" :key="e.key">
          <button v-if="resumo.por_etapa[e.key]" type="button" class="rounded px-1.5 py-0.5 font-bold" :class="e.cls" @click="etapa = etapa === e.key ? '' : e.key">
            {{ resumo.por_etapa[e.key] }} {{ e.label }}
          </button>
        </template>
      </span>
    </div>

    <div class="grid gap-3 xl:grid-cols-[1fr_460px]">
      <div class="flex min-h-0 min-w-0 flex-col rounded-md border bg-card">
        <div class="flex items-center justify-between rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-xs font-semibold text-white">
          <span>Pedidos — uma linha por pedido do Bling</span>
          <span class="text-[11px] font-normal text-slate-300">o que a loja deveria receber × o que entrou de fato × quanto sobrou</span>
        </div>
        <div class="max-h-[64vh] overflow-auto">
          <table class="w-full border-collapse text-[11px]">
            <thead class="sticky top-0 z-10 bg-muted text-[10px] uppercase tracking-wide text-muted-foreground">
              <tr class="text-center normal-case">
                <th colspan="4" class="border-b px-1.5 py-1 text-left">Identificação</th>
                <th class="border-b border-l-2 px-1.5 py-1">Bling</th>
                <th colspan="4" class="border-b border-l-2 px-1.5 py-1">Venda ML</th>
                <th colspan="2" class="border-b border-l-2 px-1.5 py-1">Extrato (entrou de fato)</th>
                <th class="border-b border-l-2 px-1.5 py-1">Conc.</th>
                <th colspan="4" class="border-b border-l-2 px-1.5 py-1">Resultado de verdade</th>
                <th colspan="3" class="border-b border-l-2 px-1.5 py-1">Situação</th>
              </tr>
              <tr>
                <th class="border-b px-1.5 py-1 text-left">Data</th>
                <th class="border-b px-1.5 py-1 text-left">Pedido</th>
                <th class="border-b px-1.5 py-1 text-left">Venda ML</th>
                <th class="border-b px-1.5 py-1 text-left">Loja</th>
                <th class="border-b border-l-2 px-1.5 py-1 text-right">Total</th>
                <th class="border-b border-l-2 px-1.5 py-1 text-right">Valor</th>
                <th class="border-b px-1.5 py-1 text-right">Tarifa</th>
                <th class="border-b px-1.5 py-1 text-right">Frete</th>
                <th class="border-b px-1.5 py-1 text-right">Líq. previsto</th>
                <th class="border-b border-l-2 px-1.5 py-1 text-left">Liberado em</th>
                <th class="border-b px-1.5 py-1 text-right">Recebido</th>
                <th class="border-b border-l-2 px-1.5 py-1 text-right">Dif.</th>
                <th class="border-b border-l-2 px-1.5 py-1 text-right">Custo</th>
                <th class="border-b px-1.5 py-1 text-right">Reembolso</th>
                <th class="border-b px-1.5 py-1 text-right">Sobrou</th>
                <th class="border-b px-1.5 py-1 text-right">Margem</th>
                <th class="border-b border-l-2 px-1.5 py-1 text-left">Conciliação</th>
                <th class="border-b px-1.5 py-1 text-left">Motivo</th>
                <th class="border-b px-1.5 py-1 text-left">Etapa</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="loading && !itens.length">
                <td colspan="19" class="py-8 text-center text-muted-foreground"><Loader2 class="mr-1 inline size-4 animate-spin" /> carregando…</td>
              </tr>
              <tr v-else-if="erro">
                <td colspan="19" class="py-8 text-center text-red-600">{{ erro }}</td>
              </tr>
              <tr v-else-if="!itens.length">
                <td colspan="19" class="py-8 text-center text-muted-foreground">nenhum pedido com esse filtro</td>
              </tr>
              <tr
                v-for="p in itens"
                :key="p.id"
                class="cursor-pointer border-b hover:bg-muted/60"
                :class="[
                  selecionado?.pedido_bling === p.pedido_bling && 'outline outline-2 -outline-offset-2 outline-primary',
                  ['recebido_a_menos', 'vencido', 'estornado_depois'].includes(p.situacao) && 'bg-red-50 dark:bg-red-950/20',
                  ['aguardando', 'recebido_a_mais', 'bling_diferente'].includes(p.situacao) && 'bg-amber-50/60 dark:bg-amber-950/10',
                ]"
                @click="abrir(p)"
              >
                <td class="whitespace-nowrap px-1.5 py-1">{{ fmtDia(p.data_pedido) }}</td>
                <td class="whitespace-nowrap px-1.5 py-1 font-bold">{{ p.pedido_bling }}</td>
                <td class="whitespace-nowrap px-1.5 py-1">{{ p.numeroloja }}<span v-if="p.pack_id" class="ml-1 text-muted-foreground">pack</span></td>
                <td class="whitespace-nowrap px-1.5 py-1">{{ p.conta }}</td>
                <td class="whitespace-nowrap border-l-2 px-1.5 py-1 text-right tabular-nums" :class="p.dif_bling_venda && Math.abs(p.dif_bling_venda) > 0.05 ? 'font-bold text-red-600' : ''">{{ fmtMoney(p.bling_total) }}</td>
                <td class="whitespace-nowrap border-l-2 px-1.5 py-1 text-right tabular-nums">{{ fmtMoney(p.venda_valor) }}</td>
                <td class="whitespace-nowrap px-1.5 py-1 text-right tabular-nums">{{ fmtMoney(p.venda_tarifa) }}</td>
                <td class="whitespace-nowrap px-1.5 py-1 text-right tabular-nums">{{ fmtMoney(p.venda_frete) }}</td>
                <td class="whitespace-nowrap px-1.5 py-1 text-right font-bold tabular-nums">{{ fmtMoney(p.venda_liquido) }}</td>
                <td class="whitespace-nowrap border-l-2 px-1.5 py-1" :class="!p.liberado_em && 'text-muted-foreground'">
                  <template v-if="p.liberado_em">{{ fmtDia(p.liberado_em) }}</template>
                  <template v-else-if="p.situacao === 'vencido'"><span class="text-red-600">venceu {{ fmtDia(p.previsao_liberacao) }}</span></template>
                  <template v-else-if="p.previsao_liberacao">prev. {{ fmtDia(p.previsao_liberacao) }}</template>
                  <template v-else>—</template>
                </td>
                <td class="whitespace-nowrap px-1.5 py-1 text-right font-bold tabular-nums" :class="!p.liberado_em && 'font-normal text-muted-foreground'">
                  {{ fmtMoney(p.recebido) }}<span v-if="p.saidas_depois" class="ml-1 font-bold text-red-600">− {{ fmtMoney(p.saidas_depois) }}</span>
                </td>
                <td class="whitespace-nowrap border-l-2 px-1.5 py-1 text-right font-bold tabular-nums" :class="sinal(p.dif_previsto_recebido, { pos: 'text-emerald-700', neg: 'text-red-600' })">
                  <template v-if="p.dif_previsto_recebido !== null">{{ p.dif_previsto_recebido > 0 ? '+ ' : '' }}{{ fmtMoney(p.dif_previsto_recebido) }}</template>
                  <template v-else-if="p.dif_bling_venda && Math.abs(p.dif_bling_venda) > 0.05"><span class="text-red-600">Bling {{ p.dif_bling_venda > 0 ? '+' : '' }}{{ fmtMoney(p.dif_bling_venda) }}</span></template>
                  <template v-else>—</template>
                </td>
                <td class="whitespace-nowrap border-l-2 px-1.5 py-1 text-right tabular-nums">{{ fmtMoney(p.custo) }}</td>
                <td class="whitespace-nowrap px-1.5 py-1 text-right tabular-nums" :class="p.reembolso && p.reembolso > 0 ? 'text-red-600' : ''">{{ fmtMoney(p.reembolso) }}</td>
                <td class="whitespace-nowrap px-1.5 py-1 text-right font-bold tabular-nums" :class="[sinal(p.sobrou, { pos: 'text-emerald-700', neg: 'text-red-600' }), !p.liberado_em && 'font-normal text-muted-foreground']">
                  <span v-if="!p.liberado_em && p.sobrou !== null" class="mr-1 text-[10px]">prev.</span>{{ fmtMoney(p.sobrou) }}
                </td>
                <td class="whitespace-nowrap px-1.5 py-1 text-right font-bold tabular-nums" :class="!p.liberado_em && 'font-normal text-muted-foreground'">{{ fmtPct(p.margem) }}</td>
                <td class="whitespace-nowrap border-l-2 px-1.5 py-1"><span class="rounded px-1.5 py-0.5 text-[10px] font-bold" :class="sitCls(p.situacao)">● {{ p.situacao_label }}</span></td>
                <td class="max-w-[220px] truncate px-1.5 py-1 text-muted-foreground" :title="p.motivo || ''">{{ p.motivo }}</td>
                <td class="whitespace-nowrap px-1.5 py-1">
                  <span class="rounded px-1.5 py-0.5 text-[10px] font-bold" :class="etapaCls(p.etapa)">{{ p.etapa === 'finalizado' ? '■' : p.etapa === 'reaberto' ? '↺' : '●' }} {{ p.etapa_label }}</span>
                  <span v-if="p.conferido" class="ml-1 text-emerald-700" :title="`conferido por ${p.conferido_por} em ${fmtDiaHora(p.conferido_em)}`">✓</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="resumo" class="grid grid-cols-5 border-t text-[11px] xl:grid-cols-10">
          <div class="border-r px-2 py-1.5"><div class="text-muted-foreground">Pedidos</div><b class="text-xs tabular-nums">{{ resumo.pedidos }}</b></div>
          <div class="border-r px-2 py-1.5"><div class="text-muted-foreground">Vendido (ML)</div><b class="text-xs tabular-nums">{{ fmtMoney(resumo.vendido) }}</b></div>
          <div class="border-r px-2 py-1.5"><div class="text-muted-foreground">Tarifas + frete</div><b class="text-xs tabular-nums text-red-600">− {{ fmtMoney(resumo.tarifas_frete) }}</b></div>
          <div class="border-r px-2 py-1.5"><div class="text-muted-foreground">Líquido previsto</div><b class="text-xs tabular-nums">{{ fmtMoney(resumo.previsto) }}</b></div>
          <div class="border-r px-2 py-1.5"><div class="text-muted-foreground">Recebido ({{ resumo.liberados }} liberados)</div><b class="text-xs tabular-nums text-emerald-700">{{ fmtMoney(resumo.recebido) }}</b></div>
          <div class="border-r px-2 py-1.5"><div class="text-muted-foreground">Diferença</div><b class="text-xs tabular-nums" :class="sinal(resumo.diferenca, { pos: 'text-emerald-700', neg: 'text-red-600' })">{{ fmtMoney(resumo.diferenca) }}</b></div>
          <div class="border-r px-2 py-1.5"><div class="text-muted-foreground">Custo dos produtos</div><b class="text-xs tabular-nums text-red-600">− {{ fmtMoney(resumo.custo) }}</b></div>
          <div class="border-r px-2 py-1.5"><div class="text-muted-foreground">Reembolsos</div><b class="text-xs tabular-nums text-red-600">− {{ fmtMoney(resumo.reembolso) }}</b></div>
          <div class="border-r px-2 py-1.5"><div class="text-muted-foreground">Sobrou</div><b class="text-xs tabular-nums" :class="sinal(resumo.sobrou, { pos: 'text-emerald-700', neg: 'text-red-600' })">{{ fmtMoney(resumo.sobrou) }}</b></div>
          <div class="px-2 py-1.5"><div class="text-muted-foreground">Margem</div><b class="text-xs tabular-nums">{{ fmtPct(resumo.margem) }}</b></div>
        </div>
        <div class="flex items-center justify-between border-t px-3 py-1.5 text-[11px] text-muted-foreground">
          <span>Sobrou = Recebido (ou previsto, em cinza, enquanto não libera) − custo dos produtos − reembolsos do painel Reembolso. Margem = Sobrou ÷ custo.</span>
          <span class="inline-flex items-center gap-2">
            <button type="button" class="rounded border px-2 py-0.5 disabled:opacity-40" :disabled="page <= 1" @click="page--">‹</button>
            página {{ page }} de {{ totalPages }}
            <button type="button" class="rounded border px-2 py-0.5 disabled:opacity-40" :disabled="page >= totalPages" @click="page++">›</button>
          </span>
        </div>
      </div>

      <div class="flex min-h-0 min-w-0 flex-col rounded-md border bg-card">
        <div class="flex items-center justify-between rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-xs font-semibold text-white">
          <span>Raio-X {{ selecionado ? 'do pedido ' + selecionado.pedido_bling : '' }}</span>
          <span class="text-[11px] font-normal text-slate-300">as quatro versões do dinheiro, lado a lado</span>
        </div>
        <div v-if="!selecionado" class="p-6 text-center text-xs text-muted-foreground">clique num pedido pra ver as quatro versões do dinheiro e cada movimento dele</div>
        <div v-else-if="raioxLoading" class="p-6 text-center text-xs text-muted-foreground"><Loader2 class="mr-1 inline size-4 animate-spin" /> carregando…</div>
        <div v-else-if="raiox" class="space-y-2 p-2.5 text-[11px]">
          <div class="grid grid-cols-2 gap-x-3 gap-y-1 rounded border bg-muted/40 px-2 py-1.5">
            <span>Pedido Bling <b>{{ raiox.pedido.pedido_bling }}</b></span>
            <span>Venda ML <b>{{ raiox.pedido.numeroloja }}</b><span v-if="raiox.pedido.pack_id"> · pack {{ raiox.pedido.pack_id }}</span></span>
            <span>Loja <b>{{ raiox.pedido.conta }}</b> · vendido {{ fmtDia(raiox.pedido.data_pedido) }}</span>
            <span>Liberado {{ fmtDia(raiox.pedido.liberado_em) }}<span v-if="raiox.pedido.previsao_liberacao && !raiox.pedido.liberado_em"> (prev. {{ fmtDia(raiox.pedido.previsao_liberacao) }})</span></span>
            <span>Bling: {{ raiox.pedido.situacao_bling || '—' }}</span>
            <span><span class="rounded px-1.5 py-0.5 font-bold" :class="sitCls(raiox.pedido.situacao)">● {{ raiox.pedido.situacao_label }}</span> <span class="rounded px-1.5 py-0.5 font-bold" :class="etapaCls(raiox.pedido.etapa)">{{ raiox.pedido.etapa_label }}</span></span>
          </div>
          <table class="w-full border-collapse">
            <thead class="bg-muted text-[10px] uppercase text-muted-foreground">
              <tr><th class="px-1.5 py-1 text-left"></th><th class="px-1.5 py-1 text-right">Bling</th><th class="px-1.5 py-1 text-right">Venda ML</th><th class="px-1.5 py-1 text-right">Fatura ML</th><th class="px-1.5 py-1 text-right">Extrato</th></tr>
            </thead>
            <tbody class="tabular-nums">
              <tr class="border-b"><td class="px-1.5 py-1">Valor da venda</td><td class="px-1.5 py-1 text-right">{{ fmtMoney(raiox.versoes.bling.valor as number) }}</td><td class="px-1.5 py-1 text-right">{{ fmtMoney(raiox.versoes.venda.valor as number) }}</td><td class="px-1.5 py-1 text-right text-muted-foreground">—</td><td class="px-1.5 py-1 text-right">{{ fmtMoney(raiox.versoes.extrato.valor as number) }}</td></tr>
              <tr class="border-b"><td class="px-1.5 py-1">Tarifa de venda</td><td class="px-1.5 py-1 text-right">{{ fmtMoney(raiox.versoes.bling.tarifa as number) }}</td><td class="px-1.5 py-1 text-right">{{ fmtMoney(raiox.versoes.venda.tarifa as number) }}</td><td class="px-1.5 py-1 text-right">{{ fmtMoney(raiox.versoes.fatura.tarifa as number) }}</td><td class="px-1.5 py-1 text-right">{{ fmtMoney(raiox.versoes.extrato.tarifa as number) }}</td></tr>
              <tr class="border-b" :class="raiox.versoes.extrato.frete != null && raiox.versoes.venda.frete != null && Math.abs((raiox.versoes.extrato.frete as number) - (raiox.versoes.venda.frete as number)) > 0.05 && 'bg-red-50 dark:bg-red-950/20'"><td class="px-1.5 py-1">Frete (custo do envio)</td><td class="px-1.5 py-1 text-right">{{ fmtMoney(raiox.versoes.bling.frete as number) }}</td><td class="px-1.5 py-1 text-right">{{ fmtMoney(raiox.versoes.venda.frete as number) }}</td><td class="px-1.5 py-1 text-right">{{ fmtMoney(raiox.versoes.fatura.frete as number) }}</td><td class="px-1.5 py-1 text-right">{{ fmtMoney(raiox.versoes.extrato.frete as number) }}</td></tr>
              <tr class="border-b"><td class="px-1.5 py-1">Parcelamento</td><td class="px-1.5 py-1 text-right text-muted-foreground">—</td><td class="px-1.5 py-1 text-right text-muted-foreground">—</td><td class="px-1.5 py-1 text-right">{{ fmtMoney(raiox.versoes.fatura.parcelamento as number) }}</td><td class="px-1.5 py-1 text-right">{{ fmtMoney(raiox.versoes.extrato.parcelamento as number) }}</td></tr>
              <tr class="border-b"><td class="px-1.5 py-1">Cupom da loja</td><td class="px-1.5 py-1 text-right text-muted-foreground">—</td><td class="px-1.5 py-1 text-right">{{ fmtMoney(raiox.versoes.venda.cupom as number) }}</td><td class="px-1.5 py-1 text-right text-muted-foreground">—</td><td class="px-1.5 py-1 text-right">{{ fmtMoney(raiox.versoes.extrato.cupom as number) }}</td></tr>
              <tr class="border-b"><td class="px-1.5 py-1">Estorno / bonificação</td><td class="px-1.5 py-1 text-right text-muted-foreground">—</td><td class="px-1.5 py-1 text-right">{{ fmtMoney(raiox.versoes.venda.estorno as number) }}</td><td class="px-1.5 py-1 text-right">{{ fmtMoney(raiox.versoes.fatura.bonificacao as number) }}</td><td class="px-1.5 py-1 text-right">{{ fmtMoney(raiox.versoes.extrato.saidas_depois as number) }}</td></tr>
              <tr class="font-bold"><td class="px-1.5 py-1">Líquido</td><td class="px-1.5 py-1 text-right">{{ fmtMoney(raiox.versoes.bling.liquido as number) }}</td><td class="px-1.5 py-1 text-right">{{ fmtMoney(raiox.versoes.venda.liquido as number) }}</td><td class="px-1.5 py-1 text-right text-muted-foreground">—</td><td class="px-1.5 py-1 text-right">{{ fmtMoney(raiox.versoes.extrato.recebido as number) }}</td></tr>
            </tbody>
          </table>
          <div class="pt-1 font-bold text-[#1f3a5f] dark:text-slate-200">Movimentos deste pedido</div>
          <div class="max-h-56 overflow-auto rounded border">
            <table class="w-full border-collapse">
              <thead class="bg-muted text-[10px] uppercase text-muted-foreground"><tr><th class="px-1.5 py-1 text-left">Data</th><th class="px-1.5 py-1 text-left">Origem</th><th class="px-1.5 py-1 text-left">O que foi</th><th class="px-1.5 py-1 text-right">Valor</th></tr></thead>
              <tbody class="tabular-nums">
                <tr v-if="!raiox.movimentos.length"><td colspan="4" class="px-1.5 py-2 text-center text-muted-foreground">nenhum movimento ainda</td></tr>
                <tr v-for="m in raiox.movimentos" :key="m.id" class="border-b">
                  <td class="whitespace-nowrap px-1.5 py-1 text-muted-foreground">{{ fmtDiaHora(m.data) }}</td>
                  <td class="whitespace-nowrap px-1.5 py-1">{{ m.origem === 'mp_extrato' ? 'extrato' : 'fatura' }}</td>
                  <td class="px-1.5 py-1">{{ tipoMov(m.tipo) }}<span v-if="m.descontado_da_venda === false" class="ml-1 text-muted-foreground">(cobrada à parte)</span></td>
                  <td class="whitespace-nowrap px-1.5 py-1 text-right font-bold" :class="(m.credito || 0) > 0 ? 'text-emerald-700' : 'text-red-600'">{{ (m.credito || 0) > 0 ? '+ ' + fmtMoney(m.credito) : '− ' + fmtMoney(m.debito) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div class="rounded border px-2 py-1.5" :class="['recebido_a_menos', 'vencido', 'estornado_depois'].includes(raiox.pedido.situacao) ? 'border-red-300 bg-red-50 dark:bg-red-950/20' : 'border-emerald-300 bg-emerald-50 dark:bg-emerald-950/20'">
            <b>Previsto {{ fmtMoney(raiox.pedido.venda_liquido) }} · Recebido {{ fmtMoney(raiox.pedido.recebido) }} · Diferença {{ fmtMoney(raiox.pedido.dif_previsto_recebido) }}</b>
            <span v-if="raiox.pedido.motivo"> — {{ raiox.pedido.motivo }}</span><br />
            <b>Sobrou: {{ fmtMoney(raiox.pedido.recebido ?? raiox.pedido.venda_liquido) }} − custo {{ fmtMoney(raiox.pedido.custo) }} − reembolso {{ fmtMoney(raiox.pedido.reembolso) }} = {{ fmtMoney(raiox.pedido.sobrou) }} · margem {{ fmtPct(raiox.pedido.margem) }}</b>
            <div v-if="raiox.pedido.etapa_por" class="mt-1 text-muted-foreground">etapa {{ raiox.pedido.etapa_label }} por {{ raiox.pedido.etapa_por }} em {{ fmtDiaHora(raiox.pedido.etapa_em) }}<span v-if="raiox.pedido.etapa_motivo">: {{ raiox.pedido.etapa_motivo }}</span></div>
          </div>
          <div v-if="canEdit" class="space-y-1.5">
            <textarea v-model="obsDraft" rows="2" placeholder="observação" class="w-full rounded border bg-background px-2 py-1 text-[11px]" />
            <input v-if="!['bateu', 'cancelado'].includes(raiox.pedido.situacao)" v-model="motivoDraft" placeholder="motivo pra finalizar (obrigatório quando tem pendência)" class="w-full rounded border bg-background px-2 py-1 text-[11px]" />
            <div class="flex flex-wrap gap-1.5">
              <button type="button" class="rounded border px-2 py-1 font-semibold hover:bg-muted" :disabled="salvando" @click="marcar({ observacao: obsDraft })">Salvar observação</button>
              <button type="button" class="rounded border px-2 py-1 font-semibold hover:bg-muted" :disabled="salvando" @click="marcar({ conferido: !raiox.pedido.conferido })">{{ raiox.pedido.conferido ? 'Desmarcar conferido' : 'Marcar conferido' }}</button>
              <button v-if="raiox.pedido.etapa !== 'finalizado'" type="button" class="rounded bg-primary px-2 py-1 font-semibold text-primary-foreground" :disabled="salvando" @click="finalizar()">Finalizar</button>
              <button v-else type="button" class="rounded border px-2 py-1 font-semibold hover:bg-muted" :disabled="salvando" @click="marcar({ etapa: 'reaberto', etapa_motivo: motivoDraft || 'reaberto à mão' })">Reabrir</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
