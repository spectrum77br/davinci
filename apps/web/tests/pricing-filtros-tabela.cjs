// Run from apps/web: node tests/pricing-filtros-tabela.cjs
// Tabela de Preços (M-8, 18/11/2026): filtros Plataforma ▾ e Contas ▾ com
// caixas de marcar, cabeçalho igual (11px, 600, 14px, 24px por linha, só
// "Produto" à esquerda), sem o aviãozinho no nome da conta e linhas de 34px.
// Executa as funções e computeds reais da página, sem API nem DOM, e compila
// o SFC inteiro.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')

const filename = path.resolve(__dirname, '../pages/pricing/[tab].vue')
const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
assert.deepEqual(errors, [])
const setup = descriptor.scriptSetup.content
const source = ts.createSourceFile(filename, setup, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)

const js = (texto) => {
  const out = ts.transpileModule(texto, { compilerOptions: { target: ts.ScriptTarget.ES2022 }, reportDiagnostics: true })
  assert.deepEqual(out.diagnostics, [])
  return out.outputText
}
// Função de primeiro nível da página.
const funcao = (nome) => {
  const n = source.statements.find((s) => ts.isFunctionDeclaration(s) && s.name?.text === nome)
  assert.ok(n, `${nome} existe na página`)
  return js(n.getText(source))
}
// Corpo (a arrow) de um `const x = computed(() => ...)` da página.
const computado = (nome) => {
  const st = source.statements.find((s) => ts.isVariableStatement(s)
    && s.declarationList.declarations.some((d) => d.name.getText(source) === nome))
  assert.ok(st, `${nome} existe na página`)
  const dec = st.declarationList.declarations.find((d) => d.name.getText(source) === nome)
  const ini = dec.initializer
  assert.ok(ini && ts.isCallExpression(ini) && ini.expression.getText(source) === 'computed', `${nome} é computed`)
  return js(`const __f = ${ini.arguments[0].getText(source)}`)
}

// ---------- 1) chaves e rótulos
const LABELS = { amazon: 'Amazon', mercadolivre: 'Mercado Livre', shopee: 'Shopee', magalu: 'Magalu' }
const platformLabel = (p) => LABELS[p] ?? p
const nomeColuna = (a) => a.coluna ?? a.name
const ajudantes = new Function('platformLabel', 'nomeColuna', [
  funcao('ehCatalogo'), funcao('chavePlataforma'), funcao('rotuloPlataforma'), funcao('chaveConta'),
  'return { ehCatalogo, chavePlataforma, rotuloPlataforma, chaveConta }',
].join('\n'))(platformLabel, nomeColuna)
const { ehCatalogo, chavePlataforma, rotuloPlataforma, chaveConta } = ajudantes

const conta = (id, platform, name, extra = {}) => ({ id, platform, name, canal: 'kit', kit_number: 1, ...extra })
const amzAtlas = conta('a1', 'amazon', 'atlas')
const mgVita = conta('a2', 'magalu', 'vita')
const mlAtlas = conta('a3', 'mercadolivre', 'atlas')
const mlCatAtlas = conta('a4', 'mercadolivre', 'atlas catálogo', { canal: 'catalogo', coluna: 'atlas' })
const shpKia = conta('a5', 'shopee', 'kia')
const shpAtlas = conta('a6', 'shopee', 'Atlas ')
// Ordem da tabela (gridAccountsOrdenadas): Amazon, Magalu, ML kit, ML catálogo, Shopee.
const ordenadas = [amzAtlas, mgVita, mlAtlas, mlCatAtlas, shpKia, shpAtlas]

assert.equal(chavePlataforma(amzAtlas), 'amazon')
assert.equal(chavePlataforma(mlAtlas), 'ml_kit')
assert.equal(chavePlataforma(mlCatAtlas), 'ml_catalogo')
assert.equal(rotuloPlataforma(mlAtlas), 'ML Kit')
assert.equal(rotuloPlataforma(mlCatAtlas), 'ML Catálogo')
assert.equal(rotuloPlataforma(shpKia), 'Shopee')
// Conta: pelo nome da coluna (o catálogo usa o nome da conta de kit), sem
// separar maiúscula/minúscula nem espaço sobrando.
assert.equal(chaveConta(mlCatAtlas), 'atlas')
assert.equal(chaveConta(shpAtlas), 'atlas')
console.log('pricing filtros: chaves ok')

