// node tests/atendimento-email-lojas.cjs — os E-MAILS de cada loja na barra de
// lojas da Caixa (27/10/2026). O Eduardo: "botar um AMPLIÁVEL que vai abrir os
// e-mails embaixo de cada loja, com as 3 categorias linkadas pra cada loja —
// INFORMATIVO, CHAMADO e VENDA … então poderá ampliar e visualizar os e-mails
// ali pelo atendimento mesmo de cada loja". Sem rede e sem e-mail de verdade:
//  - as funções puras (qual linha da barra tem e-mail, o número e a bolinha de
//    cada tipo, as URLs da lista da loja — nunca a lista de todas as lojas);
//  - a barra renderizada (Vue SSR, setup de verdade): a seta só na loja com
//    e-mail e só na barra aberta; os três tipos embaixo; sem `emails`, a barra
//    de sempre (o mesmo HTML); com os e-mails de uma loja na Caixa, nenhuma
//    linha do chat acesa;
//  - a lista da loja (setup de verdade, API falsa): a rota de "Todas as
//    conversas" da aba E-mail com a loja e o tipo; abertas/fechadas, busca,
//    tique; as linhas das pastas informativas só para quem mexe;
//  - a página: a barra, a lista e a conversa ligadas; o chat guardado; as
//    lojas abertas lembradas POR PESSOA (localStorage com try).
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
const transpileTs = (source) => ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText

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
const web = (rel) => fs.readFileSync(path.resolve(__dirname, '..', rel), 'utf8')
const semComentarios = (html) => html.replace(/<!--[\s\S]*?-->/g, '')
const esperar = () => new Promise((r) => setTimeout(r, 0))
const ICONE = { props: ['plataforma'], render() { return Vue.h('i', { 'data-icone': this.plataforma }) } }

// O setup de verdade fora de um app (os `watch` ficam vivos), o HTML de um
// app SSR com o MESMO estado — o molde de tests/atendimento-mail-atendimento.cjs.
function montar(rel, props, { respostas = {}, filhos = {} } = {}) {
  const { descriptor, filename } = sfc(rel)
  const script = compileScript(descriptor, { id: path.basename(rel), inlineTemplate: false })
  const tpl = compileTemplate({ source: descriptor.template.content, filename, id: path.basename(rel), compilerOptions: { isTS: true, bindingMetadata: script.bindings } })
  const chamadas = []
  const avisos = []
  const emitidos = []
  const montados = []
  const responder = async (url, opts = {}) => {
    chamadas.push({ url, metodo: opts.method || 'GET' })
    for (const [chave, valor] of Object.entries(respostas)) {
      if (url === chave || url.startsWith(`${chave}?`)) {
        const v = typeof valor === 'function' ? valor(url, opts) : valor
        if (v instanceof Error) throw v
        return structuredClone(v)
      }
    }
    throw new Error(`rota inesperada: ${url}`)
  }
  const g = {
    ref: Vue.ref, computed: Vue.computed, reactive: Vue.reactive, watch: Vue.watch, nextTick: Vue.nextTick,
    onMounted: (fn) => montados.push(fn), onBeforeUnmount: () => {},
    useApi: () => ({ url: (v) => v, api: responder }),
    useToasts: () => ({ success: (t) => avisos.push(['ok', t]), error: (t) => avisos.push(['erro', t]), info: (t) => avisos.push(['info', t]) }),
  }
  const nomes = Object.keys(g)
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', ...nomes, transpile(script.content))(requerer, mod, mod.exports, ...nomes.map((n) => g[n]))
  const tmod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(tpl.code))(requerer, tmod, tmod.exports)
  const comp = mod.exports.default
  const reativo = Vue.reactive({ ...props })
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
    a.component('AtendimentoIconePlataforma', ICONE)
    for (const [n, c] of Object.entries(filhos)) a.component(n, c)
    return semComentarios(await renderToString(a))
  }
  return {
    html, estado, chamadas, avisos, emitidos, reativo,
    async montar() { for (const fn of montados) await fn(); await esperar() },
  }
}

// A barra renderizada como nos outros testes dela (compileScript inline).
const lojasSfc = sfc('../components/AtendimentoLojas.vue')
async function renderizarBarra(props) {
  const { content } = compileScript(lojasSfc.descriptor, { id: 'barra', inlineTemplate: true })
  const mod = {}
  const g = { computed: Vue.computed, ref: Vue.ref, watch: Vue.watch, nextTick: Vue.nextTick }
  const nomes = Object.keys(g)
  new Function('exports', 'require', ...nomes, transpile(content))(mod, requerer, ...nomes.map((n) => g[n]))
  const app = Vue.createSSRApp(mod.default, props)
  app.config.warnHandler = () => {}
  app.component('AtendimentoIconePlataforma', ICONE)
  return semComentarios(await renderToString(app))
}

const LJ = requerer('~/components/AtendimentoLojas.vue')
const LE = requerer('~/components/AtendimentoLojaEmails.vue')

