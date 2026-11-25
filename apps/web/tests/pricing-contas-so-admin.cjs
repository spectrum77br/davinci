// Run from apps/web: node tests/pricing-contas-so-admin.cjs
// M-10 (18/11/2026): Kit, Comissão e os pares margem/frete da aba Contas só
// para o ADM; o /accounts e o /grid mandam null a quem não é ADM. Executa as
// funções e computeds reais da página (grupos da grade, colspan) e confere o
// template e o script.
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

const js = (texto) => {
  const out = ts.transpileModule(texto, { compilerOptions: { target: ts.ScriptTarget.ES2022 }, reportDiagnostics: true })
  assert.deepEqual(out.diagnostics, [])
  return out.outputText
}
const funcao = (nome) => {
  const n = source.statements.find((s) => ts.isFunctionDeclaration(s) && s.name?.text === nome)
  assert.ok(n, `${nome} existe na página`)
  return n.getText(source)
}
const computado = (nome) => {
  const st = source.statements.find((s) => ts.isVariableStatement(s)
    && s.declarationList.declarations.some((d) => d.name.getText(source) === nome))
  assert.ok(st, `${nome} existe na página`)
  const ini = st.declarationList.declarations.find((d) => d.name.getText(source) === nome).initializer
  assert.ok(ini && ts.isCallExpression(ini) && ini.expression.getText(source) === 'computed', `${nome} é computed`)
  return `const __f = ${ini.arguments[0].getText(source)}`
}
const ref = (v) => ({ value: v })

// ---------- 1) grupos da grade: com kit (ADM) "ML kit N"; sem kit, um por plataforma
const LABELS = { mercadolivre: 'ML', shopee: 'Shopee' }
const platformLabel = (p) => LABELS[p] ?? p
const gridAccounts = ref([])
const grupos = new Function('platformLabel', 'gridAccounts', js([
  funcao('ehCatalogo'), funcao('chavePlataforma'), funcao('rotuloPlataforma'), computado('accountGroups'),
  'return __f',
].join('\n')))(platformLabel, gridAccounts)
const conta = (id, platform, kit, canal = 'kit') => ({ id, platform, name: id, canal, kit_number: kit })
const rotulos = (contas) => {
  gridAccounts.value = contas
  return grupos().map((g) => `${g.label}:${g.accounts.map((a) => a.id).join(',')}`)
}
assert.deepEqual(
  rotulos([conta('a', 'mercadolivre', 1), conta('b', 'mercadolivre', 2), conta('c', 'mercadolivre', 2, 'catalogo'), conta('d', 'shopee', 1)]),
  ['ML kit 1:a', 'ML kit 2:b', 'ML Catálogo:c', 'Shopee kit 1:d'],
)
assert.deepEqual(
  rotulos([conta('a', 'mercadolivre', null), conta('b', 'mercadolivre', null), conta('c', 'mercadolivre', null, 'catalogo'), conta('d', 'shopee', null)]),
  ['ML Kit:a,b', 'ML Catálogo:c', 'Shopee:d'],
)
console.log('pricing contas só ADM: grupos da grade ok')

// ---------- 2) colspan da aba Contas
const isAdmin = ref(true)
const nTiposAba = ref(5)
const STORE_NOTE_FIELDS = new Array(5).fill({})
const colunas = new Function('isAdmin', 'nTiposAba', 'STORE_NOTE_FIELDS',
  js(`${computado('colunasContas')}\nreturn __f`))(isAdmin, nTiposAba, STORE_NOTE_FIELDS)
// Plataforma, Conta, Catálogo ML, Equipe (Tipo ML saiu na M-53) + Kit, Comissão,
// 5 pares + Afiliado, Desconto, Ads, Custo total, Obs + lixeira.
assert.equal(colunas(), 4 + 2 + 10 + 5 + 1)
isAdmin.value = false
assert.equal(colunas(), 4 + 5 + 1)
console.log('pricing contas só ADM: colspan ok')

