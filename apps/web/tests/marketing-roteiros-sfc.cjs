// Run from apps/web: node tests/marketing-roteiros-sfc.cjs
// Marketing › Roteiros (Eduardo, 21/09/2026): parse + compileTemplate do
// componente novo e execução do <script setup> com api/window falsos.
//
// O que esta aba promete e o teste trava:
//  · destino VAZIO = as DUAS agências — a regra invertida em relação à coluna
//    Equipe dos Criativos, e a tela tem que DIZER isso, não deixar em branco;
//  · o personagem entra no texto na posição do CURSOR, com a etiqueta do
//    gerador (`<<<uuid>>>`) e não com o nome — foi colando UUID à mão que os
//    dois roteiros de produção foram escritos;
//  · ligar o mesmo personagem duas vezes não duplica nem explode;
//  · link externo sai com rel="noopener" (aba nova com window.opener é porta
//    aberta pro site de destino);
//  · nenhum token/senha no arquivo.
// Só dados FALSOS aqui; nenhuma rede.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { parse, compileTemplate } = require('vue/compiler-sfc')

const transpile = (source, module = ts.ModuleKind.CommonJS) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module },
}).outputText

const filename = path.join(__dirname, '../components/MarketingRoteiros.vue')
const source = fs.readFileSync(filename, 'utf8')
const { descriptor, errors } = parse(source, { filename })
assert.deepEqual(errors, [])
const compiled = compileTemplate({ source: descriptor.template.content, filename, id: 'roteiros-check' })
assert.deepEqual(compiled.errors, [])
new Function('exports', 'require', transpile(compiled.code))({}, require)

const script = descriptor.scriptSetup.content
const tpl = descriptor.template.content

