<script setup lang="ts">
// Aba "Emitir do mês": as notas fixas ativas, já conferidas para o mês
// escolhido (o que falta, se já saiu, se está na prefeitura, se a NFE.io ficou
// sem responder). Marca, confere o valor e emite — a emissão em si é do
// assistente NfseEmitirLote (confirmação, EMITIR para empresa em Produção na
// NFE.io, notas uma a uma e o acompanhamento "Na prefeitura…").
//
// 29/09/2026 (motor NFE.io): cada empresa tem o seu ambiente na NFE.io (selo
// TESTE/PRODUÇÃO na linha da empresa) e a prévia traz o IR retido e o aviso de
// nota já emitida na NFE.io para o mesmo tomador no mês.
//
// 28/09/2026 (Eduardo: "to achando simples e bagunçado"): reescrita sem props.
// Os dados vêm de useNfseTela(); a aba só busca a prévia e as notas do mês.
// Carrega no mount, quando o mês muda e quando a página recarrega (versão).
//
// 29/09 (Eduardo: "quero emitir uma nota de serviço de 0,5%"): nota fixa de
// PERCENTUAL. A linha mostra o % e um campo "Base (R$)" (já com a base padrão,
// se a nota fixa tiver); o valor da nota sai calculado ao vivo — base × % ÷ 100,
// a mesma conta do servidor — e vai escrito "0,5% de R$ 200.000,00". Sem base a
// linha não marca ("digite a base"). Mudou a base: a prévia daquela linha é
// refeita mandando base_calculo, e o lote manda { modelo_id, base_calculo,
// percentual } (o % que a tela mostrou). Recusada no mês: o campo volta com a
// base da recusada (mesmo nº de DPS, mesma base), não com a sugerida.
//
// % da empresa (29/09, Eduardo: "a porcentagem de cada empresa que temos"): a
// nota fixa sem % própria usa a % padrão da empresa que emite (Cadastros ›
// Empresas) e a linha mostra "0,5% (da empresa) de [base]". A tela resolve na
// mesma ordem do servidor (pctDoModelo) e manda esse % na prévia e na emissão:
// o que foi conferido é o que sai. Sem % nenhuma, a prévia acusa a pendência.
//
// 01/10/2026 (Eduardo: "como virou o mês, o faturamento de outubro está zerado
// ainda… precisa ter a opção de eu escolher o mês, por exemplo setembro"): ao
// lado do mês, "Base do %: faturamento de [mês]" (NfseMesBase) — o mesmo mês da
// nota (padrão) ou um dos 3 anteriores. Só a BASE das notas de percentual muda:
// a nota continua com a competência do mês escolhido. Trocou o mês da base: carrega
// o faturamento daquele mês, refaz a prévia e as notas de % levam base_competencia.
// Trocou o mês da NOTA: a base volta para o mesmo mês (o padrão). A recusada que
// vai de novo leva a base E o mês da base gravados nela (mesDoCampo), qualquer
// que seja o seletor — para usar o faturamento do seletor, "voltar" no campo.
//
// 01/10/2026 (Eduardo: "quando vou gerar uma nova nota fixa, não aparece para qual
// mês eu quero gerar ela… quero a opção de escolher na hora"): a gaveta da nota
// fixa nova escolhe o mês e a "Base do %" e, ao salvar, a página traz para cá
// (tela.irParaEmitir). O pedido chega em tela.pedidoEmitir: a aba aplica a base,
// limpa filtro e busca (as novas têm de aparecer) e, quando a prévia daquele mês
// chega, marca as recém-criadas que podem sair. Nada é emitido sozinho.
import { computed, nextTick, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch, watchEffect } from 'vue'
import { PopoverContent, PopoverPortal, PopoverRoot, PopoverTrigger } from 'reka-ui'
import {
  AlertCircle, AlertTriangle, Building2, CalendarDays, CheckCircle2, ChevronDown, Eye, ExternalLink, FileCheck2,
  FileDown, HelpCircle, Info, Loader2, Pencil, RefreshCw, Repeat, RotateCcw, Search, SearchX,
  Settings2, Trash2, X,
} from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import {
  ambienteTexto, calcularPercentual, empresaTeste, erroApi, estadoLinha, explicarProblema, fmtBrl, fmtData, fmtDoc,
  fmtHora, fmtMes, fmtPct, fmtPctOrigem, mesAtual, mesBaseDaEmissao, mesParaData, origemDaEmissao, paraDecimal,
  opcoesMesBase, pctDoModelo, pctPositivo, plural, prestadorPorId, renderDescricao, situacao, textoFaturamento, textoIr, textoMesBase,
  TOM_TEXTO, tomadorDaEmissao, tomadorEstiloNfeio, tomadorNaNota, useNfseTela,
  type ChecklistItem, type Emissao, type EstadoLinha, type FaturamentoEmpresa, type ItemIn, type ItemLote,
  type ItemPrevia, type Modelo,
  type OrigemPct, type SecaoEmpresa,
  useNfseApi,
} from '~/lib/nfse'

const tela = useNfseTela()
// Com a chave da senha extra (a página entrega): useApi() direto volta nfse_locked.
const { api, abrirPdf } = useNfseApi()
const { mes, canEdit, canDelete, podeAbrirCadastroEmpresa } = tela

const hoje = mesAtual()

type Filtro = 'prontas' | 'ajuste' | 'emitidas' | 'conferir'
type Conserto =
  | { tipo: 'empresa'; foco?: SecaoEmpresa }
  | { tipo: 'cadastro'; href: string }
  | { tipo: 'tomador' }
  | { tipo: 'modelo' }

type Linha = {
  m: Modelo
  empresa: string
  tomador: string
  previa: ItemPrevia | null
  campo: string // o que está no campo (decimal): o valor ou, na nota de percentual, a base
  valor: string // o que vai na nota — ou o que foi, se já saiu (percentual: já calculado)
  pct: boolean // nota fixa de percentual (e ainda não saiu)
  base: string // percentual: a base (a do campo; ou a que foi, se já saiu) — '' = sem base
  percentual: string | null // percentual: "0.5000" (o da nota fixa ou da empresa; ou o que foi, se já saiu)
  origemPct: OrigemPct | null // de onde veio o % (o de agora; ou o gravado na nota que saiu): 'empresa' → "(da empresa)"
  formula: string | null // "0,5% de R$ 200.000,00" · "0,5% (da empresa) de R$ 200.000,00"
  mesBaseNota: string | null // já saiu com a base de outro mês: "faturamento de setembro/2026" (01/10)
  // Percentual que ainda não saiu: o mês do faturamento que vai junto da base do
  // campo ('AAAA-MM'). O do seletor "Base do %"; com a base da recusada, o mês dela.
  mesCampo: string
  daRecusada: boolean // o campo está com a base da nota recusada (nada digitado)
  semBase: boolean // percentual sem base digitada: não marca
  explicacao: string | undefined // troca a explicação padrão da situação
  estado: EstadoLinha
  emissao: Emissao | null
  anterior: Emissao | null
  marcavel: boolean
  travada: boolean // já saiu ou está saindo: o valor não muda mais
  idEmissao: string | null
  numero: string | null
  descricao: string
  rotulo: string | undefined
  sub: { texto: string; cor: string } | null
  conserto: Conserto | null
  naoConferida: boolean // o valor mudou e a prévia dele falhou: não sai até conferir
}

// Aviso que é da empresa (ex.: certificado vencendo): aparece uma vez, na
// linha da empresa, e não repetido embaixo de cada nota dela.
type AvisoEmpresa = { texto: string; curto: string; foco?: SecaoEmpresa }

type Grupo = {
  company_id: string
  empresa: string
  cnpj: string | null
  pronto: boolean
  nfeio: { ambiente: string | null; ligada: boolean }
  linhas: Linha[]
  marcaveis: Linha[]
  total: number
  avisos: AvisoEmpresa[]
}

const RANK: Record<EstadoLinha, number> = {
  carregando: 0, pronta: 0, pronta_aviso: 0, recusada_antes: 1, cancelada_antes: 2, pendencia: 3, valor_invalido: 3,
  incerta: 4, enviando: 4, processando: 4, cancelando: 4, emitida: 5,
}
const TRAVADAS = new Set<EstadoLinha>(['emitida', 'processando', 'incerta', 'enviando', 'cancelando'])
const PARA_CONFERIR = new Set<EstadoLinha>(['processando', 'incerta', 'enviando', 'cancelando'])
const AJUSTE = new Set<EstadoLinha>(['pendencia', 'valor_invalido'])

// --- Estado ------------------------------------------------------------------------

const previas = ref<Record<string, ItemPrevia>>({})
const previasMes = ref('') // de que mês é a prévia guardada ('' = nunca carregou)
const previasBase = ref('') // e com o faturamento de que mês (o mês da base)
const emissoes = ref<Emissao[]>([])
const emissoesMes = ref('')
const carregandoLista = ref(false)
const erro = ref<string | null>(null)
const conferidoEm = ref<Date | null>(null)
// O que foi digitado no campo, por mês e por nota fixa (vale enquanto a página
// está aberta): o valor ou, na nota de percentual, a base. Chave em chaveCampo().
const valores = ref<Record<string, Record<string, string>>>({})
const selecionados = ref<Set<string>>(new Set())
const filtro = ref<Filtro | null>(null)
const busca = ref('')
const expandidas = ref<Set<string>>(new Set())
const conferindoLinhas = ref<Set<string>>(new Set()) // prévia de 1 linha (valor mudou)
const falhaLinhas = ref<Set<string>>(new Set()) // a prévia do valor novo falhou
const conferindoNotas = ref<Set<string>>(new Set()) // "atualizar da NFE.io" em andamento
const emitindo = ref(false)

const ativos = computed(() => tela.modelos.value.filter((m) => m.ativo))

// Mês da base (01/10): a escolha vale só para o mês da nota em que foi feita —
// trocou o mês da nota, volta ao padrão (o faturamento do mesmo mês da nota).
// 01/10/2026: o ref mora na página (tela.baseEmitir) para a gaveta da nota fixa
// nova abrir com a mesma base e não apagá-la ao salvar.
const escolhaBase = tela.baseEmitir
const mesBase = computed(() => (escolhaBase.value.mes === mes.value ? escolhaBase.value.valor : ''))
const mesDaBase = computed(() => mesBase.value || mes.value) // 'AAAA-MM' que vale agora
const baseDeOutroMes = computed(() => mesDaBase.value !== mes.value)
// O seletor só aparece com nota de percentual (a de valor fixo não tem base).
const temPercentual = computed(() => ativos.value.some((m) => ehPct(m)))
// "faturamento do mês" · "faturamento de setembro/2026"
const rotuloFaturamento = computed(() =>
  baseDeOutroMes.value ? `faturamento de ${fmtMes(mesDaBase.value)}` : 'faturamento do mês',
)

