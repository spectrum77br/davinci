// Run from apps/web: node tests/marcas-sfc.cjs
// Cadastros › Marcas (15/09/2026): parse + compileTemplate do SFC e, no molde
// de certificacoes-anatel.cjs, executa o <script setup> com api/timers falsos
// pra travar as regras de higiene da senha (SPEC v2), o corpo do POST/PATCH e,
// na v3, empresa da assinatura / site / logo (checagem de tamanho no cliente,
// PUT multipart, DELETE). Só dados FALSOS aqui — nunca senha real.
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
const filename = path.join(__dirname, '../pages/marcas.vue')
const sfcSource = fs.readFileSync(filename, 'utf8')
const { descriptor, errors } = parse(sfcSource, { filename })
assert.deepEqual(errors, [])
const compiled = compileTemplate({ source: descriptor.template.content, filename, id: 'marcas-check' })
assert.deepEqual(compiled.errors, [])
// O render compilado precisa ser JS válido (imports do vue resolvem via require).
new Function('exports', 'require', transpile(compiled.code))({}, require)

// Peças do template que a spec v3 exige (o ui <Input> não faz type=file, então
// é <input> nativo escondido; miniatura só com has_logo; nota no "Nova marca").
{
  const tpl = descriptor.template.content
  assert.match(tpl, /<input\s+ref="logoInputRef"\s+type="file"/, 'input file nativo escondido')
  assert.match(tpl, /:accept="LOGO_ACCEPT"/)
  assert.match(tpl, /v-if="row\.has_logo"[\s\S]{0,80}:src="logoSrc\(row\)"/, 'miniatura só quando has_logo')
  assert.match(tpl, /salve a marca para enviar o logo/, 'nota no Nova marca')
  assert.match(tpl, /— nenhuma —/, 'opção vazia do select de empresas')
  assert.match(tpl, /editar em Redes Sociais/, 'link pro dono do sac_*')
  assert.doesNotMatch(tpl, /sac_senha|whatsapp_verificacao/, 'senha das redes e verificação do Zap NÃO são desta aba')
}

// ---------------------------------------------------------------- libs reais
function loadLib(rel) {
  const exp = {}
  new Function('exports', 'require', transpile(fs.readFileSync(path.join(__dirname, rel), 'utf8')))(exp, require)
  return exp
}
const redes = loadLib('../lib/redesSociais.ts')
const apiError = loadLib('../lib/apiError.ts')
const dateLib = loadLib('../lib/date.ts')

// ---------------------------------------------------------------- script setup
// Tira os imports (resolvidos por parâmetro abaixo) e mantém o `await load()`
// de topo — a factory é async, então a lista inicial vem do api falso.
const pageScript = descriptor.scriptSetup.content.replace(/^import[\s\S]*?from\s+'[^']+'\s*$/gm, '')
const exportsForTest = `return {
  items, loading, error, search, showInativas, filtered, load, toggleInativas,
  editing, editValue, startEdit, cancelEdit, commitEdit, isFlashed,
  toggleAtivo, remove,
  revealed, revealedPasswords, toggleReveal, copySenha, clearRevealed, copiedId,
  showModal, modalEditing, form, saveErr, senhaVisible, limparSenha, senhaRevelada,
  openNew, openEdit, closeModal, toggleSenha, marcarLimparSenha, buildBody, saveModal,
  fmtDate, daysUntil, validadeClass, validadeVencida, inpiPill, inpiLabel, COLS,
  empresas, empresasLoaded, empresasLoading, ensureEmpresas, empresaLabel,
  logoSrc, siteValido, LOGO_MAX_BYTES, LOGO_TIPOS, LOGO_ACCEPT, checkLogoFile,
  logoBusy, logoErr, uploadLogo, removeLogo, onLogoChange, pickLogo, logoInputRef,
}`
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const factory = new AsyncFunction(
  'ref', 'computed', 'reactive', 'watch', 'nextTick', 'onUnmounted',
  'definePageMeta', 'useApi', 'useCan',
  'setTimeout', 'clearTimeout', 'navigator', 'confirm',
  'TABS_CADASTROS', 'apiErrMsg', 'MARCAS_ERROS', 'isoToday',
  'INPI_STATUS', 'INPI_STATUS_LABELS', 'INPI_STATUS_PILL', 'fmtFone',
  transpile(pageScript, ts.ModuleKind.ESNext) + '\n' + exportsForTest,
)

