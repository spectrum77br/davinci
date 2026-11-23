// Run from apps/web: node tests/pricing-cabecalho-sem-descricao.cjs
// Tabela de Preços (M-25, 23/11/2026): o cabeçalho fica só com o título
// "Tabela de Preços", sem a linha "Gerencie custos, margens e preços de venda
// por conta". Vale para a página inteira (Celular, Mala, Eletro, Faturamento e
// as sub-abas), que usa um único PageHeader. As outras telas seguem com a
// descrição: o PageHeader continua mostrando-a quando ela é passada.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { parse } = require('vue/compiler-sfc')

const filename = path.resolve(__dirname, '../pages/pricing/[tab].vue')
const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
assert.deepEqual(errors, [])
const tpl = descriptor.template.content

// Um só cabeçalho na página, com o título e sem descrição.
const headers = tpl.match(/<PageHeader\b[^>]*>/g) || []
assert.equal(headers.length, 1, 'um PageHeader só na Tabela de Preços')
assert.match(headers[0], /\btitle="Tabela de Preços"/, 'o título continua')
assert.doesNotMatch(headers[0], /description/, 'sem a linha de descrição')
assert.doesNotMatch(tpl, /Gerencie custos, margens e preços de venda por conta/)

// Logo depois do cabeçalho vêm os botões de categoria (nada no meio).
const depois = tpl.slice(tpl.indexOf(headers[0]) + headers[0].length)
const proximaTag = depois.replace(/<!--[\s\S]*?-->/g, '').match(/<[A-Za-z][^>]*>/)
assert.ok(proximaTag, 'há algo depois do cabeçalho')
assert.match(proximaTag[0], /^<div class="flex flex-wrap gap-2">$/, 'os botões de categoria vêm logo abaixo do título')

// O componente comum não mudou: a descrição das outras telas continua.
const comp = fs.readFileSync(path.resolve(__dirname, '../components/PageHeader.vue'), 'utf8')
assert.match(comp, /<p v-if="description" class="page-header-description[^"]*">\{\{ description \}\}<\/p>/)

console.log('pricing-cabecalho-sem-descricao: ok')
