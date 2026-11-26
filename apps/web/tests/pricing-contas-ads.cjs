// Run from apps/web: node tests/pricing-contas-ads.cjs
// M-12 (18/11/2026): na aba Contas, a coluna Ads abre o painel Geral / Por
// produto. M-55 (25/11/2026): a célula vira a caixinha com o % Geral (sem
// "ROAS", sem seta). M-57: janela "Amazon kfa" com o cabeçalho logo abaixo do
// título, a linha Geral na mesma grade dos produtos e sem os quadros de gasto
// real. M-56: Custo total estourado mostra o aviso junto do campo. M-78
// (26/11/2026): as caixas Geral e Produto ficam lado a lado logo abaixo do
// título e são UM OU OUTRO; Geral mostra só "Todos os produtos", Produto só a
// busca e a lista (sem herdar o % Geral). M-79 + M-80 (26/11/2026): com
// Produto marcado, a mesma linha ganha o filtro "Categoria de venda ▾" e a
// caixinha "Ads" (só da tela), e a lista a coluna "Categoria de venda" depois
// do nome. Executa as funções reais da página e confere o template.
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
const erroAds = { value: null }
// M-79: o filtro "Categoria de venda" (ids marcados) e o menu dele. M-80: a
// caixinha "Ads" e os produtos limpos que ficam na lista.
const filtroCategoriaAds = { value: [] }
const menuCategoriaAdsAberto = { value: false }
const soComAds = { value: false }
const adsMantidosNoFiltro = { value: new Set() }
const f = new Function('ads', 'podeEditarAds', 'buscaAdsProduto', 'seqAdsConta', 'toast', 'api', 'flash', 'erroAds',
  'filtroCategoriaAds', 'menuCategoriaAdsAberto', 'soComAds', 'adsMantidosNoFiltro', js([
    constante('ADS_PADRAO'), constante('fmtPct'),
    funcao('parseDec'), funcao('lerPct'), funcao('adsDa'), funcao('pctGeralAds'), funcao('modoAds'), funcao('pctDoProdutoAds'),
    funcao('trocarModoAds'), funcao('tituloBotaoAds'),
    funcao('roasMinimo'), funcao('fmtRoas'), funcao('fmtGasto'), funcao('lerGasto'),
    funcao('filtroCategoriaAdsDe'), funcao('filtroAdsLigado'), funcao('produtosDoAds'), funcao('textoListaAdsVazia'),
    funcao('alternarCategoriaAds'), funcao('fecharMenuCategoriaAdsFora'), funcao('alternarFiltroAds'), funcao('zerarFiltrosAds'),
    funcao('textoErroAds'), funcao('erroCustoTotalDe'), funcao('chaveAds'), funcao('erroAdsEm'), funcao('erroAdsDoModo'),
    funcao('erroAdsDaLinhaGeral'), funcao('linhaAdsNaTela'), funcao('gravarAds'), funcao('gravarCampoAds'), funcao('abrirOuFecharAds'),
    'return { ADS_PADRAO, adsDa, pctGeralAds, modoAds, pctDoProdutoAds, trocarModoAds, tituloBotaoAds, roasMinimo, fmtRoas, fmtGasto, lerGasto, filtroAdsLigado, produtosDoAds, textoListaAdsVazia, alternarCategoriaAds, fecharMenuCategoriaAdsFora, alternarFiltroAds, abrirOuFecharAds, gravarAds, gravarCampoAds, chaveAds, erroAdsEm, erroAdsDoModo, erroAdsDaLinhaGeral }',
  ].join('\n')))(ads, podeEditarAds, buscaAdsProduto, seqAdsConta, toast, api, flash, erroAds,
  filtroCategoriaAds, menuCategoriaAdsAberto, soComAds, adsMantidosNoFiltro)

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
// M-78: conta sem nada gravado = Geral marcado, Produto desmarcado.
assert.deepEqual([f.ADS_PADRAO.ads_geral_ligada, f.ADS_PADRAO.ads_por_produto_ligada], [true, false])
assert.equal(f.modoAds(conta), 'geral')
assert.equal(f.pctGeralAds(conta), 2)
assert.equal(f.pctGeralAds({ ...conta, ads_geral_ligada: false }), null, 'Geral desmarcada: sem %')
assert.equal(f.pctDoProdutoAds(conta, 'p1'), null, 'com Geral o % do produto não vale')
const soProduto = { ...conta, ads_geral_ligada: false, ads_por_produto_ligada: true }
assert.equal(f.modoAds(soProduto), 'produto')
assert.equal(f.pctDoProdutoAds(soProduto, 'p1'), 5, 'o produto usa o dele')
assert.equal(f.pctDoProdutoAds(soProduto, 'p2'), null, '% vazio: sem Ads (não herda o Geral)')
assert.equal(f.pctDoProdutoAds(soProduto, 'p3'), null)
assert.equal(f.modoAds({ ...conta, ads_geral_ligada: false }), null, 'as duas desmarcadas: sem Ads')
assert.equal(f.modoAds({ ...conta, ads_por_produto_ligada: true }), 'geral', 'as duas marcadas: vale a Geral')
assert.match(f.tituloBotaoAds(soProduto), /^Ads por produto/)
assert.match(f.tituloBotaoAds(conta), /^Ads da conta: % Geral/)
assert.match(f.tituloBotaoAds({ ...conta, ads_geral_ligada: false }), /^Conta sem Ads/)
console.log('pricing contas ads: contas ok')

