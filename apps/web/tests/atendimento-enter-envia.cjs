// node tests/atendimento-enter-envia.cjs — Enter envia, Shift+Enter quebra a
// linha (03/11/2026, o Eduardo: "se eu apertar enter pula a linha ao invés de
// enviar a mensagem no comunicador / enter devia enviar e shift enter pular como
// é o padrão"). Nas três caixas do /atendimento: a resposta da conversa, a da
// aba (resposta por outra conversa) e a Nota interna.
//  - Enter envia (a nota: salva); Shift+Enter quebra a linha; Ctrl/Cmd+Enter
//    também envia; Enter da composição de acento (IME) não envia;
//  - com a lista do "/" aberta, o Enter insere a resposta pronta (o teste do
//    atalho é o atendimento-respostas-prontas.cjs);
//  - caixa vazia ou envio travado: nada sai (o `enviar()`/`salvar()` recusa),
//    nem com fotos/NF anexadas — elas saem pelo botão delas;
//  - no e-mail o Enter é parágrafo: lá envia o Ctrl+Enter;
//  - a dica da caixa diz "Enter envia · Shift+Enter quebra linha".
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { parse, compileTemplate } = require('vue/compiler-sfc')

const transpile = (source) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText
function sfc(rel) {
  const filename = path.resolve(__dirname, rel)
  const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename })
  assert.deepEqual(errors, [], rel)
  const tpl = compileTemplate({ source: descriptor.template.content, filename, id: path.basename(rel), compilerOptions: { isTS: true } })
  assert.deepEqual(tpl.errors, [], `${rel}: template compila`)
  return { setup: descriptor.scriptSetup.content, tpl: descriptor.template.content }
}
// A função inteira (do `function nome(` até o `}` da coluna 0).
function funcao(fonte, inicio) {
  const i = fonte.indexOf(inicio)
  assert.ok(i >= 0, inicio)
  const f = fonte.indexOf('\n}\n', i)
  return fonte.slice(i, f + 2)
}
const tecla = (key, extra = {}) => {
  const e = { key, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, isComposing: false, prevenido: false, ...extra }
  e.preventDefault = () => { e.prevenido = true }
  e.stopPropagation = () => {}
  return e
}
// O mesmo roteiro nas três caixas: `teclar(e)` e quantas vezes enviou.
async function roteiro(nome, { teclar, envios, email = false }) {
  const quebra = tecla('Enter', { shiftKey: true })
  await teclar(quebra)
  assert.equal(quebra.prevenido, false, `${nome}: Shift+Enter quebra a linha`)
  assert.equal(envios(), 0, `${nome}: Shift+Enter não envia`)
  const acento = tecla('Enter', { isComposing: true })
  await teclar(acento)
  assert.equal(envios(), 0, `${nome}: Enter do acento (IME) não envia`)
  const letra = tecla('a')
  await teclar(letra)
  assert.equal(letra.prevenido, false)
  const enter = tecla('Enter')
  await teclar(enter)
  if (email) {
    assert.equal(enter.prevenido, false, `${nome}: no e-mail o Enter é parágrafo`)
    assert.equal(envios(), 0)
  } else {
    assert.equal(enter.prevenido, true, `${nome}: o Enter não vira linha`)
    assert.equal(envios(), 1, `${nome}: Enter envia`)
  }
  await teclar(tecla('Enter', { ctrlKey: true }))
  await teclar(tecla('Enter', { metaKey: true }))
  assert.equal(envios(), email ? 2 : 3, `${nome}: Ctrl/Cmd+Enter envia`)
}

