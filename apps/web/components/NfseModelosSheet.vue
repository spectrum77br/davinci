<script setup lang="ts">
// Formulário da nota fixa (gaveta à direita), montado UMA vez pela página.
// Qualquer aba abre com tela.abrirModelo({ modelo?, preset?, titulo? }) e
// recebe de volta a nota fixa salva (ou null se a pessoa desistir).
//
// Códigos do serviço: por padrão valem os da empresa. "Usar códigos
// diferentes" liga os três campos; desligar LIMPA os três (antes ficavam
// escondidos e continuavam indo para a nota). 29/09 (motor NFE.io): são o
// código do serviço na prefeitura (city_service_code, ex.: 6303), o item da
// LC 116 (federal_service_code, ex.: 10.05) e o NBS; campo vazio = o da empresa.
//
// Valor (29/09, Eduardo: "quero emitir uma nota de serviço de 0,5%"):
// - "Valor fixo": o mesmo valor todo mês (muda na hora de emitir, se quiser);
// - "Percentual": o valor sai na hora de emitir = base digitada × % ÷ 100,
//   arredondado no centavo. A "base sugerida" (opcional) já vem preenchida.
// O formulário guarda o % como a pessoa digitou ("0,5"); a API recebe "0.5".
//
// % da empresa (29/09, Eduardo: "a porcentagem de cada empresa que temos"): se
// a empresa que emite tem % padrão (Cadastros › Empresas), o Percentual vira
// "Usar a % da empresa (0,5%)" (marcado) ou "Outra %" com o campo. Usando a da
// empresa, a nota fixa salva percentual = null e acompanha a empresa se a % dela
// mudar. Sem % na empresa, o campo é obrigatório, como antes.
import { computed, nextTick, ref, watch } from 'vue'
import { Banknote, Building2, Calculator, ChevronRight, ExternalLink, FileText, Loader2, Percent, Trash2 } from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import {
  calcularPercentual, campoDoErro, codigoErro, erroApi, fmtBrl, fmtMes, fmtPct, fmtPctOrigem, inserirNoCursor,
  mesAtual, mesParaData, modeloParaForm, paraDecimal, pctPositivo, pendenciaTexto, prestadorPorId, soDigitos,
  TOM_TEXTO, tomadorEstiloNfeio, tomadorNaNota, useNfseTela,
  type AbrirModeloOpts, type FaturamentoEmpresa, type Modelo, type ModeloApi, type ModeloForm, type OrigemPct,
  useNfseApi,
} from '~/lib/nfse'

// Campos lado a lado: cada NfseCampo vira subgrade (rótulo, campo, dica)
// para os campos ficarem na mesma altura mesmo quando um rótulo quebra linha.
const CAMPO_EM_GRADE = 'sm:row-span-3 sm:grid sm:grid-rows-subgrid sm:gap-y-1.5 sm:space-y-0'

type Campo =
  | 'company_id' | 'tomador_id' | 'nome' | 'descricao' | 'tipo_valor' | 'valor' | 'percentual' | 'base_padrao'
  | 'inf_comp' | 'city_service_code' | 'federal_service_code' | 'c_nbs'
type CampoCodigo = 'city_service_code' | 'federal_service_code' | 'c_nbs'
type TipoValor = ModeloForm['tipo_valor']

// Ordem em que os erros são mostrados (rola até o primeiro).
const ORDEM: Campo[] = [
  'company_id', 'tomador_id', 'nome', 'descricao', 'tipo_valor', 'valor', 'percentual', 'base_padrao', 'inf_comp',
  'city_service_code', 'federal_service_code', 'c_nbs',
]

const OPCOES_TIPO: { id: TipoValor; rotulo: string; icone: typeof Banknote }[] = [
  { id: 'fixo', rotulo: 'Valor fixo', icone: Banknote },
  { id: 'percentual', rotulo: 'Percentual', icone: Percent },
]
const EXPLICA_TIPO: Record<TipoValor, string> = {
  fixo: 'A nota sai com o mesmo valor todo mês. Dá para mudar na hora de emitir.',
  percentual:
    'A base é o faturamento do mês da empresa (todas as lojas com o CNPJ dela) e o valor da nota é calculado sozinho. Dá para trocar a base na hora de emitir.',
}
// Base do exemplo quando não há base sugerida.
const BASE_EXEMPLO = '100000.00'

// "0,5" · "0.5" · "0,5%" · "0.5000" (API) → { pct: '0.5' }. Vazio → pct null, sem erro.
function lerPercentual(texto: string | null | undefined): { pct: string | null; erro: string | null } {
  let s = String(texto ?? '').trim().replace(/%/g, '').replace(/\s/g, '')
  if (!s) return { pct: null, erro: null }
  if (s.includes(',')) s = s.replace(/\./g, '').replace(',', '.')
  if (!/^\d*\.?\d*$/.test(s) || !/\d/.test(s)) return { pct: null, erro: 'Digite só o número do percentual. Ex.: 0,5' }
  const casas = (s.split('.')[1] ?? '').replace(/0+$/, '').length
  if (casas > 4) return { pct: null, erro: 'Use no máximo 4 casas depois da vírgula.' }
  const n = Number(s)
  if (!(n > 0)) return { pct: null, erro: 'O percentual precisa ser maior que zero.' }
  if (n > 100) return { pct: null, erro: 'O percentual vai até 100%.' }
  return { pct: n.toFixed(4).replace(/\.?0+$/, ''), erro: null }
}

