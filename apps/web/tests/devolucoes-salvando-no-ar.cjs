// node tests/devolucoes-salvando-no-ar.cjs
// Salvamento lento da linha (lançamento no Bling) não pode parecer erro nem
// apagar o que foi digitado enquanto ele estava no ar.
//
// Caso 294247 (25/09): Condição = Usado → zXXXX. O PATCH levou 15,7 s (Bling
// devolvendo 429) e nesse tempo a linha mostrava só "não salvo" — a operadora
// achou que não tinha lançado, mas o z0340 foi criado com 1 unidade. E uma
// edição feita nesse meio-tempo era descartada (saveRow saía cedo com o save
// no ar) e a resposta ainda sobrescrevia a linha, apagando o texto da tela.
//
// Roda as funções REAIS da página, extraídas do <script setup>.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const { parse } = require('vue/compiler-sfc')

const transpile = (source) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText

const pageFile = path.join(__dirname, '../pages/devolucoes.vue')
const page = parse(fs.readFileSync(pageFile, 'utf8'), { filename: pageFile })
assert.deepEqual(page.errors, [])
const src = page.descriptor.scriptSetup.content
const ast = ts.createSourceFile('devolucoes.ts', src, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)

function pick(name) {
  const node = ast.statements.find((n) => ts.isFunctionDeclaration(n) && n.name?.text === name)
  assert.ok(node, `a página ainda tem a função ${name}`)
  return src.slice(node.getStart(ast), node.getEnd())
}
function pickVar(name) {
  const node = ast.statements.find(
    (n) => ts.isVariableStatement(n) && n.declarationList.declarations.some((d) => d.name.text === name),
  )
  assert.ok(node, `a página ainda tem ${name}`)
  return src.slice(node.getStart(ast), node.getEnd())
}

const fonte = [
  'let _toastId = 0',
  pickVar('rowVersion'),
  pickVar('resaveRows'),
  pick('pushToast'),
  pick('dismissToast'),
  pick('markDirty'),
  pick('clearDirty'),
  pick('hasDirty'),
  pick('setSaving'),
  pick('isSaving'),
  pick('setRowText'),
  pick('linkEnvioInvalido'),
  pick('linkEnvioLegado'),
  pick('rowPatchPayload'),
  pick('saveRow'),
].join('\n\n')

// PATCH controlado pelo teste: cada chamada fica pendurada até `responder`.
function montar(linhaInicial) {
  const ctx = {
    toasts: { value: [] },
    dirtyRows: { value: new Set() },
    savingRows: { value: new Set() },
    linkEnvioTocado: { value: new Set() },
    items: { value: [linhaInicial] },
    canEdit: { value: true },
    error: { value: null },
    chamadas: [],
    stockToasts: [],
  }
  const api = (url, init) => new Promise((resolve) => {
    ctx.chamadas.push({ url, body: JSON.parse(JSON.stringify(init.body)), responder: resolve })
  })
  const nomes = [
    'window', 'toasts', 'dirtyRows', 'savingRows', 'linkEnvioTocado', 'items', 'canEdit', 'error', 'api',
    'linkRequired', 'linkEnvioRequired', 'linkEnvioNaoMegaRow', 'videoFraudeRequired', 'bloqueiaSave',
    'showTrocaMotivoToast', 'platNome', 'refreshTotals', 'showStockToast',
  ]
  const fns = new Function(...nomes, transpile(`${fonte}
return { saveRow, setRowText, markDirty, hasDirty, isSaving };`))(
    { setTimeout: () => 0 },
    ctx.toasts, ctx.dirtyRows, ctx.savingRows, ctx.linkEnvioTocado, ctx.items, ctx.canEdit, ctx.error, api,
    () => false, () => false, () => null, () => false,
    (msg) => { throw new Error(`save barrado: ${msg}`) },
    () => {}, () => '', async () => {},
    (sr) => ctx.stockToasts.push(sr),
  )
  return { ctx, ...fns }
}

const tick = () => new Promise((r) => setImmediate(r))

