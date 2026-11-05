// node tests/devolucoes-busca-unidades.cjs
// "Adicionar pedido" conta UNIDADES, não SKUs (302146, 05/11): kit com 3 malas
// b056.24, uma excluída para relançar como Usado — o "já feito" por pedido+sku
// (tabela carregada e criadas na sessão) esmaecia as 3 e o botão ficava em
// "usar 0 produtos — todos os produtos deste pedido já foram adicionados".
//
// Roda o código REAL da página, extraído do <script setup>, com o Vue de verdade.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const { parse } = require('vue/compiler-sfc')
const { ref, computed } = require('vue')

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
// Template: a linha da busca esmaece por isAlreadyAdded(row) e a chave do v-for
// não pode repetir quando o pedido tem a mesma peça mais de uma vez.
const tpl = page.descriptor.template.content
assert.match(tpl, /v-for="\(row, i\) in expandedLookupResults"/)
assert.match(tpl, /:key="`\$\{row\.pedido_bling\}-\$\{row\.sku\}-\$\{row\.conta\}-\$\{i\}`"/)

const fonte = [
  pickVar('addedThisSession'),
  pickVar('expandedLookupResults'),
  pickVar('alreadyAddedRows'),
  pick('isAlreadyAdded'),
  pickVar('selectableLookupCount'),
  pick('selectAllProducts'),
  pick('lookupOrder'),
  'return { addedThisSession, expandedLookupResults, isAlreadyAdded, selectableLookupCount, selectAllProducts, lookupOrder }',
].join('\n')

function montar(linhas, { items = [], respostaBusca = [] } = {}) {
  const lookupResults = ref(linhas)
  const drafts = ref([])
  const st = {
    lookupResults, drafts,
    items: ref(items),
    lookupPedido: ref('302146'),
    lookupLoading: ref(false),
    lookupError: ref(null),
    videoLinkPorPedido: ref(new Map()),
  }
  const fn = new Function(
    'ref', 'computed', 'lookupResults', 'drafts', 'items', 'lookupPedido', 'lookupLoading',
    'lookupError', 'videoLinkPorPedido', 'api', 'apiError', transpile(fonte),
  )
  const api = async () => respostaBusca
  const pg = fn(
    ref, computed, lookupResults, drafts, st.items, st.lookupPedido, st.lookupLoading,
    st.lookupError, st.videoLinkPorPedido, api, (e) => String(e),
  )
  return { ...st, ...pg }
}

const linha = (sku, custo, ja = false) => ({
  pedido_bling: '302146', pedido_marketplace: '2610083JUEV0XV', conta: 'Shopee KFA',
  sku, custo_produto: custo, quantidade: 1, ja_devolvido: ja,
})

