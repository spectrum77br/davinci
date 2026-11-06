// node tests/atendimento-varias-imagens.cjs — várias imagens de uma vez na
// resposta do /atendimento (03/11/2026, a equipe: "às vezes preciso mandar
// umas 5"). Escolher várias (botão, colar, arrastar), a prévia com o "tirar",
// e o envio: nos chats uma mensagem por imagem, NA ORDEM; no ML todas numa
// mensagem com o texto. A que falhou mostra o porquê e reenvia sozinha; a
// incerta (pode ter saído) para tudo e não reenvia.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { parse, compileScript, compileTemplate } = require('vue/compiler-sfc')

const transpile = (source) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText

const filename = path.resolve(__dirname, '../components/AtendimentoConversa.vue')
const fonte = fs.readFileSync(filename, 'utf8')
const { descriptor, errors } = parse(fonte, { filename })
assert.deepEqual(errors, [])
const tpl = compileTemplate({ source: descriptor.template.content, filename, id: 'conversa', compilerOptions: { isTS: true } })
assert.deepEqual(tpl.errors, [], 'template compila')
compileScript(descriptor, { id: 'conversa' })

const corpo = descriptor.script.content.replace(/^import[^\n]*\n/gm, '')
const mod = { exports: {} }
new Function('require', 'module', 'exports', transpile(corpo))(require, mod, mod.exports)
const C = mod.exports
const setup = descriptor.scriptSetup.content

// ─── o que entra na lista ───────────────────────────────────────────────────
const TIPOS = ['image/jpeg', 'image/png', 'application/pdf']
const arq = (name, { type = 'image/jpeg', size = 1000, lastModified = 1 } = {}) => ({ name, type, size, lastModified })
{
  const op = { max: 5, maxBytes: 10 * 1024 * 1024, tipos: TIPOS }
  const r = C.escolherAnexos([], [arq('a.jpg'), arq('b.png', { type: 'image/png' }), arq('c.gif', { type: 'image/gif' }), arq('d.pdf', { type: 'application/pdf' })], op)
  assert.deepEqual(r.aceitos.map((f) => f.name), ['a.jpg', 'b.png', 'd.pdf'], 'na ordem, só JPG/PNG/PDF')
  assert.deepEqual(r.recusas, ['c.gif: só dá para mandar foto JPG ou PNG, ou PDF.'])
  // Grande demais e o mesmo arquivo de novo.
  const r2 = C.escolherAnexos([{ arquivo: arq('a.jpg') }], [arq('a.jpg'), arq('g.jpg', { size: 11 * 1024 * 1024 })], op)
  assert.deepEqual(r2.aceitos, [])
  assert.deepEqual(r2.recusas, ['a.jpg: já está anexado.', 'g.jpg: passa de 10 MB — escolha um menor.'])
  // O teto: 7 escolhidas com 5 de máximo → entram as 5 primeiras.
  const sete = Array.from({ length: 7 }, (_, i) => arq(`${i + 1}.jpg`, { lastModified: i }))
  const r3 = C.escolherAnexos([], sete, op)
  assert.deepEqual(r3.aceitos.map((f) => f.name), ['1.jpg', '2.jpg', '3.jpg', '4.jpg', '5.jpg'])
  assert.deepEqual(r3.recusas, ['Cabem até 5 anexos de uma vez — os outros ficaram de fora.'])
  assert.deepEqual(C.escolherAnexos([{ arquivo: arq('x.jpg') }], [arq('y.jpg')], { ...op, max: 1 }).recusas, ['Aqui vai um anexo por vez.'])
}
assert.equal(C.rotuloEnviarAnexos([{ pdf: false }]), 'Enviar foto')
assert.equal(C.rotuloEnviarAnexos([{ pdf: true }]), 'Enviar PDF')
assert.equal(C.rotuloEnviarAnexos([{ pdf: false }, { pdf: false }, { pdf: false }]), 'Enviar 3 fotos')
assert.equal(C.rotuloEnviarAnexos([{ pdf: false }, { pdf: true }]), 'Enviar 2 anexos')

