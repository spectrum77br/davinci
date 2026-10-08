// node tests/atendimento-ag-cancelamento.cjs — AGUARDANDO CANCELAMENTO na caixa
// /atendimento (item 4, fase 4a, 02/10/2026): o PORQUÊ do pedido em 83955.
// Aqui, a tela:
//  - o contrato com o backend: os campos do tipo AgCancelamento são os do
//    `AgCancelamentoOut` (schemas/atendimento_painel.py, nos dois sentidos), o
//    painel traz `ag_cancelamento`, e TODO código do classificador
//    (services/atendimento/ag_cancelamento.py: CODIGOS + DESCONHECIDO) e o
//    `painel.EM_ANALISE` (o motivo da Margem para quem não vê a Margem) tem
//    texto e cor na tela;
//  - os textos por código: trava interna da Margem (cinza, "não é
//    cancelamento"), falta de estoque com os SKUs (vermelho), restrição de
//    envio ("sem troca"), reprovação da Margem, pedido do comprador, "motivo
//    não registrado" com a observação do topo do Bling, e o lado seguro;
//  - "pode falar em cancelamento" só com `fala_cancelamento` (nunca mais que
//    o backend libera);
//  - o cartão AtendimentoAgCancelamento renderizado (Vue SSR), com a troca
//    aberta (fase 4c: o estado, e o Retomar só para quem mexe), o encaixe dele
//    no painel do pedido (o diálogo da troca fora do cartão) e a faixa de uma
//    linha da conversa, que lê o MESMO bloco do painel.
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
  const compiled = compileTemplate({ source: descriptor.template.content, filename, id: path.basename(rel), compilerOptions: { isTS: true } })
  assert.deepEqual(compiled.errors, [], `${rel}: template compila`)
  compileScript(descriptor, { id: path.basename(rel) })
  return { descriptor, fonte, render: compiled.code }
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

const cartao = sfc('../components/AtendimentoAgCancelamento.vue')
const G = exportsDe(cartao.descriptor)
const pedido = sfc('../components/AtendimentoPedido.vue')
const conversa = sfc('../components/AtendimentoConversa.vue')

// ------------------------------------------------ o contrato com o backend
const schemas = api('schemas/atendimento_painel.py')
const classificador = api('services/atendimento/ag_cancelamento.py')
const painel = api('services/atendimento/painel.py')

const campos = (classe, fonte) => {
  const m = fonte.match(new RegExp(`^class ${classe}\\(BaseModel\\):\\n([\\s\\S]*?)(?=\\n\\n\\n|\\n(?:class|def|async def|@) )`, 'm'))
  assert.ok(m, classe)
  return [...m[1].matchAll(/^ {4}([a-z_]+): /gm)].map((x) => x[1])
}
const tipo = (nome, fonte) => {
  const m = fonte.match(new RegExp(`export type ${nome} = \\{\\n([\\s\\S]*?)\\n\\}`))
  assert.ok(m, nome)
  return [...m[1].matchAll(/^ {2}([a-z_]+)\??: /gm)].map((x) => x[1])
}
const CAMPOS = campos('AgCancelamentoOut', schemas)
// A oferta (fase 4d) e o "Trocar" (fase 4c) já estão no backend: o bloco os traz.
assert.ok(CAMPOS.includes('oferta_envio') && CAMPOS.includes('troca_envio'), 'AgCancelamentoOut.oferta_envio e .troca_envio')
{
  // Os campos do bloco: o tipo da tela = o schema (nos dois sentidos).
  assert.deepEqual(tipo('AgCancelamento', pedido.fonte).sort(), CAMPOS.slice().sort(), 'AgCancelamento = AgCancelamentoOut')
  assert.match(pedido.fonte, /^ {2}oferta_envio\?: OfertaEnvio \| null$/m, 'a oferta é opcional na tela (API antiga)')
  assert.match(pedido.fonte, /^ {2}troca_envio\?: TrocaEnvio \| null$/m, 'o Trocar é opcional na tela (API antiga)')
  assert.match(pedido.fonte, /^ {2}troca_aberta: TrocaAberta \| null$/m, 'a troca aberta é o TrocaAbertaOut')
  assert.match(schemas, /^ {4}troca_aberta: TrocaAbertaOut \| None = None$/m)
  // O painel traz o bloco (opcional dos dois lados: só em 83955).
  assert.ok(campos('PainelOut', schemas).includes('ag_cancelamento'), 'PainelOut.ag_cancelamento')
  assert.ok(tipo('Painel', pedido.fonte).includes('ag_cancelamento'), 'Painel.ag_cancelamento')
  assert.match(schemas, /^ {4}ag_cancelamento: AgCancelamentoOut \| None = None$/m)
  assert.match(pedido.fonte, /^ {2}ag_cancelamento\?: AgCancelamento \| null$/m)

  // Todo campo que a tela lê do bloco existe no schema.
  const lidos = new Set([...cartao.fonte.matchAll(/\bag\.([a-z_]+)/g)].map((m) => m[1]))
  assert.ok(lidos.size >= 7, 'o cartão lê o bloco')
  for (const c of lidos) assert.ok(CAMPOS.includes(c), `a tela lê ag.${c}, que não está no AgCancelamentoOut`)
  for (const c of ['codigo', 'titulo', 'texto', 'fala_cancelamento', 'skus', 'conflito', 'observacao_topo', 'troca_aberta', 'oferta_envio', 'troca_envio']) {
    assert.ok(lidos.has(c), `a tela usa ag.${c}`)
  }
  // Quem diz se há troca é o bloco das sugestões, não um selo no cartão.
  assert.ok(!lidos.has('pode_sugerir_troca'), 'sem selo de troca: as sugestões trazem o Trocar')
}

