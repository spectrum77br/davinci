// node tests/atendimento-zap.cjs — o Zap (WhatsApp Cloud API) no Atendimento,
// Onda 1 de docs/zap/desenho.md (19/10/2026): só RECEBER, tudo desligado.
// O que a tela tem de garantir:
//  - o vocabulário: `whatsapp` em TODAS as listas paralelas (nome, curto
//    "Zap", cor, grupo da barra e do menu Plataforma, externas, modo só
//    Observar igual ao backend, caixa `zap`, limite de 4.096 igual ao
//    backend, tipos do WhatsApp, Flags `zap_*`) — esquecer uma faz a tela
//    mostrar "whatsapp" cru;
//  - o ícone próprio (círculo verde com balão; nada do `Zap` do lucide);
//  - a barra: UMA linha verde "WhatsApp" por número (`externo_ref`
//    "zap:<pnid>"), que filtra a lista pela origem; o title diz que a
//    resposta é pelo Duoke enquanto o envio do Zap não está ligado;
//  - a conversa: selo "Zap"; a janela de 24 h SEMPRE à mostra (âmbar só nas
//    últimas 2 h, vermelha quando fecha) e nenhum ramo anterior da faixa pega
//    o Zap; o painel do pedido, a gaveta, o "ver no pedido", o "Pausar IA" e o
//    AdsPower escondidos — com a caixa de resposta À MOSTRA dizendo "Responda
//    pelo Duoke" (a frase do backend); sem "Sugerir" (a IA fica fora do Zap);
//    a mídia ainda sem a nossa cópia como "[Foto — aguardando download]";
//  - a página: a faixa "Zap: envio desligado — responda pelo Duoke" só com
//    número de WhatsApp no /resumo;
//  - a aba Lojas e modo: o canal do número só em Observar (travado).
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { renderToString } = require('vue/server-renderer')
const { parse, compileTemplate, compileScript } = require('vue/compiler-sfc')

const transpile = (source, module = ts.ModuleKind.CommonJS) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module },
}).outputText

function sfc(rel) {
  const filename = path.resolve(__dirname, rel)
  const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
  assert.deepEqual(errors, [], rel)
  const compiled = compileTemplate({ source: descriptor.template.content, filename, id: path.basename(rel) })
  assert.deepEqual(compiled.errors, [], `${rel}: template compila`)
  new Function('exports', 'require', transpile(compiled.code))({}, require)
  return descriptor
}
function exportsDe(descriptor) {
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(descriptor.script.content))((n) => require(n), mod, mod.exports)
  return mod.exports
}
function trecho(src, ini, fim) {
  const a = src.indexOf(ini)
  const b = src.indexOf(fim, a + 1)
  assert.ok(a >= 0 && b > a, `trecho ${ini} … ${fim}`)
  return src.slice(a, b)
}
const api = (rel) => fs.readFileSync(path.resolve(__dirname, '../../api/app', rel), 'utf8')
const constantes = api('services/atendimento/constantes.py')
const config = api('config.py')

const P = exportsDe(sfc('../components/AtendimentoPlataforma.vue'))
const filtroSfc = sfc('../components/AtendimentoFiltroPlataforma.vue')
const F = exportsDe(filtroSfc)
const lojasSfc = sfc('../components/AtendimentoLojas.vue')
const LJ = (() => {
  const req = (n) => (n === '~/components/AtendimentoFiltroPlataforma.vue' ? F : n === '~/components/AtendimentoPlataforma.vue' ? P : require(n))
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(lojasSfc.script.content))(req, mod, mod.exports)
  return mod.exports
})()
const CORTE = ['corteDaBarra', 'gruposVisiveis', 'nomeDoCorte', 'ativaTodasDoCorte', 'filtrosTodasDoCorte', 'numeroDoCorte', 'filtrosDoGrupo', 'campoDaBarra', 'numeroDaLoja', 'numeroDe']

