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

// ------------------------------------------------- a aprovação do ADM (3/6)
// Novos procedimentos 3/6 (17/11/2026): o reembolso ao cliente lançado pelo Atendimento
// traz Motivo, Forma e a Aprovação. Acima do limite, "Aguardando ADM": o Finalizado fica
// desligado (com o porquê no title) e a API recusa com 409 `aguardando_adm`. Os dados do
// Pix só para o ADM (a API nem manda para os outros; a coluna também some) e o ADM
// aprova daqui com o mesmo POST do bloco da conversa.
const { compileScript } = require('vue/compiler-sfc')
const { renderToString } = require('vue/server-renderer')
const RC = (() => {
  const f = path.join(__dirname, '../components/AtendimentoReembolsoCliente.vue')
  const d = parse(fs.readFileSync(f, 'utf8'), { filename: f }).descriptor
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(d.script.content))(require, mod, mod.exports)
  return mod.exports
})()
// Todos os nomes do topo do <script setup> (o que o template usa), para renderizar a tela.
const ligacoes = Object.keys(compileScript(descriptor, { id: 'reembolso-check' }).bindings)
  .filter((n) => new RegExp(`^(?:const|let|function|async function) ${n}\\b`, 'm').test(descriptor.scriptSetup.content))
const factoryCompleta = new AsyncFunction(
  'ref', 'computed', 'watch', 'nextTick', 'onMounted', 'onUnmounted',
  'definePageMeta', 'useApi', 'useCan', 'useIsAdmin', 'useAuthStore',
  'setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'confirm',
  'isoToday', 'rotuloForma', 'rotuloMotivo', 'urlAprovar',
  transpile(pageScript, ts.ModuleKind.ESNext) + `\nreturn { ${ligacoes.join(', ')} }`,
)
const PIX = 'chave 11 99999-0000 · Banco Exemplo · Fulana'
const linha = (extra = {}) => ({
  id: 'a', data: null, pedido_bling: '300101', pedido_marketplace: '251117ABC', plataforma: 'shopee', conta: 'Shopee ATV', tipo: 'Cliente',
  prejuizo: 0, reembolso: -150, reembolso_at: null, chamado: null, operacao: null, conferido: false, observacao: null, situacao_bling: null,
  created_at: '2026-11-17T13:30:00Z', updated_at: '2026-11-17T13:30:00Z', ...extra,
})
const doAtendimento = (extra = {}) => linha({
  motivo: 'defeito_avaria', motivo_detalhe: 'veio riscado', forma_pagamento: 'pix', dados_pagamento: PIX, requer_aprovacao: true,
  aprovado_por: null, aprovado_em: null, origem: 'atendimento', conversa_id: 'conv-1', ...extra,
})
async function paginaCompleta({ admin, linhas, patch, aprovar }) {
  const calls = []
  const api = async (url, opts) => {
    calls.push({ url, metodo: (opts && opts.method) || 'GET', body: opts && opts.body })
    if (opts && opts.method === 'PATCH') return patch(url, opts)
    if (opts && opts.method === 'POST') return aprovar(url, opts)
    if (url.startsWith('/api/refunds')) return { ...LISTA_VAZIA, items: structuredClone(linhas), total: linhas.length }
    return {}
  }
  const state = await factoryCompleta(
    Vue.ref, Vue.computed, Vue.watch, Vue.nextTick, () => {}, () => {},
    () => {}, () => ({ api }), () => Vue.ref(true), () => Vue.ref(admin),
    () => ({ user: { email: 'x@y.z' } }),
    (fn) => { fn(); return 0 }, () => {}, () => 0, () => {}, () => true,
    () => '2026-11-17', RC.rotuloForma, RC.rotuloMotivo, RC.urlAprovar,
  )
  return { state, calls }
}
async function renderizar(state) {
  const tpl = descriptor.template.content
  const { code, errors } = require('vue/compiler-sfc').compileTemplate({ source: tpl, filename, id: 'reembolso-render' })
  assert.deepEqual(errors, [])
  const mod = {}
  new Function('exports', 'require', transpile(code))(mod, require)
  const app = Vue.createSSRApp({ setup: () => ({ ...state, rotuloForma: RC.rotuloForma, rotuloMotivo: RC.rotuloMotivo }), render: mod.render })
  app.config.warnHandler = () => {}
  for (const nome of new Set([...tpl.matchAll(/<([A-Z][A-Za-z0-9]*)/g)].map((m) => m[1]))) {
    if (nome === 'PageHeader') app.component(nome, { setup: (_, { slots }) => () => Vue.h('header', slots.actions?.()) })
    else if (nome === 'Button') app.component(nome, { props: ['disabled', 'size', 'variant'], setup: (p, { slots }) => () => Vue.h('button', { disabled: p.disabled }, slots.default?.()) })
    else app.component(nome, { render: () => Vue.h('i', { 'data-icone': nome }) })
  }
  return (await renderToString(app)).replace(/<!--[\s\S]*?-->/g, '')
}
// O atributo `disabled` (não a classe `disabled:cursor-default` do Tailwind).
const desligado = (tag) => /\sdisabled(?=[\s>=])/.test(tag || '')
const linhaDe = (h, id) => {
  const trs = h.split('<tr').slice(1).map((t) => `<tr${t}`)
  return trs.find((t) => t.includes(`>${id}<`)) || ''
}

