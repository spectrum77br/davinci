// node tests/atendimento-tela-cheia.cjs — a TELA CHEIA do Atendimento
// (03/11/2026). O Eduardo: "um botão pra ampliar no DaVinci pra deixar
// fullscreen só o atendimento, parecido em dimensão com o Duoke". A equipe veio
// do Duoke, que ocupa a tela toda:
//  - "Tela cheia" ao lado do "atualizar" (de notebook para cima) tira o menu
//    lateral, a barra do topo e o título do DaVinci (o layout lê
//    `useTelaCheia`) e as abas viram a barra estreita de ícones da esquerda,
//    com os mesmos números; a Caixa vai do topo ao fim da janela;
//  - entra e sai pelo botão, por Ctrl+Shift+F e (sair) pelo Esc — menos com
//    menu, modal, imagem ou gaveta aberta, ou digitando: aí o Esc é deles;
//  - o navegador lembra (try: sem localStorage, só não lembra); abaixo do `lg`
//    a tela é a de sempre; sair da página devolve o menu às outras;
//  - a tela inteira do navegador (Fullscreen API) é um botão à parte na barra,
//    e sair da tela cheia sai dela também.
// Aqui: o bloco de verdade da página com dublês, a barra de ícones renderizada
// (Vue SSR) e os trechos do template, do layout e do composable.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { renderToString } = require('vue/server-renderer')
const { parse, compileTemplate } = require('vue/compiler-sfc')

const transpile = (source) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText
const web = (rel) => fs.readFileSync(path.resolve(__dirname, '..', rel), 'utf8')

const pagina = web('pages/atendimento.vue')
const { descriptor, errors } = parse(pagina, { filename: 'atendimento.vue' })
assert.deepEqual(errors, [])
const tpl = descriptor.template.content
assert.deepEqual(compileTemplate({ source: tpl, filename: 'atendimento.vue', id: 'atendimento', compilerOptions: { isTS: true } }).errors, [], 'o template compila')
const semComentarios = (html) => html.replace(/<!--[\s\S]*?-->/g, '')
const esperar = () => new Promise((r) => setTimeout(r, 0))

// ------------------------------------------------ o bloco da página, com dublês
const ini = pagina.indexOf('// ─── tela cheia (03/11/2026)')
const fim = pagina.indexOf('</script>', ini)
assert.ok(ini > 0 && fim > ini, 'o bloco da tela cheia fecha o <script>')
const BLOCO = transpile(pagina.slice(ini, fim))

