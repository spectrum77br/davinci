// Run from apps/web: node tests/importacao-colunas.cjs
// Importação › grade da sub-aba Importação (02/11/2026, tarefas "Ajustes de
// colunas" e "Melhorias na Tabela de Preços" do Eduardo). Roda o <script
// setup> de verdade (API falsa) e renderiza a página (Vue SSR + jsdom):
//  - rótulos Produto/Custo/Estoque; fora consumo diário, memória consumo,
//    reposição estoque, saldo reposição e custo realizado;
//  - 30d = vendas_30d (0 é "0"; sem o campo, "—") e Reposição calculada na
//    tela (utils/reposicao.ts): negativa em vermelho com "−", balão com a
//    conta, explicação visível na barra âmbar, recalcula ao mexer no prazo;
//  - Estoque do Celular com as parcelas dos lotes no balão;
//  - Celular: colunas fixas grudadas com left acumulado e o divisor na
//    Reposição (sombra interna, que anda com a rolagem; claro e escuro);
//    rowspan = linhas do cabeçalho; colspan da linha vazia = colunas de
//    verdade, com o texto grudado à esquerda;
//  - o filtro único "reposição" (todos | falta | sobra | sem dado); filtro
//    sem resultado diz "Nenhum produto com esse filtro." com "Limpar
//    filtro" (a mensagem de criar produto só com a lista vazia de verdade)
//    e a Cotação do Celular lista todos, sem o filtro da aba Importação;
//  - prazos: o PATCH leva só o campo mexido (o outro prazo também se edita
//    na Tabela de Preços) e a tela fica com a resposta inteira;
//  - modal "Criar produto" com os nomes da grade (Produto, Custo).
// O SSR não renderiza @change/@click: a ligação dos eventos é conferida no
// AST do template.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { renderToString } = require('vue/server-renderer')
const { parse, compileScript, compileTemplate, compileStyle } = require('vue/compiler-sfc')
const { JSDOM } = require('jsdom')

const transpile = (src) => ts.transpileModule(src, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, esModuleInterop: true },
}).outputText
const ler = (rel) => fs.readFileSync(path.resolve(__dirname, rel), 'utf8')
function lib(rel) {
  const mod = { exports: {} }
  new Function('module', 'exports', 'require', transpile(ler(rel)))(mod, mod.exports, require)
  return mod.exports
}
const REPOSICAO = lib('../utils/reposicao.ts')
const LIBS = {
  '~/lib/date': lib('../lib/date.ts'),
  '~/lib/number': lib('../lib/number.ts'),
  '~/utils/reposicao': REPOSICAO,
}

// ─── a página: script setup + template compilados ─────────────────────────
const filename = path.resolve(__dirname, '../pages/importacao.vue')
const fonte = fs.readFileSync(filename, 'utf8')
const { descriptor, errors } = parse(fonte, { filename })
assert.deepEqual(errors, [], 'SFC parse')
assert.match(descriptor.scriptSetup.content, /from '~\/utils\/reposicao'/, 'importa a conta explicitamente')
const script = compileScript(descriptor, { id: 'importacao', inlineTemplate: false })
const tpl = compileTemplate({
  source: descriptor.template.content, filename, id: 'importacao',
  compilerOptions: { isTS: true, bindingMetadata: script.bindings },
})
assert.deepEqual(tpl.errors, [], 'template compila')
const render = {}
new Function('exports', 'require', transpile(tpl.code))(render, require)

// `import.meta.client` só existe no bundle do Nuxt; aqui é o servidor.
const codigo = transpile(script.content).replace(/import\.meta\.client/g, 'false')

// Elementos do template (AST do parse, antes das transformações: v-if e v-for
// ainda são diretivas do próprio elemento).
function elementos(no, saida = []) {
  if (no.type === 1) saida.push(no)
  for (const filho of no.children || []) elementos(filho, saida)
  return saida
}
const ELEMENTOS = elementos(descriptor.template.ast)
const diretiva = (el, nome, arg) => el.props.find((p) => p.type === 7 && p.name === nome && (arg == null || p.arg?.content === arg))
// Cada prazo grava só ele (@change="saveConfig('<campo>')"); sem o argumento
// o handler recebia o Event e o PATCH não levaria prazo nenhum.
for (const campo of ['tempo_reposicao', 'tempo_estoque']) {
  const input = ELEMENTOS.find((el) => el.tag === 'input' && diretiva(el, 'model')?.exp?.content === `config.${campo}`)
  assert.ok(input, `campo ${campo} na barra âmbar`)
  assert.equal(diretiva(input, 'on', 'change')?.exp?.content, `saveConfig('${campo}')`, `${campo}: o @change grava só ele`)
}
const botaoLimpar = ELEMENTOS.find((el) => el.tag === 'button' && el.children.some((c) => c.type === 2 && c.content.trim() === 'Limpar filtro'))
assert.ok(botaoLimpar, 'botão Limpar filtro')
assert.equal(diretiva(botaoLimpar, 'on', 'click')?.exp?.content, 'limparFiltro')