// ------------------------------------------------ vocabulário (todas as listas)
{
  const info = P.plataformaInfo('whatsapp')
  assert.deepEqual(info, { value: 'whatsapp', nome: 'WhatsApp', curto: 'Zap', cor: 'bg-[#25D366]', de: 'do WhatsApp' })
  assert.equal(P.plataformaInfo(' WhatsApp ').nome, 'WhatsApp', 'o código em qualquer caixa')
  assert.equal(P.nomeDoGrupo('whatsapp'), 'WhatsApp', 'o grupo da barra')
  assert.ok(P.PLATAFORMAS_EXTERNAS.includes('whatsapp'), 'canal externo (como site e redes)')
  assert.ok(!P.PLATAFORMAS_COM_CANAL.some((p) => p.value === 'whatsapp'), 'fora do manual da IA e dos modelos')
  assert.ok(!P.PLATAFORMAS_COM_ENVIO.some((p) => p.value === 'whatsapp'), 'fora das respostas prontas')
  assert.equal(P.viaRobo('whatsapp'), false)
  assert.equal(P.canalLabel('zap'), 'Zap', 'a caixa do número')
  assert.equal(P.variasCaixas('whatsapp'), false, 'uma caixa só por número: a linha não repete "Zap"')

  // O backend: os mesmos códigos.
  assert.match(constantes, /^PLATAFORMA_WHATSAPP = "whatsapp"$/m)
  assert.match(constantes, /^CANAL_ZAP = "zap"$/m)
  assert.match(constantes, /^PLATAFORMAS_EXTERNAS = \(PLATAFORMA_SITE, \*PLATAFORMAS_REDE, PLATAFORMA_WHATSAPP\)$/m)

  // O modo: só Observar na Onda 1 — o mesmo que o PATCH /canais aceita.
  assert.deepEqual(P.MODOS_EXTERNOS.whatsapp, ['observar'])
  const bloco = (constantes.match(/^MODOS_EXTERNOS[^=]*= \{([\s\S]*?)^\}/m) || [])[1] || ''
  const zapModos = (bloco.match(/PLATAFORMA_WHATSAPP: \(([^)]*)\)/) || [])[1] || ''
  assert.deepEqual(zapModos.split(',').map((x) => x.trim()).filter(Boolean), ['MODO_OBSERVAR'])

  // O limite de caracteres: o mesmo do backend (4.096 da Cloud API).
  const limites = (constantes.match(/^LIMITE_CARACTERES[^=]*= \{([\s\S]*?)^\}/m) || [])[1] || ''
  const limiteApi = Number((limites.match(/\(PLATAFORMA_WHATSAPP, CANAL_ZAP\): (\d+)/) || [])[1])
  assert.equal(limiteApi, 4096)
  assert.equal(P.LIMITE_CARACTERES['whatsapp:zap'], limiteApi)
  assert.equal(P.limiteDe('whatsapp', 'zap'), 4096)
  assert.equal(P.limiteDe(null, null), 350, 'a resposta pronta de várias caixas continua com o MENOR limite')

  // As Flags do /resumo: as chaves do Zap (opcionais: a API antiga não manda).
  const plataformaSrc = fs.readFileSync(path.resolve(__dirname, '../components/AtendimentoPlataforma.vue'), 'utf8')
  const flags = trecho(plataformaSrc, 'export type Flags = {', '\n}\n')
  assert.match(flags, /zap_leitura_ativa\?: boolean/)
  assert.match(flags, /zap_envio_ativo\?: boolean/)
  // E as chaves no servidor nascem desligadas (docs/zap/desenho.md §6.1).
  assert.match(config, /^ {4}atendimento_zap_leitura_ativa: bool = False$/m)
  assert.match(config, /^ {4}atendimento_zap_envio_ativo: bool = False$/m)
}