// ---------- 2) lista do Por produto: busca por SKU ou nome, com valor próprio primeiro
ads.value = {
  department: 'celular',
  pode_editar: true,
  produtos: [
    { id: 'p3', sku: 'a03', name: 'Capa Azul', categoria: { id: 'acess', name: 'Acessórios' } },
    { id: 'p2', sku: 'b02', name: 'Fone', categoria: { id: 'acess', name: 'Acessórios' } },
    { id: 'p1', sku: 'c01', name: 'Mala Cinza', categoria: { id: 'rob', name: 'Robusto' } },
  ],
  categorias: [{ id: 'acess', name: 'Acessórios' }, { id: 'div', name: 'Diversos' }, { id: 'rob', name: 'Robusto' }],
  contas: { c1: conta },
}
assert.deepEqual(f.produtosDoAds(conta).map((p) => p.id), ['p2', 'p1', 'p3'])
buscaAdsProduto.value = 'A0'
assert.deepEqual(f.produtosDoAds(conta).map((p) => p.id), ['p3'])
buscaAdsProduto.value = 'mala'
assert.deepEqual(f.produtosDoAds(conta).map((p) => p.id), ['p1'])
buscaAdsProduto.value = 'zzz'
assert.equal(f.textoListaAdsVazia(conta), 'Nenhum produto encontrado.')
buscaAdsProduto.value = ''
assert.equal(f.adsDa('nova'), f.ADS_PADRAO, 'conta sem nada gravado: o padrão')
console.log('pricing contas ads: lista ok')

// ---------- 2b) M-79: filtro "Categoria de venda" — nenhuma marcada = todos;
// marcadas = só elas; soma com a busca. Só vale com "Produto" marcado.
const ids = (c) => f.produtosDoAds(c).map((p) => p.id)
f.alternarCategoriaAds('rob')
assert.deepEqual(filtroCategoriaAds.value, ['rob'])
assert.deepEqual(ids(soProduto), ['p1'], 'só Robusto')
assert.deepEqual(ids(conta), ['p2', 'p1', 'p3'], 'com Geral marcada o filtro não vale (nem aparece)')
f.alternarCategoriaAds('acess')
assert.deepEqual(ids(soProduto), ['p2', 'p1', 'p3'], 'Robusto + Acessórios')
f.alternarCategoriaAds('rob')
assert.deepEqual(filtroCategoriaAds.value, ['acess'], 'clicar de novo desmarca')
assert.deepEqual(ids(soProduto), ['p2', 'p3'])
buscaAdsProduto.value = 'capa'
assert.deepEqual(ids(soProduto), ['p3'], 'busca + categoria: passa nos dois')
buscaAdsProduto.value = 'mala'
assert.deepEqual(ids(soProduto), [], 'Mala Cinza é Robusto: fora do filtro')
assert.equal(f.textoListaAdsVazia(soProduto), 'Nenhum produto com esse filtro.')
buscaAdsProduto.value = 'zzz'
assert.equal(f.textoListaAdsVazia(soProduto), 'Nenhum produto encontrado.', 'quem esvaziou foi a busca')
buscaAdsProduto.value = ''
filtroCategoriaAds.value = ['div']
assert.deepEqual(ids(soProduto), [], 'categoria sem produto')
assert.equal(f.textoListaAdsVazia(soProduto), 'Nenhum produto com esse filtro.')
// Produto sem categoria (não deveria vir) só aparece sem filtro.
ads.value.produtos.push({ id: 'p9', sku: 'z09', name: 'Sem categoria', categoria: null })
assert.ok(!ids(soProduto).includes('p9'))
filtroCategoriaAds.value = []
assert.ok(ids(soProduto).includes('p9'))
ads.value.produtos.pop()
// Clique na janela fora do botão/menu fecha o menu; dentro dele, não.
const alvo = (dentro) => ({ target: { closest: (sel) => (dentro && sel === '[data-filtro-ads-categoria]' ? {} : null) } })
menuCategoriaAdsAberto.value = true
f.fecharMenuCategoriaAdsFora(alvo(true))
assert.equal(menuCategoriaAdsAberto.value, true)
f.fecharMenuCategoriaAdsFora(alvo(false))
assert.equal(menuCategoriaAdsAberto.value, false)
console.log('pricing contas ads: filtro Categoria de venda ok')