// '0.5' → '0,5' (como a pessoa digita; sem o "%", sem separador de milhar)
function textoPercentual(v: string | null | undefined): string {
  const { pct } = lerPercentual(v)
  if (!pct) return String(v ?? '').trim()
  return Number(pct).toLocaleString('pt-BR', { maximumFractionDigits: 4, useGrouping: false })
}
// Tamanho máximo de cada código e o que ele aceita enquanto digita.
const TAMANHO_CODIGO: Record<CampoCodigo, number> = { city_service_code: 20, federal_service_code: 10, c_nbs: 9 }
const LIMPAR_CODIGO: Record<CampoCodigo, (v: string) => string> = {
  // O código da prefeitura varia de cidade para cidade: números, letras, ponto, traço e barra.
  city_service_code: (v) => v.replace(/[^\w.\-/]/g, ''),
  federal_service_code: (v) => v.replace(/[^\d.]/g, ''),
  c_nbs: (v) => soDigitos(v),
}
// Códigos da API que o campoDoErro não liga a um campo.
const CAMPO_POR_CODIGO: Record<string, Campo> = {
  empresa_nao_encontrada: 'company_id',
  tomador_nao_encontrado: 'tomador_id',
}

const tela = useNfseTela()
// Com a chave da senha extra (a página entrega): useApi() direto volta nfse_locked.
const { api } = useNfseApi()
const toasts = useToasts()

const mes = mesAtual()

function vazio(): ModeloForm {
  return {
    company_id: null,
    tomador_id: null,
    nome: '',
    // Eduardo (30/09): "fixo = intermediação (sem mais detalhes), mas deixar
    // opção de colocar se precisar" — igual às notas que já saem pela NFE.io.
    descricao: 'Intermediação',
    tipo_valor: 'fixo',
    valor: '',
    percentual: '',
    base_padrao: '',
    city_service_code: null,
    federal_service_code: null,
    c_nbs: null,
    inf_comp: null,
    ativo: true,
    ordem: 0,
  }
}

const aberto = ref(false)
const form = ref<ModeloForm>(vazio())
const original = ref<Modelo | null>(null)
const tituloPedido = ref<string | undefined>()
const codigosProprios = ref(false)
const nomeTocado = ref(false)
const abrirCodigos = ref(false)
const abrirComplemento = ref(false)
const erros = ref<Partial<Record<Campo, string>>>({})
const erroGeral = ref<string | null>(null)
const tentouSalvar = ref(false)
const salvando = ref(false)
const inicial = ref('')
const sheet = ref<{ rolarPara(id: string): void } | null>(null)
const campoDescricao = ref<HTMLTextAreaElement | null>(null)
let resolver: ((m: Modelo | null) => void) | null = null

// Percentual: usar a % da empresa (se ela tiver) ou "outra %", a desta nota fixa.
type ModoPct = 'empresa' | 'outra'
const modoPct = ref<ModoPct>('empresa')

const somenteLeitura = computed(() => !tela.canEdit.value)
const editando = computed(() => !!form.value.id)

const titulo = computed(() => {
  if (tituloPedido.value) return tituloPedido.value
  if (somenteLeitura.value) return 'Nota fixa'
  return editando.value ? 'Editar nota fixa' : 'Nova nota fixa'
})

// --- Empresa e tomador ----------------------------------------------------------

const empresa = computed(() => prestadorPorId(tela.prestadores.value, form.value.company_id))
const tomador = computed(() => tela.tomadores.value.find((t) => t.id === form.value.tomador_id) ?? null)

const tomadorIgualEmpresa = computed(
  () =>
    !!tomador.value &&
    tomador.value.tipo === 'grupo' &&
    !!form.value.company_id &&
    tomador.value.company_id === form.value.company_id,
)

const pendenciasEmpresa = computed(() => (empresa.value?.pendencias ?? []).map(pendenciaTexto))

function nomeCurtoTomador(): string {
  const t = tomador.value
  if (!t) return ''
  if (t.tipo === 'grupo') return prestadorPorId(tela.prestadores.value, t.company_id)?.apelido || t.nome_nota || ''
  return t.nome_nota || t.nome || ''
}

const subtitulo = computed(() => {
  if (empresa.value && tomador.value) return `${empresa.value.apelido} → ${nomeCurtoTomador()}`
  return 'Uma nota que sai todo mês, da empresa que emite para quem recebe.'
})

// Enquanto a pessoa não mexe no nome, ele acompanha o tomador, no estilo da
// lista da NFE.io (Eduardo, 30/09): "61.989.102 LEOMAR ALVES ANTUNES".
const sugestaoNome = computed(() => {
  const t = tomadorNaNota(tomador.value)
  return t.nome ? tomadorEstiloNfeio(t.doc, t.nome) : ''
})

watch(sugestaoNome, (s) => {
  if (aberto.value && !nomeTocado.value && s) form.value.nome = s
})

function aoDigitarNome() {
  nomeTocado.value = !!form.value.nome.trim()
}

const codigosEmpresa = computed(() => {
  const f = empresa.value?.fiscal
  if (!empresa.value) return 'Por padrão, a nota usa os códigos do serviço da empresa que emite.'
  if (!f?.city_service_code) return `A ${empresa.value.apelido} ainda não tem o código do serviço salvo (aba Empresas).`
  const partes = [
    `serviço ${f.city_service_code}`,
    f.federal_service_code ? `LC 116 ${f.federal_service_code}` : null,
    f.c_nbs ? `NBS ${f.c_nbs}` : null,
  ]
  return `Por padrão usa os códigos da ${empresa.value.apelido}: ${partes.filter(Boolean).join(' · ')}.`
})

