// node tests/atendimento-mail-leitores.cjs — "QUEM MAIS VÊ" uma caixa da
// Central de e-mail (09/10/2026; Eduardo: "deixe o usuário israel ver a aba de
// e-mail agora"). Sem rede, sem caixa de verdade, sem e-mail de verdade:
//  - o contrato com o backend (as rotas GET/PUT .../leitores, o `so_leitura`
//    da lista de caixas, a regra de ler separada da de escrever);
//  - as funções puras do seletor (AtendimentoMailLeitores);
//  - o componente de verdade (setup + Vue SSR, API falsa): carregar, marcar,
//    confirmar, salvar, erro;
//  - a aba E-mail para o LEITOR: vê a caixa, a lista e o e-mail aberto, sem
//    responder, sem Saiu/Não saiu, sem Configurar, sem trocar envio; com o
//    aviso "você só vê esta caixa"; sem Filas (não mexe).
// O harness (montar/sfc/requerer) é o mesmo de atendimento-mail-atendimento.cjs.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { renderToString } = require('vue/server-renderer')
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')

const transpile = (source) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText

function sfc(rel) {
  const filename = path.resolve(__dirname, rel)
  const fonte = fs.readFileSync(filename, 'utf8')
  const { descriptor, errors } = parse(fonte, { filename })
  assert.deepEqual(errors, [], rel)
  const tpl = compileTemplate({ source: descriptor.template.content, filename, id: path.basename(rel), compilerOptions: { isTS: true } })
  assert.deepEqual(tpl.errors, [], `${rel}: template compila`)
  return { descriptor, fonte, filename }
}
const cache = new Map()
const icones = new Proxy({}, { get: (_, nome) => ({ name: String(nome), render: () => Vue.h('i', { 'data-icone': String(nome) }) }) })
function requerer(nome) {
  if (nome === 'lucide-vue-next') return icones
  const m = /^~\/components\/(.+\.vue)$/.exec(nome)
  if (!m) return require(nome)
  if (!cache.has(m[1])) cache.set(m[1], exportsDe(sfc(`../components/${m[1]}`).descriptor))
  return cache.get(m[1])
}
function exportsDe(descriptor) {
  const mod = { exports: {} }
  if (!descriptor.script) return mod.exports
  new Function('require', 'module', 'exports', transpile(descriptor.script.content))(requerer, mod, mod.exports)
  return mod.exports
}
function trecho(src, ini, fim) {
  const a = src.indexOf(ini)
  const b = src.indexOf(fim, a + 1)
  assert.ok(a >= 0 && b > a, `trecho ${ini} … ${fim}`)
  return transpile(src.slice(a, b))
}
const api = (rel) => fs.readFileSync(path.resolve(__dirname, '../../api/app', rel), 'utf8')
const web = (rel) => fs.readFileSync(path.resolve(__dirname, '..', rel), 'utf8')
const semComentarios = (html) => html.replace(/<!--[\s\S]*?-->/g, '')
const esperar = () => new Promise((r) => setTimeout(r, 0))

