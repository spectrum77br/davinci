// Run from apps/web: node tests/pricing-contas-ml-par.cjs
// M-52..M-54 (25/11/2026), aba Contas da Tabela de Preços:
// - M-52: Plataforma e depois Conta, as duas à esquerda;
// - M-53: Clássico e Premium da mesma conta ML (mesmo par_id) viram UMA linha
//   (a do Clássico), sem a coluna Tipo ML; Comissão "14% · 19%" com a janela
//   do ADM (PUT /comissao-ml em frações); contadores contam a conta uma vez;
//   excluir com uma confirmação; conta ML nova sem comissão no corpo;
// - M-54: Catálogo ML "Ativo"/"Inativo" com a janela da conta (Catálogo ativo
//   + Margens do ADM), gravando pelo id do Clássico.
// Executa as funções reais da página e confere o template.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const { parse } = require('vue/compiler-sfc')

const filename = path.resolve(__dirname, '../pages/pricing/[tab].vue')
const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
assert.deepEqual(errors, [])
const setup = descriptor.scriptSetup.content
const source = ts.createSourceFile(filename, setup, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)

const js = (texto) => {
  const out = ts.transpileModule(texto, { compilerOptions: { target: ts.ScriptTarget.ES2022 }, reportDiagnostics: true })
  assert.deepEqual(out.diagnostics, [])
  return out.outputText
}
const funcao = (nome) => {
  const n = source.statements.find((s) => ts.isFunctionDeclaration(s) && s.name?.text === nome)
  assert.ok(n, `${nome} existe na página`)
  return n.getText(source)
}
const constante = (nome) => {
  const st = source.statements.find((s) => ts.isVariableStatement(s)
    && s.declarationList.declarations.some((d) => d.name.getText(source) === nome))
  assert.ok(st, `${nome} existe na página`)
  return st.getText(source)
}
// computed(() => …) vira um getter `.value` que recalcula sempre.
const computado = (nome) => {
  const st = source.statements.find((s) => ts.isVariableStatement(s)
    && s.declarationList.declarations.some((d) => d.name.getText(source) === nome))
  assert.ok(st, `${nome} existe na página`)
  const ini = st.declarationList.declarations.find((d) => d.name.getText(source) === nome).initializer
  assert.ok(ini && ts.isCallExpression(ini) && ini.expression.getText(source) === 'computed', `${nome} é computed`)
  return `const ${nome} = { get value() { return (${ini.arguments[0].getText(source)})() } }`
}
const ref = (v) => ({ value: v })
const tick = () => new Promise((r) => setTimeout(r, 0))

// ---------- 1) o par: uma linha na aba (a do Clássico), contadores uma vez
const accounts = ref([])
const isAdmin = ref(true)
const canEditContas = ref(true)
const LABELS = { mercadolivre: 'ML', amazon: 'Amazon', shopee: 'Shopee' }
const platformLabel = (p) => LABELS[p] ?? p
const rotuloTipo = (a) => (/premium/.test(a.listing_type || '') ? 'premium' : /cl[aá]ssico/.test(a.listing_type || '') ? 'clássico' : '')
const pedidos = []
let respostas = []
const api = async (url, opts = {}) => {
  pedidos.push([url, opts.method ?? 'GET', opts.body])
  const r = respostas.shift()
  if (r instanceof Error) throw r
  return typeof r === 'function' ? r() : r
}
const piscadas = []
const flash = (id, campo) => piscadas.push(`${id}:${campo}`)

const f = new Function('accounts', 'isAdmin', 'canEditContas', 'platformLabel', 'rotuloTipo', 'api', 'flash', 'reactive', 'ref', js([
  funcao('ehCatalogo'), funcao('ordemTipo'), funcao('lerPct'),
  computado('linhasPorPar'), funcao('parDe'), funcao('escondidaNaAba'), funcao('irmaDe'),
  funcao('tituloConta'), funcao('tipoDaContaSolta'), funcao('comissaoPadraoMl'),
  constante('CAMPOS_SO_DA_LINHA'), funcao('espelharNaIrma'),
  constante('comissaoMlId'), constante('comissaoMlValores'), constante('comissaoMlSalvando'), constante('comissaoMlErro'),
  funcao('textoComissao'), funcao('textoComissaoPar'), funcao('abrirComissaoMl'), funcao('fecharComissaoMl'), funcao('textoErroComissaoMl'),
  funcao('salvarComissaoMl'),
  computado('accountsByDept'),
  `return { linhasPorPar, parDe, escondidaNaAba, irmaDe, tituloConta, tipoDaContaSolta, comissaoPadraoMl, espelharNaIrma,
    comissaoMlId, comissaoMlValores, comissaoMlErro, textoComissao, textoComissaoPar, abrirComissaoMl, fecharComissaoMl, salvarComissaoMl, accountsByDept }`,
].join('\n')))(accounts, isAdmin, canEditContas, platformLabel, rotuloTipo, api, flash, (o) => o, ref)

