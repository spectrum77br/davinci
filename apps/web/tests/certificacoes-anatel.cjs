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
const filename = path.join(__dirname, '../components/CertificacoesPainel.vue')
const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
assert.deepEqual(errors, [])
const compiled = compileTemplate({ source: descriptor.template.content, filename, id: 'anatel-check' })
assert.deepEqual(compiled.errors, [])
const rendered = {}
new Function('exports', 'require', transpile(compiled.code))(rendered, require)
const utilitySource = fs.readFileSync(path.join(__dirname, '../utils/certificacoesAutosave.ts'), 'utf8')
// A lib do .xlsx de verdade (o Excel da barra, M-67).
const planilha = {}
new Function('exports', transpile(fs.readFileSync(path.join(__dirname, '../lib/planilhaXlsx.ts'), 'utf8')))(planilha)
const pageScript = descriptor.scriptSetup.content.replace(/^import .*$/gm, '').replace('await load()', '').replace(/^defineProps.*$/m, '')
const exportsForTest = `return {
  embutido: false,
  rows, mobileTableMode, loading, errorText, statusError, syncMessage, exporting, adding, synchronizing,
  anatelStatus, selectedRow, detailsDialog, rowBusy, busy, hasBusyRow, canEdit, canDelete, canAttach,
  inmetroStatus, inmetroStatusError, inmetroSyncMessage, inmetroSyncError, synchronizingInmetro,
  CERT_OPTIONS, ANATEL_SOURCE_URL, INMETRO_SOURCE_URL, scheduleSave, syncAnatel, syncInmetro,
  exportTable, load, loadAnatelStatus, loadInmetroStatus,
  menuAtualizarAberto, atualizarAnatel, atualizarInmetro, atualizandoAlgo, rotuloAtualizar,
  abrirMenuAtualizar, executarAtualizar, avisoFalhaConsulta,
  attachPdf, addRow, removeRow, downloadCertificate, rowStatusClass, officialSituation,
  officialSituationClass, isLinked, isOfficialField, hasManualOfficialFields, officialSource, officialData,
  isMissingSource, officialAlerts, fieldSourceTitle, openDetails, formatTimestamp, formatDate, formatCnpj,
  GARANTIA_MESES_MAX, lerGarantiaMeses, salvarGarantia,
  COLUNAS_LAYOUT, CHAVE_COLUNAS_OCULTAS, colunasOcultas, mostraColuna, alternarColuna, lerColunasOcultas,
  totalColunas, linhasExcel
}`

