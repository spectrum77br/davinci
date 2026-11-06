// node tests/atendimento-fila-envio.cjs — a FILA DE ENVIO do comunicador (06/11/2026).
// O dono: "queria ajustar a questão que quando envia msg fica carregando ali na
// mensagem pra enviar ao invés de enviar e mostrar carregando ali no comunicador
// já pra poder enviar outra pra entrar na fila".
//  - Enter: a caixa limpa na hora, o foco fica nela e a mensagem vira um balão na
//    conversa; um POST por vez por conversa, na ordem em que a pessoa apertou;
//  - `ultima_vista_id` calculado na hora de sair: a própria mensagem anterior não
//    vira `conversa_mudou`; `rascunho_id` e "a sugestão estava boa?" só de quem
//    levou a sugestão; `para` fica no item;
//  - recusa temporária: até 3 tentativas, sem erro; recusa de verdade: balão
//    vermelho, a fila PARA (Tentar de novo, Editar, Descartar); sem resposta do
//    servidor: "pode ter saído", nunca reenvia sozinho, quem decide é a conversa
//    relida (a linha `enviando` ainda não decide; "não saiu" continua de olho);
//  - "Editar" guarda o lugar: a segunda não sai antes da primeira corrigida; o que
//    espera numa fila parada dá para descartar;
//  - o anexo que não saiu e a resposta marcada "Não saiu" seguram quem vem depois;
//  - o balão some quando a releitura já trouxe a linha real;
//  - pergunta do ML, avaliação e e-mail ficam como sempre foram;
//  - a própria linha `enviando` na releitura não trava a caixa;
//  - a fila sobrevive à conversa desmontada: ao voltar, os balões estão lá;
//  - o destino da caixa não muda sozinho enquanto a pessoa escreve;
//  - fechar a aba com item vivo pergunta antes; os anexos não passam na frente;
//  - duas iguais: a linha `falhou` da primeira nunca passa pela da segunda;
//  - o `envio_em_andamento` que ficou no painel não trava o anexo sem nada no ar;
//  - saiu da conta ou entrou outra pessoa: a memória limpa, o que estava na fila não
//    sai e o POST em voo termina sem balão nem toast para quem entrou.
// Aqui: as regras puras, os trechos de verdade do setup da conversa com dublês e o
// balão renderizado.
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
  const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
  assert.deepEqual(errors, [], rel)
  if (descriptor.template) {
    const tpl = compileTemplate({ source: descriptor.template.content, filename, id: path.basename(rel), compilerOptions: { isTS: true } })
    assert.deepEqual(tpl.errors, [], `${rel}: template compila`)
  }
  return { descriptor, filename }
}
const cache = new Map()
const icones = new Proxy({}, { get: (_, nome) => ({ name: String(nome), render: () => Vue.h('i', { 'data-icone': String(nome) }) }) })
function requerer(nome) {
  if (nome === 'lucide-vue-next') return icones
  const comp = /^~\/components\/(.+\.vue)$/.exec(nome)
  if (comp) {
    if (!cache.has(nome)) {
      const { descriptor } = sfc(`../components/${comp[1]}`)
      const mod = { exports: {} }
      if (descriptor.script) new Function('require', 'module', 'exports', transpile(descriptor.script.content))(requerer, mod, mod.exports)
      cache.set(nome, mod.exports)
    }
    return cache.get(nome)
  }
  const lib = /^~\/lib\/(.+)$/.exec(nome)
  if (lib) {
    if (!cache.has(nome)) {
      const mod = { exports: {} }
      new Function('require', 'module', 'exports', transpile(fs.readFileSync(path.resolve(__dirname, `../lib/${lib[1]}.ts`), 'utf8')))(requerer, mod, mod.exports)
      cache.set(nome, mod.exports)
    }
    return cache.get(nome)
  }
  return require(nome)
}
function trecho(src, ini, fim) {
  const a = src.indexOf(ini)
  const b = src.indexOf(fim, a + 1)
  assert.ok(a >= 0 && b > a, `trecho ${ini} … ${fim}`)
  return transpile(src.slice(a, b))
}
const tique = async (n = 4) => { for (let i = 0; i < n; i++) await new Promise((r) => setImmediate(r)) }
const espera = (ms) => new Promise((r) => setTimeout(r, ms))

