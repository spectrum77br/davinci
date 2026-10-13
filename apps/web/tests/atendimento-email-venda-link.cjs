// node tests/atendimento-email-venda-link.cjs — o "E-mails da venda" no CHAT (13/10/2026).
// O dono: "o e-mail aparece só na aba de e-mail; a do chat de atendimento
// aparece só no chat". No chat, o bloco do painel ④ diz só QUANTOS e-mails a
// venda tem (e quantos esperam resposta) e abre a conversa de e-mail na aba
// E-mail — nada do e-mail (caixa, loja da caixa, pasta, assunto, remetente,
// texto). Sem rede, sem e-mail de verdade. Aqui:
//  - o contrato com o backend (routers/atendimento_email.py: só números e ids);
//  - as funções puras (a frase, as conversas, o rótulo de cada uma);
//  - o componente de verdade montado num DOM (jsdom + Vue): a frase, o link,
//    o clique (emite `abrir` com o id), nada de conteúdo mesmo se a resposta
//    trouxer; sem e-mail ou com erro, o bloco some; a conversa de e-mail
//    continua com a lista completa;
//  - o caminho do clique até a aba E-mail: o painel sobe `irPara` com o id, a
//    conversa emite `abrirConversa` (trecho de verdade rodando com dublês) e a
//    página leva a conversa de e-mail para a aba E-mail (`aoMudarConversa`).
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { JSDOM } = require('jsdom')

// O DOM antes do Vue: o runtime-dom guarda o `document` quando carrega.
const { window } = new JSDOM('<!doctype html><html><body></body></html>', { url: 'https://davinci.invalid/' })
for (const nome of ['window', 'document', 'Element', 'HTMLElement', 'SVGElement', 'Node', 'Text', 'Comment', 'Event', 'MouseEvent']) {
  globalThis[nome] = nome === 'window' ? window : window[nome]
}

const ts = require('typescript')
const Vue = require('vue')
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')

const transpile = (source) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText

