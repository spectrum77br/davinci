// Leitura dos e-mails de UMA venda no modal do cabeçalho, sem corpo na timeline.
// DOM real, sem rede: lista exata, leitura individual, permissões e corridas X→Y.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { JSDOM } = require('jsdom')
const { window } = new JSDOM('<!doctype html><html><body></body></html>', { url: 'https://davinci.invalid/', pretendToBeVisual: true })
for (const nome of ['window', 'document', 'Element', 'HTMLElement', 'HTMLInputElement', 'SVGElement', 'Node', 'NodeFilter', 'Text', 'Comment', 'Event', 'MouseEvent', 'KeyboardEvent', 'CustomEvent', 'FocusEvent', 'MutationObserver', 'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame']) {
  globalThis[nome] = nome === 'window' ? window : window[nome]
}
const ts = require('typescript')
const Vue = require('vue')
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')
const transpile = (source) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText
function sfc(nome) {
  const filename = path.resolve(__dirname, '../components', nome)
  const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
  assert.deepEqual(errors, [])
  if (descriptor.template) assert.deepEqual(compileTemplate({ source: descriptor.template.content, filename, id: nome, compilerOptions: { isTS: true } }).errors, [])
  return descriptor
}
const icones = new Proxy({}, { get: (_, nome) => ({ render: () => Vue.h('i', { 'data-icone': String(nome) }) }) })
let responderAtual
const cache = new Map()
function requerer(nome) {
  if (nome === 'lucide-vue-next') return icones
  if (nome === '@vueuse/core') return {
    onKeyStroke: (tecla, fn) => {
      const listener = (e) => { if (e.key === tecla) fn(e) }
      Vue.onMounted(() => window.addEventListener('keydown', listener))
      Vue.onBeforeUnmount(() => window.removeEventListener('keydown', listener))
    },
  }
  const m = /^~\/components\/(.+\.vue)$/.exec(nome)
  if (!m) return require(nome)
  if (!cache.has(m[1])) {
    const descriptor = sfc(m[1])
    const script = ['AtendimentoEmailVenda.vue', 'AtendimentoEmailCartao.vue'].includes(m[1])
      ? compileScript(descriptor, { id: m[1], inlineTemplate: true }).content
      : descriptor.script.content
    const g = {
      ref: Vue.ref, computed: Vue.computed, watch: Vue.watch, onBeforeUnmount: Vue.onBeforeUnmount,
      useApi: () => ({ api: (...args) => responderAtual(...args), url: (u) => u }),
    }
    const mod = { exports: {} }
    new Function('require', 'module', 'exports', ...Object.keys(g), transpile(script))(requerer, mod, mod.exports, ...Object.values(g))
    cache.set(m[1], mod.exports)
  }
  return cache.get(m[1])
}
const esperar = async () => { await new Promise((r) => setTimeout(r, 0)); await Vue.nextTick() }
const click = async (el) => { assert.ok(el, 'botão existe'); el.dispatchEvent(new window.MouseEvent('click', { bubbles: true })); await esperar() }
const escolherCategoria = async (el, tipo) => {
  const tab = el.querySelector(`[data-email-venda-categoria="${tipo}"]`)
  assert.ok(tab, 'categoria existe')
  tab.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
  await esperar()
  assert.equal(tab.getAttribute('aria-selected'), 'true')
}
function adiada() {
  let resolver, rejeitar
  const promessa = new Promise((a, b) => { resolver = a; rejeitar = b })
  return { promessa, resolver, rejeitar }
}
function item(id, pedido, extra = {}) {
  return {
    id, pedido, estado: 'vinculado', recebido_em: '2026-10-19T10:00:00Z', pasta: 'avisos', plataforma: 'shopee',
    finalidade: 'avisos', tipo_email: 'informativo', assunto: `Aviso ${pedido}/${id}`, alias: 'loja@example.invalid', loja: 'Loja',
    conversa_id: 'caso-misto', suspeito: false, suspeito_motivos: [], alertas: [], anexos: [], reply_to: [], ...extra,
  }
}
const lista = (pedido, itens) => ({ estado: 'ok', pedido, total: itens.length, itens })
async function montar(propsIniciais, responder) {
  const chamadas = [], avisos = []
  responderAtual = (url, opts) => { chamadas.push([url, opts]); return responder(url, opts) }
  const C = requerer('~/components/AtendimentoEmailVenda.vue').default
  const props = Vue.reactive(propsIniciais)
  const instancia = Vue.ref(null)
  const el = document.createElement('div')
  document.body.appendChild(el)
  const app = Vue.createApp({ render: () => Vue.h(C, { ...props, ref: instancia }) })
  app.config.warnHandler = (m) => avisos.push(m)
  app.mount(el)
  await esperar()
  return { el: document.body, props, chamadas, avisos, recarregar: () => instancia.value.carregar(), fechar: () => { app.unmount(); el.remove() } }
}
async function principal() {
  const V = requerer('~/components/AtendimentoEmailVenda.vue')
  assert.equal(V.urlEmailsDaVenda('a b'), '/api/atendimento/email/conversas/a%20b/emails-da-venda')
  assert.equal(V.urlLeituraDaVenda('a b', 'm/1'), '/api/atendimento/email/venda/a%20b/emails/m%2F1')
  {
    const x1 = item('x1', 'X', { texto: 'CORPO NA LISTA NÃO APARECE' }), x2 = item('x2', 'X', { assunto: null })
    const m = await montar({ conversaId: 'chat-X', pedido: 'X', sinal: 0, toolbar: true }, async (url) => {
      if (url.endsWith('/emails/x1')) return { ...x1, texto: 'CORPO PROTEGIDO X1', abrir_no_tuta: 'https://app.tuta.com/mail/x1' }
      return lista('X', [x1, x2])
    })
    assert.equal(m.el.textContent.replace(/\s+/g, ' ').trim(), 'E-mails 2')
    assert.deepEqual(m.chamadas.map(([u]) => u), ['/api/atendimento/email/venda/chat-X'])
    assert.ok(!m.el.textContent.includes('Aviso'), 'metadados aparecem só ao abrir modal')
    await click(m.el.querySelector('[data-emails-venda-botao]'))
    assert.equal(m.el.querySelector('[role=dialog]').getAttribute('aria-modal'), 'true')
    const dialog = m.el.querySelector('[role=dialog]')
    assert.ok(dialog.contains(document.activeElement), 'abrir leva o foco ao modal')
    const fechar = m.el.querySelector('[data-emails-venda-fechar]')
    fechar.focus()
    fechar.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true }))
    await esperar()
    assert.equal(document.activeElement, [...dialog.querySelectorAll('button')].at(-1), 'Shift+Tab circula dentro do modal')
    assert.deepEqual([...m.el.querySelectorAll('[data-email-venda-item]')].map((e) => e.dataset.emailVendaItem), ['x1', 'x2'])
    assert.match(m.el.querySelector('[data-email-venda-item="x2"]').textContent, /E-mail recebido/)
    assert.ok(!m.el.textContent.includes('(sem assunto)'), 'ausência de assunto na listagem não afirma que o e-mail não tem assunto')
    assert.ok(!m.el.textContent.includes('CORPO NA LISTA'), 'a lista nunca copia corpo recebido por engano')
    await click(m.el.querySelector('[data-email-venda-ver]'))
    assert.match(m.el.textContent, /CORPO PROTEGIDO X1/)
    assert.equal(m.el.querySelector('[data-email-responder-este]'), null, 'modal é só leitura; envio humano continua na aba E-mail')
    assert.ok(m.el.querySelector('[data-email-abrir-tuta]'), 'ações existentes de leitura preservadas')
    assert.ok(m.chamadas.every(([, o]) => !o || !o.method || o.method === 'GET'), 'nenhum envio ou alteração')
    window.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape' }))
    await esperar()
    assert.equal(m.el.querySelector('[role=dialog]'), null)
    assert.equal(document.activeElement, m.el.querySelector('[data-emails-venda-botao]'), 'fechar devolve foco ao botão')
    assert.ok(!m.el.textContent.includes('CORPO PROTEGIDO'))
    assert.deepEqual(m.avisos, [])
    m.fechar()
  }
  // As três categorias separam mensagens da mesma venda, sem consultar a
  // conversa inteira do chamado ou misturar mensagens de outras categorias.
  {
    const info = item('info', 'X', { tipo_email: 'informativo', finalidade: 'vendas' })
    const chamado1 = item('chamado1', 'X', { tipo_email: 'chamado', caso_id: 'ABC123', tipo_email_inferido: true })
    const chamado2 = item('chamado2', 'X', { tipo_email: 'chamado', caso_id: 'ABC123' })
    const venda = item('venda', 'X', { tipo_email: 'venda', finalidade: 'avisos' })
    const m = await montar({ conversaId: 'X', pedido: 'X', sinal: 0, toolbar: true }, async () => lista('X', [venda, chamado1, info, chamado2]))
    await click(m.el.querySelector('[data-emails-venda-botao]'))
    assert.deepEqual([...m.el.querySelectorAll('[role=tab]')].map((e) => e.textContent.replace(/\s+/g, ' ').trim()), ['Informativos 1', 'Chamados 2', 'Vendas 1'])
    assert.equal(m.el.querySelector('[role=tab][aria-selected=true]').dataset.emailVendaCategoria, 'informativo')
    assert.deepEqual([...m.el.querySelectorAll('[data-email-venda-item]')].map((e) => e.dataset.emailVendaItem), ['info'])
    await escolherCategoria(m.el, 'chamado')
    assert.deepEqual([...m.el.querySelectorAll('[data-email-venda-item]')].map((e) => e.dataset.emailVendaItem), ['chamado1', 'chamado2'])
    assert.match(m.el.querySelector('[data-email-venda-item="chamado1"] [title]').getAttribute('title'), /Tipo sugerido/)
    await escolherCategoria(m.el, 'venda')
    assert.deepEqual([...m.el.querySelectorAll('[data-email-venda-item]')].map((e) => e.dataset.emailVendaItem), ['venda'])
    assert.equal(m.chamadas.length, 1, 'trocar categorias usa só os e-mails já autorizados para o pedido')
    m.fechar()
  }
  {
    const m = await montar({ conversaId: 'X', pedido: 'X', sinal: 0, toolbar: true }, async () => lista('X', [item('venda', 'X', { tipo_email: 'venda' })]))
    await click(m.el.querySelector('[data-emails-venda-botao]'))
    assert.equal(m.el.querySelector('[role=tab][aria-selected=true]').dataset.emailVendaCategoria, 'venda', 'abre a primeira categoria com e-mails')
    await escolherCategoria(m.el, 'chamado')
    assert.equal(m.el.querySelector('[data-email-venda-item]'), null)
    assert.match(m.el.querySelector('[role=tabpanel]:not([hidden])').textContent, /Nenhum e-mail nesta categoria para este pedido/)
    await click(m.el.querySelector('[data-emails-venda-fechar]'))
    await click(m.el.querySelector('[data-emails-venda-botao]'))
    assert.equal(m.el.querySelector('[role=tab][aria-selected=true]').dataset.emailVendaCategoria, 'venda', 'reabre com conteúdo')
    m.fechar()
  }
  // Detalhe atrasado não reaparece ao trocar de categoria, nem ao voltar.
  {
    const detalhe = adiada()
    const m = await montar({ conversaId: 'X', pedido: 'X', sinal: 0, toolbar: true }, (url) => {
      if (url.includes('/emails/')) return detalhe.promessa
      return Promise.resolve(lista('X', [item('info', 'X'), item('venda', 'X', { tipo_email: 'venda' })]))
    })
    await click(m.el.querySelector('[data-emails-venda-botao]'))
    await click(m.el.querySelector('[data-email-venda-ver]'))
    await escolherCategoria(m.el, 'venda')
    detalhe.resolver(item('info', 'X', { texto: 'DETALHE DA CATEGORIA ANTERIOR' }))
    await esperar()
    await escolherCategoria(m.el, 'informativo')
    assert.ok(!m.el.textContent.includes('DETALHE DA CATEGORIA ANTERIOR'))
    assert.equal(m.el.querySelector('[data-email-venda-ver]').getAttribute('aria-expanded'), 'false')
    m.fechar()
  }
  // Resposta inesperada não perde mensagens nem inventa um dos três tipos.
  {
    const m = await montar({ conversaId: 'X', pedido: 'X', sinal: 0, toolbar: true }, async () => lista('X', [item('legado', 'X', { tipo_email: null })]))
    await click(m.el.querySelector('[data-emails-venda-botao]'))
    assert.match(m.el.querySelector('[role=tab][aria-selected=true]').textContent, /Sem classificação 1/)
    assert.ok(m.el.querySelector('[data-email-venda-item="legado"]'))
    m.fechar()
  }
  // Sem e-mail confirmado, sem pedido ou erro: botão escondido, sem atalho genérico.
  for (const resposta of [lista('X', []), { estado: 'sem_pedido', pedido: null, total: 1, itens: [item('z', 'Z')] }, new Error('500')]) {
    const m = await montar({ conversaId: 'sem', sinal: 0, toolbar: true }, async () => { if (resposta instanceof Error) throw resposta; return resposta })
    assert.equal(m.el.textContent.trim(), '')
    assert.equal(m.el.querySelector('[data-emails-venda-botao]'), null)
    m.fechar()
  }
  {
    const m = await montar({ conversaId: '', sinal: 0, toolbar: true }, async () => assert.fail('sem id não consulta'))
    assert.deepEqual(m.chamadas, [])
    m.fechar()
  }
  {
    const m = await montar({ conversaId: 'chat-X', pedido: ' X ', sinal: 0, toolbar: true }, async () => lista('Y', [item('y', 'Y')]))
    assert.equal(m.el.querySelector('[data-emails-venda-botao]'), null, 'pedido divergente não aparece sob cabeçalho de X')
    m.fechar()
  }
  {
    let chegou = false
    const m = await montar({ conversaId: 'X', pedido: 'X', sinal: 0, toolbar: true }, async () => lista('X', chegou ? [item('x', 'X')] : []))
    assert.equal(m.el.querySelector('[data-emails-venda-botao]'), null)
    chegou = true
    await m.recarregar()
    await esperar()
    assert.ok(m.el.querySelector('[data-emails-venda-botao]'), 'refresh manual encontra novo e-mail mesmo com sinal 0')
    assert.equal(m.chamadas.length, 2, 'uma consulta inicial e uma solicitada')
    m.fechar()
  }
  // Resposta da lista de X chega depois de Y: nunca vira conteúdo de Y.
  {
    const x = adiada()
    const m = await montar({ conversaId: 'X', pedido: 'X', sinal: 0, toolbar: true }, (url) => url.endsWith('/X') ? x.promessa : Promise.resolve(lista('Y', [item('y', 'Y')])))
    m.props.conversaId = 'Y'
    m.props.pedido = 'Y'
    await esperar()
    await click(m.el.querySelector('[data-emails-venda-botao]'))
    x.resolver(lista('X', [item('x', 'X')]))
    await esperar()
    assert.deepEqual([...m.el.querySelectorAll('[data-email-venda-item]')].map((e) => e.dataset.emailVendaItem), ['y'])
    assert.ok(!m.el.textContent.includes('Aviso X'))
    m.fechar()
  }
  // Detalhe X atrasado, seleção muda para Y: fecha o modal e descarta corpo X.
  {
    const detalheX = adiada()
    const m = await montar({ conversaId: 'X', pedido: 'X', sinal: 0, toolbar: true }, (url) => {
      if (url.includes('/emails/')) return detalheX.promessa
      return Promise.resolve(lista(url.endsWith('/X') ? 'X' : 'Y', [item(url.endsWith('/X') ? 'x' : 'y', url.endsWith('/X') ? 'X' : 'Y')]))
    })
    await click(m.el.querySelector('[data-emails-venda-botao]'))
    await click(m.el.querySelector('[data-email-venda-ver]'))
    m.props.conversaId = 'Y'
    m.props.pedido = 'Y'
    await Vue.nextTick()
    assert.equal(m.el.querySelector('[role=dialog]'), null, 'troca limpa imediatamente modal/lista/detalhe')
    await esperar()
    await click(m.el.querySelector('[data-emails-venda-botao]'))
    detalheX.resolver(item('x', 'X', { texto: 'CORPO ATRASADO X' }))
    await esperar()
    assert.ok(!m.el.textContent.includes('CORPO ATRASADO X'))
    assert.deepEqual([...m.el.querySelectorAll('[data-email-venda-item]')].map((e) => e.dataset.emailVendaItem), ['y'])
    m.fechar()
  }
  // Erro após a troca não reaproveita a lista anterior; detalhe com ID errado não aparece.
  {
    const m = await montar({ conversaId: 'X', pedido: 'X', sinal: 0, toolbar: true }, async (url) => {
      if (url.includes('/emails/')) return item('fora', 'OUTRO', { texto: 'CORPO DE OUTRA VENDA' })
      if (url.endsWith('/Y')) throw new Error('500')
      return lista('X', [item('x', 'X')])
    })
    await click(m.el.querySelector('[data-emails-venda-botao]'))
    await click(m.el.querySelector('[data-email-venda-ver]'))
    assert.ok(!m.el.textContent.includes('CORPO DE OUTRA VENDA'))
    assert.match(m.el.textContent, /Não consegui ler este e-mail/)
    m.props.conversaId = 'Y'
    await esperar()
    assert.equal(m.el.textContent.trim(), '')
    m.fechar()
  }
  // O resumo existente na conversa de e-mail continua, sem nova consulta da venda.
  {
    const m = await montar({ conversaId: 'email-1', sinal: 2 }, async () => ({ total: 2, caixas: [{ caixa: 'loja@example.invalid', loja: 'Loja', plataforma: 'shopee', quantidade: 2, ultimo_em: null, pastas: [{ pasta: 'avisos', finalidade: 'avisos', destaque: false, quantidade: 2 }] }] }))
    assert.ok(m.el.querySelector('[data-email-venda-caixa]'))
    assert.deepEqual(m.chamadas.map(([u]) => u), ['/api/atendimento/email/conversas/email-1/emails-da-venda'])
    assert.equal(m.el.querySelector('[data-emails-venda-botao]'), null)
    m.fechar()
  }
  // Um único ponto de consulta no chat: toolbar junto da Garantia/AdsPower.
  {
    const conversa = sfc('AtendimentoConversa.vue').template.content
    assert.match(conversa, /data-vincular-garantia-cabecalho[\s\S]*?<AtendimentoEmailVenda[\s\S]*?toolbar[\s\S]*?<AtendimentoAdsPower/)
    assert.match(conversa, /v-if="!conversa.email && conversa.plataforma !== 'site' && !canalExterno"/)
    assert.match(sfc('AtendimentoConversa.vue').scriptSetup.content, /function atualizarTudo\(\) \{\s+void emailsVendaRef.value\?\.carregar\(\)/)
    const pedido = sfc('AtendimentoPedido.vue').template.content
    assert.match(pedido, /<AtendimentoEmailVenda v-if="conversa.email && emailsSinal"/)
    assert.doesNotMatch(pedido, /<AtendimentoEmailVenda[^>]+\bchat\b/)
  }
  console.log('atendimento-email-venda-link: ok')
}
principal().catch((e) => { console.error(e); process.exit(1) })
