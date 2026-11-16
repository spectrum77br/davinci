<script lang="ts">
// Aba "Métricas" do Atendimento. Refeita em 13/11/2026 ("por equipe e não mais por loja; traz as
// %; faz o agente 1 vez por dia trazer uma análise geral") e de novo em 16/11/2026 — o Eduardo:
// "uma lista em cima a % de mensagens enviadas automático, daí a lista mostrando tempo de resposta
// de cada equipe pegando a média geral mesmo, e se abrir mostra específico; em lista, tirando esse
// bloco". Para o ADMIN:
// 1. a "Análise do dia" que o agente escreve às 07:40 (GET /metricas/analise), com o negrito do
//    texto dele (o **…**) virando negrito de verdade;
// 2. o período (24 h, 7, 15, 30 dias) e "atualizar";
// 3. no topo, SAIU SOZINHO: das respostas das lojas no chat (pela IA, pela equipe no DaVinci e
//    por fora), quantas a IA mandou sozinha — sem as mensagens automáticas (a meta do dono: 60%);
// 4. a LISTA das equipes (Londres, Israel, Marrocos; "sem equipe" e "todas as lojas" no fim), cada
//    linha com o que saiu sozinho, o TEMPO MÉDIO da primeira resposta (a média de todas as
//    esperas), o no prazo e a aprovação da IA (a régua do envio sozinho, 75%); clicar abre as lojas
//    dela, com os mesmos números (GET /metricas/equipes; a conta é de
//    apps/api/app/services/atendimento/metricas_equipe.py).
// Saíram os cartões grandes e o select "Ver lojas…" (16/11).
// QUEM NÃO É ADMIN (as rotas das equipes e da análise são só de admin: 403 `admin_only`) vê só a
// tabela das lojas do GET /metricas — o tempo de resposta, no escopo dele —, como antes. O admin
// que levar 403 ali (o papel mudou) cai na mesma tabela, sem erro na tela.

export const ROTAS = {
  lojas: (dias: number) => `/api/atendimento/metricas?dias=${dias}`,
  equipes: (dias: number) => `/api/atendimento/metricas/equipes?dias=${dias}`,
  analise: '/api/atendimento/metricas/analise',
}
export const PERIODOS = [1, 7, 15, 30]
// A régua do envio sozinho (`regua.LIMIAR_PADRAO`, 75% desde a virada de 13/11/2026): verde a
// partir dela; âmbar de 60%.
export const REGUA = 0.75
export const ATENCAO = 0.6
// A meta do que sai sozinho (13/11/2026, o dono: "o foco é chegar em 60% pelo menos auto").
export const META_SOZINHO = 0.6
export const FUSO = 'America/Sao_Paulo'
// O cron `atendimento_analise_diaria` (apps/api/app/worker.py): 07:40 de São Paulo.
export const HORA_ANALISE = '07:40'

export type Numeros = {
  decisoes: number
  acertos: number
  aprovacao?: number | null
  ia_enviou: number
  equipe_enviou: number
  // 16/11/2026: as respostas das lojas no chat, as por fora, a parte da IA, as esperas
  // respondidas, o tempo médio da primeira resposta (minutos) e o no prazo (0–100).
  respostas?: number
  fora?: number
  pct_sozinho?: number | null
  respondidas?: number
  tempo_medio_min?: number | null
  pct_no_prazo?: number | null
}
export type EquipeMetrica = Numeros & { equipe: string; lojas: number }
export type LojaDaEquipe = Numeros & { integration_id: string; loja: string; plataforma: string | null; equipe: string | null }
export type MetricasEquipes = { dias: number; equipes: EquipeMetrica[]; sem_equipe?: Numeros & { lojas: number }; lojas: LojaDaEquipe[]; total: Numeros }
export type Analise = { dia: string; texto: string; gerada_em: string; modelo?: string | null }
export type LojaMetrica = {
  integration_id?: string | null
  conta: string | null
  plataforma: string
  recebidas: number
  respondidas: number
  mediana_primeira_resposta_min: number | null
  p90_primeira_resposta_min: number | null
  pct_no_prazo: number | null
}
export type MetricasLojas = { dias: number; lojas: LojaMetrica[] }
export type Grupo = { chave: string; nome: string; lojas: number; numeros: Numeros; total?: boolean }
export type Trecho = { texto: string; negrito: boolean }

