<script setup lang="ts">
// ── Ouvidoria › Denúncia › aba Robô (01/10/2026) ───────────────────────────────
// Vinicius: "como eu vou saber em que passo ele está, o que está fazendo" e,
// depois, "tá meio bagunçado… igual fizemos no robô da Ouvidoria, tem as
// ocorrências". Mesmo desenho do Ouvidoria › Robôs: resumo no topo e três
// abas — Passos (os 12 passos com a última vez de hoje e "Rodar"),
// Ocorrências (o que precisa de alguém; "Tratado" tira da lista) e Pedidos ao
// robô (os cliques e a resposta do Mac mini).
// O robô do Mac mini manda o estado a cada 60 s; os botões viram comando que
// a janela do sistema no mini busca a cada 5 s. Regras em services/denuncia_robo.py.
// 02/10 (Vinicius): cada passo com a chave liga/desliga (a dos Robôs da Ouvidoria)
// e os seus horários — "6 da manhã roda passo 1, 2 e 3… passo 9 às 23 horas".
// Some o horário fixo das rodadas (06/12/18h): o despertador do mini segue esta agenda.
// 05/10 (Vinicius): "um relatório no final do dia… quantos anúncios ele achou, quantos denunciou na
// loja, quantos abriu reclamação na Anatel" — vira linha nas Ocorrências depois da meia-noite (até
// alguém marcar "Lido"); "Hoje até agora" e os dias anteriores abrem na gaveta (DenunciaRelatorio).
// 05/10 (Cairo): "isso coloca para avisar no Threema… só o Cairo recebe" — captcha na tela, robô parado,
// mini sem notícia e SEI pedindo código viram Threema (worker a cada 2 min, services/denuncia_robo_aviso);
// "Quem recebe o aviso" nas Ocorrências escolhe quem (cadastro Informar `denuncia_robo`).
// 06/10 (Vinicius): o relatório do dia também vai pelo Threema, às 7h (services/denuncia_relatorio_threema);
// "Quem recebe o relatório" = cadastro `denuncia_relatorio` (nasce com Cairo, harry potter e Roma).
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { AlertCircle, Bell, Bot, ChevronDown, ChevronRight, FileText, Flag, Loader2, Play, Plus, Power, Check, X } from 'lucide-vue-next'
import { haQuanto, numero } from '~/lib/denuncia'

type Ultima = {
  status: 'rodando' | 'fila' | 'concluida' | 'erro' | string
  inicio: string | null
  fim: string | null
  progresso: string
  erro: string
  log: string
  vezes: number
}
type Agenda = { ligado: boolean; horarios: string[]; no_robo: boolean | null }
// 07/10/2026 (Vinicius: "até agora não entendo o que cada passo faz"): ao abrir o passo, o passo a passo, quem faz (cada
// perfil pelo nome), quem confere depois e o que está sendo consertado — o log técnico fica num link pequeno
type Explicacao = { passos: string[]; quem: string[]; confere: string[]; consertando: string[] }
type PassoLinha = {
  acao: string; ordem: number; nome: string; onde: string; faz: string; ultima: Ultima | null; agenda: Agenda
  explicacao: Explicacao | null; em_criacao: boolean
}
type Frente = { fila: string; nome: string; fazendo: string | null; acao: string | null; desde: string | null; progresso: string }
type Ocorrencia = {
  chave: string
  titulo: string
  detalhe: string
  o_que_fazer: string
  quando: string | null
  tipo: 'pessoa' | 'aviso'
  origem: 'agora' | 'robo'
  robo_chave: string | null
}
type Comando = {
  id: number
  tipo: 'automatico' | 'passo' | 'resolver' | 'agenda' | string
  dados: { ligado?: boolean; acao?: string; chave?: string; loja?: string; shop_id?: string; codigo?: string; horarios?: string[] }
  pedido_por: string | null
  pedido_em: string
  entregue_em: string | null
  ok: boolean | null
  resultado: string | null
  caducou: boolean
}
// relatório do dia fechado e ainda não lido (05/10)
type RelPendente = { dia: string; fechado_em: string | null; achou: number; denunciou: number; anatel: number; removidos: number }
type Painel = {
  modo: 'manual' | 'automatico'
  recebido_em: string | null
  conectado: boolean
  agente: { estado: string; detalhe: string; versao: string | null; desde: string | null }
  frentes: Frente[]
  passos: PassoLinha[]
  ocorrencias: Ocorrencia[]
  denuncias_hoje: { canal: string; enviadas: number; refeitas: number }[]
  comandos: Comando[]
  relatorios?: RelPendente[]
}
type Aba = 'passos' | 'ocorrencias' | 'pedidos'

const { api } = useApi()
const painel = ref<Painel | null>(null)
const erro = ref<string | null>(null)
const aba = ref<Aba>('passos')
const aberto = ref<string | null>(null)

async function carregar() {
  try {
    painel.value = await api<Painel>('/api/denuncia/robo')
    erro.value = null
  } catch (e: any) {
    erro.value = e?.data?.detail?.code || e?.message || 'erro'
  }
}

