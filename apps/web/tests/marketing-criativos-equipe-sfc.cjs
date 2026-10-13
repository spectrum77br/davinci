// Run from apps/web: node tests/marketing-criativos-equipe-sfc.cjs
// Marketing › Criativos — mudança visual (Eduardo, 13/10/2026):
//   1. "agora os mais novos vão aparecer ali em cima" — dias do mais novo pro
//      mais antigo e, dentro do dia, o vídeo que chegou por último primeiro;
//   2. "cada pessoa a quantidade de cada que enviou — Mindset e Bill Gates" —
//      contador de vídeos por equipe no topo, dentro de todos os filtros
//      (texto, status, datas e a própria equipe);
//   3. "filtrar lá em cima por equipe que postou o vídeo" — seletor de equipe
//      (e clique no contador), somando com os outros filtros.
// Só tela: nenhum dado muda, nenhuma rota nova. Dados FALSOS; nenhuma rede.
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
const filename = path.join(__dirname, '../components/MarketingCriativos.vue')
const source = fs.readFileSync(filename, 'utf8')
const { descriptor, errors } = parse(source, { filename })
assert.deepEqual(errors, [])
const compiled = compileTemplate({ source: descriptor.template.content, filename, id: 'criativos-equipe-check' })
assert.deepEqual(compiled.errors, [])
new Function('exports', 'require', transpile(compiled.code))({}, require)

const script = descriptor.scriptSetup.content
const tpl = descriptor.template.content

function loadLib(rel) {
  const exp = {}
  new Function('exports', 'require', transpile(fs.readFileSync(path.join(__dirname, rel), 'utf8')))(exp, require)
  return exp
}
const apiError = loadLib('../lib/apiError.ts')
const dateLib = loadLib('../lib/date.ts')
const redes = loadLib('../lib/redesSociais.ts')

// ---------------------------------------------------------------- template
// Seletor de equipe no topo, com "todas" = null, escondido na fila do robô
// (a fila é por marca e tem a tela dela).
assert.match(
  tpl,
  /<select\s+v-if="statusFilter !== 'fila'"\s+v-model="equipeFiltro"/,
  'seletor de equipe no topo, fora da fila do robô',
)
assert.match(tpl, /<option :value="null">todas as equipes<\/option>/)
assert.match(tpl, /v-for="e in equipeOpcoes"[^>]*:value="e\.chave"/, 'opções pela chave da equipe')
// Contador por equipe: um botão por equipe que filtra com um clique.
assert.match(tpl, /v-for="e in resumoEquipes"/)
assert.match(tpl, /@click="alternaEquipe\(e\.chave\)"/, 'clicar no contador filtra')
assert.match(tpl, /Vídeos enviados por equipe:/)
// A linha de adicionar fica EM CIMA dos dias (a nova entra no dia de hoje,
// que agora é o primeiro grupo) — e continua só pra quem edita.
const addAt = tpl.indexOf('<!-- add row')
const diasAt = tpl.indexOf('<tbody v-for="g in gruposPorDia"')
assert.ok(addAt > 0 && diasAt > addAt, 'linha de adicionar antes dos grupos por dia')
assert.match(tpl.slice(addAt, diasAt), /<tr v-if="canEdit"/, 'adicionar continua só pra quem edita')
assert.equal((tpl.match(/<!-- add row/g) || []).length, 1, 'uma linha de adicionar só')

// ---------------------------------------------------------------- helpers puros
const start = script.indexOf('// ---------- helpers puros')
const end = script.indexOf('// ---------- fim helpers puros')
assert.ok(start > 0 && end > start, 'marcadores dos helpers puros presentes')
const H = new Function('isoToday', 'PLATAFORMA_LABELS', 'MARCAS_ERROS',
  transpile(script.slice(start, end), ts.ModuleKind.ESNext) +
  '\nreturn { momentoDaLinha, agrupaPorDia, equipeChave, resumoPorEquipe, diaDaLinha };',
)(dateLib.isoToday, redes.PLATAFORMA_LABELS, apiError.MARCAS_ERROS)

const vid = (enviado_em) => ({ file_mime: 'video/mp4', enviado_em })
const img = { file_mime: 'image/png', enviado_em: '2026-10-12T20:00:00Z' }

