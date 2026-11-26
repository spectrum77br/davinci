// Run from apps/web: node tests/pricing-contas-anotacoes.cjs
// M-11 (18/11/2026): na aba Contas, as anotações viram campos próprios, sempre
// à mostra, que gravam ao sair do campo ou com Enter. M-55 (25/11/2026): a
// ordem fica Afiliado | Desconto | Ads | Custo total | Obs ("Desconto até",
// "Cupom até" e "Oferta limite" saem da tela), com colunas de ~62px e a
// caixinha de 34×24px. M-56: Custo total só o ADM muda (os outros veem), e o
// 422 custo_total_estourado volta o valor com o aviso junto do campo. M-85
// (26/11/2026): "Margem mínima" depois do Custo total, só o ADM vê e muda. M-86:
// com ela preenchida o Custo total é calculado (travado para todos, cinza, title
// "Calculado pela Margem mínima") e, abaixo de Afiliado + Desconto + Ads, fica
// com a borda vermelha e o aviso até alguém corrigir. Executa as funções reais
// da página e confere o template.
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
// O Ads e as contas da aba, para o aviso do Custo total abaixo da soma (M-86).
const ads = { value: null }
const accountsCurrent = { value: [] }
const computed = (fn) => ({ get value() { return fn() } })
const ref = (v) => ({ value: v })
const ADS_PADRAO = { ads_geral_ligada: true, pode_gastar_pct: null, gasto_dia: null, ads_por_produto_ligada: false, produtos: {} }
const nomesF = ['lerPct', 'podeEditarAnotacao', 'calculadoPelaMargemMinima', 'anotacaoTravada', 'tituloAnotacao',
  'textoAnotacao', 'erroCustoTotalDe', 'estourouCustoTotal', 'gravarAnotacao', 'desfazerAnotacao', 'adsDa', 'modoAds',
  'maiorAdsDaConta', 'avisoSomaAcimaDoCustoTotal', 'avisoNoCampo', 'avisoAberto', 'fecharAvisoCustoTotal']
const f = new Function('canEditContas', 'isAdmin', 'erroCustoTotal', 'toast', '_patchAccount', 'ads', 'accountsCurrent',
  'computed', 'ref', 'ADS_PADRAO', js([
    constante('STORE_NOTE_FIELDS'), constante('camposAnotacao'), constante('avisosCustoTotal'), constante('avisosFechados'),
    ...nomesF.map(funcao),
    `return { STORE_NOTE_FIELDS, camposAnotacao, avisosCustoTotal, avisosFechados, ${nomesF.join(', ')} }`,
  ].join('\n')))(canEditContas, isAdmin, erroCustoTotal, toast, _patchAccount, ads, accountsCurrent, computed, ref, ADS_PADRAO)

assert.deepEqual(
  f.STORE_NOTE_FIELDS.map((c) => `${c.label}:${c.key}:${c.tipo ?? ''}${c.soAdmin ? ':adm' : ''}${c.soAdminVe ? ':ve' : ''}`),
  ['Afiliado:afiliado_pct:pct', 'Desconto:desconto_pct:pct', 'Ads:ads:', 'Custo total:custo_total_pct:pct:adm',
    'Margem mínima:margem_minima_pct:pct:adm:ve', 'Obs:observation:'],
)
// M-85: a Margem mínima só aparece para o ADM; título em duas linhas e o title do pedido.
const margemMinima = f.STORE_NOTE_FIELDS.find((c) => c.key === 'margem_minima_pct')
assert.deepEqual(margemMinima.cabecalho, ['Margem', 'mínima'])
assert.equal(margemMinima.titulo, 'Margem mínima da conta (%). Só o administrador vê e muda.')
assert.deepEqual(f.camposAnotacao.value.map((c) => c.key),
  ['afiliado_pct', 'desconto_pct', 'ads', 'custo_total_pct', 'observation'], 'quem não é ADM não vê')
isAdmin.value = true
assert.deepEqual(f.camposAnotacao.value.map((c) => c.key),
  ['afiliado_pct', 'desconto_pct', 'ads', 'custo_total_pct', 'margem_minima_pct', 'observation'], 'ADM vê, depois do Custo total')