let timer: ReturnType<typeof setInterval> | null = null
const route = useRoute()
onMounted(() => {
  void carregar()
  timer = setInterval(carregar, 30_000)
  // 06/10: o link do relatório no Threema (?aba=robo&relatorio=AAAA-MM-DD) já abre a gaveta do dia
  const rel = String(route.query.relatorio || '')
  if (/^\d{4}-\d{2}-\d{2}$/.test(rel)) {
    aba.value = 'ocorrencias'
    abrirRelatorio(rel)
  }
})
onUnmounted(() => {
  if (timer) clearInterval(timer)
})
defineExpose({ carregar })

// ── formatos ─────────────────────────────────────────────────────────────────
const hojeBr = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Sao_Paulo' }).format(new Date())

/** ISO ou "2026-09-30 12:26" → "12:26" (hoje) ou "30/09 12:26". */
function quando(v: string | null | undefined): string {
  if (!v) return ''
  const s = String(v)
  if (/[T ]\d{2}:\d{2}/.test(s) && /[+-]\d{2}:\d{2}$|Z$/.test(s)) {
    const d = new Date(s)
    if (!Number.isNaN(d.getTime())) {
      const dia = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Sao_Paulo' }).format(d)
      const h = d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', timeZone: 'America/Sao_Paulo' })
      return dia === hojeBr() ? h : `${dia.slice(8, 10)}/${dia.slice(5, 7)} ${h}`
    }
  }
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T_](\d{2}):?(\d{2})?/.exec(s)
  if (!m) return s
  const h = m[5] ? `${m[4]}:${m[5]}` : `${m[4]}h`
  return `${m[1]}-${m[2]}-${m[3]}` === hojeBr() ? h : `${m[3]}/${m[2]} ${h}`
}

const STATUS: Record<string, [string, string]> = {
  concluida: ['feito', 'pill-success'],
  rodando: ['rodando', 'pill-info'],
  fila: ['na fila', 'pill-muted'],
  erro: ['erro', 'pill-danger'],
}

// ── resumo do topo ──────────────────────────────────────────────────────────
const situacao = computed(() => {
  const p = painel.value
  if (!p || !p.recebido_em) return { txt: 'sem notícia', tone: 'danger' as const }
  if (!p.conectado) return { txt: 'sem notícia', tone: 'danger' as const }
  if (p.agente.estado === 'erro') return { txt: 'parado', tone: 'danger' as const }
  return { txt: 'ligado', tone: 'success' as const }
})
const rodando = computed(() => (painel.value?.frentes || []).filter((f) => f.fazendo))
const pessoa = computed(() => (painel.value?.ocorrencias || []).filter((o) => o.tipo === 'pessoa').length)
const totalHoje = computed(() => (painel.value?.denuncias_hoje || []).reduce((s, c) => s + c.enviadas, 0))
const porCanalHoje = computed(() =>
  (painel.value?.denuncias_hoje || []).filter((c) => c.enviadas).map((c) => `${c.canal} ${c.enviadas}`).join(' · ') || 'nenhuma ainda',
)

// ── botões ──────────────────────────────────────────────────────────────────
const podeMandar = useCan('denuncia', 'edit')
// Quem recebe o aviso no Threema: admin ou o Cairo (espelho do backend: _EMAILS_EXTRAS["denuncia_robo"]
// em routers/informar.py — mudou aqui, muda lá).
const auth = useAuthStore()
const AVISO_ROBO_USERS = ['sa.geral@tutamail.com']
const podeAviso = computed(() => {
  const email = (auth.user?.email || '').trim().toLowerCase()
  return auth.user?.role === 'admin' || (!!email && AVISO_ROBO_USERS.includes(email))
})
const avisoAberto = ref(false)
const relatorioThreemaAberto = ref(false)
const mandando = ref<string | null>(null)

async function mandar(chave: string, url: string, corpo: Record<string, unknown>) {
  mandando.value = chave
  try {
    await api(url, { method: 'POST', body: corpo })
    await carregar()
    // o mini pega em até 5 s: relê pra mostrar a resposta
    setTimeout(carregar, 7000)
    setTimeout(carregar, 15000)
  } catch (e: any) {
    useToasts().push({ kind: 'error', title: 'Não deu para mandar ao robô', lines: e?.data?.detail?.code || e?.message || 'erro' })
  } finally {
    mandando.value = null
  }
}

const automaticoPendente = computed(() => (painel.value?.comandos || []).some((c) => c.tipo === 'automatico' && !c.entregue_em && !c.caducou))

function alternarAutomatico() {
  const ligar = painel.value?.modo === 'manual'
  const msg = ligar
    ? 'Ligar a rotina automática? O robô volta a rodar sozinho os passos ligados, nos horários de cada um.'
    : 'Desligar a rotina automática? O robô para de começar passos sozinho (a agenda fica guardada) e só roda o que for pedido aqui.'
  if (!window.confirm(msg)) return
  void mandar('automatico', '/api/denuncia/robo/automatico', { ligado: ligar })
}

