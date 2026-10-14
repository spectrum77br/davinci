// node tests/atendimento-marcar-lida.cjs — marcar a conversa como LIDA ao abrir (14/10/2026).
// O Eduardo: "ao passar também tem que marcar como visto, igual lá no Duoke quando clicar na
// conversa". A tela chama POST /api/atendimento/conversas/{id}/lida quando:
//  - o detalhe diz `marcar_lida: true` (o servidor: quem vê mexe, a loja num modo em que o DaVinci
//    responde — nunca Observar —, o envio ligado, plataforma com "lida" e há não lidas);
//  - quem vê pode mexer (`canEdit`; quem só lê nunca chama);
//  - a conversa ficou ABERTA 1,5 s com a aba visível (quem passou rápido pela lista não marca);
//  - uma vez por abertura (o polling de 15 s e uma falha não fazem chamar de novo).
// A regra do servidor está em apps/api/app/services/atendimento/lida.py, testada em
// apps/api/tests/test_atendimento_lida.py.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { parse, compileTemplate } = require('vue/compiler-sfc')

const transpile = (source) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText

// Um relógio de mentira: `avancar(ms)` dispara o que venceu, na ordem.
function relogioFalso() {
  let agora = 0
  let seq = 0
  const fila = new Map()
  return {
    agendar(fn, ms) {
      const id = ++seq
      fila.set(id, { quando: agora + ms, fn })
      return id
    },
    cancelar(id) {
      fila.delete(id)
    },
    avancar(ms) {
      const ate = agora + ms
      for (;;) {
        const vencidos = [...fila.entries()].filter(([, t]) => t.quando <= ate).sort((a, b) => a[1].quando - b[1].quando)
        if (!vencidos.length) break
        const [id, t] = vencidos[0]
        fila.delete(id)
        agora = t.quando
        t.fn()
      }
      agora = ate
    },
    pendentes: () => fila.size,
  }
}

function loadLib(rel, globais = {}) {
  const exp = {}
  const nomes = Object.keys(globais)
  new Function('exports', 'require', ...nomes, transpile(fs.readFileSync(path.join(__dirname, rel), 'utf8')))(
    exp, require, ...nomes.map((n) => globais[n]),
  )
  return exp
}
function sfc(rel) {
  const filename = path.resolve(__dirname, rel)
  const fonte = fs.readFileSync(filename, 'utf8')
  const { descriptor, errors } = parse(fonte, { filename })
  assert.deepEqual(errors, [], rel)
  if (descriptor.template) {
    const tpl = compileTemplate({ source: descriptor.template.content, filename, id: path.basename(rel), compilerOptions: { isTS: true } })
    assert.deepEqual(tpl.errors, [], `${rel}: template compila`)
  }
  return { descriptor, fonte }
}
const api = (rel) => fs.readFileSync(path.resolve(__dirname, '../../api/app', rel), 'utf8')
const tique = () => new Promise((r) => setImmediate(r))

const L = loadLib('../lib/atendimentoLida.ts')
function det(id, extra = {}) {
  return { conversa: { id, nao_lidas: 2 }, marcar_lida: true, ...extra }
}

// ───────────────────────── a regra de quando chamar (pura)
{
  assert.equal(L.ATRASO_MARCAR_LIDA_MS, 1500)
  assert.equal(L.querMarcarLida(det('a'), true), true)
  // Observar (ou envio desligado, ou plataforma sem "lida"): o servidor diz false.
  assert.equal(L.querMarcarLida(det('a', { marcar_lida: false }), true), false)
  assert.equal(L.querMarcarLida(det('a', { marcar_lida: undefined }), true), false)
  // Quem só lê: nunca, mesmo que o detalhe dissesse sim.
  assert.equal(L.querMarcarLida(det('a'), false), false)
  // Nada para marcar: não chama; o número desconhecido (API antiga) deixa o servidor decidir.
  assert.equal(L.querMarcarLida({ conversa: { id: 'a', nao_lidas: 0 }, marcar_lida: true }, true), false)
  assert.equal(L.querMarcarLida({ conversa: { id: 'a' }, marcar_lida: true }, true), true)
  assert.equal(L.querMarcarLida(null, true), false)
}

