<script setup lang="ts">
/**
 * Marketing › Conferência › Horários dos anúncios (08/10/2026).
 *
 * O que ficou das antigas abas Mercado Livre e Shopee da página de Marketing
 * (decisão do dono em 08/10/2026: "a única coisa que eu quero que preserve é o
 * heatmap de horários da Shopee na Shopee e do Mercado Livre no Mercado
 * Livre"): o mapa 7×24 de uma conta de Ads, pintado pelo ACOS dos últimos 30
 * dias, onde cada clique liga ou desliga aquela hora. Mora dentro da
 * Conferência, embaixo do relatório do marketplace (a Amazon não tem mapa).
 *
 * Ficou de fora, de propósito: a "Agenda automática" (ligar/pausar a agenda,
 * Pausar agora, Ligar agora), a Oferta Relâmpago, os alertas de crédito, as
 * tabelas de 7/30 dias, o gráfico Evolução e o "rodar ciclo agora". Na Shopee
 * quem liga e desliga os anúncios de verdade é o robô do Mac (18h–22h); a
 * agenda do DaVinci está desligada desde julho e ligá-la brigaria com ele — por
 * isso o aviso embaixo do mapa da Shopee.
 *
 * 13/10/2026 (pedido do dono): o seletor "Conta" virou os botões Mala |
 * Celular | Eletro e a lista de TODAS as contas do segmento, uma embaixo da
 * outra (à esquerda; no celular, em cima do mapa). Clicar numa conta abre o
 * mapa dela. Na Shopee cada linha traz o último saldo de Ads lido pela
 * Conferência (0 em vermelho; sem leitura = "sem leitura"); no Mercado Livre
 * não há saldo (o ML cobra depois). Eletro = as contas que venderam ou
 * anunciaram eletro na última Conferência (as mesmas aparecem em Celular: o
 * horário é um só por conta). O link guarda a conta (?horario=) e o segmento
 * (?seg=).
 *
 * Rotas: as contas em /horarios/contas?platform= (segmento, eletro e saldo —
 * services/marketing/horarios_contas.py), as janelas em /schedules/{id} (GET;
 * PUT com a lista inteira, só com marketing:edit) e as cores em
 * /schedules/{id}/heatmap.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { AlertTriangle, Clock, Loader2 } from 'lucide-vue-next'
import { apiErrMsg } from '~/lib/apiError'

type PlataformaHorario = 'shopee' | 'ml'
const props = defineProps<{ plataforma: PlataformaHorario }>()

type Segmento = 'mala' | 'celular' | 'eletro'
type Conta = {
  id: string
  name: string
  // O grupo da conta na Conferência (Mala/Celular); Eletro é à parte (`eletro`).
  segmento: 'mala' | 'celular'
  eletro: boolean
  acos_target: number
  // Só na Shopee: o último saldo de Ads lido pela Conferência (null = sem leitura).
  saldo?: number | null
  saldo_em?: string | null
}
type ContasHorarios = {
  plataforma: PlataformaHorario
  contas: Conta[]
  // A rodada da Conferência de onde saiu o Eletro; null = nenhuma pronta ainda.
  eletro_execucao: { id: string; gerado_em: string | null } | null
}
type Schedule = {
  id: string
  account_id: string
  day_of_week: number
  start_hour: number
  end_hour: number
}
type Bloco = { day_of_week: number; start_hour: number; end_hour: number }
type HeatmapCell = {
  spend: number
  revenue: number
  impressions: number
  acos: number | null
}
type Heatmap = {
  acos_target: number
  cells: Record<string, HeatmapCell>
}

const { api } = useApi()
const toasts = useToasts()
// O PUT das janelas pede marketing:edit (routers/marketing.py): quem só vê,
// vê o mapa sem conseguir clicar.
const canEdit = useCan('marketing', 'edit')
const route = useRoute()
const router = useRouter()

const DIAS = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']
const DO_MARKETPLACE: Record<PlataformaHorario, string> = { shopee: 'da Shopee', ml: 'do Mercado Livre' }
const SEGMENTOS: { chave: Segmento; rotulo: string }[] = [
  { chave: 'mala', rotulo: 'Mala' },
  { chave: 'celular', rotulo: 'Celular' },
  { chave: 'eletro', rotulo: 'Eletro' },
]
const ROTULO_SEGMENTO: Record<Segmento, string> = { mala: 'Mala', celular: 'Celular', eletro: 'Eletro' }

// A última conta (e o último segmento) vista em cada marketplace, enquanto a
// sessão durar: Shopee → Mercado Livre → Shopee volta no mesmo lugar. useState
// (memória do Nuxt, nada no navegador) sobrevive à aba Amazon, que desmonta
// este componente.
const lembradas = useState<Partial<Record<PlataformaHorario, string>>>('marketing:horarios-conta', () => ({}))
const segmentosLembrados = useState<Partial<Record<PlataformaHorario, Segmento>>>(
  'marketing:horarios-segmento',
  () => ({}),
)

// ---------- contas

const contas = ref<Conta[]>([])
const contasCarregadas = ref(false)
const carregandoContas = ref(false)
const erroContas = ref<string | null>(null)
const eletroExecucao = ref<ContasHorarios['eletro_execucao']>(null)
const segmento = ref<Segmento>('mala')
const contaId = ref<string | null>(null)
const conta = computed(() => contas.value.find((c) => c.id === contaId.value) ?? null)

function noSegmento(c: Conta, seg: Segmento): boolean {
  return seg === 'eletro' ? c.eletro === true : c.segmento === seg
}
function segmentoValido(v: unknown): Segmento | null {
  const s = String(v ?? '')
  return SEGMENTOS.some((x) => x.chave === s) ? (s as Segmento) : null
}

/** As contas do segmento escolhido (já em ordem A–Z). */
const lista = computed(() => contas.value.filter((c) => noSegmento(c, segmento.value)))

