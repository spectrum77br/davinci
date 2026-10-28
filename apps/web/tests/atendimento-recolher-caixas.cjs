// node tests/atendimento-recolher-caixas.cjs — MINIMIZAR as caixas da conversa
// (28/10/2026). O dono: "tem que deixar diminuir essas caixas pois não dá pra
// ler o conteúdo direito, principalmente quando tem reclamação". Do mesmo jeito
// do chamado e da resposta do e-mail (27/10/2026, atendimento-mail-atendimento):
//  - a faixa da Caixa Humano (AtendimentoConversa): recolhida, o nome e os
//    primeiros motivos numa linha ("Reclamação aberta · Pediu atendente +1");
//  - o cartão da disputa/reclamação (AtendimentoReclamacao): recolhido, tipo ·
//    nº · status (o prazo continua à direita);
//  - a sugestão da IA (AtendimentoConversa): recolhida, o título (assunto ·
//    confiança · "precisa de pessoa") e o selo "N avisos".
// As três começam ABERTAS, recolhem pela linha do título (chevron,
// aria-expanded) e o navegador lembra a escolha (uma chave por caixa; sem
// localStorage, só não lembra). Recolher só esconde (v-show): o "Abrir na
// plataforma", o copiar o nº, o "usar sugestão", o copiar e o "Descartar
// sugestão" (com o motivo) seguem lá; a caixa de resposta não faz parte.
// Aqui: as funções puras, o setup de verdade (Vue SSR, API falsa) do cartão da
// reclamação e os trechos do template/setup da conversa rodando com dublês.
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
const semComentarios = (html) => html.replace(/<!--[\s\S]*?-->/g, '')
const esperar = () => new Promise((r) => setTimeout(r, 0))
const escondido = (html, attr) => new RegExp(`style="display:none;"[^>]*${attr}|${attr}[^>]*style="display:none;"`).test(html)
const fechado = (html, attr, v) => new RegExp(`${attr}[^>]*aria-expanded="${v}"|aria-expanded="${v}"[^>]*${attr}`).test(html)

// O localStorage do navegador (dublê): `quebrado` = sem localStorage (aba
// anônima, bloqueado), cada acesso lança.
function armazenamento(inicial = {}, quebrado = false) {
  const dados = new Map(Object.entries(inicial))
  const falha = () => { throw new Error('localStorage indisponível') }
  return {
    dados,
    getItem: quebrado ? falha : (k) => (dados.has(k) ? dados.get(k) : null),
    setItem: quebrado ? falha : (k, v) => { dados.set(k, String(v)) },
  }
}

// ------------------------------------------------ montar um componente de verdade
// O mesmo jeito do atendimento-mail-atendimento: o setup de verdade roda fora
// de um app (os `watch` ficam vivos) e o HTML sai de um app SSR com o MESMO
// estado.
function montar(rel, props, { respostas = {}, localStorage = armazenamento(), filhos = {} } = {}) {
  const { descriptor, filename } = sfc(rel)
  const script = compileScript(descriptor, { id: path.basename(rel), inlineTemplate: false })
  const tpl = compileTemplate({ source: descriptor.template.content, filename, id: path.basename(rel), compilerOptions: { isTS: true, bindingMetadata: script.bindings } })
  const chamadas = []
  const avisos = []
  const montados = []
  const responder = async (url, opts = {}) => {
    chamadas.push({ url, metodo: opts.method || 'GET' })
    if (url in respostas) return structuredClone(respostas[url])
    throw new Error(`rota inesperada: ${url}`)
  }
  const g = {
    ref: Vue.ref, computed: Vue.computed, reactive: Vue.reactive, watch: Vue.watch, nextTick: Vue.nextTick,
    onMounted: (fn) => montados.push(fn), onBeforeUnmount: () => {},
    useApi: () => ({ url: (v) => v, api: responder }),
    useToasts: () => ({ success: (t) => avisos.push(['ok', t]), error: (t) => avisos.push(['erro', t]) }),
    localStorage,
    window: { prompt: () => {}, addEventListener: () => {}, removeEventListener: () => {} },
  }
  const nomes = Object.keys(g)
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', ...nomes, transpile(script.content))(requerer, mod, mod.exports, ...nomes.map((n) => g[n]))
  const tmod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(tpl.code))(requerer, tmod, tmod.exports)
  const comp = mod.exports.default
  const reativo = Vue.reactive({ ...props })
  // O useRelogio (onMounted do Vue fora de um componente) só avisa aqui.
  const aviso = console.warn
  console.warn = () => {}
  let bruto
  try {
    bruto = comp.setup(reativo, { emit: () => {}, expose: () => {}, attrs: {}, slots: {} })
  } finally {
    console.warn = aviso
  }
  const estado = Vue.proxyRefs(bruto)
  const filho = { ...comp, render: tmod.exports.render, setup: () => bruto }
  async function html() {
    const a = Vue.createSSRApp({ render: () => Vue.h(filho, reativo) })
    a.config.warnHandler = () => {}
    for (const [n, c] of Object.entries(filhos)) a.component(n, c)
    return semComentarios(await renderToString(a))
  }
  return {
    html, estado, chamadas, avisos, localStorage,
    async montar() { for (const fn of montados) await fn(); await esperar() },
  }
}

