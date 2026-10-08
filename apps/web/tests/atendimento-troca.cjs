// node tests/atendimento-troca.cjs — a TROCA DE PRODUTO na caixa /atendimento
// (item 4, fases 4c e 4d, 08/10/2026): o diálogo AtendimentoTroca e o
// "enviar oferta" das sugestões. Aqui, a tela:
//  - o contrato com o backend, nos dois sentidos: os tipos do diálogo são os
//    schemas de schemas/atendimento_troca.py (PreviaTrocaIn/Out, AceiteIn,
//    TrocaIn, TrocaOut, as travas, os passos) e o TrocaAbertaOut do painel; a
//    oferta (OfertaEnvio, o corpo e a resposta) é a do router; as URLs são as
//    rotas do router (com o método e a permissão: escrever — a troca E a
//    oferta, decisão (g) do dono — pede atendimento.edit e margem.edit); todo
//    estado (`troca.ESTADOS`) e todo passo que o serviço grava têm texto;
//  - os ajudantes puros: a chave por abertura, quem pode (`acessoDaTroca`),
//    por que o botão não anda (`motivoSemClique`: a caixinha OBRIGATÓRIA nos
//    níveis 1 e 2, a prova opcional), o corpo do clique, o Duoke, os erros;
//  - o diálogo renderizado (Vue SSR) com uma API falsa: a prévia, a caixinha,
//    a prova (mensagem do cliente ou Duoke), "Trocar lote" no nível 0 sem
//    aceite, o clique com a `idem_key` (a MESMA se a rede caiu, OUTRA depois
//    de uma recusa), o resultado com os passos e o Retomar nos estados
//    abertos, o custo só quando vem (quem não vê a Margem não recebe);
//  - o diálogo só fala com as rotas da troca (prévia, troca, retomar, a lista
//    das trocas) — nunca com a de responder a conversa.
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
  const compiled = compileTemplate({ source: descriptor.template.content, filename, id: path.basename(rel), compilerOptions: { isTS: true } })
  assert.deepEqual(compiled.errors, [], `${rel}: template compila`)
  compileScript(descriptor, { id: path.basename(rel) })
  return { descriptor, fonte, render: compiled.code }
}
// O <script> comum (não o setup) é o módulo dos ajudantes exportados.
function exportsDe(descriptor) {
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(descriptor.script.content))((n) => require(n), mod, mod.exports)
  return mod.exports
}
function renderDe(sfcx) {
  const compilado = {}
  new Function('exports', 'require', transpile(sfcx.render))(compilado, require)
  return compilado.render
}
// O setup sem os imports, com o que ele usa por parâmetro.
function setupDe(sfcx, params, retorno) {
  const src = sfcx.descriptor.scriptSetup.content.replace(/^import[\s\S]*?from\s+'[^']+'\s*$/gm, '')
  const nomes = Object.keys(params)
  return new Function(...nomes, transpile(src + `\nreturn { ${retorno.join(', ')} }`))(...nomes.map((n) => params[n]))
}
const api = (rel) => fs.readFileSync(path.resolve(__dirname, '../../api/app', rel), 'utf8')
const icone = (nome) => ({ name: nome, render: () => Vue.h('i', { 'data-icone': nome }) })
const esperar = () => new Promise((r) => setTimeout(r, 0))
const semComentarios = (h) => h.replace(/<!--[\s\S]*?-->/g, '')

const dialogo = sfc('../components/AtendimentoTroca.vue')
const TR = exportsDe(dialogo.descriptor)
const sugestoesSfc = sfc('../components/AtendimentoTrocaSugestoes.vue')
const SG = exportsDe(sugestoesSfc.descriptor)
const P = exportsDe(sfc('../components/AtendimentoPlataforma.vue').descriptor)
const pedido = sfc('../components/AtendimentoPedido.vue')

// ------------------------------------------------ o contrato com o backend
const schemasTroca = api('schemas/atendimento_troca.py')
const schemasPainel = api('schemas/atendimento_painel.py')
const roteador = api('routers/atendimento_troca.py')
const servico = api('services/atendimento/troca.py')

