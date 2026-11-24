// node tests/threema-quem-recebe-sfc.cjs — o atalho dos painéis de origem para
// Cadastros › Envios Threema (tarefa 16, F2). Sem rede e sem dado real:
//  - lib/enviosThreema: a linha do atalho (nomes, desligado, ninguém), o
//    "Enviar agora" parado quando ninguém receberia e o link do envio;
//  - components/ThreemaQuemRecebe.vue: só NOMES (a rota leve), o link
//    "Editar em Cadastros › Envios Threema" só para admin ("só ADM altera
//    quem recebe", decisão do dono em 23/11/2026), o desligado em âmbar, o
//    ninguém em vermelho, a configuração antiga, quem ficou de fora e o erro
//    (403 = sem acesso), avisando o modal do que leu;
//  - components/InformarThreemaModal.vue em modo atalho (`chave`): sem lista
//    de marcar, sem "Salvar" e sem PUT; o "Enviar agora" continua (o mesmo
//    POST), para quando ninguém receberia e mostra o motivo do servidor; o
//    modo antigo (os painéis ainda não migrados) continua igual;
//  - os painéis migrados (Logística, aba Amazon, Controle de Estoque e
//    Devoluções — a tela nova e a antiga) usam o atalho com as chaves do
//    catálogo.
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
  compileScript(descriptor, { id: path.basename(rel) })
  return { descriptor, fonte, filename }
}

const icones = new Proxy({}, { get: (_, nome) => ({ name: String(nome), render: () => Vue.h('i', { 'data-icone': String(nome) }) }) })
const libs = new Map()
let montadosAtual = []
const vueDoTeste = { ...Vue, onMounted: (fn) => montadosAtual.push(fn), onBeforeUnmount: () => {} }
function requerer(nome) {
  if (nome === 'lucide-vue-next') return icones
  if (nome === 'vue') return vueDoTeste
  const m = /^~\/lib\/(.+)$/.exec(nome)
  if (!m) return require(nome)
  if (!libs.has(m[1])) {
    const exp = {}
    const fonte = fs.readFileSync(path.resolve(__dirname, '../lib', `${m[1]}.ts`), 'utf8')
    new Function('exports', 'require', transpile(fonte))(exp, requerer)
    libs.set(m[1], exp)
  }
  return libs.get(m[1])
}
const lib = requerer('~/lib/enviosThreema')
const semComentarios = (html) => html.replace(/<!--[\s\S]*?-->/g, '')
const esperar = () => new Promise((r) => setTimeout(r, 0))

// Os componentes de fora, no SSR: o Button e o NuxtLink de verdade não
// importam aqui; o bloco "Quem recebe" dentro do modal é trocado por um
// marcador (o componente tem o seu próprio teste logo abaixo).
const BUTTON = {
  props: ['disabled', 'variant', 'size'],
  setup(p, { slots }) {
    return () => Vue.h('button', { disabled: p.disabled || undefined, 'data-variant': p.variant }, slots.default?.())
  },
}
const NUXT_LINK = {
  props: ['to'],
  setup(p, { slots }) { return () => Vue.h('a', { href: p.to }, slots.default?.()) },
}
const QUEM_MARCADOR = {
  props: ['chave', 'titulo'],
  setup(p) { return () => Vue.h('div', { 'data-quem-recebe': p.chave, 'data-titulo': p.titulo }) },
}

