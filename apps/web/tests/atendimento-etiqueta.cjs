// node tests/atendimento-etiqueta.cjs — a ETIQUETA (status atual) na caixa (01/10/2026).
// RF1 do Comunicador: cada conversa tem UMA etiqueta, que muda sozinha
// (Pós-venda → Reclamação → Pós-venda), com as outras abertas no indicador
// pequeno. Aqui:
//  - as cores decididas (Reclamação vermelho, Devolução roxo, Pré-venda azul,
//    Ag. cancelamento laranja; Pós-venda sem destaque) e a prioridade igual à
//    do backend (constantes.PRIORIDADE_ETIQUETAS);
//  - o indicador das secundárias (sem a base, sem repetir, na prioridade);
//  - a lista: faixa colorida à esquerda, selo (sem o de Pós-venda), menu
//    Filtrar com Reclamação/Devolução/Ag. cancelamento e as contagens do /resumo
//    (os códigos aceitos pela API: FILTROS do router);
//  - a troca à mão: POST /conversas/{id}/etiqueta com o motivo, e a resposta
//    volta para quem usa o selo.
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
// O <script> comum (não o setup) é o módulo dos ajudantes exportados.
function exportsDe(descriptor) {
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(descriptor.script.content))((n) => require(n), mod, mod.exports)
  return mod.exports
}

const etiquetaSfc = sfc('../components/AtendimentoEtiqueta.vue')
const listaSfc = sfc('../components/AtendimentoLista.vue')
const plataforma = exportsDe(sfc('../components/AtendimentoPlataforma.vue'))
const E = exportsDe(etiquetaSfc)
const L = exportsDe(listaSfc)

// ------------------------------------------------ backend: os mesmos códigos e a mesma ordem
const constantes = fs.readFileSync(path.resolve(__dirname, '../../api/app/services/atendimento/constantes.py'), 'utf8')
const nomes = Object.fromEntries([...constantes.matchAll(/^(ETIQUETA_[A-Z_]+) = "([a-z_]+)"$/gm)].map((m) => [m[1], m[2]]))
const prioridadeApi = (constantes.match(/PRIORIDADE_ETIQUETAS: tuple\[str, \.\.\.\] = \(([\s\S]*?)\)/) || [])[1]
assert.ok(prioridadeApi, 'PRIORIDADE_ETIQUETAS no backend')
const ordemApi = prioridadeApi.split(',').map((s) => s.trim()).filter(Boolean).map((n) => nomes[n])
assert.deepEqual(E.PRIORIDADE_ETIQUETAS, ordemApi, 'a tela e o backend com a mesma prioridade')
assert.deepEqual(ordemApi, ['reclamacao', 'ag_cancelamento', 'devolucao', 'pre_venda', 'pos_venda'])
assert.deepEqual(Object.keys(E.ETIQUETAS_INFO).sort(), [...ordemApi].sort(), 'toda etiqueta do backend tem cor')

const router = fs.readFileSync(path.resolve(__dirname, '../../api/app/routers/atendimento.py'), 'utf8')
const filtrosApi = [...((router.match(/^FILTROS = \(([\s\S]*?)^\)/m) || [])[1] || '').matchAll(/"([a-z_]+)"/g)].map((m) => m[1])
for (const f of [...L.ABAS_LISTA, ...L.FILTROS_MENU]) assert.ok(filtrosApi.includes(f.value), `filtro ${f.value} existe na API`)
for (const e of ordemApi) assert.ok(L.FILTROS_ETIQUETA.has(e) && filtrosApi.includes(e), `menu filtra por ${e}`)

// ------------------------------------------------ cores e destaque
const cor = (e) => E.ETIQUETAS_INFO[e]
assert.match(cor('reclamacao').cls, /red-/)
assert.match(cor('reclamacao').faixa, /bg-red-500/)
assert.match(cor('devolucao').faixa, /bg-purple-500/)
assert.match(cor('pre_venda').faixa, /bg-blue-500/)
assert.match(cor('ag_cancelamento').faixa, /bg-orange-500/)
assert.equal(cor('pos_venda').destaque, false, 'Pós-venda sem destaque')
assert.equal(E.faixaDaEtiqueta('pos_venda'), '')
assert.equal(E.faixaDaEtiqueta(null), '')
assert.equal(E.faixaDaEtiqueta(' Reclamacao '), 'bg-red-500')
assert.equal(E.etiquetaInfo(''), null)
// Etiqueta nova no backend que a tela ainda não conhece: neutra, com o código.
assert.deepEqual([E.etiquetaInfo('avaliacao').label, E.etiquetaInfo('avaliacao').destaque], ['avaliacao', false])
assert.deepEqual(E.OPCOES_ETIQUETA.map((o) => o.value), ordemApi, 'troca à mão na ordem da prioridade')

