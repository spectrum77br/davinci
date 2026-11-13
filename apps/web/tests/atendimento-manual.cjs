// node tests/atendimento-manual.cjs — a aba "Manual do agente" do Atendimento (13/11/2026; antes
// "Manual da IA"): `components/AtendimentoManual.vue` e `AtendimentoManualForm.vue`. O Eduardo:
// "essa parte aqui não é toda da IA? a gente nem usa mais, usa o agente". O que a tela garante:
//  - só as regras POR ASSUNTO (as que o agente lê pelo `consultar_manual`): segurança e estilo
//    ficam no banco, fora da tela; o texto do topo diz que o agente consulta as regras, que o
//    jeito de responder vem do banco de respostas e que, divergindo, vale a regra — sem o
//    "Nada aqui libera…", sem `precisa_humano=true` e sem "resposta vazia";
//  - as funções puras: os pedaços do QUANDO/FAÇA (a lacuna vira chip, `precisa_humano=true` o
//    selo "pessoa decide", `=false` "agente responde" — o texto não muda), quem editou por
//    último, a busca sem acento, a ordem em que o agente lê, os grupos por assunto;
//  - a tela de verdade (setup + SSR, API falsa): os assuntos na ordem do manual com as ativas e
//    desativadas, "Sem assunto" no fim e avisado, o assunto fora da lista, os chips, "editada por
//    Ana em 12/11", a busca e o filtro da plataforma, as ações de hoje (criar no assunto,
//    editar, ativar/desativar, apagar) — e quem não edita só vê;
//  - o formulário: sem o "Tipo"; a regra nova pede o assunto; na edição, a sem assunto salva;
//  - a API devolve quem mexeu por último (`atualizado_por_nome`) e a aba se chama "Manual do agente".
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
const web = (rel) => fs.readFileSync(path.resolve(__dirname, '..', rel), 'utf8')
const semComentarios = (html) => html.replace(/<!--[\s\S]*?-->/g, '')
const esperar = (ms = 0) => new Promise((r) => setTimeout(r, ms))

const tela = sfc('../components/AtendimentoManual.vue')
const formSfc = sfc('../components/AtendimentoManualForm.vue')
const M = requerer('~/components/AtendimentoManual.vue')

// ------------------------------------------------ o que a tela diz (e o que não diz)
{
  const tpl = tela.descriptor.template.content
  const setup = tela.descriptor.scriptSetup.content
  assert.match(tpl, /<h2[^>]*>[\s\S]*Manual do agente<\/h2>/)
  const explicacao = /<p[^>]*data-explicacao>([\s\S]*?)<\/p>/.exec(tpl)[1].replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim()
  // 13/11/2026 (o Eduardo: o banco é a fonte principal): quando divergem, vale o banco.
  assert.equal(explicacao, 'A fonte principal do agente é o banco de respostas: o que a equipe já respondeu (e a correção mais nova vem primeiro). Estas regras ficam para o que o banco não tem: a política e os fatos de cada assunto. Quando os dois divergem, vale o banco — menos as regras que nunca mudam (promessa, Pix, fora da plataforma, dado pessoal).')
  for (const proibido of [/Nada aqui libera/, /resposta vazia/i, /precisa_humano/, /Manual da IA/, /Segurança/, /Estilo/, /TIPOS_REGRA/, /ICONE_TIPO/]) {
    assert.doesNotMatch(tpl, proibido, String(proibido))
  }
  assert.doesNotMatch(setup, /TIPOS_REGRA|ShieldAlert|Palette/)
  // Só as regras por assunto chegam à tela; o banco não é mexido (nenhuma rota nova).
  assert.match(setup, /const doAgente = computed\(\(\) => regras\.value\.filter\(\(r\) => tipoRegraDe\(r\) === 'categoria'\)\)/)
  const rotas = [...setup.matchAll(/api<[^>]*>\(\s*`?'?([^'`,)]+)/g)].map((m) => m[1])
  assert.ok(rotas.every((u) => /^\/api\/atendimento\/(regras|categorias|fatos)/.test(u)), rotas.join(' '))
  // O formulário: sem o "Tipo"; o assunto vem primeiro.
  const form = formSfc.descriptor.template.content
  assert.doesNotMatch(form, />Tipo</)
  assert.doesNotMatch(formSfc.fonte, /TIPOS_REGRA|hintTipo/)
  assert.match(form, /<select v-model="form\.categoria"[^>]*aria-label="assunto da regra"/)
  assert.match(form, /o agente não sabe qual seguir/)
  // A API: quem mexeu por último.
  assert.match(api('schemas/atendimento.py'), /class RegraOut\(BaseModel\):[\s\S]*?atualizado_por_nome: str \| None = None/)
  assert.match(api('routers/atendimento.py'), /atualizado_por_nome=\(nomes or \{\}\)\.get\(r\.atualizado_por\)/)
  // A aba.
  assert.match(web('pages/atendimento.vue'), /\{ value: 'manual', label: 'Manual do agente', icon: BookOpen \}/)
}

