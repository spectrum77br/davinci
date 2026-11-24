// Run from apps/web: node tests/faturamento-barra.cjs
// M-22 (24/11/2026, prévia aprovada em 23/11): a aba Faturamento com a barra da
// aba Tabela de Preços e a tabela no padrão da aba Contas. Renderiza o SFC de
// verdade (Vue SSR); só a API, o login e o popover (reka-ui, que no servidor
// não abre) são de mentira — o popover de mentira desenha o conteúdo sempre.
// Confere:
//  - a barra: Buscar | Período ▾ | Plataforma ▾ | Contas ▾ | Equipes ▾ |
//    Atualizar, "N contas" à direita, com as classes da Tabela (h-7, btn btn-sm);
//  - os popovers (caixas, "Marcar todas · Limpar", Buscar conta…, Equipes só
//    com o número e só as que o servidor manda);
//  - as colunas e o formato: Conta | Plataforma | Equipe | Tipo | Pedidos |
//    Faturamento | Ticket médio | Pendências | Investimento | % Investimento |
//    Margem (só ADM); cabeçalho e Total fixos;
//  - Pendências pela regra da grade (lib/cartaoVermelho.ts): grade e
//    conferência das três categorias, uma vez; "0", "—" e o cartãozinho;
//  - Investimento "—" sem dado, "*" com "dado guardado desde dd/mm";
//  - M-36: Investimento = afiliados + Ads do Marketing; o balão "Afiliados
//    R$ X + Ads R$ Y" (pedaço sem dado = "—"), até quando vão as semanas
//    fechadas da Conferência; % Investimento e Total com a soma;
//  - M-37: Margem = margem real da Conciliação, 1 casa ("25,2%"), negativa em
//    vermelho, "—" fora da Conciliação ou antes do início dela; o balão "Sobrou
//    R$ X − Ads R$ Y ÷ custo R$ Z · N pedidos: …"; Total = (Σ sobrou − Σ Ads)
//    ÷ Σ custo das contas filtradas; nada disso para quem não é ADM;
//  - busca e filtros valem para a tabela E o Total; Atualizar relê tudo;
//  - Período: Por mês / Personalizado, uma caixa por vez.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { renderToString } = require('vue/server-renderer')
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')

const filename = path.resolve(__dirname, '../components/FaturamentoPainel.vue')
const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
assert.deepEqual(errors, [])
const script = compileScript(descriptor, { id: 'faturamento-barra' })
const template = compileTemplate({
  source: descriptor.template.content,
  filename,
  id: 'faturamento-barra',
  compilerOptions: { bindingMetadata: script.bindings },
})
assert.deepEqual(template.errors, [])

// reka-ui de mentira: o conteúdo do popover sempre desenhado (no SSR o de
// verdade fica fechado).
const passa = (tag) => Vue.defineComponent({
  props: ['asChild'],
  inheritAttrs: !!tag,
  setup: (_, { slots }) => () => (tag ? Vue.h(tag, null, slots.default?.()) : slots.default?.()),
})
const REKA = {
  PopoverRoot: passa(null),
  PopoverTrigger: passa(null),
  PopoverPortal: passa(null),
  PopoverContent: passa('div'),
}

function evaluate(source, globals = {}) {
  const output = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
    reportDiagnostics: true,
  })
  assert.deepEqual(output.diagnostics, [])
  const exports = {}
  const names = Object.keys(globals)
  new Function('exports', 'require', ...names, output.outputText)(
    exports,
    (name) => {
      if (name === 'reka-ui') return REKA
      if (name.startsWith('~/')) return evaluate(fs.readFileSync(path.resolve(__dirname, '..', `${name.slice(2)}.ts`), 'utf8'))
      return require(name)
    },
    ...names.map((name) => globals[name]),
  )
  return exports
}
const render = evaluate(template.code).render
const L = evaluate(fs.readFileSync(path.resolve(__dirname, '../lib/cartaoVermelho.ts'), 'utf8'))

