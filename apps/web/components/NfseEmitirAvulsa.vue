<script setup lang="ts">
// Nota avulsa (gaveta à direita): uma nota que não se repete todo mês.
// Montada uma vez na página; qualquer aba abre com tela.abrirAvulsa().
// A conferência é AUTOMÁTICA: com empresa, tomador, valor e descrição
// preenchidos, a prévia roda sozinha (600 ms depois da última mudança) e
// qualquer mudança invalida a anterior — nunca se emite com prévia velha.
// Emitir passa pelo mesmo assistente do lote (confirmação, EMITIR para empresa
// em Produção na NFE.io, acompanhamento "Na prefeitura…").
//
// Recusada: a saída é REENVIAR (a mesma nota) depois de corrigir a empresa ou o
// tomador. Emitir de novo daqui criaria OUTRA nota, e a recusada ficaria
// pendurada (reenviada depois = nota em dobro). Só sai nota nova se a pessoa
// mudar os dados da nota (o reenvio não muda valor, descrição, mês nem
// tomador) e confirmar.
//
// 29/09/2026 (motor NFE.io): a prévia mostra o IR retido e o valor líquido, e a
// empresa em Teste na NFE.io ganha o selo "TESTE — nota simulada".
//
// 29/09 (Eduardo: "quero emitir uma nota de serviço de 0,5%"): o valor pode
// ser FIXO ou PERCENTUAL. No percentual a pessoa digita a base e o %, o valor
// aparece calculado antes de emitir (base × % ÷ 100, a mesma conta do
// servidor) e a prévia/emissão mandam base_calculo + percentual (sem valor).
//
// % da empresa (29/09, Eduardo: "a porcentagem de cada empresa que temos"): se
// a empresa que emite tem % padrão (Cadastros › Empresas), o Percentual já vem
// em "Usar a % da empresa (0,5%)" — ou "Outra %" com o campo. A conta mostra a
// origem ("0,5% (da empresa) de R$ …") e a prévia/emissão mandam o % que a tela
// mostrou: o que foi conferido é o que sai. Sem % na empresa, o campo é
// obrigatório, como antes.
//
// 01/10/2026 (Eduardo: "como virou o mês, o faturamento de outubro está zerado
// ainda… precisa ter a opção de eu escolher o mês, por exemplo setembro"): no
// Percentual, "Base do %: faturamento de [mês]" (NfseMesBase) — o mesmo mês da
// nota (padrão) ou um dos 3 anteriores. A base vem cheia com o faturamento
// daquele mês e a prévia/emissão mandam base_competencia; a nota continua no mês
// de competência dela. Trocou o mês da nota: a base volta para o mesmo mês.
import { computed, nextTick, reactive, ref, watch } from 'vue'
import {
  AlertTriangle, Banknote, Building2, Calculator, CalendarDays, CheckCircle2, ExternalLink, FileText, FlaskConical, Info,
  Loader2, Percent, RefreshCw, RotateCw, Send, ShieldAlert, Undo2, UserRound, XCircle,
} from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import {
  calcularPercentual, erroApi, fmtBrl, fmtDoc, fmtMes, fmtPct, fmtPctOrigem, inserirNoCursor, itemReenvio, listaE,
  mesAtual, mesParaData, mesValido, minusculo, paraDecimal, pctPositivo, pendenciaTexto, prestadorPorId, TEXTO_TESTE,
  textoFaturamento, textoIr, textoMesBase, TOM_TEXTO, useNfseTela,
  type AvulsaApi, type Emissao, type FaturamentoEmpresa, type ItemIn, type ItemPrevia, type OrigemPct,
  type ResultadoLote,
  useNfseApi,
} from '~/lib/nfse'

const tela = useNfseTela()
// Com a chave da senha extra (a página entrega): useApi() direto volta nfse_locked.
const { api } = useNfseApi()

// Eduardo (30/09): "fixo = intermediação (sem mais detalhes), mas deixar opção de colocar".
const DESCRICAO_PADRAO = 'Intermediação'

type TipoValor = 'fixo' | 'percentual'
// Percentual: a % da empresa (se ela tiver) ou "outra %", a digitada aqui.
type ModoPct = 'empresa' | 'outra'
const OPCOES_TIPO: { id: TipoValor; rotulo: string; icone: typeof Banknote }[] = [
  { id: 'fixo', rotulo: 'Valor fixo', icone: Banknote },
  { id: 'percentual', rotulo: 'Percentual', icone: Percent },
]

// "0,5" · "0.5" · "0,5%" → { pct: '0.5' } (o que a API aceita: 0 < p <= 100,
// até 4 casas). Vazio → pct null, sem erro. Mesma leitura da nota fixa.
function lerPercentual(texto: string): { pct: string | null; erro: string | null } {
  let s = texto.trim().replace(/%/g, '').replace(/\s/g, '')
  if (!s) return { pct: null, erro: null }
  if (s.includes(',')) s = s.replace(/\./g, '').replace(',', '.')
  if (!/^\d*\.?\d*$/.test(s) || !/\d/.test(s)) return { pct: null, erro: 'Digite só o número do percentual. Ex.: 0,5' }
  if ((s.split('.')[1] ?? '').replace(/0+$/, '').length > 4) {
    return { pct: null, erro: 'Use no máximo 4 casas depois da vírgula.' }
  }
  const n = Number(s)
  if (!(n > 0)) return { pct: null, erro: 'O percentual precisa ser maior que zero.' }
  if (n > 100) return { pct: null, erro: 'O percentual vai até 100%.' }
  return { pct: n.toFixed(4).replace(/\.?0+$/, ''), erro: null }
}

