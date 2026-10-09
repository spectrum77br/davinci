// node tests/atendimento-externos.cjs — a BASE do carrinho dos sites e das redes (02/10/2026).
// O que a tela tem de garantir antes das frentes:
//  - as plataformas novas (site, facebook): nome, grupo "Sites", fora do
//    manual/modelos, o modo que a aba Lojas aceita (igual ao backend);
//  - a barra de lojas: o grupo Sites e UMA linha por conta de rede (o Direct
//    e os comentários juntos), cada uma filtrando a lista pela origem
//    (`externo_ref`) ou pela conta (`rede_social_id`); sem a linha única
//    "Direct" quando a API manda as contas;
//  - a página manda e lembra os filtros novos; o cartão do carrinho/da
//    publicação vai no TOPO da conversa (o painel do pedido não abre nesses
//    canais), com faixa, selo e caixa coerentes; os ícones novos existem.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { parse, compileTemplate } = require('vue/compiler-sfc')

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

const P = exportsDe(sfc('../components/AtendimentoPlataforma.vue'))
const lojasSfc = sfc('../components/AtendimentoLojas.vue')
// As funções puras do <script> da barra (o corte pela plataforma do topo,
// 09/10/2026) — o trecho do setup usa elas.
const LJ = (() => {
  const F = exportsDe(sfc('../components/AtendimentoFiltroPlataforma.vue'))
  const req = (n) => (n === '~/components/AtendimentoFiltroPlataforma.vue' ? F : n === '~/components/AtendimentoPlataforma.vue' ? P : require(n))
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(lojasSfc.script.content))(req, mod, mod.exports)
  return mod.exports
})()
const CORTE = ['corteDaBarra', 'gruposVisiveis', 'nomeDoCorte', 'ativaTodasDoCorte', 'filtrosTodasDoCorte', 'numeroDoCorte', 'filtrosDoGrupo']
sfc('../components/AtendimentoCarrinho.vue')
sfc('../components/AtendimentoPublicacao.vue')
sfc('../components/AtendimentoIconePlataforma.vue')
const constantes = fs.readFileSync(path.resolve(__dirname, '../../api/app/services/atendimento/constantes.py'), 'utf8')

// ------------------------------------------------ plataformas novas
assert.equal(P.plataformaInfo('site').nome, 'Site')
assert.equal(P.plataformaInfo('facebook').nome, 'Facebook')
assert.equal(P.nomeDoGrupo('site'), 'Sites', 'o grupo da barra no plural')
assert.equal(P.nomeDoGrupo('instagram'), 'Instagram')
assert.equal(P.canalLabel('carrinho'), 'Carrinho')
assert.equal(P.canalLabel('comentario'), 'Comentário')
for (const p of ['site', 'facebook', 'instagram']) {
  assert.ok(!P.PLATAFORMAS_COM_CANAL.some((x) => x.value === p), `${p} fora do manual e dos modelos`)
}
// O modo do canal externo é o mesmo do backend (constantes.MODOS_EXTERNOS).
const bloco = (constantes.match(/^MODOS_EXTERNOS[^=]*= \{([\s\S]*?)^\}/m) || [])[1] || ''
const nomesModo = Object.fromEntries([...constantes.matchAll(/^(MODO_[A-Z]+) = "([a-z]+)"$/gm)].map((m) => [m[1], m[2]]))
const nomesPlat = Object.fromEntries([...constantes.matchAll(/^(PLATAFORMA_[A-Z]+) = "([a-z]+)"$/gm)].map((m) => [m[1], m[2]]))
const modosApi = Object.fromEntries([...bloco.matchAll(/(PLATAFORMA_[A-Z]+): \(([^)]*)\)/g)].map((m) => [
  nomesPlat[m[1]],
  m[2].split(',').map((x) => x.trim()).filter(Boolean).map((x) => nomesModo[x]),
]))
assert.deepEqual(modosApi, P.MODOS_EXTERNOS, 'a aba Lojas aceita o mesmo modo que o PATCH /canais')