// Divisor do bloco fixo do Celular: sombra interna da própria célula, que anda
// com o sticky ao rolar (a borda colapsada ficava para trás). No escuro é
// `.dark .x`: `:global(.dark) .x` compila para `.dark` e iria para o <html>.
const ESCOPO = 'data-v-importacao'
const CSS = compileStyle({ source: descriptor.styles.map((s) => s.content).join('\n'), filename, id: ESCOPO, scoped: true })
assert.deepEqual(CSS.errors, [], 'CSS compila')
const REGRAS = [...CSS.code.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/([^{}]+)\{([^}]*)\}/g)]
  .map(([, sel, corpo]) => ({ seletores: sel.split(',').map((s) => s.trim()), corpo: corpo.trim() }))
const corposDe = (seletor) => REGRAS.filter((r) => r.seletores.includes(seletor)).map((r) => r.corpo)
assert.deepEqual(corposDe(`.lote-divider-anchor[${ESCOPO}]`), ['box-shadow: inset -2px 0 0 hsl(var(--foreground) / 0.7);'])
assert.deepEqual(corposDe(`.dark .lote-divider-anchor[${ESCOPO}]`), ['box-shadow: inset -2px 0 0 hsl(var(--foreground) / 0.5);'])
assert.ok(!corposDe('.dark').some((c) => c.includes('box-shadow')), 'nenhuma sombra no <html class="dark">')

const icone = { render: () => Vue.h('i') }
function requerer(nome) {
  if (nome === 'lucide-vue-next') return new Proxy({}, { get: () => icone })
  return LIBS[nome] || require(nome)
}
const AUTO = ['definePageMeta', 'useApi', 'useAuthStore', 'useRoute', 'useRouter', 'useToasts', 'onBeforeUnmount']
function componente(g) {
  const valores = {
    definePageMeta: () => {},
    useApi: () => ({ api: g.api, url: (u) => u }),
    useAuthStore: () => g.auth,
    useRoute: () => ({ query: { cat: g.cat } }),
    useRouter: () => ({ replace: async () => {} }),
    useToasts: () => ({ success() {}, error() {}, info() {}, warning() {} }),
    onBeforeUnmount: Vue.onBeforeUnmount,
  }
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', ...AUTO, codigo)(requerer, mod, mod.exports, ...AUTO.map((n) => valores[n]))
  return { ...mod.exports.default, render: render.render }
}

