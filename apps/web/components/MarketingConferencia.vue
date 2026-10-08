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
 * 3. Conta sem dados continua contada, com o motivo (deslogada, Firefox,
 *    bloqueada…) no aviso embaixo da tabela: o total do grupo não pode parecer
 *    completo sem estar.
 *
 * 07/10/2026: o Resumo virou a planilha antiga do dono ("mais ou menos desse
 * jeito"): métrica × (semana × Mala/Celular/Eletro/Geral), semanas da mais
 * velha pra mais nova, e o bloco Variação (semana atual × anterior) no fim.
 * As linhas e colunas saem de planilhaResumo() (lib/conferencia.ts), o mesmo
 * desenho do Excel e do HTML.
 *
 * 07/10/2026 (tarde): o mesmo relatório para o Mercado Livre e a Amazon — um
 * por marketplace, escolhido no seletor Shopee | Mercado Livre | Amazon (vai
 * pra URL como ?conf=ml / ?conf=amazon; sem nada = Shopee). No ML e na Amazon
 * quem coleta é o servidor (vendas do Bling + Ads pela API), sem AdsPower. O
 * que o marketplace não dá vem em `relatorio.estados` e a célula mostra "não
 * coletado" / "não se aplica" / "aguardando acesso" numa pílula cinza, no
 * lugar do "—" (que continua sendo "faltou o dado").
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  AlertTriangle, Ban, Bell, ChevronRight, FileCode, FileJson, FileSpreadsheet, FileText,
  Inbox, Loader2, Play, RefreshCw, RotateCcw,
} from 'lucide-vue-next'
import { apiErrMsg } from '~/lib/apiError'
import {
  ERROS_CONFERENCIA, PLATAFORMAS,
  coletaTerminou, comparadoCom, dataHoraBr, ddmm, horaBr,
  nomeArquivo, nomeDoCabecalho, planilhaResumo, plataformaValida, rotuloExecucao,
  infoPlataforma, rotuloStatusColeta, rotuloStatusExecucao, tituloRelatorio, tomStatusColeta,
  type Coleta, type ContaConferencia, type Cor, type DetalheExecucao, type ExecucaoResumo, type Formato,
  type IntegracaoConferencia, type Plataforma,
} from '~/lib/conferencia'

const { api } = useApi()
const toasts = useToasts()
const canEdit = useCan('marketing', 'edit')
// O cadastro de quem recebe no Threema (routers/informar.py) é só de admin.
const auth = useAuthStore()
const isAdmin = computed(() => auth.user?.role === 'admin')
const route = useRoute()
const router = useRouter()

// O prefixo continua o da Shopee (o router é um só): o marketplace vai no
// ?plataforma= da lista, do "Gerar agora" e das contas.
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
const FORMATOS: { fmt: Formato; rotulo: string; icone: any; dica: string }[] = [
  { fmt: 'xlsx', rotulo: 'Excel', icone: FileSpreadsheet, dica: 'Planilha com as 4 semanas' },
  { fmt: 'csv', rotulo: 'CSV', icone: FileText, dica: 'Separado por ";" com vírgula decimal (abre no Excel)' },
  { fmt: 'md', rotulo: 'MD', icone: FileText, dica: 'Texto (Markdown)' },
  { fmt: 'json', rotulo: 'JSON', icone: FileJson, dica: 'Dados brutos do relatório' },
  { fmt: 'html', rotulo: 'HTML', icone: FileCode, dica: 'Abre o relatório numa aba nova (dá pra imprimir)' },
]
// Na tela só o Excel (pedido de 07/10/2026: "só o resumo"). Os outros formatos
// seguem na API para quem precisar dos dados.
const FORMATOS_NA_TELA = FORMATOS.filter((f) => f.fmt === 'xlsx')

