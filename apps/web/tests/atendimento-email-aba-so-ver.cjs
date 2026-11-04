// node tests/atendimento-email-aba-so-ver.cjs — quem SÓ VÊ a aba E-mail, nas lojas dele
// (04/11/2026, o Eduardo, do marrocos: "dxa ele ver tdo na aba de email dele la as lojas
// sege so as dele"). A API recorta (apps/api/tests/test_atendimento_email_aba_so_ver.py);
// aqui, sem rede e com os componentes de verdade:
//  - `podeLerFilas` carrega e mostra todas as filas, as pastas, as informativas e a saúde;
//  - sem `podeMexer`, nenhuma ação aparece: escolher a loja, vincular, tirar da fila,
//    reprocessar, Saiu/Não saiu, mudar pastas e palavras, Abrir no Tuta;
//  - quem mexe continua com tudo, e quem não tem a permissão continua só com as de conversa;
//  - as seções da aba: nem Caixas (a Central é só de admin) nem Administração.
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


const F = requerer('~/components/AtendimentoMailFilas.vue')
const CONTAGENS = { aguardando: 1, chamados: 0, chamados_por_tipo: {}, por_tipo_email: {} }
const FILAS_DE_EMAIL = ['sem_loja', 'sem_vinculo', 'suspeito', 'resumo', 'erro']

function cartao(id, extra = {}) {
  return {
    id, estado: 'sem_loja', recebido_em: '2026-11-04T10:00:00Z', pasta: 'INBOX', plataforma: 'ml', finalidade: null,
    destaque: false, alias: 'loja@tuta.com', loja: 'Minha Loja', integration_id: 'i1', motivo: 'x', motivo_texto: 'Sem cadastro',
    // A API não manda estes para quem só vê; a tela também não os mostra (defesa em dobro).
    sugestoes: [{ store_info_id: 's9', nome: 'Loja de outro', plataforma: 'ml', integration_id: 'i9', sugestao: true }],
    suspeito: false, suspeito_motivos: [], conversa_id: 'c1', pedido: null,
    pedidos_citados: [{ pedido: '555', existe: true, plataforma: 'ml' }], protocolo: null, alertas: [],
    abrir_no_tuta: 'https://app.tuta.com/x', assunto: `Assunto ${id}`, de: 'cliente@example.invalid', ...extra,
  }
}
function respostasDaAba() {
  return {
    '/api/atendimento/email/contagens': CONTAGENS,
    '/api/atendimento/email/filas': { sem_loja: 2, sem_vinculo: 1, suspeito: 0, resumo: 0, erro: 1, informativo: 0, revisar_envio: 1 },
    '/api/atendimento/email/saude': {
      caixas: [{ mailbox_id: 'b1', nome: 'Geral', visibilidade: 'empresa', estado: 'online', ponte_ligada: true, ponte_desde: null, envio: { ligado: true, modo: 'real' }, envios_a_conferir: 1, pastas_sem_revisar: null, pastas_sem_categoria: null }],
      lojas_sem_caixa_lida: [],
      lojas_sem_integracao: [{ store_info_id: 's1', nome: 'Minha ficha', plataforma: 'ml', endereco: 'a@b.invalid' }],
    },
    '/api/atendimento/email/envios': { itens: [{ id: 'j1', status: 'uncertain', codigo: null, de: 'loja@tuta.com', para: 'cliente@example.invalid', conversa_id: 'c1', mensagem_id: null, criado_em: '2026-11-04T10:00:00Z', concluido_em: null, resolucao: null, resolvido_em: null, revisar: true, lease_vencido: false }] },
    '/api/atendimento/email/emails': (url) => {
      const fila = new URL(url, 'https://example.com').searchParams.get('fila')
      return { itens: [cartao(`${fila}-1`, { estado: fila })], proximo: null }
    },
    '/api/atendimento/conversas': { itens: [], proximo: null },
  }
}
const AS_ACOES = [
  /data-sugestao-loja/, /aria-label="escolher outra loja"/, />\s*ligar\s*</, /vincular ao pedido/, /pedido 555/, /aria-label="número do pedido"/,
  />\s*reprocessar\s*</, /reprocessar a fila/, /não é de loja nenhuma/, /tirar da fila/, /data-resolver-saiu/, /data-resolver-nao-saiu/, /Abrir no Tuta/,
]
function semNenhumaAcao(html, onde) {
  for (const acao of AS_ACOES) assert.doesNotMatch(html, acao, `${onde}: ${acao}`)
}