function montar({ guardado = new Map(), quebrado = false, largura = 1366, telaDoNavegador = true } = {}) {
  const montados = []
  const desmontados = []
  const escutas = { window: [], document: [] }
  const medidas = []
  const localStorage = {
    getItem: (k) => { if (quebrado) throw new Error('bloqueado'); return guardado.has(k) ? guardado.get(k) : null },
    setItem: (k, v) => { if (quebrado) throw new Error('bloqueado'); guardado.set(k, String(v)) },
  }
  const documentElement = { nome: 'html' }
  const doc = {
    fullscreenEnabled: telaDoNavegador,
    fullscreenElement: null,
    aberto: null,
    escondido: null,
    documentElement,
    // O que casa com o seletor: `aberto` à mostra; `escondido` no DOM, sem caixa (v-show).
    querySelectorAll(sel) {
      this.ultimoSeletor = sel
      return [this.escondido && { getClientRects: () => [] }, this.aberto && { getClientRects: () => [{}] }].filter(Boolean)
    },
    addEventListener: (ev, fn) => escutas.document.push([ev, fn]),
    removeEventListener: (ev, fn) => { escutas.document = escutas.document.filter(([e, f]) => e !== ev || f !== fn) },
    async exitFullscreen() { doc.fullscreenElement = null; avisar() },
  }
  documentElement.requestFullscreen = async () => { doc.fullscreenElement = documentElement; avisar() }
  function avisar() { for (const [ev, fn] of escutas.document) if (ev === 'fullscreenchange') fn() }
  const win = {
    innerWidth: largura,
    addEventListener: (ev, fn, captura) => escutas.window.push([ev, fn, captura]),
    removeEventListener: (ev, fn, captura) => { escutas.window = escutas.window.filter(([e, f, c]) => e !== ev || f !== fn || c !== captura) },
  }
  const larguraJanela = Vue.ref(largura)
  const barraDeLojasNaTela = Vue.computed(() => larguraJanela.value >= 1024)
  const layout = Vue.ref(false)
  const naoLidasDuoke = Vue.ref(0)
  const resumo = Vue.ref(null)
  const emailEsperando = Vue.ref(0)
  const semLer = Vue.ref([])
  // A barra de lojas da página: recolhida pelo padrão de sempre (< 1536 px).
  const lojasRecolhidas = Vue.ref(largura < 1536)
  const g = {
    ref: Vue.ref, computed: Vue.computed, watch: Vue.watch, nextTick: Vue.nextTick,
    onMounted: (fn) => montados.push(fn), onBeforeUnmount: (fn) => desmontados.push(fn),
    useTelaCheia: () => layout, barraDeLojasNaTela, naoLidasDuoke, resumo, emailEsperando, semLer,
    tituloLeituraParada: (l) => `${l.length} loja(s) sem ler`, medirCaixa: () => medidas.push(1),
    localStorage, document: doc, window: win,
    LOJAS_KEY: 'davinci.atendimento.lojasRecolhidas', lojasRecolhidas, lojasEscolhidaPelaPessoa: true,
  }
  const nomes = Object.keys(g)
  const out = new Function(...nomes, `${BLOCO}\nreturn { TELA_CHEIA_KEY, telaCheia, telaCheiaAtiva, ROTULO_NA_BARRA, marcasNaBarra, mudarTelaCheia, telaDoNavegadorPode, naTelaDoNavegador, alternarTelaDoNavegador, escDeOutraCoisa, aoTeclarNaTelaCheia, ABERTO_POR_CIMA }`)(...nomes.map((n) => g[n]))
  return {
    ...out, guardado, layout, lojasRecolhidas, larguraJanela, doc, escutas, medidas, naoLidasDuoke, resumo, emailEsperando, semLer,
    montar() { montados.forEach((fn) => fn()) },
    desmontar() { desmontados.forEach((fn) => fn()) },
    teclar(o = {}) {
      let impedido = false
      const ev = { key: '', code: '', ctrlKey: false, shiftKey: false, altKey: false, metaKey: false, isComposing: false, repeat: false, target: { closest: () => null }, preventDefault: () => { impedido = true }, ...o }
      for (const [e, fn] of escutas.window) if (e === 'keydown') fn(ev)
      return impedido
    },
  }
}
const ATALHO = { key: 'F', code: 'KeyF', ctrlKey: true, shiftKey: true }
const ESC = { key: 'Escape', code: 'Escape' }

