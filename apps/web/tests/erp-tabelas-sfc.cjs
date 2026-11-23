// Run from apps/web: node tests/erp-tabelas-sfc.cjs
// Produtos 2 e Pedidos 2 (23/11/2026): as tabelas do DaVinci ERP dentro do DaVinci.
// O que este teste trava:
//  · as duas páginas compilam (parse + compileScript + compileTemplate);
//  · só quem entra no ERP vê e abre: middleware `admin` + `erp` (u.erp === true) e itens do
//    menu com erpOnly, logo abaixo de Produtos, fora de /erp/* (que o Caddy manda ao ERP);
//  · os dados vêm do próprio ERP, só GET: /api/erp-local/products/list e /store-sales.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')

const root = path.join(__dirname, '..')
for (const [file, endpoint] of [
  ['pages/erp-produtos.vue', '/api/erp-local/products/list'],
  ['pages/erp-pedidos.vue', '/api/erp-local/store-sales'],
]) {
  const source = fs.readFileSync(path.join(root, file), 'utf8')
  const { descriptor, errors } = parse(source, { filename: file })
  assert.deepEqual(errors, [], file)
  const script = compileScript(descriptor, { id: file })
  const template = compileTemplate({ source: descriptor.template.content, filename: file, id: file, compilerOptions: { bindingMetadata: script.bindings } })
  assert.deepEqual(template.errors, [], file)
  assert.match(source, /definePageMeta\(\{ middleware: \['admin', 'erp'\] \}\)/, file)
  assert.ok(source.includes(`'${endpoint}'`), `${file} lê ${endpoint}`)
  assert.doesNotMatch(source, /method:\s*'(POST|PUT|PATCH|DELETE)'/, `${file} só lê`)
}
const middleware = fs.readFileSync(path.join(root, 'middleware/erp.ts'), 'utf8')
assert.match(middleware, /u\.erp !== true\) return navigateTo\('\/403'\)/)
const sidebar = fs.readFileSync(path.join(root, 'components/AppSidebar.vue'), 'utf8')
const produtos = sidebar.indexOf("{ to: '/produtos', label: 'Produtos'")
const dois = sidebar.indexOf("{ to: '/erp-produtos', label: 'Produtos 2', icon: Package, adminOnly: true, erpOnly: true }")
const pedidos = sidebar.indexOf("{ to: '/erp-pedidos', label: 'Pedidos 2', icon: ShoppingCart, adminOnly: true, erpOnly: true }")
assert.ok(produtos > 0 && dois > produtos && pedidos > dois, 'Produtos 2 e Pedidos 2 logo abaixo de Produtos')
assert.ok(sidebar.slice(produtos, pedidos).split('\n').length <= 6, 'sem outros itens entre eles')
console.log('ok erp-tabelas-sfc')