// ---------------------------------------------------------------- dados falsos
const RESPOSTA = {
  itens: [
    { store_id: 'S1', loja: 'mega', tipo: 'shopee', departments: ['eletro', 'celular'], pedidos: 316,
      faturamento: 340373.63, ticket_medio: 1077.13, equipe: 102, contas_preco: ['A1', 'A2'],
      // M-36: o exemplo do pedido (Shopee pela Conferência, semanas até 21/11).
      investimento: 8430.57, investimento_afiliados: 5711.94, investimento_ads: 2718.63,
      investimento_desde: '2026-10-18', investimento_parcial: false, investimento_ate: '2026-11-21',
      // M-37: o exemplo do pedido — (73.017,55 − 2.718,63) ÷ 278.695,00 = 25,2%.
      margem: { valor: (73017.55 - 2718.63) / 278695, sobrou: 73017.55, ads: 2718.63, ads_com_dado: true,
        ads_ate: '2026-11-21', custo: 278695, pedidos: 397, pagos: 172, previstos: 182, cancelados: 33, sem_valor: 10,
        inicio: '2026-10-24', antes_do_inicio: false } },
    { store_id: 'S2', loja: 'forpaper', tipo: 'mercadolivre', departments: ['mala'], pedidos: 10,
      faturamento: 1000, ticket_medio: 100, equipe: 103, contas_preco: ['A3', 'A3c'],
      investimento: 150, investimento_afiliados: null, investimento_ads: 150,
      investimento_desde: '2026-11-15', investimento_parcial: true,
      // Negativa: (100 − 150) ÷ 1.000 = −5,0%, em vermelho.
      margem: { valor: -0.05, sobrou: 100, ads: 150, ads_com_dado: true, ads_ate: null, custo: 1000, pedidos: 10,
        pagos: 6, previstos: 3, cancelados: 1, sem_valor: 0, inicio: '2026-10-24', antes_do_inicio: false } },
    { store_id: 'S3', loja: 'vita', tipo: 'ml', departments: [], pedidos: 0, faturamento: 0, ticket_medio: 0,
      equipe: 101, contas_preco: ['Z9'], investimento: 0, investimento_afiliados: null, investimento_ads: 0,
      investimento_desde: '2026-05-20', investimento_parcial: false, margem: null },
    { store_id: '999', loja: 'Sem cadastro (999)', tipo: null, departments: [], pedidos: 1, faturamento: 20,
      ticket_medio: 20,
      // A órfã (Temu sem cadastro) tem a Margem pela loja do Bling; sem Ads.
      margem: { valor: 0.5, sobrou: 10, ads: 0, ads_com_dado: false, ads_ate: null, custo: 20, pedidos: 2, pagos: 1,
        previstos: 0, cancelados: 0, sem_valor: 1, inicio: '2026-10-24', antes_do_inicio: false } },
  ],
  total_pedidos: 327, total_faturamento: 341393.63,
  start: '2026-11-01T00:00:00', end: '2026-12-01T00:00:00', teams: [103, 101, 102],
  equipes: [{ numero: 101, nome: 'Londres' }, { numero: 102, nome: 'Israel' }, { numero: 103, nome: 'Marrocos' }],
  team: null,
}
const semMargem = (r) => ({ ...r, itens: r.itens.map(({ margem, ...it }) => it) })
const cel = (p, a, extra = {}) => ({ pricing_account_id: a, pricing_product_id: p, price: 100, source: 'calc', cell_status: 'auto', ...extra })
const GRADES = {
  // A1: o último envio deu erro em P1 (1 cartão). A3 (forpaper, kit) com o
  // "sem anúncio vinculado" em P1 e a coluna de catálogo A3c com o anúncio de
  // catálogo pausado em P2: 2 cartões.
  celular: {
    accounts: [
      { id: 'A1', canal: 'kit', conta_base_id: null, integration_id: 'I1' },
      { id: 'A3', canal: 'kit', conta_base_id: null, integration_id: 'I3' },
      { id: 'A3c', canal: 'catalogo', conta_base_id: 'A3', integration_id: null },
    ],
    products: [{ id: 'P1' }, { id: 'P2' }],
    cells: [
      cel('P1', 'A1', { cell_status: 'error' }), cel('P2', 'A1'),
      cel('P1', 'A3', { cell_status: 'no_link' }), cel('P2', 'A3'),
      cel('P1', 'A3c'),
      cel('P2', 'A3c', { catalogo: { bloqueio: 'pausado', texto: null, anuncios: [{ external_id: 'MLB9', status: 'paused', bloqueio: 'pausado' }] } }),
    ],
  },
  // A2 (mega eletro) sem integração: cartão nas 3 linhas.
  eletro: {
    accounts: [{ id: 'A2', canal: 'kit', conta_base_id: null, integration_id: null }],
    products: [{ id: 'P3' }, { id: 'P4' }, { id: 'P5' }],
    cells: [cel('P3', 'A2')],
  },
  mala: { accounts: [], products: [], cells: [] },
}
const CONFERENCIAS = {
  // Fora da margem (Tabela 100 × anúncio 120) em A1/P2: +1 cartão.
  celular: {
    celulas: [{ pricing_account_id: 'A1', pricing_product_id: 'P2', preco_tabela: 100, situacao: 'conferida', motivo: null,
      conferido_em: '2026-11-24T12:00:00Z', job_id: null,
      anuncios: [{ anuncio: 'X', variacao: null, preco: 120, preco_promocional: null, ativo: true, motivo: null }] }],
    margem: { margem_pct: '5.00', geral_ligada: true, por_produto_ligada: true, produtos: {} },
    sem_vinculo: [{ pricing_account_id: 'A3', pricing_product_id: 'P1' }],
  },
  eletro: { celulas: [], margem: {}, sem_vinculo: [] },
  mala: { celulas: [], margem: {}, sem_vinculo: [] },
}
// O esperado, pela regra da grade: mega = A1 (2) + A2 (3) = 5; forpaper = A3 (1) + A3c (1) = 2.
const contagem = new Map()
for (const dep of ['celular', 'eletro', 'mala']) {
  for (const [k, n] of L.cartoesPorConta(GRADES[dep], CONFERENCIAS[dep], dep)) contagem.set(k, (contagem.get(k) ?? 0) + n)
}
assert.deepEqual(Object.fromEntries(contagem), { A1: 2, A3: 1, A3c: 1, A2: 3 })