// ─── API falsa ─────────────────────────────────────────────────────────────
// Campos antigos com valores que não podem aparecer em lugar nenhum da grade.
const ANTIGOS = { consumo_diario: '4.4444', maior_media_30d: '6.6666', memoria_consumo: '5.5555', reposicao_estoque: 98765, saldo_reposicao: 87654, custo_realizado: '54321.99' }
const produto = (id, sku, o = {}) => ({
  id, sku, modelo_bling: `Produto ${sku}`, categoria: 'celular', fornecedor: null, modelo_china: null, cor_china: null,
  fechamento: null, tsa: null, cor: null, custo_bling: 560, estoque_bling: 0, vendas_30d: 0, estoque_partes: null,
  duimp: null, ncm: null, nome_gerado: '', bling_sync_status: null, bling_sync_marked_at: null, bling_product_id: null,
  bling_sync_error: null, bling_sync_attempted_at: null, bling_sync_done_at: null, lote_quantidades: {},
  valor_usd: null, valor_brl_realizado: null, frete_type: 'regular', lote_valores_usd: {}, lote_custos_manuais: {},
  lote_target_skus: {}, lote_item_ids: {}, ...ANTIGOS, ...o,
})
const lote = (id, nome, o = {}) => ({
  id, nome, abertura: '2026-05-11', fechamento: null, realizado: 0, previsto: 0, previsto_manual: null, saldo: 0,
  prazo: 113, is_aberto: true, transportadora: 'king', taxa: 4.9584, frete_pct: 0.14, adicional: 12,
  bling_stock_total: 0, bling_stock_sent: 0, bling_stock_skipped: 0, bling_stock_errors: 0,
  di_numero: null, tem_simulacao_pdf: false, tem_di_pdf: false, di_pagamento: null, ...o,
})
const CELULAR = [
  // Exemplo da tarefa: estoque 0, 100 por mês, 30 + 30 dias → −200.
  produto('p1', 'dg053.ci', { estoque_bling: 0, vendas_30d: 100, lote_quantidades: { l1: 5 }, lote_item_ids: { l1: 'i1' } }),
  // Celular: soma dos lotes; 0 venda é dado (mostra "0") → sobra = o estoque.
  produto('p2', 'dg054.ci', { estoque_bling: 8, vendas_30d: 0, estoque_partes: [{ sku: 'dg054.ci', estoque: 3 }, { sku: 'dg054.sp', estoque: 5 }] }),
  produto('p3', 'dg055.ci', { estoque_bling: 250, vendas_30d: 100 }),
  produto('p4', 'dg056.ci', { estoque_bling: null, vendas_30d: 3 }),
  produto('p5', 'dg057.ci', { estoque_bling: 200, vendas_30d: 100, estoque_partes: [{ sku: 'dg057.ci', estoque: 200 }] }),
  produto('p6', 'dg058.ci', { estoque_bling: 12, vendas_30d: undefined }),
  // 7 × 60 ÷ 30 = 14 necessárias.
  produto('p7', 'dg059.ci', { estoque_bling: 0, vendas_30d: 7 }),
]
const MALA = [
  // Mala (120 + 30): 1 venda no mês → 5 para 150 dias.
  produto('m1', 'a015', { categoria: 'mala', custo_bling: 10, estoque_bling: 0, vendas_30d: 1 }),
  produto('m2', 'b045.18', { categoria: 'mala', custo_bling: 59, estoque_bling: 1648, vendas_30d: 3 }),
]
const CONFIG = { celular: [30, 30], mala: [120, 30], eletro: [60, 30] }
// `salvo` é a row dos prazos no servidor; o teste mexe nela direto quando
// faz o papel da Tabela de Preços (que edita os mesmos prazos).
function apiFalsa(cat, produtos, lotes, chamadas, salvo) {
  return async (url, opts = {}) => {
    chamadas.push({ url, method: opts.method || 'GET', body: opts.body })
    const rota = url.split('?')[0]
    if (rota === '/api/importacao/config') {
      if (opts.method === 'PATCH') {
        // Como o ImportConfigPatch: inteiro de 0 a 999, senão 422.
        if (!Object.values(opts.body).every((v) => Number.isInteger(v) && v >= 0 && v <= 999)) {
          throw Object.assign(new Error('422'), { status: 422, data: { detail: [{ type: 'less_than_equal' }] } })
        }
        Object.assign(salvo, opts.body)
      }
      return { categoria: cat, ...salvo }
    }
    if (rota === '/api/importacao/products') return produtos.map((p) => ({ ...p }))
    if (rota === '/api/importacao/lotes') return lotes.map((l) => ({ ...l }))
    if (rota === '/api/importacao/resumo') return { items: [], total: 0 }
    if (rota === '/api/importacao/cotacao') return { fabricantes: [], produtos: [], valores: [] }
    if (rota === '/api/importacao/categoria-counts') return { mala: MALA.length, eletro: 0, celular: CELULAR.length }
    if (rota === '/api/importacao/cotacao/params') return { categoria: cat, taxa_cambio: 5.1, frete_regular_pct: 0.16, frete_swap_pct: 0.06, frete_acessorios_pct: 0.2, adicional: 12 }
    if (rota === '/api/importacao/frete') return { rows: [], transportadoras: [], total_a_entregar: 0, saldo_a_pagar: 0 }
    const sync = /^\/api\/importacao\/products\/([^/]+)\/sync-bling$/.exec(rota)
    if (sync) {
      // Como o backend: a resposta de UM produto vem sem o que só o GET monta.
      const p = produtos.find((x) => x.id === sync[1])
      return { ...p, estoque_bling: null, vendas_30d: 0, estoque_partes: null, lote_quantidades: {}, lote_item_ids: {}, bling_sync_status: 'pending', bling_sync_marked_at: '2026-11-02T12:00:00Z' }
    }
    throw new Error(`API não esperada: ${opts.method || 'GET'} ${url}`)
  }
}
const ADMIN = { isAdmin: true, user: { role: 'admin', permissions: {} } }
const LEITOR = { isAdmin: false, user: { role: 'user', permissions: { importacao: { view: true } } } }

async function abrir({ cat, produtos, lotes, auth = ADMIN }) {
  const chamadas = []
  const avisos = []
  // cat undefined = a página aberta sem ?cat= (cai no Celular).
  const doServidor = cat ?? 'celular'
  const servidor = { tempo_reposicao: CONFIG[doServidor][0], tempo_estoque: CONFIG[doServidor][1] }
  const C = componente({ cat, auth, api: apiFalsa(doServidor, produtos, lotes, chamadas, servidor) })
  const caixa = { estado: null }
  const app = Vue.createSSRApp({
    ...C,
    async setup(p, ctx) {
      const s = await C.setup(p, ctx)
      caixa.estado = Vue.proxyRefs(s)
      return s
    },
  })
  // A aba Certificações é o componente da tela do menu (testado à parte).
  const vazio = { render: () => null }
  app.component('CertificacoesPainel', vazio)
  app.config.warnHandler = (msg) => avisos.push(msg)
  const html = await renderToString(app)
  assert.deepEqual(avisos, [], `${cat}: render sem aviso do Vue`)
  assert.equal(caixa.estado.errorText, null, `${cat}: loadAll sem erro`)
  // Renderiza de novo o MESMO estado (depois de mexer nele).
  const deNovo = async () => {
    const app2 = Vue.createSSRApp({ ...C, setup: () => caixa.estado })
    app2.component('CertificacoesPainel', { props: ['embutido'], render: () => Vue.h('div', { 'data-certificacoes': '' }) })
    return new JSDOM(await renderToString(app2)).window.document
  }
  return { doc: new JSDOM(html).window.document, estado: caixa.estado, chamadas, deNovo, servidor }
}

