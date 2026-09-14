// Run from apps/web: node tests/certificacoes-anatel.cjs
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { renderToString } = require('vue/server-renderer')
const { parse, compileTemplate } = require('vue/compiler-sfc')
const transpile = source => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText
const filename = path.join(__dirname, '../pages/financeiro/suprimentos.vue')
const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
assert.deepEqual(errors, [])
const compiled = compileTemplate({ source: descriptor.template.content, filename, id: 'anatel-check' })
assert.deepEqual(compiled.errors, [])
const rendered = {}
new Function('exports', 'require', transpile(compiled.code))(rendered, require)
const utilitySource = fs.readFileSync(path.join(__dirname, '../utils/certificacoesAutosave.ts'), 'utf8')
const pageScript = descriptor.scriptSetup.content.replace(/^import .*$/gm, '').replace('await load()', '')
const exportsForTest = `return {
  rows, loading, errorText, statusError, syncMessage, exporting, adding, synchronizing,
  anatelStatus, selectedRow, detailsDialog, rowBusy, busy, hasBusyRow, canEdit, canDelete, canAttach,
  CERT_OPTIONS, ANATEL_SOURCE_URL, totalRows, scheduleSave, syncAnatel, exportTable, load,
  attachPdf, addRow, removeRow, downloadCertificate, rowStatusClass, officialSituation,
  officialSituationClass, isLinked, openDetails, formatTimestamp, formatDate, formatCnpj
}`

function page(auth = { isAdmin: true }) {
  const calls = []
  const downloads = []
  const timers = new Map()
  let nextId = 0
  const fakeTimeout = fn => { timers.set(++nextId, fn); return nextId }
  const util = {}
  new Function('exports', 'setTimeout', 'clearTimeout', transpile(utilitySource))(
    util, fakeTimeout, id => timers.delete(id),
  )
  const api = (url, options) => new Promise((resolve, reject) => calls.push({ url, options, resolve, reject }))
  const state = new Function('ref', 'reactive', 'computed', 'onBeforeUnmount', 'definePageMeta',
    'useApi', 'useAuthStore', 'createCertificacoesAutosave', 'fetch', 'document', 'URL', 'setTimeout', 'confirm',
    transpile(pageScript) + '\n' + exportsForTest)(
    Vue.ref, Vue.reactive, Vue.computed, () => {}, () => {}, () => ({ api, url: v => v }),
    () => auth, util.createCertificacoesAutosave,
    async url => { downloads.push(url); return { ok: true, blob: async () => new Blob(['pdf']) } },
    { createElement: () => ({ click() {}, remove() {} }), body: { appendChild() {} } },
    { createObjectURL: () => 'blob:test', revokeObjectURL() {} }, fakeTimeout, () => true,
  )
  return { state, calls, downloads }
}
const settle = () => new Promise(setImmediate)
const row = (values = {}) => ({
  id: 'one', produto: 'Telefone', modelo: 'USM003', nome_comercial: 'Modelo comercial',
  certificado: 'anatel', numero: '07197-26-18234', valor: 53000,
  inicio: '2026-07-30', fim: '2028-07-30', tem_pdf: true, pdf_nome: 'certificado.pdf',
  anatel_numero: null, anatel_dados: null, anatel_consultado_em: null, anatel_encontrado: null,
  ...values,
})
const source = (values = {}) => ({
  numero: '07197-26-18234', cnpj: '40191104000145', nome_empresa: 'MAKISA TRADING LTDA',
  produto: 'Telefone móvel celular', modelos: ['USM003'], nomes_comerciais: ['F112'],
  tipos_produto: ['Telefone móvel celular'], fabricantes: ['Fabricante'], certificados: ['CERT-001'],
  inicio: '2026-07-30', fim: '2028-07-30', situacao_certificado: 'Emitido',
  situacao_requerimento: 'Homologação Emitida', alertas: [], ...values,
})
const status = (values = {}) => ({
  cnpj: '40191104000145', nome_empresa: 'MAKISA TRADING LTDA', automatico: true,
  periodicidade: 'diaria', ultimo_sucesso_em: '2026-09-14T13:00:00Z',
  ultima_tentativa_em: '2026-09-14T13:00:00Z', proxima_tentativa_em: '2026-09-15T13:00:00Z',
  erro: null, source_updated_at: '2026-09-14T09:00:00Z', fonte_url: 'https://www.anatel.gov.br/', ...values,
})