async function pagina({ role = 'admin', permissoes = {}, resposta = RESPOSTA } = {}) {
  let instance
  const chamadas = []
  const falha = { faturamento: null, precos: null }
  const ajudas = []
  const auth = Vue.reactive({ user: { role, permissions: permissoes } })
  const component = evaluate(script.content, {
    useAuthStore: () => auth,
    useIsAdmin: () => Vue.computed(() => auth.user?.role === 'admin'),
    useCan: (res, acao) => Vue.computed(() => auth.user?.role === 'admin' || auth.user?.permissions?.[res]?.[acao] === true),
    useAjuda: (a) => ajudas.push(a),
    useApi: () => {
      instance = Vue.getCurrentInstance()
      return {
        api: async (url) => {
          chamadas.push(url)
          const u = new URL(url, 'https://teste.invalid')
          if (u.pathname === '/api/faturamento') {
            if (falha.faturamento) throw falha.faturamento
            return structuredClone(resposta)
          }
          if (falha.precos) throw falha.precos
          const dep = u.searchParams.get('department')
          if (u.pathname === '/api/pricing/grid') return structuredClone(GRADES[dep])
          if (u.pathname === '/api/pricing/conferencia') return structuredClone(CONFERENCIAS[dep])
          throw new Error(`url inesperada ${url}`)
        },
      }
    },
  }).default
  component.render = render
  await renderToString(Vue.createSSRApp(component, { embutido: true }))
  const state = instance.setupState
  return {
    state, chamadas, auth, falha, ajudas,
    html: async () => (await renderToString(Vue.createSSRApp({ setup: () => state, render }))).replace(/<!--[\s\S]*?-->/g, ''),
  }
}

// Chips lado a lado (<span>…</span><span>…</span>) separados por espaço.
const texto = (m) => m.replace(/<\/span>\s*<span/g, '</span> <span').replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim()
const celulas = (html, tag = 'td') => [...html.matchAll(new RegExp(`<${tag}\\b[^>]*>([\\s\\S]*?)</${tag}>`, 'g'))].map((m) => texto(m[1]))
const linhas = (html) => {
  const corpo = html.match(/<tbody>([\s\S]*?)<\/tbody>/)[1]
  return [...corpo.matchAll(/<tr\b[^>]*>([\s\S]*?)<\/tr>/g)].map((m) => celulas(m[1]))
}
const rodape = (html) => {
  const m = html.match(/<tfoot\b[^>]*>([\s\S]*?)<\/tfoot>/)
  return m ? celulas(m[1]) : null
}
const cabecalho = (html) => celulas(html.match(/<thead\b[^>]*>([\s\S]*?)<\/thead>/)[1], 'th')
const brl = (v) => v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' }).replace(/\s+/g, ' ')
const menu = (html, nome) => {
  const i = html.indexOf(`data-menu-${nome}`)
  assert.ok(i > 0, `menu ${nome}`)
  const ini = html.lastIndexOf('<div', i)
  let fim = ini
  let prof = 0
  const re = /<\/?div\b[^>]*>/g
  re.lastIndex = ini
  for (let m; (m = re.exec(html));) {
    prof += m[0].startsWith('</') ? -1 : 1
    if (prof === 0) { fim = re.lastIndex; break }
  }
  return html.slice(ini, fim)
}
const caixas = (bloco, attr) => [...bloco.matchAll(new RegExp(`<input[^>]*data-${attr}="([^"]*)"[^>]*>`, 'g'))]
  .map((m) => [m[1], /\bchecked\b/.test(m[0])])