// momentoDaLinha: o vídeo mais novo manda; imagem não; sem vídeo, a linha.
assert.equal(
  H.momentoDaLinha({ created_at: '2026-10-01T10:00:00Z', files: [vid('2026-10-02T10:00:00Z'), vid('2026-10-03T09:00:00Z'), img] }),
  Date.parse('2026-10-03T09:00:00Z'),
)
assert.equal(H.momentoDaLinha({ created_at: '2026-10-01T10:00:00Z', files: [img] }), Date.parse('2026-10-01T10:00:00Z'))
assert.equal(H.momentoDaLinha({ created_at: '2026-10-01T10:00:00Z', files: [vid(null)] }), Date.parse('2026-10-01T10:00:00Z'))
assert.equal(H.momentoDaLinha({ created_at: null, files: [] }), 0, 'sem data = 0 (vai pro fim)')
assert.equal(H.momentoDaLinha({ created_at: 'lixo', files: [vid('lixo')] }), 0, 'data inválida não vira NaN')

// agrupaPorDia: dia mais novo em cima; dentro do dia, o vídeo mais novo em
// cima; empate de hora → a linha criada por último (a API manda crescente).
{
  const g = H.agrupaPorDia([
    { id: 'velha', created_at: '2026-10-10T12:00:00Z', files: [vid('2026-10-10T12:00:00Z')] },
    { id: 'tarde', created_at: '2026-10-10T13:00:00Z', files: [vid('2026-10-12T18:00:00Z')] }, // chegou 12/10
    { id: 'semdata', created_at: null, files: [] },
    { id: 'manha', created_at: '2026-10-12T11:00:00Z', files: [vid('2026-10-12T11:00:00Z')] },
    { id: 'empate1', created_at: '2026-10-11T09:00:00Z', files: [vid('2026-10-11T15:00:00Z')] },
    { id: 'empate2', created_at: '2026-10-11T10:00:00Z', files: [vid('2026-10-11T15:00:00Z')] },
    { id: 'noite', created_at: '2026-10-13T01:00:00Z', files: [] }, // 12/10 22h BRT
  ])
  assert.deepEqual(g.map((x) => x.dia), ['2026-10-12', '2026-10-11', '2026-10-10', ''])
  assert.deepEqual(g[0].rows.map((r) => r.id), ['noite', 'tarde', 'manha'], 'hora BRT do mais novo pro mais velho')
  assert.deepEqual(g[1].rows.map((r) => r.id), ['empate2', 'empate1'], 'empate: a criada por último em cima')
  assert.deepEqual(g[3].rows.map((r) => r.id), ['semdata'])
  assert.equal(g[0].videos, 2)
  assert.equal(g[0].criativos, 3)
}

// equipeChave: sem caixa e sem espaço nas pontas, como o backend.
assert.equal(H.equipeChave('  Bill Gates '), 'bill gates')
assert.equal(H.equipeChave(null), '')
assert.equal(H.equipeChave('   '), '')

// resumoPorEquipe: vídeo = ARQUIVO de vídeo; mais vídeos primeiro, empate
// pelo nome; grafias diferentes da mesma equipe somam; "sem equipe" no fim.
{
  const r = H.resumoPorEquipe([
    { equipe: 'Mindset', files: [vid(null), vid(null), img] },
    { equipe: 'Bill Gates', files: [vid(null)] },
    { equipe: null, files: [vid(null), vid(null), vid(null), vid(null)] },
    { equipe: 'bill gates ', files: [vid(null), vid(null)] },
    { equipe: 'Agência X', files: [vid(null), vid(null), vid(null)] },
    { equipe: 'Mindset', files: [] },
  ])
  assert.deepEqual(r.map((e) => [e.nome, e.videos, e.criativos]), [
    ['Agência X', 3, 1],
    ['Bill Gates', 3, 2],
    ['Mindset', 2, 2],
    ['sem equipe', 4, 1],
  ])
  assert.deepEqual(r.map((e) => e.chave), ['agência x', 'bill gates', 'mindset', ''])
  assert.deepEqual(H.resumoPorEquipe([]), [])
  // A grafia vem da lista de equipes (/creatives/equipes) quando ela conhece.
  const g = H.resumoPorEquipe([{ equipe: 'bill gates', files: [vid(null)] }, { equipe: 'Nova', files: [] }], ['Bill Gates', 'Mindset'])
  assert.deepEqual(g.map((e) => e.nome), ['Bill Gates', 'Nova'])
}

// ---------------------------------------------------------------- script setup
const pageScript = script.replace(/^import[\s\S]*?from\s+'[^']+'\s*$/gm, '')
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const factory = new AsyncFunction(
  'ref', 'computed', 'nextTick', 'onMounted', 'onBeforeUnmount', 'reactive',
  'useApi', 'useToasts', 'useAuthStore', 'useCan',
  'apiErrMsg', 'MARCAS_ERROS', 'isoToday', 'PLATAFORMA_LABELS', 'window', 'setTimeout',
  'defineEmits',
  transpile(pageScript, ts.ModuleKind.ESNext) + `
return {
  rows, q, statusFilter, dataDe, dataAte, equipeFiltro, filteredRows,
  resumoEquipes, equipeOpcoes, alternaEquipe, gruposPorDia, videosFiltrados, newRow, addRow,
}`,
)

