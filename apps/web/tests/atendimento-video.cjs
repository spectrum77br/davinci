// node tests/atendimento-video.cjs — vídeo na resposta do /atendimento
// (19/11/2026, o Eduardo: "tem q deixar enviar vídeo no chat do atendimento
// tbm em todas plataformas q são possíveis"). O mesmo botão "Anexar": onde a
// plataforma aceita vídeo (`envio_foto.video`, o Zap do número), ele entra na
// lista e sai pelo POST /foto, uma mensagem por vídeo; onde não aceita, a
// escolha diz POR QUÊ (`video_motivo`). No balão, o vídeo TOCA.
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
const template = descriptor.template.content

const MB = 1024 * 1024
const TIPOS = ['image/jpeg', 'image/png', 'application/pdf']
const arq = (name, { type = 'video/mp4', size = 3 * MB, lastModified = 1 } = {}) => ({ name, type, size, lastModified })

// ─── é vídeo? (pelo tipo; sem tipo, pela extensão) ──────────────────────────
assert.equal(C.ehVideoArquivo({ name: 'a.mp4', type: 'video/mp4' }), true)
assert.equal(C.ehVideoArquivo({ name: 'IMG_0001.MOV', type: 'video/quicktime' }), true)
assert.equal(C.ehVideoArquivo({ name: 'IMG_0001.MOV', type: '' }), true, 'navegador sem tipo: a extensão')
assert.equal(C.ehVideoArquivo({ name: 'a.jpg', type: 'image/jpeg' }), false)
assert.equal(C.ehVideoArquivo({ name: 'a.pdf', type: '' }), false)

// ─── o que o painel diz → as opções da escolha ─────────────────────────────
assert.deepEqual(C.opcoesDeVideo(null).tipos, [])
assert.match(C.opcoesDeVideo(null).motivo, /Conferindo/)
const SHOPEE = 'Shopee: o vídeo pela API do chat ainda não foi conferido na documentação oficial — por enquanto, mande o vídeo pelo Seller Center (chat da loja).'
assert.deepEqual(C.opcoesDeVideo({ video: false, video_motivo: SHOPEE }), { tipos: [], maxBytes: 0, motivo: SHOPEE })
assert.deepEqual(C.opcoesDeVideo({ video: true, video_tipos: ['video/3gpp', 'video/mp4', 'video/quicktime'], video_max_bytes: 50 * MB }), { tipos: ['video/3gpp', 'video/mp4', 'video/quicktime'], maxBytes: 50 * MB, motivo: null })

// ─── a escolha ──────────────────────────────────────────────────────────────
{
  const video = C.opcoesDeVideo({ video: true, video_tipos: C.TIPOS_VIDEO, video_max_bytes: 50 * MB })
  const op = { max: 10, maxBytes: 10 * MB, tipos: TIPOS, video }
  const r = C.escolherAnexos([], [arq('demo.mp4'), arq('a.jpg', { type: 'image/jpeg', size: 1000 }), arq('b.webm', { type: 'video/webm' }), arq('longo.mov', { type: 'video/quicktime', size: 60 * MB }), arq('c.gif', { type: 'image/gif' })], op)
  // O vídeo passa do teto da FOTO (10 MB) e entra: o teto dele é outro (50 MB).
  assert.deepEqual(r.aceitos.map((f) => f.name), ['demo.mp4', 'a.jpg'])
  assert.deepEqual(r.recusas, [
    'b.webm: só dá para mandar vídeo MP4, MOV ou 3GP.',
    'longo.mov: o vídeo passa de 50 MB — mande um mais curto.',
    'c.gif: só dá para mandar foto JPG ou PNG, PDF ou vídeo (MP4, MOV).',
  ])
  // O mesmo vídeo duas vezes: já está anexado.
  assert.deepEqual(C.escolherAnexos([{ arquivo: arq('demo.mp4') }], [arq('demo.mp4')], op).recusas, ['demo.mp4: já está anexado.'])
}
{
  // Onde o vídeo não sai: a frase da plataforma, e a foto ainda entra.
  const op = { max: 10, maxBytes: 10 * MB, tipos: TIPOS, video: C.opcoesDeVideo({ video: false, video_motivo: SHOPEE }) }
  const r = C.escolherAnexos([], [arq('demo.mp4'), arq('a.jpg', { type: 'image/jpeg', size: 1000 })], op)
  assert.deepEqual(r.aceitos.map((f) => f.name), ['a.jpg'])
  assert.deepEqual(r.recusas, [`demo.mp4: ${SHOPEE}`])
  // Sem as opções de vídeo (outro chamador, como a foto da troca): recusado igual.
  assert.match(C.escolherAnexos([], [arq('demo.mp4')], { max: 1, maxBytes: 10 * MB, tipos: TIPOS }).recusas[0], /o vídeo não sai por esta conversa/)
}
assert.equal(C.rotuloEnviarAnexos([{ pdf: false, video: true }]), 'Enviar vídeo')
assert.equal(C.rotuloEnviarAnexos([{ pdf: false, video: true }, { pdf: false, video: true }]), 'Enviar 2 vídeos')
assert.equal(C.rotuloEnviarAnexos([{ pdf: false, video: true }, { pdf: false, video: false }]), 'Enviar 2 anexos')
assert.equal(C.rotuloEnviarAnexos([{ pdf: false }]), 'Enviar foto')

