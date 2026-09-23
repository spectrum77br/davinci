// Tela de Desempenho (components/MarketingDesempenho.vue).
//
// Trava a regra que mais fácil se perde ao mexer na tela: métrica que NINGUÉM
// reportou aparece como "—", nunca como 0. O YouTube não mede salvamento; o
// Instagram só dá views com uma permissão que o token ainda não tem. Escrever
// 0 ali afirmaria que ninguém salvou — coisa que a gente não sabe.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const { parse, compileTemplate } = require('vue/compiler-sfc')

const arquivo = path.join(__dirname, '..', 'components', 'MarketingDesempenho.vue')
const src = fs.readFileSync(arquivo, 'utf8')

const { descriptor, errors } = parse(src)
assert.equal(errors.length, 0, 'SFC parseia')
const compilado = compileTemplate({ source: descriptor.template.content, id: 'x', filename: arquivo })
assert.equal(compilado.errors.length, 0, 'template compila')

// ---- helpers puros
const script = descriptor.scriptSetup.content
const ini = script.indexOf('// ---------- helpers puros')
const fim = script.indexOf('// ---------- fim helpers puros')
assert.ok(ini > 0 && fim > ini, 'marcadores dos helpers presentes')
const js = ts.transpileModule(script.slice(ini, fim), {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext },
}).outputText
const H = new Function(js + '\nreturn { num, ganho, coleta };')()

// Ausente vira travessão. Zero é zero de verdade e aparece.
assert.equal(H.num({ views: 1234 }, 'views'), '1.234', 'formata em pt-BR')
assert.equal(H.num({ views: 0 }, 'views'), '0', 'zero medido aparece como zero')
assert.equal(H.num({}, 'salvamentos'), '—', 'não reportado vira travessão, não 0')
assert.equal(H.num({ salvamentos: null }, 'salvamentos'), '—', 'nulo também')

// O ganho no período só aparece quando existe e é diferente de zero.
assert.equal(H.ganho({ views: 200 }, 'views'), '+200')
assert.equal(H.ganho({ views: 0 }, 'views'), '', 'ganho zero não polui a tela')
assert.equal(H.ganho({}, 'views'), '', 'sem dado, sem ganho')

// "Coletado em": leitura velha precisa avisar, senão a tela mostra número
// antigo com cara de novo e ninguém percebe.
const agora = new Date().toISOString()
const tresDias = new Date(Date.now() - 3 * 864e5).toISOString()
assert.equal(H.coleta({ coletado_em: agora }).velha, false)
assert.equal(H.coleta({ coletado_em: tresDias }).velha, true, 'passou de 2 dias, avisa')
assert.equal(H.coleta({ coletado_em: null }).velha, true, 'nunca coletado é o pior caso')
assert.match(H.coleta({ coletado_em: null }).texto, /nunca/)

// Higiene. A checagem é sobre CREDENCIAL, não sobre a palavra: o componente
// comenta que "o token ainda não tem a permissão de insights", e proibir o
// termo proibiria explicar o porquê das coisas. O que não pode é a tela ler,
// guardar ou mandar credencial.
const codigo = descriptor.scriptSetup.content + descriptor.template.content
assert.ok(!/Authorization|Bearer\s|access_token|refresh_token|client_secret/i.test(codigo),
  'a tela não manda nem lê credencial')
assert.ok(!/\b(senha|password)\s*[:=]/i.test(codigo), 'nenhum campo de senha')
// O endpoint que ela chama é só o de leitura de métricas.
const urls = [...codigo.matchAll(/['\`](\/api\/[^'\`$]*)/g)].map((m) => m[1])
assert.deepEqual(urls, ['/api/marketing/metricas?dias='], 'só fala com o endpoint de métricas')

console.log('PASS: SFC parseia e compila; ausente ≠ zero; ganho zero não polui; leitura velha avisa')
