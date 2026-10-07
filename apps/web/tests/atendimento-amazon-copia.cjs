// node tests/atendimento-amazon-copia.cjs — a cópia do Seller Central que
// EMPATOU entre duas conversas do mesmo comprador (08/10/2026, caso KIA do
// dia 07: "respondi pela conta e não apareceu no DaVinci"). O aviso "o
// DaVinci não sabe qual foi" virou a escolha de 1 clique "Esta foi a
// respondida" / "Não foi esta", num componente próprio
// (AtendimentoAmazonCopia), que a conversa inclui numa linha no lugar do
// aviso antigo. Aqui:
//  - o contrato com a API (CopiaAmazonOut, a rota POST .../amazon-copia);
//  - as funções puras (quais cópias mostrar, a frase);
//  - o componente renderizado (Vue SSR): os botões só para quem mexe; quem
//    só lê vê o aviso; a marca antiga (sem o texto) só avisa, como antes;
//  - o clique: POST com o Message-ID, some na hora, a conversa é relida;
//    404 (outra pessoa resolveu) também some;
//  - a conversa: uma linha de inclusão, o aviso antigo saiu.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { renderToString } = require('vue/server-renderer')
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
  return { descriptor, fonte, filename }
}
function exportsDe(descriptor) {
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(descriptor.script.content))(require, mod, mod.exports)
  return mod.exports
}
const api = (rel) => fs.readFileSync(path.resolve(__dirname, '../../api/app', rel), 'utf8')

const P = exportsDe(sfc('../components/AtendimentoPlataforma.vue').descriptor)
const copiaSfc = sfc('../components/AtendimentoAmazonCopia.vue')
const C = exportsDe(copiaSfc.descriptor)
const conversaSfc = sfc('../components/AtendimentoConversa.vue')

const EM = '2026-10-07T11:27:56+00:00'
const NOVA = { message_id: '<rb@amazon.com>', em: EM, texto: 'O reembolso sai quando o\nproduto chegar.', pedido: null, outras: 1, pode_escolher: true }
function conversa(o = {}) {
  return { id: 'c1', plataforma: 'amazon', amazon_copia_a_conferir_em: EM, amazon_copias_a_conferir: [NOVA], ...o }
}

