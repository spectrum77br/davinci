// node tests/atendimento-outras-compras.cjs — "Outras compras (N)" na aba Pedido
// (06/11/2026). O dono, com o Duoke ao lado: "uma pessoa cancelou 2 pedidos, no nosso
// aparece o código só do último e não o histórico inteiro".
//  - a lista: o índice e o Bling na hora, a mais nova primeiro (a ordem é do backend);
//    sem compra, a seção some; sem selo de "histórico parcial" (a marca não existe em
//    nenhuma loja em produção, e o selo diria sempre uma coisa falsa);
//  - quais aparecem (`disposicaoDasCompras`, função pura): as 3 mais novas, "Carregar
//    mais" traz mais 5, Aguardando Cancelamento sempre, e a pesquisada no topo com
//    destaque e a tela rolada até ela (nº inteiro, Bling inteiro ou pedaço de SKU: o do
//    Bling ou o do retrato que já veio; o nome do anúncio não); limpou a busca, volta
//    ao normal. Retrato só do que está na tela, e o que entrou depois pede só os novos,
//    sem reler a lista;
//  - cada cartão: selo do status, nº com copiar, data "(UTC-03:00)", produtos com foto,
//    variação, SKU, quantidade e preço, valor pago, método, motivo do cancelamento,
//    transportadora e status do envio, e a linha do Bling com os dois links e o selo de
//    Aguardando Cancelamento em 83955; o "Abrir na Shopee" também sem linha do Bling;
//  - sem retrato (ainda não veio, ou falhou): o que há no índice, sem erro na tela;
//  - sem ação: nada de trocar, oferecer, NF nem mudar o pedido da conversa;
//  - quando busca: ao montar (a aba remonta) e quando o pedido da conversa muda; o tique
//    de 5 s não relê; o "atualizar" relê só aquele cartão, com a trava de 60 s;
//  - no painel: no fim da aba Pedido, só no chat de um comprador por loja; o termo vem
//    de `filtros.q` por prop; o pedido da conversa pesquisado tem o destaque no bloco de
//    cima; com compras na lista, sem o "Conversa sem pedido ligado".
// Aqui: o setup de verdade (Vue SSR, API falsa), como em atendimento-devolucao-pedido.
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
// O `useAcessoDaTroca` (AtendimentoTroca.vue) lê as auto-importações do Nuxt.
Object.assign(globalThis, {
  computed: Vue.computed,
  ref: Vue.ref,
  useAuthStore: () => ({ user: {} }),
  useCan: () => Vue.ref(false),
})
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
const semComentarios = (html) => html.replace(/<!--[\s\S]*?-->/g, '')
// O espaço do Intl ("R$ 311,91") vira espaço comum; as marcas somem.
const texto = (html) => html.replace(/<[^>]+>/g, ' ').replace(/[\s ]+/g, ' ').trim()
const esperar = () => new Promise((r) => setTimeout(r, 0))

function montar(rel, props, { respostas = {}, filhos = {} } = {}) {
  const { descriptor, filename } = sfc(rel)
  const script = compileScript(descriptor, { id: path.basename(rel), inlineTemplate: false })
  const tpl = compileTemplate({ source: descriptor.template.content, filename, id: path.basename(rel), compilerOptions: { isTS: true, bindingMetadata: script.bindings } })
  const chamadas = []
  const avisos = []
  const emitidos = []
  const montados = []
  const gestos = {}
  const responder = async (url, opts = {}) => {
    chamadas.push({ url, metodo: opts.method || 'GET', corpo: opts.body })
    if (!(url in respostas)) throw new Error(`rota inesperada: ${url}`)
    const r = respostas[url]
    return structuredClone(typeof r === 'function' ? await r(opts) : r)
  }
  const g = {
    ref: Vue.ref, computed: Vue.computed, reactive: Vue.reactive, watch: Vue.watch, nextTick: Vue.nextTick,
    onMounted: (fn) => montados.push(fn), onBeforeUnmount: () => {},
    useApi: () => ({ url: (v) => v, api: responder }),
    useToasts: () => ({ success: (t) => avisos.push(['ok', t]), error: (t) => avisos.push(['erro', t]), info: (t) => avisos.push(['info', t]), warning: (t) => avisos.push(['aviso', t]) }),
    localStorage: { getItem: () => null, setItem: () => {} },
    window: { prompt: () => {}, addEventListener: (ev, fn) => { gestos[ev] = fn }, removeEventListener: () => {} },
  }
  const nomes = Object.keys(g)
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', ...nomes, transpile(script.content))(requerer, mod, mod.exports, ...nomes.map((n) => g[n]))
  const tmod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(tpl.code))(requerer, tmod, tmod.exports)
  const comp = mod.exports.default
  const reativo = Vue.reactive({ ...props })
  // O useRelogio (onMounted do Vue fora de um componente) só avisa aqui.
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
    for (const [n, c] of Object.entries(filhos)) a.component(n, c)
    return semComentarios(await renderToString(a))
  }
  return { html, estado, chamadas, avisos, emitidos, gestos, props: reativo, async montar() { for (const fn of montados) await fn(); await esperar() } }
}

const abrirFalso = { props: ['href', 'perfil', 'conversaId', 'titulo'], setup: (p, { slots }) => () => Vue.h('a', { href: p.href, 'data-abrir-plataforma': '' }, slots.default?.()) }
const iconeFalso = { props: ['plataforma', 'tamanho', 'decorativo'], setup: () => () => Vue.h('i', { 'data-icone-plataforma': '' }) }
const filhos = { AtendimentoAbrirPlataforma: abrirFalso, AtendimentoIconePlataforma: iconeFalso }

const REL = '../components/AtendimentoOutrasCompras.vue'
const REL_PEDIDO = '../components/AtendimentoPedido.vue'
const BASE = '/api/atendimento/conversas/c1/pedidos-comprador'

