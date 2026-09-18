// Run from apps/web: node tests/reembolso-sfc.cjs
// Reembolso (18/09/2026): parse + compileTemplate do SFC e execução do
// <script setup> com api falso, no molde de marcas-sfc.cjs.
//
// O que estas travas protegem, e por quê:
//  - A lista da tela é filtrada por equipe e por "a finalizar", e ordenada pela
//    DATA DO PEDIDO. Um lançamento de pedido antigo nasce fora da 1ª página, e
//    a pessoa conclui que não salvou e relança. Daí a confirmação persistente
//    (salvoAviso) com atalho pra achar a linha.
//  - O aviso de "já tem reembolso" precisa LISTAR o que existe: pedido com
//    vários lançamentos legítimos é comum (119 pedidos em produção), e um
//    contador solto ensina o operador a ignorar o alerta.
//  - A cadeia v-if/v-else-if do painel já foi quebrada uma vez ao inserir um
//    bloco no meio dela; o teste trava a vizinhança.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const { parse, compileTemplate } = require('vue/compiler-sfc')

const transpile = (source, module = ts.ModuleKind.CommonJS) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module },
}).outputText

// ---------------------------------------------------------------- SFC
const filename = path.join(__dirname, '../pages/reembolso.vue')
const sfcSource = fs.readFileSync(filename, 'utf8')
const { descriptor, errors } = parse(sfcSource, { filename })
assert.deepEqual(errors, [])
const compiled = compileTemplate({ source: descriptor.template.content, filename, id: 'reembolso-check' })
assert.deepEqual(compiled.errors, [])
new Function('exports', 'require', transpile(compiled.code))({}, require)

