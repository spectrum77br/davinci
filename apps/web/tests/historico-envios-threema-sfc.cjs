// node tests/historico-envios-threema-sfc.cjs — Histórico › Envios Threema (tarefa 16, C2).
// A página do Histórico ganha a alternância "Alterações | Envios Threema"
// (?ver=threema); a lista dos envios lê GET /api/historico/threema (um item por
// lote, o ID do Threema já mascarado pela API). Sem rede e sem dado real:
//  - as duas SFCs compilam (template e script);
//  - a página: a alternância, o endereço ?ver=threema e a lista de Alterações
//    só quando não é Envios Threema;
//  - a lista (setup de verdade, API falsa): a consulta de fábrica (7 dias,
//    100 por página), os filtros viram parâmetros (chave, ok, origem), o
//    resultado do lote (todos receberam / com falha / não saiu), quem disparou
//    (automático ou a pessoa, pelo botão), o erro em frase de gente e o
//    detalhe por destinatário ao abrir a linha.
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
  compileScript(descriptor, { id: path.basename(rel) })
  return { descriptor, fonte, filename }
}

const icones = new Proxy({}, { get: (_, nome) => ({ name: String(nome), render: () => Vue.h('i', { 'data-icone': String(nome) }) }) })
const libs = new Map()
// O componente importa `onMounted` de 'vue' (sem auto-import): fora de um app,
// o gancho vai para a lista do teste, que o roda quando quiser.
let montadosAtual = []
const vueDoTeste = { ...Vue, onMounted: (fn) => montadosAtual.push(fn) }
function requerer(nome) {
  if (nome === 'lucide-vue-next') return icones
  if (nome === 'vue') return vueDoTeste
  const m = /^~\/lib\/(.+)$/.exec(nome)
  if (!m) return require(nome)
  if (!libs.has(m[1])) {
    const exp = {}
    const fonte = fs.readFileSync(path.resolve(__dirname, '../lib', `${m[1]}.ts`), 'utf8')
    new Function('exports', 'require', transpile(fonte))(exp, requerer)
    libs.set(m[1], exp)
  }
  return libs.get(m[1])
}
const semComentarios = (html) => html.replace(/<!--[\s\S]*?-->/g, '')
const esperar = () => new Promise((r) => setTimeout(r, 0))

// O setup de verdade fora de um app (os `watch` ficam vivos) e o HTML de um app
// SSR com o MESMO estado — o molde de tests/atendimento-email-lojas.cjs.
function montar(rel, responder, query = {}) {
  const { descriptor, filename } = sfc(rel)
  const script = compileScript(descriptor, { id: path.basename(rel), inlineTemplate: false })
  const tpl = compileTemplate({ source: descriptor.template.content, filename, id: path.basename(rel), compilerOptions: { isTS: true, bindingMetadata: script.bindings } })
  const chamadas = []
  const montados = []
  montadosAtual = montados
  const g = {
    ref: Vue.ref, computed: Vue.computed, reactive: Vue.reactive, watch: Vue.watch, nextTick: Vue.nextTick,
    onMounted: (fn) => montados.push(fn),
    useRoute: () => ({ query }),
    useApi: () => ({
      api: async (url, opts = {}) => {
        chamadas.push({ url, query: { ...(opts.query || {}) } })
        return structuredClone(responder(url, opts))
      },
    }),
  }
  const nomes = Object.keys(g)
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', ...nomes, transpile(script.content))(requerer, mod, mod.exports, ...nomes.map((n) => g[n]))
  const tmod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(tpl.code))(requerer, tmod, tmod.exports)
  const comp = mod.exports.default
  const bruto = comp.setup({}, { emit: () => {}, expose: () => {}, attrs: {}, slots: {} })
  const estado = Vue.proxyRefs(bruto)
  const filho = { ...comp, render: tmod.exports.render, setup: () => bruto }
  async function html() {
    const a = Vue.createSSRApp({ render: () => Vue.h(filho) })
    a.config.warnHandler = () => {}
    return semComentarios(await renderToString(a))
  }
  return { html, estado, chamadas, async montar() { for (const fn of montados) await fn(); await esperar() } }
}

