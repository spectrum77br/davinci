// Run from apps/web: node tests/faturamento-tipos.cjs
// A coluna Tipo do Faturamento (components/FaturamentoPainel.vue, o botão
// "Faturamento" da Tabela de Preços desde 13/11/2026; /faturamento redireciona).
// M-22 (24/11/2026): o select "Tipo" saiu (a COLUNA ficou) — a tela não manda
// mais `department` nem `team` à API: o servidor devolve as lojas do escopo e
// os filtros (Plataforma, Contas, Equipes) são na tela (tests/faturamento-barra.cjs).
// Os tipos de cada loja em chips, na ordem Celular, Eletro, Mala (M-23,
// lib/categorias.ts), e o resto depois; para quem é e para quem não é ADM.
// Renderiza o SFC de verdade; só a API, o login e o popover são de mentira.
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

const passa = (tag) => Vue.defineComponent({
  props: ['asChild'],
  inheritAttrs: !!tag,
  setup: (_, { slots }) => () => (tag ? Vue.h(tag, null, slots.default?.()) : slots.default?.()),
})
const REKA = { PopoverRoot: passa(null), PopoverTrigger: passa(null), PopoverPortal: passa(null), PopoverContent: passa('div') }

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
    (name) => {
      if (name === 'reka-ui') return REKA
      if (name.startsWith('~/')) return evaluate(fs.readFileSync(path.resolve(__dirname, '..', `${name.slice(2)}.ts`), 'utf8'))
      return require(name)
    },
    ...names.map((name) => globals[name]),
  )
  return exports
}
const render = evaluate(template.code).render

const RESPONSE = {
  itens: [
    { store_id: 'amazon-poofy', loja: 'poofy', tipo: 'amazon', departments: ['mala'], pedidos: 2, faturamento: 300, ticket_medio: 150 },
    // A API já manda em ordem; a tela ordena de novo pela lista única (M-23).
    { store_id: 'ml-vr', loja: 'VR', tipo: 'ml', departments: ['mala', 'eletro', 'celular'], pedidos: 1, faturamento: 100, ticket_medio: 100 },
    { store_id: 'ml-cat', loja: 'catalogo', tipo: 'mercadolivre', departments: ['catalogo', 'celular'], pedidos: 0, faturamento: 0, ticket_medio: 0 },
    { store_id: 'orphan', loja: 'Sem cadastro (999)', tipo: null, departments: [], pedidos: 1, faturamento: 20, ticket_medio: 20 },
  ],
  total_pedidos: 4, total_faturamento: 420,
  start: '2026-10-01T00:00:00', end: '2026-11-01T00:00:00', teams: [101, 102, 103],
  equipes: [{ numero: 101, nome: 'Londres' }, { numero: 102, nome: 'Israel' }, { numero: 103, nome: 'Marrocos' }],
  team: null,
}

async function page(role) {
  let instance
  const calls = []
  const auth = Vue.reactive({ user: { role, permissions: { faturamento: { view: true } } } })
  const component = evaluate(script.content, {
    useAuthStore: () => auth,
    useIsAdmin: () => Vue.computed(() => auth.user?.role === 'admin'),
    useCan: (res, acao) => Vue.computed(() => auth.user?.role === 'admin' || auth.user?.permissions?.[res]?.[acao] === true),
    useAjuda: () => {},
    useApi: () => {
      instance = Vue.getCurrentInstance()
      return { api: async (url) => { calls.push(url); return structuredClone(RESPONSE) } }
    },
  }).default
  component.render = render
  await renderToString(Vue.createSSRApp(component, { embutido: true }))
  const state = instance.setupState
  return {
    state, calls,
    html: async () => (await renderToString(Vue.createSSRApp({ setup: () => state, render }))).replace(/<!--[\s\S]*?-->/g, ''),
  }
}

// Os chips da coluna Tipo, linha a linha.
function tipos(html) {
  const corpo = html.match(/<tbody>([\s\S]*?)<\/tbody>/)[1]
  return [...corpo.matchAll(/<tr\b[^>]*data-linha-faturamento>([\s\S]*?)<\/tr>/g)].map((m) => {
    const td = [...m[1].matchAll(/<td\b[^>]*>([\s\S]*?)<\/td>/g)][3][1]
    const chips = [...td.matchAll(/<span class="inline-block rounded[^"]*">([^<]*)<\/span>/g)].map((c) => c[1])
    return chips.length ? chips : td.replace(/<[^>]+>/g, '').trim()
  })
}

async function run() {
  for (const role of ['admin', 'user']) {
    const p = await page(role)
    const html = await p.html()
    assert.deepEqual(tipos(html), [['Mala'], ['Celular', 'Eletro', 'Mala'], ['Celular', 'Catálogo'], '—'],
      `${role}: Celular, Eletro, Mala e o resto depois; sem tipo "—"`)
    assert.match(html, />Tipo<\/th>/, `${role}: a coluna Tipo continua`)
    assert.doesNotMatch(html, /faturamento-tipo|Todos os tipos|Sem categoria|<select\b|Mostrando só o que cada loja vendeu/,
      `${role}: o select Tipo saiu`)
    assert.ok(p.calls.length >= 1)
    for (const url of p.calls) {
      const q = new URL(url, 'https://test.invalid').searchParams
      assert.ok(q.get('start') && q.get('end'))
      assert.equal(q.has('department'), false, `${role}: nunca manda department`)
      assert.equal(q.has('team'), false, `${role}: as equipes filtram na tela`)
    }
    // Mesmo com um estado antigo/manipulado, nada de department.
    p.state.department = 'mala'
    await p.state.load()
    assert.equal(new URL(p.calls.at(-1), 'https://test.invalid').searchParams.has('department'), false)
  }

  // O link antigo /faturamento: a mesma permissão de antes e, depois, direto
  // para /pricing/faturamento mantendo a query (favoritos continuam valendo).
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

  // A fonte: os chips pela lista única das categorias.
  const fonte = descriptor.scriptSetup.content
  assert.match(fonte, /import \{ ORDEM_CATEGORIAS, ROTULO_CATEGORIA, ordenarCategorias \} from '~\/lib\/categorias'/)
  assert.match(fonte, /return ordenarCategorias\(r\.departments \?\? \[\]\)/)
  assert.match(descriptor.template.content, /v-for="slug in tiposDaLoja\(r\)"/)
}

run().then(() => {
  console.log('PASS: Faturamento — coluna Tipo em chips Celular, Eletro, Mala (e o resto depois) para ADM e leitor; sem o select Tipo; a API não recebe department nem team; /faturamento redireciona')
}).catch((error) => { console.error(error); process.exitCode = 1 })
