<script setup lang="ts">
// Sistema › Mudanças (17/11/2026). O chefe pede mudanças no próprio DaVinci
// pelo Claude dele; um robô faz a mudança e roda os testes; aqui os
// aprovadores (heisenberg e thorfinn) veem o pedido, o que o robô mudou, e
// aprovam (vai para o ar) ou recusam (com motivo). Para todos os outros esta
// página responde igual a uma página que não existe — como o Histórico.
// Link direto para uma mudança: /mudancas?id=<id> abre a gaveta dela.
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { Check, GitPullRequest, RefreshCw, X } from 'lucide-vue-next'
import { isoDateBrt, isoDaysAgo, isoToday } from '~/lib/date'

definePageMeta({
  middleware: [
    (to) => {
      const auth = useAuthStore()
      if (auth.user?.mudancas !== true) {
        // Mesma mensagem do Nuxt para rota inexistente.
        return abortNavigation(
          createError({ statusCode: 404, statusMessage: `Page not found: ${to.fullPath}`, fatal: true }),
        )
      }
    },
  ],
})

type Status =
  | 'pedida' | 'fazendo' | 'pronta' | 'aprovada' | 'publicando'
  | 'publicada' | 'recusada' | 'precisa_equipe' | 'falhou' | 'respondida'
type ItemLista = {
  id: string
  codigo: string
  // 'pergunta' (P-N, 0497): o robô só leu o código e respondeu direto a quem perguntou
  tipo?: 'mudanca' | 'pergunta' | string
  titulo: string
  status: Status | string
  status_texto: string
  pedido_por_nome: string
  criado_em: string
  atualizado_em: string
}
type Arquivo = { caminho: string; mais: number; menos: number }
type Detalhe = ItemLista & {
  descricao: string
  onde: string | null
  resumo: string | null
  testes: string | null
  diff: string | null
  diff_sha: string | null
  arquivos: Arquivo[]
  branch: string | null
  commit: string | null
  aprovado_por_nome: string | null
  aprovado_em: string | null
  recusado_por_nome: string | null
  recusado_em: string | null
  motivo_recusa: string | null
  publicado_em: string | null
  erro: string | null
  pode_aprovar: boolean
  // aprovada e a publicação esperando: dá para desfazer a aprovação
  pode_cancelar?: boolean
  // pergunta: a resposta do robô (foi direto para quem perguntou)
  resposta?: string | null
}

const { api } = useApi()
const toast = useToasts()
const route = useRoute()

// ── Situações ──────────────────────────────────────────────────────────────
const PILL: Record<string, string> = {
  pedida: 'pill-muted',
  fazendo: 'pill-info',
  pronta: 'pill-warning',
  aprovada: 'pill-info',
  publicando: 'pill-info',
  publicada: 'pill-success',
  recusada: 'pill-muted',
  precisa_equipe: 'pill-danger',
  falhou: 'pill-danger',
  respondida: 'pill-success',
}
// Só por garantia: o texto vem pronto da API.
const TEXTO: Record<string, string> = {
  pedida: 'Na fila do robô',
  fazendo: 'Robô fazendo',
  pronta: 'Esperando aprovação',
  aprovada: 'Aprovada — vai para o ar',
  publicando: 'Publicando',
  publicada: 'No ar',
  recusada: 'Recusada',
  precisa_equipe: 'Precisa da equipe',
  falhou: 'Falhou',
  respondida: 'Respondida',
}
const pill = (s: string) => PILL[s] || 'pill-muted'
const textoStatus = (m: { status: string; status_texto?: string | null }) => m.status_texto || TEXTO[m.status] || m.status
// Enquanto o robô ainda mexe, a gaveta aberta se atualiza sozinha. Na "pronta"
// NÃO: o que se aprova é exatamente a versão que está na tela.
const EM_ANDAMENTO = ['pedida', 'fazendo', 'aprovada', 'publicando']

// ── Erros: frase de gente ──────────────────────────────────────────────────
function codigoErro(e: any): string | null {
  const d = e?.data?.detail
  return d && typeof d === 'object' && !Array.isArray(d) && typeof d.code === 'string' ? d.code : null
}
function statusErro(e: any): number | null {
  return e?.statusCode ?? e?.status ?? e?.response?.status ?? null
}
function mensagemErro(e: any): string {
  const d = e?.data?.detail
  if (statusErro(e) === 404) return 'Não encontrada.'
  if (typeof d === 'string' && d) return d
  if (d && typeof d === 'object' && !Array.isArray(d) && typeof d.detalhe === 'string' && d.detalhe) return d.detalhe
  const st = statusErro(e)
  if (st === 404) return 'Não encontrada.'
  if (st && st >= 500) return 'O servidor deu erro. Tente de novo em instantes.'
  if (!st) return 'Sem conexão com o servidor. Confira a internet e tente de novo.'
  return e?.message || 'Erro desconhecido.'
}

