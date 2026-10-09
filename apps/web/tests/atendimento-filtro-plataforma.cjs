// node tests/atendimento-filtro-plataforma.cjs — escolher a PLATAFORMA na
// Caixa (08/10/2026). O Eduardo: "escolher por plataforma deveria ser mais
// visível — se a pessoa quiser ver só Mercado Livre, só Shopee, coisas do
// tipo". Aqui:
//  - as funções puras dos chips (quais aparecem, o número de "falta
//    responder", qual está aceso, o que o clique faz com os outros filtros);
//  - os chips renderizados (Vue SSR, setup de verdade);
//  - a página: os chips logo acima da Caixa, em qualquer largura; na lista,
//    o <select> de plataforma que só aparecia em tela estreita saiu; o grupo
//    "Redes" conta Instagram + Facebook;
//  - a barra de lojas: o NOME da plataforma filtra todas as lojas dela, também
//    com a barra recolhida (antes era só um traço);
//  - a página lembra a plataforma no navegador;
//  - o contrato com a API: `?plataforma=a,b` (o grupo) dentro do escopo;
//  - O CORTE (09/10/2026, Eduardo: "quando selecionar a plataforma lá em
//    cima, corta as outras ali da listagem: deixa só as lojas da plataforma
//    mesmo naquele navegável na esquerda"): escolher no menu do topo grava
//    `plataforma_topo` e a barra mostra só as lojas dela, com "Todas · Shopee"
//    (o número da plataforma no /resumo, o mesmo do botão); clicar numa LOJA
//    na barra não corta, o NOME do grupo corta (é escolher a plataforma);
//    o botão do topo mostra o corte (com uma loja clicada na barra inteira,
//    "Todas" — botão e barra nunca dizem coisas diferentes); "Todas" no menu
//    volta a barra inteira; "Redes" corta para Instagram + Facebook; com a
//    barra recolhida também; a linha antiga do Direct só com o Instagram no
//    corte; o filtro salvo ANTES do corte (sem `plataforma_topo`) corta pela
//    plataforma que o botão mostrava.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { renderToString } = require('vue/server-renderer')
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')

const transpile = (source) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText

function sfc(rel) {
  const filename = path.resolve(__dirname, rel)
  const fonte = fs.readFileSync(filename, 'utf8')
  const { descriptor, errors } = parse(fonte, { filename })
  assert.deepEqual(errors, [], rel)
  const tpl = compileTemplate({ source: descriptor.template.content, filename, id: path.basename(rel), compilerOptions: { isTS: true } })
  assert.deepEqual(tpl.errors, [], `${rel}: template compila`)
  return { descriptor, fonte, filename }
}
function exportsDe(descriptor) {
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(descriptor.script.content))(require, mod, mod.exports)
  return mod.exports
}
const api = (rel) => fs.readFileSync(path.resolve(__dirname, '../../api/app', rel), 'utf8')
const transpileTs = (source) => ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText
const web = (rel) => fs.readFileSync(path.resolve(__dirname, '..', rel), 'utf8')

const chipsSfc = sfc('../components/AtendimentoFiltroPlataforma.vue')
const F = exportsDe(chipsSfc.descriptor)
const listaSfc = sfc('../components/AtendimentoLista.vue')
const lojasSfc = sfc('../components/AtendimentoLojas.vue')
const P = exportsDe(sfc('../components/AtendimentoPlataforma.vue').descriptor)
// O <script> da barra importa do menu do topo e do vocabulário da tela.
const requerer = (n) => {
  if (n === '~/components/AtendimentoFiltroPlataforma.vue') return F
  if (n === '~/components/AtendimentoPlataforma.vue') return P
  if (n === 'lucide-vue-next') return new Proxy({}, { get: (_, nome) => ({ name: String(nome), render: () => Vue.h('i', { 'data-lucide': String(nome) }) }) })
  return require(n)
}
const LJ = (() => {
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(lojasSfc.descriptor.script.content))(requerer, mod, mod.exports)
  return mod.exports
})()

const RESUMO = {
  plataformas: [
    { plataforma: 'shopee', aguardando: 5, vencendo: 0, vencidas: 0 },
    { plataforma: 'ml', aguardando: 3, vencendo: 0, vencidas: 0 },
    { plataforma: 'tiktok', aguardando: 0, vencendo: 0, vencidas: 0 },
    { plataforma: 'amazon', aguardando: 1, vencendo: 0, vencidas: 0 },
    { plataforma: 'magalu', aguardando: 0, vencendo: 0, vencidas: 0 },
    { plataforma: 'instagram', aguardando: 2, vencendo: 0, vencidas: 0 },
    { plataforma: 'facebook', aguardando: 1, vencendo: 0, vencidas: 0 },
    { plataforma: 'site', aguardando: 0, vencendo: 0, vencidas: 0 },
  ],
  lojas: [
    { integration_id: 'i-sh', plataforma: 'shopee', conta: 'atv', aguardando: 5, vencidas: 0 },
    { integration_id: 'i-ml', plataforma: 'ml', conta: 'poofy', aguardando: 3, vencidas: 0 },
    { integration_id: 'i-am', plataforma: 'amazon', conta: 'kia', aguardando: 1, vencidas: 0 },
    { integration_id: null, plataforma: 'site', externo_ref: 'site:charlots', conta: 'Charlots', aguardando: 0, vencidas: 0 },
    { integration_id: null, plataforma: 'instagram', rede_social_id: 'r1', conta: '@charlots', aguardando: 2, vencidas: 0 },
  ],
  canais: [],
  flags: {},
}
function filtros(o = {}) {
  return { plataforma: '', integration_id: '', canal: '', filtro: 'todas', q: '', externo_ref: '', rede_social_id: '', ...o }
}

