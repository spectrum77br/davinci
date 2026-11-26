// Run from apps/web: node tests/certificacoes-layout-excel.cjs
// M-67 (25/11/2026): Importação › Certificações ganhou na barra do título
// Layout ▾ (mostra/esconde Modelo, Número, Valor e Situação oficial; lembrado
// no navegador) e Excel (.xlsx com as colunas à mostra e todas as linhas; datas
// como data e Valor como número), no lugar do "Baixar tabela em PDF".
// Usa o page()/render() de certificacoes-anatel.cjs (o <script setup> de
// verdade com api falsa) e abre o .xlsx gerado para conferir os XML.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const { page, row, render, settle } = require('./certificacoes-anatel.cjs')

const X = {}
new Function('exports', ts.transpileModule(fs.readFileSync(path.join(__dirname, '../lib/planilhaXlsx.ts'), 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText)(X)

// Lê o zip sem compressão que a lib gera: nome → texto.
function abrirZip(bytes) {
  const buf = Buffer.from(bytes.buffer, bytes.byteOffset, bytes.length)
  const fim = buf.length - 22
  assert.equal(buf.readUInt32LE(fim), 0x06054b50)
  const n = buf.readUInt16LE(fim + 10)
  let p = buf.readUInt32LE(fim + 16)
  const arquivos = {}
  for (let i = 0; i < n; i++) {
    const tam = buf.readUInt32LE(p + 20)
    const lenNome = buf.readUInt16LE(p + 28)
    const local = buf.readUInt32LE(p + 42)
    const nome = buf.toString('utf8', p + 46, p + 46 + lenNome)
    const ini = local + 30 + buf.readUInt16LE(local + 26) + buf.readUInt16LE(local + 28)
    arquivos[nome] = buf.toString('utf8', ini, ini + tam)
    p += 46 + lenNome + buf.readUInt16LE(p + 30) + buf.readUInt16LE(p + 32)
  }
  return arquivos
}

function armazenamento(inicial = {}) {
  const store = { ...inicial }
  return { store, getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v) } }
}

// As caixas do menu Layout: [chave, marcada].
function caixas(html) {
  return [...html.matchAll(/<input[^>]*data-campo="layout-certificacoes-(\w+)"[^>]*>/g)]
    .map((m) => [m[1], /\schecked(\s|>|=)/.test(m[0])])
}

// M-68: o Início saiu da tela (e do Excel) e o Fim virou Validade.
// M-69: o SKU vai logo depois do Produto (sempre à mostra).
const CAB_TUDO = ['Produto', 'SKU', 'Modelo', 'Nome comercial', 'Certificado', 'Número', 'Valor', 'Validade',
  'Garantia (meses)', 'Situação oficial', 'PDF do certificado']

