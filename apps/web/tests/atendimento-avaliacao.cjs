// node tests/atendimento-avaliacao.cjs — AVALIAÇÕES DE VENDA na caixa /atendimento (RF8, 02/10/2026).
// "Traz as avaliações das plataformas que der já" (Shopee e Mercado Livre).
// Aqui, a tela:
//  - o cartão AtendimentoAvaliacao (estrelas, 1–3 em destaque, texto, fotos só
//    https, resposta da loja, prazo INTERNO, "Abrir na plataforma") e a caixa
//    "Responder em público": desabilitada com o porquê enquanto o envio está
//    desligado, confirmação antes de publicar, POST /avaliacoes/{id}/responder;
//    no ML o motivo no lugar da caixa e "Marcar como tratada";
//  - o contrato com o backend (rotas, campos do AvaliacaoLojaOut, as réguas de
//    constantes.py/avaliacoes.py: nota baixa, quem responde, limite e prazo);
//  - a conversa (cartão abaixo do da reclamação, faixa e aviso de resposta
//    pública na conversa `avaliacao`), a lista (filtro com contagem e estrelas
//    no selo) e o painel do pedido (seção Avaliação, com o contexto de rede).
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')

const transpile = (source) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText

function sfc(rel) {
  const filename = path.resolve(__dirname, rel)
  const fonte = fs.readFileSync(filename, 'utf8')
  const { descriptor, errors } = parse(fonte, { filename })
  assert.deepEqual(errors, [], rel)
  const compiled = compileTemplate({ source: descriptor.template.content, filename, id: path.basename(rel), compilerOptions: { isTS: true } })
  assert.deepEqual(compiled.errors, [], `${rel}: template compila`)
  compileScript(descriptor, { id: path.basename(rel) })
  return { descriptor, fonte }
}
// O <script> comum (não o setup) é o módulo dos ajudantes exportados.
function exportsDe(descriptor) {
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(descriptor.script.content))((n) => require(n), mod, mod.exports)
  return mod.exports
}
function trecho(src, ini, fim) {
  const a = src.indexOf(ini)
  const b = src.indexOf(fim, a + 1)
  assert.ok(a >= 0 && b > a, `trecho ${ini} … ${fim}`)
  return transpile(src.slice(a, b))
}
const api = (rel) => fs.readFileSync(path.resolve(__dirname, '../../api/app', rel), 'utf8')

const aval = sfc('../components/AtendimentoAvaliacao.vue')
const A = exportsDe(aval.descriptor)
const P = exportsDe(sfc('../components/AtendimentoPlataforma.vue').descriptor)
const conversa = sfc('../components/AtendimentoConversa.vue')
const lista = sfc('../components/AtendimentoLista.vue')
const L = exportsDe(lista.descriptor)
const pedido = sfc('../components/AtendimentoPedido.vue')

