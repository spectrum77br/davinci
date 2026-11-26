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
// M-93: o teto das posições (10) é a constante da página.
const maxStatement = source.statements.find((candidate) => ts.isVariableStatement(candidate)
  && candidate.declarationList.declarations.some((d) => d.name.getText(source) === 'MAX_CATEGORIAS_VENDA'))
assert.ok(maxStatement, 'MAX_CATEGORIAS_VENDA exists')
const selected = [maxStatement.getText(source), ...names.map((name) => {
  const node = source.statements.find((candidate) =>
    ts.isFunctionDeclaration(candidate) && candidate.name?.text === name)
  assert.ok(node, `real page helper ${name} exists`)
  return node.getText(source)
})]
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

// M-93 (26/11/2026): positions 6..10 read margin6..10/shipping6..10 — never the 5th pair.
{
  const five = { margin5: 0.53, shipping5: 30 }
  const amazon = { id: 'atlas', canal: 'kit', kit_number: 1, platform: 'amazon', commission: 0.1, ...five }
  const p = (product_type) => ({ cost_kit1: 100, product_type })
  // Same price as before for positions 1..5.
  assert.equal(compute(amazon, p(5)), Math.round((100 * 1.53 + 30) / 0.9))
  // A new category (position 6..10) starts with "—": no computed price, no fallback to the 5th.
  for (const t of [6, 7, 8, 9, 10]) {
    assert.equal(compute(amazon, p(t)), null, `position ${t} without margin has no price`)
    assert.equal(compute({ ...amazon, [`margin${t}`]: null, [`shipping${t}`]: 25 }, p(t)), null)
  }
  assert.equal(compute({ ...amazon, margin8: 0.4, shipping8: 25 }, p(8)), Math.round((100 * 1.4 + 25) / 0.9))
  assert.equal(compute({ ...amazon, margin10: '0.2', shipping10: '10' }, p(10)), Math.round((100 * 1.2 + 10) / 0.9))
  // Above 10 (never sent by the server) is held at 10, like calc.py.
  assert.equal(compute({ ...amazon, margin10: 0.2, shipping10: 10 }, p(14)), Math.round((100 * 1.2 + 10) / 0.9))
  // Catalogue: own margin at position 7 with the base shipping7; without it, the base margin7.
  const base7 = { ...amazon, id: 'base7', platform: 'mercadolivre', margin7: 0.3, shipping7: 12 }
  contasPorId.value.set(base7.id, base7)
  const cat7 = { id: 'cat7', canal: 'catalogo', conta_base_id: base7.id, margens_catalogo: { '7': '0.5' } }
  const prod7 = { cost_kit1: 100, preco_catalogo: 200, product_type: 7 }
  assert.equal(compute(cat7, prod7), Math.round((200 * 1.5 + 12) / 0.9))
  assert.equal(compute({ ...cat7, margens_catalogo: { '7': null } }, prod7), Math.round((200 * 1.3 + 12) / 0.9))
  contasPorId.value.clear()
}
console.log('Pricing missing-input and catalogue calculation checks passed')