// ---------- 2c) M-80: caixinha "Ads" — só os produtos com Ads nesta conta
// (% próprio OU gasto por dia = têm linha em pricing_ads_produto); soma com a
// busca e com a categoria.
assert.equal(soComAds.value, false, 'começa desmarcada')
f.alternarFiltroAds()
assert.equal(soComAds.value, true)
assert.equal(f.filtroAdsLigado(soProduto), true)
assert.equal(f.filtroAdsLigado(conta), false, 'só vale com Produto marcado')
assert.deepEqual(ids(soProduto), ['p2', 'p1'], 'p2 só com gasto por dia, p1 com %; p3 sem Ads sai')
assert.deepEqual(ids(conta), ['p2', 'p1', 'p3'], 'com Geral: todos')
buscaAdsProduto.value = 'mala'
assert.deepEqual(ids(soProduto), ['p1'], 'busca + Ads')
buscaAdsProduto.value = 'a0'
assert.deepEqual(ids(soProduto), [], 'p3 acha na busca mas não tem Ads')
assert.equal(f.textoListaAdsVazia(soProduto), 'Nenhum produto com Ads nesta conta.', 'vazia por causa da caixinha')
buscaAdsProduto.value = 'zzz'
assert.equal(f.textoListaAdsVazia(soProduto), 'Nenhum produto encontrado.', 'vazia pela busca')
buscaAdsProduto.value = ''
filtroCategoriaAds.value = ['acess']
assert.deepEqual(ids(soProduto), ['p2'], 'Ads + Acessórios: só o Fone')
filtroCategoriaAds.value = ['div']
assert.equal(f.textoListaAdsVazia(soProduto), 'Nenhum produto com esse filtro.', 'a categoria esvaziou antes da caixinha')
filtroCategoriaAds.value = []
const semNada = { ...soProduto, produtos: {} }
assert.deepEqual(ids(semNada), [])
assert.equal(f.textoListaAdsVazia(semNada), 'Nenhum produto com Ads nesta conta.')
f.alternarFiltroAds()
assert.equal(soComAds.value, false)
assert.deepEqual(ids(soProduto), ['p2', 'p1', 'p3'], 'desmarcada: todos')

