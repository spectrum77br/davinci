// Run from apps/web: node tests/pricing-contas-equipe.cjs
// Coluna "Equipe" e filtro de equipe da aba Contas (03/11/2026): executa os
// helpers reais da página e confere a coluna no template, antes do Kit.
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
const names = ['equipesDaConta', 'rotuloEquipes', 'passaFiltroEquipe', 'opcoesEquipe']
const selected = names.map((name) => {
  const node = source.statements.find((candidate) =>
    ts.isFunctionDeclaration(candidate) && candidate.name?.text === name)
  assert.ok(node, `real page helper ${name} exists`)
  return node.getText(source)
})
const semEquipe = source.statements.find((s) => ts.isVariableStatement(s)
  && s.declarationList.declarations.some((d) => d.name.getText(source) === 'SEM_EQUIPE'))
assert.ok(semEquipe, 'SEM_EQUIPE exists')
const compiled = ts.transpileModule([semEquipe.getText(source), ...selected].join('\n'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022 }, reportDiagnostics: true,
})
assert.deepEqual(compiled.diagnostics, [])
const { rotuloEquipes, passaFiltroEquipe, opcoesEquipe } = new Function(
  `${compiled.outputText}\nreturn { rotuloEquipes, passaFiltroEquipe, opcoesEquipe }`)()

const londres = { equipes: [{ numero: 101, nome: 'Londres' }] }
const duas = { equipes: [{ numero: 102, nome: 'Israel' }, { numero: 103, nome: 'Marrocos' }] }
const sem = { equipes: [] }
const antiga = {} // resposta sem o campo (API de antes): sem equipe, sem quebrar

assert.equal(rotuloEquipes(londres), 'Londres')
assert.equal(rotuloEquipes(duas), 'Israel / Marrocos')
assert.equal(rotuloEquipes(sem), 'Sem equipe')
assert.equal(rotuloEquipes(antiga), 'Sem equipe')

for (const conta of [londres, duas, sem, antiga]) assert.equal(passaFiltroEquipe(conta, ''), true)
assert.equal(passaFiltroEquipe(londres, '101'), true)
assert.equal(passaFiltroEquipe(londres, '102'), false)
assert.equal(passaFiltroEquipe(duas, '102'), true, 'conta de duas equipes entra nas duas')
assert.equal(passaFiltroEquipe(duas, '103'), true)
assert.equal(passaFiltroEquipe(sem, 'sem'), true)
assert.equal(passaFiltroEquipe(antiga, 'sem'), true)
assert.equal(passaFiltroEquipe(londres, 'sem'), false)

// Opções do filtro: as equipes da aba em ordem, "Sem equipe" se houver conta
// sem nenhuma, e a equipe já escolhida mesmo sem conta nesta aba (o filtro
// sobrevive à troca Celular → Mala; sem a opção o select ficava em branco).
const marrocos = { equipes: [{ numero: 103, nome: 'Marrocos' }] }
const celular = [duas, londres, sem]
assert.deepEqual(opcoesEquipe(celular, celular, ''), {
  equipes: [{ numero: 101, nome: 'Londres' }, { numero: 102, nome: 'Israel' }, { numero: 103, nome: 'Marrocos' }],
  semEquipe: true,
})
assert.deepEqual(opcoesEquipe([londres], [londres, marrocos], '103'), {
  equipes: [{ numero: 101, nome: 'Londres' }, { numero: 103, nome: 'Marrocos' }],
  semEquipe: false,
})
assert.deepEqual(opcoesEquipe([londres], [londres], '104').equipes.at(-1), { numero: 104, nome: 'Equipe 104' })
assert.equal(opcoesEquipe([londres], [londres], 'sem').semEquipe, true, '"Sem equipe" escolhido continua na lista')

// A coluna fica entre Catálogo ML e Kit, no cabeçalho, na linha de cada conta
// e na linha de adicionar conta.
const tpl = descriptor.template.content
const contas = tpl.slice(tpl.indexOf("tab === 'contas'"))
const cab = contas.slice(0, contas.indexOf('</thead>'))
const ths = [...cab.matchAll(/<th[\s\S]*?>([^<]*)<\/th>/g)].map((m) => m[1].trim())
const iKit = ths.indexOf('Kit')
assert.ok(iKit > 0, 'Kit header exists')
assert.equal(ths[iKit - 1], 'Equipe')
assert.equal(ths[iKit - 2], 'Catálogo ML')
const linha = contas.slice(contas.indexOf('<!-- Catálogo ML: liga/desliga'))
assert.ok(linha.indexOf('data-coluna-equipe') < linha.indexOf('<!-- kit -->'),
  'equipe cell comes right before the kit cell')
assert.match(contas, /Tipo ML, Catálogo ML e Equipe: definidos depois[\s\S]*?—<\/td>[\s\S]*?—<\/td>[\s\S]*?—<\/td>[\s\S]*?newAcc\.kit_number/,
  'add-account row keeps the columns aligned')
assert.match(contas, /<select[\s\S]*?v-model="contasEquipeFilter"[\s\S]*?Todas as equipes/)
// Conta nova (nasce sem equipe) não some atrás de um filtro de equipe.
assert.match(descriptor.scriptSetup.content, /accounts\.value\.push\(created\)\s*\n(\s*\/\/.*\n)*\s*if \(!passaFiltroEquipe\(created, contasEquipeFilter\.value\)\) contasEquipeFilter\.value = ''/)

console.log('pricing-contas-equipe: ok')