// O setup de verdade fora de um app e o HTML de um app SSR com o MESMO estado.
function montar(rel, { responder, props = {}, admin = true, componentes = {} }) {
  const { descriptor, filename } = sfc(rel)
  const script = compileScript(descriptor, { id: path.basename(rel), inlineTemplate: false })
  const tpl = compileTemplate({ source: descriptor.template.content, filename, id: path.basename(rel), compilerOptions: { isTS: true, bindingMetadata: script.bindings } })
  const chamadas = []
  const montados = []
  montadosAtual = montados
  const emitidos = []
  const g = {
    // auto-imports do Nuxt que os componentes usam sem importar
    ref: Vue.ref,
    watch: Vue.watch,
    computed: Vue.computed,
    useIsAdmin: () => Vue.computed(() => admin),
    useApi: () => ({
      api: async (url, opts = {}) => {
        chamadas.push({ url, method: opts.method || 'GET', body: opts.body })
        return structuredClone(await responder(url, opts))
      },
    }),
  }
  const nomes = Object.keys(g)
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', ...nomes, transpile(script.content))(requerer, mod, mod.exports, ...nomes.map((n) => g[n]))
  const tmod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(tpl.code))(requerer, tmod, tmod.exports)
  const comp = mod.exports.default
  const propsReativas = Vue.reactive({ ...props })
  const bruto = comp.setup(propsReativas, { emit: (n, ...a) => emitidos.push([n, ...a]), expose: () => {}, attrs: {}, slots: {} })
  const estado = Vue.proxyRefs(bruto)
  const filho = { ...comp, render: tmod.exports.render, setup: () => bruto }
  async function html() {
    const a = Vue.createSSRApp({ render: () => Vue.h(filho, { ...propsReativas }) })
    a.config.warnHandler = () => {}
    a.component('Button', BUTTON)
    a.component('NuxtLink', NUXT_LINK)
    for (const [n, c] of Object.entries(componentes)) a.component(n, c)
    return semComentarios(await renderToString(a))
  }
  return { html, estado, chamadas, props: propsReativas, emitidos, async montar() { for (const fn of montados) await fn(); await esperar() } }
}

const leve = (extra = {}) => ({
  chave: 'logistica.informar', nome: 'Informar da Logística', painel: 'Logística', migrado: true, ativo: true,
  via: 'cadastro', usa_configuracao_antiga: false, ninguem: false, nomes: ['joana', 'harry potter'], avisos: [], ...extra,
})