// ---------------------------------------------------------------- a página
{
  const { fonte } = sfc('../pages/historico.vue')
  assert.match(fonte, /route\.query\.ver === 'threema' \? 'threema' : 'alteracoes'/, 'a lista certa pelo endereço')
  assert.match(fonte, /\['alteracoes', 'Alterações'\], \['threema', 'Envios Threema'\]/, 'a alternância')
  assert.match(fonte, /if \(v === 'threema'\) query\.ver = 'threema'\s*\n\s*else delete query\.ver/, 'trocar mexe só no ?ver')
  assert.match(fonte, /<HistoricoEnviosThreema v-if="ver === 'threema'" \/>\s*\n\s*<template v-else>/, 'Envios Threema no lugar das Alterações')
  // a porta da página continua a mesma (só quem foi liberado no Histórico)
  assert.match(fonte, /if \(auth\.user\?\.historico !== true\) \{/)
}

// ---------------------------------------------------------------- a lista
const LOTE_PARCIAL = {
  lote_id: 'l1', criado_em: '2026-11-19T10:00:00Z',
  envio: { chave: 'logistica.vigia_automacao', nome: 'Automação parada', painel: 'Logística' },
  escopo: null, contexto: 'logistica', canal: 'logistica', onde_roda: 'servidor', origem: 'automatico',
  via: null, ator: null, titulo: 'Logística — automação parada:', bytes: 120,
  total: 2, entregues: 1, falhas: 1, erro_geral: null,
  destinatarios: [
    { nome: 'Pessoa A', id: 'TS••••••', ok: true, http_status: 200, erro: null, quando: '2026-11-19T10:00:00Z' },
    { nome: 'Pessoa B', id: 'TS••••••', ok: false, http_status: 402, erro: 'http_402', quando: '2026-11-19T10:00:00Z' },
  ],
}
const LOTE_MANUAL = {
  ...LOTE_PARCIAL, lote_id: 'l2', origem: 'manual', ator: 'Fulano Admin', total: 1, entregues: 1, falhas: 0,
  envio: { chave: 'logistica.informar', nome: 'Informar da Logística', painel: 'Logística' },
  destinatarios: [LOTE_PARCIAL.destinatarios[0]],
}
const LOTE_NAO_SAIU = {
  ...LOTE_PARCIAL, lote_id: 'l3', total: 0, entregues: 0, falhas: 1, erro_geral: 'threema_gateway_id_missing',
  envio: { chave: 'devolucoes.prazo_contestacao', nome: 'Prazo de contestação', painel: 'Devoluções' },
  destinatarios: [],
}
const RESPOSTA = {
  items: [LOTE_MANUAL, LOTE_PARCIAL, LOTE_NAO_SAIU], total: 3, limit: 100, offset: 0,
  envios: [
    { chave: 'logistica.vigia_automacao', nome: 'Automação parada', painel: 'Logística' },
    { chave: 'sem_chave', nome: 'Sem marca (ponto de envio não catalogado)', painel: null },
  ],
}

;(async () => {
  const m = montar('../components/HistoricoEnviosThreema.vue', (url) => {
    assert.equal(url, '/api/historico/threema')
    return RESPOSTA
  })
  await m.montar()
  assert.equal(m.chamadas.length, 1)
  assert.deepEqual(m.chamadas[0].query, { dias: 7, limit: 100, offset: 0 }, 'consulta de fábrica')

  let html = await m.html()
  assert.match(html, /Informar da Logística/)
  assert.match(html, /Fulano Admin \(botão\)/, 'quem apertou o botão')
  assert.match(html, /automático/)
  assert.match(html, /pill-success[^>]*>1\/1 receberam/)
  assert.match(html, /pill-warning[^>]*>1\/2 receberam/)
  assert.match(html, /pill-danger[^>]*>não saiu/)
  assert.match(html, /remetente sem ID configurado no servidor/, 'o erro em frase de gente')
  assert.ok(!/sem crédito/.test(html), 'o detalhe só aparece ao abrir a linha')
  assert.match(html, /Pessoa A, Pessoa B/)
  assert.match(html, /1–3 de 3/)

  // abrir a linha mostra cada destinatário e o porquê de quem não recebeu
  m.estado.alternar(LOTE_PARCIAL)
  html = await m.html()
  assert.match(html, /sem crédito no Threema \(402\)/)
  assert.match(html, /TS••••••/)
  // a chave fica só no title; bytes não aparecem; o remetente em nome de gente
  assert.match(html, /title="chave logistica\.vigia_automacao"/)
  assert.ok(!/bytes/.test(html))
  assert.match(html, /Remetente: Logística ·/)
  m.estado.alternar(LOTE_PARCIAL)
  assert.ok(!/sem crédito/.test(await m.html()), 'clicar de novo fecha')

  // os filtros viram parâmetros da consulta
  m.estado.resultado = 'falhou'
  await esperar(); await esperar()
  assert.equal(m.chamadas.at(-1).query.ok, false)
  m.estado.resultado = 'ok'
  await esperar(); await esperar()
  assert.equal(m.chamadas.at(-1).query.ok, true)
  m.estado.envio = 'logistica.vigia_automacao'
  m.estado.origem = 'manual'
  m.estado.dias = 30
  await esperar(); await esperar()
  assert.deepEqual(m.chamadas.at(-1).query, {
    dias: 30, limit: 100, offset: 0, chave: 'logistica.vigia_automacao', ok: true, origem: 'manual',
  })
  html = await m.html()
  assert.match(html, /limpar filtros/)
  assert.match(html, /<option value="sem_chave"[^>]*>Sem marca/, 'o filtro de envio mostra o que apareceu no período')
  m.estado.limparFiltros()
  await esperar(); await esperar()
  assert.deepEqual(m.chamadas.at(-1).query, { dias: 7, limit: 100, offset: 0 })

  // O "Ver tudo no Histórico" da gaveta chega com ?chave=&dias=: já filtrado.
  {
    const m2 = montar('../components/HistoricoEnviosThreema.vue', () => RESPOSTA, { ver: 'threema', chave: 'logistica.informar', dias: '180' })
    await m2.montar()
    assert.deepEqual(m2.chamadas[0].query, { dias: 180, limit: 100, offset: 0, chave: 'logistica.informar' })
    const h = await m2.html()
    assert.match(h, /<option value="logistica\.informar"[^>]*>Envio: logistica\.informar/, 'o envio do endereço aparece no filtro mesmo sem lote no período')
    // período fora da lista volta para 7 dias
    const m3 = montar('../components/HistoricoEnviosThreema.vue', () => RESPOSTA, { dias: '999' })
    await m3.montar()
    assert.equal(m3.chamadas[0].query.dias, 7)
  }

  console.log('historico-envios-threema-sfc: ok')
})().catch((e) => { console.error(e); process.exit(1) })