// ── Lista ──────────────────────────────────────────────────────────────────
type Filtro = 'pronta' | 'abertas' | 'todas'
const FILTROS: { valor: Filtro; rotulo: string; vazio: string }[] = [
  { valor: 'pronta', rotulo: 'Esperando aprovação', vazio: 'Nenhuma mudança esperando aprovação.' },
  { valor: 'abertas', rotulo: 'Abertas', vazio: 'Nenhuma mudança em andamento.' },
  { valor: 'todas', rotulo: 'Todas', vazio: 'Nenhum pedido de mudança ainda.' },
]
const LIMITE = 100
const filtro = ref<Filtro>('pronta')
const itens = ref<ItemLista[]>([])
// Quando o robô do Mac perguntou por trabalho pela última vez (null = nunca).
const roboVistoEm = ref<string | null>(null)
// O servidor decide (olha a fila inteira, não só o filtro da tela, e o relógio
// dele — o do computador de quem olha pode estar errado).
const roboParado = ref(false)
const carregando = ref(false)
const carregou = ref(false)
const erroLista = ref<string | null>(null)
const textoVazio = computed(() => FILTROS.find((f) => f.valor === filtro.value)?.vazio || 'Nada por aqui.')

let seq = 0
// `silencioso` = atualização automática a cada 30 s: sem toast (não empilha um
// aviso a cada meio minuto se a rede cair) — fica só a faixa vermelha.
async function carregar(silencioso = false) {
  const meu = ++seq
  if (!silencioso) carregando.value = true
  try {
    const query: Record<string, string | number> = { limite: LIMITE }
    if (filtro.value !== 'todas') query.status = filtro.value
    const r = await api<{ itens: ItemLista[]; robo_visto_em?: string | null; robo_parado?: boolean }>(
      '/api/sistema-mudancas',
      { query },
    )
    if (meu !== seq) return
    itens.value = Array.isArray(r?.itens) ? r.itens : []
    roboVistoEm.value = typeof r?.robo_visto_em === 'string' ? r.robo_visto_em : null
    roboParado.value = r?.robo_parado === true
    erroLista.value = null
    carregou.value = true
  } catch (e: any) {
    if (meu !== seq) return
    const msg = mensagemErro(e)
    erroLista.value = `Não deu para carregar a lista. ${msg}`
    if (!silencioso) toast.error('Não deu para carregar as mudanças', msg)
  } finally {
    if (meu === seq) carregando.value = false
  }
}
function trocarFiltro(f: Filtro) {
  if (filtro.value === f) return
  filtro.value = f
  itens.value = []
  carregou.value = false
  carregar()
}

let relogio: ReturnType<typeof setInterval> | null = null
function tique() {
  carregar(true)
  const d = detalhe.value
  if (d && !abrindo.value && EM_ANDAMENTO.includes(d.status)) carregarDetalhe(d.id, true)
}
onMounted(() => {
  carregar()
  const id = route.query.id
  if (typeof id === 'string' && id) abrir(id)
  relogio = setInterval(tique, 30_000)
})
onUnmounted(() => {
  if (relogio) clearInterval(relogio)
  relogio = null
})

// ── Datas: "hoje 16:09", "ontem 22:30", "16/09 12:09" (padrão do Histórico)
const BRT = 'America/Sao_Paulo'
function fmtRel(v: string | null | undefined): string {
  if (!v) return '—'
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return v
  const dia = isoDateBrt(d)
  const hora = d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', timeZone: BRT })
  if (dia === isoToday()) return `hoje ${hora}`
  if (dia === isoDaysAgo(1)) return `ontem ${hora}`
  const mesmoAno = dia.slice(0, 4) === isoToday().slice(0, 4)
  const data = d.toLocaleDateString('pt-BR', {
    day: '2-digit', month: '2-digit', ...(mesmoAno ? {} : { year: '2-digit' }), timeZone: BRT,
  })
  return `${data} ${hora}`
}
function fmtCompleta(v: string | null | undefined): string {
  if (!v) return '—'
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return v
  return d.toLocaleString('pt-BR', { timeZone: BRT, dateStyle: 'short', timeStyle: 'short' })
}