// ─── o envio, rodando o trecho da tela com a API falsa ──────────────────────
const ini = setup.indexOf('const fotoInput = ref')
const fim = setup.indexOf('// ─── "tentar de novo"')
assert.ok(ini > 0 && fim > ini)
const js = transpile(setup.slice(ini, fim)) + '\nreturn { anexos, adicionarAnexos, tirarAnexo, enviarFoto, aoColar, aEnviar, legendaFoto, maxAnexos, anexarFotoDaTroca }'

globalThis.URL.createObjectURL = () => 'blob:x'
globalThis.URL.revokeObjectURL = () => {}

class FD {
  constructor() { this.campos = [] }
  append(k, v) { this.campos.push([k, typeof v === 'string' ? v : v.name]) }
  todos(k) { return this.campos.filter((c) => c[0] === k).map((c) => c[1]) }
  um(k) { return this.todos(k)[0] }
}

// A caixa oferece o "anexar" à foto da oferta de troca (03/11/2026): o provide.
const CHAVE_ANEXAR = Symbol('anexarNaCaixa')
function montar({ envio, respostas = [], confirmar = true, modo = 'responder' }) {
  const chamadas = []
  const providos = new Map()
  const fins = []
  let andou = 0
  const toasts = { ok: [], erro: [], aviso: [] }
  const detalhe = Vue.ref({ conversa: { id: 'c1', plataforma: envio.plataforma || 'shopee', somente_leitura: false }, mensagens: [{ id: '00000000-0000-4000-8000-000000000001', criada_em: '2026-11-03T10:00:00Z' }] })
  let n = 0
  const api = async (url, opts) => {
    chamadas.push({ url, fd: opts.body })
    const r = respostas[n++] ?? { status: 'enviada' }
    if (r.erro) throw r.erro
    return { mensagem: { id: `00000000-0000-4000-8000-0000000001${String(n).padStart(2, '0')}`, status: r.status, erro: r.erroMsg ?? null, criada_em: '2026-11-03T10:01:00Z' } }
  }
  const nomes = ['ref', 'computed', 'onBeforeUnmount', 'envioFoto', 'motivoSemFoto', 'MAX_FOTO', 'TIPOS_FOTO', 'escolherAnexos', 'toasts', 'modoCaixa', 'respondePorOutra', 'canalExterno', 'props', 'detalhe',
    'tituloDaNf', 'painelCarregando', 'LEGENDA_NF', 'erroDaApi', 'api', 'destinosReclamacao', 'destinoCaixa', 'rolarProFim', 'erroEnvioLegivel', 'statusDoErro', 'ultimaVista', 'RE_UUID', 'aberta', 'enviandoIds', 'envioSimulado', 'carregar', 'confirm', 'FormData',
    'provide', 'CHAVE_ANEXAR_NA_CAIXA', 'fimDoEnvioDeAnexos', 'andarFila']
  const valores = [Vue.ref, Vue.computed, () => {}, Vue.ref({ pode: true, max_bytes: 10 * 1024 * 1024, ...envio }), Vue.ref(''), 10 * 1024 * 1024, TIPOS, C.escolherAnexos,
    { success: (t) => toasts.ok.push(t), error: (t, l) => toasts.erro.push([t, l]), warning: (t, l) => toasts.aviso.push([t, l]) },
    Vue.ref(modo), Vue.ref(false), Vue.ref(false), { canEdit: true, flags: null }, detalhe,
    C.tituloDaNf, Vue.ref(false), C.LEGENDA_NF,
    (e, base) => ({ texto: e?.data?.detail?.detail || base, motivos: [] }),
    api, Vue.ref([]), Vue.ref(''), () => {}, (e) => `recusou: ${e}`, (e) => e?.status ?? null,
    (d) => d.mensagens[d.mensagens.length - 1].id, /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i,
    (id) => id === 'c1', new Set(), () => false, async () => {}, () => confirmar, FD,
    (k, v) => providos.set(k, v), CHAVE_ANEXAR, (_, __, resultado) => fins.push(resultado), () => { andou++ }]
  const t = new Function(...nomes, js)(...valores)
  return { t, chamadas, toasts, detalhe, providos, fins, andou: () => andou }
}
const fotos = (n) => Array.from({ length: n }, (_, i) => ({ name: `f${i + 1}.jpg`, type: 'image/jpeg', size: 500 + i, lastModified: i }))

