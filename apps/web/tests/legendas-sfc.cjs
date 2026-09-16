// Run from apps/web: node tests/legendas-sfc.cjs
// Cadastros › Legendas (Eduardo, 16/09/2026): parse + compileTemplate da
// página (molde de redes-sociais-sfc.cjs), checagens dos helpers puros e
// execução do <script setup> com api/timers/window falsos.
//
// O que esta tela promete e o teste trava:
//  · a variação NASCE aqui — sem nenhuma linha o robô recusa agendar
//    (`sem_legenda`), e o estado vazio tem que dizer isso;
//  · o padrão da marca (`product_id` nulo) aparece SEPARADO das variações de
//    produto — é a cascata lida de trás pra frente;
//  · a prévia chama POST /preview (que não grava) com debounce, e a resposta
//    atrasada nunca sobrescreve a mais nova;
//  · erro de template vira FRASE (o 422 do router `{code, erro}` E o 422 do
//    Pydantic `[{loc, msg}]`), nunca "artigo_colado_no_produto" cru;
//  · 403/404 degradam com mensagem — a tela não pode morrer;
//  · apagar pede window.confirm.
// Só dados FALSOS aqui; nenhuma rede.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { parse, compileTemplate } = require('vue/compiler-sfc')

const transpile = (source, module = ts.ModuleKind.CommonJS) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module },
}).outputText

// ---------------------------------------------------------------- SFC
const filename = path.join(__dirname, '../pages/legendas.vue')
const source = fs.readFileSync(filename, 'utf8')
const { descriptor, errors } = parse(source, { filename })
assert.deepEqual(errors, [])
const compiled = compileTemplate({ source: descriptor.template.content, filename, id: 'legendas-check' })
assert.deepEqual(compiled.errors, [])
// O render compilado precisa ser JS válido (imports do vue resolvem via require).
new Function('exports', 'require', transpile(compiled.code))({}, require)

const script = descriptor.scriptSetup.content
const tpl = descriptor.template.content

// ---------------------------------------------------------------- libs reais
function loadLib(rel) {
  const exp = {}
  new Function('exports', 'require', transpile(fs.readFileSync(path.join(__dirname, rel), 'utf8')))(exp, require)
  return exp
}
const apiError = loadLib('../lib/apiError.ts')
const navGroups = loadLib('../lib/navGroups.ts')

