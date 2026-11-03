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
const node = source.statements.find((s) =>
  ts.isFunctionDeclaration(s) && s.name?.text === 'anunciosForaDaMargem')
assert.ok(node, 'anunciosForaDaMargem existe na página')
const out = ts.transpileModule(node.getText(source), {
  compilerOptions: { target: ts.ScriptTarget.ES2022 }, reportDiagnostics: true,
})
assert.deepEqual(out.diagnostics, [])
const fora = new Function(`${out.outputText}\nreturn anunciosForaDaMargem`)()

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
const pegar = (nome) => {
  const n = source.statements.find((s) => ts.isFunctionDeclaration(s) && s.name?.text === nome)
  assert.ok(n, `${nome} existe na página`)
  return ts.transpileModule(n.getText(source), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText
}
const fmtReal = (v) => `R$ ${v}`
const fmtQuando = () => '02/11, 10:00'
const fmtPct = (v) => v.toLocaleString('pt-BR', { maximumFractionDigits: 2 })
const montarTitulo = (margem) => new Function('fmtReal', 'fmtQuando', 'fmtPct', 'margemValida',
  `${pegar('tituloDivergencia')}\nreturn tituloDivergencia`)(fmtReal, fmtQuando, fmtPct, { value: margem })
const titulo = montarTitulo(5)
const d = {
  tabela: 1000,
  conferidoEm: '2026-11-02T13:00:00Z',
  naoLidos: 0,
  anuncios: 3,
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
// Margem com vírgula (era "7.5%") e o aviso dos anúncios da célula sem leitura.
const txt2 = montarTitulo(7.5)({ ...d, naoLidos: 2, anuncios: 5 })
assert.match(txt2, /^Fora da margem de 7,5%: Tabela R\$ 1000/)
assert.match(txt2, /^2 de 5 anúncios desta célula sem preço lido$/m)

// Códigos de erro do job viram texto de gente; código novo sai cru.
const erro = new Function('ERROS_CONFERENCIA', `${pegar('textoErroConferencia')}\nreturn textoErroConferencia`)({ interrompida: 'o servidor reiniciou no meio' })
assert.equal(erro('interrompida'), 'o servidor reiniciou no meio')
// Código desconhecido (texto de exceção do servidor) não aparece cru.
assert.equal(erro('TimeoutError'), 'erro inesperado no servidor — confira de novo')
assert.equal(erro(null), '')
console.log('pricing conferencia balão ok')

// Célula não conferida (? cinza): em branco só a conferida por inteiro. As
// constantes de texto vêm da própria página.
const pegarConst = (nome) => {
  const n = source.statements.find((s) => ts.isVariableStatement(s)
    && s.declarationList.declarations.some((d) => d.name.getText(source) === nome))
  assert.ok(n, `${nome} existe na página`)
  return ts.transpileModule(n.getText(source), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText
}
const regra = new Function('fmtQuando', [
  ...['MOTIVO_SEM_INTEGRACAO', 'MOTIVO_SEM_VINCULO', 'PREFIXOS_SEM_INTEGRACAO', 'PREFIXOS_SEM_VINCULO',
    'PREFIXOS_CATALOGO_NAO_LIDO', 'MOTIVO_SEM_LINHA', 'MOTIVO_LEITURA_ANTIGA'].map(pegarConst),
  ...['tipoSemAnuncio', 'anunciosNaoLidos', 'motivoDosNaoLidos', 'naoConferidaDaCelula',
    'classificarNaoConferidas', 'contarConferencia'].map(pegar),
  'return { naoConferidaDaCelula, classificarNaoConferidas, contarConferencia }',
].join('\n'))((iso) => `quando(${iso})`)
const naoConferida = regra.naoConferidaDaCelula
// J2 = a última conferência; J1 = uma antes dela.
const terminou = { situacao: 'terminou', atual: 'J2', concluido: 'J2' }
const celula = (situacao, anuncios = [], motivo = null, job_id = 'J2', conferido_em = '2026-11-02T12:05:00Z') =>
  ({ situacao, motivo, anuncios, job_id, conferido_em })

// Sem linha na conferência: o motivo depende de como ela está.
assert.equal(naoConferida(undefined, false, terminou).tipo, 'fora_da_conferencia')
assert.match(naoConferida(undefined, false, terminou).motivo, /conta ou preço novo/)
assert.match(naoConferida(undefined, false, { situacao: 'nunca', atual: null, concluido: null }).motivo, /ainda não conferido/)
assert.match(naoConferida(undefined, false, { situacao: 'rodando', atual: 'J3', concluido: 'J2' }).motivo, /em andamento/)
assert.match(naoConferida(undefined, false, { situacao: 'parou', atual: 'J3', concluido: 'J2' }).motivo, /parou no meio/)
assert.match(naoConferida(undefined, false, { situacao: 'sem_dados', atual: null, concluido: null }).motivo, /não carregou/)
// Sem anúncio: integração, vínculo, marca de catálogo não lida ou o envio não manda (texto do servidor no balão).
assert.equal(naoConferida(celula('sem_anuncio', [], 'conta sem integração'), false, terminou).tipo, 'sem_integracao')
assert.equal(naoConferida(celula('sem_anuncio', [],
  'Conta de kit sem integração do Mercado Livre — sem anúncio de catálogo'), false, terminou).tipo, 'sem_integracao')
assert.deepEqual(naoConferida(celula('sem_anuncio', [], 'sem anúncio vinculado'), false, terminou),
  { tipo: 'sem_anuncio', motivo: 'sem anúncio vinculado' })
assert.equal(naoConferida(celula('sem_anuncio', [], 'Sem anúncio de catálogo vinculado nesta conta'), false, terminou).tipo,
  'sem_anuncio')
assert.equal(naoConferida(celula('sem_anuncio', [],
  'Marca de catálogo ainda não lida nesta conta (a varredura diária lê às 10h; …)'), false, terminou).tipo, 'catalogo_nao_lido')
assert.deepEqual(naoConferida(celula('sem_anuncio', [], 'MLB1 pausado — não envia'), false, terminou),
  { tipo: 'fora_do_envio', motivo: 'MLB1 pausado — não envia' })
assert.equal(naoConferida(celula('nao_suportada', [], 'sem leitor de preço para a plataforma magalu'), false, terminou).tipo,
  'sem_leitor')
assert.equal(naoConferida(celula('erro_conta', [], 'token'), false, terminou).motivo, 'erro na conta: token')
// Lida por inteiro e dentro da margem: em branco.
assert.equal(naoConferida(celula('conferida', [a(1000), a(990, 'MLB2')]), false, terminou), null)
// Nenhum anúncio lido.
assert.deepEqual(naoConferida(celula('conferida', [{ ...a(null), motivo: 'anúncio não encontrado' }]), false, terminou),
  { tipo: 'nao_lido', motivo: 'anúncio não encontrado' })
assert.equal(naoConferida(celula('conferida', []), false, terminou).motivo, 'nenhum anúncio lido')
// Parcial sem (!): ? com quantos foram lidos; com (!): o (!) fala por ela.
assert.match(naoConferida(celula('conferida', [a(1000), { ...a(null, 'MLB2'), motivo: 'sem preço' }]), false, terminou).motivo,
  /^1 de 2 anúncios lidos, dentro da margem; os outros: sem preço$/)
assert.equal(naoConferida(celula('conferida', [a(1500), a(null, 'MLB2')]), true, terminou), null)

// Que leitura vale: a do último job ou a do último que terminou bem.
const antiga = (estado) => naoConferida(celula('conferida', [a(1000)], null, 'J1'), false, estado)
// Terminou bem, mas esta conta não gravou: ficou com a leitura do J1.
assert.deepEqual(antiga(terminou),
  { tipo: 'desatualizada', motivo: 'lida em quando(2026-11-02T12:05:00Z), na última conferência esta conta não conseguiu gravar — confira de novo' })
// Parou no meio (J2), e o último que terminou bem é o J1: a leitura do J1 vale; a do J2 também.
const parou = { situacao: 'parou', atual: 'J2', concluido: 'J1' }
assert.equal(antiga(parou), null)
assert.equal(naoConferida(celula('conferida', [a(1000)], null, 'J2'), false, parou), null)
// Leitura de um job que parou (J1), sem nenhum concluído depois: ? — e o (!) antigo continua.
assert.match(antiga({ situacao: 'parou', atual: 'J2', concluido: 'J0' }).motivo, /numa conferência que não terminou — confira de novo$/)
assert.equal(naoConferida(celula('conferida', [a(1500)], null, 'J1'), true, { situacao: 'parou', atual: 'J2', concluido: 'J0' }), null)
// Conferindo de novo (J3) depois de uma que parou (J2): a leitura do J2 não volta a valer
// em branco; a do último concluído (J1) vale até ser trocada.
const rodando = { situacao: 'rodando', atual: 'J3', concluido: 'J1' }
assert.match(naoConferida(celula('conferida', [a(1000)], null, 'J2'), false, rodando).motivo, /a de agora ainda não chegou nela$/)
assert.equal(naoConferida(celula('conferida', [a(1000)], null, 'J1'), false, rodando), null)
assert.equal(naoConferida(celula('conferida', [a(1000)], null, 'J3'), false, rodando), null)
// Linha sem job: não vale.
assert.equal(naoConferida(celula('conferida', [a(1000)], null, null), false, terminou).tipo, 'desatualizada')
console.log('pricing conferencia não conferidas ok')

// A grade inteira: toda célula com preço cai em um balde só, e a contagem da
// faixa é a das marcas — inclusive a célula com preço que a conferência nem tem.
const cel = (p, c) => ({ pricing_product_id: p, pricing_account_id: c })
const grade = [cel('p1', 'c1'), cel('p1', 'c2'), cel('p1', 'c3'), cel('p2', 'c1'), cel('p2', 'c2'), cel('p2', 'c3')]
const semPreco = new Set(['p2::c3'])  // NA/SV/∅ ou sem preço: fora de tudo
const comparavel = (p, c) => !semPreco.has(`${p}::${c}`)
const porCelula = new Map([
  ['p1::c1', celula('conferida', [a(1000)])],  // em branco
  ['p1::c2', celula('conferida', [a(1300)])],  // (!)
  ['p1::c3', celula('sem_anuncio', [], 'conta sem integração')],  // ?
  ['p2::c1', celula('conferida', [a(1000), a(null, 'MLB9')])],  // ? parcial
  // p2::c2 sem linha: ? fora da conferência
])
const divergentes = new Map([['p1::c2', {}]])
const nc = regra.classificarNaoConferidas(grade, comparavel, porCelula, divergentes, terminou)
assert.deepEqual([...nc.keys()].sort(), ['p1::c3', 'p2::c1', 'p2::c2'])
assert.equal(nc.get('p2::c2').tipo, 'fora_da_conferencia')
const produtos = [{ id: 'p1' }, { id: 'p2' }]
const contas = [{ id: 'c1' }, { id: 'c2' }, { id: 'c3' }]
const n = regra.contarConferencia(produtos, contas, comparavel, divergentes, nc)
assert.deepEqual({ fora: n.fora, conferidas: n.conferidas, qtdNaoConferidas: n.qtdNaoConferidas }, { fora: 1, conferidas: 1, qtdNaoConferidas: 3 })
assert.equal(n.fora + n.conferidas + n.qtdNaoConferidas, grade.filter((c) => comparavel(c.pricing_product_id, c.pricing_account_id)).length)
assert.deepEqual([...n.porTipo.entries()].sort(), [['fora_da_conferencia', 1], ['nao_lido', 1], ['sem_integracao', 1]])
// Só o que está na tela: uma linha e uma coluna a menos.
const n2 = regra.contarConferencia([{ id: 'p1' }], [{ id: 'c1' }, { id: 'c3' }], comparavel, divergentes, nc)
assert.deepEqual({ fora: n2.fora, conferidas: n2.conferidas, qtdNaoConferidas: n2.qtdNaoConferidas }, { fora: 0, conferidas: 1, qtdNaoConferidas: 1 })
// Antes da primeira conferência: toda célula com preço ganha ?.
const nunca = regra.classificarNaoConferidas(grade, comparavel, new Map(), new Map(), { situacao: 'nunca', atual: null, concluido: null })
assert.equal(nunca.size, 5)
console.log('pricing conferencia grade ok')
