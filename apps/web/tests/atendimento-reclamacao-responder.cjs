// node tests/atendimento-reclamacao-responder.cjs — responder a reclamação do
// ML pelo DaVinci (28/10/2026, "responder a mediação com o Meli como no Duoke").
// A caixa da conversa da reclamação ganha as abas "Para o comprador" e "Para o
// Mercado Livre (mediador)" — só as que o ML libera agora (`envio.destinos`) —,
// o destino segue a aba de mensagens (Mediador → mediador, Reclamação →
// comprador) e vai no envio (`para`) do texto e do anexo; o balão da loja diz a
// quem foi. Aqui: o contrato com a API, a regra pura do destino, o estado da
// conversa (os watchers de verdade) e o que a tela liga.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')

const transpile = (source) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText
function sfc(rel) {
  const filename = path.resolve(__dirname, rel)
  const fonte = fs.readFileSync(filename, 'utf8')
  const { descriptor, errors } = parse(fonte, { filename })
  assert.deepEqual(errors, [], rel)
  const tpl = compileTemplate({ source: descriptor.template.content, filename, id: path.basename(rel), compilerOptions: { isTS: true } })
  assert.deepEqual(tpl.errors, [], `${rel}: template compila`)
  compileScript(descriptor, { id: path.basename(rel) })
  return { descriptor, fonte }
}
function exportsDe(descriptor) {
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(descriptor.script.content))(require, mod, mod.exports)
  return mod.exports
}
const api = (rel) => fs.readFileSync(path.resolve(__dirname, '../../api/app', rel), 'utf8')

const plataforma = sfc('../components/AtendimentoPlataforma.vue')
const P = exportsDe(plataforma.descriptor)
const conversa = sfc('../components/AtendimentoConversa.vue')
const setup = conversa.descriptor.scriptSetup.content
const tela = conversa.descriptor.template.content
const painel = api('routers/atendimento_painel.py')
const schemas = api('schemas/atendimento.py')
const constantes = api('services/atendimento/constantes.py')