// --- Valor: fixo ou percentual --------------------------------------------------

const ehPercentual = computed(() => form.value.tipo_valor === 'percentual')
const pctLido = computed(() => lerPercentual(form.value.percentual))
const pctApi = computed(() => pctLido.value.pct)
// A % padrão da empresa escolhida ("0.5000"), ou null se ela não tiver.
const pctEmpresa = computed(() => pctPositivo(empresa.value?.percentual_servico))
const usaPctEmpresa = computed(() => ehPercentual.value && !!pctEmpresa.value && modoPct.value === 'empresa')
// A % que vale nesta nota fixa: a da empresa ou a digitada (formato da API).
const pctEfetivo = computed(() => (usaPctEmpresa.value ? pctEmpresa.value : pctApi.value))
const origemPct = computed<OrigemPct | null>(() =>
  usaPctEmpresa.value ? 'empresa' : pctApi.value ? 'nota_fixa' : null,
)

// O NfseOpcoes trabalha com string/número; aqui só existem os dois modos.
const modoPctModel = computed({
  get: () => modoPct.value as string,
  set: (v: string | number | null) => {
    modoPct.value = v === 'outra' ? 'outra' : 'empresa'
    // "Outra %": o campo aparece embaixo das opções; o cursor já vai para ele.
    if (v === 'outra') nextTick(() => document.getElementById('nfse-modelo-percentual')?.focus())
  },
})

const opcoesPct = computed(() => {
  const apelido = empresa.value?.apelido
  return [
    {
      valor: 'empresa',
      titulo: `Usar a % da empresa (${fmtPct(pctEmpresa.value)})`,
      descricao: apelido
        ? `É a % cadastrada na ${apelido} (Cadastros › Empresas). Se mudar lá, muda aqui.`
        : 'É a % cadastrada na empresa (Cadastros › Empresas).',
    },
    { valor: 'outra', titulo: 'Outra %', descricao: 'Só desta nota fixa.' },
  ]
})

// Digitou uma % no campo (empresa sem % padrão): ela é "outra %". Se depois
// trocar para uma empresa com %, o que foi digitado continua valendo.
function aoDigitarPct() {
  if (form.value.percentual.trim()) modoPct.value = 'outra'
}

// Na caixa da conta: por que não dá para fazer a conta com o % digitado. "0" e
// "0," são o meio do caminho de "0,5": aí só pede o percentual, sem vermelho.
const erroPctConta = computed(() => {
  if (usaPctEmpresa.value) return null
  const e = pctLido.value.erro
  if (!e) return null
  const t = String(form.value.percentual ?? '').replace(/[%\s]/g, '')
  return /^0*[.,]?0*$/.test(t) ? null : e
})
const baseApi = computed(() => paraDecimal(form.value.base_padrao) || null)

// A prévia do valor (Eduardo, 30/09: "depois que colocamos a porcentagem que
// queremos na empresa, ele aparece aqui e tem que mostrar a prévia do valor"):
// o faturamento do mês da empresa, a mesma base que o "Emitir do mês" usa.
const faturamentoMes = ref<FaturamentoEmpresa | null>(null)
const faturamentoCarregado = ref(false)
let seqFaturamento = 0
watch(
  () => [aberto.value, form.value.company_id, form.value.tipo_valor] as const,
  async ([ab, cid, tipo]) => {
    const minha = ++seqFaturamento
    faturamentoMes.value = null
    faturamentoCarregado.value = false
    if (!ab || !cid || tipo !== 'percentual') return
    const r = await api<{ empresas: FaturamentoEmpresa[] }>(
      `/api/nfse/faturamento?competencia=${mesParaData(mes)}`,
    ).catch(() => null)
    if (minha !== seqFaturamento) return
    faturamentoMes.value = r?.empresas.find((e) => e.company_id === cid) ?? null
    faturamentoCarregado.value = !!r
  },
  { immediate: true },
)
const baseFaturamento = computed(() => {
  const v = paraDecimal(faturamentoMes.value?.valor)
  return v && Number(v) > 0 ? v : ''
})

function escolherTipo(v: string) {
  form.value.tipo_valor = v === 'percentual' ? 'percentual' : 'fixo'
}

// "0.5" → "0,5" ao sair do campo (só quando o número é válido).
function ajustarPercentual() {
  if (pctApi.value) form.value.percentual = textoPercentual(pctApi.value)
}

// A conta, igual à do servidor: base × % ÷ 100, no centavo. A base é o
// faturamento do mês; sem venda, a base sugerida antiga da nota fixa (se
// houver); senão, um exemplo.
const exemplo = computed(() => {
  const pct = pctEfetivo.value
  if (!pct) return null
  const origem: 'faturamento' | 'sugerida' | 'exemplo' = baseFaturamento.value
    ? 'faturamento'
    : baseApi.value && Number(baseApi.value) > 0 ? 'sugerida' : 'exemplo'
  const base = origem === 'faturamento' ? baseFaturamento.value : origem === 'sugerida' ? baseApi.value! : BASE_EXEMPLO
  const valor = calcularPercentual(base, pct)
  const deOnde = origem === 'faturamento' ? ` (faturamento de ${fmtMes(mes)})` : ''
  return {
    origem,
    texto: `${fmtPctOrigem(pct, origemPct.value)} de ${fmtBrl(base)}${deOnde} = ${fmtBrl(valor)}`,
    pouco: !valor || Number(valor) < 0.01,
  }
})

