// node tests/atendimento-nao-precisa-rodape.cjs — o "Não precisa de resposta" do lado do Enviar.
// 16/11/2026, o dono: "a notificacao agr so vai sair se responder no chat ou se clicar em um
// botao n precisa de resposta q bota do lado do enviar msg ali". Na conversa do NÚMERO da Caixa
// (`numero_duoke` é número) abrir não apaga mais a bolinha (atendimento-marcar-lida.cjs): ela sai
// com a resposta no chat ou com este botão. Aqui:
//  - a parte pura (lib/atendimentoLida.ts): quem vê o botão (`mostraNaoPrecisa`), o corpo do PATCH
//    (`corpoNaoPrecisa`: o `ate`), a pergunta da reclamação do ML, o title;
//  - o template do AtendimentoConversa: o botão na linha do Enviar, ENTRE o contador e o Enviar (o
//    ml-auto no grupo da direita), o balão da Amazon ali (side top, o único `balaoNaoPrecisa`), o
//    cabeçalho só com o do comentário, o "Não lida" escondido com o "não precisa" ligado, e o botão
//    embaixo do painel da observação (sem Enviar); o trecho renderizado de verdade;
//  - o setup de verdade (o trecho do PATCH): sem confirm no chat, confirm só na reclamação do ML,
//    o `ate`, o desfazer, o 409 `chegou_mensagem` (o aviso do servidor e a releitura) e a sugestão
//    intocada que sai da caixa.
// O contrato com o servidor (o PATCH aceita `ate` e responde 409 `chegou_mensagem`) é do backend
// (apps/api); a tela só manda e trata.
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

function loadLib(rel) {
  const exp = {}
  new Function('exports', 'require', transpile(fs.readFileSync(path.join(__dirname, rel), 'utf8')))(exp, require)
  return exp
}
function sfc(rel) {
  const filename = path.resolve(__dirname, rel)
  const fonte = fs.readFileSync(filename, 'utf8')
  const { descriptor, errors } = parse(fonte, { filename })
  assert.deepEqual(errors, [], rel)
  const script = compileScript(descriptor, { id: path.basename(rel), inlineTemplate: false })
  for (const bindingMetadata of [script.bindings, undefined]) {
    const tpl = compileTemplate({ source: descriptor.template.content, filename, id: path.basename(rel), compilerOptions: { isTS: true, bindingMetadata } })
    assert.deepEqual(tpl.errors, [], `${rel}: template compila`)
  }
  return { descriptor, fonte }
}
const icones = new Proxy({}, { get: (_, nome) => ({ name: String(nome), render: () => Vue.h('i', { 'data-icone': String(nome) }) }) })
const cache = new Map()
function requerer(nome) {
  if (nome === 'lucide-vue-next') return icones
  const m = /^~\/components\/(.+\.vue)$/.exec(nome)
  if (!m) return require(nome)
  if (!cache.has(m[1])) {
    const { descriptor } = parse(fs.readFileSync(path.resolve(__dirname, '../components', m[1]), 'utf8'))
    const mod = { exports: {} }
    if (descriptor.script) new Function('require', 'module', 'exports', transpile(descriptor.script.content))(requerer, mod, mod.exports)
    cache.set(m[1], mod.exports)
  }
  return cache.get(m[1])
}
// O pedaço do setup entre dois marcos, transpilado.
function trecho(src, ini, fim) {
  const a = src.indexOf(ini)
  const b = src.indexOf(fim, a + 1)
  assert.ok(a >= 0 && b > a, `trecho ${ini} … ${fim}`)
  return transpile(src.slice(a, b))
}
const semComentarios = (html) => html.replace(/<!--[\s\S]*?-->/g, '')
const tique = () => new Promise((r) => setImmediate(r))

const L = loadLib('../lib/atendimentoLida.ts')
const P = requerer('~/components/AtendimentoPlataforma.vue')
const conversaSfc = sfc('../components/AtendimentoConversa.vue')
const tpl = conversaSfc.descriptor.template.content
const setup = conversaSfc.descriptor.scriptSetup.content

