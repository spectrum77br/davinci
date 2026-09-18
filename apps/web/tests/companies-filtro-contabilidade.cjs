// Run from apps/web: node tests/companies-filtro-contabilidade.cjs
// Cadastros › Empresas (18/09/2026): filtro por contabilidade com a QUANTIDADE
// de empresas em cada uma. Duas travas que importam:
//  - a contagem sai da grade INTEIRA, não das linhas já filtradas; se saísse do
//    resultado filtrado, o número mudaria a cada clique e deixaria de dizer
//    quantas empresas existem em cada contabilidade;
//  - empresa com a contabilidade em branco tem opção própria ("sem
//    contabilidade"), que não pode colidir com a opção "todas".
const assert = require('node:assert/strict')
const fs = require('node:fs')
const { createRequire } = require('node:module')
const path = require('node:path')
const requireWeb = createRequire(path.resolve(__dirname, '../package.json'))
const { ref, computed } = requireWeb('vue')
const ts = requireWeb('typescript')
const { parse, compileTemplate } = requireWeb('vue/compiler-sfc')

const filename = path.resolve(__dirname, '../pages/companies/index.vue')
const source = fs.readFileSync(filename, 'utf8')
const { descriptor, errors } = parse(source, { filename })
assert.deepEqual(errors, [])
const template = compileTemplate({ source: descriptor.template.content, filename, id: 'companies-contab' })
assert.deepEqual(template.errors, [])

// --- peças exigidas do template
{
  const tpl = descriptor.template.content
  assert.match(tpl, /v-model="filterContabilidade"/, 'seletor de contabilidade')
  assert.match(tpl, /todas contabilidades/, 'opção que limpa o filtro')
  assert.match(tpl, /\(\{\{ c\.total \}\}\)/, 'cada opção mostra a quantidade')
  assert.match(tpl, /sem contabilidade/, 'opção para quem está em branco')
}

// --- lógica: reproduz as duas peças do script com a mesma implementação
const script = descriptor.scriptSetup.content
// Extrai SÓ os três blocos que interessam, sem arrastar as outras declarações
// da página (que colidiriam com os parâmetros do harness).
const bloco = (de, ate) => script.slice(script.indexOf(de), script.indexOf(ate))
const trecho = [
  bloco('const SEM_CONTABILIDADE', '\nconst search'),
  bloco('const contabilidadeOpts', 'const responsaveisOpts'),
  bloco('const filteredRows', 'const draft ='),
].join('\n')
const js = ts.transpileModule(trecho, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText

function empresa(apelido, contabilidade, extra = {}) {
  return { company: { apelido, razao_social: apelido, cnpj: '1', contabilidade, uf: 'SP', responsavel_nome: 'x', ...extra }, stores: {} }
}
const LINHAS = [
  empresa('a', 'CT'), empresa('b', 'CT'), empresa('c', 'CT'),
  empresa('d', 'JS'), empresa('e', 'EUA'), empresa('f', null), empresa('g', '  '),
]

const grid = ref({ marketplaces: [], rows: LINHAS })
const filterUf = ref(''), filterMk = ref(''), filterResponsavel = ref('')
const filterContabilidade = ref(''), search = ref('')
const factory = new Function(
  'ref', 'computed', 'grid', 'filterUf', 'filterMk', 'filterResponsavel', 'filterContabilidade', 'search',
  js + '\nreturn { SEM_CONTABILIDADE, contabilidadeOpts, filteredRows }',
)
const s = factory(ref, computed, grid, filterUf, filterMk, filterResponsavel, filterContabilidade, search)

// contagem por contabilidade, ordenada da maior para a menor
{
  const opts = s.contabilidadeOpts.value
  const mapa = Object.fromEntries(opts.map(o => [o.valor, o.total]))
  assert.equal(mapa.CT, 3)
  assert.equal(mapa.JS, 1)
  assert.equal(mapa.EUA, 1)
  assert.equal(mapa[s.SEM_CONTABILIDADE], 2, 'null e string de espaços contam como sem contabilidade')
  assert.equal(opts[0].valor, 'CT', 'a maior vem primeiro')
}

// filtrar por uma contabilidade
{
  filterContabilidade.value = 'CT'
  assert.equal(s.filteredRows.value.length, 3)
  assert.ok(s.filteredRows.value.every(r => r.company.contabilidade === 'CT'))
  // A CONTAGEM NÃO MUDA com o filtro aplicado — é o ponto do marcador.
  const mapa = Object.fromEntries(s.contabilidadeOpts.value.map(o => [o.valor, o.total]))
  assert.equal(mapa.CT, 3)
  assert.equal(mapa.JS, 1, 'a contagem continua mostrando o total de cada uma')
}

// filtrar pelas que estão em branco
{
  filterContabilidade.value = s.SEM_CONTABILIDADE
  assert.equal(s.filteredRows.value.length, 2)
  assert.ok(s.filteredRows.value.every(r => !(r.company.contabilidade || '').trim()))
}

// a busca livre também acha por contabilidade (foi o movimento natural do dono)
{
  filterContabilidade.value = ''
  search.value = 'ct'
  assert.equal(s.filteredRows.value.length, 3, 'digitar CT na busca acha as 3 de CT')
  search.value = 'eua'
  assert.equal(s.filteredRows.value.length, 1)
  search.value = ''
}

// limpar devolve todas, e o filtro convive com os outros
{
  filterContabilidade.value = ''
  assert.equal(s.filteredRows.value.length, LINHAS.length)
  filterContabilidade.value = 'CT'
  search.value = 'a'
  assert.equal(s.filteredRows.value.length, 1, 'contabilidade + busca somam')
  search.value = ''
}

console.log('PASS: filtro de contabilidade; contagem por opção estável sob filtro; opção "sem contabilidade"; combinação com busca')
