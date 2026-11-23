// node tests/atendimento-anexo-amazon.cjs — a foto que o comprador manda na
// AMAZON aparece na conversa (23/11/2026). O Eduardo: "a amazon as imagens vem
// como image.jpg mas não ela em si [...] tem que fazer aparecer as imagens em
// atendimento". A foto vem DENTRO do e-mail do Seller Central; a leitura guarda
// a nossa cópia no disco (`anexo_amazon`) e o anexo ganha `arquivo`
// ("<sha256>.<ext>"), `tipo` (o mime) e `tamanho`. A MESMA rota do anexo do ML
// (`GET /api/atendimento/conversas/<id>/anexos/<arquivo>`) entrega. O que a tela
// tem de garantir:
//  - foto vira a miniatura que abre grande (o visor `imagemAberta`); várias fotos
//    na mesma mensagem, várias miniaturas; PDF, o link de baixar com o tamanho;
//  - SÓ na Amazon por e-mail e SÓ com o `arquivo` no formato que o servidor gera
//    (o mesmo regex): nada de `..`, barra, maiúscula ou outra extensão;
//  - o anexo antigo (só o nome, sem cópia) continua o nome, como antes;
//  - o ML e o Zap continuam como estavam.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const { parse, compileTemplate } = require('vue/compiler-sfc')

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
function trecho(src, ini, fim) {
  const a = src.indexOf(ini)
  const b = src.indexOf(fim, a + 1)
  assert.ok(a >= 0 && b > a, `trecho ${ini} … ${fim}`)
  return src.slice(a, b)
}
const api = (rel) => fs.readFileSync(path.resolve(__dirname, '../../api/app', rel), 'utf8')

const P = exportsDe(sfc('../components/AtendimentoPlataforma.vue'))
const conversaSfc = sfc('../components/AtendimentoConversa.vue')
const cs = conversaSfc.scriptSetup.content
const tela = conversaSfc.template.content

const js = transpile(trecho(cs, 'const TIPO_LABEL', '// Links no texto viram') + '\n' + trecho(cs, 'type Peca', '// Uma vez por leitura da conversa'))
  + '\nreturn { pecasDe, urlDoAnexoAmazon, RE_ARQUIVO_AMAZON }'
const { pecasDe, urlDoAnexoAmazon, RE_ARQUIVO_AMAZON } = new Function('cartaoPedidoDe', 'cartaoProdutoDe', js)(P.cartaoPedidoDe, P.cartaoProdutoDe)

const CONVERSA = '0b6c5c0e-6f43-4d0a-9a7e-1f2a3b4c5d6e'
const SHA_1 = 'a'.repeat(32) + '0123456789abcdef'.repeat(2)
const SHA_2 = 'b'.repeat(64)
const SHA_3 = 'c'.repeat(64)
const rota = (arquivo) => `/api/atendimento/conversas/${CONVERSA}/anexos/${arquivo}`
const amazon = { conversaId: CONVERSA, plataforma: 'amazon', canal: 'email' }
const msg = (anexos) => ({ id: 'm1', anexos })
const so = (anexo, ctx = amazon) => pecasDe(msg([anexo]), ctx)[0]
// O anexo como a leitura da Amazon grava desde 23/11/2026 (`anexo_amazon.preparar`).
const foto = { tipo: 'image/jpeg', nome: 'image.jpg', arquivo: `${SHA_1}.jpg`, tamanho: 2647180 }

// ─── a foto do comprador vira a miniatura (com a rota da conversa) ─────────
assert.deepEqual(so(foto), { chave: 'm1-0', tipo: 'imagem', url: rota(`${SHA_1}.jpg`), nome: 'image.jpg' })
for (const [tipo, ext] of [['image/png', 'png'], ['image/gif', 'gif'], ['image/webp', 'webp']]) {
  assert.equal(so({ ...foto, tipo, arquivo: `${SHA_1}.${ext}` }).tipo, 'imagem', tipo)
}
// Sem nome: o próprio arquivo (nunca vazio).
assert.equal(so({ tipo: 'image/jpeg', arquivo: `${SHA_1}.jpg` }).nome, `${SHA_1}.jpg`)

// ─── várias fotos na mesma mensagem (o print: "Segue fotos") ───────────────
const varias = pecasDe(msg([
  foto,
  { tipo: 'image/jpeg', nome: 'image1.jpg', arquivo: `${SHA_2}.jpg`, tamanho: 103867 },
  { tipo: 'image/png', nome: 'print.png', arquivo: `${SHA_3}.png`, tamanho: 18326 },
]), amazon)
assert.deepEqual(varias.map((p) => [p.chave, p.tipo, p.url]), [
  ['m1-0', 'imagem', rota(`${SHA_1}.jpg`)],
  ['m1-1', 'imagem', rota(`${SHA_2}.jpg`)],
  ['m1-2', 'imagem', rota(`${SHA_3}.png`)],
])

// ─── o PDF: o link de baixar com o nome e o tamanho ────────────────────────
assert.deepEqual(so({ tipo: 'application/pdf', nome: 'nota fiscal.pdf', arquivo: `${SHA_2}.pdf`, tamanho: 234257 }), {
  chave: 'm1-0', tipo: 'documento', url: rota(`${SHA_2}.pdf`), nome: 'nota fiscal.pdf', tamanho: '229 KB',
})

// ─── o antigo (só o nome) continua o nome, como antes ──────────────────────
assert.deepEqual(so({ tipo: 'arquivo', nome: 'image.jpg' }), { chave: 'm1-0', tipo: 'nome', nome: 'image.jpg' })

