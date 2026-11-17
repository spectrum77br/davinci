// node tests/mudancas-sfc.cjs — Sistema › Mudanças (17/11/2026): o chefe pede
// mudanças no DaVinci pelo Claude; o robô faz e testa; nesta tela SÓ os
// aprovadores (`mudancas: true` no /me) veem e aprovam/recusam. Trava:
//  - a página compila; para quem não é aprovador ela responde 404 (como o Histórico);
//  - o menu tem o item /mudancas com mudancasOnly e o filtro; o tipo AuthUser tem `mudancas`;
//  - lista: filtro padrão "Esperando aprovação" (status=pronta, limite=100), "Abertas", "Todas";
//    recarrega a cada 30 s e para ao sair da tela;
//  - gaveta: pedido, resumo, arquivos, testes recolhidos, diff colorido por linha (cabeçalho
//    só entre "diff --git" e "@@"), recusa/erro/no ar em destaque;
//  - botões só na "pronta" com pode_aprovar; aprovar manda o diff_sha mostrado; 409 diff_mudou
//    e nao_esta_pronta viram frase de gente e recarregam; recusar exige motivo (3+ letras);
//  - erro de rede vira aviso (toast/faixa), nunca tela branca; a atualização automática não
//    empilha toast.
// Só dados FALSOS; nenhuma rede.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { parse, compileTemplate, compileScript } = require('vue/compiler-sfc')
const { renderToString } = require('vue/server-renderer')

