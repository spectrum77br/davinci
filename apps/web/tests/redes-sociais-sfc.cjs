// Run from apps/web: node tests/redes-sociais-sfc.cjs
// Cadastros › Redes Sociais (Eduardo, 15/09/2026, v3 — layout da planilha):
// parse + compileTemplate da página (estilo companies-new-account.cjs),
// checagens dos helpers puros e, no molde de marcas-sfc.cjs, execução do
// <script setup> com api/timers falsos pra travar: as colunas da MARCA saem
// por PATCH /api/redes-sociais/marca/{id} (nunca /api/marcas); a senha da
// marca vem de GET .../marca/{id}/sac-senha só no clique, some em 30 s e ao
// recarregar/filtrar/desmontar; o corpo do POST/PATCH da conta nunca leva
// senha vazia nem reenvia a revelada; "voltar a herdar" e "limpar" mandam
// null explícito; tooltip do chip nunca inclui senha. Só dados FALSOS aqui.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { parse, compileTemplate } = require('vue/compiler-sfc')

const transpile = (source, module = ts.ModuleKind.CommonJS) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module },
}).outputText

// ---------------------------------------------------------------- SFC
const filename = path.join(__dirname, '../pages/redes-sociais.vue')
const source = fs.readFileSync(filename, 'utf8')
const { descriptor, errors } = parse(source, { filename })
assert.deepEqual(errors, [])
const compiled = compileTemplate({ source: descriptor.template.content, filename, id: 'redes-sociais-check' })
assert.deepEqual(compiled.errors, [])
// O render compilado precisa ser JS válido (imports do vue resolvem via require).
new Function('exports', 'require', transpile(compiled.code))({}, require)

const script = descriptor.scriptSetup.content
const tpl = descriptor.template.content

// ---------------------------------------------------------------- libs reais
function loadLib(rel) {
  const exp = {}
  new Function('exports', 'require', transpile(fs.readFileSync(path.join(__dirname, rel), 'utf8')))(exp, require)
  return exp
}
const redes = loadLib('../lib/redesSociais.ts')
const apiError = loadLib('../lib/apiError.ts')

