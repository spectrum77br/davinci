// node tests/email-padroes-sfc.cjs — compilação e fluxos de assinatura por canal.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { parse, compileTemplate, compileScript } = require('vue/compiler-sfc')
const filename = path.resolve(__dirname, '../pages/email-padroes.vue')
const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
assert.deepEqual(errors, [])
const template = compileTemplate({ source: descriptor.template.content, filename, id: 'assinaturas-check' })
assert.deepEqual(template.errors, [])
compileScript(descriptor, { id: 'assinaturas-check' })
assert.match(descriptor.template.content, /sandbox=""/)
assert.doesNotMatch(descriptor.template.content, /Enviar teste|Assunto \*|Corpo \*|Remetente \(/)
const script = descriptor.scriptSetup.content.replace(/^import.*$/gm, '')
const transpiled = ts.transpileModule(script, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText
const factory = new Function('ref', 'computed', 'onMounted', 'definePageMeta', 'useCan', 'useApi',
  'apiErrMsg', 'MARCAS_ERROS', 'EMAIL_CONTEXTO_LABELS', 'navigator', 'ClipboardItem', 'Blob',
  transpiled + '\nreturn {grid,form,selected,preview,previewStale,saveError,success,load,openSignature,closeModal,updatePreview,save,rowMatches,copySignature,copied,copyError};')
const marca = { id: 'm1', nome: 'Poofy', slug: 'poofy', empresa_razao_social: null, has_logo: false }
const saved = { id: 'a1', marca_id: 'm1', contexto: 'sac', texto: 'Equipe SAC', incluir_logo: true, incluir_dados_marca: true, ativo: true }
function page({ edit = true, fail = false, deferPreview = false, failCopy = false, noClipboard = false } = {}) {
  const calls = [], pending = [], clipboardWrites = []
  const api = async (url, opts = {}) => {
    calls.push({url, opts})
    if (url.endsWith('/grid')) return {contextos: ['sac','ml'], rows: [{marca, cells: {sac: {...saved}, ml: null}}]}
    if (url.endsWith('/preview')) {
      const result = {html: `<div style="padding:24px">${opts.body.texto}<a href="https://wa.me/5511912345678"><img src="data:image/png;base64,aWNvbmU=" width="28" height="28">WhatsApp</a></div>`, text: opts.body.texto, avisos: []}
      if (deferPreview) return new Promise(resolve => pending.push(() => resolve(result)))
      return result
    }
    if (opts.method === 'PUT') {
      if (fail) throw new Error('Falha ao salvar')
      return {id: 'a2', marca_id: 'm1', contexto: url.split('/').pop(), ...opts.body}
    }
    throw new Error(`Chamada inesperada: ${url}`)
  }
  class ClipboardItemMock {
    constructor(data) { this.data = data }
    async getType(type) { return this.data[type] }
  }
  const navigator = {clipboard: noClipboard ? undefined : {write: async items => {
    if (failCopy) throw new Error('NotAllowedError')
    clipboardWrites.push(items)
  }}}
  const p = factory(Vue.ref, Vue.computed, () => {}, () => {}, () => Vue.ref(edit), () => ({api}),
    e => e.message, {}, {sac: 'SAC', ml: 'ML'}, navigator, ClipboardItemMock, Blob)
  return {p, calls, pending, clipboardWrites}
}
async function tick() { await new Promise(resolve => setImmediate(resolve)) }
;(async () => {
  {
    const {p,calls} = page()
    await p.load()
    const row = p.grid.value.rows[0]
    p.openSignature(row, 'ml')
    await tick()
    assert.equal(p.form.value.texto, '')
    p.form.value.texto = 'Atenciosamente,\nEquipe de marketplace'
    assert.equal(p.previewStale.value, true)
    await p.updatePreview()
    assert.equal(p.previewStale.value, false)
    assert.match(p.preview.value.text, /Equipe de marketplace/)
    await p.save()
    const put = calls.find(c => c.opts.method === 'PUT')
    assert.equal(put.url, '/api/email-assinaturas/m1/ml')
    assert.deepEqual(Object.keys(put.opts.body).sort(), ['ativo','incluir_dados_marca','incluir_logo','texto'])
    assert.equal(row.cells.sac.texto, 'Equipe SAC')
    assert.match(row.cells.ml.texto, /Equipe de marketplace/)
    assert.equal(p.selected.value, null)
    assert.match(p.success.value, /salva/)
    assert.equal(p.rowMatches(row, 'marketplace'), true)
    assert.ok(calls.every(c => !c.url.includes('enviar-teste')))
  }
  {
    const {p,calls} = page({edit:false})
    await p.load(); p.openSignature(p.grid.value.rows[0], 'sac'); await tick()
    await p.save()
    assert.equal(calls.filter(c => c.opts.method === 'PUT').length, 0)
    assert.equal(p.form.value.texto, 'Equipe SAC')
  }
  {
    const {p} = page({fail:true})
    await p.load(); p.openSignature(p.grid.value.rows[0], 'sac'); await tick()
    p.form.value.texto = 'Rascunho preservado'
    await p.save()
    assert.equal(p.form.value.texto, 'Rascunho preservado')
    assert.ok(p.selected.value)
    assert.equal(p.saveError.value, 'Falha ao salvar')
    assert.equal(p.grid.value.rows[0].cells.sac.texto, 'Equipe SAC')
  }
  {
    const {p,pending} = page({deferPreview:true})
    await p.load(); const row = p.grid.value.rows[0]
    p.openSignature(row, 'sac')
    p.openSignature(row, 'ml')
    pending[1](); await tick()
    assert.equal(p.preview.value.text, '')
    pending[0](); await tick()
    assert.equal(p.preview.value.text, '', 'prévia antiga não substitui o canal atual')
  }
  {
    const {p, calls, clipboardWrites} = page({edit:false})
    await p.load(); p.openSignature(p.grid.value.rows[0], 'sac'); await tick()
    await p.copySignature()
    assert.equal(clipboardWrites.length, 1)
    const item = clipboardWrites[0][0]
    const html = await (await item.getType('text/html')).text()
    assert.equal(html, p.preview.value.html, 'copia a formatação e as imagens da prévia')
    assert.match(html, /data:image\/png;base64,/)
    assert.match(html, /href="https:\/\/wa.me\//)
    assert.equal(await (await item.getType('text/plain')).text(), p.preview.value.text)
    assert.equal(p.copied.value, true)
    assert.equal(calls.filter(c => c.opts.method === 'PUT').length, 0, 'copiar não salva nem envia')
  }
  {
    const {p, clipboardWrites} = page()
    await p.load(); p.openSignature(p.grid.value.rows[0], 'sac'); await tick()
    p.form.value.texto = 'Nova assinatura'
    await p.copySignature()
    assert.equal(clipboardWrites.length, 0, 'não copia prévia desatualizada')
    await p.updatePreview(); await p.copySignature()
    assert.equal(await (await clipboardWrites[0][0].getType('text/plain')).text(), 'Nova assinatura')
    p.openSignature(p.grid.value.rows[0], 'ml'); await tick()
    assert.equal(p.copied.value, false, 'retorno de cópia não passa para outro canal')
  }
  for (const options of [{failCopy:true}, {noClipboard:true}]) {
    const {p, clipboardWrites} = page(options)
    await p.load(); p.openSignature(p.grid.value.rows[0], 'sac'); await tick()
    await p.copySignature()
    assert.equal(clipboardWrites.length, 0)
    assert.equal(p.copied.value, false)
    assert.match(p.copyError.value, /Não foi possível copiar com formatação/)
    assert.ok(p.preview.value, 'falha na cópia preserva a prévia')
  }
  console.log('Assinaturas: compilação, gravação por canal, leitura, erros, prévia concorrente e cópia formatada OK')
})().catch(e => {console.error(e);process.exitCode=1})
