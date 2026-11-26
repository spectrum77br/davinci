// Run from apps/web: node tests/cadastro-produtos-sfc.cjs
// Cadastros › Produtos (M-29, Harry 24/11/2026): aba nova depois de Integrações com a
// tabela Tipo | Nome (categorias e tags de envio). Trava:
//  - a aba (lib/navGroups) depois de Integrações, com a permissão `cadastro_produtos`;
//  - o desenho: título "Produtos" sem descrição; recarregar, + Novo tipo e + Novo cadastro
//    (azul) à direita; colunas Tipo | Nome | Ações (lápis e lixeira); letras da aba Contas
//    da Tabela de Preços (cabeçalho 11px semibold, células text-xs, bordas, mesma altura);
//  - botões de mexer só para administrador;
//  - os pedidos ao servidor: criar/renomear tipo, criar/editar cadastro, a lixeira sempre
//    pergunta antes (GET /uso) e vai com confirmar=true; categoria em uso só avisa (sem
//    Excluir); tag em uso no lápis pede "Trocar mesmo assim?" e reenvia com confirmar.
//  - M-75: coluna "Centro de custo" (Tipo | Nome | Centro de custo | Ações, "—" nas linhas sem
//    centro); tipo "Categoria de venda" (os segmentos filhos) pede o centro de custo no Novo
//    cadastro e vai pelas rotas /categorias-venda (criar, renomear, uso, excluir sem confirmar);
//    a marca "inativa"; o tipo não troca com os outros no lápis.
//  - M-76: a aba "Centro de custo" vem logo depois de Produtos (tests/cadastro-centro-custo-sfc.cjs
//    trava a tela dela); o menu "Centro de custo" é a lista de Cadastros › Centro de custo e o
//    centro novo (aceita_categoria false) avisa e trava o Salvar.
//  - M-84: a coluna "Centro de custo" saiu da tabela (Tipo | Nome | Ações); o centro segue no
//    Novo cadastro e no lápis da Categoria de venda.
//  - M-89: coluna "Tabela" antes de Tipo (Tabela | Tipo | Nome | Ações); "Tipo de conta" (Lojas)
//    só para ver ("—" em Ações, fora dos menus, sem renomear); o Novo cadastro escolhe primeiro a
//    Tabela (Produtos 2 → Categoria/Tag de envio; Contas … → Categoria de venda, com o centro da
//    tabela; centro sem raiz fora do menu); no lápis a Tabela só aparece.
//  - M-90: lixeira da Categoria de venda em uso não bloqueia — quadro vermelho, "Transferir
//    para" (escolha única entre os destinos do servidor), textos da transferência, botão
//    "Transferir e excluir" apagado até escolher e DELETE com ?destino_id=; sem uso, os avisos
//    do servidor (nome reservado, produtos da tela Produtos sem segmento) e DELETE simples.
// Só dados FALSOS; nenhuma rede.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { parse, compileTemplate } = require('vue/compiler-sfc')

const transpile = (source, module = ts.ModuleKind.CommonJS) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module },
}).outputText

