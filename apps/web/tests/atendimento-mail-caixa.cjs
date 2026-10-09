// node tests/atendimento-mail-caixa.cjs — a aba E-mail › Caixas como no Tuta
// (09/10/2026): a coluna de PASTAS, o SELO da loja na lista e no e-mail
// aberto, o filtro por LOJA e os seletores do celular. Sem rede, sem caixa de
// verdade, sem e-mail de verdade. Aqui:
//  - o contrato com o backend (routers/mail_caixa.py, services/mail_atendimento/indice.py);
//  - as regras puras do selo e da coluna de pastas;
//  - os componentes de verdade (setup + Vue SSR, API falsa): a Central dele
//    (AtendimentoMail.vue) com a lista pelo índice, os filtros combinados, a
//    paginação, a volta para a lista antiga quando a rota nova não existe, o
//    cabeçalho do e-mail aberto; a coluna/seletor; o selo.
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
const api = (rel) => fs.readFileSync(path.resolve(__dirname, '../../api/app', rel), 'utf8')
const semComentarios = (html) => html.replace(/<!--[\s\S]*?-->/g, '')
const esperar = () => new Promise((r) => setTimeout(r, 0))

function sfc(rel) {
  const filename = path.resolve(__dirname, rel)
  const fonte = fs.readFileSync(filename, 'utf8')
  const { descriptor, errors } = parse(fonte, { filename })
  assert.deepEqual(errors, [], rel)
  return { descriptor, fonte, filename }
}
const icones = new Proxy({}, { get: (_, nome) => ({ name: String(nome), render: () => Vue.h('i', { 'data-icone': String(nome) }) }) })
const modulos = new Map()
function requerer(nome) {
  if (nome === 'lucide-vue-next') return icones
  const m = /^~\/components\/(.+\.vue)$/.exec(nome)
  if (!m) return require(nome)
  if (!modulos.has(m[1])) {
    const { descriptor } = sfc(`../components/${m[1]}`)
    const mod = { exports: {} }
    if (descriptor.script) new Function('require', 'module', 'exports', transpile(descriptor.script.content))(requerer, mod, mod.exports)
    modulos.set(m[1], mod.exports)
  }
  return modulos.get(m[1])
}

// Um componente filho de verdade (script setup + template), para o SSR.
const componentes = new Map()
function componente(arquivo) {
  if (componentes.has(arquivo)) return componentes.get(arquivo)
  const rel = `../components/${arquivo}`
  const { descriptor, filename } = sfc(rel)
  const script = compileScript(descriptor, { id: arquivo, inlineTemplate: false })
  const tpl = compileTemplate({ source: descriptor.template.content, filename, id: arquivo, compilerOptions: { isTS: true, bindingMetadata: script.bindings } })
  assert.deepEqual(tpl.errors, [], `${arquivo}: template compila`)
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(script.content))(requerer, mod, mod.exports)
  const tmod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(tpl.code))(requerer, tmod, tmod.exports)
  const c = { ...mod.exports.default, render: tmod.exports.render }
  componentes.set(arquivo, c)
  return c
}
const FILHOS = ['AtendimentoMailSelo', 'AtendimentoMailPastasDaCaixa', 'AtendimentoIconePlataforma']
function registrar(app) {
  app.config.warnHandler = (msg) => { throw new Error(`aviso do Vue: ${msg}`) }
  for (const nome of FILHOS) app.component(nome, componente(`${nome}.vue`))
  app.component('AtendimentoMailFilas', { props: ['isAdmin', 'lojas'], setup: () => () => Vue.h('div', { 'data-filas-stub': '' }) })
  app.component('AtendimentoMailConfigurar', { props: ['mailbox'], setup: () => () => Vue.h('div', { 'data-configurar-stub': '' }) })
  // O corpo em HTML do e-mail aberto (componente dele, desde 98a73730): fora deste teste.
  app.component('AtendimentoMailBody', { props: ['html', 'text', 'attachments'], setup: () => () => Vue.h('div', { 'data-body-stub': '' }) })
}
async function ssr(comp, props) {
  const a = Vue.createSSRApp({ render: () => Vue.h(comp, props) })
  registrar(a)
  return semComentarios(await renderToString(a))
}

