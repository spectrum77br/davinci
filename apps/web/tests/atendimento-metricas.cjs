// node tests/atendimento-metricas.cjs — a aba "Métricas" do Atendimento
// (`components/AtendimentoMetricas.vue`), refeita em 13/11/2026 ("por equipe e não mais por loja;
// traz as %; faz o agente 1 vez por dia trazer uma análise geral") e em 16/11/2026 ("uma lista em
// cima a % de mensagens enviadas automático, daí a lista mostrando tempo de resposta de cada
// equipe pegando a média geral, e se abrir mostra específico; em lista, tirando esse bloco"). O
// que a tela tem de garantir:
//  - as ROTAS existem no backend (`routers/atendimento_metricas_equipe.py`, no `main.py`, e o
//    GET /metricas de sempre); a régua da cor é a do envio sozinho (75%) e a hora da análise é
//    a do cron (07:40 de São Paulo);
//  - as funções puras: a % de aprovação (para baixo), as cores, o que saiu sozinho (para baixo:
//    nunca a meta de 60% sem ter batido), o negrito da análise sem v-html, as lojas de cada
//    linha (as de mais respostas primeiro) e as linhas da lista (equipes, "sem equipe" só com
//    movimento, "todas as lojas" no fim);
//  - a tela do admin (setup + SSR, API falsa): a análise no topo com o negrito, o "saiu sozinho"
//    em cima, a lista das equipes com o tempo médio, o no prazo e a aprovação, o clique que abre
//    as lojas da linha; o período que relê as equipes (a análise não); sem os cartões grandes e
//    sem o select "Ver lojas…"; o admin não chama mais o GET /metricas das lojas;
//  - quem não é admin (403 `admin_only` nas rotas novas): só a tabela das lojas de sempre, sem
//    chamar as rotas de admin; o admin que leva 403 cai nela sem erro.
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
  return { descriptor, fonte, filename }
}
const icones = new Proxy({}, { get: (_, nome) => ({ name: String(nome), render: () => Vue.h('i', { 'data-icone': String(nome) }) }) })
const cache = new Map()
function requerer(nome) {
  if (nome === 'lucide-vue-next') return icones
  const m = /^~\/components\/(.+\.vue)$/.exec(nome)
  if (!m) return require(nome)
  if (!cache.has(m[1])) {
    const { descriptor } = sfc(`../components/${m[1]}`)
    const mod = { exports: {} }
    new Function('require', 'module', 'exports', transpile(descriptor.script.content))(requerer, mod, mod.exports)
    cache.set(m[1], mod.exports)
  }
  return cache.get(m[1])
}
const api = (rel) => fs.readFileSync(path.resolve(__dirname, '../../api/app', rel), 'utf8')
const semComentarios = (html) => html.replace(/<!--[\s\S]*?-->/g, '')
const esperar = (ms = 0) => new Promise((r) => setTimeout(r, ms))

const tela = sfc('../components/AtendimentoMetricas.vue')
const M = (() => {
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(tela.descriptor.script.content))(requerer, mod, mod.exports)
  return mod.exports
})()

