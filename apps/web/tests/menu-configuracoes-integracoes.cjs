// Run from apps/web: node tests/menu-configuracoes-integracoes.cjs
// Novos procedimentos 9 e 10 (17/11/2026):
// - Integrações (Integrações · Sincronizações · Alertas) e o Histórico saem do
//   menu (eram de Sistema) e viram a aba Integrações de Cadastros, com o
//   Histórico dentro: Cadastros › Integrações › Histórico.
// - Usuários (com Permissões e Tarefas) e Faturas saem do menu e viram abas de
//   Configurações: Configurações › Usuários e Configurações › Faturas.
// As rotas não mudam (o padrão de lib/navGroups): o link antigo abre a página
// já no lugar novo, com as abas e o menu marcando o grupo. As permissões
// também não: as páginas têm o mesmo middleware de antes, e o teste monta o
// menu de verdade para cada perfil.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { parse } = require('vue/compiler-sfc')

const transpile = (src, module = ts.ModuleKind.CommonJS) =>
  ts.transpileModule(src, { compilerOptions: { target: ts.ScriptTarget.ES2022, module, esModuleInterop: true } }).outputText
const ler = (rel) => fs.readFileSync(path.resolve(__dirname, rel), 'utf8')
function loadLib(rel) {
  const exp = {}
  new Function('exports', 'require', transpile(ler(rel)))(exp, require)
  return exp
}
const nav = loadLib('../lib/navGroups.ts')
const v = { view: true }
const P = (o) => ({ status: 'active', ...o })
const ADMIN = P({ role: 'admin', historico: true })
const ADMIN_SEM_HISTORICO = P({ role: 'admin' })
// Como o jinshi em produção: vê Configurações e as três telas de Integrações.
const GESTOR = P({ role: 'user', permissions: { configuracoes: v, integracoes: v, sincronizacoes: v, alertas: v } })
const SO_ALERTAS = P({ role: 'user', permissions: { alertas: v } })
const COMUM = P({ role: 'user', permissions: { devolucoes: v } })
const OPERADOR = P({ role: 'user', stock_tags: ['sp'], permissions: { controle_estoque: v } })
const tos = (tabs) => tabs.map((t) => t.to)
const rotulos = (tabs) => tabs.map((t) => t.label)

// ═══════════════════════════════════════════════ lib/navGroups
{
  assert.equal(nav.TABS_SISTEMA, undefined, 'o grupo Sistema acabou')
  assert.deepEqual(rotulos(nav.TABS_INTEGRACOES), ['Integrações', 'Sincronizações', 'Alertas', 'Histórico'])
  assert.deepEqual(nav.TABS_INTEGRACOES.map((t) => t.resource ?? t.soComChave),
    ['integracoes', 'sincronizacoes', 'alertas', 'historico'], 'mesmas permissões de antes')
  const ultima = nav.TABS_CADASTROS.at(-1)
  assert.equal(ultima.label, 'Integrações', 'Integrações é a última aba de Cadastros')
  assert.equal(ultima.abas, nav.TABS_INTEGRACOES)
  assert.deepEqual(rotulos(nav.TABS_CONFIGURACOES), ['Configurações', 'Usuários', 'Faturas'])
  const [cfg, usu, fat] = nav.TABS_CONFIGURACOES
  assert.equal(cfg.resource, 'configuracoes')
  assert.ok(usu.adminOnly && usu.abas === nav.TABS_USUARIOS, 'Usuários: só admin, com Permissões e Tarefas dentro')
  assert.ok(fat.adminOnly && fat.to === '/faturas', 'Faturas: só admin')
  assert.deepEqual(nav.tabRoutes(usu), ['/users', '/permissoes', '/tarefas'])
  assert.deepEqual(nav.tabPontos(usu), ['tarefas'])
  assert.deepEqual(nav.tabPontos(fat), ['faturas'])

  // Abas de Configurações por perfil.
  assert.deepEqual(tos(nav.allowedTabs(nav.TABS_CONFIGURACOES, ADMIN)), ['/configuracoes', '/users', '/faturas'])
  assert.deepEqual(tos(nav.allowedTabs(nav.TABS_CONFIGURACOES, GESTOR)), ['/configuracoes'], 'quem não é admin não vê Usuários nem Faturas')
  assert.deepEqual(tos(nav.allowedTabs(nav.TABS_CONFIGURACOES, COMUM)), [])
  // Abas de Integrações: o Histórico só com a chave do /me, nem todo admin.
  assert.deepEqual(tos(nav.allowedTabs(nav.TABS_INTEGRACOES, ADMIN)), ['/integrations', '/sincronizacoes', '/alertas', '/historico'])
  assert.deepEqual(tos(nav.allowedTabs(nav.TABS_INTEGRACOES, ADMIN_SEM_HISTORICO)), ['/integrations', '/sincronizacoes', '/alertas'])
  assert.deepEqual(tos(nav.allowedTabs(nav.TABS_INTEGRACOES, GESTOR)), ['/integrations', '/sincronizacoes', '/alertas'])
  assert.deepEqual(tos(nav.allowedTabs(nav.TABS_INTEGRACOES, { ...GESTOR, historico: false })), ['/integrations', '/sincronizacoes', '/alertas'])
  // A aba Integrações de Cadastros: só para quem vê alguma de dentro, levando à primeira.
  const integDe = (u) => nav.allowedTabs(nav.TABS_CADASTROS, u).find((t) => t.label === 'Integrações')
  assert.equal(integDe(ADMIN).to, '/integrations')
  assert.equal(integDe(SO_ALERTAS).to, '/alertas', 'quem só vê Alertas cai nos Alertas')
  assert.equal(integDe(COMUM), undefined, 'sem nenhuma das telas, sem a aba')
  assert.equal(integDe(P({ role: 'user', historico: true })).to, '/historico')
}

