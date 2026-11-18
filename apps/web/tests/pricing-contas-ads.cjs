// Run from apps/web: node tests/pricing-contas-ads.cjs
// M-12 (18/11/2026): na aba Contas, a coluna Ads vira o botão "2%  ROAS 50 ▾"
// com o painel Geral / % gasto real / ROAS real / Por produto (no estilo do
// painel da Margem). Executa as funções reais da página e confere o template.
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

// ---------- 1) contas: ROAS mínimo, Geral, % do produto, leitura do gasto
const ads = { value: null }
const podeEditarAds = { get value() { return !!ads.value?.pode_editar } }
const buscaAdsProduto = { value: '' }
const seqAdsConta = {}
const avisos = []
const toast = { error: (titulo, texto) => avisos.push(`${titulo} | ${texto}`) }
const pedidos = []
let resposta = null
const api = async (url, opts) => {
  pedidos.push([url, opts.method, opts.body])
  if (resposta instanceof Error) throw resposta
  return resposta
}
const piscadas = []
const flash = (id, campo) => piscadas.push(`${id}:${campo}`)
const f = new Function('ads', 'podeEditarAds', 'buscaAdsProduto', 'seqAdsConta', 'toast', 'api', 'flash', js([
  constante('ADS_PADRAO'), constante('fmtPct'),
  funcao('parseDec'), funcao('lerPct'), funcao('adsDa'), funcao('pctGeralAds'), funcao('pctDoProdutoAds'),
  funcao('roasMinimo'), funcao('fmtRoas'), funcao('fmtGasto'), funcao('lerGasto'), funcao('produtosDoAds'),
  funcao('textoErroAds'), funcao('gravarAds'), funcao('gravarCampoAds'),
  'return { ADS_PADRAO, adsDa, pctGeralAds, pctDoProdutoAds, roasMinimo, fmtRoas, fmtGasto, lerGasto, produtosDoAds, gravarAds, gravarCampoAds }',
].join('\n')))(ads, podeEditarAds, buscaAdsProduto, seqAdsConta, toast, api, flash)

assert.equal(f.roasMinimo(2), 50)
assert.equal(f.roasMinimo(0), null, '0% → —')
assert.equal(f.roasMinimo(null), null)
assert.equal(f.fmtRoas(f.roasMinimo(2)), '50')
assert.equal(f.fmtRoas(f.roasMinimo(3)), '33,3')
assert.equal(f.fmtRoas(null), '—')
assert.equal(f.fmtGasto(50.5), '50,50')
assert.equal(f.fmtGasto(null), '')
for (const [digitado, esperado] of [
  ['50', 50], ['50,9', 50.9], ['1.234,56', 1234.56], ['0', 0], ['', null], [' ', null],
  ['-1', undefined], ['abc', undefined], ['12,345', 12.35],
  // Revisão M-7..M-12: ponto de milhar sem vírgula era lido como decimal
  // ("3.000" gravava R$ 3,00).
  ['3.000', 3000], [' 1.500 ', 1500], ['1.234.567', 1234567], ['3.000,00', 3000], ['1.5', 1.5],
]) assert.equal(f.lerGasto(digitado), esperado, `gasto ${JSON.stringify(digitado)}`)

const conta = {
  ...f.ADS_PADRAO,
  pode_gastar_pct: 2,
  produtos: { p1: { pode_gastar_pct: 5, gasto_dia: null }, p2: { pode_gastar_pct: null, gasto_dia: 30 } },
}
assert.equal(f.pctGeralAds(conta), 2)
assert.equal(f.pctGeralAds({ ...conta, ads_geral_ligada: false }), null, 'Geral desmarcada: sem %')
assert.equal(f.pctDoProdutoAds(conta, 'p1'), 5, 'o produto usa o dele')
assert.equal(f.pctDoProdutoAds(conta, 'p2'), 2, '% vazio usa a Geral')
assert.equal(f.pctDoProdutoAds(conta, 'p3'), 2)
assert.equal(f.pctDoProdutoAds({ ...conta, ads_por_produto_ligada: false }, 'p1'), 2, 'Por produto desmarcado: todos usam a Geral')
assert.equal(f.pctDoProdutoAds({ ...conta, ads_geral_ligada: false }, 'p3'), null)
console.log('pricing contas ads: contas ok')

