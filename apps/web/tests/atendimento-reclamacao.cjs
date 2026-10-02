// node tests/atendimento-reclamacao.cjs — o cartão da reclamação (RF2, 01/10/2026).
// Reclamação/mediação/devolução da plataforma no topo da conversa, SÓ
// LEITURA: cores da etiqueta (vermelho/roxo), contagem regressiva do prazo,
// título com o nº (ou o pedido), ordem (abertas por prazo, depois as
// encerradas), "Abrir no/na" e nenhum botão de ação na plataforma. Desde
// 02/10/2026: status e motivo da Shopee/TikTok/ML em português (tabela; o
// código cru nunca aparece) e o "Pede: …" da Shopee/TikTok.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')

const filename = path.resolve(__dirname, '../components/AtendimentoReclamacao.vue')
const fonte = fs.readFileSync(filename, 'utf8')
const { descriptor, errors } = parse(fonte, { filename })
assert.deepEqual(errors, [])
// O <script> comum (não o setup) é o módulo dos ajudantes exportados.
const js = ts.transpileModule(descriptor.script.content, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText
const mod = { exports: {} }
new Function('require', 'module', 'exports', js)((nome) => require(nome), mod, mod.exports)
const {
  PRAZO_VENCENDO_MS,
  abrirEm,
  corDoTipo,
  motivoLegivel,
  ordenarReclamacoes,
  prazoReclamacao,
  statusLegivel,
  tempo,
  tituloReclamacao,
} = mod.exports

// O componente compila (setup + template), sem erro de template.
const script = compileScript(descriptor, { id: 'reclamacao' })
assert.ok(script.content.includes('/reclamacoes'))
const tpl = compileTemplate({ source: descriptor.template.content, filename, id: 'reclamacao' })
assert.deepEqual(tpl.errors, [])

// Cores da etiqueta: Reclamação e Mediação vermelho, Devolução roxo.
assert.match(corDoTipo('reclamacao').selo, /red/)
assert.match(corDoTipo('mediacao').selo, /red/)
assert.match(corDoTipo('devolucao').selo, /purple/)
assert.match(corDoTipo('devolucao').borda, /purple/)

// Tempo como na lista.
assert.equal(tempo(45), '45 min')
assert.equal(tempo(200), '3 h 20 min')
assert.equal(tempo(60 * 52), '2 d 4 h')

// Contagem regressiva: só da aberta com prazo; < 24 h = vencendo; passou = vencida.
const agora = Date.parse('2026-10-01T18:00:00Z')
assert.equal(PRAZO_VENCENDO_MS, 24 * 3600 * 1000)
assert.equal(prazoReclamacao({ aberta: false, prazo_em: '2026-10-05T00:00:00Z' }, agora), null)
assert.equal(prazoReclamacao({ aberta: true, prazo_em: null }, agora), null)
assert.equal(prazoReclamacao({ aberta: true, prazo_em: 'lixo' }, agora), null)
const longe = prazoReclamacao({ aberta: true, prazo_em: '2026-10-06T02:32:00Z' }, agora)
assert.equal(longe.nivel, 'ok')
assert.equal(longe.texto, 'faltam 4 d 8 h')
const perto = prazoReclamacao({ aberta: true, prazo_em: '2026-10-02T08:00:00Z' }, agora)
assert.equal(perto.nivel, 'vencendo')
assert.equal(perto.texto, 'faltam 14 h')
const vencida = prazoReclamacao({ aberta: true, prazo_em: '2026-10-01T15:00:00Z' }, agora)
assert.equal(vencida.nivel, 'vencida')
assert.equal(vencida.texto, 'prazo vencido há 3 h')

// Título: o nº da plataforma; sem ele (Logística sem o id), pelo pedido.
assert.equal(tituloReclamacao({ tipo_rotulo: 'Reclamação', numero: '5582543195', pedido_marketplace: '2000018509205724' }), 'Reclamação nº 5582543195')
assert.equal(tituloReclamacao({ tipo_rotulo: 'Devolução', numero: null, pedido_marketplace: '250930ABC' }), 'Devolução do pedido 250930ABC')
assert.equal(tituloReclamacao({ tipo_rotulo: 'Disputa', numero: null, pedido_marketplace: null }), 'Disputa')

// "Abrir no Mercado Livre", "Abrir na Shopee", "Abrir no TikTok".
assert.equal(abrirEm('ml', 'Mercado Livre'), 'Abrir no Mercado Livre')
assert.equal(abrirEm('shopee', 'Shopee'), 'Abrir na Shopee')
assert.equal(abrirEm('tiktok', 'TikTok'), 'Abrir no TikTok')

// Ordem: abertas (prazo mais curto antes, sem prazo depois), depois encerradas (mais recente antes).
const base = { plataforma: 'ml', plataforma_nome: 'Mercado Livre', tipo: 'reclamacao', tipo_rotulo: 'Reclamação', aberta_em: '2026-09-20T00:00:00Z', encerrada_em: null, prazo_em: null }
const ordem = ordenarReclamacoes([
  { ...base, id: 'enc-velha', aberta: false, encerrada_em: '2026-09-01T00:00:00Z' },
  { ...base, id: 'sem-prazo', aberta: true },
  { ...base, id: 'enc-nova', aberta: false, encerrada_em: '2026-09-30T00:00:00Z' },
  { ...base, id: 'prazo-longe', aberta: true, prazo_em: '2026-10-09T00:00:00Z' },
  { ...base, id: 'prazo-perto', aberta: true, prazo_em: '2026-10-02T00:00:00Z' },
]).map((r) => r.id)
assert.deepEqual(ordem, ['prazo-perto', 'prazo-longe', 'sem-prazo', 'enc-nova', 'enc-velha'])

// Só leitura: nenhum POST/PUT/DELETE nem botão de ação na plataforma.
assert.ok(!/method:\s*'(POST|PUT|PATCH|DELETE)'/i.test(fonte))
for (const proibido of ['Aceitar devolução', 'Oferecer solução', 'Pedir mediação', 'open-dispute', 'send-message']) {
  assert.ok(!fonte.includes(proibido), proibido)
}
// O link da plataforma abre em aba nova, sem dar acesso à janela de cá.
// O link da plataforma passa pelo AdsPower da loja (02/10/2026) e, sem perfil,
// abre em aba nova sem dar acesso à janela de cá.
assert.match(fonte, /<AtendimentoAbrirPlataforma[\s\S]*?:perfil="perfil"/)
const abrir = fs.readFileSync(path.join(__dirname, '..', 'components', 'AtendimentoAbrirPlataforma.vue'), 'utf8')
assert.match(abrir, /target="_blank" rel="noopener noreferrer"/)
assert.match(abrir, /abrirPaginaNoPerfil/)
assert.match(abrir, /ev\.ctrlKey \|\| ev\.metaKey/) // Ctrl/⌘+clique: navegador comum

// O motivo do ML chega como código (02/10/2026): vira texto em português.
assert.equal(motivoLegivel('not_working_item'), 'Produto não funciona')
assert.equal(motivoLegivel('repentant_buyer'), 'Desistiu da compra (chegou bem, não quer mais)')
assert.equal(motivoLegivel('wrong_size_xyz'), 'Wrong size xyz') // desconhecido: legível, não some
assert.equal(motivoLegivel('Produto com defeito'), 'Produto com defeito') // já traduzido
assert.equal(motivoLegivel(null), '')

// O motivo da Shopee chega como código (02/10/2026): tabela; desconhecido, legível.
assert.equal(motivoLegivel('FUNCTIONAL_DMG'), 'Produto com defeito (não funciona)')
assert.equal(motivoLegivel('CHANGE_MIND'), 'Mudou de ideia (desistiu da compra)')
assert.equal(motivoLegivel('ITEM_NOT_IN_THE_LIST'), 'Item not in the list')
assert.equal(motivoLegivel('Item com defeito'), 'Item com defeito') // rótulo da TikTok

// O status: o do backend (já em português) vale; cru ou vazio, a tabela.
const st = (plataforma, status, status_rotulo = null) => statusLegivel({ plataforma, status, status_rotulo })
assert.equal(st('shopee', 'JUDGING', 'Em análise pela Shopee (disputa)'), 'Em análise pela Shopee (disputa)')
assert.equal(st('ml', 'opened', 'Em mediação no Mercado Livre'), 'Em mediação no Mercado Livre')
assert.equal(st('shopee', 'JUDGING'), 'Em análise pela Shopee (disputa)')
assert.equal(st('shopee', 'JUDGING', 'JUDGING'), 'Em análise pela Shopee (disputa)')
assert.equal(st('shopee', 'PROCESSING'), 'Em andamento')
assert.equal(st('shopee', 'SELLER_DISPUTE'), 'Loja contestou')
assert.equal(st('shopee', 'REQUESTED'), 'Pedido de devolução aberto')
assert.equal(st('tiktok', 'AWAITING_BUYER_SHIP'), 'Esperando o comprador enviar')
assert.equal(st('tiktok', 'BUYER_SHIPPED_ITEM'), 'Comprador enviou o produto')
assert.equal(st('tiktok', 'REJECT_RECEIVE_PACKAGE'), 'Recebimento recusado')
assert.equal(st('tiktok', 'RETURN_OR_REFUND_REQUEST_PENDING'), 'Pedido de devolução/reembolso pendente')
assert.equal(st('ml', 'opened'), 'Aberta')
assert.equal(st('ml', 'closed'), 'Encerrada')
assert.equal(st('tiktok', 'SOMETHING_NEW'), 'Something new') // desconhecido: legível
assert.equal(st('shopee', null, null), '')

// O cartão mostra o status pela função, o motivo traduzido e o "Pede: …".
assert.match(fonte, /\{\{ statusLegivel\(r\) \}\}/)
assert.match(fonte, /Pede: \{\{ r\.solucao \}\}/)
assert.doesNotMatch(fonte, /\{\{ r\.status_rotulo \}\}/)

console.log('atendimento-reclamacao: ok')
