// node tests/atendimento-anexo-ml.cjs — a foto que o comprador manda no
// Mercado Livre APARECE na conversa (28/10/2026, "como no Duoke"). A leitura
// guarda só o `arquivo` (o id do anexo no ML), o nome, o tipo e o tamanho;
// no pós-venda e na reclamação do ML a tela pede o arquivo à rota da conversa
// (`GET /api/atendimento/conversas/<id>/anexos/<arquivo>`), que busca na hora
// com o token da loja. O que a tela tem de garantir:
//  - foto (JPEG/PNG/GIF/WebP) vira a miniatura que abre grande; PDF, HEIC e o
//    resto, o link de baixar com o nome e o tamanho;
//  - SÓ no ML (pós-venda e reclamação) e SÓ com um `arquivo` no formato que a
//    rota aceita (o mesmo regex do servidor): nada de `..`, barra ou outra
//    plataforma virar link;
//  - o anexo com URL http(s) (as outras plataformas) continua como antes;
//  - a foto que não carrega (a rota respondeu erro) volta a ser o nome.
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
  + '\nreturn { pecasDe, urlDoAnexoMl, RE_ARQUIVO_ML }'
const { pecasDe, urlDoAnexoMl, RE_ARQUIVO_ML } = new Function('cartaoPedidoDe', 'cartaoProdutoDe', js)(P.cartaoPedidoDe, P.cartaoProdutoDe)

const CONVERSA = '0b6c5c0e-6f43-4d0a-9a7e-1f2a3b4c5d6e'
const ARQUIVO = '118859700_3dba482b-8c64-4bdf-b64e-dd2bbd9bf89f.jpeg'
const URL_ANEXO = `/api/atendimento/conversas/${CONVERSA}/anexos/${ARQUIVO}`
const posVenda = { conversaId: CONVERSA, plataforma: 'ml', canal: 'pos_venda' }
const msg = (anexos) => ({ id: 'm1', anexos })
const so = (anexo, ctx = posVenda) => pecasDe(msg([anexo]), ctx)[0]
// O anexo como a leitura do ML grava (`ml._anexos`).
const foto = { nome: 'Screenshot_20261028_110935_Gallery.jpeg', tipo: 'image/jpeg', arquivo: ARQUIVO, tamanho: 30224 }

// ─── a foto do comprador vira a miniatura (com a rota da conversa) ─────────
assert.deepEqual(so(foto), { chave: 'm1-0', tipo: 'imagem', url: URL_ANEXO, nome: foto.nome })
for (const tipo of ['image/png', 'image/gif', 'image/webp', 'image/jpg', 'IMAGE/JPEG']) {
  assert.equal(so({ ...foto, tipo }).tipo, 'imagem', tipo)
}
// Sem tipo (ou um rótulo nosso, sem `/`): a extensão do arquivo decide.
assert.equal(so({ arquivo: ARQUIVO }).tipo, 'imagem')
assert.equal(so({ arquivo: ARQUIVO, nome: 'x' }).nome, 'x')
assert.equal(so({ arquivo: ARQUIVO }).nome, ARQUIVO, 'sem nome: o id do anexo')
// A reclamação do ML também (o anexo da mensagem da reclamação).
const reclamacao = { ...posVenda, canal: 'reclamacao' }
assert.deepEqual(so({ ...foto, arquivo: '3cf94d52-0248-4bb4-98cc-b76c01ff5dc0.jpeg' }, reclamacao), {
  chave: 'm1-0', tipo: 'imagem', url: `/api/atendimento/conversas/${CONVERSA}/anexos/3cf94d52-0248-4bb4-98cc-b76c01ff5dc0.jpeg`, nome: foto.nome,
})

// ─── PDF, HEIC e o resto: o link de baixar com o nome e o tamanho ──────────
const pdf = { nome: 'nota fiscal.pdf', tipo: 'application/pdf', arquivo: '118859700_aa.pdf', tamanho: 123456 }
assert.deepEqual(so(pdf), {
  chave: 'm1-0', tipo: 'documento', url: `/api/atendimento/conversas/${CONVERSA}/anexos/118859700_aa.pdf`, nome: 'nota fiscal.pdf', tamanho: '121 KB',
})
// HEIC: o Chrome não abre numa <img> — baixa.
assert.equal(so({ ...foto, tipo: 'image/heic', arquivo: '1_a.heic' }).tipo, 'documento')
// Tipo de verdade diz PDF mesmo com o nome de foto: vale o tipo.
assert.equal(so({ ...foto, tipo: 'application/pdf' }).tipo, 'documento')