// ---------- 2) lista do Por produto: busca por SKU ou nome, com valor próprio primeiro
ads.value = {
  department: 'celular',
  pode_editar: true,
  produtos: [
    { id: 'p3', sku: 'a03', name: 'Capa Azul' },
    { id: 'p2', sku: 'b02', name: 'Fone' },
    { id: 'p1', sku: 'c01', name: 'Mala Cinza' },
  ],
  contas: { c1: conta },
}
assert.deepEqual(f.produtosDoAds(conta).map((p) => p.id), ['p2', 'p1', 'p3'])
buscaAdsProduto.value = 'A0'
assert.deepEqual(f.produtosDoAds(conta).map((p) => p.id), ['p3'])
buscaAdsProduto.value = 'mala'
assert.deepEqual(f.produtosDoAds(conta).map((p) => p.id), ['p1'])
buscaAdsProduto.value = ''
assert.equal(f.adsDa('nova'), f.ADS_PADRAO, 'conta sem nada gravado: o padrão')
console.log('pricing contas ads: lista ok')

// ---------- 3) gravação: ao sair do campo, só se mudou; inválido volta ao salvo
const campo = (valor, dados) => ({ target: { value: valor, dataset: dados } })
;(async () => {
  resposta = { ...conta, pode_gastar_pct: 3, real: { pct_gasto: 4.1, roas: 24.39 } }
  let ev = campo('3', { adsConta: 'c1', adsCampo: 'pode_gastar_pct' })
  f.gravarCampoAds(ev)
  assert.equal(ads.value.contas.c1.pode_gastar_pct, 3, 'já na tela')
  await new Promise((r) => setTimeout(r, 0))
  assert.deepEqual(pedidos.pop(), ['/api/pricing/ads/c1', 'PUT', { pode_gastar_pct: 3 }])
  assert.equal(ads.value.contas.c1.real.roas, 24.39, 'a resposta do servidor vale')
  assert.deepEqual(piscadas.pop(), 'c1:ads')

  f.gravarCampoAds(campo('3', { adsConta: 'c1', adsCampo: 'pode_gastar_pct' }))
  assert.equal(pedidos.length, 0, 'igual ao salvo não grava')

  ev = campo('101', { adsConta: 'c1', adsCampo: 'pode_gastar_pct' })
  f.gravarCampoAds(ev)
  assert.equal(pedidos.length, 0)
  assert.equal(ev.target.value, '3', '% inválido volta ao salvo')
  assert.match(avisos.pop(), /^Valor inválido \| Use um número de 0 a 100/)

  ev = campo('-5', { adsConta: 'c1', adsCampo: 'gasto_dia' })
  f.gravarCampoAds(ev)
  assert.equal(ev.target.value, '')
  assert.match(avisos.pop(), /^Valor inválido \| Use o valor em reais/)

  // Produto: só o campo mexido vai; vazio apaga.
  resposta = { ...ads.value.contas.c1, produtos: { ...conta.produtos, p3: { pode_gastar_pct: null, gasto_dia: 12.5 } } }
  f.gravarCampoAds(campo('12,5', { adsConta: 'c1', adsCampo: 'gasto_dia', adsProduto: 'p3' }))
  assert.deepEqual(ads.value.contas.c1.produtos.p3, { pode_gastar_pct: null, gasto_dia: 12.5 })
  await new Promise((r) => setTimeout(r, 0))
  assert.deepEqual(pedidos.pop(), ['/api/pricing/ads/c1/produto/p3', 'PUT', { gasto_dia: 12.5 }])
  resposta = { ...ads.value.contas.c1, produtos: { p2: conta.produtos.p2, p3: resposta.produtos.p3 } }
  f.gravarCampoAds(campo('', { adsConta: 'c1', adsCampo: 'pode_gastar_pct', adsProduto: 'p1' }))
  await new Promise((r) => setTimeout(r, 0))
  assert.deepEqual(pedidos.pop(), ['/api/pricing/ads/c1/produto/p1', 'PUT', { pode_gastar_pct: null }])
  assert.equal(ads.value.contas.c1.produtos.p1, undefined, 'sem valor: volta para a Geral')

  // Caixa: recusa do servidor volta ao que estava, com aviso.
  resposta = Object.assign(new Error('x'), { status: 403 })
  await f.gravarAds('c1', { ads_geral_ligada: false })
  assert.equal(ads.value.contas.c1.ads_geral_ligada, true)
  assert.match(avisos.pop(), /^Não salvou o Ads \| Só quem edita Contas/)

  // Quem não edita Contas não grava.
  ads.value.pode_editar = false
  f.gravarCampoAds(campo('9', { adsConta: 'c1', adsCampo: 'pode_gastar_pct' }))
  await f.gravarAds('c1', { gasto_dia: 1 })
  assert.equal(pedidos.length, 1, 'só o pedido recusado de antes')
  console.log('pricing contas ads: gravação ok')

  // ---------- 4) template
  const tpl = descriptor.template.content
  const contas = tpl.slice(tpl.indexOf("tab === 'contas'"), tpl.indexOf("tab === 'produtos'"))
  const celula = contas.slice(contas.indexOf('v-else-if="f.key === \'ads\'"'), contas.indexOf('<td\n                  v-else\n'))
  assert.ok(celula.length > 100, 'a célula Ads existe')
  for (const trecho of [
    '<PopoverRoot @update:open="abrirOuFecharAds">',
    'data-acao="ads"',
    '<span>{{ fmtPct(pctGeralAds(adsDa(acc.id))!) }}%</span>',
    '<span>ROAS {{ fmtRoas(roasMinimo(pctGeralAds(adsDa(acc.id)))) }}</span>',
    "{{ ads || adsErro ? '—' : '…' }}",
    '<ChevronDown',
    'data-menu-ads',
    'Ads — {{ nomeComTipo(acc) }} · {{ platformLabel(acc.platform) }}',
    'data-campo="ads-geral-ligada"',
    'data-campo="ads-pct"',
    'data-campo="ads-gasto-dia"',
    'ROAS mínimo {{ fmtRoas(roasMinimo(c.pode_gastar_pct)) }}',
    '% gasto real',
    'ROAS real',
    "c.real.pct_gasto == null ? '—'",
    'fmtRoas(c.real.roas)',
    'data-campo="ads-por-produto-ligada"',
    'data-campo="ads-busca-produto"',
    'v-for="p in produtosDoAds(c)"',
    ":placeholder=\"pctGeralAds(c) == null ? '—' : fmtPct(pctGeralAds(c)!)\"",
    'fmtRoas(roasMinimo(pctDoProdutoAds(c, p.id)))',
    ':data-ads-produto="p.id"',
    ':disabled="!podeEditarAds"',
    '@change="gravarCampoAds"',
  ]) assert.ok(celula.includes(trecho), `célula Ads tem ${trecho}`)
  for (const cab of ['<span>Produto</span>', 'Pode gastar (%)', 'ROAS mínimo</span>', 'Gasto por dia (R$)']) {
    assert.ok(celula.includes(cab), `lista tem a coluna ${cab}`)
  }
  // O texto antigo saiu da tela (fica no banco): a célula não edita `ads`.
  assert.doesNotMatch(celula, /startEditAccount\(acc, f\.key\)/)
  // A leitura: na aba Contas e ao trocar de departamento nela.
  assert.match(setup, /if \(t === 'contas'\) void carregarAds\(\)/)
  assert.match(setup, /if \(tab\.value === 'contas'\) void carregarAds\(\)/)
  console.log('pricing contas ads: template ok')
  console.log('pricing-contas-ads: ok')
})().catch((e) => {
  console.error(e)
  process.exit(1)
})
