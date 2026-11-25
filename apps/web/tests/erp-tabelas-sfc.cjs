// Run from apps/web: node tests/erp-tabelas-sfc.cjs
// Produtos 2 e Pedidos 2 (23/11/2026): as telas do próprio DaVinci ERP dentro do DaVinci.
// O que este teste trava:
//  · as duas páginas compilam e mostram a tela do ERP (iframe em /erp?embed=1, mesmo site e mesmo
//    portão), Produtos e Lidos das lojas;
//  · só quem entra no ERP vê e abre: middleware `admin` + `erp` (u.erp === true) e itens do
//    menu com erpOnly, logo abaixo de Produtos, fora de /erp/* (que o Caddy manda ao ERP).
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')

const root = path.join(__dirname, '..')
for (const [file, src] of [
  ['pages/erp-produtos.vue', null],
  ['pages/erp-pedidos.vue', null],
  ['components/ErpTelas.vue', '/erp?embed=1#products'],
]) {
  const source = fs.readFileSync(path.join(root, file), 'utf8')
  const { descriptor, errors } = parse(source, { filename: file })
  assert.deepEqual(errors, [], file)
  const script = compileScript(descriptor, { id: file })
  const template = compileTemplate({ source: descriptor.template.content, filename: file, id: file, compilerOptions: { bindingMetadata: script.bindings } })
  assert.deepEqual(template.errors, [], file)
  if (src === null) {
    assert.match(source, /definePageMeta\(\{ middleware: \['admin', 'erp'\] \}\)/, file)
    continue
  }
  // A tela fica no layout e só some da vista (v-show): voltar não recarrega o ERP.
  assert.ok(source.includes("'/erp-produtos': { src: '/erp?embed=1#products'"), file)
  assert.ok(source.includes("'/erp-pedidos': { src: '/erp?embed=1#orders/store'"), file)
  assert.match(source, /v-show="atual === caminho"/, file)
  assert.match(source, /v-if="auth\.user\?\.erp === true"/, file)
  // "V 310306" num lançamento de estoque abre o pedido em Pedidos 2: só mensagens do próprio site.
  assert.match(source, /evento\.origin !== window\.location\.origin \|\| evento\.data\?\.tipo !== 'erp-abrir-pedido'/, file)
  assert.ok(source.includes('`/erp?embed=1#orders/store/${pedidoInicial.value}`'), file)
  assert.match(source, /navigateTo\(PEDIDOS\)/, file)
}
const layout = fs.readFileSync(path.join(root, 'layouts/default.vue'), 'utf8')
assert.match(layout, /<slot \/>\n\s*<ClientOnly><ErpTelas \/><\/ClientOnly>/)
const middleware = fs.readFileSync(path.join(root, 'middleware/erp.ts'), 'utf8')
assert.match(middleware, /u\.erp !== true\) return navigateTo\('\/403'\)/)
const sidebar = fs.readFileSync(path.join(root, 'components/AppSidebar.vue'), 'utf8')
const produtos = sidebar.indexOf("{ to: '/produtos', label: 'Produtos'")
const dois = sidebar.indexOf("{ to: '/erp-produtos', label: 'Produtos 2', icon: Package, adminOnly: true, erpOnly: true }")
const pedidos = sidebar.indexOf("{ to: '/erp-pedidos', label: 'Pedidos 2', icon: ShoppingCart, adminOnly: true, erpOnly: true }")
assert.ok(produtos > 0 && dois > produtos && pedidos > dois, 'Produtos 2 e Pedidos 2 logo abaixo de Produtos')
assert.ok(sidebar.slice(produtos, pedidos).split('\n').length <= 6, 'sem outros itens entre eles')
console.log('ok erp-tabelas-sfc')
