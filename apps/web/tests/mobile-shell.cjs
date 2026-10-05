// Run from apps/web: node tests/mobile-shell.cjs
// Execute the compiled Vue setup functions, mocking only browser/Nuxt boundaries.
// Real viewport sizing, focus/inert integration and visuals are checked separately
// in the local browser; these checks protect the drawer's interaction behavior.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')

const root = path.resolve(__dirname, '..')
const route = Vue.reactive({ path: '/faturamento', fullPath: '/faturamento' })
const auth = Vue.reactive({
  isAdmin: true,
  user: { id: 'preview', role: 'admin', name: 'Preview', permissions: {}, stock_tags: [] },
})
let mounted = []
let disposers = []
let mediaListeners = []
const scrolled = []
const scopes = []
const vue = {
  ...Vue,
  onMounted: (fn) => mounted.push(fn),
  onBeforeUnmount: (fn) => disposers.push(fn),
  onScopeDispose: (fn) => disposers.push(fn),
}

class Element {
  isConnected = true
  getClientRects() { return [{}] }
  focus() { documentMock.activeElement = this }
}
const trigger = Vue.markRaw(new Element())
const close = Vue.markRaw(new Element())
const first = Vue.markRaw(new Element())
const last = Vue.markRaw(new Element())
const originalBodyStyle = { position: '', top: '', left: '', right: '', overflow: 'auto' }
const documentMock = { activeElement: trigger, body: { style: { ...originalBodyStyle } } }
const media = {
  matches: true,
  addEventListener: (_, fn) => mediaListeners.push(fn),
  removeEventListener: (_, fn) => { mediaListeners = mediaListeners.filter((value) => value !== fn) },
}
const windowMock = {
  scrollY: 417,
  matchMedia: () => media,
  scrollTo: (value) => scrolled.push(value),
}
const storage = new Map()
const globals = {
  HTMLElement: Element,
  document: documentMock,
  window: windowMock,
  localStorage: { getItem: (key) => storage.get(key), setItem: (key, value) => storage.set(key, value) },
  useRoute: () => route,
  useAuthStore: () => auth,
  useRuntimeConfig: () => ({ public: { enableMarketing: false } }),
  useApi: () => ({ api: async () => ({ count: 0 }) }),
}

function evaluate(content, client = false) {
  const output = ts.transpileModule(content.replaceAll('import.meta.client', String(client)), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
    reportDiagnostics: true,
  })
  assert.deepEqual(output.diagnostics, [])
  const exports = {}
  new Function('exports', 'require', ...Object.keys(globals), output.outputText)(
    exports,
    (name) => {
      if (name === 'vue') return vue
      if (name.startsWith('~/')) {
        return evaluate(fs.readFileSync(path.join(root, `${name.slice(2)}.ts`), 'utf8'))
      }
      return require(name)
    },
    ...Object.values(globals),
  )
  return exports
}

function compile(file) {
  const filename = path.join(root, file)
  const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
  assert.deepEqual(errors, [])
  const script = compileScript(descriptor, { id: file })
  const template = compileTemplate({
    source: descriptor.template.content,
    filename,
    id: file,
    compilerOptions: { bindingMetadata: script.bindings },
  })
  assert.deepEqual(template.errors, [])
  return script.content
}

function setup(file, props = {}, emit = () => {}, client = false) {
  const scope = Vue.effectScope()
  scopes.push(scope)
  return scope.run(() => evaluate(compile(file), client).default.setup(props, { expose: () => {}, emit }))
}
async function flush() {
  await Vue.nextTick()
  await Vue.nextTick()
}