// ------------------------------------------------ o contrato com o backend
{
  const equipesPy = api('routers/atendimento_metricas_equipe.py')
  const prefixo = /APIRouter\(\s*prefix="([^"]+)"/.exec(equipesPy)[1]
  const doBackend = new Set([...equipesPy.matchAll(/@router\.get\("([^"]+)"/g)].map((m) => `${prefixo}${m[1]}`))
  assert.deepEqual([...doBackend].sort(), ['/api/atendimento/metricas/analise', '/api/atendimento/metricas/equipes'])
  assert.ok(doBackend.has(M.ROTAS.equipes(7).split('?')[0]) && doBackend.has(M.ROTAS.analise))
  assert.match(api('main.py'), /app\.include_router\(atendimento_metricas_equipe_router\.router\)/)
  assert.match(api('routers/atendimento.py'), /@router\.get\("\/metricas", response_model=MetricasOut\)/)
  assert.equal(M.ROTAS.lojas(15), '/api/atendimento/metricas?dias=15')
  assert.equal(M.ROTAS.equipes(1), '/api/atendimento/metricas/equipes?dias=1')
  // Toda chamada da tela usa as ROTAS.
  const setup = tela.descriptor.scriptSetup.content
  const chamadas = [...setup.matchAll(/(?<!function )\b(?:api|soDeAdmin)<[^>]+>\(\s*([^,)]+)/g)].map((m) => m[1].trim())
  assert.ok(chamadas.length >= 3)
  for (const c of chamadas) assert.match(c, /^(ROTAS\.|caminho$)/, c)
  // A cor: a régua do envio sozinho (75%). A hora: o cron (10:40 UTC = 07:40 de São Paulo).
  assert.equal(Number(/^LIMIAR_PADRAO = ([\d.]+)/m.exec(api('services/atendimento/regua.py'))[1]), M.REGUA)
  const worker = api('worker.py')
  const hora = Number(/^_ATENDIMENTO_ANALISE_HORA = (\d+)/m.exec(worker)[1])
  const minuto = Number(/^_ATENDIMENTO_ANALISE_MINUTO = (\d+)/m.exec(worker)[1])
  assert.equal(`${String(hora - 3).padStart(2, '0')}:${String(minuto).padStart(2, '0')}`, M.HORA_ANALISE)
  assert.deepEqual(M.PERIODOS, [1, 7, 15, 30])
}

// ------------------------------------------------ as funções puras
{
  assert.equal(M.aprovacao({ decisoes: 0, acertos: 0 }), null)
  assert.equal(M.aprovacao(null), null)
  assert.equal(M.aprovacao({ decisoes: 4, acertos: 3 }), 0.75)
  assert.equal(M.pctAprovacao(null), '—')
  assert.equal(M.pctAprovacao(0.8), '80%')
  assert.equal(M.pctAprovacao(0.7499), '74%', 'para baixo: nunca 75% sem bater a régua')
  assert.equal(M.pctAprovacao(1), '100%')
  assert.match(M.corAprovacao(0.75), /emerald/)
  assert.match(M.corAprovacao(0.7499), /amber/)
  assert.match(M.corAprovacao(0.6), /amber/)
  assert.match(M.corAprovacao(0.5999), /red/)
  assert.match(M.corAprovacao(null), /muted/)
  assert.match(M.corPrazo(95), /emerald/)
  assert.match(M.corPrazo(80), /amber/)
  assert.match(M.corPrazo(40), /red/)
  assert.equal(M.pctPrazo(88.6), '89%')
  assert.equal(M.pctPrazo(null), '—')
  // O que saiu sozinho: o do backend; sem ele, a conta; sem resposta, —.
  assert.equal(M.sozinho({ pct_sozinho: 0.42, ia_enviou: 1, respostas: 100 }), 0.42)
  assert.equal(M.sozinho({ ia_enviou: 3, respostas: 4 }), 0.75)
  assert.equal(M.sozinho({ ia_enviou: 0, respostas: 0 }), null)
  assert.equal(M.sozinho(null), null)
  assert.equal(M.pctSozinho(0.5999), '59%', 'para baixo: nunca a meta sem ter batido')
  assert.equal(M.pctSozinho(0.6), '60%')
  assert.equal(M.pctSozinho(null), '—')
  assert.equal(M.META_SOZINHO, 0.6)
  assert.match(M.corSozinho(0.6), /emerald/)
  assert.equal(M.corSozinho(0.59), '')
  assert.match(M.corSozinho(null), /muted/)
  assert.equal(M.quandoGerada('2026-11-13T10:40:00Z'), 'gerada às 07:40 de 13/11')
  assert.equal(M.quandoGerada('2026-11-14T02:30:00Z'), 'gerada às 23:30 de 13/11', 'a hora de São Paulo')
  assert.equal(M.quandoGerada(null), '')
  assert.equal(M.proximaAnalise(new Date('2026-11-13T09:00:00Z')), 'hoje às 07:40', '06:00 em São Paulo')
  assert.equal(M.proximaAnalise(new Date('2026-11-13T11:00:00Z')), 'amanhã às 07:40', '08:00 em São Paulo')
  // O negrito do modelo (**…**) vira trecho em negrito; "#" e "* " do começo saem; o resto do
  // "**" some (nada de asterisco na tela).
  assert.deepEqual(M.linhasDaAnalise('**Ontem:** a IA acertou **75%**.\n\n## Ações\n* corrigir o banco **'), [
    [{ texto: 'Ontem:', negrito: true }, { texto: ' a IA acertou ', negrito: false }, { texto: '75%', negrito: true }, { texto: '.', negrito: false }],
    [],
    [{ texto: 'Ações', negrito: false }],
    [{ texto: '• corrigir o banco ', negrito: false }],
  ])
  assert.deepEqual(M.linhasDaAnalise(null), [[]])
  const loja = (id, equipe, respostas, decisoes, nome = id) => ({ integration_id: id, loja: nome, plataforma: 'shopee', equipe, respostas, decisoes, acertos: 0, ia_enviou: 0, equipe_enviou: 0 })
  const lojas = [loja('a', 'Londres', 10, 9), loja('b', 'Israel', 50, 5), loja('c', null, 3, 0), loja('d', 'Londres', 40, 1), loja('e', 'Londres', 10, 20)]
  const ids = (chave) => M.lojasDaEquipe(lojas, chave).map((l) => l.integration_id)
  assert.deepEqual(ids('todas'), ['b', 'd', 'e', 'a', 'c'], 'mais respostas primeiro; empate: mais decisões')
  assert.deepEqual(ids('e:Londres'), ['d', 'e', 'a'])
  assert.deepEqual(ids('sem'), ['c'])
  assert.deepEqual(ids('e:ninguem'), [])
  // As linhas: as equipes na ordem do backend, "sem equipe" só com movimento, "todas" no fim.
  const base = { decisoes: 0, acertos: 0, ia_enviou: 0, equipe_enviou: 0 }
  const m = {
    dias: 7,
    equipes: [{ ...base, equipe: 'Londres', lojas: 2 }, { ...base, equipe: 'Israel', lojas: 1 }],
    sem_equipe: { ...base, lojas: 1, respostas: 3 },
    lojas,
    total: base,
  }
  assert.deepEqual(M.gruposDaLista(m).map((g) => [g.chave, g.nome, g.lojas, !!g.total]), [
    ['e:Londres', 'Londres', 2, false], ['e:Israel', 'Israel', 1, false], ['sem', 'Sem equipe', 1, false], ['todas', 'Todas as lojas', 5, true],
  ])
  assert.deepEqual(M.gruposDaLista({ ...m, sem_equipe: { ...base, lojas: 2 } }).map((g) => g.chave), ['e:Londres', 'e:Israel', 'todas'], 'sem movimento, sem a linha')
  assert.deepEqual(M.gruposDaLista(null), [])
}

// ------------------------------------------------ a tela de verdade (setup + SSR, API falsa)
const script = compileScript(tela.descriptor, { id: 'metricas', inlineTemplate: false })
const tpl = compileTemplate({ source: tela.descriptor.template.content, filename: tela.filename, id: 'metricas', compilerOptions: { isTS: true, bindingMetadata: script.bindings } })
assert.deepEqual(tpl.errors, [], 'o template compila')

const BOTAO = { props: ['disabled', 'size', 'variant'], setup(p, { slots, attrs }) { return () => Vue.h('button', { ...attrs, disabled: p.disabled || undefined }, slots.default?.()) } }
const PLATAFORMA = { props: ['codigo'], render() { return Vue.h('span', { 'data-plataforma': this.codigo }) } }
const proibido = (status, code) => Object.assign(new Error(`HTTP ${status}`), { statusCode: status, data: { detail: { code } } })

function montar({ admin, respostas }) {
  const chamadas = []
  const montados = []
  const responder = async (url) => {
    chamadas.push(url)
    const chave = Object.keys(respostas).find((k) => url === k || url.startsWith(`${k}?`))
    if (!chave) throw new Error(`rota inesperada: ${url}`)
    const valor = typeof respostas[chave] === 'function' ? respostas[chave](url) : respostas[chave]
    if (valor instanceof Error) throw valor
    return structuredClone(valor)
  }
  const g = {
    ref: Vue.ref, computed: Vue.computed, reactive: Vue.reactive, watch: Vue.watch, nextTick: Vue.nextTick,
    onMounted: (fn) => montados.push(fn), onBeforeUnmount: () => {},
    useApi: () => ({ api: responder }),
    useIsAdmin: () => Vue.computed(() => admin),
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
    a.component('Button', BOTAO)
    a.component('AtendimentoPlataforma', PLATAFORMA)
    return semComentarios(await renderToString(a))
  }
  return { estado, chamadas, html, async montar() { for (const fn of montados) await fn(); await esperar() } }
}

const LOJAS_TEMPO = {
  dias: 7,
  lojas: [
    { integration_id: 'i-kia', conta: 'Kia', plataforma: 'shopee', recebidas: 40, respondidas: 38, mediana_primeira_resposta_min: 12, p90_primeira_resposta_min: 90, pct_no_prazo: 92.4 },
    { integration_id: 'i-mega', conta: 'Marquezini', plataforma: 'ml', recebidas: 10, respondidas: 6, mediana_primeira_resposta_min: 300, p90_primeira_resposta_min: 900, pct_no_prazo: 55 },
  ],
  ia: { rascunhos: 50, enviou_igual: 20, editou: 10, descartou: 5, escreveu_do_zero: 3, nota_ok: 7, nota_erro: 2 },
}
const N = (o) => ({ decisoes: 0, acertos: 0, ia_enviou: 0, equipe_enviou: 0, respostas: 0, fora: 0, pct_sozinho: null, respondidas: 0, tempo_medio_min: null, pct_no_prazo: null, ...o })
const EQUIPES = {
  dias: 7,
  equipes: [
    { equipe: 'Londres', lojas: 2, ...N({ decisoes: 40, acertos: 34, aprovacao: 0.85, ia_enviou: 25, equipe_enviou: 12, respostas: 40, fora: 3, pct_sozinho: 0.625, respondidas: 30, tempo_medio_min: 72, pct_no_prazo: 92.4 }) },
    { equipe: 'Israel', lojas: 3, ...N({ decisoes: 20, acertos: 11, aprovacao: 0.55, ia_enviou: 4, equipe_enviou: 30, respostas: 40, fora: 6, pct_sozinho: 0.1, respondidas: 25, tempo_medio_min: 310, pct_no_prazo: 55 }) },
  ],
  sem_equipe: { lojas: 1, ...N({ ia_enviou: 1, respostas: 2, fora: 1, pct_sozinho: 0.5 }) },
  lojas: [
    { integration_id: 'i-kia', loja: 'Kia', plataforma: 'shopee', equipe: 'Londres', ...N({ decisoes: 30, acertos: 27, ia_enviou: 20, equipe_enviou: 8, respostas: 30, pct_sozinho: 0.6667, respondidas: 22, tempo_medio_min: 45, pct_no_prazo: 95 }) },
    { integration_id: 'i-atv', loja: 'Atv', plataforma: 'tiktok', equipe: 'Londres', ...N({ decisoes: 10, acertos: 7, ia_enviou: 5, equipe_enviou: 4, respostas: 10, pct_sozinho: 0.5, respondidas: 8, tempo_medio_min: 140, pct_no_prazo: 80 }) },
    { integration_id: 'i-mega', loja: 'Marquezini', plataforma: 'ml', equipe: 'Israel', ...N({ decisoes: 20, acertos: 11, ia_enviou: 4, equipe_enviou: 30, respostas: 40, pct_sozinho: 0.1, respondidas: 25, tempo_medio_min: 310, pct_no_prazo: 55 }) },
    { integration_id: 'i-solta', loja: 'Solta', plataforma: 'amazon', equipe: null, ...N({ ia_enviou: 1, respostas: 2, pct_sozinho: 0.5 }) },
  ],
  total: N({ decisoes: 60, acertos: 45, aprovacao: 0.75, ia_enviou: 30, equipe_enviou: 42, respostas: 82, fora: 10, pct_sozinho: 0.3659, respondidas: 55, tempo_medio_min: 180, pct_no_prazo: 76 }),
}
const ANALISE = { analise: { dia: '2026-11-13', texto: '**Ontem** a IA acertou 75%.\nLondres segurou a régua.', gerada_em: '2026-11-13T10:40:00Z', modelo: 'claude' } }
const trecho = (html, re) => {
  const m = re.exec(html)
  assert.ok(m, String(re))
  return m[0]
}
// As linhas de cada grupo da lista: [grupo, html da linha principal, [lojas abertas]].
function lista(html) {
  return [...html.matchAll(/<tbody[^>]*data-grupo="([^"]+)"[^>]*>([\s\S]*?)<\/tbody>/g)].map((m) => {
    const linhas = [...m[2].matchAll(/<tr([^>]*)>([\s\S]*?)<\/tr>/g)]
    return [m[1], linhas[0][2], linhas.slice(1).map((l) => (/data-loja="([^"]+)"/.exec(l[1]) || [])[1]).filter(Boolean)]
  })
}