// ── Detalhe (gaveta) ───────────────────────────────────────────────────────
const detalhe = ref<Detalhe | null>(null)
const abrindo = ref(false)
const testesAbertos = ref(false)
const diffInteiro = ref(false)
// Fechar (ou abrir outra) enquanto carrega descarta a resposta que chegar depois.
let seqDetalhe = 0
async function carregarDetalhe(id: string, silencioso = false) {
  const meu = ++seqDetalhe
  if (!silencioso) abrindo.value = true
  try {
    const d = await api<Detalhe>(`/api/sistema-mudancas/${encodeURIComponent(id)}`)
    if (meu === seqDetalhe) detalhe.value = d
  } catch (e: any) {
    if (meu !== seqDetalhe || silencioso) return
    toast.error('Não deu para abrir essa mudança', mensagemErro(e))
  } finally {
    if (meu === seqDetalhe) abrindo.value = false
  }
}
function abrir(id: string) {
  detalhe.value = null
  testesAbertos.value = false
  diffInteiro.value = false
  carregarDetalhe(id)
}
function fecharDetalhe() {
  seqDetalhe++
  abrindo.value = false
  detalhe.value = null
  confirmarAberto.value = false
  recusaAberta.value = false
}

const podeDecidir = computed(() => detalhe.value?.status === 'pronta' && detalhe.value?.pode_aprovar === true)
const podeCancelar = computed(() => detalhe.value?.status === 'aprovada' && detalhe.value?.pode_cancelar === true)
const parou = computed(() => detalhe.value?.status === 'falhou' || detalhe.value?.status === 'precisa_equipe')
// Quando parou sem `erro`, o resumo vai para a faixa vermelha (e não repete embaixo).
const textoParou = computed(() => (parou.value ? detalhe.value?.erro || detalhe.value?.resumo || null : null))
const mostraResumo = computed(() => !!detalhe.value?.resumo && !(parou.value && !detalhe.value?.erro))
const totais = computed(() => {
  const a = detalhe.value?.arquivos || []
  return { mais: a.reduce((s, x) => s + (x.mais || 0), 0), menos: a.reduce((s, x) => s + (x.menos || 0), 0) }
})

// ── Diff colorido ──────────────────────────────────────────────────────────
// Um diff gigante (ex. arquivo gerado) travaria o navegador: mostra o começo
// e um botão para ver o resto.
const MAX_LINHAS = 3000
// Cabeçalho de arquivo (index/---/+++/new file mode…) só existe entre o
// "diff --git" e o primeiro "@@" — assim uma linha removida que começa com
// "--" (comentário SQL) não vira cabeçalho por engano.
type LinhaDiff = { texto: string; cls: string }
// Caracteres invisíveis/de controle (U+202E e cia.) fazem o navegador mostrar
// um código diferente do que roda ("Trojan Source"): viram um marcador visível
// e uma faixa vermelha. (O servidor já recusa diff assim; isto é a segunda trava.)
const INVISIVEL = /(?![\t ])[\p{Cf}\p{Cc}\p{Co}\p{Zl}\p{Zp}\p{Zs}]/gu
const marcar = (l: string) => l.replace(INVISIVEL, (c) => `⟦U+${c.codePointAt(0)!.toString(16).toUpperCase().padStart(4, '0')}⟧`)
const temInvisiveis = computed(() => {
  const diff = detalhe.value?.diff
  return !!diff && new RegExp(INVISIVEL.source, 'u').test(diff.replace(/\n/g, ''))
})
const linhasDiff = computed<LinhaDiff[]>(() => {
  const diff = detalhe.value?.diff
  if (!diff) return []
  let cabecalho = false
  return diff.replace(/\n$/, '').split('\n').map(marcar).map((l) => {
    if (l.startsWith('diff --git')) {
      cabecalho = true
      return { texto: l, cls: 'mt-3 first:mt-0 border-t first:border-t-0 font-semibold text-foreground bg-muted/60' }
    }
    if (l.startsWith('@@')) {
      cabecalho = false
      return { texto: l, cls: 'text-sky-700 bg-sky-500/10 dark:text-sky-300' }
    }
    if (cabecalho) return { texto: l, cls: 'font-semibold text-muted-foreground' }
    if (l.startsWith('+')) return { texto: l, cls: 'text-emerald-800 bg-emerald-500/10 dark:text-emerald-300' }
    if (l.startsWith('-')) return { texto: l, cls: 'text-red-800 bg-red-500/10 dark:text-red-300' }
    if (l.startsWith('\\')) return { texto: l, cls: 'italic text-muted-foreground' }
    return { texto: l, cls: '' }
  })
})
const linhasVisiveis = computed(() => (diffInteiro.value ? linhasDiff.value : linhasDiff.value.slice(0, MAX_LINHAS)))

