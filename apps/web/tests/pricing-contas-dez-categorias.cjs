// Run from apps/web: node tests/pricing-contas-dez-categorias.cjs
// M-93 (Harry 26/11/2026): cada tabela de Contas (Celular, Eletro, Mala) aceita até 10
// categorias de venda. Trava, executando as funções reais da página e conferindo o template:
//  - o teto MAX_CATEGORIAS_VENDA = 10, o mesmo do servidor (services/pricing/posicoes.py);
//  - aba Contas: uma dupla marg | frete por categoria ATIVA, na ordem, lendo e gravando
//    margin1..10 / shipping1..10 pelos mesmos nomes da API; categoria nova mostra "—";
//  - rolagem lateral dentro do quadro, com Plataforma e Conta FIXAS à esquerda (sticky, fundo
//    opaco) no cabeçalho, na linha de adicionar e nas linhas; z-index abaixo do cabeçalho; a
//    janela de Slots sobe junto quando aberta;
//  - Catálogo ML: a janela de margens com as 10 posições e as margens próprias 6..10;
//  - aba Produtos: o nome e a cor da categoria 6..10 (antes presa na 5ª).
// Exemplo do pedido: Eletro com 10 (Acessórios, airfryer, barbecue, slush, 5, cafeteira,
// cooktop, cookware, purificador, smart cooking); Amazon atlas continua com 53,0% / 30 nas
// cinco primeiras e "—" nas cinco novas. Só dados FALSOS; nenhuma rede.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')

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
const pega = (nome) => {
  const node = source.statements.find((c) =>
    (ts.isFunctionDeclaration(c) && c.name?.text === nome)
    || (ts.isVariableStatement(c) && c.declarationList.declarations.some((d) => d.name.getText(source) === nome)))
  assert.ok(node, `${nome} existe na página`)
  return node.getText(source)
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

const ELETRO = ['Acessórios', 'airfryer', 'barbecue', 'slush', '5', 'cafeteira', 'cooktop', 'cookware', 'purificador',
  'smart cooking']

// ---------------------------------------------------------------- 1) o teto
const MAX = new Function(`${js(pega('MAX_CATEGORIAS_VENDA'))}\nreturn MAX_CATEGORIAS_VENDA`)()
assert.equal(MAX, 10, 'até 10 categorias de venda')
{
  const servidor = fs.readFileSync(path.resolve(__dirname, '../../api/app/services/pricing/posicoes.py'), 'utf8')
  assert.match(servidor, /^MAX_CATEGORIAS_VENDA = 10$/m, 'o mesmo número do servidor')
}
// Nenhum laço ou teto de 5 posições sobrou na página.
for (const velho of [/<= 5\b/, /< 5\b/, /Math\.min\(5,/, /v-for="i in 5"/, /\[null, null, null, null, null\]/]) {
  assert.doesNotMatch(setup + descriptor.template.content, velho, `sem o 5 fixo: ${velho}`)
}
console.log('pricing contas 10 categorias: teto ok')

// ---------------------------------------------------------------- 2) tiposAba / nTiposAba
{
  const department = ref('eletro')
  const TYPE_HEADERS = ref({ eletro: ELETRO, mala: ['Acessórios', '12"', '18" e 20"', '24" acima'], teste: [] })
  const g = new Function('department', 'TYPE_HEADERS', js([
    pega('MAX_CATEGORIAS_VENDA'), computado('tiposAba'), computado('nTiposAba'), 'return { tiposAba, nTiposAba }',
  ].join('\n')))(department, TYPE_HEADERS)
  assert.deepEqual(g.tiposAba.value, ELETRO, 'as 10, na ordem')
  assert.equal(g.nTiposAba.value, 10)
  department.value = 'mala'
  assert.equal(g.nTiposAba.value, 4, 'Mala continua com as 4 ativas')
  // Mais que 10 nunca vem do servidor; se viesse, a tela para na 10ª (não existe margin11).
  TYPE_HEADERS.value.teste = Array.from({ length: 12 }, (_, i) => `c${i + 1}`)
  department.value = 'teste'
  assert.equal(g.nTiposAba.value, 10)
  department.value = 'sem-tipos'
  assert.equal(g.nTiposAba.value, 0)
}
console.log('pricing contas 10 categorias: tipos da aba ok')

async function main() {
  // ---------------------------------------------------------------- 3) células, "—" e edição
  {
    const fmt = new Function(`${js([pega('fmtMargin'), pega('fmtShipping')].join('\n'))}\nreturn { fmtMargin, fmtShipping }`)()
    const atlas = {
      id: 'atlas', name: 'atlas', platform: 'amazon', department: 'eletro', canal: 'kit', par_id: null,
      ...Object.fromEntries([1, 2, 3, 4, 5].flatMap((n) => [[`margin${n}`, '0.5300'], [`shipping${n}`, '30.00']])),
      margin6: null, shipping6: null, margin7: null, shipping7: null, margin8: null, shipping8: null,
      margin9: null, shipping9: null, margin10: null, shipping10: null,
    }
    const celulas = (acc) => Array.from({ length: 10 }, (_, i) =>
      `${fmt.fmtMargin(acc[`margin${i + 1}`])} ${fmt.fmtShipping(acc[`shipping${i + 1}`])}`)
    assert.deepEqual(celulas(atlas), [...Array(5).fill('53.0% 30'), ...Array(5).fill('— —')],
      'atlas: 53,0% / 30 nas cinco primeiras e "—" nas cinco novas')
    // Servidor antigo (sem os campos novos) também mostra "—", sem quebrar.
    const semCampos = { ...atlas }
    for (let n = 6; n <= 10; n++) { delete semCampos[`margin${n}`]; delete semCampos[`shipping${n}`] }
    assert.deepEqual(celulas(semCampos).slice(5), Array(5).fill('— —'))

    // Edição igual à de hoje, pelos mesmos nomes (margin7, shipping9).
    const accounts = ref([atlas])
    const pedidos = []
    let respostas = []
    const api = async (url, opts = {}) => {
      pedidos.push([url, opts.method ?? 'GET', opts.body])
      const r = respostas.shift()
      if (r instanceof Error) throw r
      return r
    }
    const piscadas = []
    const patch = new Function('accounts', 'api', 'flash', 'accountsErr', 'erroCustoTotal', 'loadAccounts', 'confirm',
      'erroPlataforma', js([
        pega('ehCatalogo'), pega('ordemTipo'), pega('lerPct'), pega('parseDec'),
        computado('linhasPorPar'), pega('parDe'), pega('irmaDe'),
        pega('CAMPOS_SO_DA_LINHA'), pega('espelharNaIrma'),
        computado('catalogoPorBase'), pega('catalogoAtivo'),
        pega('erroCustoTotalDe'), pega('_patchMargemCatalogo'), pega('_patchAccount'),
        'return _patchAccount',
      ].join('\n')))(accounts, api, (id, c) => piscadas.push(`${id}:${c}`), ref(null), ref(null), async () => {},
      () => true, ref(null))
    respostas = [{ ...atlas, margin7: '0.4500', conta_catalogo_id: null }]
    await patch('atlas', 'margin7', '45')
    assert.deepEqual(pedidos.pop(), ['/api/pricing/accounts/atlas', 'PATCH', { margin7: '0.4500' }])
    assert.equal(atlas.margin7, '0.4500')
    assert.equal(atlas.margin5, '0.5300', 'a 5ª não mexe')
    respostas = [{ ...atlas, shipping9: '30.00', conta_catalogo_id: null }]
    await patch('atlas', 'shipping9', '30')
    assert.deepEqual(pedidos.pop()[2], { shipping9: 30 })
    respostas = [{ ...atlas, margin7: null, conta_catalogo_id: null }]
    await patch('atlas', 'margin7', '—')
    assert.deepEqual(pedidos.pop()[2], { margin7: null }, '"—" volta a vazio')
    assert.deepEqual(piscadas, ['atlas:margin7', 'atlas:shipping9', 'atlas:margin7'])
  }
  console.log('pricing contas 10 categorias: células e edição ok')

  // ---------------------------------------------------------------- 4) Catálogo ML com 10 posições
  {
    const conta = (id, extra = {}) => ({
      id, name: id, platform: 'mercadolivre', canal: 'kit', department: 'eletro', sort_order: 0, par_id: null,
      listing_type: 'ml classico', commission: '0.14', ...extra,
    })
    const base = conta('base', { margin8: '0.3000', margin10: null })
    const filha = conta('filha', { canal: 'catalogo', conta_base_id: 'base', margens_catalogo: Object.fromEntries(
      Array.from({ length: 10 }, (_, i) => [String(i + 1), i === 7 ? '0.4000' : null])) })
    const accounts = ref([base, filha])
    const pedidos = []
    const api = async (url, opts = {}) => { pedidos.push([url, opts.method ?? 'GET', opts.body]); return {} }
    const department = ref('eletro')
    const TYPE_HEADERS = ref({ eletro: ELETRO })
    const fmtMargin = (v) => (v == null || v === '' ? '—' : `${(Number(v) * 100).toFixed(1)}%`)
    const g = new Function('accounts', 'isAdmin', 'canEditContas', 'api', 'flash', 'ref', 'loadAccounts', 'confirm', 'toast',
      'department', 'TYPE_HEADERS', 'fmtMargin', 'platformLabel', 'rotuloTipo', 'nomeComTipo', js([
        pega('MAX_CATEGORIAS_VENDA'), computado('tiposAba'), computado('nTiposAba'),
        pega('ehCatalogo'), pega('ordemTipo'), pega('parseDec'),
        computado('linhasPorPar'), pega('parDe'), pega('tituloConta'),
        computado('catalogoPorBase'), pega('catalogoAtivo'), pega('podeTerCatalogo'), pega('margemPropria'),
        pega('temMargemPropria'), pega('resumoMargemPropria'),
        pega('catalogoJanelaId'), pega('catalogoJanelaAtivo'), pega('catalogoJanelaMargens'),
        pega('catalogoJanelaUsarDaConta'), pega('catalogoJanelaValores'), pega('catalogoJanelaSalvando'),
        pega('catalogoJanelaErro'), pega('catalogoJanelaUsarAberto'), pega('catalogoJanelaValoresAbertos'),
        pega('textoMargemCatalogo'), pega('margemDaContaTexto'), pega('abrirCatalogo'), pega('fecharCatalogo'),
        pega('tituloCatalogoDaConta'), pega('textoErroCatalogo'), pega('salvarCatalogo'),
        `return { catalogoJanelaId, catalogoJanelaUsarDaConta, catalogoJanelaValores, temMargemPropria,
          resumoMargemPropria, margemDaContaTexto, abrirCatalogo, salvarCatalogo, tituloCatalogoDaConta }`,
      ].join('\n')))(accounts, ref(true), ref(true), api, () => {}, ref, async () => {}, () => true,
      { success: () => {} }, department, TYPE_HEADERS, fmtMargin, (p) => p, () => 'clássico', (a) => a.name)

    // Margem própria só na 8ª (cookware): conta como "margens próprias" e aparece no balão.
    assert.equal(g.temMargemPropria(base), true, 'posição 8 conta como margem própria')
    assert.equal(g.resumoMargemPropria(filha), 'cookware 40.0%')
    assert.equal(g.tituloCatalogoDaConta(base), 'Catálogo ML ativo — margens próprias: cookware 40.0%')
    assert.equal(g.margemDaContaTexto(base, 8), '30,0')
    assert.equal(g.margemDaContaTexto(base, 10), '—', 'posição nova sem margem: "—"')
    g.abrirCatalogo(base)
    assert.equal(g.catalogoJanelaId.value, 'base')
    assert.equal(g.catalogoJanelaUsarDaConta.value, false)
    assert.deepEqual(g.catalogoJanelaValores.value, ['', '', '', '', '', '', '', '40', '', ''], 'um campo por categoria')
    // Muda só a 10ª (smart cooking): só ela vai, na conta de catálogo.
    g.catalogoJanelaValores.value[9] = '22,5'
    await g.salvarCatalogo()
    assert.deepEqual(pedidos, [['/api/pricing/accounts/filha', 'PATCH', { margin10: '0.2250' }]])
  }
  console.log('pricing contas 10 categorias: Catálogo ML ok')

  // ---------------------------------------------------------------- 5) aba Produtos: nome e cor 6..10
  {
    const TYPE_HEADERS = ref({ eletro: ELETRO, celular: ['Acessórios', 'Diversos', 'Regular', 'Robusto', 'Apple'] })
    const g = new Function('TYPE_HEADERS', `${js([pega('MAX_CATEGORIAS_VENDA'), pega('tabelaName'), pega('tabelaBadgeClass')]
      .join('\n'))}\nreturn { tabelaName, tabelaBadgeClass }`)(TYPE_HEADERS)
    const p = (product_type, department = 'eletro') => ({ product_type, department })
    assert.deepEqual([1, 5, 6, 7, 8, 9, 10].map((t) => g.tabelaName(p(t))),
      ['Acessórios', '5', 'cafeteira', 'cooktop', 'cookware', 'purificador', 'smart cooking'], 'a 6ª+ não vira a 5ª')
    assert.equal(g.tabelaName(p(12)), 'smart cooking', 'acima de 10 fica na 10ª, como o servidor')
    assert.equal(g.tabelaName(p(5, 'celular')), 'Apple')
    const cores = Array.from({ length: 10 }, (_, i) => g.tabelaBadgeClass(p(i + 1)))
    assert.equal(new Set(cores).size, 10, 'uma cor por posição')
    assert.deepEqual(cores.slice(0, 5), ['bg-slate-100 text-slate-700', 'bg-blue-100 text-blue-700',
      'bg-emerald-100 text-emerald-700', 'bg-orange-100 text-orange-700', 'bg-purple-100 text-purple-700'],
      'as 5 primeiras com as cores de antes')
  }
  console.log('pricing contas 10 categorias: aba Produtos ok')

  // ---------------------------------------------------------------- 6) template: rolagem e colunas fixas
  {
    const tpl = descriptor.template.content
    const contas = tpl.slice(tpl.indexOf("tab === 'contas'"), tpl.indexOf("tab === 'produtos'"))
    // A tabela rola dentro do próprio quadro (para o lado e para baixo).
    assert.match(contas, /<div class="border rounded-lg overflow-auto max-h-\[calc\(100vh-320px\)\]">\s*<table class="w-full text-sm border-collapse">\s*<thead class="sticky top-0 bg-muted z-10">/)
    const cab = contas.slice(0, contas.indexOf('</thead>'))
    const thFixa = (qual) => cab.match(new RegExp(`<th class="([^"]*)" data-coluna-fixa="${qual}">`))?.[1]
    const thPlat = thFixa('plataforma')
    const thConta = thFixa('conta')
    assert.ok(thPlat && thConta, 'Plataforma e Conta marcadas como fixas no cabeçalho')
    for (const c of ['sticky', 'left-0', 'z-[1]', 'bg-muted', 'w-[100px]', 'min-w-[100px]']) assert.ok(thPlat.split(' ').includes(c), `th Plataforma: ${c}`)
    for (const c of ['sticky', 'left-[100px]', 'z-[1]', 'bg-muted']) assert.ok(thConta.split(' ').includes(c), `th Conta: ${c}`)
    // Com border-collapse a borda vertical de célula sticky não anda junto: as linhas das
    // fixas são before:/after: de 1px (a da direita da Conta separa o que rola por baixo).
    const LINHA_ESQ = 'before:absolute before:inset-y-0 before:-left-px before:w-px before:bg-border'
    const LINHA_DIR = 'after:absolute after:inset-y-0 after:-right-px after:w-px after:bg-border'
    assert.ok(thConta.includes(LINHA_DIR), 'cabeçalho: linha à direita da Conta')
    assert.ok(!thPlat.includes('before:') && !thPlat.includes('after:'), 'cabeçalho sem linhas verticais, como antes')
    // Pares: um por categoria ativa, com o nome e "marg frete" embaixo.
    assert.match(cab, /<th\s+v-for="\(label, i\) in tiposAba"\s+:key="i"\s+colSpan="2"[\s\S]*?<div>\{\{ label \}\}<\/div>\s*<div class="text-muted-foreground flex justify-center gap-3">\s*<span>marg<\/span><span>frete<\/span>/)

    // Linha de adicionar: as duas fixas com fundo opaco; os pares com "—".
    const add = contas.slice(contas.indexOf('<!-- add row'), contas.indexOf('<!-- data rows grouped'))
    const tdsAdd = [...add.matchAll(/<td class="([^"]*)" data-coluna-fixa="(plataforma|conta)">/g)]
    assert.deepEqual(tdsAdd.map((m) => m[2]), ['plataforma', 'conta'])
    for (const [, classes, qual] of tdsAdd) {
      for (const c of ['sticky', 'z-[1]', 'bg-background', qual === 'plataforma' ? 'left-0' : 'left-[100px]']) {
        assert.ok(classes.split(' ').includes(c), `linha nova, ${qual}: ${c}`)
      }
      assert.ok(classes.includes(LINHA_ESQ), `linha nova, ${qual}: linha da esquerda`)
      assert.equal(classes.includes(LINHA_DIR), qual === 'conta', `linha nova, ${qual}: linha da direita só na Conta`)
    }
    assert.match(add, /<td v-for="i in nTiposAba \* 2" :key="i" class="[^"]*">—<\/td>/)

    // Linhas das contas.
    const linha = contas.slice(contas.indexOf('<!-- data rows grouped by platform'))
    assert.match(linha, /<span class="sticky left-3">\{\{ group\.label \}\} · \{\{ group\.rows\.length \}\} CONTA\(S\)<\/span>/,
      'o nome do grupo acompanha ao rolar')
    assert.match(linha, /<tr class="group\/linha hover:bg-accent\/30">/)
    const celPlat = linha.slice(linha.indexOf('<td\n'), linha.indexOf('data-coluna-plataforma'))
    assert.match(celPlat, /class="[^"]*\bsticky left-0 z-\[1\] w-\[100px\] min-w-\[100px\][^"]*"/)
    assert.match(celPlat, /isFlashed\(acc\.id, 'platform'\) \? 'bg-emerald-50 dark:bg-emerald-950' : 'bg-background'/,
      'fundo sempre opaco (o verde de gravado também)')
    assert.ok(celPlat.includes('data-coluna-fixa="plataforma"'))
    const iConta = linha.indexOf('<!-- conta (o nome')
    const celConta = linha.slice(iConta, linha.indexOf('<!-- Catálogo ML (M-54)', iConta))
    assert.match(celConta, /class="[^"]*\bsticky left-\[100px\][^"]*"/)
    assert.match(celConta, /slotPopoverAccountId === acc\.id \? 'z-\[9\]' : 'z-\[1\]'/, 'a janela de Slots sobe junto')
    assert.match(celConta, /isFlashed\(acc\.id, 'name'\) \? 'bg-emerald-50 dark:bg-emerald-950' : 'bg-background'/)
    assert.ok(celConta.includes('data-coluna-fixa="conta"'))
    assert.ok(celPlat.includes(LINHA_ESQ) && !celPlat.includes(LINHA_DIR), 'Plataforma: linha da esquerda')
    assert.ok(celConta.includes(LINHA_ESQ) && celConta.includes(LINHA_DIR), 'Conta: linhas dos dois lados')
    assert.ok(!/class="[^"]*\brelative\b/.test(celConta.slice(0, celConta.indexOf('>'))), 'sticky já posiciona a janela de Slots')
    // Janela de Slots: tantos quantos a conta tem (5 ou até 10), com rolagem própria.
    assert.match(celConta, /<div v-for="i in slotPopoverValues\.length" :key="i">/)
    // Pares da linha: margin{t}/shipping{t} para t = 1..nTiposAba.
    assert.match(linha, /<template v-for="t in nTiposAba" :key="t">[\s\S]*?isEditing\(acc\.id, `margin\$\{t\}`\)[\s\S]*?fmtMargin\(\(acc as any\)\[`margin\$\{t\}`\]\)[\s\S]*?fmtShipping\(\(acc as any\)\[`shipping\$\{t\}`\]\)/)
    // As camadas: cabeçalho (z-10) > célula da janela de Slots aberta (z-[9]) > colunas fixas (z-[1]).
    assert.ok(!/z-\[(1[0-9]|[2-9][0-9])\]/.test(celPlat + celConta), 'nenhuma coluna fixa passa o cabeçalho')
    // Catálogo ML: os campos de margem seguem as categorias da aba.
    assert.match(linha, /v-for="\(nome, i\) in tiposAba"[\s\S]*?:value="margemDaContaTexto\(acc, i \+ 1\)"[\s\S]*?v-model="catalogoJanelaValores\[i\]"/)
  }
  console.log('pricing contas 10 categorias: template ok')

  // ---------------------------------------------------------------- 7) Ajuda · Contas
  {
    const AJUDA = new Function(`${js(pega('AJUDA_CONTAS'))}\nreturn AJUDA_CONTAS`)()
    const colunas = AJUDA.conteudo.find((s) => s.titulo === 'Colunas').itens
    const texto = (termo) => colunas.find((i) => i.termo === termo).texto
    assert.match(texto('marg e frete (ADM)'), /cada categoria de venda ativa, na ordem \(até 10\)/)
    assert.match(texto('marg e frete (ADM)'), /começa com "—" em todas as contas/)
    assert.match(texto('Plataforma e Conta'), /ficam fixas à esquerda/)
    assert.doesNotMatch(JSON.stringify(AJUDA), /\b5 categorias|até 5\b/)
  }
  console.log('pricing contas 10 categorias: ajuda ok')

  // ---------------------------------------------------------------- 8) o SFC inteiro compila
  {
    const script = compileScript(descriptor, { id: 'pricing-tab', inlineTemplate: false })
    for (const b of ['MAX_CATEGORIAS_VENDA', 'tiposAba', 'nTiposAba', 'nSlotsDaConta']) assert.ok(script.bindings[b], `binding ${b}`)
    const r = compileTemplate({ source: descriptor.template.content, filename, id: 'pricing-tab', compilerOptions: { bindingMetadata: script.bindings } })
    assert.deepEqual(r.errors, [])
  }
  console.log('pricing-contas-dez-categorias: ok')
}

main().catch((e) => {
  console.error(e)
  process.exit(1)
})