const aberto = ref(false)
const form = reactive({
  competencia: mesAtual(),
  company_id: null as string | null,
  tomador_id: null as string | null,
  descricao: DESCRICAO_PADRAO,
  tipo_valor: 'fixo' as TipoValor,
  valor: '',
  base: '', // percentual: decimal da API ("200000.00")
  percentual: '', // percentual: como a pessoa digita ("0,5")
  modoPct: 'empresa' as ModoPct, // percentual: só vale se a empresa tiver % padrão
  inf_comp: '',
})
const previa = ref<ItemPrevia | null>(null)
const conferindo = ref(false)
const erroPrevia = ref<string | null>(null)
const emitindo = ref(false)
const emitida = ref(false)
// O assistente do lote (NfseEmitirLote) está aberto: a gaveta sai da frente e
// volta quando ele fecha, com tudo preenchido. Diálogo e gaveta ficam na mesma
// camada e a gaveta foi montada depois — por cima, ela esconderia o lote.
const noLote = ref(false)
const resultado = ref<ResultadoLote | null>(null)
// A NFE.io não respondeu (ou a conexão caiu): a nota pode ter saído. Emitir de
// novo daqui poderia gerar nota em dobro, então fica travado até fechar.
const travada = ref(false)
// A nota que foi recusada (e o formulário como estava quando ela saiu).
type Enviado = {
  competencia: string
  company_id: string | null
  tomador_id: string | null
  descricao: string
  tipo_valor: TipoValor
  valor: string
  valorDecimal: string
  base: string
  percentual: string
  modoPct: ModoPct
  pctApi: string | null // o % que valeu (o da empresa ou o digitado)
  mesBase: string // percentual: o mês da base ('' = o mês da nota)
  inf_comp: string
}
const recusada = ref<Emissao | null>(null)
const enviado = ref<Enviado | null>(null)
const acaoRecusada = ref<null | 'reenviar' | 'conferir'>(null)
const campoDescricao = ref<HTMLTextAreaElement | null>(null)
const hoje = mesAtual()

let resolver: (() => void) | null = null
let timer: ReturnType<typeof setTimeout> | null = null
let seq = 0

// --- Derivados ---------------------------------------------------------------------

const prestador = computed(() => prestadorPorId(tela.prestadores.value, form.company_id))
const tomador = computed(() => tela.tomadores.value.find((t) => t.id === form.tomador_id) ?? null)
const nomeTomador = computed(() => tomador.value?.nome_nota || tomador.value?.nome || '')
// Sem saber da ligação com a NFE.io (ou sem a chave), ninguém emite.
const semStatus = computed(
  () => !tela.status.value || !!tela.erroStatus.value || !tela.status.value.chave_configurada,
)
// Empresa em Teste na NFE.io (nota simulada)? null = não ligada.
const empresaTeste = computed(() => (prestador.value?.nfeio ? !!prestador.value.nfeio.teste : null))
// Empresa em Produção num servidor que só libera Teste: nem manda.
const producaoTravada = computed(
  () => empresaTeste.value === false && !!tela.status.value && !tela.status.value.producao_liberada,
)
const MOTIVO_SEM_STATUS = 'Não deu para conferir a ligação com a NFE.io.'
const MOTIVO_PRODUCAO = 'Esta empresa está em Produção na NFE.io e aqui só saem notas de empresas em Teste.'
const irTexto = computed(() => textoIr(previa.value?.ir, previa.value?.valor_liquido))

// --- Valor: fixo ou percentual ---------------------------------------------------------

const ehPct = computed(() => form.tipo_valor === 'percentual')
const pctLido = computed(() => lerPercentual(form.percentual))
const pctApi = computed(() => pctLido.value.pct)
// A % padrão da empresa escolhida ("0.5000"), ou null se ela não tiver.
const pctEmpresa = computed(() => pctPositivo(prestador.value?.percentual_servico))
const usaPctEmpresa = computed(() => ehPct.value && !!pctEmpresa.value && form.modoPct === 'empresa')
// O % que vale nesta nota (formato da API) e de onde ele vem.
const pctEfetivo = computed(() => (usaPctEmpresa.value ? pctEmpresa.value : pctApi.value))
// Com a prévia desta mesma base e deste mesmo % na mão, vale a origem que o
// servidor respondeu (é a que fica gravada na nota): se a % da empresa mudou
// depois que a tela carregou, o % que a tela manda já não é "o da empresa".
const origemPct = computed<OrigemPct | null>(() => {
  const p = previa.value
  if (
    p?.percentual_origem &&
    ehPct.value &&
    !!pctEfetivo.value &&
    Number(p.percentual) === Number(pctEfetivo.value) &&
    Number(p.base_calculo) === Number(baseDecimal.value)
  ) {
    return p.percentual_origem
  }
  return usaPctEmpresa.value ? 'empresa' : pctApi.value ? 'item' : null
})
// O erro do % só aparece depois de sair do campo: digitando "0,5", o "0" e o
// "0," no meio do caminho não ficam vermelhos (emitir já espera o % válido).
const pctTocado = ref(false)
const erroPct = computed(() => (pctTocado.value && !usaPctEmpresa.value ? pctLido.value.erro : null))

// O NfseOpcoes trabalha com string/número; aqui só existem os dois modos.
const modoPctModel = computed({
  get: () => form.modoPct as string,
  set: (v: string | number | null) => {
    form.modoPct = v === 'outra' ? 'outra' : 'empresa'
    // "Outra %": o campo aparece embaixo das opções; o cursor já vai para ele.
    if (v === 'outra') nextTick(() => document.getElementById('nfse-avulsa-percentual')?.focus())
  },
})

const opcoesPct = computed(() => [
  {
    valor: 'empresa',
    titulo: `Usar a % da empresa (${fmtPct(pctEmpresa.value)})`,
    descricao: prestador.value
      ? `É a % cadastrada na ${prestador.value.apelido} (Cadastros › Empresas).`
      : 'É a % cadastrada na empresa (Cadastros › Empresas).',
  },
  { valor: 'outra', titulo: 'Outra %', descricao: 'Só nesta nota.' },
])

// Empresa sem % padrão: o campo é obrigatório e a dica diz onde cadastrar a
// da empresa (quem só vê Cadastros › Empresas não consegue mudar lá).
const dicaPctSemEmpresa = computed(() => {
  const p = prestador.value
  if (!p) return 'Aceita vírgula: 0,5 = meio por cento.'
  const onde = tela.podeEditarCadastroEmpresa.value
    ? 'dá para cadastrar em Cadastros › Empresas'
    : 'quem cuida de Cadastros › Empresas pode cadastrar'
  return `Aceita vírgula: 0,5 = meio por cento. A ${p.apelido} não tem % padrão (${onde}).`
})

