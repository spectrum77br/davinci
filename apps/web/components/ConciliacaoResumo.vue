<script setup lang="ts">
// Conciliação › Resumo (passo 2d, 14/10/2026). A tela de entrada: uma linha por
// loja com o dia de ontem (fechou? linha sem dono? saldo bate?), as pendências
// dos pedidos e a lista "Onde agir hoje" — a mesma que o vigia vai mandar no
// Threema. O rodapé é o mês até ontem, por marketplace e da empresa inteira.
import { ChevronLeft, ChevronRight, Loader2, RefreshCw } from 'lucide-vue-next'
import { fmtDia, fmtDiaHora, fmtHora, fmtMoney, fmtPct, ontemISO, sinal, somaDias } from '~/lib/conciliacao'

const props = defineProps<{ canEdit: boolean }>()
const emit = defineEmits<{
  (e: 'abrir-extrato', loja: string, dia: string): void
  (e: 'ver-pedidos', loja: string, filtro: Record<string, string>): void
  (e: 'sincronizar', loja: string): void
}>()

type DiaLoja = {
  status: 'fechado' | 'pronto' | 'aberto' | 'extrato_nao_lido' | 'sem_movimento'
  texto: string
  linhas: number
  pendentes: number
  divergentes: number
  saldo_bate: boolean | null
  diferenca: number | null
  fechado_em: string | null
  fechado_por: string | null
}
type PedidosLoja = {
  pendencia: number
  reabertos: number
  aguardando: number
  finalizados: number
  conciliados: number
  vencidos: number
  recebido_a_menos: number
  estornados: number
}
type Leituras = { extrato_em: string | null; fatura_em: string | null; recalculo_em: string | null; extrato_atrasado: boolean; erro: string | null }
type Mes = {
  lojas?: number
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
  finalizados: number
  ajustes_meses_anteriores: number
  ajustes_qtd: number
}
type Dinheiro = { na_conta: number | null; saldo_em: string | null; a_liberar: number; a_liberar_pedidos: number; reservado: number | null }
type LojaResumo = {
  conta: string
  platform: string
  inicio: string
  dinheiro: Dinheiro
  dia: DiaLoja
  pedidos: PedidosLoja
  leituras: Leituras
  mes: Mes
  situacao: string
  gravidade: 'ok' | 'warn' | 'bad'
}
type Agir = {
  gravidade: 'ok' | 'warn' | 'bad'
  loja: string
  titulo: string
  detalhe: string
  acao: 'extrato' | 'pedidos' | 'sincronizar'
  dia?: string
  filtro?: Record<string, string>
}
type Resumo = {
  dia: string
  mes: string
  lojas: LojaResumo[]
  agir: Agir[]
  por_plataforma: Record<string, Mes & { dinheiro?: Dinheiro }>
  empresa: Mes & { dinheiro?: Dinheiro }
  cartoes: Record<string, number>
}

const PLATAFORMAS: Array<{ key: string; label: string }> = [
  { key: 'ml', label: 'Mercado Livre' },
  { key: 'shopee', label: 'Shopee' },
  { key: 'amazon', label: 'Amazon' },
  { key: 'tiktok', label: 'TikTok' },
  { key: 'magalu', label: 'Magalu' },
  { key: 'shein', label: 'Shein' },
  { key: 'temu', label: 'Temu' },
]
const FASE_SEGUINTE = ['Shopee', 'Amazon', 'TikTok']
const MOSTRAR_MES = false // rodapé do mês desligado em 15/10 (volta quando ele pedir)
// Bloco "Dinheiro nas plataformas": cada marketplace começa fechado (só o total); "mostrar" abre as lojas.
const abertos = ref<Record<string, boolean>>({})
function alternar(key: string) {
  abertos.value = { ...abertos.value, [key]: !abertos.value[key] }
}

const { api } = useApi()
// O dia é definido no cliente (onMounted): no servidor o relógio é UTC e perto da
// meia-noite "ontem" seria outro dia — e o HTML do servidor não bateria com o do navegador.
const dia = ref('')
const dados = ref<Resumo | null>(null)
const loading = ref(false)
const erro = ref('')

