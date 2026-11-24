// Run from apps/web: node tests/pricing-cartao-compartilhado.cjs
// M-22 (24/11/2026): a regra do cartão vermelho saiu de pages/pricing/[tab].vue
// para lib/cartaoVermelho.ts, para a coluna Pendências do Faturamento contar
// "pela MESMA regra do cartão da grade". Este teste prova que a grade não mudou:
// compara, em centenas de grades sorteadas (semente fixa), o resultado de antes
// (tests/cartao-vermelho-antes-m22.js, a cópia congelada do código da página
// antes da M-22) com
//   1) os computeds `divergencias` e `cartoes` da página DE AGORA (o código
//      real, extraído do SFC), que chamam o módulo;
//   2) `cartoesPorConta` do módulo (o que o Faturamento usa): a conta de cada
//      cartão da grade, somada.
// Inclui o "sem anúncio vinculado" vencido (aplicarSemVinculo da grade aberta).
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const { parse } = require('vue/compiler-sfc')

const js = (texto, cjs = false) => {
  const out = ts.transpileModule(texto, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, ...(cjs ? { module: ts.ModuleKind.CommonJS } : {}) },
    reportDiagnostics: true,
  })
  assert.deepEqual(out.diagnostics, [])
  return out.outputText
}

// ---------------------------------------------------------------- o módulo
const L = {}
new Function('exports', 'require', js(fs.readFileSync(path.resolve(__dirname, '../lib/cartaoVermelho.ts'), 'utf8'), true))(
  L, () => { throw new Error('lib/cartaoVermelho.ts não importa nada') },
)

// ---------------------------------------------------------------- a página de agora
const filename = path.resolve(__dirname, '../pages/pricing/[tab].vue')
const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
assert.deepEqual(errors, [])
const setup = descriptor.scriptSetup.content
const source = ts.createSourceFile(filename, setup, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
const funcao = (nome) => {
  const n = source.statements.find((s) => ts.isFunctionDeclaration(s) && s.name?.text === nome)
  assert.ok(n, `${nome} existe na página`)
  return js(n.getText(source))
}
const corpoDoComputed = (nome) => {
  const st = source.statements.find((s) => ts.isVariableStatement(s)
    && s.declarationList.declarations.some((d) => d.name.getText(source) === nome))
  assert.ok(st, `${nome} existe na página`)
  const ini = st.declarationList.declarations[0].initializer
  assert.ok(ini && ts.isCallExpression(ini) && ini.expression.getText(source) === 'computed', `${nome} é computed`)
  return js(`const ${nome}__corpo = ${ini.arguments[0].getText(source)}`)
}
assert.match(setup, /import \{[^}]*\bcartoesDaGrade\b[^}]*\} from '~\/lib\/cartaoVermelho'/)
assert.match(setup, /import \{[^}]*\bdivergenciasDaConferencia\b[^}]*\} from '~\/lib\/cartaoVermelho'/)
for (const fora of ['motivosDoCartao', 'tipoSemAnuncio', 'margemDaLinha', 'anunciosForaDaMargem', 'margemDaResposta']) {
  assert.ok(!new RegExp(`\\nfunction ${fora}\\(`).test(setup), `${fora}: sem cópia na página`)
}

const montarAgora = new Function(
  'grid', 'conferencia', 'margem', 'department', 'gradePronta', 'accounts', 'L',
  [
    'const { cartoesDaGrade, celulaComparavel, divergenciasDaConferencia } = L',
    funcao('ehCatalogo'), funcao('contaBase'), funcao('contaParametros'), funcao('celulaComparavelDaGrade'),
    corpoDoComputed('cellMap'), corpoDoComputed('conferenciaPorCelula'), corpoDoComputed('contasPorId'),
    corpoDoComputed('divergencias'), corpoDoComputed('cartoes'),
    'const cellMap = { value: cellMap__corpo() }',
    'function cellOf(p, a) { return cellMap.value.get(`${p}::${a}`) }',
    'const conferenciaPorCelula = { value: conferenciaPorCelula__corpo() }',
    'const contasPorId = { value: contasPorId__corpo() }',
    'const divergencias = { value: divergencias__corpo() }',
    'return { divergencias: divergencias.value, cartoes: cartoes__corpo() }',
  ].join('\n'),
)

