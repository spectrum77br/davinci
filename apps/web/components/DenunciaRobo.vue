<script setup lang="ts">
// ── Ouvidoria › Denúncia › aba Robô (01/10/2026) ───────────────────────────────
// Vinicius: "como eu vou saber em que passo ele está, o que está fazendo".
// O robô do Mac mini da Makisa manda a cada 60 s o estado dele; aqui aparece
// o que precisa de alguém, o que cada frente faz agora e os passos das
// rodadas de hoje (06h, 12h, 18h). Regras em services/denuncia_robo.py.
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { Bot, Globe, Landmark, Mail, Hand, TriangleAlert } from 'lucide-vue-next'
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
type Painel = {
  recebido_em: string | null
  conectado: boolean
  agente: { estado: string; detalhe: string; versao: string | null; desde: string | null }
  frentes: Frente[]
  precisa: Aviso[]
  avisos: Aviso[]
  rodadas: Rodada[]
  denuncias_hoje: { canal: string; enviadas: number; refeitas: number }[]
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
        <span class="text-xs text-muted-foreground">
          notícia do Mac mini {{ haQuanto(painel.recebido_em) }}
          <template v-if="painel.agente.versao"> · robô v{{ painel.agente.versao }}</template>
          <template v-if="painel.denuncias_hoje.length"> · {{ numero(totalHoje) }} denúncia(s) enviada(s) hoje</template>
        </span>
      </div>

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
            O robô pede a checagem 15 min antes e o resto na hora cheia.
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
