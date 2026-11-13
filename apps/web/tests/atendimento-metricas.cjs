// node tests/atendimento-metricas.cjs — a aba "Métricas" do Atendimento, refeita em 13/11/2026
// (`components/AtendimentoMetricas.vue`). O Eduardo: "por equipe e não mais por loja; traz as %;
// faz o agente 1 vez por dia trazer uma análise geral; deixa a tela mais simples; daí se abre o
// select vai abrir as lojas todas e as %". O que a tela tem de garantir:
//  - as ROTAS existem no backend (`routers/atendimento_metricas_equipe.py`, no `main.py`, e o
//    GET /metricas de sempre); a régua da cor é a do envio sozinho (80%) e a hora da análise é
//    a do cron (07:40 de São Paulo);
//  - as funções puras: a % de aprovação (para baixo: nunca "80%" sem ter batido a régua), as
//    cores (≥ 80% verde, 60–79% âmbar, < 60% vermelho, sem decisão "—"), "gerada às HH:MM de
//    DD/MM" e a próxima análise, as lojas da escolha (a pior aprovação primeiro) e o par da loja
//    no GET /metricas (o "no prazo");
//  - a tela do admin (setup + SSR, API falsa): a análise no topo (ou quando sai a primeira), um
//    cartão por equipe e o "Todas" com as cores, o select que só ao escolher abre as lojas, o
//    clique no cartão, o período que relê as equipes (a análise não); sem o placar do 👍/👎 e
//    sem "O que a equipe fez com a IA";
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
  // A cor: a régua do envio sozinho (80%). A hora: o cron (10:40 UTC = 07:40 de São Paulo).
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
  assert.equal(M.pctAprovacao(0.7999), '79%', 'para baixo: nunca 80% sem bater a régua')
  assert.equal(M.pctAprovacao(0.29), '29%')
  assert.equal(M.pctAprovacao(1), '100%')
  assert.match(M.corAprovacao(0.8), /emerald/)
  assert.match(M.corAprovacao(0.7999), /amber/)
  assert.match(M.corAprovacao(0.6), /amber/)
  assert.match(M.corAprovacao(0.5999), /red/)
  assert.match(M.corAprovacao(null), /muted/)
  assert.match(M.corPrazo(95), /emerald/)
  assert.match(M.corPrazo(80), /amber/)
  assert.match(M.corPrazo(40), /red/)
  assert.equal(M.quandoGerada('2026-11-13T10:40:00Z'), 'gerada às 07:40 de 13/11')
  assert.equal(M.quandoGerada('2026-11-14T02:30:00Z'), 'gerada às 23:30 de 13/11', 'a hora de São Paulo')
  assert.equal(M.quandoGerada(null), '')
  assert.equal(M.proximaAnalise(new Date('2026-11-13T09:00:00Z')), 'hoje às 07:40', '06:00 em São Paulo')
  assert.equal(M.proximaAnalise(new Date('2026-11-13T11:00:00Z')), 'amanhã às 07:40', '08:00 em São Paulo')
  assert.equal(M.proximaAnalise(new Date('2026-11-14T02:00:00Z')), 'amanhã às 07:40', '23:00 em São Paulo')
  const loja = (id, equipe, decisoes, acertos, nome = id) => ({ integration_id: id, loja: nome, plataforma: 'shopee', equipe, decisoes, acertos, ia_enviou: 0, equipe_enviou: 0 })
  const lojas = [loja('a', 'londres', 10, 9), loja('b', 'israel', 10, 5), loja('c', null, 0, 0), loja('d', 'londres', 20, 10), loja('e', 'londres', 10, 5)]
  const ids = (escolha) => M.lojasDaEscolha(lojas, escolha).map((l) => l.integration_id)
  assert.deepEqual(ids(''), [], 'nada escolhido, nada aberto')
  assert.deepEqual(ids('todas'), ['d', 'b', 'e', 'a', 'c'], 'a pior primeiro; empate: mais decisões; sem decisão no fim')
  assert.deepEqual(ids('e:londres'), ['d', 'e', 'a'])
  assert.deepEqual(ids('sem'), ['c'])
  assert.deepEqual(ids('e:ninguem'), [])
  const metricas = [
    { integration_id: 'a', conta: 'Kia', plataforma: 'shopee', pct_no_prazo: 91 },
    { integration_id: null, conta: 'Mega', plataforma: 'ml', pct_no_prazo: 70 },
  ]
  assert.equal(M.parDaLoja({ integration_id: 'a', loja: 'kia-integ', plataforma: 'shopee' }, metricas).conta, 'Kia', 'pela integração')
  assert.equal(M.parDaLoja({ integration_id: 'z', loja: ' mega ', plataforma: 'ML' }, metricas).pct_no_prazo, 70, 'sem ela: nome + plataforma')
  assert.equal(M.parDaLoja({ integration_id: 'z', loja: 'mega', plataforma: 'shopee' }, metricas), null)
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
const EQUIPES = {
  dias: 7,
  equipes: [
    { equipe: 'israel', lojas: 3, decisoes: 20, acertos: 11, aprovacao: 0.55, ia_enviou: 4, equipe_enviou: 30 },
    { equipe: 'londres', lojas: 2, decisoes: 40, acertos: 34, aprovacao: 0.85, ia_enviou: 25, equipe_enviou: 12 },
  ],
  lojas: [
    { integration_id: 'i-kia', loja: 'kia', plataforma: 'shopee', equipe: 'londres', decisoes: 30, acertos: 27, aprovacao: 0.9, ia_enviou: 20, equipe_enviou: 8 },
    { integration_id: 'i-atv', loja: 'atv', plataforma: 'tiktok', equipe: 'londres', decisoes: 10, acertos: 7, aprovacao: 0.7, ia_enviou: 5, equipe_enviou: 4 },
    { integration_id: 'i-mega', loja: 'mega', plataforma: 'ml', equipe: 'israel', decisoes: 20, acertos: 11, aprovacao: 0.55, ia_enviou: 4, equipe_enviou: 30 },
    { integration_id: 'i-solta', loja: 'solta', plataforma: 'amazon', equipe: null, decisoes: 0, acertos: 0, aprovacao: null, ia_enviou: 1, equipe_enviou: 0 },
  ],
  total: { decisoes: 60, acertos: 45, aprovacao: 0.75, ia_enviou: 30, equipe_enviou: 42 },
}
const ANALISE = { analise: { dia: '2026-11-13', texto: 'Ontem a IA acertou 75%.\nLondres segurou a régua.', gerada_em: '2026-11-13T10:40:00Z', modelo: 'claude' } }
const trecho = (html, re) => {
  const m = re.exec(html)
  assert.ok(m, String(re))
  return m[0]
}