// ---------------------------------------------------------------- higiene estática
assert.match(script, /permission: \{ resource: 'redes_sociais', action: 'view' \}/, 'permission middleware')
assert.ok(!/type="password"/.test(tpl), 'senha nunca é <input type="password"> (gerenciador de senhas)')
assert.match(tpl, /data-1p-ignore/, 'campo senha ignora 1Password')
assert.match(tpl, /WebkitTextSecurity/, 'campo senha mascarado por CSS')
assert.match(script, /onUnmounted\(/, 'zera senha ao sair da página')
assert.match(script, /30_000/, 'auto-oculta em 30 s')
assert.match(script, /body: \{ senha: null \}/, '"voltar a herdar" manda senha:null explícito')
assert.match(script, /body: \{ sac_senha: null \}/, '"limpar senha" da marca manda sac_senha:null explícito')
// senha_enc é coluna do banco — o front nunca lê/manda esse campo (só em
// comentário explicando que ele não vem).
assert.ok(!/senha_enc/.test(tpl) && !/[.'"]senha_enc\b|senha_enc\s*:/.test(script), 'front nunca usa senha_enc')
// v3: `verificado` (bool) não existe mais — é verificacao_status.
assert.ok(!/\.verificado\b/.test(script + tpl) && !/verificado:\s*(true|false)/.test(script), 'sem o bool `verificado` (v2)')
assert.match(script, /verificacao_status/, 'usa verificacao_status')
// Colunas da marca: só via /api/redes-sociais/marca/{id} (redes_sociais:edit).
// /api/marcas/ só aparece pra miniatura do logo (GET imagem).
assert.ok(!/api\/marcas\/\$\{[^`]*`,\s*\{\s*method/.test(script), 'nenhum PATCH/POST/DELETE em /api/marcas')
assert.match(script, /\/api\/redes-sociais\/marca\/\$\{[^}]+\}`,\s*\{\s*method: 'PATCH'/, 'PATCH /api/redes-sociais/marca/{id}')
assert.match(script, /\/api\/redes-sociais\/marca\/\$\{[^}]+\}\/sac-senha/, 'GET .../marca/{id}/sac-senha')
assert.match(script, /api\/marcas\/\$\{[^}]+\}\/logo\?v=/, 'miniatura do logo com ?v=updated_at')
// A senha nunca vai parar num title/tooltip.
// (referência ao VALOR: .get(), `${…}` ou ramo de ternário — `!form.senha ?` como condição é ok.)
assert.ok(
  !/:title="[^"]*(revealedSenhas\.get\(|\$\{\s*(form\.senha|senhaMarcaValor)\b|[?:]\s*(form\.senha|senhaMarcaValor)\s*(?=[):"]|$))/.test(tpl),
  'senha nunca em title',
)
// Só-view: bloco olho/copiar do modal atrás de canEdit; Salvar idem.
assert.match(tpl, /<div v-if="canEdit" class="absolute right-2/, 'olho/copiar do modal só com edit')
assert.match(tpl, /<Button v-if="canEdit" :disabled="saving \|\| !form\.marca_id" @click="saveRede">/, 'Salvar só com edit')
// Guia "Como verificar" com links seguros.
assert.match(tpl, /v-for="g in VERIFICACAO_GUIA"/, 'modal renderiza VERIFICACAO_GUIA')
assert.match(tpl, /target="_blank"\s+rel="noopener"/, 'links do guia com rel=noopener')
// Pontinho do Zap abre o select sem disparar a edição do fone (@click.stop).
assert.match(tpl, /@click\.stop="startEdit\(row, 'whatsapp_verificacao_status'\)"/, 'pontinho do Zap com @click.stop')
// Ordem das colunas = planilha: Marca | Fone/WhatsApp | Usuário | Senha | plataformas | Função/Tipo/Obs.
{
  const thead = tpl.slice(tpl.indexOf('<thead'), tpl.indexOf('</thead>'))
  const pos = ['>Marca<', 'Fone / WhatsApp', 'Usuário', '>Senha<', 'v-for="p in PLATAFORMAS"', 'v-for="col in MARCA_TEXT_COLS"']
    .map((s) => thead.indexOf(s))
  assert.ok(pos.every((p) => p >= 0), `cabeçalhos presentes: ${pos}`)
  assert.deepEqual([...pos].sort((a, b) => a - b), pos, 'ordem das colunas da planilha')
  assert.ok(!/Ações/.test(thead), 'sem coluna Ações')
  assert.match(thead, /sticky left-0/, 'Marca sticky')
}

// ---------------------------------------------------------------- helpers puros
const start = script.indexOf('// ---------- helpers puros')
const end = script.indexOf('// ---------- fim helpers puros')
assert.ok(start > 0 && end > start, 'marcadores dos helpers puros presentes')
const helpersJs = transpile(script.slice(start, end), ts.ModuleKind.ESNext)
const H = new Function('PLATAFORMA_LABELS', 'VERIFICACAO_LABELS', 'fmtFone', helpersJs + `
return { montaBody, chipTitle, rowMatches, sincronizaEfetivos, dotClass, VERIFICACAO_CURTO, verifLabel, senhaHintTexto };
`)(redes.PLATAFORMA_LABELS, redes.VERIFICACAO_LABELS, redes.fmtFone)

const SENHA_FALSA = 'senha-falsa-teste-123'
function form(over = {}) {
  return {
    marca_id: 'm1', plataforma: 'instagram', conta: '  loja.teste ', usuario: '', url: '',
    email: ' SAC@teste.com ', fone: '', senha: '', verificacao_status: 'em_andamento',
    verificacao_obs: '  protocolo 123 ', ativo: false, obs: '  ',
    ...over,
  }
}

// montaBody — create: sem senha e sem ativo; textos com trim e vazio → null
{
  const b = H.montaBody(form(), 'create', null)
  assert.ok(!('senha' in b), 'create sem senha digitada não manda a chave')
  assert.ok(!('ativo' in b), 'create não manda ativo (nasce ativa)')
  assert.equal(b.conta, 'loja.teste')
  assert.equal(b.email, 'SAC@teste.com')
  assert.equal(b.usuario, null)
  assert.equal(b.obs, null)
  assert.equal(b.verificacao_status, 'em_andamento')
  assert.equal(b.verificacao_obs, 'protocolo 123')
  assert.equal(b.plataforma, 'instagram')
  assert.ok(!('verificado' in b))
  assert.equal(H.montaBody(form({ verificacao_obs: ' ' }), 'create', null).verificacao_obs, null)
}
// create com senha digitada → vai como está (sem trim)
{
  const b = H.montaBody(form({ senha: SENHA_FALSA + ' ' }), 'create', null)
  assert.equal(b.senha, SENHA_FALSA + ' ')
}
// edit: ativo vai; senha revelada e não alterada NÃO é reenviada
{
  const b = H.montaBody(form({ senha: SENHA_FALSA }), 'edit', SENHA_FALSA)
  assert.equal(b.ativo, false)
  assert.ok(!('senha' in b), 'senha revelada igual à salva não é reenviada')
}
// edit: senha alterada por cima da revelada → vai
{
  const b = H.montaBody(form({ senha: SENHA_FALSA + 'x' }), 'edit', SENHA_FALSA)
  assert.equal(b.senha, SENHA_FALSA + 'x')
}
// edit: campo vazio → chave ausente (backend mantém); nunca senha:null aqui
{
  const b = H.montaBody(form({ senha: '' }), 'edit', null)
  assert.ok(!('senha' in b))
  assert.ok(!Object.values(b).includes(SENHA_FALSA))
}

// chipTitle: contato EFETIVO ("da marca" quando herdado), verificação, origem da senha — nunca a senha
function redeOut(over = {}) {
  return {
    id: 'r1', marca_id: 'm1', marca_nome: 'Loja Teste', plataforma: 'tiktok', conta: 'loja.teste',
    usuario: 'login.teste', url: null, email: null, fone: null, has_senha: false,
    email_efetivo: 'sac@teste.com', fone_efetivo: '11999990000', has_senha_efetiva: true, senha_origem: 'marca',
    verificacao_status: 'em_andamento', verificacao_obs: 'protocolo 9', obs: 'obs fake', ativo: false,
    created_at: '', updated_at: '',
    ...over,
  }
}
{
  const t = H.chipTitle(redeOut())
  assert.match(t, /^TikTok · /)
  assert.match(t, /login: login\.teste/)
  assert.match(t, /sac@teste\.com \(da marca\)/, 'e-mail herdado marcado')
  assert.match(t, /fone: \(11\) 99999-0000 \(da marca\)/, 'fone herdado formatado e marcado')
  assert.match(t, /em andamento/)
  assert.match(t, /protocolo 9/)
  assert.match(t, /inativa/)
  assert.match(t, /senha da marca/)
  assert.match(t, /obs fake/)
  assert.ok(!t.includes(SENHA_FALSA))
  // próprios da conta: sem "(da marca)"; senha própria; verificado
  const t2 = H.chipTitle(redeOut({ email: 'ig@teste.com', email_efetivo: 'ig@teste.com', fone: '11', fone_efetivo: '11', has_senha: true, senha_origem: 'conta', verificacao_status: 'verificado', verificacao_obs: null }))
  assert.match(t2, / · ig@teste\.com · /)
  assert.ok(!t2.includes('(da marca)'))
  assert.match(t2, /senha própria/)
  assert.match(t2, /verificado/)
  // não solicitado não aparece; sem senha nenhuma não fala de senha
  const t3 = H.chipTitle(redeOut({ verificacao_status: 'nao_solicitado', verificacao_obs: null, senha_origem: null, has_senha_efetiva: false }))
  assert.ok(!t3.includes('não solicitado') && !t3.includes('senha'))
  // plataforma fora do lib (removida do enum) cai no valor cru, sem quebrar
  assert.match(H.chipTitle(redeOut({ plataforma: 'orkut', usuario: null, email_efetivo: null, fone_efetivo: null, verificacao_status: 'nao_solicitado', verificacao_obs: null, ativo: true, senha_origem: null, obs: null })), /^orkut$/)
}

// rowMatches: marca (nome/slug/e-mail SAC/fone) ou qualquer conta/e-mail da linha; "@" inicial ignorado
{
  const row = {
    marca: { id: 'm1', nome: 'Loja Teste', slug: 'loja-teste', sac_email: 'sac@teste.com', sac_fone: '11999990000' },
    cells: {
      instagram: [{ conta: 'ig.loja', email: null, email_efetivo: 'sac@teste.com' }],
      tiktok: [{ conta: null, email: 'tt@outro.com', email_efetivo: 'tt@outro.com' }],
      youtube: [],
    },
  }
  assert.equal(H.rowMatches(row, 'loja teste'), true, 'nome')
  assert.equal(H.rowMatches(row, 'loja-teste'), true, 'slug')
  assert.equal(H.rowMatches(row, 'ig.loja'), true, 'conta')
  assert.equal(H.rowMatches(row, '@ig.loja'), true, 'busca com @ acha a conta gravada sem @')
  assert.equal(H.rowMatches(row, '@@IG.LOJA'), true, 'vários @ e caixa')
  assert.equal(H.rowMatches(row, 'sac@teste'), true, 'e-mail SAC da marca')
  assert.equal(H.rowMatches(row, 'tt@outro'), true, 'e-mail próprio da conta')
  assert.equal(H.rowMatches(row, '119999'), true, 'fone da marca')
  assert.equal(H.rowMatches(row, ''), true, 'vazio = tudo')
  assert.equal(H.rowMatches(row, 'nada-disso'), false)
}

// sincronizaEfetivos: mesma regra de routers/redes_sociais.py::rede_out
{
  const row = {
    marca: { sac_email: 'sac@m.x', sac_fone: '1199', has_sac_senha: true },
    cells: {
      instagram: [
        { email: null, fone: null, has_senha: false, email_efetivo: 'velho@x', fone_efetivo: '0', senha_origem: null, has_senha_efetiva: false },
        { email: 'ig@m.x', fone: '2288', has_senha: true, email_efetivo: null, fone_efetivo: null, senha_origem: null, has_senha_efetiva: false },
      ],
      youtube: [],
    },
  }
  H.sincronizaEfetivos(row)
  const [herda, propria] = row.cells.instagram
  assert.deepEqual([herda.email_efetivo, herda.fone_efetivo, herda.senha_origem, herda.has_senha_efetiva], ['sac@m.x', '1199', 'marca', true])
  assert.deepEqual([propria.email_efetivo, propria.fone_efetivo, propria.senha_origem, propria.has_senha_efetiva], ['ig@m.x', '2288', 'conta', true])
  row.marca.has_sac_senha = false
  row.marca.sac_email = null
  H.sincronizaEfetivos(row)
  assert.deepEqual([herda.email_efetivo, herda.senha_origem, herda.has_senha_efetiva], [null, null, false])
  assert.equal(propria.senha_origem, 'conta', 'senha própria não depende da marca')
}

// dotClass / VERIFICACAO_CURTO / verifLabel
{
  assert.equal(H.dotClass('em_andamento'), 'bg-amber-500')
  assert.equal(H.dotClass('recusado'), 'bg-red-500')
  assert.equal(H.dotClass('verificado'), 'bg-emerald-500')
  assert.equal(H.dotClass('nao_solicitado'), H.dotClass(null))
  assert.equal(H.dotClass('zzz'), H.dotClass('nao_solicitado'), 'status desconhecido cai no apagado')
  assert.equal(H.VERIFICACAO_CURTO.nao_solicitado, '', 'não solicitado: só o pontinho, sem texto')
  assert.ok(Object.values(H.VERIFICACAO_CURTO).every((s) => s.length <= 6), 'texto curto (cabe em 130px)')
  assert.equal(H.verifLabel('em_andamento'), 'em andamento')
  assert.equal(H.verifLabel(null), 'não solicitado')
  assert.equal(H.verifLabel('zzz'), 'zzz')
}

// senhaHintTexto: dirigida por senha_origem (spec v3.1)
{
  const base = { modo: 'edit', origem: 'marca', digitada: false, marcaTemSenha: true, revelada: null }
  assert.match(H.senhaHintTexto(base), /herdada da marca — digite para usar uma senha própria/)
  assert.match(H.senhaHintTexto({ ...base, origem: 'conta' }), /^senha própria desta conta$/)
  assert.match(H.senhaHintTexto({ ...base, origem: null }), /sem senha — nem na conta nem na marca/)
  assert.match(H.senhaHintTexto({ ...base, revelada: 'marca' }), /mostrando a senha da marca$/)
  assert.match(H.senhaHintTexto({ ...base, origem: 'conta', revelada: 'conta' }), /mostrando a senha da conta$/)
  assert.match(H.senhaHintTexto({ ...base, digitada: true }), /grava ao Salvar/)
  assert.equal(H.senhaHintTexto({ ...base, modo: 'create' }), 'em branco herda a senha da marca')
  assert.equal(H.senhaHintTexto({ ...base, modo: 'create', marcaTemSenha: false }), 'opcional')
}

// ---------------------------------------------------------------- script setup
// Tira os imports (resolvidos por parâmetro abaixo) e mantém o `await load()`
// de topo — a factory é async, então o grid inicial vem do api falso.
const pageScript = script.replace(/^import[\s\S]*?from\s+'[^']+'\s*$/gm, '')
const exportsForTest = `return {
  grid, loading, error, search, showGuia, colspan, load, filteredRows, marcasOpcoes, logoSrc, rowDaMarca,
  cellEdit, editValue, isEditing, isFlashed, inlineClass, startEdit, cancelEdit, commitEdit, zapTitle, MARCA_TEXT_COLS,
  revealed, revealedSenhas, toggleReveal, copySacSenha, copiedId, clearRevealed,
  senhaMarca, senhaMarcaValor, senhaMarcaVisible, senhaMarcaErr, openSenhaMarca, closeSenhaMarca,
  toggleSenhaMarca, saveSenhaMarca, limparSenhaMarca,
  modal, form, saving, modalErr, modo, marcaDoForm, emailPlaceholder, fonePlaceholder, urlSugerida, urlAbrir,
  openCreate, openEdit, closeModal, saveRede, removeRede,
  senhaVisible, senhaSalva, senhaOrigemRevelada, toggleSenha, copySenha, voltarHerdarSenha, senhaHint, senhaPlaceholder,
}`
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const factory = new AsyncFunction(
  'ref', 'computed', 'watch', 'nextTick', 'onUnmounted',
  'definePageMeta', 'useApi', 'useCan',
  'setTimeout', 'clearTimeout', 'navigator', 'confirm',
  'TABS_CADASTROS', 'apiErrMsg', 'MARCAS_ERROS',
  'PLATAFORMAS', 'PLATAFORMA_LABELS', 'VERIFICACAO_GUIA', 'VERIFICACAO_LABELS', 'VERIFICACAO_STATUS', 'fmtFone', 'perfilUrl',
  transpile(pageScript, ts.ModuleKind.ESNext) + '\n' + exportsForTest,
)

const FAKE = { senhaConta: 'senha-conta-teste-123', senhaMarca: 'senha-marca-teste-456' }
function marcaRef(over = {}) {
  return {
    id: 'm1', nome: 'Poofy', slug: 'poofy', ativo: true, classe: 'celular', funcao: 'locação', tipo: 'cel',
    obs: null, sac_fone: '11987654321', sac_email: 'sac@poofy.x', has_sac_senha: true,
    whatsapp_verificacao_status: 'nao_solicitado', whatsapp_verificacao_obs: null,
    has_logo: true, updated_at: '2026-09-15T10:00:00+00:00', ...over,
  }
}
function rede(over = {}) {
  return {
    id: 'r1', marca_id: 'm1', marca_nome: 'Poofy', plataforma: 'instagram', conta: 'poofy', usuario: null,
    url: null, email: null, fone: null, has_senha: false, email_efetivo: 'sac@poofy.x', fone_efetivo: '11987654321',
    has_senha_efetiva: true, senha_origem: 'marca', verificacao_status: 'nao_solicitado', verificacao_obs: null,
    obs: null, ativo: true, created_at: 't', updated_at: 't', ...over,
  }
}
const digits = (s) => (s == null ? null : String(s).replace(/\D/g, '') || null)
function gridOf(marcas, contas) {
  return {
    plataformas: [...redes.PLATAFORMAS],
    rows: marcas.map((m) => ({
      marca: { ...m },
      cells: Object.fromEntries(redes.PLATAFORMAS.map((p) => [p, contas.filter((r) => r.marca_id === m.id && r.plataforma === p).map((r) => ({ ...r }))])),
    })),
  }
}

async function page({ canEdit = true, marcas = [marcaRef()], contas = [rede()], confirmAnswer = true, postError = null } = {}) {
  const calls = []
  const timers = []
  const clipboard = []
  const unmountHooks = []
  const confirms = []
  const api = (url, opts) => {
    calls.push({ url, opts })
    if (url === '/api/redes-sociais/grid') return Promise.resolve(gridOf(marcas, contas))
    if (url.endsWith('/sac-senha')) return Promise.resolve({ senha: FAKE.senhaMarca, origem: 'marca' })
    if (url.endsWith('/senha')) {
      const r = contas.find((x) => `/api/redes-sociais/${x.id}/senha` === url)
      return Promise.resolve(r?.has_senha ? { senha: FAKE.senhaConta, origem: 'conta' } : { senha: FAKE.senhaMarca, origem: 'marca' })
    }
    if (opts?.method === 'DELETE') return Promise.resolve(null)
    if (opts?.method === 'PATCH' && url.startsWith('/api/redes-sociais/marca/')) {
      // MarcaSocialPatch → MarcaRef: fone só dígitos, senha vira has_sac_senha.
      const m = marcas.find((x) => `/api/redes-sociais/marca/${x.id}` === url)
      assert.ok(m, `marca conhecida: ${url}`)
      const { sac_senha, ...rest } = opts.body
      const upd = { ...m, ...rest }
      if ('sac_fone' in rest) upd.sac_fone = digits(rest.sac_fone)
      if ('sac_senha' in opts.body) upd.has_sac_senha = !!sac_senha
      Object.assign(m, upd)
      return Promise.resolve({ ...upd })
    }
    if (opts?.method === 'PATCH') {
      const r = contas.find((x) => `/api/redes-sociais/${x.id}` === url)
      assert.ok(r, `conta conhecida: ${url}`)
      const m = marcas.find((x) => x.id === r.marca_id)
      const { senha, ...rest } = opts.body
      const upd = { ...r, ...rest }
      if ('senha' in opts.body) upd.has_senha = !!senha
      upd.senha_origem = upd.has_senha ? 'conta' : m?.has_sac_senha ? 'marca' : null
      upd.has_senha_efetiva = upd.senha_origem !== null
      Object.assign(r, upd)
      return Promise.resolve({ ...upd })
    }
    if (opts?.method === 'POST') {
      if (postError) return Promise.reject(postError)
      return Promise.resolve(rede({ id: 'novo', ...opts.body, has_senha: !!opts.body.senha, senha: undefined }))
    }
    return Promise.reject(new Error(`api falso não conhece ${url}`))
  }
  const fakeTimeout = (fn, ms) => { timers.push({ fn, ms, cleared: false }); return timers.length }
  const fakeClear = (id) => { if (timers[id - 1]) timers[id - 1].cleared = true }
  const state = await factory(
    Vue.ref, Vue.computed, Vue.watch, Vue.nextTick, (fn) => unmountHooks.push(fn),
    () => {}, () => ({ api }), (resource, action) => Vue.ref(action === 'view' ? true : canEdit),
    fakeTimeout, fakeClear,
    { clipboard: { writeText: async (t) => { clipboard.push(t) } } }, (msg) => { confirms.push(msg); return confirmAnswer },
    [], apiError.apiErrMsg, apiError.MARCAS_ERROS,
    redes.PLATAFORMAS, redes.PLATAFORMA_LABELS, redes.VERIFICACAO_GUIA, redes.VERIFICACAO_LABELS, redes.VERIFICACAO_STATUS, redes.fmtFone, redes.perfilUrl,
  )
  return { state, calls, timers, clipboard, unmountHooks, confirms, marcas, contas }
}
const settle = () => new Promise(setImmediate)
const senhaCalls = (calls) => calls.filter((c) => /\/senha$|\/sac-senha$/.test(c.url))
const patches = (calls) => calls.filter((c) => c.opts?.method === 'PATCH')
const marcasApiCalls = (calls) => calls.filter((c) => c.url.startsWith('/api/marcas'))

async function run() {
  // Carga inicial: GET /grid, 12 colunas, nenhuma senha buscada, busca no
  // cliente (marca, @conta, e-mail SAC, fone), colunas de texto da marca.
  {
    const { state: s, calls } = await page()
    assert.equal(calls[0].url, '/api/redes-sociais/grid')
    assert.equal(senhaCalls(calls).length, 0, 'a carga inicial não pode buscar senha')
    assert.equal(s.colspan.value, 4 + redes.PLATAFORMAS.length + 3)
    assert.deepEqual(s.MARCA_TEXT_COLS.map((c) => c.key), ['funcao', 'tipo', 'obs'])
    assert.equal(s.filteredRows.value.length, 1)
    for (const q of ['poo', '@poofy', 'sac@poofy', '1198765']) {
      s.search.value = q
      assert.equal(s.filteredRows.value.length, 1, `busca "${q}"`)
    }
    s.search.value = 'zzz'
    assert.equal(s.filteredRows.value.length, 0)
    s.search.value = ''
    assert.equal(s.marcasOpcoes.value[0].label, 'Poofy')
    assert.equal(s.logoSrc(s.grid.value.rows[0].marca), '/api/marcas/m1/logo?v=2026-09-15T10%3A00%3A00%2B00%3A00')
    assert.match(s.zapTitle(s.grid.value.rows[0].marca), /não solicitado — clique para alterar/)
  }

  // Senha da marca na célula: GET .../marca/{id}/sac-senha só no clique,
  // auto-oculta em 30 s, some ao recarregar / filtrar / desmontar; copiar
  // busca sem guardar.
  {
    const { state: s, calls, timers, clipboard, unmountHooks } = await page()
    await s.toggleReveal('m1')
    assert.equal(calls.at(-1).url, '/api/redes-sociais/marca/m1/sac-senha')
    assert.equal(s.revealedSenhas.value.get('m1'), FAKE.senhaMarca)
    assert.ok(s.revealed.value.has('m1'))
    const hide = timers.find((t) => t.ms === 30_000)
    assert.ok(hide, 'auto-ocultar em 30 s')
    hide.fn()
    assert.equal(s.revealed.value.has('m1'), false)
    assert.equal(s.revealedSenhas.value.has('m1'), false)

    await s.toggleReveal('m1')
    await s.load()
    assert.equal(s.revealedSenhas.value.size, 0, 'recarregar descarta a senha revelada')
    await s.toggleReveal('m1')
    s.search.value = 'po'
    await Vue.nextTick()
    assert.equal(s.revealedSenhas.value.size, 0, 'filtrar descarta a senha revelada')
    await s.toggleReveal('m1')
    assert.equal(unmountHooks.length, 1)
    unmountHooks[0]()
    assert.equal(s.revealedSenhas.value.size, 0, 'onUnmounted descarta a senha revelada')

    const antes = senhaCalls(calls).length
    await s.copySacSenha('m1')
    assert.equal(senhaCalls(calls).length, antes + 1, 'copiar busca a senha')
    assert.deepEqual(clipboard, [FAKE.senhaMarca])
    assert.equal(s.revealedSenhas.value.size, 0, 'copiar não guarda a senha')
    assert.equal(s.copiedId.value, 'm1')
  }

  // Edição inline das colunas da MARCA: PATCH /api/redes-sociais/marca/{id}
  // (nunca /api/marcas), fone formatado no input, efetivos das contas
  // sincronizados, status do Zap NOT NULL, obs vazia → null, sem edit → nada.
  {
    const { state: s, calls } = await page()
    const row = s.grid.value.rows[0]
    await s.startEdit(row, 'sac_fone')
    assert.equal(s.editValue.value, '(11) 98765-4321', 'fone aparece formatado')
    await s.commitEdit()
    assert.equal(patches(calls).length, 0, 'sem alteração não há PATCH')
    await s.startEdit(row, 'sac_fone')
    s.editValue.value = '11 91234-5678'
    await s.commitEdit()
    assert.equal(calls.at(-1).url, '/api/redes-sociais/marca/m1')
    assert.deepEqual(calls.at(-1).opts.body, { sac_fone: '11 91234-5678' })
    assert.equal(row.marca.sac_fone, '11912345678')
    assert.equal(row.cells.instagram[0].fone_efetivo, '11912345678', 'conta que herda vê o fone novo')
    assert.ok(s.isFlashed('m1', 'sac_fone'))
    assert.equal(s.cellEdit.value, null)

    await s.startEdit(row, 'sac_email')
    s.editValue.value = 'novo@poofy.x'
    await s.commitEdit()
    assert.deepEqual(calls.at(-1).opts.body, { sac_email: 'novo@poofy.x' })
    assert.equal(row.cells.instagram[0].email_efetivo, 'novo@poofy.x')

    await s.startEdit(row, 'whatsapp_verificacao_status')
    s.editValue.value = 'em_andamento'
    await s.commitEdit()
    assert.deepEqual(calls.at(-1).opts.body, { whatsapp_verificacao_status: 'em_andamento' })
    assert.match(s.zapTitle(row.marca), /em andamento/)
    const n = patches(calls).length
    await s.startEdit(row, 'whatsapp_verificacao_status')
    s.editValue.value = ''
    await s.commitEdit()
    assert.equal(patches(calls).length, n, 'status vazio não salva (coluna NOT NULL)')

    await s.startEdit(row, 'whatsapp_verificacao_obs')
    s.editValue.value = ' protocolo 42 '
    await s.commitEdit()
    assert.deepEqual(calls.at(-1).opts.body, { whatsapp_verificacao_obs: 'protocolo 42' })

    await s.startEdit(row, 'obs')
    s.editValue.value = '  '
    await s.commitEdit()
    assert.deepEqual(calls.at(-1).opts.body, { obs: null })
    await s.startEdit(row, 'funcao')
    s.editValue.value = 'venda'
    await s.commitEdit()
    assert.deepEqual(calls.at(-1).opts.body, { funcao: 'venda' })
    assert.equal(row.marca.funcao, 'venda')

    assert.equal(marcasApiCalls(calls).length, 0, 'nada vai pra /api/marcas')
    assert.ok(patches(calls).every((c) => c.url.startsWith('/api/redes-sociais/marca/')))

    // Esc cancela sem PATCH
    const n2 = patches(calls).length
    await s.startEdit(row, 'tipo')
    s.editValue.value = 'x'
    s.cancelEdit()
    await s.commitEdit()
    assert.equal(patches(calls).length, n2)
    assert.deepEqual(s.inlineClass('m1', 'tipo', false)['cursor-pointer hover:bg-accent/30 rounded'], true)
  }

  // Só-view: nada de edição inline, mini-modal da senha nem criação.
  {
    const { state: s, calls } = await page({ canEdit: false })
    const row = s.grid.value.rows[0]
    await s.startEdit(row, 'sac_fone')
    assert.equal(s.cellEdit.value, null, 'sem edit não abre input')
    assert.equal(s.inlineClass('m1', 'sac_fone', false)['cursor-pointer hover:bg-accent/30 rounded'], false)
    s.openSenhaMarca(row.marca)
    assert.equal(s.senhaMarca.value, null)
    s.openCreate('m1', 'tiktok')
    assert.equal(s.modal.value, null)
    s.openEdit(row.cells.instagram[0])
    assert.ok(s.modal.value, 'só-view abre a conta pra ler')
    await s.toggleSenha()
    assert.equal(senhaCalls(calls).length, 0, 'só-view nunca busca senha')
    assert.equal(s.form.value.senha, '')
    await s.copySenha()
    assert.equal(senhaCalls(calls).length, 0)
    await s.saveRede()
    assert.equal(patches(calls).length, 0, 'só-view não salva')
    assert.ok(!/clique para alterar/.test(s.zapTitle(row.marca)))
  }

  // Mini-modal "Senha das redes/SAC": olho revela a salva, Salvar sem mudar
  // não manda nada, senha nova → PATCH {sac_senha}, "limpar" → {sac_senha:
  // null} (com confirm) e as contas que herdavam ficam sem senha.
  {
    const { state: s, calls, confirms } = await page()
    const row = s.grid.value.rows[0]
    s.openSenhaMarca(row.marca)
    assert.equal(s.senhaMarca.value.marca.id, 'm1')
    await s.saveSenhaMarca()
    assert.equal(patches(calls).length, 0, 'vazio não salva')
    assert.match(s.senhaMarcaErr.value, /digite a senha/)
    await s.toggleSenhaMarca()
    assert.equal(calls.at(-1).url, '/api/redes-sociais/marca/m1/sac-senha')
    assert.equal(s.senhaMarcaValor.value, FAKE.senhaMarca)
    assert.equal(s.senhaMarcaVisible.value, true)
    await s.saveSenhaMarca()
    assert.equal(patches(calls).length, 0, 'revelada e não alterada: nada a salvar')
    assert.equal(s.senhaMarca.value, null, 'fecha')

    s.openSenhaMarca(row.marca)
    assert.equal(s.senhaMarcaValor.value, '', 'reabrir começa vazio')
    s.senhaMarcaValor.value = 'nova-senha-teste'
    await s.saveSenhaMarca()
    assert.equal(calls.at(-1).url, '/api/redes-sociais/marca/m1')
    assert.deepEqual(calls.at(-1).opts.body, { sac_senha: 'nova-senha-teste' })
    assert.equal(row.marca.has_sac_senha, true)
    assert.equal(s.senhaMarca.value, null)
    assert.equal(s.senhaMarcaValor.value, '', 'plaintext zerado ao fechar')

    await s.toggleReveal('m1')
    s.openSenhaMarca(row.marca)
    await s.limparSenhaMarca()
    assert.equal(confirms.length, 1)
    assert.deepEqual(calls.at(-1).opts.body, { sac_senha: null })
    assert.equal(row.marca.has_sac_senha, false)
    assert.equal(row.cells.instagram[0].senha_origem, null, 'conta que herdava fica sem senha')
    assert.equal(row.cells.instagram[0].has_senha_efetiva, false)
    assert.equal(s.revealedSenhas.value.size, 0, 'senha revelada da marca some depois do PATCH')

    // sem senha salva, "limpar" é no-op
    s.openSenhaMarca(row.marca)
    await s.limparSenhaMarca()
    assert.equal(confirms.length, 1)
    // confirm recusado → nada
    const { state: s2, calls: c2, confirms: cf2 } = await page({ confirmAnswer: false })
    s2.openSenhaMarca(s2.grid.value.rows[0].marca)
    await s2.limparSenhaMarca()
    assert.equal(cf2.length, 1)
    assert.equal(patches(c2).length, 0)
  }

  // Modal da conta — senha herdada da marca: placeholders "herda: …", olho
  // revela a EFETIVA com origem, Salvar não reenvia a revelada, sem
  // "voltar a herdar"; timer de 30 s; fechar zera.
  {
    const { state: s, calls, timers } = await page()
    const row = s.grid.value.rows[0]
    s.openEdit(row.cells.instagram[0])
    assert.equal(s.modo.value, 'edit')
    assert.equal(s.form.value.email, '', 'só o PRÓPRIO da conta no campo')
    assert.equal(s.emailPlaceholder.value, 'herda: sac@poofy.x')
    assert.equal(s.fonePlaceholder.value, 'herda: (11) 98765-4321')
    assert.equal(s.senhaPlaceholder.value, 'herda a senha da marca')
    assert.match(s.senhaHint.value, /herdada da marca/)
    assert.equal(s.urlSugerida.value, 'https://www.instagram.com/poofy/')
    await s.toggleSenha()
    assert.equal(calls.at(-1).url, '/api/redes-sociais/r1/senha')
    assert.equal(s.form.value.senha, FAKE.senhaMarca)
    assert.equal(s.senhaOrigemRevelada.value, 'marca')
    assert.match(s.senhaHint.value, /mostrando a senha da marca/)
    assert.ok(timers.some((t) => t.ms === 30_000), 'modal auto-oculta em 30 s')
    await s.voltarHerdarSenha()
    assert.equal(patches(calls).length, 0, 'herdada: não há o que limpar')
    s.form.value.verificacao_status = 'em_andamento'
    s.form.value.verificacao_obs = 'protocolo 7'
    await s.saveRede()
    const p = patches(calls).at(-1)
    assert.equal(p.url, '/api/redes-sociais/r1')
    assert.ok(!('senha' in p.opts.body), 'senha revelada não é reenviada')
    assert.equal(p.opts.body.verificacao_status, 'em_andamento')
    assert.equal(p.opts.body.verificacao_obs, 'protocolo 7')
    assert.equal(p.opts.body.ativo, true)
    assert.equal(p.opts.body.email, null, 'vazio = continua herdando')
    assert.equal(s.modal.value, null, 'fecha e recarrega')
    assert.equal(calls.at(-1).url, '/api/redes-sociais/grid')
    assert.equal(s.form.value.senha, '', 'plaintext zerado')

    // senha digitada por cima da herdada → vira própria
    s.openEdit(s.grid.value.rows[0].cells.instagram[0])
    s.form.value.senha = 'propria-teste'
    assert.match(s.senhaHint.value, /grava ao Salvar/)
    await s.saveRede()
    assert.equal(patches(calls).at(-1).opts.body.senha, 'propria-teste')
    s.closeModal()
    assert.equal(s.form.value.senha, '')
  }

  // Modal da conta — senha PRÓPRIA: "voltar a herdar a da marca" manda
  // senha:null com confirm e atualiza a conta no grid.
  {
    const { state: s, calls, confirms } = await page({ contas: [rede({ has_senha: true, senha_origem: 'conta', email: 'ig@poofy.x', email_efetivo: 'ig@poofy.x' })] })
    const r = s.grid.value.rows[0].cells.instagram[0]
    s.openEdit(r)
    assert.equal(s.form.value.email, 'ig@poofy.x')
    assert.equal(s.senhaPlaceholder.value, 'branco = manter a própria')
    assert.match(s.senhaHint.value, /^senha própria desta conta$/)
    await s.toggleSenha()
    assert.equal(s.form.value.senha, FAKE.senhaConta)
    assert.equal(s.senhaOrigemRevelada.value, 'conta')
    await s.voltarHerdarSenha()
    assert.equal(confirms.length, 1)
    assert.deepEqual(calls.at(-1).opts.body, { senha: null })
    assert.equal(s.modal.value.rede.senha_origem, 'marca', 'volta a herdar')
    assert.equal(s.grid.value.rows[0].cells.instagram[0].senha_origem, 'marca', 'grid atualizado sem recarregar')
    assert.equal(s.form.value.senha, '')
    assert.equal(s.senhaSalva.value, null)
    assert.match(s.senhaHint.value, /herdada da marca/)
  }

  // Criar: "+" da célula pré-seleciona marca e plataforma; POST sem ativo,
  // com verificacao_status default; hint "em branco herda". Excluir: DELETE
  // com confirm.
  {
    const { state: s, calls, confirms } = await page()
    s.openCreate('m1', 'tiktok')
    assert.equal(s.modo.value, 'create')
    assert.equal(s.form.value.plataforma, 'tiktok')
    assert.equal(s.marcaDoForm.value.id, 'm1')
    assert.equal(s.senhaHint.value, 'em branco herda a senha da marca')
    assert.equal(s.senhaPlaceholder.value, 'em branco herda a senha da marca')
    s.form.value.conta = '@poofy.tt'
    await s.saveRede()
    const post = calls.find((c) => c.opts?.method === 'POST')
    assert.equal(post.url, '/api/redes-sociais')
    assert.ok(!('ativo' in post.opts.body) && !('senha' in post.opts.body))
    assert.equal(post.opts.body.verificacao_status, 'nao_solicitado')
    assert.equal(post.opts.body.marca_id, 'm1')
    assert.equal(s.modal.value, null)

    s.openCreate()
    await s.saveRede()
    assert.equal(s.modalErr.value, 'escolha a marca')

    s.openEdit(s.grid.value.rows[0].cells.instagram[0])
    await s.removeRede()
    assert.equal(confirms.length, 1)
    assert.match(confirms[0], /@poofy \(Instagram\) de Poofy/)
    assert.equal(calls.find((c) => c.opts?.method === 'DELETE').url, '/api/redes-sociais/r1')
    assert.equal(s.modal.value, null)
  }

  // Erro da API vira mensagem legível (apiErrMsg + MARCAS_ERROS).
  {
    const { state: s } = await page({ postError: { data: { detail: { code: 'rede_social_conta_conflict' } } } })
    s.openCreate('m1', 'youtube')
    await s.saveRede()
    assert.equal(s.modalErr.value, 'Essa conta já está cadastrada nessa plataforma')
    assert.ok(s.modal.value, 'modal fica aberto com o erro')
  }
}

run().then(() => {
  console.log('PASS: SFC parse + template compile; higiene de senha; helpers puros; script setup com api falso (grid, inline PATCH /marca, senha da marca, modal da conta, guia)')
}).catch((e) => {
  console.error(e)
  process.exit(1)
})