// ─── só na Amazon por e-mail, só o formato que o servidor gera ─────────────
assert.deepEqual(so(foto, { ...amazon, plataforma: 'shopee', canal: 'chat' }), { chave: 'm1-0', tipo: 'nome', nome: 'image.jpg' })
assert.deepEqual(so(foto, { ...amazon, canal: 'chat' }), { chave: 'm1-0', tipo: 'nome', nome: 'image.jpg' })
assert.deepEqual(so(foto, null), { chave: 'm1-0', tipo: 'nome', nome: 'image.jpg' }, 'sem a conversa: o nome')
assert.equal(so(foto, { ...amazon, conversaId: '' }).tipo, 'nome')
for (const ruim of [
  'image.jpg', `${SHA_1}.exe`, `${SHA_1}.JPG`, `${SHA_1.toUpperCase()}.jpg`, `../${SHA_1}.jpg`, `${SHA_1}.jpg/x`,
  `${'a'.repeat(63)}.jpg`, `${SHA_1}..jpg`, '%2e%2e.jpg', 'javascript:alert(1)', '',
]) {
  assert.equal(so({ ...foto, arquivo: ruim }).tipo, 'nome', `arquivo ruim: ${ruim}`)
  assert.equal(urlDoAnexoAmazon({ arquivo: ruim }, amazon), null, ruim)
}
assert.equal(so({ ...foto, arquivo: 12345 }).tipo, 'nome', 'arquivo que não é texto')
// O id da conversa vai codificado (o servidor ainda confere o UUID).
assert.equal(urlDoAnexoAmazon({ arquivo: `${SHA_1}.jpg` }, { ...amazon, conversaId: 'a/b' }), `/api/atendimento/conversas/a%2Fb/anexos/${SHA_1}.jpg`)

// ─── o ML e o Zap continuam como estavam ───────────────────────────────────
const posVenda = { conversaId: CONVERSA, plataforma: 'ml', canal: 'pos_venda' }
const ARQUIVO_ML = '118859700_3dba482b-8c64-4bdf-b64e-dd2bbd9bf89f.jpeg'
assert.deepEqual(so({ nome: 'x.jpeg', tipo: 'image/jpeg', arquivo: ARQUIVO_ML }, posVenda), { chave: 'm1-0', tipo: 'imagem', url: rota(ARQUIVO_ML), nome: 'x.jpeg' })
// O id do ML não tem o formato da Amazon: na conversa da Amazon fica o nome.
assert.equal(so({ nome: 'x.jpeg', tipo: 'image/jpeg', arquivo: ARQUIVO_ML }).tipo, 'nome')
const zapId = '1b6c5c0e-6f43-4d0a-9a7e-1f2a3b4c5d6e'
assert.equal(so({ tipo: 'imagem', midia_id: zapId, status: 'baixada', url: `/api/atendimento/zap/midia/${zapId}` }, { conversaId: CONVERSA, plataforma: 'whatsapp', canal: 'zap' }).url, `/api/atendimento/zap/midia/${zapId}`)

// ─── o mesmo formato e a mesma rota do servidor ────────────────────────────
const anexoPy = api('services/atendimento/anexo_amazon.py')
const rePy = (/^_RE_ARQUIVO = re\.compile\(r"([^"]+)"\)$/m.exec(anexoPy) || [])[1]
assert.ok(rePy, 'o regex do servidor')
assert.equal(RE_ARQUIVO_AMAZON.source, rePy, 'o regex da tela = o do servidor')
assert.match(anexoPy, /^PLATAFORMA = "amazon"$/m)
assert.match(anexoPy, /^CANAL = CANAL_EMAIL$/m)
const rotaPy = api('routers/atendimento.py')
assert.match(rotaPy, /^@router\.get\("\/conversas\/\{conversa_id\}\/anexos\/\{arquivo\}"\)$/m)
assert.match(rotaPy, /if anexo_amazon\.conversa_aceita\(c\):\n\s+# Amazon/)
// O leitor guarda a cópia ANTES de gravar a mensagem.
const leitorPy = api('services/atendimento/amazon_email.py')
assert.match(leitorPy, /anexos = await anexo_amazon\.guardar\(em\.anexos, em\.copias\)\n\s+_mensagem, nova = await gravar\.gravar_mensagem\(/)

// ─── a conversa passa o contexto; a foto abre no visor; a que falha é o nome ─
assert.match(cs, /const urlMl = url \? null : urlDoAnexoMl\(a, ctx\) \|\| urlDoAnexoAmazon\(a, ctx\)/)
assert.match(cs, /conversaId: String\(c\.id\), plataforma: c\.plataforma, canal: c\.canal/)
assert.match(tela, /v-else-if="pc\.tipo === 'imagem' && !fotoFalhou\[pc\.chave\]"/)
assert.match(tela, /@click="imagemAberta = \{ url: pc\.url, nome: pc\.nome \}"/)
assert.match(tela, /class="size-28 object-cover" @error="fotoFalhou\[pc\.chave\] = true"/)
assert.match(tela, /v-else-if="pc\.tipo === 'documento'"\s+:href="pc\.url"\s+download/)
// O visor grande mostra a imagem aberta.
assert.match(tela, /v-if="imagemAberta"/)

console.log('ok — atendimento-anexo-amazon (foto do comprador da Amazon na conversa: miniaturas, PDF para baixar, só Amazon e-mail, formato da rota, o antigo fica o nome)')
