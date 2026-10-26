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
// VISTO NO DAVINCI (15/10/2026, o dono: os números da Caixa batem com o Duoke, e lá abrir a
// conversa zera o número dela): o MESMO gatilho chama, para quem mexe, o POST /visto quando o
// detalhe NÃO diz `marcar_lida` — inclusive com a loja em Observar —, uma chamada por abertura
// (`rotaAoAbrir`: /lida ou /visto). O /visto só grava `visto_em` (nunca vai à plataforma); a
// linha recebe o `visto_em` e a página relê os números. O POST leva `{ ate }` = o `visto_ate`
// do detalhe (até onde a tela mostrou) e, COM A CONVERSA ABERTA, cada releitura que traz
// mensagem nova (o `visto_ate` andou) chama o /visto de novo (`rever`, a revisão de 15/10/2026:
// no Duoke a conversa aberta fica lida) — o /lida continua uma vez por abertura.
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
  // Zap (26/10/2026, o tique azul): sem contador de não lidas — vale o `marcar_lida` do servidor.
  assert.equal(L.querMarcarLida({ conversa: { id: 'z', nao_lidas: 0, canal: 'zap' }, marcar_lida: true }, true), true)
  assert.equal(L.querMarcarLida({ conversa: { id: 'z', nao_lidas: 0, canal: 'zap' }, marcar_lida: false }, true), false)
  assert.equal(L.querMarcarLida({ conversa: { id: 'z', nao_lidas: 0, canal: 'zap' }, marcar_lida: true }, false), false)
  assert.equal(L.querMarcarLida(null, true), false)
  // O visto (15/10/2026): quem mexe, em qualquer modo — só não o Direct (`ig:`) e quem só lê.
  assert.equal(L.querMarcarVisto(det('a', { marcar_lida: false }), true), true)
  assert.equal(L.querMarcarVisto(det('a'), false), false)
  assert.equal(L.querMarcarVisto(det('ig:1', { marcar_lida: false }), true), false)
  assert.equal(L.querMarcarVisto(null, true), false)
  // Uma rota por abertura: o /lida (que grava o visto também) ou o /visto.
  assert.equal(L.rotaAoAbrir(det('a'), true), 'lida')
  assert.equal(L.rotaAoAbrir(det('a', { marcar_lida: false }), true), 'visto', 'Observar: só o visto')
  assert.equal(L.rotaAoAbrir({ conversa: { id: 'a', nao_lidas: 0 }, marcar_lida: true }, true), 'visto', 'já lida: só o visto')
  assert.equal(L.rotaAoAbrir(det('a'), false), null, 'quem só lê: nada')
  assert.equal(L.rotaAoAbrir(det('ig:1', { marcar_lida: false }), true), null)
}

