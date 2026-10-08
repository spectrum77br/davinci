// Run from apps/web: node tests/marketing-horarios-sfc.cjs
//
// Marketing › Conferência › Horários dos anúncios (08/10/2026):
// components/MarketingHorarios.vue — o heatmap de horários que ficou das abas
// Mercado Livre e Shopee (removidas a pedido do dono), agora embaixo do
// relatório da Conferência do mesmo marketplace.
//
// Trava o que a tela promete:
//  - lê as contas de Ads do marketplace (/accounts?platform=), em ordem A–Z, e
//    abre no ?horario= do link, senão na última vista nesse marketplace, senão
//    na primeira;
//  - o mapa 7×24 com as mesmas cores, legenda e dica da aba antiga;
//  - o clique liga/desliga a hora com o mesmo PUT da aba antiga (a lista
//    inteira, uma janela de 1 h por célula), a tela muda na hora e os PUTs saem
//    em fila; 23h vai até a meia-noite com fim 0 (o servidor recusa 24);
//  - só quem tem marketing:edit clica (o PUT pede edit);
//  - o aviso do robô do Mac só na Shopee;
//  - nada da agenda automática, Oferta Relâmpago, crédito, tabelas ou gráfico.
// Só dados FALSOS aqui; nenhuma rede.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { parse, compileTemplate, compileScript } = require('vue/compiler-sfc')
const { renderToString } = require('vue/server-renderer')

const transpile = (source, module = ts.ModuleKind.CommonJS) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module },
}).outputText

// ---------------------------------------------------------------- SFC compila
const ARQUIVO = path.join(__dirname, '..', 'components/MarketingHorarios.vue')
const fonte = fs.readFileSync(ARQUIVO, 'utf8')
const { descriptor, errors } = parse(fonte, { filename: ARQUIVO })
assert.deepEqual(errors, [], 'SFC parseia')
const compilado = compileTemplate({ source: descriptor.template.content, filename: ARQUIVO, id: 'horarios-check' })
assert.deepEqual(compilado.errors, [], 'template compila')
const render = (() => {
  const mod = {}
  new Function('exports', 'require', transpile(compilado.code))(mod, require)
  return mod.render
})()
compileScript(descriptor, { id: 'horarios-check' })
const script = descriptor.scriptSetup.content
const tpl = descriptor.template.content

