// node tests/atendimento-abas.cjs — as ABAS da conversa aberta (RF2, 02/10/2026).
// "Como o Duoke: Com o comprador / Com Meli". Embaixo da conversa: Pré-venda ·
// Pós-venda · Reclamação · Mediador · E-mail · Zap · Avaliação, com a contagem,
// só as que têm conteúdo. Aqui, a tela:
//  - o contrato com o backend (rota, ordem e nomes das abas, campos de
//    AbasOut/AbaOut/AbaConversaOut/AbaRespondeOut/AbaMensagemOut, a trava);
//  - as regras puras (abas da barra, aba inicial = a da conversa, quem
//    responde é outra conversa?, divisor de origem, página das antigas);
//  - a barra renderizada (Vue SSR) e a caixa da aba (AtendimentoAbaResposta):
//    responde PELA conversa de origem, bloqueada com o envio desligado,
//    Mediador só leitura, avaliação com a confirmação de resposta pública;
//  - a conversa: a linha do tempo de cada aba (sem o que mora em outra aba,
//    com as mensagens das outras conversas e o divisor "De: …"), as ações
//    só nas mensagens desta conversa, e a caixa da aba no lugar certo.
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
  compileScript(descriptor, { id: path.basename(rel) })
  return { descriptor, fonte, filename }
}
// `~/components/X.vue` (o import entre componentes) → os exports do <script> dele.
const cache = new Map()
function requerer(nome) {
  const m = /^~\/components\/(.+\.vue)$/.exec(nome)
  if (!m) return require(nome)
  if (!cache.has(m[1])) cache.set(m[1], exportsDe(sfc(`../components/${m[1]}`).descriptor))
  return cache.get(m[1])
}
function exportsDe(descriptor) {
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(descriptor.script.content))(requerer, mod, mod.exports)
  return mod.exports
}
function trecho(src, ini, fim) {
  const a = src.indexOf(ini)
  const b = src.indexOf(fim, a + 1)
  assert.ok(a >= 0 && b > a, `trecho ${ini} … ${fim}`)
  return transpile(src.slice(a, b))
}
const api = (rel) => fs.readFileSync(path.resolve(__dirname, '../../api/app', rel), 'utf8')

const abasSfc = sfc('../components/AtendimentoAbas.vue')
const A = exportsDe(abasSfc.descriptor)
const respSfc = sfc('../components/AtendimentoAbaResposta.vue')
const R = exportsDe(respSfc.descriptor)
const plataforma = sfc('../components/AtendimentoPlataforma.vue')
const P = exportsDe(plataforma.descriptor)
const Av = exportsDe(sfc('../components/AtendimentoAvaliacao.vue').descriptor)
const conversa = sfc('../components/AtendimentoConversa.vue')
const setup = conversa.descriptor.scriptSetup.content
const tela = conversa.descriptor.template.content