const texto = (el) => el.textContent.replace(/\s+/g, ' ').trim()
function grade(doc) {
  const tabela = doc.querySelector('table.estoque-grid')
  assert.ok(tabela, 'grade da aba Importação')
  const linhasCab = [...tabela.querySelectorAll('thead > tr')]
  const fixas = [...linhasCab[0].children].filter((c) => c.tagName === 'TH')
  const colunas = [...linhasCab[0].children].reduce((n, c) => n + c.colSpan, 0)
  const corpo = [...tabela.querySelectorAll('tbody > tr')]
  return { tabela, linhasCab, fixas, colunas, corpo }
}
const celulaDe = (corpo, sku) => {
  const tr = corpo.find((r) => r.querySelector('td:nth-child(2) input')?.value === sku)
  assert.ok(tr, `linha ${sku}`)
  const [, , , estoque, vendas, reposicao] = tr.children
  return { tr, estoque, vendas, reposicao }
}
// A linha única do corpo quando nada aparece (lista vazia ou filtro). O
// texto fica grudado à esquerda: a linha tem a largura de todos os lotes.
function linhaVazia(doc) {
  const { corpo, colunas } = grade(doc)
  assert.equal(corpo.length, 1, 'só a linha vazia')
  assert.equal(corpo[0].children[0].colSpan, colunas, 'a linha vazia ocupa a grade inteira')
  const caixa = corpo[0].querySelector('td > div')
  assert.ok(caixa?.classList.contains('sticky') && caixa.classList.contains('left-0'), 'mensagem grudada à esquerda')
  assert.equal(texto(caixa), texto(corpo[0]), 'todo o texto dentro dela')
  return corpo[0]
}
const MSG_FILTRO = 'Nenhum produto com esse filtro. Limpar filtro'
const MSG_VAZIA = {
  celular: 'Categoria Celular em construção. Clique em "Criar produto" pra começar.',
  mala: 'Nenhum produto. Clique em "Criar produto" para começar.',
  eletro: 'Nenhum produto. Clique em "Criar produto" para começar.',
}
// Sub-aba Cotação do Celular: a tabela com Tipo Frete.
function cotacaoCelular(doc) {
  const tabela = [...doc.querySelectorAll('table')].find((t) => texto(t.querySelector('thead')).includes('Tipo Frete'))
  assert.ok(tabela, 'tabela da Cotação do Celular')
  return { linhas: [...tabela.querySelectorAll('tbody > tr')], texto: texto(tabela.querySelector('tbody')) }
}
// Modal "Criar produto": rótulos dos campos e o texto inteiro.
function modalCriar(doc) {
  const titulo = [...doc.querySelectorAll('h3')].find((h) => texto(h) === 'Criar produto')
  assert.ok(titulo, 'modal Criar produto aberto')
  const modal = titulo.closest('.fixed')
  return { rotulos: [...modal.querySelectorAll('label > span:first-child')].map(texto), texto: texto(modal) }
}
const DICA_PADRAO = 'Padrão: Mala {produto} tamanho {n após o ponto no SKU} - {cor}'
const DICA_NOME = { celular: DICA_PADRAO, mala: DICA_PADRAO, eletro: 'Eletro: nome = {produto}' }
const ROTULOS = ['Produto', 'sku', 'Custo', 'Estoque', '30d', 'Reposição', 'duimp', 'ncm']
const FORA = /consumo diário|memória consumo|reposição estoque|saldo reposição|custo realizado|modelo bling|custo bling|estoque bling/i
const ANTIGOS_NA_TELA = ['98765', '87654', '54.321,99', '4,44', '5,56', '6,67']
const VERMELHO = '!text-red-700'

