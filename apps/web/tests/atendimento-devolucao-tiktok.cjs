// node tests/atendimento-devolucao-tiktok.cjs — a devolução do TikTok inteira na aba
// Pedido (18/11/2026). O dono, com o Duoke ao lado (Poofy, pedido 586887215793276360):
// "tão completo quanto o Duoke" — produto (foto, variação, SKU, preço com o cheio
// riscado, ×1, selo), montante, logística da volta, motivo, reembolso concluído e o
// HISTÓRICO com o texto, as fotos e o vídeo do comprador; e "se der p eles responder
// a devolução por ali tbm" (tudo, inclusive o pacote): os botões e a janela.
// Os dados são os do caso medido em 18/11 (valores, datas, rastreio), com o texto
// e os links do comprador trocados por falsos.
process.env.TZ = 'America/Sao_Paulo'
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
  const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
  assert.deepEqual(errors, [], rel)
  return { descriptor, filename }
}
const cache = new Map()
const icones = new Proxy({}, { get: (_, nome) => ({ name: String(nome), render: () => Vue.h('i') }) })
function requerer(nome) {
  if (nome === 'lucide-vue-next') return icones
  const m = /^~\/components\/(.+\.vue)$/.exec(nome)
  if (!m) return require(nome)
  if (!cache.has(m[1])) {
    const mod = { exports: {} }
    const { descriptor } = sfc(`../components/${m[1]}`)
    if (descriptor.script) new Function('require', 'module', 'exports', transpile(descriptor.script.content))(requerer, mod, mod.exports)
    cache.set(m[1], mod.exports)
  }
  return cache.get(m[1])
}
const texto = (html) => html.replace(/<!--[\s\S]*?-->/g, '').replace(/<[^>]+>/g, ' ').replace(/[\s ]+/g, ' ').trim()
const esperar = () => new Promise((r) => setTimeout(r, 0))
let ids = 0

function montar(rel, props, { respostas = {}, filhos = {}, documento = {} } = {}) {
  const { descriptor, filename } = sfc(rel)
  const script = compileScript(descriptor, { id: path.basename(rel), inlineTemplate: false })
  const tpl = compileTemplate({ source: descriptor.template.content, filename, id: path.basename(rel), compilerOptions: { isTS: true, bindingMetadata: script.bindings } })
  assert.deepEqual(tpl.errors, [], `${rel}: template compila`)
  const chamadas = []
  const avisos = []
  const emitidos = []
  const montados = []
  const api = async (url, opts = {}) => {
    chamadas.push({ url, metodo: opts.method || 'GET', body: opts.body })
    const r = respostas[`${opts.method || 'GET'} ${url}`]
    if (r instanceof Error) throw r
    if (r !== undefined) return structuredClone(r)
    throw new Error(`rota inesperada: ${opts.method || 'GET'} ${url}`)
  }
  const g = {
    ref: Vue.ref, computed: Vue.computed, reactive: Vue.reactive, watch: Vue.watch, nextTick: Vue.nextTick,
    onMounted: (fn) => montados.push(fn), onBeforeUnmount: () => {}, useId: () => `id${++ids}`,
    useApi: () => ({ api }),
    useToasts: () => ({ success: (t) => avisos.push(['ok', t]), error: (t) => avisos.push(['erro', t]) }),
    window: { prompt: () => {} },
  }
  globalThis.document = documento
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
  try { bruto = comp.setup(reativo, { emit: (...a) => emitidos.push(a), expose: () => {}, attrs: {}, slots: {} }) } finally { console.warn = aviso }
  const filho = { ...comp, render: tmod.exports.render, setup: () => bruto }
  async function html() {
    const a = Vue.createSSRApp({ render: () => Vue.h(filho, reativo) })
    a.config.warnHandler = () => {}
    for (const [n, c] of Object.entries(filhos)) a.component(n, c)
    return await renderToString(a)
  }
  return { html, estado: Vue.proxyRefs(bruto), chamadas, avisos, emitidos, async montar() { for (const fn of montados) await fn(); await esperar() } }
}
const abrirFalso = { props: ['href', 'perfil', 'conversaId'], setup: (p, { slots }) => () => Vue.h('a', { href: p.href }, slots.default?.()) }

