// Run from apps/web: node tests/controle-estoque-nf-caixa.cjs
// Controle de Estoque › Pedidos: pedido com a NF de 100% que vai DENTRO DA CAIXA
// (28/09/2026). Eduardo: "a primeira é sempre 1% e a segunda, que é 100%, precisa
// colocar informação para distinguir"; "na hora de imprimir não pode aparecer
// aquela frase"; escolheu a caixinha DENTRO do botão ("algo sutil", opção 2B). Travas:
//  - a caixinha (Package) só aparece com row.nf_caixa, dentro do botão Imprimir;
//  - o título do botão explica as 3 páginas só nesse caso; nos outros, o de sempre;
//  - nenhum selo/texto a mais fora do botão.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const { createRequire } = require('node:module')
const path = require('node:path')
const requireWeb = createRequire(path.resolve(__dirname, '../package.json'))
const { parse, compileTemplate } = requireWeb('vue/compiler-sfc')

const filename = path.resolve(__dirname, '../pages/controle-estoque.vue')
const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
assert.deepEqual(errors, [])
assert.deepEqual(compileTemplate({ source: descriptor.template.content, filename, id: 'ce' }).errors, [])

const tpl = descriptor.template.content
const i = tpl.indexOf('<Package v-if="row.nf_caixa"')
assert.ok(i > 0, 'caixinha condicionada a row.nf_caixa')
const aIni = tpl.lastIndexOf('<a', i)
const aFim = tpl.indexOf('</a>', i)
const botao = tpl.slice(aIni, aFim)
assert.match(botao, /:href="etiquetaUrl\(row\)"/, 'a caixinha está DENTRO do botão Imprimir')
assert.match(botao, /:title="row\.nf_caixa \? 'Saem 3 páginas:[^']*DENTRO DA CAIXA' : 'Abrir etiqueta pronta pra impressão'"/)
assert.ok(!/última vai na caixa/.test(tpl), 'sem o selo antigo embaixo do botão')
assert.match(descriptor.scriptSetup.content, /nf_caixa\?: boolean/)
assert.match(descriptor.scriptSetup.content, /Package,\n\} from 'lucide-vue-next'/)
console.log('PASS: caixinha dentro do Imprimir só com nf_caixa; título com as 3 páginas; sem selo fora do botão')
