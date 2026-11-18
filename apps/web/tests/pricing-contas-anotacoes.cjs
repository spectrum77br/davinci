// Run from apps/web: node tests/pricing-contas-anotacoes.cjs
// M-11 (18/11/2026): na aba Contas, Desconto até / Cupom até (dd/mm) e
// Afiliado / Oferta limite (%) viram campos próprios, sempre à mostra, que
// gravam ao sair do campo ou com Enter. Executa as funções reais da página e
// confere o template.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const { parse } = require('vue/compiler-sfc')

const filename = path.resolve(__dirname, '../pages/pricing/[tab].vue')
const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
assert.deepEqual(errors, [])
const setup = descriptor.scriptSetup.content
const source = ts.createSourceFile(filename, setup, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)

const js = (texto) => {
  const out = ts.transpileModule(texto, { compilerOptions: { target: ts.ScriptTarget.ES2022 }, reportDiagnostics: true })
  assert.deepEqual(out.diagnostics, [])
  return out.outputText
}
const funcao = (nome) => {
  const n = source.statements.find((s) => ts.isFunctionDeclaration(s) && s.name?.text === nome)
  assert.ok(n, `${nome} existe na página`)
  return n.getText(source)
}
const constante = (nome) => {
  const st = source.statements.find((s) => ts.isVariableStatement(s)
    && s.declarationList.declarations.some((d) => d.name.getText(source) === nome))
  assert.ok(st, `${nome} existe na página`)
  return st.getText(source)
}

// ---------- 1) leitura dos valores digitados
const canEditContas = { value: true }
const avisos = []
const toast = { error: (titulo, texto) => avisos.push(`${titulo} | ${texto}`) }
const gravados = []
const _patchAccount = async (id, campo, raw) => { gravados.push([id, campo, raw]) }
const f = new Function('canEditContas', 'toast', '_patchAccount', js([
  constante('STORE_NOTE_FIELDS'), constante('DIAS_NO_MES'),
  funcao('lerDiaMes'), funcao('lerPct'), funcao('textoAnotacao'),
  funcao('gravarAnotacao'), funcao('desfazerAnotacao'),
  'return { STORE_NOTE_FIELDS, lerDiaMes, lerPct, textoAnotacao, gravarAnotacao, desfazerAnotacao }',
].join('\n')))(canEditContas, toast, _patchAccount)

assert.deepEqual(
  f.STORE_NOTE_FIELDS.map((c) => `${c.label}:${c.key}:${c.tipo ?? ''}`),
  ['Desconto até:desconto_ate:data', 'Afiliado:afiliado_pct:pct', 'Ads:ads:', 'Cupom até:cupom_ate:data',
    'Oferta limite:oferta_limite_pct:pct', 'Obs:observation:'],
)
for (const [digitado, esperado] of [
  ['26/06', '26/06'], ['26.06', '26/06'], ['26,06', '26/06'], ['5/4', '05/04'], ['31-12', '31/12'], ['29/02', '29/02'],
  ['', null], ['  ', null],
  ['31/02', undefined], ['31/04', undefined], ['00/05', undefined], ['10/13', undefined],
  ['26/06/2026', undefined], ['abc', undefined], ['2606', undefined],
]) assert.equal(f.lerDiaMes(digitado), esperado, `data ${JSON.stringify(digitado)}`)
// Afiliado e Oferta limite usam o lerPct da margem: só número, com decimal.
assert.equal(source.statements.filter((s) => ts.isFunctionDeclaration(s) && s.name?.text === 'lerPct').length, 1)
for (const [digitado, esperado] of [
  ['4', 4], ['4,5', 4.5], ['4.5', 4.5], ['0', 0], ['100', 100], ['', null],
  ['101', undefined], ['-1', undefined], ['7%', undefined], ['abc', undefined], ['1.234,5', undefined],
]) assert.equal(f.lerPct(digitado), esperado, `% ${JSON.stringify(digitado)}`)
console.log('pricing contas anotações: leitura ok')

// ---------- 2) gravação ao sair do campo
const campo = (k) => f.STORE_NOTE_FIELDS.find((c) => c.key === k)
const conta = { id: 'c1', desconto_ate: '19/06', cupom_ate: null, afiliado_pct: '4.00', oferta_limite_pct: null }
const evento = (valor) => ({ target: { value: valor, blur() { this.borrado = true } } })

assert.equal(f.textoAnotacao(conta, campo('afiliado_pct')), '4')
assert.equal(f.textoAnotacao(conta, campo('oferta_limite_pct')), '')
assert.equal(f.textoAnotacao({ afiliado_pct: '4.50' }, campo('afiliado_pct')), '4,5')

let ev = evento('31.12')
f.gravarAnotacao(conta, campo('cupom_ate'), ev)
assert.equal(ev.target.value, '31/12')
assert.deepEqual(gravados.pop(), ['c1', 'cupom_ate', '31/12'])