// ------------------------------------------------ a Central dele, de verdade
// O setup roda fora de um app (os `watch` ficam vivos); o HTML sai de um app
// SSR com o MESMO estado. Só os globais que o teste DELE também dá (nada de
// nextTick/localStorage na Central).
function montarCentral(props, respostas) {
  const { descriptor, filename } = sfc('../components/AtendimentoMail.vue')
  const script = compileScript(descriptor, { id: 'mail', inlineTemplate: false })
  const tpl = compileTemplate({ source: descriptor.template.content, filename, id: 'mail', compilerOptions: { isTS: true, bindingMetadata: script.bindings } })
  assert.deepEqual(tpl.errors, [])
  const chamadas = []
  const perguntas = []
  // A ordem de chamada E de resposta (a lista demora um pouco, como a rota que indexa).
  const eventos = []
  const responder = async (url, opts = {}) => {
    chamadas.push({ url, metodo: opts.method || 'GET', body: opts.body })
    eventos.push(`chamou ${url}`)
    for (const [chave, valor] of Object.entries(respostas)) {
      if (url === chave || url.startsWith(`${chave}?`)) {
        if (url.includes('/lista?')) { await esperar(); await esperar() }
        const v = typeof valor === 'function' ? valor(url, opts) : valor
        eventos.push(`respondeu ${url}`)
        if (v instanceof Error) throw v
        return structuredClone(v)
      }
    }
    throw new Error(`rota inesperada: ${url}`)
  }
  const montados = []
  const relogios = []
  const g = {
    ref: Vue.ref, computed: Vue.computed, reactive: Vue.reactive, watch: Vue.watch,
    onMounted: (fn) => montados.push(fn), onBeforeUnmount: () => {},
    useApi: () => ({ url: (v) => v, api: responder }),
    crypto: { randomUUID: () => '00000000-0000-4000-8000-000000000000' },
    window: { confirm: (t) => { perguntas.push(t); return true } },
    document: { hidden: false }, setInterval: () => 1, clearInterval: () => {},
    setTimeout: (fn, ms) => { relogios.push({ fn, ms }); return relogios.length }, clearTimeout: () => {},
  }
  const nomes = Object.keys(g)
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', ...nomes, transpile(script.content))(requerer, mod, mod.exports, ...nomes.map((n) => g[n]))
  const tmod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(tpl.code))(requerer, tmod, tmod.exports)
  const comp = mod.exports.default
  const reativo = Vue.reactive({ ...props })
  const bruto = comp.setup(reativo, { emit: () => {}, expose: () => {}, attrs: {}, slots: {} })
  const estado = Vue.proxyRefs(bruto)
  const filho = { ...comp, render: tmod.exports.render, setup: () => bruto }
  return {
    estado, chamadas, perguntas, relogios, eventos,
    html: () => ssr(filho, reativo),
    async montar() { for (const fn of montados) await fn(); await assentar() },
  }
}
// O watch da caixa roda no próximo tick; as chamadas, nas promessas seguintes.
async function assentar() { for (let i = 0; i < 6; i++) { await Vue.nextTick(); await esperar() } }

const S = requerer('~/components/AtendimentoMailSelo.vue')
const P = requerer('~/components/AtendimentoMailPastasDaCaixa.vue')

// ------------------------------------------------ dados de exemplo
const caixa = { id: 'box1', label: 'Principal — Tuta', address: '061083.jf@tuta.com', aliases: ['061083.jf@tuta.com', '21max@tuta.com'], state: 'online', send_enabled: false, can_send: false, last_sync_at: null }
const caixa2 = { ...caixa, id: 'box2', label: 'Goslin — Tuta', address: 'goslin@tuta.com' }
const LOJA_JLAS2 = 'f:11111111-1111-4111-8111-111111111111'
const pastasResp = (q = {}) => ({
  pastas: [
    { id: null, nome: 'Todas', caminho: null, tipo: 'todas', quantidade: q.todas ?? 5, total: 5 },
    { id: 's1', nome: 'Entrada', caminho: null, tipo: 'sistema', quantidade: q.s1 ?? 3, total: 3 },
    { id: 's2', nome: 'Enviados', caminho: null, tipo: 'sistema', quantidade: q.s2 ?? 1, total: 1 },
    { id: 's5', nome: 'Spam', caminho: null, tipo: 'sistema', quantidade: 0, total: 0 },
    { id: 'p0123456789abcdef0123456789abcde', nome: '*7buyers', caminho: '*7buyers', tipo: 'pessoal', quantidade: q.p ?? 1, total: 1 },
  ],
  lojas: [
    { chave: LOJA_JLAS2, tipo: 'loja', rotulo: 'JLAS2 · ML', plataforma: 'ml', loja: 'JLAS2', quantidade: q.jlas2 ?? 2, total: 2 },
    { chave: 'm:22222222-2222-4222-8222-222222222222', tipo: 'site', rotulo: 'Site Uranyx', plataforma: 'site', loja: 'Uranyx', quantidade: 1, total: 1 },
    { chave: 'sem_loja', tipo: 'sem_loja', rotulo: 'sem loja', plataforma: null, loja: null, quantidade: 1, total: 1 },
    { chave: 'seguranca', tipo: 'seguranca', rotulo: 'segurança', plataforma: null, loja: null, quantidade: 1, total: 1 },
  ],
  faltam: q.faltam ?? 0,
})
const item = (id, k = {}) => ({
  id, recebido_em: '2026-10-09T11:00:00Z', direcao: 'inbound', tem_anexos: false, ponte: null, de: 'cliente@exemplo.com', de_nome: 'Ana Cliente',
  assunto: 'Dúvida do pedido', assunto_oculto: false, pasta: { id: 's1', nome: 'Entrada', tipo: 'sistema' },
  selo: { tipo: 'loja', rotulo: 'JLAS2 · ML', plataforma: 'ml', loja: 'JLAS2', chave: LOJA_JLAS2, provavel: true }, ...k,
})
const pagina1 = {
  itens: [
    item('e1', { assunto: 'Pergunta <b>importante</b>', tem_anexos: true }),
    item('e2', { de: 'no-reply@mercadolivre.com', de_nome: 'Mercado Livre', assunto: 'e-mail de acesso/código', assunto_oculto: true, selo: { tipo: 'seguranca', rotulo: 'segurança', plataforma: null, loja: null, chave: 'seguranca', provavel: true } }),
    item('e3', { pasta: { id: 'p0123456789abcdef0123456789abcde', nome: '*7buyers', tipo: 'pessoal' }, selo: { tipo: 'site', rotulo: 'Site Uranyx', plataforma: 'site', loja: 'Uranyx', chave: 'm:22222222-2222-4222-8222-222222222222', provavel: false }, ponte: 'gravado' }),
  ],
  proximo: '1760007600000000_00000000-0000-4000-8000-0000000000e3',
  faltam: 3,
}
const pagina2 = { itens: [item('e3'), item('e4', { direcao: 'sent', de_nome: null, de: null, assunto: '(não foi possível ler)', assunto_oculto: true, selo: { tipo: 'sem_loja', rotulo: 'sem loja', plataforma: null, loja: null, chave: 'sem_loja', provavel: true } })], proximo: null, faltam: 0 }
const detalhe = (id) => ({ id, mailbox_id: 'box1', subject: 'Pergunta importante', from_address: 'cliente@exemplo.com', from_name: 'Ana Cliente', to: ['21max@tuta.com'], cc: [], reply_to: null, received_at: '2026-10-09T11:00:00Z', direction: 'inbound', folder: 'INBOX', text: 'texto', has_attachments: false, attachments: [], reply: { to: 'cliente@exemplo.com', from_address: '21max@tuta.com', can_reply: false, send_ready: false }, outbox: [] })

