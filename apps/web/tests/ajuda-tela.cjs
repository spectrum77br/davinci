// Run from apps/web: node tests/ajuda-tela.cjs
// Ajuda por tela (M-18, 23/11/2026): o "?" na barra do topo, à esquerda da lua,
// só aparece quando a tela aberta registrou a sua ajuda (useAjuda); clicar abre
// e fecha o painel à direita (440px, abaixo da barra do topo, rolagem própria),
// "Ajuda · <tela>", sem X; Esc fecha. A primeira ajuda é a da aba Tabela de Preços.
// Aqui: o composable de verdade (com useState de mentira), o "?" e o painel
// renderizados (Vue SSR), o script do painel com dublês de window/document, o
// layout e o texto da página.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { renderToString } = require('vue/server-renderer')
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')

const web = (rel) => fs.readFileSync(path.resolve(__dirname, '..', rel), 'utf8')
const transpile = (source) => {
  const out = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
    reportDiagnostics: true,
  })
  assert.deepEqual(out.diagnostics, [])
  return out.outputText
}
const semComentarios = (html) => html.replace(/<!--[\s\S]*?-->/g, '')

// useState do Nuxt: um estado reativo por chave (como o payload.state).
function novoEstado() {
  const estado = Vue.reactive({})
  return (chave, inicio) => {
    if (!(chave in estado)) estado[chave] = inicio()
    return Vue.toRef(estado, chave)
  }
}

function carregarComposable(useState, client = true) {
  const mod = { exports: {} }
  const fonte = web('composables/useAjuda.ts').replaceAll('import.meta.client', String(client))
  new Function('module', 'exports', 'require', 'useState', transpile(fonte))(
    mod, mod.exports, (n) => (n === 'vue' ? Vue : require(n)), useState,
  )
  return mod.exports
}

function compilarTemplate(fonte, nome) {
  const { code, errors } = compileTemplate({ source: fonte, filename: nome, id: nome, compilerOptions: { isTS: true } })
  assert.deepEqual(errors, [], `${nome}: o template compila`)
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(code))(require, mod, mod.exports)
  return mod.exports.render
}
async function renderizar(render, ctx, components = {}) {
  const app = Vue.createSSRApp({ setup: () => ctx, render, components })
  app.config.warnHandler = () => {}
  return semComentarios(await renderToString(app))
}