function nomePlataforma(key: string) {
  return PLATAFORMAS.find((p) => p.key === key)?.label || key
}
const grupos = computed(() => {
  const lojas = dados.value?.lojas || []
  const ordem = PLATAFORMAS.map((p) => p.key)
  const chaves = Array.from(new Set(lojas.map((l) => l.platform))).sort((a, b) => ordem.indexOf(a) - ordem.indexOf(b))
  return chaves.map((key) => ({ key, label: nomePlataforma(key), lojas: lojas.filter((l) => l.platform === key) }))
})
const faseSeguinte = computed(() => {
  const ativas = new Set((dados.value?.lojas || []).map((l) => nomePlataforma(l.platform)))
  return FASE_SEGUINTE.filter((n) => !ativas.has(n))
})
const lojasComPendente = computed(() => (dados.value?.lojas || []).filter((l) => l.dia.pendentes > 0).length)
const plataformasDoMes = computed(() => {
  const pp = dados.value?.por_plataforma || {}
  return Object.keys(pp)
    .sort((a, b) => PLATAFORMAS.findIndex((p) => p.key === a) - PLATAFORMAS.findIndex((p) => p.key === b))
    .map((key) => ({ key, label: nomePlataforma(key), mes: pp[key] }))
})
function nomeMes(mes: string) {
  const d = new Date(mes + '-15T12:00:00')
  return d.toLocaleDateString('pt-BR', { month: 'long' })
}

async function carregar() {
  if (!dia.value) return
  loading.value = true
  erro.value = ''
  try {
    dados.value = await api<Resumo>('/api/conciliacao/resumo', { query: { dia: dia.value } })
  } catch (e: any) {
    erro.value = e?.data?.detail?.code || e?.message || 'erro ao carregar'
  } finally {
    loading.value = false
  }
}

function agir(a: Agir) {
  if (a.acao === 'extrato') emit('abrir-extrato', a.loja, a.dia || dia.value)
  else if (a.acao === 'pedidos') emit('ver-pedidos', a.loja, a.filtro || {})
  else if (a.acao === 'sincronizar') {
    if (props.canEdit) emit('sincronizar', a.loja)
    else emit('abrir-extrato', a.loja, a.dia || dia.value)
  }
}
function rotuloAcao(a: Agir) {
  if (a.acao === 'extrato') return 'Abrir extrato'
  if (a.acao === 'pedidos') return 'Ver pedidos'
  return props.canEdit ? 'Sincronizar' : 'Abrir extrato'
}

function diaCls(d: DiaLoja) {
  if (d.status === 'fechado') return 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300'
  if (d.status === 'pronto') return 'bg-emerald-50 text-emerald-700 ring-1 ring-emerald-300 dark:bg-emerald-950/30 dark:text-emerald-300'
  if (d.status === 'aberto') return 'bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300'
  if (d.status === 'extrato_nao_lido') return 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300'
  return 'bg-slate-200 text-slate-700 dark:bg-slate-700 dark:text-slate-200'
}
function diaTexto(d: DiaLoja) {
  if (d.status === 'fechado') return `fechado ${fmtHora(d.fechado_em)} · ${d.fechado_por || ''}`
  if (d.status === 'pronto') return 'pronto pra fechar'
  if (d.status === 'aberto') return 'não fechou'
  if (d.status === 'extrato_nao_lido') return 'extrato não lido'
  return 'sem movimento'
}
function gravCls(g: string) {
  if (g === 'bad') return 'bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300'
  if (g === 'warn') return 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300'
  return 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300'
}
function agirCls(g: string) {
  if (g === 'bad') return 'border-red-300 bg-red-50 dark:border-red-900 dark:bg-red-950/20'
  if (g === 'warn') return 'border-amber-300 bg-amber-50 dark:border-amber-900 dark:bg-amber-950/20'
  return 'border-emerald-300 bg-emerald-50 dark:border-emerald-900 dark:bg-emerald-950/20'
}
function pctFinal(m: Mes) {
  return m.pedidos ? Math.round((m.finalizados / m.pedidos) * 100) : 0
}

