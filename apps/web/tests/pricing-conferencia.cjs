// Run from apps/web: node tests/pricing-conferencia.cjs
// Conferência dos anúncios (02/11/2026): a regra do (!) — anúncio cuja
// diferença para o preço da Tabela passa da "% margem de erro". Executa a
// função real do SFC, sem API nem DOM.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const { parse } = require('vue/compiler-sfc')

const filename = path.resolve(__dirname, '../pages/pricing/[tab].vue')
const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
assert.deepEqual(errors, [])
const source = ts.createSourceFile(filename, descriptor.scriptSetup.content,
  ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
// M-22 (24/11/2026): a regra do (!) e do cartão vermelho saiu da página para
// lib/cartaoVermelho.ts (a mesma das Pendências do Faturamento). A página
// importa de lá; os testes abaixo rodam o módulo de verdade.
const libFonte = fs.readFileSync(path.resolve(__dirname, '../lib/cartaoVermelho.ts'), 'utf8')
const libOut = ts.transpileModule(libFonte, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS }, reportDiagnostics: true,
})
assert.deepEqual(libOut.diagnostics, [])
const L = {}
new Function('exports', 'require', libOut.outputText)(L, () => { throw new Error('lib/cartaoVermelho.ts não importa nada') })
assert.match(descriptor.scriptSetup.content, /from '~\/lib\/cartaoVermelho'/, 'a página usa a regra do módulo')
for (const nome of ['motivosDoCartao', 'tipoSemAnuncio', 'margemDaLinha', 'anunciosForaDaMargem', 'margemDaResposta']) {
  assert.ok(!source.statements.some((s) => ts.isFunctionDeclaration(s) && s.name?.text === nome),
    `${nome} não tem cópia na página`)
}
const fora = L.anunciosForaDaMargem

const a = (preco, anuncio = 'MLB1') => ({ anuncio, variacao: null, preco, preco_promocional: null, ativo: true, motivo: null })

// Tabela 1000, margem 5%: 1050 está NA margem (não marca); 1051 e 949 passam.
assert.deepEqual(fora([a(1050)], 1000, 5), [])
assert.deepEqual(fora([a(950)], 1000, 5), [])
assert.equal(fora([a(1051)], 1000, 5).length, 1)
assert.equal(fora([a(949)], 1000, 5).length, 1)
// Arredondamento: 924 × 1,05 = 970,2 fica na margem.
assert.deepEqual(fora([a(970.2)], 924, 5), [])
// Anúncio sem leitura não conta; só os fora voltam.
assert.deepEqual(fora([a(null), a(1000, 'MLB2'), a(1200, 'MLB3')], 1000, 5).map((x) => x.anuncio), ['MLB3'])
// Margem zero: qualquer diferença marca, igual não marca.
assert.equal(fora([a(1001)], 1000, 0).length, 1)
assert.deepEqual(fora([a(1000)], 1000, 0), [])
// Sem preço de Tabela válido: nada a comparar.
assert.deepEqual(fora([a(10)], 0, 5), [])
assert.deepEqual(fora([a(10)], NaN, 5), [])
console.log('pricing conferencia ok')