async function principal() {
  // ------------------------------------------------ as regras puras
  for (const f of F.FILAS) {
    assert.deepEqual(Object.values(F.acoesDaFila(f.value, false, false)).filter(Boolean), [], `${f.value}: nenhuma ação`)
    assert.deepEqual(Object.values(F.acoesDaFila(f.value, true, false)).filter(Boolean), [], `${f.value}: nem com isAdmin`)
  }
  assert.deepEqual(F.acoesDaFila('sem_loja', true), { escolherLoja: true, vincular: false, ignorar: true, reprocessar: true, reprocessarFila: true }, 'quem mexe, como antes')
  assert.deepEqual(F.filasDaPessoa(true).map((f) => f.value), F.FILAS.map((f) => f.value))
  assert.deepEqual(F.filasDaPessoa(false).map((f) => f.value), ['chamados', 'aguardando', 'todas'])
  for (const f of F.FILAS) {
    assert.equal(F.dicaDaFila(f, true), f.dica, 'quem mexe: a dica de sempre')
    assert.doesNotMatch(F.dicaDaFila(f, false), /escolha a loja|vincule ao pedido|reprocesse|e marque/, `${f.value}: sem mandar fazer`)
  }
  assert.match(F.vazioDaConversa('chamados', '', false, true), /ficam com quem administra/)
  assert.equal(F.vazioDaConversa('chamados', '', false), 'Nenhum chamado aberto dos sites.')

  // ------------------------------------------------ as Filas de quem só vê
  {
    const stubs = {
      AtendimentoMailPastas: { props: ['isAdmin'], setup: (p) => () => Vue.h('div', { 'data-pastas-stub': String(p.isAdmin) }) },
      AtendimentoMailInformativos: { props: ['sinal', 'soVe'], setup: (p) => () => Vue.h('div', { 'data-informativos-stub': String(p.soVe) }) },
    }
    const m = montar('../components/AtendimentoMailFilas.vue', { isAdmin: false, lojas: [{ integration_id: 'i1', plataforma: 'ml', conta: 'Minha Loja' }], podeMexer: false, podeLerFilas: true }, { respostas: respostasDaAba(), filhos: stubs })
    await m.montar()
    assert.equal(m.estado.fila, 'sem_loja', 'abre como quem mexe: na primeira fila')
    assert.ok(m.chamadas.some((c) => c.url === '/api/atendimento/email/filas'), 'carrega as contagens')
    assert.ok(m.chamadas.some((c) => c.url === '/api/atendimento/email/saude'), 'carrega a saúde')
    let html = await m.html()
    for (const f of F.FILAS) assert.ok(html.includes(`data-fila="${f.value}"`), `a fila ${f.value}`)
    assert.ok(html.includes('data-fila="pastas"'), 'Pastas e regras')
    assert.match(html, /data-mail-saude/)
    assert.match(html, /1 loja\(s\) do cadastro sem integração/)
    assert.match(html, /data-fila="sem_loja"[^>]*>\s*Sem loja\s*<span[^>]*>2<\/span>/)

    for (const fila of FILAS_DE_EMAIL) {
      m.estado.verFila(fila)
      await Vue.nextTick(); await esperar()
      html = await m.html()
      assert.ok(html.includes(`data-email-fila="${fila}-1"`), `${fila}: mostra o e-mail`)
      assert.ok(html.includes(`Assunto ${fila}-1`))
      assert.match(html, /ver o e-mail/, 'ler continua')
      assert.match(html, /abrir a conversa/, 'abrir a conversa dele continua')
      semNenhumaAcao(html, fila)
    }
    // Revisar envio: a linha aparece, Saiu/Não saiu não.
    m.estado.verFila('revisar_envio')
    await Vue.nextTick(); await esperar()
    html = await m.html()
    assert.ok(html.includes('data-envio="j1"'))
    assert.match(html, /quem administra confere nos Enviados do Tuta/)
    assert.doesNotMatch(html, /confira nos Enviados do Tuta e marque/)
    semNenhumaAcao(html, 'revisar_envio')

    // As pastas e regras: sem o poder de mudar.
    m.estado.verFila('sem_loja')
    m.estado.pastasAbertas = true
    html = await m.html()
    assert.ok(html.includes('data-pastas-stub="false"'))

    // O chip Informativos: as pastas, sem o que é de quem administra.
    m.estado.pastasAbertas = false
    m.estado.escolherTipoEmail('informativo')
    await esperar()
    html = await m.html()
    assert.ok(html.includes('data-informativos-stub="true"'))
    assert.equal(m.perguntas.length, 0)
    assert.deepEqual(m.chamadas.filter((c) => c.metodo !== 'GET'), [], 'nenhuma escrita')
  }

  // ------------------------------------------------ quem mexe continua com tudo
  {
    const m = montar('../components/AtendimentoMailFilas.vue', { isAdmin: true, lojas: [], podeMexer: true }, { respostas: respostasDaAba() })
    await m.montar()
    let html = await m.html()
    for (const acao of [/data-sugestao-loja/, /aria-label="escolher outra loja"/, /reprocessar a fila/, /não é de loja nenhuma/, /Abrir no Tuta/]) assert.match(html, acao)
    m.estado.verFila('revisar_envio')
    await Vue.nextTick(); await esperar()
    html = await m.html()
    assert.match(html, /data-resolver-saiu/)
    assert.match(html, /confira nos Enviados do Tuta e marque/)
  }

  // ------------------------------------------------ quem não tem a permissão: como antes
  {
    const m = montar('../components/AtendimentoMailFilas.vue', { isAdmin: false, lojas: [], podeMexer: false }, { respostas: respostasDaAba() })
    await m.montar()
    assert.equal(m.estado.fila, 'chamados')
    assert.ok(!m.chamadas.some((c) => c.url.includes('/email/filas') || c.url.includes('/email/saude')), 'não pergunta o que a rota recusa')
    const html = await m.html()
    assert.ok(!html.includes('data-fila="sem_loja"') && !html.includes('data-fila="pastas"'))
    assert.match(html, /Nenhum chamado aberto dos sites\./)
  }

  // ------------------------------------------------ as pastas informativas
  {
    const respostas = {
      '/api/atendimento/email/informativos': { itens: [{ pasta_id: 'p1', nome: 'Avisos', mailbox_id: 'b1', caixa: 'Geral', total: 1, ultimo_em: '2026-11-04T10:00:00Z', lojas: ['Minha Loja'] }] },
      '/api/atendimento/email/emails': { itens: [cartao('i-1', { estado: 'informativo' })], proximo: null },
    }
    const so = montar('../components/AtendimentoMailInformativos.vue', { soVe: true }, { respostas })
    await so.montar()
    so.estado.abrirItem(so.estado.itens[0])
    await esperar()
    const html = await so.html()
    assert.ok(html.includes('data-linha-informativa="i-1"'))
    assert.match(html, /ver o e-mail/)
    assert.doesNotMatch(html, /Abrir no Tuta/)
    const mexe = montar('../components/AtendimentoMailInformativos.vue', {}, { respostas })
    await mexe.montar()
    mexe.estado.abrirItem(mexe.estado.itens[0])
    await esperar()
    assert.match(await mexe.html(), /Abrir no Tuta/)
    const vazio = montar('../components/AtendimentoMailInformativos.vue', { soVe: true }, { respostas: { '/api/atendimento/email/informativos': { itens: [] } } })
    await vazio.montar()
    const vazia = await vazio.html()
    assert.match(vazia, /Nenhum e-mail informativo das suas lojas\./)
    assert.doesNotMatch(vazia, /escolha a categoria/)
  }

  // ------------------------------------------------ pastas e regras
  {
    const respostas = {
      '/api/atendimento/email/pastas': {
        itens: [{ id: 'p1', mailbox_id: 'b1', chave: 'k', nome: 'Avisos', caminho: 'Avisos', tipo: 'pessoal', plataforma: 'ml', finalidade: null, marca: null, ler: 'corpo', plataforma_manual: null, finalidade_manual: null, ignorar: false, revisada: false, vista_em: null, sumiu_em: null, categoria: null, categoria_sugerida: 'venda', travada: false, total_30d: 2, guardados: 1 }],
        caixas: [{ id: 'b1', nome: 'Geral' }], plataformas: ['ml'], finalidades: ['vendas'], categorias: [], reler_dias: 7,
      },
      '/api/atendimento/email/regras': { itens: [{ id: 'r1', tipo: 'plataforma', palavra: 'ml', valor: 'ml', ativa: true }] },
    }
    const ACOES = [/data-salvar-pasta/, /data-categoria-pasta/, /data-confirmar-pasta/, /data-usar-sugestao/, /data-alternar-regra/, /data-regra-nova/, /aria-label="plataforma da pasta"/]
    const so = montar('../components/AtendimentoMailPastas.vue', { isAdmin: false }, { respostas })
    await so.montar()
    const html = await so.html()
    assert.ok(html.includes('data-pasta="p1"') && html.includes('data-regra="plataforma:ml"'), 'mostra as pastas e as palavras')
    assert.match(html, /Só admin muda — aqui você só vê\./)
    assert.match(html, /· 2 em 30 dias/)
    for (const acao of ACOES) assert.doesNotMatch(html, acao, String(acao))
    const ad = montar('../components/AtendimentoMailPastas.vue', { isAdmin: true }, { respostas })
    await ad.montar()
    const htmlAdmin = await ad.html()
    for (const acao of ACOES) assert.match(htmlAdmin, acao, String(acao))
  }

  // ------------------------------------------------ as seções da aba
  {
    const filhos = { AtendimentoMailFilas: { props: ['isAdmin', 'lojas', 'podeMexer', 'podeLerFilas'], setup: (p) => () => Vue.h('div', { 'data-filas-stub': '', 'data-pode-mexer': String(p.podeMexer), 'data-pode-ler': String(p.podeLerFilas) }) } }
    const so = montar('../components/AtendimentoMail.vue', { isAdmin: false, canOperate: false, canReadQueues: true, canView: true, stores: [], waiting: 2 }, {
      respostas: { '/api/mail/mailboxes': Object.assign(new Error('403'), { status: 403 }) },
      filhos,
    })
    await so.estado.loadMailboxes()
    const html = await so.html()
    assert.equal(so.estado.section, 'queues')
    assert.match(html, /data-filas-stub data-pode-mexer="false" data-pode-ler="true"/)
    assert.doesNotMatch(html, />\s*Caixas\s*</, 'a Central é só de admin')
    assert.doesNotMatch(html, /data-mail-admin-tab/)
    assert.match(html, /data-mail-filas-descricao[^>]*>[^<]*Aqui você só vê\./)
    assert.doesNotMatch(html, /Cadastrar caixa/)
    const mexe = montar('../components/AtendimentoMail.vue', { isAdmin: true, canOperate: true, stores: [] }, { respostas: { '/api/mail/mailboxes': { items: [] } }, filhos })
    const htmlMexe = await mexe.html()
    assert.match(htmlMexe, />\s*Caixas\s*</)
    assert.match(htmlMexe, /data-mail-admin-tab/)
  }

  // ------------------------------------------------ a página
  {
    const pagina = web('pages/atendimento.vue')
    assert.match(pagina, /const leFilasEmail = computed\(\(\) => \(mexe\.value && ehAdmin\.value\) \|\| \(!ehAdmin\.value && podeVerAbaEmail\.value\)\)/)
    assert.match(pagina, /:can-operate="mexe && ehAdmin"\s+:can-read-queues="leFilasEmail"/)
    // A conversa de e-mail aberta por cima da aba: vincular continua de admin.
    assert.match(pagina, /:pode-mexer="ehAdmin && mexe"/)
    const filas = web('components/AtendimentoMailFilas.vue')
    assert.match(filas, /<AtendimentoMailPastas v-if="pastasAbertas" :is-admin="isAdmin && mexe" \/>/)
    assert.match(web('components/AtendimentoMail.vue'), /:pode-mexer="!!canOperate"\s+:pode-ler-filas="!!canReadQueues"/)
  }
  console.log('ok — atendimento-email-aba-so-ver: filas, pastas, informativas e saúde para ver; nenhuma ação; quem mexe e quem não tem a permissão como antes; sem Caixas nem Administração')
}

principal().catch((erro) => {
  console.error(erro)
  process.exit(1)
})