// Pedido da gaveta da nota fixa nova (01/10): as recém-criadas a marcar quando a
// prévia daquele mês (e daquele mês da base) chegar. Ver tela.pedidoEmitir abaixo.
let paraMarcar: { mes: string; base: string; ids: Set<string> } | null = null

function escolherMesBase(v: string) {
  escolhaBase.value = { mes: mes.value, valor: v && v !== mes.value ? v : '' }
  paraMarcar = null // trocou a base à mão: o pedido da gaveta deixa de valer
}

const temPrevia = computed(() => previasMes.value === mes.value && previasBase.value === mesDaBase.value)
const colunas = computed(() => (canEdit.value ? 5 : 4))

// --- Carga ---------------------------------------------------------------------------

function ehPct(m: Modelo): boolean {
  return m.tipo_valor === 'percentual'
}

// A base e o valor ficam em chaves separadas: uma nota fixa que trocou de tipo
// não herda o número digitado no outro (R$ 1.500 de valor não vira base).
function chaveCampo(m: Modelo): string {
  return ehPct(m) ? `${m.id}:base` : m.id
}

// Faturamento do mês de cada empresa (base das notas de percentual — Eduardo,
// 30/09: "faz com base no faturamento já"), por mês → empresa. 01/10: o mês é o
// da BASE (mesDaBase), que pode ser anterior ao da nota.
const faturamentos = ref<Record<string, Record<string, FaturamentoEmpresa>>>({})

function faturamentoDe(m: Modelo, mb = mesDaBase.value): FaturamentoEmpresa | null {
  return faturamentos.value[mb]?.[m.company_id] ?? null
}

// A base que vem do faturamento ('' = sem loja ou sem venda no mês da base).
function baseFaturamento(m: Modelo, mb = mesDaBase.value): string {
  return positivo(faturamentoDe(m, mb)?.valor)
}

// O que está no campo: o digitado nesta página; senão, no percentual, a base da
// nota recusada do mês (ela vai de novo com a mesma base), o faturamento do mês
// da base ou, sem venda, a base sugerida; no valor fixo, o valor da nota fixa.
function campoDe(m: Modelo, mm = mes.value, mb = mesDaBase.value): string {
  const digitado = valores.value[mm]?.[chaveCampo(m)]
  if (digitado != null) return digitado
  if (!ehPct(m)) return m.valor ?? ''
  return (mm === mes.value ? baseRecusada(m) : '') || baseFaturamento(m, mb) || m.base_padrao || ''
}

// Percentual: a última nota recusada deste mês (null = não tem).
function recusadaDe(m: Modelo): Emissao | null {
  if (!ehPct(m)) return null
  return (emissoesPorModelo.value[m.id] ?? []).find((e) => e.status === 'rejeitada') ?? null
}

// Percentual: a base da última nota recusada deste mês ('' = não tem).
function baseRecusada(m: Modelo): string {
  return positivo(recusadaDe(m)?.base_calculo)
}

// O campo está com a base da recusada: nada digitado nesta página (campoDe).
function campoDaRecusada(m: Modelo, mm = mes.value): boolean {
  return mm === mes.value && valores.value[mm]?.[chaveCampo(m)] == null && !!baseRecusada(m)
}

// O mês do faturamento que vai junto da base do campo (revisão de 01/10): a base
// da recusada vai de novo com o mês GRAVADO nela (base de setembro numa nota de
// outubro continua "faturamento de setembro", mesmo com o seletor no padrão
// depois de recarregar; gravada antes de 01/10 = o mês da nota). A digitada, a do
// faturamento e a sugerida vão com o mês do seletor "Base do %".
function mesDoCampo(m: Modelo, mm = mes.value, mb = mesDaBase.value): string {
  return campoDaRecusada(m, mm) ? mesBaseDaEmissao(recusadaDe(m)) ?? mm : mb
}

// Decimal da API maior que zero, ou ''.
function positivo(v: string | null | undefined): string {
  const d = paraDecimal(v)
  return d && Number(d) > 0 ? d : ''
}

// O % da nota de percentual e de onde vem: o da nota fixa; sem ele, o da empresa
// que emite (a mesma ordem do servidor). Se a lista de empresas não tiver a
// empresa, vale o que a prévia do servidor resolveu.
// A prévia foi feita com ESTE mesmo % e o servidor respondeu 'item': a % da
// empresa (ou da nota fixa) mudou depois que a tela carregou (outra pessoa, por
// exemplo). A nota sai com este %, mas não é mais "o da empresa": o rótulo sai.
// Se a prévia é de outro % (ainda refazendo), vale o da tela.
function pctDaLinha(m: Modelo, previa?: ItemPrevia | null): { pct: string | null; origem: OrigemPct | null } {
  const local = pctDoModelo(m, tela.prestadores.value)
  const doServidor = pctPositivo(previa?.percentual)
  if (local.pct) {
    const mesmoPct = !!doServidor && Number(doServidor) === Number(local.pct)
    return mesmoPct && previa?.percentual_origem === 'item' ? { pct: local.pct, origem: 'item' } : local
  }
  return doServidor ? { pct: doServidor, origem: previa?.percentual_origem ?? null } : local
}

// O que vai na nota: o valor digitado; no percentual, base × % ÷ 100 (a
// mesma conta do servidor, arredondada no centavo).
function valorDaNota(m: Modelo, campo: string, pct: string | null): string {
  return ehPct(m) ? calcularPercentual(positivo(campo), pct) : campo
}

// O item da prévia com o que está no campo (vazio = o servidor usa o da nota fixa).
// Percentual: vai também o % que a tela mostra (o da nota fixa ou o da empresa),
// para a conta do servidor ser a mesma da tela mesmo se a nota fixa ou a empresa
// mudarem em outra aba — e o mês da base, se não for o da nota (mesmo sem base
// no campo: aí o servidor usa o faturamento DAQUELE mês). `mb` = o mês que vai
// junto DESTE campo (mesDoCampo: o da recusada quando a base é dela).
function itemDoCampo(m: Modelo, campo: string, mm = mes.value, mb = mesDoCampo(m, mm)): ItemIn {
  const it: ItemIn = { modelo_id: m.id }
  if (!ehPct(m)) {
    if (campo) it.valor = campo
    return it
  }
  if (mb !== mm) it.base_competencia = mesParaData(mb)
  if (!campo) return it
  it.base_calculo = campo
  const pct = pctDaLinha(m).pct
  if (pct) it.percentual = pct
  return it
}

function itemPrevia(m: Modelo, mm: string, mb: string): ItemIn {
  return itemDoCampo(m, positivo(campoDe(m, mm, mb)), mm, mesDoCampo(m, mm, mb))
}

async function preverTodos(mm: string, mb: string, lista: Modelo[]): Promise<ItemPrevia[]> {
  if (!lista.length) return []
  // A API aceita até 200 itens por prévia.
  const partes: Modelo[][] = []
  for (let i = 0; i < lista.length; i += 200) partes.push(lista.slice(i, i + 200))
  const respostas = await Promise.all(
    partes.map((p) =>
      api<{ itens: ItemPrevia[] }>('/api/nfse/previa', {
        method: 'POST',
        body: { competencia: mesParaData(mm), itens: p.map((m) => itemPrevia(m, mm, mb)) },
      }),
    ),
  )
  return respostas.flatMap((r) => r.itens)
}

let seqCarga = 0

async function carregar() {
  const mm = mes.value
  const mb = mesDaBase.value
  const lista = ativos.value
  const minha = ++seqCarga
  carregandoLista.value = true
  try {
    // O faturamento (do mês da BASE) vem antes: é ele que enche a base das notas de percentual.
    const fat = await api<{ empresas: FaturamentoEmpresa[] }>(
      `/api/nfse/faturamento?competencia=${mesParaData(mb)}`,
    ).catch(() => null)
    if (minha !== seqCarga || mm !== mes.value || mb !== mesDaBase.value) return
    if (fat) {
      faturamentos.value = {
        ...faturamentos.value,
        [mb]: Object.fromEntries(fat.empresas.map((e) => [e.company_id, e])),
      }
    }
    const [itens, ems] = await Promise.all([
      preverTodos(mm, mb, lista),
      api<Emissao[]>(`/api/nfse/emissoes?competencia=${mesParaData(mm)}`),
    ])
    // Resposta velha (outro mês, outro mês da base, ou outra carga começou depois): descarta.
    if (minha !== seqCarga || mm !== mes.value || mb !== mesDaBase.value) return
    const mapa: Record<string, ItemPrevia> = {}
    lista.forEach((m, i) => {
      const it = itens[i]
      if (it) mapa[m.id] = it
    })
    previas.value = mapa
    previasMes.value = mm
    previasBase.value = mb
    emissoes.value = ems
    emissoesMes.value = mm
    erro.value = null
    falhaLinhas.value = new Set()
    conferidoEm.value = new Date()
    podarSelecao()
    marcarPedidas(mm, mb, lista)
    reconferirRecusadas(mm, mb, lista, mapa)
  } catch (e) {
    if (minha !== seqCarga) return
    // A carga que ia marcar as recém-criadas falhou: o pedido some (não marca nada
    // mais tarde, sem ligação com a gaveta).
    if (paraMarcar && paraMarcar.mes === mm && paraMarcar.base === mb) paraMarcar = null
    erro.value = erroApi(e)
  } finally {
    if (minha === seqCarga) carregandoLista.value = false
  }
}

// Com a aba escondida (KeepAlive), só anota que precisa recarregar.
let ativa = true
let pendente = false

function pedirCarga() {
  if (ativa) carregar()
  else pendente = true
}

onMounted(() => {
  if (tela.carregado.value) carregar()
})
onActivated(() => {
  ativa = true
  if (pendente) {
    pendente = false
    carregar()
  }
})
onDeactivated(() => {
  ativa = false
})

watch(() => tela.versao.value, pedirCarga)
watch(mes, (m) => {
  // A base volta para o mesmo mês da nota — menos quando quem trocou o mês já
  // escolheu a base junto (a gaveta da nota fixa nova, 01/10).
  if (escolhaBase.value.mes !== m) escolhaBase.value = { mes: '', valor: '' }
  // Trocou para outro mês que não o do pedido da gaveta: o pedido deixa de valer.
  if (paraMarcar && paraMarcar.mes !== m) paraMarcar = null
  selecionados.value = new Set()
  expandidas.value = new Set()
  falhaLinhas.value = new Set()
})
// Mudou o mês da nota ou o da base: uma carga só (os dois mudam juntos ao trocar o mês).
watch(() => `${mes.value}|${mesDaBase.value}`, (_novo, velho) => {
  if (velho?.startsWith(`${mes.value}|`)) {
    // Só o mês da base mudou: as notas de % marcadas saem da seleção (o valor delas
    // muda) e a conferência da base nova que falhou deixa de valer.
    const pct = new Set(ativos.value.filter((m) => ehPct(m)).map((m) => m.id))
    selecionados.value = new Set([...selecionados.value].filter((id) => !pct.has(id)))
    falhaLinhas.value = new Set([...falhaLinhas.value].filter((id) => !pct.has(id)))
  }
  pedirCarga()
})

