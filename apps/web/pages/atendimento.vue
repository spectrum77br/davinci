<script setup lang="ts">
import { BarChart3, BookOpen, Bot, Eye, Inbox, MessageSquareText, RotateCcw, Store, TriangleAlert } from 'lucide-vue-next'
import { useResizeObserver, useWindowSize } from '@vueuse/core'
import {
  AVISO_SO_LEITURA,
  avisoSimulador,
  categoriasDe,
  comoLista,
  erroDaApi,
  leiturasParadas,
  podeCosturar,
  registrarCategorias,
  tituloLeituraParada,
  useRelogio,
  usePollingVisivel,
  vemDepoisNaLista,
  type ConversaResumo,
  type Modelo,
  type Resumo,
} from '~/components/AtendimentoPlataforma.vue'
import { tipoChamadoAtivo, type FiltrosLista } from '~/components/AtendimentoLista.vue'

// FASE DE OBSERVAÇÃO (SO_ADMIN em apps/api/app/routers/atendimento.py): a
// mesma trava do menu (AppSidebar) e da API. Desde 07/10/2026 ("pode liberar
// pras outras pessoas do DaVinci verem pra já obtermos feedbacks, mas claro
// por enquanto só leitura"), toda pessoa ativa entra — o /me traz
// `atendimento: true` (middleware `atendimento`) — e só quem o /me traz com
// `atendimento_mexe: true` (ATENDIMENTO_USUARIOS) responde e muda a caixa;
// os outros leem, pedem a sugestão da IA e dão 👍/👎. Quando a fase acabar:
// middleware: ['permission'], permission: { resource: 'atendimento', action: 'view' }.
definePageMeta({ middleware: ['atendimento'] })

// Atendimento (Pós-venda, 25/09/2026): a caixa única das conversas de Shopee,
// Mercado Livre, TikTok, Amazon e Magalu (30/09/2026: pergunta, chat e SAC),
// Temu e AliExpress (pelo robô do Mac mini) e o Direct do Instagram (só
// leitura), com a resposta sugerida pela IA pronta na caixa de resposta.
// Plano em docs/atendimento-unificado.md (Magalu: docs/atendimento-magalu.md).
// A equipe passa o dia nesta tela, então:
// - a Caixa tem a cara do Duoke (28/09/2026), em 4 colunas: barra de lojas
//   (com a bolinha vermelha de não lidas de cada loja; recolhível), fila,
//   conversa e o painel Pedido | Produto | Cupom (recolhível); em tela
//   estreita a barra some (os filtros de loja voltam para a fila) e vira uma
//   coluna com "voltar";
// - a fila se atualiza sozinha a cada 30 s e a conversa aberta a cada 15 s,
//   parando quando a aba do navegador fica escondida;
// - nada aqui liga envio: cada loja nasce em Observar (só lê) e o servidor
//   tem as chaves gerais (leitura, envio, IA). A faixa do topo diz o que está
//   desligado, numa linha curta (a frase inteira em "o que isso quer dizer?"),
//   para ninguém achar que a tela quebrou — sem roubar a altura da Caixa;
// - loja que parou de ler (05/10/2026: a Temu ficou dias sem ler sem ninguém
//   saber) aparece AQUI, não na Ouvidoria: a faixa vermelha "N lojas sem
//   ler" no topo da Caixa, com o link para "Lojas e modo", e a marca com a
//   contagem na aba. Vem no /resumo e some sozinha quando a loja volta a ler.
// - "Automáticas" (05/10/2026): as mensagens automáticas do Duoke recriadas
//   no DaVinci, por loja, começando em modo seco (registra o que mandaria e
//   compara com o Duoke; nada sai). Desenho em docs/atendimento-automacoes.md.

type Aba = 'caixa' | 'mail' | 'lojas' | 'manual' | 'modelos' | 'automaticas' | 'metricas'
const ABAS: { value: Aba; label: string; icon: any }[] = [
  { value: 'caixa', label: 'Caixa', icon: Inbox },
  { value: 'mail', label: 'E-mail', icon: Inbox },
  { value: 'lojas', label: 'Lojas e modo', icon: Store },
  { value: 'manual', label: 'Manual da IA', icon: BookOpen },
  { value: 'modelos', label: 'Respostas prontas', icon: MessageSquareText },
  { value: 'automaticas', label: 'Automáticas', icon: Bot },
  { value: 'metricas', label: 'Métricas', icon: BarChart3 },
]