// ---------------------------------------------------------------- o de antes (congelado)
const ANTES = fs.readFileSync(path.resolve(__dirname, 'cartao-vermelho-antes-m22.js'), 'utf8')
const fmtPctAntes = (v) => v.toLocaleString('pt-BR', { maximumFractionDigits: 2 })
const montarAntes = new Function(
  'grid', 'conferencia', 'margem', 'department', 'gradePronta', 'accounts', 'gridDepartamento', 'fmtPct',
  [
    ANTES,
    'const pushStates = { value: new Map() }',
    'function pushCellKey(p, a) { return `${p}:${a}` }',
    'function cellOf(p, a) { return cellMap__corpo().get(`${p}::${a}`) }',
    // A grade aberta recebe a conferência: tira o "sem vínculo" vencido e
    // pega a margem (carregarConferencia).
    'aplicarSemVinculo(conferencia.value?.sem_vinculo, department.value)',
    'margem.value = conferencia.value ? margemDaResposta(conferencia.value.margem, department.value) : null',
    'const cellMap = { value: cellMap__corpo() }',
    'const conferenciaPorCelula = { value: conferenciaPorCelula__corpo() }',
    'const contasPorId = { value: contasPorId__corpo() }',
    'const divergencias = { value: divergencias__corpo() }',
    'return { divergencias: divergencias.value, cartoes: cartoes__corpo(), grade: grid.value }',
  ].join('\n'),
)

// ---------------------------------------------------------------- grades sorteadas
let semente = 20261124
const sorte = () => {
  semente = (semente * 1103515245 + 12345) % 2147483648
  return semente / 2147483648
}
const um = (lista) => lista[Math.floor(sorte() * lista.length)]
const talvez = (p = 0.5) => sorte() < p

const STATUS_CELULA = ['auto', 'auto', 'auto', 'manual', 'locked', 'disabled', 'NA', 'SV', 'error', 'no_link', 'no_link']
const BLOQUEIOS_CAT = [null, null, 'sem_anuncio', 'sem_anuncio', 'mesmo_anuncio', 'sincronizado', 'pausado', 'em_revisao',
  'encerrado', 'sem_tipo', 'sem_preco_catalogo', 'so_catalogo']
const TEXTOS_CAT = [null, 'conta sem integração', 'Conta de kit sem integração do Mercado Livre — sem anúncio de catálogo',
  'Sem anúncio de catálogo vinculado nesta conta', 'sem anúncio vinculado',
  'Marca de catálogo ainda não lida nesta conta (a varredura diária lê às 10h; …)',
  'Anúncio de catálogo MLB9 pausado — não envia', 'Envia para MLB5', 'outro texto']
const STATUS_AN_CAT = [null, 'active', 'active', 'paused', 'inactive', 'under_review', 'closed', 'PAUSED']
const BLOQUEIOS_AN_CAT = [null, null, 'pausado', 'em_revisao', 'encerrado', 'so_catalogo', 'sincronizado', 'mesmo_anuncio']
const SITUACOES = ['conferida', 'conferida', 'conferida', 'sem_anuncio', 'erro_conta', 'nao_suportada']
const MOTIVOS_CONF = [null, 'sem anúncio vinculado', 'conta sem integração', 'MLB1 pausado — não envia',
  'Anúncio em revisão — não envia', 'Este produto só tem anúncio de catálogo nesta conta — o preço vai pela coluna Catálogo (MLB8)',
  'Sem anúncio de catálogo vinculado nesta conta', 'sincronizado com MLB2']
const MOTIVOS_AN = [null, null, null, 'em promoção', 'anúncio encerrado — o envio pula', 'anúncio excluído na Shopee',
  'removido por moderação — o envio pula', 'em Oferta Relâmpago — o envio pula, não conferido', 'sem promoção ativa']

