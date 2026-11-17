// node tests/atendimento-setup-sem-erro.cjs — o <script setup> das telas do Atendimento roda
// INTEIRO, na ordem, sem erro.
// 16/11/2026: a conversa da Caixa não abria — o setup do AtendimentoConversa quebrava com
// "Cannot access 'modoCaixa' before initialization" (o `watch` do balão do "Não precisa" lia um
// computed que lia o `modoCaixa`, declarado ~600 linhas abaixo: a zona morta do `const`). Os
// testes de tela rodam TRECHOS do setup, com o que falta injetado de fora — o `modoCaixa` vinha
// pronto e o erro passou (9f163041 moveu o watch para depois). Aqui o setup roda do começo ao fim,
// com o Vue de verdade (ref, computed, watch — o watch lê a fonte na hora) e todo o resto falso (a
// API, os composables do Nuxt, as libs, os outros componentes):
//  - o detector pega o erro: um componente de mentira com o MESMO defeito tem de falhar, e o
//    consertado tem de passar (sem isso, um Vue que engolisse o erro deixaria tudo verde);
//  - AtendimentoConversa.vue e AtendimentoMetricas.vue sobem sem erro e chegam ao fim do setup.
// O nome que o Nuxt importa sozinho (ref, computed, useApi…) vira, na primeira vez que falta, o do
// Vue (se existir) ou um falso — por isso uma variável que não existe de verdade não é achada
// aqui (é do typecheck). O que roda depois do setup (a API falsa, os timers) não é deste teste.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { parse, compileScript } = require('vue/compiler-sfc')

const transpile = (source) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, esModuleInterop: true },
}).outputText

// O falso de tudo que não é o Vue: chamável, com qualquer campo (que é outro falso), sem `value`
// (o `.value` dele é undefined), iterável vazio e não é promessa (o `await` devolve ele mesmo).
function falso(nome) {
  return new Proxy(function () {}, {
    get(_, k) {
      if (k === Symbol.toPrimitive) return () => ''
      if (k === Symbol.iterator) return function* () {}
      if (k === 'then' || k === 'value') return undefined
      if (k === '__v_isRef') return false
      return falso(`${nome}.${String(k)}`)
    },
    apply: () => falso(`${nome}()`),
    construct: () => falso(`new ${nome}`),
  })
}
const requerer = (m) => (m === 'vue' ? Vue : falso(m))
const META = { client: true, server: false, dev: false, env: {} }

// Roda o setup inteiro → { ok: true, ligacoes } (o que ele devolve: os nomes do template) ou
// { ok: false, erro }. Cada nome que falta (o auto-import do Nuxt) entra e o módulo roda de novo.
function rodarSetup(fonte, nome, props = {}) {
  const { descriptor, errors } = parse(fonte, { filename: nome })
  assert.deepEqual(errors, [], nome)
  const script = compileScript(descriptor, { id: nome, inlineTemplate: false })
  const js = transpile(script.content).replace(/import\.meta/g, '__importMeta')
  const globais = {}
  for (let tentativa = 0; tentativa < 400; tentativa++) {
    const nomes = Object.keys(globais)
    const mod = { exports: {} }
    const avisos = console.warn
    console.warn = () => {} // "onMounted fora de um componente": aqui não há componente montado
    const escopo = Vue.effectScope(true)
    try {
      new Function('module', 'exports', 'require', '__importMeta', ...nomes, js)(
        mod, mod.exports, requerer, META, ...nomes.map((n) => globais[n]),
      )
      const ctx = { emit() {}, expose() {}, attrs: {}, slots: {} }
      const ligacoes = escopo.run(() => mod.exports.default.setup(Vue.shallowReactive({ ...props }), ctx))
      return { ok: true, ligacoes, globais: nomes }
    } catch (erro) {
      const falta = /^(\w+) is not defined$/.exec(erro?.message || '')
      if (erro instanceof ReferenceError && falta && !(falta[1] in globais)) {
        globais[falta[1]] = falta[1] in Vue ? Vue[falta[1]] : falso(falta[1])
        continue
      }
      return { ok: false, erro }
    } finally {
      escopo.stop()
      console.warn = avisos
    }
  }
  throw new Error(`${nome}: nomes demais faltando`)
}

