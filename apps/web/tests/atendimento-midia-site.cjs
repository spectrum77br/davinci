// node tests/atendimento-midia-site.cjs — a FOTO e o VÍDEO do cliente no CHAMADO
// DO SITE aparecem e tocam na conversa (25/11/2026). O Eduardo, com os prints do
// US-26-0006 (Uranyx): "isso o site da uranyx" — a equipe perguntou "eu consigo
// ver as fotos e vídeos desse protocolo pelo atendimento?" e o vídeo dava erro
// aberto pelo Tuta. O aviso do site traz os arquivos ANEXADOS; o cartão do e-mail
// marca a foto e o vídeo (`midia`) e a rota da conversa entrega a foto e o vídeo
// convertido para MP4 (GET /api/atendimento/email/conversas/<id>/midias/<anexo>,
// `?capa=1` = a miniatura do vídeo). O que a tela tem de garantir:
//  - as fotos e os vídeos do cartão viram miniaturas (o vídeo com a capa e o
//    "play"), na ordem do formulário (01 … 05), que abrem no visor grande — o
//    vídeo no MESMO visor, que já toca vídeo;
//  - PDF, HEIC e o resto (sem `midia`) ficam no "baixar" do e-mail inteiro;
//    sem a conversa (quem não a abre) ou com id torto, nenhuma miniatura;
//  - o arquivo grande demais para vir anexado: a frase "só no link do site";
//  - o visor diz quando o vídeo não toca, em vez da tela preta;
//  - a URL é a da rota do servidor.
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
  const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
  assert.deepEqual(errors, [], rel)
  const compiled = compileTemplate({ source: descriptor.template.content, filename, id: path.basename(rel), compilerOptions: { isTS: true } })
  assert.deepEqual(compiled.errors, [], `${rel}: template compila`)
  return descriptor
}
function exportsDe(descriptor) {
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(descriptor.script.content))((n) => require(n), mod, mod.exports)
  return mod.exports
}
const api = (rel) => fs.readFileSync(path.resolve(__dirname, '../../api/app', rel), 'utf8')

const cartaoSfc = sfc('../components/AtendimentoEmailCartao.vue')
const C = exportsDe(cartaoSfc)
const midiasSfc = sfc('../components/AtendimentoEmailMidias.vue')
const conversaSfc = sfc('../components/AtendimentoConversa.vue')
const cs = conversaSfc.scriptSetup.content
const tela = conversaSfc.template.content

const CONVERSA = 'e063685f-9911-4429-a230-aecfdb7aa4cc'
const id = (n) => `00000000-0000-4000-8000-00000000000${n}`
const rota = (anexo, capa = false) => `/api/atendimento/email/conversas/${CONVERSA}/midias/${anexo}${capa ? '?capa=1' : ''}`
// O cartão do US-26-0006 como a Central guardou (a ordem do banco não é a do formulário).
const anexos = [
  { id: id(3), tamanho: 282428, filename: 'US-26-0006-03-defeito-1.jpg', content_type: 'image/jpeg', midia: 'imagem' },
  { id: id(4), tamanho: 267796, filename: 'US-26-0006-04-defeito-2.jpg', content_type: 'image/jpeg', midia: 'imagem' },
  { id: id(2), tamanho: 282428, filename: 'US-26-0006-02-etiqueta.jpg', content_type: 'image/jpeg', midia: 'imagem' },
  { id: id(5), tamanho: 2197022, filename: 'US-26-0006-05-video.mov', content_type: 'video/quicktime', midia: 'video' },
  { id: id(1), tamanho: 282428, filename: 'US-26-0006-01-produto.jpg', content_type: 'image/jpeg', midia: 'imagem' },
]
const cartao = { conversa_id: CONVERSA, anexos }