const conta = (id, extra = {}) => ({
  id, name: id, platform: 'mercadolivre', canal: 'kit', department: 'mala', sort_order: 0, par_id: null,
  listing_type: null, commission: null, ...extra,
})
const fpC = conta('fpC', { name: 'forpaper', listing_type: 'ml classico', par_id: 'P1', commission: '0.1400', observation: 'a' })
const fpP = conta('fpP', { name: 'forpaper', listing_type: 'ml premium', par_id: 'P1', commission: '0.1900', observation: 'a' })
const fpCat = conta('fpCat', { name: 'forpaper', canal: 'catalogo', conta_base_id: 'fpC', par_id: null })
const nexus = conta('nexus', { listing_type: 'ml premium', par_id: 'P2', commission: '0.1900' }) // só Premium
const kfa = conta('kfa', { platform: 'amazon', commission: '0.1500' })
// O Premium vem antes na lista do servidor: o Clássico continua sendo a linha da aba.
accounts.value = [fpP, fpC, fpCat, nexus, kfa]

assert.deepEqual(f.parDe(fpP), { classico: fpC, premium: fpP })
assert.deepEqual(f.parDe(fpC), { classico: fpC, premium: fpP })
assert.equal(f.parDe(nexus), null, 'só um tipo: sem par, como hoje')
assert.equal(f.parDe(kfa), null)
assert.equal(f.parDe(fpCat), null, 'coluna de catálogo não entra no par')
assert.equal(f.escondidaNaAba(fpP), true)
assert.equal(f.escondidaNaAba(fpC), false)
assert.equal(f.escondidaNaAba(nexus), false)
assert.equal(f.irmaDe(fpC), fpP)
assert.equal(f.irmaDe(fpP), fpC)
assert.equal(f.irmaDe(kfa), null)
assert.deepEqual(f.accountsByDept.value.mala.map((a) => a.id), ['fpC', 'kfa', 'nexus'], 'Mala: o par conta uma vez')
assert.equal(f.tituloConta(fpC), 'ML forpaper')
assert.equal(f.tituloConta(kfa), 'Amazon kfa')
assert.equal(f.tipoDaContaSolta(fpC), '', 'com par: sem tipo ao lado')
assert.equal(f.tipoDaContaSolta(nexus), 'Premium')
assert.equal(f.tipoDaContaSolta(conta('x')), 'sem tipo')
assert.equal(f.tipoDaContaSolta(kfa), '')
assert.equal(f.comissaoPadraoMl('mala'), '14% · 19%')
assert.equal(f.comissaoPadraoMl('celular'), '11% · 16%')
assert.equal(f.comissaoPadraoMl('eletro'), '11% · 16%')
// O que o servidor repetiu no Premium: a cópia local acompanha (menos a comissão).
fpC.observation = 'b'
fpC.commission = '0.1500'
f.espelharNaIrma(fpC, ['observation', 'commission'])
assert.equal(fpP.observation, 'b')
assert.equal(fpP.commission, '0.1900', 'comissão é de cada tipo')
fpC.commission = '0.1400'
console.log('pricing contas ML par: par ok')