function passoPendente(acao: string): boolean {
  return (painel.value?.comandos || []).some((c) => c.tipo === 'passo' && c.dados.acao === acao && !c.entregue_em && !c.caducou)
}

function podeRodar(p: PassoLinha): boolean {
  if (p.em_criacao) return false
  return !mandando.value && !passoPendente(p.acao) && !['rodando', 'fila'].includes(p.ultima?.status || '')
}

function rodar(p: PassoLinha) {
  if (!window.confirm(`Rodar agora: ${p.ordem} · ${p.nome}?\n${p.faz}`)) return
  void mandar(p.acao, '/api/denuncia/robo/passo', { acao: p.acao })
}

// "Tratado": o que o robô registrou e o que é de conta/fila. O estado crítico do
// momento (mini sem notícia, robô parado, SEI esperando, rodada que não
// começou) só sai quando o problema some.
function podeTratar(o: Ocorrencia): boolean {
  if (o.origem === 'robo') return true
  return !['agora:mini', 'agora:agente', 'agora:sei'].includes(o.chave) && !o.chave.startsWith('agora:agenda')
}

// ── agenda (02/10): chave liga/desliga e horários de cada passo ────────────
const mudandoAgenda = ref<string | null>(null)
const novoHorario = ref<Record<string, string>>({})
const adicionando = ref<string | null>(null)

async function salvarAgenda(p: PassoLinha, corpo: { ligado?: boolean; horarios?: string[] }) {
  mudandoAgenda.value = p.acao
  try {
    const r = await api<{ ligado: boolean; horarios: string[] }>(`/api/denuncia/robo/agenda/${p.acao}`, { method: 'PUT', body: corpo })
    p.agenda = { ...p.agenda, ...r, no_robo: false }   // até o mini confirmar
    setTimeout(carregar, 7000)
    setTimeout(carregar, 70000)   // o mini manda o estado a cada minuto
  } catch (e: any) {
    const code = e?.data?.detail?.code
    useToasts().push({ kind: 'error', title: 'Não deu para salvar a agenda', lines: code === 'denuncia_robo_horario_invalido' ? 'horário inválido (use HH:MM)' : code || e?.message || 'erro' })
  } finally {
    mudandoAgenda.value = null
  }
}

function alternarLigado(p: PassoLinha) {
  const ligar = !p.agenda.ligado
  if (!ligar && !window.confirm(`Desligar ${p.ordem} · ${p.nome}?\n\nEle para de rodar sozinho nos horários (os horários ficam guardados). O "Rodar" continua funcionando.`)) return
  if (ligar && !p.agenda.horarios.length && !window.confirm(`Ligar ${p.ordem} · ${p.nome} sem horário?\n\nSem horário ele não roda sozinho — adicione um horário em seguida.`)) return
  void salvarAgenda(p, { ligado: ligar })
}

function adicionarHorario(p: PassoLinha) {
  const h = (novoHorario.value[p.acao] || '').slice(0, 5)
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(h)) return
  adicionando.value = null
  novoHorario.value[p.acao] = ''
  if (p.agenda.horarios.includes(h)) return
  void salvarAgenda(p, { horarios: [...p.agenda.horarios, h].sort() })
}

function tirarHorario(p: PassoLinha, h: string) {
  if (!window.confirm(`Tirar o horário ${h} de ${p.ordem} · ${p.nome}?`)) return
  void salvarAgenda(p, { horarios: p.agenda.horarios.filter((x) => x !== h) })
}

function tratar(o: Ocorrencia) {
  void mandar(o.chave, '/api/denuncia/robo/ocorrencias/tratar', { chave: o.chave, titulo: o.titulo, robo_chave: o.robo_chave })
}

function nomeComando(c: Comando): string {
  if (c.tipo === 'automatico') return c.dados.ligado ? 'Ligar a rotina automática' : 'Desligar a rotina automática'
  if (c.tipo === 'resolver') return `Tratado: ${c.dados.chave}`
  if (c.tipo === 'criar_caso') return `Criar o caso da loja ${c.dados.loja || c.dados.shop_id}`
  if (c.tipo === 'excluir_caso') return `Excluir o ${c.dados.codigo || 'caso'}`
  const p = painel.value?.passos.find((x) => x.acao === c.dados.acao)
  if (c.tipo === 'agenda') {
    const nome = p ? `${p.ordem} · ${p.nome}` : c.dados.acao
    return `Agenda: ${nome} — ${c.dados.ligado ? 'ligado' : 'desligado'}${(c.dados.horarios || []).length ? ' às ' + (c.dados.horarios || []).join(', ') : ', sem horário'}`
  }
  return p ? `Rodar ${p.ordem} · ${p.nome}` : `Rodar ${c.dados.acao}`
}

function alternarLinha(acao: string) {
  aberto.value = aberto.value === acao ? null : acao
}

