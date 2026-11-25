// Run from apps/web: node tests/pricing-contas-anotacoes.cjs
// M-11 (18/11/2026): na aba Contas, as anotações viram campos próprios, sempre
// à mostra, que gravam ao sair do campo ou com Enter. M-55 (25/11/2026): a
// ordem fica Afiliado | Desconto | Ads | Custo total | Obs ("Desconto até",
// "Cupom até" e "Oferta limite" saem da tela), com colunas de ~62px e a
// caixinha de 34×24px. M-56: Custo total só o ADM muda (os outros veem), e o
// 422 custo_total_estourado volta o valor com o aviso junto do campo. Executa
// as funções reais da página e confere o template.
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

// ---------- 1) os campos e a leitura dos valores digitados
const canEditContas = { value: true }
const isAdmin = { value: false }
const erroCustoTotal = { value: null }
const avisos = []
const toast = { error: (titulo, texto) => avisos.push(`${titulo} | ${texto}`) }
const gravados = []
const _patchAccount = async (id, campo, raw) => { gravados.push([id, campo, raw]) }
const f = new Function('canEditContas', 'isAdmin', 'erroCustoTotal', 'toast', '_patchAccount', js([
  constante('STORE_NOTE_FIELDS'),
  funcao('lerPct'), funcao('podeEditarAnotacao'), funcao('textoAnotacao'), funcao('erroCustoTotalDe'),
  funcao('estourouCustoTotal'), funcao('gravarAnotacao'), funcao('desfazerAnotacao'),
  'return { STORE_NOTE_FIELDS, lerPct, podeEditarAnotacao, textoAnotacao, erroCustoTotalDe, estourouCustoTotal, gravarAnotacao, desfazerAnotacao }',
].join('\n')))(canEditContas, isAdmin, erroCustoTotal, toast, _patchAccount)

assert.deepEqual(
  f.STORE_NOTE_FIELDS.map((c) => `${c.label}:${c.key}:${c.tipo ?? ''}${c.soAdmin ? ':adm' : ''}`),
  ['Afiliado:afiliado_pct:pct', 'Desconto:desconto_pct:pct', 'Ads:ads:', 'Custo total:custo_total_pct:pct:adm', 'Obs:observation:'],
)
for (const fora of ['desconto_ate', 'cupom_ate', 'oferta_limite_pct', 'lerDiaMes', 'DIAS_NO_MES']) {
  assert.ok(!setup.includes(fora), `${fora} saiu da tela`)
}
// Afiliado, Desconto e Custo total usam o lerPct da margem: só número, com decimal.
assert.equal(source.statements.filter((s) => ts.isFunctionDeclaration(s) && s.name?.text === 'lerPct').length, 1)
for (const [digitado, esperado] of [
  ['4', 4], ['4,5', 4.5], ['4.5', 4.5], ['0', 0], ['100', 100], ['', null],
  ['101', undefined], ['-1', undefined], ['7%', undefined], ['abc', undefined], ['1.234,5', undefined],
]) assert.equal(f.lerPct(digitado), esperado, `% ${JSON.stringify(digitado)}`)
console.log('pricing contas anotações: leitura ok')

// ---------- 2) gravação ao sair do campo
const campo = (k) => f.STORE_NOTE_FIELDS.find((c) => c.key === k)
const conta = { id: 'c1', afiliado_pct: '4.00', desconto_pct: null, custo_total_pct: '20.00' }
const evento = (valor) => ({ target: { value: valor, blur() { this.borrado = true } } })

assert.equal(f.textoAnotacao(conta, campo('afiliado_pct')), '4')
assert.equal(f.textoAnotacao(conta, campo('desconto_pct')), '')
assert.equal(f.textoAnotacao(conta, campo('custo_total_pct')), '20')
assert.equal(f.textoAnotacao({ afiliado_pct: '4.50' }, campo('afiliado_pct')), '4,5')

let ev = evento('4')
f.gravarAnotacao(conta, campo('afiliado_pct'), ev)
assert.equal(gravados.length, 0, 'igual ao atual não grava')

ev = evento('7.5')
f.gravarAnotacao(conta, campo('desconto_pct'), ev)
assert.equal(ev.target.value, '7,5')
assert.deepEqual(gravados.pop(), ['c1', 'desconto_pct', '7,5'], 'Desconto: mesmas regras do Afiliado')

ev = evento('')
f.gravarAnotacao(conta, campo('afiliado_pct'), ev)
assert.deepEqual(gravados.pop(), ['c1', 'afiliado_pct', ''], 'apagar grava vazio')

ev = evento('abc')
f.gravarAnotacao(conta, campo('afiliado_pct'), ev)
assert.equal(ev.target.value, '4')
assert.equal(gravados.length, 0)
assert.match(avisos.pop(), /^Afiliado: valor inválido \| Use só número de 0 a 100/)

ev = evento('9')
f.desfazerAnotacao(conta, campo('desconto_pct'), ev)
assert.equal(ev.target.value, '')
assert.ok(ev.target.borrado, 'Esc volta e sai do campo')

// Custo total: só o ADM muda; quem edita Contas muda Afiliado e Desconto.
assert.equal(f.podeEditarAnotacao(campo('custo_total_pct')), false)
assert.equal(f.podeEditarAnotacao(campo('desconto_pct')), true)
f.gravarAnotacao(conta, campo('custo_total_pct'), evento('15'))
assert.equal(gravados.length, 0, 'não ADM não grava o Custo total')
isAdmin.value = true
f.gravarAnotacao(conta, campo('custo_total_pct'), evento('15'))
assert.deepEqual(gravados.pop(), ['c1', 'custo_total_pct', '15'])