async function principal() {
  // ---------------------------------------------------------------- a barra
  const adm = await pagina()
  await adm.state.carregarPendencias()
  let html = await adm.html()
  const barra = html.slice(html.indexOf('data-faturamento-barra'), html.indexOf('data-faturamento-tabela'))
  const ordem = ['data-campo="faturamento-busca"', 'data-filtro="periodo"', 'data-filtro="plataforma"', 'data-filtro="contas"',
    'data-filtro="equipes"', 'data-acao="atualizar"', 'data-contador']
  const pos = ordem.map((x) => barra.indexOf(x))
  assert.ok(pos.every((p, i) => p > 0 && (i === 0 || p > pos[i - 1])), `ordem da barra: ${pos}`)
  assert.match(barra, /^data-faturamento-barra/, 'barra')
  assert.match(html, /<div class="flex flex-wrap items-center gap-2" data-faturamento-barra>/)
  assert.match(barra, /placeholder="Buscar por nome\.\.\." aria-label="Buscar por nome da conta" class="h-7 border rounded pl-7 pr-2 py-0 text-sm bg-background w-full"/)
  for (const f of ['periodo', 'plataforma', 'contas', 'equipes']) {
    assert.match(barra, new RegExp(`<button type="button" class="btn btn-sm h-7 py-0" data-filtro="${f}"`), `${f}: botão da Tabela`)
  }
  assert.match(barra, /class="btn btn-sm h-7 py-0 bg-blue-600 text-white hover:bg-blue-700 hover:text-white" data-acao="atualizar"/, 'Atualizar azul, como o da Tabela')
  assert.match(texto(barra), /Período: [a-z]{3}\/\d{4}/, 'o botão mostra o período ("Período: nov/2026")')
  assert.match(barra, />4 contas<\/span>/, '"N contas" à direita')
  assert.doesNotMatch(html, /Recarregar|Pedidos faturáveis|faturamento-tipo|Todos os tipos|<select\b|<h1\b|<h2\b/,
    'saíram o título, a descrição, o Recarregar, a faixa e o select Tipo')
  assert.doesNotMatch(html, /<article\b/, 'sem os cartões do celular: uma tabela só')

  // Popovers: caixas e "Marcar todas · Limpar".
  const plat = menu(html, 'plataforma')
  assert.deepEqual(caixas(plat, 'plataforma'), [['ml', true], ['shopee', true], ['sem', true]], 'plataformas presentes (ML de "ml" e "mercadolivre" é uma só)')
  assert.match(texto(plat), /^Marcar todas\s*·\s*Limpar Mercado Livre Shopee Sem plataforma$/)
  const contas = menu(html, 'contas')
  assert.match(contas, /placeholder="Buscar conta…"/)
  assert.deepEqual(caixas(contas, 'conta').map(([v]) => v), ['forpaper', 'mega', 'sem cadastro (999)', 'vita'])
  const equipes = menu(html, 'equipes')
  assert.deepEqual(caixas(equipes, 'equipe'), [['101', true], ['102', true], ['103', true]])
  assert.match(texto(equipes), /^Marcar todas\s*·\s*Limpar 1 2 3$/, 'só o número da equipe')
  const periodo = menu(html, 'periodo')
  assert.deepEqual([...periodo.matchAll(/data-campo="(periodo-[a-z]+)"/g)].map((m) => m[1]),
    ['periodo-mes', 'periodo-personalizado', 'periodo-de', 'periodo-ate'])
  assert.match(periodo, /<input type="checkbox" class="size-4" checked data-campo="periodo-mes"/)
  assert.doesNotMatch(periodo, /checked data-campo="periodo-personalizado"/)

  // ---------------------------------------------------------------- a tabela
  assert.deepEqual(cabecalho(html), ['Conta', 'Plataforma', 'Equipe', 'Tipo', 'Pedidos', 'Faturamento', 'Ticket médio',
    'Pendências', 'Investimento', '% Investimento', 'Margem'])
  assert.match(html, /<div class="border rounded-lg overflow-auto max-h-\[calc\(100vh-240px\)\]"/, 'moldura da aba Contas')
  assert.match(html, /<thead class="sticky top-0 bg-muted z-10">/, 'cabeçalho fixo')
  assert.match(html, /<tfoot class="sticky bottom-0 z-10 bg-muted font-semibold" data-total>/, 'Total fixo embaixo')
  assert.match(html, /<th class="text-left px-2 py-2 text-\[11px\] font-semibold leading-\[14px\] border-b border-border min-w-\[140px\]">Conta<\/th>/)
  assert.match(html, /<td class="border border-border px-2 py-1 text-xs text-left font-medium">mega<\/td>/, 'nome à esquerda')
  assert.match(html, /<td class="border border-border px-2 py-1 text-xs text-right tabular-nums font-medium">R\$/, 'R$ à direita')
  const [mega, forpaper, vita, orfa] = linhas(html)
  assert.deepEqual(mega, ['mega', 'Shopee', '2', 'Celular Eletro', '316', brl(340373.63), brl(1077.13), '5', brl(8430.57), '2,48%', '25,2%'],
    'o exemplo do pedido (Tipo em Celular, Eletro, Mala); % Investimento = investimento ÷ faturamento, 2 casas')
  assert.deepEqual(forpaper, ['forpaper', 'Mercado Livre', '3', 'Mala', '10', brl(1000), brl(100), '2', `${brl(150)}*`, '15,00%', '-5,0%'])
  assert.deepEqual(vita, ['vita', 'Mercado Livre', '1', '—', '0', brl(0), brl(0), '—', brl(0), '—', '—'],
    'conta fora da grade: "—"; investimento zero é dado (R$ 0,00), mas % sem faturamento é "—"')
  assert.deepEqual(orfa, ['Sem cadastro (999)', '—', '—', '—', '1', brl(20), brl(20), '—', '—', '—', '50,0%'])
  // M-36: o balão de cada conta — "Afiliados R$ X + Ads R$ Y" e, embaixo,
  // desde quando há dado (o "*") e até quando vão as semanas fechadas.
  const balao = (trecho, attr = 'data-investimento') => {
    const m = trecho.match(new RegExp(`title="([^"]*)" ${attr}>`))
    assert.ok(m, `balão ${attr}`)
    return m[1].replace(/\u00a0/g, ' ').replace(/&quot;/g, '"')
  }
  assert.match(html, /data-investimento>R\$[^<]*<span class="text-muted-foreground" data-parcial>\*<\/span>/)
  assert.match(html, /title="Afiliados \+ Ads, do Marketing, no período\. Passe o mouse na conta para ver as duas partes\. — = sem dado\.">Investimento<\/th>/,
    'a dica do cabeçalho diz afiliados + Ads, do Marketing')
  assert.doesNotMatch(html, /Ads ou afiliados|afiliados ainda não entra|o DaVinci não a coleta/)
  const tr = (loja) => html.match(new RegExp(`<tr\\b[^>]*data-linha-faturamento>(?:(?!</tr>)[\\s\\S])*?>${loja}</td>[\\s\\S]*?</tr>`))[0]
  assert.equal(balao(tr('mega')), `Afiliados ${brl(5711.94)} + Ads ${brl(2718.63)}\nsemanas fechadas até 21/11 (a semana atual entra quando fechar)`,
    'o exemplo do pedido: "Afiliados R$ X + Ads R$ Y"')
  assert.equal(balao(tr('forpaper')), `Afiliados — + Ads ${brl(150)}\ndado guardado desde 15/11`,
    'ML: afiliados não coletados ("—"); o período começa antes do 1º dia guardado')
  assert.equal(balao(tr('vita')), `Afiliados — + Ads ${brl(0)}`, 'Ads zero é dado')
  assert.equal(balao(tr('Sem cadastro \\(999\\)')), 'Sem dado de afiliados nem de Ads no Marketing para esta conta no período.')
  // M-35: Pendências numa grade de 2 colunas (cartão 7px | número 2,4em,
  // 6px entre elas), o lugar do cartão reservado mesmo sem cartão — cartões e
  // números ficam um embaixo do outro de linha para linha.
  const grade = '<span class="inline-grid grid-cols-\\[7px_2\\.4em\\] items-center gap-x-\\[6px\\]">'
  assert.match(tr('mega'), new RegExp(`data-pendencias>${grade}<span class="block h-\\[10px\\] w-\\[7px\\] rotate-12 rounded-\\[1px\\] bg-red-600 shadow-sm" aria-hidden="true" data-cartao></span><span class="text-left">5</span></span>`),
    'o cartãozinho vermelho na coluna dele + número à esquerda na coluna ao lado')
  assert.doesNotMatch(tr('vita'), /data-cartao/)
  assert.match(tr('vita'), new RegExp(`data-pendencias>${grade}<span aria-hidden="true"></span><span class="text-left text-muted-foreground">—</span></span>`),
    'sem cartão: o lugar dele fica vazio e o "—" fica na coluna dos números')
  const rodapeHtml = html.match(/<tfoot\b[^>]*>([\s\S]*?)<\/tfoot>/)[1]
  assert.match(rodapeHtml, new RegExp(`${grade}<span class="block h-\\[10px\\] w-\\[7px\\] rotate-12 rounded-\\[1px\\] bg-red-600 shadow-sm" aria-hidden="true" data-cartao></span><span class="text-left">7</span></span>`),
    'a linha Total usa a mesma grade')
  // M-35: Ticket médio com a mesma letra do Faturamento (font-medium, à direita, tabular-nums).
  assert.equal((tr('mega').match(/<td class="border border-border px-2 py-1 text-xs text-right tabular-nums font-medium">R\$/g) || []).length, 2,
    'Faturamento e Ticket médio com o mesmo peso')
  // M-37: a Margem — 1 casa, negativa em vermelho, "—" cinza, o balão com a
  // conta do pedido e, embaixo, o que explica o Ads ou o "—".
  const tdMargem = (trecho) => trecho.match(/<td\b[^>]*data-margem\b[^>]*>/)[0]
  assert.match(html, /title="Margem real da Conciliação: \(sobrou de todos os pedidos − Ads\) ÷ custo\. Passe o mouse na conta para ver a conta\. — = fora da Conciliação\.">Margem<\/th>/)
  assert.doesNotMatch(html, /aba Contas, da menor à maior|10\.2% – 55\.3%/, 'a faixa da aba Contas saiu da coluna')
  assert.equal(balao(tr('mega'), 'data-margem'),
    `Sobrou ${brl(73017.55)} − Ads ${brl(2718.63)} ÷ custo ${brl(278695)} · 397 pedidos: 172 pagos, 182 com valor previsto, `
    + '33 cancelados, 10 sem valor de venda\nAds das semanas fechadas até 21/11 (a semana atual entra quando fechar)',
    'o balão do pedido')
  assert.doesNotMatch(tdMargem(tr('mega')), /text-red-600|text-muted-foreground/)
  assert.match(tdMargem(tr('forpaper')), /class="[^"]*text-red-600[^"]*"/, 'negativa em vermelho')
  assert.equal(balao(tr('forpaper'), 'data-margem'),
    `Sobrou ${brl(100)} − Ads ${brl(150)} ÷ custo ${brl(1000)} · 10 pedidos: 6 pagos, 3 com valor previsto, 1 cancelados, 0 sem valor de venda`)
  assert.match(tdMargem(tr('vita')), /class="[^"]*text-muted-foreground[^"]*"/)
  assert.equal(balao(tr('vita'), 'data-margem'), 'Conta fora da Conciliação.')
  assert.equal(balao(tr('Sem cadastro \\(999\\)'), 'data-margem'),
    `Sobrou ${brl(10)} − Ads ${brl(0)} ÷ custo ${brl(20)} · 2 pedidos: 1 pagos, 0 com valor previsto, 0 cancelados, 1 sem valor de venda\n`
    + 'sem dado de Ads no Marketing: conta R$ 0,00', 'Ads sem dado conta zero e o balão diz')
  // Total: soma o que está na tela. Margem = (Σ sobrou − Σ Ads) ÷ Σ custo =
  // (73.127,55 − 2.868,63) ÷ 279.715,00 = 25,1%.
  assert.deepEqual(rodape(html), ['Total', '', '327', brl(341393.63), brl(341393.63 / 327), '7', `${brl(8580.57)}*`,
    `${(8580.57 / 341393.63 * 100).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%`, '25,1%'],
    'Total: soma dos investimentos e % sobre o faturamento total; a Margem com as somas')
  assert.equal(balao(rodapeHtml, 'data-margem-total'),
    `Sobrou ${brl(73127.55)} − Ads ${brl(2868.63)} ÷ custo ${brl(279715)} · 409 pedidos: 179 pagos, 185 com valor previsto, `
    + '34 cancelados, 11 sem valor de venda\nConta sem dado de Ads no Marketing entra com R$ 0,00.\n'
    + 'Alguma conta só tem o Ads das semanas fechadas da Conferência (a semana atual entra quando fechar).')
  assert.equal(balao(rodapeHtml, 'data-investimento-total'),
    `Afiliados ${brl(5711.94)} + Ads ${brl(2868.63)}\nAlguma conta só tem dado depois do início do período.\n`
    + 'Alguma conta só tem as semanas fechadas da Conferência (a semana atual entra quando fechar).')

  // As pendências: uma grade e uma conferência por categoria, uma vez só.
  const doPreco = adm.chamadas.filter((u) => u.startsWith('/api/pricing/'))
  assert.deepEqual(doPreco.sort(), [
    '/api/pricing/conferencia?department=celular', '/api/pricing/conferencia?department=eletro', '/api/pricing/conferencia?department=mala',
    '/api/pricing/grid?department=celular', '/api/pricing/grid?department=eletro', '/api/pricing/grid?department=mala',
  ])
  assert.ok(adm.chamadas.filter((u) => u.startsWith('/api/faturamento')).every((u) => !/department=|team=/.test(u)),
    'o servidor manda todas as lojas do escopo; os filtros são na tela')

  // ---------------------------------------------------------------- filtros e busca valem para o Total
  adm.state.busca = 'MEG'
  html = await adm.html()
  assert.deepEqual(linhas(html).map((l) => l[0]), ['mega'])
  assert.deepEqual(rodape(html).slice(0, 4), ['Total', '', '316', brl(340373.63)])
  assert.equal(rodape(html)[5], '5')
  assert.equal(rodape(html).at(-1), '25,2%', 'a Margem do Total segue a busca')
  assert.match(html, />1 contas<\/span>/)
  adm.state.busca = ''
  adm.state.alternarFiltro('equipe', '102', false)
  html = await adm.html()
  assert.deepEqual(linhas(html).map((l) => l[0]), ['forpaper', 'vita'], 'equipe desmarcada: sai a loja dela e a sem equipe')
  assert.match(html, /Equipes \(2\)/)
  assert.deepEqual(rodape(html).slice(2, 7), ['10', brl(1000), brl(100), '2', `${brl(150)}*`])
  adm.state.marcarTodas('equipe', true)
  adm.state.alternarFiltro('plataforma', 'ml', false)
  html = await adm.html()
  assert.deepEqual(linhas(html).map((l) => l[0]), ['mega', 'Sem cadastro (999)'])
  assert.match(html, /Plataforma \(2\)/)
  adm.state.marcarTodas('plataforma', true)
  adm.state.marcarTodas('conta', false)
  html = await adm.html()
  assert.match(html, /Nenhuma conta corresponde aos filtros\./)
  assert.doesNotMatch(html, /<tfoot/)
  assert.match(html, />0 contas<\/span>/)
  adm.state.marcarTodas('conta', true)
  adm.state.buscaContas = 'for'
  assert.deepEqual(caixas(menu(await adm.html(), 'contas'), 'conta').map(([v]) => v), ['forpaper'])
  // Revisão (24/11): como na Tabela, "Marcar todas · Limpar" do Contas ▾ com
  // busca valem só para as contas encontradas.
  adm.state.marcarTodas('conta', false)
  html = await adm.html()
  assert.deepEqual(linhas(html).map((l) => l[0]), ['mega', 'vita', 'Sem cadastro (999)'], 'Limpar com busca: só a encontrada sai')
  assert.match(html, /Contas \(3\)/)
  adm.state.buscaContas = 'mega'
  adm.state.marcarTodas('conta', false)
  adm.state.buscaContas = 'for'
  adm.state.marcarTodas('conta', true)
  html = await adm.html()
  assert.deepEqual(linhas(html).map((l) => l[0]), ['forpaper', 'vita', 'Sem cadastro (999)'], 'Marcar todas com busca: só a encontrada volta')
  adm.state.buscaContas = ''
  adm.state.marcarTodas('conta', true)
  assert.equal(linhas(await adm.html()).length, 4)

  // ---------------------------------------------------------------- Período e Atualizar
  const antes = adm.chamadas.length
  adm.state.escolherModo('custom', { target: { checked: false } })
  await new Promise((r) => setTimeout(r, 0))
  const q = new URL(adm.chamadas.at(-1), 'https://t.invalid').searchParams
  assert.match(q.get('start'), /^\d{4}-\d{2}-01T00:00:00$/)
  assert.match(q.get('end'), /^\d{4}-\d{2}-\d{2}T23:59:59$/, 'personalizado: até o fim do dia')
  html = await adm.html()
  assert.match(menu(html, 'periodo'), /checked data-campo="periodo-personalizado"/)
  assert.doesNotMatch(menu(html, 'periodo'), /checked data-campo="periodo-mes"/, 'uma caixa por vez')
  const rotuloValido = texto(await adm.html()).match(/Período: [^ ]+ – [^ ]+/)[0]
  adm.state.customStart = '2026-11-20'
  adm.state.customEnd = '2026-11-10'
  const n = adm.chamadas.length
  adm.state.aplicarPersonalizado()
  assert.equal(adm.chamadas.length, n, 'de depois de até: não lê')
  html = await adm.html()
  assert.match(menu(html, 'periodo'), /A data inicial precisa ser antes da final\./)
  // Revisão (24/11): a tabela segue com o período anterior — o botão também,
  // e o aviso fica à vista fora do popover.
  assert.match(texto(html), new RegExp(rotuloValido.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&')), 'o botão mostra o período que está na tabela')
  assert.doesNotMatch(texto(html), /Período: 20\/11\/2026/)
  const barraInv = html.slice(html.indexOf('data-faturamento-barra'), html.indexOf('data-faturamento-tabela'))
  assert.match(barraInv, /data-aviso-periodo[^>]*>[^<]*A data inicial precisa ser antes da final/, 'aviso fora do popover')
  adm.state.customStart = '2026-11-01'
  adm.state.customEnd = '2026-11-10'
  await adm.state.aplicarPersonalizado()
  html = await adm.html()
  assert.match(texto(html), /Período: 01\/11\/2026 – 10\/11\/2026/)
  assert.doesNotMatch(html, /data-aviso-periodo/, 'período válido: sem aviso')
  adm.state.escolherModo('month')
  adm.state.month = '2026-10'
  await adm.state.load()
  let ult = new URL(adm.chamadas.at(-1), 'https://t.invalid').searchParams
  assert.deepEqual([ult.get('start'), ult.get('end')], ['2026-10-01T00:00:00', '2026-11-01T00:00:00'])
  assert.match(texto(await adm.html()), /Período: out\/2026/)
  adm.state.month = '2026-12'
  await adm.state.load()
  ult = new URL(adm.chamadas.at(-1), 'https://t.invalid').searchParams
  assert.deepEqual([ult.get('start'), ult.get('end')], ['2026-12-01T00:00:00', '2027-01-01T00:00:00'], 'dezembro vira o ano')
  assert.ok(adm.chamadas.length > antes)
  const k = adm.chamadas.length
  await adm.state.atualizar()
  assert.deepEqual(adm.chamadas.slice(k).map((u) => u.split('?')[0]).sort(),
    ['/api/faturamento', ...Array(3).fill('/api/pricing/conferencia'), ...Array(3).fill('/api/pricing/grid')],
    'Atualizar relê o faturamento e as pendências')

  // ---------------------------------------------------------------- Margem "—" (M-37)
  // Período que começa antes do início da Conciliação (24/10/2026): "—" em
  // todas, o balão diz o início, e o Total fica "—" contando quem ficou fora.
  const antesDoInicio = (m) => m && { ...m, valor: null, antes_do_inicio: true }
  adm.state.data = { ...RESPOSTA, itens: RESPOSTA.itens.map((it) => ({ ...it, margem: antesDoInicio(it.margem) })) }
  html = await adm.html()
  assert.deepEqual(linhas(html).map((l) => l.at(-1)), ['—', '—', '—', '—'])
  assert.equal(balao(tr('mega'), 'data-margem'), 'A Conciliação começa em 24/10/2026. Escolha um período a partir dessa data.')
  assert.equal(rodape(html).at(-1), '—')
  assert.equal(balao(html.match(/<tfoot\b[^>]*>([\s\S]*?)<\/tfoot>/)[1], 'data-margem-total'),
    '3 conta(s) fora: a Conciliação começa em 24/10/2026.')
  // Só uma fora: o Total segue com as outras e avisa. Conta na Conciliação sem
  // pedido no período: "—" na célula e fica fora do Total, com o Ads dela
  // (exemplo aprovado de nov/2026: Total 24,0% com Ads R$ 35.011,50).
  const itens = structuredClone(RESPOSTA.itens)
  itens[1].margem = antesDoInicio(itens[1].margem)
  itens[3].margem = { valor: null, sobrou: 0, ads: 60, ads_com_dado: true, ads_ate: null, custo: 0, pedidos: 0, pagos: 0,
    previstos: 0, cancelados: 0, sem_valor: 0, inicio: '2026-10-24', antes_do_inicio: false }
  adm.state.data = { ...RESPOSTA, itens }
  html = await adm.html()
  assert.deepEqual(linhas(html).map((l) => l.at(-1)), ['25,2%', '—', '—', '—'])
  assert.equal(balao(tr('Sem cadastro \\(999\\)'), 'data-margem'),
    `Sobrou ${brl(0)} − Ads ${brl(60)} ÷ custo ${brl(0)} · 0 pedidos: 0 pagos, 0 com valor previsto, 0 cancelados, 0 sem valor de venda\n`
    + 'nenhum pedido na Conciliação no período')
  // (73.017,55 − 2.718,63) ÷ 278.695,00 = 25,2% — os R$ 60 da conta sem pedido não entram.
  assert.equal(rodape(html).at(-1), '25,2%')
  assert.equal(balao(html.match(/<tfoot\b[^>]*>([\s\S]*?)<\/tfoot>/)[1], 'data-margem-total'),
    `Sobrou ${brl(73017.55)} − Ads ${brl(2718.63)} ÷ custo ${brl(278695)} · 397 pedidos: 172 pagos, 182 com valor previsto, `
    + '33 cancelados, 10 sem valor de venda\n'
    + 'Alguma conta só tem o Ads das semanas fechadas da Conferência (a semana atual entra quando fechar).\n'
    + '1 conta(s) fora: a Conciliação começa em 24/10/2026.\n'
    + '1 conta(s) sem pedido no período ficam fora (o Ads delas também).')
  // Só a conta sem pedido filtrada: o Total fica "—" e diz por quê.
  adm.state.busca = 'Sem cadastro'
  html = await adm.html()
  assert.equal(rodape(html).at(-1), '—')
  assert.equal(balao(html.match(/<tfoot\b[^>]*>([\s\S]*?)<\/tfoot>/)[1], 'data-margem-total'),
    '1 conta(s) sem pedido no período ficam fora (o Ads delas também).')
  adm.state.busca = ''
  // Negativa no Total também fica vermelha; arredondada a zero, não.
  itens[0].margem = { ...itens[0].margem, sobrou: 0, valor: -2718.63 / 278695 }
  itens[3].margem = null
  adm.state.data = { ...RESPOSTA, itens }
  html = await adm.html()
  assert.equal(linhas(html)[0].at(-1), '-1,0%')
  assert.match(html.match(/<tfoot\b[^>]*>([\s\S]*?)<\/tfoot>/)[1], /class="[^"]*text-red-600[^"]*"[^>]*data-margem-total>-1,0%/)
  itens[0].margem = { ...itens[0].margem, valor: -0.0004 }
  adm.state.data = { ...RESPOSTA, itens }
  html = await adm.html()
  assert.equal(linhas(html)[0].at(-1), '0,0%', 'sem "-0,0%"')
  assert.doesNotMatch(tdMargem(tr('mega')), /text-red-600/)
  await adm.state.load()

  // ---------------------------------------------------------------- erros e estados
  adm.falha.precos = new Error('rede')
  await adm.state.carregarPendencias()
  html = await adm.html()
  assert.equal(linhas(html)[0][7], '—', 'pendências que não carregaram: "—"')
  assert.match(html, /title="As pendências não carregaram — clique em Atualizar\."/)
  adm.falha.precos = null
  adm.falha.faturamento = { data: { detail: { code: 'team_not_allowed' } } }
  await adm.state.load()
  html = await adm.html()
  assert.match(html, /team_not_allowed/)
  assert.doesNotMatch(html, /<tfoot|data-linha-faturamento/, 'o erro limpa a tabela')
  assert.deepEqual(caixas(menu(html, 'equipes'), 'equipe').map(([v]) => v), ['101', '102', '103'], 'os filtros ficam com as opções')
  adm.falha.faturamento = null
  await adm.state.load()
  assert.doesNotMatch(await adm.html(), /team_not_allowed/)
  adm.state.data = null
  adm.state.loading = true
  assert.match(await adm.html(), /carregando…/)
  adm.state.loading = false

  // ---------------------------------------------------------------- quem não é ADM
  const vendedor = await pagina({ role: 'user', permissoes: { faturamento: { view: true }, tabela_precos: { view: true } },
    resposta: { ...semMargem(RESPOSTA), teams: [102] } })
  await vendedor.state.carregarPendencias()
  html = await vendedor.html()
  assert.deepEqual(cabecalho(html), ['Conta', 'Plataforma', 'Equipe', 'Tipo', 'Pedidos', 'Faturamento', 'Ticket médio',
    'Pendências', 'Investimento', '% Investimento'], 'sem a coluna Margem')
  assert.doesNotMatch(html, /data-coluna-so-admin|data-margem|25,2%|Sobrou|Conciliação/)
  assert.equal(linhas(html)[0][7], '5')
  assert.deepEqual(caixas(menu(html, 'equipes'), 'equipe'), [['102', true]], 'só as equipes que o servidor manda')
  assert.equal(rodape(html).length, 8, "o Total sem a célula da Margem")
  // Só Faturamento (sem a Tabela de Preços): não lê a grade; Pendências "—".
  const soFat = await pagina({ role: 'user', permissoes: { faturamento: { view: true } }, resposta: semMargem(RESPOSTA) })
  await soFat.state.carregarPendencias()
  assert.equal(soFat.chamadas.filter((u) => u.startsWith('/api/pricing/')).length, 0)
  html = await soFat.html()
  assert.deepEqual(linhas(html).map((l) => l[7]), ['—', '—', '—', '—'])
  assert.match(html, /title="Sem acesso à Tabela de Preços\."/)
  // Sem nada no período.
  soFat.state.data = { ...semMargem(RESPOSTA), itens: [], total_pedidos: 0, total_faturamento: 0 }
  html = await soFat.html()
  assert.match(html, /Nenhum faturamento no período\./)
  assert.match(html, /Você vê apenas lojas das suas equipes de vendas\./)
  assert.match(html, /colspan="10"/)

  // ---------------------------------------------------------------- a fonte
  const fonte = descriptor.scriptSetup.content
  assert.match(fonte, /import \{ cartoesPorConta, type ConferenciaCartao, type GradeCartao \} from '~\/lib\/cartaoVermelho'/,
    'Pendências pela regra da grade')
  assert.match(fonte, /const porCategoria = await Promise\.all\(ORDEM_CATEGORIAS\.map\(/, 'as três categorias juntas, uma vez')
  assert.doesNotMatch(descriptor.template.content, /v-html/)
  console.log('faturamento-barra: ok')
}

principal().catch((e) => { console.error(e); process.exit(1) })