/**
 * Onde abrir: a conta do link (?horario=, F5) no segmento do link (?seg=) se
 * ela estiver nele, senão no dela → a última vista neste marketplace (no
 * último segmento visto) → o último segmento visto, o do link ou Mala, na
 * primeira conta (A–Z). `comLembranca` false = o que o link sozinho abre (F5
 * numa sessão nova, link copiado).
 */
function escolhaInicial(
  todas: Conta[],
  p: PlataformaHorario,
  comLembranca = true,
): { seg: Segmento; id: string | null } {
  const achar = (id: unknown) => (typeof id === 'string' && id ? todas.find((c) => c.id === id) : undefined)
  const primeira = (seg: Segmento) => todas.find((c) => noSegmento(c, seg))?.id ?? null
  const segUrl = segmentoValido(route.query.seg)
  const daUrl = achar(String(route.query.horario || ''))
  if (daUrl) return { seg: segUrl && noSegmento(daUrl, segUrl) ? segUrl : daUrl.segmento, id: daUrl.id }
  const segLembrado = comLembranca ? (segmentosLembrados.value[p] ?? null) : null
  const lembrada = comLembranca ? achar(lembradas.value[p]) : undefined
  if (lembrada) {
    if (segLembrado && !noSegmento(lembrada, segLembrado)) return { seg: segLembrado, id: primeira(segLembrado) }
    return { seg: segLembrado ?? lembrada.segmento, id: lembrada.id }
  }
  const seg = segLembrado ?? segUrl ?? 'mala'
  return { seg, id: primeira(seg) }
}

function nomeLimpo(n: unknown): string {
  return String(n ?? '').replace(/[\s\u200b-\u200d\u2060\ufeff]+/g, ' ').trim()
}

function lembrar(p: PlataformaHorario, seg: Segmento, id: string | null) {
  segmentosLembrados.value = { ...segmentosLembrados.value, [p]: seg }
  if (id) lembradas.value = { ...lembradas.value, [p]: id }
}

