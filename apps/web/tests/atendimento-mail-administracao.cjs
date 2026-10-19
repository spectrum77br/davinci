// DOM real, dados sintéticos e API controlada: nenhuma chamada real de e-mail.
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path')
const { JSDOM } = require('jsdom')
const { window } = new JSDOM('<!doctype html><html><body></body></html>', { url: 'https://davinci.invalid/' })
for (const n of ['window', 'document', 'Document', 'ShadowRoot', 'Element', 'HTMLElement', 'HTMLInputElement', 'HTMLSelectElement', 'SVGElement', 'Node', 'Text', 'Comment', 'Event', 'MouseEvent', 'KeyboardEvent']) globalThis[n] = n === 'window' ? window : window[n]
const ts = require('typescript'), Vue = require('vue'), { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')
const transpile = (s) => ts.transpileModule(s, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText
const cache = new Map(), icons = new Proxy({}, { get: (_, n) => ({ render: () => Vue.h('i', { 'data-icon': String(n) }) }) })
let responder, confirma = true
window.confirm = () => confirma
function requerer(n) {
  if (n === 'lucide-vue-next') return icons
  const match = /^~\/components\/(.+\.vue)$/.exec(n)
  if (!match) return require(n)
  if (!cache.has(match[1])) {
    const filename = path.resolve(__dirname, '../components', match[1])
    const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
    assert.deepEqual(errors, [])
    const real = ['AtendimentoMail.vue', 'AtendimentoMailAdministracao.vue'].includes(match[1])
    const source = real ? compileScript(descriptor, { id: match[1], inlineTemplate: true }).content : descriptor.script?.content || ''
    if (descriptor.template) assert.deepEqual(compileTemplate({ source: descriptor.template.content, filename, id: match[1], compilerOptions: { isTS: true } }).errors, [])
    const globals = { ref: Vue.ref, computed: Vue.computed, reactive: Vue.reactive, watch: Vue.watch, onMounted: Vue.onMounted, onBeforeUnmount: Vue.onBeforeUnmount, useApi: () => ({ api: (...a) => responder(...a), url: (u) => u }) }
    const mod = { exports: {} }
    new Function('require', 'module', 'exports', ...Object.keys(globals), transpile(source))(requerer, mod, mod.exports, ...Object.values(globals))
    cache.set(match[1], mod.exports)
  }
  return cache.get(match[1])
}
const tick = async () => { await new Promise((r) => setTimeout(r, 0)); await Vue.nextTick() }
const adiada = () => { let resolver; const promessa = new Promise((r) => { resolver = r }); return { promessa, resolver } }
const caixa = { id: 'b1', label: 'Principal', address: 'admin@example.invalid', aliases: ['admin@example.invalid'], state: 'online', send_enabled: true, can_send: true, last_sync_at: null }
const mensagem = { id: 'm1', subject: 'Pedido X', from_address: 'cliente@example.invalid', from_name: 'Cliente', received_at: '2026-10-19T11:00:00Z', direction: 'inbound', folder: 'Entrada', has_attachments: false, to: ['admin@example.invalid'], cc: [], reply_to: null, text: 'CORPO AUTORIZADO', attachments: [], reply: { to: 'cliente@example.invalid', from_address: 'admin@example.invalid', can_reply: true, send_ready: true }, outbox: [] }
const dados = () => ({ pessoas: [{ id: 'u1', nome: 'Ana', ativo: true }, { id: 'u2', nome: 'Bia', ativo: true }], caixas: [{ id: 'b1', label: 'Principal', address: 'admin@example.invalid' }], lojas: [{ chave: 'loja-a', nome: 'Kia', plataforma: 'shopee' }, { chave: 'loja-b', nome: 'ATV', plataforma: 'ml' }], politicas: [], lixeira_total: 1 })
const excluido = (id) => ({ message_id: id, mailbox_id: 'b1', caixa: 'Principal', assunto: id, loja: 'Kia', pedido: 'X', tipo_email: 'venda', excluido_em: '2026-10-19T12:00:00Z', excluido_por: 'Admin', motivo: 'Duplicado' })
async function montar(nome, props, fn) {
  const chamadas = [], avisos = []
  responder = async (url, opts = {}) => { chamadas.push({ url, opts }); return fn(url, opts) }
  const el = document.createElement('div'); document.body.appendChild(el)
  const app = Vue.createApp({ render: () => Vue.h(requerer(`~/components/${nome}`).default, props) })
  app.config.warnHandler = (m) => avisos.push(m)
  for (const child of ['AtendimentoMailFilas', 'AtendimentoMailConfigurar', 'AtendimentoMailPastas', 'AtendimentoMailBody', 'AtendimentoMailPastasDaCaixa', 'AtendimentoMailSelo']) app.component(child, { setup: () => () => Vue.h('div') })
  app.component('AtendimentoMailAdministracao', requerer('~/components/AtendimentoMailAdministracao.vue').default)
  app.mount(el); await tick()
  return { el, chamadas, avisos, fechar: () => { app.unmount(); el.remove() } }
}
async function click(el) { assert.ok(el, 'botão existe'); el.dispatchEvent(new window.MouseEvent('click', { bubbles: true })); await tick() }
async function preencher(el, valor, evento = 'input') { assert.ok(el, 'campo existe'); el.value = valor; el.dispatchEvent(new window.Event(evento, { bubbles: true })); await tick() }
async function marcar(el, valor) { el.checked = valor; el.dispatchEvent(new window.Event('change', { bubbles: true })); await tick() }
async function salvar(m) { const b = m.el.querySelector('[data-admin-salvar-acesso]'); assert.ok(b && !b.disabled); b.closest('form').dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true })); await tick() }
async function run() {
  const A = requerer('~/components/AtendimentoMailAdministracao.vue')
  assert.match(A.resumoPoliticaEmail({ ...A.formPoliticaEmail(), modo: 'selecionadas', pedidos_permitidos: [{ loja: 'loja-a', numero: 'X' }] }), /Nenhuma loja permitida/)
  assert.deepEqual(A.corpoPoliticaEmail({ ...A.formPoliticaEmail(), lojas_permitidas: ['residuo'] }, null).lojas_permitidas, [])
  for (const props of [{ isAdmin: false, canOperate: true }, { isAdmin: true, canOperate: false }, { isAdmin: true, canOperate: true }]) {
    const m = await montar('AtendimentoMail.vue', props, async (url) => {
      if (url === '/api/mail/mailboxes') return { items: [caixa] }
      if (url.includes('/lista?')) return { itens: [], proximo: null, faltam: 0 }
      if (url.includes('/pastas')) return { pastas: [], lojas: [], faltam: 0 }
      if (url.endsWith('/admin')) return dados()
      throw Error(url)
    })
    assert.equal(m.chamadas.some((c) => c.url.endsWith('/admin')), false)
    assert.equal(!!m.el.querySelector('[data-mail-admin-tab]'), props.isAdmin && props.canOperate)
    if (props.isAdmin && props.canOperate) { await click(m.el.querySelector('[data-mail-admin-tab]')); assert.ok(m.el.querySelector('[data-mail-administracao]')) }
    assert.equal(m.chamadas.filter((c) => c.opts.method).length, 0, 'abrir nunca grava nem exclui')
    m.fechar()
  }
  {
    let corpo
    const m = await montar('AtendimentoMailAdministracao.vue', { mailboxId: 'b1' }, async (_, opts) => {
      if (!opts.method) return dados()
      corpo = opts.body
      return { ...opts.body, mailbox_id: 'b1', user_id: 'u1', updated_at: '2026-10-19T12:00:00Z', updated_by_nome: 'Admin' }
    })
    await preencher(m.el.querySelector('[aria-label="Modo de acesso aos e-mails"]'), 'selecionadas', 'change')
    assert.match(m.el.querySelector('[data-admin-acesso-resumo]').textContent, /Nenhuma loja permitida/)
    await preencher(m.el.querySelector('[aria-label="Loja dos pedidos permitidos"]'), 'loja-a', 'change')
    await preencher(m.el.querySelector('[aria-label="Números dos pedidos permitidos"]'), 'X\nX\nY')
    await click(m.el.querySelector('[data-admin-adicionar-pedidos]'))
    assert.equal(m.el.querySelector('[data-admin-permitir="loja-a"]').checked, true)
    await marcar(m.el.querySelector('[data-admin-bloquear="loja-a"]'), true)
    assert.match(m.el.querySelector('[data-admin-acesso-resumo]').textContent, /Nenhuma loja permitida/)
    await marcar(m.el.querySelector('[data-admin-bloquear="loja-a"]'), false)
    await marcar(m.el.querySelector('[data-admin-tipo="informativo"]'), false)
    await marcar(m.el.querySelector('[data-admin-tipo="chamado"]'), false)
    await salvar(m)
    assert.deepEqual(corpo, { modo: 'selecionadas', lojas_permitidas: ['loja-a'], lojas_bloqueadas: [], tipos_permitidos: ['venda'], pedidos_permitidos: [{ loja: 'loja-a', numero: 'X' }, { loja: 'loja-a', numero: 'Y' }], expected_updated_at: null })
    assert.match(m.el.textContent, /Regra de acesso salva/)
    assert.equal(m.el.querySelector('[data-admin-salvar-acesso]').disabled, true)
    await click(m.el.querySelector('[data-admin-restaurar-padrao]'))
    assert.equal(m.chamadas.filter((c) => c.opts.method).length, 1, 'padrão só muda formulário')
    assert.match(m.el.textContent, /Clique em Salvar regra de acesso para aplicar/)
    assert.deepEqual(m.avisos, [])
    m.fechar()
  }
  {
    const m = await montar('AtendimentoMailAdministracao.vue', {}, async (_, opts) => { if (opts.method) throw Object.assign(Error('conflito'), { status: 409 }); return dados() })
    await preencher(m.el.querySelector('[aria-label="Modo de acesso aos e-mails"]'), 'bloqueado', 'change')
    confirma = false
    await preencher(m.el.querySelector('[aria-label="Pessoa da regra de acesso"]'), 'u2', 'change')
    assert.equal(m.el.querySelector('[aria-label="Pessoa da regra de acesso"]').value, 'u1')
    await salvar(m)
    assert.match(m.el.textContent, /Sua edição foi mantida/)
    assert.equal(m.el.querySelector('[aria-label="Modo de acesso aos e-mails"]').value, 'bloqueado')
    assert.equal(m.el.querySelector('[data-admin-salvar-acesso]').disabled, true)
    await click(m.el.querySelector('[data-admin-recarregar]'))
    assert.equal(m.chamadas.filter((c) => !c.opts.method).length, 1)
    confirma = true; await click(m.el.querySelector('[data-admin-recarregar]'))
    assert.equal(m.el.querySelector('[aria-label="Modo de acesso aos e-mails"]').value, 'padrao')
    m.fechar()
  }
  {
    const antiga = adiada(), atual = adiada(); let leituras = 0, restaurado = false
    const m = await montar('AtendimentoMailAdministracao.vue', {}, async (url, opts) => {
      if (url.endsWith('/restaurar')) { restaurado = true; assert.deepEqual(opts.body, {}); return { ok: true } }
      if (url.includes('/lixeira?')) { leituras++; return leituras === 1 ? antiga.promessa : leituras === 2 ? atual.promessa : { itens: [], proximo: null } }
      return dados()
    })
    assert.equal(leituras, 0)
    await click(m.el.querySelector('[data-email-admin-secao="lixeira"]'))
    await preencher(m.el.querySelector('input[type=search]'), 'Atual')
    m.el.querySelector('form[role=search]').dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true })); await tick()
    atual.resolver({ itens: [excluido('atual')], proximo: null }); await tick()
    antiga.resolver({ itens: [excluido('antigo')], proximo: null }); await tick()
    assert.deepEqual([...m.el.querySelectorAll('[data-admin-excluido]')].map((e) => e.dataset.adminExcluido), ['atual'])
    assert.match(m.chamadas.filter((c) => c.url.includes('/lixeira?')).at(-1).url, /q=Atual/)
    await click(m.el.querySelector('[data-admin-restaurar="atual"]'))
    assert.ok(restaurado); assert.match(m.el.textContent, /E-mail restaurado no DaVinci/)
    assert.equal(m.el.querySelector('[data-admin-excluido]'), null)
    assert.ok(m.chamadas.every((c) => c.url.startsWith('/api/atendimento/email/admin')))
    m.fechar()
  }
  {
    const excluir = adiada(); let removido = false
    const m = await montar('AtendimentoMail.vue', { isAdmin: true, canOperate: true }, async (url, opts) => {
      if (url.endsWith('/excluir')) { assert.deepEqual(opts.body, { motivo: 'Duplicado' }); return excluir.promessa }
      if (url === '/api/mail/mailboxes') return { items: [caixa] }
      if (url.includes('/lista?')) return { itens: removido ? [] : [{ id: 'm1', recebido_em: mensagem.received_at, direcao: 'inbound', tem_anexos: false, de: mensagem.from_address, de_nome: mensagem.from_name, assunto: mensagem.subject, pasta: { id: 's1', nome: 'Entrada', tipo: 'sistema' }, selo: { tipo: 'loja', rotulo: 'Kia' } }], proximo: null, faltam: 0 }
      if (url.includes('/pastas')) return { pastas: [], lojas: [], faltam: 0 }
      if (url.endsWith('/m1')) return structuredClone(mensagem)
      throw Error(url)
    })
    await click(m.el.querySelector('[data-mail-item="m1"]'))
    await preencher(m.el.querySelector('textarea[aria-label="Texto da resposta"]'), 'RASCUNHO PRESERVADO')
    await click(m.el.querySelector('[data-mail-delete-review]'))
    assert.match(m.el.querySelector('[data-mail-delete-confirm]').textContent, /O original continua no Tuta/)
    await click(m.el.querySelector('[data-mail-delete-cancelar]'))
    assert.equal(m.el.querySelector('textarea[aria-label="Texto da resposta"]').value, 'RASCUNHO PRESERVADO')
    assert.equal(m.chamadas.filter((c) => c.opts.method).length, 0)
    await click(m.el.querySelector('[data-mail-delete-review]'))
    await preencher(m.el.querySelector('[aria-label="Motivo da exclusão do e-mail"]'), 'Duplicado')
    await click(m.el.querySelector('[data-mail-delete-confirmar]'))
    assert.equal(m.el.querySelector('textarea[aria-label="Texto da resposta"]').disabled, true)
    assert.ok([...m.el.querySelectorAll('button')].find((e) => e.textContent.includes('Conferir e enviar')).disabled)
    assert.equal(m.chamadas.filter((c) => c.opts.method).length, 1)
    removido = true; excluir.resolver({ ok: true }); await tick(); await tick()
    assert.equal(m.el.querySelector('[data-mail-item="m1"]'), null)
    assert.ok(!m.el.textContent.includes('CORPO AUTORIZADO'))
    assert.match(m.el.textContent, /E-mail excluído do DaVinci/)
    m.fechar()
  }
  console.log('atendimento-mail-administracao: ok — acessos, conflitos, lixeira, restauração e exclusão revisável')
}
run().catch((e) => { console.error(e); process.exitCode = 1 })