const conta = (abertas, aguardando = 0, total = abertas, linhas = 0) => ({ abertas, aguardando, total, linhas })
const POR_LOJA = [
  { integration_id: 'i-sh', store_info_id: null, plataforma: 'shopee', loja: null, total: 620, tipos: { informativo: conta(239, 0, 240), chamado: conta(37, 2), venda: conta(341) } },
  { integration_id: 'i-ml', store_info_id: null, plataforma: 'ml', loja: null, total: 0, tipos: {} },
  // A loja do robô (sem integração): pela ficha de mesma plataforma e mesmo nome.
  { integration_id: null, store_info_id: 'f-temu', plataforma: 'temu', loja: 'Barbosa', total: 59, tipos: { informativo: conta(40), venda: conta(19) } },
  // Duas fichas "Eron" na Temu: ambígua — sem seta (nunca a errada).
  { integration_id: null, store_info_id: 'f-eron1', plataforma: 'temu', loja: 'eron', total: 3, tipos: { venda: conta(3) } },
  { integration_id: null, store_info_id: 'f-eron2', plataforma: 'temu', loja: 'Éron', total: 2, tipos: { venda: conta(2) } },
]
const RESUMO = {
  plataformas: [{ plataforma: 'shopee', aguardando: 5 }, { plataforma: 'ml', aguardando: 0 }, { plataforma: 'temu', aguardando: 0 }, { plataforma: 'site', aguardando: 0 }],
  lojas: [
    { integration_id: 'i-sh', plataforma: 'shopee', conta: 'ATV', aguardando: 5, vencidas: 0, nao_lidas_duoke: 71 },
    { integration_id: 'i-sh2', plataforma: 'shopee', conta: 'Aguiar', aguardando: 0, vencidas: 0 },
    { integration_id: 'i-ml', plataforma: 'ml', conta: 'Poofy', aguardando: 0, vencidas: 0 },
    { integration_id: null, plataforma: 'temu', conta: 'barbosa', aguardando: 0, vencidas: 0 },
    { integration_id: null, plataforma: 'temu', conta: 'Eron', aguardando: 0, vencidas: 0 },
    { integration_id: null, plataforma: 'site', externo_ref: 'site:charlots', conta: 'Charlots', aguardando: 0, vencidas: 0 },
  ],
  canais: [],
  flags: {},
}
const filtros = (o = {}) => ({ plataforma: '', integration_id: '', canal: '', filtro: 'todas', q: '', externo_ref: '', rede_social_id: '', plataforma_topo: '', ...o })