// ------------------------------------------------ a barra de lojas
const setup = lojasSfc.scriptSetup.content
const inicio = setup.indexOf('const ORDEM')
const fim = setup.indexOf('function contador(')
assert.ok(inicio > 0 && fim > inicio)
const fns = setup.slice(setup.indexOf('function escolherTodas'))
function barra(resumo, filtrosIniciais = {}) {
  const filtros = Vue.ref({ plataforma: '', integration_id: '', canal: '', filtro: 'todas', q: '', externo_ref: '', rede_social_id: '', ...filtrosIniciais })
  const corpo = transpile(setup.slice(inicio, fim) + '\n' + fns) + '\nreturn { lojas, grupos, mostrarInstagram, escolherLoja, ativaLoja, escolherPlataforma, ativaPlataforma }'
  const r = new Function('computed', 'props', 'filtros', 'viaRobo', 'statusCanalCodigo', 'semLeitura', 'leituraParada', 'nomeDoGrupo', 'plataformaInfo', 'sellerCenterDe', 'statusCanalInfo', ...CORTE, corpo)(
    Vue.computed, { resumo }, filtros, P.viaRobo, P.statusCanalCodigo, P.semLeitura, P.leituraParada, P.nomeDoGrupo, P.plataformaInfo, P.sellerCenterDe, P.statusCanalInfo, ...CORTE.map((n) => LJ[n]),
  )
  return { ...r, filtros }
}
const resumo = {
  plataformas: [
    { plataforma: 'shopee', aguardando: 1, vencendo: 0, vencidas: 0 },
    { plataforma: 'site', aguardando: 1, vencendo: 0, vencidas: 0 },
    { plataforma: 'instagram', aguardando: 3, vencendo: 0, vencidas: 0 },
  ],
  lojas: [
    { integration_id: 'i1', plataforma: 'shopee', conta: 'ATV', aguardando: 1, vencidas: 0, status_canal: 'ok' },
    { integration_id: null, canal_id: 'c1', externo_ref: 'site:charlots', plataforma: 'site', conta: 'Charlots', aguardando: 1, vencidas: 0, status_canal: 'ok' },
    { integration_id: null, canal_id: 'c2', externo_ref: 'rede:instagram:1784', rede_social_id: 'rs-ch', plataforma: 'instagram', conta: '@charlots_br', aguardando: 2, vencidas: 0, direct_aguardando: 1, status_canal: 'sem_escopo', status_motivo: 'Sem permissão' },
    { integration_id: null, rede_social_id: 'rs-7b', plataforma: 'instagram', conta: '@7buyers_br', aguardando: 1, vencidas: 0, direct_aguardando: 1 },
    // Amazon sem conta: sem integração e sem origem — não vira linha.
    { integration_id: null, plataforma: 'amazon', conta: null, aguardando: 4, vencidas: 0 },
  ],
  canais: [],
  flags: {},
}
{
  const b = barra(resumo)
  const grupos = b.grupos.value
  assert.deepEqual(grupos.map((g) => g.plataforma), ['shopee', 'site', 'instagram'], 'na ordem: marketplaces, Sites, Instagram')
  assert.equal(grupos[1].nome, 'Sites')
  assert.deepEqual(grupos[2].lojas.map((l) => l.conta), ['@7buyers_br', '@charlots_br'], 'uma linha por conta')
  const charlots = grupos[2].lojas.find((l) => l.conta === '@charlots_br')
  assert.equal(charlots.direct, 1)
  assert.equal(charlots.rede_social_id, 'rs-ch')
  assert.equal(charlots.externo_ref, '', 'a conta filtra pela conta, não pela caixa')
  // Comentários sem escopo, mas com Direct esperando (lido): acesa com alerta
  // (frente C, 02/10/2026 — apagar esconderia a bolinha do Direct).
  assert.equal(charlots.apagada, false, 'sem escopo nos comentários + Direct esperando: acesa')
  assert.equal(charlots.parcial, true, 'com o alerta da caixa de comentários')
  assert.equal(b.mostrarInstagram.value, false, 'sem a linha única "Direct" quando a API manda as contas')
  assert.ok(!b.lojas.value.some((l) => l.plataforma === 'amazon'))
  // Clicar no site filtra pela origem; na conta, pela conta; voltar à plataforma limpa.
  b.escolherLoja(grupos[1].lojas[0])
  assert.deepEqual([b.filtros.value.plataforma, b.filtros.value.externo_ref, b.filtros.value.rede_social_id, b.filtros.value.integration_id], ['site', 'site:charlots', '', ''])
  assert.ok(b.ativaLoja(grupos[1].lojas[0]))
  assert.ok(!b.ativaPlataforma('site'))
  b.escolherLoja(charlots)
  assert.deepEqual([b.filtros.value.plataforma, b.filtros.value.externo_ref, b.filtros.value.rede_social_id], ['instagram', '', 'rs-ch'])
  assert.ok(b.ativaLoja(charlots))
  b.escolherLoja(grupos[0].lojas[0])
  assert.deepEqual([b.filtros.value.integration_id, b.filtros.value.externo_ref, b.filtros.value.rede_social_id], ['i1', '', ''])
  b.escolherPlataforma('instagram')
  assert.deepEqual([b.filtros.value.plataforma, b.filtros.value.rede_social_id, b.filtros.value.integration_id], ['instagram', '', ''])
  assert.ok(b.ativaPlataforma('instagram'))
}
{
  // API antiga (sem as contas): volta a linha única "Direct".
  const antigo = { ...resumo, lojas: resumo.lojas.filter((l) => l.plataforma === 'shopee') }
  assert.equal(barra(antigo).mostrarInstagram.value, true)
}