// Trocou de marketplace no meio da leitura: a lista velha não escreve na tela.
let geracaoContas = 0
async function carregarContas() {
  const minha = ++geracaoContas
  const p = props.plataforma
  carregandoContas.value = true
  erroContas.value = null
  try {
    const r = await api<ContasHorarios>(`/api/marketing/horarios/contas?platform=${p}`)
    if (minha !== geracaoContas) return
    // Alguns apelidos do ML vêm com espaço/caractere invisível na frente
    // (" barbosa"), o que jogava a conta pro topo da lista: limpa antes de ordenar.
    contas.value = (Array.isArray(r?.contas) ? r.contas : [])
      .map((c) => ({ ...c, name: nomeLimpo(c.name) }))
      .sort((a, b) => a.name.localeCompare(b.name, 'pt-BR', { sensitivity: 'base' }))
    eletroExecucao.value = r?.eletro_execucao ?? null
    contasCarregadas.value = true
    const { seg, id } = escolhaInicial(contas.value, p)
    lembrar(p, seg, id)
    segmento.value = seg
    contaId.value = id
    // A lembrança abriu outra coisa que o link abriria (voltou de outro
    // marketplace, da Amazon ou de outra aba): o link passa a dizer o que está
    // na tela, pra F5 e link copiado abrirem o mesmo. Abrir pelo link, ou sem
    // link nem lembrança, não escreve nada.
    const doLink = escolhaInicial(contas.value, p, false)
    if (doLink.seg !== seg || doLink.id !== id) {
      const query: Record<string, any> = { ...route.query, seg }
      if (id) query.horario = id
      else delete query.horario
      void router.replace({ query })
    }
  } catch (e: any) {
    if (minha !== geracaoContas) return
    erroContas.value = apiErrMsg(e)
  } finally {
    if (minha === geracaoContas) carregandoContas.value = false
  }
}

/** Botão do segmento: fica na conta aberta se ela é do segmento, senão abre a primeira. */
function escolherSegmento(seg: Segmento) {
  if (!segmentoValido(seg) || seg === segmento.value) return
  segmento.value = seg
  const atual = conta.value
  const id = atual && noSegmento(atual, seg) ? atual.id : (lista.value[0]?.id ?? null)
  contaId.value = id
  lembrar(props.plataforma, seg, id)
  const query: Record<string, any> = { ...route.query, seg }
  if (id) query.horario = id
  else delete query.horario
  void router.replace({ query })
}

/** Clique na conta da lista: fica lembrada e vai pro link (?horario= e ?seg=). */
function escolherConta(id: string) {
  const c = contas.value.find((x) => x.id === id)
  if (!c) return
  if (!noSegmento(c, segmento.value)) segmento.value = c.segmento
  contaId.value = id
  lembrar(props.plataforma, segmento.value, id)
  void router.replace({ query: { ...route.query, horario: id, seg: segmento.value } })
}

// ---------- saldo de Ads (só Shopee)

const FMT_DIA = new Intl.DateTimeFormat('pt-BR', { timeZone: 'America/Sao_Paulo', day: '2-digit', month: '2-digit' })
const FMT_HORA = new Intl.DateTimeFormat('pt-BR', {
  timeZone: 'America/Sao_Paulo',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})

function fmtMoney(v: number | null | undefined): string {
  if (v == null) return '—'
  return v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
}

function fmtSaldo(v: number | null | undefined): string {
  return v == null ? 'sem leitura' : fmtMoney(v)
}

// Classes por extenso pro Tailwind enxergar: zero em vermelho, sem leitura apagado.
function classeSaldo(v: number | null | undefined): string {
  if (v == null) return 'text-xs text-muted-foreground'
  if (v === 0) return 'font-semibold text-red-600 dark:text-red-400'
  return 'text-foreground'
}

/**
 * Rodapé da lista: quando o saldo foi lido ("06/10 às 15:05", "06/10 às
 * 15:05–15:40" se as leituras foram em horas diferentes, "03/10–06/10" se em
 * dias diferentes), no horário de Brasília. Só as contas da lista com leitura.
 */
const saldoLido = computed<string | null>(() => {
  if (props.plataforma !== 'shopee') return null
  const datas = lista.value
    .map((c) => (c.saldo_em ? new Date(c.saldo_em) : null))
    .filter((d): d is Date => !!d && !Number.isNaN(d.getTime()))
    .sort((a, b) => a.getTime() - b.getTime())
  if (!datas.length) return null
  const ini = datas[0] as Date
  const fim = datas[datas.length - 1] as Date
  const [dIni, dFim] = [FMT_DIA.format(ini), FMT_DIA.format(fim)]
  if (dIni !== dFim) return `saldo lido em ${dIni}–${dFim}`
  const [hIni, hFim] = [FMT_HORA.format(ini), FMT_HORA.format(fim)]
  return hIni === hFim ? `saldo lido em ${dIni} às ${hIni}` : `saldo lido em ${dIni} às ${hIni}–${hFim}`
})

