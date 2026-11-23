// Run from apps/web: node tests/faturamento-tipos.cjs
// Compile and render the real Faturamento screen; only Nuxt auth/API boundaries
// are mocked. Since 13/11/2026 it lives in components/FaturamentoPainel.vue and
// opens as the "Faturamento" button of the Tabela de Preços; /faturamento is a
// redirect page (checked at the end).
// The type filter is admin-only; mobile cards retain the desktop rows and totals.
// Type filter (17/11/2026, Novos procedimentos 7): Celular · Eletro · Mala (M-23) ·
// Sem categoria (no Shein) = what each store SOLD of that department (the API
// splits each order among its items); an error clears the previous table.
// Equipe (30/10/2026): the select lists the sales teams by name (Londres,
// Israel, Marrocos) and only shows up when there is more than one to pick.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { renderToString } = require('vue/server-renderer')
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')

const filename = path.resolve(__dirname, '../components/FaturamentoPainel.vue')
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
  start: '2026-10-01T00:00:00', end: '2026-11-01T00:00:00', teams: [101, 102, 103],
  equipes: [
    { numero: 101, nome: 'Londres' }, { numero: 102, nome: 'Israel' },
    { numero: 103, nome: 'Marrocos' },
  ],
  team: null,
}

async function page(role, props = {}) {
  let instance
  let meta
  const calls = []
  // `falha.erro` set → the next API calls reject with it (as $fetch does).
  const falha = { erro: null }
  const auth = Vue.reactive({ user: { role } })
  const component = evaluate(script.content, {
    definePageMeta: (value) => { meta = value },
    useAuthStore: () => auth,
    useApi: () => {
      instance = Vue.getCurrentInstance()
      return {
        api: async (url) => {
          calls.push(url)
          if (falha.erro) throw falha.erro
          return structuredClone(RESPONSE)
        },
      }
    },
  }).default
  component.render = render
  await renderToString(Vue.createSSRApp(component, props))
  assert.equal(meta, undefined, 'a component, not a page: the route permission lives in the pages')
  const state = instance.setupState
  return {
    state, calls, auth, falha,
    // Reuse the executed setup state when exercising interactions. This renders
    // the compiled template again, without starting a fresh page and resetting it.
    html: () => renderToString(Vue.createSSRApp({ setup: () => state, render })),
    query: () => new URL(calls.at(-1), 'https://test.invalid').searchParams,
  }
}

