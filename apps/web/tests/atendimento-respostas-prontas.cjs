// node tests/atendimento-respostas-prontas.cjs — respostas prontas rápidas e
// por equipe (03/11/2026, o Eduardo: "rapido de acesso a respostas prontas
// para os usuarios do atendimento e separe por 3 equipes ... as q ja tem la
// deixe pra todos mas as novas que eles vao criar vai ter essa ideia de
// especifico por equipe"). Aqui:
//  - a busca (sem acento, por palavra, título antes do texto) e o atalho "/";
//  - a caixa de resposta: "/" abre a lista em cima dela, ↑/↓ escolhe, Enter
//    (ou Tab, ou Ctrl+Enter) põe a resposta no lugar do "/busca" com as lacunas
//    preenchidas, Esc fecha; a lista não usa portal (vale na tela cheia);
//  - o envio pelo teclado (o Eduardo, 03/11): Enter envia, Shift+Enter quebra a
//    linha, Ctrl+Enter também envia; no e-mail só o Ctrl+Enter envia;
//  - o menu "Respostas prontas": a mesma busca e ↑/↓ + Enter;
//  - a aba: a equipe de cada uma, o filtro, o campo Equipe (o admin escolhe,
//    quem é de equipe fica com a dele) e os botões só onde a pessoa pode mexer;
//  - o contrato com a API (ModeloOut, /modelos/equipes).
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')

const transpile = (source) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText

function sfc(rel) {
  const filename = path.resolve(__dirname, rel)
  const fonte = fs.readFileSync(filename, 'utf8')
  const { descriptor, errors } = parse(fonte, { filename })
  assert.deepEqual(errors, [], rel)
  const tpl = compileTemplate({ source: descriptor.template.content, filename, id: path.basename(rel), compilerOptions: { isTS: true } })
  assert.deepEqual(tpl.errors, [], `${rel}: template compila`)
  compileScript(descriptor, { id: path.basename(rel) })
  return { descriptor, fonte, filename }
}
function exportsDe(descriptor) {
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(descriptor.script.content))(require, mod, mod.exports)
  return mod.exports
}
const api = (rel) => fs.readFileSync(path.resolve(__dirname, '../../api/app', rel), 'utf8')

const P = exportsDe(sfc('../components/AtendimentoPlataforma.vue').descriptor)
const conversaSfc = sfc('../components/AtendimentoConversa.vue')
const modelosSfc = sfc('../components/AtendimentoModelos.vue')

const M = (o) => ({ id: o.titulo, plataforma: null, canal: null, ativo: true, ordem: 0, equipe: null, equipe_nome: null, pode_editar: false, ...o })
const RASTREIO = M({ titulo: 'Rastreio enviado', texto: 'Olá, {comprador}! O rastreio do pedido {numero_pedido} é {rastreio}.', ordem: 10 })
const NOTA = M({ titulo: 'Nota fiscal', texto: 'Segue a nota {nf_numero}; o rastreio vem por e-mail.', ordem: 5, equipe: 101, equipe_nome: 'Londres', pode_editar: true })
const PRAZO = M({ titulo: 'Prazo de envio', texto: 'Enviamos em até 2 dias úteis.', ordem: 20 })
const SO_ML = M({ titulo: 'Só ML', texto: 'rastreio ML', plataforma: 'ml' })
const INATIVA = M({ titulo: 'Velha rastreio', texto: 'x', ativo: false })