// ---------- 2) janela da Comissão do ML
;(async () => {
  assert.equal(f.textoComissao('0.1400'), '14')
  assert.equal(f.textoComissao('0.1650'), '16,5')
  assert.equal(f.textoComissao(null), '')
  assert.equal(f.textoComissaoPar({ classico: fpC, premium: fpP }), '14% · 19%')
  assert.equal(f.textoComissaoPar({ classico: { commission: '0.1550' }, premium: { commission: null } }), '15,5% · —')
  f.abrirComissaoMl(fpC)
  assert.equal(f.comissaoMlId.value, 'fpC')
  assert.deepEqual({ ...f.comissaoMlValores }, { classico: '14', premium: '19' })
  // Nada mudou: fecha sem gravar.
  await f.salvarComissaoMl()
  assert.equal(pedidos.length, 0)
  assert.equal(f.comissaoMlId.value, null)

  f.abrirComissaoMl(fpC)
  f.comissaoMlValores.classico = '16,5'
  f.comissaoMlValores.premium = 'abc'
  await f.salvarComissaoMl()
  assert.match(f.comissaoMlErro.value, /^Premium: use um número de 0 a 100/)
  assert.equal(pedidos.length, 0)

  f.comissaoMlValores.premium = '21'
  respostas = [{ classico: { ...fpC, commission: '0.1650' }, premium: { ...fpP, commission: '0.2100' } }]
  await f.salvarComissaoMl()
  assert.deepEqual(pedidos.pop(), ['/api/pricing/accounts/fpC/comissao-ml', 'PUT', { classico: 0.165, premium: 0.21 }])
  assert.equal(fpC.commission, '0.1650')
  assert.equal(fpP.commission, '0.2100')
  assert.equal(f.comissaoMlId.value, null, 'salvou: fecha')
  assert.deepEqual(piscadas.pop(), 'fpC:commission')

  // Recusa: a janela fica aberta com o motivo.
  f.abrirComissaoMl(fpC)
  f.comissaoMlValores.classico = ''
  respostas = [Object.assign(new Error('x'), { status: 400, data: { detail: { code: 'sem_par' } } })]
  await f.salvarComissaoMl()
  assert.deepEqual(pedidos.pop()[2], { classico: null, premium: 0.21 }, 'vazio = null')
  assert.match(f.comissaoMlErro.value, /não tem Clássico e Premium juntos/)
  assert.equal(f.comissaoMlId.value, 'fpC')
  f.fecharComissaoMl()

  // Só o ADM abre; conta sem par não abre.
  isAdmin.value = false
  f.abrirComissaoMl(fpC)
  assert.equal(f.comissaoMlId.value, null)
  isAdmin.value = true
  f.abrirComissaoMl(nexus)
  assert.equal(f.comissaoMlId.value, null)
  console.log('pricing contas ML par: comissão ok')

  await catalogo()
  await excluirECriar()
  await estouroNaTabela()
  template()
  console.log('pricing-contas-ml-par: ok')
})().catch((e) => {
  console.error(e)
  process.exit(1)
})