// ------------------------------------------------ funções puras
{
  // A ordem do Eduardo, e "Redes" = Instagram + Facebook.
  assert.deepEqual(F.GRUPOS_CAIXA.map((g) => g.nome), ['Mercado Livre', 'Shopee', 'TikTok', 'Amazon', 'Magalu', 'Temu', 'AliExpress', 'Sites', 'Redes'])
  assert.deepEqual(F.GRUPOS_CAIXA.find((g) => g.nome === 'Redes').plataformas, ['instagram', 'facebook'])
  assert.equal(F.GRUPOS_CAIXA.find((g) => g.nome === 'Redes').valor, 'instagram,facebook')

  assert.deepEqual(F.plataformasDoValor(''), [])
  assert.deepEqual(F.plataformasDoValor(' ML , ml ,shopee'), ['ml', 'shopee'])
  assert.deepEqual(F.plataformasDoValor('instagram,facebook'), ['instagram', 'facebook'])

  // Só as que existem para a pessoa (loja dela no /resumo, ou conversa
  // esperando), com o "falta responder" de cada uma; Redes soma as duas.
  const chips = F.chipsDoResumo(RESUMO, '')
  assert.deepEqual(chips.map((c) => [c.nome, c.aguardando]), [
    ['Mercado Livre', 3], ['Shopee', 5], ['Amazon', 1], ['Sites', 0], ['Redes', 3],
  ])
  // TikTok sem loja da pessoa e sem conversa esperando: não aparece — mas,
  // escolhido (lembrado no navegador), aparece para ela ver o que filtra.
  assert.ok(F.chipsDoResumo(RESUMO, 'tiktok').some((c) => c.valor === 'tiktok'))
  assert.deepEqual(F.chipsDoResumo(null, ''), [])
  // Conversa esperando sem loja (canal apagado) também mostra o chip.
  const soConversa = { ...RESUMO, lojas: [], plataformas: [{ plataforma: 'temu', aguardando: 2 }] }
  assert.deepEqual(F.chipsDoResumo(soConversa, '').map((c) => c.valor), ['temu'])
  assert.equal(F.aguardandoDe(RESUMO, ['instagram', 'facebook']), 3)
  assert.equal(F.contadorChip(150), '99+')

  // Aceso: a plataforma inteira OU uma loja dela escolhida na barra.
  const ml = F.GRUPOS_CAIXA[0]
  const redes = F.GRUPOS_CAIXA.find((g) => g.nome === 'Redes')
  assert.equal(F.chipAtivo(null, filtros()), true)
  assert.equal(F.chipAtivo(ml, filtros()), false)
  assert.equal(F.chipAtivo(ml, filtros({ plataforma: 'ml' })), true)
  assert.equal(F.chipAtivo(ml, filtros({ plataforma: 'ml', integration_id: 'i-ml' })), true)
  assert.equal(F.chipAtivo(redes, filtros({ plataforma: 'instagram', rede_social_id: 'r1' })), true)
  assert.equal(F.chipAtivo(redes, filtros({ plataforma: 'instagram,facebook' })), true)
  assert.equal(F.chipAtivo(ml, filtros({ plataforma: 'instagram,facebook' })), false)

  // O clique: só a plataforma muda; aba, menu Filtrar e busca continuam;
  // loja/site/conta da barra saem; a caixa do ML só fica na mesma plataforma.
  const antes = filtros({ filtro: 'aguardando', q: 'rui', integration_id: 'i-sh', plataforma: 'shopee', canal: 'chat' })
  assert.deepEqual(F.filtrosDoChip(antes, ml), filtros({ filtro: 'aguardando', q: 'rui', plataforma: 'ml' }))
  assert.deepEqual(
    F.filtrosDoChip(filtros({ plataforma: 'ml', integration_id: 'i-ml', canal: 'pos_venda' }), ml),
    filtros({ plataforma: 'ml', canal: 'pos_venda' }),
    'com uma loja do ML escolhida, o chip mostra o ML inteiro (a caixa fica)',
  )
  assert.deepEqual(F.filtrosDoChip(filtros({ plataforma: 'ml' }), ml), filtros(), 'de novo no chip aceso: Todas')
  assert.deepEqual(F.filtrosDoChip(filtros({ plataforma: 'ml', filtro: 'aguardando' }), null), filtros({ filtro: 'aguardando' }))
  assert.deepEqual(F.filtrosDoChip(filtros({ plataforma: 'instagram', rede_social_id: 'r1' }), redes), filtros({ plataforma: 'instagram,facebook' }))
}

// ------------------------------------------------ os chips renderizados
async function renderizar(descriptor, props, componentes = {}) {
  const { content } = compileScript(descriptor, { id: 'teste', inlineTemplate: true })
  const mod = {}
  const g = { computed: Vue.computed, ref: Vue.ref, watch: Vue.watch, nextTick: Vue.nextTick }
  const nomes = Object.keys(g)
  new Function('exports', 'require', ...nomes, transpile(content))(mod, require, ...nomes.map((n) => g[n]))
  const app = Vue.createSSRApp(mod.default, props)
  for (const [n, c] of Object.entries(componentes)) app.component(n, c)
  return (await renderToString(app)).replace(/<!--[\s\S]*?-->/g, '')
}
const ICONE = { props: ['plataforma'], render() { return Vue.h('i', { 'data-icone': this.plataforma }) } }

