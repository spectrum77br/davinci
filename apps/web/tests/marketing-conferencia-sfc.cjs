// Run from apps/web: node tests/marketing-conferencia-sfc.cjs
//
// Marketing › Conferência Shopee (06/10/2026): components/MarketingConferencia.vue,
// a aba nova em pages/marketing.vue e o contexto do Threema.
//
// Trava o que a tela promete:
//  - abre no último relatório PRONTO, ou no ?execucao= do link do Threema
//    (mesmo uma execução mais velha que as 30 da lista);
//  - enquanto coleta, relê a cada 20 s — só com a aba visível e montada — e,
//    quando termina, avisa e mostra o relatório;
//  - "Gerar agora" pede confirmação, manda o tipo e trata o 409 de "já tem uma
//    coletando" levando a pessoa até ela;
//  - os arquivos saem com o nome do servidor; o HTML abre numa aba nova;
//  - conta sem dados continua na tabela; cartões, tabelas e "Últimas 4
//    semanas" usam as regras de lib/conferencia.ts;
//  - só quem edita gera, recalcula, cancela e mexe nas contas; o cadastro do
//    Threema é só de admin (routers/informar.py).
// Só dados FALSOS aqui; nenhuma rede.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { parse, compileTemplate, compileScript } = require('vue/compiler-sfc')

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
  '`${BASE}/execucoes?limite=30`',
  '`${BASE}/execucoes/${encodeURIComponent(id)}`',
  '`${BASE}/execucoes`, { method: \'POST\', body: { tipo: tipoNovo.value } }',
  '/recalcular`, { method: \'POST\' }',
  '/cancelar`, { method: \'POST\' }',
  '/arquivo/${fmt}`',
  '`${BASE}/contas`',
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
// Layout do relatório (contrato §5 / COMO-MONTA-O-RELATORIO §6).
for (const trecho of [
  'tituloRelatorio(rel.semanas)', 'comparadoCom(rel.semanas)', 'gerado em', // título
  'v-for="c in cartoes"', // cartões Mala · Celular · Eletro · Geral
  'ant. {{ c.anterior }}', // "ant. <valor>" embaixo de cada célula
  'Total {{ g.rotulo }} ({{ contasTxt(g.total?.contas) }})', // "Total Mala (4 contas)"
  '⚠︎ {{ a }}', // aviso da linha (afiliados só até dd/mm)
  'sem dados: ${rotuloStatusColeta(l.status)}', // motivo de conta sem dados
  'Últimas 4 semanas', 'vs semana anterior', 'vs média 3 sem.', 'Por conta',
  'Notas', 'Contas sem dados', 'Afiliados incompletos', 'Gasto de Ads sem produto',
  'Nenhum relatório ainda', // estado vazio
  'Não consegui carregar a conferência agora.', // erro
  'animate-pulse', // esqueleto
  'Contas da conferência',
]) assert.ok(tpl.includes(trecho), `na tela: ${trecho}`)
// Celular: tabelas largas sempre dentro de rolagem horizontal.
const tabelas = tpl.match(/<table /g).length
const rolagens = tpl.match(/table-card[^"]*overflow-x-auto|overflow-x-auto[^"]*table-card/g).length
assert.equal(rolagens, tabelas, 'toda tabela dentro de overflow-x-auto')
// Cor fixa sempre com a variante do escuro.
for (const m of tpl.matchAll(/(?<!dark:)\btext-(?:emerald|red|amber)-\d00\b(?![^"]*dark:)/g)) {
  assert.fail(`cor sem dark: perto de "${tpl.slice(m.index - 40, m.index + 40)}"`)
}

// ---------------------------------------------------------------- página: a aba nova
{
  const s = pagina.script
  const t = pagina.tpl
  assert.match(s, /type Platform = [^\n]*\| 'conferencia'/, 'Platform tem conferencia')
  assert.match(s, /const emOutraAba = computed\([\s\S]{0,300}platform\.value === 'conferencia'/, 'emOutraAba inclui conferencia')
  assert.match(s, /const canConferencia = useCan\('marketing', 'view'\)/, 'permissão da aba')
  assert.match(s, /if \(!canAds\.value && !canCriativos\.value && !canConferencia\.value\) \{\s*await navigateTo\('\/403'\)/, 'guarda 403')
  // O watch que recarrega o Ads usa emOutraAba (antes Desempenho recarregava o Ads).
  assert.match(s, /watch\(platform, async \(\) => \{[\s\S]{0,300}if \(emOutraAba\.value \|\| !canAds\.value\) return/, 'watch usa emOutraAba')
  // ?aba= lido no setup e escrito na troca; ?execucao sai fora da Conferência.
  assert.match(s, /const platform = ref<Platform>\(abaDaUrl\(\) \?\? 'ml'\)/, 'lê ?aba=')
  assert.match(s, /if \(q === 'conferencia' && canConferencia\.value\) return q/, 'aba só se puder ver')
  assert.match(s, /if \(p !== 'conferencia'\) delete query\.execucao/, 'execucao só na aba')
  assert.match(s, /void router\.replace\(\{ query \}\)/, 'escreve a aba na URL')
  // Summary/timeseries não pedem platform=conferencia.
  assert.match(s, /platform=\$\{plataformaAds\.value\}/, 'summary pela plataforma de Ads')
  assert.match(s, /platform: plataformaAds\.value/, 'timeseries pela plataforma de Ads')
  assert.match(t, /<button v-if="canConferencia"[\s\S]{0,400}platform = 'conferencia'[\s\S]{0,200}Conferência Shopee/, 'botão da aba')
  assert.match(t, /<MarketingConferencia v-else-if="platform === 'conferencia' && canConferencia" \/>/, 'render da aba')
  assert.match(t, /\|\| canCriativos \|\| canConferencia" class="flex flex-wrap items-center gap-3">/, 'barra de abas aparece pra quem só vê a Conferência')

  // abaDaUrl de verdade, com permissões falsas.
  const ini = s.indexOf('function abaDaUrl')
  const corpo = transpile(s.slice(ini, s.indexOf('const platform = ref')), ts.ModuleKind.ESNext)
  const aba = (q, perms) => new Function('route', 'canAds', 'canCriativos', 'canConferencia', `${corpo}; return abaDaUrl()`)(
    { query: q }, { value: !!perms.ads }, { value: !!perms.criativos }, { value: !!perms.conferencia },
  )
  assert.equal(aba({ aba: 'conferencia' }, { ads: true, conferencia: true }), 'conferencia')
  assert.equal(aba({ aba: 'conferencia' }, { criativos: true }), null, 'sem permissão cai no padrão')
  assert.equal(aba({ aba: 'roteiros' }, { criativos: true }), 'roteiros')
  assert.equal(aba({ aba: 'shopee' }, { ads: true }), 'shopee')
  assert.equal(aba({ aba: 'qualquer' }, { ads: true }), null)
  assert.equal(aba({}, { ads: true }), null)
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
  invest_afiliados: 50, invest_ads: 30, pct: 8, vendas: 1000, ...over,
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
  cartoes, tabelas, quatroSemanas, porConta, semanasCab, afiliadosIncompletosTxt, naoAtribuidoTxt, categoriaTxt, semDados,
  contasAberto, contas, contasOrdenadas, contasErro, nomes, salvarConta, salvarNome, salvandoConta,
  isAdmin, informarAberto, COR,
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

async function montar({
  lista = LISTA, detalhes = {}, query = {}, canEdit = true, admin = true, confirmar = true,
  post, contasResp = CONTAS, put, listaErro = null, arquivo,
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
  const api = (url, opts = {}) => {
    calls.push({ url, opts })
    const m = opts.method || 'GET'
    if (m === 'GET' && url === `${BASE}/execucoes?limite=30`) {
      return listaErro ? Promise.reject(listaErro) : Promise.resolve(typeof lista === 'function' ? lista() : lista)
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
    if (m === 'POST' && url === `${BASE}/execucoes`) return post ? post(opts.body) : Promise.reject(new Error('sem POST'))
    if (m === 'POST' && /\/(recalcular|cancelar)$/.test(url)) return Promise.resolve({ ok: true })
    if (m === 'GET' && url === `${BASE}/contas`) return Promise.resolve(JSON.parse(JSON.stringify(contasResp)))
    const conta = new RegExp(`^${BASE}/contas/([^/]+)$`).exec(url)
    if (m === 'PUT' && conta) {
      if (put) return put(conta[1], opts.body)
      const c = contasResp.find((x) => x.id === conta[1])
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
const DET = {
  [ID_PRONTO]: detalhe(ID_PRONTO, 'pronto'),
  [ID_VELHO]: detalhe(ID_VELHO, 'pronto'),
  [ID_COLETANDO]: detalhe(ID_COLETANDO, 'coletando'),
  [ID_LINK]: detalhe(ID_LINK, 'pronto'),
}

async function run() {
  // Abre no último PRONTO (não na coleta em andamento, que fica num aviso).
  {
    const t = await montar({ detalhes: DET })
    const { s } = t
    assert.equal(s.selecionada.value, ID_PRONTO, 'último pronto')
    assert.deepEqual(urls(t.calls), [`GET ${BASE}/execucoes?limite=30`, `GET ${BASE}/execucoes/${ID_PRONTO}`])
    assert.equal(s.emAndamento.value.id, ID_COLETANDO, 'aviso da coleta em andamento')
    assert.equal(t.routerCalls.length, 0, 'escolha automática não mexe na URL')
    assert.equal(t.relogio.pendentes().length, 0, 'relatório pronto não fica relendo')
    assert.ok(t.ouvintes.has('visibilitychange'), 'ouve a visibilidade')

    // Cartões: Mala · Celular · Eletro · Geral, com Vendas / Investimento / %.
    const c = s.cartoes.value
    assert.deepEqual(c.map((x) => x.rotulo), ['Mala', 'Celular', 'Eletro', 'Geral'])
    assert.deepEqual(c[3].linhas.map((l) => l.rotulo), ['Vendas', 'Investimento', '% s/ vendas'])
    assert.deepEqual(
      c[3].linhas.map((l) => [l.valor, l.anterior, l.variacao.texto, l.variacao.cor]),
      [
        ['R$ 4.400,00', 'R$ 3.600,00', '▲ 22,2%', 'verde'],
        ['R$ 240,00', 'R$ 80,00', '▲ 200,0%', 'cinza'],
        ['5,5%', '8,0%', '▼ 2,6 p.p.', 'verde'], // 8 − 5,45 = 2,55: meio pra longe do zero (igual ao servidor)
      ],
    )
    assert.equal(c[0].semDados, 1)

    // Tabela do grupo: 8 colunas na ordem; célula com "ant." e variação.
    const mala = s.tabelas.value[0]
    assert.equal(mala.linhas.length, 2, 'conta sem dados continua na tabela')
    const alfa = mala.linhas[0]
    assert.equal(alfa.celulas.length, 8)
    const iVendas = L.METRICAS.findIndex((m) => m.chave === 'vendas')
    assert.deepEqual([alfa.celulas[iVendas].valor, alfa.celulas[iVendas].anterior, alfa.celulas[iVendas].variacao.texto], ['R$ 1.200,00', 'R$ 1.000,00', '▲ 20,0%'])
    const iPct = L.METRICAS.findIndex((m) => m.chave === 'pct')
    assert.deepEqual([alfa.celulas[iPct].valor, alfa.celulas[iPct].variacao.texto, alfa.celulas[iPct].variacao.cor], ['6,7%', '▼ 1,3 p.p.', 'verde'])
    const beta = mala.linhas[1]
    assert.equal(s.semDados(beta), true)
    assert.ok(beta.celulas.every((x) => x.valor === '—' && x.vazia), 'sem dados: tudo "—", sem linha "ant."')
    assert.equal(s.semDados(s.tabelas.value[1].linhas[0]), false, 'parcial tem dados')
    const iSaldo = L.METRICAS.findIndex((m) => m.chave === 'saldo_ads')
    assert.equal(s.tabelas.value[2].linhas[0].celulas[iSaldo].valor, '—', 'Eletro: saldo "—"')
    assert.equal(mala.celulasTotal[iVendas].valor, 'R$ 1.200,00', 'linha de total')

    // Últimas 4 semanas: grupos + Geral, métrica × S1..S4 + as 2 variações.
    const q = s.quatroSemanas.value
    assert.deepEqual(q.map((b) => b.rotulo), ['Mala', 'Celular', 'Eletro', 'Geral'])
    const vendasMala = q[0].linhas.find((l) => l.chave === 'vendas')
    assert.deepEqual(vendasMala.valores, ['R$ 1.200,00', 'R$ 1.000,00', 'R$ 900,00', 'R$ 1.100,00'])
    assert.equal(vendasMala.vsAnterior.texto, '▲ 20,0%')
    assert.equal(vendasMala.vsMedia.texto, '▲ 20,0%', 'média de 1000, 900 e 1100')
    assert.deepEqual(s.semanasCab.value.map((x) => `${x.nome} ${x.datas}`), ['S1 28/09–04/10', 'S2 21/09–27/09', 'S3 14/09–20/09', 'S4 07/09–13/09'])

    // Por conta: vendas S1..S4, as 2 variações e o % de cada semana.
    const pc = s.porConta.value
    assert.deepEqual(pc.map((g) => g.chave), ['mala', 'celular', 'eletro'], 'separado por grupo')
    const pAlfa = pc[0].linhas[0]
    assert.deepEqual(pAlfa.vendas, ['R$ 1.200,00', 'R$ 1.000,00', 'R$ 900,00', 'R$ 1.100,00'])
    assert.equal(pAlfa.vsMedia.texto, '▲ 20,0%')
    assert.deepEqual(pAlfa.pcts, ['6,7%', '8,0%', '8,0%', '8,0%'])

    // Notas
    assert.equal(s.afiliadosIncompletosTxt.value, 'Alfa (até 03/10)')
    assert.equal(s.naoAtribuidoTxt.value, 'Gama R$ 12,30')
    assert.equal(s.categoriaTxt(100010), 'Eletrodomésticos (100010)')
    assert.equal(s.categoriaTxt(123), '123')
    assert.equal(s.categoriaTxt(null), '—')

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
    assert.ok(urls(t.calls.slice(antes)).includes(`GET ${BASE}/execucoes?limite=30`), 'lista recarregada')
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
    assert.deepEqual(corpo, { tipo: 'parcial' })
    assert.equal(t.s.selecionada.value, ID_NOVO)
    assert.deepEqual(t.routerCalls.at(-1), { query: { execucao: ID_NOVO } })
    assert.equal(t.toastLog.at(-1)[1], 'Conferência iniciada')
  }

  // Gerar agora: não confirmou, nada sai.
  {
    const t = await montar({ detalhes: DET, confirmar: false, post: () => assert.fail('não podia gerar') })
    await t.s.gerar()
    assert.ok(!urls(t.calls).includes(`POST ${BASE}/execucoes`))
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
    assert.ok(!urls(t.calls).includes(`GET ${BASE}/contas`), 'só carrega ao abrir')
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

  console.log('marketing-conferencia-sfc: ok')
}

run().catch((e) => {
  console.error(e)
  process.exit(1)
})
