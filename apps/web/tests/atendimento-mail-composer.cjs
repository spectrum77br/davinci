// Offline composer contract and interaction tests; no credentials or real delivery.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')
const filename = path.resolve(__dirname, '../components/AtendimentoMailComposer.vue')
const transpile = (source) => ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText
const helperModule = { exports: {} }
new Function('exports', transpile(fs.readFileSync(path.resolve(__dirname, '../lib/mailCompose.ts'), 'utf8')))(helperModule.exports)
const helpers = helperModule.exports
const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
assert.deepEqual(errors, [])
const script = compileScript(descriptor, { id: 'composer-test' })
const template = compileTemplate({ source: descriptor.template.content, filename, id: 'composer-test', compilerOptions: { bindingMetadata: script.bindings } })
assert.deepEqual(template.errors, [])
const source = {
  id: 'source1', subject: 'Documento', text: 'Texto original', from_address: 'customer@example.com', from_name: 'Cliente', received_at: '2026-10-28T12:00:00Z',
  to: ['store@example.com', 'team@example.com'], cc: ['owner@example.com', 'team@example.com', 'colleague@example.com'], reply_to: 'reply@example.com',
  reply: { to: 'reply@example.com', from_address: 'store@example.com', allowed_from_addresses: ['store@example.com'], can_reply: true, send_ready: true },
  attachments: [{ id: 'original1', filename: 'original.pdf', size: 3, content_type: 'application/pdf' }], outbox: [],
}
function mount({ approve = true, failSendOnce = false, unauthorized = false, unavailable = false, files = [] } = {}) {
  const calls = [], confirmations = [], emitted = [], callbacks = []
  let fail = failSendOnce
  const context = { allowed_from_addresses: unauthorized ? [] : ['store@example.com'], can_compose: !unauthorized, send_ready: !unavailable, reply_to: ['reply@example.com'], reply_all_to: ['reply@example.com', 'team@example.com'], reply_all_cc: ['colleague@example.com'] }
  const globals = {
    ref: Vue.ref, computed: Vue.computed, watch: Vue.watch, nextTick: Vue.nextTick, onMounted: (cb) => callbacks.push(cb), onBeforeUnmount: () => {},
    useApi: () => ({ api: async (url, opts = {}) => {
      calls.push({ url, opts })
      if (url.includes('/compose-context')) return structuredClone(context)
      if (url.endsWith('/send')) { if (fail) { fail = false; throw new Error('network interrupted') } return { id: 'job1', status: 'queued' } }
      if (url.endsWith('/outbox')) return { items: [] }
      if (url.endsWith('/drafts') && opts.method === 'POST') return { id: 'draft1', ...opts.body }
      if (url === '/api/mail/drafts/draft1' && opts.method === 'DELETE') return null
      if (url === '/api/mail/drafts/draft1' && opts.method === 'PATCH') return { id: 'draft1', ...opts.body }
      if (url === '/api/mail/drafts/draft1') return { id: 'draft1', action: 'forward', source_message_id: 'source1', from_address: 'store@example.com', to: ['new@example.com'], cc: [], bcc: ['hidden@example.com'], subject: 'Saved', text: 'Saved content', attachments: files, source_attachment_ids: ['original1'] }
      if (url === '/api/mail/messages/source1') return structuredClone(source)
      if (url.endsWith('/drafts')) return { items: [{ id: 'draft1', subject: 'Saved', action: 'forward', source_message_id: 'source1', updated_at: '2026-10-28' }] }
      throw new Error(`Unexpected ${url}`)
    } }),
    crypto: { randomUUID: () => '726f7f65-f8e9-4f2d-a609-75da0ac319fe' },
    window: { addEventListener: () => {}, removeEventListener: () => {}, confirm: (question) => { confirmations.push(question); return approve } },
    document: { activeElement: null }, btoa: (value) => Buffer.from(value, 'binary').toString('base64'),
  }
  const requiring = (name) => name === '~/lib/mailCompose' ? helpers : name === 'lucide-vue-next' ? new Proxy({}, { get: () => ({ render: () => Vue.h('i') }) }) : require(name)
  const module = { exports: {} }
  new Function('require', 'module', 'exports', ...Object.keys(globals), transpile(script.content))(requiring, module, module.exports, ...Object.values(globals))
  const props = Vue.reactive({ mailboxId: 'box1', mailboxLabel: 'Principal', ownAddresses: ['store@example.com', 'owner@example.com'], disabled: false, restricted: false })
  const state = Vue.proxyRefs(module.exports.default.setup(props, { expose: () => {}, emit: (...args) => emitted.push(args) }))
  return { state, props, calls, confirmations, emitted, callbacks }
}
async function run() {
  assert.deepEqual(helpers.composeRecipients('One@example.com; one@example.com', 'two@example.com, ONE@example.com', 'three@example.com;two@example.com'), { to: ['one@example.com'], cc: ['two@example.com'], bcc: ['three@example.com'] })
  assert.throws(() => helpers.parseRecipients('good@example.com, invalid'), /Confira/)
  assert.throws(() => helpers.parseRecipients('name@example.com\r\nBcc:evil@example.com'), /Confira/)
  assert.deepEqual(helpers.replyRecipients(source, 'reply_all', ['store@example.com', 'owner@example.com']), { to: ['reply@example.com', 'team@example.com'], cc: ['colleague@example.com'] })
  assert.equal(helpers.prefixedSubject('Re: Existing', 'reply'), 'Re: Existing')
  assert.equal(helpers.prefixedSubject('x'.repeat(998), 'forward').length, 998)
  assert.match(helpers.attachmentProblem([10 * 1024 * 1024, 6 * 1024 * 1024]), /15 MB/)
  assert.match(helpers.attachmentProblem(Array(11).fill(0)), /10 anexos/)
  assert.match(helpers.attachmentProblem([10 * 1024 * 1024 + 1]), /10 MB/)

  const m = mount()
  await m.state.open('reply_all', Vue.reactive(source))
  assert.equal(m.state.to, 'reply@example.com, team@example.com')
  assert.equal(m.state.cc, 'colleague@example.com')
  assert.equal(m.state.from, 'store@example.com')
  m.state.text = 'Resposta com arquivo'
  m.state.bcc = 'hidden@example.com'
  await m.state.addFiles({ target: { value: 'selected', files: [{ name: 'pedido.txt', type: 'text/plain', size: 3, arrayBuffer: async () => Uint8Array.from([65, 66, 67]).buffer }] } })
  assert.deepEqual([...m.state.attachments].map((file) => ({ ...file })), [{ filename: 'pedido.txt', content_type: 'text/plain', data_base64: 'QUJD' }])
  await m.state.saveDraft()
  const saved = m.calls.find((call) => call.url.endsWith('/drafts') && call.opts.method === 'POST')
  assert.equal(saved.opts.body.request_id, undefined)
  assert.equal(saved.opts.body.bcc[0], 'hidden@example.com')
  assert.equal(m.state.draftId, 'draft1')
  assert.equal(m.state.dirty, false)
  m.state.text = 'Alterada'
  assert.equal(m.state.dirty, true)
  await m.state.send()
  const sent = m.calls.find((call) => call.url.endsWith('/send'))
  assert.equal(sent.opts.retry, 0)
  assert.equal(sent.opts.body.source_message_id, 'source1')
  assert.equal(sent.opts.body.action, 'reply_all')
  assert.equal(sent.opts.body.attachments[0].data_base64, 'QUJD')
  assert.match(m.confirmations[0], /CCO: hidden@example.com/)
  assert.match(m.confirmations[0], /Anexos: 1/)
  assert.ok(m.calls.some((call) => call.url === '/api/mail/drafts/draft1' && call.opts.method === 'DELETE'))
  assert.equal(m.state.view, 'outbox')
  assert.deepEqual(m.emitted.find((event) => event[0] === 'sent'), ['sent', 'source1'])

  const forward = mount()
  await forward.state.open('forward', source)
  assert.equal(forward.state.subject, 'Enc: Documento')
  assert.match(forward.state.text, /Mensagem encaminhada/)
  assert.deepEqual([...forward.state.sourceAttachmentIds], ['original1'])
  assert.equal(forward.state.to, '')
  forward.state.to = 'destination@example.com'
  await forward.state.send()
  assert.deepEqual(forward.calls.find((call) => call.url.endsWith('/send')).opts.body.source_attachment_ids, ['original1'])

  const cancelled = mount({ approve: false })
  await cancelled.state.open('new')
  cancelled.state.to = 'someone@example.com'; cancelled.state.subject = 'Assunto'; cancelled.state.text = 'Preservar'
  await cancelled.state.send()
  cancelled.state.close()
  assert.equal(cancelled.state.visible, true, 'cancelling discard keeps editor and files intact')
  assert.equal(cancelled.calls.filter((call) => call.url.endsWith('/send')).length, 0)

  const uncertain = mount({ failSendOnce: true })
  await uncertain.state.open('new')
  uncertain.state.to = 'someone@example.com'; uncertain.state.subject = 'Assunto'; uncertain.state.text = 'Preservar'
  await uncertain.state.send()
  assert.equal(uncertain.state.text, 'Preservar')
  assert.equal(uncertain.state.ambiguous, true)
  const attemptId = uncertain.state.sendRequestId
  uncertain.state.savedFingerprint = uncertain.state.fingerprint()
  assert.equal(uncertain.state.dirty, false, 'saved drafts can also have ambiguous delivery')
  uncertain.state.close()
  assert.equal(uncertain.state.visible, true, 'cannot close an unresolved send')
  await uncertain.state.open('new')
  await uncertain.state.showList('drafts')
  await uncertain.state.restore('draft1')
  assert.equal(uncertain.state.view, 'editor')
  assert.equal(uncertain.state.text, 'Preservar')
  assert.equal(uncertain.state.sendRequestId, attemptId)
  let prevented = false
  const unload = { preventDefault: () => { prevented = true }, returnValue: undefined }
  uncertain.state.beforeUnload(unload)
  assert.equal(prevented, true, 'unresolved delivery warns even for a saved draft')
  await uncertain.state.send()
  const attempts = uncertain.calls.filter((call) => call.url.endsWith('/send'))
  assert.equal(attempts.length, 2)
  assert.equal(attempts[0].opts.body.request_id, attempts[1].opts.body.request_id, 'retry reuses request identity')

  const rejected = mount({ unauthorized: true })
  await rejected.state.open('reply', source)
  rejected.state.text = 'Blocked'; rejected.state.from = 'arbitrary@example.com'
  await rejected.state.send()
  assert.equal(rejected.calls.filter((call) => call.url.endsWith('/send')).length, 0)

  const offline = mount({ unavailable: true })
  await offline.state.open('new')
  offline.state.subject = 'Salvar offline'; offline.state.text = 'Sem envio'
  await offline.state.saveDraft(); await offline.state.send()
  assert.equal(offline.state.draftId, 'draft1')
  assert.equal(offline.calls.filter((call) => call.url.endsWith('/send')).length, 0)

  const restored = mount({ files: [{ filename: 'anexo.txt', content_type: 'text/plain', data_base64: 'QQ==' }] })
  await restored.state.showList('drafts'); await restored.state.restore('draft1')
  assert.equal(restored.state.bcc, 'hidden@example.com')
  assert.equal(restored.state.attachments[0].filename, 'anexo.txt')
  assert.deepEqual([...restored.state.sourceAttachmentIds], ['original1'])
  assert.equal(restored.state.dirty, false)
  assert.equal(restored.state.source.id, 'source1')
  console.log('Mail composer passed: recipients, attachments, forwarding, private drafts, confirmation, permissions and idempotent retry.')
}
run().catch((error) => { console.error(error); process.exitCode = 1 })
