// node tests/atendimento-banco.cjs — a aba "Banco de respostas" do Atendimento (13/11/2026):
// `components/AtendimentoBanco.vue`, só para admin. O que a tela tem de garantir:
//  - as ROTAS que ela chama existem no backend (`routers/atendimento_banco.py`, registrado no
//    `main.py`) e a correção tem o mesmo teto (4.000) do schema; sem a parte da régua (a % por
//    assunto foi para a aba Métricas);
//  - as funções puras: o dia (anterior/seguinte), os filtros, as contagens depois do voto, o
//    aviso do banco depois de salvar, o 👎 só com a correção escrita e diferente do que saiu, a
//    fonte da resposta do banco;
//  - a tela de verdade (setup + SSR, API falsa): a revisão do dia abre sozinha (hoje), com o selo
//    "sensível", a pergunta e o que a IA mandou; 👍 grava `ok`; 👎 abre "como seria o certo" com
//    o texto da IA e grava `erro` + a correção; o aviso diz se entrou no banco; quem não mexe só
//    vê; o "mudar" reabre; o dia anterior pede o dia; o Banco abre na primeira vez, com os
//    filtros (assunto, loja, texto com espera) e o "mostrar mais";
//  - a página: a aba (Library) só para admin, montada com quem revisa = admin que mexe.
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

const tela = sfc('../components/AtendimentoBanco.vue')
const B = (() => {
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(tela.descriptor.script.content))(requerer, mod, mod.exports)
  return mod.exports
})()

