// node tests/atendimento-devolucao-pedido.cjs — a devolução da Shopee dentro do
// pedido (04/11/2026). O dono: "o negócio é pra aquele pedido, então se
// reconhecer a devolução fica naquele" e "se assemelhar mais ao Duoke, mas que
// mantenha organizacionalmente bem".
//  - o bloco (AtendimentoDevolucaoPedido): título (status) e nº com copiar,
//    Reembolso em vermelho, Rastreio da volta com copiar, Situação da volta,
//    Motivo, Pede, Restante com "até dd/mm hh:mm", ação pendente e "Abrir na
//    Shopee", nessa ordem; linha sem dado não aparece; só leitura;
//  - o painel (AtendimentoPedido): o bloco entre os itens e os valores e, sem
//    retrato, antes do "sem retrato"; só `do_pedido` e aberta; retrato de OUTRO
//    pedido na Shopee é tratado como sem retrato (no ML, não: o ML tem pack);
//  - o cartão do topo (AtendimentoReclamacao): o caso do pedido só com a linha
//    do título e o "ver no pedido"; encerradas e casos de outro pedido iguais;
//  - a conversa (AtendimentoConversa): guarda a resposta, zera na troca, passa
//    ao painel, relê quando o pedido do chat muda e abre o painel até o bloco.
// Aqui: o setup de verdade (Vue SSR, API falsa), como em
// atendimento-recolher-caixas, e os trechos da conversa com dublês.
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
  const responder = async (url, opts = {}) => {
    chamadas.push({ url, metodo: opts.method || 'GET' })
    if (url in respostas) return structuredClone(respostas[url])
    throw new Error(`rota inesperada: ${url}`)
  }
  const g = {
    ref: Vue.ref, computed: Vue.computed, reactive: Vue.reactive, watch: Vue.watch, nextTick: Vue.nextTick,
    onMounted: (fn) => montados.push(fn), onBeforeUnmount: () => {},
    useApi: () => ({ url: (v) => v, api: responder }),
    useToasts: () => ({ success: (t) => avisos.push(['ok', t]), error: (t) => avisos.push(['erro', t]), info: () => {}, warning: () => {} }),
    localStorage: { getItem: () => null, setItem: () => {} },
    window: { prompt: () => {}, addEventListener: () => {}, removeEventListener: () => {} },
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
  return { html, estado, chamadas, avisos, emitidos, props: reativo, async montar() { for (const fn of montados) await fn(); await esperar() } }
}

const abrirFalso = { props: ['href', 'perfil', 'conversaId', 'titulo'], setup: (p, { slots }) => () => Vue.h('a', { href: p.href, 'data-abrir-plataforma': '' }, slots.default?.()) }
// O caso medido: Shopee, Em andamento, com `due_date` futuro e a resposta pendente.
const em3dias = () => new Date(Date.now() + 3 * 24 * 3600 * 1000 + 60_000).toISOString()
const caso = (extra = {}) => ({
  id: 'r1', plataforma: 'shopee', plataforma_nome: 'Shopee', numero: '2510200001', tipo: 'devolucao', tipo_rotulo: 'Devolução',
  status: 'PROCESSING', status_rotulo: 'Em andamento', aberta: true, motivo: 'FUNCTIONAL_DMG', solucao: 'Devolução + reembolso',
  pedido_marketplace: '251020AAA', prazo_em: em3dias(), aberta_em: '2026-10-20T12:00:00Z', encerrada_em: null,
  acao_pendente: 'Responder a proposta do comprador', mediacao: false, reputacao_afetada: null, devolucao_status: null, conversa_id: 'c1',
  url_plataforma: 'https://seller.shopee.com.br/portal/sale/returnrefund/1',
  do_pedido: true, valor_reembolso: 311.91, moeda: 'BRL', rastreio_volta: 'BR123456789XX', situacao_volta: 'A caminho do vendedor',
  ...extra,
})

const P = requerer('~/components/AtendimentoPlataforma.vue')
const blocoRel = '../components/AtendimentoDevolucaoPedido.vue'
const REL_PEDIDO = '../components/AtendimentoPedido.vue'
const REL_CARTAO = '../components/AtendimentoReclamacao.vue'