// Aviso do Custo total estourado: só no campo recusado; gravar de novo o
// mesmo campo tira o aviso antes de mandar.
const MSG = 'Afiliado 4% + Desconto 1% + Ads 6% = 11%. Passa do Custo total (10%). Não gravado.'
assert.equal(f.erroCustoTotalDe({ data: { detail: { code: 'custo_total_estourado', mensagem: MSG } } }), MSG)
assert.equal(f.erroCustoTotalDe({ data: { detail: { code: 'campos_so_admin' } } }), null)
erroCustoTotal.value = { id: 'c1', campo: 'desconto_pct', mensagem: MSG }
assert.equal(f.estourouCustoTotal(conta, campo('desconto_pct').key), MSG)
assert.equal(f.estourouCustoTotal(conta, 'afiliado_pct'), null)
assert.equal(f.estourouCustoTotal({ id: 'c2' }, 'desconto_pct'), null)
f.gravarAnotacao(conta, campo('desconto_pct'), evento('0,5'))
assert.equal(erroCustoTotal.value, null)
assert.deepEqual(gravados.pop(), ['c1', 'desconto_pct', '0,5'])

canEditContas.value = false
isAdmin.value = false
f.gravarAnotacao(conta, campo('afiliado_pct'), evento('9'))
assert.equal(gravados.length, 0, 'sem editar Contas não grava')
console.log('pricing contas anotações: gravação ok')

// ---------- 3) _patchAccount manda o valor normalizado e volta no estouro
const patch = funcao('_patchAccount')
assert.match(patch, /field === 'afiliado_pct' \|\| field === 'desconto_pct' \|\| field === 'custo_total_pct'\) \{\s*const v = lerPct\(raw\)\s*if \(v === undefined\) return\s*payload\[field\] = v/)
assert.match(patch, /const estouro = erroCustoTotalDe\(e\)\s*if \(estouro\) \{\s*Object\.assign\(acc, antes\)\s*erroCustoTotal\.value = \{ id, campo: field, mensagem: estouro \}\s*return\s*\}/)
assert.match(setup, /afiliado_pct: string \| number \| null[\s\S]*?desconto_pct\?: string \| number \| null\s*custo_total_pct\?: string \| number \| null/)
console.log('pricing contas anotações: script ok')

// ---------- 4) template: campos sempre à mostra, colunas de ~62px, caixinha 34×24px
const tpl = descriptor.template.content
const contas = tpl.slice(tpl.indexOf("tab === 'contas'"), tpl.indexOf("tab === 'produtos'"))
const cab = contas.slice(0, contas.indexOf('</thead>'))
assert.match(cab, /v-for="f in STORE_NOTE_FIELDS"[\s\S]*?:class="f\.key === 'observation' \? 'text-left min-w-\[120px\]' : 'text-center w-\[62px\] min-w-\[62px\]'"[\s\S]*?>\{\{ f\.label \}\}<\/th>/)
assert.doesNotMatch(cab, /capitalize/)
for (const fora of ['Desconto até', 'Cupom até', 'Oferta limite']) assert.ok(!contas.includes(fora), `${fora} saiu`)
const celulas = contas.slice(contas.indexOf('<!-- afiliado / desconto / ads / custo total / obs (M-55/M-56) -->'))
const proprio = celulas.slice(0, celulas.indexOf('v-else'))
assert.match(proprio, /<td\s+v-if="f\.tipo"\s+class="relative border border-border px-1 py-1 text-center w-\[62px\] min-w-\[62px\]"/)
const input = proprio.slice(proprio.indexOf('<input'), proprio.indexOf('/>'))
for (const trecho of [
  ':value="textoAnotacao(acc, f)"',
  'maxlength="6"',
  ':readonly="!podeEditarAnotacao(f)"',
  'class="h-6 w-[34px] min-w-0 rounded border px-0.5 text-center text-xs"',
  "podeEditarAnotacao(f) ? 'bg-background' : 'bg-transparent cursor-default'",
  "{ 'border-destructive ring-1 ring-destructive': estourouCustoTotal(acc, f.key) }",
  ':data-campo="`conta-${f.key}`"',
  '@blur="gravarAnotacao(acc, f, $event)"',
  '@keydown.enter.prevent="($event.target as HTMLInputElement).blur()"',
  '@keydown.escape.prevent="desfazerAnotacao(acc, f, $event)"',
]) assert.ok(input.includes(trecho), `input tem ${trecho}`)
assert.doesNotMatch(input, /v-if/, 'campo sempre à mostra, mesmo vazio')
assert.match(proprio, /<span class="text-xs text-muted-foreground">%<\/span>/)
assert.match(proprio, /v-if="estourouCustoTotal\(acc, f\.key\)"[\s\S]*?data-aviso-custo-total[\s\S]*?\{\{ estourouCustoTotal\(acc, f\.key\) \}\}/)
// Ads: mesma largura da coluna.
assert.match(celulas, /v-else-if="f\.key === 'ads'"\s+class="border border-border px-1 py-1 text-center w-\[62px\] min-w-\[62px\]"/)
// Obs continua como antes (clique para editar).
assert.match(celulas, /<td\s+v-else\s+class="border border-border px-2 py-1\.5 text-xs cursor-pointer text-left max-w-\[260px\]"/)
console.log('pricing contas anotações: template ok')
console.log('pricing-contas-anotacoes: ok')
