// node tests/imobilizado-sfc.cjs — Cadastros › Imobilizado: compilação, valor em R$,
// erros por campo, cadastro/edição/baixa e o aviso de bens ao desativar usuário (RN08).
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { parse, compileTemplate, compileScript } = require('vue/compiler-sfc')

const transpile = (src, module = ts.ModuleKind.CommonJS) =>
  ts.transpileModule(src, { compilerOptions: { target: ts.ScriptTarget.ES2022, module } }).outputText

function sfc(rel) {
  const filename = path.join(__dirname, rel)
  const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
  assert.deepEqual(errors, [], `${rel}: parse`)
  const t = compileTemplate({ source: descriptor.template.content, filename, id: 'imob-check', compilerOptions: { expressionPlugins: ['typescript'] } })
  assert.deepEqual(t.errors, [], `${rel}: template`)
  compileScript(descriptor, { id: 'imob-check' })
  return descriptor
}

const L = {}
new Function('exports', 'require', transpile(fs.readFileSync(path.join(__dirname, '../lib/imobilizado.ts'), 'utf8')))(L, require)

// ---------------------------------------------------------------- lib
{
  const ok = (t, v) => assert.deepEqual(L.lerValor(t), { valor: v }, t)
  ok('6.890,00', '6890.00')
  ok('6890,5', '6890.50')
  ok('6890.50', '6890.50')
  ok('6.890', '6890.00') // ponto com 3 dígitos = milhar
  ok('R$ 1.234.567,89', '1234567.89')
  ok('0,01', '0.01')
  ok(' 10 ', '10.00')
  for (const t of ['', '  ', null]) assert.match(L.lerValor(t).erro, /Informe/)
  for (const t of ['0', '0,00', '-5']) assert.match(L.lerValor(t).erro, /maior que zero/)
  assert.match(L.lerValor('10,001').erro, /duas casas/)
  assert.match(L.lerValor('abc').erro, /inválido/)
  assert.match(L.lerValor('1,2,3').erro, /inválido/)
  assert.match(L.lerValor('99999999999999').erro, /alto/)

  assert.equal(L.formatBRL('6890.00'), 'R$ 6.890,00')
  assert.equal(L.formatBRL(null), '—')
  assert.equal(L.valorParaCampo('6890.5'), '6.890,50')
  assert.equal(L.textoHistorico('valor', '6500.50'), 'R$ 6.500,50')
  assert.equal(L.textoHistorico('status', 'baixado'), 'Baixado')
  assert.equal(L.textoHistorico('responsavel', null), '—')

  // Erro do Pydantic (loc) e nosso (detail.campo) viram mensagem embaixo do campo.
  const e422 = { data: { detail: [
    { loc: ['body', 'valor'], msg: 'Input should be greater than 0' },
    { loc: ['body', 'descricao'], msg: 'String should have at least 3 characters' },
  ] } }
  assert.deepEqual(L.errosDaApi(e422), { campos: { valor: 'O valor deve ser maior que zero.', descricao: 'Mínimo de 3 caracteres.' }, geral: '' })
  const dup = { data: { detail: { code: 'numero_duplicado', campo: 'numero', numero: 'IMB-000124' } } }
  assert.deepEqual(L.errosDaApi(dup), { campos: { numero: 'Já existe um item com o número IMB-000124.' }, geral: '' })
  assert.deepEqual(L.errosDaApi({ data: { detail: { code: 'item_baixado' } } }), { campos: {}, geral: 'Item baixado não pode ser alterado.' })

  const pessoas = [{ id: 'a', nome: 'João Silva' }, { id: 'b', nome: 'Maria' }]
  assert.deepEqual(L.filtrarPessoas(pessoas, 'joao').map(p => p.id), ['a'])
  assert.equal(L.filtrarPessoas(pessoas, '').length, 2)
}

// ---------------------------------------------------------------- menu e permissão
{
  const can = fs.readFileSync(path.join(__dirname, '../composables/useCan.ts'), 'utf8')
  assert.match(can, /\| 'imobilizado'/)
  assert.match(can, /'email_padroes',[\s\S]*?'imobilizado',\n    \],/, 'no grupo Cadastros da tela de Permissões')
  const py = fs.readFileSync(path.join(__dirname, '../../api/app/schemas/permissions.py'), 'utf8')
  assert.match(py, /^ {4}"imobilizado",$/m, 'mesmo recurso no backend')
  // Aba de Cadastros (Eduardo, 06/10/2026), sem item próprio no menu e sem
  // `resource`: colaborador sem a permissão também chega nos itens dele.
  const nav = fs.readFileSync(path.join(__dirname, '../lib/navGroups.ts'), 'utf8')
  assert.match(nav, /\{ to: '\/imobilizado', label: 'Imobilizado' \},/)
  const menu = fs.readFileSync(path.join(__dirname, '../components/AppSidebar.vue'), 'utf8')
  assert.doesNotMatch(menu, /imobilizado/)
}