// ------------------------------------------------ indicador das secundárias
const sec = (e, s) => E.secundariasDe(e, s).map((o) => o.value)
assert.deepEqual(sec('reclamacao', ['devolucao', 'ag_cancelamento']), ['ag_cancelamento', 'devolucao'], 'na prioridade')
assert.deepEqual(sec('pos_venda', ['reclamacao', 'reclamacao', 'pos_venda', 'pre_venda']), ['reclamacao'], 'sem repetir e sem a base')
assert.deepEqual(sec('devolucao', ['devolucao']), [], 'nunca a própria')
assert.deepEqual(sec('reclamacao', null), [])
const titulo = E.tituloDaEtiqueta('pos_venda', { manual: true, secundarias: ['reclamacao'], desde: '2026-10-01T12:00:00Z' })
assert.match(titulo, /^Pós-venda/)
assert.match(titulo, /trocada à mão/)
assert.match(titulo, /também aberta: Reclamação/)
assert.match(titulo, /desde \d\d\/\d\d/)

// ------------------------------------------------ a lista
{
  const tpl = listaSfc.template.content
  assert.match(tpl, /v-if="faixaDaEtiqueta\(c\.etiqueta\)"[\s\S]{0,200}data-faixa/, 'faixa à esquerda')
  assert.match(tpl, /<AtendimentoEtiqueta[\s\S]{0,600}esconder-pos-venda/, 'selo sem o de Pós-venda')
  assert.match(tpl, /:secundarias="c\.etiquetas_secundarias"/)
  assert.match(tpl, />Etiqueta<\/div>/, 'o menu separa os filtros de etiqueta')
  assert.match(tpl, /placeholder="buscar comprador, pedido, nº do Bling, SKU…"/)
  const menu = L.FILTROS_MENU.map((f) => f.value)
  assert.deepEqual(menu.filter((v) => L.FILTROS_ETIQUETA.has(v)), ordemApi, 'etiquetas juntas, na prioridade')
  assert.match(L.FILTROS_MENU.find((f) => f.value === 'pos_venda').hint, /sem nada aberto/)
}

// ------------------------------------------------ contagens do menu (do /resumo)
{
  const src = listaSfc.scriptSetup.content
  const corpo = src.slice(src.indexOf('function porEtiqueta'), src.indexOf('function contadorCls'))
  const js = transpile(corpo) + '\nreturn contagem'
  function contagemPara(resumo, filtros) {
    return new Function('computed', 'props', 'filtros', 'FILTROS_ETIQUETA', js)(
      Vue.computed, { resumo }, { value: filtros }, L.FILTROS_ETIQUETA,
    ).value
  }
  const etq = (r, d, a, pr, po) => ({ reclamacao: r, devolucao: d, ag_cancelamento: a, pre_venda: pr, pos_venda: po })
  const resumo = {
    a_conferir: 0,
    etiquetas: etq(3, 2, 1, 10, 40),
    plataformas: [
      { plataforma: 'shopee', aguardando: 5, vencendo: 1, vencidas: 0, a_conferir: 0, etiquetas: etq(2, 2, 1, 9, 30) },
      { plataforma: 'ml', aguardando: 1, vencendo: 0, vencidas: 0, a_conferir: 0, etiquetas: etq(1, 0, 0, 1, 10) },
      { plataforma: 'amazon', aguardando: 0, vencendo: 0, vencidas: 0, a_conferir: 0 },
    ],
    lojas: [{ integration_id: 'L1', plataforma: 'ml', aguardando: 1, vencidas: 0, etiquetas: etq(1, 0, 0, 0, 4) }],
  }
  const base = { plataforma: '', integration_id: '', canal: '', filtro: 'todas', q: '' }
  const tudo = contagemPara(resumo, base)
  assert.deepEqual([tudo.reclamacao, tudo.devolucao, tudo.ag_cancelamento, tudo.pos_venda], [3, 2, 1, 40])
  const ml = contagemPara(resumo, { ...base, plataforma: 'ml' })
  assert.deepEqual([ml.reclamacao, ml.pre_venda], [1, 1])
  // Plataforma sem conversa (sem `etiquetas`): zero, não "sem número".
  assert.equal(contagemPara(resumo, { ...base, plataforma: 'amazon' }).reclamacao, 0)
  const loja = contagemPara(resumo, { ...base, integration_id: 'L1' })
  assert.deepEqual([loja.reclamacao, loja.pos_venda], [1, 4])
  // API antiga (sem `etiquetas`): o menu fica sem número.
  const antiga = contagemPara({ ...resumo, etiquetas: undefined }, base)
  assert.equal(antiga.reclamacao, null)
}

