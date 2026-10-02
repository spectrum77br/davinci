// node tests/atendimento-robo.cjs — Temu e AliExpress na caixa (30/09/2026).
// As duas são lidas pelo robô do Mac mini e NUNCA respondidas pelo DaVinci:
// a tela manda para o Seller Center, esconde a caixa de envio e mostra a
// loja apagada ("leitura parada") quando o robô para de mandar sinal.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const { parse } = require('vue/compiler-sfc')

const filename = path.resolve(__dirname, '../components/AtendimentoPlataforma.vue')
const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
assert.deepEqual(errors, [])
// O <script> comum (não o setup) é o módulo dos ajudantes exportados.
const js = ts.transpileModule(descriptor.script.content, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText
const mod = { exports: {} }
new Function('require', 'module', 'exports', js)((nome) => require(nome), mod, mod.exports)
const {
  PLATAFORMAS_COM_CANAL,
  PLATAFORMAS_COM_ENVIO,
  PLATAFORMAS_VIA_ROBO,
  canaisDa,
  leituraParada,
  motivoSellerCenter,
  origemLabel,
  plataformaInfo,
  respondidaNoSellerCenter,
  sellerCenterDe,
  semLeitura,
  statusCanalInfo,
  viaRobo,
} = mod.exports

// Nome e artigo certos (frases montadas com `de`).
assert.equal(plataformaInfo('temu').nome, 'Temu')
assert.equal(plataformaInfo('temu').de, 'da Temu')
assert.equal(plataformaInfo(' AliExpress ').nome, 'AliExpress')
assert.equal(plataformaInfo('aliexpress').de, 'do AliExpress')
assert.deepEqual(canaisDa('temu'), [{ value: 'chat', label: 'Chat' }])
assert.deepEqual(canaisDa('aliexpress'), [{ value: 'chat', label: 'Chat' }])

// Só as duas vão pelo robô; as de API continuam com envio.
assert.deepEqual([...PLATAFORMAS_VIA_ROBO].sort(), ['aliexpress', 'temu'])
for (const p of ['shopee', 'ml', 'tiktok', 'amazon', 'instagram', '', null, undefined]) {
  assert.equal(viaRobo(p), false, String(p))
  assert.equal(sellerCenterDe(p), null, String(p))
  assert.equal(motivoSellerCenter(p), '', String(p))
}
assert.ok(PLATAFORMAS_COM_CANAL.some((p) => p.value === 'temu'))
assert.ok(!PLATAFORMAS_COM_ENVIO.some((p) => viaRobo(p.value)))
assert.ok(PLATAFORMAS_COM_ENVIO.some((p) => p.value === 'amazon'))

// O botão abre a tela de chat de cada Seller Center (só https, nada de
// conversa específica: nenhum ?posn= na Temu).
const temu = sellerCenterDe('temu')
assert.equal(temu.url, 'https://br.seller.temu.com/chat.html')
assert.equal(temu.nome, 'Seller Center da Temu')
assert.doesNotMatch(temu.url, /posn=/)
const ali = sellerCenterDe('AliExpress')
assert.equal(ali.url, 'https://gsp.aliexpress.com/m_apps/im-chat/im#/window')
assert.equal(ali.nome, 'Seller Center do AliExpress')
for (const sc of [temu, ali]) assert.equal(new URL(sc.url).protocol, 'https:')
assert.match(motivoSellerCenter('temu'), /responda no Seller Center da Temu/)
assert.match(motivoSellerCenter('aliexpress'), /responda no Seller Center do AliExpress/)

// Leitura parada: o código do backend e os sinônimos viram o mesmo estado.
for (const s of ['parado', 'leitura_parada', 'robo_parado', 'sem_pulso', ' PARADO ']) {
  assert.equal(leituraParada(s), true, s)
  assert.equal(semLeitura(s), true, s)
  assert.equal(statusCanalInfo(s).label, 'Leitura parada', s)
  assert.match(statusCanalInfo(s).hint, /robô do Mac mini parou de mandar sinal/)
}
assert.equal(statusCanalInfo('sessao_caiu').label, 'Sessão caiu')
assert.equal(semLeitura('sessao_caiu'), true)
assert.equal(leituraParada('sessao_caiu'), false)
// O que já existia continua igual.
assert.equal(semLeitura('ok'), false)
assert.equal(semLeitura('novo'), false)
assert.equal(semLeitura('erro'), true)
assert.equal(statusCanalInfo('ok').label, 'Lendo')
assert.equal(statusCanalInfo('xyz'), null)

// Resposta da loja lida pelo robô = escrita no Seller Center.
const externa = { autor: 'loja', origem: 'externo', autor_nome: null }
assert.equal(respondidaNoSellerCenter(externa, 'temu'), true)
assert.equal(respondidaNoSellerCenter(externa, 'shopee'), false)
assert.equal(respondidaNoSellerCenter({ autor: 'cliente', origem: 'cliente' }, 'temu'), false)
assert.equal(origemLabel(externa, 'aliexpress'), 'Respondido no Seller Center')
assert.equal(origemLabel({ ...externa, autor_nome: 'Loja X' }, 'temu'), 'Respondido no Seller Center · Loja X')
assert.equal(origemLabel(externa, 'amazon'), 'Respondido no Seller Central')
assert.match(origemLabel(externa, 'shopee'), /Duoke/)

// A conversa: Temu/AliExpress caem SEMPRE no painel de observação (sem caixa
// de envio), o envio fica travado e o cabeçalho tem o botão do Seller Center.
const conversa = fs.readFileSync(path.resolve(__dirname, '../components/AtendimentoConversa.vue'), 'utf8')
assert.match(conversa, /if \(sellerCenterDe\(d\.conversa\.plataforma\)\) return true\n\s+if \(d\.conversa\.somente_leitura\) return false/)
assert.match(conversa, /if \(sellerCenterDe\(d\.conversa\.plataforma\)\) return motivoSellerCenter\(d\.conversa\.plataforma\)/)
assert.match(conversa, /v-else-if="sellerCenter"[\s\S]{0,200}:href="sellerCenter\.url"/)
// O painel de observação diz quem responde e traz o link.
const obs = fs.readFileSync(path.resolve(__dirname, '../components/AtendimentoObservacao.vue'), 'utf8')
assert.match(obs, /if \(sellerCenter\.value\) return `o \$\{sellerCenter\.value\.nome\}`/)
assert.match(obs, /:href="sellerCenter\.url"/)
// A barra de lojas apaga a loja parada e aceita loja do robô sem integração.
const lojas = fs.readFileSync(path.resolve(__dirname, '../components/AtendimentoLojas.vue'), 'utf8')
assert.match(lojas, /l\.apagada = leituraParada\(l\.status\) \|\|/)
assert.match(lojas, /if \(semId && !rede && !externo && !directSemConta && !\(viaRobo\(l\.plataforma\) && l\.conta\)\) continue/)

// O ícone tem desenho próprio para as duas (não cai na bolinha cinza).
const icone = fs.readFileSync(path.resolve(__dirname, '../components/AtendimentoIconePlataforma.vue'), 'utf8')
assert.match(icone, /cod === 'temu'/)
assert.match(icone, /cod === 'aliexpress'/)

console.log('ok: atendimento-robo')