async function principal() {
  // ── o admin: a análise, o que saiu sozinho no topo e a lista das equipes
  {
    const t = montar({
      admin: true,
      respostas: { '/api/atendimento/metricas/equipes': EQUIPES, [M.ROTAS.analise]: ANALISE },
    })
    await t.montar()
    assert.deepEqual(t.chamadas.slice().sort(), [M.ROTAS.analise, M.ROTAS.equipes(7)].sort(), 'o admin não lê mais o GET /metricas das lojas')
    let html = await t.html()
    const analise = trecho(html, /<section[^>]*data-analise[\s\S]*?<\/section>/)
    assert.match(analise, /Análise do dia[\s\S]*gerada às 07:40 de 13\/11/)
    assert.match(analise, /<strong[^>]*>Ontem<\/strong> a IA acertou 75%\.[\s\S]*Londres segurou a régua\./, 'o negrito de verdade')
    assert.doesNotMatch(analise, /\*\*/, 'nada de asterisco na tela')
    assert.ok(html.indexOf('data-analise') < html.indexOf('Período:'), 'a análise no topo')
    // O que saiu sozinho, em cima da lista.
    const sozinho = trecho(html, /<section[^>]*data-sozinho[\s\S]*?<\/section>/)
    assert.match(sozinho, /class="[^"]*text-3xl[^"]*">36%<[\s\S]*das respostas saíram sozinhas, pela IA[\s\S]*\(meta 60%\)/)
    assert.match(sozinho, /30 pela IA · 42 pela equipe no DaVinci · 10 por fora \(Duoke, central da plataforma\) — de 82 respostas no chat nos últimos 7 dias\./)
    assert.ok(html.indexOf('data-sozinho') < html.indexOf('data-equipes'), 'o "saiu sozinho" em cima')
    // A lista: as equipes, "sem equipe" e "todas as lojas"; nada aberto.
    let grupos = lista(html)
    assert.deepEqual(grupos.map((g) => g[0]), ['e:Londres', 'e:Israel', 'sem', 'todas'])
    assert.ok(grupos.every((g) => g[2].length === 0), 'nada aberto de início')
    const [londres, israel, sem, todas] = grupos.map((g) => g[1])
    assert.match(londres, />Londres<[\s\S]*>2 lojas<[\s\S]*text-emerald-700[^"]*">62%<[\s\S]*25 de 40[\s\S]*>1 h 12 min<[\s\S]*text-emerald-700[^"]*">92%<[\s\S]*text-emerald-700[^"]*" title="34 de 40 decisões">85%</)
    assert.match(israel, />Israel<[\s\S]*>3 lojas<[\s\S]*">10%<[\s\S]*4 de 40[\s\S]*>5 h 10 min<[\s\S]*text-red-600[^"]*">55%<[\s\S]*text-red-600[^"]*" title="11 de 20 decisões">55%</)
    assert.match(sem, />Sem equipe<[\s\S]*>1 loja<[\s\S]*">50%<[\s\S]*1 de 2[\s\S]*>—<[\s\S]*text-muted-foreground">—<[\s\S]*title="0 de 0 decisões">—</)
    assert.match(todas, />Todas as lojas<[\s\S]*>4 lojas<[\s\S]*">36%<[\s\S]*30 de 82[\s\S]*>3 h<[\s\S]*text-amber-700[^"]*">76%<[\s\S]*text-emerald-700[^"]*" title="45 de 60 decisões">75%</)
    assert.match(html, /<th[^>]*>Equipe<\/th>[\s\S]*Saiu sozinho[\s\S]*Tempo médio[\s\S]*No prazo[\s\S]*Aprovação da IA/)
    // O que saiu da tela (16/11): os cartões grandes e o select.
    assert.doesNotMatch(html, /data-cartao|Ver lojas|<select/)
    assert.doesNotMatch(html, /data-lojas-tempo/)

    // Clicar abre as lojas da linha; de novo, fecha. Duas abertas ao mesmo tempo, pode.
    t.estado.alternar('e:Londres')
    t.estado.alternar('todas')
    html = await t.html()
    grupos = lista(html)
    assert.deepEqual(grupos.map((g) => [g[0], g[2]]), [
      ['e:Londres', ['i-kia', 'i-atv']], ['e:Israel', []], ['sem', []], ['todas', ['i-mega', 'i-kia', 'i-atv', 'i-solta']],
    ])
    const kia = trecho(html, /<tr[^>]*data-loja="i-kia"[^>]*>[\s\S]*?<\/tr>/)
    assert.match(kia, /data-plataforma="shopee"[\s\S]*>Kia<[\s\S]*text-emerald-700[^"]*">66%<[\s\S]*20 de 30[\s\S]*>45 min<[\s\S]*text-emerald-700[^"]*">95%<[\s\S]*title="27 de 30 decisões">90%</)
    assert.match(html, /aria-expanded="true"[\s\S]*Londres/)
    // Em "todas as lojas" a equipe de cada loja aparece ao lado.
    const todasAbertas = trecho(html, /<tbody[^>]*data-grupo="todas"[\s\S]*?<\/tbody>/)
    assert.match(todasAbertas, />Marquezini<\/span><span[^>]*>· Israel</)
    assert.match(todasAbertas, />Solta<\/span><span[^>]*>· sem equipe</)
    t.estado.alternar('e:Londres')
    assert.deepEqual(lista(await t.html()).map((g) => g[2].length), [0, 0, 0, 4])

    // O período relê as equipes; a análise, não.
    t.chamadas.length = 0
    t.estado.dias = 1
    await Vue.nextTick()
    await esperar()
    assert.deepEqual(t.chamadas, [M.ROTAS.equipes(1)])
    assert.match(await t.html(), /de 82 respostas no chat nas últimas 24 h\./)
    // "atualizar" relê tudo, a análise também.
    t.chamadas.length = 0
    t.estado.atualizar()
    await esperar()
    assert.deepEqual(t.chamadas.slice().sort(), [M.ROTAS.analise, M.ROTAS.equipes(1)].sort())
  }

  // ── sem análise ainda e sem equipe: quando sai a primeira; só "todas as lojas"
  {
    const t = montar({
      admin: true,
      respostas: { '/api/atendimento/metricas/equipes': { ...EQUIPES, equipes: [], sem_equipe: { lojas: 0, ...N({}) }, lojas: [] }, [M.ROTAS.analise]: { analise: null } },
    })
    await t.montar()
    const html = await t.html()
    assert.match(trecho(html, /<section[^>]*data-analise[\s\S]*?<\/section>/), /A primeira análise sai (hoje|amanhã) às 07:40\./)
    assert.deepEqual(lista(html).map((g) => g[0]), ['todas'])
    assert.match(html, /Nenhuma equipe \(Londres, Israel, Marrocos\) com lojas marcadas ainda/)
    t.estado.alternar('todas')
    assert.match(await t.html(), /Nenhuma loja com movimento no período\./)
  }

  // ── quem não é admin: só a tabela das lojas de sempre, sem chamar as rotas de admin
  {
    const t = montar({ admin: false, respostas: { '/api/atendimento/metricas': LOJAS_TEMPO } })
    await t.montar()
    assert.deepEqual(t.chamadas, [M.ROTAS.lojas(7)])
    const html = await t.html()
    assert.doesNotMatch(html, /data-analise|data-equipes|data-sozinho|data-grupo/)
    const tabela = trecho(html, /<section[^>]*data-lojas-tempo[\s\S]*?<\/section>/)
    assert.match(tabela, /Esperas[\s\S]*Respondidas[\s\S]*Tempo típico[\s\S]*9 em 10 até[\s\S]*No prazo/)
    assert.match(tabela, /50 vez\(es\) em que o comprador esperou resposta · 44 respondida\(s\)/)
    // A pior no prazo primeiro.
    assert.ok(tabela.indexOf('Marquezini') < tabela.indexOf('Kia'))
    assert.match(tabela, /font-semibold text-red-600[^"]*">55%</)
  }

  // ── o admin que leva 403 (o papel mudou): cai na tabela de sempre, sem erro
  {
    const t = montar({
      admin: true,
      respostas: {
        '/api/atendimento/metricas': LOJAS_TEMPO,
        '/api/atendimento/metricas/equipes': proibido(403, 'admin_only'),
        [M.ROTAS.analise]: proibido(403, 'admin_only'),
      },
    })
    await t.montar()
    assert.equal(t.estado.visaoAdmin, false)
    assert.equal(t.estado.erro, null, 'o 403 não é erro')
    assert.ok(t.chamadas.includes(M.ROTAS.lojas(7)), 'depois do 403, a tabela das lojas')
    const html = await t.html()
    assert.match(html, /data-lojas-tempo/)
    assert.doesNotMatch(html, /data-analise|data-sozinho|Não consegui/)
    // Outro erro (o servidor caiu) aparece.
    const quebrado = montar({ admin: true, respostas: { '/api/atendimento/metricas': LOJAS_TEMPO, '/api/atendimento/metricas/equipes': Object.assign(new Error('HTTP 500'), { statusCode: 500 }), [M.ROTAS.analise]: ANALISE } })
    await quebrado.montar()
    assert.match(await quebrado.html(), /Não consegui carregar as métricas[\s\S]*tentar de novo/)
  }
  console.log('ok — atendimento-metricas')
}

principal().catch((e) => {
  console.error(e)
  process.exit(1)
})
