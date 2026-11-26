// Run from apps/web: node tests/erp-produtos-midias.cjs
// M-72 (26/11/2026): Fotos e Embalagem do cadastro do produto no Produtos 2 abrem, na moldura
// (ErpTelas.vue), a MESMA janela da Tabela de Preços › Produtos (MidiasProdutoDrawer).
// O que este teste trava, rodando o <script setup> de verdade com o navegador de mentira:
//  · só abre com mensagem do próprio site E do próprio quadro do Produtos 2; id de linha e aba
//    conferidos (qualquer outra coisa é ignorada, sem chamar a API);
//  · a linha é lida da API do DaVinci (/api/pricing/mega/products/{id}) e vai para a janela;
//  · o que a janela muda volta para o quadro (postMessage só para o próprio site), e fechar ou sair
//    do Produtos 2 desliga o "Enviando…" do quadro.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')

const filename = path.join(__dirname, '../components/ErpTelas.vue')
const source = fs.readFileSync(filename, 'utf8')
const { descriptor, errors } = parse(source, { filename })
assert.deepEqual(errors, [])
const script = compileScript(descriptor, { id: 'erp-midias' })
const template = compileTemplate({ source: descriptor.template.content, filename, id: 'erp-midias', compilerOptions: { bindingMetadata: script.bindings } })
assert.deepEqual(template.errors, [])
// A janela é a mesma da Tabela de Preços, com os mesmos dados que ela recebe lá.
assert.match(descriptor.template.content, /<MidiasProdutoDrawer\s+v-if="midias"\s+:produto="midias\.linha"\s+:aba-inicial="midias\.aba"\s+:pode-editar="midias\.linha\.pode_editar"\s+:irmaos="midias\.linha\.irmaos"\s+:aplicar="aplicarMidias"\s+@enviando="aoEnviarMidias"\s+@fechar="fecharMidias"/)

const transpile = code => ts.transpileModule(code, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText
const body = descriptor.scriptSetup.content.replace(/^import .*$/gm, '')
const ORIGEM = 'https://app.exemplo.test'
const LINHA = '7b0c2c1e-8f0e-4c55-9a51-2f0b8a3c1d10'

function moldura() {
  const ouvintes = {}
  const enviadas = []
  const chamadas = []
  const avisos = []
  const rota = Vue.reactive({ path: '/erp-produtos' })
  const win = {
    location: { origin: ORIGEM },
    addEventListener: (tipo, fn) => { ouvintes[tipo] = fn },
    removeEventListener: () => {},
  }
  const montar = []
  const api = url => { chamadas.push(url); return Promise.resolve({ id: LINHA, sku: 'dg019,dg020', name: 'Uranyx F105', department: 'celular', fotos_url: null, fotos_path: null, embalagens_url: null, embalagens_path: null, fotos_count: null, videos_count: null, embalagens_count: null, midias_contadas_em: null, irmaos: [], pode_editar: true }) }
  const tela = new Function('ref', 'computed', 'watch', 'onMounted', 'onBeforeUnmount', 'useRoute', 'useAuthStore',
    'navigateTo', 'useApi', 'useToasts', 'window',
    transpile(body) + '\nreturn { abertas, quadros, midias, aplicarMidias, aoEnviarMidias, fecharMidias }')(
    Vue.ref, Vue.computed, Vue.watch, fn => montar.push(fn), () => {}, () => rota, () => ({ user: { erp: true } }),
    () => {}, () => ({ api }), () => ({ error: (t, l) => avisos.push([t, l]) }), win)
  for (const fn of montar) fn()
  const produtos = { postMessage: (m, origem) => enviadas.push([m, origem]) }
  tela.quadros['/erp-produtos'] = { contentWindow: produtos }
  const mensagem = (data, extra = {}) => ouvintes.message({ origin: ORIGEM, source: produtos, data, ...extra })
  return { tela, rota, produtos, enviadas, chamadas, avisos, mensagem }
}
const espera = () => new Promise(r => setImmediate(r))

;(async () => {
  const m = moldura()
  assert.deepEqual(m.tela.abertas.value, ['/erp-produtos'])
  // Ignorado: outro site, outra janela do mesmo site, id que não é de linha, aba desconhecida.
  m.mensagem({ tipo: 'erp-abrir-midias', pricing_id: LINHA, aba: 'fotos' }, { origin: 'https://outro.test' })
  m.mensagem({ tipo: 'erp-abrir-midias', pricing_id: LINHA, aba: 'fotos' }, { source: { postMessage() {} } })
  m.mensagem({ tipo: 'erp-abrir-midias', pricing_id: '../../api/users', aba: 'fotos' })
  m.mensagem({ tipo: 'erp-abrir-midias', pricing_id: LINHA, aba: 'videos' })
  m.mensagem({ tipo: 'erp-abrir-pedido', numero: '1' })
  await espera()
  assert.deepEqual(m.chamadas, [])
  assert.equal(m.tela.midias.value, null)

  // Do próprio quadro: lê a linha com o login da pessoa e abre a janela na aba pedida.
  m.mensagem({ tipo: 'erp-abrir-midias', pricing_id: LINHA, aba: 'embalagens' })
  await espera()
  assert.deepEqual(m.chamadas, [`/api/pricing/mega/products/${LINHA}`])
  assert.equal(m.tela.midias.value.aba, 'embalagens')
  assert.equal(m.tela.midias.value.linha.sku, 'dg019,dg020')

  // O que a janela muda volta para o quadro, só para o próprio site.
  m.tela.aoEnviarMidias(true)
  m.tela.aplicarMidias(LINHA, { fotos_path: '/Celular/Uranyx F105', fotos_count: 3, videos_count: undefined })
  assert.equal(m.tela.midias.value.linha.fotos_count, 3)
  assert.deepEqual(m.enviadas, [
    [{ tipo: 'erp-midias-enviando', pricing_id: LINHA, ativo: true }, ORIGEM],
    [{ tipo: 'erp-midias', pricing_id: LINHA, campos: { fotos_path: '/Celular/Uranyx F105', fotos_count: 3 } }, ORIGEM],
  ])

  // Fechar desliga o "Enviando…" do quadro; sair do Produtos 2 fecha a janela.
  m.enviadas.length = 0
  m.tela.fecharMidias()
  assert.equal(m.tela.midias.value, null)
  assert.deepEqual(m.enviadas, [[{ tipo: 'erp-midias-enviando', pricing_id: LINHA, ativo: false }, ORIGEM]])
  m.mensagem({ tipo: 'erp-abrir-midias', pricing_id: LINHA, aba: 'fotos' })
  await espera()
  assert.equal(m.tela.midias.value.aba, 'fotos')
  m.rota.path = '/pricing/produtos'
  await espera()
  assert.equal(m.tela.midias.value, null)

  // A API recusou (sem permissão, linha apagada): aviso, sem janela.
  const falha = moldura()
  falha.tela.quadros['/erp-produtos'].contentWindow = falha.produtos
  const recusa = Object.assign(new Error('403'), { data: { detail: { code: 'forbidden' } } })
  falha.chamadas.push = () => { throw recusa }
  falha.mensagem({ tipo: 'erp-abrir-midias', pricing_id: LINHA, aba: 'fotos' })
  await espera()
  assert.equal(falha.tela.midias.value, null)
  assert.equal(falha.avisos.length, 1)
  assert.match(falha.avisos[0][1][0], /Não foi possível abrir as fotos/)
  console.log('ok erp-produtos-midias')
})().catch(e => { console.error(e); process.exit(1) })
