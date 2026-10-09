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
    throw new Error(`rota inesperada: ${url}`)
  }
  const pergunta = (t) => { perguntas.push(t); return typeof confirmar === 'function' ? confirmar(t) : confirmar }
  const g = {
    ref: Vue.ref, computed: Vue.computed, reactive: Vue.reactive, watch: Vue.watch, nextTick: Vue.nextTick,
    onMounted: (fn) => montados.push(fn), onBeforeUnmount: () => {},
    useApi: () => ({ url: (v) => v, api: responder }),
    useToasts: () => ({ success: (t) => avisos.push(['ok', t]), error: (t) => avisos.push(['erro', t]), info: (t) => avisos.push(['info', t]), warning: (t) => avisos.push(['aviso', t]) }),
    confirm: pergunta,
    window: { confirm: pergunta },
    crypto: { randomUUID: () => '00000000-0000-4000-8000-000000000000' },
    document: { hidden: false }, setInterval: () => 1, clearInterval: () => {},
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
      ['get', '/conversas/{conversa_id}/previa'], ['post', '/conversas/{conversa_id}/agrupar'], ['get', '/envios'],
      ['post', '/envios/{job_id}/resolver'], ['get', '/saude'],
    ]) assert.ok(rotas.includes(`@router.${metodo}("${caminho}")`), `rota ${metodo} ${caminho}`)
    assert.match(rotas, /prefix="\/api\/atendimento\/email"/)
    assert.match(rotasConfig, /@router\.get\("\/mailboxes\/\{mailbox_id\}\/settings"\)/)
    assert.match(rotasConfig, /@router\.patch\("\/mailboxes\/\{mailbox_id\}\/settings"\)/)
    assert.match(rotasCentral, /@router\.patch\("\/mailboxes\/\{mailbox_id\}"\)/)
    assert.match(rotasCentral, /@router\.post\("\/outbox\/\{job_id\}\/resolve"\)/)
    assert.match(api('schemas/mail.py'), /class OutboxResolve\(StrictModel\):[\s\S]*?saiu: bool/)
    assert.match(api('schemas/mail.py'), /class MailboxPatch\(StrictModel\):[\s\S]*?aliases: list\[EmailStr\] \| None/)

    // As filas da tela = as do backend (+ o "revisar envio" das contagens).
    const nomes = Object.fromEntries([...constantes.matchAll(/^(FILA_[A-Z_]+) = "([a-z_]+)"$/gm)].map((m) => [m[1], m[2]]))
    const filas = ((constantes.match(/^FILAS = \(([^)]*)\)$/m) || [])[1] || '').split(',').map((x) => nomes[x.trim()]).filter(Boolean)
    assert.deepEqual(F.FILAS.map((f) => f.value), [...filas, 'revisar_envio'])
    assert.match(rotas, /saida\["revisar_envio"\] = /)
    assert.equal(F.urlDaFila('revisar_envio'), '/api/atendimento/email/envios?estado=revisar')
    // "revisar" = o incerto E o lease vencido (o Mac pegou e sumiu): a mesma regra nas contagens.
    assert.match(rotas, /if estado == "revisar":\s+q = q\.where\(_a_conferir\(\)\)/)
    // A contagem da fila "revisar envio" = a lista (só a resposta de CONVERSA: o
    // job da caixa crua da empresa também tem ligação, `caixa_empresa`).
    assert.match(rotas, /\.where\(MailOutboxMeta\.origem == "conversa", _a_conferir\(\)\)\s+\)\s+or 0\s+\)\s+return saida/)
    assert.equal(F.urlDaFila('sem_loja'), '/api/atendimento/email/emails?fila=sem_loja')
    assert.equal(F.urlDaFila('resumo', '2026-10-08T10:00:00+00:00'), '/api/atendimento/email/emails?fila=resumo&antes_de=2026-10-08T10%3A00%3A00%2B00%3A00')

    // O "não responder" confirmável e as travas gerais: os códigos do backend.
    assert.match(responder, new RegExp(`^RECUSA_NAO_RESPONDE = "${R.CODIGO_NAO_RESPONDE}"$`, 'm'))
    assert.match(responder, new RegExp(`^RECUSA_JA_RESPONDIDO = "${R.CODIGO_JA_RESPONDIDO}"$`, 'm'))
    assert.match(responder, /CONFIRMAVEIS = frozenset\(\{RECUSA_NAO_RESPONDE, RECUSA_JA_RESPONDIDO\}\)/)
    assert.deepEqual([...R.CODIGOS_CONFIRMAVEIS].sort(), [R.CODIGO_JA_RESPONDIDO, R.CODIGO_NAO_RESPONDE].sort())
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
    // A página passa quem mexe, as lojas e o "abrir a conversa".
    assert.match(web('pages/atendimento.vue'), /<AtendimentoMail v-if="aba === 'mail'" :is-admin="isAdmin" :can-operate="mexe" :stores="resumo\?\.lojas \|\| \[\]" @open-conversation="abrirConversaNaCaixa" \/>/)
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
    assert.deepEqual(m.chamadas.map((c) => c.url).sort(), ['/api/atendimento/email/emails?fila=sem_loja', '/api/atendimento/email/filas', '/api/atendimento/email/saude'])
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
    // Abrir a conversa (a página abre na Caixa).
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
    assert.match(await x.html(), /nao_e_conversa_de_email|Não consegui/)
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
    assert.doesNotMatch(await so.html(), /data-email-agrupar/, 'quem só lê: sem agrupar')
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

  console.log('ok — atendimento-mail-atendimento: contrato, regras, seções, filas, configurar, prévia, cartão, chamado, caixa da aba e conversa')
}

principal().catch((e) => {
  console.error(e)
  process.exit(1)
})