// --- Abrir / fechar -------------------------------------------------------------

// "Sujo" compara com o que abriu; o nome sugerido sozinho não conta.
function retrato(): string {
  const f = form.value
  const pctEmpresaUsada = usaPctEmpresa.value
  return JSON.stringify({
    ...f,
    nome: nomeTocado.value ? f.nome : '',
    // "0.5" e "0,5" são o mesmo percentual. Usando a da empresa, o campo não conta.
    percentual: pctEmpresaUsada ? null : lerPercentual(f.percentual).pct ?? f.percentual,
    pctEmpresaUsada,
    codigosProprios: codigosProprios.value,
  })
}

const sujo = computed(() => aberto.value && !somenteLeitura.value && retrato() !== inicial.value)

function terminar(m: Modelo | null) {
  const r = resolver
  resolver = null
  r?.(m)
}

function abrir(o?: AbrirModeloOpts): Promise<Modelo | null> {
  terminar(null)
  const base = o?.modelo ? modeloParaForm(o.modelo) : vazio()
  form.value = { ...base, ...(o?.preset ?? {}) }
  // Nota fixa antiga (sem tipo_valor) é de valor fixo; o % aparece como se digita ("0,5").
  if (form.value.tipo_valor !== 'percentual') form.value.tipo_valor = 'fixo'
  form.value.valor = form.value.valor ?? ''
  form.value.percentual = textoPercentual(form.value.percentual)
  form.value.base_padrao = form.value.base_padrao ?? ''
  // Nota fixa sem % própria usa a da empresa; com % própria, é "outra %".
  modoPct.value = lerPercentual(form.value.percentual).pct ? 'outra' : 'empresa'
  original.value = o?.modelo ?? null
  tituloPedido.value = o?.titulo
  codigosProprios.value = !!(form.value.city_service_code || form.value.federal_service_code || form.value.c_nbs)
  abrirCodigos.value = codigosProprios.value
  abrirComplemento.value = !!form.value.inf_comp
  nomeTocado.value = !!form.value.nome.trim()
  if (!nomeTocado.value && sugestaoNome.value) form.value.nome = sugestaoNome.value
  erros.value = {}
  erroGeral.value = null
  tentouSalvar.value = false
  salvando.value = false
  inicial.value = retrato()
  aberto.value = true
  return new Promise<Modelo | null>((res) => {
    resolver = res
  })
}

function fechar(m: Modelo | null) {
  aberto.value = false
  terminar(m)
}

// A gaveta já perguntou "Sair sem salvar?" quando precisava.
function aoMudar(v: boolean) {
  if (!v) fechar(null)
}

async function cancelar() {
  if (sujo.value) {
    const ok = await tela.confirmar({
      titulo: 'Sair sem salvar?',
      texto: 'O que você mudou vai se perder.',
      tom: 'perigo',
      botao: 'Sair sem salvar',
      voltar: 'Continuar editando',
    })
    if (!ok) return
  }
  fechar(null)
}

// Excluir daqui mesmo (Eduardo, 30/09: "precisa ter um botão para apagar").
async function excluir() {
  if (!original.value) return
  if (await tela.excluirModelo(original.value)) fechar(null)
}

defineExpose<ModeloApi>({ abrir })

// --- Campos ---------------------------------------------------------------------

watch(codigosProprios, (v) => {
  if (v) return
  form.value.city_service_code = null
  form.value.federal_service_code = null
  form.value.c_nbs = null
})

function digitarCodigo(campo: CampoCodigo, ev: Event) {
  const el = ev.target as HTMLInputElement
  const d = LIMPAR_CODIGO[campo](el.value).slice(0, TAMANHO_CODIGO[campo])
  el.value = d
  form.value[campo] = d || null
}

function inserirVariavel(token: string) {
  form.value.descricao = inserirNoCursor(campoDescricao.value, form.value.descricao, token)
}

function aoAlternarDetalhe(qual: 'codigos' | 'complemento', ev: Event) {
  const abertoAgora = (ev.target as HTMLDetailsElement).open
  if (qual === 'codigos') abrirCodigos.value = abertoAgora
  else abrirComplemento.value = abertoAgora
}

// --- Validação e envio ----------------------------------------------------------

function validar(): Partial<Record<Campo, string>> {
  const f = form.value
  const e: Partial<Record<Campo, string>> = {}
  if (!f.company_id) e.company_id = 'Escolha a empresa que emite.'
  if (!f.tomador_id) e.tomador_id = 'Escolha o tomador.'
  else if (tomadorIgualEmpresa.value) e.tomador_id = 'O tomador não pode ser a própria empresa que emite.'
  if (!f.nome.trim()) e.nome = 'Dê um nome.'
  if (!f.descricao.trim()) e.descricao = 'Escreva a descrição.'
  if (f.tipo_valor === 'percentual') {
    if (!usaPctEmpresa.value) {
      const { pct: digitado, erro } = lerPercentual(f.percentual)
      if (erro) e.percentual = erro
      else if (!digitado) {
        e.percentual = pctEmpresa.value
          ? 'Digite a outra %. Ex.: 0,5 para 0,5%.'
          : 'Digite o percentual. Ex.: 0,5 para 0,5%.'
      }
    }
    const pct = pctEfetivo.value
    const base = paraDecimal(f.base_padrao)
    if (base && Number(base) <= 0) e.base_padrao = 'A base precisa ser maior que zero (ou deixe em branco).'
    else if (base && pct && !(Number(calcularPercentual(base, pct)) >= 0.01)) {
      e.base_padrao = 'Com essa base, a nota daria menos de R$ 0,01.'
    }
  } else {
    const v = paraDecimal(f.valor)
    if (!v || Number(v) <= 0) e.valor = 'Digite um valor maior que zero.'
    // Nota de valor fixo com {percentual}/{base}: o servidor não deixa sair.
    const MARCADOR = /\{(percentual|base)\}/
    if (!e.descricao && MARCADOR.test(f.descricao)) {
      e.descricao = 'Tire {percentual} e {base} do texto ou mude a nota para Percentual.'
    }
    if (MARCADOR.test(f.inf_comp || '')) {
      e.inf_comp = 'Tire {percentual} e {base} do texto ou mude a nota para Percentual.'
    }
  }
  if (codigosProprios.value) {
    if (f.c_nbs && f.c_nbs.length !== 9) e.c_nbs = 'O NBS tem 9 dígitos.'
    if (f.federal_service_code && !/^\d{1,2}\.?\d{2}$/.test(f.federal_service_code)) {
      e.federal_service_code = 'Use o item da LC 116. Ex.: 10.05'
    }
  }
  return e
}

