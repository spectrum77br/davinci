// Admin-only store mutations, without changing view access or other resources.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { parse } = require('vue/compiler-sfc')
const root = path.resolve(__dirname, '..')
const transpile = (text) => ts.transpileModule(text, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText
const auth = Vue.reactive({ user: { role: 'user', permissions: { lojas_info: { view: true, edit: true, delete: true }, empresa: { view: true, edit: true, delete: true }, cadastro: { view: true, edit: true, delete: true } } } })
const permissionModule = { exports: {} }
new Function('require', 'module', 'exports', 'useAuthStore', transpile(fs.readFileSync(path.join(root, 'composables/useCan.ts'), 'utf8')))(require, permissionModule, permissionModule.exports, () => auth)
const { useCan } = permissionModule.exports
const view = useCan('lojas_info', 'view'), edit = useCan('lojas_info', 'edit'), remove = useCan('lojas_info', 'delete')
const companyEdit = useCan('empresa', 'edit'), cadastroEdit = useCan('cadastro', 'edit')
for (const role of ['user', 'operator', 'manager']) {
  auth.user.role = role
  assert.equal(view.value, true, 'existing explicit view grants survive')
  assert.equal(edit.value, false, `${role} cannot use legacy store edit grants`)
  assert.equal(remove.value, false, `${role} cannot use legacy store delete grants`)
  assert.equal(companyEdit.value, true, 'company operations outside stores keep their own permission')
}
auth.user.permissions.lojas_info.view = false
assert.equal(view.value, false, 'read access is never broadened')
auth.user.role = 'admin'
assert.equal(view.value, true); assert.equal(edit.value, true); assert.equal(remove.value, true)
auth.user = null
assert.equal(view.value, false); assert.equal(edit.value, false); assert.equal(remove.value, false)

function pageFunctions(file, names, globals) {
  const filename = path.join(root, file)
  const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
  assert.deepEqual(errors, [])
  const source = ts.createSourceFile(filename, descriptor.scriptSetup.content, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
  const snippets = names.map((name) => {
    const fn = source.statements.find((item) => ts.isFunctionDeclaration(item) && item.name?.text === name)
    assert.ok(fn, `${file}: ${name} exists`)
    return fn.getText(source)
  })
  return new Function(...Object.keys(globals), transpile(snippets.join('\n')) + `\nreturn {${names.join(',')}}`)(...Object.values(globals))
}
async function run() {
  const calls = [], confirmations = []
  auth.user = { role: 'user', permissions: { lojas_info: { view: true, edit: true, delete: true }, empresa: { view: true, edit: true, delete: true }, cadastro: { view: true, edit: true, delete: true } } }
  const globals = {
    isAdmin: Vue.computed(() => auth.user?.role === 'admin'),
    podeTrocarEmail: Vue.computed(() => companyEdit.value && cadastroEdit.value && edit.value),
    apiE: async (...args) => calls.push(args), confirm: (message) => { confirmations.push(message); return true },
  }
  // A non-admin with broad old permissions may still read companies, but no
  // indirect account path may create/delete/change StoreInfo data.
  const index = pageFunctions('pages/companies/index.vue', ['removeStoreCell', 'abrirTrocaEmail', 'salvarEmail'], globals)
  const detail = pageFunctions('pages/companies/[id].vue', ['deleteStore'], globals)
  const row = { company: { id: 'company', apelido: 'Company' }, stores: { ml: { id: 'store' } } }
  await index.removeStoreCell(row, 'ml')
  await index.abrirTrocaEmail(row, 'ml')
  await index.salvarEmail(row, 'ml')
  await detail.deleteStore({ id: 'store', marketplace: 'ml' })
  assert.equal(calls.length, 0)
  assert.equal(confirmations.length, 0)
  console.log('store-info permissions: view scope and other resources unchanged; legacy store edit/delete and indirect account handlers blocked.')
}
run().catch((error) => { console.error(error); process.exitCode = 1 })