// ---------- 2) computeds e ações dos filtros, com refs falsas
const ref = (v) => ({ value: v })
function montar(contas) {
  const plataformasDesmarcadas = ref(new Set())
  const contasDesmarcadas = ref(new Set())
  const buscaContas = ref('')
  const gridAccountsOrdenadas = ref(contas)
  const comp = (nome, deps) => {
    const nomes = Object.keys(deps)
    const fn = new Function(...nomes, `${computado(nome)}\nreturn __f`)(...nomes.map((n) => deps[n]))
    return { get value() { return fn() } }
  }
  const base = { chavePlataforma, rotuloPlataforma, chaveConta, nomeColuna, platformLabel, ehCatalogo,
    plataformasDesmarcadas, contasDesmarcadas, buscaContas, gridAccountsOrdenadas }
  const gridAccounts = comp('gridAccounts', base)
  const gridPlatformOptions = comp('gridPlatformOptions', base)
  const gridContaOptions = comp('gridContaOptions', base)
  const gridContaOptionsBuscadas = comp('gridContaOptionsBuscadas', { ...base, gridContaOptions })
  const rotuloContas = comp('rotuloContas', { ...base, gridContaOptions })
  const acoes = new Function('plataformasDesmarcadas', 'contasDesmarcadas', 'gridContaOptionsBuscadas', 'gridPlatformOptions', [
    funcao('alternarFiltro'), funcao('marcarContas'), funcao('marcarPlataformas'),
    'return { alternarFiltro, marcarContas, marcarPlataformas }',
  ].join('\n'))(plataformasDesmarcadas, contasDesmarcadas, gridContaOptionsBuscadas, gridPlatformOptions)
  return { plataformasDesmarcadas, contasDesmarcadas, buscaContas, gridAccounts, gridPlatformOptions,
    gridContaOptions, gridContaOptionsBuscadas, rotuloContas, ...acoes }
}

const t = montar(ordenadas)
const ids = () => t.gridAccounts.value.map((a) => a.id)
// Começa tudo marcado: todas as colunas, na ordem da tabela.
assert.deepEqual(ids(), ['a1', 'a2', 'a3', 'a4', 'a5', 'a6'])
// Plataforma ▾: uma opção por coluna de plataforma, na ordem da tabela.
assert.deepEqual(t.gridPlatformOptions.value.map((o) => o.label), ['Amazon', 'Magalu', 'ML Kit', 'ML Catálogo', 'Shopee'])
assert.ok(t.gridPlatformOptions.value.every((o) => !o.disabled))
// Contas ▾: nomes sem repetir, em ordem alfabética.
assert.deepEqual(t.gridContaOptions.value.map((o) => o.label), ['atlas', 'kia', 'vita'])
assert.equal(t.rotuloContas.value, 'Contas')

// Desmarcar a Shopee esconde as duas colunas dela.
t.alternarFiltro('plataforma', 'shopee', false)
assert.deepEqual(ids(), ['a1', 'a2', 'a3', 'a4'])
// Só a conta "atlas" marcada (Limpar + marcar atlas): todas as colunas da atlas
// que passam na plataforma — a plataforma E a conta.
t.marcarContas(false)
assert.deepEqual(ids(), [])
assert.equal(t.rotuloContas.value, 'Contas (0)')
t.alternarFiltro('conta', 'atlas', true)
assert.deepEqual(ids(), ['a1', 'a3', 'a4'])
t.alternarFiltro('conta', 'kia', true)
assert.equal(t.rotuloContas.value, 'Contas (2)')
// Voltar a Shopee: entram a kia e a Atlas da Shopee.
t.alternarFiltro('plataforma', 'shopee', true)
assert.deepEqual(ids(), ['a1', 'a3', 'a4', 'a5', 'a6'])
// Plataforma: Limpar tira tudo; Marcar todas volta tudo.
t.marcarPlataformas(false)
assert.deepEqual(ids(), [])
t.marcarPlataformas(true)
assert.deepEqual(ids(), ['a1', 'a3', 'a4', 'a5', 'a6'])
// Busca do Contas ▾: Marcar todas / Limpar valem só para as encontradas.
t.buscaContas.value = 'vi'
assert.deepEqual(t.gridContaOptionsBuscadas.value.map((o) => o.label), ['vita'])
t.marcarContas(true)
assert.deepEqual(ids(), ['a1', 'a2', 'a3', 'a4', 'a5', 'a6'])
assert.equal(t.rotuloContas.value, 'Contas')
t.buscaContas.value = ''

// Sem nenhum catálogo ligado, "ML Catálogo" aparece apagado logo depois do ML Kit.
const semCat = montar([amzAtlas, mlAtlas, shpKia])
assert.deepEqual(semCat.gridPlatformOptions.value.map((o) => [o.value, !!o.disabled]),
  [['amazon', false], ['ml_kit', false], ['ml_catalogo', true], ['shopee', false]])
// "Limpar" não guarda a opção apagada.
semCat.marcarPlataformas(false)
assert.deepEqual([...semCat.plataformasDesmarcadas.value].sort(), ['amazon', 'ml_kit', 'shopee'])
console.log('pricing filtros: plataforma e contas ok')