// A aprovação (0–1) pelas contagens; sem decisão, null.
export function aprovacao(n: Pick<Numeros, 'decisoes' | 'acertos'> | null | undefined): number | null {
  if (!n || !n.decisoes) return null
  return (n.acertos || 0) / n.decisoes
}
// Para BAIXO: 74,5% aparece "74%" (âmbar) — nunca "75%" sem ter batido a régua.
export function pctAprovacao(a: number | null): string {
  return a === null ? '—' : `${Math.floor(a * 100 + 1e-6)}%`
}
export function corAprovacao(a: number | null): string {
  if (a === null) return 'text-muted-foreground'
  if (a >= REGUA) return 'text-emerald-700 dark:text-emerald-300'
  if (a >= ATENCAO) return 'text-amber-700 dark:text-amber-300'
  return 'text-red-600 dark:text-red-400'
}
// "no prazo" (0–100): a régua da Amazon (90%).
export function corPrazo(v: number | null | undefined): string {
  if (v === null || v === undefined) return 'text-muted-foreground'
  if (v >= 90) return 'text-emerald-700 dark:text-emerald-300'
  if (v >= 75) return 'text-amber-700 dark:text-amber-300'
  return 'font-semibold text-red-600 dark:text-red-400'
}
// O que saiu sozinho (0–1): o do backend; sem ele, a conta; sem resposta, null.
export function sozinho(n: Pick<Numeros, 'pct_sozinho' | 'ia_enviou' | 'respostas'> | null | undefined): number | null {
  if (!n) return null
  if (n.pct_sozinho !== undefined && n.pct_sozinho !== null) return n.pct_sozinho
  return n.respostas ? Math.min(1, (n.ia_enviou || 0) / n.respostas) : null
}
// Para baixo também: 59,6% aparece "59%" — nunca a meta sem ter batido.
export function pctSozinho(a: number | null): string {
  return a === null ? '—' : `${Math.floor(a * 100 + 1e-6)}%`
}
export function corSozinho(a: number | null): string {
  if (a === null) return 'text-muted-foreground'
  return a >= META_SOZINHO ? 'text-emerald-700 dark:text-emerald-300' : ''
}
export function pctPrazo(v: number | null | undefined): string {
  return v === null || v === undefined || Number.isNaN(Number(v)) ? '—' : `${Math.round(Number(v))}%`
}

