// A lista de usuários do e-mail (12/11/2026): DOM real, API controlada, nenhum e-mail real.
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path')
const { JSDOM } = require('jsdom')
const { window } = new JSDOM('<!doctype html><html><body></body></html>', { url: 'https://davinci.invalid/' })
for (const n of ['window', 'document', 'Document', 'ShadowRoot', 'Element', 'HTMLElement', 'HTMLInputElement', 'HTMLSelectElement', 'SVGElement', 'Node', 'Text', 'Comment', 'Event', 'MouseEvent', 'KeyboardEvent']) globalThis[n] = n === 'window' ? window : window[n]
const ts = require('typescript'), Vue = require('vue'), { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')
const transpile = (s) => ts.transpileModule(s, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText
const icons = new Proxy({}, { get: (_, n) => ({ render: () => Vue.h('i', { 'data-icon': String(n) }) }) })
let responder
function carregar(nome) {
  const filename = path.resolve(__dirname, '../components', nome)
  const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
  assert.deepEqual(errors, [])
  assert.deepEqual(compileTemplate({ source: descriptor.template.content, filename, id: nome, compilerOptions: { isTS: true } }).errors, [])
  const source = compileScript(descriptor, { id: nome, inlineTemplate: true }).content
  const globals = { ref: Vue.ref, computed: Vue.computed, reactive: Vue.reactive, watch: Vue.watch, onMounted: Vue.onMounted, onBeforeUnmount: Vue.onBeforeUnmount, useApi: () => ({ api: (...a) => responder(...a) }) }
  const mod = { exports: {} }
  const req = (n) => n === 'lucide-vue-next' ? icons : n === '~/components/AtendimentoPlataforma.vue' ? { erroDaApi: (e, p) => ({ texto: p }) } : require(n)
  new Function('require', 'module', 'exports', ...Object.keys(globals), transpile(source))(req, mod, mod.exports, ...Object.values(globals))
  return mod.exports
}
const tick = async () => { for (let i = 0; i < 3; i++) { await new Promise((r) => setTimeout(r, 0)); await Vue.nextTick() } }
const click = async (el) => { assert.ok(el, 'elemento existe'); el.dispatchEvent(new window.MouseEvent('click', { bubbles: true })); await tick() }
const caixas = [{ id: 'b1', label: 'Principal — Tuta', pastas: [{ id: 's1', nome: 'Entrada', tipo: 'sistema', total: 9 }, { id: 'pinfo', nome: 'informativo', tipo: 'pessoal', total: 5 }, { id: 'psite', nome: '*uranyx sac', tipo: 'pessoal', total: 3 }] }, { id: 'b2', label: 'Goslin — Tuta', pastas: [{ id: 's1', nome: 'Entrada', tipo: 'sistema', total: 2 }] }]
const usuario = (o) => ({ id: 'u1', login: 'luigii12@tuta.com', nome: 'israel', admin: false, ativo: true, ligado: false, caixas: [], pastas: {}, todas: false, dono_de: [], criado_em: '2026-05-15T15:18:00Z', ultimo_acesso: '2026-11-12T13:00:00Z', ...o })
async function run() {
  const M = carregar('AtendimentoMailUsuarios.vue')
  assert.equal(M.permissaoDeCaixas(usuario({ admin: true })), 'Todas as caixas')
  assert.equal(M.permissaoDeCaixas(usuario({ caixas: ['b1'] })), 'Caixas específicas')
  assert.equal(M.permissaoDeCaixas(usuario({})), 'Nenhuma caixa')
  assert.match(M.dataHora('2026-05-15T15:18:00Z'), /^\d\d\/\d\d\/2026 \d\d:\d\d$/)
  let lista = { caixas, usuarios: [usuario({ id: 'a1', login: 'cheng28@tuta.com', nome: 'Thorfinn', admin: true, ligado: true, caixas: ['b1', 'b2'], todas: true }), usuario({})] }
  const chamadas = []
  responder = async (url, opts = {}) => {
    chamadas.push({ url, opts })
    if (url === '/api/atendimento/email/admin/usuarios') return lista
    const m = /usuarios\/(.+)$/.exec(url)
    if (m && opts.method === 'PUT') { const cx = opts.body.caixas; const pastas = Object.fromEntries(cx.filter((c) => c.pastas).map((c) => [c.id, c.pastas])); return usuario({ id: m[1], ligado: opts.body.ligado, caixas: cx.map((c) => c.id), pastas, todas: cx.length === 2 && !Object.keys(pastas).length }) }
    throw Error(url)
  }
  const el = document.createElement('div'); document.body.appendChild(el)
  const mudou = []
  const app = Vue.createApp({ render: () => Vue.h(M.default, { onMudou: () => mudou.push(1) }) })
  const avisos = []; app.config.warnHandler = (m) => avisos.push(m)
  app.mount(el); await tick()
  const linhas = el.querySelectorAll('[data-usuario]')
  assert.equal(linhas.length, 2)
  const [admin, israel] = linhas
  assert.match(admin.textContent, /cheng28@tuta\.com.*Thorfinn.*admin.*Todas as caixas/s)
  assert.ok(admin.querySelector('[data-usuario-estado]').disabled, 'admin: estado fixo')
  assert.ok(admin.querySelector('[data-usuario-editar]').disabled, 'admin: sem Editar')
  assert.match(israel.querySelector('[data-usuario-permissao]').textContent, /Nenhuma caixa/)
  // Ligar pelo toggle mantém as caixas.
  await click(israel.querySelector('[data-usuario-estado]'))
  const put = chamadas.at(-1)
  assert.equal(put.opts.method, 'PUT'); assert.deepEqual(put.opts.body, { ligado: true, caixas: [] })
  assert.equal(el.querySelector('[data-usuario="u1"] [data-usuario-estado]').getAttribute('aria-checked'), 'true')
  // Editar: as contas com as pastas em checkbox; marca 2 pastas da Principal e a Goslin inteira.
  await click(el.querySelector('[data-usuario="u1"] [data-usuario-editar]'))
  const dialogo = el.querySelector('[data-usuario-dialogo]')
  assert.ok(dialogo, 'abre o Editar')
  assert.equal(dialogo.querySelectorAll('[data-dialogo-caixa]').length, 2)
  assert.equal(dialogo.querySelectorAll('[data-dialogo-pasta]').length, 4)
  assert.match(dialogo.textContent, /informativo.*\*uranyx sac/s)
  const muda = async (sel, v) => { const x = el.querySelector(sel); assert.ok(x, sel); x.checked = v; x.dispatchEvent(new window.Event('change', { bubbles: true })); await tick() }
  await muda('[data-dialogo-pasta="b1:pinfo"]', true)
  await muda('[data-dialogo-pasta="b1:psite"]', true)
  await muda('[data-dialogo-caixa="b2"]', true)
  assert.ok(el.querySelector('[data-dialogo-pasta="b2:s1"]').checked, 'conta inteira marca as pastas dela')
  el.querySelector('[data-usuario-dialogo] form').dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true })); await tick()
  assert.deepEqual(chamadas.at(-1).opts.body, { ligado: true, caixas: [{ id: 'b1', pastas: ['pinfo', 'psite'] }, { id: 'b2' }] })
  assert.equal(el.querySelector('[data-usuario-dialogo]'), null, 'fecha ao salvar')
  assert.match(el.querySelector('[data-usuario="u1"] [data-usuario-permissao]').textContent, /Caixas específicas\s*\(2\).*Principal — Tuta: 2 pasta\(s\).*Goslin — Tuta: conta inteira/s)
  // Reabrir mostra o que foi gravado; desmarcar a única pasta da conta inteira tira a conta.
  await click(el.querySelector('[data-usuario="u1"] [data-usuario-editar]'))
  assert.ok(el.querySelector('[data-dialogo-pasta="b1:pinfo"]').checked && !el.querySelector('[data-dialogo-pasta="b1:s1"]').checked)
  await muda('[data-dialogo-pasta="b2:s1"]', false)
  el.querySelector('[data-usuario-dialogo] form').dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true })); await tick()
  assert.deepEqual(chamadas.at(-1).opts.body, { ligado: true, caixas: [{ id: 'b1', pastas: ['pinfo', 'psite'] }] })
  // "Todas as caixas" marca todas.
  await click(el.querySelector('[data-usuario="u1"] [data-usuario-editar]'))
  const todas = el.querySelector('[data-dialogo-todas]'); todas.checked = true; todas.dispatchEvent(new window.Event('change', { bubbles: true })); await tick()
  el.querySelector('[data-usuario-dialogo] form').dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true })); await tick()
  assert.deepEqual(chamadas.at(-1).opts.body, { ligado: true, caixas: [{ id: 'b1' }, { id: 'b2' }] })
  assert.match(el.querySelector('[data-usuario="u1"] [data-usuario-permissao]').textContent, /Todas as caixas/)
  assert.equal(mudou.length, 4)
  assert.deepEqual(avisos, [])
  app.unmount(); el.remove()
  console.log('ok — atendimento-mail-usuarios: lista, estado, editar com contas e pastas e "todas"')
}
run().catch((e) => { console.error(e); process.exit(1) })
