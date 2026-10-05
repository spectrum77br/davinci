// node tests/email-padroes-sfc.cjs — compilação e fluxos dos e-mails do Tuta por marca.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { parse, compileTemplate, compileScript } = require('vue/compiler-sfc')
const filename = path.resolve(__dirname, '../pages/email-padroes.vue')
const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
assert.deepEqual(errors, [])
const template = compileTemplate({ source: descriptor.template.content, filename, id: 'marca-emails-check' })
assert.deepEqual(template.errors, [])
compileScript(descriptor, { id: 'marca-emails-check' })
// A matriz de assinaturas por canal saiu (Eduardo, 05/10/2026).
assert.doesNotMatch(descriptor.template.content, /Configurar|Assinatura|Copiar assinatura|iframe/)
assert.doesNotMatch(descriptor.scriptSetup.content, /email-assinaturas/)
const script = descriptor.scriptSetup.content.replace(/^import.*$/gm, '')
const transpiled = ts.transpileModule(script, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText
const factory = new Function('ref', 'computed', 'onMounted', 'definePageMeta', 'useCan', 'useApi',
  'apiErrMsg', 'MARCAS_ERROS', 'MARCA_EMAIL_TIPO_LABELS', 'navigator', 'confirm', 'setTimeout',
  transpiled + '\nreturn {grid,rascunhos,error,success,load,salvar,preencher,podePreencher,sujo,sugestao,dominio,copiar,copiado};')
const marca = (id, nome, site) => ({ id, nome, slug: nome, ativo: true, site, has_logo: false, updated_at: '2026-10-05' })
function page({ edit = true, fail = false, confirma = true } = {}) {
  const calls = [], copias = []
  const api = async (url, opts = {}) => {
    calls.push({ url, opts })
    if (url === '/api/marca-emails') return {
      tipos: ['sac', 'duvidas', 'atacado'],
      rows: [
        { marca: marca('m1', 'uranyx', 'https://www.uranyx.com.br/'), emails: { sac: 'sac@uranyx.com.br', duvidas: null, atacado: null } },
        { marca: marca('m2', 'locagil', null), emails: { sac: null, duvidas: null, atacado: null } },
      ],
    }
    if (opts.method === 'PUT') {
      if (fail) throw new Error('Falha ao salvar')
      return { marca: marca(url.split('/').pop(), 'x', null), emails: { ...opts.body } }
    }
    throw new Error(`Chamada inesperada: ${url}`)
  }
  const navigator = { clipboard: { writeText: async t => { copias.push(t) } } }
  const p = factory(Vue.ref, Vue.computed, () => {}, () => {}, () => Vue.ref(edit), () => ({ api }),
    e => e.message, {}, { sac: 'SAC', duvidas: 'Dúvidas', atacado: 'Atacado' }, navigator, () => confirma, () => 0)
  return { p, calls, copias }
}
;(async () => {
  {
    const { p, calls } = page()
    await p.load()
    const [uranyx, locagil] = p.grid.value.rows
    assert.equal(p.dominio('https://www.uranyx.com.br/'), 'uranyx.com.br')
    assert.equal(p.sugestao(uranyx.marca, 'atacado'), 'atacado@uranyx.com.br')
    assert.equal(p.rascunhos.value.m1.sac, 'sac@uranyx.com.br')
    assert.equal(p.rascunhos.value.m1.duvidas, '')
    assert.equal(p.sujo(uranyx), false)
    // "padrão" só preenche os vazios e só quando a marca tem site.
    assert.equal(p.podePreencher(uranyx), true)
    assert.equal(p.podePreencher(locagil), false)
    p.rascunhos.value.m1.sac = 'contato@uranyx.com.br'
    p.preencher(uranyx)
    assert.deepEqual({ ...p.rascunhos.value.m1 }, { sac: 'contato@uranyx.com.br', duvidas: 'duvidas@uranyx.com.br', atacado: 'atacado@uranyx.com.br' })
    assert.equal(p.sujo(uranyx), true)
    p.rascunhos.value.m1.sac = ' SAC@Uranyx.com.br '
    p.rascunhos.value.m1.atacado = ''
    await p.salvar(uranyx)
    const put = calls.find(c => c.opts.method === 'PUT')
    assert.equal(put.url, '/api/marca-emails/m1')
    assert.deepEqual(put.opts.body, { sac: 'sac@uranyx.com.br', duvidas: 'duvidas@uranyx.com.br', atacado: null })
    assert.equal(uranyx.emails.duvidas, 'duvidas@uranyx.com.br')
    assert.equal(p.sujo(uranyx), false)
    assert.match(p.success.value, /uranyx/)
    // Linha sem mudança não salva.
    await p.salvar(locagil)
    assert.equal(calls.filter(c => c.opts.method === 'PUT').length, 1)
  }
  {
    const { p, calls } = page({ edit: false })
    await p.load()
    p.rascunhos.value.m2.sac = 'sac@locagil.com.br'
    await p.salvar(p.grid.value.rows[1])
    assert.equal(calls.filter(c => c.opts.method === 'PUT').length, 0)
  }
  {
    const { p } = page({ fail: true })
    await p.load()
    const row = p.grid.value.rows[1]
    p.rascunhos.value.m2.sac = 'sac@locagil.com.br'
    await p.salvar(row)
    assert.equal(p.rascunhos.value.m2.sac, 'sac@locagil.com.br')
    assert.equal(row.emails.sac, null)
    assert.match(p.error.value, /locagil: Falha ao salvar/)
  }
  {
    const { p, calls } = page({ confirma: false })
    await p.load()
    p.rascunhos.value.m2.sac = 'sac@locagil.com.br'
    await p.load()
    assert.equal(calls.filter(c => c.url === '/api/marca-emails').length, 1)
    assert.equal(p.rascunhos.value.m2.sac, 'sac@locagil.com.br')
  }
  {
    const { p, copias } = page()
    await p.load()
    await p.copiar('sac@uranyx.com.br')
    assert.deepEqual(copias, ['sac@uranyx.com.br'])
    assert.equal(p.copiado.value, 'sac@uranyx.com.br')
  }
  console.log('email-padroes-sfc: ok')
})().catch(e => { console.error(e); process.exit(1) })