async function principal() {
  // ------------------------------------------------ as funções puras da barra
  {
    const linha = (o) => ({ chave: o.integration_id || `semid:${o.plataforma}:${o.conta}`, externo_ref: '', rede_social_id: '', ...o })
    assert.deepEqual(LJ.TIPOS_EMAIL_DA_LOJA.map((t) => t.label), ['Informativo', 'Chamado', 'Venda'])
    // Pela integração.
    const atv = LJ.emailsDaLoja(POR_LOJA, linha({ integration_id: 'i-sh', plataforma: 'shopee', conta: 'ATV' }))
    assert.equal(atv.integration_id, 'i-sh')
    // Sem e-mail nenhum (total 0) ou sem linha na resposta: sem seta.
    assert.equal(LJ.emailsDaLoja(POR_LOJA, linha({ integration_id: 'i-ml', plataforma: 'ml', conta: 'Poofy' })), null)
    assert.equal(LJ.emailsDaLoja(POR_LOJA, linha({ integration_id: 'i-sh2', plataforma: 'shopee', conta: 'Aguiar' })), null)
    assert.equal(LJ.emailsDaLoja(null, linha({ integration_id: 'i-sh', plataforma: 'shopee', conta: 'ATV' })), null)
    // A loja do robô: a ficha de mesma plataforma e mesmo nome (sem acento/caixa).
    assert.equal(LJ.emailsDaLoja(POR_LOJA, linha({ integration_id: '', plataforma: 'temu', conta: ' barbosa ' })).store_info_id, 'f-temu')
    assert.equal(LJ.emailsDaLoja(POR_LOJA, linha({ integration_id: '', plataforma: 'aliexpress', conta: 'Barbosa' })), null, 'outra plataforma')
    assert.equal(LJ.emailsDaLoja(POR_LOJA, linha({ integration_id: '', plataforma: 'temu', conta: 'Eron' })), null, 'duas fichas iguais: nunca a errada')
    // O site e as contas de rede não têm loja de e-mail na barra.
    assert.equal(LJ.emailsDaLoja(POR_LOJA, linha({ integration_id: '', plataforma: 'temu', conta: 'Barbosa', externo_ref: 'site:x' })), null)
    assert.equal(LJ.emailsDaLoja(POR_LOJA, linha({ integration_id: '', plataforma: 'temu', conta: 'Barbosa', rede_social_id: 'r1' })), null)
    // O número do tipo = o que a lista mostra (abertas + as linhas informativas de quem mexe).
    const comLinhas = { ...POR_LOJA[0], tipos: { ...POR_LOJA[0].tipos, informativo: conta(239, 0, 240, 4) } }
    assert.deepEqual(LJ.TIPOS_EMAIL_DA_LOJA.map((t) => LJ.numeroDoTipo(comLinhas, t.value)), [243, 37, 341])
    assert.equal(LJ.numeroDoTipo(null, 'venda'), 0)
    assert.equal(LJ.esperandoDoTipo(atv, 'chamado'), 2)
    assert.equal(LJ.esperandoDoTipo(atv, 'venda'), 0)
    assert.equal(LJ.tituloDoTipo(atv, 'chamado', 'ATV'), 'Chamado · ATV: 37 conversa(s) de e-mail aberta(s) · 2 esperando resposta\nAbre os e-mails desta loja aqui na Caixa')
    assert.match(LJ.tituloDoTipo(atv, 'informativo', 'ATV'), /239 conversa\(s\) de e-mail aberta\(s\) · 1 fechada\(s\)/)
    assert.equal(LJ.tituloDoAmpliavel(atv, 'ATV', false), 'Mostrar os e-mails da loja ATV (Informativo 239 · Chamado 37 · Venda 341)')
    assert.match(LJ.tituloDoAmpliavel(atv, 'ATV', true), /^Esconder/)
    // O clique: a loja (integração OU ficha, nunca as duas) e o tipo.
    assert.deepEqual(LJ.escolhaDoEmail(linha({ integration_id: 'i-sh', plataforma: 'shopee', conta: 'ATV' }), atv, 'venda'), { chave: 'i-sh', integration_id: 'i-sh', store_info_id: '', plataforma: 'shopee', conta: 'ATV', tipo: 'venda' })
    const temu = LJ.emailsDaLoja(POR_LOJA, linha({ integration_id: '', plataforma: 'temu', conta: 'barbosa' }))
    assert.deepEqual(LJ.escolhaDoEmail(linha({ integration_id: '', plataforma: 'temu', conta: 'barbosa' }), temu, 'informativo'), { chave: 'semid:temu:barbosa', integration_id: '', store_info_id: 'f-temu', plataforma: 'temu', conta: 'barbosa', tipo: 'informativo' })
    assert.deepEqual(LJ.alternarAberta([], 'a'), ['a'])
    assert.deepEqual(LJ.alternarAberta(['a', 'b'], 'a'), ['b'])
  }

  // ------------------------------------------------ a barra renderizada
  {
    const sem = await renderizarBarra({ resumo: RESUMO, filtros: filtros(), numero: 'nao_lidas_duoke' })
    // Sem `emails` (a Caixa Humano, a API antiga): nenhuma seta — e o MESMO HTML com `emails: null`.
    assert.doesNotMatch(sem, /data-ampliar-emails|data-emails-loja/)
    assert.equal(await renderizarBarra({ resumo: RESUMO, filtros: filtros(), numero: 'nao_lidas_duoke', emails: null }), sem)
    // Com os números: a seta só na loja com e-mail (ATV e a Barbosa da Temu), fechada.
    let html = await renderizarBarra({ resumo: RESUMO, filtros: filtros(), numero: 'nao_lidas_duoke', emails: POR_LOJA })
    assert.deepEqual([...html.matchAll(/data-ampliar-emails="([^"]+)"/g)].map((m) => m[1]), ['i-sh', 'semid:temu:barbosa'])
    assert.match(html, /aria-expanded="false" aria-controls="emails-loja-i-sh" aria-label="Mostrar os e-mails da loja ATV \(Informativo 239 · Chamado 37 · Venda 341\)"/)
    assert.doesNotMatch(html, /data-emails-loja=/, 'fechada: sem os tipos')
    // A linha da loja continua igual (o número do Duoke, o clique filtra o chat); só abre espaço para a seta.
    assert.match(html, /<button type="button" class="relative mx-1 [^"]*pr-7"[^>]*title="Shopee · ATV[^"]*">[\s\S]*?>71<\/span><\/button>/)
    assert.match(html, /<button type="button" class="relative mx-1 [^"]*"[^>]*title="Shopee · Aguiar/)
    assert.doesNotMatch((html.match(/<button type="button" class="relative mx-1 [^"]*"[^>]*title="Shopee · Aguiar/) || [''])[0], /pr-7/)
    // Aberta: os três tipos, com o número; a bolinha vermelha só onde há e-mail esperando resposta.
    html = await renderizarBarra({ resumo: RESUMO, filtros: filtros(), numero: 'nao_lidas_duoke', emails: POR_LOJA, emailsAbertos: ['i-sh'] })
    assert.match(html, /aria-expanded="true" aria-controls="emails-loja-i-sh"/)
    const grupo = (html.match(/<div id="emails-loja-i-sh" role="group" aria-label="E-mails da loja ATV"[\s\S]*?<\/div>/) || [''])[0]
    assert.ok(grupo, 'o grupo dos tipos embaixo da loja')
    const tipos = [...grupo.matchAll(/data-email-tipo="([^"]+)"[^>]*>([\s\S]*?)<\/button>/g)].map((m) => [m[1], m[2].replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()])
    assert.deepEqual(tipos, [['informativo', 'Informativo 239'], ['chamado', 'Chamado 2 37'], ['venda', 'Venda 341']], 'o número cinza até 999; a bolinha vermelha (2) só no Chamado')
    assert.equal((grupo.match(/data-email-esperando/g) || []).length, 1)
    assert.match(grupo, /title="Chamado · ATV: 37 conversa\(s\) de e-mail aberta\(s\) · 2 esperando resposta\nAbre os e-mails desta loja aqui na Caixa"/)
    assert.match(grupo, /aria-pressed="false"/)
    // A loja do robô aberta (a chave tem ":"): o id do grupo é seguro.
    html = await renderizarBarra({ resumo: RESUMO, filtros: filtros(), numero: 'nao_lidas_duoke', emails: POR_LOJA, emailsAbertos: ['semid:temu:barbosa'] })
    assert.match(html, /id="emails-loja-semid_temu_barbosa"/)
    assert.match(html, /data-email-tipo="chamado"[^>]*>[\s\S]*?>0<\/span>/, 'tipo sem e-mail: 0, apagado')
    // Recolhida: não cabe — sem seta nem tipos (a lembrança fica para quando abrir).
    html = await renderizarBarra({ resumo: RESUMO, filtros: filtros(), numero: 'nao_lidas_duoke', emails: POR_LOJA, emailsAbertos: ['i-sh'], recolhida: true })
    assert.doesNotMatch(html, /data-ampliar-emails|data-emails-loja/)
    // Os e-mails de uma loja na Caixa: aceso fica o tipo, nenhuma linha do chat.
    html = await renderizarBarra({ resumo: RESUMO, filtros: filtros({ plataforma: 'shopee', integration_id: 'i-sh' }), numero: 'nao_lidas_duoke', emails: POR_LOJA, emailsAbertos: ['i-sh'], emailAberto: { chave: 'i-sh', tipo: 'venda' } })
    assert.match(html, /aria-pressed="true"[^>]*data-email-tipo="venda"/)
    assert.doesNotMatch(html, /<button type="button" class="relative mx-1[^"]*"[^>]*aria-pressed="true"/, 'nenhuma loja do chat acesa')
    assert.match(html, /aria-pressed="false" title="Todas as lojas/)
    // Sem os e-mails na Caixa: a loja do filtro acesa, como sempre.
    html = await renderizarBarra({ resumo: RESUMO, filtros: filtros({ plataforma: 'shopee', integration_id: 'i-sh' }), numero: 'nao_lidas_duoke', emails: POR_LOJA })
    assert.match(html, /<button type="button" class="relative mx-1[^"]*"[^>]*aria-pressed="true" title="Shopee · ATV/)
  }

  // ------------------------------------------------ a barra: o clique (setup de verdade)
  {
    const b = montar('../components/AtendimentoLojas.vue', { resumo: RESUMO, filtros: filtros(), numero: 'nao_lidas_duoke', emails: POR_LOJA, emailsAbertos: [] })
    const atv = b.estado.lojas.find((l) => l.integration_id === 'i-sh')
    assert.ok(b.estado.emailsDe(atv))
    b.estado.alternarEmails(atv)
    assert.deepEqual(b.estado.emailsAbertos, ['i-sh'])
    b.estado.alternarEmails(atv)
    assert.deepEqual(b.estado.emailsAbertos, [])
    b.estado.abrirEmails(atv, 'chamado')
    assert.deepEqual(b.emitidos, [['abrirEmails', { chave: 'i-sh', integration_id: 'i-sh', store_info_id: '', plataforma: 'shopee', conta: 'ATV', tipo: 'chamado' }]])
    // Loja sem e-mail: o clique não manda nada.
    b.estado.abrirEmails(b.estado.lojas.find((l) => l.integration_id === 'i-sh2'), 'venda')
    assert.equal(b.emitidos.length, 1)
  }

  // ------------------------------------------------ a lista da loja: funções puras
  {
    const sh = { chave: 'i-sh', integration_id: 'i-sh', store_info_id: '', tipo: 'venda' }
    assert.equal(LE.urlDosEmailsDaLoja(sh), '/api/atendimento/conversas?caixa=email&filtro=todas&limite=50&tipo_email=venda&integration_id=i-sh')
    assert.equal(LE.urlDosEmailsDaLoja(sh, { fechadas: true, busca: ' Maria ', antesDe: 'c1' }), '/api/atendimento/conversas?caixa=email&filtro=fechadas&q=Maria&limite=50&tipo_email=venda&antes_de=c1&integration_id=i-sh')
    const ficha = { chave: 'semid:temu:barbosa', integration_id: '', store_info_id: 'f-temu', tipo: 'informativo' }
    assert.equal(LE.urlDosEmailsDaLoja(ficha), '/api/atendimento/conversas?caixa=email&filtro=todas&limite=50&tipo_email=informativo&store_info_id=f-temu')
    // Sem loja: NUNCA a lista de todas as lojas.
    assert.equal(LE.urlDosEmailsDaLoja({ integration_id: '', store_info_id: '', tipo: 'venda' }), null)
    assert.equal(LE.urlDasLinhasDaLoja(sh), '/api/atendimento/email/emails?fila=informativo&integration_id=i-sh')
    assert.equal(LE.urlDasLinhasDaLoja(ficha, 'x'), '/api/atendimento/email/emails?fila=informativo&store_info_id=f-temu&antes_de=x')
    assert.equal(LE.urlDasLinhasDaLoja({ integration_id: '', store_info_id: '', tipo: 'informativo' }), null)
    assert.equal(LE.mostraLinhas('informativo', true, false, ''), true)
    assert.equal(LE.mostraLinhas('informativo', false, false, ''), false, 'quem só lê: a rota recusaria')
    assert.equal(LE.mostraLinhas('venda', true, false, ''), false)
    assert.equal(LE.mostraLinhas('informativo', true, true, ''), false)
    assert.equal(LE.mostraLinhas('informativo', true, false, 'x'), false)
    assert.equal(LE.vazioDaLoja('venda', false, ''), 'Nenhum e-mail venda aberto desta loja.')
    assert.equal(LE.vazioDaLoja('chamado', true, ''), 'Nenhum e-mail chamado fechado desta loja.')
    assert.equal(LE.vazioDaLoja('chamado', true, 'x'), 'Nada encontrado para essa busca nesta loja.')
    assert.notEqual(LE.chaveDaEscolha({ ...sh }), LE.chaveDaEscolha({ ...sh, tipo: 'chamado' }))
  }

  // ------------------------------------------------ a lista da loja: setup de verdade
  {
    const URL_CONV = '/api/atendimento/conversas'
    const linha = (id, o = {}) => ({
      id, plataforma: 'shopee', canal: 'email', conta: 'ATV', comprador_nome: `Cliente ${id}`, pedido_marketplace: null, situacao: 'aberta',
      ultima_mensagem_em: '2026-10-27T10:00:00Z', ultima_mensagem_resumo: 'Sua venda foi aprovada', aguardando_resposta: false, prazo_resposta_em: null,
      email: { tipo_email: 'venda', tipo_email_inferido: false, alias: '22max@tuta.com', caso_id: null, protocolo: null }, ...o,
    })
    const respostas = { [URL_CONV]: (url) => ({ itens: url.includes('filtro=fechadas') ? [linha('f1', { situacao: 'fechada' })] : [linha('c1'), linha('c2', { aguardando_resposta: true })], proximo: null }) }
    const escolha = { chave: 'i-sh', integration_id: 'i-sh', store_info_id: '', plataforma: 'shopee', conta: 'ATV', tipo: 'venda' }
    const m = montar('../components/AtendimentoLojaEmails.vue', { escolha, contagem: POR_LOJA[0], podeMexer: true, selecionada: 'c2', tique: 0, sinal: 0 }, { respostas })
    await m.montar()
    assert.deepEqual(m.chamadas.map((c) => c.url), ['/api/atendimento/conversas?caixa=email&filtro=todas&limite=50&tipo_email=venda&integration_id=i-sh'], 'venda: sem as linhas informativas')
    let html = await m.html()
    assert.deepEqual([...html.matchAll(/data-email-da-loja="([^"]+)"/g)].map((x) => x[1]), ['c1', 'c2'])
    assert.match(html, /aria-current="true" data-email-da-loja="c2"/)
    assert.match(html, /data-email-da-loja="c2"[\s\S]*?falta responder[\s\S]*?Cliente c2[\s\S]*?para 22max@tuta\.com/)
    assert.match(html, /data-tipo-da-loja="venda"[^>]*>[\s\S]*?Venda<span[^>]*>341<\/span>/)
    assert.match(html, /aria-pressed="true" data-tipo-da-loja="venda"/)
    assert.match(html, /data-voltar-conversas/)
    // As fechadas, no chip.
    m.estado.verFechadas(true)
    await esperar()
    assert.equal(m.chamadas.at(-1).url, '/api/atendimento/conversas?caixa=email&filtro=fechadas&limite=50&tipo_email=venda&integration_id=i-sh')
    html = await m.html()
    assert.match(html, /data-email-da-loja="f1"[\s\S]*?>fechada</)
    // A busca: nome, pedido ou protocolo.
    m.estado.buscaTexto = ' 2000123 '
    m.estado.buscar()
    await esperar()
    assert.match(m.chamadas.at(-1).url, /filtro=fechadas&q=2000123&limite=50&tipo_email=venda&integration_id=i-sh$/)
    // O tique (polling da página) relê a 1ª página; com "carregar mais", não.
    const antes = m.chamadas.length
    m.reativo.tique = 1
    await esperar()
    assert.equal(m.chamadas.length, antes + 1)
    // Outro tipo da mesma loja: do começo, nas abertas, sem busca.
    m.reativo.escolha = { ...escolha, tipo: 'informativo' }
    await esperar()
    assert.equal(m.estado.fechadas, false)
    assert.equal(m.estado.busca, '')
    assert.deepEqual(m.chamadas.slice(-2).map((c) => c.url).sort(), [
      '/api/atendimento/conversas?caixa=email&filtro=todas&limite=50&tipo_email=informativo&integration_id=i-sh',
      '/api/atendimento/email/emails?fila=informativo&integration_id=i-sh',
    ].sort(), 'informativo de quem mexe: as linhas das pastas informativas também')
    // O vazio explica e oferece a outra metade.
    const vazio = montar('../components/AtendimentoLojaEmails.vue', { escolha: { ...escolha, tipo: 'chamado' }, podeMexer: false }, { respostas: { [URL_CONV]: { itens: [], proximo: null } } })
    await vazio.montar()
    html = await vazio.html()
    assert.match(html, /data-emails-da-loja-vazio>\s*Nenhum e-mail chamado aberto desta loja\.[\s\S]*?ver as fechadas/)
  }
  {
    // Informativo de quem MEXE: as linhas da loja em cima; "ver o e-mail" = o cartão protegido.
    const linhaInf = { id: 'm1', estado: 'informativo', recebido_em: '2026-10-27T09:00:00Z', pasta: 'avisos ml', assunto: 'Novidades da semana', de: 'avisos@ml.com', destaque: false, alias: null, loja: 'ATV', integration_id: 'i-sh', sugestoes: [], pedidos_citados: [], alertas: [] }
    const respostas = {
      '/api/atendimento/conversas': { itens: [], proximo: null },
      '/api/atendimento/email/emails': { itens: [linhaInf], proximo: null },
      '/api/atendimento/email/emails/m1': { id: 'm1', texto: 'Texto protegido', de: 'avisos@ml.com', de_nome: 'ML', anexos: [], links_removidos: 0, codigo_mascarado: false },
    }
    const escolha = { chave: 'semid:temu:barbosa', integration_id: '', store_info_id: 'f-temu', plataforma: 'temu', conta: 'barbosa', tipo: 'informativo' }
    const m = montar('../components/AtendimentoLojaEmails.vue', { escolha, podeMexer: true }, { respostas })
    await m.montar()
    assert.deepEqual(m.chamadas.map((c) => c.url).sort(), [
      '/api/atendimento/conversas?caixa=email&filtro=todas&limite=50&tipo_email=informativo&store_info_id=f-temu',
      '/api/atendimento/email/emails?fila=informativo&store_info_id=f-temu',
    ])
    let html = await m.html()
    assert.match(html, /data-linhas-da-loja[\s\S]*?Pastas informativas[\s\S]*?data-linha-da-loja="m1"[\s\S]*?avisos ml[\s\S]*?Novidades da semana[\s\S]*?ver o e-mail[\s\S]*?Conversas de e-mail/)
    await m.estado.verLinha(linhaInf)
    html = await m.html()
    assert.match(html, /data-linha-da-loja-aberta[\s\S]*?Texto protegido/)
    assert.equal(m.chamadas.at(-1).url, '/api/atendimento/email/emails/m1')
    // Quem só lê: nem pergunta pelas linhas (a rota recusaria com 403).
    const leitor = montar('../components/AtendimentoLojaEmails.vue', { escolha, podeMexer: false }, { respostas })
    await leitor.montar()
    assert.deepEqual(leitor.chamadas.map((c) => c.url), ['/api/atendimento/conversas?caixa=email&filtro=todas&limite=50&tipo_email=informativo&store_info_id=f-temu'])
    assert.doesNotMatch(await leitor.html(), /data-linhas-da-loja/)
    // Sem loja nenhuma: não pergunta nada (nunca a lista de todas as lojas).
    const sem = montar('../components/AtendimentoLojaEmails.vue', { escolha: { ...escolha, store_info_id: '' }, podeMexer: true }, { respostas })
    await sem.montar()
    assert.deepEqual(sem.chamadas, [])
    assert.match(await sem.html(), /role="alert">Esta loja não tem como filtrar os e-mails\./)
  }

  // ------------------------------------------------ a página
  {
    const pagina = web('pages/atendimento.vue')
    const tpl = sfc('../pages/atendimento.vue').descriptor.template.content
    // A barra: os números por loja SÓ na Caixa (a Caixa Humano não tem a seta), as lojas abertas, o clique.
    assert.match(tpl, /<AtendimentoLojas\s+v-model:filtros="filtros"\s+v-model:recolhida="lojasRecolhidas"\s+v-model:emails-abertos="lojasEmailsAbertos"\s+:resumo="resumo"\s+:numero="emHumano \? 'humano' : 'nao_lidas_duoke'"\s+:emails="aba === 'caixa' \? emailContagens\?\.por_loja \?\? null : null"\s+:email-aberto="emailsDaLoja \? \{ chave: emailsDaLoja\.chave, tipo: emailsDaLoja\.tipo \} : null"\s+@abrir-emails="abrirEmailsDaLoja"\s*\/>/)
    // A mesma leitura do número da aba E-mail traz os números por loja (na Caixa).
    assert.match(pagina, /api<ContagensDeConversa>\(aba\.value === 'caixa' \? '\/api\/atendimento\/email\/contagens\?por_loja=true' : '\/api\/atendimento\/email\/contagens'\)/)
    // A lista do meio: os e-mails da loja; a do chat fica GUARDADA (v-show), com os filtros dela.
    assert.match(tpl, /<AtendimentoLojaEmails\s+v-if="emailsDaLoja"\s+:escolha="emailsDaLoja"\s+:contagem="contagemDaLoja"\s+:pode-mexer="mexe"\s+:selecionada="emailNaCaixa"\s+:tique="emailTique"\s+:sinal="emailNaCaixaSinal"\s+@selecionar="selecionarEmailNaCaixa"\s+@tipo="trocarTipoDaLoja"\s+@fechar="fecharEmailsDaLoja"\s*\/>\s*<AtendimentoLista\s+v-show="!emailsDaLoja"/)
    // A conversa: a do chat escondida e PARADA (sem polling) enquanto o e-mail está na Caixa; a de e-mail com a barra do e-mail.
    assert.match(tpl, /<AtendimentoConversa\s+v-if="selecionada"\s+v-show="!emailsDaLoja"[\s\S]*?:ativa="naCaixa && !emailsDaLoja"[\s\S]*?@mudou="aoMudarConversa"/)
    assert.match(tpl, /<div v-if="emailNaCaixa"[^>]*data-email-na-caixa>[\s\S]*?<AtendimentoEmailConversaBarra\s+:conversa="emailNaCaixaLinha"\s+:pode-mexer="mexe"\s+na-caixa\s+@voltar="fecharEmailNaCaixa"\s+@vinculado="aoVincularEmailNaCaixa"\s*\/>[\s\S]*?<AtendimentoConversa\s+:key="`\$\{emailNaCaixa\}:\$\{emailNaCaixaVersao\}`"\s+:conversa-id="emailNaCaixa"[\s\S]*?@mudou="aoMudarEmailNaCaixa"[\s\S]*?@abrir-conversa="selecionarEmailNaCaixa"/)
    // No celular: a lista some quando há conversa aberta (a do chat ou a de e-mail).
    assert.match(tpl, /:class="abertaNaCaixa \? 'hidden' : 'flex'"/)
    assert.match(tpl, /:class="abertaNaCaixa \? 'flex' : 'hidden'"/)

    // O bloco da página de verdade, com dublês (sem rede).
    const ini = pagina.indexOf('// ─── os E-MAILS de cada loja na Caixa (27/10/2026)')
    const fim = pagina.indexOf('</script>', ini)
    assert.ok(ini > 0 && fim > ini)
    function bloco({ usuario = 'u1', guardado = new Map(), quebrado = false } = {}) {
      const aba = Vue.ref('caixa')
      const filtros = Vue.ref(filtros0())
      const selecionada = Vue.ref(null)
      const emailContagens = Vue.ref({ aguardando: 0, por_loja: POR_LOJA })
      const montados = []
      const lidas = { contagens: 0, modelos: 0, selecionadas: [] }
      const localStorage = {
        getItem: (k) => { if (quebrado) throw new Error('bloqueado'); return guardado.has(k) ? guardado.get(k) : null },
        setItem: (k, v) => { if (quebrado) throw new Error('bloqueado'); guardado.set(k, v) },
      }
      const corpo = `${transpileTs(pagina.slice(ini, fim))}\nreturn { emailsDaLoja, emailNaCaixa, emailNaCaixaLinha, emailNaCaixaVersao, emailNaCaixaSinal, contagemDaLoja, abertaNaCaixa, abrirEmailsDaLoja, trocarTipoDaLoja, fecharEmailsDaLoja, fecharEmailNaCaixa, selecionarEmailNaCaixa, aoMudarEmailNaCaixa, aoVincularEmailNaCaixa, lojasEmailsAbertos, chaveDosEmailsAbertos }`
      const out = new Function('ref', 'computed', 'watch', 'nextTick', 'onMounted', 'aba', 'filtros', 'selecionada', 'emailContagens', 'iniciou', 'auth', 'localStorage', 'carregarContagensEmail', 'carregarModelos', 'selecionar', corpo)(
        Vue.ref, Vue.computed, Vue.watch, Vue.nextTick, (fn) => montados.push(fn), aba, filtros, selecionada, emailContagens, true, { user: usuario ? { id: usuario } : null }, localStorage,
        () => { lidas.contagens++ }, () => { lidas.modelos++ }, (id) => { lidas.selecionadas.push(id); selecionada.value = id },
      )
      return { ...out, aba, filtros, selecionada, montados, lidas, guardado, async montar() { for (const fn of montados) fn(); await Vue.nextTick(); await esperar() } }
    }
    function filtros0() { return filtros() }
    const ATV = { chave: 'i-sh', integration_id: 'i-sh', store_info_id: '', plataforma: 'shopee', conta: 'ATV', tipo: 'venda' }
    {
      const b = bloco()
      b.selecionada.value = 'chat-1'
      b.abrirEmailsDaLoja(ATV)
      assert.equal(b.contagemDaLoja.value.integration_id, 'i-sh', 'os números da loja escolhida')
      assert.equal(b.abertaNaCaixa.value, null, 'o chat guardado não abre por cima da lista de e-mails')
      b.selecionarEmailNaCaixa('e-1')
      assert.equal(b.abertaNaCaixa.value, 'e-1')
      await esperar()
      assert.equal(b.lidas.modelos, 1, 'as respostas prontas para a conversa de e-mail')
      // A linha da conversa: mudou o que a lista e os números mostram → relê.
      b.aoMudarEmailNaCaixa({ id: 'e-1', email: { tipo_email: 'venda' }, aguardando_resposta: true, situacao: 'aberta' })
      assert.equal(b.lidas.contagens, 0, 'a primeira linha só guarda')
      b.aoMudarEmailNaCaixa({ id: 'e-1', email: { tipo_email: 'venda' }, aguardando_resposta: false, situacao: 'respondida' })
      assert.deepEqual([b.lidas.contagens, b.emailNaCaixaSinal.value], [1, 1])
      b.aoMudarEmailNaCaixa({ id: 'outra', email: {}, aguardando_resposta: true, situacao: 'aberta' })
      assert.equal(b.lidas.contagens, 1, 'a linha de outra conversa não conta')
      // Vinculou: as mensagens foram para a conversa de e-mail do pedido.
      b.aoVincularEmailNaCaixa('e-pedido')
      assert.equal(b.emailNaCaixa.value, 'e-pedido')
      b.aoVincularEmailNaCaixa('e-pedido')
      assert.equal(b.emailNaCaixaVersao.value, 1, 'na mesma: relê do zero')
      // Outro tipo da loja: a conversa aberta fecha, a loja fica.
      b.trocarTipoDaLoja('chamado')
      assert.deepEqual([b.emailsDaLoja.value.tipo, b.emailNaCaixa.value], ['chamado', null])
      // Um link de dentro do e-mail que aponta um CHAT: vai para a lista do chat, aberto.
      b.selecionarEmailNaCaixa('e-2')
      b.aoMudarEmailNaCaixa({ id: 'e-2', email: null })
      assert.deepEqual([b.emailsDaLoja.value, b.emailNaCaixa.value, b.selecionada.value], [null, null, 'e-2'])
      // Um filtro do chat (a loja na barra, o menu Plataforma): a lista do chat de volta.
      b.abrirEmailsDaLoja(ATV)
      b.filtros.value = { ...b.filtros.value, integration_id: 'i-sh' }
      await Vue.nextTick()
      assert.equal(b.emailsDaLoja.value, null)
      // Saiu da Caixa (a Caixa Humano, a aba E-mail): fecha; voltou: relê os números por loja.
      b.abrirEmailsDaLoja(ATV)
      b.aba.value = 'humano'
      await Vue.nextTick()
      assert.equal(b.emailsDaLoja.value, null)
      const lidas = b.lidas.contagens
      b.aba.value = 'caixa'
      await Vue.nextTick()
      assert.equal(b.lidas.contagens, lidas + 1)
    }
    {
      // As lojas abertas: POR PESSOA (a chave leva o id), lidas no onMounted, gravadas no clique.
      const guardado = new Map([['davinci.atendimento.lojasEmailsAbertos:u1', JSON.stringify(['i-sh', 'i-sh', 7, '', 'semid:temu:barbosa'])], ['davinci.atendimento.lojasEmailsAbertos:u2', JSON.stringify(['i-ml'])]])
      const b = bloco({ guardado })
      assert.equal(b.chaveDosEmailsAbertos(), 'davinci.atendimento.lojasEmailsAbertos:u1')
      await b.montar()
      assert.deepEqual(b.lojasEmailsAbertos.value, ['i-sh', 'semid:temu:barbosa'], 'só textos, sem repetir; a da outra pessoa não vem')
      b.lojasEmailsAbertos.value = ['i-sh']
      await Vue.nextTick()
      assert.equal(guardado.get('davinci.atendimento.lojasEmailsAbertos:u1'), '["i-sh"]')
      assert.equal(guardado.get('davinci.atendimento.lojasEmailsAbertos:u2'), '["i-ml"]', 'a da outra pessoa não muda')
      // Valor estragado e armazenamento bloqueado (aba anônima): começa fechado, sem quebrar.
      const estragado = bloco({ guardado: new Map([['davinci.atendimento.lojasEmailsAbertos:u1', '{oops']]) })
      await estragado.montar()
      assert.deepEqual(estragado.lojasEmailsAbertos.value, [])
      const bloqueado = bloco({ quebrado: true })
      await bloqueado.montar()
      assert.deepEqual(bloqueado.lojasEmailsAbertos.value, [])
      bloqueado.lojasEmailsAbertos.value = ['i-sh']
      await Vue.nextTick()
      assert.deepEqual(bloqueado.lojasEmailsAbertos.value, ['i-sh'], 'só não lembra')
    }
  }

  // ------------------------------------------------ a barra da conversa de e-mail na Caixa
  {
    const naCaixa = montar('../components/AtendimentoEmailConversaBarra.vue', { conversa: null, podeMexer: false, naCaixa: true })
    assert.match(await naCaixa.html(), /E-mail da loja, aberto pela barra de lojas: fica fora da lista e dos números do chat\./)
    const naAba = montar('../components/AtendimentoEmailConversaBarra.vue', { conversa: null, podeMexer: false })
    assert.match(await naAba.html(), /Conversa de e-mail: fica só aqui, na aba E-mail \(não aparece na Caixa do chat\)\./, 'na aba E-mail, o aviso de sempre')
  }

  // ------------------------------------------------ o contrato com a API
  {
    const api = (rel) => fs.readFileSync(path.resolve(__dirname, '../../api/app', rel), 'utf8')
    const email = api('routers/atendimento_email.py')
    assert.match(email, /por_loja: Annotated\[bool, Query\(\)\] = False/)
    assert.match(email, /async def _contagens_por_loja\(/)
    assert.match(email, /integration_id: Annotated\[UUID \| None, Query\(\)\] = None,\n    store_info_id: Annotated\[UUID \| None, Query\(\)\] = None,\n\) -> dict\[str, Any\]:\n    """Uma fila:/)
    assert.match(api('routers/atendimento.py'), /store_info_id: Annotated\[UUID \| None, Query\(\)\] = None,\n\) -> ListaConversasOut:/)
    // O e-mail continua fora da Caixa: a barra e a lista do chat não perguntam pelo e-mail.
    assert.doesNotMatch(web('components/AtendimentoLista.vue'), /caixa=email|por_loja/)
  }
  console.log('ok — atendimento-email-lojas')
}

principal().catch((e) => { console.error(e); process.exit(1) })