// ─── só no ML, só pós-venda e reclamação, só o formato da rota ─────────────
assert.deepEqual(so(foto, { ...posVenda, plataforma: 'shopee' }), { chave: 'm1-0', tipo: 'nome', nome: foto.nome })
assert.deepEqual(so(foto, { ...posVenda, canal: 'pergunta' }), { chave: 'm1-0', tipo: 'nome', nome: foto.nome })
assert.deepEqual(so(foto, null), { chave: 'm1-0', tipo: 'nome', nome: foto.nome }, 'sem a conversa: o nome')
assert.deepEqual(pecasDe(msg([foto]))[0], { chave: 'm1-0', tipo: 'nome', nome: foto.nome }, 'chamada antiga, sem contexto')
assert.equal(so(foto, { ...posVenda, conversaId: '' }).tipo, 'nome')
for (const ruim of ['../x.jpg', 'a/b.jpg', 'a..b.jpg', '.oculto.jpg', '%2e%2e.jpg', 'a b.jpg', 'javascript:alert(1)', '', 'x'.repeat(201)]) {
  assert.equal(so({ ...foto, arquivo: ruim }).tipo, 'nome', `arquivo ruim: ${ruim}`)
  assert.equal(urlDoAnexoMl({ arquivo: ruim }, posVenda), null, ruim)
}
assert.equal(so({ ...foto, arquivo: 12345 }).tipo, 'nome', 'arquivo que não é texto')
// O id da conversa vai codificado (o servidor ainda confere o UUID).
assert.equal(urlDoAnexoMl({ arquivo: ARQUIVO }, { ...posVenda, conversaId: 'a/b' }), `/api/atendimento/conversas/a%2Fb/anexos/${ARQUIVO}`)

// ─── o anexo com URL http(s) continua como antes (as outras plataformas) ───
assert.deepEqual(so({ ...foto, url: 'https://cdn.exemplo/foto.jpg' }), { chave: 'm1-0', tipo: 'imagem', url: 'https://cdn.exemplo/foto.jpg', nome: foto.nome })
assert.deepEqual(pecasDe(msg([{ tipo: 'imagem', nome: 'a.jpg', url: 'https://cdn.exemplo/a.jpg' }]), { conversaId: CONVERSA, plataforma: 'shopee', canal: 'chat' }).map((p) => p.tipo), ['imagem'])
// O Zap continua com a cópia dele (o contexto do ML não pega o Zap).
const zapId = '1b6c5c0e-6f43-4d0a-9a7e-1f2a3b4c5d6e'
assert.equal(so({ tipo: 'imagem', midia_id: zapId, status: 'baixada', url: `/api/atendimento/zap/midia/${zapId}` }, { conversaId: CONVERSA, plataforma: 'whatsapp', canal: 'zap' }).url, `/api/atendimento/zap/midia/${zapId}`)

// ─── o mesmo formato e a mesma rota do servidor ────────────────────────────
const anexoPy = api('services/atendimento/anexo_ml.py')
const rePy = (/^_RE_ARQUIVO = re\.compile\(r"([^"]+)"\)$/m.exec(anexoPy) || [])[1]
assert.ok(rePy, 'o regex do servidor')
assert.equal(RE_ARQUIVO_ML.source, rePy.replace('_.\\-', '_.-'), 'o regex da tela = o do servidor')
assert.match(anexoPy, /^CANAIS = frozenset\(\{CANAL_POS_VENDA, CANAL_RECLAMACAO\}\)$/m)
assert.match(anexoPy, /^TIPOS_INLINE = frozenset\(\{"image\/jpeg", "image\/png", "image\/gif", "image\/webp"\}\)$/m)
const rotaPy = api('routers/atendimento.py')
assert.match(rotaPy, /^@router\.get\("\/conversas\/\{conversa_id\}\/anexos\/\{arquivo\}"\)$/m)
assert.match(rotaPy, /prefix="\/api\/atendimento"/)

// ─── a conversa passa o contexto e a foto que falha volta a ser o nome ─────
assert.match(cs, /mapa\.set\(m\.id, pecasDe\(m, contextoAnexo\.value\)\)/)
assert.match(cs, /pecas\.value\.get\(m\.id\) \?\? pecasDe\(m, contextoAnexo\.value\)/)
assert.match(cs, /conversaId: String\(c\.id\), plataforma: c\.plataforma, canal: c\.canal/)
assert.match(tela, /v-else-if="pc\.tipo === 'imagem' && !fotoFalhou\[pc\.chave\]"/)
assert.match(tela, /class="size-28 object-cover" @error="fotoFalhou\[pc\.chave\] = true"/)
// Ao trocar de conversa, a marca de "não carregou" recomeça.
assert.match(cs, /imagemAberta\.value = null\n {2}fotoFalhou\.value = \{\}/)
// O documento BAIXA (nunca abre aqui) e a foto abre no visor grande.
assert.match(tela, /v-else-if="pc\.tipo === 'documento'"\s+:href="pc\.url"\s+download/)
assert.match(tela, /@click="imagemAberta = \{ url: pc\.url, nome: pc\.nome \}"/)
// A peça que caiu no nome: a última opção do balão mostra `pc.nome`.
assert.match(tela, /<span v-else class="rounded border bg-background px-2 py-0\.5 text-xs text-muted-foreground">\{\{ pc\.nome \}\}<\/span>/)

console.log('ok — atendimento-anexo-ml (foto do comprador do ML na conversa: miniatura, PDF para baixar, só ML, formato da rota, volta ao nome no erro)')
