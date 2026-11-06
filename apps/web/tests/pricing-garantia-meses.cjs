// Run from apps/web: node tests/pricing-garantia-meses.cjs
// Coluna "Garantia (meses)" da aba Produtos (06/11/2026): roda o helper real
// da página que lê o texto digitado, confere o ramo do PATCH e a coluna no
// template (cabeçalho, linha de adicionar e linha do produto, fora dos v-if
// de mala/certificado) e compila o SFC inteiro. O 0 = sem garantia ("se for
// avulso não tem garantia", 06/11/2026).
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')

const filename = path.resolve(__dirname, '../pages/pricing/[tab].vue')
const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
assert.deepEqual(errors, [])
const setup = descriptor.scriptSetup.content

// 1) helper real: vazio apaga, 0..120 vale (0 = sem garantia), o resto não envia.
const source = ts.createSourceFile(filename, setup, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
const pega = (nome) => {
  const node = source.statements.find((c) =>
    (ts.isFunctionDeclaration(c) && c.name?.text === nome)
    || (ts.isVariableStatement(c) && c.declarationList.declarations.some((d) => d.name.getText(source) === nome)))
  assert.ok(node, `real page helper ${nome} exists`)
  return node.getText(source)
}
const compiled = ts.transpileModule([pega('GARANTIA_MESES_MAX'), pega('lerGarantiaMeses')].join('\n'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022 }, reportDiagnostics: true,
})
assert.deepEqual(compiled.diagnostics, [])
const lerGarantiaMeses = new Function(`${compiled.outputText}\nreturn lerGarantiaMeses`)()
assert.equal(lerGarantiaMeses(''), null, 'vazio apaga o prazo')
assert.equal(lerGarantiaMeses('  '), null)
assert.equal(lerGarantiaMeses('-'), null)
assert.equal(lerGarantiaMeses('—'), null)
assert.equal(lerGarantiaMeses('0'), 0, '0 = sem garantia (não é vazio)')
assert.equal(lerGarantiaMeses('00'), 0)
assert.equal(lerGarantiaMeses('1'), 1)
assert.equal(lerGarantiaMeses('12'), 12)
assert.equal(lerGarantiaMeses(' 120 '), 120)
assert.equal(lerGarantiaMeses('012'), 12)
for (const ruim of ['121', '999', '-3', '-0', '12,5', '12.5', '1e2', 'abc', '12 meses']) {
  assert.equal(lerGarantiaMeses(ruim), undefined, `"${ruim}" não envia`)
}

// 2) ramo do PATCH: usa o helper, avisa e não envia quando inválido, manda
// null/número no campo garantia_meses da mesma rota das outras células.
const patch = pega('_patchProduct')
assert.match(patch, /field === 'garantia_meses'[\s\S]*?lerGarantiaMeses\(raw\)/)
assert.match(patch, /n === undefined\) \{\s*productsErr\.value = `Garantia:[^`]*`\s*return\s*\}/,
  'inválido avisa e não envia')
assert.match(patch, /payload\.garantia_meses = n/)
assert.match(patch, /\/api\/pricing\/products\/\$\{id\}`, \{\s*method: 'PATCH'/)
assert.match(setup, /garantia_meses: number \| null/, 'PricingProduct.garantia_meses')

// 3) template: coluna depois do Catálogo, fora dos v-if de mala/certificado.
const tpl = descriptor.template.content
const iCol = tpl.indexOf('data-col-garantia-meses')
assert.ok(iCol > 0, 'coluna marcada no cabeçalho')
const iThead = tpl.lastIndexOf('<thead', iCol)
const cab = tpl.slice(iThead, tpl.indexOf('</thead>', iCol))
assert.ok(cab.includes('>SKU</th>') && cab.includes('data-col-cert-modelo'), 'é o cabeçalho da aba Produtos')
const ths = [...cab.matchAll(/<th[\s\S]*?>([^<]*)<\/th>/g)].map((m) => m[1].trim())
const iGar = ths.indexOf('Garantia (meses)')
assert.ok(iGar > 0, 'cabeçalho Garantia (meses) existe')
assert.equal(ths[iGar - 1], 'Catálogo')
assert.equal(ths[iGar + 1], 'Fotos')
assert.match(cab, /title="Prazo da garantia do produto, em meses \(0 = sem garantia\)\. Aumentar vale para todas as vendas; diminuir só para as vendas a partir da mudança; corrigir no mesmo dia desfaz o valor anterior\."[\s\S]*?>Garantia \(meses\)<\/th>/)
// O cabeçalho não está dentro de <template v-if>: o último <template> aberto
// antes dele já fechou.
const antesDoTh = cab.slice(0, cab.indexOf('Garantia (meses)</th>'))
assert.ok(antesDoTh.lastIndexOf('</template>') > antesDoTh.lastIndexOf('<template'), 'cabeçalho fora dos v-if')
// Linha de adicionar: "—" entre Catálogo e Fotos.
assert.match(tpl, /newProd\.preco_catalogo[^\n]*\n\s*<\/td>\s*<!-- Garantia \(meses\): define depois, na linha criada\. -->\s*<td[^>]*>—<\/td>/)
// Linha do produto: editável só para quem edita produtos; "—" sem prazo.
const corpo = tpl.slice(tpl.indexOf('<!-- data rows -->'))
const celula = corpo.slice(corpo.indexOf('<!-- Garantia (meses), 06/11/2026'), corpo.indexOf('<!-- Fotos:'))
assert.ok(celula.length > 0, 'célula da linha existe, antes de Fotos')
assert.ok(corpo.indexOf("isEditing(p.id, 'preco_catalogo')") < corpo.indexOf("isEditing(p.id, 'garantia_meses')"))
assert.match(celula, /@click="canEditProdutos && !isEditing\(p\.id, 'garantia_meses'\) && startEditProduct\(p, 'garantia_meses'\)"/)
assert.match(celula, /'cursor-pointer': canEditProdutos/)
assert.match(celula, /p\.garantia_meses == null \? '—' : p\.garantia_meses === 0 \? 'sem garantia' : p\.garantia_meses/)
assert.match(celula, /p\.garantia_meses === 0 \? 'Sem garantia \(0 = sem garantia\)'/, 'o title da célula explica o 0')
assert.match(celula, /@blur="commitEditProduct"/)

// 4) o SFC inteiro compila (script + template).
const script = compileScript(descriptor, { id: 'pricing-tab', inlineTemplate: false })
const out = compileTemplate({
  source: tpl, filename, id: 'pricing-tab',
  compilerOptions: { isTS: true, bindingMetadata: script.bindings },
})
assert.deepEqual(out.errors, [], 'template compila')
assert.ok(script.bindings.lerGarantiaMeses, 'helper exposto ao template')

console.log('pricing-garantia-meses: ok')