const FAKE = { senha: 'senha-de-teste-123' }
function marca(over = {}) {
  return {
    id: 'm1', nome: 'Poofy', slug: 'poofy', inpi_status: 'aguardando', usuario: 'poofy@x',
    has_senha: true, email: 'sac@poofy.x', dominio_br: 'poofy.com.br', dominio: 'poofy.com',
    dono_dominio: 'omar', dominio_validade: '2034-07-04', classe: 'celular', funcao: 'locação',
    tipo: 'cel', obs: null, ativo: true,
    sac_fone: '11987654321', sac_email: 'sac@poofy.x', has_sac_senha: true,
    whatsapp_verificacao_status: 'nao_solicitado', whatsapp_verificacao_obs: null,
    company_id: 'c1', empresa_razao_social: 'Poofy Comércio LTDA', site: 'https://poofy.com.br',
    has_logo: false, created_at: 't', updated_at: '2026-09-15T10:00:00+00:00', ...over,
  }
}
const EMPRESAS = [
  { id: 'c1', apelido: 'POOFY', razao_social: 'Poofy Comércio LTDA', cnpj: '00000000000191' },
  { id: 'c2', apelido: 'LOCAGIL', razao_social: 'Locagil Locações LTDA', cnpj: null },
]

async function page({ canEdit = true, list = [marca()], confirmAnswer = true, postError = null, logoError = null } = {}) {
  const calls = []
  const timers = []
  const clipboard = []
  const unmountHooks = []
  const confirms = []
  let logoVersao = 0
  const api = (url, opts) => {
    calls.push({ url, opts })
    if (url === '/api/marcas/empresas') return Promise.resolve(EMPRESAS.map((x) => ({ ...x })))
    if (url.endsWith('/senha')) return Promise.resolve({ senha: FAKE.senha })
    if (url.endsWith('/logo')) {
      // PUT/DELETE /logo devolvem o MarcaOut (has_logo + updated_at novo).
      if (logoError) return Promise.reject(logoError)
      const row = list.find((x) => `/api/marcas/${x.id}/logo` === url) || marca()
      logoVersao += 1
      return Promise.resolve({ ...row, has_logo: opts?.method === 'PUT', updated_at: `v${logoVersao}` })
    }
    if (opts?.method === 'DELETE') return Promise.resolve(null)
    if (opts?.method === 'PATCH') {
      const row = list.find((x) => `/api/marcas/${x.id}` === url) || marca()
      return Promise.resolve({ ...row, ...opts.body, senha: undefined, has_senha: opts.body.senha === null ? false : (opts.body.senha ? true : row.has_senha) })
    }
    if (opts?.method === 'POST') {
      if (postError) return Promise.reject(postError)
      return Promise.resolve(marca({ id: 'novo', nome: opts.body.nome, has_senha: !!opts.body.senha }))
    }
    return Promise.resolve(list.map((x) => ({ ...x })))
  }
  const fakeTimeout = (fn, ms) => { timers.push({ fn, ms, cleared: false }); return timers.length }
  const fakeClear = (id) => { if (timers[id - 1]) timers[id - 1].cleared = true }
  const state = await factory(
    Vue.ref, Vue.computed, Vue.reactive, Vue.watch, Vue.nextTick, (fn) => unmountHooks.push(fn),
    () => {}, () => ({ api }), () => Vue.ref(canEdit),
    fakeTimeout, fakeClear,
    { clipboard: { writeText: async (t) => { clipboard.push(t) } } }, (msg) => { confirms.push(msg); return confirmAnswer },
    [], apiError.apiErrMsg, apiError.MARCAS_ERROS, dateLib.isoToday,
    redes.INPI_STATUS, redes.INPI_STATUS_LABELS, redes.INPI_STATUS_PILL, redes.fmtFone,
  )
  return { state, calls, timers, clipboard, unmountHooks, confirms }
}
const settle = () => new Promise(setImmediate)
const senhaCalls = (calls) => calls.filter((c) => c.url.endsWith('/senha'))
const patches = (calls) => calls.filter((c) => c.opts?.method === 'PATCH')
const empresaCalls = (calls) => calls.filter((c) => c.url === '/api/marcas/empresas')
const logoCalls = (calls) => calls.filter((c) => c.url.endsWith('/logo'))
// Arquivo falso pro upload (File é global no Node 20+). Nada de logo real.
const fakePng = (bytes = 16, type = 'image/png', name = 'logo.png') => new File([new Uint8Array(bytes)], name, { type })

