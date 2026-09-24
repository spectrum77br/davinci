// Run from apps/web: node tests/marketing-desempenho-sfc.cjs
//
// Tela de Desempenho v2 (Eduardo, 24/09/2026): components/MarketingDesempenho.vue,
// MarketingDesempenhoVideos.vue, MarketingDesempenhoInvestir.vue e os helpers
// puros em utils/desempenho.ts.
//
// Trava as regras que mais fácil se perdem ao mexer na tela:
//  - métrica que NINGUÉM reportou aparece como "—", nunca como 0 (o YouTube
//    não mede salvamento; o Instagram só dá views com uma permissão que o
//    token ainda não tem — escrever 0 afirmaria o que a gente não sabe);
//  - a comparação é NA MESMA IDADE e contra o normal da conta, e cada célula
//    sem número diz por quê (aguardando, cedo, sem views, falhou);
//  - leitura velha avisa; falha de hoje não apaga a leitura boa de ontem;
//  - "Atualizar agora" respeita a trava de 10 min do servidor;
//  - as ressalvas (venda não é medida, view não é igual entre redes, pouco
//    dado) ficam NA TELA.
// Só dados FALSOS aqui; nenhuma chamada de rede.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const Vue = require('vue')
const { parse, compileTemplate, compileScript } = require('vue/compiler-sfc')

const transpile = (source, module = ts.ModuleKind.CommonJS) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module },
}).outputText

// ---------------------------------------------------------------- SFCs
const SFCS = ['MarketingDesempenho', 'MarketingDesempenhoVideos', 'MarketingDesempenhoInvestir']
const sfc = {}
for (const nome of SFCS) {
  const filename = path.join(__dirname, '..', 'components', `${nome}.vue`)
  const source = fs.readFileSync(filename, 'utf8')
  const { descriptor, errors } = parse(source, { filename })
  assert.deepEqual(errors, [], `${nome}: SFC parseia`)
  const compilado = compileTemplate({ source: descriptor.template.content, filename, id: `${nome}-check` })
  assert.deepEqual(compilado.errors, [], `${nome}: template compila`)
  // O render compilado precisa ser JS válido (imports do vue resolvem via require).
  new Function('exports', 'require', transpile(compilado.code))({}, require)
  // O <script setup> também: macro, import e tipo de prop passam pelo compilador.
  compileScript(descriptor, { id: `${nome}-check` })
  sfc[nome] = { source, script: descriptor.scriptSetup.content, tpl: descriptor.template.content }
}

// ---------------------------------------------------------------- helpers puros
const utilsArquivo = path.join(__dirname, '..', 'utils', 'desempenho.ts')
const utils = fs.readFileSync(utilsArquivo, 'utf8')
const ini = utils.indexOf('// ---------- helpers puros')
const fim = utils.indexOf('// ---------- fim helpers puros')
assert.ok(ini > 0 && fim > ini, 'marcadores dos helpers presentes')
const H = {}
new Function('exports', transpile(utils.slice(ini, fim)))(H)

// Ausente vira travessão. Zero é zero de verdade e aparece.
assert.equal(H.num({ views: 1234 }, 'views'), '1.234', 'formata em pt-BR')
assert.equal(H.num({ views: 0 }, 'views'), '0', 'zero medido aparece como zero')
assert.equal(H.num({}, 'salvamentos'), '—', 'não reportado vira travessão, não 0')
assert.equal(H.num({ salvamentos: null }, 'salvamentos'), '—', 'nulo também')

// O ganho no período só aparece quando existe e é diferente de zero.
assert.equal(H.ganho({ views: 200 }, 'views'), '+200')
assert.equal(H.ganho({ views: 0 }, 'views'), '', 'ganho zero não polui a tela')
assert.equal(H.ganho({}, 'views'), '', 'sem dado, sem ganho')

// Título do cartão: compacto pra caber no celular.
assert.equal(H.compacto(null), '—')
assert.equal(H.compacto(0), '0')
assert.match(H.compacto(12345), /12,3\s?mil/)
assert.equal(H.maisCompacto(null), '—', 'ganho ausente não vira "+0"')
assert.equal(H.sinal(-5), '-5', 'ganho negativo mantém o sinal (o YouTube tira view)')
assert.equal(H.sinal(0), '0')
assert.equal(H.qtd(1, 'vídeo', 'vídeos'), '1 vídeo')
assert.equal(H.qtd(2, 'vídeo', 'vídeos'), '2 vídeos')

// Índice "× o normal da conta".
assert.equal(H.fmtIndice(2.06), '2,1×')
assert.equal(H.fmtIndice(1), '1,0×')
assert.equal(H.fmtIndice(null), '—')
assert.equal(H.tomIndice(1.3), 'alto')
assert.equal(H.tomIndice(1), 'normal')
assert.equal(H.tomIndice(0.5), 'baixo', 'fraco é "baixo", nunca "ruim"')
assert.equal(H.tomIndice(null), null)