// ─── as miniaturas: fotos e vídeo, na ordem do formulário ───────────────────
const ms = C.midiasDoCartao(cartao)
assert.deepEqual(ms.map((m) => [m.nome, m.tipo]), [
  ['US-26-0006-01-produto.jpg', 'imagem'],
  ['US-26-0006-02-etiqueta.jpg', 'imagem'],
  ['US-26-0006-03-defeito-1.jpg', 'imagem'],
  ['US-26-0006-04-defeito-2.jpg', 'imagem'],
  ['US-26-0006-05-video.mov', 'video'],
])
assert.deepEqual(ms[3], { chave: id(4), tipo: 'imagem', url: rota(id(4)), capa: null, nome: 'US-26-0006-04-defeito-2.jpg', tamanho: '262 KB' })
assert.deepEqual(ms[4], { chave: id(5), tipo: 'video', url: rota(id(5)), capa: rota(id(5), true), nome: 'US-26-0006-05-video.mov', tamanho: '2,1 MB' })
// Os números do nome na ordem natural (10 depois de 9).
assert.deepEqual(
  C.midiasDoCartao({ conversa_id: CONVERSA, anexos: [{ ...anexos[0], id: id(7), filename: 'X-10-defeito.jpg' }, { ...anexos[0], id: id(8), filename: 'X-9-defeito.jpg' }] }).map((m) => m.nome),
  ['X-9-defeito.jpg', 'X-10-defeito.jpg'],
)

// ─── o que não vira miniatura ──────────────────────────────────────────────
const resto = [
  { id: id(6), tamanho: 1000, filename: 'nota.pdf', content_type: 'application/pdf', midia: null },
  { id: id(7), tamanho: 1000, filename: 'foto.heic', content_type: 'image/heic' },
  { id: id(8), tamanho: 1000, filename: 'x.jpg', content_type: 'image/jpeg', midia: 'outra-coisa' },
]
assert.deepEqual(C.midiasDoCartao({ conversa_id: CONVERSA, anexos: resto }), [])
assert.deepEqual(C.midiasDoCartao({ conversa_id: null, anexos }), [], 'sem a conversa (quem não a abre): nenhuma')
assert.deepEqual(C.midiasDoCartao({ conversa_id: CONVERSA }), [])
assert.deepEqual(C.midiasDoCartao(null), [])
for (const torto of ['../x', 'abc', '', `${id(1)}/x`, `${id(1)}?capa=1`]) {
  assert.deepEqual(C.midiasDoCartao({ conversa_id: CONVERSA, anexos: [{ ...anexos[0], id: torto }] }), [], `id torto: ${torto}`)
}
// Sem nome: o tipo (nunca vazio).
assert.equal(C.midiasDoCartao({ conversa_id: CONVERSA, anexos: [{ id: id(1), tamanho: 1, midia: 'video' }] })[0].nome, 'vídeo')
// O id da conversa vai codificado (o servidor confere o UUID de novo).
assert.equal(C.urlDaMidiaDoSite('a/b', id(1)), `/api/atendimento/email/conversas/a%2Fb/midias/${id(1)}`)

