<script setup lang="ts">
/**
 * Marketing › Conferência Shopee (06/10/2026).
 *
 * O relatório que o conferencia.js montava à mão, agora no DaVinci: o robô do
 * Mac coleta loja por loja (terça = semana fechada; quinta = parcial, segunda
 * até ontem) e o servidor congela o relatório quando a última loja chega. Esta
 * aba LÊ esse relatório congelado (contrato §5) — as contas, os "—" e as cores
 * das variações saem de lib/conferencia.ts, que segue as mesmas regras do
 * Excel/CSV/MD/HTML e do Threema.
 *
 * Três decisões de tela:
 * 1. Abre no último relatório PRONTO (ou no ?execucao= do link do Threema). Uma
 *    coleta em andamento aparece num aviso em cima — abrir nela mostraria uma
 *    lista de lojas no lugar dos números da semana.
 * 2. Enquanto a execução coleta, a lista de lojas relê a cada 20 s, só com a
 *    aba visível e montada: ninguém precisa recarregar pra ver chegar.
 * 3. Conta sem dados continua na tabela, com o motivo (deslogada, Firefox,
 *    bloqueada…). Sumir com a linha faria o total do grupo parecer completo.
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  AlertTriangle, Ban, Bell, ChevronRight, FileCode, FileJson, FileSpreadsheet, FileText,
  Inbox, Loader2, Play, RefreshCw, RotateCcw,
} from 'lucide-vue-next'
import { apiErrMsg } from '~/lib/apiError'
import {
  ERROS_CONFERENCIA, METRICAS,
  celula, coletaTerminou, comparadoCom, contasTxt, dataHoraBr, ddmm, dinheiro, horaBr,
  linhaQuatroSemanas, nomeArquivo, nomeDoCabecalho, resumoCartao, rotuloExecucao, rotuloSemana,
  rotuloStatusColeta, rotuloStatusExecucao, tituloRelatorio, tomStatusColeta, valor, variacao, fmtValor, media3,
  type Coleta, type ContaConferencia, type Cor, type DetalheExecucao, type ExecucaoResumo, type Formato,
  type LinhaConta, type Metrica,
} from '~/lib/conferencia'

const { api } = useApi()
const toasts = useToasts()
const canEdit = useCan('marketing', 'edit')
// O cadastro de quem recebe no Threema (routers/informar.py) é só de admin.
const auth = useAuthStore()
const isAdmin = computed(() => auth.user?.role === 'admin')
const route = useRoute()
const router = useRouter()

const BASE = '/api/marketing/conferencia-shopee'
// Enquanto coleta, relê a cada 20 s (contrato da tela).
const INTERVALO_ACOMPANHAMENTO = 20_000
const ID_VALIDO = /^[0-9a-f-]{36}$/i

// Classes por extenso pro Tailwind enxergar (lib/ não é varrido).
const COR: Record<Cor, string> = {
  verde: 'text-emerald-600 dark:text-emerald-400',
  vermelho: 'text-red-600 dark:text-red-400',
  cinza: 'text-muted-foreground',
}
const PILL_TOM: Record<string, string> = {
  ok: 'pill-success', alerta: 'pill-warning', erro: 'pill-danger', andamento: 'pill-info', neutro: 'pill-muted',
}
const PILL_EXECUCAO: Record<string, string> = {
  coletando: 'pill-info', pronto: 'pill-success', cancelado: 'pill-muted',
}
const ROTULO_GRUPO: Record<string, string> = { mala: 'Mala', celular: 'Celular', eletro: 'Eletro' }
const LEGENDA_GRUPO: Record<string, string> = {
  mala: '',
  celular: 'total da conta menos o eletro · saldo de Ads da conta inteira',
  eletro: 'só os produtos de eletro · saldo de Ads fica na linha de Celular',
}
const VAZIO_GRUPO: Record<string, string> = {
  mala: 'Nenhuma conta de Mala nesta conferência.',
  celular: 'Nenhuma conta de Celular nesta conferência.',
  eletro: 'Nenhuma conta vendeu eletro (venda, afiliado ou Ads) nessas 4 semanas.',
}
const CATEGORIA_SHOPEE: Record<number, string> = {
  100010: 'Eletrodomésticos',
  100636: 'Casa e Decoração',
  100013: 'Celulares',
}
const FORMATOS: { fmt: Formato; rotulo: string; icone: any; dica: string }[] = [
  { fmt: 'xlsx', rotulo: 'Excel', icone: FileSpreadsheet, dica: 'Planilha com as 4 semanas' },
  { fmt: 'csv', rotulo: 'CSV', icone: FileText, dica: 'Separado por ";" com vírgula decimal (abre no Excel)' },
  { fmt: 'md', rotulo: 'MD', icone: FileText, dica: 'Texto (Markdown)' },
  { fmt: 'json', rotulo: 'JSON', icone: FileJson, dica: 'Dados brutos do relatório' },
  { fmt: 'html', rotulo: 'HTML', icone: FileCode, dica: 'Abre o relatório numa aba nova (dá pra imprimir)' },
]

// ---------- lista de execuções e a escolhida

const execucoes = ref<ExecucaoResumo[]>([])
const listaCarregada = ref(false)
const carregandoLista = ref(false)
const erro = ref<string | null>(null)
const selecionada = ref<string | null>(null)
const detalhe = ref<DetalheExecucao | null>(null)
const carregandoDetalhe = ref(false)
const erroDetalhe = ref<string | null>(null)
const recarregando = ref(false)

const exec = computed(() => detalhe.value?.execucao ?? null)
const rel = computed(() => detalhe.value?.relatorio ?? null)
const coletas = computed<Coleta[]>(() => detalhe.value?.coletas ?? [])
// Execução aberta por link e mais velha que as 30 da lista: entra no seletor também.
const opcoes = computed<ExecucaoResumo[]>(() => {
  const e = exec.value
  if (!e || execucoes.value.some((x) => x.id === e.id)) return execucoes.value
  return [e, ...execucoes.value]
})
const emAndamento = computed(() => execucoes.value.find((e) => e.status === 'coletando') ?? null)

async function carregarLista(): Promise<ExecucaoResumo[] | null> {
  try {
    const r = await api<ExecucaoResumo[]>(`${BASE}/execucoes?limite=30`)
    execucoes.value = Array.isArray(r) ? r : []
    erro.value = null
    return execucoes.value
  } catch (e: any) {
    erro.value = apiErrMsg(e, ERROS_CONFERENCIA)
    return null
  }
}

/** Link do Threema (?execucao=) manda; senão o último pronto; senão o mais novo. */
function escolhaPadrao(lista: ExecucaoResumo[], pedida: string): string | null {
  if (ID_VALIDO.test(pedida)) return pedida
  return (lista.find((e) => e.status === 'pronto') ?? lista[0])?.id ?? null
}