// Pedido da gaveta da nota fixa nova (01/10): o mês já veio trocado pela página.
// `paraMarcar` (declarado lá em cima) espera a prévia daquele mês e base chegar.
watch(
  () => tela.pedidoEmitir.value,
  (p) => {
    if (!p) return
    tela.pedidoEmitir.value = null
    if (emitindo.value) return
    if (p.mesBase !== undefined) {
      const ok = !!p.mesBase && p.mesBase !== p.mes && opcoesMesBase(p.mes).some((o) => o.valor === p.mesBase)
      escolhaBase.value = { mes: p.mes, valor: ok ? p.mesBase : '' }
    }
    filtro.value = null
    busca.value = ''
    // mes.value já é p.mes (a página trocou antes): mesDaBase já é o que vai valer.
    paraMarcar = p.marcar?.length ? { mes: p.mes, base: mesDaBase.value, ids: new Set(p.marcar) } : null
    // Mesmo mês e mesma base: nenhuma carga nova foi pedida pela troca. Se nenhuma
    // está em andamento (a do recarregar() da gaveta), pede uma para marcar.
    nextTick(() => {
      if (paraMarcar && !carregandoLista.value) pedirCarga()
    })
  },
  { immediate: true },
)

// Marca as recém-criadas que podem sair (empresa pronta, valor conferido). As
// que não podem ficam na lista, com o que falta, sem marcar. O pedido vale para
// UMA carga: a primeira que termina com as notas novas (outro mês/base = some).
function marcarPedidas(mm: string, mb: string, lista: Modelo[]) {
  const p = paraMarcar
  if (!p) return
  if (p.mes !== mm || p.base !== mb) {
    paraMarcar = null
    return
  }
  // Carga que começou antes de as notas novas chegarem: espera a próxima.
  if (!lista.some((m) => p.ids.has(m.id))) return
  paraMarcar = null
  if (!canEdit.value || emitindo.value) return
  const s = new Set(selecionados.value)
  for (const l of linhas.value) if (p.ids.has(l.m.id) && l.marcavel) s.add(l.m.id)
  if (s.size !== selecionados.value.size) selecionados.value = s
}

function conferirDeNovo() {
  // Recarrega a tela toda (empresas, tomadores, notas fixas) e, com a versão
  // nova, a prévia de todas as notas.
  tela.recarregar()
}

// --- Valor (ou base) digitado → prévia só daquela linha --------------------------------

const timersLinha = new Map<string, ReturnType<typeof setTimeout>>()

function mudarCampo(m: Modelo, v: string) {
  const mm = mes.value
  const mb = mesDaBase.value
  valores.value = { ...valores.value, [mm]: { ...(valores.value[mm] ?? {}), [chaveCampo(m)]: v } }
  const t = timersLinha.get(m.id)
  if (t) clearTimeout(t)
  timersLinha.delete(m.id)
  falhaLinhas.value.delete(m.id)
  const d = positivo(v)
  if (!d) {
    conferindoLinhas.value.delete(m.id)
    return
  }
  conferindoLinhas.value.add(m.id)
  timersLinha.set(
    m.id,
    setTimeout(() => preverLinha(m, mm, mb, d), 600),
  )
}

// `campo` = o valor ou, na nota de percentual, a base (vai como base_calculo).
// `mb` = o mês do seletor "Base do %" quando o campo mudou (01/10); o mês que vai
// junto do campo é o mesDoCampo (o da recusada, se a base é dela).
async function preverLinha(m: Modelo, mm: string, mb: string, campo: string) {
  timersLinha.delete(m.id)
  const mc = mesDoCampo(m, mm, mb)
  const mesmo = () =>
    mm === mes.value && mb === mesDaBase.value && positivo(campoDe(m, mm, mb)) === campo && mesDoCampo(m, mm, mb) === mc
  try {
    const r = await api<{ itens: ItemPrevia[] }>('/api/nfse/previa', {
      method: 'POST',
      body: { competencia: mesParaData(mm), itens: [itemDoCampo(m, campo, mm, mc)] },
    })
    const it = r.itens[0]
    // Só vale se ainda é o mesmo mês (e mês da base) e o campo não mudou de novo.
    if (it && mesmo() && previasMes.value === mm && previasBase.value === mb) {
      previas.value = { ...previas.value, [m.id]: it }
      falhaLinhas.value.delete(m.id)
    }
  } catch {
    // A conferência anterior é de OUTRO valor: a linha não pode sair com ela.
    // Fica "não conferida" (sem marcar) até conferir de novo.
    if (mesmo()) falhaLinhas.value.add(m.id)
  } finally {
    if (!timersLinha.has(m.id)) conferindoLinhas.value.delete(m.id)
  }
}

// A prévia de todas sai junto com a busca das notas do mês: a nota de percentual
// recusada foi conferida com a base sugerida (ou sem base). Agora que a base da
// recusada está no campo, confere de novo só essas linhas — também quando a base
// bate mas o mês dela não (01/10: a recusada vai com o mês gravado nela).
function reconferirRecusadas(mm: string, mb: string, lista: Modelo[], mapa: Record<string, ItemPrevia>) {
  for (const m of lista) {
    if (!ehPct(m) || valores.value[mm]?.[chaveCampo(m)] != null) continue
    const b = baseRecusada(m)
    if (!b) continue
    const p = mapa[m.id]
    if (positivo(p?.base_calculo) === b && (p?.base_competencia ?? '').slice(0, 7) === mesDoCampo(m, mm, mb)) continue
    conferindoLinhas.value.add(m.id)
    preverLinha(m, mm, mb, b)
  }
}

function reconferirLinha(l: Linha) {
  const d = positivo(l.campo)
  if (!d || conferindoLinhas.value.has(l.m.id)) return
  falhaLinhas.value.delete(l.m.id)
  conferindoLinhas.value.add(l.m.id)
  preverLinha(l.m, mes.value, mesDaBase.value, d)
}

onBeforeUnmount(() => {
  for (const t of timersLinha.values()) clearTimeout(t)
  timersLinha.clear()
})

// --- Linhas ----------------------------------------------------------------------------

const emissoesPorModelo = computed(() => {
  const mapa: Record<string, Emissao[]> = {}
  if (emissoesMes.value !== mes.value) return mapa
  for (const e of emissoes.value) if (e.modelo_id) (mapa[e.modelo_id] ||= []).push(e)
  return mapa
})

const avulsas = computed(() => (emissoesMes.value === mes.value ? emissoes.value.filter((e) => !e.modelo_id) : []))

function nomeEmpresa(companyId: string, reserva?: string | null): string {
  return prestadorPorId(tela.prestadores.value, companyId)?.apelido || reserva || 'Empresa'
}

// No estilo da lista da NFE.io (30/09): "61.989.102 LEOMAR ALVES ANTUNES".
function nomeTomador(m: Modelo, previa: ItemPrevia | null): string {
  if (previa?.tomador?.nome) return tomadorEstiloNfeio(previa.tomador.documento, previa.tomador.nome)
  const t = tela.tomadores.value.find((x) => x.id === m.tomador_id)
  const nn = tomadorNaNota(t)
  if (nn.nome) return tomadorEstiloNfeio(nn.doc, nn.nome)
  return m.tomador_nome || '—'
}

// O backend ainda fala "(aba Prestadores)" em alguns textos; a aba agora é
// "Empresas" e o botão de conserto já leva lá.
function textoLimpo(t: string): string {
  return explicarProblema(t).texto.replace(/\s*\(aba [^)]*\)/gi, '')
}

function temCodigos(m: Modelo): boolean {
  return !!(m.city_service_code || m.federal_service_code || m.c_nbs)
}

function avisoDaEmpresa(t: string): boolean {
  return explicarProblema(t).alvo === 'empresa'
}

// Os avisos que são desta nota (os da empresa vão na linha da empresa).
function avisosDaNota(previa: ItemPrevia | null): string[] {
  return (previa?.avisos ?? []).filter((t) => !avisoDaEmpresa(t))
}

function rotuloDe(estado: EstadoLinha, previa: ItemPrevia | null): string | undefined {
  if (estado === 'pronta_aviso') {
    const n = avisosDaNota(previa).length
    return n ? `Pronta · ${plural(n, 'aviso', 'avisos')}` : 'Pronta'
  }
  if (estado === 'recusada_antes') return 'Recusada antes · vai de novo'
  return undefined
}

function subDe(estado: EstadoLinha, previa: ItemPrevia | null, emissao: Emissao | null, anterior: Emissao | null) {
  const cinza = 'text-muted-foreground'
  switch (estado) {
    case 'pendencia': {
      const t = previa?.problemas[0]
      return t ? { texto: textoLimpo(t), cor: TOM_TEXTO.perigo } : null
    }
    case 'valor_invalido':
      return { texto: 'digite um valor maior que zero', cor: TOM_TEXTO.perigo }
    case 'recusada_antes': {
      const e = anterior?.erros?.[0]
      const t = e?.o_que_fazer || e?.descricao
      return t ? { texto: t, cor: cinza } : null
    }
    case 'processando':
      return { texto: 'esperando a prefeitura autorizar', cor: TOM_TEXTO.info }
    case 'incerta':
    case 'enviando':
      return { texto: 'não reenvie: atualize da NFE.io', cor: TOM_TEXTO.atencao }
    case 'cancelando':
      return { texto: 'atualize o cancelamento', cor: TOM_TEXTO.atencao }
    case 'pronta_aviso': {
      const t = avisosDaNota(previa)[0]
      return t ? { texto: textoLimpo(t), cor: cinza } : null
    }
    case 'cancelada_antes':
      return { texto: 'a anterior foi cancelada · sai uma nota nova', cor: cinza }
    case 'emitida':
      return emissao?.dh_emi ? { texto: `em ${fmtData(emissao.dh_emi)}`, cor: cinza } : null
    default:
      return null
  }
}

// --- Nota de percentual ------------------------------------------------------------------

// "Falta a base de cálculo" da prévia: na nota de percentual quem avisa é o
// próprio campo ("digite a base"), não o painel de pendências. E quem decide é
// a base que está no campo agora (a prévia pode ser de antes de digitar).
const RE_FALTA_BASE = /falta a base de c[áa]lculo/i

function previaDaLinha(m: Modelo, p: ItemPrevia | null): ItemPrevia | null {
  if (!p || !ehPct(m) || !p.problemas?.some((t) => RE_FALTA_BASE.test(t))) return p
  return { ...p, problemas: p.problemas.filter((t) => !RE_FALTA_BASE.test(t)) }
}

// "0,5% de R$ 200.000,00" (sem base: "0,5% da base"); com a % da empresa,
// "0,5% (da empresa) de R$ 200.000,00".
function formulaDe(pct: string | null | undefined, base: string, origem?: OrigemPct | null): string | null {
  if (!pct) return null
  const p = fmtPctOrigem(pct, origem)
  return base ? `${p} de ${fmtBrl(base)}` : `${p} da base`
}

