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
 * Mesmas rotas da aba antiga: as contas em /accounts?platform=, as janelas em
 * /schedules/{id} (GET; PUT com a lista inteira, só com marketing:edit) e as
 * cores em /schedules/{id}/heatmap.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { AlertTriangle, Clock, Loader2 } from 'lucide-vue-next'
import { apiErrMsg } from '~/lib/apiError'

type PlataformaHorario = 'shopee' | 'ml'
const props = defineProps<{ plataforma: PlataformaHorario }>()

type Conta = {
  id: string
  name: string
  platform: string
  acos_target: number
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

// A última conta vista em cada marketplace, enquanto a sessão durar: Shopee →
// Mercado Livre → Shopee volta na mesma conta. useState (memória do Nuxt, nada
// no navegador) sobrevive à aba Amazon, que desmonta este componente.
const lembradas = useState<Partial<Record<PlataformaHorario, string>>>('marketing:horarios-conta', () => ({}))

// ---------- contas

const contas = ref<Conta[]>([])
const contasCarregadas = ref(false)
const carregandoContas = ref(false)
const erroContas = ref<string | null>(null)
const contaId = ref<string | null>(null)
const conta = computed(() => contas.value.find((c) => c.id === contaId.value) ?? null)

/** ?horario= do link (F5) → a última vista neste marketplace → a primeira (A–Z). */
function escolhaInicial(lista: Conta[], p: PlataformaHorario): string | null {
  const existe = (id: string | undefined | null) => !!id && lista.some((c) => c.id === id)
  const daUrl = String(route.query.horario || '')
  if (existe(daUrl)) return daUrl
  const lembrada = lembradas.value[p]
  if (existe(lembrada)) return lembrada as string
  return lista[0]?.id ?? null
}

function nomeLimpo(n: unknown): string {
  return String(n ?? '').replace(/[\s\u200b-\u200d\u2060\ufeff]+/g, ' ').trim()
}

// Trocou de marketplace no meio da leitura: a lista velha não escreve na tela.
let geracaoContas = 0
async function carregarContas() {
  const minha = ++geracaoContas
  const p = props.plataforma
  carregandoContas.value = true
  erroContas.value = null
  try {
    const r = await api<Conta[]>(`/api/marketing/accounts?platform=${p}`)
    if (minha !== geracaoContas) return
    // Alguns apelidos do ML vêm com espaço/caractere invisível na frente
    // (" barbosa"), o que jogava a conta pro topo da lista: limpa antes de ordenar.
    contas.value = (Array.isArray(r) ? r : [])
      .map((c) => ({ ...c, name: nomeLimpo(c.name) }))
      .sort((a, b) => a.name.localeCompare(b.name, 'pt-BR', { sensitivity: 'base' }))
    contasCarregadas.value = true
    const id = escolhaInicial(contas.value, p)
    if (id) lembradas.value = { ...lembradas.value, [p]: id }
    contaId.value = id
  } catch (e: any) {
    if (minha !== geracaoContas) return
    erroContas.value = apiErrMsg(e)
  } finally {
    if (minha === geracaoContas) carregandoContas.value = false
  }
}

/** Escolha da pessoa no seletor: fica lembrada e vai pro link (?horario=). */
function escolherConta(id: string) {
  if (!contas.value.some((c) => c.id === id)) return
  contaId.value = id
  lembradas.value = { ...lembradas.value, [props.plataforma]: id }
  void router.replace({ query: { ...route.query, horario: id } })
}

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

function fmtMoney(v: number | null | undefined): string {
  if (v == null) return '—'
  return v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
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
      <span v-if="canEdit" class="text-xs text-muted-foreground">— clique para ligar/desligar</span>
      <span v-else class="text-xs text-muted-foreground">— só quem edita o Marketing liga/desliga</span>
      <div class="ml-auto flex min-w-0 items-center gap-2">
        <span v-if="salvando > 0" class="inline-flex items-center gap-1 text-xs text-muted-foreground">
          <Loader2 class="size-3.5 animate-spin" /> salvando…
        </span>
        <label v-if="contas.length > 0" class="inline-flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
          Conta
          <select
            :value="contaId"
            class="h-8 min-w-0 max-w-[min(18rem,60vw)] rounded-md border bg-background px-2 text-sm text-foreground"
            aria-label="Conta dos horários"
            @change="(e) => escolherConta((e.target as HTMLSelectElement).value)"
          >
            <option v-for="c in contas" :key="c.id" :value="c.id">{{ c.name }}</option>
          </select>
        </label>
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

    <template v-else-if="conta">
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

    <p v-if="plataforma === 'shopee'" class="text-[11px] text-muted-foreground">
      Os anúncios da Shopee são ligados e desligados pelo robô do Mac (18h–22h). Este mapa guarda os horários, mas ainda não comanda o robô.
    </p>
  </section>
</template>