// Barra divergente em log2: dobro e metade têm o mesmo tamanho.
assert.deepEqual(H.barra(2), { lado: 'direita', pct: 25, tom: 'acima' })
assert.deepEqual(H.barra(0.5), { lado: 'esquerda', pct: 25, tom: 'abaixo' })
assert.equal(H.barra(10).pct, 50, 'viral fica preso em 4×')
assert.equal(H.barra(0).pct, 50, 'zero fica preso em 0,25×')
assert.equal(H.barra(1).lado, 'centro')
assert.equal(H.barra(3, 'pouco_dado').tom, 'cinza', 'pouco dado nunca fica verde')
assert.equal(H.barra(null), null)

assert.match(H.rotuloLeitura('pouco_dado'), /pouco dado/)
assert.equal(H.rotuloLeitura('indicio'), 'indício')
assert.equal(H.rotuloLeitura('comparavel'), 'dá pra comparar')

assert.equal(H.pct(0.078), '7,8%')
assert.equal(H.pct(null), '—')

assert.equal(H.deltaTxt(118, 100).texto, '▲ 18%')
assert.equal(H.deltaTxt(80, 100).texto, '▼ 20%')
assert.equal(H.deltaTxt(5, null), null, 'sem período anterior, sem porcentagem')
assert.equal(H.deltaTxt(5, 0), null, 'base zero não dá porcentagem honesta')

// Frescor em horário de Brasília: "hoje" é o dia de São Paulo, não o UTC.
const AGORA = new Date('2026-09-24T18:00:00Z') // 15:00 em Brasília
assert.match(H.frescor('2026-09-24T15:47Z', AGORA).texto, /hoje às 12:47/)
assert.equal(H.frescor(new Date(AGORA - 26 * 36e5).toISOString(), AGORA).tom, 'ok')
assert.equal(H.frescor(new Date(AGORA - 31 * 36e5).toISOString(), AGORA).tom, 'velha', 'passou de 30 h, a noturna não rodou')
assert.match(H.frescor('2026-09-24T02:47:00Z', AGORA).texto, /ontem às 23:47/, '02:47 UTC é 23:47 do dia anterior em Brasília')
assert.equal(H.frescor(null).tom, 'nenhuma')
assert.equal(H.ddmm('2026-08-26'), '26/08', 'data pura não anda um dia pra trás no fuso')
assert.equal(H.diaRelativo('2026-09-25T02:47:00Z', AGORA), 'hoje', 'a noturna das 23:47 de hoje')

// Célula rede × vídeo: cada "sem número" diz por quê.
assert.equal(H.celula(undefined, 3, AGORA).texto, 'não postado')
// Saiu e foi apagado depois: dizer "não postado" seria mentira.
assert.equal(H.celula(undefined, 3, AGORA, true).texto, 'apagado da rede')
assert.equal(H.celula(undefined, 3, AGORA, true).estado, 'apagado')
assert.equal(H.celula({ estado: 'aguardando' }, 3, AGORA).texto, 'aguardando 1ª leitura')
{
  const c = H.celula({ estado: 'aguardando', publicado_em: '2026-09-24T15:04:00Z', marco_pronto_em: '2026-09-28T02:47:00Z' }, 3, AGORA)
  assert.equal(c.estado, 'aguardando')
  assert.equal(c.dica, 'Publicado às 12:04. A primeira leitura sai em até 1 hora; a comparação de 3 dias fica pronta em 27/09.')
}
{
  const cedo = { estado: 'ok', lido_em: '2026-09-24T02:47:00Z', views_marco: null, marco_motivo: 'cedo', acumulado: { views: 500 } }
  assert.match(H.celula({ ...cedo, marco_pronto_em: '2026-09-27T02:47:00Z' }, 3, AGORA).texto, /faltam 2 dias/)
  assert.equal(H.celula({ ...cedo, marco_pronto_em: '2026-09-25T02:47:00Z' }, 3, AGORA).texto, 'sai hoje à noite')
  assert.equal(H.celula({ ...cedo, marco_pronto_em: '2026-09-27T02:47:00Z' }, 3, AGORA).detalhe, 'total 500 até agora')
}
{
  const ok = H.celula({ estado: 'ok', lido_em: 'x', views_marco: 1240, marco_motivo: null, acumulado: { views: 1900 } }, 3, AGORA)
  assert.equal(ok.estado, 'ok')
  assert.equal(ok.texto, '1.240', 'o número da célula é a view na idade comparada…')
  assert.equal(ok.detalhe, 'total 1.900', '…e o total fica de apoio')
}
assert.match(H.celula({ estado: 'ok', lido_em: 'x', views_marco: null, marco_motivo: 'sem_views', acumulado: { curtidas: 12 } }, 3, AGORA).texto, /sem views/)
assert.equal(H.celula({ estado: 'ok', lido_em: 'x', views_marco: null, marco_motivo: 'buraco', acumulado: {} }, 3, AGORA).texto, 'sem leitura nessa idade')
{
  // Falhou sem nunca ter lido: âmbar, com o erro no title.
  const f = H.celula({ estado: 'falhou', lido_em: null, erro: 'HTTP 403', acumulado: {} }, 3, AGORA)
  assert.equal(f.estado, 'falhou')
  assert.equal(f.texto, 'não consegui ler')
  assert.equal(f.dica, 'HTTP 403')
  // Falhou HOJE mas já tinha número: mostra o número bom e avisa de quando é.
  const g = H.celula({ estado: 'falhou', lido_em: '2026-09-23T02:50:00Z', erro: 'timeout', views_marco: 800, acumulado: { views: 900 } }, 3, AGORA)
  assert.equal(g.estado, 'ok')
  assert.equal(g.texto, '800', 'falha de hoje não some com o número bom')
  assert.equal(g.aviso, 'a leitura de hoje falhou — mostrando a de 22/09')
}