// ---------- 3) script: não é lembrado (recarregar volta tudo marcado), a troca
// de departamento limpa, e o "Enviar todas" usa só as colunas visíveis.
assert.doesNotMatch(setup, /gridPlatformFilter/)
assert.match(setup, /const plataformasDesmarcadas = ref<Set<string>>\(new Set\(\)\)/)
assert.match(setup, /const contasDesmarcadas = ref<Set<string>>\(new Set\(\)\)/)
assert.doesNotMatch(setup, /(gravarPreferencia|localStorage)[^\n]*(plataformasDesmarcadas|contasDesmarcadas)/)
const iTroca = setup.indexOf('watch(department, async')
assert.ok(iTroca > 0, 'watch da troca de departamento')
const trocaDepto = setup.slice(iTroca)
assert.match(trocaDepto, /plataformasDesmarcadas\.value = new Set\(\)\s*contasDesmarcadas\.value = new Set\(\)/)
assert.match(funcao('pushAllVisible'), /for \(const a of gridAccounts\.value\)/)
console.log('pricing filtros: script ok')

// ---------- 4) template
const tpl = descriptor.template.content
const iTabela = tpl.indexOf(`<section v-else-if="tab === 'tabela'"`)
assert.ok(iTabela > 0, 'aba Tabela de Preços no template')
const tabela = tpl.slice(iTabela, tpl.indexOf('</section>', iTabela))
// Barra na ordem do pedido (sem os comentários, que citam os botões).
const barra = tabela.slice(0, tabela.indexOf('<table')).replace(/<!--[\s\S]*?-->/g, '')
const ordem = ['v-model="gridSearch"', 'data-filtro="plataforma"', 'data-filtro="contas"', 'data-acao="layout"',
  'handleExportExcel', 'pushAllAndClose', 'Recarregar', 'data-prazo-reposicao', 'data-acao="margem"',
  'data-acao="conferir-anuncios"']
let antes = -1
for (const marca of ordem) {
  const i = barra.indexOf(marca)
  assert.ok(i > antes, `barra: ${marca} na ordem`)
  antes = i
}
assert.doesNotMatch(barra, /<select/, 'Plataforma não é mais select')
assert.match(barra, /data-menu-plataforma[\s\S]*?Marcar todas[\s\S]*?Limpar[\s\S]*?type="checkbox"/)
assert.match(barra, /data-menu-contas[\s\S]*?v-model="buscaContas"[\s\S]*?Marcar todas[\s\S]*?Limpar[\s\S]*?type="checkbox"/)
assert.match(barra, /\{\{ rotuloContas \}\}/)

// Cabeçalho: sem aviãozinho; todo <th> com 24px e o mesmo texto; só Produto à esquerda.
const cab = tabela.slice(tabela.indexOf('<thead'), tabela.indexOf('</thead>'))
assert.ok(!cab.includes('<Send') && !cab.includes('pushAccountColumn'), 'aviãozinho saiu do cabeçalho')
assert.doesNotMatch(cab, /text-\[10px\]|text-xs|font-bold/)
const ths = [...cab.matchAll(/<th\b[\s\S]*?>/g)].map((m) => m[0])
assert.ok(ths.length >= 10)
for (const th of ths) {
  assert.match(th, /\bh-6\b/, `th com 24px: ${th.slice(0, 60)}`)
  if (th.trimEnd().endsWith('/>')) continue // canto vazio das colunas presas
  assert.match(th, /text-\[11px\] font-semibold leading-\[14px\]/, `texto do th: ${th.slice(0, 60)}`)
  assert.match(th, /align-middle/)
}
const esquerda = ths.filter((th) => th.includes('text-left'))
assert.equal(esquerda.length, 1)
assert.match(cab, /text-left[^>]*>Produto<\/th>/)
assert.match(cab, /:colspan="group\.accounts\.length"/)

// Linhas de 34px e o (!) no pé da célula, fora do fluxo.
const corpo = tabela.slice(tabela.indexOf('<tbody'))
assert.match(corpo, /class="h-\[34px\] border-t[^"]*"\s*data-linha-grade/)
assert.match(corpo, /class="absolute inset-x-1 bottom-px [^"]*text-\[9px\][^"]*"\s*:title="tituloDivergencia/)
console.log('pricing filtros: template ok')

// ---------- 5) o SFC inteiro compila (script + template).
const script = compileScript(descriptor, { id: 'pricing-tab', inlineTemplate: false })
const out = compileTemplate({
  source: tpl, filename, id: 'pricing-tab',
  compilerOptions: { isTS: true, bindingMetadata: script.bindings },
})
assert.deepEqual(out.errors, [], 'template compila')
for (const b of ['gridPlatformOptions', 'gridContaOptionsBuscadas', 'rotuloContas', 'alternarFiltro', 'marcarContas',
  'marcarPlataformas', 'plataformasDesmarcadas', 'contasDesmarcadas', 'buscaContas']) {
  assert.ok(script.bindings[b], `${b} exposto ao template`)
}
console.log('pricing-filtros-tabela: ok')
