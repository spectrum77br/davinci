// node tests/usuario-lojas-atendimento.cjs — Usuários › "Lojas no atendimento"
// (30/10/2026, as lojas do /atendimento separadas por pessoa). Sem rede:
//  - o contrato com o backend (GET/PUT /api/users/{id}/lojas-atendimento,
//    só admin; os códigos de erro com texto);
//  - as funções puras (a loja marcada só com todas as chaves, marcar/desmarcar
//    a loja inteira, os grupos por plataforma e o filtro, o que vai no PUT);
//  - o componente de verdade (setup + Vue SSR, API falsa): carregar ao abrir,
//    marcar, salvar, o erro da API, o aviso de admin.
// O harness (montar/sfc/requerer) é o mesmo de atendimento-mail-leitores.cjs.
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
  const tpl = compileTemplate({ source: descriptor.template.content, filename, id: path.basename(rel), compilerOptions: { isTS: true } })
  assert.deepEqual(tpl.errors, [], `${rel}: template compila`)
  return { descriptor, fonte, filename }
}
const cache = new Map()
const icones = new Proxy({}, { get: (_, nome) => ({ name: String(nome), render: () => Vue.h('i', { 'data-icone': String(nome) }) }) })
function requerer(nome) {
  if (nome === 'lucide-vue-next') return icones
  const m = /^~\/components\/(.+\.vue)$/.exec(nome)
  if (!m) return require(nome)
  if (!cache.has(m[1])) cache.set(m[1], exportsDe(sfc(`../components/${m[1]}`).descriptor))
  return cache.get(m[1])
}
function exportsDe(descriptor) {
  const mod = { exports: {} }
  if (!descriptor.script) return mod.exports
  new Function('require', 'module', 'exports', transpile(descriptor.script.content))(requerer, mod, mod.exports)
  return mod.exports
}
function trecho(src, ini, fim) {
  const a = src.indexOf(ini)
  const b = src.indexOf(fim, a + 1)
  assert.ok(a >= 0 && b > a, `trecho ${ini} … ${fim}`)
  return transpile(src.slice(a, b))
}
const api = (rel) => fs.readFileSync(path.resolve(__dirname, '../../api/app', rel), 'utf8')
const web = (rel) => fs.readFileSync(path.resolve(__dirname, '..', rel), 'utf8')
const semComentarios = (html) => html.replace(/<!--[\s\S]*?-->/g, '')
const esperar = () => new Promise((r) => setTimeout(r, 0))

