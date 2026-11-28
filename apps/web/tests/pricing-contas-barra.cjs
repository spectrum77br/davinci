// Run from apps/web: node tests/pricing-contas-barra.cjs
// M-51 (25/11/2026): a aba Contas perdeu o título "Contas de Venda — …" e a
// frase de baixo; ficou uma barra só, no estilo da Tabela de Preços:
// Buscar | Plataforma ▾ | Contas ▾ | Layout ▾ | Equipes ▾ | Excel … N conta(s) |
// Vincular Integrações | + Adicionar Conta. Junto com as M-52..M-57 (conta do
// ML em uma linha, sem Tipo ML, Desconto e Custo total, Catálogo Ativo/Inativo).
// Executa os helpers reais da página
// (filtro Contas, Layout, linhas do Excel) e a lib do .xlsx (abre o zip gerado
// e confere os XML). Compila o SFC inteiro no fim.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const zlib = require('node:zlib')
const ts = require('typescript')
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')

// ---------- 1) lib/planilhaXlsx.ts
const fonteLib = fs.readFileSync(path.join(__dirname, '..', 'lib', 'planilhaXlsx.ts'), 'utf8')
assert.ok(!/^import /m.test(fonteLib), 'lib sem import (roda solta aqui)')
const X = {}
new Function('exports', 'require', ts.transpileModule(fonteLib, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText)(X, () => { throw new Error('lib/planilhaXlsx.ts não pode importar nada') })

assert.equal(X.crc32(new TextEncoder().encode('123456789')), 0xcbf43926, 'CRC-32 padrão do zip')
assert.equal(X.crc32(new Uint8Array()), 0)
assert.deepEqual([0, 1, 25, 26, 27, 51, 52, 701, 702].map(X.letraColuna),
  ['A', 'B', 'Z', 'AA', 'AB', 'AZ', 'BA', 'ZZ', 'AAA'])
assert.equal(X.escaparXml('a & b <c> "d"'), 'a &amp; b &lt;c&gt; &quot;d&quot;')
assert.equal(X.escaparXml(`x${String.fromCharCode(1)}y${String.fromCharCode(11)}z\tw\n`), 'xyz\tw\n',
  'tira caracteres de controle (o Excel recusa), deixa tab e quebra de linha')
assert.equal(X.TIPO_XLSX, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

// Abre o zip "na mão": diretório central, cabeçalho local, CRC de cada arquivo.
function abrirZip(bytes) {
  const buf = Buffer.from(bytes.buffer, bytes.byteOffset, bytes.length)
  const fim = buf.length - 22
  assert.equal(buf.readUInt32LE(fim), 0x06054b50, 'fim do diretório central')
  const n = buf.readUInt16LE(fim + 10)
  assert.equal(buf.readUInt16LE(fim + 8), n)
  const tamCentral = buf.readUInt32LE(fim + 12)
  const iniCentral = buf.readUInt32LE(fim + 16)
  assert.equal(iniCentral + tamCentral, fim, 'diretório central logo antes do fim')
  const arquivos = {}
  let p = iniCentral
  for (let i = 0; i < n; i++) {
    assert.equal(buf.readUInt32LE(p), 0x02014b50, 'entrada do diretório central')
    assert.equal(buf.readUInt16LE(p + 10), 0, 'sem compressão')
    const crc = buf.readUInt32LE(p + 16)
    const tam = buf.readUInt32LE(p + 20)
    assert.equal(buf.readUInt32LE(p + 24), tam)
    const lenNome = buf.readUInt16LE(p + 28)
    const extra = buf.readUInt16LE(p + 30)
    const coment = buf.readUInt16LE(p + 32)
    const local = buf.readUInt32LE(p + 42)
    const nome = buf.toString('utf8', p + 46, p + 46 + lenNome)
    assert.equal(buf.readUInt32LE(local), 0x04034b50, `cabeçalho local de ${nome}`)
    assert.equal(buf.readUInt32LE(local + 14), crc, `CRC igual nos dois cabeçalhos (${nome})`)
    assert.equal(buf.readUInt32LE(local + 18), tam)
    const lenNomeL = buf.readUInt16LE(local + 26)
    const extraL = buf.readUInt16LE(local + 28)
    assert.equal(buf.toString('utf8', local + 30, local + 30 + lenNomeL), nome)
    const ini = local + 30 + lenNomeL + extraL
    const dados = buf.subarray(ini, ini + tam)
    assert.equal(X.crc32(new Uint8Array(dados)), crc, `CRC de ${nome}`)
    if (typeof zlib.crc32 === 'function') assert.equal(zlib.crc32(dados), crc, `CRC de ${nome} (zlib)`)
    arquivos[nome] = dados.toString('utf8')
    p += 46 + lenNome + extra + coment
  }
  assert.equal(p, fim)
  return arquivos
}

const z = abrirZip(X.gerarXlsx([
  ['Nome', 'Kit', 'Obs', 'Comissão'],
  ['atlas', 3, 'R&D <ok>', '16%'],
  ['bravo', null, '', undefined],
], 'Contas'))
assert.deepEqual(Object.keys(z).sort(), [
  '[Content_Types].xml', '_rels/.rels', 'xl/_rels/workbook.xml.rels', 'xl/styles.xml',
  'xl/workbook.xml', 'xl/worksheets/sheet1.xml',
])
const sheet = z['xl/worksheets/sheet1.xml']
assert.ok(sheet.includes('<row r="1"><c r="A1" s="1" t="inlineStr"><is><t xml:space="preserve">Nome</t></is></c>'),
  'cabeçalho em negrito (estilo 1)')
assert.ok(sheet.includes('<t xml:space="preserve">Comissão</t>'), 'acento sai certo (UTF-8)')
assert.ok(sheet.includes('<c r="B2"><v>3</v></c>'), 'número vira número no Excel')
assert.ok(sheet.includes('<c r="C2" t="inlineStr"><is><t xml:space="preserve">R&amp;D &lt;ok&gt;</t></is></c>'))
assert.ok(sheet.includes('<row r="3"><c r="A3" t="inlineStr"><is><t xml:space="preserve">bravo</t></is></c></row>'),
  'vazio não vira célula')
assert.match(z['xl/workbook.xml'], /<sheet name="Contas" sheetId="1" r:id="rId1"\/>/)
assert.match(z['xl/_rels/workbook.xml.rels'], /Target="worksheets\/sheet1\.xml"/)
assert.match(z['[Content_Types].xml'], /PartName="\/xl\/worksheets\/sheet1\.xml"/)
assert.match(z['_rels/.rels'], /Target="xl\/workbook\.xml"/)
console.log('pricing contas barra: xlsx ok')

// ---------- 2) helpers reais da página
const filename = path.resolve(__dirname, '../pages/pricing/[tab].vue')
const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
assert.deepEqual(errors, [])
const setup = descriptor.scriptSetup.content
const source = ts.createSourceFile(filename, setup, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
const pega = (nome) => {
  const node = source.statements.find((c) =>
    (ts.isFunctionDeclaration(c) && c.name?.text === nome)
    || (ts.isVariableStatement(c) && c.declarationList.declarations.some((d) => d.name.getText(source) === nome)))
  assert.ok(node, `real page helper ${nome} exists`)
  return node.getText(source)
}
const js = (texto) => {
  const out = ts.transpileModule(texto, { compilerOptions: { target: ts.ScriptTarget.ES2022 }, reportDiagnostics: true })
  assert.deepEqual(out.diagnostics, [])
  return out.outputText
}
const computed = (fn) => ({ get value() { return fn() } })
const ref = (v) => ({ value: v })

const isAdmin = ref(true)
const accountsCurrent = ref([])
const contasSearch = ref('')
const contasPlataformasDesmarcadas = ref([])
const contasEquipesDesmarcadas = ref([])
const nomes = ['SEM_EQUIPE', 'contasNomesDesmarcados', 'buscaNomesContas', 'chaveNomeConta', 'alternarNaLista',
  'equipesDaConta', 'numeroDaEquipe', 'rotuloEquipes', 'chavesEquipe', 'passaFiltroEquipe',
  'opcoesNomesContas', 'opcoesNomesContasBuscadas', 'rotuloNomesContas', 'marcarNomesContas', 'accountsFiltered',
  'COLUNAS_LAYOUT_CONTAS', 'colunasOcultasContas', 'opcoesLayoutContas', 'mostraColunaConta', 'lerColunasOcultasContas']
const P = new Function('computed', 'ref', 'isAdmin', 'accountsCurrent', 'contasSearch',
  'contasPlataformasDesmarcadas', 'contasEquipesDesmarcadas',
  `${js(nomes.map(pega).join('\n'))}\nreturn { ${nomes.join(', ')} }`,
)(computed, ref, isAdmin, accountsCurrent, contasSearch, contasPlataformasDesmarcadas, contasEquipesDesmarcadas)

// Contas ▾: pelo nome, sem repetir ("atlas" vale para a atlas de todas as plataformas).
const conta = (name, platform, equipes = []) => ({ name, platform, email: null, equipes })
accountsCurrent.value = [
  conta('Atlas', 'mercadolivre', [{ numero: 101, nome: 'Equipe 1' }]),
  conta('atlas ', 'shopee'),
  conta('Bravo', 'shopee'),
  conta('Ceres', 'amazon'),
]
assert.deepEqual(P.opcoesNomesContas.value, [
  { valor: 'atlas', rotulo: 'Atlas' }, { valor: 'bravo', rotulo: 'Bravo' }, { valor: 'ceres', rotulo: 'Ceres' },
])
const filtradas = () => P.accountsFiltered.value.map((a) => `${a.name.trim()}@${a.platform}`)
assert.equal(P.accountsFiltered.value.length, 4, 'tudo marcado = sem filtro')
assert.equal(P.rotuloNomesContas.value, 'Contas')
P.contasNomesDesmarcados.value = P.alternarNaLista(P.contasNomesDesmarcados.value, 'atlas')
assert.deepEqual(filtradas(), ['Bravo@shopee', 'Ceres@amazon'], 'desmarcar atlas tira a atlas de todas as plataformas')
assert.equal(P.rotuloNomesContas.value, 'Contas (2)')
// Soma com a Plataforma e com a busca da barra.
contasPlataformasDesmarcadas.value = ['amazon']
assert.deepEqual(filtradas(), ['Bravo@shopee'])
contasPlataformasDesmarcadas.value = []
contasSearch.value = 'cer'
assert.deepEqual(filtradas(), ['Ceres@amazon'])
contasSearch.value = ''
// "Marcar todas" / "Limpar" valem para a lista à vista (com a busca do menu).
P.buscaNomesContas.value = 'br'
assert.deepEqual(P.opcoesNomesContasBuscadas.value, [{ valor: 'bravo', rotulo: 'Bravo' }])
P.marcarNomesContas(false)
assert.deepEqual([...P.contasNomesDesmarcados.value].sort(), ['atlas', 'bravo'])
P.marcarNomesContas(true)
assert.deepEqual(P.contasNomesDesmarcados.value, ['atlas'], 'Marcar todas com busca só volta as encontradas')
P.buscaNomesContas.value = ''
P.marcarNomesContas(false)
assert.equal(P.accountsFiltered.value.length, 0, 'Limpar tira todas')
P.marcarNomesContas(true)
assert.deepEqual(P.contasNomesDesmarcados.value, [])
assert.equal(P.accountsFiltered.value.length, 4)
// Ao recarregar a página, tudo marcado (o filtro não fica salvo).
assert.match(setup, /const contasNomesDesmarcados = ref<string\[\]>\(\[\]\)/)
assert.doesNotMatch(setup, /(gravarPreferencia|localStorage)[^\n]*contasNomesDesmarcados/)
// Conta nova com o nome de uma desmarcada não some atrás do filtro.
assert.match(pega('submitNewAcc'), /if \(contasNomesDesmarcados\.value\.includes\(chaveNomeConta\(created\)\)\) \{\s*contasNomesDesmarcados\.value = alternarNaLista\(contasNomesDesmarcados\.value, chaveNomeConta\(created\)\)/)
console.log('pricing contas barra: filtro Contas ok')

// Layout ▾: Catálogo ML, Equipe, Kit, Comissão e Obs; Kit e Comissão só ADM.
const rotulosLayout = () => P.opcoesLayoutContas.value.map((c) => c.rotulo)
assert.deepEqual(rotulosLayout(), ['Catálogo ML', 'Equipe', 'Kit', 'Comissão', 'Obs'])
isAdmin.value = false
assert.deepEqual(rotulosLayout(), ['Catálogo ML', 'Equipe', 'Obs'], 'quem não é ADM não vê Kit e Comissão')
isAdmin.value = true
assert.deepEqual(P.colunasOcultasContas.value, [], 'abre com tudo marcado')
for (const c of P.COLUNAS_LAYOUT_CONTAS) assert.equal(P.mostraColunaConta(c.chave), true)
P.colunasOcultasContas.value = ['obs']
assert.equal(P.mostraColunaConta('obs'), false, 'desmarcar Obs esconde a Obs')
assert.equal(P.mostraColunaConta('kit'), true)
P.colunasOcultasContas.value = []
assert.deepEqual(P.lerColunasOcultasContas('kit, obs,xyz,'), ['kit', 'obs'], 'só as chaves que existem')
assert.deepEqual(P.lerColunasOcultasContas(null), [])
// Guardado como o Layout da Tabela de Preços: neste navegador, lido ao montar.
assert.match(setup, /const CHAVE_COLUNAS_OCULTAS_CONTAS = 'precos\.contas\.colunasOcultas'/)
assert.match(setup, /colunasOcultasContas\.value = lerColunasOcultasContas\(lerPreferencia\(CHAVE_COLUNAS_OCULTAS_CONTAS\)\)\s*watch\(colunasOcultasContas, \(v\) => gravarPreferencia\(CHAVE_COLUNAS_OCULTAS_CONTAS, v\.join\(','\)\)\)/)
console.log('pricing contas barra: Layout ok')

// Contas ▾ com a conta do ML de Clássico + Premium (M-53): o par é UMA conta,
// então o nome aparece uma vez e o contador conta uma.
const accounts = ref([])
const parBase = new Function('computed', 'accounts', `${js(['ehCatalogo', 'ordemTipo', 'linhasPorPar', 'parDe',
  'escondidaNaAba', 'accountsByDept'].map(pega).join('\n'))}\nreturn { parDe, accountsByDept }`)(computed, accounts)
const mlLinha = (id, name, listing_type, par_id, extra = {}) => ({
  id, name, platform: 'mercadolivre', department: 'mala', canal: 'kit', listing_type, par_id,
  sort_order: 0, email: null, equipes: [], ...extra,
})
accounts.value = [
  mlLinha('fpC', 'forpaper', 'ml classico', 'p1'),
  mlLinha('fpP', 'forpaper', 'ml premium', 'p1'),
  mlLinha('fpCat', 'forpaper', 'ml premium', null, { canal: 'catalogo' }),
  { ...mlLinha('bv', 'bravo', null, null), platform: 'shopee' },
]
accountsCurrent.value = parBase.accountsByDept.value.mala
assert.deepEqual(P.opcoesNomesContas.value, [{ valor: 'bravo', rotulo: 'bravo' }, { valor: 'forpaper', rotulo: 'forpaper' }],
  'forpaper uma vez (Clássico + Premium = uma conta; a coluna de catálogo não é conta)')
assert.deepEqual(P.accountsFiltered.value.map((a) => a.id), ['bv', 'fpC'], 'o par conta uma vez')
P.contasNomesDesmarcados.value = ['forpaper']
assert.deepEqual(P.accountsFiltered.value.map((a) => a.id), ['bv'], 'desmarcar forpaper tira o par inteiro')
P.contasNomesDesmarcados.value = []
console.log('pricing contas barra: filtro Contas com o par do ML ok')

// Excel: as contas da tela, as colunas à vista e na ordem da tabela (M-52..M-57);
// sem Kit/Comissão/margens/fretes para quem não é ADM. M-85: "Margem mínima (%)"
// depois do "Custo total (%)", só para o ADM.
const nomesExcel = ['MAX_CATEGORIAS_VENDA', 'STORE_NOTE_FIELDS', 'camposAnotacao', 'fmtCommission', 'fmtMargin', 'fmtShipping',
  'textoComissao', 'textoComissaoPar', 'tipoDaContaSolta', 'pctGeralAds', 'linhasExcelContas']
const fpC = mlLinha('fpC', 'forpaper', 'ml classico', 'p1', {
  kit_number: 2, commission: '0.14', margin1: '0.25', shipping1: '20', margin2: null, shipping2: null,
  afiliado_pct: '4.50', desconto_pct: '10.00', custo_total_pct: '20.00', margem_minima_pct: '25.00',
  observation: 'ver & conferir', equipes: [{ numero: 101, nome: 'Equipe 1' }],
})
const fpP = mlLinha('fpP', 'forpaper', 'ml premium', 'p1', { kit_number: 2, commission: '0.19' })
const solo = mlLinha('solo', 'solo', 'ml premium', null, {
  kit_number: 1, commission: '0.16', margin1: null, shipping1: null, margin2: null, shipping2: null,
  afiliado_pct: null, desconto_pct: null, custo_total_pct: null, observation: null,
})
const bravo = {
  id: 'b1', name: 'Bravo', platform: 'shopee', kit_number: null, commission: null,
  margin1: null, shipping1: null, margin2: null, shipping2: null,
  afiliado_pct: null, desconto_pct: '0', custo_total_pct: null, observation: null, equipes: [],
}
accounts.value = [fpC, fpP, solo, bravo]
const excelDepartamento = ref('mala')
const excelGrupos = ref([{ platform: 'mercadolivre', rows: [fpC, solo] }, { platform: 'shopee', rows: [bravo] }])
const linhasExcelContas = new Function('computed', 'isAdmin', 'department', 'TYPE_HEADERS', 'accountsGrouped', 'mostraColunaConta',
  'platformLabel', 'parDe', 'ehCatalogo', 'rotuloTipo', 'podeTerCatalogo', 'catalogoAtivo', 'rotuloEquipes', 'ads', 'adsDa',
  `${js(nomesExcel.map(pega).join('\n'))}\nreturn linhasExcelContas`,
)(
  computed, isAdmin, excelDepartamento, ref({
    mala: ['Acessórios', '12"'],
    // M-93: o exemplo do pedido — Eletro com 10 categorias de venda.
    eletro: ['Acessórios', 'airfryer', 'barbecue', 'slush', '5', 'cafeteira', 'cooktop', 'cookware', 'purificador',
      'smart cooking'],
    // Mais que 10 nunca vem do servidor; se viesse, o Excel para no par 10.
    teste: Array.from({ length: 11 }, (_, i) => `c${i + 1}`),
  }),
  excelGrupos,
  P.mostraColunaConta,
  (p) => ({ mercadolivre: 'ML', shopee: 'Shopee', amazon: 'Amazon' })[p],
  parBase.parDe,
  (a) => a.canal === 'catalogo',
  (a) => (a.listing_type || '').replace(/^ml /, '').replace('classico', 'clássico'),
  (a) => a.platform === 'mercadolivre',
  (a) => a.id === 'fpC',
  P.rotuloEquipes,
  ref({ contas: {} }),
  (id) => (id === 'fpC' ? { ads_geral_ligada: true, pode_gastar_pct: 10 } : { ads_geral_ligada: false, pode_gastar_pct: 3 }),
)
const cabTodo = ['Plataforma', 'Conta', 'Catálogo ML', 'Equipe', 'Kit', 'Comissão',
  'Acessórios marg', 'Acessórios frete', '12" marg', '12" frete',
  'Afiliado (%)', 'Desconto (%)', 'Ads (%)', 'Custo total (%)', 'Margem mínima (%)', 'Obs']
assert.deepEqual(linhasExcelContas(), [
  cabTodo,
  ['ML', 'forpaper', 'Ativo', '1', 2, '14% · 19%', '25.0%', '20', '', '', 4.5, 10, 10, 20, 25, 'ver & conferir'],
  ['ML Premium', 'solo', 'Inativo', 'Sem equipe', 1, '16%', '', '', '', '', '', '', '', '', '', ''],
  ['Shopee', 'Bravo', '', 'Sem equipe', '', '', '', '', '', '', '', 0, '', '', '', ''],
], 'ADM com tudo à mostra: o par do ML numa linha, comissão "14% · 19%" como na tela')
for (const fora of ['Tipo ML', 'Desconto até', 'Cupom até', 'Oferta limite', 'Nome']) {
  assert.ok(!cabTodo.includes(fora), `coluna removida não volta no Excel: ${fora}`)
}
// A ordem do Excel = a ordem do cabeçalho da tabela.
{
  const tplC = descriptor.template.content
  const cabTpl = tplC.slice(tplC.indexOf("tab === 'contas'"), tplC.indexOf("tab === 'produtos'"))
  const cabHtml = cabTpl.slice(0, cabTpl.indexOf('</thead>'))
  const ths = [...cabHtml.matchAll(/<th[\s\S]*?>([^<]*)<\/th>/g)].map((m) => m[1].trim()).filter(Boolean)
  assert.deepEqual(ths, ['Plataforma', 'Conta', 'Catálogo ML', 'Equipe', 'Kit', 'Comissão'])
  // As anotações: o título em linhas (Margem mínima, M-85) ou o label.
  assert.match(cabHtml, /v-for="f in camposAnotacao"[\s\S]*?><template v-if="f\.cabecalho"><span v-for="parte in f\.cabecalho" :key="parte" class="block">\{\{ parte \}\}<\/span><\/template><template v-else>\{\{ f\.label \}\}<\/template><\/th>/)
  assert.deepEqual(pega('STORE_NOTE_FIELDS').match(/label: '[^']+'/g),
    ["label: 'Afiliado'", "label: 'Desconto'", "label: 'Ads'", "label: 'Custo total'", "label: 'Margem mínima'", "label: 'Obs'"])
}
P.colunasOcultasContas.value = ['kit', 'obs']
assert.deepEqual(linhasExcelContas()[0], ['Plataforma', 'Conta', 'Catálogo ML', 'Equipe', 'Comissão',
  'Acessórios marg', 'Acessórios frete', '12" marg', '12" frete',
  'Afiliado (%)', 'Desconto (%)', 'Ads (%)', 'Custo total (%)', 'Margem mínima (%)'], 'segue o Layout')
P.colunasOcultasContas.value = ['comissao', 'catalogo', 'equipe']
assert.deepEqual(linhasExcelContas()[1], ['ML', 'forpaper', 2, '25.0%', '20', '', '', 4.5, 10, 10, 20, 25, 'ver & conferir'])
isAdmin.value = false
P.colunasOcultasContas.value = ['catalogo']
const semAdm = linhasExcelContas()
assert.deepEqual(semAdm[0], ['Plataforma', 'Conta', 'Equipe', 'Afiliado (%)', 'Desconto (%)', 'Ads (%)',
  'Custo total (%)', 'Obs'], 'sem ADM: sem Kit, Comissão, margens e fretes (Custo total todos veem)')
assert.deepEqual(semAdm[1], ['ML', 'forpaper', '1', 4.5, 10, 10, 20, 'ver & conferir'])
assert.ok(!/14%|19%|25\.0%/.test(JSON.stringify(semAdm)), 'nada de comissão ou margem')
assert.ok(!JSON.stringify(semAdm).includes('Margem mínima'), 'sem ADM: sem a Margem mínima (M-85)')
isAdmin.value = true
P.colunasOcultasContas.value = []
// M-93: Eletro com 10 categorias — um par marg/frete por categoria ativa, na ordem; a
// Amazon atlas continua com 53,0% / 30 nas cinco primeiras e vazio nas cinco novas.
{
  const atlas = {
    id: 'atlas', name: 'atlas', platform: 'amazon', department: 'eletro', kit_number: 1, commission: '0.15',
    ...Object.fromEntries([1, 2, 3, 4, 5].flatMap((n) => [[`margin${n}`, '0.5300'], [`shipping${n}`, '30.00']])),
    margin6: null, shipping6: null, margin7: null, shipping7: null, margin8: null, shipping8: null,
    margin9: null, shipping9: null, margin10: null, shipping10: null,
    afiliado_pct: null, desconto_pct: null, custo_total_pct: null, margem_minima_pct: null, observation: null, equipes: [],
  }
  excelDepartamento.value = 'eletro'
  excelGrupos.value = [{ platform: 'amazon', rows: [atlas] }]
  P.colunasOcultasContas.value = ['catalogo', 'equipe', 'kit', 'comissao', 'obs']
  const [cab, linha] = linhasExcelContas()
  const nomes = ['Acessórios', 'airfryer', 'barbecue', 'slush', '5', 'cafeteira', 'cooktop', 'cookware', 'purificador',
    'smart cooking']
  assert.deepEqual(cab, ['Plataforma', 'Conta', ...nomes.flatMap((n) => [`${n} marg`, `${n} frete`]),
    'Afiliado (%)', 'Desconto (%)', 'Ads (%)', 'Custo total (%)', 'Margem mínima (%)'], 'M-93: 10 pares, na ordem')
  assert.deepEqual(linha.slice(0, 22), ['Amazon', 'atlas',
    ...Array(5).fill(['53.0%', '30']).flat(), ...Array(10).fill('')], 'as cinco novas em branco')
  // A posição 8 preenchida sai no par da 8ª categoria (cookware), não noutro lugar.
  atlas.margin8 = '0.4000'
  atlas.shipping8 = '25'
  const l8 = linhasExcelContas()[1]
  assert.equal(l8[2 + 7 * 2], '40.0%')
  assert.equal(l8[2 + 7 * 2 + 1], '25')
  // Mais que 10 (nunca vem do servidor): para no 10º par.
  excelDepartamento.value = 'teste'
  assert.equal(linhasExcelContas()[0].filter((c) => / marg$/.test(String(c))).length, 10)
  excelDepartamento.value = 'mala'
  excelGrupos.value = [{ platform: 'mercadolivre', rows: [fpC, solo] }, { platform: 'shopee', rows: [bravo] }]
  P.colunasOcultasContas.value = []
}
const exportar = pega('exportarExcelContas')
assert.match(exportar, /if \(!accountsFiltered\.value\.length\) return/)
assert.match(exportar, /gerarXlsx\(linhasExcelContas\(\), 'Contas'\)/)
assert.match(exportar, /type: TIPO_XLSX/)
assert.match(exportar, /a\.download = `contas-\$\{department\.value\}-\$\{isoToday\(\)\}\.xlsx`/)
// Na ordem da tabela (busca e filtros já aplicados).
assert.match(pega('linhasExcelContas'), /for \(const acc of accountsGrouped\.value\.flatMap\(\(g\) => g\.rows\)\)/)
console.log('pricing contas barra: Excel ok')

// ---------- 3) template
const tpl = descriptor.template.content
const contas = tpl.slice(tpl.indexOf("tab === 'contas'"), tpl.indexOf("tab === 'produtos'"))
assert.doesNotMatch(contas, /Contas de Venda/, 'o título saiu')
assert.doesNotMatch(contas, /clique para editar/, 'a frase saiu')
const antesDaBarra = contas.slice(0, contas.indexOf('data-barra-contas'))
assert.doesNotMatch(antesDaBarra, /<h3|<p\b|<button/, 'nada antes da barra')
const barra = contas.slice(contas.indexOf('data-barra-contas'), contas.indexOf('<div v-if="accountsErr"'))
  .replace(/<!--[\s\S]*?-->/g, '')
// M-104 (27/11/2026): Equipes ▾ antes do Layout ▾, e o Layout logo antes do Excel.
const ordem = ['v-model="contasSearch"', 'data-filtro-plataforma', 'data-filtro-contas', 'data-filtro-equipe',
  'data-acao="layout-contas"', '@click="exportarExcelContas"', '{{ accountsFiltered.length }} conta(s)',
  '@click="autoMatchAccounts"', '@click="openAddAcc"']
let antes = -1
for (const marca of ordem) {
  const i = barra.indexOf(marca)
  assert.ok(i > antes, `barra: ${marca} na ordem`)
  antes = i
}
// M-104: nada entre o Layout ▾ e o Excel além do próprio menu do Layout (que
// vem junto do botão, no lugar novo).
{
  const iExcel = barra.lastIndexOf('<button', barra.indexOf('@click="exportarExcelContas"'))
  const entre = barra.slice(barra.indexOf('data-acao="layout-contas"'), iExcel)
  assert.ok(entre.includes('data-menu-layout-contas'), 'o menu do Layout está junto do botão')
  assert.ok(!entre.includes('<PopoverRoot') && !/class="btn /.test(entre), 'Layout logo antes do Excel')
}
// Busca igual à da Tabela de Preços: lupa à esquerda, 28px, largura flexível.
assert.match(barra, /<div class="relative flex-1 min-w-\[200px\] max-w-sm">\s*<Search class="absolute left-2 top-1\/2 -translate-y-1\/2 h-3\.5 w-3\.5 text-muted-foreground" \/>\s*<input\s+v-model="contasSearch"\s+placeholder="Buscar conta\.\.\."\s+class="h-7 border rounded pl-7 pr-2 py-0 text-sm bg-background w-full"/)
assert.match(tpl, /v-model="gridSearch"\s+placeholder="[^"]*"\s+class="h-7 border rounded pl-7 pr-2 py-0 text-sm bg-background w-full"/,
  'mesma classe da busca da Tabela')
// Botões no tamanho da barra da Tabela.
const botoes = [...barra.matchAll(/class="(btn [^"]*)"/g)].map((m) => m[1])
assert.equal(botoes.length, 7, 'Plataforma, Contas, Equipes, Layout, Excel, Vincular e Adicionar')
for (const b of botoes) assert.match(b, /^btn btn-sm h-7 py-0\b/, `botão no tamanho da barra: ${b}`)
// Contador, Vincular e Adicionar encostados à direita.
assert.match(barra, /<span class="ml-auto text-xs text-muted-foreground">\s*\{\{ accountsFiltered\.length \}\} conta\(s\)\s*<\/span>\s*<button v-if="canEditContas"[^>]*@click="autoMatchAccounts">[\s\S]*?Vincular Integrações\s*<\/button>\s*<button v-if="canEditContas"[^>]*@click="openAddAcc">[\s\S]*?Adicionar Conta\s*<\/button>\s*<\/div>\s*$/)
// Contas ▾ igual ao da Tabela (busca, Marcar todas · Limpar, caixas).
assert.match(barra, /\{\{ rotuloNomesContas \}\}/)
assert.match(barra, /data-menu-contas[\s\S]*?v-model="buscaNomesContas"[\s\S]*?Marcar todas[\s\S]*?Limpar[\s\S]*?v-for="o in opcoesNomesContasBuscadas"[\s\S]*?type="checkbox"[\s\S]*?:checked="!contasNomesDesmarcados\.includes\(o\.valor\)"[\s\S]*?alternarNaLista\(contasNomesDesmarcados, o\.valor\)/)
// Layout ▾.
assert.match(barra, /data-menu-layout-contas[\s\S]*?v-for="c in opcoesLayoutContas"[\s\S]*?type="checkbox"[\s\S]*?:checked="mostraColunaConta\(c\.chave\)"[\s\S]*?alternarNaLista\(colunasOcultasContas, c\.chave\)/)
// M-61: o Layout (Contas e Tabela) só com a caixa e o nome; sem descrição nem rodapé.
{
  const menuLayout = (marca) => {
    const i = tpl.search(marca)
    assert.ok(i >= 0, `menu ${marca} existe`)
    return tpl.slice(i, tpl.indexOf('</PopoverContent>', i)).replace(/<!--[\s\S]*?-->/g, '')
  }
  const menuContas = menuLayout(/data-menu-layout-contas\b/)
  const menuTabela = menuLayout(/data-menu-layout\s/)
  for (const m of [menuContas, menuTabela]) {
    assert.doesNotMatch(m, /Fica lembrado|O Excel leva|Esc ou clicar fora|border-t|text-\[11px\]/, 'sem descrição nem rodapé')
    assert.doesNotMatch(m, /items-start|mt-0\.5/, 'caixa centralizada na altura do nome')
    assert.match(m, /<label[^>]*class="flex cursor-pointer items-center gap-2 /)
    assert.match(m, /class="z-\[70\] w-64 /, 'a largura do menu não muda')
  }
  assert.match(menuContas, /type="checkbox"[\s\S]*?\/>\s*<span>\{\{ c\.rotulo \}\}<\/span>\s*<\/label>/)
  assert.match(menuTabela, /data-campo="layout-kit" \/>\s*<span>Kit<\/span>\s*<\/label>/)
  assert.match(menuTabela, /data-campo="layout-estoque" \/>\s*<span>Estoque<\/span>\s*<\/label>/)
  assert.ok(P.COLUNAS_LAYOUT_CONTAS.every((c) => !('dica' in c)), 'as opções do Layout de Contas não têm mais descrição')
}
// Excel: o mesmo botão da Tabela (ícone de download).
assert.match(barra, /<button class="btn btn-sm h-7 py-0" :disabled="!accountsFiltered\.length" data-acao="excel-contas" @click="exportarExcelContas">\s*<Download class="h-4 w-4 mr-1" \/> Excel\s*<\/button>/)

// As colunas escondidas pelo Layout: cabeçalho, linha de adicionar e linha da conta.
const conta3 = (chave) => (contas.match(new RegExp(`v-if="mostraColunaConta\\('${chave}'\\)"`, 'g')) || []).length
assert.equal(conta3('catalogo'), 3, 'Catálogo ML: th, linha nova e linha da conta (Tipo ML saiu na M-53)')
assert.equal(conta3('equipe'), 3)
assert.equal(conta3('kit'), 3)
// Comissão: th, a da linha nova (ML mostra a da categoria; as outras, o campo)
// e, na linha da conta, a do par ML e a das outras.
assert.equal(conta3('comissao'), 2)
assert.match(contas, /<template v-if="mostraColunaConta\('comissao'\)">\s*<td\s+v-if="newAcc\.platform === 'mercadolivre'"[\s\S]*?<td v-else[\s\S]*?v-model="newAcc\.commission"[\s\S]*?<\/td>\s*<\/template>/)
assert.match(contas, /<td\s+v-if="mostraColunaConta\('comissao'\) && parDe\(acc\)"[\s\S]*?data-comissao-ml[\s\S]*?<td\s+v-else-if="mostraColunaConta\('comissao'\)"/)
const cab = contas.slice(0, contas.indexOf('</thead>'))
assert.doesNotMatch(cab, />Tipo ML<\/th>/, 'Tipo ML saiu (M-53)')
assert.match(cab, /v-if="mostraColunaConta\('catalogo'\)"[^>]*>Catálogo ML<\/th>/)
assert.match(contas, /<td\s+v-if="mostraColunaConta\('catalogo'\)"\s+class="[^"]*"[\s\S]*?data-coluna-catalogo/)
assert.match(cab, /v-if="mostraColunaConta\('equipe'\)"[^>]*>Equipe<\/th>/)
assert.match(cab, /v-for="f in camposAnotacao"\s+v-show="f\.key !== 'observation' \|\| mostraColunaConta\('obs'\)"/)
assert.match(contas, /<td v-for="i in camposAnotacao\.length - \(mostraColunaConta\('obs'\) \? 0 : 1\)"/)
assert.match(contas, /<td\s+v-else\s+class="[^"]*"\s+v-show="f\.key !== 'observation' \|\| mostraColunaConta\('obs'\)"/)
console.log('pricing contas barra: template ok')

// ---------- 4) o SFC inteiro compila (script + template)
const script = compileScript(descriptor, { id: 'pricing-tab', inlineTemplate: false })
const out = compileTemplate({
  source: tpl, filename, id: 'pricing-tab',
  compilerOptions: { isTS: true, bindingMetadata: script.bindings },
})
assert.deepEqual(out.errors, [], 'template compila')
for (const b of ['contasNomesDesmarcados', 'buscaNomesContas', 'opcoesNomesContasBuscadas', 'rotuloNomesContas',
  'marcarNomesContas', 'opcoesLayoutContas', 'colunasOcultasContas', 'mostraColunaConta', 'exportarExcelContas']) {
  assert.ok(script.bindings[b], `${b} exposto ao template`)
}

console.log('pricing-contas-barra: ok')
