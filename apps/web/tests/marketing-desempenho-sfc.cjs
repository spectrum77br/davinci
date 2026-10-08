// Run from apps/web: node tests/marketing-desempenho-sfc.cjs
//
// Tela de Desempenho (Eduardo, 24/09/2026; v3 em 06/10/2026):
// components/MarketingDesempenho.vue, MarketingDesempenhoMaisVistos.vue,
// MarketingDesempenhoInvestir.vue, MarketingDesempenhoVideos.vue e os helpers
// puros em utils/desempenho.ts.
//
// Trava as regras que mais fácil se perdem ao mexer na tela:
//  - métrica que NINGUÉM reportou aparece como "—", nunca como 0 (o YouTube
//    não mede salvamento; o Instagram só dá views com uma permissão que o
//    token ainda não tem — escrever 0 afirmaria o que a gente não sabe);
//  - views são SOMADAS (a última leitura de cada post, por rede e por vídeo),
//    sem índice "× o normal"; cada célula sem número diz por quê
//    (aguardando, sem views, falhou);
//  - semana e mês são os últimos 7 e 30 dias de calendário de Brasília;
//  - leitura velha avisa; falha de hoje não apaga a leitura boa de ontem;
//  - "Atualizar agora" respeita a trava de 10 min do servidor;
//  - as ressalvas (venda não é medida, view não é igual entre redes) ficam
//    NA TELA.
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
const SFCS = ['MarketingDesempenho', 'MarketingDesempenhoMaisVistos', 'MarketingDesempenhoVideos', 'MarketingDesempenhoInvestir']
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

// Views: "—" quando a rede não deu número; zero medido é zero.
assert.equal(H.fmtViews(8362), '8.362')
assert.equal(H.fmtViews(0), '0', 'o Facebook mede 0 de verdade')
assert.equal(H.fmtViews(null), '—')
assert.equal(H.fmtViews(undefined), '—')

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

// Célula rede × vídeo: a soma das views dos posts nesta rede; sem número, diz por quê.
assert.equal(H.celula([], AGORA).texto, 'não postado')
assert.equal(H.celula(undefined, AGORA).texto, 'não postado')
// Saiu e foi apagado depois: dizer "não postado" seria mentira.
assert.equal(H.celula([], AGORA, true).texto, 'apagado da rede')
assert.equal(H.celula([], AGORA, true).estado, 'apagado')
assert.equal(H.celula([{ estado: 'aguardando' }], AGORA).texto, 'aguardando 1ª leitura')
{
  const c = H.celula([{ estado: 'aguardando', publicado_em: '2026-09-24T15:04:00Z', marco_pronto_em: '2026-09-28T02:47:00Z' }], AGORA)
  assert.equal(c.estado, 'aguardando')
  assert.equal(c.dica, 'Publicado às 12:04. A primeira leitura sai em até 1 hora.', 'sem "comparação de 3 dias"')
  const velho = H.celula([{ estado: 'aguardando', publicado_em: '2026-09-21T22:04:00Z' }], AGORA)
  assert.equal(velho.dica, 'Publicado em 21/09 às 19:04. A primeira leitura sai em até 1 hora.')
}
{
  // O número é o TOTAL da rede, desde o primeiro dia: nada de "faltam 2 dias".
  const novo = H.celula([{ estado: 'ok', lido_em: '2026-09-24T02:47:00Z', acumulado: { views: 1202 } }], AGORA)
  assert.equal(novo.estado, 'ok')
  assert.equal(novo.texto, '1.202')
  assert.equal(novo.detalhe, '', 'sem "total X" pequeno do lado')
  assert.equal(novo.dica, '')
  // Dois posts do vídeo na mesma rede: soma, e o "+1 post" diz isso.
  const dois = H.celula([
    { estado: 'ok', lido_em: 'x', acumulado: { views: 448 } },
    { estado: 'ok', lido_em: 'x', acumulado: { views: 300 } },
  ], AGORA)
  assert.equal(dois.texto, '748')
  assert.equal(dois.dica, 'soma de 2 postagens')
  const umSem = H.celula([
    { estado: 'aguardando', acumulado: {} },
    { estado: 'ok', lido_em: 'x', acumulado: { views: 300 } },
  ], AGORA)
  assert.equal(umSem.texto, '300', 'o post novo ainda sem leitura não esconde o número do outro')
  assert.equal(umSem.dica, 'soma de 1 postagem (1 ainda sem número)')
  assert.equal(H.celula([{ estado: 'ok', lido_em: 'x', acumulado: { views: 0 } }], AGORA).texto, '0', 'zero medido aparece')
}
{
  const sv = H.celula([{ estado: 'ok', lido_em: 'x', acumulado: { curtidas: 12 } }], AGORA)
  assert.equal(sv.estado, 'sem_views')
  assert.equal(sv.texto, 'sem views')
  assert.equal(sv.detalhe, '12 curtidas · a conta não liberou insights')
}
{
  // Falhou sem nunca ter lido: âmbar, com o erro no title.
  const f = H.celula([{ estado: 'falhou', lido_em: null, erro: 'HTTP 403', acumulado: {} }], AGORA)
  assert.equal(f.estado, 'falhou')
  assert.equal(f.texto, 'não consegui ler')
  assert.equal(f.dica, 'HTTP 403')
  // Falhou HOJE mas já tinha número: mostra o número bom e avisa de quando é.
  const g = H.celula([{ estado: 'falhou', lido_em: '2026-09-23T02:50:00Z', erro: 'timeout', acumulado: { views: 900 } }], AGORA)
  assert.equal(g.estado, 'ok')
  assert.equal(g.texto, '900', 'falha de hoje não some com o número bom')
  assert.equal(g.aviso, 'a leitura de hoje falhou — mostrando a de 22/09')
}

