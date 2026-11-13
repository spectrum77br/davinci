<script lang="ts">
// Aba "Métricas" do Atendimento. Refeita em 13/11/2026 — o Eduardo: "em métricas ... por equipe
// e não mais por loja; traz as %; faz o agente 1 vez por dia trazer uma análise geral; deixa a
// tela mais simples; daí se abre o select vai abrir as lojas todas e as %". Para o ADMIN:
// 1. a "Análise do dia" que o agente escreve às 07:40 (GET /metricas/analise);
// 2. o período (24 h, 7, 15, 30 dias) e "atualizar";
// 3. um cartão por equipe (e um "Todas") com a % de APROVAÇÃO DA IA — das decisões da equipe
//    sobre o rascunho que o juiz julgou, quantas foram acerto (igual ou só a forma), a mesma
//    conta da régua do envio sozinho (75%) — e, pequeno, as decisões, o que saiu sozinho e o
//    que a equipe mandou pelo DaVinci (GET /metricas/equipes; a conta é de
//    apps/api/app/services/atendimento/metricas_equipe.py);
// 4. o select "Ver lojas…": só ao escolher (todas as equipes, uma equipe ou "sem equipe")
//    aparece a tabela das lojas, a pior aprovação primeiro, com o "no prazo" do GET /metricas de
//    sempre (o par é a mesma loja: pela integração; sem ela, nome + plataforma).
// Saíram o placar do 👍/👎 (o 👍/👎 da conversa saiu em 13/11) e "O que a equipe fez com a IA".
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
export const FUSO = 'America/Sao_Paulo'
// O cron `atendimento_analise_diaria` (apps/api/app/worker.py): 07:40 de São Paulo.
export const HORA_ANALISE = '07:40'