// ─── o envio, rodando o trecho da tela com a API falsa ──────────────────────
const ini = setup.indexOf('const fotoInput = ref')
const fim = setup.indexOf('// ─── "tentar de novo"')
assert.ok(ini > 0 && fim > ini)
const js = transpile(setup.slice(ini, fim)) + '\nreturn { anexos, adicionarAnexos, enviarFoto, legendaFoto, tituloAnexar, videoAqui }'
globalThis.URL.createObjectURL = () => 'blob:x'
globalThis.URL.revokeObjectURL = () => {}
class FD {
  constructor() { this.campos = [] }
  append(k, v) { this.campos.push([k, typeof v === 'string' ? v : v.name]) }
  todos(k) { return this.campos.filter((c) => c[0] === k).map((c) => c[1]) }
  um(k) { return this.todos(k)[0] }
}
function montar({ envio, respostas = [] }) {
  const chamadas = []
  const toasts = { ok: [], erro: [], aviso: [] }
  const detalhe = Vue.ref({ conversa: { id: 'c1', plataforma: envio.plataforma || 'whatsapp', somente_leitura: false }, mensagens: [{ id: '00000000-0000-4000-8000-000000000001', criada_em: '2026-11-19T10:00:00Z' }] })
  let n = 0
  const api = async (url, opts) => {
    chamadas.push({ url, fd: opts.body })
    const r = respostas[n++] ?? { status: 'enviada' }
    if (r.erro) throw r.erro
    return { mensagem: { id: `00000000-0000-4000-8000-0000000001${String(n).padStart(2, '0')}`, status: r.status, tipo: 'video', erro: r.erroMsg ?? null, criada_em: '2026-11-19T10:01:00Z' } }
  }
  const nomes = ['ref', 'computed', 'onBeforeUnmount', 'envioFoto', 'motivoSemFoto', 'MAX_FOTO', 'TIPOS_FOTO', 'escolherAnexos', 'toasts', 'modoCaixa', 'respondePorOutra', 'canalExterno', 'props', 'detalhe',
    'tituloDaNf', 'painelCarregando', 'LEGENDA_NF', 'erroDaApi', 'api', 'destinosReclamacao', 'destinoCaixa', 'rolarProFim', 'erroEnvioLegivel', 'statusDoErro', 'ultimaVista', 'RE_UUID', 'aberta', 'enviandoIds', 'envioSimulado', 'carregar', 'confirm', 'FormData',
    'provide', 'CHAVE_ANEXAR_NA_CAIXA', 'fimDoEnvioDeAnexos', 'andarFila', 'opcoesDeVideo', 'ehVideoArquivo']
  const valores = [Vue.ref, Vue.computed, () => {}, Vue.ref({ pode: true, max_bytes: 10 * MB, ...envio }), Vue.ref(''), 10 * MB, TIPOS, C.escolherAnexos,
    { success: (t) => toasts.ok.push(t), error: (t, l) => toasts.erro.push([t, l]), warning: (t, l) => toasts.aviso.push([t, l]) },
    Vue.ref('responder'), Vue.ref(false), Vue.ref(false), { canEdit: true, flags: null }, detalhe,
    C.tituloDaNf, Vue.ref(false), C.LEGENDA_NF,
    (e, base) => ({ texto: e?.data?.detail?.detail || base, motivos: [] }),
    api, Vue.ref([]), Vue.ref(''), () => {}, (e) => `recusou: ${e}`, (e) => e?.status ?? null,
    (d) => d.mensagens[d.mensagens.length - 1].id, /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i,
    (id) => id === 'c1', new Set(), () => false, async () => {}, () => true, FD,
    () => {}, Symbol('x'), () => {}, () => {}, C.opcoesDeVideo, C.ehVideoArquivo]
  return { t: new Function(...nomes, js)(...valores), chamadas, toasts }
}