// Percentual que ainda não saiu, com a base de outro mês que veio mesmo do
// faturamento dele (a prévia do servidor diz): "base: faturamento de setembro/2026".
function textoMesBaseCampo(l: Linha): string | undefined {
  const t = l.mesCampo !== mes.value ? textoMesBase(l.mesCampo, l.previa?.base_origem) : null
  return t ? `base: ${t}` : undefined
}

// O texto embaixo da base (percentual). Revisão de 01/10: com a base da recusada
// no campo, diz que é ela — e de que mês, se veio do faturamento — em vez do
// faturamento do mês do seletor ("faturamento de outubro/2026: R$ 0,00" embaixo
// de uma base de setembro). Senão: o faturamento do mês da base e, se o campo não
// bate com ele, "base trocada à mão".
function textoDaBase(l: Linha): string {
  if (l.daRecusada) {
    const rec = recusadaDe(l.m)
    const t = textoMesBase(mesBaseDaEmissao(rec) ?? mes.value, rec?.snapshot?.servico?.base_origem)
    return `base da nota recusada${t ? ` (${t})` : ''}`
  }
  const fat = textoFaturamento(faturamentoDe(l.m), fmtMes(mesDaBase.value))
  const doFat = baseFaturamento(l.m)
  return l.base && doFat && Number(l.base) !== Number(doFat) ? `${fat} — base trocada à mão` : fat
}

// {percentual} e {base} como o servidor escreve (sem base, "{base}" fica). O
// Intl põe espaço sem quebra depois do "R$"; na nota vai espaço comum.
function comValores(texto: string, m: Modelo, base: string, pct: string | null): string {
  let s = renderDescricao(texto, mes.value)
  if (!ehPct(m)) return s
  if (Number(pct) > 0) s = s.split('{percentual}').join(fmtPct(pct))
  if (base) s = s.split('{base}').join(fmtBrl(base).replace(/\u00a0/g, ' '))
  return s
}

// A descrição da prévia é a do servidor: vale se for da base que está no campo.
function descricaoDe(m: Modelo, previa: ItemPrevia | null, base: string, pct: string | null): string {
  const d = previa?.descricao
  if (d && (!ehPct(m) || positivo(previa?.base_calculo) === base)) return d
  return comValores(m.descricao, m, base, pct)
}

// 01/10: sem venda no mês da base (ex.: dia 1º, o mês ainda zerado), a saída
// pode ser o faturamento de um mês anterior — o seletor "Base do %" lá em cima.
const SEM_BASE =
  'Digite a base (o valor sobre o qual incide o %) ou escolha o faturamento de outro mês em "Base do %", lá em cima. O valor da nota é calculado sozinho.'

// Percentual com valor inválido: o que falta de verdade (base, % ou a conta).
function ajustePct(semBase: boolean, pct: string | null): { rotulo?: string; sub: Linha['sub']; explicacao: string } {
  if (semBase) {
    return {
      rotulo: 'Falta a base',
      sub: { texto: 'digite a base ou escolha outro mês em "Base do %"', cor: TOM_TEXTO.perigo },
      explicacao: SEM_BASE,
    }
  }
  if (!(Number(pct) > 0)) {
    return {
      rotulo: 'Falta a %',
      sub: { texto: 'sem % na nota fixa e na empresa', cor: TOM_TEXTO.perigo },
      explicacao: 'Edite a nota fixa e digite a %, ou cadastre a % padrão da empresa em Cadastros › Empresas.',
    }
  }
  return {
    sub: { texto: 'a conta dá menos de R$ 0,01: aumente a base', cor: TOM_TEXTO.perigo },
    explicacao: 'O valor calculado (base × percentual) fica abaixo de R$ 0,01. Aumente a base.',
  }
}

function consertoDe(m: Modelo, estado: EstadoLinha, previa: ItemPrevia | null): Conserto | null {
  if (!canEdit.value || estado !== 'pendencia') return null
  for (const t of previa?.problemas ?? []) {
    const p = explicarProblema(t, { modeloTemCodigos: temCodigos(m) })
    if (p.alvo === 'empresa') return p.foco ? { tipo: 'empresa', foco: p.foco } : { tipo: 'empresa' }
    if (p.alvo === 'cadastro' && podeAbrirCadastroEmpresa.value) return { tipo: 'cadastro', href: `/companies/${m.company_id}` }
    if (p.alvo === 'tomador' && tela.tomadores.value.some((x) => x.id === m.tomador_id)) return { tipo: 'tomador' }
    if (p.alvo === 'modelo') return { tipo: 'modelo' }
  }
  return null
}

const linhas = computed<Linha[]>(() =>
  ativos.value.map((m) => {
    const previa = previaDaLinha(m, temPrevia.value ? previas.value[m.id] ?? null : null)
    const campo = campoDe(m)
    const baseCampo = ehPct(m) ? positivo(campo) : ''
    const agora = ehPct(m) ? pctDaLinha(m, previa) : { pct: null, origem: null }
    const valorNovo = valorDaNota(m, campo, agora.pct)
    const r = estadoLinha({ previa, emissoes: emissoesPorModelo.value[m.id] ?? [], valor: valorNovo })
    const travada = TRAVADAS.has(r.estado)
    const pct = ehPct(m) && !travada
    // Já saiu: a conta é a da nota que saiu (a nota fixa ou a empresa podem ter mudado depois).
    const percentual = travada ? r.emissao?.percentual ?? null : pct ? agora.pct : null
    const origemPct = travada ? origemDaEmissao(r.emissao) : pct ? agora.origem : null
    const base = travada ? positivo(r.emissao?.base_calculo) : baseCampo
    // Já saiu com a base de outro mês (01/10): a linha diz de qual.
    const mesBaseNota = travada
      ? textoMesBase(mesBaseDaEmissao(r.emissao), r.emissao?.snapshot?.servico?.base_origem)
      : null
    const semBase = pct && !baseCampo
    const ajuste = pct && r.estado === 'valor_invalido' ? ajustePct(semBase, agora.pct) : null
    const idEmissao = r.emissao?.id ?? (travada ? previa?.ja_emitida?.id ?? null : null)
    const naoConferida = !travada && falhaLinhas.value.has(m.id)
    return {
      m,
      empresa: nomeEmpresa(m.company_id, m.prestador_nome),
      tomador: nomeTomador(m, previa),
      previa,
      campo,
      valor: travada ? r.emissao?.valor_servico ?? previa?.valor ?? valorNovo : valorNovo,
      pct,
      base,
      percentual,
      origemPct,
      formula: formulaDe(percentual, base, origemPct),
      mesBaseNota,
      mesCampo: mesDoCampo(m),
      daRecusada: pct && campoDaRecusada(m),
      semBase,
      explicacao: ajuste?.explicacao,
      estado: r.estado,
      emissao: r.emissao,
      anterior: r.anterior,
      marcavel: r.marcavel && !naoConferida,
      travada,
      idEmissao,
      numero: r.emissao?.n_nfse ?? (travada ? previa?.ja_emitida?.n_nfse ?? null : null),
      descricao: r.emissao?.descricao || descricaoDe(m, previa, baseCampo, agora.pct),
      rotulo: ajuste?.rotulo ?? rotuloDe(r.estado, previa),
      sub: naoConferida
        ? { texto: pct ? 'não deu para conferir a base nova' : 'não deu para conferir o valor novo', cor: TOM_TEXTO.perigo }
        : ajuste
          ? ajuste.sub
          : subDe(r.estado, previa, r.emissao, r.anterior),
      conserto: consertoDe(m, r.estado, previa),
      naoConferida,
    }
  }),
)

function ordemNoGrupo(a: Linha, b: Linha): number {
  return RANK[a.estado] - RANK[b.estado] || a.m.ordem - b.m.ordem || a.m.nome.localeCompare(b.m.nome, 'pt-BR')
}

// --- Resumo (StatCards) e filtros -------------------------------------------------------

const resumo = computed(() => {
  const r = { vTotal: 0, prontas: 0, vProntas: 0, ajuste: 0, emitidas: 0, vEmitidas: 0, conferir: 0 }
  for (const l of linhas.value) {
    const v = Number(paraDecimal(l.valor)) || 0
    r.vTotal += v
    if (l.marcavel) {
      r.prontas++
      r.vProntas += v
    }
    if (AJUSTE.has(l.estado)) r.ajuste++
    if (l.estado === 'emitida') {
      r.emitidas++
      r.vEmitidas += v
    }
    if (PARA_CONFERIR.has(l.estado)) r.conferir++
  }
  return r
})

// O contador da aba (notas prontas no mês). null = ainda não sabemos.
watchEffect(() => {
  tela.contadorEmitir.value = temPrevia.value ? resumo.value.prontas : null
})

type Card = {
  id: Filtro | null
  rotulo: string
  valor: string | number
  icone: typeof CalendarDays
  tom: 'default' | 'success' | 'warning' | 'danger'
  hint?: string
}

const cards = computed<Card[]>(() => {
  const r = resumo.value
  const ok = temPrevia.value
  const lista: Card[] = [
    {
      id: null,
      rotulo: 'Notas do mês',
      valor: tela.carregado.value ? ativos.value.length : '—',
      icone: CalendarDays,
      tom: 'default',
      hint: tela.carregado.value ? `${fmtBrl(r.vTotal)} no total` : undefined,
    },
    {
      id: 'prontas',
      rotulo: 'Prontas para emitir',
      valor: ok ? r.prontas : '—',
      icone: CheckCircle2,
      tom: 'success',
      hint: ok ? fmtBrl(r.vProntas) : undefined,
    },
    {
      id: 'ajuste',
      rotulo: 'Precisam de ajuste',
      valor: ok ? r.ajuste : '—',
      icone: AlertCircle,
      tom: 'danger',
      hint: 'resolva antes de emitir',
    },
    {
      id: 'emitidas',
      rotulo: 'Já emitidas',
      valor: ok ? r.emitidas : '—',
      icone: FileCheck2,
      tom: 'default',
      hint: ok ? `${fmtBrl(r.vEmitidas)} emitidos` : undefined,
    },
  ]
  if (ok && r.conferir > 0) {
    lista.push({
      id: 'conferir',
      rotulo: 'Para conferir',
      valor: r.conferir,
      icone: HelpCircle,
      tom: 'warning',
      hint: 'na prefeitura ou sem resposta',
    })
  }
  return lista
})

function clicarCard(id: Filtro | null) {
  filtro.value = id === null || filtro.value === id ? null : id
}

// O card "Para conferir" some quando zera: o filtro dele vai junto.
watch(
  () => resumo.value.conferir,
  (n) => {
    if (!n && filtro.value === 'conferir') filtro.value = null
  },
)

function passaFiltro(l: Linha): boolean {
  switch (filtro.value) {
    case 'prontas':
      return l.marcavel
    case 'ajuste':
      return AJUSTE.has(l.estado)
    case 'emitidas':
      return l.estado === 'emitida'
    case 'conferir':
      return PARA_CONFERIR.has(l.estado)
    default:
      return true
  }
}

