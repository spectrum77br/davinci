// Run from apps/web: node tests/logistica-notas-fiscais.cjs
// Novos procedimentos 7 (17/11/2026): a tela Notas fiscais (grupo Pós-venda)
// sai do menu e vira a aba "Notas fiscais" da Logística, a última da barra.
// - /notas-fiscais continua existindo e redireciona para /logistica?tab=notas
//   com a mesma permissão de antes e a query (o link do Vigia de Nota Cheia);
// - permissão igual à de antes: a aba só aparece para quem tem notas_fiscais;
//   a Logística abre para logistica OU notas_fiscais, e quem só tem Notas
//   fiscais vê a Logística só com essa aba, sem carregar nada das outras;
// - a tela é a mesma (filtros, colunas, XML), agora com o botão PDF (DANFE do
//   XML guardado) e a etiqueta "Pedido cancelado — conferir NF".
// Executa os middlewares e monta o menu e o painel de verdade (SSR).
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')
const { renderToString } = require('vue/server-renderer')

const transpile = (src, module = ts.ModuleKind.CommonJS) =>
  ts.transpileModule(src, { compilerOptions: { target: ts.ScriptTarget.ES2022, module, esModuleInterop: true } }).outputText
const ler = (rel) => fs.readFileSync(path.resolve(__dirname, rel), 'utf8')
const ALIAS = {}
const requerer = (m) => (m in ALIAS ? ALIAS[m] : require(m))
function loadLib(rel) {
  const exp = {}
  new Function('exports', 'require', transpile(ler(rel)))(exp, requerer)
  return exp
}
function sfc(rel) {
  const filename = path.resolve(__dirname, rel)
  const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
  assert.deepEqual(errors, [], `${rel}: parse`)
  const tpl = compileTemplate({ source: descriptor.template.content, filename, id: `${path.basename(rel)}-check`, compilerOptions: { isTS: true } })
  assert.deepEqual(tpl.errors, [], `${rel}: template`)
  compileScript(descriptor, { id: path.basename(rel) })
  return descriptor
}
// definePageMeta(...) de uma página, executável (navigateTo e useAuthStore falsos).
function metaDe(descriptor, filename, { navigateTo, useAuthStore = () => ({ user: null }) }) {
  const setup = descriptor.scriptSetup.content
  const source = ts.createSourceFile(filename, setup, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
  const meta = source.statements.find((s) => ts.isExpressionStatement(s)
    && ts.isCallExpression(s.expression) && s.expression.expression.getText(source) === 'definePageMeta')
  assert.ok(meta, `${filename}: definePageMeta`)
  const compiled = transpile(`const META = ${meta.expression.arguments[0].getText(source)}`)
  return new Function('navigateTo', 'useAuthStore', `${compiled}\nreturn META`)(navigateTo, useAuthStore)
}

const v = { view: true }
const ADMIN = { status: 'active', role: 'admin' }
const SO_NOTAS = { status: 'active', role: 'user', permissions: { notas_fiscais: v } }
const SO_LOGISTICA = { status: 'active', role: 'user', permissions: { logistica: v } }
const AMBAS = { status: 'active', role: 'user', permissions: { logistica: v, notas_fiscais: v } }
const COMUM = { status: 'active', role: 'user', permissions: { devolucoes: v } }

// ═══════════════════════════════════════════════ /notas-fiscais só redireciona
{
  const d = sfc('../pages/notas-fiscais.vue')
  const navegacoes = []
  const META = metaDe(d, 'notas-fiscais.vue', { navigateTo: (to, opts) => { navegacoes.push({ to, opts }); return to } })
  assert.deepEqual(META.permission, { resource: 'notas_fiscais', action: 'view' }, 'a mesma permissão de antes')
  assert.equal(META.middleware[0], 'permission', 'a trava de permissão roda antes do redirecionamento')
  const ir = META.middleware[1]
  ir({ query: { de: '2026-11-16', ate: '2026-11-17', nf: 'sem_produto' }, hash: '' })
  assert.deepEqual(navegacoes.at(-1), {
    to: { path: '/logistica', query: { de: '2026-11-16', ate: '2026-11-17', nf: 'sem_produto', tab: 'notas' }, hash: '' },
    opts: { replace: true },
  }, 'o link do Vigia de Nota Cheia abre a aba já no dia e no filtro')
  ir({ query: {}, hash: '#x' })
  assert.deepEqual(navegacoes.at(-1).to, { path: '/logistica', query: { tab: 'notas' }, hash: '#x' })
  assert.doesNotMatch(d.template.content, /NotasFiscaisPainel|<table/, 'a tela não mora mais aqui')
}

// ═══════════════════════════════════════════════ a trava da Logística (middleware/permission de verdade)
const logistica = sfc('../pages/logistica.vue')
{
  const META = metaDe(logistica, 'logistica.vue', { navigateTo: () => {} })
  assert.deepEqual(META.permissionAny, [
    { resource: 'logistica', action: 'view' },
    { resource: 'notas_fiscais', action: 'view' },
  ])
  assert.equal(META.permission, undefined, 'não é mais só logistica')
  assert.deepEqual(META.middleware, ['permission'])
  const exp = {}
  let usuario = null
  new Function('exports', 'require', 'defineNuxtRouteMiddleware', 'useAuthStore', 'navigateTo',
    transpile(ler('../middleware/permission.ts')))(exp, require, (f) => f, () => ({ user: usuario }), (to) => to)
  const entra = (u) => { usuario = u; return exp.default({ meta: META }) }
  for (const u of [ADMIN, SO_NOTAS, SO_LOGISTICA, AMBAS]) assert.equal(entra(u), undefined, JSON.stringify(u.permissions ?? u.role))
  assert.equal(entra(COMUM), '/403', 'sem nenhuma das duas, 403 como antes')
}

// ═══════════════════════════════════════════════ a aba dentro da Logística
{
  const setup = logistica.scriptSetup.content
  const tpl = logistica.template.content
  assert.match(setup, /const podeLogistica = useCan\('logistica', 'view'\)\s*const podeNotas = useCan\('notas_fiscais', 'view'\)/)
  // Quem só tem Notas fiscais abre direto nelas; `?tab=notas` abre nelas sem carregar a lista do ML.
  assert.match(setup, /const tab = ref<PlataformaTab \| 'status' \| 'notas'>\(podeLogistica\.value \? 'ml' : 'notas'\)/)
  assert.match(setup, /if \(queryInicial\.tab === 'notas' && podeNotas\.value\) tab\.value = 'notas'/)
  // Nada da Logística carrega para quem só tem Notas fiscais (a API recusaria) nem na aba Notas.
  assert.match(setup, /async function refresh\(\) \{[\s\S]{0,200}?if \(tab\.value === 'notas' \|\| !podeLogistica\.value\) return/)
  assert.match(setup, /if \(podeLogistica\.value\) await Promise\.all\(\[refresh\(\), loadOpcoes\(\)\]\)/)
  assert.match(setup, /const abrirNoFlex = podeLogistica\.value && queryInicial\.tab === 'flex'/)
  assert.match(setup, /onMounted\(\(\) => \{\s*\/\/[^\n]*\n\s*if \(!podeLogistica\.value\) return/)
  assert.match(setup, /watch\(tab, \(t\) => \{\s*\/\/[^\n]*\n\s*if \(t === 'notas'\) return/)
  assert.match(setup, /function autoRefreshTick\(\) \{\s*if \(tab\.value === 'notas'\) return/)

  // A barra: as abas da Logística só para quem tem Logística; Notas fiscais por último, com a permissão dela.
  const barra = tpl.slice(tpl.indexOf('<div class="flex flex-wrap gap-0.5">'), tpl.indexOf('<!-- Flex: Pedidos × Anúncios -->'))
  const iLog = barra.indexOf('<template v-if="podeLogistica">')
  const iStatus = barra.indexOf("@click=\"tab = 'status'\"")
  const iNotas = barra.indexOf('data-aba-notas-fiscais')
  assert.ok(iLog >= 0 && iStatus > iLog && iNotas > iStatus, 'Notas fiscais é a última aba, depois de Status')
  assert.ok(barra.indexOf('</template>') < iNotas, 'a aba Notas fiscais não depende da permissão de Logística')
  const botao = barra.slice(barra.lastIndexOf('<button', iNotas), barra.indexOf('</button>', iNotas))
  assert.match(botao, /v-if="podeNotas"/)
  assert.match(botao, /@click="tab = 'notas'"/)
  assert.match(botao, />\s*Notas fiscais\s*$/)
  // Botões da Logística somem na aba Notas fiscais.
  assert.match(tpl, /<template v-if="tab !== 'status' && tab !== 'notas' && !flexAnuncios">\s*<button[\s\S]{0,400}?@click="recarregar"/)
  assert.match(tpl, /v-if="isAdmin && tab !== 'notas'"/)
  assert.match(tpl, /v-if="canEdit && tab !== 'status' && tab !== 'notas' && !flexAnuncios"/)
  // O conteúdo: a lista dos marketplaces não aparece; o painel fecha a cadeia v-if.
  assert.match(tpl, /<!-- ============ ABAS DE MARKETPLACE \(ML\/Shopee\/Amazon\/TikTok\/Flex\) ============ -->\s*<template v-if="tab !== 'status' && tab !== 'notas' && !flexAnuncios">/)
  assert.match(tpl, /<template v-else-if="tab === 'status'">[\s\S]*?<\/template>\s*(<!--[\s\S]*?-->\s*)*<NotasFiscaisPainel v-else-if="tab === 'notas'" embutido \/>/)
}

// ═══════════════════════════════════════════════ menu lateral (AppSidebar de verdade)
const nav = loadLib('../lib/navGroups.ts')
function montarMenu(user, rota = '/') {
  const { descriptor } = parse(ler('../components/AppSidebar.vue'))
  let src = descriptor.scriptSetup.content
  const icones = src.match(/import\s*\{([^}]*)\}\s*from\s*'lucide-vue-next'/)[1].split(',').map((s) => s.trim()).filter(Boolean)
  src = src.replace(/^import[\s\S]*?from\s+'[^']+'\s*$/gm, '').replace(/import\.meta\.client/g, 'false')
  const params = {
    computed: Vue.computed, ref: Vue.ref, watch: Vue.watch, nextTick: Vue.nextTick, onMounted: () => {}, onScopeDispose: () => {},
    ...nav, useState: (_k, ini) => Vue.ref(ini()),
    defineProps: () => ({}), withDefaults: (p, d) => ({ ...d, ...p, collapsed: false }), defineEmits: () => () => {},
    useAuthStore: () => ({ user, isAdmin: user.role === 'admin' }), useRoute: () => ({ path: rota }),
    useRuntimeConfig: () => ({ public: { enableMarketing: false } }), useApi: () => ({ api: async () => ({ count: 0 }) }),
  }
  for (const n of icones) params[n] = {}
  const nomes = Object.keys(params)
  return new Function(...nomes, transpile(src, ts.ModuleKind.ESNext) + '\nreturn { visibleSections, isActive }')(
    ...nomes.map((n) => params[n]))
}
{
  for (const u of [ADMIN, SO_NOTAS, SO_LOGISTICA, AMBAS, COMUM]) {
    const m = montarMenu(u)
    const itens = m.visibleSections.value.flatMap((s) => s.items)
    assert.ok(!itens.some((i) => i.to === '/notas-fiscais'), 'Notas fiscais não é mais item do menu')
    const logi = itens.find((i) => i.to === '/logistica')
    assert.ok(logi, 'a Logística continua no menu (para quem só tem Notas fiscais também)')
    assert.deepEqual(logi.match, ['/logistica', '/notas-fiscais'])
  }
  const posVenda = montarMenu(SO_NOTAS).visibleSections.value.find((s) => s.label === 'Pós-venda')
  assert.deepEqual(posVenda.items.map((i) => i.label).slice(0, 4), ['Devoluções', 'Reembolso', 'Logística', 'Chamados'])
  for (const rota of ['/logistica', '/notas-fiscais']) {
    const m = montarMenu(SO_NOTAS, rota)
    assert.deepEqual(m.visibleSections.value.flatMap((s) => s.items).filter((i) => m.isActive(i)).map((i) => i.label), ['Logística'], rota)
  }
  assert.doesNotMatch(ler('../components/AppSidebar.vue'), /\bFileText\b/, 'o ícone de Notas fiscais saiu do menu')
}

// ═══════════════════════════════════════════════ o painel (SSR de verdade)
const painel = sfc('../components/NotasFiscaisPainel.vue')
{
  const setup = painel.scriptSetup.content
  assert.doesNotMatch(setup, /definePageMeta/, 'é componente, não página')
  assert.match(setup, /const props = defineProps<\{ embutido\?: boolean \}>\(\)/)
  // Os mesmos filtros de antes.
  assert.match(setup, /const NF_FILTROS = \['all', 'sem_embalagem', 'sem_produto', 'sem_nf', 'completas'\]/)
  // XML e PDF pela mesma rota da nota, cada um com o seu sufixo.
  assert.match(setup, /`\/api\/notas-fiscais\/pos-vendas\/nota\/\$\{nf\.nota_id\}\/\$\{arquivo\}`/)
}

const iconeFalso = { render: () => null }
ALIAS['lucide-vue-next'] = new Proxy({}, { get: () => iconeFalso })
ALIAS['~/lib/date'] = loadLib('../lib/date.ts')

const nota = (id, numero, xml) => ({
  nota_id: id, emitente: `Emitente ${numero}`, cnpj: '11111111000111', numero, valor: 10, data_emissao: '2026-11-17 09:00:00', via: 'pedido', xml_guardado: xml,
})
const linha = (pedido, extra) => ({
  pedido_bling: pedido, pedido_marketplace: `MK${pedido}`, data_envio: '2026-11-17', envio_com_hora: false, loja: 'loja-teste', plataforma: 'ml',
  sku: 'b096.1', produto: 'Mala', valor: 100, nf_embalagem: null, nf_produto: null, etiqueta_emissao: null, etiqueta_impressao: null,
  pedido_cancelado: false, ...extra,
})
const ITENS = [
  // Cancelado no Bling com NF: a etiqueta aparece.
  linha('900001', { pedido_cancelado: true, nf_embalagem: nota('e1', '11', true), nf_produto: nota('p1', '21', false) }),
  // Não cancelado: sem etiqueta; NF só no espelho do Bling: XML sem PDF.
  linha('900002', { nf_produto: nota('p2', '22', false) }),
  // Cancelado sem nota nenhuma: nada para conferir.
  linha('900003', { pedido_cancelado: true }),
]

async function renderizarPainel(props) {
  const chamadas = []
  const { content } = compileScript(painel, { id: 'painel', inlineTemplate: true })
  const g = {
    computed: Vue.computed, ref: Vue.ref, watch: Vue.watch,
    useApi: () => ({ api: async (url) => { chamadas.push(url); return { items: ITENS, total: ITENS.length } } }),
    useRoute: () => ({ query: {} }),
  }
  const mod = {}
  const nomes = Object.keys(g)
  new Function('exports', 'require', ...nomes, transpile(content))(mod, requerer, ...nomes.map((n) => g[n]))
  const app = Vue.createSSRApp(mod.default, props)
  app.component('PageHeader', { props: ['title', 'description'], setup: (p) => () => Vue.h('h1', p.title) })
  app.component('Button', { setup: (_p, { slots }) => () => Vue.h('button', slots.default?.()) })
  return { html: await renderToString(app), chamadas }
}

;(async () => {
  const { html, chamadas } = await renderizarPainel({ embutido: true })
  assert.equal(chamadas.length, 1)
  assert.match(chamadas[0], /^\/api\/notas-fiscais\/pos-vendas\?date_from=\d{4}-\d{2}-\d{2}&date_to=\d{4}-\d{2}-\d{2}$/)
  // Embutido: sem o título da página (a barra da Logística já diz), com a descrição.
  assert.doesNotMatch(html, /<h1>Notas fiscais<\/h1>/)
  assert.match(html, /Pedidos enviados com as duas notas fiscais de cada envio/)
  // As mesmas colunas, com XML · PDF no lugar de XML.
  for (const col of ['Data envio', 'Pedido Bling', 'Pedido Marketplace', 'Loja', 'SKU', 'Produto', 'Nome', 'CNPJ', 'Emissão', 'Impressão', 'Imprimir']) {
    assert.ok(html.includes(`>${col}</th>`), `coluna ${col}`)
  }
  assert.equal((html.match(/>XML · PDF<\/th>/g) || []).length, 2, 'XML · PDF nos dois grupos (embalagem e produto)')
  // XML em toda nota; PDF só onde o XML está guardado.
  assert.equal((html.match(/\s*XML\s*<\/button>/g) || []).length, 3, 'um XML por nota (3 notas)')
  assert.equal((html.match(/data-botao-pdf/g) || []).length, 1, 'PDF só na nota com XML guardado')
  // A etiqueta só no pedido cancelado que tem nota.
  assert.equal((html.match(/data-pedido-cancelado/g) || []).length, 1)
  const i900001 = html.indexOf('900001')
  const iEtiqueta = html.indexOf('Pedido cancelado — conferir NF')
  assert.ok(iEtiqueta > i900001 && iEtiqueta < html.indexOf('900002'), 'a etiqueta fica na linha do 900001')

  // Fora da Logística (sem `embutido`), o cabeçalho de antes.
  const solto = await renderizarPainel({})
  assert.match(solto.html, /<h1>Notas fiscais<\/h1>/)

  // ═══════════════════════════════════════════ Permissões, Histórico e Ouvidoria
  const can = new Function('exports', 'require', 'useAuthStore', transpile(ler('../composables/useCan.ts')))
  const exp = {}
  can(exp, require, () => ({ user: null }))
  const posVenda = exp.RESOURCE_GROUPS.find((g) => g.label === 'Pós-venda').resources
  assert.equal(posVenda.indexOf('notas_fiscais'), posVenda.indexOf('logistica') + 1, 'Notas fiscais logo depois de Logística, como no menu')
  assert.match(exp.RESOURCE_LABELS.notas_fiscais, /aba Notas fiscais da Logística/)

  const nomes = fs.readFileSync(path.resolve(__dirname, '../../api/app/historico/nomes.py'), 'utf8')
  assert.ok(nomes.includes('    "/notas-fiscais": "Logística › Notas fiscais",'))
  assert.ok(nomes.includes('    "/api/notas-fiscais": "Logística › Notas fiscais",'))

  const robos = parse(ler('../pages/ouvidoria/robos.vue')).descriptor.scriptSetup.content
  const fonte = robos.slice(robos.indexOf('function linkInfo('), robos.indexOf('// Extras (`dados`)'))
  const linkInfo = new Function(`${transpile(fonte)}\nreturn linkInfo`)()
  assert.deepEqual(linkInfo('/logistica?tab=notas&de=2026-10-13&ate=2026-10-14&nf=sem_produto'), { label: 'Notas fiscais', interno: true })
  assert.deepEqual(linkInfo('/notas-fiscais?de=2026-10-13'), { label: 'Notas fiscais', interno: true }, 'ocorrência antiga')
  assert.deepEqual(linkInfo('/logistica?tab=ml&q=123'), { label: 'Logística', interno: true })

  console.log('logistica-notas-fiscais: ok')
})().catch((e) => {
  console.error(e)
  process.exit(1)
})