isAdmin.value = false
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
// Margem mínima (M-85): só o ADM muda, com as regras do lerPct.
assert.equal(f.podeEditarAnotacao(campo('margem_minima_pct')), true)
f.gravarAnotacao(conta, campo('margem_minima_pct'), evento('25'))
assert.deepEqual(gravados.pop(), ['c1', 'margem_minima_pct', '25'])
f.gravarAnotacao(conta, campo('margem_minima_pct'), evento('101'))
assert.equal(gravados.length, 0)
assert.match(avisos.pop(), /^Margem mínima: valor inválido/)
isAdmin.value = false
assert.equal(f.podeEditarAnotacao(campo('margem_minima_pct')), false)
f.gravarAnotacao(conta, campo('margem_minima_pct'), evento('20'))
assert.equal(gravados.length, 0, 'não ADM não grava a Margem mínima')
isAdmin.value = true

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

// ---------- 2b) M-86: Custo total calculado pela Margem mínima — travado para todos
canEditContas.value = true
isAdmin.value = true
const calculada = { id: 'c3', afiliado_pct: '1.00', desconto_pct: null, custo_total_pct: '6.00', margem_minima_pct: '25.00' }
const digitada = { id: 'c4', afiliado_pct: null, desconto_pct: null, custo_total_pct: '6.00', margem_minima_pct: null }
assert.equal(f.calculadoPelaMargemMinima(calculada, campo('custo_total_pct')), true)
assert.equal(f.calculadoPelaMargemMinima({ ...calculada, margem_minima_pct: '' }, campo('custo_total_pct')), false)
assert.equal(f.calculadoPelaMargemMinima(calculada, campo('margem_minima_pct')), false, 'só a caixinha do Custo total')
assert.equal(f.anotacaoTravada(calculada, campo('custo_total_pct')), true, 'travado até para o ADM')
assert.equal(f.anotacaoTravada(digitada, campo('custo_total_pct')), false)
assert.equal(f.anotacaoTravada(calculada, campo('afiliado_pct')), false, 'os outros campos seguem livres')
assert.equal(f.anotacaoTravada(calculada, campo('margem_minima_pct')), false)
f.gravarAnotacao(calculada, campo('custo_total_pct'), evento('9'))
assert.equal(gravados.length, 0, 'Custo total calculado não grava')
assert.equal(f.tituloAnotacao(calculada, campo('custo_total_pct')), 'Calculado pela Margem mínima')
assert.equal(f.tituloAnotacao(digitada, campo('custo_total_pct')), campo('custo_total_pct').titulo)
isAdmin.value = false
// Quem não é ADM não recebe a Margem mínima: vê o Custo total travado como sempre.
assert.equal(f.anotacaoTravada({ ...calculada, margem_minima_pct: null }, campo('custo_total_pct')), true)
assert.equal(f.tituloAnotacao({ ...calculada, margem_minima_pct: null }, campo('custo_total_pct')),
  'Só o administrador muda o Custo total')
isAdmin.value = true
console.log('pricing contas anotações: Custo total calculado ok')

// ---------- 2c) M-86 item 7: Custo total abaixo de Afiliado + Desconto + Ads
const geral = (pct) => ({ ...ADS_PADRAO, pode_gastar_pct: pct })
const porProduto = (produtos) => ({ ...ADS_PADRAO, ads_geral_ligada: false, ads_por_produto_ligada: true, produtos })
assert.equal(f.maiorAdsDaConta(geral(3), []), 3)
assert.equal(f.maiorAdsDaConta(geral(null), []), 0)
assert.equal(f.maiorAdsDaConta({ ...ADS_PADRAO, ads_geral_ligada: false }, ['p1']), 0, 'sem Ads')
// Com "Produto": o maior % próprio, só dos produtos da aba (os da janela de Ads).
const prods = porProduto({ p1: { pode_gastar_pct: 2 }, p2: { pode_gastar_pct: 5 }, fora: { pode_gastar_pct: 9 }, p3: { pode_gastar_pct: null } })
assert.equal(f.maiorAdsDaConta(prods, ['p1', 'p2', 'p3']), 5)
assert.equal(f.maiorAdsDaConta({ ...prods, pode_gastar_pct: 7 }, ['p1']), 2, 'com Produto a Geral não vale')