async function principal() {
  let html = await renderizar(chipsSfc.descriptor, { resumo: RESUMO, filtros: filtros() }, { AtendimentoIconePlataforma: ICONE })
  // Um BOTÃO que abre o menu (08/10/2026, igual ao Duoke), não a fileira de chips.
  assert.match(html, />Plataforma<\/span>/)
  assert.match(html, /aria-haspopup="menu" aria-expanded="false" aria-label="plataforma"/)
  assert.match(html, /data-plataforma-botao[^>]*>[\s\S]*?Todas[\s\S]*?>12<[\s\S]*?<\/button>/, 'fechado, o botão diz "Todas" e o total')
  assert.match(html, /<div role="menu" aria-label="escolher a plataforma"[^>]*style="display:none;"/, 'o menu começa fechado')
  assert.doesNotMatch(html, /role="group"|aria-pressed/, 'a fileira de chips saiu')
  // No menu: Todas e cada plataforma, com a logo e o "falta responder", na ordem da Caixa.
  const chips = [...html.matchAll(/data-chip="([^"]+)"[^>]*>([\s\S]*?)<\/button>/g)].map((m) => [m[1], m[2].replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()])
  assert.deepEqual(chips, [
    ['todas', 'Todas 12'],
    ['ml', 'Mercado Livre 3'],
    ['shopee', 'Shopee 5'],
    ['amazon', 'Amazon 1'],
    ['site', 'Sites'],
    ['instagram,facebook', 'Redes 3'],
  ])
  assert.match(html, /data-chip="ml"[^>]*>[\s\S]*?data-icone="ml"/, 'cada opção com a logo')
  assert.match(html, /data-chip="instagram,facebook"[\s\S]*?data-icone="instagram"[\s\S]*?data-icone="facebook"/, 'Redes mostra os dois ícones')
  assert.match(html, /role="menuitemradio"[^>]*aria-checked="true"[^>]*data-chip="todas"/)
  // Escolhido o ML (cortando a barra): o botão mostra a logo, o nome e o número dele; a opção marcada.
  html = await renderizar(chipsSfc.descriptor, { resumo: RESUMO, filtros: filtros({ plataforma: 'ml', plataforma_topo: 'ml' }) }, { AtendimentoIconePlataforma: ICONE })
  assert.match(html, /data-plataforma-botao[^>]*>[\s\S]*?data-icone="ml"[\s\S]*?Mercado Livre[\s\S]*?>3<[\s\S]*?<\/button>/)
  assert.match(html, /aria-checked="true"[^>]*data-chip="ml"/)
  assert.match(html, /aria-checked="false"[^>]*data-chip="todas"/)
  // Com uma loja do ML escolhida dentro do corte: continua marcado, e o título explica.
  html = await renderizar(chipsSfc.descriptor, { resumo: RESUMO, filtros: filtros({ plataforma: 'ml', integration_id: 'i-ml', plataforma_topo: 'ml' }) }, { AtendimentoIconePlataforma: ICONE })
  assert.match(html, /aria-checked="true"[^>]*data-chip="ml"/)
  assert.match(html, /title="Só Mercado Livre: 3 conversa\(s\) falta responder\nUma loja escolhida na barra — clique para ver todas as lojas Mercado Livre"/)
  // A loja do ML clicada na barra INTEIRA (sem corte): o botão continua
  // "Todas" com o total (antes dizia "Mercado Livre" com as outras plataformas
  // na barra — o print do Eduardo); a opção do ML ainda explica o clique.
  html = await renderizar(chipsSfc.descriptor, { resumo: RESUMO, filtros: filtros({ plataforma: 'ml', integration_id: 'i-ml' }) }, { AtendimentoIconePlataforma: ICONE })
  assert.match(html, /data-plataforma-botao[^>]*>[\s\S]*?<span>Todas<\/span>[\s\S]*?>12<[\s\S]*?<\/button>/)
  assert.doesNotMatch((html.match(/data-plataforma-botao[\s\S]*?<\/button>/) || [''])[0], /data-icone=/)
  assert.match(html, /aria-checked="true"[^>]*data-chip="todas"/)
  assert.match(html, /aria-checked="false"[^>]*data-chip="ml"/)
  assert.match(html, /title="Só Mercado Livre: 3 conversa\(s\) falta responder\nUma loja escolhida na barra — clique para ver todas as lojas Mercado Livre"/)
  // A plataforma inteira sem corte (a loja do robô sem integração filtra assim): idem, "Todas".
  html = await renderizar(chipsSfc.descriptor, { resumo: RESUMO, filtros: filtros({ plataforma: 'ml' }) }, { AtendimentoIconePlataforma: ICONE })
  assert.match(html, /aria-checked="true"[^>]*data-chip="todas"/)
  // Escolher no menu a plataforma que já está inteira só fecha (não desfaz):
  // `filtrosDoMenu` devolve o mesmo objeto (09/10/2026: e passa a cortar a barra).
  assert.match(chipsSfc.descriptor.scriptSetup.content, /const novo = filtrosDoMenu\(filtros\.value, chip\)\n\s+if \(novo === filtros\.value\) return/)
  // Sem /resumo ainda: nada (sem botão pela metade).
  html = await renderizar(chipsSfc.descriptor, { resumo: null, filtros: filtros() }, { AtendimentoIconePlataforma: ICONE })
  assert.equal(html.trim(), '')
}