const { api } = useApi()
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
// Quem só lê (fase de observação): as ações de mexer somem ou ficam
// desligadas com o AVISO_SO_LEITURA — inclusive para os outros admins. A API
// recusa do mesmo jeito (403 `atendimento_so_leitura`).
const mexe = computed(() => auth.user?.atendimento_mexe === true)
const soLeitura = computed(() => !mexe.value)
const podeEditar = useCan('atendimento', 'edit')
const podeApagar = useCan('atendimento', 'delete')
const ehAdmin = useIsAdmin()
const canEdit = computed(() => mexe.value && podeEditar.value)
const canDelete = computed(() => mexe.value && podeApagar.value)
const isAdmin = computed(() => mexe.value && ehAdmin.value)
// "Sugerir agora" e 👍/👎: todo mundo que vê a caixa (é o feedback que o dono quer).
const canSugerir = computed(() => auth.user?.atendimento === true)
const agora = useRelogio()

// `?tab=lojas` abre direto na aba; `?conversa=<id>` abre a conversa (é o link
// que o alerta de prazo no Telegram manda).
const abaQuery = typeof route.query.tab === 'string' ? route.query.tab : ''
const aba = ref<Aba>(ABAS.some((a) => a.value === abaQuery) ? (abaQuery as Aba) : 'caixa')
const selecionada = ref<string | null>(typeof route.query.conversa === 'string' && route.query.conversa ? route.query.conversa : null)

function sincronizarUrl() {
  const query = { ...route.query }
  if (aba.value === 'caixa') delete query.tab
  else query.tab = aba.value
  if (selecionada.value && aba.value === 'caixa') query.conversa = selecionada.value
  else delete query.conversa
  void router.replace({ query })
}
watch([aba, selecionada], sincronizarUrl)

// ─── resumo (contadores, lojas, flags) ──────────────────────────────────────
const resumo = ref<Resumo | null>(null)
const resumoErro = ref<string | null>(null)
async function carregarResumo() {
  try {
    resumo.value = await api<Resumo>('/api/atendimento/resumo')
    resumoErro.value = null
  } catch (e: any) {
    resumoErro.value = erroDaApi(e, 'Não consegui carregar o resumo do atendimento').texto
  }
}

// Faixa do topo: o que está desligado no servidor, dizendo o que isso
// significa na prática (não o nome da chave). Uma linha curta com o essencial
// de cada um (`curto`); a frase inteira fica no title e abre em "o que isso
// quer dizer?". Uma linha por aviso empurrava a Caixa para baixo da dobra em
// notebook — e em tela estreita cada frase quebrava em duas ou três.
const avisos = computed(() => {
  const f = resumo.value?.flags
  if (!f) return []
  const out: { chave: string; curto: string; texto: string; forte?: boolean }[] = []
  if (!f.leitura_ativa) out.push({ chave: 'leitura', curto: 'Leitura desligada: sem mensagens novas das lojas', texto: 'Leitura desligada — o DaVinci não está buscando mensagens novas nas lojas; a lista mostra só o que já tinha sido lido.' })
  if (!f.envio_ativo) out.push({ chave: 'envio', curto: 'Envio desligado: responda pelo Duoke', texto: 'Envio desligado — ninguém responde pelo DaVinci; continue respondendo pelo Duoke ou pela central da loja.' })
  if (!f.ia_ativa) out.push({ chave: 'ia', curto: 'IA desligada: sem sugestão de resposta', texto: 'IA desligada — sem sugestão de resposta; dá para ler e (se o envio estiver ligado) responder normalmente.' })
  // Com a exceção (ex.: Amazon real no teste local), a faixa diz onde CHEGA.
  const sim = avisoSimulador(f)
  if (sim) out.push({ chave: 'simulador', ...sim, forte: true })
  if (f.auto_ativo) out.push({ chave: 'auto', curto: 'Envio automático ligado', texto: 'Envio automático ligado — a IA responde sozinha nas lojas em modo Automático, só nas categorias liberadas.' })
  return out
})
const avisosAbertos = ref(false)

// Lojas sem ler além do limite de cada leitura (o retrato do /resumo).
const semLer = computed(() => leiturasParadas(resumo.value))