async function principal() {
  // Shopee (chat): 3 imagens → 3 POSTs, UMA por vez, na ordem; a próxima vai
  // com a última que saiu como "vista" (senão o servidor recusaria
  // `conversa_mudou` pela imagem anterior).
  {
    const m = montar({ envio: { max_anexos: 10, numa_mensagem: false, legenda_permitida: false } })
    m.t.adicionarAnexos(fotos(3))
    assert.equal(m.t.anexos.value.length, 3)
    await m.t.enviarFoto()
    assert.deepEqual(m.chamadas.map((c) => c.fd.todos('arquivo')), [['f1.jpg'], ['f2.jpg'], ['f3.jpg']])
    assert.equal(m.chamadas[0].fd.um('ultima_vista_id'), '00000000-0000-4000-8000-000000000001')
    assert.equal(m.chamadas[1].fd.um('ultima_vista_id'), '00000000-0000-4000-8000-000000000101')
    assert.equal(m.chamadas[2].fd.um('ultima_vista_id'), '00000000-0000-4000-8000-000000000102')
    assert.equal(m.t.anexos.value.length, 0, 'tudo saiu: a lista some')
    assert.deepEqual(m.toasts.ok, ['3 anexos enviados'])
    // O que a fila de texto ouve (06/11/2026, atendimento-fila-envio.cjs): saiu tudo.
    assert.deepEqual(m.fins, [{ previstos: 3, saidos: 3, restantes: 0 }])
    assert.equal(m.andou(), 1, 'a fila de texto é avisada, mesmo com a conversa desmontada')
  }

  // A 2ª recusada pela plataforma: a 3ª segue; a lista mostra qual falhou e
  // "reenviar" manda SÓ ela.
  {
    const m = montar({ envio: { max_anexos: 10, numa_mensagem: false }, respostas: [{ status: 'enviada' }, { status: 'falhou', erroMsg: 'shopee upload 500' }, { status: 'enviada' }] })
    m.t.adicionarAnexos(fotos(3))
    await m.t.enviarFoto()
    assert.equal(m.chamadas.length, 3)
    assert.deepEqual(m.t.anexos.value.map((a) => a.estado), ['enviada', 'falhou', 'enviada'])
    assert.equal(m.t.anexos.value[1].erro, 'A plataforma recusou: recusou: shopee upload 500')
    assert.deepEqual(m.toasts.erro[0], ['Um anexo NÃO saiu', ['2 saíram.', 'Veja qual abaixo e reenvie só ele.']])
    await m.t.enviarFoto({ so: m.t.anexos.value[1].id })
    assert.deepEqual(m.chamadas.slice(3).map((c) => c.fd.todos('arquivo')), [['f2.jpg']])
    assert.equal(m.t.anexos.value.length, 0)
    assert.deepEqual(m.fins, [{ previstos: 3, saidos: 2, restantes: 1 }, { previstos: 1, saidos: 1, restantes: 0 }], 'o texto esperando só segue no fim')
  }

  // Sem resposta do servidor (pode ter saído): PARA — o resto não vai, e a
  // incerta não volta a "enviar".
  {
    const m = montar({ envio: { max_anexos: 10, numa_mensagem: false }, respostas: [{ status: 'enviada' }, { erro: { status: 502 } }] })
    m.t.adicionarAnexos(fotos(3))
    await m.t.enviarFoto()
    assert.equal(m.chamadas.length, 2)
    assert.deepEqual(m.t.anexos.value.map((a) => a.estado), ['enviada', 'incerta', 'pendente'])
    assert.deepEqual(m.t.aEnviar.value.map((a) => a.arquivo.name), ['f3.jpg'])
    assert.equal(m.toasts.aviso.length, 1)
    assert.deepEqual(m.fins, [{ previstos: 3, saidos: 1, restantes: 2 }], 'a incerta e a que não foi seguram o texto')
  }

  // A trava de repetida do servidor (a mesma imagem há menos de 2 min): não
  // é erro — fica como já saída; a seguinte vai.
  {
    const repetida = { status: 409, data: { detail: { code: 'envio_repetido', detail: 'Esta mesma foto acabou de ser enviada' } } }
    const m = montar({ envio: { max_anexos: 10, numa_mensagem: false }, respostas: [{ erro: repetida }, { status: 'enviada' }] })
    m.t.adicionarAnexos(fotos(2))
    await m.t.enviarFoto()
    assert.equal(m.chamadas.length, 2)
    assert.equal(m.t.anexos.value.length, 0)
    assert.deepEqual(m.toasts.ok, ['Anexo enviado'])
    assert.deepEqual(m.fins, [{ previstos: 2, saidos: 2, restantes: 0 }], 'a repetida já tinha saído: conta como saída')
  }

  // Trava que vale para todas (envio desligado): para na 1ª.
  {
    const trava = { status: 409, data: { detail: { code: 'envio_desligado', detail: 'O envio está desligado.' } } }
    const m = montar({ envio: { max_anexos: 10, numa_mensagem: false }, respostas: [{ erro: trava }] })
    m.t.adicionarAnexos(fotos(3))
    await m.t.enviarFoto()
    assert.equal(m.chamadas.length, 1)
    assert.deepEqual(m.t.anexos.value.map((a) => a.estado), ['falhou', 'pendente', 'pendente'])
  }

  // Zap: a legenda (opcional) vai com a ÚLTIMA imagem.
  {
    const m = montar({ envio: { plataforma: 'whatsapp', max_anexos: 10, numa_mensagem: false, legenda_permitida: true } })
    m.t.adicionarAnexos(fotos(3))
    m.t.legendaFoto.value = 'Seguem as fotos'
    await m.t.enviarFoto()
    assert.deepEqual(m.chamadas.map((c) => c.fd.um('legenda') ?? null), [null, null, 'Seguem as fotos'])
  }

  // ML: TODAS numa mensagem, com o texto (obrigatório).
  {
    const m = montar({ envio: { plataforma: 'ml', max_anexos: 5, numa_mensagem: true, legenda_obrigatoria: true, legenda_permitida: true } })
    m.t.adicionarAnexos(fotos(3))
    await m.t.enviarFoto()
    assert.equal(m.chamadas.length, 0, 'sem texto, nada sai')
    m.t.legendaFoto.value = 'Seguem as fotos do produto.'
    await m.t.enviarFoto()
    assert.equal(m.chamadas.length, 1)
    assert.deepEqual(m.chamadas[0].fd.todos('arquivo'), ['f1.jpg', 'f2.jpg', 'f3.jpg'])
    assert.equal(m.chamadas[0].fd.um('legenda'), 'Seguem as fotos do produto.')
    assert.equal(m.t.anexos.value.length, 0)
  }

  // Tirar uma antes de enviar; colar (Ctrl+V) arquivos vira anexo.
  {
    const m = montar({ envio: { max_anexos: 10, numa_mensagem: false } })
    m.t.adicionarAnexos(fotos(3))
    m.t.tirarAnexo(m.t.anexos.value[1].id)
    assert.deepEqual(m.t.anexos.value.map((a) => a.arquivo.name), ['f1.jpg', 'f3.jpg'])
    let evitou = false
    m.t.aoColar({ clipboardData: { files: [{ name: 'image.png', type: 'image/png', size: 900, lastModified: 99 }] }, preventDefault: () => { evitou = true } })
    assert.ok(evitou)
    assert.deepEqual(m.t.anexos.value.map((a) => a.arquivo.name), ['f1.jpg', 'f3.jpg', 'image.png'])
    // Colar só texto: segue normal (não é anexo).
    evitou = false
    m.t.aoColar({ clipboardData: { files: [] }, preventDefault: () => { evitou = true } })
    assert.equal(evitou, false)
  }

  // ─── a tela ───────────────────────────────────────────────────────────────
  assert.match(fonte, /accept="image\/jpeg,image\/png,application\/pdf" :multiple="maxAnexos > 1"/)
  assert.match(fonte, /@paste="aoColar"/)
  assert.match(fonte, /@dragover="aoArrastarSobre"/)
  assert.match(fonte, /@drop="aoSoltar"/)
  assert.match(fonte, /:aria-label="`tirar o anexo \$\{i \+ 1\}/)
  assert.match(fonte, /@click="enviarFoto\(\{ so: a\.id \}\)"/)

  // ─── o contrato com a API ─────────────────────────────────────────────────
  const api = (rel) => fs.readFileSync(path.resolve(__dirname, '../../api/app', rel), 'utf8')
  assert.match(api('schemas/atendimento_painel.py'), /max_anexos: int = 1\n    numa_mensagem: bool = False/)
  assert.match(api('routers/atendimento_painel.py'), /arquivo: Annotated\[list\[UploadFile\], File\(\)\]/)
  const tipo = fs.readFileSync(path.resolve(__dirname, '../components/AtendimentoPedido.vue'), 'utf8')
  assert.match(tipo, /max_anexos\?: number\n  numa_mensagem\?: boolean/)

  // A FOTO DA OFERTA DE TROCA (03/11/2026): o "copiar oferta" das sugestões põe a
  // foto do modelo aqui como anexo (o provide com a chave das sugestões); a
  // pessoa confere e manda pelo "Enviar foto". null = anexou; senão o porquê.
  {
    assert.match(setup, /provide\(CHAVE_ANEXAR_NA_CAIXA, anexarFotoDaTroca\)/)
    assert.match(setup, /import \{ CHAVE_ANEXAR_NA_CAIXA \} from '~\/components\/AtendimentoTrocaSugestoes\.vue'/)
    const foto = { name: 'troca_dg054.sp.jpg', type: 'image/jpeg', size: 900, lastModified: 5 }
    const m = montar({ envio: { max_anexos: 10, numa_mensagem: false, legenda_permitida: false } })
    assert.equal(m.providos.get(CHAVE_ANEXAR), m.t.anexarFotoDaTroca, 'a caixa oferece o anexar')
    assert.equal(m.t.anexarFotoDaTroca(foto, { legenda: 'Olá, oferta' }), null)
    assert.deepEqual(m.t.anexos.value.map((a) => [a.arquivo.name, a.estado]), [['troca_dg054.sp.jpg', 'pendente']])
    assert.equal(m.t.legendaFoto.value, '', 'na Shopee a foto vai sozinha: a legenda fica vazia')
    // O segundo clique no "copiar oferta": a mesma foto não entra duas vezes.
    assert.equal(m.t.anexarFotoDaTroca(foto), null)
    assert.equal(m.t.anexos.value.length, 1)
    await m.t.enviarFoto()
    assert.deepEqual(m.chamadas.map((c) => c.fd.todos('arquivo')), [['troca_dg054.sp.jpg']], 'sai pelo POST /foto de sempre')

    // ML (a foto leva texto): o texto da oferta vai na legenda vazia — e não
    // passa por cima do que a pessoa já escreveu.
    const ml = montar({ envio: { plataforma: 'ml', max_anexos: 5, numa_mensagem: true, legenda_obrigatoria: true, legenda_permitida: true } })
    assert.equal(ml.t.anexarFotoDaTroca(foto, { legenda: 'Olá, oferta' }), null)
    assert.equal(ml.t.legendaFoto.value, 'Olá, oferta')
    const ml2 = montar({ envio: { plataforma: 'ml', max_anexos: 5, numa_mensagem: true, legenda_obrigatoria: true, legenda_permitida: true } })
    ml2.t.legendaFoto.value = 'o meu texto'
    ml2.t.anexarFotoDaTroca(foto, { legenda: 'Olá, oferta' })
    assert.equal(ml2.t.legendaFoto.value, 'o meu texto')

    // Não cabe: o porquê, e nada entra.
    const cheia = montar({ envio: { max_anexos: 1, numa_mensagem: false, legenda_permitida: false } })
    cheia.t.adicionarAnexos(fotos(1))
    assert.match(cheia.t.anexarFotoDaTroca(foto), /^Já há um anexo na caixa/)
    assert.deepEqual(cheia.t.anexos.value.map((a) => a.arquivo.name), ['f1.jpg'])
    // A caixa na Nota interna: o porquê.
    const nota = montar({ envio: { max_anexos: 10, numa_mensagem: false }, modo: 'nota' })
    assert.match(nota.t.anexarFotoDaTroca(foto), /não está no Responder/)
    assert.equal(nota.t.anexos.value.length, 0)
  }

  console.log('atendimento-varias-imagens: ok')
}

principal().catch((e) => {
  console.error(e)
  process.exit(1)
})
