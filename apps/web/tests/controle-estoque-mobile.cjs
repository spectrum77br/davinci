// Run from apps/web: node tests/controle-estoque-mobile.cjs
// Execute the real SFC against a local fake API. Responsive browser checks cover
// portrait/landscape layout; these checks cover card data, actions and access.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { renderToString } = require('vue/server-renderer')
const { parse, compileScript, compileTemplate, compileStyle } = require('vue/compiler-sfc')

const filename = path.resolve(__dirname, '../pages/controle-estoque.vue')
const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
assert.deepEqual(errors, [])
const script = compileScript(descriptor, { id: 'stock-mobile' })
const template = compileTemplate({ source: descriptor.template.content, filename, id: 'stock-mobile',
  compilerOptions: { bindingMetadata: script.bindings } })
assert.deepEqual(template.errors, [])
for (const style of descriptor.styles) {
  assert.deepEqual(compileStyle({ source: style.content, filename, id: 'stock-mobile', scoped: style.scoped }).errors, [])
}
function evaluate(source, globals = {}) {
  const output = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS,
  }, reportDiagnostics: true })
  assert.deepEqual(output.diagnostics, [])
  const exports = {}
  const names = Object.keys(globals)
  new Function('exports', 'require', ...names, output.outputText)(exports,
    (name) => name.startsWith('~/')
      ? evaluate(fs.readFileSync(path.resolve(__dirname, '..', `${name.slice(2)}.ts`), 'utf8'))
      : require(name), ...names.map((name) => globals[name]))
  return exports
}
const render = evaluate(template.code).render
function app(component) {
  return Vue.createSSRApp(component)
    .component('InformarThreemaModal', { render: () => null })
    .component('NuxtLink', { props: ['to'], setup: (props, { slots }) => () => {
      const href = typeof props.to === 'string' ? props.to
        : `${props.to.path}${props.to.query ? `?${new URLSearchParams(props.to.query)}` : ''}`
      return Vue.h('a', { href }, slots.default?.())
    } })
}
async function page(role = 'admin') {
  let instance
  let meta
  const calls = []
  const auth = Vue.reactive({ user: { role, name: 'Teste mobile', email: 'mobile@example.invalid', permissions: {} } })
  const component = evaluate(script.content, {
    definePageMeta: (value) => { meta = value },
    useAuthStore: () => auth,
    useApi: () => {
      instance = Vue.getCurrentInstance()
      return { api: async (url, options) => {
        calls.push({ url, options })
        if (url.includes('conferencia-hoje')) return { total: 1, conferido: 1, percent: 100 }
        return { data: [], total: 0 }
      } }
    },
  }).default
  component.render = render
  await renderToString(app(component))
  assert.deepEqual(meta, { middleware: ['permission'], permission: { resource: 'controle_estoque', action: 'view' } })
  const state = instance.setupState
  return { state, calls, auth, html: () => renderToString(app({ setup: () => state, render })) }
}
function section(html, label) {
  const match = html.match(new RegExp(`<section\\b[^>]*aria-label="${label}"[^>]*>([\\s\\S]*?)</section>`))
  assert.ok(match, `section ${label}`)
  return match[1]
}
function text(markup) {
  return markup.replace(/<!--[\s\S]*?-->/g, '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()
}
function tags(markup, tag) {
  return [...markup.matchAll(new RegExp(`<${tag}\\b[^>]*>([\\s\\S]*?)</${tag}>`, 'g'))].map((m) => text(m[1]))
}
// Text in a closed <details> is still emitted by SSR. Remove that whole
// subtree before checking the fields, so a collapsed-card regression fails.
function withoutDetails(markup) {
  return markup.replace(/<details\b[^>]*>[\s\S]*?<\/details>/g, '')
}
function orderCard(markup, marketplaceId) {
  const card = [...markup.matchAll(/<article\b[^>]*>([\s\S]*?)<\/article>/g)]
    .map((match) => match[1]).find((entry) => tags(entry, 'h2')[0] === marketplaceId)
  assert.ok(card, `order card ${marketplaceId}`)
  return withoutDetails(card)
}
function facts(markup) {
  return Object.fromEntries(tags(markup, 'dt').map((label, index) => [label, tags(markup, 'dd')[index]]))
}
const PRODUCT = {
  sku: 'mala.24', nome: 'Mala de viagem 24 polegadas', entradas: [{ movement_id: 'entry-1', qty: 12, obs: 'Recebido hoje' }],
  saidas: [{ movement_id: 'exit-1', qty: 3, origem: 'Pedido 101' }], saida_qty_total: 3, saida_origens: 'Pedido 101',
  saldo_fisico: 9, reserva: 2, saldo_virtual: 7, conferido: false,
}
const BASE_ORDER = {
  data: '2026-10-04', data_pedido: '2026-10-04', data_envio: '2026-10-05', loja: 'AMAZON loja teste',
  cliente: 'Cliente de teste', quantidade: 1, status: 'nao_enviado', conferido: false, observacao: null,
  bling_id: 1, etiqueta_disponivel: true, nf_caixa: false, estoque_compartilhado: false,
  etiqueta_em: '2026-10-05T12:00:00Z', etiqueta_impressa_em: null, enviado_em: null,
  ship_deadline: null, chamado_atraso: null, previsao_impressa_em: null, pede_video: false, video: null,
}
const ORDERS = [
  { ...BASE_ORDER, id: 'item-1', pedido_bling: '101', pedido_marketplace: 'AMZ-101', sku: 'mala.24', produto: 'Mala 24', quantidade: 2, observacao: 'Conferir rodinhas',
    etiqueta_impressa_em: '2026-10-05T13:30:00Z', nf_caixa: true,
    video: { link: 'https://mega.nz/file/test#fake-key', salvo_em: '2026-10-05T14:20:00Z', salvo_por: 'Operador teste' },
    chamado_atraso: { chamado_id: 'ticket-1', chamado: 'CH-42', canal: 'manual', resolvido: false, status: 'aberto', motivo: 'energia' } },
  { ...BASE_ORDER, id: 'item-2', pedido_bling: '101', pedido_marketplace: 'AMZ-101', sku: 'cadeado', produto: 'Cadeado', quantidade: 1 },
  { ...BASE_ORDER, id: 'item-3', pedido_bling: '102', pedido_marketplace: 'AMZ-102', sku: 'mala.20', produto: 'Mala 20', status: 'enviado', enviado_em: '2026-10-05T15:00:00Z' },
]
const SHIPMENT = { data: '2026-10-05', envios: 12, conferido: false, conferencia_estoque: 'parcial',
  videos: { necessarios: 2, feitos: 1, pendentes: ['101'], status: 'parcial' } }
async function run() {
  const admin = await page()
  assert.equal(admin.state.mobileTable, false, 'compact list is the default')
  admin.state.produtos = [structuredClone(PRODUCT)]
  let html = await admin.html()
  let mobile = section(html, 'Produtos em estoque')
  assert.deepEqual(tags(mobile, 'h2'), [PRODUCT.nome])
  assert.deepEqual(tags(mobile, 'dt'), ['Atual', 'Reserva', 'Disponível'])
  assert.deepEqual(tags(mobile, 'dd'), ['9', '2', '7'], 'stock counts are not mixed up')
  assert.match(mobile, /mala\.24/)
  assert.match(mobile, /<details\b/)
  assert.match(mobile, /Recebido hoje/)
  assert.match(mobile, /Pedido 101/)
  assert.match(mobile, /12 un\./)
  assert.match(mobile, /3 un\./)
  await admin.state.toggleProduto(admin.state.produtos[0])
  assert.equal(admin.state.produtos[0].conferido, true)
  const check = admin.calls.find((call) => call.url.startsWith('/api/estoque/check?'))
  const params = new URL(check.url, 'https://test.invalid').searchParams
  assert.equal(params.get('section'), 'estoque')
  assert.equal(params.get('reference_id'), PRODUCT.sku)
  assert.equal(params.get('conferido'), 'true')
  assert.equal(check.options.method, 'POST', 'card reuses the existing stock check action')

  admin.state.tab = 'pedidos'
  admin.state.pedidos = structuredClone(ORDERS)
  html = await admin.html()
  mobile = section(html, 'Pedidos por loja')
  assert.equal(tags(mobile, 'article').length, 2, 'two items from one order share a single card')
  const first = admin.state.pedidosMobile.find((group) => group.key === '101')
  assert.deepEqual(first.itens.map((item) => [item.sku, item.quantidade]), [['mala.24', 2], ['cadeado', 1]])
  assert.deepEqual(new Set(tags(mobile, 'h2')), new Set(['AMZ-101', 'AMZ-102']))
  assert.match(mobile, /Não enviado/)
  assert.match(mobile, />Enviado<\/span>/)
  assert.match(mobile, /Mala 24/)
  assert.match(mobile, /Cadeado/)
  assert.match(mobile, /2 un\./)
  assert.match(mobile, /Cliente de teste/)
  assert.match(mobile, /Conferir rodinhas/)
  assert.match(mobile, /href="\/api\/estoque\/pedidos\/101\/etiqueta"/, 'existing label URL is retained')
  const visibleFirst = orderCard(mobile, 'AMZ-101')
  assert.deepEqual(tags(visibleFirst, 'h2'), ['AMZ-101'])
  assert.match(text(visibleFirst), /Pedido no marketplace AMZ-101/)
  assert.match(text(visibleFirst), /Pedido Bling: 101/)
  assert.match(text(visibleFirst), /Cliente: Cliente de teste/)
  assert.match(text(visibleFirst), /AMAZON loja teste/)
  for (const value of ['Mala 24', 'mala.24', '2 un.', 'Cadeado', 'cadeado', '1 un.']) {
    assert.ok(text(visibleFirst).includes(value), `${value} is outside collapsed details`)
  }
  assert.deepEqual(facts(visibleFirst), {
    'Data de envio': '05/10/2026',
    'Etiqueta recebida': admin.state.etiquetaHora(first.row),
    'Etiqueta impressa': admin.state.impressaHora(first.row),
    'Envio confirmado': 'Ainda não enviado',
  }, 'all dates/timestamps are exposed, including the full shipping year')
  const visibleSent = orderCard(mobile, 'AMZ-102')
  assert.equal(facts(visibleSent)['Envio confirmado'], '05/10 12:00', 'confirmation timestamp is visible without opening details')
  assert.equal(facts(visibleSent).Chamado, '—', 'missing ticket is explicit')
  assert.match(visibleFirst, /href="\/api\/estoque\/pedidos\/101\/etiqueta"/)
  assert.match(text(visibleFirst), /Imprimir etiqueta/)
  assert.match(visibleFirst, /href="https:\/\/mega\.nz\/file\/test#fake-key"/)
  assert.match(text(visibleFirst), /Ver vídeo/)
  assert.match(text(visibleFirst), /Editar vídeo/)
  assert.ok(text(visibleFirst).includes(`Vídeo salvo em ${admin.state.fmtDataHoraBrt(first.row.video.salvo_em)} por Operador teste.`))
  assert.match(visibleFirst, /href="\/chamados\?search=101"/)
  assert.match(text(visibleFirst), /Chamado: nº CH-42/)
  assert.match(text(visibleFirst), /Motivo do chamado: queda de energia/)
  assert.match(text(visibleFirst), /NF de 100%.*dentro da caixa/)
  assert.match(text(visibleFirst), /Selecionar para impressão em lote/)
  assert.match(visibleFirst, /value="Conferir rodinhas"/, 'item observation is not hidden in details')
  assert.deepEqual(tags(visibleFirst, 'label').filter((value) => value.startsWith('Observação')),
    ['Observação · mala.24', 'Observação · cadeado'], 'each item keeps its own editable observation')
  assert.match(withoutDetails(mobile), /Selecionar todos os pedidos com etiqueta/)
  admin.state.toggleEtiquetaSel('101')
  assert.equal(admin.state.etiquetasSel.has('101'), true)
  assert.equal(admin.state.selecionadosCount, 1)
  admin.state.toggleTodasEtiquetas()
  assert.deepEqual(new Set(admin.state.etiquetasSel), new Set(['101', '102']), 'select all uses distinct orders, not item count')
  assert.equal(admin.state.todasSelecionadas, true)
  const selectedMobile = withoutDetails(section(await admin.html(), 'Pedidos por loja'))
  assert.match(selectedMobile.slice(0, selectedMobile.indexOf('<article')), /<input\b[^>]*checked/, 'select-all checkbox reflects selection')
  admin.state.toggleTodasEtiquetas()
  assert.equal(admin.state.selecionadosCount, 0, 'select all toggles off')
  admin.state.abrirVideo(first.row)
  assert.equal(admin.state.videoModal.pedido, '101', 'video editor targets the same order')
  admin.state.videoModal = null
  admin.state.search = 'cadeado'
  assert.deepEqual(admin.state.pedidosMobile.map((group) => group.itens.map((item) => item.sku)), [['cadeado']], 'cards honor the existing search')
  admin.state.search = ''
  admin.state.plataformaFilter = 'SHOPEE'
  assert.equal(admin.state.pedidosMobile.length, 0, 'cards honor marketplace filter')
  admin.state.plataformaFilter = ''

  // Other ticket causes must not be silently presented as queue delays.
  for (const [motivo, label] of [['fila', 'fila na postagem'], ['etiqueta', 'problema na emissão da etiqueta'], ['outro', 'outro (escrever o motivo)']]) {
    admin.state.pedidos[0].chamado_atraso.motivo = motivo
    const visible = orderCard(section(await admin.html(), 'Pedidos por loja'), 'AMZ-101')
    assert.ok(text(visible).includes(`Motivo do chamado: ${label}.`))
  }
  admin.state.pedidos = [{ ...BASE_ORDER, id: 'no-label', pedido_bling: '103', pedido_marketplace: null,
    produto: 'Produto de previsão', sku: 'prev.sp', status: 'previsao', etiqueta_disponivel: false,
    etiqueta_em: null, previsao_impressa_em: '2026-10-05T11:00:00Z', pede_video: true }]
  const missing = orderCard(section(await admin.html(), 'Pedidos por loja'), '—')
  assert.match(text(missing), /Pedido no marketplace —/)
  assert.match(text(missing), /Pedido Bling: 103/)
  assert.equal(facts(missing)['Arquivo da etiqueta'], 'Ainda não disponível')
  assert.equal(facts(missing)['Previsão impressa'], admin.state.previsaoImpressaHora(admin.state.pedidos[0]))
  assert.match(text(missing), /Vídeo obrigatório/)
  assert.match(text(missing), /Selecionar previsão para impressão/)
  assert.doesNotMatch(missing, /Imprimir etiqueta/)

  admin.state.tab = 'envios'
  admin.state.envios = { items: [structuredClone(SHIPMENT)], total: 0, total_envios: 12 }
  mobile = section(await admin.html(), 'Envios por dia')
  assert.match(mobile, /12 envio\(s\)/)
  assert.deepEqual(tags(mobile, 'dd'), ['Parcial', '1/2 feitos'])
  assert.match(mobile, /Pedidos com vídeo pendente/)
  assert.match(mobile, />101<\/li>/)
  assert.match(mobile, /type="checkbox"/, 'admin can confirm shipments')

  const reader = await page('user')
  reader.state.tab = 'pedidos'
  reader.state.pedidos = structuredClone(ORDERS)
  reader.state.videosPendentes = [{ pedido_bling: '999', pedido_marketplace: 'P-999', loja: 'Loja teste', cliente: 'Cliente',
    itens: [{ sku: 'mala', produto: 'Mala', quantidade: 1 }], solicitado_em: null, solicitado_por: 'Teste',
    refazer_motivo: null, etiqueta_disponivel: false, trava: true }]
  html = await reader.html()
  assert.doesNotMatch(html, /aria-label="Pedidos por loja"/, 'mobile cannot bypass the pending video lock')
  assert.match(html, /Envie o link do vídeo do pedido abaixo/)
  reader.state.tab = 'envios'
  reader.state.envios = { items: [structuredClone(SHIPMENT)], total: 0, total_envios: 12 }
  html = await reader.html()
  assert.doesNotMatch(html, /aria-label="Envios por dia"/, 'mobile cannot bypass stock conference lock')
  reader.state.conferenciaHoje = { total: 1, conferido: 1, percent: 100 }
  mobile = section(await reader.html(), 'Envios por dia')
  assert.doesNotMatch(mobile, /type="checkbox"/, 'reader cannot confirm shipments')
  assert.match(mobile, /Envios não conferidos/)

  admin.state.tab = 'estoque'
  admin.state.produtos = []
  assert.match(section(await admin.html(), 'Produtos em estoque'), /Nenhum produto/)
  admin.state.loading = true
  assert.match(section(await admin.html(), 'Produtos em estoque'), /Carregando estoque/)
  admin.state.tab = 'pedidos'
  admin.state.pedidos = []
  assert.match(section(await admin.html(), 'Pedidos por loja'), /Carregando pedidos/)
  admin.state.loading = false
  assert.match(section(await admin.html(), 'Pedidos por loja'), /Nenhum pedido/)
}
run().then(() => console.log('PASS: mobile stock/order/shipment cards preserve values, grouping, filters and access; all order fields/actions exposed with full dates, selection and metadata; empty/loading states'))
  .catch((error) => { console.error(error); process.exitCode = 1 })
