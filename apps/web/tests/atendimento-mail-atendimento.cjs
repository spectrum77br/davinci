// node tests/atendimento-mail-atendimento.cjs — o E-MAIL das lojas no /atendimento
// (RF5/RF6, 08/10/2026), por cima da Central de e-mail do outro dev.
// Sem rede, sem caixa de verdade, sem e-mail de verdade. Aqui, a tela:
//  - o contrato com o backend (rotas de routers/atendimento_email.py e
//    mail_atendimento.py, as filas e os códigos de constantes.py/responder.py,
//    a visão da configuração, os códigos novos da Central);
//  - as regras puras (cartão, prévia e travas, filas, configuração, chamado);
//  - os componentes de verdade (setup + Vue SSR, API falsa): a aba E-mail
//    dele com as seções (Caixas | Filas), o "Configurar", o Saiu/Não saiu da
//    caixa crua; as Filas; a prévia da resposta; o cartão; o agrupar; a caixa
//    da aba no e-mail;
//  - a conversa: o cartão nas mensagens, a prévia antes da caixa, o corpo do
//    envio e a trava do e-mail (trechos do setup rodando com dublês).
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
const cache = new Map()
const icones = new Proxy({}, { get: (_, nome) => ({ name: String(nome), render: () => Vue.h('i', { 'data-icone': String(nome) }) }) })
function requerer(nome) {
  if (nome === 'lucide-vue-next') return icones
  const m = /^~\/components\/(.+\.vue)$/.exec(nome)
  if (!m) return require(nome)
  if (!cache.has(m[1])) cache.set(m[1], exportsDe(sfc(`../components/${m[1]}`).descriptor))
  return cache.get(m[1])
}
function exportsDe(descriptor) {
  const mod = { exports: {} }
  if (!descriptor.script) return mod.exports
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
const web = (rel) => fs.readFileSync(path.resolve(__dirname, '..', rel), 'utf8')
const semComentarios = (html) => html.replace(/<!--[\s\S]*?-->/g, '')
const esperar = () => new Promise((r) => setTimeout(r, 0))

// ------------------------------------------------ montar um componente de verdade
// O setup de verdade roda fora de um app (como no teste da Central: os
// `watch` ficam vivos — no SSR eles não reagem); o defineModel vira um ref
// local. O HTML sai de um app SSR que usa o MESMO estado.
function montar(rel, props, { respostas = {}, confirmar = true, filhos = {} } = {}) {
  const { descriptor, filename } = sfc(rel)
  const script = compileScript(descriptor, { id: path.basename(rel), inlineTemplate: false })
  const tpl = compileTemplate({ source: descriptor.template.content, filename, id: path.basename(rel), compilerOptions: { isTS: true, bindingMetadata: script.bindings } })
  const chamadas = []
  const perguntas = []
  const avisos = []
  const emitidos = []
  const montados = []
  const responder = async (url, opts = {}) => {
    chamadas.push({ url, metodo: opts.method || 'GET', body: opts.body })
    for (const [chave, valor] of Object.entries(respostas)) {
      if (url === chave || url.startsWith(`${chave}?`)) {
        const v = typeof valor === 'function' ? valor(url, opts) : valor
        if (v instanceof Error) throw v
        return structuredClone(v)
      }
    }
    if (/\/(lista|pastas)(?:\?|$)/.test(url)) throw Object.assign(new Error('Older API route missing'), { status: 404 })
    throw new Error(`rota inesperada: ${url}`)
  }
  const pergunta = (t) => { perguntas.push(t); return typeof confirmar === 'function' ? confirmar(t) : confirmar }
  const g = {
    ref: Vue.ref, computed: Vue.computed, reactive: Vue.reactive, watch: Vue.watch, nextTick: Vue.nextTick,
    onMounted: (fn) => montados.push(fn), onBeforeUnmount: () => {},
    useApi: () => ({ url: (v) => v, api: responder }),
    useToasts: () => ({ success: (t) => avisos.push(['ok', t]), error: (t) => avisos.push(['erro', t]), info: (t) => avisos.push(['info', t]), warning: (t) => avisos.push(['aviso', t]) }),
    confirm: pergunta,
    window: { addEventListener: () => {}, removeEventListener: () => {}, confirm: pergunta },
    crypto: { randomUUID: () => '00000000-0000-4000-8000-000000000000' },
    document: { hidden: false, addEventListener: () => {}, removeEventListener: () => {} }, setInterval: () => 1, clearInterval: () => {}, setTimeout: () => 1, clearTimeout: () => {},
  }
  const nomes = Object.keys(g)
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', ...nomes, transpile(script.content))(requerer, mod, mod.exports, ...nomes.map((n) => g[n]))
  const tmod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(tpl.code))(requerer, tmod, tmod.exports)
  const comp = mod.exports.default
  const reativo = Vue.reactive({ ...props })
  // "useModel() called without active instance": esperado aqui (vira ref local).
  const aviso = console.warn
  console.warn = () => {}
  let bruto
  try {
    bruto = comp.setup(reativo, { emit: (...a) => emitidos.push(a), expose: () => {}, attrs: {}, slots: {} })
  } finally {
    console.warn = aviso
  }
  const estado = Vue.proxyRefs(bruto)
  const filho = { ...comp, render: tmod.exports.render, setup: () => bruto }
  async function html() {
    const a = Vue.createSSRApp({ render: () => Vue.h(filho, reativo) })
    a.config.warnHandler = () => {}
    a.component('Button', { props: ['disabled'], setup: (p, { slots }) => () => Vue.h('button', { disabled: p.disabled }, slots.default?.()) })
    for (const [n, c] of Object.entries(filhos)) a.component(n, c)
    return semComentarios(await renderToString(a))
  }
  return {
    html, estado, chamadas, perguntas, avisos, emitidos, montados, reativo,
    async montar() { for (const fn of montados) await fn(); await esperar() },
  }
}

// ------------------------------------------------ os módulos (as funções puras)
const C = requerer('~/components/AtendimentoEmailCartao.vue')
const R = requerer('~/components/AtendimentoEmailResposta.vue')
const F = requerer('~/components/AtendimentoMailFilas.vue')
const K = requerer('~/components/AtendimentoMailConfigurar.vue')
const H = requerer('~/components/AtendimentoEmailChamado.vue')
const P = requerer('~/components/AtendimentoPlataforma.vue')

