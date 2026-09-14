// Run from apps/web: node tests/certificacoes-pdf.cjs
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const { ref, reactive, computed } = require('vue')
const { parse, compileTemplate } = require('vue/compiler-sfc')
const transpile = source => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText
const utilSource = fs.readFileSync(path.join(__dirname, '../utils/certificacoesAutosave.ts'), 'utf8')
function utility(save, onError = () => {}) {
  const timers = new Map()
  let nextId = 0
  const exports = {}
  new Function('exports', 'setTimeout', 'clearTimeout', transpile(utilSource))(
    exports, fn => { timers.set(++nextId, fn); return nextId }, id => timers.delete(id),
  )
  return { ...exports.createCertificacoesAutosave(save, onError), timers }
}
const settle = () => new Promise(setImmediate)
function requests() {
  const calls = []
  const save = (id, patch) => new Promise((resolve, reject) => calls.push({ id, patch, resolve, reject }))
  return { calls, save }
}
const filename = path.join(__dirname, '../pages/financeiro/suprimentos.vue')
const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
assert.deepEqual(errors, [])
assert.deepEqual(compileTemplate({ source: descriptor.template.content, filename, id: 'pdf-check' }).errors, [])
const pageScript = descriptor.scriptSetup.content.replace(/^import .*$/gm, '').replace('await load()', '')
function page() {
  const { calls, save } = requests()
  const downloads = []
  const api = (url, options) => save(url, options)
  const state = new Function('ref', 'reactive', 'computed', 'onBeforeUnmount', 'definePageMeta',
    'useApi', 'useAuthStore', 'createCertificacoesAutosave', 'fetch', 'document', 'URL', 'setTimeout',
    transpile(pageScript) + '\nreturn { rows, errorText, exporting, scheduleSave, exportTable, attachPdf };')(
      ref, reactive, computed, () => {}, () => {}, () => ({ api, url: v => v }),
      () => ({ isAdmin: true }), utility,
      async url => { downloads.push(url); return { ok: true, blob: async () => new Blob(['pdf']) } },
      { createElement: () => ({ click() {}, remove() {} }), body: { appendChild() {} } },
      { createObjectURL: () => 'blob:test', revokeObjectURL() {} }, () => {},
    )
  return { state, calls, downloads }
}
async function run() {
  {
    const { calls, save } = requests()
    const s = utility(save)
    s.schedule('one', 'produto', 'Air fryer')
    s.schedule('one', 'modelo', 'UAF001')
    const flush = s.flush()
    assert.deepEqual(calls[0].patch, { produto: 'Air fryer', modelo: 'UAF001' })
    assert.equal(s.timers.size, 0)
    calls[0].resolve()
    await flush
  }
  {
    const { calls, save } = requests()
    const s = utility(save)
    s.schedule('one', 'produto', 'antigo')
    const first = s.flush()
    s.schedule('one', 'produto', 'novo')
    const second = s.flush()
    assert.equal(calls.length, 1, 'same row saves must be serialized')
    calls[0].resolve()
    await settle()
    assert.equal(calls.length, 2)
    assert.deepEqual(calls[1].patch, { produto: 'novo' })
    calls[1].resolve()
    await Promise.all([first, second])
    assert.equal(calls.length, 2, 'concurrent flush must not duplicate writes')
  }
  {
    const { calls, save } = requests()
    const s = utility(save)
    s.schedule('one', 'produto', 'primeiro')
    s.schedule('one', 'valor', 0)
    const failed = assert.rejects(s.flush(), /offline/)
    s.schedule('one', 'produto', 'corrigido')
    calls[0].reject(new Error('offline'))
    await failed
    const retry = s.flush()
    assert.deepEqual(calls[1].patch, { produto: 'corrigido', valor: 0 })
    calls[1].resolve()
    await retry
  }
  {
    const { state: s, calls, downloads } = page()
    const row = { id: 'one', produto: 'antigo' }
    s.rows.value = [row]
    s.scheduleSave(row, 'produto', 'novo')
    s.scheduleSave(row, 'modelo', 'UAF001')
    const download = s.exportTable()
    await s.exportTable()
    assert.equal(calls.length, 1)
    assert.deepEqual(calls[0].patch.body, { produto: 'novo', modelo: 'UAF001' })
    assert.equal(downloads.length, 0, 'export must await saved visible changes')
    calls[0].resolve()
    await download
    assert.deepEqual(downloads, ['/api/financeiro/suprimentos/pdf'])
    assert.equal(s.exporting.value, false)
  }
  {
    const { state: s, calls, downloads } = page()
    const row = { id: 'one' }
    s.scheduleSave(row, 'produto', 'novo')
    const download = s.exportTable()
    calls[0].reject(new Error('offline'))
    await download
    assert.equal(downloads.length, 0, 'failed save must block stale PDF')
    assert.match(s.errorText.value, /não foram salvas/)
  }
  {
    const { state: s, calls } = page()
    const row = { id: 'one', produto: 'edição local', tem_pdf: false, pdf_nome: null }
    const input = { value: 'arquivo.pdf', files: [new File(['%PDF'], 'arquivo.pdf', { type: 'application/pdf' })] }
    const upload = s.attachPdf(row, { target: input })
    assert.equal(input.value, '')
    calls[0].resolve({ ...row, produto: 'antigo', tem_pdf: true, pdf_nome: 'arquivo.pdf' })
    await upload
    assert.equal(row.produto, 'edição local', 'attachment response must not replace inline edits')
    assert.equal(row.tem_pdf, true)
    assert.equal(row.pdf_nome, 'arquivo.pdf')
  }
  console.log('PASS: template compilation; six autosave/export/upload regression scenarios')
}
run().catch(error => { console.error(error); process.exitCode = 1 })