// Seleção trocada duas vezes seguidas: a resposta velha pode chegar depois da
// nova. Só a última chamada escreve na tela.
let geracao = 0
async function carregarDetalhe(id: string, silencioso = false) {
  const minha = ++geracao
  if (!silencioso) {
    carregandoDetalhe.value = true
    erroDetalhe.value = null
  }
  try {
    const r = await api<DetalheExecucao>(`${BASE}/execucoes/${encodeURIComponent(id)}`)
    if (minha !== geracao) return
    const antes = detalhe.value?.execucao
    detalhe.value = {
      execucao: r.execucao,
      coletas: Array.isArray(r.coletas) ? r.coletas : [],
      relatorio: r.relatorio ?? null,
    }
    erroDetalhe.value = null
    // Acompanhando a coleta e ela terminou: a lista (seletor e aviso) muda junto.
    if (antes?.id === id && antes.status === 'coletando' && r.execucao?.status !== 'coletando') {
      void carregarLista()
      if (r.execucao?.status === 'pronto') toasts.success('Conferência pronta', 'O relatório da semana já está na tela.')
    }
  } catch (e: any) {
    // A releitura do acompanhamento falhar não apaga a tela: a próxima resolve.
    if (minha !== geracao || silencioso) return
    erroDetalhe.value = apiErrMsg(e, ERROS_CONFERENCIA)
    detalhe.value = null
  } finally {
    if (minha === geracao) {
      carregandoDetalhe.value = false
      agendarAcompanhamento()
    }
  }
}

watch(selecionada, (id) => {
  if (id) void carregarDetalhe(id)
  else detalhe.value = null
})

/** Escolha da pessoa (seletor, "ver andamento", execução nova): vira link. */
function escolher(id: string) {
  selecionada.value = id
  void router.replace({ query: { ...route.query, execucao: id } })
}

async function iniciar() {
  carregandoLista.value = true
  const lista = await carregarLista()
  carregandoLista.value = false
  listaCarregada.value = true
  if (!lista) return
  selecionada.value = escolhaPadrao(lista, String(route.query.execucao || ''))
}

async function recarregar() {
  if (recarregando.value) return
  recarregando.value = true
  try {
    const lista = await carregarLista()
    listaCarregada.value = true
    if (selecionada.value) await carregarDetalhe(selecionada.value)
    else if (lista) selecionada.value = escolhaPadrao(lista, '')
  } finally {
    recarregando.value = false
  }
}

// ---------- acompanhamento enquanto coleta (20 s, só com a aba visível)

let timer: number | undefined
let desmontado = false
function visivel(): boolean {
  return typeof document === 'undefined' || document.visibilityState !== 'hidden'
}
function agendarAcompanhamento() {
  window.clearTimeout(timer)
  timer = undefined
  const e = exec.value
  if (desmontado || !visivel() || !e || e.status !== 'coletando' || e.id !== selecionada.value) return
  timer = window.setTimeout(() => {
    timer = undefined
    if (!desmontado && selecionada.value) void carregarDetalhe(selecionada.value, true)
  }, INTERVALO_ACOMPANHAMENTO)
}
function aoMudarVisibilidade() {
  if (!visivel()) {
    window.clearTimeout(timer)
    timer = undefined
    return
  }
  // Voltou pra aba: lê na hora (o que mudou enquanto ninguém olhava) e reagenda.
  if (exec.value?.status === 'coletando' && selecionada.value) void carregarDetalhe(selecionada.value, true)
}

onMounted(() => {
  if (typeof document !== 'undefined') document.addEventListener('visibilitychange', aoMudarVisibilidade)
  void iniciar()
})
onBeforeUnmount(() => {
  desmontado = true
  window.clearTimeout(timer)
  if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', aoMudarVisibilidade)
})

const concluidas = computed(() => coletas.value.filter((c) => coletaTerminou(c.status)).length)
const pctConcluidas = computed(() =>
  coletas.value.length ? Math.round((concluidas.value / coletas.value.length) * 100) : 0,
)
const prazosTxt = computed(() => {
  const e = exec.value
  if (!e) return ''
  const frases: string[] = []
  if (e.esperar_afiliados_ate && e.afiliados_ate) {
    frases.push(`Espera os afiliados de ${ddmm(e.afiliados_ate)} até ${horaBr(e.esperar_afiliados_ate)}.`)
  }
  if (e.corte && e.prazo) {
    frases.push(`Loja nova só começa até ${horaBr(e.corte)}; o que faltar às ${horaBr(e.prazo)} fica de fora.`)
  } else if (e.corte) {
    frases.push(`Loja nova só começa até ${horaBr(e.corte)}.`)
  }
  return frases.join(' ')
})
function detalheColeta(c: Coleta): string {
  const partes: string[] = []
  if (c.erro) partes.push(c.erro)
  if (c.tentativas > 1) partes.push(`${c.tentativas}ª tentativa`)
  if (c.adiamentos > 0) {
    // adiamentos_perfil: só os de perfil em uso; o resto foi espera dos afiliados.
    const perfil = c.adiamentos_perfil ?? 0
    const motivo = perfil >= c.adiamentos ? 'perfil em uso'
      : perfil > 0 ? `${perfil}× perfil em uso, ${c.adiamentos - perfil}× esperando os afiliados`
        : 'esperando os afiliados'
    partes.push(`adiada ${c.adiamentos}× (${motivo})`)
  }
  if (c.concluido_em && coletaTerminou(c.status)) partes.push(`às ${horaBr(c.concluido_em)}`)
  return partes.join(' · ')
}

// ---------- ações: gerar, recalcular, cancelar

