// Run from apps/web: node tests/categorias-ordem.cjs
//
// M-23 (23/11/2026, prévia aprovada pelo dono): em TODO lugar onde as
// categorias aparecem — botões, abas, grupos de colunas, seletores, chips,
// filtros — a ordem é a alfabética: Celular, Eletro, Mala. O que não é
// categoria (Faturamento, Geral, Catálogo, Shein, Sem categoria…) fica depois.
// É só ordem de exibição: nada de dado, cálculo, permissão, nem a categoria
// em que cada tela abre (as que abriam em Celular continuam).
//
// Trava:
//  - a lista única (lib/categorias.ts) e o ordenador dela;
//  - a mesma lista no servidor (app/services/categorias_ordem.py) e o Excel
//    da Conferência (saida.py) montado a partir dela;
//  - cada tela que lista as três: Tabela de Preços (botões, inclusive os que
//    vêm de /api/segments, que no banco estão Celular, Mala, Eletro),
//    Marketing › Conferência (colunas e o select do grupo da conta),
//    Horários dos anúncios, Importação, Produtos, Garantias, Faturamento,
//    Lojas (tipos e faturador por tipo), Cadastros › Segmentos;
//  - varredura: qualquer lista do código com Celular e outra categoria fora
//    da ordem quebra este teste (para não voltar a divergir). As listas de
//    TAGS de estoque (CI, PI, …, Mala, Eletro, Insumos) não têm Celular e não
//    são as categorias: ficam de fora.
// Só dados FALSOS aqui; nenhuma rede.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')

const WEB = path.join(__dirname, '..')
const ler = (rel) => fs.readFileSync(path.join(WEB, rel), 'utf8')
const transpile = (src) => ts.transpileModule(src, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText
function lib(rel) {
  const exp = {}
  new Function('exports', 'require', transpile(ler(rel)))(exp, () => { throw new Error(`${rel} não pode importar nada`) })
  return exp
}

const ORDEM = ['celular', 'eletro', 'mala']

// ---------------------------------------------------------------- a lista única
const C = lib('lib/categorias.ts')
assert.deepEqual([...C.ORDEM_CATEGORIAS], ORDEM, 'Celular, Eletro, Mala')
assert.deepEqual([...C.ORDEM_CATEGORIAS], [...C.ORDEM_CATEGORIAS].sort(), 'é a ordem alfabética')
assert.deepEqual(C.ORDEM_CATEGORIAS.map((c) => C.ROTULO_CATEGORIA[c]), ['Celular', 'Eletro', 'Mala'])
assert.deepEqual(['mala', 'eletro', 'celular'].map(C.posicaoCategoria), [2, 1, 0])
assert.equal(C.posicaoCategoria(' Mala '), 2, 'maiúscula e espaço não atrapalham')
for (const fora of ['geral', 'catalogo', 'sem', 'faturamento', '', null, undefined]) {
  assert.equal(C.posicaoCategoria(fora), 3, `${fora}: depois das categorias`)
}
{
  const entrada = ['catalogo', 'mala', 'sem', 'celular', 'shein', 'eletro']
  assert.deepEqual(C.ordenarCategorias(entrada), ['celular', 'eletro', 'mala', 'catalogo', 'sem', 'shein'],
    'o resto depois, na ordem em que veio')
  assert.deepEqual(entrada, ['catalogo', 'mala', 'sem', 'celular', 'shein', 'eletro'], 'não mexe na original')
  const objs = [{ s: 'mala', n: 1 }, { s: 'x', n: 2 }, { s: 'celular', n: 3 }, { s: 'y', n: 4 }]
  assert.deepEqual(C.ordenarCategorias(objs, (o) => o.s).map((o) => o.n), [3, 1, 2, 4])
}
assert.ok(!/^import /m.test(ler('lib/categorias.ts')), 'lib sem import (roda solta nos testes)')

// ---------------------------------------------------------------- servidor
{
  const py = ler('../api/app/services/categorias_ordem.py')
  const m = /ORDEM_CATEGORIAS: tuple\[str, \.\.\.\] = \(([^)]*)\)/.exec(py)
  assert.ok(m, 'ORDEM_CATEGORIAS no servidor')
  assert.deepEqual([...m[1].matchAll(/"(\w+)"/g)].map((x) => x[1]), ORDEM, 'a mesma lista no servidor')
  const saida = ler('../api/app/services/conferencia_shopee/saida.py')
  assert.match(saida, /GRUPOS_PLANILHA[^=]*= \(\n\s*\*\(\(chave, ROTULO_GRUPO\[chave\]\) for chave in ORDEM_CATEGORIAS\),\n\s*\("geral", "Geral"\),/,
    'o Excel/HTML da Conferência sai da lista única, com o Geral por último')
}

// DaVinci ERP (admin): o rateio por centro de custo e o "Tipo de produto" da empresa por loja.
{
  const ops = ler('../api/app/erp_local/static/operations.js')
  const cc = [...ops.matchAll(/<label>(Celular|Eletro|Mala)<input name="cc_(\w+)"/g)].map((m) => m[2])
  assert.deepEqual(cc, ORDEM, 'ERP: centro de custo Celular, Eletro, Mala')
  const mc = /PRODUCT_TYPES = \(([^)]*)\)/.exec(ler('../api/app/erp_local/multi_company.py'))
  assert.deepEqual([...mc[1].matchAll(/"(\w+)"/g)].map((x) => x[1]), ORDEM, 'ERP: tipo de produto')
}