// Mais vistos: janela pela idade em dias de Brasília (semana 0–6, mês 0–29),
// sem número fica de fora, empate vai pro mais novo, e corta em n.
{
  const v = (id, idade, total, pub = '2026-09-20T15:00:00Z') => ({
    creative_id: id, idade_dias: idade, views: { total }, primeira_publicacao_em: pub,
  })
  const cs = [
    v('a', 6, 500), v('b', 7, 9000), v('c', 0, null), v('d', 29, 700), v('e', 30, 99999),
    v('f', 2, 500, '2026-09-22T15:00:00Z'), v('g', null, 50),
  ]
  assert.deepEqual(H.maisVistos(cs, 'semana').map((c) => c.creative_id), ['f', 'a'], '7 dias atrás já é fora da semana; empate: o mais novo')
  assert.deepEqual(H.maisVistos(cs, 'mes').map((c) => c.creative_id), ['b', 'd', 'f', 'a'], '30 dias atrás é fora do mês; sem número fora')
  assert.deepEqual(H.maisVistos(cs, 'mes', 2).map((c) => c.creative_id), ['b', 'd'])
  assert.equal(H.maisVistos(cs, 'mes').length, 4)
  assert.deepEqual(H.maisVistos([], 'mes'), [])
}

// Ordem dos grupos: "(sem …)" sempre no fim; sem número antes dele, no fim dos outros.
{
  const g = (chave, mes, semana, media) => ({ chave, semana: { views: semana }, mes: { views: mes, media } })
  const lista = [g('a', 100, null, 50), g('nenhum', 99999, 99999, 99999), g('b', 300, 10, 100), g('c', null, null, null), g('d', 200, 400, 200)]
  assert.deepEqual(H.ordenarGrupos(lista, 'servidor').map((x) => x.chave), ['a', 'b', 'c', 'd', 'nenhum'])
  assert.deepEqual(H.ordenarGrupos(lista, 'mes').map((x) => x.chave), ['b', 'd', 'a', 'c', 'nenhum'])
  assert.deepEqual(H.ordenarGrupos(lista, 'semana').map((x) => x.chave), ['d', 'b', 'a', 'c', 'nenhum'])
  assert.deepEqual(H.ordenarGrupos(lista, 'por_video').map((x) => x.chave), ['d', 'b', 'a', 'c', 'nenhum'])
  assert.equal(lista[0].chave, 'a', 'não mexe na lista de entrada')
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
// Shopee Vídeo (08/10/2026): as views dela entram no total do vídeo, então ela
// ganha coluna quando há post lá — senão o total não bate com a soma das redes.
assert.deepEqual(H.redesDaTabela(['shopee']), ['instagram', 'youtube', 'tiktok', 'shopee'], 'Shopee só se houver post')
assert.deepEqual(
  H.redesDaTabela(['facebook', 'shopee']), ['instagram', 'youtube', 'tiktok', 'facebook', 'shopee'], 'as cinco juntas',
)
assert.equal(H.ORDEM_REDES.at(-1), 'shopee', 'Shopee no fim, na mesma ordem da API')
assert.deepEqual([3, 4, 5].map((n) => H.colunasDaGrade(H.ORDEM_REDES.slice(0, n))), [3, 4, 5])
assert.equal(H.colunasDaGrade([]), 3)
for (const nome of ['MarketingDesempenho', 'MarketingDesempenhoVideos']) {
  const src = sfc[nome].source
  assert.match(src, /\b5: \{[\s\S]*?repeat\(5,/, `${nome}: grade de cinco colunas de rede`)
  assert.match(src, /colunasDaGrade\(/, `${nome}: escolhe a grade pelo número de redes`)
  assert.ok(!/length > 3 \? 4 : 3/.test(src), `${nome}: sem a grade presa em 4 colunas`)
}

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
// Link pro post abre fora, sem dar window.opener pra rede social — e todo
// target="_blank" leva o rel.
for (const n of ['MarketingDesempenho', 'MarketingDesempenhoMaisVistos', 'MarketingDesempenhoVideos', 'MarketingDesempenhoInvestir']) {
  assert.match(sfc[n].tpl, /target="_blank" rel="noopener"/, `${n}: link do post com rel=noopener`)
  assert.equal((sfc[n].tpl.match(/target="_blank"/g) || []).length, (sfc[n].tpl.match(/target="_blank" rel="noopener"/g) || []).length, `${n}: todo _blank com noopener`)
}
// O índice "× o normal" saiu da tela inteira (06/10/2026): nada de 1,7×,
// "indício" nem "pouco dado".
for (const n of SFCS) {
  assert.ok(!/pouco dado|indício|dá pra comparar|normal da conta|×/.test(sfc[n].tpl), `${n}: a tela não fala em índice`)
  assert.ok(!/\.indice_views|\.views_marco|fmtIndice|tomIndice|rotuloLeitura|\bbarra\(/.test(sfc[n].script), `${n}: não lê o índice`)
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
  'Vídeos mais vistos', // o ranking pedido em 06/10
  'Onde vale investir',
  'views ganhas na semana e no mês', // views da janela = ganhas nela, como no Resumo
  'Todos os vídeos',
  'publicados nos últimos 90 dias', // a lista não segue o período do topo — e diz
  'vale pro resumo', // o seletor 7/30/90 é do Resumo (e dos "+" por marca)
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
  dias, marcaId, dados, carregando, recarregando, erro, carregar, atualizarAgora, atualizando,
  aindaRodando, podeAtualizarEm, cartoes, voltarAContar, nadaPublicado, leitura, filtrarMarca, mostrarMarca`)
const videosFactory = fabrica('MarketingDesempenhoVideos', [], `
  linhas, ordenadas, ordemEfetiva, ordem, abrirTirar, confirmarTirar, tirando, motivo, motivoErro, restantes`)
const investirFactory = fabrica('MarketingDesempenhoInvestir', [], 'dim, ordem, lista, porPostagem, linhas, unidade, rodape')
const maisVistosFactory = fabrica('MarketingDesempenhoMaisVistos', [], 'janela, linhas, restantes, mostrarMais, subtitulo, infoJanela')

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
    creative_id: 'c1', nome: 'Saque Rápido', titulo: 'Tecnologia que aguenta o teu dia', marca: 'Uranyx', marca_id: 'm1',
    sku: 'dg017.pi', modelo: 'Saque Rápido',
    produto: { chave: 'dev:fone dg017', rotulo: 'Fone DG017', skus: ['dg017.pi'], variantes: [] }, formato: { chave: '15s', rotulo: 'vídeo 15s' },
    agencia: { chave: 'nenhum', rotulo: '(sem agência)' }, roteiro: { chave: 'nenhum', rotulo: '(sem roteiro)' },
    primeira_publicacao_em: '2026-09-20T15:04:00Z', idade_dias: 4,
    views: { total: 1900, por_rede: { tiktok: 1900 }, postagens: 1, com_numero: 1 },
    melhor_post: { plataforma: 'tiktok', postagem_id: 'p-tt', post_url: 'https://www.tiktok.com/@uranyx_br/video/1', views: 1900 },
    indice_views: 2.1, indice_interacao: 1.3, n_indices: 1,
    postagens: { instagram: [], youtube: [], tiktok: ['p-tt'] }, ...over,
  }
}
function grupo(over = {}) {
  return {
    chave: 'x', rotulo: 'x', detalhe: null, unidade: 'criativo',
    semana: { views: null, videos: 0, com_numero: 0, views_dos_publicados: null, media: null, mediana: null },
    mes: { views: 100, videos: 1, com_numero: 1, views_dos_publicados: 100, media: 100, mediana: 100 },
    por_rede_mes: { tiktok: 100 }, melhor: null, ...over,
  }
}
function resposta(over = {}) {
  return {
    versao: 2, dias: 30, desde: '2026-08-26', ate: '2026-09-24', marco: 3, marca_id: null, gerado_em: '2026-09-24T18:02:11Z',
    minimos: { base_conta: 5, views_taxa: 100, tolerancia_h: 36 },
    janelas: { semana: { dias: 7, desde: '2026-09-18' }, mes: { dias: 30, desde: '2026-08-26' } },
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
  const p = Vue.reactive({ canEdit: true, ...props })
  const s = await videosFactory(
    Vue.ref, Vue.computed, Vue.watch, Vue.nextTick, (fn) => fn(), () => {},
    () => ({ api }), () => toastsFalsos(toastLog), () => Vue.ref(true), apiError.apiErrMsg, {},
    () => p, () => (nome, ...a) => emitidos.push([nome, ...a]),
    ...NOMES_H.map((n) => H[n]),
  )
  return { s, calls, toastLog, emitidos }
}

async function run() {
  // Carga: um GET só, com o período; marca e período trocados refazem.
  {
    const { s, calls } = await tela()
    assert.equal(calls[0].url, '/api/marketing/metricas?dias=30', 'sem "marco": a tela não compara mais por idade')
    assert.equal(s.dados.value.postagens.length, 1)
    assert.equal(s.mostrarMarca.value, true, 'todas as marcas: a linha do vídeo diz a marca')
    s.marcaId.value = 'm1'
    await Vue.nextTick(); await esperar()
    assert.equal(calls.at(-1).url, '/api/marketing/metricas?dias=30&marca_id=m1', 'marca filtra no servidor')
    assert.equal(s.mostrarMarca.value, false)
    s.filtrarMarca('m1')
    await Vue.nextTick(); await esperar()
    assert.equal(s.marcaId.value, null, 'clicar de novo na marca tira o filtro')
    s.dias.value = 7
    await Vue.nextTick(); await esperar()
    assert.equal(calls.at(-1).url, '/api/marketing/metricas?dias=7')

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
    assert.equal(s.dados.value.janelas.semana.dias, 7)
  }
  // Servidor com a API de 24/09 (grupos com índice, sem semana/mês; criativo
  // sem views): o grupo velho some e o criativo ganha views vazias.
  {
    const velho = resposta({
      janelas: undefined, ate: '2026-10-06',
      grupos: { produto: [{ chave: 'a', rotulo: 'a', total: 3, n: 2, indice_views: 1.7, leitura: 'pouco_dado' }, grupo({ chave: 'b' })], formato: [], agencia: [], roteiro: [], horario: [] },
      criativos: [{ creative_id: 'c9', titulo: 'legenda', postagens: {} }],
    })
    delete velho.janelas
    const { s } = await tela({ get: () => Promise.resolve(velho) })
    assert.deepEqual(s.dados.value.grupos.produto.map((g) => g.chave), ['b'])
    const [c] = s.dados.value.criativos
    assert.deepEqual(c.views, { total: null, por_rede: {}, postagens: 0, com_numero: 0 })
    assert.equal(c.nome, 'legenda')
    assert.equal(c.idade_dias, null)
    assert.deepEqual(s.dados.value.janelas, { semana: { dias: 7, desde: '2026-09-30' }, mes: { dias: 30, desde: '2026-09-07' } })
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

  // Todos os vídeos: célula = soma da rede, "+1 post", total do vídeo e ordem.
  {
    const ig1 = post({ postagem_id: 'p-ig1', plataforma: 'instagram', publicado_em: '2026-09-22T22:03:00Z', acumulado: { views: 6755 } })
    const ig2 = post({ postagem_id: 'p-ig2', plataforma: 'instagram', publicado_em: '2026-09-21T22:03:00Z', acumulado: { views: 245 } })
    const novo = post({
      postagem_id: 'p-novo', creative_id: 'c2', estado: 'aguardando', lido_em: null, acumulado: {},
      publicado_em: new Date().toISOString(),
    })
    const yt = post({ postagem_id: 'p-yt3', creative_id: 'c3', plataforma: 'youtube', acumulado: { views: 1159 }, publicado_em: '2026-09-23T15:00:00Z' })
    const { s } = await videos({
      postagens: [post(), ig1, ig2, novo, yt],
      criativos: [
        criativo({ creative_id: 'c3', nome: 'Caiu do barco', titulo: 'Caiu do barco', views: { total: 1159, por_rede: { youtube: 1159 }, postagens: 1, com_numero: 1 }, postagens: { instagram: [], youtube: ['p-yt3'], tiktok: [] } }),
        criativo({
          views: { total: 8900, por_rede: { instagram: 7000, tiktok: 1900 }, postagens: 3, com_numero: 3 },
          postagens: { instagram: ['p-ig1', 'p-ig2'], youtube: [], tiktok: ['p-tt'] },
        }),
        criativo({
          creative_id: 'c2', nome: 'novo', titulo: 'novo', primeira_publicacao_em: novo.publicado_em,
          views: { total: null, por_rede: {}, postagens: 1, com_numero: 0 }, postagens: { instagram: [], youtube: [], tiktok: ['p-novo'] },
        }),
      ],
    })
    const l1 = s.linhas.value[1]
    assert.deepEqual(l1.colunas.map((c) => c.rede), ['instagram', 'youtube', 'tiktok'])
    assert.equal(l1.colunas[0].cel.texto, '7.000', 'dois posts na mesma rede: a soma')
    assert.equal(l1.colunas[0].extra, 1, '…e avisa "+1 post"')
    assert.equal(l1.colunas[0].cel.dica, 'soma de 2 postagens')
    assert.equal(l1.colunas[1].cel.texto, 'não postado')
    assert.equal(l1.colunas[2].cel.texto, '1.900')
    assert.equal(l1.meta, '“Tecnologia que aguenta o teu dia” · Fone DG017 · vídeo 15s · 20/09 12h', 'a legenda entra quando o nome é outro; "(sem agência)" não polui')
    assert.equal(s.linhas.value[0].meta.startsWith('“'), false, 'legenda igual ao nome não repete')
    const l2 = s.linhas.value[2]
    assert.equal(l2.colunas[2].cel.texto, 'aguardando 1ª leitura')
    assert.equal(s.ordemEfetiva.value, 'views', 'o padrão é sempre "mais views"')
    assert.deepEqual(s.ordenadas.value.map((l) => l.c.creative_id), ['c1', 'c3', 'c2'], 'sem número vai pro fim')
    s.ordem.value = 'novos'
    assert.equal(s.ordenadas.value[0].c.creative_id, 'c2')
    s.ordem.value = 'youtube'
    assert.deepEqual(s.ordenadas.value.map((l) => l.c.creative_id), ['c3', 'c1', 'c2'], 'por rede: as views daquela rede')
    s.ordem.value = 'instagram'
    assert.equal(s.ordenadas.value[0].c.creative_id, 'c1')
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

  // Onde vale investir: views GANHAS na semana e no mês (a conta do Resumo),
  // vídeos publicados embaixo; horário conta postagens.
  {
    const melhor = { creative_id: 'saque', postagem_id: 'p1', nome: 'Saque Rápido', titulo: 'Aparelho que…', views: 8362, plataforma: 'instagram', post_url: 'https://ig/p1' }
    const grupos = {
      produto: [
        grupo({
          chave: 'dev:mala sorriso m6', rotulo: 'Mala Sorriso M6', detalhe: 'Branco tam. 24 · SKU b055.24',
          semana: { views: 13335, videos: 6, com_numero: 6, views_dos_publicados: 13335, media: 2223, mediana: 2196 },
          mes: { views: 13335, videos: 6, com_numero: 6, views_dos_publicados: 13335, media: 2223, mediana: 2196 },
        }),
        grupo({
          chave: 'dev:uranyx f105 12.64', rotulo: 'Uranyx F105 12.64', detalhe: 'Preto + cartão 64GB · SKU dg019.ra, dg019.sp',
          // Semana: 9.245 ganhas, mais do que os 8.362 do único vídeo publicado
          // nela — o de 8 dias atrás continuou rendendo.
          semana: { views: 9245, videos: 1, com_numero: 1, views_dos_publicados: 8362, media: 8362, mediana: 8362 },
          mes: { views: 10212, videos: 3, com_numero: 2, views_dos_publicados: 10212, media: 5106, mediana: 5106 },
          por_rede_mes: { instagram: 6997, youtube: 2209, tiktok: 1006, facebook: 0 }, melhor,
        }),
        // Agência/produto sem vídeo novo, mas que atraiu views na semana.
        grupo({
          chave: 'dev:uranyx wp53 24.128', rotulo: 'Uranyx WP53 24.128',
          semana: { views: 35, videos: 0, com_numero: 0, views_dos_publicados: null, media: null, mediana: null },
          mes: { views: 1321, videos: 1, com_numero: 1, views_dos_publicados: 1321, media: 1321, mediana: 1321 },
        }),
        grupo({ chave: 'nenhum', rotulo: '(sem produto)', mes: { views: 99999, videos: 9, com_numero: 9, views_dos_publicados: 99999, media: 11111, mediana: 1 } }),
      ],
      formato: [], agencia: [], roteiro: [],
      horario: [grupo({ chave: '12h', rotulo: '12h', unidade: 'postagem', mes: { views: 33531, videos: 59, com_numero: 58, views_dos_publicados: 33500, media: 578, mediana: 248 } })],
    }
    const s = await investirFactory(
      Vue.ref, Vue.computed, Vue.watch, Vue.nextTick, (fn) => fn(), () => {},
      () => ({}), () => ({}), () => Vue.ref(true), apiError.apiErrMsg, {},
      () => Vue.reactive({ grupos, janelas: resposta().janelas }), () => () => {},
      ...NOMES_H.map((n) => H[n]),
    )
    assert.deepEqual(s.lista.value.map((g) => g.chave), ['dev:mala sorriso m6', 'dev:uranyx f105 12.64', 'dev:uranyx wp53 24.128', 'nenhum'], 'mais views no mês; "(sem produto)" no fim')
    const f105 = s.linhas.value[1]
    assert.equal(f105.semana, '1 vídeo publicado')
    assert.equal(f105.mes, '3 vídeos publicados')
    assert.equal(s.linhas.value[2].semana, '0 vídeos publicados', 'sem vídeo novo, mas com views ganhas')
    assert.equal(f105.redes, 'Instagram 6.997 · YouTube 2.209 · TikTok 1.006 · Facebook 0', 'por rede no mês, Facebook 0 medido aparece')
    assert.equal(f105.dicaMedia, 'média das views até hoje de 2 vídeos publicados no mês (10.212 ÷ 2); mediana 5.106 (metade ficou acima) · 1 ainda sem número')
    assert.equal(f105.melhorNome, 'Saque Rápido')
    assert.equal(s.linhas.value[0].barra, 100, 'a barra é só o tamanho perto do maior')
    assert.equal(f105.barra, 76.6)
    assert.equal(s.linhas.value[3].barra, 0, '"(sem produto)" não puxa a escala')
    s.ordem.value = 'por_video'
    assert.deepEqual(s.lista.value.map((g) => g.chave), ['dev:uranyx f105 12.64', 'dev:mala sorriso m6', 'dev:uranyx wp53 24.128', 'nenhum'])
    s.ordem.value = 'semana'
    assert.deepEqual(s.lista.value.map((g) => g.chave), ['dev:mala sorriso m6', 'dev:uranyx f105 12.64', 'dev:uranyx wp53 24.128', 'nenhum'])
    assert.equal(s.rodape.value, 'Semana = últimos 7 dias (desde 18/09); mês = últimos 30 dias (desde 26/08). '
      + 'Views = as que todos os vídeos do grupo ganharam nesses dias, somando as redes (vídeo antigo que continua '
      + 'rendendo conta) — a mesma conta do Resumo. Embaixo, quantos vídeos foram publicados nesses dias.')
    s.dim.value = 'horario'
    assert.equal(s.porPostagem.value, true)
    assert.equal(s.unidade.value.varios, 'postagens')
    assert.equal(s.lista.value[0].chave, '12h')
    assert.equal(s.linhas.value[0].mes, '59 postagens publicadas')
    assert.match(s.linhas.value[0].dicaMedia, /de 58 postagens publicadas no mês .* · 1 ainda sem número$/)
    assert.match(s.rodape.value, /Views = as que todas as postagens do grupo ganharam .*\(postagem antiga que continua rendendo conta\).* Embaixo, quantas postagens foram publicadas nesses dias\.$/)
    // O cabeçalho diz o que o número mede.
    assert.ok(sfc.MarketingDesempenhoInvestir.tpl.includes('ganhas nos últimos {{ diasSemana }} dias'))
    assert.ok(sfc.MarketingDesempenhoInvestir.tpl.includes('{{ unidade.publicado }} no mês, até hoje'))
  }

  // Vídeos mais vistos: Semana | Mês, chips por rede com o link do post mais visto.
  {
    const ig = post({ postagem_id: 'ig', plataforma: 'instagram', post_url: 'https://ig/1', acumulado: { views: 6755 }, publicado_em: '2026-10-02T15:04:00Z', horario: '12h' })
    const tt1 = post({ postagem_id: 'tt1', post_url: 'https://tt/1', acumulado: { views: 148 }, publicado_em: '2026-10-03T15:04:00Z' })
    const tt2 = post({ postagem_id: 'tt2', post_url: 'https://tt/2', acumulado: { views: 300 }, publicado_em: '2026-10-02T15:10:00Z' })
    const saque = criativo({
      creative_id: 'saque', nome: 'Saque Rápido', titulo: 'Aparelho que não pede carregador',
      produto: { chave: 'dev:uranyx f105', rotulo: 'Uranyx F105', skus: ['dg019.ra'], variantes: ['Preto'] }, agencia: { chave: 'bill gates', rotulo: 'Bill Gates' },
      primeira_publicacao_em: '2026-10-02T15:04:00Z', idade_dias: 4,
      views: { total: 7203, por_rede: { instagram: 6755, tiktok: 448 }, postagens: 3, com_numero: 3 },
      postagens: { instagram: ['ig'], youtube: [], tiktok: ['tt1', 'tt2'] },
    })
    const velhos = Array.from({ length: 14 }, (_, i) => criativo({
      creative_id: `v${i}`, nome: `v${i}`, idade_dias: 10 + i, primeira_publicacao_em: `2026-09-${String(10 + i).padStart(2, '0')}T15:00:00Z`,
      views: { total: 100 + i, por_rede: { youtube: 100 + i }, postagens: 1, com_numero: 1 }, postagens: {},
    }))
    const semNumero = criativo({ creative_id: 'novo', idade_dias: 0, views: { total: null, por_rede: {}, postagens: 1, com_numero: 0 } })
    const props = Vue.reactive({
      criativos: [saque, semNumero, ...velhos], postagens: [ig, tt1, tt2], janelas: resposta().janelas, mostrarMarca: true,
    })
    const s = await maisVistosFactory(
      Vue.ref, Vue.computed, Vue.watch, Vue.nextTick, (fn) => fn(), () => {},
      () => ({}), () => ({}), () => Vue.ref(true), apiError.apiErrMsg, {},
      () => props, () => () => {},
      ...NOMES_H.map((n) => H[n]),
    )
    assert.equal(s.janela.value, 'mes', 'abre no mês')
    assert.equal(s.subtitulo.value, 'publicados nos últimos 30 dias (desde 26/08) · views até hoje')
    assert.equal(s.linhas.value.length, 10, 'top 10')
    assert.equal(s.restantes.value, 5)
    const [l1] = s.linhas.value
    assert.equal(l1.c.creative_id, 'saque')
    assert.equal(l1.pos, 1)
    assert.equal(l1.sub, '“Aparelho que não pede carregador” · Uranyx · Uranyx F105 · Bill Gates · 02/10 12h')
    assert.deepEqual(l1.chips, [
      { rede: 'instagram', views: 6755, url: 'https://ig/1' },
      { rede: 'tiktok', views: 448, url: 'https://tt/2' },
    ], 'um chip por rede com número; o link é o do post mais visto da rede')
    assert.ok(!s.linhas.value.some((l) => l.c.creative_id === 'novo'), 'sem número não entra no ranking')
    s.mostrarMais()
    assert.equal(s.linhas.value.length, 15)
    assert.equal(s.restantes.value, 0)
    s.janela.value = 'semana'
    await Vue.nextTick()
    assert.equal(s.subtitulo.value, 'publicados nos últimos 7 dias (desde 18/09) · views até hoje')
    assert.deepEqual(s.linhas.value.map((l) => l.c.creative_id), ['saque'], 'semana: só os de até 6 dias')
    props.mostrarMarca = false
    assert.equal(s.linhas.value[0].sub, '“Aparelho que não pede carregador” · Uranyx F105 · Bill Gates · 02/10 12h', 'marca filtrada: não repete a marca')
    props.criativos = [velhos[0]]
    assert.equal(s.linhas.value.length, 0)
    assert.ok(sfc.MarketingDesempenhoMaisVistos.tpl.includes('Nenhum vídeo publicado nos últimos {{ infoJanela.dias }} dias tem views ainda.'))
  }

  console.log('PASS: 4 SFCs parseiam e compilam; helpers puros (— ≠ 0, views somadas, BRT, célula por estado, '
    + 'mais vistos 7/30 dias, ordem dos grupos, barras); higiene (credencial, 3 endpoints, noopener, sem índice); '
    + 'textos travados; script setup com api falso (filtros, corrida, v1 e API de 24/09, Atualizar agora 202/429/503 '
    + 'e espera, parar ao desmontar, porcentagem de dias fechados, voltar a contar, ordem da tabela, tirar do '
    + 'desempenho, onde vale investir semana/mês, mais vistos)')
}

run().catch((e) => {
  console.error(e)
  process.exit(1)
})
