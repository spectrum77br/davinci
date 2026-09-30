// node tests/atendimento-so-admin.cjs — Atendimento SÓ ADMIN por enquanto (Eduardo, 30/09/2026).
// A primeira subida em produção é só observação e a caixa fica para admin:
//  - o item "Atendimento" do menu só aparece para admin (os outros itens de
//    Pós-venda continuam como estavam);
//  - a página /atendimento usa o middleware `admin` (não-admin vai para /403,
//    mesmo com o recurso `atendimento` no JSON de permissões);
//  - o recurso saiu da tela de Permissões (RESOURCE_GROUPS/RESOURCES), para
//    ninguém recebê-lo pela tela nem pelo "marcar a coluna toda"; o tipo e o
//    rótulo continuam (a página usa useCan('atendimento', 'edit')).
// A API tem a mesma trava (SO_ADMIN em apps/api/app/routers/atendimento.py,
// testada em apps/api/tests/test_atendimento_so_admin.py).
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { parse } = require('vue/compiler-sfc')

const transpile = (source, module = ts.ModuleKind.CommonJS) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module },
}).outputText

function loadLib(rel, globais = {}) {
  const exp = {}
  const nomes = Object.keys(globais)
  new Function('exports', 'require', ...nomes, transpile(fs.readFileSync(path.join(__dirname, rel), 'utf8')))(
    exp, require, ...nomes.map((n) => globais[n]),
  )
  return exp
}

const ADMIN = { id: 'a', role: 'admin', status: 'active', permissions: {} }
const TUDO = { view: true, edit: true, delete: true }
// Não-admin com o recurso inteiro (JSON antigo ou concedido pela API): mesmo assim, não entra.
const COM_ATENDIMENTO = { id: 'u1', role: 'user', status: 'active', permissions: { atendimento: TUDO } }
const POS_VENDA = {
  id: 'u2', role: 'user', status: 'active',
  permissions: { devolucoes: TUDO, reembolso: TUDO, logistica: TUDO, notas_fiscais: TUDO, chamados: TUDO },
}
const SEM_NADA = { id: 'u3', role: 'user', status: 'active', permissions: {} }

// ------------------------------------------------ useCan.ts (tela de Permissões)
let usuarioAtual = null
const lojaFalsa = () => ({ user: usuarioAtual, isAdmin: usuarioAtual?.role === 'admin' })
const can = loadLib('../composables/useCan.ts', { useAuthStore: lojaFalsa })
{
  const posVenda = can.RESOURCE_GROUPS.find((g) => g.label === 'Pós-venda')
  assert.deepEqual(posVenda.resources, ['devolucoes', 'reembolso', 'logistica', 'notas_fiscais', 'chamados'],
    'Pós-venda sem o atendimento, e os outros recursos do grupo como estavam')
  for (const g of can.RESOURCE_GROUPS) assert.ok(!g.resources.includes('atendimento'), `atendimento fora do grupo ${g.label}`)
  assert.ok(!can.RESOURCES.includes('atendimento'), 'fora da lista que a tela de usuário marca/desmarca')
  assert.equal(can.RESOURCE_LABELS.atendimento, 'Atendimento', 'o rótulo continua (para quando abrir)')
  // useCan: admin pode tudo (os botões da página); quem não tem nada, nada.
  usuarioAtual = ADMIN
  assert.equal(can.useCan('atendimento', 'edit').value, true)
  assert.equal(can.useCan('atendimento', 'delete').value, true)
  usuarioAtual = SEM_NADA
  assert.equal(can.useCan('atendimento', 'view').value, false)
}

// ------------------------------------------------ middleware/admin.ts (a página)
{
  const destino = []
  const mw = loadLib('../middleware/admin.ts', {
    defineNuxtRouteMiddleware: (fn) => fn,
    useAuthStore: lojaFalsa,
    navigateTo: (to) => { destino.push(to); return to },
  }).default
  usuarioAtual = ADMIN
  assert.equal(mw({ path: '/atendimento' }), undefined, 'admin entra')
  for (const u of [COM_ATENDIMENTO, POS_VENDA, SEM_NADA]) {
    usuarioAtual = u
    assert.equal(mw({ path: '/atendimento' }), '/403', `${u.id} não entra`)
  }
  assert.deepEqual(destino, ['/403', '/403', '/403'])
}