// ─── lista ──────────────────────────────────────────────────────────────────
const FILTROS_KEY = 'davinci.atendimento.filtros'
// Abre em "Todas", como o All do Duoke (01/10/2026); "Falta responder" é a aba ao lado.
const filtros = ref<FiltrosLista>({ plataforma: '', integration_id: '', canal: '', filtro: 'todas', q: '', externo_ref: '', rede_social_id: '', tipo_chamado: '' })
const itens = ref<ConversaResumo[]>([])
const proximo = ref<string | null>(null)
const carregando = ref(false)
const carregandoMais = ref(false)
const listaErro = ref<string | null>(null)
const LIMITE = 50
// A pessoa já puxou páginas com "carregar mais" desde o último filtro? Aí a
// atualização automática não pode jogar essas páginas fora.
let temPaginasExtras = false
// Resposta que chega depois de o filtro ter mudado não pode sobrescrever a
// lista nova — cada consulta leva o número da geração em que saiu.
let geracao = 0

type Pagina = { itens: ConversaResumo[]; proximo: string | null }

function params(antesDe?: string | null) {
  const p = new URLSearchParams()
  const f = filtros.value
  if (f.plataforma) p.set('plataforma', f.plataforma)
  if (f.integration_id) p.set('integration_id', f.integration_id)
  // Linhas da barra sem integração (02/10/2026): o site e a conta de rede.
  if (f.externo_ref) p.set('externo_ref', f.externo_ref)
  if (f.rede_social_id) p.set('rede_social_id', f.rede_social_id)
  if (f.canal) p.set('canal', f.canal)
  // O tipo do chamado (os chips do grupo Site, RF6): só no grupo Site inteiro.
  const tipo = tipoChamadoAtivo(f)
  if (tipo) p.set('tipo_chamado', tipo)
  p.set('filtro', f.filtro || 'todas')
  if (f.q) p.set('q', f.q)
  if (antesDe) p.set('antes_de', antesDe)
  p.set('limite', String(LIMITE))
  return p.toString()
}

async function carregarLista() {
  const g = ++geracao
  carregando.value = true
  listaErro.value = null
  try {
    const r = await api<Pagina>(`/api/atendimento/conversas?${params()}`)
    if (g !== geracao) return
    itens.value = r.itens || []
    proximo.value = r.proximo || null
    temPaginasExtras = false
    polling.marcar()
  } catch (e: any) {
    if (g !== geracao) return
    listaErro.value = erroDaApi(e, 'Não consegui carregar as conversas').texto
  } finally {
    if (g === geracao) carregando.value = false
  }
}

// Atualização automática: relê só a 1ª página e costura com o que já foi
// carregado pelo "carregar mais" — quem rolou até a 3ª página não é jogado
// de volta para o topo a cada 30 s. O que ficou para trás da 1ª página nova
// segue como estava até a próxima troca de filtro.
async function atualizarLista() {
  if (carregando.value || carregandoMais.value) return
  const g = geracao
  try {
    const r = await api<Pagina>(`/api/atendimento/conversas?${params()}`)
    if (g !== geracao) return
    const novos = r.itens || []
    const ids = new Set(novos.map((c) => c.id))
    const ultimo = novos[novos.length - 1] ?? null
    const filtro = filtros.value.filtro || 'todas'
    // Sem `proximo` na 1ª página nova, ela já tem tudo: as páginas extras
    // viraram velhas e saem. O corte é na ORDEM DA ABA ("Falta responder"
    // vai pelo prazo, as outras pela mensagem mais recente).
    const manter = temPaginasExtras && !!ultimo && podeCosturar(ultimo, filtro) && !!r.proximo
    const resto = manter && ultimo
      ? itens.value.filter((c) => !ids.has(c.id) && vemDepoisNaLista(c, ultimo, filtro))
      : []
    itens.value = [...novos, ...resto]
    if (!manter) {
      temPaginasExtras = false
      proximo.value = r.proximo || null
    }
    listaErro.value = null
  } catch {
    // Falha no automático não apaga a lista: o próximo tique tenta de novo.
  }
}

async function carregarMais() {
  if (!proximo.value || carregandoMais.value) return
  const g = geracao
  carregandoMais.value = true
  try {
    const r = await api<Pagina>(`/api/atendimento/conversas?${params(proximo.value)}`)
    if (g !== geracao) return
    const ids = new Set(itens.value.map((c) => c.id))
    itens.value = [...itens.value, ...(r.itens || []).filter((c) => !ids.has(c.id))]
    proximo.value = r.proximo || null
    temPaginasExtras = true
  } catch (e: any) {
    listaErro.value = erroDaApi(e, 'Não consegui carregar mais conversas').texto
  } finally {
    carregandoMais.value = false
  }
}