async function principal() {
  // ------------------------------------------------ o contrato com a API
  // Os destinos e os rótulos são os do backend (constantes), na mesma ordem.
  const rotulos = Object.fromEntries([...constantes.matchAll(/^ {4}PARA_(COMPRADOR|MEDIADOR): "([^"]+)",$/gm)].map((m) => [m[1].toLowerCase(), m[2]]))
  assert.deepEqual(rotulos, { comprador: 'Para o comprador', mediador: 'Para o Mercado Livre (mediador)' })
  assert.match(constantes, /^PARA_COMPRADOR = "comprador"$/m)
  assert.match(constantes, /^PARA_MEDIADOR = "mediador"$/m)
  assert.deepEqual(Object.keys(P.ABA_DO_DESTINO).sort(), ['comprador', 'mediador'])
  // O corpo do /responder e o formulário da foto levam `para` (só esses dois valores).
  assert.match(schemas, /para: Literal\["comprador", "mediador"\] \| None = None/)
  assert.match(painel, /para: Annotated\[Literal\["comprador", "mediador"\] \| None, Form\(\)\] = None/)
  // O `envio` do detalhe traz os destinos; a mensagem, a quem foi.
  assert.match(schemas, /class DestinoOut\(BaseModel\):\n[\s\S]*?chave: str\n {4}rotulo: str/)
  assert.match(schemas, /destinos: list\[DestinoOut\]/)
  assert.match(plataforma.fonte, /destinos\?: DestinoReclamacao\[\]/)
  assert.match(plataforma.fonte, /para\?: string \| null/)

  // ------------------------------------------------ a regra pura do destino
  const so = (...ch) => ch.map((c) => ({ chave: c, rotulo: c }))
  assert.equal(P.destinoDaCaixa([], 'mediador', 'mediador'), null, 'nada liberado: sem destino')
  assert.equal(P.destinoDaCaixa(null, null, null), null)
  assert.equal(P.destinoDaCaixa(so('mediador'), 'reclamacao', null), 'mediador', 'na mediação, mesmo na aba Reclamação, só o mediador')
  assert.equal(P.destinoDaCaixa(so('comprador'), 'mediador', null), 'comprador')
  assert.equal(P.destinoDaCaixa(so('comprador', 'mediador'), 'mediador', 'comprador'), 'mediador', 'a aba de mensagens manda')
  assert.equal(P.destinoDaCaixa(so('comprador', 'mediador'), 'pos_venda', 'mediador'), 'mediador', 'fora das duas abas, fica o escolhido')
  assert.equal(P.destinoDaCaixa(so('comprador', 'mediador'), null, 'outro'), 'comprador', 'o escolhido que saiu da lista: o primeiro')

  // ------------------------------------------------ o estado da caixa (os watchers de verdade)
  {
    const ini = setup.indexOf('// Reclamação do ML (28/10/2026): para quem a caixa escreve.')
    const fim = setup.indexOf('function escolherDestino')
    assert.ok(ini > 0 && fim > ini)
    const js = transpile(setup.slice(ini, fim)) + '\nreturn { destinosReclamacao, destinoCaixa, rotuloDestino }'
    const detalhe = Vue.ref(null)
    const conversaRef = Vue.computed(() => detalhe.value?.conversa ?? null)
    const abaAtiva = Vue.ref(null)
    const e = new Function('computed', 'ref', 'watch', 'conversa', 'detalhe', 'abaAtiva', 'destinoDaCaixa', js)(
      Vue.computed, Vue.ref, Vue.watch, conversaRef, detalhe, abaAtiva, P.destinoDaCaixa,
    )
    const tique = () => new Promise((r) => setTimeout(r, 0))
    assert.deepEqual(e.destinosReclamacao.value, [])
    assert.equal(e.destinoCaixa.value, null)
    // A KFA em mediação: só o mediador.
    detalhe.value = { conversa: { id: 'c-recl', canal: 'reclamacao' }, envio: { destinos: [{ chave: 'mediador', rotulo: 'Para o Mercado Livre (mediador)' }] } }
    await tique()
    assert.equal(e.destinoCaixa.value, 'mediador')
    assert.equal(e.rotuloDestino.value, 'Para o Mercado Livre (mediador)')
    abaAtiva.value = 'reclamacao'
    await tique()
    assert.equal(e.destinoCaixa.value, 'mediador', 'o comprador não está liberado na mediação')
    // Fora da reclamação, nada de destino (o `envio.destinos` de outra conversa não conta).
    detalhe.value = { conversa: { id: 'c-pack', canal: 'pos_venda' }, envio: { destinos: [{ chave: 'mediador', rotulo: 'x' }] } }
    await tique()
    assert.deepEqual(e.destinosReclamacao.value, [])
    assert.equal(e.destinoCaixa.value, null)
  }

  // ------------------------------------------------ o que a tela liga
  // O texto e o anexo levam o destino; depois do envio, a aba da conversa com ele.
  assert.match(setup, /if \(destinosReclamacao\.value\.length && destinoCaixa\.value\) body\.para = destinoCaixa\.value/)
  assert.match(setup, /if \(destinosReclamacao\.value\.length && destinoCaixa\.value\) fd\.append\('para', destinoCaixa\.value\)/)
  assert.match(setup, /if \(body\.para && m\?\.status !== 'falhou'\) await mostrarAbaDoDestino\(id, body\.para\)/)
  // As abas de destino no lugar do "Responder", uma por destino liberado.
  assert.match(tela, /v-for="dst in destinosReclamacao"[\s\S]*?:data-destino="dst\.chave"[\s\S]*?@click="escolherDestino\(dst\.chave\)"[\s\S]*?\{\{ dst\.rotulo \}\}/)
  assert.match(tela, /<button\s+v-else\s+type="button"\s+role="tab"[\s\S]*?>Responder<\/button>/)
  // A faixa não diz mais "só leitura" quando há destino; o balão da loja diz a quem foi.
  const faixa = tela.slice(tela.indexOf('data-faixa-reclamacao'), tela.indexOf('</div>', tela.indexOf('data-faixa-reclamacao')))
  assert.doesNotMatch(faixa, /só leitura no DaVinci/)
  assert.match(faixa, /v-if="destinosReclamacao\.length"/)
  assert.match(tela, /data-msg-para>→ \{\{ l\.m\.para === 'mediador' \? 'Mercado Livre \(mediador\)' : 'comprador' \}\}/)

  // As frases das falhas da reclamação (conferida no ML na hora: nada saiu).
  assert.match(P.erroEnvioLegivel('reclamacao_sem_acao:send_message_to_mediator'), /etapa da reclamação mudou/)
  assert.match(P.erroEnvioLegivel('reclamacao_encerrada'), /encerrada/)
  assert.match(P.erroEnvioLegivel('reclamacao_ilegivel:ConnectError'), /nada saiu/)
  assert.match(P.erroEnvioLegivel('ml upload HTTP 400 invalid_file'), /anexo não subiu/)

  console.log('ok — atendimento-reclamacao-responder: contrato, destino, caixa e envio')
}

principal().catch((e) => {
  console.error(e)
  process.exit(1)
})