// M-23 revisão (23/11/2026): os textos que listam as categorias também. A
// descrição do aviso do Threema da Conferência (a mensagem já sai Celular,
// Eletro, Mala, Geral) e as ajudas do DaVinci ERP. O como-usar.vtt é legenda
// do vídeo gravado: segue o áudio, fica de fora.
{
  const conf = ler('components/MarketingConferencia.vue')
  assert.ok(conf.includes('s/ vendas de Celular, Eletro, Mala e Geral'), 'Conferência: texto do aviso do Threema')
  const pasta = path.join(WEB, '../api/app/erp_local/static')
  const velhos = []
  for (const f of fs.readdirSync(pasta).filter((x) => x.endsWith('.js'))) {
    const s = fs.readFileSync(path.join(pasta, f), 'utf8')
    for (const m of s.matchAll(/(Celular|Mala|Eletro)(?:, | e | ou | · |\/)(Celular|Mala|Eletro)(?:,? e |,? ou |, | · |\/)(Celular|Mala|Eletro)/g)) {
      if (m.slice(1, 4).join() !== 'Celular,Eletro,Mala') velhos.push(`${f}: ${m[0]}`)
    }
  }
  assert.deepEqual(velhos, [], 'ERP: textos com as categorias fora da ordem')
  assert.ok(ler('../api/app/erp_local/static/app.js').includes('rateio entre Celular, Eletro e Mala'))
  assert.ok(ler('../api/app/erp_local/static/fulfillment.js').includes('(Celular, Eletro ou Mala)'))
}

// ---------------------------------------------------------------- libs que não importam nada
{
  const conf = lib('lib/conferencia.ts')
  assert.deepEqual(conf.GRUPOS_PLANILHA.map((g) => g.chave), [...ORDEM, 'geral'], 'Conferência: Celular, Eletro, Mala, Geral')
  assert.deepEqual(conf.GRUPOS_PLANILHA.map((g) => g.rotulo), ['Celular', 'Eletro', 'Mala', 'Geral'])
  const gar = lib('lib/garantias.ts')
  assert.deepEqual(gar.DEPARTAMENTOS.map((d) => d.chave), ORDEM, 'Garantias: as pílulas')
  assert.equal(gar.departamentoDaUrl(undefined), 'celular', 'Garantias continua abrindo em Celular')
}

// ---------------------------------------------------------------- listas fixas das telas
/** As categorias do array que começa em `ancora`, na ordem em que aparecem. */
function categoriasDoBloco(rel, ancora) {
  const fonte = ler(rel)
  const i = fonte.indexOf(ancora)
  assert.ok(i >= 0, `${rel}: ${ancora}`)
  const fim = fonte.indexOf('\n]', i)
  const bloco = fonte.slice(i, fim < 0 ? undefined : fim)
  return [...bloco.matchAll(/(?:value|key|chave|slug)\s*:\s*'(celular|eletro|mala)'/g)].map((m) => m[1])
}
for (const [rel, ancora, esperado] of [
  ['pages/pricing/[tab].vue', 'const DEPARTMENTS_FALLBACK', ORDEM],
  ['pages/importacao.vue', 'const CATEGORIAS =', ORDEM],
  ['pages/produtos.vue', 'const DEPARTAMENTOS:', ORDEM],
  ['components/FaturamentoPainel.vue', 'const departmentOptions =', ORDEM],
  ['components/MarketingHorarios.vue', 'const SEGMENTOS:', ORDEM],
  ['pages/store-info.vue', 'const DEPARTMENTS =', ORDEM],
  ['pages/store-info.vue', 'const TIPO_OPTIONS:', ORDEM],
]) assert.deepEqual(categoriasDoBloco(rel, ancora), esperado, `${rel}: ${ancora}`)