// ------------------------------------------------ o contrato com o backend
{
  const rotasPy = api('routers/atendimento_banco.py')
  const prefixo = /APIRouter\(\s*prefix="([^"]+)"/.exec(rotasPy)[1]
  assert.equal(prefixo, '/api/atendimento/banco')
  const doBackend = new Set([...rotasPy.matchAll(/@router\.(get|post)\("([^"]+)"/g)].map((m) => `${m[1].toUpperCase()} ${prefixo}${m[2].replace(/\{[^}]+\}/g, 'X')}`))
  assert.deepEqual([...doBackend].sort(), ['GET /api/atendimento/banco/respostas', 'GET /api/atendimento/banco/revisao', 'POST /api/atendimento/banco/revisao/X'])
  assert.ok(doBackend.has(`GET ${B.ROTAS.revisao}`) && doBackend.has(`GET ${B.ROTAS.respostas}`))
  assert.ok(doBackend.has(`POST ${B.ROTAS.revisar('X')}`))
  assert.equal(B.ROTAS.revisar('a/b'), '/api/atendimento/banco/revisao/a%2Fb', 'o id vai escapado')
  assert.match(api('main.py'), /app\.include_router\(atendimento_banco_router\.router\)/)
  // Toda chamada da tela usa as ROTAS.
  const setup = tela.descriptor.scriptSetup.content
  const chamadas = [...setup.matchAll(/\bapi<[^>]+>\(\s*([^,)]+)/g)].map((m) => m[1].trim())
  assert.equal(chamadas.length, 3)
  for (const c of chamadas) assert.match(c, /^ROTAS\./, c)
  // O teto da correção = o do schema; as fontes = as do banco.
  assert.equal(Number(/^MAX_CHARS_CORRECAO = (\d+)/m.exec(api('schemas/atendimento_banco.py'))[1]), B.MAX_CORRECAO)
  const banco = api('services/atendimento/banco_respostas.py')
  const fontes = [...banco.matchAll(/^FONTE_[A-Z]+ = "([a-z_]+)"/gm)].map((m) => m[1])
  assert.deepEqual(Object.keys(B.FONTES).sort(), fontes.sort())
  // A régua saiu desta aba (vai para Métricas, por equipe): nem rota, nem parte da tela.
  assert.doesNotMatch(rotasPy, /"\/regua"/)
  assert.doesNotMatch(tela.fonte, /\/regua|R[ée]gua do assunto|parte === 'regua'/)
}

// ------------------------------------------------ as funções puras
{
  assert.equal(B.somarDias('2026-11-01', -1), '2026-10-31')
  assert.equal(B.somarDias('2026-12-31', 1), '2027-01-01')
  assert.equal(B.somarDias('2026-03-01', -1), '2026-02-28')
  assert.equal(B.somarDias('', 1), '')
  assert.equal(B.fmtDia('2026-11-13'), '13/11/2026')
  const itens = [
    { rascunho_id: 'a', sensivel: true, avaliacao: null },
    { rascunho_id: 'b', sensivel: false, avaliacao: { nota: 'ok' } },
    { rascunho_id: 'c', sensivel: false, avaliacao: { nota: 'erro' } },
  ]
  const ids = (f) => B.filtrarRevisao(itens, f).map((i) => i.rascunho_id)
  assert.deepEqual(ids('todos'), ['a', 'b', 'c'])
  assert.deepEqual(ids('faltam'), ['a'])
  assert.deepEqual(ids('sensiveis'), ['a'])
  assert.deepEqual(ids('erros'), ['c'])
  assert.deepEqual(B.FILTROS_REVISAO.map((f) => f.value), ['todos', 'faltam', 'sensiveis', 'erros'])
  const c0 = { total: 5, sensiveis: 1, revisados: 1, erros: 0, mostrados: 5 }
  assert.deepEqual(B.contagensDepois(c0, null, { nota: 'erro' }), { ...c0, revisados: 2, erros: 1 })
  assert.deepEqual(B.contagensDepois(c0, { nota: 'erro' }, { nota: 'ok' }), { ...c0, revisados: 1, erros: -1 })
  assert.deepEqual(B.contagensDepois({ ...c0, erros: 1 }, { nota: 'erro' }, { nota: 'ok' }), c0)
  assert.equal(B.avisoDoBanco('erro', { entrou: true }).tipo, 'success')
  assert.match(B.avisoDoBanco('erro', { entrou: true }).titulo, /entrou no banco/)
  const fora = B.avisoDoBanco('erro', { entrou: false, motivo: 'curta', frase: 'a correção é curta demais.' })
  assert.deepEqual([fora.tipo, fora.linhas], ['warning', ['a correção é curta demais.']])
  assert.deepEqual(B.avisoDoBanco('ok', { retirada: true }).linhas, ['A correção que estava no banco saiu.'])
  assert.deepEqual(B.avisoDoBanco('ok', { retirada: false }).linhas, [])
  assert.equal(B.correcaoPronta('', 'x'), false)
  assert.equal(B.correcaoPronta('   ', 'x'), false)
  assert.equal(B.correcaoPronta('Tem NFC.', ' Tem   NFC. '), false, 'igual ao que saiu (só espaço) não é correção')
  assert.equal(B.correcaoPronta('Não tem NFC.', 'Tem NFC.'), true)
  assert.equal(B.correcaoPronta('a'.repeat(B.MAX_CORRECAO + 1), 'x'), false)
  assert.equal(B.fonteLabel({ fonte: 'equipe_ajustou', da_revisao: true }), 'correção da revisão diária')
  assert.equal(B.fonteLabel({ fonte: 'equipe_fora', da_revisao: false }), 'equipe, por fora (Duoke/Seller Center)')
}

// ------------------------------------------------ a tela de verdade (setup + SSR, API falsa)
const script = compileScript(tela.descriptor, { id: 'banco', inlineTemplate: false })
const tpl = compileTemplate({ source: tela.descriptor.template.content, filename: tela.filename, id: 'banco', compilerOptions: { isTS: true, bindingMetadata: script.bindings } })
assert.deepEqual(tpl.errors, [], 'o template compila')

const BOTAO = { props: ['disabled', 'size', 'variant'], setup(p, { slots, attrs }) { return () => Vue.h('button', { ...attrs, disabled: p.disabled || undefined }, slots.default?.()) } }
const STAT = { props: ['label', 'value', 'hint', 'icon', 'tone', 'compact'], render() { return Vue.h('div', { 'data-stat': this.label }, `${this.label}: ${this.value}`) } }
const PLATAFORMA = { props: ['codigo'], render() { return Vue.h('span', { 'data-plataforma': this.codigo }) } }

function montar(props, respostas) {
  const chamadas = []
  const avisos = []
  const montados = []
  const responder = async (url, opts = {}) => {
    chamadas.push({ url, metodo: opts.method || 'GET', query: opts.query || null, body: opts.body ?? null })
    const valor = respostas[`${opts.method || 'GET'} ${url}`]
    if (valor === undefined) throw new Error(`rota inesperada: ${opts.method || 'GET'} ${url}`)
    const v = typeof valor === 'function' ? valor(opts) : valor
    if (v instanceof Error) throw v
    return structuredClone(v)
  }
  const g = {
    ref: Vue.ref, computed: Vue.computed, reactive: Vue.reactive, watch: Vue.watch, nextTick: Vue.nextTick,
    onMounted: (fn) => montados.push(fn), onBeforeUnmount: () => {},
    useApi: () => ({ api: responder }),
    useToasts: () => ({
      success: (t, l) => avisos.push(['ok', t, l]), warning: (t, l) => avisos.push(['aviso', t, l]),
      error: (t, l) => avisos.push(['erro', t, l]), info: (t, l) => avisos.push(['info', t, l]),
    }),
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
    a.component('StatCard', STAT)
    a.component('AtendimentoPlataforma', PLATAFORMA)
    return semComentarios(await renderToString(a))
  }
  return { estado, chamadas, avisos, html, async montar() { for (const fn of montados) await fn(); await esperar() } }
}

const ITEM_SENSIVEL = {
  rascunho_id: 'r-1', conversa_id: 'c-1', integration_id: 'i-1', loja: 'Kia', plataforma: 'shopee', canal: 'chat',
  assunto: 'reembolso', assunto_nome: 'Reembolso', sensivel: true,
  pergunta: 'oi, boa tarde\nquero o reembolso, como faço?', resposta: 'O reembolso sai pela Shopee depois da devolução.',
  com_anexo: false, enviada_em: '2026-11-13T18:00:00Z', avaliacao: null,
}
const ITEM_COMUM = {
  ...ITEM_SENSIVEL, rascunho_id: 'r-2', conversa_id: 'c-2', assunto: 'espec_celular', assunto_nome: 'Ficha técnica do celular', sensivel: false,
  pergunta: 'tem NFC?', resposta: 'Tem sim, NFC para pagamento.', enviada_em: '2026-11-13T19:00:00Z',
  avaliacao: { nota: 'erro', correcao: 'Não tem NFC.', por: 'Eduardo', em: '2026-11-13T20:00:00Z', no_banco: true },
}
const REVISAO = {
  dia: '2026-11-13', hoje: '2026-11-13', amostra: 20,
  contagens: { total: 7, sensiveis: 1, revisados: 1, erros: 1, mostrados: 2 },
  itens: [ITEM_SENSIVEL, ITEM_COMUM],
}
const RESPOSTAS = {
  total: 60,
  itens: [
    { id: 1, tipo: 'espec_celular', tipo_nome: 'Ficha técnica do celular', integration_id: 'i-1', loja: 'Kia', plataforma: 'shopee', canal: 'chat', modelos: ['F105'], pergunta: 'Esse celular tem NFC?', resposta: 'O F105 não tem NFC.', fonte: 'equipe_fora', da_revisao: false, respondido_em: '2026-11-12T12:00:00Z', conversa_id: null },
    { id: 2, tipo: 'espec_celular', tipo_nome: 'Ficha técnica do celular', integration_id: 'i-1', loja: 'Kia', plataforma: 'shopee', canal: 'chat', modelos: [], pergunta: 'tem NFC?', resposta: 'Não tem NFC.', fonte: 'equipe_ajustou', da_revisao: true, respondido_em: '2026-11-13T20:00:00Z', conversa_id: 'c-2' },
  ],
  tipos: [{ tipo: 'espec_celular', nome: 'Ficha técnica do celular', n: 2 }],
  lojas: [{ integration_id: 'i-1', plataforma: 'shopee', loja: 'Kia', n: 2 }],
}

async function principal() {
  // ── a revisão do dia: abre sozinha (hoje), com o selo, a pergunta e o que saiu
  {
    const t = montar({ podeRevisar: true }, {
      [`GET ${B.ROTAS.revisao}`]: (opts) => (opts.query?.dia === '2026-11-12' ? { ...REVISAO, dia: '2026-11-12', itens: [] } : REVISAO),
      [`POST ${B.ROTAS.revisar('r-1')}`]: (opts) => ({
        item: { ...ITEM_SENSIVEL, avaliacao: { nota: opts.body.nota, correcao: opts.body.correcao ?? null, por: 'Eduardo', em: '2026-11-13T21:00:00Z', no_banco: opts.body.nota === 'erro' ? false : null } },
        banco: opts.body.nota === 'erro' ? { entrou: false, motivo: 'dado_pessoal', frase: 'tem cara de dado pessoal (nome, contato, endereço, documento).', retirada: false } : { entrou: false, motivo: null, frase: null, retirada: false },
      }),
    })
    await t.montar()
    assert.deepEqual(t.chamadas.map((c) => [c.metodo, c.url, c.query]), [['GET', B.ROTAS.revisao, {}]], 'sem dia: o servidor decide (hoje em São Paulo)')
    let html = await t.html()
    assert.match(html, /data-parte="revisao"/)
    assert.match(html, /Revisão do dia[\s\S]*Banco/)
    assert.match(html, /data-stat="Mandou sozinha"[^>]*>Mandou sozinha: 7</)
    assert.match(html, /data-stat="Sensíveis"[^>]*>Sensíveis: 1</)
    assert.match(html, /data-stat="Errou"[^>]*>Errou: 1</)
    assert.match(html, /aria-label="filtrar a revisão"[\s\S]*>Todos<[\s\S]*>Falta revisar<[\s\S]*>Sensíveis<[\s\S]*>Errou</)
    assert.match(html, /<input type="date"[^>]*value="2026-11-13"[^>]*max="2026-11-13"/)
    const sensivel = /<li[^>]*data-revisao="r-1"[\s\S]*?<\/li>/.exec(html)[0]
    assert.match(sensivel, /data-sensivel[^>]*>sensível</)
    assert.match(sensivel, />Reembolso</)
    assert.match(sensivel, /oi, boa tarde\nquero o reembolso, como faço\?/)
    assert.match(sensivel, /O reembolso sai pela Shopee depois da devolução\./)
    assert.match(sensivel, /href="\/atendimento\?conversa=c-1" target="_blank"/)
    assert.match(sensivel, /data-votar[\s\S]*> certo\s*<[\s\S]*> errou\s*</)
    const comum = /<li[^>]*data-revisao="r-2"[\s\S]*?<\/li>/.exec(html)[0]
    assert.doesNotMatch(comum, /data-sensivel/)
    assert.match(comum, /data-revisada[\s\S]*errou[\s\S]*no banco[\s\S]*Eduardo[\s\S]*mudar[\s\S]*O certo seria <\/span>Não tem NFC\./)
    assert.doesNotMatch(comum, /data-votar/)

    // 👎: abre "como seria o certo" com o texto da IA; igual ao que saiu não salva.
    t.estado.abrirCorrecao(t.estado.revisao.itens[0])
    assert.equal(t.estado.corrigindo['r-1'], ITEM_SENSIVEL.resposta)
    html = await t.html()
    assert.match(/<li[^>]*data-revisao="r-1"[\s\S]*?<\/li>/.exec(html)[0], /data-correcao[\s\S]*<textarea[^>]*maxlength="4000"[\s\S]*salvar correção/)
    await t.estado.revisar(t.estado.revisao.itens[0], 'erro')
    assert.equal(t.chamadas.length, 1, 'sem mudar o texto, nada sai')
    t.estado.corrigindo['r-1'] = '  O reembolso é pedido no app, em Minhas Compras.  '
    await t.estado.revisar(t.estado.revisao.itens[0], 'erro')
    assert.deepEqual(t.chamadas.at(-1), { url: B.ROTAS.revisar('r-1'), metodo: 'POST', query: null, body: { nota: 'erro', correcao: 'O reembolso é pedido no app, em Minhas Compras.' } })
    assert.deepEqual(t.avisos.at(-1), ['aviso', 'Correção salva, mas não entrou no banco', ['tem cara de dado pessoal (nome, contato, endereço, documento).']])
    assert.equal(t.estado.revisao.itens[0].avaliacao.nota, 'erro')
    assert.equal(t.estado.corrigindo['r-1'], undefined, 'a caixa da correção fecha')
    assert.deepEqual(t.estado.revisao.contagens, { ...REVISAO.contagens, revisados: 2, erros: 2 })
    html = await t.html()
    assert.match(/<li[^>]*data-revisao="r-1"[\s\S]*?<\/li>/.exec(html)[0], /data-revisada[\s\S]*errou[\s\S]*fora do banco/)

    // "mudar" reabre; 👍 grava `ok` (sem correção) e as contagens acompanham.
    t.estado.mudar(t.estado.revisao.itens[0])
    html = await t.html()
    assert.match(/<li[^>]*data-revisao="r-1"[\s\S]*?<\/li>/.exec(html)[0], /data-votar[\s\S]*deixar como estava/)
    await t.estado.revisar(t.estado.revisao.itens[0], 'ok')
    assert.deepEqual(t.chamadas.at(-1).body, { nota: 'ok' })
    assert.deepEqual(t.avisos.at(-1), ['ok', 'Marcado como certo', []])
    assert.deepEqual(t.estado.revisao.contagens, { ...REVISAO.contagens, revisados: 2, erros: 1 })
    assert.equal(t.estado.mudando['r-1'], undefined)

    // Os filtros.
    t.estado.filtro = 'faltam'
    assert.deepEqual(t.estado.itens.map((i) => i.rascunho_id), [])
    t.estado.filtro = 'erros'
    assert.deepEqual(t.estado.itens.map((i) => i.rascunho_id), ['r-2'])
    html = await t.html()
    assert.doesNotMatch(html, /data-revisao="r-1"/)
    t.estado.filtro = 'todos'

    // O dia anterior pede o dia; o seguinte de hoje não existe.
    assert.equal(t.estado.ehHoje, true)
    t.estado.irPara(B.somarDias(t.estado.diaMostrado, -1))
    await Vue.nextTick()
    await esperar()
    assert.deepEqual(t.chamadas.at(-1).query, { dia: '2026-11-12' })
    assert.equal(t.estado.diaMostrado, '2026-11-12')
    html = await t.html()
    assert.match(html, /A IA não mandou nada sozinha em 12\/11\/2026\./)
    const antes = t.chamadas.length
    t.estado.irPara('2026-11-14')
    await Vue.nextTick()
    await esperar()
    assert.equal(t.chamadas.length, antes, 'o futuro não existe')
    t.estado.irPara(REVISAO.hoje)
    await Vue.nextTick()
    await esperar()
    assert.deepEqual(t.chamadas.at(-1).query, {}, 'hoje volta a ser o padrão do servidor')
  }

  // ── quem não mexe na caixa só vê
  {
    const t = montar({ podeRevisar: false }, { [`GET ${B.ROTAS.revisao}`]: REVISAO })
    await t.montar()
    const html = await t.html()
    assert.match(html, /aqui você só vê/)
    assert.doesNotMatch(html, /data-votar|> mudar\s*</)
    assert.match(html, /Ainda não revisado\./)
    await t.estado.revisar(t.estado.revisao.itens[0], 'ok')
    assert.equal(t.chamadas.length, 1, 'nada sai')
  }

  // ── o Banco: abre na primeira vez, com os filtros e o "mostrar mais"
  {
    const t = montar({ podeRevisar: true }, { [`GET ${B.ROTAS.revisao}`]: REVISAO, [`GET ${B.ROTAS.respostas}`]: RESPOSTAS })
    await t.montar()
    assert.equal(t.chamadas.filter((c) => c.url === B.ROTAS.respostas).length, 0, 'o banco só abre quando pedido')
    t.estado.parte = 'banco'
    await Vue.nextTick()
    await esperar()
    const doBanco = () => t.chamadas.filter((c) => c.url === B.ROTAS.respostas)
    assert.deepEqual(doBanco().map((c) => c.query), [{ limite: 50 }])
    let html = await t.html()
    assert.match(html, /data-parte="banco"/)
    assert.match(html, /data-total[^>]*>\s*60 respostas — mostrando 2/)
    assert.match(html, /<option value="espec_celular">Ficha técnica do celular \(2\)<\/option>/)
    assert.match(html, /<option value="i-1">Kia · shopee \(2\)<\/option>/)
    const revisada = /<li[^>]*data-resposta="2"[\s\S]*?<\/li>/.exec(html)[0]
    assert.match(revisada, /correção da revisão diária/)
    assert.match(revisada, /href="\/atendimento\?conversa=c-2"/)
    const fora = /<li[^>]*data-resposta="1"[\s\S]*?<\/li>/.exec(html)[0]
    assert.match(fora, /equipe, por fora \(Duoke\/Seller Center\)/)
    assert.match(fora, />F105</)
    assert.doesNotMatch(fora, /conversa=/)
    // O assunto e a loja recarregam na hora; o texto espera parar de digitar.
    t.estado.busca.tipo = 'espec_celular'
    await Vue.nextTick()
    await esperar()
    assert.deepEqual(doBanco().at(-1).query, { limite: 50, tipo: 'espec_celular' })
    t.estado.busca.integration_id = 'i-1'
    await Vue.nextTick()
    await esperar()
    assert.deepEqual(doBanco().at(-1).query, { limite: 50, tipo: 'espec_celular', integration_id: 'i-1' })
    const n = doBanco().length
    t.estado.busca.q = ' nfc '
    await Vue.nextTick()
    await esperar(50)
    assert.equal(doBanco().length, n, 'ainda digitando')
    await esperar(450)
    assert.deepEqual(doBanco().at(-1).query, { limite: 50, q: 'nfc', tipo: 'espec_celular', integration_id: 'i-1' })
    assert.equal(t.estado.podeMostrarMais, true)
    t.estado.mostrarMais()
    await esperar()
    assert.equal(doBanco().at(-1).query.limite, 100)
    html = await t.html()
    assert.match(html, /mostrar mais/)
  }

  // ------------------------------------------------ a página: a aba só para admin
  {
    const pagina = web('pages/atendimento.vue')
    const { descriptor } = parse(pagina, { filename: 'atendimento.vue' })
    const setup = descriptor.scriptSetup.content
    const tplPagina = descriptor.template.content
    assert.match(setup, /import \{[^}]*\bLibrary\b[^}]*\} from 'lucide-vue-next'/)
    assert.match(setup, /type Aba = [^\n]*\| 'banco'\n/)
    const abas = [...(setup.match(/const ABAS[\s\S]*?\n\]/) || [''])[0].matchAll(/value: '([a-z]+)', label: '([^']+)', icon: (\w+)/g)].map((m) => [m[1], m[2], m[3]])
    assert.deepEqual(abas.at(-1), ['banco', 'Banco de respostas', 'Library'], 'a última aba')
    assert.match(setup, /function abasDe\(veEmail: boolean\) \{\n {2}return ABAS\.filter\(\(a\) => \(a\.value !== 'mail' \|\| veEmail\) && \(a\.value !== 'banco' \|\| ehAdmin\.value\)\)\n\}/)
    assert.match(setup, /banco: 'Banco',\n\}/, 'o nome curto na barra da tela cheia')
    assert.match(tplPagina, /<LazyAtendimentoBanco v-if="aba === 'banco' && ehAdmin" :pode-revisar="isAdmin" \/>/)
    assert.match(setup, /const isAdmin = computed\(\(\) => mexe\.value && ehAdmin\.value\)/, 'revisa = admin que mexe (a API pede o mesmo)')
  }
  console.log('ok — atendimento-banco')
}

principal().catch((e) => {
  console.error(e)
  process.exit(1)
})
