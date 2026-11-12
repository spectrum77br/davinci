// A parte Agente da aba E-mail (12/11/2026): DOM real, API controlada, nenhum e-mail real.
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
const site = (o) => ({ site: 'uranyx', nome: 'Uranyx', canal_id: 'c1', ligado: false, modo: null, formulario: 'https://uranyx.com.br/sac', ...o })
const fixa = { id: 'r1', chave: 'formulario_sac', titulo: 'Reclamação no SAC sem o formulário', instrucao: 'Peça o formulário.', ativa: true, sites: [], responde_sozinho: true, ordem: 0, fixa: true, atualizada_em: null }
async function run() {
  const M = carregar('AtendimentoMailAgente.vue')
  assert.equal(M.estadoDoSite(site({})), 'desligado')
  assert.equal(M.estadoDoSite(site({ ligado: true, modo: 'auto' })), 'auto')
  assert.equal(M.rotuloDoModo('copiloto'), 'só sugere')
  let dados = { disponivel: true, sites: [site({}), site({ site: 'charlots', nome: 'Charlots', canal_id: 'c2', formulario: 'https://charlots.com.br/sac' })], lojas: [{ canal_id: 'k1', plataforma: 'shopee', loja: 'Kia', modo: 'auto' }], regras: [fixa] }
  const chamadas = []
  window.confirm = () => true
  responder = async (url, opts = {}) => {
    chamadas.push({ url, opts })
    if (url === '/api/atendimento/email/agente' && !opts.method) return dados
    const s = /sites\/(\w+)$/.exec(url)
    if (s && opts.method === 'PUT') { dados = { ...dados, sites: dados.sites.map((x) => (x.site === s[1] ? { ...x, ligado: !!opts.body.modo, modo: opts.body.modo } : x)) }; return dados }
    if (url.endsWith('/regras') && opts.method === 'POST') return { ...fixa, id: 'r2', chave: null, fixa: false, ...opts.body, ativa: true }
    const r = /regras\/(\w+)$/.exec(url)
    if (r && opts.method === 'PATCH') return { ...(r[1] === 'r1' ? fixa : { ...fixa, id: 'r2', chave: null, fixa: false }), ...opts.body }
    if (r && opts.method === 'DELETE') return null
    throw Error(url)
  }
  const el = document.createElement('div'); document.body.appendChild(el)
  const app = Vue.createApp({ render: () => Vue.h(M.default) })
  const avisos = []; app.config.warnHandler = (m) => avisos.push(m)
  app.mount(el); await tick()
  // Os dois sites, desligados; a loja pela chave do atendimento; a regra do sistema sem apagar.
  assert.equal(el.querySelectorAll('[data-agente-site]').length, 2)
  const uranyx = el.querySelector('[data-agente-site="uranyx"]')
  assert.match(uranyx.textContent, /SAC Uranyx.*desligado/s)
  assert.match(uranyx.textContent, /uranyx\.com\.br\/sac/)
  // Só os sites: as lojas não aparecem.
  assert.equal(el.querySelector('[data-agente-lojas]'), null)
  assert.ok(el.querySelector('[data-agente-so-sites]'))
  assert.equal(M.ondeVale([]), 'Uranyx e Charlots')
  assert.equal(M.ondeVale(['charlots']), 'Charlots')
  const regra = el.querySelector('[data-agente-regra="r1"]')
  assert.match(regra.textContent, /do sistema/)
  assert.match(regra.querySelector('[data-agente-regra-onde]').textContent, /Uranyx e Charlots/)
  assert.match(regra.querySelector('[data-agente-regra-sozinho]').textContent, /responde sozinho/)
  assert.equal(regra.querySelector('[aria-label^="Apagar"]'), null)
  // Responde sozinho no SAC da Uranyx.
  await click(uranyx.querySelector('[data-agente-site-estado="auto"]'))
  assert.deepEqual(chamadas.at(-1).opts.body, { modo: 'auto' })
  assert.match(el.querySelector('[data-agente-site="uranyx"]').textContent, /responde sozinho/)
  // Desligar a regra do sistema.
  await click(el.querySelector('[data-agente-regra="r1"] [data-agente-regra-ativa]'))
  assert.deepEqual(chamadas.at(-1).opts.body, { ativa: false })
  assert.equal(el.querySelector('[data-agente-regra="r1"] [data-agente-regra-ativa]').getAttribute('aria-checked'), 'false')
  // Nova regra.
  await click(el.querySelector('[data-agente-nova-regra]'))
  const form = el.querySelector('[data-agente-form-nova]')
  form.querySelector('input').value = 'Nota fiscal'; form.querySelector('input').dispatchEvent(new window.Event('input'))
  form.querySelector('textarea').value = 'Diga que a nota vai no e-mail.'; form.querySelector('textarea').dispatchEvent(new window.Event('input'))
  const so = form.querySelector('[data-agente-nova-site="charlots"]'); so.checked = true; so.dispatchEvent(new window.Event('change'))
  const soz = form.querySelector('[data-agente-nova-sozinho]'); soz.checked = true; soz.dispatchEvent(new window.Event('change'))
  form.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true })); await tick()
  assert.equal(chamadas.at(-1).opts.method, 'POST')
  assert.deepEqual(chamadas.at(-1).opts.body, { titulo: 'Nota fiscal', instrucao: 'Diga que a nota vai no e-mail.', sites: ['charlots'], responde_sozinho: true })
  assert.ok(el.querySelector('[data-agente-regra="r2"]'))
  // Servidor sem o agente: só "Desligado" fica clicável.
  dados = { ...dados, disponivel: false }
  await click(el.querySelector('[data-agente-recarregar]'))
  assert.ok(el.querySelector('[data-agente-indisponivel]'))
  assert.ok(el.querySelector('[data-agente-site="charlots"] [data-agente-site-estado="auto"]').disabled)
  assert.deepEqual(avisos, [])
  app.unmount()
  console.log('atendimento-mail-agente: ok')
}
run().catch((e) => { console.error(e); process.exit(1) })