// ------------------------------------------------ as funções puras
{
  const tipos = (t) => M.pedacos(t).map((p) => [p.tipo, p.tipo === 'texto' ? p.valor : p.rotulo || p.valor])
  assert.deepEqual(tipos('Mande o {rastreio} do pedido {numero_pedido} (precisa_humano=true).'), [
    ['texto', 'Mande o '], ['lacuna', 'código de rastreio'], ['texto', ' do pedido '], ['lacuna', 'nº do pedido na plataforma'],
    ['texto', ' ('], ['pessoa', 'precisa_humano=true'], ['texto', ').'],
  ])
  assert.deepEqual(tipos('consulta simples: precisa_humano = false'), [['texto', 'consulta simples: '], ['agente', 'precisa_humano = false']])
  assert.deepEqual(tipos('sem marca nenhuma'), [['texto', 'sem marca nenhuma']])
  assert.deepEqual(tipos(''), [])
  assert.equal(M.pedacos('a {lacuna_nova} b')[1].rotulo, 'lacuna nova', 'a lacuna desconhecida vira o nome dela')
  // O texto inteiro volta igual (só a exibição muda).
  const t = 'Faça X {nf_numero} e precisa_humano=true; y.'
  assert.equal(M.pedacos(t).map((p) => (p.tipo === 'lacuna' ? `{${p.valor}}` : p.valor)).join(''), t)
  assert.equal(M.quemEditou({ atualizado_por_nome: 'Ana', updated_at: '2026-11-12T15:00:00Z' }), 'editada por Ana em 12/11')
  assert.equal(M.quemEditou({ atualizado_por_nome: null, updated_at: '2026-11-13T02:30:00Z' }), 'editada em 12/11', 'o dia de São Paulo')
  assert.equal(M.quemEditou({ atualizado_por_nome: ' ', updated_at: null }), '')
  const regra = { quando: 'Pedido de reembolso', faca: 'Explique o estorno pela plataforma', categoria: 'reembolso' }
  assert.equal(M.bateComBusca(regra, ''), true)
  assert.equal(M.bateComBusca(regra, 'ESTÓRNO plataforma'), true, 'sem acento nem caixa, todas as palavras')
  assert.equal(M.bateComBusca(regra, 'estorno troca'), false)
  assert.equal(M.bateComBusca(regra, 'Estorno', 'Reembolso e estorno'), true)
  assert.equal(M.bateComBusca(regra, 'devolução', 'Troca ou devolução'), true, 'o nome do assunto também')
  const r = (id, extra = {}) => ({ id, quando: id, faca: id, plataforma: null, canal: null, ativa: true, updated_at: null, tipo: 'categoria', categoria: 'rastreio', prioridade: 100, ...extra })
  assert.deepEqual(M.ordemDoAgente([r('ml', { plataforma: 'ml' }), r('p40', { prioridade: 40, plataforma: 'shopee' }), r('geral'), r('ml-pergunta', { plataforma: 'ml', canal: 'pergunta' })]).map((x) => x.id), ['p40', 'geral', 'ml', 'ml-pergunta'])
  const categorias = [{ id: 'reembolso', nome: 'Reembolso e estorno', ordem: 1 }, { id: 'rastreio', nome: 'Rastreio e atraso', descricao: 'Já postado.', ordem: 2 }]
  const todas = [r('a'), r('b', { ativa: false }), r('c', { categoria: 'reembolso' }), r('d', { categoria: null }), r('e', { categoria: 'velho' })]
  const grupos = M.agruparPorAssunto(todas.filter((x) => x.id !== 'b'), categorias, todas)
  assert.deepEqual(grupos.map((g) => [g.chave, g.titulo, g.ativas, g.inativas, g.foraDaLista]), [
    ['reembolso', 'Reembolso e estorno', 1, 0, false],
    ['rastreio', 'Rastreio e atraso', 1, 1, false],
    ['velho', 'velho', 1, 0, true],
    ['-', 'Sem assunto', 1, 0, false],
  ], 'na ordem do manual; fora da lista depois; sem assunto no fim; as contagens sem o filtro')
  assert.equal(grupos[1].hint, 'Já postado.')
  assert.deepEqual(grupos[1].regras.map((x) => x.id), ['a'], 'as regras: só as que passaram no filtro')
}

