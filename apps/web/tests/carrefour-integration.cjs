// Run from apps/web: node tests/carrefour-integration.cjs
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { ref, computed, watch, reactive, nextTick } = require('vue')
const ts = require('typescript')
const { parse, compileTemplate, compileScript } = require('vue/compiler-sfc')

const components = ['components/IntegrationFormModal.vue', 'pages/integrations.vue', 'pages/companies/[id].vue', 'pages/anuncios.vue']
let modalScript
let integrationsScript
for (const name of components) {
  const filename = path.resolve(__dirname, '..', name)
  const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
  assert.deepEqual(errors, [])
  assert.deepEqual(compileTemplate({ source: descriptor.template.content, filename, id: name }).errors, [])
  compileScript(descriptor, { id: name })
  if (name.includes('IntegrationFormModal')) modalScript = descriptor.scriptSetup.content
  if (name === 'pages/integrations.vue') integrationsScript = descriptor.scriptSetup.content
}
const script = ts.transpileModule(modalScript.replace(/^import .*$/gm, ''), {
  compilerOptions: { target: ts.ScriptTarget.ES2022 },
}).outputText
const factory = new Function('ref', 'computed', 'watch', 'defineProps', 'defineEmits', 'useApi', 'useAuthStore',
  script + '\nreturn { draft, requiredOk, visibleFields, eligibleStores, currentSpec, submitForm, createErr };')

async function run() {
  const props = reactive({ open: false, stores: [
    { id: 'vr', company_id: 'vr-company', marketplace: 'carrefour', integration_id: null },
    { id: 'poofy', company_id: 'poofy-company', marketplace: 'carrefour', integration_id: null },
    { id: 'ml', company_id: 'vr-company', marketplace: 'ml', integration_id: null },
    { id: 'linked', company_id: 'vr-company', marketplace: 'carrefour', integration_id: 'already' },
  ], companies: [], editing: null })
  const calls = []
  const events = []
  let fail = false
  const s = factory(ref, computed, watch, () => props, () => (...args) => events.push(args), () => ({
    api: async (url, options) => {
      calls.push({ url, options })
      if (fail) throw { data: { detail: { code: 'carrefour_shop_id_invalid' } } }
      return { id: 'integration' }
    },
  }), () => ({ user: { email: 'operator@example.test' } }))
  s.draft.value.platform = 'carrefour'
  await nextTick()
  assert.equal(s.currentSpec.value.fields.find(f => f.key === 'api_key').type, 'password')
  assert.deepEqual(s.eligibleStores.value.map(store => store.id), ['vr', 'poofy'])
  s.draft.value.creds = { api_key: 'private-unit-key', shop_id: '123' }
  assert.equal(s.requiredOk.value, false, 'Carrefour requires a selected account')
  s.draft.value.store_id = 'ml'
  assert.equal(s.requiredOk.value, false, 'A different marketplace cannot be linked')
  s.draft.value.store_id = 'vr'
  s.draft.value.name = 'Carrefour VR'
  assert.equal(s.requiredOk.value, true)
  await s.submitForm()
  assert.deepEqual(calls[0], { url: '/api/integrations', options: { method: 'POST', body: {
    platform: 'carrefour', store_id: 'vr', name: 'Carrefour VR',
    credentials: { api_key: 'private-unit-key', shop_id: '123' },
  } } })
  assert.equal(calls.length, 1, 'Saving does not automatically test, import or change offers')
  assert.deepEqual(events[0], ['created', 'integration'])
  props.editing = { id: 'integration', platform: 'carrefour', store_id: 'vr', name: 'Carrefour VR' }
  assert.deepEqual(s.visibleFields.value.map(field => field.key), ['api_key'], 'Shop ID cannot be changed in edit mode')
  s.draft.value.creds = { api_key: 'rotated-unit-key', shop_id: '' }
  await s.submitForm()
  assert.deepEqual(calls[1].options, { method: 'PATCH', body: {
    name: 'Carrefour VR', credentials: { api_key: 'rotated-unit-key' },
  } })
  fail = true
  await s.submitForm()
  assert.equal(s.createErr.value, 'Informe o ID numérico da loja no Carrefour.')

  const testCode = integrationsScript.slice(integrationsScript.indexOf('async function testIntegration('), integrationsScript.indexOf('const editing ='))
    + integrationsScript.slice(integrationsScript.indexOf('function carrefourCheck('), integrationsScript.indexOf('function statusClass('))
  const testJs = ts.transpileModule(testCode, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText
  const checks = ref({})
  const testState = new Function('api', 'testingId', 'carrefourChecks', 'cooldownUntil', 'error', 'refresh',
    testJs + '\nreturn { testIntegration, carrefourCheck };')(
    async () => ({ ok: true, info: { checked_at: 1700000000, cached: true } }),
    ref(null), checks, ref({}), ref(null), async () => {},
  )
  const integration = { id: 'vr', platform: 'carrefour', last_test_at: new Date(1700000000 * 1000).toISOString() }
  await testState.testIntegration(integration)
  assert.deepEqual(testState.carrefourCheck(integration), {
    checkedAt: '2023-11-14T22:13:20.000Z', cached: true,
  }, 'Show the actual check time rather than the time the operator clicked')
  integration.last_test_at = null
  assert.equal(testState.carrefourCheck(integration), null, 'Key rotation must hide the previous cached result')
  await testState.testIntegration({ id: 'ml', platform: 'ml', last_test_at: null })
  assert.equal(checks.value.ml, undefined, 'Other marketplaces keep their original test display')
  console.log('PASS: 4 Vue components compile; account selection, secret field, explicit save, key rotation, error handling and cached check timestamp')
}
run().catch(error => { console.error(error); process.exitCode = 1 })