// ------------------------------------------------ o contrato com o backend
const constantes = api('services/atendimento/constantes.py')
const servico = api('services/atendimento/avaliacoes.py')
const rotas = api('routers/atendimento_avaliacoes.py')
const schemas = api('schemas/atendimento.py')
{
  assert.equal(Number((constantes.match(/^NOTA_BAIXA_AVALIACAO = (\d+)$/m) || [])[1]), A.NOTA_BAIXA, 'nota baixa = a do backend')
  const respondem = [...((constantes.match(/^PLATAFORMAS_RESPONDEM_AVALIACAO = frozenset\(\{([^}]*)\}\)/m) || [])[1] || '').matchAll(/"([a-z_]+)"/g)].map((m) => m[1])
  assert.deepEqual(A.PLATAFORMAS_RESPONDEM.slice().sort(), respondem.sort(), 'quem responde pela API = o backend')
  assert.match(constantes, /^CANAL_AVALIACAO = "avaliacao"$/m)
  for (const [plat, n] of Object.entries(A.LIMITE_RESPOSTA)) {
    const re = new RegExp(`\\("${plat}", CANAL_AVALIACAO\\): (\\d+),`)
    assert.equal(Number((constantes.match(re) || [])[1]), n, `limite da resposta ${plat}`)
  }
  const horas = Number((servico.match(/^PRAZO_RESPOSTA = timedelta\(hours=(\d+)\)$/m) || [])[1])
  assert.equal(A.PRAZO_INTERNO_MS, horas * 3600 * 1000, 'prazo interno = PRAZO_RESPOSTA')
  const aviso = (servico.match(/^AVISO_RESPOSTA_PUBLICA = \(\s*"([^"]+)"\s*\)/m) || [])[1]
  assert.equal(A.AVISO_RESPOSTA_PUBLICA, aviso, 'o aviso de rede é o mesmo do backend')

  // As rotas que a tela chama.
  assert.match(rotas, /prefix="\/api\/atendimento"/)
  assert.match(rotas, /@router\.get\("\/conversas\/\{conversa_id\}\/avaliacoes"/)
  assert.match(rotas, /@router\.post\("\/avaliacoes\/\{avaliacao_id\}\/responder"/)
  assert.match(rotas, /@router\.post\("\/avaliacoes\/\{avaliacao_id\}\/tratada"/)
  for (const url of ['/avaliacoes`', '/avaliacoes/${encodeURIComponent(a.id)}/responder', '/avaliacoes/${encodeURIComponent(a.id)}/tratada']) {
    assert.ok(aval.fonte.includes(url), `a tela chama ${url}`)
  }

  // Os campos: os do AvaliacaoLojaOut são os da tela (nos dois sentidos).
  const campos = (classe, fonte) => {
    const m = fonte.match(new RegExp(`^class ${classe}\\(BaseModel\\):\\n([\\s\\S]*?)(?=\\n\\n\\n|\\n(?:class|def|async def|@) )`, 'm'))
    assert.ok(m, classe)
    return [...m[1].matchAll(/^ {4}([a-z_]+): /gm)].map((x) => x[1])
  }
  const tipo = (nome) => {
    const m = aval.fonte.match(new RegExp(`export interface ${nome} \\{\\n([\\s\\S]*?)\\n\\}`))
    assert.ok(m, nome)
    return [...m[1].matchAll(/^ {2}([a-z_]+)\??: /gm)].map((x) => x[1])
  }
  assert.deepEqual(tipo('AvaliacaoLoja').sort(), campos('AvaliacaoLojaOut', rotas).sort(), 'AvaliacaoLoja = AvaliacaoLojaOut')
  assert.deepEqual(tipo('AvaliacoesResposta').sort(), campos('AvaliacoesOut', rotas).sort(), 'AvaliacoesResposta = AvaliacoesOut')
  assert.deepEqual(campos('ResponderAvaliacaoIn', rotas), ['texto', 'ultima_vista_id', 'confirmar'])
  assert.deepEqual(campos('TratadaIn', rotas), ['motivo'])
  assert.ok(campos('ConversaResumoOut', schemas).includes('avaliacao_estrelas'))
  // O resumo do contexto (painel sem o cartão) = o tipo da tela.
  const ctx = trecho(servico, 'async def resumo_para_contexto', 'return saida')
  const chavesCtx = [...ctx.matchAll(/"([a-z_]+)":/g)].map((m) => m[1])
  const tipoCtx = (fs.readFileSync(path.resolve(__dirname, '../components/AtendimentoPlataforma.vue'), 'utf8').match(/export type AvaliacaoContexto = \{\n([\s\S]*?)\n\}/) || [])[1]
  assert.ok(tipoCtx, 'AvaliacaoContexto')
  assert.deepEqual([...tipoCtx.matchAll(/^ {2}([a-z_]+): /gm)].map((m) => m[1]).sort(), chavesCtx.sort(), 'contexto.avaliacoes = resumo_para_contexto')
}

// ------------------------------------------------ estrelas, cor e destaque
assert.equal(A.estrelasDe(2), '★★☆☆☆')
assert.equal(A.estrelasDe(5), '★★★★★')
for (const fora of [0, 6, null, undefined, 3.5]) assert.equal(A.estrelasDe(fora), '')
assert.deepEqual([1, 2, 3, 4, 5].map(A.ehNotaBaixa), [true, true, true, false, false])
assert.match(A.corDaNota(3), /red/, '1–3 em destaque')
assert.match(A.corDaNota(4), /yellow/)
assert.match(A.bordaDaAvaliacao({ estrelas: 2, pendente: true }), /red/)
assert.match(A.bordaDaAvaliacao({ estrelas: 2, pendente: false }), /yellow/, 'a resolvida não grita')
assert.match(A.bordaDaAvaliacao({ estrelas: 5, pendente: true }), /yellow/)
assert.equal(A.tempo(45), '45 min')
assert.equal(A.tempo(200), '3 h 20 min')
assert.equal(A.tempo(60 * 52), '2 d 4 h')

// ------------------------------------------------ o prazo INTERNO (24 h da pendência)
{
  const agora = Date.parse('2026-10-02T12:00:00Z')
  const p = (desde, extra = {}) => A.prazoAvaliacao({ pendente: true, pendente_desde: desde, respondida: false, tratada_em: null, ...extra }, agora)
  assert.equal(p('2026-10-02T10:00:00Z').texto, 'faltam 22 h')
  assert.equal(p('2026-10-02T10:00:00Z').nivel, 'ok')
  assert.match(p('2026-10-02T10:00:00Z').titulo, /prazo interno do DaVinci \(a plataforma não dá prazo/)
  assert.equal(p('2026-10-01T13:00:00Z').nivel, 'vencendo')
  assert.equal(p('2026-10-01T13:00:00Z').texto, 'faltam 1 h')
  assert.equal(p('2026-10-01T10:00:00Z').nivel, 'vencida')
  assert.equal(p('2026-10-01T10:00:00Z').texto, 'prazo interno vencido há 2 h')
  assert.equal(p(null), null)
  assert.equal(p('lixo'), null)
  assert.equal(p('2026-10-02T10:00:00Z', { pendente: false }), null, 'só a pendente tem prazo')
  assert.equal(p('2026-10-02T10:00:00Z', { respondida: true }), null)
  assert.equal(p('2026-10-02T10:00:00Z', { tratada_em: '2026-10-02T11:00:00Z' }), null)
}

// ------------------------------------------------ situação (o selo do cartão e do painel)
{
  const s = (x) => A.situacaoAvaliacao({ plataforma: 'shopee', estrelas: 5, respondida: false, resposta_oculta: null, tratada_em: null, pendente: false, ...x })
  assert.equal(s({ respondida: true }).rotulo, 'Respondida')
  assert.equal(s({ respondida: true, resposta_oculta: true }).rotulo, 'Respondida (resposta oculta)')
  assert.equal(s({ tratada_em: '2026-10-02T11:00:00Z' }).rotulo, 'Tratada')
  assert.equal(s({ tratada_em: '' }).rotulo, 'Tratada', 'do contexto: tratada sem a hora')
  assert.equal(s({ pendente: true, estrelas: 2 }).rotulo, 'Sem resposta')
  assert.match(s({ pendente: true, estrelas: 2 }).cls, /red/)
  assert.match(s({ pendente: true, estrelas: 5 }).cls, /amber/)
  assert.equal(s({}).rotulo, 'Sem resposta')
  assert.match(s({}).dica, /carência/)
  assert.equal(s({ plataforma: 'ml' }).rotulo, 'Só informação', 'ML 4–5: não vira pendência')
}

// ------------------------------------------------ os grupos do cartão
const base = {
  id: 'x', plataforma: 'shopee', plataforma_nome: 'Shopee', comentario_id: 'c', pedido: '251001ABC', item_id: '9', anuncio_titulo: 'Mala ABS',
  estrelas: 5, nota_baixa: false, titulo: null, texto: 'Chegou certinho', midia: [], criado_em: '2026-10-01T10:00:00Z', editada: false,
  respondida: false, resposta_loja: null, resposta_em: null, resposta_oculta: null, pode_responder: false,
  motivo_sem_resposta: 'Resposta pública (aparece no anúncio): o envio pelo DaVinci está desligado (ATENDIMENTO_ENVIO_ATIVO).',
  pendente: false, pendente_desde: null, tratada_em: null, tratada_por_nome: null, conversa_id: null, do_pedido: true,
  // Sem o order_id interno a Shopee abre a lista de pedidos buscando o order_sn
  // (`/portal/sale/order/<order_sn>` não abre o pedido — 02/10/2026).
  url_plataforma: 'https://seller.shopee.com.br/portal/sale/order?search=251001ABC',
}
function av(x) {
  return { ...base, ...x }
}
{
  const g = A.separarAvaliacoes([
    av({ id: 'resp', respondida: true, resposta_loja: 'Obrigado!', criado_em: '2026-09-30T00:00:00Z' }),
    av({ id: 'carencia', estrelas: 5 }),
    av({ id: 'pend5', pendente: true, estrelas: 5 }),
    av({ id: 'ant', do_pedido: false, pedido: '250901XYZ', respondida: true }),
    av({ id: 'pend2', pendente: true, estrelas: 2 }),
    av({ id: 'ml5', plataforma: 'ml', plataforma_nome: 'Mercado Livre', estrelas: 5 }),
    av({ id: 'tratada', plataforma: 'ml', estrelas: 1, tratada_em: '2026-10-01T12:00:00Z' }),
  ])
  assert.deepEqual(g.abertas.map((a) => a.id), ['pend2', 'pend5', 'carencia'], 'pendentes (pior nota antes), depois a sem resposta da Shopee')
  assert.deepEqual(g.resolvidas.map((a) => a.id).sort(), ['ml5', 'resp', 'tratada'])
  assert.deepEqual(g.anteriores.map((a) => a.id), ['ant'])
}

// ------------------------------------------------ fotos: só https da plataforma
assert.deepEqual(A.midiaSegura([
  { tipo: 'imagem', url: 'https://down-br.img.susercontent.com/file/a.jpg' },
  { tipo: 'video', url: 'https://cvf.shopee.com.br/v.mp4', miniatura: 'javascript:alert(1)' },
  { tipo: 'imagem', url: 'http://inseguro/x.jpg' },
  { tipo: 'imagem', url: 'javascript:alert(1)' },
  { tipo: 'imagem', url: 'data:image/png;base64,AAAA' },
  { tipo: 'imagem', url: 'https://x/"onerror="y' },
  null,
  'https://solta.jpg',
]), [
  { tipo: 'imagem', url: 'https://down-br.img.susercontent.com/file/a.jpg', miniatura: null },
  { tipo: 'video', url: 'https://cvf.shopee.com.br/v.mp4', miniatura: null },
])
assert.deepEqual(A.midiaSegura(null), [])
// Sem v-html; miniatura sem referrer; links em aba nova sem acesso à janela.
assert.doesNotMatch(aval.fonte, /v-html/)
for (const img of aval.fonte.match(/<img[\s\S]*?\/>/g)) assert.match(img, /referrerpolicy="no-referrer"/)
for (const a of aval.fonte.match(/<a\s[\s\S]*?>/g)) assert.match(a, /target="_blank"\s+rel="noopener noreferrer"/)

// ------------------------------------------------ a caixa "Responder em público"
{
  const c = (x, opts = {}) => A.caixaDaAvaliacao(av(x), { canEdit: true, conversaId: 'conv-chat', canalConversa: 'chat', ...opts })
  // Envio desligado (hoje): caixa cinza, com o porquê do backend.
  assert.deepEqual(c({ pendente: true }), { modo: 'desligada', motivo: base.motivo_sem_resposta })
  assert.deepEqual(c({ pode_responder: true, motivo_sem_resposta: null }), { modo: 'caixa', motivo: '' })
  assert.equal(c({ pode_responder: true, motivo_sem_resposta: null }, { canEdit: false }).modo, 'desligada')
  assert.match(c({ pode_responder: true, motivo_sem_resposta: null }, { canEdit: false }).motivo, /permissão/)
  assert.equal(c({ respondida: true, resposta_loja: 'oi' }).modo, 'nenhuma')
  // ML: o motivo no lugar da caixa.
  const ml = c({ plataforma: 'ml', motivo_sem_resposta: 'O Mercado Livre não permite responder a opinião pela API: use “Marcar como tratada”.' })
  assert.equal(ml.modo, 'motivo')
  assert.match(ml.motivo, /Mercado Livre não permite/)
  assert.equal(c({ plataforma: 'ml', motivo_sem_resposta: null }).motivo, 'Esta plataforma não deixa responder a avaliação pela API.')
  // Na própria conversa da avaliação, a caixa de baixo responde.
  const aqui = c({ conversa_id: 'conv-aval', pode_responder: true, motivo_sem_resposta: null }, { conversaId: 'conv-aval', canalConversa: 'avaliacao' })
  assert.equal(aqui.modo, 'abaixo')
  assert.match(aqui.motivo, /caixa de resposta abaixo — a resposta é pública/)
  assert.equal(c({ conversa_id: 'conv-aval' }, { conversaId: 'conv-aval', canalConversa: 'avaliacao' }).motivo, base.motivo_sem_resposta)
  // A mesma avaliação vista do chat do pedido: o cartão tem a caixa.
  assert.equal(c({ conversa_id: 'conv-aval', pode_responder: true, motivo_sem_resposta: null }).modo, 'caixa')

  assert.equal(A.podeMarcarTratada(av({ pendente: true }), true), true)
  assert.equal(A.podeMarcarTratada(av({ pendente: true }), false), false, 'sem permissão de editar')
  assert.equal(A.podeMarcarTratada(av({ pendente: false }), true), false, 'só a pendente')
  assert.equal(A.podeMarcarTratada(av({ pendente: true, tratada_em: '2026-10-01T00:00:00Z' }), true), false)
  assert.equal(A.podeMarcarTratada(av({ pendente: true, respondida: true }), true), false)
}

// ------------------------------------------------ o painel sem o cartão: o contexto
{
  const ctx = { id: 'k1', plataforma: 'Mercado Livre', estrelas: 2, pedido: '2000012345', do_pedido: true, criado_em: '2026-10-01T10:00:00Z', respondida: false, pendente: true, tratada: false, pode_responder: false }
  const a = A.doContexto(ctx)
  assert.equal(a.plataforma, 'ml')
  assert.equal(a.plataforma_nome, 'Mercado Livre')
  assert.equal(a.nota_baixa, true)
  assert.equal(a.texto, null, 'o contexto não traz texto')
  assert.deepEqual(a.midia, [])
  assert.equal(A.doContexto({ ...ctx, plataforma: 'Shopee' }).plataforma, 'shopee')
  assert.equal(A.situacaoAvaliacao(A.doContexto({ ...ctx, pendente: false, tratada: true })).rotulo, 'Tratada')
}

// ------------------------------------------------ o cartão (script setup) com API falsa
async function testarCartao() {
  const src = aval.descriptor.scriptSetup.content.replace(/^import[\s\S]*?from\s+'[^']+'\s*$/gm, '')
  const js = transpile(src) + '\nreturn { dados, erro, grupos, doPedido, linhas, abertos, alternar, verAnteriores, carregar, caixa, textos, enviandoId, podeEnviar, tamanhoDaResposta, responder, tratarAberto, motivoTratada, abrirTratar, marcarTratada }'
  const tique = () => new Promise((r) => setTimeout(r, 0))
  function montar({ props, rotas, confirmar = () => true }) {
    const emitidos = []
    const chamadas = []
    const avisos = []
    const perguntas = []
    const api = async (url, opts) => {
      chamadas.push({ url, opts })
      const metodo = opts?.method || 'GET'
      const r = rotas[`${metodo} ${url}`]
      assert.ok(r, `rota inesperada: ${metodo} ${url}`)
      return typeof r === 'function' ? r(opts) : r
    }
    const toast = (tipo) => (titulo, detalhe) => avisos.push([tipo, titulo, detalhe])
    const P2 = { ...P }
    const vm = new Function(
      'ref', 'computed', 'watch', 'reactive', 'withDefaults', 'defineProps', 'defineEmits', 'defineExpose',
      'useApi', 'useToasts', 'useRelogio', 'confirm',
      'erroDaApi', 'erroEnvioLegivel', 'statusDoErro', 'tamanhoDoEnvio',
      'separarAvaliacoes', 'caixaDaAvaliacao', 'podeMarcarTratada', 'AVISO_RESPOSTA_PUBLICA', 'LIMITE_RESPOSTA',
      'perguntaRespostaPublica',
      js,
    )(
      Vue.ref, Vue.computed, Vue.watch, Vue.reactive, (p, d) => Vue.reactive({ ...d, ...p }), () => props,
      () => (n, v) => emitidos.push([n, v]), () => {},
      () => ({ api }),
      () => ({ success: toast('ok'), error: toast('erro'), warning: toast('aviso'), info: toast('info') }),
      () => Vue.ref(Date.parse('2026-10-02T12:00:00Z')),
      (t) => { perguntas.push(t); return confirmar(t) },
      P2.erroDaApi, P2.erroEnvioLegivel, P2.statusDoErro, P2.tamanhoDoEnvio,
      A.separarAvaliacoes, A.caixaDaAvaliacao, A.podeMarcarTratada, A.AVISO_RESPOSTA_PUBLICA, A.LIMITE_RESPOSTA,
      A.perguntaRespostaPublica,
    )
    return { vm, emitidos, chamadas, avisos, perguntas }
  }
  const GET = 'GET /api/atendimento/conversas/conv-chat/avaliacoes'
  const resposta = (itens, extra = {}) => ({ itens, pendentes: itens.filter((a) => a.pendente).length, pior_pendente: null, envio_ativo: false, aviso: A.AVISO_RESPOSTA_PUBLICA, ...extra })

  // 1. Envio desligado (hoje): cartão aberto, caixa desabilitada, nada sai.
  {
    const pend = av({ id: 'a1', pendente: true, estrelas: 2, nota_baixa: true, pendente_desde: '2026-10-02T10:00:00Z' })
    const m = montar({ props: { conversaId: 'conv-chat', canalConversa: 'chat', canEdit: true }, rotas: { [GET]: resposta([pend, av({ id: 'old', do_pedido: false, respondida: true })]) } })
    await tique()
    assert.deepEqual(m.chamadas.map((c) => c.url), ['/api/atendimento/conversas/conv-chat/avaliacoes'])
    assert.equal(m.emitidos[0][0], 'carregado')
    assert.equal(m.vm.doPedido.value, 1)
    assert.deepEqual(m.vm.linhas.value.map((l) => l.k), ['cartao', 'grupo'], 'as anteriores recolhidas')
    m.vm.verAnteriores.value = true
    assert.deepEqual(m.vm.linhas.value.map((l) => l.k), ['cartao', 'grupo', 'linha'])
    m.vm.alternar('old')
    assert.deepEqual(m.vm.linhas.value.map((l) => l.k), ['cartao', 'grupo', 'cartao'], 'clique abre a anterior inteira')
    assert.equal(m.vm.caixa(pend).modo, 'desligada')
    m.vm.textos.a1 = 'Sentimos muito pelo ocorrido.'
    assert.equal(m.vm.podeEnviar(pend), false)
    await m.vm.responder(pend)
    assert.equal(m.chamadas.length, 1, 'com o envio desligado nada é chamado')
    assert.deepEqual(m.perguntas, [])
  }

  // 2. Envio ligado: confirma (resposta PÚBLICA), POST, a avaliação volta respondida.
  {
    const pend = av({ id: 'a2', pendente: true, estrelas: 2, nota_baixa: true, pode_responder: true, motivo_sem_resposta: null, conversa_id: 'conv-aval' })
    const respondida = { ...pend, respondida: true, resposta_loja: 'Sentimos muito!', resposta_em: '2026-10-02T12:00:00Z', pendente: false, pendente_desde: null, pode_responder: false, motivo_sem_resposta: 'Avaliação já respondida.', do_pedido: true }
    let corpo = null
    const m = montar({
      props: { conversaId: 'conv-chat', canalConversa: 'chat', canEdit: true },
      rotas: {
        [GET]: resposta([pend], { envio_ativo: true }),
        'POST /api/atendimento/avaliacoes/a2/responder': (opts) => { corpo = opts.body; return { mensagem: { id: 'm1', status: 'enviada' }, avaliacao: respondida } },
      },
    })
    await tique()
    assert.equal(m.vm.caixa(pend).modo, 'caixa')
    m.vm.textos.a2 = '  Sentimos muito!  '
    assert.equal(m.vm.podeEnviar(pend), true)
    await m.vm.responder(pend)
    assert.equal(m.perguntas.length, 1)
    assert.match(m.perguntas[0], /PÚBLICO/)
    assert.ok(m.perguntas[0].includes(A.AVISO_RESPOSTA_PUBLICA), 'a confirmação diz que é pública')
    assert.deepEqual(corpo, { texto: 'Sentimos muito!' })
    assert.equal(m.vm.dados.value.itens[0].respondida, true)
    assert.equal(m.vm.dados.value.pendentes, 0)
    assert.equal(m.vm.textos.a2, undefined, 'a caixa esvazia')
    assert.deepEqual(m.avisos.map((a) => a[0]), ['ok'])
    assert.ok(m.emitidos.some(([n]) => n === 'mudou'), 'a conversa relê (etiqueta)')
    assert.equal(m.vm.grupos.value.abertas.length, 0, 'respondida vira linha')
  }

  // 3. Desistiu na confirmação: nada sai. Texto acima do limite: botão desligado.
  {
    const pend = av({ id: 'a3', pendente: true, pode_responder: true, motivo_sem_resposta: null })
    const m = montar({ props: { conversaId: 'conv-chat', canEdit: true }, rotas: { [GET]: resposta([pend]) }, confirmar: () => false })
    await tique()
    m.vm.textos.a3 = 'ok'
    await m.vm.responder(pend)
    assert.equal(m.chamadas.length, 1, 'cancelou: sem POST')
    m.vm.textos.a3 = 'x'.repeat(501)
    assert.equal(m.vm.tamanhoDaResposta(pend), 501)
    assert.equal(m.vm.podeEnviar(pend), false, 'acima de 500')
  }

  // 4. A plataforma recusou: o texto fica; recusa do backend: a frase dele; conversa_mudou: confirma e manda de novo.
  {
    const pend = av({ id: 'a4', pendente: true, pode_responder: true, motivo_sem_resposta: null })
    let n = 0
    const m = montar({
      props: { conversaId: 'conv-chat', canEdit: true },
      rotas: {
        [GET]: resposta([pend]),
        'POST /api/atendimento/avaliacoes/a4/responder': (opts) => {
          n += 1
          if (n === 1) return { mensagem: { id: 'm', status: 'falhou', erro: 'shopee HTTP 429 too_many_requests' }, avaliacao: pend }
          if (n === 2) { const e = new Error('409'); e.statusCode = 409; e.data = { detail: { code: 'envio_desligado', detail: 'Resposta pública (aparece no anúncio): o envio pelo DaVinci está desligado (ATENDIMENTO_ENVIO_ATIVO).' } }; throw e }
          if (n === 3) { const e = new Error('409'); e.statusCode = 409; e.data = { detail: { code: 'conversa_mudou', detail: 'a loja respondeu às 11:00' } }; throw e }
          assert.deepEqual(opts.body, { texto: 'Oi', confirmar: true })
          return { mensagem: { id: 'm2', status: 'revisar' }, avaliacao: pend }
        },
      },
    })
    await tique()
    m.vm.textos.a4 = 'Oi'
    await m.vm.responder(pend)
    assert.equal(m.avisos[0][0], 'erro')
    assert.match(m.avisos[0][1], /NÃO foi publicada/)
    assert.match(m.avisos[0][2], /esperar/)
    assert.equal(m.vm.textos.a4, 'Oi', 'recusada: o texto fica para tentar de novo')
    await m.vm.responder(pend)
    assert.match(m.avisos[1][1], /^Resposta pública \(aparece no anúncio\)/, 'a frase do backend (resposta pública)')
    await m.vm.responder(pend)
    assert.ok(m.perguntas.at(-1).includes('Responder mesmo assim?'))
    assert.equal(n, 4, 'confirmou: mandou de novo com confirmar')
    assert.equal(m.avisos.at(-1)[0], 'aviso', 'revisar: "pode ter saído"')
    assert.equal(m.vm.textos.a4, undefined)
  }

  // 5. Sem resposta do servidor: "pode ter saído", relê e avisa a conversa.
  {
    const pend = av({ id: 'a5', pendente: true, pode_responder: true, motivo_sem_resposta: null })
    const m = montar({
      props: { conversaId: 'conv-chat', canEdit: true },
      rotas: { [GET]: resposta([pend]), 'POST /api/atendimento/avaliacoes/a5/responder': () => { throw new Error('Failed to fetch') } },
    })
    await tique()
    m.vm.textos.a5 = 'Oi'
    await m.vm.responder(pend)
    await tique()
    assert.equal(m.avisos[0][0], 'aviso')
    assert.match(m.avisos[0][1], /pode ter saído/)
    assert.equal(m.chamadas.filter((c) => c.url.endsWith('/avaliacoes')).length, 2, 'relida')
    assert.ok(m.emitidos.some(([e]) => e === 'mudou'))
  }

  // 6. ML 2★: o motivo no lugar da caixa e "Marcar como tratada" (nada vai para a plataforma).
  {
    const ml = av({ id: 'a6', plataforma: 'ml', plataforma_nome: 'Mercado Livre', estrelas: 2, nota_baixa: true, pendente: true, motivo_sem_resposta: 'O Mercado Livre não permite responder a opinião pela API: use “Marcar como tratada”.', url_plataforma: 'https://www.mercadolivre.com.br/vendas/2000012345/detalhe' })
    let corpo = null
    const m = montar({
      props: { conversaId: 'conv-chat', canEdit: true },
      rotas: {
        [GET]: resposta([ml]),
        'POST /api/atendimento/avaliacoes/a6/tratada': (opts) => { corpo = opts.body; return { avaliacao: { ...ml, pendente: false, tratada_em: '2026-10-02T12:00:00Z', tratada_por_nome: 'Eduardo' } } },
      },
    })
    await tique()
    assert.equal(m.vm.caixa(ml).modo, 'motivo')
    m.vm.abrirTratar(ml)
    assert.equal(m.vm.tratarAberto.value, 'a6')
    m.vm.motivoTratada.value = '  liguei para o comprador  '
    await m.vm.marcarTratada(ml)
    assert.deepEqual(corpo, { motivo: 'liguei para o comprador' })
    assert.equal(m.vm.tratarAberto.value, null)
    assert.equal(m.vm.dados.value.itens[0].tratada_por_nome, 'Eduardo')
    assert.match(m.avisos[0][2], /Nada foi enviado à plataforma/)
    assert.ok(m.emitidos.some(([e]) => e === 'mudou'))
    // A anterior do comprador tratada daqui continua "anterior" (a rota
    // devolve do_pedido=true: é o da conversa que manda).
    const ant = { ...ml, id: 'a7', do_pedido: false, pedido: '2000099999' }
    const m2 = montar({
      props: { conversaId: 'conv-chat', canEdit: true },
      rotas: {
        [GET]: resposta([ml, ant]),
        'POST /api/atendimento/avaliacoes/a7/tratada': () => ({ avaliacao: { ...ant, do_pedido: true, pendente: false, tratada_em: '2026-10-02T12:00:00Z' } }),
      },
    })
    await tique()
    await m2.vm.marcarTratada(ant)
    assert.deepEqual(m2.vm.dados.value.itens.map((a) => [a.id, a.do_pedido, !!a.tratada_em]), [['a6', true, false], ['a7', false, true]])
    assert.equal(m2.vm.dados.value.pendentes, 1, 'o resumo acompanha')
    // Sem permissão de editar: não marca.
    const s = montar({ props: { conversaId: 'conv-chat', canEdit: false }, rotas: { [GET]: resposta([ml]) } })
    await tique()
    await s.vm.marcarTratada(ml)
    assert.equal(s.chamadas.length, 1)
  }

  // 7. A rota falhou: o cartão some quieto (a conversa segue).
  {
    const m = montar({ props: { conversaId: 'conv-chat', canEdit: true }, rotas: { [GET]: () => { throw new Error('500') } } })
    await tique()
    assert.equal(m.vm.erro.value, true)
    assert.equal(m.vm.doPedido.value, 0)
    assert.deepEqual(m.emitidos, [])
  }
}

// ------------------------------------------------ a conversa
{
  const tpl = conversa.descriptor.template.content
  const setup = conversa.descriptor.scriptSetup.content
  const cartao = (tpl.match(/<div\s+v-if="temCartaoReclamacao"\s+v-show="avaliacoesDoPedidoQtd > 0"[\s\S]*?<\/div>/) || [])[0]
  assert.ok(cartao, 'o cartão da avaliação está na conversa (só com avaliação do pedido)')
  for (const attr of ['ref="avaliacaoRef"', ':conversa-id="conversa.id"', ':canal-conversa="conversa.canal"', ':can-edit="canEdit"', '@carregado="aoCarregarAvaliacoes"', '@mudou="aoMudarAvaliacao"', '@abrir-imagem=', '@abrir-conversa=']) {
    assert.ok(cartao.includes(attr), `cartão: ${attr}`)
  }
  // Depois do da reclamação, antes das mensagens.
  assert.ok(tpl.indexOf('<AtendimentoAvaliacao') > tpl.indexOf('<AtendimentoReclamacao'))
  assert.ok(tpl.indexOf('<AtendimentoAvaliacao') < tpl.indexOf('ref="rolagem"'))
  // A faixa da conversa `avaliacao` vem antes da de bloqueio (o ML nasce bloqueado) e sem "reabrir".
  const faixa = (tpl.match(/<div v-else-if="conversa\.canal === 'avaliacao'"[\s\S]*?\n {8}<\/div>/) || [])[0]
  assert.ok(faixa, 'a faixa da avaliação')
  assert.ok(tpl.indexOf("conversa.canal === 'avaliacao'\" class=\"shrink-0") < tpl.indexOf("v-else-if=\"conversa.situacao === 'bloqueada'\""))
  assert.match(faixa, /PÚBLICA/)
  assert.doesNotMatch(faixa, /reabrir/)
  // A caixa de baixo, na conversa da avaliação, avisa que é pública.
  assert.match(tpl, /v-if="conversa\.canal === 'avaliacao' && podeDigitar"[\s\S]{0,200}data-aviso-publica/)
  // O selo do cabeçalho leva as estrelas; o painel recebe as avaliações.
  assert.match((tpl.match(/<AtendimentoEtiqueta[\s\S]*?\/>/) || [])[0], /:estrelas="conversa\.avaliacao_estrelas"/)
  assert.match(tpl, /<AtendimentoPedido[\s\S]*?:avaliacoes="avaliacoesDados"[\s\S]*?@abrir-imagem=/)
  // Relida no "atualizar", junto com o painel e zerada na troca de conversa.
  assert.match(trecho(setup, 'function atualizarTudo', '}'), /avaliacaoRef\.value\?\.carregar\(\)/)
  assert.match(setup, /reclamacoesQtd\.value = 0\n[\s\S]{0,120}avaliacoesDados\.value = null/)
  // Avaliação já respondida: a caixa de baixo não manda outra resposta pública.
  assert.match(trecho(setup, 'const bloqueioEnvio = computed', 'const podeDigitar'), /avaliacaoDaConversa\.value\?\.respondida/)

  // A avaliação da conversa e a contagem das do pedido.
  const js = trecho(setup, 'const avaliacaoRef = ref', 'function aoMudarAvaliacao') + '\nreturn { avaliacoesDados, aoCarregarAvaliacoes, avaliacoesDoPedidoQtd, avaliacaoDaConversa }'
  const conv = Vue.ref({ id: 'conv-aval', canal: 'avaliacao' })
  const x = new Function('ref', 'computed', 'conversa', js)(Vue.ref, Vue.computed, conv)
  assert.equal(x.avaliacoesDoPedidoQtd.value, 0)
  x.aoCarregarAvaliacoes({ itens: [av({ id: 'a', conversa_id: 'conv-aval', respondida: true }), av({ id: 'b', do_pedido: false })] })
  assert.equal(x.avaliacoesDoPedidoQtd.value, 1, 'só as do pedido abrem a faixa')
  assert.equal(x.avaliacaoDaConversa.value.id, 'a')
  conv.value = { id: 'conv-chat', canal: 'chat' }
  assert.equal(x.avaliacaoDaConversa.value, null, 'no chat a caixa de baixo é o chat')
}

// ------------------------------------------------ a lista e o canal
{
  const menu = L.FILTROS_MENU.map((f) => f.value)
  assert.ok(menu.indexOf('avaliacao') > menu.indexOf('devolucao') && menu.indexOf('avaliacao') < menu.indexOf('pre_venda'), 'Avaliação entre Devolução e Pré-venda')
  assert.equal(L.FILTROS_MENU.find((f) => f.value === 'avaliacao').label, 'Avaliação')
  assert.ok(L.FILTROS_ETIQUETA.has('avaliacao'), 'conta pelo /resumo')
  const tpl = lista.descriptor.template.content
  assert.match(tpl, /:estrelas="c\.avaliacao_estrelas"/)
  assert.match(tpl, /c\.situacao === 'bloqueada' && c\.canal === 'avaliacao'[\s\S]{0,300}sem resposta pela API/, 'a avaliação do ML não aparece como "bloqueada"')
  assert.match(lista.descriptor.scriptSetup.content, /avaliacao: 'Nenhuma avaliação sem resposta com esses filtros\.'/)

  assert.equal(P.canalLabel('avaliacao'), 'Avaliação')
  assert.ok(!P.CANAIS_ATENDIMENTO.some((c) => c.value === 'avaliacao'), 'não é caixa configurável por loja')
  assert.match(P.ERROS.ja_respondida, /já foi respondida/)
  assert.match(P.ERROS.avaliacao_nao_encontrada, /não encontrada/)
}

// ------------------------------------------------ o painel do pedido
{
  const tpl = pedido.descriptor.template.content
  const setup = pedido.descriptor.scriptSetup.content
  const secao = (tpl.match(/<section v-if="avaliacoesPainel\.length"[\s\S]*?\n {8}<\/section>/) || [])[0]
  assert.ok(secao, 'a seção Avaliação no painel')
  for (const parte of ['estrelasDe(a.estrelas)', 'situacaoAvaliacao(a)', 'midiaSegura(a.midia)', 'respondida em', 'prazo interno', 'Anteriores deste cliente', 'referrerpolicy="no-referrer"']) {
    assert.ok(secao.includes(parte), `painel: ${parte}`)
  }
  // Antes do "No DaVinci", na aba Pedido.
  assert.ok(tpl.indexOf('data-painel-avaliacao') < tpl.indexOf('<!-- No DaVinci -->'))
  const js = trecho(setup, 'const avaliacoesPainel', 'const avaliacoesPendentes') + '\nreturn { avaliacoesPainel, avaliacoesDoPedido, avaliacoesAnteriores }'
  const props = Vue.reactive({ avaliacoes: null, contexto: { avaliacoes: [{ id: 'k', plataforma: 'Shopee', estrelas: 1, pedido: 'P', do_pedido: true, criado_em: null, respondida: false, pendente: true, tratada: false, pode_responder: true }] } })
  const x = new Function('computed', 'props', 'doContexto', js)(Vue.computed, props, A.doContexto)
  assert.deepEqual(x.avaliacoesPainel.value.map((a) => [a.id, a.plataforma, a.pendente]), [['k', 'shopee', true]], 'sem o cartão: o contexto')
  props.avaliacoes = { itens: [av({ id: 'r1' }), av({ id: 'r2', do_pedido: false })] }
  assert.deepEqual(x.avaliacoesDoPedido.value.map((a) => a.id), ['r1'], 'com o cartão: a mesma resposta')
  assert.deepEqual(x.avaliacoesAnteriores.value.map((a) => a.id), ['r2'])
}

// ------------------------------------------------ a caixa de baixo da conversa `avaliacao`
// Ela publica no ANÚNCIO pela mesma rota do chat: pergunta como o cartão antes
// de qualquer envio (botão, Ctrl+Enter, "enviar mesmo assim", "tentar de
// novo"); desistiu, nada sai. No chat, não pergunta nada.
async function testarCaixaDeBaixo() {
  assert.equal(A.perguntaRespostaPublica(null, 'Oi'), `Responder em PÚBLICO?\n\n${A.AVISO_RESPOSTA_PUBLICA}\n\nA resposta:\nOi`)
  assert.ok(A.perguntaRespostaPublica('aviso do backend', 'Oi').includes('aviso do backend'), 'o aviso do backend vence')
  // O cartão pergunta com a mesma frase.
  assert.match(aval.descriptor.scriptSetup.content, /!confirm\(perguntaRespostaPublica\(aviso\.value, texto\)\)/)

  const setup = conversa.descriptor.scriptSetup.content
  const js = trecho(setup, 'async function enviar(', 'function aoTeclar') + '\nreturn { enviar, confirmaSePublica }'
  function montar({ canal, confirmar }) {
    const perguntas = []
    const chamadas = []
    const detalhe = Vue.ref({ conversa: { id: 'conv-x', canal, plataforma: 'shopee' }, mensagens: [], rascunho: null })
    const x = new Function(
      'detalhe', 'podeEnviar', 'lacunas', 'erroEnvio', 'titulo', 'texto', 'baseRascunhoId', 'ultimaVista',
      'enviandoIds', 'api', 'rolarProFim', 'concluirEnvio', 'aberta', 'carregar', 'statusDoErro', 'incertos',
      'mostrarErroEnvio', 'toasts', 'erroDaApi', 'RELER_APOS_RECUSA', 'avaliacoesDados', 'perguntaRespostaPublica', 'confirm',
      js,
    )(
      detalhe, Vue.ref(true), Vue.ref([]), Vue.ref(null), () => 'Fulano', Vue.ref('  Obrigado pela avaliação!  '), Vue.ref(null), () => null,
      new Set(), async (url, opts) => { chamadas.push({ url, opts }); return { mensagem: null } }, () => {}, () => {}, () => false, async () => {},
      P.statusDoErro, new Map(), () => {}, { success() {}, error() {}, warning() {}, info() {} }, P.erroDaApi, new Set(),
      Vue.ref({ itens: [], aviso: 'aviso do backend' }), A.perguntaRespostaPublica,
      (t) => { perguntas.push(t); return confirmar }
    )
    return { ...x, perguntas, chamadas }
  }
  // Desistiu: nada sai.
  {
    const m = montar({ canal: 'avaliacao', confirmar: false })
    await m.enviar()
    assert.equal(m.perguntas.length, 1, 'perguntou antes de publicar')
    assert.ok(m.perguntas[0].startsWith('Responder em PÚBLICO?'))
    assert.ok(m.perguntas[0].includes('aviso do backend') && m.perguntas[0].includes('Obrigado pela avaliação!'))
    assert.deepEqual(m.chamadas, [], 'desistiu: nada saiu')
  }
  // Confirmou: sai pela rota da conversa; "enviar mesmo assim" pergunta de novo.
  {
    const m = montar({ canal: 'avaliacao', confirmar: true })
    await m.enviar()
    assert.equal(m.chamadas.length, 1)
    assert.equal(m.chamadas[0].url, '/api/atendimento/conversas/conv-x/responder')
    assert.equal(m.chamadas[0].opts.body.texto, 'Obrigado pela avaliação!')
    await m.enviar({ confirmar: true })
    assert.equal(m.perguntas.length, 2, '"enviar mesmo assim" também pergunta')
    assert.equal(m.chamadas.length, 2)
    assert.equal(m.chamadas[1].opts.body.confirmar, true)
  }
  // Chat: sem pergunta.
  {
    const m = montar({ canal: 'chat', confirmar: false })
    await m.enviar()
    assert.deepEqual(m.perguntas, [])
    assert.equal(m.chamadas.length, 1)
  }
  // Ctrl+Enter passa pelo enviar(); o "tentar de novo" pergunta antes da API.
  assert.match(trecho(setup, 'function aoTeclar', '\n}'), /void enviar\(\)/)
  const iniDeNovo = setup.indexOf('async function tentarDeNovo')
  const deNovo = setup.slice(iniDeNovo, setup.indexOf('\n}\n', iniDeNovo))
  assert.ok(deNovo.indexOf('confirmaSePublica(d.conversa.canal, m.texto)') > 0, 'tentar de novo: pergunta')
  assert.ok(deNovo.indexOf('confirmaSePublica(') < deNovo.indexOf('await api'), 'tentar de novo: antes de mandar')
}

testarCartao().then(testarCaixaDeBaixo).then(() => console.log('ok: atendimento-avaliacao'), (e) => {
  console.error(e)
  process.exit(1)
})
