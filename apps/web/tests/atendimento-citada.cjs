// node tests/atendimento-citada.cjs — a mensagem CITADA em cima do balão (17/11/2026).
// O dono: "a equipe mandou as malas e a pessoa respondeu na mala que queria, mas no nosso
// comunicador não mostra em qual mensagem ele respondeu; no Duoke isso aparecia". O servidor
// manda `mensagem.citada` (apps/api: services/atendimento/citacao.py — Shopee `quoted_msg`, Zap
// `context`); aqui:
//  - a parte pura do AtendimentoCitada: o campo conferido (`citadaDe`: o torto vira null, a
//    miniatura pela régua das fotos da tela), de quem é ("Loja"/"Cliente"), a linha do bloco e
//    as cores;
//  - o bloco renderizado: botão com aria-label "Ir para a mensagem citada" só quando a citada
//    está gravada aqui (`mensagem_id`), a miniatura, o texto, o `ir` com o id; sem `mensagem_id`,
//    um div sem clique; sem citada, nada;
//  - o balão de verdade (o trecho do template do AtendimentoConversa): o bloco em cima do texto
//    só na mensagem que cita — a que não cita sai igual;
//  - o setup de verdade (`irParaCitada`): rola até a citada; noutra aba, a aba dela antes; fora
//    da tela, o aviso.
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
  const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
  assert.deepEqual(errors, [], rel)
  const script = compileScript(descriptor, { id: path.basename(rel), inlineTemplate: false })
  for (const bindingMetadata of [script.bindings, undefined]) {
    const tpl = compileTemplate({ source: descriptor.template.content, filename, id: path.basename(rel), compilerOptions: { isTS: true, bindingMetadata } })
    assert.deepEqual(tpl.errors, [], `${rel}: template compila`)
  }
  return { descriptor, filename, script }
}
const icones = new Proxy({}, { get: (_, nome) => ({ name: String(nome), render: () => Vue.h('i', { 'data-icone': String(nome) }) }) })
const cache = new Map()
function requerer(nome) {
  if (nome === 'lucide-vue-next') return icones
  const m = /^~\/components\/(.+\.vue)$/.exec(nome)
  if (!m) return require(nome)
  if (!cache.has(m[1])) {
    const { descriptor } = parse(fs.readFileSync(path.resolve(__dirname, '../components', m[1]), 'utf8'))
    const mod = { exports: {} }
    if (descriptor.script) new Function('require', 'module', 'exports', transpile(descriptor.script.content))(requerer, mod, mod.exports)
    cache.set(m[1], mod.exports)
  }
  return cache.get(m[1])
}
function trecho(src, ini, fim) {
  const a = src.indexOf(ini)
  const b = src.indexOf(fim, a + 1)
  assert.ok(a >= 0 && b > a, `trecho ${ini} … ${fim}`)
  return transpile(src.slice(a, b))
}
const semComentarios = (html) => html.replace(/<!--[\s\S]*?-->/g, '')
const tique = () => new Promise((r) => setImmediate(r))

const C = requerer('~/components/AtendimentoCitada.vue')
const FOTO = 'https://img.sp.mms.shopee.sg/br-11134201-mala-azul'
const COPIA_ZAP = '/api/atendimento/zap/midia/0b6f2c1e-3c33-4d4a-9a55-0c5d3b6a8f00'
const citada = (extra = {}) => ({ externo_id: '700001', mensagem_id: 'm-foto', autor: 'loja', tipo: 'imagem', texto: null, imagem_url: FOTO, ...extra })