async function render(state) {
  const app = Vue.createSSRApp({ setup: () => state, render: rendered.render })
  for (const name of ['Download', 'ExternalLink', 'FileDown', 'Paperclip', 'Plus', 'RefreshCw', 'Trash2', 'X']) {
    app.component(name, { render: () => Vue.h('span') })
  }
  return renderToString(app)
}

async function run() {
  {
    const { state: s, calls } = page()
    const current = row()
    s.rows.value = [current]
    s.scheduleSave(current, 'produto', 'Nome salvo antes de recarregar')
    const loading = s.load()
    assert.equal(calls.length, 1, 'loading must await pending autosave before reading either endpoint')
    assert.equal(calls[0].options.method, 'PATCH')
    calls[0].resolve()
    await settle()
    assert.equal(calls.length, 3)
    calls.find(c => c.url === '/api/financeiro/suprimentos').resolve([current])
    calls.find(c => c.url.endsWith('/anatel/status')).resolve(status())
    await loading
    assert.equal(s.rows.value[0].produto, 'Nome salvo antes de recarregar')
    assert.equal(s.errorText.value, null)
    assert.equal(s.loading.value, false)
  }
  {
    const { state: s, calls, downloads } = page()
    const current = row()
    s.rows.value = [current]
    s.scheduleSave(current, 'produto', 'Nome da equipe')
    const syncing = s.syncAnatel()
    assert.equal(calls[0].options.method, 'PATCH')
    assert.deepEqual(calls[0].options.body, { produto: 'Nome da equipe' })
    await s.syncAnatel()
    await s.exportTable()
    await s.addRow()
    assert.equal(calls.length, 1, 'while saving for synchronization, duplicate sync/export/create must be blocked')
    assert.equal(downloads.length, 0)
    calls[0].resolve()
    await settle()
    assert.equal(calls[1].url, '/api/financeiro/suprimentos/anatel/sincronizar')
    assert.equal(calls[1].options.method, 'POST')
    calls[1].resolve({ status: 'ok', criados: 1, atualizados: 2, nao_localizados: 1, conflitos: 1 })
    await settle()
    calls.find(c => c.url === '/api/financeiro/suprimentos').resolve([row({ produto: 'Nome da equipe', anatel_numero: '07197-26-18234' })])
    calls.find(c => c.url.endsWith('/anatel/status')).resolve(status())
    await syncing
    assert.equal(s.rows.value[0].anatel_numero, '07197-26-18234')
    assert.match(s.syncMessage.value, /1 novos registros e 2 atualizados/)
    assert.match(s.syncMessage.value, /dados anteriores preservados/)
    assert.match(s.syncMessage.value, /conferência/)
    assert.equal(s.synchronizing.value, false)
  }
  {
    const { state: s, calls } = page()
    const current = row()
    s.rows.value = [current]
    s.scheduleSave(current, 'produto', 'Edição pendente')
    const syncing = s.syncAnatel()
    calls[0].reject(new Error('offline'))
    await syncing
    assert.equal(calls.length, 1, 'failed autosave must block synchronization')
    assert.equal(s.rows.value[0].produto, 'Edição pendente')
    assert.match(s.errorText.value, /não foram salvas/)
    assert.equal(s.synchronizing.value, false)
  }
  {
    const { state: s, calls } = page()
    s.rows.value = [row()]
    s.anatelStatus.value = status()
    const syncing = s.syncAnatel()
    await settle()
    calls[0].reject(new Error('network timeout'))
    await settle()
    calls[1].reject(new Error('offline'))
    await syncing
    assert.equal(s.rows.value.length, 1)
    assert.equal(s.anatelStatus.value.ultimo_sucesso_em, '2026-09-14T13:00:00Z')
    assert.match(s.errorText.value, /dados anteriores foram preservados/)
    assert.match(s.statusError.value, /Tente recarregar/)
  }
  {
    const { state: s, calls } = page()
    s.rows.value = [row()]
    const syncing = s.syncAnatel()
    await settle()
    calls[0].resolve({ status: 'error', erro: 'private backend detail' })
    await settle()
    calls[1].resolve(status({ erro: 'private backend detail' }))
    await syncing
    assert.equal(calls.length, 2, 'source failure must not replace the table')
    assert.equal(s.rows.value.length, 1)
    const html = await render(s)
    assert.match(html, /tentará novamente automaticamente/)
    assert.doesNotMatch(html, /private backend detail/)
  }
  {
    const { state: s, calls } = page()
    s.rows.value = [row()]
    const loading = s.load()
    await settle()
    calls.find(c => c.url === '/api/financeiro/suprimentos').reject(new Error('offline'))
    calls.find(c => c.url.endsWith('/anatel/status')).resolve(status())
    await loading
    assert.equal(s.rows.value.length, 1, 'failed reload must retain visible data')
    assert.match(s.errorText.value, /dados exibidos foram preservados/)
    assert.equal(s.loading.value, false)
  }
  {
    const { state: s, calls } = page()
    const linked = row({ anatel_numero: '07197-26-18234', anatel_dados: source() })
    for (const field of ['modelo', 'nome_comercial', 'certificado', 'numero', 'inicio', 'fim']) {
      const previous = linked[field]
      s.scheduleSave(linked, field, 'invalid manual change')
      assert.equal(linked[field], previous, `automatic ${field} must remain read-only`)
    }
    await s.removeRow(linked)
    assert.equal(calls.length, 0, 'automatic records cannot be deleted')
    s.scheduleSave(linked, 'produto', 'Nome amigável')
    s.scheduleSave(linked, 'valor', 123)
    const syncing = s.syncAnatel()
    assert.deepEqual(calls[0].options.body, { produto: 'Nome amigável', valor: 123 })
    calls[0].reject(new Error('stop before source request'))
    await syncing
  }
  {
    const { state: s, calls } = page({ isAdmin: false, user: { permissions: { financeiro_suprimentos: { view: true, delete: true } } } })
    const current = row()
    s.rows.value = [current]
    s.scheduleSave(current, 'produto', 'unauthorized change')
    await s.syncAnatel()
    assert.equal(calls.length, 0)
    assert.equal(current.produto, 'Telefone')
    const html = await render(s)
    assert.doesNotMatch(html, /Atualizar agora|Nova certificação|Anexar PDF/)
    assert.match(html, /Baixar tabela em PDF/)
    assert.match(html, /Baixar PDF de Telefone/)
  }
  {
    const { state: s, calls } = page()
    s.rows.value = [row()]
    const syncing = s.syncAnatel()
    await settle()
    calls[0].resolve({ status: 'busy' })
    await settle()
    calls[1].resolve(status())
    await syncing
    assert.equal(calls.length, 2)
    assert.match(s.syncMessage.value, /já está em andamento/)
    assert.equal(s.busy.value, false)
    assert.equal(s.rows.value.length, 1)
  }
  {
    const { state: s } = page()
    const emitted = row({ anatel_numero: '07197-26-18234', anatel_dados: source(), anatel_encontrado: true, fim: '2020-01-01' })
    const pending = row({ id: 'two', anatel_numero: 'ANATEL-2', anatel_dados: source({ situacao_requerimento: 'Em Análise - RE' }), anatel_encontrado: true })
    s.rows.value = [emitted, pending, row({ id: 'three', certificado: 'inmetro' })]
    const html = await render(s)
    assert.match(html, /Homologação Emitida/)
    assert.match(html, /Em Análise - RE/)
    assert.match(html, /Manual · sem confirmação automática/)
    assert.match(html, /Atualização diária/)
    assert.equal((html.match(/readonly/g) || []).length, 10, 'five text/date fields per automatic row remain read-only')
    assert.equal((html.match(/aria-label="Excluir certificação"/g) || []).length, 1, 'only manual row exposes delete')
    assert.match(s.rowStatusClass(emitted), /red/, 'expiry styling is independent of official status')
    assert.match(s.officialSituationClass(emitted), /emerald/)
    assert.match(s.officialSituationClass(pending), /amber/)
    assert.doesNotMatch(s.officialSituationClass({ ...emitted, anatel_encontrado: false }), /emerald/)
    assert.match(html, /Baixar tabela em PDF/)
    assert.match(html, /PDF do certificado/)
    s.selectedRow.value = { ...emitted, anatel_encontrado: false }
    const details = await render(s)
    assert.match(details, /40.191.104\/0001-45/)
    assert.match(details, /Situação do certificado/)
    assert.match(details, /isso não confirma cancelamento ou irregularidade/)
    assert.match(details, /Base oficial da Anatel/)
  }
  console.log('PASS: ten Anatel synchronization, permissions, data preservation and rendered UI scenarios')
}
run().catch(error => { console.error(error); process.exitCode = 1 })