// Balão do (!): mostra o motivo do leitor (ex.: TikTok "sem promoção ativa"),
// não repete "em promoção" e diz "pausado" sempre que o anúncio está inativo.
// A margem do balão é a da linha (d.margem: a do produto ou a Geral, M-2).
const pegar = (nome) => {
  const n = source.statements.find((s) => ts.isFunctionDeclaration(s) && s.name?.text === nome)
  assert.ok(n, `${nome} existe na página`)
  return ts.transpileModule(n.getText(source), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText
}
const fmtReal = (v) => `R$ ${v}`
const fmtQuando = () => '02/11, 10:00'
const fmtPct = (v) => v.toLocaleString('pt-BR', { maximumFractionDigits: 2 })
const titulo = new Function('fmtReal', 'fmtQuando', 'fmtPct',
  `${pegar('tituloDivergencia')}\nreturn tituloDivergencia`)(fmtReal, fmtQuando, fmtPct)
const d = {
  tabela: 1000,
  conferidoEm: '2026-11-02T13:00:00Z',
  naoLidos: 0,
  anuncios: 3,
  margem: 5,
  fora: [
    { anuncio: 'T1', variacao: 'S1', preco: 900, preco_promocional: null, ativo: false, motivo: 'sem promoção ativa' },
    { anuncio: 'M1', variacao: null, preco: 1200, preco_promocional: 999, ativo: true, motivo: 'em promoção' },
    { anuncio: 'M2', variacao: null, preco: 800, preco_promocional: null, ativo: false, motivo: null },
  ],
}
const txt = titulo(d)
assert.match(txt, /^Fora da margem de 5%: Tabela R\$ 1000/)
assert.match(txt, /Anúncio T1 \(variação S1\): R\$ 900, -10,0% da Tabela · anúncio pausado · sem promoção ativa/)
assert.match(txt, /Anúncio M1: R\$ 1200, \+20,0% da Tabela · em promoção por R\$ 999$/m)
assert.match(txt, /Anúncio M2: R\$ 800, -20,0% da Tabela · anúncio pausado$/m)
assert.match(txt, /Conferido em 02\/11, 10:00$/)
assert.doesNotMatch(txt, /sem preço lido/)
// Margem do produto com vírgula (era "7.5%") e o aviso dos anúncios da célula sem leitura.
const txt2 = titulo({ ...d, margem: 7.5, naoLidos: 2, anuncios: 5 })
assert.match(txt2, /^Fora da margem de 7,5%: Tabela R\$ 1000/)
assert.match(txt2, /^2 de 5 anúncios desta célula sem preço lido$/m)
// A margem não vem mais do navegador (margemValida/localStorage saíram no M-2).
assert.doesNotMatch(descriptor.scriptSetup.content, /margemValida|precos\.margemErroPct/)

// Códigos de erro do job viram texto de gente; código novo sai cru.
const erro = new Function('ERROS_CONFERENCIA', `${pegar('textoErroConferencia')}\nreturn textoErroConferencia`)({ interrompida: 'o servidor reiniciou no meio' })
assert.equal(erro('interrompida'), 'o servidor reiniciou no meio')
// Código desconhecido (texto de exceção do servidor) não aparece cru.
assert.equal(erro('TimeoutError'), 'erro inesperado no servidor — confira de novo')
assert.equal(erro(null), '')
console.log('pricing conferencia balão ok')

// Constantes de primeiro nível da página.
const pegarConst = (nome) => {
  const n = source.statements.find((s) => ts.isVariableStatement(s)
    && s.declarationList.declarations.some((d) => d.name.getText(source) === nome))
  assert.ok(n, `${nome} existe na página`)
  return ts.transpileModule(n.getText(source), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText
}

// Margem da linha (M-2, 17/11/2026): o % do produto com "Por produto" ligado,
// senão a Geral com "Geral" ligado, senão nenhuma (a linha não é conferida).
const lerPct = new Function(`${pegar('lerPct')}\nreturn lerPct`)()
const { margemDaLinha, margemDaResposta } = L
assert.equal(L.MARGEM_PADRAO, 5)
const mg = (geral_ligada, por_produto_ligada) => ({
  department: 'celular', margem_pct: 5, geral_ligada, por_produto_ligada, produtos: { p1: 12, p0: 0 }, pode_editar: true,
})
// As duas ligadas: o produto com % usa o dele; os outros, a Geral.
assert.equal(margemDaLinha(mg(true, true), 'p1'), 12)
assert.equal(margemDaLinha(mg(true, true), 'p2'), 5)
// % próprio zero vale (não cai para a Geral).
assert.equal(margemDaLinha(mg(true, true), 'p0'), 0)
// Só a Geral: todos usam a Geral, mesmo quem tem % próprio guardado.
assert.equal(margemDaLinha(mg(true, false), 'p1'), 5)
assert.equal(margemDaLinha(mg(true, false), 'p2'), 5)
// Só por produto: quem tem % usa o dele; os outros não são conferidos.
assert.equal(margemDaLinha(mg(false, true), 'p1'), 12)
assert.equal(margemDaLinha(mg(false, true), 'p2'), null)
// Nenhuma: ninguém é conferido.
assert.equal(margemDaLinha(mg(false, false), 'p1'), null)
assert.equal(margemDaLinha(mg(false, false), 'p2'), null)
// Margem ainda não carregada.
assert.equal(margemDaLinha(null, 'p1'), null)
// A resposta do servidor: NUMERIC em texto vira número; sem o campo, o padrão
// do servidor (5%, as duas ligadas) e ninguém edita.
assert.deepEqual(margemDaResposta(undefined, 'mala'),
  { department: 'mala', margem_pct: 5, geral_ligada: true, por_produto_ligada: true, produtos: {}, pode_editar: false })
assert.deepEqual(margemDaResposta({ department: 'celular', margem_pct: '7.50', geral_ligada: false, por_produto_ligada: true,
  produtos: { p1: '3.25', p2: null }, pode_editar: true }, 'celular'),
{ department: 'celular', margem_pct: 7.5, geral_ligada: false, por_produto_ligada: true, produtos: { p1: 3.25 }, pode_editar: true })
// O campo de %: vírgula ou ponto; vazio = sem % próprio; fora de 0..100 = inválido.
assert.equal(lerPct('7,5'), 7.5)
assert.equal(lerPct(' 10 '), 10)
assert.equal(lerPct(''), null)
assert.equal(lerPct('101'), undefined)
assert.equal(lerPct('-1'), undefined)
assert.equal(lerPct('abc'), undefined)
console.log('pricing conferencia margem da linha ok')

// Cartão vermelho (M-3, 17/11/2026): os motivos a–e, do /grid e da conferência.
const { motivosDoCartao } = L
const SEM_INTEGRACAO = 'Conta sem integração no DaVinci'
const SEM_ANUNCIO = 'Sem anúncio vinculado nesta conta'
const ERRO_ENVIO = 'O último envio de preço deu erro'
const integrada = { integration_id: 'I1' }
const semInteg = { integration_id: null }
const auto = { cell_status: 'auto', catalogo: null }
const conf = (situacao, anuncios = [], motivo = null) =>
  ({ situacao, motivo, anuncios, job_id: 'J1', conferido_em: '2026-11-17T12:00:00Z' })
const cartao = (e) => motivosDoCartao({ conta: integrada, celula: auto, conferida: null, divergencia: null, ...e })

// Tudo certo: sem cartão.
assert.deepEqual(cartao({}), [])
assert.deepEqual(cartao({ conferida: conf('conferida', [a(1000)]) }), [])

// a) Conta sem integração: cartão em todas as células, com ou sem preço; não
// repete o b (sem anúncio vinculado).
assert.deepEqual(cartao({ conta: semInteg }), [SEM_INTEGRACAO])
assert.deepEqual(cartao({ conta: semInteg, celula: undefined }), [SEM_INTEGRACAO])
assert.deepEqual(cartao({
  conta: semInteg,
  celula: { cell_status: 'no_link', catalogo: null },
  conferida: conf('sem_anuncio', [], 'conta sem integração'),
}), [SEM_INTEGRACAO])
assert.deepEqual(cartao({ conta: semInteg, conferida: conf('sem_anuncio', [], 'sem anúncio vinculado') }), [SEM_INTEGRACAO])
// a) Coluna de catálogo bloqueada por conta de kit sem integração (a conta
// passada é a base): o mesmo motivo, sem o b.
const catSemInteg = { cell_status: 'auto', catalogo: { anuncios: [], bloqueio: 'sem_anuncio',
  texto: 'Conta de kit sem integração do Mercado Livre — sem anúncio de catálogo' } }
assert.deepEqual(cartao({ conta: semInteg, celula: catSemInteg }), [SEM_INTEGRACAO])
assert.deepEqual(cartao({ conta: integrada, celula: catSemInteg }), [SEM_INTEGRACAO])

// b) Sem anúncio vinculado — kit: pela conferência ou pelo no_link do envio (um motivo só).
assert.deepEqual(cartao({ conferida: conf('sem_anuncio', [], 'sem anúncio vinculado') }), [SEM_ANUNCIO])
assert.deepEqual(cartao({ celula: { cell_status: 'no_link', catalogo: null } }), [SEM_ANUNCIO])
assert.deepEqual(cartao({
  celula: { cell_status: 'no_link', catalogo: null },
  conferida: conf('sem_anuncio', [], 'sem anúncio vinculado'),
}), [SEM_ANUNCIO])
// b) Catálogo: o bloqueio 'sem_anuncio' do /grid (e a conferência não repete).
const catSemAnuncio = { cell_status: 'auto', catalogo: { anuncios: [], bloqueio: 'sem_anuncio',
  texto: 'Sem anúncio de catálogo vinculado nesta conta' } }
assert.deepEqual(cartao({ celula: catSemAnuncio }), [SEM_ANUNCIO])
assert.deepEqual(cartao({ celula: catSemAnuncio,
  conferida: conf('sem_anuncio', [], 'Sem anúncio de catálogo vinculado nesta conta') }), [SEM_ANUNCIO])
assert.deepEqual(cartao({ celula: { cell_status: 'auto', catalogo: { anuncios: [], bloqueio: 'sem_anuncio', texto: null } } }),
  [SEM_ANUNCIO])
// Marca de catálogo ainda não lida: não se sabe — sem cartão.
assert.deepEqual(cartao({ celula: { cell_status: 'auto', catalogo: { anuncios: [], bloqueio: 'sem_anuncio',
  texto: 'Marca de catálogo ainda não lida nesta conta (a varredura diária lê às 10h; …)' } } }), [])

// Bloqueios de catálogo que são regra do envio: sem cartão.
const anCat = (external_id, status, bloqueio) => ({ external_id, status, listing_type: 'gold_pro', sincronizado_com: [], bloqueio })
assert.deepEqual(cartao({ celula: { cell_status: 'auto', catalogo: { anuncios: [anCat('MLB7', 'active', 'sincronizado')],
  bloqueio: 'sincronizado', texto: 'Anúncio de catálogo MLB7 sincronizado com o anúncio comum MLB1' } } }), [])
assert.deepEqual(cartao({ celula: { cell_status: 'auto', catalogo: { anuncios: [anCat('MLB7', 'active', 'mesmo_anuncio')],
  bloqueio: 'mesmo_anuncio', texto: null } } }), [])
assert.deepEqual(cartao({ celula: { cell_status: 'auto', catalogo: { anuncios: [], bloqueio: 'sem_tipo', texto: null } } }), [])
assert.deepEqual(cartao({ celula: { cell_status: 'auto', catalogo: { anuncios: [anCat('MLB7', 'active', null)],
  bloqueio: 'sem_preco_catalogo', texto: null } } }), [])
// Kit que só tem anúncio de catálogo: o anúncio é da coluna Catálogo — nem b nem c,
// mesmo pausado; e o "fora do envio" da conferência também não conta.
assert.deepEqual(cartao({
  celula: { cell_status: 'auto', catalogo: { anuncios: [anCat('MLB8', 'paused', 'so_catalogo')], bloqueio: 'so_catalogo', texto: null } },
  conferida: conf('sem_anuncio', [], 'Este produto só tem anúncio de catálogo nesta conta — o preço vai pela coluna Catálogo (MLB8)'),
}), [])
assert.deepEqual(cartao({ conferida: conf('sem_anuncio', [],
  'Este produto só tem anúncio de catálogo nesta conta — o preço vai pela coluna Catálogo (MLB8)') }), [])

// c) Anúncio inativo — catálogo: pelo bloqueio do anúncio (ou o status gravado).
assert.deepEqual(cartao({ celula: { cell_status: 'auto', catalogo: { anuncios: [anCat('MLB9', 'paused', 'pausado')],
  bloqueio: 'pausado', texto: 'Anúncio de catálogo MLB9 pausado — não envia' } } }), ['Anúncio de catálogo MLB9 pausado'])
assert.deepEqual(cartao({ celula: { cell_status: 'auto', catalogo: { anuncios: [anCat('MLB9', 'under_review', null), anCat('MLB5', 'active', null)],
  bloqueio: null, texto: 'Envia para MLB5' } } }), ['Anúncio de catálogo MLB9 em revisão'])
assert.deepEqual(cartao({ celula: { cell_status: 'auto', catalogo: { anuncios: [anCat('MLB9', 'closed', 'encerrado')],
  bloqueio: 'encerrado', texto: null } } }), ['Anúncio de catálogo MLB9 encerrado'])
// c) Kit (M-31, 24/11/2026): só anúncio À VENDA conta. Com algum à venda, o
// inativo/excluído/rascunho não acende nada; sem nenhum à venda, um motivo só
// ("Nenhum anúncio à venda nesta conta") e embaixo a situação de cada um.
const NENHUM_A_VENDA = 'Nenhum anúncio à venda nesta conta'
assert.equal(L.CARTAO_NENHUM_A_VENDA, NENHUM_A_VENDA)
const inativo = (anuncio, motivo = null, preco = 1000) => ({ ...a(preco, anuncio), ativo: false, motivo })
assert.deepEqual(cartao({ conferida: conf('conferida', [a(1000, 'MLB1'), inativo('MLB2')]) }), [])
assert.deepEqual(cartao({ conferida: conf('conferida', [inativo('MLB2')]) }),
  [NENHUM_A_VENDA, 'Anúncio MLB2: inativo no marketplace (pausado, em revisão ou fora do ar)'])
assert.deepEqual(cartao({ conferida: conf('conferida', [inativo('MLB3', 'anúncio encerrado — o envio pula', null)]) }),
  [NENHUM_A_VENDA, 'Anúncio MLB3: anúncio encerrado'])
assert.deepEqual(cartao({ conferida: conf('conferida', [{ ...a(null, '123'), ativo: null, motivo: 'anúncio excluído na Shopee' }]) }),
  [NENHUM_A_VENDA, 'Anúncio 123: anúncio excluído na Shopee'])
assert.deepEqual(cartao({ conferida: conf('conferida', [{ ...inativo('MLB4', 'removido por moderação — o envio pula', null), variacao: 'V1' }]) }),
  [NENHUM_A_VENDA, 'Anúncio MLB4 (variação V1): removido por moderação'])
// TikTok: a situação que o leitor grava (rascunho, em revisão, reprovado, pausado).
assert.deepEqual(cartao({ conferida: conf('conferida', [
  { ...inativo('T1', 'rascunho no TikTok', 1844.7), variacao: 'S1' },
  inativo('T2', 'em revisão no TikTok'), inativo('T3', 'reprovado no TikTok'),
]) }), [NENHUM_A_VENDA, 'Anúncio T1 (variação S1): rascunho no TikTok', 'Anúncio T2: em revisão no TikTok',
  'Anúncio T3: reprovado no TikTok'])
// Sem preço por outro motivo (não lido, promoção não suportada): está à venda.
assert.deepEqual(cartao({ conferida: conf('conferida', [{ ...a(null, 'T9'), motivo: 'em Oferta Relâmpago — o envio pula, não conferido' }]) }), [])
// Leitura sem anúncio (erro na conta, sem anúncio): o "nenhum à venda" não aparece.
assert.deepEqual(cartao({ conferida: conf('conferida', []) }), [])
assert.deepEqual(cartao({ conferida: conf('erro_conta', [{ ...a(null, 'X1'), ativo: null }], 'recusou (403)') }), [])
// "Fora do envio" da conferência que é anúncio parado conta; o resto não.
assert.deepEqual(cartao({ conferida: conf('sem_anuncio', [], 'MLB1 pausado — não envia') }), ['MLB1 pausado — não envia'])
// Muitos fora de venda: o motivo, os 3 primeiros e "e mais".
const muitos = cartao({ conferida: conf('conferida', ['A', 'B', 'C', 'D', 'E'].map((x) => inativo(x))) })
assert.equal(muitos.length, 5)
assert.equal(muitos[0], NENHUM_A_VENDA)
assert.equal(muitos[4], '… e mais 2 anúncio(s) fora de venda')
// Muitos fora de venda, mas um à venda: nada.
assert.deepEqual(cartao({ conferida: conf('conferida', [...['A', 'B', 'C', 'D'].map((x) => inativo(x)), a(1000, 'OK')]) }), [])

// O (!) (M-31): só os anúncios à venda. Exemplos do pedido (conferidos em 24/11).
{
  const mgCel = L.margemDaResposta({ department: 'celular', margem_pct: 5, geral_ligada: true, por_produto_ligada: true, produtos: {} }, 'celular')
  const celConf = (anuncios) => ({ pricing_account_id: 'C1', pricing_product_id: 'P1', preco_tabela: null, situacao: 'conferida',
    motivo: null, conferido_em: '2026-11-24T12:00:00Z', job_id: 'J1', anuncios })
  const gradeCom = (preco) => () => ({ pricing_account_id: 'C1', pricing_product_id: 'P1', price: preco, source: 'calc', cell_status: 'auto' })
  const tudo = (tabela, anuncios) => {
    const c = celConf(anuncios)
    const divs = L.divergenciasDaConferencia([c], mgCel, gradeCom(tabela))
    const d = divs.get('P1::C1') ?? null
    return { d, cartao: cartao({ conferida: c, divergencia: d }) }
  }
  // A) uranyx wp53 24.128, mini TikTok kit 1, Tabela 1462: à venda a 1462 e o
  // rascunho lido a 1.844,70 → sem (!) e sem cartão.
  const A = tudo(1462, [
    { anuncio: '1735492791114106582', variacao: 'dg023.ra', preco: 1462, preco_promocional: null, ativo: true, motivo: null },
    { anuncio: '1738102761217623766', variacao: 'dg023.ra', preco: 1844.7, preco_promocional: null, ativo: false, motivo: 'rascunho no TikTok' },
  ])
  assert.equal(A.d, null)
  assert.deepEqual(A.cartao, [])
  // B) uranyx F105 12.64.64, barbosa Shopee kit 1, Tabela 1089: dois à venda a
  // 1089 e um excluído na Shopee → sem cartão.
  const B = tudo(1089, [a(1089, '1'), a(1089, '2'),
    { ...a(null, '58255874237'), ativo: false, motivo: 'anúncio excluído na Shopee' }])
  assert.equal(B.d, null)
  assert.deepEqual(B.cartao, [])
  // C) Todos inativos ou excluídos: o cartão continua, sem (!) (mesmo com o
  // preço lido fora da margem).
  const C = tudo(1000, [inativo('X1', 'pausado no TikTok', 1500), { ...a(null, 'X2'), ativo: false, motivo: 'produto excluído no TikTok' }])
  assert.equal(C.d, null)
  assert.deepEqual(C.cartao, [NENHUM_A_VENDA, 'Anúncio X1: pausado no TikTok', 'Anúncio X2: produto excluído no TikTok'])
  // D) À venda fora da margem: o (!) e o cartão continuam; o fora de venda não
  // entra na conta dos anúncios do balão.
  const D = tudo(1600, [a(1743, 'W1'), inativo('W2', 'rascunho no TikTok', 1600), { ...a(null, 'W3'), ativo: true }])
  assert.deepEqual(D.d.fora.map((x) => x.anuncio), ['W1'])
  assert.deepEqual([D.d.anuncios, D.d.naoLidos], [2, 1])
  assert.equal(D.cartao.length, 1)
  assert.match(D.cartao[0], /^Preço do anúncio fora da margem de 5%: anúncio 1743 × Tabela 1600/)
  // À venda: ativo vazio conta; ativo false ou sem preço por exclusão, não.
  assert.equal(L.anuncioAVenda({ ...a(100), ativo: null }), true)
  assert.equal(L.anuncioAVenda({ ...a(100), ativo: false }), false)
  assert.equal(L.anuncioAVenda({ ...a(null), ativo: null, motivo: 'anúncio encerrado — o envio pula' }), false)
  assert.equal(L.anuncioAVenda({ ...a(null), ativo: null, motivo: 'anúncio não encontrado' }), true)
}

// d) O último envio deu erro.
assert.deepEqual(cartao({ celula: { cell_status: 'error', catalogo: null } }), [ERRO_ENVIO])

// e) Fora da margem da linha (exemplo do pedido: dg101.pi Amazon, Tabela 1139 × anúncio 1031).
const div = { tabela: 1139, fora: [a(1031, 'B0X')], conferidoEm: '2026-11-17T12:00:00Z', naoLidos: 0, anuncios: 1, margem: 5 }
assert.deepEqual(cartao({ divergencia: div }),
  ['Preço do anúncio fora da margem de 5%: anúncio 1031 × Tabela 1139 (-9,5%)'])
assert.match(cartao({ divergencia: { ...div, margem: 12.5, fora: [a(1031, 'B0X'), a(1300, 'B0Y')] } })[0],
  /^Preço do anúncio fora da margem de 12,5%: .* \(e mais 1\)$/)

// Vários motivos juntos, na ordem a–e (o balão junta com quebra de linha). O
// inativo ao lado de um à venda não entra (M-31).
assert.deepEqual(cartao({
  celula: { cell_status: 'error', catalogo: null },
  conferida: conf('conferida', [a(1031, 'B0X'), inativo('B0Z')]),
  divergencia: div,
}).map((m) => m.slice(0, 22)), [ERRO_ENVIO.slice(0, 22), 'Preço do anúncio fora '])
assert.deepEqual(cartao({
  celula: { cell_status: 'error', catalogo: null },
  conferida: conf('conferida', [inativo('B0Z')]),
}).map((m) => m.slice(0, 22)), [NENHUM_A_VENDA.slice(0, 22), 'Anúncio B0Z: inativo n', ERRO_ENVIO.slice(0, 22)])
console.log('pricing conferencia cartão ok')

// O template: o cartão (à esquerda do aviãozinho, espaço sempre reservado) e
// o (!) continuam; o "?", o disquete, o ⊘, NA/SV e o cadeado saíram.
const tpl = descriptor.template.content
const iCelula = tpl.indexOf('<!-- Account cells')
assert.ok(iCelula > 0, 'célula de preço no template')
const celulaTpl = tpl.slice(iCelula, tpl.indexOf('</tbody>', iCelula))
assert.ok(celulaTpl.includes('data-cartao'), 'cartão na célula')
assert.ok(celulaTpl.indexOf('data-cartao') < celulaTpl.indexOf("'Push esta célula'"), 'cartão à esquerda do aviãozinho')
assert.match(celulaTpl, /<span v-else class="invisible block h-\[10px\] w-\[7px\]"/, 'espaço do cartão reservado')
assert.match(celulaTpl, /data-divergencia="1"/)
for (const fora of ['data-nao-conferida', 'Editar override', 'Desabilitar', "'NA'", "'SV'", '<Lock', 'cellTone']) {
  assert.ok(!celulaTpl.includes(fora), `${fora} saiu da célula`)
}
console.log('pricing conferencia template ok')

// ---------- M-15 (18/11/2026): clicar no cartão confere só aquela célula
{
  const celC = (p, acc, preco) => ({
    pricing_product_id: p, pricing_account_id: acc, preco_tabela: 100, situacao: 'conferida',
    motivo: null, conferido_em: '2026-11-18T12:00:00Z', anuncios: [a(preco)], job_id: null,
  })
  const precos = (lista) => lista.map((c) => [c.pricing_product_id, c.anuncios[0].preco])

  // Troca (ou tira) só a célula pedida, sem mexer na lista de entrada.
  const trocarCelula = new Function(`${pegar('trocarCelula')}\nreturn trocarCelula`)()
  const lista = [celC('P1', 'C1', 100), celC('P2', 'C1', 100)]
  assert.deepEqual(precos(trocarCelula(lista, 'P1::C1', celC('P1', 'C1', 130))), [['P2', 100], ['P1', 130]])
  assert.deepEqual(precos(trocarCelula(lista, 'P1::C1', null)), [['P2', 100]])
  assert.equal(lista.length, 2)

  // Leitura inteira que saiu antes do resultado da célula não volta a célula
  // para o dado mais antigo; a que saiu depois vale como veio do servidor.
  const recentes = new Map([
    ['P1::C1', { seq: 3, celula: celC('P1', 'C1', 130) }],
    ['P2::C1', { seq: 1, celula: null }],
  ])
  const comCelulasRecentes = new Function('trocarCelula', 'celulasRecentes',
    `${pegar('comCelulasRecentes')}\nreturn comCelulasRecentes`)(trocarCelula, recentes)
  assert.deepEqual(precos(comCelulasRecentes([celC('P1', 'C1', 100), celC('P2', 'C1', 100)], 2)),
    [['P2', 100], ['P1', 130]])
  assert.deepEqual([...recentes.keys()], ['P1::C1'])
  assert.deepEqual(precos(comCelulasRecentes([celC('P1', 'C1', 150)], 3)), [['P1', 150]])
  assert.equal(recentes.size, 0)

  // O balão: o motivo e, embaixo, o convite; o erro da leitura aparece também.
  const tituloDoCartao = new Function(`${pegar('tituloDoCartao')}\nreturn tituloDoCartao`)()
  assert.equal(tituloDoCartao('Sem anúncio vinculado nesta conta', true, null),
    'Sem anúncio vinculado nesta conta\nClique para conferir só esta célula')
  assert.equal(tituloDoCartao(SEM_INTEGRACAO, false, null), SEM_INTEGRACAO)
  assert.equal(tituloDoCartao('M', true, 'recusou (403)'),
    'M\nA conferência desta célula falhou: recusou (403)\nClique para conferir só esta célula')

  // Quem pode conferir e conta com integração: clicável. Sem permissão, conta
  // sem integração (ou catálogo de conta de kit sem integração): só o motivo.
  const conferivel = ({ pode = true, integ = 'I1', catalogo = null } = {}) => new Function(
    'conferencia', 'contaParametros', 'cellOf', 'tipoSemAnuncio',
    [pegar('celulaConferivel'), 'return celulaConferivel'].join('\n'),
  )({ value: { pode_conferir: pode } }, () => ({ integration_id: integ }), () => ({ cell_status: 'auto', catalogo }),
    L.tipoSemAnuncio)('P1', { id: 'C1' })
  assert.equal(conferivel(), true)
  assert.equal(conferivel({ pode: false }), false)
  assert.equal(conferivel({ integ: null }), false)
  assert.equal(conferivel({ catalogo: catSemInteg.catalogo }), false)
  assert.equal(conferivel({ catalogo: catSemAnuncio.catalogo }), true)

  // O clique, com a API simulada.
  const corpoClique = [
    pegarConst('ERROS_CELULA'),
    ...['textoErroCelula', 'trocarCelula', 'trocarErroDaCelula', 'conferirCelula'].map(pegar),
    'return conferirCelula',
  ].join('\n')
  const montarClique = (respostas, { podeLer = true } = {}) => {
    const env = {
      celulasConferindo: { value: new Set() },
      errosDaCelula: { value: {} },
      department: { value: 'celular' },
      desmontado: false,
      seqCelula: 0,
      celulasRecentes: new Map(),
      conferencia: { value: { celulas: [celC('P1', 'C1', 100), celC('P2', 'C1', 100)], pode_conferir: true } },
      celulaConferivel: () => podeLer,
      api: async (url, opcoes) => {
        chamadas.push([url, opcoes.method, opcoes.body])
        const r = respostas.shift()
        if (r instanceof Error) throw r
        return typeof r === 'function' ? r() : r
      },
    }
    const chamadas = []
    const nomes = Object.keys(env)
    const clicar = new Function(...nomes, corpoClique)(...nomes.map((n) => env[n]))
    return { clicar, env, chamadas }
  }
  const conta = { id: 'C1' }

  ;(async () => {
    // Dois cliques seguidos: uma leitura só; o indicador fica enquanto lê.
    let soltar
    const m = montarClique([() => new Promise((ok) => { soltar = ok })])
    const primeiro = m.clicar('P1', conta)
    assert.ok(m.env.celulasConferindo.value.has('P1::C1'))
    await m.clicar('P1', conta)
    assert.deepEqual(m.chamadas, [['/api/pricing/conferencia/celula', 'POST', { pricing_account_id: 'C1', pricing_product_id: 'P1' }]])
    soltar({ celula: celC('P1', 'C1', 130), erro: null })
    await primeiro
    assert.equal(m.env.celulasConferindo.value.size, 0)
    // Só a célula muda, sem reler a conferência inteira.
    assert.deepEqual(precos(m.env.conferencia.value.celulas), [['P2', 100], ['P1', 130]])
    assert.equal(m.env.celulasRecentes.get('P1::C1').seq, 1)

    // A leitura falhou: a célula fica como estava e o erro vai para o balão;
    // o próximo clique limpa o erro. Célula que não manda mais preço sai.
    const n = montarClique([{ celula: null, erro: 'recusou (403)' }, { celula: null, erro: null }])
    await n.clicar('P1', conta)
    assert.deepEqual(precos(n.env.conferencia.value.celulas), [['P1', 100], ['P2', 100]])
    assert.deepEqual(n.env.errosDaCelula.value, { 'P1::C1': 'recusou (403)' })
    assert.equal(n.env.celulasRecentes.size, 0)
    await n.clicar('P1', conta)
    assert.deepEqual(n.env.errosDaCelula.value, {})
    assert.deepEqual(precos(n.env.conferencia.value.celulas), [['P2', 100]])

    // Recusa do servidor: o código vira texto de gente.
    const o = montarClique([Object.assign(new Error('409'), { data: { detail: { code: 'so_no_servidor' } } })])
    await o.clicar('P2', conta)
    assert.deepEqual(o.env.errosDaCelula.value, { 'P2::C1': 'a conferência só roda no servidor' })
    assert.equal(o.env.celulasConferindo.value.size, 0)

    // Sem permissão ou conta sem integração: o clique não lê nada.
    const q = montarClique([], { podeLer: false })
    await q.clicar('P1', conta)
    assert.deepEqual([q.chamadas, q.env.celulasConferindo.value.size], [[], 0])
    console.log('pricing conferencia célula ok')
  })().catch((e) => { console.error(e); process.exit(1) })

  // O template: o indicador no lugar do cartão; o cartão clicável só para
  // quem pode conferir a célula; o outro só mostra o motivo.
  assert.ok(celulaTpl.indexOf('data-cartao-conferindo') < celulaTpl.indexOf('data-cartao-clicavel'))
  assert.match(celulaTpl, /v-if="conferindoCelula\(prod\.id, acc\.id\)"/)
  assert.match(celulaTpl, /v-else-if="cartaoDe\(prod\.id, acc\.id\) && celulaConferivel\(prod\.id, acc\)"/)
  assert.match(celulaTpl, /@click\.stop="conferirCelula\(prod\.id, acc\)"/)
  assert.match(celulaTpl, /cursor-pointer/)
  assert.match(celulaTpl, /cursor-help/)
}