// ------------------------------------------------ o detector pega o defeito de 16/11
{
  const COM_DEFEITO = `<script setup lang="ts">
// O defeito de 16/11/2026 em miniatura: o watch lê o computed, que lê o modoCaixa de baixo.
const balao = ref(false)
const noRodape = computed(() => modoCaixa.value === 'responder')
watch(noRodape, (v) => { if (!v) balao.value = false })
const modoCaixa = ref<'responder' | 'nota'>('responder')
</script>`
  const r = rodarSetup(COM_DEFEITO, 'ComDefeito.vue')
  assert.equal(r.ok, false, 'o detector tem de acusar o setup quebrado')
  assert.ok(r.erro instanceof ReferenceError, String(r.erro))
  assert.match(r.erro.message, /Cannot access 'modoCaixa' before initialization/)

  // O conserto (o ref antes do watch) passa, e o setup chega ao fim.
  const CONSERTADO = COM_DEFEITO.replace(
    "const modoCaixa = ref<'responder' | 'nota'>('responder')\n", '',
  ).replace('const balao = ref(false)', "const modoCaixa = ref<'responder' | 'nota'>('responder')\nconst balao = ref(false)")
  const ok = rodarSetup(CONSERTADO, 'Consertado.vue')
  assert.equal(ok.ok, true, String(ok.erro))
  assert.equal(ok.ligacoes.noRodape.value, true)
  assert.deepEqual(ok.globais.sort(), ['computed', 'ref', 'watch'], 'os auto-imports são os do Vue')

  // Erro de verdade que não é nome faltando também aparece (não vira falso).
  const QUEBRA = `<script setup lang="ts">\nconst x = ref(null)\nconst y = x.value.campo\n</script>`
  const q = rodarSetup(QUEBRA, 'Quebra.vue')
  assert.equal(q.ok, false)
  assert.ok(q.erro instanceof TypeError, String(q.erro))
}

// ------------------------------------------------ as telas de verdade
const tela = (rel) => fs.readFileSync(path.resolve(__dirname, '../components', rel), 'utf8')
{
  // A conversa: as props do pai (pages/atendimento) sem conversa aberta ainda.
  const r = rodarSetup(tela('AtendimentoConversa.vue'), 'AtendimentoConversa.vue', {
    conversaId: null, canEdit: true, canSugerir: true, modelos: [], flags: null, meuId: null, lojas: [],
  })
  assert.equal(r.ok, true, `o setup do AtendimentoConversa quebrou: ${r.erro?.stack || r.erro}`)
  // Chegou ao fim: o que o template usa, inclusive o que vem depois do modoCaixa.
  // 17/11/2026: o `irParaCitada` (o clique no bloco da mensagem citada) também.
  for (const n of ['modoCaixa', 'naoPrecisaNoRodape', 'naoPrecisaComBalao', 'balaoNaoPrecisa', 'detalhe', 'irParaCitada']) {
    assert.ok(n in r.ligacoes, `AtendimentoConversa: ${n} não chegou ao template`)
  }
}
{
  const r = rodarSetup(tela('AtendimentoMetricas.vue'), 'AtendimentoMetricas.vue')
  assert.equal(r.ok, true, `o setup do AtendimentoMetricas quebrou: ${r.erro?.stack || r.erro}`)
  for (const n of ['carregar', 'grupos', 'visaoAdmin', 'lojasTempo', 'totais', 'tempo']) {
    assert.ok(n in r.ligacoes, `AtendimentoMetricas: ${n} não chegou ao template`)
  }
}

{
  // O bloco da mensagem citada (17/11/2026), com uma citada de verdade.
  const r = rodarSetup(tela('AtendimentoCitada.vue'), 'AtendimentoCitada.vue', {
    citada: { externo_id: '700001', mensagem_id: 'm1', autor: 'loja', tipo: 'imagem', texto: null, imagem_url: 'https://img.sp.mms.shopee.sg/x' },
    de: 'm2',
  })
  assert.equal(r.ok, true, `o setup do AtendimentoCitada quebrou: ${r.erro?.stack || r.erro}`)
  for (const n of ['c', 'miniatura', 'clicavel', 'cores', 'ir']) {
    assert.ok(n in r.ligacoes, `AtendimentoCitada: ${n} não chegou ao template`)
  }
}

console.log('ok — atendimento-setup-sem-erro')
// O setup deixa promessas e timers do falso para trás: não são deste teste.
process.exit(0)