// ---------- 3) janela do Catálogo ML: grava pelo Clássico, só no Salvar
async function catalogo() {
  const fpCat2 = { ...fpCat, margens_catalogo: { 1: '0.6400', 2: '0.4400', 3: null, 4: null } }
  const contasDepois = [fpC, fpP, fpCat2, kfa]
  let recarregou = 0
  const loadAccounts = async () => { recarregou++; accounts.value = contasDepois.slice() }
  const confirmados = []
  let resposta = true
  const confirm = (texto) => { confirmados.push(texto); return resposta }
  const toasts = []
  const toast = { success: (t, d) => toasts.push(`${t} | ${d}`) }
  const department = ref('mala')
  const TYPE_HEADERS = ref({ mala: ['Acessórios', '12"', '18" e 20"', '24" acima'] })
  const nTiposAba = ref(4)
  const fmtMargin = (v) => (v == null || v === '' ? '—' : `${(Number(v) * 100).toFixed(1)}%`)
  const g = new Function('accounts', 'isAdmin', 'canEditContas', 'api', 'flash', 'ref', 'loadAccounts', 'confirm', 'toast',
    'department', 'TYPE_HEADERS', 'nTiposAba', 'fmtMargin', 'platformLabel', 'rotuloTipo', 'nomeComTipo', 'resumoMargemPropria', js([
      funcao('ehCatalogo'), funcao('ordemTipo'), funcao('parseDec'),
      computado('linhasPorPar'), funcao('parDe'), funcao('tituloConta'),
      computado('catalogoPorBase'), funcao('catalogoAtivo'), funcao('podeTerCatalogo'), funcao('margemPropria'),
      funcao('temMargemPropria'),
      constante('catalogoJanelaId'), constante('catalogoJanelaAtivo'), constante('catalogoJanelaMargens'),
      constante('catalogoJanelaUsarDaConta'), constante('catalogoJanelaValores'), constante('catalogoJanelaSalvando'),
      constante('catalogoJanelaErro'),
      funcao('textoMargemCatalogo'), funcao('margemDaContaTexto'), funcao('abrirCatalogo'), funcao('fecharCatalogo'),
      funcao('tituloCatalogoDaConta'), funcao('textoErroCatalogo'), funcao('salvarCatalogo'),
      `return { catalogoJanelaId, catalogoJanelaAtivo, catalogoJanelaUsarDaConta, catalogoJanelaValores, catalogoJanelaErro,
        margemDaContaTexto, abrirCatalogo, fecharCatalogo, tituloCatalogoDaConta, salvarCatalogo, catalogoAtivo }`,
    ].join('\n')))(accounts, isAdmin, canEditContas, api, flash, ref, loadAccounts, confirm, toast,
    department, TYPE_HEADERS, nTiposAba, fmtMargin, platformLabel, rotuloTipo, (a) => a.name, () => 'Acessórios 64.0%')

  accounts.value = [fpC, fpP, kfa] // sem catálogo
  fpC.margin2 = '0.3000'
  assert.equal(g.margemDaContaTexto(fpC, 2), '30,0')
  assert.equal(g.margemDaContaTexto(fpC, 5), '—')
  assert.equal(g.tituloCatalogoDaConta(fpC), 'Catálogo ML inativo — clique para ativar')
  g.abrirCatalogo(kfa)
  assert.equal(g.catalogoJanelaId.value, null, 'só conta do ML')
  g.abrirCatalogo(fpC)
  assert.equal(g.catalogoJanelaId.value, 'fpC')
  assert.equal(g.catalogoJanelaAtivo.value, false)
  assert.equal(g.catalogoJanelaUsarDaConta.value, true)
  assert.deepEqual(g.catalogoJanelaValores.value, ['', '', '', ''])
  // Cancelar não grava nada.
  g.catalogoJanelaAtivo.value = true
  g.fecharCatalogo()
  assert.equal(pedidos.length, 0)

  // Ativar com margens próprias: liga pelo Clássico, relê e grava as margens na
  // coluna de catálogo nova do Clássico (o servidor copia para a do Premium).
  g.abrirCatalogo(fpC)
  g.catalogoJanelaAtivo.value = true
  g.catalogoJanelaUsarDaConta.value = false
  g.catalogoJanelaValores.value = ['64', '44', '', '']
  const novaFilha = { ...fpCat, margens_catalogo: { 1: null, 2: null, 3: null, 4: null } }
  contasDepois.splice(0, contasDepois.length, fpC, fpP, novaFilha, kfa)
  respostas = [{}, {}]
  await g.salvarCatalogo()
  assert.deepEqual(pedidos.shift(), ['/api/pricing/accounts/fpC/catalogo', 'POST', { ativo: true }])
  assert.deepEqual(pedidos.shift(), ['/api/pricing/accounts/fpCat', 'PATCH', { margin1: '0.6400', margin2: '0.4400' }])
  assert.equal(pedidos.length, 0)
  assert.equal(recarregou, 2, 'relê depois de ligar (a coluna nova) e depois da margem')
  assert.match(toasts.pop(), /^Catálogo ML ativado \| ML forpaper/)
  assert.equal(g.catalogoJanelaId.value, null)
  assert.equal(confirmados.length, 0, 'ligar não pede confirmação')

  // Ativo com margens próprias: abre com a caixa desmarcada e os valores.
  contasDepois.splice(0, contasDepois.length, fpC, fpP, fpCat2, kfa)
  accounts.value = contasDepois.slice()
  recarregou = 0
  assert.match(g.tituloCatalogoDaConta(fpC), /^Catálogo ML ativo — margens próprias: Acessórios/)
  g.abrirCatalogo(fpC)
  assert.equal(g.catalogoJanelaAtivo.value, true)
  assert.equal(g.catalogoJanelaUsarDaConta.value, false)
  assert.deepEqual(g.catalogoJanelaValores.value, ['64,0', '44,0', '', ''])
  // "Usar as margens da conta": grava vazio (null) só onde havia margem própria.
  g.catalogoJanelaUsarDaConta.value = true
  respostas = [{}]
  await g.salvarCatalogo()
  assert.deepEqual(pedidos.pop(), ['/api/pricing/accounts/fpCat', 'PATCH', { margin1: null, margin2: null }])
  assert.equal(recarregou, 1)

  // Margem inválida: não grava e avisa na janela.
  g.abrirCatalogo(fpC)
  g.catalogoJanelaValores.value = ['6x4', '', '', '']
  await g.salvarCatalogo()
  assert.equal(g.catalogoJanelaErro.value, 'Margem inválida em Acessórios')
  assert.equal(pedidos.length, 0)
  g.fecharCatalogo()

  // Ativo → Inativo: pede a confirmação (dos dois tipos); sem OK, nada.
  g.abrirCatalogo(fpC)
  g.catalogoJanelaAtivo.value = false
  resposta = false
  await g.salvarCatalogo()
  assert.equal(pedidos.length, 0)
  assert.match(confirmados.pop(), /^Desativar o Catálogo ML de "forpaper"\?[\s\S]*Clássico e do Premium[\s\S]*dos dois tipos/)
  resposta = true
  respostas = [{}]
  contasDepois.splice(0, contasDepois.length, fpC, fpP, kfa)
  await g.salvarCatalogo()
  assert.deepEqual(pedidos.pop(), ['/api/pricing/accounts/fpC/catalogo', 'POST', { ativo: false }])
  assert.equal(pedidos.length, 0, 'desligado: não mexe em margem')
  assert.match(toasts.pop(), /^Catálogo ML desativado/)

  // Recusa do servidor: fica aberta com o motivo.
  g.abrirCatalogo(fpC)
  g.catalogoJanelaAtivo.value = true
  respostas = [Object.assign(new Error('x'), { status: 409, data: { detail: { code: 'catalogo_desencontrado' } } })]
  await g.salvarCatalogo()
  assert.match(g.catalogoJanelaErro.value, /desencontrados; nada foi gravado/)
  assert.equal(g.catalogoJanelaId.value, 'fpC')
  pedidos.length = 0
  g.fecharCatalogo()

  // Quem não edita Contas não abre.
  canEditContas.value = false
  g.abrirCatalogo(fpC)
  assert.equal(g.catalogoJanelaId.value, null)
  canEditContas.value = true
  console.log('pricing contas ML par: catálogo ok')
}