// ───────────────────────── a parte pura (lib/atendimentoLida.ts)
const CHAT = {
  numero_duoke: 2, situacao: 'aberta', falta_responder: true, aguardando_resposta: true,
  sem_resposta_necessaria: false, somente_leitura: false, plataforma: 'shopee', canal: 'chat',
}
const QUEM_RESPONDE = { podeMexer: true, respondePorOutra: false, ehEmail: false }
{
  const ve = (c, o = {}) => L.mostraNaoPrecisa(c, { ...QUEM_RESPONDE, ...o })
  assert.equal(ve(CHAT), true, 'o chat do número com a bolinha')
  // Quem não vê.
  assert.equal(ve(CHAT, { podeMexer: false }), false, 'quem só lê (sem "responder")')
  assert.equal(ve({ ...CHAT, somente_leitura: true }), false, 'o Direct (só leitura)')
  assert.equal(ve(CHAT, { respondePorOutra: true }), false, 'a aba que responde por OUTRA conversa')
  assert.equal(ve(CHAT, { ehEmail: true }), false, 'o e-mail (site/ponte)')
  for (const [canal, numero] of [['avaliacao', null], ['carrinho', null], ['comentario', null], ['pergunta', null], ['chat', undefined]]) {
    assert.equal(ve({ ...CHAT, canal, numero_duoke: numero }), false, `fora do número (${canal}, ${numero})`)
  }
  assert.equal(ve({ ...CHAT, situacao: 'fechada' }), false, 'a fechada')
  assert.equal(ve({ ...CHAT, numero_duoke: 0, falta_responder: false, aguardando_resposta: false }), false, 'nada para tirar')
  assert.equal(L.mostraNaoPrecisa(null, QUEM_RESPONDE), false)
  // Basta uma pendência — ou a marca (vira "Precisa de resposta").
  const zerada = { ...CHAT, numero_duoke: 0, falta_responder: false, aguardando_resposta: false }
  assert.equal(ve({ ...zerada, numero_duoke: 1 }), true, 'só a bolinha')
  assert.equal(ve({ ...zerada, falta_responder: true }), true, 'só o Falta responder')
  assert.equal(ve({ ...zerada, aguardando_resposta: true }), true, 'só a vez da loja')
  assert.equal(ve({ ...zerada, sem_resposta_necessaria: true }), true, 'marcada: o desfazer')
  // Com a caixa travada (bloqueada, reclamação sem destino): é a única saída além de fechar.
  assert.equal(ve({ ...CHAT, situacao: 'bloqueada' }), true, 'a bloqueada')
  assert.equal(ve({ ...CHAT, plataforma: 'ml', canal: 'reclamacao' }), true, 'a reclamação do ML (no número)')
  for (const plataforma of ['amazon', 'magalu', 'temu', 'aliexpress', 'tiktok']) assert.equal(ve({ ...CHAT, plataforma }), true, plataforma)
  assert.equal(ve({ ...CHAT, canal: 'zap', plataforma: 'whatsapp' }), true, 'o Zap')

  // O corpo do PATCH: o `ate` da tela ao marcar; o desfazer só com o false.
  assert.deepEqual(L.corpoNaoPrecisa(true, '2026-11-16T10:00:00.1Z'), { sem_resposta_necessaria: true, ate: '2026-11-16T10:00:00.1Z' })
  assert.deepEqual(L.corpoNaoPrecisa(true, null), { sem_resposta_necessaria: true }, 'sem `visto_ate`, sem `ate`')
  assert.deepEqual(L.corpoNaoPrecisa(true, undefined), { sem_resposta_necessaria: true })
  assert.deepEqual(L.corpoNaoPrecisa(false, '2026-11-16T10:00:00.1Z'), { sem_resposta_necessaria: false })
  assert.equal(L.CODIGO_CHEGOU_MENSAGEM, 'chegou_mensagem')

  // A confirmação: só a reclamação do ML (o prazo do ML continua correndo).
  assert.match(L.perguntaNaoPrecisa({ plataforma: 'ml', canal: 'reclamacao' }), /^Marcar a reclamação como "não precisa de resposta"\?\n\n[\s\S]*prazo do Mercado Livre continua correndo/)
  for (const c of [{ plataforma: 'ml', canal: 'pos_venda' }, { plataforma: 'shopee', canal: 'chat' }, { plataforma: 'amazon', canal: 'mensagem' }, null]) {
    assert.equal(L.perguntaNaoPrecisa(c), null, JSON.stringify(c))
  }
  assert.equal(L.TITULO_NAO_PRECISA, 'Tira a notificação sem responder; volta sozinha se o cliente escrever de novo')

  // E o par dele: abrir a conversa do número não grava o visto (o resto, em atendimento-marcar-lida).
  assert.equal(L.querMarcarVisto({ conversa: { id: 'c1', numero_duoke: 3 } }), false)
  assert.equal(L.rotaAoAbrir({ conversa: { id: 'c1', numero_duoke: 3, nao_lidas: 1 }, marcar_lida: true }, true), 'lida')
  assert.equal(L.rotaAoAbrir({ conversa: { id: 'c1', numero_duoke: 3, nao_lidas: 1 }, marcar_lida: false }, true), null)
  assert.equal(L.rotaAoAbrir({ conversa: { id: 'e1', canal: 'email', numero_duoke: null }, marcar_lida: false }, true), 'visto')
}