// ─── a busca e o atalho (puras) ─────────────────────────────────────────────
{
  const lista = [RASTREIO, NOTA, PRAZO]
  // Vazia: todas, na ordem do menu.
  assert.deepEqual(P.filtrarProntas(lista, '').map((m) => m.titulo), ['Nota fiscal', 'Rastreio enviado', 'Prazo de envio'])
  // Pelo título primeiro, depois pelo texto; sem acento; todas as palavras.
  assert.deepEqual(P.filtrarProntas(lista, 'rastreio').map((m) => m.titulo), ['Rastreio enviado', 'Nota fiscal'])
  assert.deepEqual(P.filtrarProntas(lista, 'UTEIS').map((m) => m.titulo), ['Prazo de envio'])
  assert.deepEqual(P.filtrarProntas(lista, 'rastr env').map((m) => m.titulo), ['Rastreio enviado'])
  assert.deepEqual(P.filtrarProntas(lista, 'nota prazo'), [])
  assert.deepEqual(P.filtrarProntas([M({ titulo: 'Devolução', texto: 'x' })], 'devolucao').map((m) => m.titulo), ['Devolução'])
  // O atalho: a caixa começa com "/" e é uma linha só.
  assert.equal(P.buscaDoAtalho('/'), '')
  assert.equal(P.buscaDoAtalho('/rastreio env'), 'rastreio env')
  assert.equal(P.buscaDoAtalho('olá /rastreio'), null)
  assert.equal(P.buscaDoAtalho('/rastreio\nmais'), null)
  assert.equal(P.buscaDoAtalho(''), null)
  assert.equal(P.buscaDoAtalho(null), null)
}

// ─── a caixa de resposta: o trecho da tela com a conversa falsa ─────────────
const setup = conversaSfc.descriptor.scriptSetup.content
const ini = setup.indexOf('// ─── respostas prontas ───')
const fim = setup.indexOf('// ─── etiqueta: troca à mão')
assert.ok(ini > 0 && fim > ini)
const aoTeclarFonte = /function aoTeclar\(e: KeyboardEvent\) \{[\s\S]*?\n\}\n/.exec(setup)
assert.ok(aoTeclarFonte, 'aoTeclar')
assert.match(aoTeclarFonte[0], /if \(atalhoAberto\.value && !e\.isComposing && teclaDoAtalho\(e\)\) return/, 'a lista aberta fica com as teclas antes do Ctrl+Enter')
const dica = /const dicaDoEnvio = [^\n]*\n/.exec(setup)
assert.ok(dica, 'dicaDoEnvio')
const js = transpile(setup.slice(ini, fim) + dica[0] + aoTeclarFonte[0]) +
  '\nreturn { dicaDoEnvio, texto, atalhoAberto, listaAtalho, atalhoIdx, atalhoFechado, buscaAtalho, aoTeclar, usarDoAtalho, buscaModelo, modelosDaConversa, menuIdx, teclaDoMenu, menuModelos, prefill, baseRascunhoId, rejeitadaId }'

const tecla = (key, extra = {}) => {
  const e = { key, ctrlKey: false, metaKey: false, shiftKey: false, isComposing: false, prevenido: false, parado: false, ...extra }
  e.preventDefault = () => { e.prevenido = true }
  e.stopPropagation = () => { e.parado = true }
  return e
}
function montarCaixa({ modelos = [RASTREIO, NOTA, PRAZO, SO_ML, INATIVA], podeDigitar = true, email = false } = {}) {
  const enviados = []
  const caixa = Vue.ref({ selectionStart: 0, selectionEnd: 0, focus() {}, setSelectionRange() {} })
  const detalhe = Vue.ref({ conversa: { id: 'c1' }, contexto: { pedido: { numeroloja: '2511ABC' }, logistica: { rastreio: 'BR123', transportadora: 'Correios' }, nota_fiscal: { numero: '4321' } } })
  const conversa = Vue.computed(() => ({ id: 'c1', plataforma: 'shopee', canal: 'chat', comprador_nome: 'maria silva', pedido_marketplace: '2511ABC' }))
  const nomes = ['ref', 'computed', 'watch', 'nextTick', 'props', 'conversa', 'texto', 'prefill', 'baseRascunhoId', 'rejeitadaId', 'caixa', 'podeDigitar', 'detalhe', 'chamadoNaTela', 'fmtData', 'preencherLacunas', 'filtrarProntas', 'buscaDoAtalho', 'enviar', 'ehEmail']
  const valores = [Vue.ref, Vue.computed, Vue.watch, (f) => (f ? f() : Promise.resolve()), { modelos }, conversa, Vue.ref(''), Vue.ref(''), Vue.ref(null), Vue.ref(null), caixa, Vue.ref(podeDigitar), detalhe, Vue.ref(null), (d) => d, P.preencherLacunas, P.filtrarProntas, P.buscaDoAtalho, async () => { enviados.push(true) }, Vue.ref(email)]
  return { t: new Function(...nomes, js)(...valores), enviados }
}
const tique = () => new Promise((r) => setTimeout(r, 0))