// ---------- 3) script: conta nova sem kit/comissão, recálculo e aviso só do ADM
const novo = funcao('submitNewAcc')
assert.doesNotMatch(novo.slice(0, novo.indexOf('if (isAdmin.value)')), /kit_number|commission/)
assert.match(novo, /if \(isAdmin\.value\) \{\s*body\.kit_number = newAcc\.kit_number \|\| 1\s*if \(newAcc\.commission && !ehMl\) body\.commission/)
assert.match(funcao('recomputeCellsForProduct'), /if \(!grid\.value \|\| !isAdmin\.value\) return/)
assert.match(funcao('_patchProduct'), /if \(mudaPreco\) \{\s*if \(isAdmin\.value\) recomputeCellsForProduct\(id\)\s*else if \(grid\.value\) void loadGrid\(\)/)
assert.match(setup, /kit_number: number \| null/)
console.log('pricing contas só ADM: script ok')

// ---------- 4) template
const tpl = descriptor.template.content
const contas = tpl.slice(tpl.indexOf("tab === 'contas'"), tpl.indexOf("tab === 'produtos'"))
assert.doesNotMatch(contas, /colSpan="30"/, 'colspan fixo saiu')
assert.equal((contas.match(/:colspan="colunasContas"/g) || []).length, 3)
const cab = contas.slice(0, contas.indexOf('</thead>'))
assert.match(cab, /<template v-if="isAdmin">\s*<th[^>]*data-coluna-so-admin="kit">Kit<\/th>\s*<th[^>]*data-coluna-so-admin="comissao">Comissão<\/th>\s*<th\s+v-for="\(label, i\) in TYPE_HEADERS\[department\] \?\? \[\]"[\s\S]*?<\/th>\s*<\/template>/)
// Linha de adicionar conta: kit, comissão e os "—" dos pares, só ADM.
assert.match(contas, /<template v-if="isAdmin">\s*<td[^>]*>\s*<input\s+v-model\.number="newAcc\.kit_number"[\s\S]*?v-model="newAcc\.commission"[\s\S]*?v-for="i in nTiposAba \* 2"[\s\S]*?<\/td>\s*<\/template>/)
// Linha da conta: kit, comissão e pares dentro do mesmo v-if.
const linha = contas.slice(contas.indexOf('<!-- kit, comissão e pares marg/frete: só ADM (M-10) -->'))
assert.match(linha, /^<!-- kit, comissão e pares marg\/frete: só ADM \(M-10\) -->\s*<template v-if="isAdmin">/)
const fimBloco = linha.indexOf('<!-- afiliado / desconto / ads / custo total / obs (M-55/M-56) -->')
assert.ok(fimBloco > 0)
const bloco = linha.slice(0, fimBloco)
for (const campo of ["'kit_number'", "'commission'", '`margin${t}`', '`shipping${t}`']) {
  assert.ok(bloco.includes(campo), `${campo} dentro do bloco só ADM`)
}
assert.ok(!contas.slice(contas.indexOf('<!-- afiliado / desconto / ads / custo total / obs')).includes("'commission'"))
// A janela da comissão do ML (M-53) também fica dentro do bloco só ADM.
assert.ok(bloco.includes('data-janela-comissao-ml'), 'comissão do ML só ADM')
// Botão "Margens" da janela do Catálogo ML (M-54; antes o link "% margens"): só ADM.
assert.match(contas, /<template v-if="isAdmin">\s*<div class="px-1 pt-1">\s*<button[^>]*?data-acao="margens-catalogo"/)
// Subtítulo sem a contagem de pares para quem não é ADM.
assert.match(contas, /conta\(s\)<template v-if="isAdmin"> com \{\{ nTiposAba \}\} pares margem\/frete por tipo<\/template>/)
// Aviso de margem negativa (precisa de kit e frete): só ADM.
assert.match(tpl, /v-if="isAdmin && negativeMarginCount > 0"\s*data-aviso-margem-negativa/)
console.log('pricing contas só ADM: template ok')
console.log('pricing-contas-so-admin: ok')