async function principal() {
  // ------------------------------------------------ o composable
  {
    const { useAjuda, useAjudaAtual, useAjudaAberta } = carregarComposable(novoEstado())
    const atual = useAjudaAtual()
    const aberta = useAjudaAberta()
    assert.equal(atual.value, null, 'sem página com ajuda: o "?" não aparece')
    assert.equal(aberta.value, false)

    const A = { titulo: 'Tela A', conteudo: [] }
    const B = { titulo: 'Tela B', conteudo: [] }
    const paginaA = Vue.effectScope()
    paginaA.run(() => useAjuda(A))
    assert.equal(Vue.toRaw(atual.value), A, 'a página registra a ajuda dela')
    aberta.value = true

    // Navegou de A para B: B registra antes de A desmontar — A não apaga a de B.
    const paginaB = Vue.effectScope()
    paginaB.run(() => useAjuda(B))
    paginaA.stop()
    assert.equal(Vue.toRaw(atual.value), B, 'a página que sai não apaga a ajuda da que entrou')
    assert.equal(aberta.value, true)

    // Saiu para uma tela sem ajuda: a ajuda some e o painel fecha.
    paginaB.stop()
    assert.equal(atual.value, null, 'saiu da página: a ajuda some')
    assert.equal(aberta.value, false, 'e o painel fecha')

    // Por função: só numa aba da página (como a Tabela de Preços).
    const aba = Vue.ref('tabela')
    const pagina = Vue.effectScope()
    pagina.run(() => useAjuda(() => (aba.value === 'tabela' ? A : null)))
    assert.equal(Vue.toRaw(atual.value), A)
    aberta.value = true
    aba.value = 'contas'
    await Vue.nextTick()
    assert.equal(atual.value, null, 'outra aba sem ajuda: o "?" some')
    assert.equal(aberta.value, false)
    aba.value = 'tabela'
    await Vue.nextTick()
    assert.equal(Vue.toRaw(atual.value), A, 'voltou à aba: a ajuda volta (fechada)')
    assert.equal(aberta.value, false)
    pagina.stop()
    assert.equal(atual.value, null)

    // No servidor não registra: a barra do topo já saiu sem o "?" (hidratação igual).
    const servidor = carregarComposable(novoEstado(), false)
    const escopo = Vue.effectScope()
    escopo.run(() => servidor.useAjuda(A))
    assert.equal(servidor.useAjudaAtual().value, null, 'no servidor: sem ajuda; o navegador registra')
    escopo.stop()
  }

  // ------------------------------------------------ o "?" da barra do topo
  {
    const fonte = web('components/AppTopbar.vue')
    const { descriptor, errors } = parse(fonte, { filename: 'AppTopbar.vue' })
    assert.deepEqual(errors, [])
    const setup = descriptor.scriptSetup.content
    assert.match(setup, /import \{[^}]*\bHelpCircle\b[^}]*\} from 'lucide-vue-next'/)
    assert.match(setup, /const ajuda = useAjudaAtual\(\)/)
    assert.match(setup, /const ajudaAberta = useAjudaAberta\(\)/)
    assert.match(setup, /function alternarAjuda\(\) \{\n {2}ajudaAberta\.value = !ajudaAberta\.value\n\}/, 'clicar de novo fecha (toggle)')
    const tpl = descriptor.template.content
    const a = tpl.indexOf('<button\n        v-if="ajuda"')
    const b = tpl.indexOf('</button>', a) + '</button>'.length
    assert.ok(a > 0, 'o "?" está na barra do topo')
    assert.ok(b < tpl.indexOf('@click="toggleTheme"'), 'à esquerda do botão de tema (lua)')
    assert.ok(a > tpl.indexOf('class="ml-auto'), 'no grupo da direita, junto da lua e do sino')

    const render = compilarTemplate(tpl.slice(a, b), 'ajuda-botao.vue')
    const icone = { name: 'HelpCircle', render: () => Vue.h('svg', { 'data-icone': 'HelpCircle' }) }
    const ctx = (o = {}) => ({ ajuda: { titulo: 'Tabela de Preços', conteudo: [] }, ajudaAberta: false, alternarAjuda() {}, ...o })
    const fechado = await renderizar(render, ctx(), { HelpCircle: icone })
    assert.match(fechado, /class="rounded-lg h-11 w-11 lg:h-9 lg:w-9 grid place-items-center hover:text-foreground hover:bg-muted text-muted-foreground"/, 'mesmo tamanho e classe do botão de tema')
    assert.match(fechado, /aria-label="Ajuda"/)
    assert.match(fechado, /title="Ajuda"/)
    assert.match(fechado, /aria-expanded="false"/)
    assert.match(fechado, /data-icone="HelpCircle"/)
    assert.doesNotMatch(fechado.replace(/<[^>]*>/g, ''), /Ajuda/, 'só o ícone, sem o texto "Ajuda"')
    const aberto = await renderizar(render, ctx({ ajudaAberta: true }), { HelpCircle: icone })
    assert.match(aberto, /hover:bg-muted text-foreground bg-muted"/, 'aberto: o ícone fica destacado')
    assert.match(aberto, /aria-expanded="true"/)
    assert.equal(await renderizar(render, ctx({ ajuda: null }), { HelpCircle: icone }), '', 'tela sem ajuda: sem o "?"')
  }

  // ------------------------------------------------ o painel (template)
  {
    const fonte = web('components/AppAjudaPainel.vue')
    const { descriptor, errors } = parse(fonte, { filename: 'AppAjudaPainel.vue' })
    assert.deepEqual(errors, [])
    const render = compilarTemplate(descriptor.template.content, 'AppAjudaPainel.vue')
    const ajuda = {
      titulo: 'Tabela de Preços',
      conteudo: [
        { titulo: 'Para que serve', itens: ['Mostra o preço.'] },
        { titulo: 'Barra', itens: [{ termo: 'Excel', texto: 'baixa tudo.' }] },
        { titulo: 'Passo a passo', numerada: true, itens: ['Um.', 'Dois.'] },
      ],
    }
    const html = await renderizar(render, { aberta: true, ajuda, topo: 57 })
    assert.match(html, /<aside id="davinci-ajuda" class="fixed right-0 bottom-0 z-\[35\] flex flex-col border-l bg-background shadow-xl"/, 'à direita, até o fim da tela, borda à esquerda e sombra')
    assert.match(html, /style="top:57px;width:min\(440px, 100vw\);"/, 'abaixo da barra do topo, 440px')
    assert.match(html, /aria-label="Ajuda · Tabela de Preços"/)
    assert.match(html, /<h2[^>]*>Ajuda · Tabela de Preços<\/h2>/)
    assert.match(html, /class="flex-1 overflow-y-auto/, 'rolagem própria')
    assert.match(html, /<h3[^>]*>Para que serve<\/h3><ul class="space-y-1 pl-5 text-muted-foreground list-disc"><li>Mostra o preço\.<\/li><\/ul>/)
    assert.match(html, /<li><strong class="font-semibold text-foreground">Excel<\/strong> — baixa tudo\.<\/li>/)
    assert.match(html, /<ol class="space-y-1 pl-5 text-muted-foreground list-decimal"><li>Um\.<\/li><li>Dois\.<\/li><\/ol>/, 'passo a passo numerado')
    assert.doesNotMatch(html, /<button/, 'sem botão X')
    assert.equal(await renderizar(render, { aberta: false, ajuda, topo: 57 }), '', 'fechado: nada')
    assert.equal(await renderizar(render, { aberta: true, ajuda: null, topo: 57 }), '', 'sem ajuda: nada')
    assert.doesNotMatch(descriptor.template.content, /v-html/, 'o texto é só texto')
  }

  // ------------------------------------------------ o painel (script): Esc fecha, mede o topo
  {
    const fonte = web('components/AppAjudaPainel.vue')
    const { descriptor } = parse(fonte, { filename: 'AppAjudaPainel.vue' })
    const script = compileScript(descriptor, { id: 'ajuda-painel' })
    const codigo = transpile(script.content.replaceAll('import.meta.client', 'true'))
    const desmontar = []
    const vue = { ...Vue, onBeforeUnmount: (fn) => desmontar.push(fn) }
    let escutas = []
    const win = {
      addEventListener: (ev, fn) => escutas.push([ev, fn]),
      removeEventListener: (ev, fn) => { escutas = escutas.filter(([e, f]) => e !== ev || f !== fn) },
    }
    let fimDaBarra = 92.4
    const doc = { querySelector: (sel) => (sel === '.app-topbar' ? { getBoundingClientRect: () => ({ bottom: fimDaBarra }) } : null) }
    const useState = novoEstado()
    const { useAjudaAtual, useAjudaAberta } = carregarComposable(useState)
    const mod = { exports: {} }
    new Function('module', 'exports', 'require', 'useAjudaAtual', 'useAjudaAberta', 'window', 'document', codigo)(
      mod, mod.exports, (n) => (n === 'vue' ? vue : require(n)), useAjudaAtual, useAjudaAberta, win, doc,
    )
    const escopo = Vue.effectScope()
    const s = escopo.run(() => mod.exports.default.setup({}, { expose: () => {} }))
    const atual = useAjudaAtual()
    const aberta = useAjudaAberta()
    const teclar = (o) => { for (const [ev, fn] of escutas) if (ev === 'keydown') fn({ key: '', defaultPrevented: false, ...o }) }

    assert.deepEqual(escutas, [], 'fechado: não escuta nada')
    atual.value = { titulo: 'Tabela de Preços', conteudo: [] }
    aberta.value = true
    await Vue.nextTick()
    assert.deepEqual(escutas.map(([e]) => e).sort(), ['keydown', 'resize'])
    assert.equal(s.topo.value, 92, 'começa onde a barra do topo termina (não a cobre)')
    fimDaBarra = 56
    for (const [ev, fn] of escutas) if (ev === 'resize') fn()
    assert.equal(s.topo.value, 56, 'mede de novo ao mudar o tamanho da janela')
    teclar({ key: 'a' })
    teclar({ key: 'Escape', defaultPrevented: true })
    assert.equal(aberta.value, true, 'outra tecla ou Esc já usado por outra coisa: continua aberto')
    teclar({ key: 'Escape' })
    assert.equal(aberta.value, false, 'Esc fecha')
    await Vue.nextTick()
    assert.deepEqual(escutas, [], 'fechou: as escutas vão embora')
    aberta.value = true
    await Vue.nextTick()
    assert.equal(escutas.length, 2)
    desmontar.forEach((fn) => fn())
    assert.deepEqual(escutas, [], 'desmontou: as escutas vão embora')
    escopo.stop()
  }

  // ------------------------------------------------ o layout
  {
    const layout = web('layouts/default.vue')
    assert.match(layout, /<AppTopbar v-show="!telaCheia" [^\n]*\/>\n[^\n]*<!--[^\n]*-->\n {6}<AppAjudaPainel v-if="!telaCheia" \/>/,
      'o painel logo depois da barra do topo; na tela cheia (sem barra do topo) não aparece')
  }

  // ------------------------------------------------ a Tabela de Preços
  {
    const filename = path.resolve(__dirname, '../pages/pricing/[tab].vue')
    const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
    assert.deepEqual(errors, [])
    const setup = descriptor.scriptSetup.content
    assert.match(setup, /\nuseAjuda\(\(\) => \(tab\.value === 'tabela' \? AJUDA_TABELA : null\)\)\n/, 'só na aba Tabela de Preços')
    const fonte = ts.createSourceFile(filename, setup, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
    const st = fonte.statements.find((s) => ts.isVariableStatement(s)
      && s.declarationList.declarations.some((d) => d.name.getText(fonte) === 'AJUDA_TABELA'))
    assert.ok(st, 'AJUDA_TABELA existe na página')
    const AJUDA = new Function(`${transpile(st.getText(fonte))}\nreturn AJUDA_TABELA`)()
    assert.equal(AJUDA.titulo, 'Tabela de Preços')
    assert.deepEqual(AJUDA.conteudo.map((s) => s.titulo), ['Para que serve', 'Como ler uma célula', 'Barra de botões', 'Passo a passo', 'Observação'])
    const secao = (t) => AJUDA.conteudo.find((s) => s.titulo === t)
    const termos = (t) => secao(t).itens.map((i) => i.termo)
    assert.deepEqual(termos('Como ler uma célula'), ['Número', '"—"', 'Aviãozinho', 'Cartão vermelho', '"(!) anúncio 3863 +2"', 'Olho', 'Cor de fundo'])
    assert.deepEqual(termos('Barra de botões'), ['Buscar', 'Plataforma e Contas', 'Layout', 'Excel', 'Atualizar', 'Reposição (ADM)', 'Margem (ADM)'])
    assert.equal(secao('Passo a passo').numerada, true)
    assert.equal(secao('Passo a passo').itens.length, 5)
    const tudo = JSON.stringify(AJUDA)
    for (const trecho of ['custo do kit', 'aba Produtos', 'aba Contas', 'conta sem integração', 'sem anúncio vinculado',
      'anúncio pausado/encerrado', 'último envio com erro', 'fora da margem', 'ML, Shopee, Amazon, TikTok', 'Não grava nada',
      'só a busca filtra', 'Geral ou Por conta', 'muda também a tela Importação', 'Atualizar › Conferir', 'Atualizar anúncio',
      'permissão de editar a Tabela de Preços']) {
      assert.ok(tudo.includes(trecho), `o texto fala de: ${trecho}`)
    }
    // O SFC inteiro continua compilando.
    const script = compileScript(descriptor, { id: 'pricing-tab', inlineTemplate: false })
    assert.ok(script.bindings.AJUDA_TABELA)
  }

  // ------------------------------------------------ o painel: o cartãozinho vermelho (M-24)
  {
    const fonte = web('components/AppAjudaPainel.vue')
    const { descriptor } = parse(fonte, { filename: 'AppAjudaPainel.vue' })
    const render = compilarTemplate(descriptor.template.content, 'AppAjudaPainel.vue')
    const ajuda = { titulo: 'Faturamento', conteudo: [{ titulo: 'Colunas', itens: [
      { termo: 'Pendências', cartao: true, texto: 'quantos cartões.' },
      { termo: 'Pedidos', texto: 'os pedidos.' },
    ] }] }
    const html = await renderizar(render, { aberta: true, ajuda, topo: 57 })
    assert.match(html, /<h2[^>]*>Ajuda · Faturamento<\/h2>/)
    assert.match(html, /<li><span class="mr-1\.5 inline-block h-\[10px\] w-\[7px\] rotate-12 rounded-\[1px\] bg-red-600 align-middle shadow-sm" role="img" aria-label="cartão vermelho" data-cartao><\/span><strong class="font-semibold text-foreground">Pendências<\/strong> — quantos cartões\.<\/li>/,
      'o cartãozinho vermelho antes de "Pendências"')
    assert.match(html, /<li><strong class="font-semibold text-foreground">Pedidos<\/strong> — os pedidos\.<\/li>/, 'sem `cartao`, como antes')
    assert.equal((html.match(/data-cartao/g) || []).length, 1)
    assert.match(web('composables/useAjuda.ts'), /export type AjudaItem = string \| \{ termo: string; texto: string; cartao\?: boolean \}/)
  }

  // ------------------------------------------------ a aba Faturamento (M-24)
  {
    const filename = path.resolve(__dirname, '../components/FaturamentoPainel.vue')
    const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
    assert.deepEqual(errors, [])
    const setup = descriptor.scriptSetup.content
    // Registra a ajuda antes do primeiro `await` (a página ainda está montando).
    const iAjuda = setup.indexOf('const ajuda = computed(() => ajudaFaturamento(isAdmin.value))\nuseAjuda(ajuda)\n')
    assert.ok(iAjuda > 0, 'o Faturamento registra a ajuda dele')
    assert.ok(iAjuda < setup.indexOf('\nawait load()'), 'antes do primeiro await')
    const fonte = ts.createSourceFile(filename, setup, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
    const fn = fonte.statements.find((s) => ts.isFunctionDeclaration(s) && s.name?.text === 'ajudaFaturamento')
    assert.ok(fn, 'ajudaFaturamento existe')
    const ajudaFaturamento = new Function(`${transpile(fn.getText(fonte))}\nreturn ajudaFaturamento`)()
    const ADM = ajudaFaturamento(true)
    const LEITOR = ajudaFaturamento(false)
    assert.equal(ADM.titulo, 'Faturamento', 'o painel diz "Ajuda · Faturamento"')
    assert.deepEqual(ADM.conteudo.map((s) => s.titulo), ['Para que serve', 'Como ler as colunas', 'Barra', 'Bom saber'])
    const secao = (A, t) => A.conteudo.find((s) => s.titulo === t)
    const termos = (A, t) => secao(A, t).itens.map((i) => i.termo)
    assert.deepEqual(termos(ADM, 'Como ler as colunas'), ['Conta, Plataforma, Equipe e Tipo', 'Pedidos', 'Faturamento', 'Ticket médio',
      'Pendências', 'Investimento', '% Investimento', 'Margem (só ADM)'])
    assert.deepEqual(termos(LEITOR, 'Como ler as colunas'), ['Conta, Plataforma, Equipe e Tipo', 'Pedidos', 'Faturamento', 'Ticket médio',
      'Pendências', 'Investimento', '% Investimento'], 'Margem (só ADM) só aparece para administrador')
    assert.equal(secao(ADM, 'Como ler as colunas').itens.find((i) => i.termo === 'Pendências').cartao, true, 'com o cartão vermelho')
    assert.deepEqual(termos(ADM, 'Barra'), ['Buscar', 'Período', 'Plataforma, Contas e Equipes', 'Atualizar'])
    assert.equal(secao(ADM, 'Bom saber').itens.length, 2)
    const tudo = JSON.stringify(ADM)
    for (const trecho of [
      'Mostra quanto cada conta vendeu no período: pedidos, faturamento e ticket médio, junto com as pendências da Tabela de Preços e o investimento (afiliados + Ads, do Marketing).',
      'pedidos Em aberto, Em andamento e Entregues. Cancelados e devolvidos não entram. Cada pedido conta uma vez.',
      'soma do total dos pedidos, como está no Bling. É o valor bruto: não desconta comissão nem reembolso.',
      'faturamento ÷ pedidos.',
      'quantos cartões vermelhos a conta tem na Tabela de Preços (Celular, Eletro e Mala).',
      // M-36 (24/11/2026): "afiliados + Ads, do Marketing".
      'afiliados + Ads, do Marketing, na conta no período. Shopee: as semanas fechadas da Conferência (a semana que cruza o período entra pelos dias; a semana atual entra quando fechar). Mercado Livre: o Ads de cada dia. Passe o mouse para ver as duas partes. \\"—\\" quer dizer sem dado.',
      'investimento ÷ faturamento da conta. Na linha Total, a soma dos investimentos ÷ o faturamento total.',
      // M-37 (24/11/2026): a Margem virou a margem real da Conciliação.
      'resultado das vendas no período, da Conciliação: (sobrou de todos os pedidos − Ads) ÷ custo dos produtos. Pedido ainda não pago entra pelo valor previsto; pedido sem valor de venda fica fora até a plataforma calcular a venda. Só o Ads é descontado: os afiliados já saem do valor de cada pedido. Negativa em vermelho. \\"—\\" quer dizer conta fora da Conciliação, sem pedido no período (fica fora do Total, com o Ads dela) ou período que começa antes do início dela (24/10/2026). Passe o mouse para ver a conta.',
      'pelo nome da conta.',
      'por mês ou personalizado (de/até). Vale a data da venda.',
      'marque o que quer ver. Quem tem equipe só vê as contas dela.',
      'relê os números. Os pedidos chegam sozinhos do Bling; o botão não busca pedido novo.',
      'Pedido com etiqueta emitida sai da conta e volta quando fica Em andamento ou Entregue. O total do mês pode cair e subir de novo.',
      'A linha Total soma só o que está filtrado.',
    ]) assert.ok(tudo.includes(trecho), `o texto do pedido: ${trecho}`)
    assert.ok(!JSON.stringify(LEITOR).includes('Margem'), 'nada de Margem para quem não é ADM')
    assert.ok(!JSON.stringify(LEITOR).includes('Conciliação'), 'nem da conta dela')
    assert.ok(!tudo.includes('aba Contas, da menor à maior'), 'a faixa da aba Contas saiu (M-37)')
    assert.doesNotMatch(descriptor.template.content, /v-html/)

    // Com o composable de verdade: na aba Faturamento a página (só Tabela) solta
    // a dela e o Faturamento registra a sua; ao voltar, fica a da Tabela.
    const { useAjuda, useAjudaAtual, useAjudaAberta } = carregarComposable(novoEstado())
    const atual = useAjudaAtual()
    const aberta = useAjudaAberta()
    const TABELA = { titulo: 'Tabela de Preços', conteudo: [] }
    const aba = Vue.ref('tabela')
    const pagina = Vue.effectScope()
    pagina.run(() => useAjuda(() => (aba.value === 'tabela' ? TABELA : null)))
    assert.equal(Vue.toRaw(atual.value), TABELA)
    aba.value = 'faturamento'
    await Vue.nextTick()
    const admin = Vue.ref(true)
    const painel = Vue.effectScope()
    painel.run(() => useAjuda(Vue.computed(() => ajudaFaturamento(admin.value))))
    assert.equal(atual.value.titulo, 'Faturamento', '"?" na aba Faturamento abre a Ajuda · Faturamento')
    aberta.value = true
    admin.value = false
    await Vue.nextTick()
    assert.ok(!JSON.stringify(atual.value).includes('Margem'), 'deixou de ser ADM: sem o item da Margem')
    aba.value = 'tabela'
    await Vue.nextTick()
    painel.stop()
    assert.equal(Vue.toRaw(atual.value), TABELA, 'voltou à Tabela: a ajuda dela')
    pagina.stop()
    assert.equal(atual.value, null)
  }

  console.log('PASS: ajuda por tela — "?" à esquerda da lua só com ajuda, abre/fecha (toggle), destacado aberto, painel 440px abaixo do topo com rolagem, Esc fecha, ajuda some ao sair, Tabela de Preços com o texto; Faturamento (M-24) com o texto do pedido, cartão vermelho e Margem só para ADM')
}

principal().catch((e) => {
  console.error(e)
  process.exit(1)
})