const aguiar = { id: 'ag', afiliado_pct: '4.00', desconto_pct: '2.00', custo_total_pct: '6.00', margem_minima_pct: '25.00' }
assert.equal(f.avisoSomaAcimaDoCustoTotal(aguiar, 3), 'Afiliado + Desconto + Ads (9%) passa do Custo total (6%)')
assert.equal(f.avisoSomaAcimaDoCustoTotal(aguiar, 0), null, 'igual ao teto cabe')
assert.equal(f.avisoSomaAcimaDoCustoTotal({ ...aguiar, custo_total_pct: null }, 3), null, 'sem Custo total não há teto')
// Centésimos: 0,1 + 0,2 = 0,3 cabe num teto de 0,3 (em float daria 0,30000000000000004).
assert.equal(f.avisoSomaAcimaDoCustoTotal({ ...aguiar, afiliado_pct: '0.10', desconto_pct: '0.20', custo_total_pct: '0.30' }, 0), null)
assert.equal(f.avisoSomaAcimaDoCustoTotal({ ...aguiar, afiliado_pct: '4.50', desconto_pct: '0.25', custo_total_pct: '0.00' }, 1.5),
  'Afiliado + Desconto + Ads (6,25%) passa do Custo total (0%)')

// Das contas da aba, com o Ads da janela; o X fecha a caixinha e a borda fica.
accountsCurrent.value = [aguiar, digitada]
assert.equal(f.avisoNoCampo(aguiar, campo('custo_total_pct')), null, 'sem o Ads carregado, Afiliado + Desconto = 6 cabe')
ads.value = { produtos: [{ id: 'p1' }], contas: { ag: geral(3), c4: geral(10) } }
assert.equal(f.avisoNoCampo(aguiar, campo('custo_total_pct')), 'Afiliado + Desconto + Ads (9%) passa do Custo total (6%)')
assert.equal(f.avisoNoCampo(aguiar, campo('afiliado_pct')), null, 'o aviso é da caixinha do Custo total')
assert.equal(f.avisoNoCampo(digitada, campo('custo_total_pct')), 'Afiliado + Desconto + Ads (10%) passa do Custo total (6%)')
assert.equal(f.tituloAnotacao(aguiar, campo('custo_total_pct')),
  'Calculado pela Margem mínima. Afiliado + Desconto + Ads (9%) passa do Custo total (6%)')
assert.equal(f.avisoAberto(aguiar, campo('custo_total_pct')), 'Afiliado + Desconto + Ads (9%) passa do Custo total (6%)')
f.fecharAvisoCustoTotal(aguiar)
assert.equal(f.avisoAberto(aguiar, campo('custo_total_pct')), null, 'fechado')
assert.ok(f.avisoNoCampo(aguiar, campo('custo_total_pct')), 'a borda continua')
ads.value.contas.ag = geral(4)
assert.equal(f.avisoAberto(aguiar, campo('custo_total_pct')), 'Afiliado + Desconto + Ads (10%) passa do Custo total (6%)',
  'outro valor reabre')
// Corrigido (Ads 0): some.
ads.value.contas.ag = geral(0)
assert.equal(f.avisoNoCampo(aguiar, campo('custo_total_pct')), null)
ads.value = null
accountsCurrent.value = []
console.log('pricing contas anotações: aviso do Custo total abaixo da soma ok')

// ---------- 3) _patchAccount manda o valor normalizado e volta no estouro
const patch = funcao('_patchAccount')
assert.match(patch, /field === 'afiliado_pct' \|\| field === 'desconto_pct' \|\| field === 'custo_total_pct' \|\| field === 'margem_minima_pct'\) \{\s*const v = lerPct\(raw\)\s*if \(v === undefined\) return\s*payload\[field\] = v/)
// M-86: o servidor recalcula o Custo total nas duas linhas do par; a irmã acompanha.
assert.match(patch, /espelharNaIrma\(acc, \[\.\.\.Object\.keys\(payload\), 'custo_total_pct'\]\)/)
assert.match(patch, /const estouro = erroCustoTotalDe\(e\)\s*if \(estouro\) \{\s*Object\.assign\(acc, antes\)\s*erroCustoTotal\.value = \{ id, campo: field, mensagem: estouro \}\s*return\s*\}/)
assert.match(setup, /afiliado_pct: string \| number \| null[\s\S]*?desconto_pct\?: string \| number \| null\s*custo_total_pct\?: string \| number \| null[\s\S]*?margem_minima_pct\?: string \| number \| null/)
console.log('pricing contas anotações: script ok')