async function principal() {
  // ── começa na tela de sempre; o salvo volta depois de montar (SSR igual)
  {
    const t = montar({ guardado: new Map([['davinci.atendimento.telaCheia', '1']]) })
    assert.equal(t.TELA_CHEIA_KEY, 'davinci.atendimento.telaCheia')
    assert.equal(t.telaCheia.value, false, 'no servidor e antes de montar: a tela de sempre')
    assert.equal(t.layout.value, false)
    t.montar()
    await Vue.nextTick()
    assert.equal(t.telaCheia.value, true, 'recarregou: volta como estava')
    assert.equal(t.layout.value, true, 'o layout esconde o menu e a barra do topo')
    assert.deepEqual(t.escutas.window.map(([e, , c]) => [e, c]), [['keydown', true]], 'o atalho escuta na captura')
    assert.deepEqual(t.escutas.document.map(([e]) => e), ['fullscreenchange'])
    // Abaixo do `lg` (sem a barra de lojas): a tela de sempre, a escolha fica.
    t.larguraJanela.value = 900
    await Vue.nextTick()
    assert.equal(t.telaCheiaAtiva.value, false)
    assert.equal(t.layout.value, false, 'no celular e no tablet o menu volta')
    assert.equal(t.guardado.get('davinci.atendimento.telaCheia'), '1', 'a escolha não muda com a largura')
    assert.equal(t.teclar(ATALHO), false, 'sem a barra de lojas o atalho não faz nada (nem impede o do navegador)')
    assert.equal(t.telaCheia.value, true)
    t.larguraJanela.value = 1920
    await Vue.nextTick()
    assert.equal(t.layout.value, true, 'a tela cresceu: volta a tela cheia')
    // Saiu da página: as outras têm o menu; as escutas vão embora.
    t.desmontar()
    assert.equal(t.layout.value, false)
    assert.deepEqual([t.escutas.window, t.escutas.document], [[], []])
  }
  // ── botão e atalho: entram, saem e salvam
  {
    const t = montar()
    t.montar()
    assert.equal(t.telaCheia.value, false, 'sem escolha salva: a tela de sempre')
    t.mudarTelaCheia(true)
    await Vue.nextTick()
    assert.deepEqual([t.telaCheia.value, t.layout.value, t.guardado.get('davinci.atendimento.telaCheia')], [true, true, '1'])
    assert.ok(t.medidas.length >= 1, 'a Caixa mede de novo depois de trocar')
    assert.equal(t.teclar(ATALHO), true, 'Ctrl+Shift+F: o atalho é da página')
    await Vue.nextTick()
    assert.deepEqual([t.telaCheia.value, t.layout.value, t.guardado.get('davinci.atendimento.telaCheia')], [false, false, '0'])
    t.teclar(ATALHO)
    await Vue.nextTick()
    assert.equal(t.telaCheia.value, true, 'o mesmo atalho entra de novo')
    // Outras combinações não são o atalho.
    for (const o of [{ ...ATALHO, altKey: true }, { ...ATALHO, metaKey: true }, { ...ATALHO, shiftKey: false }, { ...ATALHO, ctrlKey: false }, { ...ATALHO, repeat: true }, { ...ATALHO, isComposing: true }]) {
      assert.equal(t.teclar(o), false)
      assert.equal(t.telaCheia.value, true)
    }
    // O Esc digitando (a caixa de resposta, a busca) ou com algo aberto por cima é de lá.
    t.teclar({ ...ESC, target: { closest: (sel) => (sel.includes('textarea') ? {} : null) } })
    assert.equal(t.telaCheia.value, true, 'Esc na caixa de resposta não sai')
    t.doc.aberto = { modal: true }
    t.teclar(ESC)
    assert.equal(t.telaCheia.value, true, 'Esc fechando um modal/menu/imagem não sai')
    for (const s of ['[role="dialog"]', '[aria-modal="true"]', '[role="menu"]', '.fixed.inset-0']) assert.ok(t.doc.ultimoSeletor.includes(s), s)
    assert.ok(!t.doc.ultimoSeletor.includes('listbox'), 'a lista de conversas (listbox) está sempre na tela: não conta')
    t.doc.aberto = null
    // O menu da plataforma mora escondido na página (v-show): não segura o Esc.
    t.doc.escondido = true
    t.teclar({ ...ESC, repeat: true })
    assert.equal(t.telaCheia.value, true, 'Esc segurado não conta')
    t.teclar(ESC)
    await Vue.nextTick()
    assert.deepEqual([t.telaCheia.value, t.layout.value, t.guardado.get('davinci.atendimento.telaCheia')], [false, false, '0'], 'Esc sai')
    t.teclar(ESC)
    assert.equal(t.telaCheia.value, false, 'fora da tela cheia o Esc não faz nada')
    t.desmontar()
  }
  // ── a barra de lojas sem escolha da pessoa abre com os nomes na tela cheia (como a do Duoke)
  {
    const t = montar({ largura: 1366 })
    t.montar()
    assert.equal(t.lojasRecolhidas.value, true, 'fora: o padrão de sempre (recolhida abaixo de 1536)')
    t.mudarTelaCheia(true)
    await Vue.nextTick()
    assert.equal(t.lojasRecolhidas.value, false, 'na tela cheia, de 1280 px para cima: com os nomes')
    assert.equal(t.guardado.has('davinci.atendimento.lojasRecolhidas'), false, 'o padrão não vira escolha salva')
    t.mudarTelaCheia(false)
    await Vue.nextTick()
    assert.equal(t.lojasRecolhidas.value, true, 'saiu: o padrão de sempre')
    t.desmontar()
    const pequena = montar({ largura: 1100 })
    pequena.montar()
    pequena.mudarTelaCheia(true)
    await Vue.nextTick()
    assert.equal(pequena.lojasRecolhidas.value, true, 'abaixo de 1280: recolhida mesmo na tela cheia')
    pequena.desmontar()
    const escolheu = montar({ largura: 1920, guardado: new Map([['davinci.atendimento.lojasRecolhidas', '1']]) })
    escolheu.lojasRecolhidas.value = true
    escolheu.montar()
    escolheu.mudarTelaCheia(true)
    await Vue.nextTick()
    assert.equal(escolheu.lojasRecolhidas.value, true, 'a escolha salva da pessoa manda')
    escolheu.desmontar()
  }
  // ── sem localStorage (aba anônima, bloqueado): funciona, só não lembra
  {
    const t = montar({ quebrado: true })
    t.montar()
    assert.equal(t.telaCheia.value, false)
    t.mudarTelaCheia(true)
    await Vue.nextTick()
    assert.deepEqual([t.telaCheia.value, t.layout.value], [true, true])
    t.teclar(ESC)
    assert.equal(t.telaCheia.value, false)
    t.desmontar()
  }
  // ── a tela inteira do navegador (Fullscreen API): à parte, e sai junto
  {
    const t = montar()
    t.montar()
    assert.equal(t.telaDoNavegadorPode.value, true)
    t.mudarTelaCheia(true)
    await t.alternarTelaDoNavegador()
    assert.equal(t.naTelaDoNavegador.value, true, 'o fullscreenchange acende o botão')
    await t.alternarTelaDoNavegador()
    assert.equal(t.naTelaDoNavegador.value, false)
    await t.alternarTelaDoNavegador()
    t.mudarTelaCheia(false)
    await esperar()
    assert.deepEqual([t.naTelaDoNavegador.value, t.doc.fullscreenElement], [false, null], 'saiu da tela cheia: sai da tela do navegador também')
    t.desmontar()
    const sem = montar({ telaDoNavegador: false })
    sem.montar()
    assert.equal(sem.telaDoNavegadorPode.value, false, 'sem Fullscreen API (moldura): sem o botão')
    // O navegador recusa (sem o clique): fica como está, sem erro.
    sem.doc.documentElement.requestFullscreen = async () => { throw new Error('recusado') }
    await sem.alternarTelaDoNavegador()
    assert.equal(sem.naTelaDoNavegador.value, false)
    sem.desmontar()
  }
  // ── os números da barra de ícones = os das abas de cima
  {
    const t = montar()
    assert.deepEqual(t.marcasNaBarra.value, {})
    t.naoLidasDuoke.value = 709
    t.resumo.value = { humano: 3 }
    t.emailEsperando.value = 2
    t.semLer.value = [{}, {}]
    assert.deepEqual(Object.fromEntries(Object.entries(t.marcasNaBarra.value).map(([k, v]) => [k, v.n])), { caixa: 709, humano: 3, mail: 2, lojas: 2 })
    assert.match(t.marcasNaBarra.value.caixa.titulo, /709 mensagem\(ns\) não lida\(s\)/)
    assert.equal(t.marcasNaBarra.value.lojas.titulo, '2 loja(s) sem ler')
    // Um nome curto para cada aba da página.
    const abas = [...(pagina.match(/const ABAS[\s\S]*?\n\]/) || [''])[0].matchAll(/value: '([a-z]+)'/g)].map((m) => m[1])
    assert.deepEqual(Object.keys(t.ROTULO_NA_BARRA), abas)
    for (const r of Object.values(t.ROTULO_NA_BARRA)) assert.ok(r.length <= 8, `"${r}" cabe na barra estreita`)
  }

  // ------------------------------------------------ a barra de ícones (SSR)
  {
    const a = tpl.indexOf('<nav\n      v-if="telaCheiaAtiva"')
    const b = tpl.indexOf('</nav>', a)
    assert.ok(a > 0 && b > a, 'a barra de ícones está no template')
    const fonte = tpl.slice(a, b + '</nav>'.length)
    const { code, errors: e } = compileTemplate({ source: fonte, filename: 'barra.vue', id: 'barra', compilerOptions: { isTS: true } })
    assert.deepEqual(e, [])
    const mod = { exports: {} }
    new Function('require', 'module', 'exports', transpile(code))(require, mod, mod.exports)
    const icone = (nome) => ({ name: nome, render: () => Vue.h('i', { 'data-icone': nome }) })
    const t = montar()
    async function render(ctx) {
      const app = Vue.createSSRApp({ setup: () => ctx, render: mod.exports.render })
      app.config.warnHandler = () => {}
      return semComentarios(await renderToString(app))
    }
    const ABAS = [
      { value: 'caixa', label: 'Caixa', icon: icone('Inbox') }, { value: 'humano', label: 'Caixa Humano', icon: icone('UserRound') },
      { value: 'mail', label: 'E-mail', icon: icone('Inbox') }, { value: 'lojas', label: 'Lojas e modo', icon: icone('Store') },
    ]
    const marcas = Vue.computed(() => ({ caixa: { n: 709, titulo: '709 mensagem(ns) não lida(s)' }, humano: { n: 3, titulo: '3 esperando' } }))
    const ctx = (o = {}) => ({
      telaCheiaAtiva: true, abasVisiveis: ABAS, aba: 'humano', marcasNaBarra: marcas.value, ROTULO_NA_BARRA: t.ROTULO_NA_BARRA,
      Mail: icone('Mail'), RotateCcw: icone('RotateCcw'), Fullscreen: icone('Fullscreen'), Minimize2: icone('Minimize2'),
      carregando: false, telaDoNavegadorPode: true, naTelaDoNavegador: false, atualizarTudo() {}, alternarTelaDoNavegador() {}, mudarTelaCheia() {},
      ...o,
    })
    const html = await render(ctx())
    assert.match(html, /<nav [^>]*aria-label="Abas do atendimento"[^>]*data-barra-tela-cheia/)
    assert.match(html, /class="sticky top-0 flex h-dvh w-\[68px\] shrink-0/, 'estreita, da altura da janela, como a do Duoke')
    const botoes = [...html.matchAll(/<button[^>]*>([\s\S]*?)<\/button>/g)].map((m) => m[0])
    assert.equal(botoes.length, ABAS.length + 3, 'as abas + Atualizar, Monitor e Sair')
    assert.match(botoes[0], /title="Caixa — 709 mensagem\(ns\) não lida\(s\)"[\s\S]*data-icone="Inbox"[\s\S]*>Caixa<\/span>[\s\S]*>99\+<\/span>/, 'Caixa com o número do Duoke (99+)')
    assert.match(botoes[1], /aria-current="page"[\s\S]*>Humano<\/span>[\s\S]*>3<\/span>/, 'a aba aberta acesa')
    assert.doesNotMatch(botoes[0], /aria-current/)
    assert.match(botoes[2], /data-icone="Mail"[\s\S]*>E-mail<\/span>/, 'o E-mail com o envelope (não o mesmo ícone da Caixa)')
    assert.doesNotMatch(botoes[2], /bg-red-600/, 'sem número, sem bolinha')
    assert.match(botoes[ABAS.length], />Atualizar<\/span>/)
    assert.match(botoes[ABAS.length + 1], /data-tela-do-navegador[\s\S]*>Monitor<\/span>/)
    assert.match(botoes[ABAS.length + 1], /aria-pressed="false"/)
    assert.match(botoes[ABAS.length + 2], /data-tela-cheia-sair[\s\S]*>Sair<\/span>/)
    const semFs = await render(ctx({ telaDoNavegadorPode: false, carregando: true }))
    assert.doesNotMatch(semFs, /data-tela-do-navegador/, 'sem Fullscreen API: sem o botão')
    assert.match(semFs, /disabled[^>]*title="Atualizar as conversas e os números"|title="Atualizar as conversas e os números"[^>]*disabled/)
    assert.match(semFs, /animate-spin/)
    assert.equal(await render(ctx({ telaCheiaAtiva: false })), '', 'fora da tela cheia, nada')
  }

  // ------------------------------------------------ os trechos do template
  {
    // O botão ao lado do "atualizar", só de notebook para cima (onde a barra de lojas aparece).
    const header = tpl.slice(tpl.indexOf('<PageHeader'), tpl.indexOf('</PageHeader>'))
    assert.match(tpl, /<PageHeader v-if="!telaCheiaAtiva" title="Atendimento"/, 'sem o título do DaVinci na tela cheia')
    assert.match(header, /<Button\s+size="sm"\s+variant="outline"\s+class="hidden lg:inline-flex"[\s\S]*?data-tela-cheia-entrar\s+@click="mudarTelaCheia\(true\)"\s*>[\s\S]*?Tela cheia\s*<\/Button>/)
    assert.ok(header.indexOf('data-tela-cheia-entrar') < header.indexOf('atualizar\n'), 'antes do "atualizar"')
    assert.match(header, /Ctrl\+Shift\+F; Esc sai/)
    // As abas de cima somem (a barra de ícones é a vez delas); os avisos e o filtro ficam.
    assert.match(tpl, /<!-- abas \(na tela cheia, a barra de ícones da esquerda\) -->\n {4}<div v-if="!telaCheiaAtiva" class="flex gap-1 overflow-x-auto border-b">/)
    const topo = tpl.slice(tpl.indexOf('<div ref="topoEl"'), tpl.indexOf('<!-- Caixa: lojas | fila | conversa + pedido -->'))
    for (const s of ['v-if="soLeitura"\n      v-show="aba !== \'mail\'"', 'v-if="avisos.length"\n      v-show="aba !== \'mail\'"', '<AtendimentoLeituraParada\n      v-if="naCaixa || aba === \'lojas\'"', '<AtendimentoFiltroPlataforma v-if="naCaixa"']) {
      assert.ok(topo.includes(s), `continua na tela cheia: ${s.split('\n')[0]}`)
    }
    // A raiz: ícones | o resto; na Caixa, o resto tem a altura da janela e a Caixa estica.
    assert.match(tpl, /^\s*<div :class="telaCheiaAtiva \? 'flex min-h-dvh' : ''" :data-tela-cheia="telaCheiaAtiva \|\| undefined">/)
    assert.match(tpl, /<div :class="telaCheiaAtiva \? \['flex min-w-0 flex-1 flex-col gap-2 p-2', naCaixa \? 'h-dvh' : ''\] : 'space-y-3'">/)
    assert.match(tpl, /ref="caixaEl"\n {6}class="flex min-h-\[420px\] overflow-hidden rounded-lg border bg-card"\n {6}:class="\{ 'flex-1': telaCheiaAtiva \}"\n {6}:style="telaCheiaAtiva \? undefined : \{ height: alturaCaixa \}"/)
    // A conversa de e-mail da aba E-mail também cresce.
    assert.match(tpl, /:style="\{ height: telaCheiaAtiva \? 'calc\(100dvh - 4\.5rem\)' : 'calc\(100dvh - 15rem\)' \}"/)
    // Na tela cheia a Caixa já está inteira: não rola a página até ela.
    assert.match(pagina, /function mostrarCaixaInteira\(\) \{\n {2}const el = caixaEl\.value\n {2}\/\/ [^\n]*\n {2}if \(!el \|\| !naCaixa\.value \|\| telaCheiaAtiva\.value\) return/)
    assert.match(pagina, /import \{[^}]*\bFullscreen\b[^}]*\bMaximize2\b[^}]*\bMinimize2\b[^}]*\} from 'lucide-vue-next'/)
  }

  // ------------------------------------------------ o layout e o composable
  {
    const layout = web('layouts/default.vue')
    assert.match(layout, /const telaCheia = useTelaCheia\(\)/)
    assert.match(layout, /<AppSidebar\n {6}v-show="!telaCheia"/, 'v-show: o menu não remonta ao sair')
    assert.match(layout, /<AppTopbar v-show="!telaCheia" /)
    assert.match(layout, /<main class="app-main flex-1 min-w-0" :class="telaCheia \? 'app-main--tela-cheia' : 'px-3 py-4 sm:px-4 lg:px-6 lg:py-6'">/)
    assert.match(layout, /\.app-main\.app-main--tela-cheia \{ padding: 0; \}/, 'sem o respiro do <main> (vence o padding-bottom do .app-main)')
    const comp = web('composables/useTelaCheia.ts')
    assert.match(comp, /export function useTelaCheia\(\) \{\n {2}return useState<boolean>\('app:telaCheia', \(\) => false\)\n\}/)
  }
  console.log('PASS: tela cheia do Atendimento — botão, Ctrl+Shift+F, Esc (menos digitando ou com algo aberto), lembrada no navegador (try), só do lg para cima, layout sem menu/topo, barra de ícones com os números, Fullscreen API à parte')
}

principal().catch((e) => {
  console.error(e)
  process.exit(1)
})