const BLOCO = '../components/AtendimentoDevolucaoTiktok.vue'
const JANELA = '../components/AtendimentoDevolucaoResponder.vue'
const B = requerer('~/components/AtendimentoDevolucaoTiktok.vue')
const J = requerer('~/components/AtendimentoDevolucaoResponder.vue')
const daqui19h = new Date(Date.now() + 19 * 3600 * 1000 + 60_000).toISOString()

const caso = (o = {}) => ({
  id: 'r1', plataforma: 'tiktok', plataforma_nome: 'TikTok', numero: '4042968144158361032', tipo: 'devolucao', tipo_rotulo: 'Devolução',
  status: 'BUYER_SHIPPED_ITEM', status_rotulo: 'Comprador enviou o produto', aberta: true, motivo: 'O item chegou danificado.', solucao: 'Devolução + reembolso',
  pedido_marketplace: '586887215793276360', prazo_em: daqui19h, aberta_em: '2026-11-07T00:50:45Z', encerrada_em: null,
  acao_pendente: 'Confirmar ou recusar o pacote recebido no TikTok', mediacao: false, reputacao_afetada: null, devolucao_status: null,
  conversa_id: 'c1', url_plataforma: 'https://seller-br.tiktok.com/x', do_pedido: true, rastreio_volta: null, detalhe_plataforma: true, ...o,
})
const ACOES = [
  { codigo: 'confirmar_pacote', rotulo: 'Confirmar o pacote recebido', recusa: false, exige_foto: false, efeito: 'O comprador já recebeu R$ 131,57 (reembolso rápido do TikTok). Confirmar fecha o caso.', bloqueio: null, prazo: daqui19h },
  { codigo: 'recusar_pacote', rotulo: 'Recusar o pacote recebido', recusa: true, exige_foto: true, efeito: 'A loja contesta o pacote.', bloqueio: 'O TikTok já fez o reembolso rápido: pela API ele não deixa recusar o pacote.', prazo: daqui19h },
]
const detalhe = (o = {}) => ({
  reclamacao_id: 'r1', ao_vivo: true, erro: null, erro_texto: null, lido_em: '2026-11-18T17:50:00+00:00',
  status: 'BUYER_SHIPPED_ITEM', status_rotulo: 'Comprador enviou o produto', tipo_caso: 'RETURN_AND_REFUND', solucao: 'Devolução + reembolso',
  motivo: 'O item chegou danificado. Exemplo: o item está amassado, arranhado ou quebrado.',
  reembolso: { total: 131.57, subtotal: 122.96, frete: 8.61, moeda: 'BRL' }, reembolso_rapido: true, reembolso_pago_em: '2026-11-07T16:55:32+00:00',
  volta: { rastreio: '632881002', transportadora: 'Jadlog BR 4PL', frete_loja: 32.56, moeda: 'BRL' },
  itens: [{ nome: 'Mala de Viagem Despacho 24 Polegadas', variacao: 'M3 Coral', sku: 'b033.24', foto: 'https://cdn.exemplo.test/mala.jpeg', preco: 122.96, preco_original: 413.41, moeda: 'BRL', quantidade: 1 }],
  acao_pendente: 'Confirmar ou recusar o pacote recebido no TikTok', prazo: daqui19h, arbitragem: null, aberto_em: '2026-11-07T00:50:45+00:00',
  acoes: ACOES, acoes_ligadas: false,
  historico: [
    { quando: '2026-11-07T00:50:44+00:00', papel: 'BUYER', quem: 'Comprador', evento: 'ORDER_RETURN', titulo: 'Solicitação de devolução enviada pelo comprador', motivo: 'O item chegou danificado.', texto: 'TEXTO FALSO DO COMPRADOR',
      fotos: [1, 2, 3, 4, 5].map((i) => ({ url: `https://cdn.exemplo.test/f${i}.jpeg` })), videos: [{ url: 'https://cdn.exemplo.test/v.mp4', capa: 'https://cdn.exemplo.test/capa.jpeg', duracao_s: 22 }] },
    { quando: '2026-11-07T00:50:45+00:00', papel: 'SELLER', quem: 'Loja', evento: 'SELLER_AGGREE_RETURN', titulo: 'Aprovado automaticamente pela TikTok Shop de acordo com a política aplicável', motivo: null, texto: null, fotos: [], videos: [] },
    { quando: '2026-11-07T16:55:13+00:00', papel: 'BUYER', quem: 'Comprador', evento: 'BUYER_SHIPPED', titulo: 'O comprador enviou o pacote de devolução ao vendedor', motivo: null, texto: null, fotos: [], videos: [] },
    { quando: '2026-11-07T16:55:32+00:00', papel: 'SYSTEM', quem: 'TikTok', evento: 'REFUND_SUCCESS', titulo: 'Reembolso concluído', motivo: null, texto: null, fotos: [], videos: [] },
  ],
  ...o,
})
const URL_DETALHE = 'GET /api/atendimento/conversas/c1/reclamacoes/r1/detalhe'

