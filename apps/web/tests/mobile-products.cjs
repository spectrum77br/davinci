// Run from apps/web: node tests/mobile-products.cjs
// Render the real Products page with fixture API responses. Mutations are not
// sent: this checks the mobile overview's data, filtering and permission guards.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { renderToString } = require('vue/server-renderer')
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')
const filename = path.resolve(__dirname, '../pages/produtos.vue')
const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
assert.deepEqual(errors, [])
const script = compileScript(descriptor, { id: 'mobile-products' })
const template = compileTemplate({
  source: descriptor.template.content, filename, id: 'mobile-products',
  compilerOptions: { bindingMetadata: script.bindings },
})
assert.deepEqual(template.errors, [])
function evaluate(source, globals = {}) {
  const output = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
    reportDiagnostics: true,
  })
  assert.deepEqual(output.diagnostics, [])
  const exports = {}
  new Function('exports', 'require', ...Object.keys(globals), output.outputText)(
    exports, require, ...Object.values(globals),
  )
  return exports
}
const render = evaluate(template.code).render
const LINK = {
  product_id: 'p-zero', store_id: null, variation_id: null, external_sku: 'SKU-ZERO',
  listing_title: 'Anúncio de teste', listing_type: null, stock: 0, price: null,
  last_sync_status: 'ok', last_sync_at: '2026-10-01T12:00:00Z', last_error: null,
}
const PRODUCTS = [
  {
    id: 'p-zero', sku: 'SKU-ZERO', name: 'Produto sem estoque', stock: 0, min_stock: 0,
    segment_id: null, segment_path: null, image_url: null, observation: 'Conferir produto',
    bling_product_id: 123, updated_at: '2026-10-01T12:00:00Z',
    links: [
      { ...LINK, id: 'link-a', integration_id: 'amazon-a', platform: 'amazon', external_id: 'AMAZON-ID' },
      { ...LINK, id: 'link-s', integration_id: 'shopee-s', platform: 'shopee', external_id: 'SHOPEE-ID',
        external_sku: 'OUTRO-SKU', stock: 2, last_sync_status: 'fatal', last_error: 'Falha de teste' },
    ],
  },
  {
    id: 'p-ok', sku: 'SKU-OK', name: 'Produto disponível', stock: 6, min_stock: 2,
    segment_id: 'segment', segment_path: 'Eletro', image_url: null,
    updated_at: '2026-10-01T12:00:00Z', links: [],
  },
]
function app(component) {
  const result = Vue.createSSRApp(component)
  result.component('PageHeader', {
    props: ['title'], setup: (props, { slots }) => () => Vue.h('header', [Vue.h('h1', props.title), slots.actions?.()]),
  })
  result.component('Button', {
    setup: (_, { slots }) => () => Vue.h('button', slots.default?.()),
  })
  result.component('Input', { setup: () => () => Vue.h('input') })
  return result
}
async function page(canWrite) {
  let instance, meta
  const calls = []
  const component = evaluate(script.content, {
    shallowRef: Vue.shallowRef,
    definePageMeta: (value) => { meta = value },
    useCan: () => Vue.ref(canWrite),
    useApi: () => {
      instance = Vue.getCurrentInstance()
      return { api: async (url, options) => {
        calls.push({ url, options })
        if (url.startsWith('/api/products?')) return { items: structuredClone(PRODUCTS), total: 2, page: 1, page_size: 50 }
        if (url === '/api/integrations') return [
          { id: 'amazon-a', platform: 'amazon', name: 'Loja A' },
          { id: 'shopee-s', platform: 'shopee', name: 'Loja S' },
        ]
        if (url === '/api/settings') return { daily_sync_enabled: false }
        if (url === '/api/segments') return []
        throw new Error(`Unexpected request: ${url}`)
      } }
    },
  }).default
  component.render = render
  await renderToString(app(component))
  assert.deepEqual(meta, { middleware: ['permission'], permission: { resource: 'produtos', action: 'view' } })
  const state = instance.setupState
  return { state, calls, html: () => renderToString(app({ setup: () => state, render })) }
}
function cards(html) {
  return html.match(/<section\b[^>]*aria-label="Resumo dos produtos"[\s\S]*?<\/section>/)?.[0] || ''
}
async function run() {
  const reader = await page(false)
  assert.equal(reader.state.mobileProductView, 'cards', 'default view is a mobile summary')
  assert.equal(reader.state.mobileProductTools, false, 'admin tools do not occupy mobile first screen')
  assert.equal(reader.state.mobileProductFilters, false, 'extra filters start collapsed')
  let html = await reader.html()
  let summary = cards(html)
  assert.match(summary, /Produto sem estoque/)
  assert.match(summary, /Estoque Bling/)
  assert.match(summary, /Mínimo/)
  assert.match(summary, /Vínculos/)
  assert.match(summary, /Sem estoque/, 'zero stock is labelled even when minimum is also zero')
  assert.doesNotMatch(summary, /Selecionar produto/, 'read-only user has no mutation selection')
  assert.doesNotMatch(summary, /AMAZON-ID/, 'heavy link details are not rendered until expanded')
  assert.match(html, /class="table-card hidden lg:block"/, 'desktop table remains available without being the mobile default')
  reader.state.setMobileProductDetails('p-zero', { target: { open: true } })
  summary = cards(await reader.html())
  assert.match(summary, /AMAZON-ID/)
  assert.match(summary, /SHOPEE-ID/)
  assert.match(summary, /SKU diferente/)
  assert.match(summary, /Falha de teste/)
  reader.state.filtroIntegration = 'amazon-a'
  assert.deepEqual(reader.state.mobileLinksFor(reader.state.items[0]).map((link) => link.id), ['link-a'])
  summary = cards(await reader.html())
  assert.match(summary, /AMAZON-ID/)
  assert.doesNotMatch(summary, /SHOPEE-ID/, 'selected account limits displayed link details and count')
  reader.state.setMobileProductDetails('p-zero', { target: { open: false } })
  assert.doesNotMatch(cards(await reader.html()), /AMAZON-ID/)
  reader.state.stockFilter = 'ok'
  summary = cards(await reader.html())
  assert.match(summary, /Produto disponível/)
  assert.doesNotMatch(summary, /Produto sem estoque/, 'cards use the existing stock filter')
  reader.state.filtroSegment = 'missing'
  assert.match(cards(await reader.html()), /Nenhum produto encontrado com estes filtros/)
  reader.state.mobileProductView = 'table'
  html = await reader.html()
  assert.equal(cards(html), '', 'explicit table mode replaces the mobile cards')
  assert.match(html, /class="table-card block"/)
  assert.ok(reader.calls.every(({ options }) => !options?.method), 'all overview actions remain read-only')

  const editor = await page(true)
  summary = cards(await editor.html())
  assert.match(summary, /Selecionar produto SKU-ZERO/)
  editor.state.toggleSelect('p-zero')
  assert.equal(editor.state.selected.has('p-zero'), true, 'card selection reuses existing bulk selection')
  assert.match(cards(await editor.html()), /<input(?=[^>]*aria-label="Selecionar produto SKU-ZERO")(?=[^>]*\bchecked\b)[^>]*>/)
}
run().then(() => console.log('PASS: real Products mobile summary — stock/minimum, scoped link details, lazy expansion, existing filters, read-only guards, bulk selection and optional desktop table')).catch((error) => {
  console.error(error)
  process.exitCode = 1
})
