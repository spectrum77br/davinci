// node tests/devolucao-estoque-usado-sfc.cjs
// Compila o modal real, renderiza com Vue SSR e usa API falsa. Não lança estoque.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { renderToString } = require('@vue/server-renderer')
const { parse, compileTemplate } = require('vue/compiler-sfc')

const transpile = (source) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText

const modalFile = path.join(__dirname, '../components/DevolucaoEstoqueModal.vue')
const modal = parse(fs.readFileSync(modalFile, 'utf8'), { filename: modalFile })
assert.deepEqual(modal.errors, [])
const template = compileTemplate({ source: modal.descriptor.template.content, filename: modalFile, id: 'stock-usado-test' })
assert.deepEqual(template.errors, [])
const compiled = {}
new Function('exports', 'require', transpile(template.code))(compiled, require)

const script = modal.descriptor.scriptSetup.content.replace(/^import.*from\s+'[^']+'\s*$/gm, '')
const factory = new Function('ref', 'computed', 'watch', 'defineProps', 'defineEmits', 'useApi', transpile(script + `
return { loading, errorMsg, data, selectedExisting, selectedTag, selectedSuffix,
  q, searching, searchResults, searchErr, existing, hasExisting, isUsado, destinoCompleto,
  tagChoices, destinoSuffixes, usadoSku, canConfirm, SEM_TAG, saldo, destinoSku,
  runSearch, fetchVariants, pickExisting, pickTag, pickSuffix, confirm, emit };
`))

async function mountModal(props) {
  const emitted = []
  const calls = []
  const state = factory(Vue.ref, Vue.computed, () => {}, () => props,
    () => (...args) => emitted.push(args),
    () => ({ api: async (url) => {
      calls.push(url)
      if (!url.startsWith('/api/devolutions/sku-suffixes?sku=')) throw new Error('unexpected API call')
      return { base: 'b037.28', allowed_suffixes: ['ci', 'pi', 'ra', 'sa', 'sp', 'us', 'cd'],
        variants: [{ suffix: 'sp', sku: 'b037.28.sp', name: 'Mala nova', exists: true }] }
    } }))
  await state.fetchVariants()
  const component = {
    setup: () => ({ ...props, ...state }),
    render: compiled.render,
  }
  const app = Vue.createSSRApp(component)
  app.component('Button', { setup: (_, { slots }) => () => Vue.h('button', slots.default?.()) })
  for (const name of ['Loader2', 'Search', 'X']) app.component(name, { render: () => Vue.h('i') })
  const html = await renderToString(app)
  assert.equal(calls.length, 1)
  return { html, state, emitted }
}

const pageFile = path.join(__dirname, '../pages/devolucoes.vue')
const page = parse(fs.readFileSync(pageFile, 'utf8'), { filename: pageFile })
assert.deepEqual(page.errors, [])
const ast = ts.createSourceFile('devolucoes.ts', page.descriptor.scriptSetup.content, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
const resolveFunction = ast.statements.find((node) => ts.isFunctionDeclaration(node) && node.name?.text === 'resolveStockModals')
assert.ok(resolveFunction, 'testa a função real da página')
function resolver(condicaoManutencao = 'Usado') {
  const calls = []
  const fn = new Function('isStockTrigger', 'isMalaOrEletro', 'askManutencao', 'askTrocaSku', 'askEstoque',
    transpile(resolveFunction.getText(ast)) + '\nreturn resolveStockModals;')(
      () => true,
      (sku) => /^(b[0-9]|u)/i.test(sku),
      async () => ({ tipo: condicaoManutencao }),
      async () => ({ sku: 'b037.28', condicao: 'Usado' }),
      async (sku, condicao, fullDestino) => {
        calls.push({ sku, condicao, fullDestino })
        return fullDestino ? { suffix: 'sp' } : { nova_tag: 'mala' }
      },
    )
  return { fn, calls }
}

async function main() {
  // Regressão exata: mesmo se um caller antigo ainda passar fullDestino=true,
  // Usado oferece z e não a grade b037.28.ci/pi/etc.
  const usado = await mountModal({ open: true, sku: 'b037.28', condicao: 'Usado', fullDestino: true })
  assert.match(usado.html, /zXXXX\.mala/)
  assert.match(usado.html, /zXXXX\.eletro/)
  assert.doesNotMatch(usado.html, /b037\.28\.(?:ci|pi|ra|sa|sp|cd|mala|eletro)/)
  assert.doesNotMatch(usado.html, /Estoques existentes/)
  usado.state.pickTag('mala')
  usado.state.confirm()
  assert.deepEqual(usado.emitted.at(-1), ['confirm', { nova_tag: 'mala' }], 'API receberá criação do z, não suffix regional')

  // A opção legada .us continua disponível, sem alteração de regra paralela.
  assert.match(usado.html, /b037\.28\.us/)
  usado.state.pickSuffix('us')
  usado.state.confirm()
  assert.deepEqual(usado.emitted.at(-1), ['confirm', { suffix: 'us' }])

  const novo = await mountModal({ open: true, sku: 'b037.28', condicao: 'Novo', fullDestino: true })
  assert.match(novo.html, /b037\.28\.sp/)
  assert.match(novo.html, /b037\.28\.mala/)
  assert.doesNotMatch(novo.html, /zXXXX/)
  novo.state.pickSuffix('sp')
  novo.state.confirm()
  assert.deepEqual(novo.emitted.at(-1), ['confirm', { suffix: 'sp' }])

  const comum = await mountModal({ open: true, sku: 'b037.28', condicao: 'Novo', fullDestino: false })
  assert.match(comum.html, /zXXXX\.mala/)
  assert.match(comum.html, /Estoques existentes/)

  for (const condition of ['Usado', 'Manutenção', 'Trocado']) {
    const r = resolver()
    const fields = await r.fn(condition, 'b037.28', true, true)
    assert.equal(r.calls.length, 1)
    assert.equal(r.calls[0].fullDestino, false, condition)
    assert.equal(fields.estoque_nova_tag, 'mala')
    assert.equal(fields.estoque_suffix, null)
  }
  const novoR = resolver()
  const fields = await novoR.fn('Novo', 'b037.28', true, true)
  assert.equal(novoR.calls[0].fullDestino, true)
  assert.equal(fields.estoque_suffix, 'sp')
  const novoSemCorrecao = resolver()
  const direto = await novoSemCorrecao.fn('Novo', 'b037.28', true, false)
  assert.equal(novoSemCorrecao.calls.length, 0)
  assert.equal(direto.estoque_destino_sku, 'b037.28')
  console.log('OK: modal Vue renderizado, Usado → z, Novo → bin regional, .us legado e condições efetivas preservados.')
}

main().catch((err) => { console.error(err); process.exitCode = 1 })