// ------------------------------------------------ a tela de verdade (setup + SSR, API falsa)
const BOTAO = { props: ['disabled', 'size', 'variant'], setup(p, { slots, attrs }) { return () => Vue.h('button', { ...attrs, disabled: p.disabled || undefined }, slots.default?.()) } }
const formMod = (() => {
  const { content } = compileScript(formSfc.descriptor, { id: 'form', inlineTemplate: true })
  const mod = { exports: {} }
  const g = { computed: Vue.computed, ref: Vue.ref, watch: Vue.watch, nextTick: Vue.nextTick }
  new Function('require', 'module', 'exports', ...Object.keys(g), transpile(content))(requerer, mod, mod.exports, ...Object.values(g))
  return mod.exports.default
})()
const script = compileScript(tela.descriptor, { id: 'manual', inlineTemplate: false })
const tpl = compileTemplate({ source: tela.descriptor.template.content, filename: tela.filename, id: 'manual', compilerOptions: { isTS: true, bindingMetadata: script.bindings } })
assert.deepEqual(tpl.errors, [], 'o template compila')

function montar(props, respostas) {
  const chamadas = []
  const avisos = []
  const montados = []
  const responder = async (url, opts = {}) => {
    const metodo = opts.method || 'GET'
    chamadas.push({ url, metodo, body: opts.body ?? null })
    const valor = respostas[`${metodo} ${url}`]
    if (valor === undefined) throw new Error(`rota inesperada: ${metodo} ${url}`)
    const v = typeof valor === 'function' ? valor(opts) : valor
    if (v instanceof Error) throw v
    return structuredClone(v)
  }
  const g = {
    ref: Vue.ref, computed: Vue.computed, reactive: Vue.reactive, watch: Vue.watch, nextTick: Vue.nextTick,
    onMounted: (fn) => montados.push(fn), onBeforeUnmount: () => {},
    useApi: () => ({ api: responder }),
    useToasts: () => ({ success: (t) => avisos.push(['ok', t]), error: (t) => avisos.push(['erro', t]), info: (t) => avisos.push(['info', t]) }),
    useIsAdmin: () => Vue.computed(() => true),
  }
  const nomes = Object.keys(g)
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', ...nomes, transpile(script.content))(requerer, mod, mod.exports, ...nomes.map((n) => g[n]))
  const tmod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(tpl.code))(requerer, tmod, tmod.exports)
  const comp = mod.exports.default
  const reativo = Vue.reactive({ ...props })
  const bruto = comp.setup(reativo, { emit: () => {}, expose: () => {}, attrs: {}, slots: {} })
  const estado = Vue.proxyRefs(bruto)
  const filho = { ...comp, render: tmod.exports.render, setup: () => bruto }
  async function html() {
    const a = Vue.createSSRApp({ render: () => Vue.h(filho, reativo) })
    a.config.warnHandler = () => {}
    a.component('Button', BOTAO)
    a.component('AtendimentoManualForm', formMod)
    return semComentarios(await renderToString(a))
  }
  return { estado, chamadas, avisos, html, async montar() { for (const fn of montados) await fn(); await esperar() } }
}