function sfc(rel) {
  const filename = path.resolve(__dirname, rel)
  const fonte = fs.readFileSync(filename, 'utf8')
  const { descriptor, errors } = parse(fonte, { filename })
  assert.deepEqual(errors, [], rel)
  if (descriptor.template) {
    const tpl = compileTemplate({ source: descriptor.template.content, filename, id: path.basename(rel), compilerOptions: { isTS: true } })
    assert.deepEqual(tpl.errors, [], `${rel}: template compila`)
  }
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
// O código de verdade de `ini` até `fim` (sem ele), mais `fecho`, em JS.
function trecho(src, ini, fim, fecho = '') {
  const a = src.indexOf(ini)
  const b = src.indexOf(fim, a + 1)
  assert.ok(a >= 0 && b > a, `trecho ${ini} … ${fim}`)
  return transpile(src.slice(a, b) + fecho)
}
const api = (rel) => fs.readFileSync(path.resolve(__dirname, '../../api/app', rel), 'utf8')
const esperar = async () => { await new Promise((r) => setTimeout(r, 0)); await Vue.nextTick() }

// ------------------------------------------------ o componente de verdade, montado num DOM
// O SFC inteiro (script + template inline), com os auto-imports do Nuxt
// (`ref`, `computed`, `watch`, `onMounted`, `useApi`) passados de fora.
const REL = '../components/AtendimentoEmailVenda.vue'
function componente(responder) {
  const { descriptor } = sfc(REL)
  const script = compileScript(descriptor, { id: 'emailvenda', inlineTemplate: true })
  const g = {
    ref: Vue.ref, computed: Vue.computed, watch: Vue.watch, onMounted: Vue.onMounted,
    useApi: () => ({ api: responder }),
  }
  const nomes = Object.keys(g)
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', ...nomes, transpile(script.content))(requerer, mod, mod.exports, ...nomes.map((n) => g[n]))
  return mod.exports.default
}
async function montar(props, resposta) {
  const chamadas = []
  const abertos = []
  const avisos = []
  const C = componente(async (url) => {
    chamadas.push(url)
    if (resposta instanceof Error) throw resposta
    return structuredClone(resposta)
  })
  const el = document.createElement('div')
  document.body.appendChild(el)
  const app = Vue.createApp({ render: () => Vue.h(C, { ...props, onAbrir: (id) => abertos.push(id) }) })
  app.config.warnHandler = (msg) => avisos.push(msg)
  app.mount(el)
  await esperar()
  await esperar()
  return { el, chamadas, abertos, avisos, app }
}

const V = requerer('~/components/AtendimentoEmailVenda.vue')
// O que é do e-mail em si: nada disso aparece no chat, nem se a API mandar.
const CONTEUDO = ['21max', 'Barbosa', 'problema ml', 'mensagens ml', 'Chegou quebrado', 'Pedido 2000', 'maria@gmail.com', 'Maria']

async function principal() {
  // ------------------------------------------------ o contrato com o backend
  {
    const rotas = api('routers/atendimento_email.py')
    const rota = rotas.slice(rotas.indexOf('async def emails_da_venda('), rotas.indexOf('async def _emails_da_venda_no_chat('))
    // Fora da conversa de e-mail, a resposta do chat (a família é a mesma da lista completa).
    assert.match(rota, /familia = await abas\.familia\(session, conversa\)\n\s+if not separa_email\.e_conversa_de_email\(conversa\):\n\s+return await _emails_da_venda_no_chat\(session, conversa, familia, scope\)/)
    const chat = rotas.slice(rotas.indexOf('async def _emails_da_venda_no_chat('), rotas.indexOf('@router.get("/anexos/{attachment_id}")'))
    assert.ok(chat.length > 0)
    // Só as conversas de E-MAIL da ponte, no escopo da lista `?caixa=email`, pela régua da família.
    assert.match(chat, /separa_email\.conversa_de_email_sql\(c\)/)
    assert.match(chat, /_clausula_escopo\(scope, c\.integration_id\)/)
    assert.match(chat, /abas\._membro\(x, conversa, ref, pedido, set\(\), sem_produtos, familia\.compra_em\)/)
    assert.match(chat, /m\.estado\.in_\(ESTADOS_COM_TEXTO\)/)
    // Só números e ids: as chaves que saem e nenhuma do e-mail.
    const chaves = [...chat.matchAll(/"([a-z_]+)":/g)].map((x) => x[1])
    assert.deepEqual([...new Set(chaves)].sort(), ['aguardando', 'caixas', 'conversas', 'emails', 'id', 'total'])
    assert.match(chat, /"caixas": \[\]/)
    assert.doesNotMatch(chat, /decifrar|decrypt|"texto"|"assunto"|"de"|"caixa"|"pasta"|"loja"|alias_recebido|folder_id/, 'nada do e-mail em si')
  }

  // ------------------------------------------------ as funções puras
  {
    assert.deepEqual(V.conversasDoChat(null), [])
    assert.deepEqual(V.conversasDoChat({ total: 0, caixas: [] }), [], 'a resposta de sempre (sem e-mail): nenhuma')
    assert.deepEqual(V.conversasDoChat({ total: 1, caixas: [], conversas: [{ id: '', emails: 1, aguardando: false }, { id: 'a', emails: 1, aguardando: false }] }).map((c) => c.id), ['a'])
    assert.equal(V.fraseDoChat({ total: 0, caixas: [] }), null)
    assert.equal(V.fraseDoChat(null), null)
    assert.equal(V.fraseDoChat({ total: 1, caixas: [], aguardando: 0, conversas: [{ id: 'a', emails: 1, aguardando: false }] }), 'Esta venda tem 1 e-mail')
    assert.equal(V.fraseDoChat({ total: 3, caixas: [], aguardando: 1, conversas: [{ id: 'a', emails: 3, aguardando: true }] }), 'Esta venda tem 3 e-mails · 1 esperando resposta')
    assert.equal(V.fraseDoChat({ total: 0, caixas: [], aguardando: 0, conversas: [{ id: 'a', emails: 0, aguardando: false }] }), 'Esta venda tem conversa de e-mail')
    assert.equal(V.rotuloDaConversa({ id: 'a', emails: 1, aguardando: true }, 0), 'conversa 1 · 1 e-mail · esperando resposta')
    assert.equal(V.rotuloDaConversa({ id: 'b', emails: 4, aguardando: false }, 1), 'conversa 2 · 4 e-mails')
  }

  // ------------------------------------------------ o componente no CHAT
  const ID1 = '11111111-1111-4111-8111-111111111111'
  const ID2 = '22222222-2222-4222-8222-222222222222'
  const URL1 = '/api/atendimento/email/conversas/chat-1/emails-da-venda'
  {
    // Uma conversa de e-mail. A resposta traz até conteúdo (uma API errada,
    // uma versão velha): o chat não mostra nada dele.
    const resposta = {
      total: 3,
      aguardando: 1,
      conversas: [{ id: ID1, emails: 3, aguardando: true }],
      caixas: [{ caixa: '21max@tuta.com', loja: 'Barbosa', plataforma: 'ml', quantidade: 3, ultimo_em: null, pastas: [{ pasta: 'problema ml', finalidade: 'problema', destaque: true, quantidade: 3 }] }],
      assunto: 'Pedido 2000012345678901',
      texto: 'Chegou quebrado.',
      de: 'maria@gmail.com',
    }
    const m = await montar({ conversaId: 'chat-1', sinal: 0, chat: true }, resposta)
    assert.deepEqual(m.chamadas, [URL1])
    const bloco = m.el.querySelector('[data-painel-emails-venda]')
    assert.ok(bloco && bloco.hasAttribute('data-emails-venda-chat'), 'o bloco do chat aparece')
    assert.match(bloco.textContent, /E-mails da venda/)
    assert.equal(m.el.querySelector('[data-email-venda-frase]').textContent, 'Esta venda tem 3 e-mails · 1 esperando resposta')
    const botoes = m.el.querySelectorAll('[data-email-venda-abrir]')
    assert.equal(botoes.length, 1)
    assert.match(botoes[0].textContent, /abrir na aba E-mail/)
    for (const proibido of CONTEUDO) assert.ok(!m.el.innerHTML.includes(proibido), `nada do e-mail: ${proibido}`)
    assert.equal(m.el.querySelector('[data-email-venda-caixa]'), null)
    assert.equal(m.el.querySelector('[data-email-venda-pastas]'), null)
    // O clique abre a conversa de e-mail (o painel leva para a aba E-mail).
    botoes[0].dispatchEvent(new window.MouseEvent('click', { bubbles: true }))
    assert.deepEqual(m.abertos, [ID1])
    assert.deepEqual(m.avisos, [], 'sem aviso do Vue (props, emits)')
    m.app.unmount()
  }
  {
    // Duas conversas de e-mail na venda: um link para cada (a que espera resposta primeiro, como veio).
    const m = await montar({ conversaId: 'chat-2', sinal: 0, chat: true }, {
      total: 3, aguardando: 1, caixas: [],
      conversas: [{ id: ID1, emails: 3, aguardando: true }, { id: ID2, emails: 0, aguardando: false }],
    })
    const botoes = [...m.el.querySelectorAll('[data-email-venda-abrir]')]
    assert.deepEqual(botoes.map((b) => b.textContent.trim().replace(/\s+/g, ' ')), [
      'conversa 1 · 3 e-mails · esperando resposta — abrir na aba E-mail',
      'conversa 2 · 0 e-mails — abrir na aba E-mail',
    ])
    botoes[1].dispatchEvent(new window.MouseEvent('click', { bubbles: true }))
    assert.deepEqual(m.abertos, [ID2])
    m.app.unmount()
  }
  {
    // Sem e-mail na venda (o zero de sempre): o bloco some — nada de "Nenhum e-mail" em todo chat.
    const m = await montar({ conversaId: 'chat-3', sinal: 0, chat: true }, { total: 0, caixas: [] })
    assert.equal(m.chamadas.length, 1)
    assert.equal(m.el.querySelector('[data-painel-emails-venda]'), null)
    assert.equal(m.el.textContent.trim(), '')
    m.app.unmount()
    // Erro: também some (é ajuda; o chat não ganha aviso de e-mail).
    const f = await montar({ conversaId: 'chat-4', sinal: 0, chat: true }, new Error('500'))
    assert.equal(f.el.querySelector('[data-painel-emails-venda]'), null)
    assert.equal(f.el.textContent.trim(), '')
    f.app.unmount()
  }
  {
    // A conversa de E-MAIL (sem `chat`): a lista completa de antes, sem link.
    const m = await montar({ conversaId: 'email-1', sinal: 2 }, {
      total: 3,
      caixas: [{ caixa: '21max@tuta.com', loja: 'Barbosa', plataforma: 'ml', quantidade: 3, ultimo_em: '2026-10-09T03:00:00Z', pastas: [{ pasta: 'problema ml', finalidade: 'problema', destaque: true, quantidade: 3 }] }],
    })
    assert.ok(m.el.querySelector('[data-email-venda-caixa]'))
    assert.match(m.el.querySelector('[data-email-venda-caixa]').textContent, /21max@tuta\.com → Barbosa/)
    assert.equal(m.el.querySelector('[data-email-venda-pastas]').textContent, 'problema ml (3)')
    assert.equal(m.el.querySelector('[data-email-venda-abrir]'), null)
    assert.equal(m.el.querySelector('[data-emails-venda-chat]'), null)
    m.app.unmount()
  }

  // ------------------------------------------------ do clique à aba E-mail
  {
    // O painel: o bloco de sempre na conversa de e-mail; no chat, o de link.
    const pedido = sfc('../components/AtendimentoPedido.vue').descriptor
    assert.match(pedido.template.content, /<AtendimentoEmailVenda v-if="emailsSinal" :conversa-id="conversa\.id" :sinal="emailsSinal" \/>\n\s*<!--[^\n]*-->\n\s*<AtendimentoEmailVenda v-else-if="!conversa\.email" :conversa-id="conversa\.id" :sinal="0" chat @abrir="abrirEmailDaVenda" \/>/)
    // O "abrir" sobe como a linha do tempo sobe outra conversa (o `ref` = o id).
    const emitidos = []
    const abrirEmailDaVenda = new Function('emit', `${trecho(pedido.scriptSetup.content, 'function abrirEmailDaVenda(', '\n}\n', '\n}\n')}\nreturn abrirEmailDaVenda`)((...a) => emitidos.push(a))
    abrirEmailDaVenda(ID1)
    assert.deepEqual(emitidos, [['irPara', { tipo: 'mensagem', em: null, texto: null, ref: ID1 }]])
    // A conversa: o `irPara` de verdade (trecho) com um id de conversa emite `abrirConversa`.
    const conversa = sfc('../components/AtendimentoConversa.vue').descriptor
    const setup = conversa.scriptSetup.content
    assert.match(conversa.template.content, /<AtendimentoPedido[\s\S]*?@ir-para="irPara"/)
    const reUuid = /const RE_UUID = (\/.+\/i)\n/.exec(setup)
    assert.ok(reUuid, 'RE_UUID')
    const irParaJs = trecho(setup, 'function irPara(ev: EventoCliente) {', '\n// Modo observação:')
    const daConversa = []
    const irPara = new Function(
      'detalhe', 'telaLarga', 'gaveta', 'abasDados', 'abaDaConversa', 'abaAtiva', 'nextTick', 'mostrarMensagem', 'rolarProFim', 'emit', 'toasts', 'window', 'RE_UUID',
      `${irParaJs}\nreturn irPara`,
    )(
      Vue.ref({ mensagens: [{ id: 'm1' }], conversa: { id: 'chat-1', plataforma: 'ml' } }), Vue.ref(false), Vue.ref(true), Vue.ref(null), Vue.ref(null), Vue.ref(null),
      Vue.nextTick, () => true, () => {}, (...a) => daConversa.push(a), { info: () => daConversa.push(['toast']) }, { open: () => daConversa.push(['window.open']) },
      new Function(`return ${reUuid[1]}`)(),
    )
    irPara(emitidos[0][1])
    assert.deepEqual(daConversa, [['abrirConversa', ID1]], 'a conversa pede para abrir a conversa de e-mail')
    // A página: na Caixa, abrir a conversa seleciona; a de e-mail que chega à
    // Caixa pelo id vai para a aba E-mail (não editamos a página: é o que ela já faz).
    const pagina = sfc('../pages/atendimento.vue').descriptor
    assert.match(pagina.template.content, /<AtendimentoConversa\s+v-if="selecionada"[\s\S]*?@mudou="aoMudarConversa"[\s\S]*?@abrir-conversa="selecionar"/)
    const js = trecho(pagina.scriptSetup.content, 'function aoMudarConversa(c: ConversaResumo) {', '\n  const i = itens.value.findIndex', '\n}\n')
    const st = { selecionada: Vue.ref(ID1), abertas: [] }
    new Function('selecionada', 'abrirConversaDoEmail', `${js}\nreturn aoMudarConversa`)(st.selecionada, (id) => st.abertas.push(id))({ id: ID1, email: { protocolo: null } })
    assert.equal(st.selecionada.value, null, 'saiu da Caixa')
    assert.deepEqual(st.abertas, [ID1], 'abriu na aba E-mail')
    assert.match(pagina.scriptSetup.content, /function abrirConversaDoEmail\(id: string\) \{\n\s+aba\.value = 'mail'/)
  }

  console.log('atendimento-email-venda-link: ok')
}

principal().catch((e) => {
  console.error(e)
  process.exit(1)
})