// Um pedaço do template da conversa, com o contexto de dublês (os ícones e o
// Button viram marcas simples).
async function renderizarTrecho(fonte, ctx) {
  const { code, errors } = compileTemplate({ source: `<div>${fonte}</div>`, filename: 'trecho.vue', id: 'trecho', compilerOptions: { isTS: true } })
  assert.deepEqual(errors, [], 'o trecho compila')
  const tmod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(code))(requerer, tmod, tmod.exports)
  const a = Vue.createSSRApp({ setup: () => ctx, render: tmod.exports.render })
  a.config.warnHandler = () => {}
  for (const nome of new Set([...fonte.matchAll(/<([A-Z][A-Za-z0-9]*)/g)].map((m) => m[1]))) {
    if (nome === 'Button') a.component('Button', { props: ['disabled'], setup: (p, { slots }) => () => Vue.h('button', { disabled: p.disabled }, slots.default?.()) })
    else a.component(nome, icones[nome])
  }
  return semComentarios(await renderToString(a))
}

const P = requerer('~/components/AtendimentoPlataforma.vue')
const R = requerer('~/components/AtendimentoEmailResposta.vue')
const conversaSfc = sfc('../components/AtendimentoConversa.vue')
const C = exportsDe(conversaSfc.descriptor)
const RC = exportsDe(sfc('../components/AtendimentoReclamacao.vue').descriptor)
const tpl = conversaSfc.descriptor.template.content
const setup = conversaSfc.descriptor.scriptSetup.content

// O setup de verdade das duas caixas da conversa (o trecho), com o localStorage dado.
function caixasDaConversa(localStorage) {
  const montados = []
  const js = trecho(setup, 'const caixaHumanoAberta = ref(true)', '// ─── caixa de resposta')
    + '\nreturn { caixaHumanoAberta, sugestaoIaAberta, alternarCaixaHumano, alternarSugestaoIa }'
  const x = new Function('ref', 'onMounted', 'localStorage', 'CAIXA_HUMANO_ABERTA_KEY', 'SUGESTAO_IA_ABERTA_KEY', js)(
    Vue.ref, (fn) => montados.push(fn), localStorage, C.CAIXA_HUMANO_ABERTA_KEY, C.SUGESTAO_IA_ABERTA_KEY,
  )
  return { ...x, montar: () => montados.forEach((fn) => fn()) }
}

