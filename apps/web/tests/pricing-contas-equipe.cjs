// Run from apps/web: node tests/pricing-contas-equipe.cjs
// Coluna "Equipe" e filtros da aba Contas (03/11/2026; M-9, 18/11/2026: a
// coluna mostra só o número e os filtros Plataforma ▾ e Equipe ▾ viraram
// caixas de marcar): executa os helpers reais da página e confere o template.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const { parse } = require('vue/compiler-sfc')

const filename = path.resolve(__dirname, '../pages/pricing/[tab].vue')
const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
assert.deepEqual(errors, [])
const setup = descriptor.scriptSetup.content
const source = ts.createSourceFile(filename, setup, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
const names = ['alternarNaLista', 'equipesDaConta', 'numeroDaEquipe', 'rotuloEquipes', 'chavesEquipe',
  'passaFiltroEquipe', 'opcoesEquipe']
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
const { alternarNaLista, rotuloEquipes, passaFiltroEquipe, opcoesEquipe } = new Function(
  `${compiled.outputText}\nreturn { alternarNaLista, rotuloEquipes, passaFiltroEquipe, opcoesEquipe }`)()

// O nome vem da API como "Equipe N" (routers/pricing.py, _rotulo_equipe).
const um = { equipes: [{ numero: 101, nome: 'Equipe 1' }] }
const dois = { equipes: [{ numero: 102, nome: 'Equipe 2' }] }
const duas = { equipes: [{ numero: 101, nome: 'Equipe 1' }, { numero: 102, nome: 'Equipe 2' }] }
const sem = { equipes: [] }
const antiga = {} // resposta sem o campo (API de antes): sem equipe, sem quebrar
const outroNome = { equipes: [{ numero: 104, nome: 'Londres' }] }

// 1) Coluna: só o número.
assert.equal(rotuloEquipes(dois), '2', '"Equipe 2" vira "2"')
assert.equal(rotuloEquipes(duas), '1 / 2', '"Equipe 1 / Equipe 2" vira "1 / 2"')
assert.equal(rotuloEquipes(sem), 'Sem equipe')
assert.equal(rotuloEquipes(antiga), 'Sem equipe')
assert.equal(rotuloEquipes(outroNome), 'Londres', 'nome fora do formato fica como veio')

// 2) Caixas de marcar: a lista guarda o que está DESMARCADO.
assert.deepEqual(alternarNaLista([], '101'), ['101'])
assert.deepEqual(alternarNaLista(['101', 'sem'], '101'), ['sem'])

for (const conta of [um, dois, duas, sem, antiga]) assert.equal(passaFiltroEquipe(conta, []), true, 'tudo marcado = todas')
assert.equal(passaFiltroEquipe(dois, ['102']), false)
assert.equal(passaFiltroEquipe(um, ['102']), true)
assert.equal(passaFiltroEquipe(duas, ['102']), true, 'conta de duas equipes aparece por qualquer uma marcada')
assert.equal(passaFiltroEquipe(duas, ['101', '102']), false)
assert.equal(passaFiltroEquipe(sem, ['sem']), false)
assert.equal(passaFiltroEquipe(antiga, ['sem']), false)
assert.equal(passaFiltroEquipe(dois, ['sem']), true)
assert.equal(passaFiltroEquipe(sem, ['101', '102']), true)

// 3) Opções: os números em ordem e "Sem equipe" no fim, se houver conta sem.
assert.deepEqual(opcoesEquipe([dois, sem, um]), [
  { valor: '101', rotulo: '1' },
  { valor: '102', rotulo: '2' },
  { valor: 'sem', rotulo: 'Sem equipe' },
])
assert.deepEqual(opcoesEquipe([dois, duas]), [{ valor: '101', rotulo: '1' }, { valor: '102', rotulo: '2' }])
assert.deepEqual(opcoesEquipe([dois]), [{ valor: '102', rotulo: '2' }])
// Só aparece com mais de uma opção (ou com uma opção desta aba desmarcada).
assert.match(setup, /const mostrarFiltroEquipe = computed\(\(\) => \{\s*const opcoes = equipesPresentesNoDept\.value\s*return opcoes\.length > 1 \|\| opcoes\.some\(\(o\) => contasEquipesDesmarcadas\.value\.includes\(o\.valor\)\)/)
// Ao recarregar, tudo marcado: as listas começam vazias (não ficam salvas).
assert.match(setup, /const contasPlataformasDesmarcadas = ref<string\[\]>\(\[\]\)/)
assert.match(setup, /const contasEquipesDesmarcadas = ref<string\[\]>\(\[\]\)/)
assert.doesNotMatch(setup, /contasPlataformasDesmarcadas[^\n]*localStorage|localStorage[^\n]*contasEquipesDesmarcadas/)
assert.match(setup, /if \(pd\.includes\(a\.platform\)\) return false\s*\n\s*if \(!passaFiltroEquipe\(a, ed\)\) return false/)

// 4) Template: a coluna fica entre Catálogo ML e Kit, no cabeçalho, na linha
// de cada conta e na linha de adicionar conta.
const tpl = descriptor.template.content
const contas = tpl.slice(tpl.indexOf("tab === 'contas'"), tpl.indexOf("tab === 'produtos'"))
const cab = contas.slice(0, contas.indexOf('</thead>'))
const ths = [...cab.matchAll(/<th[\s\S]*?>([^<]*)<\/th>/g)].map((m) => m[1].trim())
const iKit = ths.indexOf('Kit')
assert.ok(iKit > 0, 'Kit header exists')
assert.equal(ths[iKit - 1], 'Equipe')
assert.equal(ths[iKit - 2], 'Catálogo ML')
const linha = contas.slice(contas.indexOf('<!-- Catálogo ML: liga/desliga'))
assert.ok(linha.indexOf('data-coluna-equipe') < linha.indexOf('<!-- kit -->'),
  'equipe cell comes right before the kit cell')
assert.match(linha, /data-coluna-equipe\s*>\{\{ rotuloEquipes\(acc\) \}\}<\/td>/)
assert.match(contas, /Tipo ML, Catálogo ML e Equipe: definidos depois[\s\S]*?—<\/td>[\s\S]*?—<\/td>[\s\S]*?—<\/td>[\s\S]*?newAcc\.kit_number/,
  'add-account row keeps the columns aligned')

// 5) Cabeçalho no formato da Tabela de Preços: 11px, peso 600, linha 14px;
// centralizado, menos Nome e Obs (à esquerda).
const abreTh = [...cab.matchAll(/<th\b[^>]*>/g)].map((m) => m[0])
assert.ok(abreTh.length >= 10, 'achou os cabeçalhos da aba Contas')
for (const th of abreTh) {
  assert.match(th, /text-\[11px\] font-semibold leading-\[14px\]/, `formato do cabeçalho: ${th.slice(0, 80)}`)
  assert.doesNotMatch(th, /font-medium/)
}
const aEsquerda = abreTh.filter((th) => /\btext-left\b/.test(th))
assert.equal(aEsquerda.length, 2, 'só Nome e Obs à esquerda')
assert.match(cab, /text-left[^>]*>Nome<\/th>/)
assert.ok(aEsquerda.some((th) => th.includes('v-for="f in STORE_NOTE_FIELDS"')), 'Obs à esquerda')
for (const th of abreTh.filter((t) => !/\btext-left\b/.test(t))) assert.match(th, /\btext-center\b/)
// "marg" e "frete" herdam o formato do cabeçalho (sem tamanho próprio).
assert.match(cab, /<div class="text-muted-foreground flex justify-center gap-3">\s*<span>marg<\/span><span>frete<\/span>/)

// 6) Filtros: Plataforma ▾ e Equipe ▾ com caixas, "Marcar todas" e "Limpar".
const filtros = contas.slice(contas.indexOf('v-model="contasSearch"'), contas.indexOf('conta(s)\n'))
assert.doesNotMatch(filtros, /<select/, 'não são mais select')
const menuPlat = filtros.slice(filtros.indexOf('data-filtro-plataforma'), filtros.indexOf('</PopoverRoot>'))
assert.match(menuPlat, /v-for="p in platformsPresentInDept"[\s\S]*?type="checkbox"[\s\S]*?:checked="!contasPlataformasDesmarcadas\.includes\(p\)"[\s\S]*?alternarNaLista\(contasPlataformasDesmarcadas, p\)/)
assert.match(menuPlat, /@click="contasPlataformasDesmarcadas = \[\]">Marcar todas</)
assert.match(menuPlat, /@click="contasPlataformasDesmarcadas = \[\.\.\.platformsPresentInDept\]">Limpar</)
const iEquipe = filtros.indexOf('<PopoverRoot v-if="mostrarFiltroEquipe">')
assert.ok(iEquipe > 0, 'Equipe ▾ só com mais de uma opção')
const menuEq = filtros.slice(iEquipe)
assert.match(menuEq, /data-filtro-equipe[\s\S]*?v-for="o in equipesPresentesNoDept"[\s\S]*?type="checkbox"[\s\S]*?:checked="!contasEquipesDesmarcadas\.includes\(o\.valor\)"[\s\S]*?\{\{ o\.rotulo \}\}/)
assert.match(menuEq, /@click="contasEquipesDesmarcadas = \[\]">Marcar todas</)
assert.match(menuEq, /@click="contasEquipesDesmarcadas = equipesPresentesNoDept\.map\(\(o\) => o\.valor\)">Limpar</)

// Conta nova (nasce sem equipe) não some atrás de um filtro desmarcado.
assert.match(setup, /accounts\.value\.push\(created\)\s*\n(\s*\/\/.*\n)*\s*if \(!passaFiltroEquipe\(created, contasEquipesDesmarcadas\.value\)\) \{\s*contasEquipesDesmarcadas\.value = contasEquipesDesmarcadas\.value\.filter\(\(v\) => !chavesEquipe\(created\)\.includes\(v\)\)/)
assert.match(setup, /if \(contasPlataformasDesmarcadas\.value\.includes\(created\.platform\)\) \{\s*contasPlataformasDesmarcadas\.value = alternarNaLista\(contasPlataformasDesmarcadas\.value, created\.platform\)/)

console.log('pricing-contas-equipe: ok')