// Digitou uma % no campo (empresa sem % padrão): ela é "outra %". Se depois
// trocar para uma empresa com %, o que foi digitado continua valendo.
function aoDigitarPct() {
  if (form.percentual.trim()) form.modoPct = 'outra'
}
const baseDecimal = computed(() => {
  const d = paraDecimal(form.base)
  return d && Number(d) > 0 ? d : ''
})

// Mês da base (01/10): a escolha vale só para o mês da nota em que foi feita —
// trocou o mês da nota, volta ao padrão (o faturamento do mesmo mês).
const escolhaBase = ref<{ mes: string; valor: string }>({ mes: '', valor: '' })
const mesBase = computed(() => (escolhaBase.value.mes === form.competencia ? escolhaBase.value.valor : ''))
const mesDaBase = computed(() => mesBase.value || form.competencia)

function escolherMesBase(v: string) {
  escolhaBase.value = { mes: form.competencia, valor: v && v !== form.competencia ? v : '' }
}

// Trocou o mês da nota: a escolha é apagada, como no "Emitir do mês" (revisão de
// 01/10: sem isso, voltar ao mês trazia de volta o mês da base escolhido antes). O
// desfazer() grava o mês e a escolha juntos com o mesmo mês: esse não é apagado.
watch(
  () => form.competencia,
  (c) => {
    if (escolhaBase.value.mes !== c) escolhaBase.value = { mes: '', valor: '' }
  },
)

// "(faturamento de setembro/2026)" no "Tudo certo para emitir" — só com a base de
// outro mês que veio mesmo do faturamento dele (a prévia diz).
const textoMesDaBase = computed(() =>
  ehPct.value && mesBase.value ? textoMesBase(mesBase.value, previa.value?.base_origem) : null,
)

// Base = faturamento do mês da empresa (Eduardo, 30/09: "faz com base no
// faturamento já"): ao escolher empresa/mês no Percentual, a base vem cheia com
// ele. O que a pessoa digitar vale — só troca a base que ainda é a automática.
// 01/10: o mês é o da base (mesDaBase); num mês sem venda, a base automática de
// outro mês sai do campo (não fica um número que não é daquele mês).
const faturamento = ref<FaturamentoEmpresa | null>(null)
const faturamentoCarregado = ref(false)
const baseAutomatica = ref('')
let seqFat = 0
watch(
  () => [mesDaBase.value, form.company_id, form.tipo_valor] as const,
  async ([mm, cid, tipo]) => {
    const minha = ++seqFat
    faturamento.value = null
    faturamentoCarregado.value = false
    if (tipo !== 'percentual' || !cid || !mesValido(mm)) return
    const r = await api<{ empresas: FaturamentoEmpresa[] }>(
      `/api/nfse/faturamento?competencia=${mesParaData(mm)}`,
    ).catch(() => null)
    if (minha !== seqFat) return
    faturamento.value = r?.empresas.find((e) => e.company_id === cid) ?? null
    faturamentoCarregado.value = !!r
    const v = faturamento.value && Number(faturamento.value.valor) > 0 ? paraDecimal(faturamento.value.valor) : ''
    if (v && (!form.base || form.base === baseAutomatica.value)) {
      form.base = v
      baseAutomatica.value = v
    } else if (!v && r && baseAutomatica.value && form.base === baseAutomatica.value) {
      form.base = ''
      baseAutomatica.value = ''
    }
  },
)
// O que vai na nota: o digitado; no percentual, base × % ÷ 100.
const valorDecimal = computed(() =>
  ehPct.value ? calcularPercentual(baseDecimal.value, pctEfetivo.value) : paraDecimal(form.valor),
)
const valorOk = computed(() => !!valorDecimal.value && Number(valorDecimal.value) >= 0.01)
// "0,5% de R$ 200.000,00" · "0,5% (da empresa) de R$ 200.000,00" (só com base e % válidos).
const formula = computed(() =>
  ehPct.value && pctEfetivo.value && baseDecimal.value
    ? `${fmtPctOrigem(pctEfetivo.value, origemPct.value)} de ${fmtBrl(baseDecimal.value)}`
    : null,
)
// Base e % certos, mas a conta não chega a 1 centavo.
const contaPequena = computed(() => !!formula.value && !valorOk.value)
const camposFaltando = computed(
  () => `empresa, tomador, ${ehPct.value ? (usaPctEmpresa.value ? 'base' : 'base, percentual') : 'valor'} e descrição`,
)

function escolherTipo(v: string) {
  form.tipo_valor = v === 'percentual' ? 'percentual' : 'fixo'
}

// Ao sair do campo, o % fica como a pessoa escreveria ("0.50" → "0,5").
function ajustarPercentual() {
  pctTocado.value = true
  const p = pctApi.value
  if (p) form.percentual = Number(p).toLocaleString('pt-BR', { maximumFractionDigits: 4, useGrouping: false })
}

const completo = computed(
  () =>
    !!form.company_id &&
    !!form.tomador_id &&
    !!form.descricao.trim() &&
    valorOk.value &&
    mesValido(form.competencia),
)

const preenchida = computed(
  () =>
    !!form.company_id ||
    !!form.tomador_id ||
    !!form.valor ||
    !!form.base ||
    !!form.percentual.trim() ||
    !!form.inf_comp.trim() ||
    form.descricao !== DESCRICAO_PADRAO,
)

const problemas = computed(() => previa.value?.problemas ?? [])
const avisos = computed(() => previa.value?.avisos ?? [])
const ok = computed(() => !!previa.value && !problemas.value.length)

// O que mudou no formulário desde a nota recusada. O reenvio usa os dados da
// recusada: com qualquer mudança aqui, só uma nota nova leva os dados novos.
const camposMudados = computed<string[]>(() => {
  const e = enviado.value
  if (!recusada.value || !e) return []
  const out: string[] = []
  if (form.competencia !== e.competencia) out.push('o mês')
  if (form.company_id !== e.company_id) out.push('a empresa')
  if (form.tomador_id !== e.tomador_id) out.push('o tomador')
  if (form.tipo_valor !== e.tipo_valor) out.push('o tipo de valor')
  else if (!ehPct.value && valorDecimal.value !== e.valorDecimal) out.push('o valor')
  else if (ehPct.value) {
    if (baseDecimal.value !== paraDecimal(e.base)) out.push('a base')
    if (Number(pctEfetivo.value) !== Number(e.pctApi)) out.push('o percentual')
    if (form.competencia === e.competencia && mesBase.value !== e.mesBase) out.push('o mês da base')
  }
  if (form.descricao.trim() !== e.descricao.trim()) out.push('a descrição')
  if (form.inf_comp.trim() !== e.inf_comp.trim()) out.push('as informações complementares')
  return out
})
const mudou = computed(() => camposMudados.value.length > 0)

