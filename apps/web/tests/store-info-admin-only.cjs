// Offline regression: actual Vue page and handlers, no real accounts or mutations.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { renderToString } = require('vue/server-renderer')
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')
const filename = path.resolve(__dirname, '../pages/store-info.vue')
const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
assert.deepEqual(errors, [])
const compiled = compileScript(descriptor, { id: 'store-admin-only' })
const template = compileTemplate({ source: descriptor.template.content, filename, id: 'store-admin-only', compilerOptions: { bindingMetadata: compiled.bindings } })
assert.deepEqual(template.errors, [])
const transpile = (value) => ts.transpileModule(value, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText
const store = {
  id: 'store1', platform: 'mercadolivre', account_name: 'Loja Exemplo', user_id: 'owner1', departments: ['celular', 'eletro'], has_password: true,
  email: 'loja@example.com', phone: '11999999999', cpf_name: 'Responsável', cnpj: '11222333000100', server: 'Servidor',
  has_pricing: true, has_integration: true, integration_id: 'integration1', nf_faturador_id: 'nf1', nf_faturador_por_tipo: { celular: 'nf1', eletro: 'nf2' },
  nf_faturador_produto_id: null, nf_etiqueta_id: null, nf_impressao_id: null, etiqueta_horarios: '10:00, 15:00', etiqueta_sabado_horario: '11:00', etiqueta_sabado_tags: 'ci',
  shipping_address: 'Endereço completo', return_address: 'Endereço de devolução', observation: 'Observação', bling_store_id: 'bling1',
  upseseller: true, duoker: false, uf_restrictions: ['AC', 'AM'], excecoes: [{ tipo: 'sku', termos: ['modelo-original'] }], commercial_team: 1,
}
async function mount(admin = false) {
  const calls = [], confirmations = [], copied = []
  const auth = Vue.reactive({ user: { role: admin ? 'admin' : 'user', permissions: { lojas_info: { view: true, edit: true, delete: true } } } })
  let state
  const globals = {
    ref: Vue.ref, reactive: Vue.reactive, computed: Vue.computed, watch: Vue.watch, nextTick: Vue.nextTick,
    onMounted: () => {}, onBeforeUnmount: () => {}, definePageMeta: () => {},
    useIsAdmin: () => Vue.computed(() => auth.user.role === 'admin'), useAuthStore: () => auth,
    // Legacy explicit permissions must never override the admin-only rule.
    useCan: () => Vue.computed(() => true),
    useApi: () => ({ api: async (url, options = {}) => {
      calls.push({ url, options })
      if (url.endsWith('/password')) return { password: 'local-test-password' }
      if (!options.method && url.startsWith('/api/pricing/store-info')) return [structuredClone(store)]
      if (url === '/api/integrations') return [{ id: 'integration1', name: 'Integração Exemplo', platform: 'ml' }]
      if (url.includes('/nf-cadastro')) return [{ id: 'nf1', nome: 'Faturador 1', plataforma: 'ML', tipo: 'Padrão' }, { id: 'nf2', nome: 'Faturador 2' }]
      return { ...structuredClone(store), ...(options.body || {}) }
    } }),
    confirm: (message) => { confirmations.push(message); return true },
    navigator: { clipboard: { writeText: async (value) => copied.push(value) } },
    document: { addEventListener: () => {}, removeEventListener: () => {}, getElementById: () => null },
    setTimeout: () => 1,
  }
  const requireMock = (name) => {
    if (name === 'vue') return { ...Vue, withAsyncContext: (fn) => [fn(), () => {}] }
    if (name === '~/lib/navGroups') return { TABS_CADASTROS: [] }
    if (name === 'lucide-vue-next') return new Proxy({}, { get: () => ({ render: () => Vue.h('i') }) })
    return require(name)
  }
  const module = { exports: {} }
  new Function('require', 'module', 'exports', ...Object.keys(globals), transpile(compiled.content))(requireMock, module, module.exports, ...Object.values(globals))
  const raw = await module.exports.default.setup({}, { expose: () => {} })
  state = Vue.proxyRefs(raw)
  async function render() {
    const tmod = { exports: {} }
    new Function('require', 'module', 'exports', transpile(template.code))(requireMock, tmod, tmod.exports)
    const component = { ...module.exports.default, setup: () => raw, render: tmod.exports.render }
    const app = Vue.createSSRApp(component)
    app.component('RouteTabs', { render: () => Vue.h('nav') })
    app.component('PageHeader', { props: ['title', 'description'], setup: (props, { slots }) => () => Vue.h('header', [Vue.h('h1', props.title), Vue.h('p', props.description), slots.actions?.()]) })
    app.component('Button', { props: ['disabled', 'size', 'variant'], setup: (props, { slots }) => () => Vue.h('button', { disabled: props.disabled }, slots.default?.()) })
    return renderToString(app)
  }
  return { state, auth, calls, confirmations, copied, render }
}
async function run() {
  const user = await mount(false)
  const s = user.state, row = s.items[0]
  assert.equal(s.canEdit, false)
  assert.equal(s.canDelete, false)
  await s.startEdit(row, 'password')
  assert.equal(s.editing, null)
  s.editing = { id: row.id, field: 'password' }; s.editValue = 'forbidden'; s.editOriginal = 'before'
  await s.commitEdit()
  assert.equal(s.editing, null)
  const input = { checked: false }
  const operations = [
    () => s.openStoreTable(row), () => s.openAdd(), () => s.submitNew(), () => s.remove(row), () => s.archiveRow(row), () => s.unarchiveRow(row),
    () => s.attachIntegration(row, 'other'), () => s.unlinkIntegration(row), () => s.updateField(row, 'email', 'changed@example.com'),
    () => s.toggleUfPopover(row.id), () => s.toggleUf(row, 'SP', true), () => s.cycleTriBool(row, 'upseseller'),
    () => s.toggleHorPopover(row.id), () => s.setHorarios(row, ['13:00']), () => s.addHorario(row), () => s.removeHorario(row, '10:00'),
    () => s.toggleTagSabado(row, 'sp'), () => s.setHoraSabado(row, '14:00'), () => s.toggleExcPopover(row.id),
    () => s.addExcecao(row), () => s.removeExcecao(row, 0), () => s.openTipoPopover(row.id), () => s.openFatPopover(row.id),
    () => s.patchFaturador(row, 'key', { nf_faturador_id: 'nf2' }), () => s.setFatPorTipo(row, 'celular', 'nf2'), () => s.setFatPadrao(row, 'nf2'),
    () => s.toggleDepartment(row, 'celular', input),
  ]
  s.novoHorario = '12:00'; s.excDraft.tipo = 'sku'; s.excDraft.termos = 'alteração'; s.newRow.account_name = 'Proibida'
  for (const operation of operations) await operation()
  assert.equal(user.calls.filter((call) => call.options.method).length, 0, 'every mutation entry point blocks a non-admin with legacy edit/delete permissions')
  assert.equal(user.confirmations.length, 0)
  assert.equal(s.showAdd, false)
  assert.equal(input.checked, true, 'department control restores its displayed value')
  assert.equal(s.openUfRowId, null); assert.equal(s.openHorRowId, null); assert.equal(s.openExcRowId, null)
  assert.equal(s.tipoPopoverFor, null); assert.equal(s.fatPopoverFor, null)

  await s.toggleReveal(row.id)
  assert.equal(s.revealedPasswords.get(row.id), 'local-test-password')
  await s.copyPassword(row.id); await s.copyText(row.email)
  assert.deepEqual(user.copied, ['local-test-password', 'loja@example.com'])
  assert.equal(user.calls.filter((call) => call.options.method).length, 0, 'reading and clipboard access remain read-only')

  // Stale editor/popover state must never resurrect writable controls.
  s.editing = { id: row.id, field: 'password' }; s.showAdd = true
  s.openUfRowId = row.id; s.openHorRowId = row.id; s.openExcRowId = row.id
  s.tipoPopoverFor = row.id; s.fatPopoverFor = row.id
  let html = await user.render()
  assert.doesNotMatch(html, /Nova loja|Editar na tabela|Arquivar|Ativar loja|Excluir|Desvincular|Vincular departamentos|adicionar regra|clique em qualquer célula para editar/)
  assert.doesNotMatch(html, /type="checkbox"|type="time"|id="new-store-account"|ring-2 ring-blue-500 ring-inset bg-background/)
  assert.match(html, /Mostrar senha|Ocultar senha/)
  assert.match(html, /Copiar senha|Copiada/)
  assert.match(html, /local-test-password/)
  assert.match(html, /AC, AM/)
  assert.match(html, /SKU modelo-original/)
  assert.match(html, /loja@example.com/)
  s.mobileTableView = true
  html = await user.render()
  assert.doesNotMatch(html, /colunas de edição|clique em qualquer célula para editar/)

  const admin = await mount(true), a = admin.state, adminRow = a.items[0]
  assert.equal(a.canEdit, true); assert.equal(a.canDelete, true)
  a.mobileTableView = true
  const adminHtml = await admin.render()
  assert.match(adminHtml, /Nova loja/); assert.match(adminHtml, /Excluir/); assert.match(adminHtml, /Arquivar/)
  await a.startEdit(adminRow, 'email'); a.editValue = 'admin-changed@example.com'; await a.commitEdit()
  assert.ok(admin.calls.some((call) => call.options.method === 'PATCH' && call.options.body.email === 'admin-changed@example.com'))
  a.openAdd(); assert.equal(a.showAdd, true)
  a.openUfRowId = adminRow.id; a.openHorRowId = adminRow.id; a.openExcRowId = adminRow.id
  a.tipoPopoverFor = adminRow.id; a.fatPopoverFor = adminRow.id
  await a.startEdit(adminRow, 'password')
  const before = admin.calls.filter((call) => call.options.method).length
  admin.auth.user.role = 'user'
  assert.equal(a.editing, null, 'revocation closes an already-open password editor')
  assert.equal(a.showAdd, false)
  assert.equal(a.openUfRowId, null); assert.equal(a.openHorRowId, null); assert.equal(a.openExcRowId, null)
  assert.equal(a.tipoPopoverFor, null); assert.equal(a.fatPopoverFor, null)
  await a.commitEdit(); await a.archiveRow(adminRow); await a.remove(adminRow)
  assert.equal(admin.calls.filter((call) => call.options.method).length, before)
  console.log('store-info admin-only: all mutation handlers guarded; desktop/mobile read+copy preserved; revoked role closes editors.')
}
run().catch((error) => { console.error(error); process.exitCode = 1 })