// A conversa aberta mudou (enviou, fechou, atribuiu, a IA sugeriu): a linha
// dela na lista acompanha sem esperar o próximo tique. Se mudou o que os
// contadores contam (aguardando, "a conferir"), o resumo também é relido —
// senão o "A conferir (1)" ficava lá até 30 s depois de a pessoa marcar.
function aoMudarConversa(c: ConversaResumo) {
  const i = itens.value.findIndex((x) => x.id === c.id)
  const antes = i >= 0 ? itens.value[i] : null
  if (i >= 0) itens.value[i] = { ...itens.value[i], ...c }
  // A etiqueta também (troca à mão): as contagens do menu Filtrar acompanham.
  if (antes && (antes.aguardando_resposta !== c.aguardando_resposta || !!antes.envio_a_conferir !== !!c.envio_a_conferir
    || (c.etiqueta !== undefined && (antes.etiqueta ?? null) !== (c.etiqueta ?? null)))) {
    void carregarResumo()
  }
}

// ─── respostas prontas (para o menu da conversa) ────────────────────────────
const modelos = ref<Modelo[]>([])
async function carregarModelos() {
  try {
    modelos.value = comoLista<Modelo>(await api<unknown>('/api/atendimento/modelos'), 'modelos')
  } catch {
    modelos.value = []
  }
}

// ─── nomes dos assuntos da IA ───────────────────────────────────────────────
// A sugestão diz o assunto pelo código ("prazo_envio"); o nome vem do manual
// (GET /categorias), que pode ter assunto novo que a lista fixa da tela não
// conhece. Só no navegador e sem aviso: falhou, fica o nome fixo.
onMounted(async () => {
  try {
    registrarCategorias(categoriasDe(await api<unknown>('/api/atendimento/categorias')))
  } catch {
    // API antiga (sem /categorias) ou fora do ar: os nomes fixos bastam
  }
})

// ─── atualização automática ─────────────────────────────────────────────────
// Registrada antes do primeiro await (os ganchos de ciclo de vida precisam do
// componente ainda montando).
const polling = usePollingVisivel(async () => {
  // Na aba "Lojas e modo", só o resumo: a faixa e a marca das lojas sem ler
  // somem sozinhas quando a loja volta a ler.
  if (aba.value === 'lojas') return carregarResumo()
  if (aba.value !== 'caixa') return
  await Promise.all([atualizarLista(), carregarResumo()])
}, 30_000)

// Barra de lojas: recolhida (só ícone e número, como no print do Duoke) ou
// com o nome. Lembrado neste navegador; sem escolha salva, começa recolhida
// em tela menor que 1536 px (senão a conversa fica espremida).
const LOJAS_KEY = 'davinci.atendimento.lojasRecolhidas'
const lojasRecolhidas = ref(false)
// Só a escolha da PESSOA fica salva — o padrão pela largura, não.
let lojasEscolhidaPelaPessoa = false
watch(lojasRecolhidas, (v) => {
  if (!lojasEscolhidaPelaPessoa) return
  try {
    localStorage.setItem(LOJAS_KEY, v ? '1' : '0')
  } catch {
    // sem localStorage — só não lembra
  }
})

// Filtros lembrados neste navegador (conveniência de quem usa a tela o dia
// todo). Lido só no cliente e sempre com try: aba anônima/bloqueio de
// armazenamento não pode quebrar a página.
onMounted(() => {
  try {
    const salvo = localStorage.getItem(LOJAS_KEY)
    lojasRecolhidas.value = salvo === null ? window.innerWidth < 1536 : salvo === '1'
  } catch {
    lojasRecolhidas.value = window.innerWidth < 1536
  }
  // Depois do valor inicial: daqui em diante, mudança é clique da pessoa.
  void nextTick(() => { lojasEscolhidaPelaPessoa = true })
  try {
    const raw = localStorage.getItem(FILTROS_KEY)
    if (raw) {
      const salvo = JSON.parse(raw)
      if (salvo && typeof salvo === 'object') {
        const f = { ...filtros.value }
        // A aba (`filtro`) não volta: abre sempre em "Todas", como o Duoke
        // (01/10/2026). Loja e plataforma escolhidas, sim.
        for (const k of ['plataforma', 'integration_id', 'canal', 'externo_ref', 'rede_social_id'] as const) {
          if (typeof salvo[k] === 'string') f[k] = salvo[k]
        }
        if (JSON.stringify(f) !== JSON.stringify(filtros.value)) filtros.value = f
      }
    }
  } catch {
    // sem localStorage — começa no padrão
  }
})
watch(filtros, (f) => {
  try {
    const { q: _q, ...resto } = f
    localStorage.setItem(FILTROS_KEY, JSON.stringify(resto))
  } catch {
    // sem localStorage — só não lembra
  }
  void carregarLista()
}, { deep: true })