const contextoRecusada = computed(() => ({
  companyId: recusada.value?.company_id ?? null,
  empresa: prestadorPorId(tela.prestadores.value, recusada.value?.company_id)?.apelido ?? recusada.value?.prestador_nome ?? null,
  tomadorId: recusada.value?.tomador_id ?? null,
}))

const pendenciasEmpresa = computed(() => {
  const p = prestador.value
  if (!p || p.pronto) return []
  return p.pendencias.map((x) => ({ bruto: x, ...pendenciaTexto(x) }))
})

// O conserto da 1ª pendência: dados fiscais aqui mesmo ou o cadastro da empresa.
const consertoEmpresa = computed(() => {
  const p = prestador.value
  const x = pendenciasEmpresa.value[0]
  if (!p || !x || !tela.canEdit.value) return null
  if (x.alvo === 'empresa') return { tipo: 'empresa' as const, foco: x.foco }
  return tela.podeAbrirCadastroEmpresa.value ? { tipo: 'cadastro' as const, href: `/companies/${p.company_id}` } : null
})

const contexto = computed(() => ({
  companyId: form.company_id,
  empresa: prestador.value?.apelido ?? null,
  tomadorId: form.tomador_id,
}))

const podeEmitir = computed(
  () =>
    tela.canEdit.value &&
    completo.value &&
    ok.value &&
    !conferindo.value &&
    !emitindo.value &&
    !travada.value &&
    !semStatus.value &&
    !producaoTravada.value &&
    !acaoRecusada.value &&
    (!recusada.value || mudou.value),
)

const podeReenviar = computed(
  () =>
    tela.canEdit.value &&
    !!recusada.value &&
    !mudou.value &&
    ok.value &&
    !conferindo.value &&
    !emitindo.value &&
    !travada.value &&
    !semStatus.value &&
    !producaoTravada.value &&
    !acaoRecusada.value,
)

const motivoReenvio = computed<string | null>(() => {
  if (!tela.canEdit.value) return 'Você não tem permissão para reenviar.'
  if (semStatus.value) return MOTIVO_SEM_STATUS
  if (producaoTravada.value) return MOTIVO_PRODUCAO
  if (conferindo.value) return 'Espere a conferência terminar.'
  if (erroPrevia.value) return 'A conferência falhou: tente de novo.'
  if (problemas.value.length) return 'Resolva o que falta antes de reenviar.'
  return null
})

const textoPendencias = computed(() => listaE(pendenciasEmpresa.value.map((x) => minusculo(x.texto))))

const motivoBloqueio = computed<string | null>(() => {
  if (!tela.canEdit.value) return 'Você não tem permissão para emitir.'
  if (travada.value) return 'Confira esta nota em Notas enviadas antes de emitir outra.'
  if (semStatus.value) return MOTIVO_SEM_STATUS
  if (producaoTravada.value) return MOTIVO_PRODUCAO
  if (contaPequena.value) return 'O valor calculado fica abaixo de R$ 0,01.'
  if (!completo.value) return `Preencha ${camposFaltando.value}.`
  if (conferindo.value) return 'Espere a conferência terminar.'
  if (erroPrevia.value) return 'A conferência falhou: tente de novo.'
  if (problemas.value.length) return 'Resolva o que falta antes de emitir.'
  if (emitindo.value) return 'Emitindo…'
  return null
})

// --- Prévia automática ---------------------------------------------------------------

// Avulsa: OU valor, OU base + percentual (o servidor faz a conta).
function item(): ItemIn {
  const extra = form.inf_comp.trim()
  return {
    company_id: form.company_id ?? undefined,
    tomador_id: form.tomador_id ?? undefined,
    descricao: form.descricao.trim(),
    // Percentual: vai o % que a tela mostra (o da empresa ou o digitado) e, com
    // a base de outro mês, de que mês ela é (01/10).
    ...(ehPct.value
      ? {
          base_calculo: baseDecimal.value,
          percentual: pctEfetivo.value ?? undefined,
          ...(mesBase.value ? { base_competencia: mesParaData(mesBase.value) } : {}),
        }
      : { valor: valorDecimal.value }),
    ...(extra ? { inf_comp: extra } : {}),
  }
}

function limparTimer() {
  if (timer) clearTimeout(timer)
  timer = null
}

function agendar(ms = 600) {
  seq++
  limparTimer()
  previa.value = null
  erroPrevia.value = null
  if (!aberto.value || !completo.value) {
    conferindo.value = false
    return
  }
  conferindo.value = true
  const minha = seq
  timer = setTimeout(() => conferir(minha), ms)
}

async function conferir(minha: number) {
  timer = null
  try {
    const r = await api<{ itens: ItemPrevia[] }>('/api/nfse/previa', {
      method: 'POST',
      body: { competencia: mesParaData(form.competencia), itens: [item()] },
    })
    if (minha !== seq) return
    previa.value = r.itens[0] ?? null
  } catch (e) {
    if (minha !== seq) return
    erroPrevia.value = erroApi(e)
  } finally {
    if (minha === seq) conferindo.value = false
  }
}

// Mudou algo no formulário: o resultado da última tentativa não vale mais
// (menos a trava de "sem resposta") e a conferência recomeça.
watch(
  // O % entra já lido ("0,5" e "0,50" são o mesmo: não confere de novo).
  () => [
    form.competencia, form.company_id, form.tomador_id, form.descricao, form.tipo_valor, form.valor, form.base,
    pctEfetivo.value, mesDaBase.value, form.inf_comp,
  ],
  () => {
    if (!travada.value) resultado.value = null
    agendar()
  },
)