function passaBusca(l: Linha): boolean {
  const q = busca.value.trim().toLowerCase()
  if (!q) return true
  return [l.m.nome, l.empresa, l.tomador].some((s) => s.toLowerCase().includes(q))
}

const visiveis = computed(() => linhas.value.filter((l) => passaFiltro(l) && passaBusca(l)))

function limparFiltros() {
  filtro.value = null
  busca.value = ''
}

function avisosDaEmpresa(ls: Linha[]): AvisoEmpresa[] {
  const vistos = new Map<string, AvisoEmpresa>()
  for (const l of ls) {
    if (l.travada) continue
    for (const t of l.previa?.avisos ?? []) {
      const p = explicarProblema(t)
      if (p.alvo !== 'empresa') continue
      const texto = textoLimpo(t)
      if (vistos.has(texto)) continue
      const curto = /certificado/i.test(texto)
        ? 'certificado vencendo'
        : /emissor nacional/i.test(texto)
          ? 'Emissor Nacional em 01/11'
          : texto
      vistos.set(texto, p.foco ? { texto, curto, foco: p.foco } : { texto, curto })
    }
  }
  return [...vistos.values()]
}

const grupos = computed<Grupo[]>(() => {
  const por = new Map<string, Linha[]>()
  for (const l of visiveis.value) {
    const a = por.get(l.m.company_id)
    if (a) a.push(l)
    else por.set(l.m.company_id, [l])
  }
  return [...por.entries()]
    .map(([cid, ls]) => {
      const p = prestadorPorId(tela.prestadores.value, cid)
      const ordenadas = [...ls].sort(ordemNoGrupo)
      return {
        company_id: cid,
        empresa: p?.apelido || ordenadas[0]?.empresa || 'Empresa',
        cnpj: p?.cnpj ?? null,
        pronto: p ? p.pronto : true,
        nfeio: { ambiente: p?.nfeio?.ambiente ?? null, ligada: !!p?.nfeio },
        linhas: ordenadas,
        marcaveis: ordenadas.filter((l) => l.marcavel),
        total: ordenadas.reduce((s, l) => s + (Number(paraDecimal(l.valor)) || 0), 0),
        avisos: avisosDaEmpresa(ordenadas),
      }
    })
    .sort(
      (a, b) =>
        Number(b.marcaveis.length > 0) - Number(a.marcaveis.length > 0) || a.empresa.localeCompare(b.empresa, 'pt-BR'),
    )
})

// --- Pendências / tudo emitido --------------------------------------------------------

const pendencias = computed(() =>
  linhas.value
    .filter((l) => l.estado === 'pendencia' && l.previa)
    .map((l) => ({ modelo: l.m, empresa: l.empresa, tomador: l.tomador, problemas: l.previa?.problemas ?? [] })),
)

const tudoEmitido = computed(
  () => temPrevia.value && linhas.value.length > 0 && linhas.value.every((l) => l.estado === 'emitida'),
)

// --- Seleção ----------------------------------------------------------------------------

function selecionada(l: Linha): boolean {
  return l.marcavel && selecionados.value.has(l.m.id)
}

function podeMarcar(l: Linha): boolean {
  return canEdit.value && l.marcavel && !emitindo.value
}

function alternar(l: Linha) {
  if (!podeMarcar(l)) return
  const s = new Set(selecionados.value)
  if (s.has(l.m.id)) s.delete(l.m.id)
  else s.add(l.m.id)
  selecionados.value = s
}

function marcacao(ls: Linha[]): 'todas' | 'algumas' | 'nenhuma' {
  const m = ls.filter((l) => l.marcavel)
  if (!m.length) return 'nenhuma'
  const n = m.filter((l) => selecionados.value.has(l.m.id)).length
  return n === 0 ? 'nenhuma' : n === m.length ? 'todas' : 'algumas'
}

// Marca todas as marcáveis da lista; se já estavam todas, desmarca.
function marcarVarias(ls: Linha[]) {
  if (emitindo.value) return
  const m = ls.filter((l) => l.marcavel)
  const todas = m.length > 0 && m.every((l) => selecionados.value.has(l.m.id))
  const s = new Set(selecionados.value)
  for (const l of m) {
    if (todas) s.delete(l.m.id)
    else s.add(l.m.id)
  }
  selecionados.value = s
}

function marcarTodasProntas() {
  selecionados.value = new Set(linhas.value.filter((l) => l.marcavel).map((l) => l.m.id))
}

function limparSelecao() {
  selecionados.value = new Set()
}

// Depois de cada recarga, sai da seleção o que deixou de poder sair.
function podarSelecao() {
  const pode = new Set(linhas.value.filter((l) => l.marcavel).map((l) => l.m.id))
  const s = new Set([...selecionados.value].filter((id) => pode.has(id)))
  if (s.size !== selecionados.value.size) selecionados.value = s
}

const marcadas = computed(() =>
  linhas.value
    .filter((l) => selecionada(l))
    .sort((a, b) => a.empresa.localeCompare(b.empresa, 'pt-BR') || ordemNoGrupo(a, b)),
)
const totalMarcadas = computed(() => marcadas.value.reduce((s, l) => s + (Number(paraDecimal(l.valor)) || 0), 0))
const empresasMarcadas = computed(() => new Set(marcadas.value.map((l) => l.m.company_id)).size)
const reenviosMarcadas = computed(() => marcadas.value.filter((l) => l.estado === 'recusada_antes').length)
// Das marcadas, as de empresa em Produção na NFE.io (sem saber = Produção: pede EMITIR).
const producaoMarcadas = computed(
  () =>
    marcadas.value.filter((l) => !(empresaTeste(prestadorPorId(tela.prestadores.value, l.m.company_id)) ?? l.previa?.teste ?? false))
      .length,
)

const MOTIVO_NAO_MARCA: Partial<Record<EstadoLinha, string>> = {
  emitida: 'Já emitida neste mês.',
  pendencia: 'Resolva a pendência antes de emitir.',
  valor_invalido: 'Digite um valor maior que zero.',
  processando: 'Na prefeitura: esperando a autorização.',
  incerta: 'Esperando a resposta da NFE.io.',
  enviando: 'Esperando a resposta da NFE.io.',
  cancelando: 'Esperando a prefeitura confirmar o cancelamento.',
  carregando: 'Conferindo…',
}

function motivoNaoMarca(l: Linha): string | null {
  if (l.marcavel) return null
  if (l.naoConferida) {
    return `Não deu para conferir ${l.pct ? 'a base nova' : 'o valor novo'}. Clique em "conferir de novo".`
  }
  if (l.semBase && l.estado === 'valor_invalido') return 'Digite a base (o valor sobre o qual incide o %).'
  if (l.explicacao) return l.explicacao
  return MOTIVO_NAO_MARCA[l.estado] ?? null
}

// Clicar na linha (fora de botões e campos) marca/desmarca.
function cliqueLinha(ev: MouseEvent, l: Linha) {
  if (!podeMarcar(l)) return
  const alvo = ev.target as HTMLElement | null
  if (alvo?.closest('button, a, input, textarea, select, label, [role="button"]')) return
  if (window.getSelection()?.toString()) return
  alternar(l)
}

function bordaAtencao(l: Linha): string {
  if (PARA_CONFERIR.has(l.estado)) return 'border-l-2 border-l-amber-500'
  if (l.estado === 'recusada_antes') return 'border-l-2 border-l-red-500'
  return ''
}

function alternarExpandida(id: string) {
  const s = new Set(expandidas.value)
  if (s.has(id)) s.delete(id)
  else s.add(id)
  expandidas.value = s
}

// --- Ações da linha ----------------------------------------------------------------------


function corrigir(l: Linha) {
  const c = l.conserto
  if (!c) return
  if (c.tipo === 'empresa') tela.abrirEmpresa(l.m.company_id, c.foco)
  else if (c.tipo === 'tomador') {
    const t = tela.tomadores.value.find((x) => x.id === l.m.tomador_id)
    if (t) tela.abrirTomador({ tomador: t })
  } else if (c.tipo === 'modelo') tela.abrirModelo({ modelo: l.m })
}

function hrefConserto(l: Linha): string | undefined {
  return l.conserto?.tipo === 'cadastro' ? l.conserto.href : undefined
}

function conferindoNota(l: Linha): boolean {
  return !!l.emissao && conferindoNotas.value.has(l.emissao.id)
}

async function conferir(l: Linha) {
  const e = l.emissao
  if (!e || conferindoNotas.value.has(e.id)) return
  conferindoNotas.value.add(e.id)
  try {
    await tela.conferir(e)
  } finally {
    conferindoNotas.value.delete(e.id)
  }
}

function verNota(l: Linha) {
  const alvo = l.emissao ?? (l.estado === 'emitida' ? l.idEmissao : null) ?? l.anterior
  if (alvo) tela.abrirNota(alvo)
}

function temNota(l: Linha): boolean {
  return !!(l.emissao || l.anterior || (l.estado === 'emitida' && l.idEmissao))
}

function editarModelo(l: Linha) {
  tela.abrirModelo({ modelo: l.m })
}

function contextoDe(l: Linha) {
  return {
    companyId: l.m.company_id,
    empresa: l.empresa,
    tomadorId: l.m.tomador_id,
    modeloId: l.m.id,
    modeloTemCodigos: temCodigos(l.m),
  }
}

function tituloPopover(l: Linha): string {
  return AJUSTE.has(l.estado) ? 'O que falta para esta nota sair' : 'Situação desta nota'
}

function problemasPopover(l: Linha): string[] {
  return l.estado === 'pendencia' ? l.previa?.problemas ?? [] : []
}

function errosPopover(l: Linha) {
  return l.estado === 'recusada_antes' ? l.anterior?.erros ?? null : null
}

