// node tests/atendimento-pedido-por-produto-ml.cjs — o nº de cada pedido do ML (06/11/2026).
// O dono: "só quero o nmr de cada pedido correto". Com mais de um pedido, cada produto mostra
// o nº dele e o número de cima sai; com um pedido só, fica o número de cima.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { parse, compileTemplate } = require('vue/compiler-sfc')

const filename = path.resolve(__dirname, '../components/AtendimentoPedido.vue')
const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
assert.deepEqual(errors, [])
assert.deepEqual(compileTemplate({ source: descriptor.template.content, filename, id: 'pedido' }).errors, [])
const t = descriptor.template.content
assert.match(t, /v-if="pm\.pedido && variosPedidos <= 1"/)
assert.match(t, /v-if="it\.pedido && variosPedidos > 1"[^>]*data-pedido-do-item/)
assert.match(t, /copiarTexto\(it\.pedido, 'Nº do pedido'\)/)
assert.doesNotMatch(t, /[Cc]arrinho com|Nº do carrinho/)
assert.match(descriptor.scriptSetup.content, /const variosPedidos = computed\(\(\) => pm\.value\?\.pedidos\?\.length \|\| 0\)/)

const plataforma = fs.readFileSync(path.resolve(__dirname, '../components/AtendimentoPlataforma.vue'), 'utf8')
assert.match(plataforma, /pedidos: Array\.isArray\(o\.pedidos\)/)
assert.match(plataforma, /pedido: textoOuNull\(o\.pedido\),\n\s+titulo/)

console.log('atendimento-pedido-por-produto-ml: ok')
