// node tests/atendimento-carrinho.cjs — CARRINHO ABANDONADO dos sites no /atendimento (RF9, 02/10/2026).
// O cartão (no topo) da conversa `carrinho` (AtendimentoCarrinho.vue):
//  - o contrato com o backend (as rotas, os campos do CarrinhoOut, os status do
//    estoque, a régua dos 7 dias e o aviso "nada é mandado ao lojista");
//  - os ajudantes puros: o selo da situação, o selo do estoque (desconhecido
//    nunca vira zero), o aviso de item sem estoque, o telefone, o prazo para
//    contar como recuperado, a taxa do site, o aviso da leitura (a rota ainda
//    não publicada), quem pode marcar como resolvido e a URL só https;
//  - o cartão não tem envio: o e-mail e o telefone são texto (sem mailto/wa.me).
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')

const transpile = (source) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText

function sfc(rel) {
  const filename = path.resolve(__dirname, rel)
  const fonte = fs.readFileSync(filename, 'utf8')
  const { descriptor, errors } = parse(fonte, { filename })
  assert.deepEqual(errors, [], rel)
  const compiled = compileTemplate({ source: descriptor.template.content, filename, id: path.basename(rel), compilerOptions: { isTS: true } })
  assert.deepEqual(compiled.errors, [], `${rel}: template compila`)
  compileScript(descriptor, { id: path.basename(rel) })
  return { descriptor, fonte }
}
function exportsDe(descriptor) {
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(descriptor.script.content))((n) => require(n), mod, mod.exports)
  return mod.exports
}
const api = (rel) => fs.readFileSync(path.resolve(__dirname, '../../api/app', rel), 'utf8')

const carr = sfc('../components/AtendimentoCarrinho.vue')
const C = exportsDe(carr.descriptor)
const conversa = sfc('../components/AtendimentoConversa.vue')

// ------------------------------------------------ o contrato com o backend
const constantes = api('services/atendimento/constantes.py')
const servico = api('services/atendimento/carrinhos.py')
const rotas = api('routers/atendimento_carrinhos.py')

