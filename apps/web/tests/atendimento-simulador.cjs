// node tests/atendimento-simulador.cjs — o aviso do simulador com a exceção (SEG-03, 28/09/2026).
// Com o simulador ligado e a Amazon na exceção, a resposta na Amazon CHEGA ao
// comprador: a faixa do topo e o aviso depois do envio não podem dizer o contrário.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const { parse } = require('vue/compiler-sfc')

const filename = path.resolve(__dirname, '../components/AtendimentoPlataforma.vue')
const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
assert.deepEqual(errors, [])
// O <script> comum (não o setup) é o módulo dos ajudantes exportados.
const js = ts.transpileModule(descriptor.script.content, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText
const mod = { exports: {} }
new Function('require', 'module', 'exports', js)((nome) => require(nome), mod, mod.exports)
const { avisoSimulador, envioSimulado, foraDoSimulador } = mod.exports

// Sem simulador: nada a avisar, nada simulado.
assert.equal(avisoSimulador({ simulador: false, simulador_exceto: ['amazon'] }), null)
assert.equal(avisoSimulador(null), null)
assert.deepEqual(foraDoSimulador({ simulador: false, simulador_exceto: ['amazon'] }), [])
assert.equal(envioSimulado(null, { simulador: false }, 'shopee'), false)

// Simulador sem exceção: o aviso de sempre.
const tudo = avisoSimulador({ simulador: true, simulador_exceto: [] })
assert.match(tudo.curto, /NÃO chega ao comprador/)
assert.equal(envioSimulado(null, { simulador: true }, 'amazon'), true)

// Com a Amazon na exceção: a faixa diz que na Amazon CHEGA.
const amazon = avisoSimulador({ simulador: true, simulador_exceto: ['amazon'] })
assert.equal(amazon.curto, 'Simulador ligado, EXCETO Amazon: resposta na Amazon CHEGA ao comprador')
assert.match(amazon.texto, /na Amazon a resposta sai de verdade e CHEGA ao comprador/)
assert.doesNotMatch(amazon.curto, /NÃO chega/)
// Duas plataformas na exceção (e caixa/espaço como vierem).
const duas = avisoSimulador({ simulador: true, simulador_exceto: [' Amazon ', 'ml'] })
assert.equal(duas.curto, 'Simulador ligado, EXCETO Amazon e Mercado Livre: resposta em Amazon e Mercado Livre CHEGA ao comprador')

// Depois do envio: sem o campo da mensagem, a plataforma contra as flags...
const flags = { simulador: true, simulador_exceto: ['amazon'] }
assert.equal(envioSimulado(null, flags, 'amazon'), false)
assert.equal(envioSimulado(undefined, flags, 'shopee'), true)
// ...e o que o envio gravou manda (a chave pode ter mudado depois).
assert.equal(envioSimulado({ simulado: false }, flags, 'shopee'), false)
assert.equal(envioSimulado({ simulado: true }, { simulador: false }, 'amazon'), true)

// A tela usa os ajudantes (e não o `flags.simulador` cru).
const pagina = fs.readFileSync(path.resolve(__dirname, '../pages/atendimento.vue'), 'utf8')
assert.match(pagina, /avisoSimulador\(f\)/)
assert.doesNotMatch(pagina, /if \(f\.simulador\) out\.push/)
const conversa = fs.readFileSync(path.resolve(__dirname, '../components/AtendimentoConversa.vue'), 'utf8')
assert.match(conversa, /envioSimulado\(m, props\.flags, plataforma\)/)
assert.doesNotMatch(conversa, /props\.flags\?\.simulador \?/)

console.log('ok: atendimento-simulador')
