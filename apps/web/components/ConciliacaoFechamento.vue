<script setup lang="ts">
// Conciliação › Fechamento do mês (passo 2d, 14/10/2026). O mês de uma loja em
// três contas que precisam fechar entre si: as VENDAS (pela data do pedido), o
// CAIXA (pela data do movimento no extrato do Mercado Pago, com os ajustes de
// meses anteriores e a prova dos nove saldo inicial + movimento = saldo final)
// e a FATURA do ML (o resumo que o ML dá pro período × a soma dos detalhes que
// lemos linha a linha). Embaixo, os dias do mês: quais fecharam.
import { ChevronLeft, ChevronRight, Download, Loader2, RefreshCw } from 'lucide-vue-next'
import { SITUACOES, fmtDia, fmtDiaHora, fmtMoney, fmtPct, sinal, tipoMov } from '~/lib/conciliacao'

const props = defineProps<{ loja: string; lojas: Array<{ conta: string; inicio: string }> }>()
const emit = defineEmits<{
  (e: 'update:loja', loja: string): void
  (e: 'abrir-extrato', loja: string, dia: string): void
}>()

type Vendas = {
  pedidos: number
  vendido: number
  tarifa: number
  frete: number
  parcelamento: number
  cupom: number
  previsto: number
  recebido: number
  estornos: number
  custo: number
  reembolso: number
  sobrou: number
  margem: number | null
  liberados: number
  liberados_cancelados: number
  finalizados: number
  por_situacao: Record<string, number>
  por_etapa: Record<string, number>
}
type Caixa = {
  linhas: number
  liberado: number
  mediacoes: number
  mediacoes_canceladas: number
  estornos: number
  chargebacks: number
  frete: number
  outros: number
  ajustes_meses_anteriores: number
  ajustes_qtd: number
  cancelados_entrou: number
  cancelados_saiu: number
  cancelados_liquido: number
  cancelados_linhas: number
  resultado_caixa: number
  reservas_liquido: number
  saques: number
  movimento_liquido: number
  saldo_inicial: number | null
  saldo_final: number | null
  bate: boolean | null
}
type DiaMes = { dia: string; linhas: number; pendentes: number; creditos: number | null; debitos: number | null; fechado_em: string | null; fechado_por: string | null }
type Fatura = {
  periodo: { chave: string; de: string | null; ate: string | null; expira: string | null }
  resumo_cobrancas: Array<{ tipo: string | null; label: string | null; valor: number | null }>
  resumo_bonificacoes: Array<{ tipo: string | null; label: string | null; valor: number | null }>
  resumo_total: number | null
  pago: Record<string, number>
  detalhes_por_sub_tipo: Array<{ sub_tipo: string | null; tipo: string; linhas: number; cobrado: number | null; bonificado: number | null }>
  detalhes_cobrancas: number
  detalhes_bonificacoes: number
  bonificacoes_mesma_fatura: number
  bonificacoes_de_faturas_anteriores: number
  bonificacoes_anteriores_linhas: number
  lido_em: string | null
  bate: boolean | null
  bate_bonificacoes: boolean | null
}
type Fechamento = {
  loja: string
  mes: string
  de: string
  ate: string
  vendas: Vendas
  caixa: Caixa
  dias: DiaMes[]
  dias_fechados: number
  fatura: Fatura
}

const { api } = useApi()
const toasts = useToasts()
const exportando = ref(false)

function mesLocal(d: Date) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}
function somaMes(iso: string, n: number) {
  const d = new Date(iso + '-15T12:00:00')
  d.setMonth(d.getMonth() + n)
  return mesLocal(d)
}
function nomeMes(mes: string) {
  return new Date(mes + '-15T12:00:00').toLocaleDateString('pt-BR', { month: 'long', year: 'numeric' })
}

const mes = ref('') // definido no cliente (onMounted), no fuso do navegador
const dados = ref<Fechamento | null>(null)
const loading = ref(false)
const erro = ref('')

