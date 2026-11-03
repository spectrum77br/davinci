// Run from apps/web: node tests/consorcio-filtros-imposto-sfc.cjs
// Consórcio (03/11/2026): filtros Grupo ou cota / Alienação / Empresa e a
// coluna Imposto 1% (1% do crédito nas cotas com lance, igual ao 7%), que
// desconta do Crédito líq. e aceita um valor digitado (0 zera; em branco volta
// ao automático). O auto-save manda num PATCH só os campos editados da linha.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const vue = require('vue')
const { parse } = require('vue/compiler-sfc')

const filename = path.resolve(__dirname, '../pages/financeiro/consorcio.vue')
const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
assert.deepEqual(errors, [])
const script = descriptor.scriptSetup.content
const tpl = descriptor.template.content

// O script inteiro roda com o Vue de verdade; só saem os imports, o
// definePageMeta, os composables do Nuxt e o await load() do fim.
const semNuxt = script
  .replace(/^import .*$/gm, '')
  .replace(/definePageMeta\(\{[\s\S]*?\n\}\)/, '')
  .replace('const { api } = useApi()', '')
  .replace('const auth = useAuthStore()', '')
  .replace(/^await load\(\)$/m, '')
assert.doesNotMatch(semNuxt, /useApi|useAuthStore|definePageMeta|await load\(\)/)
const compiled = ts.transpileModule(semNuxt, {
  compilerOptions: { target: ts.ScriptTarget.ES2022 }, reportDiagnostics: true,
})
assert.deepEqual(compiled.diagnostics, [])
const EXPORTA = [
  'load', 'rows', 'linhasVisiveis', 'filtroGrupoCota', 'filtroAlienacao', 'filtroEmpresa', 'filtrando',
  'opcoesEmpresa', 'opcoesAlienacao', 'limparFiltros', 'scheduleSave', 'persist', 'addRow', 'removeRow',
  'errorText', 'saveTimers', 'SEM_VALOR', 'imposto7', 'imposto1', 'imposto1Automatico', 'creditoLiquido',
  'valorDigitadoImposto1', 'empresaDaLinha', 'passaNosFiltros',
]
const montar = new Function('api', 'auth', 'confirm', 'computed', 'reactive', 'ref', 'watch',
  `${compiled.outputText}\nreturn { ${EXPORTA.join(', ')} }`)

const linha = (o) => ({
  id: 'x', credito: null, emp: null, grupo: null, cota: null, alienacao: '', nf: null, parc_a_pagar: null,
  lance: null, valor_parc: null, atualizado: null, fundo_reserva: null, obs: null, imposto1_manual: null, ...o,
})
function pagina(dados) {
  const chamadas = []
  let novo = 100
  const api = async (url, opts = {}) => {
    chamadas.push({ url, method: opts.method || 'GET', body: opts.body })
    if (opts.method === 'POST') return linha({ id: String(novo++), emp: '', alienacao: '' })
    if (opts.method) return {}
    return JSON.parse(JSON.stringify(dados))
  }
  const p = montar(api, { isAdmin: true, user: null }, () => true, vue.computed, vue.reactive, vue.ref, vue.watch)
  return { p, chamadas, patches: () => chamadas.filter((c) => c.method === 'PATCH') }
}
const dormir = (ms) => new Promise((r) => setTimeout(r, ms))
const centavos = (n) => Math.round(n * 100) / 100