// ═══════════════════════════════════════════════ menu lateral (AppSidebar de verdade)
function montarMenu(user, { rota = '/', tarefas = 0, faturas = 0 } = {}) {
  const { descriptor } = parse(ler('../components/AppSidebar.vue'))
  let src = descriptor.scriptSetup.content
  const icones = src.match(/import\s*\{([^}]*)\}\s*from\s*'lucide-vue-next'/)[1].split(',').map((s) => s.trim()).filter(Boolean)
  assert.match(src, /import \{ allowedTabs, tabRoutes, TABS_CADASTROS, TABS_CONFIGURACOES, TABS_NF \} from '~\/lib\/navGroups'/)
  src = src.replace(/^import[\s\S]*?from\s+'[^']+'\s*$/gm, '').replace(/import\.meta\.client/g, 'false')
  // O watch do usuário zera as contagens ao montar; elas chegam depois (o poll).
  const estado = { 'davinci.pendentes.tarefas': Vue.ref(0), 'davinci.pendentes.faturas': Vue.ref(0) }
  const params = {
    computed: Vue.computed, ref: Vue.ref, watch: Vue.watch, nextTick: Vue.nextTick, onMounted: () => {}, onScopeDispose: () => {},
    ...nav, useState: (k, ini) => estado[k] ?? Vue.ref(ini()),
    defineProps: () => ({}), withDefaults: (p, d) => ({ ...d, ...p, collapsed: false }), defineEmits: () => () => {},
    useAuthStore: () => ({ user, isAdmin: user.role === 'admin' }), useRoute: () => ({ path: rota }),
    useRuntimeConfig: () => ({ public: { enableMarketing: false } }), useApi: () => ({ api: async () => ({ count: 0 }) }),
  }
  for (const n of icones) params[n] = {}
  const nomes = Object.keys(params)
  const m = new Function(...nomes, transpile(src, ts.ModuleKind.ESNext) + '\nreturn { visibleSections, isActive, pontoDe }')(
    ...nomes.map((n) => params[n]))
  estado['davinci.pendentes.tarefas'].value = tarefas
  estado['davinci.pendentes.faturas'].value = faturas
  return m
}
const secao = (m, label) => m.visibleSections.value.find((s) => s.label === label)
const itens = (m, label) => (secao(m, label)?.items ?? []).map((i) => i.label)
const rotas = (m) => m.visibleSections.value.flatMap((s) => s.items.map((i) => i.to))
{
  for (const u of [ADMIN, ADMIN_SEM_HISTORICO, GESTOR, SO_ALERTAS, COMUM]) {
    const m = montarMenu(u)
    for (const r of ['/integrations', '/sincronizacoes', '/alertas', '/historico', '/users', '/faturas', '/permissoes']) {
      assert.ok(!rotas(m).includes(r), `${r} não é mais item do menu`)
    }
    assert.equal(secao(m, 'Sistema'), undefined, 'Sistema some (só a Auditoria do dono ficou)')
  }
  // Admin: Configurações agrupa Usuários e Faturas; Cadastros agrupa Integrações.
  const adm = montarMenu(ADMIN)
  assert.deepEqual(itens(adm, 'Admin'), ['Configurações'])
  const cfgItem = secao(adm, 'Admin').items[0]
  assert.equal(cfgItem.to, '/configuracoes')
  assert.deepEqual(cfgItem.match, ['/configuracoes', '/users', '/permissoes', '/tarefas', '/faturas'])
  const cad = secao(adm, 'Cadastros').items.find((i) => i.label === 'Cadastros')
  for (const r of ['/integrations', '/sincronizacoes', '/alertas', '/historico']) assert.ok(cad.match.includes(r), `Cadastros marcado em ${r}`)

  // Marcação do item certo em cada página.
  const ativo = (u, rota) => {
    const m = montarMenu(u, { rota })
    return m.visibleSections.value.flatMap((s) => s.items).filter((i) => m.isActive(i)).map((i) => i.label)
  }
  assert.deepEqual(ativo(ADMIN, '/historico'), ['Cadastros'])
  assert.deepEqual(ativo(ADMIN, '/sincronizacoes'), ['Cadastros'])
  assert.deepEqual(ativo(ADMIN, '/faturas'), ['Configurações'])
  assert.deepEqual(ativo(ADMIN, '/users/7f3c'), ['Configurações'])
  assert.deepEqual(ativo(ADMIN, '/tarefas'), ['Configurações'])
  assert.deepEqual(ativo(GESTOR, '/tarefas'), ['Tarefas'], 'quem não é admin: só o item Tarefas, não Configurações')
  assert.deepEqual(ativo(GESTOR, '/alertas'), ['Cadastros'])

  // Quem não é admin: igual a antes (Configurações + Tarefas), e Integrações em Cadastros.
  const ges = montarMenu(GESTOR)
  assert.deepEqual(itens(ges, 'Admin'), ['Configurações', 'Tarefas'])
  assert.deepEqual(secao(ges, 'Admin').items[0].match, ['/configuracoes'])
  // Sem a permissão de Configurações o item continua no menu e leva ao 403, como antes.
  const com = montarMenu(COMUM)
  assert.deepEqual(itens(com, 'Admin'), ['Configurações', 'Tarefas'])
  assert.equal(secao(com, 'Admin').items[0].to, '/configuracoes')
  assert.deepEqual(itens(com, 'Cadastros'), ['Imobilizado'], 'sem Integrações, o Cadastros dele não muda')
  assert.deepEqual(montarMenu(OPERADOR).visibleSections.value.flatMap((s) => s.items.map((i) => i.to)), ['/controle-estoque'])

  // Bolinha: uma só no item, com as duas contagens.
  const comPend = montarMenu(ADMIN, { tarefas: 2, faturas: 1 })
  assert.equal(comPend.pontoDe(secao(comPend, 'Admin').items[0]), '2 tarefas pendentes · 1 fatura vencendo')
  assert.equal(comPend.pontoDe(secao(comPend, 'Cadastros').items[0]), null)
  const so1 = montarMenu(ADMIN, { faturas: 1 })
  assert.equal(so1.pontoDe(secao(so1, 'Admin').items[0]), '1 fatura vencendo')
  const gesPend = montarMenu(GESTOR, { tarefas: 1, faturas: 3 })
  assert.equal(gesPend.pontoDe(secao(gesPend, 'Admin').items[0]), null, 'Configurações dele não tem Tarefas nem Faturas')
  assert.equal(gesPend.pontoDe(secao(gesPend, 'Admin').items[1]), '1 tarefa pendente')
}

