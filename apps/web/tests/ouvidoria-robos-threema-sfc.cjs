// node tests/ouvidoria-robos-threema-sfc.cjs — Ouvidoria › Robôs com o aviso
// dos robôs em Cadastros › Envios Threema (tarefa 16, F3/C10a). Sem rede e
// sem dado real:
//  - lib/enviosThreema: a coluna "Avisa" do robô que lê o Cadastro
//    (desligado, ninguém válido, configuração antiga vazia, os nomes), a
//    config sem as chaves que o Cadastro governa e o link dos Contatos
//    externos;
//  - pages/ouvidoria/robos.vue, o robô MIGRADO ("só ADM altera quem recebe",
//    decisão do dono em 23/11/2026): a linha aberta mostra o atalho
//    (ThreemaQuemRecebe com a chave do robô) no lugar da lista "Avisar"; o
//    re-aviso fica só leitura com o link (admin) ou a frase (os demais); a
//    edição não tem seletor nem campo de re-aviso, e o PATCH não manda a
//    lista nem o re-aviso; o que o servidor ignorou vira aviso; a chave da
//    config que o Cadastro governa (o "avisar margem alta", quando o 27
//    migrar) fica só leitura e fora do PATCH;
//  - o robô que ainda NÃO migrou (o Robô da Margem, até a C10b) segue com o
//    seletor e o re-aviso de antes — mas o contato avulso virou link para
//    Cadastros › Envios Threema › Contatos externos (não se cadastra nem se
//    remove mais daqui).
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

const PAGINA = '../pages/ouvidoria/robos.vue'

function sfc(rel) {
  const filename = path.resolve(__dirname, rel)
  const fonte = fs.readFileSync(filename, 'utf8')
  const { descriptor, errors } = parse(fonte, { filename })
  assert.deepEqual(errors, [], rel)
  const script = compileScript(descriptor, { id: path.basename(rel), inlineTemplate: false })
  const tpl = compileTemplate({
    source: descriptor.template.content, filename, id: path.basename(rel),
    compilerOptions: { isTS: true, bindingMetadata: script.bindings },
  })
  assert.deepEqual(tpl.errors, [], `${rel}: template compila`)
  return { descriptor, fonte, script, tpl }
}

const icones = new Proxy({}, { get: (_, nome) => ({ name: String(nome), render: () => Vue.h('i', { 'data-icone': String(nome) }) }) })
const libs = new Map()
const vueDoTeste = { ...Vue, onMounted: () => {}, onBeforeUnmount: () => {} }
function requerer(nome) {
  if (nome === 'lucide-vue-next') return icones
  if (nome === 'vue') return vueDoTeste
  if (nome === '~/components/OuvidoriaPlataforma.vue') {
    return { PLATAFORMAS: [], plataformaInfo: (p) => ({ nome: String(p), curto: String(p) }) }
  }
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

// Os componentes de fora, no SSR: marcadores simples (cada um tem o seu teste).
function marcador(nome, props = []) {
  return {
    props,
    setup(p, { slots }) {
      return () => Vue.h('div', { 'data-stub': nome, ...Object.fromEntries(props.map((k) => [`data-${k}`, p[k]])) }, slots.default?.())
    },
  }
}
const NUXT_LINK = {
  props: ['to'],
  setup(p, { slots }) { return () => Vue.h('a', { href: p.to }, slots.default?.()) },
}
const COMPONENTES = {
  NuxtLink: NUXT_LINK,
  ThreemaQuemRecebe: marcador('ThreemaQuemRecebe', ['chave', 'titulo']),
  PageHeader: marcador('PageHeader'),
  StatCard: marcador('StatCard'),
  Button: marcador('Button'),
  OuvidoriaModoToggle: marcador('OuvidoriaModoToggle'),
  OuvidoriaPlataforma: marcador('OuvidoriaPlataforma'),
  ObservacaoPopover: marcador('ObservacaoPopover'),
}

// O setup de verdade fora de um app e o HTML de um app SSR com o MESMO estado.
function montar({ responder, admin = true, podeEditar = true }) {
  const { script, tpl } = sfc(PAGINA)
  const chamadas = []
  const toasts = []
  const g = {
    definePageMeta: () => {},
    useApi: () => ({
      api: async (url, opts = {}) => {
        chamadas.push({ url, method: opts.method || 'GET', body: opts.body })
        return structuredClone(await responder(url, opts))
      },
    }),
    useToasts: () => Object.fromEntries(
      ['success', 'error', 'warning', 'info'].map((k) => [k, (titulo, linhas) => toasts.push([k, titulo, linhas])]),
    ),
    useCan: () => Vue.computed(() => podeEditar),
    useIsAdmin: () => Vue.computed(() => admin),
    useRoute: () => ({ query: {} }),
    useRouter: () => ({ replace: () => {} }),
  }
  const nomes = Object.keys(g)
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', ...nomes, transpile(script.content))(requerer, mod, mod.exports, ...nomes.map((n) => g[n]))
  const tmod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(tpl.code))(requerer, tmod, tmod.exports)
  const comp = mod.exports.default
  const bruto = comp.setup({}, { emit: () => {}, expose: () => {}, attrs: {}, slots: {} })
  const estado = Vue.proxyRefs(bruto)
  const filho = { ...comp, render: tmod.exports.render, setup: () => bruto }
  async function html() {
    const a = Vue.createSSRApp({ render: () => Vue.h(filho) })
    a.config.warnHandler = () => {}
    for (const [n, c] of Object.entries(COMPONENTES)) a.component(n, c)
    return semComentarios(await renderToString(a))
  }
  return { html, estado, chamadas, toasts }
}