// ─── a rota é a do servidor ─────────────────────────────────────────────────
const rotaPy = api('routers/atendimento_email.py')
assert.match(rotaPy, /^@router\.get\("\/conversas\/\{conversa_id\}\/midias\/\{attachment_id\}"\)$/m)
assert.match(rotaPy, /capa: Annotated\[bool, Query\(\)\] = False/)
assert.match(rotaPy, /if not midia_site\.conversa_aceita\(conversa\):/)
// O cartão do e-mail do site marca a mídia e o que veio só pelo link.
assert.match(rotaPy, /item\["midia"\] = midia_site\.especie_declarada\(/)
assert.match(rotaPy, /saida\["arquivos_so_pelo_link"\] = midia_site\.arquivos_so_pelo_link\(protegido\.texto\)/)
const servico = api('services/atendimento/midia_site.py')
assert.match(servico, /^FOTO = "imagem"$/m)
assert.match(servico, /^VIDEO = "video"$/m)

// ─── o componente (renderizado) ─────────────────────────────────────────────
function requerer(nome) {
  if (nome === 'lucide-vue-next') return new Proxy({}, { get: (_a, n) => ({ render: () => Vue.h('i', { 'data-icone': String(n) }) }) })
  if (nome === '~/components/AtendimentoEmailCartao.vue') return C
  return require(nome)
}
async function renderizar(props) {
  const { content } = compileScript(midiasSfc, { id: 'teste', inlineTemplate: true })
  const mod = {}
  const nomes = ['computed', 'ref']
  new Function('exports', 'require', ...nomes, transpile(content))(mod, requerer, Vue.computed, Vue.ref)
  return (await renderToString(Vue.createSSRApp(mod.default, props))).replace(/<!--[\s\S]*?-->/g, '')
}

;(async () => {
  const html = await renderizar({ cartao: { ...cartao, arquivos_so_pelo_link: [] } })
  assert.equal((html.match(/data-midia-site="imagem"/g) || []).length, 4, '4 fotos')
  assert.equal((html.match(/data-midia-site="video"/g) || []).length, 1, '1 vídeo')
  // A foto: a própria rota na <img>; o vídeo: a capa na <img> e o "play".
  assert.ok(html.includes(`src="${rota(id(4))}"`), 'a foto pela rota')
  assert.ok(html.includes(`src="${rota(id(5), true)}"`), 'a capa do vídeo')
  assert.ok(!html.includes(`src="${rota(id(5))}"`), 'o vídeo não carrega na miniatura (só a capa)')
  assert.match(html, /data-icone="Play"/)
  assert.match(html, /title="assistir US-26-0006-05-video\.mov \(2,1 MB\)"/)
  assert.ok(!html.includes('data-midias-so-link'), 'sem arquivo só no link, sem a frase')

  // O arquivo grande demais para vir anexado: a frase.
  const soLink = await renderizar({ cartao: { conversa_id: CONVERSA, anexos: [], arquivos_so_pelo_link: [{ nome: 'VID_0042.MOV', tamanho: '25,3 MB' }] } })
  assert.match(soLink, /data-midias-so-link/)
  assert.match(soLink, /Só no link do site \(grande demais para vir anexado ao e-mail\) — abra pelo Tuta:/)
  assert.match(soLink, /VID_0042\.MOV · 25,3 MB/)

  // Nada de mídia: nada na tela.
  assert.equal((await renderizar({ cartao: { conversa_id: CONVERSA, anexos: resto } })).trim(), '')

  // ─── o template: o clique abre no visor; a que falha volta ao nome ─────────
  const t = midiasSfc.template.content
  assert.match(t, /@click="emit\('abrir', \{ url: md\.url, nome: md\.nome, tipo: 'imagem' \}\)"/)
  assert.match(t, /@click="emit\('abrir', \{ url: md\.url, nome: md\.nome, tipo: 'video' \}\)"/)
  assert.match(t, /class="size-28 object-cover" @error="falhou\[md\.chave\] = true"/)
  assert.match(t, /v-if="md\.capa && !falhou\[md\.chave\]"/)

  // ─── a conversa: o bloco no balão do e-mail, abrindo no visor de sempre ────
  assert.match(tela, /<AtendimentoEmailMidias v-if="l\.m\.email && cartaoDe\(l\.m\)" :cartao="cartaoDe\(l\.m\)!"[^>]*@abrir="abrirImagem" \/>/)
  assert.match(cs, /function abrirImagem\(i: \{ url: string; nome: string; tipo\?: 'imagem' \| 'video' \}\) \{\n\s+imagemAberta\.value = i/)
  // O visor toca o vídeo e, se não carregar, diz (cada abertura começa de novo).
  assert.match(tela, /<video v-if="imagemAberta\.tipo === 'video' && !visorFalhou"[^>]*data-visor-video @error="visorFalhou = true" \/>/)
  assert.match(tela, /v-else-if="imagemAberta\.tipo === 'video'"[^>]*data-visor-erro/)
  assert.match(cs, /watch\(imagemAberta, \(\) => \{ visorFalhou\.value = false \}\)/)

  console.log('ok — atendimento-midia-site (foto e vídeo do chamado do site: miniaturas na ordem do formulário, capa e play, só no link, visor com erro, rota do servidor)')
})().catch((e) => { console.error(e); process.exit(1) })
