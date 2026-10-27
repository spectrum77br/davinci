// node tests/atendimento-email-lojas.cjs — os E-MAILS de cada loja na barra de
// lojas da Caixa (27/10/2026). O Eduardo: "botar um AMPLIÁVEL que vai abrir os
// e-mails embaixo de cada loja … então poderá ampliar e visualizar os e-mails
// ali pelo atendimento mesmo de cada loja". No mesmo dia: embaixo de cada loja
// só Informativo e Chamado (a Venda fica na aba E-mail; a escolha é UMA
// constante da API — TIPOS_EMAIL_NA_BARRA_DE_LOJAS — que chega em
// `tipos_na_barra`) e a aba E-mail só para admin (quem não é admin vê e-mail
// SÓ pela barra). Sem rede e sem e-mail de verdade:
//  - as funções puras (qual linha da barra tem e-mail, os tipos da barra, o
//    número e a bolinha de cada tipo, as lojas "só e-mail", as URLs da lista
//    da loja — nunca a lista de todas as lojas);
//  - a barra renderizada (Vue SSR, setup de verdade): a seta só na loja com
//    e-mail nos tipos da barra e só na barra aberta; sem a Venda; a linha "só
//    e-mail"; sem `emails`, a barra de sempre (o mesmo HTML); com os e-mails de
//    uma loja na Caixa, nenhuma linha do chat acesa (recolhida: o ícone dela);
//    o modo "emails" do celular;
//  - a lista da loja (setup de verdade, API falsa): a rota de "Todas as
//    conversas" da aba E-mail com a loja e o tipo; os chips = os tipos da
//    barra; abertas/fechadas, busca, tique; as linhas só para quem mexe;
//  - a página: a aba E-mail só para admin (e o `?tab=mail`), a conversa de
//    e-mail de quem não é admin na Caixa, os números por loja só com a barra à
//    mostra, a barra, a lista e a conversa ligadas; o chat guardado; as lojas
//    abertas lembradas POR PESSOA (localStorage com try).
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
        const v = typeof valor === 'function' ? valor(url, opts) : valor
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
  return semComentarios(await renderToString(app))
}

const LJ = requerer('~/components/AtendimentoLojas.vue')
const LE = requerer('~/components/AtendimentoLojaEmails.vue')

