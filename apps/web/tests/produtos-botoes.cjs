// Run from apps/web: node tests/produtos-botoes.cjs
// Produtos: barra comum Atualizar · Vincular · Sincronizar · Organizar
// (17/11/2026). Renderiza a página real (pages/produtos.vue) com a API
// simulada — nada sai daqui. Confere:
// - a barra aparece nas DUAS abas (entrando direto em ?aba=anuncios, que é para
//   onde o /anuncios antigo redireciona) e o Organizar só para o ADM;
// - os botões que saíram (Vincular Automático, Vincular Anúncios, Sincronizar
//   Todos, importar da aba Anúncios, Remover os N mortos) não voltam;
// - as contas carregam mesmo começando pela aba Anúncios;
// - o 409 da trava global diz o que está rodando, quem e desde quando, nas
//   duas abas (e também na tela Sync logs);
// - Sincronizar depois de "Sincronizar Selecionados" manda product_ids: null;
// - a lista de produtos pede &ocultos=esconder depois de &departamento=;
// - Atualizar: prévia sem gravar nada, POST com corpo vazio, resultado por
//   conta + Bling + Vincular, e no fim recarrega a aba aberta e os números;
// - Organizar: quantidades e lista ANTES do POST, POST com o token da prévia,
//   prévia vencida recarrega, "Mostrar de novo";
// - Sincronizações mostra "Atualizar: N contas".
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { renderToString } = require('vue/server-renderer')
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')

function compilar(rel, id) {
  const filename = path.resolve(__dirname, rel)
  const fonte = fs.readFileSync(filename, 'utf8')
  const { descriptor, errors } = parse(fonte, { filename })
  assert.deepEqual(errors, [])
  const script = compileScript(descriptor, { id })
  const template = compileTemplate({
    source: descriptor.template.content, filename, id,
    compilerOptions: { bindingMetadata: script.bindings },
  })
  assert.deepEqual(template.errors, [])
  return { fonte, script, render: evaluate(template.code).render }
}
function evaluate(source, globals = {}, requireFn = require) {
  const output = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
    reportDiagnostics: true,
  })
  assert.deepEqual(output.diagnostics, [])
  const exports = {}
  new Function('exports', 'require', ...Object.keys(globals), output.outputText)(
    exports, requireFn, ...Object.values(globals),
  )
  return exports
}

const PRODUTOS = compilar('../pages/produtos.vue', 'produtos-botoes')

const INTEGRACOES = [
  { id: 'bling-1', platform: 'bling', name: 'Bling' },
  { id: 'ml-1', platform: 'ml', name: 'Loja ML' },
  { id: 'shopee-1', platform: 'shopee', name: 'Loja S' },
  { id: 'tiktok-1', platform: 'tiktok', name: 'Loja T' },
  { id: 'magalu-1', platform: 'magalu', name: 'Loja Magalu' },
  { id: 'carrefour-1', platform: 'carrefour', name: 'Loja C' },
  { id: 'amazon-1', platform: 'amazon', name: 'Loja A' },
  { id: 'temu-1', platform: 'temu', name: 'Loja Temu' },
]
const PRODUCTS = [
  {
    id: 'p-zero', sku: 'SKU-ZERO', name: 'Produto sem estoque', stock: 0, min_stock: 0,
    segment_id: null, segment_path: null, image_url: null, updated_at: '2026-11-17T12:00:00Z', links: [],
  },
]
const DEPARTAMENTOS = {
  produtos: { celular: 10, mala: 4, eletro: 3, sem: 2 },
  anuncios: { celular: 120, mala: 40, eletro: 30, sem: 9 },
}

function hojeAs(h, m = 0) {
  const d = new Date()
  d.setHours(h, m, 0, 0)
  return d.toISOString()
}
function erroHttp(status, detail) {
  const e = new Error(`HTTP ${status}`)
  e.status = status
  e.statusCode = status
  e.data = { detail }
  return e
}
async function esperar(cond, msg) {
  for (let i = 0; i < 500; i++) {
    if (cond()) return
    await new Promise((resolve) => setImmediate(resolve))
  }
  throw new Error(`esperou demais: ${msg}`)
}
// pushToast / polling usam window.setTimeout; os jobs desta conferência já
// voltam terminados, então nada fica agendado.
globalThis.window = {
  setTimeout: () => 0, clearTimeout: () => {}, setInterval: () => 0, clearInterval: () => {},
  innerWidth: 1280,
}

function appProdutos(component) {
  const result = Vue.createSSRApp(component)
  result.component('ProdutosAnunciosPainel', {
    props: ['departamento'], setup: (props) => () => Vue.h('section', { 'data-painel-anuncios': props.departamento }),
  })
  result.component('PageHeader', {
    props: ['title'], setup: (props, { slots }) => () => Vue.h('header', [Vue.h('h1', props.title), slots.actions?.()]),
  })
  result.component('Button', { setup: (_, { slots }) => () => Vue.h('button', slots.default?.()) })
  result.component('Input', { setup: () => () => Vue.h('input') })
  return result
}

