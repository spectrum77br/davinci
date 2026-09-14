// Run from apps/web: node tests/companies-new-account.cjs
// Exercise the actual modal code with controlled requests and timers.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const { createRequire } = require('node:module')
const path = require('node:path')
const requireWeb = createRequire(path.resolve(__dirname, '../package.json'))
const { ref, reactive } = requireWeb('vue')
const ts = requireWeb('typescript')
const { parse, compileTemplate } = requireWeb('vue/compiler-sfc')
const filename = path.resolve(__dirname, '../pages/companies/index.vue')
const source = fs.readFileSync(filename, 'utf8')
const { descriptor, errors } = parse(source, { filename })
assert.deepEqual(errors, [])
const template = compileTemplate({ source: descriptor.template.content, filename, id: 'companies-modal-check' })
assert.deepEqual(template.errors, [])
const script = descriptor.scriptSetup.content
const modal = script.slice(script.indexOf('// ---------- new account modal'), script.indexOf('async function createStoreCell'))
const js = ts.transpileModule(modal, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText
const factory = new Function('ref', 'reactive', 'api', 'canEdit', 'MARKETPLACE_SHORT', 'refresh', 'error', 'setTimeout', js + `
return { newAccountFor, newAccountForm, availablePhones, availableEmails, availableServers,
  availableCadastrosLoading, availableCadastrosLoaded, availableCadastrosError,
  newAccountSaving, newAccountErr, newAccountResult,
  openNewAccount, closeNewAccount, loadAvailableCadastros, submitNewAccount };
`)
function setup() {
  const calls = []
  const timers = []
  let refreshed = 0
  const api = (url, opts) => new Promise((resolve, reject) => calls.push({ url, opts, resolve, reject }))
  return { calls, timers, refreshCount: () => refreshed,
    state: factory(ref, reactive, api, ref(true), {}, async () => { refreshed++ }, ref(null), (fn) => { timers.push(fn) }) }
}
const row = id => ({ company: { id, apelido: id } })
const finish = (calls, start, prefix) => calls.slice(start, start + 3).forEach((call, i) => call.resolve([{ id: `${prefix}-${i}`, codigo: prefix }]))
const selectAll = s => {
  s.newAccountForm.phoneId = s.availablePhones.value[0].id
  s.newAccountForm.emailId = s.availableEmails.value[0].id
  s.newAccountForm.serverId = s.availableServers.value[0].id
}
const settle = () => new Promise(setImmediate)
async function ready(ctx, company = 'one', marketplace = 'ml') {
  const start = ctx.calls.length
  const pending = ctx.state.openNewAccount(row(company), marketplace)
  finish(ctx.calls, start, company)
  await pending
  selectAll(ctx.state)
}
async function run() {
  {
    const { calls, state: s } = setup()
    s.availablePhones.value = [{ id: 'old' }]
    s.newAccountForm.phoneId = 'old'
    const pending = s.openNewAccount(row('one'), 'ml')
    assert.deepEqual(s.availablePhones.value, [])
    assert.equal(s.newAccountForm.phoneId, '')
    assert.equal(s.availableCadastrosLoading.value, true)
    assert.equal(s.availableCadastrosLoaded.value, false)
    await s.submitNewAccount()
    assert.equal(calls.length, 3, 'submit must not create a store while options load')
    finish(calls, 0, 'ml')
    await pending
    assert.equal(s.availableCadastrosLoaded.value, true)
    assert.equal(s.availableCadastrosLoading.value, false)
    assert.equal(s.availablePhones.value[0].id, 'ml-0')
  }
  {
    const { calls, state: s } = setup()
    const first = s.openNewAccount(row('one'), 'amazon')
    s.closeNewAccount()
    const second = s.openNewAccount(row('two'), 'ml')
    finish(calls, 3, 'ml')
    await second
    finish(calls, 0, 'amazon')
    await first
    assert.equal(s.availablePhones.value[0].id, 'ml-0', 'late old success must not overwrite new marketplace')
    assert.equal(s.availableCadastrosLoading.value, false)
  }
  {
    const { calls, state: s } = setup()
    const first = s.openNewAccount(row('one'), 'amazon')
    const second = s.openNewAccount(row('two'), 'shopee')
    calls[0].reject(new Error('old request failed'))
    await first
    assert.equal(s.availableCadastrosLoading.value, true, 'old failure must not end current loading')
    assert.equal(s.availableCadastrosError.value, null)
    finish(calls, 3, 'shopee')
    await second
    assert.equal(s.availablePhones.value[0].id, 'shopee-0')
  }
  {
    const { calls, state: s } = setup()
    const first = s.openNewAccount(row('one'), 'ml')
    calls[1].reject(new Error('unavailable'))
    await first
    assert.equal(s.availableCadastrosError.value, 'unavailable')
    assert.equal(s.availableCadastrosLoaded.value, false)
    assert.equal(s.availableCadastrosLoading.value, false)
    const retry = s.loadAvailableCadastros('ml')
    assert.equal(s.availableCadastrosError.value, null)
    assert.equal(s.availableCadastrosLoading.value, true)
    finish(calls, 3, 'retry')
    await retry
    assert.equal(s.availablePhones.value[0].id, 'retry-0')
  }
  {
    const { calls, state: s } = setup()
    const pending = s.openNewAccount(row('one'), 'ml')
    s.closeNewAccount()
    finish(calls, 0, 'closed')
    await pending
    assert.equal(s.newAccountFor.value, null)
    assert.equal(s.availableCadastrosLoaded.value, false)
    assert.deepEqual(s.availablePhones.value, [])
  }
  {
    const { calls, state: s } = setup()
    const pending = s.openNewAccount(row('one'), 'ml')
    calls.forEach(call => call.resolve([]))
    await pending
    assert.equal(s.availableCadastrosLoaded.value, true, 'empty successful load is distinct from failure')
    assert.equal(s.availableCadastrosError.value, null)
    assert.deepEqual(s.availablePhones.value, [])
  }
  {
    const ctx = setup()
    const { state: s, calls, timers } = ctx
    await ready(ctx)
    const submitted = s.submitNewAccount()
    await s.submitNewAccount()
    assert.equal(calls.length, 4, 'double click must send only one POST')
    assert.equal(calls[3].url, '/api/stores/account')
    assert.deepEqual(calls[3].opts, { method: 'POST', body: {
      company_id: 'one', marketplace: 'ml', phone_id: 'one-0', email_id: 'one-1', server_id: 'one-2'
    } })
    calls[3].resolve({ id: 'new-store' })
    await submitted
    assert.match(s.newAccountResult.value, /Conta criada/)
    assert.equal(s.newAccountSaving.value, false)
    assert.equal(calls.length, 4, 'success must not send legacy mirror/link requests')
    assert.equal(ctx.refreshCount(), 1)
    await s.submitNewAccount()
    assert.equal(calls.length, 4, 'success must prevent resubmission before closing')
    s.closeNewAccount()
    await ready(ctx, 'two', 'amazon')
    timers[0]()
    assert.equal(s.newAccountFor.value.company.id, 'two', 'old success timer must not close a new modal')
  }
  {
    const ctx = setup()
    const { state: s, calls } = ctx
    await ready(ctx)
    const submitted = s.submitNewAccount()
    calls[3].reject({ data: { detail: { code: 'cadastro_unavailable' } } })
    await settle()
    assert.equal(calls.length, 7, 'conflict must reload three availability lists')
    assert.equal(s.availableCadastrosLoading.value, true)
    assert.equal(s.newAccountResult.value, null)
    assert.equal(s.newAccountForm.phoneId, '')
    finish(calls, 4, 'fresh')
    await submitted
    assert.equal(s.availablePhones.value[0].id, 'fresh-0')
    assert.match(s.newAccountErr.value, /já está em uso/)
    assert.equal(s.newAccountResult.value, null)
  }
  {
    const ctx = setup()
    const { state: s, calls } = ctx
    await ready(ctx)
    const submitted = s.submitNewAccount()
    calls[3].reject({ data: { detail: { code: 'forbidden' } } })
    await submitted
    assert.equal(calls.length, 4, 'permission failure must not reload options or send follow-up writes')
    assert.match(s.newAccountErr.value, /permissão/)
    assert.equal(s.newAccountSaving.value, false)
    assert.equal(s.newAccountResult.value, null)
  }
  {
    const ctx = setup()
    const { state: s, calls } = ctx
    await ready(ctx)
    const firstSubmit = s.submitNewAccount()
    calls[3].reject({ data: { detail: { code: 'cadastro_unavailable' } } })
    await settle()
    s.closeNewAccount()
    await ready(ctx, 'two', 'amazon')
    const secondSubmit = s.submitNewAccount()
    assert.equal(s.newAccountSaving.value, true)
    finish(calls, 4, 'old-conflict-refresh')
    await firstSubmit
    assert.equal(s.newAccountSaving.value, true, 'finishing an old conflict refresh must not unlock the new account POST')
    calls[10].resolve({ id: 'second-store' })
    await secondSubmit
  }
  console.log('PASS: SFC parse and template compilation; 10 modal loading/submit/race/error scenarios')
}
run().catch(error => { console.error(error); process.exitCode = 1 })