// ───────────────────────── a parte pura
{
  assert.deepEqual(C.citadaDe(citada()), citada())
  // O servidor antigo não manda; o torto vira null.
  for (const ruim of [undefined, null, 'x', 7, {}, { externo_id: '' }, { externo_id: '   ' }, { externo_id: 9 }]) {
    assert.equal(C.citadaDe(ruim), null, JSON.stringify(ruim))
  }
  // Tipo e autor fora da lista: 'outro' e null; texto com espaço sobrando, aparado.
  assert.deepEqual(C.citadaDe({ externo_id: ' 1 ', tipo: 'sticker', autor: 'mediador', texto: '  oi  ', mensagem_id: '' }), {
    externo_id: '1', mensagem_id: null, autor: null, tipo: 'outro', texto: 'oi', imagem_url: null,
  })
  // A miniatura pela régua das fotos da tela (`urlSegura`): http(s) ou caminho da própria API.
  assert.equal(C.citadaDe(citada({ imagem_url: COPIA_ZAP })).imagem_url, COPIA_ZAP)
  for (const ruim of ['javascript:alert(1)', '//outro.site/x.png', 'data:image/png;base64,AA', 'br-11134231-solto', 12]) {
    assert.equal(C.citadaDe(citada({ imagem_url: ruim })).imagem_url, null, String(ruim))
  }

  assert.equal(C.autorDaCitada({ autor: 'loja' }), 'Loja')
  assert.equal(C.autorDaCitada({ autor: 'cliente' }), 'Cliente')
  assert.equal(C.autorDaCitada({ autor: null }), 'Mensagem citada')

  assert.equal(C.resumoDaCitada(citada({ texto: 'Qual das duas?' })), 'Qual das duas?')
  assert.equal(C.resumoDaCitada(citada()), 'Foto')
  assert.equal(C.resumoDaCitada(citada({ tipo: 'video', imagem_url: null })), 'Vídeo')
  assert.equal(C.resumoDaCitada(citada({ tipo: 'produto' })), 'Produto')
  assert.equal(C.resumoDaCitada(citada({ tipo: 'pedido', imagem_url: null })), 'Pedido')
  // A do celular, no Zap: só o id — o bloco diz que ela não está aqui.
  assert.equal(C.resumoDaCitada(citada({ tipo: 'outro', mensagem_id: null, imagem_url: null, autor: null })), C.NAO_ESTA_AQUI)
  assert.equal(C.resumoDaCitada(citada({ tipo: 'outro', imagem_url: null })), 'Mensagem')

  // A cor de quem escreveu, nos dois temas.
  assert.match(C.coresDaCitada({ autor: 'loja' }).barra, /border-sky-500 dark:border-sky-400/)
  assert.match(C.coresDaCitada({ autor: 'cliente' }).nome, /text-emerald-700 dark:text-emerald-300/)
  assert.match(C.coresDaCitada({ autor: null }).barra, /border-muted-foreground/)
  assert.equal(C.ROTULO_IR, 'Ir para a mensagem citada')
}