// Quem coleta em cada marketplace, nas frases da tela: na Shopee é o robô do
// Mac pelo AdsPower; no ML e na Amazon é o servidor, pelas APIs e pelo Bling.
const COLETA_TXT: Record<Plataforma, { confirmar: string; iniciada: string; andamento: string }> = {
  shopee: {
    confirmar: 'O robô do Mac abre o perfil de cada loja no AdsPower, uma de cada vez.',
    iniciada: 'O robô do Mac vai passar loja por loja. Esta tela acompanha sozinha.',
    andamento: 'O robô do Mac abre uma loja por vez.',
  },
  ml: {
    confirmar: 'O servidor lê as vendas do Bling e o Ads do Mercado Livre pela API, conta por conta (sem abrir o AdsPower). '
      + 'Afiliados ficam "não coletado" nesta versão.',
    iniciada: 'O servidor vai passar conta por conta (vendas do Bling e Ads pela API). Esta tela acompanha sozinha.',
    andamento: 'O servidor lê uma conta por vez: vendas do Bling e Ads pela API do Mercado Livre.',
  },
  amazon: {
    confirmar: 'O servidor lê as vendas do Bling, conta por conta (sem abrir o AdsPower). '
      + 'O Ads da Amazon fica "aguardando acesso" até a API de Ads ser liberada.',
    iniciada: 'O servidor vai passar conta por conta (vendas do Bling). Esta tela acompanha sozinha.',
    andamento: 'O servidor lê uma conta por vez: vendas do Bling (o Ads da Amazon ainda aguarda acesso).',
  },
}

// ---------- marketplace (Shopee | Mercado Livre | Amazon)