async function principal() {
  // ------------------------------------------------ as chaves e as funções puras
  {
    assert.equal(C.CAIXA_HUMANO_ABERTA_KEY, 'davinci.atendimento.caixaHumanoAberta')
    assert.equal(C.SUGESTAO_IA_ABERTA_KEY, 'davinci.atendimento.sugestaoIaAberta')
    assert.equal(RC.RECLAMACAO_ABERTA_KEY, 'davinci.atendimento.reclamacaoAberta')
    // Uma chave por caixa (e nenhuma igual às do e-mail, da observação ou do pedido).
    const chaves = [C.CAIXA_HUMANO_ABERTA_KEY, C.SUGESTAO_IA_ABERTA_KEY, RC.RECLAMACAO_ABERTA_KEY,
      requerer('~/components/AtendimentoEmailChamado.vue').CHAMADO_ABERTO_KEY, R.RESPOSTA_ABERTA_KEY,
      'davinci.atendimento.sugestaoRecolhida', 'davinci.atendimento.pedidoRecolhido']
    assert.equal(new Set(chaves).size, chaves.length)

    // A faixa recolhida: os dois primeiros motivos e "+N".
    const h = { motivos: ['reclamacao', 'atendente', 'assunto'], rotulos: ['Reclamação aberta', 'Pediu atendente', 'Assunto só de pessoa'], assuntos: ['garantia'], assuntos_rotulos: ['Garantia'] }
    assert.equal(P.resumoHumano(h), 'Reclamação aberta · Pediu atendente +1')
    assert.equal(P.resumoHumano(h, 1), 'Reclamação aberta +2')
    assert.equal(P.resumoHumano(h, 3), 'Reclamação aberta · Pediu atendente · Assunto só de pessoa: Garantia')
    assert.equal(P.resumoHumano({ motivos: ['ia_sem_resposta'], rotulos: [], assuntos: [], assuntos_rotulos: [] }), 'A IA não respondeu')
    assert.equal(P.resumoHumano({ motivos: [], rotulos: [], assuntos: [], assuntos_rotulos: [] }), 'Precisa de uma pessoa')
    assert.equal(P.resumoHumano(null), '')

    // Os avisos que a sugestão recolhida esconde: validador + "Confira" do agente.
    assert.equal(P.avisosDaSugestao({ validador_erros: ['Promete prazo.'], agente: { ferramentas: [], acoes_pessoa: [], avisos: ['promete ação da equipe', ''] } }), 2)
    assert.equal(P.avisosDaSugestao({ validador_erros: null, agente: null }), 0)
    assert.equal(P.avisosDaSugestao(null), 0)
  }

  // ------------------------------------------------ a faixa da Caixa Humano
  {
    const faixa = (tpl.match(/<div\n {10}v-if="conversa\.humano"[\s\S]*?\n {8}<\/div>/) || [])[0]
    assert.ok(faixa, 'a faixa da Caixa Humano')
    // O corpo some por v-show; o resumo só existe recolhida.
    assert.match(faixa, /<span v-show="caixaHumanoAberta"[^>]*data-faixa-humano-corpo>/)
    assert.match(faixa, /v-if="!caixaHumanoAberta"[\s\S]*?data-faixa-humano-resumo/)

    const ls = armazenamento()
    const x = caixasDaConversa(ls)
    x.montar()
    assert.equal(x.caixaHumanoAberta.value, true, 'começa aberta')
    const humano = { motivos: ['reclamacao', 'atendente', 'assunto'], rotulos: ['Reclamação aberta', 'Pediu atendente', 'Assunto só de pessoa'], assuntos: ['garantia'], assuntos_rotulos: ['Garantia'], na_caixa: true }
    const ctx = {
      conversa: { humano }, corHumano: P.corHumano, faixaHumano: P.faixaHumano, rotulosHumano: P.rotulosHumano, resumoHumano: P.resumoHumano,
      tituloForaDaCaixaHumano: 'fora', caixaHumanoAberta: x.caixaHumanoAberta, alternarCaixaHumano: x.alternarCaixaHumano,
    }
    let h = await renderizarTrecho(faixa, ctx)
    assert.ok(fechado(h, 'data-faixa-humano-alternar', 'true'))
    assert.match(h, /<span>Caixa Humano<\/span>/)
    assert.match(h, /data-faixa-humano-corpo>— a IA não responde esta: Reclamação aberta · Pediu atendente · Assunto só de pessoa: Garantia</)
    assert.doesNotMatch(h, /data-faixa-humano-resumo/)
    assert.doesNotMatch(h, /display:none/)

    x.alternarCaixaHumano()
    assert.equal(ls.dados.get(C.CAIXA_HUMANO_ABERTA_KEY), '0', 'lembra recolhida')
    h = await renderizarTrecho(faixa, ctx)
    assert.ok(fechado(h, 'data-faixa-humano-alternar', 'false'))
    assert.ok(escondido(h, 'data-faixa-humano-corpo'), 'recolhida: o corpo só escondido')
    assert.match(h, /title="Reclamação aberta · Pediu atendente · Assunto só de pessoa: Garantia"[^>]*data-faixa-humano-resumo[^>]*>Reclamação aberta · Pediu atendente \+1</)
    assert.match(h, /<span>Caixa Humano<\/span>/, 'o nome fica')
    x.alternarCaixaHumano()
    assert.equal(ls.dados.get(C.CAIXA_HUMANO_ABERTA_KEY), '1')

    // O navegador lembra: recolhida na próxima conversa/visita.
    const y = caixasDaConversa(armazenamento({ [C.CAIXA_HUMANO_ABERTA_KEY]: '0' }))
    y.montar()
    assert.equal(y.caixaHumanoAberta.value, false)
    assert.equal(y.sugestaoIaAberta.value, true, 'uma chave por caixa')
    // Sem localStorage: começa aberta e recolhe mesmo assim (só não lembra).
    const z = caixasDaConversa(armazenamento({}, true))
    z.montar()
    assert.equal(z.caixaHumanoAberta.value, true)
    z.alternarCaixaHumano()
    assert.equal(z.caixaHumanoAberta.value, false)
  }

  // ------------------------------------------------ a sugestão da IA (caixa de resposta)
  {
    const ini = tpl.indexOf('<div v-if="rascunho" class="space-y-1 rounded-md border border-violet')
    const fim = tpl.indexOf('<!-- depois do envio: a sugestão estava boa? -->')
    assert.ok(ini > 0 && fim > ini, 'a caixa da sugestão')
    const caixa = tpl.slice(ini, fim)
    // O corpo (porquê, rastro do agente, validador) é a ÚNICA parte que recolhe;
    // o motivo do descarte fica fora dela, e os botões ficam na linha do título.
    assert.equal((tpl.match(/v-show="sugestaoIaAberta"/g) || []).length, 1)
    assert.match(caixa, /\n( {12})<div v-show="sugestaoIaAberta"[^>]*data-sugestao-ia-corpo>[\s\S]*?\n\1<\/div>\n\1<div v-if="descarte\.aberto"/)
    const corpo = caixa.slice(caixa.indexOf('data-sugestao-ia-corpo'), caixa.indexOf('<div v-if="descarte.aberto"'))
    for (const fora of ['usarSugestao', 'copiarTexto', 'descarte', 'Descartar sugestão']) assert.ok(!corpo.includes(fora), `${fora} fora do que recolhe`)
    // A caixa de resposta não faz parte (a sugestão continua entrando nela).
    assert.doesNotMatch(caixa, /<textarea/)
    assert.ok(tpl.indexOf('v-model="texto"') > fim, 'a caixa de resposta vem depois, fora da sugestão')
    assert.doesNotMatch(trecho(setup, 'function aplicarRascunho(', '// ─── carregar'), /Aberta/, 'pôr a sugestão na caixa não depende de recolher')

    const ls = armazenamento()
    const x = caixasDaConversa(ls)
    x.montar()
    assert.equal(x.sugestaoIaAberta.value, true, 'começa aberta')
    const descarte = Vue.reactive({ aberto: false, motivo: '', salvando: null })
    const usou = []
    const rascunho = {
      id: 'r1', texto: 'Olá! Vamos resolver.', categoria: 'garantia', confianca: 0.7, precisa_humano: true, do_turno_atual: true,
      motivo: 'Cliente reclama de defeito.', validador_erros: ['Promete prazo sem dado.'], status: 'pendente', created_at: null,
      agente: { ferramentas: [], acoes_pessoa: [{ codigo: 'foto', rotulo: 'Analisar foto ou vídeo' }], avisos: ['promete ação da equipe'] },
    }
    const ctx = {
      rascunho, categoriaLabel: P.categoriaLabel, seloPrecisaDePessoa: P.seloPrecisaDePessoa, sugestaoDeAntes: P.sugestaoDeAntes,
      avisosDaSugestao: P.avisosDaSugestao, rotuloDosAvisos: R.rotuloDosAvisos, linhaDoAgente: P.linhaDoAgente,
      usandoSugestao: false, usarSugestao: () => usou.push('usar'), copiarTexto: () => usou.push('copiar'), canEdit: true,
      descarte, MOTIVOS_DESCARTE: ['tom inadequado'], MAX_MOTIVO: 2000, confirmarDescarte: () => usou.push('descartar'),
      sugestaoIaAberta: x.sugestaoIaAberta, alternarSugestaoIa: x.alternarSugestaoIa,
      // A oferta da NF (28/10/2026) fica fora do corpo recolhível: sem a ação, não aparece.
      nfNoRascunho: C.nfNoRascunho, respondePorOutra: false,
    }
    let h = await renderizarTrecho(caixa, ctx)
    assert.ok(fechado(h, 'data-sugestao-ia-alternar', 'true'))
    assert.match(h, /Por quê:<\/span> Cliente reclama de defeito\./)
    assert.match(h, /Não consultou nada · Precisa de pessoa: Analisar foto ou vídeo · Confira: promete ação da equipe/)
    assert.doesNotMatch(h, /data-sugestao-ia-avisos-selo/)
    assert.doesNotMatch(h, /display:none/)

    x.alternarSugestaoIa()
    assert.equal(ls.dados.get(C.SUGESTAO_IA_ABERTA_KEY), '0', 'lembra recolhida')
    descarte.aberto = true
    h = await renderizarTrecho(caixa, ctx)
    assert.ok(fechado(h, 'data-sugestao-ia-alternar', 'false'))
    assert.ok(escondido(h, 'data-sugestao-ia-corpo'), 'recolhida: o corpo só escondido')
    assert.equal((h.match(/display:none/g) || []).length, 1, 'só o corpo some')
    // O título fica: assunto · confiança · "precisa de pessoa" e o selo dos avisos.
    assert.match(h, /Sugestão da IA\s*<\/button>/)
    assert.match(h, /· 70% de confiança/)
    assert.match(h, />precisa de pessoa</)
    assert.match(h, /data-sugestao-ia-avisos-selo[^>]*>[\s\S]*2 avisos</)
    // Os botões e o motivo do descarte seguem à vista.
    assert.match(h, />usar sugestão</)
    assert.match(h, /Descartar sugestão/)
    assert.match(h, /data-sugestao-ia-descarte/)
    assert.match(h, /aria-label="motivo do descarte da sugestão"/)
    x.alternarSugestaoIa()
    assert.equal(ls.dados.get(C.SUGESTAO_IA_ABERTA_KEY), '1')

    const y = caixasDaConversa(armazenamento({ [C.SUGESTAO_IA_ABERTA_KEY]: '0' }))
    y.montar()
    assert.equal(y.sugestaoIaAberta.value, false)
    assert.equal(y.caixaHumanoAberta.value, true, 'uma chave por caixa')
  }

  // ------------------------------------------------ o cartão da disputa (AtendimentoReclamacao)
  {
    const em3dias = new Date(Date.now() + 3 * 24 * 3600 * 1000 + 60_000).toISOString()
    const disputa = {
      id: 'd1', plataforma: 'shopee', plataforma_nome: 'Shopee', numero: '251020ABC', tipo: 'mediacao', tipo_rotulo: 'Disputa',
      status: 'SELLER_DISPUTE', status_rotulo: 'Loja contestou', aberta: true, motivo: 'FUNCTIONAL_DMG', solucao: 'Devolução + reembolso',
      pedido_marketplace: '251020ABC', prazo_em: em3dias, aberta_em: '2026-10-20T12:00:00Z', encerrada_em: null, acao_pendente: null,
      mediacao: true, reputacao_afetada: null, devolucao_status: null, conversa_id: 'c1', url_plataforma: 'https://seller.shopee.com.br/portal/sale/returnrefund/1',
    }
    const encerrada = { ...disputa, id: 'd0', numero: '250901XYZ', aberta: false, status: 'CLOSED', status_rotulo: 'Encerrada pela Shopee', prazo_em: null, encerrada_em: '2026-09-10T12:00:00Z' }
    const respostas = { '/api/atendimento/conversas/c1/reclamacoes': { itens: [disputa, encerrada], abertas: 1, prazo_mais_curto: em3dias } }
    const filhos = { AtendimentoAbrirPlataforma: { props: ['href', 'perfil', 'conversaId', 'titulo'], setup: (p, { slots }) => () => Vue.h('a', { href: p.href, 'data-abrir-plataforma': '' }, slots.default?.()) } }

    const ls = armazenamento()
    const m = montar('../components/AtendimentoReclamacao.vue', { conversaId: 'c1', perfil: null }, { respostas, localStorage: ls, filhos })
    await m.montar()
    let h = await m.html()
    assert.equal(m.estado.cartaoAberto, true, 'começa aberto')
    assert.ok(fechado(h, 'data-reclamacao-alternar', 'true'))
    assert.match(h, /Só leitura aqui: responda e aja pela plataforma\./)
    assert.match(h, /Pede: Devolução \+ reembolso/)
    assert.doesNotMatch(h, /data-reclamacao-resumo/)
    assert.ok(!escondido(h, 'data-reclamacao-corpo'))
    assert.match(h, /class="rounded-md border text-\[13px\] leading-5 [^"]*p-3"/, 'aberto: o cartão de sempre')

    m.estado.alternar()
    await Vue.nextTick()
    assert.equal(ls.dados.get(RC.RECLAMACAO_ABERTA_KEY), '0', 'lembra recolhido')
    h = await m.html()
    assert.ok(fechado(h, 'data-reclamacao-alternar', 'false'))
    assert.ok(escondido(h, 'data-reclamacao-corpo'), 'recolhido: o corpo só escondido')
    // A linha do título: tipo · nº · plataforma · status, e o prazo à direita.
    const titulo = (h.match(/data-reclamacao-alternar[\s\S]*?data-reclamacao-corpo/) || [])[0] || ''
    assert.match(titulo, /Disputa/)
    assert.match(titulo, /nº 251020ABC/)
    assert.match(titulo, /· Shopee/)
    assert.match(titulo, /data-reclamacao-resumo[^>]*>· Loja contestou</)
    assert.match(titulo, />faltam 3 d</)
    assert.match(titulo, /aria-label="copiar o nº"/, 'o copiar o nº fica')
    assert.match(h, /class="rounded-md border text-\[13px\] leading-5 [^"]*px-3 py-1\.5"/, 'recolhido: numa linha')
    // Recolher só esconde: o "Abrir na Shopee" continua lá; a encerrada, também.
    assert.match(h, /data-abrir-plataforma[^>]*>[\s\S]*Abrir na Shopee/)
    assert.match(h, /1 encerrada/)
    assert.deepEqual(m.chamadas.map((c) => c.metodo), ['GET'], 'só leitura: recolher não chama a API')

    // O navegador lembra: recolhido na próxima conversa.
    const m2 = montar('../components/AtendimentoReclamacao.vue', { conversaId: 'c1', perfil: null }, { respostas, localStorage: armazenamento({ [RC.RECLAMACAO_ABERTA_KEY]: '0' }), filhos })
    await m2.montar()
    assert.equal(m2.estado.cartaoAberto, false)
    assert.ok(fechado(await m2.html(), 'data-reclamacao-alternar', 'false'))
    // Sem localStorage: aberto, e recolhe mesmo assim.
    const m3 = montar('../components/AtendimentoReclamacao.vue', { conversaId: 'c1', perfil: null }, { respostas, localStorage: armazenamento({}, true), filhos })
    await m3.montar()
    assert.equal(m3.estado.cartaoAberto, true)
    m3.estado.alternar()
    assert.equal(m3.estado.cartaoAberto, false)
  }
}

principal().then(
  () => console.log('ok: atendimento-recolher-caixas (Caixa Humano, disputa e sugestão da IA recolhem e lembram)'),
  (e) => { console.error(e); process.exit(1) },
)