// ---------- 4) template: campos sempre à mostra, colunas de ~62px, caixinha 34×24px
const tpl = descriptor.template.content
const contas = tpl.slice(tpl.indexOf("tab === 'contas'"), tpl.indexOf("tab === 'produtos'"))
const cab = contas.slice(0, contas.indexOf('</thead>'))
assert.match(cab, /v-for="f in camposAnotacao"[\s\S]*?:class="f\.key === 'observation' \? 'text-left min-w-\[120px\]' : 'text-center w-\[62px\] min-w-\[62px\]'"[\s\S]*?><template v-if="f\.cabecalho"><span v-for="parte in f\.cabecalho" :key="parte" class="block">\{\{ parte \}\}<\/span><\/template><template v-else>\{\{ f\.label \}\}<\/template><\/th>/)
assert.doesNotMatch(cab, /capitalize/)
for (const fora of ['Desconto até', 'Cupom até', 'Oferta limite']) assert.ok(!contas.includes(fora), `${fora} saiu`)
const celulas = contas.slice(contas.indexOf('<!-- afiliado / desconto / ads / custo total / margem mínima / obs (M-55/M-56/M-85) -->'))
assert.match(celulas, /^<!-- [^>]*-->\s*<template v-for="f in camposAnotacao" :key="`c-\$\{f\.key\}`">/)
const proprio = celulas.slice(0, celulas.indexOf('v-else'))
assert.match(proprio, /<td\s+v-if="f\.tipo"\s+class="relative border border-border px-1 py-1 text-center w-\[62px\] min-w-\[62px\]"/)
const input = proprio.slice(proprio.indexOf('<input'), proprio.indexOf('/>'))
for (const trecho of [
  ':value="textoAnotacao(acc, f)"',
  'maxlength="6"',
  ':readonly="anotacaoTravada(acc, f)"',
  'class="h-6 w-[34px] min-w-0 rounded border px-0.5 text-center text-xs"',
  "anotacaoTravada(acc, f) ? 'bg-transparent cursor-default' : 'bg-background'",
  "'text-muted-foreground': calculadoPelaMargemMinima(acc, f),",
  "'border-destructive ring-1 ring-destructive': estourouCustoTotal(acc, f.key) || avisoNoCampo(acc, f),",
  ':title="tituloAnotacao(acc, f)"',
  ':data-campo="`conta-${f.key}`"',
  '@blur="gravarAnotacao(acc, f, $event)"',
  '@keydown.enter.prevent="($event.target as HTMLInputElement).blur()"',
  '@keydown.escape.prevent="desfazerAnotacao(acc, f, $event)"',
]) assert.ok(input.includes(trecho), `input tem ${trecho}`)
assert.doesNotMatch(input, /v-if/, 'campo sempre à mostra, mesmo vazio')
assert.match(proprio, /<span class="text-xs text-muted-foreground">%<\/span>/)
assert.match(proprio, /v-if="estourouCustoTotal\(acc, f\.key\)"[\s\S]*?data-aviso-custo-total[\s\S]*?\{\{ estourouCustoTotal\(acc, f\.key\) \}\}/)
// M-86 item 7: o aviso do Custo total abaixo da soma, com o X que só fecha a caixinha.
const td = celulas.slice(0, celulas.indexOf('</td>'))
assert.match(td, /v-else-if="avisoAberto\(acc, f\)"[\s\S]*?data-aviso-custo-total-abaixo[\s\S]*?\{\{ avisoAberto\(acc, f\) \}\}[\s\S]*?@click="fecharAvisoCustoTotal\(acc\)"/)
// Ads: mesma largura da coluna.
assert.match(celulas, /v-else-if="f\.key === 'ads'"\s+class="border border-border px-1 py-1 text-center w-\[62px\] min-w-\[62px\]"/)
// Obs continua como antes (clique para editar).
assert.match(celulas, /<td\s+v-else\s+class="border border-border px-2 py-1\.5 text-xs cursor-pointer text-left max-w-\[260px\]"/)
console.log('pricing contas anotações: template ok')
console.log('pricing-contas-anotacoes: ok')
