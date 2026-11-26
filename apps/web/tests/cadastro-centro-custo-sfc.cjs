// Run from apps/web: node tests/cadastro-centro-custo-sfc.cjs
// Cadastros › Centro de custo (M-76, Harry 25/11/2026): aba nova logo depois de Produtos, com a
// tela de Cadastros › Produtos sem a coluna Tipo e sem "Novo tipo". Trava:
//  - a aba (lib/navGroups) logo depois de Produtos, com a permissão de Produtos
//    (`cadastro_produtos`): quem vê Produtos vê o Centro de custo;
//  - o desenho: título "Centro de custo" sem descrição; recarregar e + Novo cadastro (azul) à
//    direita, sem + Novo tipo; colunas Nome | Ações (lápis e lixeira); as letras de
//    Cadastros › Produtos (cabeçalho 11px semibold, células text-xs, bordas, mesma altura);
//  - botões de mexer só para administrador;
//  - os pedidos ao servidor: criar e renomear (só o nome); o lápis de Celular/Eletro/Mala avisa
//    que não mudam de nome e não tem Salvar; a lixeira sempre pergunta antes (GET /uso) e, em
//    uso, mostra quantos usam e não tem Excluir.
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
  const i = labels.indexOf('Centro de custo')
  assert.equal(labels[i - 1], 'Produtos', 'Centro de custo logo depois de Produtos')
  const aba = exp.TABS_CADASTROS[i]
  assert.equal(aba.to, '/cadastro-centro-custo')
  assert.equal(aba.resource, 'cadastro_produtos', 'mesma permissão de Cadastros › Produtos')
  const ver = { role: 'user', permissions: { cadastro_produtos: { view: true } } }
  const nao = { role: 'user', permissions: { segmentos: { view: true } } }
  assert.ok(exp.allowedTabs(exp.TABS_CADASTROS, ver).some((t) => t.label === 'Centro de custo'))
  assert.ok(!exp.allowedTabs(exp.TABS_CADASTROS, nao).some((t) => t.label === 'Centro de custo'))
  assert.ok(exp.allowedTabs(exp.TABS_CADASTROS, { role: 'admin' }).some((t) => t.label === 'Centro de custo'))
  const nomes = fs.readFileSync(path.join(__dirname, '../../api/app/historico/nomes.py'), 'utf8')
  assert.ok(nomes.includes('    "/cadastro-centro-custo": "Cadastros › Centro de custo",'), 'Histórico: nome da tela')
  assert.ok(nomes.includes('    "/api/centros-custo": "Cadastros › Centro de custo",'), 'Histórico: nome da API')
}