// Depois da 1ª tentativa, os erros acompanham o que a pessoa corrige.
watch(
  [form, codigosProprios, modoPct],
  () => {
    if (tentouSalvar.value) erros.value = validar()
  },
  { deep: true },
)

// Erro que aparece na hora, sem esperar o salvar.
const erroTomador = computed(
  () => erros.value.tomador_id || (tomadorIgualEmpresa.value ? 'O tomador não pode ser a própria empresa que emite.' : null),
)

function irParaCampo(campo: Campo) {
  if (campo === 'city_service_code' || campo === 'federal_service_code' || campo === 'c_nbs') abrirCodigos.value = true
  if (campo === 'inf_comp') abrirComplemento.value = true
  nextTick(() => {
    const id = `nfse-modelo-campo-${campo}`
    sheet.value?.rolarPara(id)
    // Percentual com "outra %": o erro é do campo, não da escolha.
    const pctDigitado = campo === 'percentual' ? document.getElementById('nfse-modelo-percentual') : null
    const alvo =
      pctDigitado ??
      document
        .getElementById(id)
        ?.querySelector<HTMLElement>(
          'input, textarea, button[role="combobox"], button[aria-selected="true"], [role="radio"][data-state="checked"]',
        )
    alvo?.focus({ preventScroll: true })
  })
}

async function salvar() {
  if (somenteLeitura.value || salvando.value) return
  tentouSalvar.value = true
  erroGeral.value = null
  const e = validar()
  erros.value = e
  const primeiro = ORDEM.find((c) => e[c])
  if (primeiro) {
    irParaCampo(primeiro)
    return
  }

  const f = form.value
  const proprio = codigosProprios.value
  const pct = f.tipo_valor === 'percentual'
  const corpo = {
    company_id: f.company_id,
    tomador_id: f.tomador_id,
    nome: f.nome.trim(),
    descricao: f.descricao.trim(),
    // Só vai o lado escolhido: no percentual o valor é null (sai na hora de emitir).
    tipo_valor: f.tipo_valor,
    valor: pct ? null : paraDecimal(f.valor),
    // Usando a % da empresa: null (a nota fixa acompanha a empresa).
    percentual: pct && !usaPctEmpresa.value ? pctApi.value : null,
    base_padrao: pct ? baseApi.value : null,
    city_service_code: (proprio && f.city_service_code?.trim()) || null,
    federal_service_code: (proprio && f.federal_service_code?.trim()) || null,
    c_nbs: (proprio && f.c_nbs) || null,
    inf_comp: f.inf_comp?.trim() || null,
    ativo: f.ativo,
    ordem: f.ordem,
  }

  salvando.value = true
  try {
    const salvo = f.id
      ? await api<Modelo>(`/api/nfse/modelos/${f.id}`, { method: 'PATCH', body: corpo })
      : await api<Modelo>('/api/nfse/modelos', { method: 'POST', body: corpo })
    toasts.success('Nota fixa salva')
    await tela.recarregar()
    // A resposta do salvar não traz os nomes; a lista recarregada traz.
    fechar(tela.modelos.value.find((m) => m.id === salvo.id) ?? salvo)
  } catch (err) {
    const msg = erroApi(err)
    let campo = (campoDoErro(err) ?? CAMPO_POR_CODIGO[codigoErro(err) ?? '']) as Campo | undefined
    // No percentual não há campo "valor" na tela: o erro vai para o percentual.
    if (campo === 'valor' && pct) campo = 'percentual'
    else if ((campo === 'percentual' || campo === 'base_padrao') && !pct) campo = 'valor'
    if (campo && ORDEM.includes(campo)) {
      erros.value = { ...erros.value, [campo]: msg }
      irParaCampo(campo)
    } else {
      erroGeral.value = msg
      nextTick(() => sheet.value?.rolarPara('nfse-modelo-erro'))
    }
  } finally {
    salvando.value = false
  }
}

async function corrigirEmpresa() {
  const p = empresa.value
  if (!p) return
  await tela.abrirEmpresa(p.company_id, pendenciasEmpresa.value.find((x) => x.alvo === 'empresa')?.foco)
}

const classeCampo =
  'h-9 w-full rounded-md border bg-background px-3 text-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50'
const classeTexto =
  'w-full rounded-md border bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50'
const classeErro = 'border-red-500 dark:border-red-400'
</script>

