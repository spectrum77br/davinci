<script setup lang="ts">
// Aba "Métricas" do Atendimento (25/09/2026): como cada loja está respondendo
// e o que a equipe faz com as sugestões da IA. Serve para duas conversas:
// - prazo: a Amazon cobra 90% das mensagens respondidas em até 24 h corridas
//   (hoje estamos em ~80 h lá); a coluna "no prazo" é o número a acompanhar;
// - confiança na IA: "enviou igual" alto e "descartou" baixo numa categoria é
//   o que sustenta, um dia, liberar o modo Automático nela.
// A unidade do backend é a VEZ que o comprador esperou (uma sequência de
// mensagens dele até a loja responder), não a conversa: uma conversa com
// três idas e vindas conta 3. A tela diz isso, senão o número não bate com a
// Caixa e ninguém confia no "% no prazo".
import { Bot, CheckCheck, Loader2, Pencil, RotateCcw, Sparkles, ThumbsDown, ThumbsUp, Trash2, UserRound } from 'lucide-vue-next'
import { duracao, erroDaApi } from '~/components/AtendimentoPlataforma.vue'

type LojaMetrica = {
  conta: string | null
  plataforma: string
  recebidas: number
  respondidas: number
  mediana_primeira_resposta_min: number | null
  p90_primeira_resposta_min: number | null
  pct_no_prazo: number | null
}
type Metricas = {
  lojas: LojaMetrica[]
  // nota_ok/nota_erro = o 👍/👎 da equipe (o placar do teste em observação:
  // a IA sugere, o Duoke responde, a equipe diz se a IA acertou). Opcionais:
  // a API antiga não manda.
  ia: { rascunhos: number; enviou_igual: number; editou: number; descartou: number; escreveu_do_zero: number; nota_ok?: number; nota_erro?: number }
}

const { api } = useApi()
const dias = ref(7)
const dados = ref<Metricas | null>(null)
const carregando = ref(false)
const erro = ref<string | null>(null)

async function carregar() {
  carregando.value = true
  erro.value = null
  try {
    dados.value = await api<Metricas>(`/api/atendimento/metricas?dias=${dias.value}`)
  } catch (e: any) {
    erro.value = erroDaApi(e, 'Não consegui carregar as métricas').texto
  } finally {
    carregando.value = false
  }
}
onMounted(carregar)
watch(dias, carregar)

// Ordena por quem está pior no prazo — é quem precisa de atenção.
const lojas = computed(() =>
  (dados.value?.lojas || []).slice().sort((a, b) => (a.pct_no_prazo ?? 101) - (b.pct_no_prazo ?? 101) || b.recebidas - a.recebidas),
)
const totais = computed(() => {
  const ls = dados.value?.lojas || []
  return { recebidas: ls.reduce((s, l) => s + (l.recebidas || 0), 0), respondidas: ls.reduce((s, l) => s + (l.respondidas || 0), 0) }
})

// pct_no_prazo vem em 0–100 (a spec chama de "pct").
function pct(v: number | null | undefined) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return null
  return Number(v)
}
function pctCls(v: number | null) {
  if (v === null) return 'text-muted-foreground'
  if (v >= 90) return 'text-emerald-700 dark:text-emerald-300'
  if (v >= 75) return 'text-amber-700 dark:text-amber-300'
  return 'font-semibold text-red-600 dark:text-red-400'
}
function tempo(min: number | null | undefined) {
  return min === null || min === undefined ? '—' : duracao(Number(min))
}

// IA: a fatia de cada ação sobre as sugestões avaliadas.
const ia = computed(() => {
  const x = dados.value?.ia
  if (!x) return null
  const base = (x.enviou_igual || 0) + (x.editou || 0) + (x.descartou || 0) + (x.escreveu_do_zero || 0)
  const p = (n: number) => (base ? `${Math.round((n / base) * 100)}% das usadas` : '—')
  return [
    { label: 'Sugestões geradas', valor: x.rascunhos || 0, icon: Sparkles, hint: dias.value === 1 ? 'nas últimas 24 h' : `nos últimos ${dias.value} dias`, tone: 'default' as const },
    { label: 'Enviou igual', valor: x.enviou_igual || 0, icon: CheckCheck, hint: p(x.enviou_igual || 0), tone: 'success' as const },
    { label: 'Editou antes', valor: x.editou || 0, icon: Pencil, hint: p(x.editou || 0), tone: 'default' as const },
    { label: 'Descartou', valor: x.descartou || 0, icon: Trash2, hint: p(x.descartou || 0), tone: 'warning' as const },
    { label: 'Escreveu do zero', valor: x.escreveu_do_zero || 0, icon: UserRound, hint: p(x.escreveu_do_zero || 0), tone: 'default' as const },
  ]
})
// O placar IA × equipe: das sugestões que alguém avaliou, quantas estavam boas.
const notas = computed(() => {
  const x = dados.value?.ia
  if (!x || (x.nota_ok === undefined && x.nota_erro === undefined)) return null
  const ok = x.nota_ok || 0
  const erro = x.nota_erro || 0
  const total = ok + erro
  const p = (n: number) => (total ? `${Math.round((n / total) * 100)}% das avaliadas` : 'nenhuma avaliada ainda')
  return [
    { label: 'Boa (dava para mandar)', valor: ok, icon: ThumbsUp, hint: p(ok), tone: 'success' as const },
    { label: 'Errou', valor: erro, icon: ThumbsDown, hint: p(erro), tone: erro ? ('danger' as const) : ('default' as const) },
  ]
})
</script>

