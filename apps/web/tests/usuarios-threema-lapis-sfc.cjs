// node tests/usuarios-threema-lapis-sfc.cjs — Configurações › Usuários, o
// lápis do Threema na lista (tarefa 16, teste no navegador de 19/11).
// O campo salva no Enter e no blur. O Enter fecha o campo, e o blur que vem
// logo depois (o campo sumindo, ou a janela de confirmação roubando o foco)
// chamava o salvar DE NOVO: dois avisos "Fulano recebe N envios", e
// cancelar o primeiro não adiantava (o segundo salvava). Esc fechava o
// campo e o blur salvava assim mesmo. Sem rede: o setup de verdade da
// página com a API falsa.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { parse, compileScript } = require('vue/compiler-sfc')

const transpile = (source) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText

function sfc(rel) {
  const filename = path.resolve(__dirname, rel)
  const fonte = fs.readFileSync(filename, 'utf8')
  const { descriptor, errors } = parse(fonte, { filename })
  assert.deepEqual(errors, [], rel)
  return { descriptor, fonte, filename }
}

const icones = new Proxy({}, { get: (_, nome) => ({ name: String(nome), render: () => Vue.h('i') }) })
const libs = new Map()
// `await` no topo do setup da página: sem instância de componente aqui.
const vueDoTeste = { ...Vue, withAsyncContext: (fn) => [fn(), () => {}] }
function requerer(nome) {
  if (nome === 'lucide-vue-next') return icones
  if (nome === 'vue') return vueDoTeste
  const m = /^~\/lib\/(.+)$/.exec(nome)
  if (!m) return require(nome)
  if (!libs.has(m[1])) {
    const exp = {}
    const fonte = fs.readFileSync(path.resolve(__dirname, '../lib', `${m[1]}.ts`), 'utf8')
    new Function('exports', 'require', transpile(fonte))(exp, requerer)
    libs.set(m[1], exp)
  }
  return libs.get(m[1])
}
const confirmacoes = []
let responderConfirm = true
globalThis.confirm = (t) => { confirmacoes.push(t); return responderConfirm }

;(async () => {
  {
    // Usuários › lápis do Threema: Enter salva e o blur que vem logo depois
    // (o campo sumindo, ou a janela de confirmação roubando o foco) NÃO salva
    // de novo — antes saíam dois avisos e cancelar o primeiro não adiantava.
    // Esc fecha o campo sem salvar.
    const rel = '../pages/users/index.vue'
    const { descriptor } = sfc(rel)
    const script = compileScript(descriptor, { id: 'users-index', inlineTemplate: false })
    const chamadas = []
    const USER = { id: 'u-c', email: 'c@x', name: 'cairo sa', role: 'user', status: 'active', threema: 'TSTC0001' }
    const g = {
      definePageMeta: () => {},
      useApi: () => ({
        api: async (url, opts = {}) => {
          chamadas.push({ url, method: opts.method || 'GET', body: opts.body })
          if (url.startsWith('/api/users?')) return { items: [USER], total: 1, page: 1, per_page: 100 }
          if (url.endsWith('/envios')) {
            return { usuario: { id: 'u-c', nome: 'cairo sa', tem_threema: true }, envios: [{ chave: 'a', nome: 'A', painel: 'P', onde_roda: 'servidor', via: 'legado', unico: false, grupo: null, onde: 'X' }], total: 1, unicos: 0, pela_configuracao_antiga: 1, envios_do_mac_no_env: 0 }
          }
          return {}
        },
      }),
    }
    const nomes = Object.keys(g)
    const mod = { exports: {} }
    new Function('require', 'module', 'exports', ...nomes, transpile(script.content))(requerer, mod, mod.exports, ...nomes.map((n) => g[n]))
    const estado = Vue.proxyRefs(await mod.exports.default.setup({}, { emit: () => {}, expose: () => {}, attrs: {}, slots: {} }))
    const u = estado.list.items[0]
    const n0 = confirmacoes.length
    responderConfirm = false
    estado.startEditThreema(u)
    estado.threemaDraft = ''
    await Promise.all([estado.saveThreema(u), estado.saveThreema(u)]) // Enter + blur
    assert.equal(confirmacoes.length - n0, 1, 'um aviso só')
    assert.ok(!chamadas.some((c) => c.method === 'PATCH'), 'cancelou: nada salvo')
    responderConfirm = true
    estado.startEditThreema(u)
    estado.threemaDraft = ''
    await Promise.all([estado.saveThreema(u), estado.saveThreema(u)])
    assert.equal(chamadas.filter((c) => c.method === 'PATCH').length, 1, 'confirmou: salva uma vez')
    // Esc: fecha sem salvar (o blur depois não salva)
    estado.startEditThreema({ ...u, threema: null })
    estado.threemaDraft = 'ABCD1234'
    estado.editingThreema = null
    await estado.saveThreema(u)
    assert.equal(chamadas.filter((c) => c.method === 'PATCH').length, 1)
  }

  console.log('usuarios-threema-lapis-sfc: ok')
})().catch((e) => { console.error(e); process.exit(1) })
