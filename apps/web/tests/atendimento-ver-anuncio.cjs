// node tests/atendimento-ver-anuncio.cjs — o botão "Ver anúncio" do cabeçalho
// da conversa (03/11/2026, o Eduardo: "um botão pra abrir ele não só no
// AdsPower mas no navegador normal, só o anúncio mesmo, como uma pessoa vê
// ele"). Aqui:
//  - o contrato com a API (AnunciosOut/AnuncioOut do painel ↔ os tipos da tela);
//  - as funções puras (o estado do botão, o rótulo, a dica, o cinto do https);
//  - o componente renderizado (Vue SSR): um anúncio = link direto com
//    target=_blank e rel=noopener noreferrer; vários = menu; nenhum = desligado
//    com o porquê; carregando = desligado com o spinner;
//  - a conversa: o botão antes do AdsPower, com o `anuncios` do painel.
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
const api = (rel) => fs.readFileSync(path.resolve(__dirname, '../../api/app', rel), 'utf8')

const botaoSfc = sfc('../components/AtendimentoVerAnuncio.vue')
const mod = { exports: {} }
new Function('require', 'module', 'exports', transpile(botaoSfc.descriptor.script.content))(require, mod, mod.exports)
const V = mod.exports

const SHOPEE = { item_id: '22693481538', titulo: 'Uranyx A17 Pro Max', variacao: 'Preto', link: 'https://shopee.com.br/product/1143525481/22693481538', origem: 'pedido' }
const SHOPEE2 = { item_id: '58252691738', titulo: 'Uranyx A17 Premium', variacao: null, link: 'https://shopee.com.br/product/1143525481/58252691738', origem: 'pedido' }

// ------------------------------------------------ o contrato com a API
{
  const schemas = api('schemas/atendimento_painel.py')
  const campos = (classe) => {
    const bloco = (schemas.match(new RegExp(`^class ${classe}\\(BaseModel\\):([\\s\\S]*?)^class `, 'm')) || [])[1] || ''
    return [...bloco.matchAll(/^ {4}([a-z_]+): /gm)].map((m) => m[1])
  }
  const tipo = (nome) => {
    const corpo = (botaoSfc.descriptor.script.content.match(new RegExp(`export type ${nome} = \\{([\\s\\S]*?)\\n\\}`)) || [])[1] || ''
    return [...corpo.matchAll(/^ {2}([a-z_]+)\??: /gm)].map((m) => m[1])
  }
  assert.deepEqual(campos('AnuncioOut'), ['item_id', 'titulo', 'variacao', 'link', 'origem'])
  assert.deepEqual(tipo('AnuncioPublico'), campos('AnuncioOut'), 'AnuncioPublico = AnuncioOut')
  assert.deepEqual(tipo('AnunciosPainel'), campos('AnunciosOut'), 'AnunciosPainel = AnunciosOut')
  assert.match(schemas, /^ {4}anuncios: AnunciosOut = Field\(default_factory=AnunciosOut\)$/m, 'o painel traz o bloco')
  assert.match(api('services/atendimento/painel.py'), /"anuncios": anuncios,/)
  const pedido = sfc('../components/AtendimentoPedido.vue').descriptor.script.content
  assert.match(pedido, /^ {2}anuncios\?: AnunciosPainel \| null$/m, 'o tipo do painel na tela')
}

// ------------------------------------------------ as funções puras
{
  assert.equal(V.linkPublico('https://shopee.com.br/product/1/2'), 'https://shopee.com.br/product/1/2')
  assert.equal(V.linkPublico('http://shopee.com.br/product/1/2'), null)
  assert.equal(V.linkPublico('javascript:alert(1)'), null)
  assert.equal(V.linkPublico('https://a@shopee.com.br/x'), null)
  assert.equal(V.linkPublico(null), null)

  assert.equal(V.rotuloAnuncio(SHOPEE), 'Uranyx A17 Pro Max (Preto)')
  assert.equal(V.rotuloAnuncio({ item_id: '123', titulo: null, variacao: null }), 'Anúncio 123')
  assert.equal(V.dicaAnuncio(SHOPEE), 'ver o anúncio do pedido como o comprador vê (aba nova): Uranyx A17 Pro Max (Preto)')
  assert.equal(V.dicaAnuncio({ ...SHOPEE2, origem: 'chat' }), 'ver o anúncio que o comprador mandou como o comprador vê (aba nova): Uranyx A17 Premium')

  assert.deepEqual(V.estadoVerAnuncio(null, true), { modo: 'carregando' })
  assert.deepEqual(V.estadoVerAnuncio(null, false), { modo: 'desligado', motivo: 'Não consegui ler o anúncio agora.' })
  assert.deepEqual(V.estadoVerAnuncio({ itens: [SHOPEE], motivo: null, codigo: null }), { modo: 'um', anuncio: SHOPEE })
  assert.deepEqual(V.estadoVerAnuncio({ itens: [SHOPEE, SHOPEE2], motivo: null, codigo: null }), { modo: 'menu', anuncios: [SHOPEE, SHOPEE2] })
  // Link torto não vira botão; sobrando um, é o direto.
  assert.deepEqual(V.estadoVerAnuncio({ itens: [{ ...SHOPEE2, link: 'javascript:x' }, SHOPEE], motivo: null, codigo: null }), { modo: 'um', anuncio: SHOPEE })
  assert.deepEqual(
    V.estadoVerAnuncio({ itens: [], motivo: 'Não achei o anúncio desta conversa.', codigo: 'sem_anuncio' }),
    { modo: 'desligado', motivo: 'Não achei o anúncio desta conversa.' },
  )
  // O painel da API antiga (sem o bloco) não quebra: desligado.
  assert.equal(V.estadoVerAnuncio({ itens: undefined }).modo, 'desligado')
}