const R = (id, extra) => ({ id, plataforma: null, canal: null, ativa: true, updated_at: '2026-11-10T12:00:00Z', tipo: 'categoria', prioridade: 100, em_conflito: false, conflita_com: [], atualizado_por_nome: null, ...extra })
const REGRAS = {
  regras: [
    R('seg', { tipo: 'seguranca', categoria: null, quando: 'A última mensagem é automática', faca: 'Não responder: resposta vazia, precisa_humano=true.' }),
    R('est', { tipo: 'estilo', categoria: null, quando: 'Sempre', faca: 'Assinar como Equipe Uranyx.' }),
    R('ras', { categoria: 'rastreio', prioridade: 50, quando: 'Pergunta do rastreio', faca: 'Mande o {rastreio} e a {transportadora}. Consulta simples: precisa_humano=false.', atualizado_por_nome: 'Ana', updated_at: '2026-11-12T15:00:00Z' }),
    R('ras-ml', { categoria: 'rastreio', plataforma: 'ml', canal: 'pergunta', ativa: false, quando: 'Rastreio na pergunta do anúncio', faca: 'Convide para as mensagens da compra.' }),
    R('ree', { categoria: 'reembolso', quando: 'Pedido de reembolso', faca: 'Explique o estorno do pedido {numero_pedido} (precisa_humano=true).' }),
    R('geral', { categoria: null, quando: 'Qualquer mensagem', faca: 'Seja educado.' }),
    R('velha', { categoria: 'assunto_velho', quando: 'Assunto antigo', faca: 'Texto antigo.' }),
  ],
  conflitos: [],
}
const CATEGORIAS_API = [
  { id: 'reembolso', nome: 'Reembolso e estorno', descricao: 'Dinheiro de volta.', ativa: true, ordem: 1 },
  { id: 'rastreio', nome: 'Rastreio e atraso', descricao: 'O pedido já foi postado.', ativa: true, ordem: 2 },
]
const respostasPadrao = () => ({
  'GET /api/atendimento/regras': REGRAS,
  'GET /api/atendimento/categorias': CATEGORIAS_API,
  'GET /api/atendimento/fatos': { fatos: [], contagem: { candidato: 0, ativo: 0, reprovado: 0 } },
})

