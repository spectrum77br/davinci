// Run from apps/web: node tests/pricing-atualizar.cjs
// Tabela de Preços (M-14, 18/11/2026): o Enviar ▾ virou "Atualizar ▾", com
// Atualizar planilha (o antigo Recarregar), Conferir (o antigo Conferir
// anúncios) e Atualizar anúncio (o antigo Enviar), e Contas em Geral ou Por
// conta. Ao abrir: só a planilha marcada e Geral. Ordem: anúncio, conferir,
// planilha. Executa as funções e computeds reais da página, sem API nem DOM.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const { parse } = require('vue/compiler-sfc')

const filename = path.resolve(__dirname, '../pages/pricing/[tab].vue')
const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
assert.deepEqual(errors, [])
const setup = descriptor.scriptSetup.content
const tpl = descriptor.template.content
const source = ts.createSourceFile(filename, setup, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)

const js = (texto) => {
  const out = ts.transpileModule(texto, { compilerOptions: { target: ts.ScriptTarget.ES2022 }, reportDiagnostics: true })
  assert.deepEqual(out.diagnostics, [])
  return out.outputText
}
const funcao = (nome) => {
  const n = source.statements.find((s) => ts.isFunctionDeclaration(s) && s.name?.text === nome)
  assert.ok(n, `${nome} existe na página`)
  return js(n.getText(source))
}
const computado = (nome) => {
  const st = source.statements.find((s) => ts.isVariableStatement(s)
    && s.declarationList.declarations.some((d) => d.name.getText(source) === nome))
  assert.ok(st, `${nome} existe na página`)
  const ini = st.declarationList.declarations.find((d) => d.name.getText(source) === nome).initializer
  assert.ok(ini && ts.isCallExpression(ini) && ini.expression.getText(source) === 'computed', `${nome} é computed`)
  return js(`const __f = ${ini.arguments[0].getText(source)}`)
}

const ref = (v) => ({ value: v })
const comp = (nome, deps) => {
  const nomes = Object.keys(deps)
  const fn = new Function(...nomes, `${computado(nome)}\nreturn __f`)(...nomes.map((n) => deps[n]))
  return { get value() { return fn() } }
}

const A = { id: 'a1', platform: 'shopee', name: 'atlas', kit_number: 1 }
const B = { id: 'a2', platform: 'shopee', name: 'kia', kit_number: 1 }
const C = { id: 'a3', platform: 'shopee', name: 'vita', kit_number: 2 }

function montar({ podeEnviar = true, podeConf = true, colunas = [A, B, C], filtro = false } = {}) {
  const s = {
    painelAtualizarAberto: ref(true),
    atualizarPlanilha: ref(false),
    atualizarConferir: ref(true),
    atualizarAnuncio: ref(true),
    atualizarModoContas: ref('conta'),
    atualizarContasMarcadas: ref(new Set(['a2'])),
    buscaAtualizarContas: ref('kia'),
    atualizando: ref(false),
    atualizandoEtapa: ref(''),
    pushing: ref(false),
    bulkPushProgress: ref(''),
    podeEnviarPreco: ref(podeEnviar),
    podeConferir: ref(podeConf),
    gridAccounts: ref(colunas),
    plataformasDesmarcadas: ref(new Set(filtro ? ['amazon'] : [])),
    contasDesmarcadas: ref(new Set()),
    equipesDesmarcadas: ref(new Set()),
    grid: ref({ cells: [{}] }),
    conferencia: ref(null),
    conferenciaIniciando: ref(false),
    conferenciaRodando: ref(false),
    department: ref('celular'),
  }
  const passos = []
  const avisos = []
  const deps = []
  s.toast = { warning: (...a) => avisos.push(a) }
  s.contasMarcadasAtualizar = comp('contasMarcadasAtualizar', s)
  s.contasDoAtualizar = comp('contasDoAtualizar', s)
  s.motivoAtualizarBloqueado = comp('motivoAtualizarBloqueado', s)
  s.rotuloAtualizar = comp('rotuloAtualizar', s)
  s.enviarPrecoDasContas = async (contas) => {
    passos.push(['anuncio', contas])
    if (s.aoEnviar) s.aoEnviar()
    return s.desistir !== true
  }
  s.conferirAnuncios = async (contas, dep) => { passos.push(['conferir', contas]); deps.push(dep) }
  s.loadGrid = async () => { passos.push(['planilha']) }
  const nomes = Object.keys(s)
  const f = new Function(...nomes, [
    funcao('abrirPainelAtualizar'), funcao('escolherModoContas'), funcao('alternarContaAtualizar'),
    funcao('executarAtualizar'),
    'return { abrirPainelAtualizar, escolherModoContas, alternarContaAtualizar, executarAtualizar }',
  ].join('\n'))(...nomes.map((n) => s[n]))
  return { s, f, passos, avisos, deps }
}