// ───────────────────────── o bloco renderizado
const citadaSfc = sfc('../components/AtendimentoCitada.vue')
function componenteCitada() {
  const { descriptor, filename, script } = citadaSfc
  const tpl = compileTemplate({ source: descriptor.template.content, filename, id: 'citada', compilerOptions: { isTS: true, bindingMetadata: script.bindings } })
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', 'computed', 'ref', 'watch', transpile(script.content))(requerer, mod, mod.exports, Vue.computed, Vue.ref, Vue.watch)
  const tmod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(tpl.code))(requerer, tmod, tmod.exports)
  return { ...mod.exports.default, render: tmod.exports.render }
}
const Citada = componenteCitada()
async function bloco(c, de = 'm-resposta') {
  const emitidos = []
  const props = Vue.reactive({ citada: c, de })
  const bruto = Citada.setup(props, { emit: (n, ...a) => emitidos.push([n, ...a]), expose: () => {}, attrs: {}, slots: {} })
  const comp = { ...Citada, setup: () => bruto }
  const app = Vue.createSSRApp({ render: () => Vue.h(comp, props) })
  app.config.warnHandler = () => {}
  return { html: semComentarios(await renderToString(app)), emitidos, bruto }
}
;(async () => {
  {
    // A foto da mala que a equipe mandou, gravada aqui: botão, miniatura, "Loja", "Foto".
    const { html, emitidos, bruto } = await bloco(citada())
    assert.match(html, /^<button[^>]*type="button"/)
    // O nome acessível é o próprio conteúdo citado, com o prefixo só para o leitor de tela.
    assert.doesNotMatch(html, /<button[^>]*aria-label=/)
    assert.match(html, /<span class="sr-only">Ir para a mensagem citada: <\/span>/)
    assert.match(html, /data-citada-ir/)
    assert.match(html, /data-citada-tipo="imagem"/)
    assert.match(html, /border-l-\[3px\]/)
    assert.match(html, /bg-foreground\/5/, 'o fundo sutil (o token da tela, claro e escuro)')
    assert.match(html, /border-sky-500 dark:border-sky-400/)
    // A miniatura decorativa (alt vazio: o texto ao lado diz o que é).
    assert.match(html, new RegExp(`<img src="${FOTO.replace(/[.]/g, '\\.')}" alt(="")? loading="lazy" referrerpolicy="no-referrer" class="size-11 [^"]*object-cover"`))
    assert.match(html, /<span id="citada-m-resposta"[^>]*>[\s\S]*Loja[\s\S]*Foto/)
    assert.match(html, /line-clamp-2/)
    // O clique manda o id da citada (quem rola é o AtendimentoConversa).
    bruto.ir()
    assert.deepEqual(emitidos, [['ir', 'm-foto']])
  }
  {
    // A pergunta da loja em texto: sem miniatura, o texto em até 2 linhas.
    const { html } = await bloco(citada({ tipo: 'texto', imagem_url: null, texto: 'Qual das duas você prefere?' }))
    assert.doesNotMatch(html, /<img/)
    assert.match(html, /data-citada-resumo>Qual das duas você prefere\?<\/span>/)
  }
  {
    // A própria fala do comprador: "Cliente", em verde.
    const { html } = await bloco(citada({ autor: 'cliente', tipo: 'texto', imagem_url: null, texto: 'quero essa' }))
    assert.match(html, /text-emerald-700 dark:text-emerald-300[^>]*>Cliente</)
  }
  {
    // A citada que não está gravada aqui: div, sem clique e sem aria de botão — mas mostra.
    const { html, emitidos, bruto } = await bloco(citada({ mensagem_id: null, tipo: 'texto', imagem_url: null, texto: 'Temos também a vermelha' }))
    assert.match(html, /^<div/)
    assert.doesNotMatch(html, /aria-label|aria-describedby|data-citada-ir|type="button"/)
    assert.match(html, /title="a mensagem citada não está nesta conversa do DaVinci"/)
    assert.match(html, /Temos também a vermelha/)
    bruto.ir()
    assert.deepEqual(emitidos, [], 'sem a nossa linha, o clique não faz nada')
  }
  {
    // URL torta: sem <img>; o texto continua.
    const { html } = await bloco(citada({ imagem_url: 'javascript:alert(1)', texto: 'Mala ABS' }))
    assert.doesNotMatch(html, /<img|javascript:/)
    assert.match(html, /Mala ABS/)
  }
  {
    // O texto do comprador é texto, nunca HTML.
    const { html } = await bloco(citada({ tipo: 'texto', imagem_url: null, texto: '<b>oi</b>' }))
    assert.match(html, /&lt;b&gt;oi&lt;\/b&gt;/)
  }
  {
    // Campo torto (o servidor mandou lixo): nada.
    const { html } = await bloco({ externo_id: '' })
    assert.equal(html.trim(), '')
  }

  // ───────────────────────── o template do AtendimentoConversa
  const conversaSfc = sfc('../components/AtendimentoConversa.vue')
  const tpl = conversaSfc.descriptor.template.content
  const setup = conversaSfc.descriptor.scriptSetup.content
  const ini = tpl.indexOf('<div class="max-w-[85%] rounded-lg px-3 py-2 transition-shadow md:max-w-[75%]" :class="[balaoCls(l.m)')
  const fim = tpl.indexOf('<!-- A frase diz o que fazer; o código cru (para o suporte) fica no title. -->', ini)
  assert.ok(ini > 0 && fim > ini, 'o balão')
  const balao = tpl.slice(ini, fim)
  {
    // Dentro do balão, depois do selo e do cartão do e-mail, antes do texto — uma vez só.
    assert.equal((tpl.match(/<AtendimentoCitada\b/g) || []).length, 1)
    const iCitada = balao.indexOf('<AtendimentoCitada v-if="l.m.citada" :citada="l.m.citada" :de="l.m.id" @ir="irParaCitada" />')
    assert.ok(iCitada > 0, 'o bloco, com o clique ligado ao irParaCitada')
    assert.ok(balao.indexOf('seloDaMsg(l.m)') < iCitada, 'o selo antes')
    assert.ok(balao.indexOf('<AtendimentoEmailCartao') < iCitada, 'o cartão do e-mail antes')
    assert.ok(iCitada < balao.indexOf('<div v-if="l.m.texto"'), 'em cima do texto')
    assert.ok(iCitada < balao.indexOf('pecasDaMsg(l.m).length'), 'em cima das fotos/cartões')
    // A linha da mensagem tem o `data-msg-id` que o `mostrarMensagem` procura.
    assert.match(tpl, /<div v-else class="flex flex-col" :class="[^"]+" :data-msg-id="l\.m\.id">/)
  }

  // O balão renderizado de verdade (com o AtendimentoCitada de verdade).
  async function renderizarBalao(m) {
    const { code, errors } = compileTemplate({ source: `${balao}</div>`, filename: 'balao.vue', id: 'balao', compilerOptions: { isTS: true } })
    assert.deepEqual(errors, [], 'o trecho compila')
    const tmod = { exports: {} }
    new Function('require', 'module', 'exports', transpile(code))(requerer, tmod, tmod.exports)
    const ctx = {
      l: { tipo: 'msg', chave: m.id, m, de: null },
      balaoCls: () => 'rounded-tl-sm border border-border/70 bg-background', destacadaId: null,
      seloDaMsg: () => null, ehCartaoDeEmail: () => false, cartaoDe: () => null, podeResponderEmail: () => false,
      emailResponder: null, responderEmail: () => {}, lado: () => 'cliente', irParaCitada: () => {},
      partesDaMensagem: (x) => [{ t: 'txt', v: x.texto || '' }], pecasDaMsg: () => [], TIPO_LABEL: {},
      fotoFalhou: {}, imagemAberta: null, conversa: { plataforma: 'shopee' }, pedidoMkt: null,
    }
    const a = Vue.createSSRApp({ setup: () => ctx, render: tmod.exports.render })
    a.config.warnHandler = () => {}
    a.component('AtendimentoCitada', Citada)
    for (const nome of new Set([...balao.matchAll(/<([A-Z][A-Za-z0-9]*)/g)].map((x) => x[1]))) {
      if (nome !== 'AtendimentoCitada') a.component(nome, icones[nome])
    }
    return semComentarios(await renderToString(a))
  }
  const fala = { id: 'm-resposta', autor: 'cliente', origem: 'cliente', autor_nome: 'Comprador', tipo: 'texto', texto: 'quero essa', anexos: [], enviada_em: '2026-11-17T10:00:00Z', status: 'recebida', erro: null }
  {
    const h = await renderizarBalao({ ...fala, citada: citada() })
    const iBloco = h.indexOf('data-citada')
    const iTexto = h.indexOf('quero essa')
    assert.ok(iBloco > 0 && iBloco < iTexto, 'o bloco em cima da fala')
    assert.match(h, /<span class="sr-only">Ir para a mensagem citada: <\/span>/)
    assert.match(h, /<img src="https:\/\/img\.sp\.mms\.shopee\.sg\/br-11134201-mala-azul"/)
  }
  {
    // Sem citada (o servidor antigo, ou a mensagem que não cita): o balão de sempre.
    for (const semCitada of [{ ...fala }, { ...fala, citada: null }]) {
      const h = await renderizarBalao(semCitada)
      assert.doesNotMatch(h, /data-citada|Ir para a mensagem citada/)
      assert.match(h, /quero essa/)
    }
    const com = await renderizarBalao({ ...fala, citada: citada() })
    const sem = await renderizarBalao({ ...fala, citada: null })
    // O resto do balão é o mesmo: só o bloco a mais.
    assert.equal(com.replace(/<button[\s\S]*?<\/button>/, ''), sem)
  }

  // ───────────────────────── o setup de verdade: irParaCitada
  function montar({ naTela = [], depoisDaAba = [], mensagens = [], foraDaAba = {}, abaDaConversa = 'pos_venda', abaAtiva = 'pos_venda' } = {}) {
    const js = trecho(setup, '// A mensagem CITADA (17/11/2026, o dono: "no Duoke', '// Modo observação: o Duoke responde')
      + '\nreturn { irParaCitada }'
    const procurados = []
    const avisos = []
    const g = {
      abaAtiva: Vue.ref(abaAtiva),
      abaDaConversa: Vue.ref(abaDaConversa),
      abasDados: Vue.ref({ fora_da_aba: foraDaAba }),
      detalhe: Vue.ref({ mensagens: mensagens.map((id) => ({ id })) }),
      nextTick: (fn) => Promise.resolve().then(fn),
      toasts: { info: (t, d) => avisos.push([t, d]) },
    }
    // O DOM de mentira: o que está na tela; trocada a aba, o que ela mostra.
    g.mostrarMensagem = (id) => {
      procurados.push([id, g.abaAtiva.value])
      return (g.abaAtiva.value === abaAtiva ? naTela : depoisDaAba).includes(id)
    }
    const nomes = Object.keys(g)
    const x = new Function(...nomes, js)(...nomes.map((n) => g[n]))
    return { ...x, g, procurados, avisos }
  }
  {
    // Na tela: só rola e destaca (o `mostrarMensagem`), sem trocar de aba nem avisar.
    const m = montar({ naTela: ['m-foto'], mensagens: ['m-foto'] })
    m.irParaCitada('m-foto')
    await tique()
    assert.deepEqual(m.procurados, [['m-foto', 'pos_venda']])
    assert.equal(m.g.abaAtiva.value, 'pos_venda')
    assert.deepEqual(m.avisos, [])
  }
  {
    // Desta conversa, noutra aba (o pré-venda do chat): a aba dela, depois rola.
    const m = montar({ depoisDaAba: ['m-foto'], mensagens: ['m-foto'], foraDaAba: { 'm-foto': 'pre_venda' } })
    m.irParaCitada('m-foto')
    assert.equal(m.g.abaAtiva.value, 'pre_venda')
    await tique()
    assert.deepEqual(m.procurados, [['m-foto', 'pos_venda'], ['m-foto', 'pre_venda']])
    assert.deepEqual(m.avisos, [])
  }
  {
    // Vendo outra aba e a citada mora na da conversa: volta para ela.
    const m = montar({ depoisDaAba: ['m-foto'], mensagens: ['m-foto'], abaAtiva: 'reclamacao' })
    m.irParaCitada('m-foto')
    assert.equal(m.g.abaAtiva.value, 'pos_venda')
    await tique()
    assert.deepEqual(m.avisos, [])
  }
  {
    // Fora da tela (mais antiga que a página): o aviso, sem trocar de aba.
    const m = montar({ mensagens: ['m-foto'] })
    m.irParaCitada('m-foto')
    await tique()
    assert.equal(m.g.abaAtiva.value, 'pos_venda')
    assert.equal(m.avisos.length, 1)
    assert.match(m.avisos[0][0], /não está na tela/)
    // De outra conversa (na aba) e não achada: o aviso também.
    const outra = montar({ mensagens: [] })
    outra.irParaCitada('m-de-outra')
    await tique()
    assert.equal(outra.avisos.length, 1)
    // Trocou de aba e mesmo assim não achou: o aviso.
    const sumiu = montar({ mensagens: ['m-foto'], foraDaAba: { 'm-foto': 'pre_venda' } })
    sumiu.irParaCitada('m-foto')
    await tique()
    assert.equal(sumiu.avisos.length, 1)
  }
  {
    // Sem id: nada.
    const m = montar()
    m.irParaCitada('')
    await tique()
    assert.deepEqual([m.procurados, m.avisos], [[], []])
  }

  console.log('ok — atendimento-citada')
})().catch((e) => {
  console.error(e)
  process.exit(1)
})
