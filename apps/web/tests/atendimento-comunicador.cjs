// node tests/atendimento-comunicador.cjs — a integração dos itens 1, 2 e 3 do
// Comunicador na caixa /atendimento (01/10/2026). As frentes testaram cada
// componente sozinho; aqui, o que só existe depois de juntar:
//  - o cabeçalho da conversa com o SELO da etiqueta editável (troca à mão) e a
//    resposta da troca voltando para o detalhe e para a lista;
//  - o CARTÃO DA RECLAMAÇÃO no topo da conversa (só quando há alguma);
//  - a MUDANÇA DE ETIQUETA na linha do tempo ("Etiqueta mudou de X para Y"),
//    na hora certa entre as mensagens;
//  - o canal novo `reclamacao` com rótulo;
//  - a costura da lista na ORDEM DA ABA ("Falta responder" pelo prazo).
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { parse, compileTemplate } = require('vue/compiler-sfc')

const transpile = (source) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText

function sfc(rel) {
  const filename = path.resolve(__dirname, rel)
  const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
  assert.deepEqual(errors, [], rel)
  const compiled = compileTemplate({ source: descriptor.template.content, filename, id: path.basename(rel) })
  assert.deepEqual(compiled.errors, [], `${rel}: template compila`)
  return descriptor
}
function exportsDe(descriptor) {
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(descriptor.script.content))((n) => require(n), mod, mod.exports)
  return mod.exports
}
// Um pedaço do <script setup>, de `ini` até `fim` (exclusive), já sem tipos.
function trecho(src, ini, fim) {
  const a = src.indexOf(ini)
  const b = src.indexOf(fim, a + 1)
  assert.ok(a >= 0 && b > a, `trecho ${ini} … ${fim}`)
  return transpile(src.slice(a, b))
}

const conversaSfc = sfc('../components/AtendimentoConversa.vue')
const C = exportsDe(conversaSfc)
const P = exportsDe(sfc('../components/AtendimentoPlataforma.vue'))
const E = exportsDe(sfc('../components/AtendimentoEtiqueta.vue'))
sfc('../components/AtendimentoReclamacao.vue')
const tpl = conversaSfc.template.content
const setup = conversaSfc.scriptSetup.content