function marcador(extra = {}) {
  const rel = relogioFalso()
  const chamadas = []
  const estado = { atual: null, visivel: true, falhar: false }
  const m = L.criarMarcadorDeLida({
    marcar: async (id) => {
      chamadas.push(id)
      if (estado.falhar) throw new Error('409')
    },
    atual: () => estado.atual,
    visivel: () => estado.visivel,
    relogio: rel,
    ...extra,
  })
  const abrir = (id, quer = true) => {
    estado.atual = id
    m.abrir(id, quer)
  }
  return { m, rel, chamadas, estado, abrir }
}

// ───────────────────────── chama ao abrir, DEPOIS do atraso
;(async () => {
  {
    const { rel, chamadas, abrir } = marcador()
    abrir('c1')
    rel.avancar(1499)
    assert.deepEqual(chamadas, [], 'antes de 1,5 s, nada')
    rel.avancar(1)
    await tique()
    assert.deepEqual(chamadas, ['c1'], 'com 1,5 s aberta, marca')
  }

  // Passou rápido pela lista com as setas: só a última (a que ficou aberta) marca.
  {
    const { rel, chamadas, abrir } = marcador()
    abrir('a')
    rel.avancar(400)
    abrir('b')
    rel.avancar(400)
    abrir('c')
    rel.avancar(1500)
    await tique()
    assert.deepEqual(chamadas, ['c'])
  }

  // Trocou de conversa ANTES de o detalhe da nova chegar: a anterior não marca.
  {
    const { m, rel, chamadas, estado, abrir } = marcador()
    abrir('a')
    rel.avancar(1000)
    estado.atual = 'b' // a prop mudou; o detalhe de b ainda não chegou
    m.fechar()
    rel.avancar(5000)
    await tique()
    assert.deepEqual(chamadas, [])
    // E mesmo sem o `fechar`, o disparo confere a conversa aberta agora.
    const outro = marcador()
    outro.abrir('a')
    outro.estado.atual = 'b'
    outro.rel.avancar(1500)
    await tique()
    assert.deepEqual(outro.chamadas, [])
  }

  // Observar / quem só lê: `quer` falso → nunca chama.
  {
    const { rel, chamadas, abrir } = marcador()
    abrir('obs', L.querMarcarLida(det('obs', { marcar_lida: false }), true))
    rel.avancar(10_000)
    abrir('leitor', L.querMarcarLida(det('leitor'), false))
    rel.avancar(10_000)
    await tique()
    assert.deepEqual(chamadas, [])
    assert.equal(rel.pendentes(), 0)
  }

  // Não chama de novo: o polling relê o detalhe (abrir de novo, ainda com não lidas) — nada.
  {
    const { rel, chamadas, abrir } = marcador()
    abrir('c1')
    rel.avancar(700)
    abrir('c1') // releitura antes do disparo: não agenda outro
    rel.avancar(800)
    await tique()
    assert.deepEqual(chamadas, ['c1'])
    for (let i = 0; i < 5; i++) {
      abrir('c1')
      rel.avancar(15_000)
    }
    await tique()
    assert.deepEqual(chamadas, ['c1'], 'uma vez por abertura')
    // Fechou e abriu de novo (outra abertura, chegou mensagem nova): marca de novo.
    abrir('c2')
    rel.avancar(1500)
    abrir('c1')
    rel.avancar(1500)
    await tique()
    assert.deepEqual(chamadas, ['c1', 'c2', 'c1'])
  }

  // A falha não quebra nada e não repete na mesma abertura.
  {
    const { rel, chamadas, estado, abrir } = marcador()
    estado.falhar = true
    abrir('c1')
    rel.avancar(1500)
    await tique()
    abrir('c1')
    rel.avancar(30_000)
    await tique()
    assert.deepEqual(chamadas, ['c1'])
  }

  // Aba escondida: não marca; voltou a aparecer, conta o atraso de novo.
  {
    const { m, rel, chamadas, estado, abrir } = marcador()
    estado.visivel = false
    abrir('c1')
    rel.avancar(5000)
    await tique()
    assert.deepEqual(chamadas, [])
    m.retomar() // ainda escondida: nada
    rel.avancar(5000)
    assert.deepEqual(chamadas, [])
    estado.visivel = true
    m.retomar()
    rel.avancar(1499)
    await tique()
    assert.deepEqual(chamadas, [])
    rel.avancar(1)
    await tique()
    assert.deepEqual(chamadas, ['c1'])
  }

  // ───────────────────────── a tela: o bloco do AtendimentoConversa rodando de verdade
  const conversaSfc = sfc('../components/AtendimentoConversa.vue')
  const setup = conversaSfc.descriptor.scriptSetup.content
  assert.match(setup, /^import \{ criarMarcadorDeLida, querMarcarLida \} from '~\/lib\/atendimentoLida'$/m)
  const ini = setup.indexOf('// ─── marcar como lida ao abrir (14/10/2026)')
  const fim = setup.indexOf('// ─── abas (RF2)', ini)
  assert.ok(ini > 0 && fim > ini, 'o bloco da tela existe')
  const bloco = setup.slice(ini, fim)
  // Quem só lê: o `canEdit` (mexe + edit), o mesmo das outras ações de mexer.
  assert.match(bloco, /querMarcarLida\(d, props\.canEdit\)/)
  assert.match(bloco, /`\/api\/atendimento\/conversas\/\$\{encodeURIComponent\(id\)\}\/lida`, \{ method: 'POST' \}/)

  async function tela({ canEdit = true, marcar_lida = true, respostaFalha = false } = {}) {
    const rel = relogioFalso()
    const Lt = loadLib('../lib/atendimentoLida.ts', {
      setTimeout: (fn, ms) => rel.agendar(fn, ms),
      clearTimeout: (t) => rel.cancelar(t),
    })
    const posts = []
    const emitidos = []
    const montar = []
    const desmontar = []
    const doc = { visibilityState: 'visible', ouvintes: [], addEventListener(_e, f) { this.ouvintes.push(f) }, removeEventListener(_e, f) { this.ouvintes = this.ouvintes.filter((x) => x !== f) } }
    const props = Vue.reactive({ conversaId: 'c1', canEdit, ativa: true })
    const detalhe = Vue.ref(null)
    const apiFalsa = async (url, opts) => {
      posts.push([url, opts && opts.method])
      if (respostaFalha) throw new Error('500')
      return { marcada: true, motivo: null, aviso: null, conversa: { id: 'c1', nao_lidas: 0, plataforma: 'shopee' } }
    }
    const js = transpile(bloco.replace(/^type LidaResposta = .*$/m, ''))
    const rodar = new Function(
      'criarMarcadorDeLida', 'querMarcarLida', 'props', 'detalhe', 'api', 'emit', 'watch', 'onMounted', 'onBeforeUnmount', 'document',
      js,
    )
    rodar(Lt.criarMarcadorDeLida, Lt.querMarcarLida, props, detalhe, apiFalsa, (e, c) => emitidos.push([e, c]), Vue.watch,
      (f) => montar.push(f), (f) => desmontar.push(f), doc)
    montar.forEach((f) => f())
    const chegar = async (id, nao = 2) => {
      detalhe.value = { conversa: { id, nao_lidas: nao, plataforma: 'shopee' }, mensagens: [], marcar_lida }
      await Vue.nextTick()
    }
    return { rel, posts, emitidos, props, detalhe, doc, chegar, desmontar }
  }

  // Quem mexe, loja em que o DaVinci responde: marca depois de 1,5 s e zera o número na tela.
  {
    const t = await tela()
    await t.chegar('c1')
    t.rel.avancar(1499)
    await tique()
    assert.deepEqual(t.posts, [])
    t.rel.avancar(1)
    await tique()
    await tique()
    assert.deepEqual(t.posts, [['/api/atendimento/conversas/c1/lida', 'POST']])
    assert.equal(t.detalhe.value.conversa.nao_lidas, 0)
    assert.deepEqual(t.emitidos.map(([e, c]) => [e, c.id, c.nao_lidas]), [['mudou', 'c1', 0]])
    // O polling relê o detalhe (até com não lidas de novo): não chama outra vez.
    await t.chegar('c1', 1)
    t.rel.avancar(60_000)
    await tique()
    assert.equal(t.posts.length, 1)
  }

  // Loja em Observar (o servidor diz `marcar_lida: false`): nunca chama.
  {
    const t = await tela({ marcar_lida: false })
    await t.chegar('c1')
    t.rel.avancar(60_000)
    await tique()
    assert.deepEqual(t.posts, [])
  }

  // Quem só lê: nunca chama.
  {
    const t = await tela({ canEdit: false })
    await t.chegar('c1')
    t.rel.avancar(60_000)
    await tique()
    assert.deepEqual(t.posts, [])
  }

  // Passou rápido: trocou de conversa antes de 1,5 s — a anterior não marca.
  {
    const t = await tela()
    await t.chegar('c1')
    t.rel.avancar(1000)
    t.props.conversaId = 'c2'
    await Vue.nextTick()
    t.detalhe.value = null
    t.rel.avancar(1000)
    await tique()
    assert.deepEqual(t.posts, [])
    await t.chegar('c2')
    t.rel.avancar(1500)
    await tique()
    assert.deepEqual(t.posts.map(([u]) => u), ['/api/atendimento/conversas/c2/lida'])
  }

  // Aba escondida: espera ela voltar (e conta o atraso de novo).
  {
    const t = await tela()
    t.doc.visibilityState = 'hidden'
    await t.chegar('c1')
    t.rel.avancar(10_000)
    await tique()
    assert.deepEqual(t.posts, [])
    t.doc.visibilityState = 'visible'
    t.doc.ouvintes.forEach((f) => f())
    t.rel.avancar(1500)
    await tique()
    assert.equal(t.posts.length, 1)
  }

  // A Caixa atrás de outra aba da página (`ativa` falso): idem.
  {
    const t = await tela()
    t.props.ativa = false
    await Vue.nextTick()
    await t.chegar('c1')
    t.rel.avancar(10_000)
    await tique()
    assert.deepEqual(t.posts, [])
    t.props.ativa = true
    await Vue.nextTick()
    t.rel.avancar(1500)
    await tique()
    assert.equal(t.posts.length, 1)
  }

  // A falha (rede, 409) não quebra a tela, não zera o número e não repete na abertura.
  {
    const t = await tela({ respostaFalha: true })
    await t.chegar('c1')
    t.rel.avancar(1500)
    await tique()
    await tique()
    assert.equal(t.posts.length, 1)
    assert.equal(t.detalhe.value.conversa.nao_lidas, 2)
    assert.deepEqual(t.emitidos, [])
    await t.chegar('c1')
    t.rel.avancar(60_000)
    await tique()
    assert.equal(t.posts.length, 1)
  }

  // Desmontou com o relógio correndo: nada marca depois.
  {
    const t = await tela()
    await t.chegar('c1')
    t.rel.avancar(500)
    t.desmontar.forEach((f) => f())
    t.rel.avancar(10_000)
    await tique()
    assert.deepEqual(t.posts, [])
    assert.deepEqual(t.doc.ouvintes, [])
  }

  // ───────────────────────── o contrato com o backend
  {
    const P = sfc('../components/AtendimentoPlataforma.vue').fonte
    assert.match(P, /^ {2}marcar_lida\?: boolean$/m, 'o tipo do detalhe tem o `marcar_lida`')
    const schemas = api('schemas/atendimento.py')
    assert.match(schemas, /^ {4}marcar_lida: bool = False$/m)
    const rotaDetalhe = api('routers/atendimento.py')
    assert.match(rotaDetalhe, /"marcar_lida": await lida_svc\.marcar_ao_abrir\(session, c, user\)/)
    const rota = api('routers/atendimento_lida.py')
    assert.match(rota, /@router\.post\("\/conversas\/\{conversa_id\}\/lida", response_model=LidaOut\)/)
    assert.match(rota, /dependencies=\[Depends\(_so_admin\)\]/)
    // Quem só lê NÃO tem esta escrita (fica fora do ROTAS_DE_QUEM_LE → 403).
    const lista = rotaDetalhe.slice(rotaDetalhe.indexOf('ROTAS_DE_QUEM_LE = frozenset('), rotaDetalhe.indexOf('SO_LEITURA = {'))
    assert.ok(lista.length > 0)
    assert.doesNotMatch(lista, /\/lida/)
    assert.match(api('main.py'), /app\.include_router\(atendimento_lida_router\.router\)/)
    const servico = api('services/atendimento/lida.py')
    for (const p of ['"shopee": frozenset({CANAL_CHAT})', '"tiktok": frozenset({CANAL_CHAT})', '"ml": frozenset({CANAL_POS_VENDA})']) {
      assert.ok(servico.includes(p), p)
    }
  }

  console.log('atendimento-marcar-lida: ok')
})().catch((e) => {
  console.error(e)
  process.exit(1)
})
