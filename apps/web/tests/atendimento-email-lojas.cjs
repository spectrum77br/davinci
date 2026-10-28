// node tests/atendimento-email-lojas.cjs — os E-MAILS de cada loja na barra de
// lojas da Caixa (27/10/2026). O Eduardo: "botar um AMPLIÁVEL que vai abrir os
// e-mails embaixo de cada loja"; no mesmo dia, embaixo de cada loja as PASTAS
// do Tuta que ele unificou — Problemas, Envios, Devolução e Informativo (UMA
// constante da API, GRUPOS_EMAIL_NA_BARRA_DE_LOJAS, que chega em
// `grupos_na_barra` com o status de cada pasta) —, cada caixa abre e fecha
// sozinha, TODA loja tem as caixas (a vazia diz por quê) e a aba E-mail é só
// de admin (e, desde 28/10/2026, de quem tem a permissão `atendimento_email`
// view). Sem rede e sem e-mail de verdade:
//  - as funções puras (as caixas de cada linha da barra, o número e a bolinha,
//    o porquê da caixa vazia, as lojas sem linha de chat, as URLs — nunca a
//    lista de todas as lojas);
//  - a barra renderizada (Vue SSR, setup de verdade): a seta em toda loja (não
//    no site), as quatro caixas, a lista curta só na caixa aberta, a ficha sem
//    integração como linha comum, recolhida, a Caixa Humano, o celular;
//  - a lista curta da caixa e a lista da loja (setup de verdade, API falsa):
//    a rota da caixa com a loja e a pasta, o e-mail da conversa abre a conversa,
//    o sem conversa vai para a área principal (a linha acesa, nunca aberto na
//    lista), o porquê da vazia, o tique;
//  - o e-mail da loja aberto na área principal (27/10/2026, o Eduardo: "o
//    e-mail tem que abrir ali na box principal, não ali na esquerda"): o
//    cabeçalho, o texto inteiro, o código, os anexos pela rota da caixa, o
//    "fechar";
//  - a página: a aba E-mail só para admin (ou a permissão), as caixas e as lojas abertas
//    lembradas POR PESSOA (localStorage com try), a barra, a lista e a conversa
//    ligadas; o chat guardado.
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
const transpileTs = (source) => ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText

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
const web = (rel) => fs.readFileSync(path.resolve(__dirname, '..', rel), 'utf8')
const semComentarios = (html) => html.replace(/<!--[\s\S]*?-->/g, '')
const esperar = () => new Promise((r) => setTimeout(r, 0))
const ICONE = { props: ['plataforma'], render() { return Vue.h('i', { 'data-icone': this.plataforma }) } }

// O setup de verdade fora de um app (os `watch` ficam vivos), o HTML de um
// app SSR com o MESMO estado — o molde de tests/atendimento-mail-atendimento.cjs.
function montar(rel, props, { respostas = {}, filhos = {} } = {}) {
  const { descriptor, filename } = sfc(rel)
  const script = compileScript(descriptor, { id: path.basename(rel), inlineTemplate: false })
  const tpl = compileTemplate({ source: descriptor.template.content, filename, id: path.basename(rel), compilerOptions: { isTS: true, bindingMetadata: script.bindings } })
  const chamadas = []
  const avisos = []
  const emitidos = []
  const montados = []
  const responder = async (url, opts = {}) => {
    chamadas.push({ url, metodo: opts.method || 'GET' })
    for (const [chave, valor] of Object.entries(respostas)) {
      if (url === chave || url.startsWith(`${chave}?`)) {
        let v = typeof valor === 'function' ? valor(url, opts) : valor
        // Uma promessa: a resposta que demora (a ordem de chegada importa).
        if (v && typeof v.then === 'function') v = await v
        if (v instanceof Error) throw v
        return structuredClone(v)
      }
    }
    throw new Error(`rota inesperada: ${url}`)
  }
  const g = {
    ref: Vue.ref, computed: Vue.computed, reactive: Vue.reactive, watch: Vue.watch, nextTick: Vue.nextTick,
    onMounted: (fn) => montados.push(fn), onBeforeUnmount: () => {},
    useApi: () => ({ url: (v) => v, api: responder }),
    useToasts: () => ({ success: (t) => avisos.push(['ok', t]), error: (t) => avisos.push(['erro', t]), info: (t) => avisos.push(['info', t]) }),
  }
  const nomes = Object.keys(g)
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', ...nomes, transpile(script.content))(requerer, mod, mod.exports, ...nomes.map((n) => g[n]))
  const tmod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(tpl.code))(requerer, tmod, tmod.exports)
  const comp = mod.exports.default
  const reativo = Vue.reactive({ ...props })
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
    a.component('AtendimentoIconePlataforma', ICONE)
    for (const [n, c] of Object.entries(filhos)) a.component(n, c)
    return semComentarios(await renderToString(a))
  }
  return {
    html, estado, chamadas, avisos, emitidos, reativo,
    async montar() { for (const fn of montados) await fn(); await esperar() },
  }
}

// A barra renderizada como nos outros testes dela (compileScript inline).
const lojasSfc = sfc('../components/AtendimentoLojas.vue')
async function renderizarBarra(props) {
  const { content } = compileScript(lojasSfc.descriptor, { id: 'barra', inlineTemplate: true })
  const mod = {}
  const g = { computed: Vue.computed, ref: Vue.ref, watch: Vue.watch, nextTick: Vue.nextTick }
  const nomes = Object.keys(g)
  new Function('exports', 'require', ...nomes, transpile(content))(mod, requerer, ...nomes.map((n) => g[n]))
  const app = Vue.createSSRApp(mod.default, props)
  app.config.warnHandler = () => {}
  app.component('AtendimentoIconePlataforma', ICONE)
  app.component('AtendimentoLojaGrupoEmails', CAIXA)
  return semComentarios(await renderToString(app))
}

const LJ = requerer('~/components/AtendimentoLojas.vue')
const LE = requerer('~/components/AtendimentoLojaEmails.vue')
const LG = requerer('~/components/AtendimentoLojaGrupoEmails.vue')
const EA = requerer('~/components/AtendimentoEmailDaLojaAberto.vue')

const conta = (emails, aguardando = 0, abertas = 0, total = abertas) => ({ emails, abertas, aguardando, total })
// As pastas da barra (a API manda: a escolha do dono mora lá), com o status de cada uma na ponte.
const GRUPOS = [
  { chave: 'problemas', rotulo: 'Problemas', pastas: ['problemas'], status: 'liga' },
  { chave: 'envios', rotulo: 'Envios', pastas: ['envio'], status: 'so_contar' },
  { chave: 'devolucao', rotulo: 'Devolução', pastas: ['devoluçoes'], status: 'liga' },
  { chave: 'informativo', rotulo: 'Informativo', pastas: ['informativo'], status: 'sem_pasta' },
]
const POR_LOJA = [
  { integration_id: 'i-sh', store_info_id: null, plataforma: 'shopee', loja: 'ATV', alias: 'na_ponte', emails_30d: 300, grupos: { problemas: conta(31, 2, 5), devolucao: conta(4) } },
  // Sem e-mail nenhum e sem endereço no cadastro: as caixas vazias dizem por quê.
  { integration_id: 'i-ml', store_info_id: null, plataforma: 'ml', loja: 'Poofy', alias: 'sem_cadastro', emails_30d: 0, grupos: {} },
  // A loja do robô (sem integração): pela ficha de mesma plataforma e mesmo nome.
  { integration_id: null, store_info_id: 'f-temu', plataforma: 'temu', loja: 'Barbosa', alias: 'na_ponte', emails_30d: 40, grupos: { problemas: conta(40) } },
  // Duas fichas "Eron" na Temu: ambígua — a linha do robô com as caixas vazias; as duas viram linhas próprias.
  { integration_id: null, store_info_id: 'f-eron1', plataforma: 'temu', loja: 'eron', alias: 'na_ponte', emails_30d: 3, grupos: { envios: conta(3) } },
  { integration_id: null, store_info_id: 'f-eron2', plataforma: 'temu', loja: 'Éron', alias: 'fora_da_caixa', emails_30d: 0, grupos: {} },
  // A ficha sem integração numa plataforma com integração (a "luno" da Shopee): linha própria.
  { integration_id: null, store_info_id: 'f-luno', plataforma: 'shopee', loja: 'luno', alias: 'na_ponte', emails_30d: 233, grupos: { problemas: conta(12) } },
]
const RESUMO = {
  plataformas: [{ plataforma: 'shopee', aguardando: 5 }, { plataforma: 'ml', aguardando: 0 }, { plataforma: 'temu', aguardando: 0 }, { plataforma: 'site', aguardando: 0 }],
  lojas: [
    { integration_id: 'i-sh', plataforma: 'shopee', conta: 'ATV', aguardando: 5, vencidas: 0, nao_lidas_duoke: 71 },
    { integration_id: 'i-sh2', plataforma: 'shopee', conta: 'Aguiar', aguardando: 0, vencidas: 0 },
    { integration_id: 'i-ml', plataforma: 'ml', conta: 'Poofy', aguardando: 0, vencidas: 0 },
    { integration_id: null, plataforma: 'temu', conta: 'barbosa', aguardando: 0, vencidas: 0 },
    { integration_id: null, plataforma: 'temu', conta: 'Eron', aguardando: 0, vencidas: 0 },
    { integration_id: null, plataforma: 'site', externo_ref: 'site:charlots', conta: 'Charlots', aguardando: 0, vencidas: 0 },
  ],
  canais: [],
  flags: {},
}
const filtros = (o = {}) => ({ plataforma: '', integration_id: '', canal: '', filtro: 'todas', q: '', externo_ref: '', rede_social_id: '', plataforma_topo: '', ...o })
// A caixa aberta na barra: um dublê (a lista curta é o AtendimentoLojaGrupoEmails, testado abaixo).
const CAIXA = { props: ['escolha', 'total', 'motivos', 'tique'], render() { return Vue.h('div', { 'data-stub-caixa': this.escolha?.grupo, 'data-total': this.total }, (this.motivos || []).join(' | ')) } }