// ---------------------------------------------------------------- higiene estática
assert.match(script, /permission: \{ resource: 'marketing_criativos', action: 'view' \}/, 'permission middleware')
assert.match(script, /useCan\('marketing_criativos', 'edit'\)/, 'escrita atrás de edit')
// Endpoints da borda (routers/marketing_legendas.py) — nada inventado.
assert.match(script, /'\/api\/marketing\/legendas\/preview'/, 'prévia por POST /preview')
assert.match(script, /method: 'POST'[\s\S]{0,200}body: \{ texto, marca_id: marcaId, product_id/, 'corpo da prévia')
assert.match(script, /\/api\/marketing\/legendas\?marca_id=/, 'lista filtrada por marca')
assert.match(script, /`\/api\/marketing\/legendas\/\$\{alvo\.id\}`, \{ method: 'PATCH'/, 'PATCH da variação')
assert.match(script, /`\/api\/marketing\/legendas\/\$\{m\.id\}`, \{ method: 'DELETE' \}/, 'DELETE da variação')
// Apagar é destrutivo do ponto de vista do operador: confirma antes.
assert.match(script, /async function remover[\s\S]{0,600}window\.confirm/, 'apagar pede confirm')
// A legenda NUNCA volta a sair do `roteiro` (prompt de vídeo em inglês).
assert.ok(!/roteiro/.test(script + tpl), 'nada de roteiro nesta tela')
assert.ok(!/localStorage|sessionStorage|document\.cookie/.test(script + tpl), 'sem armazenamento do navegador')
// `{{ }}` dentro de ATRIBUTO é erro de compilação no Vue 3 — e esta tela
// escreve placeholders o tempo todo, então é a regressão mais fácil de
// cometer aqui (o exemplo do textarea já mora numa constante).
assert.ok(!/=["'][^"']*\{\{/.test(tpl), 'nenhum {{ }} dentro de atributo')
assert.match(script, /const EXEMPLO_TEXTO/, 'exemplo do textarea fora do atributo')
assert.match(tpl, /:placeholder="EXEMPLO_TEXTO"/, 'textarea usa o exemplo por v-bind')
// Estado vazio: é o motivo da feature existir.
assert.match(tpl, /sem_legenda/, 'estado vazio cita o código da recusa')
assert.match(tpl, /Criar o padrão da marca/, 'estado vazio oferece criar o padrão')
// Aviso da armadilha de concordância (SPEC, seção Placeholders).
assert.match(tpl, /Nunca cole artigo antes do placeholder/, 'aviso do artigo colado')
assert.match(tpl, /do Cafeteira/, 'o aviso mostra o estrago')
// Escrita atrás de canEdit; só-view continua enxergando.
assert.match(tpl, /<Button v-if="canEdit" :disabled="saving \|\| !form\.texto\.trim\(\)" @click="salvar">/, 'Salvar só com edit')
assert.match(tpl, /v-if="canEdit" size="sm" variant="ghost" aria-label="Apagar variação"/, 'apagar só com edit')

// Menu: a tela entrou no grupo Cadastros com a permissão dos criativos.
{
  const aba = navGroups.TABS_CADASTROS.find((t) => t.to === '/legendas')
  assert.ok(aba, 'aba Legendas no grupo Cadastros')
  assert.equal(aba.label, 'Legendas')
  assert.equal(aba.resource, 'marketing_criativos')
  // allowedTabs é quem esconde a aba de quem não pode ver.
  const semPerm = navGroups.allowedTabs(navGroups.TABS_CADASTROS, { role: 'user', permissions: {} })
  assert.ok(!semPerm.some((t) => t.to === '/legendas'), 'sem permissão não vê a aba')
  const comPerm = navGroups.allowedTabs(navGroups.TABS_CADASTROS, {
    role: 'user', permissions: { marketing_criativos: { view: true } },
  })
  assert.ok(comPerm.some((t) => t.to === '/legendas'), 'com marketing_criativos:view vê a aba')
}

// ---------------------------------------------------------------- helpers puros
const start = script.indexOf('// ---------- helpers puros')
const end = script.indexOf('// ---------- fim helpers puros')
assert.ok(start > 0 && end > start, 'marcadores dos helpers puros presentes')
const helpersJs = transpile(script.slice(start, end), ts.ModuleKind.ESNext)
const H = new Function('apiErrMsg', 'MARCAS_ERROS', helpersJs + `
return { LEGENDA_MAX, PREVIEW_DEBOUNCE_MS, PLACEHOLDERS, LEGENDA_ERROS, EXEMPLO_TEXTO,
  chaves, placeholdersUsados, avisosDeVazio, extraiErro, legendaErrMsg, montaBody,
  produtoLabel, separaVariacoes, resumoTexto, contador, contadorClass, insereEm, marcasDasVariacoes,
  previaVazioTexto };
`)(apiError.apiErrMsg, apiError.MARCAS_ERROS)

// Teto e allowlist batem com services/marketing/legenda.py.
{
  assert.equal(H.LEGENDA_MAX, 2200)
  assert.deepEqual(
    H.PLACEHOLDERS.map((p) => p.nome),
    ['marca', 'produto', 'produto_modelo', 'whatsapp', 'email_sac', 'instagram'],
  )
  // `site` está na allowlist do backend mas fica FORA dos textos (decisão do
  // Eduardo, 16/09/2026): oferecer na tela seria convidar a usá-lo.
  assert.ok(!H.PLACEHOLDERS.some((p) => p.nome === 'site'), 'não oferece {{ site }}')
  assert.ok(H.PLACEHOLDERS.every((p) => p.vem && p.vem.length), 'todo placeholder diz de onde vem')
  assert.equal(H.chaves('produto'), '{{ produto }}')
  assert.ok(H.EXEMPLO_TEXTO.includes('{{ whatsapp }}'), 'exemplo mostra placeholder de verdade')
}

// placeholdersUsados: com/sem espaço, com `-` de whitespace control, com filtro
{
  assert.deepEqual(H.placeholdersUsados('oi {{ marca }} e {{produto}}'), ['marca', 'produto'])
  assert.deepEqual(H.placeholdersUsados('{{- whatsapp }}'), ['whatsapp'])
  assert.deepEqual(H.placeholdersUsados('{{ marca|upper }} {{ marca }}'), ['marca'], 'sem repetir')
  assert.deepEqual(H.placeholdersUsados('sem placeholder nenhum'), [])
  assert.deepEqual(H.placeholdersUsados(''), [])
  assert.deepEqual(H.placeholdersUsados('{{ MARCA }}'), ['marca'], 'caixa não cria placeholder novo')
}

// avisosDeVazio: o placeholder é válido, mas PARA ESTA MARCA sai vazio.
{
  const marcaCheia = { sac_fone: '11999990000', sac_email: 'sac@teste.com' }
  const marcaVazia = { sac_fone: null, sac_email: '  ' }
  assert.deepEqual(
    H.avisosDeVazio({ texto: '{{ marca }} {{ whatsapp }}', marca: marcaCheia, temProduto: false }),
    [], 'marca com contato não avisa nada',
  )
  const a1 = H.avisosDeVazio({ texto: '{{ produto }} em oferta', marca: marcaCheia, temProduto: false })
  assert.equal(a1.length, 1)
  assert.match(a1[0], /\{\{ produto \}\} sai vazio/)
  assert.deepEqual(
    H.avisosDeVazio({ texto: '{{ produto }} em oferta', marca: marcaCheia, temProduto: true }),
    [], 'variação de produto não avisa do produto',
  )
  const a2 = H.avisosDeVazio({ texto: 'fala com a gente {{ whatsapp }} {{ email_sac }}', marca: marcaVazia, temProduto: false })
  assert.equal(a2.length, 2)
  assert.match(a2.join(' '), /Redes Sociais/, 'diz onde consertar')
  // Sem marca carregada (fallback sem /api/marcas) não inventa aviso.
  assert.deepEqual(H.avisosDeVazio({ texto: '{{ whatsapp }}', marca: null, temProduto: false }), [])
}

// extraiErro/legendaErrMsg: os DOIS formatos viram a mesma frase.
{
  // 422 do router: HTTPException(422, {"code": ..., "erro": ...}).
  const doRouter = { data: { detail: { code: 'artigo_colado_no_produto', erro: 'artigo_colado_no_produto: do' } } }
  // 422 do Pydantic (o schema barra no salvamento).
  const doSchema = { data: { detail: [{ loc: ['body', 'texto'], msg: 'Value error, artigo_colado_no_produto: do' }] } }
  for (const e of [doRouter, doSchema]) {
    const msg = H.legendaErrMsg(e)
    assert.match(msg, /Tire o artigo antes do \{\{ produto \}\}/, 'frase, não código')
    assert.ok(!msg.includes('artigo_colado_no_produto'), 'sem código cru na tela')
    assert.ok(msg.includes('(do)'), 'diz QUAL palavra está colada')
  }
  assert.deepEqual(H.extraiErro(doSchema), { code: 'artigo_colado_no_produto', detalhe: 'do' })
  assert.match(
    H.legendaErrMsg({ data: { detail: { code: 'placeholder_desconhecido', erro: 'placeholder_desconhecido: produtoo' } } }),
    /Esse placeholder não existe.*\(produtoo\)/,
  )
  assert.match(H.legendaErrMsg({ data: { detail: { code: 'legenda_muito_longa' } } }), /2200 caracteres/)
  assert.match(H.legendaErrMsg({ data: { detail: { code: 'template_bloco_nao_permitido' } } }), /blocos \{% %\}/)
  assert.match(H.legendaErrMsg({ data: { detail: { code: 'forbidden' } } }), /Marketing › Criativos/)
  assert.match(H.legendaErrMsg({ data: { detail: { code: 'legenda_modelo_not_found' } } }), /não existe mais/)
  assert.match(H.legendaErrMsg({ data: { detail: { code: 'texto_required' } } }), /Escreva o texto/)
  // Backend mais velho que a tela: dizer isso é melhor que repetir "404".
  assert.match(H.legendaErrMsg({ status: 404, message: '404 Not Found' }), /API ainda não tem a biblioteca/)
  // O 404 seco do FastAPI (rota não registrada) chega assim — "Not Found" na
  // tela não diz nada pra quem está olhando.
  assert.match(H.legendaErrMsg({ status: 404, data: { detail: 'Not Found' } }), /API ainda não tem a biblioteca/)
  // Mas o 404 NOSSO continua sendo o nosso.
  assert.match(
    H.legendaErrMsg({ status: 404, data: { detail: { code: 'legenda_modelo_not_found' } } }),
    /não existe mais/,
  )
  // Código desconhecido não vira tela em branco: cai no tradutor comum.
  assert.equal(H.legendaErrMsg({ data: { detail: { code: 'marca_slug_conflict' } } }), 'Já existe uma marca com esse nome')
  assert.equal(H.legendaErrMsg({ message: 'Failed to fetch' }), 'Failed to fetch')
  // Nenhuma frase do mapa pode vazar código cru pro operador.
  for (const [code, frase] of Object.entries(H.LEGENDA_ERROS)) {
    assert.ok(!frase.includes(code), `frase de ${code} não repete o código`)
  }
}

// montaBody: marca_id só no POST; product_id sempre, com null EXPLÍCITO
{
  const f = { product_id: '', texto: '  Oi {{ marca }}  ', ativo: true }
  const novo = H.montaBody(f, 'create', 'm1')
  assert.deepEqual(novo, { product_id: null, texto: 'Oi {{ marca }}', ativo: true, marca_id: 'm1' })
  const edit = H.montaBody(f, 'edit', 'm1')
  assert.ok(!('marca_id' in edit), 'PATCH não manda marca_id (coluna NOT NULL; LegendaModeloPatch nem aceita)')
  assert.equal(edit.product_id, null, 'null explícito = vira padrão da marca')
  assert.equal(H.montaBody({ ...f, product_id: 'p9' }, 'edit', 'm1').product_id, 'p9')
  assert.equal(H.montaBody({ ...f, ativo: false }, 'edit', 'm1').ativo, false)
}

// produtoLabel / separaVariacoes: padrão da marca em cima, produtos agrupados
{
  assert.equal(H.produtoLabel({ product_nome: 'Cafeteira', product_sku: 'dg017' }), 'Cafeteira (dg017)')
  assert.equal(H.produtoLabel({ product_nome: 'Cafeteira', product_sku: null }), 'Cafeteira')
  assert.equal(H.produtoLabel({ product_nome: null, product_sku: 'dg017' }), 'dg017')
  assert.equal(H.produtoLabel({}), 'produto sem cadastro')

  const l = (over) => ({ id: 'x', product_id: null, product_nome: null, product_sku: null, texto: 't', ativo: true, ...over })
  const { padrao, produtos } = H.separaVariacoes([
    l({ id: 'a' }),
    l({ id: 'b', product_id: 'p2', product_nome: 'Ventilador', product_sku: 'v1' }),
    l({ id: 'c', product_id: 'p1', product_nome: 'Cafeteira', product_sku: 'c1' }),
    l({ id: 'd', product_id: 'p1', product_nome: 'Cafeteira', product_sku: 'c1' }),
    l({ id: 'e' }),
  ])
  assert.deepEqual(padrao.map((x) => x.id), ['a', 'e'])
  assert.deepEqual(produtos.map((g) => g.label), ['Cafeteira (c1)', 'Ventilador (v1)'], 'grupos em ordem alfabética')
  assert.deepEqual(produtos[0].itens.map((x) => x.id), ['c', 'd'], 'várias variações no mesmo produto (é o rodízio)')
  assert.equal(produtos[0].key, 'p1')
  assert.deepEqual(H.separaVariacoes([]), { padrao: [], produtos: [] })
}

// previaVazioTexto: com erro na tela, "escreva o texto" seria mentira
{
  assert.match(H.previaVazioTexto({ loading: true, erro: '' }), /Renderizando/)
  assert.match(H.previaVazioTexto({ loading: false, erro: 'deu ruim' }), /Prévia indisponível/)
  assert.match(H.previaVazioTexto({ loading: false, erro: '' }), /Escreva o texto/)
  assert.match(H.previaVazioTexto({ loading: true, erro: 'deu ruim' }), /Renderizando/, 'carregando manda no texto')
}

// resumoTexto / contador / contadorClass
{
  assert.equal(H.resumoTexto('linha 1\n\nlinha 2'), 'linha 1 linha 2', 'cartão mostra uma linha só')
  assert.equal(H.resumoTexto('abcdef', 3), 'abc…')
  assert.equal(H.resumoTexto(''), '')
  assert.equal(H.contador(412), '412/2200')
  assert.equal(H.contadorClass(412), 'text-muted-foreground')
  assert.match(H.contadorClass(2200), /amber/, 'no teto o texto foi cortado — avisa')
}

// insereEm: o clique no chip do placeholder põe o texto no cursor
{
  assert.equal(H.insereEm('oi mundo', 3, 3, '{{ marca }}'), 'oi {{ marca }}mundo')
  assert.equal(H.insereEm('oi mundo', 3, 8, '{{ marca }}'), 'oi {{ marca }}', 'seleção é substituída')
  assert.equal(H.insereEm('', 0, 0, 'x'), 'x')
  assert.equal(H.insereEm('abc', 99, 99, 'x'), 'abcx', 'cursor fora do texto não quebra')
  assert.equal(H.insereEm('abc', -5, 1, 'x'), 'xbc')
}

// marcasDasVariacoes: fallback de quem não tem `marcas:view`
{
  const v = (marca_id, marca_nome) => ({ marca_id, marca_nome })
  const out = H.marcasDasVariacoes([v('m2', 'Zebra'), v('m1', 'Abelha'), v('m2', 'Zebra'), v('', '')])
  assert.deepEqual(out.map((m) => [m.id, m.nome]), [['m1', 'Abelha'], ['m2', 'Zebra']], 'sem repetir, em ordem')
  assert.deepEqual(H.marcasDasVariacoes([]), [])
  assert.equal(H.marcasDasVariacoes([v('m3', '')])[0].nome, 'm3', 'sem nome cai no id, nunca vazio')
}

// ---------------------------------------------------------------- script setup
const pageScript = script.replace(/^import[\s\S]*?from\s+'[^']+'\s*$/gm, '')
const exportsForTest = `return {
  marcas, marcaSel, marcasDegradado, lista, loading, loaded, erro, marcaAtual, separadas, totalAtivas,
  carregarMarcas, carregarLista, recarregar,
  produtos, produtoBusca, produtosLoading, produtosBloqueado, buscarProdutos, produtoOptions,
  modalAberto, editando, form, saving, modalErr, modo, avisos,
  abrirCriar, abrirEditar, fecharModal, inserePlaceholder,
  previa, previaLoading, previaErro, previaStale, agendaPrevia, atualizaPrevia,
  salvar, remover, legendaErrMsg,
}`
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const factory = new AsyncFunction(
  'computed', 'nextTick', 'onMounted', 'ref', 'watch',
  'definePageMeta', 'useApi', 'useToasts', 'useCan',
  'TABS_CADASTROS', 'apiErrMsg', 'MARCAS_ERROS',
  'setTimeout', 'clearTimeout', 'window',
  transpile(pageScript, ts.ModuleKind.ESNext) + '\n' + exportsForTest,
)

const MARCAS_FAKE = [
  { id: 'm1', nome: 'Poofy', slug: 'poofy', ativo: true, sac_fone: '11999990000', sac_email: 'sac@poofy.x' },
  { id: 'm2', nome: 'Charlots', slug: 'charlots', ativo: true, sac_fone: null, sac_email: null },
]
const PRODUTOS_FAKE = [
  { id: 'p1', sku: 'dg017', name: 'Cafeteira Elétrica' },
  { id: 'p2', sku: 'dg018', name: 'Ventilador de Mesa' },
]
function modelo(over = {}) {
  return {
    id: 'l1', marca_id: 'm1', marca_nome: 'Poofy', product_id: null, product_nome: null, product_sku: null,
    texto: 'Conheça a {{ marca }} — chama no {{ whatsapp }}', ativo: true,
    created_at: '2026-09-16T10:00:00+00:00', updated_at: '2026-09-16T10:00:00+00:00', ...over,
  }
}
const ERR403 = { status: 403, data: { detail: { code: 'forbidden' } } }

// A prévia devolve o texto RENDERIZADO (o backend troca os placeholders) —
// aqui basta uma troca boba: o que importa é de onde o texto vem e quando.
function renderFake(body) {
  const texto = String(body.texto || '')
    .replace(/\{\{\s*marca\s*\}\}/g, 'Poofy')
    .replace(/\{\{\s*produto\s*\}\}/g, body.product_id === 'p1' ? 'Cafeteira Elétrica' : '')
    .replace(/\{\{\s*whatsapp\s*\}\}/g, '(11) 99999-0000')
    .replace(/\{\{\s*email_sac\s*\}\}/g, 'sac@poofy.x')
    .replace(/\{\{\s*instagram\s*\}\}/g, '@poofy')
  return { texto, tamanho: texto.length }
}

async function tela({
  canEdit = true, marcasErro = null, listaErro = null, produtosErro = null,
  variacoes = [modelo()], confirmAnswer = true, previewResp = renderFake, saveError = null,
} = {}) {
  const calls = []
  const toastLog = []
  const confirms = []
  const timers = []
  const dados = variacoes.map((v) => ({ ...v }))
  const api = (url, opts) => {
    calls.push({ url, opts })
    if (url === '/api/marcas?ativo=true') {
      return marcasErro ? Promise.reject(marcasErro) : Promise.resolve(MARCAS_FAKE.map((m) => ({ ...m })))
    }
    if (url.startsWith('/api/products')) {
      return produtosErro ? Promise.reject(produtosErro) : Promise.resolve({ items: PRODUTOS_FAKE, total: 2 })
    }
    if (url === '/api/marketing/legendas/preview') {
      assert.equal(opts?.method, 'POST', 'prévia é POST')
      return previewResp === 'erro'
        ? Promise.reject({ data: { detail: [{ loc: ['body', 'texto'], msg: 'Value error, artigo_colado_no_produto: do' }] } })
        : Promise.resolve(previewResp(opts.body))
    }
    if (url === '/api/marketing/legendas' && opts?.method === 'POST') {
      if (saveError) return Promise.reject(saveError)
      const novo = modelo({ id: `novo-${dados.length}`, ...opts.body, marca_id: opts.body.marca_id })
      dados.push(novo)
      return Promise.resolve({ ...novo })
    }
    if (url === '/api/marketing/legendas' && !opts?.method) {
      // Sem filtro: é o fallback de quem não enxerga /api/marcas.
      return listaErro ? Promise.reject(listaErro) : Promise.resolve(dados.map((v) => ({ ...v })))
    }
    if (url.startsWith('/api/marketing/legendas?marca_id=')) {
      if (listaErro) return Promise.reject(listaErro)
      const id = decodeURIComponent(url.split('marca_id=')[1])
      return Promise.resolve(dados.filter((v) => v.marca_id === id).map((v) => ({ ...v })))
    }
    const m = /^\/api\/marketing\/legendas\/([^/?]+)$/.exec(url)
    if (m && opts?.method === 'PATCH') {
      if (saveError) return Promise.reject(saveError)
      const alvo = dados.find((v) => v.id === m[1])
      assert.ok(alvo, `variação conhecida: ${url}`)
      Object.assign(alvo, opts.body)
      return Promise.resolve({ ...alvo })
    }
    if (m && opts?.method === 'DELETE') {
      const i = dados.findIndex((v) => v.id === m[1])
      // Apagar o que já não existe é o 404 do router — a tela tem que
      // aguentar (outra aba apagou primeiro).
      if (i < 0) return Promise.reject({ status: 404, data: { detail: { code: 'legenda_modelo_not_found' } } })
      dados.splice(i, 1)
      return Promise.resolve(null)
    }
    return Promise.reject(new Error(`api falso não conhece ${url}`))
  }
  const toasts = {
    success: (...a) => toastLog.push(['success', ...a]),
    error: (...a) => toastLog.push(['error', ...a]),
    warning: (...a) => toastLog.push(['warning', ...a]),
    info: (...a) => toastLog.push(['info', ...a]),
    push: () => 1,
    dismiss: () => {},
  }
  const fakeTimeout = (fn, ms) => { timers.push({ fn, ms, cleared: false }); return timers.length }
  const fakeClear = (id) => { if (timers[id - 1]) timers[id - 1].cleared = true }
  const s = await factory(
    Vue.computed, Vue.nextTick, (fn) => fn(), Vue.ref, Vue.watch,
    () => {}, () => ({ api }), () => toasts, (_r, action) => Vue.ref(action === 'view' ? true : canEdit),
    [], apiError.apiErrMsg, apiError.MARCAS_ERROS,
    fakeTimeout, fakeClear,
    { confirm: (msg) => { confirms.push(msg); return confirmAnswer } },
  )
  await settle()
  return { s, calls, toastLog, confirms, timers, dados }
}
const settle = () => new Promise(setImmediate)
const urls = (calls) => calls.map((c) => c.url)
const previews = (calls) => calls.filter((c) => c.url === '/api/marketing/legendas/preview')
// Escrita de verdade. A prévia TAMBÉM é POST (e não grava nada), então fica
// de fora: sem isso "só-view não escreve" passaria a falhar por causa dela.
const escritas = (calls) => calls.filter(
  (c) => ['POST', 'PATCH', 'DELETE'].includes(c.opts?.method) && c.url !== '/api/marketing/legendas/preview',
)
// Dispara o timer da prévia que ainda está de pé (o debounce).
async function corrPrevia(timers) {
  const t = timers.filter((x) => !x.cleared).at(-1)
  assert.ok(t, 'havia um debounce pendente')
  t.fn()
  await settle()
}

async function run() {
  // Carga inicial: marcas do cadastro, primeira marca escolhida, lista dela.
  {
    const { s, calls } = await tela({
      variacoes: [
        modelo({ id: 'l1' }),
        modelo({ id: 'l2', product_id: 'p1', product_nome: 'Cafeteira Elétrica', product_sku: 'dg017' }),
        modelo({ id: 'l3', marca_id: 'm2', marca_nome: 'Charlots' }),
      ],
    })
    assert.ok(urls(calls).includes('/api/marcas?ativo=true'), 'busca as marcas do cadastro')
    assert.equal(s.marcaSel.value, 'm1', 'primeira marca já vem escolhida')
    assert.equal(s.marcasDegradado.value, false)
    assert.ok(urls(calls).includes('/api/marketing/legendas?marca_id=m1'), 'lista filtrada pela marca')
    assert.deepEqual(s.lista.value.map((x) => x.id), ['l1', 'l2'], 'só as variações da marca escolhida')
    assert.deepEqual(s.separadas.value.padrao.map((x) => x.id), ['l1'])
    assert.deepEqual(s.separadas.value.produtos.map((g) => g.label), ['Cafeteira Elétrica (dg017)'])
    assert.equal(s.totalAtivas.value, 2)
    assert.equal(s.erro.value, '')
    assert.equal(s.loaded.value, true)
    // Nada de produtos antes de abrir o modal: a lista de produtos é de outra
    // permissão e não pode ser pedida por quem só veio olhar as legendas.
    assert.ok(!urls(calls).some((u) => u.startsWith('/api/products')), 'produtos só no modal')
  }

  // Trocar de marca recarrega a lista (é o único filtro da tela).
  {
    const { s, calls } = await tela({ variacoes: [modelo(), modelo({ id: 'l3', marca_id: 'm2', marca_nome: 'Charlots' })] })
    s.marcaSel.value = 'm2'
    await Vue.nextTick()
    await settle()
    assert.ok(urls(calls).includes('/api/marketing/legendas?marca_id=m2'))
    assert.deepEqual(s.lista.value.map((x) => x.id), ['l3'])
    assert.equal(s.marcaAtual.value.nome, 'Charlots')
  }

  // Marca sem nenhuma variação: é o estado que o robô recusa (`sem_legenda`).
  {
    const { s } = await tela({ variacoes: [] })
    assert.deepEqual(s.lista.value, [])
    assert.equal(s.loaded.value, true)
    assert.equal(s.erro.value, '', 'vazio não é erro')
  }

  // Sem `marcas:view`: /api/marcas recusa e a tela cai nas marcas que já têm
  // legenda (GET sem filtro) em vez de morrer com o select vazio.
  {
    const { s, calls } = await tela({
      marcasErro: ERR403,
      variacoes: [modelo({ id: 'l3', marca_id: 'm2', marca_nome: 'Charlots' }), modelo()],
    })
    assert.equal(s.marcasDegradado.value, true)
    assert.deepEqual(s.marcas.value.map((m) => m.nome), ['Charlots', 'Poofy'])
    assert.ok(urls(calls).includes('/api/marketing/legendas'), 'fallback lê a lista inteira uma vez')
    assert.equal(s.marcaSel.value, 'm2', 'escolhe a primeira que sobrou')
    assert.equal(s.erro.value, '', 'degradou sem erro vermelho')
  }

  // 403 na própria biblioteca: mensagem em português, tela inteira, sem lista.
  {
    const { s } = await tela({ listaErro: ERR403 })
    assert.deepEqual(s.lista.value, [])
    assert.match(s.erro.value, /Sem permissão/)
    assert.match(s.erro.value, /Marketing › Criativos/, 'diz QUAL permissão falta')
  }

  // 404 (API mais velha que a tela): idem, sem "404 Not Found" cru.
  {
    const { s } = await tela({ marcasErro: ERR403, listaErro: { status: 404, message: '[GET] "/api/...": 404 Not Found' } })
    assert.match(s.erro.value, /API ainda não tem a biblioteca/)
  }

  // Modal de criação: produtos carregados, prévia imediata não sai com o
  // textarea vazio (o schema recusaria com texto_required).
  {
    const { s, calls } = await tela()
    s.abrirCriar(null)
    await settle()
    assert.equal(s.modalAberto.value, true)
    assert.equal(s.modo.value, 'create')
    assert.deepEqual(s.form.value, { product_id: '', texto: '', ativo: true })
    assert.ok(urls(calls).some((u) => u.startsWith('/api/products')), 'busca produtos ao abrir')
    assert.equal(previews(calls).length, 0, 'texto vazio não vai pro /preview')
    assert.equal(s.previa.value, null)
    assert.deepEqual(s.produtoOptions.value.map((o) => o.id), ['p1', 'p2'])
  }

  // Prévia: debounce, corpo certo, tamanho do backend e o rótulo de "mudou".
  {
    const { s, calls, timers } = await tela()
    s.abrirCriar(null)
    await settle()
    s.form.value.texto = 'Conheça a {{ marca }} — chama no {{ whatsapp }}'
    s.agendaPrevia()
    assert.equal(previews(calls).length, 0, 'não chama a cada tecla')
    const pendente = timers.filter((t) => !t.cleared).at(-1)
    assert.equal(pendente.ms, 500, 'debounce de meio segundo')
    await corrPrevia(timers)
    const p = previews(calls).at(-1)
    assert.deepEqual(p.opts.body, {
      texto: 'Conheça a {{ marca }} — chama no {{ whatsapp }}',
      marca_id: 'm1',
      product_id: null,
    })
    assert.equal(s.previa.value.texto, 'Conheça a Poofy — chama no (11) 99999-0000')
    assert.equal(s.previa.value.tamanho, s.previa.value.texto.length)
    assert.equal(s.previaErro.value, '')
    assert.equal(s.previaStale.value, false)
    // Digitar de novo: a prévia na tela passa a ser do texto anterior.
    s.form.value.texto += ' 💛'
    assert.equal(s.previaStale.value, true)
    // Digitar depressa cancela o debounce anterior — uma chamada, não duas.
    const antes = previews(calls).length
    s.agendaPrevia()
    s.agendaPrevia()
    await corrPrevia(timers)
    assert.equal(previews(calls).length, antes + 1)
    assert.equal(s.previaStale.value, false)
  }

  // Resposta atrasada NUNCA sobrescreve a mais nova: o que está na caixa é o
  // que vai pro Instagram, e a prévia tem que ser dele.
  {
    const pendentes = []
    const { s, timers } = await tela({
      previewResp: (body) => new Promise((resolve) => pendentes.push(() => resolve(renderFake(body)))),
    })
    s.abrirCriar(null)
    await settle()
    s.form.value.texto = 'primeiro'
    s.agendaPrevia()
    await corrPrevia(timers)
    s.form.value.texto = 'segundo'
    s.agendaPrevia()
    await corrPrevia(timers)
    assert.equal(pendentes.length, 2)
    pendentes[1]()
    await settle()
    pendentes[0]()
    await settle()
    assert.equal(s.previa.value.texto, 'segundo', 'a resposta velha foi descartada')
  }

  // Template inválido: FRASE pro operador, não "artigo_colado_no_produto".
  {
    const { s, timers } = await tela({ previewResp: 'erro' })
    s.abrirCriar(null)
    await settle()
    s.form.value.texto = 'uso do {{ produto }}'
    s.agendaPrevia()
    await corrPrevia(timers)
    assert.equal(s.previa.value, null)
    assert.match(s.previaErro.value, /Tire o artigo antes do \{\{ produto \}\}/)
    assert.ok(!s.previaErro.value.includes('_'), 'sem código cru')
    assert.equal(s.previaLoading.value, false)
  }

  // Aviso do placeholder que sai VAZIO nesta marca (a prévia mostra o buraco;
  // o aviso diz de onde ele vem).
  {
    const { s } = await tela()
    s.abrirCriar(null)
    await settle()
    s.form.value.texto = '{{ produto }} com desconto'
    assert.equal(s.avisos.value.length, 1)
    assert.match(s.avisos.value[0], /padrão da marca não tem produto/)
    s.form.value.product_id = 'p1'
    assert.deepEqual(s.avisos.value, [])
  }

  // Criar: POST com marca_id, a variação entra na lista sem recarregar.
  {
    const { s, calls, toastLog } = await tela({ variacoes: [] })
    s.abrirCriar(null)
    await settle()
    s.form.value.texto = '  Chegou novidade na {{ marca }}  '
    await s.salvar()
    const post = escritas(calls).at(-1)
    assert.deepEqual(post.opts.body, {
      marca_id: 'm1',
      product_id: null,
      texto: 'Chegou novidade na {{ marca }}',
      ativo: true,
    })
    assert.equal(s.lista.value.length, 1, 'aparece na hora')
    assert.equal(s.modalAberto.value, false)
    assert.equal(s.saving.value, false)
    assert.deepEqual(toastLog.at(-1).slice(0, 2), ['success', 'Variação cadastrada'])
  }

  // Criar variação DE PRODUTO a partir do bloco do produto.
  {
    const { s, calls } = await tela({
      variacoes: [modelo({ id: 'l2', product_id: 'p1', product_nome: 'Cafeteira Elétrica', product_sku: 'dg017' })],
    })
    s.abrirCriar('p1')
    await settle()
    assert.equal(s.form.value.product_id, 'p1', 'já nasce no produto do bloco')
    s.form.value.texto = '{{ produto }} — chegou'
    await s.salvar()
    assert.equal(escritas(calls).at(-1).opts.body.product_id, 'p1')
  }

  // Editar: PATCH sem marca_id; virar padrão da marca manda product_id null.
  {
    const { s, calls } = await tela({
      variacoes: [modelo({ id: 'l2', product_id: 'p1', product_nome: 'Cafeteira Elétrica', product_sku: 'dg017' })],
    })
    s.abrirEditar(s.lista.value[0])
    await settle()
    assert.equal(s.modo.value, 'edit')
    assert.equal(s.form.value.product_id, 'p1')
    // A variação aberta continua no select mesmo sem a busca trazê-la.
    assert.ok(s.produtoOptions.value.some((o) => o.id === 'p1'))
    // Abrir uma variação que já tem texto pede a prévia na hora.
    assert.equal(previews(calls).length, 1, 'prévia imediata ao abrir com texto')
    s.form.value.product_id = ''
    s.form.value.ativo = false
    await s.salvar()
    const patch = calls.filter((c) => c.opts?.method === 'PATCH').at(-1)
    assert.equal(patch.url, '/api/marketing/legendas/l2')
    assert.ok(!('marca_id' in patch.opts.body))
    assert.equal(patch.opts.body.product_id, null)
    assert.equal(patch.opts.body.ativo, false)
    assert.equal(s.lista.value.length, 1, 'edição não duplica a linha')
    assert.equal(s.separadas.value.padrao.length, 1, 'virou padrão da marca na hora')
  }

  // Erro no salvamento: modal continua aberto com a frase (o texto do
  // operador não pode sumir).
  {
    const { s } = await tela({
      saveError: { data: { detail: [{ loc: ['body', 'texto'], msg: 'Value error, legenda_muito_longa' }] } },
    })
    s.abrirCriar(null)
    await settle()
    s.form.value.texto = 'x'.repeat(10)
    await s.salvar()
    assert.equal(s.modalAberto.value, true, 'modal fica aberto pra consertar')
    assert.match(s.modalErr.value, /2200 caracteres/)
    assert.equal(s.saving.value, false)
    assert.equal(s.form.value.texto.length, 10, 'o texto continua lá')
  }

  // Texto vazio nem sai da tela.
  {
    const { s, calls } = await tela()
    s.abrirCriar(null)
    await settle()
    await s.salvar()
    assert.deepEqual(escritas(calls), [], 'texto vazio não vai pro servidor')
    assert.match(s.modalErr.value, /Escreva o texto/)
  }

  // Apagar: confirm antes, e o recado explica que post publicado não muda.
  {
    const { s, calls, confirms, toastLog } = await tela()
    await s.remover(s.lista.value[0])
    assert.equal(confirms.length, 1)
    assert.match(confirms[0], /padrão da marca/)
    assert.match(confirms[0], /já saíram não mudam/)
    assert.match(confirms[0], /ativa/, 'oferece a saída sem perder o texto')
    assert.equal(calls.filter((c) => c.opts?.method === 'DELETE').at(-1).url, '/api/marketing/legendas/l1')
    assert.equal(s.lista.value.length, 0)
    assert.deepEqual(toastLog.at(-1).slice(0, 2), ['success', 'Variação apagada'])
  }
  {
    const { s, calls, confirms } = await tela({ confirmAnswer: false })
    await s.remover(s.lista.value[0])
    assert.equal(confirms.length, 1)
    assert.equal(calls.filter((c) => c.opts?.method === 'DELETE').length, 0, 'confirm recusado não apaga')
    assert.equal(s.lista.value.length, 1)
  }
  // Apagar recusado pelo backend vira toast, não tela em branco.
  {
    const { s, toastLog } = await tela()
    s.lista.value[0].id = 'sumiu'
    await s.remover(s.lista.value[0])
    assert.equal(toastLog.at(-1)[0], 'error')
    assert.match(toastLog.at(-1)[2], /não existe mais/, 'diz o que houve, em português')
    assert.equal(s.lista.value.length, 1, 'a linha continua na tela')
  }

  // Sem `produtos:view`: o modal continua servindo pro padrão da marca.
  {
    const { s } = await tela({ produtosErro: ERR403 })
    s.abrirCriar(null)
    await settle()
    assert.equal(s.produtosBloqueado.value, true)
    assert.deepEqual(s.produtoOptions.value, [], 'só resta o padrão da marca (a opção vazia do select)')
    assert.equal(s.modalAberto.value, true)
    assert.equal(s.erro.value, '', 'não é erro da tela')
    // Editando uma variação de produto, o produto dela continua no select.
    s.abrirEditar(modelo({ id: 'l2', product_id: 'p1', product_nome: 'Cafeteira Elétrica', product_sku: 'dg017' }))
    await settle()
    assert.deepEqual(s.produtoOptions.value, [{ id: 'p1', label: 'Cafeteira Elétrica (dg017)' }])
  }

  // Chip do placeholder escreve no texto (sem textarea no teste, vai pro fim).
  {
    const { s } = await tela()
    s.abrirCriar(null)
    await settle()
    s.form.value.texto = 'fala com a gente '
    s.inserePlaceholder('whatsapp')
    assert.equal(s.form.value.texto, 'fala com a gente {{ whatsapp }}')
  }

  // Só-view: enxerga tudo, não cria, não apaga, não salva.
  {
    const { s, calls, confirms } = await tela({ canEdit: false })
    assert.equal(s.lista.value.length, 1, 'quem só olha vê a biblioteca')
    s.abrirCriar(null)
    assert.equal(s.modalAberto.value, false, 'só-view não abre o modal de criação')
    s.abrirEditar(s.lista.value[0])
    await settle()
    assert.equal(s.modalAberto.value, true, 'mas pode ler a variação')
    s.form.value.texto = 'tentando editar'
    await s.salvar()
    await s.remover(s.lista.value[0])
    assert.equal(confirms.length, 0, 'nem pergunta')
    assert.deepEqual(escritas(calls), [], 'só-view não grava nada')
  }

  // Fechar o modal cancela o debounce pendente: nenhuma prévia chega depois.
  {
    const { s, calls, timers } = await tela()
    s.abrirCriar(null)
    await settle()
    s.form.value.texto = 'texto qualquer'
    s.agendaPrevia()
    const antes = previews(calls).length
    s.fecharModal()
    assert.ok(timers.every((t) => t.cleared || t.ms !== 500), 'debounce cancelado ao fechar')
    await settle()
    assert.equal(previews(calls).length, antes)
    assert.equal(s.previa.value, null)
    assert.equal(s.editando.value, null)
  }
}

run().then(() => {
  console.log('PASS: SFC parse + template compile; higiene (permissão, confirm, nada de roteiro, {{ }} fora de atributo); helpers puros (cascata padrão/produto, erros de template nos dois formatos, avisos de placeholder vazio); script setup com api falso (carga, troca de marca, 403/404 degradando, prévia com debounce e corrida, criar/editar/apagar, só-view)')
}).catch((e) => {
  console.error(e)
  process.exit(1)
})