watch(aba, (a) => {
  if (a === 'caixa') {
    void carregarResumo()
    void atualizarLista()
  }
})

async function atualizarTudo() {
  await Promise.all([carregarResumo(), carregarLista(), carregarModelos()])
}

await Promise.all([carregarResumo(), carregarLista(), carregarModelos()])

function selecionar(id: string) {
  selecionada.value = id
}
function abrirAba(a: string) {
  if (ABAS.some((x) => x.value === a)) aba.value = a as Aba
}
// O registro das Automáticas abre a conversa na Caixa.
function abrirConversaNaCaixa(id: string) {
  aba.value = 'caixa'
  selecionar(id)
}

// A Caixa ocupa a altura da janela para as colunas rolarem cada uma por si,
// como num app de mensagens. A altura sai do topo REAL da Caixa (medido), não
// de uma conta fixa: o cabeçalho, os avisos e as abas mudam de altura com a
// largura (em 800 px os avisos quebravam e a Caixa passava da janela).
// - Cabe com folga até o fim da janela: vai até o fim, sem rolar a página.
// - Não cabe (notebook de 720 px): a Caixa fica com a janela inteira abaixo
//   da barra do topo, e ao abrir uma conversa a página rola até ela — com a
//   conta fixa sobravam ~230 px para a conversa, e o painel da IA ficava
//   abaixo da dobra.
// Topbar (AppTopbar, h-14, grudada no topo) + um respiro.
const TOPO_FIXO = 56 + 8
// Abaixo disto a conversa com o painel da IA não se lê: melhor rolar a página.
const ALTURA_CONFORTO = 600
const ALTURA_MIN = 420
const topoEl = ref<HTMLElement | null>(null)
const caixaEl = ref<HTMLElement | null>(null)
const { height: alturaJanela } = useWindowSize()
const topoCaixa = ref(0)
// O respiro de baixo é o padding do <main> do layout.
const folgaBaixo = ref(24)
function medirCaixa() {
  const el = caixaEl.value
  // Escondida (v-show em outra aba) mede zero.
  if (!el || aba.value !== 'caixa') return
  topoCaixa.value = el.getBoundingClientRect().top + window.scrollY
  const main = el.closest('main')
  const pb = main ? Number.parseFloat(getComputedStyle(main).paddingBottom) : Number.NaN
  folgaBaixo.value = Number.isFinite(pb) ? pb : 24
}
// O que fica acima da Caixa muda de altura (avisos, erro, largura): mede de novo.
useResizeObserver(topoEl, medirCaixa)
watch(alturaJanela, () => medirCaixa())
watch(aba, (a) => {
  if (a === 'caixa') void nextTick(medirCaixa)
})
onMounted(() => { void nextTick(medirCaixa) })
const alturaCaixa = computed(() => {
  const janela = alturaJanela.value
  // No servidor e antes da primeira medida: a conta aproximada de antes.
  if (!janela || !topoCaixa.value) return 'calc(100dvh - 15rem)'
  const cabe = janela - topoCaixa.value - folgaBaixo.value
  if (cabe >= ALTURA_CONFORTO) return `${Math.floor(cabe)}px`
  return `${Math.max(Math.floor(janela - TOPO_FIXO - folgaBaixo.value), ALTURA_MIN)}px`
})
// Abriu uma conversa e a Caixa não está inteira na tela: rola a página até
// ela ficar logo abaixo da barra do topo (uma vez — depois ela já cabe).
function mostrarCaixaInteira() {
  const el = caixaEl.value
  if (!el || aba.value !== 'caixa') return
  const r = el.getBoundingClientRect()
  if (r.top >= TOPO_FIXO - 8 && r.bottom <= window.innerHeight) return
  let suave = true
  try {
    suave = !window.matchMedia('(prefers-reduced-motion: reduce)').matches
  } catch {
    // sem matchMedia — rola suave
  }
  window.scrollTo({ top: Math.max(0, r.top + window.scrollY - TOPO_FIXO), behavior: suave ? 'smooth' : 'auto' })
}
watch(selecionada, (id) => {
  if (id && import.meta.client) void nextTick(mostrarCaixaInteira)
})
</script>