// ───────────────────────── o template
{
  assert.match(setup, /import \{[^}]*\bmostraNaoPrecisa\b[^}]*\} from '~\/lib\/atendimentoLida'/)
  // A linha de ação: o grupo da direita (o ml-auto é dele) com o contador, o botão e o Enviar.
  const iLinha = tpl.indexOf('data-linha-de-acao')
  assert.ok(iLinha > 0, 'a linha de ação')
  assert.match(tpl, /<div v-if="!observacao \|\| naoPrecisaNoRodape" class="flex flex-wrap items-center gap-1\.5" data-linha-de-acao>/)
  const fimLinha = tpl.indexOf('</template>\n        </div>', iLinha)
  assert.ok(fimLinha > iLinha)
  const linha = tpl.slice(tpl.lastIndexOf('<div', iLinha), fimLinha)
  const iGrupo = linha.indexOf('<div class="ml-auto flex items-center gap-1.5" data-acoes-do-envio>')
  const iContador = linha.indexOf('{{ tamanho }}/{{ limite }}')
  const iBotao = linha.indexOf('data-nao-precisa')
  const iEnviar = linha.indexOf('<Send v-else class="mr-1.5 size-4" />')
  assert.ok(iGrupo > 0 && iGrupo < iContador && iContador < iBotao && iBotao < iEnviar, 'contador < "Não precisa" < Enviar, no grupo da direita')
  assert.ok(linha.indexOf('Sugerir resposta') < iGrupo, 'o "Sugerir resposta" fica à esquerda')
  assert.equal((semComentarios(linha).match(/ml-auto/g) || []).length, 1, 'um ml-auto só: o do grupo da direita')
  assert.doesNotMatch(linha, /:class="limite && podeDigitar \? '' : 'ml-auto'"/, 'o Enviar não leva mais o ml-auto')
  // O botão: o mesmo estilo/altura do Enviar e dos vizinhos; foco visível vem do Button.
  const botao = (linha.match(/<Button\s+v-else-if="naoPrecisaNoRodape"[\s\S]*?<\/Button>/) || [])[0]
  assert.ok(botao, 'o botão genérico')
  assert.match(botao, /size="sm"\s+variant="outline"\s+class="h-8 px-2 text-xs"/)
  assert.match(botao, /:disabled="!!acao"/, 'travado só pela ação em curso — não pela caixa')
  assert.match(botao, /:title="tituloNaoPrecisa"/)
  assert.match(botao, /:aria-label="conversa\.sem_resposta_necessaria \? 'Precisa de resposta' : 'Não precisa de resposta'"/)
  assert.match(botao, /@click="naoPrecisaDoRodape"/)
  assert.match(botao, /<Loader2 v-if="acao === 'nao_precisa'"[^>]*\/>\s*<Undo2 v-else-if="conversa\.sem_resposta_necessaria"[^>]*\/>\s*<MailX v-else[^>]*\/>/)
  assert.match(botao, /<span class="ml-1 sm:hidden">Não precisa<\/span>\s*<span class="ml-1 hidden sm:inline">Não precisa de resposta<\/span>/, 'rótulo curto em tela estreita')
  assert.match(fs.readFileSync(path.resolve(__dirname, '../components/ui/button/index.ts'), 'utf8'), /focus-visible:ring-2/, 'o Button tem o anel de foco')
  // A Amazon com o link: o balão (o mesmo conteúdo do cabeçalho de antes), abrindo para cima.
  assert.equal((tpl.match(/v-model:open="balaoNaoPrecisa"/g) || []).length, 1, 'UM balão do "não precisa" na tela inteira')
  const balao = (linha.match(/<PopoverRoot v-if="naoPrecisaComBalao" v-model:open="balaoNaoPrecisa">[\s\S]*?<\/PopoverRoot>/) || [])[0]
  assert.ok(balao, 'o balão da Amazon está na linha do Enviar')
  assert.ok(linha.indexOf(balao) > iContador && linha.indexOf(balao) < iEnviar)
  assert.match(balao, /side="top"\s+align="end"/)
  assert.match(balao, /@click="naoPrecisaSoAqui">só marcar aqui<\/button>/)
  assert.match(balao, /<Button as="a" size="sm" class="h-7 px-2 text-xs" :href="amazon\.semResposta" target="_blank" rel="noopener noreferrer" @click="naoPrecisaComLink">[\s\S]*?Marcar e abrir na Amazon/)
  assert.match(balao, /\("Não é necessária resposta"\) — sem isso a Amazon conta a mensagem como atrasada/)
  assert.match(balao, /data-nao-precisa/)
  // O cabeçalho: só o do comentário, com o texto e o confirm dele; nada da Amazon.
  const iCab = tpl.indexOf('<template v-if="!conversa.somente_leitura && canEdit">')
  const fimCab = tpl.indexOf('</template>', tpl.indexOf('fecharOuReabrir', iCab))
  const cabecalho = tpl.slice(iCab, fimCab)
  assert.doesNotMatch(cabecalho, /balaoNaoPrecisa|PopoverRoot|naoPrecisaSoAqui|naoPrecisaComLink|data-nao-precisa/)
  assert.match(cabecalho, /<Button\s+v-if="canalExterno === 'comentario'"[\s\S]*?:title="conversa\.sem_resposta_necessaria \? 'desfazer: volta para a fila' : 'não precisa de resposta \(elogio, emoji\): sai do Falta responder, continua no Mídia'"[\s\S]*?@click="naoPrecisa"/)
  // O "Não lida" some na conversa do NÚMERO (ali só desfaria o corte do "não
  // precisa"); no e-mail (fora do número) ele continua.
  assert.match(tpl, /<Button\s+v-if="naoLidaVisivel"[\s\S]*?data-nao-lida\s+@click="marcarNaoLida"/)
  assert.match(setup, /const naoLidaVisivel = computed\(\(\) => \{\s*const c = conversa\.value\s*return !!c && !c\.somente_leitura && typeof c\.numero_duoke !== 'number'\s*\}\)/)
  // O balão some com o botão: o ref volta a false (não reabre sozinho).
  assert.match(setup, /watch\(naoPrecisaComBalao, \(v\) => \{ if \(!v\) balaoNaoPrecisa\.value = false \}\)/)
  // O Fechar manda o `ate` (o corte para no que a tela mostrava).
  assert.match(setup, /const ate = detalhe\.value\?\.visto_ate\s*return patch\(\{ situacao: 'fechada', \.\.\.\(ate \? \{ ate \} : \{\}\) \}, 'fechar', 'Conversa fechada'\)/)
  // A observação (sem Enviar): o painel e, logo embaixo, a mesma linha — só com o botão.
  const iNota = tpl.indexOf('<AtendimentoNota\n            v-if="modoCaixa === \'nota\'')
  const iAba = tpl.indexOf('<AtendimentoAbaResposta')
  const iExterna = tpl.indexOf('data-caixa-externa')
  const iObs = tpl.indexOf('<AtendimentoObservacao\n            v-if="observacao"')
  const iCaixa = tpl.indexOf('ref="caixa"')
  assert.ok(iNota > 0 && iNota < iAba && iAba < iExterna && iExterna < iObs && iObs < iCaixa && iCaixa < iLinha,
    'nota interna > caixa da aba > canais de fora > (observação | caixa) > a linha de ação')
  assert.match(tpl.slice(iExterna, iObs), /<\/div>\n\n {10}<template v-else>\n/, 'observação e caixa dentro do mesmo v-else: a nota interna esconde a linha')
  assert.match(linha, /<template v-if="!observacao">[\s\S]*?Respostas prontas[\s\S]*?Sugerir resposta[\s\S]*?<\/template>/, 'na observação, sem respostas prontas nem sugerir (o painel já tem)')
  assert.match(linha, /<span\s+v-if="!observacao && limite && podeDigitar"/)
  assert.match(linha, /<Button\s+v-if="!observacao"\s+size="sm"\s+class="h-8"\s+:disabled="!podeEnviar"/)
}