// ------------------------------------------------ a lista
{
  const t = listaSfc.descriptor.template.content
  const s = listaSfc.descriptor.scriptSetup.content
  // O <select> de plataforma (só em tela estreita) saiu: os chips acima da
  // Caixa valem em qualquer largura. O de loja ficou.
  assert.doesNotMatch(t, /aria-label="plataforma"/)
  assert.doesNotMatch(t, /todas plataformas/)
  assert.match(t, /aria-label="loja"/)
  // Os números do menu Filtrar e do "Falta responder" com um GRUPO escolhido.
  assert.match(s, /const escolhidas = \(f\.plataforma \|\| ''\)\.split\(','\)\.filter\(Boolean\)\n\s+const ps = escolhidas\.length \? r\.plataformas\.filter\(\(p\) => escolhidas\.includes\(p\.plataforma\)\) : r\.plataformas/)
  assert.match(s, /\.filter\(\(l\) => !f\.length \|\| f\.includes\(l\.plataforma\)\)/)
  // O valor do chip é o que a lista sabe ler (as vírgulas do grupo).
  assert.deepEqual('instagram,facebook'.split(',').filter(Boolean), F.plataformasDoValor('instagram,facebook'))
}

// ------------------------------------------------ a barra de lojas
{
  const t = lojasSfc.descriptor.template.content
  const s = lojasSfc.descriptor.scriptSetup.content
  // Aberta e recolhida: o nome/etiqueta da plataforma filtra todas as lojas dela.
  // (09/10/2026: o nome do grupo é ESCOLHER A PLATAFORMA — filtra e corta a barra.)
  const grupos = [...t.matchAll(/<button[\s\S]*?:data-grupo="g\.plataforma"[\s\S]*?@click="escolherGrupo\(g\.plataforma\)"/g)]
  assert.equal(grupos.length, 2, 'cabeçalho aberto e etiqueta da barra recolhida')
  assert.match(s, /function escolherGrupo\(p: string\) \{\n\s+filtros\.value = filtrosDoGrupo\(filtros\.value, p\)\n\}/)
  // A loja do robô sem integração e a linha antiga do Direct são LOJAS: não cortam.
  assert.match(s, /if \(!l\.integration_id\) \{\n\s+escolherPlataforma\(l\.plataforma\)/)
  assert.match(t, /@click="escolherPlataforma\('instagram'\)"/)
  assert.match(t, /v-else\s+type="button"[\s\S]*?\{\{ plataformaInfo\(g\.plataforma\)\.curto \}\}<\/button>/)
  assert.doesNotMatch(t, /<div v-else class="mx-2 mt-1\.5 border-t pt-1\.5" \/>/, 'o traço mudo saiu')
  // "Redes" escolhido nos chips acende Instagram e Facebook na barra.
  assert.match(s, /return \(filtros\.value\.plataforma \|\| ''\)\.split\(','\)\.includes\(p\) && !filtros\.value\.integration_id/)
}

// ------------------------------------------------ a página: os chips acima da Caixa, e lembra
{
  const pagina = web('pages/atendimento.vue')
  const chips = (pagina.match(/<AtendimentoFiltroPlataforma[^>]*\/>/) || [])[0] || ''
  // Na Caixa e na Caixa Humano (09/10/2026: com os números dela).
  assert.equal(chips, '<AtendimentoFiltroPlataforma v-if="naCaixa" v-model:filtros="filtros" :resumo="resumo" :numero="emHumano ? \'humano\' : \'aguardando\'" />')
  // No bloco medido acima da Caixa (a altura da Caixa conta com ele), sem
  // classe de "só tela larga/estreita": à vista em qualquer largura.
  const iTopo = pagina.indexOf('<div ref="topoEl"')
  const iChips = pagina.indexOf('<AtendimentoFiltroPlataforma')
  const iCaixa = pagina.indexOf('<!-- Caixa: lojas | fila | conversa + pedido -->')
  assert.ok(iTopo > 0 && iTopo < iChips && iChips < iCaixa)
  assert.ok(pagina.indexOf('<AtendimentoLeituraParada') < iChips, 'depois da faixa das lojas sem ler')
  // Os mesmos filtros da lista (o v-model da página): busca, aba e menu juntos.
  assert.match(pagina, /<AtendimentoLista\s+v-model:filtros="filtros"/)
  assert.match(pagina, /for \(const k of \['plataforma', 'integration_id', 'canal', 'externo_ref', 'rede_social_id'\] as const\)/)
  assert.match(pagina, /localStorage\.setItem\(FILTROS_KEY, JSON\.stringify\(resto\)\)/)
  assert.match(pagina, /if \(f\.plataforma\) p\.set\('plataforma', f\.plataforma\)/)
}

// ------------------------------------------------ o corte da barra (09/10/2026): funções puras
{
  const shopee = F.GRUPOS_CAIXA.find((g) => g.valor === 'shopee')
  const redes = F.GRUPOS_CAIXA.find((g) => g.nome === 'Redes')
  // Escolher no MENU: filtra E corta (plataforma_topo); a loja da barra sai.
  assert.deepEqual(F.filtrosDoMenu(filtros(), shopee), filtros({ plataforma: 'shopee', plataforma_topo: 'shopee' }))
  assert.deepEqual(
    F.filtrosDoMenu(filtros({ plataforma: 'shopee', integration_id: 'i-sh', plataforma_topo: 'shopee', filtro: 'aguardando', q: 'rui' }), shopee),
    filtros({ plataforma: 'shopee', plataforma_topo: 'shopee', filtro: 'aguardando', q: 'rui' }),
    'com uma loja escolhida: a Shopee inteira (aba e busca ficam)',
  )
  // A Shopee escolhida PELA BARRA (o nome do grupo): o menu marca, e escolher
  // de novo não desfaz — passa a cortar.
  const pelaBarra = filtros({ plataforma: 'shopee', canal: 'chat' })
  assert.deepEqual(F.filtrosDoMenu(pelaBarra, shopee), { ...pelaBarra, plataforma_topo: 'shopee' })
  // Já cortando: o mesmo objeto (nada muda, nem recarrega).
  const cortando = filtros({ plataforma: 'shopee', plataforma_topo: 'shopee' })
  assert.equal(F.filtrosDoMenu(cortando, shopee), cortando)
  // "Todas" no menu: tudo de volta, a barra inteira.
  assert.deepEqual(F.filtrosDoMenu(filtros({ plataforma: 'shopee', integration_id: 'i-sh', plataforma_topo: 'shopee' }), null), filtros({ plataforma_topo: '' }))
  // Redes com o Instagram escolhido pela barra: o grupo inteiro.
  assert.deepEqual(F.filtrosDoMenu(filtros({ plataforma: 'instagram' }), redes), filtros({ plataforma: 'instagram,facebook', plataforma_topo: 'instagram,facebook' }))

  // O corte: pela plataforma escolhida (topo ou nome do grupo), e só enquanto o filtro está dentro dele.
  assert.equal(LJ.corteDaBarra, F.corteDaBarra, 'a régua mora no menu do topo')
  assert.deepEqual(LJ.corteDaBarra(filtros()), [])
  assert.deepEqual(LJ.corteDaBarra(filtros({ plataforma: 'shopee' })), [], 'loja do robô / Todas: não corta')
  assert.deepEqual(LJ.corteDaBarra(filtros({ plataforma: 'shopee', plataforma_topo: 'shopee' })), ['shopee'])
  assert.deepEqual(LJ.corteDaBarra(filtros({ plataforma: 'shopee', integration_id: 'i-sh', plataforma_topo: 'shopee' })), ['shopee'], 'uma loja dela: continua')
  assert.deepEqual(LJ.corteDaBarra(filtros({ plataforma: 'tiktok', plataforma_topo: 'shopee' })), [], 'o filtro saiu da plataforma: a barra volta inteira')
  assert.deepEqual(LJ.corteDaBarra(filtros({ plataforma: '', plataforma_topo: 'shopee' })), [])
  assert.deepEqual(LJ.corteDaBarra(filtros({ plataforma: 'instagram', rede_social_id: 'r1', plataforma_topo: 'instagram,facebook' })), ['instagram', 'facebook'])
  const gs = [{ plataforma: 'shopee' }, { plataforma: 'tiktok' }, { plataforma: 'instagram' }, { plataforma: 'facebook' }]
  assert.deepEqual(LJ.gruposVisiveis(gs, []), gs)
  assert.deepEqual(LJ.gruposVisiveis(gs, ['shopee']), [{ plataforma: 'shopee' }])
  assert.deepEqual(LJ.gruposVisiveis(gs, ['instagram', 'facebook']).map((g) => g.plataforma), ['instagram', 'facebook'])
  // O nome do grupo NA BARRA: a plataforma inteira E o corte (Instagram ou
  // Facebook cortam para Redes, o grupo do menu); a caixa do ML fica.
  assert.deepEqual(
    LJ.filtrosDoGrupo(filtros({ plataforma: 'shopee', integration_id: 'i-sh', filtro: 'aguardando', q: 'rui' }), 'shopee'),
    filtros({ plataforma: 'shopee', plataforma_topo: 'shopee', filtro: 'aguardando', q: 'rui' }),
  )
  assert.deepEqual(LJ.filtrosDoGrupo(filtros({ plataforma: 'ml', canal: 'pos_venda' }), 'ml'), filtros({ plataforma: 'ml', canal: 'pos_venda', plataforma_topo: 'ml' }))
  assert.deepEqual(LJ.filtrosDoGrupo(filtros({ plataforma: 'shopee', canal: 'chat' }), 'ml'), filtros({ plataforma: 'ml', plataforma_topo: 'ml' }))
  const ig = LJ.filtrosDoGrupo(filtros(), 'instagram')
  assert.deepEqual([ig.plataforma, ig.plataforma_topo], ['instagram', 'instagram,facebook'])
  assert.deepEqual(LJ.corteDaBarra(ig), ['instagram', 'facebook'])
  assert.equal(F.chipDoTopo(redes, ig), true, 'o botão mostra "Redes"')
  // Plataforma fora do menu: filtra, não corta.
  assert.equal(LJ.filtrosDoGrupo(filtros(), 'whatsapp').plataforma_topo, '')
  // O grupo do menu de um valor ('' = nenhum).
  assert.equal(F.topoDoValor(''), '')
  assert.equal(F.topoDoValor('shopee'), 'shopee')
  assert.equal(F.topoDoValor(' ML '), 'ml')
  assert.equal(F.topoDoValor('instagram'), 'instagram,facebook')
  assert.equal(F.topoDoValor('facebook,instagram'), 'instagram,facebook')
  assert.equal(F.topoDoValor('shopee,ml'), '')
  assert.equal(F.topoDoValor('xyz'), '')
  // O botão e o menu: o CORTE (null = "Todas", a barra inteira).
  assert.equal(F.chipDoTopo(null, filtros()), true)
  assert.equal(F.chipDoTopo(null, filtros({ plataforma: 'shopee', integration_id: 'i-sh' })), true, 'loja clicada na barra inteira: "Todas"')
  assert.equal(F.chipDoTopo(shopee, filtros({ plataforma: 'shopee', integration_id: 'i-sh' })), false)
  assert.equal(F.chipDoTopo(shopee, filtros({ plataforma: 'shopee', integration_id: 'i-sh', plataforma_topo: 'shopee' })), true)
  assert.equal(F.chipDoTopo(null, filtros({ plataforma: 'shopee', plataforma_topo: 'shopee' })), false)
  assert.equal(F.chipDoTopo(shopee, filtros({ plataforma: 'tiktok', plataforma_topo: 'shopee' })), false, 'o filtro saiu: nem botão nem barra')
  assert.equal(F.chipDoTopo(null, filtros({ plataforma: 'tiktok', plataforma_topo: 'shopee' })), true)
  assert.equal(F.chipDoTopo(redes, filtros({ plataforma: 'instagram,facebook', plataforma_topo: 'instagram,facebook' })), true)
  // Botão e barra SEMPRE juntos: o botão mostra uma plataforma ⇔ a barra está cortada nela.
  for (const f of [
    filtros(), filtros({ plataforma: 'shopee' }), filtros({ plataforma: 'shopee', integration_id: 'i-sh' }),
    filtros({ plataforma: 'shopee', plataforma_topo: 'shopee' }), filtros({ plataforma: 'tiktok', plataforma_topo: 'shopee' }),
    filtros({ plataforma: 'instagram', rede_social_id: 'r1', plataforma_topo: 'instagram,facebook' }), ig,
  ]) {
    const chip = F.GRUPOS_CAIXA.find((g) => F.chipDoTopo(g, f)) || null
    assert.deepEqual(chip ? chip.plataformas : [], LJ.corteDaBarra(f), JSON.stringify(f))
    assert.equal(F.chipDoTopo(null, f), !chip)
  }
  // O número de "Todas · Shopee": o da plataforma no /resumo (o do botão).
  assert.equal(LJ.numeroDoCorte(RESUMO, ['shopee']), 5)
  assert.equal(LJ.numeroDoCorte(RESUMO, ['instagram', 'facebook']), 3)
  assert.equal(LJ.numeroDoCorte({ plataformas: [{ plataforma: 'shopee', aguardando: 5, humano: 2 }] }, ['shopee'], 'humano'), 2)
  assert.equal(LJ.numeroDoCorte(null, ['shopee']), 0)
  assert.equal(LJ.nomeDoCorte(['shopee']), 'Shopee')
  assert.equal(LJ.nomeDoCorte(['ml']), 'Mercado Livre')
  assert.equal(LJ.nomeDoCorte(['instagram', 'facebook']), 'Redes')
  // "Todas · Shopee": acesa com a Shopee inteira; o clique tira a loja e
  // guarda a caixa (Pergunta/Pós-venda do ML) e o tipo do chamado.
  assert.equal(LJ.ativaTodasDoCorte(filtros({ plataforma: 'shopee', plataforma_topo: 'shopee' }), ['shopee']), true)
  assert.equal(LJ.ativaTodasDoCorte(filtros({ plataforma: 'shopee', integration_id: 'i-sh', plataforma_topo: 'shopee' }), ['shopee']), false)
  assert.equal(LJ.ativaTodasDoCorte(filtros({ plataforma: 'instagram', plataforma_topo: 'instagram,facebook' }), ['instagram', 'facebook']), false)
  assert.deepEqual(
    LJ.filtrosTodasDoCorte(filtros({ plataforma: 'ml', integration_id: 'i-ml', canal: 'pos_venda', plataforma_topo: 'ml' }), ['ml']),
    filtros({ plataforma: 'ml', canal: 'pos_venda', plataforma_topo: 'ml', tipo_chamado: undefined }),
  )
  assert.deepEqual(
    LJ.filtrosTodasDoCorte(filtros({ plataforma: 'instagram', rede_social_id: 'r1', plataforma_topo: 'instagram,facebook', tipo_chamado: 'sac' }), ['instagram', 'facebook']),
    filtros({ plataforma: 'instagram,facebook', plataforma_topo: 'instagram,facebook', tipo_chamado: '' }),
  )
}

// ------------------------------------------------ o corte da barra: a barra renderizada (setup de verdade)
async function renderizarBarra(props) {
  const { content } = compileScript(lojasSfc.descriptor, { id: 'barra', inlineTemplate: true })
  const mod = {}
  const g = { computed: Vue.computed, ref: Vue.ref, watch: Vue.watch, nextTick: Vue.nextTick }
  const nomes = Object.keys(g)
  new Function('exports', 'require', ...nomes, transpile(content))(mod, requerer, ...nomes.map((n) => g[n]))
  const app = Vue.createSSRApp(mod.default, props)
  app.component('AtendimentoIconePlataforma', ICONE)
  return (await renderToString(app)).replace(/<!--[\s\S]*?-->/g, '')
}
const RESUMO_BARRA = {
  ...RESUMO,
  lojas: [
    ...RESUMO.lojas,
    { integration_id: 'i-sh2', plataforma: 'shopee', conta: 'barbosa', aguardando: 2, vencidas: 0 },
    { integration_id: 'i-tt', plataforma: 'tiktok', conta: 'inova', aguardando: 4, vencidas: 0 },
    { integration_id: null, plataforma: 'facebook', rede_social_id: 'r2', conta: 'Charlots FB', aguardando: 1, vencidas: 0 },
  ],
}
// As linhas de loja (o title começa por "Plataforma · conta"; a de "Todas", não).
const lojasNaBarra = (html) => [...html.matchAll(/<button type="button" class="relative mx-1[^"]*"[^>]*title="([^"\n]*)/g)].map((m) => m[1]).filter((t) => !t.startsWith('Todas'))
async function barraRenderizada() {
  // Sem corte: todas as plataformas, com os cabeçalhos.
  let html = await renderizarBarra({ resumo: RESUMO_BARRA, filtros: filtros() })
  assert.match(html, /data-lojas-todas[^>]*>[\s\S]*?Todas<\/span>/)
  assert.deepEqual([...html.matchAll(/data-grupo="([^"]+)"/g)].map((m) => m[1]), ['shopee', 'tiktok', 'ml', 'amazon', 'site', 'instagram', 'facebook'])
  // Uma loja Shopee clicada NA BARRA (sem o menu): a barra continua inteira.
  html = await renderizarBarra({ resumo: RESUMO_BARRA, filtros: filtros({ plataforma: 'shopee', integration_id: 'i-sh' }) })
  assert.ok(html.includes('data-grupo="tiktok"'), 'clicar numa loja não corta')
  // O NOME do grupo Shopee clicado na barra: corta como o menu do topo.
  html = await renderizarBarra({ resumo: RESUMO_BARRA, filtros: LJ.filtrosDoGrupo(filtros(), 'shopee') })
  assert.doesNotMatch(html, /data-grupo=|>inova</)
  assert.match(html, /Todas · Shopee<\/span><span[^>]*>5<\/span>/)
  // Amazon cortada com a conversa da conta não identificada (sem linha na
  // barra): "Todas · Amazon" com o 1 do /resumo (antes ficava sem número).
  const semConta = { ...RESUMO_BARRA, lojas: RESUMO_BARRA.lojas.map((l) => (l.plataforma === 'amazon' ? { ...l, aguardando: 0 } : l)) }
  html = await renderizarBarra({ resumo: semConta, filtros: filtros({ plataforma: 'amazon', plataforma_topo: 'amazon' }) })
  assert.match(html, /Todas · Amazon<\/span><span[^>]*>1<\/span>/)
  // Shopee no MENU do topo: só as lojas Shopee, "Todas · Shopee" com o número
  // DELA no /resumo (5 — o mesmo do botão do topo e da lista; a soma das
  // linhas, 5 + 2, não conta a conversa sem loja e conta a da loja apagada).
  html = await renderizarBarra({ resumo: RESUMO_BARRA, filtros: filtros({ plataforma: 'shopee', plataforma_topo: 'shopee' }) })
  assert.match(html, /aria-pressed="true" title="Todas as lojas Shopee — 5 não lida\(s\)\nPara ver as outras plataformas: &quot;Todas&quot; no menu Plataforma, acima" data-lojas-todas>/)
  assert.match(html, /data-lojas-todas>[\s\S]*?data-icone="shopee"[\s\S]*?Todas · Shopee<\/span><span[^>]*>5<\/span>/)
  assert.doesNotMatch(html, /data-grupo=/, 'uma plataforma só: sem o cabeçalho que repete "Todas · Shopee"')
  assert.deepEqual(lojasNaBarra(html).map((t) => t.replace(/ · .*/, '')), ['Shopee', 'Shopee'])
  assert.match(html, />atv</)
  assert.match(html, />barbosa</)
  assert.doesNotMatch(html, />inova<|>poofy<|>kia<|>Charlots</, 'as lojas das outras plataformas saem')
  // Uma loja dela escolhida: o corte fica; "Todas · Shopee" apaga.
  html = await renderizarBarra({ resumo: RESUMO_BARRA, filtros: filtros({ plataforma: 'shopee', integration_id: 'i-sh2', plataforma_topo: 'shopee' }) })
  assert.match(html, /aria-pressed="false"[^>]*data-lojas-todas/)
  assert.doesNotMatch(html, />inova</)
  // Redes: Instagram + Facebook, com os cabeçalhos.
  html = await renderizarBarra({ resumo: RESUMO_BARRA, filtros: filtros({ plataforma: 'instagram,facebook', plataforma_topo: 'instagram,facebook' }) })
  assert.match(html, /Todas · Redes<\/span><span[^>]*>3<\/span>/)
  assert.deepEqual([...html.matchAll(/data-grupo="([^"]+)"/g)].map((m) => m[1]), ['instagram', 'facebook'])
  // Recolhida: o mesmo corte, só o ícone e o número.
  html = await renderizarBarra({ resumo: RESUMO_BARRA, recolhida: true, filtros: filtros({ plataforma: 'shopee', plataforma_topo: 'shopee' }) })
  assert.doesNotMatch(html, /data-grupo=/)
  assert.doesNotMatch(html, /Todas · Shopee/, 'recolhida: sem o texto')
  assert.match(html, /data-lojas-todas>[\s\S]*?data-icone="shopee"/)
  assert.equal(lojasNaBarra(html).length, 2)
  // A linha antiga do Direct (API sem as contas do Instagram): só com o Instagram no corte.
  const antiga = { ...RESUMO, lojas: RESUMO.lojas.filter((l) => l.plataforma !== 'instagram') }
  html = await renderizarBarra({ resumo: antiga, filtros: filtros() })
  assert.match(html, />Direct</)
  html = await renderizarBarra({ resumo: antiga, filtros: filtros({ plataforma: 'shopee', plataforma_topo: 'shopee' }) })
  assert.doesNotMatch(html, />Direct</)
  html = await renderizarBarra({ resumo: antiga, filtros: filtros({ plataforma: 'instagram,facebook', plataforma_topo: 'instagram,facebook' }) })
  assert.match(html, />Direct</)
  // O clique em "Todas" com a barra cortada é a plataforma inteira (não sai do corte).
  const setup = lojasSfc.descriptor.scriptSetup.content
  assert.match(setup, /function escolherTodas\(\) \{\n[^\n]*\n  if \(corte\.value\.length\) \{\n    filtros\.value = filtrosTodasDoCorte\(filtros\.value, corte\.value\)/)
}

// ------------------------------------------------ o corte: a página lembra a plataforma do topo
{
  const pagina = web('pages/atendimento.vue')
  assert.match(pagina, /if \(typeof salvo\.plataforma_topo === 'string'\) f\.plataforma_topo = salvo\.plataforma_topo/)
  assert.match(pagina, /import \{ topoDoValor \} from '~\/components\/AtendimentoFiltroPlataforma\.vue'/)
  // O restaurar DE VERDADE (o trecho do onMounted), com o localStorage falso.
  const ini = pagina.indexOf("const raw = localStorage.getItem(FILTROS_KEY)")
  const fim = pagina.indexOf('} catch {', ini)
  assert.ok(ini > 0 && fim > ini)
  const restaurar = new Function('localStorage', 'FILTROS_KEY', 'filtros', 'topoDoValor', transpileTs(pagina.slice(ini, fim)))
  const abrir = (salvo) => {
    const ref = { value: filtros({ plataforma_topo: '' }) }
    restaurar({ getItem: () => JSON.stringify(salvo) }, 'k', ref, F.topoDoValor)
    return ref.value
  }
  // O filtro salvo ANTES do corte (a produção 4c628db9: sem a chave) — o
  // botão mostrava "Shopee": agora a barra corta na Shopee também.
  let f = abrir({ plataforma: 'shopee', integration_id: '', canal: '', filtro: 'aguardando', externo_ref: '', rede_social_id: '' })
  assert.equal(f.plataforma_topo, 'shopee')
  assert.equal(f.filtro, 'todas', 'a aba não volta')
  assert.deepEqual(LJ.corteDaBarra(f), ['shopee'])
  assert.equal(F.chipDoTopo(F.GRUPOS_CAIXA.find((g) => g.valor === 'shopee'), f), true)
  // Com uma loja (o botão também mostrava a plataforma): corta, e a loja fica.
  f = abrir({ plataforma: 'shopee', integration_id: 'i-sh' })
  assert.deepEqual([f.plataforma_topo, f.integration_id], ['shopee', 'i-sh'])
  // O Instagram escolhido pela barra: o botão mostrava "Redes".
  assert.equal(abrir({ plataforma: 'instagram', rede_social_id: 'r1' }).plataforma_topo, 'instagram,facebook')
  assert.equal(abrir({ plataforma: '' }).plataforma_topo, '')
  // O formato novo vale como está: a loja clicada na barra inteira continua sem corte.
  f = abrir({ plataforma: 'shopee', integration_id: 'i-sh', plataforma_topo: '' })
  assert.deepEqual([f.plataforma_topo, LJ.corteDaBarra(f)], ['', []])
  assert.equal(abrir({ plataforma: 'ml', plataforma_topo: 'ml' }).plataforma_topo, 'ml')
  assert.match(pagina, /plataforma_topo: '' \}\)/)
  // Não vai para a API (a lista filtra por `plataforma`).
  assert.doesNotMatch(pagina, /p\.set\('plataforma_topo'/)
  assert.match(pagina, /<AtendimentoLojas v-model:filtros="filtros" v-model:recolhida="lojasRecolhidas" :resumo="resumo" :numero="emHumano \? 'humano' : 'aguardando'" \/>/)
}

// ------------------------------------------------ o contrato com a API
{
  const rota = api('routers/atendimento.py')
  assert.match(rota, /def _plataformas_do_filtro\(texto: str \| None\) -> tuple\[str, \.\.\.\]:/)
  assert.match(rota, /plataformas = _plataformas_do_filtro\(plataforma\)/)
  assert.match(rota, /if any\(p not in PLATAFORMAS_LISTA for p in plataformas\):/)
  assert.match(rota, /consulta = consulta\.where\(AtendimentoConversa\.plataforma\.in_\(plataforma\)\)/)
  // O escopo da equipe continua antes do filtro.
  const listar = (rota.match(/async def _listar_marketplace\([\s\S]*?\n    if len\(plataforma\) == 1:/) || [])[0] || ''
  assert.match(listar, /cond = _clausula_escopo\(scope, AtendimentoConversa\.integration_id\)/)
  // Toda plataforma dos chips é uma que a API aceita.
  const constantes = api('services/atendimento/constantes.py')
  for (const g of F.GRUPOS_CAIXA) {
    for (const p of g.plataformas) assert.match(constantes, new RegExp(`"${p}"`), `${p} existe na API`)
  }
}

principal().then(barraRenderizada).then(
  () => console.log('ok: atendimento-filtro-plataforma'),
  (e) => { console.error(e); process.exit(1) },
)