// ------------------------------------------------ o selo e a troca à mão (script setup)
async function testarSelo() {
  const src = etiquetaSfc.scriptSetup.content.replace(/^import[\s\S]*?from\s+'[^']+'\s*$/gm, '')
  const js = transpile(src) + '\nreturn { info, mostrarSelo, outras, clsSelo, podeEditar, aberto, escolhida, motivo, salvando, erro, abrir, salvar, linhaDoTempo }'
  function montar(props, apiFalsa) {
    const emitidos = []
    const chamadas = []
    const api = async (url, opts) => { chamadas.push({ url, opts }); return apiFalsa(url, opts) }
    const vm = new Function(
      'ref', 'computed', 'withDefaults', 'defineProps', 'defineEmits', 'useApi', 'onClickOutside', 'erroDaApi',
      'etiquetaInfo', 'secundariasDe', 'tituloDaEtiqueta', 'OPCOES_ETIQUETA',
      js,
    )(
      Vue.ref, Vue.computed, (p, d) => ({ ...d, ...p }), () => props, () => (n, v) => emitidos.push([n, v]),
      () => ({ api }), () => {}, plataforma.erroDaApi,
      E.etiquetaInfo, E.secundariasDe, E.tituloDaEtiqueta, E.OPCOES_ETIQUETA,
    )
    return { vm, emitidos, chamadas }
  }
  const nada = async () => { throw new Error('não chama') }
  // Na lista: Pós-venda não aparece (só o indicador, se houver).
  let { vm } = montar({ etiqueta: 'pos_venda', secundarias: ['reclamacao'], esconderPosVenda: true }, nada)
  assert.equal(vm.mostrarSelo.value, false)
  assert.deepEqual(vm.outras.value.map((o) => o.value), ['reclamacao'])
  ;({ vm } = montar({ etiqueta: 'reclamacao', selecionada: true }, nada))
  assert.equal(vm.mostrarSelo.value, true)
  assert.equal(vm.clsSelo.value, 'bg-white/20 text-current', 'na linha azul, translúcido')
  assert.equal(vm.podeEditar.value, false)
  vm.abrir()
  assert.equal(vm.aberto.value, false, 'sem `editavel` não abre')

  // No cabeçalho: troca à mão com motivo; a resposta vai para quem usa.
  const resposta = { conversa: { id: 'c1', etiqueta: 'pos_venda', etiqueta_manual: true }, etiqueta_historico: [] }
  const m = montar({ etiqueta: 'reclamacao', editavel: true, conversaId: 'c/1', historico: [{ id: 'h1' }, { id: 'h2' }] }, async () => resposta)
  m.vm.abrir()
  assert.equal(m.vm.aberto.value, true)
  assert.equal(m.vm.escolhida.value, 'reclamacao', 'começa na atual')
  assert.deepEqual(m.vm.linhaDoTempo.value.map((h) => h.id), ['h2', 'h1'], 'a mais nova em cima')
  m.vm.escolhida.value = 'pos_venda'
  m.vm.motivo.value = '  resolvido no chat '
  await m.vm.salvar()
  assert.deepEqual(m.chamadas, [{
    url: '/api/atendimento/conversas/c%2F1/etiqueta',
    opts: { method: 'POST', body: { etiqueta: 'pos_venda', motivo: 'resolvido no chat' } },
  }])
  assert.deepEqual(m.emitidos, [['trocada', resposta]])
  assert.equal(m.vm.aberto.value, false)

  // Erro da API vira frase de gente e a caixa fica aberta.
  const falha = montar({ etiqueta: 'reclamacao', editavel: true, conversaId: 'c1' }, async () => {
    const e = new Error('422'); e.data = { detail: { code: 'etiqueta_invalida' } }; throw e
  })
  falha.vm.abrir()
  await falha.vm.salvar()
  assert.match(falha.vm.erro.value, /Etiqueta desconhecida/)
  assert.equal(falha.vm.aberto.value, true)
  assert.deepEqual(falha.emitidos, [])
}


// ------------------------------------------------ tipos do contrato
{
  const tipos = fs.readFileSync(path.resolve(__dirname, '../components/AtendimentoPlataforma.vue'), 'utf8')
  for (const campo of ['etiqueta?: string | null', 'etiquetas_secundarias?: string[]', 'etiqueta_manual?: boolean', 'etiqueta_historico?: EtiquetaHistorico[]', 'etiquetas?: ContagemEtiquetas']) {
    assert.ok(tipos.includes(campo), campo)
  }
  assert.match(plataforma.ERROS.etiqueta_invalida, /Etiqueta desconhecida/)
  assert.match(plataforma.ERROS.cursor_invalido, /atualize/)
}

testarSelo().then(() => console.log('ok: atendimento-etiqueta'), (e) => {
  console.error(e)
  process.exit(1)
})
