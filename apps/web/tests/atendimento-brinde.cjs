// node tests/atendimento-brinde.cjs — o brinde do pedido não pago na tela (05/11/2026).
//  - os botões: "Adicionar AirTag ao pedido" / "Adicionar chaveiro ao pedido" e
//    "Não adicionar", com a confirmação e o resultado de verdade (verde confirmado,
//    âmbar incerto, vermelho recusa);
//  - o indicador "Brindes a adicionar (N)": sem pendente nem alerta, nada aparece;
//  - o painel do pedido traz o bloco, e a página de atendimento traz o indicador.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const { parse, compileTemplate } = require('vue/compiler-sfc')

const transpile = (source) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText

function sfc(rel) {
  const filename = path.resolve(__dirname, rel)
  const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
  assert.deepEqual(errors, [], rel)
  const compiled = compileTemplate({ source: descriptor.template.content, filename, id: path.basename(rel) })
  assert.deepEqual(compiled.errors, [], `${rel}: template compila`)
  return descriptor
}
function exportsDe(descriptor) {
  const exp = {}
  new Function('exports', 'require', transpile(descriptor.script.content))(exp, require)
  return exp
}

const acoes = sfc('../components/AtendimentoBrindeAcoes.vue')
const a = exportsDe(acoes)
assert.equal(a.textoDoBotao('AirTag'), 'Adicionar AirTag ao pedido')
assert.equal(a.textoDoBotao('chaveiro'), 'Adicionar chaveiro ao pedido')
assert.equal(a.textoDoBotao(null), 'Adicionar brinde ao pedido')
assert.equal(a.urlDaAcao('i-1', 'SN/1', 'adicionar'), '/api/atendimento/brindes/i-1/SN%2F1/adicionar')
assert.equal(a.urlDaAcao('i-1', 'SN1', 'dispensar'), '/api/atendimento/brindes/i-1/SN1/dispensar')
assert.equal(a.nivelDoResultado({ ok: true }, null), 'ok')
assert.equal(a.nivelDoResultado({ ok: false }, null), 'incerto')
assert.equal(a.nivelDoResultado(null, 'recusado'), 'erro')
assert.equal(a.nivelDoResultado(null, null), null)
for (const marca of ['data-brinde-adicionar', 'data-brinde-dispensar', 'data-brinde-confirmar-sim', 'data-brinde-resultado', 'Não adicionar']) {
  assert.ok(acoes.template.content.includes(marca), marca)
}
// Nada escreve sem a confirmação: o botão só abre o `confirmando`.
assert.ok(acoes.template.content.includes(`@click="confirmando = 'adicionar'"`))
assert.ok(acoes.template.content.includes(`@click="confirmando = 'dispensar'"`))
assert.ok(acoes.template.content.includes('@click="executar"'))

const lista = sfc('../components/AtendimentoBrindesLista.vue')
const l = exportsDe(lista)
assert.equal(l.textoDoIndicador(0, 0), null, 'sem pendente nem alerta, nada aparece')
assert.equal(l.textoDoIndicador(3, 0), 'Brindes a adicionar (3)')
assert.equal(l.textoDoIndicador(2, 5), 'Brindes a adicionar (2)')
assert.equal(l.textoDoIndicador(0, 1), 'Brindes: 1 alerta')
assert.equal(l.textoDoIndicador(0, 2), 'Brindes: 2 alertas')
assert.equal(l.haQuantoTempo(0.4), 'há menos de 1 h')
assert.equal(l.haQuantoTempo(3.2), 'há 3 h')
assert.equal(l.prazoDaNf(0), 'a NF já pode seguir')
assert.equal(l.prazoDaNf(8.2), 'a NF segue em 9 h')
assert.ok(lista.template.content.includes('v-if="texto"'), 'o indicador só com texto')
assert.ok(lista.template.content.includes('<AtendimentoBrindeAcoes'))
assert.ok(lista.template.content.includes('data-brinde-alerta'))

const pedido = sfc('../components/AtendimentoPedido.vue')
assert.ok(pedido.template.content.includes('data-brinde-pedido'))
assert.ok(pedido.template.content.includes('<AtendimentoBrindeAcoes'))
assert.ok(pedido.template.content.includes(`@mudou="emit('recarregarPainel', false)"`))

const pagina = fs.readFileSync(path.resolve(__dirname, '../pages/atendimento.vue'), 'utf8')
assert.ok(pagina.includes('<AtendimentoBrindesLista @abrir-conversa="abrirConversaNaCaixa"'))

console.log('atendimento-brinde: ok')