// ------------------------------------------------ montar um componente de verdade
// O setup de verdade roda fora de um app (como no teste da Central: os
// `watch` ficam vivos — no SSR eles não reagem); o defineModel vira um ref
// local. O HTML sai de um app SSR que usa o MESMO estado.
function montar(rel, props, { respostas = {}, confirmar = true, filhos = {} } = {}) {
  const { descriptor, filename } = sfc(rel)
  const script = compileScript(descriptor, { id: path.basename(rel), inlineTemplate: false })
  const tpl = compileTemplate({ source: descriptor.template.content, filename, id: path.basename(rel), compilerOptions: { isTS: true, bindingMetadata: script.bindings } })
  const chamadas = []
  const perguntas = []
  const avisos = []
  const emitidos = []
  const montados = []
  const responder = async (url, opts = {}) => {
    chamadas.push({ url, metodo: opts.method || 'GET', body: opts.body })
    for (const [chave, valor] of Object.entries(respostas)) {
      if (url === chave || url.startsWith(`${chave}?`)) {
        const v = typeof valor === 'function' ? valor(url, opts) : valor
        if (v instanceof Error) throw v
        return structuredClone(v)
      }
    }
    if (/\/(lista|pastas)(?:\?|$)/.test(url)) throw Object.assign(new Error('Older API route missing'), { status: 404 })
    throw new Error(`rota inesperada: ${url}`)
  }
  const pergunta = (t) => { perguntas.push(t); return typeof confirmar === 'function' ? confirmar(t) : confirmar }
  const g = {
    ref: Vue.ref, computed: Vue.computed, reactive: Vue.reactive, watch: Vue.watch, nextTick: Vue.nextTick,
    onMounted: (fn) => montados.push(fn), onBeforeUnmount: () => {},
    useApi: () => ({ url: (v) => v, api: responder }),
    useToasts: () => ({ success: (t) => avisos.push(['ok', t]), error: (t) => avisos.push(['erro', t]), info: (t) => avisos.push(['info', t]), warning: (t) => avisos.push(['aviso', t]) }),
    confirm: pergunta,
    window: { addEventListener: () => {}, removeEventListener: () => {}, confirm: pergunta },
    crypto: { randomUUID: () => '00000000-0000-4000-8000-000000000000' },
    document: { hidden: false, addEventListener: () => {}, removeEventListener: () => {} }, setInterval: () => 1, clearInterval: () => {}, setTimeout: () => 1, clearTimeout: () => {},
  }
  const nomes = Object.keys(g)
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', ...nomes, transpile(script.content))(requerer, mod, mod.exports, ...nomes.map((n) => g[n]))
  const tmod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(tpl.code))(requerer, tmod, tmod.exports)
  const comp = mod.exports.default
  const reativo = Vue.reactive({ ...props })
  // "useModel() called without active instance": esperado aqui (vira ref local).
  const aviso = console.warn
  console.warn = () => {}
  let bruto
  try {
    bruto = comp.setup(reativo, { emit: (...a) => emitidos.push(a), expose: () => {}, attrs: {}, slots: {} })
  } finally {
    console.warn = aviso
  }
  const estado = Vue.proxyRefs(bruto)
  const filho = { ...comp, render: tmod.exports.render, setup: () => bruto }
  async function html() {
    const a = Vue.createSSRApp({ render: () => Vue.h(filho, reativo) })
    a.config.warnHandler = () => {}
    a.component('Button', { props: ['disabled'], setup: (p, { slots }) => () => Vue.h('button', { disabled: p.disabled }, slots.default?.()) })
    for (const [n, c] of Object.entries(filhos)) a.component(n, c)
    return semComentarios(await renderToString(a))
  }
  return {
    html, estado, chamadas, perguntas, avisos, emitidos, montados, reativo,
    async montar() { for (const fn of montados) await fn(); await esperar() },
  }
}


const U = requerer('~/components/UsuarioLojasAtendimento.vue')

const LOJAS = [
  { chave: 'f:00000000-0000-4000-8000-0000000000a1', plataforma: 'shopee', nome: 'atlas', nome_no_chat: 'Jlas', chat: true, chaves: ['f:00000000-0000-4000-8000-0000000000a1', 'i:00000000-0000-4000-8000-0000000000b1'] },
  { chave: 'f:00000000-0000-4000-8000-0000000000a2', plataforma: 'shopee', nome: 'luno', nome_no_chat: null, chat: false, chaves: ['f:00000000-0000-4000-8000-0000000000a2'] },
  { chave: 'f:00000000-0000-4000-8000-0000000000a3', plataforma: 'temu', nome: 'barbosa', nome_no_chat: 'Barbosa', chat: true, chaves: ['f:00000000-0000-4000-8000-0000000000a3', 'c:00000000-0000-4000-8000-0000000000c3'] },
]
const VELHA = 'i:00000000-0000-4000-8000-0000000000ff' // loja arquivada, fora da lista