async function caixaDeResposta() {
  // "/" sozinho: as que servem nesta caixa (sem a do ML nem a inativa), na ordem.
  {
    const { t } = montarCaixa()
    assert.equal(t.atalhoAberto.value, false)
    t.texto.value = '/'
    assert.equal(t.atalhoAberto.value, true)
    assert.deepEqual(t.listaAtalho.value.map((m) => m.titulo), ['Nota fiscal', 'Rastreio enviado', 'Prazo de envio'])
  }
  // "/rastr", ↓ e Enter: a 2ª (a que casa pelo texto) entra no lugar do
  // "/rastr", com as lacunas preenchidas. Nada foi enviado.
  {
    const { t, enviados } = montarCaixa()
    t.texto.value = '/rastr'
    await tique()
    assert.deepEqual(t.listaAtalho.value.map((m) => m.titulo), ['Rastreio enviado', 'Nota fiscal'])
    let e = tecla('ArrowDown')
    t.aoTeclar(e)
    assert.equal(e.prevenido, true)
    assert.equal(t.atalhoIdx.value, 1)
    t.aoTeclar(tecla('ArrowDown'))
    assert.equal(t.atalhoIdx.value, 0, 'volta ao começo')
    t.aoTeclar(tecla('ArrowUp'))
    assert.equal(t.atalhoIdx.value, 1, 'e de cima vai para o fim')
    e = tecla('Enter')
    t.aoTeclar(e)
    assert.equal(e.prevenido, true)
    assert.equal(t.texto.value, 'Segue a nota 4321; o rastreio vem por e-mail.')
    assert.equal(t.atalhoAberto.value, false)
    assert.deepEqual(enviados, [])
  }
  // Enter com o primeiro: lacunas do pedido e o primeiro nome do comprador.
  {
    const { t } = montarCaixa()
    t.texto.value = '/rastreio enviado'
    t.aoTeclar(tecla('Tab'))
    assert.equal(t.texto.value, 'Olá, Maria! O rastreio do pedido 2511ABC é BR123.')
  }
  // Ctrl+Enter com a lista aberta insere (mandar "/prazo" nunca é o que se quer);
  // com ela fechada, envia como sempre.
  {
    const { t, enviados } = montarCaixa()
    t.texto.value = '/prazo'
    t.aoTeclar(tecla('Enter', { ctrlKey: true }))
    assert.equal(t.texto.value, 'Enviamos em até 2 dias úteis.')
    assert.deepEqual(enviados, [])
    t.aoTeclar(tecla('Enter', { ctrlKey: true }))
    assert.deepEqual(enviados, [true])
  }
  // Esc fecha (e não deixa a tela cheia sair); a barra que sai e volta reabre.
  {
    const { t } = montarCaixa()
    t.texto.value = '/nota'
    await tique()
    const e = tecla('Escape')
    t.aoTeclar(e)
    assert.equal(e.parado, true)
    assert.equal(t.atalhoAberto.value, false)
    // Fechada, o Shift+Enter é a quebra de linha da caixa.
    const enter = tecla('Enter', { shiftKey: true })
    t.aoTeclar(enter)
    assert.equal(enter.prevenido, false)
    t.texto.value = ''
    await tique()
    t.texto.value = '/'
    await tique()
    assert.equal(t.atalhoAberto.value, true)
  }
  // Sem nada com a busca: a lista diz, e o Enter não manda o "/xyzzy".
  {
    const { t, enviados } = montarCaixa()
    t.texto.value = '/xyzzy'
    assert.equal(t.atalhoAberto.value, true)
    assert.deepEqual(t.listaAtalho.value, [])
    const enter = tecla('Enter')
    t.aoTeclar(enter)
    assert.equal(enter.prevenido, true)
    assert.deepEqual(enviados, [])
    const quebra = tecla('Enter', { shiftKey: true })
    t.aoTeclar(quebra)
    assert.equal(quebra.prevenido, false, 'Shift+Enter quebra a linha')
  }
  // Enter envia; Shift+Enter quebra a linha; Ctrl+Enter também envia.
  {
    const { t, enviados } = montarCaixa()
    t.texto.value = 'Bom dia!'
    const quebra = tecla('Enter', { shiftKey: true })
    t.aoTeclar(quebra)
    assert.equal(quebra.prevenido, false)
    assert.deepEqual(enviados, [])
    const enter = tecla('Enter')
    t.aoTeclar(enter)
    assert.equal(enter.prevenido, true)
    t.aoTeclar(tecla('Enter', { ctrlKey: true }))
    // Escrevendo com acento composto (IME), o Enter é da composição.
    t.aoTeclar(tecla('Enter', { isComposing: true }))
    assert.deepEqual(enviados, [true, true])
    assert.equal(t.dicaDoEnvio.value, 'Enter envia · Shift+Enter quebra a linha')
  }
  // No e-mail o Enter é parágrafo; envia o Ctrl+Enter.
  {
    const { t, enviados } = montarCaixa({ email: true })
    t.texto.value = 'Prezada,'
    const enter = tecla('Enter')
    t.aoTeclar(enter)
    assert.equal(enter.prevenido, false)
    assert.deepEqual(enviados, [])
    t.aoTeclar(tecla('Enter', { metaKey: true }))
    assert.deepEqual(enviados, [true])
    assert.equal(t.dicaDoEnvio.value, 'Ctrl+Enter envia')
  }
  // "/" no meio do texto, ou em conversa sem envio: nada abre.
  {
    const { t } = montarCaixa()
    t.texto.value = 'veja /rastreio'
    assert.equal(t.atalhoAberto.value, false)
    const s = montarCaixa({ podeDigitar: false })
    s.t.texto.value = '/'
    assert.equal(s.t.atalhoAberto.value, false)
  }
  // O menu: a mesma busca, ↓ e Enter inserem.
  {
    const { t } = montarCaixa()
    t.menuModelos.value = true
    t.buscaModelo.value = 'envi'
    await tique()
    assert.deepEqual(t.modelosDaConversa.value.map((m) => m.titulo), ['Rastreio enviado', 'Prazo de envio'])
    t.teclaDoMenu(tecla('ArrowDown'))
    assert.equal(t.menuIdx.value, 1)
    t.teclaDoMenu(tecla('Enter'))
    assert.equal(t.texto.value, 'Enviamos em até 2 dias úteis.')
    assert.equal(t.menuModelos.value, false)
    assert.equal(t.buscaModelo.value, '')
  }
}

