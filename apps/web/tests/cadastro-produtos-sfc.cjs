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
  assert.deepEqual(ths.map((m) => m[1]), ['Tipo', 'Nome', 'Ações'])
  for (const m of ths) assert.match(m[0], /text-\[11px\] font-semibold leading-\[14px\]/, 'cabeçalho 11px semibold')
  const linha = tpl.slice(tpl.indexOf('<tr v-for="c in cadastros"'), tpl.indexOf('</tr>', tpl.indexOf('<tr v-for="c in cadastros"')))
  assert.match(linha, /class="h-8 /, 'linhas da mesma altura')
  assert.equal((linha.match(/class="border border-border [^"]*text-xs/g) || []).length, 2, 'Tipo e Nome em text-xs com borda')
  assert.match(linha, /<div v-if="isAdmin"[\s\S]*<Pencil[\s\S]*<Trash2/, 'lápis e lixeira, só admin')
  for (const m of tpl.matchAll(/colspan="(\d+)"/g)) assert.equal(m[1], '3')
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
  }`,
)

const TIPOS = [
  { id: 't1', nome: 'Categoria', chave: 'categoria' },
  { id: 't2', nome: 'Tag de envio', chave: 'tag_envio' },
]
const CADS = [
  { id: 'c1', tipo_id: 't1', tipo_nome: 'Categoria', tipo_chave: 'categoria', nome: 'Celular' },
  { id: 'c2', tipo_id: 't2', tipo_nome: 'Tag de envio', tipo_chave: 'tag_envio', nome: '.sp' },
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
    if (url === '/api/produto-cadastros') return Promise.resolve({ tipos: TIPOS, cadastros: CADS })
    return Promise.resolve({})
  }
  const p = await factory(Vue.ref, Vue.computed, () => {}, () => ({ api }), () => ({ user: { role } }), [])
  return { p, calls }
}

;(async () => {
  // carrega a tabela; só admin mexe
  {
    const { p } = await pagina()
    assert.equal(p.cadastros.value.length, 2)
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
  console.log('cadastro-produtos-sfc: ok')
})().catch((e) => { console.error(e); process.exit(1) })
