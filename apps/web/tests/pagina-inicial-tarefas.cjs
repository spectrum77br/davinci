// Run from apps/web: node tests/pagina-inicial-tarefas.cjs
// M-118 (27/11/2026): o Dashboard e o "Primeiros passos" saíram.
// - / e /onboarding só redirecionam para /tarefas, sem permissão própria
//   (o "voltar ao início" do 403 manda para /, e Tarefas abre para todos).
// - auth.global.ts não desvia mais para /onboarding nem chama /api/dashboard.
// - O operador de estoque, o domínio gestaoestoque.com e a conta pendente
//   continuam desviados ANTES (middlewares globais), também em / e /onboarding.
// - Login, 403 e logotipo continuam apontando para /.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const { parse } = require('vue/compiler-sfc')

const ler = (rel) => fs.readFileSync(path.resolve(__dirname, rel), 'utf8')
const transpile = (src) =>
  ts.transpileModule(src, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText

// ═══════════════════════════════════════════════ páginas / e /onboarding
for (const p of ['index.vue', 'onboarding.vue']) {
  const { descriptor } = parse(ler(`../pages/${p}`))
  const s = descriptor.scriptSetup.content
  assert.match(s, /await navigateTo\('\/tarefas', \{ redirectCode: 302 \}\)/, `${p}: redireciona para /tarefas`)
  assert.doesNotMatch(s, /definePageMeta/, `${p}: sem permissão própria (o 403 manda para /)`)
  assert.doesNotMatch(s, /\/api\/dashboard/, `${p}: não chama mais /api/dashboard`)
  assert.doesNotMatch(descriptor.template.content, /Termine a configuração inicial|Alertas|Anúncios por canal|Sincronizações recentes/,
    `${p}: o conteúdo do Dashboard saiu`)
}
assert.doesNotMatch(ler('../pages/tarefas.vue'), /definePageMeta/, 'Tarefas continua aberta a todos')

// ═══════════════════════════════════════════════ middlewares globais
function carregar(rel, { user = null, host = 'app.davinci.com.br' } = {}) {
  const src = ler(rel)
  const exp = {}
  const globais = {
    defineNuxtRouteMiddleware: (fn) => fn,
    navigateTo: (p) => ({ redirect: p }),
    useAuthStore: () => ({ fetched: true, fetchMe: async () => {}, isAuthenticated: Boolean(user), user }),
    useRequestURL: () => new URL(`https://${host}/`),
    useRuntimeConfig: () => ({}),
    useRequestHeaders: () => ({}),
    $fetch: async () => { throw new Error('não deveria chamar a API') },
  }
  const nomes = Object.keys(globais)
  new Function('exports', 'require', ...nomes, transpile(src))(exp, require, ...nomes.map((n) => globais[n]))
  return exp.default
}
const destino = async (rel, path_, opts) => {
  const r = await carregar(rel, opts)({ path: path_, fullPath: path_ })
  return r?.redirect
}

;(async () => {
  const auth = '../middleware/auth.global.ts'
  const fonte = ler(auth)
  assert.doesNotMatch(fonte, /\/api\/dashboard/, 'auth.global.ts não chama mais /api/dashboard')
  assert.doesNotMatch(fonte, /navigateTo\('\/onboarding'\)/, 'sem desvio de primeiro acesso')

  const ADMIN = { status: 'active', role: 'admin', permissions: {} }
  const COMUM = { status: 'active', role: 'user', permissions: { devolucoes: { view: true } } }
  const OPERADOR = { status: 'active', role: 'user', stock_tags: ['sp'], permissions: { controle_estoque: { view: true } } }
  const PENDENTE = { status: 'pending', role: 'user', permissions: {} }

  // Admin e usuário comum: o middleware deixa passar e a página / leva a /tarefas.
  for (const u of [ADMIN, COMUM]) {
    assert.equal(await destino(auth, '/', { user: u }), undefined)
    assert.equal(await destino(auth, '/onboarding', { user: u }), undefined)
    assert.equal(await destino(auth, '/tarefas', { user: u }), undefined)
    assert.equal(await destino(auth, '/login', { user: u }), '/', 'logado em /login → / (que abre Tarefas)')
    assert.equal(await destino(auth, '/pending-approval', { user: u }), '/', 'conta aprovada em /pending-approval → /')
  }
  // Operador de estoque: /controle-estoque tem prioridade, também em / e /onboarding.
  for (const r of ['/', '/onboarding', '/tarefas']) {
    assert.equal(await destino(auth, r, { user: OPERADOR }), '/controle-estoque', `operador em ${r}`)
    assert.equal(await destino(auth, r, { user: PENDENTE }), '/pending-approval', `pendente em ${r}`)
  }
  // Sem login: vai ao login guardando o ?next=.
  assert.equal(await destino(auth, '/', {}), '/login?next=%2F')

  // Domínio gestaoestoque.com: todo mundo, até admin, vai para /controle-estoque.
  const dominio = '../middleware/operator-domain.global.ts'
  for (const r of ['/', '/onboarding', '/tarefas']) {
    assert.equal(await destino(dominio, r, { user: ADMIN, host: 'gestaoestoque.com' }), '/controle-estoque', `gestaoestoque.com${r}`)
    assert.equal(await destino(dominio, r, { user: ADMIN }), undefined, `outro domínio${r}: sem desvio`)
  }

  // ═══════════════════════════════════════════════ quem leva a / continua apontando para /
  assert.match(ler('../pages/login.vue'), /\(route\.query\.next as string\) \|\| \(requiresApproval \? '\/pending-approval' : '\/'\)/,
    'login: respeita o ?next= e, sem ele, vai para /')
  assert.match(ler('../pages/403.vue'), /<NuxtLink to="\/" class="text-sm underline">voltar ao início<\/NuxtLink>/)
  assert.match(ler('../components/AppTopbar.vue'), /<NuxtLink to="\/" [^>]*aria-label="DaVinci — início">/)

  console.log('pagina-inicial-tarefas: ok')
})().catch((e) => { console.error(e); process.exit(1) })