// Os códigos do classificador: os de CODIGOS + o DESCONHECIDO (o lado seguro).
const CONSTANTES = Object.fromEntries([...classificador.matchAll(/^([A-Z_]+) = "([a-z_]+)"$/gm)].map((m) => [m[1], m[2]]))
const NOMES = [...((classificador.match(/^CODIGOS = \(([\s\S]*?)\)$/m) || [])[1] || '').matchAll(/([A-Z_]+),/g)].map((m) => m[1])
assert.equal(NOMES.length, 8, 'os 8 códigos do classificador')
const CLASSIFICADOR = [...NOMES.map((n) => CONSTANTES[n]), CONSTANTES.DESCONHECIDO]
assert.ok(CLASSIFICADOR.every(Boolean), 'todo código tem valor')
assert.deepEqual(
  CLASSIFICADOR.slice().sort(),
  ['cancelado_plataforma', 'desconhecido', 'manual', 'margem_reprovada', 'margem_trava', 'pedido_cliente', 'pos_nf_manual', 'restricao_envio', 'sem_estoque'],
)
// O motivo da Margem mascarado (07/10/2026, quem não vê a Margem): não é do
// classificador, é do painel (`mascarar_motivo`), com título e texto próprios.
const EM_ANALISE = (painel.match(/^EM_ANALISE = "([a-z_]+)"$/m) || [])[1]
const TEXTO_EM_ANALISE_PY = (painel.match(/^TEXTO_EM_ANALISE = "([^"]+)"$/m) || [])[1]
assert.equal(EM_ANALISE, 'em_analise')
assert.match(painel, /^TITULO_EM_ANALISE = "Em análise"$/m)
assert.match(painel, /^def mascarar_motivo\(bloco: dict\) -> dict:/m)
const CODIGOS = [...CLASSIFICADOR, EM_ANALISE]
{
  // Todo código tem cor e texto na tela (e a tela não inventa código).
  assert.deepEqual(Object.keys(G.TOM_AG_CANCELAMENTO).sort(), CODIGOS.slice().sort(), 'TOM_AG_CANCELAMENTO = os códigos do backend')
  for (const tom of Object.values(G.TOM_AG_CANCELAMENTO)) assert.ok(G.CLS_AG_CANCELAMENTO[tom], `classes da cor ${tom}`)
  // E todo código do classificador tem título no painel (o selo do cartão).
  const titulos = [...((painel.match(/^TITULO_AG_CANCELAMENTO = \{([\s\S]*?)\n\}/m) || [])[1] || '').matchAll(/^ {4}([A-Z_]+):/gm)].map((m) => CONSTANTES[m[1]])
  assert.deepEqual(titulos.slice().sort(), CLASSIFICADOR.slice().sort(), 'TITULO_AG_CANCELAMENTO cobre os códigos')
  // O texto do mascarado é o do painel.
  assert.equal(G.TEXTO_EM_ANALISE.toLowerCase(), TEXTO_EM_ANALISE_PY.toLowerCase())
  // Os formatos do texto interno que a tela lê: a restrição, o parêntese
  // do "movido à mão" e do pedido do comprador, o conflito.
  assert.ok(classificador.includes('return "restrição de envio para a região"'), 'restrição sem detalhe')
  assert.ok(classificador.includes('f"restrição de envio: {erro}"'), 'restrição com detalhe')
  assert.ok(classificador.includes('f"motivo não registrado, movido à mão no Bling ({detalhe})"'), 'o porquê do pós-NF entre parênteses')
  assert.ok(classificador.includes('f"o comprador pediu o cancelamento na plataforma ({status})"'), 'o status entre parênteses')
  assert.ok(classificador.includes('f"pedido já cancelado na plataforma, sem dizer por quem ({status})"'), 'o status do cancelado entre parênteses')
  assert.ok(classificador.includes('"a NF também marcou falta de estoque"'), 'o conflito')
}

// ------------------------------------------------ os textos por código
const ag = (codigo, extra = {}) => ({
  codigo,
  titulo: `título de ${codigo}`,
  texto: 'XYZ',
  etiqueta: true,
  fala_cancelamento: false,
  pode_sugerir_troca: false,
  skus: [],
  conflito: null,
  observacao_topo: null,
  troca_aberta: null,
  ...extra,
})
const L = G.leituraAgCancelamento
const tudo = (l) => [l.frase, l.fala || '', l.troca || '', l.faixa, ...l.detalhes].join(' | ')

