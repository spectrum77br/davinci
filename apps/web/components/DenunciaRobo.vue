<script setup lang="ts">
// ── Ouvidoria › Denúncia › aba Robô (01/10/2026) ───────────────────────────────
// Vinicius: "como eu vou saber em que passo ele está, o que está fazendo".
// O robô do Mac mini da Makisa manda a cada 60 s o estado dele; aqui aparece
// o que precisa de alguém, o que cada frente faz agora e os passos das
// rodadas de hoje (06h, 12h, 18h). Regras em services/denuncia_robo.py.
// Botões (01/10, Vinicius: "disparamos o passo 1, acompanhamos… depois o passo
// 2" e "um botão para ligar rotinas automáticas e desligar"): o DaVinci grava o
// comando e a janela do sistema no mini busca a cada 5 s e executa.
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { Bot, Globe, Landmark, Mail, Hand, TriangleAlert, Play, Power, Loader2 } from 'lucide-vue-next'
import { haQuanto, numero } from '~/lib/denuncia'

type Passo = {
  acao: string
  nome: string
  status: 'rodando' | 'fila' | 'concluida' | 'erro' | string
  inicio: string | null
  fim: string | null
  progresso: string
  erro: string
  log: string
  tentativas: number
}
type Rodada = { hora: number; janela: string; estado: string; passos: Passo[] }
type Frente = {
  fila: string
  nome: string
  faz: string
  estado: string
  fazendo: string | null
  desde: string | null
  progresso: string
  n_proximos: number
  proximos: string[]
  presa: boolean
}
type Aviso = { titulo: string; detalhe: string; o_que_fazer: string; desde: string | null }
type Comando = {
  id: number
  tipo: 'automatico' | 'passo' | string
  dados: { ligado?: boolean; acao?: string }
  pedido_por: string | null
  pedido_em: string
  entregue_em: string | null
  ok: boolean | null
  resultado: string | null
  caducou: boolean
}
type Painel = {
  modo: 'manual' | 'automatico'
  recebido_em: string | null
  conectado: boolean
  agente: { estado: string; detalhe: string; versao: string | null; desde: string | null }
  frentes: Frente[]
  precisa: Aviso[]
  avisos: Aviso[]
  rodadas: Rodada[]
  denuncias_hoje: { canal: string; enviadas: number; refeitas: number }[]
  comandos: Comando[]
  passos_disponiveis: { acao: string; nome: string; ordem: number }[]
}

const { api } = useApi()
const painel = ref<Painel | null>(null)
const erro = ref<string | null>(null)

async function carregar() {
  try {
    painel.value = await api<Painel>('/api/denuncia/robo')
    erro.value = null
  } catch (e: any) {
    erro.value = e?.data?.detail?.code || e?.message || 'erro'
  }
}

// o mini manda a cada 60 s; a tela relê a cada 30 s
let timer: ReturnType<typeof setInterval> | null = null
onMounted(() => {
  void carregar()
  timer = setInterval(carregar, 30_000)
})
onUnmounted(() => {
  if (timer) clearInterval(timer)
})
defineExpose({ carregar })

// ── botões ────────────────────────────────────────────────────────────────
const podeMandar = useCan('denuncia', 'edit')
const mandando = ref<string | null>(null)

async function mandar(chave: string, url: string, corpo: Record<string, unknown>) {
  mandando.value = chave
  try {
    await api(url, { method: 'POST', body: corpo })
    await carregar()
    // o mini pega em até 5 s: relê logo pra mostrar "recebido"
    setTimeout(carregar, 7000)
    setTimeout(carregar, 15000)
  } catch (e: any) {
    useToasts().push({ kind: 'error', title: 'Não deu para mandar ao robô', lines: e?.data?.detail?.code || e?.message || 'erro' })
  } finally {
    mandando.value = null
  }
}

