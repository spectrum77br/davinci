// Run from apps/web: node tests/reposicao.cjs
// A conta da Reposição (utils/reposicao.ts), a mesma da Importação e da
// Tabela de Preços. Exemplo da tarefa: estoque 0, 100 por mês, 60 dias → −200.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')

const filename = path.resolve(__dirname, '../utils/reposicao.ts')
const out = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  reportDiagnostics: true,
})
assert.deepEqual(out.diagnostics, [])
const mod = { exports: {} }
new Function('module', 'exports', out.outputText)(mod, mod.exports)
const { calcularReposicao, explicarReposicao, prazoDias, reposicaoPorPeca, textoFormulaReposicao } = mod.exports

// Exemplo da tarefa.
assert.deepEqual(calcularReposicao(0, 100, 30, 30), { valor: -200, necessario: 200, dias: 60 })
// Estoque cobre: sobra positiva.
assert.equal(calcularReposicao(250, 100, 30, 30).valor, 50)
// Exato: zero.
assert.equal(calcularReposicao(200, 100, 30, 30).valor, 0)
// Fração arredonda o necessário para cima (7 × 45 ÷ 30 = 10,5 → 11).
assert.deepEqual(calcularReposicao(0, 7, 15, 30), { valor: -11, necessario: 11, dias: 45 })
// Mala (120 + 30): 1 venda no mês → 5 para 150 dias.
assert.equal(calcularReposicao(0, 1, 120, 30).valor, -5)
// Sem venda: Reposição = o próprio estoque (positivo, ou o negativo do Bling).
assert.equal(calcularReposicao(103, 0, 30, 30).valor, 103)
assert.equal(calcularReposicao(-3, 0, 30, 30).valor, -3)
// Venda nula/indefinida conta como zero; venda negativa não reduz o necessário.
assert.equal(calcularReposicao(10, null, 30, 30).valor, 10)
assert.equal(calcularReposicao(10, -5, 30, 30).valor, 10)
// Sem estoque lido ou sem prazo: sem resultado (a tela mostra —).
assert.equal(calcularReposicao(null, 10, 30, 30), null)
assert.equal(calcularReposicao(undefined, 10, 30, 30), null)
assert.equal(calcularReposicao(5, 10, null, 30), null)
assert.equal(calcularReposicao(5, 10, 30, undefined), null)
// Prazo zero: nada necessário.
assert.equal(calcularReposicao(5, 10, 0, 0).valor, 5)
// Texto do balão.
assert.equal(
  explicarReposicao(calcularReposicao(0, 100, 30, 30), 0, 100),
  '100 vendas em 30 dias × 60 dias ÷ 30 = 200 necessárias; estoque 0 → faltam 200 (comprar 200).',
)
assert.match(explicarReposicao(calcularReposicao(250, 100, 30, 30), 250, 100), /sobram 50\.$/)
assert.match(explicarReposicao(calcularReposicao(200, 100, 30, 30), 200, 100), /nada a comprar\.$/)
assert.equal(explicarReposicao(null, null, 3), 'Sem estoque lido ou sem prazos configurados.')
console.log('reposicao ok')
// Singular no balão.
assert.equal(
  explicarReposicao(calcularReposicao(0, 1, 15, 15), 0, 1),
  '1 venda em 30 dias × 30 dias ÷ 30 = 1 necessária; estoque 0 → faltam 1 (comprar 1).',
)
console.log('reposicao singular ok')

// Prazo apagado ('' do v-model.number) não é zero dia: sem resultado.
assert.equal(prazoDias(''), null)
assert.equal(prazoDias(null), null)
assert.equal(prazoDias('30'), 30)
assert.equal(prazoDias(-1), null)
assert.equal(calcularReposicao(10, 10, '', 30), null)
assert.equal(calcularReposicao(10, 10, 30, ''), null)
assert.equal(calcularReposicao(0, 100, '30', '30').valor, -200)

// Peça a peça: a sobra de uma cor não cobre a falta de outra.
const linha = reposicaoPorPeca({ dg002: 245, dg003: 0 }, { dg002: 10, dg003: 30 }, 30, 30)
assert.deepEqual(linha.faltando.map((p) => [p.peca, p.r.valor]), [['dg003', -60]])
assert.equal(linha.valor, -60)            // soma só das faltas (o total da linha daria +165)
assert.equal(linha.pecas.length, 2)
// Nenhuma falta: soma das sobras.
assert.equal(reposicaoPorPeca({ a: 10, b: 5 }, { a: 3, b: 0 }, 15, 15).valor, 12)
// Peça só com venda (sem estoque lido) conta estoque 0; só com estoque, venda 0.
assert.equal(reposicaoPorPeca({}, { x: 15 }, 15, 15).valor, -15)
assert.equal(reposicaoPorPeca({ y: 7 }, {}, 15, 15).valor, 7)
// Sem peças: zero. Sem prazo: null.
assert.equal(reposicaoPorPeca({}, {}, 15, 15).valor, 0)
assert.equal(reposicaoPorPeca({ a: 1 }, { a: 1 }, '', 15), null)
// Texto da conta igual nas duas telas.
assert.equal(textoFormulaReposicao(30, 30), 'Reposição = Estoque − vendas 30d × (30 + 30) ÷ 30; a parte das vendas arredonda para cima')
assert.equal(textoFormulaReposicao('', 30), 'Reposição = Estoque − vendas 30d × (? + 30) ÷ 30; a parte das vendas arredonda para cima')
console.log('reposicao por peca ok')