;(async () => {
  // ---------- 1) ao abrir: planilha marcada, conferir e anúncio não, Contas em Geral
  {
    const { s, f } = montar()
    f.abrirPainelAtualizar(true)
    assert.equal(s.painelAtualizarAberto.value, true)
    assert.deepEqual(
      [s.atualizarPlanilha.value, s.atualizarConferir.value, s.atualizarAnuncio.value, s.atualizarModoContas.value],
      [true, false, false, 'geral'],
    )
    assert.equal(s.atualizarContasMarcadas.value.size, 0)
    assert.equal(s.buscaAtualizarContas.value, '')
    // Fechar não mexe no que está marcado.
    s.atualizarAnuncio.value = true
    f.abrirPainelAtualizar(false)
    assert.equal(s.painelAtualizarAberto.value, false)
    assert.equal(s.atualizarAnuncio.value, true)
    console.log('pricing atualizar: padrão ao abrir ok')
  }

  // ---------- 2) Geral e Por conta não andam juntas
  {
    const { s, f } = montar()
    f.abrirPainelAtualizar(true)
    assert.deepEqual(s.contasDoAtualizar.value, ['a1', 'a2', 'a3'], 'Geral = todas as visíveis')
    f.escolherModoContas('conta', true)
    assert.equal(s.atualizarModoContas.value, 'conta')
    assert.deepEqual(s.contasDoAtualizar.value, [])
    f.alternarContaAtualizar('a3', true)
    f.alternarContaAtualizar('a1', true)
    // Na ordem da tabela.
    assert.deepEqual(s.contasDoAtualizar.value, ['a1', 'a3'])
    assert.deepEqual(s.contasMarcadasAtualizar.value, ['a1', 'a3'])
    f.escolherModoContas('geral', true)
    assert.equal(s.atualizarModoContas.value, 'geral')
    assert.deepEqual(s.contasDoAtualizar.value, ['a1', 'a2', 'a3'])
    // Marcar uma conta na lista passa para Por conta.
    f.alternarContaAtualizar('a2', true)
    assert.equal(s.atualizarModoContas.value, 'conta')
    assert.deepEqual(s.contasDoAtualizar.value, ['a1', 'a2', 'a3'].filter((id) => s.atualizarContasMarcadas.value.has(id)))
    f.alternarContaAtualizar('a2', false)
    assert.equal(s.atualizarContasMarcadas.value.has('a2'), false)
    // Desmarcar a marcada deixa nenhuma; desmarcar a outra não muda nada.
    f.escolherModoContas('geral', false)
    assert.equal(s.atualizarModoContas.value, 'conta')
    f.escolherModoContas('conta', false)
    assert.equal(s.atualizarModoContas.value, null)
    assert.deepEqual(s.contasDoAtualizar.value, [])
    console.log('pricing atualizar: contas ok')
  }

  // ---------- 3) o botão do rodapé
  {
    const { s, f } = montar()
    f.abrirPainelAtualizar(true)
    assert.equal(s.motivoAtualizarBloqueado.value, '', 'só planilha: pode')
    s.atualizarPlanilha.value = false
    assert.match(s.motivoAtualizarBloqueado.value, /Marque/)
    s.atualizarAnuncio.value = true
    assert.equal(s.motivoAtualizarBloqueado.value, '')
    f.escolherModoContas('conta', true)
    assert.match(s.motivoAtualizarBloqueado.value, /Escolha as contas/)
    f.alternarContaAtualizar('a1', true)
    assert.equal(s.motivoAtualizarBloqueado.value, '')
    s.grid.value = null
    assert.match(s.motivoAtualizarBloqueado.value, /planilha ainda não carregou/)
    s.grid.value = { cells: [{}] }
    s.atualizarAnuncio.value = false
    s.atualizarConferir.value = true
    s.conferenciaRodando.value = true
    assert.match(s.motivoAtualizarBloqueado.value, /conferência rodando/)
    // Sem permissão, a opção marcada não conta.
    const sem = montar({ podeEnviar: false })
    sem.f.abrirPainelAtualizar(true)
    sem.s.atualizarPlanilha.value = false
    sem.s.atualizarAnuncio.value = true
    assert.match(sem.s.motivoAtualizarBloqueado.value, /Marque/)
    console.log('pricing atualizar: rodapé ok')
  }

  // ---------- 4) execução: anúncio, depois conferir, depois planilha
  {
    const { s, f, passos } = montar()
    f.abrirPainelAtualizar(true)
    s.atualizarConferir.value = true
    s.atualizarAnuncio.value = true
    f.escolherModoContas('conta', true)
    f.alternarContaAtualizar('a2', true)
    await f.executarAtualizar()
    assert.deepEqual(passos, [['anuncio', ['a2']], ['conferir', ['a2']], ['planilha']])
    assert.equal(s.painelAtualizarAberto.value, false, 'o painel fecha')
    assert.deepEqual([s.atualizando.value, s.atualizandoEtapa.value], [false, ''])
  }
  {
    // Só a planilha (o padrão): nada vai para os marketplaces.
    const { s, f, passos } = montar()
    f.abrirPainelAtualizar(true)
    await f.executarAtualizar()
    assert.deepEqual(passos, [['planilha']])
    assert.equal(s.atualizando.value, false)
  }
  {
    // Geral sem filtro nenhum vai sem `contas`: o servidor confere as colunas
    // que a pessoa vê (o departamento inteiro para o administrador).
    const m = montar()
    m.f.abrirPainelAtualizar(true)
    m.s.atualizarConferir.value = true
    await m.f.executarAtualizar()
    assert.deepEqual(m.passos, [['conferir', undefined], ['planilha']])
    // Com filtro: só as colunas visíveis.
    const n = montar({ filtro: true, colunas: [A, C] })
    n.f.abrirPainelAtualizar(true)
    n.s.atualizarConferir.value = true
    n.s.atualizarPlanilha.value = false
    await n.f.executarAtualizar()
    assert.deepEqual(n.passos, [['conferir', ['a1', 'a3']]])
    assert.deepEqual(n.deps, ['celular'], 'confere o departamento de quando clicou')
  }
  {
    // Revisão M-14: trocou de departamento durante o envio → a conferência e
    // a planilha (que eram do outro) não rodam, e a tela avisa.
    const { s, f, passos, avisos, deps } = montar()
    f.abrirPainelAtualizar(true)
    s.atualizarConferir.value = true
    s.atualizarAnuncio.value = true
    s.aoEnviar = () => { s.department.value = 'mala' }
    await f.executarAtualizar()
    assert.deepEqual(passos, [['anuncio', ['a1', 'a2', 'a3']]])
    assert.deepEqual(deps, [])
    assert.equal(avisos.length, 1)
    assert.match(avisos[0][0], /conferência não foi feita/)
    assert.deepEqual([s.atualizando.value, s.atualizandoEtapa.value], [false, ''])
    // Sem Conferir marcado: só não recarrega a planilha do outro, sem aviso.
    const o = montar()
    o.f.abrirPainelAtualizar(true)
    o.s.atualizarAnuncio.value = true
    o.s.aoEnviar = () => { o.s.department.value = 'mala' }
    await o.f.executarAtualizar()
    assert.deepEqual(o.passos.map((p) => p[0]), ['anuncio'])
    assert.equal(o.avisos.length, 0)
  }
  {
    // Desistiu de enviar (o "Enviar o preço…?"): o resto não roda.
    const { s, f, passos } = montar()
    f.abrirPainelAtualizar(true)
    s.atualizarConferir.value = true
    s.atualizarAnuncio.value = true
    s.desistir = true
    await f.executarAtualizar()
    assert.deepEqual(passos.map((p) => p[0]), ['anuncio'])
    assert.equal(s.atualizando.value, false)
  }
  {
    // Sem permissão de enviar: a opção marcada não envia.
    const { s, f, passos } = montar({ podeEnviar: false })
    f.abrirPainelAtualizar(true)
    s.atualizarAnuncio.value = true
    await f.executarAtualizar()
    assert.deepEqual(passos, [['planilha']])
  }
  console.log('pricing atualizar: execução ok')

  // ---------- 5) o texto do botão mostra a etapa e o andamento
  {
    const { s } = montar()
    assert.equal(s.rotuloAtualizar.value, 'Atualizar')
    s.atualizando.value = true
    s.atualizandoEtapa.value = 'anuncio'
    s.pushing.value = true
    s.bulkPushProgress.value = '3/40'
    assert.equal(s.rotuloAtualizar.value, 'Enviando 3/40…')
    s.pushing.value = false
    s.atualizandoEtapa.value = 'planilha'
    assert.equal(s.rotuloAtualizar.value, 'Atualizando planilha…')
    s.atualizando.value = false
    s.atualizandoEtapa.value = ''
    s.conferencia.value = { job: { processed: 2, total: 5 } }
    s.conferenciaRodando.value = true
    assert.equal(s.rotuloAtualizar.value, 'Conferindo 2/5…')
    console.log('pricing atualizar: rótulo ok')
  }

  // ---------- 6) "última 18/11 12:06" à direita da linha Conferir
  {
    const conferencia = ref({ job: { status: 'succeeded', processed: 5, total: 5, created_at: 'c', finished_at: 'f' } })
    const t = comp('textoUltimaConferencia', {
      conferencia, conferenciaEstado: ref('ok'), conferenciaRodando: ref(false), fmtQuando: () => '18/11, 12:06',
    })
    assert.equal(t.value, 'última 18/11 12:06')
    conferencia.value = { job: null }
    assert.equal(t.value, 'nunca')
  }

  // ---------- 7) template: o botão, o painel e quem vê cada opção
  const iTabela = tpl.indexOf(`<section v-else-if="tab === 'tabela'"`)
  const barra = tpl.slice(iTabela, tpl.indexOf('<table', iTabela)).replace(/<!--[\s\S]*?-->/g, '')
  const tagCom = (atributo, tag) => {
    const i = barra.indexOf(atributo)
    assert.ok(i > 0, `${atributo} na barra`)
    return barra.slice(barra.lastIndexOf(`<${tag}`, i), barra.indexOf('>', i) + 1)
  }
  const botao = tagCom('data-acao="atualizar"', 'button')
  assert.match(botao, /bg-blue-600/, 'Atualizar azul')
  assert.match(botao, /\bh-7\b/, '28px')
  assert.match(barra, /data-acao="atualizar"[\s\S]*?<RefreshCw[\s\S]*?\{\{ rotuloAtualizar \}\}[\s\S]*?<ChevronDown/)
  assert.match(barra, /data-menu-atualizar\s+class="[^"]*w-\[300px\]/, 'painel de 300px')
  // As opções, na ordem, com as linhas de 26px.
  const ordem = ['O que atualizar', 'data-campo="atualizar-planilha"', 'data-campo="atualizar-conferir"',
    'data-ultima-conferencia', 'data-campo="atualizar-anuncio"', 'Contas', 'data-campo="atualizar-geral"',
    'todas as visíveis', 'data-campo="atualizar-por-conta"', 'marcadas', 'data-campo="atualizar-contas-busca"',
    'data-lista-atualizar-contas', 'data-acao="atualizar-executar"']
  let antes = barra.indexOf('data-menu-atualizar')
  for (const marca of ordem) {
    const i = barra.indexOf(marca, antes)
    assert.ok(i > antes, `painel: ${marca} na ordem`)
    antes = i
  }
  for (const campo of ['atualizar-planilha', 'atualizar-conferir', 'atualizar-anuncio', 'atualizar-geral', 'atualizar-por-conta']) {
    assert.match(tagCom(`data-campo="${campo}"`, 'label'), /h-\[26px\]/, `${campo} com 26px`)
  }
  // Quem vê: Conferir pelo servidor; Atualizar anúncio pela permissão do envio.
  assert.match(tagCom('data-campo="atualizar-conferir"', 'label'), /v-if="podeConferir"/)
  assert.match(tagCom('data-campo="atualizar-anuncio"', 'label'), /v-if="podeEnviarPreco"/)
  assert.match(setup, /const podeEnviarPreco = useCan\('tabela_precos', 'edit'\)/)
  // O mesmo balão e o alerta laranja da conferência na linha Conferir.
  assert.match(tagCom('data-ultima-conferencia', 'span'), /:title="tituloConferir"/)
  assert.match(barra, /data-ultima-conferencia[\s\S]*?<AlertCircle v-if="conferirComAviso" class="[^"]*text-amber-600/)
  // A lista Por conta: agrupada, nome à esquerda e plataforma à direita.
  assert.match(barra, /v-for="group in gruposAtualizarContas"[\s\S]*?\{\{ group\.label \}\}[\s\S]*?\{\{ nomeColuna\(acc\) \}\}[\s\S]*?ml-auto/)
  // Revisão M-14: o motivo do botão apagado aparece escrito no rodapé (o
  // balão do botão desabilitado não abre) e uma conferência que começa com o
  // painel aberto desmarca a caixa Conferir (apagada), que travava o resto.
  assert.match(barra, /data-motivo-atualizar\s*>\s*\{\{ motivoAtualizarBloqueado \}\}[\s\S]*?data-acao="atualizar-executar"/)
  assert.match(setup, /watch\(conferenciaRodando, \(rodando\) => \{\s*if \(rodando\) atualizarConferir\.value = false/)
  // O rodapé executa o que estiver marcado, na largura do painel.
  assert.match(tagCom('data-acao="atualizar-executar"', 'button'), /w-full[^"]*bg-blue-600/)
  assert.match(barra, /@click="executarAtualizar"/)
  // O aviãozinho de cada célula continua.
  assert.match(setup, /headers: \{ 'Idempotency-Key': `cell:\$\{c\.pricing_product_id\}:\$\{c\.pricing_account_id\}/)
  console.log('pricing-atualizar: ok')
})().catch((e) => { console.error(e); process.exit(1) })