// ------------------------------------------------ montar um componente de verdade
// O setup de verdade roda fora de um app (como no teste da Central: os
// `watch` ficam vivos — no SSR eles não reagem); o defineModel vira um ref
// local. O HTML sai de um app SSR que usa o MESMO estado.
function montar(rel, props, { respostas = {}, confirmar = true, filhos = {} } = {}) {
  const { descriptor, filename } = sfc(rel)
  const script = compileScript(descriptor, { id: path.basename(rel), inlineTemplate: false })
  const tpl = compileTemplate({ source: descriptor.template.content, filename, id: path.basename(rel), compilerOptions: { isTS: true, bindingMetadata: script.bindings } })
  const chamadas = []
  const perguntas = []
  const avisos = []
  const emitidos = []
  const montados = []
  const responder = async (url, opts = {}) => {
    chamadas.push({ url, metodo: opts.method || 'GET', body: opts.body })
    for (const [chave, valor] of Object.entries(respostas)) {
      if (url === chave || url.startsWith(`${chave}?`)) {
        const v = typeof valor === 'function' ? valor(url, opts) : valor
        if (v instanceof Error) throw v
        return structuredClone(v)
      }
    }
    if (/\/(lista|pastas)(?:\?|$)/.test(url)) throw Object.assign(new Error('Older API route missing'), { status: 404 })
    throw new Error(`rota inesperada: ${url}`)
  }
  const pergunta = (t) => { perguntas.push(t); return typeof confirmar === 'function' ? confirmar(t) : confirmar }
  const g = {
    ref: Vue.ref, computed: Vue.computed, reactive: Vue.reactive, watch: Vue.watch, nextTick: Vue.nextTick,
    onMounted: (fn) => montados.push(fn), onBeforeUnmount: () => {},
    useApi: () => ({ url: (v) => v, api: responder }),
    useToasts: () => ({ success: (t) => avisos.push(['ok', t]), error: (t) => avisos.push(['erro', t]), info: (t) => avisos.push(['info', t]), warning: (t) => avisos.push(['aviso', t]) }),
    confirm: pergunta,
    window: { addEventListener: () => {}, removeEventListener: () => {}, confirm: pergunta },
    crypto: { randomUUID: () => '00000000-0000-4000-8000-000000000000' },
    document: { hidden: false, addEventListener: () => {}, removeEventListener: () => {} }, setInterval: () => 1, clearInterval: () => {}, setTimeout: () => 1, clearTimeout: () => {},
  }
  const nomes = Object.keys(g)
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', ...nomes, transpile(script.content))(requerer, mod, mod.exports, ...nomes.map((n) => g[n]))
  const tmod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(tpl.code))(requerer, tmod, tmod.exports)
  const comp = mod.exports.default
  const reativo = Vue.reactive({ ...props })
  // "useModel() called without active instance": esperado aqui (vira ref local).
  const aviso = console.warn
  console.warn = () => {}
  let bruto
  try {
    bruto = comp.setup(reativo, { emit: (...a) => emitidos.push(a), expose: () => {}, attrs: {}, slots: {} })
  } finally {
    console.warn = aviso
  }
  const estado = Vue.proxyRefs(bruto)
  const filho = { ...comp, render: tmod.exports.render, setup: () => bruto }
  async function html() {
    const a = Vue.createSSRApp({ render: () => Vue.h(filho, reativo) })
    a.config.warnHandler = () => {}
    a.component('Button', { props: ['disabled'], setup: (p, { slots }) => () => Vue.h('button', { disabled: p.disabled }, slots.default?.()) })
    for (const [n, c] of Object.entries(filhos)) a.component(n, c)
    return semComentarios(await renderToString(a))
  }
  return {
    html, estado, chamadas, perguntas, avisos, emitidos, montados, reativo,
    async montar() { for (const fn of montados) await fn(); await esperar() },
  }
}

const L = requerer('~/components/AtendimentoMailLeitores.vue')

