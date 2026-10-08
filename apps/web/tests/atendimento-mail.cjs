// Offline component behavior: no API network, mailbox credentials or real email.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { renderToString } = require('vue/server-renderer')
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')

const filename = path.resolve(__dirname, '../components/AtendimentoMail.vue')
const source = fs.readFileSync(filename, 'utf8')
const { descriptor, errors } = parse(source, { filename })
assert.deepEqual(errors, [])
const script = compileScript(descriptor, { id: 'mail-test' })
const template = compileTemplate({ source: descriptor.template.content, filename, id: 'mail-test', compilerOptions: { bindingMetadata: script.bindings } })
assert.deepEqual(template.errors, [])
const transpile = (input) => ts.transpileModule(input, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText
const mailbox = { id: 'box1', label: 'Private', address: 'owner@example.com', aliases: ['owner@example.com', 'support@example.com'], state: 'online', send_enabled: true, can_send: true, last_sync_at: null }
const incoming = { id: 'message1', mailbox_id: 'box1', subject: '<img src=x onerror=alert(1)>', from_address: 'customer@example.com', from_name: 'Customer', to: ['support@example.com'], cc: [], reply_to: 'reply@example.com', received_at: '2026-10-08T12:00:00Z', direction: 'inbound', folder: 'INBOX', text: '<script>alert("escaped")</script>', attachments: [{ id: 'file1', filename: 'received.txt', size: 7 }], reply: { to: 'reply@example.com', from_address: 'support@example.com', can_reply: true, send_ready: true }, outbox: [] }

function mount({ allowed = true, failOnce = false } = {}) {
  const calls = [], confirmations = []
  let fail = failOnce
  const callbacks = []
  const g = {
    ref: Vue.ref, computed: Vue.computed, reactive: Vue.reactive, watch: Vue.watch,
    onMounted: (callback) => callbacks.push(callback), onBeforeUnmount: () => {},
    useApi: () => ({ url: (value) => value, api: async (url, options = {}) => {
      calls.push({ url, options })
      if (options.method === 'POST' && url.endsWith('/reply')) {
        if (fail) { fail = false; throw new Error('simulated network failure') }
        return { id: 'job1', status: 'queued' }
      }
      if (url === '/api/mail/mailboxes') return { items: [{ ...mailbox }] }
      if (url.includes('/messages?')) return { items: [{ ...incoming }], more: false }
      if (url === '/api/mail/messages/message1') return structuredClone(incoming)
      throw new Error(`Unexpected request: ${url}`)
    } }),
    crypto: { randomUUID: () => 'f093d57f-065b-4914-90d6-1aebebdeed5e' },
    window: { confirm: (text) => { confirmations.push(text); return allowed } },
    document: { hidden: false }, setInterval: () => 1, clearInterval: () => {},
  }
  const requiring = (name) => name === 'lucide-vue-next' ? new Proxy({}, { get: () => ({ render: () => Vue.h('i') }) }) : require(name)
  const module = { exports: {} }
  const names = Object.keys(g)
  new Function('require', 'module', 'exports', ...names, transpile(script.content))(requiring, module, module.exports, ...names.map((name) => g[name]))
  const component = module.exports.default
  const state = Vue.proxyRefs(component.setup(Vue.reactive({ isAdmin: true }), { expose: () => {} }))
  return { state, component, calls, confirmations, requiring }
}

async function run() {
  const first = mount()
  await first.state.loadMailboxes()
  await Vue.nextTick()
  await first.state.openMessage('message1')
  assert.equal(first.state.detail.reply.to, 'reply@example.com')
  assert.equal(first.state.fromAddress, 'support@example.com')
  await first.state.refresh()
  assert.equal(first.calls.filter((call) => call.options.method === 'POST').length, 0, 'reading never queues a reply')
  first.state.draft = 'Human approved reply'
  await first.state.sendReply()
  const sent = first.calls.find((call) => call.options.method === 'POST')
  assert.equal(sent.url, '/api/mail/messages/message1/reply')
  assert.deepEqual(sent.options.body, { request_id: 'f093d57f-065b-4914-90d6-1aebebdeed5e', text: 'Human approved reply', from_address: 'support@example.com' })
  assert.match(first.confirmations[0], /support@example\.com para reply@example\.com/)
  assert.equal(first.state.draft, '')

  const cancelled = mount({ allowed: false })
  cancelled.state.mailboxes = [mailbox]; cancelled.state.mailboxId = 'box1'
  await Vue.nextTick()
  await cancelled.state.openMessage('message1')
  cancelled.state.draft = 'Do not send'
  await cancelled.state.sendReply()
  assert.equal(cancelled.calls.filter((call) => call.options.method === 'POST').length, 0)

  const retry = mount({ failOnce: true })
  retry.state.mailboxes = [mailbox]; retry.state.mailboxId = 'box1'
  await Vue.nextTick()
  await retry.state.openMessage('message1')
  retry.state.draft = 'Keep the same request'
  await retry.state.sendReply()
  assert.equal(retry.state.draft, 'Keep the same request')
  await retry.state.sendReply()
  const attempts = retry.calls.filter((call) => call.options.method === 'POST')
  assert.equal(attempts.length, 2)
  assert.equal(attempts[0].options.body.request_id, attempts[1].options.body.request_id)

  const blocked = mount()
  blocked.state.detail = { ...structuredClone(incoming), outbox: [{ id: 'job', status: 'uncertain', text: 'Maybe sent', created_at: '2026-10-08' }] }
  blocked.state.draft = 'Never repeat an uncertain send'
  await blocked.state.sendReply()
  assert.equal(blocked.calls.length, 0)

  // Real Vue rendering escapes email text and has an authenticated attachment URL.
  const rendered = mount()
  const setup = rendered.component.setup
  rendered.component.setup = (props, context) => {
    const state = setup(props, context)
    state.mailboxes.value = [mailbox]
    state.mailboxId.value = 'box1'
    state.detail.value = structuredClone(incoming)
    return state
  }
  const templateModule = { exports: {} }
  new Function('require', 'module', 'exports', transpile(template.code))(rendered.requiring, templateModule, templateModule.exports)
  rendered.component.render = templateModule.exports.render
  const html = await renderToString(Vue.createSSRApp(rendered.component, { isAdmin: false }))
  assert.match(html, /&lt;script&gt;/)
  assert.doesNotMatch(html, /<script>alert|<img src=x/)
  assert.match(html, /href="\/api\/mail\/attachments\/file1"/)
  assert.doesNotMatch(html, /Cadastrar caixa|Permitir respostas humanas/)
  console.log('Mail component tests passed: explicit send, cancellation, safe retry, uncertainty and escaped rendering.')
}

run().catch((error) => { console.error(error); process.exitCode = 1 })
