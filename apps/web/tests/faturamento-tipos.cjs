// Run from apps/web: node tests/faturamento-tipos.cjs
// Compile and render the real page; only Nuxt auth/API boundaries are mocked.
// The type filter is admin-only, while existing readers retain the type column.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { renderToString } = require('vue/server-renderer')
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')

const filename = path.resolve(__dirname, '../pages/faturamento.vue')
const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
assert.deepEqual(errors, [])
const script = compileScript(descriptor, { id: 'faturamento-tipos' })
const template = compileTemplate({
  source: descriptor.template.content,
  filename,
  id: 'faturamento-tipos',
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
  const names = Object.keys(globals)
  new Function('exports', 'require', ...names, output.outputText)(
    exports,
    (name) => name.startsWith('~/')
      ? evaluate(fs.readFileSync(path.resolve(__dirname, '..', `${name.slice(2)}.ts`), 'utf8'))
      : require(name),
    ...names.map((name) => globals[name]),
  )
  return exports
}
const render = evaluate(template.code).render

const RESPONSE = {
  itens: [
    { store_id: 'amazon-poofy', loja: 'poofy', tipo: 'amazon', departments: ['mala'],
      pedidos: 2, faturamento: 300, ticket_medio: 150 },
    { store_id: 'ml-vr', loja: 'VR', tipo: 'ml', departments: ['celular', 'eletro'],
      pedidos: 1, faturamento: 100, ticket_medio: 100 },
    { store_id: 'orphan', loja: 'Sem cadastro (999)', tipo: null, departments: [],
      pedidos: 1, faturamento: 20, ticket_medio: 20 },
  ],
  total_pedidos: 4, total_faturamento: 420,
  start: '2026-10-01T00:00:00', end: '2026-11-01T00:00:00', teams: [1, 2], team: null,
}

async function page(role) {
  let instance
  let meta
  const calls = []
  const auth = Vue.reactive({ user: { role } })
  const component = evaluate(script.content, {
    definePageMeta: (value) => { meta = value },
    useAuthStore: () => auth,
    useApi: () => {
      instance = Vue.getCurrentInstance()
      return { api: async (url) => { calls.push(url); return structuredClone(RESPONSE) } }
    },
  }).default
  component.render = render
  await renderToString(Vue.createSSRApp(component))
  assert.deepEqual(meta, {
    middleware: ['permission'], permission: { resource: 'faturamento', action: 'view' },
  }, 'the existing page permission is preserved')
  const state = instance.setupState
  return {
    state, calls, auth,
    // Reuse the executed setup state when exercising interactions. This renders
    // the compiled template again, without starting a fresh page and resetting it.
    html: () => renderToString(Vue.createSSRApp({ setup: () => state, render })),
    query: () => new URL(calls.at(-1), 'https://test.invalid').searchParams,
  }
}

async function run() {
  const admin = await page('admin')
  let html = await admin.html()
  assert.match(html, /id="faturamento-tipo"/, 'admin can select a type')
  assert.match(html, /Todos os tipos/)
  assert.deepEqual([...html.matchAll(/<th\b[^>]*>(.*?)<\/th>/g)].map((match) => match[1]),
    ['Loja', 'Plataforma', 'Tipo', 'Pedidos', 'Faturamento', 'Ticket médio'])
  assert.match(html, /Amazon/)
  assert.match(html, /Mercado Livre/)
  assert.match(html, />Mala<\/span>/)
  assert.match(html, />Celular<\/span>/)
  assert.match(html, />Eletro<\/span>/, 'multiple store types are displayed')
  assert.match(html, /colspan="3"[^>]*>Total<\/td>/)
  assert.equal(admin.query().has('department'), false, 'initial load has no type restriction')

  admin.state.department = 'mala'
  admin.state.team = 2
  await admin.state.load()
  assert.equal(admin.query().get('department'), 'mala')
  assert.equal(admin.query().get('team'), '2', 'type filter preserves team selection')
  assert.ok(admin.query().get('start'))
  assert.ok(admin.query().get('end'))
  html = await admin.html()
  assert.match(html, /faturamento total das lojas classificadas como Mala/,
    'copy explains this is store classification, not revenue split by product')
  admin.state.department = ''
  await admin.state.load()
  assert.equal(admin.query().has('department'), false, 'clearing selects all types')

  const reader = await page('user')
  reader.state.department = 'mala'
  await reader.state.load()
  assert.equal(reader.query().has('department'), false,
    'stale or manipulated client state must not send an admin filter')
  html = await reader.html()
  assert.doesNotMatch(html, /id="faturamento-tipo"/)
  assert.doesNotMatch(html, /faturamento total das lojas classificadas/)
  assert.match(html, />Tipo<\/th>/, 'readers still see the type column')
  assert.match(html, />Mala<\/span>/, 'readers still see store classifications')

  admin.state.department = 'mala'
  admin.auth.user.role = 'user'
  await admin.state.load()
  assert.equal(admin.query().has('department'), false, 'guard follows the current user role')
  assert.doesNotMatch(await admin.html(), /id="faturamento-tipo"/)

  reader.state.data = { ...RESPONSE, itens: [], total_pedidos: 0, total_faturamento: 0 }
  html = await reader.html()
  assert.match(html, /colspan="6"/)
  assert.match(html, /Nenhum faturamento no período/)
  assert.doesNotMatch(html, /<tfoot/)
  reader.state.data = null
  reader.state.loading = true
  html = await reader.html()
  assert.match(html, /colspan="6"/)
  assert.match(html, /carregando/)
}

run().then(() => {
  console.log('PASS: real Faturamento SFC renders types for readers; type filter/query admin-only; period/team preserved; six-column empty/loading states and totals')
}).catch((error) => { console.error(error); process.exitCode = 1 })