const conta = (abertas, aguardando = 0, total = abertas, linhas = 0) => ({ abertas, aguardando, total, linhas })
// Os tipos da barra (a API manda: a escolha do dono mora lá).
const NA_BARRA = ['informativo', 'chamado']
const POR_LOJA = [
  { integration_id: 'i-sh', store_info_id: null, plataforma: 'shopee', loja: 'ATV', tipos: { informativo: conta(239, 0, 240), chamado: conta(37, 2) } },
  // Só fechadas (ou só Venda, que a API nem manda): sem seta.
  { integration_id: 'i-ml', store_info_id: null, plataforma: 'ml', loja: 'Poofy', tipos: { informativo: conta(0, 0, 3), chamado: conta(0) } },
  // A loja do robô (sem integração): pela ficha de mesma plataforma e mesmo nome.
  { integration_id: null, store_info_id: 'f-temu', plataforma: 'temu', loja: 'Barbosa', tipos: { informativo: conta(40), chamado: conta(0) } },
  // Duas fichas "Eron" na Temu: ambígua — a linha do robô sem seta; as duas viram "só e-mail".
  { integration_id: null, store_info_id: 'f-eron1', plataforma: 'temu', loja: 'eron', tipos: { chamado: conta(3) } },
  { integration_id: null, store_info_id: 'f-eron2', plataforma: 'temu', loja: 'Éron', tipos: { chamado: conta(2) } },
  // A ficha sem integração numa plataforma com integração (a "luno" da Shopee): "só e-mail".
  { integration_id: null, store_info_id: 'f-luno', plataforma: 'shopee', loja: 'luno', tipos: { informativo: conta(233), chamado: conta(0) } },
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

async function principal() {
  // ------------------------------------------------ as funções puras da barra
  {
    const linha = (o) => ({ chave: o.integration_id || `semid:${o.plataforma}:${o.conta}`, externo_ref: '', rede_social_id: '', ...o })
    // Os rótulos de todos os tipos; QUAIS aparecem vem da API, na ordem dela.
    assert.deepEqual(LJ.TIPOS_EMAIL_DA_LOJA.map((t) => t.label), ['Informativo', 'Chamado', 'Venda'])
    assert.deepEqual(LJ.tiposDaBarra(NA_BARRA).map((t) => t.label), ['Informativo', 'Chamado'])
    assert.deepEqual(LJ.tiposDaBarra(['chamado', 'x', 'chamado']).map((t) => t.value), ['chamado'])
    assert.deepEqual(LJ.tiposDaBarra(null), [], 'a API antiga: nenhum tipo (sem seta)')
    // Pela integração.
    const atv = LJ.emailsDaLoja(POR_LOJA, linha({ integration_id: 'i-sh', plataforma: 'shopee', conta: 'ATV' }), NA_BARRA)
    assert.equal(atv.integration_id, 'i-sh')
    // Só fechadas, sem linha na resposta ou sem os tipos: sem seta.
    assert.equal(LJ.emailsDaLoja(POR_LOJA, linha({ integration_id: 'i-ml', plataforma: 'ml', conta: 'Poofy' }), NA_BARRA), null)
    assert.equal(LJ.emailsDaLoja(POR_LOJA, linha({ integration_id: 'i-sh2', plataforma: 'shopee', conta: 'Aguiar' }), NA_BARRA), null)
    assert.equal(LJ.emailsDaLoja(null, linha({ integration_id: 'i-sh', plataforma: 'shopee', conta: 'ATV' }), NA_BARRA), null)
    assert.equal(LJ.emailsDaLoja(POR_LOJA, linha({ integration_id: 'i-sh', plataforma: 'shopee', conta: 'ATV' }), []), null)
    // A loja só com Venda (o número da Venda veio de uma API que ainda manda): sem seta.
    const soVenda = [{ integration_id: 'i-v', store_info_id: null, plataforma: 'ml', loja: 'V', tipos: { venda: conta(9), informativo: conta(0), chamado: conta(0) } }]
    assert.equal(LJ.emailsDaLoja(soVenda, linha({ integration_id: 'i-v', plataforma: 'ml', conta: 'V' }), NA_BARRA), null, 'a Venda não conta')
    assert.equal(LJ.temEmailNaBarra(soVenda[0], ['venda']), true, 'a escolha do dono troca na API')
    // A loja do robô: a ficha de mesma plataforma e mesmo nome (sem acento/caixa).
    assert.equal(LJ.emailsDaLoja(POR_LOJA, linha({ integration_id: '', plataforma: 'temu', conta: ' barbosa ' }), NA_BARRA).store_info_id, 'f-temu')
    assert.equal(LJ.emailsDaLoja(POR_LOJA, linha({ integration_id: '', plataforma: 'aliexpress', conta: 'Barbosa' }), NA_BARRA), null, 'outra plataforma')
    assert.equal(LJ.emailsDaLoja(POR_LOJA, linha({ integration_id: '', plataforma: 'temu', conta: 'Eron' }), NA_BARRA), null, 'duas fichas iguais: nunca a errada')
    // O site e as contas de rede não têm loja de e-mail na barra.
    assert.equal(LJ.emailsDaLoja(POR_LOJA, linha({ integration_id: '', plataforma: 'temu', conta: 'Barbosa', externo_ref: 'site:x' }), NA_BARRA), null)
    assert.equal(LJ.emailsDaLoja(POR_LOJA, linha({ integration_id: '', plataforma: 'temu', conta: 'Barbosa', rede_social_id: 'r1' }), NA_BARRA), null)
    // As lojas com e-mail SEM linha na barra: a "luno" e as duas "Eron" (nunca a ATV nem a Barbosa da Temu).
    const linhas = [linha({ integration_id: 'i-sh', plataforma: 'shopee', conta: 'ATV' }), linha({ integration_id: '', plataforma: 'temu', conta: 'Barbosa' }), linha({ integration_id: '', plataforma: 'temu', conta: 'Eron' })]
    assert.deepEqual(LJ.lojasSemLinha(POR_LOJA, linhas, NA_BARRA).map((e) => e.store_info_id || e.integration_id), ['f-eron1', 'f-eron2', 'f-luno'])
    assert.deepEqual(LJ.lojasSemLinha(POR_LOJA, linhas, []), [], 'sem tipos: nada')
    assert.equal(LJ.chaveSoEmail({ integration_id: null, store_info_id: 'f-luno' }), 'email:f:f-luno')
    assert.equal(LJ.chaveSoEmail({ integration_id: 'i-x', store_info_id: null }), 'email:i:i-x')
    // O número do tipo = o que a lista mostra (abertas + as linhas informativas de quem mexe).
    const comLinhas = { ...POR_LOJA[0], tipos: { ...POR_LOJA[0].tipos, informativo: conta(239, 0, 240, 4) } }
    assert.deepEqual(LJ.tiposDaBarra(NA_BARRA).map((t) => LJ.numeroDoTipo(comLinhas, t.value)), [243, 37])
    assert.equal(LJ.numeroDoTipo(null, 'chamado'), 0)
    assert.equal(LJ.esperandoDoTipo(atv, 'chamado'), 2)
    assert.equal(LJ.esperandoDoTipo(atv, 'informativo'), 0)
    assert.equal(LJ.tituloDoTipo(atv, 'chamado', 'ATV'), 'Chamado · ATV: 37 conversa(s) de e-mail aberta(s) · 2 esperando resposta\nAbre os e-mails desta loja aqui na Caixa')
    assert.match(LJ.tituloDoTipo(atv, 'informativo', 'ATV'), /239 conversa\(s\) de e-mail aberta\(s\) · 1 fechada\(s\)/)
    assert.equal(LJ.tituloDoAmpliavel(atv, 'ATV', false, NA_BARRA), 'Mostrar os e-mails da loja ATV (Informativo 239 · Chamado 37)')
    assert.match(LJ.tituloDoAmpliavel(atv, 'ATV', true, NA_BARRA), /^Esconder/)
    // O clique: a loja (integração OU ficha, nunca as duas) e o tipo.
    assert.deepEqual(LJ.escolhaDoEmail(linha({ integration_id: 'i-sh', plataforma: 'shopee', conta: 'ATV' }), atv, 'chamado'), { chave: 'i-sh', integration_id: 'i-sh', store_info_id: '', plataforma: 'shopee', conta: 'ATV', tipo: 'chamado' })
    const temu = LJ.emailsDaLoja(POR_LOJA, linha({ integration_id: '', plataforma: 'temu', conta: 'barbosa' }), NA_BARRA)
    assert.deepEqual(LJ.escolhaDoEmail(linha({ integration_id: '', plataforma: 'temu', conta: 'barbosa' }), temu, 'informativo'), { chave: 'semid:temu:barbosa', integration_id: '', store_info_id: 'f-temu', plataforma: 'temu', conta: 'barbosa', tipo: 'informativo' })
    assert.deepEqual(LJ.alternarAberta([], 'a'), ['a'])
    assert.deepEqual(LJ.alternarAberta(['a', 'b'], 'a'), ['b'])
  }

  // ------------------------------------------------ a barra renderizada
  {
    const base = { resumo: RESUMO, filtros: filtros(), numero: 'nao_lidas_duoke' }
    const com = (o = {}) => ({ ...base, emails: POR_LOJA, tiposEmail: NA_BARRA, ...o })
    const sem = await renderizarBarra(base)
    // Sem `emails` (a Caixa Humano, a API antiga): nenhuma seta nem linha "só e-mail" — e o MESMO HTML com `emails: null`.
    assert.doesNotMatch(sem, /data-ampliar-emails|data-emails-loja|data-so-email/)
    assert.equal(await renderizarBarra({ ...base, emails: null, tiposEmail: NA_BARRA }), sem)
    // Com os números: a seta só na loja com e-mail nos tipos da barra (ATV e a Barbosa da Temu), fechada.
    let html = await renderizarBarra(com())
    assert.deepEqual([...html.matchAll(/data-ampliar-emails="([^"]+)"/g)].map((m) => m[1]), ['i-sh', 'semid:temu:barbosa'])
    assert.match(html, /aria-expanded="false" aria-label="Mostrar os e-mails da loja ATV \(Informativo 239 · Chamado 37\)"/)
    assert.doesNotMatch(html, /aria-controls="emails-loja-i-sh"/, 'fechada: o aria-controls não aponta para o que não existe')
    assert.match(html, /data-ampliar-emails="i-sh"/)
    assert.match((html.match(/<button[^>]*data-ampliar-emails="i-sh"[^>]*>/) || [''])[0], /focus-visible:ring-2/)
    assert.doesNotMatch(html, /data-emails-loja=/, 'fechada: sem os tipos')
    assert.doesNotMatch(html, /Venda/, 'a Venda não aparece na barra')
    // A linha da loja continua igual (o número do Duoke, o clique filtra o chat); só abre espaço para a seta.
    assert.match(html, /<button type="button" class="relative mx-1 [^"]*pr-7"[^>]*title="Shopee · ATV[^"]*">[\s\S]*?>71<\/span><\/button>/)
    assert.doesNotMatch((html.match(/<button type="button" class="relative mx-1 [^"]*"[^>]*title="Shopee · Aguiar/) || [''])[0], /pr-7/)
    assert.doesNotMatch((html.match(/<button type="button" class="relative mx-1 [^"]*"[^>]*title="Mercado Livre · Poofy/) || [''])[0], /pr-7/, 'só fechadas: sem seta')
    // As lojas "só e-mail" (sem linha de chat): no grupo da plataforma, sem número, com o Mail.
    assert.deepEqual([...html.matchAll(/data-so-email="([^"]+)"/g)].map((m) => m[1]).sort(), ['email:f:f-eron1', 'email:f:f-eron2', 'email:f:f-luno'])
    const luno = (html.match(/<button[^>]*data-so-email="email:f:f-luno"[\s\S]*?<\/button>/) || [''])[0]
    assert.match(luno, /aria-expanded="false"/)
    assert.match(luno, /title="Shopee · luno\nSó e-mail: a loja não tem chat no DaVinci/)
    assert.match(luno, />luno<\/span>[\s\S]*data-icone="Mail"/)
    assert.doesNotMatch(luno, /aria-pressed|bg-red-500/, 'sem número de chat nem filtro')
    // A Shopee: Aguiar, ATV e a luno no mesmo grupo, pelo nome.
    const shopee = html.slice(html.indexOf('data-grupo="shopee"'), html.indexOf('data-grupo="ml"'))
    assert.match(shopee, /Shopee · Aguiar[\s\S]*Shopee · ATV[\s\S]*Shopee · luno/)
    // Aberta: os tipos da barra, com o número; a bolinha vermelha só onde há e-mail esperando resposta.
    html = await renderizarBarra(com({ emailsAbertos: ['i-sh'] }))
    assert.match(html, /aria-expanded="true" aria-controls="emails-loja-i-sh"/)
    const grupo = (html.match(/<div id="emails-loja-i-sh" role="group" aria-label="E-mails da loja ATV"[\s\S]*?<\/div>/) || [''])[0]
    assert.ok(grupo, 'o grupo dos tipos embaixo da loja')
    const tipos = [...grupo.matchAll(/data-email-tipo="([^"]+)"[^>]*>([\s\S]*?)<\/button>/g)].map((m) => [m[1], m[2].replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()])
    assert.deepEqual(tipos, [['informativo', 'Informativo 239'], ['chamado', 'Chamado 2 37']], 'só Informativo e Chamado; a bolinha (2) só no Chamado')
    assert.equal((grupo.match(/data-email-esperando/g) || []).length, 1)
    assert.match(grupo, /title="Chamado · ATV: 37 conversa\(s\) de e-mail aberta\(s\) · 2 esperando resposta\nAbre os e-mails desta loja aqui na Caixa"/)
    assert.match(grupo, /focus-visible:ring-2/)
    assert.match(grupo, /aria-pressed="false"/)
    // A escolha do dono é da API: com ['chamado'], só o Chamado.
    html = await renderizarBarra(com({ emailsAbertos: ['i-sh'], tiposEmail: ['chamado'] }))
    assert.deepEqual([...html.matchAll(/data-email-tipo="([^"]+)"/g)].map((m) => m[1]), ['chamado'])
    // A loja do robô aberta (a chave tem ":"): o id do grupo é seguro.
    html = await renderizarBarra(com({ emailsAbertos: ['semid:temu:barbosa'] }))
    assert.match(html, /id="emails-loja-semid_temu_barbosa"/)
    assert.match(html, /data-email-tipo="chamado"[^>]*>[\s\S]*?>0<\/span>/, 'tipo sem e-mail: 0, apagado')
    // A "só e-mail" aberta: o próprio clique abriu, os tipos embaixo.
    html = await renderizarBarra(com({ emailsAbertos: ['email:f:f-luno'] }))
    assert.match(html, /aria-expanded="true" aria-controls="emails-loja-email_f_f-luno"[^>]*data-so-email="email:f:f-luno"/)
    assert.match(html, /id="emails-loja-email_f_f-luno" role="group"[\s\S]*?data-email-tipo="informativo"[\s\S]*?>233<\/span>/)
    // Recolhida: não cabe — sem seta nem tipos (a lembrança fica para quando abrir), sem as "só e-mail".
    html = await renderizarBarra(com({ emailsAbertos: ['i-sh'], recolhida: true }))
    assert.doesNotMatch(html, /data-ampliar-emails|data-emails-loja|data-so-email/)
    // Recolhida com os e-mails de uma loja na Caixa: o ícone dela aceso (onde se está).
    html = await renderizarBarra(com({ recolhida: true, emailAberto: { chave: 'i-sh', tipo: 'chamado' } }))
    assert.match(html, /<button type="button" class="relative mx-1[^"]*bg-primary\/10 text-primary ring-1[^"]*"[^>]*aria-current="true" title="Shopee · ATV/)
    html = await renderizarBarra(com({ recolhida: true, emailAberto: { chave: 'email:f:f-luno', tipo: 'informativo' } }))
    assert.deepEqual([...html.matchAll(/data-so-email="([^"]+)"/g)].map((m) => m[1]), ['email:f:f-luno'], 'só a "só e-mail" aberta')
    // Os e-mails de uma loja na Caixa: aceso fica o tipo, nenhuma linha do chat.
    html = await renderizarBarra(com({ filtros: filtros({ plataforma: 'shopee', integration_id: 'i-sh' }), emailsAbertos: ['i-sh'], emailAberto: { chave: 'i-sh', tipo: 'chamado' } }))
    assert.match(html, /aria-pressed="true"[^>]*data-email-tipo="chamado"/)
    assert.doesNotMatch(html, /<button type="button" class="relative mx-1[^"]*"[^>]*aria-pressed="true"/, 'nenhuma loja do chat acesa')
    assert.match(html, /aria-pressed="false" title="Todas as lojas/)
    // Sem os e-mails na Caixa: a loja do filtro acesa, como sempre.
    html = await renderizarBarra(com({ filtros: filtros({ plataforma: 'shopee', integration_id: 'i-sh' }) }))
    assert.match(html, /<button type="button" class="relative mx-1[^"]*"[^>]*aria-pressed="true" title="Shopee · ATV/)
    // A Caixa Humano (sem `emails`): nada de e-mail.
    assert.doesNotMatch(await renderizarBarra({ ...base, numero: 'humano', emails: null }), /data-so-email|data-ampliar-emails/)
    // O modo "emails" (celular e tablet): só as lojas com e-mail, sem "Todas", sem números de chat.
    html = await renderizarBarra(com({ modo: 'emails', emailsAbertos: ['i-sh'] }))
    assert.match(html, />E-mails das lojas</)
    assert.doesNotMatch(html, /data-lojas-todas|recolher a barra|Aguiar|Poofy|>71</)
    assert.match(html, /title="Shopee · ATV\nO clique mostra os e-mails desta loja\."/)
    assert.match(html, /aria-expanded="true" aria-controls="emails-loja-i-sh"/)
    assert.match(html, /data-so-email="email:f:f-luno"/)
    assert.doesNotMatch(html, /data-ampliar-emails/, 'o clique na linha já abre')
    // Sem nenhuma loja com e-mail: o vazio explica.
    html = await renderizarBarra({ ...base, modo: 'emails', emails: [], tiposEmail: NA_BARRA })
    assert.match(html, /data-sem-emails-de-loja>\s*Nenhuma loja com e-mail para mostrar agora\./)
  }

  // ------------------------------------------------ a barra: o clique (setup de verdade)
  {
    const b = montar('../components/AtendimentoLojas.vue', { resumo: RESUMO, filtros: filtros(), numero: 'nao_lidas_duoke', emails: POR_LOJA, tiposEmail: NA_BARRA, emailsAbertos: [] })
    const atv = b.estado.lojas.find((l) => l.integration_id === 'i-sh')
    assert.ok(b.estado.emailsDe(atv))
    b.estado.alternarEmails(atv)
    assert.deepEqual(b.estado.emailsAbertos, ['i-sh'])
    b.estado.alternarEmails(atv)
    assert.deepEqual(b.estado.emailsAbertos, [])
    b.estado.abrirEmails(atv, 'chamado')
    assert.deepEqual(b.emitidos, [['abrirEmails', { chave: 'i-sh', integration_id: 'i-sh', store_info_id: '', plataforma: 'shopee', conta: 'ATV', tipo: 'chamado' }]])
    // Loja sem e-mail: o clique não manda nada.
    b.estado.abrirEmails(b.estado.lojas.find((l) => l.integration_id === 'i-sh2'), 'chamado')
    assert.equal(b.emitidos.length, 1)
    // A "só e-mail": o clique na linha abre e fecha (nunca mexe no filtro do chat); o tipo manda a ficha.
    const luno = b.estado.lojasSoEmail.find((l) => l.chave === 'email:f:f-luno')
    const antes = JSON.stringify(b.estado.filtros)
    b.estado.escolherLoja(luno)
    assert.deepEqual(b.estado.emailsAbertos, ['email:f:f-luno'])
    assert.equal(JSON.stringify(b.estado.filtros), antes)
    b.estado.abrirEmails(luno, 'informativo')
    assert.deepEqual(b.emitidos.at(-1), ['abrirEmails', { chave: 'email:f:f-luno', integration_id: '', store_info_id: 'f-luno', plataforma: 'shopee', conta: 'luno', tipo: 'informativo' }])
  }

  // ------------------------------------------------ a lista da loja: funções puras
  {
    const sh = { chave: 'i-sh', integration_id: 'i-sh', store_info_id: '', tipo: 'chamado' }
    assert.equal(LE.urlDosEmailsDaLoja(sh), '/api/atendimento/conversas?caixa=email&filtro=todas&limite=50&tipo_email=chamado&integration_id=i-sh')
    assert.equal(LE.urlDosEmailsDaLoja(sh, { fechadas: true, busca: ' Maria ', antesDe: 'c1' }), '/api/atendimento/conversas?caixa=email&filtro=fechadas&q=Maria&limite=50&tipo_email=chamado&antes_de=c1&integration_id=i-sh')
    const ficha = { chave: 'semid:temu:barbosa', integration_id: '', store_info_id: 'f-temu', tipo: 'informativo' }
    assert.equal(LE.urlDosEmailsDaLoja(ficha), '/api/atendimento/conversas?caixa=email&filtro=todas&limite=50&tipo_email=informativo&store_info_id=f-temu')
    // Sem loja: NUNCA a lista de todas as lojas.
    assert.equal(LE.urlDosEmailsDaLoja({ integration_id: '', store_info_id: '', tipo: 'chamado' }), null)
    assert.equal(LE.urlDasLinhasDaLoja(sh), '/api/atendimento/email/emails?fila=informativo&integration_id=i-sh')
    assert.equal(LE.urlDasLinhasDaLoja(ficha, 'x'), '/api/atendimento/email/emails?fila=informativo&store_info_id=f-temu&antes_de=x')
    assert.equal(LE.urlDasLinhasDaLoja({ integration_id: '', store_info_id: '', tipo: 'informativo' }), null)
    assert.equal(LE.mostraLinhas('informativo', true, false, ''), true)
    assert.equal(LE.mostraLinhas('informativo', false, false, ''), false, 'quem só lê: a rota recusaria')
    assert.equal(LE.mostraLinhas('chamado', true, false, ''), false)
    assert.equal(LE.mostraLinhas('informativo', true, true, ''), false)
    assert.equal(LE.mostraLinhas('informativo', true, false, 'x'), false)
    assert.equal(LE.vazioDaLoja('informativo', false, ''), 'Nenhum e-mail informativo aberto desta loja.')
    assert.equal(LE.vazioDaLoja('chamado', true, ''), 'Nenhum e-mail chamado fechado desta loja.')
    assert.equal(LE.vazioDaLoja('chamado', true, 'x'), 'Nada encontrado para essa busca nesta loja.')
    assert.notEqual(LE.chaveDaEscolha({ ...sh }), LE.chaveDaEscolha({ ...sh, tipo: 'informativo' }))
  }

  // ------------------------------------------------ a lista da loja: setup de verdade
  {
    const URL_CONV = '/api/atendimento/conversas'
    const linha = (id, o = {}) => ({
      id, plataforma: 'shopee', canal: 'email', conta: 'ATV', comprador_nome: `Cliente ${id}`, pedido_marketplace: null, situacao: 'aberta',
      ultima_mensagem_em: '2026-10-27T10:00:00Z', ultima_mensagem_resumo: 'Sua venda foi aprovada', aguardando_resposta: false, prazo_resposta_em: null,
      email: { tipo_email: 'chamado', tipo_email_inferido: false, alias: '22max@tuta.com', caso_id: null, protocolo: null }, ...o,
    })
    const respostas = { [URL_CONV]: (url) => ({ itens: url.includes('filtro=fechadas') ? [linha('f1', { situacao: 'fechada' })] : [linha('c1'), linha('c2', { aguardando_resposta: true })], proximo: null }) }
    const escolha = { chave: 'i-sh', integration_id: 'i-sh', store_info_id: '', plataforma: 'shopee', conta: 'ATV', tipo: 'chamado' }
    const m = montar('../components/AtendimentoLojaEmails.vue', { escolha, contagem: POR_LOJA[0], tipos: NA_BARRA, podeMexer: true, selecionada: 'c2', tique: 0, sinal: 0 }, { respostas })
    await m.montar()
    assert.deepEqual(m.chamadas.map((c) => c.url), ['/api/atendimento/conversas?caixa=email&filtro=todas&limite=50&tipo_email=chamado&integration_id=i-sh'], 'chamado: sem as linhas informativas')
    let html = await m.html()
    assert.deepEqual([...html.matchAll(/data-email-da-loja="([^"]+)"/g)].map((x) => x[1]), ['c1', 'c2'])
    assert.match(html, /aria-current="true" data-email-da-loja="c2"/)
    assert.match(html, /data-email-da-loja="c2"[\s\S]*?falta responder[\s\S]*?Cliente c2[\s\S]*?para 22max@tuta\.com/)
    // Os chips: os tipos da barra (a escolha do dono vem da API), sem a Venda.
    assert.deepEqual([...html.matchAll(/data-tipo-da-loja="([^"]+)"/g)].map((x) => x[1]), ['informativo', 'chamado'])
    assert.match(html, /data-tipo-da-loja="chamado"[^>]*>[\s\S]*?Chamado<span[^>]*>37<\/span>/)
    assert.match(html, /aria-pressed="true" data-tipo-da-loja="chamado"/)
    assert.match(html, /data-voltar-conversas/)
    // As fechadas, no chip.
    m.estado.verFechadas(true)
    await esperar()
    assert.equal(m.chamadas.at(-1).url, '/api/atendimento/conversas?caixa=email&filtro=fechadas&limite=50&tipo_email=chamado&integration_id=i-sh')
    html = await m.html()
    assert.match(html, /data-email-da-loja="f1"[\s\S]*?>fechada</)
    // A busca: nome, pedido ou protocolo.
    m.estado.buscaTexto = ' 2000123 '
    m.estado.buscar()
    await esperar()
    assert.match(m.chamadas.at(-1).url, /filtro=fechadas&q=2000123&limite=50&tipo_email=chamado&integration_id=i-sh$/)
    // O tique (polling da página) relê a 1ª página; com "carregar mais", não.
    const antes = m.chamadas.length
    m.reativo.tique = 1
    await esperar()
    assert.equal(m.chamadas.length, antes + 1)
    // Outro tipo da mesma loja: do começo, nas abertas, sem busca.
    m.reativo.escolha = { ...escolha, tipo: 'informativo' }
    await esperar()
    assert.equal(m.estado.fechadas, false)
    assert.equal(m.estado.busca, '')
    assert.deepEqual(m.chamadas.slice(-2).map((c) => c.url).sort(), [
      '/api/atendimento/conversas?caixa=email&filtro=todas&limite=50&tipo_email=informativo&integration_id=i-sh',
      '/api/atendimento/email/emails?fila=informativo&integration_id=i-sh',
    ].sort(), 'informativo de quem mexe: as linhas das pastas informativas também')
    // O vazio explica e oferece a outra metade.
    const vazio = montar('../components/AtendimentoLojaEmails.vue', { escolha: { ...escolha, tipo: 'chamado' }, tipos: NA_BARRA, podeMexer: false }, { respostas: { [URL_CONV]: { itens: [], proximo: null } } })
    await vazio.montar()
    html = await vazio.html()
    assert.match(html, /data-emails-da-loja-vazio>\s*Nenhum e-mail chamado aberto desta loja\.[\s\S]*?ver as fechadas/)
  }
  {
    // Informativo de quem MEXE: as linhas da loja em cima; "ver o e-mail" = o cartão protegido.
    const linhaInf = { id: 'm1', estado: 'informativo', recebido_em: '2026-10-27T09:00:00Z', pasta: 'avisos ml', assunto: 'Novidades da semana', de: 'avisos@ml.com', destaque: false, alias: null, loja: 'ATV', integration_id: 'i-sh', sugestoes: [], pedidos_citados: [], alertas: [] }
    const respostas = {
      '/api/atendimento/conversas': { itens: [], proximo: null },
      '/api/atendimento/email/emails': { itens: [linhaInf], proximo: null },
      '/api/atendimento/email/emails/m1': { id: 'm1', texto: 'Texto protegido', de: 'avisos@ml.com', de_nome: 'ML', anexos: [], links_removidos: 0, codigo_mascarado: false },
    }
    const escolha = { chave: 'semid:temu:barbosa', integration_id: '', store_info_id: 'f-temu', plataforma: 'temu', conta: 'barbosa', tipo: 'informativo' }
    const m = montar('../components/AtendimentoLojaEmails.vue', { escolha, podeMexer: true }, { respostas })
    await m.montar()
    assert.deepEqual(m.chamadas.map((c) => c.url).sort(), [
      '/api/atendimento/conversas?caixa=email&filtro=todas&limite=50&tipo_email=informativo&store_info_id=f-temu',
      '/api/atendimento/email/emails?fila=informativo&store_info_id=f-temu',
    ])
    let html = await m.html()
    assert.match(html, /data-linhas-da-loja[\s\S]*?Pastas informativas[\s\S]*?data-linha-da-loja="m1"[\s\S]*?avisos ml[\s\S]*?Novidades da semana[\s\S]*?ver o e-mail[\s\S]*?Conversas de e-mail/)
    await m.estado.verLinha(linhaInf)
    html = await m.html()
    assert.match(html, /data-linha-da-loja-aberta[\s\S]*?Texto protegido/)
    assert.equal(m.chamadas.at(-1).url, '/api/atendimento/email/emails/m1')
    // Quem só lê: nem pergunta pelas linhas (a rota recusaria com 403).
    const leitor = montar('../components/AtendimentoLojaEmails.vue', { escolha, podeMexer: false }, { respostas })
    await leitor.montar()
    assert.deepEqual(leitor.chamadas.map((c) => c.url), ['/api/atendimento/conversas?caixa=email&filtro=todas&limite=50&tipo_email=informativo&store_info_id=f-temu'])
    assert.doesNotMatch(await leitor.html(), /data-linhas-da-loja/)
    // Sem loja nenhuma: não pergunta nada (nunca a lista de todas as lojas).
    const sem = montar('../components/AtendimentoLojaEmails.vue', { escolha: { ...escolha, store_info_id: '' }, podeMexer: true }, { respostas })
    await sem.montar()
    assert.deepEqual(sem.chamadas, [])
    assert.match(await sem.html(), /role="alert">Esta loja não tem como filtrar os e-mails\./)
  }

  // ------------------------------------------------ a página
  {
    const pagina = web('pages/atendimento.vue')
    const tpl = sfc('../pages/atendimento.vue').descriptor.template.content
    // A barra: os números por loja SÓ na Caixa (a Caixa Humano não tem a seta), os tipos da API, as lojas abertas, o clique.
    assert.match(tpl, /<AtendimentoLojas\s+v-model:filtros="filtros"\s+v-model:recolhida="lojasRecolhidas"\s+v-model:emails-abertos="lojasEmailsAbertos"\s+:resumo="resumo"\s+:numero="emHumano \? 'humano' : 'nao_lidas_duoke'"\s+:emails="aba === 'caixa' \? emailPorLoja : null"\s+:tipos-email="emailTiposNaBarra"\s+:email-aberto="emailsDaLoja \? \{ chave: emailsDaLoja\.chave, tipo: emailsDaLoja\.tipo \} : null"\s+@abrir-emails="abrirEmailsDaLoja"\s*\/>/)
    // Celular e tablet (sem a barra): o botão "E-mails das lojas" e a barra no modo "emails".
    assert.match(tpl, /<div v-if="aba === 'caixa' && canSugerir && !emailsDaLoja" class="[^"]*lg:hidden" data-emails-celular>[\s\S]*?@click="emailsNoCelular = !emailsNoCelular"[\s\S]*?'E-mails das lojas'/)
    assert.match(tpl, /<AtendimentoLojas\s+v-if="emailsNoCelular && !emailsDaLoja"[\s\S]*?class="lg:hidden"\s+modo="emails"[\s\S]*?:emails="emailPorLoja"\s+:tipos-email="emailTiposNaBarra"\s+@abrir-emails="abrirEmailsDaLoja"\s*\/>/)
    // A lista do meio: os e-mails da loja; a do chat fica GUARDADA (v-show), com os filtros dela.
    assert.match(tpl, /<AtendimentoLojaEmails\s+v-if="emailsDaLoja"\s+:escolha="emailsDaLoja"\s+:contagem="contagemDaLoja"\s+:tipos="emailTiposNaBarra"\s+:pode-mexer="mexe"\s+:selecionada="emailNaCaixa"\s+:tique="emailTique"\s+:sinal="emailNaCaixaSinal"\s+@selecionar="selecionarEmailNaCaixa"\s+@tipo="trocarTipoDaLoja"\s+@fechar="fecharEmailsDaLoja"\s*\/>\s*<AtendimentoLista\s+v-show="!emailsDaLoja && !emailsNoCelular"/)
    // A conversa: a do chat escondida e PARADA (sem polling) enquanto há e-mail na Caixa; a de e-mail com a barra do e-mail.
    assert.match(tpl, /<AtendimentoConversa\s+v-if="selecionada"\s+v-show="!emailNaTela"[\s\S]*?:ativa="naCaixa && !emailNaTela"[\s\S]*?@mudou="aoMudarConversa"/)
    assert.match(tpl, /<div v-if="emailNaCaixa"[^>]*data-email-na-caixa>[\s\S]*?<AtendimentoEmailConversaBarra\s+:conversa="emailNaCaixaLinha"\s+:pode-mexer="mexe"\s+na-caixa\s+:sem-lista="!emailsDaLoja"\s+:com-chat="!!emailNaCaixaOrigem"\s+@voltar="fecharEmailNaCaixa"\s+@voltar-ao-chat="voltarAoChatDaCaixa"\s+@vinculado="aoVincularEmailNaCaixa"\s*\/>[\s\S]*?<AtendimentoConversa\s+:key="`\$\{emailNaCaixa\}:\$\{emailNaCaixaVersao\}`"\s+:conversa-id="emailNaCaixa"[\s\S]*?@mudou="aoMudarEmailNaCaixa"[\s\S]*?@abrir-conversa="selecionarEmailNaCaixa"/)
    // No celular: a lista some quando há conversa aberta (a do chat ou a de e-mail).
    assert.match(tpl, /:class="abertaNaCaixa \? 'hidden' : 'flex'"/)
    assert.match(tpl, /:class="abertaNaCaixa \? 'flex' : 'hidden'"/)
    // A aba E-mail: só admin (as abas à mostra e o conteúdo).
    assert.match(tpl, /v-for="a in abasVisiveis"/)
    assert.doesNotMatch(tpl, /v-for="a in ABAS"/)
    assert.match(tpl, /<template v-if="aba === 'mail' && ehAdmin">/)

    // ── a aba E-mail só para admin, de verdade (o trecho da página com dublês)
    const trecho = (de, ate) => {
      const i = pagina.indexOf(de)
      const f = pagina.indexOf(ate, i)
      assert.ok(i > 0 && f > i, de)
      return transpileTs(pagina.slice(i, f))
    }
    {
      const abas = trecho('type Aba = ', "const { api } = useApi()")
      const ini = trecho('function abasDe(admin: boolean)', '// `?tab=lojas` abre direto na aba')
      const aba0 = trecho("const abaQuery = typeof route.query.tab", "const conversaQuery =")
      const montarAbas = (admin, tab) => new Function('computed', 'ref', 'ehAdmin', 'route', 'Inbox', 'UserRound', 'Store', 'BookOpen', 'MessageSquareText', 'Bot', 'BarChart3', `${abas}\n${ini}\n${aba0}\nreturn { ABAS, abasVisiveis, aba }`)(
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
        const out = new Function('ref', 'aba', 'abasVisiveis', 'ehAdmin', 'emailConversa', 'emailConversaLinha', 'chatDeOrigem', 'abrirEmailAvulsoNaCaixa', `${abrirAba}\n${doEmail}\nreturn { abrirAba, abrirConversaDoEmail }`)(
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
          canSugerir: Vue.ref(true), querPorLoja: Vue.ref(porLoja), ehAdmin: Vue.ref(admin),
          emailContagens: Vue.ref(null), emailPorLoja: Vue.ref(null), emailTiposNaBarra: Vue.ref([]), emailContagensProntas: Vue.ref(false),
        }
        const fn = new Function(...Object.keys(r), 'api', `let geracaoContagensEmail = 0\n${corpo}\nreturn carregarContagensEmail`)(
          ...Object.values(r), async (url) => { pedidos.push(url); return structuredClone(resposta) },
        )
        return { ...r, pedidos, fn }
      }
      let c = montarContagens({ admin: false, porLoja: false, resposta: {} })
      await c.fn()
      assert.deepEqual(c.pedidos, [], 'quem não é admin, fora da barra: nem pergunta (não tem a aba)')
      c = montarContagens({ admin: false, porLoja: true, resposta: { aguardando: 0, por_loja: POR_LOJA, tipos_na_barra: NA_BARRA } })
      await c.fn()
      assert.deepEqual(c.pedidos, ['/api/atendimento/email/contagens?por_loja=true'])
      assert.deepEqual([c.emailPorLoja.value.length, c.emailTiposNaBarra.value], [POR_LOJA.length, NA_BARRA])
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
    function bloco({ usuario = 'u1', guardado = new Map(), quebrado = false, largura = 1440, admin = false } = {}) {
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
      const nomes = ['ref', 'computed', 'watch', 'nextTick', 'onMounted', 'aba', 'filtros', 'selecionada', 'emailPorLoja', 'larguraJanela', 'lojasRecolhidas', 'canSugerir', 'ehAdmin', 'iniciou', 'auth', 'localStorage', 'carregarContagensEmail', 'carregarModelos', 'selecionar']
      const corpo = `${transpileTs(pagina.slice(ini, fim))}\nreturn { emailsDaLoja, emailNaCaixa, emailNaCaixaLinha, emailNaCaixaVersao, emailNaCaixaSinal, emailNaCaixaOrigem, emailsNoCelular, querPorLoja, contagemDaLoja, emailNaTela, abertaNaCaixa, abrirEmailsDaLoja, trocarTipoDaLoja, fecharEmailsDaLoja, fecharEmailNaCaixa, selecionarEmailNaCaixa, abrirEmailAvulsoNaCaixa, voltarAoChatDaCaixa, aoMudarEmailNaCaixa, aoVincularEmailNaCaixa, lojasEmailsAbertos, chaveDosEmailsAbertos }`
      const ehAdmin = Vue.ref(admin)
      const out = new Function(...nomes, corpo)(
        Vue.ref, Vue.computed, Vue.watch, Vue.nextTick, (fn) => montados.push(fn), aba, filtros, selecionada, emailPorLoja, larguraJanela, lojasRecolhidas, Vue.ref(true), ehAdmin, true, { user: usuario ? { id: usuario } : null }, localStorage,
        () => { lidas.contagens++ }, () => { lidas.modelos++ }, (id) => { lidas.selecionadas.push(id); selecionada.value = id },
      )
      return { ...out, aba, filtros, selecionada, larguraJanela, lojasRecolhidas, ehAdmin, montados, lidas, guardado, async montar() { for (const fn of montados) fn(); await Vue.nextTick(); await esperar() } }
    }
    function filtros0() { return filtros() }
    const ATV = { chave: 'i-sh', integration_id: 'i-sh', store_info_id: '', plataforma: 'shopee', conta: 'ATV', tipo: 'chamado' }
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
      // Outro tipo da loja: a conversa aberta fecha, a loja fica.
      b.trocarTipoDaLoja('informativo')
      assert.deepEqual([b.emailsDaLoja.value.tipo, b.emailNaCaixa.value], ['informativo', null])
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
      // O tipo escolhido no celular abre a lista da loja e fecha o painel.
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
      // Um tipo na barra com um e-mail avulso aberto: a lista da loja, sem o e-mail de antes.
      b.aba.value = 'caixa'
      await Vue.nextTick()
      b.abrirEmailAvulsoNaCaixa('e-9', { id: 'chat-1', aba: 'caixa' })
      b.abrirEmailsDaLoja(ATV)
      assert.deepEqual([b.emailNaCaixa.value, b.emailsDaLoja.value?.chave], [null, 'i-sh'])
      // Deixou de ser admin com a aba E-mail aberta: volta para a Caixa.
      b.ehAdmin.value = true
      b.aba.value = 'mail'
      await Vue.nextTick()
      b.ehAdmin.value = false
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
    assert.match(email, /"tipos_na_barra": list\(na_barra\)/)
    assert.match(email, /integration_id: Annotated\[UUID \| None, Query\(\)\] = None,\n    store_info_id: Annotated\[UUID \| None, Query\(\)\] = None,\n\) -> dict\[str, Any\]:\n    """Uma fila:/)
    assert.match(api('routers/atendimento.py'), /store_info_id: Annotated\[UUID \| None, Query\(\)\] = None,\n\) -> ListaConversasOut:/)
    // A escolha do dono: UMA constante, na API (o web não tem lista própria de quais tipos aparecem).
    assert.match(api('services/mail_atendimento/constantes.py'), /TIPOS_EMAIL_NA_BARRA_DE_LOJAS: tuple\[str, \.\.\.\] = \(CATEGORIA_INFORMATIVO, CATEGORIA_CHAMADO\)/)
    assert.doesNotMatch(web('components/AtendimentoLojas.vue'), /'venda'\s*\]\s*$|TIPOS_NA_BARRA\s*=/m)
    // O e-mail continua fora da Caixa: a barra e a lista do chat não perguntam pelo e-mail.
    assert.doesNotMatch(web('components/AtendimentoLista.vue'), /caixa=email|por_loja/)
  }
  console.log('ok — atendimento-email-lojas')
}

principal().catch((e) => { console.error(e); process.exit(1) })