// rotas: [[url exata | RegExp, resposta | (url, options) => resposta]]
async function pagina({ query = {}, edita = true, admin = false, rotas = [], saude = null } = {}) {
  let instance
  const calls = []
  const route = Vue.reactive({ path: '/produtos', query: { ...query } })
  const router = {
    replace: (to) => { route.query = { ...to.query } },
    push: (to) => { route.query = { ...to.query } },
  }
  const padrao = [
    ['/api/products/departamentos', DEPARTAMENTOS],
    [/^\/api\/products\?/, { items: PRODUCTS, total: 1, page: 1, page_size: 50, ...(saude ? { saude } : {}) }],
    ['/api/integrations', INTEGRACOES],
    ['/api/settings', { daily_sync_enabled: false }],
    ['/api/segments', []],
    ['/api/sync/active', { active: false }],
  ]
  const todas = [...rotas, ...padrao]
  const component = evaluate(PRODUTOS.script.content, {
    shallowRef: Vue.shallowRef,
    definePageMeta: () => {},
    useRoute: () => route,
    useRouter: () => router,
    useCan: (resource, action) => Vue.ref(action === 'view' || edita),
    useIsAdmin: () => Vue.ref(admin),
    useApi: () => {
      instance = Vue.getCurrentInstance()
      return { api: async (url, options = {}) => {
        calls.push({ url, options })
        for (const [teste, resposta] of todas) {
          if (typeof teste === 'string' ? url === teste : teste.test(url)) {
            const r = typeof resposta === 'function' ? await resposta(url, options) : resposta
            return structuredClone(r)
          }
        }
        throw new Error(`Unexpected request: ${url}`)
      } }
    },
  }).default
  component.render = PRODUTOS.render
  await renderToString(appProdutos(component))
  const state = instance.setupState
  return {
    state, calls, route,
    html: () => renderToString(appProdutos({ setup: () => state, render: PRODUTOS.render })),
    posts: () => calls.filter(({ options }) => options?.method === 'POST'),
  }
}
function textoLimpo(s) {
  return s.replace(/<!--[\s\S]*?-->/g, '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()
}
function barra(html) {
  const bloco = html.match(/<div[^>]*aria-label="Botões de Produtos e Anúncios"[^>]*>([\s\S]*?)<\/div>/)?.[1]
  if (bloco == null) return null
  return [...bloco.matchAll(/<button\b([^>]*)>([\s\S]*?)<\/button>/g)].map(([, attrs, inner]) => ({
    texto: textoLimpo(inner), desligado: /\bdisabled\b/.test(attrs),
  }))
}
// O que a pessoa vê (os comentários do template citam os nomes antigos).
function semComentarios(html) {
  return html.replace(/<!--[\s\S]*?-->/g, '')
}
const SAIRAM = /Vincular Automático|Vincular Anúncios|Sincronizar Todos|>\s*importar\s*<|Remover os|Sim, remover/

async function barraNasDuasAbas() {
  // Entrando direto na aba Anúncios (o /anuncios antigo cai aqui).
  const editorAnuncios = await pagina({ query: { aba: 'anuncios' } })
  let html = await editorAnuncios.html()
  assert.match(html, /data-painel-anuncios="celular"/)
  assert.deepEqual(barra(html).map((b) => b.texto), ['Atualizar', 'Vincular', 'Sincronizar'],
    'the bar is on the Anúncios tab too, without Organizar for non-admins')
  assert.match(html, />\s*(<svg[\s\S]*?<\/svg>)?\s*recarregar\s*</, 'recarregar stays on the Anúncios tab')
  assert.doesNotMatch(semComentarios(html), SAIRAM)
  assert.doesNotMatch(html, /Importar do Bling|Novo Produto/, 'product tools stay on the Produtos tab')

  const adminAnuncios = await pagina({ query: { aba: 'anuncios' }, admin: true })
  assert.deepEqual(barra(await adminAnuncios.html()).map((b) => b.texto),
    ['Atualizar', 'Vincular', 'Sincronizar', 'Organizar'])

  const adminProdutos = await pagina({ admin: true, saude: { mortos: 7, sku_divergente: 1, erro: 0 } })
  html = await adminProdutos.html()
  assert.deepEqual(barra(html).map((b) => b.texto), ['Atualizar', 'Vincular', 'Sincronizar', 'Organizar'])
  assert.match(html, /Importar do Bling/)
  assert.match(html, /Novo Produto/)
  assert.match(html, /7 mortos \(anúncio encerrado/, 'the Vínculos band stays (only the delete button left)')
  assert.doesNotMatch(semComentarios(html), SAIRAM)

  const editorProdutos = await pagina({ saude: { mortos: 7, sku_divergente: 0, erro: 0 } })
  html = await editorProdutos.html()
  assert.deepEqual(barra(html).map((b) => b.texto), ['Atualizar', 'Vincular', 'Sincronizar'])
  assert.doesNotMatch(semComentarios(html), SAIRAM)
  const lista = editorProdutos.calls.find(({ url }) => url.startsWith('/api/products?'))
  assert.match(lista.url, /&departamento=celular&ocultos=esconder(&|$)/,
    'the product list hides what the Organizar hid, right after &departamento=')

  // Só vê: sem barra nenhuma.
  const leitor = await pagina({ edita: false, query: { aba: 'anuncios' } })
  assert.equal(barra(await leitor.html()), null)

  // Nada do que saiu ficou no código da página.
  assert.doesNotMatch(PRODUTOS.fonte, /auto-import-link|runAutoImportLink|remover-mortos|painelAnuncios\?\.openImport/)
}

async function contasNaAbaAnuncios() {
  const p = await pagina({ query: { aba: 'anuncios' } })
  assert.ok(!p.calls.some(({ url }) => url === '/api/integrations'), 'setup on Anúncios does not read the product list')
  await p.state.carregarContas() // o onMounted (no SSR não roda)
  assert.equal(p.state.integrations.length, INTEGRACOES.length)
  await p.state.openAutoLinkDialog()
  assert.deepEqual([...p.state.selectedAutoLinkIds].sort(),
    ['amazon-1', 'magalu-1', 'ml-1', 'shopee-1', 'tiktok-1'], 'Vincular opens with every account that has a reader')
  const html = await p.html()
  assert.match(html, /Lê os anúncios ao vivo e atualiza o SKU e o vínculo SÓ no DaVinci/)
  assert.match(html, /Carrefour\/Temu: sem leitor de vínculo/)
  assert.match(html, /nada no marketplace, nada no Bling, nenhum estoque enviado/)
  assert.match(html, /Vincular agora \(5 contas\)/)
  assert.equal(p.posts().length, 0, 'opening the dialog sends nothing')

  // Clique antes do onMounted terminar: o próprio diálogo busca as contas.
  const cedo = await pagina({ query: { aba: 'anuncios' } })
  await cedo.state.openSyncAllDialog()
  assert.ok(cedo.calls.some(({ url }) => url === '/api/integrations'))
  assert.equal(cedo.state.selectedSyncAllIds.size, 7, 'Sincronizar: every marketplace account, no Bling')
}

async function aviso409() {
  const varredura = {
    code: 'sync_already_running', job_id: 'j-1', tipo: 'auto_link', gatilho: 'varredura_diaria',
    quem: 'Eduardo', desde: hojeAs(10),
  }
  const p = await pagina({
    query: { aba: 'anuncios' },
    rotas: [['/api/jobs/auto-link', () => { throw erroHttp(409, varredura) }]],
  })
  await p.state.openAutoLinkDialog()
  await p.state.runAutoLink()
  assert.equal(p.state.error, 'Já tem a varredura automática de vínculos rodando, desde 10:00. Espere terminar e tente de novo.')
  assert.equal(p.state.avisoMassa, p.state.error, 'the open dialog says it too')
  assert.equal(p.state.activeJob, null)
  let html = await p.html()
  assert.match(html, /Já tem a varredura automática de vínculos rodando, desde 10:00/,
    'the 409 shows on the Anúncios tab (it used to live only inside the Produtos tab)')
  assert.ok(barra(html).every((b) => b.desligado), 'mass buttons go grey while something runs')

  const outro = {
    code: 'sync_already_running', job_id: 'j-2', tipo: 'sync_all', gatilho: null, quem: 'Marrocos', desde: hojeAs(9, 5),
  }
  const q = await pagina({ rotas: [['/api/produtos/atualizar', () => { throw erroHttp(409, outro) }]] })
  await q.state.abrirAtualizar()
  await q.state.runAtualizar()
  assert.equal(q.state.error, 'Já tem um Sincronizar rodando, disparado por Marrocos, desde 09:05. Espere terminar e tente de novo.')
  html = await q.html()
  assert.match(html, /disparado por Marrocos, desde 09:05/)

  // O servidor manda o nome pronto (rotulo): ele vale mais que o mapa da tela.
  const comRotulo = await pagina({ rotas: [['/api/jobs/sync-all', () => {
    throw erroHttp(409, { ...outro, tipo: 'import_listings', gatilho: 'botao_atualizar', rotulo: 'o Atualizar', quem: 'Israel' })
  }]] })
  await comRotulo.state.openSyncAllDialog()
  await comRotulo.state.runSyncAll()
  assert.equal(comRotulo.state.error, 'Já tem o Atualizar rodando, disparado por Israel, desde 09:05. Espere terminar e tente de novo.')

  // Dois cliques ao mesmo tempo: a trava curta do disparo (sem tipo, só o rótulo).
  const doisCliques = await pagina({ rotas: [['/api/jobs/auto-link', () => {
    throw erroHttp(409, { code: 'sync_already_running', job_id: null, type: null, tipo: null, status: null,
      gatilho: null, quem: null, desde: null, rotulo: 'outro disparo em andamento' })
  }]] })
  await doisCliques.state.openAutoLinkDialog()
  await doisCliques.state.runAutoLink()
  assert.equal(doisCliques.state.error, 'Já tem outro disparo em andamento. Espere terminar e tente de novo.')

  const semContas = await pagina({ rotas: [['/api/produtos/atualizar', () => { throw erroHttp(400, { code: 'sem_contas' }) }]] })
  await semContas.state.runAtualizar()
  assert.match(semContas.state.error, /^Nenhuma conta de marketplace/)

  // O /api/sync/active (a cada 10 s) diz o que está rodando, de qualquer pessoa.
  const ativo = await pagina({
    rotas: [['/api/sync/active', {
      active: true, job_id: 'j-3', type: 'import_listings', status: 'running',
      gatilho: 'botao_atualizar', quem: 'Israel', desde: hojeAs(14, 30),
    }]],
  })
  await ativo.state.refreshMassActive()
  html = await ativo.html()
  assert.match(html, /Rodando agora: um Atualizar, disparado por Israel, desde 14:30/)
  assert.ok(barra(html).every((b) => b.desligado))
}

async function sincronizarDepoisDeSelecionados() {
  const job = {
    id: 'job-s', type: 'sync_all', status: 'succeeded', total: 100, processed: 120,
    payload: {}, result: { ok: 90, skipped: 5, fatal: 1, falhas_produto: 2, falhas_amostra: ['dg053.sp', 'a017.ci'] }, details_count: 0,
    error: null, started_at: null, finished_at: null,
  }
  const p = await pagina({
    rotas: [
      ['/api/jobs/sync-all', { job_id: 'job-s' }],
      ['/api/jobs/job-s/status', job],
      [/^\/api\/jobs\/job-s\/details/, { items: [], max_id: 0 }],
    ],
  })
  await p.state.carregarContas()
  p.state.toggleSelect('p-zero')
  p.state.openSyncSelected()
  assert.deepEqual(p.state.syncAllProductIds, ['p-zero'])
  p.state.showSyncAll = false // cancelou
  await p.state.openSyncAllDialog()
  let html = await p.html()
  assert.match(html, /Envia o estoque do Bling, relido agora, para os anúncios vinculados das contas marcadas/)
  assert.match(html, /Não dá para cancelar depois de começar/)
  await p.state.runSyncAll()
  const [post] = p.posts()
  assert.equal(post.url, '/api/jobs/sync-all')
  assert.equal(post.options.body.product_ids, null, 'Sincronizar after a cancelled "Selecionados" is for every product')
  assert.equal(post.options.body.integration_ids.length, 7)
  await esperar(() => p.state.activeJob?.status === 'succeeded', 'sync job')
  html = await p.html()
  assert.match(textoLimpo(html), /2 produto\(s\) deram erro e não foram enviados .* dg053\.sp, a017\.ci/)
  assert.match(html, />\s*100%\s*</, 'progress never shows more than 100%')
  assert.doesNotMatch(html, /12\d%/)
}

async function atualizar() {
  const resultado = {
    bling: { lidos: 2600, faltando: 2, criados: 2, falhas: 0, skus_criados: ['novo.sp', 'novo.ci'], pulado: null, erro: null },
    anuncios: { contas: 5, ok: 4, falhas: 1, puladas: 0, novos: 12, atualizados: 340 },
    vincular: {
      created: 3, repointed: 1, sku_atualizados: 4, mortos: 2, revividos: 0, mortos_nao_criados: 6,
      already_present: 900, not_found: 7, sku_vazio: 0, sku_ambiguo: 1,
      mudancas: [{ acao: 'movido', conta: 'Loja ML', anuncio: 'MLB1', variacao: '55', sku_anuncio: 'dg053.sp', de: 'dg053.ci', para: 'dg053.sp' }],
      lista_anuncios: { passou_a_ter_produto: 3, trocou_produto: 339, amostra: [] },
    },
    flex: { esperou_s: 35, conseguiu: true },
    sem_leitura: ['amazon'],
  }
  const detalhes = [
    { id: 1, etapa: 'bling', lidos: 2600, criados: 2 },
    { id: 2, etapa: 'conta', integration_id: 'ml-1', nome: 'Loja ML', plataforma: 'ml', resultado: 'ok', novos: 3, atualizados: 10, ignorados: 1, leitura_completa: false, erro: null, dica: null },
    { id: 3, etapa: 'conta', integration_id: 'magalu-1', nome: 'Loja Magalu', plataforma: 'magalu', resultado: 'falha', novos: 0, atualizados: 0, ignorados: 0, leitura_completa: null, erro: 'invalid_grant', dica: 'reconectar a conta em Integrações' },
  ]
  const job = {
    id: 'job-a', type: 'import_listings', status: 'succeeded', total: 6, processed: 6,
    payload: { trigger: 'botao_atualizar', integration_ids: ['ml-1', 'shopee-1', 'tiktok-1', 'magalu-1', 'carrefour-1'] },
    result: resultado, details_count: detalhes.length, error: null, started_at: null, finished_at: null,
  }
  const p = await pagina({
    query: { aba: 'anuncios' },
    rotas: [
      ['/api/produtos/atualizar', { job_id: 'job-a' }],
      ['/api/jobs/job-a/status', job],
      // como a API: só o que veio depois do cursor `after`
      [/^\/api\/jobs\/job-a\/details/, (url) => {
        const depois = Number(new URL(url, 'https://t.invalid').searchParams.get('after') || 0)
        const items = detalhes.filter((d) => d.id > depois)
        return { items, max_id: items.length ? items.at(-1).id : depois }
      }],
    ],
  })
  await p.state.carregarContas()
  await p.state.abrirAtualizar()
  let texto = textoLimpo(await p.html())
  assert.match(texto, /Listas de anúncios que serão lidas \(5 contas\)/)
  assert.match(texto, /Mercado Livre \(1\) Loja ML/)
  assert.match(texto, /Amazon: fora da lista/)
  assert.match(texto, /Temu: fora da lista \(ainda não tem leitor\)/)
  assert.match(texto, /cria só os produtos que ainda não existem no DaVinci\. Não altera, não reativa e não desativa nenhum produto/)
  assert.match(texto, /liga os anúncios aos produtos sozinho \(o mesmo Vincular\) e segura o robô do Flex até terminar/)
  assert.match(texto, /Não grava nada no Bling nem nos marketplaces/)
  assert.match(texto, /~25–45 min \(até 1 h se o robô do Flex estiver rodando\)/)
  assert.equal(p.posts().length, 0, 'the preview writes nothing')

  let painel = 0
  p.state.painelAnuncios = { refreshAll: async () => { painel++ } }
  p.calls.length = 0
  await p.state.runAtualizar()
  const [post] = p.posts()
  assert.equal(post.url, '/api/produtos/atualizar')
  assert.deepEqual(post.options.body, {}, 'the server builds the account list')
  await esperar(() => p.state.activeJob?.status === 'succeeded' && painel > 0, 'atualizar job')
  assert.ok(p.calls.some(({ url }) => url === '/api/products/departamentos'), 'tab counts reloaded at the end')
  assert.ok(!p.calls.some(({ url }) => url.startsWith('/api/products?')), 'on Anúncios the panel reloads, not the product list')
  assert.equal(p.state.produtosCarregadosDe, null, 'the product list is re-read when going back to Produtos')

  texto = textoLimpo(await p.html())
  assert.match(texto, /2 de 5 contas lidas/)
  assert.match(texto, /✓ Mercado Livre Loja ML: 3 novos · 10 atualizados · 1 ignorados \(encerrados que o DaVinci não conhecia\) · leitura incompleta/)
  assert.match(texto, /⚠ Magalu Loja Magalu: falhou — invalid_grant · reconectar a conta em Integrações/)
  assert.match(texto, /4 de 5 contas lidas · 12 anúncios novos · 340 atualizados · 1 conta\(s\) com falha/)
  assert.match(texto, /Sem leitura da lista: Amazon/)
  assert.match(texto, /Esperou 35 s o robô do Flex/)
  assert.match(texto, /2\.600 lidos · 2 faltando no DaVinci · 2 criados/)
  assert.match(texto, /Criados: novo\.sp, novo\.ci/)
  assert.match(texto, /3 vínculo\(s\) criado\(s\) · 1 movido\(s\) para o SKU novo do anúncio · 4 SKU\(s\) atualizado\(s\) no DaVinci · 2 marcado\(s\) como morto\(s\)/)
  assert.match(texto, /6 não criado\(s\) porque o anúncio já está encerrado ou excluído/)
  assert.match(texto, /Aba Anúncios: 3 passaram a ter produto · 339 trocaram de produto/)
  assert.match(texto, /Loja ML: movido dg053\.ci → dg053\.sp \(MLB1\/55\)/)
  assert.match(texto, /clique em Sincronizar/)

  // Robô do Flex não soltou a trava: o resultado avisa.
  assert.equal(p.state.atualizarSummaryClass({ status: 'succeeded', result: { flex: { conseguiu: false } } }),
    'border-amber-300 bg-amber-50 text-amber-800')
}

async function organizar() {
  const chaves = [
    { chave: 'vinculo_morto', tabela: 'product_links', rotulo: 'Vínculos mortos', explicacao: 'O marketplace disse que o anúncio acabou.', quantidade: 4003, marcado_por_padrao: true },
    { chave: 'produto_excluido_sem_anuncio', tabela: 'products', rotulo: 'Produtos excluídos no Bling sem anúncio', explicacao: 'SKU apagado no Bling.', quantidade: 2199, marcado_por_padrao: true },
    { chave: 'produto_excluido_com_anuncio', tabela: 'products', rotulo: 'Produtos excluídos no Bling com anúncio', explicacao: 'Ainda recebe 0.', quantidade: 864, marcado_por_padrao: false },
    { chave: 'produto_inativo', tabela: 'products', rotulo: 'Produtos inativos', explicacao: 'Inativo no Bling.', quantidade: 13, marcado_por_padrao: false },
    { chave: 'anuncio_loja_arquivada', tabela: 'listings', rotulo: 'Anúncios de loja arquivada', explicacao: 'Loja arquivada.', quantidade: 3205, marcado_por_padrao: true },
    { chave: 'anuncio_produto_excluido', tabela: 'listings', rotulo: 'Anúncios de produto excluído', explicacao: 'Produto E.', quantidade: 851, marcado_por_padrao: false },
    { chave: 'anuncio_encerrado', tabela: 'listings', rotulo: 'Anúncios encerrados', explicacao: 'Encerrado.', quantidade: 0, marcado_por_padrao: true },
  ]
  let previas = 0
  const previa = () => {
    previas++
    return {
      token: `tok-${previas}`, expira_em: hojeAs(23, 59), chaves,
      ocultos: previas > 2 ? { total: 9420, por_chave: { vinculo_morto: 4003, produto_inativo: 13 } } : { total: 0, por_chave: {} },
      lotes: previas > 2
        ? [{ lote_id: 'lote-1', oculto_em: hojeAs(14, 32), oculto_por_nome: 'Eduardo', quantidade: 9420, por_chave: { vinculo_morto: 4003 } }]
        : [],
    }
  }
  let aplicacoes = 0
  const p = await pagina({
    admin: true,
    rotas: [
      ['/api/produtos/organizar/previa', previa],
      [/^\/api\/produtos\/organizar\/itens\?/, (url) => ({
        itens: [{ marcador_id: new URL(url, 'https://t.invalid').searchParams.get('ocultos') === 'true' ? 'm-1' : null,
          registro_id: 'r-1', sku: 'dg053.ci', nome: 'Capinha', conta: 'Loja ML', anuncio: 'MLB9', variacao: '7',
          sku_anuncio: 'dg053.ci', motivo: 'vinculo_morto', data: '2026-11-15T10:00:00Z' }],
        total: 4003, pagina: 1, por_pagina: 100,
      })],
      ['/api/produtos/organizar/aplicar', () => {
        aplicacoes++
        if (aplicacoes === 1) throw erroHttp(409, { code: 'previa_expirada' })
        if (aplicacoes === 3) throw erroHttp(409, { code: 'organizar_em_andamento' })
        if (aplicacoes === 4) return { lote_id: null, ocultados: 0, por_chave: {}, sairam_desde_previa: 9420 }
        return { lote_id: 'lote-1', ocultados: 9420, por_chave: { vinculo_morto: 4003, produto_inativo: 13 }, sairam_desde_previa: 4 }
      }],
      ['/api/produtos/organizar/reexibir', { reexibidos: 9420 }],
    ],
  })
  await p.state.abrirOrganizar()
  assert.deepEqual([...p.state.orgMarcadas].sort(),
    ['anuncio_encerrado', 'anuncio_loja_arquivada', 'produto_excluido_sem_anuncio', 'vinculo_morto'])
  assert.equal(p.state.orgTotalMarcado, 4003 + 2199 + 3205, 'exact total of the checked boxes (an empty one counts 0)')
  let texto = textoLimpo(await p.html())
  assert.match(texto, /Só esconde da tela do DaVinci\. Nada é apagado, nada muda no Bling nem nos marketplaces, o estoque continua sendo enviado \(inclusive o 0 dos excluídos\)\. Dá para desfazer\./)
  assert.match(texto, /Vínculos mortos — 4\.003/)
  assert.match(texto, /Produtos inativos — 13/)
  assert.match(texto, /Ocultar 9\.407 itens/)
  assert.match(texto, /Ocultos \(0\)/)

  await p.state.verListaOrganizar('vinculo_morto', false)
  const itens = p.calls.filter(({ url }) => url.startsWith('/api/produtos/organizar/itens?')).at(-1)
  // com o token: a lista é a que a prévia guardou (o total bate com o botão)
  assert.equal(itens.url, '/api/produtos/organizar/itens?chave=vinculo_morto&ocultos=false&pagina=1&token=tok-1')
  texto = textoLimpo(await p.html())
  assert.match(texto, /Vão ser escondidos: Vínculos mortos — 4\.003/)
  assert.match(texto, /dg053\.ci Capinha Loja ML MLB9 7/)
  assert.match(texto, /página 1 de 41/)
  assert.equal(p.posts().length, 0, 'counts and list come before any POST')

  p.state.alternarChaveOrganizar('produto_inativo')
  assert.equal(p.state.orgTotalMarcado, 9420)

  // Prévia vencida (30 min): recarrega os números e mantém as caixas marcadas.
  await p.state.aplicarOrganizar()
  assert.match(p.state.orgErro, /venceu/)
  assert.equal(previas, 2)
  assert.equal(p.state.orgResultado, null)
  assert.ok(p.state.orgMarcadas.has('produto_inativo'), 'the ADM choice survives the reload')

  p.calls.length = 0
  await p.state.aplicarOrganizar()
  const [aplicar] = p.posts()
  assert.equal(aplicar.url, '/api/produtos/organizar/aplicar')
  assert.equal(aplicar.options.body.token, 'tok-2', 'only what the (new) preview showed')
  assert.deepEqual(aplicar.options.body.chaves,
    ['vinculo_morto', 'produto_excluido_sem_anuncio', 'produto_inativo', 'anuncio_loja_arquivada'])
  assert.ok(p.calls.some(({ url }) => /^\/api\/products\?.*&ocultos=esconder/.test(url)), 'list reloaded without the hidden')
  texto = textoLimpo(await p.html())
  assert.match(texto, /Ocultados: 9\.420 \(lote de [^)]* por Eduardo\)/)
  assert.match(texto, /4 saíram da lista desde a prévia/)
  assert.match(texto, /Desfazer este lote/)
  assert.match(texto, /Ocultos \(9\.420\)/)

  // Dois ADMs ao mesmo tempo: o segundo recebe 409.
  await p.state.aplicarOrganizar()
  assert.match(p.state.orgErro, /Outra pessoa está organizando agora/)

  // Aba Ocultos: lotes e motivos com "Mostrar de novo".
  p.state.organizarAba = 'ocultos'
  await p.state.verListaOrganizar('vinculo_morto', true)
  texto = textoLimpo(await p.html())
  assert.match(texto, /por Eduardo — 9\.420 item\(ns\)/)
  assert.match(texto, /Vínculos mortos — 4\.003 Ver lista Mostrar todos de novo/)
  assert.match(texto, /Escondidos: Vínculos mortos/)
  assert.match(texto, /Mostrar de novo/)
  p.calls.length = 0
  await p.state.reexibirOrganizar({ marcador_ids: ['m-1'] }, 'dg053.ci')
  await p.state.reexibirOrganizar({ lote_id: 'lote-1' }, 'Lote')
  assert.deepEqual(p.posts().map(({ url, options }) => [url, options.body]), [
    ['/api/produtos/organizar/reexibir', { marcador_ids: ['m-1'] }],
    ['/api/produtos/organizar/reexibir', { lote_id: 'lote-1' }],
  ])
  assert.equal(p.state.orgResultado, null, 'the undone batch leaves the result box')

  // Tudo saiu da lista entre a prévia e o clique: sem lote, sem "Desfazer".
  await p.state.aplicarOrganizar()
  texto = textoLimpo(await p.html())
  assert.match(texto, /Nada foi escondido/)
  assert.doesNotMatch(texto, /Desfazer este lote/)

  // 403 do servidor (não-admin que chegou ao endpoint): detail {code: 'admin_only'}.
  const leigo = await pagina({ rotas: [['/api/produtos/organizar/previa', () => { throw erroHttp(403, { code: 'admin_only' }) }]] })
  await leigo.state.abrirOrganizar()
  assert.equal(leigo.state.orgErro, 'Só o administrador pode organizar.')
}

async function syncLogs409() {
  const tela = compilar('../pages/sync-logs.vue', 'sync-logs-409')
  let instance
  const calls = []
  const component = evaluate(tela.script.content, {
    definePageMeta: () => {},
    useCan: () => Vue.ref(true),
    useApi: () => {
      instance = Vue.getCurrentInstance()
      return { api: async (url, options = {}) => {
        calls.push({ url, options })
        if (url === '/api/jobs/sync-all') {
          throw erroHttp(409, { code: 'sync_already_running', tipo: 'sync_all', gatilho: 'daily_sync', quem: 'Eduardo', desde: hojeAs(3) })
        }
        throw new Error(`Unexpected request: ${url}`)
      } }
    },
  }).default
  component.render = tela.render
  const app = Vue.createSSRApp(component)
  for (const nome of ['PageHeader', 'StatCard', 'Button', 'Input']) {
    app.component(nome, { setup: (_, { slots }) => () => Vue.h('div', slots.default?.()) })
  }
  await renderToString(app)
  await instance.setupState.startSyncAll()
  assert.equal(instance.setupState.error,
    'Já tem a sincronização automática da noite rodando, desde 03:00. Espere terminar e tente de novo.')
}

async function sincronizacoesAtualizar() {
  const tela = compilar('../pages/sincronizacoes.vue', 'sincronizacoes-atualizar')
  let instance
  const component = evaluate(tela.script.content, {
    definePageMeta: () => {},
    useRoute: () => ({ query: {} }),
    useApi: () => {
      instance = Vue.getCurrentInstance()
      return { api: async (url) => { throw new Error(`Unexpected request: ${url}`) } }
    },
  }, (id) => (id === '~/lib/navGroups' ? { TABS_CADASTROS: [], TABS_INTEGRACOES: [] } : require(id))).default
  component.render = tela.render
  const app = Vue.createSSRApp(component)
  for (const nome of ['PageHeader', 'StatCard', 'Button', 'RouteTabs']) {
    app.component(nome, { setup: (_, { slots }) => () => Vue.h('div', slots.default?.()) })
  }
  await renderToString(app)
  const { fmtPayloadHint } = instance.setupState
  assert.equal(fmtPayloadHint({
    type: 'import_listings', payload: { trigger: 'botao_atualizar', integration_ids: ['a', 'b', 'c'] }, result: {},
  }), 'Atualizar: 3 contas')
  assert.equal(fmtPayloadHint({ type: 'import_listings', payload: { integration_id: '12345678-aaaa' }, result: {} }),
    'integ 12345678', 'the per-account import keeps its hint')
}

// Achados da revisão da tela (17/11/2026).
async function consertosDaRevisao() {
  // T1: Vincular do fim incompleto → caixa âmbar, aviso e as contas pendentes.
  const detalhes = [
    { id: 1, etapa: 'bling', lidos: 10, criados: 0 },
    { id: 2, etapa: 'fase', fase: 'vincular', contas: 2 },
    { id: 3, etapa: 'vincular', integration_id: 'ml-1', integration_name: 'Loja ML', platform: 'ml', result: 'pending', error: 'ml 429' },
    { id: 4, etapa: 'vincular', integration_id: 'shopee-1', integration_name: 'Loja S', platform: 'shopee', result: 'ok' },
    { id: 5, etapa: 'fase', fase: 'listas_flex' },
    { id: 6, etapa: 'conta', integration_id: 'ml-1', nome: 'Loja ML', plataforma: 'ml', resultado: 'falha', erro: 'ml 429 — o Vincular não conseguiu ler a conta; a lista não foi gravada', dica: null },
    { id: 7, etapa: 'conta', integration_id: 'shopee-1', nome: 'Loja S', plataforma: 'shopee', resultado: 'ok', novos: 1, atualizados: 2 },
  ]
  const job = {
    id: 'job-v', type: 'import_listings', status: 'succeeded', total: 3, processed: 3,
    payload: { trigger: 'botao_atualizar', integration_ids: ['ml-1', 'shopee-1'] },
    result: {
      bling: { lidos: 10 }, anuncios: { contas: 2, ok: 1, falhas: 1, puladas: 0, novos: 1, atualizados: 2 },
      vincular: { created: 1, pending_integrations: 1, ok_integrations: 1 },
      flex: { esperou_s: 0, conseguiu: true }, sem_leitura: ['amazon', 'temu'],
    },
    details_count: detalhes.length, error: 'Vincular incompleto: 1 conta(s) pendente(s)', started_at: null, finished_at: null,
  }
  const rotasJob = (j, dets) => [
    ['/api/produtos/atualizar', { job_id: j.id }],
    [`/api/jobs/${j.id}/status`, () => j],
    [new RegExp(`^/api/jobs/${j.id}/details`), (url) => {
      const depois = Number(new URL(url, 'https://t.invalid').searchParams.get('after') || 0)
      const items = dets.filter((d) => d.id > depois)
      return { items, max_id: items.length ? items.at(-1).id : depois }
    }],
  ]
  let p = await pagina({ rotas: rotasJob(job, detalhes) })
  p.state.painelAnuncios = { refreshAll: async () => {} }
  await p.state.carregarContas()
  await p.state.abrirAtualizar()
  await p.state.runAtualizar()
  await esperar(() => p.state.activeJob?.status === 'succeeded', 'job with pending vincular')
  assert.equal(p.state.atualizarSummaryClass(p.state.activeJob), 'border-amber-300 bg-amber-50 text-amber-800')
  let texto = textoLimpo(await p.html())
  assert.match(texto, /O Vincular não terminou\. Rode o Vincular antes da próxima rodada do robô do Flex\./)
  assert.match(texto, /⚠ Mercado Livre Loja ML — ml 429/)
  assert.doesNotMatch(texto, /Vincular incompleto: 1 conta/, 'the raw error is not repeated in mono')
  // T8: a Temu não tem leitor nenhum
  assert.match(texto, /Sem leitura da lista: Amazon, Temu \(os vínculos da Amazon são lidos no Vincular; Temu: sem leitor\)\./)
  assert.equal(p.state.atualizarSummaryClass({ status: 'succeeded', result: { vincular_erro: 'OperationalError' } }),
    'border-amber-300 bg-amber-50 text-amber-800')

  // T2: terminou com o diálogo minimizado → aviso na página com "ver resultado".
  p = await pagina({ rotas: rotasJob(job, detalhes) })
  p.state.painelAnuncios = { refreshAll: async () => {} }
  await p.state.runAtualizar() // diálogo fechado (minimizado)
  await esperar(() => p.state.fimDeMassa != null, 'end banner')
  texto = textoLimpo(await p.html())
  assert.match(texto, /Atualizar terminou: 1 de 2 contas lidas · 1 com falha · o Vincular do fim não terminou — rode o Vincular\. ver resultado/)
  assert.ok(barra(await p.html()).some((b) => b.texto === 'ver resultado'), 'the bar offers the result too')
  p.state.verAndamento()
  assert.equal(p.state.showAtualizar, true)
  assert.equal(p.state.fimDeMassa, null)

  // T2: ao abrir a página de novo, retoma o andamento do job da própria pessoa.
  p = await pagina({
    rotas: [
      ['/api/sync/active', { active: true, meu: true, job_id: 'job-v', tipo: 'import_listings', type: 'import_listings', gatilho: 'botao_atualizar', quem: 'Eduardo', desde: hojeAs(14) }],
      ...rotasJob(job, detalhes),
    ],
  })
  p.state.painelAnuncios = { refreshAll: async () => {} }
  await p.state.retomarMassaMinha()
  await esperar(() => p.state.activeJob?.id === 'job-v', 'resumed job')
  assert.equal(p.state.dialogoDoJob, 'atualizar')
  // de outra pessoa (ou a varredura das 10h): não retoma
  p = await pagina({ rotas: [['/api/sync/active', { active: true, meu: false, job_id: 'job-x', tipo: 'auto_link', gatilho: 'botao_vincular' }]] })
  await p.state.retomarMassaMinha()
  assert.equal(p.state.activeJob, null)

  // T3: com massa rodando, "Sincronizar Selecionados" e "+ vincular" não abrem
  // o diálogo (mostrariam o andamento da outra massa como se fosse deles).
  p = await pagina({ rotas: [['/api/sync/active', { active: true, meu: false, job_id: 'job-x', tipo: 'import_listings', gatilho: 'botao_atualizar', quem: 'Cairo', desde: hojeAs(9, 5) }]] })
  await p.state.refreshMassActive()
  p.state.toggleSelect('p-zero')
  p.state.openSyncSelected()
  assert.equal(p.state.showSyncAll, false)
  assert.match(p.state.error, /Já tem um Atualizar rodando, disparado por Cairo, desde 09:05/)
  p.state.error = null
  p.state.manualLink(PRODUCTS[0], 'amazon')
  assert.equal(p.state.showAutoLink, false)
  assert.match(p.state.error, /Já tem um Atualizar rodando/)

  // T4: a fase diz o que está fazendo (e o Vincular conta as contas).
  const correndo = { ...job, status: 'running', result: null, error: null }
  const fases = [
    { id: 1, etapa: 'bling', lidos: 10 },
    { id: 2, etapa: 'fase', fase: 'esperando_flex' },
  ]
  p = await pagina({ rotas: rotasJob(correndo, fases) })
  await p.state.runAtualizar()
  await esperar(() => p.state.activeJob?.status === 'running', 'running job')
  await esperar(() => p.state.jobDetails.length === 2, 'phase details')
  assert.match(p.state.atualizarEtapa, /Esperando o robô do Flex terminar a rodada dele \(até 10 min\)/)
  fases.push({ id: 3, etapa: 'fase', fase: 'vincular', contas: 4 },
    { id: 4, etapa: 'vincular', integration_id: 'ml-1', result: 'ok' })
  p.state.jobDetails = fases
  assert.equal(p.state.atualizarEtapa, 'Ligando os anúncios aos produtos (Vincular): 1 de 4 contas…')

  // T5: interrompido (publicação no meio) → sem números que contradizem as linhas.
  const cortado = { ...job, status: 'failed', error: 'interrompido (timeout/reinício)', result: { bling: null, anuncios: { contas: 0, ok: 0 }, vincular: null } }
  p = await pagina({ rotas: rotasJob(cortado, detalhes) })
  p.state.painelAnuncios = { refreshAll: async () => {} }
  await p.state.abrirAtualizar()
  await p.state.runAtualizar()
  await esperar(() => p.state.activeJob?.status === 'failed', 'interrupted job')
  texto = textoLimpo(await p.html())
  assert.match(texto, /Interrompido no meio \(publicação ou reinício do robô\): o que já foi gravado está nas linhas acima\. Clique em Atualizar de novo\./)
  assert.doesNotMatch(texto, /0 de 0 contas lidas/)
  assert.doesNotMatch(texto, /Produtos novos do Bling/)

  // T7 e T9: os tiques seguintes do polling (captura o setTimeout).
  const agendados = []
  const setTimeoutOriginal = globalThis.window.setTimeout
  globalThis.window.setTimeout = (fn) => { agendados.push(fn); return agendados.length }
  try {
    // T7: rodada refeita pelo robô (detalhes apagados, started_at novo) → a
    // tela recomeça a lista em vez de congelar e duplicar.
    let estado = { ...correndo, started_at: '2026-11-17T14:00:00Z', details_count: 3 }
    let dets = [
      { id: 1, etapa: 'bling' },
      { id: 2, etapa: 'conta', integration_id: 'ml-1', resultado: 'ok' },
      { id: 3, etapa: 'conta', integration_id: 'shopee-1', resultado: 'ok' },
    ]
    p = await pagina({
      rotas: [
        ['/api/produtos/atualizar', { job_id: 'job-v' }],
        ['/api/jobs/job-v/status', () => estado],
        [/^\/api\/jobs\/job-v\/details/, (url) => {
          const depois = Number(new URL(url, 'https://t.invalid').searchParams.get('after') || 0)
          const items = dets.filter((d) => d.id > depois)
          return { items, max_id: items.length ? items.at(-1).id : depois }
        }],
      ],
    })
    await p.state.runAtualizar()
    await esperar(() => p.state.jobDetails.length === 3, 'first round details')
    estado = { ...estado, started_at: '2026-11-17T14:20:00Z', details_count: 2 }
    dets = [{ id: 10, etapa: 'bling' }, { id: 11, etapa: 'conta', integration_id: 'ml-1', resultado: 'ok' }]
    await agendados.at(-1)()
    assert.deepEqual(p.state.jobDetails.map((d) => d.id), [10, 11])
    assert.equal(p.state.atualizarContasLidas.length, 1)

    // T9: PENDING há mais de 20 min → a tela desiste e libera os botões.
    const pendente = { ...correndo, id: 'job-p', status: 'pending', created_at: new Date(Date.now() - 25 * 60 * 1000).toISOString(), details_count: 0 }
    p = await pagina({ rotas: rotasJob(pendente, []) })
    await p.state.runAtualizar()
    await esperar(() => p.state.activeJob?.status === 'failed', 'stale pending job')
    assert.match(p.state.activeJob.error, /O robô não pegou o pedido em 20 min/)
    assert.equal(p.state.jobActive, false)
  } finally {
    globalThis.window.setTimeout = setTimeoutOriginal
  }

  // T6: Sincronizações mostra os produtos com erro.
  const tela = compilar('../pages/sincronizacoes.vue', 'sincronizacoes-falhas')
  let instance
  const component = evaluate(tela.script.content, {
    definePageMeta: () => {},
    useRoute: () => ({ query: {} }),
    useApi: () => { instance = Vue.getCurrentInstance(); return { api: async () => { throw new Error('x') } } },
  }, (id) => (id === '~/lib/navGroups' ? { TABS_CADASTROS: [], TABS_INTEGRACOES: [] } : require(id))).default
  component.render = tela.render
  const app = Vue.createSSRApp(component)
  for (const nome of ['PageHeader', 'StatCard', 'Button', 'RouteTabs']) {
    app.component(nome, { setup: (_, { slots }) => () => Vue.h('div', slots.default?.()) })
  }
  await renderToString(app)
  assert.equal(instance.setupState.fmtPayloadHint({
    type: 'sync_all', payload: { total_products: 1800 },
    result: { total_links: 9000, ok: 8000, fatal: 3, skipped: 10, falhas_produto: 2, falhas_amostra: ['dg053.sp', 'a017.ci'] },
  }), '1800 produtos · 8000 ok · 3 fatal · 10 skip · 2 produto(s) com erro: dg053.sp, a017.ci')
}

async function run() {
  await barraNasDuasAbas()
  await contasNaAbaAnuncios()
  await aviso409()
  await sincronizarDepoisDeSelecionados()
  await atualizar()
  await organizar()
  await syncLogs409()
  await sincronizacoesAtualizar()
  await consertosDaRevisao()
}

run().then(() => console.log('PASS: Produtos — barra Atualizar/Vincular/Sincronizar/Organizar nas 2 abas, botões que saíram, contas na aba Anúncios, 409 com quem e desde quando, Sincronizar sem a seleção, &ocultos=esconder, Atualizar e Organizar ponta a ponta na tela, Sync logs e Sincronizações'))
  .catch((error) => { console.error(error); process.exitCode = 1 })