<template>
  <div class="space-y-4">
    <div class="flex flex-wrap items-center gap-2">
      <span class="text-sm text-muted-foreground">Período:</span>
      <div class="flex gap-1 rounded-md bg-muted/40 p-1">
        <button
          v-for="d in [1, 7, 15, 30]"
          :key="d"
          type="button"
          class="rounded px-2.5 py-1 text-xs transition-colors"
          :class="dias === d ? 'bg-background shadow-sm' : 'text-muted-foreground hover:text-foreground'"
          @click="dias = d"
        >{{ d === 1 ? '24 h' : `${d} dias` }}</button>
      </div>
      <Button size="sm" variant="outline" class="ml-auto" :disabled="carregando" @click="carregar">
        <RotateCcw class="mr-1.5 size-4" :class="{ 'animate-spin': carregando }" /> atualizar
      </Button>
    </div>

    <div v-if="erro" class="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-600 dark:text-red-400">
      {{ erro }} <button type="button" class="ml-2 text-xs underline" @click="carregar">tentar de novo</button>
    </div>

    <div v-if="carregando && !dados" class="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 class="size-4 animate-spin" /> carregando…</div>

    <template v-if="dados">
      <section class="space-y-2">
        <h3 class="flex items-center gap-1.5 text-sm font-semibold"><Bot class="size-4 text-muted-foreground" /> O que a equipe fez com a IA</h3>
        <div v-if="ia" class="grid grid-cols-2 gap-2 md:grid-cols-5">
          <StatCard v-for="c in ia" :key="c.label" :label="c.label" :value="c.valor" :icon="c.icon" :hint="c.hint" :tone="c.tone" compact />
        </div>
      </section>

      <section v-if="notas" class="space-y-2">
        <div class="flex flex-wrap items-baseline gap-2">
          <h3 class="text-sm font-semibold">A IA acertou? (nota da equipe)</h3>
          <span class="text-xs text-muted-foreground">O 👍/👎 dado na sugestão — no modo observação, o placar da IA contra a resposta que a equipe deu pelo Duoke. Conta pela data da nota.</span>
        </div>
        <div class="grid grid-cols-2 gap-2 md:grid-cols-5">
          <StatCard v-for="c in notas" :key="c.label" :label="c.label" :value="c.valor" :icon="c.icon" :hint="c.hint" :tone="c.tone" compact />
        </div>
      </section>

      <section class="space-y-2">
        <div class="flex flex-wrap items-baseline gap-2">
          <h3 class="text-sm font-semibold">Por loja</h3>
          <span class="text-xs text-muted-foreground">{{ totais.recebidas }} vez(es) em que o comprador esperou resposta · {{ totais.respondidas }} respondida(s). Uma "vez" junta as mensagens seguidas do comprador até a loja responder — a mesma conversa pode contar mais de uma. Tempo = da primeira dessas mensagens até a primeira resposta da loja (por qualquer caminho).</span>
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
              <tr v-if="!lojas.length">
                <td colspan="6" class="py-8 text-center text-muted-foreground">Sem conversas no período.</td>
              </tr>
              <tr v-for="(l, i) in lojas" :key="`${l.plataforma}-${l.conta}-${i}`" class="hover:bg-muted/30">
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
                <td class="px-3 py-2 text-right tabular-nums" :class="pctCls(pct(l.pct_no_prazo))">
                  {{ pct(l.pct_no_prazo) === null ? '—' : `${Math.round(pct(l.pct_no_prazo)!)}%` }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>
  </div>
</template>