// Todo código conhecido tem a frase DA TELA (não cai no texto cru do backend).
for (const c of CODIGOS) {
  const l = L(ag(c))
  assert.ok(l.frase && l.faixa, `${c}: frase e faixa`)
  assert.notEqual(l.frase, 'XYZ.', `${c}: tem texto próprio na tela`)
  assert.equal(l.tom, c === 'margem_reprovada' ? 'ambar' : G.TOM_AG_CANCELAMENTO[c], `${c}: cor`)
  assert.equal(l.titulo, `título de ${c}`, `${c}: o selo é o título do backend`)
}

// margem_trava (cinza): trava INTERNA, não é cancelamento — nem com a bandeira trocada.
{
  const l = L(ag('margem_trava', { etiqueta: false }))
  assert.equal(l.tom, 'cinza')
  assert.equal(l.frase, 'Trava interna da Margem: não é cancelamento. Não fale em cancelamento com o cliente.')
  assert.equal(l.faixa, l.frase, 'a faixa diz o mesmo, sem "Aguardando Cancelamento"')
  assert.equal(l.fala, null, 'a frase já diz')
  assert.deepEqual(l.detalhes, [])
  const c = L(ag('margem_trava', { etiqueta: false, conflito: 'a NF também marcou falta de estoque: dg053.ci', skus: ['dg053.ci'] }))
  assert.deepEqual(c.detalhes, ['Atenção: a NF também marcou falta de estoque: dg053.ci (a Margem decide primeiro).'], 'o conflito aparece')
  assert.doesNotMatch(tudo(L(ag('margem_trava', { fala_cancelamento: true }))), /Pode falar/, 'a trava nunca libera')
}
// sem_estoque (vermelho): os SKUs, sem selo de troca até a 4c.
{
  const l = L(ag('sem_estoque', { fala_cancelamento: true, pode_sugerir_troca: true, skus: ['dg053.sp', 'a001.sp'], texto: 'falta de estoque: dg053.sp, a001.sp' }))
  assert.equal(l.tom, 'vermelho')
  assert.equal(l.frase, 'Falta de estoque: dg053.sp, a001.sp.')
  assert.equal(l.troca, null, 'nada de "pode sugerir troca" antes da troca existir')
  assert.equal(l.fala, G.FALA_SIM)
  assert.equal(l.faixa, `Aguardando Cancelamento — Falta de estoque: dg053.sp, a001.sp. ${G.FALA_SIM}`)
  assert.equal(L(ag('sem_estoque', { fala_cancelamento: true })).frase, 'Falta de estoque do item.', 'sem SKUs')
  assert.equal(G.TROCA_SIM, undefined, 'o selo verde da troca não existe na 4a')
}
// restricao_envio: o texto do sweep como frase (sem "Restrição" dobrado) e "sem troca".
{
  const l = L(ag('restricao_envio', { fala_cancelamento: true, texto: 'Restrição Shopee — Apple não envia pro RJ: iphone13.sp' }))
  assert.equal(l.tom, 'laranja')
  assert.equal(l.frase, 'Restrição Shopee — Apple não envia pro RJ: iphone13.sp.')
  assert.equal(l.troca, 'sem troca')
  assert.match(l.faixa, /\(sem troca\)\. Pode falar em cancelamento/)
  assert.doesNotMatch(tudo(l), /Restrição de envio: Restrição/)
  assert.equal(L(ag('restricao_envio', { texto: 'Restrição da loja: não envia pro AM' })).frase, 'Restrição da loja: não envia pro AM.')
  // O prefixo que o backend antepõe não dobra; o detalhe solto ganha o
  // prefixo; o texto padrão vira a região.
  assert.equal(L(ag('restricao_envio', { texto: 'restrição de envio: loja fechada no AM' })).frase, 'Restrição de envio: loja fechada no AM.')
  assert.equal(L(ag('restricao_envio', { texto: 'restrição de envio: Restrição da loja: não envia pro RJ' })).frase, 'Restrição da loja: não envia pro RJ.')
  assert.equal(L(ag('restricao_envio', { texto: 'restrição de envio para a região' })).frase, 'Restrição de envio para a região do comprador.')
  assert.equal(G.detalheRestricao('Restrição da loja: não envia pro AM'), 'Restrição da loja: não envia pro AM')
  assert.equal(G.detalheRestricao(null), '')
  assert.equal(G.fraseRestricao('não envia pro RJ'), 'Restrição de envio: não envia pro RJ')
  assert.equal(G.fraseRestricao(''), 'Restrição de envio para a região do comprador')
}
// margem_reprovada: por pessoa = cancelamento decidido; sem pessoa = confira antes.
{
  const pessoa = L(ag('margem_reprovada', { fala_cancelamento: true, texto: 'reprovado por pessoa na aba Margem: cancelamento decidido' }))
  assert.equal(pessoa.tom, 'laranja')
  assert.equal(pessoa.frase, 'Reprovado por pessoa na aba Margem: cancelamento decidido pela loja.')
  assert.equal(pessoa.fala, G.FALA_SIM)
  const robo = L(ag('margem_reprovada', { texto: 'reprovado na Margem sem decisão de pessoa registrada: confira antes de cancelar', conflito: 'a NF também marcou restrição de envio' }))
  assert.equal(robo.tom, 'ambar')
  assert.match(robo.frase, /^Reprovado na Margem sem decisão de pessoa registrada/)
  assert.equal(robo.fala, G.FALA_NAO)
  assert.equal(robo.detalhes.length, 1, 'o conflito aparece')
}
// pedido_cliente: o comprador pediu, com o status da plataforma.
{
  const l = L(ag('pedido_cliente', { fala_cancelamento: true, texto: 'o comprador pediu o cancelamento na plataforma (IN_CANCEL)' }))
  assert.equal(l.tom, 'laranja')
  assert.equal(l.frase, 'O comprador pediu o cancelamento na plataforma.')
  assert.deepEqual(l.detalhes, ['Status na plataforma: IN_CANCEL.'])
  assert.equal(l.fala, G.FALA_SIM)
}
// cancelado_plataforma: já cancelado lá, sem dizer por quem (nunca "o comprador pediu").
{
  const l = L(ag('cancelado_plataforma', { fala_cancelamento: true, texto: 'pedido já cancelado na plataforma, sem dizer por quem (CANCELLED)' }))
  assert.equal(l.tom, 'laranja')
  assert.equal(l.frase, 'O pedido já está cancelado na plataforma (sem dizer por quem).')
  assert.deepEqual(l.detalhes, ['Status na plataforma: CANCELLED.'])
  assert.equal(l.fala, G.FALA_SIM)
  assert.doesNotMatch(tudo(l), /comprador pediu/)
}
// pos_nf_manual / manual: "Motivo não registrado" + a observação do topo do Bling.
{
  const pos = L(ag('pos_nf_manual', { texto: 'motivo não registrado, movido à mão no Bling (NF já emitida)', observacao_topo: '02/10 - cliente pediu para cancelar pelo chat' }))
  assert.equal(pos.tom, 'ambar')
  assert.match(pos.frase, /^Motivo não registrado/)
  assert.deepEqual(pos.detalhes, ['NF já emitida.'])
  assert.equal(pos.observacao, '02/10 - cliente pediu para cancelar pelo chat')
  assert.equal(pos.fala, G.FALA_NAO)
  const velha = L(ag('pos_nf_manual', { texto: 'motivo não registrado, movido à mão no Bling (a marca de falta de estoque da NF é velha: o pedido mudou depois)' }))
  assert.deepEqual(velha.detalhes, ['A marca de falta de estoque da NF é velha: o pedido mudou depois.'])
  const manual = L(ag('manual', { texto: 'motivo não registrado, movido à mão no Bling', observacao_topo: '  ' }))
  assert.match(manual.frase, /^Motivo não registrado/)
  assert.deepEqual(manual.detalhes, [])
  assert.equal(manual.observacao, null, 'observação em branco não aparece')
  assert.equal(G.entreParenteses('sem parênteses'), null)
}
// em_analise (quem não vê a Margem): cinza, sem o porquê nem que é da Margem.
{
  const l = L(ag('em_analise', { titulo: 'Em análise', texto: 'em análise pela equipe', etiqueta: false }))
  assert.equal(l.tom, 'cinza')
  assert.equal(l.frase, 'Em análise pela equipe.')
  assert.equal(l.fala, G.FALA_NAO)
  assert.deepEqual(l.detalhes, [])
  assert.equal(l.observacao, null)
  assert.doesNotMatch(tudo(l), /Margem|margem/, 'nada diz que é da Margem')
  // A bandeira do backend vale igual (a reprovação por pessoa libera falar).
  assert.equal(L(ag('em_analise', { fala_cancelamento: true })).fala, G.FALA_SIM)
}
// desconhecido (a classificação falhou) e código novo: o lado seguro.
{
  const d = L(ag('desconhecido', { etiqueta: false, texto: 'não consegui conferir o motivo agora: não fale em cancelamento antes de conferir' }))
  assert.equal(d.tom, 'cinza')
  assert.equal(d.frase, 'Motivo não conferido agora.')
  assert.equal(d.fala, G.FALA_NAO)
  const novo = L(ag('codigo_do_futuro', { titulo: 'Algo novo', texto: 'um motivo que a tela não conhece' }))
  assert.equal(novo.tom, 'ambar')
  assert.equal(novo.frase, 'Um motivo que a tela não conhece.', 'mostra o texto do backend')
  assert.equal(novo.titulo, 'Algo novo')
  assert.equal(novo.fala, G.FALA_NAO)
  assert.equal(L(ag('codigo_do_futuro', { titulo: '', texto: '' })).frase, 'Aguardando Cancelamento.')
}
// "Pode falar em cancelamento" SÓ com `fala_cancelamento` — em todo código.
for (const c of [...CODIGOS, 'codigo_do_futuro']) {
  const nao = L(ag(c, { fala_cancelamento: false, pode_sugerir_troca: true, skus: ['x.sp'] }))
  assert.doesNotMatch(tudo(nao), /Pode falar/, `${c}: sem fala_cancelamento, nunca libera`)
  if (c !== 'margem_trava') assert.match(nao.faixa, /Não fale em cancelamento/, `${c}: a faixa avisa`)
  const sim = L(ag(c, { fala_cancelamento: true }))
  if (c !== 'margem_trava') assert.equal(sim.fala, G.FALA_SIM, `${c}: com a bandeira, libera`)
}

