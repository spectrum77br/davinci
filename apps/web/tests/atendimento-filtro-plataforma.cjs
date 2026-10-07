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
//  - o contrato com a API: `?plataforma=a,b` (o grupo) dentro do escopo.
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
const web = (rel) => fs.readFileSync(path.resolve(__dirname, '..', rel), 'utf8')

const chipsSfc = sfc('../components/AtendimentoFiltroPlataforma.vue')
const F = exportsDe(chipsSfc.descriptor)
const listaSfc = sfc('../components/AtendimentoLista.vue')
const lojasSfc = sfc('../components/AtendimentoLojas.vue')

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
  assert.match(html, /class="[^"]*lg:flex-wrap[^"]*" role="group" aria-label="plataforma"/, 'uma linha no computador (quebra se não couber); rola de lado na tela estreita')
  assert.match(html, />Plataforma<\/span>/)
  const chips = [...html.matchAll(/data-chip="([^"]+)"[^>]*>([\s\S]*?)<\/button>/g)].map((m) => [m[1], m[2].replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()])
  assert.deepEqual(chips, [
    ['todas', 'Todas 12'],
    ['ml', 'Mercado Livre 3'],
    ['shopee', 'Shopee 5'],
    ['amazon', 'Amazon 1'],
    ['site', 'Sites'],
    ['instagram,facebook', 'Redes 3'],
  ])
  assert.match(html, /data-chip="instagram,facebook"[\s\S]*?data-icone="instagram"[\s\S]*?data-icone="facebook"/, 'Redes mostra os dois ícones')
  assert.match(html, /aria-pressed="true"[^>]*data-chip="todas"/)
  // Escolhido o ML: aceso (cheio); com uma loja dele escolhida, aceso claro.
  html = await renderizar(chipsSfc.descriptor, { resumo: RESUMO, filtros: filtros({ plataforma: 'ml' }) }, { AtendimentoIconePlataforma: ICONE })
  assert.match(html, /class="[^"]*border-primary bg-primary text-primary-foreground[^"]*" aria-pressed="true"[^>]*data-chip="ml"/)
  assert.match(html, /aria-pressed="false"[^>]*data-chip="todas"/)
  html = await renderizar(chipsSfc.descriptor, { resumo: RESUMO, filtros: filtros({ plataforma: 'ml', integration_id: 'i-ml' }) }, { AtendimentoIconePlataforma: ICONE })
  assert.match(html, /class="[^"]*border-primary bg-primary\/10 text-primary[^"]*" aria-pressed="true"[^>]*data-chip="ml"/)
  assert.match(html, /title="Só Mercado Livre: 3 conversa\(s\) falta responder\nUma loja escolhida na barra — clique para ver todas as lojas Mercado Livre"/)
  // Sem /resumo ainda: nada (sem chip pela metade).
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
  const grupos = [...t.matchAll(/<button[\s\S]*?:data-grupo="g\.plataforma"[\s\S]*?@click="escolherPlataforma\(g\.plataforma\)"/g)]
  assert.equal(grupos.length, 2, 'cabeçalho aberto e etiqueta da barra recolhida')
  assert.match(t, /v-else\s+type="button"[\s\S]*?\{\{ plataformaInfo\(g\.plataforma\)\.curto \}\}<\/button>/)
  assert.doesNotMatch(t, /<div v-else class="mx-2 mt-1\.5 border-t pt-1\.5" \/>/, 'o traço mudo saiu')
  // "Redes" escolhido nos chips acende Instagram e Facebook na barra.
  assert.match(s, /return \(filtros\.value\.plataforma \|\| ''\)\.split\(','\)\.includes\(p\) && !filtros\.value\.integration_id/)
}

// ------------------------------------------------ a página: os chips acima da Caixa, e lembra
{
  const pagina = web('pages/atendimento.vue')
  const chips = (pagina.match(/<AtendimentoFiltroPlataforma[^>]*\/>/) || [])[0] || ''
  assert.equal(chips, '<AtendimentoFiltroPlataforma v-if="aba === \'caixa\'" v-model:filtros="filtros" :resumo="resumo" />')
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

principal().then(
  () => console.log('ok: atendimento-filtro-plataforma'),
  (e) => { console.error(e); process.exit(1) },
)
