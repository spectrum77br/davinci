// Run from apps/web: node tests/use-api-context.cjs
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')

const source = fs.readFileSync(path.join(__dirname, '../composables/useApi.ts'), 'utf8')
const script = ts.transpileModule(source.replaceAll('import.meta.server', 'isServer'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText

function harness(isServer) {
  let context = null
  const calls = []
  const headerReads = []
  const exports = {}
  new Function('exports', 'isServer', 'useRuntimeConfig', 'useRequestHeaders', '$fetch', script)(
    exports,
    isServer,
    () => {
      assert.ok(context, 'runtime config requires setup context')
      return { apiUrlInternal: context.base }
    },
    names => {
      assert.ok(context, 'request headers cannot be read after setup context is lost')
      headerReads.push({ request: context.name, names })
      return context.cookie ? { cookie: context.cookie } : {}
    },
    async (url, options) => {
      calls.push({ url, options })
      return { ok: true }
    },
  )
  return {
    create(request) {
      context = request
      try { return exports.useApi() } finally { context = null }
    },
    calls,
    headerReads,
  }
}

async function run() {
  {
    const h = harness(true)
    const request = h.create({ name: 'first', base: 'http://api:8000', cookie: 'session=first-test' })
    assert.equal(h.headerReads.length, 1, 'read incoming cookies during composable setup')
    await Promise.resolve() // Page load first awaits an autosave flush.
    await request.api('/api/financeiro/suprimentos')
    await request.api('/api/financeiro/suprimentos/anatel/status')
    assert.equal(h.headerReads.length, 1, 'deferred calls must not need an active Nuxt context')
    assert.deepEqual(h.headerReads[0].names, ['cookie'])
    assert.deepEqual(h.calls.map(c => c.options.headers), [
      { cookie: 'session=first-test' }, { cookie: 'session=first-test' },
    ])
    assert.equal(h.calls[0].url, 'http://api:8000/api/financeiro/suprimentos')
    assert.equal(h.calls[0].options.credentials, 'include')
  }
  {
    const h = harness(true)
    const first = h.create({ name: 'first', base: 'http://api:8000', cookie: 'session=first-test' })
    const second = h.create({ name: 'second', base: 'http://api:8001', cookie: 'session=second-test' })
    const anonymous = h.create({ name: 'anonymous', base: 'http://api:8002' })
    await Promise.resolve()
    await Promise.all([second.api('api/two'), anonymous.api('/api/public'), first.api('/api/one')])
    assert.deepEqual(h.calls.map(c => [c.url, c.options.headers]), [
      ['http://api:8001/api/two', { cookie: 'session=second-test' }],
      ['http://api:8002/api/public', {}],
      ['http://api:8000/api/one', { cookie: 'session=first-test' }],
    ], 'each composable must retain only its own request cookies and internal URL')
    assert.equal(h.headerReads.length, 3)
  }
  {
    const h = harness(false)
    const client = h.create({ name: 'browser', base: 'http://private-api:8000', cookie: 'server-only-test' })
    await Promise.resolve()
    await client.api('/api/financeiro/suprimentos')
    assert.equal(h.headerReads.length, 0, 'browser calls must not inspect server request headers')
    assert.equal(client.url('api/test'), '/api/test')
    assert.equal(h.calls[0].url, '/api/financeiro/suprimentos')
    assert.equal(h.calls[0].options.headers, undefined)
    assert.equal(h.calls[0].options.credentials, 'include')
  }
  {
    const h = harness(true)
    const request = h.create({ name: 'custom', base: 'http://api:8000', cookie: 'session=custom-test' })
    await request.api('/api/test', { method: 'POST', headers: { 'x-test': 'explicit' }, body: { value: 1 } })
    assert.deepEqual(h.calls[0].options.headers, { 'x-test': 'explicit' })
    assert.equal(h.calls[0].options.method, 'POST')
    assert.deepEqual(h.calls[0].options.body, { value: 1 })
  }
  console.log('PASS: four API setup-context, request isolation, browser and options scenarios')
}
run().catch(error => { console.error(error); process.exitCode = 1 })
