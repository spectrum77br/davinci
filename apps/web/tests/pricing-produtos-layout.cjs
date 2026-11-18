// Run from apps/web: node tests/pricing-produtos-layout.cjs
// Aba Produtos (M-9, 18/11/2026): cabeçalho no formato da Tabela de Preços
// (11px, peso 600, linha 14px), SKU/Produto/EAN à esquerda e o resto
// centralizado, colunas de kit de ~98px com a descrição em até 2 linhas, e
// todas as linhas com 33px (nada quebra linha; nome longo com reticências;
// "3 fotos" e "2 vídeos" lado a lado). Compila o SFC inteiro no fim.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')

const filename = path.resolve(__dirname, '../pages/pricing/[tab].vue')
const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
assert.deepEqual(errors, [])
const setup = descriptor.scriptSetup.content
const tpl = descriptor.template.content

const source = ts.createSourceFile(filename, setup, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
const pega = (nome) => {
  const node = source.statements.find((c) =>
    (ts.isFunctionDeclaration(c) && c.name?.text === nome)
    || (ts.isVariableStatement(c) && c.declarationList.declarations.some((d) => d.name.getText(source) === nome)))
  assert.ok(node, `real page helper ${nome} exists`)
  return node.getText(source)
}

// 1) Cabeçalho da aba Produtos.
const iCol = tpl.indexOf('data-col-garantia-meses')
const cab = tpl.slice(tpl.lastIndexOf('<thead', iCol), tpl.indexOf('</thead>', iCol))
assert.ok(cab.includes('>SKU</th>') && cab.includes('data-col-cert-modelo'), 'é o cabeçalho da aba Produtos')
const abreTh = [...cab.matchAll(/<th\b[^>]*>/g)].map((m) => m[0])
assert.ok(abreTh.length >= 15, 'achou os cabeçalhos')
for (const th of abreTh) {
  assert.match(th, /text-\[11px\] font-semibold leading-\[14px\]/, `formato do cabeçalho: ${th.slice(0, 80)}`)
  assert.doesNotMatch(th, /font-medium|text-right/)
}
const aEsquerda = abreTh.filter((th) => /\btext-left\b/.test(th))
assert.equal(aEsquerda.length, 3, 'só SKU, Produto e EAN à esquerda')
for (const nome of ['SKU', 'Produto', 'EAN']) assert.match(cab, new RegExp(`text-left[^>]*>${nome}</th>`))
for (const th of abreTh.filter((t) => !/\btext-left\b/.test(t))) assert.match(th, /\btext-center\b/)

// 2) Colunas de kit: ~98px; "Kit N" e a descrição em 11px, descrição em até 2 linhas.
const thKit = abreTh.find((th) => th.includes('v-for="k in kitCount"'))
assert.ok(thKit, 'cabeçalho do kit')
assert.match(thKit, /w-\[98px\] min-w-\[98px\]/)
assert.match(cab, /<span v-if="kitNome\(k\)" class="[^"]*\btext-\[11px\][^"]*\bline-clamp-2\b[^"]*">\{\{ kitNome\(k\) \}\}<\/span>/)
assert.doesNotMatch(cab, /text-\[9px\]/)

// 3) Linhas: 33px, sem quebra; valores centralizados (fora SKU/Produto/EAN).
const corpo = tpl.slice(tpl.indexOf('<!-- data rows -->'), tpl.indexOf('</tbody>', tpl.indexOf('<!-- data rows -->')))
assert.match(corpo, /<tr v-for="p in productsCurrent" :key="p\.id" class="h-\[33px\] whitespace-nowrap /)
assert.match(corpo, /<span v-else class="block max-w-\[320px\] truncate" :title="p\.name">\{\{ p\.name \}\}<\/span>/,
  'nome longo cortado com reticências (completo no balão)')
assert.doesNotMatch(corpo, /text-right/, 'nenhum valor alinhado à direita')
for (const campo of ['`cost_kit${k}`', "'preco_catalogo'", "'garantia_meses'"]) {
  const i = corpo.indexOf(`isEditing(p.id, ${campo})`)
  assert.ok(i > 0, `célula ${campo}`)
  const td = corpo.slice(corpo.lastIndexOf('<td', i), i)
  assert.match(td, /\btext-center\b/, `célula ${campo} centralizada`)
}
// "sem garantia" continua o texto do 0, agora numa linha (a linha é nowrap).
assert.match(corpo, /p\.garantia_meses === 0 \? 'sem garantia'/)

// 4) Etiqueta de mídias numa linha: "3 fotos" e "2 vídeos" lado a lado.
const base = pega('ETIQUETA_MIDIA_BASE')
assert.doesNotMatch(base, /flex-col/)
assert.match(base, /inline-flex items-center justify-center gap-1 whitespace-nowrap/)

// 5) O preço do kit cabe numa linha: "R$ 1700" (helper real).
const compiled = ts.transpileModule(pega('fmtBRL'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022 }, reportDiagnostics: true,
})
assert.deepEqual(compiled.diagnostics, [])
const fmtBRL = new Function(`${compiled.outputText}\nreturn fmtBRL`)()
assert.equal(fmtBRL('1700.00'), 'R$ 1700')
assert.equal(fmtBRL(null), '—')

// 6) O SFC inteiro compila (script + template).
const script = compileScript(descriptor, { id: 'pricing-tab', inlineTemplate: false })
const out = compileTemplate({
  source: tpl, filename, id: 'pricing-tab',
  compilerOptions: { isTS: true, bindingMetadata: script.bindings },
})
assert.deepEqual(out.errors, [], 'template compila')
for (const b of ['alternarNaLista', 'contasPlataformasDesmarcadas', 'contasEquipesDesmarcadas', 'equipesPresentesNoDept']) {
  assert.ok(script.bindings[b], `${b} exposto ao template`)
}

console.log('pricing-produtos-layout: ok')