async function principal() {
  // ── o admin: a análise, os cartões por equipe e as lojas só pelo select
  {
    const t = montar({
      admin: true,
      respostas: { '/api/atendimento/metricas': LOJAS_TEMPO, '/api/atendimento/metricas/equipes': EQUIPES, [M.ROTAS.analise]: ANALISE },
    })
    await t.montar()
    assert.deepEqual(t.chamadas.slice().sort(), [M.ROTAS.analise, M.ROTAS.lojas(7), M.ROTAS.equipes(7)].sort())
    let html = await t.html()
    const analise = trecho(html, /<section[^>]*data-analise[\s\S]*?<\/section>/)
    assert.match(analise, /Análise do dia[\s\S]*gerada às 07:40 de 13\/11[\s\S]*Ontem a IA acertou 75%\.\nLondres segurou a régua\./)
    assert.ok(html.indexOf('data-analise') < html.indexOf('Período:'), 'a análise no topo')
    // Os cartões: "Todas" primeiro, depois as equipes; o número grande é a % com a cor.
    const cartoes = [...html.matchAll(/<button[^>]*data-cartao="([^"]+)"[^>]*>([\s\S]*?)<\/button>/g)].map((m) => [m[1], m[2]])
    assert.deepEqual(cartoes.map((c) => c[0]), ['todas', 'e:israel', 'e:londres'])
    const [todas, israel, londres] = cartoes.map((c) => c[1])
    assert.match(todas, />Todas</)
    assert.match(todas, /text-amber-700[^"]*">75%</)
    assert.match(todas, /60 decisões · 30 saiu sozinho · 42 pela equipe/)
    assert.doesNotMatch(todas, /loja/)
    assert.match(israel, /3 lojas[\s\S]*text-red-600[^"]*">55%</)
    assert.match(londres, /2 lojas[\s\S]*text-emerald-700[^"]*">85%</)
    // O que saiu da tela.
    assert.doesNotMatch(html, /A IA acertou\?|O que a equipe fez com a IA|nota da equipe|Enviou igual|Descartou/)
    // O select: as equipes e "sem equipe"; a tabela só depois de escolher.
    const select = trecho(html, /<select[^>]*aria-label="ver as lojas"[\s\S]*?<\/select>/)
    // (o `value` vazio sai sem `=""` no SSR)
    assert.deepEqual([...select.matchAll(/<option value(?:="([^"]*)")?[^>]*>([^<]*)<\/option>/g)].map((m) => [m[1] ?? '', m[2]]), [
      ['', 'Ver lojas…'], ['todas', 'Todas as equipes'], ['e:israel', 'israel'], ['e:londres', 'londres'], ['sem', 'Sem equipe'],
    ])
    assert.doesNotMatch(html, /<table/, 'nada aberto antes de escolher')
    assert.doesNotMatch(html, /data-lojas-tempo/)

    t.estado.escolha = 'todas'
    html = await t.html()
    const linhas = [...html.matchAll(/<tr[^>]*data-loja="([^"]+)"[^>]*>([\s\S]*?)<\/tr>/g)].map((m) => [m[1], m[2]])
    assert.deepEqual(linhas.map((l) => l[0]), ['i-mega', 'i-atv', 'i-kia', 'i-solta'], 'a pior aprovação primeiro; sem decisão no fim')
    const [mega, atv, kia, solta] = linhas.map((l) => l[1])
    assert.match(mega, /data-plataforma="ml"[\s\S]*>Marquezini<[\s\S]*>israel<[\s\S]*text-red-600[^"]*" title="11 de 20 decisões">55%<[\s\S]*>20<[\s\S]*>4<[\s\S]*>30<[\s\S]*text-red-600[^"]*">55%</)
    assert.match(kia, />Kia<[\s\S]*text-emerald-700[^"]*" title="27 de 30 decisões">90%<[\s\S]*text-emerald-700[^"]*">92%</, 'o "no prazo" da mesma loja')
    assert.match(atv, />atv<[\s\S]*text-amber-700[^"]*" title="7 de 10 decisões">70%<[\s\S]*text-muted-foreground">—</, 'sem par no /metricas: —')
    assert.match(solta, /text-muted-foreground">sem equipe<[\s\S]*text-muted-foreground" title="0 de 0 decisões">—</)
    assert.match(html, /<th[^>]*>Aprovação da IA<\/th>[\s\S]*Decisões[\s\S]*Saiu sozinho[\s\S]*Pela equipe[\s\S]*No prazo/)
    t.estado.escolha = 'e:londres'
    assert.deepEqual(t.estado.linhas.map((l) => l.integration_id), ['i-atv', 'i-kia'])
    t.estado.escolha = 'sem'
    assert.deepEqual(t.estado.linhas.map((l) => l.integration_id), ['i-solta'])
    // O clique no cartão abre as lojas da equipe; de novo, fecha.
    t.estado.abrirLojas('e:israel')
    assert.equal(t.estado.escolha, 'e:israel')
    html = await t.html()
    assert.match(html, /<button[^>]*class="[^"]*ring-2[^"]*"[^>]*data-cartao="e:israel"/)
    assert.doesNotMatch(html, /<button[^>]*class="[^"]*ring-2[^"]*"[^>]*data-cartao="e:londres"/)
    t.estado.abrirLojas('e:israel')
    assert.equal(t.estado.escolha, '')
    assert.doesNotMatch(await t.html(), /<table/)

    // O período relê as lojas e as equipes; a análise, não.
    t.chamadas.length = 0
    t.estado.dias = 30
    await Vue.nextTick()
    await esperar()
    assert.deepEqual(t.chamadas.slice().sort(), [M.ROTAS.lojas(30), M.ROTAS.equipes(30)].sort())
    // "atualizar" relê tudo, a análise também.
    t.chamadas.length = 0
    t.estado.atualizar()
    await esperar()
    assert.deepEqual(t.chamadas.slice().sort(), [M.ROTAS.analise, M.ROTAS.lojas(30), M.ROTAS.equipes(30)].sort())
  }

  // ── sem análise ainda: quando sai a primeira
  {
    const t = montar({
      admin: true,
      respostas: { '/api/atendimento/metricas': LOJAS_TEMPO, '/api/atendimento/metricas/equipes': { ...EQUIPES, equipes: [], lojas: [] }, [M.ROTAS.analise]: { analise: null } },
    })
    await t.montar()
    const html = await t.html()
    assert.match(trecho(html, /<section[^>]*data-analise[\s\S]*?<\/section>/), /A primeira análise sai (hoje|amanhã) às 07:40\./)
    assert.deepEqual([...html.matchAll(/data-cartao="([^"]+)"/g)].map((m) => m[1]), ['todas'])
    assert.match(html, /Nenhuma equipe com lojas marcadas ainda/)
  }

  // ── quem não é admin: só a tabela das lojas de sempre, sem chamar as rotas de admin
  {
    const t = montar({ admin: false, respostas: { '/api/atendimento/metricas': LOJAS_TEMPO } })
    await t.montar()
    assert.deepEqual(t.chamadas, [M.ROTAS.lojas(7)])
    const html = await t.html()
    assert.doesNotMatch(html, /data-analise|data-equipes|data-cartao|Ver lojas/)
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
    const html = await t.html()
    assert.match(html, /data-lojas-tempo/)
    assert.doesNotMatch(html, /data-analise|data-cartao|Não consegui/)
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