<template>
  <div class="space-y-3">
    <!-- tudo acima da Caixa: medido para a Caixa ir até o fim da janela -->
    <div ref="topoEl" class="space-y-3">
    <PageHeader title="Atendimento" description="Conversas de todas as lojas numa caixa só, com o pedido ao lado e a resposta sugerida pela IA.">
      <template #actions>
        <Button size="sm" variant="outline" :disabled="carregando" @click="atualizarTudo">
          <RotateCcw class="mr-1.5 size-4" :class="{ 'animate-spin': carregando }" />
          atualizar
        </Button>
      </template>
    </PageHeader>

    <!-- quem só lê (fase de observação): um aviso discreto, uma linha -->
    <div
      v-if="soLeitura"
      v-show="aba !== 'mail'"
      class="flex items-center gap-1.5 rounded-md border border-sky-300/60 bg-sky-50 px-3 py-1 text-xs text-sky-900 dark:border-sky-800/60 dark:bg-sky-900/20 dark:text-sky-200"
      data-aviso-so-leitura
    >
      <Eye class="size-3.5 shrink-0" aria-hidden="true" />
      <span>{{ AVISO_SO_LEITURA }}</span>
    </div>

    <!-- o que está desligado no servidor: uma linha curta; a frase inteira no title e em "o que isso quer dizer?" -->
    <div
      v-if="avisos.length"
      v-show="aba !== 'mail'"
      class="rounded-md border px-3 py-1.5 text-xs"
      :class="avisos.some((a) => a.forte) ? 'border-red-500/40 bg-red-500/10' : 'border-amber-500/40 bg-amber-500/10'"
    >
      <div class="flex items-start gap-2">
        <TriangleAlert class="mt-px size-4 shrink-0 text-amber-600 dark:text-amber-400" />
        <div class="flex min-w-0 flex-1 flex-wrap items-center gap-x-1.5 gap-y-0.5">
          <template v-for="(a, i) in avisos" :key="a.chave">
            <span v-if="i" class="text-muted-foreground" aria-hidden="true">·</span>
            <span :title="a.texto" :class="a.forte ? 'font-semibold text-red-700 dark:text-red-300' : 'text-amber-900 dark:text-amber-200'">{{ a.curto }}</span>
          </template>
          <button
            type="button"
            class="ml-auto shrink-0 text-muted-foreground underline hover:text-foreground"
            :aria-expanded="avisosAbertos"
            @click="avisosAbertos = !avisosAbertos"
          >{{ avisosAbertos ? 'esconder' : 'o que isso quer dizer?' }}</button>
        </div>
      </div>
      <ul v-if="avisosAbertos" class="mt-1 space-y-0.5 pl-6">
        <li v-for="a in avisos" :key="a.chave" :class="a.forte ? 'font-semibold text-red-700 dark:text-red-300' : 'text-amber-900 dark:text-amber-200'">{{ a.texto }}</li>
      </ul>
    </div>
    <div v-if="resumoErro && !resumo" v-show="aba !== 'mail'" class="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-600 dark:text-red-400">
      {{ resumoErro }}
    </div>

    <!-- abas -->
    <div class="flex gap-1 overflow-x-auto border-b">
      <button
        v-for="a in ABAS"
        :key="a.value"
        type="button"
        class="-mb-px inline-flex shrink-0 items-center gap-1.5 border-b-2 px-3 py-2 text-sm"
        :class="aba === a.value ? 'border-primary font-medium' : 'border-transparent text-muted-foreground hover:text-foreground'"
        @click="aba = a.value"
      >
        <component :is="a.icon" class="size-4" />
        {{ a.label }}
        <span
          v-if="a.value === 'lojas' && semLer.length"
          class="ml-0.5 inline-flex min-w-4 items-center justify-center rounded-full bg-red-600 px-1 text-[10px] font-semibold leading-4 text-white"
          :title="tituloLeituraParada(semLer)"
          :aria-label="tituloLeituraParada(semLer)"
          data-marca-leitura-parada
        >{{ semLer.length }}</span>
      </button>
    </div>

    <!-- lojas sem ler: no topo da Caixa (com o link para a aba) e na própria aba "Lojas e modo" -->
    <AtendimentoLeituraParada
      v-if="aba === 'caixa' || aba === 'lojas'"
      :itens="semLer"
      :agora="agora"
      :link="aba === 'caixa'"
      @abrir-lojas="aba = 'lojas'"
    />
    <!-- A plataforma (08/10/2026: "ver só Mercado Livre, só Shopee"): um botão que abre o menu com a logo e o "falta responder" de cada uma, acima da Caixa -->
    <AtendimentoFiltroPlataforma v-if="aba === 'caixa'" v-model:filtros="filtros" :resumo="resumo" />
    </div>

    <!-- Caixa: lojas | fila | conversa + pedido -->
    <div
      v-show="aba === 'caixa'"
      ref="caixaEl"
      class="flex min-h-[420px] overflow-hidden rounded-lg border bg-card"
      :style="{ height: alturaCaixa }"
    >
      <aside
        class="hidden min-h-0 shrink-0 flex-col border-r bg-muted/30 lg:flex"
        :class="lojasRecolhidas ? 'w-[64px]' : 'w-[196px]'"
      >
        <AtendimentoLojas v-model:filtros="filtros" v-model:recolhida="lojasRecolhidas" :resumo="resumo" />
      </aside>
      <aside
        class="min-h-0 w-full flex-col border-r lg:flex lg:w-[300px] lg:shrink-0 2xl:w-[330px]"
        :class="selecionada ? 'hidden' : 'flex'"
      >
        <AtendimentoLista
          v-model:filtros="filtros"
          :itens="itens"
          :carregando="carregando"
          :carregando-mais="carregandoMais"
          :erro="listaErro"
          :tem-mais="!!proximo"
          :selecionada="selecionada"
          :resumo="resumo"
          :agora="agora"
          :meu-id="auth.user?.id || null"
          @selecionar="selecionar"
          @carregar-mais="carregarMais"
          @recarregar="carregarLista"
        />
      </aside>
      <section class="min-h-0 min-w-0 flex-1 lg:flex" :class="selecionada ? 'flex' : 'hidden'">
        <AtendimentoConversa
          v-if="selecionada"
          :conversa-id="selecionada"
          :can-edit="canEdit"
          :can-sugerir="canSugerir"
          :modelos="modelos"
          :flags="resumo?.flags || null"
          :meu-id="auth.user?.id || null"
          :lojas="resumo?.lojas || []"
          :ativa="aba === 'caixa'"
          @mudou="aoMudarConversa"
          @voltar="selecionada = null"
          @abrir-aba="abrirAba"
          @abrir-conversa="selecionar"
        />
        <div v-else class="flex flex-1 items-center justify-center p-6">
          <EmptyState :icon="Inbox" title="Escolha uma conversa" description="A conversa abre aqui, com o que a IA responderia e o pedido da loja ao lado. Dica: com o foco na lista, ↑ e ↓ andam pelas conversas." />
        </div>
      </section>
    </div>

    <AtendimentoCanais v-if="aba === 'lojas'" :can-edit="canEdit" :is-admin="isAdmin" :lojas="resumo?.lojas || []" @mudou="carregarResumo" />
    <!-- E-mail: a Central (Caixas) e, para quem mexe, as Filas do e-mail das lojas (08/10/2026) -->
    <AtendimentoMail v-if="aba === 'mail'" :is-admin="isAdmin" :can-operate="mexe" :stores="resumo?.lojas || []" @open-conversation="abrirConversaNaCaixa" />
    <AtendimentoManual v-if="aba === 'manual'" :can-edit="canEdit" :can-delete="canDelete" />
    <AtendimentoModelos v-if="aba === 'modelos'" :can-edit="canEdit" :can-delete="canDelete" @mudou="(l: Modelo[]) => (modelos = l)" />
    <AtendimentoAutomaticas v-if="aba === 'automaticas'" :can-edit="canEdit" @abrir-conversa="abrirConversaNaCaixa" />
    <AtendimentoMetricas v-if="aba === 'metricas'" />
  </div>
</template>