async function principal() {
  // ------------------------------------------------ as funções puras da barra
  {
    const linha = (o) => ({ chave: o.integration_id || `semid:${o.plataforma}:${o.conta}`, externo_ref: '', rede_social_id: '', ...o })
    // QUAIS pastas aparecem vem da API, na ordem dela (sem repetir; a API antiga: nenhuma).
    assert.deepEqual(LJ.gruposDaBarra(GRUPOS).map((g) => g.rotulo), ['Problemas', 'Envios', 'Devolução', 'Informativo'])
    assert.deepEqual(LJ.gruposDaBarra([GRUPOS[0], GRUPOS[0], { chave: '' }]).map((g) => g.chave), ['problemas'])
    assert.deepEqual(LJ.gruposDaBarra(null), [])
    // Pela integração.
    const atv = LJ.emailsDaLoja(POR_LOJA, linha({ integration_id: 'i-sh', plataforma: 'shopee', conta: 'ATV' }))
    assert.equal(atv.integration_id, 'i-sh')
    // TODA loja tem as caixas: a que a API não mandou ganha as caixas vazias (sem o porquê da loja).
    assert.deepEqual(LJ.emailsDaLoja(POR_LOJA, linha({ integration_id: 'i-sh2', plataforma: 'shopee', conta: 'Aguiar' })), { integration_id: 'i-sh2', store_info_id: null, plataforma: 'shopee', loja: 'Aguiar', alias: null, grupos: {} })
    assert.equal(LJ.emailsDaLoja(null, linha({ integration_id: 'i-sh', plataforma: 'shopee', conta: 'ATV' })), null, 'sem a leitura: sem caixas')
    // A loja do robô: a ficha de mesma plataforma e mesmo nome (sem acento/caixa).
    assert.equal(LJ.emailsDaLoja(POR_LOJA, linha({ integration_id: '', plataforma: 'temu', conta: ' barbosa ' })).store_info_id, 'f-temu')
    assert.equal(LJ.emailsDaLoja(POR_LOJA, linha({ integration_id: '', plataforma: 'temu', conta: 'Eron' })).store_info_id, null, 'duas fichas iguais: nunca a errada')
    // O site e as contas de rede não são loja de e-mail.
    assert.equal(LJ.emailsDaLoja(POR_LOJA, linha({ integration_id: '', plataforma: 'site', conta: 'Charlots', externo_ref: 'site:charlots' })), null)
    assert.equal(LJ.emailsDaLoja(POR_LOJA, linha({ integration_id: '', plataforma: 'instagram', conta: 'x', rede_social_id: 'r1' })), null)
    // As lojas SEM linha de chat: a "luno" e as duas "Eron" (nunca a ATV nem a Barbosa da Temu).
    const linhas = [linha({ integration_id: 'i-sh', plataforma: 'shopee', conta: 'ATV' }), linha({ integration_id: 'i-ml', plataforma: 'ml', conta: 'Poofy' }), linha({ integration_id: '', plataforma: 'temu', conta: 'Barbosa' }), linha({ integration_id: '', plataforma: 'temu', conta: 'Eron' })]
    assert.deepEqual(LJ.lojasSemLinha(POR_LOJA, linhas).map((e) => e.store_info_id || e.integration_id), ['f-eron1', 'f-eron2', 'f-luno'])
    assert.equal(LJ.chaveSoEmail({ integration_id: null, store_info_id: 'f-luno' }), 'email:f:f-luno')
    assert.equal(LJ.chaveSoEmail({ integration_id: 'i-x', store_info_id: null }), 'email:i:i-x')
    // O número da caixa = os e-mails dela nos últimos dias; a bolinha = as conversas esperando.
    assert.deepEqual(LJ.gruposDaBarra(GRUPOS).map((g) => LJ.numeroDoGrupo(atv, g.chave)), [31, 0, 4, 0])
    assert.equal(LJ.esperandoDoGrupo(atv, 'problemas'), 2)
    assert.equal(LJ.esperandoDoGrupo(atv, 'devolucao'), 0)
    assert.equal(LJ.numeroDoGrupo(null, 'problemas'), 0)
    // O PORQUÊ da caixa vazia: a pasta (a mesma para toda loja) e, depois, a loja.
    const [prob, envio, , info] = GRUPOS
    assert.deepEqual(LJ.motivosDaCaixaVazia(info, atv), ['A pasta “informativo” ainda não chegou na ponte: o Tuta ainda não mandou essa pasta.'])
    const poofy = POR_LOJA[1]
    assert.deepEqual(LJ.motivosDaCaixaVazia(envio, poofy), [
      'A pasta “envio” ainda não está configurada: a ponte só conta os e-mails dela (E-mail › Pastas).',
      'Nenhum e-mail ligado a esta loja — o endereço/alias da loja não está na ponte (Cadastros › Lojas, campo e-mail).',
    ])
    assert.deepEqual(LJ.motivosDaCaixaVazia(prob, atv), ['Nenhum e-mail nesta pasta nos últimos 30 dias (a loja recebeu 300 em outras pastas).'])
    assert.deepEqual(LJ.motivosDaCaixaVazia(prob, { ...atv, emails_30d: 0 }, 7), ['Nenhum e-mail ligado a esta loja nos últimos 7 dias.'])
    assert.deepEqual(LJ.motivosDaCaixaVazia(prob, POR_LOJA[4]), ['Nenhum e-mail ligado a esta loja — o e-mail do cadastro não está em nenhuma caixa do Tuta lida pela ponte.'])
    assert.deepEqual(LJ.motivosDaCaixaVazia(prob, { integration_id: 'x', alias: null, grupos: {} }), ['Nenhum e-mail nesta pasta nos últimos 30 dias.'])
    assert.deepEqual(LJ.motivosDaCaixaVazia({ ...prob, status: 'sem_categoria' }, atv).length, 1)
    assert.match(LJ.motivosDaCaixaVazia({ ...prob, status: 'outra_categoria' }, atv)[0], /é de Chamado/)
    assert.equal(LJ.tituloDoAmpliavel(atv, 'ATV', false, GRUPOS), 'Mostrar os e-mails da loja ATV (Problemas 31 · Envios 0 · Devolução 4 · Informativo 0)')
    assert.match(LJ.tituloDoAmpliavel(atv, 'ATV', true, GRUPOS), /^Esconder/)
    assert.equal(LJ.tituloDoGrupo(atv, prob, 'ATV'), 'Problemas · ATV: 31 e-mail(s) nos últimos 30 dias · 2 esperando resposta\nAbre a lista da loja aqui na Caixa')
    assert.match(LJ.tituloDoGrupo(poofy, envio, 'Poofy'), /0 e-mail\(s\)[\s\S]*ainda não está configurada[\s\S]*não está na ponte/)
    // O clique: a loja (integração OU ficha, nunca as duas) e o grupo.
    assert.deepEqual(LJ.escolhaDoEmail(linha({ integration_id: 'i-sh', plataforma: 'shopee', conta: 'ATV' }), atv, 'problemas'), { chave: 'i-sh', integration_id: 'i-sh', store_info_id: '', plataforma: 'shopee', conta: 'ATV', grupo: 'problemas' })
    const temu = LJ.emailsDaLoja(POR_LOJA, linha({ integration_id: '', plataforma: 'temu', conta: 'barbosa' }))
    assert.deepEqual(LJ.escolhaDoEmail(linha({ integration_id: '', plataforma: 'temu', conta: 'barbosa' }), temu, 'envios'), { chave: 'semid:temu:barbosa', integration_id: '', store_info_id: 'f-temu', plataforma: 'temu', conta: 'barbosa', grupo: 'envios' })
    assert.deepEqual(LJ.alternarAberta([], 'a'), ['a'])
    assert.deepEqual(LJ.alternarAberta(['a', 'b'], 'a'), ['b'])
    assert.equal(LJ.chaveDoGrupoAberto('semid:temu:barbosa', 'envios'), 'semid:temu:barbosa|envios')
    // A lista curta e a lista da loja: a rota da caixa, com a loja (NUNCA todas as lojas).
    assert.equal(LG.urlDosEmailsDaCaixa({ integration_id: 'i-sh', store_info_id: '', grupo: 'problemas' }, { limite: 5 }), '/api/atendimento/email/lojas/emails?grupo=problemas&integration_id=i-sh&limite=5')
    assert.equal(LG.urlDosEmailsDaCaixa({ integration_id: '', store_info_id: 'f-temu', grupo: 'envios' }, { antesDe: 'x' }), '/api/atendimento/email/lojas/emails?grupo=envios&store_info_id=f-temu&antes_de=x')
    assert.equal(LG.urlDosEmailsDaCaixa({ integration_id: '', store_info_id: '', grupo: 'envios' }), null)
    assert.deepEqual(LG.marcasDoEmail({ seguranca: true, conversa_id: null }), ['código'])
    assert.deepEqual(LG.marcasDoEmail({ conversa_id: 'c1', aguardando_resposta: true }), ['falta responder'])
    assert.deepEqual(LG.marcasDoEmail({ conversa_id: 'c1', aguardando_resposta: false }), ['conversa'])
    assert.equal(LE.urlDoEmailDaCaixa('m 1'), '/api/atendimento/email/lojas/emails/m%201')
    assert.equal(LE.urlDoAnexoDaCaixa('a1'), '/api/atendimento/email/lojas/anexos/a1')
    assert.notEqual(LE.chaveDaEscolha({ chave: 'i-sh', grupo: 'problemas' }), LE.chaveDaEscolha({ chave: 'i-sh', grupo: 'envios' }))
    assert.equal(LE.tituloDaLinha({ assunto: '', comprador_nome: null, de: 'a@b.c' }), 'a@b.c')
    // O e-mail aberto na área principal: quem mandou, os links tirados, o cabeçalho.
    assert.equal(EA.remetenteDoEmail({ de: 'avisos@ml.com', de_nome: 'Mercado Livre' }), 'Mercado Livre <avisos@ml.com>')
    assert.equal(EA.remetenteDoEmail({ de: 'avisos@ml.com', de_nome: 'avisos@ml.com' }), 'avisos@ml.com')
    assert.equal(EA.remetenteDoEmail({ de: 'avisos@ml.com', de_nome: null }), 'avisos@ml.com')
    assert.equal(EA.remetenteDoEmail(null), '')
    assert.equal(EA.textoDosLinksRemovidos(2), '2 link(s) de acesso removido(s)')
    assert.equal(EA.textoDosLinksRemovidos(0), '')
    const linhaM = { id: 'm1', assunto: 'Etiqueta da devolução', de: 'avisos@ml.com', recebido_em: '2026-10-27T10:00:00Z', pasta: 'devoluçoes', seguranca: true }
    assert.deepEqual(EA.cabecalhoDoEmail(null, linhaM), { assunto: 'Etiqueta da devolução', de: 'avisos@ml.com', recebido_em: '2026-10-27T10:00:00Z', pasta: 'devoluçoes', seguranca: true }, 'carregando: a linha da lista')
    assert.deepEqual(EA.cabecalhoDoEmail({ id: 'm1', assunto: 'Etiqueta (lida)', de: 'avisos@ml.com', de_nome: 'ML', recebido_em: '2026-10-27T11:00:00Z', pasta: 'devoluçoes', seguranca: false, texto: 'x' }, linhaM), { assunto: 'Etiqueta (lida)', de: 'ML <avisos@ml.com>', recebido_em: '2026-10-27T11:00:00Z', pasta: 'devoluçoes', seguranca: false }, 'lido: o e-mail manda')
    assert.equal(EA.cabecalhoDoEmail({ id: 'x', texto: null, de: null }, null).assunto, '(sem assunto)')
  }

  // ------------------------------------------------ a barra renderizada
  {
    const base = { resumo: RESUMO, filtros: filtros(), numero: 'nao_lidas_duoke' }
    const com = (o = {}) => ({ ...base, emails: POR_LOJA, gruposEmail: GRUPOS, diasEmail: 30, ...o })
    const sem = await renderizarBarra(base)
    // Sem `emails` (a Caixa Humano, a API antiga): nenhuma seta nem linha de e-mail — e o MESMO HTML com `emails: null`.
    assert.doesNotMatch(sem, /data-ampliar-emails|data-emails-loja|data-so-email|data-email-grupo/)
    assert.equal(await renderizarBarra({ ...base, emails: null, gruposEmail: GRUPOS }), sem)
    // Sem as pastas (a API antiga): nenhuma seta.
    assert.doesNotMatch(await renderizarBarra(com({ gruposEmail: [] })), /data-ampliar-emails/)
    // Com os números: a seta em TODA loja (mesmo sem e-mail) — nunca no site.
    let html = await renderizarBarra(com())
    assert.deepEqual([...html.matchAll(/data-ampliar-emails="([^"]+)"/g)].map((m) => m[1]).sort(), ['i-ml', 'i-sh', 'i-sh2', 'semid:temu:Eron', 'semid:temu:barbosa'])
    assert.match(html, /aria-expanded="false" aria-label="Mostrar os e-mails da loja ATV \(Problemas 31 · Envios 0 · Devolução 4 · Informativo 0\)"/)
    assert.doesNotMatch(html, /aria-controls="emails-loja-i-sh"/, 'fechada: o aria-controls não aponta para o que não existe')
    assert.match((html.match(/<button[^>]*data-ampliar-emails="i-sh"[^>]*>/) || [''])[0], /focus-visible:ring-2/)
    assert.doesNotMatch(html, /data-emails-loja=/, 'fechada: sem as caixas')
    // A linha da loja continua igual (o número do Duoke, o clique filtra o chat); só abre espaço para a seta.
    assert.match(html, /<button type="button" class="relative mx-1 [^"]*pr-7"[^>]*title="Shopee · ATV[^"]*">[\s\S]*?>71<\/span><\/button>/)
    // As lojas sem linha de chat (as fichas): no grupo da plataforma, como as outras (sem o cinza, sem número de chat).
    // A ficha sem e-mail nas caixas (f-eron2) não vira linha (27/10/2026).
    assert.deepEqual([...html.matchAll(/data-so-email="([^"]+)"/g)].map((m) => m[1]).sort(), ['email:f:f-eron1', 'email:f:f-luno'])
    const luno = (html.match(/<button[^>]*data-so-email="email:f:f-luno"[\s\S]*?<\/button>/) || [''])[0]
    assert.match(luno, /aria-expanded="false"/)
    assert.match(luno, /title="Shopee · luno\nSem chat no DaVinci \(a loja não tem integração ligada\): o clique mostra as pastas de e-mail dela\."/)
    assert.match(luno, />luno<\/span>[\s\S]*data-icone="ChevronRight"/)
    assert.doesNotMatch(luno, /aria-pressed|bg-red-500|text-muted-foreground|data-icone="Mail"/)
    const shopee = html.slice(html.indexOf('data-grupo="shopee"'), html.indexOf('data-grupo="ml"'))
    assert.match(shopee, /Shopee · Aguiar[\s\S]*Shopee · ATV[\s\S]*Shopee · luno/)
    // Aberta: as quatro caixas, na ordem da API, com o número; a bolinha só onde há e-mail esperando.
    html = await renderizarBarra(com({ emailsAbertos: ['i-sh'] }))
    assert.match(html, /aria-expanded="true" aria-controls="emails-loja-i-sh"/)
    const grupo = (html.match(/<div id="emails-loja-i-sh" role="group" aria-label="E-mails da loja ATV"[\s\S]*?<\/div>/) || [''])[0]
    assert.ok(grupo, 'as caixas embaixo da loja')
    const caixas = [...grupo.matchAll(/data-email-grupo="([^"]+)"[^>]*>([\s\S]*?)<\/button>/g)].map((m) => [m[1], m[2].replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()])
    assert.deepEqual(caixas, [['problemas', 'Problemas 2 31'], ['envios', 'Envios 0'], ['devolucao', 'Devolução 4'], ['informativo', 'Informativo 0']])
    assert.equal((grupo.match(/data-email-esperando/g) || []).length, 1)
    assert.match(grupo, /aria-expanded="false" title="Problemas · ATV: 31 e-mail\(s\) nos últimos 30 dias · 2 esperando resposta/)
    assert.doesNotMatch(html, /data-stub-caixa/, 'caixa fechada: a lista curta nem monta')
    // Uma caixa aberta: só ela monta a lista curta (com o total e o porquê), lembrada pela chave loja|pasta.
    html = await renderizarBarra(com({ emailsAbertos: ['i-sh', 'i-ml'], gruposAbertos: ['i-sh|devolucao', 'i-ml|envios'] }))
    assert.match(html, /aria-expanded="true" aria-controls="emails-loja-i-sh-devolucao"[^>]*data-email-grupo="devolucao"/)
    assert.deepEqual([...html.matchAll(/data-stub-caixa="([^"]+)" data-total="([^"]+)"/g)].map((m) => [m[1], m[2]]), [['devolucao', '4'], ['envios', '0']])
    assert.match(html, /id="emails-loja-i-ml-envios"[^>]*>A pasta “envio” ainda não está configurada[^<]*\| Nenhum e-mail ligado a esta loja — o endereço\/alias da loja não está na ponte/)
    // A escolha do dono é da API: com duas pastas, só elas.
    html = await renderizarBarra(com({ emailsAbertos: ['i-sh'], gruposEmail: GRUPOS.slice(0, 2) }))
    assert.deepEqual([...html.matchAll(/data-email-grupo="([^"]+)"/g)].map((m) => m[1]), ['problemas', 'envios'])
    // A loja do robô aberta (a chave tem ":"): o id é seguro.
    html = await renderizarBarra(com({ emailsAbertos: ['semid:temu:barbosa'] }))
    assert.match(html, /id="emails-loja-semid_temu_barbosa"/)
    // A ficha (sem chat) aberta: o próprio clique abriu, as caixas embaixo.
    html = await renderizarBarra(com({ emailsAbertos: ['email:f:f-luno'] }))
    assert.match(html, /aria-expanded="true" aria-controls="emails-loja-email_f_f-luno"[^>]*data-so-email="email:f:f-luno"/)
    assert.match(html, /id="emails-loja-email_f_f-luno" role="group"[\s\S]*?data-email-grupo="problemas"[\s\S]*?>12<\/span>/)
    // Recolhida: não cabe — sem seta nem caixas (a lembrança fica), sem as fichas.
    html = await renderizarBarra(com({ emailsAbertos: ['i-sh'], recolhida: true }))
    assert.doesNotMatch(html, /data-ampliar-emails|data-emails-loja|data-so-email/)
    // Recolhida com os e-mails de uma loja na Caixa: o ícone dela aceso (onde se está).
    html = await renderizarBarra(com({ recolhida: true, emailAberto: { chave: 'i-sh', grupo: 'problemas' } }))
    assert.match(html, /<button type="button" class="relative mx-1[^"]*bg-primary\/10 text-primary ring-1[^"]*"[^>]*aria-current="true" title="Shopee · ATV/)
    // Os e-mails de uma loja na Caixa: acesa fica a caixa, nenhuma linha do chat.
    html = await renderizarBarra(com({ filtros: filtros({ plataforma: 'shopee', integration_id: 'i-sh' }), emailsAbertos: ['i-sh'], emailAberto: { chave: 'i-sh', grupo: 'problemas' } }))
    assert.match((html.match(/<button[^>]*data-email-grupo="problemas"[^>]*>/) || [''])[0], /bg-primary\/10 font-semibold text-primary/)
    assert.doesNotMatch((html.match(/<button[^>]*data-email-grupo="envios"[^>]*>/) || [''])[0], /bg-primary\/10/)
    assert.doesNotMatch(html, /<button type="button" class="relative mx-1[^"]*"[^>]*aria-pressed="true"/, 'nenhuma loja do chat acesa')
    // Sem os e-mails na Caixa: a loja do filtro acesa, como sempre.
    html = await renderizarBarra(com({ filtros: filtros({ plataforma: 'shopee', integration_id: 'i-sh' }) }))
    assert.match(html, /<button type="button" class="relative mx-1[^"]*"[^>]*aria-pressed="true" title="Shopee · ATV/)
    // A Caixa Humano (sem `emails`): nada de e-mail.
    assert.doesNotMatch(await renderizarBarra({ ...base, numero: 'humano', emails: null }), /data-so-email|data-ampliar-emails/)
    // O modo "emails" (celular e tablet): as lojas (não o site), sem "Todas", sem números de chat.
    html = await renderizarBarra(com({ modo: 'emails', emailsAbertos: ['i-sh'] }))
    assert.match(html, />E-mails das lojas</)
    assert.doesNotMatch(html, /data-lojas-todas|recolher a barra|Charlots|>71</)
    assert.match(html, /title="Shopee · ATV\nO clique mostra os e-mails desta loja\."/)
    assert.match(html, /aria-expanded="true" aria-controls="emails-loja-i-sh"/)
    assert.match(html, /data-so-email="email:f:f-luno"/)
    assert.doesNotMatch(html, /data-ampliar-emails/, 'o clique na linha já abre')
    // Sem nenhuma loja: o vazio explica.
    html = await renderizarBarra({ ...base, resumo: { ...RESUMO, lojas: [] }, modo: 'emails', emails: [], gruposEmail: GRUPOS })
    assert.match(html, /data-sem-emails-de-loja>\s*Nenhuma loja para mostrar agora\./)
  }


  // ------------------------------------------------ as caixas dos SITES (SAC · Admin, 27/10/2026)
  {
    const SITES = [
      { site: 'charlots', externo_ref: 'site:charlots', nome: 'Charlots', pastas: ['*charlots'], status: 'liga', caixas: [{ chave: 'sac', rotulo: 'SAC' }, { chave: 'admin', rotulo: 'Admin' }], emails_30d: 9, grupos: { sac: conta(7, 2, 3), admin: conta(2) } },
      // Sem a linha na barra (o /resumo não mandou o site): nada.
      { site: 'uranyx', externo_ref: 'site:uranyx', nome: 'Uranyx', pastas: ['*uranyx'], status: 'sem_categoria', caixas: [{ chave: 'sac', rotulo: 'SAC' }, { chave: 'admin', rotulo: 'Admin' }], emails_30d: 0, grupos: {} },
    ]
    const linhaSite = { chave: 'ext:site:charlots', integration_id: '', externo_ref: 'site:charlots', rede_social_id: '', plataforma: 'site', conta: 'Charlots' }
    // As puras: o site da linha, as caixas e os números no formato das lojas.
    assert.equal(LJ.siteDaLinha(SITES, linhaSite), SITES[0])
    assert.equal(LJ.siteDaLinha(null, linhaSite), null)
    assert.equal(LJ.siteDaLinha(SITES, { ...linhaSite, externo_ref: 'zap:123' }), null)
    assert.equal(LJ.siteDaLinha(SITES, { ...linhaSite, integration_id: 'i-x' }), null)
    assert.equal(LJ.siteDaLinha([{ ...SITES[0], caixas: [] }], linhaSite), null, 'sem caixas, sem seta')
    assert.deepEqual(LJ.gruposDoSite(SITES[0]), [
      { chave: 'sac', rotulo: 'SAC', pastas: ['*charlots'], status: 'liga' },
      { chave: 'admin', rotulo: 'Admin', pastas: ['*charlots'], status: 'liga' },
    ])
    assert.deepEqual(LJ.gruposDoSite(null), [])
    const e = LJ.emailsDoSite(SITES[0])
    assert.deepEqual([e.site, e.plataforma, e.loja, e.integration_id, e.store_info_id], ['charlots', 'site', 'Charlots', null, null])
    assert.equal(LJ.numeroDoGrupo(e, 'sac'), 7)
    assert.equal(LJ.esperandoDoGrupo(e, 'sac'), 2)
    assert.deepEqual(LJ.escolhaDoEmail(linhaSite, e, 'admin'), { chave: 'ext:site:charlots', integration_id: '', store_info_id: '', plataforma: 'site', conta: 'Charlots', grupo: 'admin', site: 'charlots' })
    assert.equal(LJ.tituloDoAmpliavel(e, 'Charlots', false, LJ.gruposDoSite(SITES[0])), 'Mostrar os e-mails do site Charlots (SAC 7 · Admin 2)')
    // O porquê da caixa vazia do site: a pasta primeiro; senão o endereço.
    const u = LJ.emailsDoSite(SITES[1])
    assert.deepEqual(LJ.motivosDaCaixaVazia(LJ.gruposDoSite(SITES[1])[0], u, 30), ['A pasta “*uranyx” está sem categoria: a ponte guarda os e-mails dela sem ligar à loja (E-mail › Pastas).'])
    assert.deepEqual(LJ.motivosDaCaixaVazia(LJ.gruposDoSite(SITES[0])[1], { ...e, grupos: {} }, 30), ['Nenhum e-mail para o admin@ (nem para os outros endereços) do site nos últimos 30 dias.'])
    assert.deepEqual(LJ.motivosDaCaixaVazia(LJ.gruposDoSite(SITES[0])[0], { ...e, grupos: {} }, 30), ['Nenhum e-mail para o sac@ do site nos últimos 30 dias.'])
    // A rota da lista: o site no lugar da loja.
    assert.equal(LG.urlDosEmailsDaCaixa({ integration_id: '', store_info_id: '', grupo: 'sac', site: 'charlots' }, { limite: 5 }), '/api/atendimento/email/lojas/emails?grupo=sac&site=charlots&limite=5')
    assert.notEqual(LE.chaveDaEscolha({ chave: 'x', grupo: 'sac', integration_id: '', store_info_id: '', site: 'charlots' }), LE.chaveDaEscolha({ chave: 'x', grupo: 'sac', integration_id: '', store_info_id: '', site: 'uranyx' }))

    // A barra renderizada: a seta no site (só com `sitesEmail`), as duas caixas embaixo.
    const base = { resumo: RESUMO, filtros: filtros(), numero: 'nao_lidas_duoke', emails: POR_LOJA, gruposEmail: GRUPOS, diasEmail: 30 }
    let html = await renderizarBarra(base)
    assert.doesNotMatch(html, /data-ampliar-emails="ext:site:charlots"/, 'sem `sitesEmail`: o site como era')
    html = await renderizarBarra({ ...base, sitesEmail: SITES })
    assert.match(html, /aria-label="Mostrar os e-mails do site Charlots \(SAC 7 · Admin 2\)"[^>]*data-ampliar-emails="ext:site:charlots"/)
    assert.match(html, /title="Site · Charlots[^"]*A seta mostra os e-mails do site: SAC \(sac@\) e Admin/)
    html = await renderizarBarra({ ...base, sitesEmail: SITES, emailsAbertos: ['ext:site:charlots'], gruposAbertos: ['ext:site:charlots|admin'] })
    const grupo = (html.match(/<div id="emails-loja-ext_site_charlots" role="group" aria-label="E-mails do site Charlots"[\s\S]*?data-email-grupo="admin"[\s\S]*?<\/div>/) || [''])[0]
    assert.ok(grupo, 'as caixas embaixo do site')
    const caixas = [...grupo.matchAll(/data-email-grupo="([^"]+)"[^>]*>([\s\S]*?)<\/button>/g)].map((m) => [m[1], m[2].replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()])
    assert.deepEqual(caixas, [['sac', 'SAC 2 7'], ['admin', 'Admin 2']])
    assert.match(html, /data-stub-caixa="admin" data-total="2"/)
    // Recolhida, na Caixa Humano (sem `emails`): nada.
    assert.doesNotMatch(await renderizarBarra({ ...base, sitesEmail: SITES, recolhida: true }), /data-ampliar-emails/)
    assert.doesNotMatch(await renderizarBarra({ ...base, emails: null, sitesEmail: SITES, numero: 'humano' }), /data-ampliar-emails/)
    // O celular (modo "emails"): o site entra com as caixas dele.
    html = await renderizarBarra({ ...base, sitesEmail: SITES, modo: 'emails' })
    assert.match(html, /Site · Charlots\nO clique mostra os e-mails desta loja\./)

    // O clique (setup de verdade): a caixa do site manda o site, não a loja; o clique na linha filtra o chat como sempre.
    const b = montar('../components/AtendimentoLojas.vue', { ...base, sitesEmail: SITES, emailsAbertos: [], gruposAbertos: [] })
    const site = b.estado.lojas.find((l) => l.externo_ref === 'site:charlots')
    assert.ok(b.estado.emailsDe(site))
    assert.deepEqual(b.estado.gruposDe(site).map((g) => g.chave), ['sac', 'admin'])
    b.estado.clicarGrupo(site, 'sac')
    assert.deepEqual(b.estado.gruposAbertos, ['ext:site:charlots|sac'])
    assert.deepEqual(b.emitidos.at(-1), ['abrirEmails', { chave: 'ext:site:charlots', integration_id: '', store_info_id: '', plataforma: 'site', conta: 'Charlots', grupo: 'sac', site: 'charlots' }])
    b.estado.escolherLoja(site)
    assert.equal(b.estado.filtros.externo_ref, 'site:charlots')

    // A lista curta da caixa do site: a rota com o site.
    const URL_CAIXA = '/api/atendimento/email/lojas/emails'
    const m = montar('../components/AtendimentoLojaGrupoEmails.vue', { escolha: { chave: 'ext:site:charlots', integration_id: '', store_info_id: '', plataforma: 'site', conta: 'Charlots', grupo: 'admin', site: 'charlots' }, total: 2, motivos: [] }, {
      respostas: { [URL_CAIXA]: { itens: [{ id: 's1', estado: 'gravado', recebido_em: '2026-10-27T10:00:00Z', pasta: '*charlots', assunto: '[CA-26-0001] Atacado — Boutique — Grade', de: 'nao-responda@envio.charlots.com.br', conversa_id: 'c9', aguardando_resposta: true }], proximo: null } },
    })
    await m.montar()
    assert.deepEqual(m.chamadas.map((c) => c.url), ['/api/atendimento/email/lojas/emails?grupo=admin&site=charlots&limite=5'])
    assert.match(await m.html(), /data-email-da-caixa="s1"[\s\S]*?falta responder/)

    // A página: os sites da API, os chips da lista do meio e a contagem do site.
    const pagina = web('pages/atendimento.vue')
    assert.match(pagina, /emailSitesNaBarra\.value = Array\.isArray\(recebidas\.sites_na_barra\) \? recebidas\.sites_na_barra : \[\]/)
    assert.match(pagina, /if \(e\.site\) \{\n    const s = emailSitesNaBarra\.value\.find\(\(x\) => x\.site === e\.site\)\n    return s \? emailsDoSite\(s\) : null\n  \}/)
    assert.match(pagina, /if \(e\?\.site\) return gruposDoSite\(emailSitesNaBarra\.value\.find\(\(x\) => x\.site === e\.site\)\)\n  return emailGruposNaBarra\.value/)
    // O contrato com a API: as caixas e os sites moram lá.
    const api = (rel) => fs.readFileSync(path.resolve(__dirname, '../../api/app', rel), 'utf8')
    assert.match(api('routers/atendimento_email.py'), /"sites_na_barra": await _contagens_dos_sites\(session, scope, visao\)/)
    assert.match(api('routers/atendimento_email.py'), /site: Annotated\[str \| None, Query\(max_length=32\)\] = None,/)
    assert.match(api('services/mail_atendimento/constantes.py'), /SITES_EMAIL_NA_BARRA_DE_LOJAS: tuple\[tuple\[str, str, tuple\[str, \.\.\.\]\], \.\.\.\] = \(\n    \("uranyx", "Uranyx", \("\*uranyx",\)\),\n    \("charlots", "Charlots", \("\*charlots",\)\),\n    \("makisa", "Makisa", \("\*makisa",\)\),\n    \("7buyers", "7buyers", \("\*7buyers",\)\),\n\)/)
    assert.match(api('routers/atendimento_email.py'), /"so_email": st\.so_email,/)
    assert.doesNotMatch(web('components/AtendimentoLojas.vue'), /'\*uranyx'|'\*charlots'|'\*makisa'|'\*7buyers'|'makisa'|'7buyers'|'sac@|'admin@/)
  }

  // ------------------------------------------------ os sites SÓ DE E-MAIL (Makisa e 7buyers, 27/10/2026)
  {
    const caixas = [{ chave: 'sac', rotulo: 'SAC' }, { chave: 'admin', rotulo: 'Admin' }]
    const SITES = [
      { site: 'charlots', externo_ref: 'site:charlots', nome: 'Charlots', pastas: ['*charlots'], so_email: false, status: 'liga', caixas, emails_30d: 9, grupos: { sac: conta(7), admin: conta(2) } },
      // Site do carrinho sem a linha na barra: continua sem linha (nunca vira "só e-mail").
      { site: 'uranyx', externo_ref: 'site:uranyx', nome: 'Uranyx', pastas: ['*uranyx'], so_email: false, status: 'liga', caixas, emails_30d: 1, grupos: { sac: conta(1) } },
      { site: 'makisa', externo_ref: 'site:makisa', nome: 'Makisa', pastas: ['*makisa'], so_email: true, status: 'sem_categoria', caixas, emails_30d: 5, grupos: { sac: conta(3), admin: conta(2) } },
      { site: '7buyers', externo_ref: 'site:7buyers', nome: '7buyers', pastas: ['*7buyers'], so_email: true, status: 'sem_pasta', caixas, emails_30d: 0, grupos: { sac: conta(0), admin: conta(0) } },
    ]
    const linhas = RESUMO.lojas.map((l) => ({ ...l, integration_id: l.integration_id || '', externo_ref: l.externo_ref || '' }))
    // As puras: só o site `so_email` sem linha; a chave é a que a linha do site teria.
    assert.deepEqual(LJ.sitesSemLinha(SITES, linhas).map((x) => x.site), ['makisa', '7buyers'])
    assert.deepEqual(LJ.sitesSemLinha(SITES, [...linhas, { integration_id: '', externo_ref: 'site:makisa' }]).map((x) => x.site), ['7buyers'], 'a linha do site que existe ganha')
    assert.deepEqual(LJ.sitesSemLinha([{ ...SITES[2], caixas: [] }], linhas), [], 'sem caixas, sem linha')
    assert.deepEqual(LJ.sitesSemLinha(null, linhas), [])
    assert.equal(LJ.chaveDoSiteSoEmail(SITES[2]), 'ext:site:makisa')
    // O porquê da caixa vazia da 7buyers: a pasta ainda não chegou pela ponte.
    const b7 = LJ.emailsDoSite(SITES[3])
    assert.deepEqual(LJ.motivosDaCaixaVazia(LJ.gruposDoSite(SITES[3])[0], b7, 30), ['A pasta “*7buyers” ainda não chegou na ponte: o Tuta ainda não mandou essa pasta.'])
    assert.deepEqual(LJ.motivosDaCaixaVazia(LJ.gruposDoSite(SITES[3])[1], b7, 30), ['A pasta “*7buyers” ainda não chegou na ponte: o Tuta ainda não mandou essa pasta.'])

    // A barra renderizada: as duas linhas no grupo Site, em ordem, com a seta na própria linha.
    const base = { resumo: RESUMO, filtros: filtros(), numero: 'nao_lidas_duoke', emails: POR_LOJA, gruposEmail: GRUPOS, diasEmail: 30, sitesEmail: SITES }
    let html = await renderizarBarra(base)
    assert.deepEqual([...html.matchAll(/data-so-email="(ext:site:[^"]+)"/g)].map((x) => x[1]), ['ext:site:7buyers', 'ext:site:makisa'])
    assert.doesNotMatch(html, /data-so-email="ext:site:uranyx"|data-so-email="ext:site:charlots"/)
    assert.match(html, /title="Site · Makisa\nSite só de e-mail \(sem carrinho nem chat no DaVinci\): o clique mostra os e-mails do site — SAC \(sac@\) e Admin \(admin@ e os outros endereços\)\."/)
    assert.match(html, /aria-expanded="false"[^>]*data-so-email="ext:site:makisa"/)
    assert.doesNotMatch(html, /data-ampliar-emails="ext:site:makisa"/, 'o clique na linha já abre')
    // Abertas: SAC e Admin com os números; a 7buyers vazia diz por quê.
    html = await renderizarBarra({ ...base, emailsAbertos: ['ext:site:makisa', 'ext:site:7buyers'], gruposAbertos: ['ext:site:7buyers|sac'] })
    const grupoMk = (html.match(/<div id="emails-loja-ext_site_makisa" role="group" aria-label="E-mails do site Makisa"[\s\S]*?data-email-grupo="admin"[\s\S]*?<\/button>/) || [''])[0]
    const caixasMk = [...grupoMk.matchAll(/data-email-grupo="([^"]+)"[^>]*>([\s\S]*?)<\/button>/g)].map((m) => [m[1], m[2].replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()])
    assert.deepEqual(caixasMk, [['sac', 'SAC 3'], ['admin', 'Admin 2']])
    assert.match(html, /id="emails-loja-ext_site_7buyers" role="group" aria-label="E-mails do site 7buyers"/)
    assert.match(html, /data-stub-caixa="sac" data-total="0"[^>]*>A pasta “\*7buyers” ainda não chegou na ponte: o Tuta ainda não mandou essa pasta\.</)
    // Sem `emails` (outra aba), na Caixa Humano, sem o /resumo ou sem os sites (quem vê só a equipe): nenhuma.
    assert.doesNotMatch(await renderizarBarra({ ...base, emails: null }), /data-so-email="ext:site/)
    assert.doesNotMatch(await renderizarBarra({ ...base, numero: 'humano' }), /data-so-email="ext:site/)
    assert.doesNotMatch(await renderizarBarra({ ...base, resumo: null }), /data-so-email="ext:site/)
    assert.doesNotMatch(await renderizarBarra({ ...base, sitesEmail: [] }), /data-so-email="ext:site/)
    // Recolhida: some (como a ficha sem chat); o celular: entra com as caixas.
    assert.doesNotMatch(await renderizarBarra({ ...base, recolhida: true }), /data-so-email="ext:site/)
    assert.match(await renderizarBarra({ ...base, modo: 'emails' }), /data-so-email="ext:site:makisa"/)

    // O clique (setup de verdade): a linha abre e fecha (nunca mexe no chat); a caixa manda o site.
    const b = montar('../components/AtendimentoLojas.vue', { ...base, emailsAbertos: [], gruposAbertos: [] })
    const mk = b.estado.sitesSoEmail.find((l) => l.chave === 'ext:site:makisa')
    assert.deepEqual([mk.plataforma, mk.conta, mk.externo_ref, mk.so_email], ['site', 'Makisa', 'site:makisa', true])
    assert.deepEqual(b.estado.gruposDe(mk).map((g) => [g.chave, g.status]), [['sac', 'sem_categoria'], ['admin', 'sem_categoria']])
    const antes = JSON.stringify(b.estado.filtros)
    b.estado.escolherLoja(mk)
    assert.deepEqual(b.estado.emailsAbertos, ['ext:site:makisa'])
    assert.equal(JSON.stringify(b.estado.filtros), antes)
    assert.equal(b.estado.ativaLoja(mk), false)
    b.estado.clicarGrupo(mk, 'sac')
    assert.deepEqual(b.estado.gruposAbertos, ['ext:site:makisa|sac'])
    assert.deepEqual(b.emitidos.at(-1), ['abrirEmails', { chave: 'ext:site:makisa', integration_id: '', store_info_id: '', plataforma: 'site', conta: 'Makisa', grupo: 'sac', site: 'makisa' }])
    b.estado.abrirEmail(mk, 'admin', { id: 'mk1', conversa_id: null })
    assert.deepEqual(b.emitidos.at(-1), ['abrirEmail', { chave: 'ext:site:makisa', integration_id: '', store_info_id: '', plataforma: 'site', conta: 'Makisa', grupo: 'admin', site: 'makisa' }, { id: 'mk1', conversa_id: null }])
    // A lista curta e a do meio: a rota com o site.
    assert.equal(LG.urlDosEmailsDaCaixa({ integration_id: '', store_info_id: '', grupo: 'admin', site: '7buyers' }, { limite: 5 }), '/api/atendimento/email/lojas/emails?grupo=admin&site=7buyers&limite=5')
  }

  // ------------------------------------------------ a barra: o clique (setup de verdade)
  {
    const b = montar('../components/AtendimentoLojas.vue', { resumo: RESUMO, filtros: filtros(), numero: 'nao_lidas_duoke', emails: POR_LOJA, gruposEmail: GRUPOS, emailsAbertos: [], gruposAbertos: [] })
    const atv = b.estado.lojas.find((l) => l.integration_id === 'i-sh')
    assert.ok(b.estado.emailsDe(atv))
    b.estado.alternarEmails(atv)
    assert.deepEqual(b.estado.emailsAbertos, ['i-sh'])
    b.estado.alternarEmails(atv)
    assert.deepEqual(b.estado.emailsAbertos, [])
    // Cada caixa abre e fecha sozinha.
    b.estado.alternarGrupo(atv, 'problemas')
    b.estado.alternarGrupo(atv, 'envios')
    b.estado.alternarGrupo(atv, 'problemas')
    assert.deepEqual(b.estado.gruposAbertos, ['i-sh|envios'])
    b.estado.abrirEmails(atv, 'problemas')
    const escolhaAtv = { chave: 'i-sh', integration_id: 'i-sh', store_info_id: '', plataforma: 'shopee', conta: 'ATV', grupo: 'problemas' }
    assert.deepEqual(b.emitidos, [['abrirEmails', escolhaAtv]])
    b.estado.abrirEmail(atv, 'problemas', { id: 'm1', conversa_id: null })
    assert.deepEqual(b.emitidos.at(-1), ['abrirEmail', escolhaAtv, { id: 'm1', conversa_id: null }])
    // A loja sem e-mail também abre (a caixa vazia diz por quê); o site não tem caixas.
    b.estado.abrirEmails(b.estado.lojas.find((l) => l.integration_id === 'i-sh2'), 'envios')
    assert.equal(b.emitidos.at(-1)[1].integration_id, 'i-sh2')
    const n = b.emitidos.length
    b.estado.abrirEmails(b.estado.lojas.find((l) => l.externo_ref === 'site:charlots'), 'envios')
    assert.equal(b.emitidos.length, n)
    // A ficha (sem chat): o clique na linha abre e fecha (nunca mexe no filtro do chat); a caixa manda a ficha.
    const luno = b.estado.lojasSoEmail.find((l) => l.chave === 'email:f:f-luno')
    const antes = JSON.stringify(b.estado.filtros)
    b.estado.escolherLoja(luno)
    assert.deepEqual(b.estado.emailsAbertos, ['email:f:f-luno'])
    assert.equal(JSON.stringify(b.estado.filtros), antes)
    b.estado.abrirEmails(luno, 'problemas')
    assert.deepEqual(b.emitidos.at(-1), ['abrirEmails', { chave: 'email:f:f-luno', integration_id: '', store_info_id: 'f-luno', plataforma: 'shopee', conta: 'luno', grupo: 'problemas' }])
    // O clique na caixa (27/10/2026): abre a caixa E a lista da loja no meio de uma vez.
    b.estado.gruposAbertos = []
    const antesDoClique = b.emitidos.length
    b.estado.clicarGrupo(atv, 'devolucao')
    assert.deepEqual(b.estado.gruposAbertos, ['i-sh|devolucao'])
    assert.equal(b.emitidos.length, antesDoClique + 1)
    assert.deepEqual(b.emitidos.at(-1), ['abrirEmails', { ...escolhaAtv, grupo: 'devolucao' }])
  }

  // ------------------------------------------------ a caixa aberta: a lista curta (setup de verdade)
  {
    const URL_CAIXA = '/api/atendimento/email/lojas/emails'
    const item = (id, o = {}) => ({ id, estado: 'informativo', recebido_em: '2026-10-27T10:00:00Z', pasta: 'devoluçoes', assunto: `Assunto ${id}`, de: 'avisos@ml.com', seguranca: false, conversa_id: null, aguardando_resposta: false, ...o })
    const escolha = { chave: 'i-sh', integration_id: 'i-sh', store_info_id: '', plataforma: 'shopee', conta: 'ATV', grupo: 'devolucao' }
    const m = montar('../components/AtendimentoLojaGrupoEmails.vue', { escolha, total: 7, motivos: ['x'], tique: 0 }, {
      respostas: { [URL_CAIXA]: { itens: [item('e1', { conversa_id: 'c1', aguardando_resposta: true }), item('e2', { estado: 'seguranca', seguranca: true })], proximo: 'p' } },
    })
    await m.montar()
    assert.deepEqual(m.chamadas.map((c) => c.url), ['/api/atendimento/email/lojas/emails?grupo=devolucao&integration_id=i-sh&limite=5'], 'só quando a caixa abre (montada), a lista curta')
    let html = await m.html()
    assert.deepEqual([...html.matchAll(/data-email-da-caixa="([^"]+)"/g)].map((x) => x[1]), ['e1', 'e2'])
    assert.match(html, /data-email-da-caixa="e1"[\s\S]*?Assunto e1[\s\S]*?falta responder/)
    assert.match(html, /data-email-da-caixa="e2"[\s\S]*?Assunto e2[\s\S]*?código/)
    assert.match(html, /data-ver-todos>ver todos \(7\)</)
    // O polling da página relê a lista curta.
    m.reativo.tique = 1
    await esperar()
    assert.equal(m.chamadas.length, 2)
    // Vazia: o porquê (da barra).
    const vazia = montar('../components/AtendimentoLojaGrupoEmails.vue', { escolha, total: 0, motivos: ['A pasta “devoluçoes” ainda não está configurada.', 'Nenhum e-mail ligado a esta loja.'] }, { respostas: { [URL_CAIXA]: { itens: [], proximo: null } } })
    await vazia.montar()
    html = await vazia.html()
    assert.match(html, /data-caixa-vazia><p>A pasta “devoluçoes” ainda não está configurada\.<\/p><p>Nenhum e-mail ligado a esta loja\.<\/p>/)
    assert.doesNotMatch(html, /data-ver-todos/)
    // Sem loja: nem pergunta.
    const semLoja = montar('../components/AtendimentoLojaGrupoEmails.vue', { escolha: { ...escolha, integration_id: '' }, motivos: [] }, { respostas: {} })
    await semLoja.montar()
    assert.deepEqual(semLoja.chamadas, [])
  }

  // ------------------------------------------------ a lista da loja na Caixa (setup de verdade)
  {
    const URL_CAIXA = '/api/atendimento/email/lojas/emails'
    const item = (id, o = {}) => ({ id, estado: 'informativo', recebido_em: '2026-10-27T10:00:00Z', pasta: 'problemas', assunto: `Assunto ${id}`, de: 'avisos@ml.com', seguranca: false, conversa_id: null, aguardando_resposta: false, situacao: null, ...o })
    const respostas = {
      [URL_CAIXA]: (url) => ({ itens: url.includes('grupo=devolucao') ? [item('d1')] : [item('e1', { conversa_id: 'c2', aguardando_resposta: true, situacao: 'aberta' }), item('m1', { estado: 'seguranca', seguranca: true })], proximo: null }),
      '/api/atendimento/email/lojas/emails/m1': { id: 'm1', texto: 'Use o código 482913 na agência', de: 'avisos@ml.com', de_nome: 'ML', links_removidos: 1, seguranca: true, anexos: [{ id: 'a1', filename: 'etiqueta.pdf', tamanho: 2048 }] },
    }
    const escolha = { chave: 'i-sh', integration_id: 'i-sh', store_info_id: '', plataforma: 'shopee', conta: 'ATV', grupo: 'problemas' }
    const m = montar('../components/AtendimentoLojaEmails.vue', { escolha, contagem: POR_LOJA[0], grupos: GRUPOS, dias: 30, selecionada: 'c2', tique: 0, sinal: 0 }, { respostas })
    await m.montar()
    assert.deepEqual(m.chamadas.map((c) => c.url), ['/api/atendimento/email/lojas/emails?grupo=problemas&integration_id=i-sh'])
    let html = await m.html()
    assert.deepEqual([...html.matchAll(/data-email-da-loja="([^"]+)"/g)].map((x) => x[1]), ['e1', 'm1'])
    assert.match(html, /aria-current="true"[^>]*data-email-da-loja="e1"/, 'a conversa aberta acesa')
    assert.match(html, /data-email-da-loja="e1"[\s\S]*?falta responder[\s\S]*?Assunto e1/)
    assert.match(html, /data-email-da-loja="m1"[\s\S]*?com código[\s\S]*?Assunto m1[\s\S]*?ver o e-mail/)
    // Os chips: as pastas da barra (a escolha do dono vem da API), com o número.
    assert.deepEqual([...html.matchAll(/data-grupo-da-loja="([^"]+)"/g)].map((x) => x[1]), ['problemas', 'envios', 'devolucao', 'informativo'])
    assert.match(html, /aria-pressed="true" data-grupo-da-loja="problemas"[^>]*>[\s\S]*?Problemas<span[^>]*>31<\/span>/)
    assert.match(html, /data-voltar-conversas/)
    // O e-mail da conversa abre a conversa; o outro vai para a ÁREA PRINCIPAL
    // (27/10/2026): a lista só avisa a página — não lê nem abre nada aqui.
    m.estado.abrir(m.estado.itens[0])
    // A linha vai junto (27/10/2026): a página apaga a bolinha do site na hora.
    assert.deepEqual(m.emitidos.at(-1), ['selecionar', 'c2', m.estado.itens[0]])
    const chamadasAntes = m.chamadas.length
    m.estado.abrir(m.estado.itens[1])
    await esperar()
    assert.deepEqual(m.emitidos.at(-1), ['ver', m.estado.itens[1]])
    assert.equal(m.chamadas.length, chamadasAntes, 'a lista não lê o e-mail')
    html = await m.html()
    assert.doesNotMatch(html, /data-email-da-loja-aberto|Use o código 482913|fechar o e-mail|aria-expanded/, 'nada abre dentro da lista')
    // Aberto na área principal: a linha acesa (e a da conversa apagada).
    m.reativo.aberto = 'm1'
    m.reativo.selecionada = null
    html = await m.html()
    assert.match(html, /class="[^"]*border-primary bg-primary\/5"[^>]*aria-current="true"[^>]*data-email-da-loja="m1"[\s\S]*?aberto ao lado/)
    assert.doesNotMatch(html, /aria-current="true"[^>]*data-email-da-loja="e1"/)
    m.reativo.aberto = null
    m.reativo.selecionada = 'c2'
    // O tique (polling da página) relê a 1ª página.
    const antes = m.chamadas.length
    m.reativo.tique = 1
    await esperar()
    assert.equal(m.chamadas.length, antes + 1)
    // Outra pasta da mesma loja: do começo.
    m.reativo.escolha = { ...escolha, grupo: 'devolucao' }
    await esperar()
    assert.equal(m.chamadas.at(-1).url, '/api/atendimento/email/lojas/emails?grupo=devolucao&integration_id=i-sh')
    assert.deepEqual(m.estado.itens.map((i) => i.id), ['d1'])
    // O clique num e-mail na barra (sem conversa): a lista abre com a linha dele acesa (o e-mail, na área principal).
    const comAberto = montar('../components/AtendimentoLojaEmails.vue', { escolha, grupos: GRUPOS, aberto: 'm1' }, { respostas })
    await comAberto.montar()
    await esperar()
    assert.deepEqual(comAberto.chamadas.map((c) => c.url), ['/api/atendimento/email/lojas/emails?grupo=problemas&integration_id=i-sh'], 'só a lista: o e-mail quem lê é a área principal')
    assert.match(await comAberto.html(), /aria-current="true"[^>]*data-email-da-loja="m1"/)
    assert.equal('linhaAberta' in comAberto.estado, false, 'sem o e-mail aberto dentro da lista')
    // Vazia: o porquê (a pasta que ainda não chegou na ponte).
    const vazio = montar('../components/AtendimentoLojaEmails.vue', { escolha: { ...escolha, grupo: 'informativo' }, contagem: POR_LOJA[0], grupos: GRUPOS }, { respostas: { [URL_CAIXA]: { itens: [], proximo: null } } })
    await vazio.montar()
    assert.match(await vazio.html(), /data-emails-da-loja-vazio><p>A pasta “informativo” ainda não chegou na ponte/)
    // Sem loja nenhuma: não pergunta nada (nunca a lista de todas as lojas).
    const sem = montar('../components/AtendimentoLojaEmails.vue', { escolha: { ...escolha, integration_id: '' }, grupos: GRUPOS }, { respostas })
    await sem.montar()
    assert.deepEqual(sem.chamadas, [])
    assert.match(await sem.html(), /role="alert">Esta loja não tem como filtrar os e-mails\./)
  }

  // ------------------------------------------------ o e-mail da loja na área principal (setup de verdade)
  {
    const URL_M1 = '/api/atendimento/email/lojas/emails/m1'
    const detalhe = { id: 'm1', estado: 'seguranca', recebido_em: '2026-10-27T10:00:00Z', pasta: 'devoluçoes', loja: 'ATV', conversa_id: null, seguranca: true, de: 'avisos@ml.com', de_nome: 'Mercado Livre', reply_to: [], assunto: 'Etiqueta da devolução', texto: 'Olá,\n\n  Use o código 482913 na agência.\nObrigado', links_removidos: 2, anexos: [{ id: 'a1', filename: 'etiqueta.pdf', content_type: 'application/pdf', tamanho: 2048 }, { id: 'a 2', tamanho: 10 }] }
    const linha = { id: 'm1', estado: 'seguranca', recebido_em: '2026-10-27T10:00:00Z', pasta: 'devoluçoes', assunto: 'Etiqueta da devolução', de: 'avisos@ml.com', seguranca: true, conversa_id: null }
    let pronto
    const v = montar('../components/AtendimentoEmailDaLojaAberto.vue', { emailId: 'm1', linha, loja: 'ATV' }, {
      respostas: {
        [URL_M1]: () => new Promise((r) => { pronto = () => r(detalhe) }),
        '/api/atendimento/email/lojas/emails/m2': { ...detalhe, id: 'm2', assunto: 'Outro aviso', seguranca: false, links_removidos: 0, anexos: [], texto: 'Seu envio saiu.' },
        '/api/atendimento/email/lojas/emails/sumiu': () => Object.assign(new Error('404'), { statusCode: 404, data: { detail: { code: 'nao_encontrado' } } }),
      },
    })
    await v.montar()
    assert.deepEqual(v.chamadas.map((c) => c.url), [URL_M1], 'a mesma leitura de antes (GET …/lojas/emails/{id})')
    // Carregando: o cabeçalho da linha já na tela.
    let html = await v.html()
    assert.match(html, /data-email-da-loja-aberto/)
    assert.match(html, /data-assunto-do-email>Etiqueta da devolução<\/h2>/)
    assert.match(html, /data-com-codigo>com código</)
    assert.match(html, /data-icone="Loader2"/)
    assert.doesNotMatch(html, /data-texto-do-email/)
    pronto()
    await esperar()
    html = await v.html()
    // Lido: quem mandou (nome e endereço), a data, a pasta, o código, o texto inteiro (espaços guardados), os anexos.
    assert.match(html, /data-de-do-email><span class="text-muted-foreground">De<\/span> Mercado Livre &lt;avisos@ml\.com&gt;<\/div>/)
    assert.match(html, /<time[^>]*datetime="2026-10-27T10:00:00Z"[^>]*>[^<]+<\/time>/)
    assert.match(html, /data-pasta-do-email>devoluçoes</)
    assert.match(html, /data-links-removidos>2 link\(s\) de acesso removido\(s\)</)
    assert.match(html, /<pre class="whitespace-pre-wrap[^"]*" data-texto-do-email>Olá,\n\n  Use o código 482913 na agência\.\nObrigado<\/pre>/)
    assert.match(html, /<div class="min-h-0 flex-1 overflow-y-auto">/, 'o texto rola na área principal')
    assert.deepEqual([...html.matchAll(/href="([^"]+)"[^>]*data-anexo-do-email/g)].map((x) => x[1]), ['/api/atendimento/email/lojas/anexos/a1', '/api/atendimento/email/lojas/anexos/a%202'])
    assert.match(html, /data-anexo-do-email="a1"[\s\S]*?etiqueta\.pdf[\s\S]*?2 KB/)
    assert.match(html, /data-anexo-do-email="a 2"[\s\S]*?>anexo<[\s\S]*?10 B/)
    assert.doesNotMatch(html, /data-icone="Loader2"/)
    // "fechar o e-mail": avisa a página (que volta ao "Escolha um e-mail na lista").
    assert.match(html, /data-fechar-email-da-loja>[\s\S]*?fechar o e-mail<\/button>/)
    // Outro e-mail clicado com este aberto: lê o novo; sem anexos e sem links tirados, nada disso aparece.
    v.reativo.linha = { ...linha, id: 'm2', assunto: 'Outro aviso', seguranca: false }
    v.reativo.emailId = 'm2'
    await esperar()
    assert.equal(v.chamadas.at(-1).url, '/api/atendimento/email/lojas/emails/m2')
    html = await v.html()
    assert.match(html, /data-assunto-do-email>Outro aviso</)
    assert.doesNotMatch(html, /data-com-codigo|data-links-removidos|data-anexos-do-email|482913/)
    assert.match(html, /data-texto-do-email>Seu envio saiu\.</)
    // Fora da régua (404): o aviso na área principal, sem quebrar.
    v.reativo.emailId = 'sumiu'
    await esperar()
    html = await v.html()
    assert.match(html, /role="alert">[^<]+</)
    assert.doesNotMatch(html, /data-texto-do-email/)
    // A resposta atrasada de um e-mail de antes nunca chega por cima do aberto.
    let lento
    const r = montar('../components/AtendimentoEmailDaLojaAberto.vue', { emailId: 'm1', linha }, {
      respostas: { [URL_M1]: () => new Promise((ok) => { lento = () => ok(detalhe) }), '/api/atendimento/email/lojas/emails/m2': { ...detalhe, id: 'm2', assunto: 'Outro aviso', texto: 'novo' } },
    })
    await r.montar()
    r.reativo.emailId = 'm2'
    await esperar()
    lento()
    await esperar()
    assert.equal(r.estado.detalhe.id, 'm2')
    // O botão fecha: emite `fechar`.
    const f = montar('../components/AtendimentoEmailDaLojaAberto.vue', { emailId: 'm1', linha }, { respostas: { [URL_M1]: detalhe } })
    const fonte = sfc('../components/AtendimentoEmailDaLojaAberto.vue').descriptor.template.content
    assert.match(fonte, /data-fechar-email-da-loja\s+@click="emit\('fechar'\)"/)
    assert.equal(f.emitidos.length, 0)
  }

  // ------------------------------------------------ a página
  {
    const pagina = web('pages/atendimento.vue')
    const tpl = sfc('../pages/atendimento.vue').descriptor.template.content
    // A barra: os números por loja SÓ na Caixa (a Caixa Humano não tem a seta), as pastas da API, as lojas e as caixas abertas, o clique.
    assert.match(tpl, /<AtendimentoLojas\s+v-model:filtros="filtros"\s+v-model:recolhida="lojasRecolhidas"\s+v-model:emails-abertos="lojasEmailsAbertos"\s+v-model:grupos-abertos="gruposEmailsAbertos"\s+:resumo="resumo"\s+:numero="emHumano \? 'humano' : 'nao_lidas_duoke'"\s+:emails="aba === 'caixa' \? emailPorLoja : null"\s+:grupos-email="emailGruposNaBarra"\s+:sites-email="emailSitesNaBarra"\s+:dias-email="emailDias"\s+:tique="emailTique"\s+:email-aberto="emailsDaLoja \? \{ chave: emailsDaLoja\.chave, grupo: emailsDaLoja\.grupo \} : null"\s+@abrir-emails="abrirEmailsDaLoja"\s+@abrir-email="abrirEmailDaBarra"\s*\/>/)
    // Celular e tablet (sem a barra): o botão "E-mails das lojas" e a barra no modo "emails".
    assert.match(tpl, /<div v-if="aba === 'caixa' && canSugerir && !emailsDaLoja" class="[^"]*lg:hidden" data-emails-celular>[\s\S]*?@click="emailsNoCelular = !emailsNoCelular"[\s\S]*?'E-mails das lojas'/)
    assert.match(tpl, /<AtendimentoLojas\s+v-if="emailsNoCelular && !emailsDaLoja"[\s\S]*?class="lg:hidden"\s+modo="emails"[\s\S]*?:emails="emailPorLoja"\s+:grupos-email="emailGruposNaBarra"\s+:sites-email="emailSitesNaBarra"\s+:dias-email="emailDias"\s+:tique="emailTique"\s+@abrir-emails="abrirEmailsDaLoja"\s+@abrir-email="abrirEmailDaBarra"\s*\/>/)
    // A lista do meio: os e-mails da loja; a do chat fica GUARDADA (v-show), com os filtros dela.
    assert.match(tpl, /<AtendimentoLojaEmails\s+v-if="emailsDaLoja"\s+:escolha="emailsDaLoja"\s+:contagem="contagemDaLoja"\s+:grupos="gruposDaEscolha"\s+:dias="emailDias"\s+:selecionada="emailNaCaixa"\s+:aberto="emailDaCaixaAberto"\s+:tique="emailTique"\s+:sinal="emailNaCaixaSinal"\s+@selecionar="selecionarEmailNaCaixa"\s+@ver="abrirEmailDaLoja"\s+@grupo="trocarGrupoDaLoja"\s+@fechar="fecharEmailsDaLoja"\s*\/>\s*<AtendimentoLista\s+v-show="!emailsDaLoja && !emailsNoCelular"/)
    // O e-mail da loja sem conversa: na área principal (depois da conversa de e-mail, antes do "Escolha um e-mail").
    assert.match(tpl, /<\/div>\s*(?:<!--[^>]*-->\s*)?<AtendimentoEmailDaLojaAberto\s+v-else-if="emailsDaLoja && emailDaCaixaAberto"\s+:email-id="emailDaCaixaAberto"\s+:linha="emailDaCaixaLinha"\s+:loja="emailsDaLoja\.conta"\s+@fechar="fecharEmailDaLoja"\s*\/>\s*<div v-else-if="emailsDaLoja"[^>]*>\s*<EmptyState :icon="Mail" :title="`E-mails · \$\{emailsDaLoja\.conta\}`"/)
    assert.ok(tpl.indexOf('data-email-na-caixa') < tpl.indexOf('<AtendimentoEmailDaLojaAberto'), 'a conversa de e-mail tem a vez')
    // A conversa: a do chat escondida e PARADA (sem polling) enquanto há e-mail na Caixa; a de e-mail com a barra do e-mail.
    assert.match(tpl, /<AtendimentoConversa\s+v-if="selecionada"\s+v-show="!emailNaTela"[\s\S]*?:ativa="naCaixa && !emailNaTela"[\s\S]*?@mudou="aoMudarConversa"/)
    assert.match(tpl, /<div v-if="emailNaCaixa"[^>]*data-email-na-caixa>[\s\S]*?<AtendimentoEmailConversaBarra\s+:conversa="emailNaCaixaLinha"\s+:pode-mexer="mexe"\s+na-caixa\s+:sem-lista="!emailsDaLoja"\s+:com-chat="!!emailNaCaixaOrigem"\s+@voltar="fecharEmailNaCaixa"\s+@voltar-ao-chat="voltarAoChatDaCaixa"\s+@vinculado="aoVincularEmailNaCaixa"\s*\/>[\s\S]*?<AtendimentoConversa\s+:key="`\$\{emailNaCaixa\}:\$\{emailNaCaixaVersao\}`"\s+:conversa-id="emailNaCaixa"[\s\S]*?@mudou="aoMudarEmailNaCaixa"[\s\S]*?@abrir-conversa="selecionarEmailNaCaixa"/)
    // No celular: a lista some quando há conversa aberta (a do chat ou a de e-mail).
    assert.match(tpl, /:class="abertaNaCaixa \? 'hidden' : 'flex'"/)
    assert.match(tpl, /:class="abertaNaCaixa \? 'flex' : 'hidden'"/)
    // A aba E-mail: só admin ou a permissão `atendimento_email` view (as abas à mostra e o conteúdo).
    assert.match(tpl, /v-for="a in abasVisiveis"/)
    assert.doesNotMatch(tpl, /v-for="a in ABAS"/)
    assert.match(tpl, /<template v-if="aba === 'mail' && veAbaEmail">/)
    // 28/10/2026 (o marrocos vendo a aba sem virar admin): admin OU a permissão, só o view.
    assert.match(pagina, /const podeVerAbaEmail = useCan\('atendimento_email', 'view'\)\nconst veAbaEmail = computed\(\(\) => ehAdmin\.value \|\| podeVerAbaEmail\.value\)/)
    assert.doesNotMatch(pagina, /abasDe\(ehAdmin|if \(!ehAdmin\.value\)|!porLoja && !ehAdmin|watch\(ehAdmin|'mail' && ehAdmin/, 'a aba E-mail não depende mais só do admin')

    // ── a aba E-mail só para quem a vê, de verdade (o trecho da página com dublês)
    const trecho = (de, ate) => {
      const i = pagina.indexOf(de)
      const f = pagina.indexOf(ate, i)
      assert.ok(i > 0 && f > i, de)
      return transpileTs(pagina.slice(i, f))
    }
    {
      // A conta da página com o useCan de verdade (admin, permissão, sem permissão).
      const corpo = trecho('const podeVerAbaEmail = ', 'function abasDe(')
      let usuario = null
      const can = {}
      new Function('exports', 'require', 'useAuthStore', transpile(web('composables/useCan.ts')))(can, require, () => ({ user: usuario }))
      const ve = (u) => {
        usuario = u
        return new Function('computed', 'useCan', 'ehAdmin', `${corpo}\nreturn veAbaEmail`)(Vue.computed, can.useCan, Vue.computed(() => u?.role === 'admin')).value
      }
      assert.equal(ve({ role: 'admin', permissions: {} }), true, 'admin')
      assert.equal(ve({ role: 'user', permissions: { atendimento_email: { view: true } } }), true, 'a permissão (o marrocos)')
      assert.equal(ve({ role: 'user', permissions: { atendimento_email: { view: false } } }), false)
      assert.equal(ve({ role: 'user', permissions: { atendimento: { view: true, edit: true, delete: true } } }), false, 'o recurso da caixa não abre a aba')
      assert.equal(ve({ role: 'user', permissions: {} }), false)
      assert.equal(ve(null), false)
      // A tela de Permissões: o recurso no grupo Pós-venda, com o rótulo do "ver".
      const posVenda = can.RESOURCE_GROUPS.find((g) => g.label === 'Pós-venda')
      assert.ok(posVenda.resources.includes('atendimento_email'))
      assert.equal(can.RESOURCE_LABELS.atendimento_email, 'Atendimento — aba E-mail: ver (view) e responder (edit)')
      const permissoesApi = fs.readFileSync(path.resolve(__dirname, '../../api/app/schemas/permissions.py'), 'utf8')
      assert.match(permissoesApi, /^ {4}"atendimento_email",$/m, 'o recurso no backend')
    }
    {
      const abas = trecho('type Aba = ', "const { api } = useApi()")
      const ini = trecho('function abasDe(veEmail: boolean)', '// `?tab=lojas` abre direto na aba')
      const aba0 = trecho("const abaQuery = typeof route.query.tab", "const conversaQuery =")
      const montarAbas = (admin, tab) => new Function('computed', 'ref', 'veAbaEmail', 'route', 'Inbox', 'UserRound', 'Store', 'BookOpen', 'MessageSquareText', 'Bot', 'BarChart3', `${abas}\n${ini}\n${aba0}\nreturn { ABAS, abasVisiveis, aba }`)(
        Vue.computed, Vue.ref, Vue.ref(admin), { query: { tab } }, 'i', 'u', 's', 'b', 'm', 'bot', 'bar',
      )
      const admin = montarAbas(true, 'mail')
      assert.ok(admin.abasVisiveis.value.some((a) => a.value === 'mail'), 'admin: a aba E-mail como antes')
      assert.equal(admin.aba.value, 'mail')
      const leitor = montarAbas(false, 'mail')
      assert.deepEqual(leitor.abasVisiveis.value.map((a) => a.value), ['caixa', 'humano', 'lojas', 'manual', 'modelos', 'automaticas', 'metricas'])
      assert.equal(leitor.aba.value, 'caixa', 'o ?tab=mail de quem não é admin cai na Caixa')
      assert.equal(montarAbas(false, 'lojas').aba.value, 'lojas')
    }
    {
      // abrirAba('mail') e a conversa de e-mail de quem não é admin: na Caixa.
      const abrirAba = trecho('function abrirAba(a: string)', '// O registro das Automáticas')
      const doEmail = trecho('function abrirConversaDoEmail(', 'function fecharConversaDoEmail()')
      const montarLinks = (admin) => {
        const aba = Vue.ref('caixa')
        const avulsos = []
        const out = new Function('ref', 'aba', 'abasVisiveis', 'veAbaEmail', 'emailConversa', 'emailConversaLinha', 'chatDeOrigem', 'abrirEmailAvulsoNaCaixa', `${abrirAba}\n${doEmail}\nreturn { abrirAba, abrirConversaDoEmail }`)(
          Vue.ref, aba, Vue.ref([{ value: 'caixa' }, { value: 'lojas' }, ...(admin ? [{ value: 'mail' }] : [])]), Vue.ref(admin), Vue.ref(null), Vue.ref(null), Vue.ref(null), (id, origem) => avulsos.push([id, origem]),
        )
        return { ...out, aba, avulsos }
      }
      const leitor = montarLinks(false)
      leitor.abrirAba('mail')
      assert.equal(leitor.aba.value, 'caixa', 'abrirAba(mail) não leva quem não é admin à aba')
      leitor.abrirAba('lojas')
      assert.equal(leitor.aba.value, 'lojas')
      leitor.abrirConversaDoEmail('e-1', { id: 'chat-1', aba: 'caixa' })
      assert.deepEqual(leitor.avulsos, [['e-1', { id: 'chat-1', aba: 'caixa' }]], 'a conversa de e-mail abre na Caixa')
      assert.equal(leitor.aba.value, 'lojas', 'quem decide a aba é o abrirEmailAvulsoNaCaixa')
      const admin = montarLinks(true)
      admin.abrirConversaDoEmail('e-1')
      assert.deepEqual([admin.aba.value, admin.avulsos.length], ['mail', 0], 'admin: a aba E-mail como antes')
    }
    {
      // As contagens: quem não é admin só pergunta pela barra; os números por loja ficam guardados.
      const corpo = trecho('async function carregarContagensEmail()', '// Faixa do topo:')
      const montarContagens = ({ admin, porLoja, resposta }) => {
        const pedidos = []
        const r = {
          canSugerir: Vue.ref(true), querPorLoja: Vue.ref(porLoja), veAbaEmail: Vue.ref(admin),
          emailContagens: Vue.ref(null), emailPorLoja: Vue.ref(null), emailGruposNaBarra: Vue.ref([]), emailDias: Vue.ref(30), emailContagensProntas: Vue.ref(false),
        }
        const fn = new Function(...Object.keys(r), 'api', `let geracaoContagensEmail = 0\n${corpo}\nreturn carregarContagensEmail`)(
          ...Object.values(r), async (url) => { pedidos.push(url); return structuredClone(resposta) },
        )
        return { ...r, pedidos, fn }
      }
      let c = montarContagens({ admin: false, porLoja: false, resposta: {} })
      await c.fn()
      assert.deepEqual(c.pedidos, [], 'quem não é admin, fora da barra: nem pergunta (não tem a aba)')
      c = montarContagens({ admin: false, porLoja: true, resposta: { aguardando: 0, por_loja: POR_LOJA, grupos_na_barra: GRUPOS, dias: 30 } })
      await c.fn()
      assert.deepEqual(c.pedidos, ['/api/atendimento/email/contagens?por_loja=true'])
      assert.deepEqual([c.emailPorLoja.value.length, c.emailGruposNaBarra.value, c.emailDias.value], [POR_LOJA.length, GRUPOS, 30])
      c = montarContagens({ admin: true, porLoja: false, resposta: { aguardando: 3 } })
      c.emailPorLoja.value = POR_LOJA
      await c.fn()
      assert.deepEqual(c.pedidos, ['/api/atendimento/email/contagens'], 'admin fora da barra: a leitura de sempre (o número da aba)')
      assert.deepEqual(c.emailPorLoja.value, POR_LOJA, 'a leitura sem por_loja não apaga as setas')
    }

    // ── o bloco dos e-mails na Caixa (a página de verdade, com dublês, sem rede)
    const ini = pagina.indexOf('// ─── os E-MAILS de cada loja na Caixa (27/10/2026)')
    const fim = pagina.indexOf('</script>', ini)
    assert.ok(ini > 0 && fim > ini)
    function bloco({ usuario = 'u1', guardado = new Map(), quebrado = false, largura = 1440, admin = false, sites = [] } = {}) {
      const aba = Vue.ref('caixa')
      const filtros = Vue.ref(filtros0())
      const selecionada = Vue.ref(null)
      const emailPorLoja = Vue.ref(POR_LOJA)
      const larguraJanela = Vue.ref(largura)
      const lojasRecolhidas = Vue.ref(false)
      const montados = []
      const lidas = { contagens: 0, modelos: 0, selecionadas: [] }
      const localStorage = {
        getItem: (k) => { if (quebrado) throw new Error('bloqueado'); return guardado.has(k) ? guardado.get(k) : null },
        setItem: (k, v) => { if (quebrado) throw new Error('bloqueado'); guardado.set(k, v) },
      }
      const emailSitesNaBarra = Vue.ref(sites)
      const nomes = ['ref', 'computed', 'watch', 'nextTick', 'onMounted', 'aba', 'filtros', 'selecionada', 'emailPorLoja', 'larguraJanela', 'lojasRecolhidas', 'canSugerir', 'veAbaEmail', 'iniciou', 'auth', 'localStorage', 'carregarContagensEmail', 'carregarModelos', 'selecionar', 'emailSitesNaBarra', 'sitesSemANaoLida', 'emailsDoSite', 'gruposDoSite']
      const corpo = `${transpileTs(pagina.slice(ini, fim))}\nreturn { emailsDaLoja, emailDaCaixaAberto, emailDaCaixaLinha, abrirEmailDaLoja, fecharEmailDaLoja, emailNaCaixa, emailNaCaixaLinha, emailNaCaixaVersao, emailNaCaixaSinal, emailNaCaixaOrigem, emailsNoCelular, querPorLoja, contagemDaLoja, emailNaTela, abertaNaCaixa, abrirEmailsDaLoja, trocarGrupoDaLoja, abrirEmailDaBarra, fecharEmailsDaLoja, fecharEmailNaCaixa, selecionarEmailNaCaixa, abrirEmailAvulsoNaCaixa, voltarAoChatDaCaixa, aoMudarEmailNaCaixa, aoVincularEmailNaCaixa, lojasEmailsAbertos, gruposEmailsAbertos, chaveDosEmailsAbertos, chaveDosGruposAbertos }`
      const veAbaEmail = Vue.ref(admin)
      const out = new Function(...nomes, corpo)(
        Vue.ref, Vue.computed, Vue.watch, Vue.nextTick, (fn) => montados.push(fn), aba, filtros, selecionada, emailPorLoja, larguraJanela, lojasRecolhidas, Vue.ref(true), veAbaEmail, true, { user: usuario ? { id: usuario } : null }, localStorage,
        () => { lidas.contagens++ }, () => { lidas.modelos++ }, (id) => { lidas.selecionadas.push(id); selecionada.value = id },
        emailSitesNaBarra, LJ.sitesSemANaoLida, LJ.emailsDoSite, LJ.gruposDoSite,
      )
      return { ...out, aba, filtros, selecionada, larguraJanela, lojasRecolhidas, veAbaEmail, montados, lidas, guardado, emailSitesNaBarra, async montar() { for (const fn of montados) fn(); await Vue.nextTick(); await esperar() } }
    }
    function filtros0() { return filtros() }
    const ATV = { chave: 'i-sh', integration_id: 'i-sh', store_info_id: '', plataforma: 'shopee', conta: 'ATV', grupo: 'problemas' }
    // A bolinha da caixa do SITE é a não lida (27/10/2026): abrir apaga na hora; o visto gravado relê os números.
    {
      const caixas = [{ chave: 'sac', rotulo: 'SAC' }, { chave: 'admin', rotulo: 'Admin' }]
      const sites = [{ site: 'uranyx', externo_ref: 'site:uranyx', nome: 'Uranyx', pastas: ['*uranyx'], status: 'liga', caixas, emails_30d: 48, grupos: { sac: { emails: 48, abertas: 22, aguardando: 22, total: 22, nao_lidas: 2 } } }]
      const b = bloco({ sites })
      const U = { chave: 'ext:site:uranyx', integration_id: '', store_info_id: '', plataforma: 'site', conta: 'Uranyx', grupo: 'sac', site: 'uranyx' }
      assert.equal(LJ.esperandoDoGrupo(b.contagemDaLoja.value ?? LJ.emailsDoSite(sites[0]), 'sac'), 2)
      // Na lista curta da caixa (a barra): a conversa abre, a bolinha baixa.
      const curto = { id: 'm9', conversa_id: 'c9', nao_lida: true, aguardando_resposta: true }
      b.abrirEmailDaBarra(U, curto)
      assert.deepEqual([b.emailNaCaixa.value, curto.nao_lida, b.emailSitesNaBarra.value[0].grupos.sac.nao_lidas], ['c9', false, 1])
      assert.equal(LJ.esperandoDoGrupo(b.contagemDaLoja.value, 'sac'), 1, 'a bolinha do site na lista do meio também')
      assert.equal(b.emailSitesNaBarra.value[0].grupos.sac.aguardando, 22, 'o "falta responder" fica')
      // De novo (já lida) não baixa outra vez; na lista do meio, a outra baixa.
      b.abrirEmailDaBarra(U, curto)
      assert.equal(b.emailSitesNaBarra.value[0].grupos.sac.nao_lidas, 1)
      b.selecionarEmailNaCaixa('c8', { id: 'm8', conversa_id: 'c8', nao_lida: true })
      assert.deepEqual([b.emailNaCaixa.value, b.emailSitesNaBarra.value[0].grupos.sac.nao_lidas], ['c8', 0])
      b.selecionarEmailNaCaixa('c7', { id: 'm7', conversa_id: 'c7', nao_lida: true })
      assert.equal(b.emailSitesNaBarra.value[0].grupos.sac.nao_lidas, 0, 'nunca abaixo de zero')
      // A conversa aberta gravou o visto (POST /visto): os números são relidos.
      b.selecionarEmailNaCaixa('c9')
      b.aoMudarEmailNaCaixa({ id: 'c9', email: { tipo_email: 'chamado' }, aguardando_resposta: true, situacao: 'aberta', visto_em: null })
      const antes = b.lidas.contagens
      b.aoMudarEmailNaCaixa({ id: 'c9', email: { tipo_email: 'chamado' }, aguardando_resposta: true, situacao: 'aberta', visto_em: '2026-10-27T18:30:00Z' })
      assert.equal(b.lidas.contagens, antes + 1)
      // As puras: a não lida manda na bolinha do site; a loja (sem ela) segue o aguardando.
      assert.equal(LJ.esperandoDoGrupo({ grupos: { sac: { aguardando: 5, nao_lidas: 0 } } }, 'sac'), 0)
      assert.equal(LJ.esperandoDoGrupo({ grupos: { problemas: { aguardando: 5 } } }, 'problemas'), 5)
      assert.deepEqual(LJ.sitesSemANaoLida(sites, 'charlots', 'sac'), sites, 'outro site: igual')
    }
    {
      const b = bloco()
      b.selecionada.value = 'chat-1'
      b.abrirEmailsDaLoja(ATV)
      assert.equal(b.contagemDaLoja.value.integration_id, 'i-sh', 'os números da loja escolhida')
      assert.equal(b.abertaNaCaixa.value, null, 'o chat guardado não abre por cima da lista de e-mails')
      assert.equal(b.emailNaTela.value, true)
      b.selecionarEmailNaCaixa('e-1')
      assert.equal(b.abertaNaCaixa.value, 'e-1')
      await esperar()
      assert.equal(b.lidas.modelos, 1, 'as respostas prontas para a conversa de e-mail')
      // A linha da conversa: mudou o que a lista e os números mostram → relê.
      b.aoMudarEmailNaCaixa({ id: 'e-1', email: { tipo_email: 'chamado' }, aguardando_resposta: true, situacao: 'aberta' })
      assert.equal(b.lidas.contagens, 0, 'a primeira linha só guarda')
      b.aoMudarEmailNaCaixa({ id: 'e-1', email: { tipo_email: 'chamado' }, aguardando_resposta: false, situacao: 'respondida' })
      assert.deepEqual([b.lidas.contagens, b.emailNaCaixaSinal.value], [1, 1])
      b.aoMudarEmailNaCaixa({ id: 'outra', email: {}, aguardando_resposta: true, situacao: 'aberta' })
      assert.equal(b.lidas.contagens, 1, 'a linha de outra conversa não conta')
      // Vinculou: as mensagens foram para a conversa de e-mail do pedido.
      b.aoVincularEmailNaCaixa('e-pedido')
      assert.equal(b.emailNaCaixa.value, 'e-pedido')
      b.aoVincularEmailNaCaixa('e-pedido')
      assert.equal(b.emailNaCaixaVersao.value, 1, 'na mesma: relê do zero')
      // Outra pasta da loja: a conversa aberta fecha, a loja fica.
      b.trocarGrupoDaLoja('devolucao')
      assert.deepEqual([b.emailsDaLoja.value.grupo, b.emailNaCaixa.value], ['devolucao', null])
      // Um e-mail da lista curta da barra: a lista da loja naquela pasta (a
      // linha acesa) e o e-mail na área principal — ou a conversa dele.
      const m1 = { id: 'm1', conversa_id: null, assunto: 'Etiqueta' }
      b.abrirEmailDaBarra(ATV, m1)
      assert.deepEqual([b.emailsDaLoja.value.grupo, b.emailDaCaixaAberto.value, b.emailDaCaixaLinha.value, b.emailNaCaixa.value], ['problemas', 'm1', m1, null])
      assert.equal(b.abertaNaCaixa.value, 'm1', 'no celular a lista some: o e-mail na tela')
      b.abrirEmailDaBarra({ ...ATV, grupo: 'envios' }, { id: 'm2', conversa_id: 'e-7' })
      assert.deepEqual([b.emailsDaLoja.value.grupo, b.emailDaCaixaAberto.value, b.emailNaCaixa.value], ['envios', null, 'e-7'])
      b.trocarGrupoDaLoja('problemas')
      // O e-mail sem conversa da lista do meio: na área principal; a conversa aberta fecha.
      b.selecionarEmailNaCaixa('e-3')
      b.abrirEmailDaLoja(m1)
      assert.deepEqual([b.emailDaCaixaAberto.value, b.emailNaCaixa.value, b.abertaNaCaixa.value], ['m1', null, 'm1'])
      // "fechar o e-mail": volta ao "Escolha um e-mail na lista" — a lista da loja fica.
      b.fecharEmailDaLoja()
      assert.deepEqual([b.emailDaCaixaAberto.value, b.emailDaCaixaLinha.value, b.emailsDaLoja.value?.chave, b.abertaNaCaixa.value], [null, null, 'i-sh', null])
      // Abrir uma conversa (da lista ou da barra) fecha o e-mail aberto.
      b.abrirEmailDaLoja(m1)
      b.abrirEmailDaLoja({ id: 'm4', conversa_id: 'e-8' })
      assert.deepEqual([b.emailDaCaixaAberto.value, b.emailDaCaixaLinha.value, b.emailNaCaixa.value], [null, null, 'e-8'])
      b.abrirEmailDaLoja(m1)
      b.selecionarEmailNaCaixa('e-8')
      assert.deepEqual([b.emailDaCaixaAberto.value, b.emailNaCaixa.value], [null, 'e-8'])
      // A MESMA loja e pasta de novo (o "ver todos", o clique na caixa): o e-mail fica aberto.
      b.abrirEmailDaLoja(m1)
      b.abrirEmailsDaLoja(ATV)
      assert.equal(b.emailDaCaixaAberto.value, 'm1')
      // Outra pasta ou outra loja: fecha.
      b.trocarGrupoDaLoja('envios')
      assert.deepEqual([b.emailDaCaixaAberto.value, b.emailDaCaixaLinha.value], [null, null])
      b.abrirEmailDaLoja(m1)
      b.abrirEmailsDaLoja({ ...ATV, chave: 'i-ml', integration_id: 'i-ml', conta: 'Poofy', grupo: 'envios' })
      assert.equal(b.emailDaCaixaAberto.value, null, 'outra loja')
      b.abrirEmailsDaLoja(ATV)
      b.abrirEmailDaLoja(m1)
      // "conversas" (voltar ao chat): fecha tudo.
      b.fecharEmailsDaLoja()
      assert.deepEqual([b.emailsDaLoja.value, b.emailDaCaixaAberto.value, b.emailDaCaixaLinha.value], [null, null, null])
      b.abrirEmailsDaLoja(ATV)
      b.trocarGrupoDaLoja('problemas')
      // Um link de dentro do e-mail que aponta um CHAT: vai para a lista do chat, aberto.
      b.selecionarEmailNaCaixa('e-2')
      b.aoMudarEmailNaCaixa({ id: 'e-2', email: null })
      assert.deepEqual([b.emailsDaLoja.value, b.emailNaCaixa.value, b.selecionada.value], [null, null, 'e-2'])
      // Um filtro de LOJA do chat (a loja na barra, o menu Plataforma): a lista do chat de volta.
      b.abrirEmailsDaLoja(ATV)
      b.filtros.value = { ...b.filtros.value, integration_id: 'i-sh' }
      await Vue.nextTick()
      assert.equal(b.emailsDaLoja.value, null)
      // A aba da lista (Todas, Falta responder) não fecha.
      b.abrirEmailsDaLoja(ATV)
      b.filtros.value = { ...b.filtros.value, filtro: 'aguardando' }
      await Vue.nextTick()
      assert.equal(b.emailsDaLoja.value?.chave, 'i-sh')
      // Saiu da Caixa (a Caixa Humano, a aba E-mail): fecha.
      b.aba.value = 'humano'
      await Vue.nextTick()
      assert.equal(b.emailsDaLoja.value, null)
    }
    {
      // Os números por loja só com onde mostrar: a barra à mostra e aberta, a lista da loja, o painel do celular.
      const b = bloco()
      assert.equal(b.querPorLoja.value, true, 'a Caixa com a barra aberta')
      b.lojasRecolhidas.value = true
      assert.equal(b.querPorLoja.value, false, 'recolhida não tem seta')
      b.abrirEmailsDaLoja(ATV)
      assert.equal(b.querPorLoja.value, true, 'os e-mails de uma loja na tela (os chips)')
      b.fecharEmailsDaLoja()
      b.lojasRecolhidas.value = false
      b.larguraJanela.value = 800
      await Vue.nextTick()
      assert.equal(b.querPorLoja.value, false, 'abaixo do lg a barra é escondida')
      b.emailsNoCelular.value = true
      assert.equal(b.querPorLoja.value, true, 'o painel "E-mails das lojas" aberto')
      const antes = b.lidas.contagens
      b.larguraJanela.value = 1440
      await Vue.nextTick()
      assert.equal(b.emailsNoCelular.value, false, 'a barra apareceu: o painel do celular fecha')
      b.aba.value = 'humano'
      assert.equal(b.querPorLoja.value, false, 'a Caixa Humano não tem e-mail')
      await Vue.nextTick()
      b.aba.value = 'caixa'
      await Vue.nextTick()
      assert.ok(b.lidas.contagens > antes, 'voltou à Caixa: relê os números por loja')
      // A pasta escolhida no celular abre a lista da loja e fecha o painel.
      b.larguraJanela.value = 800
      await Vue.nextTick()
      b.emailsNoCelular.value = true
      b.abrirEmailsDaLoja(ATV)
      assert.deepEqual([b.emailsNoCelular.value, b.emailsDaLoja.value?.chave], [false, 'i-sh'])
    }
    {
      // A conversa de e-mail SEM a lista da loja (quem não é admin: o link do chat, o alerta): na Caixa.
      const b = bloco()
      b.selecionada.value = 'chat-1'
      b.aba.value = 'humano'
      await Vue.nextTick()
      b.abrirEmailAvulsoNaCaixa('e-9', { id: 'chat-1', aba: 'humano' })
      await Vue.nextTick()
      assert.deepEqual([b.aba.value, b.emailNaCaixa.value, b.emailsDaLoja.value, b.abertaNaCaixa.value, b.emailNaTela.value], ['caixa', 'e-9', null, 'e-9', true])
      // "voltar ao chat": o chat de onde saiu, na aba de onde saiu.
      b.voltarAoChatDaCaixa()
      assert.deepEqual([b.emailNaCaixa.value, b.emailNaCaixaOrigem.value, b.aba.value, b.lidas.selecionadas.at(-1)], [null, null, 'humano', 'chat-1'])
      // Uma pasta na barra com um e-mail avulso aberto: a lista da loja, sem o e-mail de antes.
      b.aba.value = 'caixa'
      await Vue.nextTick()
      b.abrirEmailAvulsoNaCaixa('e-9', { id: 'chat-1', aba: 'caixa' })
      b.abrirEmailsDaLoja(ATV)
      assert.deepEqual([b.emailNaCaixa.value, b.emailsDaLoja.value?.chave], [null, 'i-sh'])
      // Deixou de ver a aba E-mail (admin ou a permissão) com ela aberta: volta para a Caixa.
      b.veAbaEmail.value = true
      b.aba.value = 'mail'
      await Vue.nextTick()
      b.veAbaEmail.value = false
      await Vue.nextTick()
      assert.equal(b.aba.value, 'caixa')
    }
    {
      // As lojas abertas: POR PESSOA (a chave leva o id), lidas no onMounted, gravadas no clique.
      const guardado = new Map([['davinci.atendimento.lojasEmailsAbertos:u1', JSON.stringify(['i-sh', 'i-sh', 7, '', 'semid:temu:barbosa'])], ['davinci.atendimento.lojasEmailsAbertos:u2', JSON.stringify(['i-ml'])]])
      const b = bloco({ guardado })
      assert.equal(b.chaveDosEmailsAbertos(), 'davinci.atendimento.lojasEmailsAbertos:u1')
      await b.montar()
      assert.deepEqual(b.lojasEmailsAbertos.value, ['i-sh', 'semid:temu:barbosa'], 'só textos, sem repetir; a da outra pessoa não vem')
      b.lojasEmailsAbertos.value = ['i-sh']
      await Vue.nextTick()
      assert.equal(guardado.get('davinci.atendimento.lojasEmailsAbertos:u1'), '["i-sh"]')
      assert.equal(guardado.get('davinci.atendimento.lojasEmailsAbertos:u2'), '["i-ml"]', 'a da outra pessoa não muda')
      // Valor estragado e armazenamento bloqueado (aba anônima): começa fechado, sem quebrar.
      const estragado = bloco({ guardado: new Map([['davinci.atendimento.lojasEmailsAbertos:u1', '{oops']]) })
      await estragado.montar()
      assert.deepEqual(estragado.lojasEmailsAbertos.value, [])
      const bloqueado = bloco({ quebrado: true })
      await bloqueado.montar()
      assert.deepEqual(bloqueado.lojasEmailsAbertos.value, [])
      bloqueado.lojasEmailsAbertos.value = ['i-sh']
      await Vue.nextTick()
      assert.deepEqual(bloqueado.lojasEmailsAbertos.value, ['i-sh'], 'só não lembra')
    }
    {
      // As CAIXAS abertas (`loja|pasta`): também POR PESSOA, na chave própria.
      const guardado = new Map([['davinci.atendimento.gruposEmailsAbertos:u1', JSON.stringify(['i-sh|problemas', 'i-sh|problemas', 3])]])
      const b = bloco({ guardado })
      assert.equal(b.chaveDosGruposAbertos(), 'davinci.atendimento.gruposEmailsAbertos:u1')
      await b.montar()
      assert.deepEqual(b.gruposEmailsAbertos.value, ['i-sh|problemas'])
      b.gruposEmailsAbertos.value = ['i-sh|problemas', 'i-sh|envios']
      await Vue.nextTick()
      assert.equal(guardado.get('davinci.atendimento.gruposEmailsAbertos:u1'), '["i-sh|problemas","i-sh|envios"]')
      assert.equal(guardado.has('davinci.atendimento.lojasEmailsAbertos:u1'), false, 'as lojas abertas não mudaram')
    }
  }

  // ------------------------------------------------ a barra da conversa de e-mail na Caixa
  {
    const naCaixa = montar('../components/AtendimentoEmailConversaBarra.vue', { conversa: null, podeMexer: false, naCaixa: true })
    let html = await naCaixa.html()
    assert.match(html, /E-mail da loja, aberto pela barra de lojas: fica fora da lista e dos números do chat\./)
    assert.match(html, /voltar aos e-mails/)
    // Sem a lista da loja (quem não é admin, pelo link do chat): fechar o e-mail e voltar ao chat.
    const avulsa = montar('../components/AtendimentoEmailConversaBarra.vue', { conversa: null, podeMexer: false, naCaixa: true, semLista: true, comChat: true })
    html = await avulsa.html()
    assert.match(html, /data-voltar-emails>[\s\S]*?fechar o e-mail/)
    assert.match(html, /data-voltar-chat/)
    assert.match(html, /Conversa de e-mail aberta aqui na Caixa: fica fora da lista e dos números do chat\./)
    const naAba = montar('../components/AtendimentoEmailConversaBarra.vue', { conversa: null, podeMexer: false })
    assert.match(await naAba.html(), /Conversa de e-mail: fica só aqui, na aba E-mail \(não aparece na Caixa do chat\)\./, 'na aba E-mail, o aviso de sempre')
  }

  // ------------------------------------------------ o contrato com a API
  {
    const api = (rel) => fs.readFileSync(path.resolve(__dirname, '../../api/app', rel), 'utf8')
    const email = api('routers/atendimento_email.py')
    assert.match(email, /por_loja: Annotated\[bool, Query\(\)\] = False/)
    assert.match(email, /async def _contagens_por_loja\(/)
    assert.match(email, /"grupos_na_barra": \[/)
    assert.match(email, /@router\.get\("\/lojas\/emails"\)/)
    assert.match(email, /@router\.get\("\/lojas\/emails\/\{message_id\}"\)/)
    assert.match(email, /@router\.get\("\/lojas\/anexos\/\{attachment_id\}"\)/)
    assert.match(email, /integration_id: Annotated\[UUID \| None, Query\(\)\] = None,\n    store_info_id: Annotated\[UUID \| None, Query\(\)\] = None,\n\) -> dict\[str, Any\]:\n    """Uma fila:/)
    assert.match(api('routers/atendimento.py'), /store_info_id: Annotated\[UUID \| None, Query\(\)\] = None,\n    grupo_email: Annotated\[str \| None, Query\(max_length=32\)\] = None,\n\) -> ListaConversasOut:/)
    // A escolha do dono: UMA constante, na API (o web não tem lista própria de quais pastas aparecem).
    assert.match(api('services/mail_atendimento/constantes.py'), /GRUPOS_EMAIL_NA_BARRA_DE_LOJAS: tuple\[tuple\[str, str, tuple\[str, \.\.\.\]\], \.\.\.\] = \(\n    \("problemas", "Problemas", \("problemas",\)\),\n    \("envios", "Envios", \("envio",\)\),\n    \("devolucao", "Devolução", \("devoluçoes", "devolucoes", "devoluções"\)\),\n    \("informativo", "Informativo", \("informativo",\)\),\n\)/)
    assert.doesNotMatch(web('components/AtendimentoLojas.vue'), /'problemas'|'envios'|'devolucao'|GRUPOS_NA_BARRA\s*=/)
    // O e-mail continua fora da Caixa: a barra e a lista do chat não perguntam pelo e-mail.
    assert.doesNotMatch(web('components/AtendimentoLista.vue'), /caixa=email|por_loja/)
  }
  console.log('ok — atendimento-email-lojas')
}

principal().catch((e) => { console.error(e); process.exit(1) })