function alternarAutomatico() {
  const ligar = painel.value?.modo === 'manual'
  const msg = ligar
    ? 'Ligar a rotina automática? O robô volta a rodar sozinho às 06h, 12h e 18h (checagem 15 min antes) e o ciclo de e-mails a cada 3 h.'
    : 'Desligar a rotina automática? O robô para de começar rodadas sozinho e só roda o passo que for pedido aqui.'
  if (!window.confirm(msg)) return
  void mandar('automatico', '/api/denuncia/robo/automatico', { ligado: ligar })
}

function rodarPasso(acao: string, nome: string) {
  if (!window.confirm(`Rodar agora: ${nome}?`)) return
  void mandar(acao, '/api/denuncia/robo/passo', { acao })
}

/** Último estado do passo hoje (a rodada mais recente em que ele aparece). */
function ultimoDoPasso(acao: string): Passo | null {
  let achado: Passo | null = null
  for (const r of painel.value?.rodadas || []) {
    const p = r.passos.find((x) => x.acao === acao)
    if (p) achado = p
  }
  return achado
}

function pendente(acao: string): boolean {
  return (painel.value?.comandos || []).some((c) => c.tipo === 'passo' && c.dados.acao === acao && !c.entregue_em && !c.caducou)
}

const automaticoPendente = computed(() => (painel.value?.comandos || []).some((c) => c.tipo === 'automatico' && !c.entregue_em && !c.caducou))

function nomeComando(c: Comando): string {
  if (c.tipo === 'automatico') return c.dados.ligado ? 'Ligar a rotina automática' : 'Desligar a rotina automática'
  const p = painel.value?.passos_disponiveis.find((x) => x.acao === c.dados.acao)
  return `Rodar: ${p?.nome || c.dados.acao}`
}

const ICONE: Record<string, any> = { M: Globe, S: Landmark, E: Mail }

/** "2026-10-01T12:07:00-03:00" → "12:07" (Brasília). */
function hora(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return String(iso).slice(11, 16)
  return d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', timeZone: 'America/Sao_Paulo' })
}

/** "2026-10-01 12:05" ou ISO → "01/10 12:05". */
function quando(v: string | null | undefined): string {
  if (!v) return ''
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/.exec(String(v))
  return m ? `${m[3]}/${m[2]} ${m[4]}:${m[5]}` : ''
}

const RODADA: Record<string, [string, string]> = {
  feita: ['feita', 'pill-success'],
  rodando: ['rodando', 'pill-info'],
  na_fila: ['na fila', 'pill-info'],
  com_erro: ['terminou com erro', 'pill-danger'],
  nao_comecou: ['não começou', 'pill-danger'],
  aguardando: ['vai começar', 'pill-warning'],
  futura: ['mais tarde', 'pill-muted'],
  manual: ['sem rodada (modo manual)', 'pill-muted'],
}
const PASSO: Record<string, [string, string]> = {
  concluida: ['feito', 'pill-success'],
  rodando: ['rodando', 'pill-info'],
  fila: ['na fila', 'pill-muted'],
  erro: ['erro', 'pill-danger'],
}

// rodada em andamento (ou a última que teve passos) aparece aberta
const rodadaAberta = computed(() => {
  const rs = painel.value?.rodadas || []
  const ativa = rs.find((r) => r.estado === 'rodando' || r.estado === 'na_fila')
  if (ativa) return ativa.janela
  const comPassos = rs.filter((r) => r.passos.length)
  return comPassos.length ? comPassos[comPassos.length - 1].janela : ''
})

const situacao = computed(() => {
  const p = painel.value
  if (!p) return null
  if (!p.recebido_em) return { txt: 'sem notícia do Mac mini', cls: 'pill-danger' }
  if (!p.conectado) return { txt: `sem notícia do Mac mini ${haQuanto(p.recebido_em)}`, cls: 'pill-danger' }
  if (p.agente.estado === 'erro') return { txt: 'robô parado', cls: 'pill-danger' }
  return { txt: 'robô ligado', cls: 'pill-success' }
})