// ------------------------------------------------ o contrato com a API
{
  const schemas = api('schemas/atendimento.py')
  const bloco = (schemas.match(/^class CopiaAmazonOut\(BaseModel\):([\s\S]*?)^class /m) || [])[1] || ''
  const campos = [...bloco.matchAll(/^ {4}([a-z_]+): /gm)].map((m) => m[1])
  const tipo = (copiaSfc.descriptor.script.content.match(/export type CopiaAmazon = \{([\s\S]*?)\n\}/) || [])[1] || ''
  const chavesTs = [...tipo.matchAll(/^ {2}([a-z_]+)\??: /gm)].map((m) => m[1])
  assert.deepEqual([...chavesTs].sort(), [...campos].sort(), 'o tipo da tela = o CopiaAmazonOut')
  assert.match(schemas, /^ {4}amazon_copias_a_conferir: list\[CopiaAmazonOut\] = Field\(default_factory=list\)$/m)
  const entrada = (schemas.match(/^class CopiaAmazonIn\(BaseModel\):([\s\S]*?)^class /m) || [])[1] || ''
  assert.deepEqual([...entrada.matchAll(/^ {4}([a-z_]+): /gm)].map((m) => m[1]), ['message_id', 'foi_esta'])
  const rota = api('routers/atendimento.py')
  assert.match(rota, /@router\.post\("\/conversas\/\{conversa_id\}\/amazon-copia", response_model=ConversaUnicaOut\)/)
  // Só quem mexe: a trava do router (POST fora de ROTAS_DE_QUEM_LE) + o edit.
  assert.match(rota, /async def escolher_copia_amazon\([\s\S]*?user: Annotated\[User, Depends\(_edit\)\],/)
  assert.doesNotMatch((rota.match(/^ROTAS_DE_QUEM_LE[\s\S]*?\n\}/m) || [''])[0], /amazon-copia/)
  // O serviço: o pedido do MODELO da cópia; empate nunca desempata sozinho.
  const servico = api('services/atendimento/amazon_email.py')
  assert.match(servico, /_RE_PEDIDO_DO_MODELO = re\.compile\(/)
  assert.doesNotMatch(servico, /_unica_esperando/)
  assert.match(servico, /async def resolver_copia\(/)
}

// ------------------------------------------------ funções puras
{
  assert.deepEqual(C.copiasDaConversa(conversa()), [NOVA])
  assert.deepEqual(C.copiasDaConversa(conversa({ plataforma: 'ml' })), [])
  assert.deepEqual(C.copiasDaConversa(null), [])
  assert.deepEqual(C.copiasDaConversa(conversa({ amazon_copias_a_conferir: [], amazon_copia_a_conferir_em: null })), [])
  // API antiga (só a hora): um aviso sem escolha.
  assert.deepEqual(
    C.copiasDaConversa({ id: 'c1', plataforma: 'amazon', amazon_copia_a_conferir_em: EM }),
    [{ message_id: '', em: EM, pode_escolher: false }],
  )
  // Item torto da API não quebra a tela.
  assert.deepEqual(C.copiasDaConversa(conversa({ amazon_copias_a_conferir: [null, { em: EM }, NOVA] })), [NOVA])

  assert.equal(C.fraseDaCopia(NOVA, '07/10, 08:27'), 'O Seller Central respondeu alguém com este nome em 07/10, 08:27, e há outra conversa com o mesmo nome: o DaVinci não sabe qual foi.')
  assert.match(C.fraseDaCopia({ ...NOVA, outras: 2 }, 'x'), /há outras 2 conversas com o mesmo nome/)
  assert.match(C.fraseDaCopia({ ...NOVA, outras: 0 }, 'x'), /o DaVinci não ligou sozinho a resposta a esta conversa\.$/)
  assert.equal(
    C.fraseDaCopia({ message_id: '', em: EM, pode_escolher: false }, '07/10, 08:27'),
    'O Seller Central respondeu alguém com este nome em 07/10, 08:27, mas há outra conversa com o mesmo nome e o DaVinci não sabe qual foi.',
    'a marca antiga: a frase de sempre',
  )
}

// ------------------------------------------------ o componente
function requerer(nome) {
  if (nome === 'lucide-vue-next') return new Proxy({}, { get: () => ({ render: () => Vue.h('i') }) })
  if (nome === '~/components/AtendimentoPlataforma.vue') return P
  return require(nome)
}
function globais(chamadas, { falha = null } = {}) {
  const toasts = []
  return {
    computed: Vue.computed,
    ref: Vue.ref,
    watch: Vue.watch,
    useApi: () => ({
      api: async (url, opts) => {
        chamadas.push([url, opts])
        if (falha) throw falha
        return { conversa: { id: 'c1' } }
      },
    }),
    useToasts: () => ({ success: (...a) => toasts.push(['ok', ...a]), error: (...a) => toasts.push(['erro', ...a]) }),
    toasts,
  }
}
async function renderizar(props, g = globais([])) {
  const { content } = compileScript(copiaSfc.descriptor, { id: 'teste', inlineTemplate: true })
  const mod = {}
  const nomes = ['computed', 'ref', 'watch', 'useApi', 'useToasts']
  new Function('exports', 'require', ...nomes, transpile(content))(mod, requerer, ...nomes.map((n) => g[n]))
  const app = Vue.createSSRApp(mod.default, props)
  return (await renderToString(app)).replace(/<!--[\s\S]*?-->/g, '')
}
function montar(props, g) {
  const script = compileScript(copiaSfc.descriptor, { id: 'teste', inlineTemplate: false })
  const mod = { exports: {} }
  const nomes = ['computed', 'ref', 'watch', 'useApi', 'useToasts']
  new Function('require', 'module', 'exports', ...nomes, transpile(script.content))(requerer, mod, mod.exports, ...nomes.map((n) => g[n]))
  const emitidos = []
  const reativo = Vue.reactive({ ...props })
  const estado = Vue.proxyRefs(mod.exports.default.setup(reativo, { emit: (...a) => emitidos.push(a), expose: () => {}, attrs: {}, slots: {} }))
  return { estado, emitidos }
}

async function principal() {
  const caso = 'https://sellercentral.amazon.com.br/messaging/inbox?fi=caseId&ss=abc12345&cc=abc12345'
  // Quem mexe: a frase, a resposta da loja (o que ela escreveu no Seller
  // Central) e os dois botões.
  let html = await renderizar({ conversa: conversa(), podeMexer: true, linkCaso: caso })
  assert.match(html, /data-amazon-copia/)
  assert.match(html, /há outra conversa com o mesmo nome: o DaVinci não sabe qual foi\.\s*Esta resposta foi para esta conversa\?/)
  assert.match(html, /<blockquote[^>]*data-copia-texto[^>]*>O reembolso sai quando o\nproduto chegar\.<\/blockquote>/)
  assert.match(html, /data-copia-foi-esta[^>]*>[\s\S]*?Esta foi a respondida/)
  assert.match(html, /data-copia-nao-foi[^>]*>[\s\S]*?Não foi esta/)
  assert.match(html, /href="https:\/\/sellercentral\.amazon\.com\.br\/messaging\/inbox\?fi=caseId&amp;ss=abc12345&amp;cc=abc12345"[^>]*>conferir no Seller Central/)
  // Quem só lê: o aviso e a resposta, sem botão.
  html = await renderizar({ conversa: conversa(), podeMexer: false, linkCaso: null })
  assert.doesNotMatch(html, /Esta foi a respondida|Não foi esta/)
  assert.match(html, /quem cuida do Atendimento escolhe qual foi/)
  // A marca antiga (sem o texto): só o aviso de sempre, mesmo para quem mexe.
  html = await renderizar({ conversa: conversa({ amazon_copias_a_conferir: [{ message_id: '<x@amazon.com>', em: EM, outras: 0, pode_escolher: false }] }), podeMexer: true, linkCaso: caso })
  assert.match(html, /mas há outra conversa com o mesmo nome e o DaVinci não sabe qual foi\.\s*Confira <a[^>]*>no Seller Central/)
  assert.doesNotMatch(html, /Esta foi a respondida/)
  // Nada a conferir (ou outra plataforma): nada.
  assert.equal((await renderizar({ conversa: conversa({ amazon_copias_a_conferir: [] , amazon_copia_a_conferir_em: null }), podeMexer: true })).trim(), '')
  assert.equal((await renderizar({ conversa: conversa({ plataforma: 'shopee' }), podeMexer: true })).trim(), '')

  // O clique: POST com o Message-ID; some na hora; a conversa é relida.
  let chamadas = []
  let g = globais(chamadas)
  let m = montar({ conversa: conversa(), podeMexer: true }, g)
  assert.equal(m.estado.copias.length, 1)
  await m.estado.escolher(NOVA, true)
  assert.deepEqual(chamadas, [['/api/atendimento/conversas/c1/amazon-copia', { method: 'POST', body: { message_id: '<rb@amazon.com>', foi_esta: true } }]])
  assert.equal(m.estado.copias.length, 0)
  assert.deepEqual(m.emitidos, [['recarregar']])
  assert.equal(g.toasts[0][0], 'ok')
  // "Não foi esta": foi_esta=false.
  chamadas = []
  m = montar({ conversa: conversa(), podeMexer: true }, globais(chamadas))
  await m.estado.escolher(NOVA, false)
  assert.equal(chamadas[0][1].body.foi_esta, false)
  // 404: outra pessoa (ou a leitura) já resolveu — some também, e relê.
  chamadas = []
  g = globais(chamadas, { falha: Object.assign(new Error('404'), { statusCode: 404, data: { detail: { code: 'copia_nao_encontrada' } } }) })
  m = montar({ conversa: conversa(), podeMexer: true }, g)
  await m.estado.escolher(NOVA, true)
  assert.equal(m.estado.copias.length, 0)
  assert.deepEqual(m.emitidos, [['recarregar']])
  // Erro de verdade: a escolha continua na tela, com o erro.
  g = globais([], { falha: Object.assign(new Error('500'), { statusCode: 500 }) })
  m = montar({ conversa: conversa(), podeMexer: true }, g)
  await m.estado.escolher(NOVA, true)
  assert.equal(m.estado.copias.length, 1)
  assert.equal(g.toasts[0][0], 'erro')
  // A marca antiga não tem o que mandar.
  chamadas = []
  m = montar({ conversa: conversa(), podeMexer: true }, globais(chamadas))
  await m.estado.escolher({ message_id: '', em: EM }, true)
  assert.deepEqual(chamadas, [])
}

// ------------------------------------------------ a conversa: uma linha
{
  const t = conversaSfc.descriptor.template.content
  const linhas = t.split('\n').filter((l) => l.includes('AtendimentoAmazonCopia'))
  assert.deepEqual(linhas.map((l) => l.trim()), [
    '<AtendimentoAmazonCopia v-if="conversa.plataforma === \'amazon\'" :conversa="conversa" :pode-mexer="canEdit" :link-caso="amazon.caso" @recarregar="carregar(conversa.id, true)" />',
  ])
  assert.doesNotMatch(t, /amazon_copia_a_conferir_em/, 'o aviso antigo saiu da conversa')
  // No mesmo lugar: depois do "qual conta Amazon é esta?", antes do "não precisa de resposta".
  const i = t.indexOf('<AtendimentoAmazonCopia')
  assert.ok(t.indexOf('Qual conta Amazon é esta?') < i && i < t.indexOf('Marcada como "não precisa de resposta".'))
}

principal().then(
  () => console.log('ok: atendimento-amazon-copia'),
  (e) => { console.error(e); process.exit(1) },
)
