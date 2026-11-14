// Run from apps/web: node tests/pricing-faturamento.cjs
// Organização do DaVinci (13/11/2026), item 6: o Faturamento sai do menu e vira
// um botão da Tabela de Preços, depois de Eletro, em /pricing/faturamento. As
// sub-abas (Tabela de Preços · Contas · Produtos) somem enquanto ele está
// escolhido; o link antigo /faturamento redireciona (tests/faturamento-tipos.cjs).
// Executa o middleware real da página e confere o template e o menu.
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
const meta = source.statements.find((s) => ts.isExpressionStatement(s)
  && ts.isCallExpression(s.expression) && s.expression.expression.getText(source) === 'definePageMeta')
const compiled = ts.transpileModule(`const META = ${meta.expression.arguments[0].getText(source)}`, {
  compilerOptions: { target: ts.ScriptTarget.ES2022 }, reportDiagnostics: true,
})
assert.deepEqual(compiled.diagnostics, [])
let usuario = null
const navegacoes = []
const navigateTo = (to, opts) => { navegacoes.push({ to, opts }); return to.path }
const { META } = new Function('navigateTo', 'useAuthStore',
  `${compiled.outputText}\nreturn { META }`)(navigateTo, () => ({ user: usuario }))

// Entra quem tem a Tabela de Preços OU o Faturamento (o 'permission' confere).
assert.deepEqual(META.permissionAny, [
  { resource: 'tabela_precos', action: 'view' },
  { resource: 'faturamento', action: 'view' },
])
const iPerm = META.middleware.indexOf('permission')
assert.ok(iPerm >= 0)
const porPermissao = META.middleware[iPerm + 1]
assert.equal(typeof porPermissao, 'function', 'routing by permission runs after the permission check')
const ir = (tab, perms, role = 'user') => {
  usuario = { role, permissions: perms }
  navegacoes.length = 0
  const r = porPermissao({ params: { tab }, query: { dept: 'mala' }, hash: '' })
  if (r) assert.deepEqual(navegacoes[0].opts, { replace: true })
  if (r) assert.deepEqual(navegacoes[0].to.query, { dept: 'mala' }, 'keeps ?dept=')
  return r
}
const precos = { tabela_precos: { view: true } }
const fat = { faturamento: { view: true } }
const ambos = { ...precos, ...fat }
for (const tab of ['tabela', 'contas', 'produtos', 'faturamento']) {
  assert.equal(ir(tab, {}, 'admin'), undefined, `admin opens /pricing/${tab}`)
  assert.equal(ir(tab, ambos), undefined, `both permissions open /pricing/${tab}`)
}
assert.equal(ir('faturamento', precos), '/pricing/tabela', 'without Faturamento → Tabela de Preços')
for (const tab of ['tabela', 'contas', 'produtos']) {
  assert.equal(ir(tab, precos), undefined)
  assert.equal(ir(tab, fat), '/pricing/faturamento', 'only Faturamento → straight to it')
}
assert.equal(ir('faturamento', fat), undefined)

// O botão: na linha dos departamentos, depois deles (Eletro), sem número.
const tpl = descriptor.template.content
const linha = tpl.slice(tpl.indexOf('<!-- ============================================ Department pills -->'),
  tpl.indexOf('<!-- ============================================ Sub-tabs -->'))
const iDepts = linha.indexOf('v-for="d in DEPARTMENTS"')
const iFat = linha.indexOf('data-botao-faturamento')
assert.ok(iDepts > 0 && iFat > iDepts, 'Faturamento comes after the department buttons')
const botao = linha.slice(linha.lastIndexOf('<button', iFat), linha.indexOf('</button>', iFat))
assert.match(botao, /v-if="podeFaturamento"/)
assert.match(botao, /<Coins class="h-4 w-4" \/>\s*Faturamento\s*$/, 'just the name, no count')
assert.match(botao, /tab === 'faturamento'\s*\? 'bg-primary text-primary-foreground border-primary'/, 'same pill style as the departments')
assert.match(linha, /:class="tab !== 'faturamento' && department === d\.value/, 'no department lit while Faturamento is open')
assert.match(linha, /<template v-if="podePrecos">/, 'departments are for whoever sees the Tabela de Preços')
// As sub-abas somem no Faturamento; a tela é a de antes.
assert.match(tpl, /<!-- ============================================ Sub-tabs -->[\s\S]*?<div v-if="tab !== 'faturamento'" class="flex flex-wrap gap-1 rounded-md bg-muted\/40 p-1 w-fit">/)
assert.match(tpl, /<\/section>\s*(<!--[\s\S]*?-->\s*)*<FaturamentoPainel v-else-if="tab === 'faturamento'" embutido \/>/,
  'Faturamento panel closes the v-if chain of the sections')
// Escolher um departamento com o Faturamento aberto volta para a Tabela.
assert.match(setup, /if \(tab\.value === 'faturamento'\) \{\s*router\.push\(\{ path: '\/pricing\/tabela', query: \{ \.\.\.route\.query, dept: v \} \}\)/)
// Quem só tem Faturamento não carrega a Tabela de Preços.
assert.match(setup, /if \(podePrecos\.value\) \{\s*await loadSegments\(\)\s*await loadAccounts\(\)\s*if \(tab\.value !== 'faturamento'\) \{\s*await loadProducts\(\)\s*await loadIntegrations\(\)/)

// /pricing (sem aba) deixa entrar quem /pricing/[tab] deixa: o redirecionamento
// para /pricing/tabela leva quem só tem Faturamento ao Faturamento (acima).
const indice = fs.readFileSync(path.resolve(__dirname, '../pages/pricing/index.vue'), 'utf8')
assert.match(indice, /permissionAny: \[\s*\{ resource: 'tabela_precos', action: 'view' \},\s*\{ resource: 'faturamento', action: 'view' \},\s*\]/)
assert.doesNotMatch(indice, /permission: \{/, 'not only tabela_precos any more')
assert.match(indice, /navigateTo\('\/pricing\/tabela'/)

// Menu: Faturamento saiu; a Tabela de preços fica marcada no Faturamento.
const menu = fs.readFileSync(path.resolve(__dirname, '../components/AppSidebar.vue'), 'utf8')
assert.doesNotMatch(menu, /to: '\/faturamento'/)
assert.match(menu, /to: '\/pricing\/tabela', label: 'Tabela de preços'[^\n]*match: \['\/pricing', '\/faturamento'\]/)

console.log('pricing-faturamento: ok')
