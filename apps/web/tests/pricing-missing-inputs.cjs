// Run from apps/web: node tests/pricing-missing-inputs.cjs
// Execute the real SFC calculation helpers, without API calls or DOM setup.
// Blank new accounts must not acquire a computed price during local edits.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const { parse } = require('vue/compiler-sfc')

const filename = path.resolve(__dirname, '../pages/pricing/[tab].vue')
const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
assert.deepEqual(errors, [])
const source = ts.createSourceFile(filename, descriptor.scriptSetup.content,
  ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
const names = [
  'ehCatalogo', 'contaBase', 'contaParametros', 'margemPropria', 'custoDaConta',
  'getKitCost', 'getMarginShipping', 'margemFreteDaColuna', 'computePrice',
]
const selected = names.map((name) => {
  const node = source.statements.find((candidate) =>
    ts.isFunctionDeclaration(candidate) && candidate.name?.text === name)
  assert.ok(node, `real page helper ${name} exists`)
  return node.getText(source)
})
const compiled = ts.transpileModule(selected.join('\n'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022 }, reportDiagnostics: true,
})
assert.deepEqual(compiled.diagnostics, [])
const contasPorId = { value: new Map() }
const compute = new Function('contasPorId', `${compiled.outputText}\nreturn computePrice`)(contasPorId)
const product = { cost_kit1: 100, preco_catalogo: 200, product_type: 2 }
const kit = { id: 'kit', canal: 'kit', kit_number: 1, commission: null, margin2: null, shipping2: null }

for (const platform of ['carrefour', 'netshoes']) {
  const account = { ...kit, platform }
  assert.equal(compute(account, product), null, 'blank account has no computed price')
  assert.equal(compute({ ...account, margin2: 0.2 }, product), null, 'missing commission is not zero percent')
  assert.equal(compute({ ...account, commission: 0.1, shipping2: 10 }, product), null,
    'shipping does not substitute for missing margin')
  assert.equal(compute({ ...account, commission: '', margin2: 0.2 }, product), null)
  assert.equal(compute({ ...account, commission: 0.1, margin2: '' }, product), null)
  assert.equal(compute({ ...account, commission: 0, margin2: 0 }, product), 100,
    'explicit zero commission and margin are valid values')
  assert.equal(compute({ ...account, commission: '0', margin2: '0', shipping2: '0' }, product), 100)
  assert.equal(compute({ ...account, commission: 0.1, margin2: 0.2, shipping2: 5 }, product), 139)
  assert.equal(compute({ ...account, commission: 1, margin2: 0.2 }, product), null)
}

// Catalogue commission and shipping come from the base, but a configured
// catalogue margin is valid even when the base margin has not been set.
const base = { ...kit, commission: 0.1, shipping2: 10 }
const catalogue = {
  id: 'catalogue', canal: 'catalogo', conta_base_id: base.id,
  commission: 0.8, margin2: 0.8, shipping2: 500, margens_catalogo: { '2': 0.2 },
}
contasPorId.value.set(base.id, base)
assert.equal(compute(catalogue, product), 278)
assert.equal(compute({ ...catalogue, margens_catalogo: { '2': 0 } }, product), 233)
assert.equal(compute({ ...catalogue, margens_catalogo: {} }, product), null)
base.margin2 = 0.1
assert.equal(compute({ ...catalogue, margens_catalogo: {} }, product), 256)
base.commission = null
assert.equal(compute(catalogue, product), null, 'catalogue cannot invent a missing base commission')
contasPorId.value.clear()
assert.equal(compute(catalogue, product), null, 'catalogue needs its base account')
console.log('Pricing missing-input and catalogue calculation checks passed')