// Corrigiu a empresa/tomador numa gaveta por cima: confere de novo na hora.
watch(
  () => tela.versao.value,
  () => {
    if (aberto.value) agendar(0)
  },
)

// --- Abrir / fechar -------------------------------------------------------------------

function abrir(o?: { competencia?: string }): Promise<void> {
  resolver?.()
  resolver = null
  let c = (o?.competencia ?? '').slice(0, 7)
  if (!mesValido(c)) c = tela.mes.value
  if (!mesValido(c) || c > hoje) c = hoje
  form.competencia = c
  form.company_id = null
  form.tomador_id = null
  form.descricao = DESCRICAO_PADRAO
  form.tipo_valor = 'fixo'
  form.valor = ''
  form.base = ''
  baseAutomatica.value = ''
  form.percentual = ''
  form.modoPct = 'empresa'
  escolhaBase.value = { mes: '', valor: '' }
  pctTocado.value = false
  form.inf_comp = ''
  seq++
  limparTimer()
  previa.value = null
  erroPrevia.value = null
  conferindo.value = false
  resultado.value = null
  travada.value = false
  recusada.value = null
  enviado.value = null
  acaoRecusada.value = null
  emitida.value = false
  emitindo.value = false
  aberto.value = true
  return new Promise<void>((res) => {
    resolver = res
  })
}

function fechar() {
  aberto.value = false
  seq++
  limparTimer()
  conferindo.value = false
  const r = resolver
  resolver = null
  r?.()
}

function aoMudar(v: boolean) {
  if (!v) fechar()
}

// Com nota recusada, fechar não perde nada: ela fica em Notas enviadas.
const sujo = computed(() => preenchida.value && !emitida.value && !recusada.value)

// O botão "Cancelar" do rodapé pergunta igual ao X da gaveta.
async function cancelar() {
  if (sujo.value) {
    const sair = await tela.confirmar({
      titulo: 'Sair sem salvar?',
      texto: 'O que você preencheu vai se perder.',
      tom: 'perigo',
      botao: 'Sair sem salvar',
      voltar: 'Continuar editando',
    })
    if (!sair) return
  }
  fechar()
}

// --- Ações -------------------------------------------------------------------------------

function inserir(token: string) {
  form.descricao = inserirNoCursor(campoDescricao.value, form.descricao, token)
}

function corrigirEmpresa() {
  const c = consertoEmpresa.value
  if (c?.tipo === 'empresa' && form.company_id) tela.abrirEmpresa(form.company_id, c.foco)
}

function fotoDoFormulario(): Enviado {
  return {
    competencia: form.competencia,
    company_id: form.company_id,
    tomador_id: form.tomador_id,
    descricao: form.descricao,
    tipo_valor: form.tipo_valor,
    valor: form.valor,
    valorDecimal: valorDecimal.value,
    base: form.base,
    percentual: form.percentual,
    modoPct: form.modoPct,
    pctApi: pctEfetivo.value,
    mesBase: mesBase.value,
    inf_comp: form.inf_comp,
  }
}

// O que fazer com o resultado de uma tentativa (emissão nova ou reenvio).
function tratar(r: ResultadoLote | undefined, foto: Enviado | null) {
  if (!r) return // desistiu antes de começar
  // Emitida, ou já na prefeitura (a NFE.io recebeu; o DaVinci acompanha): pronto.
  if (r.estado === 'emitida' || r.estado === 'processando') {
    emitida.value = true
    fechar()
    return
  }
  resultado.value = r
  if (r.estado === 'incerta' || r.estado === 'desconhecido') {
    travada.value = true
    return
  }
  if (r.estado === 'rejeitada' && r.emissao) {
    recusada.value = r.emissao
    if (foto) enviado.value = foto
  }
}

async function emitir() {
  if (!podeEmitir.value || !previa.value || !form.company_id) return
  const velha = recusada.value
  if (velha) {
    const seguir = await tela.confirmar({
      tom: 'perigo',
      titulo: 'Emitir uma nota nova?',
      texto: 'Ela sai como outra nota. A recusada fica em Notas enviadas e não é usada.',
      linhas: ['Não reenvie a recusada depois: sairiam duas notas do mesmo serviço.'],
      botao: 'Emitir nota nova',
    })
    if (!seguir || !podeEmitir.value || !previa.value || !form.company_id) return
  }
  const p = previa.value
  const foto = fotoDoFormulario()
  emitindo.value = true
  resultado.value = null
  noLote.value = true
  try {
    const res = await tela.emitirLote({
      competencia: form.competencia,
      itens: [
        {
          chave: 'avulsa',
          tipo: 'emitir',
          company_id: form.company_id,
          empresa: prestador.value?.apelido || p.prestador?.nome || 'Empresa',
          tomador: p.tomador?.nome || nomeTomador.value,
          titulo: 'Nota avulsa',
          valor: valorDecimal.value,
          ...(ehPct.value
            ? {
                base_calculo: baseDecimal.value,
                percentual: pctEfetivo.value ?? undefined,
                percentual_origem: origemPct.value,
                ...(mesBase.value ? { base_competencia: mesBase.value, base_origem: p.base_origem ?? null } : {}),
              }
            : {}),
          item: item(),
          avisos: p.avisos,
          teste: p.teste,
          ir: p.ir ?? null,
          valor_liquido: p.valor_liquido ?? null,
        },
      ],
    })
    tratar(res[0], foto)
  } finally {
    emitindo.value = false
    noLote.value = false
  }
}

// Reenvia a recusada (empresa e tomador com os dados de agora; valor,
// descrição e mês os da recusada).
async function reenviarRecusada() {
  const e = recusada.value
  if (!e || !podeReenviar.value) return
  acaoRecusada.value = 'reenviar'
  resultado.value = null
  // Percentual: o lote mostra a conta da recusada (o servidor reenvia com a mesma base e o mesmo %).
  const it = itemReenvio(e)
  noLote.value = true
  try {
    const res = await tela.emitirLote({ competencia: e.competencia.slice(0, 7), itens: [it] })
    tratar(res[0], null)
  } finally {
    acaoRecusada.value = null
    noLote.value = false
  }
}