// ═══════════════════════════════════════════════ RouteTabs (abas de dentro, marcação e bolinha)
{
  const { descriptor } = parse(ler('../components/RouteTabs.vue'))
  const src = descriptor.scriptSetup.content.replace(/^import[\s\S]*?from\s+'[^']+'\s*$/gm, '')
  const montar = (tabs, user, rota, pend = {}) => {
    const params = {
      computed: Vue.computed, ...nav, defineProps: () => ({ tabs }),
      useAuthStore: () => ({ user }), useRoute: () => ({ path: rota }),
      useState: (k) => Vue.ref(pend[k.split('.').at(-1)] ?? 0),
    }
    const nomes = Object.keys(params)
    return new Function(...nomes, transpile(src, ts.ModuleKind.ESNext) + '\nreturn { visible, isActive, temPonto }')(
      ...nomes.map((n) => params[n]))
  }
  const c = montar(nav.TABS_CADASTROS, ADMIN, '/historico')
  const integ = c.visible.value.find((t) => t.label === 'Integrações')
  assert.ok(c.isActive(integ), 'em /historico a aba Integrações de Cadastros fica marcada')
  assert.ok(!c.isActive(c.visible.value.find((t) => t.label === 'Cadastros')))
  const s = montar(nav.TABS_CONFIGURACOES, ADMIN, '/tarefas', { tarefas: 3 })
  const [cfg, usu, fat] = s.visible.value
  assert.ok(s.isActive(usu) && !s.isActive(cfg) && !s.isActive(fat), 'Tarefas fica dentro de Usuários')
  assert.ok(s.temPonto(usu) && !s.temPonto(fat) && !s.temPonto(cfg))
  const f = montar(nav.TABS_CONFIGURACOES, ADMIN, '/faturas', { faturas: 1 })
  assert.ok(f.temPonto(f.visible.value[2]) && f.isActive(f.visible.value[2]))
  assert.equal(montar(nav.TABS_CONFIGURACOES, GESTOR, '/configuracoes').visible.value.length, 1, 'uma aba só: a barra não aparece')
  assert.match(descriptor.template.content, /<nav\s+v-if="visible\.length > 1"/)
}

