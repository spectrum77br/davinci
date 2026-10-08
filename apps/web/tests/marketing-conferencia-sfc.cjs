// Run from apps/web: node tests/marketing-conferencia-sfc.cjs
//
// Marketing › Conferência (06/10/2026; ML e Amazon desde 07/10/2026):
// components/MarketingConferencia.vue, a aba em pages/marketing.vue e o contexto do Threema.
//
// Trava o que a tela promete:
//  - abre no último relatório PRONTO, ou no ?execucao= do link do Threema
//    (mesmo uma execução mais velha que as 30 da lista);
//  - enquanto coleta, relê a cada 20 s — só com a aba visível e montada — e,
//    quando termina, avisa e mostra o relatório;
//  - "Gerar agora" pede confirmação, manda o tipo e trata o 409 de "já tem uma
//    coletando" levando a pessoa até ela;
//  - os arquivos saem com o nome do servidor; o HTML abre numa aba nova;
//  - o Resumo é a planilha antiga do dono (07/10/2026): métrica × (semana ×
//    Mala/Celular/Eletro/Geral), semanas da mais velha pra mais nova, Variação
//    no fim, as duas colunas de rótulo paradas; tudo de lib/conferencia.ts;
//  - conta sem dados continua contada (aviso embaixo da tabela);
//  - só quem edita gera, recalcula, cancela e mexe nas contas; o cadastro do
//    Threema é só de admin (routers/informar.py);
//  - um relatório por marketplace (07/10/2026): Shopee | Mercado Livre | Amazon
//    em cima, ?conf= na URL, ?plataforma= na API, o relatório aberto por link
//    leva a tela pro marketplace dele, e o que o marketplace não dá aparece
//    como "não coletado" / "não se aplica" / "aguardando acesso" numa pílula.
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

// ---------------------------------------------------------------- SFCs compilam
function compilar(rel) {
  const filename = path.join(__dirname, '..', rel)
  const source = fs.readFileSync(filename, 'utf8')
  const { descriptor, errors } = parse(source, { filename })
  assert.deepEqual(errors, [], `${rel}: SFC parseia`)
  const id = `${path.basename(rel, '.vue')}-check`
  const compilado = compileTemplate({ source: descriptor.template.content, filename, id })
  assert.deepEqual(compilado.errors, [], `${rel}: template compila`)
  // O render compilado precisa ser JS válido (imports do vue resolvem via require).
  new Function('exports', 'require', transpile(compilado.code))({}, require)
  // O <script setup> também: macro, import e tipo passam pelo compilador.
  compileScript(descriptor, { id })
  return { script: descriptor.scriptSetup.content, tpl: descriptor.template.content }
}
const tela = compilar('components/MarketingConferencia.vue')
const pagina = compilar('pages/marketing.vue')
const modal = compilar('components/InformarThreemaModal.vue')