// ------------------------------------------------- peças exigidas do template
{
  const tpl = descriptor.template.content
  assert.match(tpl, /v-if="reembolsosExistentes > 0"/, 'aviso de reembolso já lançado')
  assert.match(tpl, /v-for="\(r, i\) in reembolsosDoPedido"/, 'o aviso LISTA o que já existe, não só o total')
  assert.match(tpl, /v-if="salvoAviso"/, 'confirmação do que acabou de ser salvo')
  assert.match(tpl, /@click="verSalvoNaLista"/, 'atalho pra achar a linha salva')
  assert.match(tpl, /role="alert"/, 'o aviso é anunciado por leitor de tela')
  // A cadeia do painel: historicoLoading e historicoDisponivel têm que seguir
  // irmãos adjacentes. Inserir um bloco entre os dois transforma o v-else-if
  // em "else" do bloco novo e muda o comportamento em silêncio.
  const cadeia = tpl.slice(tpl.indexOf('v-if="historicoLoading"'))
  const proximoIf = cadeia.search(/v-(if|else-if)="/g)
  assert.match(
    cadeia.slice(proximoIf + 1),
    /^[\s\S]{0,4000}?v-else-if="historicoDisponivel/,
    'v-else-if de historicoDisponivel continua ligado ao v-if de historicoLoading',
  )
}

// ---------------------------------------------------------------- script setup
const pageScript = descriptor.scriptSetup.content.replace(/^import[\s\S]*?from\s+'[^']+'\s*$/gm, '')
const exportsForTest = `return {
  items, total, page, loading, error, search, platform, tipoFilter, conferidoFilter,
  addOpen, lookupPedido, lookupResults, lookupError, historicoDisponivel,
  reembolsosExistentes, reembolsosDoPedido, salvoAviso, draft, creating,
  load, lookupOrder, createRefund, openAdd, closeAdd, verSalvoNaLista, selectLookup,
}`
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const factory = new AsyncFunction(
  'ref', 'computed', 'watch', 'nextTick', 'onMounted', 'onUnmounted',
  'definePageMeta', 'useApi', 'useCan', 'useIsAdmin', 'useAuthStore',
  'setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'confirm',
  'isoToday',
  transpile(pageScript, ts.ModuleKind.ESNext) + '\n' + exportsForTest,
)

const Vue = require('vue')
const LISTA_VAZIA = { items: [], total: 0, platforms: [], total_prejuizo: 0, total_reembolso: 0, total_a_conferir: 0 }

async function page({ lookup = null, created = null } = {}) {
  const calls = []
  const api = (url, opts) => {
    calls.push({ url, opts })
    if (url.startsWith('/api/refunds/order-lookup')) {
      return Promise.resolve(lookup || { items: [], historico_disponivel: false, reembolsos_existentes: 0, reembolsos_do_pedido: [] })
    }
    if (url === '/api/refunds' && opts && opts.method === 'POST') {
      return Promise.resolve(created || { id: 'r1', pedido_bling: '297631', conta: 'Shopee ATV', tipo: 'Cliente', reembolso: -60, conferido: false })
    }
    if (url.startsWith('/api/refunds')) return Promise.resolve({ ...LISTA_VAZIA })
    return Promise.resolve({})
  }
  // useCan/useIsAdmin devolvem ref no app real — o código lê `.value`.
  const state = await factory(
    Vue.ref, Vue.computed, Vue.watch, Vue.nextTick, () => {}, () => {},
    () => {}, () => ({ api }), () => Vue.ref(true), () => Vue.ref(true),
    () => ({ user: { email: 'x@y.z' } }),
    (fn) => { fn(); return 0 }, () => {}, () => 0, () => {}, () => true,
    () => '2026-09-18',
  )
  return { state, calls }
}

async function run() {
  // 1) o lookup preenche o aviso com a LISTA do que já existe
  {
    const { state } = await page({
      lookup: {
        items: [{ data: null, pedido_bling: '297631', pedido_marketplace: 'SHP1', plataforma: 'shopee', conta: 'Shopee ATV', custo_produto: null, custo_manutencao: null }],
        historico_disponivel: false,
        reembolsos_existentes: 2,
        reembolsos_do_pedido: [
          { data: '2026-09-10T12:00:00Z', conta: 'Shopee ATV', tipo: 'Cliente', reembolso: -50, conferido: true, criado_por: 'israel' },
          { data: '2026-09-11T12:00:00Z', conta: 'Shopee ATV', tipo: 'Frete', reembolso: -10, conferido: false, criado_por: null },
        ],
      },
    })
    state.lookupPedido.value = '297631'
    await state.lookupOrder(false)
    assert.equal(state.reembolsosExistentes.value, 2, 'o total vem do servidor')
    assert.equal(state.reembolsosDoPedido.value.length, 2, 'a lista do aviso vem do servidor')
    assert.equal(state.reembolsosDoPedido.value[0].criado_por, 'israel')

    // buscar com o campo vazio limpa o aviso: antes ele ficava preso na tela
    state.lookupPedido.value = ''
    await state.lookupOrder(false)
    assert.equal(state.reembolsosExistentes.value, 0, 'busca vazia zera o total')
    assert.deepEqual(state.reembolsosDoPedido.value, [], 'busca vazia zera a lista')
    assert.equal(state.draft.value, null)
  }

  // 2) depois de criar, a confirmação fica na tela (a linha pode estar fora da página)
  {
    const { state, calls } = await page()
    state.lookupPedido.value = '297631'
    await state.lookupOrder(false)
    state.draft.value = {
      data: null, pedido_bling: '297631', pedido_marketplace: 'SHP1', plataforma: 'shopee',
      conta: 'Shopee ATV', custo_produto: null, custo_manutencao: null,
      tipo: 'Cliente', prejuizo: null, reembolso: -60, chamado: '', operacao: '', observacao: '',
    }
    await state.createRefund()
    assert.ok(state.salvoAviso.value, 'a confirmação sobrevive ao fechamento do painel')
    assert.equal(state.salvoAviso.value.pedido, '297631')
    assert.equal(state.addOpen.value, false, 'o painel fecha')
    assert.equal(state.reembolsosExistentes.value, 0, 'o aviso do painel é limpo')

    // o atalho leva até a linha: filtra pelo pedido e recarrega
    await state.verSalvoNaLista()
    assert.equal(state.search.value, '297631', 'o atalho busca pelo pedido salvo')
    assert.equal(state.page.value, 1)
    assert.equal(state.salvoAviso.value, null, 'a confirmação sai depois de usada')
    assert.ok(calls.some((c) => c.url.includes('search=297631')), 'recarregou a lista filtrada')
  }

  // 3) abrir o painel de novo limpa a confirmação anterior
  {
    const { state } = await page()
    state.salvoAviso.value = { pedido: '1', conta: 'x' }
    state.openAdd()
    assert.equal(state.salvoAviso.value, null)
  }

  console.log('PASS: SFC parse/compile; aviso lista o que já existe; confirmação do salvo + atalho; resets da busca vazia')
}
run().catch((error) => { console.error(error); process.exitCode = 1 })