// A recusada pode ter virado nota (ex.: a resposta se perdeu): pergunta à NFE.io.
async function conferirRecusada() {
  const e = recusada.value
  if (!e || acaoRecusada.value) return
  acaoRecusada.value = 'conferir'
  try {
    const nova = await tela.conferir(e)
    if (!nova || recusada.value?.id !== e.id) return
    if (nova.status === 'emitida' || nova.status === 'processando') {
      emitida.value = true
      fechar()
    } else if (nova.status === 'rejeitada') {
      recusada.value = nova
    }
  } finally {
    acaoRecusada.value = null
  }
}

// Volta o formulário para os dados da recusada (aí dá para reenviar).
function desfazer() {
  const e = enviado.value
  if (!e) return
  form.competencia = e.competencia
  form.company_id = e.company_id
  form.tomador_id = e.tomador_id
  form.descricao = e.descricao
  form.tipo_valor = e.tipo_valor
  form.valor = e.valor
  form.base = e.base
  form.percentual = e.percentual
  form.modoPct = e.modoPct
  escolhaBase.value = { mes: e.competencia, valor: e.mesBase }
  pctTocado.value = false
  form.inf_comp = e.inf_comp
}

function verNota() {
  const e = resultado.value?.emissao
  if (e) {
    tela.abrirNota(e)
  } else {
    fechar()
    tela.irPara('notas')
  }
}

const exposto: AvulsaApi = { abrir }
defineExpose(exposto)
</script>