// ---------- 4) excluir o par (uma confirmação) e criar conta ML
async function excluirECriar() {
  const fpCat2 = { ...fpCat }
  const confirmados = []
  const confirm = (t) => { confirmados.push(t); return true }
  const accountsErr = ref(null)
  const h = new Function('accounts', 'api', 'confirm', 'accountsErr', 'nomeComTipo', js([
    funcao('ehCatalogo'), funcao('ordemTipo'),
    computado('linhasPorPar'), funcao('parDe'),
    computado('catalogoPorBase'), funcao('catalogoAtivo'),
    funcao('deleteAccount'),
    'return { deleteAccount }',
  ].join('\n')))(accounts, api, confirm, accountsErr, (a) => `${a.name} ${a.listing_type}`)
  accounts.value = [fpC, fpP, fpCat2, kfa]
  respostas = [{}]
  await h.deleteAccount(fpC)
  assert.equal(confirmados.length, 1, 'uma confirmação só')
  assert.match(confirmados[0], /^Excluir conta "forpaper"\?[\s\S]*Saem o Clássico e o Premium[\s\S]*O Catálogo ML desta conta também sai\./)
  assert.deepEqual(pedidos.pop(), ['/api/pricing/accounts/fpC', 'DELETE', undefined])
  assert.deepEqual(accounts.value.map((a) => a.id), ['kfa'], 'saem as duas linhas e o catálogo')

  // Conta ML nova: sem comissão no corpo (o servidor usa a da categoria) e a
  // lista é relida para trazer o Premium.
  const newAcc = { name: 'nova', platform: 'mercadolivre', kit_number: 2, commission: '13' }
  const showAddAcc = ref(true)
  const addingAcc = ref(false)
  let recarregou = 0
  const loadAccounts = async () => { recarregou++; accounts.value = [...accounts.value, { id: 'novaC' }, { id: 'novaP' }] }
  const k = new Function('newAcc', 'department', 'isAdmin', 'api', 'accounts', 'loadAccounts', 'showAddAcc', 'addingAcc',
    'accountsErr', 'passaFiltroEquipe', 'contasEquipesDesmarcadas', 'contasPlataformasDesmarcadas', 'chavesEquipe',
    'alternarNaLista', 'parseDec', js([funcao('submitNewAcc'), 'return submitNewAcc'].join('\n')),
  )(newAcc, ref('mala'), isAdmin, api, accounts, loadAccounts, showAddAcc, addingAcc, accountsErr, () => true,
    ref([]), ref([]), () => [], (l) => l, (v) => Number(String(v).replace(',', '.')))
  respostas = [{ id: 'novaC', platform: 'mercadolivre' }]
  await k()
  assert.deepEqual(pedidos.pop(), ['/api/pricing/accounts', 'POST', { name: 'nova', platform: 'mercadolivre', department: 'mala', kit_number: 2 }])
  assert.equal(recarregou, 1)
  assert.deepEqual(accounts.value.map((a) => a.id), ['kfa', 'novaC', 'novaP'], 'não duplica a resposta')
  // Outras plataformas: como antes (comissão digitada vai no corpo, sem reler).
  newAcc.platform = 'shopee'
  respostas = [{ id: 'sh', platform: 'shopee' }]
  await k()
  assert.deepEqual(pedidos.pop()[2], { name: 'nova', platform: 'shopee', department: 'mala', kit_number: 2, commission: '0.1300' })
  assert.equal(recarregou, 1)
  assert.equal(accounts.value.at(-1).id, 'sh')
  console.log('pricing contas ML par: excluir e criar ok')
}