// ---------------------------------------------------------------- SFC
const filename = path.join(__dirname, '../pages/cadastro-centro-custo.vue')
const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
assert.deepEqual(errors, [])
const compiled = compileTemplate({ source: descriptor.template.content, filename, id: 'cad-cc' })
assert.deepEqual(compiled.errors, [])
new Function('exports', 'require', transpile(compiled.code))({}, require)
const tpl = descriptor.template.content
const script = descriptor.scriptSetup.content
{
  assert.match(script, /permission: \{ resource: 'cadastro_produtos', action: 'view' \}/)
  assert.match(tpl, /<RouteTabs :tabs="TABS_CADASTROS" \/>/)
  assert.match(tpl, /<PageHeader title="Centro de custo">/, 'título, sem descrição')
  assert.doesNotMatch(tpl, /<PageHeader[^>]*description/)
  const acoes = tpl.slice(tpl.indexOf('<template #actions>'), tpl.indexOf('</template>', tpl.indexOf('<template #actions>')))
  assert.ok(acoes.indexOf('recarregar') > 0 && acoes.indexOf('recarregar') < acoes.indexOf('Novo cadastro'), 'recarregar, + Novo cadastro')
  assert.doesNotMatch(tpl, /Novo tipo/, 'sem + Novo tipo')
  assert.match(acoes, /<Button v-if="isAdmin" size="sm" data-acao="novo-cadastro"/, 'Novo cadastro azul (variante padrão) e só admin')
  const ths = [...tpl.matchAll(/<th\b[^>]*>([^<]*)<\/th>/g)]
  assert.deepEqual(ths.map((m) => m[1]), ['Nome', 'Ações'], 'sem a coluna Tipo')
  for (const m of ths) assert.match(m[0], /text-\[11px\] font-semibold leading-\[14px\]/, 'cabeçalho 11px semibold')
  const linha = tpl.slice(tpl.indexOf('<tr v-for="c in centros"'), tpl.indexOf('</tr>', tpl.indexOf('<tr v-for="c in centros"')))
  assert.match(linha, /class="h-8 /, 'linhas da mesma altura')
  assert.equal((linha.match(/class="border border-border [^"]*text-xs/g) || []).length, 1, 'Nome em text-xs com borda')
  assert.match(linha, /<div v-if="isAdmin"[\s\S]*<Pencil[\s\S]*<Trash2/, 'lápis e lixeira, só admin')
  for (const m of tpl.matchAll(/colspan="(\d+)"/g)) assert.equal(m[1], '2')
  // As mesmas letras e o mesmo contorno de Cadastros › Produtos.
  const produtos = fs.readFileSync(path.join(__dirname, '../pages/cadastro-produtos.vue'), 'utf8')
  for (const trecho of [
    '<div class="border rounded-lg overflow-auto max-h-[calc(100vh-220px)] max-w-3xl">',
    '<thead class="sticky top-0 bg-muted z-10">',
    'class="p-1 text-muted-foreground hover:text-foreground hover:bg-muted rounded"',
    'class="p-1 text-destructive hover:bg-destructive/10 rounded"',
  ]) {
    assert.ok(tpl.includes(trecho) && produtos.includes(trecho), trecho)
  }
  // Lápis de um fixo: o aviso e o campo travado, sem Salvar.
  assert.match(tpl, /v-if="cadEditando\?\.fixo"[\s\S]{0,300}data-aviso="fixo"[\s\S]{0,40}\{\{ MSG_FIXO \}\}/)
  assert.match(tpl, /:disabled="cadEditando\?\.fixo"/)
  assert.match(tpl, /<Button v-if="!cadEditando\?\.fixo"[^>]*data-acao="salvar-cadastro"/)
  assert.match(tpl, /maxlength="60"/)
  // Lixeira: em uso não tem Excluir.
  assert.match(tpl, /v-if="excluirUso && !excluirUso\.bloqueia"[\s\S]{0,200}data-acao="confirmar-exclusao"/)
}

// ---------------------------------------------------------------- script setup com api falso
const pageScript = script.replace(/^import[\s\S]*?from\s+'[^']+'\s*$/gm, '')
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const factory = new AsyncFunction(
  'ref', 'computed', 'definePageMeta', 'useApi', 'useAuthStore', 'TABS_CADASTROS',
  transpile(pageScript, ts.ModuleKind.ESNext) + `\nreturn {
    centros, error, load, isAdmin, MSG_FIXO,
    cadAberto, cadEditando, cadNome, cadErro, abrirCadastro, salvarCadastro,
    excluindo, excluirUso, excluirErro, pedirExclusao, confirmarExclusao,
  }`,
)

const CENTROS = [
  { id: 'cc-cel', nome: 'Celular', fixo: true, segment_id: 'r-cel' },
  { id: 'cc-ele', nome: 'Eletro', fixo: true, segment_id: 'r-ele' },
  { id: 'cc-mala', nome: 'Mala', fixo: true, segment_id: 'r-mala' },
  { id: 'cc-brinq', nome: 'Brinquedos', fixo: false, segment_id: null },
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
    if (url === '/api/centros-custo') return Promise.resolve(CENTROS)
    return Promise.resolve({})
  }
  const p = await factory(Vue.ref, Vue.computed, () => {}, () => ({ api }), () => ({ user: { role } }), [])
  return { p, calls }
}