// ------------------------------------------------ o cartão renderizado (Vue SSR)
const trocaSfc = sfc('../components/AtendimentoTroca.vue')
const TR = exportsDe(trocaSfc.descriptor)

async function testarCartao() {
  const compilado = {}
  new Function('exports', 'require', transpile(cartao.render))(compilado, require)
  const setup = cartao.descriptor.scriptSetup.content.replace(/^import[\s\S]*?from\s+'[^']+'\s*$/gm, '')
  const icone = (nome) => ({ name: nome, render: () => Vue.h('i', { 'data-icone': nome }) })
  const ICONES = ['Ban', 'HelpCircle', 'Hourglass', 'Lock', 'PackageX', 'RotateCcw', 'Shuffle']
  const fabrica = new Function(
    'computed', 'withDefaults', 'defineProps', 'defineEmits', 'leituraAgCancelamento', 'CLS_AG_CANCELAMENTO', 'useAcessoDaTroca', ...ICONES,
    transpile(setup + '\nreturn { leitura, cls, icone, acesso, trocaAberta, podeRetomar, emit }'),
  )
  async function renderizar(bloco, sugestoes = null, { numero = null, trocar = false } = {}) {
    const props = Vue.reactive({ ag: bloco, sugestoes, numero, conversaId: 'conv-1' })
    const emitidos = []
    const acesso = Vue.computed(() => ({ trocar, ofertar: trocar }))
    const estado = fabrica(
      Vue.computed, (p) => p, () => props, () => (...a) => emitidos.push(a),
      G.leituraAgCancelamento, G.CLS_AG_CANCELAMENTO, () => acesso, ...ICONES.map(icone),
    )
    const app = Vue.createSSRApp({
      setup: () => ({ ag: bloco, sugestoes, numero, conversaId: 'conv-1', ...estado, rotuloEstado: TR.rotuloEstado, quemTroca: TR.quemTroca }),
      render: compilado.render,
    })
    for (const n of ICONES) app.component(n, icone(n))
    // As sugestões (fase 4b) têm componente e teste próprios
    // (tests/atendimento-troca-sugestoes.cjs): aqui, só o encaixe e o que o
    // cartão passa (o pedido, a permissão, a troca aberta, a oferta).
    app.component('AtendimentoTrocaSugestoes', {
      props: ['sugestoes', 'numero', 'conversaId', 'podeTrocar', 'podeOfertar', 'trocaAberta', 'ofertaEnvio'],
      render() {
        return Vue.h('div', {
          'data-stub-troca': this.sugestoes.itens.length,
          'data-numero': this.numero,
          'data-pode-trocar': String(this.podeTrocar),
          'data-pode-ofertar': String(this.podeOfertar),
          'data-com-troca-aberta': String(!!this.trocaAberta),
          'data-oferta': this.ofertaEnvio ? String(this.ofertaEnvio.disponivel) : 'sem',
        })
      },
    })
    return { html: await renderToString(app), estado, emitidos }
  }
  const html_ = async (...a) => (await renderizar(...a)).html
  // Trava da Margem: cinza, cadeado, a frase e o conflito.
  {
    const html = await html_(ag('margem_trava', { etiqueta: false, titulo: 'Trava interna da Margem', texto: 'trava interna da Margem (segurado para análise): não é cancelamento', conflito: 'a NF também marcou falta de estoque: dg053.ci' }))
    assert.match(html, /data-painel-ag-cancelamento/)
    assert.match(html, /data-codigo="margem_trava"/)
    assert.match(html, /data-icone="Lock"/)
    assert.ok(html.includes('Trava interna da Margem: não é cancelamento. Não fale em cancelamento com o cliente.'))
    assert.ok(html.includes('Atenção: a NF também marcou falta de estoque: dg053.ci'))
    assert.ok(html.includes('bg-muted/40'), 'cinza')
    assert.doesNotMatch(html, /Pode falar/)
  }
  // Falta de estoque: vermelho, os SKUs, sem selo de troca (4c).
  {
    const html = await html_(ag('sem_estoque', { titulo: 'Falta de estoque', fala_cancelamento: true, pode_sugerir_troca: true, skus: ['dg053.sp'] }))
    assert.match(html, /data-icone="PackageX"/)
    assert.ok(html.includes('Falta de estoque: dg053.sp.'))
    assert.ok(html.includes('border-red-500/40'), 'vermelho')
    assert.ok(html.includes(G.FALA_SIM))
    assert.doesNotMatch(html, /sugerir troca|emerald/)
  }
  // Restrição: o texto do sweep sem "Restrição" dobrado, e "sem troca".
  {
    const html = await html_(ag('restricao_envio', { titulo: 'Restrição de envio', fala_cancelamento: true, texto: 'Restrição da loja: não envia pro AM' }))
    assert.ok(html.includes('>Restrição da loja: não envia pro AM.<'))
    assert.doesNotMatch(html, /Restrição de envio: Restrição/)
    assert.ok(html.includes('>sem troca<'))
  }
  // Movido à mão: "Motivo não registrado" e a observação do topo do Bling.
  {
    const html = await html_(ag('manual', { titulo: 'Motivo não registrado', texto: 'motivo não registrado, movido à mão no Bling', observacao_topo: 'cliente pediu <b>cancelar</b>' }))
    assert.ok(html.includes('Motivo não registrado: o pedido foi movido à mão no Bling.'))
    assert.ok(html.includes('Observação do Bling:'))
    assert.ok(html.includes('cliente pediu &lt;b&gt;cancelar&lt;/b&gt;'), 'a observação vai como texto, nunca HTML')
    assert.ok(html.includes(G.FALA_NAO))
  }
  // As sugestões de troca (fase 4b) vão DENTRO do cartão, só quando vêm.
  {
    const bloco = ag('sem_estoque', { titulo: 'Falta de estoque', fala_cancelamento: true, pode_sugerir_troca: true, skus: ['dg053.sp'] })
    assert.doesNotMatch(await html_(bloco), /data-stub-troca/)
    const html = await html_(bloco, { itens: [{}], aviso: null, catalogo_lido_em: null, ve_custo: true, falhou: false })
    const dentro = html.indexOf('data-stub-troca="1"')
    assert.ok(dentro > html.indexOf('border-red-500/40'), 'dentro do cartão')
    assert.ok(dentro < html.lastIndexOf('</section>'))
  }
  // Lado seguro: ícone de dúvida; o mascarado, a ampulheta (não o cadeado da trava).
  assert.match(await html_(ag('desconhecido', { etiqueta: false })), /data-icone="HelpCircle"/)
  {
    const h = await html_(ag('em_analise', { titulo: 'Em análise', texto: 'em análise pela equipe', etiqueta: false }))
    assert.match(h, /data-icone="Hourglass"/)
    assert.doesNotMatch(h, /data-icone="Lock"|Trava interna/)
    assert.ok(h.includes('Em análise pela equipe.'))
  }
  // O que o cartão passa às sugestões (fase 4c/4d): o pedido, quem pode, a
  // troca aberta e a oferta — sem permissão, os botões não aparecem lá.
  {
    const sug = { itens: [{}], aviso: null, catalogo_lido_em: null, ve_custo: true, falhou: false }
    const bloco = ag('sem_estoque', { skus: ['dg053.sp'], oferta_envio: { disponivel: true, motivo: null, texto_motivo: null } })
    const so = await html_(bloco, sug, { numero: '300101', trocar: false })
    assert.match(so, /data-numero="300101"/)
    assert.match(so, /data-pode-trocar="false"/)
    assert.match(so, /data-pode-ofertar="false"/)
    assert.match(so, /data-oferta="true"/)
    const mexe = await html_(bloco, sug, { numero: '300101', trocar: true })
    assert.match(mexe, /data-pode-trocar="true"/)
    assert.match(mexe, /data-pode-ofertar="true"/)
    // API antiga, sem `oferta_envio`: o bloco recebe null (o botão fica cinza).
    assert.match(await html_(ag('sem_estoque'), sug, { numero: '1', trocar: true }), /data-oferta="sem"/)
  }
  // A troca aberta (fase 4c): o estado, quem conduz, o erro e — só para quem
  // mexe, com o pedido e sem ninguém conduzindo — o Retomar (que pede ao painel).
  {
    const aberta = {
      id: 't-1', estado: 'em_atendido', sku_antigo: 'dg053.ci', sku_novo: 'dg056.ci', nivel: 1, automatica: false,
      criado_por_nome: 'Thorfinn', created_at: '2026-10-08T13:05:00Z', codigo_erro: 'bling_indisponivel',
      erro: 'O Bling não respondeu agora — tente de novo em instantes.', pode_retomar: true,
    }
    const bloco = ag('sem_estoque', { skus: ['dg053.ci'], troca_aberta: aberta })
    const sug = { itens: [{}], aviso: null, catalogo_lido_em: null, ve_custo: true, falhou: false }
    const r = await renderizar(bloco, sug, { numero: '300101', trocar: true })
    assert.match(r.html, /data-troca-aberta data-estado="em_atendido"/)
    assert.ok(r.html.includes('Troca em andamento:'))
    assert.ok(r.html.includes(TR.rotuloEstado('em_atendido')))
    assert.ok(r.html.includes('dg053.ci') && r.html.includes('dg056.ci'))
    assert.ok(r.html.includes('Thorfinn, 08/10 10:05'), 'quem e quando (Brasília)')
    assert.ok(r.html.includes(aberta.erro))
    assert.match(r.html, /data-retomar-troca/)
    assert.match(r.html, /data-com-troca-aberta="true"/, 'as sugestões sabem que há troca aberta')
    assert.ok(r.html.indexOf('data-troca-aberta') < r.html.indexOf('data-stub-troca'), 'o estado antes das sugestões')
    // O Retomar pede ao painel (o diálogo mora lá).
    assert.equal(r.estado.podeRetomar.value, true)
    assert.match(cartao.descriptor.template.content, /data-retomar-troca\n\s+@click="emit\('retomar'\)"/)
    // Quem só lê: o estado, sem o botão.
    const so = await html_(bloco, sug, { numero: '300101', trocar: false })
    assert.match(so, /data-troca-aberta/)
    assert.doesNotMatch(so, /data-retomar-troca/)
    // Sem o nº do pedido, sem botão.
    assert.doesNotMatch(await html_(bloco, sug, { numero: null, trocar: true }), /data-retomar-troca/)
    // Alguém conduzindo agora (a vez ainda vale): sem botão, com o aviso.
    const conduzindo = await html_(ag('sem_estoque', { troca_aberta: { ...aberta, pode_retomar: false } }), null, { numero: '300101', trocar: true })
    assert.doesNotMatch(conduzindo, /data-retomar-troca/)
    assert.ok(conduzindo.includes('alguém está conduzindo agora'))
    // Robô de lote.
    assert.ok((await html_(ag('sem_estoque', { troca_aberta: { ...aberta, automatica: true, criado_por_nome: TR.NOME_ROBO } }))).includes('robô de lote'))
  }
  // Sem v-html no cartão; o diálogo da troca NÃO mora no cartão (some com ele).
  assert.doesNotMatch(cartao.fonte, /v-html/)
  assert.doesNotMatch(cartao.descriptor.template.content, /<AtendimentoTroca\b/)
}