// `opcoes.localStorage`: o armazenamento falso do navegador (Layout, M-67).
// `mounted`: os onMounted do componente (o teste chama quando quiser).
// `planilhas`: cada .xlsx gerado ({ linhas, aba, bytes }); `baixados`: os nomes dos arquivos baixados.
function page(auth = { isAdmin: true }, opcoes = {}) {
  const calls = []
  const downloads = []
  const mounted = []
  const planilhas = []
  const baixados = []
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
    'onMounted', 'watch', 'gerarXlsx', 'TIPO_XLSX', 'localStorage',
    transpile(pageScript) + '\n' + exportsForTest)(
    Vue.ref, Vue.reactive, Vue.computed, () => {}, () => {}, () => ({ api, url: v => v }),
    () => auth, util.createCertificacoesAutosave,
    async url => { downloads.push(url); return { ok: true, blob: async () => new Blob(['pdf']) } },
    {
      createElement: () => {
        const a = { click() { baixados.push(a.download) }, remove() {} }
        return a
      },
      body: { appendChild() {} },
    },
    { createObjectURL: () => 'blob:test', revokeObjectURL() {} }, fakeTimeout, () => true,
    fn => { mounted.push(fn) }, Vue.watch,
    (linhas, aba) => {
      const bytes = planilha.gerarXlsx(linhas, aba)
      planilhas.push({ linhas, aba, bytes })
      return bytes
    },
    planilha.TIPO_XLSX, opcoes.localStorage,
  )
  return { state, calls, downloads, mounted, planilhas, baixados }
}
const settle = () => new Promise(setImmediate)
const row = (values = {}) => ({
  id: 'one', produto: 'Telefone', modelo: 'USM003', nome_comercial: 'Modelo comercial',
  certificado: 'anatel', numero: '07197-26-18234', valor: 53000,
  inicio: '2026-07-30', fim: '2028-07-30', tem_pdf: true, pdf_nome: 'certificado.pdf', campos_manuais: null,
  anatel_numero: null, anatel_dados: null, anatel_consultado_em: null, anatel_encontrado: null,
  inmetro_chave: null, inmetro_dados: null, inmetro_consultado_em: null, inmetro_encontrado: null,
  garantia_meses: null,
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
  for (const name of ['ChevronDown', 'Download', 'ExternalLink', 'Loader2', 'Paperclip', 'Plus', 'RefreshCw', 'Trash2', 'X']) {
    app.component(name, { render: () => Vue.h('span') })
  }
  // Popovers do Layout e do Atualizar (reka-ui): aqui sempre abertos, para
  // conferir as caixas.
  for (const name of ['PopoverRoot', 'PopoverTrigger', 'PopoverPortal', 'PopoverContent']) {
    app.component(name, { setup: (_, { slots }) => () => Vue.h('div', slots.default?.()) })
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
    assert.equal(calls.length, 4)
    calls.find(c => c.url === '/api/financeiro/suprimentos').resolve([current])
    calls.find(c => c.url.endsWith('/anatel/status')).resolve(status())
    calls.find(c => c.url.endsWith('/inmetro/status')).resolve(status())
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
    assert.match(s.statusError.value, /Tente atualizar/)
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
    calls.find(c => c.url.endsWith('/inmetro/status')).resolve(status())
    await loading
    assert.equal(s.rows.value.length, 1, 'failed reload must retain visible data')
    assert.match(s.errorText.value, /dados exibidos foram preservados/)
    assert.equal(s.loading.value, false)
  }
  {
    const { state: s, calls } = page()
    const linked = row({ anatel_numero: '07197-26-18234', anatel_dados: source() })
    s.rows.value = [linked]
    const changes = {
      produto: 'Nome amigável', modelo: 'Novo modelo', nome_comercial: 'Novo nome comercial',
      certificado: 'inmetro', numero: 'NOVO-001', valor: 123,
      inicio: '2026-09-01', fim: '2029-09-01',
    }
    const originalSource = structuredClone(linked.anatel_dados)
    for (const [field, value] of Object.entries(changes)) {
      s.scheduleSave(linked, field, value)
      assert.equal(linked[field], value, `linked ${field} must be editable`)
    }
    await s.removeRow(linked)
    assert.equal(calls.length, 0, 'automatic records still cannot be deleted')
    assert.deepEqual(linked.anatel_dados, originalSource, 'editing the registration must never alter official source data')
    const loading = s.load()
    assert.deepEqual(calls[0].options.body, changes, 'all edited fields must be included in the save')
    assert.equal(calls[0].options.method, 'PATCH')
    calls[0].resolve({ ...linked })
    await settle()
    calls.find(c => c.url === '/api/financeiro/suprimentos').resolve([{ ...linked, campos_manuais: Object.keys(changes) }])
    calls.find(c => c.url.endsWith('/anatel/status')).resolve(status())
    calls.find(c => c.url.endsWith('/inmetro/status')).resolve(status())
    await loading
    const saved = s.rows.value[0]
    for (const [field, value] of Object.entries(changes)) assert.equal(saved[field], value)
    assert.match(s.officialSituation(saved), /Cadastro editado/)
    assert.doesNotMatch(s.officialSituationClass(saved), /emerald/)
    s.selectedRow.value = saved
    const html = await render(s)
    const controls = html.match(/<(?:input|select)\b[^>]*class="cell-input\b[^"]*"[^>]*>/g)
    assert.equal(controls.length, 9, 'os 8 campos do cadastro + Garantia (meses)')
    for (const control of controls) assert.doesNotMatch(control, /readonly|disabled/)
    const dialog = html.slice(html.indexOf('<dialog'))
    assert.match(dialog, /Dados da fonte original/)
    assert.match(dialog, /07197-26-18234/)
    assert.match(dialog, /USM003/)
    assert.match(dialog, /Homologação Emitida/)
    assert.doesNotMatch(dialog, /NOVO-001|Novo modelo|Novo nome comercial/, 'official details must not misrepresent edited identity')
  }
  {
    const { state: s, calls } = page({ isAdmin: false, user: { permissions: { financeiro_suprimentos: { view: true, delete: true } } } })
    const current = row({ anatel_numero: '07197-26-18234', anatel_dados: source() })
    const previous = structuredClone(current)
    s.rows.value = [current]
    for (const field of ['produto', 'modelo', 'nome_comercial', 'certificado', 'numero', 'valor', 'inicio', 'fim', 'garantia_meses']) {
      s.scheduleSave(current, field, 'unauthorized change')
    }
    s.salvarGarantia(current, '12')
    await s.syncAnatel()
    assert.equal(calls.length, 0)
    assert.deepEqual(current, previous)
    const html = await render(s)
    assert.doesNotMatch(html, /Atualizar agora|Nova certificação|Anexar PDF/)
    assert.match(html, /data-acao="excel-certificacoes"/, 'quem só vê também baixa o Excel (M-67)')
    assert.doesNotMatch(html, /Baixar tabela em PDF/)
    assert.match(html, /Baixar PDF de Telefone/)
    const controls = html.match(/<(?:input|select)\b[^>]*class="cell-input\b[^"]*"[^>]*>/g)
    assert.equal(controls.length, 9, 'os 8 campos do cadastro + Garantia (meses)')
    for (const control of controls) assert.match(control, /disabled/)
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
    // M-66: os blocos Anatel/Inmetro saíram do topo.
    assert.doesNotMatch(html, /Atualização diária/)
    assert.equal((html.match(/readonly/g) || []).length, 0, 'linked source fields are editable in the table')
    assert.equal((html.match(/aria-label="Excluir certificação"/g) || []).length, 1, 'only manual row exposes delete')
    assert.match(s.rowStatusClass(emitted), /red/, 'expiry styling is independent of official status')
    assert.match(s.officialSituationClass(emitted), /emerald/)
    assert.match(s.officialSituationClass(pending), /amber/)
    assert.doesNotMatch(s.officialSituationClass({ ...emitted, anatel_encontrado: false }), /emerald/)
    assert.match(html, /data-acao="excel-certificacoes"/)
    assert.match(html, /PDF do certificado/)
    s.selectedRow.value = { ...emitted, anatel_encontrado: false }
    const details = await render(s)
    assert.match(details, /40.191.104\/0001-45/)
    assert.match(details, /Situação do certificado/)
    assert.match(details, /isso não confirma cancelamento ou irregularidade/)
    assert.match(details, /Base oficial da Anatel/)
  }
  {
    const { state: s, calls } = page()
    const current = row({ anatel_numero: '07197-26-18234', anatel_dados: source() })
    const previous = structuredClone(current)
    for (const blockedBy of ['loading', 'rowBusy']) {
      if (blockedBy === 'loading') s.loading.value = true
      else s.rowBusy[current.id] = true
      for (const field of ['produto', 'modelo', 'nome_comercial', 'certificado', 'numero', 'valor', 'inicio', 'fim']) {
        s.scheduleSave(current, field, 'blocked during save')
      }
      s.loading.value = false
      delete s.rowBusy[current.id]
    }
    assert.deepEqual(current, previous, 'source field editing must retain busy guards')
    assert.equal(calls.length, 0)
  }
  {
    // Garantia (meses) do certificado (06/11/2026): salva ao sair do campo;
    // vazio = não define (null); 0..120; o resto avisa e não salva. Vale para
    // todos os produtos do certificado (quem propaga é o servidor).
    const { state: s, calls } = page()
    const current = row({ anatel_numero: '07197-26-18234', anatel_dados: source() })
    s.rows.value = [current]
    assert.equal(s.lerGarantiaMeses(''), null)
    assert.equal(s.lerGarantiaMeses(' 0 '), 0, '0 = sem garantia (não é vazio)')
    assert.equal(s.lerGarantiaMeses('120'), 120)
    for (const ruim of ['121', '-1', '12.5', '1e2', 'abc', '12e', '12 meses', 'doze']) assert.equal(s.lerGarantiaMeses(ruim), undefined, ruim)
    s.salvarGarantia(current, '121')
    assert.match(s.errorText.value, /^Garantia: prazo inválido \("121"\)/)
    assert.equal(current.garantia_meses, null, 'inválido não muda a linha')
    // Revisão de 06/11/2026: texto digitado chega como texto (o campo não é
    // type="number", que entregaria "" e apagaria a garantia).
    current.garantia_meses = 6
    s.salvarGarantia(current, '12e')
    assert.match(s.errorText.value, /^Garantia: prazo inválido \("12e"\)/)
    assert.equal(current.garantia_meses, 6, '"12e" não apaga a garantia')
    current.garantia_meses = null
    s.salvarGarantia(current, '12')
    assert.equal(s.errorText.value, null, 'o valor certo limpa o aviso')
    assert.equal(current.garantia_meses, 12)
    assert.deepEqual(current.campos_manuais, ['garantia_meses'])
    s.salvarGarantia(current, '12')  // igual: nada novo
    const loading = s.load()
    assert.equal(calls.length, 1)
    assert.equal(calls[0].url, `/api/financeiro/suprimentos/${current.id}`)
    assert.deepEqual(calls[0].options.body, { garantia_meses: 12 })
    calls[0].resolve({ ...current })
    await settle()
    calls.find(c => c.url === '/api/financeiro/suprimentos').resolve([{ ...current }])
    calls.find(c => c.url.endsWith('/anatel/status')).resolve(status())
    calls.find(c => c.url.endsWith('/inmetro/status')).resolve(status())
    await loading
    // Apagar = null (o certificado deixa de definir; os produtos ficam como estão).
    s.salvarGarantia(s.rows.value[0], '')
    assert.equal(s.rows.value[0].garantia_meses, null)
    // Campo editado à mão que não é da fonte oficial: não marca "Cadastro editado".
    assert.doesNotMatch(s.officialSituation(s.rows.value[0]), /Cadastro editado/)
    const html = await render(s)
    assert.match(html, /<th class="text-right" data-col-garantia-meses[^>]*title="Prazo da garantia, em meses \(0 = sem garantia\)\. Mudar aqui muda a Garantia \(meses\) de todos os produtos deste certificado na Tabela de Preços\.[^"]*"[^>]*>Garantia \(meses\)<\/th>/)
    const cell = html.match(/<input[^>]*data-cert-garantia[^>]*>/)
    assert.ok(cell, 'célula da garantia')
    assert.match(cell[0], /type="text"/)
    assert.match(cell[0], /inputmode="numeric"/)
    assert.doesNotMatch(cell[0], /type="number"/, 'number entrega "" para "12e" e apagaria a garantia')
    // Cartão do celular mostra a garantia.
    s.rows.value[0].garantia_meses = 0
    assert.match(await render(s), /<dt class="text-muted-foreground">Garantia<\/dt><dd class="text-right">Sem garantia<\/dd>/)
    s.rows.value[0].garantia_meses = 18
    assert.match(await render(s), /<dd class="text-right">18 meses<\/dd>/)
  }
  // ── M-66: topo limpo e um só "Atualizar ▾" (Anatel, Inmetro) ──────────
  {
    // Topo: título e barra [Layout] [Excel] [Atualizar ▾] … [+ Nova certificação];
    // nada de contagem, legenda, blocos, frase e Recarregar / Atualizar agora /
    // Atualizar Inmetro. Sem falha, sem aviso.
    const { state: s } = page()
    s.rows.value = [row(), row({ id: 'two', fim: '2020-01-01' })]
    s.anatelStatus.value = status()
    s.inmetroStatus.value = status()
    // Sem os comentários do template (o build de produção também tira).
    const html = (await render(s)).replace(/<!--[\s\S]*?-->/g, '')
    assert.match(html, /<div class="flex flex-wrap items-center gap-2" data-barra-certificacoes><h1[^>]*>Certificações<\/h1>/, 'título na barra')
    assert.doesNotMatch(html, /\d+ itens?\b/, 'sem a contagem de itens')
    assert.doesNotMatch(html, /&lt; 30 dias|vencido<\/span>/, 'sem a legenda de cores')
    assert.doesNotMatch(html, /Recarregar|Atualizar agora|Atualizar Inmetro|Atualização diária|Última consulta bem-sucedida/)
    assert.doesNotMatch(html, /Todos os campos do cadastro podem ser editados/)
    assert.doesNotMatch(html, /data-campo="atualizar-anvisa"|>\s*Anvisa\s*<\/label>/, 'Anvisa fica fora do menu: não existe consulta')
    assert.doesNotMatch(html, /data-aviso-falha-consulta/, 'sem falha, sem aviso')
    assert.match(html, /class="btn btn-sm h-7 py-0 bg-blue-600 text-white hover:bg-blue-700 hover:text-white" data-acao="atualizar"/, 'Atualizar azul, como o da Tabela')
    assert.match(html, /class="btn btn-sm h-7 py-0 btn-primary ml-auto" data-acao="nova-certificacao"/, '+ Nova certificação à direita')
    const ordem = ['data-acao="layout-certificacoes"', 'data-acao="excel-certificacoes"', 'data-acao="atualizar"', 'data-acao="nova-certificacao"']
      .map((m) => html.indexOf(m))
    assert.ok(ordem.every((p, i) => p > 0 && (i === 0 || p > ordem[i - 1])), 'ordem: Layout, Excel, Atualizar, Nova certificação')
    const menu = html.slice(html.indexOf('data-menu-atualizar'), html.indexOf('data-acao="atualizar-executar"'))
    assert.deepEqual([...menu.matchAll(/data-campo="atualizar-(\w+)"/g)].map((m) => m[1]), ['anatel', 'inmetro'])
    for (const campo of ['atualizar-anatel', 'atualizar-inmetro']) {
      const caixa = html.match(new RegExp(`<input[^>]*data-campo="${campo}"[^>]*>`))
      assert.ok(caixa, campo)
      assert.match(caixa[0], /type="checkbox"/)
      assert.match(caixa[0], /checked/, `${campo} marcado por padrão`)
    }
    assert.match(html, /data-acao="atualizar-executar"/)
    // As cores das linhas continuam (só a legenda saiu).
    assert.match(s.rowStatusClass(s.rows.value[1]), /red/)
    // Abrir o menu de novo volta com as duas marcadas.
    s.atualizarAnatel.value = false
    s.atualizarInmetro.value = false
    s.abrirMenuAtualizar(true)
    assert.equal(s.menuAtualizarAberto.value, true)
    assert.equal(s.atualizarAnatel.value, true)
    assert.equal(s.atualizarInmetro.value, true)
  }
  {
    // Só Anatel marcada: salva a edição pendente, consulta só a Anatel e relê a lista uma vez.
    const { state: s, calls } = page()
    const current = row()
    s.rows.value = [current]
    s.abrirMenuAtualizar(true)
    s.atualizarInmetro.value = false
    s.scheduleSave(current, 'produto', 'Edição antes de atualizar')
    const atualizando = s.executarAtualizar()
    assert.equal(s.menuAtualizarAberto.value, false, 'o menu fecha ao confirmar')
    await settle()
    assert.equal(calls[0].options.method, 'PATCH')
    assert.deepEqual(calls[0].options.body, { produto: 'Edição antes de atualizar' })
    calls[0].resolve()
    await settle()
    assert.equal(calls[1].url, '/api/financeiro/suprimentos/anatel/sincronizar')
    assert.equal(s.rotuloAtualizar.value, 'Consultando Anatel...')
    calls[1].resolve({ status: 'ok', criados: 1, atualizados: 2, nao_localizados: 0, conflitos: 1 })
    await settle()
    assert.equal(calls.length, 5, 'relê a lista e a situação das duas consultas, uma vez só')
    calls.find(c => c.url === '/api/financeiro/suprimentos').resolve([row({ produto: 'Edição antes de atualizar', anatel_numero: '07197-26-18234' })])
    calls.find(c => c.url.endsWith('/anatel/status')).resolve(status())
    calls.find(c => c.url.endsWith('/inmetro/status')).resolve(status())
    await atualizando
    assert.ok(!calls.some(c => c.url.endsWith('/inmetro/sincronizar')), 'Inmetro desmarcado não é consultado')
    assert.equal(s.rows.value[0].anatel_numero, '07197-26-18234')
    assert.match(s.syncMessage.value, /1 novos registros e 2 atualizados/)
    assert.match(s.syncMessage.value, /conferência/)
    assert.equal(s.errorText.value, null)
    assert.equal(s.busy.value, false)
    assert.equal(s.rotuloAtualizar.value, 'Atualizar')
  }
  {
    // Só Inmetro marcado: consulta só o Inmetro e relê a lista.
    const { state: s, calls } = page()
    s.rows.value = [row()]
    s.abrirMenuAtualizar(true)
    s.atualizarAnatel.value = false
    const atualizando = s.executarAtualizar()
    await settle()
    assert.equal(calls[0].url, '/api/financeiro/suprimentos/inmetro/sincronizar')
    calls[0].resolve({ status: 'ok', criados: 0, atualizados: 1, nao_localizados: 0, conflitos: 0 })
    await settle()
    assert.equal(calls.length, 4)
    calls.find(c => c.url === '/api/financeiro/suprimentos').resolve([row()])
    calls.find(c => c.url.endsWith('/anatel/status')).resolve(status())
    calls.find(c => c.url.endsWith('/inmetro/status')).resolve(status())
    await atualizando
    assert.ok(!calls.some(c => c.url.endsWith('/anatel/sincronizar')))
    assert.match(s.inmetroSyncMessage.value, /0 novos registros e 1 atualizados/)
  }
  {
    // As duas marcadas; a Anatel falha e o Inmetro funciona: o aviso de erro
    // da Anatel fica, a mensagem do Inmetro aparece e a lista é relida.
    const { state: s, calls } = page()
    s.rows.value = [row()]
    s.abrirMenuAtualizar(true)
    const atualizando = s.executarAtualizar()
    await settle()
    assert.equal(calls[0].url, '/api/financeiro/suprimentos/anatel/sincronizar')
    calls[0].resolve({ status: 'error', erro: 'detalhe interno' })
    await settle()
    assert.ok(calls[1].url.endsWith('/anatel/status'))
    calls[1].resolve(status({ erro: 'detalhe interno' }))
    await settle()
    assert.equal(calls[2].url, '/api/financeiro/suprimentos/inmetro/sincronizar')
    calls[2].resolve({ status: 'ok', criados: 2, atualizados: 0, nao_localizados: 0, conflitos: 3 })
    await settle()
    assert.equal(calls.length, 6)
    calls.find(c => c.url === '/api/financeiro/suprimentos').resolve([row()])
    calls.filter(c => c.url.endsWith('/anatel/status')).at(-1).resolve(status({ erro: 'detalhe interno' }))
    calls.find(c => c.url.endsWith('/inmetro/status')).resolve(status())
    await atualizando
    assert.match(s.errorText.value, /consulta à Anatel/, 'o aviso da Anatel não some ao reler a lista')
    assert.match(s.inmetroSyncMessage.value, /2 novos registros e 0 atualizados/)
    assert.match(s.inmetroSyncMessage.value, /3 registros precisam de conferência/)
    assert.match(s.avisoFalhaConsulta.value, /^A última tentativa de consulta à Anatel falhou\./)
    let html = await render(s)
    assert.equal((html.match(/data-aviso-falha-consulta/g) || []).length, 1, 'uma linha só')
    assert.match(html, /<p[^>]*class="truncate text-xs text-amber-700/)
    assert.doesNotMatch(html, /detalhe interno/)
    // As duas falhando: uma frase só.
    s.inmetroStatus.value = status({ erro: 'x' })
    assert.match(s.avisoFalhaConsulta.value, /consulta à Anatel e ao Inmetro falhou/)
    // Não deu para ler a situação: também avisa, na mesma linha.
    s.statusError.value = 'Não foi possível consultar a atualização automática da Anatel. Tente atualizar em instantes.'
    assert.match(s.avisoFalhaConsulta.value, /^A última tentativa de consulta ao Inmetro falhou\..*Não foi possível consultar a atualização automática da Anatel/)
    // A consulta volta a funcionar: o aviso some.
    s.statusError.value = null
    s.anatelStatus.value = status()
    s.inmetroStatus.value = status()
    assert.equal(s.avisoFalhaConsulta.value, '')
    html = await render(s)
    assert.doesNotMatch(html, /data-aviso-falha-consulta/)
  }
  {
    // Edição pendente que não salvou: para tudo (não consulta nem relê por cima).
    const { state: s, calls } = page()
    const current = row()
    s.rows.value = [current]
    s.scheduleSave(current, 'produto', 'Não salvou')
    const atualizando = s.executarAtualizar()
    await settle()
    calls[0].reject(new Error('offline'))
    await atualizando
    assert.equal(calls.length, 1)
    assert.equal(s.rows.value[0].produto, 'Não salvou')
    assert.match(s.errorText.value, /não foram salvas/)
    assert.equal(s.busy.value, false)
  }
  {
    // Admin desmarca as duas: só salva e relê a lista (o que o Recarregar fazia).
    const { state: s, calls } = page()
    s.rows.value = [row()]
    s.abrirMenuAtualizar(true)
    s.atualizarAnatel.value = false
    s.atualizarInmetro.value = false
    const atualizando = s.executarAtualizar()
    await settle()
    assert.equal(calls.length, 3)
    assert.ok(!calls.some(c => c.url.endsWith('/sincronizar')))
    calls.find(c => c.url === '/api/financeiro/suprimentos').resolve([row()])
    calls.find(c => c.url.endsWith('/anatel/status')).resolve(status())
    calls.find(c => c.url.endsWith('/inmetro/status')).resolve(status())
    await atualizando
    assert.equal(s.loading.value, false)
  }
  {
    // Quem só vê: o Atualizar não tem menu nem caixas e só relê a lista.
    const { state: s, calls } = page({ isAdmin: false, user: { permissions: { financeiro_suprimentos: { view: true } } } })
    s.rows.value = [row()]
    const html = await render(s)
    assert.match(html, /data-acao="atualizar"/)
    assert.doesNotMatch(html, /data-menu-atualizar|data-campo="atualizar-anatel"|data-campo="atualizar-inmetro"/)
    const atualizando = s.executarAtualizar()
    await settle()
    assert.equal(calls.length, 3)
    assert.ok(!calls.some(c => c.url.endsWith('/sincronizar')))
    calls.find(c => c.url === '/api/financeiro/suprimentos').resolve([row()])
    calls.find(c => c.url.endsWith('/anatel/status')).resolve(status())
    calls.find(c => c.url.endsWith('/inmetro/status')).resolve(status())
    await atualizando
    assert.equal(s.loading.value, false)
  }
  console.log('PASS: Anatel editing, saved patches, official data isolation, permissions and synchronization scenarios')
}
module.exports = { page, row, source, status, render, settle }
if (require.main === module) run().catch(error => { console.error(error); process.exitCode = 1 })
