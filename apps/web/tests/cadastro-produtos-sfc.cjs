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
  assert.deepEqual(labels.slice(-2), ['Integrações', 'Produtos'], 'Produtos logo depois de Integrações')
  const aba = exp.TABS_CADASTROS.at(-1)
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
  assert.deepEqual(ths.map((m) => m[1]), ['Tipo', 'Nome', 'Centro de custo', 'Ações'])
  for (const m of ths) assert.match(m[0], /text-\[11px\] font-semibold leading-\[14px\]/, 'cabeçalho 11px semibold')
  const linha = tpl.slice(tpl.indexOf('<tr v-for="c in cadastros"'), tpl.indexOf('</tr>', tpl.indexOf('<tr v-for="c in cadastros"')))
  assert.match(linha, /class="h-8 /, 'linhas da mesma altura')
  assert.equal((linha.match(/class="border border-border [^"]*text-xs/g) || []).length, 3, 'Tipo, Nome e Centro de custo em text-xs com borda')
  assert.match(linha, /<div v-if="isAdmin"[\s\S]*<Pencil[\s\S]*<Trash2/, 'lápis e lixeira, só admin')
  for (const m of tpl.matchAll(/colspan="(\d+)"/g)) assert.equal(m[1], '4')
  // M-75: Centro de custo (ou "—" cinza) e a marca "inativa" com o nome em cinza
  assert.match(linha, /\{\{ c\.centro_custo \|\| '—' \}\}/)
  assert.match(linha, /:class="\{ 'text-muted-foreground': !c\.centro_custo \}"/)
  assert.match(linha, /:class="\{ 'text-muted-foreground': c\.ativa === false \}"[\s\S]*v-if="c\.ativa === false"[\s\S]*>inativa<\/span>/)
  const ordemCols = ['{{ c.tipo_nome }}', '{{ c.nome }}', "c.centro_custo || '—'", '<Pencil'].map((t) => linha.indexOf(t))
  assert.ok(ordemCols.every((i, k) => i > 0 && (k === 0 || i > ordemCols[k - 1])), 'Tipo | Nome | Centro de custo | Ações')
  // Novo cadastro de Categoria de venda pede o centro de custo; no lápis ele e o tipo ficam travados
  assert.match(tpl, /<label v-if="cadEhVenda"[\s\S]{0,120}Centro de custo[\s\S]{0,200}:disabled="!!cadEditando"[\s\S]{0,300}data-campo="cadastro-centro"/)
  assert.match(tpl, /:disabled="ehVenda\(cadEditando\)"/)
  assert.match(tpl, /v-for="t in tiposDoMenu"/)
  // O tipo Categoria de venda não é renomeado na janela de tipos
  assert.match(tpl, /<button v-if="!ehVenda\(t\)"[^>]*data-acao="renomear-tipo"/)
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
  }`,
)

const TIPOS = [
  { id: 't1', nome: 'Categoria', chave: 'categoria' },
  { id: 'tv', nome: 'Categoria de venda', chave: 'categoria_venda' },
  { id: 't2', nome: 'Tag de envio', chave: 'tag_envio' },
]
const CADS = [
  { id: 'c1', tipo_id: 't1', tipo_nome: 'Categoria', tipo_chave: 'categoria', nome: 'Celular' },
  { id: 'c2', tipo_id: 't2', tipo_nome: 'Tag de envio', tipo_chave: 'tag_envio', nome: '.sp' },
  { id: 's1', tipo_id: 'tv', tipo_nome: 'Categoria de venda', tipo_chave: 'categoria_venda', nome: 'Apple', centro_custo: 'Celular', ativa: true },
  { id: 's2', tipo_id: 'tv', tipo_nome: 'Categoria de venda', tipo_chave: 'categoria_venda', nome: 'Queima de estoque', centro_custo: 'Mala', ativa: false },
]
const CENTROS = [
  { id: 'r-cel', nome: 'Celular' },
  { id: 'r-mala', nome: 'Mala' },
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
    assert.equal(p.cadastros.value.length, 4)
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
  // M-75: Novo cadastro de Categoria de venda — pede o centro de custo; vai para /categorias-venda;
  // o limite de 5 mostra a frase do servidor
  {
    const limite = 'Este centro de custo já tem 5 categorias de venda.'
    const { p, calls } = await pagina({
      respostas: { 'POST /api/produto-cadastros/categorias-venda': erro(409, { code: 'categoria_venda_limite', mensagem: limite }) },
    })
    p.abrirCadastro()
    p.cadTipo.value = 'tv'
    assert.equal(p.cadEhVenda.value, true)
    p.cadNome.value = 'Mochilas'
    await p.salvarCadastro()
    assert.equal(p.cadErro.value, 'Escolha o centro de custo.')
    assert.ok(!calls.some((c) => c.method === 'POST'), 'sem centro de custo nada vai ao servidor')
    p.cadCentro.value = 'r-mala'
    await p.salvarCadastro()
    assert.deepEqual(calls.at(-1), {
      url: '/api/produto-cadastros/categorias-venda',
      method: 'POST',
      body: { centro_custo_id: 'r-mala', nome: 'Mochilas' },
    })
    assert.equal(p.cadErro.value, limite)
    assert.equal(p.cadAberto.value, true)
  }
  // M-75: lápis numa Categoria de venda — só o nome, pela rota dela; o centro vem preenchido;
  // a recusa do nome fixo aparece como veio
  {
    const fixo = '“Apple” não pode trocar de nome: outras partes do sistema procuram esta categoria pelo nome “Apple”.'
    const { p, calls } = await pagina({
      respostas: { 'PATCH /api/produto-cadastros/categorias-venda/s1': erro(409, { code: 'categoria_venda_nome_fixo', mensagem: fixo }) },
    })
    p.abrirCadastro(CADS[2])
    assert.equal(p.cadEhVenda.value, true)
    assert.equal(p.cadCentro.value, 'r-cel')
    p.cadNome.value = 'iPhone'
    await p.salvarCadastro()
    assert.deepEqual(calls.at(-1), { url: '/api/produto-cadastros/categorias-venda/s1', method: 'PATCH', body: { nome: 'iPhone' } })
    assert.equal(p.cadErro.value, fixo)
  }
  // M-75: no lápis de uma Categoria comum, o menu de tipo não oferece Categoria de venda
  {
    const { p } = await pagina()
    p.abrirCadastro(CADS[0])
    assert.deepEqual(p.tiposDoMenu.value.map((t) => t.id), ['t1', 't2'])
    p.abrirCadastro()
    assert.deepEqual(p.tiposDoMenu.value.map((t) => t.id), ['t1', 'tv', 't2'])
  }
  // M-75: lixeira de Categoria de venda — uso pela rota dela; em uso não tem Excluir;
  // livre, exclui sem confirmar=true
  {
    const emUso = '“Apple” (Celular) está em uso: 12 produtos da Tabela de Preços e 40 contas. Troque a categoria de venda desses produtos e a coluna dessas contas antes de excluir.'
    const { p, calls } = await pagina({
      respostas: {
        'GET /api/produto-cadastros/categorias-venda/s1/uso': { regra: 'categoria_venda', produtos: 12, bloqueia: true, mensagem: emUso },
        'GET /api/produto-cadastros/categorias-venda/s2/uso': { regra: 'categoria_venda', produtos: 0, bloqueia: false, mensagem: null },
      },
    })
    await p.pedirExclusao(CADS[2])
    assert.equal(p.excluirUso.value.bloqueia, true)
    assert.equal(p.excluirUso.value.mensagem, emUso)
    await p.pedirExclusao(CADS[3])
    await p.confirmarExclusao()
    assert.deepEqual(
      calls.filter((c) => c.method === 'DELETE').map((c) => c.url),
      ['/api/produto-cadastros/categorias-venda/s2'],
    )
  }
  console.log('cadastro-produtos-sfc: ok')
})().catch((e) => { console.error(e); process.exit(1) })
