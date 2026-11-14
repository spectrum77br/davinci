// Run from apps/web: node tests/pricing-sem-concorrencia.cjs
// Organização do DaVinci (13/11/2026), item 4: a aba Concorrência sai da
// Tabela de Preços. As abas ficam Tabela de Preços · Contas · Produtos, e o
// link antigo /pricing/concorrencia abre a Tabela de Preços (mantendo ?dept=).
// Executa o middleware real da página (definePageMeta) e as abas reais (TABS).
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

function pegar(nome) {
  const node = source.statements.find((s) => ts.isVariableStatement(s)
    && s.declarationList.declarations.some((d) => d.name.getText(source) === nome))
  assert.ok(node, `${nome} exists`)
  return node.getText(source)
}
const meta = source.statements.find((s) => ts.isExpressionStatement(s)
  && ts.isCallExpression(s.expression) && s.expression.expression.getText(source) === 'definePageMeta')
assert.ok(meta, 'definePageMeta exists')

const codigo = [
  'const DollarSign = "DollarSign", Settings2 = "Settings2", Upload = "Upload"',
  pegar('TABS'),
  `const META = ${meta.expression.arguments[0].getText(source)}`,
].join('\n')
const compiled = ts.transpileModule(codigo, {
  compilerOptions: { target: ts.ScriptTarget.ES2022 }, reportDiagnostics: true,
})
assert.deepEqual(compiled.diagnostics, [])
const navegacoes = []
const navigateTo = (to, opts) => { navegacoes.push({ to, opts }); return 'redirecionou' }
const { TABS, META } = new Function('navigateTo', `${compiled.outputText}\nreturn { TABS, META }`)(navigateTo)

// As abas dos departamentos.
assert.deepEqual(TABS.map((t) => t.label), ['Tabela de Preços', 'Contas', 'Produtos'])
assert.deepEqual(TABS.map((t) => t.key), ['tabela', 'contas', 'produtos'])

// A permissão da página não muda.
assert.deepEqual(META.permission, { resource: 'tabela_precos', action: 'view' })
assert.ok(META.middleware.includes('permission'), 'the permission middleware still runs')

// O link antigo vai para a Tabela de Preços, com o departamento de antes.
const redireciona = META.middleware.find((m) => typeof m === 'function'
  && m({ params: { tab: 'concorrencia' }, query: {}, hash: '' }) === 'redirecionou')
assert.ok(redireciona, 'a middleware handles /pricing/concorrencia')
navegacoes.length = 0
redireciona({ params: { tab: 'concorrencia' }, query: { dept: 'mala' }, hash: '' })
assert.deepEqual(navegacoes, [{
  to: { path: '/pricing/tabela', query: { dept: 'mala' }, hash: '' }, opts: { replace: true },
}])
navegacoes.length = 0
for (const tab of ['tabela', 'contas', 'produtos']) {
  assert.equal(redireciona({ params: { tab }, query: {}, hash: '' }), undefined, `/pricing/${tab} is untouched`)
}
assert.deepEqual(navegacoes, [])

// Nada da tela de busca sobrou no template.
const tpl = descriptor.template.content
assert.doesNotMatch(tpl, /Concorr|competitorQuery|searchCompetitor|API pública ML/)
assert.doesNotMatch(setup, /searchCompetitor|competitor-prices/)

console.log('pricing-sem-concorrencia: ok')