// ------------------------------------------------ o componente
function requerer(nome) {
  if (nome === 'lucide-vue-next') return new Proxy({}, { get: (_, k) => ({ render: () => Vue.h('i', { 'data-icone': String(k) }) }) })
  if (nome === 'reka-ui') {
    const passa = (tag) => ({ inheritAttrs: false, setup: (_, { slots }) => () => (tag ? Vue.h(tag, slots.default?.()) : slots.default?.()) })
    return { PopoverRoot: passa('div'), PopoverTrigger: passa(null), PopoverPortal: passa(null), PopoverContent: passa('section') }
  }
  return require(nome)
}
const Button = {
  props: { as: { type: String, default: 'button' }, size: String, variant: String },
  setup: (p, { slots, attrs }) => () => Vue.h(p.as, attrs, slots.default?.()),
}
async function renderizar(props) {
  const { content } = compileScript(botaoSfc.descriptor, { id: 'teste', inlineTemplate: true })
  const m = {}
  const nomes = ['computed', 'ref']
  new Function('exports', 'require', ...nomes, transpile(content))(m, requerer, Vue.computed, Vue.ref)
  const app = Vue.createSSRApp(m.default, props)
  app.component('Button', Button)
  return (await renderToString(app)).replace(/<!--[\s\S]*?-->/g, '')
}

async function principal() {
  // Um anúncio: o link direto, aba nova, sem opener nem referrer, dica com o título.
  let html = await renderizar({ anuncios: { itens: [SHOPEE], motivo: null, codigo: null } })
  assert.match(html, /^<a [^>]*href="https:\/\/shopee\.com\.br\/product\/1143525481\/22693481538"/)
  assert.match(html, /target="_blank"/)
  assert.match(html, /rel="noopener noreferrer"/)
  assert.match(html, /title="ver o anúncio do pedido como o comprador vê \(aba nova\): Uranyx A17 Pro Max \(Preto\)"/)
  assert.match(html, /<span class="ml-1">Ver anúncio<\/span>/)
  assert.doesNotMatch(html, /disabled/)

  // Vários: o menu, cada linha um link para a aba nova com o título.
  html = await renderizar({ anuncios: { itens: [SHOPEE, SHOPEE2], motivo: null, codigo: null } })
  assert.match(html, /<button [^>]*title="o pedido tem 2 anúncios — escolha qual abrir"/)
  const linhas = [...html.matchAll(/<a [^>]*data-ver-anuncio-item[^>]*>/g)].map((m) => m[0])
  assert.equal(linhas.length, 2)
  for (const l of linhas) {
    assert.match(l, /target="_blank"/)
    assert.match(l, /rel="noopener noreferrer"/)
  }
  assert.match(linhas[1], /href="https:\/\/shopee\.com\.br\/product\/1143525481\/58252691738"/)
  assert.match(html, />Uranyx A17 Pro Max \(Preto\)</)
  assert.match(html, />Uranyx A17 Premium</)

  // Nenhum: desligado, com o porquê na dica.
  html = await renderizar({ anuncios: { itens: [], motivo: 'Não achei o anúncio desta conversa.', codigo: 'sem_anuncio' } })
  assert.match(html, /^<button [^>]*disabled[^>]*title="Não achei o anúncio desta conversa\."/)
  assert.doesNotMatch(html, /href=/)

  // Carregando o painel: desligado com o spinner.
  html = await renderizar({ anuncios: null, carregando: true })
  assert.match(html, /disabled/)
  assert.match(html, /title="lendo o anúncio…"/)
  assert.match(html, /data-icone="Loader2"/)
}

// ------------------------------------------------ a conversa
{
  const tpl = sfc('../components/AtendimentoConversa.vue').descriptor.template.content
  const ver = tpl.indexOf('<AtendimentoVerAnuncio')
  const ads = tpl.indexOf('<AtendimentoAdsPower')
  assert.ok(ver > 0 && ads > ver, 'o "Ver anúncio" logo antes do AdsPower')
  const tag = tpl.slice(ver, tpl.indexOf('/>', ver))
  assert.match(tag, /v-if="!conversa\.somente_leitura && !semPainelDePedido"/, 'as mesmas condições do AdsPower')
  assert.match(tag, /:anuncios="painelDados\?\.anuncios \?\? null"/)
  assert.match(tag, /:carregando="painelCarregando"/)
  // Fora do `canEdit`: quem só lê a caixa também abre o anúncio.
  const antes = tpl.slice(0, ver)
  assert.ok(antes.lastIndexOf('</template>') > antes.lastIndexOf('<template v-if="!conversa.somente_leitura && canEdit">'))
}

principal().then(() => console.log('atendimento-ver-anuncio: ok'), (e) => { console.error(e); process.exit(1) })
