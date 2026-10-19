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
  (e: 'buscar-transacoes', loja: string): void
}>()

type DiaLoja = {
  status: 'fechado' | 'pronto' | 'aberto' | 'extrato_nao_lido' | 'sem_movimento' | 'aguardando_repasse' | 'repasse_falhou' | 'fatura_nao_lida'
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
type Dinheiro = { na_conta: number | null; saldo_em: string | null; a_liberar: number; a_liberar_pedidos: number; reservado: number | null; preso?: number | null; a_receber?: number | null; nota?: string | null }
// Quadro "Cobranças da plataforma sem pedido" (19/10): o que a plataforma tirou (positivo = saiu) sem pedido no mês
type Cobrancas = {
  imposto: number
  publicidade: number
  penalidades: number
  penalidades_linhas: number
  sem_dono: number
  sem_dono_valor: number
  fatura_ml: number
  anteriores_entrou: number
  anteriores_saiu: number
  anteriores_linhas: number
}
// Coluna "Cobranças extras (sem pedido)" do bloco Dias (19/10): positivo = a plataforma tirou
type CobrancasExtras = { total: number; sem_dono: number; imposto: number; publicidade: number; penalidades: number; fatura_ml: number }
// Bloco "Resultado do mês" (maquete de 19/10; ainda escondido na tela)
type ResultadoMes = {
  entrou_pedidos_mes: number
  saques: number
  liberados: number
  liberados_recebido: number
  liberados_custo: number
  liberados_reembolso: number
  liberados_frete_pago: number
  liberados_sobrou: number
}
type DiaResumo = {
  dia: string
  fechados: number
  sem_movimento: number
  aguardando_repasse: number
  com_pendencia: number
  pendentes: { conta: string; motivo: string }[]
  obs: string[]
  cobrancas_extras?: CobrancasExtras
}
type LojaResumo = {
  conta: string
  platform: string
  inicio: string
  dinheiro: Dinheiro
  cobrancas?: Cobrancas
  resultado_mes?: ResultadoMes
  dia: DiaLoja
  pedidos: PedidosLoja
  leituras: Leituras
  mes: Mes
  situacao: string
  gravidade: 'ok' | 'warn' | 'bad'

  obs_dia: string[]
  problemas_dia: string[]

  modo_extrato?: string
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
  por_plataforma: Record<string, Mes & { dinheiro?: Dinheiro; cobrancas?: Cobrancas; resultado_mes?: ResultadoMes }>
  empresa: Mes & { dinheiro?: Dinheiro; cobrancas?: Cobrancas; resultado_mes?: ResultadoMes }
  cartoes: Record<string, number>

  dias: DiaResumo[]
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
// Bloco "Resultado do mês": desligado em 15/10; refeito em 19/10 pela maquete aprovada, mas o
// Vinicius pediu pra não liberar ainda ("esse painel do mês não vamos usar ainda") — true liga.
const MOSTRAR_MES = false
// Quadro "Cobranças da plataforma": positivo = a plataforma tirou; negativo = devolveu.
const abertosCob = ref<Record<string, boolean>>({})
function alternarCob(k: string) {
  abertosCob.value[k] = !abertosCob.value[k]
}
const COB_COLS = ['imposto', 'publicidade', 'penalidades', 'fatura_ml'] as const
function fmtCob(v: number | null | undefined) {
  if (v === null || v === undefined || Math.abs(v) < 0.005) return '—'
  return v > 0 ? `− ${fmtMoney(v)}` : `+ ${fmtMoney(-v)}`
}
function cobCls(v: number | null | undefined) {
  if (v === null || v === undefined || Math.abs(v) < 0.005) return 'text-muted-foreground'
  return v > 0 ? 'text-red-600' : 'text-emerald-700'
}
function tituloExtras(e?: CobrancasExtras) {
  if (!e) return ''
  const partes: string[] = []
  if (Math.abs(e.imposto) > 0.005) partes.push(`impostos ${fmtCob(e.imposto)}`)
  if (Math.abs(e.publicidade) > 0.005) partes.push(`Ads ${fmtCob(e.publicidade)}`)
  if (Math.abs(e.penalidades) > 0.005) partes.push(`penalidades e ajustes ${fmtCob(e.penalidades)}`)
  if (Math.abs(e.fatura_ml) > 0.005) partes.push(`fatura do ML ${fmtCob(e.fatura_ml)}`)
  return partes.join(' · ') || 'nenhuma cobrança sem pedido nesse dia'
}
// Bloco "Resultado do mês" (maquete de 19/10): uma coluna por marketplace + Empresa.
const COLS_MES = computed(() => [...plataformasDoMes.value.map((p) => ({ key: p.key, label: p.label })), { key: 'empresa', label: 'Empresa' }])
function blocoMes(key: string) {
  return key === 'empresa' ? dados.value?.empresa : dados.value?.por_plataforma[key]
}
function rm(key: string): Partial<ResultadoMes> {
  return blocoMes(key)?.resultado_mes || {}
}
function cbm(key: string): Partial<Cobrancas> {
  return blocoMes(key)?.cobrancas || {}
}
function anterioresLiquido(key: string) {
  const c = cbm(key)
  return (c.anteriores_entrou || 0) - (c.anteriores_saiu || 0)
}
function cobrancasTotal(key: string) {
  const c = cbm(key)
  return (c.imposto || 0) + (c.publicidade || 0) + (c.penalidades || 0) + (c.fatura_ml || 0)
}
function entrouLiquido(key: string) {
  return (rm(key).entrou_pedidos_mes || 0) + anterioresLiquido(key) - cobrancasTotal(key)
}
function resultadoMes(key: string) {
  return (rm(key).liberados_sobrou || 0) - cobrancasTotal(key)
}
function margemLiberados(key: string) {
  const r = rm(key)
  return r.liberados_custo ? (r.liberados_sobrou || 0) / r.liberados_custo : null
}
function presoDe(key: string) {
  return blocoMes(key)?.dinheiro?.preso ?? (key === 'empresa' ? dados.value?.por_plataforma.amazon?.dinheiro?.preso : null)
}
function aLiberarDe(key: string) {
  return blocoMes(key)?.dinheiro?.a_liberar ?? null
}
function fmtSinal(v: number | null | undefined) {
  if (v === null || v === undefined || Math.abs(v) < 0.005) return '—'
  return v > 0 ? `+ ${fmtMoney(v)}` : `− ${fmtMoney(-v)}`
}
const mesLabel = computed(() => {
  const m = dados.value?.mes
  if (!m) return ''
  const [y, mm] = m.split('-')
  return new Date(Number(y), Number(mm) - 1, 1).toLocaleDateString('pt-BR', { month: 'long', year: 'numeric' })
})
// Bloco "Dinheiro nas plataformas": cada marketplace começa fechado (só o total); "mostrar" abre as lojas.
const abertos = ref<Record<string, boolean>>({})
function alternar(key: string) {
  abertos.value = { ...abertos.value, [key]: !abertos.value[key] }
}
// Tabela de lojas (16/10, Vinicius): cada marketplace começa escondido, com o resumo
// na linha; "mostrar" abre as lojas.
const abertosLojas = ref<Record<string, boolean>>({})
function alternarLojas(key: string) {
  abertosLojas.value = { ...abertosLojas.value, [key]: !abertosLojas.value[key] }
}
function resumoGrupo(lojas: LojaResumo[]) {
  const n = (f: (l: LojaResumo) => boolean) => lojas.filter(f).length
  const partes: string[] = [`${lojas.length} loja(s)`]
  const semMov = n((l) => l.dia.status === 'sem_movimento')
  const repasse = n((l) => l.dia.status === 'aguardando_repasse')
  const fechados = n((l) => l.dia.status === 'fechado') + semMov + repasse
  const naoLido = n((l) => l.dia.status === 'extrato_nao_lido' || l.dia.status === 'fatura_nao_lida')
  const falhou = n((l) => l.dia.status === 'repasse_falhou')
  const pend = n((l) => l.dia.status === 'aberto' || l.dia.status === 'pronto')
  const obs = n((l) => l.dia.status === 'fechado' && l.obs_dia.length > 0)
  if (fechados) {
    const detalhe = [semMov ? `${semMov} sem movimento` : '', repasse ? `${repasse} aguardando repasse` : ''].filter(Boolean).join(', ')
    partes.push(`${fechados} dia(s) fechado(s)${detalhe ? ` (${detalhe})` : ''}`)
  }
  if (falhou) partes.push(`${falhou} repasse falhou`)
  if (naoLido) partes.push(`${naoLido} leitura com erro`)
  if (pend) partes.push(`${pend} com pendência`)
  if (obs) partes.push(`${obs} com observação`)
  return partes.join(' · ')
}
function grupoTemPendencia(lojas: LojaResumo[]) {
  return lojas.some((l) => ['aberto', 'pronto', 'extrato_nao_lido', 'fatura_nao_lida', 'repasse_falhou'].includes(l.dia.status))
}
function problemasCls(l: LojaResumo) {
  const grave = l.problemas_dia.some((p) => p.includes('sem dono') || p.includes('saldo não bate') || p.startsWith('erro'))
  return grave ? 'bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300' : 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300'
}
function escolherDia(d: string) {
  dia.value = d
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

// 16/10, revisão: cada leitura leva um número; resposta mais velha é descartada
// (trocar o dia rápido podia deixar o Resumo de um dia com o cabeçalho de outro).
let carregarSeq = 0
async function carregar() {
  if (!dia.value) return
  const seq = ++carregarSeq
  loading.value = true
  erro.value = ''
  try {
    const r = await api<Resumo>('/api/conciliacao/resumo', { query: { dia: dia.value } })
    if (seq !== carregarSeq) return
    dados.value = r
  } catch (e: any) {
    if (seq !== carregarSeq) return
    erro.value = e?.data?.detail?.code || e?.message || 'erro ao carregar'
  } finally {
    if (seq === carregarSeq) loading.value = false
  }
}

// 16/10, revisão: o Resumo é de ontem pra trás; › e a data digitada passavam pra hoje
// e pintavam tudo de verde ("sem movimento"). Trava em ontem.
function avancarDia() {
  const prox = somaDias(dia.value, 1)
  if (prox <= ontemISO()) dia.value = prox
}

// 16/10, revisão: o clique em "A liberar" listava só `aguardando`, menos que o número
// (a API soma aguardando + vencido em a_liberar; na Amazon entra ainda o preso na
// reserva, que é lançamento do extrato e não pedido).
const FILTRO_A_LIBERAR: Record<string, string> = { situacao: 'aguardando,vencido' }

// 16/10, revisão: "Na conta" nulo (TikTok, KFA por arquivo) entrava como 0 na soma;
// aqui só entram as lojas que têm saldo e, sem nenhuma, fica "—".
function naContaDe(lojas: LojaResumo[]) {
  const com = lojas.map((l) => l.dinheiro.na_conta).filter((v): v is number => v != null)
  return com.length ? Math.round(com.reduce((s, v) => s + v, 0) * 100) / 100 : null
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

// 16/10 (Vinicius): sem movimento com extrato lido e "aguardando o repasse" da TikTok
// aparecem como "fechado" verde, com a explicação em obs
const COMO_FECHADO: Record<string, string> = { sem_movimento: 'sem movimento', aguardando_repasse: 'aguardando o repasse da TikTok' }
function obsDoStatus(d: DiaLoja) {
  return COMO_FECHADO[d.status] || ''
}
function diaCls(d: DiaLoja) {
  if (d.status === 'fechado' || COMO_FECHADO[d.status]) return 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300'
  if (d.status === 'pronto') return 'bg-emerald-50 text-emerald-700 ring-1 ring-emerald-300 dark:bg-emerald-950/30 dark:text-emerald-300'
  if (d.status === 'aberto') return 'bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300'
  if (d.status === 'extrato_nao_lido' || d.status === 'fatura_nao_lida') return 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300'
  if (d.status === 'aguardando_repasse') return 'bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-300'
  if (d.status === 'repasse_falhou') return 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300'
  return 'bg-slate-200 text-slate-700 dark:bg-slate-700 dark:text-slate-200'
}
function diaTexto(d: DiaLoja) {
  if (d.status === 'fechado') return `fechado ${fmtHora(d.fechado_em)} · ${d.fechado_por || ''}`
  if (COMO_FECHADO[d.status]) return 'fechado'
  if (d.status === 'pronto') return 'pronto pra fechar'
  if (d.status === 'aberto') return 'não fechou'
  if (d.status === 'extrato_nao_lido') return 'extrato não lido'
  if (d.status === 'fatura_nao_lida') return 'fatura não lida'
  if (d.status === 'aguardando_repasse') return 'liquidado, aguardando o repasse'
  if (d.status === 'repasse_falhou') return 'repasse falhou, TikTok tenta de novo'
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
defineExpose({ carregar })
watch(dia, (v) => {
  const ontem = ontemISO()
  if (v > ontem) {
    dia.value = ontem // o watch dispara de novo com o dia travado
    return
  }
  carregar()
})
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
        <button type="button" class="rounded px-1 py-0.5 hover:bg-muted" title="dia seguinte" @click="avancarDia()"><ChevronRight class="size-3.5" /></button>
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
        <div class="flex min-w-0 flex-col gap-3">
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
                  <tr class="bg-muted/60">
                    <td colspan="10" class="border-b px-2 py-1">
                      <span class="font-bold">{{ g.label }}</span>
                      <span class="ml-2 text-muted-foreground" :class="grupoTemPendencia(g.lojas) && 'text-amber-700'">{{ resumoGrupo(g.lojas) }}</span>
                      <button type="button" class="ml-2 font-semibold text-primary underline" @click="alternarLojas(g.key)">{{ abertosLojas[g.key] ? 'esconder' : 'mostrar' }}</button>
                    </td>
                  </tr>
                  <tr v-for="l in g.lojas" v-show="abertosLojas[g.key]" :key="l.conta" class="cursor-pointer border-b hover:bg-muted/40" @click="emit('abrir-extrato', l.conta, dados.dia)">
                    <td class="px-2 py-1 font-bold">
                      {{ l.conta }}
                      <button v-if="canEdit" type="button" class="ml-1 rounded px-1 text-muted-foreground hover:bg-muted hover:text-foreground" :title="l.modo_extrato === 'upload' ? 'pedir ao robô do Mac mini pra buscar as transações da Amazon e enviar' : 'rodar de novo: fatura, extrato e recálculo desta loja'" @click.stop="l.modo_extrato === 'upload' ? emit('buscar-transacoes', l.conta) : emit('sincronizar', l.conta)"><RefreshCw class="inline size-3" /></button>
                    </td>
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
                    <td class="px-2 py-1">
                      <span class="inline-block rounded px-1.5 py-0.5 font-semibold" :class="diaCls(l.dia)">{{ diaTexto(l.dia) }}</span>
                      <span v-if="l.problemas_dia.length" class="ml-1 inline-block rounded px-1.5 py-0.5 font-semibold" :class="problemasCls(l)">{{ l.problemas_dia.join(' · ') }}</span>
                      <span v-if="obsDoStatus(l.dia) || (l.obs_dia.length && (l.dia.status === 'fechado' || obsDoStatus(l.dia)))" class="ml-1 text-muted-foreground">· <b>obs:</b> {{ [obsDoStatus(l.dia), ...l.obs_dia].filter(Boolean).join(', ') }}</span>
                      <span v-else-if="l.obs_dia.length" class="ml-1 inline-block rounded bg-amber-100 px-1.5 py-0.5 font-semibold text-amber-800 dark:bg-amber-900/40 dark:text-amber-300">{{ l.obs_dia.join(' · ') }}</span>
                    </td>
                  </tr>
                </template>
                <tr v-if="faseSeguinte.length" class="bg-muted/60"><td colspan="10" class="border-b px-2 py-1 font-bold">{{ faseSeguinte.join(' · ') }}</td></tr>
                <tr v-if="faseSeguinte.length" class="text-muted-foreground"><td class="px-2 py-1" colspan="10">fase seguinte — entra depois do Mercado Livre</td></tr>
                <tr v-if="!dados.lojas.length"><td colspan="10" class="py-8 text-center text-muted-foreground">nenhuma loja ligada na conciliação</td></tr>
              </tbody>
            </table>
          </div>
          <div class="border-t px-3 py-1.5 text-[11px] text-muted-foreground">
            "Situação": <b>fechado</b> = alguém fechou (100% das linhas com destino e saldo batendo), ou o extrato foi lido e não teve movimento, ou a TikTok fechou o extrato e ainda não pagou (obs diz qual) · <b>pronto pra fechar</b> = tudo casou, falta só fechar · <b>não fechou</b> = tem linha sem dono ou saldo diferente. Depois do dia fechado, o que aconteceu nele (estornado, reaberto, recebido a menos, vencido) vira <b>obs</b> em cinza; os números continuam nas colunas. Cada marketplace começa escondido; "mostrar" abre as lojas.
          </div>
        </div>
        <div class="flex min-w-0 flex-col rounded-md border bg-card">
          <div class="flex items-center justify-between rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-xs font-semibold text-white">
            <span>Dias — como ficou cada dia das {{ dados.lojas.length }} lojas (últimos {{ dados.dias.length }})</span>
            <span class="text-[10px] font-normal text-white/80">clique no dia muda a tabela de cima</span>
          </div>
          <div class="overflow-x-auto">
            <table class="w-full text-xs">
              <thead class="bg-muted text-[10px] uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th class="border-b px-2 py-1 text-left">Dia</th>
                  <th class="border-b px-2 py-1 text-right">Fechados</th>
                  <th class="border-b px-2 py-1 text-right" title="já contados em Fechados">dos quais sem movimento</th>
                  <th class="border-b px-2 py-1 text-right" title="já contados em Fechados">dos quais aguard. repasse</th>
                  <th class="border-b px-2 py-1 text-right">Com pendência</th>
                  <th class="border-b px-2 py-1 text-right" title="o que a plataforma tirou (ou devolveu) SEM pedido nesse dia, somando as lojas: impostos, Ads, penalidades e ajustes, fatura do ML">Cobranças extras</th>
                  <th class="border-b px-2 py-1 text-left">Como ficou</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="d in dados.dias" :key="d.dia" class="cursor-pointer border-b hover:bg-muted/40" :class="d.dia === dados.dia && 'bg-amber-50 dark:bg-amber-950/20'" @click="escolherDia(d.dia)">
                  <td class="whitespace-nowrap px-2 py-1 font-bold">{{ fmtDia(d.dia + 'T12:00:00') }}<span v-if="d.dia === ontemISO()" class="font-normal text-muted-foreground"> (ontem)</span></td>
                  <td class="px-2 py-1 text-right tabular-nums">{{ d.fechados }}</td>
                  <td class="px-2 py-1 text-right tabular-nums">{{ d.sem_movimento }}</td>
                  <td class="px-2 py-1 text-right tabular-nums">{{ d.aguardando_repasse }}</td>
                  <td class="px-2 py-1 text-right tabular-nums font-bold" :class="d.com_pendencia ? 'text-amber-700' : ''">{{ d.com_pendencia }}</td>
                  <td class="whitespace-nowrap px-2 py-1 text-right tabular-nums" :class="cobCls(d.cobrancas_extras?.total)" :title="tituloExtras(d.cobrancas_extras)">{{ fmtCob(d.cobrancas_extras?.total) }}<span v-if="d.cobrancas_extras?.sem_dono" class="ml-1 rounded bg-amber-100 px-1 text-[10px] font-semibold text-amber-800 dark:bg-amber-900/40 dark:text-amber-300" title="linhas desse dia que ninguém classificou ainda">{{ d.cobrancas_extras.sem_dono }} sem dono</span></td>
                  <td class="px-2 py-1">
                    <span v-if="d.com_pendencia" class="mr-1 inline-block rounded bg-amber-100 px-1.5 py-0.5 font-semibold text-amber-800 dark:bg-amber-900/40 dark:text-amber-300">{{ d.com_pendencia }} com pendência</span>
                    <span v-else class="mr-1 inline-block rounded bg-emerald-100 px-1.5 py-0.5 font-semibold text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300">todos fechados</span>
                    <span v-if="d.pendentes.length" class="text-muted-foreground"><template v-for="(p, i) in d.pendentes.slice(0, 6)" :key="p.conta"><template v-if="i"> · </template><b class="text-foreground">{{ p.conta }}</b> {{ p.motivo }}</template><template v-if="d.pendentes.length > 6"> · +{{ d.pendentes.length - 6 }}</template></span>
                    <span v-if="d.obs.length" class="text-muted-foreground"><span v-if="d.pendentes.length"> · </span>obs: {{ d.obs.join(' · ') }}</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div class="border-t px-3 py-1.5 text-[11px] text-muted-foreground">Cada linha é um dia: quantas lojas fecharam, quantas não tiveram movimento, quantas esperam o repasse da TikTok e quantas ainda têm pendência (linha sem dono, saldo diferente, extrato não lido ou pronto pra fechar). "Como ficou" diz o nome das lojas com pendência e a obs do que aconteceu no dia.</div>
          </div>

          <!-- 19/10 (Vinicius): o que a plataforma tira SEM pedido tem que aparecer em algum lugar -->
          <div class="flex min-w-0 flex-col rounded-md border bg-card">
            <div class="flex items-center justify-between rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-xs font-semibold text-white">
              <span>Cobranças da plataforma sem pedido — {{ mesLabel }} até {{ fmtDia(dados.dia + 'T12:00:00') }}</span>
              <span class="text-[10px] font-normal text-white/80">pela data do movimento · não entra no resultado dos pedidos</span>
            </div>
            <div class="overflow-x-auto">
              <table class="w-full text-xs">
                <thead class="bg-muted text-[10px] uppercase tracking-wide text-muted-foreground">
                  <tr>
                    <th class="border-b px-2 py-1 text-left">Marketplace / loja</th>
                    <th class="border-b px-2 py-1 text-right">Impostos (ICMS DIFAL)</th>
                    <th class="border-b px-2 py-1 text-right">Publicidade (Ads)</th>
                    <th class="border-b px-2 py-1 text-right">Penalidades e ajustes</th>
                    <th class="border-b px-2 py-1 text-right">Fatura do ML</th>
                    <th class="border-b px-2 py-1 text-right" title="vendas de antes do início do painel (01/10): o que entrou e saiu delas, sem conferência pedido a pedido">Pedidos anteriores a 01/10: entrou</th>
                    <th class="border-b px-2 py-1 text-right">saiu</th>
                  </tr>
                </thead>
                <tbody>
                  <template v-for="g in grupos" :key="'c' + g.key">
                    <tr class="border-b bg-muted/60 font-bold">
                      <td class="px-2 py-1">{{ g.label }} <button type="button" class="ml-1 font-semibold text-primary underline" @click="alternarCob(g.key)">{{ abertosCob[g.key] ? 'esconder' : 'mostrar' }}</button></td>
                      <td v-for="k in COB_COLS" :key="k" class="whitespace-nowrap px-2 py-1 text-right tabular-nums" :class="cobCls(dados.por_plataforma[g.key]?.cobrancas?.[k])">
                        {{ fmtCob(dados.por_plataforma[g.key]?.cobrancas?.[k]) }}<span v-if="k === 'penalidades' && dados.por_plataforma[g.key]?.cobrancas?.sem_dono" class="ml-1 rounded bg-amber-100 px-1 text-[10px] text-amber-800 dark:bg-amber-900/40 dark:text-amber-300" title="linhas que ainda ninguém classificou">{{ dados.por_plataforma[g.key]?.cobrancas?.sem_dono }} sem dono</span>
                      </td>
                      <td class="whitespace-nowrap px-2 py-1 text-right tabular-nums text-muted-foreground">{{ fmtMoney(dados.por_plataforma[g.key]?.cobrancas?.anteriores_entrou) }}</td>
                      <td class="whitespace-nowrap px-2 py-1 text-right tabular-nums text-muted-foreground">{{ fmtMoney(dados.por_plataforma[g.key]?.cobrancas?.anteriores_saiu) }}</td>
                    </tr>
                    <tr v-for="l in g.lojas" v-show="abertosCob[g.key]" :key="'c' + l.conta" class="border-b">
                      <td class="px-2 py-1 pl-4 font-bold">{{ l.conta }}</td>
                      <td v-for="k in COB_COLS" :key="k" class="whitespace-nowrap px-2 py-1 text-right tabular-nums" :class="cobCls(l.cobrancas?.[k])">
                        {{ fmtCob(l.cobrancas?.[k]) }}<span v-if="k === 'penalidades' && l.cobrancas?.sem_dono" class="ml-1 rounded bg-amber-100 px-1 text-[10px] text-amber-800 dark:bg-amber-900/40 dark:text-amber-300">{{ l.cobrancas?.sem_dono }} sem dono</span>
                      </td>
                      <td class="whitespace-nowrap px-2 py-1 text-right tabular-nums text-muted-foreground">{{ fmtMoney(l.cobrancas?.anteriores_entrou) }}</td>
                      <td class="whitespace-nowrap px-2 py-1 text-right tabular-nums text-muted-foreground">{{ fmtMoney(l.cobrancas?.anteriores_saiu) }}</td>
                    </tr>
                  </template>
                  <tr class="bg-muted/40 font-bold">
                    <td class="px-2 py-1">Empresa</td>
                    <td v-for="k in COB_COLS" :key="k" class="whitespace-nowrap px-2 py-1 text-right tabular-nums" :class="cobCls(dados.empresa.cobrancas?.[k])">
                      {{ fmtCob(dados.empresa.cobrancas?.[k]) }}<span v-if="k === 'penalidades' && dados.empresa.cobrancas?.sem_dono" class="ml-1 rounded bg-amber-100 px-1 text-[10px] text-amber-800 dark:bg-amber-900/40 dark:text-amber-300">{{ dados.empresa.cobrancas?.sem_dono }} sem dono</span>
                    </td>
                    <td class="whitespace-nowrap px-2 py-1 text-right tabular-nums text-muted-foreground">{{ fmtMoney(dados.empresa.cobrancas?.anteriores_entrou) }}</td>
                    <td class="whitespace-nowrap px-2 py-1 text-right tabular-nums text-muted-foreground">{{ fmtMoney(dados.empresa.cobrancas?.anteriores_saiu) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div class="border-t px-3 py-1.5 text-[11px] text-muted-foreground">Vermelho = a plataforma tirou; verde = devolveu. Impostos (ICMS DIFAL da Shopee) e Publicidade (recarga de Ads) o robô classifica sozinho. Penalidades e ajustes ficam sem dono até alguém classificar no Extrato do dia — "N sem dono" é dinheiro que saiu e ninguém olhou. Fatura do ML = cobrada do saldo + segurada pra dívida, sem contar duas vezes. "Pedidos anteriores a 01/10" é o que entrou e saiu de vendas de antes do início do painel: não é conferido pedido a pedido.</div>
          </div>
        </div>


        <div class="flex min-w-0 flex-col gap-3">
        <div class="flex min-w-0 flex-col rounded-md border bg-card">
          <div class="rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-center text-xs font-semibold text-white">Onde agir hoje</div>
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

        <!-- Dinheiro nas plataformas (opção B de 15/10): estreito, embaixo do "Onde agir" -->
        <div class="flex min-w-0 flex-col rounded-md border bg-card">
          <div class="rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-center text-xs font-semibold text-white">Dinheiro nas plataformas</div>
          <table class="w-full border-collapse text-[11px]">
            <thead class="bg-muted text-[10px] uppercase tracking-wide text-muted-foreground">
              <tr>
                <th class="border-b px-2 py-1 text-left">Marketplace / loja</th>
                <th class="border-b px-2 py-1 text-right">Na conta</th>
                <th class="border-b px-2 py-1 text-right">A liberar</th>
              </tr>
            </thead>
            <tbody>
              <template v-for="g in grupos" :key="'d' + g.key">
                <tr class="border-b bg-muted/60 font-bold">
                  <td class="px-2 py-1">{{ g.label }} <button type="button" class="ml-1 font-semibold text-primary underline" @click="alternar(g.key)">{{ abertos[g.key] ? 'esconder' : 'mostrar' }}</button></td>
                  <td class="whitespace-nowrap px-2 py-1 text-right tabular-nums text-emerald-700">{{ fmtMoney(naContaDe(g.lojas)) }}</td>
                  <td class="whitespace-nowrap px-2 py-1 text-right tabular-nums text-amber-700">{{ fmtMoney(dados.por_plataforma[g.key]?.dinheiro?.a_liberar) }}</td>
                </tr>
                <tr v-for="l in g.lojas" v-show="abertos[g.key]" :key="'d' + l.conta" class="cursor-pointer border-b hover:bg-muted/40" title="abre os pedidos desta loja aguardando liberação" @click="emit('ver-pedidos', l.conta, FILTRO_A_LIBERAR)">
                  <td class="px-2 py-1 pl-4">
                    <div class="font-bold">{{ l.conta }}</div>
                    <div class="text-[10px] text-muted-foreground"><template v-if="l.platform === 'tiktok'">repassa pro banco em lotes<template v-if="l.dinheiro.a_receber"> · aguardando repasse {{ fmtMoney(l.dinheiro.a_receber) }}</template></template><template v-else-if="l.dinheiro.nota">{{ l.dinheiro.nota }}</template><template v-else>saldo de {{ fmtDiaHora(l.dinheiro.saldo_em) }}</template> · {{ l.dinheiro.a_liberar_pedidos }} pedidos a liberar<template v-if="l.dinheiro.reservado != null"> · reservado {{ fmtMoney(l.dinheiro.reservado) }}</template><template v-if="l.dinheiro.preso != null"> · preso na reserva por entrega {{ fmtMoney(l.dinheiro.preso) }}</template></div>
                  </td>
                  <td class="whitespace-nowrap px-2 py-1 text-right tabular-nums text-emerald-700">{{ fmtMoney(l.dinheiro.na_conta) }}</td>
                  <td class="whitespace-nowrap px-2 py-1 text-right tabular-nums text-amber-700">{{ fmtMoney(l.dinheiro.a_liberar) }}</td>
                </tr>
              </template>
              <tr v-if="faseSeguinte.length" class="border-b bg-muted/60 font-bold"><td class="px-2 py-1">{{ faseSeguinte.join(' · ') }}</td><td colspan="2" class="px-2 py-1 text-right font-normal text-muted-foreground">fase seguinte</td></tr>
              <tr class="bg-muted/40 font-bold">
                <td class="px-2 py-1">Empresa <span class="font-normal text-muted-foreground">({{ dados.lojas.length }} loja{{ dados.lojas.length === 1 ? '' : 's' }})</span></td>
                <td class="whitespace-nowrap px-2 py-1 text-right tabular-nums text-emerald-700">{{ fmtMoney(naContaDe(dados.lojas)) }}</td>
                <td class="whitespace-nowrap px-2 py-1 text-right tabular-nums text-amber-700">{{ fmtMoney(dados.empresa.dinheiro?.a_liberar) }}</td>
              </tr>
            </tbody>
          </table>
          <div class="border-t px-3 py-1.5 text-[11px] text-muted-foreground">Na conta = último saldo do extrato, como a plataforma informou (a TikTok não guarda saldo: repassa pro banco em lotes, e o extrato que ela ainda não pagou aparece como "aguardando repasse"; na Amazon é o que já está liberado). A liberar = líquido previsto das vendas ainda não liberadas (na Amazon inclui o que está preso na reserva por entrega). Clique na loja abre os pedidos dela.</div>
        </div>
        </div>
      </div>

      <!-- 19/10: bloco "Resultado do mês" (maquete docs/img/conciliacao-resultado-mes.png) — construído, mas
           ESCONDIDO até o Vinicius liberar ("esse painel do mês não vamos usar ainda"). MOSTRAR_MES liga. -->
      <div v-if="MOSTRAR_MES" class="rounded-md border bg-card">
        <div class="flex items-center justify-between rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-xs font-semibold text-white">
          <span>Resultado do mês — {{ nomeMes(dados.mes) }} (1º a {{ fmtDia(dados.dia + 'T12:00:00') }})</span>
          <span class="text-[10px] font-normal text-white/80">uma coluna por marketplace; "Empresa" soma tudo · pela data em que o dinheiro se mexeu, menos onde diz "vendas de {{ nomeMes(dados.mes) }}"</span>
        </div>
        <div class="overflow-auto">
          <table class="w-full border-collapse text-[11px]">
            <thead class="bg-muted text-[10px] uppercase tracking-wide text-muted-foreground">
              <tr>
                <th class="border-b px-2 py-1 text-left">o que</th>
                <th v-for="c in COLS_MES" :key="c.key" class="border-b px-2 py-1 text-right">{{ c.label }}</th>
              </tr>
            </thead>
            <tbody class="tabular-nums">
              <tr class="border-b bg-muted/60 font-bold text-[#1f3a5f] dark:text-slate-200"><td class="px-2 py-1.5" :colspan="COLS_MES.length + 1">1. Caixa do mês — o que se mexeu nas contas das plataformas (pela data do movimento)</td></tr>
              <tr class="border-b"><td class="px-2 py-1">Entrou de pedidos do mês (líquido, já com estornos e mediações)</td><td v-for="c in COLS_MES" :key="c.key" class="whitespace-nowrap px-2 py-1 text-right text-emerald-700">{{ fmtSinal(rm(c.key).entrou_pedidos_mes) }}</td></tr>
              <tr class="border-b"><td class="px-2 py-1">Entrou de pedidos de ANTES do início do painel <span class="block text-[10px] text-muted-foreground">entrou − saiu dessas vendas; não é conferido pedido a pedido e o custo não está no painel</span></td><td v-for="c in COLS_MES" :key="c.key" class="whitespace-nowrap px-2 py-1 text-right" :class="cobCls(-anterioresLiquido(c.key))">{{ fmtSinal(anterioresLiquido(c.key)) }}</td></tr>
              <tr class="border-b"><td class="px-2 py-1">Cobranças sem pedido: impostos, Ads, penalidades e ajustes, fatura do ML <span class="block text-[10px] text-muted-foreground">o quadro "Cobranças da plataforma"; ao lado, quantas linhas ainda estão sem dono</span></td><td v-for="c in COLS_MES" :key="c.key" class="whitespace-nowrap px-2 py-1 text-right" :class="cobCls(cobrancasTotal(c.key))">{{ fmtCob(cobrancasTotal(c.key)) }}<span v-if="cbm(c.key).sem_dono" class="ml-1 rounded bg-amber-100 px-1 text-[10px] text-amber-800 dark:bg-amber-900/40 dark:text-amber-300">{{ cbm(c.key).sem_dono }} sem dono</span></td></tr>
              <tr class="border-b text-muted-foreground"><td class="px-2 py-1">Preso na reserva da Amazon (lançado, ainda não liberado — não conta como entrou)</td><td v-for="c in COLS_MES" :key="c.key" class="whitespace-nowrap px-2 py-1 text-right">{{ fmtMoney(presoDe(c.key)) }}</td></tr>
              <tr class="border-b bg-muted/40 font-bold"><td class="px-2 py-1">= Entrou líquido nas plataformas no mês</td><td v-for="c in COLS_MES" :key="c.key" class="whitespace-nowrap px-2 py-1 text-right" :class="cobCls(-entrouLiquido(c.key))">{{ fmtSinal(entrouLiquido(c.key)) }}</td></tr>
              <tr class="border-b text-muted-foreground"><td class="px-2 py-1">Repasses pro banco (saques) no mês — informação, não é ganho</td><td v-for="c in COLS_MES" :key="c.key" class="whitespace-nowrap px-2 py-1 text-right">{{ fmtMoney(rm(c.key).saques) }}</td></tr>

              <tr class="border-b bg-muted/60 font-bold text-[#1f3a5f] dark:text-slate-200"><td class="px-2 py-1.5" :colspan="COLS_MES.length + 1">2. Pedidos já liberados no mês — o resultado conferido pedido a pedido (só pedidos vendidos a partir do início do painel)</td></tr>
              <tr class="border-b"><td class="px-2 py-1">Pedidos liberados (bateu, a menos, a mais, estornado depois)</td><td v-for="c in COLS_MES" :key="c.key" class="whitespace-nowrap px-2 py-1 text-right">{{ (rm(c.key).liberados || 0).toLocaleString('pt-BR') }} pedidos</td></tr>
              <tr class="border-b"><td class="px-2 py-1">Recebido (o que entrou de verdade desses pedidos)</td><td v-for="c in COLS_MES" :key="c.key" class="whitespace-nowrap px-2 py-1 text-right text-emerald-700">{{ fmtSinal(rm(c.key).liberados_recebido) }}</td></tr>
              <tr class="border-b"><td class="px-2 py-1">− Custo dos produtos (preço de custo do Bling)</td><td v-for="c in COLS_MES" :key="c.key" class="whitespace-nowrap px-2 py-1 text-right text-red-600">{{ fmtSinal(-(rm(c.key).liberados_custo || 0)) }}</td></tr>
              <tr class="border-b"><td class="px-2 py-1">− Reembolsos ao cliente (painel Reembolso)</td><td v-for="c in COLS_MES" :key="c.key" class="whitespace-nowrap px-2 py-1 text-right text-red-600">{{ fmtSinal(-(rm(c.key).liberados_reembolso || 0)) }}</td></tr>
              <tr class="border-b"><td class="px-2 py-1">− Frete pago fora da plataforma (Melhor Envio)</td><td v-for="c in COLS_MES" :key="c.key" class="whitespace-nowrap px-2 py-1 text-right text-red-600">{{ fmtSinal(-(rm(c.key).liberados_frete_pago || 0)) }}</td></tr>
              <tr class="border-b bg-muted/40 font-bold"><td class="px-2 py-1">= Sobrou dos pedidos liberados <span class="block text-[10px] font-normal text-muted-foreground">margem = sobrou ÷ custo</span></td><td v-for="c in COLS_MES" :key="c.key" class="whitespace-nowrap px-2 py-1 text-right" :class="cobCls(-(rm(c.key).liberados_sobrou || 0))">{{ fmtSinal(rm(c.key).liberados_sobrou) }}<span class="block text-[10px] font-normal text-muted-foreground">margem {{ fmtPct(margemLiberados(c.key)) }}</span></td></tr>
              <tr class="border-b text-muted-foreground"><td class="px-2 py-1">Ainda a liberar: previsto pela plataforma (sem margem — só quando liberar)</td><td v-for="c in COLS_MES" :key="c.key" class="whitespace-nowrap px-2 py-1 text-right">{{ fmtMoney(aLiberarDe(c.key)) }}</td></tr>

              <tr class="border-b bg-muted/60 font-bold text-[#1f3a5f] dark:text-slate-200"><td class="px-2 py-1.5" :colspan="COLS_MES.length + 1">3. Resultado real do mês — o que dá pra afirmar hoje</td></tr>
              <tr class="border-b"><td class="px-2 py-1">Sobrou dos pedidos já liberados (bloco 2)</td><td v-for="c in COLS_MES" :key="c.key" class="whitespace-nowrap px-2 py-1 text-right" :class="cobCls(-(rm(c.key).liberados_sobrou || 0))">{{ fmtSinal(rm(c.key).liberados_sobrou) }}</td></tr>
              <tr class="border-b"><td class="px-2 py-1">− Cobranças sem pedido do mês (bloco 1)</td><td v-for="c in COLS_MES" :key="c.key" class="whitespace-nowrap px-2 py-1 text-right" :class="cobCls(cobrancasTotal(c.key))">{{ fmtCob(cobrancasTotal(c.key)) }}</td></tr>
              <tr class="border-b bg-amber-50 font-extrabold dark:bg-amber-950/20"><td class="px-2 py-1.5">= Resultado do mês até {{ fmtDia(dados.dia + 'T12:00:00') }} (pedidos liberados − cobranças)</td><td v-for="c in COLS_MES" :key="c.key" class="whitespace-nowrap px-2 py-1.5 text-right" :class="cobCls(-resultadoMes(c.key))">{{ fmtSinal(resultadoMes(c.key)) }}</td></tr>
              <tr class="border-b text-muted-foreground"><td class="px-2 py-1">+ Entrou de vendas de antes do início (sem custo no painel — à parte, não entra no resultado)</td><td v-for="c in COLS_MES" :key="c.key" class="whitespace-nowrap px-2 py-1 text-right">{{ fmtSinal(anterioresLiquido(c.key)) }}</td></tr>
              <tr class="text-muted-foreground"><td class="px-2 py-1">Vendas ainda não liberadas (previsto; a sobra só aparece quando liberar)</td><td v-for="c in COLS_MES" :key="c.key" class="whitespace-nowrap px-2 py-1 text-right">{{ fmtMoney(aLiberarDe(c.key)) }}</td></tr>
            </tbody>
          </table>
        </div>
        <div class="border-t px-3 py-1.5 text-[11px] text-muted-foreground"><b>1. Caixa</b>: o que entrou e saiu das contas das plataformas no mês, linha por linha do extrato — a mesma conta do Extrato do dia, só somada; reservas e repasses pro banco ficam de fora do "entrou líquido". <b>2. Pedidos liberados</b>: só os pedidos vendidos a partir do início do painel que já liberaram — recebido de verdade − custo − reembolso − frete pago fora = sobrou; é o único número com custo. <b>3. Resultado</b>: sobrou dos liberados − cobranças sem pedido. O que entrou de vendas de antes do início aparece à parte (sem custo); o que ainda vai liberar aparece como previsto (sem margem). A partir do mês seguinte ao início, o mês inteiro cai no bloco 2 e o resultado fecha sozinho.</div>
      </div>
    </template>
  </div>
</template>