const arq = (id, enviado_em) => ({ id, file_name: `${id}.mp4`, file_mime: 'video/mp4', file_size: 1, enviado_em })
function criativo(over) {
  return {
    id: 'x', modelo: 'video 30s', marca: 'uranyx', marca_id: null, sku: 'SKU', equipe: null,
    files: [], aprovado: null, pushed_at: null, pushed_dest: null, created_at: null, ...over,
  }
}
const LINHAS = [
  criativo({ id: 'bg1', equipe: 'Bill Gates', sku: 'A1', aprovado: true, created_at: '2026-10-10T12:00:00Z', files: [arq('v1', '2026-10-10T12:00:00Z')] }),
  criativo({ id: 'ms1', equipe: 'Mindset', sku: 'B1', aprovado: true, created_at: '2026-10-11T12:00:00Z', files: [arq('v2', '2026-10-11T12:00:00Z'), arq('v3', '2026-10-11T12:05:00Z')] }),
  criativo({ id: 'bg2', equipe: 'bill gates', sku: 'A2', aprovado: null, created_at: '2026-10-12T12:00:00Z', files: [arq('v4', '2026-10-12T12:00:00Z')] }),
  criativo({ id: 'ms2', equipe: 'Mindset', sku: 'B2', aprovado: false, created_at: '2026-10-12T13:00:00Z', files: [arq('v5', '2026-10-12T13:00:00Z')] }),
  criativo({ id: 'sem', equipe: null, sku: 'C1', aprovado: null, created_at: '2026-10-09T12:00:00Z', files: [] }),
]

async function tela(linhas = LINHAS) {
  const calls = []
  const api = (url, opts) => {
    calls.push({ url, opts })
    if (url === '/api/marketing/creatives' && !opts?.method) return Promise.resolve(linhas.map((l) => ({ ...l })))
    if (url === '/api/marketing/creatives' && opts?.method === 'POST') {
      return Promise.resolve(criativo({ id: 'nova', ...opts.body, created_at: new Date().toISOString() }))
    }
    if (url === '/api/marketing/creatives/equipes') return Promise.resolve(['Bill Gates', 'Mindset'])
    if (url === '/api/marcas?ativo=true') return Promise.resolve([{ id: 'm1', nome: 'Uranyx', slug: 'uranyx', ativo: true }])
    if (url === '/api/marketing/postagens') return Promise.resolve([])
    return Promise.reject(new Error(`api falso não conhece ${url}`))
  }
  const toasts = { success() {}, error() {}, warning() {}, info() {}, push: () => 1, dismiss() {} }
  const s = await factory(
    Vue.ref, Vue.computed, Vue.nextTick, (fn) => fn(), () => {}, Vue.reactive,
    () => ({ api }), () => toasts, () => ({ user: { role: 'admin' } }), () => Vue.ref(true),
    apiError.apiErrMsg, apiError.MARCAS_ERROS, dateLib.isoToday, redes.PLATAFORMA_LABELS,
    { confirm: () => true, addEventListener() {}, removeEventListener() {} },
    (fn) => fn,
    () => () => {},
  )
  await new Promise(setImmediate)
  return { s, calls }
}

const resumo = (s) => s.resumoEquipes.value.map((e) => [e.nome, e.videos])