// ------------------------------------------------ o template junta as três frentes
{
  assert.doesNotMatch(tpl, /ENCAIXE \(frente [AB]/, 'os encaixes das frentes A e B foram preenchidos')
  const selo = (tpl.match(/<AtendimentoEtiqueta[\s\S]*?\/>/) || [])[0]
  assert.ok(selo, 'o selo da etiqueta está no cabeçalho')
  assert.match(selo, /:editavel="canEdit"/)
  assert.match(selo, /:conversa-id="conversa\.id"/)
  assert.match(selo, /:historico="detalhe\.etiqueta_historico"/)
  assert.match(selo, /@trocada="aoTrocarEtiqueta"/)
  assert.match(selo, /v-if="!conversa\.somente_leitura"/, 'Instagram (só leitura) sem etiqueta')
  // O selo fica logo depois do nome do comprador.
  assert.ok(tpl.indexOf('<AtendimentoEtiqueta') > tpl.indexOf('{{ titulo(conversa) }}'))
  assert.ok(tpl.indexOf('<AtendimentoEtiqueta') < tpl.indexOf('<AtendimentoIconePlataforma :plataforma="conversa.plataforma" :tamanho="15"'))

  const cartao = (tpl.match(/<div\s+v-if="temCartaoReclamacao"[\s\S]*?<\/div>/) || [])[0]
  assert.ok(cartao, 'o cartão da reclamação está na conversa')
  assert.match(cartao, /v-show="reclamacoesQtd > 0"/, 'a faixa só aparece com reclamação')
  assert.match(cartao, /<AtendimentoReclamacao ref="reclamacaoRef" :conversa-id="conversa\.id" @carregado="aoCarregarReclamacoes"/)
  // Entre as faixas e as mensagens.
  assert.ok(tpl.indexOf('temCartaoReclamacao') > tpl.indexOf('sem_resposta_necessaria" class="shrink-0'))
  assert.ok(tpl.indexOf('temCartaoReclamacao') < tpl.indexOf('ref="rolagem"'))

  assert.match(tpl, /v-else-if="l\.tipo === 'etiqueta'"/, 'a mudança de etiqueta é uma linha da linha do tempo')
  assert.match(tpl, /frasesDaEtiqueta\(l\.h\)\.titulo/)
  // Relida no "atualizar" e junto com o painel.
  assert.match(trecho(setup, 'function atualizarTudo', '}'), /reclamacaoRef\.value\?\.carregar\(\)/)
}

// ------------------------------------------------ a frase da linha do tempo
{
  const f = C.frasesDaEtiqueta
  assert.deepEqual(
    f({ de_rotulo: 'Pós-venda', para_rotulo: 'Reclamação', motivo: 'Reclamação 5582543195 aberta no ML (leitura das reclamações do ML)', por_nome: null }),
    { titulo: 'Etiqueta mudou de Pós-venda para Reclamação', detalhe: 'Reclamação 5582543195 aberta no ML (leitura das reclamações do ML)' },
  )
  assert.deepEqual(
    f({ de_rotulo: 'Reclamação', para_rotulo: 'Pós-venda', motivo: 'Trocada à mão: resolvido no chat', por_nome: 'Eduardo' }),
    { titulo: 'Etiqueta mudou de Reclamação para Pós-venda', detalhe: 'Trocada à mão: resolvido no chat · por Eduardo' },
  )
  assert.deepEqual(f({ de_rotulo: null, para_rotulo: 'Pré-venda', motivo: null, por_nome: null }), { titulo: 'Etiqueta: Pré-venda', detalhe: '' })
}

// ------------------------------------------------ mensagens + mudanças de etiqueta, pela hora
{
  const js = trecho(setup, 'type Linha =', '// A bolinha da mudança de etiqueta') + '\nreturn linhas'
  const detalhe = Vue.ref(null)
  const linhas = new Function('computed', 'detalhe', 'agora', 'rotuloDia', js)(
    Vue.computed, detalhe, Vue.ref(Date.parse('2026-10-01T15:00:00Z')), (iso) => `dia ${iso.slice(0, 10)}`,
  )
  const msg = (id, em) => ({ id, enviada_em: em, autor: 'cliente', origem: 'cliente', tipo: 'texto', texto: id, anexos: [], status: 'recebida' })
  const etq = (id, em, de, para) => ({ id, em, de, para, de_rotulo: de, para_rotulo: para, motivo: null, por_user_id: null, por_nome: null })
  detalhe.value = {
    mensagens: [msg('m2', '2026-10-01T10:00:00Z'), msg('m1', '2026-09-30T09:00:00Z'), msg('m3', '2026-10-01T12:00:00Z')],
    etiqueta_historico: [etq('h1', '2026-10-01T10:00:00Z', 'pos_venda', 'reclamacao'), etq('h2', '2026-10-01T11:00:00Z', 'reclamacao', 'pos_venda')],
  }
  const ordem = linhas.value.map((l) => (l.tipo === 'dia' ? l.texto : l.tipo === 'msg' ? l.m.id : l.h.id))
  // No empate (m2 e h1 às 10:00), a mensagem antes: foi ela que mudou o status.
  assert.deepEqual(ordem, ['dia 2026-09-30', 'm1', 'dia 2026-10-01', 'm2', 'h1', 'h2', 'm3'])
  assert.ok(linhas.value.filter((l) => l.tipo === 'etiqueta').every((l) => l.chave.startsWith('etiqueta-')))
  // API antiga, sem `etiqueta_historico`: só as mensagens.
  detalhe.value = { mensagens: [msg('m1', '2026-09-30T09:00:00Z')] }
  assert.deepEqual(linhas.value.map((l) => l.tipo), ['dia', 'msg'])
}

// ------------------------------------------------ a troca à mão volta para o detalhe e para a lista
{
  const js = trecho(setup, 'function aoTrocarEtiqueta', '// ─── reclamação da plataforma') + '\nreturn aoTrocarEtiqueta'
  const detalhe = Vue.ref({
    conversa: { id: 'c1', etiqueta: 'reclamacao', etiqueta_manual: false, comprador_nome: 'X' },
    mensagens: [],
    etiqueta_historico: [],
  })
  const emitidos = []
  const avisos = []
  const aoTrocar = new Function('detalhe', 'emit', 'etiquetaInfo', 'toasts', js)(
    detalhe, (n, v) => emitidos.push([n, v]), E.etiquetaInfo, { success: (t) => avisos.push(t) },
  )
  const hist = [{ id: 'h1', de: 'reclamacao', para: 'pos_venda' }]
  aoTrocar({ conversa: { id: 'c1', etiqueta: 'pos_venda', etiqueta_manual: true }, etiqueta_historico: hist })
  assert.equal(detalhe.value.conversa.etiqueta, 'pos_venda')
  assert.equal(detalhe.value.conversa.etiqueta_manual, true)
  assert.equal(detalhe.value.conversa.comprador_nome, 'X', 'o resto da conversa fica')
  assert.deepEqual(detalhe.value.etiqueta_historico, hist)
  assert.deepEqual(emitidos.map(([n, v]) => [n, v.id, v.etiqueta]), [['mudou', 'c1', 'pos_venda']])
  assert.deepEqual(avisos, ['Etiqueta: Pós-venda'])
  // A pessoa já trocou de conversa: o detalhe aberto não muda; a lista sim.
  aoTrocar({ conversa: { id: 'c2', etiqueta: 'devolucao' }, etiqueta_historico: [] })
  assert.equal(detalhe.value.conversa.id, 'c1')
  assert.deepEqual(emitidos.at(-1).slice(0, 1).concat(emitidos.at(-1)[1].id), ['mudou', 'c2'])
}

// ------------------------------------------------ canal novo e ordem da lista
{
  assert.equal(P.canalLabel('reclamacao'), 'Reclamação')
  // A conversa da reclamação não é caixa configurável por loja.
  assert.ok(!P.CANAIS_ATENDIMENTO.some((c) => c.value === 'reclamacao'))

  const router = fs.readFileSync(path.resolve(__dirname, '../../api/app/routers/atendimento.py'), 'utf8')
  const pelaApi = [...((router.match(/^FILTROS_PELO_PRAZO = \(([^)]*)\)/m) || [])[1] || '').matchAll(/"([a-z_]+)"/g)].map((m) => m[1])
  assert.deepEqual(P.FILTROS_PELO_PRAZO, pelaApi, 'a tela e o backend ordenam as mesmas abas pelo prazo')

  const it = (id, prazo, ultima) => ({ id, prazo_resposta_em: prazo, ultima_mensagem_em: ultima })
  const a = it('a', '2026-10-01T10:00:00Z', '2026-10-01T09:00:00Z')
  const b = it('b', '2026-10-01T12:00:00Z', '2026-10-01T11:00:00Z')
  const semPrazo = it('c', null, '2026-10-01T23:00:00Z')
  // Falta responder: prazo mais curto primeiro, sem prazo no fim, id desempata.
  assert.equal(P.vemDepoisNaLista(b, a, 'aguardando'), true)
  assert.equal(P.vemDepoisNaLista(a, b, 'aguardando'), false)
  assert.equal(P.vemDepoisNaLista(semPrazo, b, 'aguardando'), true, 'sem prazo vem no fim (mesmo sendo a mais recente)')
  assert.equal(P.vemDepoisNaLista(b, semPrazo, 'aguardando'), false)
  assert.equal(P.vemDepoisNaLista(it('d', null, null), semPrazo, 'aguardando'), true, 'entre as sem prazo, pelo id')
  assert.equal(P.vemDepoisNaLista(it('b2', '2026-10-01T12:00:00Z', null), b, 'aguardando'), true, 'mesmo prazo, pelo id')
  // As outras abas: pela mensagem mais recente (a mais velha vem depois).
  assert.equal(P.vemDepoisNaLista(a, b, 'todas'), true)
  assert.equal(P.vemDepoisNaLista(semPrazo, b, 'todas'), false, 'a mais recente vem antes')
  assert.equal(P.podeCosturar(it('x', null, null), 'todas'), false, 'sem hora não há corte')
  assert.equal(P.podeCosturar(it('x', null, null), 'aguardando'), true)
  assert.equal(P.podeCosturar(null, 'aguardando'), false)

  const pagina = fs.readFileSync(path.resolve(__dirname, '../pages/atendimento.vue'), 'utf8')
  assert.match(pagina, /vemDepoisNaLista\(c, ultimo, filtro\)/, 'a costura usa a ordem da aba')
  assert.doesNotMatch(pagina, /ultima_mensagem_em \|\| ''\) < \(ultimo/, 'nada de cortar pela hora em toda aba')
  assert.match(pagina, /antes\.etiqueta \?\? null\) !== \(c\.etiqueta \?\? null\)/, 'troca de etiqueta relê as contagens')
}

console.log('ok: atendimento-comunicador')