// ── relatório do dia (05/10) ───────────────────────────────────────────────
const relatorios = computed(() => painel.value?.relatorios || [])
const totalOcorrencias = computed(() => (painel.value?.ocorrencias.length || 0) + relatorios.value.length)
const relDia = ref<string | null>(null)
const relAberto = ref(false)
const anteriores = ref<{ dia: string; lido_em: string | null; achou: number; denunciou: number; anatel: number }[] | null>(null)
const escolhido = ref('')
const diaBr = (d: string) => `${d.slice(8, 10)}/${d.slice(5, 7)}`

function abrirRelatorio(dia: string) {
  relDia.value = dia
  relAberto.value = true
}

async function carregarAnteriores() {
  if (anteriores.value) return
  try {
    anteriores.value = (await api<{ dias: NonNullable<typeof anteriores.value> }>('/api/denuncia/relatorios')).dias
  } catch {
    anteriores.value = []
  }
}

function escolherAnterior() {
  if (escolhido.value) abrirRelatorio(escolhido.value)
  escolhido.value = ''
}

async function marcarLido(r: RelPendente) {
  mandando.value = `rel:${r.dia}`
  try {
    await api(`/api/denuncia/relatorios/${r.dia}/lido`, { method: 'POST' })
    anteriores.value = null
    await carregar()
  } catch (e: any) {
    useToasts().push({ kind: 'error', title: 'Não deu para marcar como lido', lines: e?.data?.detail?.code || e?.message || 'erro' })
  } finally {
    mandando.value = null
  }
}

function relLido() {
  anteriores.value = null
  void carregar()
}
</script>