// ---------------------------------------------------------------- higiene estática
const { script, tpl } = tela
const BASE = '/api/marketing/conferencia-shopee'
assert.match(script, /const BASE = '\/api\/marketing\/conferencia-shopee'/, 'prefixo do router')
for (const trecho of [
  '`${BASE}/execucoes?limite=30&plataforma=${p}`',
  '`${BASE}/execucoes/${encodeURIComponent(id)}`',
  '`${BASE}/execucoes?plataforma=${p}`, {\n      method: \'POST\', body: { tipo: tipoNovo.value, plataforma: p },',
  '/recalcular`, { method: \'POST\' }',
  '/cancelar`, { method: \'POST\' }',
  '/arquivo/${fmt}`',
  '`${BASE}/contas?plataforma=${p}`',
  '`${BASE}/contas/${encodeURIComponent(c.id)}`, { method: \'PUT\', body: campos }',
]) assert.ok(script.includes(trecho), `endpoint: ${trecho}`)
assert.match(script, /useCan\('marketing', 'edit'\)/, 'escrita atrás de marketing:edit')
assert.match(script, /INTERVALO_ACOMPANHAMENTO = 20_000/, 'relê a cada 20 s')
assert.match(script, /visibilitychange/, 'para quando a aba some')
assert.match(script, /onBeforeUnmount\(\(\) => \{\s*desmontado = true\s*window\.clearTimeout\(timer\)/, 'desmontou, para')
assert.ok(!/localStorage|sessionStorage|document\.cookie/.test(script + tpl), 'sem armazenamento do navegador')
// Classes de cor por extenso no componente (lib/ não é varrido pelo Tailwind).
assert.match(script, /verde: 'text-emerald-600 dark:text-emerald-400'/)
assert.match(script, /vermelho: 'text-red-600 dark:text-red-400'/)
// Escrita só com edit; Threema só admin.
assert.match(tpl, /<template v-if="canEdit">[\s\S]{0,900}Gerar agora/, 'Gerar agora só com edit')
assert.match(tpl, /<Button v-if="isAdmin"[^>]*>\s*<Bell[^>]*\/> Quem recebe no Threema/, 'Threema só admin')
assert.match(tpl, /<section v-if="canEdit" class="rounded-xl border bg-card">/, 'Contas só com edit')
assert.match(tpl, /contexto="conferencia_shopee"\s+somente-cadastro/, 'modal do Threema só cadastro')
assert.match(modal.script, /\| 'conferencia_shopee'/, 'contexto no union do modal')
// Um relatório por marketplace (07/10/2026): o seletor em cima, ?conf= na URL.
assert.match(tpl, /role="tablist" aria-label="Marketplace da conferência"/, 'seletor de marketplace')
assert.match(tpl, /v-for="p in PLATAFORMAS" :key="p\.chave"\s+role="tab" :aria-selected="plataforma === p\.chave"/)
assert.match(tpl, /@click="trocarPlataforma\(p\.chave\)"/)
assert.match(script, /const plataforma = ref<Plataforma>\(plataformaValida\(route\.query\.conf\) \?\? 'shopee'\)/, 'lê ?conf=')
// O mesmo cadastro do Threema serve os 3 (um aviso por marketplace).
assert.match(tpl, /contexto="conferencia_shopee"[\s\S]{0,200}descricao="[^"]*Shopee, Mercado Livre e Amazon/)
// Layout do relatório: SÓ o Resumo, no formato da planilha antiga do dono (pedido
// de 07/10/2026 — "mais ou menos desse jeito"), igual ao Excel e ao HTML.
for (const trecho of [
  'tituloRelatorio(rel.semanas, plataformaAberta)', 'comparadoCom(rel.semanas)', 'gerado em', // título
  '<div v-if="planilha" class="planilha overflow-x-auto', // a planilha, com rolagem pro lado
  'v-for="(l, k) in planilha.linhas"', 'Métrica',
  'Sem dados:', 'Afiliados incompletos:', // avisos que mudam a leitura
  'Nenhum relatório {{ plat.da }} ainda', // estado vazio
  'Não consegui carregar a conferência agora.', // erro
  'animate-pulse', // esqueleto
  'Contas da conferência',
]) assert.ok(tpl.includes(trecho), `na tela: ${trecho}`)
assert.match(script, /const planilha = computed\(\(\) => planilhaResumo\(rel\.value\)\)/, 'planilha sai da lib')
for (const fora of [
  'v-for="c in cartoes"', 'Por conta', 'v-for="g in tabelas"', 'Gasto de Ads sem produto',
  'quatroSemanas', 'vs semana anterior', 'vs média 3 sem.',
  // o Excel também é só o Resumo desde d5ab3ba2: não tem detalhe por loja pra mandar ver
  'O detalhe por loja está no Excel',
]) {
  assert.ok(!tpl.includes(fora) && !script.includes(fora), `não aparece mais na tela: ${fora}`)
}
// Só o Excel para baixar.
assert.match(script, /const FORMATOS_NA_TELA = FORMATOS\.filter\(\(f\) => f\.fmt === 'xlsx'\)/)
// Celular: tabelas largas sempre dentro de rolagem horizontal.
const tabelas = tpl.match(/<table /g).length
const rolagens = tpl.match(/(?:table-card|planilha)[^"]*overflow-x-auto|overflow-x-auto[^"]*table-card/g).length
assert.equal(rolagens, tabelas, 'toda tabela dentro de overflow-x-auto')
// Estilo de planilha: azul no cabeçalho, bege nos rótulos e no Geral, grade fina,
// as duas colunas de rótulo paradas, e o escuro com as mesmas cores (bege escurecido).
{
  const estilo = fs.readFileSync(path.join(__dirname, '../components/MarketingConferencia.vue'), 'utf8').split('<style scoped>')[1]
  assert.match(estilo, /--conf-azul: #1f3864;/)
  assert.match(estilo, /--conf-bege: #ddd9c4;/)
  assert.match(estilo, /--conf-grade: #bfbfbf;/)
  assert.match(estilo, /\n\.dark \.conferencia \{[^}]*--conf-azul: #1f3864;[^}]*--conf-bege: #[0-9a-f]{6};/, 'escuro mantém o azul e tem um bege próprio')
  // ":global(.dark) .conferencia" o Vue compila para só ".dark" (as variáveis claras do
  // .conferencia ganhavam e o escuro saía com célula branca e texto claro).
  assert.ok(!/:global\(\.dark\)/.test(estilo.replace(/\/\*[\s\S]*?\*\//g, '')), 'escuro sem :global(.dark) (vira só ".dark")')
  {
    const { compileStyle } = require('vue/compiler-sfc')
    const css = compileStyle({ source: estilo.split('</style>')[0], filename: 'x.vue', id: 'data-v-teste', scoped: true }).code
    assert.match(css, /\.dark \.conferencia\[data-v-teste\] \{[^}]*--conf-bege: #3b3829/, 'o escuro compila preso ao .conferencia')
  }
  assert.match(estilo, /\.planilha thead th \{[^}]*background: var\(--conf-azul\);[^}]*color: var\(--conf-azul-txt\);[^}]*font-weight: 700;/)
  assert.match(estilo, /\.planilha td\.geral \{[^}]*background: var\(--conf-bege\);/)
  assert.match(estilo, /\.planilha tbody th \{[^}]*background: var\(--conf-bege\);/)
  assert.match(estilo, /\.planilha \.rotulo-cab,\s*\.planilha \.cat,\s*\.planilha \.sub \{\s*position: sticky;/, 'colunas de rótulo paradas')
  assert.match(estilo, /\.planilha \.sub \{[^}]*left: var\(--conf-cat-w\);/, 'a 2ª para depois da 1ª')
  assert.match(estilo, /\.planilha \.cat \{[^}]*left: 0;[^}]*width: var\(--conf-cat-w\);/)
  // A 2ª coluna tem largura fixa (--conf-sub-w) e a data da semana gruda logo depois das
  // duas: no celular (375 px) a célula mesclada da semana passa da tela e a data sumia.
  assert.match(estilo, /\.planilha \.sub \{[^}]*width: var\(--conf-sub-w\);[^}]*max-width: var\(--conf-sub-w\);/)
  assert.match(estilo, /\.planilha \.semana-txt \{[^}]*position: sticky;[^}]*left: calc\(var\(--conf-cat-w\) \+ var\(--conf-sub-w\)[^}]*right: /)
  assert.match(estilo, /@media \(max-width: 639px\) \{\s*\.conferencia \{[^}]*--conf-sub-w: 6\.5rem;/)
  // a cor da variação vem das classes do Tailwind: o CSS da planilha não pinta texto de célula
  assert.ok(!/\.planilha td[^{]*\{[^}]*\bcolor:/.test(estilo), 'td da planilha sem color: (não apaga o verde/vermelho)')
}
// Cor fixa sempre com a variante do escuro.
for (const m of tpl.matchAll(/(?<!dark:)\btext-(?:emerald|red|amber)-\d00\b(?![^"]*dark:)/g)) {
  assert.fail(`cor sem dark: perto de "${tpl.slice(m.index - 40, m.index + 40)}"`)
}

// ---------------------------------------------------------------- página: as abas
// 08/10/2026: as abas Mercado Livre e Shopee (painéis de Ads) saíram; ficaram
// Conferência | Criativos | Roteiros | Desempenho, e a Conferência é a padrão.
{
  const s = pagina.script
  const t = pagina.tpl
  assert.match(s, /type Aba = 'conferencia' \| 'criativos' \| 'roteiros' \| 'desempenho'\n/, 'só as 4 abas')
  assert.match(s, /const canConferencia = useCan\('marketing', 'view'\)/, 'permissão da aba')
  assert.match(s, /const canCriativos = useCan\('marketing_criativos', 'view'\)/, 'permissão de Criativos')
  assert.match(s, /if \(!canCriativos\.value && !canConferencia\.value\) await navigateTo\('\/403'\)/, 'guarda 403')
  // ?aba= lido no setup e escrito na troca; ?execucao, ?conf e ?horario saem fora da Conferência.
  assert.match(s, /const aba = ref<Aba>\(abaDaUrl\(\) \?\? abaPadrao\(\)\)/, 'lê ?aba=, senão o padrão')
  assert.match(s, /if \(a !== 'conferencia'\) \{\s*delete query\.execucao\s*delete query\.conf\s*delete query\.horario\s*\}/, 'execucao, conf e horario só na aba')
  assert.match(s, /void router\.replace\(\{ query \}\)/, 'escreve a aba na URL')
  // Nada dos painéis de Ads sobrou na página (nem rede, nem polling, nem estado).
  // Comentários contam a história; o que vale é o código e a tela.
  const codigo = s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
  const tela = t.replace(/<!--[\s\S]*?-->/g, '')
  for (const fora of [
    'plataformaAds', '/api/marketing/metrics/summary', '/api/marketing/timeseries', '/api/marketing/credit-alerts',
    '/api/marketing/agent/status', '/api/marketing/trigger-all', '/schedule`', '/commands`', '/schedules/',
    'setInterval', 'summary', 'timeseries', 'heatmap', 'scheduleState', 'creditAlerts', 'agentPresence',
    'flashBusy', 'executorBadge', 'canAds', 'emOutraAba',
  ]) assert.ok(!codigo.includes(fora), `página sem: ${fora}`)
  for (const fora of [
    'Mercado Livre', 'Shopee', 'Heatmap', 'Agenda automática', 'Oferta Relâmpago', 'Alertas de crédito',
    'Evolução', 'Executor local', 'rodar ciclo agora', 'recarregar', "platform = p",
  ]) assert.ok(!tela.includes(fora), `aba removida não aparece: ${fora}`)
  // Um botão por aba, cada um com a permissão de antes; a Conferência primeiro.
  const botoes = [...t.matchAll(/<button v-if="(\w+)"[\s\S]*?@click="aba = '(\w+)'">/g)].map((m) => `${m[2]}:${m[1]}`)
  assert.deepEqual(botoes, ['conferencia:canConferencia', 'criativos:canCriativos', 'roteiros:canCriativos', 'desempenho:canCriativos'])
  assert.match(t, /<button v-if="canConferencia"[\s\S]{0,400}aba = 'conferencia'[\s\S]{0,200}<ClipboardCheck class="size-3\.5" \/>\s*Conferência\s*<\/button>/, 'botão da aba')
  assert.ok(!t.includes('Conferência Shopee'), 'a aba não diz mais só Shopee')
  assert.match(t, /<MarketingConferencia v-if="aba === 'conferencia' && canConferencia" \/>/, 'render da aba')
  assert.match(t, /<MarketingCriativos\s+v-else-if="aba === 'criativos' && canCriativos"\s+@abrir-roteiro="abrirRoteiro"/)
  assert.match(t, /<MarketingRoteiros\s+v-else-if="aba === 'roteiros' && canCriativos"\s+ref="roteirosEl"\s+:foco="focoRoteiro"/)
  assert.match(t, /<MarketingDesempenho v-else-if="aba === 'desempenho' && canCriativos" \/>/)
  assert.match(t, /<div v-if="canConferencia \|\| canCriativos" class="flex max-w-full">/, 'barra de abas pra quem vê alguma')
  // Celular: a barra de abas rola pro lado em vez de estourar a tela.
  assert.match(t, /class="flex w-fit max-w-full gap-1 overflow-x-auto rounded-md bg-muted\/40 p-1"/)

  // abaDaUrl e abaPadrao de verdade, com permissões falsas.
  const ini = s.indexOf('function abaDaUrl')
  const corpo = transpile(s.slice(ini, s.indexOf('const aba = ref')), ts.ModuleKind.ESNext)
  const aba = (q, perms) => new Function('route', 'canCriativos', 'canConferencia', `${corpo}; return abaDaUrl() ?? abaPadrao()`)(
    { query: q }, { value: !!perms.criativos }, { value: !!perms.conferencia },
  )
  assert.equal(aba({}, { conferencia: true, criativos: true }), 'conferencia', 'padrão de quem vê o Marketing')
  assert.equal(aba({}, { criativos: true }), 'criativos', 'padrão de quem só tem Criativos')
  assert.equal(aba({ aba: 'conferencia' }, { conferencia: true }), 'conferencia')
  assert.equal(aba({ aba: 'conferencia' }, { criativos: true }), 'criativos', 'sem permissão cai no padrão')
  assert.equal(aba({ aba: 'roteiros' }, { criativos: true }), 'roteiros')
  assert.equal(aba({ aba: 'roteiros' }, { conferencia: true }), 'conferencia', 'Roteiros sem permissão cai no padrão')
  assert.equal(aba({ aba: 'ml' }, { conferencia: true }), 'conferencia', 'link antigo do ML cai na Conferência')
  assert.equal(aba({ aba: 'shopee' }, { conferencia: true, criativos: true }), 'conferencia', 'link antigo da Shopee cai na Conferência')
  assert.equal(aba({ aba: 'shopee' }, { criativos: true }), 'criativos', 'link antigo sem permissão cai no padrão')
  assert.equal(aba({ aba: 'qualquer' }, { conferencia: true }), 'conferencia')

  // O middleware da página: link antigo vira ?aba=conferencia (+ ?conf=ml no ML) antes de montar.
  const mIni = s.indexOf('middleware: [')
  assert.ok(mIni > 0, 'página tem o middleware dos links antigos')
  const mFim = s.indexOf('\n  ],', mIni)
  const lista = transpile(`const lista = [${s.slice(mIni + 'middleware: ['.length, mFim)}\n]`, ts.ModuleKind.ESNext)
  const mw = new Function('navigateTo', `${lista}; return lista[0]`)(
    (alvo, opts) => ({ alvo, opts }),
  )
  assert.deepEqual(mw({ path: '/marketing', query: { aba: 'ml' }, hash: '' }),
    { alvo: { path: '/marketing', query: { aba: 'conferencia', conf: 'ml' }, hash: '' }, opts: { replace: true } })
  assert.deepEqual(mw({ path: '/marketing', query: { aba: 'shopee', conf: 'amazon', x: '1' }, hash: '#h' }),
    { alvo: { path: '/marketing', query: { aba: 'conferencia', x: '1' }, hash: '#h' }, opts: { replace: true } })
  for (const q of [{}, { aba: 'conferencia', conf: 'ml' }, { aba: 'criativos' }]) {
    assert.equal(mw({ path: '/marketing', query: q, hash: '' }), undefined, `sem redirecionar: ${JSON.stringify(q)}`)
  }
}

// ---------------------------------------------------------------- Conferência: os horários embaixo
{
  // Shopee e ML têm o heatmap; a Amazon não. Fora da cadeia de estados do relatório
  // (aparece mesmo sem relatório) e antes das contas da conferência.
  const tag = '<MarketingHorarios v-if="plataforma === \'shopee\' || plataforma === \'ml\'" :plataforma="plataforma" />'
  const pos = tpl.indexOf(tag)
  assert.ok(pos > 0, 'Conferência renderiza os horários')
  assert.ok(pos > tpl.indexOf('Afiliados incompletos:'), 'embaixo do relatório')
  assert.ok(pos < tpl.indexOf('<!-- contas: quem entra'), 'antes das contas')
  // Fora da cadeia v-if/v-else-if dos estados: o elemento anterior fecha o v-else-if="exec".
  const antes = tpl.slice(tpl.indexOf('v-else-if="exec"'), pos)
  const abre = (antes.match(/<div\b/g) || []).length
  const fecha = (antes.match(/<\/div>/g) || []).length
  assert.equal(abre, fecha, 'os horários ficam fora do bloco do relatório')
  assert.ok(!script.includes('/api/marketing/schedules') && !script.includes('/api/marketing/accounts'), 'a Conferência não lê os horários: o componente lê')
}

// ---------------------------------------------------------------- lib real
const L = {}
new Function('exports', transpile(fs.readFileSync(path.join(__dirname, '../lib/conferencia.ts'), 'utf8')))(L)
const apiError = {}
new Function('exports', transpile(fs.readFileSync(path.join(__dirname, '../lib/apiError.ts'), 'utf8')))(apiError)

// ---------------------------------------------------------------- dados falsos
const ID_PRONTO = '11111111-1111-4111-8111-111111111111'
const ID_VELHO = '22222222-2222-4222-8222-222222222222'
const ID_COLETANDO = '33333333-3333-4333-8333-333333333333'
const ID_LINK = '44444444-4444-4444-8444-444444444444'
const ID_NOVO = '55555555-5555-4555-8555-555555555555'
const SEMANAS = [
  { inicio: '2026-09-28', fim: '2026-10-04', rotulo: '28/09–04/10' },
  { inicio: '2026-09-21', fim: '2026-09-27', rotulo: '21/09–27/09' },
  { inicio: '2026-09-14', fim: '2026-09-20', rotulo: '14/09–20/09' },
  { inicio: '2026-09-07', fim: '2026-09-13', rotulo: '07/09–13/09' },
]
const vals = (over = {}) => ({
  vendas_afiliados: 1000, vendas_ads: 500, saldo_ads: 100, impressoes: 10000,
  invest_afiliados: 50, invest_ads: 30, pct: 8, vendas: 1000,
  cliques_afiliados: 400, pedidos_afiliados: 10, conversao_afiliados: 2.5,
  cliques_ads: 200, pedidos_ads: 6, conversao_ads: 3, ...over,
})
const nulos = () => Object.fromEntries(L.METRICAS.map((m) => [m.chave, null]))
const linha = (conta, over = {}) => ({
  conta_id: `c-${conta}`, conta, usuario: `${conta.toLowerCase()}_loja`, status: 'ok', erro: null, avisos: [],
  semanas: [vals({ vendas: 1200, pct: 6.67 }), vals(), vals({ vendas: 900 }), vals({ vendas: 1100 })], ...over,
})
const total = (contas, semDados, s) => ({ contas, sem_dados: semDados, semanas: s })
function relatorio(over = {}) {
  return {
    versao: 1, execucao_id: ID_PRONTO, tipo: 'semanal', origem: 'agenda',
    gerado_em: '2026-10-06T19:41:00Z', criado_em: '2026-10-06T16:30:00Z',
    semanas: SEMANAS, metricas: L.METRICAS,
    grupos: [
      {
        chave: 'mala', rotulo: 'Mala',
        linhas: [
          linha('Alfa', { avisos: ['afiliados só até 03/10'] }),
          linha('Beta', { status: 'sem_automacao', erro: 'perfil com Firefox', usuario: null, semanas: [nulos(), nulos(), nulos(), nulos()] }),
        ],
        total: total(2, 1, [vals({ vendas: 1200 }), vals(), vals({ vendas: 900 }), vals({ vendas: 1100 })]),
      },
      {
        chave: 'celular', rotulo: 'Celular',
        linhas: [linha('Gama', { status: 'parcial', avisos: ['Ads sem dados na S3'] })],
        total: total(1, 0, [vals({ vendas: 2000 }), vals({ vendas: 1600 }), vals(), vals()]),
      },
      {
        chave: 'eletro', rotulo: 'Eletro',
        linhas: [linha('Gama', { semanas: [vals({ saldo_ads: null }), vals({ saldo_ads: null }), vals({ saldo_ads: null }), vals({ saldo_ads: null })] })],
        total: total(1, 0, [vals({ saldo_ads: null }), vals({ saldo_ads: null }), vals({ saldo_ads: null }), vals({ saldo_ads: null })]),
      },
    ],
    geral: total(3, 1, [vals({ vendas: 4400, invest_afiliados: 150, invest_ads: 90, pct: 5.45 }), vals({ vendas: 3600 }), vals(), vals()]),
    notas: ['Fonte: Central do Vendedor (dados falsos do teste).'],
    contas_sem_dados: [{ conta: 'Beta', status: 'sem_automacao', erro: 'perfil com Firefox' }],
    afiliados_incompletos: [{ conta: 'Alfa', ate: '2026-10-03' }],
    divergencias: [{ conta: 'Gama', item_id: '9000000001', nome: 'Fritadeira teste', davinci: 'outro', categoria_shopee: 100010 }],
    nao_atribuido_ads: [{ conta: 'Gama', gasto: 12.3 }],
    ...over,
  }
}
const execResumo = (id, status, criado, over = {}) => ({
  id, tipo: 'semanal', origem: 'agenda', status, criado_em: criado, finalizado_em: null, semanas: SEMANAS,
  resumo: { contas: 3, ok: 2, sem_dados: 1 }, ...over,
})
const LISTA = [
  execResumo(ID_COLETANDO, 'coletando', '2026-10-08T16:30:00Z', { tipo: 'parcial' }),
  execResumo(ID_PRONTO, 'pronto', '2026-10-06T16:30:00Z'),
  execResumo(ID_VELHO, 'pronto', '2026-10-01T16:30:00Z'),
]
const coleta = (nome, status, over = {}) => ({
  id: `col-${nome}`, nome, grupo: 'celular', status, erro: null, tentativas: 1, adiamentos: 0, concluido_em: null, ...over,
})
function detalhe(id, status, over = {}) {
  return {
    execucao: {
      ...execResumo(id, status, '2026-10-06T16:30:00Z'),
      afiliados_ate: '2026-10-04', esperar_afiliados_ate: '2026-10-06T18:00:00Z',
      corte: '2026-10-06T20:30:00Z', prazo: '2026-10-06T21:00:00Z',
    },
    coletas: [
      coleta('Alfa', 'ok', { grupo: 'mala', concluido_em: '2026-10-06T16:41:00Z' }),
      coleta('Beta', status === 'coletando' ? 'coletando' : 'sem_automacao', { erro: status === 'coletando' ? null : 'perfil com Firefox' }),
      coleta('Gama', status === 'coletando' ? 'pendente' : 'ok', {
        adiamentos: status === 'coletando' ? 1 : 0, adiamentos_perfil: status === 'coletando' ? 1 : 0,
      }),
    ],
    relatorio: status === 'pronto' ? relatorio({ execucao_id: id }) : null,
    ...over,
  }
}
const CONTAS = [
  { id: 'k1', adspower_user_id: 'perfil1', nome: 'Zeta', grupo: 'celular', ativo: true, ordem: 0, conta_key: 'zeta', observacao: null },
  { id: 'k2', adspower_user_id: 'perfil2', nome: 'Alfa', grupo: 'mala', ativo: true, ordem: 0, conta_key: 'alfa', observacao: null },
  { id: 'k3', adspower_user_id: 'perfil3', nome: 'Beta', grupo: 'celular', ativo: false, ordem: 0, conta_key: null, observacao: null },
]

// ---------------------------------------------------------------- script setup com tudo falso
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const semImports = (s) => s.replace(/^import[\s\S]*?from\s+'[^']+'\s*$/gm, '')
const ICONES = ['AlertTriangle', 'Ban', 'Bell', 'ChevronRight', 'FileCode', 'FileJson', 'FileSpreadsheet', 'FileText', 'Inbox', 'Loader2', 'Play', 'RefreshCw', 'RotateCcw']
const NOMES_L = Object.keys(L)
const RETORNO = `return {
  execucoes, listaCarregada, carregandoLista, erro, selecionada, detalhe, carregandoDetalhe, erroDetalhe,
  exec, rel, coletas, opcoes, emAndamento, concluidas, pctConcluidas, prazosTxt, detalheColeta,
  escolher, recarregar, carregarDetalhe,
  tipoNovo, gerando, gerar, recalcular, cancelar, podeAgir, baixar, baixando,
  planilha, blocosCab, semDadosTxt, afiliadosIncompletosTxt,
  contasAberto, contas, contasOrdenadas, contasErro, nomes, salvarConta, salvarNome, salvandoConta,
  isAdmin, informarAberto, COR,
  plataforma, plat, plataformaAberta, trocarPlataforma, contasPorIntegracao, semVinculo, COLETA_TXT,
  lojas, integracoes, versaoContas, salvarLoja, salvarIntegracao, usadaPor,
}`
const fabrica = new AsyncFunction(
  'computed', 'onBeforeUnmount', 'onMounted', 'ref', 'watch',
  'useApi', 'useToasts', 'useCan', 'useAuthStore', 'useRoute', 'useRouter', 'apiErrMsg',
  'window', 'document', 'URL', 'Blob',
  ...ICONES, ...NOMES_L,
  transpile(semImports(script), ts.ModuleKind.ESNext) + '\n' + RETORNO,
)

function relogioFalso() {
  let id = 0
  const fila = new Map()
  return {
    setTimeout: (fn, ms) => { fila.set(++id, { fn, ms }); return id },
    clearTimeout: (i) => { fila.delete(i) },
    pendentes: () => [...fila.values()],
    async disparar(ms) {
      const achado = [...fila].find(([, t]) => ms === undefined || t.ms === ms)
      assert.ok(achado, `havia um timer${ms ? ` de ${ms} ms` : ''}`)
      fila.delete(achado[0])
      await achado[1].fn()
      await esperar()
    },
  }
}
const esperar = () => new Promise(setImmediate)

// `lista` = a da Shopee; `listas` = a dos outros marketplaces ({ ml: [...] }).
// Array ou função (que pode devolver uma Promise, pra segurar a resposta).
async function montar({
  lista = LISTA, listas = {}, detalhes = {}, query = {}, canEdit = true, admin = true, confirmar = true,
  post, contasResp = CONTAS, contasPlat = {}, integracoesPlat = {}, put, listaErro = null, arquivo,
} = {}) {
  const calls = []
  const toastLog = []
  const routerCalls = []
  const confirms = []
  const abas = []
  const ancoras = []
  const relogio = relogioFalso()
  const ouvintes = new Map()
  const objetos = []
  const montados = []
  const desmontados = []
  const route = { query: { ...query } }
  const salvas = new Map()
  const api = (url, opts = {}) => {
    calls.push({ url, opts })
    const m = opts.method || 'GET'
    const lst = new RegExp(`^${BASE}/execucoes\\?limite=30&plataforma=(\\w+)$`).exec(url)
    if (m === 'GET' && lst) {
      if (listaErro) return Promise.reject(listaErro)
      const l = lst[1] === 'shopee' ? lista : (listas[lst[1]] ?? [])
      return Promise.resolve(typeof l === 'function' ? l() : l)
    }
    const arq = new RegExp(`^${BASE}/execucoes/([^/]+)/arquivo/(\\w+)$`).exec(url)
    if (m === 'GET' && arq) {
      if (arquivo) return arquivo(arq[1], arq[2], opts)
      opts.onResponse?.({ response: { ok: true, headers: { get: (h) => (h === 'Content-Disposition' ? `attachment; filename="srv-${arq[2]}.${arq[2]}"` : null) } } })
      return Promise.resolve(new Blob([`arquivo ${arq[2]}`], { type: 'application/octet-stream' }))
    }
    const det = new RegExp(`^${BASE}/execucoes/([^/]+)$`).exec(url)
    if (m === 'GET' && det) {
      const id = decodeURIComponent(det[1])
      const d = detalhes[id]
      if (typeof d === 'function') return d()
      if (d) return Promise.resolve(JSON.parse(JSON.stringify(d)))
      return Promise.reject({ statusCode: 404, data: { detail: { code: 'conferencia_nao_encontrada' } } })
    }
    const criar = new RegExp(`^${BASE}/execucoes\\?plataforma=(\\w+)$`).exec(url)
    if (m === 'POST' && criar) return post ? post(opts.body, criar[1]) : Promise.reject(new Error('sem POST'))
    if (m === 'POST' && /\/(recalcular|cancelar)$/.test(url)) return Promise.resolve({ ok: true })
    const integ = new RegExp(`^${BASE}/contas/integracoes\\?plataforma=(\\w+)$`).exec(url)
    if (m === 'GET' && integ) {
      const i = integracoesPlat[integ[1]] ?? []
      return typeof i === 'function' ? i() : Promise.resolve(JSON.parse(JSON.stringify(i)))
    }
    const cts = new RegExp(`^${BASE}/contas\\?plataforma=(\\w+)$`).exec(url)
    if (m === 'GET' && cts) {
      const c = cts[1] === 'shopee' ? contasResp : (contasPlat[cts[1]] ?? [])
      return typeof c === 'function' ? c() : Promise.resolve(JSON.parse(JSON.stringify(c)))
    }
    const conta = new RegExp(`^${BASE}/contas/([^/]+)$`).exec(url)
    if (m === 'PUT' && conta) {
      if (put) return put(conta[1], opts.body)
      // Guarda o que foi salvo: o PUT seguinte parte dele, como no servidor.
      const c = salvas.get(conta[1])
        ?? [...(Array.isArray(contasResp) ? contasResp : []), ...Object.values(contasPlat).flat()].find((x) => x.id === conta[1])
      salvas.set(conta[1], { ...c, ...opts.body })
      return Promise.resolve({ ...c, ...opts.body })
    }
    return Promise.reject(new Error(`api falso não conhece ${m} ${url}`))
  }
  const f = (kind) => (...a) => toastLog.push([kind, ...a])
  const toasts = { success: f('success'), error: f('error'), warning: f('warning'), info: f('info'), push: () => 1, dismiss: () => {} }
  const documento = {
    visibilityState: 'visible',
    addEventListener: (tipo, fn) => ouvintes.set(tipo, fn),
    removeEventListener: (tipo, fn) => { if (ouvintes.get(tipo) === fn) ouvintes.delete(tipo) },
    createElement: () => { const a = { click() { this.clicado = true }, remove() {} }; ancoras.push(a); return a },
    body: { appendChild: () => {} },
  }
  const janela = {
    setTimeout: relogio.setTimeout,
    clearTimeout: relogio.clearTimeout,
    confirm: (msg) => { confirms.push(msg); return confirmar },
    open: (url, alvo) => { const aba = { url, alvo, closed: false, location: { href: '' }, close() { this.closed = true } }; abas.push(aba); return aba },
  }
  const urlFalso = { createObjectURL: (b) => { objetos.push(b); return `blob:falso/${objetos.length}` }, revokeObjectURL: () => {} }
  const s = await fabrica(
    Vue.computed, (fn) => desmontados.push(fn), (fn) => montados.push(fn), Vue.ref, Vue.watch,
    () => ({ api }), () => toasts,
    (_r, acao) => Vue.ref(acao === 'view' ? true : canEdit),
    () => ({ user: { role: admin ? 'admin' : 'user', email: 'x@teste' } }),
    () => route,
    () => ({ replace: (alvo) => { routerCalls.push(alvo); route.query = { ...alvo.query }; return Promise.resolve() } }),
    apiError.apiErrMsg,
    janela, documento, urlFalso, Blob,
    ...ICONES.map((n) => ({ name: n })), ...NOMES_L.map((n) => L[n]),
  )
  for (const fn of montados) fn()
  await esperar()
  await esperar()
  return {
    s, calls, toastLog, routerCalls, confirms, abas, ancoras, relogio, documento, ouvintes, objetos, route,
    desmontar: () => desmontados.forEach((fn) => fn()),
  }
}
const urls = (calls) => calls.map((c) => `${c.opts?.method || 'GET'} ${c.url}`)
const LISTA_URL = (p = 'shopee') => `${BASE}/execucoes?limite=30&plataforma=${p}`
const DET = {
  [ID_PRONTO]: detalhe(ID_PRONTO, 'pronto'),
  [ID_VELHO]: detalhe(ID_VELHO, 'pronto'),
  [ID_COLETANDO]: detalhe(ID_COLETANDO, 'coletando'),
  [ID_LINK]: detalhe(ID_LINK, 'pronto'),
}

// O trecho da planilha do template, renderizado com o estado da tela (SSR):
// trava o desenho de verdade — ordem do cabeçalho, mesclas, bege, cores.
const trechoPlanilha = (() => {
  const ini = tpl.indexOf('<div v-if="planilha"')
  assert.ok(ini > 0, 'template tem a planilha')
  return tpl.slice(ini, tpl.indexOf('</div>', ini) + '</div>'.length)
})()
const renderTrecho = (() => {
  const c = compileTemplate({ source: trechoPlanilha, filename: 'planilha.vue', id: 'planilha-check' })
  assert.deepEqual(c.errors, [])
  const mod = {}
  new Function('exports', 'require', transpile(c.code))(mod, require)
  return mod.render
})()
function renderPlanilha(s) {
  const estado = { planilha: s.planilha, blocosCab: s.blocosCab, COR: s.COR }
  return renderToString(Vue.createSSRApp({ setup: () => estado, render: renderTrecho }))
}

async function run() {
  // Abre no último PRONTO (não na coleta em andamento, que fica num aviso).
  {
    const t = await montar({ detalhes: DET })
    const { s } = t
    assert.equal(s.selecionada.value, ID_PRONTO, 'último pronto')
    assert.deepEqual(urls(t.calls), [`GET ${LISTA_URL()}`, `GET ${BASE}/execucoes/${ID_PRONTO}`])
    assert.equal(s.plataforma.value, 'shopee', 'sem ?conf= abre na Shopee')
    assert.equal(s.plataformaAberta.value, 'shopee', 'relatório sem plataforma (de antes) é da Shopee')
    assert.equal(s.emAndamento.value.id, ID_COLETANDO, 'aviso da coleta em andamento')
    assert.equal(t.routerCalls.length, 0, 'escolha automática não mexe na URL')
    assert.equal(t.relogio.pendentes().length, 0, 'relatório pronto não fica relendo')
    assert.ok(t.ouvintes.has('visibilitychange'), 'ouve a visibilidade')

    // Planilha do Resumo: semanas da mais velha pra mais nova, Mala/Celular/Eletro/Geral.
    const p = s.planilha.value
    assert.deepEqual(p.semanas.map((x) => x.rotulo), ['07/09 a 13/09', '14/09 a 20/09', '21/09 a 27/09', '28/09 a 04/10'])
    assert.deepEqual(p.grupos.map((g) => g.rotulo), ['Mala', 'Celular', 'Eletro', 'Geral'])
    assert.equal(p.variacao, 'Variação (28/09–04/10 × 21/09–27/09)')
    assert.equal(s.blocosCab.value, 5, '4 semanas + a Variação')
    const linhaP = (sub, cat) => p.linhas.find((l) => l.sub === sub && (!cat || l.categoria === cat))
    const vendasP = linhaP('Vendas no período')
    assert.deepEqual(vendasP.valores[0], ['R$ 1.100,00', 'R$ 1.000,00', 'R$ 1.000,00', 'R$ 1.000,00'], '07/09 primeiro')
    assert.deepEqual(vendasP.valores[3], ['R$ 1.200,00', 'R$ 2.000,00', 'R$ 1.000,00', 'R$ 4.400,00'], '28/09 por último')
    assert.deepEqual(vendasP.variacoes.map((v) => [v.texto, v.cor]), [
      ['▲ 20,0%', 'verde'], ['▲ 25,0%', 'verde'], ['=', 'cinza'], ['▲ 22,2%', 'verde'],
    ])
    // % com 2 casas e variação em p.p. (cair é bom).
    const pctP = linhaP('% investimento / vendas')
    assert.deepEqual(pctP.valores[3], ['8,00%', '8,00%', '8,00%', '5,45%'])
    assert.deepEqual([pctP.variacoes[3].texto, pctP.variacoes[3].cor], ['▼ 2,55 p.p.', 'verde']) // 8 − 5,45: p.p. com 2 casas, como o % da planilha
    assert.deepEqual(linhaP('Ads', 'Conversão').valores[3], ['3,00%', '3,00%', '3,00%', '3,00%'])
    // Impressões de afiliados não existem na Shopee.
    assert.ok(linhaP('afiliados', 'Impressões').valores.every((x) => x.every((v) => v === '—')))
    assert.ok(!p.linhas.some((l) => l.chave === 'saldo_ads'), 'Saldo fora do Resumo')

    // O HTML de verdade da planilha (o trecho do template, renderizado).
    const html = await renderPlanilha(s)
    // (a data da semana vem num <span class="semana-txt">: tira as tags de dentro do th)
    const cabecalhos = [...html.matchAll(/<th[^>]*>((?:(?!<\/?th\b)[\s\S])*)<\/th>/g)].map((m) => m[1].replace(/<[^>]+>/g, '').trim())
    assert.deepEqual(cabecalhos.slice(0, 6), [
      'Métrica', '07/09 a 13/09', '14/09 a 20/09', '21/09 a 27/09', '28/09 a 04/10', 'Variação (28/09–04/10 × 21/09–27/09)',
    ], '1ª linha: Métrica, as semanas da mais velha pra mais nova, Variação')
    assert.equal((html.match(/colspan="4"/g) || []).length, 5, 'cada semana (e a Variação) mesclada sobre 4 colunas')
    // No celular a célula mesclada é mais larga que a tela: a data vai num span
    // que gruda logo depois das 2 colunas paradas (sticky left/right).
    assert.equal((html.match(/<span class="semana-txt">/g) || []).length, 5, 'data de cada semana (e a Variação) no span parado')
    assert.match(html, /<th class="rotulo-cab" colspan="2" rowspan="2"/, 'Métrica sobre as 2 colunas de rótulo')
    assert.deepEqual(cabecalhos.slice(6, 26), Array(5).fill(['Mala', 'Celular', 'Eletro', 'Geral']).flat(), '2ª linha: os 4 grupos em cada bloco')
    const linhasHtml = html.split('<tbody>')[1].split('</tr>').filter((x) => x.includes('<td'))
    assert.equal(linhasHtml.length, 10, '10 linhas de métrica')
    const rotulosLinha = linhasHtml.map((x) => [...x.matchAll(/<th[^>]*>([^<]*)<\/th>/g)].map((m) => m[1].trim()))
    assert.deepEqual(rotulosLinha, [
      ['Vendas', 'afiliados'], ['Ads'], ['Impressões', 'afiliados'], ['Ads'], ['Conversão', 'afiliados'], ['Ads'],
      ['Investimento', 'afiliados'], ['Ads'], ['Resumo', '% investimento / vendas'], ['Vendas no período'],
    ], 'categoria mesclada (rowspan) + sub-rótulo')
    assert.equal((html.match(/rowspan="2" scope="rowgroup"/g) || []).length, 5, 'cada categoria cobre as 2 linhas dela')
    assert.match(html, /class="cat ultima"[^>]*>\s*Resumo/, 'a última categoria não dobra a borda de baixo')
    for (const l of linhasHtml) assert.equal((l.match(/<td/g) || []).length, 20, '4 semanas × 4 grupos + 4 variações')
    const celulas = (l) => [...l.matchAll(/<td class="([^"]*)"[^>]*>([^<]*)<\/td>/g)].map((m) => [m[1], m[2].trim()])
    const ultima = celulas(linhasHtml[9])
    assert.deepEqual(ultima.slice(12, 16).map((c) => c[1]), ['R$ 1.200,00', 'R$ 2.000,00', 'R$ 1.000,00', 'R$ 4.400,00'], 'semana atual no 4º bloco')
    assert.deepEqual(ultima.filter((c) => /\bgeral\b/.test(c[0])).length, 5, 'Geral em bege em cada bloco (+ a Variação)')
    assert.deepEqual(ultima.slice(16).map((c) => c[1]), ['▲ 20,0%', '▲ 25,0%', '=', '▲ 22,2%'])
    assert.match(ultima[16][0], /text-emerald-600 dark:text-emerald-400/, 'variação boa em verde')
    assert.match(ultima[18][0], /text-muted-foreground/, '"=" em cinza')
    const pctHtml = celulas(linhasHtml[8])
    assert.match(pctHtml[19][0], /text-emerald-600/, '% caindo em verde')
    assert.ok(celulas(linhasHtml[2]).every((c) => c[1] === '—'), 'Impressões de afiliados: "—" em tudo')
    assert.match(html, /class="planilha overflow-x-auto rounded-xl border"/)

    // Avisos curtos
    assert.equal(s.semDadosTxt.value, 'Beta (perfil Firefox, o robô não abre)')
    assert.equal(s.afiliadosIncompletosTxt.value, 'Alfa (até 03/10)')

    // Ações de quem edita num relatório pronto: Recalcular sim, Cancelar não.
    assert.equal(s.podeAgir.value, true)
    await s.recalcular()
    assert.ok(urls(t.calls).includes(`POST ${BASE}/execucoes/${ID_PRONTO}/recalcular`))
    assert.equal(t.toastLog.at(-1)[1], 'Relatório recalculado')

    // Trocar pela pessoa: vira ?execucao= na URL.
    s.escolher(ID_VELHO)
    await esperar()
    assert.equal(s.selecionada.value, ID_VELHO)
    assert.deepEqual(t.routerCalls.at(-1), { query: { execucao: ID_VELHO } })
    assert.equal(s.exec.value.id, ID_VELHO)

    // Desmontou: para de ouvir.
    t.desmontar()
    assert.ok(!t.ouvintes.has('visibilitychange'), 'tirou o ouvinte')
  }

  // Link do Threema: ?execucao= manda, mesmo fora das 30 da lista.
  {
    const t = await montar({ detalhes: DET, query: { aba: 'conferencia', execucao: ID_LINK } })
    assert.equal(t.s.selecionada.value, ID_LINK)
    assert.equal(t.s.opcoes.value[0].id, ID_LINK, 'entra no seletor')
    assert.equal(t.s.opcoes.value.length, 4)
    // id inválido no link: ignora e cai no padrão.
    const t2 = await montar({ detalhes: DET, query: { execucao: '../../admin' } })
    assert.equal(t2.s.selecionada.value, ID_PRONTO)
    assert.ok(!t2.calls.some((c) => c.url.includes('admin')), 'nada do link vai pra URL da API')
  }

  // Sem nenhum pronto: abre o mais novo (a coleta) e acompanha a cada 20 s.
  {
    let leituras = 0
    const det = {
      [ID_COLETANDO]: () => {
        leituras++
        return Promise.resolve(leituras < 3 ? detalhe(ID_COLETANDO, 'coletando') : detalhe(ID_COLETANDO, 'pronto'))
      },
    }
    const t = await montar({ lista: [LISTA[0]], detalhes: det })
    const { s } = t
    assert.equal(s.selecionada.value, ID_COLETANDO)
    assert.equal(s.exec.value.status, 'coletando')
    assert.equal(s.rel.value, null, 'coletando: sem relatório, mostra as lojas')
    assert.equal(s.concluidas.value, 1)
    assert.equal(s.pctConcluidas.value, 33)
    assert.match(s.prazosTxt.value, /Espera os afiliados de 04\/10 até 15:00\. Loja nova só começa até 17:30; o que faltar às 18:00 fica de fora\./)
    assert.equal(s.detalheColeta(s.coletas.value[2]), 'adiada 1× (perfil em uso)')
    // Só os de perfil em uso têm limite: a tela diz qual foi o motivo.
    const base = { ...s.coletas.value[2], erro: null }
    assert.equal(s.detalheColeta({ ...base, adiamentos: 4, adiamentos_perfil: 0 }), 'adiada 4× (esperando os afiliados)')
    assert.equal(s.detalheColeta({ ...base, adiamentos: 5, adiamentos_perfil: 2 }), 'adiada 5× (2× perfil em uso, 3× esperando os afiliados)')
    assert.equal(s.detalheColeta(s.coletas.value[0]), 'às 13:41')
    assert.equal(s.podeAgir.value, true, 'Cancelar aparece')
    assert.deepEqual(t.relogio.pendentes().map((x) => x.ms), [20000], 'relê em 20 s')

    // Aba escondida: para; voltou: lê na hora e reagenda.
    t.documento.visibilityState = 'hidden'
    t.ouvintes.get('visibilitychange')()
    assert.equal(t.relogio.pendentes().length, 0, 'escondida não relê')
    t.documento.visibilityState = 'visible'
    t.ouvintes.get('visibilitychange')()
    await esperar()
    assert.equal(leituras, 2, 'voltou: leu na hora')
    assert.deepEqual(t.relogio.pendentes().map((x) => x.ms), [20000])
    const antes = t.calls.length

    // Terceira leitura: ficou pronto → avisa, recarrega a lista, para de reler.
    await t.relogio.disparar(20000)
    await esperar()
    assert.equal(s.exec.value.status, 'pronto')
    assert.ok(s.rel.value, 'relatório na tela')
    assert.ok(t.toastLog.some((x) => x[0] === 'success' && x[1] === 'Conferência pronta'))
    assert.ok(urls(t.calls.slice(antes)).includes(`GET ${LISTA_URL()}`), 'lista recarregada')
    assert.equal(t.relogio.pendentes().length, 0, 'pronto: parou')
  }

  // Desmontar no meio do acompanhamento: o timer some e nada mais é lido.
  {
    const t = await montar({ lista: [LISTA[0]], detalhes: DET })
    assert.equal(t.relogio.pendentes().length, 1)
    t.desmontar()
    assert.equal(t.relogio.pendentes().length, 0, 'timer limpo')
  }

  // Releitura silenciosa que falha não apaga a tela.
  {
    let falhar = false
    const det = { [ID_COLETANDO]: () => (falhar ? Promise.reject(new Error('rede caiu')) : Promise.resolve(detalhe(ID_COLETANDO, 'coletando'))) }
    const t = await montar({ lista: [LISTA[0]], detalhes: det })
    falhar = true
    await t.relogio.disparar(20000)
    assert.equal(t.s.exec.value.status, 'coletando', 'continua mostrando')
    assert.equal(t.s.erroDetalhe.value, null)
  }

  // Resposta velha não sobrescreve a nova (troca rápida de relatório).
  {
    let soltarVelho
    const det = {
      ...DET,
      [ID_VELHO]: () => new Promise((r) => { soltarVelho = () => r(detalhe(ID_VELHO, 'pronto')) }),
    }
    const t = await montar({ detalhes: det })
    t.s.escolher(ID_VELHO)
    await esperar()
    t.s.escolher(ID_PRONTO)
    await esperar()
    soltarVelho()
    await esperar()
    assert.equal(t.s.exec.value.id, ID_PRONTO, 'a última escolha ganha')
  }

  // Gerar agora: confirma, manda o tipo, abre a nova e põe na URL.
  {
    let corpo = null
    let lista = LISTA.slice(1)
    const t = await montar({
      lista: () => lista,
      detalhes: { ...DET, [ID_NOVO]: detalhe(ID_NOVO, 'coletando') },
      post: (body) => {
        corpo = body
        lista = [execResumo(ID_NOVO, 'coletando', '2026-10-09T12:00:00Z'), ...lista]
        return Promise.resolve({ id: ID_NOVO })
      },
    })
    t.s.tipoNovo.value = 'parcial'
    await t.s.gerar()
    await esperar()
    assert.equal(t.confirms.length, 1)
    assert.match(t.confirms[0], /parcial \(segunda até ontem\)/)
    assert.deepEqual(corpo, { tipo: 'parcial', plataforma: 'shopee' })
    assert.match(t.confirms[0], /^Gerar agora a conferência da Shopee parcial/)
    assert.match(t.confirms[0], /robô do Mac abre o perfil de cada loja no AdsPower/)
    assert.equal(t.s.selecionada.value, ID_NOVO)
    assert.deepEqual(t.routerCalls.at(-1), { query: { execucao: ID_NOVO } })
    assert.equal(t.toastLog.at(-1)[1], 'Conferência iniciada')
  }

  // Gerar agora: não confirmou, nada sai.
  {
    const t = await montar({ detalhes: DET, confirmar: false, post: () => assert.fail('não podia gerar') })
    await t.s.gerar()
    assert.ok(!urls(t.calls).some((u) => u.startsWith(`POST ${BASE}/execucoes`)))
  }

  // Gerar agora com uma já coletando: 409 vira aviso e leva até ela.
  {
    const t = await montar({
      detalhes: DET,
      post: () => Promise.reject({ statusCode: 409, data: { detail: { code: 'conferencia_em_andamento' } } }),
    })
    await t.s.gerar()
    await esperar()
    assert.deepEqual(t.toastLog.at(-1).slice(0, 2), ['warning', 'Já tem uma conferência coletando'])
    assert.equal(t.s.selecionada.value, ID_COLETANDO, 'foi até a que está coletando')
  }

  // 409 de nenhuma loja ativa NÃO é "já tem uma coletando": diz a causa.
  {
    const t = await montar({
      detalhes: DET,
      post: () => Promise.reject({ statusCode: 409, data: { detail: { code: 'conferencia_sem_contas' } } }),
    })
    await t.s.gerar()
    await esperar()
    assert.deepEqual(t.toastLog.at(-1), ['error', 'Não consegui gerar a conferência', 'Nenhuma loja ativa — ative ao menos uma em Contas antes de gerar.'])
  }

  // Outro erro ao gerar: frase, não código cru.
  {
    const t = await montar({ detalhes: DET, post: () => Promise.reject({ statusCode: 403, data: { detail: { code: 'forbidden' } } }) })
    await t.s.gerar()
    assert.deepEqual(t.toastLog.at(-1), ['error', 'Não consegui gerar a conferência', 'Sem permissão para isso (Marketing › editar).'])
  }

  // Cancelar: confirma e manda.
  {
    const t = await montar({ lista: [LISTA[0]], detalhes: DET })
    await t.s.cancelar()
    assert.equal(t.confirms.length, 1)
    assert.ok(urls(t.calls).includes(`POST ${BASE}/execucoes/${ID_COLETANDO}/cancelar`))
    const t2 = await montar({ lista: [LISTA[0]], detalhes: DET, confirmar: false })
    await t2.s.cancelar()
    assert.ok(!urls(t2.calls).some((u) => u.endsWith('/cancelar')), 'sem confirmar não cancela')
  }

  // Arquivos: nome do servidor; o HTML abre numa aba nova.
  {
    const t = await montar({ detalhes: DET })
    await t.s.baixar('xlsx')
    const pedido = t.calls.find((c) => c.url.endsWith('/arquivo/xlsx'))
    assert.equal(pedido.opts.responseType, 'blob')
    assert.equal(t.ancoras.at(-1).download, 'srv-xlsx.xlsx', 'nome do Content-Disposition')
    assert.ok(t.ancoras.at(-1).clicado)
    assert.equal(t.abas.length, 0, 'Excel não abre aba')

    await t.s.baixar('html')
    assert.equal(t.abas.length, 1, 'HTML abre aba nova')
    assert.equal(t.abas[0].alvo, '_blank')
    assert.match(t.abas[0].location.href, /^blob:falso\//)
    assert.equal(t.objetos.at(-1).type, 'text/html;charset=utf-8', 'aba recebe HTML')
    assert.equal(t.s.baixando.value, null)

    // Sem nome no cabeçalho: o mesmo nome que o servidor montaria.
    const t2 = await montar({ detalhes: DET, arquivo: () => Promise.resolve(new Blob(['x'])) })
    await t2.s.baixar('csv')
    assert.equal(t2.ancoras.at(-1).download, 'conferencia-shopee-2026-09-28_2026-10-04.csv')

    // Erro vem como Blob (responseType blob): lê o JSON e mostra a frase; a aba fecha.
    const erroBlob = { statusCode: 404, data: new Blob([JSON.stringify({ detail: { code: 'conferencia_sem_relatorio' } })]) }
    const t3 = await montar({ detalhes: DET, arquivo: () => Promise.reject(erroBlob) })
    await t3.s.baixar('html')
    assert.equal(t3.abas[0].closed, true, 'aba fechada no erro')
    assert.deepEqual(t3.toastLog.at(-1), ['error', 'Não deu para baixar o HTML', 'Essa conferência ainda não tem relatório.'])
  }

  // Lista falhou: erro na tela, sem quebrar.
  {
    const t = await montar({ listaErro: { statusCode: 500, data: { detail: { code: 'erro_interno' } } } })
    assert.equal(t.s.erro.value, 'erro_interno')
    assert.equal(t.s.selecionada.value, null)
    assert.equal(t.s.listaCarregada.value, true)
  }

  // Nada ainda: lista vazia, nenhum detalhe pedido.
  {
    const t = await montar({ lista: [] })
    assert.equal(t.s.opcoes.value.length, 0)
    assert.equal(t.s.selecionada.value, null)
    assert.equal(t.calls.length, 1)
  }

  // Só quem vê: nada de agir; Threema só admin.
  {
    const t = await montar({ detalhes: DET, canEdit: false, admin: false })
    assert.equal(t.s.podeAgir.value, false)
    assert.equal(t.s.isAdmin.value, false)
  }

  // Contas: carrega ao abrir, ordena Mala antes, salva só o que mudou.
  {
    const t = await montar({ detalhes: DET })
    assert.ok(!urls(t.calls).includes(`GET ${BASE}/contas?plataforma=shopee`), 'só carrega ao abrir')
    t.s.contasAberto.value = true
    await esperar()
    await esperar()
    assert.deepEqual(t.s.contasOrdenadas.value.map((c) => c.nome), ['Alfa', 'Beta', 'Zeta'])
    const zeta = t.s.contas.value.find((c) => c.id === 'k1')
    // nome igual: não salva
    t.s.salvarNome(zeta)
    assert.ok(!t.calls.some((c) => c.opts?.method === 'PUT'))
    // nome vazio: volta o antigo, não salva
    t.s.nomes.value = { ...t.s.nomes.value, k1: '   ' }
    t.s.salvarNome(zeta)
    assert.equal(t.s.nomes.value.k1, 'Zeta')
    assert.ok(!t.calls.some((c) => c.opts?.method === 'PUT'))
    // nome novo: PUT só com o nome
    t.s.nomes.value = { ...t.s.nomes.value, k1: '  Zeta Nova ' }
    t.s.salvarNome(zeta)
    await esperar()
    const put = t.calls.find((c) => c.opts?.method === 'PUT')
    assert.equal(put.url, `${BASE}/contas/k1`)
    assert.deepEqual(put.opts.body, { nome: 'Zeta Nova' })
    assert.equal(t.s.contas.value.find((c) => c.id === 'k1').nome, 'Zeta Nova')
    // grupo e ativo
    await t.s.salvarConta(t.s.contas.value.find((c) => c.id === 'k3'), { ativo: true })
    assert.deepEqual(t.calls.filter((c) => c.opts?.method === 'PUT').at(-1).opts.body, { ativo: true })
    assert.equal(t.s.contas.value.find((c) => c.id === 'k3').ativo, true)
  }

  // Conta: erro ao salvar volta o nome e avisa.
  {
    const t = await montar({ detalhes: DET, put: () => Promise.reject({ statusCode: 404, data: { detail: { code: 'conta_nao_encontrada' } } }) })
    t.s.contasAberto.value = true
    await esperar()
    await esperar()
    const alfa = t.s.contas.value.find((c) => c.id === 'k2')
    t.s.nomes.value = { ...t.s.nomes.value, k2: 'Outro' }
    t.s.salvarNome(alfa)
    await esperar()
    assert.equal(t.s.nomes.value.k2, 'Alfa', 'voltou o nome')
    assert.deepEqual(t.toastLog.at(-1), ['error', 'Não consegui salvar a conta', 'Essa conta não existe mais.'])
    assert.equal(t.s.salvandoConta.value, null)
  }

  // ════════════════════ Mercado Livre e Amazon (07/10/2026) ════════════════════
  await plataformas()

  console.log('marketing-conferencia-sfc: ok')
}

// ---------------------------------------------------------------- dados falsos do ML e da Amazon
const ID_ML = '66666666-6666-4666-8666-666666666666'
const ID_ML_VELHO = '77777777-7777-4777-8777-777777777777'
const ID_AMZ = '88888888-8888-4888-8888-888888888888'
const ID_ML_NOVO = '99999999-9999-4999-8999-999999999999'
const AFILIADOS = ['vendas_afiliados', 'impressoes_afiliados', 'conversao_afiliados', 'invest_afiliados', 'cliques_afiliados', 'pedidos_afiliados']
const ADS = ['vendas_ads', 'impressoes', 'conversao_ads', 'invest_ads', 'cliques_ads', 'pedidos_ads']
const de = (chaves, estado) => Object.fromEntries(chaves.map((k) => [k, estado]))
// ML: afiliados sem API ("não coletado"); Amazon: afiliados "não se aplica" e Ads "aguardando acesso".
const ESTADOS = {
  ml: { ...de(AFILIADOS, 'nao_coletado'), saldo_ads: 'nao_se_aplica' },
  amazon: { ...de(AFILIADOS, 'nao_se_aplica'), ...de(ADS, 'aguardando_acesso'), saldo_ads: 'nao_se_aplica' },
}
const semAfiliados = (over = {}) => vals({
  vendas_afiliados: null, invest_afiliados: null, cliques_afiliados: null, pedidos_afiliados: null,
  conversao_afiliados: null, saldo_ads: null, ...over,
})
function relatorioPlat(plataforma, id) {
  const sem4 = (v) => [semAfiliados({ vendas: v }), semAfiliados(), semAfiliados(), semAfiliados()]
  return relatorio({
    execucao_id: id, plataforma, estados: ESTADOS[plataforma],
    grupos: [
      { chave: 'mala', rotulo: 'Mala', linhas: [], total: total(2, 0, sem4(1300)) },
      { chave: 'celular', rotulo: 'Celular', linhas: [], total: total(3, 1, sem4(2600)) },
      { chave: 'eletro', rotulo: 'Eletro', linhas: [], total: total(1, 0, sem4(700)) },
    ],
    geral: total(5, 1, sem4(4600)),
    notas: ['Vendas: itens dos pedidos do Bling da semana, sem frete (dados falsos do teste).'],
    contas_sem_dados: [{ conta: 'Velasco', status: 'bloqueada', erro: 'Ads respondeu 403' }],
    afiliados_incompletos: [],
    divergencias: [],
    nao_atribuido_ads: [],
  })
}
function detalhePlat(plataforma, id, status) {
  return {
    execucao: {
      ...execResumo(id, status, '2026-10-06T16:31:00Z', { plataforma }),
      afiliados_ate: null, esperar_afiliados_ate: null, corte: null, prazo: null,
    },
    coletas: [
      coleta('Marquezini', 'ok', { grupo: 'mala', concluido_em: '2026-10-06T16:32:00Z' }),
      coleta('Velasco', status === 'coletando' ? 'coletando' : 'bloqueada', { erro: status === 'coletando' ? null : 'Ads respondeu 403' }),
    ],
    relatorio: status === 'pronto' ? relatorioPlat(plataforma, id) : null,
  }
}
const LISTA_ML = [
  execResumo(ID_ML, 'pronto', '2026-10-06T16:31:00Z', { plataforma: 'ml' }),
  execResumo(ID_ML_VELHO, 'pronto', '2026-10-01T16:31:00Z', { plataforma: 'ml' }),
]
const LISTA_AMZ = [execResumo(ID_AMZ, 'pronto', '2026-10-06T16:32:00Z', { plataforma: 'amazon' })]
const DET_PLAT = {
  [ID_ML]: detalhePlat('ml', ID_ML, 'pronto'),
  [ID_ML_VELHO]: detalhePlat('ml', ID_ML_VELHO, 'pronto'),
  [ID_AMZ]: detalhePlat('amazon', ID_AMZ, 'pronto'),
}
const contaPlat = (plataforma, id, nome, grupo, over = {}) => ({
  id, plataforma, adspower_user_id: null, nome, grupo, ativo: true, ordem: 0, conta_key: null, observacao: null,
  integration_id: `int-${id}`, integracao_nome: `${nome} (integração)`, bling_loja_id: `20500000${id.slice(-1)}`, ...over,
})
const CONTAS_ML = [
  contaPlat('ml', 'm1', 'Marquezini', 'mala'),
  contaPlat('ml', 'm2', 'Atlas', 'celular', {
    ativo: false, integration_id: null, integracao_nome: null, bling_loja_id: null,
    observacao: 'Conta nova do ML: ainda sem integração no DaVinci.',
  }),
  // Integração ligada, mas sem a loja do Bling: também não entra.
  contaPlat('ml', 'm3', 'Velasco', 'celular', { ativo: false, bling_loja_id: null, integracao_arquivada: true }),
]
const INTEGRACOES_ML = [
  { id: 'int-m1', nome: 'Marquezini (integração)', arquivada: false },
  { id: 'int-livre', nome: 'Atlas ML', arquivada: false },
  { id: 'int-m3', nome: 'Velasco (integração)', arquivada: true },
]
const CONTAS_AMZ = [contaPlat('amazon', 'a1', 'Kia', 'celular')]
const LISTAS = { ml: LISTA_ML, amazon: LISTA_AMZ }
const TODOS = { ...DET, ...DET_PLAT }

// A tabela das contas, renderizada com o estado da tela (SSR).
const renderContas = (() => {
  const ini = tpl.indexOf('<table class="w-full text-xs" :class="contasPorIntegracao ? \'min-w-[760px]\' : \'min-w-[640px]\'">')
  assert.ok(ini > 0, 'template tem a tabela das contas')
  const trecho = tpl.slice(ini, tpl.indexOf('</table>', ini) + '</table>'.length)
  const c = compileTemplate({ source: trecho, filename: 'contas.vue', id: 'contas-check' })
  assert.deepEqual(c.errors, [])
  const mod = {}
  new Function('exports', 'require', transpile(c.code))(mod, require)
  return mod.render
})()
function htmlContas(s) {
  const estado = {
    contasOrdenadas: s.contasOrdenadas, nomes: s.nomes, salvandoConta: s.salvandoConta, contasPorIntegracao: s.contasPorIntegracao,
    semVinculo: s.semVinculo, salvarNome: s.salvarNome, salvarConta: s.salvarConta, versaoContas: s.versaoContas,
    integracoes: s.integracoes, usadaPor: s.usadaPor, salvarIntegracao: s.salvarIntegracao, lojas: s.lojas, salvarLoja: s.salvarLoja,
  }
  const app = Vue.createSSRApp({ setup: () => estado, render: renderContas })
  app.component('Loader2', { render: () => null })
  return renderToString(app)
}

async function plataformas() {
  // Trocar de marketplace: o da Shopee sai todo, entra o último pronto do ML.
  {
    const t = await montar({ detalhes: TODOS, listas: LISTAS, query: { aba: 'conferencia', execucao: ID_PRONTO } })
    const { s } = t
    assert.equal(s.selecionada.value, ID_PRONTO)
    const antes = t.calls.length
    s.trocarPlataforma('ml')
    assert.equal(s.plataforma.value, 'ml')
    assert.equal(s.detalhe.value, null, 'o relatório da Shopee sai na hora')
    assert.deepEqual(t.routerCalls.at(-1), { query: { aba: 'conferencia', conf: 'ml' } }, '?conf=ml na URL; a execução da Shopee sai')
    await esperar()
    await esperar()
    assert.deepEqual(urls(t.calls.slice(antes)), [`GET ${LISTA_URL('ml')}`, `GET ${BASE}/execucoes/${ID_ML}`], 'lista e relatório do ML')
    assert.equal(s.selecionada.value, ID_ML, 'último pronto do ML')
    assert.deepEqual(s.opcoes.value.map((e) => e.id), [ID_ML, ID_ML_VELHO], 'seletor só com as do ML')
    assert.equal(s.plataformaAberta.value, 'ml')
    assert.deepEqual([s.plat.value.rotulo, s.plat.value.da], ['Mercado Livre', 'do Mercado Livre'])
    assert.equal(L.tituloRelatorio(s.rel.value.semanas, s.plataformaAberta.value), 'Conferência Mercado Livre — 28/09 a 04/10/2026')
    assert.equal(s.semDadosTxt.value, 'Velasco (bloqueada pelo Mercado Livre)', 'o motivo diz o marketplace')
    assert.equal(s.afiliadosIncompletosTxt.value, '')

    // A planilha: as 4 linhas de afiliados "não coletado"; Ads e Vendas com número.
    const p = s.planilha.value
    const lin = (cat, sub) => p.linhas.find((l) => l.categoria === cat && l.sub === sub)
    for (const cat of ['Vendas', 'Impressões', 'Conversão', 'Investimento']) {
      assert.equal(lin(cat, 'afiliados').estado, 'nao_coletado', `${cat} afiliados`)
      assert.equal(lin(cat, 'Ads').estado, null, `${cat} Ads tem número`)
    }
    assert.deepEqual(lin('Resumo', 'Vendas no período').valores[3], ['R$ 1.300,00', 'R$ 2.600,00', 'R$ 700,00', 'R$ 4.600,00'])
    const html = await renderPlanilha(s)
    const linhasHtml = html.split('<tbody>')[1].split('</tr>').filter((x) => x.includes('<td'))
    assert.equal(linhasHtml.length, 10, 'mesmo desenho da Shopee')
    for (const k of [0, 2, 4, 6]) {
      const l = linhasHtml[k]
      assert.equal((l.match(/<td/g) || []).length, 20, '4 semanas × 4 grupos + 4 variações')
      assert.equal((l.match(/<td class="estado( geral)?"/g) || []).length, 16, 'semanas: célula de estado')
      assert.equal((l.match(/<td class="var estado( geral)?"/g) || []).length, 4, 'variação: célula de estado')
      assert.equal((l.match(/<span class="pill-muted">não coletado<\/span>/g) || []).length, 20, 'pílula cinza em todas')
      assert.ok(!/>—</.test(l) && !/text-(emerald|red)/.test(l), 'nem "—" nem cor de variação')
    }
    for (const k of [1, 3, 5, 7, 8, 9]) assert.ok(!linhasHtml[k].includes('pill-muted'), `linha ${k} normal`)
    assert.equal((html.match(/class="pill-muted"/g) || []).length, 80)

    // O arquivo é pedido pela execução (a rota não muda); o nome sem cabeçalho está abaixo (Amazon).
    await s.baixar('xlsx')
    assert.ok(t.calls.some((c) => c.url === `${BASE}/execucoes/${ID_ML}/arquivo/xlsx`))

    // De volta pra Shopee: o ?conf= sai da URL e volta o último pronto da Shopee.
    s.trocarPlataforma('shopee')
    assert.deepEqual(t.routerCalls.at(-1), { query: { aba: 'conferencia' } })
    await esperar()
    await esperar()
    assert.equal(s.selecionada.value, ID_PRONTO)
    assert.equal(s.plataformaAberta.value, 'shopee')
    // Clicar no que já está escolhido não recarrega nada.
    const n = t.calls.length
    s.trocarPlataforma('shopee')
    assert.equal(t.calls.length, n)
  }

  // ?conf=amazon abre direto na Amazon: afiliados "não se aplica", Ads "aguardando acesso".
  {
    const t = await montar({ detalhes: TODOS, listas: LISTAS, query: { aba: 'conferencia', conf: 'amazon' }, arquivo: () => Promise.resolve(new Blob(['x'])) })
    const { s } = t
    assert.equal(s.plataforma.value, 'amazon')
    assert.deepEqual(urls(t.calls), [`GET ${LISTA_URL('amazon')}`, `GET ${BASE}/execucoes/${ID_AMZ}`])
    const p = s.planilha.value
    for (const l of p.linhas.slice(0, 8)) {
      const esperado = l.sub === 'afiliados' ? 'não se aplica' : 'aguardando acesso'
      assert.ok(l.valores.every((x) => x.every((v) => v === esperado)) && l.variacoes.every((v) => v.texto === esperado), `${l.categoria} ${l.sub}`)
    }
    assert.equal(p.linhas[9].estado, null, 'Vendas no período vem do Bling')
    const html = await renderPlanilha(s)
    assert.equal((html.match(/<span class="pill-muted">aguardando acesso<\/span>/g) || []).length, 80)
    assert.equal((html.match(/<span class="pill-muted">não se aplica<\/span>/g) || []).length, 80)
    // Sem Content-Disposition: o nome que o servidor daria.
    await s.baixar('csv')
    assert.equal(t.ancoras.at(-1).download, 'conferencia-amazon-2026-09-28_2026-10-04.csv')
    // ?conf= estranho cai na Shopee.
    const t2 = await montar({ detalhes: TODOS, listas: LISTAS, query: { conf: 'constructor' } })
    assert.equal(t2.s.plataforma.value, 'shopee')
    assert.equal(t2.calls[0].url, LISTA_URL('shopee'))
  }

  // Link do Threema de um relatório do ML sem ?conf=: a tela vai pro ML sozinha,
  // sem ler o relatório de novo, e o seletor passa a ser o do ML.
  {
    const t = await montar({ detalhes: TODOS, listas: LISTAS, query: { aba: 'conferencia', execucao: ID_ML_VELHO } })
    const { s } = t
    assert.equal(s.selecionada.value, ID_ML_VELHO)
    assert.equal(s.plataforma.value, 'ml', 'foi pro marketplace do relatório')
    assert.deepEqual(t.routerCalls.at(-1), { query: { aba: 'conferencia', execucao: ID_ML_VELHO, conf: 'ml' } }, 'a URL ganha o ?conf=, o link continua')
    await esperar()
    assert.deepEqual(urls(t.calls), [`GET ${LISTA_URL('shopee')}`, `GET ${BASE}/execucoes/${ID_ML_VELHO}`, `GET ${LISTA_URL('ml')}`])
    assert.deepEqual(s.opcoes.value.map((e) => e.id), [ID_ML, ID_ML_VELHO])
    assert.equal(s.exec.value.id, ID_ML_VELHO)
    // Com o ?conf= certo no link, nada muda de lugar.
    const t2 = await montar({ detalhes: TODOS, listas: LISTAS, query: { aba: 'conferencia', conf: 'ml', execucao: ID_ML_VELHO } })
    assert.equal(t2.routerCalls.length, 0)
    assert.deepEqual(urls(t2.calls), [`GET ${LISTA_URL('ml')}`, `GET ${BASE}/execucoes/${ID_ML_VELHO}`])
  }

  // Execução sem o campo plataforma (API antiga) na aba do ML: vale a aba.
  {
    const semCampo = JSON.parse(JSON.stringify(DET_PLAT[ID_ML]))
    delete semCampo.execucao.plataforma
    delete semCampo.relatorio.plataforma
    const t = await montar({ detalhes: { [ID_ML]: semCampo }, listas: LISTAS, query: { conf: 'ml' } })
    assert.equal(t.s.plataforma.value, 'ml')
    assert.equal(t.s.plataformaAberta.value, 'ml')
    assert.equal(t.routerCalls.length, 0)
  }

  // Resposta da Shopee que chega depois da troca não escreve na tela (nem puxa de volta).
  {
    let soltarDetalhe
    const det = { ...TODOS, [ID_PRONTO]: () => new Promise((r) => { soltarDetalhe = () => r(detalhe(ID_PRONTO, 'pronto')) }) }
    const t = await montar({ detalhes: det, listas: LISTAS })
    t.s.trocarPlataforma('ml')
    await esperar()
    await esperar()
    soltarDetalhe()
    await esperar()
    assert.equal(t.s.exec.value.id, ID_ML)
    assert.equal(t.s.plataforma.value, 'ml')

    let soltarLista
    const t2 = await montar({ lista: () => new Promise((r) => { soltarLista = () => r(LISTA) }), detalhes: TODOS, listas: LISTAS })
    t2.s.trocarPlataforma('ml')
    await esperar()
    await esperar()
    soltarLista()
    await esperar()
    await esperar()
    assert.deepEqual(t2.s.execucoes.value.map((e) => e.id), [ID_ML, ID_ML_VELHO], 'lista velha da Shopee não entra')
    assert.equal(t2.s.selecionada.value, ID_ML)
    assert.equal(t2.s.emAndamento.value, null, 'a coleta da Shopee não aparece no aviso do ML')

    // O relatório da Shopee chega ENQUANTO a lista do ML ainda carrega (nenhum
    // relatório do ML pedido ainda): também não entra, nem leva a tela de volta.
    let soltarDet3
    let soltarListaMl
    const t3 = await montar({
      detalhes: { ...TODOS, [ID_PRONTO]: () => new Promise((r) => { soltarDet3 = () => r(detalhe(ID_PRONTO, 'pronto')) }) },
      listas: { ml: () => new Promise((r) => { soltarListaMl = () => r(LISTA_ML) }) },
    })
    t3.s.trocarPlataforma('ml')
    await esperar()
    soltarDet3()
    await esperar()
    await esperar()
    assert.equal(t3.s.detalhe.value, null, 'relatório da Shopee descartado')
    assert.equal(t3.s.plataforma.value, 'ml', 'continua no ML')
    assert.equal(t3.routerCalls.at(-1).query.conf, 'ml')
    soltarListaMl()
    await esperar()
    await esperar()
    assert.equal(t3.s.exec.value.id, ID_ML)
  }

  // A lista da Shopee chega depois da troca, com a do ML ainda carregando: a tela
  // continua no esqueleto (não pisca "Nenhum relatório do Mercado Livre ainda").
  {
    let soltarShopee
    let soltarMl
    const t = await montar({
      lista: () => new Promise((r) => { soltarShopee = () => r(LISTA) }),
      listas: { ml: () => new Promise((r) => { soltarMl = () => r(LISTA_ML) }) },
      detalhes: TODOS,
    })
    t.s.trocarPlataforma('ml')
    await esperar()
    soltarShopee()
    await esperar()
    await esperar()
    assert.equal(t.s.listaCarregada.value, false, 'ainda carregando a do ML')
    assert.equal(t.s.carregandoLista.value, true)
    soltarMl()
    await esperar()
    await esperar()
    assert.equal(t.s.listaCarregada.value, true)
    assert.equal(t.s.selecionada.value, ID_ML)
  }

  // "Gerar agora" na Shopee e troca pro ML antes da resposta: a tela fica no ML.
  {
    let soltarPost
    const t = await montar({
      detalhes: TODOS, listas: LISTAS,
      post: () => new Promise((r) => { soltarPost = () => r({ execucao: { id: ID_NOVO } }) }),
    })
    const gerando = t.s.gerar()
    await esperar()
    t.s.trocarPlataforma('ml')
    await esperar()
    await esperar()
    soltarPost()
    await gerando
    await esperar()
    assert.equal(t.s.plataforma.value, 'ml')
    assert.equal(t.s.selecionada.value, ID_ML, 'não pulou pra execução nova da Shopee')
    assert.ok(!t.calls.some((c) => c.url === `${BASE}/execucoes/${ID_NOVO}`))
    assert.ok(t.calls.some((c) => c.url === `${BASE}/execucoes?plataforma=shopee` && c.opts?.method === 'POST'), 'a da Shopee foi gerada mesmo')
  }

  // Trocar no meio do acompanhamento: o timer da coleta da Shopee para.
  {
    const t = await montar({ lista: [LISTA[0]], detalhes: TODOS, listas: LISTAS })
    assert.deepEqual(t.relogio.pendentes().map((x) => x.ms), [20000])
    t.s.trocarPlataforma('amazon')
    assert.equal(t.relogio.pendentes().length, 0, 'parou de reler a da Shopee')
    await esperar()
    await esperar()
    assert.equal(t.s.exec.value.id, ID_AMZ)
  }

  // Gerar agora no ML: ?plataforma=ml (e no corpo), frases do servidor (sem AdsPower).
  {
    let corpo = null
    let plat = null
    let lista = [...LISTA_ML]
    const t = await montar({
      query: { aba: 'conferencia', conf: 'ml' },
      detalhes: { ...TODOS, [ID_ML_NOVO]: detalhePlat('ml', ID_ML_NOVO, 'coletando') },
      listas: { ml: () => lista },
      post: (body, p) => {
        corpo = body
        plat = p
        lista = [execResumo(ID_ML_NOVO, 'coletando', '2026-10-09T12:00:00Z', { plataforma: 'ml' }), ...lista]
        return Promise.resolve({ execucao: { id: ID_ML_NOVO } })
      },
    })
    await t.s.gerar()
    await esperar()
    assert.equal(plat, 'ml')
    assert.deepEqual(corpo, { tipo: 'semanal', plataforma: 'ml' })
    assert.match(t.confirms[0], /^Gerar agora a conferência do Mercado Livre da semana fechada/)
    assert.match(t.confirms[0], /servidor lê as vendas do Bling e o Ads do Mercado Livre pela API/)
    assert.ok(!t.confirms[0].includes('robô do Mac'), 'no ML não é o robô do Mac')
    assert.deepEqual(t.toastLog.at(-1), ['success', 'Conferência iniciada', t.s.COLETA_TXT.ml.iniciada])
    assert.equal(t.s.selecionada.value, ID_ML_NOVO)
    assert.deepEqual(t.routerCalls.at(-1), { query: { aba: 'conferencia', conf: 'ml', execucao: ID_ML_NOVO } })
    assert.equal(t.s.exec.value.status, 'coletando')
    assert.deepEqual(t.relogio.pendentes().map((x) => x.ms), [20000], 'acompanha a cada 20 s igual')
    for (const p of ['shopee', 'ml', 'amazon']) {
      assert.ok(t.s.COLETA_TXT[p].confirmar && t.s.COLETA_TXT[p].iniciada && t.s.COLETA_TXT[p].andamento, `frases de ${p}`)
    }
    assert.ok(!/AdsPower/.test(t.s.COLETA_TXT.amazon.andamento + t.s.COLETA_TXT.ml.andamento))

    // 409 de uma já coletando (na Amazon): leva até a que está coletando DESTE marketplace.
    const ID_AMZ_COLETANDO = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
    const t2 = await montar({
      query: { conf: 'amazon' },
      detalhes: { ...TODOS, [ID_AMZ_COLETANDO]: detalhePlat('amazon', ID_AMZ_COLETANDO, 'coletando') },
      listas: { amazon: [execResumo(ID_AMZ_COLETANDO, 'coletando', '2026-10-08T16:32:00Z', { plataforma: 'amazon' }), ...LISTA_AMZ] },
      post: () => Promise.reject({ statusCode: 409, data: { detail: { code: 'conferencia_em_andamento' } } }),
    })
    assert.equal(t2.s.selecionada.value, ID_AMZ, 'abriu no último pronto da Amazon')
    await t2.s.gerar()
    await esperar()
    assert.match(t2.confirms[0], /^Gerar agora a conferência da Amazon da semana fechada/)
    assert.match(t2.confirms[0], /O Ads da Amazon fica "aguardando acesso"/)
    assert.deepEqual(t2.toastLog.at(-1).slice(0, 2), ['warning', 'Já tem uma conferência coletando'])
    assert.equal(t2.s.selecionada.value, ID_AMZ_COLETANDO)
    assert.deepEqual(t2.calls.find((c) => c.opts?.method === 'POST').url, `${BASE}/execucoes?plataforma=amazon`)
  }

  // Contas do ML: por integração do DaVinci + loja do Bling, não por perfil do AdsPower.
  {
    const t = await montar({
      detalhes: TODOS, listas: LISTAS, contasPlat: { ml: CONTAS_ML, amazon: CONTAS_AMZ }, integracoesPlat: { ml: INTEGRACOES_ML },
    })
    const { s } = t
    s.contasAberto.value = true
    await esperar()
    await esperar()
    assert.ok(urls(t.calls).includes(`GET ${BASE}/contas?plataforma=shopee`))
    assert.equal(s.contasPorIntegracao.value, false)
    assert.equal(s.semVinculo(s.contas.value[0]), false, 'Shopee não depende de integração')
    assert.ok(!urls(t.calls).some((u) => u.includes('/contas/integracoes')), 'Shopee não lista integrações')
    let html = await htmlContas(s)
    assert.match(html, /<th>Perfil AdsPower<\/th>/)
    assert.ok(html.includes('perfil1'), 'Shopee mostra o perfil do AdsPower')
    assert.ok(!html.includes('Integração no DaVinci'))

    // Trocou com a seção aberta: as contas do ML entram no lugar.
    s.trocarPlataforma('ml')
    await esperar()
    await esperar()
    assert.ok(urls(t.calls).includes(`GET ${BASE}/contas?plataforma=ml`))
    assert.ok(urls(t.calls).includes(`GET ${BASE}/contas/integracoes?plataforma=ml`), 'lista as integrações do ML')
    assert.deepEqual(s.contasOrdenadas.value.map((c) => c.nome), ['Marquezini', 'Atlas', 'Velasco'], 'Mala antes')
    assert.equal(s.contasPorIntegracao.value, true)
    const [marq, atlas, velasco] = s.contasOrdenadas.value
    assert.equal(s.semVinculo(marq), false)
    assert.equal(s.semVinculo(atlas), true, 'sem integração e sem loja')
    assert.equal(s.semVinculo(velasco), true, 'com integração, sem loja do Bling')
    assert.equal(s.usadaPor('int-m1', 'm2'), 'Marquezini', 'integração já usada por outra conta')
    assert.equal(s.usadaPor('int-m1', 'm1'), null, 'a própria não conta')
    assert.equal(s.usadaPor('int-livre', 'm2'), null)
    assert.deepEqual(s.lojas.value, { m1: '205000001', m2: '', m3: '' }, 'rascunho da loja do Bling')
    html = await htmlContas(s)
    assert.match(html, /<th>Integração no DaVinci<\/th>\s*<th>Loja no Bling<\/th>/)
    assert.ok(!html.includes('Perfil AdsPower'))
    const linhas = html.split('<tbody>')[1].split('</tr>').filter((x) => x.includes('<td'))
    const caixa = (l) => l.split('type="checkbox"')[1].split('>')[0]
    // Marquezini: a integração dela escolhida no select; a loja no campo.
    // (no SSR o valor escolhido sai no value do <select>)
    assert.match(linhas[0], /<select value="int-m1" [^>]*aria-label="Integração no DaVinci">/)
    assert.match(linhas[0], /<option value="int-m1">Marquezini \(integração\)<\/option>/)
    assert.match(linhas[0], /value="205000001"/)
    assert.ok(!/disabled/.test(caixa(linhas[0])), 'Marquezini pode sair/entrar')
    // Atlas: nada ligado; a integração da Marquezini aparece mas não dá pra escolher.
    assert.match(linhas[1], /<select value(="")? [^>]*aria-label="Integração no DaVinci"><option value(="")?>— sem integração —<\/option>/)
    assert.match(linhas[1], /<option value="int-m1" disabled>Marquezini \(integração\) — já em Marquezini<\/option>/)
    assert.match(linhas[1], /<option value="int-livre">Atlas ML<\/option>/)
    assert.match(linhas[1], /Conta nova do ML: ainda sem integração no DaVinci\./, 'a observação aparece')
    assert.match(caixa(linhas[1]), /disabled/, 'sem integração não dá pra ativar')
    assert.match(linhas[1], /cursor-not-allowed/)
    // Velasco: integração arquivada marcada; sem loja, não entra.
    assert.match(linhas[2], /Velasco \(integração\) \(arquivada\)/)
    assert.match(linhas[2], /<span class="pill-warning ml-1">arquivada<\/span>/)
    assert.match(caixa(linhas[2]), /disabled/, 'sem loja do Bling não dá pra ativar')

    // Salvar continua pelo id (a rota não muda).
    await s.salvarConta(marq, { grupo: 'celular' })
    const puts = () => t.calls.filter((c) => c.opts?.method === 'PUT')
    assert.deepEqual(puts().at(-1).url, `${BASE}/contas/m1`)

    // Ligar a integração: PUT só com ela; escolher a mesma não salva.
    const atlasAgora = () => s.contas.value.find((c) => c.id === 'm2')
    s.salvarIntegracao(atlasAgora(), 'int-livre')
    await esperar()
    assert.deepEqual([puts().at(-1).url, puts().at(-1).opts.body], [`${BASE}/contas/m2`, { integration_id: 'int-livre' }])
    assert.equal(atlasAgora().integration_id, 'int-livre')
    const n = puts().length
    s.salvarIntegracao(atlasAgora(), 'int-livre')
    assert.equal(puts().length, n, 'mesma integração: nada')
    // Loja do Bling: só número; vazio desliga; igual não salva.
    s.lojas.value = { ...s.lojas.value, m2: ' 20a ' }
    s.salvarLoja(atlasAgora())
    assert.equal(puts().length, n, 'loja com letra não vai')
    assert.equal(s.lojas.value.m2, '', 'voltou o que estava')
    assert.deepEqual(t.toastLog.at(-1).slice(0, 2), ['error', 'Loja do Bling inválida'])
    s.lojas.value = { ...s.lojas.value, m2: ' 204438129 ' }
    s.salvarLoja(atlasAgora())
    await esperar()
    assert.deepEqual(puts().at(-1).opts.body, { bling_loja_id: '204438129' })
    assert.equal(s.semVinculo(atlasAgora()), false, 'agora dá pra ativar')
    s.salvarLoja(atlasAgora())
    assert.equal(puts().length, n + 1, 'loja igual: nada')
    s.lojas.value = { ...s.lojas.value, m2: '' }
    s.salvarLoja(atlasAgora())
    await esperar()
    assert.deepEqual(puts().at(-1).opts.body, { bling_loja_id: null }, 'vazio desliga')

    // Contas da Shopee que chegam depois da troca não entram no lugar das da Amazon.
    let soltar
    const t2 = await montar({
      detalhes: TODOS, listas: LISTAS,
      contasResp: () => new Promise((r) => { soltar = () => r(JSON.parse(JSON.stringify(CONTAS))) }),
      contasPlat: { amazon: CONTAS_AMZ },
    })
    t2.s.contasAberto.value = true
    await esperar()
    assert.equal(typeof soltar, 'function', 'pediu as da Shopee')
    t2.s.trocarPlataforma('amazon')
    await esperar()
    await esperar()
    soltar()
    await esperar()
    assert.deepEqual(t2.s.contas.value.map((c) => c.nome), ['Kia'])
    assert.deepEqual(Object.keys(t2.s.nomes.value), ['a1'])

    // O servidor recusou (ativar sem a loja do Bling): frase certa, rascunho volta e as linhas redesenham.
    const t3 = await montar({
      query: { conf: 'ml' }, detalhes: TODOS, listas: LISTAS, contasPlat: { ml: CONTAS_ML },
      integracoesPlat: { ml: () => Promise.reject({ statusCode: 500 }) },
      put: () => Promise.reject({ statusCode: 422, data: { detail: { code: 'conta_sem_loja_bling' } } }),
    })
    t3.s.contasAberto.value = true
    await esperar()
    await esperar()
    assert.equal(t3.s.integracoes.value, null, 'lista de integrações falhou: fica só o nome')
    const htmlSemLista = await htmlContas(t3.s)
    assert.ok(!htmlSemLista.includes('<select aria-label="Integração no DaVinci"') && !/Integração no DaVinci"[^>]*>\s*<option/.test(htmlSemLista))
    assert.match(htmlSemLista, /<span class="text-foreground">Marquezini \(integração\)<\/span>/)
    assert.match(htmlSemLista, /<span class="pill-muted">sem integração<\/span>/)
    const vel = t3.s.contas.value.find((c) => c.id === 'm3')
    t3.s.lojas.value = { ...t3.s.lojas.value, m3: '999' }
    const versao = t3.s.versaoContas.value
    t3.s.salvarLoja(vel)
    await esperar()
    await esperar()
    assert.deepEqual(t3.toastLog.at(-1), ['error', 'Não consegui salvar a conta', L.ERROS_CONFERENCIA.conta_sem_loja_bling])
    assert.equal(t3.s.lojas.value.m3, '', 'rascunho voltou')
    assert.equal(t3.s.versaoContas.value, versao + 1, 'linhas redesenham (select e caixinha voltam)')
    // Trocou de marketplace: as integrações do ML não ficam no select da Amazon.
    const t4 = await montar({
      query: { conf: 'ml' }, detalhes: TODOS, listas: LISTAS, contasPlat: { ml: CONTAS_ML, amazon: CONTAS_AMZ },
      integracoesPlat: { ml: INTEGRACOES_ML, amazon: () => Promise.reject({ statusCode: 500 }) },
    })
    t4.s.contasAberto.value = true
    await esperar()
    await esperar()
    assert.equal(t4.s.integracoes.value.length, 3)
    t4.s.trocarPlataforma('amazon')
    await esperar()
    await esperar()
    assert.equal(t4.s.integracoes.value, null, 'as do ML saíram')
    assert.deepEqual(t4.s.contas.value.map((c) => c.nome), ['Kia'])
    for (const code of ['conta_sem_integracao', 'conta_sem_loja_bling', 'integracao_em_uso', 'loja_bling_em_uso', 'integracao_invalida', 'campo_so_ml_amazon', 'plataforma_invalida']) {
      assert.ok(L.ERROS_CONFERENCIA[code], `frase para ${code}`)
    }
  }
}

run().catch((e) => {
  console.error(e)
  process.exit(1)
})