// Os campos de um schema pydantic, com os herdados (TrocaIn herda PreviaTrocaIn).
function campos(classe, fonte) {
  const m = fonte.match(new RegExp(`^class ${classe}\\((\\w+)\\):\\n([\\s\\S]*?)(?=\\n\\n\\n|\\n(?:class|def|async def|@) |$(?![\\s\\S]))`, 'm'))
  assert.ok(m, classe)
  const proprios = [...m[2].matchAll(/^ {4}([a-z_]+): /gm)].map((x) => x[1])
  return m[1] === 'BaseModel' ? proprios : [...campos(m[1], fonte), ...proprios]
}
const tipo = (nome, fonte) => {
  const m = fonte.match(new RegExp(`export type ${nome} = \\{\\n([\\s\\S]*?)\\n\\}`))
  assert.ok(m, nome)
  return [...m[1].matchAll(/^ {2}([a-z_]+)\??: /gm)].map((x) => x[1])
}
const igual = (a, b, msg) => assert.deepEqual(a.slice().sort(), b.slice().sort(), msg)
{
  const pares = [
    ['TravaTroca', 'TravaTrocaOut'],
    ['ProdutoTroca', 'ProdutoTrocaOut'],
    ['AceitePossivel', 'AceitePossivelOut'],
    ['PreviaTroca', 'PreviaTrocaOut'],
    ['PassoTroca', 'PassoTrocaOut'],
    ['Troca', 'TrocaOut'],
    ['PreviaTrocaIn', 'PreviaTrocaIn'],
    ['AceiteIn', 'AceiteIn'],
    ['TrocaIn', 'TrocaIn'],
  ]
  for (const [ts_, py] of pares) igual(tipo(ts_, dialogo.fonte), campos(py, schemasTroca), `${ts_} = ${py}`)
  igual(tipo('TrocaAberta', dialogo.fonte), campos('TrocaAbertaOut', schemasPainel), 'TrocaAberta = TrocaAbertaOut')
  assert.match(schemasTroca, /^class TrocaIn\(PreviaTrocaIn\):$/m, 'o clique leva o que a prévia leva')
  // A prova do aceite: as mesmas duas fontes; a chave e o hash obrigatórios.
  assert.match(schemasTroca, /^ {4}fonte: Literal\["davinci", "duoke"\] \| None = None$/m)
  assert.match(dialogo.fonte, /^ {2}fonte\?: 'davinci' \| 'duoke' \| null$/m)
  assert.match(schemasTroca, /^ {4}idem_key: UUID$/m)
  assert.match(schemasTroca, /^ {4}previa_hash: str = Field\(min_length=8, max_length=64\)$/m)
  assert.match(schemasTroca, /^ {4}confirmar: bool = False$/m)
  // O texto colado do Duoke: o mesmo teto do serviço e do schema.
  assert.equal(TR.DUOKE_MAX, Number((servico.match(/^ACEITE_TEXTO_MAX = (\d+)$/m) || [])[1]))
  assert.match(schemasTroca, new RegExp(`^ {4}texto: str \\| None = Field\\(default=None, max_length=${TR.DUOKE_MAX}\\)$`, 'm'))
  // O painel e a lista trazem o resumo da troca aberta (sem custo).
  assert.doesNotMatch(campos('TrocaAbertaOut', schemasPainel).join(' '), /custo/)
  assert.match(pedido.fonte, /^ {2}troca_aberta: TrocaAberta \| null$/m)

  // Todo estado do serviço tem frase na tela (e a tela não inventa estado).
  const constantes = Object.fromEntries([...servico.matchAll(/^([A-Z_]+) = "([a-z_]+)"$/gm)].map((m) => [m[1], m[2]]))
  const estados = [...((servico.match(/^ESTADOS = \(([\s\S]*?)\)$/m) || [])[1] || '').matchAll(/([A-Z_]+),/g)].map((m) => constantes[m[1]])
  assert.equal(estados.length, 8, 'os 8 estados')
  igual(Object.keys(TR.ESTADO_TROCA), estados, 'ESTADO_TROCA = troca.ESTADOS')
  const fechados = [...((servico.match(/^FECHADOS = frozenset\(\{([^}]*)\}\)$/m) || [])[1] || '').matchAll(/([A-Z_]+)/g)].map((m) => constantes[m[1]])
  igual(fechados, ['concluida', 'abortada'])
  assert.equal(TR.tomEstado('concluida'), 'ok')
  assert.equal(TR.tomEstado('abortada'), 'erro')
  for (const e of estados.filter((x) => !fechados.includes(x))) assert.equal(TR.tomEstado(e), 'parada', `${e}: parada no meio`)
  // Todo passo que o serviço grava (`_passo`, `_parar`, `_erro_interno`) tem rótulo.
  const passos = new Set([
    ...[...servico.matchAll(/_passo\(\s*troca,\s*"([a-z_0-9]+)"/g)].map((m) => m[1]),
    ...[...servico.matchAll(/_parar\(\s*session,\s*troca,\s*"([a-z_0-9]+)"/g)].map((m) => m[1]),
    ...[...servico.matchAll(/_erro_interno\(session, troca\.id, "([a-z_0-9]+)"/g)].map((m) => m[1]),
  ])
  assert.ok(passos.size >= 10, `os passos do serviço (${[...passos]})`)
  for (const p of passos) assert.ok(TR.ROTULO_PASSO[p], `o passo ${p} tem rótulo na tela`)
  assert.equal(TR.rotuloPasso('passo_do_futuro'), 'passo_do_futuro', 'passo novo: o código')
  assert.equal(TR.rotuloEstado('estado_novo'), 'estado_novo')

  // As URLs = as rotas do router (prefixo + caminho), com o método.
  const prefixo = (roteador.match(/prefix="([^"]+)"/) || [])[1]
  assert.equal(prefixo, '/api/atendimento')
  const rota = (metodo, caminho) => {
    const re = new RegExp(`@router\\.${metodo}\\(\\s*"${caminho.replace(/[{}/]/g, '\\$&')}",[\\s\\S]*?\\)\\nasync def \\w+\\(([\\s\\S]*?)\\) ->`)
    const m = roteador.match(re)
    assert.ok(m, `${metodo.toUpperCase()} ${caminho} no router`)
    return { decorador: m[0].slice(0, m[0].indexOf('\nasync def')), params: m[1] }
  }
  const previa = rota('post', '/pedidos/{numero_bling}/troca/previa')
  const clique = rota('post', '/pedidos/{numero_bling}/troca')
  const retomar = rota('post', '/trocas/{troca_id}/retomar')
  const lista = rota('get', '/trocas')
  assert.equal(TR.urlPrevia('300101'), `${prefixo}/pedidos/300101/troca/previa`)
  assert.equal(TR.urlTroca('300101'), `${prefixo}/pedidos/300101/troca`)
  assert.equal(TR.urlRetomar('t-1'), `${prefixo}/trocas/t-1/retomar`)
  assert.equal(TR.URL_TROCAS, `${prefixo}/trocas`)
  assert.equal(TR.urlTroca('a/b'), `${prefixo}/pedidos/a%2Fb/troca`, 'o número vai codificado')
  // Escrever na troca: a trava da caixa + atendimento.edit + margem.edit (a troca
  // aprova a Margem); ler a lista: quem vê a caixa.
  assert.match(roteador, /dependencies=\[Depends\(_so_admin\)\]/, 'a trava da caixa em todo o router')
  assert.match(roteador, /^_margem_edit = require_permission\("margem", "edit"\)$/m)
  for (const r of [previa, clique, retomar]) {
    assert.match(r.decorador, /dependencies=\[Depends\(_margem_edit\)\]/, 'margem.edit')
    assert.match(r.params, /Depends\(_edit\)/, 'atendimento.edit')
  }
  assert.match(lista.params, /Depends\(_view\)/)
  assert.match(clique.params, /body: TrocaIn/)
  assert.match(previa.params, /body: PreviaTrocaIn/)

  // A oferta (fase 4d): o contrato — POST /pedidos/{n}/troca/oferta, corpo e
  // resposta, e o `oferta_envio` do bloco Ag. cancelamento — nos dois
  // sentidos. Escrever a oferta é PROMETER a troca ao comprador: a trava da
  // caixa + atendimento.edit + margem.edit, como a troca (decisão (g)).
  const oferta = rota('post', '/pedidos/{numero_bling}/troca/oferta')
  assert.match(oferta.decorador, /dependencies=\[Depends\(_margem_edit\)\]/, 'a oferta pede margem.edit')
  assert.match(oferta.params, /Depends\(_edit\)/)
  const saida = (oferta.decorador.match(/response_model=(\w+)/) || [])[1]
  const entrada = (oferta.params.match(/body: (\w+)/) || [])[1]
  assert.ok(saida && entrada, 'o corpo e a resposta da oferta')
  const ondeEsta = (c) => (new RegExp(`^class ${c}\\(`, 'm').test(schemasTroca) ? schemasTroca : schemasPainel)
  igual(tipo('OfertaTrocaIn', sugestoesSfc.fonte), campos(entrada, ondeEsta(entrada)), `OfertaTrocaIn = ${entrada}`)
  igual(tipo('OfertaTrocaOut', sugestoesSfc.fonte), campos(saida, ondeEsta(saida)), `OfertaTrocaOut = ${saida}`)
  const envio = (schemasPainel.match(/^ {4}oferta_envio: (\w+)\b/m) || [])[1]
  assert.ok(envio, 'AgCancelamentoOut.oferta_envio')
  igual(tipo('OfertaEnvio', sugestoesSfc.fonte), campos(envio, schemasPainel), `OfertaEnvio = ${envio}`)
  // O "Trocar" (fase 4c): o `troca_envio` do bloco, o mesmo desenho.
  const envioTroca = (schemasPainel.match(/^ {4}troca_envio: (\w+)\b/m) || [])[1]
  assert.ok(envioTroca, 'AgCancelamentoOut.troca_envio')
  igual(tipo('TrocaEnvio', sugestoesSfc.fonte), campos(envioTroca, schemasPainel), `TrocaEnvio = ${envioTroca}`)
  assert.match(pedido.fonte, /^ {2}oferta_envio\?: OfertaEnvio \| null$/m)
  assert.match(pedido.fonte, /^ {2}troca_envio\?: TrocaEnvio \| null$/m)
  // A troca aberta FORA de 83955 (parada no meio com o pedido em 9) vem no painel.
  assert.ok(campos('PainelOut', schemasPainel).includes('troca_aberta'), 'PainelOut.troca_aberta')
  assert.match(pedido.fonte, /^ {2}troca_aberta\?: TrocaAberta \| null$/m)
}

// ------------------------------------------------ os ajudantes
{
  // Uma chave v4 por abertura — e fora de HTTPS (sem randomUUID), também.
  const v4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/
  const a = TR.novaIdemKey()
  assert.match(a, v4)
  assert.notEqual(a, TR.novaIdemKey())
  const original = globalThis.crypto
  Object.defineProperty(globalThis, 'crypto', { value: { getRandomValues: (b) => original.getRandomValues(b) }, configurable: true })
  try {
    assert.match(TR.novaIdemKey(), v4, 'sem randomUUID')
  } finally {
    Object.defineProperty(globalThis, 'crypto', { value: original, configurable: true })
  }

  // Quem pode: trocar E ofertar = mexe + atendimento.edit + margem.edit (decisão (g)).
  const acesso = (mexe, editaAtendimento, editaMargem) => TR.acessoDaTroca({ mexe, editaAtendimento, editaMargem })
  assert.deepEqual(acesso(true, true, true), { trocar: true, ofertar: true })
  assert.deepEqual(acesso(true, true, false), { trocar: false, ofertar: false }, 'sem margem.edit não promete a troca')
  assert.deepEqual(acesso(true, false, true), { trocar: false, ofertar: false })
  assert.deepEqual(acesso(false, true, true), { trocar: false, ofertar: false }, 'quem só lê (fase de observação)')
  // O composable usa o mesmo critério da página (o /me e as permissões).
  const fonteAcesso = dialogo.descriptor.script.content.slice(dialogo.descriptor.script.content.indexOf('export function useAcessoDaTroca'))
  assert.match(fonteAcesso, /auth\.user\?\.atendimento_mexe === true/)
  assert.match(fonteAcesso, /useCan\('atendimento', 'edit'\)/)
  assert.match(fonteAcesso, /useCan\('margem', 'edit'\)/)
  assert.match(fs.readFileSync(path.resolve(__dirname, '../pages/atendimento.vue'), 'utf8'), /const mexe = computed\(\(\) => auth\.user\?\.atendimento_mexe === true\)/)

  assert.equal(TR.rotuloBotao(0), 'Trocar lote e voltar para Em aberto')
  assert.equal(TR.rotuloBotao(1), 'Trocar e voltar para Em aberto')
  assert.equal(TR.rotuloBotao(null), 'Trocar e voltar para Em aberto')
  assert.equal(TR.tituloDialogo(0, '300101'), 'Trocar lote do pedido 300101')
  assert.equal(TR.tituloDialogo(2, '300101'), 'Trocar produto do pedido 300101')

  assert.equal(TR.dataHora('2026-10-08T13:05:00Z'), '08/10 10:05', 'Brasília')
  assert.equal(TR.dataHora(null), '')
  assert.equal(TR.dataHora('x'), '')
  assert.ok(TR.moeda(1234.5).includes('1.234,50') && TR.moeda(1234.5).startsWith('R$'))
  assert.equal(TR.moeda(null), '')
  const agora = Date.parse('2026-10-08T13:00:00Z')
  assert.equal(TR.agoraLocal(agora), '2026-10-08T10:00')
  assert.equal(TR.instanteLocal('2026-10-08T10:00'), agora, 'sem fuso = Brasília (−03:00)')
  assert.equal(TR.instanteLocal('2026-10-08T10:00:30'), agora + 30_000)
  assert.equal(TR.instanteLocal('ontem'), null)

  // O Duoke: o que o backend recusaria, dito antes.
  const D = (t, em) => TR.problemaDuoke(t, em, agora, 7)
  assert.equal(D('', '2026-10-08T09:00'), 'Cole a resposta do cliente no Duoke.')
  assert.equal(D('x'.repeat(TR.DUOKE_MAX + 1), '2026-10-08T09:00'), `A resposta colada passa de ${TR.DUOKE_MAX} caracteres.`)
  assert.equal(D('pode mandar', ''), 'Informe a data e a hora da resposta no Duoke.')
  assert.equal(D('pode mandar', '08/10 09:00'), 'A data e a hora da resposta não estão certas.')
  assert.equal(D('pode mandar', '2026-10-08T10:30'), 'A data da resposta no Duoke está no futuro.')
  assert.equal(D('pode mandar', '2026-10-08T10:04'), null, 'a folga do relógio')
  assert.equal(D('pode mandar', '2026-09-30T09:00'), 'A resposta tem mais de 7 dias: não vale como prova.')
  assert.equal(D('pode mandar', '2026-10-07T18:00'), null)

  // O botão do clique: a prévia, a recusa, as travas, a CAIXINHA e a prova.
  const previa = (extra = {}) => ({ pode: true, travas: [], previa_hash: 'abcdef012345', exige_aceite: true, aceites_possiveis: [{ id: 'm-1' }], aceite_max_dias: 7, ...extra })
  const E = (extra = {}) => ({ previa: previa(), carregando: false, precisaNovaPrevia: false, confirmar: false, prova: 'nenhuma', mensagemId: null, duokeTexto: '', duokeEm: '', agora, ...extra })
  assert.equal(TR.motivoSemClique(E({ carregando: true })), 'Conferindo a troca no Bling…')
  assert.equal(TR.motivoSemClique(E({ previa: null })), 'Sem a prévia da troca: confira de novo.')
  assert.equal(TR.motivoSemClique(E({ precisaNovaPrevia: true, confirmar: true })), 'Confira a prévia de novo antes de trocar.')
  assert.equal(TR.motivoSemClique(E({ previa: previa({ pode: false, travas: [{ code: 'rastreio', ok: true, texto: 'ok' }, { code: 'nf_emitida', ok: false, texto: 'O pedido já tem NF.' }] }) })), 'O pedido já tem NF.', 'a 1ª trava que falhou')
  assert.equal(TR.motivoSemClique(E({ previa: previa({ pode: false }) })), 'A troca não pode ser feita agora.')
  assert.equal(TR.motivoSemClique(E({ previa: previa({ previa_hash: null }), confirmar: true })), 'Sem a conferência ao vivo: confira de novo.')
  assert.equal(TR.motivoSemClique(E()), 'Marque que o cliente aceitou a troca.', 'níveis 1 e 2: a caixinha é obrigatória')
  assert.equal(TR.motivoSemClique(E({ confirmar: true })), null, 'a prova é opcional')
  assert.equal(TR.motivoSemClique(E({ confirmar: true, prova: 'mensagem' })), 'Escolha a mensagem do cliente (ou deixe sem prova).')
  assert.equal(TR.motivoSemClique(E({ confirmar: true, prova: 'mensagem', mensagemId: 'outra' })), 'Escolha a mensagem do cliente (ou deixe sem prova).', 'só as da prévia')
  assert.equal(TR.motivoSemClique(E({ confirmar: true, prova: 'mensagem', mensagemId: 'm-1' })), null)
  assert.equal(TR.motivoSemClique(E({ confirmar: true, prova: 'duoke' })), 'Cole a resposta do cliente no Duoke.')
  assert.equal(TR.motivoSemClique(E({ confirmar: true, prova: 'duoke', duokeTexto: 'pode', duokeEm: '2026-10-08T09:00' })), null)
  assert.equal(TR.motivoSemClique(E({ previa: previa({ exige_aceite: false }) })), null, 'nível 0: sem caixinha')

  // A prova no corpo, e o corpo do clique.
  assert.equal(TR.aceiteDe('nenhuma', 'm-1', 'x', 'y'), null, 'sem prova = declarado')
  assert.deepEqual(TR.aceiteDe('mensagem', 'm-1', '', ''), { mensagem_aceite_id: 'm-1', fonte: 'davinci' })
  assert.equal(TR.aceiteDe('mensagem', null, '', ''), null)
  assert.deepEqual(TR.aceiteDe('duoke', null, '  pode mandar  ', '2026-10-08T09:00 '), { fonte: 'duoke', texto: 'pode mandar', em: '2026-10-08T09:00' })
  const escolha = { sku_antigo: 'dg057.ci', sku_novo: 'dg056.ci', nome: null, nivel: 1, mesmo_produto: false }
  const corpo = (p, extra = {}) => TR.corpoDaTroca({ escolha, previa: { conversa_id: 'conv-p', previa_hash: 'abcdef012345', ...p }, conversaId: 'conv-x', idemKey: 'k-1', confirmar: true, prova: 'mensagem', mensagemId: 'm-1', duokeTexto: '', duokeEm: '', ...extra })
  assert.deepEqual(corpo({ exige_aceite: true }), {
    sku_antigo: 'dg057.ci', sku_novo: 'dg056.ci', conversa_id: 'conv-p',
    aceite: { mensagem_aceite_id: 'm-1', fonte: 'davinci' }, previa_hash: 'abcdef012345', idem_key: 'k-1', confirmar: true,
  })
  assert.equal(corpo({ exige_aceite: true, conversa_id: null }).conversa_id, 'conv-x', 'sem a da prévia, a de onde abriu')
  assert.deepEqual(
    [corpo({ exige_aceite: false }).aceite, corpo({ exige_aceite: false }).confirmar],
    [null, false],
    'nível 0: sem aceite e sem caixinha',
  )

  // O aceite registrado e quem conduz.
  assert.equal(TR.textoAceite({ aceite_fonte: 'davinci', aceite_em: '2026-10-08T12:30:00Z', criado_por_nome: 'T', nivel: 1 }), 'Aceite: mensagem do cliente no DaVinci (08/10 09:30)')
  assert.equal(TR.textoAceite({ aceite_fonte: 'duoke', aceite_em: null, criado_por_nome: 'T', nivel: 1 }), 'Aceite: resposta do cliente no Duoke')
  assert.equal(TR.textoAceite({ aceite_fonte: 'declarado', aceite_em: null, criado_por_nome: 'Thorfinn', nivel: 2 }), 'Aceite declarado por Thorfinn')
  assert.equal(TR.textoAceite({ aceite_fonte: null, aceite_em: null, criado_por_nome: 'x', nivel: 0 }), 'Mesmo produto, outro lote: sem aceite.')
  assert.equal(TR.quemTroca({ automatica: true, criado_por_nome: 'Robô', created_at: null }), 'robô de lote')
  assert.equal(TR.quemTroca({ automatica: false, criado_por_nome: null, created_at: '2026-10-08T13:05:00Z' }), 'alguém da equipe, 08/10 10:05')

  // Os erros: o código, a troca que a recusa abortou, e se o backend respondeu.
  const e409 = { status: 409, data: { detail: { code: 'situacao_mudou', detail: 'O pedido já não está em Aguardando Cancelamento no Bling.', troca_id: 't-7' } } }
  assert.deepEqual(TR.erroDaTroca(e409, P.erroDaApi(e409, 'x'), P.statusDoErro(e409)), {
    code: 'situacao_mudou', texto: 'O pedido já não está em Aguardando Cancelamento no Bling.', motivos: [], trocaId: 't-7', definitiva: true,
  })
  const e404 = { status: 404, data: { detail: { code: 'pedido_nao_encontrado' } } }
  assert.equal(TR.erroDaTroca(e404, P.erroDaApi(e404, 'x'), 404).texto, TR.TEXTO_SEM_DETALHE.pedido_nao_encontrado, 'sem frase do backend')
  const rede = new Error('[POST] "/api/…": <no response> Failed to fetch')
  const er = TR.erroDaTroca(rede, P.erroDaApi(rede, 'Não consegui fazer a troca agora'), P.statusDoErro(rede))
  assert.equal(er.definitiva, false, 'sem resposta: talvez tenha feito')
  assert.equal(er.texto, 'Não consegui fazer a troca agora')
  assert.equal(TR.erroDaTroca({ status: 503 }, { texto: 'x', motivos: [] }, 503).definitiva, false)
}

// ------------------------------------------------ o diálogo renderizado (Vue SSR)
const ESCOLHA = { sku_antigo: 'dg057.ci', sku_novo: 'dg056.ci', nome: 'A17 - Laranja', nivel: 1, mesmo_produto: false }
const PREVIA = {
  pode: true,
  travas: [
    { code: 'motivo_nao_permite', ok: true, texto: 'Só o pedido em Aguardando Cancelamento por falta de estoque pode trocar de produto.' },
    { code: 'saldo_insuficiente', ok: true, texto: 'O produto novo não tem saldo no Bling para a quantidade do pedido.' },
  ],
  ao_vivo: true,
  numero_bling: '300101',
  bling_id: 9001,
  numeroloja: 'SHP1',
  plataforma: 'shopee',
  conversa_id: 'conv-1',
  nivel: 1,
  mesmo_produto: false,
  exige_aceite: true,
  antes: { sku: 'dg057.ci', nome: 'A17 - Branco <b>', quantidade: 1, produto_id: 11, custo: 512.3, saldo_ao_vivo: null },
  depois: { sku: 'dg056.ci', nome: 'A17 - Laranja', quantidade: 1, produto_id: 12, custo: 520.1, saldo_ao_vivo: 14 },
  valor_unitario: 1234.5,
  observacao: '08/10 - TROCA dg057.ci -> dg056.ci (aceite declarado) - Atendimento/Thorfinn',
  passos_previstos: ['PUT itens + observação no Bling', '83955 → 9 → 6', 'Margem Aprovado por Thorfinn', 'NF volta à fila'],
  aviso_nf: 'A loja está fora do envio automático da NF — depois da troca, enfileire na aba NF.',
  aviso_prazo: null,
  dif_custo_pct: 1.52,
  aceites_possiveis: [
    { id: 'm-1', texto: 'pode mandar a laranja sim', enviada_em: '2026-10-08T12:30:00Z', depois_da_oferta: true },
    { id: 'm-2', texto: 'cadê meu pedido?', enviada_em: '2026-10-07T12:00:00Z', depois_da_oferta: false },
  ],
  aceite_max_dias: 7,
  previa_hash: 'abcdef0123456789',
  troca_aberta: null,
  lido_em: '2026-10-08T13:00:00Z',
}
const passo = (p, ok = true, detalhe = null) => ({ passo: p, em: '2026-10-08T13:01:00Z', ok, detalhe })
const TROCA = {
  id: 't-1', pedido_bling: '300101', bling_id: 9001, numeroloja: 'SHP1', plataforma: 'shopee', conversa_id: 'conv-1', motivo_codigo: 'sem_estoque',
  sku_antigo: 'dg057.ci', sku_novo: 'dg056.ci', produto_novo_id: 12, descricao_nova: 'A17 - Laranja', quantidade: 1, valor_unitario: 1234.5, nivel: 1, automatica: false,
  custo_antigo: null, custo_novo: null, saldo_ao_vivo: 14, aceite_fonte: 'declarado', mensagem_aceite_id: null, aceite_texto: null, aceite_em: '2026-10-08T13:01:00Z',
  estado: 'concluida', aberta: false, pode_retomar: false, codigo_erro: null, erro: null,
  passos: [passo('iniciada', true, 'por Thorfinn'), passo('conferencia'), passo('put'), passo('item_trocado'), passo('situacao_9'), passo('situacao_6'), passo('nf_liberada'), passo('concluida')],
  criado_por_nome: 'Thorfinn', created_at: '2026-10-08T13:01:00Z', concluida_em: '2026-10-08T13:02:00Z',
}
const ICONES = ['ArrowRight', 'Check', 'CheckCircle2', 'Loader2', 'RotateCcw', 'Shuffle', 'TriangleAlert', 'X', 'XCircle']
const RETORNO = ['aberto', 'previa', 'carregando', 'erroPrevia', 'precisaNovaPrevia', 'confirmar', 'prova', 'mensagemId', 'duokeTexto', 'duokeEm', 'agora', 'idemKey', 'enviando', 'retomando', 'troca', 'erroTroca', 'nivel', 'titulo', 'semClique', 'travasFalhas', 'custo', 'maxEm', 'ocupado', 'lerPrevia', 'lerTroca', 'trocar', 'retomar', 'caixa', 'fechar']
// As rotas que o diálogo pode chamar: a prévia, o clique, o retomar e a lista das trocas.
const ROTAS_DA_TROCA = /^\/api\/atendimento\/(pedidos\/[^/]+\/troca(\/previa)?|trocas(\/[^/]+\/retomar)?)$/

function montar({ escolha = ESCOLHA, trocaAberta = null, responde }) {
  const props = Vue.reactive({ numero: '300101', escolha, conversaId: 'conv-1', trocaAberta })
  const aberto = Vue.ref(true)
  const chamadas = []
  const emitidos = []
  const estado = setupDe(dialogo, {
    ...TR,
    computed: Vue.computed,
    ref: Vue.ref,
    watch: Vue.watch,
    nextTick: Vue.nextTick,
    withDefaults: (p) => p,
    defineProps: () => props,
    defineModel: () => aberto,
    defineEmits: () => (...a) => emitidos.push(a),
    useApi: () => ({
      api: async (url, opts) => {
        chamadas.push({ url, opts: opts ? structuredClone(opts) : undefined })
        return responde(url, opts, chamadas.length)
      },
    }),
    erroDaApi: P.erroDaApi,
    statusDoErro: P.statusDoErro,
    textoCusto: SG.textoCusto,
    ...Object.fromEntries(ICONES.map((n) => [n, icone(n)])),
  }, RETORNO)
  return { props, aberto, chamadas, emitidos, estado }
}
async function html(m) {
  const app = Vue.createSSRApp({ setup: () => ({ ...TR, escolha: m.props.escolha, numero: m.props.numero, ...m.estado }), render: renderDe(dialogo) })
  for (const n of ICONES) app.component(n, icone(n))
  return semComentarios(await renderToString(app))
}
const botao = (h, dado) => (h.match(new RegExp(`<button[^>]*${dado}[^>]*>`)) || [])[0] || ''
const desligado = (tag) => /\sdisabled(?=[\s>])/.test(tag)
const respostas = (mapa) => async (url, opts, n) => {
  const r = mapa(url, opts, n)
  if (r instanceof Error || (r && r.__erro)) throw r
  return structuredClone(r)
}
const erro409 = (code, detail, trocaId) => Object.assign(new Error(code), { __erro: true, status: 409, data: { detail: { code, detail, ...(trocaId ? { troca_id: trocaId } : {}) } } })
const semRede = () => Object.assign(new Error('[POST] <no response> Failed to fetch'), { __erro: true })

async function testarNivel1() {
  let clique = 0
  const m = montar({
    responde: respostas((url) => {
      if (url.endsWith('/troca/previa')) return PREVIA
      if (url.endsWith('/troca')) {
        clique += 1
        // 1º: a rede caiu; 2º: 409 de uma trava ao vivo; 3º: a troca feita.
        if (clique === 1) return semRede()
        if (clique === 2) return erro409('situacao_mudou', 'O pedido já não está em Aguardando Cancelamento no Bling.', 't-0')
        return TROCA
      }
      throw new Error(`rota inesperada ${url}`)
    }),
  })
  await esperar()
  // Abriu: a prévia (POST, só GETs no backend), com o pedido e a conversa.
  assert.deepEqual(m.chamadas, [{ url: '/api/atendimento/pedidos/300101/troca/previa', opts: { method: 'POST', body: { sku_antigo: 'dg057.ci', sku_novo: 'dg056.ci', conversa_id: 'conv-1' } } }])
  const chave1 = m.estado.idemKey.value
  let h = await html(m)
  assert.match(h, /data-dialogo-troca/)
  assert.match(h, /role="dialog"/)
  assert.ok(h.includes('Trocar produto do pedido 300101'))
  // O antes e o depois (texto, nunca HTML), o valor mantido, o custo (quem vê a Margem).
  assert.match(h, /data-antes/)
  assert.ok(h.includes('A17 - Branco &lt;b&gt;'))
  assert.ok(h.includes('saldo no Bling agora: 14'))
  assert.ok(h.includes('1.234,50') && h.includes('o cliente paga o mesmo'))
  assert.ok(h.includes('Nosso custo: custo +1,5%.'))
  assert.ok(h.includes('512,30') && h.includes('520,10'))
  // A observação, os passos, o aviso da NF, as travas.
  assert.ok(h.includes(PREVIA.observacao.replace(/>/g, '&gt;')), 'a linha das Observações, como texto')
  assert.equal((h.match(/data-passo-previsto/g) || []).length, 4)
  assert.ok(h.includes('83955 → 9 → 6'))
  assert.match(h, /data-aviso-nf/)
  assert.equal((h.match(/data-trava /g) || []).length, 2)
  // A CAIXINHA obrigatória e a prova opcional (as 3 opções).
  assert.match(h, /data-caixinha-aceite/)
  assert.ok(h.includes('O cliente aceitou a troca'))
  for (const p of ['nenhuma', 'mensagem', 'duoke']) assert.match(h, new RegExp(`data-prova="${p}"`))
  // Sem a caixinha, o botão não anda — e diz por quê.
  const b = botao(h, 'data-botao-trocar')
  assert.ok(desligado(b), 'sem a caixinha, desligado')
  assert.ok(b.includes('title="Marque que o cliente aceitou a troca."'))
  assert.ok(h.includes('>Marque que o cliente aceitou a troca.<'))
  assert.ok(h.includes('Trocar e voltar para Em aberto'))
  await m.estado.trocar()
  assert.equal(m.chamadas.length, 1, 'o clique sem a caixinha não sai')

  // A prova pela mensagem do cliente: as da prévia, com a dica da oferta.
  m.estado.confirmar.value = true
  m.estado.prova.value = 'mensagem'
  h = await html(m)
  assert.equal((h.match(/data-aceite-possivel/g) || []).length, 2)
  assert.ok(h.includes('pode mandar a laranja sim') && h.includes('depois da oferta da loja'))
  assert.ok(desligado(botao(h, 'data-botao-trocar')), 'falta escolher a mensagem')
  m.estado.mensagemId.value = 'm-1'
  h = await html(m)
  assert.ok(!desligado(botao(h, 'data-botao-trocar')))

  // 1º clique: a rede caiu (sem resposta) — a MESMA chave fica.
  await m.estado.trocar()
  const c1 = m.chamadas[1]
  assert.equal(c1.url, '/api/atendimento/pedidos/300101/troca')
  assert.deepEqual(c1.opts, {
    method: 'POST',
    body: { sku_antigo: 'dg057.ci', sku_novo: 'dg056.ci', conversa_id: 'conv-1', aceite: { mensagem_aceite_id: 'm-1', fonte: 'davinci' }, previa_hash: 'abcdef0123456789', idem_key: chave1, confirmar: true },
  })
  assert.equal(m.estado.idemKey.value, chave1, 'sem resposta: a mesma chave (repetir devolve a mesma troca)')
  assert.equal(m.estado.precisaNovaPrevia.value, false)
  assert.ok(m.estado.erroTroca.value.motivos.join(' ').includes('sem conexão'))
  // 2º clique: a mesma chave; o backend recusa (409) — chave nova e prévia de novo.
  await m.estado.trocar()
  assert.equal(m.chamadas[2].opts.body.idem_key, chave1)
  assert.notEqual(m.estado.idemKey.value, chave1, 'recusa: a próxima é outra decisão')
  assert.equal(m.estado.erroTroca.value.code, 'situacao_mudou')
  assert.equal(m.estado.semClique.value, 'Confira a prévia de novo antes de trocar.')
  h = await html(m)
  assert.match(h, /data-erro-troca data-code="situacao_mudou"/)
  assert.ok(h.includes('O pedido já não está em Aguardando Cancelamento no Bling.'))
  assert.ok(h.includes('Confira a prévia de novo para tentar outra vez.'))
  assert.match(h, /data-ver-troca-do-erro/, 'a recusa abortou a troca t-0: dá para ver os passos dela')
  await m.estado.trocar()
  assert.equal(m.chamadas.length, 3, 'sem prévia nova, não sai')
  // Conferir de novo: a prévia (GETs no backend) e o clique com a chave nova.
  await m.estado.lerPrevia()
  assert.equal(m.chamadas[3].url, '/api/atendimento/pedidos/300101/troca/previa')
  assert.equal(m.estado.semClique.value, null)
  assert.equal(m.estado.erroTroca.value, null, 'a recusa era da prévia velha')
  assert.equal(m.estado.mensagemId.value, 'm-1', 'a mensagem que segue na janela continua escolhida')
  const chave2 = m.estado.idemKey.value
  await m.estado.trocar()
  assert.equal(m.chamadas[4].opts.body.idem_key, chave2)
  // O resultado: concluída, com os passos e o aceite; sem Retomar.
  h = await html(m)
  assert.match(h, /data-resultado-troca data-estado="concluida"/)
  assert.ok(h.includes('>Concluída<'))
  assert.equal((h.match(/data-passo /g) || []).length, 8)
  assert.ok(h.includes('Item e observação no Bling') && h.includes('Ag. Cancelamento → Atendido (9)') && h.includes('NF de volta à fila'))
  assert.ok(h.includes('Aceite declarado por Thorfinn'))
  assert.doesNotMatch(h, /data-botao-retomar|data-botao-trocar|data-previa/)
  // Fechar depois de escrever: o pai relê (o painel, a lista).
  m.estado.fechar()
  assert.equal(m.aberto.value, false)
  assert.deepEqual(m.emitidos, [['mudou']])
  // Só as rotas da troca, nunca a de responder a conversa.
  for (const c of m.chamadas) assert.match(c.url, ROTAS_DA_TROCA)
}

async function testarDuoke() {
  const m = montar({ responde: respostas((url) => (url.endsWith('/previa') ? PREVIA : TROCA)) })
  await esperar()
  m.estado.agora.value = Date.parse('2026-10-08T13:00:00Z')
  m.estado.confirmar.value = true
  m.estado.prova.value = 'duoke'
  let h = await html(m)
  assert.match(h, /data-duoke/)
  assert.match(h, /type="datetime-local" max="2026-10-08T10:00"/, 'sem futuro')
  assert.match(h, new RegExp(`maxlength="${TR.DUOKE_MAX}"`))
  assert.equal(m.estado.semClique.value, 'Cole a resposta do cliente no Duoke.')
  m.estado.duokeTexto.value = 'Pode mandar a laranja'
  m.estado.duokeEm.value = '2026-10-08T09:40'
  assert.equal(m.estado.semClique.value, null)
  await m.estado.trocar()
  assert.deepEqual(m.chamadas[1].opts.body.aceite, { fonte: 'duoke', texto: 'Pode mandar a laranja', em: '2026-10-08T09:40' })
  assert.equal(m.chamadas[1].opts.body.confirmar, true)
  // Sem prova: só a caixinha (declarado, com o nome de quem clicou).
  const s = montar({ responde: respostas((url) => (url.endsWith('/previa') ? PREVIA : TROCA)) })
  await esperar()
  s.estado.confirmar.value = true
  await s.estado.trocar()
  assert.equal(s.chamadas[1].opts.body.aceite, null)
  assert.equal(s.chamadas[1].opts.body.confirmar, true)
  h = await html(s)
  assert.match(h, /data-resultado-troca/)
}

async function testarNivel0() {
  const previa0 = { ...PREVIA, nivel: 0, mesmo_produto: true, exige_aceite: false, aceites_possiveis: [], dif_custo_pct: null }
  previa0.antes = { ...PREVIA.antes, custo: null }
  previa0.depois = { ...PREVIA.depois, sku: 'dg057.sp', custo: null }
  const m = montar({ escolha: { ...ESCOLHA, sku_novo: 'dg057.sp', nivel: 0, mesmo_produto: true }, responde: respostas((url) => (url.endsWith('/previa') ? previa0 : { ...TROCA, nivel: 0, aceite_fonte: null })) })
  await esperar()
  const h = await html(m)
  assert.ok(h.includes('Trocar lote do pedido 300101'))
  assert.ok(h.includes('Trocar lote e voltar para Em aberto'))
  assert.ok(h.includes('Mesmo produto, outro lote: a troca não pede o aceite do cliente.'))
  assert.doesNotMatch(h, /data-caixinha-aceite|data-prova|data-aceite /, 'nível 0: sem aceite')
  assert.ok(!desligado(botao(h, 'data-botao-trocar')), 'anda sem a caixinha')
  // Quem não vê a Margem: nem o % nem o custo (o backend manda null).
  assert.doesNotMatch(h, /Nosso custo|custo R\$|>custo /)
  await m.estado.trocar()
  assert.equal(m.chamadas[1].opts.body.confirmar, false)
  assert.equal(m.chamadas[1].opts.body.aceite, null)
  const r = await html(m)
  assert.ok(r.includes('Mesmo produto, outro lote: sem aceite.'))
}

async function testarTravasERecusas() {
  // Uma trava que falha: o texto em vermelho, o botão desligado com ele.
  const travada = { ...PREVIA, pode: false, previa_hash: null, travas: [...PREVIA.travas, { code: 'etiqueta_gerada', ok: false, texto: 'A etiqueta do pedido já foi gerada.' }] }
  const t = montar({ responde: respostas(() => travada) })
  await esperar()
  let h = await html(t)
  assert.match(h, /data-trava data-code="etiqueta_gerada" data-ok="false"/)
  const b = botao(h, 'data-botao-trocar')
  assert.ok(desligado(b) && b.includes('A etiqueta do pedido já foi gerada.'))
  assert.doesNotMatch(h, /data-caixinha-aceite/, 'travada: nem pede o aceite')
  // A troca aberta no pedido (a prévia diz): "ver a troca" lê a lista das trocas.
  const aberta = { id: 't-5', estado: 'em_atendido', sku_antigo: 'dg057.ci', sku_novo: 'dg056.ci', nivel: 1, automatica: false, criado_por_nome: 'T', created_at: null, codigo_erro: null, erro: null, pode_retomar: true }
  const comAberta = { ...PREVIA, pode: false, previa_hash: null, troca_aberta: aberta, travas: [{ code: 'troca_em_andamento', ok: false, texto: 'Já há uma troca em andamento neste pedido.' }] }
  const parada = { ...TROCA, id: 't-5', estado: 'em_atendido', aberta: true, pode_retomar: true, codigo_erro: 'bling_indisponivel', erro: 'O Bling não respondeu agora.', passos: [passo('iniciada'), passo('situacao', false, 'O Bling não respondeu agora.')] }
  const v = montar({
    responde: respostas((url, opts) => {
      if (url.endsWith('/previa')) return comAberta
      if (url === '/api/atendimento/trocas') return { itens: [{ ...TROCA, id: 't-0', aberta: false }, parada] }
      if (url.endsWith('/retomar')) return { ...parada, estado: 'concluida', aberta: false, pode_retomar: false }
      throw new Error(url)
    }),
  })
  await esperar()
  h = await html(v)
  assert.ok(h.includes('Já há uma troca em andamento neste pedido: Item trocado; parado em Atendido (9).'))
  assert.match(h, /data-ver-troca-aberta/)
  await v.estado.lerTroca('t-5')
  assert.deepEqual(v.chamadas[1], { url: '/api/atendimento/trocas', opts: { query: { abertas: false, pedido: '300101' } } })
  h = await html(v)
  assert.match(h, /data-resultado-troca data-estado="em_atendido"/)
  assert.ok(h.includes('O Bling não respondeu agora.'))
  assert.ok(h.includes('Parada no meio: o Retomar segue de onde parou'))
  assert.match(h, /data-botao-retomar/)
  await v.estado.retomar()
  assert.deepEqual(v.chamadas[2], { url: '/api/atendimento/trocas/t-5/retomar', opts: { method: 'POST' } })
  h = await html(v)
  assert.match(h, /data-estado="concluida"/)
  assert.doesNotMatch(h, /data-botao-retomar/)
  v.estado.fechar()
  assert.deepEqual(v.emitidos, [['mudou']], 'o retomar escreveu')

  // A chave desligada (ou fora do piloto): a prévia recusa com o porquê.
  const d = montar({ responde: respostas(() => erro409('troca_desligada', 'A troca de produto está desligada (ATENDIMENTO_TROCA_ATIVA).')) })
  await esperar()
  h = await html(d)
  assert.match(h, /data-erro-previa data-code="troca_desligada"/)
  assert.ok(h.includes('A troca de produto está desligada (ATENDIMENTO_TROCA_ATIVA).'))
  assert.doesNotMatch(h, /data-previa /)
  assert.ok(desligado(botao(h, 'data-botao-trocar')))
  d.estado.fechar()
  assert.deepEqual(d.emitidos, [], 'nada escrito: o pai não relê')
}

async function testarRetomarDoCartao() {
  // Aberto pelo "Retomar" do cartão (sem escolha): lê a troca e não pede prévia.
  const aberta = { id: 't-8', estado: 'incerta', sku_antigo: 'dg057.ci', sku_novo: 'dg056.ci', nivel: 1, automatica: false, criado_por_nome: 'T', created_at: null, codigo_erro: 'bling_indisponivel', erro: 'x', pode_retomar: false }
  const incerta = { ...TROCA, id: 't-8', estado: 'incerta', aberta: true, pode_retomar: false, passos: [passo('iniciada'), passo('put', false, 'o Bling não respondeu ao PUT: conferindo por GET')] }
  const m = montar({ escolha: null, trocaAberta: aberta, responde: respostas(() => ({ itens: [incerta] })) })
  await esperar()
  assert.deepEqual(m.chamadas, [{ url: '/api/atendimento/trocas', opts: { query: { abertas: false, pedido: '300101' } } }])
  let h = await html(m)
  assert.match(h, /data-resultado-troca data-estado="incerta"/)
  assert.ok(h.includes(TR.rotuloEstado('incerta')))
  // Alguém conduzindo (a vez vale): sem Retomar, com "Ler de novo".
  assert.doesNotMatch(h, /data-botao-retomar/)
  assert.match(h, /data-ler-de-novo/)
  assert.match(h, /data-conduzindo/)
  // A vez passou: Retomar (o texto do incerto: o GET decide, nunca repete o PUT).
  m.estado.troca.value = { ...incerta, pode_retomar: true }
  h = await html(m)
  assert.match(h, /data-botao-retomar/)
  assert.ok(h.includes('nunca repete a troca do item'))
  assert.doesNotMatch(h, /data-botao-trocar|data-conferir-de-novo/, 'sem escolha, sem clique de troca')
}

async function testarFecharNoMeio() {
  // No meio do clique o diálogo não fecha (o resultado é o que diz o que mudou).
  let solta
  const m = montar({
    responde: (url) => (url.endsWith('/previa') ? Promise.resolve(structuredClone(PREVIA)) : new Promise((r) => { solta = () => r(structuredClone(TROCA)) })),
  })
  await esperar()
  m.estado.confirmar.value = true
  const indo = m.estado.trocar()
  assert.equal(m.estado.enviando.value, true)
  m.estado.fechar()
  assert.equal(m.aberto.value, true, 'não fecha no meio')
  const h = await html(m)
  assert.ok(desligado(botao(h, 'data-botao-trocar')))
  solta()
  await indo
  assert.equal(m.estado.troca.value.estado, 'concluida')
}

// ------------------------------------------------ o que o diálogo nunca faz
{
  const setup = dialogo.descriptor.scriptSetup.content
  // Só as rotas da troca: nada de responder/enviar na conversa, nada de oferta.
  assert.doesNotMatch(setup, /\/responder|\/enviar|\/conversas\/|urlOferta|\/oferta/)
  assert.doesNotMatch(dialogo.descriptor.script.content, /\/responder|\/enviar|\/conversas\//)
  const urls = [...setup.matchAll(/api<[^>]*>\(\s*([A-Za-z_]+)\(?/g)].map((m) => m[1])
  igual([...new Set(urls)], ['urlPrevia', 'URL_TROCAS', 'urlTroca', 'urlRetomar'], 'as 4 rotas da troca')
  // A chave nasce com a abertura (e não a cada render).
  assert.match(setup, /watch\(aberto, \(v\) => \{\n {2}if \(!v\) return\n {2}idemKey\.value = novaIdemKey\(\)/)
  // Sem v-html; o Esc do diálogo não fecha a lista que está por baixo.
  assert.doesNotMatch(dialogo.fonte, /v-html/)
  assert.match(dialogo.descriptor.template.content, /@keydown\.esc\.stop="fechar"/)
}

testarNivel1()
  .then(testarDuoke)
  .then(testarNivel0)
  .then(testarTravasERecusas)
  .then(testarRetomarDoCartao)
  .then(testarFecharNoMeio)
  .then(() => console.log('ok: atendimento-troca'), (e) => {
    console.error(e)
    process.exit(1)
  })