async function principal() {
  // ------------------------------------------------ ajudantes puros
  assert.equal(B.seloDaSolucao('RETURN_AND_REFUND'), 'Devolução e Reembolso')
  assert.equal(B.seloDaSolucao('REFUND'), 'Só reembolso')
  assert.equal(B.seloDaSolucao(null), null)
  assert.equal(B.duracaoVideo(21.8), '0:22')
  assert.equal(B.duracaoVideo(75), '1:15')
  assert.equal(B.duracaoVideo(null), '')
  const midias = B.midiasDoEvento({ quem: 'Comprador', fotos: [{ url: 'https://a/1' }, { url: 'http://inseguro' }, { url: 'javascript:alert(1)' }], videos: [{ url: 'https://a/v', capa: 'data:x', duracao_s: 22 }] })
  assert.deepEqual(midias.map((m) => [m.tipo, m.nome, m.capa, m.duracao]), [['imagem', 'foto 1 do comprador', 'https://a/1', ''], ['video', 'vídeo 1 do comprador', null, '0:22']], 'só https; fotos antes do vídeo')
  assert.equal(midias[1].rotulo, 'abrir vídeo 1 do comprador, 0:22')
  assert.match(B.porQueTravada({ bloqueio: null }, { acoesLigadas: true, podeResponder: false }), /Só quem responde/)
  assert.match(B.porQueTravada({ bloqueio: 'rápido' }, { acoesLigadas: false, podeResponder: true }), /rápido/, 'o bloqueio do TikTok vem antes do desligado')
  assert.match(B.porQueTravada({ bloqueio: null }, { acoesLigadas: false, podeResponder: true }), /desligadas/)
  assert.equal(B.porQueTravada({ bloqueio: null }, { acoesLigadas: true, podeResponder: true }), null)
  assert.equal(B.reembolsoJaPago({ reembolso_pago_em: '2026-11-07T16:55:32Z', status: 'BUYER_SHIPPED_ITEM' }), true, 'reembolso rápido: pago com o caso aberto')
  assert.equal(B.reembolsoJaPago({ reembolso_pago_em: null, status: 'RETURN_OR_REFUND_REQUEST_COMPLETE' }), true)
  assert.equal(B.reembolsoJaPago({ reembolso_pago_em: null, status: 'AWAITING_BUYER_SHIP' }), false)
  assert.equal(B.dataHoraComAno('2026-11-07T16:55:32+00:00'), '07/11/2026 13:55', 'a hora do Duoke (BRT)')
  assert.equal(J.proximoFoco(2, 3, false), 0, 'Tab no último volta ao primeiro')
  assert.equal(J.proximoFoco(0, 3, true), 2, 'Shift+Tab no primeiro vai ao último')
  assert.equal(J.proximoFoco(-1, 3, false), 0)
  assert.equal(J.bytesUtf8('ação'), 6)
  const recusa = { recusa: true, exige_foto: true }
  const foto = { size: 1000, type: 'image/jpeg' }
  assert.deepEqual(J.faltaParaMandar({ recusa: false, exige_foto: false }, { entendi: true, motivo: '', comentario: '', fotos: [] }), [], 'aprovar só pede o "entendi"')
  assert.equal(J.faltaParaMandar({ recusa: false, exige_foto: false }, { entendi: false, motivo: '', comentario: '', fotos: [] }).length, 1)
  assert.deepEqual(J.faltaParaMandar(recusa, { entendi: true, motivo: 'r1', comentario: 'Produto voltou riscado e sem a rodinha.', fotos: [foto] }), [])
  const falta = J.faltaParaMandar(recusa, { entendi: false, motivo: '', comentario: 'oi', fotos: [] })
  assert.equal(falta.length, 4, 'motivo, comentário, foto e "entendi"')
  assert.ok(J.faltaParaMandar(recusa, { entendi: true, motivo: 'r', comentario: 'ã'.repeat(251), fotos: [foto] }).some((f) => /limite/.test(f)), '500 bytes, não caracteres')
  assert.ok(J.faltaParaMandar(recusa, { entendi: true, motivo: 'r', comentario: 'riscado demais', fotos: [{ size: 10, type: 'image/gif' }] }).some((f) => /JPG/.test(f)))

  // ------------------------------------------------ o bloco, com o caso lido na hora
  {
    const m = montar(BLOCO, { r: caso(), conversaId: 'c1', perfil: null, podeResponder: true }, { respostas: { [URL_DETALHE]: detalhe() }, filhos: { AtendimentoAbrirPlataforma: abrirFalso } })
    await m.montar()
    const h = await m.html()
    const t = texto(h)
    assert.deepEqual(m.chamadas.map((c) => `${c.metodo} ${c.url}`), [URL_DETALHE], 'uma leitura ao abrir')
    assert.match(t, /Devolução Comprador enviou o produto Nº do reembolso 4042968144158361032/)
    assert.match(t, /Reembolso concluído em 07\/11\/2026 13:55 — reembolso rápido do TikTok/, 'o reembolso já saiu, mesmo com o caso aberto')
    assert.match(t, /O caso segue aberto até a loja responder o pacote \(19 h( \d+ min)?\)/)
    assert.match(t, /Variação: M3 Coral SKU: b033\.24 × 1 R\$ 122,96 preço cheio: R\$ 413,41 Devolução e Reembolso/, 'a linha do produto do Duoke')
    assert.match(h, /<s[^>]*>.*413,41<\/s>/, 'o preço cheio riscado')
    assert.match(t, /Montante do reembolso -R\$ 131,57/)
    assert.match(h, /title="produto R\$\s122,96 \+ frete R\$\s8,61"/)
    assert.match(t, /Logística da volta 632881002 Jadlog BR 4PL/)
    assert.match(t, /Frete da volta \(loja paga\) R\$ 32,56/)
    assert.match(t, /Motivo O item chegou danificado\. Exemplo/)
    assert.match(t, /Pede Devolução \+ reembolso Restante 19 h( \d+ min)? até \d\d\/\d\d \d\d:\d\d/)
    // O histórico, na ordem, com as datas do Duoke.
    assert.match(t, /Histórico de reembolso 4 eventos/)
    const ordem = ['Solicitação de devolução enviada pelo comprador', 'Aprovado automaticamente', 'O comprador enviou o pacote', 'Reembolso concluído 07/11/2026 13:55'].map((x) => t.lastIndexOf(x))
    assert.ok(ordem.every((p) => p >= 0), 'os 4 eventos')
    assert.deepEqual(ordem, ordem.slice().sort((a, b) => a - b), 'do mais antigo ao mais novo')
    assert.match(t, /Motivo do reembolso: O item chegou danificado\. Outras informações: TEXTO FALSO DO COMPRADOR/)
    assert.match(t, /06\/11\/2026 21:50 · Comprador/)
    assert.equal((h.match(/data-midia="imagem"/g) || []).length, 5, 'as 5 fotos')
    assert.equal((h.match(/data-midia="video"/g) || []).length, 1, 'o vídeo')
    assert.match(h, /aria-label="abrir vídeo 1 do comprador, 0:22"/)
    assert.match(h, /<time datetime="2026-11-07T16:55:32\+00:00">/)
    // As respostas: desligadas → os dois travados, no Tab, com o porquê ligado.
    for (const codigo of ['confirmar_pacote', 'recusar_pacote']) {
      const botao = h.match(new RegExp(`<button[^>]*data-acao="${codigo}"[^>]*>`))[0]
      assert.match(botao, /aria-disabled="true"/, `${codigo} travado`)
      assert.doesNotMatch(botao, / disabled[ =>]/, `${codigo} continua no Tab`)
      const porque = /aria-describedby="([^"]+)"/.exec(botao)[1]
      assert.match(h, new RegExp(`id="${porque}"`), `${codigo}: o porquê existe`)
    }
    assert.match(t, /Confirmar o pacote recebido: As respostas de devolução pelo DaVinci ainda estão desligadas/)
    assert.match(t, /Recusar o pacote recebido: O TikTok já fez o reembolso rápido/)
    // Clique no travado não abre nada.
    m.estado.abrirResposta(ACOES[0])
    assert.equal(m.estado.respondendo, null)
    for (const cru of ['BUYER_SHIPPED_ITEM', 'RETURN_AND_REFUND', 'SELLER_RESPOND']) assert.ok(!t.includes(cru), `sem código cru: ${cru}`)
  }

  // ------------------------------------------------ ligado: confirmar abre a janela; recusar segue travado
  {
    const m = montar(BLOCO, { r: caso(), conversaId: 'c1', podeResponder: true }, { respostas: { [URL_DETALHE]: detalhe({ acoes_ligadas: true }) }, filhos: { AtendimentoAbrirPlataforma: abrirFalso } })
    await m.montar()
    const h = await m.html()
    assert.doesNotMatch(h.match(/<button[^>]*data-acao="confirmar_pacote"[^>]*>/)[0], /aria-disabled="true"/)
    assert.match(h.match(/<button[^>]*data-acao="recusar_pacote"[^>]*>/)[0], /aria-disabled="true"/, 'o reembolso rápido trava a recusa')
    m.estado.abrirResposta(ACOES[1])
    assert.equal(m.estado.respondendo, null)
    m.estado.abrirResposta(ACOES[0])
    assert.equal(m.estado.respondendo.codigo, 'confirmar_pacote')
    // A resposta saiu: o bloco troca, avisa e a conversa relê.
    m.estado.aoResponder(detalhe({ acoes: [], status: 'RETURN_OR_REFUND_REQUEST_COMPLETE', status_rotulo: 'Concluída — reembolso pago' }))
    assert.equal(m.estado.respondendo, null)
    assert.deepEqual(m.avisos, [['ok', 'Confirmar o pacote recebido: enviado ao TikTok']])
    assert.deepEqual(m.emitidos.map((e) => e[0]), ['respondido'])
    assert.match(texto(await m.html()), /Concluída — reembolso pago/)
    // Quem só lê: travado mesmo ligado.
    const so = montar(BLOCO, { r: caso(), conversaId: 'c1', podeResponder: false }, { respostas: { [URL_DETALHE]: detalhe({ acoes_ligadas: true }) }, filhos: { AtendimentoAbrirPlataforma: abrirFalso } })
    await so.montar()
    assert.match(texto(await so.html()), /Confirmar o pacote recebido: Só quem responde/)
  }

  // ------------------------------------------------ o TikTok não respondeu: fica o que o cartão tem
  {
    const m = montar(BLOCO, { r: caso({ rastreio_volta: '632881002' }), conversaId: 'c1' }, {
      respostas: { [URL_DETALHE]: { reclamacao_id: 'r1', ao_vivo: false, erro: 'tiktok_indisponivel', erro_texto: 'Não deu para ler o histórico no TikTok agora. Tente de novo.', acoes: [], acoes_ligadas: false, historico: [], itens: [] } },
      filhos: { AtendimentoAbrirPlataforma: abrirFalso },
    })
    await m.montar()
    const t = texto(await m.html())
    assert.match(t, /Comprador enviou o produto/, 'o status do cartão')
    assert.match(t, /Logística da volta 632881002/, 'o rastreio do cartão')
    assert.match(t, /Não deu para ler o histórico no TikTok agora\. Tente de novo\. tentar de novo/)
    assert.ok(!t.includes('Histórico de reembolso'))
  }

  // ------------------------------------------------ a janela: recusar com motivo, comentário e foto
  {
    const base = '/api/atendimento/conversas/c1/reclamacoes/r1'
    const respostas = {
      [`GET ${base}/motivos-recusa`]: { itens: [{ codigo: 'reverse_reject_return_parcel_reason_1', texto: 'O produto voltou danificado' }] },
      [`POST ${base}/responder`]: detalhe({ acoes: [] }),
    }
    const focados = []
    const el = (n) => ({ n, focus: () => focados.push(n) })
    const [a, b, c] = [el('a'), el('b'), el('c')]
    const documento = { activeElement: c }
    const m = montar(JANELA, { acao: { ...ACOES[1], bloqueio: null }, url: base, numero: '4042968144158361032' }, { respostas, documento })
    await m.montar()
    assert.deepEqual(m.estado.motivos.map((x) => x.codigo), ['reverse_reject_return_parcel_reason_1'], 'os motivos vêm do TikTok ao abrir')
    let h = await m.html()
    // Rótulos ligados e o título/efeito do diálogo.
    const lb = /aria-labelledby="([^"]+)"/.exec(h)[1]
    assert.match(h, new RegExp(`<h2 id="${lb}"`))
    const db = /aria-describedby="([^"]+)"/.exec(h)[1]
    assert.match(h, new RegExp(`id="${db}"[^>]*data-efeito`))
    for (const campo of ['data-motivo', 'data-comentario']) {
      const id = new RegExp(`id="([^"]+)"[^>]*${campo}`).exec(h)[1]
      assert.match(h, new RegExp(`<label for="${id}"`), `${campo} com rótulo`)
    }
    assert.match(h, /Adicionar fotos/)
    // O que falta aparece em texto e o botão diz por quê.
    assert.match(texto(h), /Escolha o motivo da recusa\. .*Mande ao menos uma foto/)
    assert.match(h.match(/<button[^>]*data-mandar[^>]*>/)[0], /aria-disabled="true"/)
    await m.estado.mandar()
    assert.equal(m.chamadas.filter((x) => x.metodo === 'POST').length, 0, 'faltando, nada sai')
    // O Tab preso: do último volta ao primeiro.
    m.estado.caixa = { querySelectorAll: () => [a, b, c], focus: () => {} }
    let evitado = false
    m.estado.prenderFoco({ shiftKey: false, preventDefault: () => { evitado = true } })
    assert.deepEqual(focados, ['a'])
    assert.ok(evitado)
    // Preenchido: sai o POST com o que a pessoa pôs.
    m.estado.motivo = 'reverse_reject_return_parcel_reason_1'
    m.estado.comentario = '  Produto voltou   riscado e sem a rodinha. '
    m.estado.fotos.push(new File([new Uint8Array(10)], 'dano.jpg', { type: 'image/jpeg' }))
    m.estado.entendi = true
    h = await m.html()
    assert.doesNotMatch(texto(h), /Escolha o motivo/)
    await m.estado.mandar()
    const post = m.chamadas.find((x) => x.metodo === 'POST')
    assert.equal(post.url, `${base}/responder`)
    const corpo = Object.fromEntries([...post.body.entries()].map(([k, v]) => [k, typeof v === 'string' ? v : v.name]))
    assert.deepEqual(corpo, { acao: 'recusar_pacote', motivo: 'reverse_reject_return_parcel_reason_1', comentario: 'Produto voltou riscado e sem a rodinha.', fotos: 'dano.jpg' })
    assert.deepEqual(m.emitidos.map((e) => e[0]), ['respondido'])

    // O TikTok recusou: a frase do servidor, e a janela fica aberta.
    const erro = Object.assign(new Error('409'), { data: { detail: { code: 'tiktok_25011025', detail: 'O TikTok já fez o reembolso rápido e não deixa recusar o pacote pela API.' } } })
    const r = montar(JANELA, { acao: ACOES[0], url: base, numero: '4042968144158361032' }, { respostas: { [`POST ${base}/responder`]: erro } })
    r.estado.entendi = true
    await r.estado.mandar()
    assert.equal(r.estado.erro, 'O TikTok já fez o reembolso rápido e não deixa recusar o pacote pela API.')
    assert.deepEqual(r.emitidos, [])
    assert.match(await r.html(), /role="alert"[^>]*data-erro/)
    // Aprovar não lê motivos nem manda comentário.
    assert.deepEqual(r.chamadas.map((x) => x.metodo), ['POST'])
    assert.deepEqual([...r.chamadas[0].body.keys()], ['acao'])
  }
  console.log('ok: atendimento-devolucao-tiktok (bloco completo, histórico, respostas e janela)')
}
principal().catch((e) => { console.error(e); process.exit(1) })