;(async () => {
  // carrega a tabela; só admin mexe
  {
    const { p } = await pagina()
    assert.deepEqual(p.centros.value.map((c) => c.nome), ['Celular', 'Eletro', 'Mala', 'Brinquedos'])
    assert.equal(p.isAdmin.value, true)
    const { p: q } = await pagina({ role: 'user' })
    assert.equal(q.isAdmin.value, false)
  }
  // + Novo cadastro: só o nome; repetido mostra a frase do servidor
  {
    const { p, calls } = await pagina({
      respostas: { 'POST /api/centros-custo': erro(409, { code: 'nome_repetido', mensagem: 'Já existe o centro de custo “Mala”.' }) },
    })
    p.abrirCadastro()
    await p.salvarCadastro()
    assert.equal(p.cadErro.value, 'Digite o nome.')
    assert.ok(!calls.some((c) => c.method === 'POST'))
    p.cadNome.value = 'mala'
    await p.salvarCadastro()
    assert.deepEqual(calls.at(-1), { url: '/api/centros-custo', method: 'POST', body: { nome: 'mala' } })
    assert.equal(p.cadErro.value, 'Já existe o centro de custo “Mala”.')
    assert.equal(p.cadAberto.value, true, 'a janela fica aberta com o erro')
  }
  {
    const { p, calls } = await pagina()
    p.abrirCadastro()
    p.cadNome.value = 'Brinquedos 2'
    await p.salvarCadastro()
    assert.deepEqual(calls.filter((c) => c.method === 'POST'), [{ url: '/api/centros-custo', method: 'POST', body: { nome: 'Brinquedos 2' } }])
    assert.equal(p.cadAberto.value, false)
    assert.equal(calls.at(-1).url, '/api/centros-custo', 'recarrega a lista')
  }
  // lápis: renomeia um centro novo; o de um fixo mostra a frase (a mesma do servidor)
  {
    const { p, calls } = await pagina()
    p.abrirCadastro(CENTROS[3])
    assert.equal(p.cadNome.value, 'Brinquedos')
    p.cadNome.value = 'Jogos'
    await p.salvarCadastro()
    assert.deepEqual(calls.find((c) => c.method === 'PATCH'), { url: '/api/centros-custo/cc-brinq', method: 'PATCH', body: { nome: 'Jogos' } })
    p.abrirCadastro(CENTROS[2])
    assert.equal(p.cadEditando.value.fixo, true)
    assert.equal(p.MSG_FIXO, 'Celular, Eletro e Mala não mudam de nome: o sistema procura esses nomes em outras telas.')
    const api = fs.readFileSync(path.join(__dirname, '../../api/app/services/centros_custo.py'), 'utf8')
    assert.ok(api.replace(/"\s*\n\s*"/g, '').includes(p.MSG_FIXO), 'a mesma frase do servidor')
  }
  // lixeira: pergunta antes; Mala (fixo) mostra quantos usam e não exclui
  {
    const msg = '“Mala” não pode ser excluído: Celular, Eletro e Mala são fixos do sistema. Hoje usam: 4 categorias de venda, 33 contas, 24 lojas e 39 produtos da Tabela de Preços.'
    const { p, calls } = await pagina({
      respostas: { 'GET /api/centros-custo/cc-mala/uso': { categorias: 4, contas: 33, lojas: 24, produtos: 39, bloqueia: true, mensagem: msg } },
    })
    await p.pedirExclusao(CENTROS[2])
    assert.equal(p.excluindo.value.id, 'cc-mala')
    assert.equal(p.excluirUso.value.bloqueia, true)
    assert.equal(p.excluirUso.value.mensagem, msg)
    assert.ok(!calls.some((c) => c.method === 'DELETE'), 'nada é excluído')
  }
  // lixeira de um centro sem uso: confirma e exclui
  {
    const { p, calls } = await pagina({
      respostas: { 'GET /api/centros-custo/cc-brinq/uso': { categorias: 0, contas: 0, lojas: 0, produtos: 0, bloqueia: false, mensagem: null } },
    })
    await p.pedirExclusao(CENTROS[3])
    assert.ok(!calls.some((c) => c.method === 'DELETE'), 'nada é excluído antes da confirmação')
    await p.confirmarExclusao()
    assert.deepEqual(calls.filter((c) => c.method === 'DELETE').map((c) => c.url), ['/api/centros-custo/cc-brinq'])
    assert.equal(p.excluindo.value, null)
  }
  // a recusa do servidor na exclusão aparece como veio
  {
    const { p } = await pagina({
      respostas: {
        'GET /api/centros-custo/cc-brinq/uso': { categorias: 0, contas: 0, lojas: 0, produtos: 0, bloqueia: false, mensagem: null },
        'DELETE /api/centros-custo/cc-brinq': erro(409, { code: 'centro_em_uso', mensagem: '“Brinquedos” está em uso: 1 conta.' }),
      },
    })
    await p.pedirExclusao(CENTROS[3])
    await p.confirmarExclusao()
    assert.equal(p.excluirErro.value, '“Brinquedos” está em uso: 1 conta.')
    assert.equal(p.excluindo.value.id, 'cc-brinq', 'a janela fica aberta')
  }
  console.log('cadastro-centro-custo-sfc: ok')
})().catch((e) => { console.error(e); process.exit(1) })