// O exemplo do pedido: Shopee aguiar, Celular, Produto marcado, 4 produtos
// preenchidos; marcando Ads ficam os 4; marcando também Robusto, só F105 e F110L.
const listaAntes = ads.value
const cat = (id, name) => ({ id, name })
const robusto = cat('rob', 'Robusto')
ads.value = {
  ...listaAntes,
  produtos: [
    { id: 'a15', sku: 'a15', name: 'A15', categoria: cat('reg', 'Regular') },
    { id: 'capa', sku: 'cp1', name: 'Capa X', categoria: cat('acess', 'Acessórios') },
    { id: 'f105', sku: 'f105', name: 'F105', categoria: robusto },
    { id: 'f110l', sku: 'f110l', name: 'F110L', categoria: robusto },
    { id: 'f200', sku: 'f200', name: 'F200', categoria: robusto },
    { id: 'fone', sku: 'fn1', name: 'Fone', categoria: cat('div', 'Diversos') },
  ],
}
const aguiar = {
  ...soProduto,
  produtos: {
    a15: { pode_gastar_pct: 3, gasto_dia: null },
    capa: { pode_gastar_pct: null, gasto_dia: 20 },
    f105: { pode_gastar_pct: 5, gasto_dia: 40 },
    f110l: { pode_gastar_pct: 4, gasto_dia: null },
  },
}
assert.equal(ids(aguiar).length, 6, 'sem filtro: todos')
f.alternarFiltroAds()
assert.deepEqual(ids(aguiar), ['a15', 'capa', 'f105', 'f110l'], 'Ads: os 4 preenchidos')
f.alternarCategoriaAds('rob')
assert.deepEqual(ids(aguiar), ['f105', 'f110l'], 'Ads + Robusto: F105 e F110L')
// Volta zerado a cada abertura da janela (como a busca); não grava nada.
buscaAdsProduto.value = 'x'
menuCategoriaAdsAberto.value = true
f.abrirOuFecharAds(true)
assert.deepEqual(filtroCategoriaAds.value, [])
assert.equal(soComAds.value, false)
assert.equal(menuCategoriaAdsAberto.value, false)
assert.equal(buscaAdsProduto.value, '')
assert.equal(ids(aguiar).length, 6)
assert.equal(pedidos.length, 0, 'os filtros não gravam nada')
ads.value = listaAntes
console.log('pricing contas ads: filtro Ads ok')

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
  assert.equal(ads.value.contas.c1.produtos.p1, undefined, 'sem valor: a linha sai')

  // Caixa: recusa do servidor volta ao que estava, com aviso.
  resposta = Object.assign(new Error('x'), { status: 403 })
  await f.gravarAds('c1', { ads_geral_ligada: false })
  assert.equal(ads.value.contas.c1.ads_geral_ligada, true)
  assert.match(avisos.pop(), /^Não salvou o Ads \| Só quem edita Contas/)

  // M-78: um ou outro. Marcar Produto desmarca Geral (as duas vão juntas).
  const tique = () => new Promise((r) => setTimeout(r, 0))
  resposta = { ...ads.value.contas.c1, ads_geral_ligada: false, ads_por_produto_ligada: true }
  f.trocarModoAds('c1', 'produto')
  assert.equal(f.modoAds(ads.value.contas.c1), 'produto', 'já na tela')
  await tique()
  assert.deepEqual(pedidos.pop(), ['/api/pricing/ads/c1', 'PUT', { ads_por_produto_ligada: true, ads_geral_ligada: false }])
  // Clicar na marcada desmarca: as duas desmarcadas.
  resposta = { ...ads.value.contas.c1, ads_por_produto_ligada: false }
  f.trocarModoAds('c1', 'produto')
  await tique()
  assert.deepEqual(pedidos.pop(), ['/api/pricing/ads/c1', 'PUT', { ads_por_produto_ligada: false }])
  assert.equal(f.modoAds(ads.value.contas.c1), null)
  // Marcar Geral desmarca Produto; nada foi apagado.
  resposta = { ...ads.value.contas.c1, ads_geral_ligada: true }
  f.trocarModoAds('c1', 'geral')
  await tique()
  assert.deepEqual(pedidos.pop(), ['/api/pricing/ads/c1', 'PUT', { ads_geral_ligada: true, ads_por_produto_ligada: false }])
  assert.equal(f.modoAds(ads.value.contas.c1), 'geral')
  assert.equal(ads.value.contas.c1.pode_gastar_pct, 3, 'o % Geral continua gravado')
  assert.deepEqual(ads.value.contas.c1.produtos.p3, { pode_gastar_pct: null, gasto_dia: 12.5 }, 'o do produto também')
  assert.equal(avisos.length, 0)

  // M-56: Custo total estourado — volta ao salvo e o aviso do servidor fica
  // junto do campo (na janela aberta, sem aviso solto).
  const MSG = 'Afiliado 4% + Desconto 10% + Ads 10% = 24%. Passa do Custo total (20%). Não gravado.'
  const estouro = () => Object.assign(new Error('x'), {
    status: 422, data: { detail: { code: 'custo_total_estourado', mensagem: MSG } },
  })
  const nPedidos = pedidos.length
  // A janela aberta da c1 com a linha das caixas, a linha Geral e as dos
  // produtos que a busca mostra.
  const linhasNaJanela = new Set(['[data-ads-modo]', '[data-ads-geral]', '[data-ads-produto-linha="p2"]'])
  const painelC1 = { querySelector: (sel) => (linhasNaJanela.has(sel) ? {} : null) }
  globalThis.document = { querySelector: (sel) => (sel === '[data-ads-painel="c1"]' ? painelC1 : null) }
  resposta = estouro()
  f.gravarCampoAds(campo('10', { adsConta: 'c1', adsCampo: 'pode_gastar_pct' }))
  assert.equal(ads.value.contas.c1.pode_gastar_pct, 10, 'já na tela')
  await new Promise((r) => setTimeout(r, 0))
  assert.equal(ads.value.contas.c1.pode_gastar_pct, 3, 'recusado: volta ao salvo')
  assert.deepEqual(erroAds.value, { accId: 'c1', chave: 'geral:pode_gastar_pct', mensagem: MSG })
  assert.equal(f.erroAdsEm('c1', 'geral:pode_gastar_pct'), MSG)
  assert.equal(f.erroAdsDaLinhaGeral('c1'), MSG, 'aviso na linha Geral')
  assert.equal(f.erroAdsDoModo('c1'), null, 'não vai para a linha das caixas')
  assert.equal(f.erroAdsEm('c2', 'geral:pode_gastar_pct'), null, 'só na conta recusada')
  assert.equal(avisos.length, 0, 'janela aberta: o aviso fica na janela')
  assert.deepEqual(pedidos.at(-1), ['/api/pricing/ads/c1', 'PUT', { pode_gastar_pct: 10 }])
  // Trocar para Produto que estoura: nada muda e o aviso fica embaixo da
  // linha das caixas.
  resposta = estouro()
  f.trocarModoAds('c1', 'produto')
  await tique()
  assert.equal(erroAds.value.chave, 'geral:ads_por_produto_ligada,ads_geral_ligada')
  assert.equal(f.erroAdsDoModo('c1'), MSG)
  assert.equal(f.erroAdsDaLinhaGeral('c1'), null, 'não repete na linha Geral')
  assert.equal(f.modoAds(ads.value.contas.c1), 'geral', 'recusado: continua em Geral')
  assert.equal(avisos.length, 0, 'janela aberta: o aviso fica na janela')
  // Produto: a chave diz o produto e o campo.
  resposta = estouro()
  await f.gravarAds('c1', { pode_gastar_pct: 30 }, 'p2')
  assert.equal(erroAds.value.chave, 'produto:p2:pode_gastar_pct')
  assert.equal(f.erroAdsEm('c1', 'produto:p2:', true), MSG)
  assert.equal(f.erroAdsEm('c1', 'produto:p1:', true), null)
  assert.equal(avisos.length, 0, 'produto na janela: o aviso fica junto dele')
  // Produto que a busca escondeu: o aviso da linha não aparece, então vai
  // também no aviso solto (antes sumia sem aviso).
  resposta = estouro()
  await f.gravarAds('c1', { pode_gastar_pct: 30 }, 'p3')
  assert.equal(erroAds.value.chave, 'produto:p3:pode_gastar_pct')
  assert.equal(avisos.pop(), `Não salvou o Ads | ${MSG}`)
  // Gravado no fechar da janela (sem a janela na tela): vai também no aviso solto.
  globalThis.document = { querySelector: () => null }
  resposta = estouro()
  await f.gravarAds('c1', { gasto_dia: 5 })
  assert.equal(avisos.pop(), `Não salvou o Ads | ${MSG}`)
  delete globalThis.document
  // A gravação seguinte que dá certo apaga o aviso.
  resposta = { ...ads.value.contas.c1 }
  await f.gravarAds('c1', { gasto_dia: 6 })
  assert.equal(erroAds.value, null)
  pedidos.splice(nPedidos)

  // M-80: com a caixinha "Ads" ligada, o produto que a pessoa limpou continua
  // na lista até a caixinha ser desligada e ligada ou a janela reabrir.
  const naLista = () => f.produtosDoAds(ads.value.contas.c1).map((p) => p.id)
  ads.value.contas.c1 = { ...soProduto, produtos: { p2: { pode_gastar_pct: null, gasto_dia: 30 } } }
  f.alternarFiltroAds()
  assert.deepEqual(naLista(), ['p2'])
  resposta = { ...ads.value.contas.c1, produtos: {} }
  f.gravarCampoAds(campo('', { adsConta: 'c1', adsCampo: 'gasto_dia', adsProduto: 'p2' }))
  await tique()
  assert.deepEqual(pedidos.pop(), ['/api/pricing/ads/c1/produto/p2', 'PUT', { gasto_dia: null }])
  assert.equal(ads.value.contas.c1.produtos.p2, undefined, 'o servidor apagou o Ads do produto')
  assert.deepEqual(naLista(), ['p2'], 'limpo com a caixinha ligada: a linha não some')
  // Com a categoria marcada também: o preso só fica se for da categoria.
  filtroCategoriaAds.value = ['rob']
  assert.deepEqual(naLista(), [], 'Fone é Acessórios: o filtro de categoria tira')
  assert.equal(f.textoListaAdsVazia(ads.value.contas.c1), 'Nenhum produto com Ads nesta conta.')
  filtroCategoriaAds.value = []
  f.alternarFiltroAds()
  f.alternarFiltroAds()
  assert.deepEqual(naLista(), [], 'desligou e ligou: sai')
  assert.equal(f.textoListaAdsVazia(ads.value.contas.c1), 'Nenhum produto com Ads nesta conta.')
  // Preenchido com a caixinha desligada: entra quando ela é ligada.
  f.alternarFiltroAds()
  resposta = { ...ads.value.contas.c1, produtos: { p1: { pode_gastar_pct: 4, gasto_dia: null } } }
  f.gravarCampoAds(campo('4', { adsConta: 'c1', adsCampo: 'pode_gastar_pct', adsProduto: 'p1' }))
  await tique()
  assert.deepEqual(pedidos.pop(), ['/api/pricing/ads/c1/produto/p1', 'PUT', { pode_gastar_pct: 4 }])
  assert.equal(adsMantidosNoFiltro.value.size, 0, 'caixinha desligada: nada fica preso')
  f.alternarFiltroAds()
  assert.deepEqual(naLista(), ['p1'])
  // Limpa com a caixinha ligada e reabre a janela: volta desmarcada e sem o preso.
  resposta = { ...ads.value.contas.c1, produtos: {} }
  f.gravarCampoAds(campo('', { adsConta: 'c1', adsCampo: 'pode_gastar_pct', adsProduto: 'p1' }))
  await tique()
  pedidos.pop()
  assert.deepEqual(naLista(), ['p1'])
  f.abrirOuFecharAds(true)
  assert.equal(soComAds.value, false, 'janela reaberta: caixinha desmarcada')
  assert.equal(adsMantidosNoFiltro.value.size, 0)
  f.alternarFiltroAds()
  assert.deepEqual(naLista(), [], 'reaberta e ligada de novo: o limpo sai')
  f.alternarFiltroAds()
  // Linha escondida pelo filtro e recusada pelo Custo total: o aviso da linha
  // não aparece, então vai no aviso solto (linhaAdsNaTela; o filtro não grava
  // nem apaga a linha escondida).
  globalThis.document = { querySelector: (sel) => (sel === '[data-ads-painel="c1"]' ? { querySelector: () => null } : null) }
  resposta = estouro()
  await f.gravarAds('c1', { pode_gastar_pct: 30 }, 'p3')
  assert.equal(avisos.pop(), `Não salvou o Ads | ${MSG}`)
  delete globalThis.document
  erroAds.value = null
  assert.equal(avisos.length, 0)
  pedidos.splice(nPedidos)
  console.log('pricing contas ads: filtro Ads na gravação ok')

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
    // M-60: o botão é "flex" com a largura da célula, para a caixinha vazia
    // ficar na mesma altura das vizinhas (e a linha não crescer).
    'class="flex w-full items-center justify-center gap-0.5"',
    // M-55: a caixinha de 34×24px com o % Geral e o "%" ao lado; vazia sem %.
    'class="inline-flex h-6 w-[34px] items-center justify-center rounded border bg-background text-xs hover:bg-muted"',
    "{{ ads && pctGeralAds(adsDa(acc.id)) != null ? fmtPct(pctGeralAds(adsDa(acc.id))!) : '' }}",
    '<span class="text-xs text-muted-foreground">%</span>',
    'data-menu-ads',
    // M-57: "Amazon kfa", 12px negrito.
    '<div class="px-1 pb-1 text-xs font-bold" data-ads-titulo>{{ tituloConta(acc) }}</div>',
    'data-campo="ads-geral-ligada"',
    'data-campo="ads-pct"',
    'data-campo="ads-gasto-dia"',
    '{{ fmtRoas(roasMinimo(c.pode_gastar_pct)) }}',
    'data-campo="ads-por-produto-ligada"',
    'data-campo="ads-busca-produto"',
    'v-for="p in produtosDoAds(c)"',
    // M-78: produto sem % próprio fica sem Ads ("—"), não mostra o Geral.
    'placeholder="—"',
    'fmtRoas(roasMinimo(pctDoProdutoAds(c, p.id)))',
    // M-78: as caixas são um ou outro; cada modo mostra só a parte dele.
    'data-ads-modo',
    'class="flex flex-wrap items-center gap-x-4 gap-y-1 border-b px-1 pb-1.5"',
    ":checked=\"modoAds(c) === 'geral'\"",
    ":checked=\"modoAds(c) === 'produto'\"",
    "@change=\"trocarModoAds(acc.id, 'geral')\"",
    "@change=\"trocarModoAds(acc.id, 'produto')\"",
    "v-if=\"modoAds(c) === 'geral'\"",
    "<template v-else-if=\"modoAds(c) === 'produto'\">",
    '<span class="truncate">Todos os produtos</span>',
    'data-ads-cabecalho-produto',
    // M-78: a ajuda do botão diz "Ads por produto" quando a conta está em Produto.
    ":title=\"adsErro ? 'O Ads não carregou — recarregue a página.' : tituloBotaoAds(adsDa(acc.id))\"",
    ':data-ads-produto="p.id"',
    ':disabled="!podeEditarAds"',
    '@change="gravarCampoAds"',
    // M-62: a lista vai até o fim da janela, com respiro embaixo igual ao das laterais.
    'class="max-h-64 overflow-y-auto px-1 pb-1 [scrollbar-gutter:stable]"',
    // M-56: aviso junto do campo.
    '{{ erroAdsDaLinhaGeral(acc.id) }}',
    '{{ erroAdsDoModo(acc.id) }}',
    "erroAdsEm(acc.id, `produto:${p.id}:`, true)",
    // M-79: janela de 40rem (sem passar da tela); clique fora do menu fecha.
    'class="z-[70] w-[40rem] max-w-[calc(100vw-16px)] rounded-md border bg-background p-2 text-xs shadow-lg"',
    '@pointerdown="fecharMenuCategoriaAdsFora"',
    // M-79/M-80: lista vazia diz qual filtro a esvaziou.
    '{{ textoListaAdsVazia(c) }}',
  ]) assert.ok(celula.includes(trecho), `célula Ads tem ${trecho}`)
  assert.ok(!celula.includes('w-[30rem]'), 'a janela de 30rem saiu')
  for (const cab of ['<span>Produto</span>', '<span>Categoria de venda</span>', 'Pode gastar (%)', 'ROAS mínimo</span>', 'Gasto por dia (R$)']) {
    assert.ok(celula.includes(cab), `lista tem a coluna ${cab}`)
  }
  // M-55/M-57: o que saiu.
  // M-55: a caixinha da célula continua sem seta (a seta da janela é a do
  // filtro Categoria de venda, M-79).
  const gatilho = celula.slice(celula.indexOf('<PopoverTrigger'), celula.indexOf('</PopoverTrigger>'))
  assert.ok(gatilho.length > 50 && !gatilho.includes('<ChevronDown'), 'caixinha Ads sem seta')
  assert.equal((celula.match(/<ChevronDown/g) || []).length, 1, 'só a seta do filtro Categoria de venda')
  for (const fora of [
    'ROAS {{', 'Ads — ', '% gasto real', 'ROAS real', 'c.real.', 'Vale para todos os produtos sem % próprio',
    'Últimos 7 dias', 'O produto com % próprio usa o dele', 'Gasto por dia R$', 'text-sm',
    // M-62: a frase do rodapé e a linha dela.
    'Grava ao sair do campo', 'Fora do cálculo de preço', 'Só quem edita Contas altera o Ads', 'mt-1 border-t px-1 pt-1',
    // M-78: o nome antigo, a lista apagada com Produto desmarcado e o Geral
    // como sugestão no % do produto.
    'Por produto', "'opacity-60': !c.ads_por_produto_ligada", 'pctGeralAds(c)',
    'clique para ver o gasto por dia e o % por produto',
  ]) assert.ok(!celula.includes(fora), `célula Ads sem ${fora}`)
  // M-78: de cima para baixo — título, a linha das caixas (Geral e Produto
  // lado a lado), o bloco Geral (cabeçalho + "Todos os produtos") e o bloco
  // Produto (busca, cabeçalho e lista); todos na mesma grade e com a mesma
  // faixa da barra de rolagem.
  const ordem = ['data-ads-titulo', 'data-ads-modo', 'data-campo="ads-geral-ligada"', 'data-campo="ads-por-produto-ligada"',
    'data-ads-cabecalho', 'data-ads-geral', 'data-campo="ads-busca-produto"', 'data-ads-cabecalho-produto',
    'data-lista-ads-produto'].map((t) => celula.indexOf(t))
  assert.ok(ordem.every((i, k) => i > 0 && (k === 0 || i > ordem[k - 1])), `ordem da janela: ${ordem}`)
  // M-79: a grade ganhou a coluna da categoria (6rem) depois do Produto.
  assert.equal((celula.match(/grid-cols-\[minmax\(0,1fr\)_6rem_5rem_4rem_6rem\]/g) || []).length, 4,
    'cabeçalho e linha do Geral, cabeçalho e linhas do Produto na mesma grade')
  assert.equal((celula.match(/grid-cols-\[minmax\(0,1fr\)_5rem_4rem_6rem\]/g) || []).length, 0, 'a grade antiga saiu')
  assert.equal((celula.match(/\[scrollbar-gutter:stable\]/g) || []).length, 3, 'Geral, cabeçalho do Produto e lista com a mesma faixa da barra')
  // A linha das caixas: Geral, Produto e (M-79/M-80, só com Produto marcado)
  // o traço fino, o filtro "Categoria de venda ▾" e a caixinha "Ads"; os
  // campos ficam nos blocos.
  const modo = celula.slice(celula.indexOf('data-ads-modo'), celula.indexOf('data-ads-cabecalho'))
  for (const t of [
    'data-campo="ads-geral-ligada"', 'Geral', 'data-campo="ads-por-produto-ligada"', 'Produto',
    "<template v-if=\"modoAds(c) === 'produto'\">",
    '<span class="h-4 w-px bg-border" aria-hidden="true"></span>',
    'data-filtro-ads-categoria',
    'class="inline-flex h-6 items-center gap-1 rounded border bg-background px-2 text-xs hover:bg-muted"',
    "{{ filtroCategoriaAds.length ? `Categoria de venda · ${filtroCategoriaAds.length}` : 'Categoria de venda' }}",
    '<ChevronDown class="h-3 w-3" />',
    'v-if="menuCategoriaAdsAberto"',
    'v-for="cat in ads?.categorias ?? []"',
    ':checked="filtroCategoriaAds.includes(cat.id)"',
    '@change="alternarCategoriaAds(cat.id)"',
    '<span class="truncate">{{ cat.name }}</span>',
    'data-campo="ads-filtro-so-com-ads"',
    ':checked="soComAds"',
    '@change="alternarFiltroAds"',
  ]) assert.ok(modo.includes(t), `linha das caixas tem ${t}`)
  for (const t of ['data-campo="ads-pct"', 'data-campo="ads-busca-produto"', 'data-lista-ads-produto']) {
    assert.ok(!modo.includes(t), `linha das caixas sem ${t}`)
  }
  // [ ] Geral  [ ] Produto  |  Categoria de venda ▾  [ ] Ads
  const ordemLinha = ['data-campo="ads-geral-ligada"', 'data-campo="ads-por-produto-ligada"',
    "<template v-if=\"modoAds(c) === 'produto'\">", 'h-4 w-px bg-border', 'data-campo="ads-filtro-categoria"',
    'data-menu-ads-categoria', 'data-campo="ads-filtro-so-com-ads"'].map((t) => modo.indexOf(t))
  assert.ok(ordemLinha.every((i, k) => i > 0 && (k === 0 || i > ordemLinha[k - 1])), `ordem da linha das caixas: ${ordemLinha}`)
  // Os filtros são só da tela: não gravam, não ficam travados para quem só vê.
  const filtros = modo.slice(modo.indexOf("<template v-if=\"modoAds(c) === 'produto'\">"))
  for (const t of ['gravarAds', 'trocarModoAds', 'data-ads-conta', ':disabled']) {
    assert.ok(!filtros.includes(t), `filtros sem ${t}`)
  }
  // O menu é só caixinha + nome (sem descrição nem rodapé).
  const menuCat = modo.slice(modo.indexOf('data-menu-ads-categoria'), modo.indexOf('data-campo="ads-filtro-so-com-ads"'))
  assert.equal((menuCat.match(/<span/g) || []).length, 1, 'menu: só o nome')
  assert.equal((menuCat.match(/<p\b|<button/g) || []).length, 0, 'menu sem rodapé nem botões')
  // Bloco Geral: "Todos os produtos" com o %, o ROAS mínimo e o gasto por dia; sem busca nem lista.
  const geral = celula.slice(celula.indexOf("v-if=\"modoAds(c) === 'geral'\""), celula.indexOf("v-else-if=\"modoAds(c) === 'produto'\""))
  for (const t of ['data-ads-cabecalho', 'Todos os produtos', 'data-campo="ads-pct"', 'data-ads-roas-minimo', 'data-campo="ads-gasto-dia"']) {
    assert.ok(geral.includes(t), `bloco Geral tem ${t}`)
  }
  assert.ok(!geral.includes('<span>Produto</span>'), 'cabeçalho do Geral sem a coluna Produto')
  for (const t of ['data-campo="ads-busca-produto"', 'data-lista-ads-produto', 'data-campo="ads-geral-ligada"']) {
    assert.ok(!geral.includes(t), `bloco Geral sem ${t}`)
  }
  // Bloco Produto: busca, cabeçalho com Produto e a lista; sem os campos do Geral.
  const produto = celula.slice(celula.indexOf("v-else-if=\"modoAds(c) === 'produto'\""))
  for (const t of ['data-campo="ads-busca-produto"', '<span>Produto</span>', 'data-lista-ads-produto']) {
    assert.ok(produto.includes(t), `bloco Produto tem ${t}`)
  }
  // M-79: Produto | Categoria de venda | Pode gastar (%) | ROAS mínimo | Gasto
  // por dia (R$); na linha, a categoria logo depois do nome (texto cinza
  // simples, à esquerda, sem etiqueta).
  const cabProduto = produto.slice(produto.indexOf('data-ads-cabecalho-produto'), produto.indexOf('data-lista-ads-produto'))
  const ordemCab = ['<span>Produto</span>', '<span>Categoria de venda</span>', 'Pode gastar (%)', 'ROAS mínimo', 'Gasto por dia (R$)']
    .map((t) => cabProduto.indexOf(t))
  assert.ok(ordemCab.every((i, k) => i > 0 && (k === 0 || i > ordemCab[k - 1])), `ordem do cabeçalho: ${ordemCab}`)
  const linhaProduto = produto.slice(produto.indexOf(':data-ads-produto-linha="p.id"'), produto.indexOf('data-ads-roas-produto'))
  assert.ok(linhaProduto.indexOf('{{ p.name }}') < linhaProduto.indexOf('data-ads-categoria'), 'categoria depois do nome')
  assert.ok(linhaProduto.indexOf('data-ads-categoria') < linhaProduto.indexOf('data-ads-campo="pode_gastar_pct"'), 'e antes do %')
  assert.match(linhaProduto, /<span\s+class="truncate text-muted-foreground"\s+:title="p\.categoria\?\.name \?\? ''"\s+data-ads-categoria\s*>\{\{ p\.categoria\?\.name \?\? '' \}\}<\/span>/)
  assert.ok(!geral.includes('Categoria de venda'), 'a Geral não tem categoria de venda')
  for (const t of ['data-campo="ads-pct"', 'data-campo="ads-gasto-dia"', 'data-ads-geral']) {
    assert.ok(!produto.includes(t), `bloco Produto sem ${t}`)
  }
  // 12px, campos de 24px, caixas de 14px, busca de 24px.
  assert.ok(!/class="[^"]*\bh-7\b/.test(celula), 'sem campos de 28px')
  // Geral, Produto, a caixa de cada categoria no menu (M-79) e a caixinha Ads (M-80).
  assert.equal((celula.match(/type="checkbox"\s+class="size-3\.5"/g) || []).length, 4)
  assert.match(celula, /class="h-6 w-full border rounded pl-6 pr-2 text-xs bg-background"\s+data-campo="ads-busca-produto"/)
  // O texto antigo saiu da tela (fica no banco): a célula não edita `ads`.
  assert.doesNotMatch(celula, /startEditAccount\(acc, f\.key\)/)
  // M-79/M-80: a Ajuda · Contas explica os dois filtros.
  for (const t of ['o botão Categoria de venda mostra só os produtos das categorias marcadas',
    'a caixinha Ads só os que têm % ou gasto por dia nesta conta', 'voltam zerados ao abrir a janela']) {
    assert.ok(setup.includes(t), `ajuda fala de: ${t}`)
  }
  // Os dois filtros zeram ao trocar de departamento (como a busca).
  const trocaDep = setup.slice(setup.indexOf('watch(department, async () => {'))
  assert.ok(trocaDep.indexOf('zerarFiltrosAds()') > 0 && trocaDep.indexOf('zerarFiltrosAds()') < trocaDep.indexOf('})'))
  // A leitura: na aba Contas e ao trocar de departamento nela.
  assert.match(setup, /if \(t === 'contas'\) void carregarAds\(\)/)
  assert.match(setup, /if \(tab\.value === 'contas'\) void carregarAds\(\)/)
  console.log('pricing contas ads: template ok')
  console.log('pricing-contas-ads: ok')
})().catch((e) => {
  console.error(e)
  process.exit(1)
})