async function aprovacaoDoAdm() {
  // As peças do template: as colunas novas, o chip, o Finalizado desligado com o porquê.
  {
    const tpl = descriptor.template.content
    assert.match(tpl, /title="Motivo do reembolso ao cliente lançado pelo Atendimento">Motivo</)
    assert.match(tpl, />Forma</)
    assert.match(tpl, />Aprovação</)
    assert.match(tpl, /<th v-if="isAdmin"[^>]*>Dados do Pix</, 'a coluna do Pix só para o ADM')
    assert.match(tpl, /<td v-if="isAdmin"[^>]*data-col-pix>/)
    assert.match(tpl, /:disabled="!canEdit \|\| aguardaAdm\(row\)"\s+:title="aguardaAdm\(row\) \? TITULO_AGUARDA_ADM : undefined"/)
    assert.match(tpl, /data-chip-aguardando-adm\s*>Aguardando ADM</)
    assert.match(tpl, /<button\s+v-if="isAdmin"[\s\S]*?data-aprovar-reembolso\s+@click="aprovarRow\(row\)"/)
    assert.doesNotMatch(tpl, /colspan="canSeeSituacaoBling \? 14 : 13"/, 'o "sem registros" segue as colunas novas')
    assert.equal((tpl.match(/:colspan="totalColunas"/g) || []).length, 2)
  }

  // O ADM: o Pix, o chip com "aprovar", o Finalizado desligado; as colunas batem com o cabeçalho.
  {
    const linhas = [
      doAtendimento({ id: 'a' }),
      doAtendimento({ id: 'b', pedido_bling: '300102', forma_pagamento: 'cupom', motivo: 'frete', motivo_detalhe: null, dados_pagamento: null, aprovado_por: 'u-1', aprovado_em: '2026-11-17T14:00:00Z', conversa_id: null }),
      linha({ id: 'c', pedido_bling: '300103', reembolso: -50, chamado: 'pix antigo no texto' }),
    ]
    const { state } = await paginaCompleta({ admin: true, linhas, patch: () => ({}), aprovar: () => ({}) })
    assert.equal(state.totalColunas.value, 17, '5 + 8 + 4 (o Pix do ADM)')
    const h = await renderizar(state)
    const ths = (h.split('<thead')[1].split('</thead>')[0].split('<tr').slice(2)[0].match(/<th/g) || []).length
    assert.equal(ths, 17, 'o cabeçalho tem as 17 colunas')
    const grupos = [...h.split('<thead')[1].split('<tr')[1].matchAll(/colspan="(\d+)"/g)].map((m) => Number(m[1]))
    assert.equal(grupos.reduce((a, b) => a + b, 0), 17, 'os grupos somam as colunas')
    assert.match(h, />Dados do Pix</)
    const a = linhaDe(h, '300101')
    assert.match(a, /data-col-motivo[\s\S]*?>Defeito ou avaria</)
    assert.match(a, /title="Defeito ou avaria: veio riscado"/)
    assert.match(a, /href="\/atendimento\?conversa=conv-1"[^>]*target="_blank"/, 'ver a conversa de onde saiu')
    assert.match(a, /data-col-forma>Pix</)
    assert.match(a, /data-chip-aguardando-adm>Aguardando ADM</)
    assert.match(a, /data-aprovar-reembolso/)
    assert.ok(a.includes(PIX), 'o ADM vê o Pix')
    const caixa = (a.match(/<input[^>]*type="checkbox"[^>]*>/) || [])[0]
    assert.ok(desligado(caixa), 'aguardando o ADM: o Finalizado não marca')
    assert.ok(caixa.includes(`title="${state.TITULO_AGUARDA_ADM}"`), 'com o porquê no title')
    const b = linhaDe(h, '300102')
    assert.match(b, /data-col-forma>Cupom</)
    assert.match(b, /aprovado 17\/11\/26/)
    assert.doesNotMatch(b, /data-chip-aguardando-adm|data-aprovar-reembolso|ver conversa/)
    assert.ok(!desligado((b.match(/<input[^>]*type="checkbox"[^>]*>/) || [])[0]), 'aprovado: finaliza')
    const c = linhaDe(h, '300103')
    assert.match(c, /data-col-motivo>[\s\S]*?>—</, 'os antigos: "—"')
    assert.match(c, /data-col-forma>—</)
    assert.match(c, /data-col-aprovacao>[\s\S]*?—/)
  }

  // Quem não é ADM: nem a coluna do Pix (nem se o dado viesse), nem o "aprovar".
  {
    const { state } = await paginaCompleta({ admin: false, linhas: [doAtendimento()], patch: () => ({}), aprovar: () => ({}) })
    assert.equal(state.totalColunas.value, 16)
    const h = await renderizar(state)
    assert.doesNotMatch(h, /Dados do Pix|data-col-pix/)
    assert.ok(!h.includes('Banco Exemplo'), 'o Pix não aparece para quem não é ADM')
    assert.doesNotMatch(h, /data-aprovar-reembolso/)
    assert.match(h, /data-chip-aguardando-adm>Aguardando ADM</, 'o chip aparece para todos')
    const vazio = await paginaCompleta({ admin: false, linhas: [], patch: () => ({}), aprovar: () => ({}) })
    assert.match(await renderizar(vazio.state), /colspan="16"[^>]*>sem registros</)
  }

  // O Finalizado: a trava da tela (sem PATCH) e o 409 `aguardando_adm` da API (volta o checkbox).
  {
    const erro409 = Object.assign(new Error('409'), { status: 409, data: { detail: { code: 'aguardando_adm', detail: 'Este reembolso espera a aprovação de um ADM.' } } })
    const { state, calls } = await paginaCompleta({
      admin: false,
      linhas: [doAtendimento({ id: 'a' }), doAtendimento({ id: 'b', aprovado_em: '2026-11-17T14:00:00Z' })],
      patch: () => { throw erro409 },
      aprovar: () => ({}),
    })
    const [a, b] = state.items.value
    await state.setRowConferido(a, true)
    assert.equal(a.conferido, false)
    assert.equal(calls.filter((c) => c.metodo === 'PATCH').length, 0, 'aguardando: nem sai o PATCH')
    assert.equal(state.error.value, state.TITULO_AGUARDA_ADM)
    // A tela achava aprovado, a API diz que não (409): o checkbox volta e diz por quê.
    state.error.value = null
    await state.setRowConferido(b, true)
    assert.equal(calls.filter((c) => c.metodo === 'PATCH').length, 1)
    assert.equal(b.conferido, false, 'o Finalizado volta')
    assert.equal(state.error.value, state.TITULO_AGUARDA_ADM)
    // Desmarcar não passa pela trava.
    const { state: s2, calls: c2 } = await paginaCompleta({ admin: false, linhas: [doAtendimento({ conferido: true, aprovado_em: '2026-11-17T14:00:00Z' })], patch: () => ({ reembolso_at: null }), aprovar: () => ({}) })
    await s2.setRowConferido(s2.items.value[0], false)
    assert.equal(c2.filter((c) => c.metodo === 'PATCH').length, 1)
  }

  // O "aprovar" do ADM: o POST do Atendimento; a linha libera o Finalizado. Só o ADM.
  {
    const { state, calls } = await paginaCompleta({
      admin: true,
      linhas: [doAtendimento({ id: 'r-1' })],
      patch: () => ({}),
      aprovar: () => ({ item: { id: 'r-1', aprovado_em: '2026-11-17T15:00:00Z', aprovado_por: 'joana dark', situacao: 'em_aberto' } }),
    })
    const r = state.items.value[0]
    assert.equal(state.aguardaAdm(r), true)
    await state.aprovarRow(r)
    assert.deepEqual(calls.filter((c) => c.metodo === 'POST').map((c) => c.url), ['/api/atendimento/reembolsos/r-1/aprovar'])
    assert.equal(r.aprovado_em, '2026-11-17T15:00:00Z')
    assert.equal(r.aprovado_por_nome, 'joana dark')
    assert.equal(state.aguardaAdm(r), false, 'aprovado: o Finalizado libera')
    // A data no formato da tela (`fmtDateTime`: "17/11/26, 12:00") e o nome que a API mandou.
    assert.match(state.textoAprovacao(r), /^aprovado \d\d\/11\/26,? \d\d:\d\d · joana dark$/)
    await state.aprovarRow(r)
    assert.equal(calls.filter((c) => c.metodo === 'POST').length, 1, 'já aprovado: não manda de novo')

    const n = await paginaCompleta({ admin: false, linhas: [doAtendimento()], patch: () => ({}), aprovar: () => ({}) })
    await n.state.aprovarRow(n.state.items.value[0])
    assert.equal(n.calls.filter((c) => c.metodo === 'POST').length, 0, 'quem não é ADM não aprova')

    // Outro ADM aprovou antes: relê a lista.
    const ja = Object.assign(new Error('409'), { status: 409, data: { detail: { code: 'ja_aprovado' } } })
    const j = await paginaCompleta({ admin: true, linhas: [doAtendimento()], patch: () => ({}), aprovar: () => { throw ja } })
    const antes = j.calls.length
    await j.state.aprovarRow(j.state.items.value[0])
    assert.ok(j.calls.slice(antes).some((c) => c.metodo === 'GET' && c.url.startsWith('/api/refunds?')), 'relê a lista')
    assert.equal(j.state.aprovandoId.value, null)
  }
  console.log('PASS: Motivo, Forma, Aprovação (Aguardando ADM), Pix só do ADM, Finalizado travado + 409 aguardando_adm, aprovar do ADM')
}
run().then(aprovacaoDoAdm).catch((error) => { console.error(error); process.exitCode = 1 })