assert.match(rotas, /@router\.get\("\/conversas\/\{conversa_id\}\/carrinho"/)
assert.match(rotas, /@router\.post\("\/carrinhos\/\{carrinho_id\}\/resolvido"/)
assert.ok(carr.fonte.includes('/api/atendimento/conversas/${encodeURIComponent(id)}/carrinho'), 'o cartão lê a rota do backend')
assert.ok(carr.fonte.includes('/api/atendimento/carrinhos/${encodeURIComponent(atual.id)}/resolvido'), 'e marca pela rota do backend')
assert.equal(Number((constantes.match(/^CARRINHO_DIAS_RECUPERACAO = (\d+)$/m) || [])[1]), C.DIAS_RECUPERACAO, 'os 7 dias são os do backend')
const aviso = (servico.match(/AVISO_SEM_ENVIO = \(\s*"([^"]+)"/) || [])[1]
assert.equal(aviso, C.AVISO_SEM_ENVIO, 'o aviso de rede é o mesmo do backend')

// Cada campo do CarrinhoOut do backend existe no tipo da tela.
function campos(classe) {
  const ini = rotas.indexOf(`class ${classe}(BaseModel):`)
  assert.ok(ini >= 0, classe)
  const resto = rotas.slice(ini).split('\n').slice(1)
  const out = []
  for (const linha of resto) {
    if (/^\S/.test(linha)) break
    const m = /^ {4}([a-z_]+): /.exec(linha)
    if (m) out.push(m[1])
  }
  return out
}
const tipoDe = (nome) => {
  const ini = carr.fonte.indexOf(`export type ${nome} = {`)
  assert.ok(ini >= 0, nome)
  return carr.fonte.slice(ini, carr.fonte.indexOf('\n}', ini))
}
for (const [classe, tipo] of [['CarrinhoOut', 'Carrinho'], ['LojistaOut', 'LojistaCarrinho'], ['ItemCarrinhoOut', 'ItemCarrinho'], ['EstoqueItemOut', 'EstoqueItemCarrinho'], ['CarrinhoResumoOut', 'CarrinhoResumo'], ['TaxaOut', 'TaxaCarrinho'], ['CarrinhoDaConversaOut', 'CarrinhoDaConversa']]) {
  const t = tipoDe(tipo)
  for (const campo of campos(classe)) assert.ok(new RegExp(`\\b${campo}\\??:`).test(t), `${tipo}.${campo} (do ${classe})`)
}
// Os status do estoque que o backend devolve têm selo.
for (const s of ['ok', 'acima', 'zero', 'desconhecido', 'sem_mapa']) {
  assert.ok(servico.includes(`"${s}"`), `backend devolve ${s}`)
  assert.ok(C.ESTOQUE_CLS[s], `selo de ${s}`)
}
// As situações do episódio têm selo.
for (const s of ['aberto', 'recuperado', 'nao_recuperado', 'resolvido']) assert.ok(C.SITUACAO_CARRINHO[s], s)

// O cartão é o da conversa `carrinho`, no topo (o integrador monta; o cartão avisa `mudou`).
assert.match(conversa.fonte, /<AtendimentoCarrinho\s+v-if="canalExterno === 'carrinho'"/)

// Sem envio pelo cartão: nada de mailto:, wa.me, tel: nem POST de mensagem.
for (const proibido of ['mailto:', 'wa.me', 'api.whatsapp', 'tel:', '/responder', '/mensagens']) {
  assert.ok(!carr.fonte.includes(proibido), `o cartão não manda nada (${proibido})`)
}

// ------------------------------------------------ os ajudantes
assert.equal(C.situacaoCarrinho('aberto').rotulo, 'Carrinho parado')
assert.equal(C.situacaoCarrinho('nao_recuperado').rotulo, 'Não recuperado')
assert.equal(C.situacaoCarrinho('x').rotulo, 'x')

const est = (o) => ({ disponivel: null, demanda: 1, status: 'desconhecido', texto: '', outros_lotes: 0, falhou: false, skus: [], ...o })
assert.equal(C.seloEstoque(est({ status: 'ok', disponivel: 5, texto: '5 em estoque' })).texto, '5 em estoque')
assert.equal(C.seloEstoque(est({ status: 'acima', disponivel: 2, demanda: 7 })).texto, '2 disp.')
assert.match(C.seloEstoque(est({ status: 'acima', disponivel: 2, demanda: 7 })).titulo, /pede 7/)
assert.equal(C.seloEstoque(est({ status: 'zero', disponivel: 0 })).texto, 'Sem estoque')
// Desconhecido NUNCA aparece como zero.
assert.equal(C.seloEstoque(est({ status: 'desconhecido' })).texto, '?')
assert.equal(C.seloEstoque(est({ status: 'desconhecido', falhou: true })).texto, 'não lido')
assert.equal(C.seloEstoque(null).texto, '—')
assert.match(C.seloEstoque(est({ status: 'ok', disponivel: 2, skus: [{ sku: 'b2002.ci', existe: true, saldo: 2 }, { sku: 'x', existe: false, saldo: null }] })).titulo, /b2002\.ci: 2 · x: fora do catálogo/)
assert.ok(C.semEstoque({ status: 'zero' }) && C.semEstoque({ status: 'acima' }))
assert.ok(!C.semEstoque({ status: 'desconhecido' }) && !C.semEstoque(null))
assert.equal(C.avisoEstoque({ itens_sem_estoque: 0, situacao: 'aberto' }), '')
assert.equal(C.avisoEstoque({ itens_sem_estoque: 1, situacao: 'aberto' }), '1 item sem estoque suficiente no DaVinci')
assert.equal(C.avisoEstoque({ itens_sem_estoque: 3, situacao: 'aberto' }), '3 itens sem estoque suficiente no DaVinci')
assert.equal(C.avisoEstoque({ itens_sem_estoque: 3, situacao: 'recuperado' }), '', 'só no carrinho aberto')

assert.equal(C.fmtTelefone('11999998888'), '(11) 99999-8888')
assert.equal(C.fmtTelefone('+55 (11) 3333-4444'), '(11) 3333-4444')
assert.equal(C.fmtTelefone('5511999998888'), '(11) 99999-8888')
assert.equal(C.fmtTelefone('123'), '123')
assert.equal(C.fmtTelefone(null), '')
assert.equal(C.statusLojista('em_analise'), 'em análise')
assert.equal(C.cidadeUf({ cidade: 'São Paulo', estado: 'SP' }), 'São Paulo/SP')
assert.equal(C.cidadeUf({ cidade: null, estado: 'SP' }), 'SP')

const T = Date.parse('2026-10-02T12:00:00Z')
const prazoEm = '2026-10-09T12:00:00Z'
assert.match(C.prazoRecuperacao({ situacao: 'aberto', prazo_em: prazoEm }, T).texto, /em até 7 d$/)
assert.match(C.prazoRecuperacao({ situacao: 'aberto', prazo_em: prazoEm }, Date.parse('2026-10-09T02:00:00Z')).cls, /amber/)
assert.match(C.prazoRecuperacao({ situacao: 'aberto', prazo_em: prazoEm }, Date.parse('2026-10-10T00:00:00Z')).texto, /não recuperado/)
assert.equal(C.prazoRecuperacao({ situacao: 'recuperado', prazo_em: prazoEm }, T), null)

assert.equal(C.taxaTexto(null), '')
assert.equal(C.taxaTexto({ dias: 30, detectados: 0, abertos: 0, recuperados: 0, nao_recuperados: 0, resolvidos: 0, encerrados: 0, taxa: null }), '')
assert.equal(
  C.taxaTexto({ dias: 30, detectados: 2, abertos: 2, recuperados: 0, nao_recuperados: 0, resolvidos: 0, encerrados: 0, taxa: null }, 'Charlots'),
  'Charlots · últimos 30 dias: 2 carrinhos parados, nenhum encerrado ainda',
)
assert.equal(
  C.taxaTexto({ dias: 30, detectados: 12, abertos: 2, recuperados: 3, nao_recuperados: 6, resolvidos: 1, encerrados: 10, taxa: 0.3 }, 'Charlots'),
  'Charlots · últimos 30 dias: 3 de 10 recuperados (30%) · 2 abertos',
)

assert.equal(C.avisoLeitura(null), '')
assert.equal(C.avisoLeitura({ status: 'ok', ultimo_erro: 'velho' }), '')
assert.equal(C.avisoLeitura({ status: 'novo', ultimo_erro: null }), '')
assert.equal(
  C.avisoLeitura({ status: 'sem_endpoint', ultimo_erro: 'HTTP 404: a rota /api/davinci/carrinhos ainda não está publicada' }),
  'O site ainda não tem a rota de carrinhos publicada (falta publicar o pacote do carrinho na Hostinger). O que aparece aqui é da última leitura que deu certo.',
)
assert.match(C.avisoLeitura({ status: 'sem_escopo', ultimo_erro: 'HTTP 401 nao_autorizado' }), /recusou o token/)
assert.match(C.avisoLeitura({ status: 'estranho', ultimo_erro: '' }), /não está em dia/)

assert.ok(C.podeResolver({ pode_resolver: true, situacao: 'aberto' }, true))
assert.ok(!C.podeResolver({ pode_resolver: true, situacao: 'aberto' }, false), 'sem permissão de editar')
assert.ok(!C.podeResolver({ pode_resolver: false, situacao: 'resolvido' }, true))
assert.ok(!C.podeResolver(null, true))
assert.match(C.perguntaResolver({ quantidade_total: 3, site_nome: 'Charlots' }), /3 peças no site Charlots[\s\S]*Nada é mandado ao lojista/)

assert.equal(C.urlDoSite('https://charlots.com.br/uploads/a.webp'), 'https://charlots.com.br/uploads/a.webp')
assert.equal(C.urlDoSite('http://localhost:8080/foto.webp'), 'http://localhost:8080/foto.webp')
for (const torta of ['javascript:alert(1)', 'http://evil.com/x.png', 'data:image/png;base64,AAAA', 'https://x.com/a b', null, 7]) {
  assert.equal(C.urlDoSite(torta), null, String(torta))
}
assert.equal(C.tempo(45), '45 min')
assert.equal(C.tempo(60 * 24 * 2 + 60 * 4), '2 d 4 h')

console.log('atendimento-carrinho ok')