async function principal() {
  // ------------------------------------------------ o bloco, completo e na ordem
  {
    const r = caso()
    const m = montar(blocoRel, { r, perfil: null, conversaId: 'c1' }, { filhos: { AtendimentoAbrirPlataforma: abrirFalso } })
    await m.montar()
    const h = await m.html()
    const t = texto(h)
    assert.match(h, /data-devolucao-pedido="r1"/, 'é por aqui que o "ver no pedido" acha o bloco')
    assert.match(t, /Devolução Em andamento nº 2510200001/, 'o título (status) e o nº')
    assert.match(h, /aria-label="copiar o nº da devolução"/)
    assert.match(t, /Reembolso -R\$ 311,91/)
    assert.match(h, /text-red-600 dark:text-red-400[^>]*>-R\$/, 'em vermelho, nos dois temas')
    assert.match(t, /Rastreio da volta BR123456789XX/)
    assert.match(h, /aria-label="copiar o rastreio da volta"/)
    assert.match(t, /Situação da volta A caminho do vendedor/)
    assert.match(t, /Motivo Produto com defeito \(não funciona\)/, 'o motivo em português')
    assert.match(t, /Pede Devolução \+ reembolso/)
    assert.match(t, /Restante 3 d/, 'a contagem, sem repetir o "faltam"')
    assert.ok(t.includes(`até ${P.fmtDataHora(r.prazo_em).replace(',', '')}`), 'a data do prazo')
    assert.match(t, /até \d\d\/\d\d \d\d:\d\d/, 'até dd/mm hh:mm')
    assert.match(t, /Responder a proposta do comprador/)
    assert.match(h, /data-abrir-plataforma[^>]*>[\s\S]*Abrir na Shopee/)
    assert.match(h, /href="https:\/\/seller\.shopee\.com\.br\/portal\/sale\/returnrefund\/1"/)
    // A ordem do Duoke.
    const ordem = ['Em andamento', 'nº 2510200001', 'Reembolso', 'Rastreio da volta', 'Situação da volta', 'Motivo', 'Pede', 'Restante', 'Responder a proposta', 'Abrir na Shopee'].map((x) => t.indexOf(x))
    assert.ok(ordem.every((p) => p >= 0), 'todas as linhas')
    assert.deepEqual(ordem, ordem.slice().sort((a, b) => a - b), 'na ordem')
    // Código cru da plataforma nunca aparece.
    for (const cru of ['FUNCTIONAL_DMG', 'PROCESSING', 'SHOPEE_RESPONDER_PROPOSTA']) assert.ok(!h.includes(cru), cru)
    // Só leitura: renderizar não chama a API.
    assert.deepEqual(m.chamadas, [], 'zero chamada')
    // Copiar leva o valor; sem área de transferência, o prompt, como os vizinhos.
    await m.estado.copiarTexto('BR123456789XX', 'Rastreio')
    assert.deepEqual(m.chamadas, [], 'copiar também não')
  }

  // ------------------------------------------------ sem dado, sem linha
  {
    const r = caso({ valor_reembolso: null, moeda: null, rastreio_volta: null, situacao_volta: null, motivo: null, solucao: null, prazo_em: null, acao_pendente: null, url_plataforma: null, numero: null })
    const m = montar(blocoRel, { r, perfil: null, conversaId: 'c1' }, { filhos: { AtendimentoAbrirPlataforma: abrirFalso } })
    await m.montar()
    const h = await m.html()
    const t = texto(h)
    for (const rotulo of ['Reembolso', 'Rastreio da volta', 'Situação da volta', 'Motivo', 'Pede', 'Restante', 'até ', 'Abrir na', 'copiar', 'null', 'undefined']) {
      assert.ok(!h.includes(rotulo), `sem dado, sem "${rotulo}"`)
    }
    assert.match(t, /Devolução Em andamento Devolução do pedido 251020AAA/, 'sem nº, pelo pedido')

    // Campos que a API antiga não manda (undefined), igual a null.
    const antigo = caso()
    for (const k of ['do_pedido', 'valor_reembolso', 'moeda', 'rastreio_volta', 'situacao_volta']) delete antigo[k]
    const a = montar(blocoRel, { r: antigo, perfil: null, conversaId: 'c1' }, { filhos: { AtendimentoAbrirPlataforma: abrirFalso } })
    const ha = await a.html()
    for (const rotulo of ['Reembolso', 'Rastreio da volta', 'Situação da volta']) assert.ok(!ha.includes(rotulo), rotulo)

    // Reembolso zero não é valor: é a Shopee ainda sem ele.
    const z = montar(blocoRel, { r: caso({ valor_reembolso: 0 }), perfil: null, conversaId: 'c1' }, { filhos: { AtendimentoAbrirPlataforma: abrirFalso } })
    assert.ok(!(await z.html()).includes('Reembolso'), 'sem linha de -R$ 0,00')
  }

  // ------------------------------------------------ o prazo
  {
    const vencido = new Date(Date.now() - 3 * 3600 * 1000 - 60_000).toISOString()
    const v = montar(blocoRel, { r: caso({ prazo_em: vencido }), perfil: null, conversaId: 'c1' }, { filhos: { AtendimentoAbrirPlataforma: abrirFalso } })
    const hv = await v.html()
    assert.match(texto(hv), /Restante prazo vencido há 3 h/)
    assert.match(hv, /bg-red-600 text-white[^>]*>prazo vencido/)
    const perto = new Date(Date.now() + 5 * 3600 * 1000 + 60_000).toISOString()
    const p = montar(blocoRel, { r: caso({ prazo_em: perto }), perfil: null, conversaId: 'c1' }, { filhos: { AtendimentoAbrirPlataforma: abrirFalso } })
    const hp = await p.html()
    assert.match(texto(hp), /Restante 5 h/)
    assert.match(hp, /bg-amber-500\/20[^>]*>5 h/, 'menos de 24 h: o mesmo alerta do cartão')
    // O relógio manda: o tique refaz a contagem sem reler a API.
    assert.equal(p.chamadas.length, 0)
  }

  // ------------------------------------------------ só leitura, no fonte
  {
    const fonte = sfc(blocoRel).fonte
    assert.doesNotMatch(fonte, /method:\s*'(POST|PUT|PATCH|DELETE)'/i)
    assert.doesNotMatch(fonte, /useApi\(/, 'o bloco nem fala com a API')
    for (const proibido of ['Reembolso total', 'Aceitar devolução', 'Oferecer solução', 'Pedir mediação', 'text_reason', 'descricao']) {
      assert.ok(!sfc(blocoRel).descriptor.template.content.includes(proibido), proibido)
    }
    // Temas claro e escuro por tokens, como os vizinhos: nada de cor fixa solta.
    assert.doesNotMatch(sfc(blocoRel).descriptor.template.content, /#[0-9a-f]{3,8}\b|bg-white|text-black/i)
  }

  // ------------------------------------------------ retrato de outro pedido
  {
    const { retratoDoPedido } = exportsDe(sfc(REL_PEDIDO).descriptor)
    const retrato = { pedido: 'B' }
    assert.equal(retratoDoPedido(retrato, { plataforma: 'shopee', pedido_marketplace: 'B' }), retrato)
    assert.equal(retratoDoPedido(retrato, { plataforma: 'shopee', pedido_marketplace: 'A' }), null, 'Shopee: outro pedido, sem retrato')
    assert.equal(retratoDoPedido(retrato, { plataforma: 'shopee', pedido_marketplace: null }), retrato, 'sem pedido na conversa, não há o que comparar')
    assert.deepEqual(retratoDoPedido({ pedido: null, itens: [] }, { plataforma: 'shopee', pedido_marketplace: 'A' }), { pedido: null, itens: [] })
    assert.equal(retratoDoPedido(retrato, { plataforma: 'ml', pedido_marketplace: 'pack-1' }), retrato, 'ML: o nº do pack difere de propósito')
    assert.equal(retratoDoPedido(retrato, { plataforma: 'tiktok', pedido_marketplace: 'A' }), retrato)
    assert.equal(retratoDoPedido(null, { plataforma: 'shopee', pedido_marketplace: 'A' }), null)
  }

  // ------------------------------------------------ o painel: os dois encaixes e o filtro
  {
    const filhos = {
      AtendimentoDevolucaoPedido: { props: ['r', 'perfil', 'conversaId'], setup: (p) => () => Vue.h('section', { 'data-bloco': p.r.id, 'data-conversa': p.conversaId }, `bloco ${p.r.id}`) },
    }
    const conversa = (pedido, plataforma = 'shopee') => ({ id: 'c1', plataforma, canal: 'chat', pedido_marketplace: pedido, somente_leitura: false, email: null })
    const retrato = (pedido) => ({
      pedido, moeda: 'BRL', status: 'COMPLETED', status_texto: 'Concluído', itens: [{ titulo: `Produto do ${pedido}`, preco: 10, quantidade: 1 }],
      valor_pago: 100, total: 100, frete: 0, pagamento_metodo: 'Pix', pago_em: null, criado_em: null, enviado_em: null, concluido_em: null, nf: null, logistica: null, atualizado_em: null,
    })
    const reclamacoes = {
      abertas: 4,
      prazo_mais_curto: null,
      itens: [
        caso({ id: 'do-pedido' }),
        caso({ id: 'sem-marca', do_pedido: false }),
        caso({ id: 'encerrada', aberta: false, encerrada_em: '2026-10-21T12:00:00Z' }),
        caso({ id: 'outro-pedido', do_pedido: false, pedido_marketplace: 'OUTRO' }),
        caso({ id: 'atrasada', pedido_marketplace: 'OUTRO' }), // `do_pedido` velho: o chat já foi para outro pedido
      ],
    }
    const base = { contexto: null, produto: null, produtosConversa: [], canEdit: true, painel: null }
    const painel = (props) => montar(REL_PEDIDO, { ...base, ...props }, { filhos })

    // Com retrato: entre os itens e os valores.
    let m = painel({ conversa: conversa('251020AAA'), pedidoMkt: retrato('251020AAA'), reclamacoes })
    await m.montar()
    let h = await m.html()
    assert.deepEqual([...h.matchAll(/data-bloco="([^"]+)"/g)].map((x) => x[1]), ['do-pedido'], 'só a aberta e `do_pedido`, deste pedido')
    assert.match(h, /data-conversa="c1"/)
    const iBloco = h.indexOf('data-bloco=')
    assert.ok(h.indexOf('Produto do 251020AAA') < iBloco, 'depois dos itens')
    assert.ok(iBloco < h.indexOf('Valor pago pelo comprador'), 'antes dos valores')
    assert.deepEqual(m.chamadas, [], 'o painel não busca nada para o bloco')

    // Sem retrato: antes do "sem retrato", sem sumir.
    m = painel({ conversa: conversa('251020AAA'), pedidoMkt: null, reclamacoes })
    await m.montar()
    h = await m.html()
    assert.equal((h.match(/data-bloco=/g) || []).length, 1, 'o bloco aparece sem retrato')
    assert.ok(h.indexOf('data-bloco=') < h.indexOf('ainda não foi buscado'), 'antes do "sem retrato"')

    // Retrato de outro pedido: nunca o nº de um com os itens do outro.
    m = painel({ conversa: conversa('251020AAA'), pedidoMkt: retrato('251019BBB'), reclamacoes })
    await m.montar()
    h = await m.html()
    assert.ok(!h.includes('Produto do 251019BBB'), 'os itens do outro pedido não entram')
    assert.ok(!h.includes('251019BBB'), 'nem o nº')
    assert.match(h, /ainda não foi buscado/)
    assert.match(h, /251020AAA/, 'o nº é o do pedido da conversa')
    assert.equal(m.estado.davinciAberto, true, 'sem retrato, o No DaVinci começa aberto')

    // No ML o retrato do pack continua valendo, e nenhum bloco nasce sem `do_pedido`.
    const ml = caso({ id: 'ml-1', plataforma: 'ml', plataforma_nome: 'Mercado Livre', do_pedido: false, pedido_marketplace: 'pack-1' })
    m = painel({ conversa: conversa('pack-1', 'ml'), pedidoMkt: retrato('2000000001'), reclamacoes: { itens: [ml], abertas: 1, prazo_mais_curto: null } })
    await m.montar()
    h = await m.html()
    assert.match(h, /Produto do 2000000001/)
    assert.ok(!h.includes('data-bloco='))

    // Sem a resposta (ainda não veio, ou falhou): nada do bloco, o resto igual.
    m = painel({ conversa: conversa('251020AAA'), pedidoMkt: retrato('251020AAA'), reclamacoes: null })
    await m.montar()
    h = await m.html()
    assert.ok(!h.includes('data-bloco='))
    assert.match(h, /Valor pago pelo comprador/)

    // Duas abertas do mesmo pedido: dois blocos, o prazo mais curto antes, sem empilhar pedidos.
    const perto = new Date(Date.now() + 3600 * 1000).toISOString()
    m = painel({ conversa: conversa('251020AAA'), pedidoMkt: null, reclamacoes: { abertas: 2, prazo_mais_curto: perto, itens: [caso({ id: 'longe' }), caso({ id: 'perto', prazo_em: perto })] } })
    await m.montar()
    h = await m.html()
    assert.deepEqual([...h.matchAll(/data-bloco="([^"]+)"/g)].map((x) => x[1]), ['perto', 'longe'])

    // O "ver no pedido": volta para a aba Pedido e rola até o bloco certo.
    m = painel({ conversa: conversa('251020AAA'), pedidoMkt: retrato('251020AAA'), reclamacoes })
    await m.montar()
    m.estado.aba = 'produto'
    const rolados = []
    const bloco = (id) => ({ dataset: { devolucaoPedido: id }, scrollIntoView: (o) => rolados.push([id, o.block]) })
    m.estado.rolagemPainel = { querySelectorAll: (sel) => { assert.equal(sel, '[data-devolucao-pedido]'); return [bloco('um'), bloco('dois')] } }
    await m.estado.mostrarDevolucao('dois')
    assert.equal(m.estado.aba, 'pedido')
    assert.deepEqual(rolados, [['dois', 'center']])
    // Sem o elemento (o bloco já saiu): não quebra.
    m.estado.rolagemPainel = null
    await m.estado.mostrarDevolucao('dois')
  }

  // ------------------------------------------------ o cartão do topo encolhe
  {
    const doPedido = caso()
    const deOutro = caso({ id: 'r2', numero: '2510200002', do_pedido: false, pedido_marketplace: 'OUTRO', status: 'REQUESTED', status_rotulo: 'Pedido de devolução aberto', valor_reembolso: null })
    const encerrada = caso({ id: 'r3', numero: '2509010003', aberta: false, status: 'CLOSED', status_rotulo: 'Encerrada pela Shopee', prazo_em: null, encerrada_em: '2026-09-10T12:00:00Z', do_pedido: false })
    const url = '/api/atendimento/conversas/c1/reclamacoes'
    const respostas = { [url]: { itens: [doPedido, deOutro, encerrada], abertas: 2, prazo_mais_curto: doPedido.prazo_em } }
    const m = montar(REL_CARTAO, { conversaId: 'c1', perfil: null }, { respostas, filhos: { AtendimentoAbrirPlataforma: abrirFalso } })
    await m.montar()
    const h = await m.html()
    const cartoes = h.split('rounded-md border text-[13px] leading-5').slice(1)
    assert.equal(cartoes.length, 2, 'os dois cartões abertos')
    const [compacto, inteiro] = cartoes
    assert.match(compacto, /nº 2510200001/, 'o do pedido primeiro (prazo mais curto)')
    assert.match(inteiro, /nº 2510200002/)
    assert.match(texto(compacto), /Devolução/, 'o tipo')
    assert.match(texto(compacto), /nº 2510200001/, 'o nº')
    assert.match(compacto, /aria-label="copiar o nº"/)
    assert.match(texto(compacto), /· Em andamento/, 'o status')
    assert.match(texto(compacto), /faltam 3 d/, 'o prazo')
    assert.match(compacto, /data-reclamacao-ver-no-pedido[^>]*>ver no pedido</)
    // Só a linha do título: o resto mora no bloco.
    for (const fora of ['data-reclamacao-corpo', 'data-reclamacao-alternar', 'data-icone="ChevronDown"', 'data-icone="ChevronRight"', 'Motivo', 'Pede', 'Só leitura aqui', 'Abrir na Shopee', 'Responder a proposta', '· Shopee']) {
      assert.ok(!compacto.includes(fora), `o caso do pedido sem "${fora}"`)
    }
    assert.match(h, /class="rounded-md border text-\[13px\] leading-5 [^"]*px-3 py-1\.5"/, 'numa linha, mesmo com o cartão aberto')

    // O caso de OUTRO pedido: o cartão de sempre, inteiro.
    assert.match(inteiro, /data-reclamacao-corpo/)
    assert.match(inteiro, /Só leitura aqui: responda e aja pela plataforma\./)
    assert.match(inteiro, /Abrir na Shopee/)
    assert.match(inteiro, /· Shopee/)
    assert.ok(!inteiro.includes('ver no pedido'), 'o de outro pedido não está no painel')
    assert.match(h, /data-reclamacao-alternar/, 'o chevron do cartão inteiro continua')
    // A encerrada, igual: recolhida na linha de baixo, sem bloco.
    assert.match(h, /1 encerrada/)
    assert.match(texto(h), /nº 2509010003 · Encerrada pela Shopee/)

    // Nenhuma chamada além do GET; a conversa recebe a resposta e o clique, o caso.
    assert.deepEqual(m.chamadas.map((c) => c.metodo), ['GET'])
    assert.deepEqual(m.emitidos[0][0], 'carregado', 'a conversa continua recebendo a resposta inteira')
    assert.equal(m.emitidos[0][1].itens.length, 3)
    assert.match(sfc(REL_CARTAO).descriptor.template.content, /@click="emit\('verNoPedido', r\.id\)"/)

    // Resposta da API antiga (sem `do_pedido`): tudo como antes.
    const velho = caso()
    delete velho.do_pedido
    const a = montar(REL_CARTAO, { conversaId: 'c1', perfil: null }, { respostas: { [url]: { itens: [velho], abertas: 1, prazo_mais_curto: null } }, filhos: { AtendimentoAbrirPlataforma: abrirFalso } })
    await a.montar()
    const ha = await a.html()
    assert.match(ha, /data-reclamacao-corpo/)
    assert.match(ha, /data-reclamacao-alternar/)
    assert.ok(!ha.includes('ver no pedido'))
  }

  // ------------------------------------------------ a conversa liga as pontas
  {
    const conv = sfc('../components/AtendimentoConversa.vue').descriptor
    const setup = conv.scriptSetup.content
    const tpl = conv.template.content
    // Guarda a resposta inteira, zera junto na troca de conversa e passa ao painel.
    assert.match(setup, /reclamacoesDados\.value = null\n\s*reclamacoesQtd\.value = 0/)
    assert.match(tpl, /<AtendimentoPedido[\s\S]*?:reclamacoes="reclamacoesDados"/)
    assert.match(tpl, /@carregado="aoCarregarReclamacoes" @ver-no-pedido="verDevolucaoNoPedido"/)
    // A chave segue só a conversa: remontar a cada citação de outra compra voltaria a aba e
    // fecharia o diálogo de troca, também sem devolução nenhuma (revisão de 04/11/2026).
    assert.match(tpl, /<AtendimentoPedido\s+:key="detalhe\.conversa\.id"\s+ref="painelPedidoRef"/, 'a chave segue só a conversa')

    const js = trecho(setup, 'const reclamacaoRef = ref', '// Carrinho do site e comentário')
      + '\nreturn { reclamacaoRef, reclamacoesQtd, reclamacoesDados, aoCarregarReclamacoes, painelPedidoRef, verDevolucaoNoPedido }'
    const detalhe = Vue.ref({ conversa: { id: 'c1', pedido_marketplace: 'B' } })
    const pedidoVisivel = Vue.ref(false)
    const passos = []
    const alternarPedido = () => { passos.push('abrir'); pedidoVisivel.value = true }
    const painelDados = Vue.ref({ numero: 'do pedido anterior' })
    const painelRelido = []
    const garantiaRelida = []
    const carregarPainel = (id) => { painelRelido.push(id) }
    const carregarGarantia = (id) => { garantiaRelida.push(id) }
    const x = new Function('ref', 'watch', 'nextTick', 'detalhe', 'pedidoVisivel', 'alternarPedido', 'painelDados', 'carregarPainel', 'carregarGarantia', js)(Vue.ref, Vue.watch, Vue.nextTick, detalhe, pedidoVisivel, alternarPedido, painelDados, carregarPainel, carregarGarantia)
    const resposta = { itens: [caso()], abertas: 1, prazo_mais_curto: null }
    x.aoCarregarReclamacoes(resposta)
    assert.equal(Vue.toRaw(x.reclamacoesDados.value), resposta, "a resposta inteira")
    assert.equal(x.reclamacoesQtd.value, 1)

    // O pedido do chat mudou com a conversa aberta: relê na hora.
    let releu = 0
    x.reclamacaoRef.value = { carregar: async () => { releu += 1 } }
    detalhe.value = { conversa: { id: 'c1', pedido_marketplace: 'A' } }
    await Vue.nextTick()
    assert.equal(releu, 1, 'o foco entrou: relê')
    // O estoque e o nº do Bling eram do pedido anterior: somem e são relidos na hora.
    assert.equal(painelDados.value, null, 'o painel não fica com os dados do outro pedido')
    assert.deepEqual(painelRelido, ['c1'])
    assert.deepEqual(garantiaRelida, ['c1'])
    detalhe.value = { conversa: { id: 'c1', pedido_marketplace: 'A' } }
    await Vue.nextTick()
    assert.equal(releu, 1, 'a releitura de 15 s com o mesmo pedido não busca nada')
    assert.deepEqual(painelRelido, ['c1'], 'nem o painel')
    // Trocar de conversa não relê por aqui: o cartão já relê pelo conversaId.
    detalhe.value = null
    await Vue.nextTick()
    detalhe.value = { conversa: { id: 'c2', pedido_marketplace: 'Z' } }
    await Vue.nextTick()
    assert.equal(releu, 1, 'troca de conversa não dobra o GET')
    assert.deepEqual(painelRelido, ['c1'], 'nem o do painel')

    // "ver no pedido": painel fechado abre; depois rola até o bloco.
    x.painelPedidoRef.value = { mostrarDevolucao: async (id) => { passos.push(`rolar ${id}`) } }
    await x.verDevolucaoNoPedido('r1')
    assert.deepEqual(passos, ['abrir', 'rolar r1'])
    // Painel já aberto: não fecha; só rola.
    await x.verDevolucaoNoPedido('r2')
    assert.deepEqual(passos, ['abrir', 'rolar r1', 'rolar r2'])
  }

  // ------------------------------------------------ as linhas de sempre continuam
  {
    const reclamacao = sfc(REL_CARTAO).fonte
    assert.doesNotMatch(reclamacao, /method:\s*'(POST|PUT|PATCH|DELETE)'/i, 'o cartão segue só leitura')
    const painel = sfc(REL_PEDIDO).descriptor.template.content
    const a = painel.indexOf('<AtendimentoDevolucaoPedido')
    const b = painel.indexOf('<AtendimentoDevolucaoPedido', a + 1)
    assert.ok(a > painel.indexOf('<!-- itens -->') && a < painel.indexOf('<!-- valores e pagamento -->'), 'entre os itens e os valores')
    assert.ok(b > painel.indexOf('<!-- sem retrato') && b < painel.indexOf('{{ semRetrato }}'), 'antes do "sem retrato"')
    assert.equal(painel.indexOf('<AtendimentoDevolucaoPedido', b + 1), -1, 'só os dois encaixes')
  }
}

principal().then(
  () => console.log('ok: atendimento-devolucao-pedido (bloco no pedido, cartão encolhido, retrato de outro pedido)'),
  (e) => { console.error(e); process.exit(1) },
)