// ---------- mapa da conta escolhida (janelas + ACOS por hora)

const schedules = ref<Schedule[]>([])
const heatmap = ref<Heatmap | null>(null)
// A conta cujo mapa está na tela: trocou de conta, o mapa da outra não aparece
// com o nome desta enquanto a nova não chega. Enquanto relê fica null: a grade
// vira esqueleto e ninguém clica em cima de um mapa que pode estar velho.
const mapaDe = ref<string | null>(null)
const erroMapa = ref<string | null>(null)
let geracaoMapa = 0

// Cliques rápidos: a tela muda na hora e os PUTs saem um atrás do outro, cada
// um com a lista inteira daquele momento — o último a chegar no servidor é o
// do último clique (a aba antiga mandava em paralelo e podia perder um clique).
let fila: Promise<unknown> = Promise.resolve()
let ultimoClique = 0
// PUTs que falharam, por conta. Os cliques que esperavam na fila atrás de um que
// falhou foram montados em cima da hora que não salvou (salvariam ela assim
// mesmo): não saem, a tela relê o servidor e a pessoa clica de novo.
const falhasPorConta = new Map<string, number>()
const salvando = ref(0)

async function carregarMapa() {
  const id = contaId.value
  const minha = ++geracaoMapa
  erroMapa.value = null
  mapaDe.value = null
  if (!id) {
    schedules.value = []
    heatmap.value = null
    return
  }
  try {
    // Primeiro os PUTs que ainda estão na fila: um GET que saísse antes leria o
    // servidor de antes do clique e, chegando depois, apagaria o clique da tela.
    await fila
    if (minha !== geracaoMapa) return
    const [s, h] = await Promise.all([
      api<Schedule[]>(`/api/marketing/schedules/${id}`),
      api<Heatmap>(`/api/marketing/schedules/${id}/heatmap`),
    ])
    if (minha !== geracaoMapa) return
    schedules.value = Array.isArray(s) ? s : []
    heatmap.value = h && typeof h === 'object' ? { acos_target: h.acos_target, cells: h.cells ?? {} } : null
    mapaDe.value = id
  } catch (e: any) {
    if (minha !== geracaoMapa) return
    erroMapa.value = apiErrMsg(e)
  }
}

const mapaPronto = computed(() => !!contaId.value && mapaDe.value === contaId.value)

// Mesma leitura da aba antiga (e do agente, services/marketing/agent.py):
// fim <= início é uma janela que vira a meia-noite.
const scheduleSet = computed(() => {
  const s = new Set<string>()
  if (!mapaPronto.value) return s
  for (const sch of schedules.value) {
    if (sch.end_hour <= sch.start_hour) {
      for (let h = sch.start_hour; h < 24; h++) s.add(`${sch.day_of_week}-${h}`)
      for (let h = 0; h < sch.end_hour; h++) s.add(`${sch.day_of_week}-${h}`)
    } else {
      for (let h = sch.start_hour; h < sch.end_hour; h++) s.add(`${sch.day_of_week}-${h}`)
    }
  }
  return s
})

const alvoAcos = computed(() => heatmap.value?.acos_target ?? conta.value?.acos_target ?? 7)

// ---------- células: cor, dica e clique

// Classes por extenso pro Tailwind enxergar. Sem edição, a célula não muda no
// hover (não parece clicável).
const COR_CELULA = {
  desligado: 'bg-muted',
  semDados: 'bg-sky-400/60',
  bom: 'bg-emerald-500/80',
  medio: 'bg-amber-400/80',
  ruim: 'bg-red-500/80',
} as const
const HOVER_CELULA: Record<keyof typeof COR_CELULA, string> = {
  desligado: 'hover:bg-muted-foreground/30',
  semDados: 'hover:bg-sky-500/80',
  bom: 'hover:bg-emerald-500',
  medio: 'hover:bg-amber-500',
  ruim: 'hover:bg-red-600',
}