<template>
  <div class="space-y-4">
    <div v-if="erro" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">{{ erro }}</div>
    <div v-if="!painel && !erro" class="text-sm text-muted-foreground">carregando…</div>

    <template v-if="painel">
      <!-- resumo -->
      <div class="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-5">
        <StatCard
          compact
          label="Robô"
          :value="situacao!.txt"
          :tone="situacao!.tone"
          :icon="Bot"
          :hint="`${painel.agente.versao ? 'v' + painel.agente.versao + ' · ' : ''}notícia ${haQuanto(painel.recebido_em)}`"
        />
        <div class="flex flex-col gap-0.5 rounded-lg border bg-card px-3 py-2">
          <div class="flex items-center gap-1.5">
            <Power class="size-3.5 text-muted-foreground" />
            <span class="truncate text-[10px] font-medium uppercase tracking-wider text-muted-foreground">Rotina automática</span>
          </div>
          <div class="flex items-center justify-between gap-2">
            <span class="text-lg font-semibold leading-6" :class="painel.modo === 'manual' ? '' : 'text-emerald-600 dark:text-emerald-400'">
              {{ painel.modo === 'manual' ? 'manual' : 'ligada' }}
            </span>
            <Button
              v-if="podeMandar"
              size="sm"
              variant="outline"
              class="h-6 px-2 text-xs"
              :disabled="!!mandando || automaticoPendente"
              @click="alternarAutomatico"
            >
              <Loader2 v-if="mandando === 'automatico' || automaticoPendente" class="mr-1 size-3 animate-spin" />
              {{ painel.modo === 'manual' ? 'ligar' : 'desligar' }}
            </Button>
          </div>
          <div class="truncate text-[11px] leading-4 text-muted-foreground">
            {{ painel.modo === 'manual' ? 'só roda o passo pedido' : 'segue a agenda de cada passo' }}
          </div>
        </div>
        <StatCard
          compact
          label="Rodando agora"
          :value="rodando.length ? rodando.map((f) => f.fazendo).join(' + ') : 'nada'"
          :icon="Play"
          :hint="rodando.length ? [rodando[0].desde ? 'desde ' + quando(rodando[0].desde) : '', rodando[0].progresso].filter(Boolean).join(' · ') : 'robô livre'"
        />
        <StatCard
          compact
          label="Ocorrências"
          :value="painel.ocorrencias.length"
          :tone="pessoa ? 'danger' : 'default'"
          :icon="AlertCircle"
          :hint="pessoa ? `${pessoa} precisa${pessoa > 1 ? 'm' : ''} de pessoa` : 'nada precisa de pessoa'"
        />
        <StatCard compact label="Denúncias hoje" :value="numero(totalHoje)" :icon="Flag" :hint="porCanalHoje" />
      </div>

      <!-- abas -->
      <div class="flex items-center gap-1 border-b border-border">
        <button
          type="button"
          class="-mb-px inline-flex h-9 items-center gap-1.5 border-b-2 px-3 text-sm font-medium transition-colors"
          :class="aba === 'passos' ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'"
          @click="aba = 'passos'"
        >
          Passos
        </button>
        <button
          type="button"
          class="-mb-px inline-flex h-9 items-center gap-1.5 border-b-2 px-3 text-sm font-medium transition-colors"
          :class="aba === 'ocorrencias' ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'"
          @click="aba = 'ocorrencias'"
        >
          Ocorrências
          <span
            v-if="totalOcorrencias"
            class="rounded-full px-1.5 text-[11px] font-semibold tabular-nums"
            :class="pessoa ? 'bg-red-100 text-red-700 dark:bg-red-950/50 dark:text-red-300' : 'bg-muted text-muted-foreground'"
          >{{ totalOcorrencias }}</span>
        </button>
        <button
          type="button"
          class="-mb-px inline-flex h-9 items-center gap-1.5 border-b-2 px-3 text-sm font-medium transition-colors"
          :class="aba === 'pedidos' ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'"
          @click="aba = 'pedidos'"
        >
          Pedidos ao robô
        </button>
      </div>

      <!-- ══ Passos ══ -->
      <div v-if="aba === 'passos'" class="table-card w-fit max-w-full overflow-x-auto">
        <!-- table-fixed: um erro comprido fica cortado (inteiro ao abrir a linha) e o "Rodar" não sai da tela -->
        <!-- 02/10 (Vinicius): sem buracos — cada coluna com a sua largura e a tabela (e o quadro)
             do tamanho delas; texto comprido do Passo fica com "…" (inteiro no title e ao abrir) -->
        <table class="table-fixed" style="width: 1310px">
          <colgroup>
            <col class="w-[44px]">
            <col class="w-[400px]">
            <col class="w-[140px]">
            <col class="w-[72px]">
            <col class="w-[150px]">
            <col class="w-[92px]">
            <col class="w-[320px]">
            <col class="w-[92px]">
          </colgroup>
          <thead>
            <tr>
              <th>#</th>
              <th>Passo</th>
              <th class="whitespace-nowrap">Onde roda</th>
              <th>Ligado</th>
              <th class="text-center">Horários</th>
              <th class="whitespace-nowrap">Última vez</th>
              <th>Resultado</th>
              <th />
            </tr>
          </thead>
          <tbody>
            <template v-for="p in painel.passos" :key="p.acao">
              <tr class="cursor-pointer" @click="alternarLinha(p.acao)">
                <td class="text-xs tabular-nums text-muted-foreground">
                  <span class="inline-flex items-center gap-0.5">
                    <ChevronDown v-if="aberto === p.acao" class="size-3" />
                    <ChevronRight v-else class="size-3" />
                    {{ p.ordem }}
                  </span>
                </td>
                <td>
                  <div class="flex items-center gap-2 text-sm font-medium" :class="p.agenda.ligado ? '' : 'text-muted-foreground'">
                    <span class="truncate">{{ p.nome }}</span>
                    <span v-if="p.em_criacao" class="pill-warning shrink-0 text-[10px]">em criação</span>
                  </div>
                  <div class="truncate text-[11px] text-muted-foreground" :title="p.faz">{{ p.faz }}</div>
                </td>
                <!-- 03/10 (Vinicius): a coluna "Onde roda" voltou (perfil 50 + 148 + celular…) -->
                <td class="text-xs text-muted-foreground">{{ p.onde }}</td>
                <!-- chave liga/desliga: o mesmo desenho dos Robôs da Ouvidoria (verde = ligado) -->
                <td @click.stop>
                  <div class="flex flex-col items-start gap-0.5">
                    <button
                      type="button"
                      role="switch"
                      :aria-checked="p.agenda.ligado"
                      :aria-label="`${p.agenda.ligado ? 'Desligar' : 'Ligar'} ${p.nome}`"
                      :disabled="!podeMandar || mudandoAgenda === p.acao || p.em_criacao"
                      :title="p.agenda.ligado ? 'Ligado: roda sozinho nos horários' : 'Desligado: só roda pelo Rodar'"
                      class="relative h-5 w-9 shrink-0 rounded-full transition-colors disabled:cursor-default"
                      :class="[p.agenda.ligado ? 'bg-emerald-500' : 'bg-gray-300 dark:bg-gray-600', mudandoAgenda === p.acao ? 'animate-pulse' : '', podeMandar ? 'cursor-pointer' : 'opacity-60']"
                      @click="alternarLigado(p)"
                    >
                      <span
                        class="pointer-events-none absolute top-0.5 left-0 inline-block size-4 rounded-full bg-white shadow transition-transform"
                        :class="p.agenda.ligado ? 'translate-x-[18px]' : 'translate-x-0.5'"
                      />
                    </button>
                    <span class="text-[11px] text-muted-foreground whitespace-nowrap">{{ p.agenda.ligado ? 'ligado' : 'desligado' }}</span>
                  </div>
                </td>
                <td class="text-xs" @click.stop>
                  <div class="flex flex-wrap items-center justify-center gap-1">
                    <span
                      v-for="h in p.agenda.horarios"
                      :key="h"
                      class="tabular-nums"
                      :class="p.agenda.ligado ? 'pill-success' : 'pill-muted'"
                    >
                      {{ h }}
                      <button
                        v-if="podeMandar"
                        type="button"
                        class="-mr-0.5 rounded-full opacity-60 hover:text-red-600 hover:opacity-100"
                        :disabled="mudandoAgenda === p.acao"
                        :title="`tirar ${h}`"
                        @click="tirarHorario(p, h)"
                      ><X class="size-2.5" /></button>
                    </span>
                    <template v-if="podeMandar">
                      <span v-if="adicionando === p.acao" class="inline-flex items-center gap-1">
                        <input
                          v-model="novoHorario[p.acao]"
                          type="time"
                          class="h-6 w-[76px] rounded-full border bg-background px-2 text-[11px] tabular-nums"
                          @keydown.enter="adicionarHorario(p)"
                          @keydown.esc="adicionando = null"
                        >
                        <Button size="sm" variant="outline" class="h-6 rounded-full px-2 text-[11px]" :disabled="!novoHorario[p.acao]" @click="adicionarHorario(p)">ok</Button>
                      </span>
                      <button
                        v-else
                        type="button"
                        class="inline-flex size-5 items-center justify-center rounded-full border border-dashed text-muted-foreground hover:text-foreground"
                        :disabled="mudandoAgenda === p.acao"
                        title="adicionar um horário"
                        @click="adicionando = p.acao"
                      ><Plus class="size-2.5" /></button>
                    </template>
                    <span v-if="!p.agenda.horarios.length && !podeMandar" class="text-muted-foreground">—</span>
                  </div>
                  <div v-if="p.agenda.no_robo === false" class="mt-0.5 flex items-center justify-center gap-1 text-[10px] text-amber-700 dark:text-amber-400">
                    <Loader2 class="size-2.5 animate-spin" /> esperando o robô aplicar
                  </div>
                </td>
                <td class="text-xs tabular-nums whitespace-nowrap">
                  <template v-if="p.ultima">{{ quando(p.ultima.fim && p.ultima.status !== 'rodando' ? p.ultima.fim : p.ultima.inicio) || '—' }}</template>
                  <span v-else class="text-muted-foreground">—</span>
                </td>
                <td class="text-xs">
                  <div v-if="p.ultima" class="flex min-w-0 items-center gap-2">
                    <span class="shrink-0" :class="(STATUS[p.ultima.status] || ['', 'pill-muted'])[1]">{{ (STATUS[p.ultima.status] || [p.ultima.status])[0] }}</span>
                    <span
                      class="min-w-0 truncate"
                      :class="p.ultima.status === 'erro' ? 'text-red-700 dark:text-red-400' : 'text-muted-foreground'"
                      :title="p.ultima.erro || p.ultima.progresso"
                    >{{ p.ultima.erro || p.ultima.progresso }}</span>
                  </div>
                  <span v-else class="text-muted-foreground">não rodou hoje</span>
                </td>
                <td class="text-right" @click.stop>
                  <Button
                    v-if="podeMandar"
                    size="sm"
                    variant="outline"
                    class="h-7"
                    :disabled="!podeRodar(p)"
                    :title="p.em_criacao ? 'em criação: o robô ainda não tem este passo' : podeRodar(p) ? 'pedir este passo ao robô agora' : 'já está na fila ou rodando'"
                    @click="rodar(p)"
                  >
                    <Loader2 v-if="mandando === p.acao || passoPendente(p.acao)" class="mr-1 size-3.5 animate-spin" />
                    <Play v-else class="mr-1 size-3.5" /> Rodar
                  </Button>
                </td>
              </tr>
              <tr v-if="aberto === p.acao">
                <td />
                <td colspan="7" class="bg-muted/30">
                  <div class="grid gap-3 py-2 text-xs sm:grid-cols-2" style="max-width: 1180px">
                    <template v-if="p.explicacao">
                      <section class="space-y-1">
                        <div class="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">O que faz</div>
                        <ol class="list-decimal space-y-0.5 pl-4">
                          <li v-for="(t, i) in p.explicacao.passos" :key="i">{{ t }}</li>
                        </ol>
                      </section>
                      <section class="space-y-1">
                        <div class="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">Quem faz</div>
                        <ul class="space-y-0.5">
                          <li v-for="(t, i) in p.explicacao.quem" :key="i" class="flex gap-1.5"><span class="text-muted-foreground">•</span>{{ t }}</li>
                        </ul>
                        <div class="pt-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">Quem confere depois</div>
                        <ul v-if="p.explicacao.confere.length" class="space-y-0.5">
                          <li v-for="(t, i) in p.explicacao.confere" :key="i" class="flex gap-1.5"><span class="text-muted-foreground">→</span>{{ t }}</li>
                        </ul>
                        <div v-else class="text-muted-foreground">—</div>
                      </section>
                      <section v-if="p.explicacao.consertando.length" class="space-y-1 sm:col-span-2">
                        <div class="text-[10px] font-semibold uppercase tracking-wider text-amber-700 dark:text-amber-400">Sendo consertado</div>
                        <ul class="space-y-0.5">
                          <li v-for="(t, i) in p.explicacao.consertando" :key="i" class="flex gap-1.5"><span class="text-amber-600">•</span>{{ t }}</li>
                        </ul>
                      </section>
                    </template>
                    <div v-else class="text-muted-foreground sm:col-span-2">{{ p.faz }} · roda em: {{ p.onde }}</div>
                    <section class="space-y-1 border-t pt-2 sm:col-span-2">
                      <div class="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">Hoje</div>
                      <template v-if="p.ultima">
                        <div>
                          <span :class="(STATUS[p.ultima.status] || ['', 'pill-muted'])[1]">{{ (STATUS[p.ultima.status] || [p.ultima.status])[0] }}</span>
                          · {{ p.ultima.vezes }} vez{{ p.ultima.vezes > 1 ? 'es' : '' }} · última de {{ quando(p.ultima.inicio) || '—' }}<template v-if="p.ultima.fim && p.ultima.status !== 'rodando'"> a {{ quando(p.ultima.fim) }}</template>
                          <template v-if="p.ultima.progresso && !p.ultima.erro"> · {{ p.ultima.progresso }}</template>
                        </div>
                        <div v-if="p.ultima.erro" class="whitespace-pre-wrap break-words text-red-700 dark:text-red-400">{{ p.ultima.erro }}</div>
                        <!-- log técnico: pequeno e fechado (Vinicius: "só você usa, algo bem restrito que você aperta e abre") -->
                        <details v-if="p.ultima.log" class="pt-1">
                          <summary class="cursor-pointer select-none text-[10px] text-muted-foreground/70 hover:text-muted-foreground">log técnico</summary>
                          <pre class="mt-1 max-h-72 overflow-auto whitespace-pre-wrap rounded border bg-background p-2 text-[11px] leading-snug">{{ p.ultima.log }}</pre>
                        </details>
                      </template>
                      <div v-else class="text-muted-foreground">{{ p.em_criacao ? 'Em criação: o robô ainda não roda este passo.' : 'Não rodou hoje.' }}</div>
                    </section>
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>

      <!-- ══ Ocorrências ══ -->
      <div v-else-if="aba === 'ocorrencias'" class="space-y-2">
        <!-- 05/10: relatório do dia — hoje ao vivo e os dias anteriores -->
        <div class="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="outline" class="h-8" @click="abrirRelatorio(hojeBr())">
            <FileText class="mr-1 size-3.5" /> Relatório de hoje (até agora)
          </Button>
          <select
            v-model="escolhido"
            class="h-8 rounded-md border bg-background px-2 text-sm"
            aria-label="Relatórios anteriores"
            @focus="carregarAnteriores"
            @pointerdown="carregarAnteriores"
            @change="escolherAnterior"
          >
            <option value="">Relatórios anteriores…</option>
            <option v-if="anteriores && !anteriores.length" value="" disabled>nenhum ainda</option>
            <option v-for="r in anteriores || []" :key="r.dia" :value="r.dia">
              {{ diaBr(r.dia) }} — achou {{ r.achou }} · denunciou {{ r.denunciou }} · Anatel {{ r.anatel }}{{ r.lido_em ? '' : ' · não lido' }}
            </option>
          </select>
          <!-- qualquer dia (antes de 05/10 sai só com os números: o robô não era anotado) -->
          <input
            type="date"
            class="h-8 rounded-md border bg-background px-2 text-sm"
            aria-label="Relatório de outro dia"
            title="Relatório de outro dia"
            min="2026-09-01"
            :max="hojeBr()"
            @change="(e) => { const v = (e.target as HTMLInputElement).value; if (v) abrirRelatorio(v) }"
          >
          <Button v-if="podeAviso" size="sm" variant="outline" class="ml-auto h-8" @click="relatorioThreemaAberto = true">
            <FileText class="mr-1 size-3.5" /> Quem recebe o relatório
          </Button>
          <Button v-if="podeAviso" size="sm" variant="outline" class="h-8" @click="avisoAberto = true">
            <Bell class="mr-1 size-3.5" /> Quem recebe o aviso
          </Button>
        </div>
        <div class="table-card overflow-x-auto">
          <table class="w-full min-w-[860px]">
            <thead>
              <tr>
                <th class="w-[100px]">Quando</th>
                <th>O que aconteceu</th>
                <th>O que fazer</th>
                <th class="w-[90px]">Tipo</th>
                <th class="w-[110px]" />
              </tr>
            </thead>
            <tbody>
              <tr v-if="!totalOcorrencias">
                <td colspan="5" class="py-6 text-center text-sm text-muted-foreground">Nenhuma ocorrência aberta.</td>
              </tr>
              <tr v-for="r in relatorios" :key="`rel:${r.dia}`" class="cursor-pointer" @click="abrirRelatorio(r.dia)">
                <td class="text-xs tabular-nums whitespace-nowrap">{{ quando(r.fechado_em) || diaBr(r.dia) }}</td>
                <td class="max-w-[380px]">
                  <div class="text-sm font-medium">Relatório do dia {{ diaBr(r.dia) }}</div>
                  <div class="text-[11px] text-muted-foreground">
                    achou {{ numero(r.achou) }} · denunciou {{ numero(r.denunciou) }} nas lojas · Anatel {{ numero(r.anatel) }} loja(s) · {{ numero(r.removidos) }} removido(s)
                  </div>
                </td>
                <td class="text-xs">Abrir e conferir (tem Excel)</td>
                <td><span class="pill-info">relatório</span></td>
                <td class="text-right" @click.stop>
                  <div class="flex justify-end gap-1">
                    <Button size="sm" variant="outline" class="h-7" @click="abrirRelatorio(r.dia)">Abrir</Button>
                    <Button
                      v-if="podeMandar"
                      size="sm"
                      variant="outline"
                      class="h-7"
                      :disabled="!!mandando"
                      title="já li: tira da lista (continua nos anteriores)"
                      @click="marcarLido(r)"
                    >
                      <Loader2 v-if="mandando === `rel:${r.dia}`" class="mr-1 size-3.5 animate-spin" />
                      <Check v-else class="mr-1 size-3.5" /> Lido
                    </Button>
                  </div>
                </td>
              </tr>
              <tr v-for="o in painel.ocorrencias" :key="o.chave">
                <td class="text-xs tabular-nums whitespace-nowrap">{{ quando(o.quando) || 'agora' }}</td>
                <td class="max-w-[380px]">
                  <div class="text-sm font-medium">{{ o.titulo }}</div>
                  <div v-if="o.detalhe" class="line-clamp-2 text-[11px] text-muted-foreground" :title="o.detalhe">{{ o.detalhe }}</div>
                </td>
                <td class="max-w-[360px] text-xs">
                  <div class="line-clamp-2" :title="o.o_que_fazer">{{ o.o_que_fazer || '—' }}</div>
                </td>
                <td>
                  <span :class="o.tipo === 'pessoa' ? 'pill-danger' : 'pill-warning'">{{ o.tipo === 'pessoa' ? 'pessoa' : 'aviso' }}</span>
                </td>
                <td class="text-right">
                  <Button
                    v-if="podeMandar && podeTratar(o)"
                    size="sm"
                    variant="outline"
                    class="h-7"
                    :disabled="!!mandando"
                    title="já resolvido: tira da lista"
                    @click="tratar(o)"
                  >
                    <Loader2 v-if="mandando === o.chave" class="mr-1 size-3.5 animate-spin" />
                    <Check v-else class="mr-1 size-3.5" /> Tratado
                  </Button>
                  <span v-else-if="!podeTratar(o)" class="text-[11px] text-muted-foreground">sai sozinha</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- ══ Pedidos ao robô ══ -->
      <div v-else class="table-card overflow-x-auto">
        <table class="w-full min-w-[760px]">
          <thead>
            <tr>
              <th class="w-[100px]">Quando</th>
              <th>Pedido</th>
              <th class="w-[140px]">Quem</th>
              <th>Resposta do Mac mini</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!painel.comandos.length">
              <td colspan="4" class="py-6 text-center text-sm text-muted-foreground">Nenhum pedido ainda.</td>
            </tr>
            <tr v-for="c in painel.comandos" :key="c.id">
              <td class="text-xs tabular-nums whitespace-nowrap">{{ quando(c.pedido_em) }}</td>
              <td class="text-sm">{{ nomeComando(c) }}</td>
              <td class="text-xs text-muted-foreground">{{ c.pedido_por }}</td>
              <td class="text-xs">
                <span v-if="c.entregue_em" :class="c.ok ? 'text-emerald-700 dark:text-emerald-400' : 'text-red-700 dark:text-red-400'">
                  {{ c.ok ? 'recebido' : 'não deu' }} às {{ quando(c.entregue_em) }}<template v-if="c.resultado"> — {{ c.resultado }}</template>
                </span>
                <span v-else-if="c.caducou" class="text-red-700 dark:text-red-400">o Mac mini não pegou em 1 h</span>
                <span v-else class="inline-flex items-center gap-1 text-amber-700 dark:text-amber-400">
                  <Loader2 class="size-3 animate-spin" /> aguardando o Mac mini…
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <DenunciaRelatorio v-model:open="relAberto" :dia="relDia" :pode-marcar="podeMandar" @lido="relLido" />
    <InformarThreemaModal
      :open="avisoAberto"
      contexto="denuncia_robo"
      somente-cadastro
      descricao="Quem está marcado recebe no Threema quando o robô de Denúncia precisa de alguém: captcha ('não sou robô') na tela do Mac mini — o robô espera uns 10 min —, robô parado, Mac mini sem notícia e SEI pedindo código ou assinatura. Uma mensagem por ocorrência, de dia e de noite. Sem ninguém salvo aqui, vale a lista da IA de Chamado. A seleção fica salva."
      @close="avisoAberto = false"
    />
    <InformarThreemaModal
      :open="relatorioThreemaAberto"
      contexto="denuncia_relatorio"
      somente-cadastro
      descricao="Quem está marcado recebe no Threema, todo dia a partir das 7h, o relatório do dia anterior: anúncios achados, denúncias nas lojas, Anatel, respostas, prints e o que precisou de alguém — com o link do relatório completo e do Excel aqui no DaVinci. Uma mensagem por dia. Sem ninguém marcado, não vai para ninguém. A seleção fica salva."
      @close="relatorioThreemaAberto = false"
    />
  </div>
</template>