const lojaSel = computed({
  get: () => props.loja,
  set: (v: string) => emit('update:loja', v),
})
const sitLabel = (k: string) => SITUACOES.find((s) => s.key === k)?.label || k
const somaResumo = computed(() => (dados.value?.fatura.resumo_cobrancas || []).reduce((s, c) => s + (c.valor || 0), 0))
// O ML manda as bonificações como valor negativo (abatem a fatura); aqui vão em módulo.
const somaBonus = computed(() => (dados.value?.fatura.resumo_bonificacoes || []).reduce((s, c) => s + Math.abs(c.valor || 0), 0))
const provaCaixa = computed(() => {
  const c = dados.value?.caixa
  if (!c || c.saldo_inicial === null) return null
  return Math.round(((c.saldo_inicial || 0) + c.movimento_liquido) * 100) / 100
})

async function carregar() {
  if (!props.loja || !mes.value) return
  loading.value = true
  erro.value = ''
  try {
    dados.value = await api<Fechamento>('/api/conciliacao/fechamento', { query: { loja: props.loja, mes: mes.value } })
  } catch (e: any) {
    erro.value = e?.data?.detail?.code || e?.data?.detail || e?.message || 'erro ao carregar'
  } finally {
    loading.value = false
  }
}

async function exportar() {
  if (!props.loja || !mes.value) return
  exportando.value = true
  try {
    const blob = await api<Blob>('/api/conciliacao/fechamento.xlsx', { method: 'POST', query: { loja: props.loja, mes: mes.value }, responseType: 'blob' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `fechamento_${props.loja}_${mes.value}.xlsx`
    a.click()
    URL.revokeObjectURL(url)
  } catch {
    toasts.error?.('não consegui gerar o Excel')
  } finally {
    exportando.value = false
  }
}

function bateCls(b: boolean | null) {
  if (b === true) return 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300'
  if (b === false) return 'bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300'
  return 'bg-slate-200 text-slate-700 dark:bg-slate-700 dark:text-slate-200'
}
function bateTexto(b: boolean | null, ok = 'bate', ruim = 'não bate', nada = 'sem dados') {
  return b === true ? `✔ ${ok}` : b === false ? `✖ ${ruim}` : nada
}

defineExpose({ carregar })
watch([() => props.loja, mes], () => carregar())
onMounted(() => {
  mes.value = mesLocal(new Date()) // dispara o watch → carregar()
})
</script>

<template>
  <div class="space-y-3">
    <div class="flex flex-wrap items-center gap-2 text-xs">
      <label class="inline-flex items-center gap-1 rounded-md border bg-card px-2 py-1">
        <span class="font-semibold text-muted-foreground">Loja</span>
        <select v-model="lojaSel" class="bg-transparent outline-none">
          <option v-for="l in lojas" :key="l.conta" :value="l.conta">{{ l.conta }}</option>
        </select>
      </label>
      <div class="inline-flex items-center gap-1 rounded-md border bg-card px-1 py-0.5">
        <span class="px-1 font-semibold text-muted-foreground">Mês</span>
        <button type="button" class="rounded px-1 py-0.5 hover:bg-muted" title="mês anterior" @click="mes = somaMes(mes, -1)"><ChevronLeft class="size-3.5" /></button>
        <input v-model="mes" type="month" class="bg-transparent outline-none" />
        <button type="button" class="rounded px-1 py-0.5 hover:bg-muted" title="mês seguinte" @click="mes = somaMes(mes, 1)"><ChevronRight class="size-3.5" /></button>
        <button type="button" class="rounded px-1.5 py-0.5 text-muted-foreground underline" @click="mes = mesLocal(new Date())">este mês</button>
      </div>
      <span v-if="dados" class="text-muted-foreground">{{ fmtDia(dados.de + 'T12:00:00') }} → {{ fmtDia(dados.ate + 'T12:00:00') }} · {{ dados.dias_fechados }} de {{ dados.dias.length }} dia(s) com movimento fechados</span>
      <button type="button" class="ml-auto inline-flex items-center gap-1 rounded-md border px-2.5 py-1.5 font-semibold hover:bg-muted" :disabled="loading" @click="carregar()">
        <RefreshCw class="size-3.5" :class="loading && 'animate-spin'" /> Recarregar
      </button>
      <button type="button" class="inline-flex items-center gap-1 rounded-md border px-2.5 py-1.5 font-semibold hover:bg-muted" :disabled="exportando || !dados" @click="exportar()">
        <Download class="size-3.5" /> Excel
      </button>
    </div>

    <div v-if="erro" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-700">{{ erro }}</div>
    <div v-else-if="loading && !dados" class="py-10 text-center text-xs text-muted-foreground"><Loader2 class="mr-1 inline size-4 animate-spin" /> carregando…</div>

    <template v-if="dados">
      <div class="grid gap-3 xl:grid-cols-3">
        <!-- Vendas -->
        <div class="flex min-w-0 flex-col rounded-md border bg-card">
          <div class="rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-xs font-semibold text-white">
            Vendas de {{ nomeMes(dados.mes) }} <span class="font-normal text-slate-300">· pela data do pedido</span>
          </div>
          <table class="w-full text-[11px]">
            <tbody>
              <tr class="border-b"><td class="px-3 py-1 text-muted-foreground">Pedidos</td><td class="px-3 py-1 text-right tabular-nums font-bold">{{ dados.vendas.pedidos }}</td></tr>
              <tr class="border-b"><td class="px-3 py-1 text-muted-foreground">Vendido (preço da venda)</td><td class="px-3 py-1 text-right tabular-nums">{{ fmtMoney(dados.vendas.vendido) }}</td></tr>
              <tr class="border-b"><td class="px-3 py-1 text-muted-foreground">Tarifa de venda</td><td class="px-3 py-1 text-right tabular-nums text-red-600">− {{ fmtMoney(dados.vendas.tarifa) }}</td></tr>
              <tr class="border-b"><td class="px-3 py-1 text-muted-foreground">Frete (parte nossa)</td><td class="px-3 py-1 text-right tabular-nums text-red-600">− {{ fmtMoney(dados.vendas.frete) }}</td></tr>
              <tr class="border-b"><td class="px-3 py-1 text-muted-foreground">Parcelamento (fatura)</td><td class="px-3 py-1 text-right tabular-nums text-red-600">− {{ fmtMoney(dados.vendas.parcelamento) }}</td></tr>
              <tr class="border-b"><td class="px-3 py-1 text-muted-foreground">Cupons</td><td class="px-3 py-1 text-right tabular-nums text-red-600">− {{ fmtMoney(dados.vendas.cupom) }}</td></tr>
              <tr class="border-b bg-muted/40"><td class="px-3 py-1 font-bold">Líquido previsto</td><td class="px-3 py-1 text-right tabular-nums font-bold">{{ fmtMoney(dados.vendas.previsto) }}</td></tr>
              <tr class="border-b"><td class="px-3 py-1 text-muted-foreground">Recebido ({{ dados.vendas.liberados }} liberados)</td><td class="px-3 py-1 text-right tabular-nums text-emerald-700">{{ fmtMoney(dados.vendas.recebido) }}</td></tr>
              <tr v-if="dados.vendas.liberados_cancelados" class="border-b"><td class="px-3 py-1 text-muted-foreground" colspan="2">{{ dados.vendas.liberados_cancelados }} cancelado(s) por reclamação: o ML liberou e tirou no mesmo dia, não contam como liberados</td></tr>
              <tr class="border-b"><td class="px-3 py-1 text-muted-foreground">Saiu depois (mediação, estorno)</td><td class="px-3 py-1 text-right tabular-nums text-red-600">− {{ fmtMoney(dados.vendas.estornos) }}</td></tr>
              <tr class="border-b"><td class="px-3 py-1 text-muted-foreground">Custo dos produtos</td><td class="px-3 py-1 text-right tabular-nums text-red-600">− {{ fmtMoney(dados.vendas.custo) }}</td></tr>
              <tr class="border-b"><td class="px-3 py-1 text-muted-foreground">Reembolsos</td><td class="px-3 py-1 text-right tabular-nums text-red-600">− {{ fmtMoney(dados.vendas.reembolso) }}</td></tr>
              <tr class="border-b bg-muted/40"><td class="px-3 py-1 font-bold">Sobrou <span class="font-normal text-muted-foreground">(só com dinheiro que entrou)</span></td><td class="px-3 py-1 text-right tabular-nums font-bold" :class="sinal(dados.vendas.sobrou, { pos: 'text-emerald-700', neg: 'text-red-600' })">{{ fmtMoney(dados.vendas.sobrou) }}</td></tr>
              <tr class="border-b"><td class="px-3 py-1 text-muted-foreground">Margem sobre o custo</td><td class="px-3 py-1 text-right tabular-nums font-bold">{{ fmtPct(dados.vendas.margem) }}</td></tr>
              <tr><td class="px-3 py-1 text-muted-foreground">Finalizados</td><td class="px-3 py-1 text-right tabular-nums">{{ dados.vendas.finalizados }} de {{ dados.vendas.pedidos }}</td></tr>
            </tbody>
          </table>
          <div class="mt-auto flex flex-wrap gap-1 border-t px-3 py-1.5 text-[11px]">
            <span v-for="(n, k) in dados.vendas.por_situacao" :key="k" class="rounded bg-muted px-1.5 py-0.5">{{ n }} {{ sitLabel(String(k)).toLowerCase() }}</span>
            <span v-if="!Object.keys(dados.vendas.por_situacao).length" class="text-muted-foreground">nenhum pedido no mês</span>
          </div>
        </div>

        <!-- Caixa -->
        <div class="flex min-w-0 flex-col rounded-md border bg-card">
          <div class="flex items-center justify-between rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-xs font-semibold text-white">
            <span>Caixa do mês <span class="font-normal text-slate-300">· pela data do movimento ({{ dados.caixa.linhas }} linhas)</span></span>
            <span class="rounded px-1.5 py-0.5 text-[11px]" :class="bateCls(dados.caixa.bate)">{{ bateTexto(dados.caixa.bate, 'prova dos nove bate', 'prova dos nove não bate', 'sem extrato') }}</span>
          </div>
          <table class="w-full text-[11px]">
            <tbody>
              <tr class="border-b"><td class="px-3 py-1 text-muted-foreground">Saldo inicial<span v-if="dados.dias.length"> ({{ fmtDia(dados.dias[0].dia + 'T12:00:00') }})</span></td><td class="px-3 py-1 text-right tabular-nums font-bold">{{ fmtMoney(dados.caixa.saldo_inicial) }}</td></tr>
              <tr class="border-b"><td class="px-3 py-1 text-muted-foreground">Vendas liberadas</td><td class="px-3 py-1 text-right tabular-nums text-emerald-700">{{ fmtMoney(dados.caixa.liberado) }}</td></tr>
              <tr class="border-b"><td class="px-3 py-1 text-muted-foreground">Mediações (reclamação perdida)</td><td class="px-3 py-1 text-right tabular-nums" :class="sinal(dados.caixa.mediacoes, { pos: 'text-emerald-700', neg: 'text-red-600' })">{{ fmtMoney(dados.caixa.mediacoes) }}</td></tr>
              <tr v-if="dados.caixa.mediacoes_canceladas" class="border-b"><td class="px-3 py-1 text-muted-foreground">Mediações canceladas</td><td class="px-3 py-1 text-right tabular-nums" :class="sinal(dados.caixa.mediacoes_canceladas, { pos: 'text-emerald-700', neg: 'text-red-600' })">{{ fmtMoney(dados.caixa.mediacoes_canceladas) }}</td></tr>
              <tr class="border-b"><td class="px-3 py-1 text-muted-foreground">Estornos</td><td class="px-3 py-1 text-right tabular-nums" :class="sinal(dados.caixa.estornos, { pos: 'text-emerald-700', neg: 'text-red-600' })">{{ fmtMoney(dados.caixa.estornos) }}</td></tr>
              <tr class="border-b"><td class="px-3 py-1 text-muted-foreground">Chargebacks</td><td class="px-3 py-1 text-right tabular-nums" :class="sinal(dados.caixa.chargebacks, { pos: 'text-emerald-700', neg: 'text-red-600' })">{{ fmtMoney(dados.caixa.chargebacks) }}</td></tr>
              <tr class="border-b"><td class="px-3 py-1 text-muted-foreground">Frete (cobrado / devolvido)</td><td class="px-3 py-1 text-right tabular-nums" :class="sinal(dados.caixa.frete, { pos: 'text-emerald-700', neg: 'text-red-600' })">{{ fmtMoney(dados.caixa.frete) }}</td></tr>
              <tr v-if="dados.caixa.cancelados_linhas" class="border-b"><td class="px-3 py-1 text-muted-foreground">Cancelados por reclamação ({{ dados.caixa.cancelados_linhas }} linhas): entrou {{ fmtMoney(dados.caixa.cancelados_entrou) }}, saiu {{ fmtMoney(dados.caixa.cancelados_saiu) }}</td><td class="px-3 py-1 text-right tabular-nums" :class="sinal(dados.caixa.cancelados_liquido, { pos: 'text-emerald-700', neg: 'text-red-600' })">{{ fmtMoney(dados.caixa.cancelados_liquido) }}</td></tr>
              <tr class="border-b"><td class="px-3 py-1 text-muted-foreground">Outros (crédito, antecipação…)</td><td class="px-3 py-1 text-right tabular-nums" :class="sinal(dados.caixa.outros, { pos: 'text-emerald-700', neg: 'text-red-600' })">{{ fmtMoney(dados.caixa.outros) }}</td></tr>
              <tr class="border-b"><td class="px-3 py-1 text-muted-foreground">Ajustes de meses anteriores ({{ dados.caixa.ajustes_qtd }} linhas)</td><td class="px-3 py-1 text-right tabular-nums" :class="sinal(dados.caixa.ajustes_meses_anteriores, { pos: 'text-emerald-700', neg: 'text-red-600' })">{{ fmtMoney(dados.caixa.ajustes_meses_anteriores) }}</td></tr>
              <tr class="border-b bg-muted/40"><td class="px-3 py-1 font-bold">= Resultado de caixa do mês</td><td class="px-3 py-1 text-right tabular-nums font-bold" :class="sinal(dados.caixa.resultado_caixa, { pos: 'text-emerald-700', neg: 'text-red-600' })">{{ fmtMoney(dados.caixa.resultado_caixa) }}</td></tr>
              <tr class="border-b"><td class="px-3 py-1 text-muted-foreground">Reservas (líquido)</td><td class="px-3 py-1 text-right tabular-nums">{{ fmtMoney(dados.caixa.reservas_liquido) }}</td></tr>
              <tr class="border-b"><td class="px-3 py-1 text-muted-foreground">Saques</td><td class="px-3 py-1 text-right tabular-nums text-red-600">{{ fmtMoney(dados.caixa.saques) }}</td></tr>
              <tr class="border-b bg-muted/40"><td class="px-3 py-1 font-bold">= Movimento líquido</td><td class="px-3 py-1 text-right tabular-nums font-bold">{{ fmtMoney(dados.caixa.movimento_liquido) }}</td></tr>
              <tr class="border-b"><td class="px-3 py-1 text-muted-foreground">Saldo inicial + movimento</td><td class="px-3 py-1 text-right tabular-nums">{{ fmtMoney(provaCaixa) }}</td></tr>
              <tr><td class="px-3 py-1 text-muted-foreground">Saldo final informado<span v-if="dados.dias.length"> ({{ fmtDia(dados.dias[dados.dias.length - 1].dia + 'T12:00:00') }})</span></td><td class="px-3 py-1 text-right tabular-nums font-bold">{{ fmtMoney(dados.caixa.saldo_final) }}</td></tr>
            </tbody>
          </table>
          <div class="mt-auto border-t px-3 py-1.5 text-[11px] text-muted-foreground">Saques e reservas não são resultado: só mudam de lugar o dinheiro. "Ajustes de meses anteriores" = movimento deste mês de pedido vendido antes do dia 1º (ou anterior ao início da conciliação).</div>
        </div>

        <!-- Fatura ML -->
        <div class="flex min-w-0 flex-col rounded-md border bg-card">
          <div class="flex items-center justify-between rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-xs font-semibold text-white">
            <span>Fatura do Mercado Livre <span class="font-normal text-slate-300">· período {{ dados.fatura.periodo.chave.slice(0, 7) }}</span></span>
            <span class="rounded px-1.5 py-0.5 text-[11px]" :class="bateCls(dados.fatura.bate)">{{ bateTexto(dados.fatura.bate, 'resumo = detalhes', 'resumo ≠ detalhes', 'resumo não lido') }}</span>
          </div>
          <div class="px-3 py-1.5 text-[11px] text-muted-foreground">
            <template v-if="dados.fatura.periodo.de">de {{ fmtDia(dados.fatura.periodo.de + 'T12:00:00') }} a {{ fmtDia(dados.fatura.periodo.ate + 'T12:00:00') }}<span v-if="dados.fatura.periodo.expira"> · vence {{ fmtDia(dados.fatura.periodo.expira + 'T12:00:00') }}</span> · lido {{ fmtDiaHora(dados.fatura.lido_em) }}</template>
            <template v-else>o resumo deste período ainda não foi lido (a fatura é lida de madrugada; "Sincronizar agora" lê na hora)</template>
          </div>
          <table class="w-full text-[11px]">
            <tbody>
              <tr class="bg-muted/40"><td class="px-3 py-1 font-bold" colspan="2">O que o ML diz (resumo)</td></tr>
              <tr v-for="c in dados.fatura.resumo_cobrancas" :key="'c' + c.tipo" class="border-b"><td class="px-3 py-1 text-muted-foreground">{{ c.label || c.tipo }} <span class="text-[10px]">{{ c.tipo }}</span></td><td class="px-3 py-1 text-right tabular-nums text-red-600">− {{ fmtMoney(c.valor) }}</td></tr>
              <tr v-for="c in dados.fatura.resumo_bonificacoes" :key="'b' + c.tipo" class="border-b"><td class="px-3 py-1 text-muted-foreground">{{ c.label || c.tipo }} <span class="text-[10px]">{{ c.tipo }}</span></td><td class="px-3 py-1 text-right tabular-nums text-emerald-700">{{ fmtMoney(Math.abs(c.valor || 0)) }}</td></tr>
              <tr class="border-b"><td class="px-3 py-1 font-bold">Total da fatura (resumo, já com as bonificações)</td><td class="px-3 py-1 text-right tabular-nums font-bold">{{ fmtMoney(dados.fatura.resumo_total) }}</td></tr>
              <tr v-if="dados.fatura.pago && dados.fatura.pago.total_collected !== undefined" class="border-b"><td class="px-3 py-1 text-muted-foreground">Já cobrado (descontado das vendas + pago) / em aberto</td><td class="px-3 py-1 text-right tabular-nums">{{ fmtMoney(Math.abs(dados.fatura.pago.total_collected || 0)) }} / {{ fmtMoney(Math.abs(dados.fatura.pago.total_debt || 0)) }}</td></tr>
              <tr class="bg-muted/40"><td class="px-3 py-1 font-bold" colspan="2">O que lemos linha a linha (detalhes)</td></tr>
              <tr v-for="d in dados.fatura.detalhes_por_sub_tipo" :key="String(d.sub_tipo) + d.tipo" class="border-b"><td class="px-3 py-1 text-muted-foreground">{{ tipoMov(d.tipo) }} <span class="text-[10px]">{{ d.sub_tipo }} · {{ d.linhas }} linhas</span></td><td class="px-3 py-1 text-right tabular-nums" :class="(d.bonificado || 0) > 0 ? 'text-emerald-700' : 'text-red-600'">{{ (d.bonificado || 0) > 0 ? fmtMoney(d.bonificado) : '− ' + fmtMoney(d.cobrado) }}</td></tr>
              <tr v-if="!dados.fatura.detalhes_por_sub_tipo.length"><td class="px-3 py-2 text-muted-foreground" colspan="2">nenhum detalhe lido neste período</td></tr>
              <tr class="border-t"><td class="px-3 py-1 font-bold">Cobranças: resumo × detalhes</td><td class="px-3 py-1 text-right tabular-nums font-bold">{{ fmtMoney(somaResumo) }} × {{ fmtMoney(dados.fatura.detalhes_cobrancas) }}</td></tr>
              <tr class="border-b"><td class="px-3 py-1 font-bold">Bonificações: resumo × detalhes desta fatura <span class="rounded px-1 text-[10px] font-semibold" :class="bateCls(dados.fatura.bate_bonificacoes)">{{ bateTexto(dados.fatura.bate_bonificacoes, 'bate', 'não bate', '—') }}</span></td><td class="px-3 py-1 text-right tabular-nums font-bold">{{ fmtMoney(somaBonus) }} × {{ fmtMoney(dados.fatura.bonificacoes_mesma_fatura) }}</td></tr>
              <tr><td class="px-3 py-1 text-muted-foreground">+ bonificações devolvendo cobrança de fatura anterior ({{ dados.fatura.bonificacoes_anteriores_linhas }} linhas): crédito real, fora do resumo do ML</td><td class="px-3 py-1 text-right tabular-nums text-emerald-700">{{ fmtMoney(dados.fatura.bonificacoes_de_faturas_anteriores) }}</td></tr>
            </tbody>
          </table>
          <div class="mt-auto border-t px-3 py-1.5 text-[11px] text-muted-foreground">O período da fatura do ML não é o mês do calendário (nesta loja vai do dia 23 ao dia 22). Aqui aparece o período cuja chave é este mês. O resumo do ML só conta a bonificação que devolve cobrança da própria fatura; a que devolve cobrança de fatura anterior aparece à parte.</div>
        </div>
      </div>

      <div class="rounded-md border bg-card">
        <div class="flex items-center justify-between rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-xs font-semibold text-white">
          <span>Dias do mês com movimento no extrato</span>
          <span class="text-[11px] font-normal text-slate-300">clique no dia abre o extrato dele</span>
        </div>
        <div class="overflow-auto">
          <table class="w-full border-collapse text-[11px]">
            <thead class="bg-muted text-[10px] uppercase tracking-wide text-muted-foreground">
              <tr>
                <th class="border-b px-2 py-1 text-left">Dia</th>
                <th class="border-b px-2 py-1 text-right">Linhas</th>
                <th class="border-b px-2 py-1 text-right">Sem dono</th>
                <th class="border-b px-2 py-1 text-right">Créditos</th>
                <th class="border-b px-2 py-1 text-right">Débitos</th>
                <th class="border-b px-2 py-1 text-left">Fechado</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="d in dados.dias" :key="d.dia" class="cursor-pointer border-b hover:bg-muted/40" @click="emit('abrir-extrato', dados.loja, d.dia)">
                <td class="px-2 py-1 font-bold tabular-nums">{{ fmtDia(d.dia + 'T12:00:00') }}</td>
                <td class="px-2 py-1 text-right tabular-nums">{{ d.linhas }}</td>
                <td class="px-2 py-1 text-right tabular-nums font-bold" :class="d.pendentes ? 'text-red-600' : ''">{{ d.pendentes }}</td>
                <td class="px-2 py-1 text-right tabular-nums text-emerald-700">{{ fmtMoney(d.creditos) }}</td>
                <td class="px-2 py-1 text-right tabular-nums text-red-600">{{ fmtMoney(d.debitos) }}</td>
                <td class="px-2 py-1">
                  <span v-if="d.fechado_em" class="rounded bg-emerald-100 px-1.5 py-0.5 font-semibold text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300">fechado {{ fmtDiaHora(d.fechado_em) }} · {{ d.fechado_por }}</span>
                  <span v-else-if="d.pendentes" class="rounded bg-red-100 px-1.5 py-0.5 font-semibold text-red-800 dark:bg-red-900/40 dark:text-red-300">não fechou · {{ d.pendentes }} sem dono</span>
                  <span v-else class="rounded bg-amber-100 px-1.5 py-0.5 font-semibold text-amber-800 dark:bg-amber-900/40 dark:text-amber-300">pronto, falta fechar</span>
                </td>
              </tr>
              <tr v-if="!dados.dias.length"><td colspan="6" class="py-6 text-center text-muted-foreground">nenhum movimento lido neste mês</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>
  </div>
</template>