async function principal() {
  {
    const t = montar({ canEdit: true, canDelete: true }, {
      ...respostasPadrao(),
      'PATCH /api/atendimento/regras/ras': (opts) => ({ ...REGRAS.regras[2], ...opts.body, atualizado_por_nome: 'Eduardo', updated_at: '2026-11-13T12:00:00Z' }),
      'POST /api/atendimento/regras': (opts) => ({ ...R('nova', opts.body), atualizado_por_nome: 'Eduardo' }),
    })
    await t.montar()
    let html = await t.html()
    // Segurança e estilo: fora da tela (e do número).
    assert.doesNotMatch(html, /resposta vazia|Assinar como|data-regra="seg"|data-regra="est"/)
    assert.match(html, /data-contagem>4 regras ativas em 3 assuntos</)
    // Por assunto, na ordem do manual; o fora da lista e o "Sem assunto" no fim.
    assert.deepEqual([...html.matchAll(/data-assunto="([^"]+)"/g)].map((m) => m[1]), ['reembolso', 'rastreio', 'assunto_velho', '-'])
    const bloco = (chave) => {
      const m = new RegExp(`<div[^>]*data-assunto="${chave}"[\\s\\S]*?(?=<div[^>]*data-assunto=|<section)`).exec(html)
      assert.ok(m, chave)
      return m[0]
    }
    const rastreio = bloco('rastreio')
    assert.match(rastreio, /<h3[^>]*>Rastreio e atraso<\/h3>[\s\S]*data-ativas>1 ativa<[\s\S]*data-inativas>1 desativada<[\s\S]*O pedido já foi postado\./)
    // Dentro do assunto, a ordem em que o agente lê; o QUANDO/FAÇA com os chips.
    assert.deepEqual([...rastreio.matchAll(/data-regra="([^"]+)"/g)].map((m) => m[1]), ['ras', 'ras-ml'])
    const ras = /<li[^>]*data-regra="ras"[\s\S]*?<\/li>/.exec(rastreio)[0]
    assert.match(ras, /data-quando>[\s\S]*Pergunta do rastreio/)
    assert.match(ras, /title="\{rastreio\}: o agente preenche com o dado do pedido" data-lacuna>código de rastreio</)
    assert.match(ras, /data-lacuna>transportadora</)
    assert.match(ras, /data-agente-responde>agente responde</)
    assert.doesNotMatch(ras, /precisa_humano/, 'a marca vira o selo')
    assert.match(ras, />todas as lojas</)
    assert.match(ras, /prioridade 50/)
    assert.match(ras, /data-quem-editou>editada por Ana em 12\/11</)
    assert.match(ras, />#2</, 'o nº no manual inteiro (reembolso é o #1)')
    assert.match(ras, />desativar</)
    assert.match(ras, /aria-label="editar a regra"/)
    assert.match(ras, /aria-label="apagar a regra"/)
    const rasMl = /<li[^>]*data-regra="ras-ml"[\s\S]*?<\/li>/.exec(rastreio)[0]
    assert.match(rasMl, /class="[^"]*opacity-60/)
    assert.match(rasMl, />Mercado Livre · Pergunta</)
    assert.match(rasMl, />desativada</)
    assert.match(rasMl, />ativar</)
    assert.match(rasMl, /data-quem-editou>editada em 10\/11</)
    const reembolso = bloco('reembolso')
    assert.match(reembolso, /data-pessoa-decide>pessoa decide</)
    assert.match(reembolso, /data-lacuna>nº do pedido na plataforma</)
    assert.match(reembolso, /title="nova regra em Reembolso e estorno"/)
    assert.match(bloco('assunto_velho'), />assunto fora da lista</)
    assert.doesNotMatch(bloco('assunto_velho'), /title="nova regra em/)
    const sem = bloco('-')
    assert.match(sem, /<h3[^>]*>Sem assunto<\/h3>[\s\S]*>o agente não lê</)
    assert.match(sem, /edite e escolha o assunto para estas valerem/)
    // Os Fatos do agente continuam embaixo.
    assert.match(html, /Fatos do agente \(Claude\)/)

    // A busca (sem acento, todas as palavras) e o filtro da plataforma.
    t.estado.busca = 'ESTÓRNO pedido'
    html = await t.html()
    assert.deepEqual([...html.matchAll(/data-assunto="([^"]+)"/g)].map((m) => m[1]), ['reembolso'])
    assert.match(html, /data-ativas>1 ativa</)
    t.estado.busca = 'nada disso existe'
    html = await t.html()
    assert.match(html, /Nenhuma regra com esse filtro\./)
    t.estado.limparFiltros()
    t.estado.filtroPlataforma = 'shopee'
    html = await t.html()
    assert.doesNotMatch(html, /data-regra="ras-ml"/, 'a do ML não vale para a Shopee')
    assert.match(html, /data-regra="ras"/)
    t.estado.limparFiltros()

    // Desativar: o PATCH de sempre; quem mexeu por último acompanha.
    await t.estado.alternar(t.estado.regras.find((x) => x.id === 'ras'))
    assert.deepEqual(t.chamadas.find((c) => c.metodo === 'PATCH'), { url: '/api/atendimento/regras/ras', metodo: 'PATCH', body: { ativa: false } })

    // Nova regra no assunto: o formulário já com o assunto; salva como "por assunto".
    t.estado.abrirNova('reembolso')
    assert.equal(t.estado.nova.categoria, 'reembolso')
    html = await t.html()
    const nova = /<div[^>]*data-nova-regra>[\s\S]*?<\/div>\s*<\/div>\s*<\/div>/.exec(html)
    assert.ok(nova, 'o formulário da regra nova aparece')
    assert.match(html, /data-nova-regra>[\s\S]*Nova regra[\s\S]*aria-label="assunto da regra"/)
    assert.doesNotMatch(html, />Tipo</)
    t.estado.nova.quando = '  Cliente pede reembolso parcial '
    t.estado.nova.faca = 'Explique que o valor é definido pela plataforma.'
    await t.estado.criar()
    const post = t.chamadas.find((c) => c.metodo === 'POST')
    assert.deepEqual(post.body, { quando: 'Cliente pede reembolso parcial', faca: 'Explique que o valor é definido pela plataforma.', plataforma: null, canal: null, tipo: 'categoria', categoria: 'reembolso', prioridade: 100 })
    assert.equal(t.estado.novaAberta, false)
    // Sem assunto, a regra nova não sai.
    t.estado.abrirNova()
    t.estado.nova.quando = 'q'
    t.estado.nova.faca = 'f'
    const antes = t.chamadas.length
    await t.estado.criar()
    assert.equal(t.chamadas.length, antes, 'sem assunto, nada é criado')
    t.estado.fecharNova()

    // Editar a regra sem assunto: o formulário de edição, que deixa salvar sem assunto.
    t.estado.editar(t.estado.regras.find((x) => x.id === 'geral'))
    html = await t.html()
    const edicao = /<li[^>]*data-regra="geral"[\s\S]*?<\/li>/.exec(html)[0]
    assert.match(edicao, /<option value[^>]*>sem assunto \(o agente não lê\)<\/option>/)
    assert.doesNotMatch(edicao, /<button[^>]*disabled[^>]*>[^<]*<i data-icone="Check"/, 'salvar liberado na edição sem assunto')
  }

  // ── quem não edita só vê (sem ações, sem "nova regra")
  {
    const t = montar({ canEdit: false, canDelete: false }, respostasPadrao())
    await t.montar()
    const html = await t.html()
    assert.match(html, /data-regra="ras"/)
    assert.doesNotMatch(html, />desativar<|>ativar<|aria-label="editar a regra"|aria-label="apagar a regra"|nova regra|title="nova regra em/)
  }

  // ── o formulário sozinho: a regra nova pede o assunto
  {
    async function renderForm(modelo, props = {}) {
      const a = Vue.createSSRApp({ render: () => Vue.h(formMod, { modelValue: modelo, 'onUpdate:modelValue': () => {}, categorias: CATEGORIAS_API, salvando: false, conflito: null, ...props }) })
      a.config.warnHandler = () => {}
      a.component('Button', BOTAO)
      return semComentarios(await renderToString(a))
    }
    const base = { quando: 'q', faca: 'f', plataforma: '', canal: '', tipo: 'categoria', categoria: '', prioridade: 100 }
    const salvar = (html) => /<button[^>]*>(?:(?!<button)[\s\S])*?data-icone="Check"[\s\S]*?<\/button>/.exec(html)[0]
    let html = await renderForm(base)
    assert.match(html, /<option value disabled[^>]*>escolha o assunto…<\/option>/)
    assert.match(salvar(html), /disabled/, 'sem assunto, a regra nova não salva')
    html = await renderForm({ ...base, categoria: 'rastreio' })
    assert.doesNotMatch(salvar(html), /disabled/)
    html = await renderForm(base, { edicao: true })
    assert.doesNotMatch(salvar(html), /disabled/, 'na edição, a sem assunto salva')
    // O assunto que saiu da lista continua como opção (a edição não troca sem a pessoa ver).
    html = await renderForm({ ...base, categoria: 'assunto_velho' }, { edicao: true })
    assert.match(html, /<option value="assunto_velho"[^>]*>assunto_velho \(fora da lista\)<\/option>/)
    assert.doesNotMatch(html, />Tipo</)
  }
  console.log('ok — atendimento-manual')
}

principal().catch((e) => {
  console.error(e)
  process.exit(1)
})