// ─── o template da conversa ─────────────────────────────────────────────────
{
  const tpl = conversaSfc.descriptor.template.content
  const i = tpl.indexOf('data-atalho-prontas')
  const caixa = tpl.indexOf('ref="caixa"')
  assert.ok(i > 0 && caixa > i, 'a lista do atalho fica logo antes da caixa de resposta')
  const trecho = tpl.slice(tpl.lastIndexOf('<div class="relative">', i), caixa)
  assert.doesNotMatch(trecho, /PopoverPortal|Teleport/, 'sem portal: aparece na tela cheia')
  assert.match(trecho, /@mousedown\.prevent="usarDoAtalho\(m\)"/, 'o clique não tira o foco da caixa')
  assert.match(tpl, /@keydown="aoTeclar"/)
  assert.match(tpl, /`Escreva a resposta… \(\/ respostas prontas · \$\{dicaDoEnvio\}\)`/)
  assert.match(tpl, /@keydown="teclaDoMenu"/)
  assert.match(tpl, /v-if="m\.equipe_nome"/, 'a equipe aparece no menu')
  // A página passa a mesma lista para a conversa na tela de sempre e na cheia.
  const pagina = fs.readFileSync(path.resolve(__dirname, '../pages/atendimento.vue'), 'utf8')
  assert.ok((pagina.match(/:modelos="modelos"/g) || []).length >= 3)
}