// ═══════════════════════════════════════════════ páginas: barras de abas e a MESMA trava de antes
{
  const pagina = (rel) => ler(`../pages/${rel}`)
  for (const p of ['integrations.vue', 'sincronizacoes.vue', 'alertas.vue', 'historico.vue']) {
    const s = pagina(p)
    assert.match(s, /import \{ TABS_CADASTROS, TABS_INTEGRACOES \} from '~\/lib\/navGroups'/, p)
    assert.match(s, /<RouteTabs :tabs="TABS_CADASTROS" \/>\s*<RouteTabs :tabs="TABS_INTEGRACOES" sub \/>/, `${p}: Cadastros e, embaixo, Integrações`)
  }
  for (const p of ['users/index.vue', 'permissoes.vue', 'tarefas.vue']) {
    const s = pagina(p)
    assert.match(s, /<RouteTabs :tabs="TABS_CONFIGURACOES" \/>\s*<RouteTabs :tabs="TABS_USUARIOS" sub \/>/, `${p}: Configurações e, embaixo, Usuários`)
  }
  for (const p of ['faturas.vue', 'configuracoes.vue']) {
    assert.match(pagina(p), /<RouteTabs :tabs="TABS_CONFIGURACOES" \/>/, p)
  }
  // Permissões das páginas: as mesmas de antes.
  assert.match(pagina('faturas.vue'), /definePageMeta\(\{\s*middleware: \['admin'\],\s*\}\)/)
  assert.match(pagina('users/index.vue'), /definePageMeta\(\{ middleware: \['admin'\] \}\)/)
  assert.match(pagina('users/[id].vue'), /definePageMeta\(\{ middleware: \['admin'\] \}\)/)
  assert.match(pagina('permissoes.vue'), /definePageMeta\(\{ middleware: \['admin'\] \}\)/)
  assert.match(pagina('configuracoes.vue'), /permission: \{ resource: 'configuracoes', action: 'view' \}/)
  assert.match(pagina('integrations.vue'), /permission: \{ resource: 'integracoes', action: 'view' \}/)
  assert.match(pagina('sincronizacoes.vue'), /permission: \{ resource: 'sincronizacoes', action: 'view' \}/)
  assert.match(pagina('alertas.vue'), /permission: \{ resource: 'alertas', action: 'view' \}/)
  assert.match(pagina('historico.vue'), /if \(auth\.user\?\.historico !== true\) \{\s*\/\/ Mesma mensagem do Nuxt para rota inexistente\./)
  assert.doesNotMatch(pagina('tarefas.vue'), /definePageMeta/, 'Tarefas continua aberta a todos (cada um vê as suas)')
  assert.match(pagina('ouvidoria/robos.vue'), />Cadastros › Integrações<\/NuxtLink>/)
}

// ═══════════════════════════════════════════════ tela de Permissões e nomes do Histórico
{
  const can = new Function('exports', 'require', 'useAuthStore', transpile(ler('../composables/useCan.ts')))
  const exp = {}
  can(exp, require, () => ({ user: null }))
  assert.equal(exp.RESOURCE_GROUPS.find((g) => g.label === 'Sistema'), undefined)
  const cadastros = exp.RESOURCE_GROUPS.find((g) => g.label === 'Cadastros').resources
  assert.deepEqual(cadastros.slice(-4), ['integracoes', 'sincronizacoes', 'alertas', 'sync_logs'])
  assert.deepEqual(exp.RESOURCE_GROUPS.find((g) => g.label === 'Admin').resources, ['usuarios', 'permissoes', 'configuracoes'])

  const nomes = fs.readFileSync(path.resolve(__dirname, '../../api/app/historico/nomes.py'), 'utf8')
  for (const [rota, nome] of [
    ['/historico', 'Integrações › Histórico'], ['/users', 'Configurações › Usuários'], ['/permissoes', 'Configurações › Permissões'],
    ['/faturas', 'Configurações › Faturas'], ['/configuracoes', 'Configurações'], ['/api/historico', 'Integrações › Histórico'],
    ['/api/users', 'Configurações › Usuários'], ['/api/faturas', 'Configurações › Faturas'], ['/api/settings', 'Configurações'],
  ]) assert.ok(nomes.includes(`    "${rota}": "${nome}",`), `nomes.py: ${rota} → ${nome}`)
  assert.doesNotMatch(nomes, /"Sistema › Histórico"|"Admin › (Usuários|Permissões|Faturas|Configurações)"/)
}

console.log('menu-configuracoes-integracoes: ok')