async function principal() {
  // ------------------------------------------------ contrato com o backend
  {
    const rotas = api('routers/atendimento_lojas_pessoa.py')
    assert.match(rotas, /prefix="\/api\/users"/)
    assert.match(rotas, /@router\.get\("\/\{user_id\}\/lojas-atendimento"\)/)
    assert.match(rotas, /@router\.put\("\/\{user_id\}\/lojas-atendimento"\)/)
    assert.match(rotas, /Admin = Annotated\[User, Depends\(require_admin\)\]/)
    for (const code of ['usuario_nao_encontrado', 'chave_invalida', 'loja_desconhecida']) {
      assert.match(rotas, new RegExp(`"${code}"`), code)
      assert.ok(U.ERROS_LOJAS[code], `texto para ${code}`)
    }
    assert.match(api('main.py'), /app\.include_router\(atendimento_lojas_pessoa_router\.router\)/)
    // A página de Usuários mostra a seção (só admin abre a página).
    const pagina = web('pages/users/[id].vue')
    assert.match(pagina, /definePageMeta\(\{ middleware: \['admin'\] \}\)/)
    assert.match(pagina, /<UsuarioLojasAtendimento :user-id="userId" :is-admin-user="isAdminUser" \/>/)
    // A Caixa de quem não é admin e não tem loja diz o porquê (e a aba E-mail é só de admin).
    const caixa = web('pages/atendimento.vue')
    assert.match(caixa, /v-if="naCaixa && !ehAdmin && resumo && !resumo\.lojas\.length"[\s\S]*?data-sem-lojas[\s\S]*?Usuários › Lojas no atendimento/)
    assert.match(caixa, /const veAbaEmail = computed\(\(\) => ehAdmin\.value\)/)
    // A Amazon só de e-mail ("amazon:vr", 30/10/2026) é loja: a linha leva as caixas de e-mail.
    const L = requerer('~/components/AtendimentoLojas.vue')
    assert.equal(L.ehAmazonSoEmail({ integration_id: null, externo_ref: 'amazon:vr' }), true)
    assert.equal(L.ehAmazonSoEmail({ integration_id: null, externo_ref: 'site:uranyx' }), false)
    assert.equal(L.linhaTemCaixas({ externo_ref: 'amazon:vr', integration_id: null }), true)
    assert.equal(L.linhaTemCaixas({ externo_ref: 'site:uranyx', integration_id: null }), false)
    assert.equal(L.linhaTemCaixas({ integration_id: 'i1' }), true)
    // A Netshoes (só e-mail da loja na barra) tem o ícone dela, não a bolinha cinza.
    const icone = web('components/AtendimentoIconePlataforma.vue')
    assert.match(icone, /netshoes: 'Netshoes'/)
    assert.match(icone, /<template v-else-if="cod === 'netshoes'">\s*<rect[^>]*fill="#5C2D91"/)
    // E no menu Plataforma, pela loja dela nos e-mails (sem chat no /resumo).
    const F = requerer('~/components/AtendimentoFiltroPlataforma.vue')
    const resumo = { plataformas: [], lojas: [{ plataforma: 'shopee' }], canais: [] }
    assert.deepEqual(F.chipsDoResumo(resumo).map((c) => c.valor), ['shopee'])
    assert.deepEqual(F.chipsDoResumo(resumo, '', 'aguardando', ['netshoes']).map((c) => c.valor), ['shopee', 'netshoes'])
    assert.match(caixa, /:extras="plataformasSoEmail"/)
  }

  // ------------------------------------------------ funções puras
  {
    const sel = new Set(['f:00000000-0000-4000-8000-0000000000a1'])
    assert.equal(U.marcada(LOJAS[0], sel), false, 'só a ficha, sem o chat: não está marcada')
    const s1 = U.alternarLoja(sel, LOJAS[0], true)
    assert.equal(U.marcada(LOJAS[0], s1), true)
    assert.equal(sel.size, 1, 'não muda a seleção de entrada')
    const s2 = U.alternarLoja(s1, LOJAS[0], false)
    assert.equal(s2.size, 0, 'desmarcar tira as chaves que andam juntas')
    assert.equal(U.marcada({ ...LOJAS[1], chaves: [] }, new Set()), false)

    const g = U.porPlataforma(LOJAS)
    assert.deepEqual(g.map((x) => x.plataforma), ['shopee', 'temu'])
    assert.deepEqual(U.porPlataforma(LOJAS, 'jlas').map((x) => x.lojas.map((l) => l.nome)), [['atlas']], 'acha pelo nome do chat')
    assert.deepEqual(U.porPlataforma(LOJAS, 'temu').map((x) => x.plataforma), ['temu'], 'acha pela plataforma')
    assert.equal(U.nomeDaPlataforma('ml'), 'Mercado Livre')
    assert.equal(U.nomeDaPlataforma('kwai'), 'Kwai')

    const marcadas = U.alternarLoja(new Set([VELHA]), LOJAS[2], true)
    assert.deepEqual(
      U.chavesParaSalvar(LOJAS, marcadas, [VELHA]),
      ['c:00000000-0000-4000-8000-0000000000c3', 'f:00000000-0000-4000-8000-0000000000a3', VELHA].sort(),
      'as da loja marcada + a gravada fora da lista',
    )
    // A loja meio marcada (só a ficha) não vai pela metade.
    assert.deepEqual(U.chavesParaSalvar(LOJAS, new Set(['f:00000000-0000-4000-8000-0000000000a1']), []), [])
    assert.equal(U.mesmasChaves(['a', 'b'], ['b', 'a']), true)
    assert.equal(U.mesmasChaves(['a'], ['a', 'b']), false)
    assert.deepEqual(U.outrosDaLoja(LOJAS[0], { 'i:00000000-0000-4000-8000-0000000000b1': ['marrocos', 'israel'] }), ['israel', 'marrocos'])
  }

  // ------------------------------------------------ o componente de verdade
  {
    const rota = '/api/users/u1/lojas-atendimento'
    let gravado = null
    const resposta = (chaves) => ({ user_id: 'u1', admin: false, chaves, lojas: LOJAS, outros: { 'f:00000000-0000-4000-8000-0000000000a2': ['londres'] } })
    const m = montar('../components/UsuarioLojasAtendimento.vue', { userId: 'u1', isAdminUser: false }, {
      respostas: {
        [rota]: (_url, opts) => {
          if (opts.method === 'PUT') { gravado = opts.body; return resposta(opts.body.chaves) }
          return resposta([VELHA, ...LOJAS[0].chaves])
        },
      },
    })
    // Fechado: nada carregado, o botão de carregar à mostra.
    let html = await m.html()
    assert.match(html, /Lojas no atendimento/)
    assert.match(html, /data-carregar-lojas/)
    assert.doesNotMatch(html, /data-aviso-admin/)
    assert.equal(m.chamadas.length, 0, 'só carrega ao abrir')

    await m.estado.carregar()
    html = await m.html()
    assert.match(html, /data-plataforma="shopee"/)
    assert.match(html, /data-plataforma="temu"/)
    assert.match(html, /atlas<\/span><span class="text-muted-foreground"> · no chat: Jlas/)
    assert.match(html, /sem chat no DaVinci: e-mails, pedidos e garantia/)
    assert.match(html, /também com: londres/)
    assert.equal(m.estado.quantas, 1)
    assert.equal(m.estado.mudou, false)

    // Marca a Temu Barbosa e tira a Shopee atlas; a gravada fora da lista fica.
    m.estado.alternar(LOJAS[2], true)
    m.estado.alternar(LOJAS[0], false)
    assert.equal(m.estado.mudou, true)
    await m.estado.salvar()
    assert.deepEqual(gravado, { chaves: [...LOJAS[2].chaves, VELHA].sort() })
    assert.equal(m.chamadas.at(-1).metodo, 'PUT')
    assert.equal(m.estado.mudou, false)
    assert.equal(m.estado.mensagem, 'Lojas no atendimento salvas.')

    // Erro conhecido da API vira o texto da tela.
    const erroApi = Object.assign(new Error('x'), { data: { detail: { code: 'loja_desconhecida' } } })
    const m2 = montar('../components/UsuarioLojasAtendimento.vue', { userId: 'u1', isAdminUser: true }, {
      respostas: { [rota]: (_url, opts) => (opts.method === 'PUT' ? erroApi : resposta([])) },
    })
    await m2.estado.carregar()
    assert.match(await m2.html(), /data-aviso-admin/)
    m2.estado.alternarPlataforma(LOJAS.slice(0, 2), true)
    assert.equal(m2.estado.quantas, 2)
    await m2.estado.salvar()
    assert.equal(m2.estado.erro, U.ERROS_LOJAS.loja_desconhecida)
    m2.estado.desfazer()
    assert.equal(m2.estado.quantas, 0)
  }
  console.log('ok — usuario-lojas-atendimento')
}

principal().catch((e) => { console.error(e); process.exit(1) })
