// node tests/envios-threema-sfc.cjs — Cadastros › Envios Threema (tarefa 16, C6).
// A página lista cada envio do Threema (GET /api/threema/envios) por painel,
// com quem recebe (só nomes), frequência, do que depende, se está ligado, o
// último envio e as faixas (âmbar "usa a configuração antiga", vermelha
// "ninguém recebe"...). A gaveta abre pelo endereço (?envio=) e mostra o
// detalhe; envio do servidor não migrado é só leitura. Sem rede e sem dado
// real:
//  - lib/enviosThreema: os textos de quem recebe e do último envio;
//  - as SFCs compilam;
//  - a página (setup de verdade, API falsa): a consulta de fábrica, os
//    filtros viram parâmetros, as faixas, o informativo em cinza, Editar só
//    no editável, o ?pessoa= e o ?envio= no endereço;
//  - a gaveta: o detalhe e os últimos envios, só leitura do não migrado
//    (com onde mudar), as regras do item 1 com a marcada, e o link do
//    Histórico só para quem o vê.
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

// O PageHeader de verdade (cabeçalho igual ao das outras abas de Cadastros).
function componente(rel) {
  const { descriptor } = sfc(rel)
  const script = compileScript(descriptor, { id: path.basename(rel), inlineTemplate: true })
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(script.content))(requerer, mod, mod.exports)
  return mod.exports.default
}
const PAGE_HEADER = componente('../components/PageHeader.vue')