async function main() {
  // 1) 302146 como o backend devolve agora: 8 lançadas, a b056.24 de 126
  //    excluída volta livre — "usar 1 produto" e o rascunho é ela.
  {
    const busca = [
      linha('b047.18', 100, true), linha('b047.18', 100, true),
      linha('b047.24', 126, true), linha('b047.24', 126, true),
      linha('b056.18', 100, true), linha('b056.18', 100, true),
      linha('b056.24', 126, true), linha('b056.24', 126, false), linha('b056.24', 130, true),
    ]
    // A tabela carregada tem as 2 b056.24 que ficaram: não pode esconder a 3ª.
    const t = montar(busca, { items: [linha('b056.24', 126), linha('b056.24', 130)] })
    assert.equal(t.selectableLookupCount.value, 1)
    t.selectAllProducts()
    assert.equal(t.drafts.value.length, 1)
    assert.equal(t.drafts.value[0].sku, 'b056.24')
    assert.equal(t.drafts.value[0].custo_produto, 126)
    assert.equal(t.drafts.value[0].condicao_produto, '')
  }

  // 2) Criadas NESTA busca contam uma unidade cada: 3 b056.24 livres, 2
  //    criadas → sobra 1 (antes, a 1ª criada já escondia as 3).
  {
    const t = montar([linha('b056.24', 126), linha('b056.24', 126), linha('b056.24', 130)])
    assert.equal(t.selectableLookupCount.value, 3)
    t.addedThisSession.value = ['302146|b056.24']
    assert.equal(t.selectableLookupCount.value, 2)
    t.addedThisSession.value = ['302146|b056.24', '302146|b056.24']
    assert.equal(t.selectableLookupCount.value, 1)
    t.addedThisSession.value = ['302146|b056.24', '302146|b056.24', '302146|b056.24', '302146|b056.24']
    assert.equal(t.selectableLookupCount.value, 0)
    // Outro SKU do mesmo pedido não é afetado.
    const u = montar([linha('b056.24', 126), linha('b056.18', 100)])
    u.addedThisSession.value = ['302146|b056.24']
    assert.deepEqual(u.expandedLookupResults.value.map((r) => u.isAlreadyAdded(r)), [true, false])
  }

  // 3) Buscar de novo zera as criadas na sessão: o backend já conta com elas,
  //    somar de novo esconderia unidades a mais.
  {
    const nova = [linha('b056.24', 126, true), linha('b056.24', 126, false), linha('b056.24', 130, true)]
    const t = montar([], { respostaBusca: nova })
    t.addedThisSession.value = ['302146|b056.24', '302146|b056.24']
    await t.lookupOrder()
    assert.deepEqual(t.addedThisSession.value, [])
    assert.equal(t.selectableLookupCount.value, 1)
  }

  // 4) Pedido inteiro já lançado continua em "usar 0".
  {
    const t = montar([linha('b056.24', 126, true), linha('b056.24', 130, true)])
    assert.equal(t.selectableLookupCount.value, 0)
  }

  // 5) Excluir na lista com o painel aberto no mesmo pedido busca de novo (a
  //    unidade volta na hora); com rascunho na tela, ou outro pedido, não.
  {
    const fonteRemove = [pick('removeRow'), 'return { removeRow }'].join('\n')
    const rodar = async ({ painel, rascunhos, pedidoBusca }) => {
      let buscas = 0
      const fn = new Function(
        'canDelete', 'isDeleting', 'pushToast', 'temEstornoPendente', 'confirm', 'textoDoQueDesfaz',
        'deleting', 'error', 'api', 'items', 'total', 'refreshTotals', 'avisarExclusao', 'apiError',
        'aplicarEstoque', 'addOpen', 'drafts', 'lookupResults', 'lookupOrder', transpile(fonteRemove),
      )
      const { removeRow } = fn(
        { value: true }, () => false, () => 0, () => false, () => true, () => '',
        ref(new Set()), ref(null), async () => ({}), ref([{ id: 'x', pedido_bling: '302146' }]), ref(9),
        () => {}, () => {}, String, () => {},
        ref(painel), ref(rascunhos), ref([linha('b056.24', 126, true)].map((r) => ({ ...r, pedido_bling: pedidoBusca }))),
        async () => { buscas += 1 },
      )
      await removeRow({ id: 'x', pedido_bling: '302146', sku: 'b056.24', estoque_mov_sku: 'b056.24' })
      return buscas
    }
    assert.equal(await rodar({ painel: true, rascunhos: [], pedidoBusca: '302146' }), 1)
    assert.equal(await rodar({ painel: true, rascunhos: [{ sku: 'b047.18' }], pedidoBusca: '302146' }), 0)
    assert.equal(await rodar({ painel: true, rascunhos: [], pedidoBusca: '999999' }), 0)
    assert.equal(await rodar({ painel: false, rascunhos: [], pedidoBusca: '302146' }), 0)
  }

  // 6) A unidade criada é contada pela chave da linha da busca (rascunho), não
  //    pelo SKU que o servidor devolve limpo.
  assert.match(src, /addedThisSession\.value = \[\.\.\.addedThisSession\.value, `\$\{d\.pedido_bling\}\|\$\{d\.sku\}`\]/)

  console.log('devolucoes-busca-unidades: ok')
}

main().catch((e) => { console.error(e); process.exit(1) })