function linha(extra) {
  return {
    id: 'row-294247',
    sku: 'dg011.pi',
    pedido_bling: '294247',
    cliente: 'Paulino',
    motivo_devolucao: 'Dano funcional / Não funciona',
    condicao_produto: 'Usado',
    link_abertura: 'https://drive.google.com/x',
    link_envio: null,
    custo_produto: null,
    custo_manutencao: 120,
    reembolso: true,
    tecnico: null,
    devolver_estoque: true,
    observacao: null,
    quantidade: 1,
    troca_sku: null,
    troca_condicao: null,
    estoque_suffix: null,
    manutencao_destino: null,
    estoque_destino_sku: null,
    estoque_nova_tag: '-',
    ...extra,
  }
}

async function main() {
  // 1) Save de estoque demorado: "salvando…" + aviso fixo enquanto está no ar;
  //    na resposta o aviso some, a linha fica salva e o resultado do Bling aparece.
  {
    const { ctx, saveRow, markDirty, hasDirty, isSaving } = montar(linha())
    const row = ctx.items.value[0]
    markDirty(row.id)
    const salvando = saveRow(row, { estoque: true })
    await tick()
    assert.equal(ctx.chamadas.length, 1, 'mandou o PATCH')
    assert.equal(isSaving(row.id), true, 'linha em "salvando…" enquanto o PATCH está no ar')
    assert.equal(ctx.toasts.value.length, 1)
    assert.equal(ctx.toasts.value[0].kind, 'info')
    assert.match(ctx.toasts.value[0].title, /Bling/)
    ctx.chamadas[0].responder({ ...linha(), cliente: null, bling_stock_result: { ok: true, action: 'product_created_usado' } })
    await salvando
    assert.equal(isSaving(row.id), false)
    assert.equal(hasDirty(row.id), false, 'salvo: sai o "não salvo"')
    assert.equal(ctx.toasts.value.length, 0, 'aviso de "Atualizando…" some com a resposta')
    assert.equal(ctx.stockToasts.length, 1, 'mostra o resultado do Bling')
    assert.equal(ctx.items.value[0].cliente, 'Paulino', 'cliente da listagem preservado')
  }

  // 2) Edição durante o PATCH: o texto digitado não some com a resposta e é
  //    salvo logo em seguida (antes o segundo save era descartado).
  {
    const { ctx, saveRow, setRowText, markDirty, hasDirty } = montar(linha())
    const row = ctx.items.value[0]
    markDirty(row.id)
    const salvando = saveRow(row, { estoque: true })
    await tick()
    setRowText(row, 'observacao', 'tela trincada')
    const segundo = saveRow(row) // blur do campo Observação com o save no ar
    await segundo
    assert.equal(ctx.chamadas.length, 1, 'não manda PATCH em paralelo')
    ctx.chamadas[0].responder({ ...linha(), observacao: null })
    await tick()
    assert.equal(ctx.items.value[0].observacao, 'tela trincada', 'a resposta não apaga o que foi digitado')
    assert.equal(ctx.chamadas.length, 2, 'salva a edição logo depois')
    assert.equal(ctx.chamadas[1].body.observacao, 'tela trincada')
    assert.equal(hasDirty(row.id), true, 'segue "não salvo" até o segundo PATCH responder')
    ctx.chamadas[1].responder({ ...linha(), observacao: 'tela trincada' })
    await salvando
    assert.equal(hasDirty(row.id), false)
    assert.equal(ctx.items.value[0].observacao, 'tela trincada')
  }

  // 3) Save comum (sem estoque) não mostra o aviso do Bling.
  {
    const { ctx, saveRow, markDirty } = montar(linha())
    const row = ctx.items.value[0]
    markDirty(row.id)
    const salvando = saveRow(row)
    await tick()
    assert.equal(ctx.toasts.value.length, 0)
    ctx.chamadas[0].responder(linha())
    await salvando
  }

  console.log('ok devolucoes-salvando-no-ar (3 casos)')
}

main().catch((e) => { console.error(e); process.exit(1) })
