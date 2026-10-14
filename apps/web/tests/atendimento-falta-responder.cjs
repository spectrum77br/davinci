// node tests/atendimento-falta-responder.cjs — o "Falta responder" igual ao Duoke (14/10/2026).
// O dono: "tem que arrumar pro atendimento ter literalmente o comportamento do
// Duoke, pra estar marcado literalmente as mesmas". Quem decide é o servidor
// (services/atendimento/pendente.py); a tela só precisa:
//  - manter o rótulo "Falta responder" e explicar a régua nova no title da aba
//    (qualquer mensagem da loja responde, até a automática; a não lida conta);
//  - reler o /resumo quando a linha aberta entra ou sai do "Falta responder"
//    (`falta_responder` da linha), como já fazia com o `aguardando_resposta`;
//  - MARCAR a linha pela régua do Duoke ("marcado literalmente as mesmas"):
//    negrito e bolinha só no "Falta responder" (a bolinha = o maior entre as
//    do comprador sem resposta e as não lidas); na Caixa Humano e com a API
//    antiga, pela fila; o selo de prazo é o da fila e o title diz quando o
//    Duoke já dá a conversa por respondida;
//  - e o contrato com a API: o /resumo conta pela régua nova (o `aguardando`
//    da plataforma e da loja), o filtro `aguardando` da Caixa também — desde
//    14/10/2026 só nas conversas de chat (`falta_responder_no_chat`) —, e a
//    linha traz o `falta_responder` (em todo canal).
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
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(descriptor.script.content))((n) => require(n), mod, mod.exports)
  return mod.exports
}
const ler = (rel) => fs.readFileSync(path.resolve(__dirname, rel), 'utf8')

// ------------------------------------------------ a aba: o mesmo rótulo, a régua no title
const L = exportsDe(sfc('../components/AtendimentoLista.vue'))
const aba = L.ABAS_LISTA.find((a) => a.value === 'aguardando')
assert.ok(aba, 'a aba "aguardando" continua')
assert.equal(aba.label, 'Falta responder', 'o rótulo não muda')
assert.match(aba.hint, /Duoke/, 'o title diz que é a régua do Duoke')
assert.match(aba.hint, /até a automática/, 'a mensagem automática responde')
assert.match(aba.hint, /não lida/, 'a não lida na plataforma conta')
assert.doesNotMatch(aba.hint, /resposta automática não conta/, 'a régua antiga saiu do title')
// A FILA (prazo) continua com os filtros dela no menu — e o title diz que é a fila.
const menu = Object.fromEntries(L.FILTROS_MENU.map((f) => [f.value, f]))
assert.ok(menu.vencendo && menu.vencidas && menu.automatica, 'Vencendo, Vencidas e Só resposta automática ficam')
assert.match(menu.vencendo.hint, /prazo da fila/)
assert.match(menu.vencidas.hint, /prazo da fila/)
assert.match(menu.vencidas.hint, /automática não conta/)