// ---------- 5) Afiliado/Desconto/Custo total: estouro volta o valor (M-56);
// gravação boa repete na irmã do par
async function estouroNaTabela() {
  const accountsErr = ref(null)
  const erroCustoTotal = ref(null)
  let recarregou = 0
  const loadAccounts = async () => { recarregou++ }
  const p = new Function('accounts', 'api', 'flash', 'accountsErr', 'erroCustoTotal', 'loadAccounts', 'confirm', js([
    funcao('ehCatalogo'), funcao('ordemTipo'), funcao('lerPct'), funcao('parseDec'),
    computado('linhasPorPar'), funcao('parDe'), funcao('irmaDe'),
    constante('CAMPOS_SO_DA_LINHA'), funcao('espelharNaIrma'),
    computado('catalogoPorBase'), funcao('catalogoAtivo'),
    funcao('erroCustoTotalDe'), funcao('_patchAccount'),
    'return _patchAccount',
  ].join('\n')))(accounts, api, flash, accountsErr, erroCustoTotal, loadAccounts, () => true)
  fpC.afiliado_pct = '4.00'
  fpP.afiliado_pct = '4.00'
  fpC.desconto_pct = null
  fpP.desconto_pct = null
  accounts.value = [fpC, fpP, kfa]
  const MSG = 'Afiliado 4% + Desconto 10% + Ads 10% = 24%. Passa do Custo total (20%). Não gravado.'
  respostas = [Object.assign(new Error('x'), { status: 422, data: { detail: { code: 'custo_total_estourado', mensagem: MSG } } })]
  await p('fpC', 'desconto_pct', '10')
  assert.deepEqual(pedidos.pop(), ['/api/pricing/accounts/fpC', 'PATCH', { desconto_pct: 10 }])
  assert.equal(fpC.desconto_pct, null, 'volta ao anterior')
  assert.deepEqual(erroCustoTotal.value, { id: 'fpC', campo: 'desconto_pct', mensagem: MSG })
  assert.equal(recarregou, 0, 'sem reler a lista')
  assert.equal(accountsErr.value, null, 'sem a faixa de erro geral')

  respostas = [{ ...fpC, desconto_pct: '5.00', conta_catalogo_id: null }]
  await p('fpC', 'desconto_pct', '5')
  assert.equal(fpC.desconto_pct, '5.00')
  assert.equal(fpP.desconto_pct, '5.00', 'a irmã acompanha')
  assert.equal(erroCustoTotal.value, null, 'gravou: o aviso sai')
  pedidos.length = 0

  // Conta do par não troca de plataforma por aqui.
  await p('fpC', 'platform', 'shopee')
  assert.equal(pedidos.length, 0)
  console.log('pricing contas ML par: custo total ok')
}