async function principal() {
// ─── a caixa da conversa ────────────────────────────────────────────────────
{
  const { setup, tpl } = sfc('../components/AtendimentoConversa.vue')
  const js = transpile(funcao(setup, 'function aoTeclar(') + /const dicaDoEnvio = [^\n]*\n/.exec(setup)[0]) + '\nreturn { aoTeclar, dicaDoEnvio }'
  for (const email of [false, true]) {
    let n = 0
    const t = new Function('computed', 'atalhoAberto', 'teclaDoAtalho', 'ehEmail', 'enviar', js)(
      Vue.computed, Vue.ref(false), () => false, Vue.ref(email), async () => { n++ },
    )
    await roteiro(email ? 'conversa (e-mail)' : 'conversa', { teclar: t.aoTeclar, envios: () => n, email })
    assert.equal(t.dicaDoEnvio.value, email ? 'Ctrl+Enter envia' : 'Enter envia · Shift+Enter quebra linha')
  }
  // Com a lista do "/" aberta, quem fica com a tecla é ela: nada é enviado.
  {
    let n = 0
    const t = new Function('computed', 'atalhoAberto', 'teclaDoAtalho', 'ehEmail', 'enviar', js)(
      Vue.computed, Vue.ref(true), (e) => { e.preventDefault(); return true }, Vue.ref(false), async () => { n++ },
    )
    t.aoTeclar(tecla('Enter'))
    t.aoTeclar(tecla('Enter', { ctrlKey: true }))
    assert.equal(n, 0, 'o Enter com a lista aberta insere, não envia')
  }
  // Caixa vazia ou envio travado: o `enviar()` volta antes da API.
  {
    const enviar = funcao(setup, 'async function enviar(')
    let chamou = 0
    const f = new Function('detalhe', 'podeEnviar', 'lacunas', 'erroEnvio', 'api', `${transpile(enviar)}\nreturn enviar`)(
      Vue.ref({ conversa: { id: 'c1' }, mensagens: [] }), Vue.ref(false), Vue.ref([]), Vue.ref(null), async () => { chamou++ },
    )
    f()
    assert.equal(chamou, 0)
    assert.match(setup, /const podeEnviar = computed\(\(\) =>\n\s+podeDigitar\.value && !!tamanho\.value && /, 'vazia (tamanho 0) ou travada (podeDigitar) não envia')
    // As fotos e a NF anexadas não saem pelo Enter (têm o botão delas).
    assert.doesNotMatch(funcao(setup, 'function aoTeclar('), /enviarFoto/)
  }
  // 13/11/2026: as respostas prontas saíram da tela — a dica não fala mais do "/".
  assert.match(tpl, /`Escreva a resposta… \(\$\{dicaDoEnvio\}\)`/)
  assert.match(tpl, /`enviar \(\$\{ehEmail \? 'Ctrl\+Enter' : 'Enter'\}\)`/)
  assert.doesNotMatch(tpl, /\(Ctrl\+Enter envia\)/)
}

// ─── a caixa da aba (resposta por outra conversa) ───────────────────────────
{
  const { setup, tpl } = sfc('../components/AtendimentoAbaResposta.vue')
  const js = transpile(funcao(setup, 'function aoTeclar(')) + '\nreturn aoTeclar'
  for (const email of [false, true]) {
    let n = 0
    const aoTeclar = new Function('ehEmail', 'enviar', js)(Vue.ref(email), async () => { n++ })
    await roteiro(email ? 'aba (e-mail)' : 'aba', { teclar: aoTeclar, envios: () => n, email })
  }
  assert.match(setup, /const podeEnviar = computed\(\(\) => !bloqueio\.value && !!tamanho\.value && /)
  assert.match(funcao(setup, 'async function enviar('), /if \(!id \|\| !r \|\| !podeEnviar\.value\) return/)
  assert.match(tpl, /'Enter envia · Shift\+Enter quebra linha'/)
}

// ─── a Nota interna ─────────────────────────────────────────────────────────
{
  const { setup, tpl } = sfc('../components/AtendimentoNota.vue')
  const ini = setup.indexOf('const podeSalvar = computed')
  assert.ok(ini > 0)
  const js = transpile(setup.slice(ini)) + '\nreturn { aoTeclar, texto }'
  function montar(inicial) {
    const salvas = []
    const texto = Vue.ref(inicial)
    const t = new Function('computed', 'props', 'texto', 'salvando', 'NOTA_MAX', 'api', 'rascunhosNota', 'emit', 'toasts', 'erroDaApi', js)(
      Vue.computed, { canEdit: true, conversaId: 'c1' }, texto, Vue.ref(false), 2000,
      async (url, opts) => { salvas.push(opts.body.texto); return { mensagem: null } },
      new Map(), () => {}, { success() {}, error() {} }, (e, b) => ({ texto: b, motivos: [] }),
    )
    return { t, salvas, texto }
  }
  const m = montar('Cliente pediu troca')
  let n = 0
  await roteiro('nota', {
    teclar: async (e) => {
      m.t.aoTeclar(e)
      await new Promise((r) => setTimeout(r, 0))
      n = m.salvas.length
      if (!m.texto.value) m.texto.value = 'de novo'
    },
    envios: () => n,
  })
  // Vazia: o Enter não salva nada.
  const vazia = montar('   ')
  vazia.t.aoTeclar(tecla('Enter'))
  assert.deepEqual(vazia.salvas, [])
  assert.match(tpl, /Escreva a nota para a equipe… \(Enter salva · Shift\+Enter quebra linha\)/)
}

}

principal()
  .then(() => console.log('atendimento-enter-envia: ok'))
  .catch((e) => {
    console.error(e)
    process.exit(1)
  })