<template>
  <NfseSheet
    ref="sheet"
    :open="aberto"
    :titulo="titulo"
    :subtitulo="subtitulo"
    largura="lg"
    :sujo="sujo"
    @update:open="aoMudar"
  >
    <template #cabecalho-extra>
      <span v-if="editando && !form.ativo" class="pill-muted">desativada</span>
      <span v-if="ehPercentual && pctEfetivo" class="pill-info tabular-nums">
        {{ fmtPct(pctEfetivo) }} da base{{ usaPctEmpresa ? ' (da empresa)' : '' }}
      </span>
    </template>

    <form id="nfse-modelo-form" class="space-y-5" novalidate @submit.prevent="salvar">
      <NfseAviso v-if="erroGeral" id="nfse-modelo-erro" tom="perigo" titulo="Não deu para salvar a nota fixa">
        {{ erroGeral }}
      </NfseAviso>
      <NfseAviso v-if="somenteLeitura" tom="neutro" compacto>
        Você pode ver esta nota fixa, mas não tem permissão para mudar.
      </NfseAviso>

      <fieldset :disabled="somenteLeitura || salvando" class="min-w-0 space-y-5">
        <!-- 1. Quem emite e quem recebe -->
        <NfseSecao titulo="Quem emite e quem recebe" :icone="Building2">
          <NfseCampo
            id="nfse-modelo-campo-company_id"
            rotulo="Empresa que emite"
            obrigatorio
            :erro="erros.company_id"
            para="nfse-modelo-empresa"
          >
            <NfseEmpresaSelect
              id="nfse-modelo-empresa"
              v-model="form.company_id"
              filtro="uteis"
              mostrar-prontidao
              :disabled="somenteLeitura || salvando"
              :invalido="!!erros.company_id"
            />
          </NfseCampo>

          <NfseAviso
            v-if="empresa && !empresa.pronto"
            tom="atencao"
            :titulo="`A ${empresa.apelido} ainda não pode emitir`"
          >
            <p>
              {{ pendenciasEmpresa.length ? pendenciasEmpresa.map((x) => x.texto.replace(/[.]$/, '')).join('; ') : 'Ligar a empresa à NFE.io' }}.
              Dá para salvar a nota fixa agora e ajustar a empresa depois.
            </p>
            <template #acoes>
              <Button type="button" size="sm" variant="outline" class="h-8" @click="corrigirEmpresa">
                corrigir {{ empresa.apelido }}
              </Button>
            </template>
          </NfseAviso>

          <NfseCampo
            id="nfse-modelo-campo-tomador_id"
            rotulo="Tomador (quem recebe a nota)"
            obrigatorio
            :erro="erroTomador"
            :dica="tomador && !tomador.ativo ? 'Este tomador está desativado: ele não aparece mais nas listas de escolha.' : undefined"
            para="nfse-modelo-tomador"
          >
            <NfseTomadorSelect
              id="nfse-modelo-tomador"
              v-model="form.tomador_id"
              :excluir-empresa-id="form.company_id"
              :disabled="somenteLeitura || salvando"
              :invalido="!!erroTomador"
            />
          </NfseCampo>
        </NfseSecao>

        <!-- 2. O que vai na nota -->
        <NfseSecao titulo="O que vai na nota" :icone="FileText">
          <NfseCampo
            id="nfse-modelo-campo-nome"
            rotulo="Nome (para você achar na lista)"
            obrigatorio
            :erro="erros.nome"
            :dica="!nomeTocado && form.nome ? 'Sugerido pelo tomador, no estilo da NFE.io. Pode trocar.' : 'Só aparece aqui no DaVinci, não vai na nota.'"
            para="nfse-modelo-nome"
          >
            <input
              id="nfse-modelo-nome"
              v-model="form.nome"
              type="text"
              maxlength="200"
              autocomplete="off"
              placeholder="Ex.: 61.989.102 LEOMAR ALVES ANTUNES"
              :class="[classeCampo, erros.nome && classeErro]"
              :aria-invalid="erros.nome ? 'true' : undefined"
              @input="aoDigitarNome"
            />
          </NfseCampo>

          <NfseCampo
            id="nfse-modelo-campo-descricao"
            rotulo="Descrição do serviço"
            obrigatorio
            :erro="erros.descricao"
            para="nfse-modelo-descricao"
          >
            <textarea
              id="nfse-modelo-descricao"
              ref="campoDescricao"
              v-model="form.descricao"
              rows="3"
              maxlength="2000"
              :class="[classeTexto, erros.descricao && classeErro]"
              :aria-invalid="erros.descricao ? 'true' : undefined"
            />
            <NfseVariaveisDescricao
              v-if="!somenteLeitura"
              :competencia="mes"
              :texto="form.descricao"
              :com-percentual="ehPercentual"
              :percentual="pctEfetivo"
              :base="baseApi"
              @inserir="inserirVariavel"
            />
          </NfseCampo>

          <!-- Valor: fixo ou percentual sobre uma base -->
          <NfseCampo
            id="nfse-modelo-campo-tipo_valor"
            rotulo="Como sai o valor da nota"
            :erro="erros.tipo_valor"
            :dica="EXPLICA_TIPO[form.tipo_valor]"
          >
            <NfseSegmentado
              :model-value="form.tipo_valor"
              :opcoes="OPCOES_TIPO"
              tamanho="sm"
              aria-label="como sai o valor da nota"
              @update:model-value="escolherTipo"
            />
          </NfseCampo>

          <NfseCampo
            v-if="!ehPercentual"
            id="nfse-modelo-campo-valor"
            rotulo="Valor padrão"
            obrigatorio
            :erro="erros.valor"
            dica="Dá para mudar na hora de emitir, mês a mês."
            para="nfse-modelo-valor"
          >
            <NfseValorInput
              id="nfse-modelo-valor"
              v-model="form.valor"
              :disabled="somenteLeitura || salvando"
              :invalido="!!erros.valor"
            />
          </NfseCampo>

          <template v-else>
            <!-- A empresa tem % padrão: usar a dela (marcado) ou outra % -->
            <NfseCampo
              v-if="pctEmpresa"
              id="nfse-modelo-campo-percentual"
              rotulo="Percentual"
              obrigatorio
              :erro="erros.percentual"
              :dica="modoPct === 'outra' ? 'Aceita vírgula: 0,5 = meio por cento.' : undefined"
            >
              <NfseOpcoes
                v-model="modoPctModel"
                :opcoes="opcoesPct"
                :colunas="2"
                :disabled="somenteLeitura || salvando"
                aria-label="percentual da nota fixa"
              />
              <div v-if="modoPct === 'outra'" class="relative inline-flex w-fit">
                <input
                  id="nfse-modelo-percentual"
                  v-model="form.percentual"
                  type="text"
                  inputmode="decimal"
                  autocomplete="off"
                  maxlength="12"
                  placeholder="0,5"
                  aria-label="outra %"
                  :class="[classeCampo, '!w-32 pr-8 text-right tabular-nums', erros.percentual && classeErro]"
                  :aria-invalid="erros.percentual ? 'true' : undefined"
                  @blur="ajustarPercentual"
                />
                <span
                  class="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-sm text-muted-foreground"
                  aria-hidden="true"
                >%</span>
              </div>
            </NfseCampo>

            <div class="grid gap-3 sm:grid-cols-2">
              <!-- Empresa sem % padrão: o campo é obrigatório, como antes -->
              <NfseCampo
                v-if="!pctEmpresa"
                :class="CAMPO_EM_GRADE"
                id="nfse-modelo-campo-percentual"
                rotulo="Percentual"
                obrigatorio
                :erro="erros.percentual"
                dica="Aceita vírgula: 0,5 = meio por cento."
                para="nfse-modelo-percentual"
              >
                <div class="relative inline-flex w-fit">
                  <input
                    id="nfse-modelo-percentual"
                    v-model="form.percentual"
                    type="text"
                    inputmode="decimal"
                    autocomplete="off"
                    maxlength="12"
                    placeholder="0,5"
                    :class="[classeCampo, '!w-32 pr-8 text-right tabular-nums', erros.percentual && classeErro]"
                    :aria-invalid="erros.percentual ? 'true' : undefined"
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

            <p v-if="!pctEmpresa && empresa && !somenteLeitura" class="text-xs text-muted-foreground">
              A {{ empresa.apelido }} não tem % padrão.
              <template v-if="tela.podeEditarCadastroEmpresa.value">
                Se todas as notas de percentual dela usam a mesma %, dá para cadastrar em
                <a
                  :href="`/companies/${empresa.company_id}`"
                  target="_blank"
                  rel="noopener"
                  class="inline-flex items-center gap-0.5 text-primary hover:underline"
                >Cadastros › Empresas<ExternalLink class="size-3" aria-hidden="true" /></a>
                e usar aqui a da empresa.
              </template>
              <template v-else>
                Se todas as notas de percentual dela usam a mesma %, peça para quem cuida de Cadastros › Empresas
                cadastrar a % da empresa.
              </template>
            </p>

            <div
              class="flex items-start gap-2 rounded-md border bg-muted/40 px-3 py-2 text-sm"
              aria-live="polite"
            >
              <Calculator class="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
              <div class="min-w-0 space-y-0.5">
                <p v-if="exemplo" class="tabular-nums">
                  <span class="text-muted-foreground">{{
                    exemplo.origem === 'faturamento' ? 'Prévia:' : exemplo.origem === 'sugerida' ? 'Com a base sugerida:' : 'Ex.:'
                  }}</span>
                  {{ exemplo.texto }}
                </p>
                <p v-else-if="erroPctConta" :class="TOM_TEXTO.perigo">{{ erroPctConta }}</p>
                <p v-else class="text-muted-foreground">Digite o percentual para ver a conta.</p>
                <p v-if="exemplo?.pouco" class="text-xs text-amber-700 dark:text-amber-400">
                  Com essa base a nota daria menos de R$ 0,01: na hora de emitir, use uma base maior.
                </p>
                <p
                  v-else-if="empresa && faturamentoCarregado && !baseFaturamento"
                  class="text-xs text-muted-foreground"
                >
                  {{
                    faturamentoMes
                      ? `A ${empresa.apelido} não teve venda em ${fmtMes(mes)}: na hora de emitir, digite a base.`
                      : `A ${empresa.apelido} não tem loja com o CNPJ dela em Cadastros › Lojas: na hora de emitir, digite a base.`
                  }}
                </p>
                <p v-else class="text-xs text-muted-foreground">
                  Na hora de emitir a base é o faturamento do mês (dá para trocar); valor = base × percentual.
                </p>
              </div>
            </div>
          </template>

          <details
            id="nfse-modelo-campo-inf_comp"
            class="group rounded-lg border"
            :open="abrirComplemento"
            @toggle="aoAlternarDetalhe('complemento', $event)"
          >
            <summary
              class="flex cursor-pointer list-none items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium hover:bg-muted/40 [&::-webkit-details-marker]:hidden"
            >
              <ChevronRight class="size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-90" aria-hidden="true" />
              Informações complementares
              <span class="font-normal text-muted-foreground">(opcional)</span>
            </summary>
            <div class="space-y-1.5 border-t px-3 py-3">
              <textarea
                id="nfse-modelo-inf_comp"
                v-model="form.inf_comp"
                rows="2"
                maxlength="2000"
                aria-label="informações complementares"
                :class="[classeTexto, erros.inf_comp && classeErro]"
              />
              <p v-if="erros.inf_comp" class="text-xs text-red-600 dark:text-red-400" role="alert">{{ erros.inf_comp }}</p>
              <p v-else class="text-xs text-muted-foreground">Texto extra que sai no fim da nota.</p>
            </div>
          </details>
        </NfseSecao>

        <!-- 3. Códigos do serviço (avançado) -->
        <details
          id="nfse-modelo-codigos"
          class="group rounded-lg border"
          :open="abrirCodigos"
          @toggle="aoAlternarDetalhe('codigos', $event)"
        >
          <summary
            class="flex cursor-pointer list-none items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium hover:bg-muted/40 [&::-webkit-details-marker]:hidden"
          >
            <ChevronRight class="size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-90" aria-hidden="true" />
            Códigos do serviço
            <span class="font-normal text-muted-foreground">(avançado)</span>
            <span v-if="codigosProprios" class="pill-info ml-auto">códigos próprios</span>
          </summary>
          <div class="space-y-3 border-t px-3 py-3">
            <p class="text-xs text-muted-foreground">{{ codigosEmpresa }}</p>
            <NfseSwitch
              v-model="codigosProprios"
              rotulo="Usar códigos diferentes nesta nota fixa"
              dica="Desligar volta para os códigos da empresa (os daqui são apagados)."
              :disabled="somenteLeitura || salvando"
            />
            <div v-if="codigosProprios" class="grid gap-3 sm:grid-cols-3">
              <NfseCampo
                :class="CAMPO_EM_GRADE"
                id="nfse-modelo-campo-city_service_code"
                rotulo="Código do serviço na prefeitura"
                :erro="erros.city_service_code"
                dica="Vazio = o da empresa. Ex.: 6303"
                para="nfse-modelo-city_service_code"
              >
                <input
                  id="nfse-modelo-city_service_code"
                  :value="form.city_service_code ?? ''"
                  type="text"
                  maxlength="20"
                  autocomplete="off"
                  :placeholder="empresa?.fiscal?.city_service_code || '6303'"
                  :class="[classeCampo, 'tabular-nums', erros.city_service_code && classeErro]"
                  @input="digitarCodigo('city_service_code', $event)"
                />
              </NfseCampo>
              <NfseCampo
                :class="CAMPO_EM_GRADE"
                id="nfse-modelo-campo-federal_service_code"
                rotulo="Item da lista de serviços (LC 116)"
                :erro="erros.federal_service_code"
                dica="Vazio = o da empresa. Ex.: 10.05"
                para="nfse-modelo-federal_service_code"
              >
                <input
                  id="nfse-modelo-federal_service_code"
                  :value="form.federal_service_code ?? ''"
                  type="text"
                  inputmode="decimal"
                  maxlength="10"
                  autocomplete="off"
                  :placeholder="empresa?.fiscal?.federal_service_code || '10.05'"
                  :class="[classeCampo, 'tabular-nums', erros.federal_service_code && classeErro]"
                  @input="digitarCodigo('federal_service_code', $event)"
                />
              </NfseCampo>
              <NfseCampo
                :class="CAMPO_EM_GRADE"
                id="nfse-modelo-campo-c_nbs"
                rotulo="Código NBS"
                opcional
                :erro="erros.c_nbs"
                dica="9 dígitos"
                para="nfse-modelo-c_nbs"
              >
                <input
                  id="nfse-modelo-c_nbs"
                  :value="form.c_nbs ?? ''"
                  type="text"
                  inputmode="numeric"
                  maxlength="9"
                  autocomplete="off"
                  :placeholder="empresa?.fiscal?.c_nbs || '000000000'"
                  :class="[classeCampo, 'tabular-nums', erros.c_nbs && classeErro]"
                  @input="digitarCodigo('c_nbs', $event)"
                />
              </NfseCampo>
            </div>
          </div>
        </details>
      </fieldset>
    </form>

    <template #rodape>
      <NfseSwitch
        v-if="!somenteLeitura"
        v-model="form.ativo"
        rotulo="Ativa: aparece em Emitir do mês"
        :disabled="salvando"
      />
      <span v-else />
      <div class="flex items-center gap-2">
        <Button
          v-if="editando && original && tela.canDelete.value && !somenteLeitura"
          type="button"
          variant="ghost"
          size="sm"
          class="text-red-700 hover:text-red-700 dark:text-red-400"
          :disabled="salvando"
          @click="excluir"
        >
          <Trash2 class="mr-1.5 size-4" aria-hidden="true" /> Excluir
        </Button>
        <Button type="button" variant="outline" size="sm" :disabled="salvando" @click="cancelar">
          {{ somenteLeitura ? 'Fechar' : 'Cancelar' }}
        </Button>
        <Button v-if="!somenteLeitura" type="submit" form="nfse-modelo-form" size="sm" :disabled="salvando">
          <Loader2 v-if="salvando" class="mr-1.5 size-4 animate-spin" aria-hidden="true" />
          Salvar nota fixa
        </Button>
      </div>
    </template>
  </NfseSheet>
</template>