// Quem recebe a lista de fora ordena pela lista única.
{
  const pricing = ler('pages/pricing/[tab].vue')
  assert.match(pricing, /import \{ ordenarCategorias \} from '~\/lib\/categorias'/)
  const seg = ler('pages/admin/segments.vue')
  assert.match(seg, /tree\.value = ordenarCategorias\(await api<TreeNode\[\]>\('\/api\/segments\/tree'\), \(n\) => n\.slug\)/,
    'Cadastros › Segmentos: as raízes em ordem alfabética')
  const lojas = ler('pages/store-info.vue')
  assert.match(lojas, /const DEPT_ORDER = \[\.\.\.ORDEM_CATEGORIAS, 'catalogo', 'shein'\]/, 'Lojas: faturador por tipo')
  assert.match(lojas, /v-for="d in tiposEmOrdem\(row\)"/, 'Lojas: os tipos do cartão')
  assert.match(lojas, /v-for="d in tiposEmOrdem\(row\)\.filter/, 'Lojas: as etiquetas da coluna Tipo')
  const imp = ler('pages/importacao.vue')
  assert.match(imp, /const CATEGORIAS = ordenarCategorias\(\[/, 'Importação pela lista única')
  // Horários: abre na primeira opção (Celular), não mais em Mala.
  const hor = ler('components/MarketingHorarios.vue')
  assert.match(hor, /const SEGMENTO_PADRAO: Segmento = SEGMENTOS\[0\]\.chave/)
  assert.match(hor, /const segmento = ref<Segmento>\(SEGMENTO_PADRAO\)/)
  assert.match(hor, /segLembrado \?\? segUrl \?\? SEGMENTO_PADRAO/)
  assert.ok(!/\?\? 'mala'|ref<Segmento>\('mala'\)/.test(hor), 'nada mais abre em Mala de fábrica')
  // Conferência: o select do grupo da conta e a ordem da lista de contas.
  const conf = ler('components/MarketingConferencia.vue')
  const opcoes = [...conf.matchAll(/<option value="(celular|eletro|mala)">/g)].map((m) => m[1])
  assert.deepEqual(opcoes, ['celular', 'mala'], 'select do grupo: Celular, Mala (a Conferência só tem esses 2 grupos de conta)')
  assert.match(conf, /posicaoGrupo\(a\.grupo\) - posicaoGrupo\(b\.grupo\)/, 'contas: Celular antes de Mala')
}

// ---------------------------------------------------------------- Tabela de Preços: os botões vindos de /api/segments
// O banco guarda as raízes em sort_order Celular 0, Mala 1, Eletro 2, Catálogo 3
// (produção, 23/11/2026). A barra mostra Celular, Eletro, Mala; os tipos de
// cada categoria (as colunas/slots) continuam no sort_order.
;(async () => {
  const fonte = ler('pages/pricing/[tab].vue')
  const ini = fonte.indexOf('async function loadSegments()')
  const fim = fonte.indexOf('\n}\n', ini)
  const codigo = transpile(fonte.slice(ini, fim + 2)).replace(/^"use strict";?/, '') + '\nreturn loadSegments'
  const rows = [
    { id: 'r-cel', parent_id: null, name: 'Celular', slug: 'celular', sort_order: 0, active: true },
    { id: 'r-mala', parent_id: null, name: 'Mala', slug: 'mala', sort_order: 1, active: true },
    { id: 'r-ele', parent_id: null, name: 'Eletro', slug: 'eletro', sort_order: 2, active: true },
    { id: 'r-cat', parent_id: null, name: 'Catálogo', slug: 'catalogo', sort_order: 3, active: true },
    { id: 'r-nova', parent_id: null, name: 'Nova', slug: 'nova', sort_order: 4, active: true },
    { id: 'm2', parent_id: 'r-mala', name: '12"', slug: 'm12', sort_order: 1, active: true },
    { id: 'm1', parent_id: 'r-mala', name: 'Acessórios', slug: 'ma', sort_order: 0, active: true },
    { id: 'e1', parent_id: 'r-ele', name: '1', slug: 'e1', sort_order: 0, active: true },
  ]
  const ref = (v) => ({ value: v })
  const DEPARTMENTS = ref([])
  const TYPE_HEADERS = ref({})
  const allSegments = ref([])
  const load = new Function(
    'api', 'allSegments', 'DEPTS_FORA_DA_BARRA', 'DEPARTMENTS', 'DEPT_ICONS', 'Tags', 'TYPE_HEADERS',
    'TYPE_HEADERS_FALLBACK', 'ordenarCategorias', codigo,
  )(
    async (url) => { assert.equal(url, '/api/segments'); return rows },
    allSegments, new Set(['catalogo']), DEPARTMENTS, {}, 'Tags', TYPE_HEADERS, {}, C.ordenarCategorias,
  )
  await load()
  assert.deepEqual(DEPARTMENTS.value.map((d) => d.label), ['Celular', 'Eletro', 'Mala', 'Nova'],
    'Tabela de Preços: Celular, Eletro, Mala (e o Faturamento é um botão depois deles)')
  assert.deepEqual(TYPE_HEADERS.value.mala, ['Acessórios', '12"'], 'os tipos da Mala continuam no sort_order')
  assert.deepEqual(rows.map((r) => r.id).slice(0, 3), ['r-cel', 'r-mala', 'r-ele'], 'a resposta da API não é mexida')
  const pricing = fonte
  assert.match(pricing, /return DEPARTMENTS\.value\.some\(\(d\) => d\.value === v\) \? v : 'celular'/, 'continua abrindo em Celular')
  const botoes = pricing.indexOf('v-for="d in DEPARTMENTS"')
  assert.ok(botoes > 0 && pricing.indexOf('data-botao-faturamento') > botoes, 'Faturamento depois das categorias')

  // ---------------------------------------------------------------- varredura
  // Qualquer lista com Celular e outra categoria precisa estar na ordem. (Os
  // tipos `'mala' | 'eletro' | 'celular'` e os mapas `{ mala: … }` não são
  // listas de tela e não entram.)
  const PASTAS = ['pages', 'components', 'lib', 'composables', 'utils', 'layouts', 'stores']
  const arquivos = []
  const andar = (d) => {
    for (const f of fs.readdirSync(d)) {
      const p = path.join(d, f)
      if (fs.statSync(p).isDirectory()) andar(p)
      else if (/\.(vue|ts)$/.test(f)) arquivos.push(p)
    }
  }
  for (const d of PASTAS) if (fs.existsSync(path.join(WEB, d))) andar(path.join(WEB, d))
  const fora = []
  for (const arq of arquivos) {
    const s = fs.readFileSync(arq, 'utf8')
    const rel = path.relative(WEB, arq)
    const grupos = []
    let grupo = []
    let ultimo = -1e9
    for (const m of s.matchAll(/(?:(?:value|key|chave|slug)\s*:\s*'(celular|eletro|mala)'|<option value="(celular|eletro|mala)")/g)) {
      if (m.index - ultimo > 400 && grupo.length) { grupos.push(grupo); grupo = [] }
      grupo.push({ k: m[1] || m[2], i: m.index })
      ultimo = m.index
    }
    if (grupo.length) grupos.push(grupo)
    for (const m of s.matchAll(/\[\s*'(celular|eletro|mala)'\s*,\s*'(celular|eletro|mala)'(?:\s*,\s*'(celular|eletro|mala)')?/g)) {
      grupos.push([m[1], m[2], m[3]].filter(Boolean).map((k) => ({ k, i: m.index })))
    }
    for (const g of grupos) {
      const ks = g.map((x) => x.k)
      if (!ks.includes('celular') || ks.length < 2) continue // tags de estoque (Mala, Eletro) não são as categorias
      const pos = ks.map((k) => ORDEM.indexOf(k))
      if (!pos.every((p, i) => i === 0 || p >= pos[i - 1])) {
        fora.push(`${rel}:${s.slice(0, g[0].i).split('\n').length} ${ks.join(', ')}`)
      }
    }
  }
  assert.deepEqual(fora, [], 'listas com as categorias fora da ordem Celular, Eletro, Mala')
  assert.ok(arquivos.length > 100, 'varreu as telas')

  console.log('categorias-ordem: ok')
})().catch((e) => { console.error(e); process.exit(1) })