const robo = (extra = {}) => ({
  chave: 'vigia_importacao', nome: 'Vigia de Importação', descricao: null, area: 'Importação',
  cadencia_texto: ':09', plataformas: [], modo: 'ligado', modo_alterado_por: null, modo_alterado_em: null,
  arquivado_em: null, arquivado_por: null, threema_recipients: 'TSTH0002',
  threema_destinatarios: [{ id: 'TS…', nome: 'joana' }], threema_origem: 'cadastro',
  threema_envio: 'ouvidoria.vigia_importacao', threema_migrado: true, threema_via: 'cadastro', threema_ativo: true,
  config_no_cadastro: {}, avisos: [], reaviso_horas: 24,
  config: { tolerancia_min: 60, janela_horas: 72 }, config_rotulos: { tolerancia_min: 'Tolerância (min)' },
  avisos_modo: {}, ultima_rodada_em: null, ultima_rodada_ok: null, ultima_rodada_resumo: null,
  ultima_rodada_duracao_ms: null, ultima_falha_em: null, ultima_falha_erro: null,
  abertas: 0, abertas_pessoa: 0, rodadas_hoje: 0, rodadas_hoje_ok: 0, saude: 'ok', ...extra,
})
const margem = (extra = {}) => robo({
  chave: 'vigia_margem', nome: 'Robô da Margem', threema_envio: 'ouvidoria.vigia_margem', threema_migrado: false,
  threema_via: null, threema_ativo: null, threema_origem: 'robo', threema_recipients: 'TSTH0002',
  threema_destinatarios: [{ id: 'TSTH0002', nome: 'harry potter' }],
  config: { cadencia_min: 30, avisar_margem_alta: false },
  config_rotulos: { avisar_margem_alta: 'Avisar margem alta' }, ...extra,
})