async function principal() {
  {
    // Os três tipos vêm da API, filtram no servidor e preservam o caso.
    const tipos = ['informativo', 'chamado', 'venda']
    assert.deepEqual(C.TIPOS_EMAIL.map((t) => t.value), tipos)
    assert.match(C.dicaTipoEmail(true), /sugerido/)
    assert.match(C.dicaTipoEmail(false), /identificado no conteúdo/)
    const linhas = tipos.map((tipo, i) => ({
      id: `tipo-${i}`, plataforma: 'ml', canal: 'email', conta: 'Loja de teste',
      comprador_nome: 'Comprador', pedido_marketplace: null, situacao: 'aberta',
      ultima_mensagem_em: '2026-10-19T12:00:00Z', ultima_mensagem_resumo: 'E-mail de teste',
      aguardando_resposta: false, prazo_resposta_em: null,
      email: { tipo_email: tipo, caso_id: tipo === 'chamado' ? 'ML-123456' : null, alias: 'loja@example.com' },
    }))
    const m = montar('../components/AtendimentoMailFilas.vue', { isAdmin: false, lojas: [], podeMexer: false }, {
      respostas: {
        '/api/atendimento/email/contagens': { aguardando: 0, chamados: 0, chamados_por_tipo: {}, por_tipo_email: { informativo: 1, chamado: 1, venda: 1 } },
        '/api/atendimento/conversas': (url) => {
          const q = new URL(url, 'https://example.com').searchParams
          return { itens: q.get('plataforma') === 'site' ? [] : linhas.filter((l) => !q.get('tipo_email') || l.email.tipo_email === q.get('tipo_email')), proximo: null }
        },
      },
    })
    await m.montar()
    m.estado.verFila('todas')
    await esperar()
    let html = await m.html()
    for (const tipo of tipos) assert.ok(html.includes(`data-tipo-email="${tipo}"`))
    assert.match(html, /pastas autorizadas a ler/)
    assert.match(html, /continuam na Central de e-mail/)
    m.estado.escolherTipoEmail('chamado')
    await esperar()
    assert.ok(m.chamadas.at(-1).url.includes('tipo_email=chamado'))
    html = await m.html()
    assert.match(html, /data-conversa-caso-email[^>]*>Chamado ML-123456/)
    assert.doesNotMatch(html, /data-conversa-email="tipo-0"|data-conversa-email="tipo-2"/)
    m.estado.escolherTipoEmail('chamado')
    await esperar()
    assert.equal(new URL(m.chamadas.at(-1).url, 'https://example.com').searchParams.get('tipo_email'), null)
    assert.equal(m.chamadas.some((c) => c.metodo !== 'GET'), false)
    const B = requerer('~/components/AtendimentoEmailConversaBarra.vue')
    for (const linha of linhas) assert.equal(B.podeVincular(linha, true), false, 'informativo/venda/caso identificado não exige pedido')
    assert.equal(B.podeVincular({ ...linhas[1], email: { tipo_email: 'chamado', caso_id: null } }, true), true)
  }
  // ------------------------------------------------ o contrato com o backend
  const rotas = api('routers/atendimento_email.py')
  const rotasConfig = api('routers/mail_atendimento.py')
  const rotasCentral = api('routers/mail.py')
  const constantes = api('services/mail_atendimento/constantes.py')
  const responder = api('services/mail_atendimento/responder.py')
  const caixa = api('services/mail_atendimento/caixa.py')
  const central = api('services/mail_central.py')
  const enviar = api('services/atendimento/enviar.py')
  const ponte = api('services/mail_atendimento/ponte.py')
  const schemas = api('schemas/atendimento.py')
  {
    // As rotas que a tela chama existem com o método certo.
    for (const [metodo, caminho] of [
      ['get', '/filas'], ['get', '/emails'], ['get', '/emails/{message_id}'], ['post', '/emails/{message_id}/loja'],
      ['post', '/emails/{message_id}/vincular'], ['post', '/emails/{message_id}/ignorar'], ['post', '/emails/{message_id}/reprocessar'],
      ['post', '/reprocessar'], ['get', '/conversas/{conversa_id}/emails'], ['get', '/anexos/{attachment_id}'],
      ['get', '/conversas/{conversa_id}/previa'], ['post', '/conversas/{conversa_id}/agrupar'],
      ['post', '/conversas/{conversa_id}/nao-agrupar'], ['get', '/envios'],
      ['post', '/envios/{job_id}/resolver'], ['get', '/saude'],
    ]) assert.ok(rotas.includes(`@router.${metodo}("${caminho}")`), `rota ${metodo} ${caminho}`)
    assert.match(rotas, /prefix="\/api\/atendimento\/email"/)
    assert.match(rotasConfig, /@router\.get\("\/mailboxes\/\{mailbox_id\}\/settings"\)/)
    assert.match(rotasConfig, /@router\.patch\("\/mailboxes\/\{mailbox_id\}\/settings"\)/)
    assert.match(rotasCentral, /@router\.patch\("\/mailboxes\/\{mailbox_id\}"\)/)
    assert.match(rotasCentral, /@router\.post\("\/outbox\/\{job_id\}\/resolve"\)/)
    assert.match(api('schemas/mail.py'), /class OutboxResolve\(StrictModel\):[\s\S]*?saiu: bool/)
    assert.match(api('schemas/mail.py'), /class MailboxPatch\(StrictModel\):[\s\S]*?aliases: list\[EmailStr\] \| None/)

    // As filas da tela = as do backend (+ as de CONVERSA de e-mail, 13/10/2026,
    // e o "revisar envio" das contagens).
    const nomes = Object.fromEntries([...constantes.matchAll(/^(FILA_[A-Z_]+) = "([a-z_]+)"$/gm)].map((m) => [m[1], m[2]]))
    const filas = ((constantes.match(/^FILAS = \(([^)]*)\)$/m) || [])[1] || '').split(',').map((x) => nomes[x.trim()]).filter(Boolean)
    const deConversa = F.FILAS.map((f) => f.value).filter((v) => F.FILAS_DE_CONVERSA.has(v))
    assert.deepEqual(deConversa, ['chamados', 'aguardando', 'todas'])
    assert.deepEqual(F.FILAS.map((f) => f.value).filter((v) => !F.FILAS_DE_CONVERSA.has(v)), [...filas, 'revisar_envio'])
    assert.deepEqual(F.FILAS.map((f) => f.label).slice(0, 5), ['Sem loja', 'Sem vínculo', 'Chamados dos sites', 'Falta responder', 'Todas as conversas'])
    // Quem só lê vê só as de conversa; quem mexe, todas.
    assert.deepEqual(F.filasDaPessoa(false).map((f) => f.value), ['chamados', 'aguardando', 'todas'])
    assert.equal(F.filasDaPessoa(true), F.FILAS)
    // As de conversa: a lista da Caixa SÓ com o e-mail (`caixa=email`), no escopo.
    assert.equal(F.urlDaFila('chamados'), '/api/atendimento/conversas?caixa=email&plataforma=site&filtro=todas&limite=50')
    assert.equal(F.urlDaFila('chamados', 'X', 'SAC'), '/api/atendimento/conversas?caixa=email&plataforma=site&filtro=todas&tipo_chamado=sac&limite=50&antes_de=X')
    assert.equal(F.urlDaFila('chamados', null, 'outro'), '/api/atendimento/conversas?caixa=email&plataforma=site&filtro=todas&limite=50', 'tipo desconhecido: todos')
    assert.equal(F.urlDaFila('aguardando', 'prazo:|c1'), '/api/atendimento/conversas?caixa=email&filtro=aguardando&limite=50&antes_de=prazo%3A%7Cc1')
    // A busca (nome, pedido, QUALQUER protocolo — como na Caixa até 13/10/2026).
    assert.equal(F.urlDaFila('chamados', null, '', ' US-26-0302 '), '/api/atendimento/conversas?caixa=email&plataforma=site&filtro=todas&q=US-26-0302&limite=50')
    assert.equal(F.urlDaFila('sem_loja', null, null, 'x'), '/api/atendimento/email/emails?fila=sem_loja', 'as filas de e-mail não buscam')
    // "Todas as conversas" (o histórico que a Caixa achava): as abertas ou, no chip, as fechadas — com a busca.
    assert.equal(F.urlDaFila('todas'), '/api/atendimento/conversas?caixa=email&filtro=todas&limite=50')
    assert.equal(F.urlDaFila('todas', 'X', 'sac', ' Maria ', true), '/api/atendimento/conversas?caixa=email&filtro=fechadas&q=Maria&limite=50&antes_de=X', 'o tipo do chamado é só dos chamados')
    assert.equal(F.urlDaFila('aguardando', null, null, null, true), '/api/atendimento/conversas?caixa=email&filtro=aguardando&limite=50', 'o "fechadas" é só de Todas')
    assert.equal(F.vazioDaConversa('todas', '', false), 'Nenhuma conversa de e-mail aberta.')
    assert.equal(F.vazioDaConversa('todas', '', true), 'Nenhuma conversa de e-mail fechada.')
    assert.equal(F.vazioDaConversa('todas', 'x', true), 'Nada encontrado para essa busca.')
    assert.equal(F.vazioDaConversa('chamados', '', false), 'Nenhum chamado aberto dos sites.')
    assert.equal(F.vazioDaConversa('aguardando', '', false), 'Nenhum e-mail esperando resposta.')
    // Os números: as filas de e-mail + as de conversa (GET /email/contagens).
    assert.deepEqual(F.juntarContagens({ sem_loja: 2 }, { aguardando: 4, chamados: 3, chamados_por_tipo: { sac: 3 } }), { sem_loja: 2, chamados: 3, aguardando: 4 })
    assert.deepEqual(F.juntarContagens(null, null), {})
    assert.ok(rotas.includes('@router.get("/contagens")'))
    const contagensPy = rotas.slice(rotas.indexOf('async def contagens('), rotas.indexOf('# ── Na conversa (quem vê)'))
    assert.match(contagensPy, /\.where\(separa_email\.conversa_de_email_sql\(\), administracao\.filtro_conversa\(user\)\)/)
    for (const campo of ['aguardando', 'chamados', 'chamados_por_tipo']) assert.ok(contagensPy.includes(`"${campo}":`), `contagens: ${campo}`)
    assert.ok(!rotas.includes('(f"{_PREFIXO}/contagens")'), 'as contagens das conversas: quem vê (não é leitura só de quem mexe)')
    assert.match(rotas, /saida\["revisar_envio"\] = /)
    assert.equal(F.urlDaFila('revisar_envio'), '/api/atendimento/email/envios?estado=revisar')
    // "revisar" = o incerto E o lease vencido (o Mac pegou e sumiu): a mesma regra nas contagens.
    assert.match(rotas, /if estado == "revisar":\s+q = q\.where\(_a_conferir\(\)\)/)
    // A contagem da fila "revisar envio" = a lista (só a resposta de CONVERSA: o
    // job da caixa crua da empresa também tem ligação, `caixa_empresa`).
    assert.match(rotas, /\.where\(\s*MailOutboxMeta\.origem == "conversa",\s*_a_conferir\(\),\s*administracao\.filtro_mensagem\(user, MailOutbox\.message_id\),\s*\)\s+\)\s+or 0\s+\)\s+return saida/)
    assert.equal(F.urlDaFila('sem_loja'), '/api/atendimento/email/emails?fila=sem_loja')
    assert.equal(F.urlDaFila('resumo', '2026-10-08T10:00:00+00:00'), '/api/atendimento/email/emails?fila=resumo&antes_de=2026-10-08T10%3A00%3A00%2B00%3A00')

    // O "não responder" confirmável e as travas gerais: os códigos do backend.
    assert.match(responder, new RegExp(`^RECUSA_NAO_RESPONDE = "${R.CODIGO_NAO_RESPONDE}"$`, 'm'))
    assert.match(responder, new RegExp(`^RECUSA_JA_RESPONDIDO = "${R.CODIGO_JA_RESPONDIDO}"$`, 'm'))
    assert.match(responder, new RegExp(`^RECUSA_PARA_FORA_DO_CHAMADO = "${R.CODIGO_PARA_FORA_DO_CHAMADO}"$`, 'm'))
    assert.match(responder, /CONFIRMAVEIS = frozenset\(\s*\{RECUSA_NAO_RESPONDE, RECUSA_JA_RESPONDIDO, RECUSA_PARA_FORA_DO_CHAMADO\}\s*\)/)
    assert.deepEqual([...R.CODIGOS_CONFIRMAVEIS].sort(), [R.CODIGO_JA_RESPONDIDO, R.CODIGO_NAO_RESPONDE, R.CODIGO_PARA_FORA_DO_CHAMADO].sort())
    for (const c of R.TRAVAS_GERAIS_DO_EMAIL) assert.match(enviar, new RegExp(`^RECUSA_[A-Z_]+ = "${c}"$`, 'm'), `trava geral ${c}`)
    assert.match(schemas, /class ResponderIn\(BaseModel\):[\s\S]*?confirmar_nao_responde: bool = False\s+mail_message_id: UUID \| None = None/)
    assert.match(schemas, /class MensagemOut\(BaseModel\):[\s\S]*?email: dict\[str, Any\] \| None = None/)
    // A prévia: os campos que a tela lê são os que a rota devolve.
    const previa = rotas.slice(rotas.indexOf('async def previa('), rotas.indexOf('class AgruparIn'))
    for (const campo of ['mail_message_id', 'de', 'para', 'formulario', 'assunto', 'assinatura', 'citacao_cabeca', 'citacao', 'modo', 'pode_mexer', 'bloqueios', 'avisos', 'abrir_no_tuta']) {
      assert.ok(previa.includes(`"${campo}":`), `prévia: ${campo}`)
    }
    assert.match(previa, /\{"codigo": b\.codigo, "texto": b\.texto, "confirmavel": b\.confirmavel\}/)
    // O status do job (a fila da Central) tem nome na tela; a mensagem guarda `mail_envio`.
    const status = Object.keys(JSON.parse(`{${((constantes.match(/^STATUS_DO_JOB = \{([\s\S]*?)\}/m) || [])[1] || '').replace(/,\s*$/, '')}}`))
    assert.deepEqual(Object.keys(C.STATUS_DA_RESPOSTA).sort(), status.sort())
    assert.match(api('routers/atendimento.py'), /for chave in \("mail", "mail_envio"\):[\s\S]*?"tipo": "recebido" if chave == "mail" else "resposta"/)
    assert.match(ponte, /^CHAVE = "mail"$/m)
    // O cartão: os campos que a tela lê.
    const cartao = rotas.slice(rotas.indexOf('async def _cartao('), rotas.indexOf('# ── As filas'))
    for (const campo of ['id', 'estado', 'recebido_em', 'pasta', 'finalidade', 'so_historico', 'alias', 'loja', 'sem_integracao', 'motivo_texto', 'sugestoes', 'suspeito', 'suspeito_motivos', 'conversa_id', 'pedidos_citados', 'protocolo', 'tipo_caixa_rotulo', 'alertas', 'codigo_mascarado', 'abrir_no_tuta', 'de', 'de_nome', 'reply_to', 'assunto', 'texto', 'links_removidos', 'anexos']) {
      assert.ok(cartao.includes(`"${campo}"`), `cartão: ${campo}`)
    }
    // A configuração: a visão da rota = o tipo da tela; o PATCH aceita o que a tela manda.
    const visao = caixa.slice(caixa.indexOf('def visao('), caixa.indexOf('def exige_mexer('))
    for (const campo of ['visibilidade', 'ponte_ligada', 'ponte_desde', 'ponte_so_aliases_de_loja', 'remetente_estrito', 'envio_modo', 'destinatarios_teste', 'teto_hora', 'teto_dia', 'teto_conta_hora', 'envio_pausado_ate', 'envio_pausa_motivo']) {
      assert.ok(visao.includes(`"${campo}":`), `visão: ${campo}`)
    }
    const patch = api('schemas/mail_atendimento.py')
    for (const campo of ['visibilidade', 'ponte_ligada', 'ponte_so_aliases_de_loja', 'remetente_estrito', 'envio_modo', 'destinatarios_teste']) {
      assert.match(patch, new RegExp(`^    ${campo}: `, 'm'), `PATCH: ${campo}`)
    }
    for (const codigo of Object.keys(K.ERROS_CONFIG)) assert.ok(caixa.includes(`"${codigo}"`) || rotasCentral.includes(`"${codigo}"`), `código ${codigo}`)
    // Os códigos novos da Central têm frase na tela dela (AtendimentoMail.knownErrors:
    // a recusa do envio e o porquê do job que falhou).
    const mail = web('components/AtendimentoMail.vue')
    for (const codigo of ['no_receiving_alias', 'sender_not_receiving_alias', 'job_not_uncertain', 'main_address_not_allowed']) assert.ok(central.includes(`"${codigo}"`), codigo)
    for (const codigo of ['sending_paused', 'sending_disabled', 'test_mode_recipient', 'hourly_limit', 'daily_limit', 'account_hourly_limit', 'atendimento_permission_required', 'main_address_not_allowed', 'visibilidade_mudou']) assert.ok(caixa.includes(`"${codigo}"`), codigo)
    for (const codigo of ['no_receiving_alias', 'sender_not_receiving_alias', 'atendimento_permission_required', 'test_mode_recipient', 'sending_paused', 'sending_disabled', 'hourly_limit', 'daily_limit', 'account_hourly_limit', 'job_not_uncertain', 'job_not_found', 'main_address_not_allowed', 'visibilidade_mudou', 'queued_timeout']) {
      assert.match(mail, new RegExp(`\\n  ${codigo}: '[^']+',`), `frase de ${codigo}`)
    }
    // O job que falhou mostra o porquê (o principal da caixa empresa, a caixa que virou privada).
    assert.match(mail, /<p v-if="job\.status === 'failed' && jobFailure\(job\.error_code\)" class="text-xs text-red-700">\{\{ jobFailure\(job\.error_code\) \}\}<\/p>/)
    // A página passa quem mexe, quem vê, as lojas e o "abrir a conversa" — que
    // abre a conversa de e-mail NA aba E-mail (13/10/2026), nunca na Caixa.
    const paginaTpl = sfc('../pages/atendimento.vue').descriptor.template.content
    assert.match(paginaTpl, /<LazyAtendimentoMail\s+v-show="!emailConversa"\s+:is-admin="isAdmin"\s+:can-operate="mexe"\s+:can-view="canSugerir"\s+:stores="resumo\?\.lojas \|\| \[\]"\s+:waiting="emailEsperando"\s+:waiting-ready="emailContagensProntas"\s+:conversation-counts="emailContagens"\s+:refresh-conversation-counts="carregarContagensEmail"\s+:paused="!!emailConversa"\s+:refresh-tick="emailTique"\s+v-model:remembered-section="emailSecao"\s+@open-conversation="abrirConversaDoEmail"\s+\/>/)
    // O número da aba E-mail (o e-mail saiu da Caixa Humano): o que falta responder nele.
    assert.match(paginaTpl, /v-if="a\.value === 'mail' && emailEsperando"[\s\S]*?data-marca-email\s*>\{\{ emailEsperando > 99 \? '99\+' : emailEsperando \}\}<\/span>/)
    assert.match(paginaTpl, /<template v-if="aba === 'mail'">\s+<div v-if="emailConversa"[^>]*data-conversa-de-email>\s+<AtendimentoEmailConversaBarra/)
    assert.match(paginaTpl, /<AtendimentoEmailConversaBarra\s+:conversa="emailConversaLinha"\s+:pode-mexer="mexe"\s+:com-chat="!!chatDeOrigem"\s+@voltar="fecharConversaDoEmail"\s+@voltar-ao-chat="voltarAoChat"\s+@vinculado="aoVincularConversaDoEmail"\s+\/>/)
    assert.match(paginaTpl, /<AtendimentoConversa\s+:key="`\$\{emailConversa\}:\$\{emailConversaVersao\}`"\s+:conversa-id="emailConversa"[\s\S]*?:ativa="aba === 'mail'"\s+@mudou="aoMudarConversaDoEmail"[\s\S]*?@abrir-conversa="abrirConversaDoEmail"/)
  }

  // ------------------------------------------------ a página: a conversa de e-mail na aba E-mail (13/10/2026)
  {
    const pagina = sfc('../pages/atendimento.vue').descriptor.scriptSetup.content
    const js = trecho(pagina, '// O registro das Automáticas abre a conversa na Caixa.', '// A Caixa ocupa a altura da janela')
      + '\n' + trecho(pagina, 'function aoMudarConversa(c: ConversaResumo) {', '// ─── respostas prontas')
      + '\n' + trecho(pagina, 'function abrirDoChat(id: string) {', '\nfunction abrirAba(')
      + '\nreturn { abrirConversaNaCaixa, abrirConversaDoEmail, fecharConversaDoEmail, voltarAoChat, aoMudarConversaDoEmail, aoVincularConversaDoEmail, aoMudarConversa, abrirDoChat }'
    const novo = () => {
      const st = { aba: Vue.ref('mail'), selecionada: Vue.ref(null), emailConversa: Vue.ref(null), emailConversaLinha: Vue.ref(null), emailConversaVersao: Vue.ref(0), chatDeOrigem: Vue.ref(null), itens: Vue.ref([]), emHumano: Vue.ref(false) }
      st.contagensLidas = 0
      st.listasRelidas = 0
      const f = new Function('aba', 'selecionada', 'emailConversa', 'emailConversaLinha', 'emailConversaVersao', 'chatDeOrigem', 'itens', 'emHumano', 'selecionar', 'carregarResumo', 'carregarContagensEmail', 'atualizarLista', `const linhasAtualizadas = new Map(); let versaoDasLinhas = 0; const naCaixa = {value:true}; const carregando = {value:false}; const carregandoMais = {value:false}; let atualizandoLista = false; let linkDoChat = null\n${js}`)(
        st.aba, st.selecionada, st.emailConversa, st.emailConversaLinha, st.emailConversaVersao, st.chatDeOrigem, st.itens, st.emHumano, (id) => { st.selecionada.value = id }, () => {}, () => { st.contagensLidas++ }, () => { st.listasRelidas++ },
      )
      return { st, f }
    }
    // Das Filas/Caixas: abre na aba E-mail.
    let { st, f } = novo()
    st.aba.value = 'caixa'
    f.abrirConversaDoEmail('c-email')
    assert.deepEqual([st.aba.value, st.emailConversa.value, st.selecionada.value], ['mail', 'c-email', null])
    // A linha que a conversa manda: a de e-mail fica (a barra lê), a de CHAT vai para a Caixa.
    f.aoMudarConversaDoEmail({ id: 'c-email', email: { protocolo: null }, aguardando_resposta: true, situacao: 'aberta' })
    assert.equal(st.emailConversaLinha.value.id, 'c-email')
    assert.equal(st.contagensLidas, 0, 'a primeira linha: o número já é o da aba')
    f.aoMudarConversaDoEmail({ id: 'c-email', email: { protocolo: null }, aguardando_resposta: false, situacao: 'respondida' })
    assert.equal(st.contagensLidas, 1, 'respondeu: o número da aba E-mail na hora')
    f.aoMudarConversaDoEmail({ id: 'c-email', email: { protocolo: null }, aguardando_resposta: false, situacao: 'respondida' })
    assert.equal(st.contagensLidas, 1, 'nada mudou na fila: não relê')
    f.abrirConversaDoEmail('c-chat')
    assert.equal(st.emailConversaLinha.value, null, 'outra conversa: a linha de antes sai')
    f.aoMudarConversaDoEmail({ id: 'c-chat', email: null })
    assert.deepEqual([st.aba.value, st.selecionada.value, st.emailConversa.value], ['caixa', 'c-chat', null], 'o chat nunca na aba E-mail')
    // A de e-mail que chega à Caixa pelo id (o link antigo): vem para a aba E-mail.
    ;({ st, f } = novo())
    st.aba.value = 'caixa'
    st.selecionada.value = 'c-email'
    f.aoMudarConversa({ id: 'c-email', email: { protocolo: 'US-26-0001' } })
    assert.deepEqual([st.aba.value, st.selecionada.value, st.emailConversa.value], ['mail', null, 'c-email'])
    assert.equal(st.chatDeOrigem.value, null, 'pelo id: sem "voltar ao chat"')
    // Aberta de dentro do chat (o "abrir na aba E-mail" do painel): o chat fica
    // aberto na Caixa e a barra volta a ele; "voltar aos e-mails" esquece o chat.
    ;({ st, f } = novo())
    st.aba.value = 'caixa'
    st.selecionada.value = 'c-chat'
    f.abrirDoChat('c-email')
    assert.equal(st.selecionada.value, 'c-email')
    f.aoMudarConversa({ id: 'c-email', email: { protocolo: null } })
    assert.deepEqual([st.aba.value, st.selecionada.value, st.emailConversa.value], ['mail', 'c-chat', 'c-email'])
    assert.deepEqual(st.chatDeOrigem.value, { id: 'c-chat', aba: 'caixa' })
    // Vinculou e as mensagens foram para a conversa de e-mail do pedido: o chat de origem fica.
    f.aoVincularConversaDoEmail('c-pedido')
    assert.deepEqual([st.emailConversa.value, st.chatDeOrigem.value], ['c-pedido', { id: 'c-chat', aba: 'caixa' }])
    f.voltarAoChat()
    assert.deepEqual([st.aba.value, st.selecionada.value, st.emailConversa.value, st.chatDeOrigem.value], ['caixa', 'c-chat', null, null])
    // Da Caixa Humano: volta para ela.
    st.aba.value = 'humano'
    f.abrirDoChat('c-email')
    f.aoMudarConversa({ id: 'c-email', email: { protocolo: null } })
    f.voltarAoChat()
    assert.deepEqual([st.aba.value, st.selecionada.value], ['humano', 'c-chat'])
    // "voltar aos e-mails": a conversa de e-mail fecha e o chat de origem sai da barra.
    f.abrirDoChat('c-email')
    f.aoMudarConversa({ id: 'c-email', email: { protocolo: null } })
    f.fecharConversaDoEmail()
    assert.deepEqual([st.aba.value, st.emailConversa.value, st.chatDeOrigem.value, st.selecionada.value], ['mail', null, null, 'c-chat'])
    f.voltarAoChat()
    assert.equal(st.aba.value, 'mail', 'sem chat de origem, o "voltar ao chat" não faz nada')
    // Das Filas (sem chat): sem "voltar ao chat", mesmo depois de uma vinda do chat.
    f.abrirConversaDoEmail('c-outra')
    assert.equal(st.chatDeOrigem.value, null)
    // A resposta da linha da Caixa (sem `email`) segue como sempre.
    st.aba.value = 'caixa'
    st.itens.value = [{ id: 'c1', aguardando_resposta: true }]
    st.selecionada.value = 'c1'
    f.aoMudarConversa({ id: 'c1', aguardando_resposta: false })
    assert.equal(st.itens.value[0].aguardando_resposta, true, 'aguarda a lista confirmar; callback não mescla retratos')
    assert.equal(st.listasRelidas, 1)
    // Vinculou: na mesma conversa relê (a chave muda); na do pedido (outra), abre ela.
    ;({ st, f } = novo())
    f.abrirConversaDoEmail('c-email')
    f.aoVincularConversaDoEmail('c-email')
    assert.equal(st.emailConversaVersao.value, 1)
    f.aoVincularConversaDoEmail('c-pedido')
    assert.equal(st.emailConversa.value, 'c-pedido')
    const lidas = st.contagensLidas
    f.fecharConversaDoEmail()
    assert.deepEqual([st.emailConversa.value, st.emailConversaLinha.value], [null, null])
    assert.equal(st.contagensLidas, lidas + 1, 'voltou aos e-mails: relê o número')
    // O número: GET /email/contagens no carregar e no polling (qualquer aba).
    assert.match(pagina, /const emailEsperando = computed\(\(\) => emailContagens\.value\?\.aguardando \|\| 0\)/)
    assert.match(pagina, /const recebidas = await api<[^>]+>\('\/api\/atendimento\/email\/contagens'\)/)
    // Quem não vê o /atendimento não pergunta (a rota recusaria); a seção escolhida na visita fica na página.
    assert.match(pagina, /async function carregarContagensEmail\(\) \{\n(?:\s*\/\/[^\n]*\n)*\s+if \(!canSugerir\.value\) return\n/)
    assert.match(pagina, /const emailSecao = ref<'' \| 'mailboxes' \| 'queues' \| 'admin'>\(''\)/)
    assert.match(pagina, /const polling = usePollingVisivel\(async \(\) => \{\s+\/\/[^\n]*\n\s+void carregarContagensEmail\(\)\n\s+\/\/[^\n]*\n\s+emailTique\.value\+\+\n/)
    assert.match(pagina, /const pedidos: Promise<unknown>\[\] = \[carregarResumo\(\), carregarContagensEmail\(\)\]/)
    assert.match(pagina, /if \(naCaixa\.value\) pedidos\.push\(carregarLista\(\)\)/)
    // O link: `?tab=mail&conversa=<id>` abre a conversa de e-mail.
    assert.match(pagina, /const selecionada = ref<string \| null>\(aba\.value === 'mail' \? null : conversaQuery\)/)
    assert.match(pagina, /const emailConversa = ref<string \| null>\(aba\.value === 'mail' \? conversaQuery : null\)/)
    assert.match(pagina, /watch\(\[aba, selecionada, emailConversa\], sincronizarUrl\)/)
  }

  // ------------------------------------------------ a barra da conversa de e-mail (vincular ao pedido)
  {
    const B = requerer('~/components/AtendimentoEmailConversaBarra.vue')
    const emailDaLinha = { protocolo: null, tipo: null, tipo_rotulo: null, marca: null, alias: '21max@tuta.com', destaque: false }
    assert.equal(B.podeVincular({ plataforma: 'shopee', pedido_marketplace: null, email: emailDaLinha }, true), true)
    assert.equal(B.podeVincular({ plataforma: 'shopee', pedido_marketplace: null, email: emailDaLinha }, false), false, 'quem só lê não vincula')
    assert.equal(B.podeVincular({ plataforma: 'shopee', pedido_marketplace: '123', email: emailDaLinha }, true), false, 'já tem pedido')
    assert.equal(B.podeVincular({ plataforma: 'site', pedido_marketplace: null, email: emailDaLinha }, true), false, 'o chamado do site não tem pedido')
    assert.equal(B.podeVincular({ plataforma: 'shopee', pedido_marketplace: null, email: null }, true), false, 'não é conversa de e-mail')
    assert.equal(B.urlVincularConversa('c 1'), '/api/atendimento/email/conversas/c%201/vincular')
    assert.ok(rotas.includes('@router.post("/conversas/{conversa_id}/vincular")'))
    const vincularPy = rotas.slice(rotas.indexOf('async def vincular_conversa('), rotas.indexOf('async def _vincular('))
    assert.match(vincularPy, /_mexe\(user\)/)
    assert.match(vincularPy, /return await _vincular\(session, meta, conversa, body, user, scope\)/)
    const conversaEmail = { id: 'c1', plataforma: 'shopee', pedido_marketplace: null, email: emailDaLinha }
    const b = montar('../components/AtendimentoEmailConversaBarra.vue', { conversa: conversaEmail, podeMexer: true }, {
      respostas: { '/api/atendimento/email/conversas/c1/vincular': { conversa_id: 'c9', pedido: '2000001' } },
    })
    let html = await b.html()
    assert.match(html, /voltar aos e-mails/)
    assert.match(html, /data-vincular-conversa-email/)
    b.estado.pedido = ' 2000001 '
    await b.estado.vincular()
    const post = b.chamadas.find((c) => c.metodo === 'POST')
    assert.deepEqual([post.url, post.body], ['/api/atendimento/email/conversas/c1/vincular', { pedido: '2000001' }])
    assert.deepEqual(b.emitidos.at(-1), ['vinculado', 'c9'])
    assert.equal(b.estado.pedido, '')
    assert.doesNotMatch(html, /data-voltar-chat/, 'das Filas: sem "voltar ao chat"')
    const leitor = montar('../components/AtendimentoEmailConversaBarra.vue', { conversa: conversaEmail, podeMexer: false })
    html = await leitor.html()
    assert.doesNotMatch(html, /data-vincular-conversa-email/, 'quem só lê: só o voltar')
    // Aberta de dentro do chat: o "voltar ao chat" (a página volta ao chat de origem).
    const doChat = montar('../components/AtendimentoEmailConversaBarra.vue', { conversa: conversaEmail, podeMexer: false, comChat: true })
    html = await doChat.html()
    assert.match(html, /data-voltar-chat[^>]*>[\s\S]*?voltar ao chat\s*<\/button>/)
    assert.match(sfc('../components/AtendimentoEmailConversaBarra.vue').descriptor.template.content, /data-voltar-chat @click="emit\('voltarAoChat'\)"/)
  }

  // ------------------------------------------------ regras puras: o cartão
  {
    assert.deepEqual(C.rotuloDaPasta({ pasta: 'problema ml', finalidade: 'problema' }), { texto: 'problema ml', destaque: true, soHistorico: false })
    assert.deepEqual(C.rotuloDaPasta({ pasta: 'vendas shopee', finalidade: 'vendas' }), { texto: 'vendas shopee', destaque: false, soHistorico: true })
    assert.equal(C.rotuloDaPasta({ pasta: '', finalidade: null }).texto, 'sem pasta')
    assert.equal(C.destinoDaLoja('16tr@tuta.com', 'JLAS2'), '16tr@tuta.com → JLAS2')
    assert.equal(C.destinoDaLoja('16tr@tuta.com', null), '16tr@tuta.com')
    assert.equal(C.destinoDaLoja(null, null), 'sem endereço da caixa')
    assert.equal(C.remetenteLegivel({ de: 'a@b.com', de_nome: 'Ana' }), 'Ana <a@b.com>')
    assert.equal(C.remetenteLegivel({ de: 'a@b.com', de_nome: 'A@B.com' }), 'a@b.com')
    assert.equal(C.tamanhoLegivel(512), '512 B')
    assert.equal(C.tamanhoLegivel(2048), '2 KB')
    assert.equal(C.tamanhoLegivel(3 * 1024 * 1024 + 100000), '3,1 MB')
    assert.equal(C.urlDoAnexo('a b'), '/api/atendimento/email/anexos/a%20b')
    assert.equal(C.resumoDaResposta({ tipo: 'resposta', de: 'sac@uranyx.com.br', para: 'cli@x.com', modo: 'teste', status: 'uncertain' }), 'sac@uranyx.com.br → cli@x.com · modo de teste · pode ter saído — confira no Tuta')
    assert.equal(C.resumoDaResposta({ tipo: 'recebido' }), '')
    assert.deepEqual(C.avisosDeProtecao({ codigo_mascarado: true, links_removidos: 2 }), ['O código de verificação deste e-mail foi mascarado (••••).', '2 links de acesso (login, senha, confirmação) foram tirados do texto.'])
    assert.deepEqual(C.avisosDeProtecao({}), [])
    const linhas = [
      { tipo: 'dia' },
      { tipo: 'msg', m: { email: { tipo: 'recebido', message_id: 'e1' } }, de: null },
      { tipo: 'msg', m: { email: { tipo: 'recebido', message_id: 'e2' } }, de: null },
      { tipo: 'msg', m: { email: { tipo: 'resposta', message_id: 'e2' } }, de: null },
      { tipo: 'msg', m: { email: { tipo: 'recebido', message_id: 'e3' } }, de: 'conv-email' },
      { tipo: 'msg', m: { email: null }, de: null },
    ]
    assert.deepEqual(C.conversasComEmail(linhas, 'aberta'), { aberta: 2, 'conv-email': 1 }, 'só os recebidos, por conversa')
    assert.equal(C.urlDosCartoes('c 1'), '/api/atendimento/email/conversas/c%201/emails')
  }

  // ------------------------------------------------ regras puras: a prévia e as travas
  {
    const previa = (bloqueios) => ({ mail_message_id: 'e1', de: 'a@tuta.com', para: 'b@x.com', formulario: false, assunto: 'Re: oi', assinatura: '', citacao_cabeca: '', citacao: '', modo: 'caixa', pode_mexer: true, bloqueios, avisos: [], abrir_no_tuta: null })
    const nr = { codigo: 'remetente_nao_responde', texto: 'Não responder.', confirmavel: true }
    const sus = { codigo: 'remetente_suspeito', texto: 'Suspeito.', confirmavel: false }
    assert.equal(R.travaDaPrevia(null, false), null, 'sem prévia: vale o envio da conversa')
    assert.equal(R.travaDaPrevia(previa([]), false), '', 'sem trava: pode')
    assert.equal(R.travaDaPrevia(previa([nr]), false), 'Não responder. Para mandar mesmo assim, marque "enviar mesmo assim" acima.')
    assert.equal(R.travaDaPrevia(previa([nr]), true), '', 'confirmou: passa')
    assert.equal(R.travaDaPrevia(previa([nr, sus]), true), 'Suspeito.', 'a confirmação não passa por cima da outra trava')
    assert.deepEqual(R.travasQueImpedem(previa([sus, nr]), true).map((b) => b.codigo), ['remetente_suspeito'])
    assert.equal(R.confirmavelDa(previa([sus, nr]))?.codigo, 'remetente_nao_responde')
    assert.equal(R.confirmavelDa(previa([sus])), null)
    // A caixinha diz O QUE a pessoa confirma.
    const jr = { codigo: 'ja_respondido_pela_caixa', texto: 'Já respondido.', confirmavel: true }
    assert.match(R.rotuloDaConfirmacao(previa([nr])), /não responder/)
    assert.match(R.rotuloDaConfirmacao(previa([jr])), /caixa da Central não basta/)
    assert.match(R.rotuloDaConfirmacao(previa([nr, jr])), /avisos acima/)
    // O chamado do site: a resposta para quem não é a cliente do chamado pede confirmação.
    const fora = { codigo: 'para_fora_do_chamado', texto: 'A resposta iria para x@y.com, que não é o e-mail da cliente deste chamado.', confirmavel: true }
    assert.match(R.rotuloDaConfirmacao(previa([fora])), /mesma cliente do chamado/)
    assert.match(R.travaDaPrevia(previa([fora]), false), /não é o e-mail da cliente[\s\S]*enviar mesmo assim/)
    assert.equal(R.travaDaPrevia(previa([fora]), true), '', 'confirmou: passa')
    assert.equal(R.travaDaPrevia(previa([jr, nr]), true), '', 'a mesma confirmação passa as duas')
    assert.equal(R.urlDaPrevia('c1', null), '/api/atendimento/email/conversas/c1/previa')
    assert.equal(R.urlDaPrevia('c1', 'e 1'), '/api/atendimento/email/conversas/c1/previa?mail_message_id=e%201')
    assert.deepEqual(R.corpoDoEmail(null, false), {})
    assert.deepEqual(R.corpoDoEmail('e1', true), { mail_message_id: 'e1', confirmar_nao_responde: true })
    assert.equal(R.ehEmailDaPonte({ canal: 'email', email_da_ponte: true }, [{ email: { tipo: 'recebido' } }]), true)
    assert.equal(R.ehEmailDaPonte({ canal: 'email', email_da_ponte: true }, [{ email: null }]), false, 'o e-mail do Tuta de antes da ponte continua sem envio')
    assert.equal(R.ehEmailDaPonte({ canal: 'chat', email_da_ponte: true }, [{ email: { tipo: 'recebido' } }]), false, 'o aviso por e-mail na conversa da API não vira conversa de e-mail')
    // A conversa da Amazon que veio pelo GMAIL (canal email, fora da ponte) com
    // uma mensagem da ponte dentro (crítica pré-subida de 08/10): nunca é da ponte.
    assert.equal(R.ehEmailDaPonte({ canal: 'email', email_da_ponte: false }, [{ email: { tipo: 'recebido' } }]), false, 'a conversa da Amazon pelo Gmail não responde pela fila da Central')
    assert.equal(R.ehEmailDaPonte({ canal: 'email' }, [{ email: { tipo: 'recebido' } }]), false, 'sem o campo da API, não é da ponte')
    // O limite da resposta de e-mail = o do backend (não o do canal da plataforma).
    assert.match(constantes, new RegExp(`^RESPOSTA_MAX_CARACTERES = ${String(R.LIMITE_RESPOSTA_EMAIL).replace(/\B(?=(\d{3})+$)/g, '_')}$`, 'm'))
    // O selo "modo de teste" já diz o aviso de modo de teste.
    assert.deepEqual(R.avisosVisiveis({ modo: 'teste', avisos: ['Modo de teste: só os endereços de teste desta caixa recebem.', 'O e-mail tinha código.'] }), ['O e-mail tinha código.'])
    assert.deepEqual(R.avisosVisiveis({ modo: 'real', avisos: ['Modo de teste: x'] }), ['Modo de teste: x'])
    assert.match(responder, /p\.avisos\.append\("Modo de teste: só os endereços de teste desta caixa recebem\."\)/)
    assert.equal(R.rotuloDoModo('teste'), 'modo de teste: só os endereços de teste da caixa recebem')
    assert.equal(R.rotuloDoModo('caixa'), '')
  }

  // ------------------------------------------------ regras puras: filas, configuração, chamado
  {
    const e = { sugestoes: [{ store_info_id: 's1', integration_id: 'i1', plataforma: 'ml', nome: 'A' }, { store_info_id: 's2', integration_id: null, plataforma: 'temu', nome: 'B' }, { store_info_id: null, marca_id: 'm1', marca_slug: 'charlots-park', nome: 'Charlots Park', plataforma: 'site', sugestao: true }, { store_info_id: null, nome: 'lixo', plataforma: 'x' }], pedidos_citados: [{ pedido: '1', existe: true }, { pedido: '2', existe: false }, { pedido: '1', existe: true }] }
    // A ficha SEM integração também se escolhe (o e-mail entra na conversa dela); a marca também.
    assert.deepEqual(F.sugestoesUteis(e).map((s) => s.store_info_id || s.marca_id), ['s1', 's2', 'm1'])
    assert.deepEqual(F.corpoDaSugestao(e.sugestoes[1]), { store_info_id: 's2' })
    assert.deepEqual(F.corpoDaSugestao(e.sugestoes[2]), { marca_id: 'm1' })
    assert.equal(F.rotuloDaSugestao(e.sugestoes[1], (p) => p.toUpperCase()), 'TEMU B (sem integração)')
    assert.equal(F.rotuloDaSugestao(e.sugestoes[2], (p) => p), 'site Charlots Park (sugestão)')
    assert.match(rotas, /class LojaIn\(BaseModel\):[\s\S]*?store_info_id: UUID \| None = None\s+integration_id: UUID \| None = None\s+marca_id: UUID \| None = None/)
    assert.deepEqual(F.pedidosQueExistem(e), ['1'])
    const lojas = [{ integration_id: 'a', plataforma: 'shopee', conta: 'A' }, { integration_id: null, plataforma: 'ml', conta: 'B' }, { integration_id: 'c', plataforma: 'ml', conta: 'C' }]
    assert.deepEqual(F.lojasParaEscolher(lojas, 'ml').map((l) => l.conta), ['C', 'A'])
    assert.deepEqual(F.acoesDaFila('sem_loja', false), { escolherLoja: true, vincular: false, ignorar: true, reprocessar: true, reprocessarFila: false })
    assert.equal(F.acoesDaFila('sem_loja', true).reprocessarFila, true, 'reprocessar a fila inteira: só admin (a rota confere)')
    assert.deepEqual(F.acoesDaFila('suspeito', true), { escolherLoja: false, vincular: false, ignorar: false, reprocessar: false, reprocessarFila: false }, 'suspeito: só ler')
    assert.equal(F.acoesDaFila('sem_vinculo', false).vincular, true)
    assert.match(F.perguntaResolver(false), /NÃO saiu[\s\S]*duas vezes/)
    // As ações que a rota aceita por fila (as mesmas da tela).
    assert.match(rotas, /def ignorar[\s\S]*?estados=\(ESTADO_SEM_LOJA, ESTADO_RESUMO, ESTADO_ERRO\)/)
    assert.match(rotas, /def reprocessar_um[\s\S]*?estados=\(ESTADO_SEM_LOJA, ESTADO_ERRO\)/)
    assert.match(rotas, /def reprocessar_fila[\s\S]*?_mexe\(user\)\s+_admin\(user\)/)

    assert.deepEqual(K.listaDeEnderecos(' A@x.com\nb@x.com; a@x.com,, '), ['a@x.com', 'b@x.com'])
    assert.deepEqual(K.aliasesDoTexto('mia30@tuta.com\nGOSLIN@tuta.com', 'goslin@tuta.com'), ['goslin@tuta.com', 'mia30@tuta.com'], 'o principal sempre, primeiro, sem repetir')
    const atual = { visibilidade: 'privada', ponte_ligada: false, ponte_so_aliases_de_loja: true, remetente_estrito: false, envio_modo: 'teste', destinatarios_teste: ['t@x.com'] }
    const form = K.formDaConfig(atual)
    assert.deepEqual(K.mudancasDaConfig(atual, form), {}, 'nada mudou: nada vai')
    assert.deepEqual(K.mudancasDaConfig(atual, { ...form, ponte_ligada: true, ponte_so_aliases_de_loja: false }), { ponte_ligada: true }, 'privada com ponte: "só aliases de loja" fica (a rota recusaria o contrário)')
    assert.deepEqual(K.mudancasDaConfig(atual, { ...form, visibilidade: 'empresa', ponte_ligada: true, ponte_so_aliases_de_loja: false }), { visibilidade: 'empresa', ponte_ligada: true, ponte_so_aliases_de_loja: false })
    assert.deepEqual(K.mudancasDaConfig(atual, { ...form, destinatarios: 'T@x.com\n' }), {}, 'a mesma lista (minúsculo, sem espaço)')
    assert.deepEqual(K.mudancasDaConfig(atual, { ...form, destinatarios: 't@x.com\nu@x.com', remetente_estrito: true, envio_modo: 'real' }), { remetente_estrito: true, envio_modo: 'real', destinatarios_teste: ['t@x.com', 'u@x.com'] })
    assert.equal(K.soAliasesObrigatorio({ visibilidade: 'privada', ponte_ligada: true }), true)
    assert.equal(K.soAliasesObrigatorio({ visibilidade: 'empresa', ponte_ligada: true }), false)
    assert.equal(K.confirmacoesDaConfig({ remetente_estrito: true }, 'G').length, 0, 'remetente estrito não pergunta')
    assert.equal(K.confirmacoesDaConfig({ ponte_ligada: true, visibilidade: 'empresa', envio_modo: 'real', ponte_so_aliases_de_loja: false }, 'Geral').length, 4)
    assert.equal(K.confirmacoesDaConfig({ ponte_ligada: false }, 'G').length, 0, 'desligar não pergunta')
    // Passar para empresa diz o que o dono perde e que o envio começa em teste.
    assert.match(K.confirmacoesDaConfig({ visibilidade: 'empresa' }, 'Goslin')[0], /se o dono não cuida, perde isso[\s\S]*MODO TESTE/)
    assert.match(K.ERROS_CONFIG.so_o_dono_da_caixa, /só o DONO/)
    assert.deepEqual(K.aliasesRemovidos(['a@x.com', 'B@x.com', 'c@x.com'], ['a@x.com', 'b@x.com']), ['c@x.com'])
    // As regras puras da tela = as da rota.
    assert.match(caixa, /linha\.visibilidade == "privada"\s+and linha\.ponte_ligada\s+and not linha\.ponte_so_aliases_de_loja/)

    assert.equal(H.rotuloDoChamado({ protocolo: 'US-26-0014', tipo_rotulo: 'SAC', marca_nome: 'Uranyx' }), 'US-26-0014 · SAC · Uranyx')
    assert.equal(H.rotuloDoChamado({ protocolo: null, tipo_rotulo: null, marca_nome: null }), 'sem protocolo')
    assert.match(H.perguntaAgrupar({ protocolo: 'US-1' }, { protocolo: 'US-2' }), /^Agrupar o chamado US-1 no US-2\?[\s\S]*nada se apaga/)
    // Fica o MAIS ANTIGO (o servidor diz qual: `fica_este`), de qualquer lado que se clique.
    assert.match(H.perguntaAgrupar({ protocolo: 'US-1' }, { protocolo: 'US-2', fica_este: false }), /^Agrupar o chamado US-2 no US-1\?[\s\S]*Fica o mais antigo/)
    assert.match(H.perguntaAgrupar({ protocolo: 'US-2' }, { protocolo: 'US-1', fica_este: true }), /^Agrupar o chamado US-2 no US-1\?/)
    // Os dois nomes na pergunta; pelo telefone/pedido (e-mails diferentes), o aviso para conferir.
    const pTel = H.perguntaAgrupar({ protocolo: 'US-2', cliente_nome: 'Maria S.' }, { protocolo: 'US-1', cliente_nome: 'Maria Souza', fica_este: true, motivo: 'pedido', motivo_rotulo: 'mesmo nº de pedido, e-mail diferente' })
    assert.match(pTel, /^Agrupar o chamado US-2 \(Maria S\.\) no US-1 \(Maria Souza\)\?/)
    assert.match(pTel, /ATENÇÃO: o que liga os dois é só o mesmo nº de pedido, e-mail diferente\. Confira que é a MESMA pessoa/)
    assert.doesNotMatch(H.perguntaAgrupar({ protocolo: 'US-2' }, { protocolo: 'US-1', motivo: 'email' }), /ATENÇÃO/)
    assert.equal(H.fraseDasSugestoes([]), '')
    assert.match(H.fraseDasSugestoes([{ motivo: 'email' }]), /^Esta cliente tem outro chamado aberto nesta marca/)
    assert.match(H.fraseDasSugestoes([{ motivo: 'email' }, { motivo: 'telefone' }]), /^Pode ser a mesma cliente: há 2 outros chamados abertos[\s\S]*confira o nome/)
    // O servidor só sugere/agrupa por telefone ou pedido com nomes que não são de pessoas diferentes.
    const chamadosSrv = api('services/mail_atendimento/chamados.py')
    assert.match(chamadosSrv, /if enderecos\.mesmo_nome\(a\.comprador_nome, b\.comprador_nome\) is False:\s+return None\s+if ma\.get\("telefone"\)/)
    assert.match(chamadosSrv, /^SUFIXO_EMAIL_DIFERENTE = ", e-mail diferente"$/m)
    assert.match(H.perguntaNaoAgrupar({ protocolo: 'US-1' }), /^Não agrupar com US-1\?[\s\S]*fica registrado/)
    // Todos os protocolos do chamado: o principal primeiro, sem repetir.
    assert.deepEqual(H.protocolosDoChamado({ protocolo: 'US-1', protocolos: ['US-1', 'UDS-2'] }), ['US-1', 'UDS-2'])
    assert.deepEqual(H.protocolosDoChamado({ protocolo: null, protocolos: [] }), [])
    // O pedido do formulário: só sugestão, nunca o nome da outra pessoa.
    assert.equal(H.frasePedidoSugerido({ pedido_citado: null, pedido_sugerido: null }), '')
    assert.match(H.frasePedidoSugerido({ pedido_citado: '777', pedido_sugerido: { numero: '777', achado: false, pedidos: [] } }), /777 \(não achado no Bling\)/)
    assert.match(
      H.frasePedidoSugerido({ pedido_citado: '777', pedido_sugerido: { numero: '777', achado: true, pedidos: [{ numero_bling: '55001', numero_loja: '777', data: null, confere: false }] } }),
      /Bling 55001 \/ loja 777 \(OUTRO nome: confira antes de ligar\)[\s\S]*Só sugestão: nada foi ligado/,
    )
    // Os campos novos do chamado vêm do servidor (resumo, sugestão e pedido).
    const chamadosPy = api('services/mail_atendimento/chamados.py')
    for (const campo of ['protocolos', 'status', 'status_rotulo', 'criado_em', 'cliente_nome', 'telefone', 'pedido_citado', 'alertas']) assert.ok(chamadosPy.includes(`"${campo}":`), `chamado: ${campo}`)
    for (const campo of ['motivo', 'motivo_rotulo', 'fica_este']) assert.ok(chamadosPy.includes(`"${campo}":`), `outros: ${campo}`)
    assert.match(rotas, /"pedido_sugerido": await chamados\.pedido_sugerido\(session, conversa, scope\)/)
    // O alerta de OUTRO endereço no chamado: o código da faixa existe no servidor.
    assert.match(constantes, /^ALERTA_OUTRO_REMETENTE = "outro_remetente"$/m)
    // Os registros de quem fez (auditoria) no cartão: os códigos da rota.
    for (const codigo of C.REGISTROS_DE_PESSOA) assert.ok(rotas.includes(`codigo="${codigo}"`), `registro ${codigo}`)

    // A resposta de e-mail que não saiu: a frase da ponte ("mail:<código> — <frase>").
    assert.equal(P.erroEnvioLegivel('mail:resolved_not_sent — alguém conferiu no Tuta: não saiu'), 'O e-mail não saiu: alguém conferiu no Tuta: não saiu.')
    assert.match(ponte, /f"mail:\{codigo\} — \{FRASE_DA_FALHA\.get\(codigo, 'o Mac não conseguiu enviar'\)\}"/)
    assert.equal(P.erroEnvioLegivel('shopee token_http_401'), 'A plataforma recusou o acesso da loja — ela precisa ser reconectada em Integrações.', 'as outras continuam')
  }

  // ------------------------------------------------ a aba E-mail dele: seções, Configurar, Saiu/Não saiu
  {
    const caixaUm = { id: 'box1', label: 'Goslin', address: 'goslin@tuta.com', aliases: ['goslin@tuta.com', 'mia30@tuta.com'], state: 'online', send_enabled: true, can_send: true, last_sync_at: null }
    const mensagem = { id: 'm1', mailbox_id: 'box1', subject: 'Oi', from_address: 'c@x.com', from_name: 'C', to: ['mia30@tuta.com'], cc: [], reply_to: null, received_at: '2026-10-08T12:00:00Z', direction: 'inbound', folder: 'INBOX', text: 'texto', attachments: [], reply: { to: 'c@x.com', from_address: 'mia30@tuta.com', can_reply: true, send_ready: true }, outbox: [{ id: 'job1', status: 'uncertain', text: 'resp', to: 'c@x.com', from_address: 'mia30@tuta.com', created_at: '2026-10-08T12:05:00Z', error_code: 'receipt_timeout' }] }
    const filhos = {
      AtendimentoMailFilas: { props: ['isAdmin', 'lojas'], setup: () => () => Vue.h('div', { 'data-filas-stub': '' }) },
      AtendimentoMailConfigurar: { props: ['mailbox'], setup: (p) => () => Vue.h('div', { 'data-configurar-stub': p.mailbox.id }) },
    }
    // Quem não é dono nem admin, mas mexe: abre nas Filas; o dono/admin, nas Caixas.
    {
      const m = montar('../components/AtendimentoMail.vue', { isAdmin: false, canOperate: true, stores: [] }, { respostas: { '/api/mail/mailboxes': { items: [] } }, filhos })
      const html = await m.html()
      assert.equal(m.estado.section, 'queues')
      assert.match(html, /data-mail-sections/)
      assert.match(html, /data-filas-stub/)
      assert.doesNotMatch(html, /Nenhuma caixa disponível/)
      m.estado.chosenSection = 'mailboxes'
      assert.equal(m.estado.section, 'mailboxes', 'a pessoa escolhe a seção')
      assert.match(await m.html(), /Nenhuma caixa disponível/)
    }
    {
      const m = montar('../components/AtendimentoMail.vue', { isAdmin: false, canOperate: true, stores: [] }, { respostas: { '/api/mail/mailboxes': { items: [caixaUm] }, '/api/mail/mailboxes/box1/messages': { items: [], more: false } }, filhos })
      await m.html()
      await m.estado.loadMailboxes()
      assert.equal(m.estado.section, 'mailboxes', 'dono de uma caixa: abre nas Caixas')
    }
    {
      const m = montar('../components/AtendimentoMail.vue', { isAdmin: true, canOperate: true, stores: [] }, { filhos })
      await m.html()
      assert.equal(m.estado.section, 'mailboxes', 'admin: abre nas Caixas')
    }
    // Quem não mexe: nem a barra, nem as Filas (mesmo escolhendo).
    {
      const m = montar('../components/AtendimentoMail.vue', { isAdmin: false }, { filhos })
      const html = await m.html()
      m.estado.chosenSection = 'queues'
      assert.equal(m.estado.section, 'mailboxes')
      assert.doesNotMatch(html, /data-mail-sections|data-filas-stub/)
    }
    // Quem só lê o /atendimento (13/10/2026: o e-mail saiu da Caixa): as Filas
    // com as de conversa só (a Filas recebe `podeMexer` falso).
    {
      const filhosLe = { ...filhos, AtendimentoMailFilas: { props: ['isAdmin', 'lojas', 'podeMexer'], setup: (p) => () => Vue.h('div', { 'data-filas-stub': '', 'data-pode-mexer': String(p.podeMexer) }) } }
      const m = montar('../components/AtendimentoMail.vue', { isAdmin: false, canView: true, stores: [], waiting: 3 }, { respostas: { '/api/mail/mailboxes': { items: [] } }, filhos: filhosLe })
      const html = await m.html()
      assert.equal(m.estado.section, 'queues')
      assert.match(html, /data-mail-sections/)
      // O número de "Filas": os e-mails esperando resposta (a página lê /email/contagens).
      assert.match(html, />\s*Filas\s*<span[^>]*data-mail-waiting[^>]*>3<\/span>/)
      assert.match(html, /data-filas-stub data-pode-mexer="false"/)
      assert.match(html, /Os chamados dos sites, os e-mails das lojas esperando resposta e todas as conversas de e-mail\./)
    }
    // Sem e-mail esperando: "Filas" sem número.
    {
      const m = montar('../components/AtendimentoMail.vue', { isAdmin: false, canOperate: true, stores: [], waiting: 0 }, { respostas: { '/api/mail/mailboxes': { items: [] } }, filhos })
      assert.doesNotMatch(await m.html(), /data-mail-waiting/)
    }
    // Pausada (a conversa de e-mail aberta por cima, componente escondido):
    // nada se atualiza sem ninguém ver; voltou aos e-mails, atualiza na hora.
    {
      const m = montar('../components/AtendimentoMail.vue', { isAdmin: true, canOperate: true, stores: [], paused: true }, { respostas: { '/api/mail/mailboxes': { items: [caixaUm] }, '/api/mail/mailboxes/box1/messages': { items: [], more: false } }, filhos })
      await m.html()
      await m.montar()
      const antes = m.chamadas.length
      await m.estado.poll()
      m.estado.resumePolling()
      await esperar()
      assert.equal(m.chamadas.length, antes, 'pausada: nem o poll nem a volta do foco leem')
      m.reativo.paused = false
      await Vue.nextTick(); await esperar(); await esperar()
      assert.ok(m.chamadas.length > antes, 'voltou: lê na hora')
    }
    // E-mail esperando resposta ao ABRIR a aba (13/10/2026: ele saiu da Caixa
    // Humano): abre em "Filas" — admin e dono de caixa também. Decide-se ao
    // abrir (o componente monta com a aba); depois, nada tira quem está nas Caixas.
    {
      const respostas = { '/api/mail/mailboxes': { items: [caixaUm] }, '/api/mail/mailboxes/box1/messages': { items: [], more: false } }
      const m = montar('../components/AtendimentoMail.vue', { isAdmin: true, canOperate: true, stores: [], waiting: 2 }, { respostas, filhos })
      await m.html()
      await m.estado.loadMailboxes()
      assert.equal(m.estado.section, 'queues', 'admin com caixa e e-mail esperando: abre nas Filas')
      // Clicou em Caixas: fica lá, e a página guarda a escolha da visita.
      m.estado.chooseSection('mailboxes')
      assert.equal(m.estado.section, 'mailboxes')
      assert.deepEqual(m.emitidos.at(-1), ['update:rememberedSection', 'mailboxes'])
      m.reativo.waiting = 5
      assert.equal(m.estado.section, 'mailboxes', 'o número subindo não tira das Caixas')
      m.estado.chooseSection('queues')
      assert.deepEqual([m.estado.section, m.emitidos.at(-1)], ['queues', ['update:rememberedSection', 'queues']])
      // Abriu sem e-mail esperando: Caixas; o número que chega depois (o polling) não muda a seção.
      const n = montar('../components/AtendimentoMail.vue', { isAdmin: true, canOperate: true, stores: [], waiting: 0 }, { respostas, filhos })
      await n.estado.loadMailboxes()
      n.reativo.waiting = 3
      assert.equal(n.estado.section, 'mailboxes')
      assert.deepEqual(n.emitidos, [], 'sem clique, nada a guardar')
      // Reabriu a aba: a escolha da visita (a página passa) vale mais que o número.
      const c = montar('../components/AtendimentoMail.vue', { isAdmin: true, canOperate: true, stores: [], waiting: 2, rememberedSection: 'mailboxes' }, { respostas, filhos })
      assert.equal(c.estado.section, 'mailboxes')
      const f = montar('../components/AtendimentoMail.vue', { isAdmin: true, canOperate: true, stores: [], waiting: 0, rememberedSection: 'queues' }, { respostas, filhos })
      assert.equal(f.estado.section, 'queues')
      // Quem não vê as Filas: o número não leva para lá.
      const s = montar('../components/AtendimentoMail.vue', { isAdmin: false, stores: [], waiting: 4 }, { filhos })
      assert.equal(s.estado.section, 'mailboxes')
      // A Filas sabe quando a conversa de e-mail está por cima (relê na volta).
      const fonteMail = web('components/AtendimentoMail.vue')
      assert.match(fonteMail, /<AtendimentoMailFilas[\s\S]*?:pausado="!!paused"[\s\S]*?\/>/)
      assert.match(fonteMail, /@click="chooseSection\('mailboxes'\)"\s*>Caixas<\/button>/)
    }
    // Caixas: o e-mail que tem conversa de e-mail abre a conversa (na aba E-mail).
    {
      const m = montar('../components/AtendimentoMail.vue', { isAdmin: true, canOperate: true, stores: [] }, { respostas: { '/api/mail/mailboxes/box1/messages': { items: [], more: false } }, filhos })
      await m.html()
      m.estado.mailboxes = [caixaUm]
      m.estado.mailboxId = 'box1'
      await Vue.nextTick(); await esperar()
      const resumoDaLista = { id: 'm1', subject: 'Oi', from_address: 'c@x.com', from_name: 'C', received_at: '2026-10-08T12:00:00Z', direction: 'inbound', folder: 'Entrada', has_attachments: false, store: { tipo: 'loja', rotulo: 'Barbosa · ML', plataforma: 'ml' }, conversation_id: 'conv-mail' }
      assert.equal(m.estado.fromIndex({ id: 'm1', recebido_em: 'x', direcao: 'inbound', tem_anexos: false, ponte: 'gravado', conversa_id: 'conv-mail', de: null, de_nome: null, assunto: null, assunto_oculto: false, pasta: { id: 's1', nome: 'Entrada', tipo: 'entrada' }, selo: resumoDaLista.store }).conversation_id, 'conv-mail')
      m.estado.openedSummary = resumoDaLista
      m.estado.detail = structuredClone(mensagem)
      const html = await m.html()
      assert.match(html, /data-mail-open-conversation[^>]*>[\s\S]*?Abrir a conversa deste e-mail/)
      m.estado.openConversation()
      assert.deepEqual(m.emitidos.at(-1), ['openConversation', 'conv-mail'])
      m.estado.openedSummary = { ...resumoDaLista, conversation_id: null }
      assert.doesNotMatch(await m.html(), /data-mail-open-conversation/, 'sem conversa de e-mail: sem o botão')
    }
    // "Configurar": só admin (que mexe — o isAdmin da página já é admin E mexe); o Saiu/Não saiu da caixa crua.
    {
      let confirma = false
      const m = montar('../components/AtendimentoMail.vue', { isAdmin: true, canOperate: true, stores: [] }, {
        respostas: { '/api/mail/outbox/job1/resolve': { id: 'job1', status: 'failed' }, '/api/mail/messages/m1': mensagem, '/api/mail/mailboxes/box1/messages': { items: [], more: false } },
        confirmar: () => confirma,
        filhos,
      })
      await m.html()
      m.estado.mailboxes = [caixaUm]
      m.estado.mailboxId = 'box1'
      await Vue.nextTick(); await esperar()
      m.estado.detail = structuredClone(mensagem)
      let html = await m.html()
      assert.match(html, /data-mail-configure/)
      assert.doesNotMatch(html, /data-configurar-stub/)
      assert.match(html, /data-mail-resolve[\s\S]*Saiu[\s\S]*Não saiu/)
      m.estado.configuring = true
      html = await m.html()
      assert.match(html, /data-configurar-stub="box1"/)
      await m.estado.resolveJob(mensagem.outbox[0], false)
      assert.equal(m.chamadas.filter((c) => c.metodo === 'POST').length, 0, 'desistiu: nada')
      assert.match(m.perguntas[0], /NÃO saiu[\s\S]*duas vezes/)
      confirma = true
      await m.estado.resolveJob(mensagem.outbox[0], false)
      const post = m.chamadas.find((c) => c.metodo === 'POST')
      assert.deepEqual([post.url, post.body], ['/api/mail/outbox/job1/resolve', { saiu: false }])
      assert.ok(m.chamadas.some((c) => c.url === '/api/mail/messages/m1'), 'relê a mensagem depois')
      // Caixa recém-cadastrada: abre o Configurar.
      m.estado.configuring = false
      m.estado.newMailboxId = 'box9'
      await Vue.nextTick()
      assert.equal(m.estado.configuring, true)
    }
    {
      const m = montar('../components/AtendimentoMail.vue', { isAdmin: false, canOperate: true, stores: [] }, { filhos })
      await m.html()
      m.estado.chosenSection = 'mailboxes'
      m.estado.mailboxes = [caixaUm]
      m.estado.mailboxId = 'box1'
      assert.doesNotMatch(await m.html(), /data-mail-configure|Cadastrar caixa/, 'quem não é admin não configura')
    }
  }

  // ------------------------------------------------ as Filas
  {
    const itemSemLoja = {
      id: 'e1', estado: 'sem_loja', recebido_em: '2026-10-08T10:00:00Z', pasta: 'Entrada', plataforma: null, finalidade: 'entrada', destaque: false,
      alias: 'mia30@tuta.com', loja: null, integration_id: null, motivo: 'ambiguo', motivo_texto: 'o alias está em mais de uma loja', sugestoes: [
        { store_info_id: 's1', nome: 'Atlas ML', plataforma: 'ml', integration_id: 'i1', sugestao: false },
        { store_info_id: 's2', nome: 'Atlas Shopee', plataforma: 'shopee', integration_id: null, sugestao: false },
        { store_info_id: null, marca_id: 'm1', marca_slug: 'charlots-park', nome: 'Charlots Park', plataforma: 'site', integration_id: null, sugestao: true },
      ], suspeito: false, suspeito_motivos: [], conversa_id: null, pedido: null, pedidos_citados: [], protocolo: null, alertas: [], abrir_no_tuta: 'https://app.tuta.com/mail?mail=a,b',
      assunto: 'Pergunta <b>importante</b>', de: 'cliente@x.com',
    }
    const itemSemVinculo = { ...itemSemLoja, id: 'e2', estado: 'sem_vinculo', conversa_id: 'conv2', loja: 'Atlas ML', pedidos_citados: [{ pedido: '2000001', existe: true, plataforma: 'ml' }], sugestoes: [] }
    const envio = { id: 'job1', status: 'uncertain', codigo: 'receipt_timeout', de: 'mia30@tuta.com', para: 'c@x.com', conversa_id: 'conv3', mensagem_id: 'msg3', criado_em: '2026-10-08T11:00:00Z', concluido_em: null, resolucao: null, resolvido_em: null, revisar: true }
    const envioSumiu = { ...envio, id: 'job2', status: 'leased', codigo: null, lease_vencido: true }
    let confirma = true
    const m = montar('../components/AtendimentoMailFilas.vue', { isAdmin: false, lojas: [{ integration_id: 'i9', plataforma: 'ml', conta: 'Fiore' }] }, {
      confirmar: () => confirma,
      respostas: {
        '/api/atendimento/email/filas': { sem_loja: 1, sem_vinculo: 1, suspeito: 0, resumo: 0, erro: 0, revisar_envio: 1 },
        '/api/atendimento/email/saude': { caixas: [{ mailbox_id: 'b1', nome: 'Geral', visibilidade: 'empresa', estado: 'online', ponte_ligada: true, ponte_desde: '2026-10-08T10:00:00Z', envio: { ligado: true, modo: 'teste' }, envios_a_conferir: 1, pastas_sem_revisar: 0 }], lojas_sem_caixa_lida: [], lojas_sem_integracao: [{ store_info_id: 's7', nome: 'JLAS2', plataforma: 'ml', endereco: '16tr@tuta.com' }] },
        '/api/atendimento/email/emails?fila=sem_loja': { itens: [itemSemLoja], proximo: null },
        '/api/atendimento/email/emails?fila=sem_vinculo': { itens: [itemSemVinculo], proximo: null },
        '/api/atendimento/email/emails/e1/loja': { id: 'e1', estado: 'gravado' },
        '/api/atendimento/email/emails/e2/vincular': { conversa_id: 'conv2', pedido: '2000001' },
        '/api/atendimento/email/emails/e1/ignorar': { id: 'e1', estado: 'ignorado' },
        '/api/atendimento/email/emails/e1': { ...itemSemLoja, texto: 'Olá <script>x</script>', anexos: [{ id: 'an1', tamanho: 2048, filename: 'nota.pdf' }], reply_to: ['golpe@y.com'], links_removidos: 1 },
        '/api/atendimento/email/envios?estado=revisar': { itens: [envio, envioSumiu] },
        '/api/atendimento/email/envios/job1/resolver': { id: 'job1', status: 'failed', resolucao: 'nao_saiu' },
      },
    })
    await m.montar()
    assert.deepEqual(m.chamadas.map((c) => c.url).sort(), ['/api/atendimento/email/contagens', '/api/atendimento/email/emails?fila=sem_loja', '/api/atendimento/email/filas', '/api/atendimento/email/saude'])
    let html = await m.html()
    assert.match(html, /Sem loja[\s\S]*?>1</)
    assert.match(html, /Pergunta &lt;b&gt;importante&lt;\/b&gt;/, 'o assunto é texto, nunca HTML')
    assert.match(html, /data-sugestao-loja[^>]*>ML Atlas ML</)
    assert.match(html, /data-sugestao-loja[^>]*>Shopee Atlas Shopee \(sem integração\)</, 'a ficha sem integração também é 1 clique')
    assert.match(html, /data-sugestao-loja[^>]*>site Charlots Park \(sugestão\)</, 'a marca ambígua: escolher a marca')
    assert.match(html, /data-lojas-sem-integracao[^>]*>\s*1 loja\(s\) do cadastro sem integração/)
    assert.match(html, /Fiore/, 'outra loja: as lojas conectadas')
    assert.equal(m.estado.lojaEscolhida.e1, '', 'o "outra loja…" começa escolhido (com undefined o select fica em branco)')
    assert.doesNotMatch(html, /reprocessar a fila/, 'não admin: sem o reprocessar da fila')
    assert.match(html, /Geral[\s\S]*da empresa[\s\S]*Mac conectado[\s\S]*ponte ligada/)
    // Escolher a loja (1 clique).
    await m.estado.escolherLoja(itemSemLoja, { store_info_id: 's1' })
    let post = m.chamadas.filter((c) => c.metodo === 'POST').at(-1)
    assert.deepEqual([post.url, post.body], ['/api/atendimento/email/emails/e1/loja', { store_info_id: 's1' }])
    await m.estado.escolherLoja(itemSemLoja, F.corpoDaSugestao(itemSemLoja.sugestoes[2]))
    post = m.chamadas.filter((c) => c.metodo === 'POST').at(-1)
    assert.deepEqual(post.body, { marca_id: 'm1' })
    // Ignorar pede confirmação.
    confirma = false
    await m.estado.ignorar(itemSemLoja)
    assert.equal(m.chamadas.filter((c) => c.url.endsWith('/ignorar')).length, 0)
    confirma = true
    await m.estado.ignorar(itemSemLoja)
    assert.equal(m.chamadas.filter((c) => c.url.endsWith('/ignorar')).length, 1)
    // Ver o e-mail: o texto protegido como texto, o anexo só baixa, o Reply-To diferente.
    await m.estado.abrir(itemSemLoja)
    html = await m.html()
    assert.match(html, /Olá &lt;script&gt;x&lt;\/script&gt;/)
    assert.doesNotMatch(html, /<script>x/)
    assert.match(html, /href="\/api\/atendimento\/email\/anexos\/an1"/)
    assert.match(html, /Pede a resposta para outro endereço: golpe@y\.com/)
    assert.match(html, /Um link de acesso/)
    // Sem vínculo: o pedido citado que existe é 1 clique.
    m.estado.fila = 'sem_vinculo'
    await Vue.nextTick(); await esperar()
    html = await m.html()
    assert.match(html, /pedido 2000001/)
    await m.estado.vincular(itemSemVinculo, '2000001')
    post = m.chamadas.filter((c) => c.metodo === 'POST').at(-1)
    assert.deepEqual([post.url, post.body], ['/api/atendimento/email/emails/e2/vincular', { pedido: '2000001' }])
    // Revisar envio: Saiu / Não saiu, com a pergunta; nunca reenvia.
    m.estado.fila = 'revisar_envio'
    await Vue.nextTick(); await esperar()
    html = await m.html()
    assert.match(html, /data-envio="job1"[\s\S]*mia30@tuta\.com → c@x\.com[\s\S]*data-resolver-saiu[\s\S]*data-resolver-nao-saiu/)
    assert.match(html, /data-envio="job2"[^>]*><div class="font-medium">O Mac pegou esta resposta e sumiu[\s\S]*?data-resolver-nao-saiu/, 'o lease vencido também se confere')
    confirma = false
    await m.estado.resolver(envio, false)
    assert.equal(m.chamadas.filter((c) => c.url.endsWith('/resolver')).length, 0, 'desistiu: nada')
    confirma = true
    await m.estado.resolver(envio, false)
    post = m.chamadas.filter((c) => c.metodo === 'POST').at(-1)
    assert.deepEqual([post.url, post.body], ['/api/atendimento/email/envios/job1/resolver', { saiu: false }])
    assert.ok(!m.chamadas.some((c) => /\/reply|\/responder/.test(c.url)), 'nada é reenviado')
    // Abrir a conversa (a página abre na aba E-mail — 13/10/2026 — nunca na Caixa).
    assert.match(html, /abrir a conversa/)
    // Admin: o reprocessar da fila inteira, com pergunta.
    const a = montar('../components/AtendimentoMailFilas.vue', { isAdmin: true, lojas: [] }, {
      respostas: { '/api/atendimento/email/filas': {}, '/api/atendimento/email/saude': { caixas: [] }, '/api/atendimento/email/emails?fila=sem_loja': { itens: [itemSemLoja], proximo: null }, '/api/atendimento/email/reprocessar': { total: 3, resolvidos: 2 } },
    })
    await a.montar()
    assert.match(await a.html(), /reprocessar a fila/)
    await a.estado.reprocessarFila()
    assert.equal(a.perguntas.length, 1)
    assert.ok(a.avisos.some(([, t]) => t === '2 de 3 acharam a loja'))
  }

  // ------------------------------------------------ as Filas de CONVERSA de e-mail (13/10/2026)
  {
    const email = (o = {}) => ({ protocolo: null, tipo: null, tipo_rotulo: null, marca: null, alias: null, destaque: false, ...o })
    const chamado = { id: 'cv1', plataforma: 'site', canal: 'email', conta: 'Uranyx', comprador_nome: 'Maria <b>', pedido_marketplace: null, ultima_mensagem_em: '2026-10-13T09:00:00Z', ultima_mensagem_resumo: 'Quero trocar', aguardando_resposta: true, prazo_resposta_em: null, situacao: 'aberta', email: email({ protocolo: 'US-26-0007', tipo: 'sac', tipo_rotulo: 'SAC', marca: 'Uranyx', alias: 'sac@uranyx.com.br' }) }
    const esperando = { ...chamado, id: 'cv2', plataforma: 'shopee', conta: 'Barbosa', comprador_nome: 'João', email: email({ alias: '21max@tuta.com' }) }
    const contagens = { aguardando: 2, chamados: 1, chamados_por_tipo: { sac: 1, atacado: 0, duvidas: 0 } }
    const respostas = {
      '/api/atendimento/email/contagens': contagens,
      '/api/atendimento/conversas?caixa=email&plataforma=site&filtro=todas&limite=50': { itens: [chamado], proximo: null },
      '/api/atendimento/conversas?caixa=email&plataforma=site&filtro=todas&tipo_chamado=sac&limite=50': { itens: [chamado], proximo: null },
      '/api/atendimento/conversas?caixa=email&filtro=aguardando&limite=50': { itens: [chamado, esperando], proximo: 'prazo:|cv2' },
      '/api/atendimento/conversas?caixa=email&filtro=aguardando&limite=50&antes_de=prazo%3A%7Ccv2': { itens: [{ ...esperando, id: 'cv3' }], proximo: null },
      '/api/atendimento/conversas?caixa=email&plataforma=site&filtro=todas&q=US-26-0007&limite=50': { itens: [chamado], proximo: null },
      '/api/atendimento/conversas?caixa=email&filtro=todas&limite=50': { itens: [esperando], proximo: null },
      '/api/atendimento/conversas?caixa=email&filtro=fechadas&limite=50': { itens: [], proximo: null },
      '/api/atendimento/conversas?caixa=email&filtro=todas&q=US-26-0001&limite=50': { itens: [], proximo: null },
      '/api/atendimento/conversas?caixa=email&filtro=fechadas&q=US-26-0001&limite=50': { itens: [], proximo: null },
    }
    // Quem só lê: só as filas de conversa (sem as de e-mail, sem a saúde, sem pastas), abrindo nos chamados.
    const r = montar('../components/AtendimentoMailFilas.vue', { isAdmin: false, lojas: [], podeMexer: false }, { respostas })
    await r.montar()
    assert.equal(r.estado.fila, 'chamados')
    assert.deepEqual(r.chamadas.map((c) => c.url).sort(), ['/api/atendimento/conversas?caixa=email&plataforma=site&filtro=todas&limite=50', '/api/atendimento/email/contagens'])
    let html = await r.html()
    assert.doesNotMatch(html, /data-fila="sem_loja"|data-fila="pastas"|data-mail-saude/)
    assert.match(html, /data-fila="chamados"[^>]*>\s*Chamados dos sites\s*<span[^>]*>1</)
    assert.match(html, /data-fila="aguardando"[^>]*>\s*Falta responder\s*<span[^>]*>2</)
    assert.match(html, /data-conversa-email="cv1"[\s\S]*US-26-0007[\s\S]*SAC[\s\S]*Site Uranyx[\s\S]*falta responder[\s\S]*Maria &lt;b&gt;[\s\S]*Quero trocar[\s\S]*abrir a conversa[\s\S]*para sac@uranyx\.com\.br/)
    assert.match(html, /data-chips-chamado[\s\S]*SAC[\s\S]*>1</)
    // O chip do tipo filtra; clicar de novo, todos.
    r.estado.escolherTipo('sac')
    await esperar()
    assert.ok(r.chamadas.some((c) => c.url.endsWith('tipo_chamado=sac&limite=50')))
    assert.equal(r.estado.tipoChamado, 'sac')
    r.estado.escolherTipo('sac')
    assert.equal(r.estado.tipoChamado, '')
    // A busca: só no Enter/"buscar"; a lista recomeça.
    r.estado.buscaTexto = ' US-26-0007 '
    r.estado.buscar()
    await esperar()
    assert.equal(r.estado.busca, 'US-26-0007')
    assert.ok(r.chamadas.some((c) => c.url === '/api/atendimento/conversas?caixa=email&plataforma=site&filtro=todas&q=US-26-0007&limite=50'))
    assert.match(await r.html(), /data-busca-conversa-email[\s\S]*limpar/)
    r.estado.buscaTexto = ''
    r.estado.buscar()
    // Falta responder, com a página seguinte; "abrir a conversa" avisa a aba E-mail.
    r.estado.fila = 'aguardando'
    await Vue.nextTick(); await esperar()
    assert.deepEqual(r.estado.conversas.map((c) => c.id), ['cv1', 'cv2'])
    await r.estado.carregar(true)
    assert.deepEqual(r.estado.conversas.map((c) => c.id), ['cv1', 'cv2', 'cv3'])
    html = await r.html()
    assert.match(html, /data-conversa-email="cv2"[\s\S]*Shopee Barbosa[\s\S]*João/)
    // Todas as conversas (o histórico): as abertas; no chip, as fechadas.
    r.estado.fila = 'todas'
    await Vue.nextTick(); await esperar()
    assert.deepEqual(r.estado.conversas.map((c) => c.id), ['cv2'])
    html = await r.html()
    assert.match(html, /data-fila="todas"[^>]*>\s*Todas as conversas\s*<\/button>/, 'sem número')
    assert.match(html, /data-chips-situacao[\s\S]*aria-pressed="true"[^>]*data-situacao="abertas"/)
    assert.doesNotMatch(html, /data-chips-chamado/, 'o tipo do chamado é só dos chamados')
    r.estado.verFechadas(true)
    await esperar()
    assert.ok(r.chamadas.some((c) => c.url === '/api/atendimento/conversas?caixa=email&filtro=fechadas&limite=50'))
    html = await r.html()
    assert.match(html, /Nenhuma conversa de e-mail fechada\./)
    assert.doesNotMatch(html, /data-buscar-na-outra/, 'sem busca: nada a oferecer')
    // A busca que não acha numa metade oferece a outra (1 clique), com a mesma busca.
    r.estado.verFechadas(false)
    await esperar()
    r.estado.buscaTexto = 'US-26-0001'
    r.estado.buscar()
    await esperar()
    assert.match(await r.html(), /Nada encontrado para essa busca\.\s*<button[^>]*data-buscar-na-outra[^>]*>procurar nas fechadas<\/button>/)
    r.estado.verFechadas(true)
    await esperar()
    assert.ok(r.chamadas.some((c) => c.url === '/api/atendimento/conversas?caixa=email&filtro=fechadas&q=US-26-0001&limite=50'))
    assert.match(await r.html(), /data-buscar-na-outra[^>]*>procurar nas abertas<\/button>/)
    r.estado.buscaTexto = ''
    r.estado.buscar()
    await esperar()
    r.estado.fila = 'aguardando'
    await Vue.nextTick(); await esperar()
    assert.ok(!r.chamadas.some((c) => /\/email\/(filas|saude|emails)/.test(c.url)), 'quem só lê não pede as filas de e-mail')
    // A conversa de e-mail aberta por cima (a página pausa) e a volta: a fila e os números de novo.
    const antesDaVolta = r.chamadas.length
    r.reativo.pausado = true
    await Vue.nextTick(); await esperar()
    assert.equal(r.chamadas.length, antesDaVolta, 'pausar não lê nada')
    r.reativo.pausado = false
    await Vue.nextTick(); await esperar()
    assert.deepEqual(r.chamadas.slice(antesDaVolta).map((c) => c.url).sort(), ['/api/atendimento/conversas?caixa=email&filtro=aguardando&limite=50', '/api/atendimento/email/contagens'])
    // O polling de 30 s da página (o `tique`): chegou um e-mail esperando
    // resposta — o número da aba sobe e o chip e a lista de "Falta responder"
    // acompanham (o e-mail saiu da Caixa, que se relê sozinha).
    {
      let servidor = { ...contagens, aguardando: 1 }
      let lista = [esperando]
      let maisPaginas = false
      const URL_AG = '/api/atendimento/conversas?caixa=email&filtro=aguardando&limite=50'
      const p = montar('../components/AtendimentoMailFilas.vue', { isAdmin: false, lojas: [], podeMexer: false, tique: 0 }, {
        respostas: {
          ...respostas,
          '/api/atendimento/email/contagens': () => servidor,
          [URL_AG]: () => (lista instanceof Error ? lista : { itens: lista, proximo: maisPaginas ? 'prazo:|cv2' : null }),
        },
      })
      await p.montar()
      p.estado.fila = 'aguardando'
      await Vue.nextTick(); await esperar()
      const chip = (h) => /data-fila="aguardando"[^>]*>\s*Falta responder\s*<span[^>]*>(\d+)</.exec(h)?.[1]
      const linhas = (h) => (h.match(/data-conversa-email="/g) || []).length
      let h = await p.html()
      assert.deepEqual([chip(h), linhas(h)], ['1', 1])
      servidor = { ...servidor, aguardando: 2 }
      lista = [esperando, { ...esperando, id: 'cv9', comprador_nome: 'Novo' }]
      let antes = p.chamadas.length
      p.reativo.tique = 1
      await Vue.nextTick(); await esperar()
      assert.deepEqual(p.chamadas.slice(antes).map((c) => c.url).sort(), [URL_AG, '/api/atendimento/email/contagens'])
      h = await p.html()
      assert.deepEqual([chip(h), linhas(h)], ['2', 2], 'o chip e a lista com o número da aba')
      // Escondida (a conversa de e-mail por cima): o tique não lê nada.
      p.reativo.pausado = true
      await Vue.nextTick(); await esperar()
      antes = p.chamadas.length
      p.reativo.tique = 2
      await Vue.nextTick(); await esperar()
      assert.equal(p.chamadas.length, antes, 'pausada: nada')
      p.reativo.pausado = false
      await Vue.nextTick(); await esperar()
      // Com o "carregar mais": só os números (reler voltaria para a 1ª página).
      maisPaginas = true
      await p.estado.carregar()
      await p.estado.carregar(true)
      assert.deepEqual(p.estado.conversas.map((c) => c.id), ['cv2', 'cv9', 'cv3'])
      antes = p.chamadas.length
      p.reativo.tique = 3
      await Vue.nextTick(); await esperar()
      assert.deepEqual(p.chamadas.slice(antes).map((c) => c.url), ['/api/atendimento/email/contagens'])
      assert.deepEqual(p.estado.conversas.map((c) => c.id), ['cv2', 'cv9', 'cv3'], 'a lista com as páginas fica')
      // A 1ª página de novo (a fila relida): o polling volta a reler a lista. A
      // falha no automático não mostra erro e não apaga a lista.
      maisPaginas = false
      await p.estado.carregar()
      lista = new Error('500')
      antes = p.chamadas.length
      p.reativo.tique = 4
      await Vue.nextTick(); await esperar()
      assert.ok(p.chamadas.slice(antes).some((c) => c.url === URL_AG))
      assert.equal(p.estado.erro, null, 'o automático que falhou não mostra erro')
      assert.deepEqual(p.estado.conversas.map((c) => c.id), ['cv2', 'cv9'])
      // A página passa o tique (o polling) pela Central até as Filas.
      assert.match(web('components/AtendimentoMail.vue'), /<AtendimentoMailFilas[\s\S]*?:tique="refreshTick"[\s\S]*?\/>/)
    }
    // Na página real, a aba e Filas compartilham a mesma resposta. Montar,
    // fazer polling ou voltar da conversa não repete a consulta da página;
    // atualizar manualmente e concluir uma ação relêem também o número da aba.
    for (const podeMexer of [false, true]) {
      let refreshes = 0
      const compartilhadas = montar('../components/AtendimentoMailFilas.vue', {
        isAdmin: podeMexer, lojas: [], podeMexer, tique: 0,
        contagensDeConversa: null,
        atualizarContagensDeConversa: async () => {
          refreshes++
          compartilhadas.reativo.contagensDeConversa = { ...contagens, aguardando: 7 }
        },
      }, {
        respostas: { ...respostas, '/api/atendimento/email/filas': { sem_loja: 3 }, '/api/atendimento/email/saude': { caixas: [] }, '/api/atendimento/email/emails?fila=sem_loja': { itens: [], proximo: null } },
      })
      const endpoint = '/api/atendimento/email/contagens'
      await compartilhadas.montar()
      assert.equal(refreshes, 0, 'montagem não repete o pedido que a página já iniciou')
      assert.equal(compartilhadas.chamadas.some((c) => c.url === endpoint), false)
      assert.equal(compartilhadas.estado.contagensConversa, null, 'resposta inicial lenta não inventa números')
      compartilhadas.reativo.contagensDeConversa = { ...contagens, por_tipo_email: { informativo: 4, chamado: 1, venda: 2 } }
      await Vue.nextTick()
      assert.equal(compartilhadas.estado.contagens.aguardando, 2)
      assert.deepEqual(compartilhadas.estado.contagensConversa.chamados_por_tipo, contagens.chamados_por_tipo)
      assert.equal(compartilhadas.estado.contagensConversa.por_tipo_email.informativo, 4)
      compartilhadas.estado.fila = 'aguardando'
      await Vue.nextTick(); await esperar()
      const antes = compartilhadas.chamadas.length
      compartilhadas.reativo.tique = 1
      await Vue.nextTick(); await esperar()
      assert.deepEqual(compartilhadas.chamadas.slice(antes).map((c) => c.url), ['/api/atendimento/conversas?caixa=email&filtro=aguardando&limite=50'], 'polling atualiza a lista sem duplicar contagens')
      compartilhadas.reativo.contagensDeConversa = { ...contagens, aguardando: 5 }
      await Vue.nextTick()
      assert.equal(compartilhadas.estado.contagens.aguardando, 5, 'a resposta tardia do polling atualiza as filas')
      compartilhadas.reativo.pausado = true
      await Vue.nextTick()
      const pausada = compartilhadas.chamadas.length
      compartilhadas.reativo.tique = 2
      await Vue.nextTick(); await esperar()
      assert.equal(compartilhadas.chamadas.length, pausada)
      compartilhadas.reativo.pausado = false
      await Vue.nextTick(); await esperar()
      assert.equal(refreshes, 0, 'voltar não duplica a atualização que a página faz ao fechar a conversa')
      await compartilhadas.estado.atualizar()
      assert.equal(refreshes, 1, 'atualização manual delega à página uma vez')
      assert.equal(compartilhadas.estado.contagens.aguardando, 7)
      if (podeMexer) {
        let acoes = 0
        await compartilhadas.estado.acao('teste', async () => { acoes++ }, 'Concluído')
        assert.equal(acoes, 1)
        assert.equal(refreshes, 2, 'ação concluída atualiza as contagens compartilhadas')
      } else {
        assert.equal(compartilhadas.chamadas.some((c) => /\/email\/(filas|saude|emails)/.test(c.url)), false, 'leitor mantém somente consultas permitidas')
      }
      assert.equal(compartilhadas.chamadas.some((c) => c.url === endpoint), false, 'nenhuma leitura local duplica o endpoint')
      assert.equal(r.estado.contagens.aguardando, 2, 'outra instância não recebe as contagens desta pessoa')
    }
    assert.match(web('pages/atendimento.vue'), /:conversation-counts="emailContagens"[\s\S]*?:refresh-conversation-counts="carregarContagensEmail"/)
    assert.match(web('components/AtendimentoMail.vue'), /:contagens-de-conversa="conversationCounts"[\s\S]*?:atualizar-contagens-de-conversa="refreshConversationCounts"/)
    // Quem mexe vê as duas no meio das outras, e as contagens juntas.
    const m = montar('../components/AtendimentoMailFilas.vue', { isAdmin: true, lojas: [] }, {
      respostas: { ...respostas, '/api/atendimento/email/filas': { sem_loja: 3 }, '/api/atendimento/email/saude': { caixas: [] }, '/api/atendimento/email/emails?fila=sem_loja': { itens: [], proximo: null } },
    })
    await m.montar()
    assert.equal(m.estado.fila, 'sem_loja')
    assert.deepEqual(m.estado.contagens, { sem_loja: 3, chamados: 1, aguardando: 2 })
    html = await m.html()
    assert.match(html, /data-fila="sem_loja"[\s\S]*data-fila="sem_vinculo"[\s\S]*data-fila="chamados"[\s\S]*data-fila="aguardando"[\s\S]*data-fila="todas"[\s\S]*data-fila="suspeito"[\s\S]*data-fila="revisar_envio"[\s\S]*data-fila="pastas"/)
  }

  // ------------------------------------------------ Configurar
  {
    const caixaG = { id: 'b1', label: 'Goslin', address: 'goslin@tuta.com', aliases: ['goslin@tuta.com', 'mia30@tuta.com'] }
    const cfg = { mailbox_id: 'b1', configurada: false, visibilidade: 'privada', ponte_ligada: false, ponte_desde: null, ponte_so_aliases_de_loja: true, remetente_estrito: false, envio_modo: 'teste', destinatarios_teste: [], teto_hora: 30, teto_dia: 300, teto_conta_hora: 100, envio_pausado_ate: null, envio_pausa_motivo: null, agente_tipo: null, updated_at: null }
    let confirma = true
    const m = montar('../components/AtendimentoMailConfigurar.vue', { mailbox: caixaG }, {
      confirmar: () => confirma,
      respostas: {
        '/api/mail/mailboxes/b1/settings': (url, opts) => (opts.method === 'PATCH' ? { ...cfg, ...opts.body, ponte_desde: opts.body.ponte_ligada ? '2026-10-08T17:00:00Z' : null } : cfg),
        '/api/mail/mailboxes/b1': (url, opts) => ({ ...caixaG, aliases: opts.body.aliases }),
      },
    })
    await m.montar()
    assert.equal(m.chamadas[0].url, '/api/mail/mailboxes/b1/settings')
    assert.equal(m.estado.aliasesTexto, 'mia30@tuta.com', 'o principal não aparece na lista (fica sempre)')
    let html = await m.html()
    assert.match(html, /Configurar Goslin/)
    assert.match(html, /30 por hora, 300 por dia/)
    // Ligar a ponte na caixa privada: "só endereços de loja" fica marcado e travado; pergunta antes.
    m.estado.form.ponte_ligada = true
    m.estado.form.ponte_so_aliases_de_loja = false
    html = await m.html()
    assert.match(html, /data-config-so-aliases[^>]*disabled|disabled[^>]*data-config-so-aliases/)
    assert.match(html, /obrigatório na caixa privada/)
    assert.deepEqual(m.estado.mudancas, { ponte_ligada: true })
    confirma = false
    await m.estado.salvarConfig()
    assert.ok(!m.chamadas.some((c) => c.metodo === 'PATCH'), 'desistiu: nada')
    confirma = true
    await m.estado.salvarConfig()
    let patch = m.chamadas.find((c) => c.metodo === 'PATCH')
    assert.deepEqual([patch.url, patch.body], ['/api/mail/mailboxes/b1/settings', { ponte_ligada: true }])
    assert.equal(m.estado.config.ponte_ligada, true)
    assert.deepEqual(m.emitidos.at(-1), ['mudou'])
    // Os endereços: pelo PATCH da Central, o principal primeiro; tirar endereço pergunta.
    m.estado.aliasesTexto = 'mia30@tuta.com\nyuki31@tuta.com'
    await m.estado.salvarAliases()
    patch = m.chamadas.filter((c) => c.metodo === 'PATCH').at(-1)
    assert.deepEqual([patch.url, patch.body], ['/api/mail/mailboxes/b1', { aliases: ['goslin@tuta.com', 'mia30@tuta.com', 'yuki31@tuta.com'] }])
    m.reativo.mailbox = { ...caixaG, aliases: ['goslin@tuta.com', 'mia30@tuta.com', 'yuki31@tuta.com'] }
    m.estado.aliasesTexto = 'yuki31@tuta.com'
    confirma = false
    const antes = m.chamadas.length
    await m.estado.salvarAliases()
    assert.equal(m.chamadas.length, antes, 'tirar endereço: desistiu, nada')
    assert.match(m.perguntas.at(-1), /Tirar da caixa: mia30@tuta\.com/)
    // Erro da rota: a frase da tela.
    const e = montar('../components/AtendimentoMailConfigurar.vue', { mailbox: caixaG }, {
      respostas: { '/api/mail/mailboxes/b1/settings': (url, opts) => (opts.method === 'PATCH' ? Object.assign(new Error('x'), { data: { detail: { code: 'atendimento_permission_required' } } }) : { ...cfg, visibilidade: 'empresa' }) },
    })
    await e.montar()
    e.estado.form.remetente_estrito = true
    await e.estado.salvarConfig()
    assert.equal(e.estado.erro, K.ERROS_CONFIG.atendimento_permission_required)
  }

  // ------------------------------------------------ a prévia da resposta
  {
    const p = { mail_message_id: 'e1', de: 'mia30@tuta.com', para: 'noreply@mercadolivre.com', formulario: false, assunto: 'Re: Pergunta', assinatura: 'Atenciosamente,\nAtlas', citacao_cabeca: 'Em 08/10/2026 10:00, ML escreveu:', citacao: 'texto', modo: 'teste', pode_mexer: true, bloqueios: [{ codigo: 'remetente_nao_responde', texto: 'Endereço não responder.', confirmavel: true }], avisos: ['Modo de teste: só os endereços de teste desta caixa recebem.'], abrir_no_tuta: 'https://app.tuta.com/mail?mail=a,b' }
    const m = montar('../components/AtendimentoEmailResposta.vue', { conversaId: 'conv1', mailMessageId: null, versao: 1 }, { respostas: { '/api/atendimento/email/conversas/conv1/previa': p } })
    await m.html()
    await esperar()
    assert.equal(m.chamadas[0].url, '/api/atendimento/email/conversas/conv1/previa')
    assert.match(m.estado.trava, /^Endereço não responder\. Para mandar mesmo assim/, 'a caixa fica travada até confirmar')
    let html = await m.html()
    assert.doesNotMatch(html, /Modo de teste: só os endereços de teste desta caixa recebem/, 'o selo já diz')
    assert.match(html, /a assinatura e a citação do e-mail/)
    assert.doesNotMatch(html, /data-email-junto/, 'a assinatura e a citação ficam recolhidas')
    m.estado.verCitacao = true
    assert.match(await m.html(), /data-email-junto>Atenciosamente,\nAtlas\n\nEm 08\/10\/2026 10:00, ML escreveu:\ntexto</)
    html = await m.html()
    assert.match(html, /data-email-de>mia30@tuta\.com/)
    assert.match(html, /data-email-para>noreply@mercadolivre\.com/)
    assert.match(html, /modo de teste/)
    assert.match(html, /data-email-nao-responde/)
    assert.match(html, /href="https:\/\/app\.tuta\.com\/mail\?mail=a,b"[^>]*>[\s\S]*Abrir no Tuta/)
    m.estado.confirmouNaoResponde = true
    await Vue.nextTick()
    assert.equal(m.estado.trava, '', 'confirmou: a caixa libera')
    // Outro e-mail escolhido: a confirmação não vale mais, a prévia é a dele.
    m.reativo.mailMessageId = 'e2'
    await Vue.nextTick(); await esperar()
    assert.equal(m.estado.confirmouNaoResponde, false)
    assert.equal(m.chamadas.at(-1).url, '/api/atendimento/email/conversas/conv1/previa?mail_message_id=e2')
    // Formulário do site: o cliente do corpo.
    const f = montar('../components/AtendimentoEmailResposta.vue', { conversaId: 'c2', mailMessageId: null, versao: 1 }, { respostas: { '/api/atendimento/email/conversas/c2/previa': { ...p, para: 'cliente@x.com', formulario: true, bloqueios: [], modo: 'real' } } })
    await f.html(); await esperar()
    assert.equal(f.estado.trava, '')
    assert.match(await f.html(), /cliente@x\.com \(o cliente do formulário do site\)/)
    // A prévia falhou: sem trava da prévia (vale o envio da conversa) e o erro na tela.
    const x = montar('../components/AtendimentoEmailResposta.vue', { conversaId: 'c3', mailMessageId: null, versao: 1 }, { respostas: { '/api/atendimento/email/conversas/c3/previa': Object.assign(new Error('x'), { data: { detail: { code: 'nao_e_conversa_de_email' } } }) } })
    await x.html(); await esperar()
    assert.equal(x.estado.trava, null)
    assert.match(await x.html(), /Esta conversa não é de e-mail da ponte\./)
  }

  // ------------------------------------------------ o cartão do e-mail
  {
    const cartao = {
      id: 'e1', estado: 'gravado', direcao: 'inbound', recebido_em: '2026-10-08T10:00:00Z', pasta: 'problema ml', plataforma: 'ml', finalidade: 'problema', finalidade_rotulo: 'problema', destaque: true, so_historico: false,
      alias: '16tr@tuta.com', loja: 'JLAS2', integration_id: 'i1', motivo: null, motivo_texto: null, sugestoes: [], suspeito: true, suspeito_motivos: ['o remetente se diz da plataforma, mas o domínio não é o oficial'],
      conversa_id: 'c1', pedido: '2000001', pedidos_citados: [], protocolo: null, tipo_caixa: null, tipo_caixa_rotulo: null, vinculado_por: 'pedido', alertas: [], codigo_mascarado: true, abrir_no_tuta: 'https://app.tuta.com/mail?mail=l,e',
      de: 'suporte@mercadolivre-seguro.com', de_nome: 'Mercado Livre', reply_to: ['golpe@y.com'], assunto: 'Sua conta', texto: 'Clique em https://golpe.example/x <img src=x onerror=alert(1)>', links_removidos: 0, anexos: [{ id: 'an1', tamanho: 10, filename: 'a.pdf' }],
    }
    const email = { tipo: 'recebido', message_id: 'e1', pasta: 'problema ml', finalidade: 'problema', alias: '16tr@tuta.com', assunto: 'Sua conta', suspeito: true, anexos: 1 }
    const m = montar('../components/AtendimentoEmailCartao.vue', { email, cartao, podeResponder: true, respondendo: false })
    let html = await m.html()
    assert.match(html, /problema ml/)
    assert.match(html, /16tr@tuta\.com → JLAS2/)
    assert.match(html, /data-email-suspeito[\s\S]*Remetente pode ser falso[\s\S]*domínio não é o oficial/)
    assert.match(html, /data-email-reply-to[\s\S]*golpe@y\.com/)
    assert.match(html, /Mercado Livre &lt;suporte@mercadolivre-seguro\.com&gt;/)
    assert.doesNotMatch(html, /data-email-responder-este/, 'suspeito: sem "responder este e-mail"')
    assert.match(html, /data-email-abrir-tuta/)
    m.estado.aberto = true
    html = await m.html()
    assert.match(html, /Clique em https:\/\/golpe\.example\/x &lt;img src=x onerror=alert\(1\)&gt;/, 'texto só: nada vira HTML nem link')
    assert.doesNotMatch(html, /href="https:\/\/golpe/)
    assert.match(html, /href="\/api\/atendimento\/email\/anexos\/an1"/)
    assert.match(html, /mascarado/)
    // Vendas: "só histórico", sem responder.
    const v = montar('../components/AtendimentoEmailCartao.vue', { email: { ...email, suspeito: false, finalidade: 'vendas', pasta: 'vendas ml' }, cartao: null, podeResponder: true })
    html = await v.html()
    assert.match(html, /data-email-historico/)
    assert.doesNotMatch(html, /data-email-responder-este/)
    // O normal: responder este e-mail emite o id do e-mail da Central.
    const n = montar('../components/AtendimentoEmailCartao.vue', { email: { ...email, suspeito: false, finalidade: 'mensagens', pasta: 'mensagens ml' }, cartao: { ...cartao, suspeito: false, suspeito_motivos: [], reply_to: [] }, podeResponder: true })
    html = await n.html()
    assert.match(html, /data-email-responder-este/)
    assert.doesNotMatch(html, /data-email-suspeito|data-email-reply-to/)
    // A loja SEM integração (a ficha): o selo, sem travar nada.
    const fi = montar('../components/AtendimentoEmailCartao.vue', { email: { ...email, suspeito: false, finalidade: 'mensagens', pasta: 'problema temu' }, cartao: { ...cartao, suspeito: false, suspeito_motivos: [], reply_to: [], loja: 'Barbosa', sem_integracao: true }, podeResponder: true })
    html = await fi.html()
    assert.match(html, /data-email-sem-integracao[^>]*>loja sem integração</)
    assert.match(html, /data-email-responder-este/)
    // O AVISO da plataforma na conversa da API (aviso_api): nunca "responder este e-mail".
    const av = montar('../components/AtendimentoEmailCartao.vue', { email: { ...email, suspeito: false, finalidade: 'mensagens', pasta: 'mensagens ml', aviso_api: true }, cartao: { ...cartao, suspeito: false, suspeito_motivos: [], reply_to: [] }, podeResponder: true })
    assert.doesNotMatch(await av.html(), /data-email-responder-este/)
    // A nossa resposta: De → Para e o status do job.
    const r = montar('../components/AtendimentoEmailCartao.vue', { email: { tipo: 'resposta', de: 'mia30@tuta.com', para: 'c@x.com', assunto: 'Re: oi', status: 'queued', modo: 'real' } })
    assert.match(await r.html(), /data-email-resposta[\s\S]*mia30@tuta\.com → c@x\.com · na fila do Mac/)
  }

  // ------------------------------------------------ o chamado do site e o agrupar
  {
    const chamado = { conversa_id: 'c1', protocolo: 'US-26-0014', marca: 'uranyx', marca_nome: 'Uranyx', tipo: 'sac', tipo_rotulo: 'SAC', situacao: 'aberta', ultima_mensagem_em: null }
    const outro = { ...chamado, conversa_id: 'c0', protocolo: 'US-26-0009' }
    let confirma = false
    const m = montar('../components/AtendimentoEmailChamado.vue', { chamado, outros: [outro], podeMexer: true }, { confirmar: () => confirma, respostas: { '/api/atendimento/email/conversas/c1/agrupar': { conversa_id: 'c0' } } })
    const html = await m.html()
    assert.match(html, /US-26-0014[\s\S]*SAC[\s\S]*Uranyx/)
    assert.match(html, /outro chamado aberto[\s\S]*US-26-0009[\s\S]*data-email-agrupar/)
    await m.estado.agrupar(outro)
    assert.equal(m.chamadas.length, 0, 'nunca sozinho: desistiu, nada')
    confirma = true
    await m.estado.agrupar(outro)
    assert.deepEqual([m.chamadas[0].url, m.chamadas[0].body], ['/api/atendimento/email/conversas/c1/agrupar', { conversa_id: 'c0' }])
    assert.deepEqual(m.emitidos.at(-1), ['agrupado', 'c0'])
    const so = montar('../components/AtendimentoEmailChamado.vue', { chamado, outros: [outro], podeMexer: false })
    const htmlSo = await so.html()
    assert.doesNotMatch(htmlSo, /data-email-agrupar|data-email-nao-agrupar/, 'quem só lê: sem agrupar nem "não agrupar"')
    assert.match(htmlSo, /data-email-ver-chamado/, 'quem só lê: "Ver chamado" fica')
    // Ver chamado: o botão emite "abrir" com o id do outro (a conversa abre).
    const tplChamado = sfc('../components/AtendimentoEmailChamado.vue').descriptor.template.content
    assert.match(tplChamado, /data-email-ver-chamado\s+@click="emit\('abrir', o\.conversa_id\)"/)
    // Não agrupar: pergunta, manda, some da faixa e avisa a conversa.
    const completo = {
      ...chamado,
      protocolos: ['US-26-0014', 'US-26-0002'],
      status: 'aguardando_cliente',
      status_rotulo: 'Aguardando cliente',
      cliente_nome: 'Maria Souza',
      telefone: '11987654321',
      alertas: [{ codigo: 'protocolo_repetido', texto: 'Protocolo repetido: outra cliente' }],
      pedido_citado: '777',
      pedido_sugerido: { numero: '777', achado: true, pedidos: [{ numero_bling: '55001', numero_loja: '777', data: null, confere: true }] },
    }
    const sug = { ...outro, motivo: 'telefone', motivo_rotulo: 'mesmo telefone, e-mail diferente', fica_este: true, cliente_nome: 'Maria S.' }
    let confirmaNao = false
    const n = montar('../components/AtendimentoEmailChamado.vue', { chamado: completo, outros: [sug], podeMexer: true }, { confirmar: () => confirmaNao, respostas: { '/api/atendimento/email/conversas/c1/nao-agrupar': { conversa_id: 'c1', outros_chamados: [] } } })
    let h = await n.html()
    assert.match(h, /data-email-protocolo[^>]*>US-26-0014<[\s\S]*data-email-protocolo[^>]*>US-26-0002</)
    assert.match(h, /data-email-chamado-status[^>]*>Aguardando cliente</)
    assert.match(h, /data-email-chamado-cliente[^>]*>\s*Cliente: Maria Souza · 11987654321/)
    assert.match(h, /data-email-pedido-sugerido[^>]*>Pedido citado no formulário: 777 — no Bling: Bling 55001 \/ loja 777 \(o nome confere\)/)
    assert.match(h, /data-email-chamado-alerta[\s\S]*Protocolo repetido: outra cliente/)
    assert.match(h, /mesmo telefone, e-mail diferente[\s\S]*data-email-ver-chamado[\s\S]*data-email-agrupar[\s\S]*data-email-nao-agrupar/)
    assert.match(h, /data-email-outro-cliente[^>]*>· Maria S\.</, 'o nome da cliente do outro chamado aparece')
    assert.match(h, /Pode ser a mesma cliente/, 'pelo telefone: "pode ser", não "esta cliente"')
    await n.estado.naoAgrupar(sug)
    assert.equal(n.chamadas.length, 0, '"não agrupar" também pergunta antes')
    confirmaNao = true
    await n.estado.naoAgrupar(sug)
    assert.deepEqual([n.chamadas[0].url, n.chamadas[0].metodo, n.chamadas[0].body], ['/api/atendimento/email/conversas/c1/nao-agrupar', 'POST', { conversa_id: 'c0' }])
    assert.deepEqual(n.emitidos.at(-1), ['recusado', 'c0'])
    assert.doesNotMatch(await n.html(), /data-email-outros-chamados/, 'a sugestão some da faixa')
    // Sem protocolo: o selo "sem protocolo".
    const sem = montar('../components/AtendimentoEmailChamado.vue', { chamado: { ...chamado, protocolo: null, protocolos: [] }, outros: [], podeMexer: true })
    assert.match(await sem.html(), />sem protocolo</)
  }

  // ------------------------------------------------ a caixa da aba E-mail (outra conversa responde)
  {
    const aba = {
      chave: 'email', rotulo: 'E-mail', total: 1,
      conversas: [{ id: 'conv-email', canal: 'email', canal_rotulo: 'E-mail', titulo: null, pedido_marketplace: '1', situacao: 'aberta', aguardando_resposta: true, ultima_mensagem_em: null, aberta: false, mensagens: 1 }],
      mensagens: [{ id: 'm1', conversa_id: 'conv-email', canal: 'email', autor: 'cliente', origem: 'plataforma', autor_nome: null, tipo: 'texto', texto: 'oi', anexos: [], enviada_em: '2026-10-08T10:00:00Z', status: 'ok', erro: null, email: { tipo: 'recebido', message_id: 'e1' } }],
      tem_mais: false, proximo: null,
      responde: { conversa_id: 'conv-email', canal: 'email', canal_rotulo: 'E-mail', pode_enviar: false, motivo: 'Endereço não responder.', codigo: 'remetente_nao_responde', limite_caracteres: 1000, modo_observacao: false, publica: false, ultima_vista_id: 'm1' },
    }
    const filhos = { AtendimentoEmailResposta: { props: ['conversaId', 'mailMessageId', 'versao'], setup: (p) => () => Vue.h('div', { 'data-previa-stub': p.conversaId, 'data-mail': p.mailMessageId || '' }) } }
    const m = montar('../components/AtendimentoAbaResposta.vue', { aba, canEdit: true, plataforma: 'ml', mailMessageId: 'e1' }, { respostas: { '/api/atendimento/conversas/conv-email/responder': { mensagem: { id: 'n1', status: 'enviando' } } }, filhos })
    let html = await m.html()
    assert.equal(m.estado.ehEmail, true)
    assert.match(html, /data-previa-stub="conv-email" data-mail="e1"/)
    assert.equal(m.estado.bloqueio, '', 'sem a prévia: o "não responder" não trava a caixa (o servidor confere)')
    assert.equal(m.estado.limite, 10000, 'o limite da resposta de e-mail, não o do canal')
    m.estado.travaEmail = 'Remetente suspeito.'
    assert.equal(m.estado.bloqueio, 'Remetente suspeito.', 'a prévia manda')
    assert.equal(m.estado.faixa, '', 'a faixa não repete o que a prévia mostra')
    assert.doesNotMatch(await m.html(), /data-bloqueio-aba/)
    m.estado.travaEmail = ''
    m.estado.confirmouNaoResponde = true
    m.estado.texto = 'Resposta'
    await m.estado.enviar()
    assert.deepEqual(m.chamadas[0].body, { texto: 'Resposta', ultima_vista_id: 'm1', mail_message_id: 'e1', confirmar_nao_responde: true })
    assert.equal(m.estado.confirmouNaoResponde, false, 'depois do envio, a confirmação não fica')
    // Trava geral (envio desligado) vale mesmo com a prévia liberando.
    const d = montar('../components/AtendimentoAbaResposta.vue', { aba: { ...aba, responde: { ...aba.responde, codigo: 'envio_desligado', motivo: 'O envio está desligado.' } }, canEdit: true, plataforma: 'ml' }, { filhos })
    await d.html()
    d.estado.travaEmail = ''
    assert.ok(d.estado.bloqueio, 'envio desligado trava')
    // Fora do e-mail: igual a antes (sem prévia, sem campos de e-mail).
    const o = montar('../components/AtendimentoAbaResposta.vue', { aba: { ...aba, chave: 'pos_venda', mensagens: aba.mensagens.map((x) => ({ ...x, email: null })), responde: { ...aba.responde, pode_enviar: true, codigo: null, motivo: null } }, canEdit: true, plataforma: 'ml' }, { respostas: { '/api/atendimento/conversas/conv-email/responder': { mensagem: null } }, filhos })
    html = await o.html()
    assert.doesNotMatch(html, /data-previa-stub/)
    o.estado.texto = 'x'
    await o.estado.enviar()
    assert.deepEqual(o.chamadas[0].body, { texto: 'x', ultima_vista_id: 'm1' })
    // A conversa de PÓS-VENDA do ML que recebeu o aviso por e-mail da plataforma
    // (a ponte grava `email` com aviso_api nela): continua chat — limite 350,
    // sem prévia de e-mail, sem campos de e-mail no corpo, "Resposta enviada".
    const pv = {
      ...aba,
      chave: 'pos_venda',
      conversas: [{ ...aba.conversas[0], id: 'conv-pv', canal: 'pos_venda', canal_rotulo: 'Pós-venda' }],
      mensagens: [{ ...aba.mensagens[0], conversa_id: 'conv-pv', canal: 'pos_venda', autor: 'sistema', email: { tipo: 'recebido', message_id: 'e7', aviso_api: true } }],
      responde: { conversa_id: 'conv-pv', canal: 'pos_venda', canal_rotulo: 'Pós-venda', pode_enviar: true, motivo: null, codigo: null, limite_caracteres: 350, modo_observacao: false, publica: false, ultima_vista_id: 'm1' },
    }
    const v = montar('../components/AtendimentoAbaResposta.vue', { aba: pv, canEdit: true, plataforma: 'ml', mailMessageId: 'e7' }, { respostas: { '/api/atendimento/conversas/conv-pv/responder': { mensagem: { id: 'n2', status: 'ok' } } }, filhos })
    html = await v.html()
    assert.equal(v.estado.ehEmail, false, 'aviso por e-mail no chat não faz do chat um e-mail')
    assert.equal(v.estado.limite, 350)
    assert.doesNotMatch(html, /data-previa-stub/)
    v.estado.texto = 'y'.repeat(400)
    assert.equal(v.estado.podeEnviar, false, 'acima do limite do chat')
    v.estado.texto = 'Oi'
    await v.estado.enviar()
    assert.deepEqual(v.chamadas[0].body, { texto: 'Oi', ultima_vista_id: 'm1' })
    assert.ok(v.avisos.some(([t, m]) => t === 'ok' && /^Resposta enviada/.test(m)), JSON.stringify(v.avisos))
  }

  // ------------------------------------------------ a conversa
  {
    const conversaSfc = sfc('../components/AtendimentoConversa.vue')
    const setup = conversaSfc.descriptor.scriptSetup.content
    const tela = conversaSfc.descriptor.template.content
    // O cartão nas mensagens (balão e linha do meio) e a prévia antes da caixa.
    assert.match(tela, /<AtendimentoEmailCartao\s+v-if="l\.m\.email && ehCartaoDeEmail\(l\.m\.email\)"[\s\S]*?:cartao="cartaoDe\(l\.m\)"[\s\S]*?:pode-responder="lado\(l\.m\) === 'cliente' && podeResponderEmail\(l\.de\)"[\s\S]*?@responder="responderEmail"/)
    assert.match(tela, /<div v-if="l\.m\.email && ehCartaoDeEmail\(l\.m\.email\)" class="w-full[^"]*"[^>]*>\s*<AtendimentoEmailCartao/)
    // Nenhum nome importado do e-mail colide com uma função da conversa (a
    // conversa já tinha `temCartao` — o do pedido/produto).
    const importados = ((setup.match(/import \{([^}]*)\} from '~\/components\/AtendimentoEmail(?:Cartao|Resposta)\.vue'/g) || []).join(',').match(/\b[A-Za-z_]\w*\b/g) || [])
      .filter((n) => !['import', 'from', 'type', 'components', 'AtendimentoEmailCartao', 'AtendimentoEmailResposta', 'vue'].includes(n))
    for (const n of importados) assert.doesNotMatch(setup, new RegExp(`^(?:async )?function ${n}\\(|^const ${n} =`, 'm'), `nome repetido na conversa: ${n}`)
    // As notas de sistema da ponte (vinculado, agrupado) levam `mail` sem e-mail: linha do meio de sempre.
    assert.equal(C.ehCartaoDeEmail({ tipo: 'recebido', agrupado_de: 'c0' }), false)
    assert.equal(C.ehCartaoDeEmail({ tipo: 'recebido', vinculado: true }), false)
    assert.equal(C.ehCartaoDeEmail({ tipo: 'recebido', message_id: 'e1' }), true)
    assert.equal(C.ehCartaoDeEmail({ tipo: 'resposta' }), true)
    assert.equal(C.ehCartaoDeEmail(null), false)
    assert.match(api('services/mail_atendimento/chamados.py'), /payload=\{ponte\.CHAVE: \{"agrupado_de": str\(origem\.id\)\}\}/)
    assert.match(rotas, /payload=\{ponte\.CHAVE: \{"vinculado": True\}\}/)
    assert.match(tela, /<AtendimentoEmailResposta\s+v-if="ehEmail && canEdit && !conversa\.somente_leitura && !respondePorOutra"\s+v-model:confirmou-nao-responde="confirmouNaoResponde"\s+v-model:trava="travaEmail"/)
    const iPrevia = tela.indexOf('<AtendimentoEmailResposta')
    const iCaixa = tela.indexOf('ref="caixa"')
    assert.ok(iPrevia > 0 && iPrevia < iCaixa, 'a prévia fica logo acima da caixa de resposta')
    assert.match(tela, /<AtendimentoEmailChamado\s+v-if="chamadoNaTela\?\.chamado"/)
    // "Ver chamado" abre a outra conversa; "Não agrupar" relê os cartões.
    assert.match(tela, /<AtendimentoEmailChamado[\s\S]*?@abrir="\(id: string\) => emit\('abrirConversa', id\)"[\s\S]*?@recusado="aoRecusarAgrupar"/)
    // A IA nunca responde e-mail: o "Sugerir resposta" nem aparece no e-mail da ponte.
    assert.match(tela, /<Button v-if="podeSugerir && !ehEmail"[^>]*@click="sugerir">/)
    // {protocolo} nas respostas prontas: o do chamado na tela.
    assert.ok(P.LACUNAS.some((l) => l.chave === 'protocolo'))
    assert.equal(P.preencherLacunas('Chamado {protocolo}', { protocolo: 'US-26-0001' }), 'Chamado US-26-0001')
    assert.deepEqual(P.lacunasAbertas('Chamado {protocolo}'), ['{protocolo}'])
    assert.match(setup, /protocolo: chamadoNaTela\.value\?\.chamado\?\.protocolo,/)
    assert.match(responder, /normalizado = preparar_texto\(preencher_protocolo\(texto_pessoa, conversa\)\)/)
    // "Tentar de novo" de uma resposta de e-mail: o MESMO e-mail (o id que ela respondia).
    const cru = (ini, fim) => setup.slice(setup.indexOf(ini), setup.indexOf(fim, setup.indexOf(ini) + 1))
    assert.match(cru('async function tentarDeNovo', '// ─── painel do pedido'), /const mailId = m\.email\?\.tipo === 'resposta' \? m\.email\.message_id : null\s+if \(mailId\) body\.mail_message_id = mailId/)
    assert.match(api('services/mail_atendimento/responder.py'), /"mail_envio": \{[\s\S]*?"message_id": str\(p\.message\.id\)/)
    // O aviso depois do Enviar no e-mail: a fila do Mac, não "saindo para a plataforma".
    assert.match(cru('function concluirEnvio', '// A conversa recarregada'), /else if \(m\.email\?\.tipo === 'resposta'\) toasts\.info\(`\$\{AVISO_EMAIL_NA_FILA\.titulo\}\$\{onde\}`, AVISO_EMAIL_NA_FILA\.texto\)/)
    assert.match(R.AVISO_EMAIL_NA_FILA.titulo, /fila do e-mail/)
    // O limite do e-mail também vem do servidor (não os 1000 do canal).
    assert.equal(R.LIMITE_RESPOSTA_EMAIL, Number((constantes.match(/^RESPOSTA_MAX_CARACTERES = ([\d_]+)$/m) || [])[1].replace(/_/g, '')))
    assert.match(api('routers/atendimento.py'), /limite_caracteres=_limite_da_resposta\(conversa\)/)
    assert.ok(tela.indexOf('<AtendimentoEmailChamado') < tela.indexOf('<!-- mensagens -->'))
    assert.match(tela, /<AtendimentoAbaResposta[\s\S]*?:mail-message-id="emailResponder"[\s\S]*?@responder-mais-novo="emailResponder = null"/)
    assert.match(tela, /@click="enviar\(erroEnvio\.naoResponde \? \{ confirmarNaoResponde: true \} : \{ confirmar: true \}\)"/)
    // O texto do e-mail que pode ser golpe vai sem link clicável.
    assert.match(tela, /v-for="\(p, i\) in partesDaMensagem\(l\.m\)"/)
    // O limite do e-mail e a faixa que não repete a trava da prévia.
    assert.match(setup, /const limite = computed\(\(\) => \(ehEmail\.value \? LIMITE_RESPOSTA_EMAIL : 0\) \|\|/)
    assert.match(setup, /const faixaDoBloqueio = computed\(\(\) => \(ehEmail\.value && travaEmail\.value && bloqueioEnvio\.value === travaEmail\.value \? '' : bloqueioEnvio\.value\)\)/)
    assert.match(tela, /<div v-if="faixaDoBloqueio"[^>]*>\s*<Lock[^>]*\/>\s*<span class="flex-1">\{\{ faixaDoBloqueio \}\}<\/span>/)
    const partes = new Function('partesComLink', `${trecho(setup, 'function partesDaMensagem', '// Anexo: cada plataforma')}\nreturn partesDaMensagem`)(() => [{ t: 'url', v: 'https://x' }])
    assert.deepEqual(partes({ texto: 'https://x', email: { suspeito: true } }), [{ t: 'txt', v: 'https://x' }])
    assert.deepEqual(partes({ texto: 'https://x', email: null }), [{ t: 'url', v: 'https://x' }])
    // O corpo do envio leva o e-mail (o trecho que roda no teste da avaliação também).
    assert.match(trecho(setup, 'async function enviar(', 'function aoTeclar'), /Object\.assign\(body, extrasDoEmail\(opcoes\?\.confirmarNaoResponde === true\)\)/)
    assert.match(trecho(setup, 'async function enviar(', 'function aoTeclar'), /naoResponde: CODIGOS_CONFIRMAVEIS\.has\(code\)/)
    assert.match(trecho(setup, 'function limparCaixa', '\n}'), /emailResponder\.value = null;?\s+confirmouNaoResponde\.value = false/)

    // A trava da caixa no e-mail (o trecho de verdade com dublês).
    function bloqueio({ envio, travaEmail, email = true, canEdit = true }) {
      const js = trecho(setup, 'const bloqueioEnvio = computed', 'const podeDigitar') + '\nreturn bloqueioEnvio'
      return new Function('computed', 'detalhe', 'props', 'sellerCenterDe', 'motivoSellerCenter', 'ERROS', 'AVISO_SO_LEITURA', 'avaliacaoDaConversa', 'ehEmail', 'TRAVAS_GERAIS_DO_EMAIL', 'motivoLegivel', 'travaEmail', 'CODIGOS_CONFIRMAVEIS', 'bloqueioLegivel', 'semConta', js)(
        Vue.computed, Vue.ref({ conversa: { plataforma: 'ml', somente_leitura: false, situacao: 'aberta' }, envio }), { canEdit, flags: null }, () => false, () => '', P.ERROS, P.AVISO_SO_LEITURA, Vue.ref(null), Vue.ref(email),
        R.TRAVAS_GERAIS_DO_EMAIL, P.motivoLegivel, Vue.ref(travaEmail), R.CODIGOS_CONFIRMAVEIS, (x) => x, Vue.ref(false),
      ).value
    }
    assert.equal(bloqueio({ envio: { pode_enviar: false, codigo: 'remetente_nao_responde', motivo: 'NR' }, travaEmail: null }), '', 'sem prévia: o "não responder" não trava (confirmável)')
    assert.equal(bloqueio({ envio: { pode_enviar: false, codigo: 'ja_respondido_pela_caixa', motivo: 'JR' }, travaEmail: null }), '', 'sem prévia: o "já respondido pela caixa" também é confirmável')
    assert.equal(bloqueio({ envio: { pode_enviar: false, codigo: 'remetente_nao_responde', motivo: 'NR' }, travaEmail: 'NR — marque' }), 'NR — marque', 'com a prévia: ela manda')
    assert.equal(bloqueio({ envio: { pode_enviar: true }, travaEmail: 'Suspeito.' }), 'Suspeito.', 'o e-mail escolhido pode ter trava que o mais novo não tem')
    assert.equal(bloqueio({ envio: { pode_enviar: false, codigo: 'envio_desligado', motivo: 'O envio pelo DaVinci está desligado.' }, travaEmail: '' }), P.motivoLegivel('O envio pelo DaVinci está desligado.'), 'trava geral vale sempre')
    assert.equal(bloqueio({ envio: { pode_enviar: false, codigo: 'caixa_sem_envio', motivo: 'Caixa sem envio.' }, travaEmail: null }), 'Caixa sem envio.', 'sem prévia: o motivo do envio')
    assert.equal(bloqueio({ envio: { pode_enviar: false, codigo: 'caixa_sem_envio', motivo: 'Caixa sem envio.' }, travaEmail: '', canEdit: false }), P.AVISO_SO_LEITURA, 'quem só lê: só leitura')
    assert.equal(bloqueio({ envio: { pode_enviar: false, codigo: 'canal_em_observacao', motivo: null }, travaEmail: null, email: false }), P.ERROS.canal_em_observacao, 'fora do e-mail: como antes')

    // Os cartões e o "responder este e-mail" (o trecho de verdade com dublês).
    const js = trecho(setup, '// ─── e-mail (a ponte da Central de e-mail', '// Por que não dá para responder') + '\nreturn { ehEmail, emailResponder, confirmouNaoResponde, travaEmail, responderEmail, extrasDoEmail, podeResponderEmail, cartoesEmail, carregarCartoesEmail, cartaoDe, chamadoNaTela, aoAgruparChamado, emailsNaTela }'
    const chamadas = []
    const emitidos = []
    const conversa = Vue.ref({ id: 'aberta', canal: 'email', email_da_ponte: true })
    const detalhe = Vue.ref({ mensagens: [{ email: { tipo: 'recebido', message_id: 'e1' } }] })
    const linhas = Vue.ref([{ tipo: 'msg', m: { email: { tipo: 'recebido', message_id: 'e1' } }, de: null }])
    const respondePorOutra = Vue.ref(false)
    const abaAtual = Vue.ref(null)
    const abaAtiva = Vue.ref('email')
    const respostas = {
      '/api/atendimento/email/conversas/aberta/emails': { emails: [{ id: 'e1', alias: 'a@tuta.com' }], chamado: { conversa_id: 'aberta', protocolo: 'US-1' }, outros_chamados: [] },
      '/api/atendimento/email/conversas/outra/emails': { emails: [{ id: 'e9' }], chamado: null, outros_chamados: [] },
    }
    const x = new Function('computed', 'ref', 'watch', 'ehEmailDaPonte', 'conversa', 'detalhe', 'modoCaixa', 'nextTick', 'caixa', 'corpoDoEmail', 'props', 'respondePorOutra', 'abaAtual', 'abaAtiva', 'api', 'urlDosCartoes', 'conversasComEmail', 'linhas', 'emit', 'carregarAbas', 'carregar', js)(
      Vue.computed, Vue.ref, Vue.watch, R.ehEmailDaPonte, conversa, detalhe, Vue.ref('nota'), Vue.nextTick, Vue.ref(null), R.corpoDoEmail, { canEdit: true, conversaId: 'aberta' }, respondePorOutra, abaAtual, abaAtiva,
      async (url) => { chamadas.push(url); return structuredClone(respostas[url]) }, C.urlDosCartoes, C.conversasComEmail, linhas, (...a) => emitidos.push(a), () => {}, () => {},
    )
    assert.equal(x.ehEmail.value, true)
    assert.deepEqual(x.extrasDoEmail(false), {})
    x.responderEmail('e1')
    x.confirmouNaoResponde.value = true
    assert.deepEqual(x.extrasDoEmail(false), { mail_message_id: 'e1', confirmar_nao_responde: true })
    abaAtiva.value = 'pos_venda'
    await Vue.nextTick()
    assert.equal(x.emailResponder.value, null, 'trocou de aba: o e-mail escolhido sai')
    // Lidos uma vez por conversa (relidos só quando muda o número de e-mails).
    await Vue.nextTick(); await esperar()
    assert.deepEqual(chamadas, ['/api/atendimento/email/conversas/aberta/emails'])
    assert.deepEqual(x.cartaoDe({ email: { tipo: 'recebido', message_id: 'e1' } }), { id: 'e1', alias: 'a@tuta.com' })
    assert.equal(x.cartaoDe({ email: { tipo: 'resposta', message_id: 'e1' } }), null, 'a nossa resposta não usa o cartão do respondido')
    assert.equal(x.chamadoNaTela.value?.chamado?.protocolo, 'US-1')
    linhas.value = [...linhas.value]
    await Vue.nextTick(); await esperar()
    assert.equal(chamadas.length, 1, 'mesma contagem: não relê')
    linhas.value = [...linhas.value, { tipo: 'msg', m: { email: { tipo: 'recebido', message_id: 'e9' } }, de: 'outra' }]
    await Vue.nextTick(); await esperar()
    assert.deepEqual(chamadas.slice(1), ['/api/atendimento/email/conversas/outra/emails'], 'a outra conversa da aba')
    // "responder este e-mail": da aberta pela caixa de sempre; da outra, só se ela responde nesta aba.
    assert.equal(x.podeResponderEmail(null), true)
    assert.equal(x.podeResponderEmail('outra'), false)
    respondePorOutra.value = true
    abaAtual.value = { responde: { conversa_id: 'outra', canal: 'email' } }
    assert.equal(x.podeResponderEmail('outra'), true)
    assert.equal(x.podeResponderEmail(null), false)
    // A conversa que responde na aba é o chat/pós-venda (que recebeu o AVISO por
    // e-mail da plataforma): nunca "responder este e-mail" (sairia pelo chat).
    abaAtual.value = { responde: { conversa_id: 'outra', canal: 'pos_venda' } }
    assert.equal(x.podeResponderEmail('outra'), false)
    // Agrupou o chamado aberto: abre o destino.
    x.aoAgruparChamado('c0')
    assert.deepEqual(emitidos.at(-1), ['abrirConversa', 'c0'])
    // A conversa trocou: os cartões e o escolhido são zerados.
    assert.match(setup, /watch\(\(\) => props\.conversaId, \(\) => \{\s+emailResponder\.value = null\s+confirmouNaoResponde\.value = false\s+travaEmail\.value = null\s+geracaoCartoes\+\+\s+cartoesLidos\.clear\(\)\s+cartoesEmail\.value = \{\}/)
  }

  // ================================================ a LISTA e o PAINEL ④ (09/10/2026)
  const L = requerer('~/components/AtendimentoLista.vue')
  const PA = requerer('~/components/AtendimentoMailPastas.vue')
  const V = requerer('~/components/AtendimentoEmailVenda.vue')
  const listaSrc = sfc('../components/AtendimentoLista.vue').descriptor
  const listaSetup = listaSrc.scriptSetup.content
  const listaTpl = listaSrc.template.content
  const rotasAtd = api('routers/atendimento.py')
  const rotasEmail = api('routers/atendimento_email.py')
  const schemasAtd = api('schemas/atendimento.py')

  // ------------------------------------------------ W1/W2 (13/10/2026): o e-mail saiu da Caixa
  // O dono: "vamos deixar o e-mail aparecer só na aba de e-mail; a do chat de
  // atendimento aparece só no chat mesmo". Os chips do chamado do Site e o
  // filtro "E-mail sem vínculo" saíram da Caixa; moram em E-mail › Filas.
  {
    const separa = api('services/atendimento/separa_email.py')
    // A régua única: canal email + dados.fonte = 'tuta' (a Amazon pelo Gmail fica).
    assert.match(separa, /def conversa_de_email_sql\(c: Any = AtendimentoConversa\):[\s\S]*?c\.canal == CANAL_EMAIL,\s+func\.coalesce\(func\.btrim\(c\.dados\[CHAVE_FONTE\]\.astext\), ""\) == FONTE_TUTA/)
    assert.match(separa, /def na_caixa_sql\(c: Any = AtendimentoConversa\):[\s\S]*?return not_\(conversa_de_email_sql\(c\)\)/)
    // A lista: a Caixa só com o chat; `caixa=email` só com o e-mail.
    assert.match(rotasAtd, /^CAIXAS = \("", CAIXA_HUMANO, CAIXA_EMAIL\)$/m)
    assert.match(rotasAtd, /if caixa_email:\s+consulta = consulta\.where\(\s*separa_email\.conversa_de_email_sql\(\), mail_admin\.filtro_conversa\(user\)\s*\)\s+else:\s+consulta = consulta\.where\(separa_email\.na_caixa_sql\(\)\)/)
    assert.match(rotasAtd, /and not caixa_email\n\s+\)/, 'o Direct nunca entra na lista do e-mail')
    // O /resumo (plataformas, barra de lojas, etiquetas, Caixa Humano): só a Caixa.
    assert.match(rotasAtd, /cond = and_\(separa_email\.na_caixa_sql\(\), \*\(\[escopo\] if escopo is not None else \[\]\)\)/)
    assert.match(rotasAtd, /q_loja = select\([\s\S]*?\)\.where\(cond\)\.group_by\(/)
    assert.doesNotMatch(rotasAtd, /q_plat\s*=/, 'plataformas usam a mesma agregação filtrada por e-mail e escopo')
    for (const classe of ['PlataformaResumoOut', 'LojaResumoOut', 'ResumoOut']) {
      const corpo = (schemasAtd.match(new RegExp(`class ${classe}\\(BaseModel\\):([\\s\\S]*?)\\n\\n\\nclass `)) || [])[1] || ''
      assert.ok(corpo && !/email_sem_vinculo:/.test(corpo) && !/^\s+chamados:/m.test(corpo), `${classe} sem o e-mail`)
    }
    // As métricas e a Caixa Humano: sem o e-mail.
    assert.match(rotasAtd, /filtro = \[AtendimentoConversa\.ultima_mensagem_em >= desde, separa_email\.na_caixa_sql\(\)\]/)
    assert.match(api('services/atendimento/humano.py'), /separa_email\.na_caixa_sql\(c\),\s+c\.plataforma\.in_\(PLATAFORMAS_CAIXA\)/)
    // A linha da lista do e-mail traz o chamado (sem texto).
    assert.match(schemasAtd, /class EmailResumoOut\(BaseModel\):[\s\S]*?protocolo: str \| None = None\s+tipo: str \| None = None\s+tipo_rotulo: str \| None = None/)
    assert.match(rotasAtd, /"email": _email_resumo\(c\),/)
    // Os tipos do chamado: na fila "Chamados dos sites" (= os da API).
    const tiposPy = [...(api('services/mail_atendimento/constantes.py').match(/^ROTULO_TIPO_CAIXA = \{([\s\S]*?)^\}/m) || [])[1].matchAll(/^\s+(TIPO_[A-Z]+):/gm)].map((m) => m[1])
    const valorPy = (nome) => (api('services/mail_atendimento/constantes.py').match(new RegExp(`^${nome} = "([a-z]+)"$`, 'm')) || [])[1]
    assert.deepEqual(F.TIPOS_CHAMADO.map((t) => t.value), tiposPy.map(valorPy))
    assert.deepEqual(F.TIPOS_CHAMADO.map((t) => t.label), ['SAC', 'Atacado', 'Dúvidas e sugestões'])
    assert.match(rotasAtd, /^TIPOS_CHAMADO = tuple\(ROTULO_TIPO_CAIXA\)$/m)
    assert.match(rotasAtd, /tipo_chamado: Annotated\[str \| None, Query\(max_length=16\)\] = None,/)
    assert.match(rotasAtd, /return AtendimentoConversa\.dados\["mail"\]\["caixa"\]\.astext/)
    // A Caixa: sem chips do chamado, sem "E-mail sem vínculo" no menu, sem o tipo na consulta.
    for (const nome of ['TIPOS_CHAMADO', 'temChipsDoChamado', 'tipoChamadoAtivo', 'contagemDosChamados']) assert.equal(L[nome], undefined, nome)
    assert.ok(!L.FILTROS_MENU.some((x) => x.value === 'email_sem_vinculo'), 'o filtro do e-mail saiu do menu')
    assert.doesNotMatch(listaTpl, /data-chips-chamado|escolherTipo/)
    assert.doesNotMatch(listaSetup, /email_sem_vinculo|tipo_chamado/)
    const pagina = web('pages/atendimento.vue')
    assert.doesNotMatch(pagina, /tipo_chamado|tipoChamadoAtivo/)
    assert.doesNotMatch(web('components/AtendimentoFiltroPlataforma.vue'), /tipo_chamado/)
    assert.doesNotMatch(web('components/AtendimentoLojas.vue'), /tipo_chamado/)
    // O filtro do servidor continua (a lista do e-mail usa: `caixa=email&filtro=email_sem_vinculo`).
    const filtrosApi = [...((rotasAtd.match(/^FILTROS = \(([\s\S]*?)^\)/m) || [])[1] || '').matchAll(/"([a-z_]+)"/g)].map((m) => m[1])
    assert.ok(filtrosApi.includes('email_sem_vinculo'))
    // O vazio do "Todas" de sempre.
    const vazio = new Function('computed', 'filtros', trecho(listaSetup, 'const VAZIO', '// ↑/↓ com o foco') + '\nreturn textoVazio')
    assert.equal(vazio(Vue.computed, Vue.ref({ filtro: 'todas', q: '' })).value, 'Nenhuma conversa com esses filtros.')
  }

  // ------------------------------------------------ W3: Pastas e regras (E-mail › Filas)
  {
    for (const [metodo, caminho] of [['get', '/pastas'], ['patch', '/pastas/{pasta_id}'], ['get', '/regras'], ['put', '/regras']]) {
      assert.ok(rotasEmail.includes(`@router.${metodo}("${caminho}")`), `rota ${metodo} ${caminho}`)
    }
    const listar = rotasEmail.slice(rotasEmail.indexOf('async def listar_pastas('), rotasEmail.indexOf('class PastaPatch'))
    for (const campo of ['id', 'mailbox_id', 'chave', 'nome', 'caminho', 'tipo', 'plataforma', 'finalidade', 'marca', 'ler', 'plataforma_manual', 'finalidade_manual', 'ignorar', 'revisada', 'vista_em', 'sumiu_em', 'caixas', 'plataformas', 'finalidades']) {
      assert.ok(listar.includes(`"${campo}":`), `pastas: ${campo}`)
    }
    assert.match(rotasEmail, /class PastaPatch\(BaseModel\):[\s\S]*?plataforma: str \| None[\s\S]*?finalidade: str \| None[\s\S]*?ignorar: bool \| None/)
    assert.match(rotasEmail, /class RegraIn\(BaseModel\):[\s\S]*?tipo: str[\s\S]*?palavra: str[\s\S]*?valor: str[\s\S]*?ativa: bool = True/)
    // O "ler" e os tipos de palavra da tela = os do backend.
    const regrasPy = api('services/mail_atendimento/regras.py')
    for (const [chave, v] of [['LER_CORPO', 'corpo'], ['LER_SO_CONTAR', 'so_contar'], ['LER_NAO', 'nao']]) {
      assert.match(regrasPy, new RegExp(`^${chave} = "${v}"$`, 'm'))
      assert.ok(PA.LER[v], `rótulo do ler ${v}`)
    }
    assert.deepEqual(PA.TIPOS_DE_REGRA.map((t) => t.value), ['plataforma', 'finalidade', 'marca'])
    assert.match(regrasPy, /^TIPOS = \(TIPO_PLATAFORMA, TIPO_FINALIDADE, TIPO_MARCA\)$/m)
    for (const codigo of Object.keys(PA.ERROS_PASTAS)) assert.ok(rotasEmail.includes(`"${codigo}"`), `código ${codigo}`)

    // As regras puras.
    const pasta = (id, x = {}) => ({ id, mailbox_id: 'b1', chave: id, nome: id, caminho: null, tipo: 'pessoal', plataforma: 'ml', finalidade: 'mensagens', marca: null, ler: 'corpo', plataforma_manual: null, finalidade_manual: null, ignorar: false, revisada: true, vista_em: null, sumiu_em: null, ...x })
    const grupos = PA.pastasPorCaixa({ itens: [pasta('velha', { sumiu_em: '2026-10-01T00:00:00Z' }), pasta('mensagens ml'), pasta('outra', { mailbox_id: 'b2' })], caixas: [{ id: 'b1', nome: 'Geral — Tuta' }], plataformas: [], finalidades: [] })
    assert.deepEqual(grupos.map((g) => [g.nome, g.pastas.map((p) => p.id)]), [['Geral — Tuta', ['mensagens ml', 'velha']], ['Caixa', ['outra']]], 'a que sumiu no fim; caixa sem nome = "Caixa"')
    assert.deepEqual(PA.edicaoDe(pasta('a', { plataforma_manual: 'shopee' })), { plataforma: 'shopee', finalidade: '', ignorar: false })
    assert.equal(PA.corpoDaPasta(pasta('a'), { plataforma: '', finalidade: '', ignorar: false }), null, 'nada mudou: nada vai')
    assert.deepEqual(PA.corpoDaPasta(pasta('a'), { plataforma: 'shopee', finalidade: '', ignorar: true }), { plataforma: 'shopee', ignorar: true }, 'só o que mudou')
    assert.deepEqual(PA.corpoDaPasta(pasta('a', { plataforma_manual: 'shopee' }), { plataforma: '', finalidade: '', ignorar: false }), { plataforma: '' }, "'' = volta para a palavra")
    const porTipo = PA.regrasPorTipo([{ id: '1', tipo: 'plataforma', palavra: 'shopee', valor: 'shopee', ativa: true }, { id: '2', tipo: 'plataforma', palavra: 'ml', valor: 'ml', ativa: true }, { id: '3', tipo: 'finalidade', palavra: 'vendas', valor: 'vendas', ativa: false }])
    assert.deepEqual(Object.keys(porTipo), ['plataforma', 'finalidade', 'marca'])
    assert.deepEqual(porTipo.plataforma.map((r) => r.palavra), ['ml', 'shopee'])
    assert.deepEqual(porTipo.marca, [])
    const ops = { plataformas: ['ml', 'shopee', 'site'], finalidades: ['vendas', 'mensagens', 'problema', 'reclamacao'] }
    assert.equal(PA.erroDaRegra({ tipo: 'plataforma', palavra: 'kwai', valor: 'ml' }, ops), '')
    assert.equal(PA.erroDaRegra({ tipo: 'plataforma', palavra: '', valor: 'ml' }, ops), 'Escreva a palavra.')
    assert.match(PA.erroDaRegra({ tipo: 'plataforma', palavra: 'duas palavras', valor: 'ml' }, ops), /uma só/)
    assert.equal(PA.erroDaRegra({ tipo: 'plataforma', palavra: 'kwai', valor: 'kwai' }, ops), 'Plataforma desconhecida.')
    assert.equal(PA.erroDaRegra({ tipo: 'finalidade', palavra: 'duvida', valor: 'outra' }, ops), 'Finalidade desconhecida.')
    assert.equal(PA.erroDaRegra({ tipo: 'marca', palavra: 'charlots', valor: 'charlots' }, ops), '', 'marca: valor livre')
    assert.deepEqual(PA.corpoDaRegra({ tipo: 'plataforma', palavra: ' Kwai ', valor: ' ML ', ativa: true }), { tipo: 'plataforma', palavra: 'Kwai', valor: 'ml', ativa: true })

    // O componente de verdade: quem mexe vê; admin muda.
    const respostas = {
      '/api/atendimento/email/pastas': { itens: [pasta('mensagens ml'), pasta('nova shopee', { plataforma: 'shopee', revisada: false, ler: 'corpo' }), pasta('financeiro', { plataforma: null, finalidade: null, ler: 'so_contar' })], caixas: [{ id: 'b1', nome: 'Geral — Tuta' }], ...ops },
      '/api/atendimento/email/regras': { itens: [{ id: '1', tipo: 'plataforma', palavra: 'ml', valor: 'ml', ativa: true }, { id: '2', tipo: 'finalidade', palavra: 'vendas', valor: 'vendas', ativa: true }] },
      '/api/atendimento/email/pastas/mensagens ml': { id: 'mensagens ml', ler: 'nao', revisada: true },
      '/api/atendimento/email/pastas/nova shopee': { id: 'nova shopee', ler: 'corpo', revisada: true },
    }
    const so = montar('../components/AtendimentoMailPastas.vue', { isAdmin: false }, { respostas })
    await so.montar()
    let html = await so.html()
    assert.deepEqual(so.chamadas.map((c) => c.url).sort(), ['/api/atendimento/email/pastas', '/api/atendimento/email/regras'])
    assert.match(html, /data-pastas-caixa="b1"[\s\S]*?Geral — Tuta[\s\S]*?3 pasta\(s\)/)
    assert.match(html, /data-pasta="nova shopee"[\s\S]*?>nova<[\s\S]*?data-pasta-ler[^>]*>lê o corpo</)
    assert.match(html, /data-pasta="financeiro"[\s\S]*?data-pasta-ler[^>]*>só conta</)
    assert.match(html, /data-regras-tipo="plataforma"[\s\S]*?data-regra="plataforma:ml"/)
    assert.match(html, /Só admin muda/)
    assert.doesNotMatch(html, /data-salvar-pasta|data-regra-nova|data-alternar-regra|data-confirmar-pasta/, 'quem não é admin: sem mudar')
    await so.estado.salvarPasta(respostas['/api/atendimento/email/pastas'].itens[0])
    assert.equal(so.chamadas.filter((c) => c.metodo !== 'GET').length, 0, 'nem pela função')

    const ad = montar('../components/AtendimentoMailPastas.vue', { isAdmin: true }, { respostas: { ...respostas, '/api/atendimento/email/regras': (url, opts) => (opts.method === 'PUT' ? { ...opts.body, id: '9' } : respostas['/api/atendimento/email/regras']) } })
    await ad.montar()
    html = await ad.html()
    assert.match(html, /data-pasta="mensagens ml"[\s\S]*?data-salvar-pasta/)
    assert.match(html, /data-pasta="nova shopee"[\s\S]*?data-confirmar-pasta/, 'pasta nova: "está certa"')
    assert.match(html, /data-regra-nova/)
    // Salvar a pasta: só o que mudou; nada mudou, nada vai.
    const ml = ad.estado.dados.itens[0]
    await ad.estado.salvarPasta(ml)
    assert.equal(ad.chamadas.filter((c) => c.metodo === 'PATCH').length, 0)
    ad.estado.edicao['mensagens ml'].ignorar = true
    await ad.estado.salvarPasta(ml)
    let mud = ad.chamadas.filter((c) => c.metodo !== 'GET').at(-1)
    assert.deepEqual([mud.url, mud.metodo, mud.body], ['/api/atendimento/email/pastas/mensagens%20ml', 'PATCH', { ignorar: true }])
    // "está certa" = PATCH vazio (marca a pasta nova como conferida).
    await ad.estado.confirmarPasta(ad.estado.dados.itens[1])
    mud = ad.chamadas.filter((c) => c.metodo !== 'GET').at(-1)
    assert.deepEqual([mud.url, mud.body], ['/api/atendimento/email/pastas/nova%20shopee', {}])
    // Desligar uma palavra e criar outra (PUT, a rota acha pelo tipo + palavra).
    await ad.estado.salvarRegra(ad.estado.regras[0], { ativa: false })
    mud = ad.chamadas.filter((c) => c.metodo !== 'GET').at(-1)
    assert.deepEqual([mud.url, mud.metodo, mud.body], ['/api/atendimento/email/regras', 'PUT', { tipo: 'plataforma', palavra: 'ml', valor: 'ml', ativa: false }])
    ad.estado.nova.tipo = 'plataforma'
    ad.estado.nova.palavra = 'kwai'
    await Vue.nextTick()
    ad.estado.nova.valor = 'kwai'
    assert.equal(ad.estado.erroNova, 'Plataforma desconhecida.')
    const antes = ad.chamadas.length
    await ad.estado.adicionarRegra()
    assert.equal(ad.chamadas.length, antes, 'inválida: nada vai')
    ad.estado.nova.valor = 'shopee'
    await ad.estado.adicionarRegra()
    mud = ad.chamadas.filter((c) => c.metodo !== 'GET').at(-1)
    assert.deepEqual(mud.body, { tipo: 'plataforma', palavra: 'kwai', valor: 'shopee', ativa: true })
    assert.equal(ad.estado.nova.palavra, '', 'a caixa da palavra nova limpa')
    assert.ok(ad.avisos.some(([t, m]) => t === 'ok' && /kwai/.test(m)))

    // Nas Filas: a aba "Pastas e regras" ao lado das filas (não é fila).
    const filasFonte = sfc('../components/AtendimentoMailFilas.vue').descriptor.template.content
    assert.match(filasFonte, /data-fila="pastas"\s+@click="pastasAbertas = true"\s*>Pastas e regras<\/button>/)
    assert.match(filasFonte, /<AtendimentoMailPastas v-if="pastasAbertas" :is-admin="isAdmin" \/>/)
    const fl = montar('../components/AtendimentoMailFilas.vue', { isAdmin: true, lojas: [] }, {
      respostas: { '/api/atendimento/email/filas': {}, '/api/atendimento/email/saude': { caixas: [] }, '/api/atendimento/email/emails?fila=sem_loja': { itens: [], proximo: null } },
      filhos: { AtendimentoMailPastas: { props: ['isAdmin'], setup: (p) => () => Vue.h('div', { 'data-pastas-stub': String(p.isAdmin) }) } },
    })
    await fl.montar()
    assert.doesNotMatch(await fl.html(), /data-pastas-stub/)
    fl.estado.pastasAbertas = true
    html = await fl.html()
    assert.match(html, /data-pastas-stub="true"/)
    assert.doesNotMatch(html, /Nada nesta fila/, 'a fila some enquanto as pastas estão abertas')
    fl.estado.verFila('erro')
    assert.equal(fl.estado.pastasAbertas, false)
    assert.equal(fl.estado.fila, 'erro')
  }

  // ------------------------------------------------ W4: "E-mails da venda" no painel ④
  {
    assert.ok(rotasEmail.includes('@router.get("/conversas/{conversa_id}/emails-da-venda")'))
    const rota = rotasEmail.slice(rotasEmail.indexOf('async def emails_da_venda('), rotasEmail.indexOf('@router.get("/anexos/{attachment_id}")'))
    assert.match(rota, /familia = await abas\.familia\(session, conversa\)/)
    assert.match(rota, /MailMessageMeta\.estado\.in_\(ESTADOS_COM_TEXTO\)/)
    for (const campo of ['caixa', 'loja', 'plataforma', 'quantidade', 'ultimo_em', 'pastas', 'pasta', 'finalidade', 'destaque', 'total', 'caixas']) assert.ok(rota.includes(`"${campo}"`), `venda: ${campo}`)
    assert.doesNotMatch(rota, /decifrar|"texto"|"assunto"|"de":/, 'nada do e-mail em si')
    assert.equal(V.urlEmailsDaVenda('c 1'), '/api/atendimento/email/conversas/c%201/emails-da-venda')
    // O sinal: a aba E-mail da venda e os recebidos da própria conversa.
    assert.equal(V.sinalDeEmails(null, []), 0)
    assert.equal(V.sinalDeEmails({ abas: [{ chave: 'pos_venda', total: 4 }, { chave: 'email', total: 2 }] }, [{ email: null }]), 2)
    assert.equal(V.sinalDeEmails({ abas: [] }, [{ email: { tipo: 'recebido' } }, { email: { tipo: 'resposta' } }]), 1, 'o aviso por e-mail no chat também conta; a nossa resposta não')
    assert.equal(V.rotuloDasPastas([{ pasta: 'problema ml', quantidade: 2 }, { pasta: null, quantidade: 1 }]), 'problema ml (2) · sem pasta')
    // O componente: lê ao montar e relê quando o sinal muda.
    const venda = { total: 3, caixas: [{ caixa: '21max@tuta.com', loja: 'Barbosa', plataforma: 'ml', quantidade: 3, ultimo_em: '2026-10-09T03:00:00Z', pastas: [{ pasta: 'problema ml', finalidade: 'problema', destaque: true, quantidade: 2 }, { pasta: 'mensagens ml', finalidade: 'mensagens', destaque: false, quantidade: 1 }] }] }
    const ve = montar('../components/AtendimentoEmailVenda.vue', { conversaId: 'p1', sinal: 2 }, { respostas: { '/api/atendimento/email/conversas/p1/emails-da-venda': venda } })
    await ve.montar()
    let html = await ve.html()
    assert.match(html, /data-painel-emails-venda[\s\S]*?E-mails da venda[\s\S]*?>3</)
    assert.match(html, /data-email-venda-caixa[\s\S]*?21max@tuta\.com → Barbosa[\s\S]*?3 e-mails/)
    assert.match(html, /data-email-venda-pastas[^>]*>problema ml \(2\) · mensagens ml</)
    assert.match(html, /class="mt-0\.5 text-red-700/, 'pasta de problema em destaque')
    ve.reativo.sinal = 3
    await Vue.nextTick(); await esperar()
    assert.equal(ve.chamadas.length, 2, 'chegou e-mail: relê')
    const vazio = montar('../components/AtendimentoEmailVenda.vue', { conversaId: 'p2', sinal: 1 }, { respostas: { '/api/atendimento/email/conversas/p2/emails-da-venda': { total: 0, caixas: [] } } })
    await vazio.montar()
    assert.match(await vazio.html(), /Nenhum e-mail das lojas ligado a esta venda\./)
    const falha = montar('../components/AtendimentoEmailVenda.vue', { conversaId: 'p3', sinal: 1 }, { respostas: { '/api/atendimento/email/conversas/p3/emails-da-venda': new Error('500') } })
    await falha.montar()
    assert.match(await falha.html(), /Não consegui ler os e-mails da venda agora\./)
    // O painel: o bloco só com e-mail, antes das Observações do Bling; a conversa passa o sinal.
    const pedidoTpl = sfc('../components/AtendimentoPedido.vue').descriptor.template.content
    assert.match(pedidoTpl, /<AtendimentoEmailVenda v-if="conversa.email && emailsSinal" :conversa-id="conversa\.id" :sinal="emailsSinal" \/>/)
    assert.ok(pedidoTpl.indexOf('<AtendimentoEmailVenda') < pedidoTpl.indexOf('OBSERVAÇÕES DO BLING'))
    assert.match(sfc('../components/AtendimentoPedido.vue').descriptor.scriptSetup.content, /emailsSinal\?: number[\s\S]*?garantiaAcesso: null, emailsSinal: 0,/)
    const conversaSfc = sfc('../components/AtendimentoConversa.vue').descriptor
    assert.match(conversaSfc.scriptSetup.content, /const emailsSinal = computed\(\(\) => sinalDeEmails\(abasDados\.value, detalhe\.value\?\.mensagens\)\)/)
    assert.match(conversaSfc.template.content, /<AtendimentoPedido[\s\S]*?:emails-sinal="emailsSinal"/)
  }

  // ------------------------------------------------ S11: o e-mail da ponte da loja Temu/AliExpress (ficha sem integração)
  {
    // Nunca o painel "o que a IA responderia" (com o Sugerir) nem a trava do
    // Seller Center: o e-mail sai pelo Tuta, com as travas do e-mail.
    const setupConversa = sfc('../components/AtendimentoConversa.vue').descriptor.scriptSetup.content
    assert.match(setupConversa, /if \(ehEmail\.value\) return false\n\s+if \(sellerCenterDe\(d\.conversa\.plataforma\)\) return true/)
    const js = trecho(setupConversa, 'const bloqueioEnvio = computed', 'const podeDigitar') + '\nreturn bloqueioEnvio'
    const travaDe = (email) => new Function('computed', 'detalhe', 'props', 'sellerCenterDe', 'motivoSellerCenter', 'ERROS', 'AVISO_SO_LEITURA', 'avaliacaoDaConversa', 'ehEmail', 'TRAVAS_GERAIS_DO_EMAIL', 'motivoLegivel', 'travaEmail', 'CODIGOS_CONFIRMAVEIS', 'bloqueioLegivel', 'semConta', js)(
      Vue.computed, Vue.ref({ conversa: { plataforma: 'temu', somente_leitura: false, situacao: 'aberta' }, envio: { pode_enviar: true } }), { canEdit: true, flags: null }, P.sellerCenterDe, P.motivoSellerCenter, P.ERROS, P.AVISO_SO_LEITURA, Vue.ref(null), Vue.ref(email),
      R.TRAVAS_GERAIS_DO_EMAIL, P.motivoLegivel, Vue.ref(''), R.CODIGOS_CONFIRMAVEIS, (x) => x, Vue.ref(false),
    ).value
    assert.equal(travaDe(true), '', 'e-mail da Temu: a caixa de resposta do e-mail')
    assert.equal(travaDe(false), P.motivoSellerCenter('temu'), 'o chat da Temu: continua no Seller Center')
  }

  // ------------------------------------------------ a caixa da aba: o mesmo aviso da fila do e-mail
  assert.match(web('components/AtendimentoAbaResposta.vue'), /if \(ehEmail\.value\) toasts\.success\(`\$\{AVISO_EMAIL_NA_FILA\.titulo\} \(\$\{r\.canal_rotulo \|\| 'outra conversa'\}\)`, AVISO_EMAIL_NA_FILA\.texto\)/)

  console.log('ok — atendimento-mail-atendimento: contrato, regras, seções, filas (e as de conversa), configurar, prévia, cartão, chamado, caixa da aba, conversa, o e-mail fora da Caixa (lista, página, barra), pastas e regras, e-mails da venda')
}

principal().catch((e) => {
  console.error(e)
  process.exit(1)
})
