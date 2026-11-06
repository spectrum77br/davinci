// node tests/atendimento-carrinho-ml.cjs — o carrinho do ML no painel do pedido (06/11/2026).
// O dono: "linkar todos os pedidos exatos do ml com cada produto caso seja um pedido de
// carrinho". Com mais de um pedido, cada produto mostra o nº dele; o nº do carrinho aparece
// quando o número mostrado é outro.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { parse, compileTemplate } = require('vue/compiler-sfc')

const filename = path.resolve(__dirname, '../components/AtendimentoPedido.vue')
const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
assert.deepEqual(errors, [])
assert.deepEqual(compileTemplate({ source: descriptor.template.content, filename, id: 'pedido' }).errors, [])
const t = descriptor.template.content
assert.match(t, /v-if="noCarrinho > 1"[^>]*data-carrinho-ml>Carrinho com \{\{ noCarrinho \}\} pedidos/)
assert.match(t, /v-if="pm\.pack"[^>]*data-carrinho-ml-pack/)
assert.match(t, /v-if="it\.pedido && noCarrinho > 1"[^>]*data-pedido-do-item/)
assert.match(t, /copiarTexto\(it\.pedido, 'Nº do pedido'\)/)
assert.match(descriptor.scriptSetup.content, /const noCarrinho = computed\(\(\) => pm\.value\?\.pedidos\?\.length \|\| 0\)/)

const plataforma = fs.readFileSync(path.resolve(__dirname, '../components/AtendimentoPlataforma.vue'), 'utf8')
assert.match(plataforma, /pack: textoOuNull\(o\.pack\)/)
assert.match(plataforma, /pedidos: Array\.isArray\(o\.pedidos\)/)
assert.match(plataforma, /pedido: textoOuNull\(o\.pedido\),\n\s+titulo/)

console.log('atendimento-carrinho-ml: ok')