// Mini-barras: null não desenha, negativo fica no chão, altura proporcional.
{
  const g = H.geomBarras([10, null, -5, 20], 100, 28)
  assert.deepEqual(g.barras.map((b) => b.i), [0, 2, 3], 'dia sem dado não tem barra')
  assert.equal(g.barras.find((b) => b.i === 2).h, 0, 'ganho negativo desenha 0')
  assert.equal(g.barras.find((b) => b.i === 2).v, -5, '…mas guarda o valor com sinal pro title')
  assert.equal(g.barras.find((b) => b.i === 3).h, 2 * g.barras.find((b) => b.i === 0).h, 'altura proporcional')
  assert.equal(g.yMax, 20)
  const vazio = H.geomBarras([null, null], 100, 28)
  assert.ok(vazio.barras.length === 0 && vazio.yMax === 1, 'tudo nulo: nada desenhado, escala 1')
}
{
  const c = H.geomCurva([[1.5, 420], [0, 0], [0.5, 180], [20, 999]])
  assert.equal(c.pontos.length, 3, 'curva só até 14 dias, em ordem')
  assert.equal(c.yMax, 420)
  assert.deepEqual(c.marcos.map((m) => m.rotulo), ['1d', '3d', '7d'])
}
assert.deepEqual(H.redesDaTabela(['tiktok']), ['instagram', 'youtube', 'tiktok'], 'as três redes sempre')