const indice = (extra = {}) => ({
  pedido: '2610301X726956', status: 'PROCESSED', status_texto: 'Processado', criado_em: '2026-10-30T13:40:10Z', total: 78.65,
  itens_resumo: '1x Mala de Bordo (Preta)', bling: null, link_plataforma: null, ...extra,
})
const retrato = (extra = {}) => ({
  fonte: 'shopee', pedido: '2610301X726956', status: 'CANCELLED', status_texto: 'Cancelado', criado_em: '2026-10-30T13:40:10Z',
  pago_em: '2026-10-30T13:41:00Z', total: 78.65, valor_pago: 78.65, moeda: 'BRL', pagamento_metodo: 'Pix', cancelamento: 'Out of Stock',
  itens: [{ titulo: 'Mala de Bordo ABS', imagem: 'https://down-br.img.susercontent.com/file/f', variacao: 'Preta,P', sku: 'MALA-P', quantidade: 2, preco: 39.33 }],
  logistica: { transportadora: 'Shopee Xpress', rastreio: null, status: 'LOGISTICS_PICKUP_DONE', status_texto: 'Coletado', descricao: null, atualizado_em: null },
  nf: { numero: '123', status: 'pending' }, atualizado_em: '2026-11-06T10:00:00Z', ...extra,
})
const em83955 = {
  numero: '306788', situacao_id: '83955', situacao: 'Aguardando Cancelamento', link: 'https://www.bling.com.br/vendas.php#edit/27271814523',
}
const shopee = { url: 'https://seller.shopee.com.br/portal/sale/order?search=2610301X726956', rotulo: 'Abrir na Shopee' }
const props = { conversaId: 'c1', plataforma: 'shopee', pedidoDaConversa: '2610312QN3T9UG', podeAtualizar: true, perfil: null }
const resposta = (itens, retratos = {}) => ({
  [BASE]: { itens },
  [`${BASE}/retratos`]: { retratos },
})
// Os 16 do caso real (a conversa 863b6644): o 13º em Aguardando Cancelamento.
const dezesseis = () => Array.from({ length: 16 }, (_, n) => indice({
  pedido: `P${n + 1}`,
  ...(n === 12 ? { bling: em83955 } : {}),
  ...(n === 8 ? { bling: { numero: '300009', situacao_id: '12', situacao: 'Cancelado', link: null } } : {}),
  ...(n === 9 || n === 14 ? { bling: { numero: `30001${n}`, situacao_id: '12', situacao: 'Cancelado', link: null, skus: ['MALA-AZUL-P'] } } : {}),
}))
const naTela = (h) => [...h.matchAll(/data-outra-compra="([^"]+)"/g)].map((x) => x[1])
const comDestaque = (h) => [...h.matchAll(/data-outra-compra="([^"]+)"[^>]*data-compra-pesquisada/g)].map((x) => x[1])
// O elemento da seção, falso, para ver as idas da rolagem (no SSR não há DOM).
function comRolagem(m, chave = 'raiz') {
  const rolagens = []
  m.estado[chave] = { querySelector: (sel) => ({ scrollIntoView: (o) => rolagens.push({ sel, ...o }) }) }
  return rolagens
}