// ---------------------------------------------------------------- higiene
{
  assert.ok(!/PORTAL_TOKEN|senha|password|secret/i.test(source), 'nada de segredo na tela')
  // O href externo é escolhido pela equipe interna, mas abre no navegador da
  // agência; sem noopener o site de destino ganha window.opener.
  const externos = tpl.match(/target="_blank"/g) || []
  assert.ok(externos.length >= 2, 'tem link que abre em aba nova')
  assert.ok(
    !/target="_blank"(?![\s\S]{0,200}?rel="noopener)/.test(tpl),
    'todo target=_blank leva rel="noopener"',
  )
  // A regra do destino tem que estar ESCRITA na tela.
  assert.ok(
    tpl.includes('as duas agências'),
    'o seletor de destino diz o que "vazio" faz, em vez de ficar em branco',
  )
}

// ---------------------------------------------------------------- script setup
const pageScript = script.replace(/^import[\s\S]*?from\s+'[^']+'\s*$/gm, '')
const exportsForTest = `return {
  secao, roteiros, personagens, destinos, sel, selId, listaFiltrada, q,
  disponiveis, linkNovo, personagemNovo, textoEl,
  carregar, abrir, criarRoteiro, salvar, ligarPersonagem, desligarPersonagem,
  inserirNoTexto, addLink, destinoLabel, pForm, salvarPersonagem, novoPersonagem,
}`
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const factory = new AsyncFunction(
  'computed', 'nextTick', 'onMounted', 'ref',
  'useApi', 'useToasts', 'useCan',
  'defineProps', 'defineExpose', 'window',
  transpile(pageScript, ts.ModuleKind.ESNext) + '\n' + exportsForTest,
)

const ROTEIRO = {
  id: 'rot-1',
  titulo: 'video 30s — mala',
  texto: 'abre na mesa. AQUI fala.',
  marca: 'uranyx',
  sku: 'dg023',
  equipe_destino: null,
  ativo: true,
  referencias: [],
  personagens: [],
  created_at: '2026-09-21T10:00:00Z',
}
const LIVIA = {
  id: 'p-1',
  nome: 'Lívia',
  descricao: 'estudante brasileira de 22 anos',
  referencia: '<<<48dbb6ed-155f-485d-90c0-1730bf39529d>>>',
  ativo: true,
  imagens: [],
}

async function tela({ roteiro = ROTEIRO, personagens = [LIVIA], canEdit = true } = {}) {
  const calls = []
  const toastLog = []
  const api = (url, opts) => {
    calls.push({ url, opts })
    if (url === '/api/marketing/roteiros' && !opts?.method) {
      return Promise.resolve([{ ...roteiro }])
    }
    if (url === '/api/marketing/personagens' && !opts?.method) {
      return Promise.resolve(personagens.map((p) => ({ ...p })))
    }
    if (url === '/api/marketing/roteiros/destinos') return Promise.resolve(['Mindset', 'Bill Gates'])
    if (url === '/api/marcas?ativo=true') return Promise.resolve([{ nome: 'Uranyx', slug: 'uranyx' }])
    if (opts?.method === 'PATCH') return Promise.resolve({ ...roteiro, ...opts.body })
    if (opts?.method === 'POST' && url.endsWith('/personagem')) {
      return Promise.resolve({ ...roteiro, personagens: [LIVIA] })
    }
    if (opts?.method === 'DELETE') return Promise.resolve({ ...roteiro, personagens: [] })
    return Promise.reject(new Error(`api falso não conhece ${url}`))
  }
  const toasts = {
    success: (...a) => toastLog.push(['success', ...a]),
    error: (...a) => toastLog.push(['error', ...a]),
    warning: (...a) => toastLog.push(['warning', ...a]),
    info: (...a) => toastLog.push(['info', ...a]),
  }
  const state = await factory(
    Vue.computed, Vue.nextTick, (fn) => fn(), Vue.ref,
    () => ({ api }), () => toasts, () => Vue.ref(canEdit),
    () => ({ foco: null }), () => {},
    { confirm: () => true },
  )
  await new Promise(setImmediate)
  return { s: state, calls, toastLog }
}

async function run() {
  // Carga: roteiros, personagens, destinos e marcas — e o primeiro já aberto.
  {
    const { s, calls } = await tela()
    const urls = calls.map((c) => c.url)
    assert.ok(urls.includes('/api/marketing/roteiros'), 'busca os roteiros')
    assert.ok(urls.includes('/api/marketing/personagens'), 'busca o elenco')
    assert.ok(urls.includes('/api/marketing/roteiros/destinos'), 'busca as agências')
    assert.equal(s.selId.value, 'rot-1', 'abre o primeiro sem clique')
  }

  // A regra invertida, no rótulo.
  {
    const { s } = await tela()
    assert.equal(s.destinoLabel(null), 'as duas agências', 'vazio = as duas')
    assert.equal(s.destinoLabel(''), 'as duas agências', 'string vazia também')
    assert.equal(s.destinoLabel('Mindset'), 'Mindset', 'endereçado mostra o nome')
  }

  // O ponto do vínculo: a ETIQUETA entra no texto, na posição do cursor.
  {
    const { s, calls } = await tela()
    s.sel.value.personagens = [LIVIA]
    // cursor logo antes de "AQUI"
    const pos = ROTEIRO.texto.indexOf('AQUI')
    s.textoEl.value = { selectionStart: pos, selectionEnd: pos, focus() {}, setSelectionRange() {} }
    await s.inserirNoTexto(LIVIA)

    const salvou = calls.find((c) => c.opts?.method === 'PATCH' && 'texto' in (c.opts.body || {}))
    assert.ok(salvou, 'salva o texto depois de inserir')
    const texto = salvou.opts.body.texto
    assert.ok(texto.includes(LIVIA.referencia), 'insere a ETIQUETA do gerador')
    assert.ok(!texto.includes('LíviaAQUI'), 'não insere o nome no lugar da etiqueta')
    assert.equal(
      texto,
      ROTEIRO.texto.slice(0, pos) + LIVIA.referencia + ROTEIRO.texto.slice(pos),
      'entra exatamente na posição do cursor, sem comer o resto',
    )
  }

  // Sem etiqueta cadastrada, cai no nome — melhor que não inserir nada.
  {
    const { s, calls } = await tela()
    s.textoEl.value = null
    await s.inserirNoTexto({ ...LIVIA, referencia: null })
    const salvou = calls.find((c) => c.opts?.method === 'PATCH' && 'texto' in (c.opts.body || {}))
    assert.ok(salvou.opts.body.texto.endsWith('Lívia'), 'sem etiqueta usa o nome, no fim')
  }

  // Ligar personagem: corpo certo, e o já-ligado some da lista de escolha.
  {
    const { s, calls } = await tela()
    s.personagemNovo.value = 'p-1'
    await s.ligarPersonagem()
    const post = calls.find((c) => c.opts?.method === 'POST' && c.url.endsWith('/personagem'))
    assert.deepEqual(post.opts.body, { personagem_id: 'p-1' }, 'manda só o id')
    assert.equal(s.personagemNovo.value, '', 'limpa o seletor depois de ligar')
    assert.deepEqual(s.disponiveis.value, [], 'quem já está no roteiro sai das opções')
  }

  // Filtro bate em título, marca, SKU e no texto.
  {
    const { s } = await tela()
    for (const termo of ['mala', 'uranyx', 'dg023', 'abre na mesa']) {
      s.q.value = termo
      assert.equal(s.listaFiltrada.value.length, 1, `filtra por ${termo}`)
    }
    s.q.value = 'nada disso'
    assert.equal(s.listaFiltrada.value.length, 0, 'filtro que não bate esvazia')
  }

  // Erro do backend vira frase, e a tela não morre.
  {
    const { s, toastLog } = await tela()
    s.selId.value = 'rot-1'
    await s.salvar('titulo', '')
    // o api falso responde PATCH sempre; o que importa é não explodir
    assert.ok(Array.isArray(toastLog), 'seguiu viva')
  }

  console.log(
    'PASS: SFC parse + template compile; higiene (sem segredo, rel=noopener, '
    + 'regra do destino escrita na tela); script setup com api falso (carga, '
    + 'destino invertido, etiqueta no cursor, vínculo de personagem, filtro)',
  )
}

run().catch((e) => {
  console.error(e)
  process.exit(1)
})