// ---------- 6) template
function template() {
  const tpl = descriptor.template.content
  const contas = tpl.slice(tpl.indexOf("tab === 'contas'"), tpl.indexOf("tab === 'produtos'"))
  const cab = contas.slice(0, contas.indexOf('</thead>'))
  const ths = [...cab.matchAll(/<th[\s\S]*?>([^<]*)<\/th>/g)].map((m) => m[1].trim()).filter(Boolean)
  assert.deepEqual(ths.slice(0, 4), ['Plataforma', 'Conta', 'Catálogo ML', 'Equipe'], 'M-52/M-53: ordem das colunas')
  assert.ok(!ths.includes('Tipo ML') && !ths.includes('Nome'))
  const linha = contas.slice(contas.indexOf('<!-- data rows grouped by platform'))
  const ordem = ['data-coluna-plataforma', '<!-- conta (o nome', 'data-coluna-catalogo', 'data-coluna-equipe']
    .map((t) => linha.indexOf(t))
  assert.ok(ordem.every((i, k) => i > 0 && (k === 0 || i > ordem[k - 1])), `células na ordem do cabeçalho: ${ordem}`)
  assert.match(linha, /<td\s+class="border border-border px-2 py-1\.5 text-xs text-left whitespace-nowrap"[\s\S]*?data-coluna-plataforma/)
  assert.match(linha, /data-tipo-conta-solta>\{\{ tipoDaContaSolta\(acc\) \}\}/)
  assert.ok(!contas.includes('definirTipoMl') && !contas.includes('rotuloTipoMl'))
  // Linha de adicionar: Plataforma, Conta; ML mostra a comissão da categoria.
  const add = contas.slice(contas.indexOf('<!-- add row'), contas.indexOf('<!-- data rows grouped'))
  assert.ok(add.indexOf('v-model="newAcc.platform"') < add.indexOf('id="new-acc-name"'))
  assert.match(add, /v-if="newAcc\.platform === 'mercadolivre'"[\s\S]*?\{\{ comissaoPadraoMl\(department\) \}\}<\/td>\s*<td v-else[\s\S]*?v-model="newAcc\.commission"/)
  // Comissão do par: "14% · 19%" e a janela "ML forpaper" com Clássico e Premium.
  const com = linha.slice(linha.indexOf('<td\n                v-if="mostraColunaConta(\'comissao\') && parDe(acc)"'), linha.indexOf('<!-- as outras (e conta ML sem par)'))
  assert.ok(com.length > 100, 'célula da comissão do par')
  for (const t of [
    '{{ textoComissaoPar(parDe(acc)!) }}',
    ':open="comissaoMlId === acc.id"',
    '<div class="px-1 pb-2 text-xs font-bold">{{ tituloConta(acc) }}</div>',
    'v-model="comissaoMlValores[t.chave]"',
    'class="h-6 w-14 rounded border bg-background px-1 text-right text-xs"',
    '@click="fecharComissaoMl()"',
    '@click="salvarComissaoMl()"',
  ]) assert.ok(com.includes(t), `comissão do par tem ${t}`)
  assert.match(setup, /const TIPOS_COMISSAO_ML = \[\s*\{ chave: 'classico', rotulo: 'Clássico' \},\s*\{ chave: 'premium', rotulo: 'Premium' \},/)
  // Catálogo ML: só "Ativo"/"Inativo", janela com a caixa e o botão Margens.
  const cat = linha.slice(linha.indexOf('data-coluna-catalogo'), linha.indexOf('data-coluna-equipe'))
  for (const t of [
    "{{ catalogoAtivo(acc) ? 'Ativo' : 'Inativo' }}",
    ':open="catalogoJanelaId === acc.id"',
    '{{ tituloConta(acc) }}',
    'v-model="catalogoJanelaAtivo"',
    'Catálogo ativo',
    'data-acao="margens-catalogo"',
    'v-model="catalogoJanelaUsarDaConta"',
    'Usar as margens da conta',
    'v-for="(nome, i) in TYPE_HEADERS[department] ?? []"',
    ':value="margemDaContaTexto(acc, i + 1)"',
    'v-model="catalogoJanelaValores[i]"',
    '@click="fecharCatalogo()"',
    '@click="salvarCatalogo()"',
  ]) assert.ok(cat.includes(t), `catálogo tem ${t}`)
  assert.ok(!/role="switch"|Ligado|Desligado|% margens/.test(cat), 'sem o interruptor e o link de antes')
  // A janela "Margem do catálogo" de antes saiu.
  for (const fora of ['Margem do catálogo', 'Em branco = usa a margem da conta', 'usar as da conta', 'data-janela-margens-catalogo']) {
    assert.ok(!tpl.includes(fora), `saiu: ${fora}`)
  }
}
