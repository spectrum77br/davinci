// Executa o setup real da página com rede controlada: navegar não espera
// pelo resumo, e a primeira busca respeita o filtro salvo sem duplicá-la.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { parse, compileScript } = require('vue/compiler-sfc')

const filename = path.resolve(__dirname, '../pages/atendimento.vue')
const source = fs.readFileSync(filename, 'utf8')
const { descriptor } = parse(source, { filename })
const compiled = compileScript(descriptor, { id: 'entrada' }).content.replaceAll('import.meta.client', 'true')
const script = ts.transpileModule(compiled, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText
const settle = async () => { for (let i = 0; i < 25; i++) await Promise.resolve(); await Vue.nextTick() }
const deferred = () => {
  let resolve, reject
  const promise = new Promise((a, b) => { resolve = a; reject = b })
  return { promise, resolve, reject }
}

function mount({ tab, conversation, saved, intercept } = {}) {
  const mounted = [], unmounted = [], calls = []
  const stored = new Map(saved ? [['davinci.atendimento.filtros', JSON.stringify(saved)]] : [])
  const api = async (url, options) => {
    calls.push({ url, options })
    const value = intercept?.(url, options)
    if (value !== undefined) return value
    if (url.startsWith('/api/atendimento/conversas?')) return { itens: [{ id: 'first' }], proximo: null }
    if (url === '/api/atendimento/resumo') return { lojas: [], nao_lidas_duoke: 3 }
    if (url.startsWith('/api/atendimento/email/contagens')) return { aguardando: 1, chamados: 0 }
    if (url === '/api/atendimento/modelos') return [{ id: 'model1' }]
    if (url === '/api/atendimento/categorias') return []
    throw Error(`Unexpected ${url}`)
  }
  const platform = {
    AVISO_SO_LEITURA: '', avisoSimulador: () => null, categoriasDe: (v) => v,
    comoLista: (v) => Array.isArray(v) ? v : v?.modelos || [],
    erroDaApi: () => ({ texto: 'Falha controlada' }), leiturasParadas: () => [],
    podeCosturar: () => true, registrarCategorias: () => {}, tituloLeituraParada: () => '',
    useRelogio: () => Vue.ref(0), usePollingVisivel: () => ({ marcar: () => {} }),
    vemDepoisNaLista: () => true,
  }
  const filter = { GRUPOS_CAIXA: [], plataformasDoValor: (v) => v ? v.split(',') : [], topoDoValor: (v) => v }
  const requiring = (name) => {
    if (name === 'lucide-vue-next') return {}
    if (name === '@vueuse/core') return { useResizeObserver: () => {}, useWindowSize: () => ({ height: Vue.ref(0), width: Vue.ref(1440) }) }
    if (name.includes('AtendimentoPlataforma.vue')) return platform
    if (name.includes('AtendimentoFiltroPlataforma.vue')) return filter
    // As caixas dos sites (27/10/2026): só as funções que a página usa.
    if (name.includes('AtendimentoLojas.vue')) return { emailsDoSite: (s) => ({ ...s }), gruposDoSite: () => [] }
    return require(name)
  }
  const globals = {
    ref: Vue.ref, computed: Vue.computed, watch: Vue.watch, nextTick: Vue.nextTick,
    onMounted: (fn) => mounted.push(fn), onBeforeUnmount: (fn) => unmounted.push(fn),
    definePageMeta: () => {}, useApi: () => ({ api }),
    useRoute: () => ({ query: { ...(tab ? { tab } : {}), ...(conversation ? { conversa: conversation } : {}) } }),
    useRouter: () => ({ replace: () => Promise.resolve() }),
    useAuthStore: () => ({ user: { id: 'operator', atendimento: true, atendimento_mexe: true } }),
    useCan: () => Vue.ref(true), useIsAdmin: () => Vue.ref(true),
    localStorage: { getItem: (key) => stored.get(key) || null, setItem: (key, value) => stored.set(key, value) },
    window: { innerWidth: 1600, scrollY: 0, innerHeight: 900 },
  }
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', ...Object.keys(globals), script)(requiring, mod, mod.exports, ...Object.values(globals))
  const scope = Vue.effectScope()
  const raw = scope.run(() => mod.exports.default.setup({}, { expose: () => {} }))
  assert.ok(!(raw instanceof Promise), 'a rota monta sem aguardar chamadas de rede')
  const state = Vue.proxyRefs(raw)
  return { state, calls, count: (part) => calls.filter((c) => c.url.includes(part)).length,
    start() { for (const callback of mounted) void callback() },
    stop() { unmounted.forEach((fn) => fn()); scope.stop() },
  }
}

async function run() {
  {
    const summary = deferred(), emailCounts = deferred()
    const m = mount({ saved: { plataforma: 'shopee', integration_id: 'store1', filtro: 'aguardando' }, intercept: (url) => {
      if (url === '/api/atendimento/resumo') return summary.promise
      if (url.startsWith('/api/atendimento/email/contagens')) return emailCounts.promise
    } })
    assert.equal(m.calls.length, 0, 'nenhum pedido de dados durante SSR/setup')
    assert.equal(m.state.emailContagensProntas, false)
    assert.equal(m.state.carregando, true, 'a primeira lista já tem indicador de carregamento')
    m.start(); await settle()
    const lists = m.calls.filter((c) => c.url.includes('/conversas?'))
    assert.equal(lists.length, 1, 'restaurar filtro não dispara lista duplicada')
    const query = new URL(lists[0].url, 'https://example.test').searchParams
    assert.equal(query.get('plataforma'), 'shopee'); assert.equal(query.get('integration_id'), 'store1')
    assert.equal(query.get('filtro'), 'todas', 'a aba começa em Todas, sem restaurar o filtro antigo')
    assert.deepEqual(m.state.itens.map((c) => c.id), ['first'], 'lista aparece com resumo e contador ainda pendentes')
    assert.equal(m.state.carregando, false)
    assert.equal(m.count('/modelos'), 0, 'respostas prontas não atrasam a abertura sem conversa')
    summary.resolve({ lojas: [] }); emailCounts.resolve({ aguardando: 0, chamados: 0 }); await settle()
    assert.equal(m.state.emailContagensProntas, true, 'E-mail pode decidir sua seção após o contador')
    m.stop()
  }
  {
    const m = mount({ tab: 'mail', saved: { plataforma: 'shopee', integration_id: 'store1' } })
    m.start(); await settle()
    assert.equal(m.count('/conversas?'), 0, 'abrir E-mail não busca lista escondida da Caixa')
    assert.equal(m.count('/modelos'), 0)
    m.state.aba = 'caixa'; await settle()
    assert.equal(m.count('/conversas?'), 1, 'primeira visita à Caixa busca sua lista')
    m.stop()
  }
  {
    const models = deferred()
    const m = mount({ tab: 'mail', conversation: 'email1', intercept: (url) => url === '/api/atendimento/modelos' ? models.promise : undefined })
    m.start(); await settle()
    assert.equal(m.count('/conversas?'), 0)
    assert.equal(m.count('/modelos'), 1, 'deep link da conversa de e-mail busca respostas prontas')
    m.state.emailConversa = 'email2'; await settle()
    assert.equal(m.count('/modelos'), 1, 'trocas rápidas compartilham os metadados em voo')
    models.resolve([{ id: 'answer' }]); await settle()
    m.state.emailConversa = 'email3'; await settle()
    assert.equal(m.count('/modelos'), 1, 'metadados resolvidos são reaproveitados nesta visita')
    await m.state.atualizarTudo()
    assert.equal(m.count('/modelos'), 2, 'atualização manual permite reler os modelos')
    m.stop()
  }
  {
    const first = deferred()
    let held = false
    const m = mount({ intercept: (url) => {
      if (url.includes('/conversas?') && !held) { held = true; return first.promise }
    } })
    m.start(); await settle()
    m.state.filtros = { ...m.state.filtros, integration_id: 'store2' }; await settle()
    assert.equal(m.count('/conversas?'), 2)
    first.resolve({ itens: [{ id: 'stale' }], proximo: null }); await settle()
    assert.deepEqual(m.state.itens.map((c) => c.id), ['first'], 'resposta antiga não sobrescreve a loja nova')
    assert.equal(m.state.carregando, false)
    m.stop()
  }
  {
    const oldSummary = deferred(), freshSummary = deferred(), oldEmail = deferred(), freshEmail = deferred()
    let summaries = 0, emails = 0
    const m = mount({ intercept: (url) => {
      if (url === '/api/atendimento/resumo') return ++summaries === 1 ? oldSummary.promise : freshSummary.promise
      if (url.startsWith('/api/atendimento/email/contagens')) return ++emails === 1 ? oldEmail.promise : freshEmail.promise
    } })
    m.start(); await settle()
    const summaryRefresh = m.state.carregarResumo(), emailRefresh = m.state.carregarContagensEmail()
    freshSummary.resolve({ lojas: [], nao_lidas_duoke: 0 }); freshEmail.resolve({ aguardando: 0, chamados: 0 })
    await summaryRefresh; await emailRefresh; await settle()
    oldSummary.resolve({ lojas: [], nao_lidas_duoke: 9 }); oldEmail.resolve({ aguardando: 8, chamados: 0 }); await settle()
    assert.equal(m.state.naoLidasDuoke, 0, 'resumo antigo não reintroduz avisos após a leitura')
    assert.equal(m.state.emailEsperando, 0, 'contagem antiga de e-mail também é ignorada')
    assert.equal(m.state.resumoErro, null)
    m.stop()
  }
  {
    const list = deferred(), oldSummary = deferred(), freshSummary = deferred()
    let summaries = 0, lists = 0
    const viewed = { id: 'chat1', aguardando_resposta: true, numero_duoke: 0, visto_em: '2026-10-19T15:00:00Z' }
    const m = mount({ conversation: 'chat1', intercept: (url) => {
      if (url.includes('/conversas?')) return ++lists === 1 ? list.promise : { itens: [viewed], proximo: null }
      if (url === '/api/atendimento/resumo') return ++summaries === 1 ? oldSummary.promise : freshSummary.promise
    } })
    m.start(); await settle()
    const chat = { id: 'chat1', aguardando_resposta: true, numero_duoke: 4, visto_em: null }
    m.state.aoMudarConversa(chat)
    m.state.aoMudarConversa(viewed)
    assert.equal(summaries, 2, 'visto anterior à primeira lista também pede um resumo novo')
    freshSummary.resolve({ lojas: [], nao_lidas_duoke: 0 }); await settle()
    list.resolve({ itens: [chat], proximo: null }); oldSummary.resolve({ lojas: [], nao_lidas_duoke: 4 }); await settle()
    assert.equal(lists, 2, 'lista em voo é descartada e conferida novamente após a leitura')
    assert.equal(m.state.itens[0].numero_duoke, 0, 'nova consulta confirma o visto recebido do detalhe')
    assert.equal(m.state.itens[0].visto_em, '2026-10-19T15:00:00Z')
    assert.equal(m.state.naoLidasDuoke, 0)
    m.stop()
  }
  {
    const first = deferred(), confirmation = deferred()
    let lists = 0
    const m = mount({ conversation: 'chat1', intercept: (url) => {
      if (url.includes('/conversas?')) return ++lists === 1 ? first.promise : confirmation.promise
    } })
    m.start(); await settle()
    const oldDetail = { id: 'chat1', ultima_mensagem_em: '2026-10-19T15:00:00Z', ultima_mensagem_resumo: 'anterior', numero_duoke: 0 }
    const newHead = { id: 'chat1', ultima_mensagem_em: '2026-10-19T15:00:00Z', ultima_mensagem_resumo: 'nova no mesmo segundo', numero_duoke: 1 }
    first.resolve({ itens: [newHead], proximo: null }); await settle()
    assert.equal(lists, 1)
    m.state.aoMudarConversa(oldDetail); await settle()
    assert.equal(lists, 2)
    assert.equal(m.state.itens[0].numero_duoke, 1, 'detalhe antigo não mescla dados na lista já carregada')
    m.state.aoMudarConversa({ ...oldDetail }); await settle()
    confirmation.resolve({ itens: [newHead], proximo: null }); await settle()
    assert.equal(lists, 2, 'emissão sem mudança não invalida a conferência')
    assert.equal(m.state.itens[0].ultima_mensagem_em, newHead.ultima_mensagem_em)
    assert.equal(m.state.itens[0].numero_duoke, 1, 'detalhe atrasado não apaga mensagem mais nova da lista')
    assert.equal(m.state.itens[0].ultima_mensagem_resumo, 'nova no mesmo segundo')
    m.stop()
  }
  {
    const requests = [deferred(), deferred(), deferred()]
    let lists = 0
    const m = mount({ conversation: 'chat1', intercept: (url) => {
      if (url.includes('/conversas?')) return requests[lists++].promise
    } })
    m.start(); await settle()
    const first = { id: 'chat1', numero_duoke: 2, visto_em: null }
    const seen = { ...first, numero_duoke: 0, visto_em: '2026-10-19T15:00:00Z' }
    const incoming = { ...seen, numero_duoke: 1, ultima_mensagem_resumo: 'nova' }
    m.state.aoMudarConversa(first); m.state.aoMudarConversa(seen)
    requests[0].resolve({ itens: [first], proximo: null }); await settle()
    m.state.aoMudarConversa(incoming)
    requests[1].resolve({ itens: [seen], proximo: null }); await settle()
    assert.equal(m.state.carregando, true, 'duas mudanças rápidas não exibem uma primeira lista vazia')
    assert.equal(lists, 3)
    requests[2].resolve({ itens: [incoming], proximo: null }); await settle()
    assert.equal(m.state.carregando, false)
    assert.equal(m.state.itens[0].numero_duoke, 1)
    m.stop()
  }
  {
    const m = mount(); m.start(); await settle()
    m.state.itens = [{ id: 'same', ultima_mensagem_em: '2026-10-19T15:00:00Z', numero_duoke: 1 }]
    const before = m.count('/conversas?')
    m.state.aoMudarConversa({ ...m.state.itens[0], comprador_id: 'extra', anuncio_id: 'extra', email_da_ponte: false })
    await settle()
    assert.equal(m.count('/conversas?'), before, 'campos exclusivos do detalhe não recarregam uma lista idêntica')
    m.state.aoMudarConversa({ ...m.state.itens[0], comprador_id: 'extra', anuncio_id: 'changed-extra' })
    await settle()
    assert.equal(m.count('/conversas?'), before, 'emissão invariável nos campos da lista não deixa dirty')
    m.stop()
  }
  {
    const m = mount({ intercept: () => Promise.reject(Error('offline')) })
    m.start(); await settle()
    assert.equal(m.state.carregando, false)
    assert.equal(m.state.listaErro, 'Falha controlada')
    assert.equal(m.state.resumoErro, 'Falha controlada', 'erro é local; a página permanece montada')
    assert.equal(m.state.emailContagensProntas, true, 'falha no contador não deixa a aba E-mail esperando para sempre')
    m.stop()
  }
  {
    const m = mount(); m.start(); m.stop(); await settle()
    assert.equal(m.count('/conversas?'), 0, 'saída antes do próximo frame cancela a carga inicial agendada')
    assert.equal(m.count('/resumo'), 0)
  }
  console.log('✓ entrada progressiva do Atendimento: setup síncrono, filtros, abas, falhas e respostas antigas')
}

run().catch((error) => { console.error(error); process.exitCode = 1 })