// ---------------------------------------------------------------- higiene estática
for (const trecho of [
  '`/api/marketing/accounts?platform=${p}`',
  '`/api/marketing/schedules/${id}`)',
  '`/api/marketing/schedules/${id}/heatmap`',
  '`/api/marketing/schedules/${id}`, {\n        method: \'PUT\',\n        body: blocks,',
]) assert.ok(script.includes(trecho), `endpoint: ${trecho}`)
assert.match(script, /const canEdit = useCan\('marketing', 'edit'\)/, 'clique atrás de marketing:edit (o PUT pede edit)')
assert.match(script, /defineProps<\{ plataforma: PlataformaHorario \}>\(\)/)
assert.match(script, /type PlataformaHorario = 'shopee' \| 'ml'/, 'só Shopee e Mercado Livre (Amazon não tem mapa)')
assert.ok(!/localStorage|sessionStorage|document\.cookie/.test(script + tpl), 'sem armazenamento do navegador')
{
  // O que ficou com as abas removidas não volta por aqui (comentário não conta).
  const codigo = script.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
  const tela = tpl.replace(/<!--[\s\S]*?-->/g, '')
  for (const fora of [
    '/schedule`', 'schedule_enabled', 'override', 'flash', 'credit', 'trigger', 'timeseries', 'metrics/summary',
    'agent/status', '/commands', 'setInterval', 'PATCH',
  ]) assert.ok(!codigo.includes(fora), `código sem: ${fora}`)
  for (const fora of [
    'Agenda automática', 'Pausar agora', 'Ligar agora', 'Voltar ao automático', 'Oferta Relâmpago', 'Duplicar',
    'Alertas de crédito', 'Últimos', 'Evolução', 'Executor local', 'rodar ciclo agora',
  ]) assert.ok(!tela.includes(fora), `tela sem: ${fora}`)
}
assert.ok(tpl.includes('Horários dos anúncios'), 'título')
assert.ok(tpl.includes('— clique para ligar/desligar'), 'subtítulo')
// Celular: a grade (24 colunas) rola pro lado.
assert.match(tpl, /<div class="overflow-x-auto">\s*<table /, 'grade dentro de overflow-x-auto')
// Cor fixa de texto sempre com a variante do escuro.
for (const m of tpl.matchAll(/(?<!dark:)\btext-(?:emerald|red|amber|sky)-\d00\b(?![^"]*dark:)/g)) {
  assert.fail(`cor sem dark: perto de "${tpl.slice(m.index - 40, m.index + 40)}"`)
}
// As classes das células por extenso (o Tailwind só gera o que lê no componente).
for (const cls of [
  'bg-muted', 'bg-sky-400/60', 'bg-emerald-500/80', 'bg-amber-400/80', 'bg-red-500/80',
  'hover:bg-muted-foreground/30', 'hover:bg-sky-500/80', 'hover:bg-emerald-500', 'hover:bg-amber-500', 'hover:bg-red-600',
]) assert.ok(script.includes(`'${cls}'`), `classe por extenso: ${cls}`)

// ---------------------------------------------------------------- script setup com tudo falso
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const semImports = (s) => s.replace(/^import[\s\S]*?from\s+'[^']+'\s*$/gm, '')
const ICONES = ['AlertTriangle', 'Clock', 'Loader2']
const apiError = {}
new Function('exports', transpile(fs.readFileSync(path.join(__dirname, '../lib/apiError.ts'), 'utf8')))(apiError)
const RETORNO = `return {
  props, DIAS, DO_MARKETPLACE, lembradas, canEdit,
  contas, contasCarregadas, carregandoContas, erroContas, contaId, conta, carregarContas, escolherConta,
  schedules, heatmap, mapaDe, erroMapa, carregarMapa, mapaPronto, scheduleSet, alvoAcos,
  heatmapCellClass, heatmapTooltip, toggleScheduleCell, salvando,
}`
const fabrica = new AsyncFunction(
  'computed', 'onMounted', 'ref', 'watch',
  'useApi', 'useToasts', 'useCan', 'useRoute', 'useRouter', 'useState', 'apiErrMsg', 'defineProps',
  ...ICONES,
  transpile(semImports(script), ts.ModuleKind.ESNext) + '\n' + RETORNO,
)

const esperar = () => new Promise(setImmediate)
const esperarTudo = async () => { for (let i = 0; i < 6; i++) await esperar() }
/** Promessa segurada: a resposta chega quando o teste mandar. */
function segurada() {
  let resolve, reject
  const p = new Promise((res, rej) => { resolve = res; reject = rej })
  return { p, resolve, reject }
}

// Contas de mentira (os ids são uuid na vida real; aqui, legíveis).
const CONTAS = {
  shopee: [
    { id: 'zeta', name: ' \u200bZeta  Malas', platform: 'shopee', acos_target: 10 }, // espaço + invisível na frente (apelido do ML)
    { id: 'alfa', name: 'alfa celulares', platform: 'shopee', acos_target: 10 },
    { id: 'beta', name: 'Beta Eletro', platform: 'shopee', acos_target: 8 },
  ],
  ml: [
    { id: 'ml2', name: 'Mercado Dois', platform: 'ml', acos_target: 12 },
    { id: 'ml1', name: 'Mercado Um', platform: 'ml', acos_target: 12 },
  ],
}
// alfa: seg 18–22 (4 h) e dom 22→2 (vira a meia-noite: 22, 23, 0, 1).
const SCHEDULES = {
  alfa: [
    { id: 's1', account_id: 'alfa', day_of_week: 0, start_hour: 18, end_hour: 22 },
    { id: 's2', account_id: 'alfa', day_of_week: 6, start_hour: 22, end_hour: 2 },
  ],
  beta: [{ id: 's3', account_id: 'beta', day_of_week: 1, start_hour: 9, end_hour: 10 }],
}
const HEATMAPS = {
  alfa: {
    acos_target: 10,
    cells: {
      '0-18': { spend: 5, revenue: 100, impressions: 1234, acos: 5 },
      '0-19': { spend: 12, revenue: 100, impressions: 10, acos: 12 },
      '0-20': { spend: 30, revenue: 100, impressions: 10, acos: 30 },
      '0-21': { spend: 1, revenue: 0, impressions: 3, acos: null },
      '1-5': { spend: 2, revenue: 100, impressions: 1, acos: 2 },
    },
  },
}

// O "servidor" guarda o que o PUT mandou (como o replace_schedules).
async function montar({
  plataforma = 'shopee', canEdit = true, query = {}, contas = CONTAS, schedules = SCHEDULES, heatmaps = HEATMAPS,
  put, nuxt = new Map(), contasErro, mapaErro,
} = {}) {
  const calls = []
  const toastLog = []
  const routerCalls = []
  const montados = []
  const route = { query: { ...query } }
  const servidor = Object.fromEntries(Object.entries(schedules).map(([k, v]) => [k, typeof v === 'function' ? v : JSON.parse(JSON.stringify(v))]))
  let seq = 0
  const api = (url, opts = {}) => {
    calls.push({ url, opts })
    const m = opts.method || 'GET'
    const acc = /^\/api\/marketing\/accounts\?platform=(\w+)$/.exec(url)
    if (m === 'GET' && acc) {
      if (contasErro) return contasErro(acc[1])
      const l = contas[acc[1]] ?? []
      return typeof l === 'function' ? l() : Promise.resolve(JSON.parse(JSON.stringify(l)))
    }
    const hm = /^\/api\/marketing\/schedules\/([^/]+)\/heatmap$/.exec(url)
    if (m === 'GET' && hm) {
      if (mapaErro) return mapaErro(hm[1], 'heatmap')
      const h = heatmaps[hm[1]]
      if (typeof h === 'function') return h()
      return Promise.resolve(JSON.parse(JSON.stringify(h ?? { acos_target: 7, cells: {} })))
    }
    const sc = /^\/api\/marketing\/schedules\/([^/]+)$/.exec(url)
    if (m === 'GET' && sc) {
      if (mapaErro) return mapaErro(sc[1], 'schedules')
      const s = servidor[sc[1]]
      if (typeof s === 'function') return s()
      return Promise.resolve(JSON.parse(JSON.stringify(s ?? [])))
    }
    if (m === 'PUT' && sc) {
      // O ScheduleIn do servidor: dia 0–6, início e fim 0–23 (fim 24 = 422).
      for (const b of opts.body) {
        if (!(b.day_of_week >= 0 && b.day_of_week <= 6 && b.start_hour >= 0 && b.start_hour <= 23 && b.end_hour >= 0 && b.end_hour <= 23)) {
          return Promise.reject({ statusCode: 422, data: { detail: [{ loc: ['body', 0, 'end_hour'], msg: 'Input should be less than or equal to 23' }] } })
        }
      }
      const salvar = () => {
        servidor[sc[1]] = opts.body.map((b) => ({ id: `srv-${++seq}`, account_id: sc[1], ...b }))
        return JSON.parse(JSON.stringify(servidor[sc[1]]))
      }
      if (put) return put(sc[1], opts.body, salvar)
      return Promise.resolve(salvar())
    }
    return Promise.reject(new Error(`api falso não conhece ${m} ${url}`))
  }
  const f = (kind) => (...a) => toastLog.push([kind, ...a])
  const toasts = { success: f('success'), error: f('error'), warning: f('warning'), info: f('info') }
  const props = Vue.reactive({ plataforma })
  const s = await fabrica(
    Vue.computed, (fn) => montados.push(fn), Vue.ref, Vue.watch,
    () => ({ api }), () => toasts,
    (recurso, acao) => {
      assert.equal(recurso, 'marketing')
      return Vue.ref(acao === 'edit' ? canEdit : true)
    },
    () => route,
    () => ({ replace: (alvo) => { routerCalls.push(alvo); route.query = { ...alvo.query }; return Promise.resolve() } }),
    (chave, ini) => {
      if (!nuxt.has(chave)) nuxt.set(chave, Vue.ref(ini()))
      return nuxt.get(chave)
    },
    apiError.apiErrMsg,
    () => props,
    ...ICONES.map((n) => ({ name: n })),
  )
  for (const fn of montados) fn()
  await esperarTudo()
  return { s, props, calls, toastLog, routerCalls, route, servidor, nuxt }
}
const urls = (calls) => calls.map((c) => `${c.opts?.method || 'GET'} ${c.url}`)
const puts = (calls) => calls.filter((c) => c.opts?.method === 'PUT')

/** A tela inteira, renderizada (SSR) com o estado do componente. */
function renderizar(t) {
  const estado = { ...t.s, plataforma: Vue.computed(() => t.props.plataforma) }
  const app = Vue.createSSRApp({ setup: () => estado, render })
  for (const n of ICONES) app.component(n, { render: () => Vue.h('svg', { 'data-icone': n }) })
  return renderToString(app)
}

async function run() {
  // Shopee: contas A–Z, abre na primeira, lê janelas e heatmap dela.
  {
    const t = await montar()
    const { s } = t
    assert.deepEqual(s.contas.value.map((c) => c.id), ['alfa', 'beta', 'zeta'], 'contas em ordem A–Z (sem ligar pra maiúscula)')
    assert.equal(s.contaId.value, 'alfa', 'abre na primeira')
    assert.deepEqual(urls(t.calls), [
      'GET /api/marketing/accounts?platform=shopee',
      'GET /api/marketing/schedules/alfa',
      'GET /api/marketing/schedules/alfa/heatmap',
    ])
    assert.equal(s.mapaPronto.value, true)
    assert.deepEqual(t.routerCalls, [], 'abrir não escreve na URL')
    assert.equal(t.nuxt.get('marketing:horarios-conta').value.shopee, 'alfa', 'lembra a conta vista')

    // A grade: seg 18–21 ligadas (fim 22 fora); dom 22→2 vira a meia-noite.
    const set = s.scheduleSet.value
    for (const k of ['0-18', '0-19', '0-20', '0-21', '6-22', '6-23', '6-0', '6-1']) assert.ok(set.has(k), `ligada: ${k}`)
    for (const k of ['0-17', '0-22', '6-2', '6-21', '1-5']) assert.ok(!set.has(k), `desligada: ${k}`)
    assert.equal(set.size, 8)

    // Cores pelo ACOS (alvo 10): <10 verde, <15 âmbar, senão vermelho; ligada sem
    // dado azul; desligada cinza (mesmo com dado).
    const cor = (d, h) => s.heatmapCellClass(d, h)
    assert.equal(cor(0, 18), 'bg-emerald-500/80 hover:bg-emerald-500 cursor-pointer')
    assert.equal(cor(0, 19), 'bg-amber-400/80 hover:bg-amber-500 cursor-pointer')
    assert.equal(cor(0, 20), 'bg-red-500/80 hover:bg-red-600 cursor-pointer')
    assert.equal(cor(0, 21), 'bg-sky-400/60 hover:bg-sky-500/80 cursor-pointer', 'ACOS nulo = ligado sem dados')
    assert.equal(cor(6, 23), 'bg-sky-400/60 hover:bg-sky-500/80 cursor-pointer', 'sem célula = ligado sem dados')
    assert.equal(cor(1, 5), 'bg-muted hover:bg-muted-foreground/30 cursor-pointer', 'desligada fica cinza')
    assert.equal(s.alvoAcos.value, 10)

    // Dica: dia, hora, ligado/desligado e os números (ou "sem dados").
    const dica = s.heatmapTooltip(0, 18).split('\n')
    assert.equal(dica[0], 'Seg 18:00 — ligado')
    assert.equal(dica[1], 'ACOS: 5.0%')
    assert.match(dica[2], /^Gasto: R\$\s?5,00$/)
    assert.match(dica[3], /^Faturamento: R\$\s?100,00$/)
    assert.equal(dica[4], 'Impressões: 1.234')
    assert.deepEqual(s.heatmapTooltip(6, 3).split('\n'), ['Dom 3:00 — desligado', 'Sem dados nesse horário'])
    assert.equal(s.heatmapTooltip(0, 21).split('\n')[1], 'ACOS: —')

    // A tela: 7 × 24 botões, título, subtítulo, legenda com o alvo, o seletor A–Z e o aviso da Shopee.
    const html = await renderizar(t)
    assert.equal((html.match(/<button type="button" class="block size-6 rounded/g) || []).length, 7 * 24, '168 células')
    assert.equal((html.match(/aria-pressed="true"/g) || []).length, 8, '8 horas ligadas')
    for (const d of ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']) assert.ok(html.includes(`>${d}</th>`), `linha ${d}`)
    assert.ok(html.includes('Horários dos anúncios'))
    assert.ok(html.includes('— clique para ligar/desligar'))
    assert.ok(html.includes('ACOS &lt; 10.0%'), 'legenda com o alvo')
    assert.ok(html.includes('ACOS médio (até 15.0%)'))
    for (const l of ['ACOS ruim', 'Ligado, sem dados', 'Desligado']) assert.ok(html.includes(l), `legenda: ${l}`)
    assert.ok(html.includes('Os anúncios da Shopee são ligados e desligados pelo robô do Mac (18h–22h). Este mapa guarda os horários, mas ainda não comanda o robô.'), 'aviso do robô na Shopee')
    const opcoes = [...html.matchAll(/<option value="(\w+)"[^>]*>([^<]*)<\/option>/g)].map((m) => m[2])
    assert.deepEqual(opcoes, ['alfa celulares', 'Beta Eletro', 'Zeta Malas'], 'seletor A–Z')
    assert.ok(!html.includes('aria-disabled'), 'quem edita clica')

    // Clique liga uma hora: o mesmo PUT da aba antiga, com TODAS as horas ligadas,
    // 1 h por célula; a tela muda antes da resposta.
    const p = s.toggleScheduleCell(2, 10)
    assert.ok(s.scheduleSet.value.has('2-10'), 'liga na hora')
    await p
    const [put1] = puts(t.calls)
    assert.equal(put1.url, '/api/marketing/schedules/alfa')
    assert.equal(put1.opts.method, 'PUT')
    const celulas = (body) => body.map((b) => `${b.day_of_week}-${b.start_hour}`).sort()
    assert.deepEqual(celulas(put1.opts.body), ['0-18', '0-19', '0-20', '0-21', '2-10', '6-0', '6-1', '6-22', '6-23'])
    for (const b of put1.opts.body) assert.equal(b.end_hour, (b.start_hour + 1) % 24, '1 h por célula')
    assert.deepEqual(Object.keys(put1.opts.body[0]).sort(), ['day_of_week', 'end_hour', 'start_hour'], 'mesmo corpo da aba antiga')
    assert.ok(s.schedules.value.every((x) => x.id.startsWith('srv-')), 'a resposta do servidor entra na tela')
    assert.ok(s.scheduleSet.value.has('2-10'))
    assert.equal(s.salvando.value, 0)

    // Clique desliga.
    await s.toggleScheduleCell(0, 18)
    assert.ok(!celulas(puts(t.calls)[1].opts.body).includes('0-18'), 'desligou 0-18')
    assert.ok(!s.scheduleSet.value.has('0-18'))

    // 23h liga com fim 0 (vira a meia-noite): o servidor aceita, e a tela relê como só 23h.
    // Com dom 23h já ligada (veio do 22→2), salvar qualquer coisa não pode dar 422.
    await s.toggleScheduleCell(3, 23)
    const put3 = puts(t.calls)[2]
    assert.ok(put3.opts.body.some((b) => b.day_of_week === 3 && b.start_hour === 23 && b.end_hour === 0), '23h → fim 0')
    assert.ok(put3.opts.body.every((b) => b.end_hour >= 0 && b.end_hour <= 23), 'nenhum fim 24')
    assert.equal(t.toastLog.length, 0, 'nenhum erro')
    assert.ok(s.scheduleSet.value.has('3-23'))
    assert.ok(!s.scheduleSet.value.has('3-0'), '23h não liga a meia-noite')

    // Trocar de conta pelo seletor: lembra, vai pro link e lê o mapa da outra.
    s.escolherConta('beta')
    await esperarTudo()
    assert.equal(s.contaId.value, 'beta')
    assert.deepEqual(t.routerCalls.at(-1), { query: { horario: 'beta' } })
    assert.equal(t.nuxt.get('marketing:horarios-conta').value.shopee, 'beta')
    assert.deepEqual(urls(t.calls).slice(-2), ['GET /api/marketing/schedules/beta', 'GET /api/marketing/schedules/beta/heatmap'])
    assert.deepEqual([...s.scheduleSet.value], ['1-9'])
    assert.equal(s.alvoAcos.value, 7, 'alvo do heatmap da conta')
    s.escolherConta('nao-existe')
    assert.equal(s.contaId.value, 'beta', 'id fora da lista não troca')
  }

  // ?horario= do link manda; inválido cai na lembrada; sem lembrada, na primeira.
  {
    const t1 = await montar({ query: { horario: 'zeta', aba: 'conferencia' } })
    assert.equal(t1.s.contaId.value, 'zeta')
    const nuxt = new Map([['marketing:horarios-conta', Vue.ref({ shopee: 'beta' })]])
    const t2 = await montar({ query: { horario: 'ml1' }, nuxt })
    assert.equal(t2.s.contaId.value, 'beta', 'id do outro marketplace no link: vale a lembrada')
    const t3 = await montar({ query: { horario: 'lixo' } })
    assert.equal(t3.s.contaId.value, 'alfa')
  }

  // Shopee → Mercado Livre → Shopee: cada marketplace com as suas contas e a sua
  // lembrada; a Amazon desmonta o componente, e a lembrada sobrevive (useState).
  {
    const nuxt = new Map()
    const t = await montar({ nuxt })
    t.s.escolherConta('zeta')
    await esperarTudo()
    t.props.plataforma = 'ml'
    await esperarTudo()
    assert.deepEqual(t.s.contas.value.map((c) => c.id), ['ml2', 'ml1'], 'contas do ML, A–Z')
    assert.equal(t.s.contaId.value, 'ml2')
    assert.ok(urls(t.calls).includes('GET /api/marketing/accounts?platform=ml'))
    assert.deepEqual(urls(t.calls).slice(-2), ['GET /api/marketing/schedules/ml2', 'GET /api/marketing/schedules/ml2/heatmap'])
    const htmlMl = await renderizar(t)
    assert.ok(!htmlMl.includes('robô do Mac'), 'ML sem o aviso do robô')
    assert.ok(htmlMl.includes('Horários dos anúncios'))
    t.s.escolherConta('ml1')
    await esperarTudo()
    // Volta pra Shopee depois de passar pela Amazon (componente novo, sem ?horario= da Shopee).
    const t2 = await montar({ nuxt, query: { horario: 'ml1' } })
    assert.equal(t2.s.contaId.value, 'zeta', 'a Shopee volta na conta lembrada')
    const t3 = await montar({ nuxt, plataforma: 'ml' })
    assert.equal(t3.s.contaId.value, 'ml1', 'o ML volta na dele')
  }

  // Só quem edita clica: sem marketing:edit, nada de PUT e a tela diz por quê.
  {
    const t = await montar({ canEdit: false })
    await t.s.toggleScheduleCell(2, 10)
    assert.equal(puts(t.calls).length, 0, 'sem PUT')
    assert.ok(!t.s.scheduleSet.value.has('2-10'))
    assert.equal(t.s.heatmapCellClass(0, 18), 'bg-emerald-500/80 cursor-default', 'sem hover de clique')
    const html = await renderizar(t)
    assert.ok(html.includes('só quem edita o Marketing liga/desliga'), 'dica pra quem só vê')
    assert.ok(!html.includes('clique para ligar/desligar'))
    assert.equal((html.match(/aria-disabled="true"/g) || []).length, 7 * 24)
  }

  // Cliques rápidos: o 2º PUT só sai depois do 1º e leva as duas horas; só a
  // resposta do último entra na tela.
  {
    const fila = []
    const t = await montar({ put: (id, body, salvar) => { const h = segurada(); fila.push({ h, salvar }); return h.p } })
    const a = t.s.toggleScheduleCell(2, 10)
    const b = t.s.toggleScheduleCell(2, 11)
    await esperarTudo()
    assert.equal(puts(t.calls).length, 1, 'um PUT por vez')
    assert.equal(t.s.salvando.value, 2)
    assert.ok(t.s.scheduleSet.value.has('2-10') && t.s.scheduleSet.value.has('2-11'), 'as duas já na tela')
    fila[0].h.resolve(fila[0].salvar())
    await esperarTudo()
    assert.equal(puts(t.calls).length, 2)
    assert.ok(t.s.scheduleSet.value.has('2-11'), 'resposta do 1º não apaga o 2º clique')
    const corpo2 = puts(t.calls)[1].opts.body.map((x) => `${x.day_of_week}-${x.start_hour}`)
    assert.ok(corpo2.includes('2-10') && corpo2.includes('2-11'), 'o 2º leva as duas')
    fila[1].h.resolve(fila[1].salvar())
    await Promise.all([a, b])
    assert.equal(t.s.salvando.value, 0)
    assert.deepEqual(t.servidor.alfa.map((x) => `${x.day_of_week}-${x.start_hour}`).sort(),
      [...t.s.scheduleSet.value].sort(), 'tela = servidor')
  }

  // PUT falhou: avisa e a tela volta ao que está salvo.
  {
    const t = await montar({ put: () => Promise.reject({ statusCode: 403, data: { detail: { code: 'forbidden' } } }) })
    await t.s.toggleScheduleCell(2, 10)
    await esperarTudo()
    assert.deepEqual(t.toastLog, [['error', 'Não consegui salvar o horário', 'forbidden']])
    assert.ok(!t.s.scheduleSet.value.has('2-10'), 'voltou ao servidor')
    assert.equal(urls(t.calls).filter((u) => u === 'GET /api/marketing/schedules/alfa').length, 2, 'releu as janelas')
    assert.equal(t.s.salvando.value, 0)
  }

  // PUT falhou com outros cliques esperando na fila: eles foram montados em cima
  // da hora que não salvou e NÃO saem (salvariam ela assim mesmo); um aviso só,
  // e a tela volta ao servidor (antes: o 2º PUT gravava a hora que "falhou" e a
  // releitura, correndo junto com ele, podia pintar desligado o que estava ligado).
  {
    const fila = []
    const t = await montar({ put: (id, body, salvar) => { const h = segurada(); fila.push({ h, salvar }); return h.p } })
    const servidorAntes = JSON.stringify(t.servidor.alfa)
    const getsJanelas = () => urls(t.calls).filter((u) => u === 'GET /api/marketing/schedules/alfa').length
    const a = t.s.toggleScheduleCell(2, 10)
    const b = t.s.toggleScheduleCell(2, 11)
    const c = t.s.toggleScheduleCell(2, 12)
    await esperarTudo()
    assert.equal(puts(t.calls).length, 1)
    assert.equal(t.s.salvando.value, 3)
    fila[0].h.reject({ statusCode: 500, message: 'boom' })
    await esperarTudo()
    assert.equal(puts(t.calls).length, 1, 'os cliques da fila não saem depois da falha')
    await Promise.all([a, b, c])
    assert.deepEqual(t.toastLog, [['error', 'Não consegui salvar o horário', 'boom']], 'um aviso só')
    assert.equal(JSON.stringify(t.servidor.alfa), servidorAntes, 'servidor sem a hora que falhou')
    assert.equal(getsJanelas(), 2, 'releu as janelas')
    assert.equal(t.s.mapaPronto.value, true)
    for (const k of ['2-10', '2-11', '2-12']) assert.ok(!t.s.scheduleSet.value.has(k), `tela sem ${k}`)
    assert.equal(t.s.scheduleSet.value.size, 8, 'tela = servidor')
    assert.equal(t.s.salvando.value, 0)
    // Depois de reler, o clique novo sai normal, montado em cima do servidor.
    const d = t.s.toggleScheduleCell(2, 11)
    await esperarTudo()
    assert.equal(puts(t.calls).length, 2)
    const corpo = puts(t.calls)[1].opts.body.map((x) => `${x.day_of_week}-${x.start_hour}`)
    assert.ok(corpo.includes('2-11') && !corpo.includes('2-10'), 'o clique novo não leva a hora que falhou')
    fila[1].h.resolve(fila[1].salvar())
    await d
    assert.deepEqual(t.servidor.alfa.map((x) => `${x.day_of_week}-${x.start_hour}`).sort(),
      [...t.s.scheduleSet.value].sort(), 'tela = servidor')
    assert.equal(t.toastLog.length, 1)
  }

  // Releitura com PUT na fila: o GET só sai depois que a fila esvazia (senão lê o
  // servidor de antes do clique) e, enquanto espera, a grade não aceita clique.
  {
    const fila = []
    const t = await montar({ put: (id, body, salvar) => { const h = segurada(); fila.push({ h, salvar }); return h.p } })
    const gets = () => urls(t.calls).filter((u) => u.startsWith('GET /api/marketing/schedules/')).length
    const p = t.s.toggleScheduleCell(2, 10)
    await esperarTudo()
    const antes = gets()
    t.s.escolherConta('beta')
    await esperarTudo()
    t.s.escolherConta('alfa')
    await esperarTudo()
    assert.equal(gets(), antes, 'nenhum GET com PUT na fila')
    assert.equal(t.s.mapaPronto.value, false, 'esqueleto enquanto espera')
    await t.s.toggleScheduleCell(3, 3)
    assert.equal(puts(t.calls).length, 1, 'sem clique enquanto relê')
    fila[0].h.resolve(fila[0].salvar())
    await p
    await esperarTudo()
    assert.deepEqual(urls(t.calls).slice(-2), ['GET /api/marketing/schedules/alfa', 'GET /api/marketing/schedules/alfa/heatmap'])
    assert.equal(t.s.mapaPronto.value, true)
    assert.ok(t.s.scheduleSet.value.has('2-10'), 'o clique salvo continua na tela')
    assert.deepEqual(t.servidor.alfa.map((x) => `${x.day_of_week}-${x.start_hour}`).sort(),
      [...t.s.scheduleSet.value].sort(), 'tela = servidor')
  }

  // Trocou de conta com o mapa da outra ainda vindo: a resposta velha não escreve.
  {
    const lenta = segurada()
    const t = await montar({ schedules: { ...SCHEDULES, alfa: () => lenta.p } })
    assert.equal(t.s.mapaPronto.value, false)
    const html = await renderizar(t)
    assert.ok(html.includes('aria-busy="true"'), 'esqueleto enquanto carrega')
    assert.ok(!html.includes('size-6 rounded'), 'sem grade de outra conta')
    t.s.escolherConta('beta')
    await esperarTudo()
    lenta.resolve(SCHEDULES.alfa)
    await esperarTudo()
    assert.equal(t.s.contaId.value, 'beta')
    assert.deepEqual([...t.s.scheduleSet.value], ['1-9'], 'mapa da beta')
    // Clique antes do mapa chegar não manda nada (não sabe o que já está ligado).
    const lenta2 = segurada()
    const t2 = await montar({ schedules: { alfa: () => lenta2.p } })
    await t2.s.toggleScheduleCell(0, 1)
    assert.equal(puts(t2.calls).length, 0)
  }

  // Trocou de marketplace com a lista da Shopee ainda vindo: a lista velha não escreve.
  {
    const lenta = segurada()
    const t = await montar({ contas: { ...CONTAS, shopee: () => lenta.p } })
    t.props.plataforma = 'ml'
    await esperarTudo()
    lenta.resolve(CONTAS.shopee)
    await esperarTudo()
    assert.deepEqual(t.s.contas.value.map((c) => c.id), ['ml2', 'ml1'])
    assert.equal(t.s.contaId.value, 'ml2')
  }

  // Nenhuma conta: diz isso, não pede mapa; o aviso da Shopee continua.
  {
    const t = await montar({ contas: { shopee: [], ml: [] } })
    assert.deepEqual(urls(t.calls), ['GET /api/marketing/accounts?platform=shopee'])
    const html = await renderizar(t)
    assert.ok(html.includes('Nenhuma conta de Ads da Shopee no DaVinci ainda.'))
    assert.ok(!html.includes('<select'), 'sem seletor vazio')
    assert.ok(html.includes('robô do Mac'))
    const tm = await montar({ plataforma: 'ml', contas: { ml: [] } })
    assert.ok((await renderizar(tm)).includes('Nenhuma conta de Ads do Mercado Livre no DaVinci ainda.'))
  }

  // Erro nas contas: aviso com "Tentar de novo", que funciona.
  {
    let falhar = true
    const t = await montar({
      contasErro: (p) => (falhar ? Promise.reject({ statusCode: 500, message: 'boom' }) : Promise.resolve(CONTAS[p])),
    })
    assert.equal(t.s.erroContas.value, 'boom')
    let html = await renderizar(t)
    assert.ok(html.includes('Não consegui carregar as contas de Ads (boom).'))
    assert.ok(html.includes('Tentar de novo'))
    falhar = false
    await t.s.carregarContas()
    await esperarTudo()
    assert.equal(t.s.erroContas.value, null)
    assert.equal(t.s.contaId.value, 'alfa')
    html = await renderizar(t)
    assert.ok(html.includes('size-6 rounded'), 'grade depois do "Tentar de novo"')
  }

  // Erro no mapa: aviso com o nome da conta e "Tentar de novo".
  {
    let falhar = true
    const t = await montar({
      mapaErro: (id, qual) => (falhar
        ? Promise.reject({ statusCode: 404, data: { detail: { code: 'account_not_found' } } })
        : Promise.resolve(qual === 'heatmap' ? HEATMAPS[id] : SCHEDULES[id])),
    })
    assert.equal(t.s.erroMapa.value, 'account_not_found')
    const html = await renderizar(t)
    assert.ok(html.includes('Não consegui carregar os horários de alfa celulares (account_not_found).'))
    await t.s.toggleScheduleCell(0, 1)
    assert.equal(puts(t.calls).length, 0, 'sem mapa, sem clique')
    falhar = false
    await t.s.carregarMapa()
    assert.equal(t.s.mapaPronto.value, true)
    assert.equal(t.s.scheduleSet.value.size, 8)
  }

  terminou = true
  console.log('marketing-horarios-sfc: ok')
}

// Uma promessa segurada que nunca chega (um PUT a mais que o teste não esperava)
// esvazia o event loop e o node sai com 0 sem terminar: isso é falha.
let terminou = false
process.on('exit', (code) => {
  if (!terminou && code === 0) {
    console.error('marketing-horarios-sfc: parou no meio (promessa que nunca resolveu)')
    process.exitCode = 1
  }
})
run().catch((e) => {
  console.error(e)
  process.exit(1)
})