// ---------------------------------------------------------------- página
const pagina = sfc('../pages/imobilizado.vue')
// Sem middleware de permissão: quem não tem view vê os próprios itens.
assert.doesNotMatch(pagina.scriptSetup.content, /definePageMeta/)
assert.match(pagina.template.content, /:disabled="!!form\.id"/, 'número travado na edição')

const script = pagina.scriptSetup.content.replace(/^import[\s\S]*?from '[^']+'\n/gm, '')
const factory = new Function(
  'TABS_CADASTROS', 'computed', 'onMounted', 'reactive', 'ref', 'watch', 'useApi', 'useCan', 'isoToday',
  'CAMPO_LABELS', 'STATUS_LABELS', 'errosDaApi', 'filtrarPessoas', 'formatBRL', 'lerValor', 'textoHistorico', 'valorParaCampo',
  transpile(script, ts.ModuleKind.ESNext) +
    '\nreturn {filtros,lista,erro,aviso,carregar,carregarPessoas,pessoas,pessoasAtivas,form,errosCampo,erroForm,novo,editar,escolherResponsavel,digitouResponsavel,salvar,sugestoes,detalhe,abrir,abrirBaixa,confirmarBaixa,formatarValor,dataBR};',
)

const ITEM = {
  id: 7, numero: 'IMB-000124', descricao: 'Notebook Dell Latitude 5440', valor: '6890.00',
  responsavel: { id: 'u-ana', nome: 'Ana' }, status: 'ativo', baixa_data: null, baixa_motivo: null,
  criado_em: '2026-10-06T12:00:00Z', criado_por: { id: 'u-adm', nome: 'Adm' }, atualizado_em: null, atualizado_por: null,
}

function montar({ perms = { view: true, edit: true, delete: true }, falha = null } = {}) {
  const chamadas = []
  const api = async (url, opts = {}) => {
    chamadas.push({ url, opts })
    if (falha && falha.url === url && (falha.method || 'GET') === (opts.method || 'GET')) throw falha.erro
    if (url === '/api/imobilizado' && !opts.method) return { itens: [ITEM], quantidade: 1, soma: '6890.00' }
    if (url === '/api/imobilizado/responsaveis') return [{ id: 'u-ana', nome: 'Ana', ativo: true }, { id: 'u-bia', nome: 'Bia', ativo: true }, { id: 'u-old', nome: 'Velho', ativo: false }]
    if (url === '/api/imobilizado/proximo-numero') return { numero: 'IMB-000125' }
    if (url.endsWith('/historico')) return [{ id: 1, campo: 'valor', valor_anterior: '1.00', valor_novo: '2.00', alterado_em: '2026-10-06T12:00:00Z', alterado_por: null }]
    if (url === '/api/imobilizado' && opts.method === 'POST') return { ...ITEM, id: 8, ...opts.body, responsavel: { id: opts.body.responsavel_id, nome: 'Bia' } }
    if (opts.method === 'PUT') return { ...ITEM, ...opts.body, responsavel: { id: opts.body.responsavel_id, nome: 'Bia' } }
    if (url.endsWith('/baixa')) return { ...ITEM, status: 'baixado', ...opts.body }
    throw new Error(`chamada inesperada ${opts.method || 'GET'} ${url}`)
  }
  const p = factory([], Vue.computed, () => {}, Vue.reactive, Vue.ref, () => {}, () => ({ api }),
    (_r, a) => Vue.ref(!!perms[a]), () => '2026-10-06',
    L.CAMPO_LABELS, L.STATUS_LABELS, L.errosDaApi, L.filtrarPessoas, L.formatBRL, L.lerValor, L.textoHistorico, L.valorParaCampo)
  return { p, chamadas }
}

