// Run from apps/web: node tests/marketing-lives-sfc.cjs
//
// Marketing › Lives (03/11/2026): components/MarketingLives.vue e os helpers
// puros em utils/lives.ts. Trava: o link decide a plataforma (Shopee/TikTok,
// e host parecido não passa), métrica ausente é travessão e não 0, o aviso de
// "robô sem sinal" e a aba no Marketing. Só dados falsos; nenhuma rede.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const { parse, compileTemplate, compileScript } = require('vue/compiler-sfc')

const transpile = (source) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText

// ---------------------------------------------------------------- SFC
const filename = path.join(__dirname, '..', 'components', 'MarketingLives.vue')
const source = fs.readFileSync(filename, 'utf8')
const { descriptor, errors } = parse(source, { filename })
assert.deepEqual(errors, [], 'SFC parseia')
const compilado = compileTemplate({ source: descriptor.template.content, filename, id: 'lives-check' })
assert.deepEqual(compilado.errors, [], 'template compila')
new Function('exports', 'require', transpile(compilado.code))({}, require)
compileScript(descriptor, { id: 'lives-check' })
const script = descriptor.scriptSetup.content
const tpl = descriptor.template.content

// ---------------------------------------------------------------- helpers puros
const utils = fs.readFileSync(path.join(__dirname, '..', 'utils', 'lives.ts'), 'utf8')
const ini = utils.indexOf('// ---------- helpers puros')
const fim = utils.indexOf('// ---------- fim helpers puros')
assert.ok(ini > 0 && fim > ini, 'marcadores dos helpers presentes')
const H = {}
new Function('exports', transpile(utils.slice(ini, fim)))(H)

assert.equal(H.plataformaDoLink('https://live.shopee.com.br/share?from=live&session=123'), 'shopee')
assert.equal(H.plataformaDoLink(' https://s.shopee.com.br/abc '), 'shopee')
assert.equal(H.plataformaDoLink('https://shp.ee/x'), 'shopee')
assert.equal(H.plataformaDoLink('https://www.tiktok.com/@loja/live'), 'tiktok')
assert.equal(H.plataformaDoLink('https://vm.tiktok.com/ZM123/'), 'tiktok')
assert.equal(H.plataformaDoLink('https://shopee.com.br.golpe.com/x'), null, 'host parecido não passa')
assert.equal(H.plataformaDoLink('live.shopee.com.br/share'), null, 'sem https não é link')
assert.equal(H.plataformaDoLink(''), null)

assert.equal(H.duracaoTxt(null), '—')
assert.equal(H.duracaoTxt(45), '45 s')
assert.equal(H.duracaoTxt(600), '10 min')
assert.equal(H.duracaoTxt(5430), '1 h 30 min')
assert.equal(H.tamanhoTxt(null), '—')
assert.equal(H.tamanhoTxt(17302976), '17 MB')
assert.equal(H.tamanhoTxt(1.5 * 1024 ** 3), '1,5 GB')
assert.equal(H.numTxt(undefined), '—', 'métrica ausente é travessão')
assert.equal(H.numTxt(0), '0', 'zero medido aparece')
assert.equal(H.numTxt(6641), '6.641')

const t = Date.parse('2026-11-03T20:10:00Z')
assert.equal(H.minutosSemSinal(null, t), null)
assert.equal(H.minutosSemSinal('2026-11-03T20:05:30Z', t), 4)

assert.equal(H.sparkline([[0, 10, 0]]), '', 'um ponto só não desenha')
assert.equal(H.sparkline([[0, 6000, 0], [60, 6400, 0]], 100, 20), '0.0,19.0 100.0,1.0', 'escala do menor ao maior')
assert.equal(H.sparkline([[0, null, 0], [60, 5, 0], [120, 10, 0]], 100, 20), '0.0,19.0 100.0,1.0', 'ponto sem número fica fora')
assert.equal(H.sparkline([[0, 7, 0], [60, 7, 0]], 100, 20), '0.0,10.0 100.0,10.0', 'série plana no meio')
assert.match(tpl, /<div v-if="l\.inicio_em" class="grid/, 'sem gravação, sem grade de métricas')

// ---------------------------------------------------------------- regras da tela
assert.match(script, /useCan\('marketing_criativos', 'edit'\)/, 'editar pede marketing_criativos.edit')
assert.match(tpl, /roboSumiu\(l\)/, 'aviso de robô sem sinal na tela')
assert.match(script, /m >= 3/, 'robô some depois de 3 min sem sinal')
assert.match(tpl, /l\.status === 'aguardando' \|\| l\.status === 'erro'/, 'só apaga aguardando/erro')
assert.match(script, /\/api\/marketing\/lives\/\$\{l\.id\}\/regravar/, 'tentar de novo chama /regravar')

const marketing = fs.readFileSync(path.join(__dirname, '..', 'pages', 'marketing.vue'), 'utf8')
assert.match(marketing, /<MarketingLives v-else-if="aba === 'lives' && canCriativos" \/>/, 'aba Lives no Marketing')
assert.match(marketing, /q === 'lives'\) && canCriativos\.value/, '?aba=lives abre a aba')

console.log('marketing-lives-sfc: ok')