// ── Aprovar / Recusar ──────────────────────────────────────────────────────
const enviando = ref<null | 'aprovar' | 'recusar'>(null)
// A mesma janela de motivo serve para recusar (pronta) e para cancelar uma
// aprovação cuja publicação ficou parada (aprovada).
const modoRecusa = ref<'recusar' | 'cancelar'>('recusar')
const confirmarAberto = ref(false)
const recusaAberta = ref(false)
const motivo = ref('')
const motivoOk = computed(() => motivo.value.trim().length >= 3)

async function aprovar() {
  const d = detalhe.value
  if (!d || enviando.value) return
  enviando.value = 'aprovar'
  try {
    const novo = await api<Detalhe>(`/api/sistema-mudancas/${encodeURIComponent(d.id)}/aprovar`, {
      method: 'POST',
      body: { diff_sha: d.diff_sha },
    })
    confirmarAberto.value = false
    if (detalhe.value?.id === d.id && novo && typeof novo === 'object') detalhe.value = novo
    toast.success(`${d.codigo} aprovada — o robô vai publicar`)
    carregar(true)
  } catch (e: any) {
    confirmarAberto.value = false
    const code = codigoErro(e)
    if (code === 'diff_mudou') {
      toast.error('A mudança foi refeita depois que você abriu — abra de novo para ver a versão nova')
      carregarDetalhe(d.id)
    } else if (code === 'nao_esta_pronta') {
      toast.error(`A ${d.codigo} não está mais esperando aprovação`, mensagemErro(e))
      carregarDetalhe(d.id)
      carregar(true)
    } else {
      toast.error(`Não deu para aprovar a ${d.codigo}`, mensagemErro(e))
    }
  } finally {
    enviando.value = null
  }
}

function abrirRecusa(modo: 'recusar' | 'cancelar' = 'recusar') {
  modoRecusa.value = modo
  motivo.value = ''
  recusaAberta.value = true
}
async function recusar() {
  const d = detalhe.value
  if (!d || enviando.value || !motivoOk.value) return
  const cancelando = modoRecusa.value === 'cancelar'
  enviando.value = 'recusar'
  try {
    const novo = await api<Detalhe>(`/api/sistema-mudancas/${encodeURIComponent(d.id)}/${cancelando ? 'cancelar' : 'recusar'}`, {
      method: 'POST',
      body: { motivo: motivo.value.trim() },
    })
    recusaAberta.value = false
    if (detalhe.value?.id === d.id && novo && typeof novo === 'object') detalhe.value = novo
    toast.success(cancelando ? `Aprovação da ${d.codigo} cancelada` : `${d.codigo} recusada`, 'O robô não vai publicar esta mudança.')
    carregar(true)
  } catch (e: any) {
    const code = codigoErro(e)
    if (code === 'nao_esta_aprovada') {
      recusaAberta.value = false
      toast.error(`A ${d.codigo} não está mais esperando para publicar`, mensagemErro(e))
      carregarDetalhe(d.id)
      carregar(true)
    } else if (code === 'nao_esta_pronta') {
      recusaAberta.value = false
      toast.error(`A ${d.codigo} não está mais esperando aprovação`, mensagemErro(e))
      carregarDetalhe(d.id)
      carregar(true)
    } else if (statusErro(e) === 422) {
      // A janela continua aberta com o texto digitado.
      toast.error('Escreva o motivo da recusa (de 3 a 1000 letras)')
    } else {
      toast.error(`Não deu para recusar a ${d.codigo}`, mensagemErro(e))
    }
  } finally {
    enviando.value = null
  }
}
</script>