// ?conf=ml abre direto no Mercado Livre (F5, link). Valor estranho → Shopee.
const plataforma = ref<Plataforma>(plataformaValida(route.query.conf) ?? 'shopee')
// Os textos do marketplace escolhido ("Mercado Livre", "do Mercado Livre"…).
const plat = computed(() => infoPlataforma(plataforma.value))

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
  // Trocou de marketplace no meio do caminho: a lista velha não escreve na tela.
  const p = plataforma.value
  try {
    const r = await api<ExecucaoResumo[]>(`${BASE}/execucoes?limite=30&plataforma=${p}`)
    if (p !== plataforma.value) return null
    execucoes.value = Array.isArray(r) ? r : []
    erro.value = null
    return execucoes.value
  } catch (e: any) {
    if (p !== plataforma.value) return null
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
    // Link do Threema de um relatório do ML aberto na Shopee (ou ao contrário):
    // a tela vai pro marketplace dele, sem perder o relatório já carregado.
    const dele = plataformaValida(r.execucao?.plataforma) ?? plataformaValida(r.relatorio?.plataforma)
    if (dele && dele !== plataforma.value) adotarPlataforma(dele)
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

/** ?conf= da URL: o marketplace (Shopee não escreve nada, é o padrão). */
function queryComPlataforma(p: Plataforma, semExecucao: boolean): Record<string, any> {
  const query: Record<string, any> = { ...route.query, conf: p }
  if (p === 'shopee') delete query.conf
  if (semExecucao) delete query.execucao
  return query
}

/**
 * A pessoa trocou o marketplace: tudo do outro sai da tela (lista, relatório,
 * acompanhamento, contas) e entra o último relatório pronto deste.
 */
function trocarPlataforma(p: Plataforma) {
  if (p === plataforma.value) return
  plataforma.value = p
  // Resposta do marketplace de antes que ainda chegar não escreve mais nada.
  geracao++
  window.clearTimeout(timer)
  timer = undefined
  execucoes.value = []
  listaCarregada.value = false
  erro.value = null
  selecionada.value = null
  detalhe.value = null
  erroDetalhe.value = null
  carregandoDetalhe.value = false
  void router.replace({ query: queryComPlataforma(p, true) })
  void iniciar('')
}

/** O relatório aberto é de outro marketplace (link): só o seletor e a lista acompanham. */
function adotarPlataforma(p: Plataforma) {
  plataforma.value = p
  void router.replace({ query: queryComPlataforma(p, false) })
  void carregarLista()
}

// `pedida`: o ?execucao= do link; na troca de marketplace, nenhuma (a URL
// ainda pode estar com a do outro enquanto o router.replace não termina).
async function iniciar(pedida = String(route.query.execucao || '')) {
  const p = plataforma.value
  carregandoLista.value = true
  const lista = await carregarLista()
  // Trocou de marketplace enquanto lia: quem manda agora é o iniciar() do novo.
  if (p !== plataforma.value) return
  carregandoLista.value = false
  listaCarregada.value = true
  if (!lista) return
  selecionada.value = escolhaPadrao(lista, pedida)
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
  const p = plataforma.value
  const txt = COLETA_TXT[p]
  const qual = tipoNovo.value === 'parcial' ? 'parcial (segunda até ontem)' : 'da semana fechada (segunda a domingo)'
  if (!window.confirm(
    `Gerar agora a conferência ${infoPlataforma(p).da} ${qual}?\n\n${txt.confirmar} `
    + 'Numa segunda-feira a parcial vira semanal (ainda não tem dia na semana).',
  )) return
  gerando.value = true
  try {
    // ?plataforma= como na lista e nas contas; o corpo leva o mesmo valor.
    const r = await api<any>(`${BASE}/execucoes?plataforma=${p}`, {
      method: 'POST', body: { tipo: tipoNovo.value, plataforma: p },
    })
    toasts.success('Conferência iniciada', txt.iniciada)
    const lista = await carregarLista()
    // Trocou de marketplace enquanto gerava: a nova fica na lista do outro.
    if (p !== plataforma.value) return
    const novo = r?.id ?? r?.execucao?.id ?? lista?.find((x) => x.status === 'coletando')?.id
    if (novo) escolher(String(novo))
  } catch (e: any) {
    if (erroJaColetando(e)) {
      toasts.warning('Já tem uma conferência coletando', 'Espere ela terminar ou cancele antes de gerar outra.')
      const lista = await carregarLista()
      if (p !== plataforma.value) return
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
  let nome = nomeArquivo(d.relatorio.semanas, fmt, plataformaAberta.value)
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

// A planilha do Resumo (linhas, colunas, textos e cores já prontos). Relatório
// de antes de 07/10 não tem cliques/pedidos/conversão: essas linhas saem "—".
const planilha = computed(() => planilhaResumo(rel.value))
// O marketplace do que está aberto: o que o relatório (ou a execução) diz; sem
// o campo — relatório de antes de 07/10, que é da Shopee —, o do seletor.
const plataformaAberta = computed<Plataforma>(() =>
  plataformaValida(rel.value?.plataforma) ?? plataformaValida(exec.value?.plataforma) ?? plataforma.value)
// Blocos de 4 colunas no 2º cabeçalho: uma por semana + a Variação.
const blocosCab = computed(() => (planilha.value ? planilha.value.semanas.length + 1 : 0))

const semDadosTxt = computed(() =>
  (rel.value?.contas_sem_dados ?? []).map((c) => `${c.conta} (${rotuloStatusColeta(c.status, plataformaAberta.value)})`).join(', '),
)
const afiliadosIncompletosTxt = computed(() =>
  (rel.value?.afiliados_incompletos ?? []).map((a) => `${a.conta} (até ${ddmm(a.ate)})`).join(', '),
)

// ---------- contas (quem entra, grupo e nome)

const contasAberto = ref(false)
const contas = ref<ContaConferencia[] | null>(null)
const contasCarregando = ref(false)
const contasErro = ref<string | null>(null)
const salvandoConta = ref<string | null>(null)
// Rascunho do nome de cada conta: só vai pro servidor no Enter ou ao sair do campo.
const nomes = ref<Record<string, string>>({})
// ML e Amazon: o mesmo rascunho para o id da loja do Bling.
const lojas = ref<Record<string, string>>({})
// As integrações do DaVinci deste marketplace, para ligar a uma conta (ML/Amazon).
const integracoes = ref<IntegracaoConferencia[] | null>(null)
// Salvamento que falhou (ou foi ignorado): as linhas redesenham e o select e a
// caixinha voltam ao que está salvo — senão mostrariam a escolha que não pegou.
const versaoContas = ref(0)

const contasOrdenadas = computed(() => [...(contas.value ?? [])].sort((a, b) =>
  (a.grupo === b.grupo ? 0 : a.grupo === 'mala' ? -1 : 1)
  || a.nome.localeCompare(b.nome, 'pt-BR', { sensitivity: 'base' }),
))

async function carregarContas() {
  const p = plataforma.value
  contasCarregando.value = true
  contasErro.value = null
  if (p !== 'shopee') void carregarIntegracoes(p)
  try {
    const r = await api<ContaConferencia[]>(`${BASE}/contas?plataforma=${p}`)
    if (p !== plataforma.value) return
    contas.value = Array.isArray(r) ? r : []
    nomes.value = Object.fromEntries(contas.value.map((c) => [c.id, c.nome]))
    lojas.value = Object.fromEntries(contas.value.map((c) => [c.id, c.bling_loja_id ?? '']))
  } catch (e: any) {
    if (p === plataforma.value) contasErro.value = apiErrMsg(e, ERROS_CONFERENCIA)
  } finally {
    if (p === plataforma.value) contasCarregando.value = false
  }
}
async function carregarIntegracoes(p: Plataforma) {
  try {
    const r = await api<IntegracaoConferencia[]>(`${BASE}/contas/integracoes?plataforma=${p}`)
    if (p === plataforma.value) integracoes.value = Array.isArray(r) ? r : []
  } catch {
    // Sem a lista, a tela mostra só o nome da integração ligada (sem o select).
  }
}
watch(contasAberto, (aberto) => {
  if (aberto && !contas.value && !contasCarregando.value) void carregarContas()
})
// Cada marketplace tem as suas contas: trocou, as do outro saem (e as deste
// entram, se a seção estiver aberta).
watch(plataforma, () => {
  contas.value = null
  nomes.value = {}
  lojas.value = {}
  integracoes.value = null
  contasErro.value = null
  contasCarregando.value = false
  if (contasAberto.value) void carregarContas()
})
// No ML e na Amazon a conta vem de uma integração do DaVinci (não de um perfil
// do AdsPower): é ela que a tabela mostra.
const contasPorIntegracao = computed(() => plataforma.value !== 'shopee')
/**
 * Conta do ML/Amazon sem a integração do DaVinci ou sem a loja do Bling não tem
 * de onde ler (Ads / vendas): não dá pra colocar na conferência.
 */
function semVinculo(c: ContaConferencia): boolean {
  return contasPorIntegracao.value && (!c.integration_id || !c.bling_loja_id)
}
/** Outra conta da lista já usa essa integração (o servidor recusa com integracao_em_uso). */
function usadaPor(integracaoId: string, contaId: string): string | null {
  return (contas.value ?? []).find((x) => x.id !== contaId && x.integration_id === integracaoId)?.nome ?? null
}

type CamposConta = Partial<Pick<ContaConferencia, 'nome' | 'grupo' | 'ativo' | 'integration_id' | 'bling_loja_id'>>
async function salvarConta(c: ContaConferencia, campos: CamposConta) {
  if (salvandoConta.value) {
    versaoContas.value++
    return
  }
  salvandoConta.value = c.id
  try {
    const r = await api<ContaConferencia>(`${BASE}/contas/${encodeURIComponent(c.id)}`, { method: 'PUT', body: campos })
    const nova: ContaConferencia = r && typeof r === 'object' && 'id' in r ? r : { ...c, ...campos }
    contas.value = (contas.value ?? []).map((x) => (x.id === c.id ? nova : x))
    nomes.value = { ...nomes.value, [c.id]: nova.nome }
    lojas.value = { ...lojas.value, [c.id]: nova.bling_loja_id ?? '' }
    toasts.success('Conta salva', `${nova.nome}: vale a partir da próxima conferência.`)
  } catch (e: any) {
    nomes.value = { ...nomes.value, [c.id]: c.nome }
    lojas.value = { ...lojas.value, [c.id]: c.bling_loja_id ?? '' }
    versaoContas.value++
    toasts.error('Não consegui salvar a conta', apiErrMsg(e, ERROS_CONFERENCIA))
  } finally {
    salvandoConta.value = null
  }
}
/** Integração escolhida no select ('' = desligar). */
function salvarIntegracao(c: ContaConferencia, id: string) {
  const nova = id || null
  if (nova !== (c.integration_id ?? null)) void salvarConta(c, { integration_id: nova })
}
/** Loja do Bling: só número (o servidor recusa o resto); vazio desliga. */
function salvarLoja(c: ContaConferencia) {
  const v = (lojas.value[c.id] ?? '').trim()
  if (v && !/^\d{1,20}$/.test(v)) {
    lojas.value = { ...lojas.value, [c.id]: c.bling_loja_id ?? '' }
    toasts.error('Loja do Bling inválida', 'O id da loja do Bling é só número (ex.: 204438129).')
    return
  }
  if (v !== (c.bling_loja_id ?? '')) void salvarConta(c, { bling_loja_id: v || null })
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
    <!-- qual marketplace: um relatório por marketplace (07/10/2026) -->
    <div class="flex w-fit max-w-full gap-1 overflow-x-auto rounded-md bg-muted/40 p-1" role="tablist" aria-label="Marketplace da conferência">
      <button
        v-for="p in PLATAFORMAS" :key="p.chave"
        role="tab" :aria-selected="plataforma === p.chave"
        class="whitespace-nowrap rounded px-3 py-1 text-sm transition-colors"
        :class="plataforma === p.chave ? 'bg-background font-medium shadow-sm' : 'text-muted-foreground hover:bg-background/60'"
        @click="trocarPlataforma(p.chave)"
      >
        {{ p.rotulo }}
      </button>
    </div>

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
          v-for="f in FORMATOS_NA_TELA" :key="f.fmt"
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
      <p class="font-medium text-foreground">Nenhum relatório {{ plat.da }} ainda</p>
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
            {{ concluidas }} de {{ coletas.length }} concluídas · {{ tituloRelatorio(exec.semanas, plataformaAberta) }}
          </span>
          <span v-if="exec.status === 'coletando'" class="text-xs text-muted-foreground">· atualiza sozinho a cada 20 s</span>
        </div>
        <p v-if="exec.status === 'coletando'" class="text-xs text-muted-foreground">
          {{ COLETA_TXT[plataformaAberta].andamento }} {{ prazosTxt }}
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
                    {{ rotuloStatusColeta(c.status, plataformaAberta) }}
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
            {{ tituloRelatorio(rel.semanas, plataformaAberta) }}
            <span :class="rel.tipo === 'parcial' ? 'pill-warning' : 'pill-muted'">
              {{ rel.tipo === 'parcial' ? 'semana parcial' : 'semana fechada' }}
            </span>
          </h2>
          <p class="text-xs text-muted-foreground">
            {{ comparadoCom(rel.semanas) }}<template v-if="rel.gerado_em"> · gerado em {{ dataHoraBr(rel.gerado_em) }}</template>
            <template v-if="rel.origem === 'manual'"> · gerado pelo botão</template>
          </p>
        </header>

        <!-- RESUMO no formato da planilha antiga do dono (07/10/2026, "mais ou menos
             desse jeito"): categoria + sub-rótulo, cada semana (da mais velha pra mais
             nova) com Mala · Celular · Eletro · Geral, e a Variação no fim. Larga: rola
             pro lado com as duas colunas de rótulo paradas. -->
        <div v-if="planilha" class="planilha overflow-x-auto rounded-xl border">
          <table class="text-xs" aria-label="Resumo da conferência por semana e grupo">
            <thead>
              <tr>
                <th class="rotulo-cab" colspan="2" rowspan="2" scope="col">Métrica</th>
                <!-- o texto num span "parado": no celular a célula mesclada (4 colunas) é mais
                     larga que o que sobra ao lado das colunas de rótulo, e a data centrada
                     ficava fora da tela, em cima de números sem semana -->
                <th
                  v-for="s in planilha.semanas" :key="s.indice"
                  colspan="4" scope="colgroup" class="semana-cab whitespace-nowrap"
                >
                  <span class="semana-txt">{{ s.rotulo }}</span>
                </th>
                <th colspan="4" scope="colgroup" class="semana-cab whitespace-nowrap">
                  <span class="semana-txt">{{ planilha.variacao }}</span>
                </th>
              </tr>
              <tr>
                <template v-for="b in blocosCab" :key="b">
                  <th
                    v-for="g in planilha.grupos" :key="`${b}-${g.chave}`"
                    scope="col" class="grupo-cab whitespace-nowrap" :class="g.chave === 'geral' && 'geral'"
                  >
                    {{ g.rotulo }}
                  </th>
                </template>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(l, k) in planilha.linhas" :key="l.chave">
                <th
                  v-if="l.span" :rowspan="l.span" scope="rowgroup"
                  class="cat" :class="k + l.span >= planilha.linhas.length && 'ultima'"
                >
                  {{ l.categoria }}
                </th>
                <th scope="row" class="sub">{{ l.sub }}</th>
                <!-- métrica que este marketplace não dá ("não coletado", "não se aplica",
                     "aguardando acesso"): a linha inteira com o texto numa pílula cinza, no
                     lugar do número e do "—" -->
                <template v-if="l.estado">
                  <template v-for="(sem, i) in l.valores" :key="i">
                    <td
                      v-for="(v, j) in sem" :key="j"
                      class="estado" :class="planilha.grupos[j]?.chave === 'geral' && 'geral'"
                    >
                      <span class="pill-muted">{{ v }}</span>
                    </td>
                  </template>
                  <td
                    v-for="(v, j) in l.variacoes" :key="`var-${j}`"
                    class="var estado" :class="planilha.grupos[j]?.chave === 'geral' && 'geral'"
                  >
                    <span class="pill-muted">{{ v.texto }}</span>
                  </td>
                </template>
                <template v-else>
                  <template v-for="(sem, i) in l.valores" :key="i">
                    <td
                      v-for="(v, j) in sem" :key="j"
                      class="num" :class="planilha.grupos[j]?.chave === 'geral' && 'geral'"
                    >
                      {{ v }}
                    </td>
                  </template>
                  <td
                    v-for="(v, j) in l.variacoes" :key="`var-${j}`"
                    class="var" :class="[COR[v.cor], planilha.grupos[j]?.chave === 'geral' && 'geral']"
                  >
                    {{ v.texto }}
                  </td>
                </template>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- só os avisos que mudam a leitura -->
        <div
          v-if="semDadosTxt || afiliadosIncompletosTxt"
          class="space-y-0.5 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-400"
        >
          <p v-if="semDadosTxt"><span class="font-medium">Sem dados:</span> {{ semDadosTxt }}</p>
          <p v-if="afiliadosIncompletosTxt"><span class="font-medium">Afiliados incompletos:</span> {{ afiliadosIncompletosTxt }}</p>
        </div>
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
        <span class="text-xs font-normal text-muted-foreground">{{ plat.rotulo }} · quem entra, em que grupo e com que nome</span>
      </button>
      <div v-if="contasAberto" class="space-y-2 border-t p-4">
        <p class="text-xs text-muted-foreground">
          Vale a partir da próxima conferência — relatório já gerado não muda. Eletro não é conta: sai dos produtos de
          eletro das contas de Celular.
          <template v-if="contasPorIntegracao">
            Cada conta {{ plat.da }} lê o Ads da integração do DaVinci e as vendas da loja do Bling; sem as duas
            ligadas, a conta não entra.
          </template>
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
          <table class="w-full text-xs" :class="contasPorIntegracao ? 'min-w-[760px]' : 'min-w-[640px]'">
            <thead>
              <tr>
                <th>Nome no relatório</th>
                <th>Grupo</th>
                <th>Entra na conferência</th>
                <template v-if="contasPorIntegracao">
                  <th>Integração no DaVinci</th>
                  <th>Loja no Bling</th>
                </template>
                <template v-else>
                  <th>Perfil AdsPower</th>
                  <th>Conta no DaVinci</th>
                </template>
              </tr>
            </thead>
            <tbody>
              <tr v-if="!contasOrdenadas.length">
                <td colspan="5" class="py-4 text-center text-muted-foreground">Nenhuma conta cadastrada.</td>
              </tr>
              <tr v-for="c in contasOrdenadas" :key="`${c.id}:${versaoContas}`" :class="!c.ativo && 'text-muted-foreground'">
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
                  <label
                    class="inline-flex items-center gap-1.5"
                    :class="semVinculo(c) && !c.ativo ? 'cursor-not-allowed' : 'cursor-pointer'"
                    :title="semVinculo(c) && !c.ativo ? 'Falta ligar a integração do DaVinci e a loja do Bling: sem elas não tem de onde ler os números' : undefined"
                  >
                    <input
                      type="checkbox" class="size-4"
                      :checked="c.ativo"
                      :disabled="salvandoConta === c.id || (semVinculo(c) && !c.ativo)"
                      @change="salvarConta(c, { ativo: !c.ativo })"
                    >
                    {{ c.ativo ? 'sim' : 'não' }}
                    <Loader2 v-if="salvandoConta === c.id" class="size-3 animate-spin" />
                  </label>
                </td>
                <template v-if="contasPorIntegracao">
                  <td class="max-w-[18rem]">
                    <!-- a lista das integrações deste marketplace; sem ela (falhou), só o nome -->
                    <select
                      v-if="integracoes"
                      :value="c.integration_id ?? ''"
                      class="h-8 w-56 max-w-full rounded-md border bg-background px-2 text-sm text-foreground"
                      aria-label="Integração no DaVinci"
                      :disabled="salvandoConta === c.id"
                      @change="(e) => salvarIntegracao(c, (e.target as HTMLSelectElement).value)"
                    >
                      <option value="">— sem integração —</option>
                      <option v-if="c.integration_id && !integracoes.some((i) => i.id === c.integration_id)" :value="c.integration_id">
                        {{ c.integracao_nome || 'integração ligada' }}
                      </option>
                      <option v-for="i in integracoes" :key="i.id" :value="i.id" :disabled="!!usadaPor(i.id, c.id)">
                        {{ i.nome }}{{ i.arquivada ? ' (arquivada)' : '' }}{{ usadaPor(i.id, c.id) ? ` — já em ${usadaPor(i.id, c.id)}` : '' }}
                      </option>
                    </select>
                    <template v-else>
                      <span v-if="c.integration_id" class="text-foreground">{{ c.integracao_nome || 'integração ligada' }}</span>
                      <span v-else class="pill-muted">sem integração</span>
                    </template>
                    <span v-if="c.integracao_arquivada" class="pill-warning ml-1">arquivada</span>
                    <span v-if="c.observacao" class="mt-0.5 block whitespace-normal text-[11px] text-muted-foreground" :title="c.observacao">
                      {{ c.observacao }}
                    </span>
                  </td>
                  <td>
                    <input
                      v-model="lojas[c.id]"
                      class="h-8 w-28 rounded-md border bg-background px-2 font-mono text-xs text-foreground"
                      inputmode="numeric" maxlength="20" placeholder="id da loja"
                      aria-label="Id da loja no Bling"
                      :disabled="salvandoConta === c.id"
                      @keydown.enter="($event.target as HTMLInputElement).blur()"
                      @change="salvarLoja(c)"
                    >
                  </td>
                </template>
                <template v-else>
                  <td class="whitespace-nowrap font-mono text-[11px] text-muted-foreground">{{ c.adspower_user_id || '—' }}</td>
                  <td class="text-muted-foreground">{{ c.conta_key || '—' }}</td>
                </template>
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
      descricao="Quem está marcado recebe no Threema o resumo de cada Conferência quando ela termina (Shopee, Mercado Livre e Amazon, cada uma na sua mensagem; terça e quinta à tarde quando a agenda está ligada): vendas, investimento e % s/ vendas de Mala, Celular, Eletro e Geral, as contas sem dados e o link do Excel. Sem ninguém marcado, não vai para ninguém. A seleção fica salva."
      @close="informarAberto = false"
    />
  </div>
</template>

<style scoped>
/* Visual de planilha (pedidos de 07/10/2026: "deixa no formato de excel … tá muito
   branco, não tem azul, as colunas separadas" e "mais ou menos desse jeito"). Mesma
   paleta do Excel exportado e da planilha antiga do dono: azul 1F3864 no
   cabeçalho, bege DDD9C4 nos rótulos e no Geral, grade BFBFBF. Sem @layer: ganha
   do .table-card global (que está em @layer components). */
.conferencia {
  --conf-azul: #1f3864;
  --conf-azul-txt: #ffffff;
  --conf-grade: #bfbfbf;
  --conf-zebra: #f3f6fb;
  --conf-hover: #e8eef8;
  --conf-bege: #ddd9c4;
  --conf-celula: #ffffff;
  /* largura da coluna da categoria = onde a 2ª coluna parada começa */
  --conf-cat-w: 6.5rem;
  /* largura da 2ª coluna parada: cat + sub = onde a data da semana gruda */
  --conf-sub-w: 11rem;
}
/* ".dark .conferencia", não ":global(.dark) .conferencia": o Vue compila o
   :global(...) seguido de mais seletor para só ".dark" — as variáveis escuras
   iam pro <html> e as claras do .conferencia ganhavam (célula branca com texto
   claro no modo escuro). Com o scoped, só o .conferencia ganha o data-v. */
.dark .conferencia {
  --conf-azul: #1f3864;
  --conf-azul-txt: #f1f5fb;
  --conf-grade: #3a4556;
  --conf-zebra: rgba(255, 255, 255, 0.035);
  --conf-hover: rgba(68, 114, 196, 0.14);
  /* bege escurecido: continua "a coluna bege" e o texto claro se lê em cima */
  --conf-bege: #3b3829;
  --conf-celula: hsl(var(--card));
}
@media (max-width: 639px) {
  .conferencia { --conf-cat-w: 5.75rem; --conf-sub-w: 6.5rem; }
}

/* ── grade das tabelas de apoio (lojas coletando, contas) ── */
.table-card { border-color: var(--conf-grade); }
.table-card table { border-collapse: collapse; }
.table-card thead th {
  background: var(--conf-azul);
  color: var(--conf-azul-txt);
  font-weight: 600;
  text-align: center;
  border: 1px solid var(--conf-grade);
  border-top: 0;
  padding: 0.45rem 0.6rem;
}
.table-card tbody td {
  border: 1px solid var(--conf-grade);
  padding: 0.4rem 0.6rem;
  font-size: 0.75rem;
}
.table-card thead th:first-child,
.table-card tbody td:first-child { border-left: 0; }
.table-card thead th:last-child,
.table-card tbody td:last-child { border-right: 0; }
.table-card tbody tr:last-child > td { border-bottom: 0; }
.table-card tbody tr:nth-child(even) > td { background: var(--conf-zebra); }
.table-card tbody tr:hover > td { background: var(--conf-hover); }

/* ── a planilha do Resumo ──
   border-collapse: separate (cada célula com a sua borda à direita e embaixo):
   com "collapse" a borda das colunas paradas fica pra trás ao rolar pro lado. */
.planilha {
  border-color: var(--conf-grade);
  background: var(--conf-celula);
}
.planilha table {
  border-collapse: separate;
  border-spacing: 0;
  min-width: 100%;
}
.planilha th,
.planilha td {
  border-right: 1px solid var(--conf-grade);
  border-bottom: 1px solid var(--conf-grade);
  padding: 0.4rem 0.6rem;
  white-space: nowrap;
}
.planilha tr > :last-child { border-right: 0; }
.planilha tbody tr:last-child > *,
.planilha tbody .cat.ultima { border-bottom: 0; }

/* cabeçalho: as duas linhas em azul, texto branco em negrito */
.planilha thead th {
  background: var(--conf-azul);
  color: var(--conf-azul-txt);
  font-weight: 700;
  text-align: center;
}

/* corpo: números à direita, variação no centro, Geral e rótulos em bege */
.planilha td { background: var(--conf-celula); }
.planilha td.num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.planilha td.var {
  text-align: center;
  font-variant-numeric: tabular-nums;
}
/* métrica que o marketplace não dá: a pílula cinza no meio da célula */
.planilha td.estado { text-align: center; }
.planilha td.geral {
  background: var(--conf-bege);
  font-weight: 600;
}
.planilha tbody th {
  background: var(--conf-bege);
  text-align: left;
}
.planilha tbody th.cat {
  font-weight: 700;
  vertical-align: middle;
}
.planilha tbody th.sub { font-weight: 500; }

/* as duas colunas de rótulo ficam paradas ao rolar pro lado */
.planilha .rotulo-cab,
.planilha .cat,
.planilha .sub {
  position: sticky;
  z-index: 1;
}
.planilha .rotulo-cab {
  left: 0;
  z-index: 2;
  text-align: left;
}
.planilha .cat {
  left: 0;
  width: var(--conf-cat-w);
  min-width: var(--conf-cat-w);
  max-width: var(--conf-cat-w);
  overflow: hidden;
  text-overflow: ellipsis;
}
.planilha .sub {
  left: var(--conf-cat-w);
  width: var(--conf-sub-w);
  min-width: var(--conf-sub-w);
  max-width: var(--conf-sub-w);
  /* separa as colunas paradas do que passa por baixo delas */
  box-shadow: 2px 0 0 var(--conf-grade);
}
/* A data da semana (e o "Variação (…)") fica à vista enquanto o bloco dela
   estiver na tela: gruda logo depois das 2 colunas paradas e não passa da
   borda direita. No desktop, com a tabela no começo, fica centrada como na
   planilha. */
.planilha .semana-txt {
  display: inline-block;
  position: sticky;
  left: calc(var(--conf-cat-w) + var(--conf-sub-w) + 0.6rem);
  right: 0.6rem;
}
@media (max-width: 639px) {
  /* celular: células mais justas e "% investimento / vendas" em 2 linhas, pra as
     duas colunas paradas não comerem a tela dos números */
  .planilha th,
  .planilha td { padding: 0.35rem 0.45rem; }
  .planilha tbody th { font-size: 0.6875rem; }
  .planilha .sub { white-space: normal; }
  /* data à esquerda, grudada só pela esquerda, e o "Variação (…)" quebrando em
     linhas: mais largo que o que sobra da tela, ele escorregava pra baixo das
     colunas paradas no fim da rolagem e só o fim aparecia */
  .planilha thead th.semana-cab { text-align: left; }
  .planilha .semana-txt {
    left: calc(var(--conf-cat-w) + var(--conf-sub-w) + 0.45rem);
    right: auto;
    max-width: 6.5rem;
    white-space: normal;
  }
}
</style>