const F = requerer('~/lib/atendimentoFilaEnvio')
const M = requerer('~/lib/atendimentoFilaMemoria')
const P = requerer('~/components/AtendimentoPlataforma.vue')
const conversaSfc = sfc('../components/AtendimentoConversa.vue')
const setup = conversaSfc.descriptor.scriptSetup.content
const tela = conversaSfc.descriptor.template.content
const U = (n) => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`

// ─── as regras puras ────────────────────────────────────────────────────────
{
  // Onde vale: lista branca, função pura.
  for (const c of ['chat', 'pos_venda', 'reclamacao', 'zap']) assert.equal(F.filaValeNoCanal(c), true, c)
  for (const c of ['pergunta', 'avaliacao', 'email', 'sac', 'dm', 'carrinho', 'comentario', '', null, undefined]) assert.equal(F.filaValeNoCanal(c), false, String(c))
  assert.equal(F.filaValeNoCanal('chat', true), false, 'o e-mail da ponte nunca, qualquer que seja o canal')

  // O tipo da falha: a mesma régua do envio de sempre.
  assert.equal(F.tipoDaFalha('envio_em_andamento', 409), 'temporaria')
  assert.equal(F.tipoDaFalha('conversa_ocupada', 409), 'temporaria')
  assert.equal(F.tipoDaFalha('conversa_mudou', 409), 'recusa')
  assert.equal(F.tipoDaFalha('texto_invalido', 422), 'recusa')
  assert.equal(F.tipoDaFalha(undefined, 422), 'recusa', 'erro de validação sem código')
  assert.equal(F.tipoDaFalha(undefined, 403), 'recusa')
  assert.equal(F.tipoDaFalha(undefined, 0), 'incerto', 'a rede caiu')
  assert.equal(F.tipoDaFalha(undefined, 502), 'incerto', 'o proxy')
  assert.equal(F.tipoDaFalha(undefined, 504), 'incerto')
  assert.equal(F.podeRetentar({ tentativas: 2 }), true)
  assert.equal(F.podeRetentar({ tentativas: F.MAX_RETENTATIVAS }), false)
  assert.equal(F.MAX_RETENTATIVAS, 3)

  const item = (n, extra = {}) => ({ ...F.novoItem(n, { conversaId: 'A', corpo: { texto: `t${n}` }, nome: 'Fulano', plataforma: 'shopee', antes: ['m1'], vista: 'm1', criadoEm: '2026-11-06T10:00:00Z' }), ...extra })
  const a = item(1)
  assert.equal(a.id, 'fila:1')
  assert.equal(a.estado, 'na_fila')
  assert.equal(a.texto, 't1')
  assert.deepEqual([...a.antes], ['m1'])

  // Um por vez: só a cabeça da fila anda; falhou ou incerto param tudo.
  assert.equal(F.proximoParaSair([]), null)
  assert.equal(F.proximoParaSair([item(1), item(2)]).id, 'fila:1')
  for (const estado of ['enviando', 'falhou', 'incerto']) assert.equal(F.proximoParaSair([item(1, { estado }), item(2)]), null, estado)
  assert.equal(F.filaParada([item(1, { estado: 'falhou' })]), true)
  assert.equal(F.filaParada([item(1, { estado: 'incerto' })]), true)
  assert.equal(F.filaParada([item(1, { estado: 'editando' }), item(2)]), true, 'o lugar guardado pelo Editar segura a fila')
  assert.equal(F.proximoParaSair([item(1, { estado: 'editando' }), item(2)]), null)
  assert.equal(F.filaParada([item(1, { estado: 'enviando' }), item(2)]), false)
  assert.equal(F.filaParada([]), false)

  // O corpo: a vista do primeiro é a da tela; a dos seguintes, a gravada.
  assert.deepEqual(F.corpoDeSaida(item(1), null), { texto: 't1', ultima_vista_id: 'm1' })
  assert.deepEqual(F.corpoDeSaida(item(1), 'm9'), { texto: 't1', ultima_vista_id: 'm9' })
  assert.deepEqual(F.corpoDeSaida(item(1, { vista: null }), null), { texto: 't1' }, 'sem vista: o backend não confere')
  assert.deepEqual(F.corpoDeSaida(item(1, { corpo: { texto: 't1', rascunho_id: 'r', para: 'mediador' }, confirmar: true }), null), { texto: 't1', rascunho_id: 'r', para: 'mediador', ultima_vista_id: 'm1', confirmar: true })

  // Transições: nova lista, nada mutado.
  const lista = [item(1), item(2)]
  const envi = F.marcarEnviando(lista, 'fila:1')
  assert.equal(lista[0].estado, 'na_fila')
  assert.equal(envi[0].estado, 'enviando')
  assert.equal(F.marcarRetentativa(envi, 'fila:1')[0].tentativas, 1)
  assert.equal(F.marcarRetentativa(envi, 'fila:1')[0].estado, 'enviando', 'na tela continua enviando')
  assert.equal(F.voltarParaFila(envi, 'fila:1')[0].estado, 'na_fila')

  // Falhou: a linha `falhou` não é mais a linha deste item.
  const fal = F.marcarFalhou(envi, 'fila:1', { texto: 'x', motivos: [] }, { permiteConfirmar: true, realId: 'r1' })
  assert.equal(fal[0].estado, 'falhou')
  assert.equal(fal[0].permiteConfirmar, true)
  assert.ok(fal[0].antes.has('r1'))
  assert.ok(fal[1].antes.has('r1'), 'a linha da que falhou nunca é a de quem espera atrás')
  assert.equal(F.marcarFalhou(envi, 'fila:1', { texto: 'x', motivos: [] })[1].antes.has('r1'), false, 'sem linha gravada, nada muda')
  assert.equal(F.marcarFalhou(envi, 'fila:1', { texto: 'x', motivos: [] }, { realId: 'r1' })[1].estado, 'na_fila', 'só a que falhou muda de estado')
  assert.equal(envi[1].antes.has('r1'), false, 'nada mutado')
  // Tentar de novo: confirma só se a recusa pedia.
  const nova = F.paraNovaTentativa(fal, 'fila:1')
  assert.equal(nova[0].estado, 'na_fila')
  assert.equal(nova[0].erro, null)
  assert.equal(nova[0].confirmar, true)
  assert.equal(nova[0].permiteConfirmar, false)
  assert.equal(F.paraNovaTentativa(F.marcarFalhou(envi, 'fila:1', { texto: 'x', motivos: [] }), 'fila:1')[0].confirmar, false)
  // Confirmou uma vez: a nova recusa (de plataforma) não desfaz.
  assert.equal(F.paraNovaTentativa(F.marcarFalhou(nova, 'fila:1', { texto: 'y', motivos: [] }), 'fila:1')[0].confirmar, true)
  const inc = F.marcarIncerto(envi, 'fila:1', 1000)
  assert.equal(inc[0].estado, 'incerto')
  assert.equal(inc[0].incertoDesde, 1000)
  assert.equal(F.marcarIncerto(inc, 'fila:1', 9000)[0].incertoDesde, 1000, 'conta desde a primeira vez')
  assert.equal(F.paraNovaTentativa(F.marcarFalhou(inc, 'fila:1', { texto: 'x', motivos: [] }), 'fila:1')[0].incertoDesde, null)

  // "Editar" com gente atrás: guarda o lugar; o próximo Enter entra nele.
  const aFalhar = F.marcarFalhou(envi, 'fila:1', { texto: 'x', motivos: [] }, { permiteConfirmar: true })
  const edit = F.marcarEditando(aFalhar, 'fila:1')
  assert.equal(edit[0].estado, 'editando')
  assert.equal(edit[0].erro, null)
  assert.equal(edit[0].permiteConfirmar, false)
  const corrigido = item(9)
  assert.deepEqual(F.entrarNaFila(edit, corrigido).map((i) => i.id), ['fila:9', 'fila:2'], 'na frente de quem esperava')
  assert.deepEqual(F.entrarNaFila(lista, corrigido).map((i) => i.id), ['fila:1', 'fila:2', 'fila:9'], 'sem lugar guardado, no fim')
  assert.deepEqual(F.descartarDaFila(lista, 'fila:2').map((i) => i.id), ['fila:1'])
  assert.deepEqual(F.descartarDaFila(edit, 'fila:2'), [], 'só o lugar guardado sobrou: não segura mais nada')
  assert.deepEqual(F.descartarDaFila(edit, 'fila:1').map((i) => i.id), ['fila:2'])

  // "Não saiu" na conferência: o item volta para a frente, vermelho, sem repetir a pergunta.
  const comSugestao = item(1, { sugestao: { id: 's', usada: true }, permiteConfirmar: true, tentativas: 2, estado: 'enviando' })
  const devolvida = F.devolverComoFalhou([item(2)], comSugestao, { texto: 'y', motivos: [] }, 'r7')
  assert.deepEqual(devolvida.map((i) => [i.id, i.estado]), [['fila:1', 'falhou'], ['fila:2', 'na_fila']])
  assert.equal(devolvida[0].sugestao, null)
  assert.equal(devolvida[0].permiteConfirmar, false)
  assert.equal(devolvida[0].tentativas, 0)
  assert.ok(devolvida[0].antes.has('r7'), 'a linha `falhou` não é mais a dele')
  assert.equal(F.conferidaNaLeitura('r1', [{ id: 'r1', status: 'revisar' }]), 'fica')
  assert.equal(F.conferidaNaLeitura('r1', [{ id: 'r1', status: 'enviando' }]), 'fica')
  assert.equal(F.conferidaNaLeitura('r1', []), 'fica', 'sem a linha, sem soltar')
  assert.equal(F.conferidaNaLeitura('r1', null), 'fica')
  assert.equal(F.conferidaNaLeitura('r1', [{ id: 'r1', status: 'enviada' }]), 'saiu')
  assert.equal(F.conferidaNaLeitura('r1', [{ id: 'r1', status: 'falhou' }]), 'nao_saiu')

  // Os anexos: só segue com tudo saído e nada para trás.
  assert.equal(F.anexosDeixamSeguir({ previstos: 2, saidos: 2, restantes: 0 }), true)
  assert.equal(F.anexosDeixamSeguir({ previstos: 1, saidos: 0, restantes: 1 }), false, 'recusado')
  assert.equal(F.anexosDeixamSeguir({ previstos: 3, saidos: 1, restantes: 0 }), false, 'largados ao trocar de conversa')
  assert.equal(F.anexosDeixamSeguir({ previstos: 1, saidos: 1, restantes: 1 }), false, 'reenviou um e outro ficou')

  // O destino: o "Editar" não junta textos de destinos diferentes, e a aba só segue
  // o destino com a pessoa no mesmo destino e sem nada escrito.
  const praComprador = { corpo: { texto: 'x', para: 'comprador' } }
  assert.equal(F.editarMudariaDestino(praComprador, 'mediador', true), true)
  assert.equal(F.editarMudariaDestino(praComprador, 'mediador', false), false, 'caixa vazia: volta para quem a mensagem ia')
  assert.equal(F.editarMudariaDestino(praComprador, 'comprador', true), false)
  assert.equal(F.editarMudariaDestino({ corpo: { texto: 'x' } }, null, true), false, 'sem destinos, como sempre')
  assert.equal(F.abaSegueODestino('comprador', 'comprador', false), true)
  assert.equal(F.abaSegueODestino('comprador', 'mediador', false), false)
  assert.equal(F.abaSegueODestino('comprador', 'comprador', true), false)

  // Saiu: a linha gravada não passa pela linha de quem vem depois.
  const tirado = F.tirarItem(lista, 'fila:1', 'r9')
  assert.deepEqual(tirado.map((i) => i.id), ['fila:2'])
  assert.ok(tirado[0].antes.has('r9'))
  assert.equal(F.tirarItem(lista, 'fila:1')[0].antes.has('r9'), false)

  // A linha real: da loja, de gente no DaVinci, fora de `antes`, o mesmo texto.
  const norm = P.normalizarTexto
  const real = (extra) => ({ id: 'r1', autor: 'loja', origem: 'davinci_humano', texto: 't1', ...extra })
  assert.equal(F.mensagemRealDoItem(item(1), [real()], norm).id, 'r1')
  assert.equal(F.mensagemRealDoItem(item(1), [real({ id: 'm1' })], norm), null, 'já estava na tela')
  assert.equal(F.mensagemRealDoItem(item(1), [real({ autor: 'cliente' })], norm), null)
  assert.equal(F.mensagemRealDoItem(item(1), [real({ origem: 'davinci_ia' })], norm), null)
  assert.equal(F.mensagemRealDoItem(item(1), [real({ texto: 'outro' })], norm), null)
  assert.equal(F.mensagemRealDoItem(item(1), null, norm), null)
  // O servidor normaliza o texto; a comparação também (no ML, o emoji sai).
  const ml = { ...item(1), texto: 'Oi  😀 tudo   bem', plataforma: 'ml' }
  assert.equal(F.mensagemRealDoItem(ml, [real({ texto: P.normalizarTexto(ml.texto, 'ml') })], norm).id, 'r1')

  // A leitura e o item sem resposta do servidor.
  const leitura = (m, desde = 1000, agora = 2000) => F.decidirPelaLeitura(item(1, { estado: 'incerto', incertoDesde: desde }), m, norm, agora)
  assert.deepEqual(leitura([]), { acao: 'nao_achou' })
  assert.equal(leitura([real({ status: 'enviada' })]).acao, 'concluir')
  assert.equal(leitura([real({ status: 'revisar' })]).acao, 'concluir')
  assert.equal(leitura([real({ status: 'falhou' })]).acao, 'concluir')
  assert.equal(leitura([real({ status: 'enviando' })]).acao, 'esperar', 'a linha `enviando` ainda não diz o que houve')
  assert.equal(leitura([real({ status: 'enviando' })], 1000, 1000 + F.ENVIO_PRESO_MS - 1).acao, 'esperar')
  assert.equal(leitura([real({ status: 'enviando' })], 1000, 1000 + F.ENVIO_PRESO_MS).acao, 'presa', 'envio morto: nenhuma leitura vai resolver')

  // O balão some só de quem já foi mandado. `na_fila` não saiu; `falhou` fica no lugar da linha.
  const comReal = [real()]
  const id1 = (estado) => F.baloesVisiveis([item(1, { estado })], comReal, norm).length
  assert.equal(id1('enviando'), 0)
  assert.equal(id1('incerto'), 0)
  assert.equal(id1('na_fila'), 1)
  assert.equal(id1('falhou'), 1)
  assert.equal(F.baloesVisiveis([item(1, { estado: 'enviando' })], [], norm).length, 1)
  // Duas mensagens iguais: a linha da primeira não apaga o balão da segunda.
  const dois = F.tirarItem([item(1, { texto: 'ok' }), item(2, { texto: 'ok', estado: 'enviando' })], 'fila:1', 'r1')
  assert.equal(F.baloesVisiveis(dois, [real({ texto: 'ok' })], norm).length, 1)

  // Os selos.
  assert.equal(F.seloDoItem({ estado: 'enviando' }, false).label, 'enviando…')
  assert.equal(F.seloDoItem({ estado: 'na_fila' }, false).label, 'na fila')
  assert.match(F.seloDoItem({ estado: 'na_fila' }, true).label, /parada/)
  assert.equal(F.seloDoItem({ estado: 'falhou' }, true).label, 'não saiu')
  assert.equal(F.seloDoItem({ estado: 'incerto' }, true).label, 'pode ter saído')
  assert.equal(F.seloDoItem({ estado: 'editando' }, true).label, 'em edição')

  // Editar nunca apaga o que a pessoa escreveu.
  assert.equal(F.textoAoEditar('', '', 'oi'), 'oi')
  assert.equal(F.textoAoEditar('   ', '', 'oi'), 'oi')
  assert.equal(F.textoAoEditar('já escrevi', '', 'oi'), 'já escrevi\noi')
  assert.equal(F.textoAoEditar('já escrevi\n\n', '', 'oi'), 'já escrevi\noi')
  assert.equal(F.textoAoEditar('sugestão', 'sugestão', 'oi'), 'oi', 'a sugestão intocada não é escrita da pessoa')
  assert.equal(F.textoAoEditar('sugestão editada', 'sugestão', 'oi'), 'sugestão editada\noi')

  // Fechar a aba: só `na_fila` e `enviando` perdem mensagem.
  assert.equal(F.temItemVivo([]), false)
  assert.equal(F.temItemVivo([[item(1, { estado: 'falhou' })], [item(2, { estado: 'incerto' })]]), false)
  assert.equal(F.temItemVivo([[item(1, { estado: 'falhou' })], [item(2)]]), true)
  assert.equal(F.temItemVivo([[item(1, { estado: 'enviando' })]]), true)
}

// ─── a conversa: os trechos de verdade do setup, com dublês ─────────────────
// `junto`: outra instância da conversa, com a mesma memória da fila e o mesmo
// servidor (a Caixa remontou a conversa).
function montar({ canal = 'chat', plataforma = 'shopee', mensagens, rascunho = null, destinos = [], junto = null } = {}) {
  const base = mensagens ?? [{ id: U(1), autor: 'cliente', origem: 'cliente', texto: 'bom dia', status: 'recebida', enviada_em: '2026-11-06T10:00:00Z' }]
  const detalheDe = (id) => ({ conversa: { id, canal, plataforma, comprador_nome: `Comprador ${id}` }, mensagens: [...(servidor.get(id) ?? base)], rascunho: rascunhos.get(id) ?? null, envio: {} })
  const servidor = junto?.servidor ?? new Map([['A', [...base]], ['B', [{ ...base[0], id: U(2) }]]])
  const rascunhos = junto?.rascunhos ?? new Map([['A', rascunho]])
  const chamadas = junto?.chamadas ?? []
  const leituras = []
  const avisos = junto?.avisos ?? []
  const montados = []
  const desmontes = []
  const ouvintes = junto?.ouvintes ?? new Map()
  const aberturas = []
  const memoria = junto?.memoria ?? M.criarMemoriaDaFila()
  let focos = 0
  const props = Vue.reactive({ conversaId: 'A', flags: null, ativa: true })
  const detalhe = Vue.ref(detalheDe('A'))
  const conversa = Vue.computed(() => detalhe.value?.conversa ?? null)
  const texto = Vue.ref('')
  const { filas, absorvidas, enviandoIds, gravadasDaFila, sugestoesLevadas, retencoes, telasDaFila, novoNumeroDaFila, avisoAoFechar } = memoria
  const filaDaConversa = Vue.computed(() => filas.get(props.conversaId) ?? [])
  const enviando = Vue.computed(() => enviandoIds.has(props.conversaId))
  const aval = { rascunhoId: null, usada: false, nota: null }
  const guardadas = new Map()
  const destinoCaixa = Vue.ref(null)
  const incertos = new Map()
  const api = (url, opts) => new Promise((resolver, rejeitar) => {
    chamadas.push({ url, body: opts.body, resolver, rejeitar })
  })
  const g = {
    computed: Vue.computed, shallowReactive: Vue.shallowReactive, watch: Vue.watch, nextTick: Vue.nextTick,
    onMounted: (fn) => montados.push(fn), onBeforeUnmount: (fn) => desmontes.push(fn),
    window: { addEventListener: (n, f) => ouvintes.set(n, f), removeEventListener: (n) => ouvintes.delete(n) },
    props, detalhe, conversa, texto, filas, absorvidas, filaDaConversa, enviandoIds, enviando,
    gravadasDaFila, sugestoesLevadas, retencoes, telasDaFila, novoNumeroDaFila, avisoAoFechar,
    prefill: Vue.ref(''), baseRascunhoId: Vue.ref(null), rejeitadaId: Vue.ref(null),
    caixa: Vue.ref({ focus: () => { focos++ } }),
    ehEmail: Vue.ref(canal === 'email'), emailResponder: Vue.ref(null), confirmouNaoResponde: Vue.ref(false),
    guardados: new Map(), limparAval() { aval.rascunhoId = null }, aval, avaliacoesGuardadas: guardadas,
    destinosReclamacao: Vue.ref(destinos), destinoCaixa, modoCaixa: Vue.ref('nota'),
    titulo: (c) => c.comprador_nome, erroEnvio: Vue.ref(null),
    rolarProFim() {}, pertoDoFim: () => true, carregando: Vue.ref(false), geracao: 0,
    carregar: async (id) => {
      leituras.push(id)
      if (id !== props.conversaId) return
      detalhe.value = detalheDe(id)
      x.resolverFilaNaLeitura(detalhe.value)
    },
    mostrarAbaDoDestino: async (id, para, podeTrocar) => { if (!podeTrocar || podeTrocar()) aberturas.push([id, para]) },
    toasts: {
      success: (t, l) => avisos.push(['ok', t, l]), error: (t, l) => avisos.push(['erro', t, l]),
      warning: (t, l) => avisos.push(['aviso', t, l]), info: (t, l) => avisos.push(['info', t, l]),
    },
    erroDoBalao: P.erroDoBalao, envioSimulado: P.envioSimulado, passaPelaModeracao: P.passaPelaModeracao,
    MODERACAO_MAGALU_ENVIADA: P.MODERACAO_MAGALU_ENVIADA, ZAP_REVISAR: P.ZAP_REVISAR,
    statusDoErro: P.statusDoErro, erroDaApi: P.erroDaApi, normalizarTexto: P.normalizarTexto,
    CODIGOS_CONFIRMAVEIS: new Set(['remetente_nao_responde']), api,
    // lib/atendimentoFilaEnvio, com o intervalo curto para o teste.
    ...F, INTERVALO_RETENTATIVA_MS: 25,
    // O caminho de sempre (fora da fila) e as travas da caixa.
    podeDigitar: Vue.ref(true), tamanho: Vue.computed(() => texto.value.trim().length), acimaDoLimite: Vue.ref(false), lacunas: Vue.ref([]),
    incertos, avaliacoesDados: Vue.ref(null), perguntaRespostaPublica: (_, t) => t, confirm: () => true,
    extrasDoEmail: () => ({}), mostrarErroEnvio() {}, RELER_APOS_RECUSA: new Set(),
    concluirEnvio: () => { avisos.push(['concluiu']) },
    // A linha do tempo.
    agora: Vue.ref(Date.parse('2026-11-06T15:00:00Z')), rotuloDia: (iso) => `dia ${iso.slice(0, 10)}`, abasDados: Vue.ref(null), abaAtual: Vue.computed(() => null),
    naAbaDaConversa: Vue.computed(() => true), conversaDaAba: () => null, rotuloDaOrigem: () => '', respondePorOutra: Vue.ref(false),
  }
  const podeEnviar = /const podeEnviar = computed\(\(\) =>\n[^\n]*\n/.exec(setup)
  assert.ok(podeEnviar, 'a linha do podeEnviar')
  const js = [
    trecho(setup, 'const RE_UUID', 'function mostrarErroEnvio'),
    trecho(setup, 'function limparCaixa', '// O que fazer com a mensagem que o envio devolveu'),
    trecho(setup, 'type Linha =', '// A bolinha da mudança de etiqueta'),
    transpile(podeEnviar[0]),
    trecho(setup, '// ─── fila de envio ', '// `opcoes` (e não um booleano solto)'),
    trecho(setup, 'async function enviar(', 'function aoTeclar'),
    'return { enviar, podeEnviar, caixaPresa, filaVale, filaDaConversaParada, motivoDaParada, podeSoltarFila, resolverFilaNaLeitura, tentarItemDeNovo, editarItem, descartarItem, soltarFila, anotarSaidaDeFora, fimDoEnvioDeAnexos, linhas }',
  ].join('\n')
  const nomes = Object.keys(g)
  const x = new Function(...nomes, js)(...nomes.map((n) => g[n]))
  for (const fn of montados) fn()
  const m = {
    ...x, g, props, detalhe, texto, filas, chamadas, leituras, avisos, aval, guardadas, destinoCaixa, ouvintes, desmontes, enviandoIds, servidor, rascunhos, absorvidas, aberturas, memoria,
    focos: () => focos,
    itens: (id = 'A') => filas.get(id) ?? [],
    estados: (id = 'A') => (filas.get(id) ?? []).map((i) => i.estado),
    // Escreve e aperta Enter.
    async mandar(t) { texto.value = t; await x.enviar(); await tique() },
    // O servidor gravou a linha e o POST respondeu.
    async responder(i, linha, conv = 'A') {
      const lin = { autor: 'loja', origem: 'davinci_humano', status: 'enviada', enviada_em: '2026-11-06T10:05:00Z', ...linha }
      servidor.get(conv).push(lin)
      chamadas[i].resolver({ mensagem: lin })
      await tique()
      return lin
    },
    async recusar(i, code, status = 409, detail) {
      const e = new Error('recusa')
      e.status = status
      e.data = code ? { detail: { code, detail } } : undefined
      chamadas[i].rejeitar(e)
      await tique()
    },
    baloes: () => x.linhas.value.filter((l) => l.tipo === 'fila').map((l) => l.item.id),
    reais: () => x.linhas.value.filter((l) => l.tipo === 'msg').map((l) => l.m.id),
    trocar(id) { props.conversaId = id; detalhe.value = detalheDe(id) },
  }
  return m
}

async function principal() {
  // Enter: a caixa limpa na hora, o balão aparece e a próxima já pode ser escrita.
  // Um POST por vez, na ordem; a própria mensagem anterior não vira `conversa_mudou`.
  {
    const m = montar()
    m.texto.value = '  primeira  '
    await m.enviar()
    assert.equal(m.texto.value, '', 'a caixa limpa na hora, antes da plataforma responder')
    await tique()
    assert.ok(m.focos() >= 1, 'o foco fica na caixa')
    assert.equal(m.caixaPresa.value, false, 'a caixa não trava')
    assert.deepEqual(m.estados(), ['enviando'])
    assert.equal(m.chamadas.length, 1)
    assert.equal(m.chamadas[0].url, '/api/atendimento/conversas/A/responder')
    assert.deepEqual(m.chamadas[0].body, { texto: 'primeira', ultima_vista_id: U(1) })
    m.texto.value = 'segunda'
    assert.equal(m.podeEnviar.value, true, 'com um POST no ar, dá para mandar a próxima')
    await m.enviar()
    await m.mandar('terceira')
    assert.deepEqual(m.estados(), ['enviando', 'na_fila', 'na_fila'])
    assert.deepEqual(m.baloes(), ['fila:1', 'fila:2', 'fila:3'], 'os balões no fim, na ordem')
    assert.equal(m.chamadas.length, 1, 'só um POST no ar por conversa')
    assert.equal(m.enviandoIds.has('A'), true, 'o polling segue parado com o POST no ar')

    await m.responder(0, { id: U(10), texto: 'primeira' })
    assert.deepEqual(m.estados(), ['enviando', 'na_fila'])
    assert.equal(m.chamadas.length, 2)
    assert.deepEqual(m.chamadas[1].body, { texto: 'segunda', ultima_vista_id: U(10) }, 'a vista é a que o item anterior gravou')
    assert.ok(m.reais().includes(U(10)), 'a linha real entra na hora')
    assert.deepEqual(m.baloes(), ['fila:2', 'fila:3'], 'no mesmo tique em que o balão sai')
    assert.deepEqual(m.leituras, [], 'não relê a cada item')

    await m.responder(1, { id: U(11), texto: 'segunda' })
    assert.deepEqual(m.chamadas[2].body, { texto: 'terceira', ultima_vista_id: U(11) })
    await m.responder(2, { id: U(12), texto: 'terceira' })
    assert.equal(m.filas.has('A'), false)
    assert.deepEqual(m.leituras, ['A'], 'a fila esvaziou: relê uma vez')
    assert.deepEqual(m.reais(), [U(1), U(10), U(11), U(12)])
    assert.deepEqual(m.avisos, [], 'conversa aberta: sem toast de sucesso, o balão já mostra')
    assert.equal(m.enviandoIds.has('A'), false)
    // Depois de esvaziar, a próxima volta a olhar a tela.
    await m.mandar('quarta')
    assert.equal(m.chamadas[3].body.ultima_vista_id, U(12))
  }

  // Id do servidor que não é uuid não vira "vista".
  {
    const m = montar()
    await m.mandar('um')
    await m.mandar('dois')
    await m.responder(0, { id: 'sim:abc', texto: 'um' })
    assert.equal(m.chamadas[1].body.ultima_vista_id, U(1))
  }

  // A sugestão: `rascunho_id` e a pergunta só de quem a levou, e só depois de sair.
  {
    const m = montar({ rascunho: { id: 'r1', texto: 'sugestão' } })
    m.g.baseRascunhoId.value = 'r1'
    await m.mandar('sugestão')
    assert.equal(m.chamadas[0].body.rascunho_id, 'r1')
    assert.equal(m.g.baseRascunhoId.value, null)
    assert.equal(m.g.rejeitadaId.value, 'r1', 'a sugestão não volta sozinha')
    await m.mandar('outra coisa')
    assert.equal(m.g.rejeitadaId.value, 'r1', 'nem depois da segunda')
    assert.equal(m.aval.rascunhoId, null, 'pergunta só ao sair')
    await m.responder(0, { id: U(10), texto: 'sugestão' })
    assert.equal(m.aval.rascunhoId, 'r1')
    assert.equal(m.aval.usada, true)
    assert.equal('rascunho_id' in m.chamadas[1].body, false)
    m.aval.rascunhoId = null
    await m.responder(1, { id: U(11), texto: 'outra coisa' })
    assert.equal(m.aval.rascunhoId, null, 'o segundo item não pergunta')
  }
  // A sugestão na tela, sem usar: a regra de hoje pergunta com `usada: false`.
  {
    const m = montar({ rascunho: { id: 'r1', texto: 'sugestão' } })
    await m.mandar('escrevi outra')
    assert.equal('rascunho_id' in m.chamadas[0].body, false)
    await m.responder(0, { id: U(10), texto: 'escrevi outra' })
    assert.equal(m.aval.rascunhoId, 'r1')
    assert.equal(m.aval.usada, false)
  }

  // A plataforma recusou: nada chegou. Balão vermelho no lugar da linha, a fila PARA.
  {
    const m = montar()
    await m.mandar('primeira')
    await m.mandar('segunda')
    await m.responder(0, { id: U(10), texto: 'primeira', status: 'falhou', erro: 'shopee HTTP 500', enviada_em: null })
    assert.deepEqual(m.estados(), ['falhou', 'na_fila'])
    assert.equal(m.chamadas.length, 1, 'a segunda não sai se a primeira não saiu')
    assert.equal(m.filaDaConversaParada.value, true)
    assert.equal(m.itens()[0].erro.motivos[0], P.erroEnvioLegivel('shopee HTTP 500'))
    assert.match(m.itens()[0].erro.texto, /NÃO chegou ao comprador/)
    assert.equal(m.avisos[0][0], 'erro')
    assert.equal(m.avisos[0][1], 'A plataforma recusou a resposta')
    assert.deepEqual(m.leituras, ['A'], 'parou: relê para mostrar o de agora')
    assert.deepEqual(m.baloes(), ['fila:1', 'fila:2'], 'o balão vermelho fica')
    assert.equal(m.reais().includes(U(10)), false, 'a linha `falhou` não aparece duas vezes')
    assert.equal(m.absorvidas.has(U(10)), true)
    // Tentar de novo: mesma mensagem; a linha `falhou` não é confundida com a nova.
    m.tentarItemDeNovo(m.itens()[0])
    await tique()
    assert.deepEqual(m.estados(), ['enviando', 'na_fila'])
    assert.equal(m.chamadas.length, 2)
    assert.equal(m.chamadas[1].body.texto, 'primeira')
    assert.equal('confirmar' in m.chamadas[1].body, false, 'só confirma se a recusa pedia')
    assert.deepEqual(m.baloes(), ['fila:1', 'fila:2'], 'o balão continua')
    // Saiu: a fila anda sozinha.
    await m.responder(1, { id: U(11), texto: 'primeira' })
    assert.deepEqual(m.chamadas[2].body, { texto: 'segunda', ultima_vista_id: U(11) })
  }

  // O servidor recusou: `conversa_mudou` confirma no "Tentar de novo"; a de outra
  // recusa, não. Toast continua, com o nome quando a conversa não está aberta.
  {
    const m = montar()
    await m.mandar('oi')
    await m.mandar('depois')
    await m.recusar(0, 'conversa_mudou', 409, 'Alguém respondeu às 10:00')
    assert.deepEqual(m.estados(), ['falhou', 'na_fila'])
    assert.equal(m.itens()[0].permiteConfirmar, true)
    assert.equal(m.itens()[0].erro.texto, P.erroDaApi({ data: { detail: { code: 'conversa_mudou', detail: 'Alguém respondeu às 10:00' } }, status: 409 }, 'x').texto)
    assert.equal(m.avisos[0][1], 'Não consegui enviar')
    m.tentarItemDeNovo(m.itens()[0])
    await tique()
    assert.equal(m.chamadas[1].body.confirmar, true)
    await m.responder(1, { id: U(10), texto: 'oi' })
    assert.equal('confirmar' in m.chamadas[2].body, false, 'o seguinte não herda a confirmação')
    assert.equal(m.chamadas[2].body.ultima_vista_id, U(10), 'a resposta de outra pessoa depois desta ainda vira conversa_mudou')
  }
  {
    const m = montar()
    await m.mandar('texto ruim')
    await m.recusar(0, 'texto_invalido', 422, ['tem telefone'])
    assert.deepEqual(m.estados(), ['falhou'])
    assert.equal(m.itens()[0].permiteConfirmar, false)
    assert.deepEqual(m.itens()[0].erro.motivos, ['tem telefone'])
    m.tentarItemDeNovo(m.itens()[0])
    await tique()
    assert.equal('confirmar' in m.chamadas[1].body, false)
  }
  // Confirmável do e-mail: a lista é a mesma regra (mesmo que nada aqui a use hoje).
  {
    const m = montar()
    await m.mandar('oi')
    await m.recusar(0, 'remetente_nao_responde', 409)
    assert.equal(m.itens()[0].permiteConfirmar, true)
  }

  // Recusa temporária: tenta de novo até 3 vezes, sem erro na tela, com a tela livre.
  {
    const m = montar()
    await m.mandar('oi')
    await m.mandar('depois')
    await m.recusar(0, 'envio_em_andamento')
    assert.deepEqual(m.estados(), ['enviando', 'na_fila'], 'na tela continua enviando')
    assert.equal(m.enviandoIds.has('A'), false, 'no intervalo o polling roda')
    await espera(60)
    await tique()
    assert.equal(m.chamadas.length, 2)
    await m.recusar(1, 'conversa_ocupada')
    await espera(60)
    await tique()
    assert.equal(m.chamadas.length, 3)
    await m.recusar(2, 'envio_em_andamento')
    await espera(60)
    await tique()
    assert.equal(m.chamadas.length, 4, 'a primeira e mais 3')
    assert.deepEqual(m.avisos, [], 'sem mostrar erro')
    await m.recusar(3, 'envio_em_andamento')
    await espera(60)
    await tique()
    assert.equal(m.chamadas.length, 4, 'parou de tentar')
    assert.deepEqual(m.estados(), ['falhou', 'na_fila'])
    assert.equal(m.itens()[0].erro.texto, P.ERROS.envio_em_andamento)
    assert.equal(m.avisos.length, 1)
    // A pessoa tenta de novo: conta do zero.
    m.tentarItemDeNovo(m.itens()[0])
    await tique()
    assert.equal(m.chamadas.length, 5)
    await m.recusar(4, 'conversa_ocupada')
    assert.deepEqual(m.estados(), ['enviando', 'na_fila'])
  }

  // Sem resposta do servidor: pode ter saído. Nunca reenvia sozinho.
  // A conversa relida não mostra a linha: não saiu, "Tentar de novo".
  {
    const m = montar()
    await m.mandar('oi')
    await m.mandar('depois')
    await m.recusar(0, null, 0)
    assert.deepEqual(m.leituras, ['A'], 'a conversa é relida')
    assert.deepEqual(m.estados(), ['falhou', 'na_fila'])
    assert.equal(m.itens()[0].erro.texto, 'A resposta não saiu')
    assert.equal(m.chamadas.length, 1, 'nunca reenvia sozinho')
    m.tentarItemDeNovo(m.itens()[0])
    await tique()
    assert.equal(m.chamadas.length, 2)
    assert.equal(m.chamadas[1].body.texto, 'oi')
  }
  // A conversa relida mostra a linha: saiu, sai da fila e segue.
  {
    const m = montar()
    await m.mandar('oi  tudo   bem')
    await m.mandar('depois')
    // O servidor gravou (normalizado) e o proxy devolveu 502.
    m.servidor.get('A').push({ id: U(10), autor: 'loja', origem: 'davinci_humano', status: 'enviada', texto: 'oi tudo bem', enviada_em: '2026-11-06T10:05:00Z' })
    await m.recusar(0, null, 502)
    assert.deepEqual(m.estados(), ['enviando'])
    assert.equal(m.chamadas.length, 2, 'só a seguinte')
    assert.equal(m.chamadas[1].body.texto, 'depois')
    assert.equal(m.chamadas[1].body.ultima_vista_id, U(10))
  }
  // A linha gravada, mas a plataforma recusou e o POST se perdeu: vira "não saiu".
  {
    const m = montar()
    await m.mandar('oi')
    m.servidor.get('A').push({ id: U(10), autor: 'loja', origem: 'davinci_humano', status: 'falhou', erro: 'timeout', texto: 'oi', enviada_em: null })
    await m.recusar(0, null, 504)
    assert.deepEqual(m.estados(), ['falhou'])
    assert.ok(m.absorvidas.has(U(10)))
    assert.deepEqual(m.baloes(), ['fila:1'])
    assert.equal(m.reais().includes(U(10)), false)
  }
  // Incerto com a conversa fechada: fica até ela abrir; aí decide.
  {
    const m = montar()
    await m.mandar('oi')
    await m.mandar('depois')
    m.trocar('B')
    await m.recusar(0, null, 0)
    assert.deepEqual(m.estados('A'), ['incerto', 'na_fila'])
    assert.deepEqual(m.leituras, [], 'a conversa da tela não é relida no lugar da A')
    assert.equal(m.avisos[0][0], 'aviso')
    assert.match(m.avisos[0][1], /Comprador A/)
    m.props.conversaId = 'A'
    await m.g.carregar('A')
    assert.deepEqual(m.estados('A'), ['falhou', 'na_fila'])
  }

  // O Wi-Fi caiu com o servidor esperando a plataforma: a linha `enviando` ainda não
  // decide. O item fica incerto, a segunda não sai e o resultado volta para a fila.
  {
    const m = montar()
    await m.mandar('primeira')
    await m.mandar('segunda')
    const lin = { id: U(10), autor: 'loja', origem: 'davinci_humano', status: 'enviando', texto: 'primeira', enviada_em: null }
    m.servidor.get('A').push(lin)
    await m.recusar(0, null, 0)
    assert.deepEqual(m.estados(), ['incerto', 'na_fila'])
    assert.equal(m.chamadas.length, 1, 'a segunda não sai com a primeira no ar')
    assert.deepEqual(m.baloes(), ['fila:2'], 'o balão dá lugar à linha real')
    // A plataforma recusou: vermelho, com as ações, e a segunda continua esperando.
    lin.status = 'falhou'
    lin.erro = 'shopee HTTP 500'
    await m.g.carregar('A')
    assert.deepEqual(m.estados(), ['falhou', 'na_fila'])
    assert.equal(m.chamadas.length, 1)
    assert.ok(m.absorvidas.has(U(10)))
    assert.deepEqual(m.baloes(), ['fila:1', 'fila:2'])
    assert.equal(m.avisos.at(-1)[1], 'A plataforma recusou a resposta')
  }
  // A mesma coisa, e ela saiu: sai da fila e a segunda vai com a linha como vista.
  {
    const m = montar()
    await m.mandar('primeira')
    await m.mandar('segunda')
    const lin = { id: U(10), autor: 'loja', origem: 'davinci_humano', status: 'enviando', texto: 'primeira', enviada_em: null }
    m.servidor.get('A').push(lin)
    await m.recusar(0, null, 502)
    assert.equal(m.chamadas.length, 1)
    lin.status = 'enviada'
    await m.g.carregar('A')
    await tique()
    assert.equal(m.chamadas.length, 2)
    assert.equal(m.chamadas[1].body.ultima_vista_id, U(10))
  }
  // Envio morto no servidor: a linha fica `enviando` para sempre. Passado o corte, a
  // pessoa decide, com o aviso de que pode ter saído.
  {
    const m = montar()
    await m.mandar('primeira')
    await m.mandar('segunda')
    m.servidor.get('A').push({ id: U(10), autor: 'loja', origem: 'davinci_humano', status: 'enviando', texto: 'primeira', enviada_em: null })
    await m.recusar(0, null, 0)
    m.filas.set('A', m.itens().map((i) => (i.estado === 'incerto' ? { ...i, incertoDesde: Date.now() - F.ENVIO_PRESO_MS } : i)))
    await m.g.carregar('A')
    assert.deepEqual(m.estados(), ['falhou', 'na_fila'])
    assert.match(m.itens()[0].erro.motivos[0], /Confira na plataforma/)
    assert.equal(m.chamadas.length, 1)
  }
  // "Não saiu" porque a releitura não achou a linha, e ela aparece depois: o item
  // sai da fila, sem o balão vermelho ao lado da mensagem real e sem o segundo POST.
  {
    const m = montar()
    await m.mandar('primeira')
    await m.mandar('segunda')
    await m.recusar(0, null, 502)
    assert.deepEqual(m.estados(), ['falhou', 'na_fila'])
    assert.equal(m.itens()[0].podeAparecer, true)
    m.servidor.get('A').push({ id: U(10), autor: 'loja', origem: 'davinci_humano', status: 'enviada', texto: 'primeira', enviada_em: '2026-11-06T10:05:00Z' })
    await m.g.carregar('A')
    await tique()
    assert.deepEqual(m.baloes(), ['fila:2'])
    assert.ok(m.reais().includes(U(10)))
    assert.equal(m.chamadas.length, 2, 'só a segunda')
    assert.equal(m.chamadas[1].body.texto, 'segunda')
    assert.equal(m.chamadas[1].body.ultima_vista_id, U(10))
  }
  // A linha aparece ainda `enviando`: volta a "pode ter saído" até o resultado.
  {
    const m = montar()
    await m.mandar('primeira')
    await m.recusar(0, null, 502)
    const lin = { id: U(10), autor: 'loja', origem: 'davinci_humano', status: 'enviando', texto: 'primeira', enviada_em: null }
    m.servidor.get('A').push(lin)
    await m.g.carregar('A')
    assert.deepEqual(m.estados(), ['incerto'])
    lin.status = 'falhou'
    await m.g.carregar('A')
    assert.deepEqual(m.estados(), ['falhou'])
    assert.equal(m.itens()[0].podeAparecer, false, 'agora é recusa de verdade')
  }
  // A recusa de verdade (código do servidor) não fica de olho: a linha com o mesmo
  // texto não é dela.
  {
    const m = montar()
    await m.mandar('primeira')
    await m.recusar(0, 'texto_invalido', 422)
    m.servidor.get('A').push({ id: U(10), autor: 'loja', origem: 'davinci_humano', status: 'enviada', texto: 'primeira', enviada_em: '2026-11-06T10:05:00Z' })
    await m.g.carregar('A')
    assert.deepEqual(m.estados(), ['falhou'])
  }

  // A plataforma não confirmou (`revisar`): quem vem depois espera a pessoa marcar.
  {
    const m = montar()
    await m.mandar('oi')
    await m.mandar('depois')
    const lin = await m.responder(0, { id: U(10), texto: 'oi', status: 'revisar', erro: 'timeout' })
    assert.deepEqual(m.estados(), ['na_fila'])
    assert.equal(m.chamadas.length, 1)
    assert.equal(m.filaDaConversaParada.value, true)
    assert.equal(m.avisos[0][0], 'aviso')
    assert.ok(m.reais().includes(U(10)), 'o balão "a conferir" é o da mensagem real')
    // A leitura com a linha ainda a conferir não solta.
    await m.g.carregar('A')
    assert.equal(m.chamadas.length, 1)
    // Marcou "Saiu": a fila anda sozinha, com a linha como vista.
    lin.status = 'enviada'
    await m.g.carregar('A')
    await tique()
    assert.equal(m.chamadas.length, 2)
    assert.equal(m.chamadas[1].body.ultima_vista_id, U(10))
  }
  // Marcou "Não saiu": a seguinte não sai sozinha. O item volta vermelho, com as
  // ações dele, no lugar da linha.
  {
    const m = montar({ rascunho: { id: 'r1', texto: 'sugestão' } })
    m.g.baseRascunhoId.value = 'r1'
    await m.mandar('sugestão')
    await m.mandar('depois')
    const lin = await m.responder(0, { id: U(10), texto: 'sugestão', status: 'revisar', erro: 'timeout' })
    assert.equal(m.aval.rascunhoId, 'r1')
    lin.status = 'falhou'
    await m.g.carregar('A')
    await tique()
    assert.equal(m.chamadas.length, 1, 'a seguinte não sai')
    assert.deepEqual(m.estados(), ['falhou', 'na_fila'])
    assert.deepEqual(m.baloes(), ['fila:1', 'fila:2'])
    assert.equal(m.reais().includes(U(10)), false, 'a linha não aparece duas vezes')
    assert.equal(m.filaDaConversaParada.value, true)
    assert.equal(m.itens()[0].sugestao, null, 'a pergunta já foi feita')
    // Tentar de novo: o item sai e depois a seguinte, com a vista certa.
    m.tentarItemDeNovo(m.itens()[0])
    await tique()
    assert.equal(m.chamadas.length, 2)
    assert.equal(m.chamadas[1].body.texto, 'sugestão')
    assert.equal(m.chamadas[1].body.ultima_vista_id, U(10))
    await m.responder(1, { id: U(11), texto: 'sugestão' })
    assert.equal(m.chamadas[2].body.texto, 'depois')
  }
  // A linha a conferir sumiu da leitura: sem certeza, não solta. Dá para tirar quem espera.
  {
    const m = montar()
    await m.mandar('oi')
    await m.mandar('depois')
    const lin = await m.responder(0, { id: U(10), texto: 'oi', status: 'revisar' })
    m.servidor.set('A', m.servidor.get('A').filter((x) => x !== lin))
    await m.g.carregar('A')
    await tique()
    assert.equal(m.chamadas.length, 1)
    m.descartarItem(m.itens()[0])
    assert.equal(m.filas.has('A'), false)
    assert.equal(m.memoria.retencoes.has('A'), false, 'sem ninguém esperando, nada segura')
  }
  // `revisar` sem ninguém esperando: nada fica parado.
  {
    const m = montar()
    await m.mandar('oi')
    await m.responder(0, { id: U(10), texto: 'oi', status: 'revisar' })
    await m.mandar('depois')
    assert.equal(m.chamadas.length, 2)
  }

  // "Editar": o texto na caixa, sem apagar o que a pessoa escreveu. Com a seguinte
  // esperando, o item guarda o lugar: ela não sai antes da primeira corrigida.
  {
    const m = montar({ rascunho: { id: 'r1', texto: 'sugestão' }, canal: 'reclamacao', destinos: [{ chave: 'comprador' }, { chave: 'mediador' }] })
    m.destinoCaixa.value = 'mediador'
    m.g.baseRascunhoId.value = 'r1'
    await m.mandar('sugestão')
    assert.equal(m.chamadas[0].body.para, 'mediador', 'o destino fica no item')
    m.destinoCaixa.value = 'comprador'
    await m.mandar('depois')
    assert.equal(m.itens()[1].corpo.para, 'comprador')
    await m.recusar(0, 'texto_invalido', 422)
    m.editarItem(m.itens()[0])
    await tique()
    assert.equal(m.texto.value, 'sugestão', 'caixa vazia: o texto do item')
    assert.equal(m.destinoCaixa.value, 'mediador', 'volta para quem a mensagem ia')
    assert.equal(m.g.modoCaixa.value, 'responder', 'a caixa de resposta fica à vista')
    assert.equal(m.g.baseRascunhoId.value, 'r1', 'a sugestão que ele usou volta a contar')
    assert.ok(m.focos() >= 1)
    assert.deepEqual(m.estados(), ['editando', 'na_fila'], 'guarda o lugar')
    assert.equal(m.chamadas.length, 1, 'a segunda não sai com a primeira parada na caixa')
    assert.equal(m.filaDaConversaParada.value, true)
    assert.deepEqual(m.baloes(), ['fila:1', 'fila:2'])
    // O próximo Enter entra no lugar guardado, com o destino e a sugestão dele.
    await m.mandar('sugestão corrigida')
    assert.equal(m.texto.value, '')
    assert.deepEqual(m.estados(), ['enviando', 'na_fila'])
    assert.deepEqual(m.itens().map((i) => i.texto), ['sugestão corrigida', 'depois'])
    assert.equal(m.chamadas[1].body.texto, 'sugestão corrigida')
    assert.equal(m.chamadas[1].body.para, 'mediador')
    assert.equal(m.chamadas[1].body.rascunho_id, 'r1')
    await m.responder(1, { id: U(10), texto: 'sugestão corrigida' })
    assert.equal(m.chamadas[2].body.texto, 'depois', 'só agora a segunda')
    assert.equal(m.chamadas[2].body.para, 'comprador')
    assert.equal(m.chamadas[2].body.ultima_vista_id, U(10))
  }
  // Desistiu da corrigida: "Descartar" no lugar guardado solta a fila; o que está na
  // caixa continua lá.
  {
    const m = montar()
    await m.mandar('Seu código é:')
    await m.mandar('AB123')
    await m.recusar(0, 'texto_invalido', 422)
    m.editarItem(m.itens()[0])
    assert.equal(m.texto.value, 'Seu código é:')
    m.descartarItem(m.itens()[0])
    await tique()
    assert.equal(m.texto.value, 'Seu código é:', 'nunca apaga a caixa')
    assert.equal(m.chamadas[1].body.texto, 'AB123')
  }
  // O que espera numa fila parada dá para tirar, sem soltar a fila. Fila andando, não.
  {
    const m = montar()
    await m.mandar('Seu código é:')
    await m.mandar('AB123')
    await m.mandar('obrigado')
    m.descartarItem(m.itens()[1])
    assert.equal(m.itens().length, 3, 'fila andando: não')
    await m.recusar(0, 'texto_invalido', 422)
    m.descartarItem(m.itens()[1])
    await tique()
    assert.deepEqual(m.itens().map((i) => i.texto), ['Seu código é:', 'obrigado'])
    assert.deepEqual(m.estados(), ['falhou', 'na_fila'])
    assert.equal(m.chamadas.length, 1)
    // Editar e tirar todo mundo de trás: o lugar guardado não segura mais nada.
    m.editarItem(m.itens()[0])
    assert.deepEqual(m.estados(), ['editando', 'na_fila'])
    m.descartarItem(m.itens()[1])
    assert.equal(m.filas.has('A'), false)
    await m.mandar('novo')
    assert.equal(m.chamadas.length, 2)
  }
  // Reclamação: texto escrito para o outro destino. O "Editar" não junta nem troca o
  // destino: avisa e o item fica como está.
  {
    const m = montar({ canal: 'reclamacao', plataforma: 'ml', destinos: [{ chave: 'comprador' }, { chave: 'mediador' }] })
    m.destinoCaixa.value = 'comprador'
    await m.mandar('Prezado comprador')
    await m.recusar(0, 'texto_invalido', 422)
    m.destinoCaixa.value = 'mediador'
    m.texto.value = 'Mediador: o comprador devolveu a caixa vazia'
    const antes = m.avisos.length
    m.editarItem(m.itens()[0])
    assert.equal(m.texto.value, 'Mediador: o comprador devolveu a caixa vazia')
    assert.equal(m.destinoCaixa.value, 'mediador')
    assert.deepEqual(m.estados(), ['falhou'])
    assert.equal(m.avisos[antes][0], 'aviso')
    assert.match(m.avisos[antes][1], /outro destino/)
    // Escrito para o mesmo destino: junta, como sempre.
    m.destinoCaixa.value = 'comprador'
    m.texto.value = 'Bom dia'
    m.editarItem(m.itens()[0])
    assert.equal(m.texto.value, 'Bom dia\nPrezado comprador')
    assert.equal(m.destinoCaixa.value, 'comprador')
  }
  // A fila esvaziou: a aba só segue o destino com a pessoa no mesmo destino e sem
  // nada escrito. Trocar a aba troca o destino da caixa, e o texto iria ao errado.
  {
    const mandarUm = async () => {
      const m = montar({ canal: 'reclamacao', plataforma: 'ml', destinos: [{ chave: 'comprador' }, { chave: 'mediador' }] })
      m.destinoCaixa.value = 'comprador'
      await m.mandar('ao comprador')
      return m
    }
    let m = await mandarUm()
    await m.responder(0, { id: U(10), texto: 'ao comprador' })
    assert.deepEqual(m.aberturas, [['A', 'comprador']], 'sem nada escrito: vai para a aba')
    m = await mandarUm()
    m.destinoCaixa.value = 'mediador'
    await m.responder(0, { id: U(10), texto: 'ao comprador' })
    assert.deepEqual(m.aberturas, [], 'mudou de destino: fica')
    assert.equal(m.destinoCaixa.value, 'mediador')
    m = await mandarUm()
    m.texto.value = 'a próxima'
    await m.responder(0, { id: U(10), texto: 'ao comprador' })
    assert.deepEqual(m.aberturas, [], 'já escrevendo: fica')
  }
  {
    const m = montar()
    await m.mandar('oi')
    await m.recusar(0, 'texto_invalido', 422)
    m.texto.value = 'já estava escrevendo'
    const item = m.itens()[0]
    m.editarItem(item)
    assert.equal(m.texto.value, 'já estava escrevendo\noi', 'nunca apaga')
    assert.equal(m.filas.has('A'), false)
    m.editarItem(item)
    assert.equal(m.texto.value, 'já estava escrevendo\noi', 'clique repetido: o texto não entra duas vezes')
  }
  // A caixa com a sugestão intocada: o texto do item entra no lugar dela, e ela não volta.
  {
    const m = montar({ rascunho: { id: 'r1', texto: 'sugestão' } })
    await m.mandar('oi')
    await m.recusar(0, 'texto_invalido', 422)
    m.texto.value = 'sugestão'
    m.g.prefill.value = 'sugestão'
    m.g.baseRascunhoId.value = 'r1'
    m.editarItem(m.itens()[0])
    assert.equal(m.texto.value, 'oi')
    assert.equal(m.g.prefill.value, '')
    assert.equal(m.g.baseRascunhoId.value, null, 'o item não usava a sugestão')
    assert.equal(m.g.rejeitadaId.value, 'r1')
  }
  // "Descartar": some, nada sai, e a fila anda sozinha.
  {
    const m = montar()
    await m.mandar('oi')
    await m.mandar('depois')
    await m.recusar(0, 'texto_invalido', 422)
    m.descartarItem(m.itens()[0])
    await tique()
    assert.deepEqual(m.estados(), ['enviando'])
    assert.equal(m.chamadas.length, 2)
    assert.equal(m.chamadas[1].body.texto, 'depois')
    assert.equal(m.chamadas[1].body.ultima_vista_id, U(1))
    assert.equal(m.texto.value, '')
    // Só o balão que não saiu tem essas ações.
    m.descartarItem(m.itens()[0])
    m.editarItem(m.itens()[0])
    m.tentarItemDeNovo(m.itens()[0])
    await tique()
    assert.equal(m.chamadas.length, 2)
    assert.deepEqual(m.estados(), ['enviando'])
    assert.equal(m.texto.value, '')
  }

  // Trocar de conversa: a resposta da A nunca entra na B; a fila continua andando.
  {
    const m = montar({ rascunho: { id: 'r1', texto: 'sugestão' } })
    await m.mandar('oi')
    await m.mandar('depois')
    m.trocar('B')
    assert.deepEqual(m.baloes(), [], 'os balões da A não aparecem na B')
    const naB = m.detalhe.value
    await m.responder(0, { id: U(10), texto: 'oi' })
    assert.equal(m.detalhe.value, naB)
    assert.equal(m.detalhe.value.mensagens.some((x) => x.id === U(10)), false, 'nada na B')
    assert.equal(m.aval.rascunhoId, null, 'a pergunta não aparece na B')
    assert.equal(m.guardadas.get('A').id, 'r1', 'fica guardada para a A')
    assert.deepEqual(m.avisos.map((a) => a[1]), ['Resposta enviada (Comprador A)'], 'conversa fechada: toast com o nome')
    assert.equal(m.chamadas.length, 2, 'a fila da A segue')
    assert.equal(m.chamadas[1].body.ultima_vista_id, U(10))
    // Na B, uma fila própria, ao mesmo tempo que a da A.
    await m.mandar('na B')
    assert.equal(m.chamadas.length, 3)
    assert.equal(m.chamadas[2].url, '/api/atendimento/conversas/B/responder')
    assert.equal(m.chamadas[2].body.ultima_vista_id, U(2))
    assert.deepEqual(m.baloes(), ['fila:3'])
    // Volta para a A: o balão da mensagem que ainda está saindo.
    m.trocar('A')
    assert.deepEqual(m.baloes(), ['fila:2'])
    await m.responder(2, { id: U(20), texto: 'na B' }, 'B')
    assert.equal(m.detalhe.value.mensagens.some((x) => x.id === U(20)), false)
    assert.deepEqual(m.avisos.map((a) => a[1]), ['Resposta enviada (Comprador A)', 'Resposta enviada (Comprador B)'])
    assert.deepEqual(m.leituras, [], 'a fila da B esvaziou com a A na tela: não relê a A à toa')
    await m.responder(1, { id: U(11), texto: 'depois' })
    assert.deepEqual(m.leituras, ['A'])
  }
  // A recusa da conversa fechada: toast com o nome.
  {
    const m = montar()
    await m.mandar('oi')
    m.trocar('B')
    await m.recusar(0, 'texto_invalido', 422)
    assert.equal(m.avisos[0][1], 'Não consegui enviar (Comprador A)')
    assert.deepEqual(m.estados('A'), ['falhou'])
  }
  // Voltou para a conversa e a leitura dela ainda não chegou: a linha entra pela leitura.
  {
    const m = montar()
    await m.mandar('oi')
    m.props.conversaId = 'B'
    m.detalhe.value = null
    m.props.conversaId = 'A'
    await m.responder(0, { id: U(10), texto: 'oi' })
    assert.equal(m.detalhe.value.mensagens.some((x) => x.id === U(10)), true)
  }

  // Duplicidade: a releitura trouxe a linha real com o balão na tela.
  {
    const m = montar()
    await m.mandar('oi')
    assert.deepEqual(m.baloes(), ['fila:1'])
    m.servidor.get('A').push({ id: U(10), autor: 'loja', origem: 'davinci_humano', status: 'enviando', texto: 'oi', enviada_em: null })
    await m.g.carregar('A')
    assert.deepEqual(m.baloes(), [], 'o balão some')
    assert.ok(m.reais().includes(U(10)))
    assert.deepEqual(m.estados(), ['enviando'], 'o item continua até o POST responder')
    m.chamadas[0].resolver({ mensagem: { id: U(10), autor: 'loja', origem: 'davinci_humano', status: 'enviada', texto: 'oi', enviada_em: '2026-11-06T10:05:00Z' } })
    await tique()
    assert.equal(m.detalhe.value.mensagens.filter((x) => x.id === U(10)).length, 1, 'uma linha só')
  }
  // A linha já estava na tela e o POST responde sem a fila esvaziar: o status acompanha.
  {
    const m = montar()
    await m.mandar('oi')
    await m.mandar('depois')
    m.servidor.get('A').push({ id: U(10), autor: 'loja', origem: 'davinci_humano', status: 'enviando', texto: 'oi', enviada_em: null })
    await m.g.carregar('A')
    m.chamadas[0].resolver({ mensagem: { id: U(10), autor: 'loja', origem: 'davinci_humano', status: 'enviada', texto: 'oi', enviada_em: '2026-11-06T10:05:00Z' } })
    await tique()
    assert.equal(m.detalhe.value.mensagens.find((x) => x.id === U(10)).status, 'enviada')
  }
  // Duas iguais: a segunda segue na fila e o servidor é a trava (`envio_repetido`).
  {
    const m = montar()
    await m.mandar('ok')
    await m.mandar('ok')
    await m.responder(0, { id: U(10), texto: 'ok' })
    assert.deepEqual(m.baloes(), ['fila:2'], 'a linha da primeira não some com o balão da segunda')
    await m.recusar(1, 'envio_repetido')
    assert.deepEqual(m.estados(), ['falhou'])
    assert.equal(m.itens()[0].erro.texto, P.ERROS.envio_repetido)
  }

  // A linha do tempo: o dia de hoje, se a última mensagem é de outro dia, e o
  // balão sempre no fim; o balão fica fora da aba em que outra conversa responde.
  {
    const m = montar({ mensagens: [{ id: U(1), autor: 'cliente', origem: 'cliente', texto: 'ontem', status: 'recebida', enviada_em: '2026-11-05T10:00:00Z' }] })
    await m.mandar('oi')
    const l = m.linhas.value
    const ultimas = l.slice(-2)
    assert.equal(ultimas[0].tipo, 'dia')
    assert.equal(ultimas[1].tipo, 'fila')
    assert.equal(ultimas[1].chave, 'fila:1', 'a chave do balão é o id local')
    assert.equal(new Set(l.map((x) => x.chave)).size, l.length, 'chaves únicas')
    m.g.respondePorOutra.value = true
    assert.deepEqual(m.baloes(), [])
  }

  // Toast de sucesso: só o que o balão não diz, com a conversa aberta.
  {
    const m = montar({ plataforma: 'magalu' })
    await m.mandar('oi')
    await m.responder(0, { id: U(10), texto: 'oi' })
    assert.deepEqual(m.avisos[0].slice(0, 3), ['ok', 'Resposta enviada', P.MODERACAO_MAGALU_ENVIADA], 'a moderação da Magalu continua avisada')
  }
  // A página em outra aba (`ativa` falso): não está na tela, o toast tem o nome.
  {
    const m = montar()
    await m.mandar('oi')
    m.props.ativa = false
    await m.responder(0, { id: U(10), texto: 'oi' })
    assert.deepEqual(m.avisos.map((a) => a[1]), ['Resposta enviada (Comprador A)'])
  }
  // Zap: a frase do "não confirmou" é a dele.
  {
    const m = montar({ canal: 'zap', plataforma: 'whatsapp' })
    assert.equal(m.filaVale.value, true)
    await m.mandar('oi')
    await m.responder(0, { id: U(10), texto: 'oi', status: 'revisar' })
    assert.deepEqual(m.avisos[0][2], P.ZAP_REVISAR.toast)
  }

  // Fechar a aba: aviso nativo com item na fila ou no ar; sem persistência.
  {
    const m = montar()
    const fechar = m.ouvintes.get('beforeunload')
    assert.equal(typeof fechar, 'function')
    const evento = () => ({ prevenido: false, returnValue: undefined, preventDefault() { this.prevenido = true } })
    let e = evento()
    fechar(e)
    assert.equal(e.prevenido, false, 'fila vazia: fecha direto')
    await m.mandar('oi')
    e = evento()
    fechar(e)
    assert.equal(e.prevenido, true)
    assert.equal(e.returnValue, '')
    await m.recusar(0, 'texto_invalido', 422)
    e = evento()
    fechar(e)
    assert.equal(e.prevenido, false, 'o que não saiu está na tela, e nada se perde em silêncio')
  }
  // Desmontou (voltar para a lista, no celular): o que foi apertado continua saindo.
  {
    const m = montar()
    await m.mandar('oi')
    await m.mandar('depois')
    for (const fn of m.desmontes) fn()
    assert.ok(m.ouvintes.has('beforeunload'), 'ainda há item vivo')
    await m.responder(0, { id: U(10), texto: 'oi' })
    assert.equal(m.chamadas.length, 2)
    assert.deepEqual(m.avisos[0].slice(0, 2), ['ok', 'Resposta enviada (Comprador A)'])
    await m.recusar(1, 'texto_invalido', 422)
    assert.match(m.avisos[1][2].join(' '), /esperando na conversa/)
    assert.equal(m.ouvintes.has('beforeunload'), true, 'a fila ainda não fechou: o ouvinte fica (e não pergunta nada)')
  }
  // Desmontou, a primeira recusou e a conversa voltou (nova instância): os balões
  // estão lá com as ações, e o que estava parado sai. Nada fica preso nem se perde.
  {
    const m = montar()
    await m.mandar('um')
    await m.mandar('dois')
    for (const fn of m.desmontes) fn()
    await m.recusar(0, 'texto_invalido', 422)
    assert.match(m.avisos[0][2].join(' '), /não saíram/)
    const pergunta = () => { const e = { prevenido: false, preventDefault() { this.prevenido = true } }; m.ouvintes.get('beforeunload')?.(e); return e.prevenido }
    assert.equal(pergunta(), true, '"dois" ainda não saiu: perder a aba perde a mensagem')
    const n = montar({ junto: m })
    assert.deepEqual(n.baloes(), ['fila:1', 'fila:2'])
    assert.equal(n.filaDaConversaParada.value, true)
    n.descartarItem(n.itens()[0])
    await tique()
    assert.equal(m.chamadas.length, 2)
    await n.responder(1, { id: U(10), texto: 'dois' })
    assert.equal(n.filas.has('A'), false)
    assert.ok(n.reais().includes(U(10)))
    assert.equal(pergunta(), false, 'tudo resolvido: não pergunta mais')
    for (const fn of n.desmontes) fn()
    assert.equal(m.ouvintes.has('beforeunload'), false, 'sem tela e sem item vivo: tira o ouvinte')
  }
  // Desmontou com o POST no ar e a conversa voltou: o resultado chega à tela nova, e
  // a instância morta não relê (reler marca a conversa como lida no servidor).
  {
    const m = montar()
    await m.mandar('um')
    await m.mandar('dois')
    for (const fn of m.desmontes) fn()
    const n = montar({ junto: m })
    assert.deepEqual(n.baloes(), ['fila:1', 'fila:2'])
    assert.equal(n.enviandoIds.has('A'), true, 'o polling da tela nova também espera o POST')
    // Mandar mais uma na tela nova: entra na mesma fila, sem segundo POST.
    await n.mandar('três')
    assert.equal(m.chamadas.length, 1)
    await n.responder(0, { id: U(10), texto: 'um' })
    assert.ok(n.reais().includes(U(10)), 'na tela nova, sem piscar')
    assert.equal(m.chamadas[1].body.texto, 'dois')
    assert.equal(m.chamadas[1].body.ultima_vista_id, U(10))
    await n.responder(1, { id: U(11), texto: 'dois' })
    assert.equal(m.chamadas[2].body.texto, 'três')
    await n.responder(2, { id: U(12), texto: 'três' })
    assert.equal(n.filas.has('A'), false)
    assert.deepEqual(n.leituras, ['A'], 'a tela nova relê uma vez, no fim')
    assert.deepEqual(m.leituras, [], 'a morta, nunca')
  }
  // Desmontou com a fila vazia no fim e nenhuma tela: ninguém relê.
  {
    const m = montar()
    await m.mandar('um')
    for (const fn of m.desmontes) fn()
    await m.responder(0, { id: U(10), texto: 'um' })
    assert.deepEqual(m.leituras, [])
  }
  {
    const m = montar()
    await m.mandar('oi')
    for (const fn of m.desmontes) fn()
    await m.responder(0, { id: U(10), texto: 'oi' })
    assert.equal(m.ouvintes.has('beforeunload'), false, 'nada vivo: tira o aviso')
  }

  // Anexos em voo: o texto entra na fila e espera; a linha do anexo não vira
  // `conversa_mudou` para ele.
  {
    const m = montar()
    m.enviandoIds.add('A')
    assert.equal(m.caixaPresa.value, false)
    await m.mandar('legenda depois da foto')
    assert.deepEqual(m.estados(), ['na_fila'])
    assert.equal(m.chamadas.length, 0, 'espera o anexo')
    m.anotarSaidaDeFora('A', U(30))
    m.enviandoIds.delete('A')
    await tique()
    assert.equal(m.chamadas.length, 1, 'o anexo terminou: a fila anda sozinha')
    assert.equal(m.chamadas[0].body.ultima_vista_id, U(30))
  }
  // O anexo não saiu: o texto que o esperava para, e não chega "segue a foto" sem a
  // foto. Saiu no reenvio: a fila anda sozinha, com a linha dele como vista.
  {
    const m = montar()
    m.enviandoIds.add('A')
    await m.mandar('segue a foto')
    m.fimDoEnvioDeAnexos('A', U(30), { previstos: 1, saidos: 0, restantes: 1 })
    m.enviandoIds.delete('A')
    await tique()
    assert.equal(m.chamadas.length, 0, 'o texto não sai sem a foto')
    assert.deepEqual(m.estados(), ['na_fila'])
    assert.equal(m.filaDaConversaParada.value, true)
    assert.equal(m.motivoDaParada.value, F.MOTIVO_ANEXO_NAO_SAIU)
    assert.equal(m.podeSoltarFila.value, true)
    // A leitura não solta: quem decide é o anexo ou a pessoa.
    await m.g.carregar('A')
    await tique()
    assert.equal(m.chamadas.length, 0)
    // O reenvio também não saiu: continua.
    m.enviandoIds.add('A')
    m.fimDoEnvioDeAnexos('A', U(31), { previstos: 1, saidos: 0, restantes: 1 })
    m.enviandoIds.delete('A')
    await tique()
    assert.equal(m.chamadas.length, 0)
    m.enviandoIds.add('A')
    m.fimDoEnvioDeAnexos('A', U(32), { previstos: 1, saidos: 1, restantes: 0 })
    m.enviandoIds.delete('A')
    await tique()
    assert.equal(m.chamadas.length, 1)
    assert.equal(m.chamadas[0].body.ultima_vista_id, U(32))
    assert.equal(m.motivoDaParada.value, '')
  }
  // "Mandar mesmo assim": a pessoa decide e a fila anda. Só vale para o anexo.
  {
    const m = montar()
    m.enviandoIds.add('A')
    await m.mandar('segue a foto')
    m.fimDoEnvioDeAnexos('A', U(30), { previstos: 1, saidos: 0, restantes: 1 })
    m.enviandoIds.delete('A')
    m.soltarFila('A')
    await tique()
    assert.equal(m.chamadas.length, 1)
    assert.equal(m.podeSoltarFila.value, false)
    await m.responder(0, { id: U(40), texto: 'segue a foto', status: 'revisar' })
    await m.mandar('outra')
    assert.equal(m.chamadas.length, 2, 'sem ninguém esperando, nada ficou')
    await m.mandar('mais uma')
    await m.responder(1, { id: U(41), texto: 'outra', status: 'revisar' })
    assert.equal(m.memoria.retencoes.get('A').tipo, 'conferir')
    m.soltarFila('A')
    assert.equal(m.memoria.retencoes.has('A'), true, 'a resposta a conferir só solta com a marcação')
  }
  // Sem texto esperando, o fim do anexo não segura nada.
  {
    const m = montar()
    m.fimDoEnvioDeAnexos('A', U(30), { previstos: 1, saidos: 0, restantes: 1 })
    assert.equal(m.memoria.retencoes.has('A'), false)
    await m.mandar('oi')
    assert.equal(m.chamadas.length, 1)
    assert.equal(m.chamadas[0].body.ultima_vista_id, U(1))
  }
  // Sem texto esperando, a linha do anexo não muda nada.
  {
    const m = montar()
    m.anotarSaidaDeFora('A', U(30))
    await m.mandar('oi')
    assert.equal(m.chamadas[0].body.ultima_vista_id, U(1))
  }

  // Onde a fila NÃO vale: fica como sempre foi, com a caixa travada.
  for (const [canal, publica] of [['pergunta', false], ['avaliacao', true], ['email', false], ['sac', false]]) {
    const m = montar({ canal })
    assert.equal(m.filaVale.value, false, canal)
    m.texto.value = 'resposta'
    const p = m.enviar()
    await tique()
    assert.equal(m.filas.has('A'), false, `${canal}: não entra na fila`)
    assert.equal(m.texto.value, 'resposta', `${canal}: o texto fica até responder`)
    assert.equal(m.caixaPresa.value, true, `${canal}: a caixa fica presa`)
    assert.equal(m.podeEnviar.value, false, `${canal}: não dá para mandar outra`)
    assert.equal(m.chamadas.length, 1)
    m.chamadas[0].resolver({ mensagem: { id: U(10), status: 'enviada', texto: 'resposta' } })
    await p
    assert.deepEqual(m.avisos, [['concluiu']], `${canal}: o caminho de sempre (publica: ${publica})`)
    assert.equal(m.caixaPresa.value, false)
  }

  // A releitura com a própria linha `enviando` no servidor: o detalhe vem com
  // `envio_em_andamento`. Onde a fila vale isso não trava a caixa; fora dela, trava.
  {
    const js = trecho(setup, 'const bloqueioEnvio = computed', 'const podeDigitar') + '\nreturn bloqueioEnvio'
    const bloqueio = (canal, envio, ehEmail = false) => new Function(
      'computed', 'detalhe', 'ehEmail', 'sellerCenterDe', 'motivoSellerCenter', 'ERROS', 'props', 'AVISO_SO_LEITURA', 'avaliacaoDaConversa', 'TRAVAS_GERAIS_DO_EMAIL', 'motivoLegivel', 'travaEmail', 'CODIGOS_CONFIRMAVEIS', 'filaValeNoCanal', 'CODIGOS_ZAP_FRASE_DO_SERVIDOR', 'bloqueioLegivel', 'semConta', js,
    )(
      Vue.computed, Vue.ref({ conversa: { canal, plataforma: 'shopee', situacao: 'aberta' }, envio }), Vue.ref(ehEmail), () => null, () => '', P.ERROS, { canEdit: true, flags: null }, '', Vue.ref(null), new Set(), (t) => t, Vue.ref(null), new Set(), F.filaValeNoCanal, new Set(), (t) => t, Vue.ref(false),
    ).value
    const emVoo = { pode_enviar: false, codigo: 'envio_em_andamento', motivo: 'Há uma resposta sendo enviada nesta conversa.' }
    for (const canal of ['chat', 'pos_venda', 'reclamacao', 'zap']) assert.equal(bloqueio(canal, emVoo), '', `${canal}: não trava`)
    for (const canal of ['pergunta', 'avaliacao']) assert.equal(bloqueio(canal, emVoo), P.ERROS.envio_em_andamento, `${canal}: trava como sempre`)
    assert.notEqual(bloqueio('chat', { pode_enviar: false, codigo: 'envio_desligado' }), '', 'as outras travas continuam')
    assert.notEqual(bloqueio('chat', { pode_enviar: false, codigo: 'conversa_bloqueada' }), '')
  }

  // A pessoa escrevendo a próxima: a própria mensagem da fila não vira "outra
  // pessoa respondeu enquanto você escrevia".
  {
    const js = trecho(setup, 'function avisarRespostaAlheia', 'function guardar(') + '\nreturn avisarRespostaAlheia'
    const avisos = []
    const filas = new Map([['A', [{ texto: 'na fila' }]]])
    const avisar = new Function('normalizarTexto', 'texto', 'filas', 'respondidaNoSellerCentral', 'respondidaNoSellerCenter', 'toasts', js)(
      P.normalizarTexto, Vue.ref('escrevendo a próxima'), filas, () => false, () => false, { warning: (t) => avisos.push(t) },
    )
    const antes = { mensagens: [] }
    const conversa = { id: 'A', plataforma: 'shopee' }
    avisar(antes, { conversa, mensagens: [{ id: 'm1', autor: 'loja', status: 'enviando', texto: 'na fila', origem: 'davinci_humano' }] })
    assert.deepEqual(avisos, [], 'é da própria pessoa')
    avisar(antes, { conversa, mensagens: [{ id: 'm2', autor: 'loja', status: 'enviada', texto: 'outra resposta', origem: 'davinci_humano', autor_nome: 'Colega' }] })
    assert.equal(avisos.length, 1)
    assert.match(avisos[0], /Colega/)
  }

  // Duas iguais ("ok", "ok"): a primeira recusada pela plataforma e descartada. A linha
  // `falhou` dela não é a da segunda: o balão fica com ela no ar, e um 502 é "sem
  // resposta", nunca "a plataforma recusou".
  {
    const m = montar()
    await m.mandar('ok')
    await m.mandar('ok')
    await m.responder(0, { id: U(10), texto: 'ok', status: 'falhou', erro: 'shopee HTTP 500' })
    assert.deepEqual(m.estados(), ['falhou', 'na_fila'])
    m.descartarItem(m.itens()[0])
    await tique()
    assert.deepEqual(m.estados(), ['enviando'])
    assert.equal(m.chamadas.length, 2)
    assert.deepEqual(m.baloes(), ['fila:2'], 'o balão não some com ela no ar')
    const avisos = m.avisos.length
    await m.recusar(1, null, 502)
    assert.deepEqual(m.estados(), ['falhou'])
    assert.equal(m.itens()[0].erro.texto, 'A resposta não saiu', 'a releitura não achou a linha dela')
    assert.equal(m.itens()[0].podeAparecer, true)
    assert.doesNotMatch(JSON.stringify(m.avisos.slice(avisos)), /plataforma recusou/)
    // A linha dela aparece depois: agora sim é a dela.
    m.servidor.get('A').push({ id: U(11), autor: 'loja', origem: 'davinci_humano', texto: 'ok', status: 'enviada', enviada_em: '2026-11-06T10:06:00Z' })
    await m.g.carregar('A')
    assert.equal(m.filas.has('A'), false)
    assert.deepEqual(m.reais(), [U(1), U(11)], 'a recusada segue fora da tela')
  }
  // O mesmo com a segunda respondendo: a linha `enviando` dela é a que esconde o balão.
  {
    const m = montar()
    await m.mandar('ok')
    await m.mandar('ok')
    await m.responder(0, { id: U(10), texto: 'ok', status: 'falhou', erro: 'shopee HTTP 500' })
    m.descartarItem(m.itens()[0])
    await tique()
    const linha = { id: U(11), autor: 'loja', origem: 'davinci_humano', texto: 'ok', status: 'enviando', enviada_em: null }
    m.servidor.get('A').push(linha)
    await m.recusar(1, null, 502)
    assert.deepEqual(m.estados(), ['incerto'], 'com a linha dela ainda no ar, espera')
    assert.deepEqual(m.baloes(), [], 'a linha real no lugar do balão')
    linha.status = 'enviada'
    linha.enviada_em = '2026-11-06T10:06:00Z'
    await m.g.carregar('A')
    assert.equal(m.filas.has('A'), false)
  }

  // O anexo com o painel lido no meio de um envio: o `envio_em_andamento` fica guardado
  // lá. Sem nada no ar nesta conversa ele não trava, com o backend seguindo como trava.
  {
    const js = trecho(setup, 'const motivoSemFoto = computed', 'const fotoInput = ref') + '\nreturn motivoSemFoto'
    const emVoo = { pode: false, codigo: 'envio_em_andamento', motivo: 'Há uma resposta sendo enviada nesta conversa.', max_anexos: 4 }
    const motivo = ({ envioFoto = emVoo, fila = [], retencao = null, enviando = false, vale = true } = {}) => new Function(
      'computed', 'detalhe', 'props', 'AVISO_SO_LEITURA', 'sellerCenterDe', 'bloqueioEnvio', 'envioFoto', 'painelCarregando', 'filaDaConversa', 'retencoes', 'MOTIVO_ANEXO_COM_FILA', 'filaVale', 'enviando', 'ERROS', 'bloqueioLegivel', 'motivoLegivel', js,
    )(
      Vue.computed, Vue.ref({ conversa: { plataforma: 'shopee', somente_leitura: false } }), { canEdit: true, conversaId: 'A' }, '', () => null, Vue.ref(''), Vue.ref(envioFoto), Vue.ref(false), Vue.ref(fila), new Map(retencao ? [['A', retencao]] : []), F.MOTIVO_ANEXO_COM_FILA, Vue.ref(vale), Vue.ref(enviando), P.ERROS, (t) => t, (t) => t,
    ).value
    assert.equal(motivo(), '', 'a fila esvaziou: o código velho não trava')
    assert.equal(motivo({ enviando: true }), P.ERROS.envio_em_andamento, 'com envio no ar nesta conversa, trava')
    assert.equal(motivo({ vale: false }), P.ERROS.envio_em_andamento, 'fora da fila, como sempre')
    assert.equal(motivo({ fila: [{ id: 'fila:1' }] }), F.MOTIVO_ANEXO_COM_FILA, 'texto na fila: não passa na frente')
    assert.equal(motivo({ fila: [{ id: 'fila:1' }], retencao: { tipo: 'anexo', motivo: 'x' } }), '', 'o texto esperando o reenvio deste anexo')
    assert.equal(motivo({ envioFoto: { pode: false, codigo: 'envio_desligado', motivo: 'y' } }), P.ERROS.envio_desligado, 'as outras travas continuam')
    assert.equal(motivo({ envioFoto: { pode: true, max_anexos: 4 } }), '')
  }

  // Saiu da conta ou entrou outra pessoa, sem recarregar a página.
  // A memória: limpa tudo o que é de quem saiu; o envio em voo continua contado.
  {
    const mem = M.criarMemoriaDaFila()
    mem.mudouUsuario('u1')
    const n = mem.novoNumeroDaFila()
    mem.filas.set('A', [{ id: `fila:${n}`, estado: 'falhou' }])
    mem.absorvidas.add(U(10))
    mem.gravadasDaFila.set('A', U(10))
    mem.sugestoesLevadas.set('A', 'r1')
    mem.retencoes.set('A', { tipo: 'anexo', motivo: 'x' })
    mem.enviandoIds.add('A')
    const tela = {}
    mem.telasDaFila.add(tela)
    mem.mudouUsuario('u1')
    assert.equal(mem.filas.size, 1, 'o mesmo usuário não limpa')
    mem.mudouUsuario(null)
    for (const nome of ['filas', 'absorvidas', 'gravadasDaFila', 'sugestoesLevadas', 'retencoes']) assert.equal(mem[nome].size, 0, nome)
    assert.equal(mem.enviandoIds.has('A'), true, 'o POST continua no ar')
    assert.equal(mem.telasDaFila.has(tela), true, 'as telas montadas seguem suas')
    assert.equal(mem.novoNumeroDaFila(), n + 1, 'o id de um item novo não repete o do item em voo')
  }
  // POST no ar e outra esperando: a que esperava não sai e a do ar termina sem nada
  // para quem entrou (nem linha nova, nem releitura, nem toast, nem pergunta).
  {
    const m = montar({ rascunho: { id: 'r1', texto: 'sugestão' } })
    m.memoria.mudouUsuario('u1')
    m.g.baseRascunhoId.value = 'r1'
    await m.mandar('sugestão')
    await m.mandar('segredo na fila')
    assert.deepEqual(m.baloes(), ['fila:1', 'fila:2'])
    m.memoria.mudouUsuario('u2')
    assert.deepEqual(m.baloes(), [], 'o outro usuário não vê balão de quem saiu')
    assert.equal(m.filaDaConversaParada.value, false)
    assert.equal(m.enviandoIds.has('A'), true, 'o POST segue no ar')
    await m.responder(0, { id: U(10), texto: 'sugestão' })
    assert.equal(m.chamadas.length, 1, 'o que esperava na fila não sai na sessão de quem entrou')
    assert.equal(m.enviandoIds.has('A'), false)
    assert.equal(m.filas.has('A'), false)
    assert.deepEqual(m.avisos, [])
    assert.deepEqual(m.leituras, [])
    assert.equal(m.aval.rascunhoId, null, 'sem a pergunta da sugestão')
    assert.equal(m.reais().includes(U(10)), false, 'a linha só entra pela leitura')
    await m.g.carregar('A')
    assert.equal(m.reais().includes(U(10)), true, 'pela leitura é mensagem como as outras')
    assert.deepEqual(m.baloes(), [])
  }
  // Sem resposta, com o POST do servidor recusando ou com a plataforma recusando: nada
  // para quem entrou, e a linha `falhou` não fica escondida.
  {
    const m = montar()
    m.memoria.mudouUsuario('u1')
    await m.mandar('um')
    m.memoria.mudouUsuario('u2')
    await m.recusar(0, null, 502)
    assert.deepEqual(m.avisos, [])
    assert.equal(m.filas.has('A'), false, 'sem lista vazia sobrando')
    assert.deepEqual(m.leituras, [])
  }
  {
    const m = montar()
    m.memoria.mudouUsuario('u1')
    await m.mandar('um')
    m.memoria.mudouUsuario(null)
    await m.recusar(0, 'texto_invalido', 422, ['x'])
    assert.deepEqual(m.avisos, [])
    assert.equal(m.filas.has('A'), false)
  }
  {
    const m = montar()
    m.memoria.mudouUsuario('u1')
    await m.mandar('um')
    m.memoria.mudouUsuario('u2')
    await m.responder(0, { id: U(10), texto: 'um', status: 'falhou', erro: 'shopee HTTP 500' })
    assert.deepEqual(m.avisos, [])
    assert.equal(m.absorvidas.has(U(10)), false)
  }
  // Quem entrou escreve na mesma conversa com o POST de quem saiu no ar: a dele espera
  // este POST acabar, sai uma vez, e a "vista" é a da tela dele.
  {
    const m = montar()
    m.memoria.mudouUsuario('u1')
    await m.mandar('de quem saiu')
    m.memoria.mudouUsuario('u2')
    await m.mandar('de quem entrou')
    assert.equal(m.chamadas.length, 1, 'espera o POST no ar')
    assert.deepEqual(m.baloes(), ['fila:2'])
    await m.responder(0, { id: U(10), texto: 'de quem saiu' })
    assert.equal(m.chamadas.length, 2)
    assert.deepEqual(m.chamadas[1].body, { texto: 'de quem entrou', ultima_vista_id: U(1) })
    assert.deepEqual(m.estados(), ['enviando'], 'o POST antigo não solta o novo')
    assert.equal(m.enviandoIds.has('A'), true)
    await m.responder(1, { id: U(11), texto: 'de quem entrou' })
    assert.equal(m.filas.has('A'), false)
    assert.equal(m.chamadas.length, 2, 'uma vez só')
  }
  // Esperando a recusa temporária passar: não volta para a fila de quem entrou.
  {
    const m = montar()
    m.memoria.mudouUsuario('u1')
    await m.mandar('um')
    await m.recusar(0, 'conversa_ocupada', 409)
    m.memoria.mudouUsuario('u2')
    await espera(50)
    assert.equal(m.chamadas.length, 1, 'não tenta de novo na sessão de quem entrou')
    assert.equal(m.filas.has('A'), false)
  }
  // O que estava parado (recusa com a linha absorvida): some, e a linha `falhou` volta
  // a ser só uma mensagem da conversa.
  {
    const m = montar()
    m.memoria.mudouUsuario('u1')
    await m.mandar('um')
    await m.responder(0, { id: U(10), texto: 'um', status: 'falhou', erro: 'shopee HTTP 500' })
    assert.deepEqual(m.baloes(), ['fila:1'])
    assert.equal(m.reais().includes(U(10)), false)
    m.memoria.mudouUsuario('u2')
    assert.deepEqual(m.baloes(), [])
    assert.equal(m.reais().includes(U(10)), true)
  }
  // O plugin: acompanha quem está logado, no mesmo tique, com a conversa montada ou não.
  {
    const src = fs.readFileSync(path.resolve(__dirname, '../plugins/atendimento-fila-usuario.client.ts'), 'utf8')
    const auth = Vue.reactive({ user: { id: 'u1' } })
    const mod = { exports: {} }
    new Function('require', 'module', 'exports', 'defineNuxtPlugin', 'useAuthStore', transpile(src))(requerer, mod, mod.exports, (f) => f, () => auth)
    mod.exports.default()
    const mem = M.memoriaDaFila
    mem.filas.set('A', [{ id: 'fila:1' }])
    auth.user = { id: 'u1', name: 'o /me relido' }
    assert.equal(mem.filas.size, 1, 'o mesmo usuário relido não é troca')
    auth.user = null
    assert.equal(mem.filas.size, 0, 'saiu: limpa na hora')
    mem.filas.set('A', [{ id: 'fila:2' }])
    auth.user = { id: 'u2' }
    assert.equal(mem.filas.size, 0, 'entrou outra pessoa: limpa na hora')
  }

  // ─── o que está no template e no setup ──────────────────────────────────
  // A caixa e o botão: onde a fila vale, não travam nem giram.
  assert.match(tela, /:disabled="!podeDigitar \|\| caixaPresa"/)
  assert.match(tela, /<Loader2 v-if="caixaPresa" class="mr-1\.5 size-4 animate-spin" \/>\s*<Send v-else class="mr-1\.5 size-4" \/>\s*Enviar/)
  assert.doesNotMatch(tela, /:disabled="!podeDigitar \|\| enviando"/)
  assert.match(setup, /const caixaPresa = computed\(\(\) => enviando\.value && !filaVale\.value\)/)
  assert.match(setup, /const filaVale = computed\(\(\) => filaValeNoCanal\(conversa\.value\?\.canal, ehEmail\.value\)\)/)
  // O polling segue parado só com o POST no ar.
  assert.match(setup, /usePollingVisivel\(async \(\) => \{\n\s+if \(!props\.conversaId \|\| enviando\.value \|\| props\.ativa === false\) return/)
  // O balão na linha do tempo, com as três ações ligadas.
  assert.match(tela, /<AtendimentoFilaBalao\s+v-else-if="l\.tipo === 'fila'"\s+:item="l\.item"\s+:parada="filaDaConversaParada"\s+:motivo-parada="filaDaConversa\[0\]\?\.id === l\.item\.id \? motivoDaParada : ''"\s+:pode-soltar="filaDaConversa\[0\]\?\.id === l\.item\.id && podeSoltarFila"\s+@tentar="tentarItemDeNovo\(l\.item\)"\s+@editar="editarItem\(l\.item\)"\s+@descartar="descartarItem\(l\.item\)"\s+@soltar="soltarFila\(l\.item\.conversaId\)"/)
  assert.ok(tela.indexOf('<AtendimentoFilaBalao') < tela.indexOf(`v-else-if="lado(l.m) === 'nota'"`), 'antes dos ramos que leem `l.m`')
  // O enviar desvia para a fila depois das validações de hoje, antes da API.
  const enviarSrc = setup.slice(setup.indexOf('async function enviar('), setup.indexOf('function aoTeclar'))
  const ordem = ['!podeEnviar.value', 'confirmaSePublica(d.conversa.canal', 'filaVale.value', 'await api<']
  for (let i = 1; i < ordem.length; i++) assert.ok(enviarSrc.indexOf(ordem[i - 1]) < enviarSrc.indexOf(ordem[i]), ordem[i])
  // Os anexos: `motivoSemFoto` é quem trava o botão, a NF, colar e o "Enviar foto".
  const semFoto = setup.slice(setup.indexOf('const motivoSemFoto = computed'), setup.indexOf('const fotoInput = ref'))
  assert.match(semFoto, /if \(filaDaConversa\.value\.length && retencoes\.get\(props\.conversaId\)\?\.tipo !== 'anexo'\) return MOTIVO_ANEXO_COM_FILA/, 'o texto que espera este anexo não trava o reenvio dele')
  assert.match(tela, /:disabled="!!motivoSemFoto \|\| enviandoFoto \|\| anexosCheios"/)
  assert.match(setup.slice(setup.indexOf('async function enviarFoto('), setup.indexOf('// ─── "tentar de novo"')), /if \(!d \|\| enviandoFoto\.value \|\| motivoSemFoto\.value\) return/)
  assert.match(F.MOTIVO_ANEXO_COM_FILA, /saindo/)
  // Com o componente desmontado o watch de `enviandoIds` para: quem solta a fila é o próprio envio.
  assert.match(setup.slice(setup.indexOf('async function enviarFoto('), setup.indexOf('// ─── "tentar de novo"')), /fimDoEnvioDeAnexos\(id, vista, \{[\s\S]+?\}\)\n\s+enviandoIds\.delete\(id\)\n\s+void andarFila\(id\)/)
  // Item que a memória limpa tirou da fila: o POST dele acabou e a fila de quem entrou
  // anda sem depender do watch, que para com o componente desmontado.
  assert.match(setup.slice(setup.indexOf('async function andarFila('), setup.indexOf('async function aoEsvaziarFila(')), /enviandoIds\.delete\(id\)\n(?:\s*\/\/[^\n]*\n)+\s+if \(!filas\.get\(id\)\?\.some\(\(x\) => x\.id === item\.id\)\) \{\n\s+void andarFila\(id\)\n\s+return\n\s+\}\n\s+if \(falha\)/)
  // A memória fica fora do componente: voltar para a lista não a perde.
  assert.match(setup, /const \{ filas, absorvidas, gravadasDaFila, sugestoesLevadas, retencoes, telasDaFila, novoNumeroDaFila, avisoAoFechar \} = memoriaDaFila/)
  assert.match(setup, /const enviandoIds = memoriaDaFila\.enviandoIds/)
  assert.equal(M.memoriaDaFila.filas.size, 0)
  // A aba do destino pergunta na hora de trocar, e quem manda de fora da fila não muda.
  assert.match(setup.slice(setup.indexOf('async function mostrarAbaDoDestino('), setup.indexOf('function aoCriarNota')), /\(!podeTrocar \|\| podeTrocar\(\)\)\) trocarAba\(aba\)/)
  // O "tentar de novo" da mensagem antiga não passa na frente da fila.
  assert.match(setup.slice(setup.indexOf('function podeTentarDeNovo('), setup.indexOf('async function tentarDeNovo(')), /filaDaConversa\.value\.length\) return false/)
  // A leitura resolve o que está pendente na fila.
  assert.match(setup.slice(setup.indexOf('async function carregar('), setup.indexOf('const atualizacaoFalhou')), /resolverIncerto\(detalhe\.value\)\n\s+resolverFilaNaLeitura\(detalhe\.value\)/)
  // A linha `falhou` absorvida sai das duas vistas.
  const linhasSrc = setup.slice(setup.indexOf('const linhas = computed'), setup.indexOf('// A bolinha da mudança de etiqueta'))
  assert.equal((linhasSrc.match(/!absorvidas\.has\(m\.id\)/g) || []).length, 2)

  // ─── o balão ────────────────────────────────────────────────────────────
  async function balao(item, parada = false, extras = {}) {
    const { descriptor, filename } = sfc('../components/AtendimentoFilaBalao.vue')
    const script = compileScript(descriptor, { id: 'balao', inlineTemplate: false })
    const tpl = compileTemplate({ source: descriptor.template.content, filename, id: 'balao', compilerOptions: { isTS: true, bindingMetadata: script.bindings } })
    const mod = { exports: {} }
    new Function('require', 'module', 'exports', 'computed', transpile(script.content))(requerer, mod, mod.exports, Vue.computed)
    const tmod = { exports: {} }
    new Function('require', 'module', 'exports', transpile(tpl.code))(requerer, tmod, tmod.exports)
    const emitidos = []
    const props = Vue.reactive({ item, parada, ...extras })
    const bruto = mod.exports.default.setup(props, { emit: (n) => emitidos.push(n), expose: () => {}, attrs: {}, slots: {} })
    const comp = { ...mod.exports.default, render: tmod.exports.render, setup: () => bruto }
    const app = Vue.createSSRApp({ render: () => Vue.h(comp, props) })
    app.config.warnHandler = () => {}
    return { html: (await renderToString(app)).replace(/<!--[\s\S]*?-->/g, ''), emitidos }
  }
  const novo = (extra) => ({ ...F.novoItem(7, { conversaId: 'A', corpo: { texto: 'Olá <b>, tudo bem?' }, nome: 'x', plataforma: 'shopee', antes: [], vista: null, criadoEm: '2026-11-06T10:00:00Z' }), ...extra })
  {
    const { html } = await balao(novo({ estado: 'enviando' }))
    assert.match(html, /data-msg-id="fila:7"/)
    assert.match(html, /data-fila-estado="enviando"/)
    assert.match(html, /enviando…/)
    assert.match(html, /data-icone="Loader2"/)
    assert.match(html, /Olá &lt;b&gt;, tudo bem\?/, 'texto cru, nunca HTML')
    assert.doesNotMatch(html, /data-fila-tentar|data-fila-editar|data-fila-descartar/)
    // Sem os botões da mensagem real.
    assert.doesNotMatch(html, /apagar|data-apagar-mensagem|tentar de novo|Saiu/)
  }
  {
    const { html } = await balao(novo({ estado: 'na_fila' }))
    assert.match(html, /na fila/)
    assert.doesNotMatch(html, /parada/)
    assert.match((await balao(novo({ estado: 'na_fila' }), true)).html, /na fila · parada/)
  }
  {
    const { html } = await balao(novo({ estado: 'falhou', erro: { texto: 'A plataforma recusou', motivos: ['loja desconectada'] } }))
    assert.match(html, /não saiu/)
    assert.match(html, /ring-red-400/)
    assert.match(html, /A plataforma recusou/)
    assert.match(html, /loja desconectada/)
    assert.match(html, /data-fila-tentar[^>]*>[\s\S]*Tentar de novo/)
    assert.match(html, /data-fila-editar[^>]*>[\s\S]*Editar/)
    assert.match(html, /data-fila-descartar[^>]*>[\s\S]*Descartar/)
  }
  {
    const { html } = await balao(novo({ estado: 'falhou', erro: null, permiteConfirmar: true }))
    assert.match(html, /você viu a resposta que já saiu/, 'o title diz que confirma')
  }
  {
    const { html } = await balao(novo({ estado: 'incerto' }))
    assert.match(html, /pode ter saído/)
    assert.match(html, /Não envie de novo ainda/)
    assert.doesNotMatch(html, /data-fila-tentar|data-fila-descartar/, 'nada de reenviar enquanto não se sabe')
  }
  {
    // Fila parada: quem espera pode sair; o primeiro diz o que segura.
    const { html } = await balao(novo({ estado: 'na_fila' }), true)
    assert.match(html, /data-fila-descartar[^>]*>[\s\S]*Descartar/)
    assert.doesNotMatch(html, /data-fila-tentar|data-fila-editar|data-fila-soltar/)
  }
  {
    const { html } = await balao(novo({ estado: 'na_fila' }), true, { motivoParada: F.MOTIVO_ANEXO_NAO_SAIU, podeSoltar: true })
    assert.match(html, /O anexo que ia antes/)
    assert.match(html, /data-fila-soltar[^>]*>[\s\S]*Mandar mesmo assim/)
  }
  {
    const { html, emitidos } = await balao(novo({ estado: 'editando' }), true)
    assert.match(html, /em edição/)
    assert.match(html, /data-fila-editando/)
    assert.match(html, /data-fila-descartar/)
    assert.doesNotMatch(html, /data-fila-tentar|data-fila-editar/)
    assert.deepEqual(emitidos, [])
  }
  {
    const { html } = await balao(novo({ corpo: { texto: 'x', para: 'mediador' } }))
    assert.match(html, /data-fila-para[^>]*>→ Mercado Livre \(mediador\)/)
  }
  console.log('ok: atendimento-fila-envio')
}

principal().catch((e) => {
  console.error(e)
  process.exit(1)
})