async function principal() {
  // Zap: uma foto e um vídeo → dois POSTs, na ordem; a legenda (opcional) vai
  // com o ÚLTIMO, o vídeo.
  {
    const m = montar({ envio: { max_anexos: 10, numa_mensagem: false, legenda_permitida: true, video: true, video_tipos: C.TIPOS_VIDEO, video_max_bytes: 50 * MB } })
    assert.match(m.t.tituloAnexar.value, /PDF ou vídeo \(MP4, MOV\)/)
    m.t.adicionarAnexos([arq('foto.jpg', { type: 'image/jpeg', size: 1000 }), arq('demo.mp4', { size: 30 * MB })])
    assert.deepEqual(m.t.anexos.value.map((a) => [a.arquivo.name, a.video]), [['foto.jpg', false], ['demo.mp4', true]])
    m.t.legendaFoto.value = 'Veja como usar'
    await m.t.enviarFoto()
    assert.deepEqual(m.chamadas.map((c) => c.url), ['/api/atendimento/conversas/c1/foto', '/api/atendimento/conversas/c1/foto'])
    assert.deepEqual(m.chamadas.map((c) => c.fd.todos('arquivo')), [['foto.jpg'], ['demo.mp4']])
    assert.deepEqual(m.chamadas.map((c) => c.fd.um('legenda') ?? null), [null, 'Veja como usar'])
    assert.deepEqual(m.toasts.ok, ['2 anexos enviados'])
  }
  // A plataforma não confirmou o vídeo (pode ter saído): para e NÃO reenvia.
  {
    const m = montar({ envio: { max_anexos: 10, numa_mensagem: false, video: true, video_tipos: C.TIPOS_VIDEO, video_max_bytes: 50 * MB }, respostas: [{ status: 'revisar' }] })
    m.t.adicionarAnexos([arq('a.mp4'), arq('b.mp4', { lastModified: 2 })])
    await m.t.enviarFoto()
    assert.equal(m.chamadas.length, 1, 'o incerto para tudo')
    assert.deepEqual(m.t.anexos.value.map((a) => a.estado), ['incerta', 'pendente'])
    assert.match(m.toasts.aviso[0][0], /pode ter saído/)
  }
  // Recusa da plataforma: "falhou" com o porquê; o reenviar é da pessoa.
  {
    const m = montar({ envio: { max_anexos: 10, numa_mensagem: false, video: true, video_tipos: C.TIPOS_VIDEO, video_max_bytes: 50 * MB }, respostas: [{ status: 'falhou', erroMsg: 'meta:131053' }] })
    m.t.adicionarAnexos([arq('a.mp4')])
    await m.t.enviarFoto()
    assert.equal(m.chamadas.length, 1)
    assert.equal(m.t.anexos.value[0].estado, 'falhou')
    assert.match(m.t.anexos.value[0].erro, /meta:131053/)
  }
  // Shopee (foto sim, vídeo não): o vídeo nem entra e a tela diz por quê.
  {
    const m = montar({ envio: { plataforma: 'shopee', max_anexos: 10, numa_mensagem: false, video: false, video_motivo: SHOPEE } })
    assert.match(m.t.tituloAnexar.value, /Vídeo não: Shopee/)
    m.t.adicionarAnexos([arq('demo.mp4')])
    assert.equal(m.t.anexos.value.length, 0)
    assert.deepEqual(m.toasts.erro, [[`demo.mp4: ${SHOPEE}`, undefined]])
    assert.equal(m.chamadas.length, 0)
  }
}

// ─── a tela ─────────────────────────────────────────────────────────────────
// O seletor oferece vídeo sempre (onde não sai, a escolha explica).
assert.match(template, /accept="image\/jpeg,image\/png,application\/pdf,video\/mp4,video\/quicktime,video\/3gpp"/)
assert.match(template, /:title="tituloAnexar"/)
// A prévia do vídeo na lista de anexos (sem som, sem tocar sozinho) e o aviso da conversão.
assert.match(template, /<video :src="a\.previa" muted playsinline preload="metadata"[^>]*data-anexo-video/)
assert.match(template, /data-video-como>O vídeo vai como MP4/)
// O balão: o vídeo da plataforma TOCA (carrega só no play); se o link venceu, volta o link.
assert.match(template, /<video v-else-if="pc\.tipo === 'video' && !fotoFalhou\[pc\.chave\]" :src="pc\.url" controls preload="none" playsinline[^>]*data-midia="video" @error="fotoFalhou\[pc\.chave\] = true"/)
assert.match(setup, /if \(url && \(tipo === 'video' \|\| tipo\.startsWith\('video\/'\)\)\) return \{ chave, tipo: 'video', url, nome \}/)
// "Tentar de novo" (só texto) nunca no vídeo com legenda: mandaria só a legenda.
assert.match(setup, /m\.tipo === 'imagem' \|\| m\.tipo === 'video'\) return false/)

// ─── o contrato com a API ───────────────────────────────────────────────────
const api = (rel) => fs.readFileSync(path.resolve(__dirname, '../../api/app', rel), 'utf8')
const schema = api('schemas/atendimento_painel.py')
for (const campo of ['video: bool = False', 'video_motivo: str | None = None', 'video_tipos: list[str]', 'video_max_bytes: int = 0', 'video_max_s: int = 0']) assert.ok(schema.includes(campo), campo)
const pedido = fs.readFileSync(path.resolve(__dirname, '../components/AtendimentoPedido.vue'), 'utf8')
for (const campo of ['video?: boolean', 'video_motivo?: string | null', 'video_tipos?: string[]', 'video_max_bytes?: number']) assert.ok(pedido.includes(campo), campo)
const modVideo = api('services/atendimento/video.py')
assert.match(modVideo, /^TIPOS_VIDEO: dict\[str, str\] = \{VIDEO_MP4: "\.mp4", VIDEO_MOV: "\.mov", VIDEO_3GP: "\.3gp"\}$/m)
assert.deepEqual(C.TIPOS_VIDEO, ['video/mp4', 'video/quicktime', 'video/3gpp'])

principal().then(() => console.log('atendimento-video: ok'), (e) => { console.error(e); process.exit(1) })