// "Como a nota vai sair" (linha aberta).
function detalhes(l: Linha) {
  const p = l.previa
  const itens: { rotulo: string; valor: string; extra?: string; largo?: boolean }[] = [
    {
      rotulo: 'Empresa que emite',
      valor: p?.prestador?.nome || l.empresa,
      extra: p?.prestador?.cnpj ? fmtDoc(p.prestador.cnpj) : undefined,
    },
    {
      rotulo: 'Tomador',
      valor: l.tomador,
      extra: p?.tomador?.documento ? fmtDoc(p.tomador.documento) : undefined,
    },
    { rotulo: 'Código do serviço', valor: p?.city_service_code || '—' },
    { rotulo: 'Item da LC 116', valor: p?.federal_service_code || '—' },
    { rotulo: 'NBS', valor: p?.c_nbs || '—' },
    {
      rotulo: 'Ambiente na NFE.io',
      valor: p?.ambiente ? ambienteTexto(p.ambiente) : p?.teste ? 'Teste — nota simulada' : '—',
    },
  ]
  const ir = l.travada ? null : textoIr(p?.ir, p?.valor_liquido)
  if (ir) itens.push({ rotulo: 'IR retido', valor: ir, largo: true })
  for (const d of l.travada ? [] : p?.duplicadas_nfeio ?? []) {
    const partes = [
      d.numero ? `nº ${d.numero}` : 'sem número',
      d.valor != null && d.valor !== '' ? fmtBrl(d.valor) : null,
      d.emitida_em ? `em ${fmtData(d.emitida_em)}` : null,
    ].filter(Boolean)
    itens.push({ rotulo: 'Já existe na NFE.io (mesmo tomador, mesmo mês)', valor: partes.join(' · '), largo: true })
  }
  if (l.percentual) {
    const v = positivo(l.valor)
    itens.push({
      rotulo: 'Valor (percentual)',
      valor: l.base
        ? `${l.formula} = ${v ? fmtBrl(v) : 'menos de R$ 0,01'}`
        : `${fmtPctOrigem(l.percentual, l.origemPct)} da base: digite a base`,
      extra: l.mesBaseNota ?? (l.pct ? textoMesBaseCampo(l) : undefined),
      largo: true,
    })
  }
  itens.push({ rotulo: 'Descrição completa', valor: l.descricao || '—', largo: true })
  // Já saiu: o texto que foi na nota (a nota fixa pode ter mudado depois).
  const gravado = l.travada ? l.emissao?.snapshot?.servico?.inf_comp : undefined
  const infComp =
    gravado !== undefined ? gravado : l.m.inf_comp ? comValores(l.m.inf_comp, l.m, l.base, l.percentual) : null
  if (infComp) itens.push({ rotulo: 'Informações complementares', valor: infComp, largo: true })
  return itens
}

// --- Emitir --------------------------------------------------------------------------------

// Enquanto o valor novo de alguma linha está sendo conferido, a prévia dela
// ainda é a do valor antigo: não emite (o resumo do lote mentiria).
const conferindoValor = computed(() => conferindoLinhas.value.size > 0)

async function emitirMarcadas() {
  const ls = marcadas.value
  if (!ls.length || emitindo.value || conferindoValor.value) return
  const itens: ItemLote[] = ls.map((l) => {
    const comum = {
      chave: l.m.id,
      tipo: 'emitir' as const,
      company_id: l.m.company_id,
      empresa: l.empresa,
      tomador: l.tomador,
      titulo: l.m.nome,
      reenvio: l.estado === 'recusada_antes',
      avisos: l.previa?.avisos ?? [],
      teste: l.previa?.teste,
      ir: l.previa?.ir ?? null,
      valor_liquido: l.previa?.valor_liquido ?? null,
    }
    // Percentual: vão a base e o % que a tela mostrou; o servidor faz a conta
    // (o `valor` aqui é a mesma conta, para o lote mostrar e somar). Com o %
    // junto, a nota sai com o % conferido mesmo se a nota fixa mudar em outra aba.
    // 01/10: com a base de outro mês, vai base_competencia (a nota segue no mês dela).
    // O mês é o do campo (l.mesCampo): o do seletor ou, na recusada que vai de novo
    // com a base dela, o mês gravado nela. A origem é a que a prévia do servidor
    // deu para essa mesma base e mês (o lote só escreve "faturamento de setembro/2026"
    // quando a base veio mesmo de lá).
    if (l.pct) {
      const item: ItemIn = { modelo_id: l.m.id, base_calculo: l.base }
      if (l.percentual) item.percentual = l.percentual
      const outroMes = l.mesCampo !== mes.value
      if (outroMes) item.base_competencia = mesParaData(l.mesCampo)
      return {
        ...comum,
        valor: l.valor,
        base_calculo: l.base,
        percentual: l.percentual ?? undefined,
        percentual_origem: l.origemPct,
        ...(outroMes ? { base_competencia: l.mesCampo, base_origem: l.previa?.base_origem ?? null } : {}),
        item,
      }
    }
    const valor = paraDecimal(l.campo)
    return { ...comum, valor, item: { modelo_id: l.m.id, valor } }
  })
  emitindo.value = true
  try {
    const res = await tela.emitirLote({ competencia: mes.value, itens })
    if (!res.length) return // desistiu antes de começar: a seleção fica
    // As que não foram enviadas continuam marcadas, para tentar de novo.
    const s = new Set(selecionados.value)
    for (const r of res) if (r.estado !== 'nao_enviada') s.delete(r.chave)
    selecionados.value = s
  } finally {
    emitindo.value = false
  }
}

// --- Vazio: primeiros passos ------------------------------------------------------------------

const temEmpresaPronta = computed(() => tela.prestadores.value.some((p) => p.pronto))
const temTomador = computed(() => tela.tomadores.value.some((t) => t.ativo))
const mostrarPrimeirosPassos = computed(() => !temEmpresaPronta.value || !temTomador.value)

const primeirosPassos = computed<ChecklistItem[]>(() => {
  const emitiu = emissoes.value.some((e) => e.status === 'emitida')
  return [
    {
      chave: 'empresa',
      titulo: 'Ligar a empresa à NFE.io',
      ok: temEmpresaPronta.value,
      acao: temEmpresaPronta.value ? undefined : 'abrir Empresas',
    },
    {
      chave: 'tomador',
      titulo: 'Cadastrar quem recebe a nota',
      ok: temTomador.value,
      acao: temTomador.value ? undefined : 'cadastrar',
    },
    { chave: 'modelo', titulo: 'Criar a nota fixa', ok: false, acao: 'criar' },
    {
      chave: 'teste',
      titulo: 'Emitir a primeira nota',
      ok: emitiu,
      acao: emitiu ? undefined : 'nota avulsa',
    },
  ]
})

function irPasso(chave: string) {
  if (!canEdit.value) return
  if (chave === 'empresa') tela.irPara('empresas')
  else if (chave === 'tomador') tela.abrirTomador()
  else if (chave === 'modelo') tela.abrirModelo()
  else if (chave === 'teste') tela.abrirAvulsa({ competencia: mes.value })
}

// --- Estados da tela ----------------------------------------------------------------------------

// Esqueleto só na 1ª carga; recarregando, a lista fica e só o ícone gira.
const carregandoPrimeira = computed(
  () => !tela.carregado.value || (ativos.value.length > 0 && !previasMes.value && !erro.value),
)
const girando = computed(() => carregandoLista.value || tela.carregando.value)
</script>

