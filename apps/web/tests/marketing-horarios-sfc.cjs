// Run from apps/web: node tests/marketing-horarios-sfc.cjs
//
// Marketing › Conferência › Horários dos anúncios (08/10/2026):
// components/MarketingHorarios.vue — o heatmap de horários que ficou das abas
// Mercado Livre e Shopee (removidas a pedido do dono), agora embaixo do
// relatório da Conferência do mesmo marketplace.
//
// Trava o que a tela promete:
//  - lê as contas de Ads do marketplace (/horarios/contas?platform=), em ordem
//    A–Z, e abre no ?horario= do link, senão na última vista nesse marketplace,
//    senão na primeira do segmento;
//  - 13/10/2026: os botões Mala | Celular | Eletro e a lista das contas do
//    segmento (clique abre o mapa), com o saldo de Ads só na Shopee (R$ pt-BR,
//    0 em vermelho, "sem leitura") e o rodapé "saldo lido em…"; Eletro com o
//    aviso de que as contas também estão em Celular e os vazios (sem relatório /
//    ninguém vendeu eletro); ?seg= no link e o segmento lembrado por marketplace;
//    quando a lembrança abre outra coisa que o link abriria (volta de outro
//    marketplace / da Amazon / de outra aba), o link passa a dizer o que está na
//    tela (F5 e link copiado abrem o mesmo);
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
  '`/api/marketing/horarios/contas?platform=${p}`',
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
assert.ok(tpl.includes('— escolha o segmento e clique na conta'), 'subtítulo')
assert.ok(tpl.includes('clique nas horas para ligar/desligar'), 'dica em cima da grade')
assert.ok(!script.includes('/api/marketing/accounts'), 'a lista vem de /horarios/contas (com segmento e saldo)')
assert.ok(tpl.includes('As contas aparecem também em Celular; o horário é um só por conta.'), 'aviso do Eletro')
assert.ok(tpl.includes('Sem relatório da Conferência para saber quem vende eletro.'), 'Eletro sem relatório')
// Lista à esquerda (290px) no computador; no celular, em cima do mapa.
assert.match(tpl, /class="flex flex-col gap-4 md:flex-row md:items-start"/)
assert.match(tpl, /md:w-\[290px\]/)
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
  SEGMENTOS, ROTULO_SEGMENTO, segmento, lista, escolherSegmento, eletroExecucao, saldoLido, fmtSaldo, classeSaldo,
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

// Contas de mentira (os ids são uuid na vida real; aqui, legíveis). Mala:
// alfa; Celular: beta e zeta; Eletro: beta (vende eletro, é de Celular).
const CONTAS = {
  shopee: [
    // espaço + invisível na frente (apelido do ML); saldo zerado
    { id: 'zeta', name: ' \u200bZeta  Celulares', segmento: 'celular', eletro: false, acos_target: 10, saldo: 0, saldo_em: '2026-10-06T18:40:00Z' },
    { id: 'alfa', name: 'alfa malas', segmento: 'mala', eletro: false, acos_target: 10, saldo: 3446.72, saldo_em: '2026-10-06T18:05:00Z' },
    { id: 'beta', name: 'Beta Eletro', segmento: 'celular', eletro: true, acos_target: 8, saldo: null, saldo_em: null },
  ],
  ml: [
    // O ML não tem saldo (cobra depois): mesmo que viesse, a tela não mostra.
    { id: 'ml2', name: 'Mercado Dois', segmento: 'celular', eletro: false, acos_target: 12, saldo: 999, saldo_em: '2026-10-06T18:05:00Z' },
    { id: 'ml1', name: 'Mercado Um', segmento: 'mala', eletro: false, acos_target: 12 },
  ],
}
const ELETRO = { shopee: { id: 'ex1', gerado_em: '2026-10-06T19:00:00Z' }, ml: null }
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
  put, nuxt = new Map(), contasErro, mapaErro, eletro = ELETRO,
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
    const acc = /^\/api\/marketing\/horarios\/contas\?platform=(\w+)$/.exec(url)
    if (m === 'GET' && acc) {
      const corpo = (l) => ({ plataforma: acc[1], contas: JSON.parse(JSON.stringify(l)), eletro_execucao: eletro[acc[1]] ?? null })
      if (contasErro) return contasErro(acc[1]).then(corpo)
      const l = contas[acc[1]] ?? []
      return typeof l === 'function' ? l().then(corpo) : Promise.resolve(corpo(l))
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

/** As linhas da lista renderizada: nome, saldo (texto, NBSP → espaço), classe do saldo e se está aberta. */
function linhas(html) {
  return [...html.matchAll(/<li class="border-b last:border-b-0"><button type="button" class="([^"]*)"[^>]*>(.*?)<\/button><\/li>/g)].map((m) => {
    const nome = /<span class="min-w-0 truncate">([^<]*)<\/span>/.exec(m[2])?.[1] ?? null
    const sal = /<span class="shrink-0 tabular-nums ([^"]*)">([^<]*)<\/span>/.exec(m[2])
    return { nome, saldo: sal ? sal[2].replace(/\u00a0/g, ' ') : null, classe: sal ? sal[1] : null, aberta: m[1].includes('border-l-blue-600') }
  })
}

