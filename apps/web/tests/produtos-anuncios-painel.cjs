// Run from apps/web: node tests/produtos-anuncios-painel.cjs
// Produtos › Anúncios (Organização do DaVinci, 13/11/2026, item 2): a tabela de
// "anúncios importados" da antiga /anuncios, agora só do departamento
// escolhido. Compila e renderiza o componente real; só a API e as permissões
// são simuladas. Confere: o ?departamento= vai na consulta, os cartões vêm do
// resumo da API (o departamento inteiro, não a página de 50), troca de
// departamento volta à página 1, vincular/desvincular avisa a página
// (`mudou`) e as Solicitações de anúncio não estão mais na tela.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { renderToString } = require('vue/server-renderer')
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')

const filename = path.resolve(__dirname, '../components/ProdutosAnunciosPainel.vue')
const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
assert.deepEqual(errors, [])
const script = compileScript(descriptor, { id: 'produtos-anuncios' })
const template = compileTemplate({
  source: descriptor.template.content, filename, id: 'produtos-anuncios',
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

const LISTING = {
  user_id: 'u', integration_id: 'shopee-1', platform: 'shopee', description: null,
  price: 1990, stock: 3, category: null, thumbnail_url: null, raw_data: {},
  imported_at: '2026-11-01T12:00:00Z', created_at: '2026-11-01T12:00:00Z', updated_at: '2026-11-01T12:00:00Z',
}
const ITEMS = [
  { ...LISTING, id: 'l1', external_id: 'EXT-1', sku: 'mala.24', title: 'Mala 24', status: 'active', product_id: 'p1' },
  { ...LISTING, id: 'l2', external_id: 'EXT-2', sku: null, title: 'Anúncio solto', status: 'under_review', product_id: null },
]
// O resumo é do departamento inteiro: bem maior que a página.
const RESUMO = { total: 312, sem_produto: 41, ativos: 250, em_revisao: 7 }

function app(component, props) {
  const result = Vue.createSSRApp(component, props)
  result.component('StatCard', {
    props: ['label', 'value'], setup: (p) => () => Vue.h('div', { 'data-card': p.label }, String(p.value)),
  })
  result.component('Button', { setup: (_, { slots }) => () => Vue.h('button', slots.default?.()) })
  result.component('Input', { setup: () => () => Vue.h('input') })
  return result
}

async function painel({ departamento = 'celular', canEdit = true, comResumo = true } = {}) {
  let instance
  const calls = []
  const emitted = []
  const props = Vue.reactive({ departamento })
  const component = evaluate(script.content, {
    ref: Vue.ref, computed: Vue.computed, watch: Vue.watch,
    onMounted: () => {}, onUnmounted: () => {},
    useCan: (resource, action) => Vue.ref(resource === 'anuncios' && (action === 'view' || canEdit)),
    confirm: () => true,
    useApi: () => {
      instance = Vue.getCurrentInstance()
      return { api: async (url, options = {}) => {
        calls.push({ url, options })
        if (url.startsWith('/api/listings?')) {
          return { items: structuredClone(ITEMS), total: 2, page: 1, page_size: 50, ...(comResumo ? { resumo: RESUMO } : {}) }
        }
        if (url === '/api/integrations') return [{ id: 'shopee-1', platform: 'shopee', name: 'Loja S' }]
        if (url.startsWith('/api/listings/') && options.method) return {}
        throw new Error(`Unexpected request: ${url}`)
      } }
    },
  }).default
  component.render = render
  const wrapper = {
    setup: () => () => Vue.h(component, { ...props, onMudou: () => emitted.push('mudou') }),
  }
  await renderToString(app(wrapper))
  const state = instance.setupState
  await state.refreshAll() // o onMounted do painel; no SSR não roda
  return {
    state, calls, props, emitted,
    html: () => renderToString(app({ setup: () => state, render })),
    query: () => new URL(calls.filter((c) => c.url.startsWith('/api/listings?')).at(-1).url, 'https://t.invalid').searchParams,
  }
}

function cartoes(html) {
  return Object.fromEntries([...html.matchAll(/data-card="([^"]+)"[^>]*>([^<]*)</g)].map((m) => [m[1], m[2]]))
}

async function run() {
  const p = await painel({ departamento: 'mala' })
  assert.equal(p.query().get('departamento'), 'mala', 'only the chosen department')
  let html = await p.html()
  assert.deepEqual(cartoes(html), {
    'Total anúncios': '312', 'Sem produto': '41', 'Ativos': '250', 'Em revisão': '7',
  }, 'cards come from the department summary, not the page of 50')
  assert.match(html, /Mala 24/)
  assert.match(html, /Anúncio solto/)
  assert.match(html, /2 de 2 · pág 1\/1/)
  assert.deepEqual([...html.matchAll(/<th\b[^>]*>([^<]*)<\/th>/g)].map((m) => m[1]),
    ['', 'External ID', 'Título', 'Canal', 'SKU', 'Produto', 'Preço', 'Estoque', 'Status', 'Ações'])
  // Solicitações de anúncio saíram.
  assert.doesNotMatch(html, /olicita/)
  assert.ok(!p.calls.some((c) => c.url.includes('listing-requests')))
  assert.doesNotMatch(fs.readFileSync(filename, 'utf8'), /listing-requests|nova solicitação/)

  // Filtros continuam indo junto com o departamento.
  p.state.search = 'mala'
  p.state.filtroStatus = 'active'
  p.state.onlyUnlinked = true
  await p.state.refreshAll()
  const q = p.query()
  assert.equal(q.get('departamento'), 'mala')
  assert.equal(q.get('search'), 'mala')
  assert.equal(q.get('status'), 'active')
  assert.equal(q.get('unlinked'), 'true')

  // Vincular / desvincular / excluir avisam a página (números das abas).
  await p.state.patchListing('l2', { product_id: 'p9' })
  await p.state.deleteListing('l1')
  assert.deepEqual(p.emitted, ['mudou', 'mudou'])

  // API sem `resumo` (a de antes): cai na conta da página.
  const antiga = await painel({ comResumo: false })
  assert.deepEqual(cartoes(await antiga.html()), {
    'Total anúncios': '2', 'Sem produto': '1', 'Ativos': '1', 'Em revisão': '1',
  })

  // Quem só vê não tem os botões de vincular/excluir.
  const leitor = await painel({ canEdit: false })
  html = await leitor.html()
  assert.doesNotMatch(html, /title="vincular produto"|title="desvincular"|title="excluir"/)
  assert.equal(leitor.state.importableIntegrations.length, 1, 'exposed for the page header button')
}

run().then(() => console.log('PASS: Produtos › Anúncios — departamento na consulta, cartões do resumo, filtros, aviso de mudança, sem Solicitações'))
  .catch((error) => { console.error(error); process.exitCode = 1 })