const tipoNovo = ref<'semanal' | 'parcial'>('semanal')
// Recalcular (pronto) e Cancelar (coletando): só quem edita, só quando cabe.
const podeAgir = computed(() => canEdit.value && (exec.value?.status === 'pronto' || exec.value?.status === 'coletando'))
const gerando = ref(false)
const recalculando = ref(false)
const cancelando = ref(false)

// Só o 409 de "já tem uma coletando": o 409 de nenhuma loja ativa
// (conferencia_sem_contas) tem outra causa e cai na frase dele.
function erroJaColetando(e: any): boolean {
  return e?.data?.detail?.code === 'conferencia_em_andamento'
}

async function gerar() {
  if (gerando.value) return
  const qual = tipoNovo.value === 'parcial' ? 'parcial (segunda até ontem)' : 'da semana fechada (segunda a domingo)'
  if (!window.confirm(
    `Gerar agora a conferência ${qual}?\n\nO robô do Mac abre o perfil de cada loja no AdsPower, uma de cada vez. `
    + 'Numa segunda-feira a parcial vira semanal (ainda não tem dia na semana).',
  )) return
  gerando.value = true
  try {
    const r = await api<any>(`${BASE}/execucoes`, { method: 'POST', body: { tipo: tipoNovo.value } })
    toasts.success('Conferência iniciada', 'O robô do Mac vai passar loja por loja. Esta tela acompanha sozinha.')
    const lista = await carregarLista()
    const novo = r?.id ?? r?.execucao?.id ?? lista?.find((x) => x.status === 'coletando')?.id
    if (novo) escolher(String(novo))
  } catch (e: any) {
    if (erroJaColetando(e)) {
      toasts.warning('Já tem uma conferência coletando', 'Espere ela terminar ou cancele antes de gerar outra.')
      const lista = await carregarLista()
      const andando = lista?.find((x) => x.status === 'coletando')
      if (andando) escolher(andando.id)
    } else {
      toasts.error('Não consegui gerar a conferência', apiErrMsg(e, ERROS_CONFERENCIA))
    }
  } finally {
    gerando.value = false
  }
}

async function recalcular() {
  const e = exec.value
  if (!e || recalculando.value) return
  recalculando.value = true
  try {
    await api(`${BASE}/execucoes/${encodeURIComponent(e.id)}/recalcular`, { method: 'POST' })
    await carregarDetalhe(e.id)
    toasts.success('Relatório recalculado', 'Com os dados já coletados — nenhuma loja foi aberta de novo.')
  } catch (err: any) {
    toasts.error('Não consegui recalcular', apiErrMsg(err, ERROS_CONFERENCIA))
  } finally {
    recalculando.value = false
  }
}

async function cancelar() {
  const e = exec.value
  if (!e || cancelando.value) return
  if (!window.confirm('Cancelar esta conferência? As lojas que ainda não foram coletadas ficam de fora e não sai relatório.')) return
  cancelando.value = true
  try {
    await api(`${BASE}/execucoes/${encodeURIComponent(e.id)}/cancelar`, { method: 'POST' })
    toasts.success('Conferência cancelada')
    await Promise.all([carregarLista(), carregarDetalhe(e.id)])
  } catch (err: any) {
    toasts.error('Não consegui cancelar', apiErrMsg(err, ERROS_CONFERENCIA))
  } finally {
    cancelando.value = false
  }
}

// ---------- arquivos

const baixando = ref<Formato | null>(null)

// Com responseType 'blob', o erro da API também chega como Blob: lê o JSON dele.
async function lerErroDeArquivo(e: any): Promise<any> {
  if (typeof Blob === 'undefined' || !(e?.data instanceof Blob)) return e
  let data: any = null
  try {
    data = JSON.parse(await e.data.text())
  } catch {
    // corpo que não é JSON (proxy fora do ar, por exemplo): fica a mensagem padrão
  }
  return { data, message: e?.message, status: e?.status, statusCode: e?.statusCode }
}

function salvarBlob(arquivo: Blob, nome: string) {
  const href = URL.createObjectURL(arquivo)
  const a = document.createElement('a')
  a.href = href
  a.download = nome
  document.body.appendChild(a)
  a.click()
  a.remove()
  // Revogar na hora corta o download em alguns navegadores.
  window.setTimeout(() => URL.revokeObjectURL(href), 60_000)
}

async function baixar(fmt: Formato) {
  const d = detalhe.value
  if (!d?.relatorio || baixando.value) return
  const rotulo = FORMATOS.find((f) => f.fmt === fmt)?.rotulo ?? fmt
  // O HTML abre numa aba nova, aberta JÁ no clique: depois do await o
  // navegador trata como pop-up e bloqueia.
  const aba = fmt === 'html' ? window.open('', '_blank') : null
  let nome = nomeArquivo(d.relatorio.semanas, fmt)
  baixando.value = fmt
  try {
    const blob = await api<Blob>(`${BASE}/execucoes/${encodeURIComponent(d.execucao.id)}/arquivo/${fmt}`, {
      responseType: 'blob',
      onResponse({ response }: { response: Response }) {
        if (response.ok) nome = nomeDoCabecalho(response.headers.get('Content-Disposition')) || nome
      },
    })
    if (aba && !aba.closed) {
      const href = URL.createObjectURL(new Blob([blob], { type: 'text/html;charset=utf-8' }))
      aba.location.href = href
      window.setTimeout(() => URL.revokeObjectURL(href), 60_000)
    } else {
      // Pop-up bloqueado: o HTML vem como arquivo, igual aos outros.
      salvarBlob(blob, nome)
    }
  } catch (e: any) {
    aba?.close()
    toasts.error(`Não deu para baixar o ${rotulo}`, apiErrMsg(await lerErroDeArquivo(e), ERROS_CONFERENCIA))
  } finally {
    baixando.value = null
  }
}

// ---------- relatório

const metricas = computed<Metrica[]>(() => (rel.value?.metricas?.length ? rel.value.metricas : METRICAS))

function semDados(l: LinhaConta): boolean {
  return l.status !== 'ok' && l.status !== 'parcial'
}