;(async () => {
  // ---------------------------------------------------------------- lib
  {
    assert.equal(lib.LINK_CONTATOS_EXTERNOS, '/envios-threema?aba=contatos')
    assert.equal(lib.roboNoCadastro(robo()), true)
    assert.equal(lib.roboNoCadastro(margem()), false, 'o 34 ainda não migrou')
    assert.equal(lib.roboNoCadastro(robo({ threema_migrado: undefined })), false, 'a API de antes')
    assert.equal(lib.roboNoCadastro(robo({ threema_envio: null })), false, 'o relatório da Denúncia')
    assert.deepEqual(lib.avisaRoboCadastro(robo()), {
      linha1: 'Threema · 1 pessoa', linha2: 're-aviso 24 h', title: 'joana (Cadastros › Envios Threema)',
    })
    const desligado = lib.avisaRoboCadastro(robo({ threema_ativo: false, threema_destinatarios: [] }))
    assert.equal(desligado.linha1, 'Desligado')
    assert.match(desligado.title, /saem juntas ao religar/)
    const ninguem = lib.avisaRoboCadastro(robo({ threema_destinatarios: [] }))
    assert.deepEqual([ninguem.linha1, ninguem.linha2], ['Ninguém', 'ninguém válido no Cadastro'])
    const antiga = lib.avisaRoboCadastro(robo({ threema_via: 'legado', threema_destinatarios: [] }))
    assert.equal(antiga.linha2, 'configuração antiga vazia')
    assert.match(lib.avisaRoboCadastro(robo({ threema_via: 'legado' })).title, /configuração antiga/)
    assert.deepEqual(
      lib.configSemCadastro({ cadencia_min: 30, avisar_margem_alta: true }, { config_no_cadastro: { avisar_margem_alta: 'margem.margem_alta' } }),
      { cadencia_min: 30 },
    )
    assert.deepEqual(lib.configSemCadastro({ a: 1 }, {}), { a: 1 })
  }

  // ---------------------------------------------------------------- estático
  {
    const { fonte } = sfc(PAGINA)
    // o contato avulso não se cadastra nem se remove mais daqui
    assert.ok(!/method: 'POST'[\s\S]{0,80}ouvidoria\/threema\/destinatarios|ouvidoria\/threema\/destinatarios[\s\S]{0,80}method: '(POST|DELETE)'/.test(fonte))
    assert.ok(!/adicionarContato|removerContato|novoContatoId/.test(fonte))
    assert.match(fonte, /:to="LINK_CONTATOS_EXTERNOS"/)
    // o atalho do robô migrado, com a chave dele
    assert.match(fonte, /<div v-if="roboNoCadastro\(r\)"[^>]*>[\s\S]*?<ThreemaQuemRecebe :chave="r\.threema_envio \|\| ''"/)
    // o campo de re-aviso e o seletor só no robô que não migrou
    const edicao = fonte.slice(fonte.indexOf('<template v-if="roboNoCadastro(r)">'))
    assert.ok(edicao.indexOf('<template v-else>') < edicao.indexOf('v-model.number="editForm.reaviso_horas"'))
    assert.ok(!/localStorage|sessionStorage/.test(fonte))
  }

  // ---------------------------------------------------------------- robô migrado: leitura (admin)
  {
    const m = montar({ responder: () => { throw new Error('não devia chamar') } })
    m.estado.robos = [robo(), margem()]
    m.estado.robosCarregou = true
    m.estado.expandidos = new Set(['vigia_importacao', 'vigia_margem'])
    const html = await m.html()
    assert.deepEqual(m.chamadas, [])
    // o atalho do migrado, no lugar da lista "Avisar"
    assert.match(html, /data-avisar-cadastro="ouvidoria\.vigia_importacao"[\s\S]*?data-stub="ThreemaQuemRecebe" data-chave="ouvidoria\.vigia_importacao" data-titulo="Quem recebe os avisos deste robô"/)
    assert.equal((html.match(/data-stub="ThreemaQuemRecebe"/g) || []).length, 1, 'só o robô migrado tem o atalho')
    // o re-aviso: só leitura, com o link do envio (admin)
    assert.match(html, /<a href="\/envios-threema\?envio=ouvidoria\.vigia_importacao"[^>]*data-reaviso-cadastro[^>]*>muda em Cadastros › Envios Threema/)
    // o não migrado segue com a lista "Avisar" de antes
    assert.match(html, /Threema: harry potter/)
    // a coluna "Avisa" do migrado
    assert.match(html, /Threema · 1 pessoa/)
  }

  // ---------------------------------------------------------------- robô migrado: leitura (não admin)
  {
    const m = montar({ admin: false, responder: () => { throw new Error('não devia chamar') } })
    m.estado.robos = [robo({ modo: 'silencioso' })]
    m.estado.robosCarregou = true
    m.estado.expandidos = new Set(['vigia_importacao'])
    const html = await m.html()
    assert.ok(!/href="\/envios-threema/.test(html), 'sem link para quem não é admin')
    assert.match(html, /só admin muda \(Cadastros › Envios Threema\)/)
    assert.match(html, /Robô em silencioso: registra e não avisa\.\s*A lista abaixo vale quando ele for ligado\./)
    assert.match(html, /data-stub="ThreemaQuemRecebe"/)
  }

  // ---------------------------------------------------------------- robô migrado: edição e PATCH
  {
    const m = montar({
      responder: (url, opts) => {
        if (opts.method === 'PATCH') {
          return robo({ avisos: ['O re-aviso deste robô agora é mudado em Cadastros › Envios Threema (só admin): o valor mandado não foi gravado.'] })
        }
        if (url.startsWith('/api/ouvidoria/robos')) return url.endsWith('/robos') ? [robo()] : { ...robo(), rodadas: [], contas: [] }
        throw new Error(`não devia chamar ${url}`)
      },
    })
    const r = robo()
    m.estado.robos = [r]
    m.estado.robosCarregou = true
    m.estado.expandidos = new Set(['vigia_importacao'])
    m.estado.abrirEdicao(r)
    await esperar()
    assert.deepEqual(m.chamadas, [], 'abrir a edição do migrado não lê o seletor antigo')
    assert.deepEqual(m.estado.editForm.threema_ids, [])
    const html = await m.html()
    assert.ok(!/id="reaviso-vigia_importacao"/.test(html), 'sem campo de re-aviso')
    assert.ok(!/Escolher quem recebe os avisos/.test(html), 'sem seletor')
    assert.match(html, /data-threema-cadastro[^>]*>\s*quem recebe é escolhido em\s*<a href="\/envios-threema\?envio=ouvidoria\.vigia_importacao"/)
    assert.match(html, /data-reaviso-cadastro[^>]*>\s*a cada 24 h ·/)
    m.estado.setConfig('tolerancia_min', 90)
    await m.estado.salvarEdicao(r)
    const patch = m.chamadas.find((c) => c.method === 'PATCH')
    assert.equal(patch.url, '/api/ouvidoria/robos/vigia_importacao')
    assert.deepEqual(patch.body, { config: { tolerancia_min: 90, janela_horas: 72 } }, 'sem lista nem re-aviso')
    assert.ok(m.toasts.some(([k, t]) => k === 'success' && t === 'Configuração salva'))
    assert.ok(m.toasts.some(([k, t]) => k === 'warning' && /re-aviso deste robô agora é mudado em Cadastros/.test(t)), 'o aviso do servidor aparece')
  }

  // ---------------------------------------------------------------- chave da config governada pelo Cadastro
  {
    const governada = margem({
      threema_migrado: true, threema_via: 'cadastro', threema_ativo: true, threema_origem: 'cadastro',
      threema_destinatarios: [{ id: 'TS…', nome: 'joana' }],
      config_no_cadastro: { avisar_margem_alta: 'margem.margem_alta' },
    })
    const m = montar({ admin: false, responder: (url, opts) => (opts.method === 'PATCH' ? governada : url.endsWith('/robos') ? [governada] : { ...governada, rodadas: [], contas: [] }) })
    m.estado.robos = [governada]
    m.estado.robosCarregou = true
    m.estado.expandidos = new Set(['vigia_margem'])
    let html = await m.html()
    assert.match(html, /<span[^>]*data-config-cadastro="avisar_margem_alta"[^>]*>só admin muda \(Cadastros › Envios Threema\)/)
    m.estado.abrirEdicao(governada)
    assert.ok(!('avisar_margem_alta' in m.estado.editForm.config), 'sem caixinha na edição')
    html = await m.html()
    assert.ok(!/id="cfg-vigia_margem-avisar_margem_alta"/.test(html))
    assert.match(html, /<span data-config-cadastro="avisar_margem_alta">\s*não ·/)
    await m.estado.salvarEdicao(governada)
    const patch = m.chamadas.find((c) => c.method === 'PATCH')
    assert.deepEqual(patch.body, { config: { cadencia_min: 30 } })
  }

  // ---------------------------------------------------------------- robô não migrado: como antes, contato vira link
  {
    const m = montar({
      responder: (url, opts) => {
        if (url === '/api/ouvidoria/threema/destinatarios' && (opts.method || 'GET') === 'GET') {
          return [{ id: 'TSTH0002', nome: 'harry potter', origem: 'usuario' }, { id: 'TSTR0008', nome: 'Roma', origem: 'contato' }]
        }
        if (opts.method === 'PATCH') return margem({ reaviso_horas: 12 })
        if (url.startsWith('/api/ouvidoria/robos')) return url.endsWith('/robos') ? [margem()] : { ...margem(), rodadas: [], contas: [] }
        throw new Error(`não devia chamar ${opts.method} ${url}`)
      },
    })
    const r = margem()
    m.estado.robos = [r]
    m.estado.robosCarregou = true
    m.estado.expandidos = new Set(['vigia_margem'])
    m.estado.abrirEdicao(r)
    await esperar()
    assert.deepEqual(m.chamadas.map((c) => [c.method, c.url]), [['GET', '/api/ouvidoria/threema/destinatarios']])
    m.estado.seletorAberto = true
    const html = await m.html()
    assert.match(html, /id="reaviso-vigia_margem"/, 'o re-aviso segue editável até a C10b')
    assert.match(html, /data-contatos-externos[\s\S]*?<a href="\/envios-threema\?aba=contatos"[^>]*>Cadastros › Envios Threema › Contatos externos/)
    assert.ok(!/placeholder="VBS64V3S"/.test(html) && !/cadastrar/.test(html), 'sem cadastrar contato aqui')
    assert.ok(!/Tirar este contato da lista/.test(html), 'sem remover contato aqui')
    m.estado.editForm = { ...m.estado.editForm, reaviso_horas: 12 }
    await m.estado.salvarEdicao(r)
    const patch = m.chamadas.find((c) => c.method === 'PATCH')
    assert.deepEqual(patch.body, {
      config: { cadencia_min: 30, avisar_margem_alta: false }, reaviso_horas: 12, threema_recipients: 'TSTH0002',
    })
  }

  console.log('ouvidoria-robos-threema-sfc: ok')
})().catch((e) => { console.error(e); process.exit(1) })
