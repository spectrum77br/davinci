// node tests/atendimento-so-leitura.cjs — Atendimento em SÓ LEITURA para a equipe (Eduardo, 07/10/2026).
// "Pode liberar pras outras pessoas do DaVinci verem pra já obtermos feedbacks, mas claro por
// enquanto só leitura ... continua só sugerindo ali se clicar." Fase de observação:
//  - quem VÊ e quem MEXE vêm do /api/auth/me (`atendimento`, `atendimento_mexe`), não do JSON de
//    permissões: o recurso continua fora da tela de Permissões (ninguém o tem);
//  - o item "Atendimento" do menu aparece para quem vem com `atendimento: true` (admin ou não);
//  - a página só pede o middleware `atendimento` (sem o `admin`);
//  - quem só lê vê o aviso discreto "Só leitura por enquanto — sugestões e 👍/👎 liberados", as
//    ações de mexer somem/desligam com essa frase (inclusive para admin fora da lista), e o
//    "Sugerir agora" e o 👍/👎 continuam (canSugerir → canAvaliar na conversa);
//  - a frase é a mesma em toda a tela e no 403 da API (`atendimento_so_leitura`), e as rotas que a
//    tela chama para quem só lê estão no ROTAS_DE_QUEM_LE do backend;
//  - o 👍/👎 de quem só lê some na nota que OUTRA pessoa deu (`de_outra_pessoa`, que a API manda) —
//    fica o selo "por outra pessoa" — e o 409 da corrida vira aviso, não erro.
// A API tem a mesma trava (SO_ADMIN em apps/api/app/routers/atendimento.py, testada em
// apps/api/tests/test_atendimento_so_leitura.py). Substitui o atendimento-so-admin.cjs (30/09).
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { renderToString } = require('vue/server-renderer')
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')

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
function sfc(rel) {
  const filename = path.resolve(__dirname, rel)
  const fonte = fs.readFileSync(filename, 'utf8')
  const { descriptor, errors } = parse(fonte, { filename })
  assert.deepEqual(errors, [], rel)
  if (descriptor.template) {
    const tpl = compileTemplate({ source: descriptor.template.content, filename, id: path.basename(rel), compilerOptions: { isTS: true } })
    assert.deepEqual(tpl.errors, [], `${rel}: template compila`)
  }
  return { descriptor, fonte }
}
// `~/components/X.vue` → os exports do <script> (não-setup) dele.
const cache = new Map()
function requerer(nome) {
  const m = /^~\/components\/(.+\.vue)$/.exec(nome)
  if (!m) return require(nome)
  if (!cache.has(m[1])) cache.set(m[1], exportsDe(sfc(`../components/${m[1]}`).descriptor))
  return cache.get(m[1])
}
function exportsDe(descriptor) {
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(descriptor.script.content))(requerer, mod, mod.exports)
  return mod.exports
}
function trecho(src, ini, fim) {
  const a = src.indexOf(ini)
  const b = src.indexOf(fim, a + 1)
  assert.ok(a >= 0 && b > a, `trecho ${ini} … ${fim}`)
  return transpile(src.slice(a, b))
}
const api = (rel) => fs.readFileSync(path.resolve(__dirname, '../../api/app', rel), 'utf8')

const AVISO = 'Só leitura por enquanto — sugestões e 👍/👎 liberados.'
const TUDO = { view: true, edit: true, delete: true }
// Quem o /me devolve, na fase de observação.
const DONO = { id: 'd', role: 'admin', status: 'active', permissions: {}, atendimento: true, atendimento_mexe: true }
const ADMIN_FORA = { id: 'a', role: 'admin', status: 'active', permissions: {}, atendimento: true, atendimento_mexe: false }
const COMUM = { id: 'u1', role: 'user', status: 'active', permissions: {}, atendimento: true, atendimento_mexe: false }
// JSON antigo com o recurso inteiro: não mexe por isso (quem mexe é a lista).
const COM_RECURSO = { id: 'u2', role: 'user', status: 'active', permissions: { atendimento: TUDO }, atendimento: true, atendimento_mexe: false }
const OPERADOR = { id: 'op', role: 'user', status: 'active', permissions: { controle_estoque: TUDO }, stock_tags: ['ci'], atendimento: false, atendimento_mexe: false }
const SEM_CHAVE = { id: 'old', role: 'user', status: 'active', permissions: {} }

