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
//
// Várias empresas de uma vez (01/10/2026, Eduardo: "nova nota fixa queria poder
// selecionar várias empresas em massa, para fazer de uma vez só"): ao CRIAR, o
// campo "Empresa que emite" aceita várias (NfseEmpresasMultiSelect). Tomador,
// nome, descrição, valor/%, base, complemento e códigos são os mesmos; salvar
// cria UMA nota fixa por empresa, uma de cada vez, com "criando 3 de 12". As que
// deram certo saem da lista (não dá para marcar de novo nesta abertura, para não
// duplicar); as que falharam ficam, com o motivo, e "tentar de novo" só refaz
// essas. Percentual: "Usar a % de cada empresa" salva percentual = null (cada
// nota fixa acompanha a sua empresa); empresa sem % padrão exige "Outra %" ou
// sair da lista. Códigos vazios = os de cada empresa. Ao EDITAR, uma só, como
// antes. Quem abre continua recebendo UMA nota fixa: a primeira criada.
import { computed, nextTick, ref, watch } from 'vue'
import { Banknote, Building2, Calculator, ChevronRight, ExternalLink, FileText, Loader2, Percent, RotateCcw, Trash2 } from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import {
  calcularPercentual, campoDoErro, codigoErro, erroApi, fmtBrl, fmtMes, fmtPct, fmtPctOrigem, inserirNoCursor,
  mesAtual, mesParaData, modeloParaForm, paraDecimal, pctPositivo, pendenciaTexto, plural, prestadorPorId, soDigitos,
  TOM_TEXTO, tomadorEstiloNfeio, tomadorNaNota, useNfseTela,
  type AbrirModeloOpts, type FaturamentoEmpresa, type Modelo, type ModeloApi, type ModeloForm, type OrigemPct,
  type Prestador,
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

// Várias empresas (01/10/2026): ao criar, as empresas marcadas (na ordem em que
// foram marcadas); form.company_id acompanha a 1ª. As já criadas nesta abertura
// e as que falharam na última tentativa (com o motivo).
const empresasIds = ref<string[]>([])
const criadas = ref<Modelo[]>([])
type Falha = { company_id: string; apelido: string; motivo: string }
const falhas = ref<Falha[]>([])
const progresso = ref<{ feito: number; total: number } | null>(null)
// 01/10/2026 (revisão): cada abertura tem um número; a criação em lote para se a
// gaveta for reaberta no meio (não mexe na abertura nova). As notas fixas que já
// existiam ao abrir: o que aparecer depois, da mesma empresa para o mesmo tomador,
// foi criado daqui (mesmo que a resposta tenha se perdido no caminho).
let sessao = 0
let idsAntes = new Set<string>()
// Duplicar: a empresa da nota original já vem marcada (avisa para desmarcar).
const empresaOriginal = ref<string | null>(null)

// Percentual: usar a % da empresa (se ela tiver) ou "outra %", a desta nota fixa.
type ModoPct = 'empresa' | 'outra'
const modoPct = ref<ModoPct>('empresa')

const somenteLeitura = computed(() => !tela.canEdit.value)
const editando = computed(() => !!form.value.id)
// Criando: dá para marcar várias empresas. Editando: uma só, como antes.
const multi = computed(() => !editando.value)
const varias = computed(() => multi.value && empresasIds.value.length > 1)

const titulo = computed(() => {
  if (tituloPedido.value) return tituloPedido.value
  if (somenteLeitura.value) return 'Nota fixa'
  return editando.value ? 'Editar nota fixa' : 'Nova nota fixa'
})

// --- Empresa e tomador ----------------------------------------------------------

// Com várias empresas, `empresa` (a única) fica null: cada trecho da tela que
// fala de "a empresa" tem a sua versão para várias.
const empresa = computed(() => (varias.value ? null : prestadorPorId(tela.prestadores.value, form.value.company_id)))
const tomador = computed(() => tela.tomadores.value.find((t) => t.id === form.value.tomador_id) ?? null)

watch(empresasIds, (ids) => {
  if (multi.value) form.value.company_id = ids[0] ?? null
})

// As empresas que vão emitir (uma ao editar; as marcadas ao criar).
const idsAlvo = computed<string[]>(() =>
  multi.value ? empresasIds.value : form.value.company_id ? [form.value.company_id] : [],
)
const empresasEscolhidas = computed(() =>
  empresasIds.value.map((id) => prestadorPorId(tela.prestadores.value, id)).filter((p): p is Prestador => !!p),
)
function apelidoDe(id: string): string {
  return prestadorPorId(tela.prestadores.value, id)?.apelido || 'empresa'
}
// "A, B e C"
function juntarNomes(ps: { apelido: string }[]): string {
  const n = ps.map((p) => p.apelido)
  return n.length > 1 ? `${n.slice(0, -1).join(', ')} e ${n[n.length - 1]}` : n[0] ?? ''
}

// A empresa do grupo que é o tomador (ou null): ela não pode estar entre as que emitem.
const empresaDoTomador = computed(() =>
  tomador.value?.tipo === 'grupo' && tomador.value.company_id ? tomador.value.company_id : null,
)
const tomadorIgualEmpresa = computed(() => !!empresaDoTomador.value && idsAlvo.value.includes(empresaDoTomador.value))
const msgTomadorIgual = computed(() =>
  varias.value && empresaDoTomador.value
    ? `O tomador é a ${apelidoDe(empresaDoTomador.value)}, que está entre as empresas que emitem: tire ela da lista.`
    : 'O tomador não pode ser a própria empresa que emite.',
)
// No seletor de várias não aparecem a empresa do tomador nem as já criadas aqui.
const excluirDaEscolha = computed(() => [
  ...(empresaDoTomador.value ? [empresaDoTomador.value] : []),
  ...criadas.value.map((m) => m.company_id),
])

const pendenciasEmpresa = computed(() => (empresa.value?.pendencias ?? []).map(pendenciaTexto))
// Várias: as marcadas que ainda não podem emitir.
const naoProntas = computed(() => (varias.value ? empresasEscolhidas.value.filter((p) => !p.pronto) : []))
function pendenciasDe(p: Prestador): string {
  const t = p.pendencias.map((x) => pendenciaTexto(x).texto.replace(/[.]$/, ''))
  return t.length ? t.join('; ') : 'ligar a empresa à NFE.io'
}

function nomeCurtoTomador(): string {
  const t = tomador.value
  if (!t) return ''
  if (t.tipo === 'grupo') return prestadorPorId(tela.prestadores.value, t.company_id)?.apelido || t.nome_nota || ''
  return t.nome_nota || t.nome || ''
}

const subtitulo = computed(() => {
  if (varias.value) {
    const n = plural(empresasIds.value.length, 'empresa', 'empresas')
    return tomador.value ? `${n} → ${nomeCurtoTomador()} (uma nota fixa para cada)` : `${n}: uma nota fixa para cada, com o mesmo tomador e o mesmo texto.`
  }
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
  if (varias.value) {
    const sem = empresasEscolhidas.value.filter((p) => !p.fiscal?.city_service_code)
    return (
      'Por padrão, cada nota fixa usa os códigos do serviço da empresa que emite.' +
      (sem.length ? ` Ainda sem código do serviço salvo (aba Empresas): ${juntarNomes(sem)}.` : '')
    )
  }
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
// Várias (01/10/2026): a opção "da empresa" aparece se ao menos uma tem % padrão;
// as que não têm ficam em semPctVarias (exigem "Outra %" ou sair da lista).
const semPctVarias = computed(() =>
  varias.value ? empresasEscolhidas.value.filter((p) => !pctPositivo(p.percentual_servico)) : [],
)
const temPctEmpresa = computed(() =>
  varias.value ? semPctVarias.value.length < empresasEscolhidas.value.length : !!pctEmpresa.value,
)
const usaPctEmpresa = computed(() => ehPercentual.value && temPctEmpresa.value && modoPct.value === 'empresa')
// A % que vale nesta nota fixa: a da empresa ou a digitada (formato da API).
// Várias usando a de cada uma: não há uma % só (null).
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
  if (varias.value) {
    const sem = semPctVarias.value
    return [
      {
        valor: 'empresa',
        titulo: 'Usar a % de cada empresa',
        descricao: sem.length
          ? `${juntarNomes(sem)} ${sem.length === 1 ? 'não tem' : 'não têm'} % padrão: para usar esta opção, tire ${sem.length === 1 ? 'ela' : 'elas'} da lista.`
          : 'Cada nota fixa usa a % cadastrada da sua empresa (Cadastros › Empresas). Se mudar lá, muda aqui.',
      },
      { valor: 'outra', titulo: 'Outra %', descricao: 'A mesma % para todas as empresas marcadas.' },
    ]
  }
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
// 01/10/2026: guarda o faturamento de todas (a API já devolve todas) para a
// prévia de cada empresa quando há várias marcadas.
const faturamentoLista = ref<FaturamentoEmpresa[] | null>(null)
const faturamentoCarregado = ref(false)
let seqFaturamento = 0
// Várias fontes (comparadas uma a uma): marcar/desmarcar empresa não busca de novo.
watch(
  [aberto, () => idsAlvo.value.length > 0, () => form.value.tipo_valor],
  async ([ab, temEmpresa, tipo]) => {
    const minha = ++seqFaturamento
    faturamentoLista.value = null
    faturamentoCarregado.value = false
    if (!ab || !temEmpresa || tipo !== 'percentual') return
    const r = await api<{ empresas: FaturamentoEmpresa[] }>(
      `/api/nfse/faturamento?competencia=${mesParaData(mes)}`,
    ).catch(() => null)
    if (minha !== seqFaturamento) return
    faturamentoLista.value = r?.empresas ?? null
    faturamentoCarregado.value = !!r
  },
  { immediate: true },
)
const faturamentoMes = computed(
  () => faturamentoLista.value?.find((e) => e.company_id === form.value.company_id) ?? null,
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

// Várias: a prévia de cada empresa (a % dela ou a outra %, sobre o faturamento
// do mês dela; sem venda, a base sugerida; sem nenhuma, só a %).
const previaVarias = computed(() => {
  if (!varias.value || !ehPercentual.value) return []
  const sugerida = baseApi.value && Number(baseApi.value) > 0 ? baseApi.value : ''
  return empresasEscolhidas.value.map((p) => {
    const pct = usaPctEmpresa.value ? pctPositivo(p.percentual_servico) : pctApi.value
    const linha = faturamentoLista.value?.find((e) => e.company_id === p.company_id)
    const fat = paraDecimal(linha?.valor)
    const baseFat = fat && Number(fat) > 0 ? fat : ''
    const base = baseFat || sugerida
    const valor = pct && base ? calcularPercentual(base, pct) : null
    // Fora do faturamento = não tem loja com o CNPJ dela (igual à tela de uma empresa).
    const semLoja = faturamentoCarregado.value && !linha
    return { p, pct, base, deFaturamento: !!baseFat, valor, pouco: !!valor && Number(valor) < 0.01, semLoja }
  })
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
    empresas: multi.value ? empresasIds.value : null,
  })
}

const sujo = computed(() => aberto.value && !somenteLeitura.value && retrato() !== inicial.value)

function terminar(m: Modelo | null) {
  const r = resolver
  resolver = null
  r?.(m)
}

// Quem abriu espera UMA nota fixa: com várias criadas, a 1ª (com os nomes da lista recarregada).
function primeiraCriada(): Modelo | null {
  const m = criadas.value[0]
  return m ? (tela.modelos.value.find((x) => x.id === m.id) ?? m) : null
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
  // Criando: a empresa do preset (Duplicar, Emitir do mês…) já vem marcada.
  empresasIds.value = !form.value.id && form.value.company_id ? [form.value.company_id] : []
  empresaOriginal.value = !o?.modelo && o?.preset?.company_id ? o.preset.company_id : null
  sessao++
  idsAntes = new Set(tela.modelos.value.map((m) => m.id))
  criadas.value = []
  falhas.value = []
  progresso.value = null
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
  // Saindo depois de criar algumas (e desistir das que falharam): devolve a 1ª criada.
  terminar(m ?? primeiraCriada())
}

// A gaveta já perguntou "Sair sem salvar?" quando precisava.
function aoMudar(v: boolean) {
  if (!v) fechar(null)
}

// O mesmo texto no Cancelar e no X / Esc / clique fora (a gaveta pergunta sozinha).
const textoSair = computed(() =>
  criadas.value.length
    ? `${criadas.value.length === 1 ? 'A nota fixa já criada continua salva' : `As ${criadas.value.length} notas fixas já criadas continuam salvas`}; as empresas que faltam ficam sem.`
    : 'O que você mudou vai se perder.',
)

async function cancelar() {
  if (sujo.value) {
    const ok = await tela.confirmar({
      titulo: 'Sair sem salvar?',
      texto: textoSair.value,
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
  if (!idsAlvo.value.length) e.company_id = multi.value ? 'Escolha a empresa que emite (dá para marcar várias).' : 'Escolha a empresa que emite.'
  if (!f.tomador_id) e.tomador_id = 'Escolha o tomador.'
  else if (tomadorIgualEmpresa.value) e.tomador_id = msgTomadorIgual.value
  if (!f.nome.trim()) e.nome = 'Dê um nome.'
  if (!f.descricao.trim()) e.descricao = 'Escreva a descrição.'
  if (f.tipo_valor === 'percentual') {
    if (!usaPctEmpresa.value) {
      const { pct: digitado, erro } = lerPercentual(f.percentual)
      if (erro) e.percentual = erro
      else if (!digitado) {
        e.percentual = temPctEmpresa.value
          ? 'Digite a outra %. Ex.: 0,5 para 0,5%.'
          : 'Digite o percentual. Ex.: 0,5 para 0,5%.'
      }
    } else if (semPctVarias.value.length) {
      // Várias usando a % de cada uma: toda empresa marcada precisa ter a dela.
      const sem = semPctVarias.value
      e.percentual = `${juntarNomes(sem)} ${sem.length === 1 ? 'não tem' : 'não têm'} % padrão: escolha "Outra %" ou tire ${sem.length === 1 ? 'ela' : 'elas'} da lista.`
    }
    // Várias usando a de cada uma: a conta da base sugerida vale para cada %.
    const pcts =
      varias.value && usaPctEmpresa.value
        ? empresasEscolhidas.value.map((p) => pctPositivo(p.percentual_servico)).filter((x): x is string => !!x)
        : pctEfetivo.value ? [pctEfetivo.value] : []
    const base = paraDecimal(f.base_padrao)
    if (base && Number(base) <= 0) e.base_padrao = 'A base precisa ser maior que zero (ou deixe em branco).'
    else if (base && pcts.some((pct) => !(Number(calcularPercentual(base, pct)) >= 0.01))) {
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
  [form, codigosProprios, modoPct, empresasIds],
  () => {
    if (tentouSalvar.value) erros.value = validar()
  },
  { deep: true },
)

// Erro que aparece na hora, sem esperar o salvar.
const erroTomador = computed(
  () => erros.value.tomador_id || (tomadorIgualEmpresa.value ? msgTomadorIgual.value : null),
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

// O campo da tela a que o erro da API se refere (ou undefined).
function campoDaFalha(err: unknown, pct: boolean): Campo | undefined {
  let campo = (campoDoErro(err) ?? CAMPO_POR_CODIGO[codigoErro(err) ?? '']) as Campo | undefined
  // No percentual não há campo "valor" na tela: o erro vai para o percentual.
  if (campo === 'valor' && pct) campo = 'percentual'
  else if ((campo === 'percentual' || campo === 'base_padrao') && !pct) campo = 'valor'
  return campo && ORDEM.includes(campo) ? campo : undefined
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

  // 01/10/2026: criando com várias empresas (ou refazendo as que falharam).
  if (multi.value && (empresasIds.value.length > 1 || criadas.value.length || falhas.value.length)) {
    await criarVarias(corpo, pct)
    return
  }

  falhas.value = []
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
    const campo = campoDaFalha(err, pct)
    if (campo) {
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

// Notas fixas que apareceram na lista desde que a gaveta abriu, destas empresas
// para este tomador, e que ainda não estão em `criadas`: o POST gravou mas a
// resposta não chegou (502/504, rede caiu). Contam como criadas: nada em dobro.
function gravadasMesmoComErro(tomadorId: unknown, cids: string[]): Modelo[] {
  const ja = new Set(criadas.value.map((m) => m.id))
  const achadas: Modelo[] = []
  for (const cid of cids) {
    const m = tela.modelos.value.find(
      (x) => !idsAntes.has(x.id) && !ja.has(x.id) && x.company_id === cid && x.tomador_id === tomadorId,
    )
    if (m) achadas.push(m)
  }
  return achadas
}

// Uma nota fixa por empresa, uma de cada vez ("criando 3 de 12"). Cada uma que
// dá certo entra em `criadas` na hora (e não dá para marcar de novo nesta
// abertura: nada em dobro); as que falharam ficam marcadas, com o motivo, para
// tentar de novo. A gaveta não fecha enquanto cria (:fechavel="!salvando").
async function criarVarias(corpo: Record<string, unknown>, pct: boolean) {
  const minha = sessao
  const erradas: (Falha & { campo?: Campo })[] = []
  let novas = 0
  salvando.value = true
  try {
    // Tentando de novo: a que "falhou" pode ter sido gravada assim mesmo.
    if (falhas.value.length) {
      await tela.recarregar()
      if (minha !== sessao) return
      const achadas = gravadasMesmoComErro(corpo.tomador_id, empresasIds.value)
      criadas.value = [...criadas.value, ...achadas]
      novas += achadas.length
    }
    falhas.value = []
    const feitasAntes = new Set(criadas.value.map((m) => m.company_id))
    const alvos = empresasIds.value.filter((id) => !feitasAntes.has(id))
    for (const [i, cid] of alvos.entries()) {
      progresso.value = { feito: i, total: alvos.length }
      try {
        const m = await api<Modelo>('/api/nfse/modelos', { method: 'POST', body: { ...corpo, company_id: cid } })
        if (minha !== sessao) break
        criadas.value = [...criadas.value, m]
        novas++
      } catch (err) {
        if (minha !== sessao) break
        erradas.push({ company_id: cid, apelido: apelidoDe(cid), motivo: erroApi(err), campo: campoDaFalha(err, pct) })
      }
    }
    await tela.recarregar()
    // Reaberta no meio (outra nota fixa): a lista já mostra as criadas; não mexe na gaveta nova.
    if (minha !== sessao) return

    // Falhou no navegador mas gravou no servidor: conta como criada.
    const achadas = erradas.length ? gravadasMesmoComErro(corpo.tomador_id, erradas.map((x) => x.company_id)) : []
    criadas.value = [...criadas.value, ...achadas]
    novas += achadas.length
    const restantes = erradas.filter((x) => !achadas.some((m) => m.company_id === x.company_id))

    // Só troca a lista se alguma saiu: trocar à toa dispara a validação de novo
    // e apaga o erro da API que vai no campo (logo abaixo).
    const feitas = new Set(criadas.value.map((m) => m.company_id))
    if (empresasIds.value.some((id) => feitas.has(id))) {
      empresasIds.value = empresasIds.value.filter((id) => !feitas.has(id))
    }

    if (!restantes.length) {
      const total = criadas.value.length
      toasts.success(total === 1 ? 'Nota fixa salva' : `${plural(total, 'nota fixa criada', 'notas fixas criadas')}`)
      fechar(primeiraCriada())
      return
    }

    falhas.value = restantes.map((x) => ({ company_id: x.company_id, apelido: x.apelido, motivo: x.motivo }))
    if (novas) {
      toasts.warning(
        `${plural(novas, 'nota fixa criada', 'notas fixas criadas')}; ${plural(restantes.length, 'empresa falhou', 'empresas falharam')}`,
      )
    }
    // Todas falharam pelo mesmo campo da tela (ex.: descrição): marca o campo.
    const campo = restantes[0]!.campo
    if (!novas && campo && campo !== 'company_id' && restantes.every((x) => x.campo === campo)) {
      erros.value = { ...erros.value, [campo]: restantes[0]!.motivo }
      irParaCampo(campo)
    } else {
      nextTick(() => sheet.value?.rolarPara('nfse-modelo-resultado'))
    }
  } finally {
    if (minha === sessao) {
      salvando.value = false
      progresso.value = null
    }
  }
}

// O botão do resumo: refaz as marcadas (normalmente só as que falharam).
function tentarDeNovo() {
  salvar()
}

// No resumo, só as que falharam e continuam marcadas (tirou da lista, some daqui).
const falhasVisiveis = computed(() => falhas.value.filter((x) => empresasIds.value.includes(x.company_id)))

// O texto acompanha o que o botão faz: marcou outras depois da falha, cria todas as marcadas.
const textoTentar = computed(() => {
  const n = empresasIds.value.length
  if (n === falhasVisiveis.value.length) {
    return n === 1 ? 'tentar de novo a que falhou' : `tentar de novo as ${n} que falharam`
  }
  return n === 1 ? 'criar a marcada' : `criar as ${n} marcadas`
})

const tituloResultado = computed(() => {
  const c = criadas.value.length
  const f = falhasVisiveis.value.length
  const feitas = plural(c, 'nota fixa criada', 'notas fixas criadas')
  if (!f) return feitas
  const falharam = plural(f, 'empresa falhou', 'empresas falharam')
  return c ? `${feitas}; ${falharam}` : `Nenhuma nota fixa criada: ${falharam}`
})

// Dica do campo das empresas. Duplicar: a da nota original já vem marcada e,
// diferente do seletor de uma só, marcar outra SOMA (não troca).
const dicaEmpresas = computed(() => {
  if (!multi.value) return undefined
  const orig = empresaOriginal.value
  if (orig && empresasIds.value.includes(orig)) {
    return `A ${apelidoDe(orig)} já vem marcada (é a da nota original). Se a cópia for só para outra empresa, desmarque ela.`
  }
  return empresasIds.value.length < 2 ? 'Dá para marcar várias: sai uma nota fixa para cada, com o resto igual.' : undefined
})

const textoSalvar = computed(() => {
  if (!multi.value || empresasIds.value.length < 2) return 'Salvar nota fixa'
  return `Criar ${empresasIds.value.length} notas fixas`
})

async function corrigirEmpresa(alvo?: Prestador) {
  const p = alvo ?? empresa.value
  if (!p) return
  const pend = p.pendencias.map(pendenciaTexto)
  await tela.abrirEmpresa(p.company_id, pend.find((x) => x.alvo === 'empresa')?.foco)
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
    :fechavel="!salvando"
    :texto-sujo="textoSair"
    @update:open="aoMudar"
  >
    <template #cabecalho-extra>
      <span v-if="editando && !form.ativo" class="pill-muted">desativada</span>
      <span v-if="ehPercentual && pctEfetivo" class="pill-info tabular-nums">
        {{ fmtPct(pctEfetivo) }} da base{{ usaPctEmpresa ? ' (da empresa)' : '' }}
      </span>
      <span v-else-if="ehPercentual && varias && usaPctEmpresa" class="pill-info">a % de cada empresa</span>
      <span v-if="varias" class="pill-info tabular-nums">{{ plural(empresasIds.length, 'empresa', 'empresas') }}</span>
    </template>

    <form id="nfse-modelo-form" class="space-y-5" novalidate @submit.prevent="salvar">
      <NfseAviso v-if="erroGeral" id="nfse-modelo-erro" tom="perigo" titulo="Não deu para salvar a nota fixa">
        {{ erroGeral }}
      </NfseAviso>

      <!-- Várias empresas (01/10/2026): progresso e resumo -->
      <NfseAviso v-if="progresso" tom="info" compacto>
        <span aria-live="polite" class="tabular-nums">
          Criando {{ Math.min(progresso.feito + 1, progresso.total) }} de {{ progresso.total }}…
        </span>
      </NfseAviso>
      <NfseAviso
        v-else-if="criadas.length || falhasVisiveis.length"
        id="nfse-modelo-resultado"
        :tom="falhasVisiveis.length ? (criadas.length ? 'atencao' : 'perigo') : 'sucesso'"
        :titulo="tituloResultado"
      >
        <p v-if="criadas.length" class="text-xs">
          Criadas: {{ juntarNomes(criadas.map((m) => ({ apelido: apelidoDe(m.company_id) }))) }}.
          <template v-if="falhasVisiveis.length">Elas já estão salvas e não vão de novo.</template>
        </p>
        <template v-if="falhasVisiveis.length">
          <p class="text-xs">Continuam marcadas só as que falharam:</p>
          <ul class="list-disc space-y-0.5 pl-5 text-xs">
            <li v-for="x in falhasVisiveis" :key="x.company_id">
              <span class="font-medium">{{ x.apelido }}</span>: {{ x.motivo }}
            </li>
          </ul>
          <p v-if="criadas.length" class="text-xs text-muted-foreground">
            Tentar de novo usa o formulário como está agora; as já criadas não mudam.
          </p>
        </template>
        <template v-if="falhasVisiveis.length && !somenteLeitura" #acoes>
          <Button type="button" size="sm" variant="outline" class="h-8" :disabled="salvando" @click="tentarDeNovo">
            <RotateCcw class="mr-1.5 size-3.5" aria-hidden="true" />
            {{ textoTentar }}
          </Button>
        </template>
      </NfseAviso>
      <NfseAviso v-if="somenteLeitura" tom="neutro" compacto>
        Você pode ver esta nota fixa, mas não tem permissão para mudar.
      </NfseAviso>

      <fieldset :disabled="somenteLeitura || salvando" class="min-w-0 space-y-5">
        <!-- 1. Quem emite e quem recebe -->
        <NfseSecao titulo="Quem emite e quem recebe" :icone="Building2">
          <NfseCampo
            id="nfse-modelo-campo-company_id"
            :rotulo="multi ? 'Empresas que emitem' : 'Empresa que emite'"
            obrigatorio
            :erro="erros.company_id"
            :dica="dicaEmpresas"
            para="nfse-modelo-empresa"
          >
            <!-- Criando: várias de uma vez (01/10/2026). Editando: uma só. -->
            <NfseEmpresasMultiSelect
              v-if="multi"
              id="nfse-modelo-empresa"
              v-model="empresasIds"
              filtro="uteis"
              mostrar-prontidao
              :excluir-ids="excluirDaEscolha"
              :disabled="somenteLeitura || salvando"
              :invalido="!!erros.company_id"
            >
              <template #extra="{ empresa: p }">
                <template v-if="ehPercentual && usaPctEmpresa">
                  <span v-if="pctPositivo(p.percentual_servico)" class="pill-info shrink-0 tabular-nums">
                    {{ fmtPct(p.percentual_servico) }}
                  </span>
                  <!-- Sem % padrão: atalho para cadastrar (igual à tela de uma empresa) -->
                  <a
                    v-else-if="tela.podeEditarCadastroEmpresa.value"
                    :href="`/companies/${p.company_id}`"
                    target="_blank"
                    rel="noopener"
                    class="pill-danger inline-flex shrink-0 items-center gap-0.5 hover:underline"
                    :title="`cadastrar a % da ${p.apelido} em Cadastros › Empresas`"
                  >sem % padrão<ExternalLink class="size-3" aria-hidden="true" /></a>
                  <span v-else class="pill-danger shrink-0">sem % padrão</span>
                </template>
              </template>
            </NfseEmpresasMultiSelect>
            <NfseEmpresaSelect
              v-else
              id="nfse-modelo-empresa"
              v-model="form.company_id"
              filtro="uteis"
              mostrar-prontidao
              :disabled="somenteLeitura || salvando"
              :invalido="!!erros.company_id"
            />
          </NfseCampo>

          <NfseAviso
            v-if="naoProntas.length"
            tom="atencao"
            :titulo="plural(naoProntas.length, 'empresa ainda não pode emitir', 'empresas ainda não podem emitir')"
          >
            <ul class="space-y-1">
              <li v-for="p in naoProntas" :key="p.company_id" class="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                <span><span class="font-medium">{{ p.apelido }}</span>: {{ pendenciasDe(p) }}.</span>
                <button type="button" class="text-xs text-primary hover:underline" @click="corrigirEmpresa(p)">
                  corrigir
                </button>
              </li>
            </ul>
            <p>Dá para criar as notas fixas agora e ajustar as empresas depois.</p>
          </NfseAviso>

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
              <Button type="button" size="sm" variant="outline" class="h-8" @click="corrigirEmpresa()">
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
              :excluir-empresa-id="varias ? null : form.company_id"
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
              v-if="temPctEmpresa"
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
                v-if="!temPctEmpresa"
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

            <p v-if="varias && !temPctEmpresa && !somenteLeitura" class="text-xs text-muted-foreground">
              Nenhuma das empresas marcadas tem % padrão: a % digitada vale para todas.
            </p>
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
              <!-- Várias (01/10/2026): a prévia de cada empresa -->
              <div v-if="varias" class="min-w-0 flex-1 space-y-1">
                <p class="text-muted-foreground">
                  Prévia de cada empresa (faturamento de {{ fmtMes(mes) }}):
                </p>
                <p v-if="erroPctConta" :class="TOM_TEXTO.perigo">{{ erroPctConta }}</p>
                <ul class="max-h-48 space-y-0.5 overflow-y-auto text-xs">
                  <li v-for="x in previaVarias" :key="x.p.company_id" class="flex flex-wrap gap-x-1.5 tabular-nums">
                    <span class="font-medium">{{ x.p.apelido }}:</span>
                    <span v-if="!x.pct" :class="usaPctEmpresa ? TOM_TEXTO.perigo : 'text-muted-foreground'">
                      {{ usaPctEmpresa ? 'sem % padrão (use "Outra %" ou tire ela da lista)' : 'digite a % para ver a conta' }}
                    </span>
                    <span v-else-if="x.valor">
                      {{ fmtPct(x.pct) }} de {{ fmtBrl(x.base) }}{{ x.deFaturamento ? '' : ' (base sugerida)' }} = {{ fmtBrl(x.valor) }}
                      <span v-if="x.pouco" class="text-amber-700 dark:text-amber-400">· menos de R$ 0,01</span>
                    </span>
                    <span v-else class="text-muted-foreground">
                      {{ fmtPct(x.pct) }} ·
                      {{
                        !faturamentoCarregado
                          ? 'carregando o faturamento…'
                          : x.semLoja
                            ? 'não tem loja com o CNPJ dela em Cadastros › Lojas: na hora de emitir, digite a base'
                            : `sem venda em ${fmtMes(mes)}: na hora de emitir, digite a base`
                      }}
                    </span>
                  </li>
                </ul>
                <p class="text-xs text-muted-foreground">
                  Na hora de emitir a base é o faturamento do mês de cada empresa (dá para trocar); valor = base × percentual.
                </p>
              </div>
              <div v-else class="min-w-0 space-y-0.5">
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
          <template v-if="salvando && progresso">Criando {{ Math.min(progresso.feito + 1, progresso.total) }} de {{ progresso.total }}…</template>
          <template v-else>{{ textoSalvar }}</template>
        </Button>
      </div>
    </template>
  </NfseSheet>
</template>