// ------------------------------------------------ pages/atendimento.vue
{
  const filename = path.resolve(__dirname, '../pages/atendimento.vue')
  const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
  assert.deepEqual(errors, [])
  const chamadas = descriptor.scriptSetup.content.match(/definePageMeta\(([\s\S]*?)\)\s*\n/g) || []
  assert.equal(chamadas.length, 1, 'um definePageMeta só')
  let meta
  new Function('definePageMeta', chamadas[0])((m) => { meta = m })
  assert.deepEqual(meta, { middleware: ['admin'] }, 'página: middleware admin, sem o `permission`')
}

// ------------------------------------------------ components/AppSidebar.vue (o menu)
const nav = loadLib('../lib/navGroups.ts')
function menuPara(user) {
  const filename = path.resolve(__dirname, '../components/AppSidebar.vue')
  const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
  assert.deepEqual(errors, [])
  let src = descriptor.scriptSetup.content
  // Os ícones viram objetos vazios; o resto dos imports entra por parâmetro.
  const icones = (src.match(/import\s*\{([^}]*)\}\s*from\s*'lucide-vue-next'/) || [])[1]
  assert.ok(icones, 'import dos ícones')
  const nomesIcones = icones.split(',').map((s) => s.trim()).filter(Boolean)
  src = src.replace(/^import[\s\S]*?from\s+'[^']+'\s*$/gm, '').replace(/import\.meta\.client/g, 'false')
  const js = transpile(src, ts.ModuleKind.ESNext) + '\nreturn { sections, visibleSections }'
  const params = {
    computed: Vue.computed, ref: Vue.ref, watch: Vue.watch,
    onMounted: () => {}, onScopeDispose: () => {},
    allowedTabs: nav.allowedTabs, TABS_CADASTROS: nav.TABS_CADASTROS, TABS_NF: nav.TABS_NF, TABS_SISTEMA: nav.TABS_SISTEMA,
    defineProps: () => ({ collapsed: false }), defineEmits: () => () => {},
    useAuthStore: () => ({ user, isAdmin: user.role === 'admin' }),
    useRoute: () => ({ path: '/' }),
    useRuntimeConfig: () => ({ public: { enableMarketing: false } }),
    useApi: () => ({ api: async () => ({ count: 0 }) }),
  }
  for (const n of nomesIcones) params[n] = {}
  const nomes = Object.keys(params)
  const out = new Function(...nomes, js)(...nomes.map((n) => params[n]))
  return out.visibleSections.value
}
const rotasDoGrupo = (secoes, label) => (secoes.find((s) => s.label === label)?.items ?? []).map((i) => i.to)
{
  const admin = menuPara(ADMIN)
  assert.ok(rotasDoGrupo(admin, 'Pós-venda').includes('/atendimento'), 'admin vê o Atendimento no menu')
  const item = admin.find((s) => s.label === 'Pós-venda').items.find((i) => i.to === '/atendimento')
  assert.equal(item.adminOnly, true)
  assert.equal(item.resource, undefined, 'o item não depende do recurso (que saiu da tela de Permissões)')

  const POS_VENDA_SEM_ATENDIMENTO = ['/devolucoes', '/reembolso', '/logistica', '/notas-fiscais', '/chamados']
  for (const u of [COM_ATENDIMENTO, POS_VENDA, SEM_NADA]) {
    const menu = menuPara(u)
    const todas = menu.flatMap((s) => s.items.map((i) => i.to))
    assert.ok(!todas.includes('/atendimento'), `${u.id}: o Atendimento some do menu`)
    // Os outros itens de Pós-venda não mudaram (o menu não filtra por recurso;
    // quem não tem, a página manda para /403, como antes).
    assert.deepEqual(rotasDoGrupo(menu, 'Pós-venda'), POS_VENDA_SEM_ATENDIMENTO, `${u.id}: Pós-venda como antes`)
  }
  assert.deepEqual(rotasDoGrupo(admin, 'Pós-venda'), [...POS_VENDA_SEM_ATENDIMENTO, '/atendimento'])
}

console.log('ok: atendimento-so-admin')