async function principal() {
  // ------------------------------------------------ o cartão completo, na ordem
  {
    const m = montar(REL, props, { respostas: resposta([indice({ bling: em83955, link_plataforma: shopee })], { '2610301X726956': retrato() }), filhos })
    await m.montar()
    await esperar()
    const h = await m.html()
    const t = texto(h)
    assert.match(t, /Outras compras \(1\)/)
    assert.match(h, /data-outra-compra="2610301X726956"/)
    assert.match(h, /data-com-retrato/)
    assert.match(t, /Cancelado/, 'o status do retrato, em selo')
    assert.ok(!t.includes('Processado'), 'o retrato é mais novo que o índice')
    assert.match(h, /aria-label="copiar nº do pedido"/)
    assert.match(t, /2026\/10\/30 10:40 \(UTC-03:00\)/)
    assert.match(h, /src="https:\/\/down-br\.img\.susercontent\.com\/file\/f"/, 'a foto')
    assert.match(t, /Mala de Bordo ABS/)
    assert.match(t, /Variação: Preta,P/)
    assert.match(t, /SKU: MALA-P/)
    assert.match(t, /× 2/)
    assert.match(t, /R\$ 39,33/)
    assert.match(t, /Valor pago pelo comprador R\$ 78,65/)
    assert.match(t, /Método de pagamento Pix/)
    assert.match(t, /Motivo do cancelamento Out of Stock/)
    assert.match(t, /Provedor de transporte Shopee Xpress/)
    assert.match(t, /Estado da logística Coletado/)
    assert.match(t, /Bling 306788/)
    assert.match(h, /data-compra-ag-cancelamento[^>]*>Aguardando Cancelamento</, 'o selo em destaque')
    assert.match(h, /href="https:\/\/www\.bling\.com\.br\/vendas\.php#edit\/27271814523"/)
    assert.match(t, /Abrir no Bling/)
    assert.match(h, /data-abrir-plataforma[^>]*>[\s\S]*Abrir na Shopee/)
    assert.ok(!h.includes('data-resumo-do-indice'), 'com produtos, sem o resumo')
    const ordem = ['Cancelado', '2610301X726956', 'UTC-03:00', 'Mala de Bordo ABS', 'Valor pago', 'Método', 'Motivo', 'Provedor', 'Estado da logística', 'Bling 306788'].map((x) => t.indexOf(x))
    assert.ok(ordem.every((p) => p >= 0), 'todas as linhas')
    assert.deepEqual(ordem, ordem.slice().sort((a, b) => a - b), 'na ordem do Duoke')
    // Sem ação: nada de trocar, oferta, NF.
    for (const proibido of ['Trocar', 'oferta', 'Retomar', 'Nota fiscal', 'NF', 'rastreio', 'Rastreio']) assert.ok(!t.includes(proibido), proibido)
    // A lista primeiro; os retratos à parte, em lote, com os pedidos da lista.
    assert.deepEqual(m.chamadas.map((c) => [c.metodo, c.url]), [['GET', BASE], ['POST', `${BASE}/retratos`]])
    assert.deepEqual(m.chamadas[1].corpo, { pedidos: ['2610301X726956'] })
  }

  // ------------------------------------------------ situação comum do Bling, sem selo
  {
    const bling = { numero: '306797', situacao_id: '12', situacao: 'Cancelado', link: null }
    const m = montar(REL, props, { respostas: resposta([indice({ bling })]), filhos })
    await m.montar()
    await esperar()
    const h = await m.html()
    assert.ok(!h.includes('data-compra-ag-cancelamento'), 'o selo é só de 83955')
    assert.match(h, /data-bling-da-compra/)
    assert.match(texto(h), /Bling 306797 Cancelado/)
    assert.ok(!texto(h).includes('Abrir no Bling'), 'sem id, sem link')
  }

  // ------------------------------------------------ sem retrato: o que há no índice
  {
    const m = montar(REL, props, { respostas: resposta([indice()]), filhos })
    await m.montar()
    await esperar()
    const h = await m.html()
    const t = texto(h)
    assert.match(t, /Processado/, 'o status do índice')
    assert.match(t, /2610301X726956/)
    assert.match(t, /2026\/10\/30 10:40 \(UTC-03:00\)/)
    assert.match(t, /Total do pedido R\$ 78,65/)
    assert.match(h, /data-resumo-do-indice[^>]*>1x Mala de Bordo \(Preta\)</)
    assert.ok(!h.includes('data-com-retrato'))
    for (const rotulo of ['Valor pago', 'Método', 'Motivo', 'Provedor', 'Estado da logística', 'Bling ']) assert.ok(!t.includes(rotulo), rotulo)
    assert.ok(!h.includes('data-bling-da-compra'), 'sem linha no Bling, sem a linha')
  }

  // ------------------------------------------------ o retrato falha: sem erro na tela
  {
    const m = montar(REL, props, {
      respostas: { [BASE]: { itens: [indice()] }, [`${BASE}/retratos`]: () => { throw new Error('500') } },
      filhos,
    })
    await m.montar()
    await esperar()
    const t = texto(await m.html())
    assert.match(t, /Processado/)
    assert.match(t, /Total do pedido R\$ 78,65/)
    assert.deepEqual(m.avisos, [], 'nenhum aviso na cara da pessoa')
    assert.ok(!/erro|falhou|não consegui/i.test(t), 'nenhum texto de erro')
  }

  // ------------------------------------------------ a lista falha ou vem vazia: some
  {
    const falha = montar(REL, props, { respostas: { [BASE]: () => { throw new Error('500') } }, filhos })
    await falha.montar()
    await esperar()
    assert.ok(!(await falha.html()).includes('data-outras-compras'), 'a seção some')
    assert.deepEqual(falha.avisos, [])
    assert.equal(falha.chamadas.length, 1, 'sem lista, sem retrato')
    assert.deepEqual(falha.emitidos, [['compras', 0]])

    const vazia = montar(REL, props, { respostas: resposta([]), filhos })
    await vazia.montar()
    await esperar()
    assert.ok(!(await vazia.html()).includes('data-outras-compras'))
    assert.equal(vazia.chamadas.length, 1, 'sem compra, sem retrato')
    assert.deepEqual(vazia.emitidos, [['compras', 0]])
  }

  // ------------------------------------------------ o "Abrir na Shopee" não depende da linha do Bling
  {
    const m = montar(REL, props, { respostas: resposta([indice({ link_plataforma: shopee })]), filhos })
    await m.montar()
    await esperar()
    const h = await m.html()
    assert.match(h, /data-abrir-plataforma[^>]*>[\s\S]*Abrir na Shopee/, 'sem Bling, o link da plataforma fica')
    assert.ok(!h.includes('data-bling-da-compra'), 'sem a parte do Bling')
    assert.ok(!texto(h).includes('Abrir no Bling'))
    assert.ok(!texto(h).includes('Bling '))
  }

  // ------------------------------------------------ várias compras, sem selo de histórico
  {
    const m = montar(REL, props, {
      respostas: resposta([indice({ pedido: 'B2' }), indice({ pedido: 'A1' })], { A1: retrato({ pedido: 'A1' }) }),
      filhos,
    })
    await m.montar()
    await esperar()
    const h = await m.html()
    assert.equal(texto(h).startsWith('Outras compras (2) '), true)
    assert.ok(!/histórico parcial|historico_completo|historicoCompleto/.test(sfc(REL).fonte), 'nem o selo, nem a leitura da marca')
    assert.deepEqual(naTela(h), ['B2', 'A1'], 'a ordem do backend')
    // Cada cartão com o dele.
    assert.match(texto(h), /B2 .*Total do pedido.*A1 .*Valor pago/)
  }

  // ------------------------------------------------ o tique não relê; o pedido da conversa, sim
  {
    const m = montar(REL, props, { respostas: resposta([indice()]), filhos })
    await m.montar()
    await esperar()
    assert.equal(m.chamadas.length, 2)
    // O detalhe é relido a cada tique: as mesmas props, de novo, não buscam nada.
    m.props.podeAtualizar = false
    m.props.pedidoDaConversa = '2610312QN3T9UG'
    m.props.plataforma = 'shopee'
    await esperar()
    await Vue.nextTick()
    assert.equal(m.chamadas.length, 2, 'nada de ir ao servidor a cada tique')
    m.props.pedidoDaConversa = '2610301X726956'
    await esperar()
    await esperar()
    assert.equal(m.chamadas.length, 4, 'o pedido da conversa mudou: a lista muda')
  }

  // ------------------------------------------------ o "atualizar" do cartão
  {
    let ida = 0
    const url = `${BASE}/2610301X726956/atualizar`
    const respostas = {
      ...resposta([indice({ pedido: '2610301X726956' }), indice({ pedido: 'OUTRO77' })]),
      [url]: () => {
        ida += 1
        return { retrato: retrato({ status: 'COMPLETED', status_texto: 'Concluído', cancelamento: null }), item: indice({ bling: em83955 }), atualizado: true, motivo: null }
      },
    }
    const m = montar(REL, props, { respostas, filhos })
    await m.montar()
    await esperar()
    const antes = m.chamadas.length
    assert.match(await m.html(), /aria-label="atualizar este pedido da plataforma"/)
    await m.estado.atualizar('2610301X726956')
    assert.equal(ida, 1)
    assert.deepEqual(m.chamadas.slice(antes).map((c) => [c.metodo, c.url]), [['POST', url]], 'só aquele pedido, sem reler a lista')
    assert.deepEqual(m.avisos.at(-1), ['ok', 'Pedido atualizado da plataforma'])
    let h = await m.html()
    assert.match(h, /data-outra-compra="2610301X726956"[\s\S]*Concluído[\s\S]*data-compra-ag-cancelamento[\s\S]*data-outra-compra="OUTRO77"/, 'o retrato e a linha do Bling novos, no cartão certo')
    // A trava de 60 s também na tela, sem ir ao servidor.
    await m.estado.atualizar('2610301X726956')
    assert.equal(ida, 1, 'dentro do minuto, nem chama')
    assert.deepEqual(m.avisos.at(-1), ['info', 'Pedido atualizado há pouco'])
    // A falha não é erro: os mesmos avisos do botão de hoje.
    respostas[`${BASE}/OUTRO77/atualizar`] = { retrato: null, item: null, atualizado: false, motivo: 'limite' }
    await m.estado.atualizar('OUTRO77')
    assert.deepEqual(m.avisos.at(-1), ['info', 'Muitas atualizações desta loja agora'])

    // Sem poder atualizar (TikTok, leitura desligada, só leitura), sem botão.
    const sem = montar(REL, { ...props, podeAtualizar: false }, { respostas: resposta([indice()]), filhos })
    await sem.montar()
    await esperar()
    assert.ok(!(await sem.html()).includes('atualizar este pedido'))

    // O 409 da leitura desligada esconde o botão e libera a trava.
    const off = montar(REL, props, {
      respostas: {
        ...resposta([indice({ pedido: 'DESL9' })]),
        [`${BASE}/DESL9/atualizar`]: () => {
          throw Object.assign(new Error('409'), { data: { detail: { code: 'leitura_desligada', detail: 'A leitura das lojas está desligada no DaVinci.' } } })
        },
      },
      filhos,
    })
    await off.montar()
    await esperar()
    await off.estado.atualizar('DESL9')
    assert.ok(!(await off.html()).includes('atualizar este pedido'), 'o botão some')
  }

  // ------------------------------------------------ as regras dos cartões (função pura)
  {
    const { COMPRAS_NA_ABERTURA, COMPRAS_POR_CLIQUE, compraPesquisada, disposicaoDasCompras, limiteDepoisDoClique, termoEhDoPedido } = exportsDe(sfc(REL).descriptor)
    assert.equal(COMPRAS_NA_ABERTURA, 3)
    assert.equal(COMPRAS_POR_CLIQUE, 5)
    const nomes = (d) => d.visiveis.map((i) => i.pedido)
    const lista = dezesseis()
    const abrir = (termo, limite = 3, fixas) => disposicaoDasCompras(lista, limite, termo, fixas)

    // As 3 mais novas, e a em Aguardando Cancelamento, a 13ª de 16.
    let d = abrir('')
    assert.deepEqual(nomes(d), ['P1', 'P2', 'P3', 'P13'])
    assert.equal(d.ocultas, 12)
    assert.equal(d.pesquisadas.size, 0)
    // Poucas compras: todas, sem nada oculto.
    d = disposicaoDasCompras(lista.slice(0, 2), 3, '')
    assert.deepEqual(nomes(d), ['P1', 'P2'])
    assert.equal(d.ocultas, 0)
    // Exatamente 3: nada oculto, sem botão.
    assert.equal(disposicaoDasCompras(lista.slice(0, 3), 3, '').ocultas, 0)
    // A em Aguardando Cancelamento dentro das 3 não soma cartão.
    d = disposicaoDasCompras([indice({ pedido: 'Z1', bling: em83955 }), indice({ pedido: 'Z2' }), indice({ pedido: 'Z3' }), indice({ pedido: 'Z4' })], 3, '')
    assert.deepEqual(nomes(d), ['Z1', 'Z2', 'Z3'])
    assert.equal(d.ocultas, 1)
    // Vazia.
    assert.deepEqual(disposicaoDasCompras([], 3, 'P1'), { visiveis: [], pesquisadas: new Set(), ocultas: 0 })

    // Cada clique traz 5 NOVAS: a 13ª já estava na tela e não conta.
    let limite = limiteDepoisDoClique(lista, 3, '')
    assert.equal(limite, 8)
    assert.deepEqual(nomes(abrir('', limite)), ['P1', 'P2', 'P3', 'P4', 'P5', 'P6', 'P7', 'P8', 'P13'])
    limite = limiteDepoisDoClique(lista, limite, '')
    assert.equal(limite, 14, 'P9 a P12 e P14: cinco novas, pulando a P13')
    assert.equal(abrir('', limite).ocultas, 2)
    limite = limiteDepoisDoClique(lista, limite, '')
    assert.equal(limite, 16)
    assert.equal(abrir('', limite).ocultas, 0)
    assert.equal(limiteDepoisDoClique(lista, limite, ''), 16, 'no fim, fica')
    // A pesquisada que já está no topo também não conta como nova.
    assert.equal(limiteDepoisDoClique(lista, 8, 'P9'), 15, 'entram P10, P11, P12, P14 e P15')
    assert.deepEqual(nomes(abrir('P9', 15)).slice(0, 2), ['P9', 'P1'])

    // O nº inteiro, em qualquer caixa e com espaço; pedaço de nº não casa.
    d = abrir('P9')
    assert.deepEqual(nomes(d), ['P9', 'P1', 'P2', 'P3', 'P13'], 'no topo, e as 3 de sempre depois')
    assert.deepEqual([...d.pesquisadas], ['P9'])
    assert.equal(d.ocultas, 11)
    assert.deepEqual(nomes(abrir('  p9 ')).slice(0, 1), ['P9'])
    const longo = indice({ pedido: '2610301X726956' })
    assert.equal(compraPesquisada(longo, '2610301x726956'), true, 'o digitado em minúscula')
    assert.equal(compraPesquisada(longo, '2610301X72695'), false, 'pedaço não')
    assert.equal(compraPesquisada(longo, '26103'), false)
    // O nº do Bling, inteiro.
    d = abrir('300009')
    assert.deepEqual(nomes(d).slice(0, 2), ['P9', 'P1'])
    assert.equal(abrir('30000').pesquisadas.size, 0, 'pedaço do nº do Bling não')
    // Pedaço de SKU: todas as que baterem, em ordem de data, no topo.
    d = abrir('mala-azul')
    assert.deepEqual([...d.pesquisadas], ['P10', 'P15'])
    assert.deepEqual(nomes(d), ['P10', 'P15', 'P1', 'P2', 'P3', 'P13'])
    // O nome do anúncio não casa: a busca pelo nome do comprador não destaca a "Capa Rosa".
    assert.equal(abrir('1x').pesquisadas.size, 0)
    assert.equal(abrir('bordo').pesquisadas.size, 0)
    assert.equal(abrir('Preta').pesquisadas.size, 0)
    assert.equal(compraPesquisada(indice({ pedido: 'AB', itens_resumo: 'ab cd' }), 'ab'), true, 'o nº inteiro vale até com pouca letra')
    // A pesquisada dentro das 3 sobe, sem somar cartão.
    d = abrir('P2')
    assert.deepEqual(nomes(d), ['P2', 'P1', 'P3', 'P13'])
    // A em Aguardando Cancelamento pesquisada vem no topo, uma vez só.
    assert.deepEqual(nomes(abrir('306788')), ['P13', 'P1', 'P2', 'P3'])
    // O SKU de verdade: o resumo é o nome do anúncio (`indice.pedido_shopee`). Vem da linha
    // do Bling, com a lista, em qualquer caixa, pedaço; do retrato, quando já veio.
    const comSku = indice({ pedido: 'ANTIGA1', itens_resumo: '1× Chaleira Teste', bling: { numero: '900002', situacao_id: '12', situacao: 'Cancelado', link: null, skus: ['AB-1', 'QWX-55'] } })
    assert.equal(compraPesquisada(comSku, 'QWX-55'), true, 'inteiro')
    assert.equal(compraPesquisada(comSku, 'qwx'), true, 'pedaço, em minúscula')
    assert.equal(compraPesquisada(comSku, 'QWX-99'), false, 'outro SKU')
    assert.equal(compraPesquisada(comSku, 'AB'), false, 'o mesmo piso de 3 letras da busca da lista')
    assert.equal(compraPesquisada(indice({ itens_resumo: '1× Chaleira Teste' }), 'SKU-01'), false, 'sem retrato, não há de onde saber')
    assert.equal(compraPesquisada(indice({ itens_resumo: '1× Chaleira Teste' }), 'SKU-01', { itens: [{ sku: 'XSKU-01' }, { sku: null }] }), true, 'o SKU que o cartão mostra')
    assert.equal(compraPesquisada(indice(), 'SKU-01', { itens: [{ sku: 'OUTRO-1' }] }), false)
    assert.equal(compraPesquisada(indice(), 'SKU-01', null), false, 'retrato que não veio não quebra')
    assert.equal(compraPesquisada(indice(), 'SK', { itens: [{ sku: 'SKU-01' }] }), false, 'o piso vale no retrato também')
    // Fora das 3: a do Bling vem ao topo; a do retrato, assim que ele chega.
    const comAntiga = [...lista.slice(0, 3), comSku, ...lista.slice(3)]
    d = disposicaoDasCompras(comAntiga, 3, 'qwx')
    assert.deepEqual(nomes(d), ['ANTIGA1', 'P1', 'P2', 'P3', 'P13'])
    assert.deepEqual([...d.pesquisadas], ['ANTIGA1'])
    d = disposicaoDasCompras(lista, 3, 'SKU-01', new Set(), { P2: { itens: [{ sku: 'SKU-01' }] } })
    assert.deepEqual(nomes(d), ['P2', 'P1', 'P3', 'P13'])
    assert.deepEqual([...d.pesquisadas], ['P2'])
    // A que subiu pelo retrato também não conta como nova no clique.
    assert.equal(limiteDepoisDoClique(lista, 3, 'SKU-01', new Set(), { P5: { itens: [{ sku: 'SKU-01' }] } }), 9, 'P4, P6, P7, P8 e P9; a P5 já estava no topo')

    // Sem termo, nada casa; sem resumo, não quebra.
    for (const vazio of ['', '   ', null, undefined]) assert.equal(abrir(vazio).pesquisadas.size, 0)
    assert.equal(compraPesquisada({ pedido: 'P', bling: null, itens_resumo: null }, 'mala'), false)
    // Limpou a busca: o mesmo de antes.
    assert.deepEqual(nomes(abrir('')), nomes(abrir(null)))
    // A fixa (a que saiu de Aguardando Cancelamento na mão da pessoa) fica.
    assert.deepEqual(nomes(abrir('', 3, new Set(['P7']))), ['P1', 'P2', 'P3', 'P7', 'P13'])

    // O termo é o pedido da conversa?
    assert.equal(termoEhDoPedido('P1', ['P1', null]), true)
    assert.equal(termoEhDoPedido(' p1 ', [null, 'P1']), true)
    assert.equal(termoEhDoPedido('306797', [null, 306797]), true, 'o nº do Bling pode vir como número')
    assert.equal(termoEhDoPedido('P', ['P1']), false)
    assert.equal(termoEhDoPedido('', ['', null]), false, 'sem termo, nunca')
    assert.equal(termoEhDoPedido(null, [null]), false)
  }

  // ------------------------------------------------ 3 cartões, "Carregar mais" e os retratos só do que está na tela
  {
    const respostas = {
      [BASE]: { itens: dezesseis() },
      [`${BASE}/retratos`]: (o) => ({ retratos: Object.fromEntries(o.body.pedidos.map((p) => [p, retrato({ pedido: p })])) }),
    }
    const m = montar(REL, props, { respostas, filhos })
    const rolagens = comRolagem(m)
    await m.montar()
    await esperar()
    let h = await m.html()
    assert.match(texto(h), /Outras compras \(16\)/, 'o N é o total, não o que está na tela')
    assert.deepEqual(naTela(h), ['P1', 'P2', 'P3', 'P13'])
    assert.deepEqual(m.emitidos, [['compras', 16]], 'o painel sabe que há compras')
    assert.match(h, /data-carregar-mais-compras[^>]*>Carregar mais</)
    assert.deepEqual(m.chamadas.map((c) => [c.metodo, c.url]), [['GET', BASE], ['POST', `${BASE}/retratos`]])
    assert.deepEqual(m.chamadas[1].corpo, { pedidos: ['P1', 'P2', 'P3', 'P13'] }, 'só o que está na tela')
    assert.equal(m.estado.cartoes.filter((c) => c.r).length, 4)

    // Cada clique pede só os novos, sem reler a lista.
    m.estado.carregarMais()
    await esperar()
    h = await m.html()
    assert.deepEqual(naTela(h), ['P1', 'P2', 'P3', 'P4', 'P5', 'P6', 'P7', 'P8', 'P13'])
    assert.deepEqual(m.chamadas.slice(2).map((c) => [c.metodo, c.url, c.corpo]), [['POST', `${BASE}/retratos`, { pedidos: ['P4', 'P5', 'P6', 'P7', 'P8'] }]])
    m.estado.carregarMais()
    await esperar()
    assert.deepEqual(m.chamadas[3].corpo, { pedidos: ['P9', 'P10', 'P11', 'P12', 'P14'] }, 'a P13 já tinha retrato')
    assert.ok((await m.html()).includes('data-carregar-mais-compras'), 'ainda faltam 2')
    m.estado.carregarMais()
    await esperar()
    assert.deepEqual(m.chamadas[4].corpo, { pedidos: ['P15', 'P16'] })
    h = await m.html()
    assert.equal(naTela(h).length, 16)
    assert.ok(!h.includes('data-carregar-mais-compras'), 'sem nada oculto, sem o botão')
    assert.equal(m.chamadas.filter((c) => c.metodo === 'GET').length, 1)
    assert.deepEqual(rolagens, [], 'sem busca, nenhuma rolagem; o "Carregar mais" não mexe na tela')

    // Poucas compras: sem o botão.
    const poucas = montar(REL, props, { respostas: resposta([indice({ pedido: 'A' }), indice({ pedido: 'B' }), indice({ pedido: 'C' })]), filhos })
    await poucas.montar()
    await esperar()
    assert.ok(!(await poucas.html()).includes('data-carregar-mais-compras'))
  }

  // ------------------------------------------------ a pessoa mexeu com o retrato a caminho
  {
    const segurarRetrato = () => {
      const respostas = resposta(dezesseis())
      const url = `${BASE}/retratos`
      const original = respostas[url]
      const porta = {}
      respostas[url] = (o) => new Promise((ok) => { porta.soltar = () => ok(typeof original === 'function' ? original(o) : original) })
      const m = montar(REL, { ...props, termoBusca: 'P9' }, { respostas, filhos })
      return { m, porta, rolagens: comRolagem(m) }
    }
    // Sem mexer: rola ao chegar a lista e de novo depois do retrato.
    const parada = segurarRetrato()
    let aberta = parada.m.montar()
    await esperar()
    assert.equal(parada.rolagens.length, 1)
    parada.porta.soltar()
    await aberta
    assert.equal(parada.rolagens.length, 2)
    // Clicou em "Carregar mais" ou rolou no meio: a tela fica onde ela está.
    for (const mexer of [(m) => m.estado.carregarMais(), (m) => m.gestos.wheel(), (m) => m.gestos.pointerdown(), (m) => m.gestos.keydown(), (m) => m.gestos.touchmove()]) {
      const c = segurarRetrato()
      aberta = c.m.montar()
      await esperar()
      mexer(c.m)
      c.porta.soltar()
      await aberta
      await esperar()
      assert.equal(c.rolagens.length, 1, 'sem a segunda rolagem')
      // O termo novo é ela pedindo: rola de novo.
      c.m.props.termoBusca = 'P10'
      await esperar()
      await esperar()
      assert.equal(c.rolagens.length, 2)
    }
  }

  // ------------------------------------------------ o pedido pesquisado, com a lista já aberta
  {
    const respostas = resposta(dezesseis())
    const m = montar(REL, { ...props, termoBusca: '' }, { respostas, filhos })
    const rolagens = comRolagem(m)
    await m.montar()
    await esperar()
    assert.deepEqual(rolagens, [])
    const antes = m.chamadas.length

    // Fora das 3: vem para o topo, com destaque, e só o retrato dela vai.
    m.props.termoBusca = 'P9'
    await esperar()
    await esperar()
    let h = await m.html()
    assert.deepEqual(naTela(h), ['P9', 'P1', 'P2', 'P3', 'P13'])
    assert.deepEqual(comDestaque(h), ['P9'])
    assert.match(h, /data-outra-compra="P9"/)
    assert.match(h.slice(h.lastIndexOf('<li', h.indexOf('data-outra-compra="P9"')), h.indexOf('data-outra-compra="P9"') + 80), /ring-2/, 'o destaque é visível')
    assert.deepEqual(m.chamadas.slice(antes).map((c) => [c.metodo, c.url, c.corpo]), [['POST', `${BASE}/retratos`, { pedidos: ['P9'] }]], 'sem reler a lista')
    assert.deepEqual(rolagens, [{ sel: '[data-compra-pesquisada]', block: 'start', behavior: 'smooth' }], 'a tela vai até ela')

    // O tique da conversa não relê nem rola.
    m.props.podeAtualizar = false
    m.props.termoBusca = 'P9'
    await esperar()
    assert.equal(rolagens.length, 1)
    assert.equal(m.chamadas.length, antes + 1)

    // Pedaço de SKU: as duas no topo; pede só as que não tinham retrato.
    m.props.termoBusca = 'MALA-AZUL'
    await esperar()
    await esperar()
    h = await m.html()
    assert.deepEqual(naTela(h), ['P10', 'P15', 'P1', 'P2', 'P3', 'P13'])
    assert.deepEqual(comDestaque(h), ['P10', 'P15'])
    assert.deepEqual(m.chamadas.at(-1).corpo, { pedidos: ['P10', 'P15'] })
    assert.equal(rolagens.length, 2)

    // Limpou: volta ao normal, sem ida nenhuma e sem rolar.
    const depois = m.chamadas.length
    m.props.termoBusca = ''
    await esperar()
    await esperar()
    h = await m.html()
    assert.deepEqual(naTela(h), ['P1', 'P2', 'P3', 'P13'])
    assert.deepEqual(comDestaque(h), [])
    assert.equal(m.chamadas.length, depois)
    assert.equal(rolagens.length, 2)

    // De novo a mesma: o retrato já foi pedido nesta leitura.
    m.props.termoBusca = 'P9'
    await esperar()
    await esperar()
    assert.equal(m.chamadas.length, depois)
    assert.deepEqual(comDestaque(await m.html()), ['P9'])

    // Sem casar com nada: sem destaque, sem ida e sem rolar.
    const rolou = rolagens.length
    m.props.termoBusca = 'nada disso'
    await esperar()
    await esperar()
    h = await m.html()
    assert.deepEqual(naTela(h), ['P1', 'P2', 'P3', 'P13'])
    assert.equal(rolagens.length, rolou)
    assert.equal(m.chamadas.length, depois)

    // O "atualizar" e o "Carregar mais" com a busca aberta não mexem na rolagem.
    m.props.termoBusca = 'P9'
    await esperar()
    await esperar()
    const rolou2 = rolagens.length
    m.estado.carregarMais()
    await esperar()
    assert.equal(rolagens.length, rolou2)
    assert.equal(naTela(await m.html())[0], 'P9')
  }

  // ------------------------------------------------ a busca por SKU: o do Bling e o do retrato (06/11/2026)
  {
    // O caso medido: o resumo é o nome do anúncio; o SKU só está no Bling ou no retrato.
    const nome = '1× Chaleira Teste'
    const skuBling = (pedido, numero, skus) => indice({ pedido, itens_resumo: nome, bling: { numero, situacao_id: '12', situacao: 'Cancelado', link: null, skus } })
    const lista = [
      indice({ pedido: 'N1', itens_resumo: nome }),
      indice({ pedido: 'N2', itens_resumo: nome }),
      indice({ pedido: 'N3', itens_resumo: nome }),
      skuBling('ANTIGA1', '900002', ['QWX-55']),
      skuBling('ANTIGA2', '900003', ['ZK9-77']),
      indice({ pedido: 'SEM_BLING', itens_resumo: nome }),
    ]
    const retratos = { N2: retrato({ pedido: 'N2', itens: [{ titulo: 'Chaleira Teste', sku: 'SKU-01', quantidade: 1, preco: 10 }] }) }
    const respostas = {
      [BASE]: { itens: lista },
      [`${BASE}/retratos`]: (o) => ({ retratos: Object.fromEntries(o.body.pedidos.filter((p) => retratos[p]).map((p) => [p, retratos[p]])) }),
    }

    // A busca com a conversa aberta, por SKU que só existe em compra antiga, fora das 3.
    const m = montar(REL, { ...props, termoBusca: '' }, { respostas, filhos })
    const rolagens = comRolagem(m)
    await m.montar()
    await esperar()
    assert.deepEqual(naTela(await m.html()), ['N1', 'N2', 'N3'])
    const antes = m.chamadas.length
    m.props.termoBusca = 'ZK9-77'
    await esperar()
    await esperar()
    let h = await m.html()
    assert.deepEqual(naTela(h), ['ANTIGA2', 'N1', 'N2', 'N3'], 'no topo, com as 3 de sempre depois')
    assert.deepEqual(comDestaque(h), ['ANTIGA2'])
    assert.deepEqual(m.chamadas.slice(antes).map((c) => [c.metodo, c.url, c.corpo]), [['POST', `${BASE}/retratos`, { pedidos: ['ANTIGA2'] }]], 'sem reler a lista')
    assert.equal(rolagens.length, 1, 'a tela vai até ela')

    // O SKU que o cartão mostra, do retrato, destaca sem ir a lugar nenhum.
    const idas = m.chamadas.length
    m.props.termoBusca = 'sku-01'
    await esperar()
    await esperar()
    h = await m.html()
    assert.match(texto(h), /SKU: SKU-01/)
    assert.deepEqual(naTela(h), ['N2', 'N1', 'N3'])
    assert.deepEqual(comDestaque(h), ['N2'])
    assert.equal(m.chamadas.length, idas, 'o retrato já estava aqui')
    assert.equal(rolagens.length, 2)

    // Limpou a busca: volta ao normal.
    m.props.termoBusca = ''
    await esperar()
    await esperar()
    h = await m.html()
    assert.deepEqual(naTela(h), ['N1', 'N2', 'N3'])
    assert.deepEqual(comDestaque(h), [])

    // A conversa aberta vindo da busca: o retrato chega depois, o cartão sobe e a tela vai.
    const veio = montar(REL, { ...props, termoBusca: 'SKU-01' }, { respostas, filhos })
    const rolou = comRolagem(veio)
    await veio.montar()
    await esperar()
    h = await veio.html()
    assert.deepEqual(naTela(h), ['N2', 'N1', 'N3'])
    assert.deepEqual(comDestaque(h), ['N2'])
    assert.equal(rolou.length, 1, 'só depois do retrato: antes dele nada casava')

    // O "Carregar mais" conta as novas com os retratos de agora: a que está no topo só pelo
    // retrato, fora do limite, não é nova.
    const dez = Array.from({ length: 10 }, (_, n) => (n === 4 ? skuBling('P5', '900005', ['ZK9-77']) : indice({ pedido: `P${n + 1}`, itens_resumo: nome })))
    const mais = montar(REL, { ...props, termoBusca: 'ZK9-77' }, {
      respostas: {
        [BASE]: { itens: dez },
        [`${BASE}/retratos`]: (o) => ({ retratos: o.body.pedidos.includes('P5') ? { P5: retrato({ pedido: 'P5', itens: [{ titulo: 'Chaleira Teste', sku: 'SKU-55', quantidade: 1, preco: 10 }] }) } : {} }),
      },
      filhos,
    })
    await mais.montar()
    await esperar()
    mais.props.termoBusca = 'SKU-55'
    await esperar()
    await esperar()
    assert.deepEqual(comDestaque(await mais.html()), ['P5'], 'pelo retrato, sem precisar do Bling')
    mais.estado.carregarMais()
    await esperar()
    assert.deepEqual(naTela(await mais.html()), ['P5', 'P1', 'P2', 'P3', 'P4', 'P6', 'P7', 'P8', 'P9'], 'cinco novas')
  }

  // ------------------------------------------------ a conversa aberta já com a busca (veio da lista)
  {
    const m = montar(REL, { ...props, termoBusca: '306788' }, { respostas: resposta(dezesseis()), filhos })
    const rolagens = comRolagem(m)
    await m.montar()
    await esperar()
    const h = await m.html()
    assert.deepEqual(naTela(h), ['P13', 'P1', 'P2', 'P3'], 'a pesquisada pelo nº do Bling, no topo')
    assert.deepEqual(comDestaque(h), ['P13'])
    assert.deepEqual(m.chamadas[1].corpo, { pedidos: ['P13', 'P1', 'P2', 'P3'] })
    assert.equal(m.chamadas.length, 2)
    // Uma ao chegar a lista e outra depois do retrato: o painel, acima, empurra o cartão.
    assert.equal(rolagens.length, 2)

    // O termo é o pedido da conversa: aqui nada casa, e a tela não sai do lugar.
    const conversa = montar(REL, { ...props, termoBusca: props.pedidoDaConversa }, { respostas: resposta(dezesseis()), filhos })
    const nada = comRolagem(conversa)
    await conversa.montar()
    await esperar()
    const hc = await conversa.html()
    assert.deepEqual(comDestaque(hc), [])
    assert.deepEqual(naTela(hc), ['P1', 'P2', 'P3', 'P13'])
    assert.deepEqual(nada, [])
  }

  // ------------------------------------------------ o cartão que sai de Aguardando Cancelamento não some na mão da pessoa
  {
    const lista = dezesseis()
    const url = `${BASE}/P13/atualizar`
    const respostas = {
      ...resposta(lista),
      [url]: { retrato: null, item: indice({ pedido: 'P13', bling: { numero: '306788', situacao_id: '12', situacao: 'Cancelado', link: null } }), atualizado: true, motivo: null },
    }
    const m = montar(REL, props, { respostas, filhos })
    await m.montar()
    await esperar()
    const antes = m.chamadas.length
    await m.estado.atualizar('P13')
    const h = await m.html()
    assert.deepEqual(naTela(h), ['P1', 'P2', 'P3', 'P13'], 'continua na tela')
    assert.ok(!h.includes('data-compra-ag-cancelamento'), 'já sem o selo')
    assert.deepEqual(m.chamadas.slice(antes).map((c) => c.url), [url], 'sem reler a lista')
  }

  // ------------------------------------------------ retrato torto não quebra
  {
    const { retratoDaCompraDe, emAgCancelamento } = exportsDe(sfc(REL).descriptor)
    assert.equal(retratoDaCompraDe(null), null)
    assert.equal(retratoDaCompraDe('x'), null)
    assert.equal(retratoDaCompraDe({}), null, 'sem nº de pedido não é retrato')
    assert.equal(retratoDaCompraDe({ pedido: 'P', cancelamento: '  ' }).cancelamento, null)
    assert.equal(retratoDaCompraDe({ pedido: 'P', cancelamento: 'Out of Stock' }).cancelamento, 'Out of Stock')
    assert.equal(emAgCancelamento(null), false)
    assert.equal(emAgCancelamento({ situacao_id: '83955' }), true)
    assert.equal(emAgCancelamento({ situacao_id: '12' }), false)
  }

  // ------------------------------------------------ no painel: no fim da aba Pedido, só no chat do comprador
  {
    const { descriptor } = sfc(REL_PEDIDO)
    const tpl = descriptor.template.content
    const encaixe = tpl.indexOf('<AtendimentoOutrasCompras')
    assert.ok(encaixe > tpl.indexOf('<!-- No DaVinci -->'), 'depois de tudo do pedido da conversa')
    assert.ok(encaixe < tpl.indexOf('<!-- ═══ PRODUTO ═══ -->'), 'dentro da aba Pedido')
    assert.ok(encaixe > tpl.indexOf('<!-- ═══ PEDIDO ═══ -->'))
    assert.equal(tpl.indexOf('<AtendimentoOutrasCompras', encaixe + 1), -1, 'um encaixe só')
    const conversa = (extra = {}) => ({ id: 'c1', plataforma: 'shopee', canal: 'chat', comprador_id: '778899', afiliado: false, pedido_marketplace: 'P1', somente_leitura: false, ...extra })
    const baseProps = { contexto: null, pedidoMkt: null, produto: null, produtosConversa: [], canEdit: true, leituraAtiva: true, atualizavel: true }
    const filho = { props: ['conversaId', 'plataforma', 'pedidoDaConversa', 'podeAtualizar', 'perfil', 'termoBusca'], emits: ['compras'], setup: (p) => () => Vue.h('div', { 'data-filho': p.conversaId, 'data-plataforma': p.plataforma, 'data-pode': String(p.podeAtualizar), 'data-pedido': p.pedidoDaConversa, 'data-termo': p.termoBusca }) }
    const painel = (c, extra = {}) => montar(REL_PEDIDO, { ...baseProps, conversa: conversa(c), ...extra }, { filhos: { AtendimentoOutrasCompras: filho } })
    const aparece = async (c, extra) => (await painel(c, extra).html()).includes('data-filho="c1"')
    assert.equal(await aparece({}), true, 'Shopee, chat, com comprador')
    assert.equal(await aparece({ plataforma: 'tiktok' }), true, 'TikTok também')
    for (const p of ['ml', 'amazon', 'magalu', 'instagram']) assert.equal(await aparece({ plataforma: p }), false, p)
    assert.equal(await aparece({ canal: 'email' }), false, 'e-mail')
    assert.equal(await aparece({ canal: 'reclamacao' }), false, 'reclamação')
    assert.equal(await aparece({ afiliado: true }), false, 'chat de afiliado')
    assert.equal(await aparece({ comprador_id: null }), false, 'sem comprador')

    let h = await painel({}).html()
    assert.match(h, /data-pode="true"/)
    assert.match(h, /data-pedido="P1"/)
    assert.match(await painel({ plataforma: 'tiktok' }).html(), /data-pode="false"/, 'TikTok não tem retrato: sem o botão')
    assert.match(await painel({}, { leituraAtiva: false }).html(), /data-pode="false"/, 'leitura desligada')
    assert.match(await painel({}, { canEdit: false }).html(), /data-pode="false"/, 'sem permissão')
    assert.match(await painel({ somente_leitura: true }).html(), /data-pode="false"/, 'conversa só leitura')
    assert.match(await painel({}, { atualizavel: false }).html(), /data-pode="false"/, 'loja não atualizável')
    assert.match(await painel({ pedido_marketplace: null }).html(), /data-pode="true"/, 'o botão do cartão não depende do pedido da conversa')
    // O bloco de cima não muda e não depende da lista.
    assert.ok(h.indexOf('data-filho') > h.indexOf('No DaVinci'), 'por último na tela')

    // O termo vem da busca da lista (`filtros.q`), por prop, até a lista.
    assert.match(await painel({}, { termoBusca: 'P9' }).html(), /data-termo="P9"/)
    assert.match(fs.readFileSync(path.resolve(__dirname, '../pages/atendimento.vue'), 'utf8'), /:ativa="naCaixa && !emailNaTela"\s+:termo-busca="filtros\.q"/, 'a conversa da Caixa')
    assert.match(fs.readFileSync(path.resolve(__dirname, '../components/AtendimentoConversa.vue'), 'utf8'), /<AtendimentoPedido[\s\S]*?:termo-busca="termoBusca"[\s\S]*?\/>/)
    assert.match(tpl, /<AtendimentoOutrasCompras[\s\S]*?:termo-busca="termoBusca"[\s\S]*?\/>/)

    // O termo é o pedido da conversa: o destaque fica no bloco de cima.
    const comRetrato = { pedidoMkt: retrato({ pedido: 'P1' }) }
    const destacado = async (c, extra) => (await painel(c, extra).html()).includes('data-pedido-pesquisado')
    assert.equal(await destacado({}, { ...comRetrato, termoBusca: 'P1' }), true, 'com retrato')
    assert.equal(await destacado({}, { ...comRetrato, termoBusca: ' p1 ' }), true, 'em minúscula')
    assert.equal(await destacado({}, { termoBusca: 'P1' }), true, 'sem retrato, no bloco tracejado')
    assert.equal(await destacado({}, { ...comRetrato, contexto: { pedido: { numero: '306797' } }, termoBusca: '306797' }), true, 'o nº do Bling')
    assert.equal(await destacado({}, { ...comRetrato, termoBusca: 'P' }), false, 'pedaço não')
    assert.equal(await destacado({}, { ...comRetrato, termoBusca: 'P9' }), false, 'outra compra: o destaque é na lista')
    assert.equal(await destacado({}, comRetrato), false, 'sem busca')
    assert.equal(await destacado({ plataforma: 'ml' }, { ...comRetrato, termoBusca: 'P1' }), false, 'fora do chat do comprador, nada muda')
    {
      const bloco = painel({}, { ...comRetrato, termoBusca: 'P1' })
      const h1 = await bloco.html()
      assert.match(h1.slice(h1.lastIndexOf('<section', h1.indexOf('data-pedido-pesquisado')), h1.indexOf('data-pedido-pesquisado')), /ring-2/, 'visível')
      assert.equal(h1.indexOf('data-pedido-pesquisado') < h1.indexOf('data-filho'), true, 'acima da lista')
      // Limpou: volta ao normal.
      bloco.props.termoBusca = ''
      assert.ok(!(await bloco.html()).includes('data-pedido-pesquisado'))
      // A busca muda com a tela lá embaixo: ela volta até o bloco; ao limpar, não mexe.
      const rolagens = comRolagem(bloco, 'rolagemPainel')
      bloco.props.termoBusca = 'P1'
      await esperar()
      await esperar()
      assert.deepEqual(rolagens, [{ sel: '[data-pedido-pesquisado]', block: 'nearest', behavior: 'smooth' }])
      bloco.props.termoBusca = ''
      await esperar()
      await esperar()
      assert.equal(rolagens.length, 1)
    }

    // Chat sem pedido ligado e com anúncio abre no Produto, com busca ou sem. O código de
    // uma compra digitado com o chat aberto em outra aba leva ao Pedido; o nome, não.
    const semPedidoComAnuncio = { pedido_marketplace: null, anuncio_titulo: 'Mala de Bordo' }
    assert.equal(await aparece(semPedidoComAnuncio), false, 'sem busca, a aba Produto, como sempre')
    assert.equal(await aparece(semPedidoComAnuncio, { termoBusca: 'rosa' }), false, 'a busca pelo nome não muda a aba')
    for (const [termo, aba] of [['rosa', 'produto'], ['12', 'produto'], ['maria 306788', 'produto'], ['2610301X726956', 'pedido'], ['306788', 'pedido'], ['A019.US', 'pedido']]) {
      const m = painel(semPedidoComAnuncio, { termoBusca: '' })
      assert.equal(m.estado.aba, 'produto')
      m.props.termoBusca = termo
      await esperar()
      assert.equal(m.estado.aba, aba, termo)
    }
    {
      const m = painel({ ...semPedidoComAnuncio, plataforma: 'ml' }, { termoBusca: '' })
      m.props.termoBusca = '2610301X726956'
      await esperar()
      assert.equal(m.estado.aba, 'produto', 'fora do chat do comprador, nada muda')
    }

    // "Conversa sem pedido ligado" acima da lista parecia contradizê-la: só sem compras.
    const FRASE = 'Conversa sem pedido ligado'
    const avisa = { props: filho.props, emits: filho.emits, setup: (p, { emit }) => { emit('compras', Number(p.termoBusca)); return () => Vue.h('div', { 'data-filho': p.conversaId }) } }
    const semPedido = (termo) => montar(REL_PEDIDO, { ...baseProps, conversa: conversa({ pedido_marketplace: null }), termoBusca: termo }, { filhos: { AtendimentoOutrasCompras: avisa } })
    {
      const m = semPedido('0')
      assert.ok(texto(await m.html()).includes(FRASE), 'antes de a lista dizer')
      assert.ok(texto(await m.html()).includes(FRASE), 'sem compras, a frase fica')
      const com = semPedido('2')
      assert.ok(texto(await com.html()).includes(FRASE), 'a primeira renderização é antes do evento')
      const hCom = await com.html()
      assert.ok(!texto(hCom).includes(FRASE), 'com compras, sem a frase')
      assert.ok(!hCom.includes('border-dashed px-3 py-4'), 'nem o bloco, que só teria o ícone')
      assert.ok(hCom.includes('data-filho'), 'a lista fica')
      com.estado.comprasNaLista = 0
      assert.ok(texto(await com.html()).includes(FRASE), 'a lista esvaziou: a frase volta')
      // Com pedido ligado o bloco sem retrato segue, com ou sem compras na lista.
      const ligado = painel({})
      ligado.estado.comprasNaLista = 2
      assert.match(texto(await ligado.html()), /P1/)
      assert.match(await ligado.html(), /border-dashed px-3 py-4/)
      // No TikTok a frase é outra e fica.
      const tiktok = montar(REL_PEDIDO, { ...baseProps, conversa: conversa({ plataforma: 'tiktok', pedido_marketplace: null }) }, { filhos: { AtendimentoOutrasCompras: filho } })
      tiktok.estado.comprasNaLista = 2
      assert.match(texto(await tiktok.html()), /ainda não vem pela API/)
    }
  }
}

principal().then(
  () => console.log('ok: atendimento-outras-compras (cartões, retrato à parte, sem ação, só no chat do comprador)'),
  (e) => { console.error(e); process.exit(1) },
)