;(async () => {
  // ---------------------------------------------------------------- lib
  {
    assert.deepEqual(lib.situacaoQuemRecebe(leve()), { tipo: 'ok', texto: 'joana, harry potter' })
    assert.deepEqual(lib.situacaoQuemRecebe(leve({ ativo: false, nomes: [] })), {
      tipo: 'desligado', texto: 'Desligado em Cadastros › Envios Threema: não sai para ninguém.',
    })
    assert.deepEqual(lib.situacaoQuemRecebe(leve({ ninguem: true, nomes: [] })), {
      tipo: 'ninguem', texto: 'Ninguém recebe: este aviso não está saindo.',
    })
    assert.equal(lib.situacaoQuemRecebe(leve({ nomes: [] })).tipo, 'ninguem', 'sem nomes é ninguém')
    assert.equal(lib.podeEnviarAgora(leve()), true)
    assert.equal(lib.podeEnviarAgora(leve({ ativo: false })), false)
    assert.equal(lib.podeEnviarAgora(leve({ ninguem: true, nomes: [] })), false)
    assert.equal(lib.podeEnviarAgora(null), true, 'sem a lista, quem decide é o servidor')
    assert.equal(lib.linkDoEnvio('logistica.amazon_prazos'), '/envios-threema?envio=logistica.amazon_prazos')
    assert.equal(lib.linkDoEnvio('a b&c'), '/envios-threema?envio=a%20b%26c')
  }

  // ---------------------------------------------------------------- ThreemaQuemRecebe: estático
  {
    const { fonte } = sfc('../components/ThreemaQuemRecebe.vue')
    assert.ok(fonte.includes('`/api/threema/envios/${encodeURIComponent(props.chave)}/quem-recebe`'), 'a rota leve')
    assert.match(fonte, /<NuxtLink\s+v-if="isAdmin"\s+:to="link"/, 'o link de editar só para admin')
    assert.ok(!/threema_id|destinatarios|PUT|PATCH/.test(fonte), 'só lê nomes; não edita nada')
    assert.ok(!/localStorage|sessionStorage/.test(fonte))
  }

  // ---------------------------------------------------------------- ThreemaQuemRecebe: admin com nomes
  {
    const m = montar('../components/ThreemaQuemRecebe.vue', {
      props: { chave: 'logistica.informar' },
      responder: () => leve({ avisos: ['sumido está suspenso'] }),
    })
    await m.montar()
    assert.deepEqual(m.chamadas.map((c) => [c.method, c.url]), [['GET', '/api/threema/envios/logistica.informar/quem-recebe']])
    assert.deepEqual(m.emitidos, [['carregado', leve({ avisos: ['sumido está suspenso'] })]])
    const html = await m.html()
    assert.match(html, /data-quem-recebe="logistica\.informar"/)
    assert.match(html, />\s*Quem recebe\s*</, 'título padrão')
    assert.match(html, /data-situacao="ok"[^>]*>joana, harry potter</)
    assert.match(html, /<li>sumido está suspenso<\/li>/, 'quem ficou de fora aparece')
    assert.match(html, /<a href="\/envios-threema\?envio=logistica\.informar"[^>]*>\s*Editar em Cadastros › Envios Threema/)
    assert.ok(!/Só admin muda/.test(html))
    assert.ok(!/configuração antiga/.test(html))
  }

  // ---------------------------------------------------------------- não-admin: só lê
  {
    const m = montar('../components/ThreemaQuemRecebe.vue', {
      props: { chave: 'logistica.amazon_prazos', titulo: 'Quem recebe os avisos automáticos' },
      admin: false,
      responder: () => leve({ chave: 'logistica.amazon_prazos', via: 'legado', usa_configuracao_antiga: true }),
    })
    await m.montar()
    const html = await m.html()
    assert.match(html, /Quem recebe os avisos automáticos/)
    assert.ok(!/Editar em Cadastros/.test(html) && !/href=/.test(html), 'sem link de editar')
    assert.match(html, /Só admin muda quem recebe \(Cadastros › Envios Threema\)\./)
    assert.match(html, /Usando a configuração antiga/)
  }

  // ---------------------------------------------------------------- desligado e ninguém
  {
    const m = montar('../components/ThreemaQuemRecebe.vue', {
      props: { chave: 'logistica.amazon_prazos' },
      responder: () => leve({ ativo: false, nomes: [] }),
    })
    await m.montar()
    let html = await m.html()
    assert.match(html, /data-situacao="desligado"[^>]*>[\s\S]*Desligado em Cadastros › Envios Threema: não sai para ninguém\./)
    assert.match(html, /class="[^"]*text-amber-700[^"]*"[^>]*data-situacao="desligado"/, 'âmbar')
    const n = montar('../components/ThreemaQuemRecebe.vue', {
      props: { chave: 'logistica.informar' },
      responder: () => leve({ ninguem: true, nomes: [], avisos: ['joana está suspensa'] }),
    })
    await n.montar()
    html = await n.html()
    assert.match(html, /class="[^"]*text-red-600[^"]*"[^>]*data-situacao="ninguem"[^>]*>[\s\S]*Ninguém recebe: este aviso não está saindo\./)
    assert.match(html, /joana está suspensa/)
  }

  // ---------------------------------------------------------------- erro: 403 e rede
  {
    const m = montar('../components/ThreemaQuemRecebe.vue', {
      props: { chave: 'logistica.informar' },
      admin: false,
      responder: () => { throw Object.assign(new Error('403'), { status: 403 }) },
    })
    await m.montar()
    assert.deepEqual(m.emitidos, [['carregado', null]])
    assert.match(await m.html(), /Sem acesso a quem recebe este envio\./)
    const r = montar('../components/ThreemaQuemRecebe.vue', {
      props: { chave: 'logistica.informar' },
      responder: () => { throw new Error('rede') },
    })
    await r.montar()
    assert.match(await r.html(), /Não consegui carregar quem recebe\./)
    // trocar a chave relê
    let pedidos = 0
    const t = montar('../components/ThreemaQuemRecebe.vue', {
      props: { chave: 'logistica.informar' },
      responder: (url) => { pedidos += 1; return leve({ chave: url.split('/')[4] }) },
    })
    await t.montar()
    t.props.chave = 'logistica.amazon_informar'
    await esperar(); await esperar()
    assert.equal(pedidos, 2)
    assert.equal(t.chamadas.at(-1).url, '/api/threema/envios/logistica.amazon_informar/quem-recebe')
  }

  // ---------------------------------------------------------------- modal em modo atalho
  {
    const m = montar('../components/InformarThreemaModal.vue', {
      props: {
        open: false, contexto: 'logistica_amazon', chave: 'logistica.amazon_informar',
        chaveAuto: 'logistica.amazon_prazos', descricao: 'Aba Amazon',
      },
      componentes: { ThreemaQuemRecebe: QUEM_MARCADOR },
      responder: (url) => {
        if (url === '/api/informar/logistica_amazon/enviar') return { pedidos: 3, mensagens: 1, sent: ['X'], failed: [] }
        throw new Error(`não devia chamar ${url}`)
      },
    })
    m.props.open = true
    await esperar()
    assert.deepEqual(m.chamadas, [], 'abrir não lê a lista antiga')
    let html = await m.html()
    assert.match(html, /data-quem-recebe="logistica\.amazon_informar" data-titulo="Quem recebe o Enviar agora"/)
    assert.match(html, /data-quem-recebe="logistica\.amazon_prazos" data-titulo="Quem recebe os avisos automáticos"/)
    assert.ok(!/type="checkbox"/.test(html), 'sem lista de marcar')
    assert.ok(!/Salvar/.test(html), 'sem Salvar')
    assert.match(html, /<button>[\s\S]*Enviar agora/, 'Enviar agora continua e liga antes de ler a lista')
    // o bloco leu: ninguém recebe → o botão para
    m.estado.quemPrincipal = leve({ ninguem: true, nomes: [] })
    html = await m.html()
    assert.match(html, /<button disabled[^>]*>[\s\S]*Enviar agora/, 'ninguém recebe: o botão para')
    m.estado.quemPrincipal = leve()
    html = await m.html()
    assert.match(html, /<button>[\s\S]*Enviar agora/)
    await m.estado.enviar()
    assert.deepEqual(m.chamadas.map((c) => [c.method, c.url]), [['POST', '/api/informar/logistica_amazon/enviar']], 'sem PUT antes')
    assert.equal(m.estado.resultado, 'Enviado: 3 pedido(s) para 1 destinatário(s).')
    // reabrir limpa o que o bloco leu (ele lê de novo)
    m.props.open = false
    await esperar()
    m.props.open = true
    await esperar()
    assert.equal(m.estado.quemPrincipal, null)
    assert.equal(m.estado.resultado, null)
  }

  // ---------------------------------------------------------------- atalho: o 422 do servidor aparece
  {
    const m = montar('../components/InformarThreemaModal.vue', {
      props: { open: true, contexto: 'logistica', chave: 'logistica.informar', descricao: 'x' },
      componentes: { ThreemaQuemRecebe: QUEM_MARCADOR },
      responder: () => {
        throw { data: { detail: { code: 'envio_desligado', message: 'Este envio está desligado em Cadastros › Envios Threema.' } } }
      },
    })
    let html = await m.html()
    assert.match(html, /data-quem-recebe="logistica\.informar" data-titulo="Quem recebe"/)
    assert.ok(!/avisos automáticos/.test(html), 'sem chaveAuto, um bloco só')
    await m.estado.enviar()
    assert.equal(m.estado.erro, 'Este envio está desligado em Cadastros › Envios Threema.')
    html = await m.html()
    assert.match(html, /Este envio está desligado em Cadastros › Envios Threema\./)
  }

  // ---------------------------------------------------------------- modo antigo continua igual
  {
    const m = montar('../components/InformarThreemaModal.vue', {
      props: { open: false, contexto: 'margem', contextoAuto: 'margem_auto', descricao: 'Margem' },
      componentes: { ThreemaQuemRecebe: QUEM_MARCADOR },
      responder: (url, opts) => {
        if (opts.method === 'PUT') return { contexto: 'margem', recipients: opts.body.recipients, destinatarios: [] }
        if (url === '/api/informar/margem') return { contexto: 'margem', recipients: ['AAAA1111'], destinatarios: [{ id: 'AAAA1111', nome: 'Ana' }] }
        if (url === '/api/informar/margem_auto') return { contexto: 'margem_auto', recipients: [], destinatarios: [] }
        throw new Error(`não devia chamar ${url}`)
      },
    })
    m.props.open = true
    await esperar(); await esperar()
    assert.deepEqual(m.chamadas.map((c) => c.url), ['/api/informar/margem', '/api/informar/margem_auto'])
    const html = await m.html()
    assert.match(html, /type="checkbox"/)
    assert.match(html, /Salvar/)
    assert.ok(!/data-quem-recebe/.test(html), 'sem o bloco do Cadastro')
    await m.estado.salvarFechar()
    assert.deepEqual(m.chamadas.filter((c) => c.method === 'PUT').map((c) => c.url), ['/api/informar/margem', '/api/informar/margem_auto'])
  }

  // ---------------------------------------------------------------- os painéis migrados usam o atalho
  {
    const catalogo = fs.readFileSync(path.resolve(__dirname, '../../api/app/services/threema_catalogo.py'), 'utf8')
    const logistica = fs.readFileSync(path.resolve(__dirname, '../pages/logistica.vue'), 'utf8')
    const modais = logistica.match(/<InformarThreemaModal[\s\S]*?\/>/g)
    assert.equal(modais.length, 2)
    const [amazon, geral] = modais
    assert.match(amazon, /contexto="logistica_amazon"\s+chave="logistica\.amazon_informar"\s+chave-auto="logistica\.amazon_prazos"/)
    assert.match(geral, /contexto="logistica"\s+chave="logistica\.informar"/)
    const estoque = fs.readFileSync(path.resolve(__dirname, '../pages/controle-estoque.vue'), 'utf8')
    const modaisEstoque = estoque.match(/<InformarThreemaModal[\s\S]*?\/>/g)
    assert.equal(modaisEstoque.length, 1)
    assert.match(modaisEstoque[0], /contexto="controle_estoque"\s+chave="controle_estoque\.informar"/)
    // Devoluções (C9c): o "Enviar agora" (11) e o aviso do prazo de resposta
    // da loja (10), na tela nova e na antiga
    const modaisDevolucoes = ['../pages/devolucoes.vue', '../pages/devolucoes-antigo.vue'].map((rel) => {
      const fonte = fs.readFileSync(path.resolve(__dirname, rel), 'utf8')
      const achados = fonte.match(/<InformarThreemaModal[\s\S]*?\/>/g)
      assert.equal(achados.length, 1, rel)
      assert.match(
        achados[0],
        /contexto="devolucoes"\s+chave="devolucoes\.informar"\s+chave-auto="devolucoes\.prazo_resposta_loja"/,
        rel,
      )
      assert.ok(!/escolher quem recebe/.test(fonte), `${rel}: o botão não promete escolher quem recebe`)
      return achados[0]
    })
    for (const modal of [...modais, ...modaisEstoque, ...modaisDevolucoes]) {
      assert.ok(!/contexto-auto|enviar-com-auto|A seleção|Quem está marcado/.test(modal), 'nada de lista para salvar')
      for (const [, chave] of modal.matchAll(/chave(?:-auto)?="([^"]+)"/g)) {
        assert.ok(catalogo.includes(`= "${chave}"`), `chave do catálogo: ${chave}`)
      }
    }
  }

  console.log('threema-quem-recebe-sfc: ok')
})().catch((e) => {
  console.error(e)
  process.exit(1)
})