async function run() {
  compile('components/AppTopbar.vue')
  const events = []
  const props = Vue.reactive({ collapsed: true, mobile: true, mobileOpen: false })
  const sidebar = setup('components/AppSidebar.vue', props, (event) => events.push(event))
  assert.equal(sidebar.compact.value, false, 'mobile labels remain visible despite saved desktop collapse')
  const mobileItems = sidebar.visibleSections.value.flatMap((section) => section.items.map((item) => item.to))

  sidebar.closeButton.value = close
  sidebar.sidebarElement.value = { querySelectorAll: () => [first, last] }
  props.mobileOpen = true
  await flush()
  assert.equal(documentMock.activeElement, close, 'opening focuses close control')

  let prevented = false
  documentMock.activeElement = last
  sidebar.onMenuKeydown({ key: 'Tab', shiftKey: false, preventDefault() { prevented = true } })
  assert.equal(documentMock.activeElement, first, 'Tab wraps at last menu item')
  assert.equal(prevented, true)
  documentMock.activeElement = first
  sidebar.onMenuKeydown({ key: 'Tab', shiftKey: true, preventDefault() {} })
  assert.equal(documentMock.activeElement, last, 'Shift+Tab wraps at first menu item')
  sidebar.onMenuKeydown({ key: 'Escape', preventDefault() {}, stopPropagation() {} })
  assert.deepEqual(events, ['close'], 'Escape requests close')

  props.mobileOpen = false
  await flush()
  assert.equal(documentMock.activeElement, trigger, 'closing restores trigger focus')
  sidebar.onMenuKeydown({ key: 'Escape', preventDefault() {}, stopPropagation() {} })
  assert.deepEqual(events, ['close'], 'closed desktop navigation does not capture Escape')
  props.mobile = false
  assert.equal(sidebar.compact.value, true, 'desktop collapse preference retained')
  assert.deepEqual(
    sidebar.visibleSections.value.flatMap((section) => section.items.map((item) => item.to)),
    mobileItems,
    'mobile navigation uses the same visible entries as desktop',
  )
  props.mobile = true
  auth.isAdmin = false
  auth.user.role = 'user'
  auth.user.stock_tags = ['sp']
  auth.user.permissions = { controle_estoque: { view: true } }
  assert.deepEqual(
    sidebar.visibleSections.value.flatMap((section) => section.items.map((item) => item.to)),
    ['/controle-estoque'],
    'restricted stock operator keeps existing navigation scope on mobile',
  )

  // A stored collapsed preference must not change the client's initial markup
  // before hydration; SSR has no access to this browser-local setting.
  storage.set('sidebar:collapsed', '1')
  mounted = []
  disposers = []
  const serverLayout = setup('layouts/default.vue', {}, () => {}, false)
  assert.equal(serverLayout.collapsed.value, false, 'server initially renders expanded navigation')
  mounted = []
  disposers = []
  const layout = setup('layouts/default.vue', {}, () => {}, true)
  assert.equal(layout.collapsed.value, serverLayout.collapsed.value,
    'client initial navigation matches server even with a stored collapsed preference')
  mounted.forEach((fn) => fn())
  assert.equal(layout.collapsed.value, true, 'stored preference restored after mount')
  assert.equal(layout.isMobile.value, true, 'viewport detected')
  layout.mobileMenuOpen.value = true
  await flush()
  assert.equal(documentMock.body.style.position, 'fixed')
  assert.equal(documentMock.body.style.top, '-417px', 'scroll preserved while drawer open')

  route.fullPath = '/store-info'
  await flush()
  assert.equal(layout.mobileMenuOpen.value, false, 'route navigation closes drawer')
  assert.deepEqual(documentMock.body.style, originalBodyStyle, 'all previous inline body styles restored')
  assert.equal(scrolled.at(-1).top, 417, 'scroll position restored')

  layout.mobileMenuOpen.value = true
  await flush()
  media.matches = false
  mediaListeners.forEach((fn) => fn())
  await flush()
  assert.equal(layout.mobileMenuOpen.value, false, 'desktop resize closes drawer')
  assert.equal(layout.isMobile.value, false)
  layout.toggle()
  assert.equal(storage.get('sidebar:collapsed'), '0', 'updated desktop preference saved')

  // Leaving the layout while the drawer is open must not freeze the next page.
  media.matches = true
  mediaListeners.forEach((fn) => fn())
  layout.mobileMenuOpen.value = true
  await flush()
  disposers.forEach((fn) => fn())
  assert.deepEqual(documentMock.body.style, originalBodyStyle, 'unmount restores scroll styles')
  assert.equal(mediaListeners.length, 0, 'media listener removed on dispose')
}

run().then(() => {
  console.log('PASS: compiled mobile shell — focus entry/restore, Tab/Shift+Tab trap, Escape, route close, scroll lock/restore, resize, hydration-safe desktop preference, existing navigation scope and cleanup')
}).catch((error) => {
  console.error(error)
  process.exitCode = 1
}).finally(() => scopes.forEach((scope) => scope.stop()))