;(async () => {
  // ═════════════════════════════════════════════ Celular
  {
    const lotes = [lote('l1', 'AG232'), lote('l2', 'AG233', { is_aberto: false, fechamento: '2026-09-01', taxa: null, frete_pct: null })]
    const { doc, estado, chamadas, deNovo, servidor } = await abrir({ cat: 'celular', produtos: CELULAR, lotes })
    const { tabela, linhasCab, fixas, colunas, corpo } = grade(doc)

    // Rótulos: renomeados, novos e os que saíram.
    assert.deepEqual(fixas.slice(0, 8).map(texto), ROTULOS)
    assert.doesNotMatch(texto(tabela.querySelector('thead')), FORA)
    assert.doesNotMatch(texto(doc.body), /saldo reposição|reposição estoque|consumo diário|custo realizado/, 'nem nos filtros')
    for (const v of ANTIGOS_NA_TELA) assert.ok(!texto(tabela).includes(v), `campo antigo ${v} fora da grade`)

    // rowspan = linhas do cabeçalho (9 do lote + 4 do Celular + sub-cabeçalho).
    assert.equal(linhasCab.length, 14)
    for (const th of fixas) assert.equal(th.rowSpan, 14, `rowspan ${texto(th)}`)

    // Colunas fixas grudadas: left acumulado e min-width do mapa.
    const larguras = [300, 130, 60, 66, 56, 76]
    let left = 0
    larguras.forEach((w, i) => {
      assert.ok(fixas[i].classList.contains('sticky'), `${ROTULOS[i]} sticky`)
      assert.equal(fixas[i].style.left, `${left}px`, `${ROTULOS[i]} left`)
      assert.equal(fixas[i].style.minWidth, `${w}px`, `${ROTULOS[i]} largura`)
      for (const tr of corpo) {
        assert.equal(tr.children[i].style.left, `${left}px`)
        assert.ok(tr.children[i].classList.contains('sticky'))
      }
      left += w
    })
    assert.ok(!fixas[6].classList.contains('sticky'), 'duimp não gruda')
    // O divisor do bloco grudado fica na Reposição (cabeçalho + cada linha), só nela.
    assert.ok(fixas[5].classList.contains('lote-divider-anchor'))
    assert.equal(tabela.querySelectorAll('.lote-divider-anchor').length, 1 + corpo.length)

    // Toda linha tem o número de colunas do cabeçalho: 6 fixas + duimp/ncm + 3 por lote + bling + excluir.
    assert.equal(colunas, 6 + 2 + lotes.length * 3 + 2)
    assert.equal(estado.colunasGrade, colunas)
    for (const tr of corpo) assert.equal(tr.children.length, colunas)
    assert.equal(linhasCab.at(-1).children.length, lotes.length * 3, 'quant | valor | custo por lote')

    // 30d e Reposição: o exemplo da tarefa.
    const p1 = celulaDe(corpo, 'dg053.ci')
    assert.equal(texto(p1.estoque), '0')
    assert.equal(texto(p1.vendas), '100')
    assert.equal(texto(p1.reposicao), '−200', 'negativo com o sinal −')
    assert.ok(p1.reposicao.classList.contains(VERMELHO) && p1.reposicao.classList.contains('dark:!text-red-400'), 'em vermelho (ganha do td.calc)')
    assert.equal(p1.reposicao.title, '100 vendas em 30 dias × 60 dias ÷ 30 = 200 necessárias; estoque 0 → faltam 200 (comprar 200).')
    assert.equal(p1.estoque.title, 'Estoque no Bling: 0')
    // Soma dos lotes no balão; 0 venda aparece "0" e a sobra é o estoque.
    const p2 = celulaDe(corpo, 'dg054.ci')
    assert.equal(p2.estoque.title, 'Estoque no Bling, soma dos lotes: dg054.ci 3 + dg054.sp 5 = 8')
    assert.equal(texto(p2.vendas), '0')
    assert.equal(texto(p2.reposicao), '8')
    assert.ok(!p2.reposicao.classList.contains(VERMELHO), 'positivo normal')
    assert.equal(texto(celulaDe(corpo, 'dg055.ci').reposicao), '50')
    const p5 = celulaDe(corpo, 'dg057.ci')
    assert.equal(texto(p5.reposicao), '0')
    assert.equal(p5.estoque.title, 'Estoque no Bling: dg057.ci 200')
    assert.match(p5.reposicao.title, /nada a comprar\.$/)
    // Sem estoque lido: — nos dois.
    const p4 = celulaDe(corpo, 'dg056.ci')
    assert.equal(texto(p4.estoque), '—')
    assert.equal(p4.estoque.title, 'Sem estoque lido no Bling.')
    assert.equal(texto(p4.reposicao), '—')
    assert.equal(p4.reposicao.title, 'Sem estoque lido ou sem prazos configurados.')
    // Sem vendas_30d (API antiga): — no 30d e na Reposição (zero daria sobra falsa).
    const p6 = celulaDe(corpo, 'dg058.ci')
    assert.equal(texto(p6.vendas), '—')
    assert.equal(texto(p6.reposicao), '—')
    assert.equal(p6.reposicao.title, 'Sem as vendas dos últimos 30 dias.')
    // 7 × 60 ÷ 30 = 14 necessárias (a fração é conferida mais abaixo).
    assert.equal(texto(celulaDe(corpo, 'dg059.ci').reposicao), '−14')

    // Cabeçalhos explicados no balão.
    assert.match(fixas[3].title, /somando todos os lotes do produto/)
    assert.equal(fixas[4].title, 'Vendas dos últimos 30 dias (unidades). Kit e conjunto contam em cada peça.')
    assert.equal(
      fixas[5].title,
      'Reposição = Estoque − vendas 30d × (30 + 30) ÷ 30; a parte das vendas é arredondada para cima. '
      + 'Negativo (vermelho) = falta: é quanto comprar. Positivo = sobra além do prazo. Não desconta o que já está em lote aberto.',
    )
    // Explicação visível na barra âmbar, com os prazos de agora.
    const formula = doc.querySelector('[data-formula-reposicao]')
    assert.equal(
      texto(formula),
      'Reposição = Estoque − vendas 30d × (30 + 30) ÷ 30. Negativo (vermelho) = falta: é quanto comprar. Não desconta o que já está em lote aberto.',
    )
    assert.ok(formula.closest('.bg-amber-50'), 'na barra dos prazos')

    // Filtro único "reposição".
    const select = [...doc.querySelectorAll('select')].find((s) => s.closest('label')?.textContent.includes('reposição:'))
    assert.ok(select, 'select reposição')
    assert.deepEqual([...select.options].map((o) => [o.value, texto(o)]), [['todos', 'todos'], ['falta', 'falta'], ['sobra', 'sobra'], ['sem_dado', 'sem dado']])
    const skus = () => estado.filteredProducts.map((p) => p.sku)
    assert.equal(estado.filteredProducts.length, CELULAR.length, 'todos = a lista inteira')
    estado.reposicaoFiltro = 'falta'
    assert.deepEqual(skus(), ['dg053.ci', 'dg059.ci'])
    estado.reposicaoFiltro = 'sobra'
    assert.deepEqual(skus(), ['dg054.ci', 'dg055.ci', 'dg057.ci'], 'zero entra na sobra')
    estado.reposicaoFiltro = 'sem_dado'
    assert.deepEqual(skus(), ['dg056.ci', 'dg058.ci'])
    estado.search = 'dg058'
    assert.deepEqual(skus(), ['dg058.ci'], 'em AND com a busca')
    // Filtro sem resultado: a grade diz que é o filtro e oferece limpar, em
    // vez de mandar criar produto (são 7 na categoria).
    estado.search = 'dg054'
    estado.reposicaoFiltro = 'falta'
    assert.deepEqual(skus(), [], 'dg054 é sobra: nada falta')
    const vazia = linhaVazia(await deNovo())
    assert.equal(texto(vazia), MSG_FILTRO)
    assert.equal(texto(vazia.querySelector('button')), 'Limpar filtro')
    // Só a busca também conta como filtro.
    estado.reposicaoFiltro = 'todos'
    estado.search = 'nao-existe'
    assert.equal(texto(linhaVazia(await deNovo())), MSG_FILTRO)
    // A Cotação do Celular lista todos: a busca e a reposição são da aba
    // Importação e não aparecem lá.
    estado.reposicaoFiltro = 'sem_dado'
    estado.tab = 'cotacao'
    const cot = cotacaoCelular(await deNovo())
    assert.ok(!cot.texto.includes('Nenhum produto'), 'Cotação sem a linha vazia')
    assert.deepEqual(cot.linhas.map((tr) => texto(tr.children[1])), CELULAR.map((p) => p.sku), 'Cotação sem o filtro da Importação')
    estado.tab = 'mala'
    // "Limpar filtro" zera a busca e a reposição.
    estado.limparFiltro()
    assert.equal(estado.search, '')
    assert.equal(estado.reposicaoFiltro, 'todos')
    assert.equal(estado.filteredProducts.length, CELULAR.length)

    // Mexer no prazo recalcula na hora, sem recarregar os produtos.
    const antes = chamadas.length
    estado.config.tempo_reposicao = 60
    assert.equal(estado.reposicaoDe(estado.products[0]).valor, -300)
    const doc2 = await deNovo()
    assert.equal(texto(celulaDe(grade(doc2).corpo, 'dg053.ci').reposicao), '−300')
    assert.match(texto(doc2.querySelector('[data-formula-reposicao]')), /× \(60 \+ 30\) ÷ 30/)
    // 7 × (15 + 30) ÷ 30 = 10,5 → 11 necessárias: −11 (e não −10).
    estado.config.tempo_reposicao = 15
    assert.equal(estado.reposicaoDe(estado.products.find((p) => p.sku === 'dg059.ci')).valor, -11)
    estado.config.tempo_reposicao = ''
    assert.equal(estado.reposicaoDe(estado.products[0]), null, 'prazo apagado: sem conta')
    assert.match(estado.formulaReposicao, /\(\? \+ 30\)/)
    assert.equal(chamadas.length, antes, 'nada foi buscado na API')
    // O @change grava SÓ o prazo mexido e não recarrega a grade.
    estado.config.tempo_reposicao = 45
    await estado.saveConfig('tempo_reposicao')
    assert.deepEqual(chamadas.slice(antes).map((c) => `${c.method} ${c.url}`), ['PATCH /api/importacao/config?categoria=celular'])
    assert.deepEqual(chamadas.at(-1).body, { tempo_reposicao: 45 })
    assert.equal(estado.reposicaoDe(estado.products[0]).valor, -250)
    // A Tabela de Preços (mesma row) mudou o tempo de estoque para 40 depois
    // que esta tela abriu: gravar o tempo de reposição não volta ele a 30, e
    // a resposta põe o 40 na tela (100 × (50 + 40) ÷ 30 = 300).
    const prazos = () => ({ tempo_reposicao: estado.config.tempo_reposicao, tempo_estoque: estado.config.tempo_estoque })
    servidor.tempo_estoque = 40
    estado.config.tempo_reposicao = 50
    await estado.saveConfig('tempo_reposicao')
    assert.deepEqual(chamadas.at(-1).body, { tempo_reposicao: 50 })
    assert.deepEqual({ ...servidor }, { tempo_reposicao: 50, tempo_estoque: 40 }, 'o 40 da outra tela fica')
    assert.deepEqual(prazos(), { tempo_reposicao: 50, tempo_estoque: 40 }, 'a tela mostra o prazo atual do outro campo')
    assert.equal(estado.reposicaoDe(estado.products[0]).valor, -300)
    // E ao contrário: o tempo de estoque leva só ele.
    servidor.tempo_reposicao = 45
    estado.config.tempo_estoque = 30
    await estado.saveConfig('tempo_estoque')
    assert.deepEqual(chamadas.at(-1).body, { tempo_estoque: 30 })
    assert.deepEqual({ ...servidor }, { tempo_reposicao: 45, tempo_estoque: 30 })
    assert.deepEqual(prazos(), { tempo_reposicao: 45, tempo_estoque: 30 })
    assert.equal(estado.reposicaoDe(estado.products[0]).valor, -250)
    // Prazo recusado pelo servidor: a tela volta ao que está salvo (45 + 30).
    for (const [campo, ruim] of [['tempo_reposicao', 1000], ['tempo_reposicao', ''], ['tempo_reposicao', -1], ['tempo_estoque', 1000]]) {
      estado.config[campo] = ruim
      await estado.saveConfig(campo)
      assert.deepEqual(chamadas.slice(-2).map((c) => [c.method, c.body]), [['PATCH', { [campo]: ruim }], ['GET', undefined]])
      assert.deepEqual(prazos(), { tempo_reposicao: 45, tempo_estoque: 30 }, `${campo} ${JSON.stringify(ruim)} recusado volta ao salvo`)
      assert.match(estado.errorText, /^Prazo inválido: use um número inteiro de dias, de 0 a 999\./)
      assert.equal(estado.reposicaoDe(estado.products[0]).valor, -250)
    }
    estado.errorText = null

    // Enviar pro Bling: só o estado do envio muda na linha.
    const linha = estado.products[0]
    await estado.sendToBling(linha)
    const depois = estado.products[0]
    assert.equal(depois.bling_sync_status, 'pending')
    assert.equal(depois.estoque_bling, 0)
    assert.equal(depois.vendas_30d, 100)
    assert.deepEqual({ ...depois.lote_quantidades }, { l1: 5 })
    assert.deepEqual({ ...depois.lote_item_ids }, { l1: 'i1' })
  }

  // ═════════════════════════════════════════════ Mala (admin)
  {
    const lotes = [lote('ml25', 'ML25', { transportadora: null, taxa: null, frete_pct: null, adicional: null })]
    const { doc } = await abrir({ cat: 'mala', produtos: MALA, lotes })
    const { tabela, linhasCab, fixas, colunas, corpo } = grade(doc)
    assert.deepEqual(fixas.slice(0, 8).map(texto), ROTULOS)
    assert.doesNotMatch(texto(tabela.querySelector('thead')), FORA)
    assert.equal(linhasCab.length, 10)
    for (const th of fixas) assert.equal(th.rowSpan, 10)
    // Mala não gruda nada nem tem o divisor do Celular.
    assert.equal(tabela.querySelectorAll('.sticky').length, 0)
    assert.equal(tabela.querySelectorAll('.lote-divider-anchor').length, 0)
    assert.deepEqual(fixas.slice(0, 6).map((th) => th.style.minWidth), ['500px', '130px', '60px', '66px', '56px', '76px'])
    assert.equal(colunas, 6 + 2 + lotes.length * 2 + 2)
    for (const tr of corpo) assert.equal(tr.children.length, colunas)
    assert.equal(fixas[3].title, 'Estoque do SKU no Bling.')
    // 120 + 30 dias.
    const m1 = celulaDe(corpo, 'a015')
    assert.equal(texto(m1.reposicao), '−5')
    assert.ok(m1.reposicao.classList.contains(VERMELHO))
    assert.equal(texto(celulaDe(corpo, 'b045.18').reposicao), '1633')
    assert.match(texto(doc.querySelector('[data-formula-reposicao]')), /× \(120 \+ 30\) ÷ 30/)
  }

  // ═════════════════════════════════════════════ linha "nenhum produto"
  for (const [cat, auth, porLote, lotes] of [
    ['celular', ADMIN, 3, [lote('l1', 'AG232'), lote('l2', 'AG233')]],
    ['mala', LEITOR, 2, [lote('ml25', 'ML25')]],
    ['eletro', ADMIN, 2, []],
  ]) {
    const { doc, estado, deNovo } = await abrir({ cat, produtos: [], lotes, auth })
    const { colunas } = grade(doc)
    const acoes = auth === ADMIN ? 2 : 0
    assert.equal(colunas, 6 + 2 + lotes.length * porLote + acoes, `${cat}: colunas do cabeçalho`)
    // Lista vazia de verdade: manda criar produto, mesmo com filtro ligado
    // (limpar não traria nada).
    assert.equal(texto(linhaVazia(doc)), MSG_VAZIA[cat])
    estado.search = 'dg'
    estado.reposicaoFiltro = 'falta'
    assert.equal(texto(linhaVazia(await deNovo())), MSG_VAZIA[cat], `${cat}: sem produto nenhum, com filtro`)
    if (cat === 'celular') {
      estado.tab = 'cotacao'
      assert.equal(cotacaoCelular(await deNovo()).texto, 'Nenhum produto na categoria celular.')
      estado.tab = 'mala'
    }
    // Modal "Criar produto": os nomes da grade (Produto, Custo), nada de
    // "modelo bling"/"custo bling" — campos e nome gerado iguais.
    estado.openCreateModal()
    const modal = modalCriar(await deNovo())
    assert.equal(modal.rotulos[1], 'Produto', `${cat}: rótulo do modelo_bling`)
    assert.ok(modal.rotulos.includes('Custo (R$)'), `${cat}: rótulo do custo_bling`)
    assert.doesNotMatch(modal.texto, FORA)
    assert.ok(modal.texto.includes(DICA_NOME[cat]), `${cat}: dica do nome gerado`)
  }

  // ═════════════════════ Categorias na ordem da Tabela de Preços; Kit → Certificações
  {
    const botoes = (doc) => [...doc.querySelectorAll('button')].map(texto)
    const { doc, estado, chamadas, deNovo } = await abrir({ cat: undefined, produtos: CELULAR, lotes: [] })
    // Sem ?cat= abre no Celular, como a Tabela de Preços.
    assert.equal(estado.categoria, 'celular')
    const cats = botoes(doc).filter((b) => /^(Celular|Mala|Eletro) \(/.test(b)).map((b) => b.split(' ')[0])
    assert.deepEqual(cats, ['Celular', 'Mala', 'Eletro'])
    assert.ok(!chamadas.some((c) => c.url.startsWith('/api/importacao/kit')), 'nada da aba Kit é carregado')
    const abas = (d) => botoes(d).filter((b) => ['Importação', 'Resumo', 'Cotação', 'Certificações', 'Frete', 'Kit'].includes(b))
    assert.deepEqual(abas(doc), ['Importação', 'Resumo', 'Cotação', 'Certificações', 'Frete'])
    estado.tab = 'certificacoes'
    const d2 = await deNovo()
    assert.ok(d2.querySelector('[data-certificacoes]'), 'aba Certificações mostra a tela de Certificações')
    assert.ok(!botoes(d2).includes('Recarregar'), 'o Recarregar da Importação some (a tela tem o dela)')
    for (const cat of ['mala', 'eletro']) {
      const { doc: d } = await abrir({ cat, produtos: [], lotes: [] })
      assert.deepEqual(abas(d), ['Importação', 'Resumo', 'Cotação', 'Certificações'], `${cat}: abas`)
    }
    // Quem vê a Importação mas não vê Certificações não ganha a aba.
    const { doc: d3 } = await abrir({ cat: 'mala', produtos: [], lotes: [], auth: LEITOR })
    assert.deepEqual(abas(d3), ['Importação', 'Resumo', 'Cotação'])
    const comCert = { isAdmin: false, user: { role: 'user', permissions: { importacao: { view: true }, financeiro_suprimentos: { view: true } } } }
    const { doc: d4 } = await abrir({ cat: 'mala', produtos: [], lotes: [], auth: comCert })
    assert.deepEqual(abas(d4), ['Importação', 'Resumo', 'Cotação', 'Certificações'])
  }

  console.log('Importação: colunas Produto/Custo/Estoque/30d/Reposição ok')
})().catch((e) => {
  console.error(e)
  process.exit(1)
})
