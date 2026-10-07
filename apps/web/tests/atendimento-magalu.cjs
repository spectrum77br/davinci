// node tests/atendimento-magalu.cjs — a Magalu na caixa (30/09/2026).
// Lida e respondida por API, com três caixas por loja (Pergunta, Chat, SAC),
// o limite de cada uma, o aviso de que a resposta passa pela moderação da
// Magalu e o botão "Abrir no portal" com a tela da caixa certa.
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
  AVISO_MODERACAO_MAGALU,
  MODERACAO_MAGALU_ENVIADA,
  PLATAFORMAS_COM_CANAL,
  PLATAFORMAS_COM_ENVIO,
  canaisDa,
  canalLabel,
  erroEnvioLegivel,
  limiteDe,
  origemLabel,
  passaPelaModeracao,
  plataformaInfo,
  portalMagaluDe,
  respondidaNoPortalMagalu,
  sellerCenterDe,
  tamanhoDoEnvio,
  variasCaixas,
  viaRobo,
} = mod.exports

// Nome, artigo e cor da marca (só a cor).
assert.equal(plataformaInfo('magalu').nome, 'Magalu')
assert.equal(plataformaInfo(' Magalu ').curto, 'Magalu')
assert.equal(plataformaInfo('magalu').de, 'da Magalu')
assert.match(plataformaInfo('magalu').cor, /#0086FF/i)

// Três caixas, com os rótulos da tela; o canal aparece na lista/cabeçalho.
assert.deepEqual(canaisDa('magalu'), [
  { value: 'pergunta', label: 'Pergunta' },
  { value: 'chat', label: 'Chat' },
  { value: 'sac', label: 'SAC' },
])
assert.equal(canalLabel('sac'), 'SAC')
assert.equal(variasCaixas('magalu'), true)
assert.equal(variasCaixas('ml'), true)
for (const p of ['shopee', 'tiktok', 'amazon', 'temu', 'aliexpress', '', null]) assert.equal(variasCaixas(p), false, String(p))

// Responde-se pelo DaVinci (não é loja do robô): tem canal, modo e respostas prontas.
assert.equal(viaRobo('magalu'), false)
assert.equal(sellerCenterDe('magalu'), null)
assert.ok(PLATAFORMAS_COM_CANAL.some((p) => p.value === 'magalu'))
assert.ok(PLATAFORMAS_COM_ENVIO.some((p) => p.value === 'magalu'))

// Limite por caixa: chat 2200, SAC 3000; a pergunta não tem limite
// documentado — vale o que o backend mandar (`envio.limite_caracteres`).
assert.equal(limiteDe('magalu', 'chat'), 2200)
assert.equal(limiteDe('magalu', 'sac'), 3000)
assert.equal(limiteDe('magalu', 'pergunta'), null)
// Resposta pronta para a Magalu inteira: o menor limite entre as caixas que têm.
assert.equal(limiteDe('magalu', null), 2200)
// As outras continuam iguais.
assert.equal(limiteDe('ml', 'pos_venda'), 350)
assert.equal(limiteDe('shopee', 'chat'), 1000)
// Contagem sem a regra do ML (emoji conta 1, "…" fica "…").
assert.equal(tamanhoDoEnvio('Olá… 👍', 'magalu'), 6)

// Moderação: só a Magalu; as frases dizem que a Magalu decide.
assert.equal(passaPelaModeracao('magalu'), true)
assert.equal(passaPelaModeracao(' MAGALU '), true)
for (const p of ['shopee', 'ml', 'tiktok', 'amazon', 'temu', '', null, undefined]) assert.equal(passaPelaModeracao(p), false, String(p))
assert.match(AVISO_MODERACAO_MAGALU, /passa pela moderação da Magalu/)
assert.match(AVISO_MODERACAO_MAGALU, /CPF, Pix, e-mail/)
assert.match(MODERACAO_MAGALU_ENVIADA, /depois da moderação/)

// "Abrir no portal": a tela da caixa certa, só https, no portal do seller.
assert.equal(portalMagaluDe('magalu', 'chat').url, 'https://seller.magalu.com/chat-com-cliente')
assert.equal(portalMagaluDe('magalu', 'pergunta').url, 'https://seller.magalu.com/perguntas-e-respostas')
assert.equal(portalMagaluDe('Magalu', 'SAC').url, 'https://seller.magalu.com/sac')
// Caixa desconhecida: o início do portal (nunca link nenhum na Magalu).
assert.equal(portalMagaluDe('magalu', 'xyz').url, 'https://seller.magalu.com/')
assert.equal(portalMagaluDe('magalu', null).url, 'https://seller.magalu.com/')
for (const c of ['chat', 'pergunta', 'sac', null]) {
  const u = new URL(portalMagaluDe('magalu', c).url)
  assert.equal(u.protocol, 'https:')
  assert.equal(u.hostname, 'seller.magalu.com')
}
assert.equal(portalMagaluDe('magalu', 'chat').nome, 'Portal do Seller da Magalu')
for (const p of ['shopee', 'ml', 'amazon', 'temu', '', null]) assert.equal(portalMagaluDe(p, 'chat'), null, String(p))

// Resposta da loja por fora: o Duoke não lê a Magalu — é o portal.
const externa = { autor: 'loja', origem: 'externo', autor_nome: null }
assert.equal(respondidaNoPortalMagalu(externa, 'magalu'), true)
assert.equal(respondidaNoPortalMagalu(externa, 'shopee'), false)
assert.equal(respondidaNoPortalMagalu({ autor: 'cliente', origem: 'cliente' }, 'magalu'), false)
assert.equal(origemLabel(externa, 'magalu'), 'Fora do DaVinci (portal da Magalu)')
assert.equal(origemLabel({ ...externa, autor_nome: 'Ana' }, 'magalu'), 'Fora do DaVinci (portal da Magalu) · Ana')
assert.doesNotMatch(origemLabel(externa, 'magalu'), /Duoke/)
assert.match(origemLabel(externa, 'shopee'), /Duoke/)
// Nossa resposta continua "Equipe"/"IA".
assert.equal(origemLabel({ autor: 'loja', origem: 'davinci_ia', autor_nome: null }, 'magalu'), 'IA')

// Recusa da moderação e limite de requisições viram frase de gente.
assert.match(erroEnvioLegivel('magalu moderacao: RESPONSE_REJECTED'), /moderação da Magalu recusou/)
assert.match(erroEnvioLegivel('rejected_response'), /moderação da Magalu recusou/)
assert.match(erroEnvioLegivel('magalu HTTP 429 too many'), /pediu para esperar/)
// Os que já existiam não mudaram.
assert.match(erroEnvioLegivel('sem_pergunta_pendente'), /já foi respondida/)
assert.match(erroEnvioLegivel('shopee token_http_401'), /reconectada em Integrações/)

// A conversa: botão do portal, aviso de moderação na caixa, limite com a
// cópia da tela quando a API não manda, e o canal no painel de observação.
const conversa = fs.readFileSync(path.resolve(__dirname, '../components/AtendimentoConversa.vue'), 'utf8')
assert.match(conversa, /const portal = computed\(\(\) => portalMagaluDe\(conversa\.value\?\.plataforma, conversa\.value\?\.canal\)\)/)
assert.match(conversa, /v-else-if="portal"[\s\S]{0,200}:href="portal\.url"/)
assert.match(conversa, /Abrir no portal/)
assert.match(conversa, /v-if="moderacao && podeDigitar"[\s\S]{0,200}AVISO_MODERACAO_MAGALU/)
assert.match(conversa, /envio\.limite_caracteres \|\| limiteDe\(/)
assert.match(conversa, /passaPelaModeracao\(plataforma\) \? MODERACAO_MAGALU_ENVIADA/)
assert.match(conversa, /:canal="conversa\?\.canal"/)
assert.match(conversa, /variasCaixas\(c\.plataforma\) \|\| c\.plataforma === 'amazon'/)
// O Temu/AliExpress continua antes (a ordem do v-else-if importa).
assert.ok(conversa.indexOf('v-else-if="sellerCenter"') < conversa.indexOf('v-else-if="portal"'))

// Observação: quem responde é o portal, com o link da caixa.
const obs = fs.readFileSync(path.resolve(__dirname, '../components/AtendimentoObservacao.vue'), 'utf8')
assert.match(obs, /if \(portal\.value\) return `o \$\{portal\.value\.nome\}`/)
assert.match(obs, /v-else-if="portal"[\s\S]{0,120}:href="portal\.url"/)

// Barra de lojas: a Magalu vem depois da Amazon, antes das lojas do robô.
const lojas = fs.readFileSync(path.resolve(__dirname, '../components/AtendimentoLojas.vue'), 'utf8')
assert.match(lojas, /const ORDEM = \['shopee', 'tiktok', 'ml', 'amazon', 'magalu', 'temu', 'aliexpress', 'site', 'instagram', 'facebook'\]/)

// Lista: canal na linha (variasCaixas) e a Magalu no filtro só com o /resumo.
const lista = fs.readFileSync(path.resolve(__dirname, '../components/AtendimentoLista.vue'), 'utf8')
assert.match(lista, /return variasCaixas\(c\.plataforma\) \?/)
assert.match(lista, /const SO_COM_RESUMO = new Set\(\['instagram', 'magalu', 'site', 'facebook'\]\)/)

// Painel do pedido: sem retrato pela API (enriquecer só tem Shopee e ML), e
// pergunta de pré-venda não finge que "falta a API".
const pedido = fs.readFileSync(path.resolve(__dirname, '../components/AtendimentoPedido.vue'), 'utf8')
assert.match(pedido, /const temRetratoNaApi = computed\(\(\) => \['shopee', 'ml'\]\.includes/)
assert.match(pedido, /plataforma === 'magalu' && !numeroMarketplace\.value/)
assert.match(pedido, /Pergunta de pré-venda \(no anúncio\): ainda não há pedido\./)
assert.match(pedido, /v-if="temAbaLogistica"/)

// Ícone próprio (não cai na bolinha cinza) e sem arquivo de logo — a única
// imagem é o logo do Mercado Livre (07/10/2026, pedido do Eduardo).
const icone = fs.readFileSync(path.resolve(__dirname, '../components/AtendimentoIconePlataforma.vue'), 'utf8')
assert.match(icone, /cod === 'magalu'/)
assert.match(icone, /magalu: 'Magalu'/)
const imagens = icone.match(/<image[^>]*>/g) || []
assert.deepStrictEqual(imagens.map((t) => (t.match(/href="([^"]+)"/) || [])[1]), ['/logos/mercado-livre.png'])
assert.doesNotMatch(icone.replace(/<image[^>]*>/g, ''), /<image|\.png|\.svg"/)

// Nada de marcar como lido: a tela não chama o read_by da Magalu.
for (const f of ['AtendimentoConversa.vue', 'AtendimentoPlataforma.vue', 'AtendimentoObservacao.vue', 'AtendimentoPedido.vue', 'AtendimentoLista.vue']) {
  const src = fs.readFileSync(path.resolve(__dirname, '../components', f), 'utf8')
  assert.doesNotMatch(src, /read_by/, f)
}

console.log('ok: atendimento-magalu')
