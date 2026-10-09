// Offline real component setup with a fake clock and controllable API promises.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { parse, compileScript } = require('vue/compiler-sfc')
const filename = path.resolve(__dirname, '../components/AtendimentoMail.vue')
const { descriptor } = parse(fs.readFileSync(filename, 'utf8'), { filename })
const script = ts.transpileModule(compileScript(descriptor, { id: 'polling' }).content, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText
const settle = async () => { for (let i = 0; i < 35; i++) await Promise.resolve(); await Vue.nextTick() }
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }
const box = (id = 'box1') => ({ id, label: id, aliases: ['support@example.com'], address: 'support@example.com', state: 'online', can_send: true, send_enabled: true })
const item = (id) => ({ id, recebido_em: '2026-10-09T12:00:00Z', direcao: 'inbound', tem_anexos: false, de: 'customer@example.com', de_nome: 'Customer', assunto: id, assunto_oculto: false, pasta: { id: 's1', nome: 'Entrada' }, selo: { tipo: 'loja', rotulo: 'Loja A' } })
const page = (ids, next = null) => ({ itens: ids.map(item), proximo: next, faltam: 0 })
const body = (id) => ({ id, text: 'Original body', html: '<p>Original body</p>', attachments: [], outbox: [], reply: { from_address: 'support@example.com', to: 'customer@example.com', can_reply: true, send_ready: true } })
function events(extra = {}) {
  const listeners = new Map()
  return { ...extra, listeners, addEventListener: (type, fn) => listeners.set(type, fn), removeEventListener: (type, fn) => { if (listeners.get(type) === fn) listeners.delete(type) }, fire: (type) => listeners.get(type)?.() }
}
function mount() {
  let now = 1_000_000, sequence = 0
  const timers = new Map(), mounted = [], unmounted = [], calls = []
  const document = events({ hidden: false }), window = events({ confirm: () => true })
  const data = { first: page(['m1'], null), folders: { pastas: [{ id: 's1', nome: 'Entrada' }], lojas: [{ chave: 'store1' }], faltam: 0 }, mailboxes: [box(), box('box2')], detail: body('m1'), intercept: null }
  const api = async (url, opts = {}) => {
    calls.push({ url, opts })
    const intercepted = data.intercept?.(url, opts)
    if (intercepted !== undefined) return intercepted
    if (url === '/api/mail/mailboxes') return { items: structuredClone(data.mailboxes) }
    if (url.includes('/lista?')) return structuredClone(data.first)
    if (url.includes('/pastas')) return structuredClone(data.folders)
    if (url.startsWith('/api/mail/messages/')) return { ...structuredClone(data.detail), id: url.split('/').at(-1) }
    throw Error(`Unexpected ${url}`)
  }
  const globals = {
    ref: Vue.ref, computed: Vue.computed, reactive: Vue.reactive, watch: Vue.watch,
    onMounted: (fn) => mounted.push(fn), onBeforeUnmount: (fn) => unmounted.push(fn),
    useApi: () => ({ api, url: (value) => value }), document, window,
    Date: class extends Date { static now() { return now } }, crypto: { randomUUID: () => 'request1' },
    setTimeout: (fn, ms) => { const id = ++sequence; timers.set(id, { fn, at: now + ms }); return id }, clearTimeout: (id) => timers.delete(id),
  }
  const mod = { exports: {} }
  const requiring = (name) => name === 'lucide-vue-next' ? {} : require(name)
  new Function('require', 'module', 'exports', ...Object.keys(globals), script)(requiring, mod, mod.exports, ...Object.values(globals))
  const scope = Vue.effectScope()
  const state = Vue.proxyRefs(scope.run(() => mod.exports.default.setup(Vue.reactive({ isAdmin: true }), { expose: () => {}, emit: () => {} })))
  return { state, data, calls, timers, document, window,
    start: async () => { for (const fn of mounted) await fn(); await settle() },
    stop: () => { unmounted.forEach((fn) => fn()); scope.stop() },
    async advance(ms) {
      now += ms
      for (const [id, timer] of [...timers]) if (timer.at <= now) { timers.delete(id); timer.fn() }
      await settle()
    },
    count: (fragment) => calls.filter((call) => call.url.includes(fragment)).length,
  }
}
async function run() {
  {
    const m = mount(); await m.start(); await m.state.openMessage('m1')
    m.state.draft = 'Keep my unsent answer'; m.state.fromAddress = 'alias@example.com'; m.state.draftRequestId = 'keep-request'
    const original = m.state.detail
    const detailReads = m.count('/api/mail/messages/'), metadataReads = m.count('/api/mail/mailboxes') - m.count('/lista?') - m.count('/pastas')
    m.data.first = page(['new-code', 'm1'])
    await m.advance(5_000)
    assert.equal(m.state.messages[0].id, 'new-code', 'new code appears after 5 seconds')
    assert.equal(m.state.detail, original, 'body/iframe unchanged at 5 seconds')
    assert.equal(m.count('/api/mail/messages/'), detailReads)
    for (let i = 0; i < 5; i++) await m.advance(5_000)
    assert.equal(m.count('/api/mail/messages/'), detailReads + 1, 'detail still refreshes every 30 seconds')
    assert.equal(m.calls.filter((x) => x.url === '/api/mail/mailboxes').length, metadataReads + 1)
    assert.equal(m.state.draft, 'Keep my unsent answer'); assert.equal(m.state.fromAddress, 'alias@example.com'); assert.equal(m.state.draftRequestId, 'keep-request')
    assert.equal(m.state.detail.id, 'm1'); assert.equal(m.state.detail.reply.to, 'customer@example.com')
    m.data.detail.outbox = [{ status: 'queued' }]; m.state.detail.outbox = [{ status: 'queued' }]
    await m.advance(5_000)
    assert.equal(m.count('/api/mail/messages/'), detailReads + 2, 'pending reply refreshes at 5 seconds')
    assert.equal(m.calls.filter((x) => x.opts.method).length, 0, 'poll never sends or changes anything')
    m.stop()
  }
  {
    const m = mount(); await m.start(); await m.state.openMessage('m1')
    const waiting = deferred(); let held = false
    m.data.intercept = (url) => { if (url.includes('/lista?') && !held) { held = true; return waiting.promise } }
    const before = m.count('/lista?'); await m.advance(5_000)
    m.window.fire('focus'); m.document.fire('visibilitychange'); const manual = m.state.refresh()
    await m.advance(60_000)
    assert.equal(m.count('/lista?'), before + 1, 'timer/resume/manual share in-flight refresh')
    waiting.resolve(page(['fresh', 'm1'])); await manual; await settle()
    assert.equal(m.state.messages[0].id, 'm1', 'queued full refresh completes against latest API page')
    assert.equal(m.count('/api/mail/messages/'), 2, 'manual refresh is honored during a poll')
    assert.equal(m.timers.size, 1, 'one next timer despite two resume events')
    m.stop()
  }
  {
    const m = mount(); m.data.first = page(['m1', 'm2'], 'cursor-old'); await m.start()
    const waiting = deferred()
    m.data.intercept = (url) => url.includes('antes=cursor-old') ? waiting.promise : undefined
    const next = m.state.loadMessages(true); await settle()
    await m.advance(5_000)
    assert.equal(m.count('/lista?'), 2, 'poll waits for load-more instead of interrupting it')
    waiting.resolve(page(['m3', 'm4'], 'cursor-last')); await next; await settle()
    assert.deepEqual(m.state.messages.map((x) => x.id), ['m1', 'm2', 'm3', 'm4'])
    assert.equal(m.state.nextCursor, 'cursor-last')
    m.data.first = page(['fresh', 'm1'], 'cursor-new'); await m.advance(5_000)
    assert.deepEqual(m.state.messages.map((x) => x.id), ['fresh', 'm1', 'm2', 'm3', 'm4'])
    assert.equal(m.state.nextCursor, 'cursor-last', 'overlapping head preserves older pages/cursor')
    m.data.first = page(['new-a', 'new-b'], 'cursor-gap'); await m.advance(5_000)
    assert.deepEqual(m.state.messages.map((x) => x.id), ['new-a', 'new-b'])
    assert.equal(m.state.nextCursor, 'cursor-gap', 'no overlap resets cursor without skipping the gap')
    m.stop()
  }
  {
    const m = mount(); await m.start()
    m.state.chooseFilter('folder', 's1'); await settle(); m.state.chooseFilter('store', 'store1'); await settle()
    await m.advance(5_000)
    assert.match(m.calls.filter((x) => x.url.includes('/lista?')).at(-1).url, /pasta=s1&loja=store1/)
    const calls = m.calls.length
    m.document.hidden = true; m.document.fire('visibilitychange'); await m.advance(60_000)
    assert.equal(m.calls.length, calls); assert.equal(m.timers.size, 0)
    m.document.hidden = false; m.document.fire('visibilitychange'); await settle()
    assert.ok(m.calls.length > calls, 'returning to the tab refreshes immediately')
    const afterVisible = m.count('/lista?'); m.window.fire('focus'); await settle()
    assert.equal(m.count('/lista?'), afterVisible + 1)
    m.stop(); assert.equal(m.timers.size, 0); assert.equal(m.document.listeners.size, 0); assert.equal(m.window.listeners.size, 0)
    const afterStop = m.calls.length; await m.advance(60_000); assert.equal(m.calls.length, afterStop)
  }
  {
    const m = mount(), waiting = deferred()
    m.data.intercept = (url) => url === '/api/mail/mailboxes' ? waiting.promise : undefined
    const start = m.start(); await settle(); m.stop(); waiting.resolve({ items: [box()] }); await start
    assert.equal(m.calls.length, 1); assert.equal(m.state.mailboxes.length, 0); assert.equal(m.timers.size, 0)
  }
  {
    const m = mount(); await m.start(); await m.state.openMessage('m1')
    const listWait = deferred(), detailWait = deferred()
    m.data.intercept = (url) => url.includes('/box1/lista?') ? listWait.promise : url === '/api/mail/messages/stale' ? detailWait.promise : undefined
    await m.advance(5_000); const opening = m.state.openMessage('stale'); await settle()
    m.state.mailboxId = 'box2'; await settle(); listWait.resolve(page(['wrong-box'])); detailWait.resolve(body('stale'))
    await opening; await settle()
    assert.equal(m.state.mailboxId, 'box2'); assert.equal(m.state.detail, null)
    assert.deepEqual(m.state.messages.map((x) => x.id), ['m1'], 'old mailbox result cannot reappear')
    const delayed = deferred(); m.data.intercept = (url) => url.includes('/lista?') ? delayed.promise : undefined
    await m.advance(5_000); m.stop(); delayed.resolve(page(['after-unmount'])); await settle()
    assert.deepEqual(m.state.messages.map((x) => x.id), ['m1'])
  }
  for (const route of ['/lista?', '/pastas']) {
    const m = mount(); await m.start()
    const waiting = deferred(); let held = false
    m.data.intercept = (url) => { if (url.includes(route) && !held) { held = true; return waiting.promise } }
    const oldRead = m.state.loadList(); await settle()
    m.data.first = page(['current-selection'])
    m.state.chooseFilter('folder', 's1'); await settle()
    m.state.chooseFilter('folder', null); await settle()
    waiting.resolve(route === '/lista?' ? page(['stale-selection']) : { pastas: [{ id: 'stale-folder' }], lojas: [], faltam: 999 })
    await oldRead; await settle()
    assert.deepEqual(m.state.messages.map((x) => x.id), ['current-selection'], `${route}: A→B→A must fetch the current selection`)
    assert.equal(m.state.loading, false)
    assert.equal(m.state.folders.some((x) => x.id === 'stale-folder'), false)
    assert.equal(m.state.organizing, 0)
    m.stop()
  }
  for (const status of [undefined, 0, 500, 504]) {
    const m = mount(); await m.start()
    let fail = true
    m.data.intercept = (url) => {
      if (fail && url.includes('/lista?')) { fail = false; return Promise.reject(Object.assign(new Error('Timeout'), { status })) }
    }
    await m.advance(5_000)
    assert.equal(m.state.loading, false, 'failed read releases the loading gate')
    assert.equal(m.count('/messages?'), 0, `failure ${status} must not reveal subjects through legacy fallback`)
    assert.equal(m.state.messages[0].id, 'm1', 'failed poll preserves previous safe list')
    assert.ok(m.state.error)
    m.data.first = page(['recovered']); await m.advance(5_000)
    assert.equal(m.state.messages[0].id, 'recovered', 'next poll resumes after a failed read')
    assert.ok(m.calls.every((x) => x.opts.timeout === 15_000 && x.opts.retry === 0), 'all reads are bounded and never retried in parallel')
    m.stop()
  }
  console.log('Mail polling tests passed: 5s arrivals, 30s detail, serialized reads, drafts, cursors, filters, visibility and stale responses.')
}
run().catch((error) => { console.error(error); process.exitCode = 1 })
