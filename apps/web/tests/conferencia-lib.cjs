// Run from apps/web: node tests/conferencia-lib.cjs
//
// Conferência Shopee (06/10/2026): as regras de lib/conferencia.ts — formato
// pt-BR do dinheiro, do inteiro e do percentual, e a variação "▲ 12,3%" /
// "▼ 0,8 p.p." / "novo" / "=" / "—" com a cor pelo que é bom em cada métrica.
// São as MESMAS regras do servidor (Excel, CSV, MD, HTML, Threema; contrato §5):
// se este teste mudar, o do servidor muda junto.
// Só dados FALSOS aqui; nenhuma rede.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')

const arquivo = path.join(__dirname, '..', 'lib', 'conferencia.ts')
const fonte = fs.readFileSync(arquivo, 'utf8')
const js = ts.transpileModule(fonte, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText
const L = {}
new Function('exports', 'require', js)(L, () => { throw new Error('lib/conferencia.ts não pode importar nada') })

// ---------------------------------------------------------------- higiene
// lib/ não é varrido pelo Tailwind: classe escrita aqui sairia sem CSS.
assert.ok(!/['"`][^'"`]*\b(text|bg|border)-(emerald|red|amber|muted)/.test(fonte), 'nenhuma classe do Tailwind na lib')
assert.ok(!/^import /m.test(fonte), 'lib sem import (o teste roda ela solta)')

// ---------------------------------------------------------------- métricas (ordem e regra do contrato §5)
assert.deepEqual(
  L.METRICAS.map((m) => [m.chave, m.rotulo, m.tipo, m.bom]),
  [
    ['vendas_afiliados', 'Vendas afiliados', 'dinheiro', 'sobe'],
    ['vendas_ads', 'Vendas Ads', 'dinheiro', 'sobe'],
    ['saldo_ads', 'Saldo Ads', 'dinheiro', 'neutro'],
    ['impressoes', 'Impressões', 'inteiro', 'sobe'],
    ['invest_afiliados', 'Invest. afiliados', 'dinheiro', 'neutro'],
    ['invest_ads', 'Invest. Ads', 'dinheiro', 'neutro'],
    ['pct', '% s/ vendas', 'percentual', 'desce'],
    ['vendas', 'Vendas', 'dinheiro', 'sobe'],
    // 07/10/2026: cliques, pedidos e conversão — no fim, a ordem antiga não muda.
    ['cliques_afiliados', 'Cliques afiliados', 'inteiro', 'neutro'],
    ['pedidos_afiliados', 'Pedidos afiliados', 'inteiro', 'sobe'],
    ['conversao_afiliados', 'Conversão afiliados', 'percentual', 'sobe'],
    ['cliques_ads', 'Cliques Ads', 'inteiro', 'neutro'],
    ['pedidos_ads', 'Pedidos Ads', 'inteiro', 'sobe'],
    ['conversao_ads', 'Conversão Ads', 'percentual', 'sobe'],
  ],
)

// ---------------------------------------------------------------- formatos
assert.equal(L.dinheiro(1234.56), 'R$ 1.234,56', 'dinheiro pt-BR com espaço comum')
assert.equal(L.dinheiro(1234567.891), 'R$ 1.234.567,89')
assert.equal(L.dinheiro(0), 'R$ 0,00', 'zero medido aparece')
assert.equal(L.dinheiro(0.005), 'R$ 0,01')
assert.equal(L.dinheiro(-12), '-R$ 12,00')
assert.equal(L.dinheiro(null), '—', 'sem dados vira travessão')
assert.equal(L.dinheiro(undefined), '—')
assert.equal(L.dinheiro(Number.NaN), '—')
assert.equal(L.inteiro(72388), '72.388')
assert.equal(L.inteiro(0), '0')
assert.equal(L.inteiro(1234.6), '1.235')
assert.equal(L.inteiro(null), '—')
assert.equal(L.percentual(8.2), '8,2%')
assert.equal(L.percentual(8.24), '8,2%')
assert.equal(L.percentual(0), '0,0%')
assert.equal(L.percentual(1234.5), '1.234,5%')
assert.equal(L.percentual(null), '—')
// Arredondamento IGUAL ao do servidor (calculo.meio_para_cima): sobre o
// número como se escreve, meio pra longe do zero — como o Excel mostra a
// célula. Pelo binário exato com empate pro par (o format do Python) daria
// "6,3%" pro 6,35 e "6,2%" pro 6,25, e a tela discordaria da planilha.
assert.equal(L.percentual(6.35), '6,4%', '6,35 escrito → 6,4 (o binário 6.3499… não manda)')
assert.equal(L.percentual(6.25), '6,3%', 'meio vai pra longe do zero')
assert.equal(L.percentual(8.75), '8,8%')
assert.equal(L.percentual(0.25), '0,3%')
assert.equal(L.percentual(2.675), '2,7%')
assert.equal(L.percentual(9.96), '10,0%', '"vai um" passa pro inteiro')
assert.equal(L.percentual(999.95), '1.000,0%', '"vai um" cria um dígito novo')
assert.equal(L.percentual(-0.04), '-0,0%', 'sinal mesmo arredondando pra zero (igual ao Python)')
assert.equal(L.dinheiro(0.125), 'R$ 0,13', 'meio centavo sobe')
assert.equal(L.dinheiro(2.675), 'R$ 2,68', '2,675 escrito → 2,68')
assert.equal(L.dinheiro(1.005), 'R$ 1,01')
assert.equal(L.dinheiro(999999.995), 'R$ 1.000.000,00')
assert.equal(L.dinheiro(-0.001), '-R$ 0,00', 'mesmo sinal do servidor')
assert.equal(L.inteiro(2.5), '2', 'sem casas o meio vai pro par, como o round() do Python: 2,5 → 2')
assert.equal(L.inteiro(3.5), '4')
assert.equal(L.inteiro(-0.4), '0', 'sem "-0"')
assert.equal(L.inteiro(-1234.6), '-1.235')
assert.equal(L.fmtValor(1500, 'dinheiro'), 'R$ 1.500,00')
assert.equal(L.fmtValor(1500, 'inteiro'), '1.500')
assert.equal(L.fmtValor(3.14159, 'percentual'), '3,1%')
// Planilha do Resumo (07/10/2026): % com 2 casas ("7,50%"); o resto igual.
assert.equal(L.percentual(7.5, 2), '7,50%')
assert.equal(L.percentual(null, 2), '—')
assert.equal(L.fmtPlanilha(7.5, 'percentual'), '7,50%')
assert.equal(L.fmtPlanilha(0, 'percentual'), '0,00%', 'zero medido aparece')
assert.equal(L.fmtPlanilha(1234.5, 'dinheiro'), 'R$ 1.234,50')
assert.equal(L.fmtPlanilha(72388, 'inteiro'), '72.388')
assert.equal(L.fmtPlanilha(null, 'percentual'), '—')
assert.equal(L.fmtPlanilha(undefined, 'dinheiro'), '—')

// ---------------------------------------------------------------- empates: os MESMOS do servidor
// tests/conferencia-arredondamento.json também é conferido por
// apps/api/tests/test_conferencia_shopee_calculo.py: um lado mudou a regra, o
// outro quebra.
{
  const casos = JSON.parse(fs.readFileSync(path.join(__dirname, 'conferencia-arredondamento.json'), 'utf8'))
  assert.ok(casos.formatos.length > 20 && casos.variacoes.length > 5)
  for (const [v, tipo, texto] of casos.formatos) assert.equal(L.fmtValor(v, tipo), texto, `${v} (${tipo})`)
  for (const [atual, anterior, tipo, bom, chave, texto, cor] of casos.variacoes) {
    const r = L.variacao(atual, anterior, tipo, bom, chave ?? undefined)
    assert.deepEqual([r.texto, r.cor], [texto, cor], `${atual} vs ${anterior} (${tipo})`)
  }
  // Variação da planilha: p.p. com 2 casas (no servidor, variacao(..., casas=2)).
  assert.equal(L.CASAS_PLANILHA, 2)
  assert.ok(casos.variacoes_2casas.length >= 8)
  for (const [atual, anterior, tipo, bom, chave, texto, cor] of casos.variacoes_2casas) {
    const r = L.variacao(atual, anterior, tipo, bom, chave ?? undefined, L.CASAS_PLANILHA)
    assert.deepEqual([r.texto, r.cor], [texto, cor], `${atual} vs ${anterior} (${tipo}, 2 casas)`)
  }
  // % com 2 casas da planilha do Resumo (07/10/2026); no servidor, percentual(v, 2).
  assert.ok(casos.percentual_2casas.length >= 8)
  for (const [v, texto] of casos.percentual_2casas) {
    assert.equal(L.percentual(v, 2), texto, `${v} (% 2 casas)`)
    assert.equal(L.fmtPlanilha(v, 'percentual'), texto, `${v} (célula da planilha)`)
  }
}

// ---------------------------------------------------------------- variação: dinheiro/inteiro
const v = (...a) => { const r = L.variacao(...a); return [r.texto, r.cor] }
assert.deepEqual(v(112.3, 100, 'dinheiro', 'sobe'), ['▲ 12,3%', 'verde'])
assert.deepEqual(v(95.9, 100, 'dinheiro', 'sobe'), ['▼ 4,1%', 'vermelho'])
assert.deepEqual(v(1500, 1000, 'inteiro', 'sobe'), ['▲ 50,0%', 'verde'])
assert.deepEqual(v(25000, 1000, 'dinheiro', 'sobe'), ['▲ 2.400,0%', 'verde'], 'milhar com ponto')
assert.deepEqual(v(10, 0, 'dinheiro', 'sobe'), ['novo', 'verde'], 'anterior 0 e atual > 0 → novo')
assert.deepEqual(v(0, 0, 'dinheiro', 'sobe'), ['=', 'cinza'], 'os dois 0 → =')
assert.deepEqual(v(100, 100, 'dinheiro', 'sobe'), ['=', 'cinza'], 'iguais → =')
assert.deepEqual(v(0, 100, 'dinheiro', 'sobe'), ['▼ 100,0%', 'vermelho'], 'caiu a zero')
assert.deepEqual(v(null, 100, 'dinheiro', 'sobe'), ['—', 'cinza'], 'atual null → —')
assert.deepEqual(v(100, null, 'dinheiro', 'sobe'), ['—', 'cinza'], 'anterior null → —')
assert.deepEqual(v(undefined, undefined, 'dinheiro', 'sobe'), ['—', 'cinza'])
assert.deepEqual(v(-5, 0, 'dinheiro', 'sobe'), ['—', 'cinza'], 'de 0 para negativo não é "novo"')
assert.deepEqual(v(50, -100, 'dinheiro', 'sobe'), ['▲ 150,0%', 'verde'], 'divide por |anterior|')
// investimentos e saldo: cinza sempre, nos dois sentidos
assert.deepEqual(v(200, 100, 'dinheiro', 'neutro'), ['▲ 100,0%', 'cinza'])
assert.deepEqual(v(50, 100, 'dinheiro', 'neutro'), ['▼ 50,0%', 'cinza'])
assert.deepEqual(v(10, 0, 'dinheiro', 'neutro'), ['novo', 'cinza'], 'investimento novo é "novo", em cinza')
// Saldo nunca é "novo"
assert.deepEqual(v(50, 0, 'dinheiro', 'neutro', 'saldo_ads'), ['—', 'cinza'], 'saldo nunca "novo"')
assert.deepEqual(v(0, 0, 'dinheiro', 'neutro', 'saldo_ads'), ['=', 'cinza'])
assert.deepEqual(v(120, 100, 'dinheiro', 'neutro', 'saldo_ads'), ['▲ 20,0%', 'cinza'])
// direção
assert.equal(L.variacao(2, 1, 'dinheiro', 'sobe').direcao, 'sobe')
assert.equal(L.variacao(1, 2, 'dinheiro', 'sobe').direcao, 'desce')
assert.equal(L.variacao(1, 1, 'dinheiro', 'sobe').direcao, null)
assert.equal(L.variacao(1, 0, 'dinheiro', 'sobe').direcao, 'sobe', '"novo" sobe')

// ---------------------------------------------------------------- variação: % s/ vendas (p.p., cair é bom)
assert.deepEqual(v(9.4, 8.2, 'percentual', 'desce'), ['▲ 1,2 p.p.', 'vermelho'], 'subir o % é ruim')
assert.deepEqual(v(7.4, 8.2, 'percentual', 'desce'), ['▼ 0,8 p.p.', 'verde'], 'cair o % é bom')
assert.deepEqual(v(8.2, 8.2, 'percentual', 'desce'), ['=', 'cinza'])
assert.deepEqual(v(5, 0, 'percentual', 'desce'), ['▲ 5,0 p.p.', 'vermelho'], '% nunca é "novo": é diferença')
assert.deepEqual(v(8.25, 8, 'percentual', 'desce'), ['▲ 0,3 p.p.', 'vermelho'], 'meio pra longe do zero, igual ao servidor')
assert.deepEqual(v(8.75, 8, 'percentual', 'desce'), ['▲ 0,8 p.p.', 'vermelho'])
assert.deepEqual(v(null, 8.2, 'percentual', 'desce'), ['—', 'cinza'])
assert.deepEqual(v(8.2, null, 'percentual', 'desce'), ['—', 'cinza'])

// ---------------------------------------------------------------- somas, investimento, %
assert.equal(L.soma([null, 2, undefined, 3]), 5, 'null + x = x')
assert.equal(L.soma([null, null]), null, 'tudo null = null')
assert.equal(L.soma([0, null]), 0, 'zero é número')
assert.equal(L.investimento({ invest_afiliados: 10, invest_ads: 5 }), 15)
assert.equal(L.investimento({ invest_afiliados: null, invest_ads: 3 }), 3)
assert.equal(L.investimento({ invest_afiliados: null, invest_ads: null }), null)
assert.equal(L.investimento(undefined), null)
assert.equal(L.pctDe(10, 200), 5)
assert.equal(L.pctDe(10, 0), null, 'sem vendas → null')
assert.equal(L.pctDe(null, 100), null, 'sem investimento → null')
assert.equal(L.pctDe(10, null), null)
assert.equal(L.conversaoDe(5, 200), 2.5, 'pedidos ÷ cliques × 100')
assert.equal(L.conversaoDe(5, 0), null, 'sem cliques → null')
assert.equal(L.conversaoDe(5, -10), null, 'cliques negativos → null (nunca conversão negativa)')
assert.equal(L.conversaoDe(-5, 10), null)
assert.equal(L.conversaoDe(5, null), null)
assert.equal(L.conversaoDe(null, 200), null)
assert.equal(L.conversaoDe(0, 200), 0, 'zero pedido é 0%')
assert.equal(L.valor({ vendas: 3 }, 'vendas'), 3)
assert.equal(L.valor({}, 'vendas'), null, 'ausente vira null')
assert.equal(L.valor({ vendas: 'x' }, 'vendas'), null, 'não-número vira null')

// ---------------------------------------------------------------- média das 3 semanas anteriores
{
  const s = [{ vendas: 100 }, { vendas: 50 }, { vendas: null }, { vendas: 150 }]
  assert.equal(L.media3(s, 'vendas'), 100, 'só os valores que existem entram')
  assert.equal(L.media3([{ vendas: 1 }, {}, {}, {}], 'vendas'), null, 'nenhum → null')
  assert.equal(L.media3([], 'vendas'), null)
  assert.equal(L.media3(null, 'vendas'), null)
  assert.equal(L.media3([{ vendas: 999 }, { vendas: 30 }], 'vendas'), 30, 'S1 nunca entra na média')
  // % s/ vendas: Σinvest ÷ Σvendas das 3, não a média dos percentuais
  const p = [
    { invest_afiliados: 999, invest_ads: 999, vendas: 1, pct: 99900 },
    { invest_afiliados: 10, invest_ads: 5, vendas: 100, pct: 15 },
    { invest_afiliados: 20, invest_ads: null, vendas: 300, pct: 6.67 },
    { invest_afiliados: null, invest_ads: null, vendas: null, pct: null },
  ]
  assert.equal(L.media3(p, 'pct'), 8.75, '(15 + 20) ÷ 400 × 100')
  assert.equal(L.media3([{}, { invest_ads: 3 }, {}, {}], 'pct'), null, 'sem vendas → null')
  assert.equal(L.media3([{}, { vendas: 100 }, {}, {}], 'pct'), null, 'sem investimento → null')
  // vs média: a mesma variação, contra a média
  const lq = L.linhaQuatroSemanas(s, L.METRICAS.find((m) => m.chave === 'vendas'))
  assert.deepEqual(lq.valores, ['R$ 100,00', 'R$ 50,00', '—', 'R$ 150,00'])
  assert.deepEqual([lq.vsAnterior.texto, lq.vsAnterior.cor], ['▲ 100,0%', 'verde'])
  assert.deepEqual([lq.vsMedia.texto, lq.vsMedia.cor], ['=', 'cinza'])
  const lqp = L.linhaQuatroSemanas(
    [{ pct: 10, invest_afiliados: 10, vendas: 100 }, ...p.slice(1)],
    L.METRICAS.find((m) => m.chave === 'pct'),
  )
  assert.equal(lqp.vsMedia.texto, '▲ 1,3 p.p.', '10 − 8,75 = 1,25: meio pra longe do zero, igual ao servidor')
  assert.equal(lqp.vsMedia.cor, 'vermelho')
  // conversão: Σpedidos ÷ Σcliques das 3, não a média dos percentuais
  const c = [
    { pedidos_ads: 99, cliques_ads: 100, conversao_ads: 99 },
    { pedidos_ads: 10, cliques_ads: 100, conversao_ads: 10 },
    { pedidos_ads: 10, cliques_ads: 900, conversao_ads: 1.11 },
    { pedidos_ads: null, cliques_ads: null, conversao_ads: null },
  ]
  assert.equal(L.media3(c, 'conversao_ads'), 2, '(10 + 10) ÷ (100 + 900) × 100 — a média dos % daria 5,56')
  assert.equal(L.media3([{}, { pedidos_afiliados: 3 }, {}, {}], 'conversao_afiliados'), null, 'sem cliques → null')
  assert.equal(L.media3([{}, {}, {}, {}], 'conversao_ads'), null, 'relatório antigo (sem as chaves) → null')
  // saldo: média sem "novo"
  const saldo = L.METRICAS.find((m) => m.chave === 'saldo_ads')
  const ls = L.linhaQuatroSemanas([{ saldo_ads: 80 }, { saldo_ads: 0 }, { saldo_ads: 0 }, {}], saldo)
  assert.equal(ls.vsAnterior.texto, '—', 'saldo 0 → 80 não é "novo"')
  assert.equal(ls.vsMedia.texto, '—')
}

// ---------------------------------------------------------------- célula e cartão
{
  const vendas = L.METRICAS.find((m) => m.chave === 'vendas')
  const c = L.celula([{ vendas: 1100 }, { vendas: 1000 }], vendas)
  assert.deepEqual([c.valor, c.anterior, c.variacao.texto, c.variacao.cor, c.vazia], ['R$ 1.100,00', 'R$ 1.000,00', '▲ 10,0%', 'verde', false])
  const vazia = L.celula([{ vendas: null }, {}], vendas)
  assert.equal(vazia.vazia, true, 'sem número nas duas semanas: sem linha "ant."')
  assert.equal(vazia.valor, '—')
  assert.equal(L.celula(undefined, vendas).vazia, true)
  const so1 = L.celula([{ vendas: 5 }, { vendas: null }], vendas)
  assert.equal(so1.vazia, false)
  assert.equal(so1.anterior, '—')
  assert.equal(so1.variacao.texto, '—')

  const card = L.resumoCartao({
    contas: 4, sem_dados: 0,
    semanas: [
      { vendas: 2000, invest_afiliados: 100, invest_ads: 60, pct: 8 },
      { vendas: 1600, invest_afiliados: 100, invest_ads: null, pct: 6.25 },
      {}, {},
    ],
  })
  assert.deepEqual([card.vendas.valor, card.vendas.anterior, card.vendas.variacao.texto, card.vendas.variacao.cor],
    ['R$ 2.000,00', 'R$ 1.600,00', '▲ 25,0%', 'verde'])
  assert.deepEqual([card.investimento.valor, card.investimento.anterior, card.investimento.variacao.texto, card.investimento.variacao.cor],
    ['R$ 160,00', 'R$ 100,00', '▲ 60,0%', 'cinza'], 'investimento = afiliados + Ads, em cinza')
  assert.deepEqual([card.pct.valor, card.pct.anterior, card.pct.variacao.texto, card.pct.variacao.cor],
    ['8,0%', '6,3%', '▲ 1,8 p.p.', 'vermelho'], '6,25 → 6,3 e 1,75 → 1,8: meio pra longe do zero')
  const nada = L.resumoCartao(null)
  assert.deepEqual([nada.vendas.valor, nada.vendas.variacao.texto], ['—', '—'])
}

// ---------------------------------------------------------------- datas e textos
assert.equal(L.ddmm('2026-09-28'), '28/09')
assert.equal(L.ddmmaaaa('2026-10-04'), '04/10/2026')
assert.equal(L.ddmm(null), '')
const SEMANAS = [
  { inicio: '2026-09-28', fim: '2026-10-04' },
  { inicio: '2026-09-21', fim: '2026-09-27', rotulo: '21/09–27/09' },
]
assert.equal(L.tituloRelatorio(SEMANAS), 'Conferência Shopee — 28/09 a 04/10/2026')
assert.equal(L.tituloRelatorio([]), 'Conferência Shopee')
assert.equal(L.comparadoCom(SEMANAS), 'comparado com 21/09 a 27/09')
assert.equal(L.comparadoCom(SEMANAS.slice(0, 1)), '')
assert.equal(L.rotuloSemana(SEMANAS[0]), '28/09–04/10', 'monta das datas')
assert.equal(L.rotuloSemana(SEMANAS[1]), '21/09–27/09', 'usa o rótulo do relatório')
assert.equal(L.rotuloSemana(null), '—')
assert.equal(L.dataHoraBr('2026-10-06T16:41:02Z'), '06/10/2026 às 13:41', 'horário de Brasília')
assert.equal(L.dataHoraBr('2026-10-07T01:30:00Z'), '06/10/2026 às 22:30', 'virada do dia em UTC não muda o dia daqui')
assert.equal(L.dataHoraBr(null), '')
assert.equal(L.horaBr('2026-10-06T20:30:00Z'), '17:30')
assert.equal(
  L.rotuloExecucao({ id: 'x', tipo: 'semanal', origem: 'agenda', status: 'pronto', criado_em: '2026-10-06T16:30:00Z', finalizado_em: null, semanas: SEMANAS }),
  '06/10 às 13:30 · semanal · 28/09–04/10 · pronto',
)
assert.equal(L.contasTxt(1), '1 conta')
assert.equal(L.contasTxt(4), '4 contas')
assert.equal(L.contasTxt(null), '0 contas')

// ---------------------------------------------------------------- planilha do Resumo (07/10/2026)
// O desenho da planilha antiga do dono: métrica × (semana × Mala/Celular/Eletro/
// Geral), semanas da mais velha pra mais nova, e o bloco Variação (S1 × S2).
{
  const SEM4 = [
    { inicio: '2026-09-28', fim: '2026-10-04', rotulo: '28/09–04/10' },
    { inicio: '2026-09-21', fim: '2026-09-27', rotulo: '21/09–27/09' },
    { inicio: '2026-09-14', fim: '2026-09-20' },
    { inicio: '2026-09-07', fim: '2026-09-13' },
  ]
  // Cada semana com valores diferentes por grupo, pra a posição da célula importar.
  const sem = (base, over = {}) => ({
    vendas_afiliados: 1000 * base, vendas_ads: 500 * base, saldo_ads: 77, impressoes: 10000 * base,
    invest_afiliados: 50 * base, invest_ads: 25 * base, pct: 7.5, vendas: 1000 * base,
    cliques_afiliados: 400, pedidos_afiliados: 10, conversao_afiliados: 2.5,
    cliques_ads: 200, pedidos_ads: 5, conversao_ads: 2.5, ...over,
  })
  const tot = (semanas) => ({ contas: 1, sem_dados: 0, semanas })
  const rel = {
    semanas: SEM4,
    grupos: [
      { chave: 'mala', rotulo: 'Mala', linhas: [], total: tot([sem(4), sem(3), sem(2), sem(1)]) },
      { chave: 'celular', rotulo: 'Celular', linhas: [], total: tot([sem(2, { pct: 8.25, conversao_ads: 3 }), sem(2, { pct: 8, conversao_ads: 2.5 }), sem(2), sem(2)]) },
      // Eletro sem nada numa semana e sem Ads em outra: "—", nunca 0.
      { chave: 'eletro', rotulo: 'Eletro', linhas: [], total: tot([sem(1, { vendas_ads: null, invest_ads: null }), {}, sem(1), sem(1)]) },
    ],
    geral: tot([sem(10, { pct: 7.25 }), sem(5, { pct: 7.5 }), sem(6), sem(7)]),
  }
  const p = L.planilhaResumo(rel)

  // Cabeçalho: semanas da mais VELHA pra mais nova; Variação no fim.
  assert.deepEqual(p.semanas.map((s) => [s.indice, s.rotulo]), [
    [3, '07/09 a 13/09'], [2, '14/09 a 20/09'], [1, '21/09 a 27/09'], [0, '28/09 a 04/10'],
  ])
  assert.deepEqual(p.grupos.map((g) => g.rotulo), ['Mala', 'Celular', 'Eletro', 'Geral'])
  assert.equal(p.variacao, 'Variação (28/09–04/10 × 21/09–27/09)')

  // Linhas: categoria mesclada nas 2 linhas dela + sub-rótulo, na ordem da planilha.
  assert.deepEqual(p.linhas.map((l) => [l.categoria, l.sub, l.span]), [
    ['Vendas', 'afiliados', 2], ['Vendas', 'Ads', 0],
    ['Impressões', 'afiliados', 2], ['Impressões', 'Ads', 0],
    ['Conversão', 'afiliados', 2], ['Conversão', 'Ads', 0],
    ['Investimento', 'afiliados', 2], ['Investimento', 'Ads', 0],
    ['Resumo', '% investimento / vendas', 2], ['Resumo', 'Vendas no período', 0],
  ])
  assert.equal(new Set(p.linhas.map((l) => l.chave)).size, p.linhas.length, 'chave única por linha')
  assert.ok(!p.linhas.some((l) => l.chave === 'saldo_ads'), 'Saldo Ads não entra na planilha')
  const lin = (cat, sub) => p.linhas.find((l) => l.categoria === cat && l.sub === sub)

  // Valores: [semana velha→nova][Mala, Celular, Eletro, Geral].
  const va = lin('Vendas', 'afiliados')
  assert.equal(va.valores.length, 4)
  assert.deepEqual(va.valores[0], ['R$ 1.000,00', 'R$ 2.000,00', 'R$ 1.000,00', 'R$ 7.000,00'], 'S4 primeiro')
  assert.deepEqual(va.valores[3], ['R$ 4.000,00', 'R$ 2.000,00', 'R$ 1.000,00', 'R$ 10.000,00'], 'S1 por último')
  assert.deepEqual(va.valores[2][2], '—', 'Eletro sem a semana → "—"')
  assert.deepEqual(lin('Vendas', 'Ads').valores[3], ['R$ 2.000,00', 'R$ 1.000,00', '—', 'R$ 5.000,00'], 'Eletro sem Ads → "—"')
  assert.deepEqual(lin('Impressões', 'Ads').valores[3], ['40.000', '20.000', '10.000', '100.000'])
  // Impressões de afiliados não existem na Shopee: "—" em tudo, variação "—" cinza.
  const ia = lin('Impressões', 'afiliados')
  assert.ok(ia.valores.every((s) => s.every((x) => x === '—')))
  assert.deepEqual(ia.variacoes.map((v) => [v.texto, v.cor]), Array(4).fill(['—', 'cinza']))
  // % com 2 casas.
  assert.deepEqual(lin('Conversão', 'Ads').valores[3], ['2,50%', '3,00%', '2,50%', '2,50%'])
  assert.deepEqual(lin('Resumo', '% investimento / vendas').valores[3], ['7,50%', '8,25%', '7,50%', '7,25%'])
  assert.deepEqual(lin('Resumo', 'Vendas no período').valores[0], ['R$ 1.000,00', 'R$ 2.000,00', 'R$ 1.000,00', 'R$ 7.000,00'])
  assert.deepEqual(lin('Investimento', 'Ads').valores[3], ['R$ 100,00', 'R$ 50,00', '—', 'R$ 250,00'])

  // Variação S1 × S2 por grupo, com as regras de sempre.
  const vv = (cat, sub) => lin(cat, sub).variacoes.map((v) => [v.texto, v.cor])
  assert.deepEqual(vv('Vendas', 'afiliados'), [['▲ 33,3%', 'verde'], ['=', 'cinza'], ['—', 'cinza'], ['▲ 100,0%', 'verde']])
  assert.deepEqual(vv('Investimento', 'afiliados')[0], ['▲ 33,3%', 'cinza'], 'investimento: cinza')
  // p.p. com 2 casas, como o % da planilha (com 1 casa, 8,25 − 8 sairia "▲ 0,3").
  assert.deepEqual(vv('Resumo', '% investimento / vendas'), [['=', 'cinza'], ['▲ 0,25 p.p.', 'vermelho'], ['—', 'cinza'], ['▼ 0,25 p.p.', 'verde']], '% em p.p.; cair é bom')
  assert.deepEqual(vv('Conversão', 'Ads')[1], ['▲ 0,50 p.p.', 'verde'], 'conversão em p.p.; subir é bom')
  {
    // Conversão pequena (0,44% → 0,43%): "▼ 0,01 p.p.", nunca "▼ 0,0 p.p." dizendo zero.
    const r2 = JSON.parse(JSON.stringify(rel))
    r2.geral.semanas[0].conversao_afiliados = 0.43
    r2.geral.semanas[1].conversao_afiliados = 0.44
    const v2 = L.planilhaResumo(r2).linhas.find((l) => l.chave === 'conversao_afiliados').variacoes[3]
    assert.deepEqual([v2.texto, v2.cor], ['▼ 0,01 p.p.', 'vermelho'])
  }

  // Relatório de ANTES de 07/10 (sem cliques/pedidos/conversão): "—", nunca 0, nunca erro.
  const antigo = JSON.parse(JSON.stringify(rel))
  for (const t of [...antigo.grupos.map((g) => g.total), antigo.geral]) {
    for (const s of t.semanas) for (const k of Object.keys(s)) if (/cliques|pedidos|conversao/.test(k)) delete s[k]
  }
  const pa = L.planilhaResumo(antigo)
  for (const sub of ['afiliados', 'Ads']) {
    const l = pa.linhas.find((x) => x.categoria === 'Conversão' && x.sub === sub)
    assert.ok(l.valores.every((s) => s.every((x) => x === '—')), `conversão ${sub} sem dados → "—"`)
    assert.ok(l.variacoes.every((v) => v.texto === '—'))
  }
  assert.deepEqual(pa.linhas.find((x) => x.sub === 'Vendas no período').valores[3], ['R$ 4.000,00', 'R$ 2.000,00', 'R$ 1.000,00', 'R$ 10.000,00'], 'o resto continua')

  // Grupo faltando, semanas a menos, lixo: não quebra.
  const torto = L.planilhaResumo({ semanas: SEM4.slice(0, 1), grupos: [{ chave: 'mala', rotulo: 'Mala', linhas: [], total: null }], geral: null })
  assert.deepEqual(torto.semanas.map((s) => s.rotulo), ['28/09 a 04/10'])
  assert.equal(torto.variacao, 'Variação', 'sem S2 não diz contra o quê')
  assert.ok(torto.linhas.every((l) => l.valores.length === 1 && l.valores[0].every((x) => x === '—')))
  assert.ok(torto.linhas.every((l) => l.variacoes.length === 4 && l.variacoes.every((v) => v.texto === '—')))
  assert.equal(L.planilhaResumo(null), null)
  const vazio = L.planilhaResumo({ semanas: undefined, grupos: undefined, geral: undefined })
  assert.deepEqual([vazio.semanas, vazio.linhas.length], [[], 10])
  assert.equal(L.semanaPlanilha(null), '—')
}

// ---------------------------------------------------------------- arquivos
assert.equal(L.nomeArquivo(SEMANAS, 'xlsx'), 'conferencia-shopee-2026-09-28_2026-10-04.xlsx', 'mesmo nome do servidor')
assert.equal(L.nomeArquivo([], 'csv'), 'conferencia-shopee.csv')
assert.equal(L.nomeDoCabecalho('attachment; filename="conferencia-shopee-2026-09-28_2026-10-04.csv"'), 'conferencia-shopee-2026-09-28_2026-10-04.csv')
assert.equal(L.nomeDoCabecalho('attachment; filename=relatorio.md'), 'relatorio.md')
assert.equal(L.nomeDoCabecalho("attachment; filename=\"x.csv\"; filename*=UTF-8''confer%C3%AAncia.csv"), 'conferência.csv', 'filename* ganha')
assert.equal(L.nomeDoCabecalho("attachment; filename*=UTF-8''%E0%A4%A.csv; filename=\"ok.csv\""), 'ok.csv', 'filename* quebrado cai no comum')
assert.equal(L.nomeDoCabecalho('inline'), null)
assert.equal(L.nomeDoCabecalho(null), null)

// ---------------------------------------------------------------- status
assert.equal(L.rotuloStatusColeta('sem_automacao'), 'perfil Firefox, o robô não abre')
assert.equal(L.rotuloStatusColeta('pendente'), 'na fila')
assert.equal(L.rotuloStatusColeta('expirada'), 'não coletada a tempo')
assert.equal(L.rotuloStatusColeta('status_novo'), 'status novo', 'desconhecido aparece legível')
assert.equal(L.tomStatusColeta('ok'), 'ok')
assert.equal(L.tomStatusColeta('parcial'), 'alerta')
assert.equal(L.tomStatusColeta('coletando'), 'andamento')
assert.equal(L.tomStatusColeta('pendente'), 'neutro')
for (const s of ['deslogada', 'sem_automacao', 'bloqueada', 'interrompida', 'erro']) {
  assert.equal(L.tomStatusColeta(s), 'erro', s)
}
assert.equal(L.coletaTerminou('pendente'), false)
assert.equal(L.coletaTerminou('coletando'), false)
assert.equal(L.coletaTerminou('ok'), true)
assert.equal(L.coletaTerminou('expirada'), true)
assert.equal(L.rotuloStatusExecucao('pronto'), 'pronto')
assert.equal(L.ERROS_CONFERENCIA.conferencia_em_andamento.includes('coletando'), true)

console.log('conferencia-lib: ok')