// ------------------------------------------------ o ícone
{
  const icone = sfc('../components/AtendimentoIconePlataforma.vue')
  const fonte = icone.scriptSetup.content + icone.template.content
  assert.match(fonte, /whatsapp: 'WhatsApp'/, 'o nome para o leitor de tela')
  assert.match(icone.template.content, /<template v-else-if="cod === 'whatsapp'">[\s\S]*?#25D366[\s\S]*?<\/template>/, 'o desenho próprio, verde')
  assert.ok(!/lucide-vue-next/.test(icone.scriptSetup.content), 'nada do lucide (o `Zap` dele é um raio, o das Automáticas)')
  const { content } = compileScript(icone, { id: 'icone', inlineTemplate: true })
  const mod = {}
  new Function('exports', 'require', transpile(content))(mod, require)
  const render = async (plataforma) => (await renderToString(Vue.createSSRApp(mod.default, { plataforma }))).replace(/<!--[\s\S]*?-->/g, '')
  ;(async () => {
    const html = await render('whatsapp')
    assert.match(html, /aria-label="WhatsApp"/)
    assert.match(html, /<title>WhatsApp<\/title>/)
    assert.match(html, /fill="#25D366"/)
    assert.ok(!/>W</.test(html), 'não cai na bolinha cinza com a inicial')
  })().catch((e) => { console.error(e); process.exit(1) })
}

// ------------------------------------------------ a barra: uma linha por número
const vazio = { plataforma: '', integration_id: '', canal: '', filtro: 'todas', q: '', externo_ref: '', rede_social_id: '' }
const resumo = (flags = {}) => ({
  plataformas: [
    { plataforma: 'shopee', aguardando: 1, vencendo: 0, vencidas: 0 },
    { plataforma: 'whatsapp', aguardando: 3, vencendo: 0, vencidas: 0 },
  ],
  lojas: [
    { integration_id: 'i1', plataforma: 'shopee', conta: 'ATV', aguardando: 1, vencidas: 0, status_canal: 'ok' },
    { integration_id: null, canal_id: 'cz1', externo_ref: 'zap:1111', plataforma: 'whatsapp', conta: 'Uranyx (11) 9999-0001', aguardando: 2, vencidas: 0, status_canal: 'ok' },
    { integration_id: null, canal_id: 'cz2', externo_ref: 'zap:2222', plataforma: 'whatsapp', conta: 'Charlots (11) 9999-0002', aguardando: 1, vencidas: 0, status_canal: 'novo' },
  ],
  canais: [
    { id: 'c-shopee', integration_id: 'i1', plataforma: 'shopee', canal: 'chat', status: 'ok', modo: 'observar' },
    { id: 'cz1', integration_id: null, externo_ref: 'zap:1111', plataforma: 'whatsapp', canal: 'zap', status: 'ok', modo: 'observar' },
    { id: 'cz2', integration_id: null, externo_ref: 'zap:2222', plataforma: 'whatsapp', canal: 'zap', status: 'novo', modo: 'observar' },
  ],
  flags,
})
const setup = lojasSfc.scriptSetup.content
function barra(r, filtrosIniciais = {}) {
  const filtros = Vue.ref({ ...vazio, ...filtrosIniciais })
  const corpo = transpile(trecho(setup, 'const ORDEM', 'function escolherTodas') + '\n' + setup.slice(setup.indexOf('function escolherTodas')))
    + '\nreturn { lojas, grupos, total, motivo, escolherLoja, ativaLoja, escolherGrupo }'
  const out = new Function('computed', 'props', 'filtros', 'viaRobo', 'statusCanalCodigo', 'semLeitura', 'leituraParada', 'nomeDoGrupo', 'plataformaInfo', 'sellerCenterDe', 'statusCanalInfo', ...CORTE, corpo)(
    Vue.computed, { resumo: r }, filtros, P.viaRobo, P.statusCanalCodigo, P.semLeitura, P.leituraParada, P.nomeDoGrupo, P.plataformaInfo, P.sellerCenterDe, P.statusCanalInfo, ...CORTE.map((n) => LJ[n]),
  )
  return { ...out, filtros }
}
{
  const b = barra(resumo())
  const grupos = b.grupos.value
  assert.deepEqual(grupos.map((g) => g.plataforma), ['shopee', 'whatsapp'], 'o WhatsApp no fim da barra')
  const zap = grupos[1]
  assert.equal(zap.nome, 'WhatsApp')
  assert.deepEqual(zap.lojas.map((l) => [l.conta, l.externo_ref, l.nao_lidas]), [
    ['Charlots (11) 9999-0002', 'zap:2222', 1],
    ['Uranyx (11) 9999-0001', 'zap:1111', 2],
  ], 'uma linha por número, cada uma com a sua bolinha')
  assert.equal(zap.nao_lidas, 3)
  assert.ok(zap.lojas.every((l) => !l.apagada), 'o número que ainda não recebeu nada (novo) fica aceso')
  // Clicar no número filtra pela origem (todas as conversas daquele número).
  b.escolherLoja(zap.lojas[1])
  assert.deepEqual(
    [b.filtros.value.plataforma, b.filtros.value.externo_ref, b.filtros.value.integration_id, b.filtros.value.rede_social_id],
    ['whatsapp', 'zap:1111', '', ''],
  )
  assert.ok(b.ativaLoja(zap.lojas[1]))
  assert.ok(!b.ativaLoja(zap.lojas[0]))
  // O nome do grupo: o WhatsApp inteiro, e corta a barra (é grupo do menu).
  b.escolherGrupo('whatsapp')
  assert.deepEqual([b.filtros.value.plataforma, b.filtros.value.externo_ref, b.filtros.value.plataforma_topo], ['whatsapp', '', 'whatsapp'])
  // O title: a resposta, por enquanto, é pelo Duoke.
  assert.match(b.motivo(zap.lojas[1]), /^WhatsApp · Uranyx \(11\) 9999-0001/)
  assert.match(b.motivo(zap.lojas[1]), /Número de WhatsApp \(API oficial da Meta\) — por enquanto só leitura: a resposta é pelo Duoke/)
  // Com o freio único e a chave do Zap ligados, sem o "pelo Duoke".
  const ligado = barra(resumo({ envio_ativo: true, zap_envio_ativo: true }))
  const linha = ligado.grupos.value.find((g) => g.plataforma === 'whatsapp').lojas[0]
  assert.match(ligado.motivo(linha), /Número de WhatsApp \(API oficial da Meta\)/)
  assert.ok(!/pelo Duoke/.test(ligado.motivo(linha)))
  // Só a chave do Zap ligada (o freio único desligado): continua pelo Duoke.
  const meio = barra(resumo({ envio_ativo: false, zap_envio_ativo: true }))
  assert.match(meio.motivo(meio.grupos.value.find((g) => g.plataforma === 'whatsapp').lojas[0]), /pelo Duoke/)
}

// ------------------------------------------------ o menu Plataforma
{
  const chips = F.chipsDoResumo(resumo(), '')
  assert.deepEqual(chips.map((c) => [c.valor, c.nome, c.aguardando]), [['shopee', 'Shopee', 1], ['whatsapp', 'WhatsApp', 3]])
  assert.equal(F.topoDoValor('whatsapp'), 'whatsapp')
  // Sem número de WhatsApp (nem conversa): o chip não aparece.
  const semZap = { ...resumo(), lojas: resumo().lojas.filter((l) => l.plataforma !== 'whatsapp'), plataformas: [{ plataforma: 'shopee', aguardando: 1 }] }
  assert.ok(!F.chipsDoResumo(semZap, '').some((c) => c.valor === 'whatsapp'))
}

// ------------------------------------------------ a janela de 24 h
{
  const agora = new Date(2026, 9, 19, 10, 0, 0).getTime()
  const em = (min) => new Date(agora + min * 60000).toISOString()
  assert.equal(P.janelaDoZap(null, agora), null)
  assert.equal(P.janelaDoZap('lixo', agora), null)
  // Aberta: neutra, com a hora (hoje) e quanto falta.
  const aberta = P.janelaDoZap(em(190), agora)
  assert.equal(aberta.nivel, 'aberta')
  assert.equal(aberta.texto, `Janela do WhatsApp: dá para responder até ${P.fmtHora(em(190))} (faltam 3 h 10 min).`)
  assert.ok(!/amber|red/.test(aberta.cls), 'neutra com a janela aberta')
  // Amanhã: com o dia.
  const amanha = P.janelaDoZap(em(20 * 60), agora)
  assert.equal(amanha.nivel, 'aberta')
  assert.match(amanha.texto, new RegExp(`até ${P.fmtDataHora(em(20 * 60)).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')} \\(faltam 20 h\\)`))
  // Âmbar SÓ nas últimas 2 h (a régua genérica de 48 h deixaria sempre âmbar).
  assert.equal(P.JANELA_ZAP_VENCENDO_MS, 2 * 3600 * 1000)
  assert.equal(P.janelaDoZap(em(121), agora).nivel, 'aberta')
  const vencendo = P.janelaDoZap(em(119), agora)
  assert.equal(vencendo.nivel, 'vencendo')
  assert.match(vencendo.cls, /amber/)
  assert.match(vencendo.texto, /\(faltam 1 h 59 min\)\.$/)
  // Fechada: vermelha, só modelo aprovado.
  const fechada = P.janelaDoZap(em(-5), agora)
  assert.equal(fechada.nivel, 'fechada')
  assert.match(fechada.cls, /red/)
  assert.match(fechada.texto, /^Passaram 24 h: o WhatsApp só aceita um modelo aprovado \(a janela fechou em .+\)\.$/)
  assert.equal(P.janelaDoZap(em(0), agora).nivel, 'fechada')
}

// ------------------------------------------------ a conversa do Zap
const conversaSfc = sfc('../components/AtendimentoConversa.vue')
const C = exportsDe(conversaSfc)
const cs = conversaSfc.scriptSetup.content
const tela = conversaSfc.template.content
const zapConversa = { id: 'cv1', plataforma: 'whatsapp', canal: 'zap', conta: '(11) 9999-0001', somente_leitura: false, ia_pausada: false, situacao: 'aberta', anuncio_titulo: null, pode_enviar_ate: null }
{
  // A janela: o ramo do Zap vem antes da régua genérica (que só mostraria nas últimas 48 h).
  const js = transpile(trecho(cs, 'const janela = computed', '// ML pergunta: cada pergunta')) + '\nreturn janela'
  const janela = (c, agora) => new Function('computed', 'conversa', 'agora', 'janelaDoZap', 'fmtDataHora', 'duracao', js)(
    Vue.computed, Vue.ref(c), Vue.ref(agora), P.janelaDoZap, P.fmtDataHora, P.duracao,
  ).value
  const agora = Date.now()
  const daqui = (h) => new Date(agora + h * 3600000).toISOString()
  assert.equal(janela({ ...zapConversa, pode_enviar_ate: daqui(20) }, agora).nivel, 'aberta', 'no Zap a faixa aparece sempre')
  assert.equal(janela({ ...zapConversa, pode_enviar_ate: daqui(1) }, agora).nivel, 'vencendo')
  assert.match(janela({ ...zapConversa, pode_enviar_ate: daqui(-1) }, agora).texto, /só aceita um modelo aprovado/)
  // Fora do Zap, a régua de sempre: âmbar com menos de 48 h; com 60 h, nada.
  assert.equal(janela({ ...zapConversa, plataforma: 'shopee', canal: 'chat', pode_enviar_ate: daqui(60) }, agora), null)
  assert.match(janela({ ...zapConversa, plataforma: 'shopee', canal: 'chat', pode_enviar_ate: daqui(20) }, agora).texto, /^Dá para responder até/)

  // Na cadeia de faixas, nenhum ramo ANTES da janela pega o Zap.
  const cadeia = trecho(tela, '<!-- faixas: bloqueio, janela', 'v-else-if="janela"')
  const condicoes = [...cadeia.matchAll(/<div\s+v-(?:else-)?if="([^"]*)"/g)].map((m) => m[1])
  assert.deepEqual(condicoes, [
    "conversa.canal === 'reclamacao'",
    "conversa.canal === 'avaliacao'",
    "canalExterno === 'carrinho'",
    "canalExterno === 'comentario'",
    "conversa.situacao === 'bloqueada'",
  ], 'reclamação, avaliação, carrinho, comentário e bloqueada — nada do Zap')
  assert.match(tela, /<div v-else-if="janela" class="shrink-0 border-b px-3 py-1\.5 text-xs" :class="janela\.cls">\{\{ janela\.texto \}\}<\/div>/)
}
{
  // O painel do pedido e companhia: `semPainelDePedido` = carrinho/comentário OU Zap.
  const js = transpile(trecho(cs, 'const canalExterno = computed', '// O cartão do topo, aberto')) + '\nreturn { canalExterno, ehZap, semPainelDePedido }'
  const estado = (c) => new Function('computed', 'conversa', js)(Vue.computed, Vue.ref(c))
  const z = estado(zapConversa)
  assert.deepEqual([z.canalExterno.value, z.ehZap.value, z.semPainelDePedido.value], [null, true, true], 'o Zap não amplia o canalExterno')
  const sh = estado({ ...zapConversa, plataforma: 'shopee', canal: 'chat' })
  assert.deepEqual([sh.canalExterno.value, sh.ehZap.value, sh.semPainelDePedido.value], [null, false, false])
  for (const canal of ['carrinho', 'comentario']) {
    const e = estado({ ...zapConversa, plataforma: 'site', canal })
    assert.deepEqual([e.canalExterno.value, e.semPainelDePedido.value], [canal, true], `${canal}: como antes`)
  }
  assert.match(cs, /const CANAIS_SEM_PEDIDO = new Set\(\['carrinho', 'comentario', 'zap'\]\)/, 'sem reclamação nem avaliação de pedido para buscar')
  // Onde some (e só aí): o painel, a gaveta, o botão Pedido, o "ver no pedido",
  // o "Pausar IA" e o AdsPower.
  assert.match(tela, /<aside\s+v-if="detalhe && pedidoVisivel && !semPainelDePedido"/)
  assert.match(tela, /<div v-if="gaveta && !telaLarga && !semPainelDePedido" class="fixed inset-0/)
  assert.match(tela, /<Button v-else-if="!semPainelDePedido" size="sm"[^>]*@click="alternarPedido">/)
  assert.match(tela, /<button v-if="!pedidoVisivel && !semPainelDePedido"[^>]*>ver no pedido<\/button>/)
  assert.match(tela, /v-if="!semPainelDePedido"\s+size="sm"[\s\S]{0,300}pausar a IA/)
  assert.match(tela, /<AtendimentoAdsPower\s+v-if="!conversa\.somente_leitura && !semPainelDePedido"/)
  assert.ok(!/canalExterno \|\| ehZap|ehZap \|\| canalExterno/.test(tela), 'o template usa o semPainelDePedido, não a conta de novo')
  // A caixa de resposta CONTINUA no Zap: o aviso "nada é mandado" é só do carrinho/comentário.
  assert.match(tela, /v-else-if="canalExterno"\s+class="[^"]*"\s+data-caixa-externa/)
  assert.ok(!/semPainelDePedido"[^>]*data-caixa-externa/.test(tela))
}
{
  // O selo do canal ao lado do número.
  const js = transpile(trecho(cs, 'const seloCanal = computed', '// ─── avaliação de venda')) + '\nreturn seloCanal'
  const selo = (c) => new Function('computed', 'conversa', 'ehMencao', 'canalLabel', 'variasCaixas', js)(
    Vue.computed, Vue.ref(c), Vue.ref(false), P.canalLabel, P.variasCaixas,
  ).value
  assert.equal(selo(zapConversa), 'Zap')
  assert.equal(selo({ ...zapConversa, plataforma: 'shopee', canal: 'chat' }), '', 'a Shopee continua sem selo')
}
{
  // Sem "Sugerir": a IA fica fora do Zap (P8) — o backend recusaria.
  const js = transpile(trecho(cs, 'const podeSugerir = computed', '// Por que não veio sugestão')) + '\nreturn podeSugerir'
  const pode = (c) => {
    const props = { canEdit: true, canSugerir: true, flags: { ia_ativa: true } }
    return new Function('computed', 'props', 'conversa', 'rascunho', 'canAvaliar', js)(
      Vue.computed, props, Vue.ref(c), Vue.ref(null), Vue.computed(() => true),
    ).value
  }
  assert.equal(pode(zapConversa), false)
  assert.equal(pode({ ...zapConversa, plataforma: 'shopee', canal: 'chat' }), true, 'nas outras, como antes')
}
{
  // A caixa de resposta diz "Responda pelo Duoke": a frase do backend
  // (MOTIVO_ZAP_SEM_ENVIO, que não muda na Onda 1), sem tradução na tela.
  const bloco = (constantes.match(/^MOTIVO_ZAP_SEM_ENVIO = \(([\s\S]*?)^\)/m) || [])[1] || ''
  const frase = [...bloco.matchAll(/"([^"]*)"/g)].map((x) => x[1]).join('')
  assert.match(frase, /Responda pelo Duoke\.$/)
  assert.equal(P.ERROS.canal_sem_envio, undefined)
  const js = transpile(trecho(cs, '// Por que não dá para responder, em linguagem simples.', 'const podeDigitar')) + '\nreturn bloqueioEnvio'
  const bloqueio = (d, props) => new Function(
    'computed', 'detalhe', 'ehEmail', 'sellerCenterDe', 'motivoSellerCenter', 'AVISO_SO_LEITURA', 'props', 'avaliacaoDaConversa',
    'TRAVAS_GERAIS_DO_EMAIL', 'motivoLegivel', 'ERROS', 'travaEmail', 'CODIGOS_CONFIRMAVEIS', 'bloqueioLegivel', 'semConta', js,
  )(
    Vue.computed, Vue.ref(d), Vue.ref(false), P.sellerCenterDe, P.motivoSellerCenter, P.AVISO_SO_LEITURA, props, Vue.ref(null),
    new Set(), P.motivoLegivel, P.ERROS, Vue.ref(null), new Set(), C.bloqueioLegivel, Vue.ref(false),
  ).value
  const envio = { pode_enviar: false, codigo: 'canal_sem_envio', motivo: frase, limite_caracteres: 4096, modo: 'observar', sla_horas: 24, modo_observacao: false }
  const props = { canEdit: true, flags: { envio_ativo: true, zap_envio_ativo: false } }
  assert.equal(bloqueio({ conversa: zapConversa, envio }, props), frase, 'a caixa à mostra, travada com "Responda pelo Duoke"')
  assert.equal(bloqueio({ conversa: zapConversa, envio }, { ...props, canEdit: false }), P.AVISO_SO_LEITURA, 'quem só lê: a frase de sempre')
}
{
  // Os tipos do WhatsApp e a mídia ainda sem a nossa cópia.
  const js = transpile(trecho(cs, 'const TIPO_LABEL', '// Links no texto viram') + '\n' + trecho(cs, 'type Peca', '// Uma vez por leitura da conversa'))
    + '\nreturn { TIPO_LABEL, pecasDe }'
  const { TIPO_LABEL, pecasDe } = new Function('cartaoPedidoDe', 'cartaoProdutoDe', js)(P.cartaoPedidoDe, P.cartaoProdutoDe)
  assert.deepEqual(
    ['audio', 'localizacao', 'contato', 'figurinha', 'botao'].map((t) => TIPO_LABEL[t]),
    ['áudio', 'localização', 'contato', 'figurinha', 'botão'],
  )
  const msg = (anexos) => ({ id: 'm1', anexos })
  const nomes = (anexos) => pecasDe(msg(anexos)).map((p) => [p.tipo, p.nome])
  assert.deepEqual(nomes([{ tipo: 'imagem', midia_id: 'a1b2', mime: 'image/jpeg', status: 'pendente' }]), [['nome', '[Foto — aguardando download]']])
  assert.deepEqual(nomes([{ tipo: 'audio', midia_id: 'a1b3', mime: 'audio/ogg', status: 'baixando' }]), [['nome', '[Áudio — aguardando download]']])
  assert.deepEqual(nomes([{ tipo: 'arquivo', midia_id: 'a1b4', status: 'pendente' }]), [['nome', '[Documento — aguardando download]']])
  assert.deepEqual(nomes([{ tipo: 'figurinha', midia_id: 'a1b5', status: 'pendente' }]), [['nome', '[Figurinha — aguardando download]']])
  assert.deepEqual(nomes([{ tipo: 'video', midia_id: 'a1b6', status: 'expirada' }]), [['nome', '[Vídeo — a mídia expirou antes de ser baixada]']])
  assert.deepEqual(nomes([{ tipo: 'video', midia_id: 'a1b7', status: 'grande_demais' }]), [['nome', '[Vídeo — grande demais para baixar]']])
  // Estado que o mapa não conhece (nem chave do protótipo): o nome de sempre.
  assert.deepEqual(nomes([{ tipo: 'imagem', midia_id: 'a1b8', status: 'constructor' }]), [['nome', 'imagem']])
  assert.deepEqual(nomes([{ tipo: 'constructor', midia_id: 'a1b9', status: 'pendente' }]), [['nome', '[Anexo — aguardando download]']])
  // Com link http(s) (as outras plataformas e, na Onda 3, a cópia): como antes.
  assert.deepEqual(pecasDe(msg([{ tipo: 'imagem', midia_id: 'x', status: 'pendente', url: 'https://cdn.exemplo/foto.jpg' }])).map((p) => p.tipo), ['imagem'])
  // Sem `midia_id` (as outras plataformas): o `status` não muda nada.
  assert.deepEqual(nomes([{ tipo: 'imagem', status: 'pendente' }]), [['nome', 'imagem']])
  assert.deepEqual(nomes([{ type: 'video' }]), [['nome', 'vídeo']])
}

// ------------------------------------------------ a faixa do topo da página
{
  const r = resumo()
  assert.equal(P.temZap(r), true)
  assert.deepEqual(P.avisoZap(r), {
    curto: 'Zap: envio desligado — responda pelo Duoke',
    texto: 'Zap (WhatsApp) — o envio pelo DaVinci está desligado: as conversas dos números de WhatsApp aparecem aqui só para leitura; responda pelo Duoke.',
  })
  assert.ok(P.avisoZap({ ...r, flags: { envio_ativo: true } }), 'o freio único ligado não basta')
  assert.ok(P.avisoZap({ ...r, flags: { zap_envio_ativo: true } }), 'a chave do Zap sozinha também não')
  assert.equal(P.avisoZap({ ...r, flags: { envio_ativo: true, zap_envio_ativo: true } }), null)
  // Sem número de WhatsApp no /resumo: nada.
  const semZap = { ...r, lojas: r.lojas.filter((l) => l.plataforma !== 'whatsapp'), canais: r.canais.filter((c) => c.plataforma !== 'whatsapp') }
  assert.equal(P.temZap(semZap), false)
  assert.equal(P.avisoZap(semZap), null)
  assert.equal(P.avisoZap(null), null)
  // Só o canal (o número ainda sem conversa) já conta.
  assert.equal(P.temZap({ lojas: [], canais: r.canais }), true)
  const pagina = fs.readFileSync(path.resolve(__dirname, '../pages/atendimento.vue'), 'utf8')
  assert.match(pagina, /const zap = avisoZap\(resumo\.value\)\s+if \(zap\) out\.push\(\{ chave: 'zap', \.\.\.zap \}\)/)
}

// ------------------------------------------------ Lojas e modo: só Observar, travado
{
  const canaisSfc = sfc('../components/AtendimentoCanais.vue')
  const js = transpile(trecho(canaisSfc.scriptSetup.content, 'function opcoesModo', 'function tituloModo')) + '\nreturn { opcoesModo, modoTravado, soAdmin }'
  const M = new Function('MODOS', 'MODOS_EXTERNOS', 'sellerCenterDe', 'props', js)(P.MODOS, P.MODOS_EXTERNOS, P.sellerCenterDe, { isAdmin: true })
  const canal = { id: 'cz1', integration_id: null, externo_ref: 'zap:1111', plataforma: 'whatsapp', canal: 'zap', modo: 'observar' }
  assert.deepEqual(M.opcoesModo(canal).map((m) => m.value), ['observar'], 'nem admin escolhe outro modo na Onda 1')
  assert.equal(M.modoTravado(canal), true)
}

console.log('ok — atendimento-zap (Onda 1: vocabulário, ícone, barra, janela, conversa sem painel do pedido, faixa do topo)')