defineExpose({ carregar })
watch(dia, () => carregar())
// Sem `await` no setup: componente síncrono. Com top-level await, trocar de uma
// visão assíncrona pra outra (Resumo → Fechamento) quebrava o patch do DOM
// (NotFoundError insertBefore) por não ter <Suspense> próprio.
onMounted(() => {
  dia.value = ontemISO() // dispara o watch → carregar()
})
</script>

<template>
  <div class="space-y-3">
    <div class="flex flex-wrap items-center gap-2 text-xs">
      <div class="inline-flex items-center gap-1 rounded-md border bg-card px-1 py-0.5">
        <span class="px-1 font-semibold text-muted-foreground">Dia</span>
        <button type="button" class="rounded px-1 py-0.5 hover:bg-muted" title="dia anterior" @click="dia = somaDias(dia, -1)"><ChevronLeft class="size-3.5" /></button>
        <input v-model="dia" type="date" class="bg-transparent outline-none" />
        <button type="button" class="rounded px-1 py-0.5 hover:bg-muted" title="dia seguinte" @click="dia = somaDias(dia, 1)"><ChevronRight class="size-3.5" /></button>
        <button type="button" class="rounded px-1.5 py-0.5 text-muted-foreground underline" @click="dia = ontemISO()">ontem</button>
      </div>
      <span v-if="dados" class="text-muted-foreground">{{ dados.lojas.length }} loja(s) na conciliação</span>
      <button type="button" class="ml-auto inline-flex items-center gap-1 rounded-md border px-2.5 py-1.5 font-semibold hover:bg-muted" :disabled="loading" @click="carregar()">
        <RefreshCw class="size-3.5" :class="loading && 'animate-spin'" /> Recarregar
      </button>
    </div>

    <div v-if="erro" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-700">{{ erro }}</div>
    <div v-else-if="loading && !dados" class="py-10 text-center text-xs text-muted-foreground"><Loader2 class="mr-1 inline size-4 animate-spin" /> carregando…</div>

    <template v-if="dados">
      <div class="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-6">
        <div class="rounded-md border bg-card px-3 py-2">
          <div class="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Dia de {{ fmtDia(dados.dia + 'T12:00:00') }} fechado</div>
          <div class="text-lg font-bold" :class="dados.cartoes.dias_fechados === dados.cartoes.lojas ? 'text-emerald-700' : 'text-amber-700'">{{ dados.cartoes.dias_fechados }} de {{ dados.cartoes.lojas }} lojas</div>
        </div>
        <div class="rounded-md border bg-card px-3 py-2">
          <div class="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Movimentos sem dono</div>
          <div class="text-lg font-bold" :class="dados.cartoes.pendentes ? 'text-red-600' : 'text-emerald-700'">{{ dados.cartoes.pendentes }} <span v-if="dados.cartoes.pendentes" class="text-xs font-normal text-muted-foreground">em {{ lojasComPendente }} loja(s)</span></div>
        </div>
        <div class="rounded-md border bg-card px-3 py-2">
          <div class="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Pedidos reabertos</div>
          <div class="text-lg font-bold" :class="dados.cartoes.reabertos ? 'text-violet-700' : 'text-emerald-700'">{{ dados.cartoes.reabertos }}</div>
        </div>
        <div class="rounded-md border bg-card px-3 py-2">
          <div class="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Não liberados (vencidos)</div>
          <div class="text-lg font-bold" :class="dados.cartoes.vencidos ? 'text-amber-700' : 'text-emerald-700'">{{ dados.cartoes.vencidos }}</div>
        </div>
        <div class="rounded-md border bg-card px-3 py-2">
          <div class="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Extrato atrasado</div>
          <div class="text-lg font-bold" :class="dados.cartoes.extrato_atrasado ? 'text-red-600' : 'text-emerald-700'">{{ dados.cartoes.extrato_atrasado }} loja(s)</div>
        </div>
        <div class="rounded-md border bg-card px-3 py-2">
          <div class="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Saldo bate</div>
          <div class="text-lg font-bold" :class="dados.cartoes.saldo_bate === dados.cartoes.com_movimento ? 'text-emerald-700' : 'text-red-600'">{{ dados.cartoes.saldo_bate }} de {{ dados.cartoes.com_movimento }}</div>
        </div>
      </div>

      <div class="grid gap-3 xl:grid-cols-[1fr_400px]">
        <div class="flex min-w-0 flex-col rounded-md border bg-card">
          <div class="flex items-center justify-between rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-xs font-semibold text-white">
            <span>Lojas — situação de {{ fmtDia(dados.dia + 'T12:00:00') }} e pendências abertas</span>
            <span class="text-[11px] font-normal text-slate-300">clique na loja abre o extrato do dia dela</span>
          </div>
          <div class="overflow-auto">
            <table class="w-full border-collapse text-[11px]">
              <thead class="bg-muted text-[10px] uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th class="border-b px-2 py-1 text-left">Loja</th>
                  <th class="whitespace-nowrap border-b px-2 py-1 text-left">Dia</th>
                  <th class="border-b px-2 py-1 text-right">Movimentos</th>
                  <th class="border-b px-2 py-1 text-right">Sem dono</th>
                  <th class="border-b px-2 py-1 text-right">Pedidos c/ pendência</th>
                  <th class="border-b px-2 py-1 text-right">Reabertos</th>
                  <th class="border-b px-2 py-1 text-right">Não liberados</th>
                  <th class="border-b px-2 py-1 text-left">Saldo</th>
                  <th class="whitespace-nowrap border-b px-2 py-1 text-left">Extrato lido</th>
                  <th class="whitespace-nowrap border-b px-2 py-1 text-left">Fatura ML</th>
                  <th class="border-b px-2 py-1 text-left">Situação</th>
                </tr>
              </thead>
              <tbody>
                <template v-for="g in grupos" :key="g.key">
                  <tr class="bg-muted/60"><td colspan="11" class="border-b px-2 py-1 font-bold">{{ g.label }}</td></tr>
                  <tr v-for="l in g.lojas" :key="l.conta" class="cursor-pointer border-b hover:bg-muted/40" @click="emit('abrir-extrato', l.conta, dados.dia)">
                    <td class="px-2 py-1 font-bold">{{ l.conta }}</td>
                    <td class="whitespace-nowrap px-2 py-1"><span class="rounded px-1.5 py-0.5 font-semibold" :class="diaCls(l.dia)">{{ diaTexto(l.dia) }}</span></td>
                    <td class="px-2 py-1 text-right tabular-nums">{{ l.dia.linhas || '—' }}</td>
                    <td class="px-2 py-1 text-right tabular-nums font-bold" :class="l.dia.pendentes ? 'text-red-600' : ''">{{ l.dia.linhas ? l.dia.pendentes : '—' }}</td>
                    <td class="px-2 py-1 text-right tabular-nums font-bold" :class="l.pedidos.pendencia ? 'text-red-600' : ''" @click.stop="emit('ver-pedidos', l.conta, { etapa: 'pendencia' })">{{ l.pedidos.pendencia }}</td>
                    <td class="px-2 py-1 text-right tabular-nums font-bold" :class="l.pedidos.reabertos ? 'text-violet-700' : ''" @click.stop="emit('ver-pedidos', l.conta, { etapa: 'reaberto' })">{{ l.pedidos.reabertos }}</td>
                    <td class="px-2 py-1 text-right tabular-nums font-bold" :class="l.pedidos.vencidos ? 'text-amber-700' : ''" @click.stop="emit('ver-pedidos', l.conta, { situacao: 'vencido' })">{{ l.pedidos.vencidos }}</td>
                    <td class="px-2 py-1">
                      <span v-if="l.dia.saldo_bate === true" class="rounded bg-emerald-100 px-1.5 py-0.5 font-bold text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300">✔ bate</span>
                      <span v-else-if="l.dia.saldo_bate === false" class="rounded bg-red-100 px-1.5 py-0.5 font-bold text-red-800 dark:bg-red-900/40 dark:text-red-300">✖ {{ fmtMoney(l.dia.diferenca) }}</span>
                      <span v-else class="text-muted-foreground">—</span>
                    </td>
                    <td class="whitespace-nowrap px-2 py-1 tabular-nums" :class="l.leituras.extrato_atrasado ? 'font-bold text-red-600' : ''" :title="l.leituras.erro || ''">{{ fmtDiaHora(l.leituras.extrato_em) }}<span v-if="l.leituras.extrato_atrasado"> (atrasado)</span></td>
                    <td class="whitespace-nowrap px-2 py-1 tabular-nums">{{ l.platform === 'ml' ? fmtDiaHora(l.leituras.fatura_em) : 'n/a' }}</td>
                    <td class="px-2 py-1"><span class="inline-block rounded px-1.5 py-0.5 font-semibold" :class="gravCls(l.gravidade)">{{ l.situacao }}</span></td>
                  </tr>
                </template>
                <tr v-if="faseSeguinte.length" class="bg-muted/60"><td colspan="11" class="border-b px-2 py-1 font-bold">{{ faseSeguinte.join(' · ') }}</td></tr>
                <tr v-if="faseSeguinte.length" class="text-muted-foreground"><td class="px-2 py-1" colspan="11">fase seguinte — entra depois do Mercado Livre</td></tr>
                <tr v-if="!dados.lojas.length"><td colspan="11" class="py-8 text-center text-muted-foreground">nenhuma loja ligada na conciliação</td></tr>
              </tbody>
            </table>
          </div>
          <div class="border-t px-3 py-1.5 text-[11px] text-muted-foreground">
            "Dia": <b>fechado</b> = alguém fechou (100% das linhas com destino e saldo batendo) · <b>pronto pra fechar</b> = tudo casou, falta só fechar · <b>não fechou</b> = tem linha sem dono ou saldo diferente. Loja sem pendência some da lista "Onde agir".
          </div>
        </div>

        <div class="flex min-w-0 flex-col rounded-md border bg-card">
          <div class="flex items-center justify-between rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-xs font-semibold text-white">
            <span>Onde agir hoje</span>
            <span class="text-[11px] font-normal text-slate-300">em ordem do que mais dói</span>
          </div>
          <div class="space-y-2 p-2">
            <div v-for="(a, i) in dados.agir" :key="i" class="flex items-start justify-between gap-2 rounded-md border px-2.5 py-2 text-xs" :class="agirCls(a.gravidade)">
              <div class="min-w-0">
                <div class="font-bold">{{ a.titulo }}</div>
                <div class="text-[11px] text-muted-foreground">{{ a.detalhe }}</div>
              </div>
              <button type="button" class="shrink-0 rounded-md bg-primary px-2 py-1 text-[11px] font-semibold text-primary-foreground" @click="agir(a)">{{ rotuloAcao(a) }}</button>
            </div>
            <div v-if="!dados.agir.length" class="rounded-md border border-emerald-300 bg-emerald-50 px-2.5 py-3 text-center text-xs text-emerald-800 dark:bg-emerald-950/20 dark:text-emerald-300">nada pra agir: dia fechado e sem pendência em todas as lojas</div>
          </div>
          <div class="mt-auto border-t px-3 py-1.5 text-[11px] text-muted-foreground">Essa lista é a mesma que o vigia vai mandar no Threema de manhã — quem abre o painel vê o detalhe; quem está no celular vê o resumo.</div>
        </div>
      </div>

      <div class="rounded-md border bg-card">
        <div class="flex items-center justify-between rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-xs font-semibold text-white">
          <span>Dinheiro nas plataformas</span>
          <span class="text-[11px] font-normal text-slate-300">onde o dinheiro está agora: na conta e preso esperando liberação</span>
        </div>
        <div class="overflow-auto">
          <table class="w-full border-collapse text-[11px]">
            <thead class="bg-muted text-[10px] uppercase tracking-wide text-muted-foreground">
              <tr>
                <th class="border-b px-2 py-1 text-left">Marketplace / loja</th>
                <th class="border-b px-2 py-1 text-right">Na conta (disponível)</th>
                <th class="whitespace-nowrap border-b px-2 py-1 text-left">Saldo de quando</th>
                <th class="border-b px-2 py-1 text-right">A liberar</th>
                <th class="whitespace-nowrap border-b px-2 py-1 text-right">Reservado (em disputa)</th>
              </tr>
            </thead>
            <tbody>
              <template v-for="g in grupos" :key="'d' + g.key">
                <tr class="border-b bg-muted/60 font-bold">
                  <td class="px-2 py-1">
                    {{ g.label }} <span class="font-normal text-muted-foreground">({{ g.lojas.length }} loja{{ g.lojas.length === 1 ? '' : 's' }})</span>
                    <button type="button" class="ml-2 font-semibold text-primary underline" @click="alternar(g.key)">{{ abertos[g.key] ? 'esconder' : 'mostrar' }}</button>
                  </td>
                  <td class="px-2 py-1 text-right tabular-nums text-emerald-700">{{ fmtMoney(dados.por_plataforma[g.key]?.dinheiro?.na_conta) }}</td>
                  <td class="px-2 py-1"></td>
                  <td class="px-2 py-1 text-right tabular-nums text-amber-700">{{ fmtMoney(dados.por_plataforma[g.key]?.dinheiro?.a_liberar) }} <span class="font-normal text-muted-foreground">({{ dados.por_plataforma[g.key]?.dinheiro?.a_liberar_pedidos || 0 }} pedidos)</span></td>
                  <td class="px-2 py-1 text-right tabular-nums">{{ g.key === 'ml' ? fmtMoney(dados.por_plataforma[g.key]?.dinheiro?.reservado) : '—' }}</td>
                </tr>
                <tr v-for="l in g.lojas" v-show="abertos[g.key]" :key="'d' + l.conta" class="cursor-pointer border-b hover:bg-muted/40" title="abre os pedidos desta loja aguardando liberação" @click="emit('ver-pedidos', l.conta, { situacao: 'aguardando' })">
                  <td class="px-2 py-1 pl-6 font-bold">{{ l.conta }}</td>
                  <td class="px-2 py-1 text-right tabular-nums text-emerald-700">{{ fmtMoney(l.dinheiro.na_conta) }}</td>
                  <td class="whitespace-nowrap px-2 py-1 text-muted-foreground">{{ fmtDiaHora(l.dinheiro.saldo_em) }}</td>
                  <td class="px-2 py-1 text-right tabular-nums text-amber-700">{{ fmtMoney(l.dinheiro.a_liberar) }} <span class="font-normal text-muted-foreground">({{ l.dinheiro.a_liberar_pedidos }} pedidos)</span></td>
                  <td class="px-2 py-1 text-right tabular-nums">{{ l.dinheiro.reservado == null ? '—' : fmtMoney(l.dinheiro.reservado) }}</td>
                </tr>
              </template>
              <tr v-if="faseSeguinte.length" class="border-b bg-muted/60 font-bold"><td class="px-2 py-1">{{ faseSeguinte.join(' · ') }}</td><td colspan="4" class="px-2 py-1 font-normal text-muted-foreground">fase seguinte</td></tr>
              <tr class="bg-muted/40 font-bold">
                <td class="px-2 py-1">Empresa <span class="font-normal text-muted-foreground">({{ dados.lojas.length }} loja{{ dados.lojas.length === 1 ? '' : 's' }})</span></td>
                <td class="px-2 py-1 text-right tabular-nums text-emerald-700">{{ fmtMoney(dados.empresa.dinheiro?.na_conta) }}</td>
                <td class="px-2 py-1"></td>
                <td class="px-2 py-1 text-right tabular-nums text-amber-700">{{ fmtMoney(dados.empresa.dinheiro?.a_liberar) }} <span class="font-normal text-muted-foreground">({{ dados.empresa.dinheiro?.a_liberar_pedidos || 0 }} pedidos)</span></td>
                <td class="px-2 py-1 text-right tabular-nums">{{ fmtMoney(dados.empresa.dinheiro?.reservado) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="border-t px-3 py-1.5 text-[11px] text-muted-foreground">"Na conta" = o último saldo do extrato, como a plataforma informou. "A liberar" = soma do líquido previsto das vendas ainda não liberadas (a coluna Líq. previsto da aba Pedidos). "Reservado" = dinheiro travado em reclamação aberta no Mercado Pago. Clique na loja abre os pedidos dela aguardando liberação.</div>
      </div>

      <!-- 15/10: o rodapé do mês fica guardado (Vinicius: "por enquanto não preciso"). -->
      <div v-if="MOSTRAR_MES" class="rounded-md border bg-card">
        <div class="overflow-auto">
          <table class="w-full border-collapse text-[11px]">
            <thead class="bg-muted text-[10px] uppercase tracking-wide text-muted-foreground">
              <tr>
                <th class="border-b px-2 py-1 text-left">Mês atual ({{ nomeMes(dados.mes) }}, até {{ fmtDia(dados.dia + 'T12:00:00') }})</th>
                <th class="border-b px-2 py-1 text-right">Pedidos</th>
                <th class="border-b px-2 py-1 text-right">Vendido</th>
                <th class="border-b px-2 py-1 text-right">Tarifas + frete</th>
                <th class="border-b px-2 py-1 text-right">Recebido</th>
                <th class="border-b px-2 py-1 text-right">Ajustes meses anteriores</th>
                <th class="border-b px-2 py-1 text-right">Custo</th>
                <th class="border-b px-2 py-1 text-right">Reembolsos</th>
                <th class="border-b px-2 py-1 text-right">Sobrou</th>
                <th class="border-b px-2 py-1 text-right">Margem</th>
                <th class="border-b px-2 py-1 text-right">Finalizados</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="p in [...plataformasDoMes, { key: 'empresa', label: 'Empresa', mes: dados.empresa }]" :key="p.key" class="border-b" :class="p.key === 'empresa' ? 'bg-muted/40 font-bold' : ''">
                <td class="px-2 py-1 font-bold">{{ p.label }} <span class="font-normal text-muted-foreground">({{ p.key === 'empresa' ? 'tudo' : `${p.mes.lojas || 0} loja(s)` }})</span></td>
                <td class="px-2 py-1 text-right tabular-nums">{{ p.mes.pedidos }}</td>
                <td class="px-2 py-1 text-right tabular-nums">{{ fmtMoney(p.mes.vendido) }}</td>
                <td class="px-2 py-1 text-right tabular-nums text-red-600">− {{ fmtMoney((p.mes.tarifa || 0) + (p.mes.frete || 0)) }}</td>
                <td class="px-2 py-1 text-right tabular-nums text-emerald-700">{{ fmtMoney(p.mes.recebido) }}</td>
                <td class="px-2 py-1 text-right tabular-nums" :class="sinal(p.mes.ajustes_meses_anteriores, { pos: 'text-emerald-700', neg: 'text-red-600' })">{{ fmtMoney(p.mes.ajustes_meses_anteriores) }}</td>
                <td class="px-2 py-1 text-right tabular-nums text-red-600">− {{ fmtMoney(p.mes.custo) }}</td>
                <td class="px-2 py-1 text-right tabular-nums text-red-600">− {{ fmtMoney(p.mes.reembolso) }}</td>
                <td class="px-2 py-1 text-right tabular-nums font-bold" :class="sinal(p.mes.sobrou, { pos: 'text-emerald-700', neg: 'text-red-600' })">{{ fmtMoney(p.mes.sobrou) }}</td>
                <td class="px-2 py-1 text-right tabular-nums font-bold">{{ fmtPct(p.mes.margem) }}</td>
                <td class="px-2 py-1 text-right tabular-nums">{{ p.mes.finalizados }} ({{ pctFinal(p.mes) }}%)</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="border-t px-3 py-1.5 text-[11px] text-muted-foreground">Pedidos pela data da venda, do dia 1º até o dia escolhido. "Recebido" é o que já entrou na conta; "Sobrou" e "Margem" só com dinheiro que entrou: sobrou = recebido − custo − reembolsos; cancelado não carrega custo. O fechamento completo, com a fatura do ML e a prova dos nove do caixa, está em "Fechamento do mês".</div>
      </div>
    </template>
  </div>
</template>