// ---------------------------------------------------------------- aba
{
  const exp = {}
  new Function('exports', 'require', transpile(fs.readFileSync(path.join(__dirname, '../lib/navGroups.ts'), 'utf8')))(exp, require)
  const labels = exp.TABS_CADASTROS.map((t) => t.label)
  // M-76: Centro de custo entrou logo depois de Produtos.
  assert.deepEqual(labels.slice(-3), ['Integrações', 'Produtos', 'Centro de custo'], 'Produtos logo depois de Integrações')
  const aba = exp.TABS_CADASTROS.at(-2)
  assert.equal(aba.to, '/cadastro-produtos')
  assert.equal(aba.resource, 'cadastro_produtos')
  const ver = { role: 'user', permissions: { cadastro_produtos: { view: true } } }
  const nao = { role: 'user', permissions: { segmentos: { view: true } } }
  assert.ok(exp.allowedTabs(exp.TABS_CADASTROS, ver).some((t) => t.label === 'Produtos'))
  assert.ok(!exp.allowedTabs(exp.TABS_CADASTROS, nao).some((t) => t.label === 'Produtos'))
  assert.ok(exp.allowedTabs(exp.TABS_CADASTROS, { role: 'admin' }).some((t) => t.label === 'Produtos'))
  const can = fs.readFileSync(path.join(__dirname, '../composables/useCan.ts'), 'utf8')
  assert.match(can, /\| 'cadastro_produtos'/)
  assert.match(can, /cadastro_produtos: 'Produtos — /)
  const perms = fs.readFileSync(path.join(__dirname, '../../api/app/schemas/permissions.py'), 'utf8')
  assert.match(perms, /"cadastro_produtos",/, 'mesmo recurso no servidor')
}

// ---------------------------------------------------------------- SFC
const filename = path.join(__dirname, '../pages/cadastro-produtos.vue')
const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
assert.deepEqual(errors, [])
const compiled = compileTemplate({ source: descriptor.template.content, filename, id: 'cad-prod' })
assert.deepEqual(compiled.errors, [])
new Function('exports', 'require', transpile(compiled.code))({}, require)
const tpl = descriptor.template.content
const script = descriptor.scriptSetup.content
{
  assert.match(script, /permission: \{ resource: 'cadastro_produtos', action: 'view' \}/)
  assert.match(tpl, /<RouteTabs :tabs="TABS_CADASTROS" \/>/)
  assert.match(tpl, /<PageHeader title="Produtos">/, 'título Produtos, sem descrição')
  assert.doesNotMatch(tpl, /<PageHeader[^>]*description/)
  const acoes = tpl.slice(tpl.indexOf('<template #actions>'), tpl.indexOf('</template>', tpl.indexOf('<template #actions>')))
  const ordem = ['recarregar', 'Novo tipo', 'Novo cadastro'].map((t) => acoes.indexOf(t))
  assert.ok(ordem.every((i) => i > 0) && ordem[0] < ordem[1] && ordem[1] < ordem[2], 'recarregar, + Novo tipo, + Novo cadastro')
  assert.match(acoes, /<Button v-if="isAdmin" size="sm" data-acao="novo-cadastro"/, 'Novo cadastro azul (variante padrão) e só admin')
  assert.match(acoes, /<Button v-if="isAdmin" size="sm" variant="outline" data-acao="novo-tipo"/)
  const ths = [...tpl.matchAll(/<th\b[^>]*>([^<]*)<\/th>/g)]
  // M-84: a coluna "Centro de custo" saiu. M-89: "Tabela" antes de Tipo.
  assert.deepEqual(ths.map((m) => m[1]), ['Tabela', 'Tipo', 'Nome', 'Ações'])
  for (const m of ths) assert.match(m[0], /text-\[11px\] font-semibold leading-\[14px\]/, 'cabeçalho 11px semibold')
  const linha = tpl.slice(tpl.indexOf('<tr v-for="c in cadastros"'), tpl.indexOf('</tr>', tpl.indexOf('<tr v-for="c in cadastros"')))
  assert.match(linha, /class="h-8 /, 'linhas da mesma altura')
  assert.equal((linha.match(/class="border border-border [^"]*text-xs/g) || []).length, 3, 'Tabela, Tipo e Nome em text-xs com borda')
  assert.match(linha, /<div v-else-if="isAdmin"[\s\S]*<Pencil[\s\S]*<Trash2/, 'lápis e lixeira, só admin')
  // M-89: Tipo de conta só para ver — "—" no lugar do lápis e da lixeira
  assert.match(linha, /<span v-if="!podeMexer\(c\)"[^>]*data-marca="so-ver">—<\/span>\s*<div v-else-if="isAdmin"/)
  for (const m of tpl.matchAll(/colspan="(\d+)"/g)) assert.equal(m[1], '4')
  // M-84: nenhuma célula do centro de custo na linha; a marca "inativa" com o nome em cinza segue
  assert.doesNotMatch(linha, /c\.centro_custo/, 'sem a célula do centro de custo')
  assert.match(linha, /:class="\{ 'text-muted-foreground': c\.ativa === false \}"[\s\S]*v-if="c\.ativa === false"[\s\S]*>inativa<\/span>/)
  const ordemCols = ["{{ c.tabela || '—' }}", '{{ c.tipo_nome }}', '{{ c.nome }}', '<Pencil'].map((t) => linha.indexOf(t))
  assert.ok(ordemCols.every((i, k) => i > 0 && (k === 0 || i > ordemCols[k - 1])), 'Tabela | Tipo | Nome | Ações')
  // M-89: Novo cadastro escolhe a Tabela (menu, antes do Tipo); no lápis a Tabela aparece travada
  // e, na Categoria de venda, o tipo também fica travado
  assert.match(tpl, /<label v-if="cadEditando"[\s\S]{0,120}Tabela[\s\S]{0,120}:value="cadEditando\.tabela \|\| '—'"[\s\S]{0,60}disabled[\s\S]{0,200}data-campo="cadastro-tabela-fixa"/)
  assert.match(tpl, /<label v-else[\s\S]{0,120}Tabela[\s\S]{0,60}<select[\s\S]{0,40}v-model="cadTabela"[\s\S]{0,200}data-campo="cadastro-tabela"\s+@change="trocarTabela"[\s\S]{0,60}v-for="r in tabelasDoMenu"/)
  assert.ok(tpl.indexOf('data-campo="cadastro-tabela"') < tpl.indexOf('data-campo="cadastro-tipo"'), 'Tabela antes do Tipo')
  assert.doesNotMatch(tpl, /data-campo="cadastro-centro/, 'o menu Centro de custo virou o menu Tabela')
  assert.match(tpl, /:placeholder="cadEhVenda \? 'ex\.: Acessórios' : 'ex\.: \.sp ou Celular'"/)
  assert.match(tpl, /:disabled="ehVenda\(cadEditando\)"/)
  assert.match(tpl, /v-for="t in tiposDoMenu"/)
  // Os tipos Categoria de venda e Tipo de conta não são renomeados na janela de tipos
  assert.match(tpl, /<button v-if="!ehVenda\(t\) && !ehTipoConta\(t\)"[^>]*data-acao="renomear-tipo"/)
}

// ---------------------------------------------------------------- script setup com api falso
const pageScript = script.replace(/^import[\s\S]*?from\s+'[^']+'\s*$/gm, '')
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const factory = new AsyncFunction(
  'ref', 'computed', 'definePageMeta', 'useApi', 'useAuthStore', 'TABS_CADASTROS',
  transpile(pageScript, ts.ModuleKind.ESNext) + `\nreturn {
    tipos, cadastros, error, load, isAdmin,
    tipoAberto, tipoNome, tipoErro, abrirTipo, criarTipo, editarTipo, tipoEditNome, salvarTipo,
    cadAberto, cadTipo, cadNome, cadErro, cadAviso, abrirCadastro, salvarCadastro,
    excluindo, excluirUso, excluirErro, pedirExclusao, confirmarExclusao,
    centros, cadCentro, cadEhVenda, tiposDoMenu,
    cadTabela, tabelasDoMenu, trocarTabela, podeMexer, cadEditando,
    excluirDestino, escolherDestino, precisaDestino, textoTransferencia, textoPrecos, textoTelaProdutos,
    doMeio, textosDoMeio,
  }`,
)

const TIPOS = [
  { id: 't1', nome: 'Categoria', chave: 'categoria', tabela: 'Produtos 2' },
  { id: 'tv', nome: 'Categoria de venda', chave: 'categoria_venda', tabela: 'Contas' },
  { id: 't2', nome: 'Tag de envio', chave: 'tag_envio', tabela: 'Produtos 2' },
  { id: 'tc', nome: 'Tipo de conta', chave: 'tipo_conta', tabela: 'Lojas' },
]
const CADS = [
  { id: 'c1', tipo_id: 't1', tipo_nome: 'Categoria', tipo_chave: 'categoria', nome: 'Celular', tabela: 'Produtos 2' },
  { id: 'c2', tipo_id: 't2', tipo_nome: 'Tag de envio', tipo_chave: 'tag_envio', nome: '.sp', tabela: 'Produtos 2' },
  { id: 's1', tipo_id: 'tv', tipo_nome: 'Categoria de venda', tipo_chave: 'categoria_venda', nome: 'Apple', centro_custo: 'Celular', ativa: true, tabela: 'Contas Celular' },
  { id: 's2', tipo_id: 'tv', tipo_nome: 'Categoria de venda', tipo_chave: 'categoria_venda', nome: 'Queima de estoque', centro_custo: 'Mala', ativa: false, tabela: 'Contas Mala' },
  { id: 'r-cel', tipo_id: 'tc', tipo_nome: 'Tipo de conta', tipo_chave: 'tipo_conta', nome: 'Celular', tabela: 'Lojas' },
]
const CENTROS = [
  { id: 'r-cel', nome: 'Celular', aceita_categoria: true, tabela: 'Contas Celular' },
  { id: 'r-mala', nome: 'Mala', aceita_categoria: true, tabela: 'Contas Mala' },
  { id: 'cc-brinq', nome: 'Brinquedos', aceita_categoria: false, tabela: null },
]
const erro = (status, detail) => Object.assign(new Error(String(status)), { data: { detail } })

async function pagina({ role = 'admin', respostas = {} } = {}) {
  const calls = []
  const api = (url, opts = {}) => {
    calls.push({ url, method: opts.method || 'GET', body: opts.body })
    const r = respostas[`${opts.method || 'GET'} ${url}`]
    if (typeof r === 'function') return r(opts)
    if (r instanceof Error) return Promise.reject(r)
    if (r !== undefined) return Promise.resolve(r)
    if (url === '/api/produto-cadastros') return Promise.resolve({ tipos: TIPOS, cadastros: CADS, centros_custo: CENTROS })
    return Promise.resolve({})
  }
  const p = await factory(Vue.ref, Vue.computed, () => {}, () => ({ api }), () => ({ user: { role } }), [])
  return { p, calls }
}

;(async () => {
  // carrega a tabela; só admin mexe
  {
    const { p } = await pagina()
    assert.equal(p.cadastros.value.length, 5)
    assert.deepEqual(p.centros.value, CENTROS)
    assert.equal(p.isAdmin.value, true)
    const { p: q } = await pagina({ role: 'user' })
    assert.equal(q.isAdmin.value, false)
  }
  // + Novo tipo e renomear tipo
  {
    const { p, calls } = await pagina()
    p.abrirTipo()
    p.tipoNome.value = 'Cor'
    await p.criarTipo()
    assert.deepEqual(calls.find((c) => c.method === 'POST'), { url: '/api/produto-cadastros/tipos', method: 'POST', body: { nome: 'Cor' } })
    p.editarTipo(TIPOS[0])
    p.tipoEditNome.value = 'Categorias'
    await p.salvarTipo(TIPOS[0])
    assert.deepEqual(calls.find((c) => c.method === 'PATCH'), { url: '/api/produto-cadastros/tipos/t1', method: 'PATCH', body: { nome: 'Categorias' } })
  }
  // + Novo cadastro: tipo da lista + nome; repetido mostra a frase do servidor
  {
    const { p, calls } = await pagina({
      respostas: { 'POST /api/produto-cadastros': erro(409, { code: 'nome_repetido', mensagem: 'Já existe “.sp” em Tag de envio.' }) },
    })
    p.abrirCadastro()
    assert.equal(p.cadTipo.value, 't1', 'começa no primeiro tipo')
    p.cadTipo.value = 't2'
    p.cadNome.value = '.sp'
    await p.salvarCadastro()
    assert.deepEqual(calls.at(-1), { url: '/api/produto-cadastros', method: 'POST', body: { tipo_id: 't2', nome: '.sp' } })
    assert.equal(p.cadErro.value, 'Já existe “.sp” em Tag de envio.')
    assert.equal(p.cadAberto.value, true, 'a janela fica aberta com o erro')
  }
  // lápis numa tag em uso: pergunta e reenvia com confirmar
  {
    let vez = 0
    const { p, calls } = await pagina({
      respostas: {
        'PATCH /api/produto-cadastros/c2': () => (vez++ === 0
          ? Promise.reject(erro(409, { code: 'tag_em_uso', produtos: 639, precisa_confirmar: true, mensagem: '639 produtos terminam com “.sp”.' }))
          : Promise.resolve({})),
      },
    })
    p.abrirCadastro(CADS[1])
    p.cadNome.value = '.sp2'
    await p.salvarCadastro()
    assert.equal(p.cadAviso.value, '639 produtos terminam com “.sp”. Trocar mesmo assim?')
    await p.salvarCadastro(true)
    const patches = calls.filter((c) => c.method === 'PATCH')
    assert.deepEqual(patches.map((c) => c.body.confirmar), [false, true])
    assert.equal(p.cadAberto.value, false)
  }
  // lápis numa categoria em uso: a frase do servidor, nada de confirmar
  {
    const { p } = await pagina({
      respostas: { 'PATCH /api/produto-cadastros/c1': erro(409, { code: 'categoria_em_uso', produtos: 178, mensagem: '178 produtos usam a categoria “Celular”.' }) },
    })
    p.abrirCadastro(CADS[0])
    p.cadNome.value = 'Celulares'
    await p.salvarCadastro()
    assert.equal(p.cadAviso.value, null)
    assert.equal(p.cadErro.value, '178 produtos usam a categoria “Celular”.')
  }
  // lixeira: pergunta antes, com o uso; categoria em uso não exclui
  {
    const { p, calls } = await pagina({
      respostas: { 'GET /api/produto-cadastros/c1/uso': { regra: 'categoria', produtos: 178, bloqueia: true, mensagem: '178 produtos usam a categoria “Celular”.' } },
    })
    await p.pedirExclusao(CADS[0])
    assert.equal(p.excluindo.value.id, 'c1')
    assert.equal(p.excluirUso.value.bloqueia, true)
    assert.ok(!calls.some((c) => c.method === 'DELETE'), 'nada é excluído antes da confirmação')
    assert.match(tpl, /v-if="excluirUso && !excluirUso\.bloqueia"[\s\S]{0,200}data-acao="confirmar-exclusao"/, 'sem botão Excluir quando bloqueia')
  }
  {
    const { p, calls } = await pagina({
      respostas: { 'GET /api/produto-cadastros/c2/uso': { regra: 'tag_envio', produtos: 639, bloqueia: false, mensagem: '639 produtos terminam com “.sp”.' } },
    })
    await p.pedirExclusao(CADS[1])
    assert.equal(p.excluirUso.value.produtos, 639)
    await p.confirmarExclusao()
    assert.deepEqual(calls.filter((c) => c.method === 'DELETE').map((c) => c.url), ['/api/produto-cadastros/c2?confirmar=true'])
    assert.equal(p.excluindo.value, null)
  }
  // M-75 + M-89: Novo cadastro de Categoria de venda — a Tabela "Contas Mala" escolhe o tipo e o
  // centro de custo; vai para /categorias-venda; o limite de 5 mostra a frase do servidor
  {
    const limite = 'Este centro de custo já tem 5 categorias de venda.'
    const { p, calls } = await pagina({
      respostas: { 'POST /api/produto-cadastros/categorias-venda': erro(409, { code: 'categoria_venda_limite', mensagem: limite }) },
    })
    p.abrirCadastro()
    assert.equal(p.cadTabela.value, 'Produtos 2', 'começa na tabela Produtos 2')
    // Categoria de venda sem tabela de Contas: nada vai ao servidor
    p.cadTipo.value = 'tv'
    p.cadNome.value = 'Mochilas'
    await p.salvarCadastro()
    assert.equal(p.cadErro.value, 'Escolha a tabela.')
    assert.ok(!calls.some((c) => c.method === 'POST'), 'sem tabela nada vai ao servidor')
    p.cadTabela.value = 'r-mala'
    p.trocarTabela()
    assert.equal(p.cadTipo.value, 'tv', 'Contas … → Categoria de venda')
    assert.equal(p.cadEhVenda.value, true)
    assert.equal(p.cadCentro.value, 'r-mala', 'a tabela define o centro de custo')
    await p.salvarCadastro()
    assert.deepEqual(calls.at(-1), {
      url: '/api/produto-cadastros/categorias-venda',
      method: 'POST',
      body: { centro_custo_id: 'r-mala', nome: 'Mochilas' },
    })
    assert.equal(p.cadErro.value, limite)
    assert.equal(p.cadAberto.value, true)
    // Voltar para Produtos 2 volta o Tipo para Categoria
    p.cadTabela.value = 'Produtos 2'
    p.trocarTabela()
    assert.equal(p.cadTipo.value, 't1')
    assert.equal(p.cadCentro.value, '')
  }
  // M-89: o menu Tabela — Produtos 2 e as tabelas de Contas; o centro novo (Brinquedos, sem
  // raiz) não tem tabela na aba Contas e não aparece
  {
    const { p } = await pagina()
    p.abrirCadastro()
    assert.deepEqual(
      p.tabelasDoMenu.value.map((t) => [t.id, t.nome]),
      [['Produtos 2', 'Produtos 2'], ['r-cel', 'Contas Celular'], ['r-mala', 'Contas Mala']],
    )
  }
  // M-75: lápis numa Categoria de venda — só o nome, pela rota dela; o centro só aparece;
  // a recusa do nome fixo aparece como veio
  {
    const fixo = '“Apple” não pode trocar de nome: outras partes do sistema procuram esta categoria pelo nome “Apple”.'
    const { p, calls } = await pagina({
      respostas: { 'PATCH /api/produto-cadastros/categorias-venda/s1': erro(409, { code: 'categoria_venda_nome_fixo', mensagem: fixo }) },
    })
    p.abrirCadastro(CADS[2])
    assert.equal(p.cadEhVenda.value, true)
    assert.equal(p.cadCentro.value, '', 'no lápis o centro não é escolhido (só aparece)')
    p.cadNome.value = 'iPhone'
    await p.salvarCadastro()
    assert.deepEqual(calls.at(-1), { url: '/api/produto-cadastros/categorias-venda/s1', method: 'PATCH', body: { nome: 'iPhone' } })
    assert.equal(p.cadErro.value, fixo)
  }
  // M-75: no lápis de uma Categoria comum, o menu de tipo não oferece Categoria de venda.
  // M-89: o Tipo depende da Tabela (Produtos 2 → Categoria, Tag de envio; Contas … → Categoria
  // de venda) e Tipo de conta não entra em menu nenhum
  {
    const { p } = await pagina()
    p.abrirCadastro(CADS[0])
    assert.deepEqual(p.tiposDoMenu.value.map((t) => t.id), ['t1', 't2'])
    p.abrirCadastro(CADS[2])
    assert.deepEqual(p.tiposDoMenu.value.map((t) => t.id), ['tv'])
    p.abrirCadastro()
    assert.deepEqual(p.tiposDoMenu.value.map((t) => t.id), ['t1', 't2'])
    p.cadTabela.value = 'r-cel'
    p.trocarTabela()
    assert.deepEqual(p.tiposDoMenu.value.map((t) => t.id), ['tv'])
  }
  // M-89: Tipo de conta (Lojas) é só para ver — sem lápis nem lixeira
  {
    const { p, calls } = await pagina()
    const conta = CADS[4]
    assert.equal(p.podeMexer(conta), false)
    assert.equal(p.podeMexer(CADS[0]), true)
    assert.equal(p.podeMexer(CADS[2]), true)
    p.abrirCadastro(conta)
    assert.equal(p.cadAberto.value, false, 'o lápis não abre')
    await p.pedirExclusao(conta)
    assert.equal(p.excluindo.value, null, 'a lixeira não abre')
    assert.ok(!calls.some((c) => c.url.includes('/uso')), 'nada vai ao servidor')
  }
  // M-75: lixeira de Categoria de venda — uso pela rota dela; bloqueada (ex.: em uso sem destino
  // possível) não tem Excluir; livre, exclui sem confirmar=true
  {
    const doMeio = '“Apple” (Celular) está em uso: 1 produto da Tabela de Preços, e não há outra categoria de venda ativa em Celular para recebê-los.'
    const { p, calls } = await pagina({
      respostas: {
        'GET /api/produto-cadastros/categorias-venda/s1/uso': { regra: 'categoria_venda', produtos: 0, bloqueia: true, mensagem: doMeio, precisa_destino: false, destinos: [], avisos: [] },
        'GET /api/produto-cadastros/categorias-venda/s2/uso': { regra: 'categoria_venda', produtos: 0, bloqueia: false, mensagem: null, precisa_destino: false, destinos: [], avisos: [] },
      },
    })
    await p.pedirExclusao(CADS[2])
    assert.equal(p.excluirUso.value.bloqueia, true)
    assert.equal(p.excluirUso.value.mensagem, doMeio)
    assert.equal(p.precisaDestino.value, false, 'bloqueada não pede destino')
    await p.pedirExclusao(CADS[3])
    await p.confirmarExclusao()
    assert.deepEqual(
      calls.filter((c) => c.method === 'DELETE').map((c) => c.url),
      ['/api/produto-cadastros/categorias-venda/s2'],
    )
  }
  // M-90: Categoria de venda sem uso na tabela dela — confirmação simples com os avisos do
  // servidor (nome reservado; produtos da tela Produtos sem segmento); DELETE sem destino
  {
    const avisos = [
      '“Apple” é um nome reservado: depois de excluída, não dá para criar de novo pela tela.',
      '264 produtos da tela Produtos ficam sem segmento.',
    ]
    const { p, calls } = await pagina({
      respostas: {
        'GET /api/produto-cadastros/categorias-venda/s1/uso': {
          regra: 'categoria_venda', produtos: 0, bloqueia: false, mensagem: null, contas: 0,
          produtos_tela_produtos: 264, precisa_destino: false, destinos: [], centro_custo: 'Celular', avisos,
        },
      },
    })
    await p.pedirExclusao(CADS[2])
    assert.equal(p.precisaDestino.value, false)
    assert.deepEqual(p.excluirUso.value.avisos, avisos)
    await p.confirmarExclusao()
    assert.deepEqual(
      calls.filter((c) => c.method === 'DELETE').map((c) => c.url),
      ['/api/produto-cadastros/categorias-venda/s1'],
    )
    assert.match(tpl, /v-for="a in excluirUso\?\.avisos \?\? \[\]"[\s\S]{0,120}data-aviso="aviso"/)
  }
  // M-90: Categoria de venda em uso na tabela dela — pede o destino, mostra o que vai acontecer
  // e manda ?destino_id=; sem destino, nada vai ao servidor
  {
    const emUso = '“Apple” (Celular) está em uso: 12 produtos da Tabela de Preços e 40 contas. Escolha para qual categoria de venda eles vão antes de excluir.'
    const { p, calls } = await pagina({
      respostas: {
        'GET /api/produto-cadastros/categorias-venda/s1/uso': {
          regra: 'categoria_venda', produtos: 12, bloqueia: false, mensagem: emUso, contas: 40,
          produtos_tela_produtos: 264, precisa_destino: true, centro_custo: 'Celular', avisos: [],
          destinos: [{ id: 'd1', nome: 'Acessórios' }, { id: 'd2', nome: 'Robusto' }],
        },
      },
    })
    await p.pedirExclusao(CADS[2])
    assert.equal(p.precisaDestino.value, true)
    assert.equal(p.excluirUso.value.mensagem, emUso)
    assert.equal(p.textoTransferencia.value, null, 'sem destino, sem texto')
    await p.confirmarExclusao()
    assert.ok(!calls.some((c) => c.method === 'DELETE'), 'sem destino nada é excluído')
    // escolha única
    p.escolherDestino('d1')
    p.escolherDestino('d2')
    assert.equal(p.excluirDestino.value, 'd2')
    p.escolherDestino('d2')
    assert.equal(p.excluirDestino.value, null, 'clicar na marcada desmarca')
    p.escolherDestino('d1')
    assert.equal(
      p.textoTransferencia.value,
      'Os 12 produtos e as 40 contas passam para “Acessórios” (Celular) e a categoria “Apple” é excluída. Não dá para desfazer.',
    )
    assert.equal(p.textoPrecos.value, 'Os preços desses produtos passam a usar a margem e o frete de “Acessórios”.')
    assert.equal(p.textoTelaProdutos.value, '264 produtos da tela Produtos vão junto para “Acessórios”.')
    await p.confirmarExclusao()
    assert.deepEqual(
      calls.filter((c) => c.method === 'DELETE').map((c) => c.url),
      ['/api/produto-cadastros/categorias-venda/s1?destino_id=d1'],
    )
    assert.equal(p.excluindo.value, null, 'a janela fecha')
    assert.equal(p.excluirDestino.value, null)
    // o desenho: quadro vermelho, "Transferir para" com caixinhas e o botão apagado até escolher
    assert.match(tpl, /v-else-if="precisaDestino"[\s\S]{0,250}data-aviso="em-uso"/)
    assert.match(tpl, /data-campo="transferir-para"[\s\S]{0,150}Transferir para[\s\S]{0,700}type="checkbox" :checked="excluirDestino === d\.id" @change="escolherDestino\(d\.id\)"/)
    assert.match(tpl, /:disabled="excluirCarregando \|\| \(precisaDestino && !excluirDestino\)"/)
    assert.match(tpl, /\{\{ precisaDestino \? 'Transferir e excluir' : 'Excluir' \}\}/)
  }
  // M-90: só uma conta, sem produto da Tabela de Preços — singular e sem o texto dos preços
  {
    const { p } = await pagina({
      respostas: {
        'GET /api/produto-cadastros/categorias-venda/s1/uso': {
          regra: 'categoria_venda', produtos: 0, bloqueia: false, mensagem: 'x', contas: 1,
          produtos_tela_produtos: 0, precisa_destino: true, centro_custo: 'Celular', avisos: [],
          destinos: [{ id: 'd1', nome: 'Acessórios' }],
        },
      },
    })
    await p.pedirExclusao(CADS[2])
    p.escolherDestino('d1')
    assert.equal(
      p.textoTransferencia.value,
      'A conta passa para “Acessórios” (Celular) e a categoria “Apple” é excluída. Não dá para desfazer.',
    )
    assert.equal(p.textoPrecos.value, null)
    assert.equal(p.textoTelaProdutos.value, null)
    assert.equal(p.doMeio.value, false, 'a última: sem a lista da M-91')
    assert.deepEqual(p.textosDoMeio.value, [])
  }
  // M-91: Categoria de venda do meio, em uso — "Diversos" (Celular) com destino "Regular": a lista
  // abaixo de "Transferir para" (transferência com os preços, colunas que andam, margem/frete
  // apagados, "Não dá para desfazer."), sem os textos da M-90; DELETE com ?destino_id=
  {
    const { p, calls } = await pagina({
      respostas: {
        'GET /api/produto-cadastros/categorias-venda/s1/uso': {
          regra: 'categoria_venda', produtos: 11, bloqueia: false, mensagem: 'x', contas: 0,
          produtos_tela_produtos: 0, precisa_destino: true, centro_custo: 'Celular', avisos: [],
          destinos: [{ id: 'd1', nome: 'Acessórios' }, { id: 'd3', nome: 'Regular' }, { id: 'd4', nome: 'Robusto' }],
          sobem: ['Regular', 'Robusto'], contas_centro: 40,
        },
      },
    })
    const diversos = { ...CADS[2], nome: 'Diversos' }
    await p.pedirExclusao(diversos)
    assert.equal(p.doMeio.value, true)
    // Antes de escolher o destino: só as colunas e o apagar.
    assert.deepEqual(p.textosDoMeio.value, [
      'Na aba Contas, as colunas “Regular” e “Robusto” andam uma posição, levando a margem e o frete de cada conta.',
      'A margem e o frete de “Diversos” nas 40 contas são apagados.',
      'Não dá para desfazer.',
    ])
    p.escolherDestino('d3')
    assert.deepEqual(p.textosDoMeio.value, [
      'Os 11 produtos passam para “Regular” e os preços deles passam a usar a margem e o frete de “Regular”.',
      'Na aba Contas, as colunas “Regular” e “Robusto” andam uma posição, levando a margem e o frete de cada conta.',
      'A margem e o frete de “Diversos” nas 40 contas são apagados.',
      'Não dá para desfazer.',
    ])
    await p.confirmarExclusao()
    assert.deepEqual(
      calls.filter((c) => c.method === 'DELETE').map((c) => c.url),
      ['/api/produto-cadastros/categorias-venda/s1?destino_id=d3'],
    )
    // o desenho: a lista fica abaixo de "Transferir para" e os textos da M-90 somem no "do meio"
    assert.match(tpl, /v-if="textoTransferencia && !doMeio"/)
    assert.ok(tpl.indexOf('data-campo="transferir-para"') < tpl.indexOf('data-aviso="do-meio"'))
    assert.match(tpl, /v-if="textosDoMeio\.length"[\s\S]{0,120}data-aviso="do-meio"[\s\S]{0,80}v-for="t in textosDoMeio"/)
  }
  // M-91: do meio, sem uso — só uma coluna anda, uma conta; botão "Excluir" e DELETE simples
  {
    const { p, calls } = await pagina({
      respostas: {
        'GET /api/produto-cadastros/categorias-venda/s1/uso': {
          regra: 'categoria_venda', produtos: 0, bloqueia: false, mensagem: null, contas: 0,
          produtos_tela_produtos: 0, precisa_destino: false, destinos: [], centro_custo: 'Eletro',
          avisos: [], sobem: ['3'], contas_centro: 1,
        },
      },
    })
    await p.pedirExclusao({ ...CADS[2], nome: '2' })
    assert.equal(p.precisaDestino.value, false)
    assert.deepEqual(p.textosDoMeio.value, [
      'Na aba Contas, a coluna “3” anda uma posição, levando a margem e o frete de cada conta.',
      'A margem e o frete de “2” na conta são apagados.',
      'Não dá para desfazer.',
    ])
    await p.confirmarExclusao()
    assert.deepEqual(
      calls.filter((c) => c.method === 'DELETE').map((c) => c.url),
      ['/api/produto-cadastros/categorias-venda/s1'],
    )
  }
  console.log('cadastro-produtos-sfc: ok')
})().catch((e) => { console.error(e); process.exit(1) })