<template>
  <NfseSheet
    :open="aberto && !noLote"
    titulo="Nota avulsa"
    subtitulo="Uma nota que não se repete todo mês."
    largura="lg"
    :sujo="sujo"
    texto-sujo="O que você preencheu vai se perder."
    @update:open="aoMudar"
  >
    <template #cabecalho-extra>
      <NfseAmbienteBadge
        v-if="prestador"
        tamanho="sm"
        :ambiente="prestador.nfeio?.ambiente"
        :ligada="!!prestador.nfeio"
      />
    </template>

    <div class="space-y-5">
      <!-- 1. Quem emite -->
      <NfseSecao titulo="Quem emite" :icone="Building2">
        <NfseCampo rotulo="Empresa que emite" obrigatorio para="nfse-avulsa-empresa">
          <NfseEmpresaSelect
            id="nfse-avulsa-empresa"
            v-model="form.company_id"
            filtro="ligadas"
            mostrar-prontidao
          />
        </NfseCampo>
        <NfseAviso v-if="prestador && empresaTeste" tom="atencao" :icone="FlaskConical" compacto>
          <span class="font-medium">{{ TEXTO_TESTE }}</span>
        </NfseAviso>
        <NfseAviso v-else-if="producaoTravada" tom="perigo" :icone="ShieldAlert" compacto>
          {{ MOTIVO_PRODUCAO }}
        </NfseAviso>
        <NfseAviso v-if="prestador && pendenciasEmpresa.length" tom="atencao">
          A {{ prestador.apelido }} ainda não pode emitir: {{ textoPendencias }}.
          <template v-if="consertoEmpresa" #acoes>
            <Button
              v-if="consertoEmpresa.tipo === 'empresa'"
              size="sm"
              variant="outline"
              class="h-8 px-2.5 text-foreground"
              @click="corrigirEmpresa"
            >
              corrigir {{ prestador.apelido }}
            </Button>
            <Button
              v-else
              as="a"
              :href="consertoEmpresa.href"
              target="_blank"
              rel="noopener"
              size="sm"
              variant="outline"
              class="h-8 px-2.5 text-foreground"
            >
              abrir Cadastros › Empresas
              <ExternalLink class="ml-1.5 size-3.5" aria-hidden="true" />
            </Button>
          </template>
        </NfseAviso>
      </NfseSecao>

      <!-- 2. Para quem -->
      <NfseSecao titulo="Para quem" :icone="UserRound">
        <NfseCampo rotulo="Tomador (quem recebe a nota)" obrigatorio para="nfse-avulsa-tomador">
          <NfseTomadorSelect
            id="nfse-avulsa-tomador"
            v-model="form.tomador_id"
            :excluir-empresa-id="form.company_id"
          />
          <p v-if="tomador" class="text-xs text-muted-foreground">
            Na nota vai: <span class="text-foreground">{{ nomeTomador }}</span>
            · <span class="tabular-nums">{{ fmtDoc(tomador.documento_nota || tomador.documento) }}</span>
          </p>
        </NfseCampo>
      </NfseSecao>

      <!-- 3. Mês e valor -->
      <NfseSecao titulo="Mês e valor" :icone="CalendarDays">
        <div class="grid gap-3 sm:grid-cols-2">
          <NfseCampo
            rotulo="Mês de competência"
            dica="O mês em que o serviço foi prestado. É ele que vai escrito na nota."
          >
            <NfseMesPicker v-model="form.competencia" :max="hoje" />
          </NfseCampo>
          <NfseCampo
            rotulo="Como sai o valor da nota"
            :dica="ehPct ? 'Você digita a base (e a %, se não usar a da empresa); o valor sai calculado.' : 'Você digita o valor da nota.'"
          >
            <NfseSegmentado
              :model-value="form.tipo_valor"
              :opcoes="OPCOES_TIPO"
              tamanho="sm"
              aria-label="como sai o valor da nota"
              @update:model-value="escolherTipo"
            />
          </NfseCampo>
        </div>

        <div v-if="!ehPct" class="grid gap-3 sm:grid-cols-2">
          <NfseCampo rotulo="Valor do serviço" obrigatorio para="nfse-avulsa-valor">
            <NfseValorInput id="nfse-avulsa-valor" v-model="form.valor" />
          </NfseCampo>
        </div>

        <template v-else>
          <!-- A empresa tem % padrão: usar a dela (marcado) ou outra % -->
          <NfseCampo
            v-if="pctEmpresa"
            rotulo="Percentual"
            obrigatorio
            :erro="erroPct"
            :dica="form.modoPct === 'outra' ? 'Aceita vírgula: 0,5 = meio por cento.' : undefined"
          >
            <NfseOpcoes
              v-model="modoPctModel"
              :opcoes="opcoesPct"
              :colunas="2"
              aria-label="percentual da nota"
            />
            <div v-if="form.modoPct === 'outra'" class="relative inline-flex w-fit">
              <input
                id="nfse-avulsa-percentual"
                v-model="form.percentual"
                type="text"
                inputmode="decimal"
                autocomplete="off"
                maxlength="12"
                placeholder="0,5"
                aria-label="outra %"
                class="h-9 w-32 rounded-md border bg-background pl-3 pr-8 text-right text-sm tabular-nums focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                :class="erroPct && 'border-red-500 dark:border-red-400'"
                :aria-invalid="erroPct ? 'true' : undefined"
                @blur="ajustarPercentual"
              />
              <span
                class="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-sm text-muted-foreground"
                aria-hidden="true"
              >%</span>
            </div>
          </NfseCampo>

          <!-- 01/10: de que mês vem o faturamento que vira a base -->
          <NfseMesBase
            id="nfse-avulsa-mes-base"
            :model-value="mesBase"
            :competencia="form.competencia"
            @update:model-value="escolherMesBase"
          />

          <div class="grid gap-3 sm:grid-cols-2">
            <NfseCampo
              rotulo="Base (R$)"
              obrigatorio
              dica="O valor sobre o qual incide o %. Vem preenchido com o faturamento da empresa (todas as lojas com o CNPJ dela) no mês escolhido em “Base do %”; pode trocar."
              para="nfse-avulsa-base"
            >
              <NfseValorInput id="nfse-avulsa-base" v-model="form.base" :invalido="contaPequena" />
              <p v-if="form.company_id && faturamentoCarregado" class="mt-1 text-xs text-muted-foreground">
                {{ textoFaturamento(faturamento, fmtMes(mesDaBase)) }}{{
                  baseAutomatica && form.base && form.base !== baseAutomatica ? ' — base trocada à mão' : ''
                }}
              </p>
            </NfseCampo>
            <!-- Empresa sem % padrão: o campo é obrigatório, como antes -->
            <NfseCampo
              v-if="!pctEmpresa"
              rotulo="Percentual"
              obrigatorio
              :erro="erroPct"
              :dica="dicaPctSemEmpresa"
              para="nfse-avulsa-percentual"
            >
              <div class="relative inline-flex w-fit">
                <input
                  id="nfse-avulsa-percentual"
                  v-model="form.percentual"
                  type="text"
                  inputmode="decimal"
                  autocomplete="off"
                  maxlength="12"
                  placeholder="0,5"
                  class="h-9 w-32 rounded-md border bg-background pl-3 pr-8 text-right text-sm tabular-nums focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  :class="erroPct && 'border-red-500 dark:border-red-400'"
                  :aria-invalid="erroPct ? 'true' : undefined"
                  @input="aoDigitarPct"
                  @blur="ajustarPercentual"
                />
                <span
                  class="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-sm text-muted-foreground"
                  aria-hidden="true"
                >%</span>
              </div>
            </NfseCampo>
          </div>

          <!-- O valor calculado, antes de emitir -->
          <div class="flex items-start gap-2 rounded-md border bg-muted/40 px-3 py-2 text-sm" aria-live="polite">
            <Calculator class="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
            <p v-if="formula && !contaPequena" class="tabular-nums">
              <span class="text-muted-foreground">Valor da nota: </span>
              <strong class="font-semibold">{{ fmtBrl(valorDecimal) }}</strong>
              <span class="text-muted-foreground"> = {{ formula }}</span>
            </p>
            <p v-else-if="contaPequena" :class="TOM_TEXTO.perigo">
              {{ formula }} dá menos de R$ 0,01. Aumente a base ou o percentual.
            </p>
            <p v-else-if="usaPctEmpresa" class="text-muted-foreground">
              Digite a base para ver o valor da nota: {{ fmtPctOrigem(pctEmpresa, 'empresa') }} da base.
            </p>
            <p v-else class="text-muted-foreground">
              Digite a base e o percentual para ver o valor da nota. Ex.: 0,5% de R$ 200.000,00 = R$ 1.000,00.
            </p>
          </div>
        </template>
      </NfseSecao>

      <!-- 4. Descrição -->
      <NfseSecao titulo="Descrição na nota" :icone="FileText">
        <NfseCampo rotulo="Descrição do serviço" obrigatorio para="nfse-avulsa-descricao">
          <textarea
            id="nfse-avulsa-descricao"
            ref="campoDescricao"
            v-model="form.descricao"
            rows="3"
            class="w-full rounded-md border bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
          <NfseVariaveisDescricao
            :competencia="form.competencia"
            :texto="form.descricao"
            :com-percentual="ehPct"
            :percentual="pctEfetivo"
            :base="baseDecimal || null"
            @inserir="inserir"
          />
        </NfseCampo>
        <details class="group rounded-lg border" :open="!!form.inf_comp">
          <summary class="cursor-pointer select-none px-3 py-2 text-sm font-medium hover:bg-muted/40">
            Informações complementares <span class="font-normal text-muted-foreground">(opcional)</span>
          </summary>
          <div class="space-y-1.5 border-t px-3 py-2.5">
            <label for="nfse-avulsa-infcomp" class="sr-only">Informações complementares</label>
            <textarea
              id="nfse-avulsa-infcomp"
              v-model="form.inf_comp"
              rows="2"
              class="w-full rounded-md border bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
            <p class="text-xs text-muted-foreground">Sai num campo separado da nota, abaixo da descrição.</p>
          </div>
        </details>
      </NfseSecao>

      <!-- 5. Checagem -->
      <div class="space-y-2.5 rounded-lg border px-3 py-2.5 text-sm" aria-live="polite">
        <p v-if="!completo" class="flex items-center gap-2 text-muted-foreground">
          <Info class="size-4 shrink-0" aria-hidden="true" />
          {{
            contaPequena
              ? 'O valor calculado fica abaixo de R$ 0,01: aumente a base ou o percentual.'
              : `Preencha ${camposFaltando} para conferir.`
          }}
        </p>
        <p v-else-if="conferindo" class="flex items-center gap-2 text-muted-foreground">
          <Loader2 class="size-4 shrink-0 animate-spin motion-reduce:animate-none" aria-hidden="true" />
          Conferindo…
        </p>
        <div v-else-if="erroPrevia" class="flex flex-wrap items-center gap-2">
          <XCircle class="size-4 shrink-0" :class="TOM_TEXTO.perigo" aria-hidden="true" />
          <span class="min-w-0 flex-1">Não deu para conferir: {{ erroPrevia }}</span>
          <Button size="sm" variant="outline" class="h-8 px-2.5" @click="agendar(0)">tentar de novo</Button>
        </div>
        <template v-else-if="ok">
          <p class="flex items-center gap-2">
            <CheckCircle2 class="size-4 shrink-0" :class="TOM_TEXTO.sucesso" aria-hidden="true" />
            <span>
              {{ recusada && !mudou ? 'Nada bloqueia o reenvio.' : 'Tudo certo para emitir.' }}
              <span class="text-muted-foreground">
                {{ fmtBrl(previa?.valor || valorDecimal) }}<template v-if="formula"> = {{ formula }}<template
                  v-if="textoMesDaBase"
                > ({{ textoMesDaBase }})</template>,</template>
                em {{ fmtMes(form.competencia) }}.
              </span>
            </span>
          </p>
          <p v-if="irTexto" class="pl-6 text-xs tabular-nums" :class="previa?.ir?.retem ? 'text-foreground' : 'text-muted-foreground'">
            {{ irTexto }}
          </p>
          <NfseProblemas :avisos="avisos" :contexto="contexto" />
        </template>
        <template v-else-if="previa">
          <p class="font-medium">{{ recusada && !mudou ? 'Resolva antes de reenviar:' : 'Resolva antes de emitir:' }}</p>
          <NfseProblemas :problemas="problemas" :avisos="avisos" :contexto="contexto" />
        </template>
      </div>

      <!-- Resultado da última tentativa (quando não saiu) -->
      <NfseAviso
        v-if="travada && resultado"
        tom="atencao"
        :icone="AlertTriangle"
        titulo="A NFE.io não confirmou esta nota"
      >
        A nota pode ter saído. Não emita de novo daqui: confira primeiro em Notas enviadas.
        <template #acoes>
          <Button size="sm" variant="outline" class="h-8 px-2.5 text-foreground" @click="verNota">
            ver em Notas enviadas
          </Button>
        </template>
      </NfseAviso>
      <template v-else>
        <!-- Recusada: corrigir e REENVIAR (a mesma nota). Nunca emitir outra às cegas. -->
        <NfseAviso v-if="recusada" tom="perigo" titulo="A nota foi recusada">
          <template v-if="!mudou">
            <p>
              Corrija o que foi apontado (na empresa ou no tomador) e reenvie. Vai a mesma nota de novo, sem risco de
              nota em dobro.
            </p>
            <p v-if="enviado?.inf_comp.trim()" class="text-xs">
              As informações complementares não vão no reenvio. Se a nota precisa delas, mude o que for preciso e
              emita uma nota nova.
            </p>
          </template>
          <template v-else>
            <p>
              Você mudou {{ listaE(camposMudados) }}. O reenvio usa os dados da recusada: para sair com os dados
              novos, vai uma nota <strong class="font-semibold">nova</strong>.
            </p>
            <p class="font-medium">
              A recusada fica em Notas enviadas e não deve ser reenviada depois, senão saem duas notas do mesmo
              serviço.
            </p>
          </template>
          <NfseProblemas :erros="recusada.erros" :contexto="contextoRecusada" />
          <template #acoes>
            <template v-if="!mudou">
              <NfseDica :texto="podeReenviar ? null : motivoReenvio">
                <span class="inline-flex" :tabindex="podeReenviar ? undefined : 0">
                  <Button size="sm" class="h-8 px-2.5" :disabled="!podeReenviar" @click="reenviarRecusada">
                    <Loader2
                      v-if="acaoRecusada === 'reenviar'"
                      class="mr-1.5 size-4 animate-spin motion-reduce:animate-none"
                      aria-hidden="true"
                    />
                    <RotateCw v-else class="mr-1.5 size-4" aria-hidden="true" />
                    Reenviar
                  </Button>
                </span>
              </NfseDica>
              <Button
                v-if="tela.canEdit.value"
                size="sm"
                variant="outline"
                class="h-8 px-2.5 text-foreground"
                :disabled="!!acaoRecusada || emitindo"
                @click="conferirRecusada"
              >
                <Loader2
                  v-if="acaoRecusada === 'conferir'"
                  class="mr-1.5 size-4 animate-spin motion-reduce:animate-none"
                  aria-hidden="true"
                />
                <RefreshCw v-else class="mr-1.5 size-4" aria-hidden="true" />
                atualizar da NFE.io
              </Button>
            </template>
            <Button v-else size="sm" variant="outline" class="h-8 px-2.5 text-foreground" @click="desfazer">
              <Undo2 class="mr-1.5 size-4" aria-hidden="true" />
              voltar aos dados da recusada
            </Button>
          </template>
        </NfseAviso>
        <NfseAviso v-if="resultado && resultado.estado !== 'rejeitada'" tom="perigo" titulo="A nota não foi enviada">
          <p v-if="!resultado.problemas?.length">{{ resultado.texto }}</p>
          <NfseProblemas v-else :problemas="resultado.problemas" :contexto="contexto" />
        </NfseAviso>
      </template>
    </div>

    <template #rodape>
      <Button variant="outline" size="sm" @click="cancelar">{{ recusada ? 'Fechar' : 'Cancelar' }}</Button>
      <!-- Com recusada e os mesmos dados, a saída é o "Reenviar" do aviso acima. -->
      <NfseDica v-if="!recusada || mudou" :texto="podeEmitir ? null : motivoBloqueio">
        <span class="inline-flex" :tabindex="podeEmitir ? undefined : 0">
          <Button size="sm" :disabled="!podeEmitir" @click="emitir">
            <Loader2 v-if="emitindo" class="mr-1.5 size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
            <Send v-else class="mr-1.5 size-4" aria-hidden="true" />
            {{ recusada ? 'Emitir nota nova' : 'Emitir nota avulsa' }}
          </Button>
        </span>
      </NfseDica>
    </template>
  </NfseSheet>
</template>