// ------------------------------------------------ o contrato com o backend
const servico = api('services/atendimento/abas.py')
const rotas = api('routers/atendimento_abas.py')
const mainPy = api('main.py')
const constantes = api('services/atendimento/constantes.py')
{
  const nomes = Object.fromEntries([...servico.matchAll(/^(ABA_[A-Z_]+) = "([a-z_]+)"$/gm)].map((m) => [m[1], m[2]]))
  const ordem = ((servico.match(/^ORDEM_ABAS = \(([\s\S]*?)\)$/m) || [])[1] || '').match(/ABA_[A-Z_]+/g).map((n) => nomes[n])
  assert.deepEqual([...A.ORDEM_ABAS], ordem, 'a ordem das abas = a do backend')
  assert.deepEqual([...A.ORDEM_ABAS], ['pre_venda', 'pos_venda', 'reclamacao', 'mediador', 'email', 'zap', 'avaliacao'])
  const rotulos = Object.fromEntries([...((servico.match(/^ROTULO_ABA = \{([\s\S]*?)^\}/m) || [])[1] || '').matchAll(/(ABA_[A-Z_]+): "([^"]+)"/g)].map((m) => [nomes[m[1]], m[2]]))
  assert.deepEqual(A.ROTULO_ABAS, rotulos, 'os nomes das abas = os do backend')
  assert.match(constantes, /^CANAL_ZAP = "zap"$/m, 'o canal do Zap tem nome')

  // A rota, com a trava do atendimento, registrada no app.
  assert.match(rotas, /prefix="\/api\/atendimento", tags=\["atendimento"\], dependencies=\[Depends\(_so_admin\)\]/)
  assert.match(rotas, /@router\.get\("\/conversas\/\{conversa_id\}\/abas", response_model=AbasOut\)/)
  assert.match(rotas, /user: Annotated\[User, Depends\(_view\)\]/)
  assert.match(mainPy, /app\.include_router\(atendimento_abas_router\.router\)/)
  assert.ok(setup.includes('/abas`'), 'a conversa lê GET /conversas/{id}/abas')
  assert.ok(setup.includes('/abas?aba=${encodeURIComponent(chave)}&antes_de=${encodeURIComponent(aba.proximo)}'), 'e pagina por aba')
  assert.ok(respSfc.fonte.includes('/api/atendimento/conversas/${encodeURIComponent(id)}/responder'), 'a caixa da aba responde pelo caminho único')

  // Os campos: os da tela = os do backend (nos dois sentidos).
  const campos = (classe) => {
    const m = rotas.match(new RegExp(`^class ${classe}\\((?:BaseModel|MensagemOut)\\):\\n([\\s\\S]*?)(?=\\n\\n\\n|\\n(?:class|def|async def|@) )`, 'm'))
    assert.ok(m, classe)
    return [...m[1].matchAll(/^ {4}([a-z_]+): /gm)].map((x) => x[1])
  }
  const tipo = (nome) => {
    const m = abasSfc.fonte.match(new RegExp(`export interface ${nome}(?: extends \\w+)? \\{\\n([\\s\\S]*?)\\n\\}`))
    assert.ok(m, nome)
    return [...m[1].matchAll(/^ {2}([a-z_]+)\??: /gm)].map((x) => x[1])
  }
  for (const [ts_, py] of [['AbasResposta', 'AbasOut'], ['Aba', 'AbaOut'], ['AbaConversa', 'AbaConversaOut'], ['AbaResponde', 'AbaRespondeOut'], ['AbaMensagem', 'AbaMensagemOut']]) {
    assert.deepEqual(tipo(ts_).sort(), campos(py).sort(), `${ts_} = ${py}`)
  }
  assert.match(abasSfc.fonte, /export interface AbaMensagem extends Mensagem \{/, 'a mensagem da aba é a Mensagem de sempre + a origem')
  // O motivo do Mediador vem do backend; a tela não inventa outro.
  assert.match(servico, /^MOTIVO_MEDIADOR = \(/m)
}

// ------------------------------------------------ regras puras
const resp = (o = {}) => ({
  conversa_id: 'c-pack', canal: 'pos_venda', canal_rotulo: 'Pós-venda', pode_enviar: false, motivo: 'O envio pelo DaVinci está desligado (ATENDIMENTO_ENVIO_ATIVO).',
  codigo: 'envio_desligado', limite_caracteres: 350, modo_observacao: true, publica: false, ultima_vista_id: 'm-9', ...o,
})
const conv = (id, o = {}) => ({ id, canal: 'pergunta', canal_rotulo: 'Pergunta no anúncio', titulo: 'Mala ABS', pedido_marketplace: null, situacao: 'aberta', aguardando_resposta: false, ultima_mensagem_em: null, aberta: false, mensagens: 1, ...o })
const msg = (id, conversaId, em, o = {}) => ({ id, conversa_id: conversaId, canal: 'pergunta', autor: 'cliente', origem: 'cliente', autor_nome: null, tipo: 'texto', texto: id, anexos: [], enviada_em: em, status: 'recebida', erro: null, ...o })
const aba = (chave, o = {}) => ({ chave, rotulo: A.ROTULO_ABAS[chave], total: 1, conversas: [], mensagens: [], tem_mais: false, proximo: null, responde: resp(), ...o })
const resposta = (o = {}) => ({
  conversa_id: 'c-pack',
  aba_da_conversa: 'pos_venda',
  abas: [
    aba('avaliacao', { total: 1 }),
    aba('pos_venda', { total: 3, conversas: [conv('c-pack', { canal: 'pos_venda', canal_rotulo: 'Pós-venda', aberta: true, mensagens: 3 })] }),
    aba('pre_venda', { total: 2, conversas: [conv('c-p1')], responde: resp({ conversa_id: 'c-p1', canal: 'pergunta', canal_rotulo: 'Pergunta no anúncio' }) }),
    aba('mediador', { total: 2, responde: resp({ conversa_id: null, canal: null, canal_rotulo: null, codigo: 'somente_leitura', motivo: 'Mediador: só leitura.' }) }),
    aba('email', { total: 0 }),
  ],
  fora_da_aba: {},
  pedido: ['2000009999999999', '2000018509205724'],
  compra_em: '2026-09-27T12:00:00+00:00',
  primeira_compra_em: null,
  ...o,
})
{
  // A barra: na ordem da tela, só as com conteúdo (+ a da conversa, mesmo vazia).
  assert.deepEqual(A.abasVisiveis(resposta()).map((a) => a.chave), ['pre_venda', 'pos_venda', 'mediador', 'avaliacao'])
  const vazia = resposta({ aba_da_conversa: 'email' })
  assert.deepEqual(A.abasVisiveis(vazia).map((a) => a.chave), ['pre_venda', 'pos_venda', 'mediador', 'email', 'avaliacao'])
  assert.deepEqual(A.abasVisiveis(null), [])
  assert.deepEqual(A.abasVisiveis({ ...resposta(), abas: [] }), [])

  // A ativa: a da conversa aberta; a escolhida continua se ainda existir.
  assert.equal(A.abaInicial(resposta(), null), 'pos_venda')
  assert.equal(A.abaInicial(resposta(), 'mediador'), 'mediador')
  assert.equal(A.abaInicial(resposta(), 'zap'), 'pos_venda', 'a que sumiu volta para a da conversa')
  assert.equal(A.abaInicial(resposta({ aba_da_conversa: null }), null), 'pre_venda')
  assert.equal(A.abaInicial(null, 'pos_venda'), null)

  // Quem responde: a aberta (caixa de sempre) × outra conversa × ninguém.
  const r = resposta()
  const por = (c) => r.abas.find((a) => a.chave === c)
  assert.equal(A.respondeOutra(por('pos_venda'), 'c-pack'), false)
  assert.equal(A.respondeOutra(por('pre_venda'), 'c-pack'), true)
  assert.equal(A.respondeOutra(por('mediador'), 'c-pack'), true, 'Mediador: a caixa da aba diz que é só leitura')
  assert.equal(A.respondeOutra(null, 'c-pack'), false, 'sem abas: a caixa de sempre')

  // De onde veio (o divisor da linha do tempo).
  assert.equal(A.rotuloDaOrigem(conv('x')), 'Pergunta no anúncio · Mala ABS')
  assert.equal(A.rotuloDaOrigem({ canal: 'reclamacao', canal_rotulo: 'Reclamação', titulo: null }), 'Reclamação')
  assert.equal(A.rotuloDaOrigem(null), 'Outra conversa')
  assert.equal(A.conversaDaAba(r, 'c-p1').titulo, 'Mala ABS')
  assert.equal(A.conversaDaAba(r, 'nao-existe'), null)

  // A página das antigas entra antes, sem repetir.
  const atual = aba('reclamacao', { mensagens: [msg('m3', 'c-r', '2026-10-01T10:00:00Z'), msg('m4', 'c-r', '2026-10-01T11:00:00Z')], tem_mais: true, proximo: 'cur-1' })
  const pagina = aba('reclamacao', { mensagens: [msg('m1', 'c-r', '2026-09-30T10:00:00Z'), msg('m3', 'c-r', '2026-10-01T10:00:00Z')], tem_mais: false, proximo: null })
  const junta = A.mesclarPagina(atual, pagina)
  assert.deepEqual(junta.mensagens.map((m) => m.id), ['m1', 'm3', 'm4'])
  assert.equal(junta.tem_mais, false)
  assert.equal(junta.proximo, null)

  // A releitura das abas mantém as mais antigas já carregadas (com o cursor
  // delas): a primeira página nova ainda alcança a lista de antes.
  {
    const m = (id, conversa = 'c-r') => msg(id, conversa, `2026-10-01T${id.slice(1).padStart(2, '0')}:00:00Z`, { canal: 'reclamacao' })
    const convR = [conv('c-r', { canal: 'reclamacao' })]
    const velha = resposta({ conversa_id: 'c-recl', abas: [aba('reclamacao', { conversas: convR, mensagens: [m('m1'), m('m2'), m('m3'), m('m4')], tem_mais: false, proximo: null }), aba('pre_venda', { mensagens: [m('m5', 'c-p1')], tem_mais: true, proximo: 'cur-p' })] })
    // Chegou a m5: a página nova (2 por página) é m4, m5 — com mais antigas.
    const nova = resposta({ conversa_id: 'c-recl', abas: [aba('reclamacao', { conversas: convR, mensagens: [m('m4'), m('m5')], tem_mais: true, proximo: 'cur-4' }), aba('pre_venda', { mensagens: [m('m5', 'c-p1')], tem_mais: true, proximo: 'cur-p2' })] })
    const fica = A.manterAntigas(nova, velha)
    const recl = fica.abas.find((a) => a.chave === 'reclamacao')
    assert.deepEqual(recl.mensagens.map((x) => x.id), ['m1', 'm2', 'm3', 'm4', 'm5'])
    assert.equal(recl.tem_mais, false, 'as antigas já estavam todas: nada mais a carregar')
    assert.equal(recl.proximo, null)
    // Nada carregado além da primeira página (a mais antiga é a mesma): a nova.
    assert.equal(fica.abas.find((a) => a.chave === 'pre_venda').proximo, 'cur-p2')
    // Página nova completa, outra conversa aberta, ou longe demais: a nova.
    const completa = resposta({ conversa_id: 'c-recl', abas: [aba('reclamacao', { conversas: convR, mensagens: [m('m2'), m('m5')], tem_mais: false })] })
    assert.deepEqual(A.manterAntigas(completa, velha).abas[0].mensagens.map((x) => x.id), ['m2', 'm5'])
    assert.equal(A.manterAntigas(nova, { ...velha, conversa_id: 'outra' }), nova)
    assert.equal(A.manterAntigas(nova, null), nova)
    const longe = resposta({ conversa_id: 'c-recl', abas: [aba('reclamacao', { conversas: convR, mensagens: [m('m8'), m('m9')], tem_mais: true, proximo: 'cur-8' })] })
    assert.deepEqual(A.manterAntigas(longe, velha).abas[0].mensagens.map((x) => x.id), ['m8', 'm9'])
    // A conversa que saiu da aba leva as antigas dela junto.
    const semC = resposta({ conversa_id: 'c-recl', abas: [aba('reclamacao', { conversas: [conv('c-x')], mensagens: [m('m4'), m('m5')], tem_mais: true, proximo: 'cur-4' })] })
    assert.deepEqual(A.manterAntigas(semC, velha).abas[0].mensagens.map((x) => x.id), ['m4', 'm5'])
  }

  // O "title" da aba.
  assert.equal(A.tituloDaAba(por('pos_venda'), 'pos_venda'), 'Pós-venda: 3 mensagens · esta conversa')
  assert.equal(A.tituloDaAba(por('pre_venda'), 'pos_venda'), 'Pré-venda: 2 mensagens · 1 outra conversa do mesmo comprador e pedido')
  assert.match(A.tituloDaAba(por('mediador'), 'pos_venda'), /só leitura$/)

  // Por que a caixa da aba não responde.
  assert.equal(R.bloqueioDaAba(por('mediador'), true), 'Mediador: só leitura.')
  assert.equal(R.bloqueioDaAba(por('pre_venda'), true), P.ERROS.envio_desligado, 'envio desligado: a frase de sempre')
  assert.equal(R.bloqueioDaAba(por('pre_venda'), false), 'Você pode ler, mas não responder: falta a permissão de editar o Atendimento.')
  assert.equal(R.bloqueioDaAba({ chave: 'pos_venda', responde: resp({ codigo: 'conversa_bloqueada', motivo: 'blocked_by_claim' }) }, true), P.ERROS.conversa_bloqueada)
  assert.equal(R.bloqueioDaAba({ chave: 'pos_venda', responde: resp({ codigo: 'conversa_bloqueada', motivo: 'A janela fechou.' }) }, true), 'A plataforma não deixa mais responder esta conversa: A janela fechou.')
  assert.equal(R.bloqueioDaAba({ chave: 'avaliacao', responde: resp({ codigo: 'ja_respondida', motivo: 'x' }) }, true), P.ERROS.ja_respondida)
  assert.equal(R.bloqueioDaAba({ chave: 'pos_venda', responde: resp({ pode_enviar: true, codigo: null, motivo: null }) }, true), '')
  assert.equal(R.bloqueioDaAba(null, true), 'Esta aba é só de leitura.')

  // E-mail do Tuta e Zap (05/10/2026): o envio deles ainda não existe no
  // DaVinci. O código `canal_sem_envio` NÃO tem frase na tela de propósito:
  // a caixa (da aba e da conversa) e o aviso do 409 mostram a frase do
  // backend, que diz qual dos dois é e onde responder.
  {
    assert.match(api('services/atendimento/enviar.py'), /^RECUSA_CANAL_SEM_ENVIO = "canal_sem_envio"$/m)
    assert.equal(P.ERROS.canal_sem_envio, undefined, 'sem tradução: a tela mostra a frase do backend')
    for (const [nome, chave] of [['MOTIVO_TUTA_SEM_ENVIO', 'email'], ['MOTIVO_ZAP_SEM_ENVIO', 'zap']]) {
      const bloco = (constantes.match(new RegExp(`^${nome} = \\(([\\s\\S]*?)^\\)`, 'm')) || [])[1] || ''
      const frase = [...bloco.matchAll(/"([^"]*)"/g)].map((x) => x[1]).join('')
      assert.match(frase, /ainda não existe no DaVinci/, nome)
      assert.equal(R.bloqueioDaAba({ chave, responde: resp({ codigo: 'canal_sem_envio', motivo: frase }) }, true), frase)
      assert.equal(P.motivoLegivel(frase), frase, 'a caixa da conversa: `motivoLegivel(envio.motivo)`')
      assert.equal(P.erroDaApi({ data: { detail: { code: 'canal_sem_envio', detail: frase } } }).texto, frase, 'o 409 do responder e do "Sugerir"')
    }
  }
}

// ------------------------------------------------ a barra renderizada (Vue SSR)
async function renderBarra(props) {
  const tpl = compileTemplate({ source: abasSfc.descriptor.template.content, filename: abasSfc.filename, id: 'abas' })
  const mod = {}
  new Function('exports', 'require', transpile(tpl.code))(mod, require)
  const emitidos = []
  const app = Vue.createSSRApp({ setup: () => ({ ...props, ...A, emit: (...a) => emitidos.push(a) }), render: mod.render })
  return { html: (await renderToString(app)).replace(/<!--[\s\S]*?-->/g, ''), emitidos }
}

// ------------------------------------------------ a caixa da aba (setup de verdade, API falsa)
function montarResposta(props, { respostaApi, confirmar = true } = {}) {
  const script = compileScript(respSfc.descriptor, { id: 'aba-resposta', inlineTemplate: false })
  const chamadas = []
  const avisos = []
  const perguntas = []
  const req = (nome) => {
    if (nome === 'lucide-vue-next') return new Proxy({}, { get: () => ({ render: () => Vue.h('i') }) })
    return requerer(nome)
  }
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', 'ref', 'computed', 'watch', 'useApi', 'useToasts', 'confirm', transpile(script.content))(
    req, mod, mod.exports, Vue.ref, Vue.computed, Vue.watch,
    () => ({ api: async (url, opts) => { chamadas.push({ url, opts }); if (respostaApi instanceof Error) throw respostaApi; return respostaApi } }),
    () => ({ success: (t) => avisos.push(t), error: (t) => avisos.push(t) }),
    (p) => { perguntas.push(p); return confirmar },
  )
  const emitidos = []
  const reativo = Vue.reactive({ ...props })
  // Como o Vue faz com o que o setup devolve: os refs lidos e gravados pelo valor.
  const estado = Vue.proxyRefs(mod.exports.default.setup(reativo, { emit: (...a) => emitidos.push(a), expose: () => {}, attrs: {}, slots: {} }))
  return { estado, chamadas, avisos, perguntas, emitidos, reativo }
}
async function renderResposta(m) {
  const tpl = compileTemplate({ source: respSfc.descriptor.template.content, filename: respSfc.filename, id: 'aba-resposta' })
  const mod = {}
  new Function('exports', 'require', transpile(tpl.code))(mod, require)
  const app = Vue.createSSRApp({ setup: () => ({ ...m.reativo, ...m.estado }), render: mod.render })
  app.component('Button', { props: ['disabled'], setup: (p, { slots }) => () => Vue.h('button', { disabled: p.disabled }, slots.default?.()) })
  for (const n of ['ExternalLink', 'Globe', 'Loader2', 'Lock', 'Send', 'X']) app.component(n, { render: () => Vue.h('i') })
  // Sem os comentários de fragmento do SSR (<!--[-->).
  return (await renderToString(app)).replace(/<!--[\s\S]*?-->/g, '')
}

// ------------------------------------------------ a linha do tempo da conversa com as abas
function linhasCom({ detalhe, abas, ativa }) {
  const js = trecho(setup, 'type Linha =', '// A bolinha da mudança de etiqueta') + '\nreturn linhas'
  const abasDados = Vue.ref(abas)
  const abaAtual = Vue.computed(() => A.abasVisiveis(abasDados.value).find((a) => a.chave === ativa) ?? null)
  const naAbaDaConversa = Vue.computed(() => !abaAtual.value || abaAtual.value.chave === abasDados.value?.aba_da_conversa)
  return new Function('computed', 'detalhe', 'agora', 'rotuloDia', 'abasDados', 'abaAtual', 'naAbaDaConversa', 'conversaDaAba', 'rotuloDaOrigem', js)(
    Vue.computed, Vue.ref(detalhe), Vue.ref(Date.parse('2026-10-02T15:00:00Z')), (iso) => `dia ${iso.slice(0, 10)}`,
    abasDados, abaAtual, naAbaDaConversa, A.conversaDaAba, A.rotuloDaOrigem,
  ).value
}
const resumo = (linhas) => linhas.map((l) => (l.tipo === 'dia' ? l.texto : l.tipo === 'msg' ? `${l.m.id}${l.de ? `<${l.de}` : ''}` : l.tipo === 'origem' ? `[${l.texto}]` : l.h.id))

async function principal() {
  // A barra: rótulo, contagem, a ativa marcada; clicar troca.
  {
    const { html } = await renderBarra({ abas: A.abasVisiveis(resposta()), ativa: 'mediador', daConversa: 'pos_venda' })
    const ordem = [...html.matchAll(/data-aba="([a-z_]+)"/g)].map((m) => m[1])
    assert.deepEqual(ordem, ['pre_venda', 'pos_venda', 'mediador', 'avaliacao'])
    assert.match(html, /role="tablist"/)
    assert.match(html, /aria-selected="true"[^>]*data-aba="mediador"/)
    assert.match(html, />Pré-venda<\/span>/)
    assert.match(html, />Mediador<\/span>/)
    assert.match(html, /tabular-nums[^>]*>2<\/span>/)
    assert.match(html, /title="Pós-venda: 3 mensagens · esta conversa"/)
    assert.match(html, /bg-violet-500/, 'o mediador no violeta do balão dele')
  }

  // Mediador: só leitura — sem caixa, sem chamada.
  {
    const r = resposta()
    const m = montarResposta({ aba: r.abas.find((a) => a.chave === 'mediador'), canEdit: true, plataforma: 'ml', avisoPublica: null })
    const html = await renderResposta(m)
    assert.match(html, /Mediador: só leitura\./)
    assert.doesNotMatch(html, /<textarea/)
    await m.estado.enviar()
    assert.equal(m.chamadas.length, 0)
  }

  // Envio desligado: a caixa aparece, desabilitada, com o porquê; nada sai.
  {
    const r = resposta()
    const m = montarResposta({ aba: r.abas.find((a) => a.chave === 'pre_venda'), canEdit: true, plataforma: 'ml', avisoPublica: null })
    m.estado.texto = 'Temos sim!'
    const html = await renderResposta(m)
    assert.match(html, /Responde em <span[^>]*>Pergunta no anúncio<\/span> · Mala ABS/)
    assert.match(html, /<textarea[^>]*disabled/)
    assert.ok(html.includes(P.ERROS.envio_desligado))
    await m.estado.enviar()
    assert.equal(m.chamadas.length, 0, 'com o envio desligado nada é chamado')
  }

  // Envio ligado: responde PELA conversa de origem, com a última vista dela.
  {
    const a = aba('pre_venda', { conversas: [conv('c-p1')], responde: resp({ conversa_id: 'c-p1', canal: 'pergunta', canal_rotulo: 'Pergunta no anúncio', pode_enviar: true, codigo: null, motivo: null, limite_caracteres: 2000, ultima_vista_id: 'm-p1' }) })
    const m = montarResposta({ aba: a, canEdit: true, plataforma: 'ml', avisoPublica: null }, { respostaApi: { mensagem: msg('nova', 'c-p1', '2026-10-02T12:00:00Z', { autor: 'loja', status: 'enviada' }) } })
    m.estado.texto = '  Temos sim!  '
    await m.estado.enviar()
    assert.equal(m.chamadas.length, 1)
    assert.equal(m.chamadas[0].url, '/api/atendimento/conversas/c-p1/responder')
    assert.deepEqual(m.chamadas[0].opts, { method: 'POST', body: { texto: 'Temos sim!', ultima_vista_id: 'm-p1' } })
    assert.equal(m.estado.texto, '', 'saiu: a caixa limpa')
    assert.equal(m.emitidos[0][0], 'enviada')
    assert.deepEqual(m.perguntas, [], 'pergunta no anúncio não pede a confirmação de resposta pública')
  }

  // Avaliação: a mesma confirmação de resposta PÚBLICA; desistiu, nada sai.
  {
    const a = aba('avaliacao', { conversas: [conv('c-av', { canal: 'avaliacao', canal_rotulo: 'Avaliação' })], responde: resp({ conversa_id: 'c-av', canal: 'avaliacao', canal_rotulo: 'Avaliação', pode_enviar: true, codigo: null, motivo: null, publica: true, limite_caracteres: 500 }) })
    const m = montarResposta({ aba: a, canEdit: true, plataforma: 'shopee', avisoPublica: null }, { confirmar: false, respostaApi: { mensagem: null } })
    m.estado.texto = 'Sentimos muito!'
    const html = await renderResposta(m)
    assert.ok(html.includes(Av.AVISO_RESPOSTA_PUBLICA))
    await m.estado.enviar()
    assert.equal(m.chamadas.length, 0)
    assert.deepEqual(m.perguntas, [Av.perguntaRespostaPublica(null, 'Sentimos muito!')])
  }

  // A conversa mudou: a recusa oferece "enviar mesmo assim", com confirmar=true.
  {
    const a = aba('pos_venda', { conversas: [conv('c-pack', { canal: 'pos_venda' })], responde: resp({ pode_enviar: true, codigo: null, motivo: null }) })
    const erro = Object.assign(new Error('409'), { statusCode: 409, data: { detail: { code: 'conversa_mudou', detail: 'x' } } })
    const m = montarResposta({ aba: a, canEdit: true, plataforma: 'ml', avisoPublica: null }, { respostaApi: erro })
    m.estado.texto = 'oi'
    await m.estado.enviar()
    assert.equal(m.estado.erro.confirmar, true)
    assert.equal(m.estado.texto, 'oi', 'recusou: o texto fica')
    await m.estado.enviar({ confirmar: true })
    assert.equal(m.chamadas[1].opts.body.confirmar, true)
  }

  // A linha do tempo da conversa aberta (o pack), na aba DELA: a de sempre,
  // com as mensagens das OUTRAS conversas desta aba (e o divisor de origem).
  const detalhe = {
    conversa: { id: 'c-recl' },
    mensagens: [
      msg('r1', 'c-recl', '2026-10-01T10:00:00Z'),
      msg('r2', 'c-recl', '2026-10-01T11:00:00Z', { autor: 'mediador', origem: 'sistema' }),
      msg('r3', 'c-recl', '2026-10-01T12:00:00Z', { autor: 'loja', origem: 'externo' }),
    ],
    etiqueta_historico: [{ id: 'h1', em: '2026-10-01T10:30:00Z', de: 'pos_venda', para: 'reclamacao' }],
  }
  const abasDaRecl = {
    conversa_id: 'c-recl',
    aba_da_conversa: 'reclamacao',
    fora_da_aba: { r2: 'mediador' },
    pedido: [],
    compra_em: null,
    abas: [
      aba('pos_venda', { total: 1, conversas: [conv('c-pack', { canal: 'pos_venda', canal_rotulo: 'Pós-venda', titulo: 'pedido 2000' })], mensagens: [msg('p1', 'c-pack', '2026-09-29T09:00:00Z', { canal: 'pos_venda' })] }),
      aba('reclamacao', {
        total: 3,
        conversas: [conv('c-recl', { canal: 'reclamacao', canal_rotulo: 'Reclamação', titulo: 'nº 1', aberta: true }), conv('c-recl2', { canal: 'reclamacao', canal_rotulo: 'Reclamação', titulo: 'nº 2' })],
        // Na aba da conversa aberta vêm só as das OUTRAS conversas.
        mensagens: [msg('x1', 'c-recl2', '2026-10-01T11:30:00Z', { canal: 'reclamacao' })],
      }),
      aba('mediador', { total: 1, conversas: [conv('c-recl', { aberta: true })], mensagens: [msg('r2', 'c-recl', '2026-10-01T11:00:00Z', { autor: 'mediador', origem: 'sistema' })] }),
    ],
  }
  {
    const linhas = linhasCom({ detalhe, abas: abasDaRecl, ativa: 'reclamacao' })
    assert.deepEqual(resumo(linhas), ['dia 2026-10-01', 'r1', 'h1', '[Reclamação · nº 2]', 'x1<c-recl2', '[Esta conversa]', 'r3'])
    const origem = linhas.find((l) => l.tipo === 'origem')
    assert.equal(origem.id, 'c-recl2')
    assert.equal(origem.aberta, false)
    assert.ok(linhas.filter((l) => l.tipo === 'msg' && l.de).every((l) => l.chave.startsWith('aba-')), 'a chave da de fora não bate com a desta conversa')
  }
  {
    // Outra aba (Mediador): as mensagens daquela parte; as desta conversa na
    // versão do detalhe, sem divisor (são desta conversa).
    const linhas = linhasCom({ detalhe, abas: abasDaRecl, ativa: 'mediador' })
    assert.deepEqual(resumo(linhas), ['dia 2026-10-01', 'r2'])
    assert.equal(Vue.toRaw(linhas[1].m), detalhe.mensagens[1], 'a versão do detalhe (relida a cada 15 s)')
    assert.equal(linhas[1].de, null)
  }
  {
    // Pós-venda (de outra conversa): o divisor "De: …" no começo.
    const linhas = linhasCom({ detalhe, abas: abasDaRecl, ativa: 'pos_venda' })
    assert.deepEqual(resumo(linhas), ['dia 2026-09-29', '[Pós-venda · pedido 2000]', 'p1<c-pack'])
  }
  {
    // Sem abas (rota fora do ar): a de sempre, sem divisor.
    const linhas = linhasCom({ detalhe, abas: null, ativa: null })
    assert.deepEqual(resumo(linhas), ['dia 2026-10-01', 'r1', 'h1', 'r2', 'r3'])
  }

  // A conversa: a barra entre as mensagens e a caixa; a caixa da aba antes da
  // observação (na aba de outra conversa, o painel da IA desta não aparece).
  {
    const iMsgs = tela.indexOf('<!-- mensagens -->')
    const iBarra = tela.indexOf('<AtendimentoAbas')
    const iCaixa = tela.indexOf('<!-- resposta -->')
    assert.ok(iMsgs > 0 && iMsgs < iBarra && iBarra < iCaixa, 'a barra fica embaixo das mensagens, em cima da caixa')
    assert.match(tela, /<AtendimentoAbas\s+v-if="abasDaBarra\.length"[\s\S]*?@trocar="trocarAba"/)
    const iNota = tela.indexOf('<AtendimentoNota\n            v-if="modoCaixa === \'nota\'')
    const iDaAba = tela.indexOf('<AtendimentoAbaResposta')
    const iObs = tela.indexOf('<AtendimentoObservacao')
    assert.ok(iNota > 0 && iNota < iDaAba && iDaAba < iObs, 'nota interna > caixa da aba > observação > caixa de sempre')
    assert.match(tela, /<AtendimentoAbaResposta\s+v-else-if="respondePorOutra && abaAtual"/)
    assert.match(tela, /@enviada="aoResponderPelaAba"/)
    // As ações (tentar de novo, conferir) só nas mensagens DESTA conversa.
    assert.match(tela, /v-if="!l\.de && podeTentarDeNovo\(l\.m\)"/)
    assert.match(tela, /l\.m\.status === 'revisar' && lado\(l\.m\) === 'loja' && !l\.de/)
    // A foto é desta conversa: some na aba em que responde outra.
    assert.match(tela, /<template v-if="!respondePorOutra">\s*<input ref="fotoInput"/)
    assert.match(tela, /modoCaixa === 'responder' && foto && !respondePorOutra/)
    // O divisor de origem abre a conversa de origem.
    assert.match(tela, /l\.tipo === 'origem'[\s\S]{0,600}emit\('abrirConversa', l\.id\)/)
    // A conversa nova começa na aba dela; as abas relidas com o resto.
    assert.match(trecho(setup, "watch(() => props.conversaId, (novo, velho) => {", '}, { immediate: true })'), /abasDados\.value = null[\s\S]*abaAtiva\.value = null[\s\S]*void carregarAbas\(novo\)/)
    assert.match(trecho(setup, 'function atualizarTudo', '\n}'), /carregarAbas\(props\.conversaId\)/)
    // A releitura mantém as mais antigas; a primeira leitura (ou a falha, ou
    // o tempo) libera a linha do tempo — que espera só no chat e no mediador.
    const carregar = trecho(setup, 'async function carregarAbas', '\n}')
    assert.match(carregar, /abasDados\.value = manterAntigas\(r, abasDados\.value\)/)
    assert.match(carregar, /finally \{\s*if \(g === geracaoAbas\)\s*liberarAbas\(id\)/)
    assert.match(trecho(setup, "watch(() => props.conversaId, (novo, velho) => {", '}, { immediate: true })'), /esperarAbas\(novo\);?\s*void carregarAbas\(novo\)/)
    assert.match(setup, /setTimeout\(\(\) => liberarAbas\(id\), ESPERA_ABAS_MS\)/)
    assert.match(tela, /<div v-if="esperandoAbas"[^>]*data-abas-pendentes/)
    assert.match(tela, /v-for="l in \(esperandoAbas \? \[\] : linhas\)"/)
  }

  // A espera da primeira leitura: só no chat e com mediador; liberada uma vez.
  {
    const js = trecho(setup, 'const abasPendentes = ref(false)', 'async function carregarAbas') + '\nreturn { abasPendentes, esperarAbas, liberarAbas, esperandoAbas }'
    const props = { conversaId: 'c-1' }
    const detalheRef = Vue.ref({ conversa: { id: 'c-1', canal: 'reclamacao' }, mensagens: [msg('r1', 'c-1', '2026-10-01T10:00:00Z')] })
    const rolagens = []
    const timers = []
    const e = new Function('ref', 'computed', 'props', 'detalhe', 'rolarProFim', 'onBeforeUnmount', 'setTimeout', 'clearTimeout', js)(
      Vue.ref, Vue.computed, props, detalheRef, () => rolagens.push(1), () => {}, (fn) => { timers.push(fn); return timers.length }, () => {},
    )
    e.esperarAbas('c-1')
    assert.equal(e.abasPendentes.value, true)
    assert.equal(e.esperandoAbas.value, false, 'reclamação sem mediador: nada mora em outra aba, não espera')
    detalheRef.value = { ...detalheRef.value, mensagens: [...detalheRef.value.mensagens, msg('r2', 'c-1', '2026-10-01T11:00:00Z', { autor: 'mediador' })] }
    assert.equal(e.esperandoAbas.value, true, 'com o mediador: espera as abas')
    detalheRef.value = { conversa: { id: 'c-1', canal: 'chat' }, mensagens: [msg('c1', 'c-1', '2026-10-01T10:00:00Z')] }
    assert.equal(e.esperandoAbas.value, true, 'o chat: espera (pré e pós-venda)')
    e.liberarAbas('outra')
    assert.equal(e.abasPendentes.value, true, 'a resposta de outra conversa não libera')
    timers[0]()
    assert.equal(e.abasPendentes.value, false, 'o tempo libera')
    assert.equal(rolagens.length, 1, 'e rola para o fim (a linha do tempo apareceu agora)')
    e.liberarAbas('c-1')
    assert.equal(rolagens.length, 1, 'liberada uma vez só')
    e.esperarAbas('ig:9')
    assert.equal(e.abasPendentes.value, false, 'Direct: sem abas, não espera')
  }

  console.log('ok — atendimento-abas: contrato, regras, barra, caixa da aba e linha do tempo')
}

principal().catch((e) => {
  console.error(e)
  process.exit(1)
})