// Matriz por marca: ganho negativo (o YouTube tira view de robô) não sai em
// verde — emerald é ganho. A cor vem do sinal, não é fixa na célula.
{
  const celula = sfc.MarketingDesempenho.tpl.match(/<span\s+v-if="ganho\(plat\(m, r\)[\s\S]*?<\/span>/)
  assert.ok(celula, 'célula de ganho da matriz por marca')
  assert.ok(!/\sclass="[^"]*emerald/.test(celula[0]), 'o verde não é fixo')
  assert.match(celula[0], /:class="\(plat\(m, r\)\?\.no_periodo\?\.views \?\? 0\) > 0 \? 'text-emerald/)
}
assert.deepEqual(H.redesDaTabela(['facebook']), ['instagram', 'youtube', 'tiktok', 'facebook'], 'Facebook só se houver post')

// ---------------------------------------------------------------- higiene estática
// A checagem é sobre CREDENCIAL, não sobre a palavra: o componente comenta que
// "o token ainda não tem a permissão de insights", e proibir o termo proibiria
// explicar o porquê das coisas. O que não pode é a tela ler, guardar ou mandar
// credencial.
const arquivos = [...SFCS.map((n) => sfc[n].source), utils]
for (const codigo of arquivos) {
  assert.ok(!/Authorization|Bearer\s|access_token|refresh_token|client_secret/i.test(codigo),
    'a tela não manda nem lê credencial')
  assert.ok(!/\b(senha|password)\s*[:=]/i.test(codigo), 'nenhum campo de senha')
}
// Só fala com os três endpoints de métricas: ler, pedir leitura, tirar/voltar.
const urls = new Set(arquivos.flatMap((c) => [...c.matchAll(/['`](\/api\/[^'`$]*)/g)].map((m) => m[1])))
assert.deepEqual([...urls].sort(), [
  '/api/marketing/metricas/atualizar',
  '/api/marketing/metricas/postagens/',
  '/api/marketing/metricas?',
], 'só fala com os endpoints de métricas')
// Link pro post abre fora, sem dar window.opener pra rede social.
for (const n of ['MarketingDesempenho', 'MarketingDesempenhoVideos']) {
  assert.match(sfc[n].tpl, /target="_blank" rel="noopener"/, `${n}: link do post com rel=noopener`)
}
// O Tailwind não varre utils/: classe de cor lá sairia sem CSS.
assert.ok(!/\b(bg|text|border)-(emerald|amber|red|muted|foreground)/.test(utils), 'utils não carrega classe do Tailwind')

// ---------------------------------------------------------------- textos travados
const templates = SFCS.map((n) => sfc[n].tpl).join('\n')
for (const trecho of [
  'aguardando 1ª leitura', // vídeo novo não é "—" nem erro
  'Fora do desempenho', // o que foi tirado continua à vista
  'não é medido', // venda por vídeo não é medida — a tela não finge
  'não conta igual em cada rede', // soma das redes é só tendência
  'pouco dado', // amostra pequena avisa
]) {
  assert.ok(templates.includes(trecho), `texto na tela: ${trecho}`)
}

// ---------------------------------------------------------------- script setup
// Molde de marketing-criativos-sfc.cjs: executa o <script setup> com api,
// window e timers FALSOS. Os helpers entram pelos mesmos nomes do import.
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const semImports = (s) => s.replace(/^import[\s\S]*?from\s+'[^']+'\s*$/gm, '')
const apiError = {}
new Function('exports', transpile(fs.readFileSync(path.join(__dirname, '../lib/apiError.ts'), 'utf8')))(apiError)
const NOMES_H = Object.keys(H)

function fabrica(nome, extras, retorno) {
  return new AsyncFunction(
    'ref', 'computed', 'watch', 'nextTick', 'onMounted', 'onBeforeUnmount',
    'useApi', 'useToasts', 'useCan', 'apiErrMsg', 'window', 'defineProps', 'defineEmits', ...NOMES_H,
    transpile(semImports(sfc[nome].script), ts.ModuleKind.ESNext) + `\nreturn { ${retorno} }`,
  )
}
const telaFactory = fabrica('MarketingDesempenho', [], `
  dias, marco, marcaId, dados, carregando, recarregando, erro, carregar, atualizarAgora, atualizando,
  aindaRodando, podeAtualizarEm, cartoes, voltarAContar, nadaPublicado, leitura, filtrarMarca, semIndice`)
const videosFactory = fabrica('MarketingDesempenhoVideos', [], `
  linhas, ordenadas, ordemEfetiva, ordem, abrirTirar, confirmarTirar, tirando, motivo, motivoErro, restantes`)
const investirFactory = fabrica('MarketingDesempenhoInvestir', [], 'dim, lista, comIndice, porPostagem, linhas')

function relogioFalso() {
  let id = 0
  const fila = new Map()
  return {
    setTimeout: (fn, ms) => { fila.set(++id, { fn, ms }); return id },
    clearTimeout: (i) => { fila.delete(i) },
    setInterval: () => 0,
    clearInterval: () => {},
    pendentes: () => [...fila.values()],
    async disparar() {
      const [[i, t]] = [...fila]
      fila.delete(i)
      await t.fn()
    },
  }
}
function toastsFalsos(log) {
  const f = (kind) => (...a) => log.push([kind, ...a])
  return { success: f('success'), error: f('error'), warning: f('warning'), info: f('info'), push: () => 1, dismiss: () => {} }
}
const esperar = () => new Promise(setImmediate)

function post(over = {}) {
  return {
    postagem_id: 'p-tt', creative_id: 'c1', marca_id: 'm1', marca: 'Uranyx', plataforma: 'tiktok', conta: 'uranyx_br',
    post_url: 'https://www.tiktok.com/@uranyx_br/video/1', publicado_em: '2026-09-20T15:04:00Z', origem: 'robo',
    titulo: 'Tecnologia que aguenta o teu dia', horario: '12h', idade_horas: 98, estado: 'ok',
    lido_em: '2026-09-24T02:47:00Z', tentado_em: '2026-09-24T02:47:00Z', erro: null,
    acumulado: { views: 1900, curtidas: 88 }, no_periodo: { views: 820 }, no_periodo_estimado: false,
    views_marco: 1240, marco_motivo: null, marco_pronto_em: '2026-09-24T02:47:00Z',
    indice_views: 2.1, indice_motivo: null, base: { mediana: 590, n: 7 },
    taxa_interacao: 0.064, indice_interacao: 1.3, ritmo_dia: 120,
    curva: [[0, 0], [0.5, 180], [1.5, 420]], autor_diferente: null, ...over,
  }
}
function criativo(over = {}) {
  return {
    creative_id: 'c1', titulo: 'Tecnologia que aguenta o teu dia', marca: 'Uranyx', marca_id: 'm1',
    sku: 'dg017.pi', modelo: 'video 15s',
    produto: { chave: 'prod:1', rotulo: 'Fone DG017' }, formato: { chave: '15s', rotulo: 'vídeo 15s' },
    agencia: { chave: 'nenhum', rotulo: '(sem agência)' }, roteiro: { chave: 'nenhum', rotulo: '(sem roteiro)' },
    primeira_publicacao_em: '2026-09-20T15:04:00Z', indice_views: 2.1, indice_interacao: 1.3, n_indices: 1,
    postagens: { instagram: [], youtube: [], tiktok: ['p-tt'] }, ...over,
  }
}
function resposta(over = {}) {
  return {
    versao: 2, dias: 30, desde: '2026-08-26', ate: '2026-09-24', marco: 3, marca_id: null, gerado_em: '2026-09-24T18:02:11Z',
    minimos: { base_conta: 5, views_taxa: 100, indicio: 3, comparavel: 8, tolerancia_h: 36 },
    coleta: {
      ultima_leitura_em: new Date(Date.now() - 2 * 36e5).toISOString(), proxima_leitura_em: null,
      proxima_noturna_em: '2026-09-25T02:47:00Z', inicio_da_coleta: '2026-09-23', em_andamento: false,
      pode_atualizar_em: null, ultima_rodada: null,
    },
    marcas_disponiveis: [{ id: 'm1', nome: 'Uranyx' }],
    resumo: {
      videos: { no_ar: 1, aguardando: 0, com_falha: 0, fora_do_ar: 0, fora_do_desempenho: 0 },
      redes: [{
        plataforma: 'tiktok', videos: 1, aguardando: 0, com_falha: 0, metrica_serie: 'views', sem_views: false,
        no_periodo: { views: 820 }, interacoes_no_periodo: 40, acumulado: { views: 1900 }, periodo_anterior: null,
        comparacao: { atual: 700, anterior: null, ate: '2026-09-23' },
        serie: [null, 400, 300, 120], estimado_dias: ['2026-09-22'], lido_em: '2026-09-24T02:47:00Z', erro: null,
      }],
      tendencia: { views_no_periodo: 820, redes_sem_views: [] },
    },
    serie_dias: ['2026-09-21', '2026-09-22', '2026-09-23', '2026-09-24'],
    publicacoes_por_dia: [0, 1, 0, 0],
    postagens: [post()],
    criativos: [criativo()],
    grupos: { produto: [], formato: [], agencia: [], roteiro: [], horario: [] },
    marcas: [], sem_video_no_ar: [], fora_do_ar: [], fora_do_desempenho: [],
    ...over,
  }
}

async function tela({ get, post: postResp, patch } = {}) {
  const calls = []
  const toastLog = []
  const relogio = relogioFalso()
  const aoDesmontar = []
  const api = (url, opts) => {
    calls.push({ url, opts })
    if (!opts?.method) return get ? get(url) : Promise.resolve(resposta())
    if (opts.method === 'POST') return postResp ? postResp(url) : Promise.reject(new Error('sem POST'))
    if (opts.method === 'PATCH') return patch ? patch(url, opts) : Promise.resolve({})
    return Promise.reject(new Error(`api falso não conhece ${url}`))
  }
  const s = await telaFactory(
    Vue.ref, Vue.computed, Vue.watch, Vue.nextTick, (fn) => fn(), (fn) => aoDesmontar.push(fn),
    () => ({ api }), () => toastsFalsos(toastLog), () => Vue.ref(true), apiError.apiErrMsg, relogio,
    () => ({}), () => () => {},
    ...NOMES_H.map((n) => H[n]),
  )
  await esperar()
  return { s, calls, toastLog, relogio, desmontar: () => aoDesmontar.forEach((fn) => fn()) }
}

async function videos(props, { patch } = {}) {
  const calls = []
  const toastLog = []
  const emitidos = []
  const api = (url, opts) => {
    calls.push({ url, opts })
    return patch ? patch(url, opts) : Promise.resolve({})
  }
  const p = Vue.reactive({ marco: 3, minimos: resposta().minimos, canEdit: true, ...props })
  const s = await videosFactory(
    Vue.ref, Vue.computed, Vue.watch, Vue.nextTick, (fn) => fn(), () => {},
    () => ({ api }), () => toastsFalsos(toastLog), () => Vue.ref(true), apiError.apiErrMsg, {},
    () => p, () => (nome, ...a) => emitidos.push([nome, ...a]),
    ...NOMES_H.map((n) => H[n]),
  )
  return { s, calls, toastLog, emitidos }
}

async function run() {
  // Carga: um GET só, com período e idade; marca e período trocados refazem.
  {
    const { s, calls } = await tela()
    assert.equal(calls[0].url, '/api/marketing/metricas?dias=30&marco=3')
    assert.equal(s.dados.value.postagens.length, 1)
    s.marcaId.value = 'm1'
    await Vue.nextTick(); await esperar()
    assert.equal(calls.at(-1).url, '/api/marketing/metricas?dias=30&marco=3&marca_id=m1', 'marca filtra no servidor')
    s.filtrarMarca('m1')
    await Vue.nextTick(); await esperar()
    assert.equal(s.marcaId.value, null, 'clicar de novo na marca tira o filtro')
    s.marco.value = 7
    s.dias.value = 7
    await Vue.nextTick(); await esperar()
    assert.equal(calls.at(-1).url, '/api/marketing/metricas?dias=7&marco=7')

    // Cartão da rede: hoje e dia estimado ficam mais claros e dizem por quê.
    const c = s.cartoes.value[0]
    assert.deepEqual(c.barras.map((b) => b.i), [1, 2, 3], 'dia sem dado não tem barra')
    const hoje = c.barras.find((b) => b.i === 3)
    assert.equal(hoje.opacidade, 0.4)
    assert.match(hoje.dica, /hoje \(parcial\)/)
    const est = c.barras.find((b) => b.i === 1)
    assert.equal(est.opacidade, 0.55)
    assert.match(est.dica, /estimado: ganho de vários dias dividido igualmente entre eles/)
    assert.equal(s.leitura.value.tom, 'ok')
  }

  // A porcentagem do cartão é o par de dias FECHADOS que o servidor manda,
  // não o título (que tem hoje pela metade): fluxo parado dá 0%, não "▼ 14%".
  {
    const base = resposta()
    const rede = {
      ...base.resumo.redes[0], no_periodo: { views: 600 }, periodo_anterior: 700,
      comparacao: { atual: 700, anterior: 700, ate: '2026-09-23' },
    }
    const { s } = await tela({ get: () => Promise.resolve(resposta({ resumo: { ...base.resumo, redes: [rede] } })) })
    const c = s.cartoes.value[0]
    assert.equal(c.delta.texto, '▲ 0%')
    assert.equal(c.deltaDica, 'Compara só dias fechados: os 30 dias até 23/09 com os 30 anteriores a eles.')
    const semBase = { ...rede, periodo_anterior: null, comparacao: { atual: 700, anterior: null, ate: '2026-09-23' } }
    const t2 = await tela({ get: () => Promise.resolve(resposta({ resumo: { ...base.resumo, redes: [semBase] } })) })
    assert.equal(t2.s.cartoes.value[0].delta, null, 'sem período anterior, sem porcentagem')
  }
  // Rede só com vídeo aguardando: o cartão diz isso, não "sem insights".
  assert.ok(sfc.MarketingDesempenho.tpl.includes("v-else-if=\"!c.r.videos && c.r.aguardando\""))

  // Aba trocada com a checagem em voo: nada de timer órfão nem toast na outra aba.
  {
    const pedido = new Date().toISOString()
    let segurar = false
    const pendentes = []
    const { s, toastLog, relogio, desmontar } = await tela({
      get: () => (segurar ? new Promise((resolve) => pendentes.push(resolve)) : Promise.resolve(resposta())),
      post: () => Promise.resolve({ enfileirado: true, pedido_em: pedido, pode_atualizar_em: new Date(Date.now() + 600e3).toISOString() }),
    })
    await s.atualizarAgora()
    segurar = true
    const voo = relogio.disparar() // a checagem de 15 s sai; o GET fica em voo
    await esperar()
    desmontar()
    const fim = new Date(Date.parse(pedido) + 1000).toISOString()
    pendentes[0](resposta({ coleta: { ...resposta().coleta, ultima_rodada: { modo: 'agora', inicio: pedido, fim, total: 1, ok: 1, falhou: 0 } } }))
    await voo
    assert.equal(relogio.pendentes().length, 0, 'desmontou: nenhum timer novo')
    assert.ok(!toastLog.some(([k]) => k === 'success'), 'nenhum toast na aba em que o usuário está')
  }
  // O POST que volta depois do unmount também não arma a checagem.
  {
    let soltar
    const { s, relogio, desmontar } = await tela({ post: () => new Promise((resolve) => { soltar = resolve }) })
    const pedindo = s.atualizarAgora()
    desmontar()
    soltar({ enfileirado: true, pedido_em: new Date().toISOString(), pode_atualizar_em: new Date(Date.now() + 600e3).toISOString() })
    await pedindo
    assert.equal(relogio.pendentes().length, 0)
  }

  // Filtro trocado rápido: a resposta velha que chega depois não sobrescreve.
  {
    const pendentes = []
    const { s } = await tela({ get: () => new Promise((resolve) => pendentes.push(resolve)) })
    s.carregar()
    pendentes[1](resposta({ dias: 7 }))
    await esperar()
    pendentes[0](resposta({ dias: 30 }))
    await esperar()
    assert.equal(s.dados.value.dias, 7, 'só a última chamada escreve na tela')
  }

  // Falha ao carregar: caixa com o código, sem derrubar a aba.
  {
    const { s } = await tela({ get: () => Promise.reject({ data: { detail: { code: 'boom' } } }) })
    assert.equal(s.erro.value, 'boom')
    assert.equal(s.dados.value, null)
  }

  // Servidor ainda na API v1 (deploy pela metade): a aba abre vazia, não quebra.
  {
    const { s } = await tela({ get: () => Promise.resolve({ dias: 30, desde: 'x', marcas: [], sem_video_no_ar: [] }) })
    assert.deepEqual(s.dados.value.resumo.redes, [])
    assert.deepEqual(s.dados.value.postagens, [])
    assert.equal(s.nadaPublicado.value, true)
  }

  // Atualizar agora: 202 → confere a cada 15 s até a rodada terminar.
  {
    const pedido = new Date().toISOString()
    let fim = null
    const { s, calls, toastLog, relogio } = await tela({
      get: () => Promise.resolve(resposta({
        coleta: { ...resposta().coleta, ultima_rodada: fim && { modo: 'agora', inicio: pedido, fim, total: 1, ok: 1, falhou: 0 } },
      })),
      post: () => Promise.resolve({ enfileirado: true, pedido_em: pedido, pode_atualizar_em: new Date(Date.now() + 600e3).toISOString() }),
    })
    await s.atualizarAgora()
    assert.equal(calls.at(-1).url, '/api/marketing/metricas/atualizar')
    assert.equal(calls.at(-1).opts.method, 'POST')
    assert.equal(s.atualizando.value, true, 'botão diz "Atualizando…"')
    assert.ok(s.podeAtualizarEm.value, 'botão travado pelos 10 min do servidor')
    assert.equal(relogio.pendentes()[0].ms, 15000)
    await relogio.disparar()
    assert.equal(s.atualizando.value, true, 'rodada ainda não terminou')
    fim = new Date(Date.now() + 1000).toISOString()
    await relogio.disparar()
    assert.equal(s.atualizando.value, false)
    assert.ok(toastLog.some(([k, t]) => k === 'success' && /^Números atualizados às \d\d:\d\d\.$/.test(t)), 'avisa quando terminou')
    const antes = calls.length
    await s.atualizarAgora()
    assert.equal(calls.length, antes, 'travado: nem chama o servidor')
  }

  // Doze conferências sem terminar: para de girar e avisa que ainda roda.
  {
    const { s, relogio } = await tela({
      post: () => Promise.resolve({ enfileirado: true, pedido_em: new Date().toISOString(), pode_atualizar_em: new Date(Date.now() + 600e3).toISOString() }),
    })
    await s.atualizarAgora()
    for (let i = 0; i < 12; i++) await relogio.disparar()
    assert.equal(s.atualizando.value, false)
    assert.equal(s.aindaRodando.value, true, '"a leitura ainda está rodando"')
    assert.equal(relogio.pendentes()[0].ms, 60000, 'segue olhando, mais devagar')
  }

  // 429: o servidor já leu há pouco — toast com a hora e botão travado.
  {
    const quando = new Date(Date.now() + 5 * 60e3).toISOString()
    const { s, toastLog } = await tela({
      post: () => Promise.reject({ statusCode: 429, data: { detail: { code: 'atualizacao_recente', pode_atualizar_em: quando } } }),
    })
    await s.atualizarAgora()
    assert.ok(toastLog.some(([k, t]) => k === 'warning' && /^Já atualizei há pouco — dá pra pedir de novo às \d\d:\d\d\.$/.test(t)))
    assert.equal(s.podeAtualizarEm.value, new Date(quando).toISOString())
    assert.equal(s.atualizando.value, false)
  }

  // 503: fila fora do ar.
  {
    const { s, toastLog } = await tela({
      post: () => Promise.reject({ statusCode: 503, data: { detail: { code: 'fila_indisponivel' } } }),
    })
    await s.atualizarAgora()
    assert.ok(toastLog.some(([k, t]) => k === 'error' && t === 'Não consegui pedir a leitura agora (fila_indisponivel).'))
  }

  // Voltar a contar: PATCH contar=true e recarrega.
  {
    const { s, calls, toastLog } = await tela()
    const gets = calls.length
    await s.voltarAContar('p-velho')
    const patch = calls.find((c) => c.opts?.method === 'PATCH')
    assert.equal(patch.url, '/api/marketing/metricas/postagens/p-velho/desempenho')
    assert.deepEqual(patch.opts.body, { contar: true, motivo: null })
    assert.ok(toastLog.some(([k, t]) => k === 'success' && t === 'Vídeo voltou a contar.'))
    assert.ok(calls.length > gets + 1, 'recarrega depois')
  }

  // Tabela de vídeos: célula por rede, "+1 post", motivo do índice nulo e ordem.
  {
    const ig1 = post({ postagem_id: 'p-ig1', plataforma: 'instagram', publicado_em: '2026-09-22T22:03:00Z', indice_views: null, indice_motivo: 'base_pequena' })
    const ig2 = post({ postagem_id: 'p-ig2', plataforma: 'instagram', publicado_em: '2026-09-21T22:03:00Z' })
    const novo = post({
      postagem_id: 'p-novo', creative_id: 'c2', estado: 'aguardando', lido_em: null, views_marco: null,
      marco_motivo: 'aguardando', indice_views: null, publicado_em: new Date().toISOString(),
    })
    const { s } = await videos({
      postagens: [post(), ig1, ig2, novo],
      criativos: [
        criativo({ postagens: { instagram: ['p-ig1', 'p-ig2'], youtube: [], tiktok: ['p-tt'] } }),
        criativo({ creative_id: 'c2', titulo: 'novo', indice_views: null, n_indices: 0, primeira_publicacao_em: novo.publicado_em, postagens: { instagram: [], youtube: [], tiktok: ['p-novo'] } }),
      ],
    })
    const l1 = s.linhas.value[0]
    assert.deepEqual(l1.colunas.map((c) => c.rede), ['instagram', 'youtube', 'tiktok'])
    assert.equal(l1.colunas[0].post.postagem_id, 'p-ig1', 'dois posts na mesma rede: mostra o mais novo')
    assert.equal(l1.colunas[0].extra, 1, '…e avisa "+1 post"')
    assert.equal(l1.colunas[1].cel.texto, 'não postado')
    assert.equal(l1.meta, 'Fone DG017 · vídeo 15s · 20/09 12h', '"(sem agência)" não polui a linha')
    const l2 = s.linhas.value[1]
    assert.equal(l2.colunas[2].cel.texto, 'aguardando 1ª leitura')
    assert.equal(l2.semIndice, 'cedo demais', 'índice nulo diz por quê')
    assert.equal(s.ordemEfetiva.value, 'novos', 'com menos de 3 índices, o padrão é "mais novos"')
    assert.equal(s.ordenadas.value[0].c.creative_id, 'c2')
    s.ordem.value = 'indice'
    assert.equal(s.ordenadas.value[0].c.creative_id, 'c1', 'por índice, nulo vai pro fim')
  }
  {
    const cs = [1, 2, 3].map((i) => criativo({ creative_id: `c${i}`, indice_views: i }))
    const { s } = await videos({ postagens: [post()], criativos: cs })
    assert.equal(s.ordemEfetiva.value, 'indice', 'com 3 índices, o padrão é o índice')
    assert.deepEqual(s.ordenadas.value.map((l) => l.c.creative_id), ['c3', 'c2', 'c1'])
  }

  // Tirar do desempenho: exige motivo, manda o PATCH e pede pra tela recarregar.
  {
    const alvo = post()
    const { s, calls, toastLog, emitidos } = await videos({ postagens: [alvo], criativos: [criativo()] })
    s.abrirTirar(alvo)
    s.motivo.value = ' ab '
    await s.confirmarTirar()
    assert.equal(calls.length, 0, 'motivo curto nem chega no servidor')
    assert.equal(s.motivoErro.value, 'Escreva o motivo (pelo menos 3 letras).')
    s.motivo.value = '  conta antiga  '
    await s.confirmarTirar()
    assert.equal(calls[0].url, '/api/marketing/metricas/postagens/p-tt/desempenho')
    assert.equal(calls[0].opts.method, 'PATCH')
    assert.deepEqual(calls[0].opts.body, { contar: false, motivo: 'conta antiga' })
    assert.equal(s.tirando.value, null, 'fecha o diálogo')
    assert.ok(toastLog.some(([k, t]) => k === 'success' && t === 'Vídeo tirado do desempenho.'))
    assert.deepEqual(emitidos, [['mudou']], 'a tela-mãe recarrega (somas e medianas mudam)')
  }
  {
    const alvo = post()
    const { s, toastLog, emitidos } = await videos(
      { postagens: [alvo], criativos: [criativo()] },
      { patch: () => Promise.reject({ statusCode: 403, data: { detail: { code: 'fora_da_sua_equipe' } } }) },
    )
    s.abrirTirar(alvo)
    s.motivo.value = 'teste'
    await s.confirmarTirar()
    assert.ok(toastLog.some(([k, , l]) => k === 'error' && l === 'Esse vídeo é de outra equipe de marketing.'))
    assert.deepEqual(emitidos, [])
    assert.ok(s.tirando.value, 'erro deixa o diálogo aberto')
  }

  // Onde vale investir: conta criativos com índice; horário conta postagens.
  {
    const g = (over) => ({
      chave: 'x', rotulo: 'x', unidade: 'criativo', total: 3, n: 2, indice_views: 1.8, indice_interacao: null,
      leitura: 'pouco_dado', por_rede: { tiktok: { mediana: 1240, n: 2 }, instagram: { mediana: null, n: 0 } }, melhor: null, ...over,
    })
    const grupos = { produto: [g({ chave: 'a' }), g({ chave: 'b', n: 3, leitura: 'indicio' })], formato: [], agencia: [], roteiro: [], horario: [g({ chave: '12h', unidade: 'postagem' })] }
    const s = await investirFactory(
      Vue.ref, Vue.computed, Vue.watch, Vue.nextTick, (fn) => fn(), () => {},
      () => ({}), () => ({}), () => Vue.ref(true), apiError.apiErrMsg, {},
      () => Vue.reactive({ grupos, marco: 3, minimos: resposta().minimos }), () => () => {},
      ...NOMES_H.map((n) => H[n]),
    )
    assert.equal(s.comIndice.value, 5)
    assert.equal(s.linhas.value[0].b.tom, 'cinza', 'pouco dado fica cinza')
    assert.equal(s.linhas.value[0].redes, 'Instagram — (0) · TikTok 1.240 (2)', 'mediana por rede, na ordem das redes')
    s.dim.value = 'horario'
    assert.equal(s.porPostagem.value, true)
    assert.equal(s.lista.value[0].chave, '12h')
  }

  console.log('PASS: 3 SFCs parseiam e compilam; helpers puros (— ≠ 0, índice, barra log2, BRT, célula por estado, barras); '
    + 'higiene (credencial, 3 endpoints, noopener); textos travados; script setup com api falso '
    + '(filtros, corrida, v1, Atualizar agora 202/429/503 e espera, parar ao desmontar, porcentagem de dias fechados, '
    + 'voltar a contar, ordem da tabela, tirar do desempenho, grupos)')
}

run().catch((e) => {
  console.error(e)
  process.exit(1)
})