;(async () => {
  // Listagem: filtros vão na query; colaborador não manda responsável nem carrega pessoas.
  {
    const { p, chamadas } = montar()
    await p.carregarPessoas()
    assert.deepEqual(p.pessoasAtivas.value.map(x => x.id), ['u-ana', 'u-bia'])
    p.filtros.busca = ' note '
    p.filtros.responsavel_id = 'u-ana'
    p.filtros.status = 'todos'
    await p.carregar()
    assert.deepEqual(chamadas.at(-1).opts.query, { status: 'todos', busca: 'note', responsavel_id: 'u-ana' })
    assert.equal(p.lista.value.quantidade, 1)
  }
  {
    const { p, chamadas } = montar({ perms: {} })
    await p.carregarPessoas()
    p.filtros.responsavel_id = 'u-bia'
    await p.carregar()
    assert.equal(chamadas.length, 1)
    assert.deepEqual(chamadas[0].opts.query, { status: 'ativo' })
  }

  // Cadastro: número sugerido, validação local embaixo de cada campo, corpo certo.
  {
    const { p, chamadas } = montar()
    await p.carregarPessoas()
    await p.novo()
    assert.equal(p.form.numero, 'IMB-000125')
    p.form.descricao = 'ab'
    p.form.valor = '0'
    await p.salvar()
    assert.deepEqual(Object.keys(p.errosCampo.value).sort(), ['descricao', 'responsavel_id', 'valor'])
    assert.equal(chamadas.filter(c => c.opts.method === 'POST').length, 0)

    p.form.numero = ' imb-000200 '
    p.form.descricao = 'Cadeira'
    p.form.valor = '1.250,5'
    p.formatarValor()
    assert.equal(p.form.valor, '1.250,50')
    p.form.responsavelBusca = 'bi'
    p.digitouResponsavel()
    assert.deepEqual(p.sugestoes.value.map(x => x.nome), ['Bia'])
    p.escolherResponsavel(p.sugestoes.value[0])
    await p.salvar()
    const post = chamadas.find(c => c.opts.method === 'POST')
    assert.deepEqual(post.opts.body, { numero: 'IMB-000200', descricao: 'Cadeira', valor: '1250.50', responsavel_id: 'u-bia' })
    assert.equal(p.form.aberto, false)
    assert.match(p.aviso.value, /IMB-000200 cadastrado/)
  }
  // Inativo não aparece para escolher; digitar depois de escolher desfaz a escolha.
  {
    const { p } = montar()
    await p.carregarPessoas()
    await p.novo()
    p.form.responsavelBusca = 'velho'
    p.digitouResponsavel()
    assert.equal(p.sugestoes.value.length, 0)
    p.escolherResponsavel({ id: 'u-ana', nome: 'Ana' })
    p.form.responsavelBusca = 'Anx'
    p.digitouResponsavel()
    assert.equal(p.form.responsavel_id, '')
  }
  // Número repetido: mensagem embaixo do Número.
  {
    const erro = { data: { detail: { code: 'numero_duplicado', campo: 'numero', numero: 'IMB-000124' } } }
    const { p } = montar({ falha: { url: '/api/imobilizado', method: 'POST', erro } })
    await p.carregarPessoas()
    await p.novo()
    Object.assign(p.form, { numero: 'IMB-000124', descricao: 'Notebook', valor: '10' })
    p.escolherResponsavel({ id: 'u-ana', nome: 'Ana' })
    await p.salvar()
    assert.equal(p.errosCampo.value.numero, 'Já existe um item com o número IMB-000124.')
    assert.equal(p.form.aberto, true)
  }
  // Edição: número não vai no corpo (RN02).
  {
    const { p, chamadas } = montar()
    await p.carregarPessoas()
    await p.abrir(ITEM)
    p.editar(ITEM)
    assert.equal(p.form.valor, '6.890,00')
    p.escolherResponsavel({ id: 'u-bia', nome: 'Bia' })
    await p.salvar()
    const put = chamadas.find(c => c.opts.method === 'PUT')
    assert.equal(put.url, '/api/imobilizado/7')
    assert.deepEqual(put.opts.body, { descricao: ITEM.descricao, valor: '6890.00', responsavel_id: 'u-bia' })
    assert.equal(p.detalhe.item.responsavel.id, 'u-bia')
  }
  // Baixa: exige data e motivo, não aceita data futura, depois mostra baixado.
  {
    const { p, chamadas } = montar()
    await p.abrir(ITEM)
    assert.equal(p.detalhe.historico.length, 1)
    p.abrirBaixa()
    assert.equal(p.detalhe.baixaData, '2026-10-06')
    await p.confirmarBaixa()
    assert.match(p.detalhe.errosBaixa.baixa_motivo, /Informe o motivo/)
    p.detalhe.baixaData = '2026-10-07'
    p.detalhe.baixaMotivo = 'Quebrou'
    await p.confirmarBaixa()
    assert.match(p.detalhe.errosBaixa.baixa_data, /depois de hoje/)
    p.detalhe.baixaData = '2026-10-05'
    await p.confirmarBaixa()
    const baixa = chamadas.find(c => c.url.endsWith('/baixa'))
    assert.deepEqual(baixa.opts.body, { baixa_data: '2026-10-05', baixa_motivo: 'Quebrou' })
    assert.equal(p.detalhe.item.status, 'baixado')
    assert.equal(p.detalhe.baixaAberta, false)
    assert.equal(p.dataBR('2026-10-05'), '05/10/2026')
  }

  // ---------------------------------------------------------------- usuário (RN08)
  const usuario = sfc('../pages/users/[id].vue')
  const u = usuario.scriptSetup.content
  assert.match(u, /det\?\.code !== 'responsavel_imobilizado'/)
  assert.match(u, /query: ignorarImobilizado \? \{ ignorar_imobilizado: true \} : undefined/)
  assert.match(u, /api\('\/api\/imobilizado\/transferir', \{ method: 'POST', body: \{ de_id: userId, para_id: imob\.para_id \} \}\)/)
  // O clique não pode passar o evento como "ignorar".
  assert.match(usuario.template.content, /@click="removeUser\(\)"/)
  assert.match(usuario.template.content, /@click="saveCadastral\(\)"/)

  console.log('imobilizado-sfc: ok')
})().catch(e => { console.error(e); process.exit(1) })