// ───────────────────────── o trecho renderizado (a linha de ação, com dublês)
async function renderizarLinha(ctx) {
  const ini = tpl.lastIndexOf('<div', tpl.indexOf('data-linha-de-acao'))
  const fim = tpl.indexOf('</template>\n        </div>', ini)
  const fonte = tpl.slice(ini, fim)
  const { code, errors } = compileTemplate({ source: `<div>${fonte}</div>`, filename: 'linha.vue', id: 'linha', compilerOptions: { isTS: true } })
  assert.deepEqual(errors, [], 'o trecho compila')
  const tmod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(code))(requerer, tmod, tmod.exports)
  const a = Vue.createSSRApp({ setup: () => ctx, render: tmod.exports.render })
  a.config.warnHandler = () => {}
  const passa = (tag) => ({ inheritAttrs: false, setup: (_p, { slots, attrs }) => () => Vue.h(tag, attrs, slots.default?.()) })
  a.component('Button', {
    props: ['disabled', 'as', 'href', 'size', 'variant'],
    inheritAttrs: false,
    setup: (p, { slots, attrs }) => () => Vue.h(p.as === 'a' ? 'a' : 'button', { ...attrs, href: p.href, disabled: p.disabled || undefined, 'data-variant': p.variant }, slots.default?.()),
  })
  a.component('PopoverRoot', { setup: (_p, { slots }) => () => Vue.h('span', { 'data-popover': '' }, slots.default?.()) })
  a.component('PopoverTrigger', { setup: (_p, { slots }) => () => slots.default?.() })
  a.component('PopoverPortal', { setup: (_p, { slots }) => () => slots.default?.() })
  a.component('PopoverContent', { props: ['side', 'align'], setup: (p, { slots, attrs }) => () => Vue.h('div', { ...attrs, 'data-side': p.side, 'data-align': p.align }, slots.default?.()) })
  for (const nome of new Set([...fonte.matchAll(/<([A-Z][A-Za-z0-9]*)/g)].map((m) => m[1]))) {
    if (!['Button', 'PopoverRoot', 'PopoverTrigger', 'PopoverPortal', 'PopoverContent'].includes(nome)) a.component(nome, icones[nome])
  }
  return semComentarios(await renderToString(a))
}
function ctxDaLinha(extra = {}) {
  return {
    observacao: false, naoPrecisaNoRodape: true, naoPrecisaComBalao: false, modelosQueServem: [], menuModelos: false,
    podeDigitar: true, buscaModelo: '', teclaDoMenu: () => {}, modelosDaConversa: [], menuIdx: 0, inserirModelo: () => {},
    plataformaInfo: P.plataformaInfo, emit: () => {}, podeSugerir: true, ehEmail: false, gerando: false, sugerir: () => {},
    limite: 1000, acimaDoLimite: false, tamanho: 12, variasCaixas: P.variasCaixas, canalLabel: P.canalLabel,
    balaoNaoPrecisa: false, acao: null, tituloNaoPrecisa: L.TITULO_NAO_PRECISA, naoPrecisaSoAqui: () => {},
    amazon: { semResposta: null, caso: null }, naoPrecisaComLink: () => {}, naoPrecisaDoRodape: () => {},
    podeEnviar: true, incertos: new Map(), conversaId: 'c1', enviar: () => {}, caixaPresa: false,
    conversa: { id: 'c1', plataforma: 'shopee', canal: 'chat', sem_resposta_necessaria: false },
    ...extra,
  }
}
;(async () => {
  {
    const h = await renderizarLinha(ctxDaLinha())
    const iC = h.indexOf('12/1000')
    const iB = h.indexOf('data-nao-precisa')
    const iE = h.indexOf('data-icone="Send"')
    assert.ok(iC > 0 && iC < iB && iB < iE, 'na tela: contador, "Não precisa", Enviar')
    assert.match(h, /<button[^>]*title="Tira a notificação sem responder; volta sozinha se o cliente escrever de novo"[^>]*aria-label="Não precisa de resposta"[^>]*data-nao-precisa[^>]*>/)
    assert.match(h, /data-icone="MailX"[\s\S]*?Não precisa de resposta/)
    assert.match(h, /Sugerir resposta/)
  }
  // Marcada: vira o desfazer.
  {
    const h = await renderizarLinha(ctxDaLinha({ conversa: { id: 'c1', plataforma: 'shopee', canal: 'chat', sem_resposta_necessaria: true }, tituloNaoPrecisa: 'desfaz' }))
    assert.match(h, /aria-label="Precisa de resposta"/)
    assert.match(h, /data-icone="Undo2"/)
    assert.match(h, /<span class="ml-1">Precisa de resposta<\/span>/)
    assert.doesNotMatch(h, /Não precisa de resposta<\/span>/)
  }
  // Salvando: gira e trava.
  {
    const h = await renderizarLinha(ctxDaLinha({ acao: 'nao_precisa' }))
    assert.match(h, /<button(?=[^>]*\bdisabled\b)[^>]*data-nao-precisa[^>]*>\s*<i data-icone="Loader2"/)
  }
  // Sem o botão (fora do número, só leitura…): a linha de sempre.
  {
    const h = await renderizarLinha(ctxDaLinha({ naoPrecisaNoRodape: false }))
    assert.doesNotMatch(h, /data-nao-precisa/)
    assert.match(h, /data-icone="Send"/)
    assert.match(h, /data-acoes-do-envio/)
  }
  // A caixa travada (o Enviar desligado): o botão continua clicável.
  {
    const h = await renderizarLinha(ctxDaLinha({ podeDigitar: false, podeEnviar: false }))
    assert.match(h, /<button(?![^>]*disabled)[^>]*data-nao-precisa/)
    assert.match(h, /<button(?=[^>]*\bdisabled\b)[^>]*>\s*<i data-icone="Send"/)
  }
  // A observação: só o botão, embaixo do painel (sem Enviar, sem contador, sem sugerir).
  {
    const h = await renderizarLinha(ctxDaLinha({ observacao: true }))
    assert.match(h, /data-linha-de-acao/)
    assert.match(h, /data-nao-precisa/)
    assert.doesNotMatch(h, /data-icone="Send"|12\/1000|Sugerir resposta/)
    const sem = await renderizarLinha(ctxDaLinha({ observacao: true, naoPrecisaNoRodape: false }))
    assert.doesNotMatch(sem, /data-linha-de-acao/, 'na observação sem o botão, a linha some')
  }
  // A Amazon com o link: o balão (abre para cima, à direita), com os dois caminhos.
  {
    const link = 'https://sellercentral.amazon.com.br/messaging/inbox?fi=caseId&ss=1&cc=1'
    const h = await renderizarLinha(ctxDaLinha({ naoPrecisaComBalao: true, amazon: { semResposta: link, caso: null } }))
    assert.equal((h.match(/data-nao-precisa/g) || []).length, 1, 'um botão só (o do balão)')
    assert.match(h, /data-balao-nao-precisa[^>]*data-side="top" data-align="end"|data-side="top" data-align="end"[^>]*data-balao-nao-precisa/)
    assert.match(h, /só marcar aqui/)
    const hrefHtml = link.replace(/&/g, '&amp;').replace(/[.?]/g, (c) => `\\${c}`)
    assert.match(h, new RegExp(`<a[^>]*target="_blank"[^>]*rel="noopener noreferrer"[^>]*href="${hrefHtml}"[^>]*>[\\s\\S]*?Marcar e abrir na Amazon`))
    const i = h.indexOf('data-balao-nao-precisa')
    assert.ok(h.indexOf('12/1000') < i && i < h.indexOf('data-icone="Send"'))
  }

  // ───────────────────────── o setup de verdade: o trecho do PATCH e do "Não precisa"
  function montar({ conversa = {}, visto_ate = 'T1', texto = '', prefill = '', respostas = [], confirmar = true, modo = 'responder', canEdit = true, respondePorOutra = false, ehEmail = false } = {}) {
    const js = trecho(setup, 'const acoes = reactive(new Map', '// Amazon sem conta:')
      + '\nreturn { acao, acoes, patch, naoPrecisaNoRodape, naoPrecisaComBalao, tituloNaoPrecisa, balaoNaoPrecisa, naoPrecisaDoRodape, naoPrecisaComLink, naoPrecisaSoAqui, naoPrecisa }'
    const props = Vue.reactive({ conversaId: 'c1', canEdit, meuId: 'u1' })
    const c = { id: 'c1', plataforma: 'shopee', canal: 'chat', situacao: 'aberta', numero_duoke: 2, falta_responder: true, aguardando_resposta: true, sem_resposta_necessaria: false, somente_leitura: false, ...conversa }
    const detalhe = Vue.ref({ conversa: c, mensagens: [], visto_ate })
    const conversaRef = Vue.computed(() => detalhe.value?.conversa ?? null)
    const chamadas = []
    const avisos = []
    const relidas = []
    const perguntas = []
    const emitidos = []
    const fila = [...respostas]
    const api = async (url, opts) => {
      chamadas.push({ url, metodo: opts?.method, body: opts?.body })
      const r = fila.shift()
      if (r instanceof Error || r?.erro) throw r.erro || r
      if (typeof r === 'function') return r()
      return r ?? { conversa: { ...detalhe.value.conversa, ...(opts?.body?.sem_resposta_necessaria !== undefined ? { sem_resposta_necessaria: opts.body.sem_resposta_necessaria } : {}) } }
    }
    const g = {
      reactive: Vue.reactive, computed: Vue.computed, ref: Vue.ref, watch: Vue.watch, props, detalhe, api, comoObjeto: P.comoObjeto,
      emit: (e, x) => emitidos.push([e, x]), toasts: { success: (t) => avisos.push(['ok', t]), error: (t, m) => avisos.push(['erro', t, m]) },
      aberta: (id) => props.conversaId === id, carregar: async (id, silencioso) => { relidas.push([id, silencioso]) },
      titulo: (x) => x.comprador_nome || 'Comprador', erroDaApi: P.erroDaApi, CODIGO_CHEGOU_MENSAGEM: L.CODIGO_CHEGOU_MENSAGEM,
      conversa: conversaRef, linksAmazon: P.linksAmazon, modoCaixa: Vue.ref(modo), mostraNaoPrecisa: L.mostraNaoPrecisa,
      respondePorOutra: Vue.ref(respondePorOutra), ehEmail: Vue.ref(ehEmail), TITULO_NAO_PRECISA: L.TITULO_NAO_PRECISA,
      perguntaNaoPrecisa: L.perguntaNaoPrecisa, texto: Vue.ref(texto), prefill: Vue.ref(prefill), guardados: new Map(),
      corpoNaoPrecisa: L.corpoNaoPrecisa, confirm: (q) => { perguntas.push(q); return typeof confirmar === 'function' ? confirmar(q) : confirmar },
    }
    const nomes = Object.keys(g)
    const x = new Function(...nomes, js)(...nomes.map((n) => g[n]))
    return { ...x, g, props, detalhe, chamadas, avisos, relidas, perguntas, emitidos }
  }
  const PATCH_C1 = '/api/atendimento/conversas/c1'

  // O chat: um clique, sem confirm; o `ate`; relê; a sugestão intocada sai da caixa.
  {
    const m = montar({ texto: 'Olá! Já vou verificar.', prefill: 'Olá! Já vou verificar.' })
    assert.equal(m.naoPrecisaNoRodape.value, true)
    assert.equal(m.naoPrecisaComBalao.value, false)
    assert.equal(m.tituloNaoPrecisa.value, L.TITULO_NAO_PRECISA)
    await m.naoPrecisaDoRodape()
    assert.deepEqual(m.perguntas, [], 'o chat não pergunta')
    assert.deepEqual(m.chamadas, [{ url: PATCH_C1, metodo: 'PATCH', body: { sem_resposta_necessaria: true, ate: 'T1' } }])
    assert.deepEqual(m.avisos, [['ok', 'Marcada: não precisa de resposta']])
    assert.deepEqual(m.relidas, [['c1', true]])
    assert.equal(m.g.texto.value, '', 'um Enter não manda a sugestão')
    assert.equal(m.g.prefill.value, '')
    assert.equal(m.emitidos[0][0], 'mudou')
    assert.equal(m.detalhe.value.conversa.sem_resposta_necessaria, true)
    assert.equal(m.acao.value, null, 'a ação terminou')
  }
  // O que a pessoa escreveu fica.
  {
    const m = montar({ texto: 'Vou conferir o rastreio e já volto.', prefill: 'Olá! Já vou verificar.' })
    await m.naoPrecisaDoRodape()
    assert.equal(m.chamadas.length, 1)
    assert.equal(m.g.texto.value, 'Vou conferir o rastreio e já volto.')
  }
  // Sem `visto_ate` no detalhe: sem `ate`.
  {
    const m = montar({ visto_ate: null })
    await m.naoPrecisaDoRodape()
    assert.deepEqual(m.chamadas[0].body, { sem_resposta_necessaria: true })
  }
  // O desfazer ("Precisa de resposta"): só o false, sem confirm.
  {
    const m = montar({ conversa: { sem_resposta_necessaria: true, numero_duoke: 0, falta_responder: false, aguardando_resposta: false }, texto: 'x', prefill: 'x' })
    assert.equal(m.naoPrecisaNoRodape.value, true, 'marcada: o botão fica, para desfazer')
    assert.match(m.tituloNaoPrecisa.value, /^Precisa de resposta: desfaz/)
    await m.naoPrecisaDoRodape()
    assert.deepEqual(m.perguntas, [])
    assert.deepEqual(m.chamadas, [{ url: PATCH_C1, metodo: 'PATCH', body: { sem_resposta_necessaria: false } }])
    assert.deepEqual(m.avisos, [['ok', 'Voltou para a fila: precisa de resposta']])
    assert.equal(m.g.texto.value, 'x', 'desfazer não mexe na caixa')
  }
  // A reclamação do ML: pergunta antes (o prazo do ML). Cancelou: nada vai.
  {
    const conversa = { plataforma: 'ml', canal: 'reclamacao' }
    const m = montar({ conversa, confirmar: false })
    assert.match(m.tituloNaoPrecisa.value, /O prazo do Mercado Livre continua correndo/)
    await m.naoPrecisaDoRodape()
    assert.equal(m.perguntas.length, 1)
    assert.equal(m.perguntas[0], L.perguntaNaoPrecisa(conversa))
    assert.deepEqual(m.chamadas, [])
    const ok = montar({ conversa, confirmar: true })
    await ok.naoPrecisaDoRodape()
    assert.deepEqual(ok.chamadas[0].body, { sem_resposta_necessaria: true, ate: 'T1' })
  }
  // 409 `chegou_mensagem`: o aviso é a frase do servidor, a conversa é relida e a caixa fica.
  {
    const frase = 'O comprador escreveu de novo depois do que você viu — confira a mensagem nova.'
    const erro = Object.assign(new Error('409'), { statusCode: 409, data: { detail: { code: 'chegou_mensagem', detail: frase } } })
    const m = montar({ texto: 'sugestão', prefill: 'sugestão', respostas: [{ erro }] })
    await m.naoPrecisaDoRodape()
    assert.deepEqual(m.avisos, [['erro', frase, []]])
    assert.deepEqual(m.relidas, [['c1', true]], 'relê: a mensagem nova aparece')
    assert.equal(m.g.texto.value, 'sugestão', 'não marcou: a caixa fica')
    assert.equal(m.acao.value, null)
    assert.equal(m.detalhe.value.conversa.sem_resposta_necessaria, false)
    // Outra recusa (rede, 500): o aviso de sempre, sem reler.
    const m2 = montar({ respostas: [{ erro: Object.assign(new Error('500'), { statusCode: 500 }) }] })
    await m2.naoPrecisaDoRodape()
    assert.equal(m2.avisos[0][0], 'erro')
    assert.deepEqual(m2.relidas, [])
  }
  // Um clique por vez: com a ação em curso, nada (nem pergunta).
  {
    let solta
    const m = montar({ conversa: { plataforma: 'ml', canal: 'reclamacao' }, respostas: [() => new Promise((r) => { solta = r })] })
    const primeiro = m.naoPrecisaDoRodape()
    await tique()
    assert.equal(m.acao.value, 'nao_precisa')
    m.naoPrecisaDoRodape()
    assert.equal(m.perguntas.length, 1, 'o segundo clique não pergunta')
    assert.equal(m.chamadas.length, 1)
    solta({ conversa: { id: 'c1', sem_resposta_necessaria: true } })
    await primeiro
  }
  // Trocou de conversa enquanto o PATCH ia: a sugestão intocada sai da caixa GUARDADA dela.
  {
    let solta
    const m = montar({ respostas: [() => new Promise((r) => { solta = r })] })
    const indo = m.naoPrecisaDoRodape()
    await tique()
    m.g.guardados.set('c1', { texto: 'sugestão', prefill: 'sugestão', base: 'r1', rejeitada: null })
    m.props.conversaId = 'c2'
    solta({ conversa: { id: 'c1', sem_resposta_necessaria: true } })
    await indo
    assert.deepEqual(m.g.guardados.get('c1'), { texto: '', prefill: '', base: null, rejeitada: 'r1' })
    assert.deepEqual(m.relidas, [], 'a outra conversa não é relida')
    assert.match(m.avisos[0][1], /\(Comprador\)$/, 'o aviso diz de qual conversa')
  }
  // Quem vê (o computed): a lib + a aba Responder.
  {
    assert.equal(montar({ modo: 'nota' }).naoPrecisaNoRodape.value, false, 'na Nota interna a linha some')
    assert.equal(montar({ canEdit: false }).naoPrecisaNoRodape.value, false, 'quem só lê')
    assert.equal(montar({ respondePorOutra: true }).naoPrecisaNoRodape.value, false, 'a aba de outra conversa')
    assert.equal(montar({ ehEmail: true }).naoPrecisaNoRodape.value, false, 'o e-mail')
    assert.equal(montar({ conversa: { numero_duoke: null } }).naoPrecisaNoRodape.value, false, 'fora do número')
    assert.equal(montar({ conversa: { situacao: 'fechada' } }).naoPrecisaNoRodape.value, false, 'a fechada')
  }
  // A Amazon com o link: o balão; os dois caminhos fecham o balão e mandam o `ate`.
  {
    const conversa = { plataforma: 'amazon', canal: 'mensagem', amazon_link_sem_resposta: 'https://sellercentral.amazon.com.br/messaging/inbox?fi=caseId&ss=1&cc=1' }
    const m = montar({ conversa, texto: 's', prefill: 's' })
    assert.equal(m.naoPrecisaComBalao.value, true)
    assert.match(m.tituloNaoPrecisa.value, /O balão também abre a Amazon/)
    m.balaoNaoPrecisa.value = true
    await m.naoPrecisaComLink()
    assert.equal(m.balaoNaoPrecisa.value, false)
    assert.deepEqual(m.chamadas[0].body, { sem_resposta_necessaria: true, ate: 'T1' })
    assert.deepEqual(m.avisos, [['ok', 'Marcada: não precisa de resposta — confira na aba da Amazon']])
    assert.equal(m.g.texto.value, '')
    const so = montar({ conversa })
    so.balaoNaoPrecisa.value = true
    await so.naoPrecisaSoAqui()
    assert.equal(so.balaoNaoPrecisa.value, false)
    assert.deepEqual(so.chamadas[0].body, { sem_resposta_necessaria: true, ate: 'T1' })
    assert.deepEqual(so.avisos, [['ok', 'Marcada: não precisa de resposta — marque também no Seller Central']])
    assert.deepEqual(so.perguntas, [], 'o balão já foi a confirmação')
    // Marcada, o balão some: fica o desfazer.
    assert.equal(montar({ conversa: { ...conversa, sem_resposta_necessaria: true } }).naoPrecisaComBalao.value, false)
    // Sem o link: o botão de sempre, que lembra do Seller Central.
    const semLink = montar({ conversa: { plataforma: 'amazon', canal: 'mensagem' } })
    assert.equal(semLink.naoPrecisaComBalao.value, false)
    assert.match(semLink.tituloNaoPrecisa.value, /Seller Central/)
    await semLink.naoPrecisaDoRodape()
    assert.deepEqual(semLink.perguntas, [])
    assert.deepEqual(semLink.avisos, [['ok', 'Marcada: não precisa de resposta — marque também no Seller Central']])
  }
  // O do comentário (cabeçalho): o confirm e o texto de sempre, sem `ate` (fora do número).
  {
    const m = montar({ conversa: { canal: 'comentario', plataforma: 'instagram', numero_duoke: null } })
    assert.equal(m.naoPrecisaNoRodape.value, false, 'o comentário não tem o do rodapé')
    await m.naoPrecisa()
    assert.equal(m.perguntas.length, 1)
    assert.match(m.perguntas[0], /^Marcar como "não precisa de resposta"\?\n\nA conversa sai da fila de aguardando \(continua na lista e no filtro Mídia\)\. Nada é publicado na rede\.$/)
    assert.deepEqual(m.chamadas[0].body, { sem_resposta_necessaria: true })
  }

  console.log('ok — atendimento-nao-precisa-rodape')
})().catch((e) => {
  console.error(e)
  process.exit(1)
})