async function principal() {
  // ------------------------------------------------ o contrato com o backend
  {
    const rotas = api('routers/mail_caixa.py')
    const indice = api('services/mail_atendimento/indice.py')
    const constantes = api('services/mail_atendimento/constantes.py')
    assert.match(rotas, /APIRouter\(prefix="\/api\/mail"/)
    assert.match(rotas, /@router\.get\("\/mailboxes\/\{mailbox_id\}\/pastas"\)/)
    assert.match(rotas, /@router\.get\("\/mailboxes\/\{mailbox_id\}\/lista"\)/)
    assert.match(api('main.py'), /app\.include_router\(mail_caixa_router\.router\)/)
    // A mesma permissão da caixa dele.
    assert.equal((rotas.match(/mailbox = await user_mailbox\(session, mailbox_id, user\)/g) || []).length, 2)
    // Os parâmetros que a tela manda, com os nomes que a rota lê.
    const lista = rotas.slice(rotas.indexOf('async def lista_da_caixa('))
    for (const p of ['pasta', 'loja', 'antes', 'limite']) assert.match(lista, new RegExp(`\\n    ${p}: `), `lista: ${p}`)
    const pastas = rotas.slice(rotas.indexOf('async def pastas_da_caixa('), rotas.indexOf('async def lista_da_caixa('))
    for (const p of ['loja', 'pasta']) assert.match(pastas, new RegExp(`\\n    ${p}: `), `pastas: ${p}`)
    // Os campos que a tela lê são os que as rotas devolvem.
    for (const campo of ['itens', 'proximo', 'faltam', 'id', 'recebido_em', 'direcao', 'tem_anexos', 'ponte', 'selo', 'chave', 'provavel', 'de', 'de_nome', 'assunto', 'assunto_oculto', 'pasta', 'nome', 'tipo']) {
      assert.ok(lista.includes(`"${campo}"`) || lista.includes(`${campo}=`), `lista: ${campo}`)
    }
    for (const campo of ['pastas', 'lojas', 'faltam', 'id', 'nome', 'caminho', 'tipo', 'quantidade', 'total', 'chave']) assert.ok(pastas.includes(`"${campo}"`), `pastas: ${campo}`)
    for (const campo of ['tipo', 'rotulo', 'plataforma', 'loja']) assert.ok(rotas.includes(`"${campo}":`), `selo: ${campo}`)
    assert.match(rotas, /"nome": "Todas",[\s\S]*?"tipo": "todas"/)
    // Os tipos do selo = os do índice; os rótulos fixos e o assunto de segurança.
    const selos = [...indice.matchAll(/^SELO_[A-Z_]+ = "([a-z_]+)"$/gm)].map((m) => m[1])
    assert.deepEqual([...selos].sort(), [...S.TIPOS_DE_SELO].sort())
    assert.match(indice, /SELO_SEM_LOJA: "sem loja",\s+SELO_SEGURANCA: "segurança",\s+SELO_PRIVADO: "privado",/)
    assert.match(indice, /^ASSUNTO_DE_SEGURANCA = "e-mail de acesso\/código"$/m)
    // O ícone de cada pasta de sistema: as chaves "s" + o tipo do Tuta.
    const kind = Object.fromEntries([...constantes.matchAll(/^KIND_([A-Z]+) = "(\d+)"$/gm)].map((m) => [m[1], m[2]]))
    const esperado = { ENTRADA: 'entrada', RASCUNHOS: 'rascunhos', ENVIADOS: 'enviados', LIXEIRA: 'lixeira', ARQUIVO: 'arquivo', SPAM: 'spam', AGENDADOS: 'agendados', TODOS: 'todas' }
    for (const [k, icone] of Object.entries(esperado)) assert.equal(P.iconeDaPasta({ id: `s${kind[k]}`, tipo: 'sistema' }), icone, `ícone de ${k}`)
    assert.match(indice, /^PASTA_ILEGIVEL = "x"$/m)
    assert.match(rotas, /_RE_PASTA = re\.compile\(r"\^\(s\\d\{1,2\}\|p\[0-9a-f\]\{31\}\|x\)\$"\)/)
    // As chaves de loja que a tela devolve como filtro passam no filtro da rota.
    const reLoja = new RegExp(rotas.match(/_UUID = r"([^"]+)"/)[1])
    assert.ok(reLoja.test(LOJA_JLAS2.slice(2)))
  }

  // ------------------------------------------------ regras puras: o selo
  {
    assert.equal(S.iconeDoSelo({ tipo: 'loja', plataforma: 'ml' }), 'plataforma')
    assert.equal(S.iconeDoSelo({ tipo: 'site', plataforma: 'site' }), 'plataforma')
    assert.equal(S.iconeDoSelo({ tipo: 'loja', plataforma: null }), 'loja')
    assert.equal(S.iconeDoSelo({ tipo: 'seguranca', plataforma: null }), 'seguranca')
    assert.equal(S.iconeDoSelo({ tipo: 'privado', plataforma: null }), 'privado')
    assert.equal(S.iconeDoSelo({ tipo: 'sem_loja', plataforma: null }), 'sem_loja')
    assert.equal(S.iconeDoSelo(null), 'sem_loja')
    assert.match(S.estiloDoSelo('seguranca'), /amber[\s\S]*dark:/, 'segurança: cor própria nos dois temas')
    assert.equal(S.estiloDoSelo('qualquer'), S.estiloDoSelo('sem_loja'))
    assert.match(S.dicaDoSelo({ tipo: 'loja', rotulo: 'JLAS2 · ML', plataforma: 'ml', provavel: true }), /^JLAS2 · ML — loja provável/)
    assert.match(S.dicaDoSelo({ tipo: 'loja', rotulo: 'JLAS2 · ML', plataforma: 'ml', provavel: false }), /decidida pela ponte/)
    assert.match(S.dicaDoSelo({ tipo: 'seguranca', rotulo: 'segurança', plataforma: null }), /assunto não aparece/)
    assert.equal(S.dicaDoSelo(null), '')
    // Contraste AA no claro (revisão de 09/10): o cinza muted-foreground dava 4,4:1.
    for (const tipo of ['privado', 'sem_loja']) assert.doesNotMatch(S.estiloDoSelo(tipo), /text-muted-foreground/, `contraste: ${tipo}`)
    // "(loja provável)" só quando o selo é uma loja.
    assert.equal(S.lojaProvavel({ tipo: 'loja', provavel: true }), true)
    assert.equal(S.lojaProvavel({ tipo: 'site', provavel: true }), true)
    assert.equal(S.lojaProvavel({ tipo: 'loja', provavel: false }), false)
    for (const tipo of ['privado', 'sem_loja', 'seguranca']) assert.equal(S.lojaProvavel({ tipo, provavel: true }), false, tipo)
  }

  // ------------------------------------------------ regras puras: a coluna de pastas
  {
    const r = pastasResp()
    const g = P.gruposDasPastas(r.pastas)
    assert.equal(g.todas.nome, 'Todas')
    assert.deepEqual(g.sistema.map((p) => p.nome), ['Entrada', 'Enviados', 'Spam'], 'a ordem da rota (a do Tuta)')
    assert.deepEqual(g.pessoais.map((p) => p.nome), ['*7buyers'])
    assert.deepEqual(P.gruposDasPastas([{ id: 'x', nome: 'Não lidos', tipo: 'ilegivel', quantidade: 1, total: 1 }]).outras.map((p) => p.id), ['x'])
    assert.equal(P.iconeDaPasta({ id: null, tipo: 'todas' }), 'todas')
    assert.equal(P.iconeDaPasta({ id: 'p1', tipo: 'pessoal' }), 'pasta')
    assert.equal(P.iconeDaPasta({ id: 'x', tipo: 'ilegivel' }), 'ilegivel')
    assert.equal(P.quantidadeLegivel(12345), '12.345')
    assert.equal(P.quantidadeLegivel(-3), '0')
    assert.equal(P.rotuloComQuantidade('Entrada', 80), 'Entrada (80)')
    assert.equal(P.valorDoSeletor(null), '')
    assert.equal(P.filtroDoSeletor(''), null)
    assert.equal(P.filtroDoSeletor('s1'), 's1')
    assert.equal(P.totalDasLojasNaPasta(r.lojas), 5, 'cada e-mail tem um selo só: a soma = a pasta')
    assert.equal(P.pastaEscolhida(r.pastas, 's2').nome, 'Enviados')
    assert.equal(P.lojaEscolhida(r.lojas, null), null)
    assert.equal(P.lojaEscolhida(r.lojas, LOJA_JLAS2).rotulo, 'JLAS2 · ML')
  }

  // ------------------------------------------------ o selo e a coluna, renderizados
  {
    let html = await ssr(componente('AtendimentoMailSelo.vue'), { selo: { tipo: 'loja', rotulo: 'JLAS2 · ML', plataforma: 'ml', provavel: true } })
    assert.match(html, /data-selo-tipo="loja"/)
    assert.match(html, /<svg[^>]*aria-hidden="true"/, 'o ícone da plataforma (decorativo: o rótulo já diz)')
    assert.match(html, /mercado-livre\.png/, 'o mesmo ícone do /atendimento')
    assert.match(html, />JLAS2 · ML</)
    assert.match(html, /sr-only"> \(loja provável\)/)
    html = await ssr(componente('AtendimentoMailSelo.vue'), { selo: { tipo: 'seguranca', rotulo: 'segurança', plataforma: null, provavel: true } })
    assert.match(html, /data-icone="ShieldAlert"[\s\S]*>segurança</)
    assert.doesNotMatch(html, /provável/)
    html = await ssr(componente('AtendimentoMailSelo.vue'), { selo: { tipo: 'privado', rotulo: 'privado', plataforma: null, provavel: true } })
    assert.match(html, /data-icone="Lock"/)
    assert.doesNotMatch(html, /provável/, 'privado não é "loja provável"')
    html = await ssr(componente('AtendimentoMailSelo.vue'), { selo: { tipo: 'sem_loja', rotulo: 'sem loja', plataforma: null, provavel: true } })
    assert.doesNotMatch(html, /provável/, 'sem loja não é "loja provável"')

    const r = pastasResp({ s1: 2, todas: 2, p: 0 })
    const coluna = componente('AtendimentoMailPastasDaCaixa.vue')
    html = await ssr(coluna, { modo: 'coluna', pastas: r.pastas, lojas: r.lojas, pasta: 's1', loja: LOJA_JLAS2 })
    assert.match(html, /data-mail-pastas-coluna/)
    const ordem = [...html.matchAll(/data-pasta="([^"]+)"/g)].map((m) => m[1])
    assert.deepEqual(ordem, ['todas', 's1', 's2', 's5', 'p0123456789abcdef0123456789abcde'], 'Todas, sistema na ordem do Tuta, depois as pessoais')
    assert.match(html, /data-icone="Inbox"[\s\S]*?>Entrada<\/span>[\s\S]*?>2<\/span>/)
    assert.match(html, /aria-current="true"[^>]*data-pasta="s1"/, 'a pasta escolhida')
    assert.match(html, /Suas pastas[\s\S]*\*7buyers/)
    assert.match(html, />Lojas<[\s\S]*Todas as lojas[\s\S]*>5<[\s\S]*JLAS2 · ML[\s\S]*Site Uranyx[\s\S]*sem loja[\s\S]*segurança/)
    assert.match(html, /aria-current="true"[^>]*data-loja="f:11111111/, 'a loja escolhida')
    assert.doesNotMatch(html, /data-mail-pastas-seletor/)
    // Pastas e lojas rolam SEPARADAS: com ~40 pastas, as lojas continuam à vista.
    assert.match(html, /<nav class="[^"]*\bflex-col\b[^"]*"[^>]*data-mail-pastas-coluna/)
    assert.match(html, /<div class="[^"]*\bflex-1\b[^"]*\boverflow-y-auto\b[^"]*" data-mail-pastas-rolagem>[\s\S]*Suas pastas[\s\S]*<\/div><div class="[^"]*\bmax-h-\[360px\][^"]*\bshrink-0\b[^"]*" data-mail-lojas-bloco>[\s\S]*>Lojas<[\s\S]*<ul class="[^"]*\boverflow-y-auto\b[^"]*" data-mail-lojas-coluna>/)
    assert.doesNotMatch(html.slice(0, html.indexOf('data-mail-lojas-bloco')), /data-loja=/, 'nenhuma loja dentro da rolagem das pastas')
    html = await ssr(coluna, { modo: 'seletor', pastas: r.pastas, lojas: r.lojas, pasta: null, loja: null })
    assert.match(html, /data-mail-pastas-seletor/)
    assert.match(html, /aria-label="Pasta"[\s\S]*<option value>Todas \(2\)<\/option><optgroup label="Pastas"><option value="s1">Entrada \(2\)<\/option>[\s\S]*<optgroup label="Suas pastas"><option value="p0123456789abcdef0123456789abcde">\*7buyers \(0\)<\/option>/)
    assert.match(html, /aria-label="Loja"[\s\S]*<option value>Todas as lojas \(5\)<\/option><option value="f:11111111-1111-4111-8111-111111111111">JLAS2 · ML \(2\)<\/option>/)

    // Clicar: avisa a Central (e não avisa de novo o que já está escolhido, nem quando está desligado).
    const emitidos = []
    const props = Vue.reactive({ modo: 'coluna', pastas: r.pastas, lojas: r.lojas, pasta: null, loja: null, desligado: false })
    const st = coluna.setup(props, { emit: (...a) => emitidos.push(a), expose: () => {}, attrs: {}, slots: {} })
    st.escolherPasta('s1')
    st.escolherPasta(null)
    st.escolherLoja('seguranca')
    props.desligado = true
    st.escolherLoja('sem_loja')
    assert.deepEqual(emitidos, [['pasta', 's1'], ['loja', 'seguranca']])
  }

  // ------------------------------------------------ a Central dele, pelo índice
  {
    const m = montarCentral({ isAdmin: true, canOperate: false }, {
      '/api/mail/mailboxes': { items: [caixa, caixa2] },
      '/api/mail/mailboxes/box1/lista': (url) => (url.includes('antes=') ? pagina2 : url.includes('pasta=s5') ? { itens: [], proximo: null, faltam: 0 } : pagina1),
      '/api/mail/mailboxes/box1/pastas': (url) => pastasResp(url.includes('loja=') ? { s1: 1, todas: 1, faltam: 3 } : { faltam: 3 }),
      '/api/mail/mailboxes/box2/lista': { itens: [], proximo: null, faltam: 0 },
      '/api/mail/mailboxes/box2/pastas': { pastas: [{ id: null, nome: 'Todas', caminho: null, tipo: 'todas', quantidade: 0, total: 0 }], lojas: [], faltam: 0 },
      '/api/mail/messages/e1': detalhe('e1'),
      '/api/mail/messages/e3': detalhe('e3'),
    })
    await m.montar()
    const urls = () => m.chamadas.map((c) => c.url)
    assert.deepEqual(urls(), ['/api/mail/mailboxes', '/api/mail/mailboxes/box1/lista?limite=50', '/api/mail/mailboxes/box1/pastas'])
    assert.deepEqual(m.eventos.slice(2), ['chamou /api/mail/mailboxes/box1/lista?limite=50', 'respondeu /api/mail/mailboxes/box1/lista?limite=50', 'chamou /api/mail/mailboxes/box1/pastas', 'respondeu /api/mail/mailboxes/box1/pastas'], 'a lista primeiro (a rota dela indexa o que chegou), DEPOIS as contagens: as duas leem o mesmo índice')
    assert.equal(m.estado.listSource, 'index')
    assert.deepEqual(m.estado.messages.map((i) => i.id), ['e1', 'e2', 'e3'])
    assert.equal(m.estado.messages[0].store.rotulo, 'JLAS2 · ML')
    assert.equal(m.estado.messages[0].folder, 'Entrada', 'o nome da pasta em português')
    assert.equal(m.estado.more, true)
    assert.equal(m.estado.organizing, 3)
    let html = await m.html()
    assert.match(html, /data-mail-pastas-coluna/, 'tela larga: a coluna de pastas')
    assert.match(html, /data-mail-pastas-seletor/, 'tela estreita: os seletores no topo da lista')
    assert.match(html, /<nav class="[^"]*\bhidden\b[^"]*\bxl:flex\b[^"]*"[^>]*data-mail-pastas-coluna/, 'a coluna só na tela larga (flex: pastas e lojas rolam separadas)')
    assert.match(html, /class="[^"]*\bxl:hidden\b[^"]*" data-mail-pastas-seletor/, 'os seletores só na tela estreita')
    assert.match(html, /xl:grid-cols-\[230px_340px_minmax\(0,1fr\)\]/, '3 colunas: pastas | lista | leitura')
    assert.match(html, /data-mail-list-header[\s\S]*?>Todas<\/h3>[\s\S]*?>5</)
    assert.match(html, /data-mail-organizing[^>]*>Organizando 3 e-mail\(s\)/)
    // Cada e-mail com o selo da loja (+ a pasta, em "Todas").
    const e1 = html.slice(html.indexOf('data-mail-item="e1"'), html.indexOf('data-mail-item="e2"'))
    assert.match(e1, /Ana Cliente[\s\S]*Pergunta &lt;b&gt;importante&lt;\/b&gt;/, 'o assunto é texto, nunca HTML')
    assert.match(e1, /data-mail-selo[^>]*data-selo-tipo="loja"[\s\S]*JLAS2 · ML/)
    assert.match(e1, /data-mail-item-folder[\s\S]*Entrada/)
    assert.match(e1, /data-icone="Paperclip"/)
    const e2 = html.slice(html.indexOf('data-mail-item="e2"'), html.indexOf('data-mail-item="e3"'))
    assert.match(e2, /italic[\s\S]*e-mail de acesso\/código/, 'segurança: sem o assunto')
    assert.match(e2, /data-selo-tipo="seguranca"/)
    const e3 = html.slice(html.indexOf('data-mail-item="e3"'))
    assert.match(e3, /data-selo-tipo="site"[\s\S]*Site Uranyx[\s\S]*\*7buyers/)
    assert.match(html, />Carregar mais</)

    // Faltam e-mails no índice: lê de novo daqui a pouco (um relógio só, com teto).
    assert.equal(m.relogios.length, 1)
    assert.equal(m.relogios[0].ms, 5000)
    const antesDoRelogio = m.chamadas.length
    m.relogios[0].fn()
    await assentar()
    assert.deepEqual(urls().slice(antesDoRelogio), ['/api/mail/mailboxes/box1/lista?limite=50', '/api/mail/mailboxes/box1/pastas'])
    assert.equal(m.relogios.length, 2, 'ainda faltam: outro relógio')
    for (let i = 0; i < 20; i++) { const r = m.relogios.at(-1); r.fn(); await assentar() }
    assert.equal(m.relogios.length, 12, 'no máximo 12 seguidos')

    // Carregar mais: o cursor da rota; sem repetir.
    await m.estado.loadMessages(true)
    assert.match(urls().at(-1), /^\/api\/mail\/mailboxes\/box1\/lista\?limite=50&antes=1760007600000000_00000000-0000-4000-8000-0000000000e3$/)
    assert.deepEqual(m.estado.messages.map((i) => i.id), ['e1', 'e2', 'e3', 'e4'])
    assert.equal(m.estado.more, false)
    html = await m.html()
    assert.match(html.slice(html.indexOf('data-mail-item="e4"')), /\(remetente ilegível\)[\s\S]*\(não foi possível ler\)[\s\S]*data-selo-tipo="sem_loja"[\s\S]*Enviado/)

    // Abrir: o cabeçalho com o selo e a pasta (no lugar do nome cru "INBOX").
    await m.estado.openMessage('e1')
    html = await m.html()
    const cab = html.slice(html.indexOf('<header class="space-y-2 border-b pb-4">'), html.indexOf('</header>'))
    assert.match(cab, /data-mail-open-badge[\s\S]*Loja:[\s\S]*JLAS2 · ML[\s\S]*Pasta:[\s\S]*Entrada/)
    assert.doesNotMatch(cab, /INBOX/)

    // Filtro por PASTA: a lista e as contagens das lojas daquela pasta.
    const antes = m.chamadas.length
    m.estado.chooseFilter('folder', 's1')
    assert.deepEqual(m.estado.messages, [], 'a lista antiga sai na hora')
    await assentar()
    assert.deepEqual(urls().slice(antes), ['/api/mail/mailboxes/box1/lista?limite=50&pasta=s1', '/api/mail/mailboxes/box1/pastas?pasta=s1'])
    // ... combinado com a LOJA.
    m.estado.chooseFilter('store', LOJA_JLAS2)
    await assentar()
    assert.deepEqual(urls().slice(-2), [
      '/api/mail/mailboxes/box1/lista?limite=50&pasta=s1&loja=f%3A11111111-1111-4111-8111-111111111111',
      '/api/mail/mailboxes/box1/pastas?pasta=s1&loja=f%3A11111111-1111-4111-8111-111111111111',
    ])
    html = await m.html()
    assert.match(html, /data-mail-list-header[\s\S]*?>Entrada<\/h3>[\s\S]*?>1<[\s\S]*?data-mail-store-filter[\s\S]*?JLAS2 · ML/, 'o título: a pasta, a quantidade daquela loja e o filtro da loja')
    assert.doesNotMatch(html, /data-mail-item-folder/, 'com uma pasta escolhida, a lista não repete a pasta')
    assert.match(html, /data-mail-open-badge/, 'o e-mail aberto continua com o selo')
    // Pasta vazia.
    m.estado.chooseFilter('folder', 's5')
    await assentar()
    html = await m.html()
    assert.match(html, /Nenhum e-mail desta loja nesta pasta\./)
    // Tirar a loja (o × do título).
    m.estado.chooseFilter('store', null)
    m.estado.chooseFilter('folder', null)
    await assentar()
    assert.equal(urls().filter((u) => u.includes('/lista?')).at(-1), '/api/mail/mailboxes/box1/lista?limite=50')
    assert.equal(urls().at(-1), '/api/mail/mailboxes/box1/pastas')
    assert.deepEqual(m.estado.messages.map((i) => i.id), ['e1', 'e2', 'e3'], 'a resposta velha (de antes do clique) não vale')

    // Enquanto envia, nada de trocar o filtro.
    m.estado.sending = true
    const n = m.chamadas.length
    m.estado.chooseFilter('folder', 's1')
    assert.equal(m.chamadas.length, n)
    assert.equal(m.estado.folderId, null)
    m.estado.sending = false

    // Outra caixa: os filtros voltam para "Todas".
    m.estado.chooseFilter('folder', 's2')
    await assentar()
    m.estado.mailboxId = 'box2'
    await assentar()
    assert.equal(m.estado.folderId, null)
    assert.equal(m.estado.storeKey, null)
    assert.deepEqual(urls().slice(-2), ['/api/mail/mailboxes/box2/lista?limite=50', '/api/mail/mailboxes/box2/pastas'])
    assert.ok(!urls().some((u) => u.startsWith('/api/mail/mailboxes/box2/') && u.includes('pasta=')), 'a pasta da outra caixa nunca vai na URL')
    assert.equal(m.estado.openedSummary, null)
    assert.equal(m.estado.organizing, 0)
  }

  // ------------------------------------------------ a pasta escolhida sumiu: volta para "Todas"
  {
    let sumiu = false
    const m = montarCentral({ isAdmin: true }, {
      '/api/mail/mailboxes': { items: [caixa] },
      '/api/mail/mailboxes/box1/lista': pagina1,
      '/api/mail/mailboxes/box1/pastas': () => {
        const r = pastasResp()
        if (sumiu) r.pastas = r.pastas.filter((p) => p.id !== 'p0123456789abcdef0123456789abcde')
        return r
      },
    })
    await m.montar()
    m.estado.chooseFilter('folder', 'p0123456789abcdef0123456789abcde')
    await assentar()
    sumiu = true
    await m.estado.loadFolders()
    await assentar()
    assert.equal(m.estado.folderId, null)
    assert.equal(m.chamadas.at(-1).url, '/api/mail/mailboxes/box1/pastas')
  }

  // ------------------------------------------------ sem a rota nova: a lista de antes, sem coluna
  {
    const m = montarCentral({ isAdmin: false }, {
      '/api/mail/mailboxes': { items: [caixa] },
      '/api/mail/mailboxes/box1/lista': Object.assign(new Error('404'), { status: 404 }),
      '/api/mail/mailboxes/box1/pastas': Object.assign(new Error('404'), { status: 404 }),
      '/api/mail/mailboxes/box1/messages': { items: [{ id: 'm1', subject: 'Oi <i>x</i>', from_address: 'c@x.com', from_name: 'C', received_at: '2026-10-08T12:00:00Z', direction: 'inbound', folder: 'INBOX', has_attachments: true }], more: true },
      '/api/mail/messages/m1': { ...detalhe('m1'), folder: 'INBOX' },
    })
    await m.montar()
    assert.deepEqual(m.chamadas.map((c) => c.url).sort(), ['/api/mail/mailboxes', '/api/mail/mailboxes/box1/lista?limite=50', '/api/mail/mailboxes/box1/messages?limit=50&offset=0', '/api/mail/mailboxes/box1/pastas'].sort())
    assert.ok(m.chamadas.findIndex((c) => c.url.includes('/lista?')) < m.chamadas.findIndex((c) => c.url.includes('/messages?')), 'tenta o índice antes')
    assert.equal(m.estado.listSource, 'plain')
    assert.equal(m.estado.error, '', 'nenhum erro na tela')
    await m.estado.loadMessages(true)
    assert.equal(m.chamadas.at(-1).url, '/api/mail/mailboxes/box1/messages?limit=50&offset=1', 'carregar mais: a paginação dele')
    await m.estado.openMessage('m1')
    const html = await m.html()
    assert.doesNotMatch(html, /data-mail-pastas-coluna|data-mail-pastas-seletor|data-mail-list-header|data-mail-selo|data-mail-open-badge/)
    assert.match(html, /lg:grid-cols-\[340px_1fr\]/, 'o layout de antes')
    assert.match(html, /Oi &lt;i&gt;x&lt;\/i&gt;[\s\S]*Com anexo/)
    assert.match(html, /· INBOX<\/p>/, 'o cabeçalho de antes, com a pasta crua')
  }

  // ------------------------------------------------ rota que não existe (405) também volta; ERRO do servidor não volta
  {
    const m405 = montarCentral({ isAdmin: true }, {
      '/api/mail/mailboxes': { items: [caixa] },
      '/api/mail/mailboxes/box1/lista': Object.assign(new Error('405'), { statusCode: 405 }),
      '/api/mail/mailboxes/box1/pastas': Object.assign(new Error('405'), { statusCode: 405 }),
      '/api/mail/mailboxes/box1/messages': { items: [{ id: 'm1', subject: 'Oi', from_address: 'c@x.com', from_name: 'C', received_at: '2026-10-08T12:00:00Z', direction: 'inbound', folder: 'INBOX', has_attachments: false }], more: false },
    })
    await m405.montar()
    assert.equal(m405.estado.listSource, 'plain')
    assert.deepEqual(m405.estado.messages.map((i) => i.id), ['m1'])

    // A rota existe mas falhou (500 — p.ex. a 0388 ainda não aplicada): a lista
    // simples mostraria o assunto de um e-mail de segurança. O erro aparece.
    const m = montarCentral({ isAdmin: true }, {
      '/api/mail/mailboxes': { items: [caixa] },
      '/api/mail/mailboxes/box1/lista': Object.assign(new Error('500'), { status: 500, response: { status: 500 } }),
      '/api/mail/mailboxes/box1/pastas': Object.assign(new Error('500'), { status: 500 }),
      '/api/mail/mailboxes/box1/messages': { items: [{ id: 'm9', subject: 'Seu código de acesso 551177', from_address: 'no-reply@mercadolivre.com', from_name: 'ML', received_at: '2026-10-08T12:00:00Z', direction: 'inbound', folder: 'INBOX', has_attachments: false }], more: false },
    })
    await m.montar()
    assert.ok(!m.chamadas.some((c) => c.url.includes('/messages?')), 'erro do servidor: não volta para a lista simples')
    assert.match(m.estado.error, /Não foi possível concluir/)
    assert.deepEqual(m.estado.messages, [])
    assert.doesNotMatch(await m.html(), /551177/)
  }

  // ------------------------------------------------ com filtro e a rota falhando: o erro aparece (não a lista sem filtro)
  {
    let falhar = false
    const m = montarCentral({ isAdmin: true }, {
      '/api/mail/mailboxes': { items: [caixa] },
      '/api/mail/mailboxes/box1/lista': () => (falhar ? new Error('500') : pagina1),
      '/api/mail/mailboxes/box1/pastas': pastasResp(),
      '/api/mail/mailboxes/box1/messages': { items: [], more: false },
    })
    await m.montar()
    falhar = true
    m.estado.chooseFilter('folder', 's1')
    await assentar()
    assert.ok(!m.chamadas.some((c) => c.url.includes('/messages?')), 'não volta para a lista sem filtro')
    assert.match(m.estado.error, /Não foi possível concluir/)
    assert.equal(m.estado.folderId, 's1')
  }

  console.log('ok — atendimento-mail-caixa: contrato, selo, coluna de pastas, seletores, lista pelo índice, filtros combinados, paginação, cabeçalho, volta para a lista antiga só sem a rota, erro do servidor e com filtro')
}

principal().catch((erro) => { console.error(erro); process.exitCode = 1 })