async function run() {
  // Carga inicial: GET ?ativo=true, 17 colunas (colspan) — Empresa e Site logo
  // depois de Marca —, busca no cliente (inclui empresa e site). A lista de
  // empresas NÃO é buscada na carga (só quando o modal abre).
  {
    const { state: s, calls } = await page()
    assert.equal(calls[0].url, '/api/marcas?ativo=true')
    assert.equal(s.items.value.length, 1)
    assert.equal(s.COLS.length, 17)
    assert.deepEqual(s.COLS.map((c) => c.label), [
      'Marca', 'Empresa', 'Site', 'INPI', 'Classe', 'Função', 'Tipo', 'Usuário', 'Senha', 'E-mail',
      'Domínio .br', 'Domínio', 'Dono', 'Validade domínio', 'Obs', 'Ativo', '',
    ])
    assert.equal(s.COLS[1].kind, 'empresa', 'Empresa é só leitura (edita no modal)')
    assert.deepEqual(s.COLS[2], { kind: 'text', key: 'site', label: 'Site', th: 'min-w-[150px]', truncate: true })
    assert.equal(empresaCalls(calls).length, 0, 'a carga inicial não busca empresas')
    s.search.value = 'com.br'
    assert.equal(s.filtered.value.length, 1)
    s.search.value = 'locações'
    assert.equal(s.filtered.value.length, 0)
    s.search.value = 'comércio'
    assert.equal(s.filtered.value.length, 1, 'busca pela razão social da empresa')
    s.search.value = 'zzz'
    assert.equal(s.filtered.value.length, 0)
    s.search.value = ''
    s.toggleInativas()
    await settle()
    assert.equal(calls.at(-1).url, '/api/marcas?ativo=false')
    assert.equal(s.showInativas.value, true)
  }

  // Senha na tabela: revela só no clique, auto-oculta em 30 s, some ao
  // recarregar / filtrar / desmontar; copiar busca sem guardar.
  {
    const { state: s, calls, timers, clipboard, unmountHooks } = await page()
    assert.equal(senhaCalls(calls).length, 0, 'a carga inicial não pode buscar senha')
    await s.toggleReveal('m1')
    assert.equal(senhaCalls(calls).length, 1)
    assert.equal(s.revealedPasswords.value.get('m1'), FAKE.senha)
    assert.ok(s.revealed.value.has('m1'))
    const hide = timers.find((t) => t.ms === 30_000)
    assert.ok(hide, 'auto-ocultar em 30 s')
    hide.fn()
    assert.equal(s.revealed.value.has('m1'), false)
    assert.equal(s.revealedPasswords.value.has('m1'), false)

    await s.toggleReveal('m1')
    await s.load()
    assert.equal(s.revealedPasswords.value.size, 0, 'recarregar descarta a senha revelada')
    await s.toggleReveal('m1')
    s.search.value = 'po'
    await Vue.nextTick()
    assert.equal(s.revealedPasswords.value.size, 0, 'filtrar descarta a senha revelada')
    await s.toggleReveal('m1')
    assert.equal(unmountHooks.length, 1)
    unmountHooks[0]()
    assert.equal(s.revealedPasswords.value.size, 0, 'onUnmounted descarta a senha revelada')
    assert.ok(timers.filter((t) => t.ms === 30_000).every((t) => t.cleared), 'timers de auto-ocultar cancelados')

    const before = senhaCalls(calls).length
    await s.copySenha('m1')
    assert.equal(senhaCalls(calls).length, before + 1, 'copiar busca a senha')
    assert.deepEqual(clipboard, [FAKE.senha])
    assert.equal(s.revealedPasswords.value.has('m1'), false, 'copiar não guarda a senha')
    assert.equal(s.copiedId.value, 'm1')
  }

  // Edição inline: nome vazio não salva; texto vazio vira null; select do INPI
  // que dispara change+blur manda UM PATCH só; site precisa de http(s);
  // sem edit não abre.
  {
    const { state: s, calls } = await page()
    const row = s.items.value[0]
    await s.startEdit(row, 'nome')
    s.editValue.value = '   '
    await s.commitEdit()
    assert.equal(patches(calls).length, 0, 'nome vazio não salva')
    assert.equal(s.editing.value, null)

    await s.startEdit(row, 'obs')
    s.editValue.value = ''
    await s.commitEdit()
    assert.equal(patches(calls).length, 0, 'sem mudança não salva')
    await s.startEdit(row, 'obs')
    s.editValue.value = 'x'
    await s.commitEdit()
    assert.deepEqual(patches(calls).at(-1).opts.body, { obs: 'x' })
    await s.startEdit(row, 'obs')
    s.editValue.value = ' '
    await s.commitEdit()
    assert.deepEqual(patches(calls).at(-1).opts.body, { obs: null }, 'vazio vira null')
    assert.ok(s.isFlashed('m1', 'obs'))

    await s.startEdit(row, 'inpi_status')
    s.editValue.value = 'registrado'
    const first = s.commitEdit()
    const second = s.commitEdit() // blur logo depois do change
    await Promise.all([first, second])
    const inpi = patches(calls).filter((c) => 'inpi_status' in c.opts.body)
    assert.equal(inpi.length, 1, 'change + blur = um PATCH')
    assert.deepEqual(inpi[0].opts.body, { inpi_status: 'registrado' })
    assert.equal(row.inpi_status, 'registrado')

    // site inline: inválido não vai pro servidor e explica; válido PATCH {site}; vazio limpa
    const n = patches(calls).length
    await s.startEdit(row, 'site')
    assert.equal(s.editValue.value, 'https://poofy.com.br', 'abre com o valor atual')
    s.editValue.value = 'poofy.com.br'
    await s.commitEdit()
    assert.equal(patches(calls).length, n, 'site sem http(s) não salva')
    assert.equal(s.error.value, 'Site precisa começar com http:// ou https://')
    assert.equal(s.editing.value, null)
    await s.startEdit(row, 'site')
    s.editValue.value = ' https://www.poofy.com.br '
    await s.commitEdit()
    assert.deepEqual(patches(calls).at(-1).opts.body, { site: 'https://www.poofy.com.br' }, 'site com strip')
    assert.equal(row.site, 'https://www.poofy.com.br')
    await s.startEdit(row, 'site')
    s.editValue.value = ''
    await s.commitEdit()
    assert.deepEqual(patches(calls).at(-1).opts.body, { site: null }, 'site vazio limpa')
    assert.ok(!patches(calls).some((c) => 'senha' in c.opts.body), 'inline nunca manda senha')
    assert.ok(!patches(calls).some((c) => 'company_id' in c.opts.body), 'empresa não é inline')
  }
  {
    const { state: s, calls } = await page({ canEdit: false })
    await s.startEdit(s.items.value[0], 'nome')
    assert.equal(s.editing.value, null, 'sem edit não abre input')
    await s.toggleAtivo(s.items.value[0])
    assert.equal(patches(calls).length, 0)
  }

  // Ativo (pill) e excluir: PATCH {ativo}, a linha sai da aba; confirm avisa
  // que as redes sociais (e os padrões de e-mail) vão junto.
  {
    const { state: s, calls } = await page()
    await s.toggleAtivo(s.items.value[0])
    assert.deepEqual(patches(calls).at(-1).opts.body, { ativo: false })
    assert.equal(s.items.value.length, 0, 'inativada sai da aba Ativas')
  }
  {
    const { state: s, calls, confirms } = await page({ confirmAnswer: false })
    await s.remove(s.items.value[0])
    assert.equal(confirms.length, 1)
    assert.match(confirms[0], /redes sociais/i, 'confirm avisa que as contas de redes sociais vão junto')
    assert.match(confirms[0], /e-mail/i, 'e os padrões de e-mail')
    assert.match(confirms[0], /Poofy/)
    assert.equal(calls.some((c) => c.opts?.method === 'DELETE'), false, 'cancelar o confirm não exclui')
    assert.equal(s.items.value.length, 1)
  }
  {
    const { state: s, calls } = await page()
    await s.remove(s.items.value[0])
    assert.equal(calls.at(-1).opts.method, 'DELETE')
    assert.equal(calls.at(-1).url, '/api/marcas/m1')
    assert.equal(s.items.value.length, 0)
  }

  // Modal: senha só entra no corpo quando digitada; "limpar senha" manda
  // null; revelar traz a salva e não a reenvia se ficou igual; fechar zera.
  // Empresas: buscadas na 1ª abertura (uma vez), texto `${apelido} — ${razao_social}`.
  {
    const { state: s, calls } = await page()
    s.openNew()
    assert.equal(s.showModal.value, true)
    await settle()
    assert.equal(empresaCalls(calls).length, 1, 'abrir o modal busca as empresas')
    assert.equal(s.empresas.value.length, 2)
    assert.equal(s.empresaLabel(s.empresas.value[0]), 'POOFY — Poofy Comércio LTDA')
    assert.equal(s.form.value.company_id, '', 'nova marca nasce sem empresa')
    assert.equal(s.form.value.site, '')
    s.form.value.nome = ' Nova '
    s.form.value.dominio_validade = ''
    s.form.value.classe = '  '
    await s.saveModal()
    const post = calls.find((c) => c.opts?.method === 'POST')
    assert.ok(post)
    assert.equal(post.opts.body.nome, 'Nova')
    assert.equal('senha' in post.opts.body, false, 'POST sem senha quando vazia')
    assert.equal('ativo' in post.opts.body, false, 'criar não manda ativo')
    assert.equal(post.opts.body.dominio_validade, null)
    assert.equal(post.opts.body.classe, null)
    assert.equal(post.opts.body.company_id, null, 'select vazio → company_id null')
    assert.equal(post.opts.body.site, null)
    assert.equal(s.showModal.value, false)
    assert.equal(s.items.value.length, 2)

    s.openNew()
    await settle()
    assert.equal(empresaCalls(calls).length, 1, 'segunda abertura não busca de novo')
    s.form.value.nome = 'Com senha'
    s.form.value.senha = 'abc 123'
    s.form.value.company_id = 'c2'
    s.form.value.site = ' https://locagil.com.br '
    await s.saveModal()
    assert.equal(calls.at(-1).opts.body.senha, 'abc 123', 'senha digitada vai sem strip')
    assert.equal(calls.at(-1).opts.body.company_id, 'c2')
    assert.equal(calls.at(-1).opts.body.site, 'https://locagil.com.br', 'site com strip')
    assert.equal(s.form.value.senha, '', 'fechar zera a senha')

    // site inválido no modal: não manda nada e explica
    const total = calls.length
    s.openNew()
    s.form.value.nome = 'Site ruim'
    s.form.value.site = 'www.x.com'
    await s.saveModal()
    assert.equal(calls.length, total, 'site inválido não chama a API')
    assert.equal(s.saveErr.value, 'Site precisa começar com http:// ou https://')
    assert.equal(s.showModal.value, true)
    s.closeModal()

    // recarregar invalida o cache de empresas
    await s.load()
    s.openNew()
    await settle()
    assert.equal(empresaCalls(calls).length, 2, 'depois de recarregar busca de novo')
    s.closeModal()
  }
  {
    const { state: s, calls } = await page()
    const row = s.items.value[0]
    s.openEdit(row)
    await settle()
    assert.equal(s.modalEditing.value.id, 'm1')
    assert.equal(s.form.value.senha, '')
    assert.equal(s.form.value.company_id, 'c1', 'editar abre com a empresa atual')
    assert.equal(s.form.value.site, 'https://poofy.com.br')
    assert.equal(senhaCalls(calls).length, 0, 'abrir o modal não busca a senha')
    s.form.value.obs = 'nova obs'
    await s.saveModal()
    let patch = patches(calls).at(-1)
    assert.equal('senha' in patch.opts.body, false, 'PATCH sem senha = mantém')
    assert.equal(patch.opts.body.ativo, true)
    assert.equal(patch.opts.body.obs, 'nova obs')
    assert.equal(patch.opts.body.company_id, 'c1')
    assert.equal(row.obs, 'nova obs')

    // limpar o select → PATCH company_id: null (desvincula)
    s.openEdit(row)
    s.form.value.company_id = ''
    await s.saveModal()
    patch = patches(calls).at(-1)
    assert.equal(patch.opts.body.company_id, null, 'limpar a empresa manda null explícito')
    assert.equal('company_id' in patch.opts.body, true)

    // revelar no modal e salvar sem mexer: não reenvia a senha
    s.openEdit(row)
    await s.toggleSenha()
    assert.equal(senhaCalls(calls).length, 1)
    assert.equal(s.form.value.senha, FAKE.senha)
    assert.equal(s.senhaVisible.value, true)
    await s.saveModal()
    patch = patches(calls).at(-1)
    assert.equal('senha' in patch.opts.body, false, 'senha revelada e intocada não é reenviada')
    assert.equal(s.form.value.senha, '')
    assert.equal(s.senhaRevelada.value, null)

    // revelar, trocar, salvar: manda a nova
    s.openEdit(row)
    await s.toggleSenha()
    s.form.value.senha = 'outra'
    await s.saveModal()
    assert.equal(patches(calls).at(-1).opts.body.senha, 'outra')

    // limpar senha → senha: null; digitar depois de limpar vence
    s.openEdit(row)
    s.marcarLimparSenha()
    assert.equal(s.limparSenha.value, true)
    await s.saveModal()
    assert.equal(patches(calls).at(-1).opts.body.senha, null, 'limpar senha manda null')
    assert.equal(s.limparSenha.value, false, 'fechar zera o flag')
    s.openEdit(row)
    s.marcarLimparSenha()
    s.form.value.senha = 'nova'
    await s.saveModal()
    assert.equal(patches(calls).at(-1).opts.body.senha, 'nova')

    // ativo desmarcado no modal: PATCH ativo=false e a linha sai da aba Ativas
    s.openEdit(row)
    s.form.value.ativo = false
    await s.saveModal()
    assert.equal(patches(calls).at(-1).opts.body.ativo, false)
    assert.equal(s.items.value.length, 0)

    // fechar sem salvar zera a senha
    s.openNew()
    s.form.value.senha = 'tmp'
    s.closeModal()
    assert.equal(s.form.value.senha, '')
    assert.equal(s.senhaVisible.value, false)
  }

  // buildBody puro: empresa/site/senha nas quatro combinações.
  {
    const { state: s } = await page()
    const f = { ...s.form.value, nome: 'X', company_id: '', site: '  ', senha: '' }
    const novo = s.buildBody(f, { editing: false, limpar: false, senhaSalva: null })
    assert.equal(novo.company_id, null)
    assert.equal(novo.site, null)
    assert.equal('senha' in novo, false)
    assert.equal('ativo' in novo, false)
    const edit = s.buildBody({ ...f, company_id: 'c2', site: ' https://x.y ', senha: 's' }, { editing: true, limpar: true, senhaSalva: 's' })
    assert.equal(edit.company_id, 'c2')
    assert.equal(edit.site, 'https://x.y')
    assert.equal('senha' in edit, false, 'senha igual à revelada não vai, mesmo com limpar marcado')
    assert.equal(edit.ativo, true)
    const limpa = s.buildBody({ ...f, senha: '' }, { editing: true, limpar: true, senhaSalva: null })
    assert.equal(limpa.senha, null)
    assert.equal(s.siteValido('https://a.b'), true)
    assert.equal(s.siteValido('HTTP://a.b'), true)
    assert.equal(s.siteValido('  https://a.b  '), true)
    assert.equal(s.siteValido('a.b'), false)
    assert.equal(s.siteValido('https://'), false)
    assert.equal(s.siteValido('ftp://a.b'), false)
  }

  // Logo: miniatura com cache-buster; checagem de tamanho/tipo no cliente
  // ANTES do PUT; PUT multipart campo "file" na hora de escolher; DELETE
  // com confirm; "Nova marca" (sem id) não sobe nada.
  {
    const { state: s, calls, confirms } = await page()
    assert.equal(s.logoSrc({ id: 'm1', updated_at: '2026-09-15T10:00:00+00:00' }), '/api/marcas/m1/logo?v=2026-09-15T10%3A00%3A00%2B00%3A00')
    assert.equal(s.LOGO_MAX_BYTES, 1_048_576)
    assert.deepEqual(s.LOGO_TIPOS, ['image/png', 'image/jpeg', 'image/gif'], 'sem webp/svg — igual ao backend')
    assert.equal(s.LOGO_ACCEPT, 'image/png,image/jpeg,image/gif')
    assert.equal(s.checkLogoFile({ size: 1_048_576, type: 'image/png' }), null, 'exatamente 1 MB passa')
    assert.equal(s.checkLogoFile({ size: 1_048_577, type: 'image/png' }), 'Logo muito grande — envie uma imagem menor (até 1 MB; depois de reduzida precisa caber em 300 KB)')
    assert.equal(s.checkLogoFile({ size: 10, type: 'text/plain' }), apiError.MARCAS_ERROS.logo_tipo_invalido)
    assert.equal(s.checkLogoFile({ size: 10, type: 'image/svg+xml' }), apiError.MARCAS_ERROS.logo_tipo_invalido)
    assert.equal(s.checkLogoFile({ size: 10, type: '' }), null, 'tipo desconhecido: deixa o backend decidir pelos magic bytes')
    assert.equal(s.checkLogoFile({ size: 10, type: 'image/jpeg' }), null)
    assert.equal(s.checkLogoFile({ size: 10, type: 'image/gif' }), null)

    // Nova marca: sem id, nada sobe
    s.openNew()
    await s.uploadLogo(fakePng())
    assert.equal(logoCalls(calls).length, 0, 'sem marca salva não há PUT')
    s.closeModal()

    // Editar: arquivo grande barra no cliente
    const row = s.items.value[0]
    s.openEdit(row)
    await s.uploadLogo(fakePng(1_048_577))
    assert.equal(logoCalls(calls).length, 0, 'arquivo > 1 MB não vai pro servidor')
    assert.equal(s.logoErr.value, 'Logo muito grande — envie uma imagem menor (até 1 MB; depois de reduzida precisa caber em 300 KB)')
    assert.equal(s.logoBusy.value, false)

    // PUT multipart campo "file"; a linha e o modal passam a ter logo
    const file = fakePng(32)
    const up = s.uploadLogo(file)
    assert.equal(s.logoBusy.value, true, 'spinner enquanto sobe')
    await up
    assert.equal(s.logoBusy.value, false)
    assert.equal(s.logoErr.value, null)
    const put = logoCalls(calls).at(-1)
    assert.equal(put.url, '/api/marcas/m1/logo')
    assert.equal(put.opts.method, 'PUT')
    assert.ok(put.opts.body instanceof FormData, 'corpo é FormData (multipart)')
    assert.equal(put.opts.body.get('file'), file, 'campo "file"')
    assert.equal(row.has_logo, true, 'linha atualizada no lugar')
    assert.equal(s.modalEditing.value.has_logo, true, 'prévia do modal atualizada')
    assert.equal(s.modalEditing.value, row, 'modal e linha são o mesmo objeto')
    assert.equal(row.updated_at, 'v1', 'updated_at novo → miniatura fura o cache')
    assert.equal(row.has_senha, true, 'o resto da linha continua')

    // o <input type=file> passa pelo mesmo caminho e permite re-escolher o mesmo arquivo
    const input = { files: [fakePng(8)], value: 'C:\\fakepath\\logo.png' }
    s.onLogoChange({ target: input })
    await settle()
    assert.equal(input.value, '', 'input zerado após escolher')
    assert.equal(logoCalls(calls).length, 2)
    s.onLogoChange({ target: { files: [], value: '' } })
    await settle()
    assert.equal(logoCalls(calls).length, 2, 'cancelar o seletor não sobe nada')

    // Remover: confirm → DELETE /logo; linha sem logo
    await s.removeLogo()
    assert.match(confirms.at(-1), /Remover o logo/)
    const del = logoCalls(calls).at(-1)
    assert.equal(del.opts.method, 'DELETE')
    assert.equal(del.url, '/api/marcas/m1/logo')
    assert.equal(row.has_logo, false)
    assert.equal(s.modalEditing.value.has_logo, false)
    const n = logoCalls(calls).length
    await s.removeLogo()
    assert.equal(logoCalls(calls).length, n, 'sem logo não há DELETE')
    s.closeModal()
  }
  {
    const { state: s, calls, confirms } = await page({ list: [marca({ has_logo: true })], confirmAnswer: false })
    const row = s.items.value[0]
    s.openEdit(row)
    await s.removeLogo()
    assert.equal(confirms.length, 1)
    assert.equal(logoCalls(calls).length, 0, 'cancelar o confirm não remove')
    assert.equal(row.has_logo, true)
  }
  {
    // erro do backend (415 logo_tipo_invalido) vira mensagem pt-BR e o modal segue aberto
    const { state: s } = await page({ logoError: { data: { detail: { code: 'logo_tipo_invalido' } } } })
    s.openEdit(s.items.value[0])
    await s.uploadLogo(fakePng(8, 'image/gif', 'x.gif'))
    assert.equal(s.logoErr.value, apiError.MARCAS_ERROS.logo_tipo_invalido)
    assert.equal(s.showModal.value, true)
    assert.equal(s.logoBusy.value, false)
    assert.equal(s.items.value[0].has_logo, false)
  }

  // Datas e pills.
  {
    const { state: s } = await page()
    assert.equal(s.fmtDate('2034-07-04'), '04/07/2034')
    assert.equal(s.fmtDate(null), '—')
    const today = dateLib.isoToday()
    assert.equal(s.validadeVencida('2000-01-01'), true)
    assert.equal(s.validadeVencida(today), false)
    assert.equal(s.validadeClass('2000-01-01'), 'text-red-500 font-semibold')
    const soon = new Date(); soon.setDate(soon.getDate() + 30)
    const far = new Date(); far.setDate(far.getDate() + 200)
    assert.equal(s.validadeClass(dateLib.isoDateBrt(soon)), 'text-amber-500')
    assert.equal(s.validadeClass(dateLib.isoDateBrt(far)), '')
    assert.equal(s.validadeClass(null), 'text-muted-foreground')
    assert.ok(s.daysUntil(dateLib.isoDateBrt(soon)) >= 29 && s.daysUntil(dateLib.isoDateBrt(soon)) <= 31)
    assert.equal(s.inpiPill('registrado'), 'pill-success')
    assert.equal(s.inpiPill('aguardando'), 'pill-warning')
    assert.equal(s.inpiPill('expirado'), 'pill-danger')
    assert.equal(s.inpiPill('nao_registrado'), 'pill-muted')
    assert.equal(s.inpiPill('???'), 'pill-muted')
    assert.equal(s.inpiLabel('nao_registrado'), 'não registrado')
  }

  // Erro da API vira mensagem pt-BR (apiErrMsg + MARCAS_ERROS) e o modal
  // fica aberto com a senha ainda no campo (o usuário corrige e tenta de novo).
  {
    const { state: s } = await page({ postError: { data: { detail: { code: 'marca_slug_conflict' } } } })
    s.openNew()
    s.form.value.nome = 'Dup'
    s.form.value.senha = 'abc'
    await s.saveModal()
    assert.equal(s.saveErr.value, 'Já existe uma marca com esse nome')
    assert.equal(s.showModal.value, true)
    assert.equal(s.form.value.senha, 'abc')
    s.closeModal()
    assert.equal(s.form.value.senha, '')
  }
  {
    const e422 = { data: { detail: [{ loc: ['body', 'nome'], msg: 'Value error, nome_too_long' }] } }
    const { state: s } = await page({ postError: e422 })
    s.openNew()
    s.form.value.nome = 'x'
    await s.saveModal()
    assert.equal(s.saveErr.value, 'nome: Nome muito longo (máx. 128)')
  }
  {
    // 422 do backend pro site (se o cliente deixar passar) também vira pt-BR
    const e422 = { data: { detail: [{ loc: ['body', 'site'], msg: 'Value error, site_invalido' }] } }
    const { state: s } = await page({ postError: e422 })
    s.openNew()
    s.form.value.nome = 'x'
    await s.saveModal()
    assert.equal(s.saveErr.value, 'site: Site precisa começar com http:// ou https://')
  }
  {
    // company_not_found (empresa apagada entre abrir e salvar)
    const { state: s } = await page({ postError: { data: { detail: { code: 'company_not_found' } } } })
    s.openNew()
    s.form.value.nome = 'x'
    s.form.value.company_id = 'sumiu'
    await s.saveModal()
    assert.equal(s.saveErr.value, 'Empresa não encontrada')
  }

  console.log('PASS: SFC parse/compile; carga+busca; senha (reveal/auto-hide/clear/copy); inline (+site); ativo/excluir; modal POST/PATCH/limpar senha/empresa; buildBody; logo (check/PUT/DELETE); datas/pills; erros')
}
run().catch((error) => { console.error(error); process.exitCode = 1 })