<template>
  <div>
    <PageHeader
      title="Mudanças do sistema"
      description="Pedidos de mudança feitos pelo Claude. O robô prepara e testa; só vai para o ar depois que você aprova — ou sozinho, nos pedidos de quem tem aprovação automática (dá para cancelar antes de publicar)."
    >
      <template #actions>
        <button
          class="inline-flex items-center gap-1.5 h-9 rounded-md border bg-background px-3 text-sm hover:bg-muted"
          :disabled="carregando"
          @click="carregar()"
        >
          <RefreshCw class="size-4" :class="{ 'animate-spin': carregando }" /> atualizar
        </button>
      </template>
    </PageHeader>

    <div class="mb-3 flex flex-wrap items-center gap-2">
      <div class="inline-flex rounded-md border p-px text-sm" role="tablist" aria-label="Quais mudanças mostrar">
        <button
          v-for="f in FILTROS"
          :key="f.valor"
          type="button"
          role="tab"
          class="rounded px-3 py-1.5"
          :aria-selected="filtro === f.valor"
          :class="filtro === f.valor ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground'"
          @click="trocarFiltro(f.valor)"
        >
          {{ f.rotulo }}
        </button>
      </div>
      <span class="ml-auto text-xs text-muted-foreground">atualiza sozinha a cada 30 segundos</span>
    </div>

    <div v-if="erroLista" class="mb-3 rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-500/40 dark:bg-red-500/10 dark:text-red-300">
      {{ erroLista }}
    </div>
    <div v-if="roboParado" class="mb-3 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-300">
      O robô do Mac não aparece há mais de 10 minutos<span v-if="roboVistoEm"> (última vez: {{ fmtCompleta(roboVistoEm) }})</span>
      e há pedido esperando por ele. Confira se o Mac está ligado e o robô de mudanças carregado.
    </div>

    <div class="table-card overflow-x-auto">
      <table class="w-full text-sm">
        <thead>
          <tr>
            <th class="text-left whitespace-nowrap">Código</th>
            <th class="text-left">Pedido</th>
            <th class="text-left whitespace-nowrap">Quem pediu</th>
            <th class="text-left whitespace-nowrap">Quando</th>
            <th class="text-left">Situação</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="carregando && itens.length === 0">
            <td colspan="5" class="py-10 text-center text-muted-foreground">Carregando…</td>
          </tr>
          <tr v-else-if="carregou && itens.length === 0">
            <td colspan="5" class="py-10 text-center text-muted-foreground">
              <GitPullRequest class="mx-auto mb-2 size-6 opacity-50" />
              {{ textoVazio }}
            </td>
          </tr>
          <tr
            v-for="m in itens"
            :key="m.id"
            class="cursor-pointer hover:bg-muted/40"
            @click="abrir(m.id)"
          >
            <td class="whitespace-nowrap font-mono text-xs font-medium">{{ m.codigo }}</td>
            <td class="max-w-[28rem]">
              <span v-if="m.tipo === 'pergunta'" class="mr-1.5 rounded bg-sky-500/15 px-1.5 py-0.5 text-[11px] font-medium text-sky-800 dark:text-sky-300">Pergunta</span>
              <span class="line-clamp-2 inline">{{ m.titulo }}</span>
            </td>
            <td class="whitespace-nowrap">{{ m.pedido_por_nome || '—' }}</td>
            <td class="whitespace-nowrap text-muted-foreground" :title="`Pedido em ${fmtCompleta(m.criado_em)} · última novidade ${fmtCompleta(m.atualizado_em)}`">
              {{ fmtRel(m.criado_em) }}
            </td>
            <td class="whitespace-nowrap"><span :class="pill(m.status)">{{ textoStatus(m) }}</span></td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-if="itens.length >= LIMITE" class="mt-2 text-xs text-muted-foreground">
      Mostrando as {{ LIMITE }} mais recentes.
    </p>

    <!-- Gaveta do detalhe -->
    <Teleport to="body">
      <div v-if="detalhe || abrindo" class="fixed inset-0 z-50 flex justify-end bg-black/30" @click.self="fecharDetalhe">
        <div class="flex h-full w-full max-w-4xl flex-col bg-background shadow-xl">
          <div class="flex items-start gap-3 border-b px-5 py-4">
            <div class="min-w-0">
              <div v-if="detalhe" class="mb-1 flex flex-wrap items-center gap-2">
                <span class="font-mono text-sm font-semibold">{{ detalhe.codigo }}</span>
                <span :class="pill(detalhe.status)">{{ textoStatus(detalhe) }}</span>
              </div>
              <h2 class="text-lg font-semibold leading-snug">{{ detalhe ? detalhe.titulo : 'Abrindo…' }}</h2>
              <p v-if="detalhe" class="text-sm text-muted-foreground">
                Pedido por {{ detalhe.pedido_por_nome || '—' }} · {{ fmtCompleta(detalhe.criado_em) }}
              </p>
            </div>
            <button class="ml-auto rounded p-1 hover:bg-muted" title="Fechar" @click="fecharDetalhe"><X class="size-5" /></button>
          </div>

          <div class="min-h-0 flex-1 overflow-y-auto px-5 py-4">
            <p v-if="!detalhe" class="py-10 text-center text-sm text-muted-foreground">Carregando…</p>
            <template v-else>
              <!-- Situação em destaque -->
              <div v-if="detalhe.status === 'publicada'" class="mb-4 rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-800 dark:text-emerald-300">
                <b>No ar desde {{ fmtCompleta(detalhe.publicado_em) }}.</b>
                <span v-if="detalhe.aprovado_por_nome"> Aprovada por {{ detalhe.aprovado_por_nome }} em {{ fmtCompleta(detalhe.aprovado_em) }}.</span>
              </div>
              <div v-else-if="detalhe.status === 'aprovada' || detalhe.status === 'publicando'" class="mb-4 rounded-md border border-primary/30 bg-primary/10 px-3 py-2 text-sm">
                Aprovada<span v-if="detalhe.aprovado_por_nome"> por {{ detalhe.aprovado_por_nome }}</span>
                <span v-if="detalhe.aprovado_em"> em {{ fmtCompleta(detalhe.aprovado_em) }}</span>.
                {{ detalhe.status === 'publicando' ? 'O robô está publicando agora.' : 'O robô vai publicar em seguida.' }}
                <p v-if="detalhe.status === 'aprovada' && detalhe.erro" class="mt-1 text-amber-800 dark:text-amber-300">
                  Publicação adiada: {{ detalhe.erro }} — o robô tenta de novo a cada 10 minutos.
                </p>
              </div>
              <div v-else-if="detalhe.status === 'recusada'" class="mb-4 rounded-md border bg-muted/40 px-3 py-2 text-sm">
                <p>
                  <b>Recusada</b><span v-if="detalhe.recusado_por_nome"> por {{ detalhe.recusado_por_nome }}</span>
                  <span v-if="detalhe.recusado_em"> em {{ fmtCompleta(detalhe.recusado_em) }}</span>.
                </p>
                <p v-if="detalhe.motivo_recusa" class="mt-1 whitespace-pre-wrap"><span class="text-muted-foreground">Motivo:</span> {{ detalhe.motivo_recusa }}</p>
              </div>
              <div v-else-if="parou" class="mb-4 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-800 dark:text-red-300">
                <p class="font-semibold">
                  {{ detalhe.status === 'precisa_equipe' ? 'O robô não consegue fazer esta sozinho — precisa da equipe.' : 'O robô não conseguiu terminar esta mudança.' }}
                </p>
                <p v-if="textoParou" class="mt-1 whitespace-pre-wrap break-words">{{ textoParou }}</p>
              </div>
              <div v-else-if="detalhe.status === 'pedida' || detalhe.status === 'fazendo'" class="mb-4 rounded-md border border-primary/30 bg-primary/10 px-3 py-2 text-sm">
                O robô ainda está trabalhando nesta mudança. Quando ela estiver pronta, aparecem os botões de aprovar — se for de quem tem aprovação automática, ela já vai direto para a fila de publicar.
              </div>
              <div v-else-if="detalhe.status === 'pronta' && !detalhe.pode_aprovar" class="mb-4 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-800 dark:text-amber-300">
                Esta mudança está esperando aprovação, mas a aprovação não está liberada para você.
              </div>

              <section class="mb-5">
                <h3 class="mb-1 text-sm font-semibold">{{ detalhe.tipo === 'pergunta' ? 'A pergunta' : 'O que foi pedido' }}</h3>
                <p class="whitespace-pre-wrap break-words text-sm">{{ detalhe.descricao || '—' }}</p>
                <p v-if="detalhe.onde" class="mt-2 text-sm"><span class="text-muted-foreground">Onde:</span> {{ detalhe.onde }}</p>
              </section>

              <section v-if="detalhe.tipo === 'pergunta'" class="mb-5">
                <h3 class="mb-1 text-sm font-semibold">Resposta do robô <span class="font-normal text-muted-foreground">(foi direto para quem perguntou)</span></h3>
                <pre v-if="detalhe.resposta" class="max-h-[70vh] overflow-auto whitespace-pre-wrap break-words rounded-md border bg-muted/20 p-3 font-sans text-sm leading-6">{{ detalhe.resposta }}</pre>
                <p v-else class="text-sm text-muted-foreground">O robô ainda não respondeu.</p>
              </section>

              <section v-if="mostraResumo && detalhe.tipo !== 'pergunta'" class="mb-5">
                <h3 class="mb-1 text-sm font-semibold">O que o robô fez</h3>
                <p class="whitespace-pre-wrap break-words text-sm">{{ detalhe.resumo }}</p>
              </section>

              <section v-if="detalhe.arquivos?.length" class="mb-5">
                <h3 class="mb-1 text-sm font-semibold">
                  Arquivos alterados
                  <span class="font-normal text-muted-foreground">
                    ({{ detalhe.arquivos.length }} ·
                    <span class="text-emerald-700 dark:text-emerald-400">+{{ totais.mais }}</span>
                    <span class="text-red-700 dark:text-red-400">−{{ totais.menos }}</span>)
                  </span>
                </h3>
                <ul class="divide-y rounded-md border text-sm">
                  <li v-for="a in detalhe.arquivos" :key="a.caminho" class="flex items-center gap-3 px-3 py-1.5">
                    <span class="min-w-0 flex-1 break-all font-mono text-xs">{{ a.caminho }}</span>
                    <span class="whitespace-nowrap font-mono text-xs text-emerald-700 dark:text-emerald-400">+{{ a.mais }}</span>
                    <span class="whitespace-nowrap font-mono text-xs text-red-700 dark:text-red-400">−{{ a.menos }}</span>
                  </li>
                </ul>
              </section>

              <section v-if="detalhe.testes" class="mb-5">
                <h3 class="mb-1 flex items-center gap-2 text-sm font-semibold">
                  Testes
                  <button type="button" class="text-xs font-normal text-muted-foreground underline" @click="testesAbertos = !testesAbertos">
                    {{ testesAbertos ? 'esconder' : 'ver' }}
                  </button>
                </h3>
                <pre v-if="testesAbertos" class="max-h-80 overflow-auto whitespace-pre-wrap break-all rounded-md border bg-muted/30 p-3 font-mono text-[11px] leading-4">{{ detalhe.testes }}</pre>
              </section>

              <section v-if="detalhe.tipo !== 'pergunta'" class="mb-5">
                <h3 class="mb-1 text-sm font-semibold">Mudança no código</h3>
                <p v-if="!linhasDiff.length" class="text-sm text-muted-foreground">
                  {{ detalhe.status === 'pedida' || detalhe.status === 'fazendo' ? 'O robô ainda não terminou de mexer no código.' : 'Nenhuma mudança no código registrada.' }}
                </p>
                <template v-else>
                  <div v-if="temInvisiveis" class="mb-2 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm font-medium text-red-800 dark:text-red-300">
                    Esta mudança tem caracteres invisíveis (marcados como ⟦U+…⟧). Eles podem esconder código — não aprove; recuse e chame a equipe.
                  </div>
                  <pre class="max-h-[70vh] overflow-auto whitespace-pre-wrap break-all rounded-md border bg-muted/20 py-2 font-mono text-[12px] leading-5"><span v-for="(l, i) in linhasVisiveis" :key="i" class="block px-3" :class="l.cls">{{ l.texto || ' ' }}</span></pre>
                  <p v-if="linhasDiff.length > MAX_LINHAS" class="mt-1 text-xs text-muted-foreground">
                    <template v-if="!diffInteiro">
                      Mostrando {{ MAX_LINHAS.toLocaleString('pt-BR') }} de {{ linhasDiff.length.toLocaleString('pt-BR') }} linhas.
                      <button type="button" class="underline" @click="diffInteiro = true">ver tudo</button>
                    </template>
                    <template v-else>{{ linhasDiff.length.toLocaleString('pt-BR') }} linhas.</template>
                  </p>
                </template>
              </section>

              <p v-if="detalhe.branch || detalhe.commit" class="text-xs text-muted-foreground">
                Para a equipe técnica:
                <span v-if="detalhe.branch">ramo <span class="font-mono">{{ detalhe.branch }}</span></span>
                <span v-if="detalhe.branch && detalhe.commit"> · </span>
                <span v-if="detalhe.commit">commit <span class="font-mono">{{ detalhe.commit.slice(0, 10) }}</span></span>
              </p>
            </template>
          </div>

          <!-- Botões: só na "pronta" e para quem pode aprovar -->
          <div v-if="detalhe && podeDecidir" class="flex flex-wrap items-center gap-2 border-t bg-background px-5 py-3">
            <button
              type="button"
              class="inline-flex h-9 items-center gap-1.5 rounded-md bg-emerald-600 px-4 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
              :disabled="!!enviando"
              @click="confirmarAberto = true"
            >
              <Check class="size-4" /> Aprovar e publicar
            </button>
            <button
              type="button"
              class="inline-flex h-9 items-center gap-1.5 rounded-md border border-red-500/40 bg-background px-4 text-sm font-medium text-red-700 hover:bg-red-500/10 disabled:opacity-50 dark:text-red-400"
              :disabled="!!enviando"
              @click="abrirRecusa('recusar')"
            >
              <X class="size-4" /> Recusar
            </button>
            <span class="ml-auto text-xs text-muted-foreground">Confira a mudança no código antes de aprovar.</span>
          </div>
          <!-- Aprovada, publicação esperando: dá para desfazer a aprovação -->
          <div v-else-if="detalhe && podeCancelar" class="flex flex-wrap items-center gap-2 border-t bg-background px-5 py-3">
            <button
              type="button"
              class="inline-flex h-9 items-center gap-1.5 rounded-md border border-red-500/40 bg-background px-4 text-sm font-medium text-red-700 hover:bg-red-500/10 disabled:opacity-50 dark:text-red-400"
              :disabled="!!enviando"
              @click="abrirRecusa('cancelar')"
            >
              <X class="size-4" /> Cancelar aprovação
            </button>
            <span class="ml-auto text-xs text-muted-foreground">Aprovada, esperando para publicar. Cancelar impede o robô de publicar.</span>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Confirmar aprovação -->
    <Teleport to="body">
      <div v-if="confirmarAberto && detalhe" class="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 p-4" @click.self="!enviando && (confirmarAberto = false)">
        <div class="w-full max-w-md rounded-lg border bg-background p-5 shadow-xl">
          <h2 class="mb-2 text-lg font-semibold">Publicar a {{ detalhe.codigo }} no ar?</h2>
          <p class="mb-4 text-sm text-muted-foreground">O robô vai publicar esta mudança no DaVinci.</p>
          <div class="flex justify-end gap-2">
            <button type="button" class="h-9 rounded-md border bg-background px-4 text-sm hover:bg-muted disabled:opacity-50" :disabled="!!enviando" @click="confirmarAberto = false">
              Cancelar
            </button>
            <button
              type="button"
              class="inline-flex h-9 items-center gap-1.5 rounded-md bg-emerald-600 px-4 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
              :disabled="!!enviando"
              @click="aprovar"
            >
              <RefreshCw v-if="enviando === 'aprovar'" class="size-4 animate-spin" />
              Confirmar
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Recusar -->
    <Teleport to="body">
      <div v-if="recusaAberta && detalhe" class="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 p-4" @click.self="!enviando && (recusaAberta = false)">
        <div class="w-full max-w-md rounded-lg border bg-background p-5 shadow-xl">
          <h2 class="mb-2 text-lg font-semibold">{{ modoRecusa === 'cancelar' ? `Cancelar a aprovação da ${detalhe.codigo}` : `Recusar a ${detalhe.codigo}` }}</h2>
          <label for="motivo-recusa" class="mb-1 block text-sm">{{ modoRecusa === 'cancelar' ? 'Por que cancelar?' : 'Por que recusar?' }}</label>
          <textarea
            id="motivo-recusa"
            v-model="motivo"
            rows="4"
            maxlength="1000"
            class="w-full rounded-md border bg-background px-3 py-2 text-sm"
            placeholder="Ex.: não era isso que eu queria; o botão deveria ficar na tela de Pedidos."
          />
          <p class="mb-4 mt-1 text-xs text-muted-foreground">
            Obrigatório (pelo menos 3 letras). O motivo fica registrado na mudança.
          </p>
          <div class="flex justify-end gap-2">
            <button type="button" class="h-9 rounded-md border bg-background px-4 text-sm hover:bg-muted disabled:opacity-50" :disabled="!!enviando" @click="recusaAberta = false">
              Cancelar
            </button>
            <button
              type="button"
              class="inline-flex h-9 items-center gap-1.5 rounded-md bg-red-600 px-4 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50"
              :disabled="!!enviando || !motivoOk"
              @click="recusar"
            >
              <RefreshCw v-if="enviando === 'recusar'" class="size-4 animate-spin" />
              {{ modoRecusa === 'cancelar' ? 'Cancelar aprovação' : 'Recusar' }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>