// ------------------------------------------------ página, conversa e ícones
const pagina = fs.readFileSync(path.resolve(__dirname, '../pages/atendimento.vue'), 'utf8')
assert.match(pagina, /if \(f\.externo_ref\) p\.set\('externo_ref', f\.externo_ref\)/)
assert.match(pagina, /if \(f\.rede_social_id\) p\.set\('rede_social_id', f\.rede_social_id\)/)
assert.match(pagina, /\['plataforma', 'integration_id', 'canal', 'externo_ref', 'rede_social_id'\]/, 'lembra os filtros novos')
const conversa = fs.readFileSync(path.resolve(__dirname, '../components/AtendimentoConversa.vue'), 'utf8')
// O cartão do carrinho/da publicação vai no TOPO (como o da reclamação), e o
// painel da direita (pedido) não abre nesses canais (integrador, 02/10/2026).
const blocoTopo = (conversa.match(/<div\s+v-if="canalExterno"\s+v-show="cartaoExternoAberto"[\s\S]*?<!-- mensagens -->/) || [])[0] || ''
assert.ok(blocoTopo, 'o cartão do topo existe, antes das mensagens')
assert.match(blocoTopo, /<AtendimentoCarrinho\s+v-if="canalExterno === 'carrinho'"[\s\S]*?\btopo\b/)
assert.match(blocoTopo, /<AtendimentoPublicacao\s+v-else[\s\S]*?\btopo\b/)
assert.match(blocoTopo, /@mudou="aoMudarPainelExterno"/)
assert.match(conversa, /<aside\s+v-if="detalhe && pedidoVisivel && !canalExterno"/)
const aside = (conversa.match(/<aside[\s\S]*?<\/aside>/) || [])[0] || ''
assert.ok(!/AtendimentoCarrinho|AtendimentoPublicacao/.test(aside), 'os cartões saíram do painel da direita')
assert.match(aside, /<AtendimentoPedido\s+:key="detalhe\.conversa\.id"/)
assert.match(conversa, /CANAIS_SEM_PEDIDO = new Set\(\['carrinho', 'comentario'\]\)/)
// Faixa, selo e caixa coerentes: carrinho só leitura; comentário PÚBLICO pelo cartão.
assert.match(conversa, /v-else-if="canalExterno === 'carrinho'"[^>]*data-faixa-carrinho/)
assert.match(conversa, /v-else-if="canalExterno === 'comentario'"[^>]*data-faixa-comentario/)
assert.match(conversa, /v-if="conversa\.somente_leitura \|\| canalExterno === 'carrinho'"[\s\S]{0,400}data-selo-so-leitura/)
assert.match(conversa, /if \(c\.id\.startsWith\('ig:'\)\) return 'Direct'/, 'o selo do Direct')
assert.match(conversa, /return ehMencao\.value \? 'Menção' : 'Comentário'/)
// A caixa de baixo: nos canais de fora, o aviso (nada de IA nem envio) ANTES do "o que a IA responderia".
const iCaixa = conversa.indexOf('v-else-if="canalExterno"')
const iObs = conversa.indexOf('<AtendimentoObservacao')
assert.ok(iCaixa > 0 && iCaixa < iObs, 'o aviso da caixa vem antes da observação/IA')
assert.match(conversa, /v-if="!canalExterno"\s+size="sm"[\s\S]{0,300}pausar a IA/, 'sem "Pausar IA" no carrinho/comentário')
assert.match(conversa, /<AtendimentoAdsPower\s+v-if="!conversa\.somente_leitura && !canalExterno"/)
assert.match(conversa, /v-if="modoCaixa === 'responder' && !canalExterno"/, 'sem foto nos canais de fora')
// O status novo do site e a frase da IA recusada.
assert.equal(P.statusCanalInfo('sem_endpoint').label, 'Rota não publicada')
assert.equal(P.semLeitura('sem_endpoint'), true, 'a rota não publicada apaga o site na barra')
assert.match(P.ERROS.canal_sem_ia, /carrinho de site/)
assert.match(constantes, /STATUS_CANAL = \("novo", "ok", "sem_escopo", "erro", "desligado", STATUS_CANAL_SEM_ENDPOINT\)/)
const icone = fs.readFileSync(path.resolve(__dirname, '../components/AtendimentoIconePlataforma.vue'), 'utf8')
assert.match(icone, /cod === 'facebook'/)
assert.match(icone, /cod === 'site'/)
const lista = fs.readFileSync(path.resolve(__dirname, '../components/AtendimentoLista.vue'), 'utf8')
assert.match(lista, /externo_ref\?: string/)
assert.match(lista, /rede_social_id\?: string/)

console.log('ok — atendimento externos (base)')