const cartoes = computed(() => {
  const r = rel.value
  if (!r) return []
  const blocos = [
    ...r.grupos.map((g) => ({ chave: g.chave as string, rotulo: g.rotulo, total: g.total })),
    { chave: 'geral', rotulo: 'Geral', total: r.geral },
  ]
  return blocos.map((b) => {
    const c = resumoCartao(b.total)
    return {
      chave: b.chave,
      rotulo: b.rotulo,
      contas: b.total?.contas ?? 0,
      semDados: b.total?.sem_dados ?? 0,
      linhas: [
        { rotulo: 'Vendas', ...c.vendas },
        { rotulo: 'Investimento', ...c.investimento },
        { rotulo: '% s/ vendas', ...c.pct },
      ],
    }
  })
})

const tabelas = computed(() => (rel.value?.grupos ?? []).map((g) => ({
  chave: g.chave,
  rotulo: g.rotulo,
  total: g.total,
  linhas: g.linhas.map((l) => ({ ...l, celulas: metricas.value.map((m) => celula(l.semanas, m)) })),
  celulasTotal: metricas.value.map((m) => celula(g.total?.semanas, m)),
})))

const quatroSemanas = computed(() => {
  const r = rel.value
  if (!r) return []
  const blocos = [
    ...r.grupos.map((g) => ({ chave: g.chave as string, rotulo: g.rotulo, total: g.total })),
    { chave: 'geral', rotulo: 'Geral', total: r.geral },
  ]
  return blocos.map((b) => ({
    chave: b.chave,
    rotulo: b.rotulo,
    contas: b.total?.contas ?? 0,
    linhas: metricas.value.map((m) => ({ chave: m.chave, rotulo: m.rotulo, ...linhaQuatroSemanas(b.total?.semanas, m) })),
  }))
})

const porConta = computed(() => (rel.value?.grupos ?? []).map((g) => ({
  chave: g.chave,
  rotulo: g.rotulo,
  linhas: g.linhas.map((l) => {
    const atual = valor(l.semanas?.[0], 'vendas')
    return {
      chave: l.conta_id ?? l.conta,
      conta: l.conta,
      vendas: [0, 1, 2, 3].map((i) => fmtValor(valor(l.semanas?.[i], 'vendas'), 'dinheiro')),
      vsAnterior: variacao(atual, valor(l.semanas?.[1], 'vendas'), 'dinheiro', 'sobe', 'vendas'),
      vsMedia: variacao(atual, media3(l.semanas, 'vendas'), 'dinheiro', 'sobe', 'vendas'),
      pcts: [0, 1, 2, 3].map((i) => fmtValor(valor(l.semanas?.[i], 'pct'), 'percentual')),
    }
  }),
})))
// Só pra o v-for do cabeçalho: S1..S4 com as datas.
const semanasCab = computed(() => (rel.value?.semanas ?? []).slice(0, 4).map((s, i) => ({ i, nome: `S${i + 1}`, datas: rotuloSemana(s) })))

const afiliadosIncompletosTxt = computed(() =>
  (rel.value?.afiliados_incompletos ?? []).map((a) => `${a.conta} (até ${ddmm(a.ate)})`).join(', '),
)
const naoAtribuidoTxt = computed(() =>
  (rel.value?.nao_atribuido_ads ?? []).map((n) => `${n.conta} ${dinheiro(n.gasto)}`).join(' · '),
)
function categoriaTxt(c: number | null | undefined): string {
  if (c === null || c === undefined) return '—'
  return CATEGORIA_SHOPEE[c] ? `${CATEGORIA_SHOPEE[c]} (${c})` : String(c)
}

// ---------- contas (quem entra, grupo e nome)

const contasAberto = ref(false)
const contas = ref<ContaConferencia[] | null>(null)
const contasCarregando = ref(false)
const contasErro = ref<string | null>(null)
const salvandoConta = ref<string | null>(null)
// Rascunho do nome de cada conta: só vai pro servidor no Enter ou ao sair do campo.
const nomes = ref<Record<string, string>>({})

const contasOrdenadas = computed(() => [...(contas.value ?? [])].sort((a, b) =>
  (a.grupo === b.grupo ? 0 : a.grupo === 'mala' ? -1 : 1)
  || a.nome.localeCompare(b.nome, 'pt-BR', { sensitivity: 'base' }),
))

async function carregarContas() {
  contasCarregando.value = true
  contasErro.value = null
  try {
    const r = await api<ContaConferencia[]>(`${BASE}/contas`)
    contas.value = Array.isArray(r) ? r : []
    nomes.value = Object.fromEntries(contas.value.map((c) => [c.id, c.nome]))
  } catch (e: any) {
    contasErro.value = apiErrMsg(e, ERROS_CONFERENCIA)
  } finally {
    contasCarregando.value = false
  }
}
watch(contasAberto, (aberto) => {
  if (aberto && !contas.value && !contasCarregando.value) void carregarContas()
})

async function salvarConta(c: ContaConferencia, campos: Partial<Pick<ContaConferencia, 'nome' | 'grupo' | 'ativo'>>) {
  if (salvandoConta.value) return
  salvandoConta.value = c.id
  try {
    const r = await api<ContaConferencia>(`${BASE}/contas/${encodeURIComponent(c.id)}`, { method: 'PUT', body: campos })
    const nova: ContaConferencia = r && typeof r === 'object' && 'id' in r ? r : { ...c, ...campos }
    contas.value = (contas.value ?? []).map((x) => (x.id === c.id ? nova : x))
    nomes.value = { ...nomes.value, [c.id]: nova.nome }
    toasts.success('Conta salva', `${nova.nome}: vale a partir da próxima conferência.`)
  } catch (e: any) {
    nomes.value = { ...nomes.value, [c.id]: c.nome }
    toasts.error('Não consegui salvar a conta', apiErrMsg(e, ERROS_CONFERENCIA))
  } finally {
    salvandoConta.value = null
  }
}
function salvarNome(c: ContaConferencia) {
  const n = (nomes.value[c.id] ?? '').trim()
  if (!n) {
    nomes.value = { ...nomes.value, [c.id]: c.nome }
    return
  }
  if (n !== c.nome) void salvarConta(c, { nome: n })
}

// ---------- Threema

const informarAberto = ref(false)
</script>