async function run() {
  // Sem filtro: contador de todas as equipes, mais novos em cima.
  {
    const { s, calls } = await tela()
    assert.ok(!calls.some((c) => c.url.includes('equipe=')), 'filtro de equipe não vai pro servidor')
    assert.equal(s.equipeFiltro.value, null, 'começa em "todas as equipes"')
    assert.deepEqual(resumo(s), [['Mindset', 3], ['Bill Gates', 2], ['sem equipe', 0]])
    assert.deepEqual(s.equipeOpcoes.value.map((e) => e.nome), ['Bill Gates', 'Mindset', 'sem equipe'], 'opções em ordem alfabética')
    assert.deepEqual(s.gruposPorDia.value.map((g) => g.dia), ['2026-10-12', '2026-10-11', '2026-10-10', '2026-10-09'])
    assert.deepEqual(s.gruposPorDia.value[0].rows.map((r) => r.id), ['ms2', 'bg2'])
    assert.equal(s.videosFiltrados.value, 5)

    // Contador sem equipe escolhida segue texto, status e datas.
    s.statusFilter.value = 'aprovado'
    assert.deepEqual(resumo(s), [['Mindset', 2], ['Bill Gates', 1]])
    s.statusFilter.value = 'todos'
    s.dataDe.value = '2026-10-12'
    assert.deepEqual(resumo(s), [['Bill Gates', 1], ['Mindset', 1]], 'empate pelo nome')
    s.dataDe.value = ''
    s.q.value = 'B2'
    assert.deepEqual(resumo(s), [['Mindset', 1]])
    s.q.value = ''

    // Equipe escolhida: a tabela só dela (as duas grafias) e o contador
    // também — só ela, com o mesmo número do topo.
    s.equipeFiltro.value = 'bill gates'
    assert.deepEqual(s.filteredRows.value.map((r) => r.id), ['bg1', 'bg2'])
    assert.equal(s.videosFiltrados.value, 2)
    assert.deepEqual(s.gruposPorDia.value.map((g) => g.dia), ['2026-10-12', '2026-10-10'])
    assert.deepEqual(resumo(s), [['Bill Gates', 2]])

    // Soma com texto, status e datas — e o contador também.
    s.statusFilter.value = 'aprovado'
    assert.deepEqual(s.filteredRows.value.map((r) => r.id), ['bg1'])
    assert.deepEqual(resumo(s), [['Bill Gates', 1]])
    s.statusFilter.value = 'todos'

    s.dataDe.value = '2026-10-12'
    assert.deepEqual(s.filteredRows.value.map((r) => r.id), ['bg2'])
    assert.deepEqual(resumo(s), [['Bill Gates', 1]])
    s.dataDe.value = ''

    s.q.value = 'B2'
    assert.deepEqual(s.filteredRows.value, [], 'texto + equipe')
    // A equipe escolhida sem nada no recorte continua no contador, com 0 —
    // é nela que se clica pra voltar a "todas".
    assert.deepEqual(resumo(s), [['Bill Gates', 0]])
    s.q.value = ''

    // Clique no contador: escolhe; clique de novo na mesma: volta a todas.
    s.alternaEquipe('mindset')
    assert.equal(s.equipeFiltro.value, 'mindset')
    assert.deepEqual(s.filteredRows.value.map((r) => r.id), ['ms1', 'ms2'])
    s.alternaEquipe('mindset')
    assert.equal(s.equipeFiltro.value, null)
    assert.equal(s.filteredRows.value.length, 5)

    // "sem equipe" é filtrável também.
    s.alternaEquipe('')
    assert.deepEqual(s.filteredRows.value.map((r) => r.id), ['sem'])
    assert.deepEqual(resumo(s), [['sem equipe', 0]])
    s.alternaEquipe('')

    // A última linha da equipe escolhida sai (apagada ou trocada de equipe):
    // a opção continua no seletor (não fica em branco) e o contador mostra 0.
    s.alternaEquipe('mindset')
    s.rows.value = s.rows.value.filter((r) => r.equipe !== 'Mindset')
    assert.deepEqual(s.filteredRows.value, [])
    assert.deepEqual(s.equipeOpcoes.value.map((e) => [e.chave, e.nome]), [['bill gates', 'Bill Gates'], ['mindset', 'Mindset'], ['', 'sem equipe']])
    assert.deepEqual(resumo(s), [['Mindset', 0]])
    s.alternaEquipe('mindset')
    assert.deepEqual(s.equipeOpcoes.value.map((e) => e.nome), ['Bill Gates', 'sem equipe'], 'sem filtro, some de novo')
    assert.deepEqual(resumo(s), [['Bill Gates', 2], ['sem equipe', 0]])
  }

  // Linha nova: entra no dia de hoje, que é o primeiro grupo (em cima).
  {
    const { s, calls } = await tela()
    s.newRow.modelo = 'video 30s'
    s.newRow.equipe = 'Mindset'
    await s.addRow()
    const post = calls.find((c) => c.opts?.method === 'POST')
    assert.deepEqual(post.opts.body, { modelo: 'video 30s', marca: null, sku: null, equipe: 'Mindset' }, 'POST igual ao de antes')
    assert.equal(s.gruposPorDia.value[0].rows[0].id, 'nova', 'a linha nova aparece em cima')
    assert.deepEqual(resumo(s)[0], ['Mindset', 3], 'contador conta linha, vídeo é o que soma')
    assert.equal(s.resumoEquipes.value[0].criativos, 3)
  }

  // Sem linhas: nada de contador nem de opções.
  {
    const { s } = await tela([])
    assert.deepEqual(s.resumoEquipes.value, [])
    assert.deepEqual(s.equipeOpcoes.value, [])
  }
}

run().then(() => {
  console.log('PASS: Criativos — mais novos em cima (dia e hora do vídeo), contador de vídeos por equipe (dentro de todos os filtros; clique filtra), seletor de equipe somando com os outros filtros, linha de adicionar em cima')
}).catch((e) => {
  console.error(e)
  process.exit(1)
})
