// node tests/atendimento-nf-anexo.cjs — anexar a NF (PDF) na resposta da
// caixa /atendimento (28/10/2026, o 1º empecilho da equipe respondendo pelo
// DaVinci). O botão de anexo aceita PDF; o botão "NF" busca a DANFE da nota
// cheia do pedido (GET /conversas/{id}/nf) e anexa; o rascunho do agente com
// "Enviar o arquivo da NF" oferece o anexo — a pessoa confere e manda.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
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
  compileScript(descriptor, { id: path.basename(rel) })
  return { descriptor, fonte }
}

function exportsDoScript(descriptor) {
  // Só o <script> comum (sem imports de componentes): os ajudantes puros.
  const corpo = descriptor.script.content.replace(/^import[^\n]*\n/gm, '')
  const mod = { exports: {} }
  new Function('require', 'module', 'exports', transpile(corpo))(require, mod, mod.exports)
  return mod.exports
}

const conversa = sfc('../components/AtendimentoConversa.vue')
const C = exportsDoScript(conversa.descriptor)
const fonte = conversa.fonte

// ─── o agente oferece a NF só quando marcou a ação ──────────────────────────
assert.equal(C.nfNoRascunho({ agente: { acoes_pessoa: [{ codigo: 'enviar_arquivo_nf', rotulo: 'Enviar o arquivo da NF' }] } }), true)
assert.equal(C.nfNoRascunho({ agente: { acoes_pessoa: [{ codigo: 'abrir_chamado' }] } }), false)
assert.equal(C.nfNoRascunho({ agente: null }), false)
assert.equal(C.nfNoRascunho(null), false)
assert.equal(C.LEGENDA_NF, 'Segue a nota fiscal do seu pedido.')

// ─── o title do botão "NF" ──────────────────────────────────────────────────
// Sem anexo na plataforma: o caminho dela (vem do servidor).
assert.match(C.tituloDaNf({ nf_numero: '15', pdf: null }, 'Amazon: … Seller Central …'), /Seller Central/)
assert.equal(C.tituloDaNf({ nf_numero: '15', pdf: 'arquivo' }, ''), 'anexar a NF nº 15 deste pedido (PDF)')
assert.match(C.tituloDaNf({ nf_numero: '15', pdf: 'imagem' }, ''), /não aceita PDF: vai como imagem/)
assert.match(C.tituloDaNf({ nf_numero: null, nf_motivo: 'O pedido 1 ainda não tem NF…' }, ''), /ainda não tem NF/)
assert.equal(C.tituloDaNf(null, ''), 'anexar a NF deste pedido')

// ─── a tela ─────────────────────────────────────────────────────────────────
// O botão de anexo aceita PDF (pelo seletor e pela conferência do tipo).
assert.match(fonte, /accept="image\/jpeg,image\/png,application\/pdf"/)
assert.match(fonte, /const TIPOS_FOTO = \['image\/jpeg', 'image\/png', 'application\/pdf'\]/)
// O atalho busca a DANFE como arquivo (blob) pela rota da conversa e anexa como PDF.
assert.match(fonte, /\$fetch\.raw<Blob>\(`\/api\/atendimento\/conversas\/\$\{encodeURIComponent\(id\)\}\/nf`, \{ credentials: 'include', responseType: 'blob' \}\)/)
// O nome do arquivo vem da NF que o servidor escolheu (cabeçalho), não do painel.
assert.match(fonte, /r\.headers\.get\('x-nf-numero'\) \|\| envioFoto\.value\?\.nf_numero/)
assert.match(fonte, /new File\(\[r\._data\], numero \? `NF_\$\{numero\}\.pdf` : 'NF\.pdf', \{ type: 'application\/pdf' \}\)/)
// O erro vem como Blob e o `data` do FetchError é só leitura: lê numa cópia.
assert.match(fonte, /let dados = e\?\.data\n    if \(dados instanceof Blob\)/)
assert.doesNotMatch(fonte, /e\.data = JSON\.parse/)
// No ML (texto obrigatório) a frase da NF já vem escrita.
assert.match(fonte, /legenda_obrigatoria && !legendaFoto\.value\.trim\(\)\) legendaFoto\.value = LEGENDA_NF/)
// Sem anexo aqui (Amazon, Magalu, pergunta do ML): o clique diz o caminho.
assert.match(fonte, /toasts\.warning\('A NF não sai pelo DaVinci nesta conversa', motivoSemFoto\.value\)/)
// O botão "NF" e o cartão do rascunho existem; o anexo PDF diz como chega.
assert.match(fonte, /data-anexar-nf/)
assert.match(fonte, /data-nf-no-rascunho/)
assert.match(fonte, /Este chat não aceita PDF: o PDF vai como IMAGEM/)
// Nada sai sozinho: o cartão do rascunho só ANEXA (o envio é o botão da pessoa).
const cartao = fonte.slice(fonte.indexOf('data-nf-no-rascunho'), fonte.indexOf('data-nf-no-rascunho') + 1600)
assert.match(cartao, /@click="anexarNf"/)
assert.doesNotMatch(cartao, /enviarFoto/)

// ─── o contrato com a API ───────────────────────────────────────────────────
const api = (rel) => fs.readFileSync(path.resolve(__dirname, '../../api/app', rel), 'utf8')
const rota = api('routers/atendimento_painel.py')
assert.match(rota, /@router\.get\("\/conversas\/\{conversa_id\}\/nf"\)/)
assert.match(rota, /"X-NF-Numero": nf\.numero/)
const schema = api('schemas/atendimento_painel.py')
for (const campo of ['pdf: str | None', 'nf_numero: str | None', 'nf_motivo: str | None']) assert.ok(schema.includes(campo), campo)
const tipo = fs.readFileSync(path.resolve(__dirname, '../components/AtendimentoPedido.vue'), 'utf8')
assert.match(tipo, /pdf\?: 'arquivo' \| 'imagem' \| null/)

console.log('atendimento-nf-anexo: ok')