const ROOT = path.join(__dirname, '..')
const ler = (rel) => fs.readFileSync(path.join(ROOT, rel), 'utf8')
const transpile = (src) =>
  ts.transpileModule(src, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText

function sfc(rel) {
  const filename = path.join(ROOT, rel)
  const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
  assert.deepEqual(errors, [], `${rel}: parse`)
  const t = compileTemplate({ source: descriptor.template.content, filename, id: 'mudancas', compilerOptions: { expressionPlugins: ['typescript'] } })
  assert.deepEqual(t.errors, [], `${rel}: template`)
  return descriptor
}

// ---------------------------------------------------------------- estático
const pagina = sfc('pages/mudancas.vue')
const script = pagina.scriptSetup.content
assert.match(script, /auth\.user\?\.mudancas !== true/, 'middleware olha `mudancas`')
assert.match(script, /statusCode: 404/, 'quem não é aprovador recebe 404')
assert.match(script, /onUnmounted\(\(\) => \{\s*if \(relogio\) clearInterval\(relogio\)/, 'para o relógio ao sair')
assert.ok(!pagina.template.content.includes('v-html'), 'nada de v-html (diff/descrição vêm de fora)')
// Revisão de 17/11: a publicação adiada mostra o porquê; o robô parado (fila
// esperando e ele sumido há 10 min) vira faixa; 404 nunca mostra "Not Found".
assert.ok(pagina.template.content.includes('Publicação adiada:'), 'motivo da publicação adiada')
assert.ok(pagina.template.content.includes('O robô do Mac não aparece há mais de 10 minutos'), 'faixa do robô parado')
assert.match(pagina.scriptSetup.content, /robo_visto_em/, 'lê robo_visto_em da lista')
assert.match(pagina.scriptSetup.content, /if \(statusErro\(e\) === 404\) return 'Não encontrada\.'/, '404 em português')

const sidebar = sfc('components/AppSidebar.vue')
{
  const s = sidebar.scriptSetup.content
  assert.match(s, /mudancasOnly\?: boolean/, 'flag no tipo Item')
  assert.match(s, /\{ to: '\/mudancas', label: 'Mudanças', icon: GitPullRequest, mudancasOnly: true \}/, 'item no menu')
  assert.match(s, /if \(it\.mudancasOnly && auth\.user\?\.mudancas !== true\) return false/, 'filtro do menu')
  assert.match(s, /\bGitPullRequest\b[\s\S]*from 'lucide-vue-next'/, 'ícone importado')
  const sistema = s.slice(s.indexOf("label: 'Sistema'"))
  assert.ok(sistema.indexOf("'/mudancas'") > sistema.indexOf("'/historico'"), 'logo depois do Histórico')
  assert.ok(sistema.indexOf("'/mudancas'") < sistema.indexOf("label: 'Cadastros'"), 'na seção Sistema')
}
assert.match(ler('stores/auth.ts'), /\n\s+mudancas\?: boolean\n/, 'AuthUser.mudancas')

// ---------------------------------------------------------------- dados falsos
const DIFF = [
  'diff --git a/apps/web/pages/pedidos.vue b/apps/web/pages/pedidos.vue',
  'index 1111111..2222222 100644',
  '--- a/apps/web/pages/pedidos.vue',
  '+++ b/apps/web/pages/pedidos.vue',
  '@@ -10,3 +10,4 @@ const x = 1',
  ' contexto igual',
  '-<button>Velho</button>',
  '+<button>Exportar planilha</button>',
  'diff --git a/apps/api/sql/x.sql b/apps/api/sql/x.sql',
  'index 3333333..4444444 100644',
  '--- a/apps/api/sql/x.sql',
  '+++ b/apps/api/sql/x.sql',
  '@@ -1,2 +1,2 @@',
  '--- comentario sql removido',
  '+++ linha nova que começa com ++',
].join('\n') + '\n'

const base = {
  id: 'a1', codigo: 'M-12', titulo: 'Botão de exportar na tela de Pedidos', status: 'pronta',
  status_texto: 'Esperando aprovação', pedido_por_nome: 'Chefe Teste',
  criado_em: '2026-11-17T13:05:00Z', atualizado_em: '2026-11-17T13:20:00Z',
}
const LISTA = [
  base,
  { ...base, id: 'a2', codigo: 'M-11', titulo: 'Coluna nova', status: 'fazendo', status_texto: 'Robô fazendo' },
  { ...base, id: 'a3', codigo: 'M-10', titulo: 'Mexer no banco', status: 'precisa_equipe', status_texto: 'Precisa da equipe' },
]
const detalheDe = (extra = {}) => ({
  ...base,
  descricao: 'Quero um botão\npara exportar os pedidos.',
  onde: 'Tela de Pedidos',
  resumo: 'Coloquei o botão Exportar no topo da tela.',
  testes: 'SAIDA-DOS-TESTES: 42 passed',
  diff: DIFF,
  diff_sha: 'sha-1',
  arquivos: [{ caminho: 'apps/web/pages/pedidos.vue', mais: 2, menos: 1 }],
  branch: 'mudanca/m-12', commit: 'abcdef1234567890',
  aprovado_por_nome: null, aprovado_em: null, recusado_por_nome: null, recusado_em: null,
  motivo_recusa: null, publicado_em: null, erro: null, pode_aprovar: true,
  ...extra,
})

// ---------------------------------------------------------------- carregar a página
function carregarPagina({ inline, vueExtra, api, toasts, route, timers, auth }) {
  const { content } = compileScript(pagina, { id: 'mudancas', inlineTemplate: inline })
  const mod = { exports: {} }
  const vue = { ...Vue, ...vueExtra }
  const req = (m) => {
    if (m === 'vue') return vue
    if (m === 'lucide-vue-next') return new Proxy({}, { get: () => ({ render: () => null }) })
    if (m === '~/lib/date') {
      const dm = { exports: {} }
      new Function('module', 'exports', transpile(ler('lib/date.ts')))(dm, dm.exports)
      return dm.exports
    }
    return require(m)
  }
  const pageMeta = []
  const globais = {
    definePageMeta: (m) => pageMeta.push(m),
    useAuthStore: () => auth,
    abortNavigation: (err) => ({ abortado: err }),
    createError: (o) => ({ ...o }),
    useApi: () => ({ api }),
    useToasts: () => toasts,
    useRoute: () => route,
    setInterval: (fn, ms) => { timers.push({ fn, ms }); return 777 },
    clearInterval: (h) => { timers.limpos = (timers.limpos || []).concat(h) },
  }
  new Function('module', 'exports', 'require', ...Object.keys(globais), transpile(content))(
    mod, mod.exports, req, ...Object.values(globais),
  )
  return { comp: mod.exports.default, pageMeta }
}

function fakeToasts() {
  const log = []
  return {
    log,
    success: (t, l) => log.push(['success', t, l]),
    error: (t, l) => log.push(['error', t, l]),
    info: (t, l) => log.push(['info', t, l]),
    warning: (t, l) => log.push(['warning', t, l]),
  }
}
function erroHttp(status, detail) {
  const e = new Error(`HTTP ${status}`)
  e.status = status
  e.statusCode = status
  e.data = { detail }
  return e
}
// api falsa: responde por caminho; `respostas` pode trocar o que cada rota devolve.
function fakeApi(respostas) {
  const chamadas = []
  const api = async (url, opts = {}) => {
    chamadas.push({ url, opts })
    const metodo = opts.method || 'GET'
    const r = respostas[`${metodo} ${url}`] ?? (metodo === 'GET' && url.startsWith('/api/sistema-mudancas/') ? respostas['GET detalhe'] : undefined)
    if (r === undefined) throw erroHttp(404, 'Not Found')
    const v = typeof r === 'function' ? r(opts) : r
    if (v instanceof Error) throw v
    return JSON.parse(JSON.stringify(v))
  }
  return { api, chamadas }
}
const espera = () => new Promise((r) => setTimeout(r, 5))

const PageHeader = {
  props: ['title', 'description'],
  setup: (p, { slots }) => () => Vue.h('header', [Vue.h('h1', p.title), Vue.h('p', p.description), slots.actions?.()]),
}

async function renderizar({ lista = LISTA, detalhe = null, abrirId = null, listaErro = null } = {}) {
  const toasts = fakeToasts()
  const { api, chamadas } = fakeApi({
    'GET /api/sistema-mudancas': listaErro || { itens: lista },
    'GET detalhe': detalhe,
  })
  const timers = []
  const { comp } = carregarPagina({
    inline: true,
    // SSR não roda onMounted: aqui ele vira serverPrefetch, que o render espera.
    vueExtra: { onMounted: (fn) => Vue.onServerPrefetch(async () => { fn(); await espera() }), onUnmounted: () => {} },
    api, toasts, timers, auth: { user: { mudancas: true } },
    route: { query: abrirId ? { id: abrirId } : {} },
  })
  const app = Vue.createSSRApp(comp)
  app.component('PageHeader', PageHeader)
  const ctx = {}
  const html = await renderToString(app, ctx)
  return { html, gaveta: ctx.teleports?.body || '', chamadas, toasts, timers }
}
// texto da linha do diff → classes
function linhasDoDiff(h) {
  const m = new Map()
  for (const [, cls, txt] of h.matchAll(/<span class="block px-3 ?([^"]*)">([^<]*)<\/span>/g)) m.set(txt, cls)
  return m
}

;(async () => {
  // ------------------------------------------------------------ middleware 404
  {
    const auth = { user: { role: 'admin' } }
    const { comp, pageMeta } = carregarPagina({
      inline: false, vueExtra: { onMounted: () => {}, onUnmounted: () => {} },
      api: async () => ({ itens: [] }), toasts: fakeToasts(), route: { query: {} }, timers: [], auth,
    })
    comp.setup({}, { expose: () => {} })
    const mw = pageMeta[0].middleware[0]
    const r = mw({ fullPath: '/mudancas' })
    assert.equal(r?.abortado?.statusCode, 404, 'admin sem `mudancas` → 404')
    auth.user = null
    assert.equal(mw({ fullPath: '/mudancas' })?.abortado?.statusCode, 404, 'sem login → 404')
    auth.user = { mudancas: true }
    assert.equal(mw({ fullPath: '/mudancas' }), undefined, 'aprovador entra')
  }

  // ------------------------------------------------------------ lista + gaveta "pronta"
  {
    const { html, gaveta, chamadas, timers } = await renderizar({ detalhe: detalheDe(), abrirId: 'a1' })
    assert.ok(html.includes('Mudanças do sistema'), 'título')
    assert.ok(html.includes('só vai para o ar depois que você aprova'), 'descrição')
    for (const r of ['Esperando aprovação', 'Abertas', 'Todas']) assert.ok(html.includes(`${r}`), `filtro ${r}`)
    for (const c of ['Código', 'Pedido', 'Quem pediu', 'Quando', 'Situação']) assert.ok(html.includes(`>${c}</th>`), `coluna ${c}`)
    const lista = chamadas.find((c) => c.url === '/api/sistema-mudancas')
    assert.deepEqual(lista.opts.query, { limite: 100, status: 'pronta' }, 'padrão = esperando aprovação')
    assert.ok(html.includes('M-12') && html.includes('Botão de exportar na tela de Pedidos'), 'linha da lista')
    assert.ok(html.includes('<span class="pill-warning">Esperando aprovação</span>'), 'pill pronta')
    assert.ok(html.includes('<span class="pill-info">Robô fazendo</span>'), 'pill fazendo')
    assert.ok(html.includes('<span class="pill-danger">Precisa da equipe</span>'), 'pill precisa_equipe')
    assert.ok(html.includes('Chefe Teste'), 'quem pediu')
    assert.ok(timers.some((t) => t.ms === 30000), 'recarrega a cada 30 s')

    assert.ok(chamadas.some((c) => c.url === '/api/sistema-mudancas/a1'), '?id= abre a gaveta')
    assert.ok(gaveta.includes('max-w-4xl'), 'gaveta larga')
    assert.ok(gaveta.includes('Quero um botão\npara exportar os pedidos.'), 'descrição com quebra de linha')
    assert.ok(gaveta.includes('Tela de Pedidos'), 'onde')
    assert.ok(gaveta.includes('Coloquei o botão Exportar no topo da tela.'), 'resumo do robô')
    assert.ok(gaveta.includes('apps/web/pages/pedidos.vue') && gaveta.includes('+2') && gaveta.includes('−1'), 'arquivos')
    assert.match(gaveta, /> Testes <button[^>]*>ver<\/button>/, 'bloco Testes com botão "ver"')
    assert.ok(!gaveta.includes('SAIDA-DOS-TESTES'), 'testes recolhidos por padrão')
    assert.ok(gaveta.includes('Aprovar e publicar') && gaveta.includes('Recusar'), 'botões na pronta')
    assert.ok(gaveta.includes('&lt;button&gt;Exportar planilha&lt;/button&gt;'), 'diff escapado')

    const L = linhasDoDiff(gaveta)
    assert.match(L.get('diff --git a/apps/web/pages/pedidos.vue b/apps/web/pages/pedidos.vue'), /font-semibold/)
    assert.match(L.get('index 1111111..2222222 100644'), /font-semibold text-muted-foreground/)
    assert.match(L.get('--- a/apps/web/pages/pedidos.vue'), /font-semibold text-muted-foreground/)
    assert.match(L.get('+++ b/apps/web/pages/pedidos.vue'), /font-semibold text-muted-foreground/)
    assert.match(L.get('@@ -10,3 +10,4 @@ const x = 1'), /text-sky-700/)
    assert.equal(L.get(' contexto igual'), '')
    assert.match(L.get('-&lt;button&gt;Velho&lt;/button&gt;'), /text-red-800/)
    assert.match(L.get('+&lt;button&gt;Exportar planilha&lt;/button&gt;'), /text-emerald-800/)
    assert.match(L.get('--- comentario sql removido'), /text-red-800/, 'linha removida "---" depois do @@ é vermelha')
    assert.match(L.get('+++ linha nova que começa com ++'), /text-emerald-800/, 'linha nova "+++" depois do @@ é verde')
  }

  // ------------------------------------------------------------ outras situações
  {
    const vazio = await renderizar({ lista: [] })
    assert.ok(vazio.html.includes('Nenhuma mudança esperando aprovação'), 'lista vazia amigável')

    const semPoder = await renderizar({ detalhe: detalheDe({ pode_aprovar: false }), abrirId: 'a1' })
    assert.ok(!semPoder.gaveta.includes('Aprovar e publicar'), 'pode_aprovar=false: sem botões')

    const fazendo = await renderizar({ detalhe: detalheDe({ status: 'fazendo', status_texto: 'Robô fazendo', diff: null }), abrirId: 'a1' })
    assert.ok(!fazendo.gaveta.includes('Aprovar e publicar'), 'fazendo: sem botões')
    assert.ok(fazendo.gaveta.includes('ainda está trabalhando'), 'fazendo: aviso')

    const rec = await renderizar({
      detalhe: detalheDe({
        status: 'recusada', status_texto: 'Recusada', recusado_por_nome: 'Eduardo Teste',
        recusado_em: '2026-11-17T14:00:00Z', motivo_recusa: 'Não era isso.\nO botão vai em Vendas.',
      }),
      abrirId: 'a1',
    })
    assert.ok(rec.gaveta.includes('Eduardo Teste') && rec.gaveta.includes('Não era isso.\nO botão vai em Vendas.'), 'recusa: quem e motivo')
    assert.ok(!rec.gaveta.includes('Aprovar e publicar'), 'recusada: sem botões')

    const falhou = await renderizar({ detalhe: detalheDe({ status: 'falhou', status_texto: 'Falhou', erro: 'ERRO-XYZ: teste quebrou' }), abrirId: 'a1' })
    assert.match(falhou.gaveta, /border-red-500\/40[^>]*>[\s\S]*ERRO-XYZ: teste quebrou/, 'falhou: erro em destaque')
    const equipe = await renderizar({ detalhe: detalheDe({ status: 'precisa_equipe', status_texto: 'Precisa da equipe', erro: null, resumo: 'RESUMO-EQUIPE' }), abrirId: 'a1' })
    assert.match(equipe.gaveta, /precisa da equipe[\s\S]*RESUMO-EQUIPE/, 'precisa_equipe: resumo em destaque')
    assert.equal(equipe.gaveta.split('RESUMO-EQUIPE').length, 2, 'resumo não repete')

    const noAr = await renderizar({ detalhe: detalheDe({ status: 'publicada', status_texto: 'No ar', publicado_em: '2026-11-17T15:00:00Z', aprovado_por_nome: 'Eduardo Teste' }), abrirId: 'a1' })
    assert.ok(noAr.gaveta.includes('No ar desde'), 'publicada: no ar desde')

    const caiu = await renderizar({ listaErro: new TypeError('fetch failed') })
    assert.ok(caiu.html.includes('Não deu para carregar a lista'), 'erro de rede: faixa, sem tela branca')
    assert.ok(caiu.toasts.log.some(([k]) => k === 'error'), 'erro de rede: toast')
  }

  // ------------------------------------------------------------ fluxos (setup direto)
  async function montar(respostas) {
    const toasts = fakeToasts()
    const { api, chamadas } = fakeApi(respostas)
    const timers = []
    const mon = []
    const desm = []
    const { comp } = carregarPagina({
      inline: false, vueExtra: { onMounted: (f) => mon.push(f), onUnmounted: (f) => desm.push(f) },
      api, toasts, timers, auth: { user: { mudancas: true } }, route: { query: {} },
    })
    const b = comp.setup({}, { expose: () => {} })
    mon.forEach((f) => f())
    await espera()
    return { b, chamadas, toasts, timers, desmontar: () => desm.forEach((f) => f()) }
  }

  // filtros
  {
    const { b, chamadas, timers, desmontar } = await montar({ 'GET /api/sistema-mudancas': { itens: LISTA } })
    b.trocarFiltro('abertas'); await espera()
    assert.deepEqual(chamadas.at(-1).opts.query, { limite: 100, status: 'abertas' })
    b.trocarFiltro('todas'); await espera()
    assert.deepEqual(chamadas.at(-1).opts.query, { limite: 100 }, 'Todas = sem status')
    desmontar()
    assert.deepEqual(timers.limpos, [777], 'relógio parado ao sair')
  }

  // aprovar: manda o diff_sha mostrado
  {
    const posts = []
    const { b, chamadas, toasts } = await montar({
      'GET /api/sistema-mudancas': { itens: LISTA },
      'GET detalhe': detalheDe(),
      'POST /api/sistema-mudancas/a1/aprovar': (o) => { posts.push(o.body); return detalheDe({ status: 'aprovada', status_texto: 'Aprovada — vai para o ar' }) },
    })
    b.abrir('a1'); await espera()
    assert.equal(b.podeDecidir.value, true)
    const p1 = b.aprovar()
    const p2 = b.aprovar() // clique duplo
    assert.equal(b.enviando.value, 'aprovar', 'botões travados durante a chamada')
    await Promise.all([p1, p2])
    assert.deepEqual(posts, [{ diff_sha: 'sha-1' }], 'um POST só, com o diff_sha da tela')
    assert.ok(toasts.log.some(([k, t]) => k === 'success' && t === 'M-12 aprovada — o robô vai publicar'))
    assert.equal(b.detalhe.value.status, 'aprovada', 'gaveta mostra a resposta')
    assert.equal(b.enviando.value, null)
    await espera()
    assert.ok(chamadas.filter((c) => c.url === '/api/sistema-mudancas').length >= 2, 'lista recarregada')
  }

  // aprovar: 409 diff_mudou → frase + recarrega o detalhe
  {
    const { b, chamadas, toasts } = await montar({
      'GET /api/sistema-mudancas': { itens: LISTA },
      'GET detalhe': detalheDe(),
      'POST /api/sistema-mudancas/a1/aprovar': () => erroHttp(409, { code: 'diff_mudou', detalhe: 'x' }),
    })
    b.abrir('a1'); await espera()
    b.confirmarAberto.value = true
    const antes = chamadas.filter((c) => c.url === '/api/sistema-mudancas/a1').length
    await b.aprovar(); await espera()
    assert.ok(toasts.log.some(([k, t]) => k === 'error' && t === 'A mudança foi refeita depois que você abriu — abra de novo para ver a versão nova'))
    assert.equal(chamadas.filter((c) => c.url === '/api/sistema-mudancas/a1').length, antes + 1, 'detalhe recarregado')
    assert.equal(b.confirmarAberto.value, false)
  }

  // aprovar: 409 nao_esta_pronta
  {
    const { b, toasts } = await montar({
      'GET /api/sistema-mudancas': { itens: LISTA },
      'GET detalhe': detalheDe(),
      'POST /api/sistema-mudancas/a1/aprovar': () => erroHttp(409, { code: 'nao_esta_pronta', detalhe: 'Já foi aprovada por outra pessoa.' }),
    })
    b.abrir('a1'); await espera()
    await b.aprovar()
    assert.ok(toasts.log.some(([k, t, l]) => k === 'error' && t.includes('não está mais esperando aprovação') && l === 'Já foi aprovada por outra pessoa.'))
  }

  // recusar
  {
    const posts = []
    let falha = null
    const { b, toasts } = await montar({
      'GET /api/sistema-mudancas': { itens: LISTA },
      'GET detalhe': detalheDe(),
      'POST /api/sistema-mudancas/a1/recusar': (o) => {
        if (falha) return falha
        posts.push(o.body)
        return detalheDe({ status: 'recusada', status_texto: 'Recusada', motivo_recusa: o.body.motivo })
      },
    })
    b.abrir('a1'); await espera()
    b.abrirRecusa()
    assert.equal(b.recusaAberta.value, true)
    b.motivo.value = '  a  '
    assert.equal(b.motivoOk.value, false, 'motivo curto não vale')
    await b.recusar()
    assert.deepEqual(posts, [], 'motivo curto não chama a API')

    b.motivo.value = 'abc'
    falha = erroHttp(422, [{ msg: 'String should have at least 3 characters' }])
    await b.recusar()
    assert.ok(toasts.log.some(([k, t]) => k === 'error' && t.startsWith('Escreva o motivo')), '422 vira frase de gente')
    assert.equal(b.recusaAberta.value, true, '422: janela continua aberta')

    falha = null
    b.motivo.value = '  Não era isso que eu queria.  '
    await b.recusar()
    assert.deepEqual(posts, [{ motivo: 'Não era isso que eu queria.' }])
    assert.equal(b.recusaAberta.value, false)
    assert.ok(toasts.log.some(([k, t]) => k === 'success' && t === 'M-12 recusada'))
    assert.equal(b.detalhe.value.status, 'recusada')
  }

  // atualização automática: erro de rede não empilha toast
  {
    let cair = false
    const { b, toasts, timers } = await montar({
      'GET /api/sistema-mudancas': () => (cair ? new TypeError('fetch failed') : { itens: LISTA }),
    })
    cair = true
    timers[0].fn(); await espera()
    assert.ok(b.erroLista.value, 'faixa de erro')
    assert.ok(!toasts.log.some(([k]) => k === 'error'), 'sem toast no automático')
    assert.equal(b.itens.value.length, 3, 'mantém a última lista')
  }

  console.log('mudancas-sfc: ok')
})().catch((e) => { console.error(e); process.exit(1) })