export type Numeros = { decisoes: number; acertos: number; aprovacao?: number | null; ia_enviou: number; equipe_enviou: number }
export type EquipeMetrica = Numeros & { equipe: string; lojas: number }
export type LojaDaEquipe = Numeros & { integration_id: string; loja: string; plataforma: string | null; equipe: string | null }
export type MetricasEquipes = { dias: number; equipes: EquipeMetrica[]; lojas: LojaDaEquipe[]; total: Numeros }
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
// "no prazo" (0–100, do GET /metricas): a régua da Amazon (90%).
export function corPrazo(v: number | null | undefined): string {
  if (v === null || v === undefined) return 'text-muted-foreground'
  if (v >= 90) return 'text-emerald-700 dark:text-emerald-300'
  if (v >= 75) return 'text-amber-700 dark:text-amber-300'
  return 'font-semibold text-red-600 dark:text-red-400'
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

// O select "Ver lojas…": '' = nada aberto; 'todas'; 'sem' (sem equipe); 'e:<nome>'.
export function lojasDaEscolha(lojas: LojaDaEquipe[], escolha: string): LojaDaEquipe[] {
  if (!escolha) return []
  const lista = escolha === 'todas'
    ? lojas
    : escolha === 'sem'
      ? lojas.filter((l) => !l.equipe)
      : lojas.filter((l) => escolha.startsWith('e:') && l.equipe === escolha.slice(2))
  // A pior aprovação primeiro; sem decisão no fim; empate: mais decisões, depois o nome.
  return [...lista].sort((a, b) => {
    const pa = aprovacao(a)
    const pb = aprovacao(b)
    if (pa === null || pb === null) {
      if (pa !== pb) return pa === null ? 1 : -1
    } else if (pa !== pb) {
      return pa - pb
    }
    return b.decisoes - a.decisoes || (a.loja || '').localeCompare(b.loja || '', 'pt-BR')
  })
}
// O par da loja no GET /metricas (o "no prazo" e o nome que a Caixa mostra): pela integração;
// sem ela, o nome e a plataforma.
export function parDaLoja(loja: Pick<LojaDaEquipe, 'integration_id' | 'loja' | 'plataforma'>, metricas: LojaMetrica[]): LojaMetrica | null {
  const porId = metricas.find((m) => !!m.integration_id && m.integration_id === loja.integration_id)
  if (porId) return porId
  const chave = (s: string | null | undefined) => (s || '').trim().toLowerCase()
  return metricas.find((m) => chave(m.conta) === chave(loja.loja) && chave(m.plataforma) === chave(loja.plataforma)) || null
}
</script>

<script setup lang="ts">
import { Loader2, RotateCcw, Sparkles } from 'lucide-vue-next'
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
const escolha = ref('')

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
    const [l, e] = await Promise.all([
      api<MetricasLojas>(ROTAS.lojas(dias.value)),
      visaoAdmin.value ? soDeAdmin<MetricasEquipes>(ROTAS.equipes(dias.value)) : Promise.resolve(null),
    ])
    if (g !== geracao) return
    lojas.value = l
    equipes.value = e
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

// Os cartões: "Todas" (o total) e uma por equipe. Clicar abre as lojas dela.
const cartoes = computed(() => {
  const e = equipes.value
  if (!e) return []
  return [
    { chave: 'todas', nome: 'Todas', lojas: null as number | null, numeros: e.total },
    ...e.equipes.map((x) => ({ chave: `e:${x.equipe}`, nome: x.equipe, lojas: x.lojas as number | null, numeros: x as Numeros })),
  ]
})
const linhas = computed(() =>
  lojasDaEscolha(equipes.value?.lojas || [], escolha.value).map((l) => {
    const par = parDaLoja(l, lojas.value?.lojas || [])
    return { ...l, nome: par?.conta || l.loja, noPrazo: par ? par.pct_no_prazo : null, aprovacao: aprovacao(l) }
  }),
)
function abrirLojas(chave: string) {
  escolha.value = escolha.value === chave ? '' : chave
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
function pctPrazo(v: number | null | undefined) {
  return v === null || v === undefined || Number.isNaN(Number(v)) ? '—' : `${Math.round(Number(v))}%`
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
      <p v-if="analise" class="mt-1.5 whitespace-pre-line text-sm leading-relaxed">{{ analise.texto }}</p>
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
    <div v-if="carregando && !lojas" class="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 class="size-4 animate-spin" /> carregando…</div>

    <!-- ADMIN: por equipe, as % — e as lojas só pelo select -->
    <template v-if="visaoAdmin">
      <section v-if="equipes" class="space-y-2" data-equipes>
        <div class="flex flex-wrap items-baseline gap-2">
          <h3 class="text-sm font-semibold">Aprovação da IA por equipe</h3>
          <span class="text-xs text-muted-foreground">Das decisões da equipe sobre o rascunho da IA, quantas o aprovaram (igual ou só a forma). Sai sozinho o assunto com 75% (85% nos sensíveis).</span>
        </div>
        <div class="grid grid-cols-2 gap-2 md:grid-cols-4">
          <button
            v-for="c in cartoes"
            :key="c.chave"
            type="button"
            class="flex min-w-0 flex-col gap-0.5 rounded-xl border bg-card px-3 py-2.5 text-left transition-colors hover:bg-muted/40"
            :class="escolha === c.chave ? 'ring-2 ring-primary/60' : ''"
            :title="`ver as lojas de ${c.nome === 'Todas' ? 'todas as equipes' : c.nome}`"
            :data-cartao="c.chave"
            @click="abrirLojas(c.chave)"
          >
            <span class="flex items-center justify-between gap-2 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
              <span class="truncate">{{ c.nome }}</span>
              <span v-if="c.lojas !== null" class="shrink-0 normal-case tracking-normal">{{ c.lojas }} loja{{ c.lojas === 1 ? '' : 's' }}</span>
            </span>
            <span class="text-2xl font-semibold tabular-nums leading-8" :class="corAprovacao(aprovacao(c.numeros))">{{ pctAprovacao(aprovacao(c.numeros)) }}</span>
            <span class="text-[11px] leading-4 text-muted-foreground tabular-nums">
              {{ c.numeros.decisoes }} decis{{ c.numeros.decisoes === 1 ? 'ão' : 'ões' }} · {{ c.numeros.ia_enviou }} saiu sozinho · {{ c.numeros.equipe_enviou }} pela equipe
            </span>
          </button>
        </div>
        <p v-if="!equipes.equipes.length" class="text-xs text-muted-foreground">Nenhuma equipe com lojas marcadas ainda (Usuários › Lojas no atendimento).</p>
      </section>

      <section v-if="equipes" class="space-y-2" data-lojas>
        <select v-model="escolha" class="h-8 rounded-md border bg-background px-2 text-xs" aria-label="ver as lojas">
          <option value="">Ver lojas…</option>
          <option value="todas">Todas as equipes</option>
          <option v-for="e in equipes.equipes" :key="e.equipe" :value="`e:${e.equipe}`">{{ e.equipe }}</option>
          <option value="sem">Sem equipe</option>
        </select>
        <div v-if="escolha" class="overflow-x-auto rounded-lg border">
          <table class="w-full min-w-[720px] text-xs">
            <thead class="bg-muted/40 text-[11px] text-muted-foreground">
              <tr class="text-left">
                <th class="px-3 py-2 font-semibold">Loja</th>
                <th class="px-2 py-2 font-semibold">Equipe</th>
                <th class="px-2 py-2 text-right font-semibold" title="das decisões da equipe sobre o rascunho, quantas o aprovaram (igual ou só a forma)">Aprovação da IA</th>
                <th class="px-2 py-2 text-right font-semibold" title="decisões da equipe julgadas no período">Decisões</th>
                <th class="px-2 py-2 text-right font-semibold" title="respostas que a IA mandou sozinha">Saiu sozinho</th>
                <th class="px-2 py-2 text-right font-semibold" title="respostas que a equipe mandou pelo DaVinci">Pela equipe</th>
                <th class="px-3 py-2 text-right font-semibold" title="respondidas dentro do prazo da loja (Amazon cobra 90% em 24 h)">No prazo</th>
              </tr>
            </thead>
            <tbody class="divide-y">
              <tr v-if="!linhas.length">
                <td colspan="7" class="py-8 text-center text-muted-foreground">Nenhuma loja com movimento no período.</td>
              </tr>
              <tr v-for="l in linhas" :key="l.integration_id" class="hover:bg-muted/30" :data-loja="l.integration_id">
                <td class="px-3 py-2">
                  <div class="flex items-center gap-1.5">
                    <AtendimentoPlataforma :codigo="l.plataforma" />
                    <span class="font-medium">{{ l.nome || '—' }}</span>
                  </div>
                </td>
                <td class="px-2 py-2" :class="l.equipe ? '' : 'text-muted-foreground'">{{ l.equipe || 'sem equipe' }}</td>
                <td class="px-2 py-2 text-right font-semibold tabular-nums" :class="corAprovacao(l.aprovacao)" :title="`${l.acertos} de ${l.decisoes} decisões`">{{ pctAprovacao(l.aprovacao) }}</td>
                <td class="px-2 py-2 text-right tabular-nums">{{ l.decisoes }}</td>
                <td class="px-2 py-2 text-right tabular-nums">{{ l.ia_enviou }}</td>
                <td class="px-2 py-2 text-right tabular-nums">{{ l.equipe_enviou }}</td>
                <td class="px-3 py-2 text-right tabular-nums" :class="corPrazo(l.noPrazo)">{{ pctPrazo(l.noPrazo) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
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