function hhmm(d: Date): string {
  return d.toLocaleTimeString('pt-BR', { timeZone: FUSO, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
}
// "gerada às 07:40 de 13/11" (a hora de São Paulo).
export function quandoGerada(iso: string | null | undefined): string {
  const d = iso ? new Date(iso) : null
  if (!d || Number.isNaN(d.getTime())) return ''
  return `gerada às ${hhmm(d)} de ${d.toLocaleDateString('pt-BR', { timeZone: FUSO, day: '2-digit', month: '2-digit' })}`
}
// Quando sai a próxima (sem nenhuma ainda): hoje, antes das 07:40 de São Paulo; senão amanhã.
export function proximaAnalise(agora: Date = new Date()): string {
  return `${hhmm(agora) < HORA_ANALISE ? 'hoje' : 'amanhã'} às ${HORA_ANALISE}`
}

// O texto da análise em linhas, com o negrito do modelo (**assim**) separado — sem v-html. O
// título com "#" vira linha comum; o "* " ou "- " do começo, "• "; o "**" que sobrar some.
export function linhasDaAnalise(texto: string | null | undefined): Trecho[][] {
  return (texto || '').split('\n').map((bruta) => {
    const linha = bruta.replace(/^\s*#{1,6}\s+/, '').replace(/^(\s*)[*-]\s+/, '$1• ')
    const partes: Trecho[] = []
    const negrito = /\*\*(.+?)\*\*/g
    let i = 0
    let m: RegExpExecArray | null
    while ((m = negrito.exec(linha))) {
      if (m.index > i) partes.push({ texto: linha.slice(i, m.index), negrito: false })
      partes.push({ texto: m[1], negrito: true })
      i = m.index + m[0].length
    }
    if (i < linha.length) partes.push({ texto: linha.slice(i), negrito: false })
    return partes.map((p) => ({ ...p, texto: p.texto.replace(/\*\*/g, '') })).filter((p) => p.texto)
  })
}

// As lojas de uma linha da lista: 'todas'; 'sem' (sem equipe); 'e:<nome>'. As de mais
// respostas primeiro (as que pesam); empate: mais decisões, depois o nome.
export function lojasDaEquipe(lojas: LojaDaEquipe[], chave: string): LojaDaEquipe[] {
  const lista = chave === 'todas'
    ? lojas
    : chave === 'sem'
      ? lojas.filter((l) => !l.equipe)
      : lojas.filter((l) => chave.startsWith('e:') && l.equipe === chave.slice(2))
  return [...lista].sort((a, b) => (b.respostas || 0) - (a.respostas || 0) || b.decisoes - a.decisoes || (a.loja || '').localeCompare(b.loja || '', 'pt-BR'))
}

// As linhas da lista: as equipes, "sem equipe" (só com movimento) e "todas as lojas" no fim.
export function gruposDaLista(m: MetricasEquipes | null | undefined): Grupo[] {
  if (!m) return []
  const grupos: Grupo[] = m.equipes.map((e) => ({ chave: `e:${e.equipe}`, nome: e.equipe, lojas: e.lojas, numeros: e }))
  const sem = m.sem_equipe
  if (sem && sem.lojas && (sem.respostas || sem.decisoes || sem.respondidas)) {
    grupos.push({ chave: 'sem', nome: 'Sem equipe', lojas: sem.lojas, numeros: sem })
  }
  grupos.push({ chave: 'todas', nome: 'Todas as lojas', lojas: m.lojas.length, numeros: m.total, total: true })
  return grupos
}
</script>

<script setup lang="ts">
import { ChevronRight, Loader2, RotateCcw, Sparkles } from 'lucide-vue-next'
import { duracao, erroDaApi, statusDoErro } from '~/components/AtendimentoPlataforma.vue'

const { api } = useApi()
const ehAdmin = useIsAdmin()
// O admin que levou 403 nas rotas das equipes (o papel mudou): a tela de quem não é admin.
const semAcesso = ref(false)
const visaoAdmin = computed(() => ehAdmin.value && !semAcesso.value)

const dias = ref(7)
const lojas = ref<MetricasLojas | null>(null)
const equipes = ref<MetricasEquipes | null>(null)
const carregando = ref(false)
const erro = ref<string | null>(null)
const analise = ref<Analise | null>(null)
const analiseLida = ref(false)
const erroAnalise = ref<string | null>(null)
// As linhas abertas da lista (as lojas delas aparecem embaixo).
const abertas = ref<string[]>([])

// O 403 das rotas de admin não é erro: vira a tela de quem não é admin.
async function soDeAdmin<T>(caminho: string): Promise<T | null> {
  try {
    return await api<T>(caminho)
  } catch (e: any) {
    if (statusDoErro(e) === 403) {
      semAcesso.value = true
      return null
    }
    throw e
  }
}

let geracao = 0
async function carregar() {
  const g = ++geracao
  carregando.value = true
  erro.value = null
  try {
    if (visaoAdmin.value) {
      const e = await soDeAdmin<MetricasEquipes>(ROTAS.equipes(dias.value))
      if (g !== geracao) return
      equipes.value = e
      if (e) return
    }
    // Quem não é admin (ou o admin que levou 403): a tabela das lojas de sempre.
    const l = await api<MetricasLojas>(ROTAS.lojas(dias.value))
    if (g !== geracao) return
    lojas.value = l
  } catch (e: any) {
    if (g !== geracao) return
    erro.value = erroDaApi(e, 'Não consegui carregar as métricas').texto
  } finally {
    if (g === geracao) carregando.value = false
  }
}
async function carregarAnalise() {
  if (!visaoAdmin.value) return
  try {
    const r = await soDeAdmin<{ analise: Analise | null }>(ROTAS.analise)
    analise.value = r?.analise ?? null
    erroAnalise.value = null
  } catch (e: any) {
    erroAnalise.value = erroDaApi(e, 'Não consegui carregar a análise do dia').texto
  } finally {
    analiseLida.value = true
  }
}
function atualizar() {
  void carregar()
  void carregarAnalise()
}
onMounted(atualizar)
watch(dias, () => void carregar())

const grupos = computed(() => gruposDaLista(equipes.value))
const total = computed(() => equipes.value?.total || null)
const linhasAnalise = computed(() => linhasDaAnalise(analise.value?.texto))
const noPeriodo = computed(() => (dias.value === 1 ? 'nas últimas 24 h' : `nos últimos ${dias.value} dias`))
function aberta(chave: string) {
  return abertas.value.includes(chave)
}
function alternar(chave: string) {
  abertas.value = aberta(chave) ? abertas.value.filter((c) => c !== chave) : [...abertas.value, chave]
}
function lojasDe(chave: string) {
  return lojasDaEquipe(equipes.value?.lojas || [], chave)
}

// Quem não é admin: a tabela de sempre, a pior no prazo primeiro.
const lojasTempo = computed(() =>
  (lojas.value?.lojas || []).slice().sort((a, b) => (a.pct_no_prazo ?? 101) - (b.pct_no_prazo ?? 101) || b.recebidas - a.recebidas),
)
const totais = computed(() => {
  const ls = lojas.value?.lojas || []
  return { recebidas: ls.reduce((s, l) => s + (l.recebidas || 0), 0), respondidas: ls.reduce((s, l) => s + (l.respondidas || 0), 0) }
})
function tempo(min: number | null | undefined) {
  return min === null || min === undefined ? '—' : duracao(Number(min))
}
</script>

<template>
  <div class="space-y-4">
    <!-- a análise que o agente escreve uma vez por dia (só admin) -->
    <section v-if="visaoAdmin" class="rounded-lg border bg-card px-3 py-2.5" data-analise>
      <div class="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <h3 class="flex items-center gap-1.5 text-sm font-semibold"><Sparkles class="size-4 text-muted-foreground" /> Análise do dia</h3>
        <span v-if="analise" class="text-xs text-muted-foreground">{{ quandoGerada(analise.gerada_em) }}</span>
      </div>
      <div v-if="analise" class="mt-1.5 space-y-0.5 text-sm leading-relaxed" data-analise-texto>
        <p v-for="(linha, i) in linhasAnalise" :key="i" :class="linha.length ? '' : 'h-1.5'">
          <template v-for="(t, j) in linha" :key="j">
            <strong v-if="t.negrito" class="font-semibold">{{ t.texto }}</strong>
            <template v-else>{{ t.texto }}</template>
          </template>
        </p>
      </div>
      <p v-else-if="erroAnalise" class="mt-1 text-xs text-red-600 dark:text-red-400">{{ erroAnalise }}</p>
      <p v-else-if="analiseLida" class="mt-1 text-sm text-muted-foreground">A primeira análise sai {{ proximaAnalise() }}.</p>
    </section>

    <div class="flex flex-wrap items-center gap-2">
      <span class="text-sm text-muted-foreground">Período:</span>
      <div class="flex gap-1 rounded-md bg-muted/40 p-1">
        <button
          v-for="d in PERIODOS"
          :key="d"
          type="button"
          class="rounded px-2.5 py-1 text-xs transition-colors"
          :class="dias === d ? 'bg-background shadow-sm' : 'text-muted-foreground hover:text-foreground'"
          @click="dias = d"
        >{{ d === 1 ? '24 h' : `${d} dias` }}</button>
      </div>
      <Button size="sm" variant="outline" class="ml-auto" :disabled="carregando" @click="atualizar">
        <RotateCcw class="mr-1.5 size-4" :class="{ 'animate-spin': carregando }" /> atualizar
      </Button>
    </div>

    <div v-if="erro" class="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-600 dark:text-red-400">
      {{ erro }} <button type="button" class="ml-2 text-xs underline" @click="atualizar">tentar de novo</button>
    </div>
    <div v-if="carregando && !lojas && !equipes" class="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 class="size-4 animate-spin" /> carregando…</div>

    <!-- ADMIN: o que saiu sozinho no topo e a lista das equipes (abre as lojas) -->
    <template v-if="visaoAdmin">
      <section v-if="total" class="rounded-lg border bg-card px-3 py-2.5" data-sozinho>
        <div class="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <span class="text-3xl font-semibold tabular-nums leading-9" :class="corSozinho(sozinho(total))">{{ pctSozinho(sozinho(total)) }}</span>
          <span class="text-sm font-medium">das respostas saíram sozinhas, pela IA</span>
          <span class="text-xs text-muted-foreground">(meta {{ Math.round(META_SOZINHO * 100) }}%)</span>
        </div>
        <p class="mt-0.5 text-xs text-muted-foreground tabular-nums">
          {{ total.ia_enviou }} pela IA · {{ total.equipe_enviou }} pela equipe no DaVinci · {{ total.fora ?? 0 }} por fora (Duoke, central da plataforma) — de {{ total.respostas ?? 0 }} respostas no chat {{ noPeriodo }}. Mensagem automática (menu, campanha, cartão da plataforma) não conta.
        </p>
      </section>

      <section v-if="equipes" class="space-y-2" data-equipes>
        <div class="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <h3 class="text-sm font-semibold">Por equipe</h3>
          <span class="text-xs text-muted-foreground">Clique na equipe para ver as lojas. Tempo médio = da primeira mensagem do comprador até a primeira resposta da loja, na média de todas as esperas. Aprovação = das decisões da equipe sobre a sugestão da IA, quantas a aprovaram (o assunto sai sozinho com 75%; 85% nos sensíveis).</span>
        </div>
        <div class="overflow-x-auto rounded-lg border">
          <table class="w-full min-w-[620px] text-xs">
            <thead class="bg-muted/40 text-[11px] text-muted-foreground">
              <tr class="text-left">
                <th class="px-3 py-2 font-semibold">Equipe</th>
                <th class="px-2 py-2 text-right font-semibold" title="das respostas das lojas no chat, quantas a IA mandou sozinha">Saiu sozinho</th>
                <th class="px-2 py-2 text-right font-semibold" title="da primeira mensagem do comprador até a primeira resposta da loja, na média de todas as esperas">Tempo médio</th>
                <th class="px-2 py-2 text-right font-semibold" title="respondidas dentro do prazo da loja (Amazon cobra 90% em 24 h)">No prazo</th>
                <th class="px-3 py-2 text-right font-semibold" title="das decisões da equipe sobre a sugestão da IA, quantas a aprovaram (igual ou só a forma)">Aprovação da IA</th>
              </tr>
            </thead>
            <tbody v-for="g in grupos" :key="g.chave" class="border-t" :data-grupo="g.chave">
              <tr class="cursor-pointer hover:bg-muted/30" :class="g.total ? 'bg-muted/20' : ''" @click="alternar(g.chave)">
                <td class="px-3 py-2">
                  <button type="button" class="flex items-center gap-1.5 text-left" :aria-expanded="aberta(g.chave)" @click.stop="alternar(g.chave)">
                    <ChevronRight class="size-3.5 shrink-0 text-muted-foreground transition-transform" :class="aberta(g.chave) ? 'rotate-90' : ''" />
                    <span class="font-semibold">{{ g.nome }}</span>
                    <span class="text-muted-foreground">{{ g.lojas }} loja{{ g.lojas === 1 ? '' : 's' }}</span>
                  </button>
                </td>
                <td class="px-2 py-2 text-right tabular-nums">
                  <span class="font-semibold" :class="corSozinho(sozinho(g.numeros))">{{ pctSozinho(sozinho(g.numeros)) }}</span>
                  <span class="ml-1 text-[10px] text-muted-foreground">{{ g.numeros.ia_enviou }} de {{ g.numeros.respostas ?? 0 }}</span>
                </td>
                <td class="px-2 py-2 text-right font-semibold tabular-nums">{{ tempo(g.numeros.tempo_medio_min) }}</td>
                <td class="px-2 py-2 text-right tabular-nums" :class="corPrazo(g.numeros.pct_no_prazo)">{{ pctPrazo(g.numeros.pct_no_prazo) }}</td>
                <td class="px-3 py-2 text-right font-semibold tabular-nums" :class="corAprovacao(aprovacao(g.numeros))" :title="`${g.numeros.acertos} de ${g.numeros.decisoes} decisões`">{{ pctAprovacao(aprovacao(g.numeros)) }}</td>
              </tr>
              <template v-if="aberta(g.chave)">
                <tr v-for="l in lojasDe(g.chave)" :key="l.integration_id" class="bg-muted/10 hover:bg-muted/30" :data-loja="l.integration_id">
                  <td class="py-1.5 pl-9 pr-3">
                    <div class="flex items-center gap-1.5">
                      <AtendimentoPlataforma :codigo="l.plataforma" />
                      <span>{{ l.loja || '—' }}</span>
                      <span v-if="g.chave === 'todas'" class="text-muted-foreground">· {{ l.equipe || 'sem equipe' }}</span>
                    </div>
                  </td>
                  <td class="px-2 py-1.5 text-right tabular-nums">
                    <span :class="corSozinho(sozinho(l))">{{ pctSozinho(sozinho(l)) }}</span>
                    <span class="ml-1 text-[10px] text-muted-foreground">{{ l.ia_enviou }} de {{ l.respostas ?? 0 }}</span>
                  </td>
                  <td class="px-2 py-1.5 text-right tabular-nums">{{ tempo(l.tempo_medio_min) }}</td>
                  <td class="px-2 py-1.5 text-right tabular-nums" :class="corPrazo(l.pct_no_prazo)">{{ pctPrazo(l.pct_no_prazo) }}</td>
                  <td class="px-3 py-1.5 text-right tabular-nums" :class="corAprovacao(aprovacao(l))" :title="`${l.acertos} de ${l.decisoes} decisões`">{{ pctAprovacao(aprovacao(l)) }}</td>
                </tr>
                <tr v-if="!lojasDe(g.chave).length">
                  <td colspan="5" class="py-3 pl-9 text-muted-foreground">Nenhuma loja com movimento no período.</td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
        <p v-if="!equipes.equipes.length" class="text-xs text-muted-foreground">Nenhuma equipe (Londres, Israel, Marrocos) com lojas marcadas ainda (Usuários › Lojas no atendimento).</p>
      </section>
    </template>

    <!-- quem não é admin: a tabela das lojas de sempre (o tempo de resposta, no escopo dele) -->
    <section v-else-if="lojas" class="space-y-2" data-lojas-tempo>
      <div class="flex flex-wrap items-baseline gap-2">
        <h3 class="text-sm font-semibold">Por loja</h3>
        <span class="text-xs text-muted-foreground">{{ totais.recebidas }} vez(es) em que o comprador esperou resposta · {{ totais.respondidas }} respondida(s). Uma "vez" junta as mensagens seguidas do comprador até a loja responder — a mesma conversa pode contar mais de uma. Tempo = da primeira dessas mensagens até a primeira resposta da loja (pelo DaVinci ou por fora). Mensagem automática não é resposta: o robô e as campanhas do Duoke (inclusive o "já segue nossa loja" e a figurinha da campanha), os cartões que a própria Shopee põe na conversa (cupom, logística, pedido de avaliação) e o que o DaVinci manda sozinho (senha da devolução) não contam.</span>
      </div>
      <div class="overflow-x-auto rounded-lg border">
        <table class="w-full min-w-[720px] text-xs">
          <thead class="bg-muted/40 text-[11px] text-muted-foreground">
            <tr class="text-left">
              <th class="px-3 py-2 font-semibold">Loja</th>
              <th class="px-2 py-2 text-right font-semibold" title="vezes em que o comprador falou e ficou esperando resposta (não é número de conversas: uma conversa com 3 idas e vindas conta 3)">Esperas</th>
              <th class="px-2 py-2 text-right font-semibold" title="dessas vezes, quantas a loja já respondeu">Respondidas</th>
              <th class="px-2 py-2 text-right font-semibold" title="metade das esperas foi respondida em até este tempo">Tempo típico</th>
              <th class="px-2 py-2 text-right font-semibold" title="9 em cada 10 esperas foram respondidas em até este tempo">9 em 10 até</th>
              <th class="px-3 py-2 text-right font-semibold" title="respondidas dentro do prazo da loja (Amazon cobra 90% em 24 h)">No prazo</th>
            </tr>
          </thead>
          <tbody class="divide-y">
            <tr v-if="!lojasTempo.length">
              <td colspan="6" class="py-8 text-center text-muted-foreground">Sem conversas no período.</td>
            </tr>
            <tr v-for="(l, i) in lojasTempo" :key="`${l.plataforma}-${l.conta}-${i}`" class="hover:bg-muted/30">
              <td class="px-3 py-2">
                <div class="flex items-center gap-1.5">
                  <AtendimentoPlataforma :codigo="l.plataforma" />
                  <span class="font-medium">{{ l.conta || '—' }}</span>
                </div>
              </td>
              <td class="px-2 py-2 text-right tabular-nums">{{ l.recebidas }}</td>
              <td class="px-2 py-2 text-right tabular-nums">{{ l.respondidas }}</td>
              <td class="px-2 py-2 text-right tabular-nums">{{ tempo(l.mediana_primeira_resposta_min) }}</td>
              <td class="px-2 py-2 text-right tabular-nums">{{ tempo(l.p90_primeira_resposta_min) }}</td>
              <td class="px-3 py-2 text-right tabular-nums" :class="corPrazo(l.pct_no_prazo)">{{ pctPrazo(l.pct_no_prazo) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>