// ------------------------------------------------ o encaixe no painel do pedido
{
  const tpl = pedido.descriptor.template.content
  const setup = pedido.descriptor.scriptSetup.content
  // Com as sugestões de troca da fase 4b (o mesmo bloco do painel, dentro do
  // cartão), o pedido e a conversa (4c/4d) e os eventos da troca.
  const encaixe = (tpl.match(/<AtendimentoAgCancelamento\n[\s\S]*?\/>/) || [])[0]
  assert.ok(encaixe, 'o cartão no painel')
  for (const a of ['v-if="agCancelamento"', ':ag="agCancelamento"', ':sugestoes="sugestoesTroca"', ':numero="numeroBling"', ':conversa-id="conversa.id"', '@trocar="abrirTroca"', '@retomar="abrirTroca(null)"', '@mudou="emit(\'recarregarPainel\', false)"']) {
    assert.ok(encaixe.includes(a), `o cartão recebe ${a}`)
  }
  // No lugar reservado: logo abaixo do Estoque, antes da Margem (aba Pedido).
  const pos = tpl.indexOf('<AtendimentoAgCancelamento')
  assert.ok(pos > tpl.indexOf('<!-- ESTOQUE'), 'depois do estoque')
  assert.ok(pos < tpl.indexOf('<!-- MARGEM'), 'antes da margem')
  assert.ok(pos > tpl.indexOf('ENCAIXE (item 4'), 'no encaixe do item 4')
  // Lê o bloco do painel; sem ele (API antiga, fora de 83955), nada.
  const js = trecho(setup, 'const agCancelamento', 'const NIVEL_SALDO_CLS') + '\nreturn { agCancelamento, numeroBling, trocaDialogo, trocaNumero, trocaEscolha, trocaAbertaDoDialogo, trocaForaDe83955, abrirTroca }'
  const pnl = Vue.ref(null)
  const y = new Function('computed', 'ref', 'pnl', js)(Vue.computed, Vue.ref, pnl)
  const x = y.agCancelamento
  assert.equal(x.value, null)
  pnl.value = { estoque: { itens: [] } }
  assert.equal(x.value, null, 'API antiga, sem o campo')
  pnl.value = { ag_cancelamento: ag('sem_estoque') }
  assert.equal(x.value.codigo, 'sem_estoque')
  // O diálogo da troca (fase 4c) mora no painel, FORA do cartão: a troca tira
  // o pedido de 83955, o painel relido some com o cartão, e o resultado fica.
  const dialogo = (tpl.match(/<AtendimentoTroca\n[\s\S]*?\/>/) || [])[0]
  assert.ok(dialogo, 'o diálogo no painel')
  assert.ok(tpl.indexOf('<AtendimentoTroca\n') > tpl.indexOf('<!-- ═══ CUPOM'), 'fora das abas e do cartão')
  for (const a of ['v-if="trocaDialogo && trocaNumero"', 'v-model:aberto="trocaDialogo"', ':numero="trocaNumero"', ':escolha="trocaEscolha"', ':troca-aberta="trocaAbertaDoDialogo"', '@mudou="emit(\'recarregarPainel\', false)"']) {
    assert.ok(dialogo.includes(a), `o diálogo recebe ${a}`)
  }
  // "Trocar": guarda o pedido e a escolha na abertura; "Retomar": a troca aberta.
  y.abrirTroca({ sku_antigo: 'a', sku_novo: 'b', nome: null, nivel: 1, mesmo_produto: false })
  assert.equal(y.trocaDialogo.value, false, 'sem o nº do pedido, nada abre')
  const aberta = { id: 't-1', estado: 'item_trocado', pode_retomar: true }
  pnl.value = { pedido: { numero_bling: '300101' }, ag_cancelamento: ag('sem_estoque', { troca_aberta: aberta }) }
  y.abrirTroca({ sku_antigo: 'a', sku_novo: 'b', nome: null, nivel: 1, mesmo_produto: false })
  assert.equal(y.trocaDialogo.value, true)
  assert.equal(y.trocaNumero.value, '300101')
  assert.equal(y.trocaEscolha.value.sku_novo, 'b')
  assert.equal(y.trocaAbertaDoDialogo.value, null, 'a escolha abre a prévia')
  y.trocaDialogo.value = false
  y.abrirTroca(null)
  assert.equal(y.trocaDialogo.value, true)
  assert.equal(y.trocaEscolha.value, null)
  assert.deepEqual(y.trocaAbertaDoDialogo.value, aberta, 'o Retomar abre na troca aberta')
  // O painel relido (pedido fora de 83955) não mexe no que o diálogo guardou.
  pnl.value = { pedido: { numero_bling: '300101' }, ag_cancelamento: null }
  assert.equal(y.trocaNumero.value, '300101')
  assert.deepEqual(y.trocaAbertaDoDialogo.value, aberta)
  y.trocaDialogo.value = false
  y.abrirTroca(null)
  assert.equal(y.trocaDialogo.value, false, 'sem troca aberta, o Retomar não abre nada')
  // A troca parada no meio com o pedido FORA de 83955 (o PATCH 6 falhou, o
  // espelho foi a 9): sem o cartão, vem no `troca_aberta` do painel — a
  // faixa própria aparece e o Retomar abre nela.
  const parada = { id: 't-2', estado: 'em_atendido', pode_retomar: true }
  pnl.value = { pedido: { numero_bling: '300101' }, ag_cancelamento: null, troca_aberta: parada }
  assert.deepEqual(y.trocaForaDe83955.value, parada)
  y.abrirTroca(null)
  assert.equal(y.trocaDialogo.value, true)
  assert.deepEqual(y.trocaAbertaDoDialogo.value, parada, 'o Retomar da troca fora de 83955')
  // Em 83955, a do cartão manda (a faixa de fora não aparece).
  pnl.value = { pedido: { numero_bling: '300101' }, ag_cancelamento: ag('sem_estoque', { troca_aberta: aberta }), troca_aberta: aberta }
  assert.equal(y.trocaForaDe83955.value, null)
  const faixaFora = (tpl.match(/<section\n\s+v-if="trocaForaDe83955"[\s\S]*?<\/section>/) || [])[0]
  assert.ok(faixaFora, 'a faixa da troca parada fora de 83955')
  assert.match(faixaFora, /data-troca-aberta-fora/)
  assert.match(faixaFora, /v-if="acessoTroca\.trocar && trocaForaDe83955\.pode_retomar"/, 'o Retomar só para quem mexe')
  assert.match(faixaFora, /@click="abrirTroca\(null\)"/)
}