// ─── a aba Respostas prontas ────────────────────────────────────────────────
function requerer(nome) {
  if (nome === 'lucide-vue-next') return new Proxy({}, { get: () => ({ render: () => Vue.h('i') }) })
  if (nome === '~/components/AtendimentoPlataforma.vue') return P
  return require(nome)
}
const EQUIPES = [{ numero: 101, nome: 'Londres' }, { numero: 102, nome: 'Israel' }, { numero: 103, nome: 'Marrocos' }]
function globais({ lista, equipes, chamadas = [] }) {
  return {
    computed: Vue.computed,
    ref: Vue.ref,
    reactive: Vue.reactive,
    watch: Vue.watch,
    onMounted: () => {},
    nextTick: Vue.nextTick,
    useApi: () => ({
      api: async (url, opts) => {
        chamadas.push([url, opts])
        if (url === '/api/atendimento/modelos/equipes') return equipes
        if (url === '/api/atendimento/modelos' && !opts) return lista
        if (opts?.method === 'POST') return { id: 'novo', ...opts.body, equipe_nome: 'Londres', pode_editar: true }
        return {}
      },
    }),
    useToasts: () => ({ success() {}, error() {} }),
  }
}
const NOMES = ['computed', 'ref', 'reactive', 'watch', 'onMounted', 'nextTick', 'useApi', 'useToasts']
function montarAba(props, g) {
  const script = compileScript(modelosSfc.descriptor, { id: 'aba', inlineTemplate: false })
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', ...NOMES, transpile(script.content))(requerer, mod, mod.exports, ...NOMES.map((n) => g[n]))
  return Vue.proxyRefs(mod.exports.default.setup(Vue.reactive({ ...props }), { emit: () => {}, expose: () => {}, attrs: {}, slots: {} }))
}
async function aba() {
  const ANTIGA = M({ titulo: 'Obrigado', texto: 'Obrigado!', pode_editar: false })
  const DA_LONDRES = M({ titulo: 'Rastreio L', texto: 'Segue.', equipe: 101, equipe_nome: 'Londres', pode_editar: true })
  const DA_ISRAEL = M({ titulo: 'Rastreio I', texto: 'Ok.', equipe: 102, equipe_nome: 'Israel', pode_editar: true })

  // Quem é de equipe (londres): cria a da equipe dele; só mexe nas da equipe.
  {
    const chamadas = []
    const g = globais({ lista: [ANTIGA, DA_LONDRES], equipes: { equipes: EQUIPES, minhas: [101], todas: false }, chamadas })
    const a = montarAba({ canEdit: false, canDelete: false }, g)
    await a.carregar()
    assert.equal(a.podeCriar, true)
    assert.deepEqual(a.equipesDoFiltro.map((e) => e.nome), ['Londres'])
    assert.equal(a.podeMexer(ANTIGA), false)
    assert.equal(a.podeMexer(DA_LONDRES), true)
    assert.equal(a.podeApagar(DA_LONDRES), true)
    assert.equal(a.rotuloEquipe(ANTIGA), 'para todos')
    assert.equal(a.rotuloEquipe(DA_LONDRES), 'Londres')
    a.abrirNova()
    assert.equal(a.form.equipe, '101')
    assert.equal(a.escolheEquipe, false)
    assert.deepEqual(a.opcoesEquipe.map((e) => e.numero), [101])
    Object.assign(a.form, { titulo: 'Troca', texto: 'Pode trocar.' })
    await a.salvar()
    const post = chamadas.find((c) => c[1]?.method === 'POST')
    assert.equal(post[1].body.equipe, 101)
    // O filtro "só as para todos".
    a.filtroEquipe = 'todos'
    assert.deepEqual(a.visiveis.map((m) => m.titulo), ['Obrigado'])
    a.filtroEquipe = '101'
    assert.deepEqual(a.visiveis.map((m) => m.titulo).sort(), ['Rastreio L', 'Troca'])
  }

  // O admin: escolhe a equipe ou "para todos" (o padrão), filtra por equipe e
  // mexe em todas; apagar segue o `delete`.
  {
    const chamadas = []
    const lista = [ANTIGA, DA_LONDRES, DA_ISRAEL].map((m) => ({ ...m, pode_editar: true }))
    const g = globais({ lista, equipes: { equipes: EQUIPES, minhas: [], todas: true }, chamadas })
    const a = montarAba({ canEdit: true, canDelete: true }, g)
    await a.carregar()
    assert.equal(a.podeCriar, true)
    assert.deepEqual(a.equipesDoFiltro.map((e) => e.nome), ['Londres', 'Israel', 'Marrocos'])
    a.filtroEquipe = '102'
    assert.deepEqual(a.visiveis.map((m) => m.titulo), ['Rastreio I'])
    a.abrirNova()
    assert.equal(a.form.equipe, '102', 'a nova nasce com a equipe do filtro')
    a.fechar()
    a.filtroEquipe = ''
    a.abrirNova()
    assert.equal(a.form.equipe, '', 'sem filtro: para todos')
    assert.equal(a.escolheEquipe, true)
    a.abrirEdicao(lista[1])
    assert.equal(a.form.equipe, '101')
    a.form.equipe = ''
    await a.salvar()
    const patch = chamadas.find((c) => c[1]?.method === 'PATCH')
    assert.equal(patch[1].body.equipe, null, '"para todos" vai como null')
    assert.equal(a.podeApagar(lista[0]), true)
    const semDelete = montarAba({ canEdit: true, canDelete: false }, globais({ lista, equipes: { equipes: EQUIPES, minhas: [], todas: true } }))
    assert.equal(semDelete.podeApagar(lista[0]), false)
  }

  // Quem não é de equipe: só lê (sem "nova resposta" nem botões).
  {
    const g = globais({ lista: [ANTIGA], equipes: { equipes: EQUIPES, minhas: [], todas: false } })
    const a = montarAba({ canEdit: false, canDelete: false }, g)
    await a.carregar()
    assert.equal(a.podeCriar, false)
    assert.equal(a.podeMexer(ANTIGA), false)
    assert.deepEqual(a.equipesDoFiltro, [])
  }

  const tpl = modelosSfc.descriptor.template.content
  assert.match(tpl, /<Button v-if="podeCriar && !formAberto"/)
  assert.match(tpl, /<div v-if="podeMexer\(m\)" class="flex shrink-0/)
  assert.match(tpl, /<Button v-if="podeApagar\(m\)"/)
  assert.match(tpl, /<option v-if="escolheEquipe" value="">para todos<\/option>/)
  assert.match(tpl, /data-equipe-pronta/)
  assert.match(tpl, /<select v-if="equipesDoFiltro\.length" v-model="filtroEquipe"[^>]*aria-label="filtrar por equipe"/)
}

// ─── o contrato com a API ───────────────────────────────────────────────────
{
  const schemas = api('schemas/atendimento.py')
  const out = /class ModeloOut\(BaseModel\):[\s\S]*?\n\n\n/.exec(schemas)[0]
  for (const campo of ['equipe: int | None', 'equipe_nome: str | None', 'pode_editar: bool']) assert.ok(out.includes(campo), campo)
  assert.match(schemas, /class ModelosEquipesOut\(BaseModel\):[\s\S]*?equipes: list\[EquipeModelo\][\s\S]*?minhas: list\[int\][\s\S]*?todas: bool/)
  const rota = api('routers/atendimento.py')
  assert.match(rota, /@router\.get\("\/modelos\/equipes", response_model=ModelosEquipesOut\)/)
  assert.match(rota, /ROTAS_DA_EQUIPE = frozenset\(/)
}

caixaDeResposta()
  .then(aba)
  .then(() => console.log('atendimento-respostas-prontas: ok'))
  .catch((e) => {
    console.error(e)
    process.exit(1)
  })