function sortearCenario(n) {
  const dep = um(['celular', 'eletro', 'mala'])
  const nContas = 1 + Math.floor(sorte() * 6)
  const accounts = []
  for (let i = 0; i < nContas; i++) {
    const base = { id: `K${n}-${i}`, name: `conta${i}`, canal: 'kit', conta_base_id: null,
      integration_id: talvez(0.75) ? `I${i}` : null }
    accounts.push(base)
    if (talvez(0.35)) accounts.push({ id: `C${n}-${i}`, name: `conta${i}`, canal: 'catalogo', conta_base_id: base.id, integration_id: null })
  }
  const products = Array.from({ length: 1 + Math.floor(sorte() * 8) }, (_, i) => ({ id: `P${n}-${i}` }))
  const cells = []
  const celulas = []
  for (const acc of accounts) {
    for (const prod of products) {
      if (talvez(0.9)) {
        const preco = talvez(0.85) ? Math.round(100 + sorte() * 900) : null
        const cell = {
          pricing_account_id: acc.id, pricing_product_id: prod.id,
          price: preco == null ? null : (talvez() ? String(preco) : preco),
          source: um(['calc', 'calc', 'calc', 'disabled', 'override']),
          cell_status: um(STATUS_CELULA), has_override: false, cell_color: null,
        }
        if (acc.canal === 'catalogo' || talvez(0.25)) {
          cell.catalogo = talvez(0.15) ? null : {
            bloqueio: um(BLOQUEIOS_CAT),
            texto: um(TEXTOS_CAT),
            anuncios: Array.from({ length: Math.floor(sorte() * 4) }, (_, k) => ({
              external_id: `MLB${k}`, status: um(STATUS_AN_CAT), listing_type: 'gold_pro', sincronizado_com: [],
              bloqueio: um(BLOQUEIOS_AN_CAT),
            })),
          }
        }
        cells.push(cell)
        if (talvez(0.7)) {
          const base = preco ?? 500
          celulas.push({
            pricing_account_id: acc.id, pricing_product_id: prod.id, preco_tabela: preco,
            situacao: um(SITUACOES), motivo: um(MOTIVOS_CONF), conferido_em: '2026-11-24T12:00:00Z', job_id: 'J1',
            anuncios: Array.from({ length: Math.floor(sorte() * 6) }, (_, k) => ({
              anuncio: `A${k}`, variacao: talvez(0.2) ? `V${k}` : null,
              preco: talvez(0.8) ? Math.round(base * (0.85 + sorte() * 0.3) * 100) / 100 : null,
              preco_promocional: talvez(0.1) ? 99 : null,
              ativo: um([true, true, true, false, null]),
              motivo: um(MOTIVOS_AN),
            })),
          })
        }
      }
    }
  }
  const produtosMargem = {}
  for (const prod of products) if (talvez(0.3)) produtosMargem[prod.id] = um([0, 3, '7.50', 12, null, 'abc'])
  const margem = talvez(0.1) ? null : {
    department: dep,
    margem_pct: um([5, '5.00', 2, 10, null, 0]),
    geral_ligada: um([true, true, false, undefined]),
    por_produto_ligada: um([true, false, undefined]),
    produtos: produtosMargem,
    pode_editar: talvez(),
  }
  const noLink = cells.filter((c) => c.cell_status === 'no_link')
  const sem_vinculo = talvez(0.25) ? null
    : noLink.filter(() => talvez(0.6)).map((c) => ({ pricing_account_id: c.pricing_account_id, pricing_product_id: c.pricing_product_id }))
  const conferencia = talvez(0.08) ? null : { celulas, margem, sem_vinculo }
  return { dep, grid: { accounts, products, cells }, conferencia }
}

const ref = (v) => ({ value: v })
const clone = (x) => (x == null ? x : structuredClone(x))
const contarPorConta = (cartoes, contas) => {
  const m = new Map(contas.map((a) => [a.id, 0]))
  for (const k of cartoes.keys()) {
    const conta = k.split('::')[1]
    m.set(conta, m.get(conta) + 1)
  }
  return m
}

let comCartao = 0
let semVinculoTirado = 0
for (let n = 0; n < 600; n++) {
  const c = sortearCenario(n)
  const antes = montarAntes(ref(clone(c.grid)), ref(clone(c.conferencia)), ref(null), ref(c.dep), ref(true), ref([]),
    ref(c.dep), fmtPctAntes)
  // A grade de agora, no mesmo estado (o aplicarSemVinculo da página não mudou).
  const gradeAgora = clone(antes.grade)
  const margemAgora = c.conferencia ? L.margemDaResposta(c.conferencia.margem, c.dep) : null
  const agora = montarAgora(ref(gradeAgora), ref(clone(c.conferencia)), ref(margemAgora), ref(c.dep), ref(true), ref([]), L)
  assert.deepEqual([...agora.divergencias], [...antes.divergencias], `cenário ${n}: (!) igual`)
  assert.deepEqual([...agora.cartoes], [...antes.cartoes], `cenário ${n}: cartões iguais (mesmos motivos)`)
  // O Faturamento: a grade como veio do /grid e a conferência, sem a página.
  const porConta = L.cartoesPorConta(clone(c.grid), clone(c.conferencia), c.dep)
  assert.deepEqual([...porConta], [...contarPorConta(antes.cartoes, c.grid.accounts)], `cenário ${n}: Pendências = cartões da grade`)
  comCartao += antes.cartoes.size
  semVinculoTirado += c.grid.cells.filter((x) => x.cell_status === 'no_link').length
    - antes.grade.cells.filter((x) => x.cell_status === 'no_link').length
}
assert.ok(comCartao > 1000, `os sorteios têm cartões de verdade (${comCartao})`)
assert.ok(semVinculoTirado > 20, 'e casos de "sem vínculo" vencido')
console.log(`pricing-cartao-compartilhado: 600 grades sorteadas, ${comCartao} cartões — iguais aos de antes da M-22`)