// ------------------------------------------------ a marca da linha
const linha = (x) => ({ aguardando_resposta: false, nao_lidas: 0, pendentes: 0, ...x })
// O menu do Duoke depois do comprador: a fila espera, o Duoke dá por respondida.
const menuDuoke = linha({ aguardando_resposta: true, falta_responder: false, pendentes: 2 })
assert.equal(L.linhaMarcada(menuDuoke), false, 'sem negrito na Caixa')
assert.equal(L.bolinhaDaLinha(menuDuoke), 0, 'sem bolinha na Caixa')
assert.equal(L.linhaMarcada(menuDuoke, true), true, 'na Caixa Humano, pela fila')
assert.equal(L.bolinhaDaLinha(menuDuoke, true), 2)
assert.match(L.tituloDoPrazo(menuDuoke, 'prazo venceu em 14/10'), /^prazo venceu em 14\/10 — prazo da fila: só a resposta automática/)
assert.equal(L.tituloDoPrazo(menuDuoke, 'prazo venceu em 14/10', true), 'prazo venceu em 14/10')
// Não lida na plataforma (entra pela régua, sem a fila): negrito e o número da plataforma.
const naoLida = linha({ falta_responder: true, nao_lidas: 3 })
assert.equal(L.linhaMarcada(naoLida), true)
assert.equal(L.bolinhaDaLinha(naoLida), 3)
// O comprador por último: o maior dos dois números.
assert.equal(L.bolinhaDaLinha(linha({ aguardando_resposta: true, falta_responder: true, pendentes: 4, nao_lidas: 1 })), 4)
// A respondida que a plataforma ainda diz não lida (o ML): nada.
assert.equal(L.bolinhaDaLinha(linha({ falta_responder: false, nao_lidas: 5 })), 0)
// A API antiga (sem o campo): como antes, pela fila.
assert.equal(L.linhaMarcada(linha({ aguardando_resposta: true, pendentes: 1 })), true)
assert.equal(L.bolinhaDaLinha({ aguardando_resposta: true, nao_lidas: 2 }), 2)
const lista = ler('../components/AtendimentoLista.vue')
assert.match(lista, /:nao-lidas="bolinhaDaLinha\(c, emHumano\)"/, 'a bolinha da linha')
assert.match(lista, /:class="linhaMarcada\(c, emHumano\) \? 'font-semibold' : 'font-medium'"/, 'o negrito da linha')
assert.equal((lista.match(/:title="tituloDoPrazo\(c, prazoDe\(c, agora\)!\.titulo, emHumano\)"/g) || []).length, 2, 'os dois selos de prazo')
assert.doesNotMatch(lista, /c\.pendentes \?\? c\.nao_lidas"/, 'a bolinha antiga saiu do template')

// ------------------------------------------------ a página relê os números quando a linha muda de lado
const pagina = ler('../pages/atendimento.vue')
const aoMudar = (pagina.match(/function aoMudarConversa\([\s\S]*?\n}\n/) || [])[0]
assert.ok(aoMudar, 'aoMudarConversa existe')
assert.match(aoMudar, /c\.falta_responder !== undefined && !!antes\.falta_responder !== !!c\.falta_responder/,
  'entrou/saiu do "Falta responder" → relê o /resumo (a API antiga, sem o campo, não dispara)')
assert.match(aoMudar, /carregarResumo\(\)/)
const tipos = ler('../components/AtendimentoPlataforma.vue')
assert.match(tipos, /falta_responder\?: boolean/, 'a linha tem o campo (opcional: a API antiga não manda)')

// ------------------------------------------------ o contrato com a API
const router = ler('../../api/app/routers/atendimento.py')
const resumo = (router.match(/async def resumo\([\s\S]*?\n\nasync def _leitura_parada/) || [])[0]
assert.ok(resumo, 'o /resumo existe')
assert.match(resumo, /falta = pendente\.falta_responder_no_chat_sql\(\)/, 'só o chat (14/10/2026)')
assert.equal((resumo.match(/func\.count\(\)\.filter\(falta\)/g) || []).length, 2, 'por plataforma e por loja')
assert.equal((resumo.match(/func\.count\(\)\.filter\(aguardando\)/g) || []).length, 0, 'o número não é mais a fila')
assert.match(router, /aguardando if caixa_humano or caixa_email else pendente\.falta_responder_no_chat_sql\(\)/,
  'o filtro da Caixa é a régua nova, só no chat; a Caixa Humano e a aba E-mail ficam na fila')
assert.match(router, /"falta_responder": pendente\.falta_responder\(c\)/, 'a linha traz o campo')
const schemas = ler('../../api/app/schemas/atendimento.py')
assert.match(schemas, /falta_responder: bool = False/)
const regua = ler('../../api/app/services/atendimento/pendente.py')
assert.match(regua, /def falta_responder_sql\(/)
assert.match(regua, /c\.nao_lidas > 0/)
assert.match(regua, /ultima_loja_qualquer_em/)
assert.match(regua, /NAO_LIDA_CONTA_EM[^=]*= \(\("shopee", CANAL_CHAT\), \("tiktok", CANAL_CHAT\)\)/, 'o não lido só no chat da Shopee/TikTok')
assert.match(regua, /CHAVE_VEZ_DA_LOJA/, 'onde a plataforma diz de quem é a vez, a fila')

console.log('ok — atendimento-falta-responder')