// ------------------------------------------------ a faixa da conversa
{
  const tpl = conversa.descriptor.template.content
  const setup = conversa.descriptor.scriptSetup.content
  const faixa = (tpl.match(/<div\s+v-if="agCancelamento"[\s\S]*?\n {8}<\/div>/) || [])[0]
  assert.ok(faixa, 'a faixa do Ag. cancelamento')
  assert.match(faixa, /data-faixa-ag-cancelamento/)
  assert.match(faixa, /:class="CLS_AG_CANCELAMENTO\[agCancelamento\.tom\]\.faixa"/)
  assert.match(faixa, /<span class="min-w-0 flex-1 truncate">\{\{ agCancelamento\.faixa \}\}<\/span>/, 'uma linha só')
  assert.match(faixa, /:title="agCancelamento\.faixa"/, 'o texto inteiro no título')
  // No bloco das faixas: depois do "não precisa de resposta", antes do cartão da reclamação.
  const pos = tpl.indexOf('data-faixa-ag-cancelamento')
  assert.ok(pos > tpl.indexOf('v-if="conversa.sem_resposta_necessaria" class="shrink-0'), 'no bloco das faixas')
  assert.ok(pos < tpl.indexOf('v-if="temCartaoReclamacao"'), 'antes do cartão da reclamação')
  assert.ok(pos < tpl.indexOf('ref="rolagem"'), 'antes das mensagens')
  assert.match(setup, /import \{ CLS_AG_CANCELAMENTO, leituraAgCancelamento \} from '~\/components\/AtendimentoAgCancelamento\.vue'/)
  // O mesmo bloco do painel (painelDados), e o mesmo texto do cartão.
  const js = trecho(setup, 'const agCancelamento = computed', '// ─── caixa: Responder') + '\nreturn agCancelamento'
  const painelDados = Vue.ref(null)
  const x = new Function('computed', 'painelDados', 'leituraAgCancelamento', js)(Vue.computed, painelDados, G.leituraAgCancelamento)
  assert.equal(x.value, null, 'sem painel, sem faixa')
  painelDados.value = { ag_cancelamento: null }
  assert.equal(x.value, null, 'fora de 83955, sem faixa')
  const bloco = ag('margem_trava', { etiqueta: false })
  painelDados.value = { ag_cancelamento: bloco }
  assert.deepEqual(x.value, G.leituraAgCancelamento(bloco))
  assert.equal(x.value.faixa, G.TEXTO_TRAVA_MARGEM)
  // A troca de conversa zera o painel (e com ele a faixa).
  assert.match(setup, /\/\/ guardada no AtendimentoNota \(por conversa\)\.\n {2}painelDados\.value = null/)
}

testarCartao().then(() => console.log('ok: atendimento-ag-cancelamento'), (e) => {
  console.error(e)
  process.exit(1)
})