// ---------------------------------------------------------------- casos à mão
{
  const conta = (id, extra = {}) => ({ id, name: id, canal: 'kit', conta_base_id: null, integration_id: 'I1', ...extra })
  const cel = (p, a, extra = {}) => ({ pricing_account_id: a, pricing_product_id: p, price: 100, source: 'calc', cell_status: 'auto', ...extra })
  const grade = {
    accounts: [conta('A'), conta('B', { integration_id: null }), conta('C'), { id: 'CC', canal: 'catalogo', conta_base_id: 'B', integration_id: null }],
    products: [{ id: 'P1' }, { id: 'P2' }],
    cells: [cel('P1', 'A'), cel('P2', 'A', { cell_status: 'error' }), cel('P1', 'C', { cell_status: 'no_link' }), cel('P2', 'C', { cell_status: 'no_link' })],
  }
  // Sem conferência: conta sem integração tem cartão em todas as células (até sem
  // célula); a coluna de catálogo herda da base sem integração; no_link conta.
  assert.deepEqual([...L.cartoesPorConta(grade, null, 'celular')], [['A', 1], ['B', 2], ['C', 2], ['CC', 2]])
  // O "sem vínculo" que o servidor não manda mais sai (só P2 continua marcado);
  // fora da margem conta (Tabela 100 × anúncio 120 > 5%).
  const conf = {
    celulas: [{ pricing_account_id: 'A', pricing_product_id: 'P1', preco_tabela: 100, situacao: 'conferida', motivo: null,
      conferido_em: '2026-11-24T12:00:00Z', job_id: null, anuncios: [{ anuncio: 'X', variacao: null, preco: 120, preco_promocional: null, ativo: true, motivo: null }] }],
    margem: { margem_pct: '5.00', geral_ligada: true, por_produto_ligada: true, produtos: {} },
    sem_vinculo: [{ pricing_account_id: 'C', pricing_product_id: 'P2' }],
  }
  assert.deepEqual([...L.cartoesPorConta(grade, conf, 'celular')], [['A', 2], ['B', 2], ['C', 1], ['CC', 2]])
  // A grade de entrada não é mexida (o Faturamento guarda a resposta).
  assert.equal(grade.cells[2].cell_status, 'no_link')
  // Margem desligada: sem o motivo "fora da margem".
  assert.deepEqual([...L.cartoesPorConta(grade, { ...conf, margem: { ...conf.margem, geral_ligada: false } }, 'celular')],
    [['A', 1], ['B', 2], ['C', 1], ['CC', 2]])
}

// ---------------------------------------------------------------- desempenho
// Uma categoria grande (900 linhas × 60 colunas, a ordem da Mala): uma passada só.
{
  const accounts = Array.from({ length: 60 }, (_, i) => ({ id: `A${i}`, canal: 'kit', conta_base_id: null, integration_id: i % 7 ? `I${i}` : null }))
  const products = Array.from({ length: 900 }, (_, i) => ({ id: `P${i}` }))
  const cells = []
  const celulas = []
  for (const a of accounts) {
    for (const p of products) {
      cells.push({ pricing_account_id: a.id, pricing_product_id: p.id, price: 100, source: 'calc', cell_status: 'auto' })
      celulas.push({ pricing_account_id: a.id, pricing_product_id: p.id, preco_tabela: 100, situacao: 'conferida', motivo: null,
        conferido_em: '2026-11-24T12:00:00Z', job_id: null, anuncios: [{ anuncio: 'X', variacao: null, preco: 104, preco_promocional: null, ativo: true, motivo: null }] })
    }
  }
  const t0 = Date.now()
  const r = L.cartoesPorConta({ accounts, products, cells }, { celulas, margem: {}, sem_vinculo: [] }, 'mala')
  const ms = Date.now() - t0
  assert.equal(r.get('A0'), 900)
  assert.equal(r.get('A1'), 0)
  assert.ok(ms < 3000, `54 mil células em ${ms} ms`)
  console.log(`pricing-cartao-compartilhado: 54 mil células em ${ms} ms`)
}
console.log('pricing-cartao-compartilhado: ok')