;(async () => {
  const { p: f } = pagina([])

  // ── Imposto 1% e Crédito líq. ──
  // Cota 1513/1245 da produção: 125.472,00 de crédito, lance 62.925,00 (a API manda texto).
  const kia = linha({ credito: '125472.00', lance: '62925.00' })
  assert.equal(centavos(f.imposto7(kia)), 8783.04)
  assert.equal(centavos(f.imposto1Automatico(kia)), 1254.72)
  assert.equal(centavos(f.imposto1(kia)), 1254.72)
  assert.equal(centavos(f.creditoLiquido(kia)), 52509.24) // 53.763,96 sem o 1%
  // Digitado: 0 zera (o líquido volta a 53.763,96); outro valor vale no lugar do 1%.
  for (const zero of [0, '0.00', '0']) {
    const l = { ...kia, imposto1_manual: zero }
    assert.equal(f.imposto1(l), 0)
    assert.equal(centavos(f.creditoLiquido(l)), 53763.96)
  }
  assert.equal(centavos(f.creditoLiquido({ ...kia, imposto1_manual: '1141.80' })), 52622.16)
  // Vazio = automático.
  for (const auto of [null, undefined, '', ' ']) assert.equal(centavos(f.imposto1({ ...kia, imposto1_manual: auto })), 1254.72)
  // Sem lance (a contemplar, LOCAGIL): sem 7%, sem 1% automático, sem Crédito líq. — como antes.
  for (const lance of [null, 0, -5, '0.00']) {
    const l = linha({ credito: '42092.00', lance })
    assert.equal(f.imposto7(l), null)
    assert.equal(f.imposto1Automatico(l), null)
    assert.equal(f.imposto1(l), null)
    assert.equal(f.creditoLiquido(l), null)
    assert.equal(f.creditoLiquido({ ...l, imposto1_manual: 0 }), null)
  }
  // O que a célula salva.
  assert.equal(f.valorDigitadoImposto1(''), null)
  assert.equal(f.valorDigitadoImposto1('  '), null)
  assert.equal(f.valorDigitadoImposto1('0'), 0)
  assert.equal(f.valorDigitadoImposto1('1141.8'), 1141.8)
  assert.equal(f.valorDigitadoImposto1('-1'), undefined)
  assert.equal(f.valorDigitadoImposto1('abc'), undefined)

  // ── Empresa: as grafias da mesma EMP viram uma ──
  for (const e of ['Kia', 'KIA', 'KIa', 'Kia ', ' kia']) assert.equal(f.empresaDaLinha(e), 'KIA')
  assert.equal(f.empresaDaLinha(null), '')
  assert.equal(f.empresaDaLinha('   '), '')

  // ── passaNosFiltros ──
  const todas = { grupoCota: '', alienacao: '', empresa: '' }
  const filtro = (o) => ({ ...todas, ...o })
  const a = linha({ emp: 'Kia ', grupo: 1654, cota: 566, alienacao: 'a contemplar' })
  const b = linha({ emp: 'LOCAGIL', grupo: 2122, cota: 261, alienacao: 'pago' })
  const c = linha({ emp: '', alienacao: '' })
  assert.ok([a, b, c].every((r) => f.passaNosFiltros(r, todas)))
  assert.ok(f.passaNosFiltros(a, filtro({ grupoCota: '1654' })))
  assert.ok(f.passaNosFiltros(a, filtro({ grupoCota: '566' })))
  assert.ok(f.passaNosFiltros(a, filtro({ grupoCota: ' 165 ' })))
  assert.ok(!f.passaNosFiltros(b, filtro({ grupoCota: '1654' })))
  assert.ok(!f.passaNosFiltros(c, filtro({ grupoCota: '1' })))
  assert.ok(f.passaNosFiltros(a, filtro({ grupoCota: '1654/566' })))
  assert.ok(f.passaNosFiltros(a, filtro({ grupoCota: '1654 / 5' })))
  assert.ok(!f.passaNosFiltros(a, filtro({ grupoCota: '165/566' })))
  assert.ok(!f.passaNosFiltros(a, filtro({ grupoCota: '1654/66' })))
  assert.ok(f.passaNosFiltros(c, filtro({ grupoCota: 'abc' })))
  assert.ok(f.passaNosFiltros(b, filtro({ alienacao: 'pago' })))
  assert.ok(!f.passaNosFiltros(a, filtro({ alienacao: 'pago' })))
  assert.ok(f.passaNosFiltros(c, filtro({ alienacao: f.SEM_VALOR })))
  assert.ok(f.passaNosFiltros(linha({ alienacao: null }), filtro({ alienacao: f.SEM_VALOR })))
  assert.ok(!f.passaNosFiltros(a, filtro({ alienacao: f.SEM_VALOR })))
  assert.ok(f.passaNosFiltros(a, filtro({ empresa: 'KIA' })))
  assert.ok(!f.passaNosFiltros(b, filtro({ empresa: 'KIA' })))
  assert.ok(f.passaNosFiltros(c, filtro({ empresa: f.SEM_VALOR })))
  assert.ok(!f.passaNosFiltros(a, filtro({ empresa: f.SEM_VALOR })))
  assert.ok(f.passaNosFiltros(a, filtro({ grupoCota: '1654', alienacao: 'a contemplar', empresa: 'KIA' })))
  assert.ok(!f.passaNosFiltros(a, filtro({ grupoCota: '1654', alienacao: 'pago', empresa: 'KIA' })))

  // ── A página de verdade: filtros, linha que não some, linha nova ──
  const dados = [
    linha({ id: '1', emp: 'Kia ', grupo: 1654, cota: 566, alienacao: null }),
    linha({ id: '2', emp: 'LOCAGIL', grupo: 2122, cota: 261, alienacao: 'pago' }),
    linha({ id: '3', emp: 'KIA', grupo: 1524, cota: 4926, alienacao: 'pago' }),
  ]
  const { p, chamadas, patches } = pagina(dados)
  const ids = () => p.linhasVisiveis.value.map((r) => r.id).join(',')
  await p.load()
  assert.equal(ids(), '1,2,3')
  assert.equal(p.filtrando.value, false)
  assert.deepEqual(p.opcoesEmpresa.value, { nomes: ['KIA', 'LOCAGIL'], semEmpresa: false })
  assert.deepEqual(p.opcoesAlienacao.value, ['pago', 'ag. repasse', 'a contemplar'])
  p.filtroEmpresa.value = 'KIA'; await vue.nextTick()
  assert.equal(ids(), '1,3')
  assert.equal(p.filtrando.value, true)
  // Trocar a EMP de uma linha filtrada não a tira da tela no meio da edição...
  p.scheduleSave(p.rows.value[0], 'emp', 'LOCAGIL'); await vue.nextTick()
  assert.equal(ids(), '1,3')
  // ...e a linha nova aparece mesmo com o filtro.
  await p.addRow(); await vue.nextTick()
  assert.equal(ids(), '100,1,3')
  p.filtroAlienacao.value = 'pago'; await vue.nextTick() // mexer no filtro recalcula
  assert.equal(ids(), '3')
  p.filtroAlienacao.value = p.SEM_VALOR; p.filtroEmpresa.value = ''; await vue.nextTick()
  assert.equal(ids(), '100,1')
  p.limparFiltros(); await vue.nextTick()
  assert.equal(ids(), '100,1,2,3')
  // Recarregar reaplica o filtro escolhido.
  p.filtroGrupoCota.value = '2122'; await vue.nextTick()
  await p.load()
  assert.equal(ids(), '2')
  p.limparFiltros(); await vue.nextTick()

  // ── Auto-save: Crédito, Tab, Lance, Imposto 1% em menos de 500 ms = um PATCH com os três ──
  await dormir(600)
  chamadas.length = 0
  const l3 = p.rows.value.find((r) => r.id === '3')
  p.scheduleSave(l3, 'credito', 50000)
  await dormir(200)
  p.scheduleSave(l3, 'lance', 1000)
  p.scheduleSave(l3, 'imposto1_manual', 0)
  await dormir(650)
  assert.deepEqual(patches(), [{ url: '/api/financeiro/consorcio/3', method: 'PATCH', body: { credito: 50000, lance: 1000, imposto1_manual: 0 } }])
  // A alienação salva na hora e leva junto o que estava pendente; o timer não dispara de novo.
  chamadas.length = 0
  p.scheduleSave(l3, 'obs', '1% pago')
  l3.alienacao = 'ag. repasse'
  await p.persist(l3, 'alienacao')
  await dormir(650)
  assert.deepEqual(patches(), [{ url: '/api/financeiro/consorcio/3', method: 'PATCH', body: { obs: '1% pago', alienacao: 'ag. repasse' } }])
  // Apagar o 1% manda null (volta ao automático).
  chamadas.length = 0
  p.scheduleSave(l3, 'imposto1_manual', null)
  await dormir(650)
  assert.deepEqual(patches().map((c) => c.body), [{ imposto1_manual: null }])
  // Excluir a linha com edição pendente não manda PATCH depois.
  chamadas.length = 0
  p.scheduleSave(l3, 'obs', 'x')
  await p.removeRow(l3)
  await dormir(650)
  assert.deepEqual(chamadas.map((c) => c.method), ['DELETE'])
  assert.equal(p.errorText.value, null)

  // ── Tela ──
  assert.match(tpl, /<th class="text-right">Imposto 7%<\/th>\s*<th class="text-right" data-coluna-imposto1>Imposto 1%<\/th>/)
  // A célula do 1% é editável: em branco mostra o automático em cinza (placeholder).
  const celula = tpl.slice(tpl.indexOf('data-imposto1'), tpl.indexOf('</td>', tpl.indexOf('data-imposto1')))
  assert.match(celula, /:value="row\.imposto1_manual \?\? ''"/)
  assert.match(celula, /:placeholder="fmtMoney\(imposto1Automatico\(row\)\)"/)
  assert.match(celula, /:disabled="!canEdit"/)
  assert.match(celula, /valorDigitadoImposto1\(/)
  assert.match(celula, /if \(v !== undefined\) scheduleSave\(row, 'imposto1_manual', v\)/)
  assert.match(tpl, /<span[^>]*data-dica-imposto1>\s*Imposto 1%: em branco calcula sozinho/)
  // Cabeçalho e linha com o mesmo número de colunas; o colspan acompanha.
  const ths = (tpl.match(/<th\b/g) || []).length
  const tds = (tpl.slice(tpl.indexOf('<tr v-for="row in linhasVisiveis"')).match(/<td\b/g) || []).length
  assert.equal(ths, 18)
  assert.equal(tds, 18)
  assert.equal((tpl.match(/:colspan="canDelete \? 18 : 17"/g) || []).length, 2)
  assert.match(tpl, /<tr v-for="row in linhasVisiveis" :key="row\.id"/)
  assert.doesNotMatch(tpl, /v-for="row in rows"/)
  // Os três filtros, rotulados.
  assert.match(tpl, /<span class="text-muted-foreground">Grupo ou cota<\/span>\s*<input\s+v-model="filtroGrupoCota"/)
  assert.match(tpl, /<span class="text-muted-foreground">Alienação<\/span>\s*<select v-model="filtroAlienacao"/)
  assert.match(tpl, /<span class="text-muted-foreground">Empresa<\/span>\s*<select v-model="filtroEmpresa"/)
  // O load() roda no fim do setup, depois de declarados os filtros.
  assert.match(script, /\nawait load\(\)\n$/)
  console.log('consorcio-filtros-imposto: ok')
})().catch((e) => { console.error(e); process.exit(1) })