function tomCelula(dow: number, hour: number): keyof typeof COR_CELULA {
  if (!scheduleSet.value.has(`${dow}-${hour}`)) return 'desligado'
  const cell = heatmap.value?.cells[`${dow}-${hour}`]
  const target = alvoAcos.value
  if (!cell || cell.acos == null) return 'semDados'
  if (cell.acos < target) return 'bom'
  if (cell.acos < target * 1.5) return 'medio'
  return 'ruim'
}

function heatmapCellClass(dow: number, hour: number): string {
  const tom = tomCelula(dow, hour)
  return canEdit.value
    ? `${COR_CELULA[tom]} ${HOVER_CELULA[tom]} cursor-pointer`
    : `${COR_CELULA[tom]} cursor-default`
}

function heatmapTooltip(dow: number, hour: number): string {
  const cell = heatmap.value?.cells[`${dow}-${hour}`]
  const on = scheduleSet.value.has(`${dow}-${hour}`)
  const lines = [`${DIAS[dow]} ${hour}:00${on ? ' — ligado' : ' — desligado'}`]
  if (cell) {
    lines.push(`ACOS: ${cell.acos != null ? cell.acos.toFixed(1) + '%' : '—'}`)
    lines.push(`Gasto: ${fmtMoney(cell.spend)}`)
    lines.push(`Faturamento: ${fmtMoney(cell.revenue)}`)
    lines.push(`Impressões: ${(cell.impressions ?? 0).toLocaleString('pt-BR')}`)
  } else {
    lines.push('Sem dados nesse horário')
  }
  return lines.join('\n')
}

function toggleScheduleCell(dow: number, hour: number): Promise<void> {
  const id = contaId.value
  if (!canEdit.value || !id || !mapaPronto.value) return Promise.resolve()
  // A mesma conta da aba antiga: liga/desliga a célula e manda TODAS as horas
  // ligadas, uma janela de 1 h por célula.
  const key = `${dow}-${hour}`
  const set = new Set(scheduleSet.value)
  if (set.has(key)) set.delete(key)
  else set.add(key)
  const blocks: Bloco[] = []
  for (const cell of set) {
    const [d, h] = cell.split('-').map(Number)
    // 23h termina à meia-noite: fim 0 (fim <= início = vira a meia-noite, na
    // tela e no agente). A aba antiga mandava 24 e o servidor recusava (422,
    // ScheduleIn aceita 0–23): conta com 23h ligada não salvava mais nada.
    blocks.push({ day_of_week: d, start_hour: h, end_hour: (h + 1) % 24 })
  }
  schedules.value = blocks.map((b, i) => ({ id: `local-${i}`, account_id: id, ...b }))
  const meu = ++ultimoClique
  const falhas = falhasPorConta.get(id) ?? 0
  salvando.value++
  const envio = fila.then(async () => {
    // Um PUT desta conta falhou enquanto este esperava: não sai (null).
    if ((falhasPorConta.get(id) ?? 0) !== falhas) return null
    try {
      return await api<Schedule[]>(`/api/marketing/schedules/${id}`, {
        method: 'PUT',
        body: blocks,
      })
    } catch (e) {
      // Marca antes de a fila andar: o próximo da fila já enxerga a falha.
      falhasPorConta.set(id, falhas + 1)
      throw e
    }
  })
  fila = envio.catch(() => undefined)
  return envio.then(
    (r) => {
      // Só a resposta do último clique (na mesma conta) substitui a tela.
      if (meu === ultimoClique && contaId.value === id && Array.isArray(r)) schedules.value = r
    },
    (e: any) => {
      toasts.error('Não consegui salvar o horário', apiErrMsg(e))
      // A tela volta ao que está salvo no servidor (a releitura espera a fila).
      if (contaId.value === id) void carregarMapa()
    },
  ).finally(() => {
    salvando.value--
  })
}

// ---------- ciclo de vida

watch(contaId, () => {
  void carregarMapa()
})
// Shopee ↔ Mercado Livre na Conferência: as contas e o mapa do outro saem.
watch(() => props.plataforma, () => {
  contas.value = []
  contasCarregadas.value = false
  eletroExecucao.value = null
  contaId.value = null
  void carregarContas()
})
onMounted(() => {
  void carregarContas()
})
</script>