const P = exportsDe(sfc('../components/AtendimentoPlataforma.vue').descriptor)

// ------------------------------------------------ a frase: uma só, na tela e na API
{
  assert.equal(P.AVISO_SO_LEITURA, AVISO)
  assert.equal(P.ERROS.atendimento_so_leitura, AVISO, 'o 403 da API vira a mesma frase')
  assert.ok(P.ERROS.avaliacao_de_outra_pessoa, 'o 409 do 👍/👎 por cima da nota de outra pessoa')
  assert.ok(P.ERROS.atendimento_restrito)
  const r = P.erroDaApi({ data: { detail: { code: 'atendimento_so_leitura', detail: 'qualquer' } } }, 'x')
  assert.equal(r.texto, AVISO)
  const rotas = api('routers/atendimento.py')
  assert.match(rotas, /"code": "atendimento_so_leitura",\s*"detail": "Só leitura por enquanto — sugestões e 👍\/👎 liberados\./)
  // Toda frase "só leitura" escrita à mão nos componentes é a mesma; a antiga sumiu.
  const pasta = path.resolve(__dirname, '../components')
  const arquivos = fs.readdirSync(pasta).filter((f) => f.startsWith('Atendimento') && f.endsWith('.vue'))
  let copias = 0
  for (const f of arquivos) {
    const src = fs.readFileSync(path.join(pasta, f), 'utf8')
    assert.ok(!/falta a permissão de editar o Atendimento/i.test(src), `${f}: a frase antiga ("falta a permissão")`)
    for (const m of src.matchAll(/Só leitura por enquanto[^'"<`]*/g)) {
      if (f === 'AtendimentoPlataforma.vue') continue
      assert.equal(m[0], AVISO, `${f}: a frase escrita à mão = AVISO_SO_LEITURA`)
      copias++
    }
  }
  assert.ok(copias >= 5, 'Avaliação, Publicação, AdsPower, Carrinho e Nota')
}

// ------------------------------------------------ useCan.ts (tela de Permissões)
let usuarioAtual = null
const lojaFalsa = () => ({ user: usuarioAtual, isAdmin: usuarioAtual?.role === 'admin' })
const can = loadLib('../composables/useCan.ts', { useAuthStore: lojaFalsa })
{
  const posVenda = can.RESOURCE_GROUPS.find((g) => g.label === 'Pós-venda')
  assert.deepEqual(posVenda.resources, ['devolucoes', 'reembolso', 'logistica', 'notas_fiscais', 'chamados'],
    'o recurso continua fora da tela de Permissões (quem vê e quem mexe vêm do /me)')
  for (const g of can.RESOURCE_GROUPS) assert.ok(!g.resources.includes('atendimento'), `atendimento fora do grupo ${g.label}`)
  assert.ok(!can.RESOURCES.includes('atendimento'))
  assert.equal(can.RESOURCE_LABELS.atendimento, 'Atendimento', 'o rótulo continua (para quando a fase acabar)')
}

// ------------------------------------------------ middleware/atendimento.ts (a página)
{
  const destino = []
  const mw = loadLib('../middleware/atendimento.ts', {
    defineNuxtRouteMiddleware: (fn) => fn,
    useAuthStore: lojaFalsa,
    navigateTo: (to) => { destino.push(to); return to },
  }).default
  for (const u of [DONO, ADMIN_FORA, COMUM, COM_RECURSO]) {
    usuarioAtual = u
    assert.equal(mw({ path: '/atendimento' }), undefined, `${u.id} entra (lê)`)
  }
  for (const u of [OPERADOR, SEM_CHAVE]) {
    usuarioAtual = u
    assert.equal(mw({ path: '/atendimento' }), '/403', `${u.id} não entra`)
  }
  usuarioAtual = null
  assert.equal(mw({ path: '/atendimento' }), '/login')
}

// ------------------------------------------------ pages/atendimento.vue
const pagina = sfc('../pages/atendimento.vue')
{
  const setup = pagina.descriptor.scriptSetup.content
  const chamadas = setup.match(/definePageMeta\(([\s\S]*?)\)\s*\n/g) || []
  assert.equal(chamadas.length, 1, 'um definePageMeta só')
  let meta
  new Function('definePageMeta', chamadas[0])((m) => { meta = m })
  assert.deepEqual(meta, { middleware: ['atendimento'] }, 'página: só o middleware `atendimento` (sem o `admin`)')

  // As chaves de mexer da página, para cada pessoa.
  const js = trecho(setup, 'const mexe = computed', 'const agora = useRelogio()') + '\nreturn { mexe, soLeitura, canEdit, canDelete, isAdmin, canSugerir }'
  const flags = (u) => {
    const auth = { user: u }
    const useCanFalso = (resource, action) => Vue.computed(() => u.role === 'admin' || u.permissions?.[resource]?.[action] === true)
    const out = new Function('computed', 'auth', 'useCan', 'useIsAdmin', js)(
      Vue.computed, auth, useCanFalso, () => Vue.computed(() => u.role === 'admin'),
    )
    return Object.fromEntries(Object.entries(out).map(([k, v]) => [k, v.value]))
  }
  assert.deepEqual(flags(DONO), { mexe: true, soLeitura: false, canEdit: true, canDelete: true, isAdmin: true, canSugerir: true })
  for (const u of [ADMIN_FORA, COMUM, COM_RECURSO]) {
    assert.deepEqual(flags(u), { mexe: false, soLeitura: true, canEdit: false, canDelete: false, isAdmin: false, canSugerir: true }, u.id)
  }

  const tela = pagina.descriptor.template.content
  assert.match(tela, /v-if="soLeitura"[\s\S]{0,400}data-aviso-so-leitura[\s\S]{0,200}\{\{ AVISO_SO_LEITURA \}\}/, 'o aviso discreto para quem só lê')
  assert.match(tela, /<AtendimentoConversa[\s\S]*?:can-edit="canEdit"\s+:can-sugerir="canSugerir"/)
  assert.match(tela, /<AtendimentoCanais v-if="aba === 'lojas'" :can-edit="canEdit" :is-admin="isAdmin"/)
  assert.match(tela, /<AtendimentoManual v-if="aba === 'manual'" :can-edit="canEdit" :can-delete="canDelete" \/>/)
  assert.match(tela, /<AtendimentoModelos v-if="aba === 'modelos'" :can-edit="canEdit" :can-delete="canDelete"/)
  assert.match(tela, /<AtendimentoAutomaticas v-if="aba === 'automaticas'" :can-edit="canEdit"/)
}

// ------------------------------------------------ components/AppSidebar.vue (o menu)
const nav = loadLib('../lib/navGroups.ts')
function menuPara(user) {
  const filename = path.resolve(__dirname, '../components/AppSidebar.vue')
  const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
  assert.deepEqual(errors, [])
  let src = descriptor.scriptSetup.content
  const icones = (src.match(/import\s*\{([^}]*)\}\s*from\s*'lucide-vue-next'/) || [])[1]
  assert.ok(icones, 'import dos ícones')
  const nomesIcones = icones.split(',').map((s) => s.trim()).filter(Boolean)
  src = src.replace(/^import[\s\S]*?from\s+'[^']+'\s*$/gm, '').replace(/import\.meta\.client/g, 'false')
  const js = transpile(src, ts.ModuleKind.ESNext) + '\nreturn { sections, visibleSections }'
  const params = {
    computed: Vue.computed, ref: Vue.ref, watch: Vue.watch, nextTick: Vue.nextTick,
    onMounted: () => {}, onScopeDispose: () => {},
    allowedTabs: nav.allowedTabs, TABS_CADASTROS: nav.TABS_CADASTROS, TABS_NF: nav.TABS_NF, TABS_SISTEMA: nav.TABS_SISTEMA,
    defineProps: () => ({}), withDefaults: (p, d) => ({ ...d, ...p, collapsed: false }), defineEmits: () => () => {},
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
const rotas = (secoes) => secoes.flatMap((s) => s.items.map((i) => i.to))
{
  for (const u of [DONO, ADMIN_FORA, COMUM, COM_RECURSO]) {
    assert.ok(rotas(menuPara(u)).includes('/atendimento'), `${u.id} vê o Atendimento no menu`)
  }
  for (const u of [SEM_CHAVE, { ...ADMIN_FORA, atendimento: false }]) {
    assert.ok(!rotas(menuPara(u)).includes('/atendimento'), `${u.id}: sem a chave, sem o item`)
  }
  assert.deepEqual(rotas(menuPara(OPERADOR)), ['/controle-estoque'], 'o operador continua preso no estoque')
  const item = menuPara(COMUM).flatMap((s) => s.items).find((i) => i.to === '/atendimento')
  assert.equal(item.atendimentoOnly, true)
  assert.equal(item.adminOnly, undefined, 'não é mais só de admin')
  assert.equal(item.resource, undefined, 'não depende do recurso (fora da tela de Permissões)')
  const posVenda = menuPara(COMUM).find((s) => s.items.some((i) => i.to === '/atendimento'))
  assert.deepEqual(posVenda.items.map((i) => i.to), ['/devolucoes', '/reembolso', '/logistica', '/notas-fiscais', '/chamados', '/atendimento'])
}

// ------------------------------------------------ stores/auth.ts (o contrato do /me)
{
  const loja = fs.readFileSync(path.resolve(__dirname, '../stores/auth.ts'), 'utf8')
  assert.match(loja, /atendimento\?: boolean\n\s*atendimento_mexe\?: boolean/)
  const auth = api('routers/auth.py')
  assert.match(auth, /extra\["atendimento"\] = atendimento_acesso\.pode_ver\(user\)/)
  assert.match(auth, /extra\["atendimento_mexe"\] = extra\["atendimento"\] and atendimento_acesso\.pode_mexer\(user\)/)
}

// ------------------------------------------------ a conversa: quem só lê sugere e avalia
const conversa = sfc('../components/AtendimentoConversa.vue')
{
  const setup = conversa.descriptor.scriptSetup.content
  const tela = conversa.descriptor.template.content
  assert.match(setup, /canSugerir\?: boolean/)
  assert.match(setup, /const canAvaliar = computed\(\(\) => props\.canEdit \|\| props\.canSugerir === true\)/)
  // "Sugerir agora": quem lê também pede (com as mesmas travas da conversa).
  const js = trecho(setup, 'const podeSugerir = computed', '// Por que não veio sugestão') + '\nreturn podeSugerir'
  const pode = ({ canEdit, canSugerir, c = {}, rascunho = null, iaAtiva = true }) => {
    const props = { canEdit, canSugerir, flags: { ia_ativa: iaAtiva } }
    const canAvaliar = Vue.computed(() => props.canEdit || props.canSugerir === true)
    const conversaRef = Vue.ref({ somente_leitura: false, ia_pausada: false, situacao: 'aberta', ...c })
    return new Function('computed', 'props', 'conversa', 'rascunho', 'canAvaliar', js)(
      Vue.computed, props, conversaRef, Vue.ref(rascunho), canAvaliar,
    ).value
  }
  assert.equal(pode({ canEdit: false, canSugerir: true }), true, 'quem só lê pede a sugestão')
  assert.equal(pode({ canEdit: true, canSugerir: false }), true, 'quem mexe também')
  assert.equal(pode({ canEdit: false, canSugerir: false }), false)
  assert.equal(pode({ canEdit: false, canSugerir: true, c: { ia_pausada: true } }), false, 'as travas de sempre valem')
  assert.equal(pode({ canEdit: false, canSugerir: true, c: { somente_leitura: true } }), false)
  assert.equal(pode({ canEdit: false, canSugerir: true, iaAtiva: false }), false)
  // 👍/👎: o painel da observação e o "A IA teria respondido" usam o canAvaliar.
  assert.match(tela, /<AtendimentoObservacao[\s\S]*?:can-edit="canAvaliar"\s+:pode-sugerir="podeSugerir"/)
  const iaTeria = [...tela.matchAll(/<AtendimentoIaTeria[\s\S]*?\/>/g)].map((m) => m[0])
  assert.equal(iaTeria.length, 2)
  for (const t of iaTeria) assert.match(t, /:can-edit="canAvaliar"/)
  // Mexer continua com o canEdit: a caixa Responder/Nota/Foto some, e o resto desliga com a frase.
  assert.match(tela, /<div v-if="!conversa\.somente_leitura && canEdit" class="flex items-center gap-1 text-xs" role="tablist" aria-label="caixa de resposta">/)
  assert.match(tela, /<template v-if="!conversa\.somente_leitura && canEdit">/, 'atribuir, pausar e fechar só para quem mexe')
  assert.match(tela, /:editavel="canEdit"/, 'etiqueta à mão só para quem mexe')
  assert.match(tela, /<AtendimentoNota[\s\S]*?:can-edit="canEdit"/)
  assert.match(tela, /<AtendimentoAbaResposta[\s\S]*?:can-edit="canEdit"/)
  assert.match(tela, /<AtendimentoPedido[\s\S]*?:can-edit="canEdit"/)
  assert.match(tela, /<AtendimentoAdsPower[\s\S]*?:can-edit="canEdit"/)
  assert.equal((setup.match(/if \(!props\.canEdit\) return AVISO_SO_LEITURA/g) || []).length, 2, 'caixa e foto: a frase do só leitura')
}

// ------------------------------------------------ as caixas dos cartões: a frase do só leitura
{
  const R = exportsDe(sfc('../components/AtendimentoAbaResposta.vue').descriptor)
  const aba = { chave: 'pre_venda', responde: { conversa_id: 'c1', pode_enviar: true } }
  assert.equal(R.bloqueioDaAba(aba, false), AVISO)
  assert.equal(R.bloqueioDaAba(aba, true), '')
  const req = (n) => require(n)
  const semImport = (rel) => {
    const { descriptor } = sfc(rel)
    const mod = { exports: {} }
    new Function('require', 'module', 'exports', transpile(descriptor.script.content))(req, mod, mod.exports)
    return mod.exports
  }
  const Av = semImport('../components/AtendimentoAvaliacao.vue')
  const caixa = Av.caixaDaAvaliacao(
    { plataforma: 'shopee', respondida: false, pode_responder: true, motivo_sem_resposta: null, conversa_id: 'x' },
    { canEdit: false, conversaId: 'outra', canalConversa: 'chat' },
  )
  assert.deepEqual(caixa, { modo: 'desligada', motivo: AVISO })
  const Pub = semImport('../components/AtendimentoPublicacao.vue')
  assert.deepEqual(Pub.caixaDe({ pode_responder: true, motivo_responder: null }, 'comentario', false), { modo: 'desligada', motivo: AVISO })
}

// ------------------------------------------------ o backend aceita o que a tela pede para quem lê
{
  const rotasPy = api('routers/atendimento.py')
  const bloco = (rotasPy.match(/^ROTAS_DE_QUEM_LE = frozenset\(\n([\s\S]*?)^\)/m) || [])[1] || ''
  const liberadas = [...bloco.matchAll(/\("(POST|PATCH|PUT|DELETE)", "([^"]+)"\)/g)].map((m) => `${m[1]} ${m[2]}`)
  assert.deepEqual(liberadas.sort(), [
    'POST /api/atendimento/adspower/aberto',
    'POST /api/atendimento/automacoes/previa',
    'POST /api/atendimento/conversas/{conversa_id}/rascunho',
    'POST /api/atendimento/rascunhos/{rascunho_id}/avaliacao',
  ])
  // E a tela chama exatamente essas para sugerir, avaliar, ver a prévia e registrar o AdsPower.
  const fonte = (f) => fs.readFileSync(path.resolve(__dirname, '../components', f), 'utf8')
  assert.match(fonte('AtendimentoConversa.vue'), /\/api\/atendimento\/conversas\/\$\{encodeURIComponent\(id\)\}\/rascunho`, \{ method: 'POST' \}/)
  assert.match(fonte('AtendimentoAvaliarIa.vue'), /\/api\/atendimento\/rascunhos\/\$\{encodeURIComponent\(id\)\}\/avaliacao`, \{ method: 'POST'/)
  assert.match(fonte('AtendimentoAutomaticas.vue'), /'\/api\/atendimento\/automacoes\/previa', \{\s*method: 'POST'/)
  assert.match(fonte('AtendimentoAbrirPlataforma.vue'), /'\/api\/atendimento\/adspower\/aberto', \{\s*method: 'POST'/)
}

// ------------------------------------------------ AtendimentoAvaliarIa: a nota de outra pessoa (setup de verdade, API falsa)
const avaliarSfc = sfc('../components/AtendimentoAvaliarIa.vue')
function montarAvaliar(props, user, { respostaApi = {} } = {}) {
  const script = compileScript(avaliarSfc.descriptor, { id: 'avaliar-ia', inlineTemplate: false })
  const chamadas = []
  const avisos = []
  const req = (nome) => {
    if (nome === 'lucide-vue-next') return new Proxy({}, { get: () => ({ render: () => Vue.h('i') }) })
    return requerer(nome)
  }
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', 'ref', 'computed', 'watch', 'inject', 'useApi', 'useToasts', 'useAuthStore', transpile(script.content))(
    req, mod, mod.exports, Vue.ref, Vue.computed, Vue.watch, () => null,
    () => ({ api: async (url, opts) => { chamadas.push({ url, opts }); if (respostaApi instanceof Error) throw respostaApi; return respostaApi } }),
    () => ({ success: (t) => avisos.push(['success', t]), error: (t) => avisos.push(['error', t]), info: (t) => avisos.push(['info', t]) }),
    () => ({ user }),
  )
  const emitidos = []
  const reativo = Vue.reactive({ rotuloBoa: 'Boa', avaliacao: null, ...props })
  const estado = Vue.proxyRefs(mod.exports.default.setup(reativo, { emit: (...a) => emitidos.push(a), expose: () => {}, attrs: {}, slots: {} }))
  return { estado, chamadas, avisos, emitidos, reativo }
}
async function renderAvaliar(m) {
  const tpl = compileTemplate({ source: avaliarSfc.descriptor.template.content, filename: path.resolve(__dirname, '../components/AtendimentoAvaliarIa.vue'), id: 'avaliar-ia' })
  const mod = {}
  new Function('exports', 'require', transpile(tpl.code))(mod, require)
  const app = Vue.createSSRApp({ setup: () => ({ ...m.reativo, ...m.estado }), render: mod.render })
  app.component('Button', { props: ['disabled'], setup: (p, { slots }) => () => Vue.h('button', { disabled: p.disabled }, slots.default?.()) })
  for (const n of ['Check', 'Loader2', 'Pencil', 'ThumbsDown', 'ThumbsUp']) app.component(n, { render: () => Vue.h('i') })
  return (await renderToString(app)).replace(/<!--[\s\S]*?-->/g, '')
}
const botoes = (html) => [...html.matchAll(/<button[^>]*>([\s\S]*?)<\/button>/g)].map((b) => b[1].replace(/<[^>]+>/g, '').trim())

async function principal() {
  const DA_OUTRA = { nota: 'ok', correcao: null, de_outra_pessoa: true }
  const MINHA = { nota: 'erro', correcao: 'faltou o prazo', de_outra_pessoa: false }
  // Quem só lê, na nota de outra pessoa: só o selo, sem 👍/👎 (a API daria 409).
  for (const u of [COMUM, ADMIN_FORA]) {
    const m = montarAvaliar({ sugestaoId: 's1', avaliacao: DA_OUTRA, canEdit: true }, u)
    const html = await renderAvaliar(m)
    assert.match(html, /avaliada: boa/)
    assert.match(html, /data-avaliada-por-outra[^>]*>por outra pessoa</, `${u.id}: o selo diz de quem é`)
    assert.deepEqual(botoes(html), [], `${u.id}: sem Boa/Errou na nota de outra pessoa`)
    await m.estado.avaliar('ok')
    assert.equal(m.chamadas.length, 0, 'nem chama a API')
  }
  // A própria nota ela troca; e a sugestão sem nota ela avalia.
  {
    const html = await renderAvaliar(montarAvaliar({ sugestaoId: 's1', avaliacao: MINHA, canEdit: true }, COMUM))
    assert.deepEqual(botoes(html), ['Boa', 'Mudar correção'])
    assert.ok(!/por outra pessoa/.test(html))
    const livre = await renderAvaliar(montarAvaliar({ sugestaoId: 's2', avaliacao: null, canEdit: true }, COMUM))
    assert.deepEqual(botoes(livre), ['Boa', 'Errou'])
  }
  // Quem mexe troca qualquer nota (como antes), sem selo.
  {
    const html = await renderAvaliar(montarAvaliar({ sugestaoId: 's1', avaliacao: DA_OUTRA, canEdit: true }, DONO))
    assert.deepEqual(botoes(html), ['Errou'])
    assert.ok(!/por outra pessoa/.test(html))
  }
  // Sem canEdit (nem sugere nem avalia): nada, como antes.
  {
    const html = await renderAvaliar(montarAvaliar({ sugestaoId: 's1', avaliacao: DA_OUTRA, canEdit: false }, COMUM))
    assert.deepEqual(botoes(html), [])
    assert.ok(!/por outra pessoa/.test(html))
  }
  // Corrida: outra pessoa avaliou com a tela aberta → o 409 vira aviso (não erro) e os botões somem.
  {
    const erro409 = Object.assign(new Error('409'), { statusCode: 409, data: { detail: { code: 'avaliacao_de_outra_pessoa', detail: 'x' } } })
    const m = montarAvaliar({ sugestaoId: 's3', avaliacao: null, canEdit: true }, COMUM, { respostaApi: erro409 })
    await m.estado.avaliar('ok')
    assert.equal(m.chamadas.length, 1)
    assert.deepEqual(m.avisos, [['info', P.ERROS.avaliacao_de_outra_pessoa]])
    assert.deepEqual(m.emitidos, [])
    const html = await renderAvaliar(m)
    assert.deepEqual(botoes(html), [])
    assert.match(html, /já avaliada por outra pessoa/)
    // A nota dela chega na próxima atualização: fica o selo dela.
    m.reativo.avaliacao = DA_OUTRA
    await Vue.nextTick()
    assert.match(await renderAvaliar(m), /avaliada: boa[\s\S]*por outra pessoa/)
    // Outro erro continua erro.
    const m2 = montarAvaliar({ sugestaoId: 's4', avaliacao: null, canEdit: true }, COMUM, { respostaApi: Object.assign(new Error('500'), { statusCode: 500 }) })
    await m2.estado.avaliar('ok')
    assert.equal(m2.avisos[0][0], 'error')
  }
  // O contrato com a API: o campo vem na sugestão e na resposta do 👍/👎.
  {
    const esquemas = api('schemas/atendimento.py')
    assert.match(esquemas, /class AvaliacaoResumoOut\(BaseModel\):[\s\S]*?de_outra_pessoa: bool = False/)
    assert.match(esquemas, /class AvaliacaoOut\(BaseModel\):[\s\S]*?de_outra_pessoa: bool = False/)
    assert.match(fs.readFileSync(path.resolve(__dirname, '../components/AtendimentoPlataforma.vue'), 'utf8'), /export type AvaliacaoIa = \{[^}]*de_outra_pessoa\?: boolean \}/)
  }
}

principal().then(() => console.log('ok: atendimento-so-leitura'), (e) => { console.error(e); process.exit(1) })