<template>
  <div class="conferencia min-w-0 space-y-5">
    <!-- barra de cima: qual relatório, situação, gerar e Threema -->
    <div class="flex flex-wrap items-center gap-2">
      <label class="inline-flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
        Relatório
        <select
          v-model="selecionada"
          class="h-8 min-w-0 max-w-[min(24rem,70vw)] rounded-md border bg-background px-2 text-sm text-foreground"
          aria-label="Escolher o relatório"
          :disabled="!opcoes.length"
          @change="(e) => escolher((e.target as HTMLSelectElement).value)"
        >
          <option v-if="!opcoes.length" :value="null">nenhum ainda</option>
          <option v-for="e in opcoes" :key="e.id" :value="e.id">{{ rotuloExecucao(e) }}</option>
        </select>
      </label>
      <span v-if="exec" :class="PILL_EXECUCAO[exec.status] ?? 'pill-muted'">{{ rotuloStatusExecucao(exec.status) }}</span>
      <button
        class="btn btn-sm btn-ghost px-1.5"
        title="recarregar" aria-label="recarregar"
        :disabled="recarregando || carregandoLista"
        @click="recarregar"
      >
        <RefreshCw class="size-3.5" :class="(recarregando || carregandoDetalhe) && 'animate-spin'" />
      </button>
      <div class="ml-auto flex flex-wrap items-center gap-2">
        <template v-if="canEdit">
          <label class="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
            Tipo
            <select
              v-model="tipoNovo"
              class="h-8 rounded-md border bg-background px-2 text-sm text-foreground"
              aria-label="Tipo da conferência a gerar"
            >
              <option value="semanal">semana fechada (seg–dom)</option>
              <option value="parcial">parcial (seg até ontem)</option>
            </select>
          </label>
          <Button size="sm" class="h-8" :disabled="gerando" @click="gerar">
            <Loader2 v-if="gerando" class="mr-1 size-3.5 animate-spin" />
            <Play v-else class="mr-1 size-3.5" />
            Gerar agora
          </Button>
        </template>
        <Button v-if="isAdmin" size="sm" variant="outline" class="h-8" @click="informarAberto = true">
          <Bell class="mr-1 size-3.5" /> Quem recebe no Threema
        </Button>
      </div>
    </div>

    <!-- aviso: tem coleta andando e a tela está em outro relatório -->
    <div
      v-if="emAndamento && emAndamento.id !== selecionada"
      class="flex flex-wrap items-center gap-2 rounded-md border border-primary/30 bg-primary/5 px-3 py-2 text-xs"
    >
      <Loader2 class="size-3.5 shrink-0 animate-spin text-primary" />
      <span>Tem uma conferência coletando agora (começou {{ dataHoraBr(emAndamento.criado_em) }}).</span>
      <button class="font-medium text-primary underline-offset-2 hover:underline" @click="escolher(emAndamento.id)">
        Ver andamento
      </button>
    </div>

    <!-- arquivos e ações do relatório escolhido -->
    <div v-if="exec && (rel || podeAgir)" class="flex flex-wrap items-center gap-2">
      <template v-if="rel">
        <span class="text-xs text-muted-foreground">Baixar:</span>
        <Button
          v-for="f in FORMATOS" :key="f.fmt"
          size="sm" variant="outline" class="h-8"
          :title="f.dica" :disabled="!!baixando"
          @click="baixar(f.fmt)"
        >
          <Loader2 v-if="baixando === f.fmt" class="mr-1 size-3.5 animate-spin" />
          <component :is="f.icone" v-else class="mr-1 size-3.5" />
          {{ f.rotulo }}
        </Button>
      </template>
      <div v-if="podeAgir" class="ml-auto flex items-center gap-2">
        <Button
          v-if="exec.status === 'pronto'"
          size="sm" variant="outline" class="h-8"
          title="Refaz as contas com os dados já coletados (não abre loja nenhuma)"
          :disabled="recalculando" @click="recalcular"
        >
          <Loader2 v-if="recalculando" class="mr-1 size-3.5 animate-spin" />
          <RotateCcw v-else class="mr-1 size-3.5" />
          Recalcular
        </Button>
        <Button
          v-if="exec.status === 'coletando'"
          size="sm" variant="outline" class="h-8 text-red-600 dark:text-red-400"
          :disabled="cancelando" @click="cancelar"
        >
          <Loader2 v-if="cancelando" class="mr-1 size-3.5 animate-spin" />
          <Ban v-else class="mr-1 size-3.5" />
          Cancelar
        </Button>
      </div>
    </div>

    <!-- estados: primeira carga, erro, nada ainda -->
    <div
      v-if="(carregandoLista && !listaCarregada) || (carregandoDetalhe && !detalhe)"
      class="space-y-3" aria-busy="true"
    >
      <div class="h-6 w-80 max-w-full animate-pulse rounded-md bg-muted/40" />
      <div class="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <div v-for="i in 4" :key="i" class="h-24 animate-pulse rounded-xl bg-muted/40" />
      </div>
      <div class="space-y-2">
        <div v-for="i in 4" :key="i" class="h-12 animate-pulse rounded-md bg-muted/40" />
      </div>
    </div>

    <div
      v-else-if="(erro && !execucoes.length) || (erroDetalhe && !detalhe)"
      class="space-y-2 rounded-md border border-amber-500/30 bg-amber-500/10 p-4 text-sm text-amber-700 dark:text-amber-400"
    >
      <p class="flex items-center gap-1.5">
        <AlertTriangle class="size-4 shrink-0" /> Não consegui carregar a conferência agora.
      </p>
      <div class="flex flex-wrap items-center gap-3">
        <button class="btn btn-sm" @click="recarregar">Tentar de novo</button>
        <span class="break-all text-[11px] opacity-80">{{ erroDetalhe || erro }}</span>
      </div>
    </div>

    <div
      v-else-if="listaCarregada && !opcoes.length"
      class="space-y-2 rounded-md border p-6 text-center text-sm text-muted-foreground"
    >
      <Inbox class="mx-auto size-6" />
      <p class="font-medium text-foreground">Nenhum relatório ainda</p>
      <p>
        A conferência roda terça (semana fechada) e quinta (parcial) às 13:30, quando a agenda está ligada.
        <template v-if="canEdit">Dá para gerar uma agora pelo botão <b>Gerar agora</b>.</template>
      </p>
    </div>

    <div
      v-else-if="exec"
      class="space-y-6 transition-opacity"
      :class="carregandoDetalhe && 'pointer-events-none opacity-60'"
    >
      <!-- andamento da coleta (ou as lojas de uma execução cancelada) -->
      <section
        v-if="exec.status === 'coletando' || (!rel && coletas.length)"
        class="space-y-3 rounded-xl border bg-card p-4"
      >
        <div class="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <h3 class="text-sm font-semibold">
            {{ exec.status === 'coletando' ? 'Coletando as lojas' : 'Lojas desta conferência' }}
          </h3>
          <span class="text-xs text-muted-foreground">
            {{ concluidas }} de {{ coletas.length }} concluídas · {{ tituloRelatorio(exec.semanas) }}
          </span>
          <span v-if="exec.status === 'coletando'" class="text-xs text-muted-foreground">· atualiza sozinho a cada 20 s</span>
        </div>
        <p v-if="exec.status === 'coletando'" class="text-xs text-muted-foreground">
          O robô do Mac abre uma loja por vez. {{ prazosTxt }}
        </p>
        <p v-else-if="exec.status === 'cancelado'" class="text-xs text-muted-foreground">
          Conferência cancelada — não saiu relatório.
        </p>
        <div class="h-1.5 overflow-hidden rounded-full bg-muted">
          <div class="h-full rounded-full bg-primary transition-all" :style="{ width: `${pctConcluidas}%` }" />
        </div>
        <div class="table-card overflow-x-auto">
          <table class="w-full min-w-[560px] text-xs">
            <thead>
              <tr>
                <th>Loja</th>
                <th>Grupo</th>
                <th>Situação</th>
                <th>Detalhe</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="!coletas.length">
                <td colspan="4" class="py-4 text-center text-muted-foreground">Nenhuma loja nesta conferência.</td>
              </tr>
              <tr v-for="c in coletas" :key="c.id">
                <td class="font-medium">{{ c.nome }}</td>
                <td>{{ ROTULO_GRUPO[c.grupo] ?? c.grupo }}</td>
                <td>
                  <span :class="PILL_TOM[tomStatusColeta(c.status)]">
                    <Loader2 v-if="c.status === 'coletando'" class="size-3 animate-spin" />
                    {{ rotuloStatusColeta(c.status) }}
                  </span>
                </td>
                <td class="max-w-[24rem] text-muted-foreground">
                  <span class="line-clamp-2 break-words" :title="detalheColeta(c)">{{ detalheColeta(c) || '—' }}</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <div
        v-else-if="!rel"
        class="rounded-md border p-6 text-center text-sm text-muted-foreground"
      >
        Esta conferência terminou sem relatório.
        <template v-if="canEdit && exec.status === 'pronto'"> Tente <b>Recalcular</b>.</template>
      </div>

      <!-- ═══════════════════════ RELATÓRIO ═══════════════════════ -->
      <div v-if="rel" class="space-y-6">
        <header class="space-y-1">
          <h2 class="flex flex-wrap items-center gap-2 text-lg font-semibold">
            {{ tituloRelatorio(rel.semanas) }}
            <span :class="rel.tipo === 'parcial' ? 'pill-warning' : 'pill-muted'">
              {{ rel.tipo === 'parcial' ? 'semana parcial' : 'semana fechada' }}
            </span>
          </h2>
          <p class="text-xs text-muted-foreground">
            {{ comparadoCom(rel.semanas) }}<template v-if="rel.gerado_em"> · gerado em {{ dataHoraBr(rel.gerado_em) }}</template>
            <template v-if="rel.origem === 'manual'"> · gerado pelo botão</template>
          </p>
        </header>

        <!-- cartões: Mala · Celular · Eletro · Geral -->
        <div class="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <div v-for="c in cartoes" :key="c.chave" class="min-w-0 space-y-2 rounded-xl border bg-card p-4">
            <div class="flex items-baseline justify-between gap-2">
              <span class="text-sm font-semibold">{{ c.rotulo }}</span>
              <span class="text-[11px] text-muted-foreground">
                {{ contasTxt(c.contas) }}<template v-if="c.semDados"> · {{ c.semDados }} sem dados</template>
              </span>
            </div>
            <dl class="space-y-1.5">
              <div v-for="l in c.linhas" :key="l.rotulo">
                <div class="flex items-baseline justify-between gap-2">
                  <dt class="text-xs text-muted-foreground">{{ l.rotulo }}</dt>
                  <dd class="text-base font-semibold tabular-nums">{{ l.valor }}</dd>
                </div>
                <div class="flex justify-end gap-1.5 text-[11px] tabular-nums">
                  <span class="text-muted-foreground">ant. {{ l.anterior }}</span>
                  <span :class="COR[l.variacao.cor]">{{ l.variacao.texto }}</span>
                </div>
              </div>
            </dl>
          </div>
        </div>

        <!-- uma tabela por grupo -->
        <section v-for="g in tabelas" :key="g.chave" class="space-y-2">
          <div class="flex flex-wrap items-baseline gap-x-2">
            <h3 class="text-sm font-semibold">{{ g.rotulo }}</h3>
            <span v-if="LEGENDA_GRUPO[g.chave]" class="text-xs text-muted-foreground">({{ LEGENDA_GRUPO[g.chave] }})</span>
          </div>
          <div class="table-card overflow-x-auto">
            <table class="w-full min-w-[1080px] text-xs">
              <thead>
                <tr>
                  <th>Conta</th>
                  <th v-for="m in metricas" :key="m.chave" class="whitespace-nowrap text-right">{{ m.rotulo }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-if="!g.linhas.length">
                  <td :colspan="metricas.length + 1" class="py-4 text-center text-muted-foreground">{{ VAZIO_GRUPO[g.chave] }}</td>
                </tr>
                <tr v-for="l in g.linhas" :key="l.conta_id ?? l.conta" class="align-top">
                  <td class="min-w-[9rem]">
                    <div class="font-medium">{{ l.conta }}</div>
                    <div v-if="l.usuario" class="text-[11px] text-muted-foreground">{{ l.usuario }}</div>
                    <div v-if="semDados(l) || l.status === 'parcial'" class="mt-0.5">
                      <span :class="PILL_TOM[tomStatusColeta(l.status)]" :title="l.erro || ''">
                        {{ semDados(l) ? `sem dados: ${rotuloStatusColeta(l.status)}` : rotuloStatusColeta(l.status) }}
                      </span>
                    </div>
                    <div
                      v-if="semDados(l) && l.erro"
                      class="max-w-[14rem] truncate text-[11px] text-muted-foreground" :title="l.erro"
                    >
                      {{ l.erro }}
                    </div>
                    <div v-for="a in l.avisos ?? []" :key="a" class="text-[11px] text-amber-700 dark:text-amber-400">⚠︎ {{ a }}</div>
                  </td>
                  <td v-for="(c, i) in l.celulas" :key="i" class="whitespace-nowrap text-right tabular-nums">
                    <div>{{ c.valor }}</div>
                    <div v-if="!c.vazia" class="text-[10px] leading-4">
                      <span class="text-muted-foreground">ant. {{ c.anterior }}</span>
                      <span class="ml-1" :class="COR[c.variacao.cor]">{{ c.variacao.texto }}</span>
                    </div>
                  </td>
                </tr>
                <tr v-if="g.linhas.length" class="bg-muted/40 font-semibold align-top">
                  <td>
                    Total {{ g.rotulo }} ({{ contasTxt(g.total?.contas) }})
                    <div v-if="g.total?.sem_dados" class="text-[11px] font-normal text-muted-foreground">
                      {{ g.total.sem_dados }} sem dados
                    </div>
                  </td>
                  <td v-for="(c, i) in g.celulasTotal" :key="i" class="whitespace-nowrap text-right tabular-nums">
                    <div>{{ c.valor }}</div>
                    <div v-if="!c.vazia" class="text-[10px] font-normal leading-4">
                      <span class="text-muted-foreground">ant. {{ c.anterior }}</span>
                      <span class="ml-1" :class="COR[c.variacao.cor]">{{ c.variacao.texto }}</span>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <!-- últimas 4 semanas -->
        <section class="space-y-3">
          <h3 class="text-sm font-semibold">Últimas 4 semanas</h3>
          <div class="grid grid-cols-1 gap-4 2xl:grid-cols-2">
            <div v-for="b in quatroSemanas" :key="b.chave" class="min-w-0 space-y-1.5">
              <h4 class="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {{ b.rotulo }} <span class="font-normal normal-case">({{ contasTxt(b.contas) }})</span>
              </h4>
              <div class="table-card overflow-x-auto">
                <table class="w-full min-w-[760px] text-xs">
                  <thead>
                    <tr>
                      <th>Métrica</th>
                      <th v-for="s in semanasCab" :key="s.i" class="whitespace-nowrap text-right">
                        {{ s.nome }} <span class="font-normal">{{ s.datas }}</span>
                      </th>
                      <th class="whitespace-nowrap text-right">vs semana anterior</th>
                      <th class="whitespace-nowrap text-right">vs média 3 sem.</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="l in b.linhas" :key="l.chave">
                      <td class="whitespace-nowrap font-medium">{{ l.rotulo }}</td>
                      <td
                        v-for="(v, i) in l.valores" :key="i"
                        class="whitespace-nowrap text-right tabular-nums" :class="i === 0 && 'font-semibold'"
                      >
                        {{ v }}
                      </td>
                      <td class="whitespace-nowrap text-right tabular-nums" :class="COR[l.vsAnterior.cor]">{{ l.vsAnterior.texto }}</td>
                      <td class="whitespace-nowrap text-right tabular-nums" :class="COR[l.vsMedia.cor]">{{ l.vsMedia.texto }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          <!-- por conta: vendas das 4 semanas, as 2 variações e o % de cada semana -->
          <div class="space-y-1.5">
            <h4 class="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Por conta</h4>
            <div class="table-card overflow-x-auto">
              <table class="w-full min-w-[1100px] text-xs">
                <thead>
                  <tr>
                    <th rowspan="2">Conta</th>
                    <th :colspan="semanasCab.length" class="text-center">Vendas</th>
                    <th rowspan="2" class="whitespace-nowrap text-right">vs semana anterior</th>
                    <th rowspan="2" class="whitespace-nowrap text-right">vs média 3 sem.</th>
                    <th :colspan="semanasCab.length" class="text-center">% s/ vendas</th>
                  </tr>
                  <tr>
                    <th v-for="s in semanasCab" :key="`v${s.i}`" class="whitespace-nowrap text-right">
                      {{ s.nome }} <span class="font-normal">{{ s.datas }}</span>
                    </th>
                    <th v-for="s in semanasCab" :key="`p${s.i}`" class="whitespace-nowrap text-right">
                      {{ s.nome }} <span class="font-normal">{{ s.datas }}</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <template v-for="g in porConta" :key="g.chave">
                    <tr class="bg-muted/20">
                      <td :colspan="3 + semanasCab.length * 2" class="text-[11px] font-semibold uppercase tracking-wide">{{ g.rotulo }}</td>
                    </tr>
                    <tr v-if="!g.linhas.length">
                      <td :colspan="3 + semanasCab.length * 2" class="py-3 text-center text-muted-foreground">{{ VAZIO_GRUPO[g.chave] }}</td>
                    </tr>
                    <tr v-for="l in g.linhas" :key="`${g.chave}-${l.chave}`">
                      <td class="whitespace-nowrap font-medium">{{ l.conta }}</td>
                      <td
                        v-for="(v, i) in l.vendas" :key="`v${i}`"
                        class="whitespace-nowrap text-right tabular-nums" :class="i === 0 && 'font-semibold'"
                      >
                        {{ v }}
                      </td>
                      <td class="whitespace-nowrap text-right tabular-nums" :class="COR[l.vsAnterior.cor]">{{ l.vsAnterior.texto }}</td>
                      <td class="whitespace-nowrap text-right tabular-nums" :class="COR[l.vsMedia.cor]">{{ l.vsMedia.texto }}</td>
                      <td v-for="(v, i) in l.pcts" :key="`p${i}`" class="whitespace-nowrap text-right tabular-nums">{{ v }}</td>
                    </tr>
                  </template>
                </tbody>
              </table>
            </div>
          </div>
        </section>

        <!-- notas -->
        <section class="space-y-2">
          <h3 class="text-sm font-semibold">Notas</h3>
          <div
            v-if="rel.contas_sem_dados?.length"
            class="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-400"
          >
            <p class="font-medium">Contas sem dados</p>
            <ul class="mt-1 space-y-0.5">
              <li v-for="s in rel.contas_sem_dados" :key="s.conta">
                {{ s.conta }} — {{ rotuloStatusColeta(s.status) }}<template v-if="s.erro">: {{ s.erro }}</template>
              </li>
            </ul>
          </div>
          <div
            v-if="rel.afiliados_incompletos?.length"
            class="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-400"
          >
            <span class="font-medium">Afiliados incompletos:</span> {{ afiliadosIncompletosTxt }}
          </div>
          <ul class="list-disc space-y-1 pl-5 text-xs text-muted-foreground">
            <li v-for="(n, i) in rel.notas ?? []" :key="i">{{ n }}</li>
            <li v-if="rel.nao_atribuido_ads?.length">
              Gasto de Ads sem produto (fica em Celular): {{ naoAtribuidoTxt }}
            </li>
          </ul>
          <details v-if="rel.divergencias?.length" class="rounded-md border px-3 py-2 text-xs">
            <summary class="cursor-pointer font-medium">
              {{ rel.divergencias.length }} {{ rel.divergencias.length === 1 ? 'produto' : 'produtos' }} em que o DaVinci e a
              categoria da Shopee discordam (vale o DaVinci)
            </summary>
            <div class="table-card mt-2 overflow-x-auto">
              <table class="w-full min-w-[640px] text-xs">
                <thead>
                  <tr>
                    <th>Conta</th>
                    <th>Item</th>
                    <th>Nome</th>
                    <th>No DaVinci</th>
                    <th>Categoria Shopee</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="d in rel.divergencias" :key="`${d.conta}-${d.item_id}`">
                    <td class="whitespace-nowrap">{{ d.conta }}</td>
                    <td class="whitespace-nowrap font-mono text-[11px]">{{ d.item_id }}</td>
                    <td class="max-w-[22rem]"><span class="line-clamp-2 break-words" :title="d.nome">{{ d.nome }}</span></td>
                    <td class="whitespace-nowrap">{{ d.davinci === 'eletro' ? 'eletro' : 'não é eletro' }}</td>
                    <td class="whitespace-nowrap">{{ categoriaTxt(d.categoria_shopee) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </details>
        </section>
      </div>
    </div>

    <!-- contas: quem entra, em que grupo e com que nome (só quem edita) -->
    <section v-if="canEdit" class="rounded-xl border bg-card">
      <button
        class="flex w-full flex-wrap items-center gap-x-2 gap-y-0.5 px-4 py-3 text-left text-sm font-semibold"
        :aria-expanded="contasAberto"
        @click="contasAberto = !contasAberto"
      >
        <ChevronRight class="size-4 shrink-0 transition-transform" :class="contasAberto && 'rotate-90'" />
        Contas da conferência
        <span class="text-xs font-normal text-muted-foreground">quem entra, em que grupo e com que nome</span>
      </button>
      <div v-if="contasAberto" class="space-y-2 border-t p-4">
        <p class="text-xs text-muted-foreground">
          Vale a partir da próxima conferência — relatório já gerado não muda. Eletro não é conta: sai dos produtos de
          eletro das contas de Celular.
        </p>
        <div v-if="contasCarregando && !contas" class="space-y-2" aria-busy="true">
          <div v-for="i in 4" :key="i" class="h-9 animate-pulse rounded-md bg-muted/40" />
        </div>
        <div
          v-else-if="contasErro && !contas"
          class="flex flex-wrap items-center gap-3 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-400"
        >
          <span>Não consegui carregar as contas ({{ contasErro }}).</span>
          <button class="btn btn-xs" @click="carregarContas">Tentar de novo</button>
        </div>
        <div v-else-if="contas" class="table-card overflow-x-auto">
          <table class="w-full min-w-[640px] text-xs">
            <thead>
              <tr>
                <th>Nome no relatório</th>
                <th>Grupo</th>
                <th>Entra na conferência</th>
                <th>Perfil AdsPower</th>
                <th>Conta no DaVinci</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="!contasOrdenadas.length">
                <td colspan="5" class="py-4 text-center text-muted-foreground">Nenhuma conta cadastrada.</td>
              </tr>
              <tr v-for="c in contasOrdenadas" :key="c.id" :class="!c.ativo && 'text-muted-foreground'">
                <td>
                  <input
                    v-model="nomes[c.id]"
                    class="h-8 w-40 rounded-md border bg-background px-2 text-sm text-foreground"
                    maxlength="60"
                    aria-label="Nome no relatório"
                    :disabled="salvandoConta === c.id"
                    @keydown.enter="($event.target as HTMLInputElement).blur()"
                    @change="salvarNome(c)"
                  >
                </td>
                <td>
                  <select
                    :value="c.grupo"
                    class="h-8 rounded-md border bg-background px-2 text-sm text-foreground"
                    aria-label="Grupo"
                    :disabled="salvandoConta === c.id"
                    @change="(e) => salvarConta(c, { grupo: (e.target as HTMLSelectElement).value as 'mala' | 'celular' })"
                  >
                    <option value="mala">Mala</option>
                    <option value="celular">Celular</option>
                  </select>
                </td>
                <td>
                  <label class="inline-flex cursor-pointer items-center gap-1.5">
                    <input
                      type="checkbox" class="size-4"
                      :checked="c.ativo"
                      :disabled="salvandoConta === c.id"
                      @change="salvarConta(c, { ativo: !c.ativo })"
                    >
                    {{ c.ativo ? 'sim' : 'não' }}
                    <Loader2 v-if="salvandoConta === c.id" class="size-3 animate-spin" />
                  </label>
                </td>
                <td class="whitespace-nowrap font-mono text-[11px] text-muted-foreground">{{ c.adspower_user_id }}</td>
                <td class="text-muted-foreground">{{ c.conta_key || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>

    <InformarThreemaModal
      :open="informarAberto"
      contexto="conferencia_shopee"
      somente-cadastro
      descricao="Quem está marcado recebe no Threema o resumo da Conferência Shopee quando ela termina (terça e quinta à tarde): vendas, investimento e % s/ vendas de Mala, Celular, Eletro e Geral, as contas sem dados e o link do Excel. Sem ninguém marcado, não vai para ninguém. A seleção fica salva."
      @close="informarAberto = false"
    />
  </div>
</template>