/** A tela inteira, renderizada (SSR) com o estado do componente. */
function renderizar(t) {
  const estado = { ...t.s, plataforma: Vue.computed(() => t.props.plataforma) }
  const app = Vue.createSSRApp({ setup: () => estado, render })
  for (const n of ICONES) app.component(n, { render: () => Vue.h('svg', { 'data-icone': n }) })
  return renderToString(app)
}

async function run() {
  // Shopee: contas A–Z, abre em Mala na primeira, lê janelas e heatmap dela.
  {
    const t = await montar()
    const { s } = t
    assert.deepEqual(s.contas.value.map((c) => c.id), ['alfa', 'beta', 'zeta'], 'contas em ordem A–Z (sem ligar pra maiúscula)')
    assert.equal(s.contas.value[2].name, 'Zeta Celulares', 'nome limpo (espaço e invisível)')
    assert.equal(s.segmento.value, 'mala', 'sem link nem lembrança: Mala')
    assert.equal(s.contaId.value, 'alfa', 'abre na primeira do segmento')
    assert.deepEqual(urls(t.calls), [
      'GET /api/marketing/horarios/contas?platform=shopee',
      'GET /api/marketing/schedules/alfa',
      'GET /api/marketing/schedules/alfa/heatmap',
    ])
    assert.equal(s.mapaPronto.value, true)
    assert.deepEqual(t.routerCalls, [], 'abrir não escreve na URL')
    assert.equal(t.nuxt.get('marketing:horarios-conta').value.shopee, 'alfa', 'lembra a conta vista')
    assert.equal(t.nuxt.get('marketing:horarios-segmento').value.shopee, 'mala', 'lembra o segmento visto')

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

    // A tela: 7 × 24 botões, título, subtítulo, legenda com o alvo, a lista do segmento e o aviso da Shopee.
    const html = await renderizar(t)
    assert.equal((html.match(/<button type="button" class="block size-6 rounded/g) || []).length, 7 * 24, '168 células')
    assert.equal((html.match(/aria-pressed="true"/g) || []).length, 8, '8 horas ligadas')
    for (const d of ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']) assert.ok(html.includes(`>${d}</th>`), `linha ${d}`)
    assert.ok(html.includes('Horários dos anúncios'))
    assert.ok(html.includes('— escolha o segmento e clique na conta'))
    assert.ok(html.includes('clique nas horas para ligar/desligar'))
    assert.ok(html.includes('ACOS &lt; 10.0%'), 'legenda com o alvo')
    assert.ok(html.includes('ACOS médio (até 15.0%)'))
    for (const l of ['ACOS ruim', 'Ligado, sem dados', 'Desligado']) assert.ok(html.includes(l), `legenda: ${l}`)
    assert.ok(html.includes('Os anúncios da Shopee são ligados e desligados pelo robô do Mac seguindo este mapa. Ele relê os horários de 5 em 5 minutos.'), 'aviso do robô na Shopee')
    assert.ok(!html.includes('<select'), 'sem o seletor antigo')
    assert.deepEqual(linhas(html).map((l) => l.nome), ['alfa malas'], 'a lista de Mala')
    assert.ok(html.includes('Contas de Mala (1)'))
    assert.equal((html.match(/aria-selected="true"/g) || []).length, 1, 'um segmento marcado')
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
    assert.equal(s.segmento.value, 'celular', 'conta de outro segmento: vai pro dela')
    assert.deepEqual(t.routerCalls.at(-1), { query: { horario: 'beta', seg: 'celular' } })
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
    assert.equal(t1.s.segmento.value, 'celular', 'o segmento da conta do link')
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
    assert.ok(urls(t.calls).includes('GET /api/marketing/horarios/contas?platform=ml'))
    assert.deepEqual(urls(t.calls).slice(-2), ['GET /api/marketing/schedules/ml2', 'GET /api/marketing/schedules/ml2/heatmap'])
    // (Na tela de verdade a Conferência tira o ?seg/?horario da Shopee na troca;
    // aqui o link ficou com eles: um ?seg= no link vale.)
    assert.equal(t.s.segmento.value, 'celular', 'sem lembrança no ML: o ?seg= que estiver no link')
    const htmlMl = await renderizar(t)
    assert.ok(!htmlMl.includes('robô do Mac'), 'ML sem o aviso do robô')
    // ML: nada de saldo (o ML cobra depois), mesmo que a API mandasse.
    for (const fora of ['Saldo Ads', 'sem leitura', 'saldo lido', '999']) assert.ok(!htmlMl.includes(fora), `ML sem: ${fora}`)
    assert.equal(t.s.saldoLido.value, null)
    assert.deepEqual(linhas(htmlMl), [{ nome: 'Mercado Dois', saldo: null, classe: null, aberta: true }])
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
    assert.ok(!html.includes('clique nas horas'))
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
    assert.deepEqual([t.s.segmento.value, t.s.contaId.value], ['mala', 'ml1'], 'o ML abre em Mala (sem link nem lembrança)')
  }

  // Nenhuma conta: diz isso, não pede mapa; o aviso da Shopee continua.
  {
    const t = await montar({ contas: { shopee: [], ml: [] } })
    assert.deepEqual(urls(t.calls), ['GET /api/marketing/horarios/contas?platform=shopee'])
    const html = await renderizar(t)
    assert.ok(html.includes('Nenhuma conta de Ads da Shopee no DaVinci ainda.'))
    assert.ok(!html.includes('<select'), 'sem seletor vazio')
    assert.ok(!html.includes('role="tablist"'), 'sem os botões de segmento')
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
    assert.ok(html.includes('Não consegui carregar os horários de alfa malas (account_not_found).'))
    await t.s.toggleScheduleCell(0, 1)
    assert.equal(puts(t.calls).length, 0, 'sem mapa, sem clique')
    falhar = false
    await t.s.carregarMapa()
    assert.equal(t.s.mapaPronto.value, true)
    assert.equal(t.s.scheduleSet.value.size, 8)
  }


  // Segmentos: Mala → Celular → Eletro. Trocar mantém a conta aberta se ela é do
  // segmento, senão abre a primeira; o link e a lembrança acompanham.
  {
    const nuxt = new Map()
    const t = await montar({ nuxt })
    const { s } = t
    assert.deepEqual(s.SEGMENTOS.map((x) => x.rotulo), ['Mala', 'Celular', 'Eletro'])
    s.escolherSegmento('celular')
    await esperarTudo()
    assert.deepEqual(s.lista.value.map((c) => c.id), ['beta', 'zeta'], 'Celular A–Z')
    assert.equal(s.contaId.value, 'beta', 'a aberta (Mala) não é de Celular: a primeira')
    assert.deepEqual(t.routerCalls.at(-1), { query: { seg: 'celular', horario: 'beta' } })
    assert.deepEqual(urls(t.calls).slice(-2), ['GET /api/marketing/schedules/beta', 'GET /api/marketing/schedules/beta/heatmap'])
    let html = await renderizar(t)
    assert.ok(html.includes('Contas de Celular (2)'))
    assert.ok(!html.includes('As contas aparecem também em Celular'), 'o aviso é só do Eletro')
    assert.deepEqual(linhas(html).map((l) => [l.nome, l.aberta]), [['Beta Eletro', true], ['Zeta Celulares', false]], 'a aberta destacada')
    assert.match(html, /<h3 class="text-sm font-semibold">Beta Eletro<\/h3>/, 'o nome da conta em cima da grade')
    s.escolherConta('zeta')
    await esperarTudo()
    s.escolherSegmento('eletro')
    await esperarTudo()
    assert.deepEqual(s.lista.value.map((c) => c.id), ['beta'])
    assert.equal(s.contaId.value, 'beta')
    s.escolherConta('beta')
    await esperarTudo()
    s.escolherSegmento('celular')
    await esperarTudo()
    assert.equal(s.contaId.value, 'beta', 'beta é de Eletro e de Celular: fica aberta')
    s.escolherSegmento('eletro')
    await esperarTudo()
    html = await renderizar(t)
    assert.ok(html.includes('Contas de Eletro (1)'))
    assert.ok(html.includes('As contas aparecem também em Celular; o horário é um só por conta.'))
    assert.equal(nuxt.get('marketing:horarios-segmento').value.shopee, 'eletro')
    const getsAntes = t.calls.length
    s.escolherSegmento('eletro')
    assert.equal(t.calls.length, getsAntes, 'o mesmo segmento não faz nada')
    // Voltar (componente novo, mesma sessão): o segmento e a conta lembrados,
    // e o link (que voltou limpo) passa a dizer isso.
    const t2 = await montar({ nuxt })
    assert.equal(t2.s.segmento.value, 'eletro')
    assert.equal(t2.s.contaId.value, 'beta')
    assert.deepEqual(t2.routerCalls, [{ query: { seg: 'eletro', horario: 'beta' } }])
  }

  // Saldo de Ads (Shopee): R$ pt-BR, 0 em vermelho e negrito, sem leitura apagado;
  // em cima da grade também; rodapé com a hora da leitura (Brasília).
  {
    const t = await montar({ query: { seg: 'celular' } })
    assert.equal(t.s.segmento.value, 'celular')
    let html = await renderizar(t)
    assert.ok(html.includes('Saldo Ads</span>'), 'a coluna Saldo Ads')
    assert.deepEqual(linhas(html), [
      { nome: 'Beta Eletro', saldo: 'sem leitura', classe: 'text-xs text-muted-foreground', aberta: true },
      { nome: 'Zeta Celulares', saldo: 'R$ 0,00', classe: 'font-semibold text-red-600 dark:text-red-400', aberta: false },
    ])
    assert.equal(t.s.saldoLido.value, 'saldo lido em 06/10 às 15:40', 'só as leituras da lista')
    assert.ok(html.includes('saldo lido em 06/10 às 15:40'))
    assert.match(html, /Saldo Ads <span class="tabular-nums text-xs text-muted-foreground">sem leitura<\/span>/, 'em cima da grade')
    t.s.escolherSegmento('mala')
    await esperarTudo()
    html = await renderizar(t)
    assert.deepEqual(linhas(html).map((l) => [l.saldo, l.classe]), [['R$ 3.446,72', 'text-foreground']])
    assert.match(html.replace(/\u00a0/g, ' '), /Saldo Ads <span class="tabular-nums text-foreground">R\$ 3\.446,72<\/span>/)
    assert.equal(t.s.saldoLido.value, 'saldo lido em 06/10 às 15:05')
    assert.equal(t.s.fmtSaldo(1883.79).replace(/\u00a0/g, ' '), 'R$ 1.883,79')

    // Leituras em horas diferentes do mesmo dia; e em dias diferentes.
    const mesmoDia = [
      { id: 'a', name: 'A', segmento: 'mala', eletro: false, acos_target: 7, saldo: 1, saldo_em: '2026-10-06T18:05:00Z' },
      { id: 'b', name: 'B', segmento: 'mala', eletro: false, acos_target: 7, saldo: 2, saldo_em: '2026-10-06T18:40:00Z' },
      { id: 'c', name: 'C', segmento: 'mala', eletro: false, acos_target: 7, saldo: null, saldo_em: null },
    ]
    const t2 = await montar({ contas: { shopee: mesmoDia } })
    assert.equal(t2.s.saldoLido.value, 'saldo lido em 06/10 às 15:05–15:40')
    const t3 = await montar({ contas: { shopee: [...mesmoDia, { ...mesmoDia[0], id: 'd', name: 'D', saldo_em: '2026-10-03T13:00:00Z' }] } })
    assert.equal(t3.s.saldoLido.value, 'saldo lido em 03/10–06/10')
    // 02:30 UTC de 07/10 ainda é 06/10 em Brasília.
    const t4 = await montar({ contas: { shopee: [{ ...mesmoDia[0], saldo_em: '2026-10-07T02:30:00Z' }] } })
    assert.equal(t4.s.saldoLido.value, 'saldo lido em 06/10 às 23:30')
    // Ninguém com leitura: sem rodapé.
    const t5 = await montar({ contas: { shopee: [mesmoDia[2]] } })
    assert.equal(t5.s.saldoLido.value, null)
    assert.ok(!(await renderizar(t5)).includes('saldo lido'))
  }

  // Eletro vazio: sem relatório da Conferência / relatório sem ninguém de eletro.
  // Sem conta no segmento, o link perde o ?horario= e nada de mapa.
  {
    const semEletro = { shopee: CONTAS.shopee.map((c) => ({ ...c, eletro: false })) }
    const t = await montar({ contas: semEletro, eletro: { shopee: null } })
    t.s.escolherSegmento('eletro')
    await esperarTudo()
    assert.equal(t.s.contaId.value, null)
    assert.deepEqual(t.routerCalls.at(-1), { query: { seg: 'eletro' } })
    let html = await renderizar(t)
    assert.ok(html.includes('Contas de Eletro (0)'))
    assert.ok(html.includes('Sem relatório da Conferência para saber quem vende eletro.'))
    assert.ok(!html.includes('size-6 rounded'), 'sem grade')
    assert.ok(!html.includes('As contas aparecem também em Celular'))
    assert.ok(!html.includes('Escolha uma conta na lista.'))
    const t2 = await montar({ contas: semEletro, query: { seg: 'eletro' } })
    html = await renderizar(t2)
    assert.ok(html.includes('Nenhuma conta vendeu nem anunciou eletro nas 4 semanas do último relatório da Conferência.'))
    assert.deepEqual(urls(t2.calls), ['GET /api/marketing/horarios/contas?platform=shopee'], 'sem conta, sem mapa')
    // Volta pra Celular: abre a primeira de lá.
    t2.s.escolherSegmento('celular')
    await esperarTudo()
    assert.equal(t2.s.contaId.value, 'beta')
  }

  // ?seg= do link: com a conta do link, só vale se ela for do segmento.
  {
    const casos = [
      [{ seg: 'eletro' }, 'eletro', 'beta'],
      [{ horario: 'beta', seg: 'eletro' }, 'eletro', 'beta'],
      [{ horario: 'beta', seg: 'mala' }, 'celular', 'beta'],
      [{ horario: 'zeta', seg: 'eletro' }, 'celular', 'zeta'],
      [{ seg: 'lixo' }, 'mala', 'alfa'],
      [{ seg: ['celular', 'mala'] }, 'mala', 'alfa'],
    ]
    for (const [query, seg, id] of casos) {
      const t = await montar({ query })
      assert.deepEqual([t.s.segmento.value, t.s.contaId.value], [seg, id], JSON.stringify(query))
      assert.deepEqual(t.routerCalls, [], 'abrir não escreve na URL')
    }
    // Lembrança de outro segmento vence o ?seg= de outro marketplace — e o link
    // passa a dizer o que está na tela.
    const nuxt = new Map([['marketing:horarios-segmento', Vue.ref({ shopee: 'celular' })]])
    const t = await montar({ nuxt, query: { seg: 'mala' } })
    assert.deepEqual([t.s.segmento.value, t.s.contaId.value], ['celular', 'beta'])
    assert.deepEqual(t.routerCalls, [{ query: { seg: 'celular', horario: 'beta' } }])
    // Lembrança que bate com o link: nada a escrever.
    const t2 = await montar({ nuxt, query: { seg: 'celular', horario: 'beta' } })
    assert.deepEqual(t2.routerCalls, [])
  }

  // Tela e link andam juntos depois de trocar de marketplace (achado da revisão):
  // Shopee → ML → Celular no ML → volta pra Shopee. A tela volta na lembrada e
  // o link abre o mesmo; F5 (sessão nova) abre igual.
  {
    // (a) Como na Conferência de verdade: a troca tira ?horario/?seg do outro.
    // Shopee em Celular/zeta → ML (abre no dele, Mala) → Celular → volta.
    const nuxt = new Map()
    const t = await montar({ nuxt })
    t.s.escolherConta('zeta')
    await esperarTudo()
    assert.deepEqual(t.route.query, { horario: 'zeta', seg: 'celular' })
    t.route.query = { conf: 'ml' }
    t.props.plataforma = 'ml'
    await esperarTudo()
    assert.deepEqual([t.s.segmento.value, t.s.contaId.value], ['mala', 'ml1'], 'o ML abre no dele (Mala), não no ?seg da Shopee')
    assert.deepEqual(t.route.query, { conf: 'ml' }, 'sem lembrança no ML: nada a escrever')
    t.s.escolherSegmento('celular')
    await esperarTudo()
    assert.deepEqual(t.route.query, { conf: 'ml', seg: 'celular', horario: 'ml2' })
    t.route.query = {}
    t.props.plataforma = 'shopee'
    await esperarTudo()
    assert.deepEqual([t.s.segmento.value, t.s.contaId.value], ['celular', 'zeta'], 'a Shopee volta na lembrada')
    assert.deepEqual(t.route.query, { seg: 'celular', horario: 'zeta' }, 'o link diz o que está na tela')
    const f5 = await montar({ query: t.route.query })
    assert.deepEqual([f5.s.segmento.value, f5.s.contaId.value], ['celular', 'zeta'], 'F5 abre o mesmo')
    assert.deepEqual(f5.routerCalls, [])
    // Lembrada = o que o link limpo já abre (Mala/alfa): nada a escrever.
    const nuxtA = new Map()
    const ta = await montar({ nuxt: nuxtA })
    ta.props.plataforma = 'ml'
    await esperarTudo()
    ta.route.query = {}
    ta.props.plataforma = 'shopee'
    await esperarTudo()
    assert.deepEqual([ta.s.segmento.value, ta.s.contaId.value], ['mala', 'alfa'])
    assert.deepEqual(ta.routerCalls, [])

    // (b) Link que ficou com a conta e o segmento do ML: corrigido do mesmo jeito.
    const nuxt2 = new Map()
    const u = await montar({ nuxt: nuxt2 })
    u.s.escolherConta('alfa')
    await esperarTudo()
    u.props.plataforma = 'ml'
    await esperarTudo()
    u.s.escolherSegmento('celular')
    await esperarTudo()
    assert.deepEqual(u.route.query, { horario: 'ml2', seg: 'celular' })
    u.props.plataforma = 'shopee'
    await esperarTudo()
    assert.deepEqual([u.s.segmento.value, u.s.contaId.value], ['mala', 'alfa'])
    assert.deepEqual(u.route.query, { horario: 'alfa', seg: 'mala' })
    const f5b = await montar({ query: u.route.query })
    assert.deepEqual([f5b.s.segmento.value, f5b.s.contaId.value], ['mala', 'alfa'])

    // (c) Lembrado um segmento sem conta: o link fica só com o ?seg=.
    const semEletro = { ...CONTAS, shopee: CONTAS.shopee.map((c) => ({ ...c, eletro: false })) }
    const nuxt3 = new Map([['marketing:horarios-segmento', Vue.ref({ shopee: 'eletro' })]])
    const v = await montar({ nuxt: nuxt3, contas: semEletro, query: { horario: 'ml2', aba: 'conferencia' } })
    assert.deepEqual([v.s.segmento.value, v.s.contaId.value], ['eletro', null])
    assert.deepEqual(v.routerCalls, [{ query: { aba: 'conferencia', seg: 'eletro' } }])
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
