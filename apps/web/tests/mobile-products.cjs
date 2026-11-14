// Run from apps/web: node tests/mobile-products.cjs
// Render the real Products page with fixture API responses. Mutations are not
// sent: this checks the mobile overview's data, filtering and permission guards.
// Since 13/11/2026 the page is split by department (Celular · Mala · Eletro ·
// Sem departamento) with the tabs Produtos · Anúncios, like the Tabela de Preços.
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
const DEPARTAMENTOS = {
  produtos: { celular: 10, mala: 4, eletro: 3, sem: 2 },
  anuncios: { celular: 120, mala: 40, eletro: 30, sem: 9 },
}
function app(component) {
  const result = Vue.createSSRApp(component)
  // The Anúncios tab is its own component (tested by compiling it elsewhere).
  result.component('ProdutosAnunciosPainel', {
    props: ['departamento'], setup: (props) => () => Vue.h('section', { 'data-painel-anuncios': props.departamento }),
  })
  result.component('PageHeader', {
    props: ['title'], setup: (props, { slots }) => () => Vue.h('header', [Vue.h('h1', props.title), slots.actions?.()]),
  })
  result.component('Button', {
    setup: (_, { slots }) => () => Vue.h('button', slots.default?.()),
  })
  result.component('Input', { setup: () => () => Vue.h('input') })
  return result
}
async function page(canWrite, { query = {}, contagens = DEPARTAMENTOS, can = null } = {}) {
  let instance, meta
  const calls = []
  const route = Vue.reactive({ path: '/produtos', query: { ...query } })
  const navegacoes = []
  const router = {
    replace: (to) => { navegacoes.push(['replace', to]); route.query = { ...to.query } },
    push: (to) => { navegacoes.push(['push', to]); route.query = { ...to.query } },
  }
  const component = evaluate(script.content, {
    shallowRef: Vue.shallowRef,
    definePageMeta: (value) => { meta = value },
    useRoute: () => route,
    useRouter: () => router,
    useCan: (resource, action) => Vue.ref(can ? can(resource, action) : canWrite || action === 'view'),
    useApi: () => {
      instance = Vue.getCurrentInstance()
      return { api: async (url, options) => {
        calls.push({ url, options })
        if (url === '/api/products/departamentos') return structuredClone(contagens)
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
  assert.deepEqual(meta, {
    middleware: ['permission'],
    permissionAny: [{ resource: 'produtos', action: 'view' }, { resource: 'anuncios', action: 'view' }],
  }, 'Produtos opens for whoever sees Produtos OR Anúncios')
  const state = instance.setupState
  return { state, calls, route, navegacoes, html: () => renderToString(app({ setup: () => state, render })) }
}
function cards(html) {
  return html.match(/<section\b[^>]*aria-label="Resumo dos produtos"[\s\S]*?<\/section>/)?.[0] || ''
}
function botoes(html, label) {
  const bloco = html.match(new RegExp(`<div[^>]*aria-label="${label}"[^>]*>([\\s\\S]*?)</div>`))?.[1] || ''
  return [...bloco.matchAll(/<button\b([^>]*)>([\s\S]*?)<\/button>/g)].map(([, attrs, inner]) => ({
    texto: inner.replace(/<!--[\s\S]*?-->/g, '').replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim(),
    ativo: /aria-pressed="true"/.test(attrs),
  }))
}
async function departamentos() {
  // Celular · Mala · Eletro · Sem departamento: só o nome, sem número.
  const p = await page(false)
  let html = await p.html()
  assert.deepEqual(botoes(html, 'Departamentos'), [
    { texto: 'Celular', ativo: true }, { texto: 'Mala', ativo: false },
    { texto: 'Eletro', ativo: false }, { texto: 'Sem departamento', ativo: false },
  ])
  // Abas com o número do departamento escolhido.
  assert.deepEqual(botoes(html, 'Abas de Produtos'), [
    { texto: 'Produtos (10)', ativo: true }, { texto: 'Anúncios (120)', ativo: false },
  ])
  const lista = p.calls.find(({ url }) => url.startsWith('/api/products?'))
  assert.match(lista.url, /[?&]departamento=celular(&|$)/, 'the list asks only for the chosen department')
  assert.doesNotMatch(html, /data-painel-anuncios/, 'Anúncios panel is not mounted on the Produtos tab')
  assert.match(html, /Vincular Automático|Sincronizar Todos|Importar do Bling|Novo Produto|<\/header>/)

  // Trocar de departamento: URL (?dept=), página 1 e nova leitura.
  p.state.page = 3
  p.calls.length = 0
  p.state.departamento = 'mala'
  // SSR does not run watchers: call what watch(departamento) calls.
  p.state.aoTrocarDepartamento()
  await new Promise((resolve) => setTimeout(resolve, 0)) // a releitura não é aguardada
  assert.deepEqual(p.navegacoes.at(-1), ['replace', { path: '/produtos', query: { dept: 'mala' } }])
  assert.equal(p.state.page, 1)
  assert.ok(p.calls.some(({ url }) => /\/api\/products\?page=1&page_size=50&departamento=mala/.test(url)))
  html = await p.html()
  assert.deepEqual(botoes(html, 'Abas de Produtos').map((b) => b.texto), ['Produtos (4)', 'Anúncios (40)'])

  // Aba Anúncios: o painel recebe o departamento; a lista de produtos sai.
  p.state.toggleSelect('p-zero')
  p.state.setAba('anuncios')
  p.state.aoTrocarAba('anuncios')
  assert.deepEqual(p.navegacoes.at(-1), ['push', { path: '/produtos', query: { dept: 'mala', aba: 'anuncios' } }])
  assert.equal(p.state.selected.size, 0, 'product selection does not follow into Anúncios')
  html = await p.html()
  assert.match(html, /data-painel-anuncios="mala"/)
  assert.doesNotMatch(html, /Resumo dos produtos/)
  assert.doesNotMatch(html, />\s*importar\s*</, 'importar needs anuncios.edit, like the old page')
  assert.match(html, />\s*recarregar\s*</)
  assert.doesNotMatch(html, /Importar do Bling|Novo Produto/, 'product tools belong to the Produtos tab')

  const editorAnuncios = await page(true, { query: { aba: 'anuncios' } })
  assert.match(await editorAnuncios.html(), />\s*importar\s*</, 'whoever edits Anúncios gets importar')

  // Link antigo /anuncios → ?aba=anuncios abre direto o painel, sem ler a lista de produtos.
  const antigo = await page(false, { query: { aba: 'anuncios' } })
  assert.equal(antigo.state.aba, 'anuncios')
  assert.ok(!antigo.calls.some(({ url }) => url.startsWith('/api/products?')), 'no product list on the Anúncios tab')
  assert.ok(antigo.calls.some(({ url }) => url === '/api/products/departamentos'))
  assert.match(await antigo.html(), /data-painel-anuncios="celular"/)

  // Sem departamento só aparece com algo para classificar; ?dept=sem vazio → Celular.
  const vazio = { produtos: { ...DEPARTAMENTOS.produtos, sem: 0 }, anuncios: { ...DEPARTAMENTOS.anuncios, sem: 0 } }
  const semNada = await page(false, { query: { dept: 'sem' }, contagens: vazio })
  html = await semNada.html()
  assert.deepEqual(botoes(html, 'Departamentos').map((b) => b.texto), ['Celular', 'Mala', 'Eletro'])
  assert.equal(semNada.state.departamento, 'celular')
  semNada.state.acertarUrlDoDepartamento() // onMounted (o SSR não roda)
  assert.deepEqual(semNada.navegacoes.at(-1), ['replace', { path: '/produtos', query: { dept: 'celular' } }],
    'the URL follows, so the page does not jump back to Sem departamento later')
  const ordem = semNada.calls.map(({ url }) => url.split('?')[0])
  assert.ok(ordem.indexOf('/api/products/departamentos') < ordem.indexOf('/api/products'),
    'counts first: they decide which department the list is for')
  const soAnuncioSolto = { produtos: { ...vazio.produtos }, anuncios: { ...vazio.anuncios, sem: 1 } }
  const comAnuncio = await page(false, { query: { dept: 'sem' }, contagens: soAnuncioSolto })
  assert.equal(comAnuncio.state.departamento, 'sem', 'one unlinked listing is enough to show the button')
  assert.deepEqual(botoes(await comAnuncio.html(), 'Departamentos').at(-1), { texto: 'Sem departamento', ativo: true })

  // Quem só vê Anúncios fica na aba Anúncios (e não vê a aba Produtos).
  const soAnuncios = await page(false, { can: (r, a) => r === 'anuncios' && a === 'view' })
  assert.equal(soAnuncios.state.aba, 'anuncios')
  assert.ok(!soAnuncios.calls.some(({ url }) => url.startsWith('/api/products?')))
  assert.deepEqual(botoes(await soAnuncios.html(), 'Abas de Produtos').map((b) => b.texto), ['Anúncios (120)'])

  // Voltar para Produtos no mesmo departamento não relê a lista.
  p.calls.length = 0
  p.state.setAba('produtos')
  p.state.aoTrocarAba('produtos')
  await new Promise((resolve) => setTimeout(resolve, 0))
  assert.ok(!p.calls.some(({ url }) => url.startsWith('/api/products?')), 'same department: no reload')
  // Filtro de segmento: só os segmentos do departamento (Catálogo entra em Celular).
  p.state.segments = [
    { id: 'r-cel', parent_id: null, name: 'Celular', slug: 'celular', sort_order: 0, active: true },
    { id: 'r-cat', parent_id: null, name: 'Catálogo', slug: 'catalogo', sort_order: 1, active: true },
    { id: 'r-mala', parent_id: null, name: 'Mala', slug: 'mala', sort_order: 2, active: true },
    { id: 'apple', parent_id: 'r-cat', name: 'Apple', slug: 'apple', sort_order: 0, active: true },
    { id: 'm24', parent_id: 'r-mala', name: '24', slug: '24', sort_order: 0, active: true },
  ]
  assert.deepEqual(p.state.segmentFilterOptions.map((o) => o.label), ['Mala', 'Mala / 24'])
  p.state.departamento = 'celular'
  assert.deepEqual(p.state.segmentFilterOptions.map((o) => o.label), ['Catálogo', 'Catálogo / Apple', 'Celular'])
  assert.equal(p.state.departamentoDoSegmento('apple'), 'celular')
  assert.equal(p.state.departamentoDoSegmento(null), 'sem')
  assert.equal(p.state.departamentoDoSegmento('desconhecido'), 'sem')
}
async function run() {
  await departamentos()
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