const totalHoje = computed(() => (painel.value?.denuncias_hoje || []).reduce((s, c) => s + c.enviadas, 0))
</script>

<template>
  <div class="space-y-5">
    <div v-if="erro" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">{{ erro }}</div>
    <div v-if="!painel && !erro" class="text-sm text-muted-foreground">carregando…</div>

    <template v-if="painel">
      <div class="flex flex-wrap items-center gap-2 text-sm">
        <Bot class="size-4 text-muted-foreground" />
        <span v-if="situacao" :class="situacao.cls">{{ situacao.txt }}</span>
        <span
          v-if="painel.modo === 'manual'"
          class="pill-info"
          title="O despertador do robô está desligado (despertador.json no Mac mini): nada começa sozinho, cada passo é disparado à mão."
        >modo manual</span>
        <span class="text-xs text-muted-foreground">
          notícia do Mac mini {{ haQuanto(painel.recebido_em) }}
          <template v-if="painel.agente.versao"> · robô v{{ painel.agente.versao }}</template>
          <template v-if="painel.denuncias_hoje.length"> · {{ numero(totalHoje) }} denúncia(s) enviada(s) hoje</template>
        </span>
      </div>

      <section class="rounded-lg border bg-card">
        <div class="flex flex-wrap items-center gap-3 px-4 py-3">
          <Power class="size-4 text-muted-foreground" />
          <div class="min-w-0 flex-1">
            <div class="text-sm font-medium">
              Rotina automática:
              <span :class="painel.modo === 'manual' ? 'text-muted-foreground' : 'text-emerald-700 dark:text-emerald-400'">
                {{ painel.modo === 'manual' ? 'desligada (modo manual)' : 'ligada' }}
              </span>
            </div>
            <div class="text-xs text-muted-foreground">
              <template v-if="painel.modo === 'manual'">O robô só roda o passo que for pedido abaixo.</template>
              <template v-else>Rodadas às 06h, 12h e 18h (checagem 15 min antes) e ciclo de e-mails a cada 3 h.</template>
            </div>
          </div>
          <Button
            v-if="podeMandar"
            size="sm"
            :variant="painel.modo === 'manual' ? 'default' : 'outline'"
            :disabled="!!mandando || automaticoPendente"
            @click="alternarAutomatico"
          >
            <Loader2 v-if="mandando === 'automatico' || automaticoPendente" class="mr-1.5 size-4 animate-spin" />
            {{ painel.modo === 'manual' ? 'Ligar' : 'Desligar' }}
          </Button>
        </div>
        <details v-if="podeMandar" class="border-t" :open="painel.modo === 'manual'">
          <summary class="cursor-pointer px-4 py-2 text-sm">Rodar um passo agora</summary>
          <ul class="divide-y border-t">
            <li v-for="p in painel.passos_disponiveis" :key="p.acao" class="flex items-center gap-2 px-4 py-1.5 text-sm">
              <span class="w-6 text-right text-xs tabular-nums text-muted-foreground">{{ p.ordem }}</span>
              <span class="flex-1">{{ p.nome }}</span>
              <template v-if="ultimoDoPasso(p.acao)">
                <span class="text-xs" :class="(PASSO[ultimoDoPasso(p.acao)!.status] || ['', 'pill-muted'])[1]">
                  {{ (PASSO[ultimoDoPasso(p.acao)!.status] || [ultimoDoPasso(p.acao)!.status])[0] }}
                </span>
                <span class="w-[86px] text-xs tabular-nums text-muted-foreground">
                  {{ hora(ultimoDoPasso(p.acao)!.fim || ultimoDoPasso(p.acao)!.inicio) }}
                </span>
              </template>
              <span v-else class="w-[86px] text-xs text-muted-foreground">não rodou hoje</span>
              <Button
                size="sm"
                variant="outline"
                class="h-7"
                :disabled="!!mandando || pendente(p.acao) || ['rodando', 'fila'].includes(ultimoDoPasso(p.acao)?.status || '')"
                @click="rodarPasso(p.acao, p.nome)"
              >
                <Loader2 v-if="mandando === p.acao || pendente(p.acao)" class="mr-1 size-3.5 animate-spin" />
                <Play v-else class="mr-1 size-3.5" /> Rodar agora
              </Button>
            </li>
          </ul>
        </details>
        <div v-if="painel.comandos.length" class="border-t px-4 py-2">
          <div class="mb-1 text-xs font-medium text-muted-foreground">Pedidos ao robô</div>
          <ul class="space-y-0.5 text-xs">
            <li v-for="c in painel.comandos.slice(0, 5)" :key="c.id" class="flex flex-wrap gap-x-2">
              <span class="tabular-nums text-muted-foreground">{{ hora(c.pedido_em) }}</span>
              <span>{{ nomeComando(c) }}</span>
              <span class="text-muted-foreground">· {{ c.pedido_por }}</span>
              <span v-if="c.entregue_em" :class="c.ok ? 'text-emerald-700 dark:text-emerald-400' : 'text-red-700 dark:text-red-400'">
                · {{ c.ok ? 'Mac mini recebeu' : 'não deu' }} às {{ hora(c.entregue_em) }}<template v-if="c.resultado"> — {{ c.resultado }}</template>
              </span>
              <span v-else-if="c.caducou" class="text-red-700 dark:text-red-400">· o Mac mini não pegou em 1 h</span>
              <span v-else class="text-amber-700 dark:text-amber-400">· aguardando o Mac mini…</span>
            </li>
          </ul>
        </div>
      </section>

      <section
        v-if="painel.precisa.length"
        class="rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300"
      >
        <h3 class="font-semibold flex items-center gap-1.5 mb-2">
          <Hand class="size-4" /> Precisa de alguém ({{ painel.precisa.length }})
        </h3>
        <ul class="space-y-2">
          <li v-for="(p, i) in painel.precisa" :key="i">
            <div class="font-medium">
              {{ p.titulo }}
              <span v-if="quando(p.desde)" class="font-normal text-xs opacity-75"> · {{ quando(p.desde) }}</span>
            </div>
            <div v-if="p.detalhe" class="text-xs opacity-90">{{ p.detalhe }}</div>
            <div v-if="p.o_que_fazer" class="text-xs"><span class="font-medium">O que fazer:</span> {{ p.o_que_fazer }}</div>
          </li>
        </ul>
      </section>

      <div class="grid gap-3 sm:grid-cols-3">
        <div v-for="f in painel.frentes" :key="f.fila" class="rounded-lg border bg-card px-4 py-3">
          <div class="flex items-center gap-1.5 text-xs text-muted-foreground">
            <component :is="ICONE[f.fila]" class="size-3.5" /> {{ f.nome }}
          </div>
          <div class="mt-1 text-sm font-medium" :class="f.estado === 'erro' ? 'text-red-700 dark:text-red-400' : ''">
            {{ f.fazendo || 'parado' }}
            <span v-if="f.fazendo && f.desde" class="font-normal text-xs text-muted-foreground"> desde {{ hora(f.desde) }}</span>
          </div>
          <div v-if="f.progresso" class="text-xs text-muted-foreground truncate" :title="f.progresso">{{ f.progresso }}</div>
          <div v-if="f.n_proximos" class="text-xs text-muted-foreground">depois: {{ f.proximos.join(', ') }}<template v-if="f.n_proximos > f.proximos.length"> …</template></div>
          <div v-else-if="!f.fazendo" class="text-xs text-muted-foreground">{{ f.faz }}</div>
        </div>
      </div>

      <div class="space-y-3">
        <details
          v-for="r in painel.rodadas"
          :key="r.janela"
          class="rounded-lg border bg-card"
          :open="r.janela === rodadaAberta"
        >
          <summary class="flex cursor-pointer items-center gap-2 px-4 py-2.5 text-sm">
            <span class="font-medium">Rodada das {{ String(r.hora).padStart(2, '0') }}h</span>
            <span :class="(RODADA[r.estado] || ['', 'pill-muted'])[1]">{{ (RODADA[r.estado] || [r.estado])[0] }}</span>
            <span v-if="r.passos.length" class="text-xs text-muted-foreground">
              {{ r.passos.filter((p) => p.status === 'concluida').length }} de {{ r.passos.length }} passos feitos
            </span>
          </summary>
          <div v-if="r.estado === 'nao_comecou'" class="border-t px-4 py-3 text-xs text-red-700 dark:text-red-400">
            Nenhuma varredura foi pedida para esta rodada.<template v-if="r.passos.length"> Abaixo, só o que o robô roda sozinho pelo relógio.</template>
          </div>
          <div v-else-if="!r.passos.length" class="border-t px-4 py-3 text-xs text-muted-foreground">
            <template v-if="painel.modo === 'manual'">Modo manual: o robô só roda o passo que for pedido.</template>
            <template v-else>O robô pede a checagem 15 min antes e o resto na hora cheia.</template>
          </div>
          <ul v-if="r.passos.length" class="divide-y border-t">
            <li v-for="p in r.passos" :key="p.acao" class="px-4 py-2 text-sm">
              <div class="flex flex-wrap items-center gap-2">
                <span class="w-[86px] shrink-0 text-center" :class="(PASSO[p.status] || ['', 'pill-muted'])[1]">
                  {{ (PASSO[p.status] || [p.status])[0] }}
                </span>
                <span class="flex-1 min-w-[140px]">{{ p.nome }}</span>
                <span class="text-xs text-muted-foreground tabular-nums">
                  <template v-if="p.inicio">{{ hora(p.inicio) }}<template v-if="p.fim && p.status !== 'rodando'">–{{ hora(p.fim) }}</template></template>
                  <template v-if="p.tentativas > 1"> · {{ p.tentativas }} tentativas</template>
                </span>
              </div>
              <div v-if="p.status === 'rodando' && p.progresso" class="mt-0.5 pl-[94px] text-xs text-muted-foreground">{{ p.progresso }}</div>
              <div v-if="p.erro" class="mt-0.5 pl-[94px] text-xs text-red-700 dark:text-red-400">{{ p.erro }}</div>
              <details v-if="p.log" class="mt-1 pl-[94px]">
                <summary class="cursor-pointer text-xs text-muted-foreground">ver o fim do log</summary>
                <pre class="mt-1 max-h-64 overflow-auto whitespace-pre-wrap rounded bg-muted p-2 text-[11px] leading-snug">{{ p.log }}</pre>
              </details>
            </li>
          </ul>
        </details>
      </div>

      <details v-if="painel.avisos.length" class="rounded-lg border bg-card">
        <summary class="flex cursor-pointer items-center gap-1.5 px-4 py-2.5 text-sm">
          <TriangleAlert class="size-4 text-amber-600" /> Avisos ({{ painel.avisos.length }}) — não param o robô
        </summary>
        <ul class="divide-y border-t">
          <li v-for="(a, i) in painel.avisos" :key="i" class="px-4 py-2 text-sm">
            <div class="font-medium">
              {{ a.titulo }}
              <span v-if="quando(a.desde)" class="font-normal text-xs text-muted-foreground"> · {{ quando(a.desde) }}</span>
            </div>
            <div v-if="a.detalhe" class="text-xs text-muted-foreground">{{ a.detalhe }}</div>
            <div v-if="a.o_que_fazer" class="text-xs">{{ a.o_que_fazer }}</div>
          </li>
        </ul>
      </details>
    </template>
  </div>
</template>