async function run() {
  // ---------- lib: datas viram data de verdade no Excel
  assert.equal(X.serialDaData('1970-01-01'), 25569)
  assert.equal(X.serialDaData('2000-01-01'), 36526)
  for (const ruim of ['', 'abc', '2026-02-30', '2026-13-01', '30/07/2026', '1900-01-01']) {
    assert.equal(X.serialDaData(ruim), null, ruim)
  }
  {
    const z = abrirZip(X.gerarXlsx([['Fim'], [{ data: '2000-01-01' }], [{ data: 'ruim' }]], 'Teste'))
    assert.ok(z['xl/worksheets/sheet1.xml'].includes('<row r="2"><c r="A2" s="2"><v>36526</v></c></row>'), 'data com o estilo 2')
    assert.ok(z['xl/worksheets/sheet1.xml'].includes('<row r="3"></row>'), 'data inválida fica em branco')
    assert.ok(z['xl/styles.xml'].includes('<numFmt numFmtId="164" formatCode="dd/mm/yyyy"/>'))
    assert.ok(z['xl/styles.xml'].includes('<cellXfs count="3">'))
    assert.ok(z['xl/styles.xml'].includes('<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'))
  }

  // ---------- Layout: tudo marcado no começo; menu só com caixa e nome
  {
    const ls = armazenamento()
    const { state: s, mounted } = page({ isAdmin: true }, { localStorage: ls })
    assert.equal(mounted.length, 1)
    mounted[0]()
    s.rows.value = [row()]
    assert.deepEqual(s.colunasOcultas.value, [])
    assert.equal(s.totalColunas.value, 12, '11 colunas + lixeira')
    const html = await render(s)
    assert.deepEqual(caixas(html), [['modelo', true], ['numero', true], ['valor', true], ['situacao', true]])
    const ini = html.indexOf('data-menu-layout-certificacoes')
    const menu = html.slice(ini, html.indexOf('</div>', ini)).replace(/<[^>]*>/g, '').replace(/^[^>]*>/, '')
    assert.equal(menu.replace(/\s+/g, ''), 'ModeloNúmeroValorSituação', 'menu sem descrição nem rodapé')
    for (const th of ['<th class="text-left">Modelo</th>', '<th class="text-left">Número</th>',
      '<th class="text-right">Valor</th>', '<th class="text-left">Situação oficial</th>']) {
      assert.ok(html.includes(th), th)
    }
    assert.match(html, /data-acao="layout-certificacoes"/)
    assert.match(html, /data-acao="excel-certificacoes"/)
    assert.doesNotMatch(html, /Baixar tabela em PDF/)
    assert.ok(html.indexOf('data-acao="layout-certificacoes"') < html.indexOf('data-acao="excel-certificacoes"'), 'ordem: Layout, Excel')
    // M-66: o Atualizar ▾ ficou no lugar do Recarregar.
    assert.doesNotMatch(html.replace(/<!--[\s\S]*?-->/g, ''), /Recarregar/)
    const atualizar = html.indexOf('data-acao="atualizar"')
    assert.ok(atualizar > 0, 'botão Atualizar')
    assert.ok(html.indexOf('data-acao="excel-certificacoes"') < atualizar, 'ordem: Excel, Atualizar')
  }

  // ---------- Layout: desmarcar Valor esconde a coluna e fica lembrado
  {
    const ls = armazenamento()
    const { state: s, mounted } = page({ isAdmin: true }, { localStorage: ls })
    mounted[0]()
    s.rows.value = [row()]
    s.alternarColuna('valor')
    await settle()
    assert.deepEqual(s.colunasOcultas.value, ['valor'])
    assert.equal(ls.store['certificacoes.colunasOcultas'], 'valor', 'lembrado no navegador')
    const html = await render(s)
    assert.ok(!html.includes('<th class="text-right">Valor</th>'), 'coluna Valor some')
    assert.doesNotMatch(html, /<input type="number"/, 'célula do Valor some')
    const controls = html.match(/<(?:input|select)\b[^>]*class="cell-input\b[^"]*"[^>]*>/g)
    assert.equal(controls.length, 7, 'os 8 campos editáveis menos o Valor')
    assert.deepEqual(caixas(html), [['modelo', true], ['numero', true], ['valor', false], ['situacao', true]])
    // Marcar de novo volta.
    s.alternarColuna('valor')
    await settle()
    assert.deepEqual(s.colunasOcultas.value, [])
    assert.equal(ls.store['certificacoes.colunasOcultas'], '')
  }

  // ---------- Layout: lê o que estava guardado (só chaves válidas)
  {
    const ls = armazenamento({ 'certificacoes.colunasOcultas': 'situacao, xyz,modelo,situacao' })
    const { state: s, mounted } = page({ isAdmin: false, user: { permissions: { financeiro_suprimentos: { view: true } } } }, { localStorage: ls })
    mounted[0]()
    assert.deepEqual(s.colunasOcultas.value, ['situacao', 'modelo'])
    assert.equal(s.totalColunas.value, 9, '11 - 2, sem lixeira')
    s.rows.value = []
    const html = await render(s)
    assert.match(html, /<td colspan="9"[^>]*>\s*Nenhuma certificação/)
    assert.ok(!html.includes('<th class="text-left">Modelo</th>'))
    assert.ok(!html.includes('<th class="text-left">Situação oficial</th>'))
    assert.ok(html.includes('<th class="text-left">Número</th>'))
  }

  // ---------- Layout: navegador sem armazenamento não quebra
  {
    const quebrado = { getItem() { throw new Error('bloqueado') }, setItem() { throw new Error('bloqueado') } }
    const { state: s, mounted } = page({ isAdmin: true }, { localStorage: quebrado })
    mounted[0]()
    assert.deepEqual(s.colunasOcultas.value, [])
    s.alternarColuna('numero')
    await settle()
    assert.deepEqual(s.colunasOcultas.value, ['numero'], 'vale só nesta visita')
  }

  // ---------- Excel: todas as colunas; Validade como data; Valor como número
  {
    const { state: s, planilhas, baixados } = page()
    s.rows.value = [row(), row({ id: 'two', produto: 'Fone', valor: '1234.5', inicio: null, fim: '2027-01-15', tem_pdf: false, pdf_nome: null, garantia_meses: 12, skus: ['dg017', 'dg018'] })]
    await s.exportTable()
    assert.equal(s.errorText.value, null)
    assert.equal(planilhas.length, 1)
    assert.equal(planilhas[0].aba, 'Certificações')
    // M-68: Certificado com o nome da tela; Situação de linha manual em branco.
    assert.deepEqual(planilhas[0].linhas, [
      CAB_TUDO,
      ['Telefone', '', 'USM003', 'Modelo comercial', 'Anatel', '07197-26-18234', 53000, { data: '2028-07-30' },
        '', '', 'certificado.pdf'],
      ['Fone', 'dg017, dg018', 'USM003', 'Modelo comercial', 'Anatel', '07197-26-18234', 1234.5, { data: '2027-01-15' },
        12, '', ''],
    ])
    assert.equal(baixados.length, 1)
    assert.match(baixados[0], /^certificacoes-\d{4}-\d{2}-\d{2}\.xlsx$/)
    const sheet = abrirZip(planilhas[0].bytes)['xl/worksheets/sheet1.xml']
    assert.ok(sheet.includes('<c r="G2"><v>53000</v></c>'), 'Valor como número')
    assert.ok(sheet.includes(`<c r="H2" s="2"><v>${X.serialDaData('2028-07-30')}</v></c>`), 'Validade como data')
    assert.ok(!sheet.includes(`<v>${X.serialDaData('2026-07-30')}</v>`), 'sem o Início')
    assert.ok(sheet.includes('<c r="I3"><v>12</v></c>'), 'Garantia como número')
  }

  // ---------- Excel: sem a coluna Valor quando ela está desmarcada (exemplo do pedido)
  {
    const { state: s, mounted, planilhas } = page({ isAdmin: true }, { localStorage: armazenamento() })
    mounted[0]()
    s.rows.value = [row()]
    s.alternarColuna('valor')
    await s.exportTable()
    const [cab, linha] = planilhas[0].linhas
    assert.deepEqual(cab, CAB_TUDO.filter((c) => c !== 'Valor'))
    assert.ok(!linha.includes(53000))
    const sheet = abrirZip(planilhas[0].bytes)['xl/worksheets/sheet1.xml']
    assert.ok(sheet.includes(`<c r="G2" s="2"><v>${X.serialDaData('2028-07-30')}</v></c>`), 'Validade sobe para a coluna G')
    // Escondendo as quatro: só as colunas fixas.
    s.alternarColuna('modelo')
    s.alternarColuna('numero')
    s.alternarColuna('situacao')
    await s.exportTable()
    assert.deepEqual(planilhas[1].linhas[0], ['Produto', 'SKU', 'Nome comercial', 'Certificado', 'Validade', 'Garantia (meses)', 'PDF do certificado'])
  }

  // ---------- Excel: salva as edições pendentes antes e sai com elas
  {
    const { state: s, calls, planilhas } = page()
    const current = row()
    s.rows.value = [current]
    s.scheduleSave(current, 'produto', 'Nome novo')
    const baixando = s.exportTable()
    assert.equal(calls.length, 1)
    assert.equal(calls[0].options.method, 'PATCH')
    assert.equal(planilhas.length, 0, 'espera salvar antes de gerar')
    calls[0].resolve()
    await baixando
    assert.equal(planilhas[0].linhas[1][0], 'Nome novo')
  }
  {
    const { state: s, calls, planilhas } = page()
    const current = row()
    s.rows.value = [current]
    s.scheduleSave(current, 'produto', 'Nome novo')
    const baixando = s.exportTable()
    calls[0].reject(new Error('offline'))
    await baixando
    assert.equal(planilhas.length, 0, 'sem salvar, não baixa')
    assert.match(s.errorText.value, /Tente baixar o Excel novamente/)
    assert.equal(s.exporting.value, false)
  }

  console.log('PASS: Layout (mostrar/esconder e lembrar) e Excel (colunas à mostra, datas e números) das Certificações')
}
run().catch((error) => { console.error(error); process.exitCode = 1 })