ev = evento('4')
f.gravarAnotacao(conta, campo('afiliado_pct'), ev)
assert.equal(gravados.length, 0, 'igual ao atual não grava')

ev = evento('7,5')
f.gravarAnotacao(conta, campo('oferta_limite_pct'), ev)
assert.deepEqual(gravados.pop(), ['c1', 'oferta_limite_pct', '7,5'])

ev = evento('')
f.gravarAnotacao(conta, campo('desconto_ate'), ev)
assert.deepEqual(gravados.pop(), ['c1', 'desconto_ate', ''], 'apagar grava vazio')

ev = evento('31/02')
f.gravarAnotacao(conta, campo('desconto_ate'), ev)
assert.equal(ev.target.value, '19/06', 'data inválida volta ao que estava')
assert.equal(gravados.length, 0)
assert.match(avisos.pop(), /^Desconto até: valor inválido \| Use dia e mês que existem/)

ev = evento('abc')
f.gravarAnotacao(conta, campo('afiliado_pct'), ev)
assert.equal(ev.target.value, '4')
assert.match(avisos.pop(), /^Afiliado: valor inválido \| Use só número de 0 a 100/)

ev = evento('20/07')
f.desfazerAnotacao(conta, campo('desconto_ate'), ev)
assert.equal(ev.target.value, '19/06')
assert.ok(ev.target.borrado, 'Esc volta e sai do campo')

canEditContas.value = false
f.gravarAnotacao(conta, campo('cupom_ate'), evento('01/01'))
assert.equal(gravados.length, 0, 'sem editar Contas não grava')
console.log('pricing contas anotações: gravação ok')

// ---------- 3) _patchAccount manda o valor normalizado
const patch = funcao('_patchAccount')
assert.match(patch, /field === 'desconto_ate' \|\| field === 'cupom_ate'\) \{\s*const v = lerDiaMes\(raw\)\s*if \(v === undefined\) return\s*payload\[field\] = v/)
assert.match(patch, /field === 'afiliado_pct' \|\| field === 'oferta_limite_pct'\) \{\s*const v = lerPct\(raw\)\s*if \(v === undefined\) return\s*payload\[field\] = v/)
assert.match(setup, /desconto_ate: string \| null\s*cupom_ate: string \| null\s*afiliado_pct: string \| number \| null\s*oferta_limite_pct: string \| number \| null/)
console.log('pricing contas anotações: script ok')

// ---------- 4) template: campos sempre à mostra, 24px, ~84px, centralizados
const tpl = descriptor.template.content
const contas = tpl.slice(tpl.indexOf("tab === 'contas'"), tpl.indexOf("tab === 'produtos'"))
const cab = contas.slice(0, contas.indexOf('</thead>'))
assert.match(cab, /v-for="f in STORE_NOTE_FIELDS"[\s\S]*?:class="f\.tipo \? 'text-center w-\[84px\] min-w-\[84px\]' : 'text-left min-w-\[120px\]'"[\s\S]*?>\{\{ f\.label \}\}<\/th>/)
assert.doesNotMatch(cab, /capitalize/)
const celulas = contas.slice(contas.indexOf('<!-- desconto até / afiliado / ads / cupom até / oferta limite / obs -->'))
const proprio = celulas.slice(0, celulas.indexOf('v-else'))
assert.match(proprio, /<td\s+v-if="f\.tipo"\s+class="[^"]*text-center w-\[84px\] min-w-\[84px\]"/)
const input = proprio.slice(proprio.indexOf('<input'), proprio.indexOf('/>'))
for (const trecho of [
  ':value="textoAnotacao(acc, f)"',
  ':placeholder="f.tipo === \'data\' ? \'dd/mm\' : \'\'"',
  ':readonly="!canEditContas"',
  'h-6',
  ":class=\"canEditContas ? 'bg-background' : 'bg-transparent cursor-default'\"",
  'text-center',
  ':data-campo="`conta-${f.key}`"',
  '@blur="gravarAnotacao(acc, f, $event)"',
  '@keydown.enter.prevent="($event.target as HTMLInputElement).blur()"',
  '@keydown.escape.prevent="desfazerAnotacao(acc, f, $event)"',
]) assert.ok(input.includes(trecho), `input tem ${trecho}`)
assert.doesNotMatch(input, /v-if/, 'campo sempre à mostra, mesmo vazio')
assert.match(proprio, /<span v-if="f\.tipo === 'pct'" class="text-xs text-muted-foreground">%<\/span>/)
// Ads e Obs continuam como antes (clique para editar).
assert.match(celulas, /<td\s+v-else\s+class="border border-border px-2 py-1\.5 text-xs cursor-pointer text-left max-w-\[260px\]"/)
console.log('pricing contas anotações: template ok')
console.log('pricing-contas-anotacoes: ok')