<template>
  <section class="min-w-0 space-y-3 rounded-xl border bg-card p-4" aria-label="Horários dos anúncios">
    <div class="flex flex-wrap items-center gap-x-2 gap-y-2">
      <Clock class="size-4 shrink-0 text-primary" />
      <h2 class="text-base font-semibold">Horários dos anúncios</h2>
      <span class="text-xs text-muted-foreground">— escolha o segmento e clique na conta</span>
      <div class="ml-auto flex min-w-0 items-center gap-2">
        <span v-if="salvando > 0" class="inline-flex items-center gap-1 text-xs text-muted-foreground">
          <Loader2 class="size-3.5 animate-spin" /> salvando…
        </span>
        <div
          v-if="contas.length > 0"
          class="flex w-fit max-w-full gap-1 overflow-x-auto rounded-md bg-muted/40 p-1"
          role="tablist"
          aria-label="Segmento das contas"
        >
          <button
            v-for="s in SEGMENTOS"
            :key="s.chave"
            type="button"
            role="tab"
            :aria-selected="segmento === s.chave"
            class="whitespace-nowrap rounded px-3 py-1 text-sm transition-colors"
            :class="segmento === s.chave ? 'bg-background font-medium shadow-sm' : 'text-muted-foreground hover:bg-background/60'"
            @click="escolherSegmento(s.chave)"
          >
            {{ s.rotulo }}
          </button>
        </div>
      </div>
    </div>

    <!-- estados das contas: primeira carga, erro, nenhuma -->
    <div v-if="carregandoContas && !contasCarregadas" class="space-y-1.5" aria-busy="true">
      <div v-for="i in 7" :key="i" class="h-6 animate-pulse rounded bg-muted/40" />
    </div>

    <div
      v-else-if="erroContas && !contasCarregadas"
      class="flex flex-wrap items-center gap-3 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-400"
    >
      <AlertTriangle class="size-4 shrink-0" />
      <span>Não consegui carregar as contas de Ads ({{ erroContas }}).</span>
      <button class="btn btn-xs" @click="carregarContas">Tentar de novo</button>
    </div>

    <p v-else-if="contasCarregadas && !contas.length" class="py-2 text-sm text-muted-foreground">
      Nenhuma conta de Ads {{ DO_MARKETPLACE[plataforma] }} no DaVinci ainda.
    </p>

    <div v-else-if="contasCarregadas" class="flex flex-col gap-4 md:flex-row md:items-start">
      <!-- a lista do segmento (no celular, em cima do mapa) -->
      <div class="w-full shrink-0 overflow-hidden rounded-lg border md:w-[290px]">
        <div class="flex items-center justify-between gap-2 border-b bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
          <span>Contas de {{ ROTULO_SEGMENTO[segmento] }} ({{ lista.length }})</span>
          <span v-if="plataforma === 'shopee' && lista.length">Saldo Ads</span>
        </div>
        <ul v-if="lista.length" :aria-label="`Contas de ${ROTULO_SEGMENTO[segmento]}`">
          <li v-for="c in lista" :key="c.id" class="border-b last:border-b-0">
            <button
              type="button"
              class="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm transition-colors"
              :class="c.id === contaId
                ? 'border-l-[3px] border-l-blue-600 bg-blue-50 pl-[9px] font-semibold dark:border-l-blue-400 dark:bg-blue-500/15'
                : 'hover:bg-muted/50'"
              :aria-current="c.id === contaId ? 'true' : undefined"
              @click="escolherConta(c.id)"
            >
              <span class="min-w-0 truncate">{{ c.name }}</span>
              <span
                v-if="plataforma === 'shopee'"
                class="shrink-0 tabular-nums"
                :class="classeSaldo(c.saldo)"
              >{{ fmtSaldo(c.saldo) }}</span>
            </button>
          </li>
        </ul>
        <p v-else-if="segmento === 'eletro' && !eletroExecucao" class="px-3 py-3 text-sm text-muted-foreground">
          Sem relatório da Conferência para saber quem vende eletro.
        </p>
        <p v-else-if="segmento === 'eletro'" class="px-3 py-3 text-sm text-muted-foreground">
          Nenhuma conta vendeu nem anunciou eletro nas 4 semanas do último relatório da Conferência.
        </p>
        <p v-else class="px-3 py-3 text-sm text-muted-foreground">
          Nenhuma conta de Ads de {{ ROTULO_SEGMENTO[segmento] }} {{ DO_MARKETPLACE[plataforma] }}.
        </p>
        <p v-if="segmento === 'eletro' && lista.length" class="border-t px-3 py-2 text-[11px] text-muted-foreground">
          As contas aparecem também em Celular; o horário é um só por conta.
        </p>
        <div v-if="saldoLido" class="border-t bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
          {{ saldoLido }}
        </div>
      </div>

      <!-- o mapa da conta escolhida -->
      <div class="min-w-0 flex-1 space-y-3">
        <template v-if="conta">
          <div class="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <h3 class="text-sm font-semibold">{{ conta.name }}</h3>
            <span class="text-xs text-muted-foreground">
              <template v-if="plataforma === 'shopee'">
                Saldo Ads <span class="tabular-nums" :class="classeSaldo(conta.saldo)">{{ fmtSaldo(conta.saldo) }}</span> ·
              </template>
              <template v-if="canEdit">clique nas horas para ligar/desligar</template>
              <template v-else>só quem edita o Marketing liga/desliga</template>
            </span>
          </div>

          <!-- estados do mapa da conta escolhida -->
          <div
            v-if="erroMapa && !mapaPronto"
            class="flex flex-wrap items-center gap-3 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-400"
          >
            <AlertTriangle class="size-4 shrink-0" />
            <span>Não consegui carregar os horários de {{ conta.name }} ({{ erroMapa }}).</span>
            <button class="btn btn-xs" @click="carregarMapa">Tentar de novo</button>
          </div>

          <div v-else-if="!mapaPronto" class="space-y-1.5" aria-busy="true">
            <div v-for="i in 7" :key="i" class="h-6 animate-pulse rounded bg-muted/40" />
          </div>

          <template v-else>
            <div class="overflow-x-auto">
              <table class="border-separate border-spacing-0.5 text-[10px]" :aria-label="`Horários de ${conta.name}`">
                <thead>
                  <tr>
                    <th class="w-10" />
                    <th v-for="h in 24" :key="h" scope="col" class="w-6 text-center font-normal text-muted-foreground">{{ h - 1 }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(d, dIdx) in DIAS" :key="d">
                    <th scope="row" class="pr-2 text-right font-medium text-muted-foreground">{{ d }}</th>
                    <td v-for="h in 24" :key="h" class="p-0">
                      <button
                        type="button"
                        class="block size-6 rounded transition-colors"
                        :class="heatmapCellClass(dIdx, h - 1)"
                        :title="heatmapTooltip(dIdx, h - 1)"
                        :aria-label="heatmapTooltip(dIdx, h - 1)"
                        :aria-pressed="scheduleSet.has(`${dIdx}-${h - 1}`)"
                        :aria-disabled="!canEdit || undefined"
                        @click="toggleScheduleCell(dIdx, h - 1)"
                      />
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div class="flex flex-wrap items-center gap-3 text-[11px] text-muted-foreground">
              <span class="inline-flex items-center gap-1"><span class="size-3 rounded bg-emerald-500/80" /> ACOS &lt; {{ alvoAcos.toFixed(1) }}%</span>
              <span class="inline-flex items-center gap-1"><span class="size-3 rounded bg-amber-400/80" /> ACOS médio (até {{ (alvoAcos * 1.5).toFixed(1) }}%)</span>
              <span class="inline-flex items-center gap-1"><span class="size-3 rounded bg-red-500/80" /> ACOS ruim</span>
              <span class="inline-flex items-center gap-1"><span class="size-3 rounded bg-sky-400/60" /> Ligado, sem dados</span>
              <span class="inline-flex items-center gap-1"><span class="size-3 rounded border bg-muted" /> Desligado</span>
            </div>
          </template>
        </template>
        <p v-else-if="lista.length" class="py-2 text-sm text-muted-foreground">Escolha uma conta na lista.</p>
      </div>
    </div>

    <p v-if="plataforma === 'shopee'" class="text-[11px] text-muted-foreground">
      Os anúncios da Shopee são ligados e desligados pelo robô do Mac (18h–22h). Este mapa guarda os horários, mas ainda não comanda o robô.
    </p>
  </section>
</template>