// O setup de verdade fora de um app e o HTML de um app SSR com o MESMO estado.
function montar(rel, { responder, props = {}, query = {}, user = { role: 'admin' } }) {
  const { descriptor, filename } = sfc(rel)
  const script = compileScript(descriptor, { id: path.basename(rel), inlineTemplate: false })
  const tpl = compileTemplate({ source: descriptor.template.content, filename, id: path.basename(rel), compilerOptions: { isTS: true, bindingMetadata: script.bindings } })
  const chamadas = []
  const montados = []
  montadosAtual = montados
  const route = Vue.reactive({ query: { ...query } })
  const emitidos = []
  const g = {
    definePageMeta: () => {},
    useRoute: () => route,
    useRouter: () => ({ replace: ({ query: q }) => { route.query = { ...q } } }),
    useAuthStore: () => ({ user }),
    useApi: () => ({
      api: async (url, opts = {}) => {
        chamadas.push({ url, method: opts.method || 'GET', query: { ...(opts.query || {}) }, body: opts.body })
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
    a.component('PageHeader', PAGE_HEADER)
    return semComentarios(await renderToString(a))
  }
  return { html, estado, chamadas, route, props: propsReativas, emitidos, async montar() { for (const fn of montados) await fn(); await esperar() } }
}

// ---------------------------------------------------------------- dados falsos
const BASE = {
  painel: 'Logística', aba: null, descricao: 'Descrição curta.', frequencia_tipo: 'evento',
  frequencia_texto: 'conferido às :13 e :43', onde_roda: 'servidor', remetente: 'logistica',
  gerenciado: true, migrado: false, fatia: 'F1', interruptor: 'proprio', ao_desligar: 'descarta',
  ao_desligar_texto: 'os avisos deste período se perdem', escopo_tipo: null, editavel: false,
  desligavel: true, por_que_nao_edita: 'Ainda usa a configuração antiga. Para mudar quem recebe: .env do servidor (NF_SEM_ESTOQUE_THREEMA_RECIPIENTS).',
  onde_configurar: '.env do servidor (NF_SEM_ESTOQUE_THREEMA_RECIPIENTS)', tem_reaviso: false, reaviso_horas: null,
  ativo: true, ativo_texto: 'ligado (segue o interruptor de hoje)', ativo_cadastro: null, ativo_alterado_por: null,
  ativo_alterado_em: null, observacao: null, semeado_em: null, depende_de: [], ultimo: null,
}
const VIGIA = {
  ...BASE, chave: 'logistica.vigia_automacao', nome: 'Automação parada',
  quem_recebe: { via: 'legado', motivo: 'nao_migrado', fonte: 'NF_SEM_ESTOQUE_THREEMA_RECIPIENTS do servidor', nomes: [{ nome: 'londres', tipo: 'usuario' }, { nome: 'harry potter', tipo: 'usuario' }, { nome: 'heisenberg', tipo: 'usuario' }, { nome: 'cairo sa', tipo: 'usuario' }, { nome: 'Roma', tipo: 'contato' }], avisos: [], total: 5 },
  ultimo: { lote_id: 'l1', criado_em: new Date(Date.now() - 2 * 3600e3).toISOString(), titulo: 'Logística — automação parada:', total: 2, entregues: 1, falhas: 1, erro: 'http_402', origem: 'automatico', onde_roda: 'servidor', via: null },
  situacao: 'falhando', situacoes: ['falhando', 'legado'],
  faixas: [
    { tipo: 'vermelho', situacao: 'falhando', texto: 'O último envio não chegou a todos (1/2): sem crédito no Threema (402).' },
    { tipo: 'ambar', situacao: 'legado', texto: 'Usa a configuração antiga: NF_SEM_ESTOQUE_THREEMA_RECIPIENTS do servidor. Para mudar quem recebe: .env do servidor (NF_SEM_ESTOQUE_THREEMA_RECIPIENTS).' },
  ],
}
const PRAZO = {
  ...BASE, chave: 'devolucoes.prazo_resposta_loja', painel: 'Devoluções', nome: 'Prazo de resposta da loja',
  quem_recebe: { via: 'legado', motivo: 'nao_migrado', fonte: "nenhuma linha do Informar (devolucoes_auto)", nomes: [], avisos: [], total: 0 },
  situacao: 'ninguem', situacoes: ['ninguem', 'legado'],
  faixas: [{ tipo: 'vermelho', situacao: 'ninguem', texto: 'Ninguém recebe: este aviso não está saindo.' }],
}
const INFORMATIVO = {
  ...BASE, chave: 'pedidos.correcao_dia_envio', painel: 'Pedidos', nome: 'Correção do dia de envio', gerenciado: false,
  quem_recebe: { via: 'nenhum', motivo: null, fonte: 'nada sai pelo DaVinci', nomes: [], avisos: [], total: 0 },
  ativo: false, ativo_texto: '—', situacao: 'informativo', situacoes: ['informativo'],
  faixas: [{ tipo: 'cinza', situacao: 'informativo', texto: 'Informativo: nada sai daqui pelo DaVinci hoje.' }],
}
const MAC = {
  ...BASE, chave: 'devolucoes.codigos_ml_tuta', painel: 'Devoluções', nome: 'Códigos de devolução do ML', onde_roda: 'mac',
  remetente: null, editavel: true, por_que_nao_edita: null, fatia: 'mac',
  quem_recebe: { via: 'mac_env', motivo: null, fonte: '.env do Mac (DEVOL_CODIGOS_THREEMA_TO)', nomes: [], avisos: [], total: 0 },
  situacao: 'mac_env', situacoes: ['mac_env'],
  faixas: [{ tipo: 'azul', situacao: 'mac_env', texto: 'O Mac ainda usa o .env (DEVOL_CODIGOS_THREEMA_TO).' }],
}
const LISTA = {
  gerado_em: '2026-11-19T12:00:00Z',
  emergencia: { usar_legado: false, alterado_por: null, alterado_em: null, env_desligado: false },
  paineis: ['Logística', 'Devoluções', 'Pedidos'],
  contagem: { ok: 0, legado: 2, ninguem: 1, invalido: 0, desligado: 0, bloqueado: 0, informativo: 1, externo: 0, falhando: 1, mac_env: 1 },
  total: 60, envios: [VIGIA, PRAZO, MAC, INFORMATIVO], pessoa: null,
}

// ---------------------------------------------------------------- lib
{
  assert.equal(lib.quemRecebeTexto(VIGIA), 'londres, harry potter, heisenberg, cairo sa e mais 1')
  assert.equal(lib.quemRecebeTexto(PRAZO), 'ninguém')
  assert.equal(lib.quemRecebeTexto(INFORMATIVO), '—')
  const agora = new Date('2026-11-19T12:00:00Z')
  assert.equal(lib.haQuanto('2026-11-19T11:55:00Z', agora), 'há 5 min')
  assert.equal(lib.haQuanto('2026-11-19T10:00:00Z', agora), 'há 2 h')
  assert.equal(lib.haQuanto('2026-11-16T12:00:00Z', agora), 'há 3 dias')
  const u = { ...VIGIA.ultimo, criado_em: '2026-11-19T10:00:00Z' }
  assert.equal(lib.ultimoEnvioTexto(u, agora), 'há 2 h, 1/2 — falhou: sem crédito (402)')
  assert.equal(lib.ultimoEnvioTexto({ ...u, falhas: 0, entregues: 2 }, agora), 'há 2 h, 2/2')
  assert.equal(lib.ultimoEnvioTexto({ ...u, total: 0, entregues: 0, erro: 'sem_destinatario_valido' }, agora), 'há 2 h, não saiu: ninguém válido')
  assert.equal(lib.ultimoEnvioTexto(null), 'nenhum registrado')
  assert.match(lib.classeFaixa('vermelho'), /bg-red-50/)
  assert.match(lib.classeFaixa('ambar'), /bg-amber-50/)
  assert.deepEqual(Object.keys(lib.SITUACAO).sort(), ['bloqueado', 'desligado', 'externo', 'falhando', 'informativo', 'invalido', 'legado', 'mac_env', 'ninguem', 'ok'])
}

;(async () => {
  // ---------------------------------------------------------------- a página
  const { fonte } = sfc('../pages/envios-threema.vue')
  assert.match(fonte, /definePageMeta\(\{ middleware: \['admin'\] \}\)/, 'só admin')
  assert.match(fonte, /<RouteTabs :tabs="TABS_CADASTROS" \/>/)
  assert.match(fonte, /<PageHeader title="Envios Threema" :description="descricao">/, 'cabeçalho igual ao das abas vizinhas')
  {
    const m = montar('../pages/envios-threema.vue', { responder: () => LISTA })
    await m.montar()
    assert.equal(m.chamadas.length, 1)
    assert.deepEqual(m.chamadas[0], { url: '/api/threema/envios', method: 'GET', query: {}, body: undefined })
    let html = await m.html()
    assert.match(html, /<h1 class="page-header-title[^"]*">Envios Threema<\/h1>/)
    assert.match(html, /page-header-description[^>]*>[^<]*60 envios · 0 lendo daqui/)
    assert.match(html, /Emergência: voltar todos para a configuração antiga/)
    assert.match(html, /Desligada\. Os envios migrados leem a lista daqui\./)
    // por painel, na ordem
    const paineis = [...html.matchAll(/<h2 class="text-sm font-semibold text-muted-foreground">([^<]+)<\/h2>/g)].map((x) => x[1])
    assert.deepEqual(paineis, ['Logística', 'Devoluções', 'Pedidos'])
    // faixas e quem recebe
    assert.match(html, /data-faixa="legado"[^>]*>Usa a configuração antiga: NF_SEM_ESTOQUE/)
    assert.match(html, /data-faixa="falhando"/)
    assert.match(html, /data-faixa="ninguem"[^>]*>Ninguém recebe: este aviso não está saindo\./)
    assert.match(html, /data-faixa="mac_env"/)
    assert.match(html, /londres, harry potter, heisenberg, cairo sa e mais 1/)
    assert.match(html, /text-red-700 font-medium[^>]*>ninguém</, 'ninguém em vermelho')
    assert.match(html, />o \.env do Mac</, 'o Mac sem lista daqui usa o .env (não é "ninguém")')
    assert.match(html, /última mensagem: Logística — automação parada:/)
    assert.match(html, /1\/2 — falhou: sem crédito \(402\)/)
    // informativo em cinza; Editar só no editável
    assert.match(html, /data-envio="pedidos\.correcao_dia_envio" class="align-top text-muted-foreground bg-muted\/20"/)
    const botoes = [...html.matchAll(/data-envio="([^"]+)"[\s\S]*?>(Editar|Detalhes)<\/button>/g)].map((x) => [x[1], x[2]])
    assert.deepEqual(botoes, [
      ['logistica.vigia_automacao', 'Detalhes'],
      ['devolucoes.prazo_resposta_loja', 'Detalhes'],
      ['devolucoes.codigos_ml_tuta', 'Editar'],
      ['pedidos.correcao_dia_envio', 'Detalhes'],
    ])
    assert.match(html, /<option value="ninguem"[^>]*>Ninguém recebe \(1\)\s*<\/option>/, 'a contagem por situação')

    // os filtros viram parâmetros
    m.estado.painel = 'Devoluções'
    m.estado.situacao = 'ninguem'
    m.estado.ondeRoda = 'servidor'
    await esperar(); await esperar()
    assert.deepEqual(m.chamadas.at(-1).query, { painel: 'Devoluções', situacao: 'ninguem', onde_roda: 'servidor' })
    html = await m.html()
    assert.match(html, /limpar filtros/)
    m.estado.limparFiltros()
    await esperar(); await esperar()
    assert.deepEqual(m.chamadas.at(-1).query, {})

    // abrir a gaveta põe o envio no endereço; fechar tira (e a regra junto)
    m.estado.abrir(VIGIA)
    assert.equal(m.route.query.envio, 'logistica.vigia_automacao')
    m.route.query = { ...m.route.query, regra: 'r1' }
    assert.equal(m.estado.regraMarcada, 'r1')
    m.estado.fechar()
    assert.deepEqual(m.route.query, {})
  }
  {
    // ?pessoa= no endereço vira filtro, com o nome dela
    const m = montar('../pages/envios-threema.vue', {
      query: { pessoa: 'u-1' },
      responder: () => ({ ...LISTA, envios: [VIGIA], pessoa: { id: 'u-1', nome: 'harry potter', tem_threema: true } }),
    })
    await m.montar()
    assert.deepEqual(m.chamadas[0].query, { pessoa: 'u-1' })
    assert.match(await m.html(), /Só os envios de harry potter/)
    m.estado.tirarDoEndereco('pessoa')
    await esperar(); await esperar()
    assert.deepEqual(m.chamadas.at(-1).query, {}, 'tirar a pessoa recarrega tudo')
  }
  {
    // emergência ligada: a caixa fica vermelha e diz quem ligou
    const m = montar('../pages/envios-threema.vue', {
      responder: () => ({ ...LISTA, emergencia: { usar_legado: true, alterado_por: 'Fulano', alterado_em: null, env_desligado: false } }),
    })
    await m.montar()
    const html = await m.html()
    assert.match(html, /border-red-300 bg-red-50/)
    assert.match(html, /LIGADA<span> por Fulano<\/span>: todos os envios usam a configuração antiga\./)
  }

  // ---------------------------------------------------------------- a gaveta
  const DETALHE_VIGIA = { ...VIGIA, destinatarios: [], antes: { fonte: 'x', ativo: true, nomes: [], avisos: [] }, regras: [], emergencia: LISTA.emergencia }
  const LOTES = {
    items: [{
      lote_id: 'l1', criado_em: VIGIA.ultimo.criado_em, origem: 'automatico', onde_roda: 'servidor', via: null, ator: null,
      titulo: 'Logística — automação parada:', escopo: null, contexto: 'logistica', total: 2, entregues: 1, falhas: 1, erro_geral: null,
      destinatarios: [
        { nome: 'londres', id: 'TS…', ok: true, http_status: 200, erro: null },
        { nome: 'harry potter', id: 'TS…', ok: false, http_status: 402, erro: 'http_402' },
      ],
    }],
    mais: false,
  }
  {
    const m = montar('../components/EnviosThreemaGaveta.vue', {
      props: { chave: 'logistica.vigia_automacao', regra: null },
      user: { role: 'admin', historico: true },
      responder: (url) => (url.endsWith('/registros') ? LOTES : DETALHE_VIGIA),
    })
    await esperar(); await esperar()
    assert.deepEqual(m.chamadas.map((c) => c.url), [
      '/api/threema/envios/logistica.vigia_automacao',
      '/api/threema/envios/logistica.vigia_automacao/registros',
    ])
    assert.deepEqual(m.chamadas[1].query, { limit: 50 })
    const html = await m.html()
    assert.match(html, /role="dialog"/)
    assert.match(html, /data-so-leitura[^>]*>Ainda usa a configuração antiga\. Para mudar quem recebe: \.env do servidor/, 'só leitura, com onde mudar')
    assert.match(html, /De onde vem: NF_SEM_ESTOQUE_THREEMA_RECIPIENTS do servidor/)
    assert.match(html, /pill-warning[^>]*>1\/2 receberam/)
    assert.match(html, /harry potter<span class="text-red-700"> \(sem crédito \(402\)\)/)
    assert.match(html, /Ver tudo no Histórico/)
    assert.match(html, /Desligado: os avisos deste período se perdem\./)
    assert.ok(!/TSTL0001/.test(html))
    m.props.chave = null
    await esperar()
    assert.equal(await m.html(), '', 'sem envio, sem gaveta')
  }
  {
    // Item 1: as regras, a marcada pelo endereço; sem Histórico para quem não o vê.
    const ITEM1 = {
      ...DETALHE_VIGIA, chave: 'logistica.mensagem_regra', nome: 'Mensagem da regra de status', interruptor: 'fixo', desligavel: false,
      ativo_texto: 'sempre (o interruptor é a Mensagem de cada regra)', escopo_tipo: 'regra_status',
      regras: [
        { id: 'r1', plataforma: 'Mercado Livre', status_plataforma: 'Extraviado', tem_mensagem: true, mensagem: 'Confira o pedido', usa_padrao: false, antes: { nomes: [{ nome: 'harry potter', tipo: 'usuario' }], avisos: [] }, cadastro: [] },
        { id: 'r2', plataforma: null, status_plataforma: 'Atrasado', tem_mensagem: true, mensagem: 'Aviso geral', usa_padrao: true, antes: { nomes: [], avisos: [] }, cadastro: [] },
      ],
    }
    const m = montar('../components/EnviosThreemaGaveta.vue', {
      props: { chave: 'logistica.mensagem_regra', regra: 'r1' },
      user: { role: 'admin' },
      responder: (url) => (url.endsWith('/registros') ? { items: [], mais: false } : ITEM1),
    })
    await esperar(); await esperar()
    const html = await m.html()
    assert.match(html, /data-regra="r1" class="[^"]*ring-2 ring-primary/, 'a regra do endereço fica marcada')
    assert.match(html, /data-regra="r2" class="rounded border px-3 py-2"/)
    assert.match(html, /Quem recebe: [\s\S]*?harry potter/)
    assert.match(html, /usa o padrão do envio/)
    assert.match(html, /sempre \(o interruptor é a Mensagem de cada regra\)/)
    assert.ok(!/Desligado: /.test(html), 'item 1 não tem desligar')
    assert.ok(!/Ver tudo no Histórico/.test(html))
    assert.match(html, /Nenhum envio registrado/)
  }

  console.log('envios-threema-sfc: ok')
})().catch((e) => { console.error(e); process.exit(1) })