function marcador(extra = {}) {
  const rel = relogioFalso()
  const chamadas = []
  const revistas = []
  const estado = { atual: null, visivel: true, falhar: false, falharRever: false }
  const m = L.criarMarcadorDeLida({
    marcar: async (id) => {
      chamadas.push(id)
      if (estado.falhar) throw new Error('409')
    },
    rever: async (id) => {
      revistas.push(id)
      if (estado.falharRever) throw new Error('500')
    },
    atual: () => estado.atual,
    visivel: () => estado.visivel,
    relogio: rel,
    ...extra,
  })
  const abrir = (id, quer = true, ate, geracao) => {
    estado.atual = id
    m.abrir(id, quer, ate, geracao)
  }
  return { m, rel, chamadas, revistas, estado, abrir }
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
    await tique()
    abrir('c1')
    rel.avancar(1500)
    await tique()
    assert.deepEqual(chamadas, ['c1', 'c2', 'c1'])
  }

  // O timer venceu, mas antes de executar o POST a base de leitura mudou.
  // Não se confirma uma geração nova usando o disparo da anterior.
  {
    const { rel, chamadas, abrir } = marcador()
    abrir('c1', true, 'A', 'antes')
    rel.avancar(1500)
    abrir('c1', true, 'A', 'depois')
    await tique()
    assert.deepEqual(chamadas, [])
    rel.avancar(1500)
    await tique()
    assert.deepEqual(chamadas, ['c1'])
  }

  // A revisão foi agendada, mas a pessoa já saiu da conversa quando sua
  // microtask executa. Não pode usar o detalhe/token da próxima conversa.
  {
    const { m, rel, chamadas, revistas, estado, abrir } = marcador()
    abrir('c1', true, 'A', 'base')
    rel.avancar(1500)
    await tique()
    abrir('c1', true, 'B', 'base')
    estado.atual = 'c2'
    m.fechar()
    await tique()
    assert.deepEqual(chamadas, ['c1'])
    assert.deepEqual(revistas, [])
  }

  // Sair e voltar para o mesmo ID/geração também é outra abertura. O
  // disparo anterior não pode confirmar o detalhe que acabou de aparecer.
  {
    const { rel, chamadas, abrir } = marcador()
    abrir('c1', true, 'A', 'base')
    rel.avancar(1500)
    abrir('c2', true, 'B', 'base')
    abrir('c1', true, 'C', 'base')
    await tique()
    assert.deepEqual(chamadas, [])
    rel.avancar(1500)
    await tique()
    assert.deepEqual(chamadas, ['c1'])
  }

  // Mesmo nessa janela curta, aba escondida não confirma leitura. Ao
  // retornar, a abertura continua apta a tentar novamente.
  {
    const { m, rel, chamadas, estado, abrir } = marcador()
    abrir('c1', true, 'A')
    rel.avancar(1500)
    estado.visivel = false
    await tique()
    assert.deepEqual(chamadas, [])
    estado.visivel = true
    m.retomar()
    rel.avancar(1500)
    await tique()
    assert.deepEqual(chamadas, ['c1'])
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

  // COM A CONVERSA ABERTA (15/10/2026, a revisão): a releitura que traz mensagem nova (o
  // `visto_ate` andou) grava o visto de novo — só o `rever` (o /visto), nunca outro `marcar`.
  {
    const { m, rel, chamadas, revistas, estado, abrir } = marcador()
    abrir('c1', true, 'A')
    // Antes da abertura contar (1,5 s), a mensagem nova vai junto no primeiro POST.
    rel.avancar(700)
    abrir('c1', true, 'B')
    assert.deepEqual(revistas, [])
    rel.avancar(800)
    await tique()
    assert.deepEqual(chamadas, ['c1'])
    assert.equal(m.estado().ateGravado, 'B')
    // A releitura sem nada novo: nada.
    abrir('c1', true, 'B')
    await tique()
    assert.deepEqual(revistas, [])
    // Mensagem nova na tela: o /visto de novo, na hora (a pessoa está vendo).
    abrir('c1', true, 'C')
    await tique()
    assert.deepEqual(revistas, ['c1'])
    abrir('c1', true, 'C')
    abrir('c1', true, 'D')
    await tique()
    assert.deepEqual(revistas, ['c1', 'c1'])
    assert.deepEqual(chamadas, ['c1'], 'o /lida continua uma vez por abertura')
    // Aba escondida: a mensagem nova espera a volta da aba.
    estado.visivel = false
    abrir('c1', true, 'E')
    await tique()
    assert.equal(revistas.length, 2)
    estado.visivel = true
    m.retomar()
    await tique()
    assert.equal(revistas.length, 3)
    // Falhou: a próxima releitura tenta de novo (com o mesmo `visto_ate`).
    estado.falharRever = true
    abrir('c1', true, 'F')
    await tique()
    await tique()
    assert.equal(revistas.length, 4)
    estado.falharRever = false
    abrir('c1', true, 'F')
    await tique()
    assert.equal(revistas.length, 5)
    abrir('c1', true, 'F')
    await tique()
    assert.equal(revistas.length, 5)
    // Outra conversa: começa do zero (a abertura dela, depois do atraso).
    abrir('c2', true, 'G')
    await tique()
    assert.equal(revistas.length, 5)
    rel.avancar(1500)
    await tique()
    assert.deepEqual(chamadas, ['c1', 'c2'])
  }

  // A demonstração da revisão (rev-visto-aberta.cjs): aberta, 2 releituras com mensagem nova
  // → 1 POST da abertura + 2 do `rever` (antes: 1 só, e as 2 mensagens vistas contavam).
  {
    const { rel, chamadas, revistas, abrir } = marcador()
    const det2 = (n, ate) => ({ conversa: { id: 'c1', nao_lidas: n }, marcar_lida: false, visto_ate: ate })
    const quer = (d) => L.rotaAoAbrir(d, true) !== null
    abrir('c1', quer(det2(0, 't0')), 't0')
    rel.avancar(1500)
    await tique()
    rel.avancar(15_000)
    abrir('c1', quer(det2(2, 't1')), 't1')
    rel.avancar(30_000)
    abrir('c1', quer(det2(3, 't2')), 't2')
    await tique()
    assert.equal(chamadas.length + revistas.length, 3)
  }

  // Quem só lê (`quer` falso): nem a abertura, nem o `rever`.
  {
    const { rel, chamadas, revistas, abrir } = marcador()
    abrir('c1', false, 'A')
    rel.avancar(5000)
    abrir('c1', false, 'B')
    await tique()
    assert.deepEqual([chamadas, revistas], [[], []])
  }

  // ───────────────────────── a tela: o bloco do AtendimentoConversa rodando de verdade
  const conversaSfc = sfc('../components/AtendimentoConversa.vue')
  const setup = conversaSfc.descriptor.scriptSetup.content
  assert.match(setup, /^import \{ criarMarcadorDeLida, rotaAoAbrir \} from '~\/lib\/atendimentoLida'$/m)
  const ini = setup.indexOf('// ─── marcar como lida ao abrir (14/10/2026)')
  const fim = setup.indexOf('// ─── abas (RF2)', ini)
  assert.ok(ini > 0 && fim > ini, 'o bloco da tela existe')
  const bloco = setup.slice(ini, fim)
  // Quem só lê: o `canEdit` (mexe + edit), o mesmo das outras ações de mexer.
  assert.match(bloco, /rotaAoAbrir\(d, props\.canEdit\) !== null/)
  assert.match(bloco, /rotaAoAbrir\(aberto, props\.canEdit\)/)
  assert.match(bloco, /`\/api\/atendimento\/conversas\/\$\{encodeURIComponent\(id\)\}\/\$\{rota\}`, \{ method: 'POST', body \}/)
  // A releitura com mensagem nova: só o /visto.
  assert.match(bloco, /rever: \(id\) => gravarVisto\(id, 'visto'\)/)
  assert.match(bloco, /marcadorLida\.abrir\(d\.conversa\.id, rotaAoAbrir\(d, props\.canEdit\) !== null, d\.visto_ate \?\? null, d\.conversa\.notificacoes_migracao_id\)/)

  async function tela({ canEdit = true, marcar_lida = true, respostaFalha = false, naoMarcou = false, adiarRespostas = false } = {}) {
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
    const VISTO = '2026-10-15T10:00:00+00:00'
    const corpos = []
    const responder = []
    const apiFalsa = async (url, opts) => {
      posts.push([url, opts && opts.method])
      corpos.push(opts && opts.body)
      if (adiarRespostas) return new Promise((resolve) => responder.push(resolve))
      if (respostaFalha) throw new Error('500')
      const id = url.split('/')[4]
      const nativo = opts?.body?.notificacoes_migracao_id ? {
        notificacoes_migracao_id: opts.body.notificacoes_migracao_id,
        notificacoes_visto_em: VISTO, numero_duoke: 0,
      } : {}
      if (url.endsWith('/visto')) return { visto_em: VISTO, conversa: { id, nao_lidas: 2, plataforma: 'shopee', visto_em: VISTO, ...nativo } }
      // A plataforma recusou: o /lida responde 200 sem marcar, com o visto gravado.
      if (naoMarcou) return { marcada: false, motivo: 'plataforma_falhou', aviso: 'x', conversa: { id, nao_lidas: 2, plataforma: 'shopee', visto_em: VISTO } }
      return { marcada: true, motivo: null, aviso: null, conversa: { id, nao_lidas: 0, plataforma: 'shopee', visto_em: VISTO, ...nativo } }
    }
    const js = transpile(bloco.replace(/^type LidaResposta = .*$/m, ''))
    const rodar = new Function(
      'criarMarcadorDeLida', 'rotaAoAbrir', 'props', 'detalhe', 'api', 'emit', 'watch', 'onMounted', 'onBeforeUnmount', 'document',
      js,
    )
    rodar(Lt.criarMarcadorDeLida, Lt.rotaAoAbrir, props, detalhe, apiFalsa, (e, c) => emitidos.push([e, c]), Vue.watch,
      (f) => montar.push(f), (f) => desmontar.push(f), doc)
    montar.forEach((f) => f())
    const chegar = async (id, nao = 2, visto_ate = null, conversa = {}) => {
      detalhe.value = { conversa: { id, nao_lidas: nao, plataforma: 'shopee', ...conversa }, mensagens: [], marcar_lida, visto_ate }
      await Vue.nextTick()
    }
    return { rel, posts, corpos, emitidos, props, detalhe, doc, chegar, desmontar, responder }
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
    assert.deepEqual(t.emitidos.map(([e, c]) => [e, c.id, c.nao_lidas, c.visto_em]), [['mudou', 'c1', 0, '2026-10-15T10:00:00+00:00']])
    // O polling relê o detalhe (até com não lidas de novo): não chama outra vez.
    await t.chegar('c1', 1)
    t.rel.avancar(60_000)
    await tique()
    assert.equal(t.posts.length, 1)
  }

  // Loja em Observar (o servidor diz `marcar_lida: false`): nunca o /lida — o /visto
  // (15/10/2026), depois do mesmo atraso, uma vez por abertura; a linha recebe o visto
  // (a página relê os números) e as não lidas da plataforma ficam como estão.
  {
    const t = await tela({ marcar_lida: false })
    await t.chegar('c1')
    t.rel.avancar(1499)
    await tique()
    assert.deepEqual(t.posts, [])
    t.rel.avancar(1)
    await tique()
    await tique()
    assert.deepEqual(t.posts, [['/api/atendimento/conversas/c1/visto', 'POST']])
    assert.equal(t.detalhe.value.conversa.nao_lidas, 2)
    assert.deepEqual(t.emitidos.map(([e, c]) => [e, c.id, c.nao_lidas, c.visto_em]), [['mudou', 'c1', 2, '2026-10-15T10:00:00+00:00']])
    await t.chegar('c1')
    t.rel.avancar(60_000)
    await tique()
    assert.equal(t.posts.length, 1, 'uma vez por abertura')
  }

  // Até onde a tela mostrou vai no POST (`{ ate }`) e, com a conversa aberta, a releitura que
  // traz mensagem nova chama o /visto de novo — em Observar e na loja em que o DaVinci responde
  // (aí a abertura foi o /lida; a releitura, só o /visto).
  for (const marcar_lida of [false, true]) {
    const t = await tela({ marcar_lida })
    const A = '2026-10-15T10:00:00.100000Z'
    const B = '2026-10-15T10:01:10.200000Z'
    await t.chegar('c1', 2, A)
    t.rel.avancar(1500)
    await tique()
    await tique()
    const primeira = marcar_lida ? '/api/atendimento/conversas/c1/lida' : '/api/atendimento/conversas/c1/visto'
    assert.deepEqual(t.posts.map(([u]) => u), [primeira])
    assert.deepEqual(t.corpos, [{ ate: A }])
    // O polling (15 s) relê: nada novo — nada.
    await t.chegar('c1', 2, A)
    await tique()
    assert.equal(t.posts.length, 1)
    // Mensagem nova na tela: o /visto com o novo `ate`, sem esperar.
    await t.chegar('c1', 3, B)
    await tique()
    await tique()
    assert.deepEqual(t.posts.map(([u]) => u), [primeira, '/api/atendimento/conversas/c1/visto'])
    assert.deepEqual(t.corpos, [{ ate: A }, { ate: B }])
    assert.equal(t.emitidos.length, 2, 'a linha recebe o visto (a página relê os números)')
  }

  // O visto não responde pelo cliente nem interfere no atendimento. O mesmo
  // fluxo funciona com a loja em Observar ou com envio habilitado: os campos
  // da fila, do rascunho e da pausa da IA continuam como vieram do servidor.
  for (const marcar_lida of [false, true]) {
    const t = await tela({ marcar_lida })
    const fila = {
      aguardando_resposta: true, falta_responder: true, tem_rascunho: true,
      ia_pausada: true, sem_resposta_necessaria: false,
      humano: { motivos: ['ia_pausada'], rotulos: ['IA pausada'], assuntos: [], assuntos_rotulos: [] },
    }
    const A = '2026-10-19T13:00:00.100000Z'
    const B = '2026-10-19T13:01:00.200000Z'
    await t.chegar('c1', 2, A, fila)
    t.rel.avancar(1500)
    await tique()
    await tique()
    for (const campo of Object.keys(fila)) {
      assert.deepEqual(t.detalhe.value.conversa[campo], fila[campo], `abrir preserva ${campo} (lida=${marcar_lida})`)
    }

    // A mensagem nova chegou enquanto outra aba estava à frente: a pessoa
    // ainda não a viu, então o cursor de chegada não pode avançar sozinho.
    t.doc.visibilityState = 'hidden'
    await t.chegar('c1', 3, B, { ...fila, numero_duoke: 1 })
    await tique()
    assert.equal(t.posts.length, 1)
    assert.equal(t.detalhe.value.conversa.numero_duoke, 1)
    assert.deepEqual(t.corpos, [{ ate: A }])
    t.doc.visibilityState = 'visible'
    t.doc.ouvintes.forEach((f) => f())
    await tique()
    await tique()
    assert.deepEqual(t.corpos, [{ ate: A }, { ate: B }], 'a volta visível marca só a chegada que a tela mostra')
    for (const campo of Object.keys(fila)) {
      assert.deepEqual(t.emitidos.at(-1)[1][campo], fila[campo], `rever preserva ${campo} (lida=${marcar_lida})`)
    }
  }

  // Respostas fora de ordem: a abertura exibia A, o polling trouxe B e o
  // /visto de B voltou primeiro. O retorno lento de A não pode recolocar
  // não lidas antigas, regredir o visto ou apagar a fila/estado atual da IA.
  for (const marcar_lida of [false, true]) {
    const t = await tela({ marcar_lida, adiarRespostas: true })
    const A = '2026-10-19T13:00:00.100000Z'
    const B = '2026-10-19T13:01:00.200000Z'
    await t.chegar('c1', 2, A)
    t.rel.avancar(1500)
    await tique()
    const atual = {
      numero_duoke: 0, visto_em: B, aguardando_resposta: true,
      falta_responder: true, tem_rascunho: true, ia_pausada: true,
      humano: { motivos: ['ia_pausada'], rotulos: ['IA pausada'], assuntos: [], assuntos_rotulos: [] },
    }
    await t.chegar('c1', 3, B, atual)
    await tique()
    assert.deepEqual(t.corpos, [{ ate: A }, { ate: B }])
    t.responder[1]({ conversa: { id: 'c1', nao_lidas: 3, plataforma: 'shopee', ...atual } })
    await tique()
    await tique()
    assert.equal(t.emitidos.length, 1)
    const depoisDeB = { ...t.detalhe.value.conversa }
    t.responder[0]({
      marcada: marcar_lida,
      conversa: {
        id: 'c1', nao_lidas: 0, plataforma: 'shopee', visto_em: A,
        numero_duoke: 2, aguardando_resposta: false, falta_responder: false,
        tem_rascunho: false, ia_pausada: false, humano: null,
      },
    })
    await tique()
    await tique()
    assert.deepEqual(t.detalhe.value.conversa, depoisDeB)
    assert.equal(t.emitidos.length, 1, 'o retorno antigo não publica outro estado da conversa')
  }

  // Uma importação muda a geração mesmo sem chegar outra mensagem. O
  // detalhe já aberto precisa confirmar a leitura dessa nova base.
  for (const marcar_lida of [false, true]) {
    const t = await tela({ marcar_lida })
    const A = '2026-10-19T13:00:00.100000Z'
    const migracao = '9269dc7b-6114-4fd9-9eca-e958b2e0afc4'
    await t.chegar('c1', 2, A)
    t.rel.avancar(1500)
    await tique()
    await tique()
    await t.chegar('c1', 2, A, { notificacoes_migracao_id: migracao, numero_duoke: 5 })
    assert.equal(t.posts.length, 1, 'a base nova respeita o tempo de abertura')
    t.rel.avancar(1500)
    await tique()
    await tique()
    assert.deepEqual(t.corpos, [{ ate: A }, { ate: A, notificacoes_migracao_id: migracao }])
    assert.equal(t.detalhe.value.conversa.numero_duoke, 0)
    assert.equal(t.detalhe.value.conversa.notificacoes_migracao_id, migracao)
  }

  // Um POST legado pendente quando a base é importada não pode apagar os
  // avisos dela, ainda que visto_ate seja exatamente o mesmo.
  {
    const t = await tela({ marcar_lida: false, adiarRespostas: true })
    const A = '2026-10-19T13:00:00.100000Z'
    const migracao = '9269dc7b-6114-4fd9-9eca-e958b2e0afc4'
    await t.chegar('c1', 2, A)
    t.rel.avancar(1500)
    await tique()
    await t.chegar('c1', 2, A, { notificacoes_migracao_id: migracao, numero_duoke: 5 })
    t.responder[0]({ conversa: { id: 'c1', nao_lidas: 0, numero_duoke: 0, visto_em: A } })
    await tique()
    await tique()
    assert.equal(t.detalhe.value.conversa.numero_duoke, 5)
    assert.equal(t.detalhe.value.conversa.notificacoes_migracao_id, migracao)
    assert.equal(t.emitidos.length, 0)
    t.rel.avancar(1500)
    await tique()
    assert.deepEqual(t.corpos[1], { ate: A, notificacoes_migracao_id: migracao })
    t.responder[1]({ conversa: {
      id: 'c1', nao_lidas: 2, numero_duoke: 0, visto_em: A,
      notificacoes_migracao_id: migracao, notificacoes_visto_em: A,
    } })
    await tique()
    await tique()
    assert.equal(t.detalhe.value.conversa.numero_duoke, 0)
    assert.equal(t.emitidos.length, 1)
  }

  // Quem só lê, com mensagem nova na conversa aberta: nada.
  {
    const t = await tela({ canEdit: false })
    await t.chegar('c1', 2, 'A')
    t.rel.avancar(5000)
    await t.chegar('c1', 3, 'B')
    await tique()
    assert.deepEqual(t.posts, [])
  }

  // O /lida que não marcou na plataforma (falhou): o visto chega à linha, as não lidas ficam.
  {
    const t = await tela({ naoMarcou: true })
    await t.chegar('c1')
    t.rel.avancar(1500)
    await tique()
    await tique()
    assert.deepEqual(t.posts.map(([u]) => u), ['/api/atendimento/conversas/c1/lida'])
    assert.equal(t.detalhe.value.conversa.nao_lidas, 2)
    assert.deepEqual(t.emitidos.map(([e, c]) => [e, c.nao_lidas, c.visto_em]), [['mudou', 2, '2026-10-15T10:00:00+00:00']])
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
    // O /visto (15/10/2026): a rota leve, a mesma trava; o /lida grava o visto antes da plataforma.
    assert.match(rota, /@router\.post\("\/conversas\/\{conversa_id\}\/visto", response_model=VistoOut\)/)
    assert.match(rota, /await lida_svc\.gravar_visto\([\s\S]*?body\.ate if body else None,[\s\S]*?notificacoes_migracao_id=[\s\S]*?\n {8}\)\n {8}r = await lida_svc\.marcar_como_lida\(session, c, user=user\)/)
    assert.match(rota, /notificacoes_migracao_id: UUID \| None = None/)
    // O `{ ate }` (opcional) nos dois; o detalhe manda o `visto_ate`.
    assert.equal((rota.match(/^ {4}body: VistoIn \| None = None,$/gm) || []).length, 2)
    assert.match(rotaDetalhe, /"visto_ate": lida_svc\.visto_ate\(mensagens\)/)
    assert.match(schemas, /^ {4}visto_ate: datetime \| None = None$/m)
    assert.match(P, /^ {2}visto_ate\?: string \| null$/m, 'o tipo do detalhe tem o `visto_ate`')
    assert.match(schemas, /^ {4}visto_em: datetime \| None = None$/m)
    assert.match(P, /^ {2}visto_em\?: string \| null$/m, 'o tipo da linha tem o `visto_em`')
    assert.match(rota, /dependencies=\[Depends\(_so_admin\)\]/)
    // Quem só lê NÃO tem esta escrita (fica fora do ROTAS_DE_QUEM_LE → 403).
    const lista = rotaDetalhe.slice(rotaDetalhe.indexOf('ROTAS_DE_QUEM_LE = frozenset('), rotaDetalhe.indexOf('SO_LEITURA = {'))
    assert.ok(lista.length > 0)
    assert.doesNotMatch(lista, /\/lida/)
    assert.doesNotMatch(lista, /\/visto/, 'quem só lê não grava o visto (403)')
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