// These checks exercise rendered values, not viewport classes. The browser
// checks cover which layout is visible; SSR guards the shared financial data.
function textContent(markup) {
  return markup.replace(/<!--[\s\S]*?-->/g, '').replace(/<[^>]+>/g, '')
    .replace(/\s+/g, ' ').trim()
}
function tagContents(markup, tag) {
  return [...markup.matchAll(new RegExp(`<${tag}\\b[^>]*>([\\s\\S]*?)</${tag}>`, 'g'))]
    .map((match) => textContent(match[1]))
}
// [value, label] of each Tipo option, or null when the select is not rendered.
function typeOptions(html) {
  const select = html.match(/<select\b[^>]*id="faturamento-tipo"[^>]*>([\s\S]*?)<\/select>/)
  if (!select) return null
  return [...select[1].matchAll(/<option\b([^>]*)>([\s\S]*?)<\/option>/g)]
    .map(([, attrs, label]) => [attrs.match(/\bvalue="([^"]*)"/)?.[1] ?? null, textContent(label)])
}
// [value, label] of each Equipe option, or null when the select is not rendered.
function teamOptions(html) {
  const select = html.match(/<select\b[^>]*id="faturamento-equipe"[^>]*>([\s\S]*?)<\/select>/)
  if (!select) return null
  return [...select[1].matchAll(/<option\b([^>]*)>([\s\S]*?)<\/option>/g)]
    .map(([, attrs, label]) => [attrs.match(/\bvalue="([^"]*)"/)?.[1] ?? null, textContent(label)])
}
function mobileSection(html) {
  const match = html.match(/<section\b[^>]*aria-label="Faturamento por loja"[^>]*>([\s\S]*?)<\/section>/)
  assert.ok(match, 'the mobile store summary is rendered')
  return match[1]
}
function assertMobileCards(html, response) {
  const mobile = mobileSection(html)
  const cards = [...mobile.matchAll(/<article\b[^>]*>([\s\S]*?)<\/article>/g)]
    .map((match) => match[1])
  assert.equal(cards.length, response.itens.length, 'one mobile card per store row')
  const tableBody = html.match(/<tbody>([\s\S]*?)<\/tbody>/)[1]
  const tableRows = [...tableBody.matchAll(/<tr\b[^>]*>([\s\S]*?)<\/tr>/g)]
    .map((match) => tagContents(match[1], 'td'))
  const money = (value) => value.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
    .replace(/\s+/g, ' ')
  const typeLabels = { mala: 'Mala', celular: 'Celular', eletro: 'Eletro' }
  cards.forEach((card, index) => {
    const row = response.itens[index]
    const desktop = tableRows[index]
    assert.deepEqual(tagContents(card, 'h2'), [row.loja || '—'])
    assert.deepEqual(tagContents(card, 'p'), [desktop[1]], 'same platform on card and desktop')
    assert.deepEqual(tagContents(card, 'span'), row.departments.length
      ? row.departments.map((type) => typeLabels[type]) : ['Sem tipo'])
    assert.deepEqual(tagContents(card, 'dt'), ['Faturamento', 'Pedidos', 'Ticket médio'])
    const expected = [money(row.faturamento), row.pedidos.toLocaleString('pt-BR'), money(row.ticket_medio)]
    assert.deepEqual(tagContents(card, 'dd'), expected, 'card keeps each financial value under the correct label')
    assert.deepEqual([desktop[4], desktop[3], desktop[5]], expected, 'card and table use the same store values')
  })
  const summary = mobile.slice(0, mobile.indexOf('<article'))
  assert.deepEqual(tagContents(summary, 'p'), [
    'Pedidos no período', response.total_pedidos.toLocaleString('pt-BR'),
    'Faturamento total', money(response.total_faturamento),
  ], 'mobile totals come from the response, independently of store cards')
  const footer = html.match(/<tfoot\b[^>]*>([\s\S]*?)<\/tfoot>/)[1]
  assert.deepEqual(tagContents(footer, 'td').slice(1, 3), [
    response.total_pedidos.toLocaleString('pt-BR'), money(response.total_faturamento),
  ], 'desktop and mobile totals agree')
}

async function run() {
  const admin = await page('admin')
  let html = await admin.html()
  assertMobileCards(html, RESPONSE)
  assert.match(html, /<div class="faturamento-page min-w-0 space-y-4 p-4">/, 'standalone keeps the page padding')
  assert.match(html, /<h1 class="text-xl font-semibold">Faturamento<\/h1>/)
  // Inside the Tabela de Preços: the same filters and table, no page padding,
  // and an <h2> under the page's own <h1>.
  const embutido = await page('admin', { embutido: true })
  const htmlEmbutido = await embutido.html()
  assert.match(htmlEmbutido, /<div class="faturamento-page min-w-0 space-y-4">/)
  assert.match(htmlEmbutido, /<h2 class="text-base font-semibold">Faturamento<\/h2>/)
  assert.doesNotMatch(htmlEmbutido, /<h1\b/)
  assert.match(htmlEmbutido, /Pedidos faturáveis \(em aberto, em andamento e entregues\), por loja\./)
  assertMobileCards(htmlEmbutido, RESPONSE)
  assert.deepEqual([...htmlEmbutido.matchAll(/<th\b[^>]*>(.*?)<\/th>/g)].map((match) => match[1]),
    ['Loja', 'Plataforma', 'Tipo', 'Pedidos', 'Faturamento', 'Ticket médio'])
  assert.match(htmlEmbutido, /id="faturamento-tipo"/)
  assert.deepEqual(teamOptions(htmlEmbutido), teamOptions(html), 'same team filter embedded')
  assert.match(html, /id="faturamento-tipo"/, 'admin can select a type')
  assert.match(html, /Todos os tipos/)
  assert.deepEqual(typeOptions(html), [
    [null, 'Todos os tipos'], ['celular', 'Celular'], ['eletro', 'Eletro'], ['mala', 'Mala'],
    ['sem', 'Sem categoria'],
  ], 'the product departments in alphabetical order (M-23, 23/11/2026); Shein is not one')
  assert.doesNotMatch(html, /Shein/)
  assert.deepEqual([...html.matchAll(/<th\b[^>]*>(.*?)<\/th>/g)].map((match) => match[1]),
    ['Loja', 'Plataforma', 'Tipo', 'Pedidos', 'Faturamento', 'Ticket médio'])
  assert.match(html, /Amazon/)
  assert.match(html, /Mercado Livre/)
  assert.match(html, />Mala<\/span>/)
  assert.match(html, />Celular<\/span>/)
  assert.match(html, />Eletro<\/span>/, 'multiple store types are displayed')
  assert.match(html, /colspan="3"[^>]*>Total<\/td>/)
  assert.equal(admin.query().has('department'), false, 'initial load has no type restriction')
  assert.equal(admin.query().has('team'), false, 'initial load has no team restriction')
  assert.deepEqual(teamOptions(html), [
    [null, 'Todas as equipes'], ['101', 'Londres'], ['102', 'Israel'], ['103', 'Marrocos'],
  ], 'admin picks the sales teams by name; the value sent is the team number')
  assert.doesNotMatch(html, /Equipe 10[123]/, 'no bare team numbers when the API sends names')

  admin.state.department = 'mala'
  admin.state.team = 102
  await admin.state.load()
  assert.equal(admin.query().get('department'), 'mala')
  assert.equal(admin.query().get('team'), '102', 'type filter preserves team selection')
  assert.ok(admin.query().get('start'))
  assert.ok(admin.query().get('end'))
  html = await admin.html()
  assert.match(html, /Mostrando só o que cada loja vendeu de Mala\./,
    'copy explains this is what each store sold of the department, not its whole revenue')
  assert.doesNotMatch(html, /classificadas como/)
  admin.state.department = 'sem'
  await admin.state.load()
  assert.equal(admin.query().get('department'), 'sem')
  assert.match(await admin.html(), /Mostrando só o que cada loja vendeu sem categoria/)

  // An error (e.g. 400 invalid_department) clears the previous table instead of
  // leaving it under the message as if it were the new filter's answer; the
  // team filter stays usable.
  admin.state.department = 'eletro'
  admin.falha.erro = { data: { detail: { code: 'invalid_department' } } }
  await admin.state.load()
  admin.falha.erro = null
  html = await admin.html()
  assert.match(html, /invalid_department/)
  assert.equal(admin.state.data, null)
  assert.doesNotMatch(html, /<article\b|<tfoot|poofy|Nenhum faturamento/)
  assert.deepEqual(teamOptions(html), [
    [null, 'Todas as equipes'], ['101', 'Londres'], ['102', 'Israel'], ['103', 'Marrocos'],
  ], 'team filter survives the error')
  await admin.state.load()
  assert.doesNotMatch(await admin.html(), /invalid_department/, 'next good load clears the error')
  assertMobileCards(await admin.html(), RESPONSE)

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
  assert.doesNotMatch(html, /Mostrando só o que cada loja vendeu/)
  assert.match(html, />Tipo<\/th>/, 'readers still see the type column')
  assert.match(html, />Mala<\/span>/, 'readers still see store classifications')
  assertMobileCards(html, RESPONSE)

  admin.state.department = 'mala'
  admin.auth.user.role = 'user'
  await admin.state.load()
  assert.equal(admin.query().has('department'), false, 'guard follows the current user role')
  assert.doesNotMatch(await admin.html(), /id="faturamento-tipo"/)

  // A team member gets only their own team back: nothing to pick, no select.
  reader.state.data = { ...RESPONSE, teams: [102], equipes: [{ numero: 102, nome: 'Israel' }] }
  assert.equal(teamOptions(await reader.html()), null, 'single team hides the select')
  // Someone in two teams can still switch between them, by name.
  reader.state.data = { ...RESPONSE, teams: [101, 102], equipes: RESPONSE.equipes.slice(0, 2) }
  assert.deepEqual(teamOptions(await reader.html()), [
    [null, 'Todas as equipes'], ['101', 'Londres'], ['102', 'Israel'],
  ])
  // An API without `equipes` (the one before 30/10) still yields a usable list.
  const { equipes: _ignored, ...semNomes } = RESPONSE
  reader.state.data = { ...semNomes, teams: [1, 2] }
  assert.deepEqual(teamOptions(await reader.html()), [
    [null, 'Todas as equipes'], ['1', 'Equipe 1'], ['2', 'Equipe 2'],
  ])

  const refreshed = { ...RESPONSE, itens: [RESPONSE.itens[1]], total_pedidos: 1, total_faturamento: 100 }
  reader.state.data = refreshed
  assertMobileCards(await reader.html(), refreshed)

  reader.state.data = { ...RESPONSE, itens: [], total_pedidos: 0, total_faturamento: 0 }
  html = await reader.html()
  assert.match(html, /colspan="6"/)
  assert.match(html, /Nenhum faturamento no período/)
  assert.doesNotMatch(html, /<tfoot/)
  const emptyMobile = mobileSection(html)
  assert.doesNotMatch(emptyMobile, /<article\b|Faturamento total/)
  assert.match(emptyMobile, /Nenhum faturamento no período/)
  assert.match(emptyMobile, /Você vê apenas lojas das suas equipes de vendas/)
  reader.state.data = null
  reader.state.loading = true
  html = await reader.html()
  assert.match(html, /colspan="6"/)
  assert.match(html, /carregando/)
  const loadingMobile = mobileSection(html)
  assert.match(loadingMobile, /role="status"/)
  assert.match(loadingMobile, /carregando/)
  assert.doesNotMatch(loadingMobile, /<article\b|Faturamento total|Nenhum faturamento/)

  // The old /faturamento link: same permission as before, then straight to
  // /pricing/faturamento keeping the query (bookmarks keep working).
  const antiga = path.resolve(__dirname, '../pages/faturamento.vue')
  const pagina = parse(fs.readFileSync(antiga, 'utf8'), { filename: antiga }).descriptor
  let metaAntiga
  const navegacoes = []
  evaluate(compileScript(pagina, { id: 'faturamento-antiga' }).content, {
    definePageMeta: (value) => { metaAntiga = value },
    navigateTo: (to, opts) => { navegacoes.push({ to, opts }); return 'redirecionou' },
  }).default.setup({}, { expose() {} })
  assert.deepEqual(metaAntiga.permission, { resource: 'faturamento', action: 'view' })
  assert.equal(metaAntiga.middleware[0], 'permission', 'permission is checked before redirecting')
  assert.equal(metaAntiga.middleware[1]({ query: { x: '1' }, hash: '' }), 'redirecionou')
  assert.deepEqual(navegacoes, [{ to: { path: '/pricing/faturamento', query: { x: '1' }, hash: '' }, opts: { replace: true } }])
}

run().then(() => {
  console.log('PASS: real Faturamento SFC renders types for readers; type filter/query admin-only (Celular/Eletro/Mala/Sem categoria, what each store sold); error clears the table; period/team preserved; Equipe select by name (Londres/Israel/Marrocos), hidden with one team, "Equipe N" without names; mobile cards/desktop values and totals agree after refresh; empty/loading states')
}).catch((error) => { console.error(error); process.exitCode = 1 })