<template>
  <section class="space-y-4">
    <!-- A) Barra do mês -->
    <div class="flex flex-wrap items-center gap-2">
      <NfseMesPicker v-model="mes" nome="Mês da nota" :max="hoje" />
      <NfseDica texto="O mês em que o serviço foi prestado. É ele que vai escrito na nota.">
        <button
          type="button"
          class="grid size-8 place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          aria-label="o que é o mês de competência"
        >
          <Info class="size-4" aria-hidden="true" />
        </button>
      </NfseDica>
      <!-- 01/10: de que mês vem o faturamento que vira a base das notas de % -->
      <NfseMesBase
        v-if="temPercentual"
        :model-value="mesBase"
        :competencia="mes"
        :disabled="emitindo"
        @update:model-value="escolherMesBase"
      />
      <div v-if="ativos.length > 10" class="relative w-full sm:w-64">
        <Search
          class="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
          aria-hidden="true"
        />
        <input
          v-model="busca"
          type="search"
          autocomplete="off"
          placeholder="buscar nota, empresa ou tomador…"
          aria-label="buscar nota, empresa ou tomador"
          class="h-9 w-full rounded-md border bg-background pl-8 text-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-search-cancel-button]:hidden"
          :class="busca ? 'pr-8' : 'pr-3'"
        />
        <button
          v-if="busca"
          type="button"
          class="absolute right-1.5 top-1/2 grid size-6 -translate-y-1/2 place-items-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
          aria-label="limpar busca"
          @click="busca = ''"
        >
          <X class="size-3.5" aria-hidden="true" />
        </button>
      </div>
      <div class="ml-auto flex items-center gap-2 text-xs text-muted-foreground">
        <span v-if="conferidoEm" class="tabular-nums">conferido às {{ fmtHora(conferidoEm) }}</span>
        <NfseDica texto="Confere de novo todas as notas do mês: empresa, tomador e o que já existe na NFE.io.">
          <Button size="sm" variant="ghost" :disabled="girando" @click="conferirDeNovo">
            <RotateCcw
              class="mr-1.5 size-4"
              :class="girando && 'animate-spin motion-reduce:animate-none'"
              aria-hidden="true"
            />
            conferir de novo
          </Button>
        </NfseDica>
      </div>
    </div>

    <NfseAviso v-if="!canEdit" tom="info" compacto>
      Você pode ver, mas não emitir. Peça acesso de edição em Emissão de Serviço.
    </NfseAviso>

    <!-- B) Resumo: cada card filtra a lista -->
    <div
      v-if="ativos.length || !tela.carregado.value"
      class="grid grid-cols-2 gap-2"
      :class="cards.length > 4 ? 'lg:grid-cols-5' : 'lg:grid-cols-4'"
    >
      <button
        v-for="c in cards"
        :key="c.rotulo"
        type="button"
        class="rounded-lg text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
        :class="c.id && filtro === c.id ? 'ring-2 ring-primary' : ''"
        :title="c.id && filtro === c.id ? 'tirar o filtro' : c.id ? `filtrar: ${c.rotulo}` : 'mostrar todas'"
        :aria-pressed="c.id ? filtro === c.id : undefined"
        @click="clicarCard(c.id)"
      >
        <StatCard class="h-full" :label="c.rotulo" :value="c.valor" :icon="c.icone" :tone="c.tom" :hint="c.hint" compact />
      </button>
    </div>

    <NfseAviso v-if="erro" tom="perigo" titulo="Não deu para conferir as notas do mês">
      {{ erro }}
      <template #acoes>
        <Button size="sm" variant="outline" class="text-foreground" :disabled="girando" @click="carregar">
          tentar de novo
        </Button>
      </template>
    </NfseAviso>

    <!-- C) Pendências (ou: tudo emitido) -->
    <NfseAviso v-if="tudoEmitido" tom="sucesso">
      Tudo certo: {{ linhas.length === 1 ? 'a nota' : `as ${linhas.length} notas` }} de {{ fmtMes(mes) }}
      {{ linhas.length === 1 ? 'já foi emitida' : 'já foram emitidas' }} ({{ fmtBrl(resumo.vEmitidas) }}).
      <template #acoes>
        <Button size="sm" variant="outline" class="text-foreground" @click="tela.irPara('notas')">
          ver notas emitidas
        </Button>
      </template>
    </NfseAviso>
    <NfseEmitirPendencias v-else-if="pendencias.length" :linhas="pendencias" />

    <!-- G) Estados + D) Lista -->
    <NfseSkeletonTabela v-if="carregandoPrimeira" :linhas="6" :colunas="5" />

    <EmptyState
      v-else-if="!ativos.length"
      :icon="Repeat"
      title="Nenhuma nota fixa ainda"
      description="Cadastre as notas que saem todo mês (quem emite, quem recebe, descrição e valor). Depois é só marcar e emitir aqui."
    >
      <!-- 01/10/2026: "nova nota fixa" e "nota avulsa" ficam só no topo da página
           (Eduardo: "precisa ir lá pra cima do lado de nota avulsa"). -->
      <p v-if="canEdit" class="text-sm text-muted-foreground">
        Use <span class="font-medium text-foreground">+ nova nota fixa</span> lá em cima, ao lado de
        <span class="font-medium text-foreground">nota avulsa</span>.
      </p>
      <div v-if="mostrarPrimeirosPassos" class="mx-auto mt-6 max-w-md rounded-lg border bg-background p-3 text-left">
        <NfseChecklist
          titulo="Primeiros passos"
          :itens="primeirosPassos"
          :disabled="!canEdit"
          @ir="irPasso"
          @acao="irPasso"
        />
      </div>
    </EmptyState>

    <!-- A prévia deste mês não veio: o aviso de erro acima já diz o que fazer. -->
    <template v-else-if="!temPrevia && erro" />

    <EmptyState
      v-else-if="!visiveis.length"
      :icon="SearchX"
      title="Nada com esses filtros"
      description="Nenhuma nota do mês bate com o resumo escolhido ou com a busca."
    >
      <Button size="sm" variant="outline" @click="limparFiltros">
        <X class="mr-1.5 size-4" aria-hidden="true" /> limpar filtros
      </Button>
    </EmptyState>

    <div v-else class="table-card tabela-nfse relative overflow-x-auto">
      <table class="w-full">
        <thead>
          <tr>
            <th v-if="canEdit" class="w-10">
              <input
                type="checkbox"
                class="size-4 rounded align-middle accent-primary disabled:opacity-40 dark:[color-scheme:dark]"
                :checked="marcacao(visiveis) === 'todas'"
                :indeterminate="marcacao(visiveis) === 'algumas'"
                :disabled="!visiveis.some((l) => l.marcavel) || emitindo"
                aria-label="marcar todas as prontas visíveis"
                title="marcar todas as prontas visíveis"
                @change="marcarVarias(visiveis)"
              />
            </th>
            <th>Nota</th>
            <th class="!text-right">Valor</th>
            <th>Situação</th>
            <th class="col-acoes w-px"><span class="sr-only">Ações</span></th>
          </tr>
        </thead>
        <tbody>
          <template v-for="g in grupos" :key="g.company_id">
            <!-- Linha da empresa -->
            <tr class="linha-grupo">
              <td v-if="canEdit" class="w-10 bg-muted/30">
                <input
                  type="checkbox"
                  class="size-4 rounded align-middle accent-primary disabled:opacity-40 dark:[color-scheme:dark]"
                  :checked="marcacao(g.linhas) === 'todas'"
                  :indeterminate="marcacao(g.linhas) === 'algumas'"
                  :disabled="!g.marcaveis.length || emitindo"
                  :aria-label="`marcar as prontas da ${g.empresa}`"
                  @change="marcarVarias(g.linhas)"
                />
              </td>
              <td colspan="2" class="bg-muted/30">
                <div class="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <Building2 class="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                  <span class="font-medium">{{ g.empresa }}</span>
                  <span v-if="g.cnpj" class="text-xs tabular-nums text-muted-foreground">{{ fmtDoc(g.cnpj) }}</span>
                  <NfseAmbienteBadge tamanho="sm" :ambiente="g.nfeio.ambiente" :ligada="g.nfeio.ligada" />
                  <span v-if="!g.pronto" class="pill-warning">empresa com pendência</span>
                  <NfseDica
                    v-for="a in g.avisos"
                    :key="a.texto"
                    :texto="canEdit ? `${a.texto} Não impede a emissão. Clique para ver.` : `${a.texto} Não impede a emissão.`"
                  >
                    <button
                      type="button"
                      class="pill-warning transition-colors enabled:hover:bg-amber-500/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-default"
                      :disabled="!canEdit"
                      @click="tela.abrirEmpresa(g.company_id, a.foco)"
                    >
                      <AlertTriangle class="size-3" aria-hidden="true" />
                      {{ a.curto }}
                    </button>
                  </NfseDica>
                  <span class="ml-auto whitespace-nowrap text-xs tabular-nums text-muted-foreground">
                    {{ plural(g.linhas.length, 'nota', 'notas') }} · {{ fmtBrl(g.total) }}
                  </span>
                </div>
              </td>
              <td class="bg-muted/30" />
              <td class="col-acoes text-right">
                <NfseDica :texto="`${g.empresa}: NFE.io e serviço prestado`">
                  <Button
                    variant="ghost"
                    size="icon"
                    class="size-8"
                    :aria-label="`${g.empresa}: NFE.io e serviço prestado`"
                    @click="tela.abrirEmpresa(g.company_id)"
                  >
                    <Settings2 class="size-4" aria-hidden="true" />
                  </Button>
                </NfseDica>
              </td>
            </tr>

            <template v-for="l in g.linhas" :key="l.m.id">
              <!-- Linha da nota -->
              <tr
                class="transition-colors"
                :class="[selecionada(l) && 'linha-marcada bg-primary/5', podeMarcar(l) && 'cursor-pointer']"
                @click="cliqueLinha($event, l)"
              >
                <td v-if="canEdit" class="w-10" :class="bordaAtencao(l)">
                  <NfseDica :texto="motivoNaoMarca(l)">
                    <span class="inline-flex align-middle">
                      <input
                        type="checkbox"
                        class="size-4 rounded accent-primary disabled:pointer-events-none disabled:opacity-40 dark:[color-scheme:dark]"
                        :checked="selecionada(l)"
                        :disabled="!l.marcavel || emitindo"
                        :aria-label="`marcar ${l.m.nome}`"
                        @change="alternar(l)"
                      />
                    </span>
                  </NfseDica>
                </td>

                <!-- w-full + max-w-0: a coluna Nota fica com o espaço que sobra e corta o texto longo -->
                <td class="w-full min-w-[10rem] max-w-0 xl:min-w-[14rem]" :class="!canEdit && bordaAtencao(l)">
                  <div class="truncate font-medium" :class="l.estado === 'emitida' && 'text-muted-foreground'">{{ l.m.nome }}</div>
                  <div
                    class="truncate text-xs text-muted-foreground"
                    :title="`para ${l.tomador} · ${l.descricao}`"
                  >
                    para {{ l.tomador }} · {{ l.descricao }}
                  </div>
                </td>

                <td class="whitespace-nowrap text-right align-middle">
                  <!-- Já saiu (ou está saindo): o valor não muda mais -->
                  <div v-if="l.travada" class="inline-flex flex-col items-end">
                    <span class="tabular-nums" :class="l.estado === 'emitida' && 'text-muted-foreground'">
                      {{ fmtBrl(l.valor) }}
                    </span>
                    <span v-if="l.formula" class="text-[11px] tabular-nums text-muted-foreground">{{ l.formula }}</span>
                    <span v-if="l.mesBaseNota" class="text-[11px] text-muted-foreground">{{ l.mesBaseNota }}</span>
                  </div>
                  <!-- Percentual: o valor da nota sai calculado; o campo é a Base (R$),
                       lido como "0,5% de [R$ 200.000,00]" -->
                  <div v-else-if="l.pct" class="inline-flex flex-col items-end">
                    <span
                      v-if="!l.semBase"
                      class="px-1.5 font-medium tabular-nums"
                      :class="l.estado === 'valor_invalido' && TOM_TEXTO.perigo"
                      aria-live="polite"
                    >
                      {{ positivo(l.valor) ? fmtBrl(l.valor) : l.percentual ? 'R$ 0,00' : '—' }}
                    </span>
                    <div
                      class="flex items-start gap-1"
                      role="group"
                      :aria-label="`Base (R$) de ${l.m.nome}: o valor sobre o qual incide o ${fmtPct(l.percentual)}`"
                    >
                      <span class="whitespace-nowrap pt-0.5 text-xs tabular-nums text-muted-foreground">
                        <!-- Sem % na nota fixa nem na empresa: a pendência explica ao lado -->
                        <NfseDica
                          v-if="!l.percentual"
                          texto="Falta a porcentagem: digite na nota fixa ou cadastre a % padrão da empresa em Cadastros › Empresas."
                        >
                          <span class="cursor-help font-medium" :class="TOM_TEXTO.perigo">?%</span>
                        </NfseDica>
                        <template v-else>{{ fmtPct(l.percentual) }}</template>{{ ' ' }}<NfseDica
                          v-if="l.origemPct === 'empresa'"
                          :texto="`A % padrão da ${l.empresa} (Cadastros › Empresas): esta nota fixa não tem % própria. Para usar outra % só nesta nota fixa: editar nota fixa › Outra %.`"
                        >
                          <span class="cursor-help underline decoration-dotted underline-offset-2">(da empresa)</span>
                        </NfseDica>
                        de
                      </span>
                      <!-- A base um tamanho abaixo do valor da nota (o valor é o que manda). -->
                      <NfseValorInput
                        class="[&_button]:text-xs"
                        compacto
                        :model-value="l.campo"
                        :padrao="baseFaturamento(l.m) || l.m.base_padrao"
                        rotulo="base"
                        :rotulo-padrao="baseFaturamento(l.m) ? rotuloFaturamento : 'base sugerida'"
                        placeholder="digite a base"
                        :invalido="l.semBase || l.estado === 'valor_invalido'"
                        :disabled="!canEdit || emitindo"
                        @update:model-value="mudarCampo(l.m, $event)"
                      />
                    </div>
                    <button
                      v-if="l.semBase && (baseFaturamento(l.m) || positivo(l.m.base_padrao)) && canEdit && !emitindo"
                      type="button"
                      class="rounded px-1.5 text-[11px] text-primary underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      @click.stop="mudarCampo(l.m, baseFaturamento(l.m) || positivo(l.m.base_padrao))"
                    >
                      {{ baseFaturamento(l.m) ? `usar o ${rotuloFaturamento} (${fmtBrl(baseFaturamento(l.m))})` : `usar a base sugerida (${fmtBrl(l.m.base_padrao)})` }}
                    </button>
                    <span v-if="l.pct" class="max-w-[280px] whitespace-normal text-right text-[11px] leading-tight text-muted-foreground">
                      {{ textoDaBase(l) }}
                    </span>
                  </div>
                  <NfseValorInput
                    v-else
                    compacto
                    :model-value="l.campo"
                    :padrao="l.m.valor"
                    :invalido="l.estado === 'valor_invalido'"
                    :disabled="!canEdit || emitindo"
                    @update:model-value="mudarCampo(l.m, $event)"
                  />
                </td>

                <td>
                  <PopoverRoot>
                    <PopoverTrigger as-child>
                      <button
                        type="button"
                        class="-mx-1 block max-w-[200px] rounded-md px-1 py-0.5 text-left transition-colors hover:bg-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring xl:max-w-[280px]"
                        :aria-label="`situação: ${l.rotulo ?? situacao(l.estado).rotulo}. Ver detalhes.`"
                      >
                        <span class="inline-flex items-center gap-1.5">
                          <NfseSituacao :estado="l.estado" :numero="l.numero" :rotulo="l.rotulo" :dica="false" />
                          <Loader2
                            v-if="conferindoLinhas.has(l.m.id)"
                            class="size-3 animate-spin text-muted-foreground motion-reduce:animate-none"
                            aria-label="conferindo o valor novo"
                          />
                        </span>
                        <span v-if="l.sub" class="mt-0.5 block truncate text-xs" :class="l.sub.cor">{{ l.sub.texto }}</span>
                      </button>
                    </PopoverTrigger>
                    <PopoverPortal>
                      <PopoverContent
                        side="bottom"
                        align="start"
                        :side-offset="4"
                        :collision-padding="8"
                        class="z-[80] w-[380px] max-w-[calc(100vw-16px)] space-y-2.5 rounded-md border bg-background p-3 text-sm shadow-lg duration-150 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=open]:fade-in-0 data-[state=closed]:fade-out-0 motion-reduce:animate-none"
                      >
                        <div class="space-y-0.5">
                          <p class="font-medium">{{ tituloPopover(l) }}</p>
                          <p class="text-xs text-muted-foreground">{{ l.explicacao ?? situacao(l.estado).explicacao }}</p>
                          <p v-if="l.formula" class="text-xs tabular-nums">
                            Valor: {{ l.formula }}{{ l.base ? ` = ${positivo(l.valor) ? fmtBrl(l.valor) : 'menos de R$ 0,01'}` : '' }}
                          </p>
                        </div>
                        <NfseProblemas
                          :problemas="problemasPopover(l)"
                          :avisos="l.previa?.avisos ?? []"
                          :erros="errosPopover(l)"
                          :contexto="contextoDe(l)"
                        />
                        <div class="flex flex-wrap gap-2 border-t pt-2.5">
                          <Button
                            v-if="canEdit && PARA_CONFERIR.has(l.estado) && l.emissao"
                            size="sm"
                            variant="outline"
                            class="h-8 px-2.5"
                            :disabled="conferindoNota(l)"
                            @click="conferir(l)"
                          >
                            <RefreshCw class="mr-1.5 size-4" aria-hidden="true" /> atualizar da NFE.io
                          </Button>
                          <Button
                            v-if="l.estado === 'emitida' && l.idEmissao"
                            size="sm"
                            variant="ghost"
                            class="h-8 px-2.5"
                            @click="abrirPdf(l.idEmissao)"
                          >
                            <FileDown class="mr-1.5 size-4" aria-hidden="true" /> PDF
                          </Button>
                          <Button v-if="temNota(l)" size="sm" variant="ghost" class="h-8 px-2.5" @click="verNota(l)">
                            <Eye class="mr-1.5 size-4" aria-hidden="true" />
                            {{ l.estado === 'recusada_antes' || l.estado === 'cancelada_antes' ? 'ver nota anterior' : 'ver nota' }}
                          </Button>
                          <Button v-if="canEdit" size="sm" variant="ghost" class="h-8 px-2.5" @click="editarModelo(l)">
                            <Pencil class="mr-1.5 size-4" aria-hidden="true" /> editar nota fixa
                          </Button>
                          <Button
                            v-if="canDelete"
                            size="sm"
                            variant="ghost"
                            class="h-8 px-2.5 text-red-700 hover:text-red-700 dark:text-red-400"
                            @click="tela.excluirModelo(l.m)"
                          >
                            <Trash2 class="mr-1.5 size-4" aria-hidden="true" /> excluir nota fixa
                          </Button>
                        </div>
                      </PopoverContent>
                    </PopoverPortal>
                  </PopoverRoot>
                </td>

                <td class="col-acoes w-px whitespace-nowrap text-right">
                  <div class="inline-flex items-center justify-end gap-1">
                    <Button
                      v-if="l.naoConferida && canEdit"
                      size="sm"
                      variant="outline"
                      class="h-8 px-2.5"
                      :disabled="conferindoLinhas.has(l.m.id)"
                      @click="reconferirLinha(l)"
                    >
                      <RotateCcw class="mr-1.5 size-4" aria-hidden="true" /> conferir de novo
                    </Button>
                    <template v-else-if="l.conserto">
                      <Button
                        v-if="l.conserto.tipo === 'cadastro'"
                        as="a"
                        :href="hrefConserto(l)"
                        target="_blank"
                        rel="noopener"
                        size="sm"
                        variant="outline"
                        class="h-8 px-2.5"
                      >
                        corrigir <ExternalLink class="ml-1 size-3.5" aria-hidden="true" />
                      </Button>
                      <Button v-else size="sm" variant="outline" class="h-8 px-2.5" @click="corrigir(l)">corrigir</Button>
                    </template>
                    <Button
                      v-else-if="canEdit && PARA_CONFERIR.has(l.estado) && l.emissao"
                      size="sm"
                      variant="outline"
                      class="h-8 px-2.5"
                      :disabled="conferindoNota(l)"
                      @click="conferir(l)"
                    >
                      <Loader2
                        v-if="conferindoNota(l)"
                        class="mr-1.5 size-4 animate-spin motion-reduce:animate-none"
                        aria-hidden="true"
                      />
                      <RefreshCw v-else class="mr-1.5 size-4" aria-hidden="true" />
                      atualizar
                    </Button>
                    <Button
                      v-else-if="l.estado === 'recusada_antes' && l.anterior"
                      size="sm"
                      variant="ghost"
                      class="h-8 px-2.5"
                      @click="verNota(l)"
                    >
                      ver motivo
                    </Button>
                    <Button
                      v-else-if="l.estado === 'emitida' && l.idEmissao"
                      size="sm"
                      variant="ghost"
                      class="h-8 px-2.5"
                      @click="abrirPdf(l.idEmissao)"
                    >
                      <FileDown class="mr-1.5 size-4" aria-hidden="true" /> PDF
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      class="size-8"
                      :aria-label="expandidas.has(l.m.id) ? 'fechar detalhes' : 'detalhes'"
                      :aria-expanded="expandidas.has(l.m.id)"
                      @click="alternarExpandida(l.m.id)"
                    >
                      <ChevronDown
                        class="size-4 transition-transform motion-reduce:transition-none"
                        :class="expandidas.has(l.m.id) && 'rotate-180'"
                        aria-hidden="true"
                      />
                    </Button>
                  </div>
                </td>
              </tr>

              <!-- Linha aberta: como a nota vai sair -->
              <tr v-if="expandidas.has(l.m.id)">
                <td :colspan="colunas" class="bg-muted/20 !px-4 !py-3" :class="canEdit && 'sm:!pl-[52px]'">
                  <p class="mb-2 text-xs font-medium text-muted-foreground">
                    {{ l.travada ? 'Como a nota saiu' : 'Como a nota vai sair' }}
                  </p>
                  <dl class="grid grid-cols-2 gap-x-6 gap-y-2 text-xs md:grid-cols-4">
                    <div v-for="d in detalhes(l)" :key="d.rotulo" :class="d.largo && 'col-span-full'">
                      <dt class="text-muted-foreground">{{ d.rotulo }}</dt>
                      <dd class="text-foreground" :class="d.largo && 'whitespace-pre-line'">{{ d.valor }}</dd>
                      <dd v-if="d.extra" class="tabular-nums text-muted-foreground">{{ d.extra }}</dd>
                    </div>
                  </dl>
                  <div v-if="canEdit || canDelete || temNota(l)" class="mt-3 flex flex-wrap gap-2">
                    <Button v-if="canEdit" size="sm" variant="ghost" class="h-8 px-2.5" @click="editarModelo(l)">
                      <Pencil class="mr-1.5 size-4" aria-hidden="true" /> editar nota fixa
                    </Button>
                    <Button
                      v-if="canDelete"
                      size="sm"
                      variant="ghost"
                      class="h-8 px-2.5 text-red-700 hover:text-red-700 dark:text-red-400"
                      @click="tela.excluirModelo(l.m)"
                    >
                      <Trash2 class="mr-1.5 size-4" aria-hidden="true" /> excluir nota fixa
                    </Button>
                    <Button v-if="temNota(l)" size="sm" variant="ghost" class="h-8 px-2.5" @click="verNota(l)">
                      <Eye class="mr-1.5 size-4" aria-hidden="true" />
                      {{ l.estado === 'recusada_antes' || l.estado === 'cancelada_antes' ? 'ver nota anterior' : 'ver nota' }}
                    </Button>
                  </div>
                </td>
              </tr>
            </template>
          </template>
        </tbody>
      </table>
    </div>

    <!-- E) Notas avulsas do mês -->
    <details v-if="avulsas.length" class="group rounded-lg border bg-card">
      <summary
        class="flex cursor-pointer list-none select-none items-center gap-2 px-4 py-2.5 text-sm font-medium hover:bg-muted/30 [&::-webkit-details-marker]:hidden"
      >
        <ChevronDown
          class="size-4 -rotate-90 text-muted-foreground transition-transform group-open:rotate-0 motion-reduce:transition-none"
          aria-hidden="true"
        />
        Notas avulsas de {{ fmtMes(mes) }}
        <span class="font-normal text-muted-foreground">({{ avulsas.length }})</span>
      </summary>
      <ul class="divide-y border-t">
        <li v-for="e in avulsas" :key="e.id" class="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-2 text-sm">
          <div class="min-w-0 flex-1">
            <div class="truncate">
              <span class="font-medium">{{ nomeEmpresa(e.company_id, e.prestador_nome) }}</span>
              <span class="text-muted-foreground"> → </span>{{ tomadorDaEmissao(e) }}
            </div>
            <div class="truncate text-xs text-muted-foreground" :title="e.descricao">{{ e.descricao }}</div>
          </div>
          <span class="inline-flex flex-col items-end">
            <span class="tabular-nums">{{ fmtBrl(e.valor_servico) }}</span>
            <span v-if="e.percentual" class="text-[11px] tabular-nums text-muted-foreground">
              {{ formulaDe(e.percentual, positivo(e.base_calculo), origemDaEmissao(e)) }}
            </span>
          </span>
          <NfseSituacao :estado="e.status" :numero="e.n_nfse" />
          <NfseAcoesNota :emissao="e" @abrir="tela.abrirNota(e)" />
        </li>
      </ul>
    </details>

    <!-- F) Barra fixa de ação -->
    <NfseEmitirBarra
      v-if="canEdit"
      :prontas="resumo.prontas"
      :total-prontas="resumo.vProntas"
      :marcadas="marcadas.length"
      :total-marcadas="totalMarcadas"
      :empresas="empresasMarcadas"
      :reenvios="reenviosMarcadas"
      :em-producao="producaoMarcadas"
      :ocupado="emitindo"
      :conferindo="conferindoValor"
      @emitir="emitirMarcadas"
      @limpar="limparSelecao"
      @marcar-todas="marcarTodasProntas"
    />
  </section>
</template>