async function principal() {
  // ------------------------------------------------ contrato com o backend
  {
    const rotas = api('routers/mail_atendimento.py')
    assert.match(rotas, /@router\.get\("\/mailboxes\/\{mailbox_id\}\/leitores"\)/)
    assert.match(rotas, /@router\.put\("\/mailboxes\/\{mailbox_id\}\/leitores"\)/)
    assert.match(rotas, /pode_mexer\(user\)[\s\S]*atendimento_permission_required/)
    const svc = api('services/mail_atendimento/leitores.py')
    for (const code of ['leitor_inativo', 'leitores_demais']) {
      assert.match(svc, new RegExp(`"${code}"`), code)
      assert.ok(L.ERROS_LEITORES[code], `texto para ${code}`)
    }
    for (const chave of ['"leitores"', '"candidatos"', '"atualizado_por"', '"atualizado_em"', '"ativo"', '"nome"']) assert.ok(svc.includes(chave), chave)
    const central = api('routers/mail.py')
    // Ler: a regra de leitores; escrever: o allowed() dele (user_mailbox).
    assert.match(central, /"so_leitura": True/)
    assert.match(central, /async def readable_mailbox[\s\S]*pode_ver_caixa/)
    assert.match(central, /def list_messages[\s\S]*?await readable_mailbox\(/)
    assert.match(central, /def get_message[\s\S]*?user_message\(session, message_id, user, read=True\)/)
    assert.match(central, /def download_attachment[\s\S]*?read=True\)/)
    assert.match(central, /def reply\([\s\S]*?user_message\(session, message_id, user, lock=True\)\n/)
    assert.match(central, /def resolve_outbox[\s\S]*?service\.allowed\(mailbox, user\)/)
  }

  // ------------------------------------------------ funções puras
  {
    const v = { mailbox_id: 'b', leitores: [{ id: 'u9', nome: 'Zé', ativo: false }, { id: 'u1', nome: 'Israel', ativo: true }], candidatos: [{ id: 'u1', nome: 'Israel' }, { id: 'u2', nome: 'Ana' }], atualizado_por: null, atualizado_em: null }
    assert.deepEqual(L.opcoesDeLeitores(v).map((o) => [o.id, o.ativo]), [['u2', true], ['u1', true], ['u9', false]])
    assert.equal(L.mesmaLista(['a', 'b'], ['b', 'a']), true)
    assert.equal(L.mesmaLista(['a'], ['a', 'b']), false)
    assert.equal(L.mesmaLista([], []), true)
  }

  // ------------------------------------------------ o seletor "Quem mais vê"
  {
    const caixa = { id: 'box1', label: 'Principal 061' }
    const rota = '/api/mail/mailboxes/box1/leitores'
    const visao = { mailbox_id: 'box1', leitores: [], candidatos: [{ id: 'isr', nome: 'Israel' }, { id: 'ana', nome: 'Ana' }], atualizado_por: null, atualizado_em: null }
    let confirma = false
    const m = montar('../components/AtendimentoMailLeitores.vue', { mailbox: caixa }, {
      respostas: { [rota]: (url, o) => (o.method === 'PUT' ? { ...visao, leitores: o.body.leitores.map((id) => ({ id, nome: 'Israel', ativo: true })), atualizado_por: { id: 'adm', nome: 'Thorfinn' }, atualizado_em: '2026-10-09T12:00:00Z' } : visao) },
      confirmar: () => confirma,
    })
    await m.montar()
    let html = await m.html()
    assert.match(html, /Quem mais vê \(só leitura\)/)
    assert.match(html, /data-leitor="isr"[\s\S]*Israel/)
    assert.match(html, /Ninguém além do dono e dos admins/)
    assert.equal(m.estado.mudou, false)
    await m.estado.salvar()
    assert.equal(m.chamadas.filter((c) => c.metodo === 'PUT').length, 0, 'sem mudança: nada')
    m.estado.alternar('isr', true)
    assert.equal(m.estado.mudou, true)
    await m.estado.salvar()
    assert.equal(m.chamadas.filter((c) => c.metodo === 'PUT').length, 0, 'desistiu: nada')
    assert.match(m.perguntas[0], /Israel ver a caixa "Principal 061" inteira \(só leitura/)
    confirma = true
    await m.estado.salvar()
    const put = m.chamadas.find((c) => c.metodo === 'PUT')
    assert.deepEqual([put.url, put.body], [rota, { leitores: ['isr'] }])
    assert.deepEqual(m.emitidos, [['mudou']])
    assert.deepEqual(m.avisos, [['ok', 'Quem vê a caixa: salvo']])
    html = await m.html()
    assert.match(html, /Mudou por último: Thorfinn/)
    assert.equal(m.estado.mudou, false)
    // Tirar não pergunta (só fecha); o erro da rota vira texto.
    const e = new Error('x'); e.data = { detail: { code: 'leitor_inativo' } }
    const m2 = montar('../components/AtendimentoMailLeitores.vue', { mailbox: caixa }, {
      respostas: { [rota]: (url, o) => (o.method === 'PUT' ? e : { ...visao, leitores: [{ id: 'isr', nome: 'Israel', ativo: false }] }) },
    })
    await m2.montar()
    assert.match(await m2.html(), /inativo — tire da lista/)
    m2.estado.alternar('isr', false)
    m2.estado.alternar('ana', true)
    await m2.estado.salvar()
    assert.match(m2.estado.erro, /Só usuários ativos/)
    assert.match(await m2.html(), /role="alert"[^>]*>Só usuários ativos/)
  }

  // ------------------------------------------------ o Configurar inclui a seção
  {
    const fonte = web('components/AtendimentoMailConfigurar.vue')
    assert.match(fonte, /<AtendimentoMailLeitores :mailbox="mailbox" \/>/)
  }

  // ------------------------------------------------ a aba E-mail para o LEITOR
  {
    const caixa = { id: 'box1', label: 'Principal 061', address: 'p@tuta.com', aliases: ['p@tuta.com', 'loja@tuta.com'], state: 'online', send_enabled: true, can_send: true, last_sync_at: null, so_leitura: true }
    const mensagem = { id: 'm1', mailbox_id: 'box1', subject: 'Pedido 123', from_address: 'c@x.com', from_name: 'Cliente', to: ['loja@tuta.com'], cc: [], reply_to: null, received_at: '2026-10-09T12:00:00Z', direction: 'inbound', folder: 'INBOX', text: 'Onde está meu pedido?', attachments: [{ id: 'a1', filename: 'nota.pdf', size: 2048 }], reply: { to: 'c@x.com', from_address: 'loja@tuta.com', can_reply: true, send_ready: true }, outbox: [{ id: 'job1', status: 'uncertain', text: 'resp', to: 'c@x.com', from_address: 'loja@tuta.com', created_at: '2026-10-09T12:05:00Z', error_code: null }] }
    const filhos = {
      AtendimentoMailFilas: { props: ['isAdmin', 'lojas'], setup: () => () => Vue.h('div', { 'data-filas-stub': '' }) },
      AtendimentoMailConfigurar: { props: ['mailbox'], setup: (p) => () => Vue.h('div', { 'data-configurar-stub': p.mailbox.id }) },
    }
    // O leitor (usuário comum: a página passa isAdmin=false e canOperate=false).
    const m = montar('../components/AtendimentoMail.vue', { isAdmin: false, canOperate: false, stores: [] }, {
      respostas: { '/api/mail/mailboxes': { items: [caixa] }, '/api/mail/mailboxes/box1/messages': { items: [mensagem], more: false }, '/api/mail/messages/m1': mensagem },
      filhos,
    })
    await m.html()
    await m.estado.loadMailboxes()
    await Vue.nextTick(); await esperar()
    await m.estado.openMessage('m1')
    assert.equal(m.estado.section, 'mailboxes')
    assert.equal(m.estado.readOnly, true)
    let html = await m.html()
    assert.match(html, /data-mail-read-only[^>]*>Você só vê esta caixa/)
    assert.match(html, /Pedido 123/)
    assert.match(html, /Onde está meu pedido\?/)
    assert.match(html, /href="\/api\/mail\/attachments\/a1"/)
    assert.doesNotMatch(html, /Responder|Conferir e enviar resposta|aria-label="Texto da resposta"/)
    assert.doesNotMatch(html, /data-mail-resolve|Não saiu/)
    assert.doesNotMatch(html, /data-mail-configure|data-configurar-stub|Permitir respostas humanas|Desativar respostas|Cadastrar caixa/)
    assert.doesNotMatch(html, /data-mail-sections|data-filas-stub/)
    // Mesmo que alguém force: nada é enviado.
    m.estado.draft = 'não pode'
    await m.estado.sendReply()
    assert.equal(m.chamadas.filter((c) => c.metodo !== 'GET').length, 0)
    // O dono/admin (sem so_leitura) continua vendo o responder e o Saiu/Não saiu, como antes.
    const d = montar('../components/AtendimentoMail.vue', { isAdmin: true, canOperate: true, stores: [] }, {
      respostas: { '/api/mail/mailboxes': { items: [{ ...caixa, so_leitura: undefined }] }, '/api/mail/mailboxes/box1/messages': { items: [mensagem], more: false }, '/api/mail/messages/m1': mensagem },
      filhos,
    })
    await d.html()
    await d.estado.loadMailboxes()
    await Vue.nextTick(); await esperar()
    await d.estado.openMessage('m1')
    html = await d.html()
    assert.equal(d.estado.readOnly, false)
    assert.doesNotMatch(html, /data-mail-read-only/)
    assert.match(html, /Conferir e enviar resposta/)
    assert.match(html, /data-mail-resolve/)
    assert.match(html, /data-mail-configure/)
  }

  console.log('atendimento-mail-leitores: ok')
}

principal().catch((e) => { console.error(e); process.exitCode = 1 })
